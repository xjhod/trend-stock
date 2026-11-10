@echo off
title 趋势全景 - 一键更新
cd /d "%~dp0"

echo ============================================
echo    趋势全景 . 一键更新（保留数据与设置）
echo ============================================
echo.
echo 更新前请先关闭：黑色命令行窗口 + 浏览器页面
echo 你的自选股、持仓、bt_data 历史数据都会保留
echo.
pause

set "PKG=update_pkg.zip"
set "TMP=update_tmp"

echo.
echo [0/4] 清理上次临时文件...
if exist "%PKG%" del /f /q "%PKG%"
if exist "%TMP%" rmdir /s /q "%TMP%"

echo.
echo [1/4] 下载更新包（自动尝试多个源）...
curl -L --connect-timeout 15 --max-time 300 -o "%PKG%" "https://aka.doubaocdn.com/s/rGV9svlsEO" 2>nul
if exist "%PKG%" for %%A in ("%PKG%") do if %%~zA GEQ 20000 goto unpack

echo    源1不可用，尝试加速源...
curl -L --connect-timeout 15 --max-time 120 -o "%PKG%" "https://ghfast.top/https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%PKG%" for %%A in ("%PKG%") do if %%~zA GEQ 20000 goto unpack

echo    加速源不可用，尝试第二个...
curl -L --connect-timeout 15 --max-time 120 -o "%PKG%" "https://gh-proxy.com/https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%PKG%" for %%A in ("%PKG%") do if %%~zA GEQ 20000 goto unpack

echo    尝试 GitHub 直连...
curl -L --connect-timeout 20 --max-time 180 -o "%PKG%" "https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%PKG%" for %%A in ("%PKG%") do if %%~zA GEQ 20000 goto unpack

echo.
echo [失败] 所有下载源都不可用。
echo        请改用手动补丁包（89KB，解压覆盖即可）。
pause
exit /b 1

:unpack
echo [2/4] 解压更新包...
mkdir "%TMP%" 2>nul
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%~dp0%PKG%' -DestinationPath '%~dp0%TMP%' -Force" >nul 2>nul
if not exist "%TMP%\app.py" if not exist "%TMP%\trend-stock-main\app.py" (
    tar -xf "%PKG%" -C "%TMP%" 2>nul
)

set "SRC=%TMP%"
if exist "%TMP%\trend-stock-main\app.py" set "SRC=%TMP%\trend-stock-main"

if not exist "%SRC%\app.py" (
    echo [失败] 压缩包内找不到 app.py，更新中止。
    echo        你的原文件没有被修改。
    pause
    exit /b 1
)

echo [3/4] 复制新文件（跳过数据和配置）...
robocopy "%SRC%" "." /E /XF *.json *.pid *.log *.db /XD bt_data research .git deploy __pycache__ .sessions /NP /R:1 /W:1 >nul

echo.
echo [4/4] 清理临时文件...
if exist "%PKG%" del /f /q "%PKG%"
if exist "%TMP%" rmdir /s /q "%TMP%"
if exist ".app.pid" del /f /q ".app.pid"

echo.
echo ============================================
echo   更新完成！当前版本：
type VERSION
echo.
echo   请双击 start.bat 重新启动
echo   浏览器按 Ctrl+F5 强制刷新
echo ============================================
echo.
pause
