from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from api.config import Settings
from api.main import create_app
from domain.models import OperationalIssue, RecommendedAction
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import TenantRow
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
from infrastructure.repositories.work_items import OperationalWorkItemRepository

NOW = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)


def _issue(*, tenant_id: str = "tenant-a", suffix: str = "1") -> OperationalIssue:
    return OperationalIssue(
        id=f"issue-{suffix}",
        tenant_id=tenant_id,
        seller_sku=f"SKU-{suffix}",
        marketplace_id="ATVPDKIKX0DER",
        issue_type="inventory_stockout_risk",
        severity="critical",
        title="Stockout risk",
        summary="Coverage is below lead time.",
        status="open",
        dedupe_key=f"issue-dedupe-{suffix}",
        confidence=Decimal("0.90"),
        data_freshness_at=NOW,
        evidence=({"snapshot_id": "inventory-1"},),
        missing_data=(),
        detected_at=NOW,
        last_detected_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        priority_score=Decimal("5000"),
    )


def _action(*, tenant_id: str = "tenant-a", suffix: str = "1") -> RecommendedAction:
    return RecommendedAction(
        id=f"action-{suffix}",
        tenant_id=tenant_id,
        issue_id=f"issue-{suffix}",
        action_type="review_replenishment",
        title="Review replenishment plan",
        description="Validate demand and inbound stock.",
        execution_mode="single_approval",
        risk_level="critical",
        status="proposed",
        parameters={"seller_sku": f"SKU-{suffix}", "reorder_quantity": 600},
        evidence=({"snapshot_id": "inventory-1"},),
        expected_outcome="Avoid a preventable stockout.",
        confidence=Decimal("0.90"),
        automation_eligible=False,
        dedupe_key=f"action-dedupe-{suffix}",
        currency="USD",
        expected_revenue_impact=Decimal("1000"),
        expected_profit_impact=Decimal("250"),
        expected_cost_impact=Decimal("0"),
        created_at=NOW,
        updated_at=NOW,
    )


def test_recommended_action_creates_one_tenant_scoped_work_item(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'work-items.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    issues = OperationalIssueRepository(session_factory)
    actions = RecommendedActionRepository(session_factory)
    work_items = OperationalWorkItemRepository(session_factory)

    issues.upsert(_issue())
    actions.upsert(_action(), canonical_issue_id="issue-1")
    actions.upsert(_action(), canonical_issue_id="issue-1")

    items = work_items.list(tenant_id="tenant-a")
    assert len(items) == 1
    item = items[0]
    assert item.source_type == "recommended_action"
    assert item.source_id == "action-1"
    assert item.domain == "inventory"
    assert item.team == "supply_chain"
    assert item.status == "open"
    assert item.due_at == NOW + timedelta(hours=1)
    assert item.sla_policy_version == "ops-sla-v1"
    assert item.expected_outcome == "Avoid a preventable stockout."
    assert item.baseline_metrics["expected_revenue_impact"] == "1000"
    assert len(item.action_hash) == 64
    assert work_items.list(tenant_id="tenant-b") == []


def test_resolved_issue_completes_linked_work_item(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'work-item-resolution.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    issues = OperationalIssueRepository(session_factory)
    actions = RecommendedActionRepository(session_factory)
    work_items = OperationalWorkItemRepository(session_factory)

    issues.upsert(_issue())
    actions.upsert(_action(), canonical_issue_id="issue-1")
    item = work_items.list(tenant_id="tenant-a")[0]
    assert item.status == "open"

    issues.transition(
        tenant_id="tenant-a", issue_id="issue-1", target_status="acknowledged", actor_id="operator-1", note="Claimed"
    )
    issues.transition(
        tenant_id="tenant-a",
        issue_id="issue-1",
        target_status="in_progress",
        actor_id="operator-1",
        note="Investigating",
    )
    issues.transition(
        tenant_id="tenant-a",
        issue_id="issue-1",
        target_status="resolved",
        actor_id="operator-1",
        note="Verified resolved",
    )

    resolved = work_items.get(tenant_id="tenant-a", work_item_id=item.id)
    assert resolved is not None
    assert resolved.status == "completed"
    assert resolved.completed_at is not None


def test_work_item_bulk_collaboration_and_escalation_are_side_effect_free(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'work-item-bulk.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    OperationalIssueRepository(session_factory).upsert(_issue())
    RecommendedActionRepository(session_factory).upsert(_action(), canonical_issue_id="issue-1")
    repository = OperationalWorkItemRepository(session_factory)
    item = repository.list(tenant_id="tenant-a")[0]

    assigned = repository.bulk_update(
        tenant_id="tenant-a",
        work_item_ids=[item.id],
        operation="assign",
        actor_id="operator-1",
        assignee_id="operator-1",
    )
    assert assigned[0].assignee_id == "operator-1"
    assert assigned[0].status == "assigned"

    handed_off = repository.bulk_update(
        tenant_id="tenant-a",
        work_item_ids=[item.id],
        operation="handoff",
        actor_id="operator-1",
        assignee_id="operator-2",
        note="Continue supplier confirmation.",
    )
    assert handed_off[0].assignee_id == "operator-2"
    assert handed_off[0].handoff_note == "Continue supplier confirmation."

    repository.bulk_update(
        tenant_id="tenant-a",
        work_item_ids=[item.id],
        operation="set_due_at",
        actor_id="operator-2",
        due_at=NOW - timedelta(minutes=1),
    )
    escalated = repository.escalate_due(tenant_id="tenant-a", now=NOW)
    assert escalated[0].escalation_level == 1
    assert escalated[0].status == "assigned"
    assert escalated[0].result_metrics == {}
    assert escalated[0].verification_status == "pending"


def test_work_item_api_supports_views_workload_and_rejects_batch_approval(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'work-item-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    with session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="Tenant B", slug="tenant-b"))
    OperationalIssueRepository(session_factory).upsert(_issue())
    RecommendedActionRepository(session_factory).upsert(_action(), canonical_issue_id="issue-1")
    for suffix in ("2", "3"):
        OperationalIssueRepository(session_factory).upsert(_issue(suffix=suffix))
        RecommendedActionRepository(session_factory).upsert(
            _action(suffix=suffix),
            canonical_issue_id=f"issue-{suffix}",
        )
    app = create_app(
        Settings(
            database_url=database_url,
            auto_create_schema=False,
            api_key="test-key",
            auth_mode="api_key",
        )
    )

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            headers = {
                "X-API-Key": "test-key",
                "X-Tenant-ID": "tenant-a",
                "X-User-ID": "operator-1",
                "X-User-Role": "operator",
                "X-Permission-Scopes": "read write",
            }
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                unassigned = await client.get("/api/work-items?view=unassigned&page=1&page_size=2", headers=headers)
                assert unassigned.status_code == 200, unassigned.text
                assert unassigned.json()["total"] == 3
                assert unassigned.json()["page"] == 1
                assert unassigned.json()["page_size"] == 2
                assert unassigned.json()["pages"] == 2
                assert unassigned.json()["has_next"] is True
                assert len(unassigned.json()["items"]) == 2
                second_page = await client.get("/api/work-items?view=unassigned&page=2&page_size=2", headers=headers)
                assert second_page.status_code == 200, second_page.text
                assert second_page.json()["has_next"] is False
                assert len(second_page.json()["items"]) == 1
                assert {item["id"] for item in unassigned.json()["items"]}.isdisjoint(
                    {item["id"] for item in second_page.json()["items"]},
                )
                work_item_id = unassigned.json()["items"][0]["id"]

                direct = await client.get(f"/api/work-items/{work_item_id}", headers=headers)
                assert direct.status_code == 200, direct.text
                assert direct.json()["id"] == work_item_id

                foreign_direct = await client.get(
                    f"/api/work-items/{work_item_id}",
                    headers={**headers, "X-Tenant-ID": "tenant-b"},
                )
                assert foreign_direct.status_code == 404

                assigned = await client.post(
                    "/api/work-items/bulk",
                    headers=headers,
                    json={
                        "work_item_ids": [work_item_id],
                        "operation": "assign",
                        "assignee_id": "operator-1",
                    },
                )
                assert assigned.status_code == 200, assigned.text
                assert assigned.json()["items"][0]["status"] == "assigned"

                workload = await client.get("/api/work-items/workload", headers=headers)
                assert workload.status_code == 200
                assert any(item["assignee_id"] == "operator-1" for item in workload.json()["assignees"])

                forbidden = await client.post(
                    "/api/work-items/bulk",
                    headers=headers,
                    json={
                        "work_item_ids": [work_item_id],
                        "operation": "approve",
                    },
                )
                assert forbidden.status_code == 422

                foreign = await client.get(
                    "/api/work-items",
                    headers={**headers, "X-Tenant-ID": "tenant-b"},
                )
                assert foreign.status_code == 200
                assert foreign.json()["total"] == 0
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def test_celery_scheduler_escalates_due_work_items_without_external_actions(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'work-item-scheduler.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    OperationalIssueRepository(session_factory).upsert(_issue())
    RecommendedActionRepository(session_factory).upsert(_action(), canonical_issue_id="issue-1")
    item = OperationalWorkItemRepository(session_factory).list(tenant_id="tenant-a")[0]
    OperationalWorkItemRepository(session_factory).bulk_update(
        tenant_id="tenant-a",
        work_item_ids=[item.id],
        operation="set_due_at",
        actor_id="operator-1",
        due_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    from application import celery_app as celery_module

    monkeypatch.setattr(celery_module.settings, "database_url", database_url)
    result = celery_module.escalate_operational_work_items.run()

    current = OperationalWorkItemRepository(session_factory).get(tenant_id="tenant-a", work_item_id=item.id)
    assert result == {"tenants_scanned": 1, "escalated": 1}
    assert current is not None
    assert current.escalation_level == 1
    assert current.status == "open"
    assert celery_module.celery_app.conf.beat_schedule["escalate-operational-work-items"]["schedule"] == 300.0
