param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArguments
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$hostCli = Get-Command agt -ErrorAction SilentlyContinue
if ($hostCli) {
    & $hostCli.Source @CliArguments
    exit $LASTEXITCODE
}

$controller = docker ps --filter 'name=^/agentteams-controller$' --format '{{.Names}}' 2>$null
if ($LASTEXITCODE -ne 0 -or $controller -ne 'agentteams-controller') {
    throw 'AgentTeams CLI unavailable: install the host agt CLI or start agentteams-controller'
}

$containerArguments = [Collections.Generic.List[string]]::new()
$temporaryRoot = $null
for ($index = 0; $index -lt $CliArguments.Count; $index++) {
    $argument = $CliArguments[$index]
    $containerArguments.Add($argument)
    if ($argument -in @('-f', '--filename', '--zip') -and $index + 1 -lt $CliArguments.Count) {
        $isResourceFile = $argument -in @('-f', '--filename')
        $index++
        $source = (Resolve-Path -LiteralPath $CliArguments[$index]).Path
        if (-not $temporaryRoot) {
            $temporaryRoot = "/tmp/launchscope-agt-$([guid]::NewGuid().ToString('n'))"
            & docker exec agentteams-controller mkdir -p -- $temporaryRoot
            if ($LASTEXITCODE -ne 0) { throw 'Could not create a temporary AgentTeams CLI workspace' }
        }
        $target = "$temporaryRoot/$([IO.Path]::GetFileName($source))"
        & docker cp $source "agentteams-controller:$target" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not copy AgentTeams resource into controller: $source" }
        $containerArguments.Add($target)
        if ($isResourceFile) {
            $resource = Get-Content -LiteralPath $source -Raw
            foreach ($match in [regex]::Matches($resource, 'file://\./([^\s"''}]+)')) {
                $relative = $match.Groups[1].Value
                $localReference = $null
                foreach ($base in @((Get-Location).Path, (Split-Path -Parent $source), (Split-Path -Parent (Split-Path -Parent $source)))) {
                    $candidate = Join-Path $base ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
                    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $localReference = (Resolve-Path -LiteralPath $candidate).Path; break }
                }
                if (-not $localReference) { throw "Referenced AgentTeams package not found: $relative" }
                $containerReference = "$temporaryRoot/$($relative.Replace('\','/'))"
                $containerParent = $containerReference.Substring(0, $containerReference.LastIndexOf('/'))
                & docker exec agentteams-controller mkdir -p -- $containerParent
                & docker cp $localReference "agentteams-controller:$containerReference" | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Could not copy AgentTeams package into controller: $relative" }
                # Resource reconciliation is asynchronous. Keep a controller-local
                # copy at AgentTeams' documented file-package fallback after this
                # CLI process removes its short-lived import workspace.
                $packageTarget = "/$($relative.Replace('\','/'))"
                $packageParent = $packageTarget.Substring(0, $packageTarget.LastIndexOf('/'))
                & docker exec agentteams-controller mkdir -p -- $packageParent
                & docker cp $localReference "agentteams-controller:$packageTarget" | Out-Null
                if ($LASTEXITCODE -ne 0) { throw "Could not stage AgentTeams package for reconciliation: $relative" }
            }
        }
    }
}

try {
    if ($temporaryRoot) { & docker exec --workdir $temporaryRoot agentteams-controller agt @containerArguments }
    else { & docker exec agentteams-controller agt @containerArguments }
    exit $LASTEXITCODE
} finally {
    if ($temporaryRoot) { & docker exec agentteams-controller rm -rf -- $temporaryRoot 2>$null | Out-Null }
}
