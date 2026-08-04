import subprocess
import json
import os
import uuid
import shutil
import logging
from pathlib import Path
from sqlalchemy.orm import Session
from app.db import models

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("/downloads")

def check_exists(db: Session, track_id: str) -> bool:
    """Checks if a track ID already exists in the database."""
    return db.query(models.Download).filter(models.Download.track_id == track_id).first() is not None

def insert_download(db: Session, track_id: str, title: str, artist: str, file_path: str):
    """Inserts a new download record into the database."""
    dl = models.Download(track_id=track_id, title=title, artist=artist, file_path=file_path)
    db.add(dl)
    db.commit()

def handle_spotify(url: str, db: Session) -> str:
    """Handles Spotify downloads with spotdl, applying delta-sync."""
    job_id = uuid.uuid4().hex
    temp_file = f"temp_{job_id}.spotdl"
    job_dir = DOWNLOAD_DIR / job_id
    
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
        
        if not to_download:
            logger.info("All tracks already downloaded.")
            return metadata[0].get("name", "Spotify Tracks")

        # Update metadata file for delta sync
        with open(temp_file, "w") as f:
            json.dump(to_download, f)

        # Download remaining tracks into job folder
        job_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["spotdl", temp_file], cwd=str(job_dir), check=True)
        
        # Insert to db
        for track in to_download:
            track_id = track.get("song_id", uuid.uuid4().hex)
            title = track.get("name", "Unknown Title")
            artist = track.get("artist", "Unknown Artist")
            insert_download(db, track_id, title, artist, f"/downloads/{job_id}/{title}.mp3")

        is_playlist = len(metadata) > 1
        main_title = metadata[0].get("name", "Spotify Playlist") if not is_playlist else "Spotify Playlist"

        if is_playlist:
            zip_path = DOWNLOAD_DIR / f"{job_id}.zip"
            shutil.make_archive(str(DOWNLOAD_DIR / job_id), 'zip', str(job_dir))
            logger.info(f"Zipped playlist to {zip_path}")

        return main_title

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def handle_youtube(url: str, db: Session) -> str:
    """Handles YouTube downloads with yt-dlp, applying delta-sync."""
    job_id = uuid.uuid4().hex
    batch_file = f"batch_{job_id}.txt"
    job_dir = DOWNLOAD_DIR / job_id

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

        if not to_download:
            logger.info("All tracks already downloaded.")
            return main_title or "YouTube Video"

        # Create batch file to avoid subprocess argument limit
        with open(batch_file, "w") as f:
            for track in to_download:
                f.write(f"https://www.youtube.com/watch?v={track['id']}\n")

        # Download remaining tracks into job folder
        job_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "yt-dlp",
            "--paths", str(job_dir),
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "-a", batch_file
        ], check=True)

        # Insert to db
        for track in to_download:
            track_id = track.get("id", uuid.uuid4().hex)
            title = track.get("title", "Unknown Title")
            artist = track.get("uploader", "Unknown Artist")
            insert_download(db, track_id, title, artist, f"/downloads/{job_id}/{title}.mp3")

        is_playlist = len(lines) > 1
        if is_playlist:
            zip_path = DOWNLOAD_DIR / f"{job_id}.zip"
            shutil.make_archive(str(DOWNLOAD_DIR / job_id), 'zip', str(job_dir))
            logger.info(f"Zipped playlist to {zip_path}")

        return main_title or "YouTube Video"

    finally:
        if os.path.exists(batch_file):
            os.remove(batch_file)
