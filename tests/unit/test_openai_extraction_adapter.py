import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion

from saas_platform.config import Settings
from saas_platform.modules.telecom_extraction.context import ExtractionContextPreparer
from saas_platform.modules.telecom_extraction.domain import (
    ContextLimits,
    ProviderExtractionRequest,
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
from saas_platform.modules.telecom_extraction.openai_adapter import (
    OpenAICommercialExtractionAdapter,
    build_openai_extraction_adapter,
)
from saas_platform.modules.telecom_extraction.prompts import (
    PROMPT_COMPONENTS,
    render_extraction_instructions,
)
from saas_platform.modules.telecom_extraction.schema import validate_provider_payload
from tests.extraction_fixtures import NOW, base_payload, make_command, make_message


class FakeResponses:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeOpenAI:
    def __init__(self, outcome: object) -> None:
        self.responses = FakeResponses(outcome)


def provider_request(content: str = "Quiero un telefono.") -> ProviderExtractionRequest:
    return (
        ExtractionContextPreparer(ContextLimits())
        .prepare(
            make_command((make_message(content),)),
            now=NOW,
        )
        .request
    )


def sdk_response(
    *,
    status: str = "completed",
    parsed: object | None = None,
    output: object | None = None,
    usage: object | None = None,
) -> SimpleNamespace:
    if parsed is None and status == "completed" and output is None:
        parsed = validate_provider_payload(base_payload())[0]
    return SimpleNamespace(
        status=status,
        output_parsed=parsed,
        output=[] if output is None else output,
        usage=usage,
    )


def make_adapter(
    outcome: object,
    *,
    input_cost: Decimal | None = None,
    output_cost: Decimal | None = None,
) -> tuple[OpenAICommercialExtractionAdapter, FakeOpenAI]:
    client = FakeOpenAI(outcome)
    adapter = OpenAICommercialExtractionAdapter(
        cast(OpenAI, client),
        model="gpt-test",
        max_output_tokens=1_000,
        input_cost_per_million_usd=input_cost,
        output_cost_per_million_usd=output_cost,
    )
    return adapter, client


def test_adapter_uses_one_non_streaming_strict_responses_call() -> None:
    usage = SimpleNamespace(input_tokens=1_000, output_tokens=500)
    adapter, client = make_adapter(
        sdk_response(usage=usage),
        input_cost=Decimal("2"),
        output_cost=Decimal("8"),
    )

    response = adapter.extract_commercial_data(provider_request())

    assert response.provider == "openai"
    assert response.model == "gpt-test"
    assert response.finish_reason == "completed"
    assert response.usage.input_tokens == 1_000
    assert response.usage.output_tokens == 500
    assert response.usage.estimated_cost_usd == Decimal("0.006")
    call = client.responses.calls[0]
    text_format = cast(type[object], call["text_format"])
    assert text_format.__name__ == "ProviderExtractionPayload"
    assert call["store"] is False
    assert call["max_output_tokens"] == 1_000
    assert call["reasoning"] == {"effort": "low"}
    assert "score" in cast(str, call["instructions"])
    sent = json.loads(cast(str, call["input"]))
    assert sent["messages"][0]["content"] == "Quiero un telefono."
    assert "tenant_id" not in sent


def test_adapter_omits_cost_when_rates_and_usage_are_unavailable() -> None:
    adapter, _ = make_adapter(sdk_response(usage=None))

    response = adapter.extract_commercial_data(provider_request())

    assert response.usage.input_tokens == 0
    assert response.usage.estimated_cost_usd is None


def api_response(status: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(status, request=request)


def sdk_errors() -> list[tuple[Exception, type[Exception]]]:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    completion = ChatCompletion(
        id="completion",
        choices=[],
        created=0,
        model="test",
        object="chat.completion",
    )
    return [
        (openai.APITimeoutError(request), ExtractionTimeoutError),
        (
            openai.RateLimitError("rate", response=api_response(429), body=None),
            ExtractionRateLimitError,
        ),
        (
            openai.AuthenticationError("auth", response=api_response(401), body=None),
            ExtractionAuthenticationError,
        ),
        (openai.ContentFilterFinishReasonError(), ExtractionRefusedError),
        (
            openai.LengthFinishReasonError(completion=completion),
            ExtractionIncompleteResponseError,
        ),
        (
            openai.APIResponseValidationError(
                api_response(200),
                {},
                message="invalid response",
            ),
            ExtractionInvalidResponseError,
        ),
        (
            openai.APIConnectionError(request=request),
            ExtractionTemporaryProviderError,
        ),
        (
            openai.InternalServerError("server", response=api_response(500), body=None),
            ExtractionTemporaryProviderError,
        ),
        (
            openai.BadRequestError("bad", response=api_response(400), body=None),
            ExtractionPermanentProviderError,
        ),
        (openai.OpenAIError("generic"), ExtractionPermanentProviderError),
    ]


@pytest.mark.parametrize(("sdk_error", "expected"), sdk_errors())
def test_sdk_errors_are_translated_without_leaking_provider_types(
    sdk_error: Exception,
    expected: type[Exception],
) -> None:
    adapter, _ = make_adapter(sdk_error)

    with pytest.raises(expected) as captured:
        adapter.extract_commercial_data(provider_request())

    assert not isinstance(captured.value, openai.OpenAIError)


def test_incomplete_empty_and_refused_responses_are_distinct() -> None:
    adapter, _ = make_adapter(sdk_response(status="incomplete"))
    with pytest.raises(ExtractionIncompleteResponseError):
        adapter.extract_commercial_data(provider_request())

    empty = SimpleNamespace(status="completed", output_parsed=None, output=[], usage=None)
    adapter, _ = make_adapter(empty)
    with pytest.raises(ExtractionEmptyResponseError):
        adapter.extract_commercial_data(provider_request())

    refusal = SimpleNamespace(
        status="completed",
        output_parsed=None,
        output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])],
        usage=None,
    )
    adapter, _ = make_adapter(refusal)
    with pytest.raises(ExtractionRefusedError):
        adapter.extract_commercial_data(provider_request())


def settings(**changes: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "service_name": "test",
        "database_url": "postgresql+psycopg://localhost/test",
        "database_worker_url": "postgresql+psycopg://localhost/test",
    }
    values.update(changes)
    return Settings.model_validate(values)


def test_factory_requires_opt_in_and_builds_client_without_network() -> None:
    with pytest.raises(ValueError, match="disabled"):
        build_openai_extraction_adapter(settings())

    configured = settings(
        ai_extraction_enabled=True,
        openai_api_key="test-key-not-real",  # pragma: allowlist secret
        ai_extraction_model="gpt-test",
    )
    adapter = build_openai_extraction_adapter(configured)
    assert isinstance(adapter, OpenAICommercialExtractionAdapter)

    configured.openai_api_key = None
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_openai_extraction_adapter(configured)


def test_versioned_prompt_registry_covers_all_tasks_and_repair_is_bounded() -> None:
    identifiers = {item.identifier for item in PROMPT_COMPONENTS}

    assert len(identifiers) == 6
    assert all(item.version == "telecom-extraction.v1" for item in PROMPT_COMPONENTS)
    normal = render_extraction_instructions()
    repair = render_extraction_instructions(repair_schema=True)
    assert "chain-of-thought" in normal
    assert "Repair structure only" not in normal
    assert "Repair structure only" in repair
