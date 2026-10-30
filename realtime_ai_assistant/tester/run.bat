@echo off
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Run setup.bat first.
    pause
    exit /b 1
)
if not exist ".env" (
    echo There is no .env file with your Roboflow key. Run setup.bat first.
    pause
    exit /b 1
)

rem Show emoji in the log without encoding errors
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

echo Starting Maria. Switch to Minecraft within 5 seconds.
echo   Ctrl+Alt+G = chat window     Ctrl+Alt+S = status and settings
echo   Close this window or press Ctrl+C to stop.
echo.
"venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo Maria stopped with an error. Please send me the newest file in the logs folder.
    pause
)
