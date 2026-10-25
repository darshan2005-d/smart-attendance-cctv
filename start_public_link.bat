@echo off
title Smart Attendance - Public Web Link (Cloudflare)
echo ============================================================
echo   SMART ATTENDANCE AI - PUBLIC WEB LINK GENERATOR
echo ============================================================
echo.
echo Starting secure public HTTPS link for your local server...
echo (Make sure start_backend.bat is running!)
echo.
"%LOCALAPPDATA%\Programs\cloudflared.exe" tunnel --url http://127.0.0.1:5000
pause
