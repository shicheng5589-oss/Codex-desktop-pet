@echo off
setlocal
cd /d "%~dp0"
echo 正在关闭旧的桌宠…
echo stop > "%~dp0stop.request"
ping -n 7 127.0.0.1 >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*CodexPet*run.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
ping -n 2 127.0.0.1 >nul
echo 正在启动新的桌宠…
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0pet-launcher.ps1"
ping -n 3 127.0.0.1 >nul
