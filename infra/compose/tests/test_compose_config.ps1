[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$composeDirectory = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$repositoryRoot = (Resolve-Path (Join-Path $composeDirectory "..\.." )).Path
$baseCompose = Join-Path $composeDirectory "docker-compose.yml"
$testCompose = Join-Path $composeDirectory "docker-compose.test.yml"
$envExample = Join-Path $composeDirectory ".env.example"

function Get-ComposeModel([string] $composeFile) {
    $output = & docker compose -f $composeFile config --format json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose config failed for $composeFile`n$($output -join [Environment]::NewLine)"
    }

    $json = $output -join [Environment]::NewLine
    if ($json -match '(?<!\$)\$\{') {
        throw "Unresolved Compose interpolation remains in $composeFile"
    }
    return $json | ConvertFrom-Json
}

function Assert-ComposeModel($model, [string] $label) {
    $expectedServices = @(
        "postgres",
        "minio",
        "rocketmq-namesrv",
        "rocketmq-broker",
        "nacos",
        "higress",
        "otel-collector"
    )
    $serviceProperties = @($model.services.PSObject.Properties)
    $actualServices = @($serviceProperties | ForEach-Object { $_.Name })

    foreach ($service in $expectedServices) {
        if ($actualServices -notcontains $service) {
            throw "$label is missing required service '$service'"
        }
    }

    $images = @($serviceProperties | ForEach-Object { [string]$_.Value.image })
    foreach ($image in $images) {
        if ($image -notmatch '@sha256:[0-9a-f]{64}$') {
            throw "$label has an unpinned image: $image"
        }
        if ($image -match '(?i)(^|:)latest(@|$)') {
            throw "$label uses a latest image tag: $image"
        }
    }

    $publishedPorts = @{}
    foreach ($serviceProperty in $serviceProperties) {
        foreach ($port in @($serviceProperty.Value.ports)) {
            if ($null -eq $port.published) {
                continue
            }
            $published = [string]$port.published
            if ($publishedPorts.ContainsKey($published)) {
                throw "$label publishes host port $published for both '$($publishedPorts[$published])' and '$($serviceProperty.Name)'"
            }
            $publishedPorts[$published] = $serviceProperty.Name
        }
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker is required to validate Compose configuration"
}

Assert-ComposeModel (Get-ComposeModel $baseCompose) "base Compose"
Assert-ComposeModel (Get-ComposeModel $testCompose) "test Compose"

$secretKeys = "PASSWORD|TOKEN|SECRET|OAUTH|ACCESS_KEY|PRIVATE_KEY"
foreach ($line in Get-Content -LiteralPath $envExample) {
    if ($line -match "^\s*(?<key>[A-Z0-9_]*(?:$secretKeys)[A-Z0-9_]*)\s*=\s*(?<value>.*)$") {
        $value = $Matches.value.Trim()
        if ($value -and $value -notmatch '^\$\{[A-Z0-9_]+\}$') {
            throw ".env.example contains a non-empty secret-like value for $($Matches.key)"
        }
    }
}

Write-Output "Compose configuration checks passed for base and test files."
Write-Output "Repository root: $repositoryRoot"
