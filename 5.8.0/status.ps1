$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$crawler = Join-Path $scriptDir 'ue58_docs.py'
$statePath = Join-Path $scriptDir 'background-state.json'
$pidPath = Join-Path $scriptDir 'background-runner.pid'
$logPath = Join-Path $scriptDir 'crawl.log'
$errorLogPath = Join-Path $scriptDir 'crawl-error.log'

Write-Host '=== 后台状态 ==='
if (Test-Path -LiteralPath $statePath) {
    Get-Content -LiteralPath $statePath -Raw
}
elseif (Test-Path -LiteralPath $pidPath) {
    $runnerPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $runnerPid -ErrorAction SilentlyContinue) {
        Write-Host "后台启动器运行中，PID：$runnerPid"
    }
    else {
        Write-Host '后台启动器当前未运行。'
    }
}
else {
    Write-Host '尚未启动后台任务。'
}

Write-Host '=== 覆盖率 ==='
python.exe $crawler stats

if (Test-Path -LiteralPath $logPath) {
    Write-Host '=== 最近进度 ==='
    Get-Content -LiteralPath $logPath -Tail 15
}

if ((Test-Path -LiteralPath $errorLogPath) -and
    (Get-Item -LiteralPath $errorLogPath).Length -gt 0) {
    Write-Host '=== 最近错误 ==='
    Get-Content -LiteralPath $errorLogPath -Tail 10
}
