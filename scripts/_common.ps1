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

# `paths` 失败最常见的原因是本机装了不止一个数据集、还没定过默认查哪个——
# `python` 把这句人话写去 stderr、退出码非零。stderr 重定向到**文件**而不是
# `2>&1`：native exe 的 2>&1 会把每行 stderr 包成 ErrorRecord 自动打到控制台，
# 再叠上下面这个 throw，同一条消息会显示两遍，还裹着 NativeCommandError 的
# CategoryInfo / FullyQualifiedErrorId 噪音。重定向到文件是纯字节操作，不走
# PowerShell 的错误对象模型，干净拿到原始文本。
$errFile = [System.IO.Path]::GetTempFileName()
try {
    $stdout = & python.exe -m docatlas paths 2> $errFile
    $exitCode = $LASTEXITCODE
    $stderr = (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue)
} finally {
    Remove-Item -LiteralPath $errFile -Force -ErrorAction SilentlyContinue
}
if ($exitCode -ne 0) {
    throw $(if ($stderr) { $stderr.Trim() } else { "python -m docatlas paths 失败（退出码 $exitCode），且没有错误信息。" })
}
$script:_paths = $stdout | ConvertFrom-Json
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
