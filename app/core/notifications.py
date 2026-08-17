import httpx
import logging
from app.core.config import settings
from app.db.database import SessionLocal
from app.db import models

logger = logging.getLogger(__name__)


def send_telegram_notification(message_body: str, is_error: bool = False):
    """Sends a Telegram notification."""
    db = SessionLocal()
    db_settings = db.query(models.Settings).first()
    bot_token = (
        db_settings.telegram_bot_token
        if db_settings and db_settings.telegram_bot_token
        else None
    ) or settings.telegram_bot_token
    chat_id = (
        db_settings.telegram_chat_id
        if db_settings and db_settings.telegram_chat_id
        else None
    ) or settings.telegram_chat_id
    db.close()

    if not bot_token or not chat_id:
        logger.info("Telegram configuration missing. Skipping notification.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    prefix = "❌ Download failed" if is_error else "✅ Download complete"
    message = f"{prefix}: {message_body}"

    try:
        response = httpx.post(url, json={"chat_id": chat_id, "text": message})
        response.raise_for_status()
        logger.info(f"Telegram notification sent: {message_body}")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
