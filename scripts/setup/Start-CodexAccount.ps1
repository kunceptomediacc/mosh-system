[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{0,31}$')]
    [string]$Alias,

    [Parameter(ValueFromRemainingArguments)]
    [string[]]$CodexArguments
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$accountHome = Join-Path $repoRoot ".local\codex-accounts\$($Alias.ToLowerInvariant())"

if (-not (Test-Path -LiteralPath $accountHome -PathType Container)) {
    throw "Unknown account alias '$Alias'. Run Initialize-CodexAccounts.ps1 first."
}

$env:CODEX_HOME = $accountHome
Write-Host "Codex account context: $Alias" -ForegroundColor Cyan
Write-Host "CODEX_HOME: $accountHome" -ForegroundColor DarkGray
& codex @CodexArguments
exit $LASTEXITCODE
