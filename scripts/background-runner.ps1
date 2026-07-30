param(
    [int]$WaitForPid = 0,
    [int]$Workers = 8,
    [double]$RequestsPerSecond = 0,
    [ValidateSet('discover', 'content')]
    [string]$Mode = 'discover'
)

. (Join-Path $PSScriptRoot '_common.ps1')

$logPath = $LogPath
$errorLogPath = $ErrorLogPath
$statePath = $StatePath

function Write-State {
    param(
        [string]$Status,
        [int]$ExitCode = 0,
        [string]$Message = ''
    )

    [ordered]@{
        status = $Status
        runner_pid = $PID
        wait_for_pid = $WaitForPid
        mode = $Mode
        exit_code = $ExitCode
        message = $Message
        updated_at = (Get-Date).ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
}

if ($WaitForPid -gt 0) {
    Write-State -Status 'waiting' -Message "Waiting for the running acceptance task $WaitForPid to finish"
    while (Get-Process -Id $WaitForPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Seconds 30
    }
}

if ($Mode -eq 'discover') {
    $statusMessage = 'Enumerating the full page inventory only; stops when done, fetches no bodies'
}
else {
    $statusMessage = 'Fetching bodies, processing knowledge, downloading assets and exporting Markdown'
}

Write-State -Status 'running' -Message $statusMessage
"[$(Get-Date -Format o)] Starting the resumable $Mode phase" |
    Set-Content -LiteralPath $logPath -Encoding UTF8
if (Test-Path -LiteralPath $errorLogPath) {
    Clear-Content -LiteralPath $errorLogPath
}

$arguments = @('-m', 'docatlas', 'crawl')
if ($Mode -eq 'discover') {
    $arguments += @(
        '--sitemap-workers', 1,
        '--requests-per-second', $RequestsPerSecond,
        '--log-file', $logPath,
        '--discovery-only'
    )
}
else {
    $arguments += @(
        '--skip-discovery',
        '--workers', $Workers,
        '--requests-per-second', $RequestsPerSecond,
        '--log-file', $logPath,
        '--download-assets',
        '--export'
    )
}

try {
    & python.exe @arguments 2>> $errorLogPath | Out-Null
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        Write-State -Status 'completed' -ExitCode 0 -Message "The $Mode phase is complete"
    }
    else {
        Write-State -Status 'failed' -ExitCode $exitCode -Message 'The task exited; rerun start-background.ps1 to resume'
    }
}
catch {
    $_ | Out-String | Add-Content -LiteralPath $errorLogPath -Encoding UTF8
    Write-State -Status 'failed' -ExitCode 1 -Message $_.Exception.Message
}
