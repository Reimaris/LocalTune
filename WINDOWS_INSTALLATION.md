# LocalTune: Complete Windows Installation Guide

If you are setting up LocalTune on a fresh Windows machine and have **no idea where to start**, this guide is for you. We will install all required dependencies step-by-step.

## Step 1: Install Git
LocalTune uses `git` to download its source code and retrieve updates easily.

1. Go to the [Git for Windows Download Page](https://git-scm.com/downloads).
2. Download the **64-bit Git for Windows Setup**.
3. Run the installer. You can safely click **Next** through all the prompts and use the default settings.
4. Once completed, Git is installed.

## Step 2: Install Docker Desktop
Docker is what allows LocalTune to run securely in an isolated container without messing with your local Python environment.

1. Go to the [Docker Desktop Download Page](https://www.docker.com/products/docker-desktop/).
2. Download **Docker Desktop for Windows**.
3. Run the installer. **Make sure "Use WSL 2 instead of Hyper-V" is checked** (it usually is by default).
4. Let the installation finish. **You may be prompted to restart your computer.** If so, restart now.
5. After restarting, open **Docker Desktop** from your start menu. 
6. Accept the terms and skip any tutorials. Wait until the status bar in the bottom left says **Engine running**. Docker MUST be running for LocalTune to work.

## Step 3: Download LocalTune Manager
Now that your PC is ready, you can get the LocalTune Manager. This is a standalone dark-themed helper application that handles everything else for you.

1. Go to the [LocalTune Releases](https://github.com/Reimaris/LocalTune/releases) page on GitHub.
2. Download the latest `LocalTune_Manager.exe` single-file executable.
3. Place `LocalTune_Manager.exe` into a folder where you want your application and downloads to be kept (e.g., `Documents/LocalTune`).

## Step 4: Run the Manager
1. Double-click `LocalTune_Manager.exe`.
2. Windows Defender might show a "Windows protected your PC" notification because it is a standalone executable. Click **More info** -> **Run anyway**.
3. In the dark-themed LocalTune Manager, click **Install LocalTune** (if the repository is not present yet).
4. The manager will automatically download the codebase into a `LocalTune` subfolder right next to the `.exe`. You'll see progress in the built-in console.
5. Once installation finishes, click **▶️ Start LocalTune**. The button will switch to **⏹️ Stop LocalTune** while the container is active.
6. Click **🌐 Open Dashboard** to open `http://localhost:8000` in your web browser and start using LocalTune!

## Troubleshooting
- **Manager says "Docker is missing"**: Ensure Docker Desktop is actually open and running in your system tray (bottom right of your screen).
- **Console error during update or start**: Restart Docker Desktop and click **▶️ Start LocalTune** again.
