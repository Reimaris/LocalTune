import logging
import re

from sqlalchemy.orm import Session

from app.core.downloader import (
    get_canonical_source_url,
    handle_spotify,
    handle_ytdlp,
    sync_playlist_job,
)
from app.core.notifications import send_telegram_notification
from app.core.process_registry import download_manager
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


def retry_single_track(track_db_id: int, db: Session | None = None):
    """Background worker task to retry downloading a single track in Failed/Aborted/Deleted state."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        track = db.query(models.Download).filter(models.Download.id == track_db_id).first()
        if not track:
            logger.error(f"Cannot retry track {track_db_id}: not found in DB")
            return

        canonical_url = get_canonical_source_url(track)
        if not canonical_url:
            logger.error(f"Cannot retry track {track_db_id}: could not determine canonical source URL")
            track.status = "Failed"
            db.commit()
            return

        settings = db.query(models.Settings).first()
        file_format = settings.default_audio_format if settings and settings.default_audio_format else "opus"
        audio_bitrate = settings.default_audio_bitrate if settings and settings.default_audio_bitrate else "best"

        job_id = track.job_id or track.track_id or "retry_job"
        logger.info(f"Retrying download for track {track.id} ({track.title}) via {canonical_url} (job_id: {job_id})")

        if re.search(r"(spotify\.com)", canonical_url):
            handle_spotify(
                canonical_url,
                db,
                job_id,
                file_format=file_format,
                audio_bitrate=audio_bitrate,
                target_playlist_title=track.job_title,
            )
        else:
            handle_ytdlp(
                canonical_url,
                db,
                job_id,
                media_type="audio",
                file_format=file_format,
                resolution_cap="best",
                audio_bitrate=audio_bitrate,
                target_playlist_title=track.job_title,
            )
    except Exception as e:
        logger.error(f"Failed retrying track {track_db_id}: {e}", exc_info=True)
        track = db.query(models.Download).filter(models.Download.id == track_db_id).first()
        if track:
            track.status = "Failed"
            db.commit()
    finally:
        if close_db:
            db.close()


def retry_job_batch(job_id: str, db: Session | None = None):
    """Background worker task to retry all child tracks in a job batch that are in Queued state."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    try:
        queued_tracks = (
            db.query(models.Download)
            .filter(
                models.Download.job_id == job_id,
                models.Download.synced_playlist_id.is_(None),
                models.Download.status == "Queued",
            )
            .all()
        )
        if not queued_tracks:
            logger.info(f"No queued tracks found to retry for job {job_id}")
            return

        for track in queued_tracks:
            tid = str(track.track_id or "")
            if download_manager.is_job_aborted(job_id) or (tid and download_manager.is_track_aborted(job_id, tid)):
                track.status = "Aborted"
                db.commit()
                continue

            if track.id is not None:
                retry_single_track(int(track.id), db=db)
    except Exception as e:
        logger.error(f"Error executing batch retry for job {job_id}: {e}", exc_info=True)
    finally:
        if close_db:
            db.close()
