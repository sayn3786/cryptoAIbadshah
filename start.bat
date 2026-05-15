@echo off
title CryptoBadshah AI Analysis
echo.
echo  ============================================
echo   CryptoBadshah AI Analysis Platform
echo  ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo  Download from https://python.org/downloads
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: Create .env if missing
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo  Created .env from .env.example
    )
)

:: Install dependencies
echo  Installing dependencies...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo  ERROR: Failed to install packages. Try running as Administrator.
    pause
    exit /b 1
)

:: Open browser after 4 seconds in background
echo  Opening browser in 4 seconds...
start /b cmd /c "timeout /t 4 /nobreak >nul && start http://localhost:8000/dashboard/"

:: Start server
echo.
echo  ============================================
echo   Server running at localhost:8000
echo   Dashboard: http://localhost:8000/dashboard/
echo   Press Ctrl+C to stop
echo  ============================================
echo.
cd backend
python app.py
pause
