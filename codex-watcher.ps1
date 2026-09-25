# 盯着 Codex：Codex 一启动，桌宠就跟着起来。
# 用「跟随Codex启动-开启.cmd」把它放进开机启动，之后它会一直静静等着（每 4 秒看一眼）。
param(
  [int]$IntervalSeconds = 4,
  [string]$ProcessName = ""     # 留空 = 自动识别 Codex 桌面版 / CLI
)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logFile = Join-Path $here "watcher.log"
$pidFile = Join-Path $here "pet.pid"

function Write-Log([string]$text) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $text
  Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Get-CodexProcess {
  if ($ProcessName) { return Get-Process -Name $ProcessName -ErrorAction SilentlyContinue }
  # 桌面版是 ChatGPT.exe（在 OpenAI.Codex 目录下），CLI 是 codex.exe
  $list = @()
  foreach ($name in @("ChatGPT", "codex")) {
    $list += Get-Process -Name $name -ErrorAction SilentlyContinue
  }
  return $list
}

function Test-PetRunning {
  if (-not (Test-Path $pidFile)) { return $false }
  $raw = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($raw -notmatch '^\d+$') { return $false }
  $proc = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
  return [bool]($proc -and $proc.ProcessName -like "python*")
}

Write-Log "监视器启动（每 $IntervalSeconds 秒检查一次 Codex）"
$wasRunning = $false

while ($true) {
  try {
    $codex = Get-CodexProcess
    $codexRunning = [bool]$codex
    $petRunning = Test-PetRunning

    if ($codexRunning -and -not $petRunning) {
      Write-Log "检测到 Codex 已启动，拉起桌宠"
      Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $here "pet-launcher.ps1")) `
        -WorkingDirectory $here -WindowStyle Hidden
    }
    if ($codexRunning -ne $wasRunning) { $wasRunning = $codexRunning }
  } catch {
    Write-Log ("检查出错：" + $_.Exception.Message)
  }
  Start-Sleep -Seconds $IntervalSeconds
}
