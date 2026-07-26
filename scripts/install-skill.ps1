<#
.SYNOPSIS
    把 DocAtlas 技能装到本机的 AI 客户端。

.DESCRIPTION
    技能文件里必须写明程序在哪、当前装的是哪份文档，否则 AI 找不到知识库。
    这个脚本在安装时把仓库的**实际位置**和数据集信息填进去——所以移动或改名
    项目目录之后，重跑一次就行，不用手工改任何文件。

        .\scripts\install-skill.ps1                  # 装到检测到的所有客户端
        .\scripts\install-skill.ps1 -Client codex    # 只装 Codex
        .\scripts\install-skill.ps1 -Target D:\x     # 装到指定目录

    仓库里的 skills/docatlas/*.md 是原本，用 {{占位符}} 写通用措辞；
    填充由 `python -m docatlas render-skill` 完成（只有它认识数据集）。

    **写出的文件一律是 UTF-8 无 BOM。** Windows PowerShell 的 `Set-Content
    -Encoding UTF8` 会加 BOM，Codex 读到 BOM 就认不出 SKILL.md 的 frontmatter。

.PARAMETER Client
    claude-code / codex / all（默认）。

.PARAMETER Target
    直接指定安装目录，跳过客户端检测。用于测试或不常见的客户端。

.PARAMETER RetireLegacy
    把旧的 ue5-docs 技能挪进 _backup/。旧技能里写死的是改名前的路径，
    留着会让 AI 按错误的路径去找、然后失败。
#>

param(
    [ValidateSet('all', 'claude-code', 'codex')]
    [string]$Client = 'all',
    [string]$Target,
    [switch]$RetireLegacy
)

. (Join-Path $PSScriptRoot '_common.ps1')

$SkillName = 'docatlas'
$sourceDir = Join-Path $RepoRoot "skills\$SkillName"
if (-not (Test-Path -LiteralPath (Join-Path $sourceDir 'SKILL.md'))) {
    throw "找不到技能原本：$sourceDir\SKILL.md"
}

# 两个客户端都是 <家目录>/skills/<技能名>/。加新客户端就在这里加一行，
# 下面的逻辑一行都不用改。
$ClientHomes = [ordered]@{
    'claude-code' = Join-Path $HOME '.claude'
    'codex'       = Join-Path $HOME '.codex'
}

function Get-InstallTargets {
    if ($Target) { return @([pscustomobject]@{ Name = 'custom'; Path = $Target }) }
    $found = @()
    foreach ($name in $ClientHomes.Keys) {
        if ($Client -ne 'all' -and $Client -ne $name) { continue }
        $clientHome = $ClientHomes[$name]
        # 只装到真的存在的客户端：凭空造一个 ~/.codex 只会留下没人读的目录。
        if (Test-Path -LiteralPath $clientHome) {
            $found += [pscustomobject]@{
                Name = $name
                Path = Join-Path $clientHome "skills\$SkillName"
            }
        }
    }
    return $found
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Content)
    # 显式用不带 BOM 的 UTF8Encoding；PowerShell 5.1 的 -Encoding UTF8 会加 BOM。
    [System.IO.File]::WriteAllText(
        $Path, $Content, (New-Object System.Text.UTF8Encoding $false)
    )
}

# 占位符由 Python 填：只有它认识数据集。这个脚本只管把文件放对地方——
# 换个平台重写一个安装脚本，也不用把数据集那套知识再抄一遍。
$rendered = [ordered]@{}
foreach ($file in Get-ChildItem -LiteralPath $sourceDir -Filter *.md) {
    $content = & python.exe -m docatlas render-skill $file.FullName | Out-String
    if ($LASTEXITCODE -ne 0) { throw "填充 $($file.Name) 失败" }
    if (-not $content.Trim()) { throw "$($file.Name) 填出来是空的" }
    $rendered[$file.Name] = $content
}

$targets = Get-InstallTargets
if (-not $targets) {
    throw "没有检测到 AI 客户端（找过：$($ClientHomes.Keys -join '、')）。用 -Target 指定目录。"
}

foreach ($item in $targets) {
    New-Item -ItemType Directory -Force -Path $item.Path | Out-Null
    foreach ($name in $rendered.Keys) {
        Write-Utf8NoBom -Path (Join-Path $item.Path $name) -Content $rendered[$name]
    }

    # 装完自己验一遍：文件在不在、有没有 BOM、占位符是不是真填掉了、
    # frontmatter 还在不在。"命令跑完没报错"不等于客户端认得出这个技能。
    foreach ($name in $rendered.Keys) {
        $path = Join-Path $item.Path $name
        $bytes = [System.IO.File]::ReadAllBytes($path)
        if ($bytes.Length -lt 3) { throw "$path 写出来是空的" }
        if ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            throw "$path 带 BOM，Codex 会认不出来"
        }
        if ([System.IO.File]::ReadAllText($path) -match '\{\{[A-Z_]+\}\}') {
            throw "$path 里还有没填的占位符"
        }
    }
    $skill = [System.IO.File]::ReadAllText((Join-Path $item.Path 'SKILL.md'))
    if (-not $skill.StartsWith('---')) {
        throw "SKILL.md 开头不是 frontmatter，客户端不会把它当技能"
    }

    Write-Host "✓ $($item.Name)：$($item.Path)" -ForegroundColor Green
    Write-Host "  文件：$($rendered.Keys -join '、')"
}

Write-Host ''
Write-Host "程序位置写为：$RepoRoot"
Write-Host "当前数据集：$DatasetId（原文语言 $DatasetLanguage）"

$legacy = Join-Path $HOME '.claude\skills\ue5-docs'
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
