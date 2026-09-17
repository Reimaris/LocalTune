import asyncio
import os
import re
import shutil
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.downloader import DOWNLOAD_DIR, fetch_playlist_title, yt_dlp_sanitize
from app.core.logging_config import log_generator, setup_logging
from app.core.process_registry import download_manager
from app.core.scheduler import periodic_sync_loop
from app.db import models
from app.db.database import engine, get_db
from app.worker import process_download, process_playlist_sync

# Initialize logging before doing anything else
logger = setup_logging()
logger.info("Starting LocalTune application")

# Create database tables
models.Base.metadata.create_all(bind=engine)


try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE downloads ADD COLUMN job_title VARCHAR"))
        conn.commit()
except OperationalError:
    pass

try:
    with engine.connect() as conn:
        conn.execute(
            text("ALTER TABLE downloads ADD COLUMN synced_playlist_id INTEGER")
        )
        conn.commit()
except OperationalError:
    pass

try:
    with engine.connect() as conn:
        conn.execute(
            text(
                "ALTER TABLE settings ADD COLUMN enable_browser_downloads BOOLEAN DEFAULT 0"
            )
        )
        conn.commit()
except OperationalError:
    pass

for col_name, col_type, default_val in [
    ("naming_template", "VARCHAR", "'{playlist}/{artist} - {title}.{ext}'"),
    ("default_audio_format", "VARCHAR", "'opus'"),
    ("default_audio_bitrate", "VARCHAR", "'best'"),
    ("sync_interval_hours", "INTEGER", "6"),
    ("sync_on_startup", "BOOLEAN", "1"),
]:
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"ALTER TABLE settings ADD COLUMN {col_name} {col_type} DEFAULT {default_val}"
                )
            )
            conn.commit()
    except OperationalError:
        pass


def get_installed_ytdlp_version() -> str:
    """Detect the currently installed version of yt-dlp."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception as e:
        logger.warning(f"Failed to detect yt-dlp version: {e}")
    return "Unknown"


def run_ytdlp_upgrade() -> tuple[bool, str]:
    """Upgrade yt-dlp in-place using pip without cache."""
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--no-cache-dir",
                "yt-dlp",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            new_ver = get_installed_ytdlp_version()
            return True, f"Successfully updated yt-dlp to {new_ver}!"
        else:
            err = proc.stderr.strip() or proc.stdout.strip()
            return False, f"Failed to update yt-dlp: {err}"
    except Exception as e:
        return False, f"Error upgrading yt-dlp: {e!s}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern FastAPI lifespan manager.

    Starts the 6-hour periodic playlist sync background task on startup and
    cancels it cleanly on shutdown, suppressing the expected CancelledError.
    This replaces the legacy startup/shutdown hooks and eliminates the Uvicorn
    LifespanOn queue deadlock on Python 3.12 Windows Proactor event loops.
    """
    sync_task = asyncio.create_task(periodic_sync_loop())
    yield
    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="LocalTune", lifespan=lifespan)



# Setup static files and templates
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

downloads_dir = str(DOWNLOAD_DIR)
try:
    os.makedirs(downloads_dir, exist_ok=True)
    app.mount("/downloads", StaticFiles(directory=downloads_dir), name="downloads")
except OSError as e:
    logger.warning(f"Could not mount downloads directory {downloads_dir}: {e}")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Verification Route"""
    logger.info("Health check endpoint accessed.")
    try:
        count = db.query(models.Download).count()
        playlists_count = db.query(models.SyncedPlaylist).count()
        return {
            "status": "ok",
            "logger": "configured",
            "database": "connected",
            "downloads_count": count,
            "synced_playlists_count": playlists_count,
        }
    except OSError as e:
        logger.error(f"Database connection failed: {e}")
        return {"status": "error", "logger": "configured", "database": f"failed: {e}"}


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Renders the main dashboard."""
    context = {"request": request, "title": "LocalTune Dashboard"}
    return templates.TemplateResponse("dashboard.html", context)


@app.post("/download", response_class=HTMLResponse)
async def download_url(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    media_type: str = Form("audio"),
    file_format: str = Form("opus"),
    resolution_cap: str = Form("best"),
    audio_bitrate: str = Form("best"),
    db: Session = Depends(get_db),
):
    """Receives URL from frontend, validates it, and queues for download."""
    logger.info(f"Received download request for URL: {url}")

    if not url or not url.startswith(("http://", "https://")):
        return """
        <div class="bg-red-900 border border-red-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
          <strong class="font-bold">Error!</strong>
          <span class="block sm:inline">Invalid URL format. Please provide a valid HTTP/HTTPS link.</span>
        </div>
        """

    is_spotify = bool(re.search(r"(spotify\.com)", url))
    if is_spotify and media_type == "video":
        return """
        <div class="bg-red-900 border border-red-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
          <strong class="font-bold">Error!</strong>
          <span class="block sm:inline">Spotify does not support video downloads. Please select Music.</span>
        </div>
        """

    job_id = uuid.uuid4().hex
    background_tasks.add_task(
        process_download,
        job_id,
        url,
        media_type,
        file_format,
        resolution_cap,
        audio_bitrate,
    )

    try:
        new_download = models.Download(
            track_id=job_id,
            title=url,
            artist="Pending Metadata...",
            status="Queued",
            job_id=job_id,
        )
        db.add(new_download)
        db.commit()
    except OSError as e:
        logger.error(f"Failed to insert placeholder download: {e}")
        db.rollback()

    return f"""
    <div class="bg-emerald-900 border border-emerald-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
      <strong class="font-bold">Success!</strong>
      <span class="block sm:inline">Job queued for {url} (ID: {job_id})</span>
    </div>
    """


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    db_settings = db.query(models.Settings).first()
    if not db_settings:
        db_settings = models.Settings()
        db.add(db_settings)
        db.commit()

    ytdlp_version = get_installed_ytdlp_version()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "title": "Settings",
            "settings": db_settings,
            "ytdlp_version": ytdlp_version,
        },
    )


@app.post("/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    telegram_bot_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    spotify_client_id: str = Form(""),
    spotify_client_secret: str = Form(""),
    enable_browser_downloads: str | None = Form(None),
    naming_template: str = Form("{playlist}/{artist} - {title}.{ext}"),
    default_audio_format: str = Form("opus"),
    default_audio_bitrate: str = Form("best"),
    sync_interval_hours: int = Form(6),
    sync_on_startup: str | None = Form(None),
    db: Session = Depends(get_db),
):
    db_settings = db.query(models.Settings).first()
    if not db_settings:
        db_settings = models.Settings()
        db.add(db_settings)

    db_settings.telegram_bot_token = telegram_bot_token
    db_settings.telegram_chat_id = telegram_chat_id
    db_settings.spotify_client_id = spotify_client_id
    db_settings.spotify_client_secret = spotify_client_secret
    db_settings.enable_browser_downloads = bool(enable_browser_downloads)
    db_settings.naming_template = naming_template.strip() or "{playlist}/{artist} - {title}.{ext}"
    db_settings.default_audio_format = default_audio_format
    db_settings.default_audio_bitrate = default_audio_bitrate
    db_settings.sync_interval_hours = max(2, sync_interval_hours)
    db_settings.sync_on_startup = bool(sync_on_startup)
    db.commit()

    return """
    <div class="bg-emerald-900 border border-emerald-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
      <strong class="font-bold">Success!</strong>
      <span class="block sm:inline">Settings saved successfully.</span>
    </div>
    """


@app.post("/api/settings/update-ytdlp", response_class=HTMLResponse)
async def api_update_ytdlp():
    success, message = await asyncio.to_thread(run_ytdlp_upgrade)
    if success:
        return f"""
        <div class="bg-emerald-900/80 border border-emerald-500 text-white px-4 py-3 rounded-lg text-sm mb-4" role="alert">
          <strong class="font-bold">Success!</strong>
          <span class="block sm:inline">{message}</span>
        </div>
        """
    else:
        return f"""
        <div class="bg-red-900/80 border border-red-500 text-white px-4 py-3 rounded-lg text-sm mb-4" role="alert">
          <strong class="font-bold">Update Failed:</strong>
          <span class="block sm:inline">{message}</span>
        </div>
        """


@app.get("/api/tracks", response_class=HTMLResponse)
async def api_tracks(request: Request, db: Session = Depends(get_db)):
    settings = db.query(models.Settings).first()
    enable_browser_downloads = settings.enable_browser_downloads if settings else False
    tracks = (
        db.query(models.Download)
        .filter(models.Download.synced_playlist_id.is_(None))
        .order_by(models.Download.downloaded_at.desc())
        .limit(150)
        .all()
    )
    queued = (
        db.query(models.Download)
        .filter(
            models.Download.synced_playlist_id.is_(None),
            models.Download.status == "Queued",
        )
        .count()
    )
    downloading = (
        db.query(models.Download)
        .filter(
            models.Download.synced_playlist_id.is_(None),
            models.Download.status == "Downloading",
        )
        .count()
    )
    done = (
        db.query(models.Download)
        .filter(
            models.Download.synced_playlist_id.is_(None),
            models.Download.status == "Completed",
        )
        .count()
    )
    errors = (
        db.query(models.Download)
        .filter(
            models.Download.synced_playlist_id.is_(None),
            models.Download.status == "Failed",
        )
        .count()
    )

    grouped_jobs: dict[str, list] = {}
    for t in tracks:
        if t.job_id:
            if t.job_id not in grouped_jobs:
                grouped_jobs[t.job_id] = []
            grouped_jobs[t.job_id].append(t)

    seen_jobs = set()
    display_items = []

    for t in tracks:
        if not t.job_id or not t.job_title:
            display_items.append({"type": "track", "item": t})
        else:
            if t.job_id not in seen_jobs:
                seen_jobs.add(t.job_id)
                job_tracks = grouped_jobs[t.job_id]

                statuses = [child.status for child in job_tracks]
                if (
                    "Downloading" in statuses
                    or "Queued" in statuses
                    or "Fetching Metadata" in statuses
                ):
                    status = "Downloading"
                elif all(s == "Deleted" for s in statuses):
                    status = "Deleted"
                elif all(s == "Aborted" for s in statuses):
                    status = "Aborted"
                elif all(s == "Failed" for s in statuses):
                    status = "Failed"
                elif "Completed" in statuses:
                    status = "Completed"
                else:
                    status = "Completed"

                display_items.append(
                    {
                        "type": "playlist",
                        "title": t.job_title,
                        "job_id": t.job_id,
                        "status": status,
                        "tracks": job_tracks,
                    }
                )

    return templates.TemplateResponse(
        "partials/track_list.html",
        {
            "request": request,
            "display_items": display_items,
            "queued": queued + downloading,
            "done": done,
            "errors": errors,
            "enable_browser_downloads": enable_browser_downloads,
        },
    )


@app.delete("/api/tracks/{track_id}", response_class=HTMLResponse)
async def delete_track(request: Request, track_id: int, db: Session = Depends(get_db)):
    track = db.query(models.Download).filter(models.Download.id == track_id).first()
    if track:
        if track.file_path:
            parent_dir = os.path.dirname(track.file_path)
            if os.path.exists(track.file_path):
                try:
                    os.remove(track.file_path)
                except OSError as e:
                    logger.error(f"Could not delete file {track.file_path}: {e}")
            if parent_dir != str(DOWNLOAD_DIR) and parent_dir.startswith(
                str(DOWNLOAD_DIR)
            ):
                try:
                    os.rmdir(parent_dir)
                    logger.info(f"Purged empty playlist folder from disk: {parent_dir}")
                except OSError:
                    pass
        db.delete(track)
        db.commit()
    return ""


@app.post("/api/tracks/{track_id}/delete", response_class=HTMLResponse)
async def soft_delete_track(
    request: Request, track_id: int, db: Session = Depends(get_db)
):
    """Soft-deletes a terminal on-demand track.

    Removes the physical file and transitions status to 'Deleted'.
    Active tracks (Queued/Downloading) and synced playlist tracks are rejected.
    """
    track = db.query(models.Download).filter(models.Download.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    if track.synced_playlist_id is not None:
        raise HTTPException(
            status_code=400, detail="Synced playlist tracks cannot be deleted here"
        )

    if track.status in ("Queued", "Downloading"):
        # Cannot soft-delete active tracks; must be aborted first
        return await _render_tracks(request, db)

    if track.file_path:
        parent_dir = os.path.dirname(track.file_path)
        if os.path.exists(track.file_path):
            try:
                os.remove(track.file_path)
            except OSError as e:
                logger.error(f"Could not delete file {track.file_path}: {e}")
        if parent_dir != str(DOWNLOAD_DIR) and parent_dir.startswith(str(DOWNLOAD_DIR)):
            try:
                os.rmdir(parent_dir)
                logger.info(f"Purged empty playlist folder from disk: {parent_dir}")
            except OSError:
                pass

    track.status = "Deleted"
    track.file_path = None
    db.commit()
    logger.info(f"Track {track_id} soft-deleted by user.")

    return await _render_tracks(request, db)


@app.post("/api/tracks/{track_id}/abort", response_class=HTMLResponse)
async def abort_track(request: Request, track_id: int, db: Session = Depends(get_db)):
    """Abort an individual on-demand track that is Queued or Downloading.

    Synced Playlist tracks (synced_playlist_id is set) are rejected.
    Signals the download_manager to terminate the active subprocess and
    marks the track status as Aborted in the database.
    """
    track = db.query(models.Download).filter(models.Download.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    # Safety guard: only on-demand downloads are abortable
    if track.synced_playlist_id is not None:
        raise HTTPException(
            status_code=400, detail="Synced playlist tracks cannot be aborted"
        )

    if track.status not in ("Queued", "Downloading"):
        # Already terminal — return refreshed list without erroring
        return await _render_tracks(request, db)

    job_id = track.job_id or track.track_id
    if not job_id or not track.track_id:
        # Cannot abort without identifiers — just mark Aborted in DB
        track.status = "Aborted"
        db.commit()
        return await _render_tracks(request, db)

    download_manager.abort_track(job_id, track.track_id)

    # Eagerly mark the record as Aborted so polling reflects the change immediately
    track.status = "Aborted"
    db.commit()
    logger.info(
        f"Track {track_id} (track_id={track.track_id}) marked as Aborted by user."
    )

    return await _render_tracks(request, db)


@app.post("/api/jobs/{job_id}/abort", response_class=HTMLResponse)
async def abort_job(request: Request, job_id: str, db: Session = Depends(get_db)):
    """Abort an entire on-demand job/batch that is partially or fully Queued/Downloading.

    Signals the download_manager to terminate all active subprocesses for the
    job and marks all Queued/Downloading tracks in the batch as Aborted.
    """
    active_tracks = (
        db.query(models.Download)
        .filter(
            models.Download.job_id == job_id,
            models.Download.synced_playlist_id.is_(None),
            models.Download.status.in_(["Queued", "Downloading"]),
        )
        .all()
    )

    if not active_tracks:
        return await _render_tracks(request, db)

    # Signal the process registry to abort all subprocesses for this job
    download_manager.abort_job(job_id)

    # Eagerly mark all active tracks as Aborted
    for t in active_tracks:
        t.status = "Aborted"
    db.commit()
    logger.info(
        f"Job {job_id} aborted by user — {len(active_tracks)} tracks marked Aborted."
    )

    return await _render_tracks(request, db)


@app.post("/api/jobs/{job_id}/delete", response_class=HTMLResponse)
async def soft_delete_job(request: Request, job_id: str, db: Session = Depends(get_db)):
    """Soft-deletes all child tracks of an on-demand playlist job."""
    tracks = (
        db.query(models.Download)
        .filter(
            models.Download.job_id == job_id,
            models.Download.synced_playlist_id.is_(None),
        )
        .all()
    )

    if not tracks:
        raise HTTPException(status_code=404, detail="Job not found")

    # Guard: do not allow if any track is active
    if any(t.status in ("Queued", "Downloading") for t in tracks):
        return await _render_tracks(request, db)

    folders_to_clean = set()
    for track in tracks:
        if track.file_path:
            parent_dir = os.path.dirname(track.file_path)
            if parent_dir != str(DOWNLOAD_DIR) and parent_dir.startswith(
                str(DOWNLOAD_DIR)
            ):
                folders_to_clean.add(parent_dir)
            if os.path.exists(track.file_path):
                try:
                    os.remove(track.file_path)
                except OSError as e:
                    logger.error(f"Could not delete file {track.file_path}: {e}")
        track.status = "Deleted"
        track.file_path = None

    for folder in folders_to_clean:
        if os.path.isdir(folder):
            try:
                shutil.rmtree(folder)
                logger.info(f"Purged playlist folder from disk: {folder}")
            except OSError as e:
                logger.error(f"Error deleting playlist folder {folder}: {e}")

    db.commit()
    logger.info(f"Job {job_id} soft-deleted by user — {len(tracks)} tracks affected.")

    return await _render_tracks(request, db)


@app.delete("/api/jobs/{job_id}", response_class=HTMLResponse)
async def delete_job(request: Request, job_id: str, db: Session = Depends(get_db)):
    """Permanently dismisses all child tracks of an on-demand playlist job."""
    tracks = (
        db.query(models.Download)
        .filter(
            models.Download.job_id == job_id,
            models.Download.synced_playlist_id.is_(None),
        )
        .all()
    )

    folders_to_clean = set()
    count = 0
    for track in tracks:
        if track.file_path:
            parent_dir = os.path.dirname(track.file_path)
            if parent_dir != str(DOWNLOAD_DIR) and parent_dir.startswith(
                str(DOWNLOAD_DIR)
            ):
                folders_to_clean.add(parent_dir)
            if os.path.exists(track.file_path):
                try:
                    os.remove(track.file_path)
                except OSError as e:
                    logger.error(f"Could not delete file {track.file_path}: {e}")
        db.delete(track)
        count += 1

    for folder in folders_to_clean:
        if os.path.isdir(folder):
            try:
                shutil.rmtree(folder)
                logger.info(f"Purged playlist folder from disk: {folder}")
            except OSError as e:
                logger.error(f"Error deleting playlist folder {folder}: {e}")

    if count > 0:
        db.commit()
        logger.info(f"Job {job_id} dismissed — {count} tracks purged.")

    # Return full re-render so the entire playlist container is removed
    return await _render_tracks(request, db)


async def _render_tracks(request: Request, db: Session) -> HTMLResponse:
    """Re-renders the full track list partial (shared by abort & other endpoints)."""
    return await api_tracks(request, db)


# SYNCED PLAYLISTS API ENDPOINTS


@app.get("/api/synced-playlists", response_class=HTMLResponse)
async def get_synced_playlists(request: Request, db: Session = Depends(get_db)):
    """Returns HTMX partial grid of Synced Playlists."""
    synced_playlists = (
        db.query(models.SyncedPlaylist)
        .order_by(models.SyncedPlaylist.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "partials/synced_playlists_grid.html",
        {"request": request, "synced_playlists": synced_playlists},
    )


@app.post("/api/synced-playlists", response_class=HTMLResponse)
async def add_synced_playlist(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    sync_mode: str = Form("append_only"),
    title: str = Form(""),
    db: Session = Depends(get_db),
):
    """Creates a new Synced Playlist and triggers initial background sync."""
    title_fetched, is_single = fetch_playlist_title(url, db)
    if not title or not title.strip():
        title = title_fetched

    single_note = (
        "Single track link detected (not a full playlist)" if is_single else None
    )

    # Check if URL already tracked
    existing = (
        db.query(models.SyncedPlaylist).filter(models.SyncedPlaylist.url == url).first()
    )
    if existing:
        existing.title = title
        existing.sync_mode = sync_mode
        existing.is_active = True
        existing.status = "Syncing"
        if single_note:
            existing.last_error = single_note
        db.commit()
        playlist_id = existing.id
    else:
        new_sp = models.SyncedPlaylist(
            url=url,
            title=title,
            sync_mode=sync_mode,
            is_active=True,
            status="Syncing",
            last_error=single_note,
        )
        db.add(new_sp)
        db.commit()
        playlist_id = new_sp.id

    background_tasks.add_task(process_playlist_sync, int(str(playlist_id)))

    synced_playlists = (
        db.query(models.SyncedPlaylist)
        .order_by(models.SyncedPlaylist.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "partials/synced_playlists_grid.html",
        {"request": request, "synced_playlists": synced_playlists},
    )


@app.post("/api/synced-playlists/{playlist_id}/sync", response_class=HTMLResponse)
async def manual_sync_playlist(
    playlist_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Triggers on-demand manual sync for a specific Synced Playlist."""
    sp = (
        db.query(models.SyncedPlaylist)
        .filter(models.SyncedPlaylist.id == playlist_id)
        .first()
    )
    if sp:
        sp.status = "Syncing"
        db.commit()
        background_tasks.add_task(process_playlist_sync, playlist_id)

    synced_playlists = (
        db.query(models.SyncedPlaylist)
        .order_by(models.SyncedPlaylist.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "partials/synced_playlists_grid.html",
        {"request": request, "synced_playlists": synced_playlists},
    )


@app.post("/api/synced-playlists/{playlist_id}/toggle", response_class=HTMLResponse)
async def toggle_sync_playlist(
    playlist_id: int, request: Request, db: Session = Depends(get_db)
):
    """Toggles active/paused state of a Synced Playlist."""
    sp = (
        db.query(models.SyncedPlaylist)
        .filter(models.SyncedPlaylist.id == playlist_id)
        .first()
    )
    if sp:
        sp.is_active = not sp.is_active
        sp.status = "Active" if sp.is_active else "Paused"
        db.commit()

    synced_playlists = (
        db.query(models.SyncedPlaylist)
        .order_by(models.SyncedPlaylist.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "partials/synced_playlists_grid.html",
        {"request": request, "synced_playlists": synced_playlists},
    )


@app.put("/api/synced-playlists/{playlist_id}", response_class=HTMLResponse)
async def update_synced_playlist(
    playlist_id: int,
    request: Request,
    title: str = Form(...),
    sync_mode: str = Form("append_only"),
    db: Session = Depends(get_db),
):
    """Updates title and sync_mode of a Synced Playlist."""
    sp = (
        db.query(models.SyncedPlaylist)
        .filter(models.SyncedPlaylist.id == playlist_id)
        .first()
    )
    if sp:
        sp.title = title
        sp.sync_mode = sync_mode
        db.commit()

    synced_playlists = (
        db.query(models.SyncedPlaylist)
        .order_by(models.SyncedPlaylist.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "partials/synced_playlists_grid.html",
        {"request": request, "synced_playlists": synced_playlists},
    )


@app.delete("/api/synced-playlists/{playlist_id}", response_class=HTMLResponse)
async def delete_synced_playlist(
    playlist_id: int,
    request: Request,
    delete_files: str = Form("false"),
    db: Session = Depends(get_db),
):
    """Deletes a Synced Playlist entity, with optional file purging."""
    sp = (
        db.query(models.SyncedPlaylist)
        .filter(models.SyncedPlaylist.id == playlist_id)
        .first()
    )
    should_delete_files = delete_files.lower() in (
        "true",
        "1",
        "yes",
    ) or request.query_params.get("delete_files", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    if sp:
        if should_delete_files:
            # Collect potential playlist folders to delete from disk
            folders_to_clean = set()
            if sp.title:
                sanitized_title = yt_dlp_sanitize(sp.title)
                playlist_dir = os.path.join(DOWNLOAD_DIR, sanitized_title)
                if os.path.isdir(playlist_dir):
                    folders_to_clean.add(playlist_dir)

            downloads = (
                db.query(models.Download)
                .filter(models.Download.synced_playlist_id == sp.id)
                .all()
            )
            for dl in downloads:
                if dl.file_path:
                    parent_dir = os.path.dirname(dl.file_path)
                    if (
                        parent_dir
                        and parent_dir != str(DOWNLOAD_DIR)
                        and parent_dir.startswith(str(DOWNLOAD_DIR))
                    ):
                        folders_to_clean.add(parent_dir)
                    if os.path.exists(dl.file_path):
                        try:
                            os.remove(dl.file_path)
                        except OSError as e:
                            logger.error(
                                f"Error deleting file {dl.file_path} on playlist purge: {e}"
                            )
                db.delete(dl)

            # Purge playlist folders from disk
            for folder in folders_to_clean:
                if os.path.isdir(folder):
                    try:
                        shutil.rmtree(folder)
                        logger.info(f"Purged playlist folder from disk: {folder}")
                    except OSError as e:
                        logger.error(f"Error deleting playlist folder {folder}: {e}")

        db.delete(sp)
        db.commit()

    synced_playlists = (
        db.query(models.SyncedPlaylist)
        .order_by(models.SyncedPlaylist.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "partials/synced_playlists_grid.html",
        {"request": request, "synced_playlists": synced_playlists},
    )


@app.get("/api/logs")
async def stream_logs():
    return StreamingResponse(log_generator(), media_type="text/event-stream")
