param(
    [string]$EnvironmentFile = '.env.demo.local',
    [string]$JsonOutput = '.demo/preflight.json',
    [switch]$RequireExternalCase,
    [switch]$BootstrapMode,
    [int]$WebPort = 0,
    [int]$OpsPort = 0
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
Import-DemoEnvironment (Join-Path $root $EnvironmentFile)
$demoPorts = Initialize-DemoApplicationPorts -WebPort $WebPort -OpsPort $OpsPort
$defaultWorkspaceFile = if ([string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE)) {
    Join-Path $root '.demo/default-workspace.json'
} elseif ([IO.Path]::IsPathRooted($env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE)) {
    $env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE
} else {
    Join-Path $root $env:LAUNCHSCOPE_DEMO_DEFAULT_WORKSPACE_FILE
}
if (Test-Path -LiteralPath $defaultWorkspaceFile -PathType Leaf) {
    $defaultWorkspaceBinding = Get-Content -LiteralPath $defaultWorkspaceFile -Raw | ConvertFrom-Json
    if (-not [string]::IsNullOrWhiteSpace([string]$defaultWorkspaceBinding.databaseName)) {
        $env:DATABASE_URL = [regex]::Replace(
            $env:DATABASE_URL,
            '/[^/?]+(?=(?:\?|$))',
            "/$($defaultWorkspaceBinding.databaseName)"
        )
    }
}
$agentTeamsGeneration = Get-AgentTeamsGeneration
$agentTeamsWorkerMap = Get-AgentTeamsWorkerMap
$agentTeamsWorkerResourceMap = Get-AgentTeamsWorkerResourceMap
$expectedAgentTeamsWorkers = $agentTeamsWorkerMap.Count
$checks = [System.Collections.Generic.List[object]]::new()
function Add-Check([string]$Name, [string]$Status, [string]$Detail) {
    $checks.Add([pscustomobject][ordered]@{ name=$Name; status=$Status; detail=$Detail })
}
function Command-Check([string]$Name, [string]$Command) {
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    Add-Check $Name $(if($found){'PASS'}else{'FAIL'}) $(if($found){$found.Source}else{'not found'})
}
$featureFlagValue = [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED')
$featureFlagValid = [string]::IsNullOrWhiteSpace($featureFlagValue) -or $featureFlagValue -in @('true','false')
Add-Check 'Supervisor 1+4 feature flag' $(if($featureFlagValid){'PASS'}else{'FAIL'}) `
    $(if($featureFlagValid){"$agentTeamsGeneration selected"}else{'must be true, false, or unset'})
$deliveryTokensValid = $env:DELIVERY_SCOPED_MODEL_TOKEN_ENABLED -eq 'true'
$ledgerModeValid = $env:MODEL_USAGE_LEDGER_MODE -eq 'GATEWAY_DELIVERY'
Add-Check 'Delivery-scoped model control' $(if($deliveryTokensValid -and $ledgerModeValid){'PASS'}else{'FAIL'}) `
    $(if($deliveryTokensValid -and $ledgerModeValid){'delivery tokens and gateway ledger enabled for new Runs'}else{'DELIVERY_SCOPED_MODEL_TOKEN_ENABLED=true and MODEL_USAGE_LEDGER_MODE=GATEWAY_DELIVERY are required'})
Command-Check 'PowerShell 7' 'pwsh'; Command-Check 'Docker' 'docker'; Command-Check 'Python' 'python'
Command-Check 'Node' 'node'; Command-Check 'pnpm' 'pnpm.cmd'
Add-Check 'AgentTeams CLI' $(if(Test-AgentTeamsCliAvailable){'PASS'}else{'FAIL'}) `
    $(if(Get-Command agt -ErrorAction SilentlyContinue){'host agt'}elseif(Test-AgentTeamsCliAvailable){'agentteams-controller container'}else{'not found'})
if ($PSVersionTable.PSVersion.Major -lt 7) { Add-Check 'PowerShell version' 'FAIL' $PSVersionTable.PSVersion.ToString() }
else { Add-Check 'PowerShell version' 'PASS' $PSVersionTable.PSVersion.ToString() }
$drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).TrimEnd(':\'))
Add-Check 'Free disk >= 10 GB' $(if($drive.Free -ge 10GB){'PASS'}else{'FAIL'}) ("{0:N1} GB" -f ($drive.Free/1GB))
foreach($pair in @(
    @('Web',[int]$env:LAUNCHSCOPE_WEB_PORT),
    @('Ops',[int]$env:LAUNCHSCOPE_OPS_PORT),
    @('API',8100),
    @('Model egress',[int]$env:LAUNCHSCOPE_MODEL_GATEWAY_PORT),
    @('PostgreSQL',[int]$env:POSTGRES_PORT),
    @('MinIO',[int]$env:MINIO_API_PORT),
    @('RocketMQ Proxy',[int]$env:ROCKETMQ_PROXY_PORT)
)) {
    Add-Check "$($pair[0]) port" $(if(Test-TcpPort '127.0.0.1' $pair[1]){'PASS'}else{'NOT_RUNNING'}) "127.0.0.1:$($pair[1])"
}
foreach($endpoint in @(
    [pscustomobject]@{ Name='AgentTeams Controller'; Value=$env:AGENTTEAMS_CONTROLLER_URL },
    [pscustomobject]@{ Name='AgentTeams Manager'; Value=$env:AGENTTEAMS_MANAGER_URL },
    [pscustomobject]@{ Name='Matrix'; Value=$env:AGENTTEAMS_MATRIX_URL },
    [pscustomobject]@{ Name='Element'; Value=$env:AGENTTEAMS_ELEMENT_URL }
)) {
    $uri = $null
    $valid = [uri]::TryCreate([string]$endpoint.Value, [UriKind]::Absolute, [ref]$uri)
    if (-not $valid -or -not $uri.Host -or $uri.Port -le 0) {
        Add-Check $endpoint.Name 'FAIL' 'URL missing or invalid'
        continue
    }
    Add-Check $endpoint.Name $(if(Test-TcpPort $uri.Host $uri.Port){'PASS'}else{'NOT_RUNNING'}) "$($uri.Host):$($uri.Port)"
}
foreach($name in @(
    'DATABASE_URL',
    'POSTGRES_PASSWORD',
    'LAUNCHSCOPE_S3_ACCESS_KEY',
    'LAUNCHSCOPE_S3_SECRET_KEY',
    'LAUNCHSCOPE_MCP_CONSUMER_TOKEN',
    'LAUNCHSCOPE_AGENTTEAMS_BRIDGE_TOKEN',
    'LAUNCHSCOPE_MODEL_GATEWAY_SECRET'
)) {
    $present = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))
    Add-Check "Environment $name" $(if($present){'PASS'}else{'FAIL'}) $(if($present){'configured (redacted)'}else{'missing'})
}
[decimal]$budget = 0
$budgetValid = [decimal]::TryParse($env:LAUNCHSCOPE_RUN_BUDGET_USD, [ref]$budget) -and $budget -gt 0 -and $budget -le 20
Add-Check 'Run budget <= USD 20' $(if($budgetValid){'PASS'}else{'FAIL'}) $(if($budgetValid){"USD $budget"}else{'invalid or over limit'})
$costMode = if([string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_PROVIDER_COST_MODE)){'TOKEN_ONLY'}else{$env:LAUNCHSCOPE_PROVIDER_COST_MODE.Trim().ToUpperInvariant()}
$costModeValid = $costMode -in @('EXACT','TOKEN_ONLY')
$pricesConfigured = -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION) -and `
    -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION)
Add-Check 'Provider cost mode' $(if($costModeValid){'PASS'}else{'FAIL'}) `
    $(if($costModeValid){$costMode}else{'must be EXACT or TOKEN_ONLY'})
$external = -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_AUTHORIZED_CASE_URL) -and `
    -not [string]::IsNullOrWhiteSpace($env:TAVILY_API_KEY) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_MODEL_API_KEY) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_MODEL_BASE_URL) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_MODEL_ID) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_TEAM_ROOM_ID) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_HUMAN_ACCESS_TOKEN) -and `
    -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS) -and `
    $env:LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON -ne '{}' -and `
    $env:LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON -ne '{}' -and `
    $costModeValid -and ($costMode -eq 'TOKEN_ONLY' -or $pricesConfigured)
Add-Check 'Authorized live case' $(if($external){'PASS'}else{'BLOCKED_NO_AUTHORIZED_CASE'}) `
    $(if($external){"URL, model and search credentials configured; cost mode $costMode (redacted)"}else{'authorized URL/model/search credentials or cost-mode requirements incomplete'})
$bridgeGenerationMatches = Test-AgentTeamsBridgeDirectoryMatchesGeneration
Add-Check 'AgentTeams bridge generation' `
    $(if($bridgeGenerationMatches){'PASS'}elseif($BootstrapMode){'NOT_RUNNING'}else{'FAIL'}) `
    $(if($bridgeGenerationMatches){"Matrix directory matches $agentTeamsGeneration"}else{"bridge provisioning must refresh the Matrix directory for $agentTeamsGeneration"})
$materialOnly = $env:LAUNCHSCOPE_MATERIAL_ONLY -eq 'true'
Add-Check 'External research mode' `
    $(if($RequireExternalCase -and $materialOnly){'FAIL'}elseif($RequireExternalCase){'PASS'}else{'NOT_REQUIRED'}) `
    $(if($RequireExternalCase -and $materialOnly){'LAUNCHSCOPE_MATERIAL_ONLY must be false for an authorized external case'}elseif($RequireExternalCase){'browser/search tools enabled'}else{'material-only or recorded acceptance'})
$python = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $python) {
    & $python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" 2>$null
    $playwrightExit = $LASTEXITCODE
    Add-Check 'Playwright Chromium runtime' $(if($playwrightExit -eq 0){'PASS'}else{'FAIL'}) `
        $(if($playwrightExit -eq 0){'Python package and headless Chromium available'}else{'run demo-bootstrap.ps1'})
    & $python (Join-Path $root 'scripts/build-agentteams-packages.py') --check --generation $agentTeamsGeneration
    Add-Check "AgentTeams $agentTeamsGeneration contract/package drift" $(if($LASTEXITCODE -eq 0){'PASS'}else{'FAIL'}) `
        $(if($LASTEXITCODE -eq 0){"$expectedAgentTeamsWorkers Workers, one leader, Human and tool allowlists validated"}else{'resource drift detected'})
    Push-Location (Join-Path $root 'apps/api')
    try {
        $heads = @(& $python -m alembic -c 'alembic.ini' heads 2>&1)
        $headsExit = $LASTEXITCODE
    } finally { Pop-Location }
    $headsText = (($heads | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
    $revisionPattern = '(?m)^([0-9A-Za-z_]+)(?:\s+\(head\))?\s*$'
    $headRevision = [regex]::Match($headsText, $revisionPattern).Groups[1].Value
    if (Test-TcpPort '127.0.0.1' ([int]$env:POSTGRES_PORT)) {
        $migration = @(& $python -m alembic -c (Join-Path $root 'apps/api/alembic.ini') current 2>&1)
        $migrationExit = $LASTEXITCODE
        $migrationText = (($migration | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
        $currentRevision = [regex]::Match($migrationText, $revisionPattern).Groups[1].Value
        $migrationStatus = if($migrationExit -ne 0){'NOT_RUNNING'}elseif($headsExit -ne 0 -or -not $headRevision){'FAIL'}elseif($currentRevision -eq $headRevision){'PASS'}elseif($BootstrapMode){'NOT_RUNNING'}else{'FAIL'}
        $migrationDetail = if($migrationExit -ne 0){'database unavailable'}elseif($migrationStatus -eq 'PASS'){$migrationText}elseif($BootstrapMode){"startup will migrate schema to head $headRevision`: $migrationText"}else{"schema is behind head $headRevision`: $migrationText"}
    } else {
        $migrationStatus = 'NOT_RUNNING'
        $migrationDetail = 'database unavailable; startup will migrate after infrastructure is ready'
    }
    Add-Check 'Database migration head' $migrationStatus $migrationDetail
    if ($env:LAUNCHSCOPE_USER_VALIDATION_ENABLED -eq 'true') {
        if ($migrationStatus -eq 'PASS') {
            $uvdDrain = @(& $python (Join-Path $root 'scripts/check-user-validation-cutover.py') 2>&1)
            $uvdDrainExit = $LASTEXITCODE
            Add-Check 'UVD 1.0.4 drain before 1.0.5 cutover' $(if($uvdDrainExit -eq 0){'PASS'}else{'FAIL'}) `
                $(if($uvdDrainExit -eq 0){'no AWAITING_STEP or NEEDS_ATTENTION executions'}else{($uvdDrain -join ' ')})
        } else {
            Add-Check 'UVD 1.0.4 drain before 1.0.5 cutover' 'FAIL' 'database migration must be current before checking the drain gate'
        }
    } else {
        Add-Check 'UVD 1.0.4 drain before 1.0.5 cutover' 'NOT_REQUIRED' 'feature remains disabled'
    }
}
if (Test-AgentTeamsCliAvailable) {
    try {
        $workerJson = & (Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1') @('get','workers','-o','json') 2>$null
        $workerPayload = $workerJson | ConvertFrom-Json
        $workers = if ($null -ne $workerPayload.workers) { @($workerPayload.workers) } else { @($workerPayload) }
        $selectedNames = @($agentTeamsWorkerResourceMap.Values)
        $selectedWorkers = @($workers | Where-Object { $_.name -in $selectedNames })
        $runningWorkers = @($selectedWorkers | Where-Object { $_.phase -eq 'Running' })
        $workerStatus = if ($runningWorkers.Count -eq $expectedAgentTeamsWorkers -and `
            $selectedWorkers.Count -eq $expectedAgentTeamsWorkers) { 'PASS' } else { 'NOT_RUNNING' }
        $workerDetail = "$($runningWorkers.Count)/$expectedAgentTeamsWorkers selected $agentTeamsGeneration Workers Running"
        Add-Check "AgentTeams $agentTeamsGeneration Workers" $workerStatus $workerDetail
        try {
            $whoami = Invoke-RestMethod -Method Get `
                -Uri "$($env:AGENTTEAMS_MATRIX_URL.TrimEnd('/'))/_matrix/client/v3/account/whoami" `
                -Headers @{ Authorization = "Bearer $($env:AGENTTEAMS_HUMAN_ACCESS_TOKEN)" } -TimeoutSec 20
            if ([string]::IsNullOrWhiteSpace([string]$whoami.user_id)) { throw 'authenticated Human MXID is unavailable' }
            $directoryPayload = $env:LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON | ConvertFrom-Json
            $roomPayload = $env:LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON | ConvertFrom-Json
            $directoryByCode = @{}
            foreach ($property in $directoryPayload.PSObject.Properties) {
                $directoryByCode[[string]$property.Value] = [string]$property.Name
            }
            $roomByCode = @{}
            foreach ($property in $roomPayload.PSObject.Properties) {
                $roomByCode[[string]$property.Name] = [string]$property.Value
            }
            $invalidRooms = @()
            foreach ($agentCode in $agentTeamsWorkerMap.Keys) {
                $workerName = [string]$agentTeamsWorkerResourceMap[$agentCode]
                $worker = @($selectedWorkers | Where-Object name -eq $workerName)
                if ($worker.Count -ne 1 -or -not $directoryByCode.ContainsKey($agentCode) -or `
                    -not $roomByCode.ContainsKey($agentCode) -or `
                    [string]$directoryByCode[$agentCode] -ne [string]$worker[0].matrixUserID) {
                    $invalidRooms += $agentCode
                    continue
                }
                $encodedDispatchRoom = [uri]::EscapeDataString([string]$roomByCode[$agentCode])
                $members = Invoke-RestMethod -Method Get `
                    -Uri "$($env:AGENTTEAMS_MATRIX_URL.TrimEnd('/'))/_matrix/client/v3/rooms/$encodedDispatchRoom/joined_members" `
                    -Headers @{ Authorization = "Bearer $($env:AGENTTEAMS_HUMAN_ACCESS_TOKEN)" } -TimeoutSec 20
                $roomMembers = @($members.joined.PSObject.Properties.Name)
                $expectedMembers = @([string]$whoami.user_id, [string]$worker[0].matrixUserID)
                if ($roomMembers.Count -ne 2 -or @($expectedMembers | Where-Object { $_ -notin $roomMembers }).Count -ne 0) {
                    $invalidRooms += $agentCode
                }
            }
            Add-Check 'AgentTeams Matrix dispatch rooms' `
                $(if($invalidRooms.Count -eq 0){'PASS'}else{'NOT_RUNNING'}) `
                $(if($invalidRooms.Count -eq 0){"$expectedAgentTeamsWorkers/$expectedAgentTeamsWorkers stable rooms have exact Human and Worker membership"}else{"invalid: $($invalidRooms -join ', ')"})
        } catch {
            Add-Check 'AgentTeams Matrix dispatch rooms' 'NOT_RUNNING' 'Human identity or private-room membership unavailable'
        }
        try {
            $heartbeats = Get-LaunchScopeAgentConsoleEndpoints -IncludeLegacy
            $enabled = @($heartbeats.GetEnumerator() | Where-Object {
                $heartbeat = Invoke-RestMethod -Method Get -Uri "$($_.Value)/api/config/heartbeat" `
                    -Headers @{'X-Agent-Id'='default'} -TimeoutSec 10
                $heartbeat.enabled -ne $false
            })
            Add-Check 'AgentTeams heartbeats disabled' `
                $(if($enabled.Count -eq 0){'PASS'}elseif($BootstrapMode){'NOT_RUNNING'}else{'FAIL'}) `
                $(if($enabled.Count -eq 0){"$($heartbeats.Count) LaunchScope Agent endpoints disabled"}else{"enabled: $($enabled.Key -join ', ')"})
        } catch {
            Add-Check 'AgentTeams heartbeats disabled' 'NOT_RUNNING' 'could not read all Agent heartbeat settings'
        }
        try {
            $directWorkers = @()
            $unsafeRuntimeWorkers = @()
            foreach ($entry in (Get-AgentTeamsWorkerConsoleEndpoints -RequireAll).GetEnumerator()) {
                $active = Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/models/active?scope=effective" `
                    -Headers @{'X-Agent-Id'='default'} -TimeoutSec 10
                $providers = @(Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/models" `
                    -Headers @{'X-Agent-Id'='default'} -TimeoutSec 10)
                $hasSupersededDirectProvider = @($providers | Where-Object { $_.id -eq 'agentteams-gateway' }).Count -gt 0
                if ($active.active_llm.provider_id -ne 'launchscope-model-egress' -or $hasSupersededDirectProvider) {
                    $directWorkers += $entry.Key
                }
                $running = Invoke-RestMethod -Method Get -Uri "$($entry.Value)/api/agent/running-config" `
                    -Headers @{'X-Agent-Id'='default'} -TimeoutSec 10
                if ($running.llm_retry_enabled -ne $false -or [int]$running.llm_max_concurrent -ne 1 -or `
                    [int]$running.llm_max_qpm -ne 6) {
                    $unsafeRuntimeWorkers += $entry.Key
                }
            }
            Add-Check 'Strict model egress routing' $(if($directWorkers.Count -eq 0){'PASS'}else{'NOT_RUNNING'}) `
                $(if($directWorkers.Count -eq 0){'all v4 Workers use only launchscope-model-egress'}else{"not strictly gated: $($directWorkers -join ', ')"})
            Add-Check 'Worker model-call guards' `
                $(if($unsafeRuntimeWorkers.Count -eq 0){'PASS'}elseif($BootstrapMode){'NOT_RUNNING'}else{'FAIL'}) `
                $(if($unsafeRuntimeWorkers.Count -eq 0){'retry disabled, concurrency 1, QPM 6'}else{"unsafe runtime config: $($unsafeRuntimeWorkers -join ', ')"})
        } catch {
            Add-Check 'Strict model egress routing' 'NOT_RUNNING' 'Worker model configuration unavailable'
            Add-Check 'Worker model-call guards' 'NOT_RUNNING' 'Worker running configuration unavailable'
        }
        try {
            $usageEndpoints = Get-AgentTeamsUsageEndpointsJson -RequireAll | ConvertFrom-Json
            Add-Check 'Agent model usage receipts' 'PASS' `
                "$(@($usageEndpoints.psobject.Properties).Count)/$expectedAgentTeamsWorkers Worker counter endpoints discovered"
        } catch {
            Add-Check 'Agent model usage receipts' 'NOT_RUNNING' `
                'dedicated Worker token counters are unavailable'
        }
    } catch { Add-Check "AgentTeams $agentTeamsGeneration Workers" 'NOT_RUNNING' 'AgentTeams API unavailable' }
}
$result = [ordered]@{
    schema_version='launchscope.demo.preflight.v1'; generated_at=(Get-Date).ToUniversalTime().ToString('o')
    agentteams_generation=$agentTeamsGeneration; expected_worker_count=$expectedAgentTeamsWorkers
    resource_bundle=(Get-AgentTeamsResourceRelativePath)
    external_e2e_status=$(if($external){'READY'}else{'BLOCKED_NO_AUTHORIZED_CASE'}); checks=$checks
}
$output = Join-Path $root $JsonOutput
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $output -Encoding utf8
$checks | Format-Table name,status,detail -AutoSize
Write-Host "Preflight JSON: $output"
$notRunningBlocks = $RequireExternalCase -and -not $BootstrapMode -and $checks.status -contains 'NOT_RUNNING'
if ($checks.status -contains 'FAIL' -or $notRunningBlocks -or ($RequireExternalCase -and -not $external)) { exit 1 }
