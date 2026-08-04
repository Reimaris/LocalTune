# Architecture Decisions Record (ADR)

## 1. Web Framework: FastAPI
- **Why**: Excellent async support, built-in validation (Pydantic), fast development cycle. Fits perfectly with a modern, lightweight backend needing robust APIs.

## 2. Background Tasks: Redis + RQ (Redis Queue)
- **Why**: Simpler to deploy and maintain than Celery for straightforward task queueing. Sufficient for enqueuing subprocess calls (`yt-dlp`, `spotdl`) and pushing results back.

## 3. Database: SQLite (via SQLAlchemy)
- **Why**: Self-contained and portable. Easy to volume mount into Docker (`/app/config/localtune.db`). Sufficient concurrent read capacity for a single-user/small-scale downloader tool.

## 4. Containerization: Dual Docker Compose
- **Why**: Keep production data pristine. `docker-compose.yml` mounts real configuration, while `docker-compose.test.yml` mounts ephemeral or mocked configurations. Ensures a clean slate for integration tests. Both environments use the same `Dockerfile` for parity.

## 5. UI: HTMX + TailwindCSS (CDN)
- **Why**: Adopted from `project_reference_export`. Minimal build step required. HTMX provides smooth SPA-like interactivity (e.g., polling task progress) without writing bulky JavaScript.
