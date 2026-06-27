@echo off
setlocal
title Local Telegram Bot API
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Run install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" local_bot_api_runner.py
set "CODE=%errorlevel%"
echo.
echo Local Bot API stopped with exit code %CODE%.
pause
exit /b %CODE%
