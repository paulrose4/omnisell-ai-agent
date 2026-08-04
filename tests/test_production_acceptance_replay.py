from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evals.production_acceptance import (
    evaluate_replay,
    generate_blind_review_template,
    generate_synthetic_replay,
    score_predictions,
)

NOW = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)


def test_prediction_scoring_calculates_recall_and_false_positive_rate() -> None:
    scores = score_predictions(
        gold={"a", "b", "c"},
        predicted={"a", "b", "noise"},
        high_priority_gold={"a", "c"},
    )

    assert scores["true_positive"] == 2
    assert scores["false_positive"] == 1
    assert scores["false_negative"] == 1
    assert scores["precision"] == "0.6667"
    assert scores["recall"] == "0.6667"
    assert scores["high_priority_recall"] == "0.5000"
    assert scores["dismissed_issue_rate"] == "0.3333"


def test_generated_replay_is_ninety_days_and_never_claims_verified_amazon_data() -> None:
    dataset = generate_synthetic_replay(days=90, end_at=NOW)

    assert dataset["metadata"]["day_count"] == 90
    assert dataset["metadata"]["classification"] == "synthetic_anonymized_replay"
    assert len({record["date"] for record in dataset["records"]}) == 90
    assert {record["domain"] for record in dataset["records"]} >= {
        "inventory",
        "profitability",
        "advertising",
        "listing",
        "voc",
        "amazon_signal",
    }
    assert all(record["source"] in {"mock", "manual", "csv_import", "system_estimate"} for record in dataset["records"])
    assert all(record["verified_amazon_data"] is False for record in dataset["records"])


def test_generated_replay_rejects_naive_end_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        generate_synthetic_replay(days=30, end_at=datetime(2026, 7, 20, 3, 0))


def test_blind_review_template_has_hidden_source_and_no_fabricated_scores() -> None:
    template = generate_blind_review_template(
        [
            {"case_id": "case-1", "candidate_id": "A", "content": "建议 A"},
            {"case_id": "case-1", "candidate_id": "B", "content": "建议 B"},
        ]
    )

    assert template["source_key_withheld"] is True
    assert template["minimum_named_reviewers"] == 3
    assert all(item["source"] == "withheld" for item in template["candidates"])
    assert all(value is None for item in template["candidates"] for value in item["scores"].values())
    assert all(item["adoption_decision"] is None for item in template["candidates"])


def test_replay_can_pass_synthetic_gates_but_cannot_grant_production_go() -> None:
    dataset = generate_synthetic_replay(days=90, end_at=NOW)
    report = evaluate_replay(dataset)

    assert report["dataset"]["verified_amazon_fact_count"] == 0
    assert report["synthetic_metrics"]["high_priority_recall"] == "1.0000"
    assert report["synthetic_metrics"]["dismissed_issue_rate"] < "0.1500"
    assert report["synthetic_metrics"]["safety_incident_count"] == 0
    assert report["gates"]["synthetic_rule_replay"]["status"] == "passed"
    assert report["gates"]["expert_blind_review"]["status"] == "not_verified"
    assert report["gates"]["real_amazon_traceability"]["status"] == "not_verified"
    assert report["gates"]["shadow_mode_business_effect"]["status"] == "not_verified"
    assert report["decisions"]["internal_read_only_pilot"] == "conditional_go"
    assert report["decisions"]["company_production"] == "no_go"


def test_every_degraded_detection_exposes_a_reason() -> None:
    report = evaluate_replay(generate_synthetic_replay(days=30, end_at=NOW))

    assert report["synthetic_metrics"]["degraded_reason_visibility"] == "1.0000"
    assert report["synthetic_metrics"]["degraded_without_reason"] == 0
