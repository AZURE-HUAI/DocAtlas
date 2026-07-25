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

$source = Join-Path $RepoRoot 'skills\docatlas\SKILL.md'
if (-not (Test-Path -LiteralPath $source)) { throw "找不到技能原本：$source" }

$skillsHome = Join-Path $HOME '.claude\skills'
$target = Join-Path $skillsHome 'docatlas'
New-Item -ItemType Directory -Force -Path $target | Out-Null

# 反斜杠路径写进 Markdown 就是原样文本，不需要转义。
$content = (Get-Content -LiteralPath $source -Raw).Replace('{{DOCATLAS_ROOT}}', $RepoRoot)
$targetFile = Join-Path $target 'SKILL.md'
$content | Set-Content -LiteralPath $targetFile -Encoding UTF8 -NoNewline

Write-Host "已安装技能：$targetFile" -ForegroundColor Green
Write-Host "  程序位置写为：$RepoRoot"
Write-Host "  当前数据集：$DatasetId"

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
