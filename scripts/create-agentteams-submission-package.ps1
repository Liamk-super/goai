[CmdletBinding()]
param(
    [string]$OutputDirectory = 'deliverables/launchscope-agentteams-v6-code-package-20260816',
    [string]$ArchivePath = 'deliverables/launchscope-agentteams-v6-code-package-20260816.zip',
    [switch]$SkipPackageBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$destination = [IO.Path]::GetFullPath((Join-Path $root $OutputDirectory))
$archive = [IO.Path]::GetFullPath((Join-Path $root $ArchivePath))
$deliverablesRoot = [IO.Path]::GetFullPath((Join-Path $root 'deliverables'))
$templateRoot = Join-Path $root 'submission-templates/agentteams-v6'
$python = Join-Path $root '.venv/Scripts/python.exe'

if (-not $destination.StartsWith($deliverablesRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must be a child of deliverables.'
}
if (-not $archive.StartsWith($deliverablesRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'ArchivePath must be a child of deliverables.'
}
if ((Test-Path -LiteralPath $destination) -or (Test-Path -LiteralPath $archive)) {
    throw 'Refusing to overwrite an existing submission package. Choose a new output name.'
}
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Repository .venv is required to build the deterministic AgentTeams packages.'
}

function Copy-SourceTree([string]$RelativeSource) {
    $source = Join-Path $root $RelativeSource
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Required source root is missing: $RelativeSource"
    }
    $excludedSegment = '^(node_modules|\.next.*|__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|coverage|output|logs|\.demo|\.venv)$'
    foreach ($file in Get-ChildItem -LiteralPath $source -Recurse -File -Force) {
        $relative = $file.FullName.Substring($source.Length).TrimStart('\', '/')
        $segments = $relative -split '[\\/]'
        if ($segments | Where-Object { $_ -match $excludedSegment }) {
            continue
        }
        if ($RelativeSource -eq 'infra' -and $relative -match '^agentteams[\\/]generated([\\/]|$)') {
            continue
        }
        if ($RelativeSource -eq 'scripts' -and $relative -eq 'create-agentteams-submission-package.ps1') {
            continue
        }
        $target = Join-Path (Join-Path $destination $RelativeSource) $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target
    }
}

if (-not $SkipPackageBuild) {
    $validationOutput = & $python (Join-Path $root 'scripts/build-agentteams-packages.py') --check --generation v6 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw 'AgentTeams v6 resource and contract validation failed.'
    }
    $buildOutput = & $python (Join-Path $root 'scripts/build-agentteams-packages.py') --generation v6 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw 'AgentTeams v6 package build failed.'
    }
    $testOutput = & $python -m pytest -q `
        tests/test_agentteams_package_build.py `
        apps/orchestrator/tests/test_supervisor_1p4_resources.py `
        apps/orchestrator/tests/test_evidence_calibration_agentteams_package.py `
        packages/skills/tests/test_report_v22_skill_registry.py 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw 'Focused AgentTeams v6 package tests failed.'
    }
}

$expectedPackages = @(
    'evaluation-manager.zip',
    'user-evidence.zip',
    'product-engineering.zip',
    'business-investment.zip',
    'evidence-auditor.zip'
)
$generatedPackageRoot = Join-Path $root 'infra/agentteams/generated/packages-v6'
foreach ($package in $expectedPackages) {
    if (-not (Test-Path -LiteralPath (Join-Path $generatedPackageRoot $package))) {
        throw "Missing generated package: $package"
    }
}

New-Item -ItemType Directory -Path $destination | Out-Null
Get-ChildItem -LiteralPath $templateRoot -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $destination $_.Name) -Recurse
}
if (-not $SkipPackageBuild) {
    $evidencePath = Join-Path $destination 'execution-evidence/build-and-focused-test.txt'
    New-Item -ItemType Directory -Path (Split-Path -Parent $evidencePath) -Force | Out-Null
    @(
        '# Execution evidence',
        '',
        'Command: .venv/Scripts/python.exe scripts/build-agentteams-packages.py --check --generation v6',
        $validationOutput.TrimEnd(),
        '',
        'Command: .venv/Scripts/python.exe scripts/build-agentteams-packages.py --generation v6',
        $buildOutput.TrimEnd(),
        '',
        'Command: .venv/Scripts/python.exe -m pytest -q tests/test_agentteams_package_build.py apps/orchestrator/tests/test_supervisor_1p4_resources.py apps/orchestrator/tests/test_evidence_calibration_agentteams_package.py packages/skills/tests/test_report_v22_skill_registry.py',
        $testOutput.TrimEnd(),
        '',
        'Boundary: These local package checks do not prove a Live AgentTeams, Matrix, RocketMQ, model, search, billing, or prediction-accuracy result.'
    ) | Set-Content -LiteralPath $evidencePath -Encoding utf8
}

@('apps', 'agent', 'docs', 'infra', 'packages', 'scripts', 'tests') | ForEach-Object { Copy-SourceTree $_ }
@('.env.demo.example', '.gitignore', 'README.md', 'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'pyproject.toml', 'start.cmd', 'start.ps1', 'tsconfig.base.json') |
    ForEach-Object { Copy-Item -LiteralPath (Join-Path $root $_) -Destination (Join-Path $destination $_) }

$packageDestination = Join-Path $destination 'infra/agentteams/generated/packages-v6'
New-Item -ItemType Directory -Path $packageDestination -Force | Out-Null
foreach ($package in $expectedPackages) {
    Copy-Item -LiteralPath (Join-Path $generatedPackageRoot $package) -Destination (Join-Path $packageDestination $package)
}

$contentManifest = [ordered]@{
    schema_version = 'launchscope.agentteams.submission-package.v1'
    package_generation = 'v6'
    topology = 'supervisor-1+4'
    worker_packages = $expectedPackages
    run_entry = 'RUN_AGENTTEAMS_PACKAGE.ps1'
    config_template = '.env.demo.example'
    sample_input = 'sample-input/recorded-agentteams-package-case.json'
    sample_output_note = 'sample-output/README.md'
    illustrated_guide = '图文使用指南.md'
    execution_evidence = 'execution-evidence/build-and-focused-test.txt'
    source_roots = @('apps', 'agent', 'docs', 'infra', 'packages', 'scripts', 'tests')
    excluded = @('.env.demo.local', '.git', '.venv', 'node_modules', '.next*', '.demo', 'reference', 'deliverables', 'tmp', 'logs')
}
$contentManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $destination 'PACKAGE_CONTENTS.json') -Encoding utf8

$hashPaths = @(
    (Join-Path $destination 'RUN_AGENTTEAMS_PACKAGE.ps1'),
    (Join-Path $destination '.env.demo.example'),
    (Join-Path $destination 'infra/agentteams/resources/launchscope-team-v6.yaml')
) + ($expectedPackages | ForEach-Object { Join-Path $packageDestination $_ })
$hashLines = foreach ($path in $hashPaths) {
    $relative = $path.Substring($destination.Length).TrimStart('\', '/') -replace '\\', '/'
    $digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    "$digest  $relative"
}
$hashLines | Set-Content -LiteralPath (Join-Path $destination 'SHA256SUMS.txt') -Encoding ascii

Compress-Archive -LiteralPath $destination -DestinationPath $archive -CompressionLevel Optimal
Write-Host "Submission directory: $destination"
Write-Host "Submission archive:   $archive"
