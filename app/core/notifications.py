import httpx
import logging
from app.core.config import settings
from app.db.database import SessionLocal
from app.db import models

logger = logging.getLogger(__name__)

def send_telegram_notification(title: str):
    """Sends a Telegram notification for a completed download."""
    db = SessionLocal()
    db_settings = db.query(models.Settings).first()
    bot_token = (db_settings.telegram_bot_token if db_settings and db_settings.telegram_bot_token else None) or settings.telegram_bot_token
    chat_id = (db_settings.telegram_chat_id if db_settings and db_settings.telegram_chat_id else None) or settings.telegram_chat_id
    db.close()

    if not bot_token or not chat_id:
        logger.info("Telegram configuration missing. Skipping notification.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    message = f"Download complete: {title}"
    
    try:
        response = httpx.post(url, json={
            "chat_id": chat_id,
            "text": message
        })
        response.raise_for_status()
        logger.info(f"Telegram notification sent for: {title}")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
