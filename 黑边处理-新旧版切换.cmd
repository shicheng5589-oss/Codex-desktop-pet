@echo off
setlocal
cd /d "%~dp0"
echo.
if exist "%~dp0黑边修复-用旧版.txt" (
  del "%~dp0黑边修复-用旧版.txt" >nul
  echo  已切回【新版】补救：缩放后重画 9 次（间隔越来越稀，约 1.2 秒），
  echo  最后一轮还会把窗口抖 1px 逼系统重新合成。
) else (
  type nul > "%~dp0黑边修复-用旧版.txt"
  echo  已退回【旧版】补救：缩放后重画 6 次、固定 50 毫秒一次，不抖窗口。
)
echo.
echo  正在重启桌宠让它生效 ...
echo stop > "%~dp0stop.request"
ping -n 7 127.0.0.1 >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*CodexPet*run.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
ping -n 2 127.0.0.1 >nul
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0pet-launcher.ps1"
ping -n 3 127.0.0.1 >nul
echo  完成。想再切回来，再运行一次这个脚本就行。
ping -n 3 127.0.0.1 >nul
exit /b 0
