import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser

try:
    import customtkinter as ctk
    from PIL import Image

    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
except ImportError:
    ctk = None
    Image = None  # type: ignore[assignment]


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
        # Fallback to docker compose file if app/main.py is absent
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


class ManagerApp(ctk.CTk if ctk else object):  # type: ignore[misc]
    def __init__(self):
        if not ctk:
            raise RuntimeError("customtkinter is required to instantiate ManagerApp GUI.")
        super().__init__()

        self.title("LocalTune Manager")
        self.geometry("640x580")
        self.minsize(580, 480)

        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.backend_proc: subprocess.Popen[str] | None = None
        self.is_running: bool = False
        self.is_processing_cmd: bool = False

        # Set Window Icon if available
        icon_ico = get_resource_path("icon.ico")
        if os.path.exists(icon_ico):
            try:
                self.iconbitmap(icon_ico)
            except (OSError, RuntimeError):
                pass

        # Handle window close (Clean Termination Protocol)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Main Layout Container
        self.main_frame = ctk.CTkFrame(self, corner_radius=12)
        self.main_frame.pack(fill="both", expand=True, padx=15, pady=15)

        # Title Label with Logo
        icon_png = get_resource_path("icon.png")
        header_image = None
        if Image and os.path.exists(icon_png):
            try:
                pil_img = Image.open(icon_png)
                header_image = ctk.CTkImage(
                    light_image=pil_img, dark_image=pil_img, size=(26, 26)
                )
            except (OSError, ValueError):
                header_image = None

        if header_image:
            self.title_label = ctk.CTkLabel(
                self.main_frame,
                text="  LocalTune Manager",
                image=header_image,
                compound="left",
                font=ctk.CTkFont(size=20, weight="bold"),
            )
        else:
            self.title_label = ctk.CTkLabel(
                self.main_frame,
                text="🎵 LocalTune Manager",
                font=ctk.CTkFont(size=20, weight="bold"),
            )
        self.title_label.pack(pady=(15, 10))

        # Control Buttons Frame
        self.button_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.button_frame.pack(fill="x", padx=15, pady=5)

        # Console Log Frame
        self.console_frame = ctk.CTkFrame(self.main_frame)
        self.console_frame.pack(fill="both", expand=True, padx=15, pady=10)

        self.console_label = ctk.CTkLabel(
            self.console_frame,
            text="Console Log Output",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        )
        self.console_label.pack(anchor="w", padx=10, pady=(8, 2))

        self.console = ctk.CTkTextbox(
            self.console_frame,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
            activate_scrollbars=True,
        )
        self.console.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.console.configure(state="disabled")

        # Status Bar
        self.status_var = ctk.StringVar(value="Ready")
        self.status_label = ctk.CTkLabel(
            self.main_frame,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=11, slant="italic"),
            text_color="gray",
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=15, pady=(0, 10))

        # Queue Poller
        self.after(100, self.process_queue)

        # Initial Render
        self.render_controls()

    def process_queue(self):
        try:
            while True:
                _tag, msg = self.q.get_nowait()
                self.console.configure(state="normal")
                self.console.insert("end", msg)
                self.console.see("end")
                self.console.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.process_queue)

    def log(self, message: str):
        self.q.put(("info", message + "\n"))

    def is_backend_active(self) -> bool:
        """Returns True if the backend child process is currently alive."""
        return self.backend_proc is not None and self.backend_proc.poll() is None

    def render_controls(self):
        for widget in self.button_frame.winfo_children():
            widget.destroy()

        project_dir = get_project_dir()
        if not project_dir:
            self.log("⚠️ LocalTune source code ('app/main.py') not found in this folder.")
            warn_lbl = ctk.CTkLabel(
                self.button_frame,
                text="LocalTune application source is missing.",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#ff5555",
            )
            warn_lbl.pack(pady=10)
            return

        python_exe = get_python_executable(project_dir)
        if not python_exe:
            self.log("⚠️ Python runtime not found. Ensure 'runtime/' folder or Python is installed.")
            warn_lbl = ctk.CTkLabel(
                self.button_frame,
                text="Python runtime not found.",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#ff5555",
            )
            warn_lbl.pack(pady=10)
            return

        self.button_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Dynamic Start / Stop Toggle Button
        if self.is_backend_active():
            self.toggle_btn = ctk.CTkButton(
                self.button_frame,
                text="⏹️ Stop LocalTune",
                font=ctk.CTkFont(weight="bold"),
                fg_color="#ef4444",
                hover_color="#dc2626",
                command=self.stop_backend,
            )
        else:
            self.toggle_btn = ctk.CTkButton(
                self.button_frame,
                text="▶️ Start LocalTune",
                font=ctk.CTkFont(weight="bold"),
                fg_color="#10b981",
                hover_color="#059669",
                command=self.start_backend,
            )
        self.toggle_btn.grid(row=0, column=0, padx=5, pady=5, sticky="ew")

        # Open Dashboard Button
        ctk.CTkButton(
            self.button_frame,
            text="🌐 Open Dashboard",
            command=self.open_dashboard,
        ).grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        # Update Button
        ctk.CTkButton(
            self.button_frame,
            text="🔄 Update LocalTune",
            fg_color="#8b5cf6",
            hover_color="#7c3aed",
            command=self.update_localtune,
        ).grid(row=0, column=2, padx=5, pady=5, sticky="ew")

        # Open Downloads Button
        ctk.CTkButton(
            self.button_frame,
            text="📁 Open Downloads",
            fg_color="#4b5563",
            hover_color="#374151",
            command=lambda: self.open_folder("downloads"),
        ).grid(row=1, column=0, padx=5, pady=5, sticky="ew")

        # Open Logs Button
        ctk.CTkButton(
            self.button_frame,
            text="📄 Open Logs",
            fg_color="#4b5563",
            hover_color="#374151",
            command=lambda: self.open_folder("config/logs"),
        ).grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        # Refresh / Check Status Button
        ctk.CTkButton(
            self.button_frame,
            text="🔄 Refresh Status",
            fg_color="#4b5563",
            hover_color="#374151",
            command=self.render_controls,
        ).grid(row=1, column=2, padx=5, pady=5, sticky="ew")

    def start_backend(self):
        """Starts the FastAPI uvicorn backend as a managed background child process."""
        if self.is_backend_active():
            return

        project_dir = get_project_dir() or get_base_dir()
        python_exe = get_python_executable(project_dir)
        if not python_exe:
            self.log("Error: Cannot start backend, Python executable not found.")
            return

        port = os.environ.get("LOCALTUNE_PORT", "8000").strip()
        if not port or not port.isdigit():
            port = "8000"

        cmd = [python_exe, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port]
        env = build_backend_env(project_dir)
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

        self.log(f"Starting LocalTune backend on 127.0.0.1:{port}...")
        self.status_var.set("Starting backend...")

        try:
            self.backend_proc = subprocess.Popen(
                cmd,
                cwd=project_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
            )

            # Spawn daemon thread to stream backend stdout/stderr to GUI console
            def log_reader(proc: subprocess.Popen[str]):
                if proc.stdout:
                    for line in iter(proc.stdout.readline, ""):
                        self.q.put(("cmd", line))
                proc.wait()
                self.after(0, self.on_backend_exit)

            threading.Thread(target=log_reader, args=(self.backend_proc,), daemon=True).start()

            self.status_var.set(f"Running (Port {port})")
            self.after(500, self.render_controls)
        except Exception as e:
            self.log(f"Failed to start backend process: {e}")
            self.status_var.set("Error starting backend")
            self.render_controls()

    def stop_backend(self):
        """Gracefully terminates the backend process."""
        if not self.is_backend_active() or not self.backend_proc:
            self.render_controls()
            return

        self.log("Stopping LocalTune backend...")
        self.status_var.set("Stopping...")

        try:
            self.backend_proc.terminate()
            try:
                self.backend_proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.log("Backend did not stop in time, force-killing...")
                self.backend_proc.kill()
            self.log("Backend process stopped.")
        except Exception as e:
            self.log(f"Error stopping backend process: {e}")
        finally:
            self.backend_proc = None
            self.status_var.set("Stopped")
            self.render_controls()

    def on_backend_exit(self):
        """Callback invoked when backend process terminates."""
        self.status_var.set("Stopped")
        self.render_controls()

    def on_close(self):
        """Handles WM_DELETE_WINDOW: cleanly terminates backend process before exiting."""
        if self.backend_proc and self.backend_proc.poll() is None:
            self.status_var.set("Terminating backend...")
            try:
                self.backend_proc.terminate()
                self.backend_proc.wait(timeout=3.0)
            except Exception:
                try:
                    self.backend_proc.kill()
                except Exception:
                    pass
        if hasattr(self, "destroy"):
            self.destroy()

    def open_dashboard(self):
        port = os.environ.get("LOCALTUNE_PORT", "8000").strip()
        if not port or not port.isdigit():
            port = "8000"
        url = f"http://127.0.0.1:{port}"
        self.log(f"Opening {url} in browser...")
        webbrowser.open(url)

    def open_folder(self, folder_path: str):
        project_dir = get_project_dir() or get_base_dir()
        parts = folder_path.split("/")
        target_path = os.path.join(project_dir, *parts)

        if not os.path.exists(target_path):
            try:
                os.makedirs(target_path, exist_ok=True)
            except Exception as e:
                self.log(f"Error creating directory: {e}")
                return

        try:
            if hasattr(os, "startfile"):
                os.startfile(target_path)  # type: ignore[attr-defined]
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.call([opener, target_path])
            self.log(f"Opened folder: {target_path}")
        except Exception as e:
            self.log(f"Failed to open folder: {e}")

    def update_localtune(self):
        self.log("Checking for updates...")
        # Note: Hybrid auto-update flow is implemented in Ticket 03


def main():
    if not ctk:
        print("customtkinter is required to run LocalTune Manager GUI.")
        sys.exit(1)
    app = ManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
