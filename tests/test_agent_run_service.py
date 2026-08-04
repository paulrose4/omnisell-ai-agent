from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agents.registry import AdvisorRegistry
from application.agent_services import AgentRunService


class Store:
    def __init__(self) -> None:
        self.row = SimpleNamespace(
            id="failed-run",
            tenant_id="tenant-a",
            actor_id="operator-1",
            workflow_type="customer_case_analysis",
            correlation_id="corr-old",
            status="failed",
            prompt_version="customer-case-v1",
            agent_type="customer_case_advisor",
            schema_version="1.0",
            model=None,
            token_usage=0,
            estimated_cost=0,
            model_usage={},
            evidence_manifest={},
            analysis_result={},
            error_category="ModelUnavailable",
            retry_count=0,
            idempotency_key="idem-old",
            state={
                "request": {"case_id": "case-1", "locale": "en-US"},
                "dispatch": {"task_id": "task-1", "status": "failed"},
            },
            created_at=datetime(2026, 7, 16, tzinfo=UTC),
            updated_at=datetime(2026, 7, 16, tzinfo=UTC),
        )

    def get_run(self, *, tenant_id: str, run_id: str):
        return self.row if tenant_id == "tenant-a" and run_id == "failed-run" else None

    def list_runs(self, *, tenant_id: str, status: str | None = None, limit: int = 50):
        return [self.row] if tenant_id == "tenant-a" else []

    def increment_retry(self, *, tenant_id: str, run_id: str) -> int:
        self.row.retry_count += 1
        return self.row.retry_count


class Workflow:
    async def run_advisory(self, **kwargs):
        self.kwargs = kwargs
        return {"run_id": "retry-run", "status": "succeeded", "analysis": {}}


async def fallback(**kwargs):
    raise AssertionError("advisor retry must not use action workflow fallback")


def test_failed_advisor_run_resumes_as_new_retry_run() -> None:
    store = Store()
    workflow = Workflow()
    advisor = object()
    collector = object()
    service = AgentRunService(
        store=store,
        workflow=workflow,
        registry=AdvisorRegistry({"customer_case_analysis": advisor}),
        evidence_collectors={"customer_case_analysis": collector},
        action_resume=fallback,
    )

    result = asyncio.run(
        service.resume(tenant_id="tenant-a", run_id="failed-run", actor_id="operator-2", correlation_id="corr-new")
    )

    assert result["retried_from_run_id"] == "failed-run"
    assert result["run_id"] == "retry-run"
    assert workflow.kwargs["request"]["idempotency_key"] == "retry:failed-run:1"
    assert workflow.kwargs["request"]["case_id"] == "case-1"
    assert workflow.kwargs["advisor"] is advisor
    assert workflow.kwargs["evidence_collector"] is collector


def test_agent_run_service_hides_cross_tenant_run() -> None:
    service = AgentRunService(
        store=Store(),
        workflow=Workflow(),
        registry=AdvisorRegistry(),
        evidence_collectors={},
        action_resume=fallback,
    )
    with pytest.raises(KeyError):
        asyncio.run(service.resume(tenant_id="tenant-b", run_id="failed-run", actor_id="x", correlation_id="x"))


def test_agent_run_service_exposes_dispatch_task_linkage() -> None:
    service = AgentRunService(
        store=Store(),
        workflow=Workflow(),
        registry=AdvisorRegistry(),
        evidence_collectors={},
        action_resume=fallback,
    )

    run = service.get_run(tenant_id="tenant-a", run_id="failed-run")

    assert run is not None
    assert run["dispatch_task_id"] == "task-1"
    assert run["dispatch_task_status"] == "failed"
    assert run["workflow_class"] == "bounded_advisory"
    assert run["generation_method"] == "model"
    assert run["workflow_name"] == "客户案例 Advisor"
    assert run["amazon_write_capability"] is False


def test_agent_run_service_adapts_safe_v1_analysis_and_quarantines_unsafe_payload() -> None:
    store = Store()
    store.row.workflow_type = "operations_brief_analysis"
    store.row.agent_type = "operations_brief_analyst"
    store.row.analysis_result = {
        "schema_version": "1.0",
        "agent_type": "operations_brief_analyst",
        "prompt_version": "operations-brief-v6",
        "tenant_id": "tenant-a",
        "workflow_run_id": "failed-run",
        "generated_at": datetime(2026, 7, 16, tzinfo=UTC).isoformat(),
        "executive_summary": "Review the evidence.",
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
    service = AgentRunService(
        store=store,
        workflow=Workflow(),
        registry=AdvisorRegistry(),
        evidence_collectors={},
        action_resume=fallback,
    )

    adapted = service.get_run(tenant_id="tenant-a", run_id="failed-run")

    assert adapted is not None
    assert adapted["analysis_result_status"] == "adapted"
    assert adapted["analysis_result_source_schema_version"] == "1.0"
    assert adapted["analysis_result"]["schema_version"] == "2.0"

    store.row.analysis_result["proposed_actions"] = [
        {
            "action_type": "create_internal_task_draft",
            "resource_type": "operational_issue",
            "resource_id": "issue-1",
            "arguments": {"target_quantity": 600},
            "reason": "Unsafe legacy target.",
            "evidence_ids": ["issue-1"],
            "expected_outcome": "Unsafe.",
            "risk_level": "low",
            "requires_approval": False,
            "automation_eligible": False,
            "prohibited": False,
            "prohibited_reason": None,
        }
    ]

    quarantined = service.get_run(tenant_id="tenant-a", run_id="failed-run")

    assert quarantined is not None
    assert quarantined["analysis_result"] == {}
    assert quarantined["analysis_result_status"] == "legacy_incompatible"
