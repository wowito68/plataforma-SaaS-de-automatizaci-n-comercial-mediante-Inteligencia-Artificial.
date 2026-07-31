from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError

from saas_platform.foundation import Uuid7Generator
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.postgres_queue import (
    CONSUMER_NAME,
    PostgresOutboxDispatcher,
    PostgresQueue,
    SyntheticEventWorker,
)
from saas_platform.infrastructure.schema import (
    audit_events,
    consumer_receipts,
    outbox_items,
    queue_messages,
    synthetic_events,
)
from saas_platform.modules.synthetic_events.application import ProcessSyntheticEvent
from saas_platform.modules.synthetic_events.ports import ProcessSyntheticEventCommand
from saas_platform.modules.synthetic_events.postgres import PostgresSyntheticEventStore
from saas_platform.modules.tenancy.domain import TenantId
from tests.conftest import ADAPTER_TOKEN
from tests.fakes import FixedIdGenerator, MutableClock

pytestmark = pytest.mark.integration


def _accept(client: TestClient, tenant_id: object, key: str) -> dict[str, object]:
    response = client.post(
        f"/internal/v1/tenants/{tenant_id}/synthetic-events",
        headers={
            "Authorization": f"Bearer {ADAPTER_TOKEN}",
            "Idempotency-Key": key,
        },
        json={"kind": "demo.worker", "attributes": {"key": key}},
    )
    assert response.status_code == 202
    body: dict[str, object] = response.json()
    return body


def test_worker_processes_event_once(
    api_context: tuple[FastAPI, TestClient],
    tenant_ids: tuple[object, object],
    admin_engine: Engine,
) -> None:
    api_app, client = api_context
    accepted = _accept(client, tenant_ids[0], "worker-happy")

    first = api_app.state.container.worker.run_cycle()
    second = api_app.state.container.worker.run_cycle()

    with admin_engine.connect() as connection:
        event = (
            connection.execute(
                select(synthetic_events).where(synthetic_events.c.id == accepted["id"])
            )
            .mappings()
            .one()
        )
        outbox_status = connection.execute(select(outbox_items.c.status)).scalar_one()
        queue_status = connection.execute(select(queue_messages.c.status)).scalar_one()
        receipts = connection.execute(
            select(func.count()).select_from(consumer_receipts)
        ).scalar_one()
        processed_audits = connection.execute(
            select(func.count())
            .select_from(audit_events)
            .where(audit_events.c.action == "synthetic_event.processed")
        ).scalar_one()

    assert first.dispatched == first.processed == 1
    assert first.failed == first.duplicates == 0
    assert second.dispatched == second.processed == 0
    assert event["status"] == "processed"
    assert event["version"] == 2
    assert outbox_status == "published"
    assert queue_status == "completed"
    assert receipts == processed_audits == 1


def test_expired_lease_and_partial_ack_recover_without_duplicate_effect(
    api_context: tuple[FastAPI, TestClient],
    tenant_ids: tuple[object, object],
    admin_engine: Engine,
) -> None:
    api_app, client = api_context
    accepted = _accept(client, tenant_ids[0], "worker-recovery")

    settings = api_app.state.container.settings
    database: Database = api_app.state.container.database
    clock = MutableClock()
    ids = Uuid7Generator()
    store = PostgresSyntheticEventStore(database, ids, clock)
    dispatcher = PostgresOutboxDispatcher(database, ids, clock)
    queue = PostgresQueue(database, clock, lease_seconds=5, max_attempts=3)
    processor = ProcessSyntheticEvent(store)
    dispatcher.dispatch_batch()
    claimed = queue.claim()
    assert claimed is not None

    event_id = UUID(str(claimed.payload["event_id"]))
    outcome = processor.execute(
        ProcessSyntheticEventCommand(
            tenant_id=TenantId(claimed.tenant_id),
            event_id=event_id,
            message_id=claimed.id,
            message_payload_hash=claimed.payload_hash,
            event_payload_hash=str(claimed.payload["payload_hash"]),
            correlation_id=claimed.correlation_id,
            consumer_name=CONSUMER_NAME,
        )
    )
    assert outcome.effect_applied is True

    clock.advance(seconds=settings.queue_lease_seconds + 1)
    recovered = SyntheticEventWorker(dispatcher, queue, processor).run_cycle()

    with admin_engine.connect() as connection:
        row = (
            connection.execute(
                select(synthetic_events).where(synthetic_events.c.id == accepted["id"])
            )
            .mappings()
            .one()
        )
        processed_audits = connection.execute(
            select(func.count())
            .select_from(audit_events)
            .where(audit_events.c.action == "synthetic_event.processed")
        ).scalar_one()

    assert recovered.duplicates == 1
    assert recovered.failed == 0
    assert row["version"] == 2
    assert processed_audits == 1


def test_dispatch_failure_rolls_back_and_preserves_pending_outbox(
    api_context: tuple[FastAPI, TestClient],
    tenant_ids: tuple[object, object],
    admin_engine: Engine,
) -> None:
    api_app, client = api_context
    _accept(client, tenant_ids[0], "dispatch-first")
    container = api_app.state.container
    container.worker.run_cycle()
    with admin_engine.connect() as connection:
        existing_message_id = connection.execute(select(queue_messages.c.id)).scalar_one()

    _accept(client, tenant_ids[0], "dispatch-second")
    failing_dispatcher = PostgresOutboxDispatcher(
        container.database,
        FixedIdGenerator(existing_message_id),
        MutableClock(),
    )

    with pytest.raises(IntegrityError):
        failing_dispatcher.dispatch_batch()

    with admin_engine.connect() as connection:
        pending = connection.execute(
            select(func.count()).select_from(outbox_items).where(outbox_items.c.status == "pending")
        ).scalar_one()
        messages = connection.execute(select(func.count()).select_from(queue_messages)).scalar_one()

    assert pending == 1
    assert messages == 1


def test_permanent_bad_message_moves_to_dead_letter_state(
    api_context: tuple[FastAPI, TestClient],
    tenant_ids: tuple[object, object],
    admin_engine: Engine,
) -> None:
    api_app, client = api_context
    _accept(client, tenant_ids[0], "worker-dead")
    container = api_app.state.container
    clock = MutableClock()
    dispatcher = PostgresOutboxDispatcher(container.database, Uuid7Generator(), clock)
    queue = PostgresQueue(container.database, clock, lease_seconds=5, max_attempts=3)
    dispatcher.dispatch_batch()
    with admin_engine.begin() as connection:
        connection.execute(queue_messages.update().values(message_type="unsupported.v1"))

    result = SyntheticEventWorker(
        dispatcher,
        queue,
        ProcessSyntheticEvent(
            PostgresSyntheticEventStore(container.database, Uuid7Generator(), clock)
        ),
    ).run_cycle()

    with admin_engine.connect() as connection:
        status = connection.execute(select(queue_messages.c.status)).scalar_one()
        error_type = connection.execute(select(queue_messages.c.last_error_type)).scalar_one()

    assert result.failed == 1
    assert status == "dead"
    assert error_type == "PermanentError"
