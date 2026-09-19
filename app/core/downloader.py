import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from spotdl.utils.formatter import sanitize_string as spotdl_sanitize
from sqlalchemy.orm import Session
from yt_dlp.utils import sanitize_filename as yt_dlp_sanitize

from app.core.notifications import send_telegram_notification
from app.core.process_registry import cleanup_partial_files, download_manager
from app.db import models
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)


def get_ytdlp_cmd() -> list[str]:
    """Returns the executable command prefix for yt-dlp.

    Prioritizes 'yt-dlp' from PATH if available (Docker / Linux venvs),
    otherwise falls back to invoking the module via the active Python runtime
    (sys.executable -m yt_dlp), supporting standalone embedded Windows environments.
    """
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def get_spotdl_cmd() -> list[str]:
    """Returns the executable command prefix for spotdl.

    Prioritizes 'spotdl' from PATH if available (Docker / Linux venvs),
    otherwise falls back to invoking the module via the active Python runtime
    (sys.executable -m spotdl), supporting standalone embedded Windows environments.
    """
    if shutil.which("spotdl"):
        return ["spotdl"]
    return [sys.executable, "-m", "spotdl"]


def get_download_dir() -> Path:
    env_dir = os.getenv("DOWNLOAD_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    if os.path.isdir("/downloads"):
        return Path("/downloads").resolve()
    return Path("./downloads").resolve()


DOWNLOAD_DIR = get_download_dir()


def get_subprocess_creationflags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


SUBPROCESS_CREATIONFLAGS: int = get_subprocess_creationflags()


def check_exists(db: Session, track_id: str) -> bool:
    """
    Checks if a track ID (or legacy raw ID) exists in the database AND its audio file exists on disk.
    If marked 'Completed' in DB but the file is missing from disk, removes the stale DB record
    so the track will be automatically re-downloaded.
    """
    raw_id = track_id.split("_", 1)[-1] if "_" in track_id else track_id
    dl = (
        db.query(models.Download)
        .filter(
            (models.Download.track_id == track_id)
            | (models.Download.track_id == raw_id)
        )
        .first()
    )
    if not dl:
        return False

    if dl.status == "Completed":
        if dl.file_path and os.path.exists(dl.file_path):
            return True
        else:
            logger.info(
                f"Track '{dl.title}' ({dl.track_id}) missing from disk ({dl.file_path}). Purging stale record for re-download."
            )
            db.delete(dl)
            db.commit()
            return False

    if dl.status == "Deleted":
        logger.info(f"Track '{dl.title}' ({dl.track_id}) is marked as Deleted. Allowing re-download.")
        return False

    return False


def inspect_url_tracks(
    url: str,
    db: Session,
    media_type: str = "audio",
    timeout: float = 4.0,
) -> tuple[int, int]:
    """Inspects a target download URL and determines (new_count, duplicate_count) against the database and disk.

    Uses fast regex matching for standard YouTube and Spotify singles to avoid subprocess overhead.
    Falls back to fast flat-playlist inspection for playlists and generic extractors.
    """
    url_stripped = url.strip()

    # Fast-path 1: Single YouTube Video
    yt_match = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url_stripped)
    if yt_match and not re.search(r"[?&]list=", url_stripped):
        raw_id = yt_match.group(1)
        track_id = f"youtube_{raw_id}"
        if check_exists(db, track_id):
            return (0, 1)
        return (1, 0)

    # Fast-path 2: Single Spotify Track
    sp_match = re.search(r"spotify\.com/track/([a-zA-Z0-9]+)", url_stripped)
    if sp_match:
        raw_id = sp_match.group(1)
        track_id = f"spotify_{raw_id}"
        if check_exists(db, track_id):
            return (0, 1)
        return (1, 0)

    # Probe-path: Spotify playlist or album
    if re.search(r"spotify\.com", url_stripped):
        temp_file = f"temp_probe_{uuid.uuid4().hex}.spotdl"
        try:
            settings = db.query(models.Settings).first()
            auth_args: list[str] = []
            if settings and settings.spotify_client_id and settings.spotify_client_secret:
                auth_args = [
                    "--client-id",
                    str(settings.spotify_client_id).strip(),
                    "--client-secret",
                    str(settings.spotify_client_secret).strip(),
                ]
            cmd = (
                get_spotdl_cmd()
                + auth_args
                + [
                    "--yt-dlp-args",
                    "extractor-args=youtube:player_client=android,web,ios",
                    "save",
                    url_stripped,
                    "--save-file",
                    temp_file,
                ]
            )
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
            if os.path.exists(temp_file):
                with open(temp_file, "r") as f:
                    metadata = json.load(f)
                dupes = 0
                new = 0
                for item in metadata:
                    raw_id = item.get("song_id")
                    if raw_id:
                        tid = f"spotify_{raw_id}"
                        if check_exists(db, tid):
                            dupes += 1
                        else:
                            new += 1
                    else:
                        new += 1
                return (new, dupes)
        except Exception as e:
            logger.debug(f"Spotify duplicate probe failed or timed out: {e}")
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
        return (1, 0)

    # Probe-path: Generic yt-dlp / YouTube playlist
    try:
        cmd_meta = get_ytdlp_cmd() + [
            "--yes-playlist",
            "--ignore-errors",
            "-J",
            "--flat-playlist",
            url_stripped,
        ]
        res = subprocess.run(
            cmd_meta,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=SUBPROCESS_CREATIONFLAGS,
        )
        data = json.loads(res.stdout)
        extractor = (
            data.get("extractor_key") or data.get("extractor") or "ytdlp"
        )
        entries = data.get("entries")
        dupes = 0
        new = 0
        if entries:
            for entry in entries:
                raw_id = entry.get("id")
                if raw_id:
                    tid = f"{extractor.lower()}_{raw_id}"
                    if check_exists(db, tid):
                        dupes += 1
                    else:
                        new += 1
                else:
                    new += 1
        else:
            raw_id = data.get("id")
            if raw_id:
                tid = f"{extractor.lower()}_{raw_id}"
                if check_exists(db, tid):
                    dupes += 1
                else:
                    new += 1
            else:
                new += 1
        return (new, dupes)
    except Exception as e:
        logger.debug(f"yt-dlp duplicate probe failed or timed out: {e}")
        return (1, 0)


def get_canonical_source_url(download: models.Download) -> str:
    """Returns the canonical source webpage URL for a download record.

    If source_url is already stored, returns it directly.
    For legacy records without source_url, dynamically reconstructs the canonical
    webpage link based on the namespaced track_id (e.g. youtube_{id}, spotify_{id}, soundcloud_{id}).
    """
    if download.source_url and download.source_url.strip():
        return download.source_url.strip()

    track_id = (download.track_id or "").strip()
    if not track_id:
        return ""

    if track_id.startswith("youtube_"):
        raw_id = track_id[len("youtube_"):]
        if raw_id:
            return f"https://www.youtube.com/watch?v={raw_id}"
    elif track_id.startswith("spotify_"):
        raw_id = track_id[len("spotify_"):]
        if raw_id:
            return f"https://open.spotify.com/track/{raw_id}"
    elif track_id.startswith("soundcloud_"):
        raw_id = track_id[len("soundcloud_"):]
        if raw_id:
            return f"https://soundcloud.com/track/{raw_id}"
    elif track_id.startswith(("http://", "https://")):
        return track_id

    if download.title and download.title.strip().startswith(("http://", "https://")):
        return download.title.strip()

    return ""


def insert_download(
    db: Session,
    track_id: str,
    title: str,
    artist: str,
    file_path: str | None = None,
    status: str = "Completed",
    job_id: str | None = None,
    job_title: str | None = None,
    synced_playlist_id: int | None = None,
    source_url: str | None = None,
):
    """Inserts or updates a download record in the database."""
    dl = db.query(models.Download).filter(models.Download.track_id == track_id).first()
    if dl:
        dl.title = title
        dl.artist = artist
        if file_path:
            dl.file_path = file_path
        dl.status = status
        if job_id:
            dl.job_id = job_id
        if job_title is not None:
            dl.job_title = job_title
        if synced_playlist_id:
            dl.synced_playlist_id = synced_playlist_id
        if source_url:
            dl.source_url = source_url
    else:
        dl = models.Download(
            track_id=track_id,
            title=title,
            artist=artist,
            file_path=file_path,
            status=status,
            job_id=job_id,
            job_title=job_title,
            synced_playlist_id=synced_playlist_id,
            source_url=source_url,
        )
        db.add(dl)
    db.commit()


def fix_permissions(path: Path):
    """Recursively apply open permissions so the host can read/write/delete."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o777)
        for root, dirs, files in os.walk(path):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o777)
            for f in files:
                os.chmod(os.path.join(root, f), 0o666)
    except Exception as e:
        logger.error(f"Failed to fix permissions: {e}")


def sanitize_path_segment(value: str) -> str:
    """Sanitizes a string to be safely used as a single file or directory segment."""
    sanitized = yt_dlp_sanitize(value.strip())
    # Strip any characters that yt_dlp_sanitize might preserve that could act as path separators
    for sep in ("/", "\\", ":"):
        sanitized = sanitized.replace(sep, "_")
    return sanitized.strip(" .")


def resolve_track_path(
    template: str,
    artist: str = "",
    title: str = "",
    album: str = "",
    playlist: str = "",
    track_number: int | str = "",
    ext: str = "opus",
    download_dir: Path | str | None = None,
) -> Path:
    """Resolves a target file path given a template and metadata placeholders.

    Tokens supported: {artist}, {title}, {album}, {playlist}, {track_number}, {ext}.
    For standalone tracks with empty playlist/album tokens, empty directory segments
    are collapsed so the file is placed directly inside download_dir.
    """
    base_dir = Path(download_dir if download_dir is not None else DOWNLOAD_DIR).resolve()
    clean_ext = ext.lstrip(".") or "opus"

    # Normalize track number: format single digits as 01, 02, etc. if integer
    formatted_track_num = ""
    if track_number is not None and str(track_number).strip():
        s_num = str(track_number).strip()
        if s_num.isdigit():
            formatted_track_num = f"{int(s_num):02d}"
        else:
            formatted_track_num = s_num

    # Token dictionary with sanitized values
    tokens = {
        "artist": sanitize_path_segment(artist) if artist else "",
        "title": sanitize_path_segment(title) if title else "",
        "album": sanitize_path_segment(album) if album else "",
        "playlist": sanitize_path_segment(playlist) if playlist else "",
        "track_number": formatted_track_num,
        "ext": clean_ext,
    }

    # Normalize template separators
    norm_template = template.replace("\\", "/").strip("/")
    segments = norm_template.split("/")

    dir_segments: list[str] = []
    # All segments except the last are directory levels
    for seg in segments[:-1]:
        res_seg = seg
        for token_name, val in tokens.items():
            res_seg = res_seg.replace(f"{{{token_name}}}", val)
        res_seg = re.sub(r"\s+", " ", res_seg).strip(" -_.")
        # If segment resulted in an empty string (e.g. {playlist} was empty), collapse it!
        if res_seg:
            dir_segments.append(res_seg)

    # Last segment is filename
    file_seg = segments[-1]
    for token_name, val in tokens.items():
        file_seg = file_seg.replace(f"{{{token_name}}}", val)

    file_seg = re.sub(r"\s+", " ", file_seg).strip(" -_")
    if not file_seg or file_seg == f".{clean_ext}":
        file_seg = f"Unknown - Track.{clean_ext}"

    if not file_seg.endswith(f".{clean_ext}"):
        file_seg = f"{file_seg}.{clean_ext}"

    target_path = base_dir
    for d in dir_segments:
        target_path = target_path / d
    target_path = target_path / file_seg

    return target_path


def get_collision_free_path(target_path: Path) -> Path:
    """If target_path exists on disk, appends a numeric suffix: ' (1)', ' (2)', etc."""
    if not target_path.exists():
        return target_path

    parent = target_path.parent
    stem = target_path.stem
    suffix = target_path.suffix

    counter = 1
    match = re.search(r"^(.*?)\s*\((\d+)\)$", stem)
    base_stem = stem
    if match:
        base_stem = match.group(1).rstrip()
        counter = int(match.group(2)) + 1

    candidate = parent / f"{base_stem} ({counter}){suffix}"
    while candidate.exists():
        counter += 1
        candidate = parent / f"{base_stem} ({counter}){suffix}"

    return candidate


def write_m3u8(
    playlist_dir: Path | str,
    db: Session | None = None,
    tracks: list[models.Download] | None = None,
) -> Path | None:
    """Writes or refreshes a standardized UTF-8 playlist.m3u8 file inside playlist_dir.

    All track entries strictly use relative filenames so the music folder can be
    moved or mounted across media servers, network shares, and mobile players without broken links.
    Conforms strictly to #EXTM3U and #EXTINF metadata standards.
    If no completed tracks remain on disk, removes any existing playlist.m3u8 and returns None.
    """
    p_dir = Path(playlist_dir).resolve()
    # Guard against generating in root download directory or non-directory
    if p_dir == Path(DOWNLOAD_DIR).resolve() or not p_dir.is_dir():
        return None

    own_session = False
    if tracks is None and db is None:
        db = SessionLocal()
        own_session = True

    try:
        valid_tracks: list[tuple[str, str, str]] = []  # (artist, title, rel_path)

        if tracks is not None:
            track_list = tracks
        elif db is not None:
            candidates = (
                db.query(models.Download)
                .filter(
                    models.Download.status == "Completed",
                    models.Download.file_path.isnot(None),
                )
                .order_by(models.Download.id.asc())
                .all()
            )
            track_list = [
                t
                for t in candidates
                if t.file_path
                and (
                    Path(t.file_path).resolve().parent == p_dir
                    or p_dir in Path(t.file_path).resolve().parents
                )
            ]
        else:
            track_list = []

        for t in track_list:
            if t.file_path and os.path.isfile(t.file_path):
                try:
                    rel = os.path.relpath(t.file_path, p_dir).replace("\\", "/")
                except ValueError:
                    rel = os.path.basename(t.file_path)
                artist = (t.artist or "").strip()
                title = (t.title or "").strip()
                valid_tracks.append((artist, title, rel))

        m3u8_path = p_dir / "playlist.m3u8"

        if not valid_tracks:
            # No completed audio files remain; remove existing m3u8 if present
            if m3u8_path.exists():
                try:
                    m3u8_path.unlink()
                    logger.info(f"Removed empty playlist.m3u8 from {p_dir}")
                except OSError as e:
                    logger.warning(f"Could not remove {m3u8_path}: {e}")
            return None

        lines = ["#EXTM3U"]
        for artist, title, rel in valid_tracks:
            if artist and artist.lower() not in ("unknown artist", "unknown", ""):
                entry_name = f"{artist} - {title}"
            else:
                entry_name = title or "Unknown Track"
            lines.append(f"#EXTINF:-1,{entry_name}")
            lines.append(rel)

        content = "\n".join(lines) + "\n"
        m3u8_path.write_text(content, encoding="utf-8")
        logger.info(
            f"Generated playlist.m3u8 at {m3u8_path} with {len(valid_tracks)} tracks."
        )
        return m3u8_path
    finally:
        if own_session and db is not None:
            db.close()


def handle_spotify(
    url: str,
    db: Session,
    job_id: str,
    file_format: str = "opus",
    synced_playlist_id: int | None = None,
    audio_bitrate: str = "best",
    target_playlist_title: str | None = None,
) -> str:
    """Handles Spotify downloads with spotdl, applying delta-sync.

    On-demand downloads (synced_playlist_id is None) support granular per-track
    abort via the download_manager process registry.
    Synced Playlist jobs (synced_playlist_id is set) are not abortable.
    """
    temp_file = f"temp_{job_id}.spotdl"
    is_on_demand = synced_playlist_id is None

    try:
        settings = db.query(models.Settings).first()
        auth_args: list[str] = []
        if settings and settings.spotify_client_id and settings.spotify_client_secret:
            auth_args = [
                "--client-id",
                str(settings.spotify_client_id).strip(),
                "--client-secret",
                str(settings.spotify_client_secret).strip(),
            ]

        # Generate metadata (single blocking call — fast, not per-track abortable)
        cmd = (
            get_spotdl_cmd()
            + auth_args
            + [
                "--yt-dlp-args",
                "extractor-args=youtube:player_client=android,web,ios",
                "save",
                url,
                "--save-file",
                temp_file,
            ]
        )
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=3600,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
        except subprocess.CalledProcessError as e:
            error_output = (
                e.stderr.strip()
                if e.stderr and e.stderr.strip()
                else (e.stdout.strip() if e.stdout else "Unknown error")
            )
            raise RuntimeError(f"spotdl save failed: {error_output}")

        with open(temp_file, "r") as f:
            metadata = json.load(f)

        # Delete placeholder now that we have real metadata
        placeholder = (
            db.query(models.Download).filter(models.Download.track_id == job_id).first()
        )
        if placeholder:
            db.delete(placeholder)
            db.commit()

        if not metadata:
            return "Empty URL"

        is_playlist = len(metadata) > 1 or bool(target_playlist_title)
        main_title = (
            target_playlist_title
            or (metadata[0].get("list_name") if metadata and metadata[0].get("list_name") else None)
            or (metadata[0].get("name", "Spotify Track") if metadata else "Spotify Track")
        )
        job_title_to_save = target_playlist_title or (main_title if is_playlist else None)
        list_name = target_playlist_title or (metadata[0].get("list_name", "") if (metadata and is_playlist) else "")
        sanitized_list_name = spotdl_sanitize(list_name) if list_name else ""

        to_download = []
        for track in metadata:
            raw_id = track.get("song_id")
            track_id = f"spotify_{raw_id}" if raw_id else uuid.uuid4().hex
            track_source_url = f"https://open.spotify.com/track/{raw_id}" if raw_id else url
            if not check_exists(db, track_id):
                to_download.append((track, track_id, track_source_url))
                insert_download(
                    db,
                    track_id,
                    track.get("name", "Unknown Title"),
                    track.get("artist", "Unknown Artist"),
                    None,
                    "Downloading",
                    job_id,
                    job_title_to_save,
                    synced_playlist_id,
                    source_url=track_source_url,
                )

        if not to_download:
            if is_playlist and sanitized_list_name:
                write_m3u8(Path(DOWNLOAD_DIR) / sanitized_list_name, db=db)
            logger.info("All tracks already downloaded.")
            return main_title

        # Per-track download loop with abort support
        for track, track_id, track_source_url in to_download:
            # Check abort signal before starting each track
            if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                logger.info(f"Track {track_id} aborted before download start.")
                insert_download(
                    db, track_id,
                    track.get("name", "Unknown Title"),
                    track.get("artist", "Unknown Artist"),
                    None, "Aborted", job_id, job_title_to_save, synced_playlist_id,
                    source_url=track_source_url,
                )
                continue

            title = track.get("name", "Unknown Title")
            artist = track.get("artist", "Unknown Artist")
            list_name = track.get("list_name", "")
            sanitized_title = spotdl_sanitize(title)
            sanitized_artist = spotdl_sanitize(artist)
            sanitized_list_name = spotdl_sanitize(list_name) if list_name else ""

            file_path = (
                f"{DOWNLOAD_DIR}/{sanitized_list_name}/{sanitized_artist} - {sanitized_title}.{file_format}"
                if list_name
                else f"{DOWNLOAD_DIR}/{sanitized_artist} - {sanitized_title}.{file_format}"
            )

            # Write single-track temp file for spotdl
            track_temp_file = f"temp_{job_id}_{track_id}.spotdl"
            with open(track_temp_file, "w") as f:
                json.dump([track], f)

            cmd_dl = (
                get_spotdl_cmd()
                + auth_args
                + [
                    "--yt-dlp-args",
                    "extractor-args=youtube:player_client=android,web,ios",
                    track_temp_file,
                    "--output",
                    f"{DOWNLOAD_DIR}/{{list-name}}/{{artist}} - {{title}}.{file_format}"
                    if list_name
                    else f"{DOWNLOAD_DIR}/{{artist}} - {{title}}.{file_format}",
                    "--format",
                    file_format,
                    "--bitrate",
                    "auto" if audio_bitrate == "best" else audio_bitrate,
                ]
            )

            aborted = False
            try:
                proc = subprocess.Popen(
                    cmd_dl,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=SUBPROCESS_CREATIONFLAGS,
                )
                if is_on_demand:
                    download_manager.register_process(job_id, track_id, proc)

                _stdout, _stderr = proc.communicate(timeout=3600)

                if is_on_demand:
                    download_manager.unregister_process(job_id, track_id)

                # Re-check abort in case signal arrived during download
                if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                    aborted = True

                if proc.returncode != 0 and not aborted:
                    err_output = (
                        _stderr.strip()
                        if _stderr
                        else (_stdout.strip() if _stdout else "Unknown error")
                    )
                    if len(err_output) > 2000:
                        err_output = err_output[-2000:]
                    logger.error(
                        f"spotdl failed for track {track_id} (exit code {proc.returncode}): {err_output}"
                    )
            except Exception as e:
                if is_on_demand:
                    download_manager.unregister_process(job_id, track_id)
                if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                    aborted = True
                else:
                    logger.error(f"spotdl failed for track {track_id}: {e}")

            if aborted:
                logger.info(f"Track {track_id} aborted — cleaning up partial files.")
                cleanup_partial_files(
                    output_path=file_path,
                    temp_files=[track_temp_file],
                )
                insert_download(
                    db, track_id, title, artist, None, "Aborted",
                    job_id, job_title_to_save, synced_playlist_id,
                    source_url=track_source_url,
                )
                if is_on_demand and os.path.exists(track_temp_file):
                    os.remove(track_temp_file)
                continue

            # Mark completed if file exists on disk
            if os.path.exists(file_path):
                insert_download(
                    db, track_id, title, artist, file_path, "Completed",
                    job_id, job_title_to_save, synced_playlist_id,
                    source_url=track_source_url,
                )
            else:
                logger.error(f"spotdl output file not found: {file_path}")
                insert_download(
                    db, track_id, title, artist, None, "Failed",
                    job_id, job_title_to_save, synced_playlist_id,
                    source_url=track_source_url,
                )

            if os.path.exists(track_temp_file):
                os.remove(track_temp_file)

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        if is_playlist and sanitized_list_name:
            write_m3u8(Path(DOWNLOAD_DIR) / sanitized_list_name, db=db)

        return main_title

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        if is_on_demand:
            download_manager.cleanup_job(job_id)


def handle_ytdlp(
    url: str,
    db: Session,
    job_id: str,
    media_type: str = "audio",
    file_format: str = "opus",
    synced_playlist_id: int | None = None,
    resolution_cap: str = "best",
    audio_bitrate: str = "best",
    target_playlist_title: str | None = None,
) -> str:
    """Handles generic yt-dlp downloads for non-Spotify URLs (YouTube, SoundCloud, Bandcamp, etc.).

    On-demand downloads (synced_playlist_id is None) support granular per-track
    abort via the download_manager process registry.
    Synced Playlist jobs (synced_playlist_id is set) are not abortable.
    """
    is_youtube = bool(re.search(r"(youtube\.com|youtu\.be)", url))
    is_on_demand = synced_playlist_id is None

    try:
        cmd_meta = get_ytdlp_cmd() + ["--yes-playlist", "--ignore-errors"]
        if is_youtube:
            if media_type == "video":
                cmd_meta.extend(
                    ["--extractor-args", "youtube:player_client=web_embedded,android"]
                )
            else:
                cmd_meta.extend(
                    ["--extractor-args", "youtube:player_client=android,web,ios"]
                )
        cmd_meta.extend(["-J", "--flat-playlist", url])

        result = subprocess.run(
            cmd_meta,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=SUBPROCESS_CREATIONFLAGS,
        )

        # Delete placeholder now that we have real metadata
        placeholder = (
            db.query(models.Download).filter(models.Download.track_id == job_id).first()
        )
        if placeholder:
            db.delete(placeholder)
            db.commit()

        to_download = []
        metadata = json.loads(result.stdout)
        main_title = metadata.get("title", "Audio Download")
        extractor = (
            metadata.get("extractor_key") or metadata.get("extractor") or "ytdlp"
        )
        entries = metadata.get("entries")

        if entries:
            is_playlist = True
            job_title_to_save = main_title
            tracks_data = entries
        else:
            is_playlist = bool(target_playlist_title)
            job_title_to_save = target_playlist_title
            tracks_data = [metadata]

        playlist_folder_name = job_title_to_save if job_title_to_save else main_title
        sanitized_playlist_title = yt_dlp_sanitize(playlist_folder_name) if is_playlist else ""

        for track in tracks_data:
            raw_id = track.get("id")
            if not raw_id:
                raw_id = uuid.uuid4().hex
            track_id = f"{extractor.lower()}_{raw_id}"

            # Resolve canonical URL for this track
            track_source_url = track.get("webpage_url") or track.get("url")
            if not track_source_url or not track_source_url.startswith("http"):
                if is_youtube and track.get("id"):
                    track_source_url = f"https://www.youtube.com/watch?v={track['id']}"
                else:
                    track_source_url = url

            if not check_exists(db, track_id):
                to_download.append((track, track_id, track_source_url))
                insert_download(
                    db,
                    track_id,
                    track.get("title", "Unknown Title"),
                    track.get("uploader") or track.get("artist") or "Unknown Artist",
                    None,
                    "Downloading",
                    job_id,
                    job_title_to_save,
                    synced_playlist_id,
                    source_url=track_source_url,
                )

        if not to_download:
            if is_playlist and sanitized_playlist_title:
                write_m3u8(Path(DOWNLOAD_DIR) / sanitized_playlist_title, db=db)
            logger.info("All tracks already downloaded.")
            return main_title

        # Per-track download loop with abort support
        failed_count = 0
        for track, track_id, track_source_url in to_download:
            # Check abort signal before starting each track
            if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                logger.info(f"Track {track_id} aborted before download start.")
                title = track.get("title", "Unknown Title")
                artist = track.get("uploader") or track.get("artist") or "Unknown Artist"
                insert_download(
                    db, track_id, title, artist, None, "Aborted",
                    job_id, job_title_to_save, synced_playlist_id,
                    source_url=track_source_url,
                )
                continue

            title = track.get("title", "Unknown Title")
            artist = track.get("uploader") or track.get("artist") or "Unknown Artist"
            sanitized_title = yt_dlp_sanitize(title)

            file_path = (
                f"{DOWNLOAD_DIR}/{sanitized_playlist_title}/{sanitized_title}.{file_format}"
                if is_playlist
                else f"{DOWNLOAD_DIR}/{sanitized_title}.{file_format}"
            )

            # Resolve canonical URL for this track
            target_url = track_source_url

            # Build the yt-dlp download command for this single track
            output_tmpl = (
                f"{DOWNLOAD_DIR}/{sanitized_playlist_title}/%(title)s.%(ext)s"
                if is_playlist and sanitized_playlist_title
                else f"{DOWNLOAD_DIR}/%(title)s.%(ext)s"
            )
            cmd_dl = get_ytdlp_cmd() + ["--ignore-errors"]
            if is_youtube:
                if media_type == "video":
                    cmd_dl.extend(
                        ["--extractor-args", "youtube:player_client=web_embedded,android"]
                    )
                else:
                    cmd_dl.extend(
                        ["--extractor-args", "youtube:player_client=android,web,ios"]
                    )

            if media_type == "audio":
                quality_val = "0" if audio_bitrate == "best" else audio_bitrate
                cmd_dl.extend(
                    [
                        "-x",
                        "--audio-format",
                        file_format,
                        "--audio-quality",
                        quality_val,
                        "--windows-filenames",
                        "-o",
                        output_tmpl,
                        target_url,
                    ]
                )
            else:
                cap = resolution_cap.strip() if resolution_cap else "best"
                if cap and cap != "best":
                    fmt = f"bestvideo[height<=?{cap}]+bestaudio/best[height<=?{cap}]/best"
                else:
                    fmt = "bestvideo+bestaudio/best"
                cmd_dl.extend(
                    [
                        "-f",
                        fmt,
                        "--merge-output-format",
                        file_format,
                        "--windows-filenames",
                        "-o",
                        output_tmpl,
                        target_url,
                    ]
                )

            aborted = False
            try:
                proc = subprocess.Popen(
                    cmd_dl,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=SUBPROCESS_CREATIONFLAGS,
                )
                if is_on_demand:
                    download_manager.register_process(job_id, track_id, proc)

                _stdout, _stderr = proc.communicate(timeout=3600)

                if is_on_demand:
                    download_manager.unregister_process(job_id, track_id)

                # Re-check abort in case signal arrived during download
                if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                    aborted = True

                if proc.returncode != 0 and not aborted:
                    err_output = (
                        _stderr.strip()
                        if _stderr
                        else (_stdout.strip() if _stdout else "Unknown error")
                    )
                    if len(err_output) > 2000:
                        err_output = err_output[-2000:]
                    logger.error(
                        f"yt-dlp failed for track {track_id} (exit code {proc.returncode}): {err_output}"
                    )
            except Exception as e:
                if is_on_demand:
                    download_manager.unregister_process(job_id, track_id)
                if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                    aborted = True
                else:
                    logger.error(f"yt-dlp failed for track {track_id}: {e}")

            if aborted:
                logger.info(f"Track {track_id} aborted — cleaning up partial files.")
                cleanup_partial_files(output_path=file_path)
                insert_download(
                    db, track_id, title, artist, None, "Aborted",
                    job_id, job_title_to_save, synced_playlist_id,
                )
                continue

            # Verify file on disk (with sanitization fallback)
            if not os.path.exists(file_path):
                target_dir = os.path.dirname(file_path)
                matching_files = [
                    os.path.join(target_dir, f)
                    for f in (
                        os.listdir(target_dir) if os.path.exists(target_dir) else []
                    )
                    if f.endswith(f".{file_format}")
                    and sanitized_title[:20].lower() in f.lower()
                ]
                if matching_files:
                    file_path = matching_files[0]

            if os.path.exists(file_path):
                insert_download(
                    db,
                    track_id,
                    title,
                    artist,
                    file_path,
                    "Completed",
                    job_id,
                    job_title_to_save,
                    synced_playlist_id,
                    source_url=track_source_url,
                )
            else:
                failed_count += 1
                logger.error(f"Download output file not found: {file_path}.")
                insert_download(
                    db,
                    track_id,
                    title,
                    artist,
                    None,
                    "Failed",
                    job_id,
                    job_title_to_save,
                    synced_playlist_id,
                    source_url=track_source_url,
                )

        if failed_count == len(to_download):
            raise RuntimeError("All yt-dlp track downloads failed: no output files found on disk.")

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        if is_playlist and sanitized_playlist_title:
            write_m3u8(Path(DOWNLOAD_DIR) / sanitized_playlist_title, db=db)

        return main_title

    finally:
        if is_on_demand:
            download_manager.cleanup_job(job_id)


# Alias for backwards compatibility
handle_youtube = handle_ytdlp


def fetch_playlist_title(url: str, db: Session | None = None) -> tuple[str, bool]:
    """Fetches the title and single-track boolean for a URL."""
    if re.search(r"(spotify\.com)", url):
        temp_file = f"temp_title_{uuid.uuid4().hex}.spotdl"
        try:
            auth_args: list[str] = []
            if db:
                settings = db.query(models.Settings).first()
                if (
                    settings
                    and settings.spotify_client_id
                    and settings.spotify_client_secret
                ):
                    auth_args = [
                        "--client-id",
                        str(settings.spotify_client_id).strip(),
                        "--client-secret",
                        str(settings.spotify_client_secret).strip(),
                    ]
            cmd = get_spotdl_cmd() + auth_args + ["save", url, "--save-file", temp_file]
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
            with open(temp_file, "r") as f:
                data = json.load(f)
            if data and isinstance(data, list):
                title = (
                    data[0].get("list_name")
                    or data[0].get("name")
                    or "Synced Spotify Playlist"
                )
                is_single = len(data) <= 1
                return title, is_single
        except Exception as e:
            logger.error(f"Failed to fetch Spotify playlist title: {e}")
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        return "Synced Spotify Playlist", False
    else:
        try:
            cmd = get_ytdlp_cmd() + ["--yes-playlist", "-J", "--flat-playlist", url]
            if re.search(r"(youtube\.com|youtu\.be)", url):
                cmd = (
                    get_ytdlp_cmd()
                    + [
                        "--yes-playlist",
                        "--extractor-args",
                        "youtube:player_client=android,web,ios",
                        "-J",
                        "--flat-playlist",
                        url,
                    ]
                )
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
            data = json.loads(result.stdout)
            title = data.get("title") or data.get("playlist_title") or "Synced Playlist"
            entries = data.get("entries")
            is_single = not entries or len(entries) <= 1
            return title, is_single
        except Exception as e:
            logger.error(f"Failed to fetch yt-dlp playlist title: {e}")
            return "Synced Playlist", False


def sync_playlist_job(synced_playlist_id: int, db: Session) -> dict:
    """Executes periodic or manual sync for a Synced Playlist entity."""
    sp = (
        db.query(models.SyncedPlaylist)
        .filter(models.SyncedPlaylist.id == synced_playlist_id)
        .first()
    )
    if not sp or not sp.is_active or not sp.url:
        return {"status": "skipped", "message": "Playlist not found, paused, or missing URL"}

    sp.status = "Syncing"
    db.commit()

    job_id = uuid.uuid4().hex
    url: str = sp.url
    is_spotify = bool(re.search(r"(spotify\.com)", url))

    try:
        # Step 1: Extract 100% verified metadata
        remote_tracks = []
        if is_spotify:
            temp_file = f"temp_sync_{job_id}.spotdl"
            try:
                settings = db.query(models.Settings).first()
                auth_args: list[str] = []
                if (
                    settings
                    and settings.spotify_client_id
                    and settings.spotify_client_secret
                ):
                    auth_args = [
                        "--client-id",
                        str(settings.spotify_client_id).strip(),
                        "--client-secret",
                        str(settings.spotify_client_secret).strip(),
                    ]
                cmd = get_spotdl_cmd() + auth_args + ["save", url, "--save-file", temp_file]
                subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=1200,
                    creationflags=SUBPROCESS_CREATIONFLAGS,
                )
                if os.path.exists(temp_file):
                    with open(temp_file, "r") as f:
                        metadata = json.load(f)
                    for t in metadata:
                        raw_id = t.get("song_id")
                        if raw_id:
                            remote_tracks.append(
                                (
                                    f"spotify_{raw_id}",
                                    t.get("name"),
                                    t.get("artist"),
                                )
                            )
                if not remote_tracks:
                    has_credentials = bool(
                        settings
                        and settings.spotify_client_id
                        and settings.spotify_client_secret
                    )
                    if not has_credentials:
                        sp.last_error = "0 tracks found. Ensure playlist is Public in Spotify and Spotify Client ID/Secret are set in Settings."
                    else:
                        sp.last_error = "0 tracks found. Ensure playlist is set to Public in Spotify."
            finally:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
        else:
            is_youtube = bool(re.search(r"(youtube\.com|youtu\.be)", url))
            cmd = get_ytdlp_cmd() + ["--yes-playlist", "--ignore-errors"]
            if is_youtube:
                cmd.extend(
                    ["--extractor-args", "youtube:player_client=android,web,ios"]
                )
            cmd.extend(["-J", "--flat-playlist", url])
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1200,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
            data = json.loads(result.stdout)
            extractor = data.get("extractor_key") or data.get("extractor") or "ytdlp"
            entries = data.get("entries") or [data]
            for t in entries:
                raw_id = t.get("id")
                if raw_id:
                    remote_tracks.append(
                        (
                            f"{extractor.lower()}_{raw_id}",
                            t.get("title"),
                            t.get("uploader") or t.get("artist"),
                        )
                    )

        remote_track_ids = {t[0] for t in remote_tracks}

        # Step 2: Handle Mirror / Prune mode if active
        pruned_count = 0
        affected_folders: set[Path] = set()
        if sp.sync_mode == "mirror":
            existing_downloads = (
                db.query(models.Download)
                .filter(models.Download.synced_playlist_id == sp.id)
                .all()
            )
            for dl in existing_downloads:
                if dl.track_id not in remote_track_ids:
                    if dl.file_path:
                        p = Path(dl.file_path).resolve().parent
                        if p != Path(DOWNLOAD_DIR).resolve():
                            affected_folders.add(p)
                    if dl.file_path and os.path.exists(dl.file_path):
                        try:
                            os.remove(dl.file_path)
                        except Exception as e:
                            logger.error(
                                f"Failed to delete pruned file {dl.file_path}: {e}"
                            )
                    db.delete(dl)
                    pruned_count += 1
            db.commit()

        # Step 3: Trigger downloads for new tracks
        settings = db.query(models.Settings).first()
        fmt = (
            settings.default_audio_format
            if settings and settings.default_audio_format
            else "opus"
        )
        bitrate = (
            settings.default_audio_bitrate
            if settings and settings.default_audio_bitrate
            else "best"
        )

        if is_spotify:
            title = handle_spotify(
                url,
                db,
                job_id,
                fmt,
                synced_playlist_id=sp.id,
                audio_bitrate=bitrate,
            )
        else:
            title = handle_ytdlp(
                url,
                db,
                job_id,
                "audio",
                fmt,
                synced_playlist_id=sp.id,
                audio_bitrate=bitrate,
            )

        completed_sp_downloads = (
            db.query(models.Download)
            .filter(
                models.Download.synced_playlist_id == sp.id,
                models.Download.status == "Completed",
                models.Download.file_path.isnot(None),
            )
            .all()
        )
        for dl in completed_sp_downloads:
            if dl.file_path:
                p = Path(dl.file_path).resolve().parent
                if p != Path(DOWNLOAD_DIR).resolve():
                    affected_folders.add(p)

        if sp.title:
            cand = Path(DOWNLOAD_DIR) / yt_dlp_sanitize(sp.title)
            if cand.is_dir():
                affected_folders.add(cand.resolve())

        for folder in affected_folders:
            write_m3u8(folder, db=db)

        sp.status = "Active"
        sp.last_synced_at = datetime.now(timezone.utc)
        sp.last_error = None
        db.commit()

        logger.info(f"Sync for playlist '{sp.title}' finished. Pruned: {pruned_count}.")
        return {"status": "success", "title": title, "pruned": pruned_count}

    except Exception as e:
        logger.error(f"Sync failed for playlist '{sp.title}': {e}", exc_info=True)
        sp.status = "Failed"
        sp.last_error = str(e)[:300]
        db.commit()
        send_telegram_notification(
            f"Sync Error: {sp.title}\n\nError: {str(e)[:200]}", is_error=True
        )
        return {"status": "error", "message": str(e)}
