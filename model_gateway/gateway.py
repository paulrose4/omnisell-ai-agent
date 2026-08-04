from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, TypeVar, cast

from openai import AsyncOpenAI
from openai.lib._pydantic import to_strict_json_schema
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from model_gateway.contracts import (
    ModelBudgetEstimate,
    ModelBudgetRequest,
    combine_model_budget_estimates,
    estimate_single_provider_budget,
)
from model_gateway.errors import (
    ModelBudgetExceeded,
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

T = TypeVar("T", bound=BaseModel)


def compact_transport_schema(value: Any) -> Any:
    """Remove non-validating JSON Schema annotations before sending them to a provider."""
    if isinstance(value, dict):
        return {
            key: compact_transport_schema(item)
            for key, item in value.items()
            if key not in {"title", "description", "default", "examples"}
        }
    if isinstance(value, list):
        return [compact_transport_schema(item) for item in value]
    return value


@dataclass(frozen=True)
class ModelUsage:
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: Decimal
    estimated_cost: Decimal = Decimal("0")
    retry_count: int = 0
    repair_attempted: bool = False
    first_pass_schema_success: bool = True
    schema_failure_categories: tuple[str, ...] = ()
    initial_latency_ms: Decimal = Decimal("0")
    repair_latency_ms: Decimal = Decimal("0")
    provider_call_count: int = 1
    request_id: str | None = None
    fallback_used: bool = False
    fallback_from_provider: str | None = None
    fallback_reason: str | None = None
    attempted_providers: tuple[str, ...] = ()
    unattributed_providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class StructuredModelResult:
    value: BaseModel
    usage: ModelUsage


class ModelGateway(Protocol):
    provider_name: str

    def estimate_worst_case(self, request: ModelBudgetRequest) -> ModelBudgetEstimate: ...

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> StructuredModelResult: ...


class FakeModelGateway:
    def __init__(
        self,
        outcomes: list[StructuredModelResult | BaseException],
        *,
        provider_name: str = "fake",
        default_model: str = "fake-model",
        max_tokens_per_task: int = 100000,
        input_cost_per_million: Decimal = Decimal("0"),
        output_cost_per_million: Decimal = Decimal("0"),
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.provider_name = provider_name
        self.default_model = default_model
        self.max_tokens_per_task = max_tokens_per_task
        self.input_cost_per_million = Decimal(str(input_cost_per_million))
        self.output_cost_per_million = Decimal(str(output_cost_per_million))

    def estimate_worst_case(self, request: ModelBudgetRequest) -> ModelBudgetEstimate:
        return estimate_single_provider_budget(
            request=request,
            provider=self.provider_name,
            model=request.requested_model or self.default_model,
            provider_token_limit=self.max_tokens_per_task,
            input_cost_per_million=self.input_cost_per_million,
            output_cost_per_million=self.output_cost_per_million,
            timeout_seconds=30,
            pricing_version="fake-pricing-v1",
        )

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> StructuredModelResult:
        self.calls.append({"messages": messages, "schema": schema, "model": model})
        if not self.outcomes:
            raise ModelUnavailable("FakeModelGateway has no configured outcome")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome.value, schema):
            raise ModelSchemaError("FakeModelGateway outcome does not match requested schema")
        return outcome

    async def close(self) -> None:
        return None


class FailoverModelGateway:
    """Use one bounded backup provider for transient primary failures."""

    def __init__(self, *, primary: ModelGateway, fallback: ModelGateway) -> None:
        self.primary = primary
        self.fallback = fallback
        self.provider_name = f"{primary.provider_name}+{fallback.provider_name}"

    def estimate_worst_case(self, request: ModelBudgetRequest) -> ModelBudgetEstimate:
        primary = self.primary.estimate_worst_case(request)
        fallback = self.fallback.estimate_worst_case(request.model_copy(update={"requested_model": None}))
        return combine_model_budget_estimates(
            (*primary.attempts, *fallback.attempts),
            includes_failover=True,
        )

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> StructuredModelResult:
        try:
            result = await self.primary.generate_structured(
                messages=messages,
                schema=schema,
                model=model,
                max_completion_tokens=max_completion_tokens,
            )
            return replace(
                result,
                usage=replace(
                    result.usage,
                    attempted_providers=result.usage.attempted_providers or (self.primary.provider_name,),
                ),
            )
        except Exception as primary_error:
            if not _is_failover_eligible(primary_error):
                raise
            try:
                # The backup provider owns its model name. Never send the primary model ID to it.
                result = await self.fallback.generate_structured(
                    messages=messages,
                    schema=schema,
                    model=None,
                    max_completion_tokens=max_completion_tokens,
                )
            except Exception as fallback_error:
                raise ModelFailoverError(primary_error=primary_error, fallback_error=fallback_error) from fallback_error
            return replace(
                result,
                usage=replace(
                    result.usage,
                    retry_count=result.usage.retry_count + 1,
                    fallback_used=True,
                    fallback_from_provider=_provider_name(self.primary),
                    fallback_reason=type(primary_error).__name__,
                    attempted_providers=(_provider_name(self.primary), result.usage.provider),
                    unattributed_providers=(_provider_name(self.primary),),
                    provider_call_count=result.usage.provider_call_count + 1,
                ),
            )

    async def close(self) -> None:
        for gateway in (self.primary, self.fallback):
            close = getattr(gateway, "close", None)
            if close is not None:
                await close()


class OpenAICompatibleGateway:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        provider_name: str = "openai_compatible",
        max_tokens_per_task: int = 8000,
        timeout: float = 120.0,
        input_cost_per_million: Decimal = Decimal("0"),
        output_cost_per_million: Decimal = Decimal("0"),
        pricing_version: str = "unversioned",
        pricing_effective_at: datetime = datetime(1970, 1, 1, tzinfo=UTC),
        bill_on_unknown_failure: bool = True,
    ) -> None:
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.default_model = default_model
        self.provider_name = provider_name
        self.max_tokens_per_task = max_tokens_per_task
        self.input_cost_per_million = Decimal(str(input_cost_per_million))
        self.output_cost_per_million = Decimal(str(output_cost_per_million))
        self.timeout = timeout
        self.pricing_version = pricing_version
        self.pricing_effective_at = pricing_effective_at
        self.bill_on_unknown_failure = bill_on_unknown_failure

    def estimate_worst_case(self, request: ModelBudgetRequest) -> ModelBudgetEstimate:
        return estimate_single_provider_budget(
            request=request,
            provider=self.provider_name,
            model=request.requested_model or self.default_model,
            provider_token_limit=self.max_tokens_per_task,
            input_cost_per_million=self.input_cost_per_million,
            output_cost_per_million=self.output_cost_per_million,
            timeout_seconds=self.timeout,
            pricing_version=self.pricing_version,
            pricing_effective_at=self.pricing_effective_at,
            bill_on_unknown_failure=self.bill_on_unknown_failure,
        )

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        model: str | None = None,
        max_completion_tokens: int | None = None,
    ) -> StructuredModelResult:
        selected_model = model or self.default_model
        completion_limit = min(max_completion_tokens or self.max_tokens_per_task, self.max_tokens_per_task)
        if completion_limit <= 0:
            raise ModelBudgetExceeded("Model completion token budget must be positive")
        validation_schema = to_strict_json_schema(schema)
        schema_json, maps_adapted = _adapt_open_maps(compact_transport_schema(validation_schema))
        map_instruction = (
            " Object-map fields are represented as arrays of {key, value_json}; "
            "value_json must contain one valid JSON value encoded as a string."
            if maps_adapted
            else ""
        )
        schema_instruction = {
            "role": "system",
            "content": (
                "Return only one JSON object matching the supplied JSON Schema. "
                f"Do not include markdown or commentary.{map_instruction}"
            ),
        }
        response, latency = await self._complete(
            selected_model,
            [schema_instruction, *messages],
            schema_json,
            completion_limit,
        )
        usage = self._usage(response, selected_model, latency)
        self._enforce_budget(usage, "initial generation")
        content = self._content(response)
        try:
            value = _validate_adapted_content(content, schema=schema, validation_schema=validation_schema)
        except (ValidationError, ValueError, TypeError) as first_error:
            repair_errors = _validation_error_details(first_error)
            schema_failure_categories = tuple(
                sorted({str(item.get("type") or "validation_error") for item in repair_errors})
            )
            repair_messages = [
                schema_instruction,
                *messages,
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "The previous JSON did not match the schema. Return exactly one corrected JSON object. Validation errors: "
                    + json.dumps(repair_errors, default=str),
                },
            ]
            repaired, repair_latency = await self._complete(
                selected_model,
                repair_messages,
                schema_json,
                completion_limit,
            )
            repair_usage = self._usage(repaired, selected_model, repair_latency)
            usage = ModelUsage(
                provider=self.provider_name,
                model=selected_model,
                prompt_tokens=usage.prompt_tokens + repair_usage.prompt_tokens,
                completion_tokens=usage.completion_tokens + repair_usage.completion_tokens,
                total_tokens=usage.total_tokens + repair_usage.total_tokens,
                latency_ms=usage.latency_ms + repair_usage.latency_ms,
                estimated_cost=usage.estimated_cost + repair_usage.estimated_cost,
                retry_count=usage.retry_count + repair_usage.retry_count,
                repair_attempted=True,
                first_pass_schema_success=False,
                schema_failure_categories=schema_failure_categories,
                initial_latency_ms=usage.initial_latency_ms or usage.latency_ms,
                repair_latency_ms=repair_usage.latency_ms,
                provider_call_count=usage.provider_call_count + repair_usage.provider_call_count,
                request_id=repair_usage.request_id or usage.request_id,
                attempted_providers=(self.provider_name,),
            )
            self._enforce_budget(usage, "schema repair")
            try:
                value = _validate_adapted_content(
                    self._content(repaired),
                    schema=schema,
                    validation_schema=validation_schema,
                )
            except (ValidationError, ValueError, TypeError) as second_error:
                raise ModelSchemaError(
                    "Structured model output failed schema validation after one repair "
                    f"({len(_validation_error_details(second_error))} errors)"
                ) from second_error
        return StructuredModelResult(value=value, usage=usage)

    async def close(self) -> None:
        await self.client.close()

    async def _complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        schema_json: dict[str, Any],
        max_completion_tokens: int,
    ) -> tuple[Any, Decimal]:
        started = time.perf_counter()
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=cast(list[ChatCompletionMessageParam], messages),
                temperature=0.1,
                max_completion_tokens=max_completion_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "bounded_advisor_output", "strict": True, "schema": schema_json},
                },
            )
        except Exception as exc:
            raise self._classified_error(exc) from exc
        latency = Decimal(str(round((time.perf_counter() - started) * 1000, 2)))
        return response, latency

    def _usage(self, response: Any, model: str, latency_ms: Decimal) -> ModelUsage:
        raw = getattr(response, "usage", None)
        if raw is None:
            raise ModelUsageError("Model provider did not report token usage")
        try:
            prompt_tokens = int(raw.prompt_tokens)
            completion_tokens = int(raw.completion_tokens)
            raw_total = getattr(raw, "total_tokens", None)
            total_tokens = prompt_tokens + completion_tokens if raw_total is None else int(raw_total)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ModelUsageError("Model provider returned invalid token usage") from exc
        if (
            prompt_tokens < 0
            or completion_tokens < 0
            or total_tokens <= 0
            or total_tokens != prompt_tokens + completion_tokens
        ):
            raise ModelUsageError("Model provider returned inconsistent token usage")
        estimated_cost = (
            Decimal(prompt_tokens) * self.input_cost_per_million
            + Decimal(completion_tokens) * self.output_cost_per_million
        ) / Decimal("1000000")
        return ModelUsage(
            provider=self.provider_name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            estimated_cost=estimated_cost,
            initial_latency_ms=latency_ms,
            request_id=str(getattr(response, "id", "") or "") or None,
            attempted_providers=(self.provider_name,),
        )

    def _enforce_budget(self, usage: ModelUsage, stage: str) -> None:
        if usage.total_tokens > self.max_tokens_per_task:
            raise ModelBudgetExceeded(
                f"Model token budget exceeded during {stage}: {usage.total_tokens} > {self.max_tokens_per_task}"
            )

    @staticmethod
    def _content(response: Any) -> str:
        try:
            choice = response.choices[0]
            refusal = getattr(choice.message, "refusal", None)
            finish_reason = str(getattr(choice, "finish_reason", "") or "").lower()
            if refusal or finish_reason in {"content_filter", "safety"}:
                raise ModelSafetyRejected("Model provider rejected the request for safety reasons")
            return choice.message.content or "{}"
        except (AttributeError, IndexError, TypeError) as exc:
            raise ModelSchemaError("Model response did not contain structured message content") from exc

    @staticmethod
    def _classified_error(exc: Exception) -> Exception:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        response = getattr(exc, "response", None)
        status_code = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
        if isinstance(exc, TimeoutError) or "timeout" in name or "timed out" in message:
            return ModelTimeout("Model request timed out")
        if any(marker in message for marker in ("content_filter", "safety", "policy rejection", "moderation")):
            return ModelSafetyRejected("Model provider rejected the request for safety reasons")
        if status_code == 429 or "rate limit" in message or "too many requests" in message:
            return ModelRateLimited("Model provider rate limit was exceeded")
        context_markers = (
            "context length",
            "maximum context",
            "context window",
            "request too large",
            "payload too large",
            "too many tokens",
            "token limit",
        )
        if status_code == 413 or any(marker in message for marker in context_markers):
            return ModelContextRejected("Model provider rejected the request context size")
        if (
            isinstance(exc, ConnectionError)
            or "connection" in name
            or any(marker in message for marker in ("connection refused", "connection reset", "name resolution"))
        ):
            return ModelConnectionError("Model provider connection failed")
        if isinstance(status_code, int) and status_code >= 500:
            return ModelProviderError(f"Model provider returned HTTP {status_code}", retryable=True)
        if isinstance(status_code, int) and status_code in {400, 401, 403, 404, 409, 422}:
            return ModelProviderError(
                f"Model provider rejected the request with HTTP {status_code}",
                retryable=False,
            )
        return ModelUnavailable(f"Model provider is unavailable ({type(exc).__name__})")


class StructuredPlanner:
    def __init__(self, gateway: ModelGateway, model: str | None = None) -> None:
        self.gateway = gateway
        self.model = model

    async def plan(self, *, request: dict[str, Any], schema: type[T]) -> T:
        result = await self.gateway.generate_structured(
            model=self.model,
            schema=schema,
            messages=[
                {
                    "role": "system",
                    "content": "You are a commerce planner. Never execute tools. Produce the minimum safe action plan. Refunds and payment reversals are forbidden.",
                },
                {"role": "user", "content": json.dumps(request, ensure_ascii=False, default=str)},
            ],
        )
        return result.value  # type: ignore[return-value]


def _adapt_open_maps(schema: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    adapted = json.loads(json.dumps(schema))
    maps_adapted = False

    def visit(node: Any) -> Any:
        nonlocal maps_adapted
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        if _is_open_map_schema(node):
            maps_adapted = True
            result: dict[str, Any] = {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value_json": {"type": "string"},
                    },
                    "required": ["key", "value_json"],
                    "additionalProperties": False,
                },
            }
            for annotation in ("title", "description"):
                if annotation in node:
                    result[annotation] = node[annotation]
            return result
        return {
            key: visit(value) for key, value in node.items() if not (key == "pattern" and _uses_regex_lookaround(value))
        }

    return visit(adapted), maps_adapted


def _validate_adapted_content(
    content: str,
    *,
    schema: type[T],
    validation_schema: dict[str, Any],
) -> T:
    payload = json.loads(content)
    restored = _restore_open_maps(payload, validation_schema, validation_schema)
    return schema.model_validate(restored)


def _restore_open_maps(value: Any, node: Any, root: dict[str, Any]) -> Any:
    if not isinstance(node, dict):
        return value
    if "$ref" in node:
        return _restore_open_maps(value, _resolve_schema_ref(root, str(node["$ref"])), root)
    for keyword in ("anyOf", "oneOf"):
        branches = node.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if _schema_accepts_value(branch, value, root):
                    return _restore_open_maps(value, branch, root)
            return value
    if _is_open_map_schema(node):
        if not isinstance(value, list):
            raise ValueError("object-map field must be an array of key/value_json entries")
        restored: dict[str, Any] = {}
        value_schema = node.get("additionalProperties")
        for entry in value:
            if not isinstance(entry, dict) or set(entry) != {"key", "value_json"}:
                raise ValueError("object-map entry must contain only key and value_json")
            key = entry["key"]
            value_json = entry["value_json"]
            if not isinstance(key, str) or not key:
                raise ValueError("object-map entry key must be a non-empty string")
            if key in restored:
                raise ValueError(f"object-map field contains duplicate key: {key}")
            if not isinstance(value_json, str):
                raise ValueError(f"object-map value_json must be a string for key: {key}")
            decoded = json.loads(value_json)
            if isinstance(value_schema, dict):
                decoded = _restore_open_maps(decoded, value_schema, root)
            restored[key] = decoded
        return restored
    if node.get("type") == "object" and isinstance(value, dict):
        properties = node.get("properties") or {}
        return {key: _restore_open_maps(item, properties.get(key), root) for key, item in value.items()}
    if node.get("type") == "array" and isinstance(value, list):
        item_schema = node.get("items")
        return [_restore_open_maps(item, item_schema, root) for item in value]
    return value


def _is_open_map_schema(node: dict[str, Any]) -> bool:
    return node.get("type") == "object" and not node.get("properties") and node.get("additionalProperties") is not False


def _uses_regex_lookaround(value: Any) -> bool:
    return isinstance(value, str) and any(marker in value for marker in ("(?=", "(?!", "(?<=", "(?<!"))


def _schema_accepts_value(node: Any, value: Any, root: dict[str, Any]) -> bool:
    if not isinstance(node, dict):
        return True
    if "$ref" in node:
        return _schema_accepts_value(_resolve_schema_ref(root, str(node["$ref"])), value, root)
    if _is_open_map_schema(node):
        return isinstance(value, list)
    expected_type = node.get("type")
    if expected_type == "null":
        return value is None
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _resolve_schema_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"Unsupported JSON Schema reference: {ref}")
    current: Any = root
    for part in ref[2:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"Unresolvable JSON Schema reference: {ref}")
        current = current[key]
    if not isinstance(current, dict):
        raise ValueError(f"JSON Schema reference does not target an object: {ref}")
    return current


def _validation_error_details(error: Exception) -> list[dict[str, Any]]:
    if isinstance(error, ValidationError):
        return [
            {
                "location": item.get("loc"),
                "type": item.get("type"),
                "message": item.get("msg"),
            }
            for item in error.errors()
        ]
    return [{"location": (), "type": type(error).__name__, "message": str(error)}]


def _is_failover_eligible(error: Exception) -> bool:
    if isinstance(error, ModelProviderError):
        return error.retryable
    return isinstance(error, (ModelConnectionError, ModelRateLimited, ModelTimeout, ModelUnavailable))


def _provider_name(gateway: ModelGateway) -> str:
    return gateway.provider_name
