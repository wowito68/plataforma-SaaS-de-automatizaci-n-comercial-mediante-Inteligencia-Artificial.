from uuid import UUID

from saas_platform.modules.synthetic_events.domain import (
    AcceptedSyntheticEvent,
    ProcessedSyntheticEvent,
    SyntheticEvent,
)
from saas_platform.modules.synthetic_events.ports import (
    AcceptSyntheticEventCommand,
    ProcessSyntheticEventCommand,
    SyntheticEventProcessingStore,
    SyntheticEventStore,
)
from saas_platform.modules.tenancy.domain import TenantId


class AcceptSyntheticEvent:
    def __init__(self, store: SyntheticEventStore) -> None:
        self._store = store

    def execute(self, command: AcceptSyntheticEventCommand) -> AcceptedSyntheticEvent:
        return self._store.accept(command)


class GetSyntheticEvent:
    def __init__(self, store: SyntheticEventStore) -> None:
        self._store = store

    def execute(self, tenant_id: TenantId, event_id: UUID) -> SyntheticEvent:
        return self._store.get(tenant_id, event_id)


class ProcessSyntheticEvent:
    def __init__(self, store: SyntheticEventProcessingStore) -> None:
        self._store = store

    def execute(self, command: ProcessSyntheticEventCommand) -> ProcessedSyntheticEvent:
        return self._store.process(command)
