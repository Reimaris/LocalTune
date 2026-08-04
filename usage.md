# LocalTune - Usage Guide

## Prerequisites
- Docker and Docker Compose
- Git

## Environment Setup
Create a `.env` file in the root of the project with the following keys:

```env
# Example .env configuration
TELEGRAM_BOT_TOKEN="your-bot-token"
TELEGRAM_CHAT_ID="your-chat-id"

# Optional: Spotify API Keys (if spotdl requires them explicitly, though it usually handles it automatically)
# SPOTIPY_CLIENT_ID="your-client-id"
# SPOTIPY_CLIENT_SECRET="your-client-secret"
```

## Running in Production Mode
This will mount `./config` for persistent logs and SQLite database, and `./downloads` for the audio files.

```bash
docker-compose up --build -d
```

Access the UI at `http://localhost:8000`.

## Running in Testing Mode
This isolates data to `./test_config`.

```bash
docker-compose -f docker-compose.test.yml up --build
```
