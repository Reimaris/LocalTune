import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.downloader import DOWNLOAD_DIR
from app.db import models

_size_cache: dict[str, tuple[float, int]] = {}


def format_bytes(bytes_count: int) -> str:
    """Formats raw byte count into human-readable representation."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.1f} GB"


def get_directory_size(path: Path | str) -> int:
    """Recursively computes total disk bytes consumed by files in a directory."""
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return 0
    total = 0
    try:
        for root, _, files in os.walk(p):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.stat(fp).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def get_cached_directory_size(path: Path | str, ttl_seconds: float = 5.0) -> int:
    """Caches directory size for ttl_seconds to prevent disk I/O thrashing during frequent polling."""
    path_str = str(Path(path).resolve())
    now = time.monotonic()
    if ttl_seconds > 0 and path_str in _size_cache:
        cached_time, cached_size = _size_cache[path_str]
        if now - cached_time < ttl_seconds:
            return cached_size

    size = get_directory_size(path_str)
    _size_cache[path_str] = (now, size)
    return size


def get_library_metrics(
    db: Session,
    download_dir: Path | str | None = None,
    ttl_seconds: float = 5.0,
) -> dict[str, Any]:
    """Computes total active completed tracks and total storage footprint."""
    target_dir = download_dir if download_dir is not None else DOWNLOAD_DIR
    storage_bytes = get_cached_directory_size(target_dir, ttl_seconds=ttl_seconds)
    track_count = db.query(models.Download).filter(models.Download.status == "Completed").count()

    return {
        "total_tracks": track_count,
        "total_storage_bytes": storage_bytes,
        "total_storage_formatted": format_bytes(storage_bytes),
    }
