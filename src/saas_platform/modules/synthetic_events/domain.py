import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from saas_platform.errors import ValidationError
from saas_platform.modules.tenancy.domain import TenantId

type SyntheticScalar = str | int | bool
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
EVENT_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not 1 <= len(self.value) <= 128:
            raise ValidationError("idempotency key must contain between 1 and 128 characters")
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(self.value):
            raise ValidationError("idempotency key contains unsupported characters")


@dataclass(frozen=True, slots=True)
class SyntheticPayload:
    kind: str
    attributes: Mapping[str, SyntheticScalar]

    def __post_init__(self) -> None:
        if not EVENT_KIND_PATTERN.fullmatch(self.kind) or len(self.kind) > 64:
            raise ValidationError("event kind is invalid")
        if len(self.attributes) > 20:
            raise ValidationError("synthetic payload accepts at most 20 attributes")
        normalized: dict[str, SyntheticScalar] = {}
        for key, value in self.attributes.items():
            if not key or len(key) > 64 or not EVENT_KIND_PATTERN.fullmatch(key):
                raise ValidationError("synthetic attribute name is invalid")
            if isinstance(value, str) and len(value) > 256:
                raise ValidationError("synthetic string attributes accept at most 256 characters")
            normalized[key] = value
        object.__setattr__(self, "attributes", MappingProxyType(normalized))

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "attributes": dict(self.attributes)}

    def canonical_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class SyntheticEventStatus(StrEnum):
    ACCEPTED = "accepted"
    PROCESSED = "processed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SyntheticEvent:
    id: UUID
    tenant_id: TenantId
    idempotency_key: IdempotencyKey
    payload: SyntheticPayload
    status: SyntheticEventStatus
    correlation_id: UUID
    created_at: datetime
    processed_at: datetime | None
    version: int


@dataclass(frozen=True, slots=True)
class AcceptedSyntheticEvent:
    event: SyntheticEvent
    created: bool


@dataclass(frozen=True, slots=True)
class ProcessedSyntheticEvent:
    event_id: UUID
    effect_applied: bool
