from datetime import datetime
from decimal import Decimal
from uuid import UUID

from saas_platform.modules.lead_qualification.domain import (
    ClassificationBand,
    DecisionRule,
    DimensionDefinition,
    LeadClassification,
    LeadState,
    OpportunityType,
    PenaltyRule,
    PolicyStatus,
    RecommendedAction,
    RuleEffect,
    ScoreDimension,
    ScoringCriterion,
    ScoringPolicy,
    SignalCode,
)
from saas_platform.modules.tenancy.domain import TenantId

PLAN_OPPORTUNITIES = frozenset(
    {
        OpportunityType.DEVICE_WITH_PLAN,
        OpportunityType.PLAN_SUBSCRIPTION,
        OpportunityType.NEW_LINE,
        OpportunityType.PORTABILITY,
        OpportunityType.RENEWAL,
        OpportunityType.PREPAID_TO_POSTPAID,
        OpportunityType.MULTIPLE_LINES,
        OpportunityType.BUSINESS_ACCOUNT,
    }
)
DEVICE_OPPORTUNITIES = frozenset(
    {
        OpportunityType.DEVICE_PURCHASE,
        OpportunityType.DEVICE_WITH_PLAN,
        OpportunityType.RENEWAL,
        OpportunityType.MULTIPLE_LINES,
        OpportunityType.BUSINESS_ACCOUNT,
    }
)


def build_default_telecom_policy(
    *,
    tenant_id: TenantId,
    policy_id: UUID,
    opportunity_type: OpportunityType,
    published_at: datetime,
    version: int = 1,
) -> ScoringPolicy:
    return ScoringPolicy(
        id=policy_id,
        tenant_id=tenant_id,
        name=f"Default telecommunications qualification: {opportunity_type.value}",
        version=version,
        opportunity_type=opportunity_type,
        status=PolicyStatus.PUBLISHED,
        dimensions=_dimensions(),
        criteria=_criteria(opportunity_type),
        penalties=_penalties(),
        decisions=_decisions(),
        classification_bands=_classification_bands(),
        minimum_signal_confidence=Decimal("0.70"),
        published_at=published_at,
    )


def _dimensions() -> tuple[DimensionDefinition, ...]:
    return (
        DimensionDefinition(ScoreDimension.NEED_COMPATIBILITY, 25),
        DimensionDefinition(ScoreDimension.PURCHASE_INTENT, 25),
        DimensionDefinition(ScoreDimension.BUDGET_PAYMENT, 20),
        DimensionDefinition(ScoreDimension.URGENCY, 15),
        DimensionDefinition(ScoreDimension.ENGAGEMENT, 10),
        DimensionDefinition(ScoreDimension.COMMERCIAL_POTENTIAL, 5),
    )


def _criteria(opportunity_type: OpportunityType) -> tuple[ScoringCriterion, ...]:
    criteria = [
        _criterion(
            "need.defined",
            ScoreDimension.NEED_COMPATIBILITY,
            5,
            SignalCode.NEED_DEFINED,
            "The commercial need is clearly defined",
        ),
        _criterion(
            "need.coverage_confirmed",
            ScoreDimension.NEED_COMPATIBILITY,
            5,
            SignalCode.COVERAGE_CONFIRMED,
            "Coverage is confirmed for the requested offer",
        ),
        _criterion(
            "need.requirements_compatible",
            ScoreDimension.NEED_COMPATIBILITY,
            5,
            SignalCode.COMMERCIAL_REQUIREMENTS_COMPATIBLE,
            "Preliminary commercial requirements are compatible",
        ),
    ]
    if opportunity_type in PLAN_OPPORTUNITIES:
        criteria.append(
            _criterion(
                "need.plan_compatible",
                ScoreDimension.NEED_COMPATIBILITY,
                5,
                SignalCode.PLAN_COMPATIBLE,
                "A compatible plan exists",
            )
        )
    if opportunity_type in DEVICE_OPPORTUNITIES:
        criteria.append(
            _criterion(
                "need.device_compatible",
                ScoreDimension.NEED_COMPATIBILITY,
                5,
                SignalCode.DEVICE_COMPATIBLE,
                "A compatible device exists",
            )
        )

    criteria.extend(
        (
            _criterion(
                "intent.general_information",
                ScoreDimension.PURCHASE_INTENT,
                3,
                SignalCode.GENERAL_INFORMATION,
                "The prospect requests general information",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.asks_price",
                ScoreDimension.PURCHASE_INTENT,
                7,
                SignalCode.ASKS_PRICE,
                "The prospect asks for a price",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.asks_monthly_payment",
                ScoreDimension.PURCHASE_INTENT,
                10,
                SignalCode.ASKS_MONTHLY_PAYMENT,
                "The prospect asks for a monthly payment",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.asks_availability",
                ScoreDimension.PURCHASE_INTENT,
                12,
                SignalCode.ASKS_AVAILABILITY,
                "The prospect asks about availability",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.compares_products",
                ScoreDimension.PURCHASE_INTENT,
                15,
                SignalCode.COMPARES_PRODUCTS,
                "The prospect compares products",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.asks_requirements",
                ScoreDimension.PURCHASE_INTENT,
                18,
                SignalCode.ASKS_REQUIREMENTS,
                "The prospect asks for contracting requirements",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.requests_quote",
                ScoreDimension.PURCHASE_INTENT,
                20,
                SignalCode.REQUESTS_QUOTE,
                "The prospect requests a quote",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.requests_portability",
                ScoreDimension.PURCHASE_INTENT,
                22,
                SignalCode.REQUESTS_PORTABILITY,
                "The prospect requests portability",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "intent.wants_contract_now",
                ScoreDimension.PURCHASE_INTENT,
                25,
                SignalCode.WANTS_CONTRACT_NOW,
                "The prospect wants to start contracting now",
                exclusive_group="purchase_intent_level",
            ),
            _criterion(
                "budget.unspecified",
                ScoreDimension.BUDGET_PAYMENT,
                2,
                SignalCode.BUDGET_UNSPECIFIED,
                "The prospect explicitly has not defined a budget",
                exclusive_group="budget_level",
            ),
            _criterion(
                "budget.incompatible",
                ScoreDimension.BUDGET_PAYMENT,
                5,
                SignalCode.BUDGET_INCOMPATIBLE,
                "The current budget is incompatible with the requested offer",
                exclusive_group="budget_level",
            ),
            _criterion(
                "budget.accepts_alternatives",
                ScoreDimension.BUDGET_PAYMENT,
                10,
                SignalCode.ACCEPTS_ALTERNATIVES,
                "The prospect accepts alternatives within budget",
                exclusive_group="budget_level",
            ),
            _criterion(
                "budget.compatible",
                ScoreDimension.BUDGET_PAYMENT,
                15,
                SignalCode.BUDGET_COMPATIBLE,
                "The budget is compatible with an offer",
                exclusive_group="budget_level",
            ),
            _criterion(
                "budget.payment_defined",
                ScoreDimension.BUDGET_PAYMENT,
                20,
                SignalCode.BUDGET_AND_PAYMENT_DEFINED,
                "The budget and payment preference are defined",
                exclusive_group="budget_level",
            ),
            _criterion(
                "urgency.research_only",
                ScoreDimension.URGENCY,
                2,
                SignalCode.RESEARCH_ONLY,
                "The prospect is only researching",
                exclusive_group="urgency_level",
            ),
            _criterion(
                "urgency.over_three_months",
                ScoreDimension.URGENCY,
                5,
                SignalCode.PURCHASE_OVER_THREE_MONTHS,
                "The expected purchase is more than three months away",
                exclusive_group="urgency_level",
            ),
            _criterion(
                "urgency.next_month",
                ScoreDimension.URGENCY,
                9,
                SignalCode.NEXT_MONTH,
                "The expected purchase is during the next month",
                exclusive_group="urgency_level",
            ),
            _criterion(
                "urgency.this_week",
                ScoreDimension.URGENCY,
                12,
                SignalCode.THIS_WEEK,
                "The expected purchase is during this week",
                exclusive_group="urgency_level",
            ),
            _criterion(
                "urgency.immediate",
                ScoreDimension.URGENCY,
                15,
                SignalCode.IMMEDIATE,
                "The prospect needs the offer immediately",
                exclusive_group="urgency_level",
            ),
            _criterion(
                "engagement.relevant_information",
                ScoreDimension.ENGAGEMENT,
                3,
                SignalCode.PROVIDES_RELEVANT_INFORMATION,
                "The prospect provides commercially relevant information",
            ),
            _criterion(
                "engagement.answers_questions",
                ScoreDimension.ENGAGEMENT,
                3,
                SignalCode.ANSWERS_QUALIFICATION_QUESTIONS,
                "The prospect answers qualification questions",
            ),
            _criterion(
                "engagement.sustained_progress",
                ScoreDimension.ENGAGEMENT,
                2,
                SignalCode.SUSTAINED_PROGRESS,
                "The conversation makes sustained commercial progress",
            ),
            _criterion(
                "engagement.accepts_call_or_branch",
                ScoreDimension.ENGAGEMENT,
                2,
                SignalCode.ACCEPTS_CALL_OR_BRANCH,
                "The prospect accepts a call or branch visit",
            ),
            _criterion(
                "potential.device_and_plan",
                ScoreDimension.COMMERCIAL_POTENTIAL,
                2,
                SignalCode.DEVICE_AND_PLAN,
                "The opportunity combines a device and a plan",
            ),
            _criterion(
                "potential.accessory",
                ScoreDimension.COMMERCIAL_POTENTIAL,
                1,
                SignalCode.ACCESSORY_INTEREST,
                "The prospect is interested in accessories",
            ),
            _criterion(
                "potential.multiple_lines",
                ScoreDimension.COMMERCIAL_POTENTIAL,
                3,
                SignalCode.MULTIPLE_LINES,
                "The opportunity includes multiple lines",
            ),
            _criterion(
                "potential.business_account",
                ScoreDimension.COMMERCIAL_POTENTIAL,
                5,
                SignalCode.BUSINESS_ACCOUNT,
                "The opportunity is a business account",
            ),
        )
    )
    return tuple(criteria)


def _criterion(
    code: str,
    dimension: ScoreDimension,
    points: int,
    signal: SignalCode,
    explanation: str,
    *,
    exclusive_group: str | None = None,
) -> ScoringCriterion:
    return ScoringCriterion(
        code=code,
        dimension=dimension,
        points=points,
        required_signals=frozenset({signal}),
        explanation=explanation,
        exclusive_group=exclusive_group,
    )


def _penalties() -> tuple[PenaltyRule, ...]:
    return (
        PenaltyRule(
            code="penalty.no_coverage",
            adjustment=-20,
            effect=RuleEffect.OFFER_BLOCK,
            required_signals=frozenset({SignalCode.NO_COVERAGE}),
            explanation="The requested offer has no confirmed coverage",
            recommended_action=RecommendedAction.SEARCH_ALTERNATIVES,
            blocked_offer="requested_offer",
        ),
        PenaltyRule(
            code="penalty.device_out_of_stock",
            adjustment=-10,
            effect=RuleEffect.OFFER_BLOCK,
            required_signals=frozenset({SignalCode.DEVICE_OUT_OF_STOCK}),
            explanation="The requested device is out of stock",
            recommended_action=RecommendedAction.SEARCH_ALTERNATIVES,
            blocked_offer="requested_device",
        ),
        PenaltyRule(
            code="penalty.incompatible_budget_no_alternatives",
            adjustment=-15,
            effect=RuleEffect.PENALTY,
            required_signals=frozenset({SignalCode.BUDGET_INCOMPATIBLE_NO_ALTERNATIVES}),
            explanation="The budget is incompatible and alternatives were declined",
        ),
        PenaltyRule(
            code="penalty.purchase_over_six_months",
            adjustment=-8,
            effect=RuleEffect.PENALTY,
            required_signals=frozenset({SignalCode.PURCHASE_OVER_SIX_MONTHS}),
            explanation="The expected purchase is more than six months away",
        ),
        PenaltyRule(
            code="penalty.contradictory_information",
            adjustment=-5,
            effect=RuleEffect.PENALTY,
            required_signals=frozenset({SignalCode.CONTRADICTORY_INFORMATION}),
            explanation="The available commercial information is contradictory",
        ),
        PenaltyRule(
            code="penalty.prolonged_inactivity",
            adjustment=-5,
            effect=RuleEffect.TEMPORARY_EXCLUSION,
            required_signals=frozenset({SignalCode.PROLONGED_INACTIVITY}),
            explanation="The conversation has been inactive for the configured period",
        ),
        PenaltyRule(
            code="control.do_not_contact",
            adjustment=0,
            effect=RuleEffect.DO_NOT_CONTACT,
            required_signals=frozenset({SignalCode.DO_NOT_CONTACT}),
            explanation="The prospect requested no further contact",
            recommended_action=RecommendedAction.STOP_AUTOMATION,
        ),
    )


def _decisions() -> tuple[DecisionRule, ...]:
    return (
        DecisionRule(
            code="decision.do_not_contact",
            priority=1000,
            required_signals=frozenset({SignalCode.DO_NOT_CONTACT}),
            action=RecommendedAction.STOP_AUTOMATION,
            explanation="A no-contact request supersedes commercial prioritization",
            state_recommendation=LeadState.DO_NOT_CONTACT,
        ),
        DecisionRule(
            code="decision.explicit_human_request",
            priority=950,
            required_signals=frozenset({SignalCode.REQUESTS_HUMAN}),
            action=RecommendedAction.IMMEDIATE_HANDOFF,
            explanation="The prospect explicitly requested a human advisor",
            handoff_required=True,
            handoff_reason="customer.requested_human",
            state_recommendation=LeadState.TRANSFERRED_TO_ADVISOR,
        ),
        DecisionRule(
            code="decision.business_multiple_lines",
            priority=900,
            required_signals=frozenset({SignalCode.BUSINESS_ACCOUNT, SignalCode.MULTIPLE_LINES}),
            action=RecommendedAction.SPECIALIZED_HANDOFF,
            explanation="A business opportunity with multiple lines needs specialist care",
            handoff_required=True,
            specialized_handoff=True,
            handoff_reason="business.multiple_lines",
            state_recommendation=LeadState.TRANSFERRED_TO_ADVISOR,
        ),
        DecisionRule(
            code="decision.contract_now",
            priority=850,
            required_signals=frozenset({SignalCode.WANTS_CONTRACT_NOW}),
            action=RecommendedAction.IMMEDIATE_HANDOFF,
            explanation="The prospect wants to begin contracting immediately",
            handoff_required=True,
            handoff_reason="intent.contract_now",
            state_recommendation=LeadState.CONTRACTING_STARTED,
        ),
        DecisionRule(
            code="decision.portability",
            priority=800,
            required_signals=frozenset({SignalCode.REQUESTS_PORTABILITY}),
            action=RecommendedAction.IMMEDIATE_HANDOFF,
            explanation="A portability request needs commercial follow-through",
            handoff_required=True,
            handoff_reason="intent.portability",
            state_recommendation=LeadState.TRANSFERRED_TO_ADVISOR,
        ),
        DecisionRule(
            code="decision.quote",
            priority=700,
            required_signals=frozenset({SignalCode.REQUESTS_QUOTE}),
            action=RecommendedAction.NOTIFY_ADVISOR,
            explanation="A quote request should be visible to an advisor",
            handoff_required=True,
            handoff_reason="intent.quote",
            state_recommendation=LeadState.QUOTE_REQUESTED,
        ),
        DecisionRule(
            code="decision.out_of_stock_alternative_accepted",
            priority=690,
            required_signals=frozenset(
                {SignalCode.DEVICE_OUT_OF_STOCK, SignalCode.ACCEPTS_ALTERNATIVES}
            ),
            action=RecommendedAction.IMMEDIATE_HANDOFF,
            explanation="The requested device is unavailable and an alternative was accepted",
            handoff_required=True,
            handoff_reason="inventory.alternative_accepted",
            state_recommendation=LeadState.TRANSFERRED_TO_ADVISOR,
        ),
        DecisionRule(
            code="decision.human_review",
            priority=650,
            required_signals=frozenset({SignalCode.REQUIRES_HUMAN_REVIEW}),
            action=RecommendedAction.NOTIFY_ADVISOR,
            explanation="The normalized evidence requires human review",
            handoff_required=True,
            handoff_reason="qualification.human_review",
        ),
        DecisionRule(
            code="decision.contradiction",
            priority=600,
            required_signals=frozenset({SignalCode.CONTRADICTORY_INFORMATION}),
            action=RecommendedAction.NOTIFY_ADVISOR,
            explanation="Material contradictions require advisor review",
            handoff_required=True,
            handoff_reason="qualification.contradiction",
        ),
    )


def _classification_bands() -> tuple[ClassificationBand, ...]:
    return (
        ClassificationBand(
            0,
            29,
            LeadClassification.EXPLORATION,
            RecommendedAction.RESOLVE_AND_PRESENT_OPTIONS,
        ),
        ClassificationBand(
            30,
            49,
            LeadClassification.COLD,
            RecommendedAction.AUTOMATED_FOLLOW_UP,
        ),
        ClassificationBand(
            50,
            69,
            LeadClassification.WARM,
            RecommendedAction.COLLECT_MISSING_INFORMATION,
        ),
        ClassificationBand(
            70,
            84,
            LeadClassification.HOT,
            RecommendedAction.NOTIFY_ADVISOR,
        ),
        ClassificationBand(
            85,
            100,
            LeadClassification.PRIORITY,
            RecommendedAction.IMMEDIATE_HANDOFF,
        ),
    )
