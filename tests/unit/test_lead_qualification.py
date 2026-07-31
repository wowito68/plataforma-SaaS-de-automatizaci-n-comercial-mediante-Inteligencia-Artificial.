from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from saas_platform.errors import ConflictError, ForbiddenError, ValidationError
from saas_platform.modules.lead_qualification.application import EvaluateLeadQualification
from saas_platform.modules.lead_qualification.default_policy import (
    build_default_telecom_policy,
)
from saas_platform.modules.lead_qualification.domain import (
    ENGINE_VERSION,
    ClassificationBand,
    CommercialSignal,
    DecisionRule,
    DimensionDefinition,
    EligibilityStatus,
    ExtractionMetadata,
    ExtractionMethod,
    FactConflict,
    FactSource,
    LeadClassification,
    LeadProfile,
    LeadState,
    Opportunity,
    OpportunityType,
    PenaltyRule,
    PolicyStatus,
    ProfileFact,
    ProfileField,
    QualificationCause,
    QualificationRequest,
    QualificationResult,
    RecommendedAction,
    RuleEffect,
    ScoreDimension,
    ScoringCriterion,
    ScoringPolicy,
    SignalCode,
    ValidationStatus,
)
from saas_platform.modules.lead_qualification.scoring import LeadScoringEngine
from saas_platform.modules.tenancy.domain import TenantId
from tests.fakes import FixedIdGenerator, MutableClock

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
TENANT_ID = TenantId(UUID("018f0000-0000-7000-8000-000000000001"))
OTHER_TENANT_ID = TenantId(UUID("018f0000-0000-7000-8000-000000000002"))
POLICY_ID = UUID("018f0000-0000-7000-8000-000000000010")
LEAD_ID = UUID("018f0000-0000-7000-8000-000000000020")
CONVERSATION_ID = UUID("018f0000-0000-7000-8000-000000000030")
TRIGGER_ID = UUID("018f0000-0000-7000-8000-000000000040")
CORRELATION_ID = UUID("018f0000-0000-7000-8000-000000000050")
EVALUATION_ID = UUID("018f0000-0000-7000-8000-000000000060")
MESSAGE_ID = UUID("018f0000-0000-7000-8000-000000000070")


def make_policy(
    opportunity_type: OpportunityType = OpportunityType.DEVICE_WITH_PLAN,
    *,
    tenant_id: TenantId = TENANT_ID,
    policy_id: UUID = POLICY_ID,
    version: int = 1,
) -> ScoringPolicy:
    return build_default_telecom_policy(
        tenant_id=tenant_id,
        policy_id=policy_id,
        opportunity_type=opportunity_type,
        published_at=NOW,
        version=version,
    )


def make_profile(
    opportunity_type: OpportunityType = OpportunityType.DEVICE_WITH_PLAN,
    *,
    tenant_id: TenantId = TENANT_ID,
    secondary: tuple[OpportunityType, ...] = (),
    facts: Mapping[ProfileField, ProfileFact] | None = None,
    conflicts: tuple[FactConflict, ...] = (),
    missing: tuple[ProfileField, ...] = (),
) -> LeadProfile:
    return LeadProfile(
        tenant_id=tenant_id,
        lead_id=LEAD_ID,
        conversation_id=CONVERSATION_ID,
        opportunity=Opportunity(opportunity_type, secondary),
        facts=facts or {},
        conflicts=conflicts,
        missing_information=missing,
    )


def make_signal(
    code: SignalCode,
    *,
    confidence: str = "0.95",
    validation_status: ValidationStatus = ValidationStatus.ACCEPTED,
    observed_at: datetime = NOW,
    extraction_method: ExtractionMethod = ExtractionMethod.AI,
) -> CommercialSignal:
    return CommercialSignal(
        code=code,
        confidence=Decimal(confidence),
        source_message_id=MESSAGE_ID,
        observed_at=observed_at,
        extraction_method=extraction_method,
        validation_status=validation_status,
    )


def make_request(
    codes: Iterable[SignalCode] = (),
    *,
    opportunity_type: OpportunityType = OpportunityType.DEVICE_WITH_PLAN,
    tenant_id: TenantId = TENANT_ID,
    secondary: tuple[OpportunityType, ...] = (),
    signals: tuple[CommercialSignal, ...] | None = None,
    missing: tuple[ProfileField, ...] = (),
    eligibility: EligibilityStatus = EligibilityStatus.NOT_APPLICABLE,
    extraction: ExtractionMetadata | None = None,
) -> QualificationRequest:
    return QualificationRequest(
        profile=make_profile(
            opportunity_type,
            tenant_id=tenant_id,
            secondary=secondary,
            missing=missing,
        ),
        signals=(signals if signals is not None else tuple(make_signal(code) for code in codes)),
        cause=QualificationCause.MESSAGE_RECEIVED,
        trigger_id=TRIGGER_ID,
        correlation_id=CORRELATION_ID,
        eligibility=eligibility,
        extraction=extraction or ExtractionMetadata(),
    )


def evaluate(
    request: QualificationRequest,
    policy: ScoringPolicy | None = None,
    *,
    evaluated_at: datetime = NOW,
) -> QualificationResult:
    selected_policy = policy or make_policy(request.profile.opportunity.primary)
    return LeadScoringEngine().evaluate(
        request,
        selected_policy,
        evaluation_id=EVALUATION_ID,
        evaluated_at=evaluated_at,
    )


def all_positive_signals() -> tuple[SignalCode, ...]:
    return (
        SignalCode.NEED_DEFINED,
        SignalCode.PLAN_COMPATIBLE,
        SignalCode.DEVICE_COMPATIBLE,
        SignalCode.COVERAGE_CONFIRMED,
        SignalCode.COMMERCIAL_REQUIREMENTS_COMPATIBLE,
        SignalCode.GENERAL_INFORMATION,
        SignalCode.ASKS_PRICE,
        SignalCode.ASKS_MONTHLY_PAYMENT,
        SignalCode.ASKS_AVAILABILITY,
        SignalCode.COMPARES_PRODUCTS,
        SignalCode.ASKS_REQUIREMENTS,
        SignalCode.REQUESTS_QUOTE,
        SignalCode.REQUESTS_PORTABILITY,
        SignalCode.WANTS_CONTRACT_NOW,
        SignalCode.BUDGET_UNSPECIFIED,
        SignalCode.BUDGET_INCOMPATIBLE,
        SignalCode.ACCEPTS_ALTERNATIVES,
        SignalCode.BUDGET_COMPATIBLE,
        SignalCode.BUDGET_AND_PAYMENT_DEFINED,
        SignalCode.RESEARCH_ONLY,
        SignalCode.PURCHASE_OVER_THREE_MONTHS,
        SignalCode.NEXT_MONTH,
        SignalCode.THIS_WEEK,
        SignalCode.IMMEDIATE,
        SignalCode.PROVIDES_RELEVANT_INFORMATION,
        SignalCode.ANSWERS_QUALIFICATION_QUESTIONS,
        SignalCode.SUSTAINED_PROGRESS,
        SignalCode.ACCEPTS_CALL_OR_BRANCH,
        SignalCode.DEVICE_AND_PLAN,
        SignalCode.ACCESSORY_INTEREST,
        SignalCode.MULTIPLE_LINES,
        SignalCode.BUSINESS_ACCOUNT,
    )


def test_default_policy_has_required_dimensions_ranges_and_penalties() -> None:
    policy = make_policy()

    assert [(item.dimension, item.max_points) for item in policy.dimensions] == [
        (ScoreDimension.NEED_COMPATIBILITY, 25),
        (ScoreDimension.PURCHASE_INTENT, 25),
        (ScoreDimension.BUDGET_PAYMENT, 20),
        (ScoreDimension.URGENCY, 15),
        (ScoreDimension.ENGAGEMENT, 10),
        (ScoreDimension.COMMERCIAL_POTENTIAL, 5),
    ]
    assert [
        (item.minimum, item.maximum, item.classification) for item in policy.classification_bands
    ] == [
        (0, 29, LeadClassification.EXPLORATION),
        (30, 49, LeadClassification.COLD),
        (50, 69, LeadClassification.WARM),
        (70, 84, LeadClassification.HOT),
        (85, 100, LeadClassification.PRIORITY),
    ]
    assert {item.adjustment for item in policy.penalties} == {0, -5, -8, -10, -15, -20}
    assert policy.minimum_signal_confidence == Decimal("0.70")
    assert policy.dimension_maximum(ScoreDimension.PURCHASE_INTENT) == 25


def test_empty_evidence_produces_minimum_exploration_score() -> None:
    result = evaluate(make_request())

    assert result.score == 0
    assert result.subtotal == 0
    assert result.classification is LeadClassification.EXPLORATION
    assert result.action is RecommendedAction.RESOLVE_AND_PRESENT_OPTIONS
    assert result.contributions == ()
    assert result.handoff.required is False


def test_complete_evidence_is_capped_at_one_hundred() -> None:
    result = evaluate(make_request(all_positive_signals()))

    assert result.score == 100
    assert result.subtotal == 100
    assert result.classification is LeadClassification.PRIORITY
    assert {item.dimension: item.points for item in result.dimensions} == {
        ScoreDimension.NEED_COMPATIBILITY: 25,
        ScoreDimension.PURCHASE_INTENT: 25,
        ScoreDimension.BUDGET_PAYMENT: 20,
        ScoreDimension.URGENCY: 15,
        ScoreDimension.ENGAGEMENT: 10,
        ScoreDimension.COMMERCIAL_POTENTIAL: 5,
    }
    potential = next(
        item for item in result.dimensions if item.dimension is ScoreDimension.COMMERCIAL_POTENTIAL
    )
    assert potential.raw_points == 11


def test_related_signals_do_not_double_count() -> None:
    result = evaluate(
        make_request(
            (
                SignalCode.GENERAL_INFORMATION,
                SignalCode.ASKS_PRICE,
                SignalCode.REQUESTS_QUOTE,
                SignalCode.WANTS_CONTRACT_NOW,
                SignalCode.BUDGET_UNSPECIFIED,
                SignalCode.BUDGET_COMPATIBLE,
                SignalCode.BUDGET_AND_PAYMENT_DEFINED,
                SignalCode.RESEARCH_ONLY,
                SignalCode.THIS_WEEK,
                SignalCode.IMMEDIATE,
            )
        )
    )

    by_dimension = {item.dimension: item for item in result.dimensions}
    assert by_dimension[ScoreDimension.PURCHASE_INTENT].raw_points == 25
    assert by_dimension[ScoreDimension.BUDGET_PAYMENT].raw_points == 20
    assert by_dimension[ScoreDimension.URGENCY].raw_points == 15
    assert (
        len(
            [
                item
                for item in result.contributions
                if item.dimension is ScoreDimension.PURCHASE_INTENT
            ]
        )
        == 1
    )


def test_low_confidence_is_ignored_but_human_confirmation_is_authoritative() -> None:
    request = make_request(
        signals=(
            make_signal(SignalCode.WANTS_CONTRACT_NOW, confidence="0.69"),
            make_signal(
                SignalCode.BUDGET_AND_PAYMENT_DEFINED,
                confidence="0.10",
                validation_status=ValidationStatus.CONFIRMED,
                extraction_method=ExtractionMethod.HUMAN,
            ),
            make_signal(
                SignalCode.IMMEDIATE,
                confidence="1.0",
                validation_status=ValidationStatus.REJECTED,
            ),
        )
    )

    result = evaluate(request)

    assert result.score == 20
    assert result.signals_used == (SignalCode.BUDGET_AND_PAYMENT_DEFINED,)


def test_duplicate_signal_is_reconciled_without_duplicate_points() -> None:
    request = make_request(
        signals=(
            make_signal(SignalCode.ASKS_PRICE, confidence="0.70"),
            make_signal(SignalCode.ASKS_PRICE, confidence="0.99"),
            make_signal(
                SignalCode.ASKS_PRICE,
                confidence="1.0",
                validation_status=ValidationStatus.REJECTED,
            ),
        )
    )

    result = evaluate(request)

    assert result.score == 7
    assert result.signals_used == (SignalCode.ASKS_PRICE,)
    assert len(result.contributions) == 1


def test_penalties_are_explained_and_score_is_clamped() -> None:
    penalty_signals = (
        SignalCode.NO_COVERAGE,
        SignalCode.DEVICE_OUT_OF_STOCK,
        SignalCode.BUDGET_INCOMPATIBLE_NO_ALTERNATIVES,
        SignalCode.PURCHASE_OVER_SIX_MONTHS,
        SignalCode.CONTRADICTORY_INFORMATION,
        SignalCode.PROLONGED_INACTIVITY,
    )
    result = evaluate(make_request(penalty_signals))

    assert result.score == 0
    assert sum(item.adjustment for item in result.penalties) == -63
    assert result.explanation.blocked_offers == ("requested_device", "requested_offer")
    assert RuleEffect.OFFER_BLOCK in result.effects
    assert RuleEffect.TEMPORARY_EXCLUSION in result.effects
    assert len(result.explanation.penalty_reasons) == 6


def test_offer_block_searches_alternatives_without_disqualifying_lead() -> None:
    result = evaluate(make_request((SignalCode.NO_COVERAGE,)))

    assert result.score == 0
    assert result.action is RecommendedAction.SEARCH_ALTERNATIVES
    assert result.state_recommendation is None
    assert result.effects == (RuleEffect.OFFER_BLOCK,)
    assert result.explanation.blocked_offers == ("requested_offer",)


def test_disqualification_effect_is_separate_from_point_penalty() -> None:
    policy = make_policy()
    disqualification = PenaltyRule(
        code="control.commercial_disqualification",
        adjustment=0,
        effect=RuleEffect.DISQUALIFICATION,
        required_signals=frozenset({SignalCode.BUDGET_INCOMPATIBLE_NO_ALTERNATIVES}),
        explanation="Tenant policy disqualifies this commercial combination",
    )
    policy = replace(policy, penalties=policy.penalties + (disqualification,))

    result = evaluate(
        make_request((SignalCode.BUDGET_INCOMPATIBLE_NO_ALTERNATIVES,)),
        policy,
    )

    assert result.state_recommendation is LeadState.NOT_ELIGIBLE
    assert RuleEffect.DISQUALIFICATION in result.effects


def test_do_not_contact_supersedes_score_and_handoff() -> None:
    result = evaluate(
        make_request(
            all_positive_signals()
            + (
                SignalCode.REQUESTS_HUMAN,
                SignalCode.DO_NOT_CONTACT,
            )
        )
    )

    assert result.score == 100
    assert result.action is RecommendedAction.STOP_AUTOMATION
    assert result.state_recommendation is LeadState.DO_NOT_CONTACT
    assert result.handoff.required is False
    assert RuleEffect.DO_NOT_CONTACT in result.effects


def test_high_score_does_not_infer_financial_eligibility() -> None:
    extraction = ExtractionMetadata(
        provider="example-ai",
        model="extractor-v1",
        prompt_version="telecom-profile.v3",
        schema_version="lead-signals.v1",
        overall_confidence=Decimal("0.92"),
    )
    result = evaluate(
        make_request(
            all_positive_signals(),
            eligibility=EligibilityStatus.PENDING_VALIDATION,
            extraction=extraction,
        )
    )

    assert result.score == 100
    assert result.eligibility is EligibilityStatus.PENDING_VALIDATION
    assert result.extraction == extraction
    assert result.engine_version == ENGINE_VERSION


@pytest.mark.parametrize(
    ("score", "classification", "action"),
    [
        (0, LeadClassification.EXPLORATION, RecommendedAction.RESOLVE_AND_PRESENT_OPTIONS),
        (29, LeadClassification.EXPLORATION, RecommendedAction.RESOLVE_AND_PRESENT_OPTIONS),
        (30, LeadClassification.COLD, RecommendedAction.AUTOMATED_FOLLOW_UP),
        (49, LeadClassification.COLD, RecommendedAction.AUTOMATED_FOLLOW_UP),
        (50, LeadClassification.WARM, RecommendedAction.COLLECT_MISSING_INFORMATION),
        (69, LeadClassification.WARM, RecommendedAction.COLLECT_MISSING_INFORMATION),
        (70, LeadClassification.HOT, RecommendedAction.NOTIFY_ADVISOR),
        (84, LeadClassification.HOT, RecommendedAction.NOTIFY_ADVISOR),
        (85, LeadClassification.PRIORITY, RecommendedAction.IMMEDIATE_HANDOFF),
        (100, LeadClassification.PRIORITY, RecommendedAction.IMMEDIATE_HANDOFF),
    ],
)
def test_classification_thresholds_are_inclusive(
    score: int,
    classification: LeadClassification,
    action: RecommendedAction,
) -> None:
    assert LeadScoringEngine._classification(make_policy(), score) == (classification, action)


def test_policy_must_be_published_and_belong_to_tenant_and_opportunity() -> None:
    request = make_request(opportunity_type=OpportunityType.DEVICE_PURCHASE)

    with pytest.raises(ConflictError, match="published"):
        evaluate(
            request,
            replace(make_policy(OpportunityType.DEVICE_PURCHASE), status=PolicyStatus.DRAFT),
        )
    with pytest.raises(ForbiddenError, match="different tenant"):
        evaluate(request, make_policy(OpportunityType.DEVICE_PURCHASE, tenant_id=OTHER_TENANT_ID))
    with pytest.raises(ValidationError, match="primary opportunity"):
        evaluate(request, make_policy(OpportunityType.PLAN_SUBSCRIPTION))


def test_policy_fingerprint_is_stable_and_historical_reference_is_immutable() -> None:
    first = make_policy(version=1)
    same = make_policy(version=1)
    result = evaluate(make_request((SignalCode.ASKS_PRICE,)), first)
    second = make_policy(
        policy_id=UUID("018f0000-0000-7000-8000-000000000011"),
        version=2,
    )

    assert first.fingerprint == same.fingerprint
    assert second.fingerprint != first.fingerprint
    assert result.policy.version == 1
    assert result.policy.fingerprint == first.fingerprint
    assert result.policy.fingerprint != second.fingerprint


def test_opportunity_specific_policy_only_includes_relevant_compatibility_rules() -> None:
    accessory_codes = {item.code for item in make_policy(OpportunityType.ACCESSORY).criteria}
    plan_codes = {item.code for item in make_policy(OpportunityType.PLAN_SUBSCRIPTION).criteria}
    combined_codes = {item.code for item in make_policy().criteria}

    assert "need.plan_compatible" not in accessory_codes
    assert "need.device_compatible" not in accessory_codes
    assert "need.plan_compatible" in plan_codes
    assert "need.device_compatible" not in plan_codes
    assert {"need.plan_compatible", "need.device_compatible"} <= combined_codes


@pytest.mark.parametrize("opportunity_type", tuple(OpportunityType))
def test_default_policy_is_valid_for_every_opportunity_type(
    opportunity_type: OpportunityType,
) -> None:
    policy = make_policy(opportunity_type)

    assert policy.opportunity_type is opportunity_type
    assert sum(item.max_points for item in policy.dimensions) == 100


def test_tenants_can_apply_independent_policy_versions_to_the_same_signal() -> None:
    first_policy = make_policy(OpportunityType.DEVICE_PURCHASE)
    second_policy = make_policy(
        OpportunityType.DEVICE_PURCHASE,
        tenant_id=OTHER_TENANT_ID,
        policy_id=UUID("018f0000-0000-7000-8000-000000000012"),
    )
    second_policy = replace(
        second_policy,
        criteria=tuple(
            replace(item, points=17) if item.code == "intent.asks_price" else item
            for item in second_policy.criteria
        ),
    )

    first = evaluate(
        make_request(
            (SignalCode.ASKS_PRICE,),
            opportunity_type=OpportunityType.DEVICE_PURCHASE,
        ),
        first_policy,
    )
    second = evaluate(
        make_request(
            (SignalCode.ASKS_PRICE,),
            opportunity_type=OpportunityType.DEVICE_PURCHASE,
            tenant_id=OTHER_TENANT_ID,
        ),
        second_policy,
    )

    assert first.score == 7
    assert second.score == 17
    assert first.policy.tenant_id == TENANT_ID
    assert second.policy.tenant_id == OTHER_TENANT_ID


def test_explicit_human_request_handoffs_even_with_zero_score() -> None:
    result = evaluate(make_request((SignalCode.REQUESTS_HUMAN,)))

    assert result.score == 0
    assert result.action is RecommendedAction.IMMEDIATE_HANDOFF
    assert result.handoff.required is True
    assert result.handoff.reason_codes == ("customer.requested_human",)


def test_hot_score_handoffs_without_a_special_decision_rule() -> None:
    result = evaluate(
        make_request(
            (
                SignalCode.NEED_DEFINED,
                SignalCode.PLAN_COMPATIBLE,
                SignalCode.DEVICE_COMPATIBLE,
                SignalCode.COVERAGE_CONFIRMED,
                SignalCode.COMMERCIAL_REQUIREMENTS_COMPATIBLE,
                SignalCode.BUDGET_AND_PAYMENT_DEFINED,
                SignalCode.IMMEDIATE,
                SignalCode.PROVIDES_RELEVANT_INFORMATION,
                SignalCode.ANSWERS_QUALIFICATION_QUESTIONS,
                SignalCode.SUSTAINED_PROGRESS,
                SignalCode.ACCEPTS_CALL_OR_BRANCH,
            )
        )
    )

    assert result.score == 70
    assert result.classification is LeadClassification.HOT
    assert result.action is RecommendedAction.NOTIFY_ADVISOR
    assert result.handoff.reason_codes == ("score.hot",)


class RecordingPolicyProvider:
    def __init__(self, policy: ScoringPolicy) -> None:
        self.policy = policy
        self.calls: list[tuple[TenantId, OpportunityType]] = []

    def get_published(
        self,
        tenant_id: TenantId,
        opportunity_type: OpportunityType,
    ) -> ScoringPolicy:
        self.calls.append((tenant_id, opportunity_type))
        return self.policy


def test_application_case_selects_policy_and_supplies_audit_identifiers() -> None:
    policy = make_policy(OpportunityType.PORTABILITY)
    provider = RecordingPolicyProvider(policy)
    use_case = EvaluateLeadQualification(
        provider,
        LeadScoringEngine(),
        FixedIdGenerator(EVALUATION_ID),
        MutableClock(NOW),
    )
    request = make_request(
        (SignalCode.REQUESTS_PORTABILITY,),
        opportunity_type=OpportunityType.PORTABILITY,
    )

    result = use_case.execute(request)

    assert provider.calls == [(TENANT_ID, OpportunityType.PORTABILITY)]
    assert result.id == EVALUATION_ID
    assert result.evaluated_at == NOW
    assert result.trigger_id == TRIGGER_ID
    assert result.correlation_id == CORRELATION_ID


def test_reference_case_a_priority_portability_with_device() -> None:
    result = evaluate(
        make_request(
            (
                SignalCode.NEED_DEFINED,
                SignalCode.PLAN_COMPATIBLE,
                SignalCode.COVERAGE_CONFIRMED,
                SignalCode.COMMERCIAL_REQUIREMENTS_COMPATIBLE,
                SignalCode.REQUESTS_PORTABILITY,
                SignalCode.BUDGET_AND_PAYMENT_DEFINED,
                SignalCode.THIS_WEEK,
                SignalCode.PROVIDES_RELEVANT_INFORMATION,
                SignalCode.ANSWERS_QUALIFICATION_QUESTIONS,
                SignalCode.DEVICE_AND_PLAN,
            ),
            opportunity_type=OpportunityType.PORTABILITY,
            secondary=(OpportunityType.DEVICE_PURCHASE,),
            eligibility=EligibilityStatus.PENDING_VALIDATION,
        ),
        make_policy(OpportunityType.PORTABILITY),
    )

    assert result.opportunity.primary is OpportunityType.PORTABILITY
    assert OpportunityType.DEVICE_PURCHASE in result.opportunity.secondary
    assert result.score == 82
    assert result.classification is LeadClassification.HOT
    assert result.eligibility is EligibilityStatus.PENDING_VALIDATION
    assert result.action is RecommendedAction.IMMEDIATE_HANDOFF
    assert result.handoff.required is True


def test_reference_case_b_exploration_collects_device_information() -> None:
    missing = (
        ProfileField.BRAND,
        ProfileField.MODEL,
        ProfileField.DEVICE_BUDGET,
        ProfileField.PAYMENT_METHOD,
    )
    result = evaluate(
        make_request(
            (SignalCode.GENERAL_INFORMATION,),
            opportunity_type=OpportunityType.DEVICE_PURCHASE,
            missing=missing,
        ),
        make_policy(OpportunityType.DEVICE_PURCHASE),
    )

    assert result.score == 3
    assert result.classification is LeadClassification.EXPLORATION
    assert result.explanation.missing_information == missing
    assert result.action is RecommendedAction.RESOLVE_AND_PRESENT_OPTIONS
    assert result.handoff.required is False


def test_reference_case_c_out_of_stock_searches_then_handoffs_on_acceptance() -> None:
    base_codes = (
        SignalCode.NEED_DEFINED,
        SignalCode.DEVICE_COMPATIBLE,
        SignalCode.COMPARES_PRODUCTS,
        SignalCode.IMMEDIATE,
        SignalCode.DEVICE_OUT_OF_STOCK,
    )
    policy = make_policy(OpportunityType.DEVICE_PURCHASE)

    unavailable = evaluate(
        make_request(base_codes, opportunity_type=OpportunityType.DEVICE_PURCHASE),
        policy,
    )
    accepted_alternative = evaluate(
        make_request(
            base_codes + (SignalCode.ACCEPTS_ALTERNATIVES,),
            opportunity_type=OpportunityType.DEVICE_PURCHASE,
        ),
        policy,
    )

    assert unavailable.score == 30
    assert unavailable.action is RecommendedAction.SEARCH_ALTERNATIVES
    assert unavailable.handoff.required is False
    assert unavailable.explanation.blocked_offers == ("requested_device",)
    assert accepted_alternative.action is RecommendedAction.IMMEDIATE_HANDOFF
    assert accepted_alternative.handoff.reason_codes == ("inventory.alternative_accepted",)


def test_reference_case_d_business_opportunity_gets_specialized_handoff() -> None:
    result = evaluate(
        make_request(
            (
                SignalCode.NEED_DEFINED,
                SignalCode.PLAN_COMPATIBLE,
                SignalCode.COMMERCIAL_REQUIREMENTS_COMPATIBLE,
                SignalCode.NEXT_MONTH,
                SignalCode.PROVIDES_RELEVANT_INFORMATION,
                SignalCode.ANSWERS_QUALIFICATION_QUESTIONS,
                SignalCode.MULTIPLE_LINES,
                SignalCode.BUSINESS_ACCOUNT,
            ),
            opportunity_type=OpportunityType.BUSINESS_ACCOUNT,
            secondary=(OpportunityType.MULTIPLE_LINES,),
            missing=(ProfileField.BUSINESS_SIZE,),
        ),
        make_policy(OpportunityType.BUSINESS_ACCOUNT),
    )

    assert result.score == 35
    assert result.action is RecommendedAction.SPECIALIZED_HANDOFF
    assert result.handoff.required is True
    assert result.handoff.specialized is True
    assert result.handoff.reason_codes == ("business.multiple_lines",)


def test_reference_case_e_no_contact_stops_automation() -> None:
    result = evaluate(
        make_request(
            (SignalCode.DO_NOT_CONTACT,),
            opportunity_type=OpportunityType.UNIDENTIFIED,
        ),
        make_policy(OpportunityType.UNIDENTIFIED),
    )

    assert result.score == 0
    assert result.action is RecommendedAction.STOP_AUTOMATION
    assert result.state_recommendation is LeadState.DO_NOT_CONTACT
    assert result.handoff.required is False


def make_fact(
    field: ProfileField = ProfileField.BRAND,
    *,
    value: str = "Samsung",
    confidence: str = "0.90",
    status: ValidationStatus = ValidationStatus.ACCEPTED,
    confirmed_by: str | None = None,
    observed_at: datetime = NOW,
) -> ProfileFact:
    return ProfileFact(
        field=field,
        value=value,
        source=FactSource.CUSTOMER,
        source_message_id=MESSAGE_ID,
        observed_at=observed_at,
        confidence=Decimal(confidence),
        extraction_method=ExtractionMethod.EXPLICIT,
        validation_status=status,
        confirmed_by=confirmed_by,
    )


def test_profile_facts_preserve_provenance_conflicts_and_immutability() -> None:
    previous = make_fact(value="Samsung")
    current = make_fact(value="Apple")
    conflict = FactConflict(
        field=ProfileField.BRAND,
        previous=previous,
        current=current,
        reason="The customer corrected the requested brand",
        detected_at=NOW,
    )
    source: dict[ProfileField, ProfileFact] = {ProfileField.BRAND: current}
    profile = make_profile(
        facts=source,
        conflicts=(conflict,),
        missing=(ProfileField.MODEL,),
    )
    source.clear()

    assert profile.fact(ProfileField.BRAND) == current
    assert profile.fact(ProfileField.MODEL) is None
    assert profile.conflicts == (conflict,)
    with pytest.raises(TypeError):
        cast(dict[ProfileField, ProfileFact], profile.facts)[ProfileField.MODEL] = current


@pytest.mark.parametrize(
    "factory",
    [
        lambda: Opportunity(
            OpportunityType.PORTABILITY,
            (OpportunityType.PORTABILITY,),
        ),
        lambda: Opportunity(
            OpportunityType.PORTABILITY,
            (OpportunityType.DEVICE_PURCHASE, OpportunityType.DEVICE_PURCHASE),
        ),
        lambda: make_fact(value="   "),
        lambda: make_fact(confidence="1.01"),
        lambda: make_fact(observed_at=datetime(2026, 1, 1)),
        lambda: make_fact(status=ValidationStatus.CONFIRMED),
        lambda: LeadProfile(
            tenant_id=TENANT_ID,
            lead_id=LEAD_ID,
            conversation_id=CONVERSATION_ID,
            opportunity=Opportunity(OpportunityType.DEVICE_PURCHASE),
            facts={ProfileField.MODEL: make_fact(ProfileField.BRAND)},
        ),
        lambda: LeadProfile(
            tenant_id=TENANT_ID,
            lead_id=LEAD_ID,
            conversation_id=CONVERSATION_ID,
            opportunity=Opportunity(OpportunityType.DEVICE_PURCHASE),
            facts={},
            missing_information=(ProfileField.MODEL, ProfileField.MODEL),
        ),
        lambda: make_signal(SignalCode.ASKS_PRICE, confidence="-0.1"),
        lambda: make_signal(
            SignalCode.ASKS_PRICE,
            observed_at=datetime(2026, 1, 1),
        ),
        lambda: DimensionDefinition(ScoreDimension.URGENCY, 0),
        lambda: ClassificationBand(
            -1, 10, LeadClassification.EXPLORATION, RecommendedAction.AUTOMATED_FOLLOW_UP
        ),
        lambda: ExtractionMetadata(overall_confidence=Decimal("1.1")),
    ],
)
def test_value_objects_reject_invalid_data(factory: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_confirmed_fact_requires_and_preserves_confirmer() -> None:
    fact = make_fact(
        status=ValidationStatus.CONFIRMED,
        confirmed_by="advisor:42",
        confidence="0.2",
    )

    assert fact.confirmed_by == "advisor:42"


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ScoringCriterion(
            "Invalid Code",
            ScoreDimension.URGENCY,
            1,
            frozenset({SignalCode.IMMEDIATE}),
            "valid explanation",
        ),
        lambda: ScoringCriterion(
            "criterion.zero",
            ScoreDimension.URGENCY,
            0,
            frozenset({SignalCode.IMMEDIATE}),
            "valid explanation",
        ),
        lambda: ScoringCriterion(
            "criterion.empty",
            ScoreDimension.URGENCY,
            1,
            frozenset(),
            "valid explanation",
        ),
        lambda: ScoringCriterion(
            "criterion.overlap",
            ScoreDimension.URGENCY,
            1,
            frozenset({SignalCode.IMMEDIATE}),
            "valid explanation",
            excluded_signals=frozenset({SignalCode.IMMEDIATE}),
        ),
        lambda: ScoringCriterion(
            "criterion.no_explanation",
            ScoreDimension.URGENCY,
            1,
            frozenset({SignalCode.IMMEDIATE}),
            " ",
        ),
        lambda: ScoringCriterion(
            "criterion.bad_group",
            ScoreDimension.URGENCY,
            1,
            frozenset({SignalCode.IMMEDIATE}),
            "valid explanation",
            exclusive_group="Invalid Group",
        ),
        lambda: PenaltyRule(
            "penalty.positive",
            1,
            RuleEffect.PENALTY,
            frozenset({SignalCode.NO_COVERAGE}),
            "valid explanation",
        ),
        lambda: PenaltyRule(
            "penalty.zero",
            0,
            RuleEffect.PENALTY,
            frozenset({SignalCode.NO_COVERAGE}),
            "valid explanation",
        ),
        lambda: PenaltyRule(
            "penalty.empty",
            -1,
            RuleEffect.PENALTY,
            frozenset(),
            "valid explanation",
        ),
        lambda: PenaltyRule(
            "penalty.no_explanation",
            -1,
            RuleEffect.PENALTY,
            frozenset({SignalCode.NO_COVERAGE}),
            " ",
        ),
        lambda: PenaltyRule(
            "penalty.block_without_offer",
            -1,
            RuleEffect.OFFER_BLOCK,
            frozenset({SignalCode.NO_COVERAGE}),
            "valid explanation",
        ),
        lambda: DecisionRule(
            "decision.negative_priority",
            -1,
            frozenset({SignalCode.REQUESTS_HUMAN}),
            RecommendedAction.IMMEDIATE_HANDOFF,
            "valid explanation",
        ),
        lambda: DecisionRule(
            "decision.empty",
            1,
            frozenset(),
            RecommendedAction.IMMEDIATE_HANDOFF,
            "valid explanation",
        ),
        lambda: DecisionRule(
            "decision.no_explanation",
            1,
            frozenset({SignalCode.REQUESTS_HUMAN}),
            RecommendedAction.IMMEDIATE_HANDOFF,
            " ",
        ),
        lambda: DecisionRule(
            "decision.no_reason",
            1,
            frozenset({SignalCode.REQUESTS_HUMAN}),
            RecommendedAction.IMMEDIATE_HANDOFF,
            "valid explanation",
            handoff_required=True,
        ),
        lambda: DecisionRule(
            "decision.specialized_without_handoff",
            1,
            frozenset({SignalCode.BUSINESS_ACCOUNT}),
            RecommendedAction.SPECIALIZED_HANDOFF,
            "valid explanation",
            specialized_handoff=True,
        ),
    ],
)
def test_rule_objects_reject_invalid_configuration(factory: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_criterion_exclusions_prevent_a_match() -> None:
    policy = make_policy()
    criterion = ScoringCriterion(
        code="need.coverage_without_contradiction",
        dimension=ScoreDimension.NEED_COMPATIBILITY,
        points=5,
        required_signals=frozenset({SignalCode.COVERAGE_CONFIRMED}),
        excluded_signals=frozenset({SignalCode.CONTRADICTORY_INFORMATION}),
        explanation="Coverage is usable only without a material contradiction",
    )
    policy = replace(policy, criteria=policy.criteria + (criterion,))

    result = evaluate(
        make_request((SignalCode.COVERAGE_CONFIRMED, SignalCode.CONTRADICTORY_INFORMATION)),
        policy,
    )

    assert criterion.code not in result.explanation.applied_rule_codes


def test_policy_rejects_invalid_structure() -> None:
    policy = make_policy()
    dimensions_total_99 = (
        replace(policy.dimensions[0], max_points=24),
        *policy.dimensions[1:],
    )
    duplicate_dimensions = (policy.dimensions[0],) + policy.dimensions[:-1]
    duplicate_rule = replace(policy.criteria[0], code=policy.criteria[1].code)
    oversized_rule = replace(policy.criteria[0], points=26)
    gap_bands = (
        replace(policy.classification_bands[0], maximum=28),
        *policy.classification_bands[1:],
    )
    duplicate_classification = (
        *policy.classification_bands[:-1],
        replace(
            policy.classification_bands[-1],
            classification=LeadClassification.HOT,
        ),
    )
    invalid_policies: tuple[Callable[[], ScoringPolicy], ...] = (
        lambda: replace(policy, name=" "),
        lambda: replace(policy, version=0),
        lambda: replace(policy, minimum_signal_confidence=Decimal("-0.1")),
        lambda: replace(policy, published_at=datetime(2026, 1, 1)),
        lambda: replace(policy, dimensions=duplicate_dimensions),
        lambda: replace(policy, dimensions=dimensions_total_99),
        lambda: replace(policy, criteria=(duplicate_rule, *policy.criteria[1:])),
        lambda: replace(policy, criteria=(oversized_rule, *policy.criteria[1:])),
        lambda: replace(policy, classification_bands=()),
        lambda: replace(policy, classification_bands=gap_bands),
        lambda: replace(policy, classification_bands=duplicate_classification),
    )

    for factory in invalid_policies:
        with pytest.raises(ValidationError):
            factory()


def test_fact_conflict_validates_field_reason_and_timestamp() -> None:
    brand = make_fact(ProfileField.BRAND)
    model = make_fact(ProfileField.MODEL, value="S25")

    with pytest.raises(ValidationError, match="same field"):
        FactConflict(ProfileField.BRAND, brand, model, "changed", NOW)
    with pytest.raises(ValidationError, match="reason"):
        FactConflict(ProfileField.BRAND, brand, brand, " ", NOW)
    with pytest.raises(ValidationError, match="timezone"):
        FactConflict(
            ProfileField.BRAND,
            brand,
            brand,
            "changed",
            datetime(2026, 1, 1),
        )


def test_signal_usability_respects_validation_state() -> None:
    hypothesis = make_signal(
        SignalCode.ASKS_PRICE,
        confidence="0.50",
        validation_status=ValidationStatus.HYPOTHESIS,
    )
    confirmed = make_signal(
        SignalCode.ASKS_PRICE,
        confidence="0.10",
        validation_status=ValidationStatus.CONFIRMED,
    )
    rejected = make_signal(
        SignalCode.ASKS_PRICE,
        confidence="1.0",
        validation_status=ValidationStatus.REJECTED,
    )

    assert hypothesis.is_usable(Decimal("0.70")) is False
    assert confirmed.is_usable(Decimal("0.70")) is True
    assert rejected.is_usable(Decimal("0.70")) is False


def test_result_validates_score_subtotal_and_timestamp() -> None:
    result = evaluate(make_request())

    with pytest.raises(ValidationError, match="score"):
        replace(result, score=101)
    with pytest.raises(ValidationError, match="subtotal"):
        replace(result, subtotal=-1)
    with pytest.raises(ValidationError, match="timezone"):
        replace(result, evaluated_at=datetime(2026, 1, 1))


def test_unmatched_score_raises_instead_of_silently_classifying() -> None:
    with pytest.raises(ValidationError, match="classification band"):
        LeadScoringEngine._classification(make_policy(), 101)
