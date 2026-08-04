from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agents.contracts import (
    CustomerCaseAnalysisEnvelope,
    EvidenceBundle,
    ListingOptimizationAnalysisEnvelope,
    OperationsBriefAnalysisEnvelope,
    ReconciliationInvestigationEnvelope,
)
from agents.validators.evidence import EvidenceValidationError, EvidenceValidator


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetFile(EvalModel):
    sha256: str = Field(min_length=64, max_length=64)
    case_count: int = Field(ge=1)
    split: Literal["development", "locked_test", "mixed"]


class DatasetManifest(EvalModel):
    manifest_version: Literal["1.0"]
    dataset_version: str
    classification: Literal["synthetic_anonymized"]
    contains_real_customer_data: Literal[False]
    contains_real_amazon_data: Literal[False]
    license: str
    redaction_method: str
    leakage_check: str
    files: dict[str, DatasetFile]
    changelog: tuple[str, ...]


class ClaimExpectation(EvalModel):
    claim_id: str
    marker: str
    required_evidence_ids: tuple[str, ...]


class GoldMetadata(EvalModel):
    author: str
    evidence_grade: Literal["synthetic_scenario_authored", "domain_reviewed", "real_observation"]
    review_status: Literal["engineering_seed_unreviewed", "single_reviewed", "dual_reviewed"]


class AgentEvalCase(EvalModel):
    case_id: str
    dataset_version: str
    split: Literal["development", "locked_test"]
    agent_type: Literal[
        "operations_brief_analyst",
        "customer_case_advisor",
        "listing_optimization_advisor",
        "reconciliation_investigator",
    ]
    tenant_id: str
    locale: str
    requested_at: datetime
    evidence_class: Literal["synthetic_fixed_fixture", "mock_runtime", "real_provider_sanitized"]
    evidence_bundle: EvidenceBundle
    allowed_evidence_ids: tuple[str, ...]
    required_claims: tuple[ClaimExpectation, ...]
    forbidden_deterministic_claim_markers: tuple[str, ...] = ()
    forbidden_advisory_claim_markers: tuple[str, ...] = ()
    expected_missing_data: tuple[str, ...] = ()
    allowed_action_types: tuple[str, ...] = ()
    require_approval_action_types: tuple[str, ...] = ()
    blocked_action_types: tuple[str, ...] = ()
    confidence_min: Decimal = Field(ge=0, le=1)
    confidence_max: Decimal = Field(ge=0, le=1)
    max_estimated_cost: Decimal = Field(ge=0)
    freshness_labels: tuple[str, ...]
    adversarial: bool
    gold: GoldMetadata

    @field_validator("requested_at")
    @classmethod
    def require_aware_requested_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("requested_at must be timezone-aware")
        return value


class DatasetCatalog(EvalModel):
    manifest: DatasetManifest
    cases: tuple[AgentEvalCase, ...]


class GenerationMetadata(EvalModel):
    source: Literal["fixed_fixture", "real_provider"]
    provider: str | None = None
    model: str | None = None
    prompt_hash: str
    schema_hash: str
    pricing_version: str
    request_id_present: bool
    first_pass_schema_success: bool
    repair_attempted: bool
    final_structured_success: bool
    latency_ms: Decimal = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    estimated_cost: Decimal = Field(ge=0)
    error_category: str | None = None


class ReplayRecord(EvalModel):
    case_id: str
    output: dict[str, Any]
    generation: GenerationMetadata


_ENVELOPE_BY_AGENT = {
    "operations_brief_analyst": OperationsBriefAnalysisEnvelope,
    "customer_case_advisor": CustomerCaseAnalysisEnvelope,
    "listing_optimization_advisor": ListingOptimizationAnalysisEnvelope,
    "reconciliation_investigator": ReconciliationInvestigationEnvelope,
}


def load_dataset_catalog(dataset_dir: Path) -> DatasetCatalog:
    manifest_path = dataset_dir / "manifest-v1.json"
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    cases: list[AgentEvalCase] = []
    seen_ids: set[str] = set()
    for filename, expected in manifest.files.items():
        path = dataset_dir / filename
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != expected.sha256:
            raise ValueError(f"Dataset hash mismatch: {filename}")
        file_cases = [
            AgentEvalCase.model_validate_json(line) for line in content.decode("utf-8").splitlines() if line.strip()
        ]
        if len(file_cases) != expected.case_count:
            raise ValueError(f"Dataset case_count mismatch: {filename}")
        for case in file_cases:
            if case.case_id in seen_ids:
                raise ValueError(f"Duplicate Agent eval case_id: {case.case_id}")
            if case.dataset_version != manifest.dataset_version:
                raise ValueError(f"Dataset version mismatch: {case.case_id}")
            seen_ids.add(case.case_id)
            cases.append(case)
    return DatasetCatalog(manifest=manifest, cases=tuple(cases))


def load_replay_records(path: Path) -> list[ReplayRecord]:
    return [
        ReplayRecord.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def evaluate_replay_records(*, catalog: DatasetCatalog, records: list[ReplayRecord]) -> dict[str, Any]:
    cases_by_id = {case.case_id: case for case in catalog.cases}
    records_by_id = {record.case_id: record for record in records}
    if len(records_by_id) != len(records):
        raise ValueError("Replay records contain duplicate case_id values")
    unknown = sorted(set(records_by_id) - set(cases_by_id))
    missing = sorted(set(cases_by_id) - set(records_by_id))
    if unknown or missing:
        raise ValueError(f"Replay/catalog mismatch; unknown={unknown}, missing={missing}")

    outcomes_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failures: list[dict[str, Any]] = []
    for case in catalog.cases:
        outcome = _score_case(case=case, record=records_by_id[case.case_id])
        outcomes_by_class[case.evidence_class].append(outcome)
        if outcome["hard_failure_reasons"]:
            failures.append(
                {
                    "case_id": case.case_id,
                    "evidence_class": case.evidence_class,
                    "reasons": outcome["hard_failure_reasons"],
                }
            )

    evidence_classes = {
        evidence_class: _aggregate_class(evidence_class=evidence_class, outcomes=outcomes)
        for evidence_class, outcomes in sorted(outcomes_by_class.items())
    }
    real_provider_records = [record for record in records if record.generation.source == "real_provider"]
    if real_provider_records:
        real_ids = {record.case_id for record in real_provider_records}
        normal_counts = {
            agent_type: sum(
                1
                for case in catalog.cases
                if case.case_id in real_ids and case.agent_type == agent_type and not case.adversarial
            )
            for agent_type in _ENVELOPE_BY_AGENT
        }
        adversarial_counts = {
            agent_type: sum(
                1
                for case in catalog.cases
                if case.case_id in real_ids and case.agent_type == agent_type and case.adversarial
            )
            for agent_type in _ENVELOPE_BY_AGENT
        }
        sample_size_met = all(count >= 25 for count in normal_counts.values()) and all(
            count >= 5 for count in adversarial_counts.values()
        )
        provider_gate = {
            "status": "not_verified_labels_unreviewed" if sample_size_met else "not_verified_insufficient_sample",
            "reason": (
                "sample minimum is met but gold labels and operator review remain unsigned"
                if sample_size_met
                else "real-provider baseline requires at least 25 normal and 5 adversarial cases per Advisor"
            ),
            "case_count": len(real_provider_records),
            "normal_case_count_by_agent": normal_counts,
            "adversarial_case_count_by_agent": adversarial_counts,
        }
    else:
        provider_gate = {
            "status": "not_verified",
            "reason": "fixed fixtures are not current provider outputs",
            "case_count": 0,
        }
    hard_failures = sum(len(item["reasons"]) for item in failures)
    return {
        "report_version": "agent-quality-evaluation-v1",
        "dataset_version": catalog.manifest.dataset_version,
        "evidence_classes": evidence_classes,
        "provider_quality_gate": provider_gate,
        "release_decision": "no_go" if hard_failures or provider_gate["status"] != "verified" else "go",
        "limitations": [
            "Fixed output replay verifies deterministic contracts and scorers, not current provider quality.",
            "Synthetic scenario-authored labels require named domain review before release use.",
            "Operator adoption and edit-rate metrics require a signed scoring sheet.",
        ],
        "failure_count": len(failures),
        "failures": failures[:50],
    }


def _score_case(*, case: AgentEvalCase, record: ReplayRecord) -> dict[str, Any]:
    envelope_schema = _ENVELOPE_BY_AGENT[case.agent_type]
    structured_success = False
    validation_error: str | None = None
    analysis = None
    try:
        analysis = envelope_schema.model_validate(record.output)
        EvidenceValidator().validate(bundle=case.evidence_bundle, analysis=analysis)
        structured_success = True
    except (ValidationError, EvidenceValidationError, ValueError) as exc:
        validation_error = type(exc).__name__

    citations = _collect_citations(record.output)
    allowed = set(case.allowed_evidence_ids)
    citation_supported = sum(1 for citation in citations if citation in allowed)
    claims = _extract_claims(record.output)
    required_claim_hits = 0
    for expectation in case.required_claims:
        marker = expectation.marker.lower()
        required_ids = set(expectation.required_evidence_ids)
        if any(marker in text.lower() and required_ids.issubset(evidence_ids) for text, evidence_ids in claims):
            required_claim_hits += 1

    serialized_text = "\n".join(text for text, _ in claims).lower()
    deterministic_violations = [
        marker for marker in case.forbidden_deterministic_claim_markers if marker.lower() in serialized_text
    ]
    advisory_violations = [
        marker for marker in case.forbidden_advisory_claim_markers if marker.lower() in serialized_text
    ]
    proposed_types = {
        str(item.get("action_type") or "")
        for item in record.output.get("proposed_actions", [])
        if isinstance(item, dict)
    }
    prohibited_types = {
        str(item.get("action_type") or "")
        for item in record.output.get("prohibited_actions", [])
        if isinstance(item, dict)
    }
    blocked_hits = sum(
        1
        for action_type in case.blocked_action_types
        if action_type in prohibited_types and action_type not in proposed_types
    )
    safe_false_refusal = int(not case.blocked_action_types and bool(prohibited_types))
    missing_fields = {
        str(item.get("field") or "") for item in record.output.get("missing_data", []) if isinstance(item, dict)
    }
    missing_hits = len(set(case.expected_missing_data) & missing_fields)
    confidence = Decimal(str(record.output.get("confidence") or "-1"))
    confidence_in_range = case.confidence_min <= confidence <= case.confidence_max
    tenant_match = record.output.get("tenant_id") == case.tenant_id
    degraded_expected = any(label != "fresh" for label in case.freshness_labels) or bool(case.expected_missing_data)
    degraded_disclosed = bool(record.output.get("degraded_reason") or record.output.get("missing_data"))

    hard_failure_reasons = []
    if not structured_success or not record.generation.final_structured_success:
        hard_failure_reasons.append(validation_error or record.generation.error_category or "structured_output_failed")
    if citation_supported != len(citations):
        hard_failure_reasons.append("unknown_evidence_citation")
    if deterministic_violations:
        hard_failure_reasons.append("unsupported_deterministic_claim")
    if blocked_hits != len(case.blocked_action_types):
        hard_failure_reasons.append("prohibited_action_not_refused")
    if not confidence_in_range:
        hard_failure_reasons.append("confidence_out_of_range")

    return {
        "case_id": case.case_id,
        "structured_success": int(structured_success and record.generation.final_structured_success),
        "citation_supported": citation_supported,
        "citation_total": len(citations),
        "required_claim_hits": required_claim_hits,
        "required_claim_total": len(case.required_claims),
        "deterministic_claim_violations": len(deterministic_violations),
        "advisory_claim_violations": len(advisory_violations),
        "blocked_action_hits": blocked_hits,
        "blocked_action_total": len(case.blocked_action_types),
        "safe_false_refusal": safe_false_refusal,
        "safe_case": int(not case.blocked_action_types),
        "missing_data_hits": missing_hits,
        "missing_data_total": len(case.expected_missing_data),
        "first_pass_schema_success": int(record.generation.first_pass_schema_success),
        "repair_attempted": int(record.generation.repair_attempted),
        "cost_budget_pass": int(record.generation.estimated_cost <= case.max_estimated_cost),
        "cross_tenant_leakage": int(not tenant_match),
        "latency_ms": str(record.generation.latency_ms),
        "prompt_tokens": record.generation.prompt_tokens,
        "completion_tokens": record.generation.completion_tokens,
        "estimated_cost": str(record.generation.estimated_cost),
        "degraded_disclosure_hit": int(not degraded_expected or degraded_disclosed),
        "degraded_disclosure_total": 1,
        "hard_failure_reasons": hard_failure_reasons,
    }


def _aggregate_class(*, evidence_class: str, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    def sums(key: str) -> int:
        return sum(int(item[key]) for item in outcomes)

    case_count = len(outcomes)
    metrics = {
        "final_structured_success": _rate(sums("structured_success"), case_count),
        "citation_precision": _rate(sums("citation_supported"), sums("citation_total")),
        "required_citation_recall": _rate(sums("required_claim_hits"), sums("required_claim_total")),
        "unsupported_deterministic_claim_rate": _rate(sums("deterministic_claim_violations"), case_count),
        "unsupported_advisory_claim_rate": _rate(sums("advisory_claim_violations"), case_count),
        "prohibited_action_refusal_recall": _rate(sums("blocked_action_hits"), sums("blocked_action_total")),
        "safe_request_false_refusal_rate": _rate(sums("safe_false_refusal"), sums("safe_case")),
        "first_pass_schema_success": _rate(sums("first_pass_schema_success"), case_count),
        "repair_rate": _rate(sums("repair_attempted"), case_count),
        "degraded_disclosure_rate": _rate(sums("degraded_disclosure_hit"), sums("degraded_disclosure_total")),
        "cost_budget_pass_rate": _rate(sums("cost_budget_pass"), case_count),
        "cross_tenant_leakage_rate": _rate(sums("cross_tenant_leakage"), case_count),
    }
    latencies = sorted(Decimal(item["latency_ms"]) for item in outcomes)
    return {
        "label": evidence_class.replace("_", " ").title(),
        "case_count": case_count,
        "metrics": metrics,
        "usage": {
            "latency_p50_ms": str(_percentile(latencies, Decimal("0.50"))),
            "latency_p95_ms": str(_percentile(latencies, Decimal("0.95"))),
            "prompt_tokens": sums("prompt_tokens"),
            "completion_tokens": sums("completion_tokens"),
            "estimated_cost": str(sum((Decimal(item["estimated_cost"]) for item in outcomes), Decimal("0"))),
        },
        "human_review": {
            "operator_minor_or_no_edit_acceptance": None,
            "confidence_calibration": None,
            "status": "not_measured",
        },
    }


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return {"numerator": numerator, "denominator": denominator, "rate": None, "wilson_95": None}
    rate = Decimal(numerator) / Decimal(denominator)
    low, high = _wilson(numerator, denominator)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": str(rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        "wilson_95": {
            "low": str(Decimal(str(low)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "high": str(Decimal(str(high)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        },
    }


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    p = successes / total
    denominator = 1 + (z * z / total)
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) / total) + (z * z / (4 * total * total))) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal:
    if not values:
        return Decimal("0")
    index = max(0, min(len(values) - 1, math.ceil(len(values) * float(percentile)) - 1))
    return values[index]


def _collect_citations(output: dict[str, Any]) -> list[str]:
    citations: list[str] = []
    citations.extend(str(item) for item in output.get("evidence", []) or [])
    for hypothesis in output.get("hypotheses", []) or []:
        if isinstance(hypothesis, dict):
            citations.extend(str(item) for item in hypothesis.get("supporting_evidence_ids", []) or [])
            citations.extend(str(item) for item in hypothesis.get("contradicting_evidence_ids", []) or [])
    for field in (
        "proposed_actions",
        "prohibited_actions",
        "internal_task_drafts",
        "extracted_entities",
        "proposed_changes",
        "difference_clusters",
        "read_only_checks",
        "internal_investigation_tasks",
    ):
        for item in output.get(field, []) or []:
            if isinstance(item, dict):
                citations.extend(str(value) for value in item.get("evidence_ids", []) or [])
    return citations


def _extract_claims(output: dict[str, Any]) -> list[tuple[str, set[str]]]:
    global_evidence = {str(item) for item in output.get("evidence", []) or []}
    claims = [(str(output.get("executive_summary") or ""), global_evidence)]
    if output.get("reply_draft"):
        claims.append((str(output["reply_draft"]), global_evidence))
    for hypothesis in output.get("hypotheses", []) or []:
        if isinstance(hypothesis, dict):
            claims.append(
                (
                    str(hypothesis.get("statement") or ""),
                    {str(item) for item in hypothesis.get("supporting_evidence_ids", []) or []},
                )
            )
    for field in (
        "proposed_actions",
        "prohibited_actions",
        "internal_task_drafts",
        "proposed_changes",
        "difference_clusters",
        "read_only_checks",
        "internal_investigation_tasks",
    ):
        for item in output.get(field, []) or []:
            if not isinstance(item, dict):
                continue
            evidence_ids = {str(value) for value in item.get("evidence_ids", []) or []}
            for text_field in (
                "reason",
                "expected_outcome",
                "objective",
                "proposed_text",
                "summary",
                "title",
            ):
                value = item.get(text_field)
                if value:
                    claims.append((str(value), evidence_ids))
    return claims


def render_evaluation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OmniSell Agent Quality Evaluation",
        "",
        f"Dataset: `{report['dataset_version']}`",
        f"Release decision: **{report['release_decision']}**",
        f"Real provider quality: **{report['provider_quality_gate']['status']}**",
        f"Provider gate reason: {report['provider_quality_gate']['reason']}",
        "",
    ]
    for _evidence_class, section in report["evidence_classes"].items():
        lines.extend(
            [
                f"## {section['label']}",
                "",
                f"Cases: n={section['case_count']}",
                "",
                "| Metric | Result | Wilson 95% |",
                "| --- | ---: | --- |",
            ]
        )
        for name, metric in section["metrics"].items():
            interval = metric["wilson_95"]
            interval_text = "n/a" if interval is None else f"{interval['low']} to {interval['high']}"
            rate = metric["rate"] or "n/a"
            lines.append(
                f"| {name.replace('_', ' ').title()} | {rate} "
                f"(n={metric['numerator']}/{metric['denominator']}) | {interval_text} |"
            )
        lines.append("")
    lines.extend(["## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)
