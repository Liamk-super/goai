param(
    [string]$EnvironmentFile = '.env.demo.local',
    [switch]$RecordedOnly,
    [switch]$MaterialOnly,
    [int]$WebPort = 0,
    [int]$OpsPort = 0
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
Import-DemoEnvironment (Join-Path $root $EnvironmentFile)
$demoPorts = Initialize-DemoApplicationPorts -WebPort $WebPort -OpsPort $OpsPort
function Test-DemoHttpOk([string]$Uri) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 10
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}
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
[Environment]::SetEnvironmentVariable(
    'NEXT_PUBLIC_LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED',
    $(if(Test-Supervisor1P4Enabled){'true'}else{'false'}),
    'Process'
)
$executionMode = if ($RecordedOnly) { 'RECORDED' } elseif ($MaterialOnly) { 'MATERIAL' } else { 'LIVE' }
[Environment]::SetEnvironmentVariable('LAUNCHSCOPE_EXECUTION_MODE', $executionMode, 'Process')
[Environment]::SetEnvironmentVariable('NEXT_PUBLIC_LAUNCHSCOPE_EXECUTION_MODE', $executionMode, 'Process')
Assert-LocalDemo
[Environment]::SetEnvironmentVariable(
    'LAUNCHSCOPE_MATERIAL_ONLY',
    $(if($MaterialOnly){'true'}else{'false'}),
    'Process'
)
if (-not $RecordedOnly) {
    $preflightArguments = @(
        '-NoProfile','-File',(Join-Path $PSScriptRoot 'demo-preflight.ps1'),
        '-EnvironmentFile',$EnvironmentFile,'-WebPort',$demoPorts.WebPort,'-OpsPort',$demoPorts.OpsPort
    )
    $preflightArguments += '-BootstrapMode'
    if (-not $MaterialOnly) { $preflightArguments += '-RequireExternalCase' }
    & pwsh @preflightArguments
    if ($LASTEXITCODE -ne 0) { throw 'Live Demo preflight failed; use -MaterialOnly for an authorized private-material run or -RecordedOnly for the labelled fallback' }
}
$invalidRevisions = @(Get-ChildItem (Join-Path $root 'apps/api/migrations/versions') -Filter '*.py' -File |
    ForEach-Object {
        $match = [regex]::Match(
            (Get-Content -LiteralPath $_.FullName -Raw),
            '(?m)^revision\s*=\s*["'']([^"'']+)["'']'
        )
        if ($match.Success -and $match.Groups[1].Value.Length -gt 32) {
            [pscustomobject]@{ file = $_.Name; revision = $match.Groups[1].Value }
        }
    })
if ($invalidRevisions.Count -gt 0) {
    $details = ($invalidRevisions | ForEach-Object { "$($_.file): $($_.revision)" }) -join ', '
    throw "Alembic revision IDs must be 32 characters or fewer: $details"
}
$state = Join-Path $root '.demo\run'; $logs = Join-Path $root '.demo\logs'
New-Item -ItemType Directory -Force -Path $state,$logs | Out-Null
$readinessPath = Join-Path $state 'execution-readiness.json'
Remove-Item -LiteralPath $readinessPath -Force -ErrorAction SilentlyContinue
[Environment]::SetEnvironmentVariable('LAUNCHSCOPE_EXECUTION_READINESS_FILE', $readinessPath, 'Process')
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
        if (-not $RecordedOnly) {
            [Environment]::SetEnvironmentVariable('DELIVERY_SCOPED_MODEL_TOKEN_ENABLED', 'true', 'Process')
            [Environment]::SetEnvironmentVariable('MODEL_USAGE_LEDGER_MODE', 'GATEWAY_DELIVERY', 'Process')
            & .venv\Scripts\python.exe -m launchscope_api.modules.evaluation.agentteams_daemon reconcile-undelivered-once
            if ($LASTEXITCODE -ne 0) { throw 'Undelivered Task reconciliation failed before publisher startup' }
        }
        & .venv\Scripts\python.exe scripts/build-agentteams-packages.py --generation (Get-AgentTeamsGeneration)
        if ($LASTEXITCODE -ne 0) { throw 'AgentTeams package validation failed' }
        if (-not $RecordedOnly) {
            if (-not (Test-AgentTeamsCliAvailable)) { throw 'AgentTeams CLI is required; run demo-bootstrap.ps1 -InstallAgentTeams first' }
            [int]$copawMaxIters = 12
            if (-not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_COPAW_MAX_ITERS) -and `
                (-not [int]::TryParse($env:LAUNCHSCOPE_COPAW_MAX_ITERS, [ref]$copawMaxIters) -or `
                $copawMaxIters -lt 1 -or $copawMaxIters -gt 256)) {
                throw 'LAUNCHSCOPE_COPAW_MAX_ITERS must be an integer between 1 and 256'
            }
            $resourceRelativePath = Get-AgentTeamsResourceRelativePath
            $packageDirectoryRelativePath = Get-AgentTeamsPackageDirectoryRelativePath
            $packageResourcePath = $packageDirectoryRelativePath.Replace('infra/agentteams/', '')
            $resource = Get-Content -LiteralPath $resourceRelativePath -Raw
            foreach ($package in Get-ChildItem -LiteralPath $packageDirectoryRelativePath -Filter '*.zip' -File |
                Where-Object { $_.BaseName -notmatch '-[0-9a-f]{64}$' }) {
                $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $package.FullName).Hash.ToLowerInvariant()
                $versionedName = "$($package.BaseName)-$digest.zip"
                $versionedPath = Join-Path $package.DirectoryName $versionedName
                Copy-Item -LiteralPath $package.FullName -Destination $versionedPath -Force
                $resource = $resource.Replace(
                    "file://./$packageResourcePath/$($package.Name)",
                    "file://./$packageResourcePath/$versionedName"
                )
            }
            $resource = $resource.Replace('__LAUNCHSCOPE_COPAW_MAX_ITERS__', [string]$copawMaxIters)
            [int]$userCopawMaxIters = $copawMaxIters
            if ($env:LAUNCHSCOPE_USER_VALIDATION_ENABLED -eq 'true') {
                $userCopawMaxIters = 32
            }
            if (-not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_USER_COPAW_MAX_ITERS) -and `
                (-not [int]::TryParse($env:LAUNCHSCOPE_USER_COPAW_MAX_ITERS, [ref]$userCopawMaxIters) -or `
                $userCopawMaxIters -lt 1 -or $userCopawMaxIters -gt 256)) {
                throw 'LAUNCHSCOPE_USER_COPAW_MAX_ITERS must be an integer between 1 and 256'
            }
            $resource = $resource.Replace('__LAUNCHSCOPE_USER_COPAW_MAX_ITERS__', [string]$userCopawMaxIters)
            $runtimeModels = [ordered]@{}
            foreach($agentCode in (Get-AgentTeamsWorkerMap).Keys) {
                $role = $agentCode.ToUpperInvariant().Replace('-','_')
                $override = [Environment]::GetEnvironmentVariable("AGENTTEAMS_MODEL_$role")
                $model = if([string]::IsNullOrWhiteSpace($override)){$env:AGENTTEAMS_MODEL_ID}else{$override}
                $resource = $resource.Replace("__LAUNCHSCOPE_MODEL_$($role)__", $model)
                $runtimeModels[$agentCode] = $model
            }
            $rendered = Join-Path $root "infra/agentteams/generated/$(Get-AgentTeamsRenderedResourceName)"
            $resource | Set-Content -LiteralPath $rendered -Encoding utf8
            $humanPayload = & (Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1') @('get','humans','-o','json') | ConvertFrom-Json
            $applyPath = $rendered
            $humanName = Get-AgentTeamsHumanName
            if (@($humanPayload.humans | Where-Object name -eq $humanName).Count -gt 0) {
                $documents = @($resource -split '(?m)^---\s*$' | Where-Object { $_ -notmatch '(?m)^kind:\s*Human\s*$' })
                $applyPath = Join-Path $root "infra/agentteams/generated/$(Get-AgentTeamsExistingHumanResourceName)"
                ($documents -join "`n---`n") | Set-Content -LiteralPath $applyPath -Encoding utf8
            }
            Push-Location (Join-Path $root 'infra/agentteams')
            try { & (Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1') @('apply','-f',(Resolve-Path -Relative $applyPath)) }
            finally { Pop-Location }
            if ($LASTEXITCODE -ne 0) { throw "Applying the $(Get-AgentTeamsGeneration) AgentTeams resources failed" }
            Wait-AgentTeamsWorkerConsoles -TimeoutSeconds 600 -StableSeconds 30
            Wait-AgentTeamsTeamReady -TimeoutSeconds 600
            Sync-AgentTeamsPackageSkills -PackageDirectory $packageDirectoryRelativePath
            Set-AgentTeamsHeartbeatsDisabled -IncludeLegacy
            Set-AgentTeamsActiveModel -ProviderId 'launchscope-model-egress' -ModelByWorker $runtimeModels
            Remove-AgentTeamsDirectModelProvider
            & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'demo-provision-agentteams.ps1') `
                -EnvironmentFile $EnvironmentFile
            $provisionExitCode = $LASTEXITCODE
            Import-DemoEnvironmentValues -Path (Join-Path $root $EnvironmentFile) -Names @(
                'AGENTTEAMS_TEAM_ROOM_ID',
                'AGENTTEAMS_LEADER_ROOM_ID',
                'AGENTTEAMS_HUMAN_ACCESS_TOKEN',
                'LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON',
                'LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON'
            )
            if ($provisionExitCode -ne 0 -or -not (Test-AgentTeamsBridgeDirectoryMatchesGeneration)) {
                throw "AgentTeams $(Get-AgentTeamsGeneration) Matrix bridge provisioning failed"
            }
            Set-AgentTeamsRunningMaxIters -MaxIters $copawMaxIters
            if ($userCopawMaxIters -ne $copawMaxIters) {
                Set-AgentTeamsRunningMaxIters -MaxIters $userCopawMaxIters -AgentCodes @('user-evidence')
            }
            $usageEndpoints = Get-AgentTeamsUsageEndpointsJson -RequireAll
            [Environment]::SetEnvironmentVariable(
                'LAUNCHSCOPE_AGENT_USAGE_ENDPOINTS_JSON', $usageEndpoints, 'Process'
            )
            $workerEndpoints = Get-AgentTeamsWorkerConsoleEndpoints -RequireAll | ConvertTo-Json -Compress
            [Environment]::SetEnvironmentVariable(
                'LAUNCHSCOPE_WORKER_CONSOLE_ENDPOINTS_JSON', $workerEndpoints, 'Process'
            )
            $workerResources = Get-AgentTeamsWorkerResourceMap | ConvertTo-Json -Compress
            [Environment]::SetEnvironmentVariable(
                'LAUNCHSCOPE_AGENTTEAMS_WORKER_NAMES_JSON', $workerResources, 'Process'
            )
        }

        foreach ($port in @(8100, [int]$env:LAUNCHSCOPE_MCP_PORT, [int]$env:LAUNCHSCOPE_MODEL_GATEWAY_PORT, $demoPorts.WebPort, $demoPorts.OpsPort)) {
            if (Test-TcpPort '127.0.0.1' $port) {
                throw "Refusing to start over an occupied Demo application port: 127.0.0.1:$port"
            }
        }

        $expectedProcessNames = @('api', 'mcp', 'model-gateway', 'web', 'ops')
        if (-not $RecordedOnly) {
            $expectedProcessNames += @(
                'outbox-publisher',
                'agentteams-dispatch-bridge',
                'agentteams-matrix-listener'
            )
        }
        $python = Join-Path $root '.venv\Scripts\python.exe'
        $gatewayUpstreamBaseUrl = if (-not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL)) {
            $env:LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL
        } else {
            $env:AGENTTEAMS_MODEL_BASE_URL
        }
        $gatewayUpstreamApiKey = if (-not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY)) {
            $env:LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY
        } else {
            $env:AGENTTEAMS_MODEL_API_KEY
        }
        foreach ($name in @(
            'LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL',
            'LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY',
            'LAUNCHSCOPE_INTAKE_MODEL_BASE_URL',
            'LAUNCHSCOPE_INTAKE_MODEL_API_KEY',
            'AGENTTEAMS_MODEL_BASE_URL',
            'AGENTTEAMS_MODEL_API_KEY'
        )) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        $intakeGatewayToken = [string](& $python -m launchscope_api.model_gateway issue-intake-token | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($intakeGatewayToken)) {
            throw 'Failed to issue the API intake model gateway credential'
        }
        try {
            [Environment]::SetEnvironmentVariable(
                'LAUNCHSCOPE_INTAKE_MODEL_BASE_URL',
                "http://127.0.0.1:$($env:LAUNCHSCOPE_MODEL_GATEWAY_PORT)/v1/intake",
                'Process'
            )
            [Environment]::SetEnvironmentVariable(
                'LAUNCHSCOPE_INTAKE_MODEL_API_KEY', $intakeGatewayToken, 'Process'
            )
            Start-DemoProcess 'api' $python @(
                '-m','uvicorn','launchscope_api.main:app','--host','127.0.0.1','--port','8100'
            ) $root $state $logs
        } finally {
            [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_INTAKE_MODEL_BASE_URL', $null, 'Process')
            [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_INTAKE_MODEL_API_KEY', $null, 'Process')
        }
        Start-DemoProcess 'mcp' $python @('-m','uvicorn','launchscope_api.mcp:app','--host','127.0.0.1','--port',$env:LAUNCHSCOPE_MCP_PORT) $root $state $logs
        try {
            [Environment]::SetEnvironmentVariable(
                'LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL', $gatewayUpstreamBaseUrl, 'Process'
            )
            [Environment]::SetEnvironmentVariable(
                'LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY', $gatewayUpstreamApiKey, 'Process'
            )
            Start-DemoProcess 'model-gateway' $python @(
                '-m','uvicorn','launchscope_api.model_gateway:app','--host','127.0.0.1',
                '--port',$env:LAUNCHSCOPE_MODEL_GATEWAY_PORT
            ) $root $state $logs
        } finally {
            [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_MODEL_UPSTREAM_BASE_URL', $null, 'Process')
            [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_MODEL_UPSTREAM_API_KEY', $null, 'Process')
        }
        foreach ($nextCachePath in @(
            (Join-Path $root 'apps/web/.next-demo'),
            (Join-Path $root 'apps/ops/.next')
        )) {
            if (Test-Path -LiteralPath $nextCachePath -PathType Container) {
                Remove-Item -LiteralPath $nextCachePath -Recurse -Force
            }
        }
        try {
            [Environment]::SetEnvironmentVariable('NEXT_DIST_DIR', '.next-demo', 'Process')
            Start-DemoProcess 'web' (Get-Command pnpm.cmd).Source `
                @('--config.verify-deps-before-run=false','--filter','@launchscope/web','exec','next','dev','--hostname','127.0.0.1','--port',[string]$demoPorts.WebPort) $root $state $logs
        } finally {
            [Environment]::SetEnvironmentVariable('NEXT_DIST_DIR', $null, 'Process')
        }
        Start-DemoProcess 'ops' (Get-Command pnpm.cmd).Source `
            @('--config.verify-deps-before-run=false','--filter','@launchscope/ops','exec','next','dev','--hostname','127.0.0.1','--port',[string]$demoPorts.OpsPort) $root $state $logs
        if (-not $RecordedOnly) {
            Start-DemoProcess 'outbox-publisher' $python @('-m','launchscope_api.infrastructure.messaging.publisher_daemon') $root $state $logs
            if ($env:LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED -eq 'true') {
                Start-DemoProcess 'material-analysis-worker' $python `
                    @('-m','launchscope_api.modules.project_dossier.material_analysis_daemon') $root $state $logs
            }
            Start-DemoProcess 'agentteams-dispatch-bridge' $python @('-m','launchscope_api.modules.evaluation.agentteams_daemon','dispatch-bridge') $root $state $logs
            Start-DemoProcess 'agentteams-matrix-listener' $python @('-m','launchscope_api.modules.evaluation.agentteams_daemon','matrix-listener') $root $state $logs
        }
    } finally { Pop-Location }
    $deadline = (Get-Date).AddSeconds(180)
    do {
        $ready = (Test-TcpPort '127.0.0.1' ([int]$env:LAUNCHSCOPE_MCP_PORT)) -and `
            (Test-DemoHttpOk 'http://127.0.0.1:8100/docs') -and `
            (Test-DemoHttpOk "$($demoPorts.WebOrigin)/") -and `
            (Test-DemoHttpOk "$($demoPorts.OpsOrigin)/audit/events") -and `
            (Test-DemoHttpOk "http://127.0.0.1:$($env:LAUNCHSCOPE_MODEL_GATEWAY_PORT)/healthz")
        if (-not $ready) { Start-Sleep -Seconds 2 }
    } until ($ready -or (Get-Date) -ge $deadline)
    if (-not $ready) { throw "Demo processes did not become healthy; inspect $logs" }
    if (-not $RecordedOnly) {
        Set-AgentTeamsRunningMaxIters -MaxIters $copawMaxIters
        if ($userCopawMaxIters -ne $copawMaxIters) {
            Set-AgentTeamsRunningMaxIters -MaxIters $userCopawMaxIters -AgentCodes @('user-evidence')
        }
    }
    $deadRecords = @(Get-ChildItem -LiteralPath $state -Filter '*.pid.json' -File | Where-Object {
        $record = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
        $expectedProcessNames -contains $record.name -and
            -not (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue)
    })
    if ($deadRecords.Count -gt 0) { throw "A Demo launcher exited before readiness: $($deadRecords.Name -join ', ')" }
    $processRecords = @($expectedProcessNames | ForEach-Object {
        $recordPath = Join-Path $state "$_.pid.json"
        if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) {
            throw "A Demo launcher has no process record: $_"
        }
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        if (-not (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue)) {
            throw "A Demo launcher exited before readiness: $_"
        }
        $record
    })
    $executionProcesses = @($processRecords | Where-Object {
        $_.name -in @('outbox-publisher', 'agentteams-dispatch-bridge', 'agentteams-matrix-listener')
    } | ForEach-Object { [ordered]@{ name = $_.name; pid = [int]$_.pid } })
    [ordered]@{
        mode = $executionMode
        dispatch_enabled = -not $RecordedOnly
        processes = $executionProcesses
        established_at = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $readinessPath -Encoding utf8
    $restored = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8100/api/v1/demo/default-session' -Headers @{
        Origin = $demoPorts.WebOrigin
        'X-Correlation-Id' = [guid]::NewGuid().ToString()
    }
    if ([string]::IsNullOrWhiteSpace([string]$restored.workspaceId)) {
        throw 'The fixed Demo workspace recovery endpoint returned no workspace'
    }
    Write-Host 'LaunchScope Demo ready:'
    Write-Host "  Workspace  $($demoPorts.WebOrigin)/"
    Write-Host "  Ops        $($demoPorts.OpsOrigin)/audit/events"
    Write-Host '  API        http://127.0.0.1:8100/docs'
    Write-Host "  MCP        http://127.0.0.1:$($env:LAUNCHSCOPE_MCP_PORT)"
    Write-Host "  Model gate http://127.0.0.1:$($env:LAUNCHSCOPE_MODEL_GATEWAY_PORT)/healthz"
    if ($RecordedOnly) { Write-Warning 'Recorded-only mode: no AgentTeams/RocketMQ bridge was started; use the labelled snapshot page only.' }
    elseif ($MaterialOnly) { Write-Warning 'Material-only live mode: AgentTeams is real, but public research remains fail-closed until authorized URL/search credentials are configured.' }
} finally {
    if ($lock) { $lock.Dispose() }
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
