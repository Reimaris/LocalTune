import subprocess
import json
import os
import uuid
import logging
from pathlib import Path
from spotdl.utils.formatter import sanitize_string as spotdl_sanitize
from yt_dlp.utils import sanitize_filename as yt_dlp_sanitize
from sqlalchemy.orm import Session
from app.db import models

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("/downloads")

def check_exists(db: Session, track_id: str) -> bool:
    """Checks if a track ID already exists in the database and is completed."""
    dl = db.query(models.Download).filter(models.Download.track_id == track_id).first()
    return dl is not None and dl.status == "Completed"

def insert_download(db: Session, track_id: str, title: str, artist: str, file_path: str, status: str = "Completed", job_id: str = None, job_title: str = None):
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
    else:
        dl = models.Download(track_id=track_id, title=title, artist=artist, file_path=file_path, status=status, job_id=job_id, job_title=job_title)
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

def handle_spotify(url: str, db: Session, job_id: str, file_format: str = "opus") -> str:
    """Handles Spotify downloads with spotdl, applying delta-sync."""
    temp_file = f"temp_{job_id}.spotdl"
    
    try:
        settings = db.query(models.Settings).first()
        auth_args = []
        if settings and settings.spotify_client_id and settings.spotify_client_secret:
            auth_args = ["--client-id", settings.spotify_client_id.strip(), "--client-secret", settings.spotify_client_secret.strip()]

        # Generate metadata
        cmd = ["spotdl"] + auth_args + ["--yt-dlp-args", "extractor-args=youtube:player_client=android", "save", url, "--save-file", temp_file]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3600)
        except subprocess.CalledProcessError as e:
            error_output = e.stderr.strip() if e.stderr and e.stderr.strip() else (e.stdout.strip() if e.stdout else "Unknown error")
            raise RuntimeError(f"spotdl save failed: {error_output}")
        
        with open(temp_file, "r") as f:
            metadata = json.load(f)
            
        # Delete placeholder now that we have real metadata
        placeholder = db.query(models.Download).filter(models.Download.track_id == job_id).first()
        if placeholder:
            db.delete(placeholder)
            db.commit()
        
        if not metadata:
            return "Empty URL"
            
        is_playlist = len(metadata) > 1
        main_title = metadata[0].get("list_name", "Spotify Playlist") if is_playlist and metadata[0].get("list_name") else metadata[0].get("name", "Spotify Track")
        job_title_to_save = main_title if is_playlist else None

        to_download = []
        for track in metadata:
            track_id = track.get("song_id")
            if not track_id or not check_exists(db, track_id):
                to_download.append(track)
                insert_download(db, track_id or uuid.uuid4().hex, track.get("name", "Unknown Title"), track.get("artist", "Unknown Artist"), None, "Downloading", job_id, job_title_to_save)
        
        if not to_download:
            logger.info("All tracks already downloaded.")
            return main_title

        # Update metadata file for delta sync
        with open(temp_file, "w") as f:
            json.dump(to_download, f)

        # Download using built-in template
        cmd_dl = ["spotdl"] + auth_args + [
            "--yt-dlp-args", "extractor-args=youtube:player_client=android", temp_file,
            "--output", f"/downloads/{{list-name}}/{{artist}} - {{title}}.{file_format}",
            "--format", file_format
        ]
        try:
            subprocess.run(cmd_dl, check=True, capture_output=True, text=True, timeout=3600)
        except subprocess.CalledProcessError as e:
            error_output = e.stderr.strip() if e.stderr and e.stderr.strip() else (e.stdout.strip() if e.stdout else "Unknown error")
            raise RuntimeError(f"spotdl download failed: {error_output}")
        
        # Mark as completed
        for track in to_download:
            track_id = track.get("song_id")
            title = track.get("name", "Unknown Title")
            artist = track.get("artist", "Unknown Artist")
            list_name = track.get("list_name", "")
            
            sanitized_title = spotdl_sanitize(title)
            sanitized_artist = spotdl_sanitize(artist)
            sanitized_list_name = spotdl_sanitize(list_name) if list_name else ""
            
            file_path = f"/downloads/{sanitized_list_name}/{sanitized_artist} - {sanitized_title}.{file_format}" if list_name else f"/downloads/{sanitized_artist} - {sanitized_title}.{file_format}"
            insert_download(db, track_id, title, artist, file_path, "Completed", job_id, job_title_to_save)

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        return main_title

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def handle_youtube(url: str, db: Session, job_id: str, media_type: str = "audio", file_format: str = "opus") -> str:
    """Handles YouTube downloads with yt-dlp, applying delta-sync."""
    batch_file = f"batch_{job_id}.txt"

    try:
        # Extract flat metadata to skip downloading large JSON dumps for videos themselves
        result = subprocess.run(["yt-dlp", "--extractor-args", "youtube:player_client=android", "-J", "--flat-playlist", url], check=True, capture_output=True, text=True)
        
        # Delete placeholder now that we have real metadata
        placeholder = db.query(models.Download).filter(models.Download.track_id == job_id).first()
        if placeholder:
            db.delete(placeholder)
            db.commit()
            
        to_download = []
        
        metadata = json.loads(result.stdout)
        main_title = metadata.get("title", "YouTube Video")
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
            track_id = track.get("id")
            if not track_id:
                track_id = uuid.uuid4().hex

            if not check_exists(db, track_id):
                to_download.append(track)
                insert_download(db, track_id, track.get("title", "Unknown Title"), track.get("uploader", "Unknown Artist"), None, "Downloading", job_id, job_title_to_save)

        if not to_download:
            logger.info("All tracks already downloaded.")
            return main_title

        # Create batch file to avoid subprocess argument limit
        with open(batch_file, "w") as f:
            for track in to_download:
                f.write(f"https://www.youtube.com/watch?v={track['id']}\n")

        # Download remaining tracks using built-in template
        if media_type == "audio":
            cmd_dl = [
                "yt-dlp",
                "--extractor-args", "youtube:player_client=android",
                "-x",
                "--audio-format", file_format,
                "--audio-quality", "0",
                "--windows-filenames",
                "-a", batch_file,
                "-o", "/downloads/%(playlist_title|)s/%(title)s.%(ext)s"
            ]
        else:
            cmd_dl = [
                "yt-dlp",
                "--extractor-args", "youtube:player_client=android",
                "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", file_format,
                "--windows-filenames",
                "-a", batch_file,
                "-o", "/downloads/%(playlist_title|)s/%(title)s.%(ext)s"
            ]
            
        subprocess.run(cmd_dl, check=True)

        # Mark as completed
        for track in to_download:
            track_id = track.get("id")
            title = track.get("title", "Unknown Title")
            artist = track.get("uploader", "Unknown Artist")
            
            sanitized_title = yt_dlp_sanitize(title)
            sanitized_playlist_title = yt_dlp_sanitize(main_title) if is_playlist else ""
            
            file_path = f"/downloads/{sanitized_playlist_title}/{sanitized_title}.{file_format}" if is_playlist else f"/downloads/{sanitized_title}.{file_format}"
            insert_download(db, track_id, title, artist, file_path, "Completed", job_id, job_title_to_save)

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        return main_title

    finally:
        if os.path.exists(batch_file):
            os.remove(batch_file)

