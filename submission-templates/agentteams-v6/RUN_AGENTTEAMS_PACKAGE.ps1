[CmdletBinding()]
param(
    [ValidateSet('Validate', 'Recorded', 'Live')]
    [string]$Mode = 'Validate',
    [string]$PythonPath = '',
    [switch]$Bootstrap,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = $PSScriptRoot
$python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    Join-Path $root '.venv/Scripts/python.exe'
} else {
    [IO.Path]::GetFullPath($PythonPath)
}
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python virtual environment is required. Install the dependencies listed in SUBMISSION_README.md first.'
}

& $python (Join-Path $root 'scripts/build-agentteams-packages.py') --check --generation v6
if ($LASTEXITCODE -ne 0) {
    throw 'AgentTeams v6 resource and contract validation failed.'
}
& $python (Join-Path $root 'scripts/build-agentteams-packages.py') --generation v6
if ($LASTEXITCODE -ne 0) {
    throw 'AgentTeams v6 Worker package build failed.'
}

if ($Mode -eq 'Validate') {
    Write-Host 'AgentTeams v6 package validation and deterministic build succeeded.'
    exit 0
}

$startArgs = @('-Mode', $Mode)
if ($Bootstrap) {
    $startArgs += '-Bootstrap'
}
if ($NoBrowser) {
    $startArgs += '-NoBrowser'
}
& (Join-Path $root 'start.ps1') @startArgs
exit $LASTEXITCODE
