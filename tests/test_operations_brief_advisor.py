from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from agents.prompts.operations_brief_v7 import OPERATIONS_BRIEF_PROMPT_VERSION, OPERATIONS_BRIEF_SYSTEM_PROMPT
from agents.registry import AdvisorRegistry
from application.operations_brief_agent import OperationsBriefAgentService, OperationsBriefEvidenceCollector

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


class BriefRepository:
    def get_latest(self, *, tenant_id: str):
        assert tenant_id == "tenant-a"
        return SimpleNamespace(
            id="brief-1",
            tenant_id=tenant_id,
            brief_date=date(2026, 7, 16),
            timezone="UTC",
            generated_at=NOW,
            issue_counts={"high": 1},
            domain_counts={"inventory": 1},
            total_open_issues=1,
            data_quality_issue_count=1,
            impact_by_currency={"USD": {"profit": "25.00"}},
            unallocated_impact_issue_count=0,
            priority_issue_ids=("issue-1",),
            priority_action_ids=("action-1",),
            sku_health=(
                SimpleNamespace(
                    tenant_id=tenant_id,
                    seller_sku="GOLDEN-SKU-1",
                    marketplace_id="ATVPDKIKX0DER",
                    health_status="at_risk",
                    highest_severity="high",
                    open_issue_count=1,
                    issue_types=("inventory_data_quality",),
                    recommended_action_ids=("action-1",),
                    impact_by_currency={"USD": {"profit": "25.00"}},
                    has_data_quality_risk=True,
                    last_detected_at=NOW,
                ),
            ),
            source_manifest={"mode": "mock", "inventory_snapshot_ids": ["inventory-1"]},
        )


class IssueRepository:
    def list_issues(self, *, tenant_id: str):
        return [
            SimpleNamespace(
                id="issue-1",
                tenant_id=tenant_id,
                seller_sku="GOLDEN-SKU-1",
                marketplace_id="ATVPDKIKX0DER",
                issue_type="inventory_data_quality",
                severity="high",
                title="Inventory freshness missing",
                summary="Inventory evidence is incomplete.",
                status="open",
                confidence=Decimal("0.60"),
                priority_score=Decimal("90"),
                data_freshness_at=NOW,
                evidence=(),
                missing_data=("inventory_observed_at",),
                degraded_reason="inventory_freshness_unknown",
                currency="USD",
                estimated_revenue_impact=None,
                estimated_profit_impact=Decimal("25.00"),
                estimated_cost_impact=None,
            )
        ]


class ActionRepository:
    def list_actions(self, *, tenant_id: str, status: str | None = None):
        return [
            SimpleNamespace(
                id="action-1",
                tenant_id=tenant_id,
                issue_id="issue-1",
                action_type="review_inventory_evidence",
                title="Review inventory evidence",
                description="Collect a fresh snapshot.",
                execution_mode="recommendation_only",
                risk_level="high",
                status="proposed",
                parameters={"snapshot_id": "inventory-1"},
                evidence=(),
                expected_outcome="Fresh deterministic inventory evidence.",
                confidence=Decimal("0.60"),
                automation_eligible=False,
                currency=None,
                expected_revenue_impact=None,
                expected_profit_impact=None,
                expected_cost_impact=None,
            )
        ]


class FakeWorkflow:
    async def run_advisory(self, **kwargs):
        self.kwargs = kwargs
        return {"run_id": "run-1", "status": "succeeded", "analysis": {"agent_type": kwargs["advisor"].agent_type}}


def test_operations_brief_collector_uses_persisted_tenant_scoped_records() -> None:
    collector = OperationsBriefEvidenceCollector(
        brief_repository=BriefRepository(),
        issue_repository=IssueRepository(),
        action_repository=ActionRepository(),
    )

    bundle = asyncio.run(collector.collect(tenant_id="tenant-a", request={"brief_id": "brief-1"}))

    assert bundle.tenant_id == "tenant-a"
    assert {item.evidence_id for item in bundle.evidence} == {
        "brief-1",
        "issue-1",
        "action-1",
        "sku-health:GOLDEN-SKU-1:ATVPDKIKX0DER",
    }
    assert all(item.source == "mock" for item in bundle.evidence)
    assert not any(item.verified_amazon_data for item in bundle.evidence)
    issue = bundle.by_id()["issue-1"]
    assert issue.metadata["deterministic_values"]["priority_score"] == "90"
    assert issue.metadata["missing_data"] == ["inventory_observed_at"]


def test_operations_brief_collector_compacts_large_queue_without_losing_high_risk_coverage() -> None:
    issue_ids = tuple(f"issue-{index:02d}" for index in range(20))
    action_ids = tuple(f"action-{index:02d}" for index in range(20))

    class LargeBriefRepository:
        def get_latest(self, *, tenant_id: str):
            return SimpleNamespace(
                id="brief-large",
                tenant_id=tenant_id,
                brief_date=date(2026, 7, 16),
                timezone="UTC",
                generated_at=NOW,
                issue_counts={"critical": 10, "high": 10},
                domain_counts={"inventory": 20},
                total_open_issues=20,
                data_quality_issue_count=4,
                impact_by_currency={"USD": {"profit": "5000.00"}},
                unallocated_impact_issue_count=0,
                priority_issue_ids=issue_ids,
                priority_action_ids=action_ids,
                sku_health=tuple(
                    SimpleNamespace(
                        tenant_id=tenant_id,
                        seller_sku=f"MOCK-{index:02d}",
                        marketplace_id="ATVPDKIKX0DER",
                        health_status="critical" if index < 10 else "high",
                        highest_severity="critical" if index < 10 else "high",
                        open_issue_count=1,
                        issue_types=("inventory_stockout_risk",),
                        recommended_action_ids=(action_ids[index],),
                        impact_by_currency={"USD": {"profit": str(500 - index)}},
                        has_data_quality_risk=index % 5 == 0,
                        last_detected_at=NOW,
                    )
                    for index in range(20)
                ),
                source_manifest={"mode": "mock"},
            )

    class LargeIssueRepository:
        def list_issues(self, *, tenant_id: str):
            return [
                SimpleNamespace(
                    id=issue_ids[index],
                    tenant_id=tenant_id,
                    seller_sku=f"MOCK-{index:02d}",
                    marketplace_id="ATVPDKIKX0DER",
                    issue_type="inventory_stockout_risk",
                    severity="critical" if index < 10 else "high",
                    title=f"Risk {index}",
                    summary="Review risk.",
                    status="open",
                    confidence=Decimal("0.80"),
                    priority_score=Decimal(str(1000 - index)),
                    data_freshness_at=NOW,
                    evidence=(),
                    missing_data=("fresh_inventory",) if index % 5 == 0 else (),
                    degraded_reason="inventory_stale" if index % 5 == 0 else None,
                    currency="USD",
                    estimated_revenue_impact=Decimal(str(1000 - index)),
                    estimated_profit_impact=Decimal(str(500 - index)),
                    estimated_cost_impact=None,
                )
                for index in range(20)
            ]

    class LargeActionRepository:
        def list_actions(self, *, tenant_id: str, status: str | None = None):
            return [
                SimpleNamespace(
                    id=action_ids[index],
                    tenant_id=tenant_id,
                    issue_id=issue_ids[index],
                    action_type="review_replenishment",
                    title=f"Action {index}",
                    description="Review evidence.",
                    execution_mode="recommendation_only",
                    risk_level="high",
                    status="proposed",
                    parameters={"seller_sku": f"MOCK-{index:02d}"},
                    evidence=(),
                    expected_outcome="Restore coverage.",
                    confidence=Decimal("0.80"),
                    automation_eligible=False,
                    currency="USD",
                    expected_revenue_impact=None,
                    expected_profit_impact=None,
                    expected_cost_impact=None,
                )
                for index in range(20)
            ]

    collector = OperationsBriefEvidenceCollector(
        brief_repository=LargeBriefRepository(),
        issue_repository=LargeIssueRepository(),
        action_repository=LargeActionRepository(),
    )

    bundle = asyncio.run(collector.collect(tenant_id="tenant-a", request={"brief_id": "brief-large"}))

    assert len(bundle.evidence) <= 14
    assert bundle.evidence[0].evidence_type == "daily_operations_brief"
    packets = [item for item in bundle.evidence if item.evidence_type == "operations_risk_packet"]
    remainder = next(item for item in bundle.evidence if item.evidence_type == "operations_risk_remainder")
    represented_issue_ids = {item.metadata["issue"]["issue_id"] for item in packets}
    represented_issue_ids.update(item["issue_id"] for item in remainder.metadata["issues"])
    assert represented_issue_ids == set(issue_ids)
    assert all(item.metadata["recommended_actions"] for item in packets)
    assert {item["action_id"] for item in remainder.metadata["recommended_actions"]} == set(action_ids[12:])


def test_operations_brief_service_routes_explicit_advisor() -> None:
    workflow = FakeWorkflow()
    advisor = SimpleNamespace(agent_type="operations_brief_analyst", prompt_version="operations-brief-v1")
    collector = object()
    service = OperationsBriefAgentService(
        workflow=workflow,
        registry=AdvisorRegistry({"operations_brief_analysis": advisor}),
        evidence_collector=collector,
    )

    result = asyncio.run(
        service.analyze(tenant_id="tenant-a", actor_id="operator-1", correlation_id="corr-1", brief_id="brief-1")
    )

    assert result["status"] == "succeeded"
    assert workflow.kwargs["workflow_type"] == "operations_brief_analysis"
    assert workflow.kwargs["advisor"] is advisor
    assert workflow.kwargs["evidence_collector"] is collector
    assert workflow.kwargs["request"]["allowed_capabilities"] == ["read_evidence", "create_internal_task_draft"]


def test_operations_prompt_uses_model_facing_draft_contract() -> None:
    prompt = OPERATIONS_BRIEF_SYSTEM_PROMPT.lower()

    assert OPERATIONS_BRIEF_PROMPT_VERSION == "operations-brief-v7"
    assert "evidence is an array of evidence_id strings" in prompt
    assert "runtime adds identity" in prompt
    assert "including prohibited_actions" in prompt
    assert "field, reason, required_for, blocking, and suggested_source" in prompt
    assert "confidence must be 0.65 or lower" in prompt
    assert "no more than 5 proposed_actions" in prompt
    assert "operations_risk_remainder" in prompt
    assert "resource_id must exactly match" in prompt
    assert "never invent a cluster resource_id" in prompt
