@echo off
chcp 65001 >nul
title 趋势全景 · 一键更新
cd /d "%~dp0"
echo ========================================
echo   趋势全景 · 一键更新（不经过浏览器）
echo   你的自选股/设置/数据都会保留
echo ========================================
echo.

set "ZIP=update_pkg.zip"
del "%ZIP%" 2>nul
rmdir /s /q update_tmp 2>nul

echo [1/3] 正在下载更新包（自动尝试多个源）...

curl -L --connect-timeout 15 --max-time 60 -o "%ZIP%" "https://ghfast.top/https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%ZIP%" for %%A in ("%ZIP%") do if %%~zA GEQ 10000 goto extract

echo   源1较慢，切换源2...
curl -L --connect-timeout 15 --max-time 60 -o "%ZIP%" "https://gh-proxy.com/https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%ZIP%" for %%A in ("%ZIP%") do if %%~zA GEQ 10000 goto extract

echo   源2较慢，切换GitHub直连...
curl -L --connect-timeout 20 --max-time 120 -o "%ZIP%" "https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%ZIP%" for %%A in ("%ZIP%") do if %%~zA GEQ 10000 goto extract

goto fail

:extract
echo   下载成功，正在解压...
mkdir update_tmp 2>nul
tar -xf "%ZIP%" -C update_tmp 2>nul
if errorlevel 1 goto fail
if not exist "update_tmp\trend-stock-main\app.py" goto fail

echo [2/3] 正在替换代码文件（保留你的自选股/设置/数据）...
robocopy "update_tmp\trend-stock-main" "." /E /XF *.json /XD bt_data research .git .sessions /NFL /NDL /NJH /NJS /NP /R:1 /W:1 >nul 2>&1
if errorlevel 8 goto fail
copy /y "update_tmp\trend-stock-main\latest.json" "." >nul 2>&1
copy /y "update_tmp\trend-stock-main\VERSION" "." >nul 2>&1

echo [3/3] 正在清理临时文件...
rmdir /s /q update_tmp 2>nul
del "%ZIP%" 2>nul

echo.
echo ========================================
echo   更新完成！
echo   请先关闭软件窗口，再重新双击启动
echo ========================================
echo.
pause
exit /b 0

:fail
echo.
echo ========================================
echo   更新失败：所有下载源均不可用
echo   请检查网络连接后重试
echo ========================================
rmdir /s /q update_tmp 2>nul
del "%ZIP%" 2>nul
echo.
pause
exit /b 1
