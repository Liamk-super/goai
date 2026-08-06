param(
    [ValidateSet("Local", "Test")]
    [string]$Environment = "Local",
    [ValidateRange(0, 10000)]
    [int]$BudgetLimit = 0,
    [string]$ArtifactRoot = "artifacts/acceptance"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) { throw "Create the repository .venv before verification." }
if ($Environment -eq "Test" -and -not $env:LAUNCHSCOPE_TEST_DATABASE_URL) {
    throw "Test verification requires LAUNCHSCOPE_TEST_DATABASE_URL from a secure environment."
}

$runId = "local-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$artifact = Join-Path $repo (Join-Path $ArtifactRoot $runId)
New-Item -ItemType Directory -Force -Path $artifact | Out-Null

$commands = @(
    @("contracts-domain", @("-m", "pytest", "packages/domain/tests", "packages/contracts/tests", "-q")),
    @("t11", @("-m", "pytest", "apps/api/tests/integration/test_budget_reservation.py", "tests/security/test_observability_redaction.py", "apps/api/tests/integration/test_retention_delete.py", "-q")),
    @("local-vertical-slice", @("-m", "pytest", "tests/e2e/test_vertical_slice.py", "tests/e2e/test_v1_v2_regression.py", "-q")),
    @("readonly-tools", @("-m", "pytest", "tests/integration/test_real_readonly_tools.py", "-q")),
    @("security", @("-m", "pytest", "tests/security/test_full_security_gate.py", "-q"))
)

$summary = @()
foreach ($entry in $commands) {
    $name = $entry[0]
    $arguments = $entry[1]
    $log = Join-Path $artifact "$name.txt"
    & $python @arguments 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) { throw "$name failed; see $log" }
    $summary += [ordered]@{ gate = $name; status = "PASS"; log = "$name.txt" }
}

$external = if ($env:LAUNCHSCOPE_REAL_READONLY_URL) { "REQUESTED_SEPARATELY" } else { "BLOCKED_NO_AUTHORIZED_CASE" }
$manifest = [ordered]@{
    environment = $Environment
    budget_limit = $BudgetLimit
    external_readonly_e2e = $external
    paid_provider_calls = "NOT_RUN"
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    gates = $summary
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $artifact "verification-summary.json")
Get-ChildItem -File $artifact | Get-FileHash -Algorithm SHA256 |
    ForEach-Object { "$($_.Hash.ToLower())  $($_.Path.Substring($artifact.Length + 1))" } |
    Set-Content -Encoding utf8 (Join-Path $artifact "hashes.txt")

Write-Output "Verification artifacts: $artifact"
