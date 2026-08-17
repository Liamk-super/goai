param(
    [switch]$FullWorkspace,
    [switch]$SkipPromptfoo
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$promptfooPackage = Join-Path $repoRoot "benchmarks\adapters\promptfoo\package.json"
$promptfoo = Join-Path $repoRoot "benchmarks\adapters\promptfoo\node_modules\.bin\promptfoo.CMD"
$promptfooConfig = Join-Path $repoRoot "benchmarks\adapters\promptfoo\promptfooconfig.yaml"
$promptfooMatrixConfig = Join-Path $repoRoot "benchmarks\adapters\promptfoo\model-matrix.yaml"
$artifactRoot = Join-Path $repoRoot "artifacts\benchmarks"

function Invoke-Checked {
    param([scriptblock]$Command, [string]$Label)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Push-Location $repoRoot
try {
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Repository Python is missing: $python"
    }
    $nodeMajor = [int](& node -p "process.versions.node.split('.')[0]")
    if ($LASTEXITCODE -ne 0 -or $nodeMajor -ne 24) {
        throw "Benchmark V1 requires Node 24 LTS; found $(& node --version)"
    }
    New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
    $env:PYTHONPATH = "$(Join-Path $repoRoot 'packages\benchmark\src');$(Join-Path $repoRoot 'packages\observability\src')"

    Invoke-Checked { & $python -m launchscope_benchmark validate } "Benchmark schema/hash validation"
    Invoke-Checked { & $python -m pytest packages\benchmark\tests -q } "Benchmark pytest"
    Invoke-Checked { & $python -m ruff check packages\benchmark } "Benchmark ruff"
    Invoke-Checked { & $python -m mypy packages\benchmark\src } "Benchmark mypy"
    Invoke-Checked {
        & $python -m launchscope_benchmark self-test --suite system-e2e-v1 --output (Join-Path $artifactRoot "system-self-test.json")
    } "Deterministic Benchmark E2E"
    Invoke-Checked {
        & $python -m launchscope_benchmark export-promptfoo --case-set formal-model-selection `
            --output (Join-Path $artifactRoot "model-matrix-promptfoo-cases.json")
    } "Formal model matrix export"

    if ($FullWorkspace) {
        Invoke-Checked { & $python -m pytest -q } "Workspace pytest"
        Invoke-Checked { & $python -m ruff check . } "Workspace ruff"
        Invoke-Checked { & $python -m mypy . } "Workspace mypy"
    }

    if (-not $SkipPromptfoo) {
        $dependency = (Get-Content -LiteralPath $promptfooPackage -Raw | ConvertFrom-Json).devDependencies.promptfoo
        if ($dependency -ne "0.121.19") {
            throw "Promptfoo must be exactly pinned to 0.121.19; found $dependency"
        }
        $env:PROMPTFOO_DISABLE_TELEMETRY = "1"
        $env:PROMPTFOO_DISABLE_UPDATE = "1"
        $env:PROMPTFOO_DISABLE_REMOTE_GENERATION = "true"
        $env:PROMPTFOO_DISABLE_SHARING = "1"
        $env:PROMPTFOO_SELF_HOSTED = "1"
        $env:PROMPTFOO_CONFIG_DIR = Join-Path $artifactRoot "promptfoo-state"
        $env:PROMPTFOO_PYTHON = $python
        $env:FORCE_COLOR = "0"
        if (-not (Test-Path -LiteralPath $promptfoo)) {
            throw "Promptfoo local executable is missing; run pnpm install with Node 24 LTS"
        }
        $version = (& $promptfoo --version | Select-Object -Last 1).Trim()
        if ($LASTEXITCODE -ne 0 -or $version -ne "0.121.19") {
            throw "Promptfoo executable must be 0.121.19; found $version"
        }
        Invoke-Checked { & $promptfoo validate -c $promptfooConfig } "Promptfoo config validation"
        Invoke-Checked { & $promptfoo validate -c $promptfooMatrixConfig } "Promptfoo formal matrix validation"
        Invoke-Checked {
            & $promptfoo eval -c $promptfooConfig --no-share --no-cache --no-progress-bar --no-table -o (Join-Path $artifactRoot "promptfoo-offline-dry-run.json")
        } "Promptfoo offline dry run"
    }

    Write-Output "BENCHMARK_V1_VERIFIED"
}
finally {
    Pop-Location
}
