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
        echo  Created .env file - add your ANTHROPIC_API_KEY for AI journal generation
    )
)

:: Install dependencies
echo  Installing dependencies...
pip install -r requirements.txt -q

:: Start server
echo.
echo  Starting server...
echo  Dashboard: http://localhost:8000/dashboard/
echo  Press Ctrl+C to stop
echo.
cd backend
python app.py
pause
