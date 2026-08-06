param([string]$EnvironmentFile = '.env.demo.local', [switch]$Force)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
Import-DemoEnvironment (Join-Path $root $EnvironmentFile)
Assert-LocalDemo -RequireForce -Force:$Force
$pidRecords = @(Get-ChildItem -LiteralPath (Join-Path $root '.demo\run') -Filter '*.pid.json' -File -ErrorAction SilentlyContinue)
foreach($recordPath in $pidRecords) {
    $record = Get-Content -LiteralPath $recordPath.FullName -Raw | ConvertFrom-Json
    if (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue) {
        throw "Refusing reset while Demo process $($record.name) is live; run demo-stop.ps1 first"
    }
}
Push-Location $root
try {
    $compose = @('compose','--env-file',(Join-Path $root $EnvironmentFile),'-f','infra/compose/docker-compose.yml')
    & docker @compose up -d --wait --wait-timeout 90 postgres
    if ($LASTEXITCODE -ne 0) { throw 'Cannot start PostgreSQL to prove reset safety' }
    $running = & docker @compose exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -Atc `
        "SELECT count(*) FROM evaluation_run WHERE status IN ('RUNNING','NEEDS_ATTENTION') OR last_failure_class='SUBMISSION_UNKNOWN';" 2>$null
    $claimed = & docker @compose exec -T postgres psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -Atc `
        "SELECT count(*) FROM outbox_message WHERE publish_status='CLAIMED';" 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot prove database reset safety; refusing reset' }
    if ([int]$running -gt 0 -or [int]$claimed -gt 0) {
        throw "Refusing reset: unsafe Run/claim state exists (runs=$running, claimed=$claimed)"
    }
    $rendered = Join-Path $root 'infra/agentteams/generated/launchscope-team.rendered.yaml'
    if ((Test-AgentTeamsCliAvailable) -and (Test-Path -LiteralPath $rendered)) {
        Push-Location (Join-Path $root 'infra/agentteams')
        try { & (Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1') @('delete','-f','generated/launchscope-team.rendered.yaml') }
        finally { Pop-Location }
    }
    & docker @compose down --volumes --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw 'Scoped Compose volume removal failed' }
    Write-Host 'Removed only LaunchScope Compose volumes and frozen 1+5 resources. AgentTeams installation/config remains.'
    Write-Host 'Run demo-start.ps1 to recreate migrations and resources; cached browser sessions will fail validation.'
} finally { Pop-Location }
