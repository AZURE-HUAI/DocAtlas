<#
.SYNOPSIS
    把 DocAtlas 技能装到 Claude Code。

.DESCRIPTION
    技能文件里必须写明程序在哪，否则 AI 找不到知识库。这个脚本在安装时
    把仓库的**实际位置**填进去——所以移动或改名项目目录之后，重跑一次
    这个脚本就行，不用手工改任何文件。

        .\scripts\install-skill.ps1

    仓库里的 skills/docatlas/SKILL.md 是原本，用 {{DOCATLAS_ROOT}} 占位；
    装出来的副本在 ~/.claude/skills/docatlas/。

.PARAMETER RetireLegacy
    把旧的 ue5-docs 技能挪进 _backup/。旧技能里写死的是改名前的路径，
    留着会让 AI 按错误的路径去找、然后失败。
#>

param([switch]$RetireLegacy)

. (Join-Path $PSScriptRoot '_common.ps1')

$sourceDir = Join-Path $RepoRoot 'skills\docatlas'
if (-not (Test-Path -LiteralPath (Join-Path $sourceDir 'SKILL.md'))) {
    throw "找不到技能原本：$sourceDir\SKILL.md"
}

$skillsHome = Join-Path $HOME '.claude\skills'
$target = Join-Path $skillsHome 'docatlas'
New-Item -ItemType Directory -Force -Path $target | Out-Null

# 装整个目录，不只是 SKILL.md：建库流程在 WORKFLOWS.md 里，漏掉它 AI 就只会查、
# 不会建。以后再加参考文件也不用回来改这个脚本。
#
# 占位符由 Python 填（render-skill）：只有它认识数据集。这个脚本只管把文件
# 放对地方——换个平台重写一个安装脚本，也不用把数据集那套知识再抄一遍。
$installed = @()
foreach ($file in Get-ChildItem -LiteralPath $sourceDir -Filter *.md) {
    $content = & python.exe -m docatlas render-skill $file.FullName | Out-String
    if ($LASTEXITCODE -ne 0) { throw "填充 $($file.Name) 失败" }
    $targetFile = Join-Path $target $file.Name
    $content | Set-Content -LiteralPath $targetFile -Encoding UTF8 -NoNewline
    $installed += $file.Name
}

Write-Host "已安装技能：$target" -ForegroundColor Green
Write-Host "  文件：$($installed -join '、')"
Write-Host "  程序位置写为：$RepoRoot"
Write-Host "  当前数据集：$DatasetId（原文语言 $DatasetLanguage）"

$legacy = Join-Path $skillsHome 'ue5-docs'
if (Test-Path -LiteralPath $legacy) {
    if ($RetireLegacy) {
        $shelf = Join-Path $RepoRoot '_backup\legacy-skill-ue5-docs'
        New-Item -ItemType Directory -Force -Path (Split-Path $shelf) | Out-Null
        if (Test-Path -LiteralPath $shelf) { Remove-Item -LiteralPath $shelf -Recurse -Force }
        Move-Item -LiteralPath $legacy -Destination $shelf
        Write-Host "旧的 ue5-docs 技能已挪到 $shelf（没有删除，需要可以搬回去）" -ForegroundColor Yellow
    }
    else {
        Write-Host ''
        Write-Host "注意：还装着旧的 ue5-docs 技能（$legacy）。" -ForegroundColor Yellow
        Write-Host "它里面写死的是项目改名前的路径，会让 AI 找错地方。"
        Write-Host "加 -RetireLegacy 重跑一次可以把它挪走："
        Write-Host "    .\scripts\install-skill.ps1 -RetireLegacy"
    }
}
