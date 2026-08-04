import subprocess
import json
import os
import uuid
import logging
from pathlib import Path
from sqlalchemy.orm import Session
from app.db import models

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("/downloads")

def check_exists(db: Session, track_id: str) -> bool:
    """Checks if a track ID already exists in the database and is completed."""
    dl = db.query(models.Download).filter(models.Download.track_id == track_id).first()
    return dl is not None and dl.status == "Completed"

def insert_download(db: Session, track_id: str, title: str, artist: str, file_path: str, status: str = "Completed", job_id: str = None):
    """Inserts or updates a download record in the database."""
    dl = db.query(models.Download).filter(models.Download.track_id == track_id).first()
    if dl:
        dl.title = title
        dl.artist = artist
        if file_path:
            dl.file_path = file_path
        dl.status = status
        dl.job_id = job_id
    else:
        dl = models.Download(track_id=track_id, title=title, artist=artist, file_path=file_path, status=status, job_id=job_id)
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

def handle_spotify(url: str, db: Session, job_id: str) -> str:
    """Handles Spotify downloads with spotdl, applying delta-sync."""
    temp_file = f"temp_{job_id}.spotdl"
    
    try:
        # Generate metadata
        subprocess.run(["spotdl", "save", url, "--save-file", temp_file], check=True, capture_output=True)
        with open(temp_file, "r") as f:
            metadata = json.load(f)
        
        if not metadata:
            return "Empty URL"

        to_download = []
        for track in metadata:
            track_id = track.get("song_id")
            if not track_id or not check_exists(db, track_id):
                to_download.append(track)
                insert_download(db, track_id or uuid.uuid4().hex, track.get("name", "Unknown Title"), track.get("artist", "Unknown Artist"), None, "Downloading", job_id)
        
        if not to_download:
            logger.info("All tracks already downloaded.")
            return metadata[0].get("name", "Spotify Tracks")

        # Update metadata file for delta sync
        with open(temp_file, "w") as f:
            json.dump(to_download, f)

        # Download using built-in template
        subprocess.run([
            "spotdl", temp_file,
            "--output", "/downloads/{list-name}/{artist} - {title}.{ext}"
        ], check=True)
        
        # Mark as completed
        for track in to_download:
            track_id = track.get("song_id")
            title = track.get("name", "Unknown Title")
            artist = track.get("artist", "Unknown Artist")
            list_name = track.get("list_name", "")
            file_path = f"/downloads/{list_name}/{artist} - {title}.mp3" if list_name else f"/downloads/{artist} - {title}.mp3"
            insert_download(db, track_id, title, artist, file_path, "Completed", job_id)

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        is_playlist = len(metadata) > 1
        main_title = metadata[0].get("list_name", "Spotify Playlist") if is_playlist and metadata[0].get("list_name") else metadata[0].get("name", "Spotify Track")

        return main_title

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def handle_youtube(url: str, db: Session, job_id: str) -> str:
    """Handles YouTube downloads with yt-dlp, applying delta-sync."""
    batch_file = f"batch_{job_id}.txt"

    try:
        # Extract flat metadata to skip downloading large JSON dumps for videos themselves
        result = subprocess.run(["yt-dlp", "-J", "--flat-playlist", url], check=True, capture_output=True, text=True)
        to_download = []
        main_title = None
        
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for line in lines:
            metadata = json.loads(line)
            if main_title is None:
                main_title = metadata.get("title", "YouTube Video")

            track_id = metadata.get("id")
            # If flat-playlist didn't yield an id, fallback
            if not track_id:
                track_id = uuid.uuid4().hex

            if not check_exists(db, track_id):
                to_download.append(metadata)
                insert_download(db, track_id, metadata.get("title", "Unknown Title"), metadata.get("uploader", "Unknown Artist"), None, "Downloading", job_id)

        if not to_download:
            logger.info("All tracks already downloaded.")
            return main_title or "YouTube Video"

        # Create batch file to avoid subprocess argument limit
        with open(batch_file, "w") as f:
            for track in to_download:
                f.write(f"https://www.youtube.com/watch?v={track['id']}\n")

        # Download remaining tracks using built-in template
        subprocess.run([
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "-a", batch_file,
            "-o", "/downloads/%(playlist_title|)s/%(title)s.%(ext)s"
        ], check=True)

        # Mark as completed
        for track in to_download:
            track_id = track.get("id")
            title = track.get("title", "Unknown Title")
            artist = track.get("uploader", "Unknown Artist")
            insert_download(db, track_id, title, artist, f"/downloads/{title}.mp3", "Completed", job_id)

        # Fix permissions on /downloads
        fix_permissions(DOWNLOAD_DIR)

        return main_title or "YouTube Video"

    finally:
        if os.path.exists(batch_file):
            os.remove(batch_file)

