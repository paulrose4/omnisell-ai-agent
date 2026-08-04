from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


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
        listing_experiment_columns = {item["name"] for item in inspect(engine).get_columns("listing_optimization_experiments")}
        approval_columns = {item["name"] for item in inspect(engine).get_columns("approval_requests")}
        work_item_columns = {item["name"] for item in inspect(engine).get_columns("operational_work_items")}
        outbox_columns = {item["name"] for item in inspect(engine).get_columns("outbox_events")}
        assert {"execution_status", "executed_by", "decision", "decision_reason"} <= listing_experiment_columns
        assert {
            "agent_type", "schema_version", "model_usage", "evidence_manifest",
            "analysis_result", "error_category", "retry_count", "idempotency_key",
        } <= agent_run_columns
        assert {"first_approver_id", "first_approved_at", "first_approval_reason", "decided_at", "decision_reason"} <= approval_columns
        assert "approval_decisions" in inspect(engine).get_table_names()
        assert {
            "source_type", "source_id", "domain", "team", "assignee_id", "due_at",
            "sla_policy_version", "escalation_level", "result_metrics", "verification_status",
            "evidence_manifest", "action_hash", "handoff_note", "blocked_reason",
        } <= work_item_columns
        assert {"dedupe_key", "status", "attempts", "lease_expires_at", "next_retry_at", "error_code"} <= outbox_columns
        assert {
            "amazon_operational_signals", "advertising_daily_snapshots",
            "search_term_performance_snapshots", "advertising_budget_diagnostics",
            "return_events", "review_observations", "buyer_message_tags", "sku_voc_summaries",
            "issue_feedback", "missed_issue_reports", "operator_activity_events",
            "business_context_events", "action_execution_attempts", "action_outcomes",
            "intelligence_rule_versions", "intelligence_calibration_proposals",
            "action_execution_authorizations", "action_attribution_plans",
            "customer_case_clusters", "customer_case_cluster_members",
        } <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()



def test_agent_idempotency_migration_downgrades_on_sqlite(tmp_path: Path) -> None:
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
        [sys.executable, "-m", "alembic", "downgrade", "0015"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr

    engine = create_engine(database_url)
    try:
        columns = {item["name"] for item in inspect(engine).get_columns("agent_runs")}
        assert "idempotency_key" not in columns
    finally:
        engine.dispose()
