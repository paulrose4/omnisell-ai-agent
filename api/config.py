from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    api_key: str = Field(default="dev_api_key_12345", validation_alias="API_KEY")

    vllm_base_url: str = Field(default="http://localhost:11434/v1", validation_alias="VLLM_BASE_URL")
    vllm_model_name: str = Field(default="qwen2.5:3b", validation_alias="VLLM_MODEL_NAME")
    vllm_api_key: str = Field(default="not-needed", validation_alias="VLLM_API_KEY")

    chroma_host: str = Field(default="localhost", validation_alias="CHROMA_HOST")
    chroma_port: int = Field(default=8001, validation_alias="CHROMA_PORT")
    chroma_collection: str = Field(default="omnisell_customer_kb", validation_alias="CHROMA_COLLECTION")

    mock_platform_url: str = Field(default="http://localhost:8002", validation_alias="MOCK_PLATFORM_URL")
    oauth_client_id: str = Field(default="omnisell_agent", validation_alias="OAUTH_CLIENT_ID")
    oauth_client_secret: str = Field(default="dev_secret_key_12345", validation_alias="OAUTH_CLIENT_SECRET")
    oauth_redirect_uri: str = Field(default="http://localhost:3000/oauth/callback", validation_alias="OAUTH_REDIRECT_URI")

    sandbox_image: str = Field(default="omnisell-sandbox:latest", validation_alias="SANDBOX_IMAGE")
    sandbox_timeout_seconds: int = Field(default=30, validation_alias="SANDBOX_TIMEOUT_SECONDS")

    rate_limit_per_minute: int = Field(default=60, validation_alias="RATE_LIMIT_PER_MINUTE")
    audit_log_path: str = Field(default=str(PROJECT_ROOT / "logs" / "audit.jsonl"), validation_alias="AUDIT_LOG_PATH")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached API settings."""
    return Settings()


if __name__ == "__main__":
    print(get_settings().model_dump())
