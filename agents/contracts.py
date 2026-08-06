from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from model_gateway.contracts import ModelBudgetEstimate

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
Identifier: TypeAlias = Annotated[str, Field(min_length=1, max_length=100)]
ShortText: TypeAlias = Annotated[str, Field(min_length=1, max_length=200)]
LongText: TypeAlias = Annotated[str, Field(min_length=1, max_length=2000)]
OptionalLongText: TypeAlias = Annotated[str | None, Field(max_length=2000)]
EvidenceIds: TypeAlias = Annotated[tuple[Identifier, ...], Field(max_length=20)]
ResourceIds: TypeAlias = Annotated[tuple[Identifier, ...], Field(max_length=20)]
EvidenceMetadata: TypeAlias = Annotated[dict[Identifier, JsonValue], Field(max_length=64)]
MAX_EVIDENCE_METADATA_DEPTH = 6
MAX_EVIDENCE_METADATA_ITEMS = 128
MAX_EVIDENCE_METADATA_NODES = 256
MAX_EVIDENCE_METADATA_STRING_CHARS = 2000
ListingField = Literal[
    "title",
    "bullet_point_1",
    "bullet_point_2",
    "bullet_point_3",
    "bullet_point_4",
    "bullet_point_5",
    "description",
    "search_terms",
]
ReadOnlyCheckType = Literal[
    "fetch_amazon_record",
    "fetch_internal_record",
    "compare_event_timeline",
    "verify_mapping",
    "verify_sync_cursor",
    "inspect_audit_trail",
]
ActionType = Literal[
    "create_internal_task_draft",
    "reference_evidence",
    "request_human_review",
    "refund",
    "issue_refund",
    "approve-refund",
    "payment_reversal",
    "reverse_payment",
    "autonomous_cancellation",
    "cancel_order",
    "approval_bypass",
    "cross_tenant_action",
    "arbitrary_amazon_write",
]
CustomerCaseActionType = Literal[
    "draft_customer_reply",
    "draft_compliant_reply",
    "reference_evidence",
    "request_human_review",
    "refund",
    "issue_refund",
    "approve-refund",
    "payment_reversal",
    "reverse_payment",
    "autonomous_cancellation",
    "cancel_order",
    "approval_bypass",
    "cross_tenant_action",
    "arbitrary_amazon_write",
]
ListingActionType = Literal[
    "draft_listing_change",
    "create_listing_experiment_draft",
    "reference_evidence",
    "request_human_review",
    "refund",
    "issue_refund",
    "approve-refund",
    "payment_reversal",
    "reverse_payment",
    "autonomous_cancellation",
    "cancel_order",
    "approval_bypass",
    "cross_tenant_action",
    "arbitrary_amazon_write",
]
ReconciliationActionType = Literal[
    "create_internal_investigation_task",
    "run_read_only_check",
    "compare_records_read_only",
    "reference_evidence",
    "request_human_review",
    "refund",
    "issue_refund",
    "approve-refund",
    "payment_reversal",
    "reverse_payment",
    "autonomous_cancellation",
    "cancel_order",
    "approval_bypass",
    "cross_tenant_action",
    "arbitrary_amazon_write",
]


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must be timezone-aware")
    return value


def _validate_json_metadata(value: object, *, path: str = "metadata", depth: int = 0) -> int:
    if depth > MAX_EVIDENCE_METADATA_DEPTH:
        raise ValueError(f"{path} exceeds maximum metadata depth {MAX_EVIDENCE_METADATA_DEPTH}")
    if value is None or isinstance(value, (bool, int)):
        return 1
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"{path} must be a finite JSON number")
        return 1
    if isinstance(value, str):
        if len(value) > MAX_EVIDENCE_METADATA_STRING_CHARS:
            raise ValueError(f"{path} string exceeds {MAX_EVIDENCE_METADATA_STRING_CHARS} characters")
        return 1
    if isinstance(value, list):
        if len(value) > MAX_EVIDENCE_METADATA_ITEMS:
            raise ValueError(f"{path} exceeds {MAX_EVIDENCE_METADATA_ITEMS} items")
        nodes = 1
        for index, item in enumerate(value):
            nodes += _validate_json_metadata(item, path=f"{path}[{index}]", depth=depth + 1)
            if nodes > MAX_EVIDENCE_METADATA_NODES:
                raise ValueError(f"metadata exceeds {MAX_EVIDENCE_METADATA_NODES} total nodes")
        return nodes
    if isinstance(value, dict):
        if len(value) > MAX_EVIDENCE_METADATA_ITEMS:
            raise ValueError(f"{path} exceeds {MAX_EVIDENCE_METADATA_ITEMS} items")
        nodes = 1
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100:
                raise ValueError(f"{path} keys must be non-empty strings of at most 100 characters")
            nodes += _validate_json_metadata(item, path=f"{path}.{key}", depth=depth + 1)
            if nodes > MAX_EVIDENCE_METADATA_NODES:
                raise ValueError(f"metadata exceeds {MAX_EVIDENCE_METADATA_NODES} total nodes")
        return nodes
    raise ValueError(f"{path} must contain only JSON-compatible values")


class AgentContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def reject_serialized_json_in_text_fields(self) -> AgentContractModel:
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if not candidate or candidate[0] not in "[{" or candidate[-1] not in "]}":
                continue
            try:
                decoded = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, (dict, list)):
                raise ValueError(f"{field_name} must not contain serialized JSON")
        return self


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
    first_pass_schema_success: bool = True
    schema_failure_categories: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()
    initial_latency_ms: Decimal = Field(default=Decimal("0"), ge=0)
    repair_latency_ms: Decimal = Field(default=Decimal("0"), ge=0)
    provider_call_count: int = Field(default=1, ge=1, le=4)
    request_id: str | None = None
    fallback_used: bool = False
    fallback_from_provider: str | None = None
    fallback_reason: str | None = None
    attempted_providers: tuple[str, ...] = ()
    reserved_tokens: int = Field(default=0, ge=0)
    reserved_cost: Decimal = Field(default=Decimal("0"), ge=0)
    unattributed_providers: tuple[str, ...] = ()
    unattributed_cost_upper_bound: Decimal = Field(default=Decimal("0"), ge=0)
    cost_accounting_status: Literal["reported", "partially_unattributed"] = "reported"

    @model_validator(mode="after")
    def validate_schema_attempt_accounting(self) -> ModelUsage:
        if self.repair_attempted and self.first_pass_schema_success:
            raise ValueError("repair_attempted requires first_pass_schema_success=false")
        return self


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
    model_budget_estimate: ModelBudgetEstimate
    degraded: bool = False

    @model_validator(mode="after")
    def validate_counts(self) -> EvidencePreparationManifest:
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
    metadata: EvidenceMetadata = Field(default_factory=dict)

    _validate_observed_at = field_validator("observed_at")(_require_aware)
    _validate_collected_at = field_validator("collected_at")(_require_aware)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata_budget(cls, value: object) -> object:
        _validate_json_metadata(value)
        return value

    @model_validator(mode="after")
    def validate_source_truthfulness(self) -> EvidenceReference:
        if self.verified_amazon_data and self.source not in {"amazon_api", "advertising_api"}:
            raise ValueError(f"{self.source} evidence cannot be marked as verified Amazon data")
        return self


class EvidenceBundle(AgentContractModel):
    tenant_id: str = Field(min_length=1)
    collected_at: datetime
    evidence: tuple[EvidenceReference, ...]

    _validate_collected_at = field_validator("collected_at")(_require_aware)

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> EvidenceBundle:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")
        return self

    def by_id(self) -> dict[str, EvidenceReference]:
        return {item.evidence_id: item for item in self.evidence}


class MissingDataItem(AgentContractModel):
    field: Identifier
    reason: LongText
    required_for: ShortText
    blocking: bool
    suggested_source: Annotated[str | None, Field(max_length=200)] = None


class AgentHypothesis(AgentContractModel):
    hypothesis_id: Identifier
    statement: LongText
    supporting_evidence_ids: EvidenceIds = ()
    contradicting_evidence_ids: EvidenceIds = ()
    confidence: Decimal = Field(ge=0, le=1)
    status: HypothesisStatus = "hypothesis"

    @model_validator(mode="after")
    def require_support_or_insufficient_status(self) -> AgentHypothesis:
        if not self.supporting_evidence_ids and self.status != "insufficient_evidence":
            raise ValueError("hypothesis must cite supporting evidence or be insufficient_evidence")
        return self


class ActionArgumentsBase(AgentContractModel):
    argument_type: str


class NoActionArguments(ActionArgumentsBase):
    argument_type: Literal["none"] = "none"


class InternalTaskActionArguments(ActionArgumentsBase):
    argument_type: Literal["internal_task"]
    related_resource_ids: ResourceIds = ()
    related_evidence_ids: EvidenceIds = ()
    task_title: Annotated[str | None, Field(max_length=200)] = None


class ReadOnlyCheckActionArguments(ActionArgumentsBase):
    argument_type: Literal["read_only_check"]
    check_type: ReadOnlyCheckType
    related_resource_ids: ResourceIds = ()
    related_evidence_ids: EvidenceIds = ()


class CustomerReplyDraftActionArguments(ActionArgumentsBase):
    argument_type: Literal["customer_reply_draft"]
    reply_mode: Literal["draft_only"] = "draft_only"
    rag_evidence_ids: EvidenceIds = ()


class ListingChangeDraftActionArguments(ActionArgumentsBase):
    argument_type: Literal["listing_change_draft"]
    fields: Annotated[tuple[ListingField, ...], Field(min_length=1, max_length=8)]
    experiment_required: Literal[True] = True


class EvidenceReferenceActionArguments(ActionArgumentsBase):
    argument_type: Literal["evidence_reference"]
    related_resource_ids: ResourceIds = ()
    related_evidence_ids: EvidenceIds = ()


OperationsActionArguments: TypeAlias = Annotated[
    NoActionArguments | InternalTaskActionArguments | EvidenceReferenceActionArguments,
    Field(discriminator="argument_type"),
]
CustomerCaseActionArguments: TypeAlias = Annotated[
    NoActionArguments | CustomerReplyDraftActionArguments | EvidenceReferenceActionArguments,
    Field(discriminator="argument_type"),
]
ListingActionArguments: TypeAlias = Annotated[
    NoActionArguments | ListingChangeDraftActionArguments | EvidenceReferenceActionArguments,
    Field(discriminator="argument_type"),
]
ReconciliationActionArguments: TypeAlias = Annotated[
    NoActionArguments | InternalTaskActionArguments | ReadOnlyCheckActionArguments | EvidenceReferenceActionArguments,
    Field(discriminator="argument_type"),
]


class AgentProposedActionBase(AgentContractModel):
    action_type: str
    resource_type: Identifier
    resource_id: Identifier
    arguments: ActionArgumentsBase
    reason: LongText
    evidence_ids: EvidenceIds
    expected_outcome: LongText
    risk_level: RiskLevel
    requires_approval: bool
    automation_eligible: bool = False
    prohibited: bool = False
    prohibited_reason: OptionalLongText = None
    ARGUMENT_TYPE_BY_ACTION: ClassVar[dict[str, str]] = {}

    @model_validator(mode="after")
    def enforce_advisory_boundary(self) -> AgentProposedActionBase:
        if self.automation_eligible:
            raise ValueError("automation_eligible is determined by deterministic policy code and must be false")
        if self.prohibited and not self.prohibited_reason:
            raise ValueError("prohibited actions require prohibited_reason")
        expected_argument_types = self.ARGUMENT_TYPE_BY_ACTION
        expected = expected_argument_types[self.action_type]
        if not hasattr(self.arguments, "argument_type") or self.arguments.argument_type != expected:
            raise ValueError(f"{self.action_type} requires arguments.argument_type={expected}")
        return self


class AgentProposedAction(AgentProposedActionBase):
    action_type: ActionType
    arguments: OperationsActionArguments = Field(default_factory=NoActionArguments)
    ARGUMENT_TYPE_BY_ACTION: ClassVar[dict[str, str]] = {
        "create_internal_task_draft": "internal_task",
        "reference_evidence": "evidence_reference",
        "request_human_review": "evidence_reference",
        "refund": "none",
        "issue_refund": "none",
        "approve-refund": "none",
        "payment_reversal": "none",
        "reverse_payment": "none",
        "autonomous_cancellation": "none",
        "cancel_order": "none",
        "approval_bypass": "none",
        "cross_tenant_action": "none",
        "arbitrary_amazon_write": "none",
    }


class CustomerCaseProposedAction(AgentProposedActionBase):
    action_type: CustomerCaseActionType
    arguments: CustomerCaseActionArguments = Field(default_factory=NoActionArguments)
    ARGUMENT_TYPE_BY_ACTION: ClassVar[dict[str, str]] = {
        "draft_customer_reply": "customer_reply_draft",
        "draft_compliant_reply": "customer_reply_draft",
        "reference_evidence": "evidence_reference",
        "request_human_review": "evidence_reference",
        "refund": "none",
        "issue_refund": "none",
        "approve-refund": "none",
        "payment_reversal": "none",
        "reverse_payment": "none",
        "autonomous_cancellation": "none",
        "cancel_order": "none",
        "approval_bypass": "none",
        "cross_tenant_action": "none",
        "arbitrary_amazon_write": "none",
    }


class ListingProposedAction(AgentProposedActionBase):
    action_type: ListingActionType
    arguments: ListingActionArguments = Field(default_factory=NoActionArguments)
    ARGUMENT_TYPE_BY_ACTION: ClassVar[dict[str, str]] = {
        "draft_listing_change": "listing_change_draft",
        "create_listing_experiment_draft": "listing_change_draft",
        "reference_evidence": "evidence_reference",
        "request_human_review": "evidence_reference",
        "refund": "none",
        "issue_refund": "none",
        "approve-refund": "none",
        "payment_reversal": "none",
        "reverse_payment": "none",
        "autonomous_cancellation": "none",
        "cancel_order": "none",
        "approval_bypass": "none",
        "cross_tenant_action": "none",
        "arbitrary_amazon_write": "none",
    }


class ReconciliationProposedAction(AgentProposedActionBase):
    action_type: ReconciliationActionType
    arguments: ReconciliationActionArguments = Field(default_factory=NoActionArguments)
    ARGUMENT_TYPE_BY_ACTION: ClassVar[dict[str, str]] = {
        "create_internal_investigation_task": "internal_task",
        "run_read_only_check": "read_only_check",
        "compare_records_read_only": "read_only_check",
        "reference_evidence": "evidence_reference",
        "request_human_review": "evidence_reference",
        "refund": "none",
        "issue_refund": "none",
        "approve-refund": "none",
        "payment_reversal": "none",
        "reverse_payment": "none",
        "autonomous_cancellation": "none",
        "cancel_order": "none",
        "approval_bypass": "none",
        "cross_tenant_action": "none",
        "arbitrary_amazon_write": "none",
    }


class InternalTaskDraft(AgentContractModel):
    title: ShortText
    objective: LongText
    resource_type: Identifier
    resource_id: Identifier
    evidence_ids: EvidenceIds
    priority: Literal["low", "medium", "high"]
    expected_outcome: LongText


class ExtractedEntity(AgentContractModel):
    entity_type: Literal[
        "order_id",
        "asin",
        "seller_sku",
        "shipment_id",
        "marketplace_id",
        "tracking_id",
        "customer_reference",
        "other",
    ]
    value: ShortText
    evidence_ids: EvidenceIds


class ListingFieldChange(AgentContractModel):
    field: ListingField
    operation: Literal["replace", "append", "remove"]
    proposed_text: Annotated[str | None, Field(max_length=2000)] = None
    reason: LongText
    evidence_ids: EvidenceIds

    @model_validator(mode="after")
    def require_text_unless_removing(self) -> ListingFieldChange:
        if self.operation != "remove" and not self.proposed_text:
            raise ValueError("proposed_text is required unless operation is remove")
        if self.operation == "remove" and self.proposed_text is not None:
            raise ValueError("remove operations must not include proposed_text")
        return self


class DifferenceCluster(AgentContractModel):
    cluster_id: Identifier
    difference_type: Literal[
        "inventory",
        "order",
        "listing",
        "financial",
        "mapping",
        "synchronization",
        "event",
        "audit",
        "other",
    ]
    resource_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=20)]
    evidence_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=20)]
    summary: LongText
    hypothesis_status: HypothesisStatus


class ReadOnlyCheck(AgentContractModel):
    check_type: ReadOnlyCheckType
    resource_type: Identifier
    resource_id: Identifier
    evidence_ids: EvidenceIds
    reason: LongText


class InvestigationTaskDraft(AgentContractModel):
    title: ShortText
    objective: LongText
    resource_type: Identifier
    resource_id: Identifier
    evidence_ids: EvidenceIds
    priority: Literal["low", "medium", "high"]
    expected_outcome: LongText


class AgentAnalysisDraft(AgentContractModel):
    executive_summary: LongText
    evidence: EvidenceIds
    hypotheses: Annotated[tuple[AgentHypothesis, ...], Field(max_length=10)]
    missing_data: Annotated[tuple[MissingDataItem, ...], Field(max_length=20)]
    proposed_actions: Annotated[tuple[AgentProposedAction, ...], Field(max_length=10)]
    prohibited_actions: Annotated[tuple[AgentProposedAction, ...], Field(max_length=10)]
    confidence: Decimal = Field(ge=0, le=1)
    degraded_reason: OptionalLongText = None
    warnings: Annotated[tuple[ShortText, ...], Field(max_length=20)] = ()


class OperationsBriefAnalysisDraft(AgentAnalysisDraft):
    grouped_issue_ids: Annotated[tuple[ResourceIds, ...], Field(max_length=20)] = ()
    daily_checklist: Annotated[tuple[ShortText, ...], Field(max_length=8)] = ()
    internal_task_drafts: Annotated[tuple[InternalTaskDraft, ...], Field(max_length=20)] = ()


class CustomerCaseAnalysisDraft(AgentAnalysisDraft):
    proposed_actions: Annotated[tuple[CustomerCaseProposedAction, ...], Field(max_length=10)]
    prohibited_actions: Annotated[tuple[CustomerCaseProposedAction, ...], Field(max_length=10)]
    intents: Annotated[tuple[Identifier, ...], Field(max_length=10)]
    extracted_entities: Annotated[tuple[ExtractedEntity, ...], Field(max_length=20)] = ()
    sentiment_recommendation: ShortText
    urgency_recommendation: ShortText
    reply_draft: Annotated[str | None, Field(max_length=4000)] = None
    escalation_recommended: bool
    rag_evidence_needed: Annotated[tuple[ShortText, ...], Field(max_length=20)] = ()
    voc_tags: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()


class ListingOptimizationAnalysisDraft(AgentAnalysisDraft):
    proposed_actions: Annotated[tuple[ListingProposedAction, ...], Field(max_length=10)]
    prohibited_actions: Annotated[tuple[ListingProposedAction, ...], Field(max_length=10)]
    proposed_changes: Annotated[tuple[ListingFieldChange, ...], Field(max_length=20)] = ()
    experiment_hypothesis: OptionalLongText = None
    expected_metrics: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()
    unsupported_claims: Annotated[tuple[LongText, ...], Field(max_length=20)] = ()


class ReconciliationInvestigationDraft(AgentAnalysisDraft):
    proposed_actions: Annotated[tuple[ReconciliationProposedAction, ...], Field(max_length=10)]
    prohibited_actions: Annotated[tuple[ReconciliationProposedAction, ...], Field(max_length=10)]
    difference_clusters: Annotated[tuple[DifferenceCluster, ...], Field(max_length=20)] = ()
    read_only_checks: Annotated[tuple[ReadOnlyCheck, ...], Field(max_length=20)] = ()
    internal_investigation_tasks: Annotated[tuple[InvestigationTaskDraft, ...], Field(max_length=20)] = ()


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
    grouped_issue_ids: Annotated[tuple[ResourceIds, ...], Field(max_length=20)] = ()
    daily_checklist: Annotated[tuple[ShortText, ...], Field(max_length=8)] = ()
    internal_task_drafts: Annotated[tuple[InternalTaskDraft, ...], Field(max_length=20)] = ()


class CustomerCaseAnalysisEnvelope(AgentAnalysisEnvelope):
    proposed_actions: Annotated[tuple[CustomerCaseProposedAction, ...], Field(max_length=10)]
    prohibited_actions: Annotated[tuple[CustomerCaseProposedAction, ...], Field(max_length=10)]
    intents: Annotated[tuple[Identifier, ...], Field(max_length=10)]
    extracted_entities: Annotated[tuple[ExtractedEntity, ...], Field(max_length=20)] = ()
    sentiment_recommendation: ShortText
    urgency_recommendation: ShortText
    reply_draft: Annotated[str | None, Field(max_length=4000)] = None
    escalation_recommended: bool
    rag_evidence_needed: Annotated[tuple[ShortText, ...], Field(max_length=20)] = ()
    voc_tags: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()


class ListingOptimizationAnalysisEnvelope(AgentAnalysisEnvelope):
    proposed_actions: Annotated[tuple[ListingProposedAction, ...], Field(max_length=10)]
    prohibited_actions: Annotated[tuple[ListingProposedAction, ...], Field(max_length=10)]
    proposed_changes: Annotated[tuple[ListingFieldChange, ...], Field(max_length=20)] = ()
    experiment_hypothesis: OptionalLongText = None
    expected_metrics: Annotated[tuple[Identifier, ...], Field(max_length=20)] = ()
    unsupported_claims: Annotated[tuple[LongText, ...], Field(max_length=20)] = ()


class ReconciliationInvestigationEnvelope(AgentAnalysisEnvelope):
    proposed_actions: Annotated[tuple[ReconciliationProposedAction, ...], Field(max_length=10)]
    prohibited_actions: Annotated[tuple[ReconciliationProposedAction, ...], Field(max_length=10)]
    difference_clusters: Annotated[tuple[DifferenceCluster, ...], Field(max_length=20)] = ()
    read_only_checks: Annotated[tuple[ReadOnlyCheck, ...], Field(max_length=20)] = ()
    internal_investigation_tasks: Annotated[tuple[InvestigationTaskDraft, ...], Field(max_length=20)] = ()


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
