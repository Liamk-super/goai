param([string]$EnvironmentFile = '.env.demo.local')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
$environmentPath = Join-Path $root $EnvironmentFile
Import-DemoEnvironment $environmentPath
Assert-LocalDemo

$cli = Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1'
$workerResources = Get-AgentTeamsWorkerResourceMap
$expectedWorkers = $workerResources.Count
function Invoke-AgentTeamsJson([string[]]$CommandArguments) {
    $lastFailure = $null
    $rawPath = Join-Path ([IO.Path]::GetTempPath()) "launchscope-agt-$([guid]::NewGuid().ToString('n')).json"
    try {
        for ($attempt = 1; $attempt -le 5; $attempt += 1) {
            & $cli @CommandArguments > $rawPath
            $exitCode = $LASTEXITCODE
            $raw = Get-Content -LiteralPath $rawPath -Raw
            if ($exitCode -eq 0) {
                try { return $raw | ConvertFrom-Json }
                catch { $lastFailure = $_.Exception }
            } else {
                $lastFailure = "AgentTeams CLI exited with code $exitCode"
            }
            if ($attempt -lt 5) { Start-Sleep -Seconds 2 }
        }
    } finally {
        Remove-Item -LiteralPath $rawPath -Force -ErrorAction SilentlyContinue
    }
    throw "AgentTeams CLI did not return valid JSON after 5 attempts: $lastFailure"
}
$allWorkers = (Invoke-AgentTeamsJson @('get','workers','-o','json')).workers
$workers = @($allWorkers | Where-Object { $_.name -in @($workerResources.Values) })
$teams = (Invoke-AgentTeamsJson @('get','teams','-o','json')).teams
$humans = (Invoke-AgentTeamsJson @('get','humans','-o','json')).humans
$teamName = Get-AgentTeamsTeamName
$humanName = Get-AgentTeamsHumanName
$team = @($teams | Where-Object name -eq $teamName)
$human = @($humans | Where-Object name -eq $humanName)
if ($team.Count -ne 1 -or $team[0].phase -ne 'Active' -or -not $team[0].teamRoomID -or -not $team[0].leaderDMRoomID) {
    throw 'LaunchScope AgentTeams team is not active or has no Team/Leader Matrix room'
}
if ($human.Count -ne 1 -or $human[0].phase -ne 'Active' -or -not $human[0].initialPassword) {
    throw 'LaunchScope Human is not active or has no initial Matrix credential'
}
if ($workers.Count -ne $expectedWorkers -or @($workers | Where-Object phase -ne 'Running').Count -ne 0) {
    throw "All $expectedWorkers selected $(Get-AgentTeamsGeneration) Workers must be Running before provisioning the bridge"
}

$loginBody = @{
    type = 'm.login.password'
    identifier = @{ type = 'm.id.user'; user = $human[0].matrixUserID }
    password = $human[0].initialPassword
} | ConvertTo-Json -Depth 4 -Compress
$login = Invoke-RestMethod -Method Post -Uri "$($env:AGENTTEAMS_MATRIX_URL.TrimEnd('/'))/_matrix/client/v3/login" `
    -ContentType 'application/json' -Body $loginBody -TimeoutSec 20
if (-not $login.access_token) { throw 'Matrix login returned no access token' }
$encodedRoom = [uri]::EscapeDataString([string]$team[0].teamRoomID)
Invoke-RestMethod -Method Post -Uri "$($env:AGENTTEAMS_MATRIX_URL.TrimEnd('/'))/_matrix/client/v3/rooms/$encodedRoom/join" `
    -Headers @{ Authorization = "Bearer $($login.access_token)" } -ContentType 'application/json' -Body '{}' -TimeoutSec 20 | Out-Null

$agentCodes = @{}
foreach ($entry in $workerResources.GetEnumerator()) { $agentCodes[$entry.Value] = $entry.Key }
$directory = [ordered]@{}
foreach ($worker in $workers) {
    if (-not $worker.matrixUserID -or -not $agentCodes.ContainsKey($worker.name)) {
        throw "Worker Matrix identity is incomplete: $($worker.name)"
    }
    $directory[$worker.matrixUserID] = $agentCodes[$worker.name]
}
$humanHeaders = @{ Authorization = "Bearer $($login.access_token)" }
function Test-AgentRoomMembership([string]$RoomID, [string]$WorkerMXID) {
    try {
        $encodedDirectRoom = [uri]::EscapeDataString($RoomID)
        $members = Invoke-RestMethod -Method Get -Uri "$($env:AGENTTEAMS_MATRIX_URL.TrimEnd('/'))/_matrix/client/v3/rooms/$encodedDirectRoom/joined_members" `
            -Headers $humanHeaders -TimeoutSec 20
        $roomMembers = @($members.joined.PSObject.Properties.Name)
        $expectedMembers = @([string]$human[0].matrixUserID, [string]$WorkerMXID)
        return $roomMembers.Count -eq 2 -and @($expectedMembers | Where-Object { $_ -notin $roomMembers }).Count -eq 0
    } catch { return $false }
}
$existingRooms = @{}
$roomsJson = [Environment]::GetEnvironmentVariable('LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON')
if (-not [string]::IsNullOrWhiteSpace($roomsJson)) {
    try {
        $parsedRooms = $roomsJson | ConvertFrom-Json
        foreach ($property in $parsedRooms.PSObject.Properties) { $existingRooms[$property.Name] = [string]$property.Value }
    } catch { $existingRooms = @{} }
}
$agentRooms = [ordered]@{}
foreach ($worker in $workers) {
    $agentCode = $agentCodes[$worker.name]
    $roomID = if ($existingRooms.ContainsKey($agentCode)) { [string]$existingRooms[$agentCode] } else { '' }
    $roomReady = $false
    if (-not [string]::IsNullOrWhiteSpace($roomID)) {
        $roomReady = Test-AgentRoomMembership $roomID ([string]$worker.matrixUserID)
    }
    if (-not $roomReady) {
        $directBody = @{
            invite = @([string]$worker.matrixUserID)
            is_direct = $true
            preset = 'trusted_private_chat'
            name = "LaunchScope human to $agentCode"
        } | ConvertTo-Json -Depth 4 -Compress
        $direct = Invoke-RestMethod -Method Post -Uri "$($env:AGENTTEAMS_MATRIX_URL.TrimEnd('/'))/_matrix/client/v3/createRoom" `
            -Headers $humanHeaders -ContentType 'application/json' -Body $directBody -TimeoutSec 20
        if (-not $direct.room_id) { throw "Matrix direct-room creation returned no room_id for $agentCode" }
        $roomID = [string]$direct.room_id
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            if (Test-AgentRoomMembership $roomID ([string]$worker.matrixUserID)) {
                $roomReady = $true
                break
            }
            Start-Sleep -Seconds 2
        }
        if (-not $roomReady) {
            throw "Matrix direct room for $agentCode did not reach exact Human and Worker membership"
        }
    }
    $agentRooms[$agentCode] = $roomID
}
$leaderRoomID = [string]$agentRooms['evaluation-manager']

Set-DemoEnvironmentValue $environmentPath 'AGENTTEAMS_TEAM_ROOM_ID' ([string]$team[0].teamRoomID)
Set-DemoEnvironmentValue $environmentPath 'AGENTTEAMS_LEADER_ROOM_ID' ([string]$leaderRoomID)
Set-DemoEnvironmentValue $environmentPath 'AGENTTEAMS_HUMAN_ACCESS_TOKEN' ([string]$login.access_token)
Set-DemoEnvironmentValue $environmentPath 'LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON' ($directory | ConvertTo-Json -Compress)
Set-DemoEnvironmentValue $environmentPath 'LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON' ($agentRooms | ConvertTo-Json -Compress)
Write-Host "Provisioned AgentTeams $(Get-AgentTeamsGeneration) bridge: one active Team, $expectedWorkers running Workers, one Human token, $expectedWorkers direct rooms, and $expectedWorkers MXID mappings (credentials redacted)."
