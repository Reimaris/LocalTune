import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any

try:
    import tkinter as tk
except ImportError:
    tk = None  # type: ignore[assignment]


MUTEX_NAME = "LocalTune_SingleInstance_Mutex"
DEFAULT_PORT = "8000"
LOCK_SOCKET_PORT = 48991
SUBPROCESS_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0


def get_resource_path(relative_path: str) -> str:
    """Get absolute path to bundled resource (works in dev and PyInstaller onefile)."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def get_base_dir() -> str:
    """Return directory containing the executable or script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_project_dir() -> str | None:
    """Locate the LocalTune project root containing app/main.py."""
    base = get_base_dir()
    for candidate in (base, os.path.join(base, "LocalTune")):
        if os.path.exists(os.path.join(candidate, "app", "main.py")):
            return candidate
        for compose_name in ("compose.yaml", "docker-compose.yml"):
            if os.path.exists(os.path.join(candidate, compose_name)):
                return candidate
    return None


def get_python_executable(project_dir: str | None = None) -> str | None:
    """Discovers python executable in priority order: embedded runtime -> .venv -> system."""
    if project_dir is None:
        project_dir = get_project_dir() or get_base_dir()

    # 1. Embedded runtime (All-in-One standalone bundle)
    runtime_candidates = [
        os.path.join(project_dir, "runtime", "python.exe"),
        os.path.join(project_dir, "runtime", "bin", "python"),
        os.path.join(get_base_dir(), "runtime", "python.exe"),
    ]
    for cand in runtime_candidates:
        if os.path.exists(cand):
            return cand

    # 2. Local virtual environment (.venv or venv)
    venv_subdirs = [
        os.path.join(project_dir, ".venv", "Scripts", "python.exe"),
        os.path.join(project_dir, ".venv", "bin", "python"),
        os.path.join(project_dir, "venv", "Scripts", "python.exe"),
        os.path.join(project_dir, "venv", "bin", "python"),
    ]
    for cand in venv_subdirs:
        if os.path.exists(cand):
            return cand

    # 3. System python fallback
    if not getattr(sys, "frozen", False) and sys.executable:
        return sys.executable

    which_py = shutil.which("python") or shutil.which("python3")
    if which_py:
        return which_py

    return None


def build_backend_env(project_dir: str | None = None) -> dict[str, str]:
    """Builds environment variables injecting bundled bin/ and runtime/ into PATH."""
    if project_dir is None:
        project_dir = get_project_dir() or get_base_dir()

    env = os.environ.copy()
    bin_dir = os.path.abspath(os.path.join(project_dir, "bin"))
    runtime_dir = os.path.abspath(os.path.join(project_dir, "runtime"))

    path_sep = ";" if os.name == "nt" else ":"
    existing_path = env.get("PATH", "")
    env["PATH"] = f"{bin_dir}{path_sep}{runtime_dir}{path_sep}{existing_path}"
    env["PYTHONPATH"] = project_dir
    env["PYTHONUNBUFFERED"] = "1"

    if "DOWNLOAD_DIR" not in env:
        env["DOWNLOAD_DIR"] = os.path.join(project_dir, "downloads")

    return env


def kill_process_tree(proc: subprocess.Popen[Any]) -> None:
    """Terminates process and any spawned child processes cleanly across platforms."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3.0)
            return
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass

    # Force kill process tree
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    else:
        try:
            proc.kill()
        except Exception:
            pass


class SingleInstanceLock:
    """Win32 Named Mutex single-instance lock with local socket fallback."""

    def __init__(
        self,
        mutex_name: str = MUTEX_NAME,
        port: int = LOCK_SOCKET_PORT,
    ) -> None:
        self.mutex_name = mutex_name
        self.port = port
        self.is_locked = False
        self._win32_handle: Any = None
        self._socket: socket.socket | None = None

    def acquire(self) -> bool:
        if os.name == "nt":
            import ctypes

            kernel32 = getattr(ctypes, "windll", None)
            if kernel32:
                kernel32 = kernel32.kernel32
                self._win32_handle = kernel32.CreateMutexW(None, False, self.mutex_name)
                # ERROR_ALREADY_EXISTS = 183
                if kernel32.GetLastError() == 183:
                    self.is_locked = False
                    return False
                self.is_locked = True
                return True

        # Non-Windows or fallback socket lock
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
            self._socket = s
            self.is_locked = True
            return True
        except OSError:
            self.is_locked = False
            return False

    def release(self) -> None:
        if self._win32_handle is not None and os.name == "nt":
            import ctypes

            kernel32 = getattr(ctypes, "windll", None)
            if kernel32:
                kernel32.kernel32.CloseHandle(self._win32_handle)
            self._win32_handle = None

        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

        self.is_locked = False

    def handle_existing_instance(self, url: str = "http://127.0.0.1:8000") -> None:
        """Opens the existing dashboard in browser and exits process with code 0."""
        webbrowser.open(url)
        sys.exit(0)


def spawn_backend_process(
    project_dir: str | None = None,
    port: str = DEFAULT_PORT,
    log_file: str | None = None,
) -> tuple[subprocess.Popen[str], threading.Thread]:
    """Spawns the uvicorn backend subprocess and streams output to log file."""
    if project_dir is None:
        project_dir = get_project_dir() or get_base_dir()

    python_exe = get_python_executable(project_dir)
    if not python_exe:
        raise RuntimeError("Python runtime not found.")

    if log_file is None:
        log_file = os.path.join(project_dir, "config", "logs", "localtune.log")

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    cmd = [python_exe, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
    env = build_backend_env(project_dir)

    proc: subprocess.Popen[str] = subprocess.Popen(
        cmd,
        cwd=project_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )

    def log_streamer(p: subprocess.Popen[str], path: str) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                if p.stdout:
                    for line in iter(p.stdout.readline, ""):
                        f.write(line)
                        f.flush()
        except Exception:
            pass

    thread = threading.Thread(target=log_streamer, args=(proc, log_file), daemon=True)
    thread.start()

    return proc, thread


def wait_for_backend_health(
    url: str = "http://127.0.0.1:8000",
    timeout: float = 5.0,
    interval: float = 0.25,
    proc: subprocess.Popen[Any] | None = None,
) -> bool:
    """Polls backend URL until it responds with HTTP or timeout/process exit occurs."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LocalTune-Launcher"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                status = getattr(resp, "status", getattr(resp, "code", None))
                if status in (200, 204, 301, 302, 307, 308):
                    return True
        except urllib.error.HTTPError:
            # Any HTTP status response means backend is accepting requests
            return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def read_recent_logs(log_path: str, max_lines: int = 20) -> str:
    """Reads the last N lines from the log file."""
    if not os.path.exists(log_path):
        return "(No log file found)"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return "".join(lines[-max_lines:])
    except Exception as e:
        return f"(Error reading logs: {e})"


def open_file_or_folder(target_path: str) -> None:
    """Opens a file or folder using the OS default application."""
    if not os.path.exists(target_path):
        os.makedirs(target_path, exist_ok=True)
    try:
        if hasattr(os, "startfile"):
            os.startfile(target_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.call(["open", target_path])
        else:
            subprocess.call(["xdg-open", target_path])
    except Exception as e:
        sys.stderr.write(f"Failed to open {target_path}: {e}\n")


def show_startup_error_dialog(error_details: str, log_path: str, root: Any = None) -> None:
    """Displays a diagnostic modal dialog showing startup failure and 'Open Logs' button."""
    if tk is None:
        sys.stderr.write(f"Startup error occurred:\n{error_details}\n")
        return

    try:
        should_destroy_root = False
        if root is None:
            root = tk.Tk()
            root.title("LocalTune Startup Error")
            should_destroy_root = True
        else:
            root.title("LocalTune Startup Error")

        root.geometry("520x360")
        root.minsize(450, 300)

        frame = tk.Frame(root, padx=15, pady=15)
        frame.pack(fill=tk.BOTH, expand=True)

        header_lbl = tk.Label(
            frame,
            text="⚠️ Failed to start LocalTune backend",
            font=("Arial", 12, "bold"),
            fg="#dc2626",
            anchor="w",
        )
        header_lbl.pack(fill=tk.X, pady=(0, 8))

        desc_lbl = tk.Label(
            frame,
            text="The server failed to respond on port 8000. Recent log output:",
            font=("Arial", 9),
            anchor="w",
        )
        desc_lbl.pack(fill=tk.X, pady=(0, 5))

        text_box = tk.Text(frame, wrap=tk.WORD, height=10, font=("Consolas", 9))
        text_box.insert(tk.END, error_details)
        text_box.config(state=tk.DISABLED)
        text_box.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        btn_frame = tk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        def on_open_logs() -> None:
            open_file_or_folder(log_path)

        def on_copy() -> None:
            root.clipboard_clear()
            root.clipboard_append(error_details)

        def on_close() -> None:
            if should_destroy_root:
                root.destroy()

        open_btn = tk.Button(btn_frame, text="📄 Open Logs", command=on_open_logs, padx=10)
        open_btn.pack(side=tk.LEFT, padx=(0, 5))

        copy_btn = tk.Button(btn_frame, text="📋 Copy Details", command=on_copy, padx=10)
        copy_btn.pack(side=tk.LEFT, padx=(0, 5))

        close_btn = tk.Button(btn_frame, text="Close", command=on_close, padx=10)
        close_btn.pack(side=tk.RIGHT)

        if should_destroy_root:
            root.mainloop()

    except Exception as e:
        sys.stderr.write(f"Startup error dialog failed to display: {e}\nDetails:\n{error_details}\n")


class LocalTuneSupervisor:
    """Headless native process supervisor with single-instance lock and health guard."""

    def __init__(
        self,
        project_dir: str | None = None,
        port: str = DEFAULT_PORT,
        log_file: str | None = None,
    ) -> None:
        self.project_dir = project_dir or get_project_dir() or get_base_dir()
        self.port = port
        self.log_file = log_file or os.path.join(self.project_dir, "config", "logs", "localtune.log")
        self.lock = SingleInstanceLock()
        self.backend_proc: subprocess.Popen[str] | None = None
        self.log_thread: threading.Thread | None = None

    def acquire_lock(self) -> bool:
        return self.lock.acquire()

    def start(self, open_browser: bool = True) -> bool:
        """Starts the backend, performs health check, and opens browser."""
        if not self.acquire_lock():
            self.lock.handle_existing_instance(f"http://127.0.0.1:{self.port}")
            return False

        try:
            self.backend_proc, self.log_thread = spawn_backend_process(
                project_dir=self.project_dir,
                port=self.port,
                log_file=self.log_file,
            )
        except Exception as e:
            show_startup_error_dialog(f"Failed to spawn backend process: {e}", self.log_file)
            self.stop()
            return False

        # Health probe (up to 5s)
        healthy = wait_for_backend_health(
            url=f"http://127.0.0.1:{self.port}",
            timeout=5.0,
            interval=0.25,
            proc=self.backend_proc,
        )

        if not healthy:
            logs = read_recent_logs(self.log_file)
            show_startup_error_dialog(logs, self.log_file)
            self.stop()
            return False

        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{self.port}")

        return True

    def stop(self) -> None:
        """Terminates backend process tree and releases single-instance lock."""
        if self.backend_proc:
            kill_process_tree(self.backend_proc)
            self.backend_proc = None
        self.lock.release()

    def open_dashboard(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}")

    def open_logs(self) -> None:
        open_file_or_folder(self.log_file)

    def open_downloads(self) -> None:
        downloads_dir = os.path.join(self.project_dir, "downloads")
        open_file_or_folder(downloads_dir)


def main() -> None:
    supervisor = LocalTuneSupervisor()
    if supervisor.start(open_browser=True):
        try:
            # Keep supervisor running if started standalone
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            supervisor.stop()


if __name__ == "__main__":
    main()
