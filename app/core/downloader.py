import subprocess
import json
import os
import uuid
import logging
import re
from pathlib import Path
from datetime import datetime, timezone
from spotdl.utils.formatter import sanitize_string as spotdl_sanitize
from yt_dlp.utils import sanitize_filename as yt_dlp_sanitize
from sqlalchemy.orm import Session
from app.db import models
from app.core.notifications import send_telegram_notification

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/downloads"))


def check_exists(db: Session, track_id: str) -> bool:
    """Checks if a track ID already exists in the database and is completed."""
    dl = db.query(models.Download).filter(models.Download.track_id == track_id).first()
    return dl is not None and dl.status == "Completed"


def insert_download(
    db: Session,
    track_id: str,
    title: str,
    artist: str,
    file_path: str,
    status: str = "Completed",
    job_id: str = None,
    job_title: str = None,
    synced_playlist_id: int = None,
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
    synced_playlist_id: int = None,
) -> str:
    """Handles Spotify downloads with spotdl, applying delta-sync."""
    temp_file = f"temp_{job_id}.spotdl"

    try:
        settings = db.query(models.Settings).first()
        auth_args = []
        if settings and settings.spotify_client_id and settings.spotify_client_secret:
            auth_args = [
                "--client-id",
                settings.spotify_client_id.strip(),
                "--client-secret",
                settings.spotify_client_secret.strip(),
            ]

        # Generate metadata
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
                cmd, check=True, capture_output=True, text=True, timeout=3600
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
                to_download.append(track)
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

        # Update metadata file for delta sync
        with open(temp_file, "w") as f:
            json.dump(to_download, f)

        # Download using built-in template
        cmd_dl = (
            ["spotdl"]
            + auth_args
            + [
                "--yt-dlp-args",
                "extractor-args=youtube:player_client=android,web,ios",
                temp_file,
                "--output",
                f"/downloads/{{list-name}}/{{artist}} - {{title}}.{file_format}",
                "--format",
                file_format,
            ]
        )
        try:
            subprocess.run(
                cmd_dl, check=True, capture_output=True, text=True, timeout=3600
            )
        except subprocess.CalledProcessError as e:
            error_output = (
                e.stderr.strip()
                if e.stderr and e.stderr.strip()
                else (e.stdout.strip() if e.stdout else "Unknown error")
            )
            raise RuntimeError(f"spotdl download failed: {error_output}")

        # Mark as completed
        for track in to_download:
            raw_id = track.get("song_id")
            track_id = f"spotify_{raw_id}" if raw_id else uuid.uuid4().hex
            title = track.get("name", "Unknown Title")
            artist = track.get("artist", "Unknown Artist")
            list_name = track.get("list_name", "")

            sanitized_title = spotdl_sanitize(title)
            sanitized_artist = spotdl_sanitize(artist)
            sanitized_list_name = spotdl_sanitize(list_name) if list_name else ""

            file_path = (
                f"/downloads/{sanitized_list_name}/{sanitized_artist} - {sanitized_title}.{file_format}"
                if list_name
                else f"/downloads/{sanitized_artist} - {sanitized_title}.{file_format}"
            )
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

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        return main_title

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def handle_ytdlp(
    url: str,
    db: Session,
    job_id: str,
    media_type: str = "audio",
    file_format: str = "opus",
    synced_playlist_id: int = None,
) -> str:
    """Handles generic yt-dlp downloads for non-Spotify URLs (YouTube, SoundCloud, Bandcamp, etc.)."""
    batch_file = f"batch_{job_id}.txt"
    is_youtube = bool(re.search(r"(youtube\.com|youtu\.be)", url))

    try:
        cmd_meta = ["yt-dlp"]
        if is_youtube:
            cmd_meta.extend(["--extractor-args", "youtube:player_client=android,web,ios"])
        cmd_meta.extend(["-J", "--flat-playlist", url])

        result = subprocess.run(
            cmd_meta,
            check=True,
            capture_output=True,
            text=True,
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

        # Create batch file using canonical webpage_url / url
        with open(batch_file, "w") as f:
            for track, _ in to_download:
                target_url = track.get("webpage_url") or track.get("url")
                if not target_url or not target_url.startswith("http"):
                    if is_youtube and track.get("id"):
                        target_url = f"https://www.youtube.com/watch?v={track['id']}"
                    else:
                        target_url = url
                f.write(f"{target_url}\n")

        # Download remaining tracks
        cmd_dl = ["yt-dlp"]
        if is_youtube:
            cmd_dl.extend(["--extractor-args", "youtube:player_client=android,web,ios"])

        if media_type == "audio":
            cmd_dl.extend([
                "-x",
                "--audio-format",
                file_format,
                "--audio-quality",
                "0",
                "--windows-filenames",
                "-a",
                batch_file,
                "-o",
                "/downloads/%(playlist_title|)s/%(title)s.%(ext)s",
            ])
        else:
            cmd_dl.extend([
                "-f",
                "bestvideo+bestaudio/best",
                "--merge-output-format",
                file_format,
                "--windows-filenames",
                "-a",
                batch_file,
                "-o",
                "/downloads/%(playlist_title|)s/%(title)s.%(ext)s",
            ])

        subprocess.run(cmd_dl, check=True)

        # Mark as completed
        for track, track_id in to_download:
            title = track.get("title", "Unknown Title")
            artist = track.get("uploader") or track.get("artist") or "Unknown Artist"

            sanitized_title = yt_dlp_sanitize(title)
            sanitized_playlist_title = (
                yt_dlp_sanitize(main_title) if is_playlist else ""
            )

            file_path = (
                f"/downloads/{sanitized_playlist_title}/{sanitized_title}.{file_format}"
                if is_playlist
                else f"/downloads/{sanitized_title}.{file_format}"
            )
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

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        return main_title

    finally:
        if os.path.exists(batch_file):
            os.remove(batch_file)


# Alias for backwards compatibility
handle_youtube = handle_ytdlp


def fetch_playlist_title(url: str, db: Session = None) -> str:
    """Fetches the official title of a playlist from Spotify or generic yt-dlp."""
    if re.search(r"(spotify\.com)", url):
        temp_file = f"temp_title_{uuid.uuid4().hex}.spotdl"
        try:
            auth_args = []
            if db:
                settings = db.query(models.Settings).first()
                if (
                    settings
                    and settings.spotify_client_id
                    and settings.spotify_client_secret
                ):
                    auth_args = [
                        "--client-id",
                        settings.spotify_client_id.strip(),
                        "--client-secret",
                        settings.spotify_client_secret.strip(),
                    ]
            cmd = ["spotdl"] + auth_args + ["save", url, "--save-file", temp_file]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
            with open(temp_file, "r") as f:
                data = json.load(f)
            if data and isinstance(data, list):
                return (
                    data[0].get("list_name")
                    or data[0].get("name")
                    or "Synced Spotify Playlist"
                )
        except Exception as e:
            logger.error(f"Failed to fetch Spotify playlist title: {e}")
        finally:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        return "Synced Spotify Playlist"
    else:
        try:
            cmd = ["yt-dlp", "-J", "--flat-playlist", url]
            if re.search(r"(youtube\.com|youtu\.be)", url):
                cmd = [
                    "yt-dlp",
                    "--extractor-args",
                    "youtube:player_client=android,web,ios",
                    "-J",
                    "--flat-playlist",
                    url,
                ]
            result = subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=120
            )
            data = json.loads(result.stdout)
            return data.get("title") or data.get("playlist_title") or "Synced Playlist"
        except Exception as e:
            logger.error(f"Failed to fetch yt-dlp playlist title: {e}")
            return "Synced Playlist"


def sync_playlist_job(synced_playlist_id: int, db: Session) -> dict:
    """Executes periodic or manual sync for a Synced Playlist entity."""
    sp = (
        db.query(models.SyncedPlaylist)
        .filter(models.SyncedPlaylist.id == synced_playlist_id)
        .first()
    )
    if not sp or not sp.is_active:
        return {"status": "skipped", "message": "Playlist not found or paused"}

    sp.status = "Syncing"
    db.commit()

    job_id = uuid.uuid4().hex
    url = sp.url
    is_spotify = bool(re.search(r"(spotify\.com)", url))

    try:
        # Step 1: Extract 100% verified metadata
        remote_tracks = []
        if is_spotify:
            temp_file = f"temp_sync_{job_id}.spotdl"
            try:
                settings = db.query(models.Settings).first()
                auth_args = []
                if (
                    settings
                    and settings.spotify_client_id
                    and settings.spotify_client_secret
                ):
                    auth_args = [
                        "--client-id",
                        settings.spotify_client_id.strip(),
                        "--client-secret",
                        settings.spotify_client_secret.strip(),
                    ]
                cmd = ["spotdl"] + auth_args + ["save", url, "--save-file", temp_file]
                subprocess.run(
                    cmd, check=True, capture_output=True, text=True, timeout=1200
                )
                with open(temp_file, "r") as f:
                    metadata = json.load(f)
                for t in metadata:
                    raw_id = t.get("song_id")
                    if raw_id:
                        remote_tracks.append((
                            f"spotify_{raw_id}",
                            t.get("name"),
                            t.get("artist"),
                        ))
            finally:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
        else:
            is_youtube = bool(re.search(r"(youtube\.com|youtu\.be)", url))
            cmd = ["yt-dlp"]
            if is_youtube:
                cmd.extend(["--extractor-args", "youtube:player_client=android,web,ios"])
            cmd.extend(["-J", "--flat-playlist", url])
            result = subprocess.run(
                cmd, check=True, capture_output=True, text=True, timeout=1200
            )
            data = json.loads(result.stdout)
            extractor = (
                data.get("extractor_key") or data.get("extractor") or "ytdlp"
            )
            entries = data.get("entries") or [data]
            for t in entries:
                raw_id = t.get("id")
                if raw_id:
                    remote_tracks.append((
                        f"{extractor.lower()}_{raw_id}",
                        t.get("title"),
                        t.get("uploader") or t.get("artist"),
                    ))

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
        if is_spotify:
            title = handle_spotify(url, db, job_id, "opus", synced_playlist_id=sp.id)
        else:
            title = handle_ytdlp(
                url, db, job_id, "audio", "opus", synced_playlist_id=sp.id
            )

        sp.status = "Active"
        sp.last_synced_at = datetime.now(timezone.utc)
        sp.last_error = None
        db.commit()

        logger.info(
            f"Sync for playlist '{sp.title}' finished. Pruned: {pruned_count}."
        )
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
