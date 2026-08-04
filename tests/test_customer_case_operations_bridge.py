from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from domain.customer_service import CustomerServiceCase
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import (
    CustomerCaseClusterMemberRow,
    CustomerCaseClusterRow,
    CustomerCaseEventRow,
    OperationalIssueRow,
    OperationalWorkItemRow,
    OutboxEventRow,
)
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.customer_case_operations import CustomerCaseOperationsBridge
from infrastructure.repositories.customer_service import CustomerCaseRepository
from infrastructure.repositories.tasks import EventRepository


NOW = datetime(2026, 7, 23, 3, 0, tzinfo=UTC)


def _case(*, suffix: str, tenant_id: str = "tenant-a", seller_sku: str = "SKU-1", root_cause: str = "packaging_problem", created_at: datetime = NOW, verified: bool = False) -> CustomerServiceCase:
    return CustomerServiceCase(
        id=f"case-{tenant_id}-{suffix}", tenant_id=tenant_id, external_case_id=f"external-{suffix}",
        marketplace_id="ATVPDKIKX0DER", external_order_id=f"order-{suffix}", seller_sku=seller_sku,
        language="en-US", intent="damaged_item", customer_message="Synthetic package damage report.",
        severity="high", risk_level="high", priority_score=Decimal("90"), status="resolved",
        source="amazon_customer_service" if verified else "mock_buyer", verified_amazon_data=verified,
        resolution_code="replacement_sent", root_cause_code=root_cause, evidence={
            "data_source": "amazon_customer_service" if verified else "mock_buyer",
            "verified_amazon_data": verified,
        }, created_at=created_at, updated_at=created_at, resolved_at=created_at,
    )


def _setup(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'case-bridge.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    return session_factory, CustomerCaseRepository(session_factory), CustomerCaseOperationsBridge(
        session_factory, minimum_samples=3, window_days=7,
    )


def test_case_root_cause_threshold_creates_one_cross_team_work_item(tmp_path) -> None:
    session_factory, cases, bridge = _setup(tmp_path)

    for index in range(1, 3):
        case = cases.create(_case(suffix=str(index)), actor_id="support-1")
        result = bridge.process_case(tenant_id="tenant-a", case_id=case.id, actor_id="case-bridge")
        assert result["cluster"]["status"] == "collecting"
        assert result["work_item"] is None

    third = cases.create(_case(suffix="3"), actor_id="support-1")
    result = bridge.process_case(tenant_id="tenant-a", case_id=third.id, actor_id="case-bridge")
    assert result["cluster"]["status"] == "linked"
    assert result["cluster"]["sample_count"] == 3
    assert result["work_item"]["team"] == "supply_chain"
    assert result["work_item"]["domain"] == "inventory"
    assert result["work_item"]["source_type"] == "customer_case_cluster"
    assert result["work_item"]["baseline_metrics"]["related_case_count"] == 3

    fourth = cases.create(_case(suffix="4"), actor_id="support-1")
    repeated = bridge.process_case(tenant_id="tenant-a", case_id=fourth.id, actor_id="case-bridge")
    assert repeated["cluster"]["id"] == result["cluster"]["id"]
    assert repeated["work_item"]["id"] == result["work_item"]["id"]

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CustomerCaseClusterRow)) == 1
        assert session.scalar(select(func.count()).select_from(CustomerCaseClusterMemberRow)) == 4
        assert session.scalar(select(func.count()).select_from(OperationalIssueRow)) == 1
        assert session.scalar(select(func.count()).select_from(OperationalWorkItemRow)) == 1


def test_clusters_never_cross_tenant_sku_root_cause_or_window(tmp_path) -> None:
    session_factory, cases, bridge = _setup(tmp_path)
    variants = (
        _case(suffix="a"),
        _case(suffix="b", seller_sku="SKU-2"),
        _case(suffix="c", root_cause="product_defect"),
        _case(suffix="d", created_at=NOW + timedelta(days=8)),
    )
    for item in variants:
        cases.create(item, actor_id="support-1")
        bridge.process_case(tenant_id=item.tenant_id, case_id=item.id, actor_id="case-bridge")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CustomerCaseClusterRow)) == 4


def test_case_and_outbox_are_atomic_idempotent_and_preserve_provenance(tmp_path) -> None:
    session_factory, cases, _ = _setup(tmp_path)
    case = _case(suffix="outbox")
    cases.create(case, actor_id="support-1")
    cases.create(case, actor_id="support-1")

    with session_factory() as session:
        rows = list(session.scalars(select(OutboxEventRow).where(
            OutboxEventRow.tenant_id == "tenant-a",
            OutboxEventRow.aggregate_id == case.id,
            OutboxEventRow.event_type == "case.created",
        )))
        assert len(rows) == 1
        assert rows[0].payload["verified_amazon_data"] is False
        assert rows[0].status == "pending"


def test_outbox_consumer_is_retryable_and_work_item_result_returns_to_cases(tmp_path) -> None:
    session_factory, cases, bridge = _setup(tmp_path)
    for index in range(3):
        case = cases.create(_case(suffix=str(index)), actor_id="support-1")
        bridge.process_case(tenant_id="tenant-a", case_id=case.id, actor_id="case-bridge")
    context = bridge.case_context(tenant_id="tenant-a", case_id="case-tenant-a-0")
    work_item_id = context["work_item"]["id"]

    with session_factory.begin() as session:
        row = session.scalar(select(OperationalWorkItemRow).where(OperationalWorkItemRow.id == work_item_id))
        row.status = "completed"
        row.completed_at = NOW
        row.result_metrics = {"packaging_spec_updated": True}
    propagated = bridge.propagate_completed(tenant_id="tenant-a", actor_id="case-bridge")
    assert propagated == 3
    assert cases.get(tenant_id="tenant-a", case_id="case-tenant-a-0").status == "resolved"
    timeline = cases.timeline(tenant_id="tenant-a", case_id="case-tenant-a-0")
    assert timeline[-1]["event_type"] == "root_cause_remediation_completed"
    assert timeline[-1]["payload"]["work_item_id"] == work_item_id

    events = EventRepository(session_factory)
    pending = events.claim_outbox(tenant_id="tenant-a", limit=100)
    assert pending
    events.mark_outbox_retry(tenant_id="tenant-a", event_id=pending[0].id, error=TimeoutError("retry"))
    summary = bridge.reconcile(tenant_id="tenant-a")
    assert summary["outbox_retry_pending"] == 1
    assert summary["orphan_case_members"] == 0
    assert summary["actionable_clusters_without_work_items"] == 0


def test_case_context_is_tenant_scoped_and_exposes_cluster_evidence(tmp_path) -> None:
    _, cases, bridge = _setup(tmp_path)
    for index in range(3):
        case = cases.create(_case(suffix=str(index)), actor_id="support-1")
        bridge.process_case(tenant_id="tenant-a", case_id=case.id, actor_id="case-bridge")
    context = bridge.case_context(tenant_id="tenant-a", case_id="case-tenant-a-0")
    assert context["cluster"]["sample_count"] == 3
    assert context["cluster"]["verified_amazon_data"] is False
    assert len(context["related_cases"]) == 3
    assert bridge.case_context(tenant_id="tenant-b", case_id="case-tenant-a-0") is None
