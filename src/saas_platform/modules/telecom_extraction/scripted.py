from collections import deque
from collections.abc import Iterable

from saas_platform.modules.telecom_extraction.domain import (
    ProviderExtractionRequest,
    ProviderExtractionResponse,
)


class ScriptedCommercialExtractionAdapter:
    """Deterministic offline adapter for application and reference tests."""

    def __init__(
        self,
        script: Iterable[ProviderExtractionResponse | Exception],
    ) -> None:
        self._script = deque(script)
        self.requests: list[ProviderExtractionRequest] = []

    def extract_commercial_data(
        self,
        request: ProviderExtractionRequest,
    ) -> ProviderExtractionResponse:
        self.requests.append(request)
        if not self._script:
            raise RuntimeError("scripted extraction adapter has no remaining response")
        response = self._script.popleft()
        if isinstance(response, Exception):
            raise response
        return response
