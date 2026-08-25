"""
Thread-safe in-memory process registry for tracking active download subprocesses
and managing cancellation at track or job level.

Only on-demand downloads (synced_playlist_id IS NULL) are managed here.
Synced Playlist jobs are intentionally excluded from abort capabilities.
"""
import glob
import logging
import os
import subprocess
import threading

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
            # Also mark as aborted at the track level for completeness
            active_procs = {
                k: v for k, v in self._processes.items() if k[0] == job_id
            }

        for (j_id, t_id), proc in active_procs.items():
            self._terminate_process(proc, f"job {j_id} / track {t_id}")

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
