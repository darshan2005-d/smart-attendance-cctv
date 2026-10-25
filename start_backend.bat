@echo off
setlocal enabledelayedexpansion
title Smart Attendance AI Server Launcher

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo ============================================================
echo   Smart Attendance AI and CCTV Surveillance System
echo ============================================================

set "PYTHON=%PROJECT_DIR%venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [Error] Virtual environment Python not found at:
    echo %PYTHON%
    echo Please make sure the venv folder exists.
    pause
    exit /b 1
)

:: Check if server is already running on port 5000
powershell -NoProfile -Command ^
  "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/health' -UseBasicParsing -TimeoutSec 1; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"

if %ERRORLEVEL% EQU 0 (
    echo [Info] Smart Attendance server is already running!
    echo Opening Dashboard: http://127.0.0.1:5000
    start "" "http://127.0.0.1:5000"
    timeout /t 3 >nul
    exit /b 0
)

echo Starting Backend Server in dedicated console window...
start "Smart Attendance AI Server" "%PYTHON%" "%PROJECT_DIR%backend.py"

echo Waiting for server to initialize...
powershell -NoProfile -Command ^
  "for ($i=0; $i -lt 25; $i++) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"

if %ERRORLEVEL% EQU 0 (
    echo [Success] Server is online!
    echo Opening Dashboard: http://127.0.0.1:5000
    start "" "http://127.0.0.1:5000"
) else (
    echo [Warning] Server took longer than expected to report ready.
    echo Opening dashboard anyway: http://127.0.0.1:5000
    start "" "http://127.0.0.1:5000"
)

timeout /t 4 >nul
endlocal
