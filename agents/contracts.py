from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EvidenceSource = Literal[
    "amazon_api",
    "advertising_api",
    "csv_import",
    "manual",
    "system_estimate",
    "mock",
    "internal_record",
]
FreshnessStatus = Literal["fresh", "stale", "unknown"]
HypothesisStatus = Literal["hypothesis", "supported", "insufficient_evidence"]
RiskLevel = Literal["low", "medium", "high", "critical"]


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must be timezone-aware")
    return value


class AgentContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelUsage(AgentContractModel):
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    schema_version: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    latency_ms: Decimal = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    repair_attempted: bool = False
    request_id: str | None = None
    fallback_used: bool = False
    fallback_from_provider: str | None = None
    fallback_reason: str | None = None
    attempted_providers: tuple[str, ...] = ()


class EvidencePreparationManifest(AgentContractModel):
    selection_strategy: str = "priority_then_evidence_id_v1"
    original_evidence_count: int = Field(ge=0)
    selected_evidence_count: int = Field(ge=0)
    omitted_evidence_count: int = Field(ge=0)
    original_serialized_chars: int = Field(ge=0)
    selected_serialized_chars: int = Field(ge=0)
    request_bytes: int = Field(ge=0)
    estimated_prompt_tokens: int = Field(ge=0)
    max_prompt_tokens: int = Field(gt=0)
    max_total_evidence_chars: int = Field(gt=0)
    selected_evidence_ids: tuple[str, ...] = ()
    omitted_evidence_ids: tuple[str, ...] = ()
    repair_call_reserved: bool = True
    worst_case_estimated_tokens: int = Field(default=0, ge=0)
    model_token_budget: int | None = Field(default=None, gt=0)
    worst_case_estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    degraded: bool = False

    @model_validator(mode="after")
    def validate_counts(self) -> "EvidencePreparationManifest":
        if self.selected_evidence_count != len(self.selected_evidence_ids):
            raise ValueError("selected evidence count does not match selected_evidence_ids")
        if self.omitted_evidence_count != len(self.omitted_evidence_ids):
            raise ValueError("omitted evidence count does not match omitted_evidence_ids")
        if self.original_evidence_count != self.selected_evidence_count + self.omitted_evidence_count:
            raise ValueError("original evidence count must equal selected plus omitted evidence")
        return self


class EvidenceReference(AgentContractModel):
    evidence_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    source: EvidenceSource
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    observed_at: datetime | None = None
    collected_at: datetime
    freshness_status: FreshnessStatus
    confidence: Decimal = Field(ge=0, le=1)
    verified_amazon_data: bool = False
    content_digest: str = Field(min_length=64, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_observed_at = field_validator("observed_at")(_require_aware)
    _validate_collected_at = field_validator("collected_at")(_require_aware)

    @model_validator(mode="after")
    def validate_source_truthfulness(self) -> "EvidenceReference":
        if self.verified_amazon_data and self.source not in {"amazon_api", "advertising_api"}:
            raise ValueError(f"{self.source} evidence cannot be marked as verified Amazon data")
        return self


class EvidenceBundle(AgentContractModel):
    tenant_id: str = Field(min_length=1)
    collected_at: datetime
    evidence: tuple[EvidenceReference, ...]

    _validate_collected_at = field_validator("collected_at")(_require_aware)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "EvidenceBundle":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")
        return self

    def by_id(self) -> dict[str, EvidenceReference]:
        return {item.evidence_id: item for item in self.evidence}


class MissingDataItem(AgentContractModel):
    field: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required_for: str = Field(min_length=1)
    blocking: bool
    suggested_source: str | None = None


class AgentHypothesis(AgentContractModel):
    hypothesis_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    supporting_evidence_ids: tuple[str, ...] = ()
    contradicting_evidence_ids: tuple[str, ...] = ()
    confidence: Decimal = Field(ge=0, le=1)
    status: HypothesisStatus = "hypothesis"

    @model_validator(mode="after")
    def require_support_or_insufficient_status(self) -> "AgentHypothesis":
        if not self.supporting_evidence_ids and self.status != "insufficient_evidence":
            raise ValueError("hypothesis must cite supporting evidence or be insufficient_evidence")
        return self


class AgentProposedAction(AgentContractModel):
    action_type: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    evidence_ids: tuple[str, ...]
    expected_outcome: str = Field(min_length=1)
    risk_level: RiskLevel
    requires_approval: bool
    automation_eligible: bool = False
    prohibited: bool = False
    prohibited_reason: str | None = None

    @model_validator(mode="after")
    def enforce_advisory_boundary(self) -> "AgentProposedAction":
        if self.automation_eligible:
            raise ValueError("automation_eligible is determined by deterministic policy code and must be false")
        if self.prohibited and not self.prohibited_reason:
            raise ValueError("prohibited actions require prohibited_reason")
        return self


class AgentAnalysisDraft(AgentContractModel):
    executive_summary: str
    evidence: tuple[str, ...]
    hypotheses: tuple[AgentHypothesis, ...]
    missing_data: tuple[MissingDataItem, ...]
    proposed_actions: tuple[AgentProposedAction, ...]
    prohibited_actions: tuple[AgentProposedAction, ...]
    confidence: Decimal = Field(ge=0, le=1)
    degraded_reason: str | None = None
    warnings: tuple[str, ...] = ()


class OperationsBriefAnalysisDraft(AgentAnalysisDraft):
    grouped_issue_ids: tuple[tuple[str, ...], ...] = ()
    daily_checklist: tuple[str, ...] = ()
    internal_task_drafts: tuple[dict[str, Any], ...] = ()


class CustomerCaseAnalysisDraft(AgentAnalysisDraft):
    intents: tuple[str, ...]
    extracted_entities: dict[str, str] = Field(default_factory=dict)
    sentiment_recommendation: str
    urgency_recommendation: str
    reply_draft: str | None = None
    escalation_recommended: bool
    rag_evidence_needed: tuple[str, ...] = ()
    voc_tags: tuple[str, ...] = ()


class ListingOptimizationAnalysisDraft(AgentAnalysisDraft):
    proposed_changes: tuple[dict[str, Any], ...] = ()
    experiment_hypothesis: str | None = None
    expected_metrics: tuple[str, ...] = ()
    unsupported_claims: tuple[str, ...] = ()


class ReconciliationInvestigationDraft(AgentAnalysisDraft):
    difference_clusters: tuple[dict[str, Any], ...] = ()
    read_only_checks: tuple[dict[str, Any], ...] = ()
    internal_investigation_tasks: tuple[dict[str, Any], ...] = ()


class AgentAnalysisEnvelope(AgentAnalysisDraft):
    schema_version: str = Field(min_length=1)
    agent_type: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    generated_at: datetime
    model_usage: ModelUsage | None = None
    evidence_preparation: EvidencePreparationManifest | None = None

    _validate_generated_at = field_validator("generated_at")(_require_aware)


class OperationsBriefAnalysisEnvelope(AgentAnalysisEnvelope):
    grouped_issue_ids: tuple[tuple[str, ...], ...] = ()
    daily_checklist: tuple[str, ...] = ()
    internal_task_drafts: tuple[dict[str, Any], ...] = ()


class CustomerCaseAnalysisEnvelope(AgentAnalysisEnvelope):
    intents: tuple[str, ...]
    extracted_entities: dict[str, str] = Field(default_factory=dict)
    sentiment_recommendation: str
    urgency_recommendation: str
    reply_draft: str | None = None
    escalation_recommended: bool
    rag_evidence_needed: tuple[str, ...] = ()
    voc_tags: tuple[str, ...] = ()


class ListingOptimizationAnalysisEnvelope(AgentAnalysisEnvelope):
    proposed_changes: tuple[dict[str, Any], ...] = ()
    experiment_hypothesis: str | None = None
    expected_metrics: tuple[str, ...] = ()
    unsupported_claims: tuple[str, ...] = ()


class ReconciliationInvestigationEnvelope(AgentAnalysisEnvelope):
    difference_clusters: tuple[dict[str, Any], ...] = ()
    read_only_checks: tuple[dict[str, Any], ...] = ()
    internal_investigation_tasks: tuple[dict[str, Any], ...] = ()


ANALYSIS_DRAFT_SCHEMAS: dict[type[AgentAnalysisEnvelope], type[AgentAnalysisDraft]] = {
    AgentAnalysisEnvelope: AgentAnalysisDraft,
    OperationsBriefAnalysisEnvelope: OperationsBriefAnalysisDraft,
    CustomerCaseAnalysisEnvelope: CustomerCaseAnalysisDraft,
    ListingOptimizationAnalysisEnvelope: ListingOptimizationAnalysisDraft,
    ReconciliationInvestigationEnvelope: ReconciliationInvestigationDraft,
}


def analysis_draft_schema(envelope_schema: type[AgentAnalysisEnvelope]) -> type[AgentAnalysisDraft]:
    try:
        return ANALYSIS_DRAFT_SCHEMAS[envelope_schema]
    except KeyError as exc:
        raise ValueError(f"No model-facing draft schema registered for {envelope_schema.__name__}") from exc
