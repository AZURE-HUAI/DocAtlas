[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$issueRoot = Join-Path $repoRoot 'issues'
$indexPath = Join-Path $issueRoot 'README.md'
$errors = @()

function Get-RelativePath {
    param([string]$BasePath, [string]$TargetPath)

    if ([System.IO.Path].GetMethods().Name -contains 'GetRelativePath') {
        return [System.IO.Path]::GetRelativePath(
            $BasePath,
            $TargetPath
        ).Replace('\', '/')
    }

    $base = [System.IO.Path]::GetFullPath($BasePath)
    if (-not $base.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $base += [System.IO.Path]::DirectorySeparatorChar
    }
    $baseUri = [System.Uri]::new($base)
    $targetUri = [System.Uri]::new([System.IO.Path]::GetFullPath($TargetPath))
    return [System.Uri]::UnescapeDataString(
        $baseUri.MakeRelativeUri($targetUri).ToString()
    )
}

function Get-Field {
    param([string]$Content, [string]$Name)

    $match = [regex]::Match(
        $Content,
        "(?m)^$([regex]::Escape($Name)):\s*(.+)$"
    )
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return $null
}

if (-not (Test-Path -LiteralPath $indexPath -PathType Leaf)) {
    throw "Issue index not found: $indexPath"
}

$requiredFields = @(
    'id', 'title', 'type', 'status', 'lifecycle', 'priority', 'area',
    'labels', 'reported_at', 'resolved_at', 'github_issue', 'fix_pr', 'related'
)
$activeStatuses = @('open', 'investigating', 'in_progress', 'blocked', 'discussion')
$closedStatuses = @('resolved', 'closed')
$records = @()

$recordFiles = Get-ChildItem -LiteralPath $issueRoot -Recurse -File -Filter '*.md' |
    Where-Object {
        $_.Name -ne 'README.md' -and
        $_.FullName -match '[\\/](unresolved|resolved)[\\/]'
    }

foreach ($file in $recordFiles) {
    $content = Get-Content -LiteralPath $file.FullName -Raw
    $path = Get-RelativePath $issueRoot $file.FullName
    $fields = @{}

    foreach ($name in $requiredFields) {
        $fields[$name] = Get-Field $content $name
        if ([string]::IsNullOrWhiteSpace($fields[$name])) {
            $errors += "$path`: missing field '$name'"
        }
    }
    if (-not $fields.id) {
        continue
    }

    $isOpen = $path -match '/unresolved/'
    $isClosed = $path -match '/resolved/'

    if ($isOpen -and (
        $fields.lifecycle -ne 'unresolved' -or
        $fields.status -notin $activeStatuses -or
        $fields.resolved_at -ne 'null'
    )) {
        $errors += "$path`: unresolved metadata does not match its directory"
    }
    if ($isClosed -and (
        $fields.lifecycle -ne 'resolved' -or
        $fields.status -notin $closedStatuses -or
        $fields.resolved_at -eq 'null'
    )) {
        $errors += "$path`: resolved metadata does not match its directory"
    }

    $expectedType = if ($path.StartsWith('bugs/')) { 'bug' } else { 'enhancement' }
    if ($fields.type -ne $expectedType) {
        $errors += "$path`: expected type '$expectedType'"
    }

    $idMatch = [regex]::Match($fields.id, '^(BUG|ENH)-(\d{3})$')
    $fileMatch = [regex]::Match($file.Name, '^(\d{4})-')
    if (-not $idMatch.Success -or -not $fileMatch.Success) {
        $errors += "$path`: invalid id or filename"
    } elseif ([int]$idMatch.Groups[2].Value -ne [int]$fileMatch.Groups[1].Value) {
        $errors += "$path`: id does not match filename"
    }

    $records += [pscustomobject]@{
        Id = $fields.id
        Related = $fields.related
        Path = $path
        IsOpen = $isOpen
    }
}

foreach ($duplicate in $records | Group-Object Id | Where-Object Count -gt 1) {
    $errors += "duplicate id '$($duplicate.Name)'"
}

$knownIds = @($records.Id)
foreach ($record in $records) {
    $relatedIds = [regex]::Matches(
        $record.Related,
        '(BUG|ENH)-\d{3}'
    ) | ForEach-Object Value
    foreach ($relatedId in $relatedIds) {
        if ($relatedId -notin $knownIds) {
            $errors += "$($record.Path): unknown related id '$relatedId'"
        }
    }
}

$index = Get-Content -LiteralPath $indexPath -Raw
foreach ($record in $records | Where-Object IsOpen) {
    if ($index -notmatch [regex]::Escape($record.Id) -or
        $index -notmatch [regex]::Escape($record.Path)) {
        $errors += "$($record.Path): missing from issues/README.md"
    }
}

foreach ($file in Get-ChildItem -LiteralPath $issueRoot -Recurse -File -Filter '*.md') {
    $content = Get-Content -LiteralPath $file.FullName -Raw
    foreach ($link in [regex]::Matches($content, '\[[^\]]+\]\(([^)]+)\)')) {
        $target = $link.Groups[1].Value
        if ($target -match '^(https?://|mailto:|#)') {
            continue
        }
        $target = $target.Split('#')[0]
        $resolved = [System.IO.Path]::GetFullPath(
            (Join-Path $file.DirectoryName $target)
        )
        if (-not (Test-Path -LiteralPath $resolved)) {
            $path = Get-RelativePath $issueRoot $file.FullName
            $errors += "$path`: broken link '$target'"
        }
    }
}

$formRoot = Join-Path $repoRoot '.github/ISSUE_TEMPLATE'
$forms = Get-ChildItem -LiteralPath $formRoot -File -Filter '*.yml' |
    Where-Object Name -ne 'config.yml'
foreach ($form in $forms) {
    $content = Get-Content -LiteralPath $form.FullName -Raw
    $path = Get-RelativePath $repoRoot $form.FullName
    foreach ($name in @('name', 'description', 'title', 'labels', 'body')) {
        if ($content -notmatch "(?m)^$name`:") {
            $errors += "$path`: missing top-level field '$name'"
        }
    }
    $ids = [regex]::Matches(
        $content,
        '(?m)^\s+id:\s*([A-Za-z0-9_-]+)\s*$'
    ) | ForEach-Object { $_.Groups[1].Value }
    foreach ($duplicate in $ids | Group-Object | Where-Object Count -gt 1) {
        $errors += "$path`: duplicate form id '$($duplicate.Name)'"
    }
}

if ($errors) {
    Write-Host "Issue validation failed:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}

Write-Host (
    "Issue validation passed: {0} records, lifecycle, index, links, and forms." -f
    $records.Count
) -ForegroundColor Green
