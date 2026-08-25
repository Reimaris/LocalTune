# 02: Process Registry, Subprocess Lifecycle & Partial File Purge Engine

**What to build:** An in-memory subprocess manager and cancellation engine integrated with the downloader worker. Enables granular track-by-track execution, active subprocess tracking (`Popen`), immediate SIGTERM termination on abort, instant cleanup of `.part` / `.ytdl` / temp files from disk, `Aborted` state recording in SQLite, Telegram notification suppression on cancellation, and seamless continuation of remaining queued tracks in a batch.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] In-memory process registry tracks active worker subprocesses and cancellation flags per `(job_id, track_id)`.
- [ ] Subprocess execution for batch jobs supports granular per-track abort checks before and during download runs.
- [ ] Aborting a running download terminates the subprocess group cleanly via SIGTERM/kill.
- [ ] Any partial or temporary files (`.part`, `.ytdl`, `temp_*.spotdl`, `batch_*.txt`, incomplete media outputs) are automatically cleaned up from the filesystem upon abort.
- [ ] Aborted tracks transition to `Aborted` status in SQLite.
- [ ] Aborting an individual track within a multi-track batch allows subsequent queued tracks in that batch to continue downloading normally.
- [ ] Aborting a full job cancels all pending and active tracks for that job and halts further processing.
- [ ] Telegram notifications are completely suppressed for user-initiated aborts.
