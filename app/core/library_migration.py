import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.downloader import (
    DOWNLOAD_DIR,
    get_collision_free_path,
    resolve_track_path,
    write_m3u8,
)
from app.db import models
from app.db.database import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class MigrationStatus:
    is_running: bool = False
    total: int = 0
    processed: int = 0
    moved: int = 0
    collisions: int = 0
    errors: int = 0
    current_file: str = ""
    error_messages: list[str] = field(default_factory=list)


migration_state = MigrationStatus()
_migration_lock = threading.Lock()


def run_library_migration() -> dict[str, Any]:
    """Executes the library migration across all completed tracks on disk."""
    with _migration_lock:
        if migration_state.is_running:
            return {"status": "already_running"}

        migration_state.is_running = True
        migration_state.total = 0
        migration_state.processed = 0
        migration_state.moved = 0
        migration_state.collisions = 0
        migration_state.errors = 0
        migration_state.current_file = ""
        migration_state.error_messages.clear()

    db = SessionLocal()
    affected_folders: set[Path] = set()

    try:
        settings = db.query(models.Settings).first()
        template = (
            settings.naming_template
            if settings and settings.naming_template
            else "{playlist}/{artist} - {title}.{ext}"
        )

        downloads = (
            db.query(models.Download)
            .filter(
                models.Download.status == "Completed",
                models.Download.file_path.isnot(None),
            )
            .all()
        )

        valid_tracks = [
            t for t in downloads if t.file_path and os.path.isfile(t.file_path)
        ]
        migration_state.total = len(valid_tracks)

        logger.info(
            f"Starting library migration for {len(valid_tracks)} files using template: '{template}'"
        )

        for track in valid_tracks:
            try:
                assert track.file_path is not None
                current_path = Path(track.file_path).resolve()
                migration_state.current_file = current_path.name

                # Resolve playlist name
                playlist_name = ""
                if track.synced_playlist_id is not None:
                    sp = (
                        db.query(models.SyncedPlaylist)
                        .filter_by(id=track.synced_playlist_id)
                        .first()
                    )
                    playlist_name = (sp.title or "") if sp else (track.job_title or "")
                else:
                    playlist_name = track.job_title or ""

                artist = track.artist or ""
                title = track.title or ""
                ext = current_path.suffix.lstrip(".") or "opus"

                target_path = resolve_track_path(
                    template=template,
                    artist=artist,
                    title=title,
                    playlist=playlist_name,
                    ext=ext,
                    download_dir=DOWNLOAD_DIR,
                ).resolve()

                if target_path == current_path:
                    migration_state.processed += 1
                    continue

                if target_path.exists():
                    target_path = get_collision_free_path(target_path)
                    migration_state.collisions += 1
                    logger.warning(
                        f"Target collision for '{track.title}': appending suffix to {target_path.name}"
                    )

                target_path.parent.mkdir(parents=True, exist_ok=True)
                old_parent = current_path.parent

                shutil.move(str(current_path), str(target_path))

                track.file_path = str(target_path)
                db.commit()

                migration_state.moved += 1
                migration_state.processed += 1

                affected_folders.add(old_parent)
                affected_folders.add(target_path.parent)

            except Exception as e:
                migration_state.errors += 1
                msg = f"Error migrating '{track.title}': {e}"
                logger.error(msg, exc_info=True)
                migration_state.error_messages.append(msg)
                migration_state.processed += 1

        # Post-migration M3U8 refresh & empty folder cleanup
        for folder in affected_folders:
            if folder != Path(DOWNLOAD_DIR).resolve() and folder.is_dir():
                write_m3u8(folder, db=db)
                try:
                    os.rmdir(folder)
                    logger.info(f"Purged empty directory: {folder}")
                except OSError:
                    pass

        summary = {
            "status": "completed",
            "total": migration_state.total,
            "processed": migration_state.processed,
            "moved": migration_state.moved,
            "collisions": migration_state.collisions,
            "errors": migration_state.errors,
        }
        logger.info(f"Library migration finished: {summary}")
        return summary

    finally:
        migration_state.is_running = False
        migration_state.current_file = ""
        db.close()


def start_background_migration() -> bool:
    """Starts run_library_migration in a daemon thread if not already running."""
    if migration_state.is_running:
        return False
    t = threading.Thread(target=run_library_migration, daemon=True)
    t.start()
    return True
