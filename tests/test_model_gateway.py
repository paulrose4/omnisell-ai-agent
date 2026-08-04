from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from model_gateway.errors import (
    ModelConnectionError,
    ModelContextRejected,
    ModelFailoverError,
    ModelProviderError,
    ModelRateLimited,
    ModelSchemaError,
    ModelSafetyRejected,
    ModelTimeout,
    ModelUnavailable,
    ModelUsageError,
)
from model_gateway.factory import create_configured_gateway
from model_gateway.gateway import FailoverModelGateway, FakeModelGateway, ModelUsage, OpenAICompatibleGateway, StructuredModelResult


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
        base_url="http://example.invalid/v1", api_key="test", default_model="fake",
        max_tokens_per_task=100, input_cost_per_million=Decimal("1"), output_cost_per_million=Decimal("2"),
    )
    item.client = Client(outcomes)
    return item


def test_gateway_repairs_json_once_and_records_usage() -> None:
    item = gateway([response('{"bad": true}'), response('{"value": "ok"}', request_id="req-2")])

    result = asyncio.run(item.generate_structured(messages=[{"role": "user", "content": "x"}], schema=Output))

    assert result.value == Output(value="ok")
    assert result.usage.repair_attempted is True
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

    asyncio.run(item.generate_structured(
        messages=[],
        schema=Output,
        max_completion_tokens=37,
    ))

    assert item.client.chat.completions.calls[0]["max_completion_tokens"] == 37


def test_gateway_rejects_provider_response_without_usage_accounting() -> None:
    item = gateway([
        SimpleNamespace(
            id="req-no-usage",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"value": "ok"}'))],
            usage=None,
        )
    ])

    with pytest.raises(ModelUsageError, match="token usage"):
        asyncio.run(item.generate_structured(messages=[], schema=Output))


def test_gateway_converts_pydantic_defaults_to_strict_required_fields() -> None:
    item = gateway([response('{"value": "ok", "note": null}')])

    asyncio.run(item.generate_structured(messages=[], schema=OutputWithDefault))

    strict_schema = item.client.chat.completions.calls[0]["response_format"]["json_schema"]["schema"]
    assert set(strict_schema["properties"]) == set(strict_schema["required"])


def test_gateway_adapts_open_maps_to_closed_strict_schema_without_losing_values() -> None:
    item = gateway([
        response(
            '{"arguments":['
            '{"key":"seller_sku","value_json":"\\"MOCK-OOS\\""},'
            '{"key":"issue_ids","value_json":"[\\"issue-1\\",\\"issue-2\\"]"}'
            ']}'
        )
    ])

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
        usage=ModelUsage(provider="fake", model="fake", prompt_tokens=1, completion_tokens=1, total_tokens=2, latency_ms=Decimal("0")),
    )
    fake = FakeModelGateway([expected])

    result = asyncio.run(fake.generate_structured(messages=[], schema=Output))

    assert result == expected
    assert len(fake.calls) == 1


def test_failover_gateway_uses_backup_only_for_retryable_provider_failure() -> None:
    primary = FakeModelGateway([ModelTimeout("primary timed out")])
    fallback = FakeModelGateway([StructuredModelResult(
        value=Output(value="fallback"),
        usage=ModelUsage(
            provider="wenrugouai", model="backup-model", prompt_tokens=2, completion_tokens=3,
            total_tokens=5, latency_ms=Decimal("12"),
        ),
    )])
    gateway = FailoverModelGateway(primary=primary, fallback=fallback)

    result = asyncio.run(gateway.generate_structured(messages=[], schema=Output, model="primary-model"))

    assert result.value == Output(value="fallback")
    assert result.usage.provider == "wenrugouai"
    assert result.usage.model == "backup-model"
    assert result.usage.fallback_used is True
    assert result.usage.fallback_from_provider == "fake"
    assert result.usage.fallback_reason == "ModelTimeout"
    assert result.usage.attempted_providers == ("fake", "wenrugouai")
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
        vllm_base_url="http://primary.invalid/v1", vllm_api_key="primary-key", vllm_model_name="primary-model",
        model_provider="primary", model_max_tokens_per_task=100, model_timeout_seconds=10,
        model_input_cost_per_million=Decimal("1"), model_output_cost_per_million=Decimal("2"),
        model_fallback_enabled=fallback_enabled, model_fallback_base_url="https://api.wenrugouai.com/v1",
        model_fallback_model_name="backup-model", model_fallback_api_key="backup-key",
        model_fallback_provider="wenrugouai_openai_compatible", model_fallback_timeout_seconds=10,
        model_fallback_input_cost_per_million=Decimal("1"), model_fallback_output_cost_per_million=Decimal("2"),
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
