@echo off
chcp 65001 >nul
title 趋势全景 · 一键停止
echo ================================================
echo    趋势全景 · 一键停止所有Python进程
echo ================================================
echo.
echo  此操作将结束所有 python.exe / pythonw.exe 进程。
echo  仅当趋势全景是您电脑上唯一的 Python 程序时使用！
echo  （如微信开发者工具/其他软件也用Python，请勿使用）
echo.
set /p "yn=确认结束所有Python进程? (y/n): "
if /i not "%yn%"=="y" (
    echo 已取消。
    pause
    exit /b
)
echo.
echo 正在结束 Python 进程...
taskkill /f /im python.exe >nul 2>nul
taskkill /f /im pythonw.exe >nul 2>nul
echo 完成。请重新双击「启动股票分析.bat」打开程序。
pause
