from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process configuration loaded once at the composition root."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"]
    service_name: str = Field(min_length=1, max_length=64)
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    metrics_enabled: bool = True
    worker_metrics_port: int = Field(default=9001, ge=1, le=65535)

    database_url: str = Field(min_length=1)
    database_worker_url: str = Field(min_length=1)
    database_admin_url: str | None = None

    synthetic_adapter_enabled: bool = False
    synthetic_adapter_token: SecretStr | None = None
    conversation_adapter_enabled: bool = False
    conversation_adapter_token: SecretStr | None = None

    ai_extraction_enabled: bool = False
    ai_extraction_provider: Literal["openai"] = "openai"
    openai_api_key: SecretStr | None = None
    ai_extraction_model: str = Field(default="gpt-5.6-terra", min_length=1, max_length=100)
    ai_extraction_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    ai_extraction_max_retries: int = Field(default=1, ge=0, le=3)
    ai_extraction_max_output_tokens: int = Field(default=4_000, ge=256, le=16_000)
    ai_extraction_context_max_messages: int = Field(default=12, ge=1, le=50)
    ai_extraction_context_max_characters: int = Field(default=12_000, ge=500, le=50_000)
    ai_extraction_summary_max_characters: int = Field(default=2_000, ge=100, le=10_000)
    ai_extraction_context_max_age_days: int = Field(default=30, ge=1, le=365)
    ai_extraction_auto_accept_confidence: Decimal = Field(default=Decimal("0.85"), ge=0, le=1)
    ai_extraction_provisional_confidence: Decimal = Field(default=Decimal("0.65"), ge=0, le=1)
    ai_extraction_confirmation_confidence: Decimal = Field(default=Decimal("0.40"), ge=0, le=1)
    ai_extraction_input_cost_per_million_usd: Decimal | None = Field(default=None, ge=0)
    ai_extraction_output_cost_per_million_usd: Decimal | None = Field(default=None, ge=0)
    ai_extraction_log_content: bool = False

    queue_poll_interval_ms: int = Field(default=250, ge=50, le=60_000)
    queue_lease_seconds: int = Field(default=30, ge=5, le=600)
    queue_max_attempts: int = Field(default=3, ge=1, le=10)
    conversation_processing_lease_seconds: int = Field(default=120, ge=10, le=900)
    conversation_processing_max_attempts: int = Field(default=3, ge=1, le=10)
    conversation_worker_batch_size: int = Field(default=10, ge=1, le=100)
    conversation_outbox_batch_size: int = Field(default=50, ge=1, le=500)
    conversation_outbox_lease_seconds: int = Field(default=30, ge=5, le=600)
    conversation_outbox_max_attempts: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_security_boundaries(self) -> Self:
        database_urls = [self.database_url, self.database_worker_url]
        if self.database_admin_url is not None:
            database_urls.append(self.database_admin_url)
        if any(not url.startswith("postgresql+psycopg://") for url in database_urls):
            raise ValueError("database URLs must use the postgresql+psycopg driver")

        if self.synthetic_adapter_enabled:
            if self.app_env == "production":
                raise ValueError("the synthetic adapter cannot be enabled in production")
            if self.synthetic_adapter_token is None:
                raise ValueError("SYNTHETIC_ADAPTER_TOKEN is required when the adapter is enabled")
            if len(self.synthetic_adapter_token.get_secret_value()) < 24:
                raise ValueError("SYNTHETIC_ADAPTER_TOKEN must contain at least 24 characters")

        if self.conversation_adapter_enabled:
            if self.app_env == "production":
                raise ValueError("the conversation adapter cannot be enabled in production")
            if self.conversation_adapter_token is None:
                raise ValueError(
                    "CONVERSATION_ADAPTER_TOKEN is required when the adapter is enabled"
                )
            if len(self.conversation_adapter_token.get_secret_value()) < 24:
                raise ValueError("CONVERSATION_ADAPTER_TOKEN must contain at least 24 characters")

        if self.ai_extraction_enabled and self.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is required when AI extraction is enabled")
        if self.app_env == "production" and self.ai_extraction_log_content:
            raise ValueError("AI extraction content logging cannot be enabled in production")
        if not (
            self.ai_extraction_confirmation_confidence
            <= self.ai_extraction_provisional_confidence
            <= self.ai_extraction_auto_accept_confidence
        ):
            raise ValueError(
                "AI extraction confidence thresholds must satisfy confirmation <= provisional "
                "<= auto-accept"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()


class MigrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_migration_url: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_driver(self) -> Self:
        if not self.database_migration_url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_MIGRATION_URL must use the postgresql+psycopg driver")
        return self
