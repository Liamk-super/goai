Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-DemoRoot { return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path }

function Import-DemoEnvironment([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Demo environment file not found: $Path" }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $parts = $trimmed.Split('=', 2)
        if ($parts.Count -ne 2 -or -not $parts[0].Trim()) { throw "Malformed environment entry in $Path" }
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1], 'Process')
    }
}

function Set-DemoEnvironmentValue([string]$Path, [string]$Name, [string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Name) -or $Value.Contains("`r") -or $Value.Contains("`n")) {
        throw 'Demo environment values must be single-line named entries'
    }
    $lines = [Collections.Generic.List[string]]::new()
    $updated = $false
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            if ($line -match "^$([regex]::Escape($Name))=") {
                if (-not $updated) { $lines.Add("$Name=$Value"); $updated = $true }
            } else { $lines.Add($line) }
        }
    }
    if (-not $updated) { $lines.Add("$Name=$Value") }
    [IO.File]::WriteAllLines($Path, $lines, [Text.UTF8Encoding]::new($false))
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

function Test-TcpPort([string]$HostName, [int]$Port, [int]$TimeoutMs = 800) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.ConnectAsync($HostName, $Port)
        return $pending.Wait($TimeoutMs) -and $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Test-AgentTeamsCliAvailable {
    if (Get-Command agt -ErrorAction SilentlyContinue) { return $true }
    $controller = docker ps --filter 'name=^/agentteams-controller$' --format '{{.Names}}' 2>$null
    return $LASTEXITCODE -eq 0 -and $controller -eq 'agentteams-controller'
}

function Start-DemoProcess(
    [string]$Name, [string]$FilePath, [string[]]$ArgumentList,
    [string]$WorkingDirectory, [string]$StateDirectory, [string]$LogDirectory
) {
    $stdout = Join-Path $LogDirectory "$Name.stdout.log"
    $stderr = Join-Path $LogDirectory "$Name.stderr.log"
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    $actualProcess = Get-Process -Id $process.Id -ErrorAction Stop
    $record = [ordered]@{
        name = $Name; pid = $process.Id; started_at = $process.StartTime.ToUniversalTime().ToString('o')
        executable = $actualProcess.Path; requested_executable = (Resolve-Path -LiteralPath $FilePath).Path
        marker = 'launchscope-local-demo'
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StateDirectory "$Name.pid.json") -Encoding utf8
}

function Assert-LocalDemo([switch]$RequireForce, [switch]$Force) {
    if ($env:LAUNCHSCOPE_ENV -ne 'local-demo') { throw 'Refusing operation: LAUNCHSCOPE_ENV must equal local-demo' }
    if ($RequireForce -and -not $Force) { throw 'Refusing destructive reset without explicit -Force' }
}
