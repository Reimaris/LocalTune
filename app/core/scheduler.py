import asyncio
import logging

from app.core.downloader import sync_playlist_job
from app.db import models
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)

# Default 6-hour sync interval in seconds
DEFAULT_SYNC_INTERVAL_SECONDS = 21600


def get_sync_interval_seconds() -> int:
    """Retrieve the sync interval in seconds from settings, enforcing a 2-hour minimum."""
    db = SessionLocal()
    try:
        settings = db.query(models.Settings).first()
        if settings and settings.sync_interval_hours is not None:
            return max(2, int(settings.sync_interval_hours)) * 3600
    except Exception as e:
        logger.warning(f"Could not read sync_interval_hours from settings: {e}")
    finally:
        db.close()
    return DEFAULT_SYNC_INTERVAL_SECONDS


def get_sync_on_startup() -> bool:
    """Check whether startup sync is enabled in settings (default True)."""
    db = SessionLocal()
    try:
        settings = db.query(models.Settings).first()
        if settings is not None and settings.sync_on_startup is not None:
            return bool(settings.sync_on_startup)
    except Exception as e:
        logger.warning(f"Could not read sync_on_startup from settings: {e}")
    finally:
        db.close()
    return True


async def run_sync_cycle() -> None:
    """Execute a single delta-sync pass across all active Synced Playlists."""
    db = SessionLocal()
    try:
        active_playlists = (
            db.query(models.SyncedPlaylist)
            .filter(models.SyncedPlaylist.is_active.is_(True))
            .all()
        )
        if active_playlists:
            logger.info(
                f"Periodic sync check running for {len(active_playlists)} active playlist(s)..."
            )
            for playlist in active_playlists:
                if playlist.id is None:
                    continue
                try:
                    # Run synchronous sync job in a worker thread so main HTTP loop is never blocked
                    await asyncio.to_thread(sync_playlist_job, playlist.id, db)
                except Exception as e:
                    logger.error(
                        f"Error syncing playlist '{playlist.title}' (ID {playlist.id}): {e}"
                    )
        else:
            logger.info("Periodic sync check: No active playlists found.")
    except Exception as e:
        logger.error(f"Error in run_sync_cycle: {e}")
    finally:
        db.close()


async def periodic_sync_loop():
    """Background task loop that periodically syncs active playlists."""
    logger.info("Starting Playlist Sync background scheduler...")

    # Delay initial check slightly on startup so dashboard loads instantly
    await asyncio.sleep(5)

    if get_sync_on_startup():
        logger.info("Startup sync enabled: running initial sync check...")
        await run_sync_cycle()
    else:
        logger.info("Startup sync disabled: skipping initial sync check.")

    while True:
        interval_seconds = get_sync_interval_seconds()
        logger.info(
            f"Scheduler sleeping for {interval_seconds // 3600} hour(s) ({interval_seconds}s) until next sync cycle..."
        )
        await asyncio.sleep(interval_seconds)
        await run_sync_cycle()
