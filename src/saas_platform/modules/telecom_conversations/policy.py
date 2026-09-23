from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from saas_platform.modules.lead_qualification.default_policy import (
    build_default_telecom_policy,
)
from saas_platform.modules.lead_qualification.domain import OpportunityType, ScoringPolicy
from saas_platform.modules.tenancy.domain import TenantId

BASE_POLICY_PUBLISHED_AT = datetime(2026, 7, 31, tzinfo=UTC)


class CodeDefaultScoringPolicyProvider:
    """Materialize the Delivery 1 approved base policy for one tenant/opportunity."""

    def get_published(
        self,
        tenant_id: TenantId,
        opportunity_type: OpportunityType,
    ) -> ScoringPolicy:
        policy_id = uuid5(
            NAMESPACE_URL,
            f"saas-platform:telecom-policy:{tenant_id.value}:{opportunity_type.value}:v1",
        )
        return build_default_telecom_policy(
            tenant_id=tenant_id,
            policy_id=policy_id,
            opportunity_type=opportunity_type,
            published_at=BASE_POLICY_PUBLISHED_AT,
            version=1,
        )
