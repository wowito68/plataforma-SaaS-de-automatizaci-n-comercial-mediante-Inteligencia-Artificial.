from typing import Any

import pytest

from saas_platform.config import Settings
from saas_platform.modules.telecom_extraction.composition import (
    build_openai_extraction_use_case,
)
from saas_platform.modules.telecom_extraction.scripted import (
    ScriptedCommercialExtractionAdapter,
)
from saas_platform.modules.telecom_extraction.telemetry import NullExtractionTelemetry
from tests.extraction_fixtures import (
    EXECUTION_ID,
    NOW,
    TelemetrySpy,
    base_payload,
    make_command,
    provider_response,
)
from tests.fakes import FixedIdGenerator, MutableClock


def settings(**changes: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "service_name": "test",
        "database_url": "postgresql+psycopg://localhost/test",
        "database_worker_url": "postgresql+psycopg://localhost/test",
        "ai_extraction_enabled": True,
        "openai_api_key": "test-key-not-real",  # pragma: allowlist secret
    }
    values.update(changes)
    return Settings.model_validate(values)


@pytest.mark.parametrize("metrics_enabled", [True, False])
def test_composition_consumes_central_settings_without_calling_network(
    monkeypatch: pytest.MonkeyPatch,
    metrics_enabled: bool,
) -> None:
    adapter = ScriptedCommercialExtractionAdapter([provider_response(base_payload())])
    monkeypatch.setattr(
        "saas_platform.modules.telecom_extraction.composition.build_openai_extraction_adapter",
        lambda configured: adapter,
    )
    configured = settings(
        metrics_enabled=metrics_enabled,
        ai_extraction_context_max_messages=3,
        ai_extraction_auto_accept_confidence="0.9",
        ai_extraction_max_retries=0,
    )

    use_case = build_openai_extraction_use_case(
        configured,
        id_generator=FixedIdGenerator(EXECUTION_ID),
        clock=MutableClock(NOW),
    )
    result = use_case.execute(make_command())

    assert result.provider == "scripted"
    assert len(adapter.requests) == 1


def test_composition_accepts_explicit_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ScriptedCommercialExtractionAdapter([provider_response(base_payload())])
    monkeypatch.setattr(
        "saas_platform.modules.telecom_extraction.composition.build_openai_extraction_adapter",
        lambda configured: adapter,
    )
    telemetry = TelemetrySpy()

    use_case = build_openai_extraction_use_case(
        settings(metrics_enabled=False),
        id_generator=FixedIdGenerator(EXECUTION_ID),
        clock=MutableClock(NOW),
        telemetry=telemetry,
    )
    use_case.execute(make_command())

    assert [item[0] for item in telemetry.events] == ["started", "completed"]
    assert not isinstance(telemetry, NullExtractionTelemetry)
