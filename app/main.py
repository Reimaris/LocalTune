from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.core.logging_config import setup_logging
from app.core.config import settings

# Initialize logging before doing anything else
logger = setup_logging()
logger.info("Starting LocalTune application")

app = FastAPI(title="LocalTune")

# Setup templates
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

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
