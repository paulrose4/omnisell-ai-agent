from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from api.config import Settings
from api.main import create_app
from infrastructure.database.bootstrap import initialize_database
from domain.models import Listing
from infrastructure.database.models import ChannelConnectionRow, TenantRow


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)


def test_synced_listing_snapshot_preserves_experiment_controls(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'listing-controls.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key"))
    with app.state.session_factory.begin() as session:
        session.add(ChannelConnectionRow(
            id="connection-1", tenant_id="tenant-a", provider="amazon", selling_partner_id="seller-1",
            region="NA", marketplace_ids=["ATVPDKIKX0DER"], secret_ref="mock", status="active", version=1,
        ))
    app.state.commerce_sync_repository.upsert_listing(Listing(
        tenant_id="tenant-a", channel_connection_id="connection-1", variant_id="placeholder",
        marketplace_id="ATVPDKIKX0DER", seller_sku="SKU-CONTROL", title="Controlled listing title",
        status="active", content_version=1, platform_extensions={
            "data_source": "mock_amazon", "observed_at": NOW.isoformat(), "sessions": 500,
            "units_ordered": 50, "conversion_rate": "0.10", "return_rate": "0.04",
            "ad_spend": "100", "ad_traffic_share": "0.40", "cpc": "1.20", "price": "29.99",
            "discount_rate": "0.10", "buy_box_share": "0.95", "sellable_units": 500,
            "out_of_stock_hours": "0", "natural_rank": "12", "promotion_active": False,
            "seasonality_key": "regular", "competitor_change": False,
        },
    ))

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            headers = {
                "X-API-Key": "test-key", "X-Tenant-ID": "tenant-a", "X-User-ID": "operator-1",
                "X-User-Role": "operator", "X-Permission-Scopes": "read write",
            }
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/listing-quality/snapshots/calculate-from-listing", headers=headers, json={
                    "connection_id": "connection-1", "seller_sku": "SKU-CONTROL",
                    "marketplace_id": "ATVPDKIKX0DER", "calculated_at": NOW.isoformat(),
                })
                assert response.status_code == 200, response.text
                metrics = response.json()["snapshot"]["baseline_metrics"]
                assert metrics == {
                    "sessions": 500, "units_ordered": 50, "conversion_rate": "0.10", "return_rate": "0.04",
                    "ad_spend": "100", "ad_traffic_share": "0.40", "cpc": "1.20", "price": "29.99",
                    "discount_rate": "0.10", "buy_box_share": "0.95", "sellable_units": 500,
                    "out_of_stock_hours": "0", "natural_rank": "12", "promotion_active": False,
                    "seasonality_key": "regular", "competitor_change": False,
                }
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def _quality_input(*, good: bool, conversion: str, return_rate: str):
    from domain.listing_quality import ListingQualityInput

    return ListingQualityInput(
        tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
        content_version=2 if good else 1,
        title=("Premium Stainless Steel Insulated Water Bottle for Travel and Daily Use, 24 oz" if good else "Bottle"),
        bullet_points=(
            "Double-wall vacuum insulation keeps beverages at the intended temperature.",
            "Food-grade stainless steel construction designed for repeated daily use.",
            "Leak-resistant lid supports commuting, gym and travel scenarios.",
            "24 oz capacity balances portability with everyday hydration needs.",
            "Hand-wash care guidance and complete dimensions are clearly disclosed.",
        ) if good else ("Nice bottle",),
        description=("Detailed product description with materials, dimensions, care instructions, intended use and package contents. " * 7) if good else "Short description.",
        image_count=7 if good else 1,
        search_terms=("insulated bottle", "stainless steel", "travel bottle", "water flask", "24 oz") if good else (),
        status="active" if good else "suppressed",
        source="amazon_api", source_observed_at=NOW - timedelta(hours=1), calculated_at=NOW,
        sessions=1000, units_ordered=100 if not good else 80,
        conversion_rate=Decimal(conversion), return_rate=Decimal(return_rate),
    )


def test_listing_quality_snapshot_scores_sections_and_generates_structured_suggestions() -> None:
    from domain.listing_quality import ListingQualityEvaluator

    snapshot = ListingQualityEvaluator().evaluate(_quality_input(good=False, conversion="0.10", return_rate="0.05"))

    assert snapshot.quality_score < Decimal("40")
    assert snapshot.quality_status == "critical"
    assert {issue["code"] for issue in snapshot.quality_issues} >= {
        "title_too_short", "insufficient_bullets", "description_too_short", "insufficient_images", "missing_search_terms", "listing_not_active",
    }
    assert {item["section"] for item in snapshot.recommendations} >= {"title", "bullets", "description", "images", "search_terms", "status"}
    assert snapshot.automation_eligible is False
    assert snapshot.evidence[0]["source"] == "amazon_api"


def test_listing_experiment_evaluation_recommends_rollback_when_business_metrics_worsen() -> None:
    from domain.listing_quality import ListingExperimentEvaluator, ListingOptimizationExperiment, ListingQualityEvaluator

    before = ListingQualityEvaluator().evaluate(_quality_input(good=False, conversion="0.10", return_rate="0.05"))
    after = ListingQualityEvaluator().evaluate(_quality_input(good=True, conversion="0.08", return_rate="0.08"))
    experiment = ListingOptimizationExperiment.create(
        tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
        before_snapshot=before, proposed_changes={"title": after.title, "bullet_points": list(after.bullet_points)},
        observation_start=NOW, observation_end=NOW + timedelta(days=14), created_at=NOW,
    )
    experiment.status = "approved"

    evaluated = ListingExperimentEvaluator().evaluate(experiment=experiment, before=before, after=after, evaluated_at=NOW + timedelta(days=15))

    assert evaluated.status == "rollback_recommended"
    assert evaluated.rollback_recommended is True
    assert evaluated.effect_metrics["quality_score_delta"] > "0"
    assert evaluated.effect_metrics["conversion_rate_relative_delta"] == "-0.2000"
    assert "conversion_drop_exceeds_guardrail" in evaluated.evaluation_reasons
    assert "return_rate_increase_exceeds_guardrail" in evaluated.evaluation_reasons


def test_listing_quality_repositories_are_tenant_scoped_and_versioned(tmp_path) -> None:
    from domain.listing_quality import ListingOptimizationExperiment, ListingQualityEvaluator
    from infrastructure.database.session import create_session_factory
    from infrastructure.repositories.listing_quality import ListingExperimentRepository, ListingQualitySnapshotRepository

    database_url = f"sqlite:///{tmp_path / 'listing-quality.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    with session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))
    snapshot_repository = ListingQualitySnapshotRepository(session_factory)
    experiment_repository = ListingExperimentRepository(session_factory)
    before = snapshot_repository.save(ListingQualityEvaluator().evaluate(_quality_input(good=False, conversion="0.10", return_rate="0.05")))
    experiment = ListingOptimizationExperiment.create(
        tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
        before_snapshot=before, proposed_changes={"title": "New title"}, observation_start=NOW,
        observation_end=NOW + timedelta(days=14), created_at=NOW,
    )
    saved = experiment_repository.save(experiment)

    assert snapshot_repository.get(tenant_id="tenant-a", snapshot_id=before.id) is not None
    assert snapshot_repository.get(tenant_id="tenant-b", snapshot_id=before.id) is None
    assert experiment_repository.get(tenant_id="tenant-a", experiment_id=saved.id) is not None
    assert experiment_repository.list_experiments(tenant_id="tenant-b") == []
    assert saved.action_hash
    assert saved.baseline_metrics["conversion_rate"] == "0.10"


def test_listing_quality_api_runs_approval_and_rollback_flow(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'listing-quality-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    app = create_app(Settings(database_url=database_url, auto_create_schema=False, api_key="test-key", auth_mode="api_key"))
    with app.state.session_factory.begin() as session:
        session.add(TenantRow(id="tenant-b", name="B", slug="tenant-b"))

    def payload(*, good: bool, conversion: str, return_rate: str) -> dict:
        item = _quality_input(good=good, conversion=conversion, return_rate=return_rate)
        return {
            "listing_id": item.listing_id, "seller_sku": item.seller_sku, "marketplace_id": item.marketplace_id,
            "content_version": item.content_version, "title": item.title, "bullet_points": list(item.bullet_points),
            "description": item.description, "image_count": item.image_count, "search_terms": list(item.search_terms),
            "status": item.status, "source": item.source, "source_observed_at": item.source_observed_at.isoformat(),
            "calculated_at": item.calculated_at.isoformat(), "sessions": item.sessions, "units_ordered": item.units_ordered,
            "conversion_rate": str(item.conversion_rate), "return_rate": str(item.return_rate),
        }

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                owner = {
                    "X-API-Key": "test-key", "X-Tenant-ID": "tenant-a", "X-User-ID": "owner-1",
                    "X-User-Role": "owner", "X-Permission-Scopes": "read write admin",
                }
                before_response = await client.post("/api/listing-quality/snapshots/calculate", headers=owner, json=payload(good=False, conversion="0.10", return_rate="0.05"))
                assert before_response.status_code == 200, before_response.text
                before = before_response.json()["snapshot"]
                assert before_response.json()["operational_issue"]["issue_type"] == "listing_quality_risk"

                experiment_payload = {
                    "before_snapshot_id": before["id"],
                    "proposed_changes": {"title": "Improved title", "bullet_points": ["A", "B", "C", "D", "E"]},
                    "observation_start": "2026-07-14T12:00:00Z", "observation_end": "2026-07-28T12:00:00Z",
                }
                pending = await client.post("/api/listing-quality/experiments", headers=owner, json=experiment_payload)
                assert pending.status_code == 202, pending.text
                approval_id = pending.json()["approval_request"]["request_id"]
                assert pending.json()["approval_request"]["payload"]["action_hash"] == pending.json()["experiment"]["action_hash"]

                approver = {**owner, "X-User-ID": "approver-1", "X-User-Role": "approver"}
                approved = await client.post(
                    f"/api/admin/approvals/{approval_id}/approve", headers=approver,
                    json={"reason": "copy reviewed"},
                )
                assert approved.status_code == 200
                activated = await client.post(
                    "/api/listing-quality/experiments", headers=owner,
                    json={**experiment_payload, "approval_request_id": approval_id},
                )
                assert activated.status_code == 200, activated.text
                experiment = activated.json()["experiment"]
                assert experiment["status"] == "approved"

                execution = await client.post(
                    f"/api/listing-quality/experiments/{experiment['id']}/execution",
                    headers=owner,
                    json={
                        "action_hash": experiment["action_hash"],
                        "actual_changes": {"title": "Improved title"},
                        "execution_evidence": {"seller_central_reference": "SC-LISTING-001", "platform_write_executed": False},
                        "notes": "Operator applied the approved draft manually.",
                    },
                )
                assert execution.status_code == 200, execution.text
                assert execution.json()["experiment"]["execution_status"] == "executed"
                assert execution.json()["experiment"]["status"] == "observing"

                after_response = await client.post("/api/listing-quality/snapshots/calculate", headers=owner, json=payload(good=True, conversion="0.08", return_rate="0.08"))
                after = after_response.json()["snapshot"]
                evaluated = await client.post(
                    f"/api/listing-quality/experiments/{experiment['id']}/evaluate", headers=owner,
                    json={"after_snapshot_id": after["id"], "evaluated_at": "2026-07-29T12:00:00Z"},
                )
                assert evaluated.status_code == 200, evaluated.text
                assert evaluated.json()["status"] == "rollback_recommended"
                assert evaluated.json()["execution_status"] == "verified"

                decision = await client.post(
                    f"/api/listing-quality/experiments/{experiment['id']}/decision",
                    headers=approver,
                    json={"decision": "rollback", "reason": "Conversion and return-rate guardrails regressed."},
                )
                assert decision.status_code == 200, decision.text
                assert decision.json()["experiment"]["status"] == "rollback_planned"
                assert decision.json()["experiment"]["decision"] == "rollback"
                assert decision.json()["platform_write_executed"] is False

                listed = await client.get("/api/listing-quality/experiments", headers=owner)
                assert listed.status_code == 200
                assert listed.json()["total"] == 1
                tenant_b = {**owner, "X-Tenant-ID": "tenant-b"}
                hidden = await client.get("/api/listing-quality/experiments", headers=tenant_b)
                assert hidden.json()["total"] == 0
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())


def test_listing_experiment_guardrail_eval_dataset() -> None:
    import json
    from dataclasses import replace
    from pathlib import Path

    from domain.listing_quality import ListingExperimentEvaluator, ListingOptimizationExperiment, ListingQualityEvaluator

    cases = json.loads((Path(__file__).parents[1] / "evals" / "listing_experiment_guardrails.json").read_text(encoding="utf-8"))
    evaluator = ListingExperimentEvaluator()
    quality_evaluator = ListingQualityEvaluator()
    for case in cases:
        before_input = replace(
            _quality_input(good=False, conversion="0.10", return_rate="0.05"),
            conversion_rate=Decimal(case["before_conversion"]) if case["before_conversion"] is not None else None,
            return_rate=Decimal(case["before_return_rate"]) if case["before_return_rate"] is not None else None,
        )
        after_input = replace(
            _quality_input(good=True, conversion="0.10", return_rate="0.05"),
            conversion_rate=Decimal(case["after_conversion"]) if case["after_conversion"] is not None else None,
            return_rate=Decimal(case["after_return_rate"]) if case["after_return_rate"] is not None else None,
        )
        before = quality_evaluator.evaluate(before_input)
        after = quality_evaluator.evaluate(after_input)
        experiment = ListingOptimizationExperiment.create(
            tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
            before_snapshot=before, proposed_changes={"title": "Improved title"}, observation_start=NOW,
            observation_end=NOW + timedelta(days=14), created_at=NOW,
        )
        experiment.status = "approved"
        evaluated_at = NOW + timedelta(days=15 if case["evaluated_after_window"] else 1)
        if "expected_error" in case:
            try:
                evaluator.evaluate(experiment=experiment, before=before, after=after, evaluated_at=evaluated_at)
            except ValueError as exc:
                assert case["expected_error"] in str(exc), case["id"]
            else:
                raise AssertionError(f"{case['id']} should have been blocked")
            continue
        evaluated = evaluator.evaluate(experiment=experiment, before=before, after=after, evaluated_at=evaluated_at)
        assert evaluated.status == case["expected_status"], case["id"]
        for reason in case["expected_reasons"]:
            assert reason in evaluated.evaluation_reasons, case["id"]


def test_listing_experiment_attribution_requires_stable_controls() -> None:
    from dataclasses import replace

    from domain.listing_quality import ListingExperimentEvaluator, ListingOptimizationExperiment, ListingQualityEvaluator

    controls = {
        "ad_spend": Decimal("100"), "ad_traffic_share": Decimal("0.40"), "cpc": Decimal("1.20"),
        "price": Decimal("29.99"), "discount_rate": Decimal("0.10"), "buy_box_share": Decimal("0.95"),
        "sellable_units": 500, "out_of_stock_hours": Decimal("0"), "natural_rank": Decimal("12"),
        "promotion_active": False, "seasonality_key": "regular", "competitor_change": False,
    }
    before_input = replace(_quality_input(good=False, conversion="0.10", return_rate="0.05"), **controls)
    after_input = replace(_quality_input(good=True, conversion="0.11", return_rate="0.05"), **controls)
    evaluator = ListingQualityEvaluator()
    before = evaluator.evaluate(before_input)
    after = evaluator.evaluate(after_input)
    experiment = ListingOptimizationExperiment.create(
        tenant_id="tenant-a", listing_id="listing-1", seller_sku="SKU-1", marketplace_id="ATVPDKIKX0DER",
        before_snapshot=before, proposed_changes={"title": "Improved title"}, observation_start=NOW,
        observation_end=NOW + timedelta(days=14), created_at=NOW,
    )
    experiment.status = "approved"
    evaluated = ListingExperimentEvaluator().evaluate(experiment=experiment, before=before, after=after, evaluated_at=NOW + timedelta(days=15))
    assert evaluated.status == "directional_improvement"
    assert evaluated.effect_metrics["attribution_status"] == "directional_improvement"
    assert "control_variables_incomplete" not in evaluated.evaluation_reasons

    confounded_after = evaluator.evaluate(replace(after_input, ad_spend=Decimal("160")))
    experiment.status = "approved"
    confounded = ListingExperimentEvaluator().evaluate(experiment=experiment, before=before, after=confounded_after, evaluated_at=NOW + timedelta(days=15))
    assert confounded.status == "confounded"
    assert "confounded_ad_spend_drift" in confounded.evaluation_reasons
