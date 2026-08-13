import re
import uuid
from rq import get_current_job
from app.db.database import SessionLocal
from app.db import models
from app.core.downloader import handle_spotify, handle_youtube
from app.core.notifications import send_telegram_notification
from app.core.logging_config import setup_logging

logger = setup_logging()

def process_download(url: str, media_type: str = "audio", file_format: str = "opus"):
    """
    Background task to process downloads, apply delta-sync, and notify.
    """
    logger.info(f"Worker started processing: {url}")
    db = SessionLocal()
    job = get_current_job()
    job_id = job.id if job else uuid.uuid4().hex
    
    try:
        if re.search(r'(spotify\.com)', url):
            logger.info("Routing to Spotify handler...")
            title = handle_spotify(url, db, job_id, file_format)
        elif re.search(r'(youtube\.com|youtu\.be)', url):
            logger.info("Routing to YouTube handler...")
            title = handle_youtube(url, db, job_id, media_type, file_format)
        else:
            logger.error("Unsupported URL type in worker.")
            return

        logger.info(f"Download handled successfully: {title}")
        metadata_str = f"URL: {url}\nFormat: {file_format.upper()} ({media_type.capitalize()})"
        send_telegram_notification(f"{title}\n\n{metadata_str}")
        
    except Exception as e:
        logger.error(f"Worker failed processing {url}: {e}", exc_info=True)
        # Mark all tracks in this job as failed
        failed_tracks = db.query(models.Download).filter(models.Download.job_id == job_id).all()
        for track in failed_tracks:
            track.status = "Failed"
        db.commit()
        metadata_str = f"URL: {url}\nFormat: {file_format.upper()} ({media_type.capitalize()})\nError: {str(e)[:200]}"
        send_telegram_notification(f"Processing Error\n\n{metadata_str}", is_error=True)
    finally:
        db.close()
