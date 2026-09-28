[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action,
    [ValidateRange(1, 65535)][int]$DashboardPort = 8889,
    [ValidateRange(1, 65535)][int]$IdentityPort = 1455,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$stateRoot = Join-Path $projectRoot '.local\services'

function Get-StatePath([string]$Name) {
    Join-Path $stateRoot "$Name.json"
}

function Read-ServiceState([string]$Name) {
    $path = Get-StatePath $Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    } catch {
        throw "The $Name service state file is invalid; refusing to manage a process from it."
    }
}

function Write-ServiceState([string]$Name, [int]$ProcessId, [int]$Port) {
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $state = [ordered]@{
        version = 1
        service = $Name
        process_id = $ProcessId
        executable_path = $process.Path
        process_started_utc = $process.StartTime.ToUniversalTime().ToString('o')
        process_started_utc_ticks = $process.StartTime.ToUniversalTime().Ticks
        port = $Port
        recorded_utc = [DateTime]::UtcNow.ToString('o')
    }
    $path = Get-StatePath $Name
    $temporaryPath = "$path.$PID.tmp"
    $state | ConvertTo-Json | Set-Content -LiteralPath $temporaryPath -Encoding utf8
    Move-Item -LiteralPath $temporaryPath -Destination $path -Force
}

function Get-OwnedProcess($State) {
    $process = Get-Process -Id ([int]$State.process_id) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }

    $actualPath = $process.Path
    $actualStartTicks = $process.StartTime.ToUniversalTime().Ticks
    $recordedStartTicks = if ($null -ne $State.process_started_utc_ticks) {
        [long]$State.process_started_utc_ticks
    } else {
        ([DateTimeOffset]$State.process_started_utc).UtcDateTime.Ticks
    }
    if ($actualPath -ne [string]$State.executable_path -or $actualStartTicks -ne $recordedStartTicks) {
        throw "PID $($State.process_id) no longer matches the recorded MOSH process; refusing to stop it."
    }
    $process
}

function Get-ServiceStatus([string]$Name) {
    $state = Read-ServiceState $Name
    if ($null -eq $state) {
        return [ordered]@{ service = $Name; status = 'stopped'; managed = $false }
    }
    try {
        $process = Get-OwnedProcess $state
        if ($null -eq $process) {
            return [ordered]@{ service = $Name; status = 'stale'; managed = $true; process_id = [int]$state.process_id; port = [int]$state.port }
        }
        return [ordered]@{ service = $Name; status = 'running'; managed = $true; process_id = $process.Id; port = [int]$state.port }
    } catch {
        return [ordered]@{ service = $Name; status = 'ownership_mismatch'; managed = $false; process_id = [int]$state.process_id; port = [int]$state.port }
    }
}

function Stop-OwnedService([string]$Name) {
    $state = Read-ServiceState $Name
    if ($null -eq $state) {
        return [ordered]@{ service = $Name; status = 'not_managed' }
    }
    $process = Get-OwnedProcess $state
    if ($null -eq $process) {
        Remove-Item -LiteralPath (Get-StatePath $Name) -Force
        return [ordered]@{ service = $Name; status = 'stale_state_removed'; process_id = [int]$state.process_id }
    }

    Stop-Process -Id $process.Id -ErrorAction Stop
    $process.WaitForExit(5000) | Out-Null
    if (-not $process.HasExited) { throw "$Name process $($process.Id) did not stop within five seconds." }
    Remove-Item -LiteralPath (Get-StatePath $Name) -Force
    [ordered]@{ service = $Name; status = 'stopped'; process_id = $process.Id }
}

function Start-ManagedServices {
    $existing = @((Get-ServiceStatus 'dashboard'), (Get-ServiceStatus 'identity'))
    $blocked = @($existing | Where-Object { $_.status -ne 'stopped' -and $_.status -ne 'stale' })
    if ($blocked.Count -gt 0) {
        throw 'One or more services are already managed or have an ownership mismatch; inspect status before starting.'
    }
    foreach ($item in $existing | Where-Object status -eq 'stale') {
        Remove-Item -LiteralPath (Get-StatePath $item.service) -Force
    }

    $dashboardScript = Join-Path $PSScriptRoot 'Start-MoshDashboard.ps1'
    $identityScript = Join-Path $PSScriptRoot 'Start-MoshIdentity.ps1'
    $dashboard = $null
    try {
        $dashboard = (& $dashboardScript -Port $DashboardPort -NoBrowser) | ConvertFrom-Json
        Write-ServiceState 'dashboard' ([int]$dashboard.process_id) $DashboardPort
        $identity = (& $identityScript -Port $IdentityPort) | ConvertFrom-Json
        Write-ServiceState 'identity' ([int]$identity.process_id) $IdentityPort
    } catch {
        if ($null -ne $dashboard) {
            try { Stop-OwnedService 'dashboard' | Out-Null } catch { }
        }
        throw
    }

    if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$DashboardPort/" }
    @((Get-ServiceStatus 'dashboard'), (Get-ServiceStatus 'identity'))
}

$result = switch ($Action) {
    'status' {
        [ordered]@{ ok = $true; action = 'status'; services = @((Get-ServiceStatus 'dashboard'), (Get-ServiceStatus 'identity')) }
    }
    'start' {
        [ordered]@{ ok = $true; action = 'start'; services = @(Start-ManagedServices) }
    }
    'stop' {
        [ordered]@{ ok = $true; action = 'stop'; services = @((Stop-OwnedService 'identity'), (Stop-OwnedService 'dashboard')) }
    }
    'restart' {
        $stopped = @((Stop-OwnedService 'identity'), (Stop-OwnedService 'dashboard'))
        $started = @(Start-ManagedServices)
        [ordered]@{ ok = $true; action = 'restart'; stopped = $stopped; services = $started }
    }
}

$result | ConvertTo-Json -Depth 5 -Compress
