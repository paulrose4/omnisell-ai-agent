from __future__ import annotations

import os
import warnings
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from api.release_profiles import ReleaseProfile, normalize_release_profile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER_MARKERS = ("change-me", "replace-", "generate-")
_LEGACY_MODEL_WARNING_EMITTED = False
LEGACY_MODEL_CONFIG_REMOVAL_VERSION = "2.0.0"


def _default_agent_budget_profiles() -> dict[str, dict[str, Any]]:
    return {
        "operations_brief_analysis": {
            "max_prompt_tokens": 9000,
            "max_completion_tokens": 5000,
            "max_estimated_cost": "0.12",
            "max_evidence_items": 14,
            "max_evidence_chars": 6000,
            "max_total_evidence_chars": 24000,
            "max_request_bytes": 64000,
            "timeout_seconds": 240,
        },
        "customer_case_analysis": {
            "max_prompt_tokens": 7000,
            "max_completion_tokens": 5000,
            "max_estimated_cost": "0.10",
            "max_evidence_items": 80,
            "max_evidence_chars": 6000,
            "max_total_evidence_chars": 18000,
            "max_request_bytes": 56000,
            "timeout_seconds": 180,
        },
        "listing_optimization_analysis": {
            "max_prompt_tokens": 6000,
            "max_completion_tokens": 4000,
            "max_estimated_cost": "0.08",
            "max_evidence_items": 100,
            "max_evidence_chars": 8000,
            "max_total_evidence_chars": 20000,
            "max_request_bytes": 48000,
            "timeout_seconds": 120,
        },
        "reconciliation_investigation": {
            "max_prompt_tokens": 8000,
            "max_completion_tokens": 5000,
            "max_estimated_cost": "0.11",
            "max_evidence_items": 150,
            "max_evidence_chars": 6000,
            "max_total_evidence_chars": 26000,
            "max_request_bytes": 64000,
            "timeout_seconds": 240,
        },
    }


def _is_placeholder(value: str | None) -> bool:
    normalized = (value or "").lower()
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "OmniSell AI API"
    environment: str = "development"
    release_profile: ReleaseProfile = Field(
        default=ReleaseProfile.FULL_DEVELOPMENT,
        validation_alias=AliasChoices("release_profile", "RELEASE_PROFILE"),
    )
    api_key: str = Field(default="dev_api_key_12345", validation_alias=AliasChoices("api_key", "API_KEY"))
    auth_mode: str = Field(default="api_key", validation_alias=AliasChoices("auth_mode", "AUTH_MODE"))
    oidc_issuer: str = Field(default="", validation_alias=AliasChoices("oidc_issuer", "OIDC_ISSUER"))
    oidc_audience: str = Field(default="omnisell-api", validation_alias=AliasChoices("oidc_audience", "OIDC_AUDIENCE"))
    oidc_jwks_url: str = Field(default="", validation_alias=AliasChoices("oidc_jwks_url", "OIDC_JWKS_URL"))
    oidc_algorithms: str = Field(default="RS256", validation_alias=AliasChoices("oidc_algorithms", "OIDC_ALGORITHMS"))
    oidc_require_mfa: bool = Field(default=True, validation_alias=AliasChoices("oidc_require_mfa", "OIDC_REQUIRE_MFA"))
    oidc_mfa_amr_values: str = Field(
        default="mfa,otp", validation_alias=AliasChoices("oidc_mfa_amr_values", "OIDC_MFA_AMR_VALUES")
    )
    oidc_required_acr: str = Field(default="", validation_alias=AliasChoices("oidc_required_acr", "OIDC_REQUIRED_ACR"))
    allowed_origins: str = Field(
        default="http://localhost:3000", validation_alias=AliasChoices("allowed_origins", "ALLOWED_ORIGINS")
    )

    database_url: str = Field(
        default="sqlite:///./tmp/omnisell.db", validation_alias=AliasChoices("database_url", "DATABASE_URL")
    )
    migration_database_url: str = Field(
        default="", validation_alias=AliasChoices("migration_database_url", "MIGRATION_DATABASE_URL")
    )
    auto_create_schema: bool = Field(
        default=True, validation_alias=AliasChoices("auto_create_schema", "AUTO_CREATE_SCHEMA")
    )
    task_backend: str = Field(default="memory", validation_alias=AliasChoices("task_backend", "TASK_BACKEND"))
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias=AliasChoices("redis_url", "REDIS_URL"))
    celery_task_always_eager: bool = Field(
        default=False, validation_alias=AliasChoices("celery_task_always_eager", "CELERY_TASK_ALWAYS_EAGER")
    )
    customer_case_cluster_minimum_samples: int = Field(
        default=3,
        ge=2,
        le=100,
        validation_alias=AliasChoices("customer_case_cluster_minimum_samples", "CUSTOMER_CASE_CLUSTER_MINIMUM_SAMPLES"),
    )
    customer_case_cluster_window_days: int = Field(
        default=7,
        ge=1,
        le=90,
        validation_alias=AliasChoices("customer_case_cluster_window_days", "CUSTOMER_CASE_CLUSTER_WINDOW_DAYS"),
    )

    model_base_url: str = Field(
        default="http://localhost:11434/v1", validation_alias=AliasChoices("model_base_url", "MODEL_BASE_URL")
    )
    model_name: str = Field(default="qwen2.5:3b", validation_alias=AliasChoices("model_name", "MODEL_NAME"))
    model_api_key: str = Field(default="not-needed", validation_alias=AliasChoices("model_api_key", "MODEL_API_KEY"))
    legacy_vllm_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("vllm_base_url", "VLLM_BASE_URL"), exclude=True, repr=False
    )
    legacy_vllm_model_name: str | None = Field(
        default=None, validation_alias=AliasChoices("vllm_model_name", "VLLM_MODEL_NAME"), exclude=True, repr=False
    )
    legacy_vllm_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("vllm_api_key", "VLLM_API_KEY"), exclude=True, repr=False
    )
    model_fallback_enabled: bool = Field(
        default=False, validation_alias=AliasChoices("model_fallback_enabled", "MODEL_FALLBACK_ENABLED")
    )
    model_fallback_base_url: str = Field(
        default="https://api.wenrugouai.com/v1",
        validation_alias=AliasChoices("model_fallback_base_url", "MODEL_FALLBACK_BASE_URL"),
    )
    model_fallback_model_name: str = Field(
        default="", validation_alias=AliasChoices("model_fallback_model_name", "MODEL_FALLBACK_MODEL_NAME")
    )
    model_fallback_api_key: str = Field(
        default="", validation_alias=AliasChoices("model_fallback_api_key", "MODEL_FALLBACK_API_KEY")
    )
    model_fallback_provider: str = Field(
        default="wenrugouai_openai_compatible",
        validation_alias=AliasChoices("model_fallback_provider", "MODEL_FALLBACK_PROVIDER"),
    )
    model_fallback_timeout_seconds: float = Field(
        default=240.0,
        gt=0,
        validation_alias=AliasChoices("model_fallback_timeout_seconds", "MODEL_FALLBACK_TIMEOUT_SECONDS"),
    )
    model_fallback_input_cost_per_million: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        validation_alias=AliasChoices("model_fallback_input_cost_per_million", "MODEL_FALLBACK_INPUT_COST_PER_MILLION"),
    )
    model_fallback_output_cost_per_million: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        validation_alias=AliasChoices(
            "model_fallback_output_cost_per_million", "MODEL_FALLBACK_OUTPUT_COST_PER_MILLION"
        ),
    )
    model_fallback_pricing_version: str = Field(
        default="development-fallback-pricing-v1",
        validation_alias=AliasChoices("model_fallback_pricing_version", "MODEL_FALLBACK_PRICING_VERSION"),
    )
    model_fallback_pricing_effective_at: datetime = Field(
        default=datetime(2026, 1, 1, tzinfo=UTC),
        validation_alias=AliasChoices("model_fallback_pricing_effective_at", "MODEL_FALLBACK_PRICING_EFFECTIVE_AT"),
    )
    model_fallback_bill_on_unknown_failure: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "model_fallback_bill_on_unknown_failure", "MODEL_FALLBACK_BILL_ON_UNKNOWN_FAILURE"
        ),
    )

    vision_base_url: str = Field(
        default="http://localhost:11434", validation_alias=AliasChoices("vision_base_url", "VISION_BASE_URL")
    )
    vision_model_name: str = Field(
        default="llava:7b", validation_alias=AliasChoices("vision_model_name", "VISION_MODEL_NAME")
    )
    vision_timeout_seconds: float = Field(
        default=180.0, validation_alias=AliasChoices("vision_timeout_seconds", "VISION_TIMEOUT_SECONDS")
    )
    vision_max_image_bytes: int = Field(
        default=8388608, validation_alias=AliasChoices("vision_max_image_bytes", "VISION_MAX_IMAGE_BYTES")
    )

    chroma_host: str = Field(default="localhost", validation_alias=AliasChoices("chroma_host", "CHROMA_HOST"))
    chroma_port: int = Field(default=8001, validation_alias=AliasChoices("chroma_port", "CHROMA_PORT"))
    chroma_collection: str = Field(
        default="omnisell_customer_kb", validation_alias=AliasChoices("chroma_collection", "CHROMA_COLLECTION")
    )

    mock_platform_url: str = Field(
        default="http://localhost:8002", validation_alias=AliasChoices("mock_platform_url", "MOCK_PLATFORM_URL")
    )
    oauth_client_id: str = Field(
        default="omnisell_agent", validation_alias=AliasChoices("oauth_client_id", "OAUTH_CLIENT_ID")
    )
    oauth_client_secret: str = Field(
        default="dev_secret_key_12345", validation_alias=AliasChoices("oauth_client_secret", "OAUTH_CLIENT_SECRET")
    )
    oauth_redirect_uri: str = Field(
        default="http://localhost:3000/oauth/callback",
        validation_alias=AliasChoices("oauth_redirect_uri", "OAUTH_REDIRECT_URI"),
    )

    sandbox_image: str = Field(
        default="omnisell-sandbox:1.0.0", validation_alias=AliasChoices("sandbox_image", "SANDBOX_IMAGE")
    )
    sandbox_timeout_seconds: int = Field(
        default=30, validation_alias=AliasChoices("sandbox_timeout_seconds", "SANDBOX_TIMEOUT_SECONDS")
    )

    rate_limit_per_minute: int = Field(
        default=60, validation_alias=AliasChoices("rate_limit_per_minute", "RATE_LIMIT_PER_MINUTE")
    )
    text_queue_workers: int = Field(
        default=2, validation_alias=AliasChoices("text_queue_workers", "TEXT_QUEUE_WORKERS")
    )
    vision_queue_workers: int = Field(
        default=1, validation_alias=AliasChoices("vision_queue_workers", "VISION_QUEUE_WORKERS")
    )
    audit_log_path: str = Field(
        default=str(PROJECT_ROOT / "logs" / "audit.jsonl"),
        validation_alias=AliasChoices("audit_log_path", "AUDIT_LOG_PATH"),
    )

    otel_service_name: str = Field(
        default="omnisell-api", validation_alias=AliasChoices("otel_service_name", "OTEL_SERVICE_NAME")
    )
    otel_exporter_otlp_endpoint: str = Field(
        default="", validation_alias=AliasChoices("otel_exporter_otlp_endpoint", "OTEL_EXPORTER_OTLP_ENDPOINT")
    )
    otel_exporter_otlp_headers: str = Field(
        default="", validation_alias=AliasChoices("otel_exporter_otlp_headers", "OTEL_EXPORTER_OTLP_HEADERS")
    )
    telemetry_capture_llm_content: bool = Field(
        default=False, validation_alias=AliasChoices("telemetry_capture_llm_content", "TELEMETRY_CAPTURE_LLM_CONTENT")
    )

    amazon_lwa_client_id: str = Field(
        default="", validation_alias=AliasChoices("amazon_lwa_client_id", "AMAZON_LWA_CLIENT_ID")
    )
    amazon_lwa_client_secret: str = Field(
        default="", validation_alias=AliasChoices("amazon_lwa_client_secret", "AMAZON_LWA_CLIENT_SECRET")
    )
    amazon_lwa_token_url: str = Field(
        default="https://api.amazon.com/auth/o2/token",
        validation_alias=AliasChoices("amazon_lwa_token_url", "AMAZON_LWA_TOKEN_URL"),
    )
    amazon_lwa_revoke_url: str = Field(
        default="https://api.amazon.com/auth/o2/token/revoke",
        validation_alias=AliasChoices("amazon_lwa_revoke_url", "AMAZON_LWA_REVOKE_URL"),
    )
    amazon_application_id: str = Field(
        default="", validation_alias=AliasChoices("amazon_application_id", "AMAZON_APPLICATION_ID")
    )
    amazon_redirect_uri: str = Field(
        default="", validation_alias=AliasChoices("amazon_redirect_uri", "AMAZON_REDIRECT_URI")
    )
    amazon_sp_api_endpoint: str = Field(
        default="https://sellingpartnerapi-na.amazon.com",
        validation_alias=AliasChoices("amazon_sp_api_endpoint", "AMAZON_SP_API_ENDPOINT"),
    )
    amazon_seller_central_url: str = Field(
        default="https://sellercentral.amazon.com",
        validation_alias=AliasChoices("amazon_seller_central_url", "AMAZON_SELLER_CENTRAL_URL"),
    )
    amazon_region: str = Field(default="na", validation_alias=AliasChoices("amazon_region", "AMAZON_REGION"))
    amazon_marketplace_ids: str = Field(
        default="ATVPDKIKX0DER", validation_alias=AliasChoices("amazon_marketplace_ids", "AMAZON_MARKETPLACE_IDS")
    )
    amazon_writes_enabled: bool = Field(
        default=False, validation_alias=AliasChoices("amazon_writes_enabled", "AMAZON_WRITES_ENABLED")
    )
    amazon_pilot_read_only: bool = Field(
        default=True, validation_alias=AliasChoices("amazon_pilot_read_only", "AMAZON_PILOT_READ_ONLY")
    )
    amazon_notification_token: str = Field(
        default="", validation_alias=AliasChoices("amazon_notification_token", "AMAZON_NOTIFICATION_TOKEN")
    )
    mock_drill_enabled: bool = Field(
        default=False, validation_alias=AliasChoices("mock_drill_enabled", "MOCK_DRILL_ENABLED")
    )
    mock_amazon_rate_limit_multiplier: float = Field(
        default=1.0,
        ge=1,
        le=100000,
        validation_alias=AliasChoices("mock_amazon_rate_limit_multiplier", "MOCK_AMAZON_RATE_LIMIT_MULTIPLIER"),
    )
    secret_encryption_key: str = Field(
        default="", validation_alias=AliasChoices("secret_encryption_key", "SECRET_ENCRYPTION_KEY")
    )

    model_provider: str = Field(
        default="openai_compatible", validation_alias=AliasChoices("model_provider", "MODEL_PROVIDER")
    )
    model_max_tokens_per_task: int = Field(
        default=36000, gt=0, validation_alias=AliasChoices("model_max_tokens_per_task", "MODEL_MAX_TOKENS_PER_TASK")
    )
    model_max_tool_calls: int = Field(
        default=8, validation_alias=AliasChoices("model_max_tool_calls", "MODEL_MAX_TOOL_CALLS")
    )
    model_timeout_seconds: float = Field(
        default=240.0, gt=0, validation_alias=AliasChoices("model_timeout_seconds", "MODEL_TIMEOUT_SECONDS")
    )
    model_input_cost_per_million: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        validation_alias=AliasChoices("model_input_cost_per_million", "MODEL_INPUT_COST_PER_MILLION"),
    )
    model_output_cost_per_million: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        validation_alias=AliasChoices("model_output_cost_per_million", "MODEL_OUTPUT_COST_PER_MILLION"),
    )
    model_pricing_version: str = Field(
        default="development-pricing-v1",
        validation_alias=AliasChoices("model_pricing_version", "MODEL_PRICING_VERSION"),
    )
    model_pricing_effective_at: datetime = Field(
        default=datetime(2026, 1, 1, tzinfo=UTC),
        validation_alias=AliasChoices("model_pricing_effective_at", "MODEL_PRICING_EFFECTIVE_AT"),
    )
    model_bill_on_unknown_failure: bool = Field(
        default=True, validation_alias=AliasChoices("model_bill_on_unknown_failure", "MODEL_BILL_ON_UNKNOWN_FAILURE")
    )
    agent_tenant_quota_per_minute: int = Field(
        default=10,
        gt=0,
        validation_alias=AliasChoices("agent_tenant_quota_per_minute", "AGENT_TENANT_QUOTA_PER_MINUTE"),
    )
    agent_tenant_quota_per_day: int = Field(
        default=200, gt=0, validation_alias=AliasChoices("agent_tenant_quota_per_day", "AGENT_TENANT_QUOTA_PER_DAY")
    )
    agent_budget_profiles: dict[str, dict[str, Any]] = Field(
        default_factory=_default_agent_budget_profiles,
        validation_alias=AliasChoices("agent_budget_profiles", "AGENT_BUDGET_PROFILES"),
    )

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def oidc_algorithm_list(self) -> list[str]:
        return [item.strip() for item in self.oidc_algorithms.split(",") if item.strip()]

    @property
    def oidc_mfa_amr_list(self) -> list[str]:
        return [item.strip().lower() for item in self.oidc_mfa_amr_values.split(",") if item.strip()]

    @property
    def amazon_marketplaces(self) -> list[str]:
        return [item.strip() for item in self.amazon_marketplace_ids.split(",") if item.strip()]

    @property
    def alembic_database_url(self) -> str:
        return self.migration_database_url or self.database_url

    @model_validator(mode="after")
    def validate_production_safety(self):
        legacy_pairs = (
            ("model_base_url", "MODEL_BASE_URL", "legacy_vllm_base_url", "VLLM_BASE_URL"),
            ("model_name", "MODEL_NAME", "legacy_vllm_model_name", "VLLM_MODEL_NAME"),
            ("model_api_key", "MODEL_API_KEY", "legacy_vllm_api_key", "VLLM_API_KEY"),
        )
        legacy_used = False
        for current_field, current_env, legacy_field, legacy_env in legacy_pairs:
            legacy_value = getattr(self, legacy_field)
            if legacy_value is None:
                continue
            legacy_used = True
            current_explicit = current_field in self.model_fields_set or os.getenv(current_env) is not None
            current_value = getattr(self, current_field)
            if current_explicit and current_value != legacy_value:
                raise ValueError(f"{current_env} conflicts with deprecated {legacy_env}")
            if not current_explicit:
                setattr(self, current_field, legacy_value)
        if legacy_used:
            global _LEGACY_MODEL_WARNING_EMITTED
            if not _LEGACY_MODEL_WARNING_EMITTED:
                warnings.warn(
                    "VLLM_* model configuration is deprecated and will be removed in "
                    f"{LEGACY_MODEL_CONFIG_REMOVAL_VERSION}; migrate to MODEL_BASE_URL, MODEL_NAME, and MODEL_API_KEY.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                _LEGACY_MODEL_WARNING_EMITTED = True
        if self.mock_amazon_rate_limit_multiplier != 1:
            mock_host = (urlparse(self.amazon_sp_api_endpoint).hostname or "").lower()
            if (
                self.environment.lower() == "production"
                or not self.mock_drill_enabled
                or mock_host not in {"mock-cloud", "localhost", "127.0.0.1"}
            ):
                raise ValueError(
                    "MOCK_AMAZON_RATE_LIMIT_MULTIPLIER can only exceed 1 in a non-production isolated Mock Amazon drill"
                )
        if self.agent_tenant_quota_per_day < self.agent_tenant_quota_per_minute:
            raise ValueError("AGENT_TENANT_QUOTA_PER_DAY must be at least AGENT_TENANT_QUOTA_PER_MINUTE")
        required_model_token_budget = max(
            (int(profile["max_prompt_tokens"]) * 2) + (int(profile["max_completion_tokens"]) * 3) + 512
            for profile in self.agent_budget_profiles.values()
        )
        if self.model_max_tokens_per_task < required_model_token_budget:
            raise ValueError(
                "MODEL_MAX_TOKENS_PER_TASK must reserve the worst-case initial generation and one schema repair"
            )
        for workflow_type, profile in self.agent_budget_profiles.items():
            prompt_tokens = Decimal(str(profile["max_prompt_tokens"]))
            completion_tokens = Decimal(str(profile["max_completion_tokens"]))
            repair_prompt_tokens = prompt_tokens + completion_tokens + Decimal("512")
            worst_case_repair_cost = (
                (prompt_tokens + repair_prompt_tokens) * self.model_input_cost_per_million
                + (completion_tokens * 2) * self.model_output_cost_per_million
            ) / Decimal("1000000")
            if self.model_fallback_enabled:
                worst_case_repair_cost += (
                    (prompt_tokens + repair_prompt_tokens) * self.model_fallback_input_cost_per_million
                    + (completion_tokens * 2) * self.model_fallback_output_cost_per_million
                ) / Decimal("1000000")
            if worst_case_repair_cost > Decimal(str(profile["max_estimated_cost"])):
                raise ValueError(
                    f"{workflow_type} max_estimated_cost cannot cover worst-case schema repair and failover output "
                    f"at the configured model token rates"
                )
        if self.environment.lower() == "production":
            if self.release_profile is not ReleaseProfile.READ_ONLY_PILOT:
                raise ValueError("Production RELEASE_PROFILE must be read_only_pilot")
            if self.auth_mode != "oidc":
                raise ValueError("Production requires OIDC authentication; API key and hybrid modes are disabled")
            if not self.oidc_issuer or not self.oidc_jwks_url:
                raise ValueError("Production OIDC issuer and JWKS URL are required")
            if any(urlparse(value).scheme != "https" for value in (self.oidc_issuer, self.oidc_jwks_url)):
                raise ValueError("Production OIDC issuer and JWKS URL must use HTTPS")
            if not self.oidc_require_mfa:
                raise ValueError("Production OIDC authentication must require MFA")
            origins = self.cors_origins
            if not origins or any(
                urlparse(origin).scheme != "https"
                or urlparse(origin).hostname in {None, "localhost", "127.0.0.1"}
                or "*" in origin
                for origin in origins
            ):
                raise ValueError("Production ALLOWED_ORIGINS must contain explicit HTTPS origins")
            application_database = urlparse(self.database_url)
            migration_database = urlparse(self.migration_database_url)
            if not application_database.scheme.startswith("postgresql"):
                raise ValueError("Production DATABASE_URL must use PostgreSQL")
            if not self.migration_database_url or not migration_database.scheme.startswith("postgresql"):
                raise ValueError("Production MIGRATION_DATABASE_URL must use PostgreSQL")
            if not application_database.username or application_database.username == migration_database.username:
                raise ValueError("Production application and migration database roles must be distinct")
            if not application_database.password or _is_placeholder(application_database.password):
                raise ValueError("Production DATABASE_URL must include a non-placeholder password")
            if not migration_database.password or _is_placeholder(migration_database.password):
                raise ValueError("Production MIGRATION_DATABASE_URL must include a non-placeholder password")
            redis = urlparse(self.redis_url)
            if redis.scheme not in {"redis", "rediss"} or not redis.password or _is_placeholder(redis.password):
                raise ValueError("Production REDIS_URL must include authentication")
            try:
                Fernet(self.secret_encryption_key.encode())
            except (TypeError, ValueError) as exc:
                raise ValueError("Production SECRET_ENCRYPTION_KEY must be a valid Fernet key") from exc
            if len(self.amazon_notification_token) < 32 or _is_placeholder(self.amazon_notification_token):
                raise ValueError("Production AMAZON_NOTIFICATION_TOKEN must be a strong random token")
            if self.auto_create_schema:
                raise ValueError("Production AUTO_CREATE_SCHEMA must be false")
            if self.task_backend != "celery":
                raise ValueError("Production TASK_BACKEND must be celery")
            if self.mock_drill_enabled:
                raise ValueError("Production MOCK_DRILL_ENABLED must be false")
            if self.telemetry_capture_llm_content:
                raise ValueError("Production LLM content telemetry is disabled to protect business and customer data")
            if self.model_input_cost_per_million <= 0 or self.model_output_cost_per_million <= 0:
                raise ValueError("Production model input and output cost rates must be greater than zero")
            if not self.model_pricing_version.strip() or _is_placeholder(self.model_pricing_version):
                raise ValueError("Production MODEL_PRICING_VERSION must identify the approved price schedule")
            if not self.model_bill_on_unknown_failure:
                raise ValueError("Production model budgeting must reserve unknown provider failures as billable")
            if self.model_fallback_enabled:
                if urlparse(self.model_fallback_base_url).scheme != "https":
                    raise ValueError("Production model fallback endpoint must use HTTPS")
                if not self.model_fallback_model_name or _is_placeholder(self.model_fallback_model_name):
                    raise ValueError("Production MODEL_FALLBACK_MODEL_NAME must be configured")
                if not self.model_fallback_api_key or _is_placeholder(self.model_fallback_api_key):
                    raise ValueError("Production MODEL_FALLBACK_API_KEY must be configured")
                if self.model_fallback_input_cost_per_million <= 0 or self.model_fallback_output_cost_per_million <= 0:
                    raise ValueError("Production fallback model cost rates must be greater than zero")
                if not self.model_fallback_pricing_version.strip() or _is_placeholder(
                    self.model_fallback_pricing_version
                ):
                    raise ValueError(
                        "Production MODEL_FALLBACK_PRICING_VERSION must identify the approved price schedule"
                    )
                if not self.model_fallback_bill_on_unknown_failure:
                    raise ValueError("Production fallback budgeting must reserve unknown provider failures as billable")
            if self.amazon_writes_enabled or not self.amazon_pilot_read_only:
                raise ValueError("Current production release requires Amazon read-only pilot mode with writes disabled")
        return self

    @field_validator("auth_mode")
    @classmethod
    def validate_auth_mode(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"api_key", "oidc", "hybrid"}:
            raise ValueError("AUTH_MODE must be api_key, oidc, or hybrid")
        return normalized

    @field_validator("release_profile", mode="before")
    @classmethod
    def validate_release_profile(cls, value: object) -> ReleaseProfile:
        return normalize_release_profile(value)

    @field_validator("task_backend")
    @classmethod
    def validate_task_backend(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"memory", "celery"}:
            raise ValueError("TASK_BACKEND must be memory or celery")
        return normalized

    @field_validator("model_pricing_effective_at", "model_fallback_pricing_effective_at")
    @classmethod
    def validate_pricing_effective_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Model pricing effective timestamps must be timezone-aware")
        return value

    @field_validator("agent_budget_profiles")
    @classmethod
    def validate_agent_budget_profiles(cls, value: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        required_workflows = set(_default_agent_budget_profiles())
        missing_workflows = required_workflows - set(value)
        if missing_workflows:
            raise ValueError(f"AGENT_BUDGET_PROFILES is missing workflows: {', '.join(sorted(missing_workflows))}")
        integer_fields = (
            "max_prompt_tokens",
            "max_completion_tokens",
            "max_evidence_items",
            "max_evidence_chars",
            "max_total_evidence_chars",
            "max_request_bytes",
            "timeout_seconds",
        )
        for workflow_type in required_workflows:
            profile = value[workflow_type]
            missing_fields = {*integer_fields, "max_estimated_cost"} - set(profile)
            if missing_fields:
                raise ValueError(f"{workflow_type} budget is missing: {', '.join(sorted(missing_fields))}")
            if any(int(profile[field]) <= 0 for field in integer_fields):
                raise ValueError(f"{workflow_type} budget values must be positive")
            if Decimal(str(profile["max_estimated_cost"])) <= 0:
                raise ValueError(f"{workflow_type} max_estimated_cost must be positive")
            if int(profile["max_total_evidence_chars"]) < int(profile["max_evidence_chars"]):
                raise ValueError(f"{workflow_type} total evidence budget must fit one evidence item")
            if int(profile["max_request_bytes"]) <= int(profile["max_total_evidence_chars"]):
                raise ValueError(f"{workflow_type} request budget must exceed total evidence budget")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached API settings."""
    return Settings()


if __name__ == "__main__":
    print(get_settings().model_dump())
