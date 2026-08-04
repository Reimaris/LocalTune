import re
from app.db.database import SessionLocal
from app.core.downloader import handle_spotify, handle_youtube
from app.core.notifications import send_telegram_notification
from app.core.logging_config import setup_logging

logger = setup_logging()

def process_download(url: str):
    """
    Background task to process downloads, apply delta-sync, and notify.
    """
    logger.info(f"Worker started processing: {url}")
    db = SessionLocal()
    
    try:
        if re.search(r'(spotify\.com)', url):
            logger.info("Routing to Spotify handler...")
            title = handle_spotify(url, db)
        elif re.search(r'(youtube\.com|youtu\.be)', url):
            logger.info("Routing to YouTube handler...")
            title = handle_youtube(url, db)
        else:
            logger.error("Unsupported URL type in worker.")
            return

        logger.info(f"Download handled successfully: {title}")
        send_telegram_notification(title)
        
    except Exception as e:
        logger.error(f"Worker failed processing {url}: {e}", exc_info=True)
    finally:
        db.close()
