import json
from decimal import Decimal
from time import perf_counter

import openai
from openai import OpenAI

from saas_platform.config import Settings
from saas_platform.modules.telecom_extraction.domain import (
    ProviderExtractionRequest,
    ProviderExtractionResponse,
    ProviderUsage,
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
from saas_platform.modules.telecom_extraction.prompts import render_extraction_instructions
from saas_platform.modules.telecom_extraction.schema import ProviderExtractionPayload


class OpenAICommercialExtractionAdapter:
    def __init__(
        self,
        client: OpenAI,
        *,
        model: str,
        max_output_tokens: int,
        input_cost_per_million_usd: Decimal | None = None,
        output_cost_per_million_usd: Decimal | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._input_cost = input_cost_per_million_usd
        self._output_cost = output_cost_per_million_usd

    def extract_commercial_data(
        self,
        request: ProviderExtractionRequest,
    ) -> ProviderExtractionResponse:
        started = perf_counter()
        try:
            response = self._client.responses.parse(
                model=self._model,
                instructions=render_extraction_instructions(repair_schema=request.repair_schema),
                input=self._input_payload(request),
                text_format=ProviderExtractionPayload,
                max_output_tokens=self._max_output_tokens,
                reasoning={"effort": "low"},
                store=False,
                metadata={
                    "prompt_version": request.prompt_version,
                    "schema_version": request.schema_version,
                    "idempotency_key": request.idempotency_key[:64],
                },
            )
        except openai.APITimeoutError as exc:
            raise ExtractionTimeoutError("structured extraction timed out") from exc
        except openai.RateLimitError as exc:
            raise ExtractionRateLimitError("structured extraction was rate limited") from exc
        except openai.AuthenticationError as exc:
            raise ExtractionAuthenticationError("provider authentication failed") from exc
        except openai.ContentFilterFinishReasonError as exc:
            raise ExtractionRefusedError("provider rejected the structured extraction") from exc
        except openai.LengthFinishReasonError as exc:
            raise ExtractionIncompleteResponseError(
                "provider stopped before completing the structured extraction"
            ) from exc
        except openai.APIResponseValidationError as exc:
            raise ExtractionInvalidResponseError(
                "provider response was incompatible with the SDK contract"
            ) from exc
        except openai.APIConnectionError as exc:
            raise ExtractionTemporaryProviderError("provider connection failed") from exc
        except openai.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in {408, 409, 429}:
                raise ExtractionTemporaryProviderError(
                    "provider returned a temporary status"
                ) from exc
            raise ExtractionPermanentProviderError("provider returned a permanent status") from exc
        except openai.OpenAIError as exc:
            raise ExtractionPermanentProviderError("provider request failed") from exc

        if response.status == "incomplete":
            raise ExtractionIncompleteResponseError(
                "provider returned an incomplete structured extraction"
            )
        parsed = response.output_parsed
        if parsed is None:
            if self._contains_refusal(response.output):
                raise ExtractionRefusedError("provider refused the structured extraction")
            raise ExtractionEmptyResponseError("provider returned no structured extraction")

        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else 0
        output_tokens = usage.output_tokens if usage is not None else 0
        estimated_cost = self._estimated_cost(input_tokens, output_tokens)
        finish_reason = response.status or "completed"
        return ProviderExtractionResponse(
            payload=parsed.model_dump(mode="json"),
            provider="openai",
            model=self._model,
            latency_ms=round((perf_counter() - started) * 1_000),
            finish_reason=finish_reason,
            usage=ProviderUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost,
            ),
        )

    @staticmethod
    def _input_payload(request: ProviderExtractionRequest) -> str:
        payload = {
            "expected_language": request.expected_language,
            "messages": [
                {
                    "message_id": str(message.id),
                    "role": message.role.value,
                    "sent_at": message.sent_at.isoformat(),
                    "content": message.content,
                }
                for message in request.messages
            ],
            "allowed_commercial_context": dict(request.profile_context),
            "previous_summary": request.previous_summary,
            "recently_asked_fields": [item.value for item in request.recently_asked_fields],
            "handoff_confirmed": request.handoff_confirmed,
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _contains_refusal(output: object) -> bool:
        if not isinstance(output, list):
            return False
        for item in output:
            for content in getattr(item, "content", ()):
                if getattr(content, "type", None) == "refusal":
                    return True
        return False

    def _estimated_cost(self, input_tokens: int, output_tokens: int) -> Decimal | None:
        if self._input_cost is None or self._output_cost is None:
            return None
        million = Decimal("1000000")
        return (
            Decimal(input_tokens) * self._input_cost + Decimal(output_tokens) * self._output_cost
        ) / million


def build_openai_extraction_adapter(settings: Settings) -> OpenAICommercialExtractionAdapter:
    if not settings.ai_extraction_enabled:
        raise ValueError("AI extraction is disabled")
    if settings.openai_api_key is None:
        raise ValueError("OPENAI_API_KEY is required when AI extraction is enabled")
    client = OpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.ai_extraction_timeout_seconds,
        max_retries=0,
    )
    return OpenAICommercialExtractionAdapter(
        client,
        model=settings.ai_extraction_model,
        max_output_tokens=settings.ai_extraction_max_output_tokens,
        input_cost_per_million_usd=settings.ai_extraction_input_cost_per_million_usd,
        output_cost_per_million_usd=settings.ai_extraction_output_cost_per_million_usd,
    )
