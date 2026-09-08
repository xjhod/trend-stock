@echo off
chcp 65001 >nul
title 趋势全景 - 股票分析
cd /d "%~dp0"

echo ============================================
echo    趋势全景 · 股票分析（本地服务版）
echo ============================================
echo.

if not exist "app.py" (
    echo [提示] 找不到 app.py，请确认已完整解压压缩包内所有文件。
    echo 当前目录: %~dp0
    pause
    exit /b
)

where python >nul 2>nul
if errorlevel 1 (
    echo [提示] 未检测到 Python。
    echo 请先安装 Python 3.10+ 并勾选 "Add Python to PATH"。
    echo   下载地址: https://www.python.org/downloads/
    pause
    exit /b
)

rem ---- 检查 5000 端口是否被旧实例占用（覆盖更新后旧进程仍跑旧代码）----
set PORT_BUSY=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
    set PORT_BUSY=1
)
if "%PORT_BUSY%"=="1" (
    echo [提示] 检测到旧的服务实例仍在运行（端口5000被占用）。
    echo   覆盖更新后必须结束旧进程，否则打开的还是旧版本。
    set /p yn=是否结束旧实例并重新启动? (y/n): 
    if /i not "%yn%"=="y" (
        echo 已取消。请先运行 stop.bat 再启动。
        pause
        exit /b
    )
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000" ^| findstr "LISTENING"') do (
        taskkill /f /pid %%a >nul 2>nul
    )
    echo 旧实例已结束。
    timeout /t 1 >nul
)

echo [1/3] 检查运行环境...
python -c "import flask, requests, pandas, numpy, waitress" >nul 2>nul
if errorlevel 1 (
    echo [2/3] 首次运行，正在安装依赖（约1-2分钟，请耐心等待）...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [提示] 依赖安装失败，请检查网络后重试。
        pause
        exit /b
    )
)

echo [3/3] 正在启动服务，并自动打开浏览器...
echo.
echo    如果浏览器没有自动打开，请手动访问:  http://127.0.0.1:5000
echo    关闭本窗口 = 关闭软件
echo ============================================
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:5000"
python app.py
pause
