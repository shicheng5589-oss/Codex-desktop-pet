@echo off
setlocal
cd /d "%~dp0"

echo ==== Codex 桌宠 自检 ====
echo.

set "PY=%CODEXPET_PYTHON%"
if not defined PY (
  for /d %%d in ("%USERPROFILE%\.cache\codex-runtimes\*") do (
    if not defined PY if exist "%%~fd\dependencies\python\python.exe" set "PY=%%~fd\dependencies\python\python.exe"
  )
)
if not defined PY (
  for /f "delims=" %%f in ('where python.exe 2^>nul') do (
    if not defined PY set "PY=%%f"
  )
)
if not defined PY (
  echo [X] 没找到 Python 解释器，请把环境变量 CODEXPET_PYTHON 指向 python.exe
  pause
  exit /b 1
)

echo 解释器: %PY%
echo 正在自检（约 40 秒），结束后会打印日志。
echo.
"%PY%" run.py --selftest 25 --shot "%~dp0selftest.png"
echo.
echo ==== 日志 ====
type pet.log
echo.
echo 结果截图: %~dp0selftest.png
echo.
pause
