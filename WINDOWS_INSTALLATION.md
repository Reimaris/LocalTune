# LocalTune: Complete Windows Installation Guide

Setting up LocalTune on Windows requires **zero command line usage, zero programming tools, and no Docker.**

The Windows standalone release comes pre-packaged with its own self-contained runtime environment, including audio processors (`ffmpeg`) and extractors (`yt-dlp` / `spotdl`).

---

## Step 1: Download LocalTune for Windows

1. Navigate to the official [LocalTune Releases](https://github.com/Reimaris/LocalTune/releases) page on GitHub.
2. Under the latest release **Assets**, download:  
   👉 **`LocalTune-Windows-x64.zip`**

---

## Step 2: Extract to Your Preferred Folder

1. Locate the downloaded `LocalTune-Windows-x64.zip` in your Downloads folder.
2. Right-click the zip file and select **Extract All...** (or use 7-Zip / WinRAR).
3. Choose a folder where you want your application and music to be stored (e.g., `Documents\LocalTune` or `D:\LocalTune`).
4. Click **Extract**.

---

## Step 3: Run LocalTune

1. Open the extracted folder and double-click **`LocalTune.exe`**.
2. *Note:* If Windows Defender SmartScreen displays a *"Windows protected your PC"* notification (standard for independent open-source executables), click **More info** -> **Run anyway**.
3. LocalTune automatically boots the backend in the background and opens `http://127.0.0.1:8000` in your default web browser.
4. A LocalTune icon will park in your Windows notification area (System Tray).

---

## System Tray Controls

LocalTune runs unobtrusively in your system tray without cluttering your taskbar. Right-click the LocalTune tray icon at any time to:
- 🌐 **Open Dashboard**: Opens your browser directly to `http://127.0.0.1:8000`.
- 📁 **Open Downloads Folder**: Opens the local folder containing your downloaded music and videos.
- 📄 **View Logs**: Opens `config/logs/localtune.log` in Notepad for quick troubleshooting.
- ✕ **Quit**: Completely stops LocalTune and cleanly terminates all background processes.

*Tip:* Single-clicking or double-clicking the tray icon directly opens the web dashboard. If you accidentally launch `LocalTune.exe` while it is already running, it automatically opens your existing browser tab without crashing or creating duplicate processes.

---

## Automatic Updates

On launch, LocalTune checks GitHub for newer releases:
1. If an update is detected, an update prompt will appear:
   - **⚡ Update & Launch**: Automatically downloads and extracts the update in-place, strictly preserving your database (`config/localtune.db`), settings, and downloaded music (`downloads/`), then restarts the application.
   - **🌐 Manual Update**: Opens the GitHub Releases page in your browser.
   - **Skip Update**: Dismisses the dialog and proceeds immediately with normal launch.
