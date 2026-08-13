# Architecture Decisions — LocalTune

This document tracks all major architecture decisions (ADRs) and technology choices made throughout the project.

## 1. Core Stack
- **Frontend**: HTML, Vanilla TailwindCSS, HTMX, AlpineJS. Chosen for a lightweight, interactive, and beautifully simple interface without the overhead of heavy SPA frameworks.
- **Backend**: Python 3.11, FastAPI. Selected for its high performance and robust asynchronous capabilities.
- **Database**: SQLAlchemy with SQLite. Suitable for a lightweight, self-hosted local application.
- **Queue/Workers**: Redis Queue (RQ) for handling background processing of tracks asynchronously.
- **Core Extractors**: `spotdl` and `yt-dlp` for reliable audio extraction from Spotify and YouTube.
- **Deployment**: Docker and Docker Compose to ensure predictable, environment-agnostic setups.

## 2. Infrastructure Patterns
- **Delta-Sync**: We verify tracks against a local SQLite database to prevent redundant downloads and save resources.
- **Live Progress**: Employs seamless HTMX polling for real-time dashboard updates.
- **Notifications**: Telegram integration provides immediate feedback to users for batch download completions.
