import os
import sys
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

def get_base_dir():
    """
    Returns the root directory of the LocalTune project.
    If running as a compiled PyInstaller executable, it returns the folder where the .exe is.
    If running as a script, it assumes it is inside a subdirectory (e.g. windows_manager) 
    and returns the parent directory.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def run_command_in_new_window(cmd_str):
    """
    Runs a shell command in a new command prompt window on Windows, 
    so the user can see the output. It pauses at the end.
    """
    try:
        # 'start cmd /c' opens a new terminal window on Windows.
        if os.name == 'nt':
            full_cmd = f'start cmd /c "{cmd_str} && echo. && echo Press any key to close... && pause > nul"'
            subprocess.Popen(full_cmd, shell=True)
        else:
            # Fallback for testing on Linux/Mac
            subprocess.Popen(cmd_str, shell=True)
    except Exception as e:
        messagebox.showerror("Error", f"Failed to run command:\n{e}")

def open_dashboard():
    webbrowser.open("http://localhost:8001")

def open_folder(folder_path):
    base_dir = get_base_dir()
    
    # Handle nested paths cross-platform safely
    parts = folder_path.split('/')
    target_path = os.path.join(base_dir, *parts)
    
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
    
    # Try to make it look a bit more modern using ttk themes
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
    
    # Web UI
    ttk.Button(main_frame, text="Open Dashboard", command=open_dashboard, **btn_style).pack(pady=6)
    
    ttk.Separator(main_frame, orient='horizontal').pack(fill='x', pady=15)
    
    # Folders
    ttk.Button(main_frame, text="Open Downloads Folder", command=lambda: open_folder("downloads"), **btn_style).pack(pady=6)
    ttk.Button(main_frame, text="Open Logs Folder", command=lambda: open_folder("config/logs"), **btn_style).pack(pady=6)
    
    ttk.Separator(main_frame, orient='horizontal').pack(fill='x', pady=15)
    
    # Docker Controls
    ttk.Button(main_frame, text="Start Docker", command=start_docker, **btn_style).pack(pady=6)
    ttk.Button(main_frame, text="Stop Docker", command=stop_docker, **btn_style).pack(pady=6)
    ttk.Button(main_frame, text="Update LocalTune", command=update_localtune, **btn_style).pack(pady=6)
    
    # Status / Helper label
    status_label = ttk.Label(main_frame, text="Ensure Docker Desktop is running before starting.", font=("Segoe UI", 9, "italic"), foreground="gray")
    status_label.pack(side=tk.BOTTOM, pady=(15, 0))
    
    root.mainloop()

if __name__ == "__main__":
    main()
