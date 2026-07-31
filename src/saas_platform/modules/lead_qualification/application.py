from saas_platform.foundation import Clock, IdGenerator
from saas_platform.modules.lead_qualification.domain import (
    QualificationRequest,
    QualificationResult,
)
from saas_platform.modules.lead_qualification.ports import ScoringPolicyProvider
from saas_platform.modules.lead_qualification.scoring import LeadScoringEngine


class EvaluateLeadQualification:
    def __init__(
        self,
        policies: ScoringPolicyProvider,
        scoring_engine: LeadScoringEngine,
        id_generator: IdGenerator,
        clock: Clock,
    ) -> None:
        self._policies = policies
        self._scoring_engine = scoring_engine
        self._id_generator = id_generator
        self._clock = clock

    def execute(self, request: QualificationRequest) -> QualificationResult:
        policy = self._policies.get_published(
            request.profile.tenant_id,
            request.profile.opportunity.primary,
        )
        return self._scoring_engine.evaluate(
            request,
            policy,
            evaluation_id=self._id_generator.new(),
            evaluated_at=self._clock.now(),
        )
