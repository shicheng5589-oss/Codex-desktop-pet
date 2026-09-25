@echo off
setlocal
cd /d "%~dp0"

echo 桌宠的 WebView2 缓存通常在 200MB 以上（就是它撑着启动速度），
echo 删掉可以释放空间，下次启动会自动重建（第一次启动会稍慢）。
echo.
set /p OK=确定要清理吗？(Y/N):
if /i not "%OK%"=="Y" exit /b 0

echo 正在关闭桌宠...
echo stop > "%~dp0stop.request"
ping -n 6 127.0.0.1 >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*CodexPet*run.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
ping -n 2 127.0.0.1 >nul

if exist "%~dp0.webview2" (
  rmdir /s /q "%~dp0.webview2"
  echo [OK] 缓存已清理。
) else (
  echo 没有找到缓存目录。
)

echo 正在重新启动桌宠...
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0pet-launcher.ps1"
ping -n 4 127.0.0.1 >nul
echo 完成。
timeout /t 3 >nul
