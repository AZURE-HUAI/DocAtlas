<#
.SYNOPSIS
    DocAtlas 本地文档知识库 —— 唯一入口。

.DESCRIPTION
    不用记 Python 命令，所有事情都从这里做：

        .\docatlas.ps1                          打开交互式搜索（不带参数时）
        .\docatlas.ps1 ask   "<要查的东西>"     直接给出可读的答案材料（推荐；
                                                本地没有会自动去官网补抓那一页）
        .\docatlas.ps1 get   "<页面名>"         只把指定的页面抓到本地
        .\docatlas.ps1 find  "<关键词>"         只列标题和出处，不展开正文
        .\docatlas.ps1 show  K9290              展开某一条知识
        .\docatlas.ps1 links "<名称或 K 编号>"
                                                看蓝图 / C++ / 类型的对应关系
        .\docatlas.ps1 status                   看抓取进度
        .\docatlas.ps1 start                    开始 / 继续抓取（可随时中断续传）
        .\docatlas.ps1 stop                     停止抓取
        .\docatlas.ps1 check                    数据质量验收
        .\docatlas.ps1 where                    数据实际存在哪个目录

    换数据集：先设 $env:DOCATLAS_DATASET = 'epic-ue-5.9'，再照常用。
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('ask', 'get', 'find', 'show', 'links', 'status', 'watch', 'start', 'stop', 'check', 'where', 'menu')]
    [string]$Action = 'menu',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest,

    [int]$Limit = 10,
    [int]$TokenBudget = 3000,
    # 分类名由数据集决定，这里不写死——写死了换个数据集就会拒绝合法的分类。
    # 合不合法交给 Python 那一层判断，它认识当前数据集。
    [string]$Category
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir 'scripts\_common.ps1')

$subject = ($Rest -join ' ').Trim()

function Invoke-Kb {
    param([string[]]$KbArgs)
    Invoke-DocAtlas $KbArgs
}

function Require-Subject {
    param([string]$Hint)
    if ([string]::IsNullOrWhiteSpace($subject)) {
        Write-Host "需要一个查询内容，例如：$Hint" -ForegroundColor Yellow
        exit 2
    }
}

function Show-Menu {
    Write-Host ''
    Write-Host '========================================'
    Write-Host "  DocAtlas — $DatasetId"
    Write-Host '========================================'
    Write-Host '直接输入要查的东西；回车留空退出。'
    Write-Host "用原文语言（$DatasetLanguage）里的官方写法命中率最高。"
    Write-Host ''

    while ($true) {
        $query = Read-Host '查什么'
        if ([string]::IsNullOrWhiteSpace($query)) { break }

        Invoke-Kb @('search', $query, '--limit', $Limit)
        if ($LASTEXITCODE -ne 0) { continue }

        Write-Host ''
        Write-Host '输入知识 ID（如 K9290）看全文；输入 a 看整理好的答案；回车继续搜索。'
        $choice = Read-Host '下一步'
        if ($choice -match '^[Kk]?\d+$') {
            Write-Host ''
            Invoke-Kb @('show', $choice)
        }
        elseif ($choice -eq 'a') {
            Write-Host ''
            Invoke-Kb @('ask', $query, '--token-budget', $TokenBudget)
        }
        Write-Host ''
    }
    Write-Host '已退出。'
}

switch ($Action) {
    'menu' { Show-Menu }

    'ask' {
        Require-Subject '.\docatlas.ps1 ask "<要查的东西>"'
        $kbArgs = @('ask', $subject, '--token-budget', $TokenBudget)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'get' {
        Require-Subject '.\docatlas.ps1 get "<页面名>"'
        $kbArgs = @('get', $subject, '--limit', $Limit)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'find' {
        Require-Subject '.\docatlas.ps1 find "<关键词>"'
        $kbArgs = @('search', $subject, '--limit', $Limit)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'show' {
        Require-Subject '.\docatlas.ps1 show K9290'
        Invoke-Kb @('show', $subject)
    }

    'links' {
        Require-Subject '.\docatlas.ps1 links "<名称或 K 编号>"'
        Invoke-Kb @('related', $subject)
    }

    'where' { Invoke-Kb @('paths') }

    'status' { & (Join-Path $scriptDir 'scripts\status.ps1') }

    'watch' {
        if (-not (Test-Path -LiteralPath $LogPath)) {
            Write-Host '还没有抓取日志。先运行 .\docatlas.ps1 start'
            exit 1
        }
        Write-Host '实时进度（按 Ctrl+C 退出，退出不会影响后台抓取）：' -ForegroundColor Cyan
        Write-Host ''
        Get-Content -LiteralPath $LogPath -Tail 15 -Wait
    }

    'start' { & (Join-Path $scriptDir 'scripts\start-background.ps1') -Mode content }

    'stop' {
        $stopped = $false
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*docatlas*crawl*' } |
            ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                $stopped = $true
            }
        if (Test-Path -LiteralPath $PidPath) {
            $runnerPid = [int](Get-Content -LiteralPath $PidPath -Raw)
            Stop-Process -Id $runnerPid -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        }
        if ($stopped) {
            Write-Host '已停止。进度都在数据库里，下次 .\docatlas.ps1 start 会从断点继续。'
        }
        else {
            Write-Host '当前没有在跑的抓取任务。'
        }
    }

    'check' { Invoke-Kb @('validate', '--phase', 'content') }
}
