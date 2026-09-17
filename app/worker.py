import logging
import re

from app.core.downloader import handle_spotify, handle_ytdlp, sync_playlist_job
from app.core.notifications import send_telegram_notification
from app.db import models
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)


def process_download(
    job_id: str,
    url: str,
    media_type: str = "audio",
    file_format: str = "opus",
    resolution_cap: str = "best",
    audio_bitrate: str = "best",
):
    """
    Background task to process downloads, apply delta-sync, and notify.
    """
    logger.info(f"Worker started processing: {url}")
    db = SessionLocal()

    try:
        if re.search(r"(spotify\.com)", url):
            logger.info("Routing to Spotify handler...")
            title = handle_spotify(
                url, db, job_id, file_format, audio_bitrate=audio_bitrate
            )
        else:
            logger.info("Routing to generic yt-dlp fallback handler...")
            title = handle_ytdlp(
                url,
                db,
                job_id,
                media_type,
                file_format,
                resolution_cap=resolution_cap,
                audio_bitrate=audio_bitrate,
            )

        # Remove the placeholder row now that real tracks are registered
        placeholder = (
            db.query(models.Download).filter(models.Download.track_id == job_id).first()
        )
        if placeholder:
            db.delete(placeholder)
            db.commit()

        logger.info(f"Download handled successfully: {title}")
        metadata_str = (
            f"URL: {url}\nFormat: {file_format.upper()} ({media_type.capitalize()})"
        )
        send_telegram_notification(f"{title}\n\n{metadata_str}")

    except Exception as e:
        logger.error(f"Worker failed processing {url}: {e}", exc_info=True)
        # Mark all tracks in this job as failed
        failed_tracks = (
            db.query(models.Download).filter(models.Download.job_id == job_id).all()
        )
        for track in failed_tracks:
            track.status = "Failed"
        db.commit()
        metadata_str = f"URL: {url}\nFormat: {file_format.upper()} ({media_type.capitalize()})\nError: {str(e)[:200]}"
        send_telegram_notification(f"Processing Error\n\n{metadata_str}", is_error=True)
    finally:
        db.close()


def process_playlist_sync(synced_playlist_id: int):
    """Background task to execute sync for a Synced Playlist entity."""
    logger.info(f"Worker executing sync for playlist ID: {synced_playlist_id}")
    db = SessionLocal()
    try:
        sync_playlist_job(synced_playlist_id, db)
    except Exception as e:
        logger.error(f"Error executing playlist sync for {synced_playlist_id}: {e}")
    finally:
        db.close()
