[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$registryPath = Join-Path $repoRoot '.local\codex-accounts\accounts.json'

if (-not (Test-Path -LiteralPath $registryPath -PathType Leaf)) {
    throw 'Codex account registry not found. Run Initialize-CodexAccounts.ps1 first.'
}

$registry = Get-Content -Raw -LiteralPath $registryPath | ConvertFrom-Json
$previousCodexHome = $env:CODEX_HOME
try {
    foreach ($account in $registry.accounts) {
        $env:CODEX_HOME = $account.home
        $status = (& codex login status 2>&1 | Out-String).Trim()
        [pscustomobject]@{
            Alias = $account.alias
            Label = $account.label
            Ready = $LASTEXITCODE -eq 0 -and $status -match '^Logged in'
            Status = $status
        }
    }
} finally {
    $env:CODEX_HOME = $previousCodexHome
}
