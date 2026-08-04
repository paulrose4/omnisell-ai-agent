param(
    [string]$VenvPath = ".venv",
    [switch]$Recreate,
    [switch]$SkipAudit,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$targetPath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $VenvPath))
$projectPrefix = $projectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $targetPath.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The virtual environment must stay inside the OmniSell workspace."
}
if ([System.IO.Path]::GetFileName($targetPath) -notmatch '^\.venv(?:[-_][A-Za-z0-9._-]+)?$') {
    throw "The virtual environment directory must be named .venv or .venv-<name>."
}

function Get-Python312 {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>$null
        if ($LASTEXITCODE -eq 0) { return @("py", "-3.12") }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & python -c "import sys; assert sys.version_info[:2] == (3, 12)" 2>$null
        if ($LASTEXITCODE -eq 0) { return @("python") }
    }
    throw "Python 3.12 is required. Install it and ensure py -3.12 or python resolves to it."
}

if (Test-Path -LiteralPath $targetPath) {
    if (-not $Recreate) {
        throw "Virtual environment already exists at $targetPath. Re-run with -Recreate for a verified clean install."
    }
    $resolvedTarget = (Resolve-Path -LiteralPath $targetPath).Path
    if (-not $resolvedTarget.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a virtual environment outside the workspace."
    }
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}

$pythonCommand = Get-Python312
$previousPythonUtf8 = $env:PYTHONUTF8
$previousDatabaseUrl = $env:DATABASE_URL
$previousMigrationDatabaseUrl = $env:MIGRATION_DATABASE_URL
$previousOutputEncoding = $OutputEncoding
$previousConsoleInputEncoding = [Console]::InputEncoding
$previousConsoleOutputEncoding = [Console]::OutputEncoding
Push-Location $projectRoot
try {
    $utf8Encoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = $utf8Encoding
    [Console]::InputEncoding = $utf8Encoding
    [Console]::OutputEncoding = $utf8Encoding
    $env:PYTHONUTF8 = "1"
    if ($pythonCommand.Count -eq 2) {
        & $pythonCommand[0] $pythonCommand[1] -m venv $targetPath
    } else {
        & $pythonCommand[0] -m venv $targetPath
    }
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 failed to create the virtual environment." }

    $venvPython = Join-Path $targetPath "Scripts/python.exe"
    & $venvPython -m pip install --upgrade pip==26.2
    & $venvPython -m pip install --require-hashes --requirement requirements.lock.txt
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "pip check found an inconsistent dependency environment." }

    if (-not $SkipAudit) {
        & $venvPython -m pip_audit -r requirements.runtime.lock.txt
        if ($LASTEXITCODE -ne 0) { throw "pip-audit found a vulnerable runtime dependency." }
        & $venvPython -m pip_audit -r requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw "pip-audit found a vulnerable development dependency." }
    }

    & $venvPython -m compileall api application commerce domain infrastructure model_gateway workflows safety agents rag scripts
    if ($LASTEXITCODE -ne 0) { throw "compileall failed." }

    $tmpPath = Join-Path $projectRoot "tmp"
    New-Item -ItemType Directory -Path $tmpPath -Force | Out-Null
    $bootstrapDatabasePath = Join-Path $tmpPath "bootstrap.sqlite"
    if (Test-Path -LiteralPath $bootstrapDatabasePath) {
        Remove-Item -LiteralPath $bootstrapDatabasePath -Force
    }
    $bootstrapDatabase = $bootstrapDatabasePath.Replace("\", "/")
    $env:DATABASE_URL = "sqlite:///$bootstrapDatabase"
    $env:MIGRATION_DATABASE_URL = $env:DATABASE_URL
    & $venvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic upgrade head failed." }
    & $venvPython -m alembic check
    if ($LASTEXITCODE -ne 0) { throw "alembic check failed." }
    & $venvPython scripts/capture_schema_fingerprint.py --database-url $env:DATABASE_URL --check
    if ($LASTEXITCODE -ne 0) { throw "The committed SQLite schema fingerprint is stale." }
    & $venvPython -m alembic downgrade base
    if ($LASTEXITCODE -ne 0) { throw "alembic downgrade base failed." }
    & $venvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "alembic re-upgrade head failed." }

    if (-not $SkipSmoke) {
        & $venvPython -m pytest -q tests/test_safety.py tests/test_workflow_approval_integrity.py tests/test_agent_runtime.py
        if ($LASTEXITCODE -ne 0) { throw "The focused safety and Agent smoke tests failed." }
    }

    $pythonVersion = (& $venvPython -c "import platform; print(platform.python_version())").Trim()
    $pipVersion = (& $venvPython -c "import importlib.metadata; print(importlib.metadata.version('pip'))").Trim()
    $manifest = [ordered]@{
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        os_platform = [System.Environment]::OSVersion.Platform.ToString()
        os_version = [System.Environment]::OSVersion.VersionString
        powershell_version = $PSVersionTable.PSVersion.ToString()
        python_version = $pythonVersion
        pip_version = $pipVersion
        python_utf8_mode = "enabled"
        workspace_path_character_profile = $(if ($projectRoot -match '[^\x00-\x7F]') { "contains_non_ascii" } else { "ascii_only" })
        requirements_lock_sha256 = (Get-FileHash -Algorithm SHA256 requirements.lock.txt).Hash.ToLowerInvariant()
        runtime_lock_sha256 = (Get-FileHash -Algorithm SHA256 requirements.runtime.lock.txt).Hash.ToLowerInvariant()
        pip_check = "passed"
        dependency_audit = $(if ($SkipAudit) { "skipped" } else { "passed" })
        compileall = "passed"
        alembic_sqlite_smoke = "passed"
        sqlite_schema_fingerprint = "passed_non_authoritative_for_postgresql_rls"
        focused_smoke = $(if ($SkipSmoke) { "skipped" } else { "passed" })
        postgresql_rls = "not_verified_requires_external_non_owner_postgresql"
        docker_compose = "not_verified_by_python_bootstrap"
    }
    $manifest | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $tmpPath "bootstrap-manifest.json")

    Write-Host "OmniSell Python 3.12 environment verified at $targetPath"
} finally {
    if ($null -eq $previousPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue } else { $env:PYTHONUTF8 = $previousPythonUtf8 }
    if ($null -eq $previousDatabaseUrl) { Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue } else { $env:DATABASE_URL = $previousDatabaseUrl }
    if ($null -eq $previousMigrationDatabaseUrl) { Remove-Item Env:MIGRATION_DATABASE_URL -ErrorAction SilentlyContinue } else { $env:MIGRATION_DATABASE_URL = $previousMigrationDatabaseUrl }
    $OutputEncoding = $previousOutputEncoding
    [Console]::InputEncoding = $previousConsoleInputEncoding
    [Console]::OutputEncoding = $previousConsoleOutputEncoding
    Pop-Location
}
