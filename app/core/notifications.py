import httpx
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

def send_telegram_notification(title: str):
    """Sends a Telegram notification for a completed download."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("Telegram configuration missing. Skipping notification.")
        return

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    message = f"Download complete: {title}"
    
    try:
        response = httpx.post(url, json={
            "chat_id": settings.telegram_chat_id,
            "text": message
        })
        response.raise_for_status()
        logger.info(f"Telegram notification sent for: {title}")
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
