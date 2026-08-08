import os
import sys
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

def get_base_dir():
    """
    Returns the directory where the executable (or script) is located.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_project_dir():
    """
    Returns the LocalTune project directory if docker-compose.yml is found.
    Checks the current directory, then checks if there's a LocalTune subfolder.
    """
    base = get_base_dir()
    if os.path.exists(os.path.join(base, "docker-compose.yml")):
        return base
    if os.path.exists(os.path.join(base, "LocalTune", "docker-compose.yml")):
        return os.path.join(base, "LocalTune")
    return None

def run_command_in_new_window(cmd_str, cwd=None):
    """
    Runs a shell command in a new command prompt window on Windows.
    """
    if cwd is None:
        cwd = get_project_dir() or get_base_dir()
        
    try:
        if os.name == 'nt':
            # cd /d safely changes drive and directory
            full_cmd = f'start cmd /c "cd /d "{cwd}" && {cmd_str} && echo. && echo Press any key to close... && pause > nul"'
            subprocess.Popen(full_cmd, shell=True)
        else:
            subprocess.Popen(cmd_str, shell=True, cwd=cwd)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to run command:\n{e}")

def install_localtune():
    base = get_base_dir()
    cmd = "git clone https://github.com/Reimaris/LocalTune.git"
    run_command_in_new_window(cmd, cwd=base)
    messagebox.showinfo("Installation Started", "A terminal window has opened to download LocalTune.\n\nPlease wait for the download to finish, then restart this Manager to use it!")
    sys.exit(0)

def open_dashboard():
    webbrowser.open("http://localhost:8001")

def open_folder(folder_path):
    project_dir = get_project_dir()
    if not project_dir:
        return
        
    parts = folder_path.split('/')
    target_path = os.path.join(project_dir, *parts)
    
    if not os.path.exists(target_path):
        try:
            os.makedirs(target_path, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Could not create folder '{folder_path}':\n{e}")
            return
            
    try:
        if os.name == 'nt':
            os.startfile(target_path)
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.call([opener, target_path])
    except Exception as e:
        messagebox.showerror("Error", f"Failed to open folder:\n{e}")

def start_docker():
    run_command_in_new_window("docker compose up -d")

def stop_docker():
    run_command_in_new_window("docker compose down")

def update_localtune():
    run_command_in_new_window("git pull && docker compose up --build -d")

def main():
    root = tk.Tk()
    root.title("LocalTune Manager")
    root.geometry("380x480")
    root.resizable(False, False)
    
    style = ttk.Style()
    available_themes = style.theme_names()
    if 'vista' in available_themes:
        style.theme_use('vista')
    elif 'xpnative' in available_themes:
        style.theme_use('xpnative')
    elif 'clam' in available_themes:
        style.theme_use('clam')
        
    main_frame = ttk.Frame(root, padding="25 25 25 25")
    main_frame.pack(fill=tk.BOTH, expand=True)
    
    title_label = ttk.Label(main_frame, text="LocalTune Manager", font=("Segoe UI", 18, "bold"))
    title_label.pack(pady=(0, 25))
    
    btn_style = {"padding": 8, "width": 30}
    project_dir = get_project_dir()
    
    if not project_dir:
        # --- Install Mode ---
        msg = "LocalTune is not installed here.\n\nClick below to download it\ninto this folder."
        ttk.Label(main_frame, text=msg, justify="center").pack(pady=30)
        
        ttk.Button(main_frame, text="Install LocalTune", command=install_localtune, **btn_style).pack(pady=20)
        
        status_label = ttk.Label(main_frame, text="Requires Git to be installed.", font=("Segoe UI", 9, "italic"), foreground="gray")
        status_label.pack(side=tk.BOTTOM, pady=(15, 0))
    else:
        # --- Normal Mode ---
        ttk.Button(main_frame, text="Open Dashboard", command=open_dashboard, **btn_style).pack(pady=6)
        ttk.Separator(main_frame, orient='horizontal').pack(fill='x', pady=15)
        
        ttk.Button(main_frame, text="Open Downloads Folder", command=lambda: open_folder("downloads"), **btn_style).pack(pady=6)
        ttk.Button(main_frame, text="Open Logs Folder", command=lambda: open_folder("config/logs"), **btn_style).pack(pady=6)
        
        ttk.Separator(main_frame, orient='horizontal').pack(fill='x', pady=15)
        
        ttk.Button(main_frame, text="Start Docker", command=start_docker, **btn_style).pack(pady=6)
        ttk.Button(main_frame, text="Stop Docker", command=stop_docker, **btn_style).pack(pady=6)
        ttk.Button(main_frame, text="Update LocalTune", command=update_localtune, **btn_style).pack(pady=6)
        
        status_label = ttk.Label(main_frame, text="Ensure Docker Desktop is running before starting.", font=("Segoe UI", 9, "italic"), foreground="gray")
        status_label.pack(side=tk.BOTTOM, pady=(15, 0))
    
    root.mainloop()

if __name__ == "__main__":
    main()
