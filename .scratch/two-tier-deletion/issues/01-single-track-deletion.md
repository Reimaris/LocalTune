# 01: Single Track Two-Stage Deletion & Dismissal

**What to build:** 
Implement a two-stage deletion lifecycle for individual on-demand tracks. When a user clicks the trash icon on a terminal track (`Completed`, `Failed`, `Aborted`), the physical file on disk is immediately deleted and the database row's status transitions to `Deleted` with `file_path` set to `None`. The row remains visible in the history table with a `Deleted` badge. When in the `Deleted` state, a new `✕` (Dismiss / Clear) icon appears, which permanently purges the record from SQLite and the UI table. Active downloads (`Queued`, `Downloading`) must remain protected by the Abort button and cannot be deleted until terminal. Re-submitting a URL for a `Deleted` track via Delta-Sync allows it to be re-downloaded (updating status back to `Downloading`/`Completed`).

**Blocked by:** None (can start immediately).

**Status:** done

- [x] Implement `POST /api/tracks/{track_id}/delete` endpoint to handle soft-deletion (delete disk file, set status `Deleted`, clear `file_path`, re-render table).
- [x] Update `DELETE /api/tracks/{track_id}` endpoint to handle permanent dismissal (purge from SQLite).
- [x] Update Delta-Sync logic in `check_exists` to treat `Deleted` status or missing `file_path` as false, allowing re-download.
- [x] Ensure `Queued` and `Downloading` tracks are rejected by the delete endpoint.
- [x] Update `track_list.html` to display the `Deleted` status badge.
- [x] Update `track_list.html` action buttons for single tracks and child tracks to reflect the two-stage lifecycle (Trash icon for terminal, `✕` Dismiss icon for `Deleted`).
- [x] Write tests covering single track soft-deletion, permanent dismissal, and Delta-Sync re-download.
