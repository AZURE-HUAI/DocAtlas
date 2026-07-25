param(
    [int]$Workers = 8,
    [double]$RequestsPerSecond = 0,
    [switch]$SkipAssets
)

. (Join-Path $PSScriptRoot '_common.ps1')

$inventorySummaryPath = $InventorySummaryPath

Write-Host "开始构建知识库（数据集 $DatasetId）。请确认数据合同已冻结；任务可断点续传。"

if (-not (Test-Path -LiteralPath $inventorySummaryPath)) {
    throw '尚未生成 site_inventory_summary.json，请先完成 discover 阶段。'
}
$inventory = Get-Content -LiteralPath $inventorySummaryPath -Raw |
    ConvertFrom-Json
if ($inventory.status -ne 'complete' -or $inventory.failed_sitemaps -ne 0) {
    throw '页面清单尚未达到 complete，拒绝启动正文阶段。'
}

if ($SkipAssets) {
    Invoke-DocAtlas @('crawl', '--skip-discovery', '--workers', $Workers, '--requests-per-second', $RequestsPerSecond, '--export')
}
else {
    Invoke-DocAtlas @('crawl', '--skip-discovery', '--workers', $Workers, '--requests-per-second', $RequestsPerSecond, '--download-assets', '--export')
}

if ($LASTEXITCODE -ne 0) {
    throw "采集器退出码：$LASTEXITCODE"
}

Write-Host "完成。总路由：$(Join-Path $DataDir 'ROUTER.md')"
