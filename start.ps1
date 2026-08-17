[CmdletBinding()]
param(
    [ValidateSet('Recorded', 'Material', 'Live')]
    [string]$Mode = 'Live',
    [string]$EnvironmentFile = '.env.demo.local',
    [switch]$Bootstrap,
    [switch]$NoBrowser,
    [int]$WebPort = 0,
    [int]$OpsPort = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$bootstrapScript = Join-Path $root 'scripts/demo-bootstrap.ps1'
$startScript = Join-Path $root 'scripts/demo-start.ps1'
$stopScript = Join-Path $root 'scripts/demo-stop.ps1'
$commonScript = Join-Path $root 'scripts/demo-common.ps1'
$environmentPath = Join-Path $root $EnvironmentFile
$python = Join-Path $root '.venv/Scripts/python.exe'

function Assert-LaunchCommand([string]$Name, [string]$Command) {
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "$Name is required but was not found in PATH"
    }
}

function Get-LaunchScopeListeners([int[]]$Ports) {
    $portSet = [Collections.Generic.HashSet[int]]::new($Ports)
    foreach ($line in & netstat -ano -p tcp) {
        if ($line -match '^\s*TCP\s+127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
            $port = [int]$matches[1]
            if ($portSet.Contains($port)) {
                [pscustomobject]@{ port = $port; process_id = [int]$matches[2] }
            }
        }
    }
}

function Stop-ResidualLaunchScopeProcesses([int[]]$Ports) {
    $knownCommandPattern = 'launchscope_api\.(main:app|mcp:app|model_gateway:app|infrastructure\.messaging\.publisher_daemon)|launchscope_api\.modules\.(evaluation\.agentteams_daemon|project_dossier\.material_analysis_daemon)|@launchscope/(web|ops)'
    $knownProcesses = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and
        $_.CommandLine.Contains($root, [StringComparison]::OrdinalIgnoreCase) -and
        $_.CommandLine -match $knownCommandPattern
    })
    foreach ($process in $knownProcesses) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped residual LaunchScope process $($process.ProcessId)"
    }

    foreach ($listener in @(Get-LaunchScopeListeners $Ports)) {
        $cursor = [int]$listener.process_id
        $verified = [Collections.Generic.List[int]]::new()
        for ($depth = 0; $depth -lt 8 -and $cursor -gt 0; $depth += 1) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId=$cursor" -ErrorAction SilentlyContinue
            if (-not $process -or -not $process.CommandLine -or
                -not $process.CommandLine.Contains($root, [StringComparison]::OrdinalIgnoreCase)) {
                break
            }
            $verified.Add([int]$process.ProcessId)
            $cursor = [int]$process.ParentProcessId
        }
        for ($index = $verified.Count - 1; $index -ge 0; $index -= 1) {
            Stop-Process -Id $verified[$index] -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped residual LaunchScope listener $($verified[$index]) on port $($listener.port)"
        }
    }
}

Assert-LaunchCommand 'PowerShell 7' 'pwsh'
Assert-LaunchCommand 'Docker' 'docker'
Assert-LaunchCommand 'Node.js' 'node'
Assert-LaunchCommand 'pnpm' 'pnpm.cmd'

& docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Desktop is not running or the Docker engine is unavailable'
}

$agentTeamsAvailable = [bool](Get-Command agt -ErrorAction SilentlyContinue)
if (-not $agentTeamsAvailable -and $Mode -ne 'Recorded') {
    $controller = & docker ps --filter 'name=^/agentteams-controller$' --format '{{.Names}}' 2>$null
    $agentTeamsAvailable = $LASTEXITCODE -eq 0 -and $controller -eq 'agentteams-controller'
}

$needsBootstrap = $Bootstrap -or
    -not (Test-Path -LiteralPath $environmentPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $python -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $root 'node_modules') -PathType Container) -or
    ($Mode -ne 'Recorded' -and -not $agentTeamsAvailable) -or
    -not (Select-String -LiteralPath $environmentPath -Pattern '^LAUNCHSCOPE_MODEL_GATEWAY_SECRET=.{32,}$' -Quiet)

if ($needsBootstrap) {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Assert-LaunchCommand 'Python launcher' 'py'
    }
    Write-Host 'Preparing LaunchScope dependencies...'
    $bootstrapArguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $bootstrapScript,
        '-EnvironmentFile', $EnvironmentFile
    )
    if ($Mode -ne 'Recorded' -and -not $agentTeamsAvailable) {
        $bootstrapArguments += '-InstallAgentTeams'
    }
    & pwsh @bootstrapArguments
    if ($LASTEXITCODE -ne 0) { throw 'LaunchScope bootstrap failed' }
}

. $commonScript
Import-DemoEnvironment $environmentPath
$demoPorts = Initialize-DemoApplicationPorts -WebPort $WebPort -OpsPort $OpsPort
$defaultWorkspaceFile = if ([string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE)) {
    Join-Path $root '.demo/default-workspace.json'
} elseif ([IO.Path]::IsPathRooted($env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE)) {
    $env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE
} else {
    Join-Path $root $env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE
}
if (-not (Test-Path -LiteralPath $defaultWorkspaceFile -PathType Leaf)) {
    throw "The fixed Demo workspace binding is missing: $defaultWorkspaceFile"
}
$defaultWorkspaceBinding = Get-Content -LiteralPath $defaultWorkspaceFile -Raw | ConvertFrom-Json
if (-not [string]::IsNullOrWhiteSpace([string]$defaultWorkspaceBinding.databaseName)) {
    $env:DATABASE_URL = [regex]::Replace(
        $env:DATABASE_URL,
        '/[^/?]+(?=(?:\?|$))',
        "/$($defaultWorkspaceBinding.databaseName)"
    )
}
if (-not [string]::IsNullOrWhiteSpace([string]$defaultWorkspaceBinding.evidenceBucket)) {
    $env:LAUNCHSCOPE_EVIDENCE_BUCKET = [string]$defaultWorkspaceBinding.evidenceBucket
}
[Environment]::SetEnvironmentVariable('LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE', $defaultWorkspaceFile, 'Process')
$applicationPorts = @(8100, [int]$env:LAUNCHSCOPE_MCP_PORT, [int]$env:LAUNCHSCOPE_MODEL_GATEWAY_PORT, $demoPorts.WebPort, $demoPorts.OpsPort)
$occupiedApplicationPorts = @($applicationPorts |
    Where-Object { Test-TcpPort '127.0.0.1' $_ })
if ($occupiedApplicationPorts.Count -gt 0) {
    Write-Host "Restarting the existing LaunchScope instance on port(s): $($occupiedApplicationPorts -join ', ')"
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $stopScript -EnvironmentFile $EnvironmentFile -KeepInfrastructure
    if ($LASTEXITCODE -ne 0) { throw 'The existing LaunchScope instance could not be stopped safely' }
    Stop-ResidualLaunchScopeProcesses $applicationPorts
    Start-Sleep -Seconds 2
    $remainingPorts = @($applicationPorts | Where-Object { Test-TcpPort '127.0.0.1' $_ })
    if ($remainingPorts.Count -gt 0) {
        throw "Application port(s) remain occupied by an unverified process: $($remainingPorts -join ', ')"
    }
}

Write-Host "Starting LaunchScope in $Mode mode..."
$startArguments = @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $startScript,
    '-EnvironmentFile', $EnvironmentFile,
    '-WebPort', $demoPorts.WebPort,
    '-OpsPort', $demoPorts.OpsPort
)
switch ($Mode) {
    'Recorded' { $startArguments += '-RecordedOnly' }
    'Material' { $startArguments += '-MaterialOnly' }
}
& pwsh @startArguments
if ($LASTEXITCODE -ne 0) { throw 'LaunchScope startup failed' }

$modelGatewayHealth = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$($env:LAUNCHSCOPE_MODEL_GATEWAY_PORT)/healthz"
if ($modelGatewayHealth.status -ne 'ok' -or $modelGatewayHealth.egress_gate_enforced -ne $true) {
    throw 'The local model egress gate did not pass its strict health check'
}

$restored = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8100/api/v1/demo/default-session' -Headers @{
    Origin = $demoPorts.WebOrigin
    'X-Correlation-Id' = [guid]::NewGuid().ToString()
}
if ([string]::IsNullOrWhiteSpace([string]$restored.workspaceId)) {
    throw 'The fixed Demo workspace recovery endpoint returned no workspace'
}

if (-not $NoBrowser) {
    Start-Process "$($demoPorts.WebOrigin)/"
}
