import logging
import sys
import asyncio
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Create a global queue for log events
log_queue = asyncio.Queue()


class AsyncQueueHandler(logging.Handler):
    """
    A custom logging handler that puts log messages into an asyncio queue.
    """

    def emit(self, record):
        try:
            msg = self.format(record)
            # Use the running event loop if it exists
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(log_queue.put_nowait, msg)
            except RuntimeError:
                pass
        except Exception:
            self.handleError(record)


async def log_generator():
    """
    Generator that yields logs formatted as Server-Sent Events (SSE).
    """
    while True:
        log_message = await log_queue.get()
        # Prevent newlines from breaking SSE format
        safe_msg = log_message.replace("\n", " ")
        yield f"data: {safe_msg}\n\n"


def setup_logging(log_level: str = "INFO"):
    """
    Sets up the application logging architecture.
    Ensures that the config/logs/ directory exists dynamically.
    Returns the configured root logger.
    """
    # Ensure config/logs/ exists dynamically based on standard volume structure
    config_dir = Path("config")
    log_dir = config_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Define the common formatter
    formatter = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console Handler for real-time stdout tracking
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # File Handler for daily rotation (midnight) keeping 5 backups
    file_handler = TimedRotatingFileHandler(
        filename=log_dir / "localtune.log",
        when="midnight",
        interval=1,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)

    # In-memory Queue Handler for SSE
    queue_handler = AsyncQueueHandler()
    queue_handler.setFormatter(formatter)

    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear any existing handlers to prevent duplicate logs
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(queue_handler)

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return root_logger
