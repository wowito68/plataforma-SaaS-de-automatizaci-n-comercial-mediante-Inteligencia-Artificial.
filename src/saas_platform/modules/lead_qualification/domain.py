import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from saas_platform.errors import ValidationError
from saas_platform.modules.tenancy.domain import TenantId

RULE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")
ENGINE_VERSION = "telecom-scoring.v1"

type ProfileFactValue = str | int | bool | Decimal


def _ensure_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must include timezone information")


def _ensure_ratio(value: Decimal, field_name: str) -> None:
    if value < Decimal("0") or value > Decimal("1"):
        raise ValidationError(f"{field_name} must be between 0 and 1")


def _ensure_rule_code(value: str, field_name: str = "rule code") -> None:
    if not RULE_CODE_PATTERN.fullmatch(value):
        raise ValidationError(f"{field_name} is invalid")


class OpportunityType(StrEnum):
    DEVICE_PURCHASE = "device_purchase"
    DEVICE_WITH_PLAN = "device_with_plan"
    PLAN_SUBSCRIPTION = "plan_subscription"
    NEW_LINE = "new_line"
    PORTABILITY = "portability"
    RENEWAL = "renewal"
    PREPAID_TO_POSTPAID = "prepaid_to_postpaid"
    ACCESSORY = "accessory"
    MULTIPLE_LINES = "multiple_lines"
    BUSINESS_ACCOUNT = "business_account"
    UNIDENTIFIED = "unidentified"


@dataclass(frozen=True, slots=True)
class Opportunity:
    primary: OpportunityType
    secondary: tuple[OpportunityType, ...] = ()

    def __post_init__(self) -> None:
        if self.primary in self.secondary:
            raise ValidationError("primary opportunity cannot also be secondary")
        if len(set(self.secondary)) != len(self.secondary):
            raise ValidationError("secondary opportunities must be unique")


class ProfileField(StrEnum):
    PRODUCT = "product"
    PRODUCT_CATEGORY = "product_category"
    BRAND = "brand"
    MODEL = "model"
    STORAGE_CAPACITY = "storage_capacity"
    COLOR = "color"
    DEVICE_BUDGET = "device_budget"
    MONTHLY_BUDGET = "monthly_budget"
    PAYMENT_METHOD = "payment_method"
    DOWN_PAYMENT = "down_payment"
    MAX_MONTHLY_PAYMENT = "max_monthly_payment"
    PREFERRED_TERM = "preferred_term"
    PLAN = "plan"
    DATA_USAGE = "data_usage"
    CALLS_REQUIRED = "calls_required"
    ROAMING_REQUIRED = "roaming_required"
    PORTABILITY = "portability"
    KEEP_NUMBER = "keep_number"
    CURRENT_OPERATOR = "current_operator"
    PURCHASE_TIMEFRAME = "purchase_timeframe"
    LINE_COUNT = "line_count"
    CUSTOMER_TYPE = "customer_type"
    BUSINESS_SIZE = "business_size"
    LOCATION = "location"
    POSTAL_CODE = "postal_code"
    COVERAGE = "coverage"
    INVENTORY = "inventory"
    REQUESTED_ACTION = "requested_action"
    PREFERRED_CHANNEL = "preferred_channel"
    PREFERRED_BRANCH = "preferred_branch"
    DETECTED_INTENT = "detected_intent"


class FactSource(StrEnum):
    CUSTOMER = "customer"
    SYSTEM_LOOKUP = "system_lookup"
    HUMAN_ADVISOR = "human_advisor"
    IMPORT = "import"


class ExtractionMethod(StrEnum):
    EXPLICIT = "explicit"
    AI = "ai"
    HUMAN = "human"
    SYSTEM = "system"
    IMPORT = "import"


class ValidationStatus(StrEnum):
    HYPOTHESIS = "hypothesis"
    ACCEPTED = "accepted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProfileFact:
    field: ProfileField
    value: ProfileFactValue
    source: FactSource
    source_message_id: UUID | None
    observed_at: datetime
    confidence: Decimal
    extraction_method: ExtractionMethod
    validation_status: ValidationStatus
    confirmed_by: str | None = None

    def __post_init__(self) -> None:
        _ensure_aware(self.observed_at, "fact observed_at")
        _ensure_ratio(self.confidence, "fact confidence")
        if isinstance(self.value, str) and not self.value.strip():
            raise ValidationError("profile fact string value cannot be empty")
        if self.validation_status is ValidationStatus.CONFIRMED and not self.confirmed_by:
            raise ValidationError("confirmed profile facts require confirmed_by")


@dataclass(frozen=True, slots=True)
class FactConflict:
    field: ProfileField
    previous: ProfileFact
    current: ProfileFact
    reason: str
    detected_at: datetime

    def __post_init__(self) -> None:
        if self.previous.field is not self.field or self.current.field is not self.field:
            raise ValidationError("fact conflict values must refer to the same field")
        if not self.reason.strip():
            raise ValidationError("fact conflict requires a reason")
        _ensure_aware(self.detected_at, "conflict detected_at")


@dataclass(frozen=True, slots=True)
class LeadProfile:
    tenant_id: TenantId
    lead_id: UUID
    conversation_id: UUID
    opportunity: Opportunity
    facts: Mapping[ProfileField, ProfileFact]
    conflicts: tuple[FactConflict, ...] = ()
    missing_information: tuple[ProfileField, ...] = ()

    def __post_init__(self) -> None:
        normalized_facts = dict(self.facts)
        for field_name, fact in normalized_facts.items():
            if field_name is not fact.field:
                raise ValidationError("profile fact key must match its field")
        if len(set(self.missing_information)) != len(self.missing_information):
            raise ValidationError("missing profile fields must be unique")
        object.__setattr__(self, "facts", MappingProxyType(normalized_facts))

    def fact(self, field_name: ProfileField) -> ProfileFact | None:
        return self.facts.get(field_name)


class SignalCode(StrEnum):
    NEED_DEFINED = "need_defined"
    PLAN_COMPATIBLE = "plan_compatible"
    DEVICE_COMPATIBLE = "device_compatible"
    COVERAGE_CONFIRMED = "coverage_confirmed"
    COMMERCIAL_REQUIREMENTS_COMPATIBLE = "commercial_requirements_compatible"

    GENERAL_INFORMATION = "general_information"
    ASKS_PRICE = "asks_price"
    ASKS_MONTHLY_PAYMENT = "asks_monthly_payment"
    ASKS_AVAILABILITY = "asks_availability"
    COMPARES_PRODUCTS = "compares_products"
    ASKS_REQUIREMENTS = "asks_requirements"
    REQUESTS_QUOTE = "requests_quote"
    REQUESTS_PORTABILITY = "requests_portability"
    WANTS_CONTRACT_NOW = "wants_contract_now"

    BUDGET_UNSPECIFIED = "budget_unspecified"
    BUDGET_INCOMPATIBLE = "budget_incompatible"
    ACCEPTS_ALTERNATIVES = "accepts_alternatives"
    BUDGET_COMPATIBLE = "budget_compatible"
    BUDGET_AND_PAYMENT_DEFINED = "budget_and_payment_defined"
    BUDGET_INCOMPATIBLE_NO_ALTERNATIVES = "budget_incompatible_no_alternatives"

    RESEARCH_ONLY = "research_only"
    PURCHASE_OVER_THREE_MONTHS = "purchase_over_three_months"
    PURCHASE_OVER_SIX_MONTHS = "purchase_over_six_months"
    NEXT_MONTH = "next_month"
    THIS_WEEK = "this_week"
    IMMEDIATE = "immediate"

    PROVIDES_RELEVANT_INFORMATION = "provides_relevant_information"
    ANSWERS_QUALIFICATION_QUESTIONS = "answers_qualification_questions"
    SUSTAINED_PROGRESS = "sustained_progress"
    ACCEPTS_CALL_OR_BRANCH = "accepts_call_or_branch"

    DEVICE_AND_PLAN = "device_and_plan"
    ACCESSORY_INTEREST = "accessory_interest"
    MULTIPLE_LINES = "multiple_lines"
    BUSINESS_ACCOUNT = "business_account"

    NO_COVERAGE = "no_coverage"
    DEVICE_OUT_OF_STOCK = "device_out_of_stock"
    CONTRADICTORY_INFORMATION = "contradictory_information"
    PROLONGED_INACTIVITY = "prolonged_inactivity"
    REQUESTS_HUMAN = "requests_human"
    REQUIRES_HUMAN_REVIEW = "requires_human_review"
    DO_NOT_CONTACT = "do_not_contact"


@dataclass(frozen=True, slots=True)
class CommercialSignal:
    code: SignalCode
    confidence: Decimal
    source_message_id: UUID | None
    observed_at: datetime
    extraction_method: ExtractionMethod
    validation_status: ValidationStatus

    def __post_init__(self) -> None:
        _ensure_ratio(self.confidence, "signal confidence")
        _ensure_aware(self.observed_at, "signal observed_at")

    def is_usable(self, minimum_confidence: Decimal) -> bool:
        if self.validation_status is ValidationStatus.REJECTED:
            return False
        if self.validation_status is ValidationStatus.CONFIRMED:
            return True
        return self.confidence >= minimum_confidence


class ScoreDimension(StrEnum):
    NEED_COMPATIBILITY = "need_compatibility"
    PURCHASE_INTENT = "purchase_intent"
    BUDGET_PAYMENT = "budget_payment"
    URGENCY = "urgency"
    ENGAGEMENT = "engagement"
    COMMERCIAL_POTENTIAL = "commercial_potential"


class LeadClassification(StrEnum):
    EXPLORATION = "exploration"
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"
    PRIORITY = "priority"


class RecommendedAction(StrEnum):
    RESOLVE_AND_PRESENT_OPTIONS = "resolve_and_present_options"
    AUTOMATED_FOLLOW_UP = "automated_follow_up"
    COLLECT_MISSING_INFORMATION = "collect_missing_information"
    NOTIFY_ADVISOR = "notify_advisor"
    IMMEDIATE_HANDOFF = "immediate_handoff"
    SPECIALIZED_HANDOFF = "specialized_handoff"
    SEARCH_ALTERNATIVES = "search_alternatives"
    STOP_AUTOMATION = "stop_automation"


class LeadState(StrEnum):
    NEW = "new"
    IN_CONVERSATION = "in_conversation"
    QUALIFYING = "qualifying"
    INCOMPLETE_INFORMATION = "incomplete_information"
    QUALIFIED = "qualified"
    QUOTE_REQUESTED = "quote_requested"
    QUOTE_SENT = "quote_sent"
    PENDING_DOCUMENTS = "pending_documents"
    PENDING_VALIDATION = "pending_validation"
    PENDING_INVENTORY = "pending_inventory"
    PENDING_COVERAGE = "pending_coverage"
    PENDING_FINANCING = "pending_financing"
    TRANSFERRED_TO_ADVISOR = "transferred_to_advisor"
    CONTACTED_BY_ADVISOR = "contacted_by_advisor"
    PORTABILITY_STARTED = "portability_started"
    CONTRACTING_STARTED = "contracting_started"
    SALE_COMPLETED = "sale_completed"
    LOST = "lost"
    NOT_ELIGIBLE = "not_eligible"
    DUPLICATE = "duplicate"
    DO_NOT_CONTACT = "do_not_contact"


class EligibilityStatus(StrEnum):
    PENDING_VALIDATION = "pending_validation"
    ELIGIBLE = "eligible"
    ELIGIBLE_WITH_CONDITIONS = "eligible_with_conditions"
    NOT_ELIGIBLE = "not_eligible"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"
    NOT_APPLICABLE = "not_applicable"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class RuleEffect(StrEnum):
    PENALTY = "penalty"
    OFFER_BLOCK = "offer_block"
    DISQUALIFICATION = "disqualification"
    TEMPORARY_EXCLUSION = "temporary_exclusion"
    DO_NOT_CONTACT = "do_not_contact"


class QualificationCause(StrEnum):
    MESSAGE_RECEIVED = "message_received"
    PROFILE_UPDATED = "profile_updated"
    MANUAL_RECALCULATION = "manual_recalculation"
    POLICY_PUBLISHED = "policy_published"


@dataclass(frozen=True, slots=True)
class DimensionDefinition:
    dimension: ScoreDimension
    max_points: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_points <= 100:
            raise ValidationError("dimension max points must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class ScoringCriterion:
    code: str
    dimension: ScoreDimension
    points: int
    required_signals: frozenset[SignalCode]
    explanation: str
    excluded_signals: frozenset[SignalCode] = frozenset()
    exclusive_group: str | None = None

    def __post_init__(self) -> None:
        _ensure_rule_code(self.code)
        if self.points <= 0:
            raise ValidationError("scoring criterion points must be positive")
        if not self.required_signals:
            raise ValidationError("scoring criterion requires at least one signal")
        if self.required_signals & self.excluded_signals:
            raise ValidationError("criterion signals cannot be both required and excluded")
        if not self.explanation.strip():
            raise ValidationError("scoring criterion requires an explanation")
        if self.exclusive_group is not None:
            _ensure_rule_code(self.exclusive_group, "exclusive group")


@dataclass(frozen=True, slots=True)
class PenaltyRule:
    code: str
    adjustment: int
    effect: RuleEffect
    required_signals: frozenset[SignalCode]
    explanation: str
    recommended_action: RecommendedAction | None = None
    blocked_offer: str | None = None

    def __post_init__(self) -> None:
        _ensure_rule_code(self.code)
        if not -100 <= self.adjustment <= 0:
            raise ValidationError("penalty adjustment must be between -100 and 0")
        if self.effect is RuleEffect.PENALTY and self.adjustment == 0:
            raise ValidationError("a point penalty must have a negative adjustment")
        if not self.required_signals:
            raise ValidationError("penalty rule requires at least one signal")
        if not self.explanation.strip():
            raise ValidationError("penalty rule requires an explanation")
        if self.effect is RuleEffect.OFFER_BLOCK and not self.blocked_offer:
            raise ValidationError("offer block rules must identify the blocked offer")


@dataclass(frozen=True, slots=True)
class DecisionRule:
    code: str
    priority: int
    required_signals: frozenset[SignalCode]
    action: RecommendedAction
    explanation: str
    handoff_required: bool = False
    specialized_handoff: bool = False
    handoff_reason: str | None = None
    state_recommendation: LeadState | None = None

    def __post_init__(self) -> None:
        _ensure_rule_code(self.code)
        if self.priority < 0:
            raise ValidationError("decision priority cannot be negative")
        if not self.required_signals:
            raise ValidationError("decision rule requires at least one signal")
        if not self.explanation.strip():
            raise ValidationError("decision rule requires an explanation")
        if self.handoff_required and not self.handoff_reason:
            raise ValidationError("handoff decisions require a reason")
        if self.specialized_handoff and not self.handoff_required:
            raise ValidationError("specialized handoff must require handoff")


@dataclass(frozen=True, slots=True)
class ClassificationBand:
    minimum: int
    maximum: int
    classification: LeadClassification
    action: RecommendedAction

    def __post_init__(self) -> None:
        if not 0 <= self.minimum <= self.maximum <= 100:
            raise ValidationError("classification band must remain within 0 and 100")


@dataclass(frozen=True, slots=True)
class ScoringPolicy:
    id: UUID
    tenant_id: TenantId
    name: str
    version: int
    opportunity_type: OpportunityType
    status: PolicyStatus
    dimensions: tuple[DimensionDefinition, ...]
    criteria: tuple[ScoringCriterion, ...]
    penalties: tuple[PenaltyRule, ...]
    decisions: tuple[DecisionRule, ...]
    classification_bands: tuple[ClassificationBand, ...]
    minimum_signal_confidence: Decimal
    published_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 120:
            raise ValidationError("scoring policy name must contain between 1 and 120 characters")
        if self.version <= 0:
            raise ValidationError("scoring policy version must be positive")
        _ensure_ratio(self.minimum_signal_confidence, "minimum signal confidence")
        _ensure_aware(self.published_at, "policy published_at")

        dimension_map = {definition.dimension: definition for definition in self.dimensions}
        if len(dimension_map) != len(self.dimensions):
            raise ValidationError("scoring policy dimensions must be unique")
        if set(dimension_map) != set(ScoreDimension):
            raise ValidationError("scoring policy must define all score dimensions")
        if sum(item.max_points for item in self.dimensions) != 100:
            raise ValidationError("scoring policy dimension maxima must total 100")

        all_rule_codes = [item.code for item in self.criteria]
        all_rule_codes += [item.code for item in self.penalties]
        all_rule_codes += [item.code for item in self.decisions]
        if len(set(all_rule_codes)) != len(all_rule_codes):
            raise ValidationError("scoring policy rule codes must be unique")
        for criterion in self.criteria:
            if criterion.points > dimension_map[criterion.dimension].max_points:
                raise ValidationError("criterion points cannot exceed its dimension maximum")

        bands = sorted(self.classification_bands, key=lambda item: item.minimum)
        if not bands or bands[0].minimum != 0 or bands[-1].maximum != 100:
            raise ValidationError("classification bands must cover 0 through 100")
        for previous, current in zip(bands, bands[1:], strict=False):
            if current.minimum != previous.maximum + 1:
                raise ValidationError("classification bands cannot contain gaps or overlaps")
        if len({item.classification for item in bands}) != len(bands):
            raise ValidationError("classification bands must use unique classifications")

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            self._canonical_data(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def dimension_maximum(self, dimension: ScoreDimension) -> int:
        return next(item.max_points for item in self.dimensions if item.dimension is dimension)

    def _canonical_data(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id.value),
            "name": self.name,
            "version": self.version,
            "opportunity_type": self.opportunity_type.value,
            "status": self.status.value,
            "minimum_signal_confidence": str(self.minimum_signal_confidence),
            "published_at": self.published_at.isoformat(),
            "dimensions": [
                {"dimension": item.dimension.value, "max_points": item.max_points}
                for item in sorted(self.dimensions, key=lambda value: value.dimension.value)
            ],
            "criteria": [
                {
                    "code": item.code,
                    "dimension": item.dimension.value,
                    "points": item.points,
                    "required_signals": sorted(value.value for value in item.required_signals),
                    "excluded_signals": sorted(value.value for value in item.excluded_signals),
                    "exclusive_group": item.exclusive_group,
                    "explanation": item.explanation,
                }
                for item in sorted(self.criteria, key=lambda value: value.code)
            ],
            "penalties": [
                {
                    "code": item.code,
                    "adjustment": item.adjustment,
                    "effect": item.effect.value,
                    "required_signals": sorted(value.value for value in item.required_signals),
                    "explanation": item.explanation,
                    "recommended_action": (
                        item.recommended_action.value if item.recommended_action else None
                    ),
                    "blocked_offer": item.blocked_offer,
                }
                for item in sorted(self.penalties, key=lambda value: value.code)
            ],
            "decisions": [
                {
                    "code": item.code,
                    "priority": item.priority,
                    "required_signals": sorted(value.value for value in item.required_signals),
                    "action": item.action.value,
                    "explanation": item.explanation,
                    "handoff_required": item.handoff_required,
                    "specialized_handoff": item.specialized_handoff,
                    "handoff_reason": item.handoff_reason,
                    "state_recommendation": (
                        item.state_recommendation.value if item.state_recommendation else None
                    ),
                }
                for item in sorted(self.decisions, key=lambda value: value.code)
            ],
            "classification_bands": [
                {
                    "minimum": item.minimum,
                    "maximum": item.maximum,
                    "classification": item.classification.value,
                    "action": item.action.value,
                }
                for item in sorted(self.classification_bands, key=lambda value: value.minimum)
            ],
        }


@dataclass(frozen=True, slots=True)
class ExtractionMetadata:
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    overall_confidence: Decimal | None = None

    def __post_init__(self) -> None:
        if self.overall_confidence is not None:
            _ensure_ratio(self.overall_confidence, "extraction overall confidence")


@dataclass(frozen=True, slots=True)
class QualificationRequest:
    profile: LeadProfile
    signals: tuple[CommercialSignal, ...]
    cause: QualificationCause
    trigger_id: UUID
    correlation_id: UUID
    eligibility: EligibilityStatus = EligibilityStatus.NOT_APPLICABLE
    extraction: ExtractionMetadata = field(default_factory=ExtractionMetadata)


@dataclass(frozen=True, slots=True)
class PolicyReference:
    id: UUID
    tenant_id: TenantId
    version: int
    opportunity_type: OpportunityType
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    rule_code: str
    dimension: ScoreDimension
    points: int
    signal_codes: tuple[SignalCode, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class DimensionScore:
    dimension: ScoreDimension
    raw_points: int
    points: int
    maximum: int


@dataclass(frozen=True, slots=True)
class AppliedPenalty:
    rule_code: str
    adjustment: int
    effect: RuleEffect
    signal_codes: tuple[SignalCode, ...]
    explanation: str
    blocked_offer: str | None


@dataclass(frozen=True, slots=True)
class HandoffDecision:
    required: bool
    specialized: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationExplanation:
    summary: str
    positive_reasons: tuple[str, ...]
    penalty_reasons: tuple[str, ...]
    applied_rule_codes: tuple[str, ...]
    missing_information: tuple[ProfileField, ...]
    blocked_offers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualificationResult:
    id: UUID
    tenant_id: TenantId
    lead_id: UUID
    conversation_id: UUID
    opportunity: Opportunity
    score: int
    subtotal: int
    classification: LeadClassification
    dimensions: tuple[DimensionScore, ...]
    contributions: tuple[ScoreContribution, ...]
    penalties: tuple[AppliedPenalty, ...]
    effects: tuple[RuleEffect, ...]
    action: RecommendedAction
    handoff: HandoffDecision
    eligibility: EligibilityStatus
    state_recommendation: LeadState | None
    policy: PolicyReference
    engine_version: str
    evaluated_at: datetime
    cause: QualificationCause
    trigger_id: UUID
    correlation_id: UUID
    extraction: ExtractionMetadata
    signals_used: tuple[SignalCode, ...]
    explanation: QualificationExplanation

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValidationError("qualification score must remain between 0 and 100")
        if not 0 <= self.subtotal <= 100:
            raise ValidationError("qualification subtotal must remain between 0 and 100")
        _ensure_aware(self.evaluated_at, "qualification evaluated_at")
