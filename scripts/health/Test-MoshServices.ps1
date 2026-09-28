[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$DashboardPort = 8889,
    [ValidateRange(1, 65535)][int]$IdentityPort = 1455
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$tokenFile = Join-Path $projectRoot '.local\api\token'
$database = Join-Path $projectRoot 'data\mosh.db'
$token = (Get-Content -LiteralPath $tokenFile -Raw).Trim()
$headers = @{ Authorization = "Bearer $token" }
$dashboard = Invoke-RestMethod -Uri "http://127.0.0.1:$DashboardPort/api/v1/health" -Headers $headers -TimeoutSec 3
$identity = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$IdentityPort/" -TimeoutSec 3
$databaseStatus = python -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute("PRAGMA integrity_check").fetchone()[0]); print(c.execute("SELECT max(version) FROM schema_migrations").fetchone()[0])' $database
if ($LASTEXITCODE -ne 0) { throw 'Database health check failed.' }

[pscustomobject]@{
    ok = ($dashboard.data.status -eq 'ok' -and $identity.StatusCode -eq 200 -and $databaseStatus[0] -eq 'ok')
    dashboard = $dashboard.data.status
    identity = if ($identity.StatusCode -eq 200) { 'ok' } else { 'failed' }
    database_integrity = $databaseStatus[0]
    schema_version = [int]$databaseStatus[1]
} | ConvertTo-Json -Compress
