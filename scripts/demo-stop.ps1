param([string]$EnvironmentFile = '.env.demo.local')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
Import-DemoEnvironment (Join-Path $root $EnvironmentFile)
Assert-LocalDemo
$state = Join-Path $root '.demo\run'
function Stop-VerifiedDescendants([int]$ParentPid) {
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentPid")
    foreach($child in $children) {
        Stop-VerifiedDescendants ([int]$child.ProcessId)
        if ($child.CommandLine -and $child.CommandLine.Contains($root,[StringComparison]::OrdinalIgnoreCase)) {
            Stop-Process -Id ([int]$child.ProcessId) -Force -ErrorAction SilentlyContinue
        } else {
            Write-Warning "Refusing to stop descendant PID $($child.ProcessId): command line is outside the Demo root"
        }
    }
}
if (Test-Path -LiteralPath $state) {
    foreach ($recordPath in Get-ChildItem -LiteralPath $state -Filter '*.pid.json' -File) {
        $record = Get-Content -LiteralPath $recordPath.FullName -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if (-not $process) { Remove-Item -LiteralPath $recordPath.FullName -Force; continue }
        $actualStart = $process.StartTime.ToUniversalTime()
        $recordedStart = ([datetime]$record.started_at).ToUniversalTime()
        $actualPath = $process.Path
        $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)").CommandLine
        $pathMatches = if ([string]::IsNullOrWhiteSpace([string]$record.executable)) {
            -not [string]::IsNullOrWhiteSpace([string]$record.requested_executable) -and
            (Test-Path -LiteralPath ([string]$record.requested_executable) -PathType Leaf)
        } else { $actualPath -eq $record.executable }
        if ([math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 1 -or -not $pathMatches -or $record.marker -ne 'launchscope-local-demo') {
            Write-Warning "PID $($record.pid) no longer matches $($record.name); refusing to stop it"
            continue
        }
        Stop-VerifiedDescendants $process.Id
        Stop-Process -Id $process.Id -Force
        Remove-Item -LiteralPath $recordPath.FullName -Force
        Write-Host "Stopped $($record.name) (PID $($process.Id))"
    }
}
$labelled = @(docker ps --filter 'label=com.launchscope.demo=agentteams-v1.2.0' --format '{{.Names}}')
foreach ($name in $labelled) { if ($name) { docker stop $name | Out-Null; Write-Host "Stopped labelled AgentTeams container $name" } }
Push-Location $root
try {
    & docker compose --env-file (Join-Path $root $EnvironmentFile) -f infra/compose/docker-compose.yml stop `
        postgres minio rocketmq-proxy rocketmq-broker rocketmq-namesrv
} finally { Pop-Location }
Write-Host 'Demo stopped. PostgreSQL, evidence, volumes and configuration were preserved.'
