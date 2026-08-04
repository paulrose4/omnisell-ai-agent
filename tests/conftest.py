from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


PRIMARY_TEST_MARKERS = (
    "unit",
    "contract",
    "sqlite_integration",
    "postgres_integration",
    "e2e",
    "security",
)

POSTGRES_TEST_MODULES = {"test_postgres_rls.py"}
E2E_TEST_MODULES = {
    "test_e2e.py",
    "test_golden_mock_tenant.py",
    "test_local_e2e_mock.py",
    "test_local_oidc_amazon_e2e.py",
    "test_mock_customer_service_drill.py",
    "test_mock_operations_drill.py",
}
SECURITY_TEST_MODULES = {
    "test_audit_redaction.py",
    "test_domain_guardrail.py",
    "test_operational_issue_approval.py",
    "test_production_foundation.py",
    "test_production_quality_gates.py",
    "test_refund_safety.py",
    "test_safety.py",
    "test_tool_permission.py",
    "test_workflow_approval_integrity.py",
}
CONTRACT_TEST_MODULES = {
    "test_agent_contracts.py",
    "test_agent_evals.py",
    "test_external_validation_tools.py",
    "test_high_fidelity_mock_store.py",
    "test_legacy_agent_runtime_boundaries.py",
    "test_model_gateway.py",
    "test_production_acceptance_replay.py",
}
SQLITE_INTEGRATION_TEST_MODULES = {
    "test_agent_run_api.py",
    "test_agent_run_idempotency.py",
    "test_agent_run_service.py",
    "test_async_ops.py",
    "test_customer_case_advisor.py",
    "test_daily_operations_brief.py",
    "test_inventory_health.py",
    "test_listing_optimization_advisor.py",
    "test_listing_quality_experiments.py",
    "test_migrations.py",
    "test_mock_oidc_personas.py",
    "test_mock_scenario_api.py",
    "test_operational_issues.py",
    "test_operational_work_items.py",
    "test_operations_agent_workflow.py",
    "test_operations_brief_advisor.py",
    "test_operations_learning_loop.py",
    "test_profitability.py",
    "test_reconciliation_investigator.py",
}
SQLITE_SHARD_A_TEST_MODULES = {
    "test_agent_run_api.py",
    "test_agent_run_service.py",
    "test_daily_operations_brief.py",
    "test_listing_quality_experiments.py",
    "test_migrations.py",
    "test_mock_scenario_api.py",
}


def pytest_configure() -> None:
    require_postgres = os.getenv("OMNISELL_REQUIRE_POSTGRES_TESTS", "").lower() in {"1", "true", "yes"}
    if require_postgres and not os.getenv("POSTGRES_RLS_URL"):
        raise pytest.UsageError(
            "POSTGRES_RLS_URL is required when OMNISELL_REQUIRE_POSTGRES_TESTS=true; "
            "PostgreSQL/RLS tests must not be silently skipped in a production gate."
        )


def _primary_marker_for(module_name: str) -> str:
    if module_name in POSTGRES_TEST_MODULES:
        return "postgres_integration"
    if module_name in E2E_TEST_MODULES:
        return "e2e"
    if module_name in SECURITY_TEST_MODULES:
        return "security"
    if module_name in CONTRACT_TEST_MODULES:
        return "contract"
    if module_name in SQLITE_INTEGRATION_TEST_MODULES:
        return "sqlite_integration"
    return "unit"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        module_name = Path(str(item.fspath)).name
        existing = [name for name in PRIMARY_TEST_MARKERS if name in item.keywords]
        if len(existing) > 1:
            raise pytest.UsageError(f"{item.nodeid} has multiple primary test markers: {existing}")
        marker_name = existing[0] if existing else _primary_marker_for(module_name)
        if not existing:
            item.add_marker(getattr(pytest.mark, marker_name))
        if marker_name == "sqlite_integration":
            shard_marker = "ci_shard_a" if module_name in SQLITE_SHARD_A_TEST_MODULES else "ci_shard_b"
            item.add_marker(getattr(pytest.mark, shard_marker))
        if marker_name == "e2e":
            item.add_marker(pytest.mark.timeout(180))
