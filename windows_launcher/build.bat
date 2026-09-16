@echo off
echo ========================================================
echo Building LocalTune Windows System Tray Launcher (.exe)
echo ========================================================

python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller --noconfirm --onefile --windowed ^
    --icon="icon.ico" ^
    --add-data "icon.ico;." ^
    --add-data "icon.png;." ^
    --hidden-import="tkinter" ^
    --hidden-import="tkinter.ttk" ^
    --name "LocalTune" ^
    localtune.py

echo.
echo Build complete! Executable located at dist\LocalTune.exe
pause
