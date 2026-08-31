@echo off
chcp 65001 >nul
title 趋势全景 · 一键更新
cd /d "%~dp0"

echo ========================================
echo   趋势全景 · 一键更新（不经过浏览器）
echo   你的自选股/设置/数据都会保留
echo ========================================
echo.
echo 【重要】更新前请确保软件已完全关闭！
echo        （包括黑色命令行窗口和浏览器页面）
echo.
pause

set "ZIP=update_pkg.zip"
del "%ZIP%" 2>nul
rmdir /s /q update_tmp 2>nul

echo.
echo [1/4] 正在下载更新包（自动尝试多个源）...

curl -L --connect-timeout 15 --max-time 60 -o "%ZIP%" "https://ghfast.top/https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%ZIP%" for %%A in ("%ZIP%") do if %%~zA GEQ 10000 goto extract

echo   源1较慢，切换源2...
curl -L --connect-timeout 15 --max-time 60 -o "%ZIP%" "https://gh-proxy.com/https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%ZIP%" for %%A in ("%ZIP%") do if %%~zA GEQ 10000 goto extract

echo   源2较慢，切换GitHub直连...
curl -L --connect-timeout 20 --max-time 120 -o "%ZIP%" "https://github.com/xjhod/trend-stock/archive/refs/heads/main.zip" 2>nul
if exist "%ZIP%" for %%A in ("%ZIP%") do if %%~zA GEQ 10000 goto extract

goto fail_download

:extract
echo   下载成功，正在解压...
mkdir update_tmp 2>nul
tar -xf "%ZIP%" -C update_tmp 2>nul
if errorlevel 1 goto fail_extract
if not exist "update_tmp\trend-stock-main\app.py" goto fail_extract

echo [2/4] 正在替换代码文件...
robocopy "update_tmp\trend-stock-main" "." /E /XF *.json /XD bt_data research .git .sessions /NP /R:1 /W:1
set "RC=%errorlevel%"
if %RC% GEQ 8 goto fail_copy
echo   代码文件替换完成（robocopy 返回码 %RC%）

echo [3/4] 正在更新版本信息...
copy /y "update_tmp\trend-stock-main\latest.json" "." >nul 2>&1
copy /y "update_tmp\trend-stock-main\VERSION" "." >nul 2>&1
if not exist "VERSION" goto fail_version

set /p NEWVER=<VERSION
echo   版本文件已更新为：%NEWVER%

echo [4/4] 正在清理临时文件...
rmdir /s /q update_tmp 2>nul
del "%ZIP%" 2>nul

echo.
echo ========================================
echo   更新完成！当前版本：%NEWVER%
echo   请重新双击启动软件即可使用新版
echo ========================================
echo.
pause
exit /b 0

:fail_download
echo.
echo ========================================
echo   更新失败：所有下载源均不可用
echo   请检查网络连接后重试
echo ========================================
goto cleanup

:fail_extract
echo.
echo ========================================
echo   更新失败：解压失败或更新包损坏
echo   请删除 update_pkg.zip 后重试
echo ========================================
goto cleanup

:fail_copy
echo.
echo ========================================
echo   更新失败：文件替换失败（返回码 %RC%）
echo   可能原因：软件未完全关闭，文件被占用
echo   请关闭软件后重新运行本脚本
echo ========================================
goto cleanup

:fail_version
echo.
echo ========================================
echo   更新失败：版本文件写入失败
echo   请检查目录权限后重试
echo ========================================
goto cleanup

:cleanup
rmdir /s /q update_tmp 2>nul
del "%ZIP%" 2>nul
echo.
pause
exit /b 1
