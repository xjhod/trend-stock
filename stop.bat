@echo off
title 趋势全景 - 停止服务
echo ================================================
echo   趋势全景 - 停止本地服务（只停本软件）
echo ================================================
echo.
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    taskkill /f /pid %%a >nul 2>nul
    set FOUND=1
)
if "%FOUND%"=="0" (
    echo 未检测到正在运行的服务（端口5000未被占用）。
) else (
    echo 服务已停止。
)
echo.
echo 提示: 如浏览器窗口未自动关闭，请手动关闭。
pause
