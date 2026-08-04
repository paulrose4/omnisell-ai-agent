param(
    [switch]$ResetDatabase,
    [switch]$NoBuild,
    [int]$Seed = 20260722,
    [ValidateRange(1, 90)][int]$Days = 30,
    [ValidateRange(10, 500)][int]$SkuCount = 120,
    [ValidateRange(100, 100000)][int]$OrderCount = 12000,
    [ValidateRange(1, 500)][int]$CampaignCount = 60,
    [ValidateRange(10, 50000)][int]$SearchTermCount = 3500,
    [ValidateRange(1, 10000)][int]$ReturnCount = 700,
    [ValidateRange(1, 10000)][int]$MessageCount = 1000,
    [ValidateRange(1, 10000)][int]$ReviewCount = 400,
    [ValidateRange(1, 5000)][int]$CustomerCaseCount = 350,
    [ValidateRange(1, 5000)][int]$BusinessContextCount = 75,
    [ValidateRange(5, 120)][int]$TimeoutMinutes = 45
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot ".env.local"
$localLauncher = Join-Path $projectRoot "scripts/start_local.ps1"
$composeFiles = @("docker-compose.e2e.yml", "docker-compose.local.yml")

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required for PostgreSQL high-fidelity UAT."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is installed but the Docker engine is not running."
}

if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.local.example") -Destination $envFile
    Write-Host "Created .env.local from the local-only example."
}

function Get-LocalEnvValue([string]$Name, [string]$DefaultValue) {
    $match = Get-Content -LiteralPath $envFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if (-not $match) { return $DefaultValue }
    return ($match -split "=", 2)[1].Trim()
}

$frontendPort = Get-LocalEnvValue "LOCAL_FRONTEND_PORT" "3000"
$apiPort = Get-LocalEnvValue "E2E_API_PORT" "8080"
$mockCloudPort = Get-LocalEnvValue "E2E_MOCK_CLOUD_PORT" "9000"
$postgresPort = Get-LocalEnvValue "E2E_POSTGRES_PORT" "55432"

Push-Location $projectRoot
try {
    if ($ResetDatabase) {
        $downArgs = @("compose", "--env-file", $envFile)
        foreach ($composeFile in $composeFiles) { $downArgs += @("-f", $composeFile) }
        $downArgs += @("down", "--volumes", "--remove-orphans")
        & docker $downArgs
        if ($LASTEXITCODE -ne 0) { throw "Failed to reset the isolated local UAT stack." }
    }

    & $localLauncher -SkipSmoke -NoBuild:$NoBuild
    if ($LASTEXITCODE -ne 0) { throw "scripts/start_local.ps1 failed." }

    $composeArgs = @("compose", "--env-file", $envFile)
    foreach ($composeFile in $composeFiles) { $composeArgs += @("-f", $composeFile) }
    $composeArgs += @("exec", "-T", "postgres", "pg_isready", "-U", "omnisell", "-d", "omnisell")
    & docker $composeArgs
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL did not pass its readiness check." }

    $token = Invoke-RestMethod -Method Post -Uri "http://localhost:$mockCloudPort/oidc/token" -NoProxy -Body @{
        grant_type = "client_credentials"
        tenant_id = "local-demo"
        subject = "local-owner"
        role = "owner"
        scope = "read write admin"
    }
    $headers = @{ Authorization = "Bearer $($token.access_token)" }

    $connections = Invoke-RestMethod -Method Get -Uri "http://localhost:$apiPort/api/channels/amazon/connections" -NoProxy -Headers $headers
    if ([int]$connections.total -eq 0) {
        $authorizationBody = @{
            selling_partner_id = "A1LOCALMOCKSELLER"
            marketplace_ids = @("ATVPDKIKX0DER")
        } | ConvertTo-Json -Compress
        $authorization = Invoke-RestMethod -Method Post -Uri "http://localhost:$apiPort/api/channels/amazon/authorize" -NoProxy -Headers $headers -ContentType "application/json" -Body $authorizationBody
        $callback = Invoke-RestMethod -Method Get -Uri $authorization.authorization_url -NoProxy
        if ($callback.status -ne "active") { throw "Mock Amazon authorization did not become active." }
        $connectionId = $callback.connection_id
    } else {
        $connectionId = $connections.items[0].id
    }

    $requestBody = @{
        connection_id = $connectionId
        seed = $Seed
        days = $Days
        sku_count = $SkuCount
        order_count = $OrderCount
        campaign_count = $CampaignCount
        search_term_count = $SearchTermCount
        return_count = $ReturnCount
        message_count = $MessageCount
        review_count = $ReviewCount
        customer_case_count = $CustomerCaseCount
        business_context_count = $BusinessContextCount
        reference_time = [DateTime]::UtcNow.Date.ToString("o")
    } | ConvertTo-Json -Compress

    $submitted = Invoke-RestMethod -Method Post -Uri "http://localhost:$apiPort/api/mock-scenarios/high-fidelity/seed" -NoProxy -Headers $headers -ContentType "application/json" -Body $requestBody
    if ($submitted.verified_amazon_data -ne $false -or $submitted.amazon_write_performed -ne $false) {
        throw "The high-fidelity seed response violated the mock/read-only data contract."
    }

    $taskUrl = "http://localhost:$apiPort/api/mock-scenarios/high-fidelity/tasks/$($submitted.task_id)"
    $deadline = [DateTime]::UtcNow.AddMinutes($TimeoutMinutes)
    do {
        Start-Sleep -Seconds 2
        $status = Invoke-RestMethod -Method Get -Uri $taskUrl -NoProxy -Headers $headers
        Write-Progress -Activity "High-fidelity Amazon UAT seed" -Status $status.status
        if ($status.status -in @("succeeded", "failed", "dead_letter", "cancelled")) { break }
    } while ([DateTime]::UtcNow -lt $deadline)
    Write-Progress -Activity "High-fidelity Amazon UAT seed" -Completed

    if ($status.status -ne "succeeded") {
        $detail = if ($status.error_message) { $status.error_message } else { "Timed out or ended in $($status.status)" }
        throw "High-fidelity seed failed: $detail"
    }
    if ($status.result.verified_amazon_data -ne $false -or $status.result.amazon_write_performed -ne $false) {
        throw "The completed UAT task violated the mock/read-only data contract."
    }
    $tasks = Invoke-RestMethod -Method Get -Uri "http://localhost:$apiPort/api/tasks?limit=500" -NoProxy -Headers $headers
    $invalidRefreshes = @($tasks.items | Where-Object {
        $_.action -eq "operations.refresh" -and $_.result.verified_amazon_data -eq $true
    })
    if ($invalidRefreshes.Count -gt 0) {
        throw "Mock UAT produced an operations.refresh task that claimed verified Amazon data."
    }

    Write-Host ""
    Write-Host "High-fidelity PostgreSQL UAT is ready."
    Write-Host "  Web UI:      http://localhost:$frontendPort"
    Write-Host "  API:         http://localhost:$apiPort"
    Write-Host "  Mock Cloud:  http://localhost:$mockCloudPort"
    Write-Host "  PostgreSQL:  localhost:$postgresPort (Alembic-managed)"
    Write-Host "  Connection:  $connectionId"
    Write-Host "  Fingerprint: $($submitted.dataset_fingerprint)"
    Write-Host "  Data source: mock / verified_amazon_data=false"
    Write-Host "  Amazon write: amazon_write_performed=false"
} finally {
    Pop-Location
}
