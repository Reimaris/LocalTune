# Ubiquitous Language & Domain Context — LocalTune

## Core Terms

- **LocalTune Core**: The single-container FastAPI audio downloader and sync application running inside Docker.
- **Windows Manager**: An optional, standalone Windows GUI executable (`LocalTune_Manager.exe`) built with Python `customtkinter` to assist non-technical users in installing, starting, stopping, updating, and opening folders for LocalTune.
- **Single-Container Architecture**: The operational pattern where LocalTune runs entirely within one Docker container (FastAPI + BackgroundTasks + SQLite) without external service dependencies like Redis or RQ.
- **GHCR Image**: The official pre-built Docker container image hosted at `ghcr.io/reimaris/localtune:latest`.
- **Delta-Sync**: The process of cross-referencing requested tracks against the local SQLite database before triggering downloads to eliminate duplicate work.
