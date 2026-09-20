import asyncio
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import String, and_, case, cast, func, or_, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.downloader import (
    DOWNLOAD_DIR,
    fetch_playlist_title,
    get_canonical_source_url,
    inspect_url_tracks,
    write_m3u8,
    yt_dlp_sanitize,
)
from app.core.library_metrics import get_library_metrics
from app.core.logging_config import log_generator, setup_logging
from app.core.process_registry import cleanup_all_partial_files, download_manager
from app.core.scheduler import periodic_sync_loop
from app.db import models
from app.db.database import SessionLocal, engine, get_db
from app.worker import (
    process_download,
    process_playlist_sync,
    retry_job_batch,
    retry_single_track,
)

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
        conn.execute(text("ALTER TABLE downloads ADD COLUMN source_url VARCHAR"))
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
    """Upgrade yt-dlp in-place using pip without cache.

    If pip is missing in standalone environments (e.g. embedded Python runtime),
    automatically bootstraps pip on-demand via get-pip.py and retries.
    """
    pip_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--no-cache-dir",
        "yt-dlp",
    ]

    try:
        proc = subprocess.run(
            pip_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        err_output = proc.stderr.strip() or proc.stdout.strip()

        # Check if pip is missing from Python environment
        if proc.returncode != 0 and "No module named pip" in err_output:
            logger.warning("pip is missing from current runtime. Bootstrapping via get-pip.py...")
            with tempfile.TemporaryDirectory() as temp_dir:
                get_pip_path = os.path.join(temp_dir, "get-pip.py")
                get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
                req = urllib.request.Request(get_pip_url, headers={"User-Agent": "LocalTune-Upgrader"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(get_pip_path, "wb") as out_f:
                    out_f.write(resp.read())

                bootstrap_proc = subprocess.run(
                    [sys.executable, get_pip_path, "--no-warn-script-location"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if bootstrap_proc.returncode != 0:
                    b_err = bootstrap_proc.stderr.strip() or bootstrap_proc.stdout.strip()
                    logger.error(f"Failed to bootstrap pip: {b_err}")
                    return False, f"Failed to bootstrap pip: {b_err}"

            # Retry pip install after bootstrap
            logger.info("pip successfully bootstrapped. Retrying yt-dlp upgrade...")
            proc = subprocess.run(
                pip_cmd,
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


def get_db_session() -> Session:
    """Get a database session, respecting app.dependency_overrides if present."""
    if get_db in app.dependency_overrides:
        override = app.dependency_overrides[get_db]
        res = override()
        if hasattr(res, "__next__"):
            return next(res)
        return res
    return SessionLocal()


def pause_active_downloads(db: Session | None = None) -> int:
    """Transition all in-flight 'Downloading', 'Queued', and 'Fetching Metadata' downloads to 'Paused'
    and clean up partial files on disk.
    """
    owns_session = False
    if db is None:
        db = get_db_session()
        owns_session = True
    try:
        active_tracks = (
            db.query(models.Download)
            .filter(models.Download.status.in_(["Downloading", "Queued", "Fetching Metadata"]))
            .all()
        )
        count = len(active_tracks)
        if count > 0:
            for t in active_tracks:
                t.status = "Paused"
            db.commit()
            logger.info(f"Transitioned {count} active downloads to 'Paused' state on shutdown.")
        cleanup_all_partial_files()
        return count
    finally:
        if owns_session:
            db.close()


def resume_paused_downloads(db: Session | None = None) -> int:
    """Automatically resume downloads that were transitioned to 'Paused' upon previous shutdown."""
    owns_session = False
    if db is None:
        db = get_db_session()
        owns_session = True
    try:
        paused_tracks = (
            db.query(models.Download)
            .filter(models.Download.status == "Paused")
            .order_by(models.Download.id.asc())
            .all()
        )
        count = len(paused_tracks)
        if count == 0:
            return 0

        for t in paused_tracks:
            t.status = "Queued"
            t.file_path = None
        db.commit()
        logger.info(f"Re-enqueued {count} paused tracks to 'Queued' status for execution.")

        dispatched_jobs: set[str] = set()
        dispatched_single_tracks: list[int] = []

        for t in paused_tracks:
            jid = t.job_id
            if jid:
                if jid not in dispatched_jobs:
                    dispatched_jobs.add(jid)
            else:
                if t.id is not None:
                    dispatched_single_tracks.append(int(t.id))

        threads: list[threading.Thread] = []
        for jid in dispatched_jobs:
            thread = threading.Thread(target=retry_job_batch, args=(jid,), daemon=True)
            thread.start()
            threads.append(thread)

        for track_id in dispatched_single_tracks:
            thread = threading.Thread(target=retry_single_track, args=(track_id,), daemon=True)
            thread.start()
            threads.append(thread)

        for th in threads:
            th.join(timeout=0.05)

        return count
    finally:
        if owns_session:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern FastAPI lifespan manager.

    Resumes paused downloads from previous sessions and starts the 6-hour periodic playlist sync
    background task on startup. On shutdown, transitions in-flight downloads to 'Paused', cleans up
    partial files, and cleanly cancels the sync task.
    """
    try:
        resume_paused_downloads()
    except Exception as e:
        logger.error(f"Error resuming paused downloads on startup: {e}", exc_info=True)

    sync_task = asyncio.create_task(periodic_sync_loop())
    yield
    try:
        pause_active_downloads()
    except Exception as e:
        logger.error(f"Error pausing active downloads on shutdown: {e}", exc_info=True)

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


def to_utc_iso(dt: datetime | None) -> str:
    """Format datetime as UTC ISO-8601 string for client-side device time conversion."""
    if not dt:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


templates.env.filters["utc_iso"] = to_utc_iso
templates.env.filters["canonical_url"] = get_canonical_source_url
templates.env.globals["get_canonical_source_url"] = get_canonical_source_url


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
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Renders the main dashboard."""
    metrics = get_library_metrics(db)
    context = {
        "request": request,
        "title": "LocalTune Dashboard",
        "library_tracks": metrics["total_tracks"],
        "library_storage": metrics["total_storage_formatted"],
    }
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

    new_count, dupes = inspect_url_tracks(url, db, media_type=media_type)
    if dupes > 0 and new_count == 0:
        return """
        <div class="bg-amber-900 border border-amber-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
          <strong class="font-bold">Info:</strong>
          <span class="block sm:inline">All tracks already downloaded and verified on disk — skipped redundant download.</span>
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
            source_url=url,
        )
        db.add(new_download)
        db.commit()
    except OSError as e:
        logger.error(f"Failed to insert placeholder download: {e}")
        db.rollback()

    if dupes > 0 and new_count > 0:
        msg = f"{new_count} new tracks queued ({dupes} already downloaded & skipped)."
    else:
        msg = f"Job queued for {url} (ID: {job_id})"

    return f"""
    <div class="bg-emerald-900 border border-emerald-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
      <strong class="font-bold">Success!</strong>
      <span class="block sm:inline">{msg}</span>
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

    from app.core.launcher_config import load_launcher_config

    launcher_cfg = load_launcher_config()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "title": "Settings",
            "settings": db_settings,
            "launcher_config": launcher_cfg,
            "ytdlp_version": ytdlp_version,
        },
    )


@app.get("/api/settings/launcher-config")
async def get_launcher_config_endpoint():
    from app.core.launcher_config import load_launcher_config

    cfg = load_launcher_config()
    return JSONResponse(
        {
            "include_prereleases": bool(cfg.get("include_prereleases", False)),
            "download_dir": cfg.get("download_dir"),
        }
    )


@app.post("/api/settings/launcher-config", response_class=HTMLResponse)
async def update_launcher_config_endpoint(
    include_prereleases: str | None = Form(None),
):
    from app.core.launcher_config import load_launcher_config, save_launcher_config

    cfg = load_launcher_config()
    cfg["include_prereleases"] = bool(include_prereleases)
    save_launcher_config(cfg)

    return """
    <div class="bg-emerald-900 border border-emerald-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
      <strong class="font-bold">Success!</strong>
      <span class="block sm:inline">Launcher settings updated successfully.</span>
    </div>
    """


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


def _render_migration_status_html() -> str:
    from app.core.library_migration import migration_state

    summary = migration_state.last_summary or (
        {
            "total": migration_state.total,
            "moved": migration_state.moved,
            "collisions": migration_state.collisions,
            "errors": migration_state.errors,
        }
        if (migration_state.total > 0 and migration_state.processed >= migration_state.total)
        else None
    )

    if migration_state.is_running:
        pct = (
            int((migration_state.processed / migration_state.total) * 100)
            if migration_state.total > 0
            else 0
        )
        return f"""
        <div id="migration-progress" class="space-y-3" hx-get="/api/library/migrate/status" hx-trigger="every 1s" hx-swap="outerHTML">
            <div class="flex items-center justify-between text-xs text-white">
                <span class="flex items-center gap-2">
                    <svg class="animate-spin h-3.5 w-3.5 text-[#d40060]" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    Migrating library files... ({migration_state.processed}/{migration_state.total})
                </span>
                <span class="font-mono text-[#a1a1aa]">{pct}%</span>
            </div>
            <div class="w-full bg-[#111115] rounded-full h-2 overflow-hidden border border-[#3f3f46]">
                <div class="bg-[#d40060] h-2 rounded-full transition-all duration-300" style="width: {pct}%"></div>
            </div>
            <p class="text-[11px] text-[#71717a] truncate font-mono">Current: {migration_state.current_file}</p>
        </div>
        """
    elif summary:
        return f"""
        <div id="migration-progress" class="space-y-3">
            <div class="bg-emerald-900/80 border border-emerald-500 text-white px-4 py-3 rounded-lg text-sm flex items-start gap-2.5">
                <svg class="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
                <div>
                    <strong class="font-bold">Migration Complete!</strong>
                    <div class="text-xs text-emerald-200 mt-0.5">
                        Processed {summary['total']} file(s): moved {summary['moved']}, {summary['collisions']} collision(s) suffixed, {summary['errors']} error(s).
                    </div>
                </div>
            </div>
            <button type="button" 
                hx-post="/api/library/migrate" 
                hx-target="#migration-progress"
                class="px-4 py-2 bg-[#27272a] hover:bg-[#3f3f46] text-white text-sm font-medium rounded-lg transition-colors border border-[#3f3f46] flex items-center gap-2 whitespace-nowrap shrink-0">
                <svg class="w-4 h-4 text-[#d40060] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                <span>Migrate Again</span>
            </button>
        </div>
        """
    else:
        return """
        <div id="migration-progress">
            <button type="button" 
                hx-post="/api/library/migrate" 
                hx-target="#migration-progress"
                class="px-4 py-2 bg-[#27272a] hover:bg-[#3f3f46] text-white text-sm font-medium rounded-lg transition-colors border border-[#3f3f46] flex items-center gap-2 whitespace-nowrap shrink-0">
                <svg class="w-4 h-4 text-[#d40060] shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
                <span>Migrate Existing Files</span>
            </button>
            <p class="text-xs text-[#71717a] mt-1.5">
                Relocates all completed audio files in your library to match the current naming template above without re-downloading.
            </p>
        </div>
        """


@app.post("/api/library/migrate", response_class=HTMLResponse)
async def api_library_migrate():
    from app.core.library_migration import start_background_migration
    start_background_migration()
    return _render_migration_status_html()


@app.get("/api/library/migrate/status", response_class=HTMLResponse)
async def api_library_migrate_status():
    return _render_migration_status_html()


@app.get("/api/tracks", response_class=HTMLResponse)
async def api_tracks(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    sort_by: str | None = None,
    order: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    db: Session = Depends(get_db),
):
    settings = db.query(models.Settings).first()
    enable_browser_downloads = settings.enable_browser_downloads if settings else False

    # Extract query params if not explicitly passed
    form_data: dict[str, str] = {}
    if request.method == "POST":
        try:
            raw_form = await request.form()
            form_data = {k: str(v) for k, v in raw_form.items()}
        except Exception:
            pass

    q_str = (q if q is not None else (request.query_params.get("q") or form_data.get("q", ""))).strip()
    status_str = (status if status is not None else (request.query_params.get("status") or form_data.get("status", "all"))).strip().lower()
    sort_by_str = (sort_by if sort_by is not None else (request.query_params.get("sort_by") or form_data.get("sort_by", "downloaded_at"))).strip().lower()
    order_str = (order if order is not None else (request.query_params.get("order") or form_data.get("order", "desc"))).strip().lower()

    p_raw: str | int | None = page if page is not None else (request.query_params.get("page") or form_data.get("page"))
    try:
        p_val = int(p_raw) if p_raw is not None else 1
    except (ValueError, TypeError):
        p_val = 1

    ps_raw: str | int | None = page_size if page_size is not None else (request.query_params.get("page_size") or form_data.get("page_size"))
    try:
        ps_val = int(ps_raw) if ps_raw is not None else 25
    except (ValueError, TypeError):
        ps_val = 25

    if ps_val not in (25, 50, 100):
        ps_val = 25

    # Global stat counters across all on-demand tracks (for #stat-cards)
    stat_base = db.query(models.Download).filter(models.Download.synced_playlist_id.is_(None))
    queued = stat_base.filter(models.Download.status == "Queued").count()
    downloading = stat_base.filter(models.Download.status.in_(["Downloading", "Fetching Metadata"])).count()
    done = stat_base.filter(models.Download.status == "Completed").count()
    errors = stat_base.filter(models.Download.status == "Failed").count()

    # Build filter conditions for child tracks
    filter_conditions: list[Any] = [models.Download.synced_playlist_id.is_(None)]
    if q_str:
        pattern = f"%{q_str}%"
        filter_conditions.append(
            or_(
                models.Download.title.ilike(pattern),
                models.Download.artist.ilike(pattern),
                models.Download.job_title.ilike(pattern),
            )
        )

    if status_str and status_str != "all":
        if status_str == "downloading":
            filter_conditions.append(
                models.Download.status.in_(["Downloading", "Queued", "Fetching Metadata"])
            )
        else:
            filter_conditions.append(models.Download.status.ilike(status_str))

    is_job = and_(
        models.Download.job_id.isnot(None),
        models.Download.job_id != "",
        models.Download.job_title.isnot(None),
    )
    unit_expr = case(
        (is_job, func.concat("job_", models.Download.job_id)),
        else_=func.concat("track_", cast(models.Download.id, String)),
    )
    job_id_expr = case((is_job, models.Download.job_id), else_=None)
    track_id_expr = case((is_job, None), else_=models.Download.id)

    sort_date = func.max(models.Download.downloaded_at)
    sort_id = func.max(models.Download.id)
    sort_title = func.max(case((is_job, models.Download.job_title), else_=models.Download.title))
    sort_artist = func.max(case((is_job, ""), else_=models.Download.artist))
    sort_status = func.max(models.Download.status)

    unit_query = (
        db.query(
            unit_expr.label("unit_key"),
            job_id_expr.label("job_id"),
            track_id_expr.label("track_id"),
            sort_date.label("sort_date"),
            sort_id.label("sort_id"),
            sort_title.label("sort_title"),
            sort_artist.label("sort_artist"),
            sort_status.label("sort_status"),
        )
        .filter(*filter_conditions)
        .group_by(unit_expr)
    )

    # 3. Total count & pagination bounds (based on Top-Level Display Units)
    total_count = unit_query.count()
    total_pages = max(1, math.ceil(total_count / ps_val))
    current_page = max(1, min(p_val, total_pages))
    offset = (current_page - 1) * ps_val
    start_item = offset + 1 if total_count > 0 else 0
    end_item = min(offset + ps_val, total_count)

    # 4. Sorting
    allowed_sort: dict[str, Any] = {
        "downloaded_at": sort_date,
        "id": sort_id,
        "title": sort_title,
        "artist": sort_artist,
        "status": sort_status,
    }
    sort_col: Any = allowed_sort.get(sort_by_str, sort_date)
    if order_str == "asc":
        unit_query = unit_query.order_by(sort_col.asc(), sort_id.asc())
    else:
        unit_query = unit_query.order_by(sort_col.desc(), sort_id.desc())

    # 5. Fetch page slice of units
    units = unit_query.offset(offset).limit(ps_val).all()

    page_track_ids: list[int] = [int(u.track_id) for u in units if u.track_id is not None]
    page_job_ids: list[str] = [str(u.job_id) for u in units if u.job_id is not None]

    standalone_tracks: dict[int, models.Download] = {}
    if page_track_ids:
        for t in db.query(models.Download).filter(models.Download.id.in_(page_track_ids)).all():
            if t.id is not None:
                standalone_tracks[t.id] = t

    job_tracks_map: dict[str, list[models.Download]] = {jid: [] for jid in page_job_ids}
    if page_job_ids:
        child_query = db.query(models.Download).filter(
            models.Download.synced_playlist_id.is_(None),
            models.Download.job_id.in_(page_job_ids),
        )
        if q_str:
            pattern = f"%{q_str}%"
            child_query = child_query.filter(
                or_(
                    models.Download.title.ilike(pattern),
                    models.Download.artist.ilike(pattern),
                    models.Download.job_title.ilike(pattern),
                )
            )
        if status_str and status_str != "all":
            if status_str == "downloading":
                child_query = child_query.filter(
                    models.Download.status.in_(["Downloading", "Queued", "Fetching Metadata"])
                )
            else:
                child_query = child_query.filter(models.Download.status.ilike(status_str))

        for t in child_query.order_by(models.Download.id.asc()).all():
            if t.job_id in job_tracks_map:
                job_tracks_map[t.job_id].append(t)

    display_items: list[dict] = []
    for u in units:
        if u.job_id is not None:
            job_tracks = job_tracks_map.get(str(u.job_id), [])
            if not job_tracks:
                continue
            playlist_title = job_tracks[0].job_title or u.sort_title or "Playlist"

            statuses = [child.status for child in job_tracks]
            has_retryable = any(s in ("Failed", "Aborted", "Deleted") for s in statuses)
            if (
                "Downloading" in statuses
                or "Queued" in statuses
                or "Fetching Metadata" in statuses
            ):
                job_status = "Downloading"
            elif "Paused" in statuses:
                job_status = "Paused"
            elif all(s == "Deleted" for s in statuses):
                job_status = "Deleted"
            elif all(s == "Aborted" for s in statuses):
                job_status = "Aborted"
            elif all(s == "Failed" for s in statuses):
                job_status = "Failed"
            elif "Completed" in statuses:
                job_status = "Completed"
            else:
                job_status = "Completed"

            display_items.append(
                {
                    "type": "playlist",
                    "title": playlist_title,
                    "job_id": u.job_id,
                    "status": job_status,
                    "tracks": job_tracks,
                    "has_retryable": has_retryable,
                }
            )
        else:
            tid = int(u.track_id) if u.track_id is not None else None
            if tid is not None and tid in standalone_tracks:
                display_items.append({"type": "track", "item": standalone_tracks[tid]})

    # Library storage and track metrics for top header metric box
    lib_metrics = get_library_metrics(db)

    return templates.TemplateResponse(
        "partials/track_list.html",
        {
            "request": request,
            "display_items": display_items,
            "queued": queued + downloading,
            "done": done,
            "errors": errors,
            "enable_browser_downloads": enable_browser_downloads,
            "library_tracks": lib_metrics["total_tracks"],
            "library_storage": lib_metrics["total_storage_formatted"],
            "q": q_str,
            "status": status_str,
            "sort_by": sort_by_str,
            "order": order_str,
            "page": current_page,
            "page_size": ps_val,
            "total_pages": total_pages,
            "total_items": total_count,
            "start_item": start_item,
            "end_item": end_item,
        },
    )


@app.delete("/api/tracks/{track_id}", response_class=HTMLResponse)
async def delete_track(request: Request, track_id: int, db: Session = Depends(get_db)):
    track = db.query(models.Download).filter(models.Download.id == track_id).first()
    if track:
        parent_dir = None
        if track.file_path:
            parent_dir = os.path.dirname(track.file_path)
            if os.path.exists(track.file_path):
                try:
                    os.remove(track.file_path)
                except OSError as e:
                    logger.error(f"Could not delete file {track.file_path}: {e}")
        db.delete(track)
        db.commit()

        if parent_dir and parent_dir != str(DOWNLOAD_DIR) and parent_dir.startswith(str(DOWNLOAD_DIR)):
            write_m3u8(parent_dir, db=db)
            try:
                os.rmdir(parent_dir)
                logger.info(f"Purged empty playlist folder from disk: {parent_dir}")
            except OSError:
                pass
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

    parent_dir = None
    if track.file_path:
        parent_dir = os.path.dirname(track.file_path)
        if os.path.exists(track.file_path):
            try:
                os.remove(track.file_path)
            except OSError as e:
                logger.error(f"Could not delete file {track.file_path}: {e}")

    track.status = "Deleted"
    track.file_path = None
    db.commit()
    logger.info(f"Track {track_id} soft-deleted by user.")

    if parent_dir and parent_dir != str(DOWNLOAD_DIR) and parent_dir.startswith(str(DOWNLOAD_DIR)):
        write_m3u8(parent_dir, db=db)
        try:
            os.rmdir(parent_dir)
            logger.info(f"Purged empty playlist folder from disk: {parent_dir}")
        except OSError:
            pass

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
    return await api_tracks(request, db=db)


@app.post("/api/tracks/{track_id}/retry", response_class=HTMLResponse)
async def retry_track(
    request: Request,
    track_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Retries downloading an individual on-demand track in Failed, Aborted, or Deleted state."""
    track = db.query(models.Download).filter(models.Download.id == track_id).first()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    if track.synced_playlist_id is not None:
        raise HTTPException(
            status_code=400, detail="Synced playlist tracks cannot be retried here"
        )

    if track.status not in ("Failed", "Aborted", "Deleted"):
        raise HTTPException(
            status_code=400,
            detail="Only Failed, Aborted, or Deleted tracks can be retried",
        )

    job_id = track.job_id or track.track_id or ""
    if track.track_id:
        download_manager.clear_track_abort(job_id, track.track_id)

    track.status = "Queued"
    track.file_path = None
    db.commit()

    from app.worker import retry_single_track

    background_tasks.add_task(retry_single_track, track_id)
    return await _render_tracks(request, db)


@app.post("/api/jobs/{job_id}/retry", response_class=HTMLResponse)
async def retry_job(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Retries all child tracks of an on-demand playlist job that are in Failed, Aborted, or Deleted state."""
    retryable_tracks = (
        db.query(models.Download)
        .filter(
            models.Download.job_id == job_id,
            models.Download.synced_playlist_id.is_(None),
            models.Download.status.in_(["Failed", "Aborted", "Deleted"]),
        )
        .all()
    )

    if not retryable_tracks:
        return await _render_tracks(request, db)

    download_manager.clear_job_abort(job_id)

    for track in retryable_tracks:
        track.status = "Queued"
        track.file_path = None
    db.commit()

    from app.worker import retry_job_batch

    background_tasks.add_task(retry_job_batch, job_id)
    return await _render_tracks(request, db)


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
