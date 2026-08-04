from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from app.core.logging_config import setup_logging
from app.db import models
from app.db.database import engine, get_db

# Initialize logging before doing anything else
logger = setup_logging()
logger.info("Starting LocalTune application")

# Create database tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="LocalTune")

# Setup templates
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

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
