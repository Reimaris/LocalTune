import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from collections.abc import Callable
from typing import Any

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    tk = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    filedialog = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]

try:
    import pystray
    from PIL import Image
except ImportError:
    pystray = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]


class SplashScreen:
    """Lightweight frameless dark-mode startup splash screen built on tkinter.

    Displays the app title, version badge, an indeterminate animated progress
    bar, and a dynamic status label while the supervisor initialises.

    If tkinter is unavailable (headless / server environments), all methods
    silently no-op so the supervisor continues without errors.
    """

    def __init__(self, title: str = "LocalTune", version: str = "") -> None:
        self._root: Any = None
        self._status_var: Any = None
        self._progress: Any = None
        self._closed = False

        if tk is None:
            return  # headless fallback — do nothing

        try:
            root = tk.Tk()
            root.title(title)
            root.overrideredirect(True)  # frameless window
            root.configure(bg="#1e1e24")
            root.resizable(False, False)

            # --- geometry: centre on primary display ---
            width, height = 420, 220
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            x = (sw - width) // 2
            y = (sh - height) // 2
            root.geometry(f"{width}x{height}+{x}+{y}")
            root.lift()
            root.attributes("-topmost", True)
            root.attributes("-topmost", False)  # raise to foreground on open without forcing topmost
            root.focus_force()

            # --- thin border frame ---
            border_frame = tk.Frame(root, bg="#3f3f46", padx=1, pady=1)
            border_frame.pack(fill=tk.BOTH, expand=True)
            inner = tk.Frame(border_frame, bg="#1e1e24", padx=24, pady=20)
            inner.pack(fill=tk.BOTH, expand=True)

            # --- app title ---
            tk.Label(
                inner,
                text=title,
                font=("Arial", 18, "bold"),
                bg="#1e1e24",
                fg="#f4f4f5",
            ).pack(anchor="w")

            # --- version badge ---
            if version:
                tk.Label(
                    inner,
                    text=version,
                    font=("Arial", 10),
                    bg="#1e1e24",
                    fg="#10b981",
                ).pack(anchor="w", pady=(0, 12))
            else:
                tk.Label(inner, text="", bg="#1e1e24").pack(pady=(0, 12))

            # --- dynamic status label ---
            self._status_var = tk.StringVar(value="Starting…")
            tk.Label(
                inner,
                textvariable=self._status_var,
                font=("Arial", 9),
                bg="#1e1e24",
                fg="#a1a1aa",
            ).pack(anchor="w", pady=(0, 10))

            # --- indeterminate progress bar (styled emerald) ---
            if ttk is not None:
                try:
                    style = ttk.Style(root)
                    style.theme_use("default")
                    style.configure(
                        "Splash.Horizontal.TProgressbar",
                        troughcolor="#3f3f46",
                        background="#10b981",
                        thickness=6,
                    )
                    bar = ttk.Progressbar(
                        inner,
                        mode="indeterminate",
                        style="Splash.Horizontal.TProgressbar",
                        length=370,
                    )
                    bar.pack(fill=tk.X, pady=(0, 4))
                    bar.start(12)
                    self._progress = bar
                except Exception:
                    pass  # progress bar is cosmetic; continue without it

            self._root = root
            root.update()

        except Exception:
            # Any Tk initialisation failure — degrade gracefully
            self._root = None

    def pump(self) -> None:
        """Pumps the Tkinter event loop to keep the splash responsive and animating."""
        if self._root is None or self._closed:
            return
        try:
            self._root.update()
        except Exception:
            pass

    def update_status(self, text: str) -> None:
        """Update the dynamic status label text and immediately refresh."""
        if self._root is None or self._closed:
            return
        try:
            if self._status_var is not None:
                self._status_var.set(text)
            self._root.update()
        except Exception:
            pass

    def close(self) -> None:
        """Destroy the splash window. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        self._status_var = None
        if self._root is not None:
            try:
                if self._progress is not None:
                    self._progress.stop()
                self._progress = None
                self._root.destroy()
            except Exception:
                pass
            self._root = None


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


def get_launcher_config_path(project_dir: str | None = None) -> str:
    """Returns absolute path to config/launcher.json."""
    if project_dir is None:
        project_dir = get_project_dir() or get_base_dir()
    return os.path.abspath(os.path.join(project_dir, "config", "launcher.json"))


def load_launcher_config(project_dir: str | None = None) -> dict[str, Any]:
    """Loads launcher configuration safely from config/launcher.json."""
    config_path = get_launcher_config_path(project_dir)
    if not os.path.exists(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to load launcher config from {config_path}: {e}\n")
    return {}


def save_launcher_config(config: dict[str, Any], project_dir: str | None = None) -> None:
    """Saves launcher configuration to config/launcher.json atomically."""
    config_path = get_launcher_config_path(project_dir)
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    temp_path = config_path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        if os.name == "nt" and os.path.exists(config_path):
            try:
                os.replace(temp_path, config_path)
            except OSError:
                os.remove(config_path)
                os.rename(temp_path, config_path)
        else:
            os.replace(temp_path, config_path)
    except Exception as e:
        sys.stderr.write(f"Error saving launcher config to {config_path}: {e}\n")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def get_effective_download_dir(project_dir: str | None = None) -> str:
    """Resolves the download directory following strict 3-tier precedence:
    1. System/User os.environ["DOWNLOAD_DIR"]
    2. config/launcher.json -> download_dir
    3. Default <project_dir>/downloads
    """
    if project_dir is None:
        project_dir = get_project_dir() or get_base_dir()

    env_dir = os.environ.get("DOWNLOAD_DIR")
    if env_dir and env_dir.strip():
        return os.path.abspath(env_dir.strip())

    config = load_launcher_config(project_dir)
    cfg_dir = config.get("download_dir")
    if cfg_dir and isinstance(cfg_dir, str) and cfg_dir.strip():
        return os.path.abspath(cfg_dir.strip())

    return os.path.abspath(os.path.join(project_dir, "downloads"))


def validate_directory_writable(target_dir: str) -> tuple[bool, str]:
    """Ensures the directory exists and probes write permissions with a temporary file."""
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        return False, f"Could not create directory: {e}"

    test_probe = os.path.join(target_dir, ".localtune_write_test")
    try:
        with open(test_probe, "w", encoding="utf-8") as f:
            f.write("probe")
        if os.path.exists(test_probe):
            try:
                os.remove(test_probe)
            except OSError:
                pass
        return True, ""
    except Exception as e:
        return False, f"Directory is not writable: {e}"


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

    env["DOWNLOAD_DIR"] = get_effective_download_dir(project_dir)

    return env



def parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Parses a version string like 'v2.5.0' into an integer tuple (2, 5, 0)."""
    cleaned = version_str.strip().lstrip("v")
    try:
        return tuple(int(p) for p in cleaned.split("."))
    except ValueError:
        return (0, 0, 0)


def is_newer_version(remote_tag: str, local_tag: str) -> bool:
    """Compares semver tags to check if remote_tag is strictly newer than local_tag."""
    return parse_version_tuple(remote_tag) > parse_version_tuple(local_tag)


def get_local_version(project_dir: str | None = None) -> str:
    """Reads the current LocalTune version from app/templates/base.html or fallback."""
    if project_dir is None:
        project_dir = get_project_dir() or get_base_dir()
    base_html = os.path.join(project_dir, "app", "templates", "base.html")
    if os.path.exists(base_html):
        try:
            with open(base_html, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"\b(v\d+\.\d+\.\d+)\b", content)
            if match:
                return match.group(1)
        except Exception:
            pass
    return "v2.6.2"


def check_github_release(repo: str = "Reimaris/LocalTune", timeout: float = 3.0) -> dict[str, Any] | None:
    """Queries GitHub API for the latest release metadata with timeout."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "LocalTune-Launcher"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", getattr(resp, "code", None))
            if status == 200:
                data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                return data
    except Exception:
        pass
    return None


def get_release_zip_url(release_info: dict[str, Any]) -> str | None:
    """Extracts the browser download URL for the Windows release zip from release info."""
    assets = release_info.get("assets", [])
    for a in assets:
        name = a.get("name", "")
        if name.endswith(".zip") and ("Windows" in name or "localtune" in name.lower()):
            return str(a.get("browser_download_url"))
    for a in assets:
        if a.get("name", "").endswith(".zip"):
            return str(a.get("browser_download_url"))
    zipball = release_info.get("zipball_url")
    return str(zipball) if zipball else None


def apply_update_archive(zip_path: str, project_dir: str) -> None:
    """Extracts update zip over project_dir, strictly preserving config, downloads, and .env."""
    with tempfile.TemporaryDirectory() as temp_extract:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(temp_extract)

        entries = os.listdir(temp_extract)
        source_dir = temp_extract
        if len(entries) == 1:
            single_sub = os.path.join(temp_extract, entries[0])
            if os.path.isdir(single_sub) and os.path.exists(os.path.join(single_sub, "app")):
                source_dir = single_sub

        protected_names = {"config", "downloads", ".git", ".env"}

        for root, _dirs, files in os.walk(source_dir):
            rel_dir = os.path.relpath(root, source_dir)
            parts = rel_dir.split(os.sep) if rel_dir != "." else []

            if parts and parts[0].lower() in protected_names:
                continue

            dest_dir = os.path.abspath(os.path.join(project_dir, rel_dir))
            os.makedirs(dest_dir, exist_ok=True)

            for f in files:
                if rel_dir == "." and f.lower() in protected_names:
                    continue
                src_file = os.path.join(root, f)
                dest_file = os.path.join(dest_dir, f)
                try:
                    shutil.copy2(src_file, dest_file)
                except PermissionError:
                    old_file = dest_file + ".old"
                    if os.path.exists(old_file):
                        try:
                            os.remove(old_file)
                        except OSError:
                            pass
                    try:
                        os.rename(dest_file, old_file)
                        shutil.copy2(src_file, dest_file)
                    except OSError:
                        pass


def cleanup_old_executables(target_dir: str | None = None) -> None:
    """Removes leftover *.old files from previous in-place updates."""
    if target_dir is None:
        target_dir = get_project_dir() or get_base_dir()
    try:
        for f in os.listdir(target_dir):
            if f.endswith(".old"):
                old_path = os.path.join(target_dir, f)
                try:
                    os.remove(old_path)
                except OSError:
                    pass
    except OSError:
        pass


def download_and_apply_update(download_url: str, project_dir: str | None = None) -> bool:
    """Downloads release zip in background and applies in-place update."""
    if project_dir is None:
        project_dir = get_project_dir() or get_base_dir()

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            temp_zip = tmp_file.name

        req = urllib.request.Request(download_url, headers={"User-Agent": "LocalTune-Launcher"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(temp_zip, "wb") as out:
            shutil.copyfileobj(resp, out)

        apply_update_archive(temp_zip, project_dir)

        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except OSError:
                pass
        return True
    except Exception as e:
        sys.stderr.write(f"Auto-update failed: {e}\n")
        return False


def show_update_dialog(
    remote_tag: str,
    local_tag: str,
    release_url: str,
    download_url: str | None,
    on_update: Callable[[], None] | None = None,
    on_manual: Callable[[], None] | None = None,
    on_skip: Callable[[], None] | None = None,
    root: Any = None,
) -> dict[str, Callable[[], None]]:
    """Displays a modal dialog prompting the user for update actions."""
    def handle_update() -> None:
        if on_update:
            on_update()

    def handle_manual() -> None:
        if on_manual:
            on_manual()
        else:
            webbrowser.open(release_url)

    def handle_skip() -> None:
        if on_skip:
            on_skip()

    callbacks = {
        "on_update": handle_update,
        "on_manual": handle_manual,
        "on_skip": handle_skip,
    }

    if tk is None:
        return callbacks

    try:
        should_destroy_root = False
        if root is None:
            root = tk.Tk()
            root.title("LocalTune Update Available")
            should_destroy_root = True
        else:
            root.title("LocalTune Update Available")

        root.geometry("480x200")
        root.minsize(440, 180)

        frame = tk.Frame(root, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        title_lbl = tk.Label(
            frame,
            text="A new version of LocalTune is available!",
            font=("Arial", 12, "bold"),
            anchor="w",
        )
        title_lbl.pack(fill=tk.X, pady=(0, 6))

        ver_lbl = tk.Label(
            frame,
            text=f"New: {remote_tag}   |   Current: {local_tag}",
            font=("Arial", 10),
            fg="#059669",
            anchor="w",
        )
        ver_lbl.pack(fill=tk.X, pady=(0, 15))

        btn_frame = tk.Frame(frame)
        btn_frame.pack(fill=tk.X, pady=(5, 0))

        def btn_update_click() -> None:
            if should_destroy_root:
                root.destroy()
            handle_update()

        def btn_manual_click() -> None:
            if should_destroy_root:
                root.destroy()
            handle_manual()

        def btn_skip_click() -> None:
            if should_destroy_root:
                root.destroy()
            handle_skip()

        update_btn = tk.Button(
            btn_frame,
            text="⚡ Update & Launch",
            font=("Arial", 9, "bold"),
            bg="#10b981",
            fg="white",
            padx=8,
            pady=4,
            command=btn_update_click,
        )
        update_btn.pack(side=tk.LEFT, padx=(0, 6))

        manual_btn = tk.Button(
            btn_frame,
            text="🌐 Manual Update",
            padx=8,
            pady=4,
            command=btn_manual_click,
        )
        manual_btn.pack(side=tk.LEFT, padx=(0, 6))

        skip_btn = tk.Button(
            btn_frame,
            text="Skip Update",
            padx=8,
            pady=4,
            command=btn_skip_click,
        )
        skip_btn.pack(side=tk.RIGHT)

        if should_destroy_root:
            root.mainloop()

    except Exception as e:
        sys.stderr.write(f"Failed to display update dialog: {e}\n")

    return callbacks


def show_restart_dialog(
    new_dir: str,
    on_restart: Callable[[], None] | None = None,
    on_later: Callable[[], None] | None = None,
    root: Any = None,
) -> dict[str, Callable[[], None]]:
    """Displays a modal dialog prompting the user to restart LocalTune after directory change."""
    def handle_restart() -> None:
        if on_restart:
            on_restart()

    def handle_later() -> None:
        if on_later:
            on_later()

    callbacks = {
        "on_restart": handle_restart,
        "on_later": handle_later,
    }

    if tk is None:
        return callbacks

    try:
        should_destroy_root = False
        if root is None:
            root = tk.Tk()
            root.title("Download Folder Updated")
            should_destroy_root = True
        else:
            root.title("Download Folder Updated")

        root.geometry("480x210")
        root.minsize(440, 190)
        root.configure(bg="#1e1e24")

        frame = tk.Frame(root, bg="#1e1e24", padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        title_lbl = tk.Label(
            frame,
            text="Download Folder Updated",
            font=("Arial", 12, "bold"),
            bg="#1e1e24",
            fg="#f4f4f5",
            anchor="w",
        )
        title_lbl.pack(fill=tk.X, pady=(0, 8))

        desc_lbl = tk.Label(
            frame,
            text=f"New download directory:\n{new_dir}\n\nRestart LocalTune now to apply this change?",
            font=("Arial", 9),
            bg="#1e1e24",
            fg="#a1a1aa",
            justify=tk.LEFT,
            anchor="w",
        )
        desc_lbl.pack(fill=tk.X, pady=(0, 15))

        btn_frame = tk.Frame(frame, bg="#1e1e24")
        btn_frame.pack(fill=tk.X, pady=(5, 0))

        def btn_restart_click() -> None:
            if should_destroy_root:
                root.destroy()
            handle_restart()

        def btn_later_click() -> None:
            if should_destroy_root:
                root.destroy()
            handle_later()

        restart_btn = tk.Button(
            btn_frame,
            text="⚡ Restart Now",
            font=("Arial", 9, "bold"),
            bg="#10b981",
            fg="white",
            padx=10,
            pady=4,
            command=btn_restart_click,
        )
        restart_btn.pack(side=tk.LEFT, padx=(0, 8))

        later_btn = tk.Button(
            btn_frame,
            text="Later",
            padx=10,
            pady=4,
            command=btn_later_click,
        )
        later_btn.pack(side=tk.RIGHT)

        if should_destroy_root:
            root.mainloop()

    except Exception as e:
        sys.stderr.write(f"Failed to display restart dialog: {e}\n")

    return callbacks


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
            import signal

            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        except Exception:
            pass
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

    cmd = [
        python_exe,
        "-m", "uvicorn",
        "app.main:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--app-dir", project_dir,
    ]
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
            if p.stdout:
                for line in iter(p.stdout.readline, ""):
                    try:
                        with open(path, "a", encoding="utf-8") as f:
                            f.write(line)
                    except Exception:
                        pass
        except Exception:
            pass

    thread = threading.Thread(target=log_streamer, args=(proc, log_file), daemon=True)
    thread.start()

    return proc, thread


def wait_for_backend_health(
    url: str = "http://127.0.0.1:8000",
    timeout: float = 25.0,
    interval: float = 0.25,
    proc: subprocess.Popen[Any] | None = None,
    pump_callback: Callable[[], None] | None = None,
) -> bool:
    """Polls backend URL until it responds with HTTP or timeout/process exit occurs."""
    deadline = time.time() + timeout
    probe_timeout = min(1.0, max(0.05, interval * 2))
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LocalTune-Launcher"})
            with urllib.request.urlopen(req, timeout=probe_timeout) as resp:
                status = getattr(resp, "status", getattr(resp, "code", None))
                if status in (200, 204, 301, 302, 307, 308):
                    return True
        except urllib.error.HTTPError:
            # Any HTTP status response means backend is accepting requests
            return True
        except Exception:
            pass
        remaining = deadline - time.time()
        if remaining <= 0:
            break

        # Sleep in small slices while pumping the splash screen event loop
        slice_end = time.time() + min(interval, remaining)
        while time.time() < slice_end:
            if pump_callback is not None:
                pump_callback()
            time.sleep(0.02)
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
        cleanup_old_executables(self.project_dir)

    def acquire_lock(self) -> bool:
        return self.lock.acquire()

    def check_and_prompt_updates(self, pump_callback: Callable[[], None] | None = None) -> bool:
        """Checks GitHub for newer version, prompts user if found, and handles selection."""
        cleanup_old_executables(self.project_dir)

        info: dict[str, Any] | None = None
        done = threading.Event()

        def fetch() -> None:
            nonlocal info
            try:
                info = check_github_release(timeout=3.0)
            except Exception:
                info = None
            finally:
                done.set()

        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()

        deadline = time.time() + 3.5
        while not done.is_set() and time.time() < deadline:
            if pump_callback is not None:
                pump_callback()
            time.sleep(0.02)

        if not info:
            return True

        remote_tag = str(info.get("tag_name", ""))
        local_tag = get_local_version(self.project_dir)

        if not remote_tag or not is_newer_version(remote_tag, local_tag):
            return True

        release_url = str(info.get("html_url", "https://github.com/Reimaris/LocalTune/releases"))
        download_url = get_release_zip_url(info)

        user_choice = {"action": "continue"}

        def on_update() -> None:
            if download_url:
                success = download_and_apply_update(download_url, self.project_dir)
                if success:
                    user_choice["action"] = "relaunch"
                    python_exe = get_python_executable(self.project_dir) or sys.executable
                    launcher_target = sys.executable if getattr(sys, "frozen", False) else python_exe
                    args = [launcher_target] if getattr(sys, "frozen", False) else [launcher_target, os.path.abspath(__file__)]
                    try:
                        subprocess.Popen(args, cwd=self.project_dir, creationflags=SUBPROCESS_CREATIONFLAGS)
                    except Exception:
                        pass
                    sys.exit(0)
            else:
                webbrowser.open(release_url)

        def on_manual() -> None:
            webbrowser.open(release_url)
            user_choice["action"] = "continue"

        def on_skip() -> None:
            user_choice["action"] = "continue"

        show_update_dialog(
            remote_tag=remote_tag,
            local_tag=local_tag,
            release_url=release_url,
            download_url=download_url,
            on_update=on_update,
            on_manual=on_manual,
            on_skip=on_skip,
        )

        return user_choice["action"] == "continue"

    def start(self, open_browser: bool = True, check_updates: bool = True) -> bool:
        """Starts the backend, performs health check, and opens browser.

        On first-instance launch, a SplashScreen is displayed immediately and
        updated with real-time status text throughout the startup sequence.
        On secondary-instance launch, the splash is skipped entirely and the
        existing dashboard is focused in the browser.
        """
        if not self.acquire_lock():
            # Secondary instance — open browser on existing session, no splash
            self.lock.handle_existing_instance(f"http://127.0.0.1:{self.port}")
            return False

        # Show splash immediately for first instance
        version = get_local_version(self.project_dir)
        splash = SplashScreen(title="LocalTune", version=version)
        start_time = time.time()

        try:
            splash.update_status("Checking for updates...")
            if check_updates and not self.check_and_prompt_updates(pump_callback=splash.pump):
                splash.close()
                return False

            splash.update_status("Starting backend server...")
            try:
                self.backend_proc, self.log_thread = spawn_backend_process(
                    project_dir=self.project_dir,
                    port=self.port,
                    log_file=self.log_file,
                )
            except Exception as e:
                splash.close()
                show_startup_error_dialog(f"Failed to spawn backend process: {e}", self.log_file)
                self.stop()
                return False

            # Health probe (up to 25s for cold start and initial migrations)
            splash.update_status("Waiting for backend readiness...")
            healthy = wait_for_backend_health(
                url=f"http://127.0.0.1:{self.port}",
                timeout=25.0,
                interval=0.25,
                proc=self.backend_proc,
                pump_callback=splash.pump,
            )

            if not healthy:
                # Flush pending log output before reading — eliminates blank error dialogs
                if self.log_thread and self.log_thread.is_alive():
                    self.log_thread.join(timeout=1.0)
                logs = read_recent_logs(self.log_file)
                splash.close()
                show_startup_error_dialog(logs, self.log_file)
                self.stop()
                return False

            # Ensure minimum splash display time of 1.0s for polished branding
            min_duration = 1.0
            elapsed = time.time() - start_time
            if elapsed < min_duration:
                splash.update_status("Opening dashboard...")
                end_time = start_time + min_duration
                while time.time() < end_time:
                    splash.pump()
                    time.sleep(0.02)
            else:
                splash.update_status("Opening dashboard...")
                splash.pump()

            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{self.port}")

        finally:
            splash.close()

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
        downloads_dir = get_effective_download_dir(self.project_dir)
        open_file_or_folder(downloads_dir)

    def restart_backend(self, pump_callback: Callable[[], None] | None = None) -> bool:
        """Cleanly terminates the running backend process and restarts it with updated environment."""
        if self.backend_proc:
            kill_process_tree(self.backend_proc)
            self.backend_proc = None
        if self.log_thread and self.log_thread.is_alive():
            try:
                self.log_thread.join(timeout=1.0)
            except Exception:
                pass
        try:
            self.backend_proc, self.log_thread = spawn_backend_process(
                project_dir=self.project_dir,
                port=self.port,
                log_file=self.log_file,
            )
        except Exception as e:
            show_startup_error_dialog(f"Failed to restart backend process: {e}", self.log_file)
            return False

        return wait_for_backend_health(
            url=f"http://127.0.0.1:{self.port}",
            timeout=15.0,
            interval=0.25,
            proc=self.backend_proc,
            pump_callback=pump_callback,
        )



def load_tray_icon(icon_path: str | None = None) -> Any:
    """Loads icon from icon.ico or icon.png into a PIL Image, or creates fallback image."""
    if Image is None:
        return None

    candidate_paths: list[str] = []
    if icon_path:
        candidate_paths.append(icon_path)

    project_dir = get_project_dir() or get_base_dir()
    candidate_paths.extend(
        [
            get_resource_path("icon.ico"),
            get_resource_path("icon.png"),
            os.path.join(project_dir, "windows_launcher", "icon.ico"),
            os.path.join(project_dir, "windows_launcher", "icon.png"),
            os.path.join(get_base_dir(), "icon.ico"),
            os.path.join(get_base_dir(), "icon.png"),
        ]
    )

    for path in candidate_paths:
        if os.path.exists(path):
            try:
                img = Image.open(path)
                img.load()
                return img
            except Exception:
                pass

    # Fallback emerald green 32x32 image
    return Image.new("RGBA", (32, 32), color=(16, 185, 129, 255))


def build_tray_menu(supervisor: LocalTuneSupervisor) -> Any:
    """Builds the pystray context menu bound to supervisor actions."""
    if pystray is None:
        raise RuntimeError("pystray is required to build the system tray menu.")

    def on_open_dashboard(icon: Any = None, item: Any = None) -> None:
        supervisor.open_dashboard()

    def on_open_downloads(icon: Any = None, item: Any = None) -> None:
        supervisor.open_downloads()

    def on_change_downloads(icon: Any = None, item: Any = None) -> None:
        def worker() -> None:
            if tk is None or filedialog is None:
                return
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            current_dir = get_effective_download_dir(supervisor.project_dir)
            selected = filedialog.askdirectory(
                parent=root,
                title="Select LocalTune Download Folder",
                initialdir=current_dir if os.path.exists(current_dir) else supervisor.project_dir,
            )
            root.destroy()
            if not selected:
                return

            ok, err = validate_directory_writable(selected)
            if not ok:
                if messagebox is not None:
                    err_root = tk.Tk()
                    err_root.withdraw()
                    err_root.attributes("-topmost", True)
                    messagebox.showerror(
                        "Invalid Directory",
                        f"The selected directory cannot be used:\n{err}",
                        parent=err_root,
                    )
                    err_root.destroy()
                return

            cfg = load_launcher_config(supervisor.project_dir)
            cfg["download_dir"] = os.path.abspath(selected)
            save_launcher_config(cfg, supervisor.project_dir)

            if supervisor.backend_proc and supervisor.backend_proc.poll() is None:
                def do_restart() -> None:
                    supervisor.restart_backend()

                show_restart_dialog(selected, on_restart=do_restart)

        threading.Thread(target=worker, daemon=True).start()

    def on_reset_downloads(icon: Any = None, item: Any = None) -> None:
        def worker() -> None:
            cfg = load_launcher_config(supervisor.project_dir)
            if "download_dir" in cfg:
                del cfg["download_dir"]
                save_launcher_config(cfg, supervisor.project_dir)
            default_dir = os.path.join(supervisor.project_dir, "downloads")
            if supervisor.backend_proc and supervisor.backend_proc.poll() is None:
                def do_restart() -> None:
                    supervisor.restart_backend()

                show_restart_dialog(default_dir, on_restart=do_restart)

        threading.Thread(target=worker, daemon=True).start()

    def on_open_logs(icon: Any = None, item: Any = None) -> None:
        supervisor.open_logs()

    def on_quit(icon: Any = None, item: Any = None) -> None:
        if icon is not None and hasattr(icon, "stop"):
            icon.stop()
        supervisor.stop()

    return pystray.Menu(
        pystray.MenuItem("🌐 Open Dashboard", on_open_dashboard, default=True),
        pystray.MenuItem("📁 Open Downloads Folder", on_open_downloads),
        pystray.MenuItem("⚙ Change Downloads Folder...", on_change_downloads),
        pystray.MenuItem("↺ Reset Downloads Folder to Default", on_reset_downloads),
        pystray.MenuItem("📄 View Logs", on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("✕ Quit", on_quit),
    )



class LocalTuneTray:
    """Manages the pystray System Tray icon and event lifecycle."""

    def __init__(
        self,
        supervisor: LocalTuneSupervisor,
        icon_image: Any = None,
    ) -> None:
        self.supervisor = supervisor
        self.icon_image = icon_image or load_tray_icon()
        self.icon: Any = None

    def setup(self) -> Any:
        """Initializes the pystray Icon instance."""
        if pystray is None:
            raise RuntimeError("pystray is required to initialize LocalTuneTray.")

        menu = build_tray_menu(self.supervisor)
        title = f"LocalTune (Running on 127.0.0.1:{self.supervisor.port})"
        self.icon = pystray.Icon(
            name="LocalTune",
            icon=self.icon_image,
            title=title,
            menu=menu,
        )
        return self.icon

    def run(self) -> None:
        """Runs the system tray event loop."""
        if not self.icon:
            self.setup()
        if self.icon and hasattr(self.icon, "run"):
            self.icon.run()

    def stop(self) -> None:
        """Stops the system tray icon and terminates the supervisor."""
        if self.icon and hasattr(self.icon, "stop"):
            try:
                self.icon.stop()
            except Exception:
                pass
        self.supervisor.stop()


def main() -> None:
    supervisor = LocalTuneSupervisor()
    if supervisor.start(open_browser=True):
        if pystray is not None:
            tray = LocalTuneTray(supervisor)
            try:
                tray.run()
            except KeyboardInterrupt:
                tray.stop()
        else:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                supervisor.stop()


if __name__ == "__main__":
    main()
