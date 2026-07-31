from typing import cast

import pytest
from prometheus_client import REGISTRY

from saas_platform.modules.telecom_extraction.application import (
    ExtractTelecomConversation,
    SystemRetrySleeper,
)
from saas_platform.modules.telecom_extraction.context import ExtractionContextPreparer
from saas_platform.modules.telecom_extraction.domain import (
    ConfidencePolicy,
    ContextLimits,
    ExtractionOutcome,
    SpecialControl,
)
from saas_platform.modules.telecom_extraction.errors import (
    ExtractionAuthenticationError,
    ExtractionEmptyResponseError,
    ExtractionIncompleteResponseError,
    ExtractionInvalidResponseError,
    ExtractionPermanentProviderError,
    ExtractionRateLimitError,
    ExtractionRefusedError,
    ExtractionTemporaryProviderError,
    ExtractionTimeoutError,
)
from saas_platform.modules.telecom_extraction.scripted import (
    ScriptedCommercialExtractionAdapter,
)
from saas_platform.modules.telecom_extraction.telemetry import (
    NullExtractionTelemetry,
    PrometheusExtractionTelemetry,
)
from tests.extraction_fixtures import (
    EXECUTION_ID,
    NOW,
    NoSleep,
    TelemetrySpy,
    base_payload,
    make_command,
    make_message,
    make_service,
    provider_response,
)
from tests.fakes import FixedIdGenerator, MutableClock


def test_invalid_schema_is_repaired_once_with_same_idempotency_key() -> None:
    telemetry = TelemetrySpy()
    sleeper = NoSleep()
    service, adapter = make_service(
        [provider_response({"score": 100}), provider_response(base_payload())],
        max_retries=1,
        telemetry=telemetry,
        sleeper=sleeper,
    )

    result = service.execute(make_command())

    assert result.outcome is ExtractionOutcome.COMPLETED
    assert len(adapter.requests) == 2
    assert adapter.requests[0].repair_schema is False
    assert adapter.requests[1].repair_schema is True
    assert adapter.requests[0].idempotency_key == adapter.requests[1].idempotency_key
    assert sleeper.delays == [0.25]
    assert [item[0] for item in telemetry.events] == [
        "started",
        "failed",
        "repair",
        "completed",
    ]
    assert result.recoverable_errors[0].code == "extraction_invalid_response"


def test_transient_retry_does_not_request_schema_repair() -> None:
    sleeper = NoSleep()
    service, adapter = make_service(
        [ExtractionTimeoutError("timeout"), provider_response(base_payload())],
        max_retries=1,
        sleeper=sleeper,
    )

    result = service.execute(make_command())

    assert result.outcome is ExtractionOutcome.COMPLETED
    assert [item.repair_schema for item in adapter.requests] == [False, False]
    assert sleeper.delays == [0.25]


@pytest.mark.parametrize(
    "error",
    [
        ExtractionTimeoutError("timeout"),
        ExtractionRateLimitError("rate limit"),
        ExtractionTemporaryProviderError("down"),
        ExtractionIncompleteResponseError("incomplete"),
    ],
)
def test_exhausted_transient_error_returns_explicit_degraded_result(error: Exception) -> None:
    service, adapter = make_service([error])

    result = service.execute(make_command())

    assert result.outcome is ExtractionOutcome.DEGRADED
    assert len(adapter.requests) == 1
    assert result.recoverable_errors[0].retryable is True
    assert result.audit.raw_structured_payload is None


@pytest.mark.parametrize(
    "error",
    [
        ExtractionAuthenticationError("auth"),
        ExtractionPermanentProviderError("bad request"),
        ExtractionRefusedError("refused"),
        ExtractionEmptyResponseError("empty"),
    ],
)
def test_permanent_provider_error_is_not_retried(error: Exception) -> None:
    sleeper = NoSleep()
    service, adapter = make_service(
        [error, provider_response(base_payload())],
        max_retries=1,
        sleeper=sleeper,
    )

    result = service.execute(make_command())

    assert result.outcome is ExtractionOutcome.DEGRADED
    assert len(adapter.requests) == 1
    assert sleeper.delays == []
    assert result.recoverable_errors[0].retryable is False


def test_do_not_contact_fallback_survives_timeout() -> None:
    service, _ = make_service([ExtractionTimeoutError("timeout")])

    result = service.execute(make_command((make_message("No me contacten."),)))

    assert SpecialControl.DO_NOT_CONTACT in {item.control for item in result.accepted.controls}
    assert result.accepted.next_question is None


def test_scripted_adapter_records_request_and_fails_when_script_is_exhausted() -> None:
    adapter = ScriptedCommercialExtractionAdapter([provider_response(base_payload())])
    preparer = ExtractionContextPreparer(ContextLimits()).prepare(make_command(), now=NOW)

    response = adapter.extract_commercial_data(preparer.request)

    assert response.model == "extractor-test-v1"
    assert adapter.requests == [preparer.request]
    with pytest.raises(RuntimeError, match="no remaining"):
        adapter.extract_commercial_data(preparer.request)


def test_retry_configuration_is_bounded() -> None:
    adapter = ScriptedCommercialExtractionAdapter([])
    arguments = (
        adapter,
        ExtractionContextPreparer(ContextLimits()),
        ConfidencePolicy(),
        FixedIdGenerator(EXECUTION_ID),
        MutableClock(NOW),
    )
    with pytest.raises(ValueError, match="between 0 and 3"):
        ExtractTelecomConversation(
            *arguments,
            provider_name="scripted",
            model="test",
            max_retries=-1,
        )
    with pytest.raises(ValueError, match="between 0 and 3"):
        ExtractTelecomConversation(
            *arguments,
            provider_name="scripted",
            model="test",
            max_retries=4,
        )


def test_invalid_id_generator_is_rejected_and_context_vars_are_reset() -> None:
    class BadIds:
        def new(self) -> object:
            return "not-a-uuid"

    service = ExtractTelecomConversation(
        ScriptedCommercialExtractionAdapter([provider_response(base_payload())]),
        ExtractionContextPreparer(ContextLimits()),
        ConfidencePolicy(),
        cast(FixedIdGenerator, BadIds()),
        MutableClock(NOW),
        provider_name="scripted",
        model="test",
    )

    with pytest.raises(TypeError, match="UUID"):
        service.execute(make_command())


def test_system_retry_sleeper_delegates_to_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []
    monkeypatch.setattr(
        "saas_platform.modules.telecom_extraction.application.sleep",
        delays.append,
    )

    SystemRetrySleeper().sleep(0.5)

    assert delays == [0.5]


def test_null_telemetry_accepts_every_event() -> None:
    telemetry = NullExtractionTelemetry()
    telemetry.started(provider="x", model="m", prompt_version="v")
    telemetry.repair_attempted(provider="x", model="m")
    telemetry.failed(provider="x", model="m", error_code="error")
    telemetry.completed(
        provider="x",
        model="m",
        prompt_version="v",
        duration_seconds=0.1,
        confidence=0.9,
        field_count=1,
        contradiction_count=0,
        no_contact=False,
        handoff=False,
        input_tokens=1,
        output_tokens=1,
        estimated_cost_usd=None,
    )


def test_prometheus_telemetry_emits_bounded_lifecycle_and_usage_metrics() -> None:
    telemetry = PrometheusExtractionTelemetry()
    labels = {"provider": "test-provider", "model": "test-model"}
    telemetry.started(**labels, prompt_version="telecom-extraction.v1")
    telemetry.repair_attempted(**labels)
    telemetry.failed(**labels, error_code="extraction_timeout")
    telemetry.completed(
        **labels,
        prompt_version="telecom-extraction.v1",
        duration_seconds=0.1,
        confidence=0.9,
        field_count=2,
        contradiction_count=1,
        no_contact=True,
        handoff=True,
        input_tokens=10,
        output_tokens=5,
        estimated_cost_usd=0.01,
    )

    names = {metric.name for metric in REGISTRY.collect()}
    assert "saas_ai_extractions" in names
    assert "saas_ai_extraction_duration_seconds" in names
    assert "saas_ai_extraction_tokens" in names
    assert "saas_ai_extraction_estimated_cost_usd" in names


def test_invalid_response_exception_is_retryable_at_application_boundary() -> None:
    service, _ = make_service([ExtractionInvalidResponseError("invalid")])

    result = service.execute(make_command())

    assert result.recoverable_errors == (result.recoverable_errors[0],)
    assert result.recoverable_errors[0].retryable is True
