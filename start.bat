@echo off
chcp 65001 >nul
title 趋势全景 · 股票分析
cd /d "%~dp0"

echo ============================================
echo    趋势全景 · 股票分析（本地版）
echo ============================================
echo.

if not exist "app.py" (
    echo [错误] 找不到 app.py，请先完整解压整个压缩包再运行本文件。
    echo 当前目录: %~dp0
    pause
    exit /b
)

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python。
    echo 请先安装 Python 3.10+ 并勾选 "Add Python to PATH"：
    echo   下载地址: https://www.python.org/downloads/
    echo   安装时务必勾选 "Add Python to PATH"，否则无法启动。
    pause
    exit /b
)

echo [1/3] 检查依赖...
python -c "import flask, requests, pandas, numpy" >nul 2>nul
if errorlevel 1 (
    echo [2/3] 首次运行，正在安装依赖（约1-2分钟，请耐心等待）...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
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
