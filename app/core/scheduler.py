import asyncio
import logging
from app.db.database import SessionLocal
from app.db import models
from app.core.downloader import sync_playlist_job

logger = logging.getLogger(__name__)

# Global 6-hour sync interval in seconds
SYNC_INTERVAL_SECONDS = 21600


async def periodic_sync_loop():
    """Background task loop that runs every 6 hours to sync active playlists."""
    logger.info("Starting global 6-hour Playlist Sync background scheduler...")
    while True:
        try:
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
                        try:
                            sync_playlist_job(playlist.id, db)
                        except Exception as e:
                            logger.error(
                                f"Error syncing playlist '{playlist.title}' (ID {playlist.id}): {e}"
                            )
                else:
                    logger.info("Periodic sync check: No active playlists found.")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"Error in periodic_sync_loop: {e}")

        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
