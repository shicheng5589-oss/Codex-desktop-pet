@echo off
setlocal
cd /d "%~dp0"
echo stop > "%~dp0stop.request"
echo 已通知桌宠退出…
ping -n 6 127.0.0.1 >nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*CodexPet*run.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
echo 完成。
ping -n 3 127.0.0.1 >nul
