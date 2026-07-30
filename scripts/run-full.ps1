param(
    [int]$Workers = 8,
    [double]$RequestsPerSecond = 0,
    [switch]$SkipAssets
)

. (Join-Path $PSScriptRoot '_common.ps1')

$inventorySummaryPath = $InventorySummaryPath

Write-Host "Building the knowledge base (dataset $DatasetId). The task is resumable."

if (-not (Test-Path -LiteralPath $inventorySummaryPath)) {
    throw 'site_inventory_summary.json does not exist yet; finish the discover phase first.'
}
$inventory = Get-Content -LiteralPath $inventorySummaryPath -Raw |
    ConvertFrom-Json
if ($inventory.status -ne 'complete' -or $inventory.failed_sitemaps -ne 0) {
    throw 'The page inventory is not complete; refusing to start the body phase.'
}

if ($SkipAssets) {
    Invoke-DocAtlas @('crawl', '--skip-discovery', '--workers', $Workers, '--requests-per-second', $RequestsPerSecond, '--export')
}
else {
    Invoke-DocAtlas @('crawl', '--skip-discovery', '--workers', $Workers, '--requests-per-second', $RequestsPerSecond, '--download-assets', '--export')
}

if ($LASTEXITCODE -ne 0) {
    throw "The crawler exited with code $LASTEXITCODE"
}

Write-Host "Done. Router: $(Join-Path $DataDir 'ROUTER.md')"
