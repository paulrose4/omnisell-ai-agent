from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_pytest_runtime_has_bounded_parallel_gate_plugins() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "pytest-timeout==2.4.0" in requirements
    assert "pytest-xdist==3.8.0" in requirements
    assert "pytest-cov==7.1.0" in requirements
    assert "pyright==1.1.411" in requirements
    assert "ruff==0.16.1" in requirements
    assert "diff-cover==10.4.1" in requirements


def test_pytest_configuration_declares_strict_primary_suites() -> None:
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "--strict-markers" in config
    assert "timeout = 60" in config
    for marker in (
        "unit",
        "contract",
        "sqlite_integration",
        "postgres_integration",
        "e2e",
        "security",
    ):
        assert f"{marker}:" in config


def test_python_quality_configuration_is_risk_scoped() -> None:
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.ruff.lint]" in config
    assert 'select = ["E", "F", "I", "UP", "B", "SIM"]' in config
    assert "[tool.pyright]" in config
    assert '"agents/contracts.py"' in config
    assert '"safety/policy_engine.py"' in config
    assert '"workflows/engine.py"' in config
    assert "[tool.coverage.run]" in config
    assert "branch = true" in config


def test_collection_hook_assigns_primary_suites_and_requires_postgres() -> None:
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert "pytest_collection_modifyitems" in conftest
    assert "PRIMARY_TEST_MARKERS" in conftest
    assert "OMNISELL_REQUIRE_POSTGRES_TESTS" in conftest
    assert "POSTGRES_RLS_URL" in conftest
    assert "pytest.UsageError" in conftest
    assert "pytest.mark.timeout(180)" in conftest


def test_ci_partitions_backend_and_publishes_test_evidence() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    backend = jobs["backend-tests"]
    suites = {entry["suite"]: entry for entry in backend["strategy"]["matrix"]["include"]}
    assert set(suites) == {
        "unit-contract",
        "sqlite-integration-a",
        "sqlite-integration-b",
        "postgres-integration",
        "e2e",
        "security",
    }
    assert suites["unit-contract"]["workers"] != ""
    assert suites["postgres-integration"]["require_postgres"] == "true"
    assert backend["timeout-minutes"] <= 15

    coverage = jobs["backend-coverage"]
    assert coverage["timeout-minutes"] <= 25
    coverage_steps = "\n".join(str(step) for step in coverage["steps"])
    assert "--cov-branch" in coverage_steps
    assert "test-results/coverage.json" in coverage_steps
    assert "test-results/coverage.xml" in coverage_steps
    assert "test-results/coverage-junit.xml" in coverage_steps
    assert "scripts/check_critical_coverage.py" in coverage_steps
    assert "coverage-evidence" in coverage_steps

    rendered = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--junitxml" in rendered
    assert "--durations=50" in rendered
    assert "actions/upload-artifact@v4" in rendered
    assert "if: always()" in rendered
    assert "npm audit --omit=dev --audit-level=high" in rendered
    assert "python -m ruff check ." in rendered
    assert "python -m ruff format --check ." in rendered
    assert "python -m pyright" in rendered
    assert "python scripts/measure_agent_contract_schemas.py --check" in rendered
    assert "python scripts/run_agent_quality_eval.py --check" in rendered


def test_frontend_dependencies_and_unused_image_optimizer_are_hardened() -> None:
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    config = (ROOT / "frontend" / "next.config.mjs").read_text(encoding="utf-8")
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    assert package["dependencies"]["next"] == "15.5.21"
    assert package["overrides"]["sharp"] == "0.35.3"
    assert "unoptimized: true" in config
    assert "path /_next/image" in caddy
    assert "respond @next_image 404" in caddy


def test_runtime_images_pin_current_bases_and_remove_unused_vulnerable_tooling() -> None:
    api_dockerfile = (ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    frontend_dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "python:3.12.13-slim-trixie@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
        in api_dockerfile
    )
    assert "--allow-remove-essential perl-base" in api_dockerfile
    assert (
        "node:22.23.1-alpine3.23@sha256:8516dce0483394d5708d4b2ee6cacb79fb1d617ea4e2787c2120bcca92ce372e"
        in frontend_dockerfile
    )
    assert "/usr/local/lib/node_modules/npm" in frontend_dockerfile
    assert "/usr/local/lib/node_modules/corepack" in frontend_dockerfile


def test_advertising_diagnostics_ui_has_bounded_pagination() -> None:
    workbench = (ROOT / "frontend" / "components" / "operations" / "operational-intelligence-workbench.tsx").read_text(
        encoding="utf-8"
    )

    assert "diagnosticPage" in workbench
    assert "diagnosticPageSize = 50" in workbench
    assert "上一页广告诊断" in workbench
    assert "下一页广告诊断" in workbench


def test_high_fidelity_uat_uses_postgres_api_seeding_and_read_only_guards() -> None:
    launcher = (ROOT / "scripts" / "start_high_fidelity_uat.ps1").read_text(encoding="utf-8")

    assert "scripts/start_local.ps1" in launcher
    assert "/api/mock-scenarios/high-fidelity/seed" in launcher
    assert "/api/mock-scenarios/high-fidelity/tasks/" in launcher
    assert "postgresql" in launcher.lower()
    assert "verified_amazon_data" in launcher
    assert "amazon_write_performed" in launcher
    assert 'action -eq "operations.refresh"' in launcher
    assert "sqlite" not in launcher.lower()
    assert "initialize_database" not in launcher


def test_production_read_only_amazon_boundary_remains_enforced() -> None:
    config = (ROOT / "api" / "config.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.e2e.yml").read_text(encoding="utf-8")

    assert 'AMAZON_WRITES_ENABLED: "false"' in compose
    assert 'AMAZON_PILOT_READ_ONLY: "true"' in compose
    assert "Current production release requires Amazon read-only pilot mode with writes disabled" in config
