from typing import Protocol

from saas_platform.modules.telecom_extraction.domain import (
    ProviderExtractionRequest,
    ProviderExtractionResponse,
)


class CommercialExtractionProvider(Protocol):
    """Capability port for one structured commercial extraction."""

    def extract_commercial_data(
        self,
        request: ProviderExtractionRequest,
    ) -> ProviderExtractionResponse: ...


class RetrySleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


class ExtractionTelemetry(Protocol):
    def started(self, *, provider: str, model: str, prompt_version: str) -> None: ...

    def repair_attempted(self, *, provider: str, model: str) -> None: ...

    def failed(self, *, provider: str, model: str, error_code: str) -> None: ...

    def completed(
        self,
        *,
        provider: str,
        model: str,
        prompt_version: str,
        duration_seconds: float,
        confidence: float,
        field_count: int,
        contradiction_count: int,
        no_contact: bool,
        handoff: bool,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float | None,
    ) -> None: ...
