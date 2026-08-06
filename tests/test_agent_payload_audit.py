from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.contract_adapters import adapt_persisted_analysis
from application.agent_payload_audit import (
    AgentPayloadRecord,
    render_agent_payload_scan_report,
    scan_agent_payload_database,
    scan_agent_payload_records,
)
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import AgentRunRow, TenantRow
from infrastructure.database.session import create_session_factory


def _operations_v1(*, run_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "agent_type": "operations_brief_analyst",
        "prompt_version": "operations-brief-v6",
        "tenant_id": "tenant-a",
        "workflow_run_id": run_id,
        "generated_at": datetime(2026, 8, 5, tzinfo=UTC).isoformat(),
        "executive_summary": "Review deterministic evidence.",
        "evidence": ["issue-1"],
        "hypotheses": [],
        "missing_data": [],
        "proposed_actions": [],
        "prohibited_actions": [],
        "confidence": "0.50",
        "degraded_reason": None,
        "warnings": [],
        "grouped_issue_ids": [],
        "daily_checklist": [],
        "internal_task_drafts": [],
        "model_usage": None,
        "evidence_preparation": None,
    }


def test_payload_scan_classifies_versions_rejections_and_empty_records_without_leaking_payload() -> None:
    generated_at = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    valid_v1 = _operations_v1(run_id="run-v1")
    valid_v2 = adapt_persisted_analysis(workflow_type="operations_brief_analysis", payload=valid_v1)
    invalid_version = {**valid_v1, "schema_version": "3.0", "workflow_run_id": "run-bad-version"}
    records = [
        AgentPayloadRecord("run-v1", "tenant-a", "operations_brief_analysis", "1.0", valid_v1),
        AgentPayloadRecord("run-v2", "tenant-a", "operations_brief_analysis", "2.0", valid_v2),
        AgentPayloadRecord("run-bad-version", "tenant-a", "operations_brief_analysis", "3.0", invalid_version),
        AgentPayloadRecord("run-unsupported", "tenant-a", "unknown_workflow", "1.0", valid_v1),
        AgentPayloadRecord("run-empty", "tenant-a", "operations_brief_analysis", "2.0", {}),
    ]

    report = scan_agent_payload_records(records, generated_at=generated_at)

    assert report["scan_mode"] == "read_only"
    assert report["evidence_class"] == "deterministic_fixture"
    assert report["generated_at"] == "2026-08-05T09:00:00+00:00"
    assert report["totals"] == {
        "records_scanned": 5,
        "payload_records": 4,
        "compatible_records": 2,
        "adapted_v1_records": 1,
        "validated_v2_records": 1,
        "empty_payload_records": 1,
        "rejected_records": 2,
    }
    assert report["rejections_by_reason"] == [
        {"reason_code": "unsupported_schema_version", "count": 1},
        {"reason_code": "unsupported_workflow", "count": 1},
    ]
    assert [item["record_id"] for item in report["manual_review_records"]] == [
        "run-bad-version",
        "run-unsupported",
    ]
    serialized = json.dumps(report, ensure_ascii=False)
    assert "Review deterministic evidence" not in serialized
    assert "issue-1" not in serialized

    evidence_path = Path("docs/evidence/agent-contracts/payload-compatibility-scan-fixture-v1.json")
    assert evidence_path.read_text(encoding="utf-8") == render_agent_payload_scan_report(report)


def test_database_payload_scan_is_explicitly_tenant_scoped_and_does_not_rewrite_rows(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'payload-audit.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    factory = create_session_factory(database_url)
    tenant_a_payload = _operations_v1(run_id="run-a")
    tenant_b_payload = {**tenant_a_payload, "workflow_run_id": "run-b", "tenant_id": "tenant-b"}
    with factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="Tenant B", slug="tenant-b"))
        session.add_all(
            [
                AgentRunRow(
                    id="run-a",
                    tenant_id="tenant-a",
                    actor_id="operator-a",
                    workflow_type="operations_brief_analysis",
                    correlation_id="corr-a",
                    status="succeeded",
                    prompt_version="operations-brief-v6",
                    schema_version="1.0",
                    analysis_result=tenant_a_payload,
                ),
                AgentRunRow(
                    id="run-b",
                    tenant_id="tenant-b",
                    actor_id="operator-b",
                    workflow_type="operations_brief_analysis",
                    correlation_id="corr-b",
                    status="succeeded",
                    prompt_version="operations-brief-v6",
                    schema_version="1.0",
                    analysis_result=tenant_b_payload,
                ),
            ]
        )

    report = scan_agent_payload_database(
        factory,
        tenant_ids=["tenant-a"],
        generated_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
    )

    assert report["tenant_scope"]["tenant_count"] == 1
    assert report["evidence_class"] == "persisted_database"
    assert report["totals"]["records_scanned"] == 1
    assert report["totals"]["adapted_v1_records"] == 1
    with factory() as session:
        row_a = session.get(AgentRunRow, "run-a")
        row_b = session.get(AgentRunRow, "run-b")
        assert row_a is not None and row_a.analysis_result == tenant_a_payload
        assert row_b is not None and row_b.analysis_result == tenant_b_payload


def test_payload_scan_report_rendering_is_stable_and_newline_terminated() -> None:
    report = scan_agent_payload_records([], generated_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    rendered = render_agent_payload_scan_report(report)
    assert rendered.endswith("\n")
    assert rendered == render_agent_payload_scan_report(report)
    assert '"records_scanned": 0' in rendered


def test_payload_scan_uses_stable_reason_codes_and_requires_explicit_tenants(tmp_path) -> None:
    valid = _operations_v1(run_id="run-base")
    reports = scan_agent_payload_records(
        [
            AgentPayloadRecord(
                "run-non-json",
                "tenant-a",
                "operations_brief_analysis",
                "1.0",
                {**valid, "confidence": float("nan")},
            ),
            AgentPayloadRecord(
                "run-resource-limit",
                "tenant-a",
                "operations_brief_analysis",
                "1.0",
                {**valid, "executive_summary": "x" * 20001},
            ),
            AgentPayloadRecord(
                "run-invalid",
                "tenant-a",
                "operations_brief_analysis",
                "1.0",
                {**valid, "confidence": "not-a-decimal"},
            ),
            AgentPayloadRecord(
                "run-not-object",
                "tenant-a",
                "operations_brief_analysis",
                "1.0",
                ["invalid"],
            ),
        ],
        generated_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
    )
    assert reports["rejections_by_reason"] == [
        {"reason_code": "invalid_payload", "count": 2},
        {"reason_code": "non_json_value", "count": 1},
        {"reason_code": "resource_budget_exceeded", "count": 1},
    ]

    database_url = f"sqlite:///{tmp_path / 'empty-tenant-scope.sqlite'}"
    initialize_database(database_url)
    with pytest.raises(ValueError, match="explicit tenant_id"):
        scan_agent_payload_database(create_session_factory(database_url), tenant_ids=[])


def test_payload_scan_report_never_includes_dynamic_validation_details() -> None:
    secret_field_name = "api_key-do-not-publish"
    invalid = {**_operations_v1(run_id="run-secret-field"), secret_field_name: "ignored"}

    report = scan_agent_payload_records(
        [AgentPayloadRecord("run-secret-field", "tenant-a", "operations_brief_analysis", "1.0", invalid)],
        generated_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
    )

    serialized = json.dumps(report, ensure_ascii=False)
    assert secret_field_name not in serialized
    assert set(report["manual_review_records"][0]) == {
        "record_id",
        "tenant_ref",
        "workflow_type",
        "source_schema_version",
        "reason_code",
    }
