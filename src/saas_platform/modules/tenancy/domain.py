from dataclasses import dataclass
from uuid import UUID

from saas_platform.errors import ValidationError


@dataclass(frozen=True, slots=True)
class TenantId:
    value: UUID

    @classmethod
    def parse(cls, raw: str | UUID) -> "TenantId":
        if isinstance(raw, UUID):
            return cls(raw)
        try:
            return cls(UUID(raw))
        except ValueError as error:
            raise ValidationError("tenant_id must be a valid UUID") from error


@dataclass(frozen=True, slots=True)
class Tenant:
    id: TenantId
    name: str
    status: str = "active"

    def __post_init__(self) -> None:
        normalized = self.name.strip()
        if not normalized or len(normalized) > 120:
            raise ValidationError("tenant name must contain between 1 and 120 characters")
        if self.status not in {"active", "suspended"}:
            raise ValidationError("tenant status is invalid")
        object.__setattr__(self, "name", normalized)
