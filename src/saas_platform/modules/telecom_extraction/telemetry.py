from prometheus_client import Counter, Histogram

from saas_platform.modules.telecom_extraction.domain import PROMPT_VERSION
from saas_platform.modules.telecom_extraction.ports import ExtractionTelemetry

EXTRACTIONS = Counter(
    "saas_ai_extractions_total",
    "Structured AI extraction lifecycle events",
    ("outcome", "provider", "model", "prompt_version"),
)
EXTRACTION_ERRORS = Counter(
    "saas_ai_extraction_errors_total",
    "Structured AI extraction errors",
    ("error_code", "provider", "model"),
)
SCHEMA_REPAIRS = Counter(
    "saas_ai_schema_repairs_total",
    "Controlled structured output repair attempts",
    ("provider", "model"),
)
EXTRACTION_DURATION = Histogram(
    "saas_ai_extraction_duration_seconds",
    "Structured extraction duration",
    ("provider", "model", "prompt_version"),
)
EXTRACTION_CONFIDENCE = Histogram(
    "saas_ai_extraction_confidence",
    "Accepted extraction confidence",
    ("provider", "model", "prompt_version"),
    buckets=(0.0, 0.4, 0.65, 0.85, 0.95, 1.0),
)
EXTRACTED_FIELDS = Counter(
    "saas_ai_extracted_fields_total",
    "Fields accepted or retained provisionally",
    ("provider", "model", "prompt_version"),
)
EXTRACTION_CONTRADICTIONS = Counter(
    "saas_ai_extraction_contradictions_total",
    "Contradictions detected after structured extraction",
    ("provider", "model", "prompt_version"),
)
EXTRACTION_CONTROLS = Counter(
    "saas_ai_extraction_controls_total",
    "High-priority extraction controls",
    ("control", "provider", "model"),
)
EXTRACTION_HANDOFFS = Counter(
    "saas_ai_extraction_handoffs_total",
    "Validated handoff suggestions",
    ("provider", "model", "prompt_version"),
)
EXTRACTION_TOKENS = Counter(
    "saas_ai_extraction_tokens_total",
    "Provider token usage",
    ("direction", "provider", "model", "prompt_version"),
)
EXTRACTION_ESTIMATED_COST = Counter(
    "saas_ai_extraction_estimated_cost_usd_total",
    "Estimated provider cost when configured",
    ("provider", "model"),
)


class PrometheusExtractionTelemetry(ExtractionTelemetry):
    def started(self, *, provider: str, model: str, prompt_version: str) -> None:
        EXTRACTIONS.labels("started", provider, model, prompt_version).inc()

    def repair_attempted(self, *, provider: str, model: str) -> None:
        SCHEMA_REPAIRS.labels(provider, model).inc()

    def failed(self, *, provider: str, model: str, error_code: str) -> None:
        EXTRACTIONS.labels("failed", provider, model, PROMPT_VERSION).inc()
        EXTRACTION_ERRORS.labels(error_code, provider, model).inc()

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
    ) -> None:
        EXTRACTIONS.labels("completed", provider, model, prompt_version).inc()
        EXTRACTION_DURATION.labels(provider, model, prompt_version).observe(duration_seconds)
        EXTRACTION_CONFIDENCE.labels(provider, model, prompt_version).observe(confidence)
        EXTRACTED_FIELDS.labels(provider, model, prompt_version).inc(field_count)
        EXTRACTION_CONTRADICTIONS.labels(provider, model, prompt_version).inc(contradiction_count)
        if no_contact:
            EXTRACTION_CONTROLS.labels("do_not_contact", provider, model).inc()
        if handoff:
            EXTRACTION_HANDOFFS.labels(provider, model, prompt_version).inc()
        EXTRACTION_TOKENS.labels("input", provider, model, prompt_version).inc(input_tokens)
        EXTRACTION_TOKENS.labels("output", provider, model, prompt_version).inc(output_tokens)
        if estimated_cost_usd is not None:
            EXTRACTION_ESTIMATED_COST.labels(provider, model).inc(estimated_cost_usd)


class NullExtractionTelemetry(ExtractionTelemetry):
    def started(self, *, provider: str, model: str, prompt_version: str) -> None:
        pass

    def repair_attempted(self, *, provider: str, model: str) -> None:
        pass

    def failed(self, *, provider: str, model: str, error_code: str) -> None:
        pass

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
    ) -> None:
        pass
