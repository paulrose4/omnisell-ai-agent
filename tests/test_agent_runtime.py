from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agents.contracts import AgentAnalysisDraft, AgentAnalysisEnvelope, EvidenceBundle, EvidenceReference
from agents.registry import AdvisorRegistry
from agents.runtime import AgentExecutionContext, StructuredBoundedAdvisor
from agents.validators.evidence import EvidenceValidator
from model_gateway.contracts import ModelBudgetRequest, estimate_single_provider_budget
from model_gateway.errors import ModelBudgetExceeded, ModelTimeout
from model_gateway.gateway import FailoverModelGateway, FakeModelGateway, ModelUsage, StructuredModelResult

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)


class FakeGateway:
    def __init__(
        self,
        value: AgentAnalysisDraft,
        usage: ModelUsage | None = None,
        *,
        provider_name: str = "fake",
        default_model: str = "fake-model",
        max_tokens_per_task: int = 100000,
        input_cost_per_million: Decimal = Decimal("0"),
        output_cost_per_million: Decimal = Decimal("0"),
    ) -> None:
        self.value = value
        self.provider_name = provider_name
        self.default_model = default_model
        self.max_tokens_per_task = max_tokens_per_task
        self.input_cost_per_million = input_cost_per_million
        self.output_cost_per_million = output_cost_per_million
        self.usage = usage or ModelUsage(
            provider=provider_name,
            model=default_model,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            latency_ms=Decimal("1.5"),
        )
        self.calls: list[dict] = []

    def estimate_worst_case(self, request: ModelBudgetRequest):
        return estimate_single_provider_budget(
            request=request,
            provider=self.provider_name,
            model=request.requested_model or self.default_model,
            provider_token_limit=self.max_tokens_per_task,
            input_cost_per_million=self.input_cost_per_million,
            output_cost_per_million=self.output_cost_per_million,
            timeout_seconds=30,
            pricing_version="test-pricing-v1",
        )

    async def generate_structured(self, **kwargs: object) -> StructuredModelResult:
        self.calls.append(dict(kwargs))
        return StructuredModelResult(
            value=self.value,
            usage=self.usage,
        )


class SlowGateway:
    provider_name = "slow"

    def estimate_worst_case(self, request: ModelBudgetRequest):
        return estimate_single_provider_budget(
            request=request,
            provider=self.provider_name,
            model="slow-model",
            provider_token_limit=100000,
            input_cost_per_million=Decimal("0"),
            output_cost_per_million=Decimal("0"),
            timeout_seconds=30,
            pricing_version="test-pricing-v1",
        )

    async def generate_structured(self, **kwargs: object) -> StructuredModelResult:
        await asyncio.sleep(1.05)
        raise AssertionError("outer timeout must cancel the provider call")


def bundle() -> EvidenceBundle:
    return EvidenceBundle(
        tenant_id="tenant-a",
        collected_at=NOW,
        evidence=(
            EvidenceReference(
                evidence_id="brief-1",
                evidence_type="daily_operations_brief",
                source="internal_record",
                resource_type="daily_operations_brief",
                resource_id="brief-1",
                observed_at=NOW,
                collected_at=NOW,
                freshness_status="fresh",
                confidence=Decimal("1"),
                verified_amazon_data=False,
                content_digest="b" * 64,
                metadata={},
            ),
        ),
    )


def analysis(*, tenant_id: str = "tenant-a", prompt_version: str = "operations-brief-v1") -> AgentAnalysisEnvelope:
    return AgentAnalysisEnvelope(
        schema_version="1.0",
        agent_type="operations_brief_analyst",
        prompt_version=prompt_version,
        tenant_id=tenant_id,
        workflow_run_id="run-1",
        generated_at=NOW,
        executive_summary="Read-only analysis.",
        evidence=("brief-1",),
        hypotheses=(),
        missing_data=(),
        proposed_actions=(),
        prohibited_actions=(),
        confidence=Decimal("0.80"),
        degraded_reason=None,
        model_usage=None,
        warnings=(),
    )


def draft() -> AgentAnalysisDraft:
    return AgentAnalysisDraft(
        executive_summary="Read-only analysis.",
        evidence=("brief-1",),
        hypotheses=(),
        missing_data=(),
        proposed_actions=(),
        prohibited_actions=(),
        confidence=Decimal("0.80"),
        degraded_reason=None,
        warnings=(),
    )


def context() -> AgentExecutionContext:
    return AgentExecutionContext(
        tenant_id="tenant-a",
        actor_id="operator-1",
        workflow_run_id="run-1",
        correlation_id="corr-1",
        requested_at=NOW,
        locale="en-US",
        allowed_capabilities=frozenset({"read_evidence", "create_internal_task_draft"}),
        max_prompt_tokens=4000,
        max_completion_tokens=2000,
        max_estimated_cost=Decimal("1.00"),
        timeout_seconds=30,
    )


def test_registry_routes_one_explicit_workflow_type() -> None:
    advisor = object()
    registry = AdvisorRegistry({"operations_brief_analysis": advisor})

    assert registry.get("operations_brief_analysis") is advisor
    with pytest.raises(KeyError, match="Unsupported agent workflow_type"):
        registry.get("free_chat")


def test_structured_advisor_makes_one_bounded_gateway_call() -> None:
    gateway = FakeGateway(draft())
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence and return the schema.",
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
        model="fake-model",
    )

    result = asyncio.run(advisor.analyze(context=context(), evidence=bundle()))

    assert result.model_usage is not None
    assert result.model_usage.first_pass_schema_success is True
    assert result.model_usage.provider_call_count == 1
    assert result.model_usage.provider == "fake"
    assert result.tenant_id == "tenant-a"
    assert result.workflow_run_id == "run-1"
    assert result.generated_at.tzinfo is not None
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["schema"] is AgentAnalysisDraft
    assert gateway.calls[0]["max_completion_tokens"] == context().max_completion_tokens
    prompt = gateway.calls[0]["messages"][1]["content"]
    assert "required_identity" not in prompt
    assert "actor_id" not in prompt
    assert "correlation_id" not in prompt


def test_structured_advisor_prompt_preflight_uses_conservative_token_estimate() -> None:
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence and return the schema.",
        gateway=FakeGateway(draft()),
        evidence_validator=EvidenceValidator(),
    )

    request_bytes, estimated_prompt_tokens = advisor._request_size("中文证据" * 200)

    assert estimated_prompt_tokens >= math.ceil(request_bytes / 1.8)


def test_structured_advisor_owns_tenant_identity() -> None:
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=FakeGateway(draft()),
        evidence_validator=EvidenceValidator(),
    )

    result = asyncio.run(advisor.analyze(context=context(), evidence=bundle()))

    assert result.tenant_id == context().tenant_id


def test_structured_advisor_owns_prompt_version() -> None:
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=FakeGateway(draft()),
        evidence_validator=EvidenceValidator(),
    )

    result = asyncio.run(advisor.analyze(context=context(), evidence=bundle()))

    assert result.prompt_version == "operations-brief-v1"


def test_structured_advisor_rejects_evidence_count_before_model_call() -> None:
    gateway = FakeGateway(draft())
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
    )
    base = bundle().evidence[0]
    oversized_bundle = EvidenceBundle(
        tenant_id="tenant-a",
        collected_at=NOW,
        evidence=(base, base.model_copy(update={"evidence_id": "brief-2", "resource_id": "brief-2"})),
    )

    with pytest.raises(ModelBudgetExceeded, match="evidence item budget"):
        asyncio.run(
            advisor.analyze(
                context=context().model_copy(update={"max_evidence_items": 1}),
                evidence=oversized_bundle,
            )
        )

    assert gateway.calls == []


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens", "error"),
    [(11, 1, "prompt"), (1, 6, "completion")],
)
def test_structured_advisor_enforces_prompt_and_completion_budgets_separately(
    prompt_tokens: int,
    completion_tokens: int,
    error: str,
) -> None:
    usage = ModelUsage(
        provider="fake",
        model="fake-model",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        latency_ms=Decimal("1"),
    )
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=FakeGateway(draft(), usage=usage),
        evidence_validator=EvidenceValidator(),
    )

    with pytest.raises(ModelBudgetExceeded, match=error):
        asyncio.run(
            advisor.analyze(
                context=context().model_copy(
                    update={
                        "max_prompt_tokens": 10 if error == "prompt" else 4000,
                        "max_completion_tokens": 5,
                    }
                ),
                evidence=bundle(),
            )
        )


def test_structured_advisor_rejects_oversized_evidence_before_model_call() -> None:
    gateway = FakeGateway(draft())
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
    )
    base = bundle().evidence[0]
    oversized_bundle = EvidenceBundle(
        tenant_id="tenant-a",
        collected_at=NOW,
        evidence=(base.model_copy(update={"metadata": {"payload": "x" * 200}}),),
    )

    with pytest.raises(ModelBudgetExceeded, match="evidence size budget"):
        asyncio.run(
            advisor.analyze(
                context=context().model_copy(update={"max_evidence_chars": 100}),
                evidence=oversized_bundle,
            )
        )

    assert gateway.calls == []


def test_structured_advisor_rejects_request_that_cannot_fit_before_model_call() -> None:
    gateway = FakeGateway(draft())
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence and return the strict schema.",
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
    )

    with pytest.raises(ModelBudgetExceeded, match="preflight prompt token budget"):
        asyncio.run(
            advisor.analyze(
                context=context().model_copy(update={"max_prompt_tokens": 10}),
                evidence=bundle(),
            )
        )

    assert gateway.calls == []


def test_structured_advisor_reserves_global_token_budget_for_schema_repair() -> None:
    gateway = FakeGateway(draft())
    gateway.max_tokens_per_task = 1000
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence and return the strict schema.",
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
    )

    with pytest.raises(ModelBudgetExceeded, match="provider token budget"):
        asyncio.run(advisor.analyze(context=context(), evidence=bundle()))

    assert gateway.calls == []


def test_structured_advisor_reserves_failover_cost_before_any_provider_call() -> None:
    primary = FakeGateway(
        draft(),
        provider_name="primary",
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
    )
    fallback = FakeGateway(
        draft(),
        provider_name="fallback",
        input_cost_per_million=Decimal("1000"),
        output_cost_per_million=Decimal("2000"),
    )
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=FailoverModelGateway(primary=primary, fallback=fallback),
        evidence_validator=EvidenceValidator(),
    )

    with pytest.raises(ModelBudgetExceeded, match="cost budget"):
        asyncio.run(
            advisor.analyze(
                context=context().model_copy(update={"max_estimated_cost": Decimal("0.01")}),
                evidence=bundle(),
            )
        )

    assert primary.calls == []
    assert fallback.calls == []


def test_structured_advisor_persists_typed_failover_budget_estimate() -> None:
    primary = FakeGateway(draft(), provider_name="primary")
    fallback = FakeGateway(draft(), provider_name="fallback")
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=FailoverModelGateway(primary=primary, fallback=fallback),
        evidence_validator=EvidenceValidator(),
    )

    result = asyncio.run(advisor.analyze(context=context(), evidence=bundle()))

    estimate = result.evidence_preparation.model_budget_estimate
    assert estimate.includes_failover is True
    assert estimate.max_provider_attempts == 2
    assert [attempt.provider for attempt in estimate.attempts] == ["primary", "fallback"]
    assert result.model_usage.reserved_tokens == estimate.worst_case_total_tokens
    assert result.model_usage.reserved_cost == estimate.worst_case_estimated_cost


def test_structured_advisor_rejects_fallback_provider_token_cap_before_calls() -> None:
    primary = FakeGateway(draft(), provider_name="primary", max_tokens_per_task=100000)
    fallback = FakeGateway(draft(), provider_name="fallback", max_tokens_per_task=1000)
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=FailoverModelGateway(primary=primary, fallback=fallback),
        evidence_validator=EvidenceValidator(),
    )

    with pytest.raises(ModelBudgetExceeded, match="fallback"):
        asyncio.run(advisor.analyze(context=context(), evidence=bundle()))

    assert primary.calls == []
    assert fallback.calls == []


def test_structured_advisor_marks_primary_timeout_cost_as_partially_unattributed() -> None:
    primary = FakeModelGateway(
        [ModelTimeout("primary timed out")],
        provider_name="primary",
        max_tokens_per_task=100000,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
    )
    fallback = FakeModelGateway(
        [
            StructuredModelResult(
                value=draft(),
                usage=ModelUsage(
                    provider="fallback",
                    model="fallback-model",
                    prompt_tokens=10,
                    completion_tokens=20,
                    total_tokens=30,
                    latency_ms=Decimal("3"),
                    estimated_cost=Decimal("0.00005"),
                ),
            )
        ],
        provider_name="fallback",
        default_model="fallback-model",
        max_tokens_per_task=100000,
        input_cost_per_million=Decimal("3"),
        output_cost_per_million=Decimal("4"),
    )
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=FailoverModelGateway(primary=primary, fallback=fallback),
        evidence_validator=EvidenceValidator(),
    )

    result = asyncio.run(advisor.analyze(context=context(), evidence=bundle()))

    assert result.model_usage.cost_accounting_status == "partially_unattributed"
    assert result.model_usage.unattributed_providers == ("primary",)
    assert result.model_usage.unattributed_cost_upper_bound > 0
    assert result.model_usage.estimated_cost == Decimal("0.00005")


def test_structured_advisor_compacts_total_evidence_deterministically() -> None:
    base = bundle().evidence[0]
    references = tuple(
        base.model_copy(
            update={
                "evidence_id": f"evidence-{index}",
                "resource_id": f"resource-{index}",
                "evidence_type": "operational_issue" if index else "daily_operations_brief",
                "metadata": {"payload": str(index) * 220},
            }
        )
        for index in range(6)
    )
    selected_id = "evidence-0"
    model_draft = draft().model_copy(update={"evidence": (selected_id,)})

    def run(items: tuple[EvidenceReference, ...]) -> tuple[AgentAnalysisEnvelope, FakeGateway]:
        gateway = FakeGateway(model_draft)
        advisor = StructuredBoundedAdvisor(
            agent_type="operations_brief_analyst",
            prompt_version="operations-brief-v1",
            system_prompt="Use only supplied evidence.",
            gateway=gateway,
            evidence_validator=EvidenceValidator(),
        )
        result = asyncio.run(
            advisor.analyze(
                context=context().model_copy(
                    update={
                        "max_prompt_tokens": 8000,
                        "max_total_evidence_chars": 900,
                    }
                ),
                evidence=EvidenceBundle(tenant_id="tenant-a", collected_at=NOW, evidence=items),
            )
        )
        return result, gateway

    forward, forward_gateway = run(references)
    reverse, reverse_gateway = run(tuple(reversed(references)))

    assert forward.evidence_preparation.selected_evidence_ids == reverse.evidence_preparation.selected_evidence_ids
    assert forward.evidence_preparation.omitted_evidence_ids == reverse.evidence_preparation.omitted_evidence_ids
    assert selected_id in forward.evidence_preparation.selected_evidence_ids
    assert forward.evidence_preparation.omitted_evidence_ids
    assert forward.evidence_preparation.degraded is True
    assert "omitted" in " ".join(forward.warnings).lower()
    assert len(forward_gateway.calls) == len(reverse_gateway.calls) == 1
    assert forward_gateway.calls[0]["messages"][1]["content"] == reverse_gateway.calls[0]["messages"][1]["content"]


def test_operations_brief_budget_reduction_keeps_risk_remainder_coverage() -> None:
    base = bundle().evidence[0]
    daily = base
    packet = base.model_copy(
        update={
            "evidence_id": "risk-packet:issue-1",
            "evidence_type": "operations_risk_packet",
            "resource_type": "operations_risk_packet",
            "resource_id": "risk-packet:issue-1",
            "metadata": {"payload": "p" * 100},
        }
    )
    remainder = base.model_copy(
        update={
            "evidence_id": "risk-remainder:brief-1",
            "evidence_type": "operations_risk_remainder",
            "resource_type": "operations_risk_remainder",
            "resource_id": "risk-remainder:brief-1",
            "metadata": {"payload": "r" * 300, "issue_ids": ["issue-2", "issue-3"]},
        }
    )
    gateway = FakeGateway(draft())
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=gateway,
        evidence_validator=EvidenceValidator(),
    )
    evidence_limit = sum(len(advisor._canonical_json(item.model_dump(mode="json"))) for item in (daily, remainder)) + 5

    result = asyncio.run(
        advisor.analyze(
            context=context().model_copy(
                update={
                    "max_prompt_tokens": 9000,
                    "max_total_evidence_chars": evidence_limit,
                }
            ),
            evidence=EvidenceBundle(
                tenant_id="tenant-a",
                collected_at=NOW,
                evidence=(daily, packet, remainder),
            ),
        )
    )

    assert "risk-remainder:brief-1" in result.evidence_preparation.selected_evidence_ids


def test_structured_advisor_preserves_model_timeout_category() -> None:
    advisor = StructuredBoundedAdvisor(
        agent_type="operations_brief_analyst",
        prompt_version="operations-brief-v1",
        system_prompt="Use only supplied evidence.",
        gateway=SlowGateway(),
        evidence_validator=EvidenceValidator(),
    )

    with pytest.raises(ModelTimeout, match="timed out"):
        asyncio.run(
            advisor.analyze(
                context=context().model_copy(update={"timeout_seconds": 1}),
                evidence=bundle(),
            )
        )
