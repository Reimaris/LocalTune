import os
import sys
import shutil
import subprocess
import webbrowser
import threading
import queue
import customtkinter as ctk

# Configure CustomTkinter aesthetic
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


def get_base_dir() -> str:
    """Return directory containing the executable or script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_project_dir() -> str | None:
    """Locate the LocalTune project root containing compose.yaml or docker-compose.yml."""
    base = get_base_dir()
    for filename in ("compose.yaml", "docker-compose.yml"):
        if os.path.exists(os.path.join(base, filename)):
            return base
        subfolder = os.path.join(base, "LocalTune")
        if os.path.exists(os.path.join(subfolder, filename)):
            return subfolder
    return None


class ManagerApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("LocalTune Manager")
        self.geometry("640x580")
        self.minsize(580, 480)

        self.q: queue.Queue = queue.Queue()
        self.is_running_container: bool = False
        self.is_processing_cmd: bool = False

        # Main Layout Container
        self.main_frame = ctk.CTkFrame(self, corner_radius=12)
        self.main_frame.pack(fill="both", expand=True, padx=15, pady=15)

        # Title Label
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
        self.check_dependencies_and_render()

    def process_queue(self):
        try:
            while True:
                tag, msg = self.q.get_nowait()
                self.console.configure(state="normal")
                self.console.insert("end", msg)
                self.console.see("end")
                self.console.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.process_queue)

    def log(self, message: str):
        self.q.put(("info", message + "\n"))

    def check_dependencies_and_render(self):
        has_git = shutil.which("git") is not None
        has_docker = shutil.which("docker") is not None

        # Clear existing button widgets
        for widget in self.button_frame.winfo_children():
            widget.destroy()

        if not has_git or not has_docker:
            self.log("⚠️ Missing dependencies detected!")
            if not has_git:
                self.log(" -> Git is not installed or not in PATH.")
            if not has_docker:
                self.log(" -> Docker is not installed or not in PATH.")

            warn_lbl = ctk.CTkLabel(
                self.button_frame,
                text="Missing required system dependencies for LocalTune.",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#ff5555",
            )
            warn_lbl.grid(row=0, column=0, columnspan=2, pady=5)

            if not has_git:
                ctk.CTkButton(
                    self.button_frame,
                    text="Download Git",
                    fg_color="#3b82f6",
                    hover_color="#2563eb",
                    command=lambda: webbrowser.open("https://git-scm.com/downloads"),
                ).grid(row=1, column=0, padx=5, pady=5)
            if not has_docker:
                ctk.CTkButton(
                    self.button_frame,
                    text="Download Docker",
                    fg_color="#3b82f6",
                    hover_color="#2563eb",
                    command=lambda: webbrowser.open(
                        "https://www.docker.com/products/docker-desktop/"
                    ),
                ).grid(row=1, column=1, padx=5, pady=5)

            ctk.CTkButton(
                self.button_frame,
                text="Refresh Dependencies",
                fg_color="#4b5563",
                hover_color="#374151",
                command=self.check_dependencies_and_render,
            ).grid(row=2, column=0, columnspan=2, pady=10)
            return

        project_dir = get_project_dir()
        if not project_dir:
            self.log("LocalTune repository is not installed in current folder.")
            ctk.CTkButton(
                self.button_frame,
                text="Install LocalTune",
                font=ctk.CTkFont(size=14, weight="bold"),
                fg_color="#10b981",
                hover_color="#059669",
                command=self.install_localtune,
            ).pack(pady=10)
        else:
            self.button_frame.grid_columnconfigure((0, 1, 2), weight=1)

            # Dynamic Toggle Button for Docker Container State
            if self.is_running_container:
                self.toggle_btn = ctk.CTkButton(
                    self.button_frame,
                    text="⏹️ Stop LocalTune",
                    font=ctk.CTkFont(weight="bold"),
                    fg_color="#ef4444",
                    hover_color="#dc2626",
                    command=self.toggle_docker,
                )
            else:
                self.toggle_btn = ctk.CTkButton(
                    self.button_frame,
                    text="▶️ Start LocalTune",
                    font=ctk.CTkFont(weight="bold"),
                    fg_color="#10b981",
                    hover_color="#059669",
                    command=self.toggle_docker,
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

            # Async container status update in background
            if not self.is_processing_cmd:
                threading.Thread(
                    target=self.detect_container_status, daemon=True
                ).start()

    def detect_container_status(self):
        """Asynchronously check if LocalTune Docker container is currently active."""
        project_dir = get_project_dir()
        if not project_dir:
            return

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            result = subprocess.run(
                ["docker", "compose", "ps", "--services", "--filter", "status=running"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                creationflags=creationflags,
            )
            is_running = bool(result.stdout and result.stdout.strip())
            if is_running != self.is_running_container:
                self.is_running_container = is_running
                self.after(0, self.check_dependencies_and_render)
        except Exception:
            pass

    def run_command(
        self,
        cmd_list: list[str],
        cwd: str | None = None,
        success_msg: str = "Completed successfully.",
        on_complete_callback=None,
    ):
        if cwd is None:
            cwd = get_project_dir() or get_base_dir()

        def target():
            self.is_processing_cmd = True
            self.status_var.set("Running command...")
            self.log(f"\n> {' '.join(cmd_list)}")
            try:
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                process = subprocess.Popen(
                    cmd_list,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )

                if process.stdout:
                    for line in process.stdout:
                        self.q.put(("cmd", line))

                process.wait()
                if process.returncode == 0:
                    self.log(success_msg)
                else:
                    self.log(f"Process exited with code {process.returncode}")
            except Exception as e:
                self.log(f"Error executing command: {e}")
            finally:
                self.is_processing_cmd = False
                self.status_var.set("Ready")
                if on_complete_callback:
                    on_complete_callback()
                self.after(0, self.check_dependencies_and_render)

        threading.Thread(target=target, daemon=True).start()

    def install_localtune(self):
        base = get_base_dir()
        self.run_command(
            ["git", "clone", "https://github.com/Reimaris/LocalTune.git"],
            cwd=base,
            success_msg="Installation complete! You can now start LocalTune.",
        )

    def toggle_docker(self):
        if self.is_running_container:
            self.log("Stopping LocalTune container...")
            self.run_command(
                ["docker", "compose", "down"],
                success_msg="LocalTune container stopped.",
                on_complete_callback=lambda: setattr(
                    self, "is_running_container", False
                ),
            )
        else:
            self.log("Starting LocalTune container...")
            self.run_command(
                ["docker", "compose", "up", "-d"],
                success_msg="LocalTune container started successfully!",
                on_complete_callback=lambda: setattr(
                    self, "is_running_container", True
                ),
            )

    def open_dashboard(self):
        port = os.environ.get("LOCALTUNE_PORT", "").strip()
        if not port or not port.isdigit():
            port = "8000"
        url = f"http://127.0.0.1:{port}"
        self.log(f"Opening {url} in browser...")
        webbrowser.open(url)

    def open_folder(self, folder_path: str):
        project_dir = get_project_dir()
        if not project_dir:
            return

        parts = folder_path.split("/")
        target_path = os.path.join(project_dir, *parts)

        if not os.path.exists(target_path):
            try:
                os.makedirs(target_path, exist_ok=True)
            except Exception as e:
                self.log(f"Error creating directory: {e}")
                return

        try:
            if os.name == "nt":
                os.startfile(target_path)
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.call([opener, target_path])
            self.log(f"Opened folder: {target_path}")
        except Exception as e:
            self.log(f"Failed to open folder: {e}")

    def update_localtune(self):
        def target():
            self.is_processing_cmd = True
            self.status_var.set("Updating...")
            cwd = get_project_dir()
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

            env = os.environ.copy()
            env["BUILDKIT_PROGRESS"] = "plain"

            try:
                self.log("\n> git pull")
                p1 = subprocess.Popen(
                    ["git", "pull"],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                if p1.stdout:
                    for line in p1.stdout:
                        self.q.put(("cmd", line))
                p1.wait()

                self.log("\n> docker compose pull")
                p2 = subprocess.Popen(
                    ["docker", "compose", "pull"],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    creationflags=creationflags,
                )
                if p2.stdout:
                    for line in p2.stdout:
                        self.q.put(("cmd", line))
                p2.wait()

                self.log("\n> docker compose up -d")
                p3 = subprocess.Popen(
                    ["docker", "compose", "up", "-d"],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    creationflags=creationflags,
                )
                if p3.stdout:
                    for line in p3.stdout:
                        self.q.put(("cmd", line))
                p3.wait()

                self.is_running_container = True
                self.log("LocalTune update completed successfully!")
            except Exception as e:
                self.log(f"Error during update: {e}")
            finally:
                self.is_processing_cmd = False
                self.status_var.set("Ready")
                self.after(0, self.check_dependencies_and_render)

        threading.Thread(target=target, daemon=True).start()


def main():
    app = ManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
