. (Join-Path $PSScriptRoot '_common.ps1')

$statePath = $StatePath
$pidPath = $PidPath
$logPath = $LogPath
$errorLogPath = $ErrorLogPath

Write-Host "=== 后台状态（数据集 $DatasetId）==="
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
Invoke-DocAtlas @('stats')

if (Test-Path -LiteralPath $logPath) {
    Write-Host '=== 最近进度 ==='
    Get-Content -LiteralPath $logPath -Tail 15
}

if ((Test-Path -LiteralPath $errorLogPath) -and
    (Get-Item -LiteralPath $errorLogPath).Length -gt 0) {
    Write-Host '=== 最近错误 ==='
    Get-Content -LiteralPath $errorLogPath -Tail 10
}
