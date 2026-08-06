from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_ROOT = REPOSITORY_ROOT / "alembic" / "versions"
SQLITE_FINGERPRINT_PATH = REPOSITORY_ROOT / "docs" / "evidence" / "migrations" / "0023_sqlite_schema_fingerprint.json"
POSTGRES_FINGERPRINT_PATH = (
    REPOSITORY_ROOT / "docs" / "evidence" / "migrations" / "0023_postgresql16_schema_rls_fingerprint.json"
)
FROZEN_RLS_TABLES_PATTERN = re.compile(
    r"FROZEN_RLS_TABLES\s*=\s*\((?P<body>.*?)\)\s*\n",
    re.DOTALL,
)


def test_active_migration_chain_is_frozen_and_self_contained() -> None:
    migration_files = sorted(VERSIONS_ROOT.glob("*.py"))

    assert [path.name for path in migration_files] == ["0023_frozen_baseline.py"]
    source = migration_files[0].read_text(encoding="utf-8")
    forbidden_patterns = {
        "application database imports": r"(?:from|import)\s+infrastructure\.database",
        "runtime ORM metadata": r"Base\.metadata|models\.Base",
        "metadata create/drop": r"\.create_all\(|\.drop_all\(",
        "conditional schema creation": r"checkfirst\s*=\s*True",
    }
    for label, pattern in forbidden_patterns.items():
        assert re.search(pattern, source) is None, f"Frozen migration contains {label}."

    assert 'revision = "0023"' in source
    assert "down_revision = None" in source
    assert "op.create_table(" in source
    assert "op.drop_table(" in source


def test_frozen_baseline_declares_literal_rls_registry_for_all_tenant_tables() -> None:
    migration_source = (VERSIONS_ROOT / "0023_frozen_baseline.py").read_text(encoding="utf-8")
    match = FROZEN_RLS_TABLES_PATTERN.search(migration_source)

    assert match is not None, "Frozen migration must declare FROZEN_RLS_TABLES as a literal tuple."
    declared_tables = set(re.findall(r'["\']([^"\']+)["\']', match.group("body")))

    from commerce.adapters.amazon.secrets import SecretRow  # noqa: F401
    from infrastructure.database import models  # noqa: F401
    from infrastructure.database.session import Base

    expected_tables = {table.name for table in Base.metadata.sorted_tables if "tenant_id" in table.columns}
    assert declared_tables == expected_tables


def test_committed_sqlite_schema_fingerprint_matches_frozen_baseline(tmp_path: Path) -> None:
    from scripts.capture_schema_fingerprint import build_schema_evidence

    database_path = tmp_path / "fingerprint.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {**os.environ, "DATABASE_URL": database_url, "MIGRATION_DATABASE_URL": database_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    committed = json.loads(SQLITE_FINGERPRINT_PATH.read_text(encoding="utf-8"))
    assert committed == build_schema_evidence(database_url, repository_root=REPOSITORY_ROOT)


def test_committed_postgresql_fingerprint_is_authoritative_sanitized_rls_evidence() -> None:
    evidence = json.loads(POSTGRES_FINGERPRINT_PATH.read_text(encoding="utf-8"))

    assert evidence["evidence_class"] == "postgresql_schema_and_rls"
    assert evidence["authoritative_for_postgresql_rls"] is True
    assert evidence["schema"]["dialect"] == "postgresql"
    assert evidence["schema"]["revision"] == "0023"
    assert "database_url" not in evidence

    tenant_tables = {
        table["name"]
        for table in evidence["schema"]["tables"]
        if any(column["name"] == "tenant_id" for column in table["columns"])
    }
    rls_tables = {table["table_name"] for table in evidence["schema"]["rls"]["tables"] if table["rls_enabled"]}
    policy_tables = {policy["table_name"] for policy in evidence["schema"]["rls"]["policies"]}

    assert tenant_tables
    assert rls_tables == tenant_tables
    assert policy_tables == tenant_tables


def test_empty_database_upgrades_to_alembic_head(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-check.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {**os.environ, "DATABASE_URL": database_url, "MIGRATION_DATABASE_URL": database_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0023"
        columns = {item["name"] for item in inspect(engine).get_columns("tenants")}
        assert "amazon_writes_enabled" in columns
        agent_run_columns = {item["name"] for item in inspect(engine).get_columns("agent_runs")}
        listing_experiment_columns = {
            item["name"] for item in inspect(engine).get_columns("listing_optimization_experiments")
        }
        approval_columns = {item["name"] for item in inspect(engine).get_columns("approval_requests")}
        work_item_columns = {item["name"] for item in inspect(engine).get_columns("operational_work_items")}
        outbox_columns = {item["name"] for item in inspect(engine).get_columns("outbox_events")}
        assert {"execution_status", "executed_by", "decision", "decision_reason"} <= listing_experiment_columns
        assert {
            "agent_type",
            "schema_version",
            "model_usage",
            "evidence_manifest",
            "analysis_result",
            "error_category",
            "retry_count",
            "idempotency_key",
        } <= agent_run_columns
        assert {
            "policy_decision",
            "policy_version",
            "policy_reason",
            "required_approval_count",
            "first_approver_id",
            "first_approved_at",
            "first_approval_reason",
            "decided_at",
            "decision_reason",
        } <= approval_columns
        assert "approval_decisions" in inspect(engine).get_table_names()
        assert {
            "source_type",
            "source_id",
            "domain",
            "team",
            "assignee_id",
            "due_at",
            "sla_policy_version",
            "escalation_level",
            "result_metrics",
            "verification_status",
            "evidence_manifest",
            "action_hash",
            "handoff_note",
            "blocked_reason",
        } <= work_item_columns
        assert {"dedupe_key", "status", "attempts", "lease_expires_at", "next_retry_at", "error_code"} <= outbox_columns
        assert {
            "amazon_operational_signals",
            "advertising_daily_snapshots",
            "search_term_performance_snapshots",
            "advertising_budget_diagnostics",
            "return_events",
            "review_observations",
            "buyer_message_tags",
            "sku_voc_summaries",
            "issue_feedback",
            "missed_issue_reports",
            "operator_activity_events",
            "business_context_events",
            "action_execution_attempts",
            "action_outcomes",
            "intelligence_rule_versions",
            "intelligence_calibration_proposals",
            "action_execution_authorizations",
            "action_attribution_plans",
            "customer_case_clusters",
            "customer_case_cluster_members",
        } <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_frozen_baseline_downgrades_to_empty_database_on_sqlite(tmp_path: Path) -> None:
    database_path = tmp_path / "migration-downgrade-check.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = {**os.environ, "DATABASE_URL": database_url, "MIGRATION_DATABASE_URL": database_url}
    repository_root = Path(__file__).resolve().parents[1]

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr

    engine = create_engine(database_url)
    try:
        assert inspect(engine).get_table_names() == ["alembic_version"]
    finally:
        engine.dispose()
