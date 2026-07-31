from saas_platform.config import Settings
from saas_platform.foundation import Clock, IdGenerator
from saas_platform.modules.telecom_extraction.application import ExtractTelecomConversation
from saas_platform.modules.telecom_extraction.context import ExtractionContextPreparer
from saas_platform.modules.telecom_extraction.domain import ConfidencePolicy, ContextLimits
from saas_platform.modules.telecom_extraction.openai_adapter import (
    build_openai_extraction_adapter,
)
from saas_platform.modules.telecom_extraction.ports import ExtractionTelemetry
from saas_platform.modules.telecom_extraction.telemetry import (
    NullExtractionTelemetry,
    PrometheusExtractionTelemetry,
)


def build_openai_extraction_use_case(
    settings: Settings,
    *,
    id_generator: IdGenerator,
    clock: Clock,
    telemetry: ExtractionTelemetry | None = None,
) -> ExtractTelecomConversation:
    """Compose the opt-in provider only where this capability is required."""
    adapter = build_openai_extraction_adapter(settings)
    selected_telemetry = telemetry
    if selected_telemetry is None:
        selected_telemetry = (
            PrometheusExtractionTelemetry()
            if settings.metrics_enabled
            else NullExtractionTelemetry()
        )
    return ExtractTelecomConversation(
        adapter,
        ExtractionContextPreparer(
            ContextLimits(
                max_messages=settings.ai_extraction_context_max_messages,
                max_characters=settings.ai_extraction_context_max_characters,
                max_summary_characters=settings.ai_extraction_summary_max_characters,
                max_age_days=settings.ai_extraction_context_max_age_days,
            )
        ),
        ConfidencePolicy(
            auto_accept=settings.ai_extraction_auto_accept_confidence,
            provisional=settings.ai_extraction_provisional_confidence,
            confirmation_required=settings.ai_extraction_confirmation_confidence,
        ),
        id_generator,
        clock,
        provider_name=settings.ai_extraction_provider,
        model=settings.ai_extraction_model,
        max_retries=settings.ai_extraction_max_retries,
        telemetry=selected_telemetry,
    )
