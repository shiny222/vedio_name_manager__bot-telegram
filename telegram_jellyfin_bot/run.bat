@echo off
setlocal
title Telegram Jellyfin Bot
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" bot.py
set "CODE=%errorlevel%"
echo.
echo Bot stopped with exit code %CODE%.
pause
exit /b %CODE%
