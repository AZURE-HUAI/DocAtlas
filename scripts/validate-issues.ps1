[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$issueRoot = Join-Path $repoRoot 'issues'
$indexPath = Join-Path $issueRoot 'README.md'
$errors = @()

function Add-ValidationError {
    param([string]$Message)

    $script:errors += $Message
}

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

function Get-FrontMatter {
    param([string]$Content, [string]$Path)

    $match = [regex]::Match(
        $Content,
        '\A---\r?\n(?<frontMatter>.*?)\r?\n---(?:\r?\n|\z)',
        [System.Text.RegularExpressions.RegexOptions]::Singleline
    )
    if (-not $match.Success) {
        Add-ValidationError "$path`: missing or malformed front matter"
        return @{}
    }

    $fields = @{}
    foreach ($line in $match.Groups['frontMatter'].Value -split '\r?\n') {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $fieldMatch = [regex]::Match($line, '^(?<name>[a-z_]+):\s*(?<value>.*)$')
        if (-not $fieldMatch.Success) {
            Add-ValidationError "$path`: malformed front matter line '$line'"
            continue
        }
        $name = $fieldMatch.Groups['name'].Value
        if ($fields.ContainsKey($name)) {
            Add-ValidationError "$path`: duplicate field '$name'"
            continue
        }
        $fields[$name] = $fieldMatch.Groups['value'].Value.Trim()
    }
    return $fields
}

function Get-ScalarValue {
    param([string]$Value)

    if ($null -eq $Value) {
        return $null
    }
    $value = $Value.Trim()
    if ($value.Length -ge 2 -and (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
    )) {
        return $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Get-ListValues {
    param(
        [string]$Value,
        [string]$Path,
        [string]$Name
    )

    if ($Value -notmatch '^\[(?<items>.*)\]$') {
        Add-ValidationError "$path`: field '$name' must be an inline list"
        return ,@()
    }
    $items = $Matches['items'].Trim()
    if (-not $items) {
        return ,@()
    }
    return ,@($items -split ',' | ForEach-Object {
        Get-ScalarValue $_.Trim()
    })
}

function Test-IsoDate {
    param([string]$Value)

    $parsed = [datetime]::MinValue
    return [datetime]::TryParseExact(
        $Value,
        'yyyy-MM-dd',
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::None,
        [ref]$parsed
    )
}

function Get-MarkdownSection {
    param([string]$Content, [string]$Heading)

    $match = [regex]::Match(
        $Content,
        "(?ms)^##\s+$([regex]::Escape($Heading))\s*\r?\n(?<body>.*?)(?=^##\s+|\z)"
    )
    if ($match.Success) {
        return $match.Groups['body'].Value.Trim()
    }
    return $null
}

function Get-IndexSection {
    param([string]$Content, [string]$Heading)

    $match = [regex]::Match(
        $Content,
        "(?ms)^##\s+$([regex]::Escape($Heading))\s*\r?\n(?<body>.*?)(?=^##\s+|\z)"
    )
    if ($match.Success) {
        return $match.Groups['body'].Value
    }
    return $null
}

function Escape-MarkdownTableCell {
    param([string]$Value)

    return $Value.Replace('|', '\|').Replace("`r", ' ').Replace("`n", ' ')
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
$priorities = @('high', 'medium', 'low')
$records = @()

$recordFiles = Get-ChildItem -LiteralPath $issueRoot -Recurse -File -Filter '*.md' |
    Where-Object {
        $_.Name -ne 'README.md' -and
        $_.FullName -match '[\\/](unresolved|resolved)[\\/]'
    }

foreach ($file in $recordFiles) {
    $content = Get-Content -LiteralPath $file.FullName -Raw
    $path = Get-RelativePath $issueRoot $file.FullName
    $fields = Get-FrontMatter $content $path

    foreach ($name in $requiredFields) {
        if (-not $fields.ContainsKey($name) -or
            [string]::IsNullOrWhiteSpace($fields[$name])) {
            $errors += "$path`: missing field '$name'"
        }
    }
    if (-not $fields.ContainsKey('id')) {
        continue
    }

    $id = Get-ScalarValue $fields.id
    $title = Get-ScalarValue $fields.title
    $type = Get-ScalarValue $fields.type
    $status = Get-ScalarValue $fields.status
    $lifecycle = Get-ScalarValue $fields.lifecycle
    $priority = Get-ScalarValue $fields.priority
    $area = Get-ScalarValue $fields.area
    $reportedAt = Get-ScalarValue $fields.reported_at
    $resolvedAt = Get-ScalarValue $fields.resolved_at
    $githubIssue = Get-ScalarValue $fields.github_issue
    $fixPr = Get-ScalarValue $fields.fix_pr
    $labels = Get-ListValues $fields.labels $path 'labels'
    $related = Get-ListValues $fields.related $path 'related'
    $isOpen = $path -match '/unresolved/'
    $isClosed = $path -match '/resolved/'

    if ($isOpen -and (
        $lifecycle -ne 'unresolved' -or
        $status -notin $activeStatuses -or
        $resolvedAt -ne 'null'
    )) {
        $errors += "$path`: unresolved metadata does not match its directory"
    }
    if ($isClosed -and (
        $lifecycle -ne 'resolved' -or
        $status -notin $closedStatuses -or
        $resolvedAt -eq 'null'
    )) {
        $errors += "$path`: resolved metadata does not match its directory"
    }

    $expectedType = if ($path.StartsWith('bugs/')) { 'bug' } else { 'enhancement' }
    if ($type -ne $expectedType) {
        $errors += "$path`: expected type '$expectedType'"
    }

    $idMatch = [regex]::Match($id, '^(BUG|ENH)-(\d{3})$')
    $fileMatch = [regex]::Match($file.Name, '^(\d{4})-')
    if (-not $idMatch.Success -or -not $fileMatch.Success) {
        $errors += "$path`: invalid id or filename"
    } elseif ([int]$idMatch.Groups[2].Value -ne [int]$fileMatch.Groups[1].Value) {
        $errors += "$path`: id does not match filename"
    }

    if ([string]::IsNullOrWhiteSpace($title) -or $title.Contains('|')) {
        $errors += "$path`: title must be non-empty and cannot contain '|'"
    }
    if ($priority -notin $priorities) {
        $errors += "$path`: invalid priority '$priority'"
    }
    if ($area -notmatch '^[a-z0-9][a-z0-9-]*$') {
        $errors += "$path`: invalid area '$area'"
    }
    if (-not (Test-IsoDate $reportedAt)) {
        $errors += "$path`: reported_at must use YYYY-MM-DD"
    }
    if ($resolvedAt -ne 'null' -and -not (Test-IsoDate $resolvedAt)) {
        $errors += "$path`: resolved_at must be null or YYYY-MM-DD"
    }
    if ($githubIssue -ne 'null' -and
        $githubIssue -notmatch '^https://github\.com/[^/]+/[^/]+/issues/\d+/?$') {
        $errors += "$path`: github_issue must be null or a GitHub Issue URL"
    }
    if ($fixPr -ne 'null' -and
        $fixPr -notmatch '^https://github\.com/[^/]+/[^/]+/pull/\d+/?$') {
        $errors += "$path`: fix_pr must be null or a GitHub Pull Request URL"
    }
    foreach ($label in $labels) {
        if ($label -notmatch '^[a-z0-9][a-z0-9-]*$') {
            $errors += "$path`: invalid label '$label'"
        }
    }
    if ($labels.Count -ne ($labels | Select-Object -Unique).Count) {
        $errors += "$path`: duplicate labels"
    }
    if ($related -contains $id) {
        $errors += "$path`: related cannot reference its own id"
    }
    if ($related.Count -ne ($related | Select-Object -Unique).Count) {
        $errors += "$path`: duplicate related ids"
    }

    if ($isClosed) {
        foreach ($heading in @('验证', '解决记录')) {
            $section = Get-MarkdownSection $content $heading
            if ([string]::IsNullOrWhiteSpace($section) -or
                $section -match '^未解决时留空') {
                $errors += "$path`: resolved record requires a non-empty '$heading' section"
            }
        }
    }

    $records += [pscustomobject]@{
        Id = $id
        Title = $title
        Type = $type
        Status = $status
        Lifecycle = $lifecycle
        Priority = $priority
        Related = $related
        Path = $path
        IsOpen = $isOpen
    }
}

foreach ($duplicate in $records | Group-Object Id | Where-Object Count -gt 1) {
    $errors += "duplicate id '$($duplicate.Name)'"
}

$knownIds = @($records.Id)
foreach ($record in $records) {
    foreach ($relatedId in $record.Related) {
        if ($relatedId -notin $knownIds) {
            $errors += "$($record.Path): unknown related id '$relatedId'"
        }
    }
}

$index = Get-Content -LiteralPath $indexPath -Raw
foreach ($record in $records) {
    $anchor = $record.Id.ToLowerInvariant()
    $title = Escape-MarkdownTableCell $record.Title
    $expectedRow = (
        '| <a id="{0}"></a>[{1}]({2}) | {3} | {4} | {5} |' -f
        $anchor, $record.Id, $record.Path, $title, $record.Status,
        $record.Priority
    )
    $sectionName = if ($record.Type -eq 'bug') {
        if ($record.IsOpen) { '未解决问题' } else { '已解决问题' }
    } else {
        if ($record.IsOpen) { '未解决增强建议' } else { '已解决增强建议' }
    }
    $section = Get-IndexSection $index $sectionName
    if ($null -eq $section -or
        $section -notmatch "(?m)^$([regex]::Escape($expectedRow))\s*$") {
        $errors += "$($record.Path): index row is missing, stale, or in the wrong section"
    }
    $anchorCount = [regex]::Matches(
        $index,
        [regex]::Escape("<a id=""$anchor""></a>")
    ).Count
    if ($anchorCount -ne 1) {
        $errors += "$($record.Path): stable index anchor must appear exactly once"
    }
    $linkCount = [regex]::Matches(
        $index,
        [regex]::Escape("[$($record.Id)](")
    ).Count
    if ($linkCount -ne 1) {
        $errors += "$($record.Path): index link must appear exactly once"
    }
}

$indexedIds = [regex]::Matches(
    $index,
    '\[((?:BUG|ENH)-\d{3})\]\('
) | ForEach-Object { $_.Groups[1].Value }
foreach ($indexedId in $indexedIds) {
    if ($indexedId -notin $knownIds) {
        $errors += "issues/README.md: unknown indexed id '$indexedId'"
    }
}

$indexGroups = @(
    @{ Heading = '未解决问题'; Type = 'bug'; IsOpen = $true; EmptyText = $null },
    @{ Heading = '已解决问题'; Type = 'bug'; IsOpen = $false; EmptyText = '当前没有已解决问题。' },
    @{ Heading = '未解决增强建议'; Type = 'enhancement'; IsOpen = $true; EmptyText = $null },
    @{ Heading = '已解决增强建议'; Type = 'enhancement'; IsOpen = $false; EmptyText = '当前没有已解决增强。' }
)
foreach ($group in $indexGroups) {
    $section = Get-IndexSection $index $group.Heading
    $count = @($records | Where-Object {
        $_.Type -eq $group.Type -and $_.IsOpen -eq $group.IsOpen
    }).Count
    if ($group.EmptyText) {
        $hasEmptyText = $section -match [regex]::Escape($group.EmptyText)
        if (($count -eq 0) -ne $hasEmptyText) {
            $errors += "issues/README.md: '$($group.Heading)' empty-state text is stale"
        }
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
    if ($content.Contains("`t")) {
        $errors += "$path`: YAML must not contain tab indentation"
    }
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
