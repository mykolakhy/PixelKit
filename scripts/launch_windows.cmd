@echo off
setlocal
set "pixelkit_project=%~dp0.."
if exist "%pixelkit_project%\.venv\Scripts\pythonw.exe" (
    start "" "%pixelkit_project%\.venv\Scripts\pythonw.exe" -m pixelkit %*
) else (
    echo Create .venv and install PixelKit first. See README.md.
    pause
    exit /b 1
)
