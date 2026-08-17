param(
    [string]$EnvironmentFile = '.env.demo.local',
    [string]$Output = 'deliverables/m7-b/runtime-evidence.json',
    [string]$Phase = 'unspecified'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
Import-DemoEnvironment (Join-Path $root $EnvironmentFile)

$generation = Get-AgentTeamsGeneration
$workerResources = Get-AgentTeamsWorkerResourceMap
$workerContainers = Get-AgentTeamsWorkerMap
$workerPayload = & (Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1') @('get','workers','-o','json') | ConvertFrom-Json
$teamPayload = & (Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1') @('get','teams','-o','json') | ConvertFrom-Json
$selectedWorkers = @($workerPayload.workers | Where-Object { $_.name -in @($workerResources.Values) })
$teamName = Get-AgentTeamsTeamName
$team = @($teamPayload.teams | Where-Object name -eq $teamName)
if ($selectedWorkers.Count -ne $workerResources.Count -or $team.Count -ne 1) {
    throw "Selected AgentTeams $generation topology is incomplete"
}

$runtimeModels = [ordered]@{}
foreach ($entry in $workerContainers.GetEnumerator()) {
    $published = @(docker port $entry.Value '8088/tcp' 2>$null | ForEach-Object { "$_" })
    $match = $published | Where-Object { $_ -match '127\.0\.0\.1:(\d+)$|0\.0\.0\.0:(\d+)$' } | Select-Object -First 1
    if (-not $match -or $match -notmatch ':(\d+)$') { throw "Worker console is unavailable: $($entry.Key)" }
    $endpoint = "http://127.0.0.1:$($Matches[1])"
    $headers = @{ 'X-Agent-Id' = 'default' }
    $active = Invoke-RestMethod -Method Get -Uri "$endpoint/api/models/active?scope=effective" `
        -Headers $headers -TimeoutSec 20
    $running = Invoke-RestMethod -Method Get -Uri "$endpoint/api/agent/running-config" `
        -Headers $headers -TimeoutSec 20
    $usage = Invoke-RestMethod -Method Get -Uri "$endpoint/api/token-usage" `
        -Headers $headers -TimeoutSec 20
    $role = $entry.Key.ToUpperInvariant().Replace('-','_')
    $override = [Environment]::GetEnvironmentVariable("AGENTTEAMS_MODEL_$role")
    $declaredModel = if([string]::IsNullOrWhiteSpace($override)){$env:AGENTTEAMS_MODEL_ID}else{$override}
    $image = docker inspect $entry.Value --format '{{.Config.Image}}|{{.Image}}'
    $runtimeModels[$entry.Key] = [ordered]@{
        worker_name = $workerResources[$entry.Key]
        container_name = $entry.Value
        declared_model = $declaredModel
        effective_provider = $active.active_llm.provider_id
        effective_model = $active.active_llm.model
        max_iters = [int]$running.max_iters
        memory_summary_enabled = [bool]$running.memory_summary.memory_summary_enabled
        memory_prompt_enabled = [bool]$running.memory_summary.memory_prompt_enabled
        usage = [ordered]@{
            total_prompt_tokens = [long]$usage.total_prompt_tokens
            total_completion_tokens = [long]$usage.total_completion_tokens
            total_calls = [long]$usage.total_calls
            by_model = $usage.by_model
            by_provider = $usage.by_provider
        }
        image = $image
    }
}

$workers = @($selectedWorkers | Sort-Object name | ForEach-Object {
    [ordered]@{
        name = $_.name
        phase = $_.phase
        matrix_user_id = $_.matrixUserID
        room_id = $_.roomID
    }
})
$directory = $env:LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON | ConvertFrom-Json
$rooms = $env:LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON | ConvertFrom-Json
$matrixDirectory = [ordered]@{}
foreach ($property in $directory.PSObject.Properties) { $matrixDirectory[$property.Name] = $property.Value }
$matrixRooms = [ordered]@{}
foreach ($property in $rooms.PSObject.Properties) { $matrixRooms[$property.Name] = $property.Value }
$databaseName = [regex]::Match($env:DATABASE_URL, '/([^/?]+)(?:\?.*)?$').Groups[1].Value
$resource = Join-Path $root (Get-AgentTeamsResourceRelativePath)
$packageRoot = Join-Path $root (Get-AgentTeamsPackageDirectoryRelativePath)
$packageHashes = [ordered]@{}
foreach ($package in Get-ChildItem -LiteralPath $packageRoot -Filter '*.zip' -File |
    Where-Object { $_.BaseName -notmatch '-[0-9a-f]{64}$' } | Sort-Object Name) {
    $packageHashes[$package.Name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $package.FullName).Hash.ToLowerInvariant()
}

$result = [ordered]@{
    schema_version = 'launchscope.m7b.runtime-evidence.v1'
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    phase = $Phase
    generation = $generation
    topology = [ordered]@{
        resource = (Get-AgentTeamsResourceRelativePath)
        resource_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $resource).Hash.ToLowerInvariant()
        expected_worker_count = $workerResources.Count
        team_name = $team[0].name
        team_phase = $team[0].phase
        team_room_id = $team[0].teamRoomID
        leader_name = $team[0].leaderName
        leader_ready = [bool]$team[0].leaderReady
        ready_domain_and_audit_workers = [int]$team[0].readyWorkers
        peer_mentions = $(if($generation -eq 'v4'){$false}else{$true})
        workers = $workers
    }
    runtime_models = $runtimeModels
    matrix = [ordered]@{
        mapped_identities = $matrixDirectory
        direct_rooms = $matrixRooms
    }
    isolation = [ordered]@{
        database = $databaseName
        evidence_bucket = $env:LAUNCHSCOPE_EVIDENCE_BUCKET
        rocketmq_topic = $env:LAUNCHSCOPE_ROCKETMQ_TOPIC
        rocketmq_consumer_group = $env:LAUNCHSCOPE_ROCKETMQ_CONSUMER_GROUP
    }
    external_case = [ordered]@{
        authorized_url = $env:LAUNCHSCOPE_AUTHORIZED_CASE_URL
        browser_allowed_domains = $env:LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS
        provider_usage_required = $env:LAUNCHSCOPE_REQUIRE_PROVIDER_USAGE -eq 'true'
        provider_cost_mode = $(if([string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_PROVIDER_COST_MODE)){'TOKEN_ONLY'}else{$env:LAUNCHSCOPE_PROVIDER_COST_MODE.ToUpperInvariant()})
        run_budget_usd = $env:LAUNCHSCOPE_RUN_BUDGET_USD
        model_input_usd_per_million = $env:LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION
        model_output_usd_per_million = $env:LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION
    }
    package_sha256 = $packageHashes
    redaction = 'No API key, token, password, private body, prompt, or model reasoning is exported.'
}
$outputPath = Join-Path $root $Output
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null
$result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $outputPath -Encoding utf8
Write-Host "Sanitized M7-B runtime evidence: $outputPath"
