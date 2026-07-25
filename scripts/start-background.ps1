param(
    [int]$WaitForPid = 0,
    [int]$Workers = 8,
    [double]$RequestsPerSecond = 0,
    [ValidateSet('discover', 'content')]
    [string]$Mode = 'discover'
)

. (Join-Path $PSScriptRoot '_common.ps1')

$runner = Join-Path $PSScriptRoot 'background-runner.ps1'
$pidPath = $PidPath
$inventorySummaryPath = $InventorySummaryPath

if ($Mode -eq 'content') {
    if (-not (Test-Path -LiteralPath $inventorySummaryPath)) {
        throw '尚未生成完整页面清单，拒绝启动正文阶段。'
    }
    $inventory = Get-Content -LiteralPath $inventorySummaryPath -Raw |
        ConvertFrom-Json
    if ($inventory.status -ne 'complete' -or $inventory.failed_sitemaps -ne 0) {
        throw '页面清单尚未达到 complete，拒绝启动正文阶段。'
    }
}

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
        Write-Host "后台任务已经在运行，PID：$existingPid"
        exit 0
    }
}

$argumentList = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', "`"$runner`"",
    '-WaitForPid', $WaitForPid,
    '-Workers', $Workers,
    '-RequestsPerSecond', $RequestsPerSecond,
    '-Mode', $Mode
)

$process = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList $argumentList `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -PassThru

$process.Id | Set-Content -LiteralPath $pidPath -Encoding ASCII
Write-Host "后台 $Mode 阶段已排队，PID：$($process.Id)"
Write-Host '查看进度：.\docatlas.ps1 status'
