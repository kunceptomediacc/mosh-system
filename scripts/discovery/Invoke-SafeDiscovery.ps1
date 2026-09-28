[CmdletBinding()]
param(
    [string[]]$Roots = @('D:\Agents', 'D:\Hermes', 'D:\MCP', 'D:\Dev', 'D:\Workspace'),
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\..\artifacts\reports\discovery.json')
)

$ErrorActionPreference = 'Stop'
$forbiddenRoot = [System.IO.Path]::GetFullPath('D:\balot\thor').TrimEnd('\')
$blockedNames = @('node_modules', '.git', '.venv', 'venv', '__pycache__', 'dist', 'build', 'cache', 'caches')
$sensitiveName = '(?i)(^\.env($|\.)|secret|credential|token|auth\.json$|\.pem$|\.key$)'
$items = [System.Collections.Generic.List[object]]::new()

function Test-ForbiddenPath([string]$Path) {
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    return $full.Equals($forbiddenRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($forbiddenRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
    if (Test-ForbiddenPath $root) { throw "Forbidden discovery root rejected: $root" }

    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push([System.IO.Path]::GetFullPath($root))
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        if (Test-ForbiddenPath $current) { continue }

        foreach ($entry in Get-ChildItem -LiteralPath $current -Force -ErrorAction SilentlyContinue) {
            if (Test-ForbiddenPath $entry.FullName) { continue }
            if ($entry.PSIsContainer) {
                if ($blockedNames -notcontains $entry.Name) { $pending.Push($entry.FullName) }
                continue
            }
            if ($entry.Name -match $sensitiveName) { continue }
            $items.Add([ordered]@{
                path = $entry.FullName
                bytes = $entry.Length
                modified = $entry.LastWriteTimeUtc.ToString('o')
                extension = $entry.Extension
            })
        }
    }
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedOutput) -Force | Out-Null
[ordered]@{
    generatedAt = [DateTime]::UtcNow.ToString('o')
    roots = $Roots
    exclusionRespected = $true
    sensitiveContentRead = $false
    itemCount = $items.Count
    items = $items
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resolvedOutput -Encoding utf8

Write-Output "Wrote metadata-only inventory for $($items.Count) files to $resolvedOutput"
