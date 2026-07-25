# 所有 PowerShell 脚本共用的路径解析。
#
# 代码和数据现在分开住了，脚本不能再假设"数据在我旁边"。
# 数据在哪由 config.py 一处说了算，这里只问一次，不重写一遍规则——
# 换数据集（DOCATLAS_DATASET）或换盘（DOCATLAS_HOME）时不用改任何脚本。

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'

# 本文件在 scripts/ 下，无论谁点源引用，$PSScriptRoot 都是 scripts/ 自己。
$RepoRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $RepoRoot

function Invoke-DocAtlas {
    param([string[]]$DocArgs)
    & python.exe -m docatlas @DocArgs
}

$script:_paths = & python.exe -m docatlas paths | ConvertFrom-Json
if (-not $script:_paths) { throw '无法定位数据目录：python -m docatlas paths 没有输出。' }

$DatasetId = $script:_paths.dataset
$DatasetLanguage = $script:_paths.language
$DataDir = $script:_paths.data_dir
$DbPath = $script:_paths.database

$LogPath = Join-Path $DataDir 'crawl.log'
$ErrorLogPath = Join-Path $DataDir 'crawl-error.log'
$StatePath = Join-Path $DataDir 'background-state.json'
$PidPath = Join-Path $DataDir 'background-runner.pid'
$InventorySummaryPath = Join-Path $DataDir 'site_inventory_summary.json'
