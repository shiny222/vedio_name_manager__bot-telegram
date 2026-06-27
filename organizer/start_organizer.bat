@echo off
setlocal EnableExtensions
title Jellyfin TV Series Organizer
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1
    if %errorlevel% equ 0 (
        set "PYTHON=py"
    ) else (
        where python >nul 2>&1
        if %errorlevel% equ 0 (
            set "PYTHON=python"
        ) else (
            echo.
            echo ERROR: Python was not found.
            echo Install Python and enable "Add Python to PATH", then try again.
            echo.
            pause
            exit /b 1
        )
    )
)

:menu
cls
echo ==================================================
echo          Jellyfin TV Series Organizer
echo ==================================================
echo.
echo  1. Dry run ^(preview only - recommended first^)
echo  2. Organize files
echo  3. Undo last batch
echo  4. Undo a specific batch
echo  5. Undo one folder
echo  6. Install or update requirements
echo  0. Exit
echo.
set "CHOICE="
set /p "CHOICE=Choose an option: "

if "%CHOICE%"=="1" goto dryrun
if "%CHOICE%"=="2" goto run
if "%CHOICE%"=="3" goto undolast
if "%CHOICE%"=="4" goto undobatch
if "%CHOICE%"=="5" goto undofolder
if "%CHOICE%"=="6" goto install
if "%CHOICE%"=="0" exit /b 0

echo.
echo Invalid option.
pause
goto menu

:getpaths
echo.
set "SERIES_FOLDER="
echo Select ONE folder named after the anime or TV series.
echo Only video files directly inside that folder will be processed.
echo Season folders will be created inside it.
echo Example: D:\JellyfinLibrary\Breaking Bad
echo.
set /p "SERIES_FOLDER=Anime/series folder: "
if not defined SERIES_FOLDER (
    echo ERROR: Anime/series folder is required.
    pause
    goto menu
)
exit /b 0

:dryrun
call :getpaths
if errorlevel 1 goto menu
echo.
echo Previewing changes...
echo.
%PYTHON% organizer.py dry-run --series-folder "%SERIES_FOLDER%"
goto finished

:run
call :getpaths
if errorlevel 1 goto menu
echo.
echo IMPORTANT: Existing destination files will never be overwritten.
set "CONFIRM="
set /p "CONFIRM=Organize these files now? (Y/N): "
if /i not "%CONFIRM%"=="Y" goto menu
echo.
%PYTHON% organizer.py run --series-folder "%SERIES_FOLDER%"
goto finished

:undolast
echo.
set "LIBRARY="
set /p "LIBRARY=Jellyfin library folder: "
if not defined LIBRARY goto menu
echo.
%PYTHON% organizer.py undo-last --library "%LIBRARY%"
goto finished

:undobatch
echo.
set "LIBRARY="
set "BATCH_ID="
set /p "LIBRARY=Jellyfin library folder: "
set /p "BATCH_ID=Batch ID: "
if not defined LIBRARY goto menu
if not defined BATCH_ID goto menu
echo.
%PYTHON% organizer.py undo-batch "%BATCH_ID%" --library "%LIBRARY%"
goto finished

:undofolder
echo.
set "UNDO_FOLDER="
set /p "UNDO_FOLDER=Folder containing .rename_history.json: "
if not defined UNDO_FOLDER goto menu
echo.
%PYTHON% organizer.py undo-folder "%UNDO_FOLDER%"
goto finished

:install
echo.
echo Installing requirements...
echo.
%PYTHON% -m pip install -r requirements.txt
goto finished

:finished
echo.
echo ==================================================
echo Finished. Exit code: %errorlevel%
echo ==================================================
echo.
pause
goto menu
