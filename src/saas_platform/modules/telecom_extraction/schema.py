import json
from collections.abc import Mapping
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from saas_platform.modules.lead_qualification.domain import (
    OpportunityType,
    ProfileField,
    SignalCode,
)
from saas_platform.modules.telecom_extraction.domain import (
    CommercialIntent,
    CommercialUrgency,
    Currency,
    HandoffKind,
    ProviderFieldState,
    ProviderScalar,
    SpecialControl,
    ValidatedExtraction,
)
from saas_platform.modules.telecom_extraction.errors import ExtractionInvalidResponseError

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
EvidenceText = Annotated[str, Field(min_length=1, max_length=300)]


class StrictProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ProviderOpportunity(StrictProviderModel):
    primary: OpportunityType
    secondary: Annotated[list[OpportunityType], Field(max_length=10)]
    confidence: Confidence
    evidence: EvidenceText
    message_id: UUID | None

    @model_validator(mode="after")
    def validate_unique_opportunities(self) -> Self:
        if self.primary in self.secondary:
            raise ValueError("primary opportunity cannot also be secondary")
        if len(set(self.secondary)) != len(self.secondary):
            raise ValueError("secondary opportunities must be unique")
        return self


class ProviderField(StrictProviderModel):
    field: ProfileField
    value: ProviderScalar
    original_text: ShortText
    message_id: UUID
    evidence: EvidenceText
    state: ProviderFieldState
    confidence: Confidence
    currency: Currency | None


class ProviderSignal(StrictProviderModel):
    code: SignalCode
    message_id: UUID
    evidence: EvidenceText
    confidence: Confidence


class ProviderControl(StrictProviderModel):
    control: SpecialControl
    message_id: UUID
    evidence: EvidenceText
    confidence: Confidence


class ProviderIntent(StrictProviderModel):
    value: CommercialIntent
    message_id: UUID | None
    evidence: Annotated[str, Field(max_length=300)]
    confidence: Confidence

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.value is not CommercialIntent.UNKNOWN and (
            self.message_id is None or not self.evidence
        ):
            raise ValueError("a known intent requires evidence and a source message")
        return self


class ProviderUrgency(StrictProviderModel):
    value: CommercialUrgency
    message_id: UUID | None
    evidence: Annotated[str, Field(max_length=300)]
    confidence: Confidence

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.value is not CommercialUrgency.UNSPECIFIED and (
            self.message_id is None or not self.evidence
        ):
            raise ValueError("a known urgency requires evidence and a source message")
        return self


class ProviderHandoff(StrictProviderModel):
    kind: HandoffKind
    reason: Annotated[str, Field(max_length=300)]
    message_id: UUID | None
    evidence: Annotated[str, Field(max_length=300)]
    confidence: Confidence

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.kind is not HandoffKind.NONE and (
            self.message_id is None or not self.evidence or not self.reason
        ):
            raise ValueError("a handoff suggestion requires reason, evidence, and source message")
        return self


class ProviderNextQuestion(StrictProviderModel):
    target_field: ProfileField
    text: Annotated[str, Field(min_length=1, max_length=300)]
    reason: Annotated[str, Field(min_length=1, max_length=300)]
    priority: Annotated[int, Field(ge=1, le=5)]
    alternatives: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=300)]], Field(max_length=3)
    ]
    skippable: bool


class ProviderExtractionPayload(StrictProviderModel):
    language: Annotated[str, Field(min_length=2, max_length=12)]
    opportunity: ProviderOpportunity
    fields: Annotated[list[ProviderField], Field(max_length=60)]
    signals: Annotated[list[ProviderSignal], Field(max_length=40)]
    intent: ProviderIntent
    urgency: ProviderUrgency
    missing_information: Annotated[list[ProfileField], Field(max_length=40)]
    controls: Annotated[list[ProviderControl], Field(max_length=20)]
    handoff: ProviderHandoff
    next_question: ProviderNextQuestion | None
    summary: Annotated[str, Field(min_length=1, max_length=1_000)]
    overall_confidence: Confidence
    warnings: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=300)]], Field(max_length=20)
    ]
    recoverable_errors: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=300)]], Field(max_length=20)
    ]

    @model_validator(mode="after")
    def validate_unique_missing_fields(self) -> Self:
        if len(set(self.missing_information)) != len(self.missing_information):
            raise ValueError("missing information fields must be unique")
        return self


def validate_provider_payload(
    payload: object,
) -> tuple[ProviderExtractionPayload, ValidatedExtraction]:
    try:
        serializable = dict(payload) if isinstance(payload, Mapping) else payload
        encoded = json.dumps(serializable, ensure_ascii=True)
        parsed = ProviderExtractionPayload.model_validate_json(encoded)
    except (TypeError, ValueError) as exc:
        raise ExtractionInvalidResponseError(
            "provider returned an invalid structured payload"
        ) from exc

    canonical = parsed.model_dump(mode="json")
    return parsed, ValidatedExtraction(
        payload=canonical,
        validations=("strict_schema", "known_enumerations", "bounded_values"),
    )
