from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from model_gateway.contracts import (
    ModelAttemptBudget,
    ModelBudgetEstimate,
    ModelBudgetRequest,
    combine_model_budget_estimates,
    estimate_single_provider_budget,
)
from model_gateway.errors import (
    ModelConnectionError,
    ModelContextRejected,
    ModelFailoverError,
    ModelProviderError,
    ModelRateLimited,
    ModelSafetyRejected,
    ModelSchemaError,
    ModelTimeout,
    ModelUnavailable,
    ModelUsageError,
)
from model_gateway.factory import create_configured_gateway
from model_gateway.gateway import (
    FailoverModelGateway,
    FakeModelGateway,
    ModelUsage,
    OpenAICompatibleGateway,
    StructuredModelResult,
)


class Output(BaseModel):
    value: str


class OutputWithDefault(BaseModel):
    value: str
    note: str | None = None


class OutputWithArguments(BaseModel):
    arguments: dict[str, object]


class OutputWithConfidence(BaseModel):
    confidence: Decimal = Field(ge=0, le=1)


def response(content: str, *, request_id: str = "req-1"):
    return SimpleNamespace(
        id=request_id,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class Completions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Client:
    def __init__(self, outcomes):
        self.chat = SimpleNamespace(completions=Completions(outcomes))


def gateway(outcomes) -> OpenAICompatibleGateway:
    item = OpenAICompatibleGateway(
        base_url="http://example.invalid/v1",
        api_key="test",
        default_model="fake",
        max_tokens_per_task=100,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
    )
    item.client = Client(outcomes)
    return item


def test_gateway_repairs_json_once_and_records_usage() -> None:
    item = gateway([response('{"bad": true}'), response('{"value": "ok"}', request_id="req-2")])

    result = asyncio.run(item.generate_structured(messages=[{"role": "user", "content": "x"}], schema=Output))

    assert result.value == Output(value="ok")
    assert result.usage.repair_attempted is True
    assert result.usage.first_pass_schema_success is False
    assert result.usage.schema_failure_categories == ("missing",)
    assert result.usage.provider_call_count == 2
    assert result.usage.initial_latency_ms >= 0
    assert result.usage.repair_latency_ms >= 0
    assert result.usage.total_tokens == 30
    assert result.usage.estimated_cost == Decimal("0.00004")
    assert result.usage.request_id == "req-2"
    assert len(item.client.chat.completions.calls) == 2
    assert item.client.chat.completions.calls[0]["response_format"]["type"] == "json_schema"

    strict_schema = item.client.chat.completions.calls[0]["response_format"]["json_schema"]["schema"]
    assert set(strict_schema["properties"]) == set(strict_schema["required"])

    repair_instruction = item.client.chat.completions.calls[1]["messages"][-1]["content"]
    assert "missing" in repair_instruction
    assert "Field required" in repair_instruction


def test_gateway_passes_completion_budget_to_provider() -> None:
    item = gateway([response('{"value": "ok"}')])

    asyncio.run(
        item.generate_structured(
            messages=[],
            schema=Output,
            max_completion_tokens=37,
        )
    )

    assert item.client.chat.completions.calls[0]["max_completion_tokens"] == 37


def test_gateway_records_first_pass_schema_success_without_repair() -> None:
    item = gateway([response('{"value": "ok"}')])

    result = asyncio.run(item.generate_structured(messages=[], schema=Output))

    assert result.usage.first_pass_schema_success is True
    assert result.usage.schema_failure_categories == ()
    assert result.usage.provider_call_count == 1
    assert result.usage.repair_latency_ms == 0


def test_gateway_rejects_provider_response_without_usage_accounting() -> None:
    item = gateway(
        [
            SimpleNamespace(
                id="req-no-usage",
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"value": "ok"}'))],
                usage=None,
            )
        ]
    )

    with pytest.raises(ModelUsageError, match="token usage"):
        asyncio.run(item.generate_structured(messages=[], schema=Output))


def test_gateway_converts_pydantic_defaults_to_strict_required_fields() -> None:
    item = gateway([response('{"value": "ok", "note": null}')])

    asyncio.run(item.generate_structured(messages=[], schema=OutputWithDefault))

    strict_schema = item.client.chat.completions.calls[0]["response_format"]["json_schema"]["schema"]
    assert set(strict_schema["properties"]) == set(strict_schema["required"])


def test_gateway_adapts_open_maps_to_closed_strict_schema_without_losing_values() -> None:
    item = gateway(
        [
            response(
                '{"arguments":['
                '{"key":"seller_sku","value_json":"\\"MOCK-OOS\\""},'
                '{"key":"issue_ids","value_json":"[\\"issue-1\\",\\"issue-2\\"]"}'
                "]}"
            )
        ]
    )

    result = asyncio.run(item.generate_structured(messages=[], schema=OutputWithArguments))

    assert result.value.arguments == {
        "seller_sku": "MOCK-OOS",
        "issue_ids": ["issue-1", "issue-2"],
    }
    strict_schema = item.client.chat.completions.calls[0]["response_format"]["json_schema"]["schema"]
    arguments_schema = strict_schema["properties"]["arguments"]
    assert arguments_schema["type"] == "array"
    assert arguments_schema["items"]["additionalProperties"] is False


def test_gateway_removes_provider_unsupported_regex_lookaround_from_transport_schema() -> None:
    item = gateway([response('{"confidence":"0.80"}')])

    result = asyncio.run(item.generate_structured(messages=[], schema=OutputWithConfidence))

    assert result.value.confidence == Decimal("0.80")
    strict_schema = item.client.chat.completions.calls[0]["response_format"]["json_schema"]["schema"]
    serialized_schema = str(strict_schema)
    assert "(?=" not in serialized_schema
    assert "(?!" not in serialized_schema
    assert "(?<=" not in serialized_schema
    assert "(?<!" not in serialized_schema


def test_gateway_raises_schema_error_after_single_repair() -> None:
    item = gateway([response('{"bad": true}'), response('{"still_bad": true}')])

    with pytest.raises(ModelSchemaError):
        asyncio.run(item.generate_structured(messages=[], schema=Output))


def test_gateway_classifies_timeout_and_connection_failure() -> None:
    with pytest.raises(ModelTimeout):
        asyncio.run(gateway([TimeoutError("slow")]).generate_structured(messages=[], schema=Output))
    with pytest.raises(ModelConnectionError):
        asyncio.run(gateway([ConnectionError("offline")]).generate_structured(messages=[], schema=Output))


@pytest.mark.parametrize(
    ("status_code", "message", "expected"),
    [
        (400, "maximum context length exceeded", ModelContextRejected),
        (413, "request too large", ModelContextRejected),
        (429, "rate limit exceeded", ModelRateLimited),
        (502, "bad gateway", ModelProviderError),
        (503, "service unavailable", ModelProviderError),
    ],
)
def test_gateway_classifies_provider_http_failures(status_code: int, message: str, expected: type[Exception]) -> None:
    error = RuntimeError(message)
    error.status_code = status_code

    with pytest.raises(expected):
        asyncio.run(gateway([error]).generate_structured(messages=[], schema=Output))


def test_fake_gateway_is_injectable_and_deterministic() -> None:
    expected = StructuredModelResult(
        value=Output(value="fake"),
        usage=ModelUsage(
            provider="fake", model="fake", prompt_tokens=1, completion_tokens=1, total_tokens=2, latency_ms=Decimal("0")
        ),
    )
    fake = FakeModelGateway([expected])

    result = asyncio.run(fake.generate_structured(messages=[], schema=Output))

    assert result == expected
    assert len(fake.calls) == 1


def test_failover_gateway_uses_backup_only_for_retryable_provider_failure() -> None:
    primary = FakeModelGateway([ModelTimeout("primary timed out")], provider_name="primary")
    fallback = FakeModelGateway(
        [
            StructuredModelResult(
                value=Output(value="fallback"),
                usage=ModelUsage(
                    provider="wenrugouai",
                    model="backup-model",
                    prompt_tokens=2,
                    completion_tokens=3,
                    total_tokens=5,
                    latency_ms=Decimal("12"),
                ),
            )
        ],
        provider_name="wenrugouai",
    )
    gateway = FailoverModelGateway(primary=primary, fallback=fallback)

    result = asyncio.run(gateway.generate_structured(messages=[], schema=Output, model="primary-model"))

    assert result.value == Output(value="fallback")
    assert result.usage.provider == "wenrugouai"
    assert result.usage.model == "backup-model"
    assert result.usage.fallback_used is True
    assert result.usage.fallback_from_provider == "primary"
    assert result.usage.fallback_reason == "ModelTimeout"
    assert result.usage.attempted_providers == ("primary", "wenrugouai")
    assert result.usage.unattributed_providers == ("primary",)
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1
    assert fallback.calls[0]["model"] is None


def test_failover_gateway_does_not_hide_safety_rejection() -> None:
    primary = FakeModelGateway([ModelSafetyRejected("blocked")])
    fallback = FakeModelGateway([ModelUnavailable("must not be called")])
    gateway = FailoverModelGateway(primary=primary, fallback=fallback)

    with pytest.raises(ModelSafetyRejected):
        asyncio.run(gateway.generate_structured(messages=[], schema=Output))

    assert len(fallback.calls) == 0


@pytest.mark.parametrize(
    "error",
    [
        ModelSchemaError("schema mismatch"),
        ModelProviderError("bad request", retryable=False),
        ModelContextRejected("context too large"),
    ],
)
def test_failover_gateway_does_not_retry_non_transient_failures(error: Exception) -> None:
    primary = FakeModelGateway([error], provider_name="primary")
    fallback = FakeModelGateway([ModelUnavailable("must not be called")], provider_name="fallback")
    gateway = FailoverModelGateway(primary=primary, fallback=fallback)

    with pytest.raises(type(error)):
        asyncio.run(gateway.generate_structured(messages=[], schema=Output))

    assert len(fallback.calls) == 0


def test_failover_gateway_surfaces_both_failures_without_free_text_fallback() -> None:
    primary = FakeModelGateway([ModelConnectionError("primary offline")])
    fallback = FakeModelGateway([ModelSchemaError("backup schema mismatch")])
    gateway = FailoverModelGateway(primary=primary, fallback=fallback)

    with pytest.raises(ModelFailoverError) as error:
        asyncio.run(gateway.generate_structured(messages=[], schema=Output))

    assert error.value.primary_error_category == "ModelConnectionError"
    assert error.value.fallback_error_category == "ModelSchemaError"
    assert error.value.retryable is False


def _gateway_settings(*, fallback_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url="http://primary.invalid/v1",
        model_api_key="primary-key",
        model_name="primary-model",
        model_provider="primary",
        model_max_tokens_per_task=100,
        model_timeout_seconds=10,
        model_input_cost_per_million=Decimal("1"),
        model_output_cost_per_million=Decimal("2"),
        model_pricing_version="primary-pricing-v1",
        model_pricing_effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        model_bill_on_unknown_failure=True,
        model_fallback_enabled=fallback_enabled,
        model_fallback_base_url="https://api.wenrugouai.com/v1",
        model_fallback_model_name="backup-model",
        model_fallback_api_key="backup-key",
        model_fallback_provider="wenrugouai_openai_compatible",
        model_fallback_timeout_seconds=10,
        model_fallback_input_cost_per_million=Decimal("1"),
        model_fallback_output_cost_per_million=Decimal("2"),
        model_fallback_pricing_version="fallback-pricing-v1",
        model_fallback_pricing_effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        model_fallback_bill_on_unknown_failure=True,
    )


def test_configured_gateway_does_not_enable_fallback_by_default() -> None:
    gateway = create_configured_gateway(_gateway_settings(fallback_enabled=False))

    assert isinstance(gateway, OpenAICompatibleGateway)
    asyncio.run(gateway.close())


def test_configured_gateway_builds_explicit_fallback_chain() -> None:
    gateway = create_configured_gateway(_gateway_settings(fallback_enabled=True))

    assert isinstance(gateway, FailoverModelGateway)
    assert gateway.fallback.default_model == "backup-model"
    asyncio.run(gateway.close())


def test_single_gateway_exposes_typed_worst_case_budget() -> None:
    item = OpenAICompatibleGateway(
        base_url="http://example.invalid/v1",
        api_key="test",
        default_model="primary-model",
        provider_name="primary",
        max_tokens_per_task=1000,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
        pricing_version="primary-price-v1",
    )

    estimate = item.estimate_worst_case(
        ModelBudgetRequest(
            estimated_prompt_tokens=100,
            max_completion_tokens=50,
            requested_model="requested-model",
        )
    )

    assert estimate.max_provider_attempts == 1
    assert estimate.includes_schema_repair is True
    assert estimate.includes_failover is False
    assert estimate.worst_case_total_tokens == 862
    assert estimate.worst_case_estimated_cost == Decimal("0.000962")
    assert estimate.provider_limits_satisfied is True
    assert estimate.attempts[0].model == "requested-model"
    assert estimate.attempts[0].pricing_version == "primary-price-v1"


def test_failover_budget_sums_both_providers_and_preserves_each_token_cap() -> None:
    primary = OpenAICompatibleGateway(
        base_url="http://primary.invalid/v1",
        api_key="test",
        default_model="primary-model",
        provider_name="primary",
        max_tokens_per_task=1000,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
        pricing_version="primary-price-v1",
    )
    fallback = OpenAICompatibleGateway(
        base_url="http://fallback.invalid/v1",
        api_key="test",
        default_model="fallback-model",
        provider_name="fallback",
        max_tokens_per_task=700,
        input_cost_per_million=Decimal("3"),
        output_cost_per_million=Decimal("4"),
        pricing_version="fallback-price-v2",
    )
    chain = FailoverModelGateway(primary=primary, fallback=fallback)
    request = ModelBudgetRequest(estimated_prompt_tokens=100, max_completion_tokens=50)

    primary_estimate = primary.estimate_worst_case(request)
    estimate = chain.estimate_worst_case(request)

    assert estimate.max_provider_attempts == 2
    assert estimate.includes_failover is True
    assert estimate.worst_case_total_tokens == primary_estimate.worst_case_total_tokens * 2
    assert estimate.worst_case_estimated_cost > primary_estimate.worst_case_estimated_cost
    assert [attempt.provider for attempt in estimate.attempts] == ["primary", "fallback"]
    assert estimate.attempts[1].model == "fallback-model"
    assert estimate.attempts[1].provider_token_limit == 700
    assert estimate.provider_limits_satisfied is False


def _valid_attempt(**updates) -> ModelAttemptBudget:
    payload = {
        "provider": "primary",
        "model": "model-1",
        "initial_prompt_tokens": 100,
        "initial_completion_tokens": 50,
        "repair_prompt_tokens": 25,
        "repair_completion_tokens": 10,
        "max_input_tokens": 125,
        "max_output_tokens": 60,
        "max_total_tokens": 185,
        "provider_token_limit": 200,
        "token_limit_satisfied": True,
        "max_repair_calls": 1,
        "input_cost_per_million": Decimal("1"),
        "output_cost_per_million": Decimal("2"),
        "max_estimated_cost": Decimal("0.000245"),
        "timeout_seconds": 10,
        "bill_on_unknown_failure": True,
        "pricing_version": "pricing-v1",
        "pricing_effective_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    payload.update(updates)
    return ModelAttemptBudget(**payload)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"max_input_tokens": 124}, "max_input_tokens"),
        ({"max_output_tokens": 59}, "max_output_tokens"),
        ({"max_total_tokens": 184}, "max_total_tokens"),
        ({"token_limit_satisfied": False}, "token_limit_satisfied"),
    ],
)
def test_model_attempt_budget_rejects_inconsistent_totals(updates: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _valid_attempt(**updates)


def test_model_attempt_budget_requires_timezone_aware_pricing_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _valid_attempt(pricing_effective_at=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"max_provider_attempts": 2}, "max_provider_attempts"),
        ({"worst_case_total_tokens": 184}, "worst_case_total_tokens"),
        ({"aggregate_provider_token_limit": 199}, "aggregate_provider_token_limit"),
        ({"worst_case_estimated_cost": Decimal("0.01")}, "worst_case_estimated_cost"),
        ({"provider_limits_satisfied": False}, "provider_limits_satisfied"),
    ],
)
def test_model_budget_estimate_rejects_inconsistent_aggregates(updates: dict, message: str) -> None:
    attempt = _valid_attempt()
    payload = {
        "attempts": (attempt,),
        "max_provider_attempts": 1,
        "worst_case_total_tokens": 185,
        "aggregate_provider_token_limit": 200,
        "worst_case_estimated_cost": Decimal("0.000245"),
        "provider_limits_satisfied": True,
        "includes_schema_repair": True,
        "includes_failover": False,
        "unknown_failure_may_be_billed": True,
        "assumptions": (),
    }
    payload.update(updates)
    with pytest.raises(ValueError, match=message):
        ModelBudgetEstimate(**payload)


def test_model_budget_estimate_requires_at_least_one_attempt() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ModelBudgetEstimate(
            attempts=(),
            max_provider_attempts=1,
            worst_case_total_tokens=1,
            aggregate_provider_token_limit=1,
            worst_case_estimated_cost=Decimal("0"),
            provider_limits_satisfied=True,
            includes_schema_repair=False,
            includes_failover=False,
            unknown_failure_may_be_billed=False,
            assumptions=(),
        )


def test_single_provider_budget_without_repair_has_one_call_and_exact_cost_bound() -> None:
    estimate = estimate_single_provider_budget(
        request=ModelBudgetRequest(
            estimated_prompt_tokens=100,
            max_completion_tokens=50,
            reserve_schema_repair=False,
        ),
        provider="primary",
        model="model-1",
        provider_token_limit=149,
        input_cost_per_million=Decimal("1"),
        output_cost_per_million=Decimal("2"),
        timeout_seconds=5,
        pricing_version="pricing-v1",
        bill_on_unknown_failure=False,
    )

    assert estimate.includes_schema_repair is False
    assert estimate.provider_limits_satisfied is False
    assert estimate.unknown_failure_may_be_billed is False
    assert estimate.attempts[0].max_repair_calls == 0
    assert estimate.worst_case_total_tokens == 150
    assert estimate.cost_upper_bound_for(("primary",)) == Decimal("0.0002")
    assert estimate.cost_upper_bound_for(("fallback",)) == Decimal("0")


def test_combined_budget_marks_failover_and_unknown_billing_across_attempts() -> None:
    primary = _valid_attempt()
    fallback = _valid_attempt(
        provider="fallback",
        model="model-2",
        bill_on_unknown_failure=False,
    )

    estimate = combine_model_budget_estimates((primary, fallback), includes_failover=True)

    assert estimate.includes_failover is True
    assert estimate.max_provider_attempts == 2
    assert estimate.unknown_failure_may_be_billed is True
    assert estimate.cost_upper_bound_for(("fallback",)) == fallback.max_estimated_cost
