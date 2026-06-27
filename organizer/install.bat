@echo off
setlocal
title Install Jellyfin Organizer
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel% equ 0 (
  set "PYTHON=py"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo ERROR: Python was not found. Install Python and enable Add to PATH.
    pause
    exit /b 1
  )
  set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
  %PYTHON% -m venv .venv
  if errorlevel 1 goto failed
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Organizer installation complete.
pause
exit /b 0

:failed
echo.
echo Installation failed. Review the error above.
pause
exit /b 1
