from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import asyncio

import httpx
import pytest

from commerce.adapters.mock_operations_intelligence import MockOperationsIntelligenceSource
from application.intelligence_ingestion import OperationsIntelligenceIngestionService
from api.config import Settings
from api.main import create_app
from domain.operations_learning import (
    ActivityEvent,
    IssueFeedbackLabel,
    assess_action_outcome,
    classify_ad_execution,
    calculate_labor_metrics,
    calculate_incremental_contribution_profit,
    evaluate_issue_feedback,
    evaluate_sponsored_products,
    generate_calibration_proposal,
    normalize_voc_reason,
    prepare_attribution_plan,
    route_voc_reason,
)
from infrastructure.database.bootstrap import initialize_database
from infrastructure.database.models import RuleVersionRow
from infrastructure.database.session import create_session_factory
from infrastructure.repositories.operational_intelligence import AdvertisingRepository, VoCRepository
from infrastructure.repositories.operations_learning import OperationsLearningRepository
from infrastructure.repositories.work_items import OperationalWorkItemRepository


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=UTC)


def test_sponsored_products_recommendations_respect_profit_and_inventory_guards() -> None:
    result = evaluate_sponsored_products(
        seller_sku="SKU-LOSS",
        marketplace_id="ATVPDKIKX0DER",
        campaign_id="campaign-1",
        spend=Decimal("40"),
        attributed_sales=Decimal("400"),
        total_sales=Decimal("500"),
        impressions=2000,
        clicks=30,
        orders=6,
        budget=Decimal("100"),
        elapsed_days=Decimal("2"),
        period_days=Decimal("7"),
        break_even_acos=Decimal("0.30"),
        contribution_profit=Decimal("-12.50"),
        inventory_status="critical_stockout",
    )

    assert result["metrics"]["acos"] == Decimal("0.1000")
    assert result["metrics"]["roas"] == Decimal("10.0000")
    assert {item["code"] for item in result["findings"]} >= {
        "low_acos_but_contribution_loss",
        "inventory_guard_blocks_growth",
    }
    assert all(candidate["automation_eligible"] is False for candidate in result["candidates"])
    assert all(candidate["action_type"] != "increase_bid_candidate" for candidate in result["candidates"])
    assert result["guards"]["growth_blocked"] is True


def test_voc_taxonomy_normalizes_reasons_and_routes_to_accountable_team() -> None:
    assert normalize_voc_reason("missing_part") == "wrong_or_missing_item"
    assert normalize_voc_reason("damaged_in_transit") == "shipping_damage"
    assert normalize_voc_reason("does_not_fit") == "size_or_fit"
    assert normalize_voc_reason("unmapped-provider-code") == "unknown"
    assert route_voc_reason("listing_mismatch") == {"domain": "listing", "team": "listing"}
    assert route_voc_reason("packaging_problem") == {"domain": "inventory", "team": "supply_chain"}
    assert route_voc_reason("product_defect") == {"domain": "product_quality", "team": "quality"}
    assert route_voc_reason("wrong_or_missing_item") == {"domain": "fulfillment", "team": "operations"}


def test_labor_and_feedback_metrics_use_explicit_events_not_page_presence() -> None:
    events = [
        ActivityEvent("task-1", "operator-1", "started", NOW),
        ActivityEvent("task-1", "operator-1", "paused", NOW + timedelta(minutes=12)),
        ActivityEvent("task-1", "operator-1", "resumed", NOW + timedelta(minutes=20)),
        ActivityEvent("task-1", "operator-1", "completed", NOW + timedelta(minutes=28)),
    ]
    labor = calculate_labor_metrics(events)
    assert labor["active_touch_seconds"] == 20 * 60
    assert labor["cycle_seconds"] == 28 * 60
    assert labor["waiting_seconds"] == 8 * 60

    labels = [
        IssueFeedbackLabel("issue-1", "high", True, True, "actionable"),
        IssueFeedbackLabel("issue-2", "medium", False, False, "threshold_not_applicable"),
        IssueFeedbackLabel("issue-3", "critical", True, True, "actionable"),
    ]
    score = evaluate_issue_feedback(labels=labels, missed_issue_severities=["high"])
    assert score["precision"] == Decimal("0.6667")
    assert score["recall"] == Decimal("0.6667")
    assert score["dismissed_as_false_positive_rate"] == Decimal("0.3333")
    assert score["critical_high_recall"] == Decimal("0.6667")


def test_action_outcome_attribution_never_claims_causality_without_design_evidence() -> None:
    observed = assess_action_outcome(
        baseline_metrics={"contribution_profit": Decimal("100")},
        result_metrics={"contribution_profit": Decimal("125")},
        primary_metric="contribution_profit",
        desired_direction="increase",
        execution_verified=True,
        confounders=["promotion_changed"],
    )
    assert observed["attribution_level"] == "observed_association"
    assert observed["outcome_status"] == "directional_improvement"
    assert observed["incremental_value"] == Decimal("25")
    assert observed["causal_claim_allowed"] is False

    causal = assess_action_outcome(
        baseline_metrics={"conversion_rate": Decimal("0.08")},
        result_metrics={"conversion_rate": Decimal("0.10")},
        primary_metric="conversion_rate",
        desired_direction="increase",
        execution_verified=True,
        randomized_experiment=True,
        minimum_sample_satisfied=True,
    )
    assert causal["attribution_level"] == "causal_experiment"
    assert causal["causal_claim_allowed"] is True

    inconclusive = assess_action_outcome(
        baseline_metrics={},
        result_metrics={"conversion_rate": Decimal("0.10")},
        primary_metric="conversion_rate",
        desired_direction="increase",
        execution_verified=True,
    )
    assert inconclusive["attribution_level"] == "inconclusive"
    assert inconclusive["incremental_value"] is None


def test_rule_calibration_requires_replay_gates_and_human_approval() -> None:
    blocked = generate_calibration_proposal(
        rule_key="advertising.acos_above_break_even",
        current_version="v1",
        current_parameters={"minimum_cycles": 2},
        candidate_parameters={"minimum_cycles": 3},
        label_count=29,
        replay_metrics={"precision": "0.90", "recall": "0.97", "critical_high_recall": "0.96", "false_positive_rate": "0.10", "expected_profit_risk": "0"},
    )
    assert blocked["eligible"] is False
    assert blocked["status"] == "insufficient_labels"

    eligible = generate_calibration_proposal(
        rule_key="advertising.acos_above_break_even",
        current_version="v1",
        current_parameters={"minimum_cycles": 2},
        candidate_parameters={"minimum_cycles": 3},
        label_count=30,
        replay_metrics={"precision": "0.90", "recall": "0.96", "critical_high_recall": "0.95", "false_positive_rate": "0.12", "expected_profit_risk": "0"},
    )
    assert eligible["eligible"] is True
    assert eligible["status"] == "pending_approval"
    assert eligible["publish_mode"] == "shadow_then_canary"


def test_ad_execution_policy_is_bounded_before_any_amazon_write() -> None:
    blocked = classify_ad_execution(
        action_type="increase_bid_candidate",
        verified_amazon_data=True,
        data_fresh_seconds=900,
        currency="USD",
        account_currency="USD",
        inventory_status="critical_stockout",
        contribution_profit="50",
    )
    assert blocked["eligible"] is False
    assert "inventory" in " ".join(blocked["reasons"])

    manual = classify_ad_execution(
        action_type="decrease_bid_candidate",
        verified_amazon_data=True,
        data_fresh_seconds=900,
        currency="USD",
        account_currency="USD",
        inventory_status="healthy",
        contribution_profit="50",
    )
    assert manual["eligible"] is True
    assert manual["execution_mode"] == "manual_confirm"
    assert manual["amazon_write_performed"] is False


def test_attribution_plan_is_preregistered_and_profit_is_incremental() -> None:
    plan = prepare_attribution_plan(
        primary_metric="contribution_profit",
        desired_direction="increase",
        baseline_start=NOW - timedelta(days=28),
        baseline_end=NOW,
        observation_start=NOW + timedelta(hours=2),
        observation_end=NOW + timedelta(days=14),
        currency="USD",
        randomized_experiment=False,
        has_control_group=True,
        minimum_sample=200,
    )
    assert plan["method"] == "difference_in_differences"
    assert plan["causal_claim_allowed"] is False
    assert calculate_incremental_contribution_profit(
        baseline={"contribution_profit": "100"},
        result={"contribution_profit": "135"},
    ) == Decimal("35.0000")


def test_mock_intelligence_source_is_decoupled_and_never_claims_amazon_verification() -> None:
    batch = MockOperationsIntelligenceSource(reference_time=NOW).collect(
        tenant_id="tenant-a",
        marketplace_id="ATVPDKIKX0DER",
    )
    assert batch.source == "mock"
    assert batch.verified_amazon_data is False
    assert batch.advertising_snapshots
    assert batch.search_terms
    assert batch.return_events
    assert batch.review_observations
    assert batch.message_tags
    assert all(item["source"] == "mock" for item in batch.all_records())
    assert all(item["verified_amazon_data"] is False for item in batch.all_records())


def _create_advertising_work_item(session_factory):
    repository = AdvertisingRepository(session_factory)
    snapshot = repository.save_daily_snapshot(tenant_id="tenant-a", payload={
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
        "source": "mock",
        "verified_amazon_data": False,
    })
    repository.diagnose_daily_snapshot(tenant_id="tenant-a", snapshot_id=snapshot["id"])
    return OperationalWorkItemRepository(session_factory).list(tenant_id="tenant-a")[0]


def test_learning_repository_persists_feedback_time_execution_and_outcome(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'learning.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    item = _create_advertising_work_item(session_factory)
    repository = OperationsLearningRepository(session_factory)

    feedback = repository.record_issue_feedback(
        tenant_id="tenant-a",
        issue_id=item.operational_issue_id,
        actor_id="operator-1",
        is_correct=True,
        is_useful=True,
        reason_code="actionable",
        rule_version="advertising-v1",
        note="Evidence matches the Ads console.",
    )
    assert feedback["tenant_id"] == "tenant-a"
    assert repository.list_issue_feedback(tenant_id="tenant-b") == []

    repository.report_missed_issue(
        tenant_id="tenant-a",
        actor_id="operator-1",
        marketplace_id="ATVPDKIKX0DER",
        seller_sku="SKU-MISSED",
        issue_type="stockout_risk",
        severity="high",
        description="Operator found a missed stockout risk.",
        evidence_manifest=[{"source": "manual", "verified_amazon_data": False}],
    )
    for event_type, minute in (("started", 0), ("paused", 12), ("resumed", 20), ("completed", 28)):
        repository.record_activity(
            tenant_id="tenant-a",
            work_item_id=item.id,
            actor_id="operator-1",
            event_type=event_type,
            occurred_at=NOW + timedelta(minutes=minute),
            idempotency_key=f"task-1:{event_type}",
        )
    labor = repository.labor_metrics(tenant_id="tenant-a", work_item_id=item.id, actor_id="operator-1")
    assert labor["active_touch_seconds"] == 1200
    assert labor["waiting_seconds"] == 480

    execution = repository.record_execution(
        tenant_id="tenant-a",
        work_item_id=item.id,
        actor_id="operator-1",
        action_hash=item.action_hash,
        exact_parameters={"campaign_id": "campaign-1", "decision": "manual_pause"},
        execution_evidence={"source": "mock_seller_central", "verified_amazon_data": False},
        executed_at=NOW + timedelta(minutes=30),
        idempotency_key="execution-1",
    )
    assert execution["execution_mode"] == "manual_only"
    assert execution["amazon_write_performed"] is False

    outcome = repository.evaluate_outcome(
        tenant_id="tenant-a",
        work_item_id=item.id,
        execution_id=execution["id"],
        primary_metric="contribution_profit",
        desired_direction="increase",
        baseline_metrics={"contribution_profit": "100"},
        result_metrics={"contribution_profit": "125"},
        guardrail_metrics={"return_rate": "0.03"},
        observation_start=NOW + timedelta(minutes=30),
        observation_end=NOW + timedelta(days=7),
        confounders=["promotion_changed"],
        currency="USD",
    )
    assert outcome["attribution_level"] == "observed_association"
    assert outcome["causal_claim_allowed"] is False
    updated = OperationalWorkItemRepository(session_factory).get(tenant_id="tenant-a", work_item_id=item.id)
    assert updated is not None
    assert updated.verification_status == "succeeded"
    assert updated.result_metrics["attribution_level"] == "observed_association"

    metrics = repository.learning_metrics(tenant_id="tenant-a")
    assert metrics["feedback"]["true_positive"] == 1
    assert metrics["feedback"]["false_negative"] == 1
    assert metrics["labor"]["completed_sessions"] == 1


def test_governed_calibration_l1_authorization_and_preregistered_attribution(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'governed-learning.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    item = _create_advertising_work_item(session_factory)
    repository = OperationsLearningRepository(session_factory)

    initial = repository.create_rule_version(
        tenant_id="tenant-a", rule_key="advertising.acos_above_break_even",
        version="v1", parameters={"minimum_cycles": 2}, created_by="owner-1", status="active",
    )
    assert initial["status"] == "active"
    for index in range(30):
        repository.record_issue_feedback(
            tenant_id="tenant-a", issue_id=item.operational_issue_id,
            actor_id=f"operator-{index}", is_correct=True, is_useful=True,
            reason_code="actionable", rule_version="v1",
        )
    proposal = repository.create_calibration_proposal(
        tenant_id="tenant-a", rule_key="advertising.acos_above_break_even", base_version="v1",
        current_parameters={"minimum_cycles": 2}, candidate_parameters={"minimum_cycles": 3},
        replay_metrics={"precision": "0.90", "recall": "0.96", "critical_high_recall": "0.95", "false_positive_rate": "0.12", "expected_profit_risk": "0"},
        proposed_by="calibration-agent",
    )
    assert proposal["eligible"] is True
    approved = repository.approve_calibration_proposal(
        tenant_id="tenant-a", proposal_id=proposal["id"], approver_id="approver-1",
    )
    with session_factory.begin() as session:
        shadow_row = session.get(RuleVersionRow, approved["rule_version"]["id"])
        assert shadow_row is not None
        shadow_row.shadow_started_at = datetime.now(UTC) - timedelta(days=8)
    shadow = repository.complete_rule_shadow(
        tenant_id="tenant-a", rule_version_id=approved["rule_version"]["id"],
        shadow_metrics={"critical_high_recall": "0.96", "false_positive_rate": "0.10"}, actor_id="owner-1",
    )
    assert shadow["status"] == "shadow_ready"
    published = repository.publish_rule_version(
        tenant_id="tenant-a", rule_version_id=shadow["id"], actor_id="owner-1",
    )
    assert published["status"] == "active"
    rolled_back = repository.rollback_rule_version(
        tenant_id="tenant-a", active_rule_version_id=published["id"],
        target_rule_version_id=initial["id"], actor_id="owner-1",
    )
    assert rolled_back["rolled_back"]["status"] == "rolled_back"
    assert rolled_back["restored"]["status"] == "active"

    attribution = repository.register_attribution_plan(
        tenant_id="tenant-a", work_item_id=item.id, action_hash=item.action_hash,
        registered_by="operator-1", payload={
            "primary_metric": "contribution_profit", "desired_direction": "increase",
            "baseline_start": NOW - timedelta(days=28), "baseline_end": NOW,
            "observation_start": NOW + timedelta(hours=2), "observation_end": NOW + timedelta(days=14),
            "currency": "USD", "randomized_experiment": False, "has_control_group": True,
            "minimum_sample": 200, "guardrail_metrics": ["return_rate", "conversion_rate"],
            "control_spec": {"matched_campaign_id": "campaign-control"}, "confounders": ["promotion_changed"],
        },
    )
    assert attribution["method"] == "difference_in_differences"
    assert attribution["status"] == "planned"

    authorization = repository.prepare_execution_authorization(
        tenant_id="tenant-a", work_item_id=item.id, action_hash=item.action_hash,
        action_type="decrease_bid_candidate", exact_parameters={"bid_change_percent": "-10"}, before_snapshot={"bid": "1.00"},
        policy_input={"verified_amazon_data": True, "data_fresh_seconds": 600, "currency": "USD", "account_currency": "USD", "inventory_status": "healthy", "contribution_profit": "25"},
        requested_by="operator-1", idempotency_key="authorization-1",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert authorization["status"] == "approved"
    execution = repository.record_execution(
        tenant_id="tenant-a", work_item_id=item.id, actor_id="operator-1",
        action_hash=item.action_hash, authorization_id=authorization["id"],
        exact_parameters={"bid_change_percent": "-10"},
        execution_evidence={"source": "mock_seller_central", "verified_amazon_data": False},
        executed_at=datetime.now(UTC), idempotency_key="execution-governed-1",
    )
    assert execution["execution_mode"] == "manual_confirm"
    assert execution["amazon_write_performed"] is False
    assert repository.list_execution_authorizations(tenant_id="tenant-a", work_item_id=item.id)[0]["status"] == "consumed"

    medium_authorization = repository.prepare_execution_authorization(
        tenant_id="tenant-a", work_item_id=item.id, action_hash=item.action_hash,
        action_type="increase_bid_candidate", exact_parameters={"bid_change_percent": "10"}, before_snapshot={"bid": "0.90"},
        policy_input={"verified_amazon_data": True, "data_fresh_seconds": 600, "currency": "USD", "account_currency": "USD", "inventory_status": "healthy", "contribution_profit": "25"},
        requested_by="operator-1", idempotency_key="authorization-medium-1",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert medium_authorization["status"] == "pending_approval"
    with pytest.raises(ValueError, match="cannot approve"):
        repository.approve_execution_authorization(
            tenant_id="tenant-a", authorization_id=medium_authorization["id"], approver_id="operator-1",
        )
    independently_approved = repository.approve_execution_authorization(
        tenant_id="tenant-a", authorization_id=medium_authorization["id"], approver_id="approver-1",
    )
    assert independently_approved["status"] == "approved"


def test_mock_batch_ingests_through_shared_ports_and_existing_repositories(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'mock-ingestion.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    service = OperationsIntelligenceIngestionService(
        advertising_repository=AdvertisingRepository(session_factory),
        voc_repository=VoCRepository(session_factory),
        learning_repository=OperationsLearningRepository(session_factory),
    )
    result = service.ingest(
        source=MockOperationsIntelligenceSource(reference_time=NOW),
        tenant_id="tenant-a",
        marketplace_id="ATVPDKIKX0DER",
    )
    assert result == {
        "source": "mock",
        "verified_amazon_data": False,
        "advertising_snapshots": 1,
        "search_terms": 1,
        "advertising_diagnostics": 3,
        "return_events": 5,
        "review_observations": 1,
        "message_tags": 1,
        "voc_summaries": 1,
        "business_contexts": 1,
    }
    snapshots = AdvertisingRepository(session_factory).list_snapshots(tenant_id="tenant-a")
    assert snapshots[0]["source"] == "mock"
    assert snapshots[0]["verified_amazon_data"] is False
    summaries = VoCRepository(session_factory).list_summaries(tenant_id="tenant-a")
    assert summaries[0]["top_reason_code"] == "wrong_or_missing_item"
    assert summaries[0]["work_item_id"] is not None


def test_learning_api_and_mock_seed_are_tenant_scoped_and_advisory_only(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'learning-api.sqlite'}"
    initialize_database(database_url, default_tenant_id="tenant-a")
    session_factory = create_session_factory(database_url)
    item = _create_advertising_work_item(session_factory)
    app = create_app(Settings(
        database_url=database_url, auto_create_schema=False, api_key="test-key",
        auth_mode="api_key", environment="test", mock_drill_enabled=True,
    ))

    async def scenario() -> None:
        for handler in app.router.on_startup:
            value = handler()
            if hasattr(value, "__await__"):
                await value
        headers = {
            "X-API-Key": "test-key", "X-Tenant-ID": "tenant-a", "X-User-ID": "operator-1",
            "X-User-Role": "operator", "X-Permission-Scopes": "read write",
        }
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                feedback = await client.post(f"/api/learning/issues/{item.operational_issue_id}/feedback", headers=headers, json={
                    "is_correct": True, "is_useful": True, "reason_code": "actionable",
                    "rule_version": "advertising-v1", "note": "Validated against source data.",
                })
                assert feedback.status_code == 200, feedback.text
                cross_tenant = await client.post(
                    f"/api/learning/issues/{item.operational_issue_id}/feedback",
                    headers={**headers, "X-Tenant-ID": "tenant-b"},
                    json={"is_correct": False, "is_useful": False, "reason_code": "not_a_real_issue", "rule_version": "v1"},
                )
                assert cross_tenant.status_code == 404

                for event_type, minute in (("started", 0), ("paused", 2), ("resumed", 3), ("completed", 5)):
                    activity = await client.post("/api/learning/activities", headers={**headers, "Idempotency-Key": f"api:{event_type}"}, json={
                        "work_item_id": item.id, "event_type": event_type,
                        "occurred_at": (NOW + timedelta(minutes=minute)).isoformat(),
                    })
                    assert activity.status_code == 200, activity.text

                execution = await client.post("/api/learning/executions", headers={**headers, "Idempotency-Key": "api-execution-1"}, json={
                    "work_item_id": item.id, "action_hash": item.action_hash,
                    "exact_parameters": {"decision": "manual_pause"},
                    "execution_evidence": {"source": "mock_seller_central", "verified_amazon_data": False},
                    "executed_at": (NOW + timedelta(minutes=6)).isoformat(),
                })
                assert execution.status_code == 200, execution.text
                assert execution.json()["amazon_write_performed"] is False

                outcome = await client.post("/api/learning/outcomes", headers=headers, json={
                    "work_item_id": item.id, "execution_id": execution.json()["id"],
                    "primary_metric": "contribution_profit", "desired_direction": "increase",
                    "baseline_metrics": {"contribution_profit": "100"},
                    "result_metrics": {"contribution_profit": "110"}, "guardrail_metrics": {},
                    "observation_start": (NOW + timedelta(minutes=6)).isoformat(),
                    "observation_end": (NOW + timedelta(days=7)).isoformat(),
                    "confounders": ["promotion_changed"], "currency": "USD",
                })
                assert outcome.status_code == 200, outcome.text
                assert outcome.json()["attribution_level"] == "observed_association"
                assert outcome.json()["causal_claim_allowed"] is False

                metrics = await client.get("/api/learning/metrics", headers=headers)
                assert metrics.status_code == 200, metrics.text
                assert metrics.json()["labor"]["completed_sessions"] == 1

                seeded = await client.post("/api/mock-scenarios/operations-learning/seed", headers=headers)
                assert seeded.status_code == 200, seeded.text
                assert seeded.json()["source"] == "mock"
                assert seeded.json()["verified_amazon_data"] is False

                plan = await client.post("/api/learning/attribution-plans", headers=headers, json={
                    "work_item_id": item.id, "action_hash": item.action_hash,
                    "primary_metric": "contribution_profit", "desired_direction": "increase",
                    "baseline_start": (NOW - timedelta(days=28)).isoformat(),
                    "baseline_end": NOW.isoformat(),
                    "observation_start": (NOW + timedelta(hours=1)).isoformat(),
                    "observation_end": (NOW + timedelta(days=7)).isoformat(),
                    "currency": "USD", "has_control_group": True, "minimum_sample": 100,
                })
                assert plan.status_code == 200, plan.text
                assert plan.json()["method"] == "difference_in_differences"

                authorization = await client.post("/api/learning/execution-authorizations", headers={**headers, "Idempotency-Key": "api-authorization-1"}, json={
                    "work_item_id": item.id, "action_hash": item.action_hash,
                    "action_type": "decrease_bid_candidate", "exact_parameters": {"bid_change_percent": "-10"},
                    "verified_amazon_data": True, "data_fresh_seconds": 300,
                    "currency": "USD", "account_currency": "USD", "inventory_status": "healthy",
                    "contribution_profit": "20", "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                })
                assert authorization.status_code == 200, authorization.text
                assert authorization.json()["status"] == "approved"
        finally:
            for handler in app.router.on_shutdown:
                value = handler()
                if hasattr(value, "__await__"):
                    await value

    asyncio.run(scenario())
