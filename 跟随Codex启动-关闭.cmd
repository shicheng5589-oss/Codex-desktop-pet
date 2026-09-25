@echo off
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CodexPetWatcher.lnk"
if exist "%DST%" (
  del /f /q "%DST%"
  echo [OK] 已关闭「跟随 Codex 启动」。
) else (
  echo 当前没有开启「跟随 Codex 启动」。
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*codex-watcher.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
timeout /t 4 >nul
