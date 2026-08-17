@echo off
echo ===================================================
echo   LocalTune Manager - Windows Executable Builder
echo ===================================================
echo.

echo 1. Checking for Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH!
    echo Please install Python from python.org and make sure to check "Add Python to PATH".
    pause
    exit /b
)

echo 2. Creating temporary virtual environment...
python -m venv venv_build
call venv_build\Scripts\activate.bat

echo 3. Installing dependencies (PyInstaller and CustomTkinter)...
pip install -r requirements.txt >nul

echo 4. Compiling standalone single-file executable...
:: Compile with windowed mode (no console), onefile, and collect customtkinter assets
pyinstaller --noconfirm --onefile --windowed --collect-all customtkinter --name "LocalTune_Manager" localtune_manager.py

echo 5. Cleaning up...
if exist dist\LocalTune_Manager.exe (
    move /y dist\LocalTune_Manager.exe ..\LocalTune_Manager.exe >nul
)
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist LocalTune_Manager.spec del LocalTune_Manager.spec
call deactivate
if exist venv_build rmdir /s /q venv_build

echo.
echo ===================================================
echo   Success! 
echo   LocalTune_Manager.exe has been created in the main directory.
echo   You can now double-click it to start the manager.
echo ===================================================
pause
