import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

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
    log_file = log_dir / "app.log"

    # Define the common formatter
    formatter = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s", 
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console Handler for real-time stdout tracking
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # TimedRotatingFileHandler for persistent, rotated logging in config/logs/
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.suffix = "%Y-%m-%d.log"
    file_handler.setFormatter(formatter)

    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    # Clear any existing handlers to prevent duplicate logs
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return root_logger
