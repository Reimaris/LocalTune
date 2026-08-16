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
Now that your PC is ready, you can get the LocalTune Manager. This is a small program that will handle everything else for you.

1. Go to the [LocalTune Releases](https://github.com/Reimaris/LocalTune/releases) page on GitHub.
2. Download the latest `LocalTuneManager.exe` file.
3. Move `LocalTuneManager.exe` into a new folder where you want your downloads to be saved (e.g., `Documents/LocalTune_App`).

## Step 4: Run the Manager
1. Double-click `LocalTuneManager.exe`.
2. Windows Defender might show a "Windows protected your PC" blue screen because it's a new executable. Click **More info** -> **Run anyway**.
3. In the LocalTune Manager, click **Install LocalTune**.
4. The manager will automatically download the codebase into a `LocalTune` folder right next to the `.exe`. You'll see the progress in the built-in console.
5. Once installation says "Completed successfully", the buttons will update.
6. Click **Start LocalTune**. The first time you do this, Docker will take a few minutes to download the container image.
7. Once finished, click **Open Dashboard** to start downloading music!

## Troubleshooting
- **Manager says "Docker is missing"**: Ensure Docker Desktop is actually open and running in your system tray (bottom right of your screen). Ensure you didn't uncheck adding it to your PATH during installation.
- **Console says "failed to solve"**: Sometimes Docker gets stuck. Restart Docker Desktop and click **Start LocalTune** again.
