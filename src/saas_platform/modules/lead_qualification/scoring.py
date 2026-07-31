from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from saas_platform.errors import ConflictError, ForbiddenError, ValidationError
from saas_platform.modules.lead_qualification.domain import (
    ENGINE_VERSION,
    AppliedPenalty,
    CommercialSignal,
    DecisionRule,
    DimensionScore,
    HandoffDecision,
    LeadClassification,
    LeadState,
    PenaltyRule,
    PolicyReference,
    PolicyStatus,
    ProfileField,
    QualificationExplanation,
    QualificationRequest,
    QualificationResult,
    RecommendedAction,
    RuleEffect,
    ScoreContribution,
    ScoreDimension,
    ScoringCriterion,
    ScoringPolicy,
    SignalCode,
    ValidationStatus,
)

HANDOFF_ACTIONS = frozenset(
    {
        RecommendedAction.NOTIFY_ADVISOR,
        RecommendedAction.IMMEDIATE_HANDOFF,
        RecommendedAction.SPECIALIZED_HANDOFF,
    }
)


class LeadScoringEngine:
    def evaluate(
        self,
        request: QualificationRequest,
        policy: ScoringPolicy,
        *,
        evaluation_id: UUID,
        evaluated_at: datetime,
    ) -> QualificationResult:
        self._ensure_policy_applies(request, policy)
        signals = self._usable_signals(request, policy)
        signal_codes = frozenset(signals)

        criteria = self._select_criteria(policy.criteria, signal_codes)
        contributions = tuple(
            ScoreContribution(
                rule_code=criterion.code,
                dimension=criterion.dimension,
                points=criterion.points,
                signal_codes=tuple(sorted(criterion.required_signals, key=lambda item: item.value)),
                explanation=criterion.explanation,
            )
            for criterion in criteria
        )
        dimensions = self._dimension_scores(policy, contributions)
        subtotal = sum(item.points for item in dimensions)

        matched_penalties = tuple(
            item
            for item in sorted(policy.penalties, key=lambda value: value.code)
            if item.required_signals <= signal_codes
        )
        penalties = tuple(self._applied_penalty(item) for item in matched_penalties)
        score = max(0, min(100, subtotal + sum(item.adjustment for item in penalties)))
        classification, classification_action = self._classification(policy, score)

        matched_decisions = tuple(
            item
            for item in sorted(policy.decisions, key=lambda value: (-value.priority, value.code))
            if item.required_signals <= signal_codes
        )
        action, selected_decision = self._recommended_action(
            classification_action,
            matched_penalties,
            matched_decisions,
        )
        handoff = self._handoff(action, classification, selected_decision, matched_decisions)
        state_recommendation = self._state_recommendation(selected_decision, penalties)
        blocked_offers = tuple(
            sorted({item.blocked_offer for item in penalties if item.blocked_offer is not None})
        )
        effects = tuple(sorted({item.effect for item in penalties}, key=lambda item: item.value))
        used_signal_codes = self._signals_used(contributions, penalties, matched_decisions)
        explanation = self._explanation(
            score=score,
            classification=classification,
            action=action,
            contributions=contributions,
            penalties=penalties,
            decisions=matched_decisions,
            missing_information=request.profile.missing_information,
            blocked_offers=blocked_offers,
        )

        return QualificationResult(
            id=evaluation_id,
            tenant_id=request.profile.tenant_id,
            lead_id=request.profile.lead_id,
            conversation_id=request.profile.conversation_id,
            opportunity=request.profile.opportunity,
            score=score,
            subtotal=subtotal,
            classification=classification,
            dimensions=dimensions,
            contributions=contributions,
            penalties=penalties,
            effects=effects,
            action=action,
            handoff=handoff,
            eligibility=request.eligibility,
            state_recommendation=state_recommendation,
            policy=PolicyReference(
                id=policy.id,
                tenant_id=policy.tenant_id,
                version=policy.version,
                opportunity_type=policy.opportunity_type,
                fingerprint=policy.fingerprint,
            ),
            engine_version=ENGINE_VERSION,
            evaluated_at=evaluated_at,
            cause=request.cause,
            trigger_id=request.trigger_id,
            correlation_id=request.correlation_id,
            extraction=request.extraction,
            signals_used=used_signal_codes,
            explanation=explanation,
        )

    @staticmethod
    def _ensure_policy_applies(
        request: QualificationRequest,
        policy: ScoringPolicy,
    ) -> None:
        if policy.status is not PolicyStatus.PUBLISHED:
            raise ConflictError("only published scoring policies can evaluate leads")
        if policy.tenant_id != request.profile.tenant_id:
            raise ForbiddenError("scoring policy belongs to a different tenant")
        if policy.opportunity_type is not request.profile.opportunity.primary:
            raise ValidationError("scoring policy does not match the primary opportunity")

    @staticmethod
    def _usable_signals(
        request: QualificationRequest,
        policy: ScoringPolicy,
    ) -> dict[SignalCode, CommercialSignal]:
        selected: dict[SignalCode, CommercialSignal] = {}
        for signal in request.signals:
            if not signal.is_usable(policy.minimum_signal_confidence):
                continue
            current = selected.get(signal.code)
            if current is None or LeadScoringEngine._signal_rank(signal) > (
                LeadScoringEngine._signal_rank(current)
            ):
                selected[signal.code] = signal
        return selected

    @staticmethod
    def _signal_rank(signal: CommercialSignal) -> tuple[int, Decimal, datetime]:
        validation_rank = {
            ValidationStatus.HYPOTHESIS: 0,
            ValidationStatus.ACCEPTED: 1,
            ValidationStatus.CONFIRMED: 2,
            ValidationStatus.REJECTED: -1,
        }
        return validation_rank[signal.validation_status], signal.confidence, signal.observed_at

    @staticmethod
    def _select_criteria(
        criteria: tuple[ScoringCriterion, ...],
        signal_codes: frozenset[SignalCode],
    ) -> tuple[ScoringCriterion, ...]:
        matched = [
            item
            for item in criteria
            if item.required_signals <= signal_codes and not (item.excluded_signals & signal_codes)
        ]
        ungrouped = [item for item in matched if item.exclusive_group is None]
        groups: dict[tuple[ScoreDimension, str], list[ScoringCriterion]] = defaultdict(list)
        for item in matched:
            if item.exclusive_group is not None:
                groups[(item.dimension, item.exclusive_group)].append(item)

        selected = list(ungrouped)
        for values in groups.values():
            selected.append(sorted(values, key=lambda item: (-item.points, item.code))[0])
        return tuple(sorted(selected, key=lambda item: (item.dimension.value, item.code)))

    @staticmethod
    def _dimension_scores(
        policy: ScoringPolicy,
        contributions: tuple[ScoreContribution, ...],
    ) -> tuple[DimensionScore, ...]:
        raw_points: dict[ScoreDimension, int] = defaultdict(int)
        for contribution in contributions:
            raw_points[contribution.dimension] += contribution.points
        return tuple(
            DimensionScore(
                dimension=definition.dimension,
                raw_points=raw_points[definition.dimension],
                points=min(raw_points[definition.dimension], definition.max_points),
                maximum=definition.max_points,
            )
            for definition in policy.dimensions
        )

    @staticmethod
    def _applied_penalty(rule: PenaltyRule) -> AppliedPenalty:
        return AppliedPenalty(
            rule_code=rule.code,
            adjustment=rule.adjustment,
            effect=rule.effect,
            signal_codes=tuple(sorted(rule.required_signals, key=lambda item: item.value)),
            explanation=rule.explanation,
            blocked_offer=rule.blocked_offer,
        )

    @staticmethod
    def _classification(
        policy: ScoringPolicy,
        score: int,
    ) -> tuple[LeadClassification, RecommendedAction]:
        for band in policy.classification_bands:
            if band.minimum <= score <= band.maximum:
                return band.classification, band.action
        raise ValidationError("score did not match a classification band")

    @staticmethod
    def _recommended_action(
        classification_action: RecommendedAction,
        penalties: tuple[PenaltyRule, ...],
        decisions: tuple[DecisionRule, ...],
    ) -> tuple[RecommendedAction, DecisionRule | None]:
        if any(item.effect is RuleEffect.DO_NOT_CONTACT for item in penalties):
            do_not_contact_decision = next(
                (item for item in decisions if SignalCode.DO_NOT_CONTACT in item.required_signals),
                None,
            )
            return RecommendedAction.STOP_AUTOMATION, do_not_contact_decision
        if decisions:
            return decisions[0].action, decisions[0]
        if classification_action not in HANDOFF_ACTIONS:
            penalty_actions = sorted(
                {
                    item.recommended_action
                    for item in penalties
                    if item.recommended_action is not None
                },
                key=lambda item: item.value,
            )
            if penalty_actions:
                return penalty_actions[0], None
        return classification_action, None

    @staticmethod
    def _handoff(
        action: RecommendedAction,
        classification: LeadClassification,
        selected_decision: DecisionRule | None,
        matched_decisions: tuple[DecisionRule, ...],
    ) -> HandoffDecision:
        if action is RecommendedAction.STOP_AUTOMATION:
            return HandoffDecision(required=False, specialized=False, reason_codes=())
        if selected_decision is not None and not selected_decision.handoff_required:
            return HandoffDecision(required=False, specialized=False, reason_codes=())
        decision_reasons = tuple(
            item.handoff_reason
            for item in matched_decisions
            if item.handoff_required and item.handoff_reason is not None
        )
        required = action in HANDOFF_ACTIONS or bool(decision_reasons)
        reasons = decision_reasons
        if required and not reasons:
            reasons = (f"score.{classification.value}",)
        return HandoffDecision(
            required=required,
            specialized=(
                selected_decision.specialized_handoff
                if selected_decision is not None
                else action is RecommendedAction.SPECIALIZED_HANDOFF
            ),
            reason_codes=reasons,
        )

    @staticmethod
    def _state_recommendation(
        selected_decision: DecisionRule | None,
        penalties: tuple[AppliedPenalty, ...],
    ) -> LeadState | None:
        if selected_decision is not None and selected_decision.state_recommendation is not None:
            return selected_decision.state_recommendation
        if any(item.effect is RuleEffect.DO_NOT_CONTACT for item in penalties):
            return LeadState.DO_NOT_CONTACT
        if any(item.effect is RuleEffect.DISQUALIFICATION for item in penalties):
            return LeadState.NOT_ELIGIBLE
        return None

    @staticmethod
    def _signals_used(
        contributions: tuple[ScoreContribution, ...],
        penalties: tuple[AppliedPenalty, ...],
        decisions: tuple[DecisionRule, ...],
    ) -> tuple[SignalCode, ...]:
        used: set[SignalCode] = set()
        for contribution in contributions:
            used.update(contribution.signal_codes)
        for penalty in penalties:
            used.update(penalty.signal_codes)
        for decision in decisions:
            used.update(decision.required_signals)
        return tuple(sorted(used, key=lambda item: item.value))

    @staticmethod
    def _explanation(
        *,
        score: int,
        classification: LeadClassification,
        action: RecommendedAction,
        contributions: tuple[ScoreContribution, ...],
        penalties: tuple[AppliedPenalty, ...],
        decisions: tuple[DecisionRule, ...],
        missing_information: tuple[ProfileField, ...],
        blocked_offers: tuple[str, ...],
    ) -> QualificationExplanation:
        positive_reasons = tuple(item.explanation for item in contributions)
        penalty_reasons = tuple(item.explanation for item in penalties)
        applied_rules = tuple(item.rule_code for item in contributions)
        applied_rules += tuple(item.rule_code for item in penalties)
        applied_rules += tuple(item.code for item in decisions)

        summary_parts = [
            f"{classification.value} lead with {score}/100",
            f"recommended action: {action.value}",
        ]
        if contributions:
            strongest = sorted(
                contributions,
                key=lambda item: (-item.points, item.rule_code),
            )[0]
            summary_parts.append(f"strongest evidence: {strongest.explanation}")
        if penalty_reasons:
            summary_parts.append(f"penalty: {penalty_reasons[0]}")
        if missing_information:
            summary_parts.append(f"missing information: {len(missing_information)} field(s)")

        return QualificationExplanation(
            summary="; ".join(summary_parts) + ".",
            positive_reasons=positive_reasons,
            penalty_reasons=penalty_reasons,
            applied_rule_codes=applied_rules,
            missing_information=missing_information,
            blocked_offers=blocked_offers,
        )
