@echo off
title Trend Panorama - Stop
echo ================================================
echo   Trend Panorama - Stop all Python processes
echo ================================================
echo.
echo   This will kill all python.exe / pythonw.exe
echo   processes on this PC.
echo   (Only use if Trend Panorama is the only Python
echo    program running on this machine.)
echo.
set /p yn="Confirm kill all Python processes? (y/n): "
if /i not "%yn%"=="y" (
    echo Cancelled.
    pause
    exit /b
)
echo.
echo Killing Python processes...
taskkill /f /im python.exe >nul 2>nul
taskkill /f /im pythonw.exe >nul 2>nul
echo Done. Please restart with "start.bat".
pause
