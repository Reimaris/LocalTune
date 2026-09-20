"""
Thread-safe in-memory process registry for tracking active download subprocesses
and managing cancellation at track or job level.

Only on-demand downloads (synced_playlist_id IS NULL) are managed here.
Synced Playlist jobs are intentionally excluded from abort capabilities.
"""
import glob
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Singleton registry that maps active on-demand download jobs and tracks
    to their underlying subprocess handles and cancellation flags.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (job_id, track_id) -> Popen
        self._processes: dict[tuple[str, str], subprocess.Popen] = {}  # type: ignore[type-arg]
        # job_id -> set of aborted track_ids
        self._aborted_tracks: dict[str, set[str]] = {}
        # job_id -> True when the whole job is aborted
        self._aborted_jobs: set[str] = set()
        # job_id -> set of track_ids explicitly cleared/unaborted
        self._cleared_tracks: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_process(
        self, job_id: str, track_id: str, proc: "subprocess.Popen[str]"
    ) -> None:
        """Register an active subprocess under (job_id, track_id)."""
        with self._lock:
            self._processes[(job_id, track_id)] = proc

    def unregister_process(self, job_id: str, track_id: str) -> None:
        """Remove a completed or terminated subprocess from the registry."""
        with self._lock:
            self._processes.pop((job_id, track_id), None)

    # ------------------------------------------------------------------
    # Abort queries
    # ------------------------------------------------------------------

    def is_track_aborted(self, job_id: str, track_id: str) -> bool:
        """Returns True if either this specific track or its entire job is aborted."""
        with self._lock:
            if track_id in self._cleared_tracks.get(job_id, set()):
                return False
            return (
                job_id in self._aborted_jobs
                or track_id in self._aborted_tracks.get(job_id, set())
            )

    def is_job_aborted(self, job_id: str) -> bool:
        """Returns True if the whole job has been aborted."""
        with self._lock:
            return job_id in self._aborted_jobs

    # ------------------------------------------------------------------
    # Abort actions
    # ------------------------------------------------------------------

    def abort_track(self, job_id: str, track_id: str) -> bool:
        """
        Mark a single track as aborted and kill its active subprocess if one exists.
        Returns True if the track was in an active state that could be aborted.
        """
        with self._lock:
            if job_id in self._cleared_tracks:
                self._cleared_tracks[job_id].discard(track_id)
            # Record the abort signal
            if job_id not in self._aborted_tracks:
                self._aborted_tracks[job_id] = set()
            self._aborted_tracks[job_id].add(track_id)

            proc = self._processes.get((job_id, track_id))

        if proc is not None:
            self._terminate_process(proc, f"track {track_id}")
            return True
        return True  # Signal recorded even if process not yet started

    def abort_job(self, job_id: str) -> None:
        """
        Mark an entire job as aborted and kill all its active subprocesses.
        """
        with self._lock:
            self._aborted_jobs.add(job_id)
            self._cleared_tracks.pop(job_id, None)
            # Also mark as aborted at the track level for completeness
            active_procs = {
                k: v for k, v in self._processes.items() if k[0] == job_id
            }

        for (j_id, t_id), proc in active_procs.items():
            self._terminate_process(proc, f"job {j_id} / track {t_id}")

    def clear_track_abort(self, job_id: str, track_id: str) -> None:
        """
        Clear abort state for an individual track.
        Used when retrying a single previously aborted track.
        """
        with self._lock:
            if job_id in self._aborted_tracks:
                self._aborted_tracks[job_id].discard(track_id)
                if not self._aborted_tracks[job_id]:
                    self._aborted_tracks.pop(job_id, None)
            if job_id in self._aborted_jobs:
                if job_id not in self._cleared_tracks:
                    self._cleared_tracks[job_id] = set()
                self._cleared_tracks[job_id].add(track_id)

    def clear_job_abort(self, job_id: str) -> None:
        """
        Clear abort state for a job and all its tracks.
        Used when retrying a previously aborted job batch.
        """
        with self._lock:
            self._aborted_jobs.discard(job_id)
            self._aborted_tracks.pop(job_id, None)
            self._cleared_tracks.pop(job_id, None)

    def _terminate_process(
        self, proc: "subprocess.Popen[str]", label: str
    ) -> None:
        """Gracefully terminate a subprocess, falling back to kill."""
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                logger.info(f"Process for {label} terminated.")
        except Exception as e:
            logger.warning(f"Error terminating process for {label}: {e}")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def terminate_all(self) -> int:
        """Terminate every active download subprocess immediately.

        Called during application shutdown *before* partial-file cleanup so
        that no subprocess is still writing to disk when we try to delete
        their output files.  Returns the number of processes that were
        signalled.
        """
        with self._lock:
            snapshot = dict(self._processes)

        count = 0
        for (j_id, t_id), proc in snapshot.items():
            self._terminate_process(proc, f"shutdown / job {j_id} track {t_id}")
            count += 1

        if count:
            logger.info(f"terminate_all: signalled {count} active download process(es) on shutdown.")
        return count

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_job(self, job_id: str) -> None:
        """Remove all registry entries for a completed or aborted job."""
        with self._lock:
            keys_to_remove = [k for k in self._processes if k[0] == job_id]
            for k in keys_to_remove:
                self._processes.pop(k, None)
            self._aborted_tracks.pop(job_id, None)
            self._aborted_jobs.discard(job_id)
            self._cleared_tracks.pop(job_id, None)


def cleanup_partial_files(
    output_path: str | None = None,
    temp_files: list[str] | None = None,
) -> None:
    """
    Remove partial / temporary download artifacts from disk.

    - Deletes the named output_path and any sibling .part / .ytdl files.
    - Deletes every path listed in temp_files.
    """
    # Clean up .part and .ytdl artefacts adjacent to the output path
    if output_path:
        for pattern in (
            output_path + ".part",
            output_path + ".ytdl",
            output_path,  # the incomplete target file itself
        ):
            _safe_remove(pattern)

        # Also glob for yt-dlp .part files in the same directory
        parent_dir = os.path.dirname(output_path)
        if parent_dir and os.path.isdir(parent_dir):
            for part_file in glob.glob(os.path.join(parent_dir, "*.part")):
                _safe_remove(part_file)

    # Clean up explicitly named temp files
    for path in temp_files or []:
        _safe_remove(path)


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.debug(f"Removed partial/temp file: {path}")
    except Exception as e:
        logger.warning(f"Could not remove file {path}: {e}")


# Module-level singleton — imported by downloader.py and main.py
download_manager = DownloadManager()


def cleanup_all_partial_files(download_dir: str | Path | None = None) -> int:
    """Recursively remove all in-flight .part and .ytdl files, root temp_*.spotdl files,
    and all stale files inside <DOWNLOAD_DIR>/.temp staging directory.

    Returns the count of removed partial files.
    """
    from app.core.downloader import DOWNLOAD_DIR

    target_dir = str(download_dir or DOWNLOAD_DIR)
    removed_count = 0
    if os.path.isdir(target_dir):
        # 1. Clean all files and directories in .temp staging directory
        temp_dir = os.path.join(target_dir, ".temp")
        if os.path.isdir(temp_dir):
            for item in os.listdir(temp_dir):
                item_path = os.path.join(temp_dir, item)
                try:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                        removed_count += 1
                    else:
                        os.remove(item_path)
                        removed_count += 1
                        logger.debug(f"Removed staging file: {item_path}")
                except OSError as e:
                    logger.warning(f"Failed to remove staging file {item_path}: {e}")

        # 2. Walk rest of target_dir for any stray .part and .ytdl files
        for root, _, files in os.walk(target_dir):
            for f in files:
                if f.endswith((".part", ".ytdl")):
                    full_path = os.path.join(root, f)
                    try:
                        os.remove(full_path)
                        removed_count += 1
                        logger.debug(f"Removed partial file: {full_path}")
                    except OSError as e:
                        logger.warning(f"Failed to remove {full_path}: {e}")

    for temp_spotdl in glob.glob("temp_*.spotdl"):
        try:
            os.remove(temp_spotdl)
            removed_count += 1
        except OSError:
            pass

    return removed_count
