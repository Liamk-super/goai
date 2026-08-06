param([string]$EnvironmentFile = '.env.demo.local')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'demo-common.ps1')
$root = Get-DemoRoot
$environmentPath = Join-Path $root $EnvironmentFile
Import-DemoEnvironment $environmentPath
Assert-LocalDemo

$cli = Join-Path $PSScriptRoot 'invoke-agentteams-cli.ps1'
$workers = (& $cli @('get','workers','-o','json') | ConvertFrom-Json).workers
$teams = (& $cli @('get','teams','-o','json') | ConvertFrom-Json).teams
$humans = (& $cli @('get','humans','-o','json') | ConvertFrom-Json).humans
$team = @($teams | Where-Object name -eq 'launchscope-potential-review')
$human = @($humans | Where-Object name -eq 'launchscope-human-coordinator')
if ($team.Count -ne 1 -or $team[0].phase -ne 'Active' -or -not $team[0].teamRoomID -or -not $team[0].leaderDMRoomID) {
    throw 'LaunchScope AgentTeams team is not active or has no Team/Leader Matrix room'
}
if ($human.Count -ne 1 -or $human[0].phase -ne 'Active' -or -not $human[0].initialPassword) {
    throw 'LaunchScope Human is not active or has no initial Matrix credential'
}
if (@($workers | Where-Object phase -ne 'Running').Count -ne 0) {
    throw 'All six LaunchScope Workers must be Running before provisioning the bridge'
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

$agentCodes = @{
    'launchscope-evaluation-supervisor' = 'evaluation-manager'
    'launchscope-product-engineering' = 'product-engineering'
    'launchscope-user-evidence' = 'user-evidence'
    'launchscope-business-investment' = 'business-investment'
    'launchscope-geo-policy-trend' = 'geo-policy-trend'
    'launchscope-evidence-auditor' = 'evidence-auditor'
}
$directory = [ordered]@{}
foreach ($worker in $workers) {
    if (-not $worker.matrixUserID -or -not $agentCodes.ContainsKey($worker.name)) {
        throw "Worker Matrix identity is incomplete: $($worker.name)"
    }
    $directory[$worker.matrixUserID] = $agentCodes[$worker.name]
}
$humanHeaders = @{ Authorization = "Bearer $($login.access_token)" }
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
        try {
            $encodedDirectRoom = [uri]::EscapeDataString($roomID)
            $members = Invoke-RestMethod -Method Get -Uri "$($env:AGENTTEAMS_MATRIX_URL.TrimEnd('/'))/_matrix/client/v3/rooms/$encodedDirectRoom/joined_members" `
                -Headers $humanHeaders -TimeoutSec 20
            $roomReady = @($members.joined.PSObject.Properties.Name) -contains [string]$worker.matrixUserID
        } catch { $roomReady = $false }
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
    }
    $agentRooms[$agentCode] = $roomID
}
$leaderRoomID = [string]$agentRooms['evaluation-manager']

Set-DemoEnvironmentValue $environmentPath 'AGENTTEAMS_TEAM_ROOM_ID' ([string]$team[0].teamRoomID)
Set-DemoEnvironmentValue $environmentPath 'AGENTTEAMS_LEADER_ROOM_ID' ([string]$leaderRoomID)
Set-DemoEnvironmentValue $environmentPath 'AGENTTEAMS_HUMAN_ACCESS_TOKEN' ([string]$login.access_token)
Set-DemoEnvironmentValue $environmentPath 'LAUNCHSCOPE_MATRIX_AGENT_DIRECTORY_JSON' ($directory | ConvertTo-Json -Compress)
Set-DemoEnvironmentValue $environmentPath 'LAUNCHSCOPE_MATRIX_AGENT_ROOMS_JSON' ($agentRooms | ConvertTo-Json -Compress)
Write-Host 'Provisioned AgentTeams bridge: one active Team, six running Workers, one Human token, six direct rooms, and six MXID mappings (credentials redacted).'
