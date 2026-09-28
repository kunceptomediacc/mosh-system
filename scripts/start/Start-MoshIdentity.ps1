[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 1455
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$coreRoot = Join-Path $projectRoot 'apps\mosh-core'
$packageRoot = Join-Path $projectRoot '.local\python-packages'
$database = Join-Path $projectRoot 'data\mosh.db'
$clientIdFile = Join-Path $projectRoot '.local\identity\google-client-id'
$logRoot = Join-Path $projectRoot '.local\logs'
$python = (Get-Command python -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $clientIdFile -PathType Leaf)) {
    throw 'Google client ID file is missing.'
}

$probe = [System.Net.Sockets.TcpClient]::new()
try {
    $probe.Connect('127.0.0.1', $Port)
    throw "Port $Port is already in use; MOSH identity did not start another server."
} catch [System.Net.Sockets.SocketException] {
    # Connection refused means the requested loopback port is available.
} finally {
    $probe.Dispose()
}

$pythonPath = "$packageRoot;$coreRoot"
$arguments = @('-m', 'mosh_core.google_signin', '--db', $database, '--client-id-file', $clientIdFile, '--port', [string]$Port)
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$server = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -Environment @{ PYTHONPATH = $pythonPath } -RedirectStandardOutput (Join-Path $logRoot 'identity.stdout.log') -RedirectStandardError (Join-Path $logRoot 'identity.stderr.log')
$url = "http://127.0.0.1:$Port/"
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 200
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        if ($server.HasExited) { throw "MOSH identity server exited with code $($server.ExitCode)." }
    }
}
if (-not $ready) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue; throw 'MOSH identity server did not become ready.' }

[pscustomobject]@{ ok = $true; url = $url; process_id = $server.Id } | ConvertTo-Json -Compress
