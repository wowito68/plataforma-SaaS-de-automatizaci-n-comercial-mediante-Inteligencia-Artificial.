from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from saas_platform.config import MigrationSettings, Settings

BASE = {
    "app_env": "test",
    "service_name": "test",
    "database_url": "postgresql+psycopg://runtime:test@localhost/test",
    "database_worker_url": "postgresql+psycopg://worker:test@localhost/test",
}


@pytest.fixture(autouse=True)
def isolate_settings_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for model in (Settings, MigrationSettings):
        for field_name in model.model_fields:
            monkeypatch.delenv(field_name.upper(), raising=False)


def test_required_configuration_fails_explicitly() -> None:
    with pytest.raises(PydanticValidationError):
        Settings.model_validate({})


def test_non_psycopg_database_url_is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match=r"postgresql\+psycopg"):
        Settings.model_validate(BASE | {"database_url": "sqlite:///test.db"})


def test_enabled_adapter_requires_a_long_token() -> None:
    with pytest.raises(PydanticValidationError, match="at least 24"):
        Settings.model_validate(
            BASE | {"synthetic_adapter_enabled": True, "synthetic_adapter_token": "too-short"}
        )


def test_enabled_adapter_requires_a_token() -> None:
    with pytest.raises(PydanticValidationError, match="TOKEN is required"):
        Settings.model_validate(BASE | {"synthetic_adapter_enabled": True})


def test_production_forbids_synthetic_adapter() -> None:
    with pytest.raises(PydanticValidationError, match="cannot be enabled"):
        Settings.model_validate(
            BASE
            | {
                "app_env": "production",
                "synthetic_adapter_enabled": True,
                "synthetic_adapter_token": "a-valid-token-with-24-characters",
            }
        )


def test_migration_configuration_validates_its_driver() -> None:
    valid = MigrationSettings.model_validate(
        {"database_migration_url": "postgresql+psycopg://owner:test@localhost/test"}
    )

    assert valid.database_migration_url.startswith("postgresql+psycopg://")
    with pytest.raises(PydanticValidationError, match="DATABASE_MIGRATION_URL"):
        MigrationSettings.model_validate({"database_migration_url": "sqlite:///test.db"})


def test_ai_extraction_is_disabled_and_secretless_by_default() -> None:
    settings = Settings.model_validate(BASE)

    assert settings.ai_extraction_enabled is False
    assert settings.openai_api_key is None
    assert settings.ai_extraction_model == "gpt-5.6-terra"


def test_enabled_ai_extraction_requires_only_its_own_secret() -> None:
    with pytest.raises(PydanticValidationError, match="OPENAI_API_KEY"):
        Settings.model_validate(BASE | {"ai_extraction_enabled": True})

    settings = Settings.model_validate(
        BASE
        | {
            "ai_extraction_enabled": True,
            "openai_api_key": "test-key-not-real",  # pragma: allowlist secret
        }
    )
    assert settings.openai_api_key is not None
    assert (
        settings.openai_api_key.get_secret_value() == "test-key-not-real"
    )  # pragma: allowlist secret


def test_confidence_thresholds_must_be_ordered() -> None:
    with pytest.raises(PydanticValidationError, match="confirmation <= provisional"):
        Settings.model_validate(
            BASE
            | {
                "ai_extraction_confirmation_confidence": "0.8",
                "ai_extraction_provisional_confidence": "0.6",
            }
        )


def test_production_forbids_ai_conversation_content_logging() -> None:
    with pytest.raises(PydanticValidationError, match="content logging"):
        Settings.model_validate(
            BASE
            | {
                "app_env": "production",
                "ai_extraction_log_content": True,
            }
        )
