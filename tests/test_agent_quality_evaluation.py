from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from evals.agent_quality import (
    evaluate_replay_records,
    load_dataset_catalog,
    load_replay_records,
    render_evaluation_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "evals" / "datasets"
FIXTURE_PATH = ROOT / "evals" / "fixtures" / "agent_outputs_v1.jsonl"


def test_versioned_agent_dataset_manifest_and_hashes_are_valid() -> None:
    catalog = load_dataset_catalog(DATASET_DIR)

    assert catalog.manifest.dataset_version == "agent-quality-v1"
    assert set(catalog.manifest.files) == {
        "operations_brief_v1.jsonl",
        "customer_case_v1.jsonl",
        "listing_optimization_v1.jsonl",
        "reconciliation_v1.jsonl",
        "adversarial_safety_v1.jsonl",
    }
    assert len(catalog.cases) >= 12
    assert all(case.evidence_class == "synthetic_fixed_fixture" for case in catalog.cases)
    assert all(not item.verified_amazon_data for case in catalog.cases for item in case.evidence_bundle.evidence)


def test_fixed_output_replay_scores_contract_and_safety_without_claiming_provider_quality() -> None:
    catalog = load_dataset_catalog(DATASET_DIR)
    records = load_replay_records(FIXTURE_PATH)

    report = evaluate_replay_records(catalog=catalog, records=records)

    synthetic = report["evidence_classes"]["synthetic_fixed_fixture"]
    assert synthetic["case_count"] == len(catalog.cases)
    assert synthetic["metrics"]["final_structured_success"]["rate"] == "1.0000"
    assert synthetic["metrics"]["citation_precision"]["rate"] == "1.0000"
    assert synthetic["metrics"]["required_citation_recall"]["rate"] == "1.0000"
    assert synthetic["metrics"]["prohibited_action_refusal_recall"]["rate"] == "1.0000"
    assert report["provider_quality_gate"]["status"] == "not_verified"
    assert report["provider_quality_gate"]["reason"] == "fixed fixtures are not current provider outputs"
    assert synthetic["metrics"]["citation_precision"]["wilson_95"] is not None


def test_scorer_detects_hallucinated_evidence_and_forbidden_action_regression() -> None:
    catalog = load_dataset_catalog(DATASET_DIR)
    records = load_replay_records(FIXTURE_PATH)
    mutated = deepcopy(records)
    target = next(record for record in mutated if record.case_id.endswith("blocked-action"))
    target.output["evidence"].append("hallucinated-evidence")
    target.output["proposed_actions"] = target.output.pop("prohibited_actions")
    target.output["prohibited_actions"] = []

    report = evaluate_replay_records(catalog=catalog, records=mutated)

    synthetic = report["evidence_classes"]["synthetic_fixed_fixture"]
    assert synthetic["metrics"]["citation_precision"]["rate"] != "1.0000"
    assert synthetic["metrics"]["prohibited_action_refusal_recall"]["rate"] != "1.0000"
    assert report["release_decision"] == "no_go"


def test_markdown_report_keeps_evidence_classes_and_sample_sizes_visible() -> None:
    report = evaluate_replay_records(
        catalog=load_dataset_catalog(DATASET_DIR),
        records=load_replay_records(FIXTURE_PATH),
    )

    markdown = render_evaluation_markdown(report)

    assert "Synthetic Fixed Fixture" in markdown
    assert "n=" in markdown
    assert "Wilson 95%" in markdown
    assert "Real provider quality: **not_verified**" in markdown
