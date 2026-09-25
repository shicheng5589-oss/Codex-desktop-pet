# Codex 桌宠启动器：找一个能用的 Python，用 pythonw 静默把桌宠拉起来。
# 直接运行 = 启动；-Install = 加入开机启动；-Uninstall = 取消开机启动。
param([switch]$Install, [switch]$Uninstall)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$startupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$startupLink = Join-Path $startupDir "CodexPet.lnk"

function Find-Python {
  param([string]$Leaf)
  if ($env:CODEXPET_PYTHON -and (Test-Path $env:CODEXPET_PYTHON)) { return $env:CODEXPET_PYTHON }
  $roots = Join-Path $env:USERPROFILE ".cache\codex-runtimes"
  if (Test-Path $roots) {
    foreach ($dir in Get-ChildItem $roots -Directory) {
      foreach ($rel in @("dependencies\python\$Leaf", "dependencies\python\python.exe")) {
        $candidate = Join-Path $dir.FullName $rel
        if (Test-Path $candidate) { return $candidate }
      }
    }
  }
  foreach ($name in @($Leaf, "python.exe")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  return $null
}

if ($Uninstall) {
  try {
    if (Test-Path $startupLink) { Remove-Item $startupLink -Force; Write-Host "[OK] 已取消开机启动。" }
    else { Write-Host "当前没有设置开机启动。" }
  } catch { Write-Host "[X] 取消失败：$($_.Exception.Message)" }
  exit 0
}

if ($Install) {
  try {
    if (-not (Test-Path $startupDir)) { New-Item -ItemType Directory -Force -Path $startupDir | Out-Null }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($startupLink)
    $link.TargetPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $link.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $here "pet-launcher.ps1") + '"'
    $link.WorkingDirectory = $here
    $link.IconLocation = Join-Path $here "web\icon.ico"
    $link.Description = "Codex 桌宠"
    $link.Save()
    Write-Host "[OK] 已加入开机启动：$startupLink"
  } catch { Write-Host "[X] 加入开机启动失败：$($_.Exception.Message)" }
  exit 0
}

$python = Find-Python -Leaf "pythonw.exe"
if (-not $python) {
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show("没找到 Python 解释器。请把环境变量 CODEXPET_PYTHON 指向 python.exe，或用 诊断.cmd 查看详情。", "Codex 桌宠") | Out-Null
  exit 1
}

# 如果上一次没退干净（比如旧版本不认识停止请求），先按记录下来的 PID 收掉它
$pidFile = Join-Path $here "pet.pid"
if (Test-Path $pidFile) {
  $oldPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($oldPid -match '^\d+$') {
    $proc = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -match 'python') {
      Write-Host "关闭上一个桌宠进程 $oldPid"
      Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
      Start-Sleep -Milliseconds 800
    }
  }
}

Start-Process -FilePath $python -ArgumentList (Join-Path $here "run.py") -WorkingDirectory $here -WindowStyle Hidden
