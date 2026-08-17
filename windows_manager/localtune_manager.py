import os
import sys
import shutil
import subprocess
import webbrowser
import threading
import tkinter as tk
from tkinter import ttk
import queue

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_project_dir():
    base = get_base_dir()
    if os.path.exists(os.path.join(base, "docker-compose.yml")):
        return base
    if os.path.exists(os.path.join(base, "LocalTune", "docker-compose.yml")):
        return os.path.join(base, "LocalTune")
    return None

class ManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LocalTune Manager")
        self.root.geometry("600x550")
        self.root.resizable(True, True)
        self.q = queue.Queue()
        
        style = ttk.Style()
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        elif 'clam' in style.theme_names():
            style.theme_use('clam')
            
        self.main_frame = ttk.Frame(root, padding="15")
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        title_label = ttk.Label(self.main_frame, text="LocalTune Manager", font=("Segoe UI", 16, "bold"))
        title_label.pack(pady=(0, 10))
        
        self.button_frame = ttk.Frame(self.main_frame)
        self.button_frame.pack(fill=tk.X, pady=5)
        
        self.console_frame = ttk.Frame(self.main_frame)
        self.console_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.console = tk.Text(self.console_frame, height=15, wrap="word", font=("Consolas", 9), bg="#1e1e1e", fg="#cccccc")
        self.scrollbar = ttk.Scrollbar(self.console_frame, command=self.console.yview)
        self.console.configure(yscrollcommand=self.scrollbar.set)
        
        self.console.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.console.config(state=tk.DISABLED)
        
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(self.main_frame, textvariable=self.status_var, font=("Segoe UI", 9, "italic"), foreground="gray")
        self.status_label.pack(side=tk.BOTTOM, anchor="w")

        self.root.after(100, self.process_queue)
        
        self.check_dependencies_and_render()
        
    def process_queue(self):
        try:
            while True:
                tag, msg = self.q.get_nowait()
                self.console.config(state=tk.NORMAL)
                self.console.insert(tk.END, msg)
                self.console.see(tk.END)
                self.console.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self.process_queue)

    def log(self, message):
        self.q.put(("info", message + "\n"))

    def check_dependencies_and_render(self):
        has_git = shutil.which("git") is not None
        has_docker = shutil.which("docker") is not None
        
        # Clear button frame
        for widget in self.button_frame.winfo_children():
            widget.destroy()

        if not has_git or not has_docker:
            self.log("Missing dependencies detected!")
            if not has_git:
                self.log("-> Git is missing.")
            if not has_docker:
                self.log("-> Docker is missing.")
                
            warn_lbl = ttk.Label(self.button_frame, text="Missing required dependencies for LocalTune.", font=("Segoe UI", 10, "bold"), foreground="red")
            warn_lbl.grid(row=0, column=0, columnspan=2, pady=5)
            
            if not has_git:
                ttk.Button(self.button_frame, text="Download Git", command=lambda: webbrowser.open("https://git-scm.com/downloads")).grid(row=1, column=0, padx=5, pady=5)
            if not has_docker:
                ttk.Button(self.button_frame, text="Download Docker", command=lambda: webbrowser.open("https://www.docker.com/products/docker-desktop/")).grid(row=1, column=1, padx=5, pady=5)
                
            ttk.Button(self.button_frame, text="Refresh", command=self.check_dependencies_and_render).grid(row=2, column=0, columnspan=2, pady=10)
            return

        project_dir = get_project_dir()
        if not project_dir:
            self.log("LocalTune is not installed in the current directory.")
            ttk.Button(self.button_frame, text="Install LocalTune", command=self.install_localtune).grid(row=0, column=0, padx=5, pady=5)
        else:
            ttk.Button(self.button_frame, text="Open Dashboard", command=self.open_dashboard).grid(row=0, column=0, padx=5, pady=5)
            ttk.Button(self.button_frame, text="Start LocalTune", command=self.start_docker).grid(row=0, column=1, padx=5, pady=5)
            ttk.Button(self.button_frame, text="Stop LocalTune", command=self.stop_docker).grid(row=0, column=2, padx=5, pady=5)
            ttk.Button(self.button_frame, text="Update", command=self.update_localtune).grid(row=1, column=0, padx=5, pady=5)
            ttk.Button(self.button_frame, text="Open Downloads", command=lambda: self.open_folder("downloads")).grid(row=1, column=1, padx=5, pady=5)
            ttk.Button(self.button_frame, text="Open Logs", command=lambda: self.open_folder("config/logs")).grid(row=1, column=2, padx=5, pady=5)
            
    def run_command(self, cmd_list, cwd=None, success_msg="Completed successfully."):
        if cwd is None:
            cwd = get_project_dir() or get_base_dir()
            
        def target():
            self.status_var.set("Running command...")
            self.log(f"\n> {' '.join(cmd_list)}")
            try:
                # Use CREATE_NO_WINDOW on Windows to prevent console popup
                creationflags = 0
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW
                    
                process = subprocess.Popen(
                    cmd_list,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags
                )
                
                for line in process.stdout:
                    self.q.put(("cmd", line))
                    
                process.wait()
                if process.returncode == 0:
                    self.log(success_msg)
                else:
                    self.log(f"Process failed with exit code {process.returncode}")
            except Exception as e:
                self.log(f"Error: {e}")
            finally:
                self.status_var.set("Ready")
                # Ensure the UI reflects new state if we just installed
                self.root.after(0, self.check_dependencies_and_render)
                
        threading.Thread(target=target, daemon=True).start()

    def install_localtune(self):
        base = get_base_dir()
        self.run_command(["git", "clone", "https://github.com/Reimaris/LocalTune.git"], cwd=base, success_msg="Installation complete! You can now start LocalTune.")

    def open_dashboard(self):
        self.log("Opening dashboard in browser...")
        webbrowser.open("http://localhost:8001")

    def open_folder(self, folder_path):
        project_dir = get_project_dir()
        if not project_dir:
            return
            
        parts = folder_path.split('/')
        target_path = os.path.join(project_dir, *parts)
        
        if not os.path.exists(target_path):
            try:
                os.makedirs(target_path, exist_ok=True)
            except Exception as e:
                self.log(f"Error creating folder: {e}")
                return
                
        try:
            if os.name == 'nt':
                os.startfile(target_path)
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.call([opener, target_path])
            self.log(f"Opened folder: {target_path}")
        except Exception as e:
            self.log(f"Failed to open folder: {e}")

    def start_docker(self):
        self.run_command(["docker", "compose", "up", "-d"])

    def stop_docker(self):
        self.run_command(["docker", "compose", "down"])

    def update_localtune(self):
        # Using a batch file or chained commands is tricky with list format.
        # We'll run git pull first, then docker compose.
        def target():
            self.status_var.set("Updating...")
            cwd = get_project_dir()
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            
            env = os.environ.copy()
            env["BUILDKIT_PROGRESS"] = "plain"
            
            try:
                self.log("\n> git pull")
                p1 = subprocess.Popen(["git", "pull"], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=creationflags)
                for line in p1.stdout:
                    self.q.put(("cmd", line))
                p1.wait()
                
                self.log("\n> docker compose pull")
                p2 = subprocess.Popen(["docker", "compose", "pull"], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, creationflags=creationflags)
                for line in p2.stdout:
                    self.q.put(("cmd", line))
                p2.wait()
                
                self.log("\n> docker compose up -d")
                p3 = subprocess.Popen(["docker", "compose", "up", "-d"], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, creationflags=creationflags)
                for line in p3.stdout:
                    self.q.put(("cmd", line))
                p3.wait()
                
                self.log("Update completed!")
            except Exception as e:
                self.log(f"Error during update: {e}")
            finally:
                self.status_var.set("Ready")
                
        threading.Thread(target=target, daemon=True).start()

def main():
    root = tk.Tk()
    _app = ManagerApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
