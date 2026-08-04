from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from api.config import Settings
from api.main import create_app
from domain.models import OperationalIssue, RecommendedAction
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import TenantRow


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def _issue(
    *, issue_id: str, issue_type: str, severity: str, seller_sku: str,
    currency: str | None = None, revenue: str | None = None, profit: str | None = None,
    cost: str | None = None, degraded_reason: str | None = None,
) -> OperationalIssue:
    return OperationalIssue(
        id=issue_id, tenant_id="tenant-a", seller_sku=seller_sku, marketplace_id="ATVPDKIKX0DER",
        issue_type=issue_type, severity=severity, title=f"{seller_sku} {issue_type}", summary="deterministic finding",
        status="open", dedupe_key=f"dedupe-{issue_id}", confidence=Decimal("0.90"), data_freshness_at=NOW,
        evidence=({"snapshot_id": f"snapshot-{issue_id}"},), missing_data=(), detected_at=NOW, last_detected_at=NOW,
        currency=currency, estimated_revenue_impact=Decimal(revenue) if revenue else None,
        estimated_profit_impact=Decimal(profit) if profit else None,
        estimated_cost_impact=Decimal(cost) if cost else None, degraded_reason=degraded_reason,
    )


def _action(*, action_id: str, issue: OperationalIssue, action_type: str) -> RecommendedAction:
    return RecommendedAction(
        id=action_id, tenant_id=issue.tenant_id, issue_id=issue.id, action_type=action_type,
        title=f"Action for {issue.seller_sku}", description="review evidence", execution_mode="recommendation_only",
        risk_level=issue.severity, status="proposed", parameters={"seller_sku": issue.seller_sku},
        evidence=issue.evidence, expected_outcome="reduce risk", confidence=issue.confidence,
        automation_eligible=False, dedupe_key=f"action-{action_id}", created_at=NOW, updated_at=NOW,
        currency=issue.currency, expected_revenue_impact=issue.estimated_revenue_impact,
        expected_profit_impact=issue.estimated_profit_impact, expected_cost_impact=issue.estimated_cost_impact,
    )


def test_daily_brief_builder_ranks_priorities_and_keeps_currency_impacts_separate() -> None:
    from domain.briefing import DailyOperationsBriefBuilder

    stockout = _issue(issue_id="i-stock", issue_type="inventory_stockout_risk", severity="critical", seller_sku="SKU-A", currency="USD", revenue="1200", profit="360")
    loss = _issue(issue_id="i-loss", issue_type="sku_contribution_loss", severity="high", seller_sku="SKU-B", currency="EUR", profit="200")
    quality = _issue(issue_id="i-data", issue_type="inventory_data_quality", severity="medium", seller_sku="SKU-A", degraded_reason="inventory_stale")
    actions = [_action(action_id="a-stock", issue=stockout, action_type="review_replenishment"), _action(action_id="a-loss", issue=loss, action_type="review_profit_levers")]

    result = DailyOperationsBriefBuilder().build(
        tenant_id="tenant-a", brief_date=date(2026, 7, 14), timezone="America/Los_Angeles",
        generated_at=NOW, issues=[quality, loss, stockout], actions=actions,
    )

    assert result.brief.issue_counts == {"critical": 1, "high": 1, "medium": 1, "low": 0}
    assert result.brief.domain_counts["inventory"] == 2
    assert result.brief.domain_counts["profit"] == 1
    assert result.brief.impact_by_currency == {
        "USD": {"revenue": "1200", "profit": "360", "cost": "0"},
        "EUR": {"revenue": "0", "profit": "200", "cost": "0"},
    }
    assert result.brief.unallocated_impact_issue_count == 1
    assert result.brief.priority_issue_ids[:2] == ("i-stock", "i-loss")
    assert result.brief.priority_action_ids == ("a-stock", "a-loss")
    sku_a = next(item for item in result.sku_health if item.seller_sku == "SKU-A")
    assert sku_a.health_status == "critical"
    assert sku_a.open_issue_count == 2
    assert sku_a.has_data_quality_risk is True


def test_listing_basic_evaluator_is_honest_about_unknown_freshness() -> None:
    from domain.briefing import ListingBasicSignal, ListingIssueEvaluator

    result = ListingIssueEvaluator().evaluate(
        ListingBasicSignal(
            tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-L", marketplace_id="ATVPDKIKX0DER",
            status="suppressed", title=None, price_amount=None, price_currency=None, external_product_id=None,
            observed_at=None,
        ),
        assessed_at=NOW,
    )

    assert result is not None
    issue, action = result
    assert issue.issue_type == "listing_basic_quality"
    assert issue.severity == "high"
    assert set(issue.missing_data) == {"title", "price", "external_product_id", "listing_observed_at"}
    assert issue.degraded_reason == "listing_freshness_unknown"
    assert issue.confidence == Decimal("0.40")
    assert action.action_type == "review_listing_quality"
    assert action.execution_mode == "recommendation_only"


def test_listing_complete_scan_resolves_absent_quality_issue(tmp_path) -> None:
    from application.briefing import DailyOperationsBriefService
    from domain.briefing import ListingBasicSignal
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.briefing import DailyOperationsBriefRepository
    from infrastructure.repositories.operations import OperationalIssueRepository, RecommendedActionRepository

    database_url = f"sqlite:///{tmp_path / 'listing-complete-scan.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)

    class ListingSignals:
        items = [ListingBasicSignal(
            tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-L",
            marketplace_id="ATVPDKIKX0DER", status="buyable", title="",
            price_amount=Decimal("29.99"), price_currency="USD",
            external_product_id="B000000001", observed_at=NOW,
        )]

        def list_signals(self, *, tenant_id: str):
            return [item for item in self.items if item.tenant_id == tenant_id]

    listing_signals = ListingSignals()
    issue_repository = OperationalIssueRepository(session_factory)
    service = DailyOperationsBriefService(
        brief_repository=DailyOperationsBriefRepository(session_factory),
        listing_repository=listing_signals,
        issue_repository=issue_repository,
        action_repository=RecommendedActionRepository(session_factory),
    )

    service.generate(
        tenant_id="tenant-a", brief_date=date(2026, 7, 14), timezone="UTC",
        scan_listings=True, generated_at=NOW,
    )
    issue = issue_repository.list_issues(
        tenant_id="tenant-a", issue_type="listing_basic_quality", seller_sku="SKU-L",
    )[0]
    assert issue.status == "open"

    listing_signals.items = [ListingBasicSignal(
        tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-L",
        marketplace_id="ATVPDKIKX0DER", status="buyable",
        title="Complete and reviewable product title for the validation listing",
        price_amount=Decimal("29.99"), price_currency="USD",
        external_product_id="B000000001", observed_at=NOW,
    )]
    service.generate(
        tenant_id="tenant-a", brief_date=date(2026, 7, 14), timezone="UTC",
        scan_listings=True, generated_at=NOW,
    )

    resolved = issue_repository.get(tenant_id="tenant-a", issue_id=issue.id)
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.resolution_code == "condition_absent_from_complete_scan"


def test_daily_brief_repository_upserts_per_tenant_date_and_isolates_reads(tmp_path) -> None:
    from domain.briefing import DailyOperationsBriefBuilder
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.briefing import DailyOperationsBriefRepository

    database_url = f"sqlite:///{tmp_path / 'brief.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    with session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    issue = _issue(issue_id="i-1", issue_type="inventory_stockout_risk", severity="critical", seller_sku="SKU-A")
    built = DailyOperationsBriefBuilder().build(
        tenant_id="tenant-a", brief_date=date(2026, 7, 14), timezone="America/Los_Angeles",
        generated_at=NOW, issues=[issue], actions=[],
    )
    repository = DailyOperationsBriefRepository(session_factory)

    first = repository.save(built.brief)
    built.brief.issue_counts["high"] = 1
    repeated = repository.save(built.brief)
    assert repeated.id == first.id
    assert repeated.issue_counts["high"] == 1
    assert repository.get_latest(tenant_id="tenant-a") is not None
    assert repository.get_latest(tenant_id="tenant-b") is None


def test_daily_brief_api_returns_dashboard_and_sku_health_with_tenant_isolation(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'brief-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key"))
    with app.state.session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    stockout = _issue(issue_id="i-stock", issue_type="inventory_stockout_risk", severity="critical", seller_sku="SKU-A", currency="USD", revenue="1200", profit="360")
    loss = _issue(issue_id="i-loss", issue_type="sku_contribution_loss", severity="high", seller_sku="SKU-B", currency="USD", profit="200")
    saved_stockout = app.state.operational_issue_repository.upsert(stockout)
    saved_loss = app.state.operational_issue_repository.upsert(loss)
    app.state.recommended_action_repository.upsert(_action(action_id="a-stock", issue=stockout, action_type="review_replenishment"), canonical_issue_id=saved_stockout.id)
    app.state.recommended_action_repository.upsert(_action(action_id="a-loss", issue=loss, action_type="review_profit_levers"), canonical_issue_id=saved_loss.id)

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers_a = {
                    "X-API-Key": "test-key", "X-Tenant-ID": "tenant-a", "X-User-ID": "operator-1",
                    "X-User-Role": "operator", "X-Permission-Scopes": "read write",
                }
                generated = await client.post(
                    "/api/operations/briefs/generate", headers=headers_a,
                    json={"brief_date": "2026-07-14", "timezone": "America/Los_Angeles", "scan_listings": False},
                )
                assert generated.status_code == 200, generated.text
                assert generated.json()["brief"]["issue_counts"]["critical"] == 1
                assert generated.json()["brief"]["priority_issue_ids"][0] == "i-stock"
                assert generated.json()["sku_health"][0]["seller_sku"] == "SKU-A"

                dashboard = await client.get("/api/operations/dashboard", headers=headers_a)
                assert dashboard.status_code == 200
                assert dashboard.json()["brief"]["id"] == generated.json()["brief"]["id"]
                assert len(dashboard.json()["priorities"]) == 2
                assert dashboard.json()["queue_summary"]["urgent"] == 2
                assert dashboard.json()["queue_summary"]["shift_owner"] == "operator-1"

                health = await client.get("/api/operations/sku-health", headers=headers_a)
                assert health.status_code == 200
                assert health.json()["total"] == 2

                headers_b = {**headers_a, "X-Tenant-ID": "tenant-b"}
                hidden = await client.get("/api/operations/dashboard", headers=headers_b)
                assert hidden.status_code == 404
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
