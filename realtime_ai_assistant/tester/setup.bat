@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo   Maria - tester setup
echo ==========================================
echo.

rem --- 1. Find Python 3 ---
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [!] Python 3 was not found.
    echo     Install it from https://www.python.org/downloads/ and tick
    echo     "Add python.exe to PATH" in the installer, then run setup.bat again.
    pause
    exit /b 1
)
echo Using:
%PY% --version
echo.

rem --- 2. Virtual environment ---
if not exist "venv\Scripts\python.exe" (
    echo Creating the virtual environment...
    %PY% -m venv venv
    if errorlevel 1 (
        echo [!] Could not create the virtual environment.
        pause
        exit /b 1
    )
)

rem --- 3. Dependencies ---
echo Installing dependencies. This can take a few minutes...
"venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [!] Installing the dependencies failed. Check your internet connection
    echo     and run setup.bat again.
    pause
    exit /b 1
)

rem --- 4. Roboflow API key ---
echo.
if exist ".env" (
    echo .env already exists, keeping it.
) else (
    echo Maria needs a Roboflow API key. The person who sent you this build
    echo will give you one. Do not share it with anyone else.
    set "KEY="
    set /p "KEY=Paste the key here and press Enter: "
    if defined KEY (
        > ".env" echo ROBOFLOW_API_KEY=!KEY!
        echo Saved to .env
    ) else (
        echo No key entered. Copy .env.example to .env and add the key before running.
    )
)

rem --- 5. Ollama and the llava model ---
echo.
where ollama >nul 2>nul
if errorlevel 1 (
    echo [!] Ollama was not found. Install it from https://ollama.com
    echo     then run:  ollama pull llava:7b
) else (
    ollama list 2>nul | findstr /i "llava" >nul
    if errorlevel 1 (
        echo Ollama is installed but the llava:7b model is missing.
        choice /c YN /m "Download it now, about 4.7 GB"
        if not errorlevel 2 ollama pull llava:7b
    ) else (
        echo Ollama and the llava model were found.
    )
)

echo.
echo ==========================================
echo   Setup finished. Start Maria with run.bat
echo ==========================================
pause
