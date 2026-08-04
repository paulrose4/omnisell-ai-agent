param([switch]$ResetData)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$envFile = Join-Path $projectRoot ".env.local"
if (-not (Test-Path -LiteralPath $envFile)) { $envFile = Join-Path $projectRoot ".env.local.example" }

$arguments = @("compose", "--env-file", $envFile, "-f", "docker-compose.e2e.yml", "-f", "docker-compose.local.yml", "down", "--remove-orphans")
if ($ResetData) { $arguments += "--volumes" }
& docker $arguments
if ($LASTEXITCODE -ne 0) { throw "The OmniSell local stack failed to stop cleanly." }

if ($ResetData) {
    Write-Host "Local containers and PostgreSQL/Redis volumes were removed."
} else {
    Write-Host "Local containers stopped. PostgreSQL and Redis data were preserved."
}
