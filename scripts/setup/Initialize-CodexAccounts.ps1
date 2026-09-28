[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateCount(2, 20)]
    [string[]]$Aliases = @('personal', 'work')
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$accountsRoot = Join-Path $repoRoot '.local\codex-accounts'
$registryPath = Join-Path $accountsRoot 'accounts.json'

$normalized = foreach ($alias in $Aliases) {
    $value = $alias.Trim().ToLowerInvariant()
    if ($value -notmatch '^[a-z][a-z0-9-]{0,31}$') {
        throw "Invalid account alias '$alias'. Use letters, digits, and hyphens; start with a letter."
    }
    $value
}

if (($normalized | Sort-Object -Unique).Count -ne $normalized.Count) {
    throw 'Account aliases must be unique.'
}

if ($PSCmdlet.ShouldProcess($accountsRoot, 'Create isolated Codex account homes')) {
    New-Item -ItemType Directory -Path $accountsRoot -Force | Out-Null
    $accounts = foreach ($alias in $normalized) {
        $accountHome = Join-Path $accountsRoot $alias
        New-Item -ItemType Directory -Path $accountHome -Force | Out-Null

        $configPath = Join-Path $accountHome 'config.toml'
        if (-not (Test-Path -LiteralPath $configPath)) {
            @(
                '# MOSH-managed account-isolated Codex home.'
                '# Authentication is created by an explicit `codex login` for this account.'
                'cli_auth_credentials_store = "file"'
            ) | Set-Content -LiteralPath $configPath -Encoding utf8
        }

        [ordered]@{
            alias = $alias
            label = ((Get-Culture).TextInfo.ToTitleCase($alias)) + ' Codex'
            home = $accountHome
        }
    }

    [ordered]@{
        version = 1
        defaultAccount = $normalized[0]
        accounts = $accounts
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $registryPath -Encoding utf8
}

Write-Output "Initialized $($normalized.Count) isolated Codex account homes."
Write-Output 'No account is authenticated yet. Run Start-CodexAccount.ps1 -Alias <name> login for each account.'
