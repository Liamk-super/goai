Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-DemoRoot { return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }

function Import-DemoEnvironment([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Demo environment file not found: $Path" }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $parts = $trimmed.Split('=', 2)
        if ($parts.Count -ne 2 -or -not $parts[0].Trim()) { throw "Malformed environment entry in $Path" }
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1], 'Process')
    }
    if ([string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED')
    )) {
        [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED', 'true', 'Process')
    }
    if ([string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED')
    )) {
        [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED', 'true', 'Process')
    }
    if ([string]::IsNullOrWhiteSpace(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_REPORT_V3_ENABLED')
    )) {
        [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_REPORT_V3_ENABLED', 'true', 'Process')
    }
    $reportV3Enabled = [string]::Equals(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_REPORT_V3_ENABLED'),
        'true',
        [StringComparison]::OrdinalIgnoreCase
    )
    $reportV2Enabled = [string]::Equals(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_REPORT_V2_ENABLED'),
        'true',
        [StringComparison]::OrdinalIgnoreCase
    )
    $agentGeneration = if ($reportV3Enabled -or $reportV2Enabled) { 'v6' } elseif ([string]::Equals(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED'),
        'true',
        [StringComparison]::OrdinalIgnoreCase
    )) { 'v5' } else { 'v4' }
    [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_AGENT_GENERATION', $agentGeneration, 'Process')
    foreach ($default in @{
        RUN_PAUSE_CONTROL_ENABLED = 'true'
        MODEL_EGRESS_GATE_ENFORCED = 'true'
        DELIVERY_SCOPED_MODEL_TOKEN_ENABLED = 'true'
        MODEL_USAGE_LEDGER_MODE = 'GATEWAY_DELIVERY'
        LAUNCHSCOPE_MODEL_GATEWAY_PORT = '8092'
        LAUNCHSCOPE_MODEL_MAX_OUTPUT_TOKENS = '32768'
        LAUNCHSCOPE_MODEL_REQUEST_TIMEOUT_SECONDS = '3600'
        LAUNCHSCOPE_REPORT_RENDER_WEB_URL = 'http://127.0.0.1:3000'
    }.GetEnumerator()) {
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($default.Key))) {
            [Environment]::SetEnvironmentVariable($default.Key, $default.Value, 'Process')
        }
    }
}

function Initialize-DemoApplicationPorts([int]$WebPort = 0, [int]$OpsPort = 0) {
    if ($WebPort -eq 0) {
        $WebPort = if ([string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_WEB_PORT)) { 3000 } else { [int]$env:LAUNCHSCOPE_WEB_PORT }
    }
    if ($OpsPort -eq 0) {
        $OpsPort = if ([string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_OPS_PORT)) { 3001 } else { [int]$env:LAUNCHSCOPE_OPS_PORT }
    }
    if ($WebPort -lt 1 -or $WebPort -gt 65535 -or $OpsPort -lt 1 -or $OpsPort -gt 65535) {
        throw 'Demo Web and Ops ports must be between 1 and 65535'
    }
    if ($WebPort -eq $OpsPort) { throw 'Demo Web and Ops ports must be distinct' }
    $webOrigin = "http://127.0.0.1:$WebPort"
    $opsOrigin = "http://127.0.0.1:$OpsPort"
    [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_WEB_PORT', [string]$WebPort, 'Process')
    [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_OPS_PORT', [string]$OpsPort, 'Process')
    [Environment]::SetEnvironmentVariable('LAUNCHSCOPE_REPORT_RENDER_WEB_URL', $webOrigin, 'Process')
    [Environment]::SetEnvironmentVariable('MINIO_API_CORS_ALLOW_ORIGIN', $webOrigin, 'Process')
    foreach ($entry in @(
        [pscustomobject]@{ Name = 'LAUNCHSCOPE_DEMO_ORIGINS'; Required = @($webOrigin) },
        [pscustomobject]@{ Name = 'LAUNCHSCOPE_CORS_ORIGINS'; Required = @($webOrigin, $opsOrigin) }
    )) {
        $origins = [Collections.Generic.List[string]]::new()
        foreach ($origin in ([string][Environment]::GetEnvironmentVariable($entry.Name)).Split(',')) {
            if (-not [string]::IsNullOrWhiteSpace($origin) -and -not $origins.Contains($origin.Trim())) {
                $origins.Add($origin.Trim())
            }
        }
        foreach ($origin in $entry.Required) {
            if (-not $origins.Contains($origin)) { $origins.Add($origin) }
        }
        [Environment]::SetEnvironmentVariable($entry.Name, ($origins -join ','), 'Process')
    }
    return [pscustomobject]@{ WebPort = $WebPort; OpsPort = $OpsPort; WebOrigin = $webOrigin; OpsOrigin = $opsOrigin }
}

function Import-DemoEnvironmentValues([string]$Path, [string[]]$Names) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Demo environment file not found: $Path" }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $parts = $trimmed.Split('=', 2)
        if ($parts.Count -ne 2 -or -not $parts[0].Trim()) { throw "Malformed environment entry in $Path" }
        $name = $parts[0].Trim()
        if ($name -in $Names) { [Environment]::SetEnvironmentVariable($name, $parts[1], 'Process') }
    }
}

function Set-DemoEnvironmentValue([string]$Path, [string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'Demo environment values must be single-line named entries'
    }
    $lines = [Collections.Generic.List[string]]::new()
    $updated = $false
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            if ($line -match "^$([regex]::Escape($Name))=") {
                if (-not $updated) { $lines.Add("$Name=$Value"); $updated = $true }
            } else { $lines.Add($line) }
        }
    }
    if (-not $updated) { $lines.Add("$Name=$Value") }
    [IO.File]::WriteAllLines($Path, $lines, [Text.UTF8Encoding]::new($false))
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

function Test-TcpPort([string]$HostName, [int]$Port, [int]$TimeoutMs = 800) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.ConnectAsync($HostName, $Port)
        return $pending.Wait($TimeoutMs) -and $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Test-AgentTeamsCliAvailable {
    if (Get-Command agt -ErrorAction SilentlyContinue) { return $true }
    $controller = docker ps --filter 'name=^/agentteams-controller$' --format '{{.Names}}' 2>$null
    return $LASTEXITCODE -eq 0 -and $controller -eq 'agentteams-controller'
}

function Test-Supervisor1P4Enabled {
    return [string]::Equals(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED'),
        'true',
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Get-AgentTeamsGeneration {
    if ([string]::Equals(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_REPORT_V3_ENABLED'),
        'true',
        [StringComparison]::OrdinalIgnoreCase
    )) { return 'v6' }
    if ([string]::Equals(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_REPORT_V2_ENABLED'),
        'true',
        [StringComparison]::OrdinalIgnoreCase
    )) { return 'v6' }
    if ([string]::Equals(
        [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED'),
        'true',
        [StringComparison]::OrdinalIgnoreCase
    )) { return 'v5' }
    return 'v4'
}

function Get-AgentTeamsResourceRelativePath {
    return "infra/agentteams/resources/launchscope-team-$(Get-AgentTeamsGeneration).yaml"
}

function Get-AgentTeamsPackageDirectoryRelativePath {
    return "infra/agentteams/generated/packages-$(Get-AgentTeamsGeneration)"
}

function Get-AgentTeamsRenderedResourceName {
    return "launchscope-team-$(Get-AgentTeamsGeneration).rendered.yaml"
}

function Get-AgentTeamsExistingHumanResourceName {
    return "launchscope-team-$(Get-AgentTeamsGeneration).existing-human.yaml"
}

function Get-AgentTeamsHumanName {
    return "launchscope-human-coordinator-$(Get-AgentTeamsGeneration)-live"
}

function Get-AgentTeamsTeamName {
    return "launchscope-potential-review-$(Get-AgentTeamsGeneration)-operational"
}

function Get-AgentTeamsWorkerResourceMap {
    $generation = Get-AgentTeamsGeneration
    return [ordered]@{
        'evaluation-manager' = "launchscope-evaluation-supervisor-$generation-live"
        'product-engineering' = "launchscope-product-engineering-$generation-live"
        'user-evidence' = "launchscope-user-evidence-$generation-live"
        'business-investment' = "launchscope-business-investment-$generation-live"
        'evidence-auditor' = if ($generation -eq 'v4') { 'launchscope-evidence-auditor-v4-live-2' } else { "launchscope-evidence-auditor-$generation-live" }
    }
}

function Get-AgentTeamsWorkerMap {
    $workers = [ordered]@{}
    foreach ($entry in (Get-AgentTeamsWorkerResourceMap).GetEnumerator()) {
        $workers[$entry.Key] = "agentteams-worker-$($entry.Value)"
    }
    return $workers
}

function Test-AgentTeamsBridgeDirectoryMatchesGeneration {
    $raw = [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON')
    if ([string]::IsNullOrWhiteSpace($raw) -or $raw -eq '{}') { return $false }
    try { $directory = $raw | ConvertFrom-Json }
    catch { return $false }
    $properties = @($directory.PSObject.Properties)
    $expected = Get-AgentTeamsWorkerResourceMap
    if ($properties.Count -ne $expected.Count) { return $false }
    foreach ($entry in $expected.GetEnumerator()) {
        $matching = @($properties | Where-Object {
            $_.Value -eq $entry.Key -and $_.Name -match "^@$([regex]::Escape($entry.Value)):"
        })
        if ($matching.Count -ne 1) { return $false }
    }
    return $true
}

function Get-AgentTeamsWorkerConsoleEndpoints([switch]$RequireAll) {
    $workers = Get-AgentTeamsWorkerMap
    $endpoints = [ordered]@{}
    foreach ($entry in $workers.GetEnumerator()) {
        $published = @(docker port $entry.Value '8088/tcp' 2>$null | ForEach-Object { "$_" })
        $match = $published | Where-Object { $_ -match '127\.0\.0\.1:(\d+)$|0\.0\.0\.0:(\d+)$' } | Select-Object -First 1
        if ($match -and $match -match ':(\d+)$') {
            $endpoints[$entry.Key] = "http://127.0.0.1:$($Matches[1])"
        } elseif ($RequireAll) {
            throw "AgentTeams Worker console endpoint is unavailable: $($entry.Value)"
        }
    }
    return $endpoints
}

function Get-AgentTeamsUsageEndpointsJson([switch]$RequireAll) {
    $workers = Get-AgentTeamsWorkerConsoleEndpoints -RequireAll:$RequireAll
    $endpoints = [ordered]@{}
    foreach ($entry in $workers.GetEnumerator()) {
        $endpoints[$entry.Key] = "$($entry.Value)/api/token-usage"
    }
    return ($endpoints | ConvertTo-Json -Compress)
}

function Wait-AgentTeamsWorkerConsoles([int]$TimeoutSeconds = 120, [int]$StableSeconds = 0) {
    if ($TimeoutSeconds -lt 1 -or $StableSeconds -lt 0 -or $StableSeconds -ge $TimeoutSeconds) {
        throw 'AgentTeams Worker console wait bounds are invalid'
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $readySince = $null
    $headers = @{ 'X-Agent-Id' = 'default' }
    $expected = (Get-AgentTeamsWorkerMap).Count
    do {
        $ready = 0
        $workers = Get-AgentTeamsWorkerConsoleEndpoints
        foreach ($endpoint in $workers.Values) {
            try {
                Invoke-RestMethod -Method Get -Uri "$endpoint/api/agent/running-config" `
                    -Headers $headers -TimeoutSec 3 | Out-Null
                $ready += 1
            } catch { }
        }
        if ($ready -eq $expected) {
            if ($StableSeconds -eq 0) { return }
            if ($null -eq $readySince) { $readySince = Get-Date }
            if (((Get-Date) - $readySince).TotalSeconds -ge $StableSeconds) { return }
        } else {
            $readySince = $null
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "AgentTeams $(Get-AgentTeamsGeneration) Worker consoles did not become ready within $TimeoutSeconds seconds ($ready/$expected ready)"
}

function Wait-AgentTeamsTeamReady([int]$TimeoutSeconds = 120) {
    if ($TimeoutSeconds -lt 1) { throw 'AgentTeams Team wait timeout must be positive' }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $teamName = Get-AgentTeamsTeamName
    do {
        try {
            $payload = & (Join-Path (Get-DemoRoot) 'scripts/invoke-agentteams-cli.ps1') `
                @('get','teams','-o','json') | ConvertFrom-Json
            $teams = @($payload.teams | Where-Object name -eq $teamName)
            if ($teams.Count -eq 1 -and $teams[0].phase -eq 'Active' -and `
                -not [string]::IsNullOrWhiteSpace([string]$teams[0].teamRoomID) -and `
                -not [string]::IsNullOrWhiteSpace([string]$teams[0].leaderDMRoomID)) {
                return
            }
            if ($teams.Count -eq 1 -and $teams[0].phase -eq 'Failed') {
                throw "AgentTeams Team failed to become active: $($teams[0].message)"
            }
        } catch {
            if ($_.Exception.Message -like 'AgentTeams Team failed*') { throw }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "AgentTeams Team did not become active within $TimeoutSeconds seconds"
}

function Set-AgentTeamsActiveModel(
    [string]$ModelId,
    [string]$ProviderId = 'agentteams-gateway',
    [Collections.IDictionary]$ModelByWorker
) {
    if ([string]::IsNullOrWhiteSpace($ProviderId)) {
        throw 'AgentTeams provider ID must be non-empty'
    }
    $workers = Get-AgentTeamsWorkerConsoleEndpoints -RequireAll
    $headers = @{ 'X-Agent-Id' = 'default' }
    foreach ($entry in $workers.GetEnumerator()) {
        $targetModel = $ModelId
        if ($ModelByWorker -and $ModelByWorker.Contains($entry.Key)) {
            $targetModel = [string]$ModelByWorker[$entry.Key]
        }
        if ([string]::IsNullOrWhiteSpace($targetModel)) {
            throw "AgentTeams model ID must be non-empty for $($entry.Key)"
        }
        $uri = "$($entry.Value)/api/models/active"
        $current = Invoke-RestMethod -Method Get -Uri "${uri}?scope=effective" -Headers $headers -TimeoutSec 20
        if ($ProviderId -ne 'launchscope-model-egress' -and `
            $current.active_llm.provider_id -eq $ProviderId -and $current.active_llm.model -eq $targetModel) {
            continue
        }
        $providers = @(Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/models" -Headers $headers -TimeoutSec 20)
        $provider = @($providers | Where-Object { $_.id -eq $ProviderId })
        $supportedGateway = $ProviderId -in @('agentteams-gateway','launchscope-model-egress')
        $usingEgressGate = $ProviderId -eq 'launchscope-model-egress'
        if ($supportedGateway) {
            $baseUrl = if ($usingEgressGate) {
                "http://host.docker.internal:$($env:LAUNCHSCOPE_MODEL_GATEWAY_PORT)/v1"
            } else {
                [Environment]::GetEnvironmentVariable('AGENTTEAMS_MODEL_BASE_URL')
            }
            $apiKey = if ($usingEgressGate) {
                if ($env:DELIVERY_SCOPED_MODEL_TOKEN_ENABLED -ne 'true') {
                    throw 'Delivery-scoped model credentials must be enabled before AgentTeams model configuration'
                }
                'lsmg.v2.unassigned'
            } else {
                [Environment]::GetEnvironmentVariable('AGENTTEAMS_MODEL_API_KEY')
            }
            $parsedBaseUrl = $null
            $baseUrlValid = [uri]::TryCreate($baseUrl, [UriKind]::Absolute, [ref]$parsedBaseUrl) -and `
                ($parsedBaseUrl.Scheme -eq 'https' -or `
                    ($parsedBaseUrl.Scheme -eq 'http' -and $parsedBaseUrl.Host -in @('127.0.0.1','localhost','host.docker.internal')))
            if (-not $baseUrlValid -or [string]::IsNullOrWhiteSpace($apiKey)) {
                throw "AgentTeams gateway configuration is unavailable for $($entry.Key)"
            }
            if ($provider.Count -eq 0) {
                $providerBody = [ordered]@{
                    id = $ProviderId
                    name = if ($usingEgressGate) { 'LaunchScope Model Egress' } else { 'AgentTeams Gateway' }
                    default_base_url = $baseUrl
                    api_key_prefix = ''
                    chat_model = 'OpenAIChatModel'
                    models = @(@{ id = $targetModel; name = $targetModel })
                } | ConvertTo-Json -Depth 5 -Compress
                Invoke-RestMethod -Method Post -Uri "$($entry.Value)/api/models/custom-providers" `
                    -Headers $headers -ContentType 'application/json' -Body $providerBody -TimeoutSec 30 | Out-Null
            }
            $configBody = [ordered]@{
                api_key = $apiKey
                base_url = $baseUrl
                chat_model = 'OpenAIChatModel'
                generate_kwargs = @{}
            } | ConvertTo-Json -Depth 4 -Compress
            Invoke-RestMethod -Method Put -Uri "$($entry.Value)/api/models/$ProviderId/config" `
                -Headers $headers -ContentType 'application/json' -Body $configBody -TimeoutSec 30 | Out-Null
            $providers = @(Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/models" `
                -Headers $headers -TimeoutSec 20)
            $provider = @($providers | Where-Object { $_.id -eq $ProviderId })
        }
        if ($provider.Count -ne 1) {
            throw "CoPaw provider $ProviderId is unavailable for $($entry.Key)"
        }
        $knownModels = @($provider[0].models) + @($provider[0].extra_models)
        $modelBody = @{ id = $targetModel; name = $targetModel } | ConvertTo-Json -Compress
        if (@($knownModels | Where-Object { $_.id -eq $targetModel }).Count -eq 0) {
            Invoke-RestMethod -Method Post -Uri "$($entry.Value)/api/models/$ProviderId/models" `
                -Headers $headers -ContentType 'application/json' -Body $modelBody -TimeoutSec 30 | Out-Null
        }
        $modelRegistered = $false
        for ($attempt = 0; $attempt -lt 20; $attempt += 1) {
            $providers = @(Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/models" `
                -Headers $headers -TimeoutSec 20)
            $provider = @($providers | Where-Object { $_.id -eq $ProviderId })
            if ($provider.Count -eq 1) {
                $knownModels = @($provider[0].models) + @($provider[0].extra_models)
                if (@($knownModels | Where-Object { $_.id -eq $targetModel }).Count -gt 0) {
                    $modelRegistered = $true
                    break
                }
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $modelRegistered) {
            throw "CoPaw model $ProviderId/$targetModel did not become readable for $($entry.Key)"
        }
        foreach ($scope in @('global', 'agent')) {
            $payload = [ordered]@{
                provider_id = $ProviderId
                model = $targetModel
                scope = $scope
            }
            if ($scope -eq 'agent') { $payload.agent_id = 'default' }
            $body = $payload | ConvertTo-Json -Compress
            $updated = $null
            for ($attempt = 0; $attempt -lt 120; $attempt += 1) {
                try {
                    $updated = Invoke-RestMethod -Method Put -Uri $uri -Headers $headers `
                        -ContentType 'application/json' -Body $body -TimeoutSec 30
                    break
                }
                catch {
                    if ($attempt -ge 119 -or $_.ErrorDetails.Message -notmatch 'MODEL_NOT_FOUND') {
                        throw
                    }
                    Invoke-RestMethod -Method Post -Uri "$($entry.Value)/api/models/$ProviderId/models" `
                        -Headers $headers -ContentType 'application/json' -Body $modelBody -TimeoutSec 30 | Out-Null
                    Start-Sleep -Milliseconds 500
                }
            }
            if ($updated.active_llm.provider_id -ne $ProviderId -or $updated.active_llm.model -ne $targetModel) {
                throw "CoPaw active model did not persist for $($entry.Key) at $scope scope"
            }
        }
        $effective = Invoke-RestMethod -Method Get -Uri "${uri}?scope=effective" -Headers $headers -TimeoutSec 20
        if ($effective.active_llm.provider_id -ne $ProviderId -or $effective.active_llm.model -ne $targetModel) {
            throw "CoPaw effective model does not match the requested model for $($entry.Key)"
        }
    }
}

function Remove-AgentTeamsDirectModelProvider([string]$ProviderId = 'agentteams-gateway') {
    if ($ProviderId -ne 'agentteams-gateway') {
        throw 'Only the superseded LaunchScope direct provider may be removed by this safety function'
    }
    $headers = @{ 'X-Agent-Id' = 'default' }
    foreach ($entry in (Get-AgentTeamsWorkerConsoleEndpoints -RequireAll).GetEnumerator()) {
        $providers = @(Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/models" `
            -Headers $headers -TimeoutSec 20)
        if (@($providers | Where-Object { $_.id -eq $ProviderId }).Count -gt 0) {
            Invoke-RestMethod -Method Delete -Uri "$($entry.Value)/api/models/custom-providers/$ProviderId" `
                -Headers $headers -TimeoutSec 20 | Out-Null
        }
        $remaining = @(Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/models" `
            -Headers $headers -TimeoutSec 20)
        if (@($remaining | Where-Object { $_.id -eq $ProviderId }).Count -gt 0) {
            throw "Superseded direct model provider remains configured for $($entry.Key)"
        }
    }
}

function Get-LaunchScopeAgentConsoleEndpoints([switch]$IncludeLegacy) {
    $endpoints = [ordered]@{}
    foreach ($name in @(docker ps --format '{{.Names}}' | Where-Object {
        $_ -like 'agentteams-worker-launchscope*' -and ($IncludeLegacy -or $_ -like '*-v4')
    })) {
        $binding = docker inspect $name `
            --format '{{(index (index .NetworkSettings.Ports "8088/tcp") 0).HostPort}}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $binding -match '^\d+$') {
            $endpoints[$name] = "http://127.0.0.1:$binding"
        }
    }
    return $endpoints
}

function Sync-AgentTeamsPackageSkills([string]$PackageDirectory) {
    $resolvedPackageDirectory = (Resolve-Path -LiteralPath $PackageDirectory).Path
    $containers = Get-AgentTeamsWorkerMap
    foreach ($entry in (Get-AgentTeamsWorkerResourceMap).GetEnumerator()) {
        $agentCode = [string]$entry.Key
        $workerName = [string]$entry.Value
        $containerName = [string]$containers[$agentCode]
        $packagePath = Join-Path $resolvedPackageDirectory "$agentCode.zip"
        if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
            throw "AgentTeams package is missing for Skill sync: $packagePath"
        }
        $temporaryRoot = "/tmp/launchscope-package-sync-$([guid]::NewGuid().ToString('n'))"
        & docker exec $containerName mkdir -p "$temporaryRoot/package"
        if ($LASTEXITCODE -ne 0) { throw "Could not prepare Skill sync workspace for $workerName" }
        & docker cp $packagePath "${containerName}:$temporaryRoot/package.zip" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not copy the generated package for $workerName" }
        & docker exec $containerName python3 -m zipfile -e "$temporaryRoot/package.zip" "$temporaryRoot/package"
        if ($LASTEXITCODE -ne 0) { throw "Could not extract the generated package for $workerName" }
        & docker exec $containerName test -d "$temporaryRoot/package/skills"
        if ($LASTEXITCODE -ne 0) { throw "Generated package contains no Skill directory for $workerName" }

        $workerRoot = "/root/.copaw-worker/$workerName"
        foreach ($localSkills in @("$workerRoot/skills", "$workerRoot/.copaw/workspaces/default/skills")) {
            & docker exec $containerName mkdir -p $localSkills
            if ($LASTEXITCODE -ne 0) { throw "Could not prepare the live Skill directory for $workerName" }
            & docker exec $containerName cp -R "$temporaryRoot/package/skills/." "$localSkills/"
            if ($LASTEXITCODE -ne 0) { throw "Could not refresh the live Skills for $workerName" }
        }
        foreach ($remoteSkills in @(
            "agentteams/agentteams-storage/agents/$workerName/skills/",
            "agentteams/agentteams-storage/agents/$workerName/.copaw/workspaces/default/skills/"
        )) {
            & docker exec $containerName mc cp --recursive "$temporaryRoot/package/skills/" $remoteSkills | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Could not persist refreshed Skills for $workerName" }
        }
    }
}

function Set-AgentTeamsHeartbeatsDisabled([switch]$IncludeLegacy) {
    $headers = @{ 'X-Agent-Id' = 'default' }
    $body = @{ enabled = $false } | ConvertTo-Json -Compress
    foreach ($entry in (Get-LaunchScopeAgentConsoleEndpoints -IncludeLegacy:$IncludeLegacy).GetEnumerator()) {
        $configured = $false
        $lastError = $null
        for ($attempt = 1; $attempt -le 3; $attempt += 1) {
            try {
                Invoke-RestMethod -Method Put -Uri "$($entry.Value)/api/config/heartbeat" `
                    -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 20 | Out-Null
                $observed = Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/config/heartbeat" `
                    -Headers $headers -TimeoutSec 20
                if ($observed.enabled -ne $false) {
                    throw "CoPaw heartbeat remains enabled for $($entry.Key)"
                }
                $configured = $true
                break
            } catch {
                $lastError = $_.Exception.Message
                if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
            }
        }
        if (-not $configured) {
            throw "CoPaw heartbeat configuration failed for $($entry.Key) after 3 attempts: $lastError"
        }
    }
}

function Set-AgentTeamsRunningMaxIters([int]$MaxIters, [string[]]$AgentCodes = @()) {
    if ($MaxIters -lt 1 -or $MaxIters -gt 256) {
        throw 'CoPaw max iterations must be between 1 and 256'
    }
    $workers = Get-AgentTeamsWorkerConsoleEndpoints -RequireAll
    if ($AgentCodes.Count -gt 0) {
        $unknown = @($AgentCodes | Where-Object { -not $workers.Contains($_) })
        if ($unknown.Count -gt 0) {
            throw "Unknown AgentTeams Worker code: $($unknown -join ', ')"
        }
    }
    foreach ($entry in $workers.GetEnumerator()) {
        if ($AgentCodes.Count -gt 0 -and $entry.Key -notin $AgentCodes) { continue }
        $uri = "$($entry.Value)/api/agent/running-config"
        $headers = @{ 'X-Agent-Id' = 'default' }
        $running = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -TimeoutSec 20
        $running.max_iters = $MaxIters
        $running.memory_summary.memory_summary_enabled = $false
        $running.memory_summary.memory_prompt_enabled = $false
        $running | Add-Member -NotePropertyName llm_retry_enabled -NotePropertyValue $false -Force
        $running | Add-Member -NotePropertyName llm_max_concurrent -NotePropertyValue 1 -Force
        $running | Add-Member -NotePropertyName llm_max_qpm -NotePropertyValue 6 -Force
        $body = $running | ConvertTo-Json -Depth 12 -Compress
        $updated = Invoke-RestMethod -Method Put -Uri $uri -Headers $headers -ContentType 'application/json' `
            -Body $body -TimeoutSec 30
        if ([int]$updated.max_iters -ne $MaxIters) {
            throw "CoPaw running config did not persist for $($entry.Key)"
        }
        if ($updated.memory_summary.memory_summary_enabled -or $updated.memory_summary.memory_prompt_enabled) {
            throw "CoPaw cross-session memory remained enabled for $($entry.Key)"
        }
        if ($updated.llm_retry_enabled -ne $false -or [int]$updated.llm_max_concurrent -ne 1 -or `
            [int]$updated.llm_max_qpm -ne 6) {
            throw "CoPaw model retry or concurrency guard did not persist for $($entry.Key)"
        }
    }
}

function Start-DemoProcess(
    [string]$Name, [string]$FilePath, [string[]]$ArgumentList,
    [string]$WorkingDirectory, [string]$StateDirectory, [string]$LogDirectory
) {
    $stdout = Join-Path $LogDirectory "$Name.stdout.log"
    $stderr = Join-Path $LogDirectory "$Name.stderr.log"
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    $actualProcess = Get-Process -Id $process.Id -ErrorAction Stop
    $record = [ordered]@{
        name = $Name; pid = $process.Id; started_at = $process.StartTime.ToUniversalTime().ToString('o')
        executable = $actualProcess.Path; requested_executable = (Resolve-Path -LiteralPath $FilePath).Path
        marker = 'launchscope-local-demo'
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StateDirectory "$Name.pid.json") -Encoding utf8
}

function Assert-LocalDemo([switch]$RequireForce, [switch]$Force) {
    if ($env:LAUNCHSCOPE_ENV -ne 'local-demo') { throw 'Refusing operation: LAUNCHSCOPE_ENV must equal local-demo' }
    if ($RequireForce -and -not $Force) { throw 'Refusing destructive reset without explicit -Force' }
}
