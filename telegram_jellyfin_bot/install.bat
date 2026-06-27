@echo off
setlocal
title Install Telegram Jellyfin Bot
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel% equ 0 (
  set "PYTHON=py"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo ERROR: Python was not found. Install Python 3.10 or newer.
    pause
    exit /b 1
  )
  set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PYTHON% -m venv .venv
  if errorlevel 1 goto failed
)

echo Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed

echo Installing requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

if not exist "config.json" (
  copy /y "config.example.json" "config.json" >nul
  echo Created config.json from the example.
)

if not exist "data" mkdir "data"
if not exist "logs" mkdir "logs"
if not exist "temp" mkdir "temp"
if not exist "tools" mkdir "tools"

echo.
echo Installation complete.
echo 1. Edit config.json.
echo 2. Put telegram-bot-api.exe in tools or set its path in config.json.
echo 3. Run run_local_bot_api.bat.
echo 4. Run run.bat in a second window.
pause
exit /b 0

:failed
echo.
echo Installation failed. Review the error above.
pause
exit /b 1
