from uuid import UUID

from sqlalchemy import Connection, insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import RowMapping

from saas_platform.errors import ConflictError, NotFoundError, PermanentError
from saas_platform.foundation import Clock, IdGenerator
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.schema import (
    audit_events,
    consumer_receipts,
    outbox_items,
    synthetic_events,
    tenants,
)
from saas_platform.modules.synthetic_events.domain import (
    AcceptedSyntheticEvent,
    IdempotencyKey,
    ProcessedSyntheticEvent,
    SyntheticEvent,
    SyntheticEventStatus,
    SyntheticPayload,
)
from saas_platform.modules.synthetic_events.ports import (
    AcceptSyntheticEventCommand,
    ProcessSyntheticEventCommand,
)
from saas_platform.modules.tenancy.domain import TenantId


class PostgresSyntheticEventStore:
    def __init__(self, database: Database, ids: IdGenerator, clock: Clock) -> None:
        self._database = database
        self._ids = ids
        self._clock = clock

    def accept(self, command: AcceptSyntheticEventCommand) -> AcceptedSyntheticEvent:
        now = self._clock.now()
        event_id = self._ids.new()
        payload = command.payload.as_dict()
        payload_hash = command.payload.sha256()
        with self._database.tenant_transaction(command.tenant_id.value) as connection:
            self._require_active_tenant(connection, command.tenant_id)
            inserted = (
                connection.execute(
                    postgres_insert(synthetic_events)
                    .values(
                        tenant_id=command.tenant_id.value,
                        id=event_id,
                        idempotency_key=command.idempotency_key.value,
                        payload=payload,
                        payload_hash=payload_hash,
                        status=SyntheticEventStatus.ACCEPTED.value,
                        correlation_id=command.correlation_id,
                        created_at=now,
                        processed_at=None,
                        version=1,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            synthetic_events.c.tenant_id,
                            synthetic_events.c.idempotency_key,
                        ]
                    )
                    .returning(*synthetic_events.c)
                )
                .mappings()
                .one_or_none()
            )

            if inserted is None:
                existing = self._get_by_idempotency_key(
                    connection, command.tenant_id, command.idempotency_key
                )
                if existing["payload_hash"] != payload_hash:
                    raise ConflictError(
                        "idempotency key was already used with a different payload",
                        details={"idempotency_key": command.idempotency_key.value},
                    )
                return AcceptedSyntheticEvent(self._to_domain(existing), created=False)

            outbox_id = self._ids.new()
            envelope = {
                "event_id": str(event_id),
                "tenant_id": str(command.tenant_id.value),
                "payload_hash": payload_hash,
            }
            connection.execute(
                insert(outbox_items).values(
                    tenant_id=command.tenant_id.value,
                    id=outbox_id,
                    aggregate_type="synthetic_event",
                    aggregate_id=event_id,
                    event_type="synthetic_event.accepted.v1",
                    payload=envelope,
                    correlation_id=command.correlation_id,
                    status="pending",
                    attempts=0,
                    available_at=now,
                    created_at=now,
                    published_at=None,
                )
            )
            connection.execute(
                insert(audit_events).values(
                    tenant_id=command.tenant_id.value,
                    id=self._ids.new(),
                    action="synthetic_event.accepted",
                    target_type="synthetic_event",
                    target_id=event_id,
                    actor_type="internal_adapter",
                    actor_id=None,
                    correlation_id=command.correlation_id,
                    metadata={"event_kind": command.payload.kind},
                    created_at=now,
                )
            )
            return AcceptedSyntheticEvent(self._to_domain(inserted), created=True)

    def get(self, tenant_id: TenantId, event_id: UUID) -> SyntheticEvent:
        with self._database.tenant_transaction(tenant_id.value) as connection:
            row = (
                connection.execute(
                    select(synthetic_events).where(
                        synthetic_events.c.tenant_id == tenant_id.value,
                        synthetic_events.c.id == event_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise NotFoundError("synthetic event was not found")
            return self._to_domain(row)

    def process(self, command: ProcessSyntheticEventCommand) -> ProcessedSyntheticEvent:
        now = self._clock.now()
        with self._database.worker_transaction() as connection:
            event = (
                connection.execute(
                    select(synthetic_events)
                    .where(
                        synthetic_events.c.tenant_id == command.tenant_id.value,
                        synthetic_events.c.id == command.event_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if event is None:
                raise PermanentError("synthetic event referenced by the message was not found")
            if event["payload_hash"] != command.event_payload_hash:
                raise PermanentError("synthetic event payload hash does not match the message")

            inserted_receipt = connection.execute(
                postgres_insert(consumer_receipts)
                .values(
                    tenant_id=command.tenant_id.value,
                    consumer_name=command.consumer_name,
                    message_id=command.message_id,
                    payload_hash=command.message_payload_hash,
                    processed_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        consumer_receipts.c.tenant_id,
                        consumer_receipts.c.consumer_name,
                        consumer_receipts.c.message_id,
                    ]
                )
                .returning(consumer_receipts.c.message_id)
            ).scalar_one_or_none()
            if inserted_receipt is None:
                receipt_hash = connection.execute(
                    select(consumer_receipts.c.payload_hash).where(
                        consumer_receipts.c.tenant_id == command.tenant_id.value,
                        consumer_receipts.c.consumer_name == command.consumer_name,
                        consumer_receipts.c.message_id == command.message_id,
                    )
                ).scalar_one()
                if receipt_hash != command.message_payload_hash:
                    raise PermanentError("message identifier was reused with a different payload")
                return ProcessedSyntheticEvent(command.event_id, effect_applied=False)

            if event["status"] == SyntheticEventStatus.PROCESSED.value:
                return ProcessedSyntheticEvent(command.event_id, effect_applied=False)
            if event["status"] != SyntheticEventStatus.ACCEPTED.value:
                raise PermanentError("synthetic event is not in a processable state")

            connection.execute(
                synthetic_events.update()
                .where(
                    synthetic_events.c.tenant_id == command.tenant_id.value,
                    synthetic_events.c.id == command.event_id,
                    synthetic_events.c.status == SyntheticEventStatus.ACCEPTED.value,
                )
                .values(
                    status=SyntheticEventStatus.PROCESSED.value,
                    processed_at=now,
                    version=synthetic_events.c.version + 1,
                )
            )
            connection.execute(
                insert(audit_events).values(
                    tenant_id=command.tenant_id.value,
                    id=self._ids.new(),
                    action="synthetic_event.processed",
                    target_type="synthetic_event",
                    target_id=command.event_id,
                    actor_type="worker",
                    actor_id=command.consumer_name,
                    correlation_id=command.correlation_id,
                    metadata={"message_id": str(command.message_id)},
                    created_at=now,
                )
            )
            return ProcessedSyntheticEvent(command.event_id, effect_applied=True)

    @staticmethod
    def _require_active_tenant(connection: Connection, tenant_id: TenantId) -> None:
        exists = connection.execute(
            select(tenants.c.id).where(
                tenants.c.id == tenant_id.value,
                tenants.c.status == "active",
            )
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError("active tenant was not found")

    @staticmethod
    def _get_by_idempotency_key(
        connection: Connection,
        tenant_id: TenantId,
        idempotency_key: IdempotencyKey,
    ) -> RowMapping:
        row = (
            connection.execute(
                select(synthetic_events).where(
                    synthetic_events.c.tenant_id == tenant_id.value,
                    synthetic_events.c.idempotency_key == idempotency_key.value,
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConflictError("concurrent idempotency conflict could not be resolved")
        return row

    @staticmethod
    def _to_domain(row: RowMapping) -> SyntheticEvent:
        raw_payload = row["payload"]
        return SyntheticEvent(
            id=row["id"],
            tenant_id=TenantId(row["tenant_id"]),
            idempotency_key=IdempotencyKey(row["idempotency_key"]),
            payload=SyntheticPayload(
                kind=raw_payload["kind"],
                attributes=raw_payload.get("attributes", {}),
            ),
            status=SyntheticEventStatus(row["status"]),
            correlation_id=row["correlation_id"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
            version=row["version"],
        )
