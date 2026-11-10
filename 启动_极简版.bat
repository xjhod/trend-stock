@echo off
cd /d "%~dp0"
title TrendStock Launcher
echo ============================================
echo   TrendStock  -  starting local service
echo ============================================
echo.
echo Working dir: %CD%
echo.
python --version
echo.
echo [INFO] Starting app.py ...  (this window must stay open)
echo ============================================
python app.py
echo.
echo ============================================
echo [ERROR] Service has stopped. See the message above.
echo ============================================
pause
