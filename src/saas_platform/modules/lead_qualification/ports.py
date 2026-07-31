from typing import Protocol

from saas_platform.modules.lead_qualification.domain import OpportunityType, ScoringPolicy
from saas_platform.modules.tenancy.domain import TenantId


class ScoringPolicyProvider(Protocol):
    def get_published(
        self,
        tenant_id: TenantId,
        opportunity_type: OpportunityType,
    ) -> ScoringPolicy: ...
