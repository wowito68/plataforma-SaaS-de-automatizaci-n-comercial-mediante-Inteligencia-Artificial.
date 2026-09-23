import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from saas_platform.modules.lead_qualification.domain import (
    ExtractionMethod,
    FactConflict,
    FactSource,
    LeadProfile,
    Opportunity,
    OpportunityType,
    ProfileFact,
    ProfileField,
    ValidationStatus,
)
from saas_platform.modules.telecom_extraction.domain import (
    AcceptedExtraction,
    ConfidenceTreatment,
    ProviderFieldState,
    ResolvedField,
)

CORRECTION_PATTERN = re.compile(
    r"\b(?:en\s+realidad|mi\s+maximo\s+real|mi\s+m[aá]ximo\s+real|"
    r"correcci[oó]n|me\s+equivoqu[eé]|quise\s+decir|realmente)\b",
    re.IGNORECASE,
)
AI_FORBIDDEN_FIELDS = frozenset({ProfileField.COVERAGE, ProfileField.INVENTORY})


class ProfileValueAction(StrEnum):
    NEW_CURRENT = "new_current"
    REINFORCE_CURRENT = "reinforce_current"
    SUPERSEDE_CURRENT = "supersede_current"
    HISTORICAL_ONLY = "historical_only"
    DUPLICATE = "duplicate"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class ProfileValueDecision:
    candidate: ProfileFact
    action: ProfileValueAction
    previous: ProfileFact | None
    conflict_reason: str | None = None

    @property
    def makes_current(self) -> bool:
        return self.action in {
            ProfileValueAction.NEW_CURRENT,
            ProfileValueAction.REINFORCE_CURRENT,
            ProfileValueAction.SUPERSEDE_CURRENT,
        }

    @property
    def creates_conflict(self) -> bool:
        return self.conflict_reason is not None and self.previous is not None


@dataclass(frozen=True, slots=True)
class ProfileApplicationPlan:
    decisions: tuple[ProfileValueDecision, ...]
    opportunity: Opportunity
    missing_information: tuple[ProfileField, ...]
    updated_profile: LeadProfile

    @property
    def changed_fields(self) -> tuple[ProfileField, ...]:
        return tuple(item.candidate.field for item in self.decisions if item.makes_current)

    @property
    def conflict_count(self) -> int:
        return sum(item.creates_conflict for item in self.decisions)

    @property
    def forbidden_fields(self) -> tuple[ProfileField, ...]:
        return tuple(
            item.candidate.field
            for item in self.decisions
            if item.action is ProfileValueAction.FORBIDDEN
        )


class IncrementalLeadProfileApplier:
    """Resolve profile history without giving the extractor overwrite authority."""

    def plan(
        self,
        profile: LeadProfile,
        extraction: AcceptedExtraction,
        *,
        message_content: str,
        applied_at: datetime,
    ) -> ProfileApplicationPlan:
        decisions = tuple(
            self._decide(
                profile.fact(item.value.field),
                item,
                explicit_correction=bool(CORRECTION_PATTERN.search(message_content)),
            )
            for item in self._candidates(extraction)
        )
        facts = dict(profile.facts)
        conflicts = list(profile.conflicts)
        for decision in decisions:
            if decision.makes_current:
                facts[decision.candidate.field] = decision.candidate
            if decision.creates_conflict:
                assert decision.previous is not None
                conflicts.append(
                    FactConflict(
                        field=decision.candidate.field,
                        previous=decision.previous,
                        current=decision.candidate,
                        reason=decision.conflict_reason or "conflict",
                        detected_at=applied_at,
                    )
                )
        opportunity = self._opportunity(profile.opportunity, extraction)
        updated = LeadProfile(
            tenant_id=profile.tenant_id,
            lead_id=profile.lead_id,
            conversation_id=profile.conversation_id,
            opportunity=opportunity,
            facts=facts,
            conflicts=tuple(conflicts),
            missing_information=extraction.missing_information,
        )
        return ProfileApplicationPlan(
            decisions=decisions,
            opportunity=opportunity,
            missing_information=extraction.missing_information,
            updated_profile=updated,
        )

    @staticmethod
    def _candidates(extraction: AcceptedExtraction) -> tuple[ResolvedField, ...]:
        contradicted = tuple(
            item
            for item in extraction.rejected_fields
            if item.treatment is ConfidenceTreatment.CONTRADICTED
        )
        return extraction.fields + contradicted

    @staticmethod
    def _fact(item: ResolvedField, *, corrected: bool = False) -> ProfileFact:
        if corrected or item.treatment is ConfidenceTreatment.AUTO_ACCEPT:
            status = ValidationStatus.ACCEPTED
        elif item.treatment is ConfidenceTreatment.PROVISIONAL:
            status = ValidationStatus.HYPOTHESIS
        else:
            status = ValidationStatus.REJECTED
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

    def _decide(
        self,
        existing: ProfileFact | None,
        item: ResolvedField,
        *,
        explicit_correction: bool,
    ) -> ProfileValueDecision:
        candidate = self._fact(item)
        if candidate.field in AI_FORBIDDEN_FIELDS:
            return ProfileValueDecision(candidate, ProfileValueAction.FORBIDDEN, existing)
        if existing is None:
            if item.treatment in {
                ConfidenceTreatment.AUTO_ACCEPT,
                ConfidenceTreatment.PROVISIONAL,
            }:
                return ProfileValueDecision(candidate, ProfileValueAction.NEW_CURRENT, None)
            return ProfileValueDecision(candidate, ProfileValueAction.HISTORICAL_ONLY, None)
        if (
            existing.source_message_id == candidate.source_message_id
            and existing.value == candidate.value
        ):
            return ProfileValueDecision(candidate, ProfileValueAction.DUPLICATE, existing)
        if existing.value == candidate.value:
            stronger = candidate.confidence > existing.confidence or (
                existing.validation_status is ValidationStatus.HYPOTHESIS
                and candidate.validation_status is ValidationStatus.ACCEPTED
            )
            action = (
                ProfileValueAction.REINFORCE_CURRENT
                if stronger
                else ProfileValueAction.HISTORICAL_ONLY
            )
            return ProfileValueDecision(candidate, action, existing)

        correction_applies = (
            explicit_correction
            and item.value.provider_state is ProviderFieldState.OBSERVED
            and existing.validation_status is not ValidationStatus.CONFIRMED
        )
        if correction_applies:
            corrected = self._fact(item, corrected=True)
            return ProfileValueDecision(
                corrected,
                ProfileValueAction.SUPERSEDE_CURRENT,
                existing,
                "explicit_customer_correction_applied",
            )
        if existing.validation_status is ValidationStatus.CONFIRMED:
            return ProfileValueDecision(
                candidate,
                ProfileValueAction.HISTORICAL_ONLY,
                existing,
                "confirmed_value_preserved_human_review_required",
            )
        if (
            candidate.confidence > existing.confidence
            and item.value.provider_state is ProviderFieldState.OBSERVED
            and candidate.validation_status is ValidationStatus.ACCEPTED
        ):
            return ProfileValueDecision(
                candidate,
                ProfileValueAction.SUPERSEDE_CURRENT,
                existing,
                "higher_confidence_observed_value_applied",
            )
        return ProfileValueDecision(
            candidate,
            ProfileValueAction.HISTORICAL_ONLY,
            existing,
            "existing_value_preserved_clarification_required",
        )

    @staticmethod
    def _opportunity(current: Opportunity, extraction: AcceptedExtraction) -> Opportunity:
        proposed = extraction.opportunity.opportunity
        if proposed.primary is OpportunityType.UNIDENTIFIED:
            return current
        if current.primary is OpportunityType.UNIDENTIFIED:
            return proposed
        if proposed.primary is current.primary:
            return Opportunity(
                current.primary,
                tuple(dict.fromkeys(current.secondary + proposed.secondary)),
            )
        secondary = tuple(
            item
            for item in dict.fromkeys(current.secondary + (proposed.primary,) + proposed.secondary)
            if item is not current.primary
        )
        return Opportunity(current.primary, secondary)
