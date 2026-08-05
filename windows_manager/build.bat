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

echo 3. Installing pyinstaller...
pip install pyinstaller >nul

echo 4. Compiling the executable...
:: Compile with windowed mode (no console) and onefile
pyinstaller --noconfirm --onefile --windowed --name "LocalTuneManager" localtune_manager.py

echo 5. Cleaning up...
move dist\LocalTuneManager.exe ..\LocalTuneManager.exe >nul
rmdir /s /q build
rmdir /s /q dist
del LocalTuneManager.spec
call deactivate
rmdir /s /q venv_build

echo.
echo ===================================================
echo   Success! 
echo   LocalTuneManager.exe has been created in the main directory.
echo   You can now double-click it to start the manager.
echo ===================================================
pause
