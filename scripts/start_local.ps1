param(
    [switch]$SkipSmoke,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required. Install it, start Docker, and run this script again."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is installed but the Docker engine is not running."
}

$envFile = Join-Path $projectRoot ".env.local"
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
$postgresPassword = Get-LocalEnvValue "E2E_POSTGRES_PASSWORD" "local-postgres-password"
$notificationToken = Get-LocalEnvValue "E2E_NOTIFICATION_TOKEN" "local-notification-token-change-for-shared-machines"

$composeArgs = @("compose", "--env-file", $envFile, "-f", "docker-compose.e2e.yml", "-f", "docker-compose.local.yml", "up", "-d")
if (-not $NoBuild) { $composeArgs += "--build" }
& docker $composeArgs
if ($LASTEXITCODE -ne 0) { throw "The OmniSell local stack failed to start." }

function Wait-ForHttp([string]$Name, [string]$Url, [int]$Attempts = 90) {
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(3)
    try {
        for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
            try {
                $response = $client.GetAsync($Url).GetAwaiter().GetResult()
                if ([int]$response.StatusCode -eq 200) { return }
            } catch {
                Start-Sleep -Seconds 2
            }
        }
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
    throw "$Name did not become ready: $Url"
}

Wait-ForHttp "API" "http://localhost:$apiPort/api/admin/health"
Wait-ForHttp "Frontend" "http://localhost:$frontendPort/signin"

if (-not $SkipSmoke) {
    $smokeArgs = @("scripts/run_local_e2e.py", "--api", "http://localhost:$apiPort", "--mock-cloud", "http://localhost:$mockCloudPort", "--database", "postgresql://omnisell:$postgresPassword@localhost:$postgresPort/omnisell", "--notification-token", $notificationToken)
    & python $smokeArgs
    if ($LASTEXITCODE -ne 0) { throw "The local integration smoke test failed." }
    $frontendSmokeArgs = @("scripts/run_local_frontend_auth_smoke.py", "--frontend", "http://localhost:$frontendPort")
    & python $frontendSmokeArgs
    if ($LASTEXITCODE -ne 0) { throw "The local frontend OIDC smoke test failed." }
}

Write-Host ""
Write-Host "OmniSell local environment is ready:"
Write-Host "  Web UI:     http://localhost:$frontendPort"
Write-Host "  API:        http://localhost:$apiPort"
Write-Host "  Mock OIDC:  http://localhost:$mockCloudPort"
Write-Host "  PostgreSQL: localhost:$postgresPort"
Write-Host ""
Write-Host "Use one of the four local personas on the sign-in page."
Write-Host "Amazon writes remain blocked by the read-only pilot gate."
Write-Host "Stop with: ./scripts/stop_local.ps1"
