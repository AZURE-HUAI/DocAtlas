param(
    [int]$Workers = 8,
    [double]$RequestsPerSecond = 0,
    [switch]$SkipAssets
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$crawler = Join-Path $scriptDir 'ue58_docs.py'
$inventorySummaryPath = Join-Path $scriptDir 'site_inventory_summary.json'

Write-Host '开始构建 UE 5.8 官方文档知识库。请确认数据合同已冻结；任务可断点续传。'

if (-not (Test-Path -LiteralPath $inventorySummaryPath)) {
    throw '尚未生成 site_inventory_summary.json，请先完成 discover 阶段。'
}
$inventory = Get-Content -LiteralPath $inventorySummaryPath -Raw |
    ConvertFrom-Json
if ($inventory.status -ne 'complete' -or $inventory.failed_sitemaps -ne 0) {
    throw '页面清单尚未达到 complete，拒绝启动正文阶段。'
}

if ($SkipAssets) {
    python.exe $crawler crawl --skip-discovery --workers $Workers --requests-per-second $RequestsPerSecond --export
}
else {
    python.exe $crawler crawl --skip-discovery --workers $Workers --requests-per-second $RequestsPerSecond --download-assets --export
}

if ($LASTEXITCODE -ne 0) {
    throw "采集器退出码：$LASTEXITCODE"
}

Write-Host "完成。总路由：$(Join-Path $scriptDir 'ROUTER.md')"
