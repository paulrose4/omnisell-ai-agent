from __future__ import annotations

from datetime import UTC, datetime

from application.high_fidelity_mock import HighFidelityStoreConfig, generate_high_fidelity_store
from commerce.adapters.high_fidelity_mock_intelligence import HighFidelityMockOperationsSource
from domain.operations_learning import assess_action_outcome

REFERENCE_TIME = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)


def _small_config() -> HighFidelityStoreConfig:
    return HighFidelityStoreConfig(
        seed=20260722,
        days=30,
        sku_count=20,
        order_count=240,
        campaign_count=10,
        search_term_count=80,
        return_count=30,
        message_count=40,
        review_count=20,
        customer_case_count=24,
        business_context_count=12,
    )


def test_high_fidelity_store_is_deterministic_relational_and_pii_free() -> None:
    first = generate_high_fidelity_store(config=_small_config(), reference_time=REFERENCE_TIME)
    second = generate_high_fidelity_store(config=_small_config(), reference_time=REFERENCE_TIME)

    assert first.fingerprint == second.fingerprint
    assert first.counts == {
        "skus": 20,
        "orders": 240,
        "inventory_snapshots": 600,
        "campaign_daily_snapshots": 300,
        "search_terms": 80,
        "returns": 30,
        "messages": 40,
        "reviews": 20,
        "customer_cases": 24,
        "business_contexts": 12,
        "data_quality_events": 5,
        "action_trials": 4,
        "ground_truth": 20,
    }
    sku_ids = {item["seller_sku"] for item in first.catalog}
    order_ids = {item["AmazonOrderId"] for item in first.orders}
    assert all(item["seller_sku"] in sku_ids for item in first.inventory_snapshots)
    assert all(item["external_order_id"] in order_ids for item in first.return_events)
    assert all(item["external_order_id"] in order_ids for item in first.customer_messages)
    assert all(item["source"] == "mock" and item["verified_amazon_data"] is False for item in first.all_records())
    serialized_messages = " ".join(item["message"] for item in first.customer_messages).lower()
    assert "@" not in serialized_messages
    assert "whatsapp" in serialized_messages  # Explicit compliance scenario, not contact data.
    assert not any(key in item for item in first.orders for key in ("BuyerName", "ShippingAddress", "BuyerInfo"))


def test_high_fidelity_store_models_amazon_report_failures_and_reprocessing() -> None:
    dataset = generate_high_fidelity_store(config=_small_config(), reference_time=REFERENCE_TIME)
    events = {item["event_type"]: item for item in dataset.data_quality_events}

    assert set(events) == {
        "late_report",
        "missing_report_partition",
        "report_restatement",
        "cross_currency_quarantine",
        "duplicate_notification",
    }
    assert events["late_report"]["expected_behavior"] == "degrade_freshness_no_business_alert"
    assert events["missing_report_partition"]["expected_behavior"] == "mark_incomplete_no_business_alert"
    assert events["report_restatement"]["report_version"] == 2
    assert events["cross_currency_quarantine"]["currency"] == "EUR"
    assert events["cross_currency_quarantine"]["expected_behavior"] == "quarantine_currency_mismatch"
    assert events["duplicate_notification"]["delivery_attempt"] == 2
    assert all(item["idempotency_key"] for item in dataset.data_quality_events)


def test_high_fidelity_action_trials_reconcile_with_deterministic_attribution() -> None:
    dataset = generate_high_fidelity_store(config=_small_config(), reference_time=REFERENCE_TIME)
    expected_levels = {"observed_association", "quasi_experimental", "causal_experiment", "inconclusive"}

    assert {item["expected_attribution_level"] for item in dataset.action_trials} == expected_levels
    for trial in dataset.action_trials:
        assessment = assess_action_outcome(
            baseline_metrics=trial["baseline_metrics"],
            result_metrics=trial["result_metrics"],
            primary_metric=trial["primary_metric"],
            desired_direction=trial["desired_direction"],
            execution_verified=trial["execution_verified"],
            confounders=trial["confounders"],
            control_metrics=trial["control_metrics"],
            randomized_experiment=trial["randomized_experiment"],
            minimum_sample_satisfied=trial["minimum_sample_satisfied"],
        )
        assert assessment["attribution_level"] == trial["expected_attribution_level"]
        assert assessment["outcome_status"] == trial["expected_outcome_status"]
        assert assessment["causal_claim_allowed"] is trial["causal_claim_allowed"]
        assert trial["amazon_write_performed"] is False
        assert len(trial["action_hash"]) == 64


def test_high_fidelity_ground_truth_covers_operational_and_customer_service_risks() -> None:
    dataset = generate_high_fidelity_store(config=_small_config(), reference_time=REFERENCE_TIME)
    scenarios = {item["scenario_id"] for item in dataset.ground_truth}
    assert scenarios >= {
        "healthy",
        "high_acos",
        "low_acos_contribution_loss",
        "waste_clicks",
        "budget_exhaustion",
        "inventory_guard",
        "packaging_damage",
        "wrong_or_missing_item",
        "listing_mismatch",
        "planned_promotion_suppression",
    }
    assert any(item["expected_alert"] is False for item in dataset.ground_truth)
    assert any(item["expected_severity"] == "critical" for item in dataset.ground_truth)
    intents = {item["scenario_id"] for item in dataset.customer_messages}
    assert intents >= {"delivery_delay", "refund_demand", "damaged_item", "off_platform_contact", "review_incentive"}


def test_high_fidelity_intelligence_adapter_preserves_mock_provenance() -> None:
    dataset = generate_high_fidelity_store(config=_small_config(), reference_time=REFERENCE_TIME)
    batch = HighFidelityMockOperationsSource(dataset).collect(
        tenant_id="tenant-a",
        marketplace_id="ATVPDKIKX0DER",
    )
    assert batch.source == "mock"
    assert batch.verified_amazon_data is False
    assert len(batch.advertising_snapshots) == 300
    assert len(batch.search_terms) == 80
    assert len(batch.return_events) == 30
    assert len(batch.message_tags) == 40
    assert len(batch.review_observations) == 20
    assert all(item["tenant_id"] == "tenant-a" for item in batch.all_records())
    assert all(item["marketplace_id"] == "ATVPDKIKX0DER" for item in batch.all_records())
    observations = [item["observed_at"] for item in batch.all_records() if "observed_at" in item]
    assert observations
    assert all(isinstance(value, datetime) and value.tzinfo is not None for value in observations)
    assert all(isinstance(item["starts_at"], datetime) for item in batch.business_contexts)
    assert all(isinstance(item["ends_at"], datetime) for item in batch.business_contexts)
