<#
.SYNOPSIS
    UE 5.8 本地文档知识库 —— 唯一入口。

.DESCRIPTION
    不用记 Python 命令，所有事情都从这里做：

        .\ue.ps1                          打开交互式搜索（不带参数时）
        .\ue.ps1 ask   "Nanite"           直接给出可读的答案材料（推荐；
                                          本地没有会自动去 Epic 补抓那一页）
        .\ue.ps1 get   "ACharacter"       只把指定的页面抓到本地
        .\ue.ps1 find  "Nanite"           只列标题和出处，不展开正文
        .\ue.ps1 show  K9290              展开某一条知识
        .\ue.ps1 links "Set Timer by Function Name"
                                          看蓝图 / C++ / 类型的对应关系
        .\ue.ps1 status                   看抓取进度
        .\ue.ps1 start                    开始 / 继续抓取（可随时中断续传）
        .\ue.ps1 stop                     停止抓取
        .\ue.ps1 check                    数据质量验收
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('ask', 'get', 'find', 'show', 'links', 'status', 'watch', 'start', 'stop', 'check', 'menu')]
    [string]$Action = 'menu',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest,

    [int]$Limit = 10,
    [int]$TokenBudget = 3000,
    [ValidateSet('guides', 'community_docs', 'blueprint_api', 'cpp_api', 'python_api', 'node_reference')]
    [string]$Category
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$crawler = Join-Path $scriptDir 'ue58_docs.py'
$subject = ($Rest -join ' ').Trim()

function Invoke-Kb {
    param([string[]]$KbArgs)
    & python.exe $crawler @KbArgs
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
    Write-Host '  Unreal Engine 5.8 本地文档'
    Write-Host '========================================'
    Write-Host '直接输入要查的东西；回车留空退出。'
    Write-Host '英文关键词命中率最高，例：Nanite、Set Timer、Lumen'
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
        Require-Subject '.\ue.ps1 ask "Nanite"'
        $kbArgs = @('ask', $subject, '--token-budget', $TokenBudget)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'get' {
        Require-Subject '.\ue.ps1 get "ACharacter"'
        $kbArgs = @('get', $subject, '--limit', $Limit)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'find' {
        Require-Subject '.\ue.ps1 find "Nanite"'
        $kbArgs = @('search', $subject, '--limit', $Limit)
        if ($Category) { $kbArgs += @('--category', $Category) }
        Invoke-Kb $kbArgs
    }

    'show' {
        Require-Subject '.\ue.ps1 show K9290'
        Invoke-Kb @('show', $subject)
    }

    'links' {
        Require-Subject '.\ue.ps1 links "Set Timer by Function Name"'
        Invoke-Kb @('related', $subject)
    }

    'status' { & (Join-Path $scriptDir 'status.ps1') }

    'watch' {
        $logPath = Join-Path $scriptDir 'crawl.log'
        if (-not (Test-Path -LiteralPath $logPath)) {
            Write-Host '还没有抓取日志。先运行 .\ue.ps1 start'
            exit 1
        }
        Write-Host '实时进度（按 Ctrl+C 退出，退出不会影响后台抓取）：' -ForegroundColor Cyan
        Write-Host ''
        Get-Content -LiteralPath $logPath -Tail 15 -Wait
    }

    'start' { & (Join-Path $scriptDir 'start-background.ps1') -Mode content }

    'stop' {
        $pidPath = Join-Path $scriptDir 'background-runner.pid'
        $stopped = $false
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*ue58_docs.py*crawl*' } |
            ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                $stopped = $true
            }
        if (Test-Path -LiteralPath $pidPath) {
            $runnerPid = [int](Get-Content -LiteralPath $pidPath -Raw)
            Stop-Process -Id $runnerPid -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
        }
        if ($stopped) {
            Write-Host '已停止。进度都在数据库里，下次 .\ue.ps1 start 会从断点继续。'
        }
        else {
            Write-Host '当前没有在跑的抓取任务。'
        }
    }

    'check' { Invoke-Kb @('validate', '--phase', 'content') }
}
