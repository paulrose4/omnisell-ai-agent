$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required. Install it, start Docker, and run this script again."
}

$envFile = Join-Path $projectRoot ".env.e2e"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $projectRoot ".env.e2e.example") $envFile
    Write-Host "Created .env.e2e from the local-only example."
}

docker compose --env-file $envFile -f docker-compose.e2e.yml up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "The mock pilot stack failed to start."
}

$apiReady = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8080/api/admin/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $apiReady = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $apiReady) {
    throw "The mock pilot API did not become ready within 120 seconds."
}

python scripts/run_local_e2e.py
if ($LASTEXITCODE -ne 0) {
    throw "The mock pilot smoke test failed."
}

Write-Host ""
Write-Host "OmniSell mock pilot is ready:"
Write-Host "  API:        http://localhost:8080"
Write-Host "  Mock OIDC:  http://localhost:9000"
Write-Host "  PostgreSQL: localhost:55432 (local development only)"
Write-Host ""
Write-Host "Amazon writes remain blocked by AMAZON_WRITES_ENABLED=false and AMAZON_PILOT_READ_ONLY=true."
