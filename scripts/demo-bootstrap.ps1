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

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { py -3 -m venv (Join-Path $root '.venv') }
& $python -m pip install -e "$root[dev]" -e (Join-Path $root 'packages/domain') -e (Join-Path $root 'packages/contracts') `
    -e (Join-Path $root 'packages/skills') -e (Join-Path $root 'packages/observability') `
    -e (Join-Path $root 'apps/api') -e (Join-Path $root 'apps/orchestrator') -e (Join-Path $root 'apps/worker')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency bootstrap failed' }
Push-Location $root
try { & pnpm.cmd install --frozen-lockfile; if ($LASTEXITCODE -ne 0) { throw 'pnpm bootstrap failed' } }
finally { Pop-Location }
& $python (Join-Path $root 'scripts/build-agentteams-packages.py')

$installer = Join-Path $cache 'agentteams-install-v1.2.0.ps1'
$installerUrl = 'https://raw.githubusercontent.com/agentscope-ai/AgentTeams/v1.2.0/install/agentteams-install.ps1'
$expected = 'f46a6b0a4e676bf4557f83448bfdb59fdb872a01349a1320a1aedbdb2db7bb41'
Invoke-WebRequest -UseBasicParsing -Uri $installerUrl -OutFile $installer
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "AgentTeams installer hash mismatch; expected $expected, got $actual" }
if ($InstallAgentTeams) {
    Import-DemoEnvironment (Join-Path $root 'infra/agentteams/ports.env.example')
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw 'Official AgentTeams v1.2.0 installer failed' }
}
Write-Host "Bootstrap complete. AgentTeams installer verified: $actual"
