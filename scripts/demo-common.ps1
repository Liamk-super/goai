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

function Test-TcpPort([string]$HostName, [int]$Port, [int]$TimeoutMs = 800) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.ConnectAsync($HostName, $Port)
        return $pending.Wait($TimeoutMs) -and $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}

function Start-DemoProcess(
    [string]$Name, [string]$FilePath, [string[]]$ArgumentList,
    [string]$WorkingDirectory, [string]$StateDirectory, [string]$LogDirectory
) {
    $stdout = Join-Path $LogDirectory "$Name.stdout.log"
    $stderr = Join-Path $LogDirectory "$Name.stderr.log"
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    $record = [ordered]@{
        name = $Name; pid = $process.Id; started_at = $process.StartTime.ToUniversalTime().ToString('o')
        executable = $process.Path; requested_executable = (Resolve-Path -LiteralPath $FilePath).Path
        marker = 'launchscope-local-demo'
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StateDirectory "$Name.pid.json") -Encoding utf8
}

function Assert-LocalDemo([switch]$RequireForce, [switch]$Force) {
    if ($env:LAUNCHSCOPE_ENV -ne 'local-demo') { throw 'Refusing operation: LAUNCHSCOPE_ENV must equal local-demo' }
    if ($RequireForce -and -not $Force) { throw 'Refusing destructive reset without explicit -Force' }
}
