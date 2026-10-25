@echo off
title Smart Attendance - Direct Desktop Camera Monitor
cd /d "%~dp0"
echo ============================================================
echo   SMART ATTENDANCE - DIRECT DESKTOP CAMERA MONITOR
echo ============================================================
echo.
echo Connecting directly to CCTV camera...
echo (Press ESC or Q in the camera window to close)
echo.
.\venv\Scripts\python.exe attendance.py
pause
