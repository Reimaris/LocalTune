# Project Reference Export for New AI Agent

This directory contains baseline architecture components extracted from a previous successful project. Your task is to use these as the foundation for the new project.

## 1. Frontend & UI Design (`frontend/`)

The HTML templates in the `frontend/` directory provide a pure structural shell of the original project's UI.

*   **Styling Engine**: This project uses **TailwindCSS** via CDN (`<script src="https://cdn.tailwindcss.com"></script>`). The styling heavily uses Tailwind's utility classes for a dark, sleek, premium aesthetic. Custom colors like `#1e1e24`, `#2a2a32`, and `#d40060` are used frequently.
*   **Interactions**: It includes **htmx** (`htmx.org`) for seamless, SPA-like frontend-backend interactions and **Alpine.js** for lightweight client-side state (like tabs and modals).
*   **Structure**: 
    *   `base.html` provides the overarching layout, navigation shell, and imports. 
    *   `dashboard.html`, `settings.html`, and `item_detail.html` serve as specific view templates showing how cards, lists, settings panels, and detail views are structured.
*   **Action for the New Agent**: Re-implement your new backend routing to render these HTML shells. Re-introduce Jinja loops (`{% for %}`) and dynamic variables (`{{ }}`) where data should be injected for the new business logic.

## 2. Logging Architecture (`logging/`)

The `logging/logger_setup.py` file demonstrates the standardized logging configuration used in the original project.

*   **Structure**: It establishes both a `StreamHandler` (for standard console output) and a `TimedRotatingFileHandler` (for rolling log files that archive daily).
*   **Dynamic Directories**: It dynamically ensures that the `config/logs/` directory exists. This is critical because the new project will likely mount a local `./config/` directory into its Docker container, and we want logs to safely persist there.
*   **Action for the New Agent**: Place this or a similar configuration in the core of your new Python application (e.g., `app/main.py` or `app/core/logging_config.py`). Import and call `setup_logging()` on startup so the entire app immediately logs uniformly to both the console and the persistent `config/logs/` volume.
