from __future__ import annotations

from typing import Any

from model_gateway.gateway import FailoverModelGateway, ModelGateway, OpenAICompatibleGateway


def create_configured_gateway(settings: Any) -> ModelGateway:
    primary = OpenAICompatibleGateway(
        base_url=settings.vllm_base_url,
        api_key=settings.vllm_api_key,
        default_model=settings.vllm_model_name,
        provider_name=settings.model_provider,
        max_tokens_per_task=settings.model_max_tokens_per_task,
        timeout=settings.model_timeout_seconds,
        input_cost_per_million=settings.model_input_cost_per_million,
        output_cost_per_million=settings.model_output_cost_per_million,
    )
    if not settings.model_fallback_enabled:
        return primary
    if not settings.model_fallback_model_name or not settings.model_fallback_api_key:
        raise ValueError(
            "MODEL_FALLBACK_MODEL_NAME and MODEL_FALLBACK_API_KEY are required when MODEL_FALLBACK_ENABLED is true"
        )
    fallback = OpenAICompatibleGateway(
        base_url=settings.model_fallback_base_url,
        api_key=settings.model_fallback_api_key,
        default_model=settings.model_fallback_model_name,
        provider_name=settings.model_fallback_provider,
        max_tokens_per_task=settings.model_max_tokens_per_task,
        timeout=settings.model_fallback_timeout_seconds,
        input_cost_per_million=settings.model_fallback_input_cost_per_million,
        output_cost_per_million=settings.model_fallback_output_cost_per_million,
    )
    return FailoverModelGateway(primary=primary, fallback=fallback)
