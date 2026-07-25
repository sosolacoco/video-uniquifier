@echo off
cd /d "%~dp0"

echo ============================================
echo  Building autonomous VideoUniquifier.exe
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

%PY% build_exe.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo Result: dist\VideoUniquifier.exe
pause
