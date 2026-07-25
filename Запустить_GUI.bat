@echo off
cd /d "%~dp0"

REM Try 'python', then 'py' launcher.
where python >nul 2>nul
if %errorlevel%==0 (
    python video_uniquifier_gui.py
    goto end
)
where py >nul 2>nul
if %errorlevel%==0 (
    py video_uniquifier_gui.py
    goto end
)

echo.
echo [ERROR] Python not found in PATH.
echo Install Python from https://www.python.org/downloads/
echo and enable "Add Python to PATH" during setup.
echo.
pause

:end
if %errorlevel% neq 0 (
    echo.
    echo Something went wrong. See the message above.
    pause
)
