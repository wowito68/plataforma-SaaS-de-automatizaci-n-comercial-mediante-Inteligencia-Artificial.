from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from saas_platform.modules.lead_qualification.domain import (
    FactConflict,
    LeadProfile,
    Opportunity,
    OpportunityType,
    ProfileFact,
    ProfileField,
)
from saas_platform.modules.telecom_extraction.application import ExtractTelecomConversation
from saas_platform.modules.telecom_extraction.context import ExtractionContextPreparer
from saas_platform.modules.telecom_extraction.domain import (
    ConfidencePolicy,
    ContextLimits,
    ConversationMessage,
    ConversationRole,
    ProviderExtractionResponse,
    ProviderUsage,
    TelecomExtractionCommand,
)
from saas_platform.modules.telecom_extraction.scripted import (
    ScriptedCommercialExtractionAdapter,
)
from saas_platform.modules.tenancy.domain import TenantId
from tests.fakes import FixedIdGenerator, MutableClock

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
TENANT_ID = TenantId(UUID("019867ab-cdef-7abc-8def-0123456789ab"))
OTHER_TENANT_ID = TenantId(UUID("019867ab-cdef-7abc-8def-0123456789ac"))
LEAD_ID = UUID("018f0000-0000-7000-8000-000000000020")
CONVERSATION_ID = UUID("018f0000-0000-7000-8000-000000000030")
CORRELATION_ID = UUID("018f0000-0000-7000-8000-000000000050")
EXECUTION_ID = UUID("018f0000-0000-7000-8000-000000000060")
MESSAGE_ID = UUID("018f0000-0000-7000-8000-000000000070")
SECOND_MESSAGE_ID = UUID("018f0000-0000-7000-8000-000000000071")


def make_profile(
    opportunity: OpportunityType = OpportunityType.UNIDENTIFIED,
    *,
    facts: Mapping[ProfileField, ProfileFact] | None = None,
    conflicts: tuple[FactConflict, ...] = (),
    tenant_id: TenantId = TENANT_ID,
) -> LeadProfile:
    return LeadProfile(
        tenant_id=tenant_id,
        lead_id=LEAD_ID,
        conversation_id=CONVERSATION_ID,
        opportunity=Opportunity(opportunity),
        facts=facts or {},
        conflicts=conflicts,
    )


def make_message(
    content: str = "Quiero un telefono.",
    *,
    message_id: UUID = MESSAGE_ID,
    tenant_id: TenantId = TENANT_ID,
    role: ConversationRole = ConversationRole.CUSTOMER,
    sent_at: datetime = NOW,
) -> ConversationMessage:
    return ConversationMessage(
        id=message_id,
        tenant_id=tenant_id,
        role=role,
        content=content,
        sent_at=sent_at,
    )


def make_command(
    messages: tuple[ConversationMessage, ...] | None = None,
    *,
    profile: LeadProfile | None = None,
    recently_asked_fields: tuple[ProfileField, ...] = (),
    previous_summary: str | None = None,
) -> TelecomExtractionCommand:
    return TelecomExtractionCommand(
        profile=profile or make_profile(),
        messages=messages or (make_message(),),
        correlation_id=CORRELATION_ID,
        recently_asked_fields=recently_asked_fields,
        previous_summary=previous_summary,
    )


def make_field(
    field: str,
    value: str | int | float | bool,
    *,
    message_id: UUID = MESSAGE_ID,
    evidence: str | None = None,
    confidence: float = 0.95,
    state: str = "observed",
    currency: str | None = None,
) -> dict[str, object]:
    evidence = evidence or str(value)
    return {
        "field": field,
        "value": value,
        "original_text": evidence,
        "message_id": str(message_id),
        "evidence": evidence,
        "state": state,
        "confidence": confidence,
        "currency": currency,
    }


def make_signal(
    code: str,
    *,
    message_id: UUID = MESSAGE_ID,
    evidence: str = "Quiero",
    confidence: float = 0.95,
) -> dict[str, object]:
    return {
        "code": code,
        "message_id": str(message_id),
        "evidence": evidence,
        "confidence": confidence,
    }


def make_control(
    control: str,
    *,
    message_id: UUID = MESSAGE_ID,
    evidence: str,
    confidence: float = 0.95,
) -> dict[str, object]:
    return {
        "control": control,
        "message_id": str(message_id),
        "evidence": evidence,
        "confidence": confidence,
    }


def base_payload(
    *,
    message_id: UUID = MESSAGE_ID,
    opportunity: str = "device_purchase",
    opportunity_confidence: float = 0.95,
    evidence: str = "telefono",
    fields: list[dict[str, object]] | None = None,
    signals: list[dict[str, object]] | None = None,
    controls: list[dict[str, object]] | None = None,
    intent: str = "exploration",
    urgency: str = "unspecified",
    missing: list[str] | None = None,
    handoff: str = "none",
    handoff_confidence: float = 0.0,
    next_question: dict[str, object] | None = None,
    summary: str = "Customer is exploring a device.",
    overall_confidence: float = 0.9,
) -> dict[str, object]:
    return {
        "language": "es",
        "opportunity": {
            "primary": opportunity,
            "secondary": [],
            "confidence": opportunity_confidence,
            "evidence": evidence,
            "message_id": str(message_id),
        },
        "fields": fields or [],
        "signals": signals or [],
        "intent": {
            "value": intent,
            "message_id": str(message_id) if intent != "unknown" else None,
            "evidence": evidence if intent != "unknown" else "",
            "confidence": opportunity_confidence if intent != "unknown" else 0.0,
        },
        "urgency": {
            "value": urgency,
            "message_id": str(message_id) if urgency != "unspecified" else None,
            "evidence": evidence if urgency != "unspecified" else "",
            "confidence": opportunity_confidence if urgency != "unspecified" else 0.0,
        },
        "missing_information": missing or [],
        "controls": controls or [],
        "handoff": {
            "kind": handoff,
            "reason": "provider suggestion" if handoff != "none" else "",
            "message_id": str(message_id) if handoff != "none" else None,
            "evidence": evidence if handoff != "none" else "",
            "confidence": handoff_confidence,
        },
        "next_question": next_question,
        "summary": summary,
        "overall_confidence": overall_confidence,
        "warnings": [],
        "recoverable_errors": [],
    }


def provider_response(
    payload: dict[str, object],
    *,
    latency_ms: int = 18,
    usage: ProviderUsage | None = None,
) -> ProviderExtractionResponse:
    return ProviderExtractionResponse(
        payload=payload,
        provider="scripted",
        model="extractor-test-v1",
        latency_ms=latency_ms,
        finish_reason="completed",
        usage=usage or ProviderUsage(input_tokens=120, output_tokens=80),
    )


class NoSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)


class TelemetrySpy:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def started(self, *, provider: str, model: str, prompt_version: str) -> None:
        self.events.append(("started", (provider, model, prompt_version)))

    def repair_attempted(self, *, provider: str, model: str) -> None:
        self.events.append(("repair", (provider, model)))

    def failed(self, *, provider: str, model: str, error_code: str) -> None:
        self.events.append(("failed", error_code))

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
        self.events.append(
            (
                "completed",
                {
                    "provider": provider,
                    "model": model,
                    "prompt_version": prompt_version,
                    "duration": duration_seconds,
                    "confidence": confidence,
                    "fields": field_count,
                    "contradictions": contradiction_count,
                    "dnc": no_contact,
                    "handoff": handoff,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": estimated_cost_usd,
                },
            )
        )


def make_service(
    script: list[ProviderExtractionResponse | Exception],
    *,
    max_retries: int = 0,
    telemetry: TelemetrySpy | None = None,
    sleeper: NoSleep | None = None,
    limits: ContextLimits | None = None,
) -> tuple[ExtractTelecomConversation, ScriptedCommercialExtractionAdapter]:
    adapter = ScriptedCommercialExtractionAdapter(script)
    service = ExtractTelecomConversation(
        adapter,
        ExtractionContextPreparer(limits or ContextLimits()),
        ConfidencePolicy(),
        FixedIdGenerator(EXECUTION_ID),
        MutableClock(NOW),
        provider_name="scripted",
        model="extractor-test-v1",
        max_retries=max_retries,
        telemetry=telemetry,
        retry_sleeper=sleeper,
    )
    return service, adapter
