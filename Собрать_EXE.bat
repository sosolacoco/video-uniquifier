@echo off
cd /d "%~dp0"

echo ============================================
echo  Building VideoUniquifier.exe (PyInstaller)
echo ============================================
echo.

set PY=python
where python >nul 2>nul || set PY=py
where %PY% >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH.
    echo Install Python from https://www.python.org/downloads/
    pause
    exit /b 1
)

%PY% -m pip install --upgrade pyinstaller tqdm
if %errorlevel% neq 0 goto err

%PY% -m PyInstaller --onefile --noconsole --name VideoUniquifier --add-data "video_uniquifier.py;." video_uniquifier_gui.py
if %errorlevel% neq 0 goto err

echo.
echo DONE. File is here:  dist\VideoUniquifier.exe
echo NOTE: FFmpeg (ffmpeg/ffprobe) must still be installed in the system
echo       or set its path inside the app fields.
echo.
pause
exit /b 0

:err
echo.
echo [ERROR] Build failed. Make sure Python is installed and internet is available.
pause
exit /b 1
