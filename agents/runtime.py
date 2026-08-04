from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.contracts import (
    AgentAnalysisEnvelope,
    EvidenceBundle,
    EvidencePreparationManifest,
    EvidenceReference,
    MissingDataItem,
    ModelUsage,
    analysis_draft_schema,
)
from agents.validators.evidence import EvidenceValidator
from model_gateway.errors import ModelBudgetExceeded, ModelTimeout
from model_gateway.gateway import ModelGateway
from observability.telemetry import trace_attributes, tracer


class AgentRuntimeError(RuntimeError):
    pass


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("requested_at must be timezone-aware")
    return value


class AgentExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    requested_at: datetime
    locale: str = "en-US"
    allowed_capabilities: frozenset[str] = frozenset()
    max_prompt_tokens: int = Field(default=4000, gt=0)
    max_completion_tokens: int = Field(default=2000, gt=0)
    max_estimated_cost: Decimal = Field(default=Decimal("1"), ge=0)
    max_evidence_items: int = Field(default=100, gt=0, le=500)
    max_evidence_chars: int = Field(default=12000, gt=0, le=100000)
    max_total_evidence_chars: int = Field(default=48000, gt=0, le=1000000)
    max_request_bytes: int = Field(default=128000, gt=0, le=4000000)
    timeout_seconds: int = Field(default=30, gt=0, le=300)

    _validate_requested_at = field_validator("requested_at")(_require_aware)


class BoundedAdvisor(Protocol):
    agent_type: str
    prompt_version: str

    async def analyze(
        self,
        *,
        context: AgentExecutionContext,
        evidence: EvidenceBundle,
    ) -> AgentAnalysisEnvelope: ...


class StructuredBoundedAdvisor:
    _EVIDENCE_TYPE_PRIORITY = {
        "daily_operations_brief": 0,
        "customer_case": 0,
        "listing_quality_snapshot": 0,
        "reconciliation_run": 0,
        "marketplace_policy": 1,
        "product_fact": 1,
        "brand_guideline": 1,
        "operational_issue": 2,
        "operations_risk_packet": 2,
        "operations_risk_remainder": 1,
        "reconciliation_difference": 2,
        "recommended_action": 3,
    }

    def __init__(
        self,
        *,
        agent_type: str,
        prompt_version: str,
        system_prompt: str,
        gateway: ModelGateway,
        evidence_validator: EvidenceValidator,
        model: str | None = None,
        output_schema: type[AgentAnalysisEnvelope] = AgentAnalysisEnvelope,
    ) -> None:
        self.agent_type = agent_type
        self.prompt_version = prompt_version
        self.system_prompt = system_prompt
        self.gateway = gateway
        self.evidence_validator = evidence_validator
        self.model = model
        self.output_schema = output_schema
        self.model_output_schema = analysis_draft_schema(output_schema)
        self.schema_version = "1.0"
        self.prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()

    async def analyze(
        self,
        *,
        context: AgentExecutionContext,
        evidence: EvidenceBundle,
    ) -> AgentAnalysisEnvelope:
        if evidence.tenant_id != context.tenant_id:
            raise AgentRuntimeError("evidence tenant_id does not match execution context")
        if len(evidence.evidence) > context.max_evidence_items:
            raise ModelBudgetExceeded(
                f"Agent evidence item budget exceeded: {len(evidence.evidence)} > {context.max_evidence_items}"
            )
        serialized_references: dict[str, str] = {}
        for reference in evidence.evidence:
            serialized_reference = self._canonical_json(reference.model_dump(mode="json"))
            if len(serialized_reference) > context.max_evidence_chars:
                raise ModelBudgetExceeded(
                    f"Agent evidence size budget exceeded for {reference.evidence_id}: "
                    f"{len(serialized_reference)} > {context.max_evidence_chars}"
                )
            serialized_references[reference.evidence_id] = serialized_reference

        selected_evidence, request, user_content, preparation = self._prepare_request(
            context=context,
            evidence=evidence,
            serialized_references=serialized_references,
        )
        try:
            result = await asyncio.wait_for(
                self.gateway.generate_structured(
                    model=self.model,
                    schema=self.model_output_schema,
                    max_completion_tokens=context.max_completion_tokens,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                ),
                timeout=context.timeout_seconds,
            )
        except TimeoutError as exc:
            raise ModelTimeout("Bounded advisor model call timed out") from exc

        with tracer.start_as_current_span("agent.output.validate") as output_span:
            output_span.set_attributes(trace_attributes(agent_type=self.agent_type, prompt_version=self.prompt_version))
            if not isinstance(result.value, self.model_output_schema):
                raise AgentRuntimeError("model gateway returned an unexpected schema type")
        analysis = self.output_schema.model_validate(
            {
                **result.value.model_dump(mode="python"),
                "schema_version": self.schema_version,
                "agent_type": self.agent_type,
                "prompt_version": self.prompt_version,
                "tenant_id": context.tenant_id,
                "workflow_run_id": context.workflow_run_id,
                "generated_at": datetime.now(UTC),
                "model_usage": None,
                "evidence_preparation": preparation,
            }
        )

        raw_usage = result.usage
        usage = ModelUsage(
            provider=raw_usage.provider,
            model=raw_usage.model,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            schema_version=self.schema_version,
            prompt_tokens=raw_usage.prompt_tokens,
            completion_tokens=raw_usage.completion_tokens,
            total_tokens=raw_usage.total_tokens,
            estimated_cost=Decimal(str(getattr(raw_usage, "estimated_cost", 0))),
            latency_ms=Decimal(str(raw_usage.latency_ms)),
            retry_count=int(getattr(raw_usage, "retry_count", 0)),
            repair_attempted=bool(getattr(raw_usage, "repair_attempted", False)),
            request_id=getattr(raw_usage, "request_id", None),
            fallback_used=bool(getattr(raw_usage, "fallback_used", False)),
            fallback_from_provider=getattr(raw_usage, "fallback_from_provider", None),
            fallback_reason=getattr(raw_usage, "fallback_reason", None),
            attempted_providers=tuple(getattr(raw_usage, "attempted_providers", ()) or ()),
        )
        if usage.prompt_tokens > context.max_prompt_tokens:
            raise ModelBudgetExceeded(
                "model usage exceeded execution context prompt token budget: "
                f"{usage.prompt_tokens} > {context.max_prompt_tokens}"
            )
        if usage.completion_tokens > context.max_completion_tokens:
            raise ModelBudgetExceeded(
                "model usage exceeded execution context completion token budget: "
                f"{usage.completion_tokens} > {context.max_completion_tokens}"
            )
        if usage.total_tokens > context.max_prompt_tokens + context.max_completion_tokens:
            raise ModelBudgetExceeded(
                "model usage exceeded execution context token budget: "
                f"{usage.total_tokens} > {context.max_prompt_tokens + context.max_completion_tokens}"
            )
        if usage.estimated_cost > context.max_estimated_cost:
            raise ModelBudgetExceeded(
                "model usage exceeded execution context cost budget: "
                f"{usage.estimated_cost} > {context.max_estimated_cost}"
            )
        analysis_updates: dict[str, Any] = {"model_usage": usage}
        if preparation.degraded:
            omitted_count = preparation.omitted_evidence_count
            warning = f"Runtime preflight omitted {omitted_count} lower-priority evidence item(s) to stay within the model budget."
            analysis_updates.update({
                "warnings": (*analysis.warnings, warning),
                "missing_data": (
                    *analysis.missing_data,
                    MissingDataItem(
                        field="evidence_bundle",
                        reason=f"{omitted_count} lower-priority evidence item(s) were omitted by deterministic runtime preflight.",
                        required_for="complete advisory analysis",
                        blocking=False,
                        suggested_source="agent_run.evidence_manifest",
                    ),
                ),
                "degraded_reason": analysis.degraded_reason or "evidence_reduced_by_runtime_budget",
            })
        analysis = analysis.model_copy(update=analysis_updates)
        with tracer.start_as_current_span("agent.evidence.validate") as evidence_span:
            evidence_span.set_attributes(trace_attributes(agent_type=self.agent_type, prompt_version=self.prompt_version))
            return self.evidence_validator.validate(bundle=selected_evidence, analysis=analysis)

    def _prepare_request(
        self,
        *,
        context: AgentExecutionContext,
        evidence: EvidenceBundle,
        serialized_references: dict[str, str],
    ) -> tuple[EvidenceBundle, dict[str, Any], str, EvidencePreparationManifest]:
        ordered = sorted(evidence.evidence, key=self._evidence_sort_key)
        selected: list[EvidenceReference] = []
        selected_chars = 0
        for reference in ordered:
            reference_chars = len(serialized_references[reference.evidence_id])
            if selected_chars + reference_chars > context.max_total_evidence_chars:
                continue
            selected.append(reference)
            selected_chars += reference_chars

        if not selected:
            raise ModelBudgetExceeded("Agent preflight evidence budget cannot fit any evidence item")

        while selected:
            selected_bundle = EvidenceBundle(
                tenant_id=evidence.tenant_id,
                collected_at=evidence.collected_at,
                evidence=tuple(selected),
            )
            request = self._request_payload(context=context, evidence=selected_bundle)
            user_content = self._canonical_json(request)
            request_bytes, estimated_prompt_tokens = self._request_size(user_content)
            if request_bytes <= context.max_request_bytes and estimated_prompt_tokens <= context.max_prompt_tokens:
                break
            removed = selected.pop()
            selected_chars -= len(serialized_references[removed.evidence_id])
        else:
            raise ModelBudgetExceeded(
                "Agent preflight prompt token budget cannot fit the required prompt, schema, and one evidence item"
            )

        worst_case_cost = self._worst_case_cost(
            prompt_tokens=estimated_prompt_tokens,
            completion_tokens=context.max_completion_tokens,
        )
        worst_case_tokens = (
            estimated_prompt_tokens * 2
            + context.max_completion_tokens * 3
            + 512
        )
        model_token_budget = getattr(self.gateway, "max_tokens_per_task", None)
        if model_token_budget is not None and worst_case_tokens > int(model_token_budget):
            raise ModelBudgetExceeded(
                "Agent preflight worst-case initial and repair usage exceeds the global model token budget"
            )
        if worst_case_cost > context.max_estimated_cost:
            raise ModelBudgetExceeded(
                "Agent preflight worst-case initial and repair cost exceeds execution context cost budget"
            )

        selected_ids = tuple(reference.evidence_id for reference in selected)
        selected_id_set = set(selected_ids)
        omitted_ids = tuple(
            reference.evidence_id for reference in ordered if reference.evidence_id not in selected_id_set
        )
        preparation = EvidencePreparationManifest(
            original_evidence_count=len(ordered),
            selected_evidence_count=len(selected_ids),
            omitted_evidence_count=len(omitted_ids),
            original_serialized_chars=sum(len(value) for value in serialized_references.values()),
            selected_serialized_chars=selected_chars,
            request_bytes=request_bytes,
            estimated_prompt_tokens=estimated_prompt_tokens,
            max_prompt_tokens=context.max_prompt_tokens,
            max_total_evidence_chars=context.max_total_evidence_chars,
            selected_evidence_ids=selected_ids,
            omitted_evidence_ids=omitted_ids,
            worst_case_estimated_tokens=worst_case_tokens,
            model_token_budget=model_token_budget,
            worst_case_estimated_cost=worst_case_cost,
            degraded=bool(omitted_ids),
        )
        return selected_bundle, request, user_content, preparation

    def _request_payload(
        self,
        *,
        context: AgentExecutionContext,
        evidence: EvidenceBundle,
    ) -> dict[str, Any]:
        return {
            "analysis_context": context.model_dump(
                mode="json",
                include={"requested_at", "locale", "allowed_capabilities"},
            ),
            "evidence_bundle": evidence.model_dump(mode="json", exclude={"tenant_id"}),
        }

    def _request_size(self, user_content: str) -> tuple[int, int]:
        schema_content = self._canonical_json(to_strict_json_schema(self.model_output_schema))
        schema_instruction = "Return only one JSON object matching the supplied JSON Schema. Do not include markdown or commentary."
        serialized = self._canonical_json({
            "messages": [
                {"role": "system", "content": schema_instruction},
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_schema": schema_content,
        })
        request_bytes = len(serialized.encode("utf-8"))
        return request_bytes, max(1, math.ceil(request_bytes / 1.8))

    def _worst_case_cost(self, *, prompt_tokens: int, completion_tokens: int) -> Decimal:
        input_rate = Decimal(str(getattr(self.gateway, "input_cost_per_million", 0)))
        output_rate = Decimal(str(getattr(self.gateway, "output_cost_per_million", 0)))
        repair_prompt_tokens = prompt_tokens + completion_tokens + 512
        total_input_tokens = prompt_tokens + repair_prompt_tokens
        total_output_tokens = completion_tokens * 2
        return (
            Decimal(total_input_tokens) * input_rate
            + Decimal(total_output_tokens) * output_rate
        ) / Decimal("1000000")

    @classmethod
    def _evidence_sort_key(cls, reference: EvidenceReference) -> tuple[int, str, str]:
        explicit_priority = reference.metadata.get("selection_priority")
        if isinstance(explicit_priority, int) and not isinstance(explicit_priority, bool):
            priority = max(-100, min(explicit_priority, 100))
        else:
            priority = cls._EVIDENCE_TYPE_PRIORITY.get(reference.evidence_type, 50)
        return priority, reference.evidence_type, reference.evidence_id

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
