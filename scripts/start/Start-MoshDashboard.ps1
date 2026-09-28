[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8889,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$coreRoot = Join-Path $projectRoot 'apps\mosh-core'
$database = Join-Path $projectRoot 'data\mosh.db'
$tokenFile = Join-Path $projectRoot '.local\api\token'
$writeTokenFile = Join-Path $projectRoot '.local\api\write-token'
$logRoot = Join-Path $projectRoot '.local\logs'
$python = (Get-Command python -ErrorAction Stop).Source
$previousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = $coreRoot

try {
    $probe = [System.Net.Sockets.TcpClient]::new()
    try {
        $probe.Connect('127.0.0.1', $Port)
        throw "Port $Port is already in use; MOSH did not start another server."
    } catch [System.Net.Sockets.SocketException] {
        # Connection refused means the requested loopback port is available.
    } finally {
        $probe.Dispose()
    }

    if (-not (Test-Path -LiteralPath $tokenFile -PathType Leaf)) {
        & $python -m mosh_core.cli api-token-init --token-file $tokenFile | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize the dashboard token.' }
    }
    if (-not (Test-Path -LiteralPath $writeTokenFile -PathType Leaf)) {
        & $python -m mosh_core.cli api-token-init --token-file $writeTokenFile | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize the dashboard write token.' }
    }

    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $arguments = @('-m', 'mosh_core.cli', '--db', $database, 'serve-local', '--port', [string]$Port, '--token-file', $tokenFile, '--write-token-file', $writeTokenFile)
    $server = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'dashboard.stdout.log') -RedirectStandardError (Join-Path $logRoot 'dashboard.stderr.log')
    $url = "http://127.0.0.1:$Port/"
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 200
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            if ($server.HasExited) { throw "MOSH server exited with code $($server.ExitCode)." }
        }
    }
    if (-not $ready) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue; throw 'MOSH dashboard did not become ready.' }

    Get-Content -LiteralPath $tokenFile -Raw | Set-Clipboard
    if (-not $NoBrowser) { Start-Process $url }
    [pscustomobject]@{ ok = $true; url = $url; process_id = $server.Id; token_copied = $true; submissions_enabled = $true } | ConvertTo-Json -Compress
} finally {
    $env:PYTHONPATH = $previousPythonPath
}
