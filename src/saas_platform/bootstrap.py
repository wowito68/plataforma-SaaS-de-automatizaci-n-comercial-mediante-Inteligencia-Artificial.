from dataclasses import dataclass

from saas_platform.config import Settings
from saas_platform.foundation import SystemClock, Uuid7Generator
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.postgres_queue import (
    PostgresOutboxDispatcher,
    PostgresQueue,
    SyntheticEventWorker,
)
from saas_platform.modules.synthetic_events.application import (
    AcceptSyntheticEvent,
    GetSyntheticEvent,
    ProcessSyntheticEvent,
)
from saas_platform.modules.synthetic_events.postgres import PostgresSyntheticEventStore


@dataclass(slots=True)
class Container:
    settings: Settings
    database: Database
    accept_synthetic_event: AcceptSyntheticEvent
    get_synthetic_event: GetSyntheticEvent
    worker: SyntheticEventWorker

    @classmethod
    def build(cls, settings: Settings) -> "Container":
        database = Database(settings)
        ids = Uuid7Generator()
        clock = SystemClock()
        event_store = PostgresSyntheticEventStore(database, ids, clock)
        dispatcher = PostgresOutboxDispatcher(database, ids, clock)
        queue = PostgresQueue(
            database,
            clock,
            lease_seconds=settings.queue_lease_seconds,
            max_attempts=settings.queue_max_attempts,
        )
        worker = SyntheticEventWorker(
            dispatcher,
            queue,
            ProcessSyntheticEvent(event_store),
        )
        return cls(
            settings=settings,
            database=database,
            accept_synthetic_event=AcceptSyntheticEvent(event_store),
            get_synthetic_event=GetSyntheticEvent(event_store),
            worker=worker,
        )

    def close(self) -> None:
        self.database.dispose()
