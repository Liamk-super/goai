param(
    [ValidateSet('Recorded', 'RealModel')]
    [string]$Mode = 'Recorded',
    [decimal]$BudgetLimitUsd = 1.00,
    [int]$MaxOutputTokens = 16000,
    [int]$TimeoutSeconds = 180,
    [string]$OutputDirectory = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Repository Python is missing: $python" }

if ($Mode -eq 'RealModel') {
    Import-DemoEnvironment (Join-Path $root '.env.demo.local')
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    $OutputDirectory = Join-Path $root ".demo\user-agent-tests\$stamp-$($Mode.ToLowerInvariant())"
} elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $root $OutputDirectory
}

$arguments = @(
    (Join-Path $root 'scripts\test-user-agent.py'),
    '--mode', $Mode,
    '--output-dir', $OutputDirectory,
    '--budget-limit-usd', $BudgetLimitUsd.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--max-output-tokens', $MaxOutputTokens,
    '--timeout-seconds', $TimeoutSeconds
)
if ($Mode -eq 'RealModel') { $arguments += '--authorize-real-model' }

& $python @arguments
exit $LASTEXITCODE
