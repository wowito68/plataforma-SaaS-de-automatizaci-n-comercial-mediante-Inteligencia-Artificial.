from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from saas_platform.modules.synthetic_events.domain import (
    AcceptedSyntheticEvent,
    IdempotencyKey,
    ProcessedSyntheticEvent,
    SyntheticEvent,
    SyntheticPayload,
)
from saas_platform.modules.tenancy.domain import TenantId


@dataclass(frozen=True, slots=True)
class AcceptSyntheticEventCommand:
    tenant_id: TenantId
    idempotency_key: IdempotencyKey
    payload: SyntheticPayload
    correlation_id: UUID


@dataclass(frozen=True, slots=True)
class ProcessSyntheticEventCommand:
    tenant_id: TenantId
    event_id: UUID
    message_id: UUID
    message_payload_hash: str
    event_payload_hash: str
    correlation_id: UUID
    consumer_name: str


class SyntheticEventStore(Protocol):
    def accept(self, command: AcceptSyntheticEventCommand) -> AcceptedSyntheticEvent: ...

    def get(self, tenant_id: TenantId, event_id: UUID) -> SyntheticEvent: ...


class SyntheticEventProcessingStore(Protocol):
    def process(self, command: ProcessSyntheticEventCommand) -> ProcessedSyntheticEvent: ...
