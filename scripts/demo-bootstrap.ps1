param(
    [string]$EnvironmentFile = '.env.demo.local',
    [switch]$InstallAgentTeams
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
$environmentPath = Join-Path $root $EnvironmentFile
$template = Join-Path $root '.env.demo.example'
$state = Join-Path $root '.demo'
$cache = Join-Path $state 'cache'
New-Item -ItemType Directory -Force -Path $cache | Out-Null
if (-not (Test-Path -LiteralPath $environmentPath)) {
    Copy-Item -LiteralPath $template -Destination $environmentPath
    Write-Host "Created untracked $EnvironmentFile; fill local credentials before start."
}

Import-DemoEnvironment $environmentPath
function Ensure-LocalValue([string]$Name, [string]$Value) {
    $current = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($current)) { return }
    Add-Content -LiteralPath $environmentPath -Value "$Name=$Value" -Encoding utf8
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}
function Ensure-LocalSecret([string]$Name) {
    $current = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($current)) { return }
    $bytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $value = [Convert]::ToHexString($bytes).ToLowerInvariant()
    Add-Content -LiteralPath $environmentPath -Value "$Name=$value" -Encoding utf8
    [Environment]::SetEnvironmentVariable($Name, $value, 'Process')
}

# A minimal credentials-only overlay is convenient for hand setup. Complete it
# from the checked-in template and the ignored Compose environment without ever
# printing or replacing an existing value.
$composeValues = @{}
$composeEnvironment = Join-Path $root 'infra/compose/.env.local'
if (Test-Path -LiteralPath $composeEnvironment) {
    foreach ($line in Get-Content -LiteralPath $composeEnvironment) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $composeValues[$matches[1]] = $matches[2] }
    }
}
foreach ($line in Get-Content -LiteralPath $template) {
    if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
    $name = $matches[1]; $defaultValue = $matches[2]
    $value = if ($composeValues.ContainsKey($name)) { $composeValues[$name] } else { $defaultValue }
    if (-not [string]::IsNullOrWhiteSpace($value)) { Ensure-LocalValue $name $value }
}
foreach ($name in @(
    'LAUNCHSCOPE_MCP_CONSUMER_TOKEN',
    'LAUNCHSCOPE_MCP_CAPABILITY_SECRET',
    'LAUNCHSCOPE_AGENTTEAMS_BRIDGE_TOKEN',
    'LAUNCHSCOPE_MODEL_GATEWAY_SECRET'
)) {
    Ensure-LocalSecret $name
}
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL) -and -not [string]::IsNullOrWhiteSpace($env:POSTGRES_PASSWORD)) {
    Ensure-LocalValue 'DATABASE_URL' "postgresql+psycopg://$($env:POSTGRES_USER):$($env:POSTGRES_PASSWORD)@127.0.0.1:$($env:POSTGRES_PORT)/$($env:POSTGRES_DB)"
}
Ensure-LocalSecret 'AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN'
Ensure-LocalSecret 'AGENTTEAMS_MATRIX_APPSERVICE_HS_TOKEN'
Ensure-LocalSecret 'AGENTTEAMS_ADMIN_PASSWORD'

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { py -3 -m venv (Join-Path $root '.venv') }
& $python -m pip install -e "$root[dev]" -e (Join-Path $root 'packages/domain') -e (Join-Path $root 'packages/contracts') `
    -e (Join-Path $root 'packages/skills') -e (Join-Path $root 'packages/observability') `
    -e (Join-Path $root 'apps/api') -e (Join-Path $root 'apps/orchestrator') -e (Join-Path $root 'apps/worker')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency bootstrap failed' }
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Playwright Chromium bootstrap failed' }
Push-Location $root
try { & pnpm.cmd install --frozen-lockfile; if ($LASTEXITCODE -ne 0) { throw 'pnpm bootstrap failed' } }
finally { Pop-Location }
& $python (Join-Path $root 'scripts/build-agentteams-packages.py') --generation (Get-AgentTeamsGeneration)

$installer = Join-Path $cache 'agentteams-install-v1.2.0.ps1'
$installerUrl = 'https://raw.githubusercontent.com/agentscope-ai/AgentTeams/v1.2.0/install/agentteams-install.ps1'
$expected = 'f46a6b0a4e676bf4557f83448bfdb59fdb872a01349a1320a1aedbdb2db7bb41'
Invoke-WebRequest -UseBasicParsing -Uri $installerUrl -OutFile $installer
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "AgentTeams installer hash mismatch; expected $expected, got $actual" }
if ($InstallAgentTeams) {
    Import-DemoEnvironment (Join-Path $root 'infra/agentteams/ports.env.example')
    $compatInstaller = Join-Path $cache 'agentteams-install-v1.2.0-launchscope.ps1'
    $source = Get-Content -LiteralPath $installer -Raw
    $needle = '            "-e", "AGENTTEAMS_MANAGER_PASSWORD=$($config.MANAGER_PASSWORD)",'
    if (-not $source.Contains($needle)) { throw 'AgentTeams installer compatibility point was not found' }
    $injection = @'
            "-e", "AGENTTEAMS_MANAGER_PASSWORD=$($config.MANAGER_PASSWORD)",
            "-e", "AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN=$($env:AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN)",
            "-e", "AGENTTEAMS_MATRIX_APPSERVICE_HS_TOKEN=$($env:AGENTTEAMS_MATRIX_APPSERVICE_HS_TOKEN)",
'@
    $source.Replace($needle, $injection.TrimEnd()) | Set-Content -LiteralPath $compatInstaller -Encoding utf8
    $env:AGENTTEAMS_NON_INTERACTIVE = '1'
    $env:AGENTTEAMS_VERSION = 'v1.2.0'
    $env:AGENTTEAMS_ENV_FILE = (Join-Path $state 'agentteams-manager.env')
    $env:AGENTTEAMS_DATA_DIR = 'launchscope-agentteams-data'
    $env:AGENTTEAMS_WORKSPACE_DIR = (Join-Path $state 'agentteams-manager')
    $env:AGENTTEAMS_ADMIN_USER = 'admin'
    $env:AGENTTEAMS_LLM_PROVIDER = 'openai-compat'
    $env:AGENTTEAMS_DEFAULT_MODEL = $env:AGENTTEAMS_MODEL_ID
    $env:AGENTTEAMS_OPENAI_BASE_URL = "http://host.docker.internal:$($env:LAUNCHSCOPE_MODEL_GATEWAY_PORT)/v1"
    $env:AGENTTEAMS_LLM_API_KEY = 'lsmg.v2.unassigned'
    $env:AGENTTEAMS_LOCAL_ONLY = '1'
    $env:AGENTTEAMS_PORT_GATEWAY = $env:AGENTTEAMS_GATEWAY_PORT
    $env:AGENTTEAMS_PORT_ELEMENT_WEB = $env:AGENTTEAMS_ELEMENT_PORT
    $env:AGENTTEAMS_PORT_MANAGER_CONSOLE = $env:AGENTTEAMS_CONTROLLER_PORT
    $env:AGENTTEAMS_MATRIX_E2EE = '0'
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $compatInstaller -NonInteractive
    if ($LASTEXITCODE -ne 0) { throw 'Official AgentTeams v1.2.0 installer failed' }
}
Write-Host "Bootstrap complete. AgentTeams installer verified: $actual"
