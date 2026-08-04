from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from api.config import Settings
from api.main import create_app
from domain.models import InventoryHealthSnapshot, SKUProfitSnapshot
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import TenantRow

NOW = datetime(2026, 7, 14, 0, 0, tzinfo=UTC)


def _inventory_snapshot(
    *, tenant_id: str = "tenant-a", snapshot_id: str = "inventory-1", degraded: bool = False
) -> InventoryHealthSnapshot:
    return InventoryHealthSnapshot(
        id=snapshot_id,
        tenant_id=tenant_id,
        seller_sku="SKU-1",
        marketplace_id="ATVPDKIKX0DER",
        policy_id="policy-1",
        policy_version=1,
        available=20,
        reserved=0,
        inbound=0,
        in_transit=0,
        unsellable=0,
        inventory_position=20,
        weighted_daily_sales=Decimal("5"),
        days_of_supply=Decimal("4"),
        expected_stockout_date=NOW + timedelta(days=4),
        total_lead_time_days=35,
        safety_stock_units=Decimal("35"),
        reorder_point=Decimal("210"),
        recommended_reorder_quantity=400,
        excess_units=0,
        recommended_shipping_mode="air",
        inventory_health="stockout_risk",
        stockout_risk="critical",
        excess_risk="none",
        forecast_confidence=Decimal("0.40" if degraded else "0.95"),
        missing_data=("sales_30_days",) if degraded else (),
        data_warnings=("inventory_stale",) if degraded else (),
        degraded_reason="inventory_stale" if degraded else None,
        automation_eligible=not degraded,
        inventory_source="amazon_api",
        inventory_collected_at=NOW - timedelta(hours=8 if degraded else 1),
        calculated_at=NOW,
        calculation_version="inventory-health-v1",
        input_manifest={"available_is_net_of_reserved": True},
        estimated_revenue_at_risk=Decimal("1200.00"),
        estimated_profit_at_risk=Decimal("360.00"),
        estimated_extra_logistics_cost=Decimal("80.00"),
    )


def _profit_snapshot(*, tenant_id: str = "tenant-a", snapshot_id: str = "profit-1") -> SKUProfitSnapshot:
    return SKUProfitSnapshot(
        id=snapshot_id,
        tenant_id=tenant_id,
        seller_sku="SKU-1",
        marketplace_id="ATVPDKIKX0DER",
        currency="USD",
        window_start=NOW - timedelta(days=30),
        window_end=NOW,
        revenue=Decimal("1000.00"),
        units=100,
        refunds=Decimal("20.00"),
        promotions=Decimal("10.00"),
        calculated_at=NOW,
        calculation_version="profit-v1",
        missing_inputs=(),
        confidence=Decimal("0.96"),
        degraded_reason=None,
        input_manifest={"source": "amazon_api"},
        estimated=False,
        referral_fees=Decimal("150.00"),
        fulfillment_fees=Decimal("300.00"),
        storage_cost=Decimal("30.00"),
        advertising_cost=Decimal("250.00"),
        landed_cost=Decimal("350.00"),
        return_loss=Decimal("20.00"),
        other_variable_cost=Decimal("10.00"),
        contribution_profit=Decimal("-130.00"),
        contribution_margin=Decimal("-0.13"),
        break_even_price=Decimal("11.30"),
        break_even_acos=Decimal("0.12"),
    )


def test_detector_creates_ranked_stockout_and_loss_issues_with_evidence() -> None:
    from domain.operations import OperationalIssueDetector

    result = OperationalIssueDetector().detect(
        tenant_id="tenant-a",
        inventory_snapshots=[_inventory_snapshot()],
        profit_snapshots=[_profit_snapshot()],
        detected_at=NOW,
    )

    assert [issue.issue_type for issue in result.issues] == ["inventory_stockout_risk", "sku_contribution_loss"]
    stockout = result.issues[0]
    assert stockout.severity == "critical"
    assert stockout.estimated_revenue_impact == Decimal("1200.00")
    assert stockout.estimated_profit_impact == Decimal("360.00")
    assert stockout.evidence[0]["snapshot_id"] == "inventory-1"
    assert stockout.confidence == Decimal("0.95")
    assert stockout.dedupe_key

    replenishment = next(action for action in result.actions if action.issue_id == stockout.id)
    assert replenishment.action_type == "review_replenishment"
    assert replenishment.execution_mode == "recommendation_only"
    assert replenishment.parameters["recommended_reorder_quantity"] == 400
    assert replenishment.expected_profit_impact == Decimal("360.00")


def test_detector_does_not_flag_normal_inventory_buffer_as_excess() -> None:
    from domain.operations import OperationalIssueDetector

    snapshot = replace(
        _inventory_snapshot(),
        inventory_health="healthy",
        stockout_risk="low",
        excess_units=10,
        excess_risk="none",
        days_of_supply=Decimal("70"),
        estimated_revenue_at_risk=Decimal("0"),
        estimated_profit_at_risk=Decimal("0"),
        estimated_extra_logistics_cost=Decimal("0"),
    )
    result = OperationalIssueDetector().detect(
        tenant_id="tenant-a",
        inventory_snapshots=[snapshot],
        profit_snapshots=[],
        detected_at=NOW,
    )

    assert result.issues == ()
    assert result.actions == ()


def test_detector_does_not_create_per_sku_data_quality_noise_for_complete_mock_evidence() -> None:
    from domain.operations import OperationalIssueDetector

    snapshot = replace(
        _inventory_snapshot(),
        inventory_source="mock",
        forecast_confidence=Decimal("0.20"),
        degraded_reason="unverified_inventory_source",
        automation_eligible=False,
    )
    result = OperationalIssueDetector().detect(
        tenant_id="tenant-a",
        inventory_snapshots=[snapshot],
        profit_snapshots=[],
        detected_at=NOW,
    )

    assert [issue.issue_type for issue in result.issues] == ["inventory_stockout_risk"]


def test_detector_blocks_replenishment_first_action_for_loss_making_stockout() -> None:
    from domain.operations import OperationalIssueDetector

    snapshot = replace(
        _inventory_snapshot(),
        estimated_profit_at_risk=Decimal("0.00"),
        degraded_reason="negative_unit_contribution_profit",
        automation_eligible=False,
        input_manifest={"unit_contribution_profit": "-4.00"},
    )
    result = OperationalIssueDetector().detect(
        tenant_id="tenant-a",
        inventory_snapshots=[snapshot],
        profit_snapshots=[],
        detected_at=NOW,
    )

    stockout = next(issue for issue in result.issues if issue.issue_type == "inventory_stockout_risk")
    action = next(item for item in result.actions if item.issue_id == stockout.id)
    assert action.action_type == "review_loss_making_stockout"
    assert action.parameters["replenishment_blocked"] is True
    assert "profit" in action.expected_outcome.lower()
    assert all(issue.issue_type != "inventory_data_quality" for issue in result.issues)


def test_detector_emits_data_quality_issue_and_disables_automation_for_degraded_evidence() -> None:
    from domain.operations import OperationalIssueDetector

    result = OperationalIssueDetector().detect(
        tenant_id="tenant-a",
        inventory_snapshots=[_inventory_snapshot(degraded=True)],
        profit_snapshots=[],
        detected_at=NOW,
    )

    data_issue = next(issue for issue in result.issues if issue.issue_type == "inventory_data_quality")
    assert data_issue.severity == "high"
    assert data_issue.missing_data == ("sales_30_days",)
    assert data_issue.degraded_reason == "inventory_stale"
    refresh = next(action for action in result.actions if action.issue_id == data_issue.id)
    assert refresh.action_type == "refresh_inventory_data"
    assert refresh.automation_eligible is False


def test_detector_blocks_recommendations_when_inventory_signals_conflict() -> None:
    from domain.operations import OperationalIssueDetector

    contradictory = replace(
        _inventory_snapshot(),
        inventory_health="stockout_risk",
        stockout_risk="critical",
        excess_risk="high",
        excess_units=300,
    )

    result = OperationalIssueDetector().detect(
        tenant_id="tenant-a",
        inventory_snapshots=[contradictory],
        profit_snapshots=[],
        detected_at=NOW,
    )

    assert [issue.issue_type for issue in result.issues] == ["inventory_signal_conflict"]
    assert result.issues[0].degraded_reason == "conflicting_inventory_signals"
    assert result.actions[0].action_type == "reconcile_inventory_signals"
    assert result.actions[0].parameters["commercial_actions_blocked"] is True
    assert result.actions[0].automation_eligible is False


def test_issue_repository_deduplicates_and_enforces_tenant_scope(tmp_path) -> None:
    from domain.operations import OperationalIssueDetector
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository

    database_url = f"sqlite:///{tmp_path / 'operations.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    with session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))

    detected = OperationalIssueDetector().detect(
        tenant_id="tenant-a",
        inventory_snapshots=[_inventory_snapshot()],
        profit_snapshots=[],
        detected_at=NOW,
    )
    issue_repository = OperationalIssueRepository(session_factory)
    action_repository = RecommendedActionRepository(session_factory)

    first = issue_repository.upsert(detected.issues[0])
    detected.issues[0].severity = "high"
    repeated = issue_repository.upsert(detected.issues[0])
    assert repeated.id == first.id
    assert repeated.occurrence_count == 2
    assert repeated.severity == "high"
    assert len(issue_repository.list_issues(tenant_id="tenant-a")) == 1
    assert issue_repository.get(tenant_id="tenant-b", issue_id=first.id) is None

    saved_action = action_repository.upsert(detected.actions[0], canonical_issue_id=first.id)
    repeated_action = action_repository.upsert(detected.actions[0], canonical_issue_id=first.id)
    assert repeated_action.id == saved_action.id
    assert len(action_repository.list_actions(tenant_id="tenant-a", issue_id=first.id)) == 1
    assert action_repository.list_actions(tenant_id="tenant-b") == []
    accepted_action = action_repository.transition(
        tenant_id="tenant-a",
        action_id=saved_action.id,
        target_status="accepted",
    )
    assert accepted_action.status == "accepted"
    with pytest.raises(ValueError):
        action_repository.transition(
            tenant_id="tenant-a",
            action_id=saved_action.id,
            target_status="rejected",
            reason="invalid rollback",
        )

    acknowledged = issue_repository.transition(
        tenant_id="tenant-a",
        issue_id=first.id,
        target_status="acknowledged",
        actor_id="operator-1",
        note="reviewing",
    )
    assert acknowledged.status == "acknowledged"
    with pytest.raises(ValueError):
        issue_repository.transition(
            tenant_id="tenant-a",
            issue_id=first.id,
            target_status="open",
            actor_id="operator-1",
            note="invalid rollback",
        )


def test_complete_detection_resolves_active_issue_absent_from_latest_scan(tmp_path) -> None:
    from application.operations import OperationalIssueService
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository
    from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
    from infrastructure.repositories.profitability import SKUProfitSnapshotRepository

    database_url = f"sqlite:///{tmp_path / 'complete-detection.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    inventory_repository = InventoryHealthSnapshotRepository(session_factory)
    issue_repository = OperationalIssueRepository(session_factory)
    service = OperationalIssueService(
        inventory_repository=inventory_repository,
        profit_repository=SKUProfitSnapshotRepository(session_factory),
        issue_repository=issue_repository,
        action_repository=RecommendedActionRepository(session_factory),
    )
    unhealthy = _inventory_snapshot()
    healthy = replace(
        unhealthy,
        id="inventory-healthy",
        calculated_at=NOW + timedelta(hours=1),
        available=500,
        inventory_position=500,
        inventory_health="healthy",
        stockout_risk="low",
        excess_units=0,
        excess_risk="none",
        days_of_supply=Decimal("45"),
        estimated_revenue_at_risk=Decimal("0"),
        estimated_profit_at_risk=Decimal("0"),
    )
    inventory_repository.save(unhealthy)
    first = service.detect(
        tenant_id="tenant-a",
        inventory_snapshot_ids=[unhealthy.id],
        profit_snapshot_ids=[],
        complete_scan=True,
        detected_at=NOW,
    )
    assert first.issues

    inventory_repository.save(healthy)
    second = service.detect(
        tenant_id="tenant-a",
        inventory_snapshot_ids=[healthy.id],
        profit_snapshot_ids=[],
        complete_scan=True,
        detected_at=NOW + timedelta(hours=1),
    )

    assert second.issues == ()
    resolved = issue_repository.get(tenant_id="tenant-a", issue_id=first.issues[0].id)
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolution_code == "condition_absent_from_complete_scan"


def test_partial_detection_never_auto_resolves_absent_issue(tmp_path) -> None:
    from application.operations import OperationalIssueService
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository
    from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
    from infrastructure.repositories.profitability import SKUProfitSnapshotRepository

    database_url = f"sqlite:///{tmp_path / 'partial-detection.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    inventory_repository = InventoryHealthSnapshotRepository(session_factory)
    issue_repository = OperationalIssueRepository(session_factory)
    service = OperationalIssueService(
        inventory_repository=inventory_repository,
        profit_repository=SKUProfitSnapshotRepository(session_factory),
        issue_repository=issue_repository,
        action_repository=RecommendedActionRepository(session_factory),
    )
    unhealthy = _inventory_snapshot()
    healthy = replace(
        unhealthy,
        id="inventory-healthy",
        calculated_at=NOW + timedelta(hours=1),
        available=500,
        inventory_position=500,
        inventory_health="healthy",
        stockout_risk="low",
        excess_units=0,
        excess_risk="none",
        days_of_supply=Decimal("45"),
        estimated_revenue_at_risk=Decimal("0"),
        estimated_profit_at_risk=Decimal("0"),
    )
    inventory_repository.save(unhealthy)
    first = service.detect(
        tenant_id="tenant-a",
        inventory_snapshot_ids=[unhealthy.id],
        profit_snapshot_ids=[],
        complete_scan=True,
    )
    inventory_repository.save(healthy)
    service.detect(
        tenant_id="tenant-a",
        inventory_snapshot_ids=[healthy.id],
        profit_snapshot_ids=[],
        complete_scan=False,
    )

    still_active = issue_repository.get(tenant_id="tenant-a", issue_id=first.issues[0].id)
    assert still_active is not None
    assert still_active.status != "resolved"


def test_detection_uses_latest_snapshot_per_marketplace_sku(tmp_path) -> None:
    from application.operations import OperationalIssueService
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.inventory import InventoryHealthSnapshotRepository
    from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository
    from infrastructure.repositories.profitability import SKUProfitSnapshotRepository

    database_url = f"sqlite:///{tmp_path / 'latest-snapshot.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    inventory_repository = InventoryHealthSnapshotRepository(session_factory)
    service = OperationalIssueService(
        inventory_repository=inventory_repository,
        profit_repository=SKUProfitSnapshotRepository(session_factory),
        issue_repository=OperationalIssueRepository(session_factory),
        action_repository=RecommendedActionRepository(session_factory),
    )
    historical_stockout = _inventory_snapshot()
    latest_healthy = replace(
        historical_stockout,
        id="inventory-latest",
        calculated_at=NOW + timedelta(hours=1),
        available=500,
        inventory_position=500,
        inventory_health="healthy",
        stockout_risk="low",
        excess_units=0,
        excess_risk="none",
        days_of_supply=Decimal("45"),
        estimated_revenue_at_risk=Decimal("0"),
        estimated_profit_at_risk=Decimal("0"),
    )
    inventory_repository.save(historical_stockout)
    inventory_repository.save(latest_healthy)

    result = service.detect(
        tenant_id="tenant-a",
        inventory_snapshot_ids=[latest_healthy.id, historical_stockout.id],
        profit_snapshot_ids=[],
        complete_scan=True,
    )

    assert result.issues == ()
    assert result.actions == ()


def test_operations_api_detects_lists_and_updates_tenant_scoped_issues(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'operations-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(
        Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key")
    )
    with app.state.session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    app.state.inventory_health_repository.save(_inventory_snapshot())
    app.state.profit_snapshot_repository.save(_profit_snapshot())

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers_a = {
                    "X-API-Key": "test-key",
                    "X-Tenant-ID": "tenant-a",
                    "X-User-ID": "operator-1",
                    "X-User-Role": "operator",
                    "X-Permission-Scopes": "read write",
                }
                detected = await client.post(
                    "/api/operations/issues/detect",
                    headers=headers_a,
                    json={"inventory_snapshot_ids": ["inventory-1"], "profit_snapshot_ids": ["profit-1"]},
                )
                assert detected.status_code == 200, detected.text
                assert detected.json()["issue_count"] == 2
                issue_id = detected.json()["issues"][0]["id"]

                listed = await client.get("/api/operations/issues?severity=critical", headers=headers_a)
                assert listed.status_code == 200
                assert listed.json()["total"] == 2
                assert listed.json()["items"][0]["severity"] == "critical"

                actions = await client.get(f"/api/operations/actions?issue_id={issue_id}", headers=headers_a)
                assert actions.status_code == 200
                assert actions.json()["total"] == 1
                high_risk_action = actions.json()["items"][0]
                assert high_risk_action["risk_level"] in {"high", "critical"}
                bypass = await client.patch(
                    f"/api/operations/actions/{high_risk_action['id']}",
                    headers=headers_a,
                    json={"status": "accepted", "reason": "bypass approval"},
                )
                assert bypass.status_code == 403

                updated = await client.patch(
                    f"/api/operations/issues/{issue_id}",
                    headers=headers_a,
                    json={"status": "acknowledged", "note": "owner assigned"},
                )
                assert updated.status_code == 200
                assert updated.json()["status"] == "acknowledged"

                headers_b = {**headers_a, "X-Tenant-ID": "tenant-b"}
                hidden = await client.get(f"/api/operations/issues/{issue_id}", headers=headers_b)
                assert hidden.status_code == 404
                foreign_snapshot = await client.post(
                    "/api/operations/issues/detect",
                    headers=headers_b,
                    json={"inventory_snapshot_ids": ["inventory-1"], "profit_snapshot_ids": []},
                )
                assert foreign_snapshot.status_code == 404
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
