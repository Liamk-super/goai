param(
    [Parameter(Mandatory = $true)]
    [string]$ExpectedModel,
    [string]$ArtifactRoot = 'artifacts/benchmarks/agentteams-runtime'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$outputRoot = Join-Path $repoRoot $ArtifactRoot
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$snapshotPath = Join-Path $outputRoot 'workers.json'
$identityPath = Join-Path $outputRoot 'runtime-identity.json'
$workerJson = & (Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1') @('get','workers','-o','json')
if ($LASTEXITCODE -ne 0) { throw 'Could not read live AgentTeams Worker status' }
$workerJson | Set-Content -LiteralPath $snapshotPath -Encoding utf8
$env:PYTHONPATH = "$(Join-Path $repoRoot 'packages\benchmark\src');$(Join-Path $repoRoot 'packages\observability\src')"
& $python -m launchscope_benchmark verify-worker-runtime $snapshotPath `
    --expected-model $ExpectedModel --output $identityPath
if ($LASTEXITCODE -ne 0) { throw 'AgentTeams runtime model identity is missing or mismatched' }
Write-Output "AGENTTEAMS_RUNTIME_MODEL_VERIFIED=$ExpectedModel"
