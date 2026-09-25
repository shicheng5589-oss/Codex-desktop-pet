@echo off
setlocal
cd /d "%~dp0"

set "PY=%CODEXPET_PYTHON%"
if not defined PY (
  for /d %%d in ("%USERPROFILE%\.cache\codex-runtimes\*") do (
    if not defined PY if exist "%%~fd\dependencies\python\pythonw.exe" set "PY=%%~fd\dependencies\python\pythonw.exe"
  )
)
if not defined PY (
  for /f "delims=" %%f in ('where pythonw.exe 2^>nul') do (
    if not defined PY set "PY=%%f"
  )
)
if not defined PY (
  echo [X] 没找到 Python 解释器，请把环境变量 CODEXPET_PYTHON 指向 python.exe
  pause
  exit /b 1
)

start "" "%PY%" "%~dp0run.py"
exit /b 0
