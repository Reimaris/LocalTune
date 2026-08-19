# Ubiquitous Language & Domain Context — LocalTune

## Core Terms

- **LocalTune Core**: The single-container FastAPI audio downloader and sync application running inside Docker.
- **Windows Manager**: An optional, standalone Windows GUI executable (`LocalTune_Manager.exe`) built with Python `customtkinter` to assist non-technical users in installing, starting, stopping, updating, and opening folders for LocalTune.
- **Single-Container Architecture**: The operational pattern where LocalTune runs entirely within one Docker container (FastAPI + BackgroundTasks + SQLite) without external service dependencies like Redis or RQ.
- **GHCR Image**: The official pre-built Docker container image hosted at `ghcr.io/reimaris/localtune:latest`.
- **Delta-Sync**: The process of cross-referencing requested tracks against the local SQLite database before triggering downloads to eliminate duplicate work.
- **Spotify Handler**: The dedicated download engine using `spotdl` CLI for Spotify tracks, albums, and playlists.
- **yt-dlp Fallback Handler**: The generalized download engine using `yt-dlp` CLI for all non-Spotify URLs, natively supporting SoundCloud, YouTube, Bandcamp, Mixcloud, and other platforms.
- **Namespaced Track ID**: The `{extractor_key}_{raw_id}` string format stored in SQLite to uniquely identify tracks across different platforms during Delta-Sync.
- **Canonical Track Batching**: The process of extracting canonical item URLs (`webpage_url`) from `yt-dlp` metadata for multi-track batch downloads across all supported platforms.
- **Playlist Sync**: The background automation feature in LocalTune Core that periodically polls remote playlists and downloads new tracks.
- **Synced Playlist**: A persisted entity in SQLite representing a tracked remote playlist with an assigned `Sync Mode`, status, and schedule tracking.
- **Sync Mode**: The per-playlist policy governing file retention (`Append-Only` vs `Mirror / Prune`).
- **Global Sync Scheduler**: The 6-hour automatic background polling timer running inside LocalTune Core.
- **Sync-Now Trigger**: The manual UI action allowing instant on-demand sync for an individual `Synced Playlist`.
- **Verified Metadata Pruning**: The safety enforcement requiring 100% complete metadata extraction before `Mirror / Prune` mode deletes any local audio files.
- **Feature Switcher**: The top-left toggle navigation allowing users to switch between On-Demand Downloader and Synced Playlists views.
- **On-Demand Downloader View**: The view containing the single download URL input form and global download history table.
- **Synced Playlists View**: The card-grid view displaying all tracked playlists, manual sync triggers, and setup controls.
- **Add Playlist Modal**: The setup overlay for adding a new Synced Playlist with URL, Sync Mode selection, and title override options.
- **Synced Playlist Card**: The UI component displaying playlist metadata, platform badge, sync status (`Active`, `Paused`, `Syncing`, `Failed`), and actions (`Sync-Now`, `Pause/Resume`, `Edit`, `Delete`).
- **Deletion Confirmation Modal**: The modal prompting users to choose between keeping or purging downloaded files when deleting a Synced Playlist card.
- **On-Demand History Scope**: The strict scope filtering for the On-Demand Downloader history table, displaying strictly manual on-demand downloads (`synced_playlist_id IS NULL`) and suppressing Synced Playlist background sync jobs.
- **Tab State Persistence**: The client-side mechanism using `localStorage` to retain the user's active view (`On-Demand Downloader View` vs `Synced Playlists View`) across browser refreshes (F5).
- **Dashboard URL Override**: The convention in the Windows Manager to strictly use `127.0.0.1` to avoid IPv4/IPv6 loopback issues, with an interchangeable port defined via `LOCALTUNE_PORT` environment variable.
