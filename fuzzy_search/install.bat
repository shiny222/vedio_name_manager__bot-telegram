@echo off
setlocal
title Install IMDb Fuzzy Search Tool
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel% equ 0 (set "PYTHON=py") else (set "PYTHON=python")
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python was not found.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" %PYTHON% -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
if not exist "data" mkdir "data"
echo.
echo IMDb fuzzy search tool installed.
echo Test it with:
echo .venv\Scripts\python.exe imdb_tool.py search "dr ston"
pause
exit /b 0
:failed
echo Installation failed.
pause
exit /b 1
