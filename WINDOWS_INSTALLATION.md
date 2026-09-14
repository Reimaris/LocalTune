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

## Step 3: Run the Manager

1. Open the extracted folder and double-click **`LocalTune_Manager.exe`**.
2. *Note:* If Windows Defender SmartScreen displays a *"Windows protected your PC"* notification (standard for independent open-source executables), click **More info** -> **Run anyway**.
3. The dark-themed **LocalTune Manager** window will appear.
4. Click **▶️ Start LocalTune**. The status bar will show *"Running (Port 8000)"* and live logs will appear in the console box.
5. Click **🌐 Open Dashboard** to launch the web interface in your browser (`http://127.0.0.1:8000`) and begin syncing your favorite playlists!

---

## Managing Your Downloads and Folders

- **Open Downloads:** Click **📁 Open Downloads** in the Manager to immediately open the local folder where your downloaded music and videos are stored.
- **Open Logs:** Click **📄 Open Logs** to view diagnostic server logs.
- **Stopping LocalTune:** Click **⏹️ Stop LocalTune** or simply close the Manager window (✕)—the backend process terminates cleanly with zero lingering background processes.

---

## Updates

When a new version is released:
1. Click **🔄 Update LocalTune** in the Manager (or accept the update prompt on startup).
2. Click **⚡ Auto-Update** to automatically apply the update in-place without losing your database, configuration tokens, or downloaded songs.
