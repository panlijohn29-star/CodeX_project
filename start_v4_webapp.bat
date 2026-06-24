@echo off
setlocal

cd /d "%~dp0"

echo Checking for an existing web app process on port 5001...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5001" ^| findstr "LISTENING"') do (
    echo Closing old process PID %%P ...
    taskkill /PID %%P /F >nul 2>nul
)

set "PY_CMD="

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=python"
    )
)

if not defined PY_CMD (
    echo Python was not found.
    echo Please install Python 3, then run this file again.
    pause
    exit /b 1
)

echo Starting Closing Report Web App V4...
echo Project folder: %cd%
echo.

start "" http://localhost:5001/login

call %PY_CMD% app_v4.py

if errorlevel 1 (
    echo.
    echo The web app stopped with an error.
    pause
)

endlocal
