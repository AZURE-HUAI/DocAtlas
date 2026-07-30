. (Join-Path $PSScriptRoot '_common.ps1')

$statePath = $StatePath
$pidPath = $PidPath
$logPath = $LogPath
$errorLogPath = $ErrorLogPath

Write-Host "=== Background status (dataset $DatasetId) ==="
if (Test-Path -LiteralPath $statePath) {
    Get-Content -LiteralPath $statePath -Raw
}
elseif (Test-Path -LiteralPath $pidPath) {
    $runnerPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $runnerPid -ErrorAction SilentlyContinue) {
        Write-Host "Background runner is up, PID $runnerPid"
    }
    else {
        Write-Host 'The background runner is not running.'
    }
}
else {
    Write-Host 'No background task has been started.'
}

Write-Host '=== Coverage ==='
Invoke-DocAtlas @('stats')

if (Test-Path -LiteralPath $logPath) {
    Write-Host '=== Recent progress ==='
    Get-Content -LiteralPath $logPath -Tail 15
}

if ((Test-Path -LiteralPath $errorLogPath) -and
    (Get-Item -LiteralPath $errorLogPath).Length -gt 0) {
    Write-Host '=== Recent errors ==='
    Get-Content -LiteralPath $errorLogPath -Tail 10
}
