param([string]$EnvironmentFile = '.env.demo.local', [switch]$RecordedOnly)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
Import-DemoEnvironment (Join-Path $root $EnvironmentFile)
Assert-LocalDemo
if (-not $RecordedOnly) {
    & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'demo-preflight.ps1') `
        -EnvironmentFile $EnvironmentFile -RequireExternalCase
    if ($LASTEXITCODE -ne 0) { throw 'Live Demo preflight failed; use -RecordedOnly only for the labelled fallback' }
}
$state = Join-Path $root '.demo\run'; $logs = Join-Path $root '.demo\logs'
New-Item -ItemType Directory -Force -Path $state,$logs | Out-Null
$lockPath = Join-Path $state 'start.lock'
try { $lock = [IO.File]::Open($lockPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None) }
catch { throw 'LaunchScope Demo start is already running or a stale lock exists; inspect .demo/run/start.lock' }
try {
    Push-Location $root
    try {
        & docker compose --env-file (Join-Path $root $EnvironmentFile) -f infra/compose/docker-compose.yml up -d --wait --wait-timeout 180 `
            postgres minio rocketmq-namesrv rocketmq-broker rocketmq-proxy
        if ($LASTEXITCODE -ne 0) { throw 'Local infrastructure startup failed' }
        & docker compose --env-file (Join-Path $root $EnvironmentFile) -f infra/compose/docker-compose.yml `
            exec -T rocketmq-broker sh mqadmin updateTopic -n rocketmq-namesrv:9876 `
            -b rocketmq-broker:10911 -t $env:LAUNCHSCOPE_ROCKETMQ_TOPIC
        if ($LASTEXITCODE -ne 0) { throw 'RocketMQ topic creation failed' }
        & .venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
        if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
        & .venv\Scripts\python.exe scripts/build-agentteams-packages.py
        if ($LASTEXITCODE -ne 0) { throw 'AgentTeams package validation failed' }
        if (-not $RecordedOnly) {
            $agt = Get-Command agt -ErrorAction SilentlyContinue
            if (-not $agt) { throw 'agt CLI is required; run demo-bootstrap.ps1 -InstallAgentTeams first' }
            $resource = Get-Content -LiteralPath 'infra/agentteams/resources/launchscope-team.yaml' -Raw
            foreach($role in @('EVALUATION_MANAGER','PRODUCT_ENGINEERING','USER_EVIDENCE','BUSINESS_INVESTMENT','GEO_POLICY_TREND','EVIDENCE_AUDITOR')) {
                $override = [Environment]::GetEnvironmentVariable("AGENTTEAMS_MODEL_$role")
                $model = if([string]::IsNullOrWhiteSpace($override)){$env:AGENTTEAMS_MODEL_ID}else{$override}
                $resource = $resource.Replace("__LAUNCHSCOPE_MODEL_$($role)__", $model)
            }
            $rendered = Join-Path $root 'infra/agentteams/generated/launchscope-team.rendered.yaml'
            $resource | Set-Content -LiteralPath $rendered -Encoding utf8
            Push-Location (Join-Path $root 'infra/agentteams')
            try { & $agt.Source apply -f generated/launchscope-team.rendered.yaml }
            finally { Pop-Location }
            if ($LASTEXITCODE -ne 0) { throw 'Applying the frozen 1+5 AgentTeams resources failed' }
        }

        $python = Join-Path $root '.venv\Scripts\python.exe'
        Start-DemoProcess 'api' $python @('-m','uvicorn','launchscope_api.main:app','--host','127.0.0.1','--port','8100') $root $state $logs
        Start-DemoProcess 'mcp' $python @('-m','uvicorn','launchscope_api.mcp:app','--host','127.0.0.1','--port',$env:LAUNCHSCOPE_MCP_PORT) $root $state $logs
        Start-DemoProcess 'web' (Get-Command pnpm.cmd).Source @('--filter','@launchscope/web','dev') $root $state $logs
        Start-DemoProcess 'ops' (Get-Command pnpm.cmd).Source @('--filter','@launchscope/ops','dev') $root $state $logs
        if (-not $RecordedOnly) {
            Start-DemoProcess 'outbox-publisher' $python @('-m','launchscope_api.infrastructure.messaging.publisher_daemon') $root $state $logs
            Start-DemoProcess 'agentteams-dispatch-bridge' $python @('-m','launchscope_api.modules.evaluation.agentteams_daemon','dispatch-bridge') $root $state $logs
            Start-DemoProcess 'agentteams-matrix-listener' $python @('-m','launchscope_api.modules.evaluation.agentteams_daemon','matrix-listener') $root $state $logs
        }
    } finally { Pop-Location }
    $deadline = (Get-Date).AddSeconds(90)
    do {
        $ready = (Test-TcpPort '127.0.0.1' 8100) -and (Test-TcpPort '127.0.0.1' 3000) -and `
            (Test-TcpPort '127.0.0.1' 3001) -and (Test-TcpPort '127.0.0.1' ([int]$env:LAUNCHSCOPE_MCP_PORT))
        if (-not $ready) { Start-Sleep -Seconds 2 }
    } until ($ready -or (Get-Date) -ge $deadline)
    if (-not $ready) { throw "Demo processes did not become healthy; inspect $logs" }
    Write-Host 'LaunchScope Demo ready:'
    Write-Host '  Workspace  http://127.0.0.1:3000/demo-login'
    Write-Host '  Ops        http://127.0.0.1:3001/audit/events'
    Write-Host '  API        http://127.0.0.1:8100/docs'
    Write-Host "  MCP        http://127.0.0.1:$($env:LAUNCHSCOPE_MCP_PORT)"
    if ($RecordedOnly) { Write-Warning 'Recorded-only mode: no AgentTeams/RocketMQ bridge was started; use the labelled snapshot page only.' }
} finally {
    if ($lock) { $lock.Dispose() }
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
