# Usage & Runbook — LocalTune

Precise instructions on how to install, run, test, and manage the application.

## Prerequisites

- **Docker** and **Docker Compose** installed on the host machine.
- Optional: Telegram Bot Token, Chat ID, and Spotify Client ID/Secret for extended features.

## Installation & Running (Docker)

1. Clone the repository and change directory to the project root:
   ```bash
   git clone https://github.com/Reimaris/LocalTune.git
   cd LocalTune
   ```
2. Build and start the containers (Web app, Worker, Redis):
   ```bash
   docker compose up --build -d
   ```
3. Access the web interface at `http://localhost:8001`.

## Configuration

- Navigate to the **Settings** tab in the web UI to enter Telegram Notification tokens and Spotify API credentials securely.

## Local Testing

- For development and testing, you can use the test environment configuration:
  ```bash
  docker-compose -f docker-compose.test.yml up --build -d
  ```

## Windows Manager GUI

- For Windows users, a standalone `LocalTuneManager.exe` is available in the Releases section on GitHub.
- Developers can build it from source by navigating to `windows_manager/` and running `build.bat`.
