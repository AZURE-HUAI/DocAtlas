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
        throw 'No complete page inventory yet; refusing to start the body phase.'
    }
    $inventory = Get-Content -LiteralPath $inventorySummaryPath -Raw |
        ConvertFrom-Json
    if ($inventory.status -ne 'complete' -or $inventory.failed_sitemaps -ne 0) {
        throw 'The page inventory is not complete; refusing to start the body phase.'
    }
}

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) {
        Write-Host "A background task is already running, PID $existingPid"
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
Write-Host "Background $Mode phase queued, PID $($process.Id)"
Write-Host 'Watch progress with: .\docatlas.ps1 status'
