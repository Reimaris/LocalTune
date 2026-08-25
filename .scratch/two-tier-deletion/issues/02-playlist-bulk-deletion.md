# 02: On-Demand Playlist Bulk Deletion & Dismissal

**What to build:** 
Implement bulk soft-deletion and dismissal for on-demand playlist jobs. Clicking the trash can on a parent playlist row (when terminal) cascades soft-deletion to all child tracks (deleting all their physical files and setting their statuses to `Deleted`). The aggregate playlist status calculates as `Deleted` when all children are `Deleted`, triggering the display of the `Deleted` badge on the parent row. When the aggregate status is `Deleted`, the parent row displays a dismiss (`✕`) icon, which permanently purges all child records from SQLite and clears the entire playlist from the UI table.

**Blocked by:** 01-single-track-deletion

**Status:** done

- [x] Implement `POST /api/jobs/{job_id}/delete` endpoint to bulk soft-delete all child tracks of an on-demand playlist (delete files, set status `Deleted`, clear `file_path`, re-render table).
- [x] Implement `DELETE /api/jobs/{job_id}` endpoint to permanently dismiss/purge all child tracks of a playlist from SQLite.
- [x] Update `api_tracks()` logic in `app/main.py` to calculate playlist aggregate status as `"Deleted"` when all child tracks are `"Deleted"`.
- [x] Ensure playlist aggregate status calculation properly ignores `"Deleted"` tracks for counting `Queued`, `Done`, and `Errors` header stats.
- [x] Update `track_list.html` parent playlist action buttons to show Trash for terminal states, and `✕` Dismiss when aggregate status is `Deleted`.
- [x] Write tests covering playlist bulk soft-deletion and playlist bulk permanent dismissal.
