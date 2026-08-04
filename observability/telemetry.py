from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, TypeVar

from opentelemetry import metrics, trace

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except ImportError:  # pragma: no cover - telemetry remains optional for local tooling
    FastAPIInstrumentor = None  # type: ignore[assignment]
    MeterProvider = None  # type: ignore[assignment]
    PeriodicExportingMetricReader = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[assignment]
    BatchSpanProcessor = None  # type: ignore[assignment]

try:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:  # pragma: no cover - exporters are optional in lightweight test installs
    OTLPMetricExporter = None  # type: ignore[assignment]
    OTLPSpanExporter = None  # type: ignore[assignment]


logger = logging.getLogger("omnisell.observability")
tracer = trace.get_tracer("omnisell")
meter = metrics.get_meter("omnisell")

workflow_transition_duration = meter.create_histogram(
    "omnisell.agent.transition.duration",
    unit="ms",
    description="Time spent in an Agent workflow state before transition.",
)
llm_call_duration = meter.create_histogram(
    "omnisell.llm.call.duration",
    unit="ms",
    description="LLM request latency.",
)
llm_token_usage = meter.create_counter(
    "omnisell.llm.token.usage",
    unit="token",
    description="LLM input and output token usage.",
)
policy_decisions = meter.create_counter(
    "omnisell.policy.decisions",
    unit="decision",
    description="Policy Engine decisions by action and risk level.",
)
amazon_api_duration = meter.create_histogram(
    "omnisell.amazon.api.duration",
    unit="ms",
    description="Amazon SP-API attempt latency.",
)
amazon_api_calls = meter.create_counter(
    "omnisell.amazon.api.calls",
    unit="call",
    description="Amazon SP-API calls by operation and status.",
)

_capture_llm_content = False
_initialized = False
_agent_run_context: ContextVar[dict[str, str]] = ContextVar("agent_run_context", default={})
_SENSITIVE_TRACE_ATTRIBUTE_KEYS = frozenset({
    "tenant_id",
    "actor_id",
    "user_id",
    "run_id",
    "correlation_id",
    "resource_id",
    "request_id",
    "action_hash",
    "idempotency_key",
    "selling_partner_id",
    "seller_sku",
    "sku",
    "order_id",
    "listing_id",
    "customer_case_id",
    "evidence_id",
    "http_route",
})


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


def configure_telemetry(settings: Any, app: Any | None = None) -> None:
    global _capture_llm_content, _initialized, tracer, meter
    _capture_llm_content = bool(getattr(settings, "telemetry_capture_llm_content", False))
    if _initialized:
        if app is not None and FastAPIInstrumentor is not None:
            FastAPIInstrumentor.instrument_app(app)
        return

    if Resource is None or TracerProvider is None or MeterProvider is None:
        instrument_openai_calls()
        _initialized = True
        return

    resource = Resource.create(
        {
            "service.name": getattr(settings, "otel_service_name", "omnisell-api"),
            "service.version": "0.2.0",
            "deployment.environment.name": getattr(settings, "environment", "development"),
        }
    )
    trace_provider = TracerProvider(resource=resource)
    metric_readers = []
    endpoint = str(getattr(settings, "otel_exporter_otlp_endpoint", "") or "").rstrip("/")
    headers = _parse_headers(getattr(settings, "otel_exporter_otlp_headers", ""))
    if endpoint and OTLPSpanExporter is not None and OTLPMetricExporter is not None and BatchSpanProcessor is not None and PeriodicExportingMetricReader is not None:
        trace_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers))
        )
        metric_readers.append(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers)
            )
        )
    trace.set_tracer_provider(trace_provider)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=metric_readers))
    tracer = trace.get_tracer("omnisell")
    meter = metrics.get_meter("omnisell")
    _initialized = True
    instrument_openai_calls()
    if app is not None and FastAPIInstrumentor is not None:
            FastAPIInstrumentor.instrument_app(app)


def bind_agent_run_context(**values: str) -> Any:
    return _agent_run_context.set({key: value for key, value in values.items() if value})


def reset_agent_run_context(token: Any) -> None:
    _agent_run_context.reset(token)


def current_agent_run_context() -> dict[str, str]:
    return dict(_agent_run_context.get())


def trace_attributes(**values: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for key, value in values.items():
        if key.lower() in _SENSITIVE_TRACE_ATTRIBUTE_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            attributes[key] = value
        else:
            attributes[key] = json.dumps(value, ensure_ascii=False, default=str)
    return attributes


def log_structured(level: int, event: str, **details: Any) -> None:
    span_context = trace.get_current_span().get_span_context()
    payload = {
        "event": event,
        "trace_id": format(span_context.trace_id, "032x") if span_context.is_valid else None,
        "span_id": format(span_context.span_id, "016x") if span_context.is_valid else None,
        **current_agent_run_context(),
        **details,
    }
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))


@contextmanager
def llm_span(*, model: str, operation: str, prompt: Any) -> Iterator[Any]:
    started = time.perf_counter()
    with tracer.start_as_current_span(f"llm.{operation}") as span:
        span.set_attributes(
            trace_attributes(
                **current_agent_run_context(),
                **{
                    "gen_ai.operation.name": operation,
                    "gen_ai.request.model": model,
                    "gen_ai.system": "openai_compatible",
                },
            )
        )
        if _capture_llm_content:
            span.set_attribute("gen_ai.input.messages", _safe_json(prompt))
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            raise
        finally:
            llm_call_duration.record(
                (time.perf_counter() - started) * 1000,
                trace_attributes(model=model, operation=operation),
            )


def record_llm_response(span: Any, response: Any) -> LLMUsage:
    usage = _extract_usage(response)
    model = str(getattr(response, "model", "unknown"))
    completion = _extract_completion(response)
    span.set_attributes(
        trace_attributes(
            **{
                "gen_ai.response.model": model,
                "gen_ai.usage.input_tokens": usage.input_tokens,
                "gen_ai.usage.output_tokens": usage.output_tokens,
                "gen_ai.usage.total_tokens": usage.total_tokens,
            }
        )
    )
    if _capture_llm_content:
        span.set_attribute("gen_ai.output.messages", _safe_json(completion))
    for token_type, value in (("input", usage.input_tokens), ("output", usage.output_tokens)):
        if value:
            llm_token_usage.add(value, trace_attributes(model=model, token_type=token_type))
    return usage


def instrument_openai_calls() -> None:
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions
    except ImportError:  # pragma: no cover
        return
    _wrap_openai_create(Completions, asynchronous=False)
    _wrap_openai_create(AsyncCompletions, asynchronous=True)


def _wrap_openai_create(target: type[Any], *, asynchronous: bool) -> None:
    original = target.create
    if getattr(original, "__omnisell_instrumented__", False):
        return
    if asynchronous:
        @wraps(original)
        async def async_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            model = str(kwargs.get("model", "unknown"))
            with llm_span(model=model, operation="chat", prompt=kwargs.get("messages", [])) as span:
                response = await original(self, *args, **kwargs)
                record_llm_response(span, response)
                return response
        async_wrapper.__omnisell_instrumented__ = True  # type: ignore[attr-defined]
        target.create = async_wrapper  # type: ignore[method-assign]
        return

    @wraps(original)
    def sync_wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        model = str(kwargs.get("model", "unknown"))
        with llm_span(model=model, operation="chat", prompt=kwargs.get("messages", [])) as span:
            response = original(self, *args, **kwargs)
            record_llm_response(span, response)
            return response
    sync_wrapper.__omnisell_instrumented__ = True  # type: ignore[attr-defined]
    target.create = sync_wrapper  # type: ignore[method-assign]


def _extract_usage(response: Any) -> LLMUsage:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or input_tokens + output_tokens)
    return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)


def _extract_completion(response: Any) -> Any:
    choices = getattr(response, "choices", None) or []
    return [getattr(getattr(choice, "message", None), "content", None) for choice in choices]


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:131072]


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            headers[key.strip()] = value.strip()
    return headers
