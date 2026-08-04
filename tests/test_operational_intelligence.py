from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from api.config import Settings
from api.main import create_app
from domain.operational_intelligence import (
    AmazonOperationalSignal,
    calculate_advertising_metrics,
    diagnose_advertising,
    summarize_voc,
)
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.operational_intelligence import (
    AdvertisingRepository,
    AmazonOperationalSignalRepository,
    VoCRepository,
)
from infrastructure.repositories.work_items import OperationalWorkItemRepository

NOW = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)


def test_advertising_metrics_are_decimal_and_keep_zero_denominators_missing() -> None:
    metrics = calculate_advertising_metrics(
        spend=Decimal("100"),
        attributed_sales=Decimal("500"),
        total_sales=Decimal("1000"),
        impressions=10000,
        clicks=100,
        orders=10,
    )
    assert metrics["acos"] == Decimal("0.20")
    assert metrics["tacos"] == Decimal("0.10")
    assert metrics["ctr"] == Decimal("0.01")
    assert metrics["cvr"] == Decimal("0.10")
    assert metrics["cpc"] == Decimal("1")

    missing = calculate_advertising_metrics(
        spend=Decimal("12"),
        attributed_sales=None,
        total_sales=Decimal("0"),
        impressions=0,
        clicks=0,
        orders=0,
    )
    assert missing["acos"] is None
    assert missing["tacos"] is None
    assert missing["ctr"] is None
    assert missing["cvr"] is None
    assert missing["cpc"] is None
    assert "attributed_sales" in missing["missing_data"]
    assert "total_sales" in missing["missing_data"]


def test_advertising_diagnostics_create_actionable_reasons() -> None:
    diagnostic = diagnose_advertising(
        seller_sku="SKU-1",
        marketplace_id="ATVPDKIKX0DER",
        campaign_id="campaign-1",
        spend=Decimal("150"),
        attributed_sales=Decimal("300"),
        total_sales=Decimal("500"),
        impressions=1000,
        clicks=40,
        orders=0,
        budget=Decimal("180"),
        elapsed_days=2,
        period_days=7,
        break_even_acos=Decimal("0.30"),
        min_clicks_without_order=20,
    )
    codes = {item["code"] for item in diagnostic}
    assert "acos_above_break_even" in codes
    assert "click_waste_without_orders" in codes
    assert "budget_projected_to_exhaust_early" in codes


def test_voc_requires_minimum_sample_before_high_confidence_action() -> None:
    events = [{"reason_code": "missing_part", "quantity": 1, "observed_at": NOW.isoformat()} for _ in range(2)]
    summary = summarize_voc(
        tenant_id="tenant-a",
        seller_sku="SKU-1",
        marketplace_id="ATVPDKIKX0DER",
        returns=events,
        reviews=[{"rating": 1, "text": "missing part", "observed_at": NOW.isoformat()}],
        messages=[{"tag": "missing_part", "observed_at": NOW.isoformat()}],
        min_sample_size=5,
    )
    assert summary["status"] == "insufficient_sample"
    assert summary["recommended_action"] is None
    assert summary["sample_size"] == 4

    enough = summarize_voc(
        tenant_id="tenant-a",
        seller_sku="SKU-1",
        marketplace_id="ATVPDKIKX0DER",
        returns=events * 3,
        reviews=[],
        messages=[],
        min_sample_size=5,
    )
    assert enough["status"] == "actionable"
    assert enough["top_reason_code"] == "missing_part"
    assert enough["recommended_action"] is not None


def test_amazon_signal_requires_complete_provenance() -> None:
    with pytest.raises(ValueError):
        AmazonOperationalSignal(
            tenant_id="tenant-a",
            marketplace_id="ATVPDKIKX0DER",
            signal_type="buy_box_loss",
            resource_id="ASIN-1",
            severity="high",
            title="Buy Box lost",
            summary="Observed.",
            observed_at=NOW,
            collected_at=NOW,
            freshness_seconds=None,
            verified_amazon_data=True,
            source="mock",
            raw_status_code="BUY_BOX_LOST",
            evidence_manifest=[],
        )

    signal = AmazonOperationalSignal(
        tenant_id="tenant-a",
        marketplace_id="ATVPDKIKX0DER",
        signal_type="buy_box_loss",
        resource_id="ASIN-1",
        severity="high",
        title="Buy Box lost",
        summary="Observed.",
        observed_at=NOW,
        collected_at=NOW + timedelta(minutes=1),
        freshness_seconds=60,
        verified_amazon_data=True,
        source="amazon_sp_api",
        raw_status_code="BUY_BOX_LOST",
        evidence_manifest=[{"resource_id": "ASIN-1"}],
    )
    assert signal.verified_amazon_data is True


def test_advertising_snapshot_persists_diagnostics_and_work_items(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'advertising.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    repository = AdvertisingRepository(session_factory)
    snapshot = repository.save_daily_snapshot(
        tenant_id="tenant-a",
        payload={
            "marketplace_id": "ATVPDKIKX0DER",
            "seller_sku": "SKU-1",
            "entity_type": "campaign",
            "entity_id": "campaign-1",
            "campaign_id": "campaign-1",
            "period_start": NOW - timedelta(days=1),
            "period_end": NOW,
            "currency": "USD",
            "spend": "150",
            "attributed_sales": "300",
            "total_sales": "500",
            "budget": "180",
            "impressions": 1000,
            "clicks": 40,
            "orders": 0,
            "elapsed_days": "2",
            "period_days": "7",
            "break_even_acos": "0.30",
            "source": "manual",
            "verified_amazon_data": False,
        },
    )
    diagnostics = repository.diagnose_daily_snapshot(tenant_id="tenant-a", snapshot_id=snapshot["id"])
    assert {item["code"] for item in diagnostics} == {
        "acos_above_break_even",
        "click_waste_without_orders",
        "budget_projected_to_exhaust_early",
    }
    items = OperationalWorkItemRepository(session_factory).list(tenant_id="tenant-a")
    assert len(items) == 3
    assert all(item.domain == "advertising" and item.team == "growth" for item in items)


def test_amazon_signal_persists_provenance_and_creates_sla_work_item(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'amazon-signals.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    saved = AmazonOperationalSignalRepository(session_factory).upsert(
        AmazonOperationalSignal(
            tenant_id="tenant-a",
            marketplace_id="ATVPDKIKX0DER",
            signal_type="stranded_inventory",
            resource_id="SKU-STRANDED",
            severity="high",
            title="库存不可售",
            summary="Amazon stranded inventory signal.",
            observed_at=NOW,
            collected_at=NOW + timedelta(minutes=2),
            freshness_seconds=120,
            verified_amazon_data=True,
            source="amazon_sp_api",
            raw_status_code="STRANDED",
            evidence_manifest=[{"resource_id": "SKU-STRANDED"}],
        )
    )
    assert saved["work_item_id"] is not None
    item = OperationalWorkItemRepository(session_factory).get(tenant_id="tenant-a", work_item_id=saved["work_item_id"])
    assert item is not None
    assert item.team == "supply_chain"
    assert item.due_at == item.created_at + timedelta(hours=4)
    assert item.evidence_manifest[0]["verified_amazon_data"] is True


def test_voc_summary_generates_product_improvement_only_after_sample_threshold(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'voc.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    repository = VoCRepository(session_factory)
    for index in range(4):
        repository.save_return(
            tenant_id="tenant-a",
            payload={
                "external_return_id": f"return-{index}",
                "marketplace_id": "ATVPDKIKX0DER",
                "seller_sku": "SKU-1",
                "reason_code": "missing_part",
                "quantity": 1,
                "observed_at": NOW,
                "source": "manual",
            },
        )
    summary = repository.summarize(
        tenant_id="tenant-a",
        seller_sku="SKU-1",
        marketplace_id="ATVPDKIKX0DER",
        period_start=NOW - timedelta(days=1),
        period_end=NOW,
        min_sample_size=5,
    )
    assert summary["status"] == "insufficient_sample"
    assert summary["evidence_status"] == "insufficient"
    assert summary["route"] == {"domain": "fulfillment", "team": "operations"}
    assert summary["source"] == "deterministic_voc_aggregation"
    assert summary["verified_amazon_data"] is False
    assert OperationalWorkItemRepository(session_factory).list(tenant_id="tenant-a") == []
    repository.save_message_tag(
        tenant_id="tenant-a",
        payload={
            "external_message_id": "message-1",
            "marketplace_id": "ATVPDKIKX0DER",
            "seller_sku": "SKU-1",
            "tag": "missing_part",
            "observed_at": NOW,
            "source": "manual",
        },
    )
    summary = repository.summarize(
        tenant_id="tenant-a",
        seller_sku="SKU-1",
        marketplace_id="ATVPDKIKX0DER",
        period_start=NOW - timedelta(days=1),
        period_end=NOW,
        min_sample_size=5,
    )
    assert summary["status"] == "actionable"
    assert summary["evidence_status"] == "sufficient"
    assert summary["route"] == {"domain": "fulfillment", "team": "operations"}
    assert summary["work_item_id"] is not None
    assert len(OperationalWorkItemRepository(session_factory).list(tenant_id="tenant-a")) == 1


def test_intelligence_api_exposes_signals_and_advertising_diagnostics(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'intelligence-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(
        Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key")
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
                signal = await client.post(
                    "/api/amazon/signals",
                    headers=headers,
                    json={
                        "marketplace_id": "ATVPDKIKX0DER",
                        "signal_type": "buy_box_loss",
                        "resource_id": "ASIN-1",
                        "severity": "high",
                        "title": "Buy Box lost",
                        "summary": "Observed on Amazon.",
                        "observed_at": NOW.isoformat(),
                        "collected_at": (NOW + timedelta(minutes=1)).isoformat(),
                        "freshness_seconds": 60,
                        "verified_amazon_data": True,
                        "source": "amazon_sp_api",
                        "raw_status_code": "BUY_BOX_LOST",
                        "evidence_manifest": [{"resource_id": "ASIN-1"}],
                    },
                )
                assert signal.status_code == 200, signal.text
                listed = await client.get("/api/amazon/signals", headers=headers)
                assert listed.status_code == 200
                assert listed.json()["total"] == 1
                invalid_provenance = await client.post(
                    "/api/amazon/signals",
                    headers=headers,
                    json={
                        "marketplace_id": "ATVPDKIKX0DER",
                        "signal_type": "buy_box_loss",
                        "resource_id": "ASIN-INVALID",
                        "severity": "high",
                        "title": "Invalid provenance",
                        "summary": "Must be rejected.",
                        "observed_at": NOW.isoformat(),
                        "collected_at": (NOW + timedelta(minutes=1)).isoformat(),
                        "freshness_seconds": 60,
                        "verified_amazon_data": True,
                        "source": "manual",
                        "raw_status_code": "BUY_BOX_LOST",
                        "evidence_manifest": [{"resource_id": "ASIN-INVALID"}],
                    },
                )
                assert invalid_provenance.status_code == 422, invalid_provenance.text
                snapshot = await client.post(
                    "/api/advertising/snapshots",
                    headers=headers,
                    json={
                        "marketplace_id": "ATVPDKIKX0DER",
                        "seller_sku": "SKU-1",
                        "entity_type": "campaign",
                        "entity_id": "campaign-1",
                        "campaign_id": "campaign-1",
                        "period_start": (NOW - timedelta(days=1)).isoformat(),
                        "period_end": NOW.isoformat(),
                        "currency": "USD",
                        "spend": "150",
                        "attributed_sales": "300",
                        "total_sales": "500",
                        "budget": "180",
                        "impressions": 1000,
                        "clicks": 40,
                        "orders": 0,
                        "elapsed_days": "2",
                        "period_days": "7",
                        "break_even_acos": "0.30",
                        "source": "manual",
                        "verified_amazon_data": False,
                    },
                )
                assert snapshot.status_code == 200, snapshot.text
                assert {item["code"] for item in snapshot.json()["diagnostics"]} >= {
                    "click_waste_without_orders",
                    "acos_above_break_even",
                }
                diagnostics = await client.get(
                    "/api/advertising/diagnostics?page=1&page_size=1",
                    headers=headers,
                )
                assert diagnostics.status_code == 200, diagnostics.text
                assert diagnostics.json()["total"] >= 2
                assert diagnostics.json()["page"] == 1
                assert diagnostics.json()["page_size"] == 1
                assert diagnostics.json()["pages"] >= 2
                assert diagnostics.json()["has_next"] is True
                assert len(diagnostics.json()["items"]) == 1
                other_diagnostics = await client.get(
                    "/api/advertising/diagnostics?page=1&page_size=1",
                    headers={**headers, "X-Tenant-ID": "tenant-b"},
                )
                assert other_diagnostics.status_code == 200
                assert other_diagnostics.json()["total"] == 0
                oversized_diagnostics = await client.get(
                    "/api/advertising/diagnostics?page_size=201",
                    headers=headers,
                )
                assert oversized_diagnostics.status_code == 422
                search_term = await client.post(
                    "/api/advertising/search-terms",
                    headers=headers,
                    json={
                        "marketplace_id": "ATVPDKIKX0DER",
                        "seller_sku": "SKU-1",
                        "campaign_id": "campaign-1",
                        "ad_group_id": "ad-group-1",
                        "search_term": "insulated water bottle",
                        "period_start": (NOW - timedelta(days=1)).isoformat(),
                        "period_end": NOW.isoformat(),
                        "currency": "USD",
                        "spend": "50",
                        "attributed_sales": "100",
                        "impressions": 1000,
                        "clicks": 25,
                        "orders": 2,
                        "source": "mock",
                        "verified_amazon_data": False,
                    },
                )
                assert search_term.status_code == 200, search_term.text
                assert search_term.json()["search_term"]["metrics"] == {
                    "spend": "50",
                    "attributed_sales": "100",
                    "total_sales": None,
                    "impressions": 1000,
                    "clicks": 25,
                    "orders": 2,
                    "acos": "0.5000",
                    "tacos": None,
                    "ctr": "0.0250",
                    "cvr": "0.0800",
                    "cpc": "2.0000",
                    "missing_data": ["total_sales"],
                }
                other_tenant = await client.get(
                    "/api/advertising/search-terms",
                    headers={**headers, "X-Tenant-ID": "tenant-b"},
                )
                assert other_tenant.status_code == 200
                assert other_tenant.json()["total"] == 0
                work_items = await client.get("/api/work-items", headers=headers)
                assert work_items.status_code == 200
                assert work_items.json()["total"] == 4
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def test_search_term_api_is_tenant_scoped_and_paginated(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'search-term-pagination.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    repository = AdvertisingRepository(session_factory)
    for index in range(5):
        repository.save_search_term(
            tenant_id="tenant-a",
            payload={
                "marketplace_id": "ATVPDKIKX0DER",
                "seller_sku": "SKU-1",
                "campaign_id": "campaign-1",
                "ad_group_id": "ad-group-1",
                "search_term": f"term-{index}",
                "period_start": NOW - timedelta(days=index + 1),
                "period_end": NOW - timedelta(days=index),
                "currency": "USD",
                "spend": "10",
                "attributed_sales": "20",
                "impressions": 100,
                "clicks": 10,
                "orders": 1,
                "source": "mock",
                "verified_amazon_data": False,
            },
        )
    repository.save_search_term(
        tenant_id="tenant-b",
        payload={
            "marketplace_id": "ATVPDKIKX0DER",
            "seller_sku": "SKU-OTHER",
            "campaign_id": "campaign-other",
            "search_term": "other-tenant-term",
            "period_start": NOW - timedelta(days=1),
            "period_end": NOW,
            "currency": "USD",
            "spend": "10",
            "source": "mock",
            "verified_amazon_data": False,
        },
    )
    app = create_app(
        Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key")
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
                first = await client.get("/api/advertising/search-terms?page=1&page_size=2", headers=headers)
                second = await client.get("/api/advertising/search-terms?page=2&page_size=2", headers=headers)
                assert first.status_code == 200, first.text
                assert second.status_code == 200, second.text
                assert first.json()["total"] == 5
                assert first.json()["page"] == 1
                assert first.json()["page_size"] == 2
                assert first.json()["pages"] == 3
                assert first.json()["has_next"] is True
                assert len(first.json()["items"]) == 2
                assert second.json()["total"] == 5
                assert second.json()["page"] == 2
                assert len(second.json()["items"]) == 2
                assert {item["search_term"] for item in first.json()["items"]}.isdisjoint(
                    {item["search_term"] for item in second.json()["items"]}
                )
                assert all(item["seller_sku"] == "SKU-1" for item in first.json()["items"] + second.json()["items"])

                too_large = await client.get("/api/advertising/search-terms?page_size=201", headers=headers)
                assert too_large.status_code == 422
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
