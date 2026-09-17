import json
import logging
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from spotdl.utils.formatter import sanitize_string as spotdl_sanitize
from sqlalchemy.orm import Session
from yt_dlp.utils import sanitize_filename as yt_dlp_sanitize

from app.core.notifications import send_telegram_notification
from app.core.process_registry import cleanup_partial_files, download_manager
from app.db import models

logger = logging.getLogger(__name__)
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
):
    """Inserts or updates a download record in the database."""
    dl = db.query(models.Download).filter(models.Download.track_id == track_id).first()
    if dl:
        dl.title = title
        dl.artist = artist
        if file_path:
            dl.file_path = file_path
        dl.status = status
        dl.job_id = job_id
        dl.job_title = job_title
        if synced_playlist_id:
            dl.synced_playlist_id = synced_playlist_id
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


def handle_spotify(
    url: str,
    db: Session,
    job_id: str,
    file_format: str = "opus",
    synced_playlist_id: int | None = None,
    audio_bitrate: str = "best",
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
            ["spotdl"]
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

        is_playlist = len(metadata) > 1
        main_title = (
            metadata[0].get("list_name", "Spotify Playlist")
            if is_playlist and metadata[0].get("list_name")
            else metadata[0].get("name", "Spotify Track")
        )
        job_title_to_save = main_title if is_playlist else None

        to_download = []
        for track in metadata:
            raw_id = track.get("song_id")
            track_id = f"spotify_{raw_id}" if raw_id else uuid.uuid4().hex
            if not check_exists(db, track_id):
                to_download.append((track, track_id))
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
                )

        if not to_download:
            logger.info("All tracks already downloaded.")
            return main_title

        # Per-track download loop with abort support
        for track, track_id in to_download:
            # Check abort signal before starting each track
            if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                logger.info(f"Track {track_id} aborted before download start.")
                insert_download(
                    db, track_id,
                    track.get("name", "Unknown Title"),
                    track.get("artist", "Unknown Artist"),
                    None, "Aborted", job_id, job_title_to_save, synced_playlist_id,
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
                ["spotdl"]
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
                )
                if is_on_demand and os.path.exists(track_temp_file):
                    os.remove(track_temp_file)
                continue

            # Mark completed if file exists on disk
            if os.path.exists(file_path):
                insert_download(
                    db, track_id, title, artist, file_path, "Completed",
                    job_id, job_title_to_save, synced_playlist_id,
                )
            else:
                logger.error(f"spotdl output file not found: {file_path}")
                insert_download(
                    db, track_id, title, artist, None, "Failed",
                    job_id, job_title_to_save, synced_playlist_id,
                )

            if os.path.exists(track_temp_file):
                os.remove(track_temp_file)

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

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
) -> str:
    """Handles generic yt-dlp downloads for non-Spotify URLs (YouTube, SoundCloud, Bandcamp, etc.).

    On-demand downloads (synced_playlist_id is None) support granular per-track
    abort via the download_manager process registry.
    Synced Playlist jobs (synced_playlist_id is set) are not abortable.
    """
    is_youtube = bool(re.search(r"(youtube\.com|youtu\.be)", url))
    is_on_demand = synced_playlist_id is None

    try:
        cmd_meta = ["yt-dlp", "--yes-playlist", "--ignore-errors"]
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
            is_playlist = False
            job_title_to_save = None
            tracks_data = [metadata]

        sanitized_playlist_title = yt_dlp_sanitize(main_title) if is_playlist else ""

        for track in tracks_data:
            raw_id = track.get("id")
            if not raw_id:
                raw_id = uuid.uuid4().hex
            track_id = f"{extractor.lower()}_{raw_id}"

            if not check_exists(db, track_id):
                to_download.append((track, track_id))
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
                )

        if not to_download:
            logger.info("All tracks already downloaded.")
            return main_title

        # Per-track download loop with abort support
        failed_count = 0
        for track, track_id in to_download:
            # Check abort signal before starting each track
            if is_on_demand and download_manager.is_track_aborted(job_id, track_id):
                logger.info(f"Track {track_id} aborted before download start.")
                title = track.get("title", "Unknown Title")
                artist = track.get("uploader") or track.get("artist") or "Unknown Artist"
                insert_download(
                    db, track_id, title, artist, None, "Aborted",
                    job_id, job_title_to_save, synced_playlist_id,
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
            target_url = track.get("webpage_url") or track.get("url")
            if not target_url or not target_url.startswith("http"):
                if is_youtube and track.get("id"):
                    target_url = f"https://www.youtube.com/watch?v={track['id']}"
                else:
                    target_url = url

            # Build the yt-dlp download command for this single track
            output_tmpl = (
                f"{DOWNLOAD_DIR}/{sanitized_playlist_title}/%(title)s.%(ext)s"
                if is_playlist and sanitized_playlist_title
                else f"{DOWNLOAD_DIR}/%(title)s.%(ext)s"
            )
            cmd_dl = ["yt-dlp", "--ignore-errors"]
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
                )

        if failed_count == len(to_download):
            raise RuntimeError("All yt-dlp track downloads failed: no output files found on disk.")

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

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
            cmd = ["spotdl"] + auth_args + ["save", url, "--save-file", temp_file]
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
            cmd = ["yt-dlp", "--yes-playlist", "-J", "--flat-playlist", url]
            if re.search(r"(youtube\.com|youtu\.be)", url):
                cmd = [
                    "yt-dlp",
                    "--yes-playlist",
                    "--extractor-args",
                    "youtube:player_client=android,web,ios",
                    "-J",
                    "--flat-playlist",
                    url,
                ]
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
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
                cmd = ["spotdl"] + auth_args + ["save", url, "--save-file", temp_file]
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
            cmd = ["yt-dlp", "--yes-playlist", "--ignore-errors"]
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
        if sp.sync_mode == "mirror":
            existing_downloads = (
                db.query(models.Download)
                .filter(models.Download.synced_playlist_id == sp.id)
                .all()
            )
            for dl in existing_downloads:
                if dl.track_id not in remote_track_ids:
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
