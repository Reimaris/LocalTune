import re
import os
from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text
from pathlib import Path
from redis import Redis
from rq import Queue
from app.core.logging_config import setup_logging
from app.core.config import settings
from app.db import models
from app.db.database import engine, get_db

# Initialize logging before doing anything else
logger = setup_logging()
logger.info("Starting LocalTune application")

# Create database tables
models.Base.metadata.create_all(bind=engine)

try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE downloads ADD COLUMN job_title VARCHAR"))
        conn.commit()
except Exception:
    pass

app = FastAPI(title="LocalTune")

# Setup static files and templates
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Setup Redis and RQ
redis_conn = Redis.from_url(settings.redis_url)
task_queue = Queue("downloads", connection=redis_conn)

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    Verification Route:
    1. Writes a test log entry.
    2. Performs a basic read query.
    3. Returns JSON status.
    """
    logger.info("Health check endpoint accessed.")
    try:
        # Perform a basic read query against the downloads table (count rows)
        count = db.query(models.Download).count()
        return {
            "status": "ok",
            "logger": "configured",
            "database": "connected",
            "downloads_count": count
        }
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return {
            "status": "error",
            "logger": "configured",
            "database": f"failed: {e}"
        }

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """
    Renders the main dashboard.
    """
    # Later we will fetch data from DB and queue to render the status
    context = {
        "request": request,
        "title": "LocalTune Dashboard"
    }
    return templates.TemplateResponse("dashboard.html", context)

@app.post("/download", response_class=HTMLResponse)
async def download_url(
    request: Request,
    url: str = Form(...),
    media_type: str = Form("audio"),
    file_format: str = Form("opus"),
    db: Session = Depends(get_db)
):
    """
    Receives URL from the frontend, validates it, and queues for download.
    Returns HTMX snippet.
    """
    logger.info(f"Received download request for URL: {url}")
    
    # Basic Validation
    is_youtube = re.search(r'(youtube\.com|youtu\.be)', url)
    is_spotify = re.search(r'(spotify\.com)', url)
    
    if not (is_youtube or is_spotify):
        return """
        <div class="bg-red-900 border border-red-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
          <strong class="font-bold">Error!</strong>
          <span class="block sm:inline">Invalid URL. Must be a valid Spotify or YouTube link.</span>
        </div>
        """
        
    if is_spotify and media_type == "video":
        return """
        <div class="bg-red-900 border border-red-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
          <strong class="font-bold">Error!</strong>
          <span class="block sm:inline">Spotify does not support video downloads. Please select Music.</span>
        </div>
        """
        
    job = task_queue.enqueue("app.worker.process_download", url=url, media_type=media_type, file_format=file_format)
    
    try:
        new_download = models.Download(
            track_id=job.id,
            title=url,
            artist="Pending Metadata...",
            status="Queued",
            job_id=job.id
        )
        db.add(new_download)
        db.commit()
    except Exception as e:
        logger.error(f"Failed to insert placeholder download: {e}")
        db.rollback()
    
    return f"""
    <div class="bg-emerald-900 border border-emerald-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
      <strong class="font-bold">Success!</strong>
      <span class="block sm:inline">Job queued for {url} (ID: {job.id})</span>
    </div>
    """

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    db_settings = db.query(models.Settings).first()
    if not db_settings:
        db_settings = models.Settings()
        db.add(db_settings)
        db.commit()
    
    return templates.TemplateResponse("settings.html", {"request": request, "title": "Settings", "settings": db_settings})

@app.post("/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    telegram_bot_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    spotify_client_id: str = Form(""),
    spotify_client_secret: str = Form(""),
    db: Session = Depends(get_db)
):
    db_settings = db.query(models.Settings).first()
    if not db_settings:
        db_settings = models.Settings()
        db.add(db_settings)

    db_settings.telegram_bot_token = telegram_bot_token
    db_settings.telegram_chat_id = telegram_chat_id
    db_settings.spotify_client_id = spotify_client_id
    db_settings.spotify_client_secret = spotify_client_secret
    db.commit()
    
    return """
    <div class="bg-emerald-900 border border-emerald-700 text-white px-4 py-3 rounded relative mb-4" role="alert">
      <strong class="font-bold">Success!</strong>
      <span class="block sm:inline">Settings saved successfully.</span>
    </div>
    """

@app.get("/api/tracks", response_class=HTMLResponse)
async def api_tracks(request: Request, db: Session = Depends(get_db)):
    tracks = db.query(models.Download).order_by(models.Download.downloaded_at.desc()).limit(150).all()
    queued = db.query(models.Download).filter(models.Download.status == "Queued").count()
    downloading = db.query(models.Download).filter(models.Download.status == "Downloading").count()
    done = db.query(models.Download).filter(models.Download.status == "Completed").count()
    errors = db.query(models.Download).filter(models.Download.status == "Failed").count()
    
    grouped_jobs = {}
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
                if "Downloading" in statuses or "Queued" in statuses or "Fetching Metadata" in statuses:
                    status = "Downloading"
                elif "Failed" in statuses and "Completed" not in statuses:
                    status = "Failed"
                else:
                    status = "Completed"
                    
                display_items.append({
                    "type": "playlist",
                    "title": t.job_title,
                    "job_id": t.job_id,
                    "status": status,
                    "tracks": job_tracks
                })
    
    return templates.TemplateResponse("partials/track_list.html", {
        "request": request,
        "display_items": display_items,
        "queued": queued + downloading,
        "done": done,
        "errors": errors
    })

@app.delete("/api/tracks/{track_id}", response_class=HTMLResponse)
async def delete_track(request: Request, track_id: int, db: Session = Depends(get_db)):
    track = db.query(models.Download).filter(models.Download.id == track_id).first()
    if track:
        if track.file_path and os.path.exists(track.file_path):
            try:
                os.remove(track.file_path)
            except Exception as e:
                logger.error(f"Could not delete file {track.file_path}: {e}")
        db.delete(track)
        db.commit()
    return ""

