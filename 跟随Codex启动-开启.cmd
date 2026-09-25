@echo off
setlocal
cd /d "%~dp0"
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\CodexPetWatcher.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $l = $ws.CreateShortcut('%DST%');" ^
  "$l.TargetPath = '%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe';" ^
  "$l.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0codex-watcher.ps1"';" ^
  "$l.WorkingDirectory = '%~dp0'; $l.IconLocation = '%~dp0web\icon.ico'; $l.Save()"
if errorlevel 1 (
  echo [X] 创建失败，请手动把 codex-watcher.ps1 的快捷方式放进 shell:startup
) else (
  echo [OK] 已开启「跟随 Codex 启动」：
  echo      %DST%
  echo      以后登录 Windows 后它会静静等着，Codex 一开桌宠就跟着起来。
)
timeout /t 6 >nul
