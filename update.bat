@echo off
setlocal
title Update Jellyfin Video Manager
cd /d "%~dp0"

echo ==================================================
echo          Jellyfin Video Manager Updater
echo ==================================================
echo.

where git >nul 2>&1
if errorlevel 1 (
  echo ERROR: Git is not installed or is not available in PATH.
  echo Install Git for Windows, then try again.
  goto failed
)

if not exist ".git" (
  echo ERROR: This folder came from a ZIP file and is not a Git clone.
  echo.
  echo Clone the repository once with:
  echo git clone https://github.com/shiny222/vedio_name_manager__bot-telegram.git
  echo.
  echo Then run install.bat once in each project folder.
  goto failed
)

echo Checking for local source-code changes...
for /f "delims=" %%I in ('git status --porcelain --untracked-files=no') do (
  echo ERROR: Tracked files have local changes.
  echo Commit or restore them before updating:
  git status --short
  goto failed
)

echo Downloading the latest code...
git pull --ff-only
if errorlevel 1 goto failed

echo.
echo Checking organizer dependencies...
if exist "organizer\.venv\Scripts\python.exe" (
  "organizer\.venv\Scripts\python.exe" -m pip install -r "organizer\requirements.txt"
  if errorlevel 1 goto failed
) else (
  echo Organizer is not installed. Run organizer\install.bat once if needed.
)

echo.
echo Checking Telegram bot dependencies...
if exist "telegram_jellyfin_bot\.venv\Scripts\python.exe" (
  "telegram_jellyfin_bot\.venv\Scripts\python.exe" -m pip install -r "telegram_jellyfin_bot\requirements.txt"
  if errorlevel 1 goto failed
) else (
  echo Telegram bot is not installed. Run telegram_jellyfin_bot\install.bat once.
)

echo.
echo Update complete.
echo Your config.json, database, logs, and virtual environments were preserved.
echo Restart run_local_bot_api.bat and run.bat to use the new version.
pause
exit /b 0

:failed
echo.
echo Update was not completed. Review the message above.
pause
exit /b 1
