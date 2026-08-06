param(
    [string]$EnvironmentFile = '.env.demo.local',
    [string]$JsonOutput = '.demo/preflight.json',
    [switch]$RequireExternalCase
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
Import-DemoEnvironment (Join-Path $root $EnvironmentFile)
$checks = [System.Collections.Generic.List[object]]::new()
function Add-Check([string]$Name, [string]$Status, [string]$Detail) {
    $checks.Add([ordered]@{ name=$Name; status=$Status; detail=$Detail })
}
function Command-Check([string]$Name, [string]$Command) {
    $found = Get-Command $Command -ErrorAction SilentlyContinue
    Add-Check $Name $(if($found){'PASS'}else{'FAIL'}) $(if($found){$found.Source}else{'not found'})
}
Command-Check 'PowerShell 7' 'pwsh'; Command-Check 'Docker' 'docker'; Command-Check 'Python' 'python'
Command-Check 'Node' 'node'; Command-Check 'pnpm' 'pnpm.cmd'; Command-Check 'AgentTeams CLI' 'agt'
if ($PSVersionTable.PSVersion.Major -lt 7) { Add-Check 'PowerShell version' 'FAIL' $PSVersionTable.PSVersion.ToString() }
else { Add-Check 'PowerShell version' 'PASS' $PSVersionTable.PSVersion.ToString() }
$drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).TrimEnd(':\'))
Add-Check 'Free disk >= 10 GB' $(if($drive.Free -ge 10GB){'PASS'}else{'FAIL'}) ("{0:N1} GB" -f ($drive.Free/1GB))
foreach($pair in @(@('Web',3000),@('Ops',3001),@('API',8100),@('PostgreSQL',[int]$env:POSTGRES_PORT),@('MinIO',[int]$env:MINIO_API_PORT),@('RocketMQ Proxy',[int]$env:ROCKETMQ_PROXY_PORT))) {
    Add-Check "$($pair[0]) port" $(if(Test-TcpPort '127.0.0.1' $pair[1]){'PASS'}else{'NOT_RUNNING'}) "127.0.0.1:$($pair[1])"
}
foreach($pair in @(
    @('AgentTeams Controller',[uri]$env:AGENTTEAMS_CONTROLLER_URL),
    @('AgentTeams Manager',[uri]$env:AGENTTEAMS_MANAGER_URL),
    @('Matrix',[uri]$env:AGENTTEAMS_MATRIX_URL),
    @('Element',[uri]$env:AGENTTEAMS_ELEMENT_URL)
)) {
    Add-Check $pair[0] $(if(Test-TcpPort $pair[1].Host $pair[1].Port){'PASS'}else{'NOT_RUNNING'}) "$($pair[1].Host):$($pair[1].Port)"
}
foreach($name in @('DATABASE_URL','POSTGRES_PASSWORD','LAUNCHSCOPE_S3_ACCESS_KEY','LAUNCHSCOPE_S3_SECRET_KEY','LAUNCHSCOPE_MCP_CONSUMER_TOKEN','LAUNCHSCOPE_AGENTTEAMS_BRIDGE_TOKEN')) {
    $present = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))
    Add-Check "Environment $name" $(if($present){'PASS'}else{'FAIL'}) $(if($present){'configured (redacted)'}else{'missing'})
}
[decimal]$budget = 0
$budgetValid = [decimal]::TryParse($env:LAUNCHSCOPE_RUN_BUDGET_USD, [ref]$budget) -and $budget -gt 0 -and $budget -le 20
Add-Check 'Run budget <= USD 20' $(if($budgetValid){'PASS'}else{'FAIL'}) $(if($budgetValid){"USD $budget"}else{'invalid or over limit'})
$external = -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_AUTHORIZED_CASE_URL) -and `
    -not [string]::IsNullOrWhiteSpace($env:TAVILY_API_KEY) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_MODEL_API_KEY) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_MODEL_BASE_URL) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_MODEL_ID) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_TEAM_ROOM_ID) -and `
    -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_HUMAN_ACCESS_TOKEN) -and `
    -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS) -and `
    $env:LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON -ne '{}' -and `
    -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_MODEL_INPUT_USD_PER_MILLION) -and `
    -not [string]::IsNullOrWhiteSpace($env:LAUNCHSCOPE_MODEL_OUTPUT_USD_PER_MILLION)
Add-Check 'Authorized live case' $(if($external){'PASS'}else{'BLOCKED_NO_AUTHORIZED_CASE'}) `
    $(if($external){'URL, model and search credentials configured (redacted)'}else{'authorized URL/model/search credentials incomplete'})
$python = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $python) {
    & $python (Join-Path $root 'scripts/build-agentteams-packages.py') --check
    Add-Check 'AgentTeams 1+5 contract/package drift' $(if($LASTEXITCODE -eq 0){'PASS'}else{'FAIL'}) `
        $(if($LASTEXITCODE -eq 0){'6 Workers, one leader, Human and tool allowlists validated'}else{'resource drift detected'})
    $migration = & $python -m alembic -c (Join-Path $root 'apps/api/alembic.ini') current 2>&1
    $migrationStatus = if($LASTEXITCODE -ne 0){'NOT_RUNNING'}elseif("$migration" -match '0014_v02_publisher_role'){'PASS'}else{'FAIL'}
    $migrationDetail = if($LASTEXITCODE -ne 0){'database unavailable'}elseif($migrationStatus -eq 'PASS'){"$migration".Trim()}else{"schema is behind head: $($migration.Trim())"}
    Add-Check 'Database migration head' $migrationStatus $migrationDetail
}
$agt = Get-Command agt -ErrorAction SilentlyContinue
if ($agt) {
    try {
        $workerJson = & $agt.Source get workers -o json 2>$null
        $workers = @($workerJson | ConvertFrom-Json)
        $launchscopeWorkers = @($workers | Where-Object { $_.metadata.name -like 'launchscope-*' })
        Add-Check 'Six AgentTeams Workers' $(if($launchscopeWorkers.Count -eq 6){'PASS'}else{'NOT_RUNNING'}) "$($launchscopeWorkers.Count) observed"
    } catch { Add-Check 'Six AgentTeams Workers' 'NOT_RUNNING' 'AgentTeams API unavailable' }
}
$result = [ordered]@{
    schema_version='launchscope.demo.preflight.v1'; generated_at=(Get-Date).ToUniversalTime().ToString('o')
    external_e2e_status=$(if($external){'READY'}else{'BLOCKED_NO_AUTHORIZED_CASE'}); checks=$checks
}
$output = Join-Path $root $JsonOutput
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $output) | Out-Null
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $output -Encoding utf8
$checks | Format-Table name,status,detail -AutoSize
Write-Host "Preflight JSON: $output"
if ($checks.status -contains 'FAIL' -or ($RequireExternalCase -and -not $external)) { exit 1 }
