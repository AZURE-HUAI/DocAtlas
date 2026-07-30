# Path resolution shared by every PowerShell script.
#
# Code and data can live apart, so a script must not assume the data is
# next to it. Where the data lives is decided in one place by config.py;
# this asks once instead of restating the rules, so switching dataset
# (DOCATLAS_DATASET) or drive (DOCATLAS_HOME) needs no script change.

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'

# This file lives in scripts/, so $PSScriptRoot is scripts/ itself no
# matter who dot-sources it.
$RepoRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $RepoRoot

function Invoke-DocAtlas {
    param([string[]]$DocArgs)
    & python.exe -m docatlas @DocArgs
}

# `paths` most often fails because the machine has more than one dataset
# and no default was ever chosen; Python writes that in plain words to
# stderr and exits non-zero. stderr goes to a **file** rather than
# `2>&1`: for a native exe, 2>&1 wraps each stderr line in an ErrorRecord
# that prints itself to the console, so together with the throw below the
# same message appears twice, wrapped in NativeCommandError CategoryInfo
# and FullyQualifiedErrorId noise. Redirecting to a file is a plain byte
# operation that bypasses PowerShell's error object model and yields the
# original text.
$errFile = [System.IO.Path]::GetTempFileName()
try {
    $stdout = & python.exe -m docatlas paths 2> $errFile
    $exitCode = $LASTEXITCODE
    $stderr = (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue)
} finally {
    Remove-Item -LiteralPath $errFile -Force -ErrorAction SilentlyContinue
}
if ($exitCode -ne 0) {
    throw $(if ($stderr) { $stderr.Trim() } else { "python -m docatlas paths failed (exit code $exitCode) with no error message." })
}
$script:_paths = $stdout | ConvertFrom-Json
if (-not $script:_paths) { throw 'Cannot locate the data directory: python -m docatlas paths produced no output.' }

$DatasetId = $script:_paths.dataset
$DatasetLanguage = $script:_paths.language
$DataDir = $script:_paths.data_dir
$DbPath = $script:_paths.database

$LogPath = Join-Path $DataDir 'crawl.log'
$ErrorLogPath = Join-Path $DataDir 'crawl-error.log'
$StatePath = Join-Path $DataDir 'background-state.json'
$PidPath = Join-Path $DataDir 'background-runner.pid'
$InventorySummaryPath = Join-Path $DataDir 'site_inventory_summary.json'
