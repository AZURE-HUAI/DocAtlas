param(
    [int]$WaitForPid = 0,
    [int]$Workers = 8,
    [double]$RequestsPerSecond = 0,
    [ValidateSet('discover', 'content')]
    [string]$Mode = 'discover'
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$crawler = Join-Path $scriptDir 'ue58_docs.py'
$logPath = Join-Path $scriptDir 'crawl.log'
$errorLogPath = Join-Path $scriptDir 'crawl-error.log'
$statePath = Join-Path $scriptDir 'background-state.json'

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
    Write-State -Status 'waiting' -Message "等待现有验收任务 $WaitForPid 结束"
    while (Get-Process -Id $WaitForPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Seconds 30
    }
}

if ($Mode -eq 'discover') {
    $statusMessage = '只枚举完整页面清单；完成后自动停止，不抓正文'
}
else {
    $statusMessage = '正在执行正文、知识加工、图片与 Markdown 导出'
}

Write-State -Status 'running' -Message $statusMessage
"[$(Get-Date -Format o)] 开始 $Mode 阶段断点续传任务" |
    Set-Content -LiteralPath $logPath -Encoding UTF8
if (Test-Path -LiteralPath $errorLogPath) {
    Clear-Content -LiteralPath $errorLogPath
}

$arguments = @($crawler, 'crawl')
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
        Write-State -Status 'completed' -ExitCode 0 -Message "$Mode 阶段已完成"
    }
    else {
        Write-State -Status 'failed' -ExitCode $exitCode -Message '任务退出，可重新运行 start-background.ps1 续传'
    }
}
catch {
    $_ | Out-String | Add-Content -LiteralPath $errorLogPath -Encoding UTF8
    Write-State -Status 'failed' -ExitCode 1 -Message $_.Exception.Message
}
