<#
.SYNOPSIS
    转发到当前版本的知识库入口，省得每次都要先 cd 进版本目录。

.DESCRIPTION
    真正的入口在 5.8.0\ue.ps1。这个脚本把所有参数原样转过去，
    所以在 UE5文档 这一层直接运行就行：

        .\ue.ps1              交互式搜索
        .\ue.ps1 watch        实时看抓取进度
        .\ue.ps1 ask "Nanite"
#>

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# 有多个版本目录时用最新的那个。
$versionDir = Get-ChildItem -LiteralPath $scriptDir -Directory |
    Where-Object { $_.Name -match '^\d+\.\d+' } |
    Sort-Object { [version]($_.Name) } |
    Select-Object -Last 1

if (-not $versionDir) {
    Write-Host "在 $scriptDir 下没找到版本目录（例如 5.8.0）。" -ForegroundColor Red
    exit 1
}

$target = Join-Path $versionDir.FullName 'ue.ps1'
if (-not (Test-Path -LiteralPath $target)) {
    Write-Host "找不到 $target" -ForegroundColor Red
    exit 1
}

& $target @args
