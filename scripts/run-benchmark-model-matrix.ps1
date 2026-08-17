param(
    [switch]$AuthorizePaidCalls,
    [string]$EnvironmentFile = '.env.demo.local',
    [string]$ArtifactRoot = 'artifacts/benchmarks/model-matrix-live'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$repoRoot = Get-DemoRoot
if (-not $AuthorizePaidCalls) { throw 'Use -AuthorizePaidCalls for the approved 18-call formal matrix.' }
Import-DemoEnvironment (Join-Path $repoRoot $EnvironmentFile)
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$outputRoot = Join-Path $repoRoot $ArtifactRoot
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
$env:PYTHONPATH = "$(Join-Path $repoRoot 'packages\benchmark\src');$(Join-Path $repoRoot 'packages\observability\src')"
$manifests = [Collections.Generic.List[string]]::new()

Push-Location $repoRoot
try {
    foreach ($model in @('kimi-k3','glm-5.2','qwen3.8-max')) {
        foreach ($repeat in 1..3) {
            $safeModel = $model.Replace('.','-')
            $manifest = Join-Path $outputRoot "$safeModel-repeat-$repeat.json"
            & $python -m launchscope_benchmark run-model-api --model $model --repeat-index $repeat `
                --authorize-paid-calls --output $manifest
            if ($LASTEXITCODE -ne 0) {
                throw "Model matrix stopped at $model repeat $repeat with exit code $LASTEXITCODE; no retry or failover was attempted."
            }
            $manifests.Add($manifest)
        }
    }
    & $python -m launchscope_benchmark compare-models @manifests `
        --output (Join-Path $outputRoot 'comparison.json')
    if ($LASTEXITCODE -ne 0) { throw 'Model comparison aggregation failed' }
} finally {
    Pop-Location
}
