# 03: Two-Tier Abort Endpoints & Table Action Buttons Integration

**What to build:** End-to-end API endpoints and UI interactivity where users see the distinct `Aborted` status badge and can click dedicated Abort buttons on active single tracks (`POST /api/tracks/{id}/abort`) or parent playlist rows (`POST /api/jobs/{id}/abort`) via HTMX. The UI reacts instantly to aborted downloads and replaces the abort buttons with standard delete/download actions once terminal.

**Blocked by:** 01: Responsive Table Layout & Natural Text Wrapping, 02: Process Registry, Subprocess Lifecycle & Partial File Purge Engine

**Status:** ready-for-agent

- [ ] `POST /api/tracks/{track_id}/abort` cancels an individual on-demand track and updates the table row state.
- [ ] `POST /api/jobs/{job_id}/abort` cancels an entire multi-track job batch and updates the playlist and child track rows.
- [ ] Table view renders a distinct `Aborted` status badge (styled with subtle zinc/dark badges distinct from `Failed`).
- [ ] Table rows for tracks in `Queued` or `Downloading` display an Abort button in the Actions column.
- [ ] Parent playlist rows with active tracks display an Abort Job button in the Actions column.
- [ ] Terminal rows (`Completed`, `Failed`, `Aborted`) display the standard Delete action (and Download action for `Completed`).
- [ ] Aborting via HTMX updates the UI immediately and maintains correct counts in the header stats widget.
