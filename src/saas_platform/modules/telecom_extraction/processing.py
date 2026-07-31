import re
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from saas_platform.modules.lead_qualification.domain import (
    CommercialSignal,
    ExtractionMetadata,
    ExtractionMethod,
    FactConflict,
    FactSource,
    LeadProfile,
    Opportunity,
    OpportunityType,
    ProfileFact,
    ProfileFactValue,
    ProfileField,
    QualificationCause,
    QualificationRequest,
    SignalCode,
    ValidationStatus,
)
from saas_platform.modules.telecom_extraction.domain import (
    AcceptedExtraction,
    CommercialIntent,
    CommercialUrgency,
    ConfidencePolicy,
    ConfidenceTreatment,
    ConversationMessage,
    Currency,
    ExtractedOpportunity,
    ExtractionContradiction,
    ExtractionFieldStatus,
    HandoffKind,
    HandoffSuggestion,
    NormalizedExtraction,
    NormalizedField,
    NormalizedSignal,
    ProfileUpdateProposal,
    ProviderFieldState,
    ResolvedField,
    ResolvedSignal,
    SpecialControl,
    SpecialControlFinding,
    SuggestedQuestion,
    TelecomExtractionCommand,
)
from saas_platform.modules.telecom_extraction.errors import ExtractionInvalidResponseError
from saas_platform.modules.telecom_extraction.schema import ProviderExtractionPayload

MONEY_FIELDS = frozenset(
    {
        ProfileField.MENTIONED_PRICE,
        ProfileField.DEVICE_BUDGET,
        ProfileField.MONTHLY_BUDGET,
        ProfileField.DOWN_PAYMENT,
        ProfileField.MAX_MONTHLY_PAYMENT,
    }
)
INTEGER_FIELDS = frozenset(
    {
        ProfileField.QUANTITY,
        ProfileField.PREFERRED_TERM,
        ProfileField.LINE_COUNT,
        ProfileField.BUSINESS_SIZE,
        ProfileField.EQUIPMENT_COUNT,
    }
)
BOOLEAN_FIELDS = frozenset(
    {
        ProfileField.CALLS_REQUIRED,
        ProfileField.MESSAGES_REQUIRED,
        ProfileField.SOCIAL_NETWORKS_REQUIRED,
        ProfileField.ROAMING_REQUIRED,
        ProfileField.INTERNATIONAL_CALLS_REQUIRED,
        ProfileField.DEVICE_INCLUDED,
        ProfileField.PORTABILITY,
        ProfileField.KEEP_NUMBER,
        ProfileField.INVOICING_REQUIRED,
        ProfileField.CENTRALIZED_ADMINISTRATION,
    }
)
DATE_FIELDS = frozenset({ProfileField.CUTOFF_DATE})
PROVIDER_FORBIDDEN_FIELDS = frozenset({ProfileField.COVERAGE, ProfileField.INVENTORY})
PROVIDER_FORBIDDEN_SIGNALS = frozenset(
    {
        SignalCode.PLAN_COMPATIBLE,
        SignalCode.DEVICE_COMPATIBLE,
        SignalCode.COVERAGE_CONFIRMED,
        SignalCode.COMMERCIAL_REQUIREMENTS_COMPATIBLE,
        SignalCode.BUDGET_INCOMPATIBLE,
        SignalCode.BUDGET_COMPATIBLE,
        SignalCode.BUDGET_INCOMPATIBLE_NO_ALTERNATIVES,
        SignalCode.NO_COVERAGE,
        SignalCode.DEVICE_OUT_OF_STOCK,
        SignalCode.PROLONGED_INACTIVITY,
        SignalCode.CONTRADICTORY_INFORMATION,
        SignalCode.DO_NOT_CONTACT,
    }
)
FORBIDDEN_SUMMARY_PATTERN = re.compile(
    r"(?:lead\s*score|score|puntuaci[oó]n)\s*[:=]?\s*\d|"
    r"(?:elegibilidad|financiamiento)\s+(?:aprobada|aprobado)",
    re.IGNORECASE,
)
SENSITIVE_QUESTION_PATTERN = re.compile(
    r"\b(?:telefono|teléfono|email|correo|contraseña|password|tarjeta|cvv|nip|rfc|curp|ine)\b",
    re.IGNORECASE,
)


class ExtractionNormalizer:
    def normalize(
        self,
        payload: ProviderExtractionPayload,
        messages: tuple[ConversationMessage, ...],
        deterministic_controls: tuple[SpecialControlFinding, ...],
    ) -> NormalizedExtraction:
        by_id = {message.id: message for message in messages}
        self._validate_references(payload, by_id)
        warnings = list(payload.warnings)

        fields: list[NormalizedField] = []
        for field_item in payload.fields:
            if field_item.field in PROVIDER_FORBIDDEN_FIELDS:
                warnings.append(f"blocked_authoritative_field:{field_item.field.value}")
                continue
            fields.append(
                NormalizedField(
                    field=field_item.field,
                    value=self._normalize_value(
                        field_item.field,
                        field_item.value,
                        field_item.currency,
                    ),
                    original_text=field_item.original_text.strip(),
                    source_message_id=field_item.message_id,
                    evidence=field_item.evidence.strip(),
                    provider_state=field_item.state,
                    declared_confidence=Decimal(str(field_item.confidence)),
                    currency=field_item.currency,
                    observed_at=by_id[field_item.message_id].sent_at,
                )
            )

        signals: list[NormalizedSignal] = []
        for signal_item in payload.signals:
            if signal_item.code in PROVIDER_FORBIDDEN_SIGNALS:
                warnings.append(f"blocked_authoritative_signal:{signal_item.code.value}")
                continue
            signals.append(
                NormalizedSignal(
                    code=signal_item.code,
                    source_message_id=signal_item.message_id,
                    evidence=signal_item.evidence.strip(),
                    declared_confidence=Decimal(str(signal_item.confidence)),
                    observed_at=by_id[signal_item.message_id].sent_at,
                )
            )

        controls = list(deterministic_controls)
        existing_controls = {item.control for item in controls}
        for control_item in payload.controls:
            if control_item.control in existing_controls:
                continue
            controls.append(
                SpecialControlFinding(
                    control=control_item.control,
                    confidence=self._evidence_confidence(
                        control_item.confidence,
                        control_item.evidence,
                        control_item.message_id,
                        by_id,
                    ),
                    evidence=control_item.evidence.strip(),
                    source_message_id=control_item.message_id,
                )
            )

        question = payload.next_question
        summary = payload.summary
        if FORBIDDEN_SUMMARY_PATTERN.search(summary):
            warnings.append("blocked_authoritative_summary")
            summary = "Commercial facts extracted; authoritative decisions remain pending."

        opportunity_message = (
            payload.opportunity.message_id if payload.opportunity.message_id in by_id else None
        )
        opportunity_confidence = self._evidence_confidence(
            payload.opportunity.confidence,
            payload.opportunity.evidence,
            opportunity_message,
            by_id,
        )
        intent_confidence = self._evidence_confidence(
            payload.intent.confidence,
            payload.intent.evidence,
            payload.intent.message_id,
            by_id,
        )
        urgency_confidence = self._evidence_confidence(
            payload.urgency.confidence,
            payload.urgency.evidence,
            payload.urgency.message_id,
            by_id,
        )
        handoff_confidence = self._evidence_confidence(
            payload.handoff.confidence,
            payload.handoff.evidence,
            payload.handoff.message_id,
            by_id,
        )
        return NormalizedExtraction(
            language=payload.language.lower(),
            opportunity=ExtractedOpportunity(
                opportunity=Opportunity(
                    primary=payload.opportunity.primary,
                    secondary=tuple(payload.opportunity.secondary),
                ),
                confidence=opportunity_confidence,
                evidence=payload.opportunity.evidence.strip(),
                source_message_id=opportunity_message,
            ),
            fields=tuple(fields),
            signals=tuple(signals),
            intent=payload.intent.value,
            intent_confidence=intent_confidence,
            intent_evidence=payload.intent.evidence.strip(),
            intent_source_message_id=payload.intent.message_id,
            urgency=payload.urgency.value,
            urgency_confidence=urgency_confidence,
            urgency_evidence=payload.urgency.evidence.strip(),
            urgency_source_message_id=payload.urgency.message_id,
            provider_missing_information=tuple(payload.missing_information),
            controls=tuple(controls),
            provider_handoff=payload.handoff.kind,
            provider_handoff_reason=payload.handoff.reason.strip(),
            provider_handoff_confidence=handoff_confidence,
            provider_question_target=question.target_field if question else None,
            provider_question=question.text.strip() if question else None,
            provider_question_reason=question.reason.strip() if question else None,
            provider_question_priority=question.priority if question else None,
            provider_question_alternatives=(tuple(question.alternatives) if question else ()),
            provider_question_skippable=question.skippable if question else True,
            summary=summary.strip(),
            declared_overall_confidence=Decimal(str(payload.overall_confidence)),
            warnings=tuple(dict.fromkeys(warnings)),
            recoverable_errors=tuple(payload.recoverable_errors),
        )

    @staticmethod
    def _validate_references(
        payload: ProviderExtractionPayload,
        messages: dict[UUID, ConversationMessage],
    ) -> None:
        references = [item.message_id for item in payload.fields]
        references += [item.message_id for item in payload.signals]
        references += [item.message_id for item in payload.controls]
        if payload.intent.message_id is not None:
            references.append(payload.intent.message_id)
        if payload.urgency.message_id is not None:
            references.append(payload.urgency.message_id)
        if payload.handoff.message_id is not None:
            references.append(payload.handoff.message_id)
        if payload.opportunity.message_id is not None:
            references.append(payload.opportunity.message_id)
        if any(reference not in messages for reference in references):
            raise ExtractionInvalidResponseError(
                "provider referenced a message outside the prepared tenant context"
            )

    @staticmethod
    def _evidence_confidence(
        declared: float,
        evidence: str,
        message_id: UUID | None,
        messages: dict[UUID, ConversationMessage],
    ) -> Decimal:
        confidence = Decimal(str(declared))
        if message_id is None:
            return min(confidence, Decimal("0.39")) if evidence else confidence
        if evidence.casefold() not in messages[message_id].content.casefold():
            confidence -= Decimal("0.20")
        return min(Decimal("1"), max(Decimal("0"), confidence))

    @staticmethod
    def _normalize_value(
        field: ProfileField,
        value: str | int | float | bool,
        currency: Currency | None,
    ) -> ProfileFactValue:
        if field in MONEY_FIELDS:
            if currency is None:
                raise ExtractionInvalidResponseError("monetary fields require an explicit currency")
            if isinstance(value, bool):
                raise ExtractionInvalidResponseError("monetary fields require a numeric value")
            try:
                normalized_money = Decimal(str(value))
            except InvalidOperation as exc:
                raise ExtractionInvalidResponseError(
                    "monetary fields require a numeric value"
                ) from exc
            if normalized_money < 0:
                raise ExtractionInvalidResponseError("monetary values cannot be negative")
            return normalized_money
        if currency is not None:
            raise ExtractionInvalidResponseError("currency is only valid for monetary fields")
        if field in INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ExtractionInvalidResponseError(
                    "quantity and term fields require non-negative integers"
                )
            return value
        if field in BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                raise ExtractionInvalidResponseError("boolean commercial fields require booleans")
            return value
        if field in DATE_FIELDS:
            if not isinstance(value, str):
                raise ExtractionInvalidResponseError("date fields require ISO date strings")
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError as exc:
                raise ExtractionInvalidResponseError("date fields require ISO dates") from exc
        if isinstance(value, str):
            normalized = " ".join(value.split())
            if not normalized:
                raise ExtractionInvalidResponseError("commercial text values cannot be empty")
            if field is ProfileField.BRAND:
                return normalized.title()
            if field in {
                ProfileField.PAYMENT_METHOD,
                ProfileField.SERVICE_MODALITY,
                ProfileField.CUSTOMER_TYPE,
            }:
                return normalized.lower()
            return normalized
        if isinstance(value, (bool, int)):
            return value
        return Decimal(str(value))


class ConfidenceResolver:
    def __init__(self, policy: ConfidencePolicy) -> None:
        self._policy = policy

    def resolve_fields(
        self,
        extraction: NormalizedExtraction,
        messages: tuple[ConversationMessage, ...],
    ) -> tuple[ResolvedField, ...]:
        contents = {message.id: message.content.casefold() for message in messages}
        support: dict[tuple[ProfileField, str], int] = defaultdict(int)
        for item in extraction.fields:
            support[(item.field, str(item.value))] += 1

        resolved = []
        for item in extraction.fields:
            confidence = item.declared_confidence
            reasons: list[str] = []
            if item.evidence.casefold() in contents[item.source_message_id]:
                reasons.append("evidence_verified")
            else:
                confidence -= Decimal("0.20")
                reasons.append("evidence_not_verbatim")
            if item.provider_state is ProviderFieldState.INFERRED:
                confidence -= Decimal("0.15")
                reasons.append("inference_penalty")
            elif item.provider_state is ProviderFieldState.UNKNOWN:
                confidence = min(confidence, Decimal("0.39"))
                reasons.append("unknown_value")
            if support[(item.field, str(item.value))] > 1:
                confidence += Decimal("0.05")
                reasons.append("multiple_supporting_messages")
            confidence = min(Decimal("1"), max(Decimal("0"), confidence))
            treatment, status = self._field_treatment(confidence, item.provider_state)
            resolved.append(
                ResolvedField(
                    value=item,
                    confidence=confidence,
                    status=status,
                    treatment=treatment,
                    reasons=tuple(reasons),
                )
            )
        return tuple(resolved)

    def resolve_signals(
        self,
        extraction: NormalizedExtraction,
        messages: tuple[ConversationMessage, ...],
    ) -> tuple[ResolvedSignal, ...]:
        contents = {message.id: message.content.casefold() for message in messages}
        resolved = []
        for item in extraction.signals:
            confidence = item.declared_confidence
            if item.evidence.casefold() not in contents[item.source_message_id]:
                confidence -= Decimal("0.20")
            confidence = min(Decimal("1"), max(Decimal("0"), confidence))
            treatment = (
                ConfidenceTreatment.AUTO_ACCEPT
                if confidence >= self._policy.auto_accept
                else ConfidenceTreatment.PROVISIONAL
                if confidence >= self._policy.provisional
                else ConfidenceTreatment.CONFIRMATION_REQUIRED
                if confidence >= self._policy.confirmation_required
                else ConfidenceTreatment.REJECTED
            )
            resolved.append(ResolvedSignal(item, confidence, treatment))
        return tuple(resolved)

    def _field_treatment(
        self,
        confidence: Decimal,
        provider_state: ProviderFieldState,
    ) -> tuple[ConfidenceTreatment, ExtractionFieldStatus]:
        if confidence >= self._policy.auto_accept and provider_state is ProviderFieldState.OBSERVED:
            return ConfidenceTreatment.AUTO_ACCEPT, ExtractionFieldStatus.OBSERVED
        if confidence >= self._policy.provisional:
            return ConfidenceTreatment.PROVISIONAL, ExtractionFieldStatus.INFERRED
        if confidence >= self._policy.confirmation_required:
            return ConfidenceTreatment.CONFIRMATION_REQUIRED, ExtractionFieldStatus.UNKNOWN
        return ConfidenceTreatment.REJECTED, ExtractionFieldStatus.REJECTED


class ContradictionReconciler:
    def reconcile(
        self,
        resolved: tuple[ResolvedField, ...],
        profile: LeadProfile,
    ) -> tuple[tuple[ResolvedField, ...], tuple[ExtractionContradiction, ...]]:
        grouped: dict[ProfileField, list[ResolvedField]] = defaultdict(list)
        for item in resolved:
            grouped[item.value.field].append(item)

        output: list[ResolvedField] = []
        contradictions: list[ExtractionContradiction] = []
        for field, candidates in grouped.items():
            by_value: dict[str, ResolvedField] = {}
            for item in sorted(candidates, key=lambda value: value.value.observed_at):
                current = by_value.get(str(item.value.value))
                if current is None or item.confidence >= current.confidence:
                    by_value[str(item.value.value)] = item
            unique = sorted(by_value.values(), key=lambda value: value.value.observed_at)
            latest = unique[-1]
            if len(unique) > 1:
                previous = unique[-2]
                contradictions.append(self._between_extractions(field, previous, latest))
                output.append(self._contradicted(latest))
                continue

            existing = profile.fact(field)
            if existing is not None and existing.value != latest.value.value:
                contradictions.append(self._against_profile(existing, latest))
                output.append(self._contradicted(latest))
                continue
            output.append(latest)
        return tuple(output), tuple(contradictions)

    @staticmethod
    def _contradicted(value: ResolvedField) -> ResolvedField:
        return replace(
            value,
            confidence=max(Decimal("0"), value.confidence - Decimal("0.25")),
            status=ExtractionFieldStatus.CONTRADICTED,
            treatment=ConfidenceTreatment.CONTRADICTED,
            reasons=value.reasons + ("contradiction_detected",),
        )

    @staticmethod
    def _between_extractions(
        field: ProfileField,
        previous: ResolvedField,
        current: ResolvedField,
    ) -> ExtractionContradiction:
        return ExtractionContradiction(
            field=field,
            previous_value=previous.value.value,
            new_value=current.value.value,
            previous_source_message_id=previous.value.source_message_id,
            new_source_message_id=current.value.source_message_id,
            previous_evidence=previous.value.evidence,
            new_evidence=current.value.evidence,
            confidence=min(previous.confidence, current.confidence),
            resolution="clarification_required; neither value replaces the other",
            clarification_question=f"Could you confirm the current value for {field.value}?",
        )

    @staticmethod
    def _against_profile(
        previous: ProfileFact,
        current: ResolvedField,
    ) -> ExtractionContradiction:
        protected = previous.validation_status is ValidationStatus.CONFIRMED
        return ExtractionContradiction(
            field=previous.field,
            previous_value=previous.value,
            new_value=current.value.value,
            previous_source_message_id=previous.source_message_id,
            new_source_message_id=current.value.source_message_id,
            previous_evidence=None,
            new_evidence=current.value.evidence,
            confidence=current.confidence,
            resolution=(
                "confirmed value preserved; human clarification required"
                if protected
                else "existing value preserved; clarification required"
            ),
            clarification_question=(
                f"Could you confirm the current value for {previous.field.value}?"
            ),
        )


REQUIRED_FIELDS: dict[OpportunityType, tuple[ProfileField, ...]] = {
    OpportunityType.DEVICE_PURCHASE: (
        ProfileField.BRAND,
        ProfileField.DEVICE_BUDGET,
        ProfileField.PAYMENT_METHOD,
        ProfileField.PURCHASE_TIMEFRAME,
    ),
    OpportunityType.DEVICE_WITH_PLAN: (
        ProfileField.BRAND,
        ProfileField.DEVICE_BUDGET,
        ProfileField.MONTHLY_BUDGET,
        ProfileField.DATA_USAGE,
        ProfileField.LINE_COUNT,
        ProfileField.PURCHASE_TIMEFRAME,
    ),
    OpportunityType.PLAN_SUBSCRIPTION: (
        ProfileField.MONTHLY_BUDGET,
        ProfileField.DATA_USAGE,
        ProfileField.PORTABILITY,
        ProfileField.LINE_COUNT,
    ),
    OpportunityType.NEW_LINE: (
        ProfileField.MONTHLY_BUDGET,
        ProfileField.DATA_USAGE,
        ProfileField.LINE_COUNT,
        ProfileField.PURCHASE_TIMEFRAME,
    ),
    OpportunityType.PORTABILITY: (
        ProfileField.KEEP_NUMBER,
        ProfileField.CURRENT_OPERATOR,
        ProfileField.PURCHASE_TIMEFRAME,
        ProfileField.MONTHLY_BUDGET,
    ),
    OpportunityType.RENEWAL: (
        ProfileField.CURRENT_OPERATOR,
        ProfileField.PLAN,
        ProfileField.PURCHASE_TIMEFRAME,
    ),
    OpportunityType.PREPAID_TO_POSTPAID: (
        ProfileField.CURRENT_OPERATOR,
        ProfileField.MONTHLY_BUDGET,
        ProfileField.DATA_USAGE,
    ),
    OpportunityType.ACCESSORY: (
        ProfileField.PRODUCT_CATEGORY,
        ProfileField.DEVICE_BUDGET,
        ProfileField.PURCHASE_TIMEFRAME,
    ),
    OpportunityType.MULTIPLE_LINES: (
        ProfileField.LINE_COUNT,
        ProfileField.MONTHLY_BUDGET,
        ProfileField.PURCHASE_TIMEFRAME,
    ),
    OpportunityType.BUSINESS_ACCOUNT: (
        ProfileField.LINE_COUNT,
        ProfileField.DEVICE_INCLUDED,
        ProfileField.PURCHASE_TIMEFRAME,
        ProfileField.CONTACT_ROLE,
        ProfileField.REQUESTED_ACTION,
    ),
    OpportunityType.UNIDENTIFIED: (ProfileField.REQUESTED_ACTION,),
}

QUESTION_TEXT: dict[ProfileField, str] = {
    ProfileField.BRAND: "Do you have a preferred brand, or what will you use the device for?",
    ProfileField.DEVICE_BUDGET: "What is your maximum budget for the device?",
    ProfileField.MONTHLY_BUDGET: "What monthly budget would you like to stay within?",
    ProfileField.PAYMENT_METHOD: "Would you prefer cash payment or financing?",
    ProfileField.PURCHASE_TIMEFRAME: "When would you like to complete the purchase?",
    ProfileField.DATA_USAGE: "How much mobile data do you expect to use?",
    ProfileField.PORTABILITY: "Would this be a new line or a portability request?",
    ProfileField.KEEP_NUMBER: "Would you like to keep your current number?",
    ProfileField.CURRENT_OPERATOR: "Which operator do you currently use?",
    ProfileField.LINE_COUNT: "How many lines do you need?",
    ProfileField.DEVICE_INCLUDED: "Will the lines also require devices?",
    ProfileField.CONTACT_ROLE: "What is your role in this purchase decision?",
    ProfileField.REQUESTED_ACTION: "What would you like us to help you with next?",
    ProfileField.PRODUCT_CATEGORY: "What type of accessory are you looking for?",
    ProfileField.PLAN: "Do you have a plan in mind?",
}


class ExtractionDecisionService:
    def __init__(self, confidence_policy: ConfidencePolicy) -> None:
        self._confidence_policy = confidence_policy

    def accept(
        self,
        normalized: NormalizedExtraction,
        fields: tuple[ResolvedField, ...],
        contradictions: tuple[ExtractionContradiction, ...],
        signals: tuple[ResolvedSignal, ...],
        command: TelecomExtractionCommand,
    ) -> AcceptedExtraction:
        controls = self._accepted_controls(normalized.controls)
        dnc = any(item.control is SpecialControl.DO_NOT_CONTACT for item in controls)
        accepted_fields = tuple(
            item
            for item in fields
            if item.treatment in {ConfidenceTreatment.AUTO_ACCEPT, ConfidenceTreatment.PROVISIONAL}
        )
        rejected_fields = tuple(item for item in fields if item not in accepted_fields)
        accepted_signals = tuple(
            item for item in signals if item.treatment is ConfidenceTreatment.AUTO_ACCEPT
        )
        rejected_signals = tuple(item for item in signals if item not in accepted_signals)

        opportunity = normalized.opportunity
        if opportunity.confidence < self._confidence_policy.provisional:
            opportunity = ExtractedOpportunity(
                opportunity=Opportunity(OpportunityType.UNIDENTIFIED),
                confidence=opportunity.confidence,
                evidence=opportunity.evidence,
                source_message_id=opportunity.source_message_id,
            )
        missing = self._missing(opportunity.opportunity.primary, command.profile, accepted_fields)
        handoff = self._handoff(normalized, controls, accepted_fields, dnc)
        question = self._question(normalized, missing, contradictions, controls, command)
        overall = self._overall(normalized.declared_overall_confidence, accepted_fields)
        intent_treatment = self._finding_treatment(normalized.intent_confidence)
        urgency_treatment = self._finding_treatment(normalized.urgency_confidence)
        return AcceptedExtraction(
            opportunity=opportunity,
            fields=accepted_fields,
            rejected_fields=rejected_fields,
            signals=accepted_signals,
            rejected_signals=rejected_signals,
            controls=controls,
            contradictions=contradictions,
            missing_information=missing,
            next_question=question,
            handoff=handoff,
            intent=normalized.intent,
            intent_treatment=intent_treatment,
            urgency=normalized.urgency,
            urgency_treatment=urgency_treatment,
            summary=normalized.summary,
            overall_confidence=overall,
        )

    def _accepted_controls(
        self,
        controls: tuple[SpecialControlFinding, ...],
    ) -> tuple[SpecialControlFinding, ...]:
        accepted = [
            item
            for item in controls
            if item.deterministic or item.confidence >= self._confidence_policy.auto_accept
        ]
        by_control: dict[SpecialControl, SpecialControlFinding] = {}
        for item in accepted:
            existing = by_control.get(item.control)
            if existing is None or item.confidence > existing.confidence:
                by_control[item.control] = item
        return tuple(by_control.values())

    @staticmethod
    def _missing(
        opportunity: OpportunityType,
        profile: LeadProfile,
        fields: tuple[ResolvedField, ...],
    ) -> tuple[ProfileField, ...]:
        known = set(profile.facts)
        known.update(item.value.field for item in fields)
        return tuple(item for item in REQUIRED_FIELDS[opportunity] if item not in known)

    def _handoff(
        self,
        normalized: NormalizedExtraction,
        controls: tuple[SpecialControlFinding, ...],
        fields: tuple[ResolvedField, ...],
        dnc: bool,
    ) -> HandoffSuggestion:
        control_set = {item.control for item in controls}
        if dnc:
            return HandoffSuggestion(HandoffKind.NONE, ("do_not_contact",))
        if SpecialControl.HUMAN_REQUESTED in control_set:
            return HandoffSuggestion(HandoffKind.GENERAL, ("customer_requested_human",))
        if control_set & {SpecialControl.SUSPECTED_FRAUD, SpecialControl.COMPLAINT}:
            return HandoffSuggestion(HandoffKind.SPECIALIZED, ("special_control",))
        opportunity = normalized.opportunity.opportunity.primary
        line_count = next(
            (
                item.value.value
                for item in fields
                if item.value.field is ProfileField.LINE_COUNT and isinstance(item.value.value, int)
            ),
            0,
        )
        if opportunity is OpportunityType.BUSINESS_ACCOUNT or line_count >= 10:
            return HandoffSuggestion(HandoffKind.SPECIALIZED, ("business_or_volume",))
        if (
            opportunity is OpportunityType.PORTABILITY
            and normalized.urgency
            in {
                CommercialUrgency.IMMEDIATE,
                CommercialUrgency.THIS_WEEK,
            }
            and normalized.urgency_confidence >= self._confidence_policy.auto_accept
        ):
            return HandoffSuggestion(HandoffKind.GENERAL, ("urgent_portability",))
        if (
            normalized.provider_handoff is not HandoffKind.NONE
            and normalized.provider_handoff_confidence >= self._confidence_policy.auto_accept
        ):
            return HandoffSuggestion(
                normalized.provider_handoff,
                ("validated_provider_suggestion",),
            )
        return HandoffSuggestion(HandoffKind.NONE, ())

    @staticmethod
    def _question(
        normalized: NormalizedExtraction,
        missing: tuple[ProfileField, ...],
        contradictions: tuple[ExtractionContradiction, ...],
        controls: tuple[SpecialControlFinding, ...],
        command: TelecomExtractionCommand,
    ) -> SuggestedQuestion | None:
        control_set = {item.control for item in controls}
        if command.handoff_confirmed or control_set & {
            SpecialControl.DO_NOT_CONTACT,
            SpecialControl.HUMAN_REQUESTED,
        }:
            return None
        recently_asked = set(command.recently_asked_fields)
        eligible_targets = [item.field for item in contradictions]
        eligible_targets += list(missing)
        eligible_targets = [item for item in eligible_targets if item not in recently_asked]
        if not eligible_targets:
            return None
        target = eligible_targets[0]
        if (
            normalized.provider_question_target is target
            and normalized.provider_question
            and normalized.provider_question_reason
            and normalized.provider_question_priority is not None
            and len(normalized.provider_question) <= 180
            and normalized.provider_question.count("?") <= 1
            and not SENSITIVE_QUESTION_PATTERN.search(normalized.provider_question)
        ):
            return SuggestedQuestion(
                target_field=target,
                text=normalized.provider_question,
                reason=normalized.provider_question_reason,
                priority=normalized.provider_question_priority,
                alternatives=normalized.provider_question_alternatives,
                skippable=normalized.provider_question_skippable,
            )
        question = next(
            (QUESTION_TEXT[item] for item in eligible_targets if item in QUESTION_TEXT),
            None,
        )
        if question is None:
            return None
        return SuggestedQuestion(
            target_field=target,
            text=question,
            reason="Resolve the highest-priority relevant missing or conflicting field.",
            priority=1 if contradictions else 2,
            alternatives=(),
            skippable=True,
        )

    @staticmethod
    def _overall(
        declared: Decimal,
        fields: tuple[ResolvedField, ...],
    ) -> Decimal:
        if not fields:
            return min(declared, Decimal("0.50"))
        field_average = sum((item.confidence for item in fields), Decimal("0")) / len(fields)
        return min(Decimal("1"), max(Decimal("0"), (declared + field_average) / 2))

    def _finding_treatment(self, confidence: Decimal) -> ConfidenceTreatment:
        if confidence >= self._confidence_policy.auto_accept:
            return ConfidenceTreatment.AUTO_ACCEPT
        if confidence >= self._confidence_policy.provisional:
            return ConfidenceTreatment.PROVISIONAL
        if confidence >= self._confidence_policy.confirmation_required:
            return ConfidenceTreatment.CONFIRMATION_REQUIRED
        return ConfidenceTreatment.REJECTED


INTENT_SIGNALS = {
    CommercialIntent.EXPLORATION: SignalCode.RESEARCH_ONLY,
    CommercialIntent.COMPARISON: SignalCode.COMPARES_PRODUCTS,
    CommercialIntent.PRICE_INQUIRY: SignalCode.ASKS_PRICE,
    CommercialIntent.MONTHLY_PAYMENT_INQUIRY: SignalCode.ASKS_MONTHLY_PAYMENT,
    CommercialIntent.INVENTORY_INQUIRY: SignalCode.ASKS_AVAILABILITY,
    CommercialIntent.REQUIREMENTS_INQUIRY: SignalCode.ASKS_REQUIREMENTS,
    CommercialIntent.QUOTE_REQUEST: SignalCode.REQUESTS_QUOTE,
    CommercialIntent.PORTABILITY_REQUEST: SignalCode.REQUESTS_PORTABILITY,
    CommercialIntent.CONTRACT: SignalCode.WANTS_CONTRACT_NOW,
    CommercialIntent.PURCHASE: SignalCode.WANTS_CONTRACT_NOW,
}
URGENCY_SIGNALS = {
    CommercialUrgency.IMMEDIATE: SignalCode.IMMEDIATE,
    CommercialUrgency.THIS_WEEK: SignalCode.THIS_WEEK,
    CommercialUrgency.FUTURE: SignalCode.PURCHASE_OVER_THREE_MONTHS,
}


class ExtractionDomainMapper:
    def apply(
        self,
        command: TelecomExtractionCommand,
        extraction: AcceptedExtraction,
        *,
        execution_id: UUID,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        now: datetime,
    ) -> ProfileUpdateProposal:
        facts = dict(command.profile.facts)
        facts_added: list[ProfileField] = []
        facts_preserved: list[ProfileField] = []
        for item in extraction.fields:
            field = item.value.field
            existing = facts.get(field)
            if existing is not None:
                facts_preserved.append(field)
                continue
            status = (
                ValidationStatus.ACCEPTED
                if item.treatment is ConfidenceTreatment.AUTO_ACCEPT
                else ValidationStatus.HYPOTHESIS
            )
            facts[field] = self._fact(item, status)
            facts_added.append(field)

        conflicts = list(command.profile.conflicts)
        for contradiction_item in extraction.contradictions:
            previous = facts.get(contradiction_item.field)
            if previous is None:
                previous = ProfileFact(
                    field=contradiction_item.field,
                    value=contradiction_item.previous_value,
                    source=FactSource.CUSTOMER,
                    source_message_id=contradiction_item.previous_source_message_id,
                    observed_at=now,
                    confidence=contradiction_item.confidence,
                    extraction_method=ExtractionMethod.AI,
                    validation_status=ValidationStatus.HYPOTHESIS,
                )
            current = ProfileFact(
                field=contradiction_item.field,
                value=contradiction_item.new_value,
                source=FactSource.CUSTOMER,
                source_message_id=contradiction_item.new_source_message_id,
                observed_at=now,
                confidence=contradiction_item.confidence,
                extraction_method=ExtractionMethod.AI,
                validation_status=ValidationStatus.REJECTED,
            )
            conflicts.append(
                FactConflict(
                    field=contradiction_item.field,
                    previous=previous,
                    current=current,
                    reason=contradiction_item.resolution,
                    detected_at=now,
                )
            )

        opportunity = command.profile.opportunity
        proposed = extraction.opportunity.opportunity
        if opportunity.primary is OpportunityType.UNIDENTIFIED:
            opportunity = proposed
        elif opportunity.primary is proposed.primary:
            opportunity = Opportunity(
                opportunity.primary,
                tuple(dict.fromkeys(opportunity.secondary + proposed.secondary)),
            )

        updated = LeadProfile(
            tenant_id=command.profile.tenant_id,
            lead_id=command.profile.lead_id,
            conversation_id=command.profile.conversation_id,
            opportunity=opportunity,
            facts=facts,
            conflicts=tuple(conflicts),
            missing_information=extraction.missing_information,
        )
        commercial_signals = self._signals(extraction, now)
        qualification = QualificationRequest(
            profile=updated,
            signals=commercial_signals,
            cause=QualificationCause.PROFILE_UPDATED,
            trigger_id=max(command.messages, key=lambda item: item.sent_at).id,
            correlation_id=command.correlation_id,
            eligibility=command.eligibility,
            extraction=ExtractionMetadata(
                provider=provider,
                model=model,
                prompt_version=prompt_version,
                schema_version=schema_version,
                overall_confidence=extraction.overall_confidence,
            ),
        )
        return ProfileUpdateProposal(
            previous_profile=command.profile,
            updated_profile=updated,
            facts_added=tuple(facts_added),
            facts_preserved=tuple(dict.fromkeys(facts_preserved)),
            conflicts_added=len(conflicts) - len(command.profile.conflicts),
            qualification_request=qualification,
        )

    @staticmethod
    def _fact(item: ResolvedField, status: ValidationStatus) -> ProfileFact:
        return ProfileFact(
            field=item.value.field,
            value=item.value.value,
            source=FactSource.CUSTOMER,
            source_message_id=item.value.source_message_id,
            observed_at=item.value.observed_at,
            confidence=item.confidence,
            extraction_method=ExtractionMethod.AI,
            validation_status=status,
        )

    @staticmethod
    def _signals(
        extraction: AcceptedExtraction,
        now: datetime,
    ) -> tuple[CommercialSignal, ...]:
        candidates: dict[SignalCode, tuple[Decimal, UUID | None, ExtractionMethod]] = {}
        for item in extraction.signals:
            candidates[item.value.code] = (
                item.confidence,
                item.value.source_message_id,
                ExtractionMethod.AI,
            )
        control_map = {
            SpecialControl.DO_NOT_CONTACT: SignalCode.DO_NOT_CONTACT,
            SpecialControl.HUMAN_REQUESTED: SignalCode.REQUESTS_HUMAN,
        }
        for control in extraction.controls:
            code = control_map.get(control.control)
            if code is not None:
                candidates[code] = (
                    control.confidence,
                    control.source_message_id,
                    ExtractionMethod.EXPLICIT if control.deterministic else ExtractionMethod.AI,
                )
        if extraction.contradictions:
            first = extraction.contradictions[0]
            candidates[SignalCode.CONTRADICTORY_INFORMATION] = (
                min(item.confidence for item in extraction.contradictions),
                first.new_source_message_id,
                ExtractionMethod.SYSTEM,
            )
        intent_signal = (
            INTENT_SIGNALS.get(extraction.intent)
            if extraction.intent_treatment is ConfidenceTreatment.AUTO_ACCEPT
            else None
        )
        if intent_signal is not None and extraction.opportunity.source_message_id is not None:
            candidates.setdefault(
                intent_signal,
                (
                    extraction.opportunity.confidence,
                    extraction.opportunity.source_message_id,
                    ExtractionMethod.AI,
                ),
            )
        urgency_signal = (
            URGENCY_SIGNALS.get(extraction.urgency)
            if extraction.urgency_treatment is ConfidenceTreatment.AUTO_ACCEPT
            else None
        )
        if urgency_signal is not None and extraction.opportunity.source_message_id is not None:
            candidates.setdefault(
                urgency_signal,
                (
                    extraction.opportunity.confidence,
                    extraction.opportunity.source_message_id,
                    ExtractionMethod.AI,
                ),
            )
        opportunity_signal = {
            OpportunityType.DEVICE_WITH_PLAN: SignalCode.DEVICE_AND_PLAN,
            OpportunityType.ACCESSORY: SignalCode.ACCESSORY_INTEREST,
            OpportunityType.MULTIPLE_LINES: SignalCode.MULTIPLE_LINES,
            OpportunityType.BUSINESS_ACCOUNT: SignalCode.BUSINESS_ACCOUNT,
        }.get(extraction.opportunity.opportunity.primary)
        if opportunity_signal is not None:
            candidates.setdefault(
                opportunity_signal,
                (
                    extraction.opportunity.confidence,
                    extraction.opportunity.source_message_id,
                    ExtractionMethod.AI,
                ),
            )
        return tuple(
            CommercialSignal(
                code=code,
                confidence=confidence,
                source_message_id=source_message_id,
                observed_at=now,
                extraction_method=method,
                validation_status=ValidationStatus.ACCEPTED,
            )
            for code, (confidence, source_message_id, method) in candidates.items()
        )
