import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from saas_platform.errors import ValidationError
from saas_platform.modules.lead_qualification.domain import (
    EligibilityStatus,
    LeadProfile,
    Opportunity,
    ProfileFactValue,
    ProfileField,
    QualificationRequest,
    SignalCode,
)
from saas_platform.modules.tenancy.domain import TenantId

PROMPT_VERSION = "telecom-extraction.v1"
SCHEMA_VERSION = "telecom-extraction-result.v1"
OPERATION_NAME = "extract_telecom_commercial_profile"

type ProviderScalar = str | int | float | bool


def _ensure_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{name} must include timezone information")


def _ensure_confidence(value: Decimal, name: str) -> None:
    if value < Decimal("0") or value > Decimal("1"):
        raise ValidationError(f"{name} must be between 0 and 1")


class ConversationRole(StrEnum):
    CUSTOMER = "customer"
    ADVISOR = "advisor"


class Currency(StrEnum):
    MXN = "MXN"
    USD = "USD"


class ProviderFieldState(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ExtractionFieldStatus(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class ConfidenceTreatment(StrEnum):
    AUTO_ACCEPT = "auto_accept"
    PROVISIONAL = "provisional"
    CONFIRMATION_REQUIRED = "confirmation_required"
    REJECTED = "rejected"
    CONTRADICTED = "contradicted"


class CommercialIntent(StrEnum):
    EXPLORATION = "exploration"
    COMPARISON = "comparison"
    PRICE_INQUIRY = "price_inquiry"
    MONTHLY_PAYMENT_INQUIRY = "monthly_payment_inquiry"
    INVENTORY_INQUIRY = "inventory_inquiry"
    REQUIREMENTS_INQUIRY = "requirements_inquiry"
    QUOTE_REQUEST = "quote_request"
    PORTABILITY_REQUEST = "portability_request"
    CONTRACT = "contract"
    PURCHASE = "purchase"
    CALL_REQUEST = "call_request"
    BRANCH_REQUEST = "branch_request"
    ADVISOR_REQUEST = "advisor_request"
    UNKNOWN = "unknown"


class CommercialUrgency(StrEnum):
    IMMEDIATE = "immediate"
    THIS_WEEK = "this_week"
    THIS_MONTH = "this_month"
    FUTURE = "future"
    UNSPECIFIED = "unspecified"


class SpecialControl(StrEnum):
    DO_NOT_CONTACT = "do_not_contact"
    HUMAN_REQUESTED = "human_requested"
    COMPLAINT = "complaint"
    OFFENSIVE_LANGUAGE = "offensive_language"
    SUSPECTED_FRAUD = "suspected_fraud"
    SENSITIVE_INFORMATION = "sensitive_information"
    UNRELATED_CONTENT = "unrelated_content"
    LOW_COMPREHENSION = "low_comprehension"
    CONTRADICTION = "contradiction"
    INSUFFICIENT_DATA = "insufficient_data"
    PROMPT_INJECTION = "prompt_injection"
    UNVERIFIED_COVERAGE_CLAIM = "unverified_coverage_claim"
    UNVERIFIED_INVENTORY_CLAIM = "unverified_inventory_claim"
    CUSTOMER_FINANCING_CLAIM = "customer_financing_claim"


class HandoffKind(StrEnum):
    NONE = "none"
    GENERAL = "general"
    SPECIALIZED = "specialized"


class ExtractionOutcome(StrEnum):
    COMPLETED = "completed"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: UUID
    tenant_id: TenantId
    role: ConversationRole
    content: str
    sent_at: datetime

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValidationError("conversation message content cannot be empty")
        if len(self.content) > 50_000:
            raise ValidationError("conversation message exceeds the absolute size limit")
        _ensure_aware(self.sent_at, "message sent_at")


@dataclass(frozen=True, slots=True)
class PreparedMessage:
    id: UUID
    role: ConversationRole
    content: str
    sent_at: datetime


@dataclass(frozen=True, slots=True)
class TelecomExtractionCommand:
    profile: LeadProfile
    messages: tuple[ConversationMessage, ...]
    correlation_id: UUID
    expected_language: str = "es"
    previous_summary: str | None = None
    recently_asked_fields: tuple[ProfileField, ...] = ()
    eligibility: EligibilityStatus = EligibilityStatus.NOT_APPLICABLE
    handoff_confirmed: bool = False

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValidationError("at least one conversation message is required")
        if not self.expected_language.strip() or len(self.expected_language) > 12:
            raise ValidationError("expected language is invalid")
        if self.previous_summary is not None and len(self.previous_summary) > 10_000:
            raise ValidationError("previous summary exceeds the absolute size limit")
        if len(set(self.recently_asked_fields)) != len(self.recently_asked_fields):
            raise ValidationError("recently asked fields must be unique")
        if any(message.tenant_id != self.profile.tenant_id for message in self.messages):
            raise ValidationError("conversation messages cannot cross tenant boundaries")


@dataclass(frozen=True, slots=True)
class ContextLimits:
    max_messages: int = 12
    max_characters: int = 12_000
    max_summary_characters: int = 2_000
    max_age_days: int = 30

    def __post_init__(self) -> None:
        if (
            min(
                self.max_messages,
                self.max_characters,
                self.max_summary_characters,
                self.max_age_days,
            )
            <= 0
        ):
            raise ValidationError("context limits must be positive")


@dataclass(frozen=True, slots=True)
class ConfidencePolicy:
    auto_accept: Decimal = Decimal("0.85")
    provisional: Decimal = Decimal("0.65")
    confirmation_required: Decimal = Decimal("0.40")

    def __post_init__(self) -> None:
        for value, name in (
            (self.auto_accept, "auto_accept"),
            (self.provisional, "provisional"),
            (self.confirmation_required, "confirmation_required"),
        ):
            _ensure_confidence(value, name)
        if not self.confirmation_required <= self.provisional <= self.auto_accept:
            raise ValidationError("confidence thresholds must be ordered")


@dataclass(frozen=True, slots=True)
class ProviderExtractionRequest:
    tenant_id: TenantId
    conversation_id: UUID
    messages: tuple[PreparedMessage, ...]
    profile_context: Mapping[str, object]
    previous_summary: str | None
    recently_asked_fields: tuple[ProfileField, ...]
    expected_language: str
    prompt_version: str
    schema_version: str
    correlation_id: UUID
    idempotency_key: str
    handoff_confirmed: bool = False
    repair_schema: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_context", MappingProxyType(dict(self.profile_context)))


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValidationError("provider token usage cannot be negative")
        if self.estimated_cost_usd is not None and self.estimated_cost_usd < 0:
            raise ValidationError("provider estimated cost cannot be negative")


@dataclass(frozen=True, slots=True)
class ProviderExtractionResponse:
    payload: Mapping[str, object]
    provider: str
    model: str
    latency_ms: int
    finish_reason: str
    usage: ProviderUsage = field(default_factory=ProviderUsage)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValidationError("provider metadata is required")
        if self.latency_ms < 0:
            raise ValidationError("provider latency cannot be negative")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class ValidatedExtraction:
    payload: Mapping[str, object]
    validations: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class ExtractedOpportunity:
    opportunity: Opportunity
    confidence: Decimal
    evidence: str
    source_message_id: UUID | None

    def __post_init__(self) -> None:
        _ensure_confidence(self.confidence, "opportunity confidence")


@dataclass(frozen=True, slots=True)
class NormalizedField:
    field: ProfileField
    value: ProfileFactValue
    original_text: str
    source_message_id: UUID
    evidence: str
    provider_state: ProviderFieldState
    declared_confidence: Decimal
    currency: Currency | None
    observed_at: datetime

    def __post_init__(self) -> None:
        _ensure_confidence(self.declared_confidence, "field declared confidence")
        _ensure_aware(self.observed_at, "field observed_at")


@dataclass(frozen=True, slots=True)
class NormalizedSignal:
    code: SignalCode
    source_message_id: UUID
    evidence: str
    declared_confidence: Decimal
    observed_at: datetime

    def __post_init__(self) -> None:
        _ensure_confidence(self.declared_confidence, "signal declared confidence")
        _ensure_aware(self.observed_at, "signal observed_at")


@dataclass(frozen=True, slots=True)
class SpecialControlFinding:
    control: SpecialControl
    confidence: Decimal
    evidence: str
    source_message_id: UUID
    deterministic: bool = False

    def __post_init__(self) -> None:
        _ensure_confidence(self.confidence, "control confidence")


@dataclass(frozen=True, slots=True)
class NormalizedExtraction:
    language: str
    opportunity: ExtractedOpportunity
    fields: tuple[NormalizedField, ...]
    signals: tuple[NormalizedSignal, ...]
    intent: CommercialIntent
    intent_confidence: Decimal
    intent_evidence: str
    intent_source_message_id: UUID | None
    urgency: CommercialUrgency
    urgency_confidence: Decimal
    urgency_evidence: str
    urgency_source_message_id: UUID | None
    provider_missing_information: tuple[ProfileField, ...]
    controls: tuple[SpecialControlFinding, ...]
    provider_handoff: HandoffKind
    provider_handoff_reason: str
    provider_handoff_confidence: Decimal
    provider_question_target: ProfileField | None
    provider_question: str | None
    provider_question_reason: str | None
    provider_question_priority: int | None
    provider_question_alternatives: tuple[str, ...]
    provider_question_skippable: bool
    summary: str
    declared_overall_confidence: Decimal
    warnings: tuple[str, ...]
    recoverable_errors: tuple[str, ...]

    def __post_init__(self) -> None:
        _ensure_confidence(self.intent_confidence, "intent confidence")
        _ensure_confidence(self.urgency_confidence, "urgency confidence")
        _ensure_confidence(self.provider_handoff_confidence, "provider handoff confidence")
        _ensure_confidence(self.declared_overall_confidence, "declared overall confidence")


@dataclass(frozen=True, slots=True)
class ResolvedField:
    value: NormalizedField
    confidence: Decimal
    status: ExtractionFieldStatus
    treatment: ConfidenceTreatment
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _ensure_confidence(self.confidence, "resolved field confidence")


@dataclass(frozen=True, slots=True)
class ResolvedSignal:
    value: NormalizedSignal
    confidence: Decimal
    treatment: ConfidenceTreatment

    def __post_init__(self) -> None:
        _ensure_confidence(self.confidence, "resolved signal confidence")


@dataclass(frozen=True, slots=True)
class ExtractionContradiction:
    field: ProfileField
    previous_value: ProfileFactValue
    new_value: ProfileFactValue
    previous_source_message_id: UUID | None
    new_source_message_id: UUID
    previous_evidence: str | None
    new_evidence: str
    confidence: Decimal
    resolution: str
    clarification_question: str

    def __post_init__(self) -> None:
        _ensure_confidence(self.confidence, "contradiction confidence")


@dataclass(frozen=True, slots=True)
class SuggestedQuestion:
    target_field: ProfileField
    text: str
    reason: str
    priority: int
    alternatives: tuple[str, ...]
    skippable: bool


@dataclass(frozen=True, slots=True)
class HandoffSuggestion:
    kind: HandoffKind
    reason_codes: tuple[str, ...]

    @property
    def required(self) -> bool:
        return self.kind is not HandoffKind.NONE


@dataclass(frozen=True, slots=True)
class AcceptedExtraction:
    opportunity: ExtractedOpportunity
    fields: tuple[ResolvedField, ...]
    rejected_fields: tuple[ResolvedField, ...]
    signals: tuple[ResolvedSignal, ...]
    rejected_signals: tuple[ResolvedSignal, ...]
    controls: tuple[SpecialControlFinding, ...]
    contradictions: tuple[ExtractionContradiction, ...]
    missing_information: tuple[ProfileField, ...]
    next_question: SuggestedQuestion | None
    handoff: HandoffSuggestion
    intent: CommercialIntent
    intent_treatment: ConfidenceTreatment
    urgency: CommercialUrgency
    urgency_treatment: ConfidenceTreatment
    summary: str
    overall_confidence: Decimal

    def __post_init__(self) -> None:
        _ensure_confidence(self.overall_confidence, "accepted overall confidence")


@dataclass(frozen=True, slots=True)
class ProfileUpdateProposal:
    previous_profile: LeadProfile
    updated_profile: LeadProfile
    facts_added: tuple[ProfileField, ...]
    facts_preserved: tuple[ProfileField, ...]
    conflicts_added: int
    qualification_request: QualificationRequest


@dataclass(frozen=True, slots=True)
class ExtractionFailure:
    code: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class ExtractionAuditRecord:
    execution_id: UUID
    message_ids: tuple[UUID, ...]
    prompt_version: str
    schema_version: str
    provider: str
    model: str
    raw_structured_payload: Mapping[str, object] | None
    validations: tuple[str, ...]
    accepted_fields: tuple[ProfileField, ...]
    rejected_fields: tuple[ProfileField, ...]
    contradictions: tuple[ProfileField, ...]
    proposed_updates: tuple[ProfileField, ...]
    handoff_reason_codes: tuple[str, ...]
    failures: tuple[ExtractionFailure, ...]

    def __post_init__(self) -> None:
        if self.raw_structured_payload is not None:
            object.__setattr__(
                self,
                "raw_structured_payload",
                MappingProxyType(dict(self.raw_structured_payload)),
            )


@dataclass(frozen=True, slots=True)
class TelecomExtractionResult:
    execution_id: UUID
    tenant_id: TenantId
    conversation_id: UUID
    message_ids: tuple[UUID, ...]
    idempotency_key: str
    outcome: ExtractionOutcome
    accepted: AcceptedExtraction
    update: ProfileUpdateProposal
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    executed_at: datetime
    usage: ProviderUsage
    latency_ms: int
    warnings: tuple[str, ...]
    recoverable_errors: tuple[ExtractionFailure, ...]
    audit: ExtractionAuditRecord

    def __post_init__(self) -> None:
        _ensure_aware(self.executed_at, "extraction executed_at")


def extraction_idempotency_key(
    *,
    tenant_id: TenantId,
    conversation_id: UUID,
    message_ids: tuple[UUID, ...],
    prompt_version: str = PROMPT_VERSION,
    schema_version: str = SCHEMA_VERSION,
) -> str:
    canonical = json.dumps(
        {
            "tenant_id": str(tenant_id.value),
            "conversation_id": str(conversation_id),
            "message_ids": [str(value) for value in message_ids],
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "operation": OPERATION_NAME,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
