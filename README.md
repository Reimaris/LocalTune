# LocalTune

LocalTune is a self-hosted, lightweight web application for managing and downloading audio tracks from Spotify and YouTube. Built with FastAPI, HTMX, TailwindCSS, and Docker, it offers a beautifully simple interface to securely delta-sync your favorite playlists directly to your local storage.

## Features

- **Spotify & YouTube Support:** Powered by `spotdl` and `yt-dlp`.
- **Delta-Sync:** Tracks are intelligently checked against a local SQLite database to prevent re-downloading duplicates.
- **Live Progress Dashboard:** Watch your downloads queue, process, and complete in real-time via seamless HTMX polling.
- **Host File Permissions:** Background processes automatically fix file permissions so your host machine can effortlessly manage the downloaded MP3s.
- **Telegram Notifications:** Get instantly notified via Telegram when a batch download completes.
- **Secure Settings:** Configure API keys and Bot Tokens securely via a database-backed Settings panel on the web app.

## Prerequisites

- **Docker & Docker Compose** must be installed on your host machine.
- (Optional) Telegram Bot Token and Chat ID for notifications.
- (Optional) Spotify Client ID and Secret for robust playlist extraction.

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Reimaris/LocalTune.git
   cd LocalTune
   ```

2. **Start the application:**
   Using Docker Compose, build and spin up the web interface, the background worker, and the Redis queue:
   ```bash
   docker compose up --build -d
   ```
   *(For development/testing, use `docker-compose.test.yml`)*

3. **Access the Web App:**
   Open your browser and navigate to:
   `http://localhost:8000`

4. **Configure Settings:**
   Head to the **Settings** tab in the navigation bar to configure your Telegram Notification tokens and Spotify API credentials.

## Usage

Simply paste a Spotify Track/Playlist URL or a YouTube Video/Music URL into the Dashboard's input field and click **Download**. 
LocalTune will queue the job in the background, extract the metadata, cross-reference the local SQLite database, and download any missing MP3s into the `downloads/` directory.

## Architecture

- **Frontend:** HTML, Vanilla TailwindCSS, HTMX, AlpineJS.
- **Backend:** Python 3.11, FastAPI, SQLAlchemy (SQLite), Redis Queue (RQ).
- **Core Extractors:** `spotdl`, `yt-dlp`.

## License

MIT License.
