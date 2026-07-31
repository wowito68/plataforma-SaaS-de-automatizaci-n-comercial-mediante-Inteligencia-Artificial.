import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import RowMapping

from saas_platform.errors import InfrastructureError, PermanentError
from saas_platform.foundation import Clock, IdGenerator
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.schema import outbox_items, queue_messages
from saas_platform.modules.synthetic_events.application import ProcessSyntheticEvent
from saas_platform.modules.synthetic_events.ports import ProcessSyntheticEventCommand
from saas_platform.modules.tenancy.domain import TenantId
from saas_platform.observability import (
    OUTBOX_DISPATCHES,
    OUTBOX_PENDING,
    WORKER_MESSAGES,
    bind_context,
    reset_context,
)

logger = logging.getLogger(__name__)
CONSUMER_NAME = "synthetic-event-worker.v1"
MESSAGE_TYPE = "synthetic_event.accepted.v1"


@dataclass(frozen=True, slots=True)
class ClaimedMessage:
    tenant_id: UUID
    id: UUID
    message_type: str
    payload: dict[str, Any]
    payload_hash: str
    correlation_id: UUID
    attempts: int


@dataclass(frozen=True, slots=True)
class WorkerCycleResult:
    dispatched: int
    processed: int
    duplicates: int
    failed: int


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PostgresOutboxDispatcher:
    def __init__(self, database: Database, ids: IdGenerator, clock: Clock) -> None:
        self._database = database
        self._ids = ids
        self._clock = clock

    def dispatch_batch(self, *, limit: int = 50) -> int:
        now = self._clock.now()
        with self._database.worker_transaction() as connection:
            pending = (
                connection.execute(
                    select(outbox_items)
                    .where(
                        outbox_items.c.status == "pending",
                        outbox_items.c.available_at <= now,
                    )
                    .order_by(outbox_items.c.available_at, outbox_items.c.id)
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
                .mappings()
                .all()
            )

            for item in pending:
                payload = dict(item["payload"])
                connection.execute(
                    postgres_insert(queue_messages)
                    .values(
                        tenant_id=item["tenant_id"],
                        id=self._ids.new(),
                        source_outbox_id=item["id"],
                        message_type=item["event_type"],
                        payload=payload,
                        payload_hash=_payload_hash(payload),
                        correlation_id=item["correlation_id"],
                        status="available",
                        attempts=0,
                        available_at=now,
                        lease_until=None,
                        created_at=now,
                        completed_at=None,
                        last_error_type=None,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            queue_messages.c.tenant_id,
                            queue_messages.c.source_outbox_id,
                        ]
                    )
                )
                connection.execute(
                    update(outbox_items)
                    .where(
                        outbox_items.c.tenant_id == item["tenant_id"],
                        outbox_items.c.id == item["id"],
                        outbox_items.c.status == "pending",
                    )
                    .values(
                        status="published",
                        attempts=outbox_items.c.attempts + 1,
                        published_at=now,
                    )
                )

            count = connection.execute(
                select(func.count())
                .select_from(outbox_items)
                .where(outbox_items.c.status == "pending")
            ).scalar_one()
            OUTBOX_PENDING.set(count)

        if pending:
            OUTBOX_DISPATCHES.labels(result="published").inc(len(pending))
            logger.info(
                "outbox batch dispatched",
                extra={"operation": "outbox.dispatch", "result": "published"},
            )
        return len(pending)


class PostgresQueue:
    def __init__(
        self,
        database: Database,
        clock: Clock,
        *,
        lease_seconds: int,
        max_attempts: int,
    ) -> None:
        self._database = database
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    def claim(self) -> ClaimedMessage | None:
        now = self._clock.now()
        with self._database.worker_transaction() as connection:
            candidate = connection.execute(
                select(queue_messages.c.tenant_id, queue_messages.c.id)
                .where(
                    queue_messages.c.available_at <= now,
                    or_(
                        queue_messages.c.status == "available",
                        and_(
                            queue_messages.c.status == "leased",
                            queue_messages.c.lease_until <= now,
                        ),
                    ),
                )
                .order_by(queue_messages.c.available_at, queue_messages.c.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            ).one_or_none()
            if candidate is None:
                return None

            row = (
                connection.execute(
                    update(queue_messages)
                    .where(
                        queue_messages.c.tenant_id == candidate.tenant_id,
                        queue_messages.c.id == candidate.id,
                    )
                    .values(
                        status="leased",
                        attempts=queue_messages.c.attempts + 1,
                        lease_until=now + timedelta(seconds=self._lease_seconds),
                        last_error_type=None,
                    )
                    .returning(*queue_messages.c)
                )
                .mappings()
                .one()
            )
        return self._to_message(row)

    def complete(self, message: ClaimedMessage) -> None:
        now = self._clock.now()
        with self._database.worker_transaction() as connection:
            result = connection.execute(
                update(queue_messages)
                .where(
                    queue_messages.c.tenant_id == message.tenant_id,
                    queue_messages.c.id == message.id,
                    queue_messages.c.status == "leased",
                )
                .values(status="completed", completed_at=now, lease_until=None)
            )
            if result.rowcount != 1:
                status = connection.execute(
                    select(queue_messages.c.status).where(
                        queue_messages.c.tenant_id == message.tenant_id,
                        queue_messages.c.id == message.id,
                    )
                ).scalar_one_or_none()
                if status != "completed":
                    raise InfrastructureError("claimed queue message could not be completed")

    def fail(self, message: ClaimedMessage, error: Exception) -> None:
        now = self._clock.now()
        is_dead = isinstance(error, PermanentError) or message.attempts >= self._max_attempts
        delay = min(60, 2**message.attempts)
        with self._database.worker_transaction() as connection:
            connection.execute(
                update(queue_messages)
                .where(
                    queue_messages.c.tenant_id == message.tenant_id,
                    queue_messages.c.id == message.id,
                    queue_messages.c.status == "leased",
                )
                .values(
                    status="dead" if is_dead else "available",
                    available_at=now + timedelta(seconds=delay),
                    lease_until=None,
                    last_error_type=type(error).__name__,
                )
            )

    @staticmethod
    def _to_message(row: RowMapping) -> ClaimedMessage:
        return ClaimedMessage(
            tenant_id=row["tenant_id"],
            id=row["id"],
            message_type=row["message_type"],
            payload=dict(row["payload"]),
            payload_hash=row["payload_hash"],
            correlation_id=row["correlation_id"],
            attempts=row["attempts"],
        )


class SyntheticEventWorker:
    def __init__(
        self,
        dispatcher: PostgresOutboxDispatcher,
        queue: PostgresQueue,
        processor: ProcessSyntheticEvent,
    ) -> None:
        self._dispatcher = dispatcher
        self._queue = queue
        self._processor = processor

    def run_cycle(self, *, batch_size: int = 50) -> WorkerCycleResult:
        dispatched = self._dispatcher.dispatch_batch(limit=batch_size)
        processed = 0
        duplicates = 0
        failed = 0

        for _ in range(batch_size):
            message = self._queue.claim()
            if message is None:
                break
            bound = bind_context(
                correlation_id=message.correlation_id,
                tenant_id=message.tenant_id,
            )
            try:
                command = self._decode(message)
                outcome = self._processor.execute(command)
                self._queue.complete(message)
                if outcome.effect_applied:
                    processed += 1
                    result = "processed"
                else:
                    duplicates += 1
                    result = "duplicate"
                WORKER_MESSAGES.labels(result=result).inc()
                logger.info(
                    "queue message handled",
                    extra={
                        "message_id": str(message.id),
                        "operation": "synthetic_event.process",
                        "result": result,
                    },
                )
            except Exception as error:
                failed += 1
                self._queue.fail(message, error)
                WORKER_MESSAGES.labels(result="failed").inc()
                logger.exception(
                    "queue message failed",
                    extra={
                        "message_id": str(message.id),
                        "operation": "synthetic_event.process",
                        "error_type": type(error).__name__,
                        "result": "failed",
                    },
                )
            finally:
                reset_context(bound)

        return WorkerCycleResult(dispatched, processed, duplicates, failed)

    @staticmethod
    def _decode(message: ClaimedMessage) -> ProcessSyntheticEventCommand:
        if message.message_type != MESSAGE_TYPE:
            raise PermanentError("queue message type is not supported")
        try:
            event_id = UUID(str(message.payload["event_id"]))
            payload_tenant_id = UUID(str(message.payload["tenant_id"]))
            event_payload_hash = str(message.payload["payload_hash"])
        except (KeyError, TypeError, ValueError) as error:
            raise PermanentError("queue message payload is invalid") from error
        if payload_tenant_id != message.tenant_id:
            raise PermanentError("queue message tenant does not match its envelope")
        if len(event_payload_hash) != 64:
            raise PermanentError("queue message event payload hash is invalid")
        return ProcessSyntheticEventCommand(
            tenant_id=TenantId(message.tenant_id),
            event_id=event_id,
            message_id=message.id,
            message_payload_hash=message.payload_hash,
            event_payload_hash=event_payload_hash,
            correlation_id=message.correlation_id,
            consumer_name=CONSUMER_NAME,
        )
