@echo off
title Push Project to GitHub
echo ============================================================
echo      SMART ATTENDANCE SYSTEM - GITHUB DEPLOYMENT
echo ============================================================
echo.
echo Please enter your GitHub repository URL:
echo (Example: https://github.com/username/smart-attendance.git)
echo.
set /p REPO_URL="Repository URL: "
if "%REPO_URL%"=="" (
    echo [ERROR] No URL provided. Aborting.
    pause
    exit /b 1
)

echo.
echo [1/3] Setting remote origin...
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%

echo [2/3] Setting default branch to main...
git branch -M main

echo [3/3] Pushing code to GitHub...
git push -u origin main

if %ERRORLEVEL% equ 0 (
    echo.
    echo ============================================================
    echo [SUCCESS] Your project has been successfully pushed to GitHub!
    echo ============================================================
) else (
    echo.
    echo [NOTE] If prompted, authenticate in the pop-up window or enter your Personal Access Token.
)
echo.
pause
