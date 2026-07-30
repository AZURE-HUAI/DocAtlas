<#
.SYNOPSIS
    DocAtlas local documentation knowledge base -- the single entry point.

.DESCRIPTION
    Everything happens from here, with no Python commands to remember:

        .\docatlas.ps1                          interactive search (no arguments)
        .\docatlas.ps1 ask   "<what to look up>"  readable answer material
                                                (recommended; a page missing
                                                locally is fetched on demand)
        .\docatlas.ps1 get   "<page name>"      fetch that page only
        .\docatlas.ps1 find  "<keywords>"       titles and sources, no bodies
        .\docatlas.ps1 show  K9290              expand one piece of knowledge
        .\docatlas.ps1 links "<name or K id>"   how entities relate
        .\docatlas.ps1 status                   crawl progress
        .\docatlas.ps1 start                    start / resume crawling
        .\docatlas.ps1 stop                     stop crawling
        .\docatlas.ps1 check                    data quality check
        .\docatlas.ps1 where                    where the data actually lives

    To switch library: set $env:DOCATLAS_DATASET = '<dataset-id>' first, then
    carry on as usual.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('ask', 'get', 'find', 'show', 'links', 'status', 'watch', 'start', 'stop', 'check', 'where', 'menu')]
    [string]$Action = 'menu',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest,

    [int]$Limit = 10,
    [int]$TokenBudget = 3000,
    # Category names come from the dataset and are deliberately not listed here:
    # a hardcoded list would reject a legitimate category after switching
    # dataset. Validity is decided by the Python layer, which knows the dataset.
    [string]$Category
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir 'scripts\_common.ps1')

$subject = ($Rest -join ' ').Trim()

function Invoke-Kb {
    param([string[]]$KbArgs)
    Invoke-DocAtlas $KbArgs
}

function Require-Subject {
    param([string]$Hint)
    if ([string]::IsNullOrWhiteSpace($subject)) {
        Write-Host "A query is required, for example: $Hint" -ForegroundColor Yellow
        exit 2
    }
}

function Show-Menu {
    Write-Host ''
    Write-Host '========================================'
    Write-Host "  DocAtlas — $DatasetId"
    Write-Host '========================================'
    Write-Host 'Type what to look up; press Enter on an empty line to quit.'
    Write-Host "Official wording in the source language ($DatasetLanguage) hits best."
    Write-Host ''

    while ($true) {
        $query = Read-Host 'Look up'
        if ([string]::IsNullOrWhiteSpace($query)) { break }

        Invoke-Kb @('search', $query, '--limit', $Limit)
        if ($LASTEXITCODE -ne 0) { continue }

        Write-Host ''
        Write-Host 'Enter a knowledge ID (e.g. K9290) for the full text, a for a prepared answer, or Enter to search again.'
        $choice = Read-Host 'Next'
        if ($choice -match '^[Kk]?\d+$') {
            Write-Host ''
            Invoke-Kb @('show', $choice)
        }
        elseif ($choice -eq 'a') {
            Write-Host ''
            Invoke-Kb @('ask', $query, '--token-budget', $TokenBudget)
        }
        Write-Host ''
    }
    Write-Host 'Done.'
}

switch ($Action) {
    'menu' { Show-Menu }

    'ask' {
        Require-Subject '.\docatlas.ps1 ask "<what to look up>"'
        $kbArgs = @('ask', $subject, '--token-budget', $TokenBudget)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'get' {
        Require-Subject '.\docatlas.ps1 get "<page name>"'
        $kbArgs = @('get', $subject, '--limit', $Limit)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'find' {
        Require-Subject '.\docatlas.ps1 find "<keywords>"'
        $kbArgs = @('search', $subject, '--limit', $Limit)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'show' {
        Require-Subject '.\docatlas.ps1 show K9290'
        Invoke-Kb @('show', $subject)
    }

    'links' {
        Require-Subject '.\docatlas.ps1 links "<name or K id>"'
        Invoke-Kb @('related', $subject)
    }

    'where' { Invoke-Kb @('paths') }

    'status' { & (Join-Path $scriptDir 'scripts\status.ps1') }

    'watch' {
        if (-not (Test-Path -LiteralPath $LogPath)) {
            Write-Host 'No crawl log yet. Run .\docatlas.ps1 start first.'
            exit 1
        }
        Write-Host 'Live progress (Ctrl+C to leave; the background crawl keeps running):' -ForegroundColor Cyan
        Write-Host ''
        Get-Content -LiteralPath $LogPath -Tail 15 -Wait
    }

    'start' { & (Join-Path $scriptDir 'scripts\start-background.ps1') -Mode content }

    'stop' {
        $stopped = $false
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*docatlas*crawl*' } |
            ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                $stopped = $true
            }
        if (Test-Path -LiteralPath $PidPath) {
            $runnerPid = [int](Get-Content -LiteralPath $PidPath -Raw)
            Stop-Process -Id $runnerPid -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        }
        if ($stopped) {
            Write-Host 'Stopped. Progress lives in the database; .\docatlas.ps1 start resumes from there.'
        }
        else {
            Write-Host 'No crawl is running.'
        }
    }

    'check' { Invoke-Kb @('validate', '--phase', 'content') }
}
