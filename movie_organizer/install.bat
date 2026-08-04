@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Install Python 3.10 or newer.
        pause
        exit /b 1
    )
    set "PYTHON=python"
) else (
    set "PYTHON=py -3"
)

if not exist ".venv\Scripts\python.exe" (
    %PYTHON% -m venv .venv
    if errorlevel 1 goto :error
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Movie organizer installation completed.
pause
exit /b 0

:error
echo.
echo Installation failed. Review the error above.
pause
exit /b 1
