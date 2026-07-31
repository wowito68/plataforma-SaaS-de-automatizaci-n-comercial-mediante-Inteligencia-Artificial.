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

    queue_poll_interval_ms: int = Field(default=250, ge=50, le=60_000)
    queue_lease_seconds: int = Field(default=30, ge=5, le=600)
    queue_max_attempts: int = Field(default=3, ge=1, le=10)

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
