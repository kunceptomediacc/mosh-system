[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$tokenFile = Join-Path $projectRoot '.local\api\write-token'
if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
    throw 'MOSH write token is missing. Start the dashboard once to initialize it.'
}
$token = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
if ($token.Length -lt 16) { throw 'MOSH write token is invalid.' }
Set-Clipboard -Value $token
Write-Output 'MOSH write token copied to clipboard.'
