@echo off
setlocal
cd /d "%~dp0"

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
  echo [X] 没找到 Python，请设置 CODEXPET_PYTHON
  pause
  exit /b 1
)

echo ==== 备份里可以恢复的动作 ====
"%PY%" restore_props.py --list
echo.
echo 直接回车 = 恢复全部动作（含锤子/猫手/泡泡等道具动画）
echo 也可以输入关键字只恢复某一个，例如： 自拍
set /p KEY=关键字:
echo.
if "%KEY%"=="" (
  "%PY%" restore_props.py
) else (
  "%PY%" restore_props.py --motions "%KEY%"
)
echo.
echo 完成后重启桌宠即可；想再删掉就再跑一次 work/purge_props.py（或找我要一键脚本）。
pause
