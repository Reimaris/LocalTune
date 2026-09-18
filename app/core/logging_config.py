import asyncio
import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Create a global queue for log events
log_queue: asyncio.Queue[str] = asyncio.Queue()


class SafeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """
    TimedRotatingFileHandler that safely handles Windows file locking on rollover.
    If the file is temporarily locked by another process (such as a viewer or supervisor),
    suppress PermissionError to avoid log storming and retry on subsequent cycles.
    """

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            pass


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


class EndpointFilter(logging.Filter):
    """
    Suppresses noisy polling logs for HTMX background updates.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/api/tracks" not in msg and "/api/synced-playlists" not in msg


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
    file_handler = SafeTimedRotatingFileHandler(
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

    # Silence noisy third-party loggers and polling endpoints
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.addFilter(EndpointFilter())

    return root_logger
