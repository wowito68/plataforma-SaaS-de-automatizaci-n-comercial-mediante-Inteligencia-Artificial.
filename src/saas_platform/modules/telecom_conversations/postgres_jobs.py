import logging
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from sqlalchemy import and_, func, insert, or_, select, update

from saas_platform.errors import PermanentError
from saas_platform.foundation import Clock, IdGenerator
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.schema import (
    audit_events,
    conversation_messages,
    conversation_outbox_events,
    conversations,
    message_processing,
)
from saas_platform.modules.telecom_conversations.domain import (
    MessageDirection,
    MessageProcessingStatus,
    ProcessingClaim,
)
from saas_platform.modules.telecom_conversations.errors import (
    ConcurrencyConflictError,
    OutboxPublicationError,
)
from saas_platform.modules.telecom_conversations.ports import (
    ClaimedOutboxEvent,
    OutboxEventSink,
)
from saas_platform.modules.tenancy.domain import TenantId

logger = logging.getLogger(__name__)


class PostgresMessageJobClaimer:
    def __init__(
        self,
        database: Database,
        ids: IdGenerator,
        clock: Clock,
        *,
        lease_seconds: int,
    ) -> None:
        self._database = database
        self._ids = ids
        self._clock = clock
        self._lease_seconds = lease_seconds

    def claim_next(self) -> ProcessingClaim | None:
        now = self._clock.now()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        lease_token = self._ids.new()
        with self._database.worker_transaction() as connection:
            candidate = (
                connection.execute(
                    select(
                        message_processing.c.tenant_id,
                        message_processing.c.id.label("processing_id"),
                        message_processing.c.conversation_id,
                        message_processing.c.message_id,
                        message_processing.c.processing_version,
                        message_processing.c.attempts,
                        conversation_messages.c.correlation_id,
                    )
                    .select_from(
                        message_processing.join(
                            conversation_messages,
                            and_(
                                conversation_messages.c.tenant_id == message_processing.c.tenant_id,
                                conversation_messages.c.id == message_processing.c.message_id,
                            ),
                        ).join(
                            conversations,
                            and_(
                                conversations.c.tenant_id == message_processing.c.tenant_id,
                                conversations.c.id == message_processing.c.conversation_id,
                            ),
                        )
                    )
                    .where(
                        message_processing.c.next_attempt_at <= now,
                        conversation_messages.c.direction == MessageDirection.INBOUND.value,
                        or_(
                            message_processing.c.status.in_(
                                (
                                    MessageProcessingStatus.PENDING.value,
                                    MessageProcessingStatus.RETRYABLE_FAILURE.value,
                                )
                            ),
                            and_(
                                message_processing.c.status
                                == MessageProcessingStatus.PROCESSING.value,
                                message_processing.c.lease_until <= now,
                            ),
                        ),
                        or_(
                            conversations.c.processing_lease_until.is_(None),
                            conversations.c.processing_lease_until <= now,
                        ),
                    )
                    .order_by(
                        conversation_messages.c.external_timestamp,
                        conversation_messages.c.received_at,
                        message_processing.c.id,
                    )
                    .with_for_update(of=conversations, skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if candidate is None:
                return None

            conversation_result = connection.execute(
                update(conversations)
                .where(
                    conversations.c.tenant_id == candidate["tenant_id"],
                    conversations.c.id == candidate["conversation_id"],
                    or_(
                        conversations.c.processing_lease_until.is_(None),
                        conversations.c.processing_lease_until <= now,
                    ),
                )
                .values(
                    processing_message_id=candidate["message_id"],
                    processing_lease_token=lease_token,
                    processing_lease_until=lease_until,
                    version=conversations.c.version + 1,
                )
            )
            if conversation_result.rowcount != 1:
                raise ConcurrencyConflictError("conversation could not be leased")

            processing_row = connection.execute(
                update(message_processing)
                .where(
                    message_processing.c.tenant_id == candidate["tenant_id"],
                    message_processing.c.id == candidate["processing_id"],
                )
                .values(
                    status=MessageProcessingStatus.PROCESSING.value,
                    attempts=message_processing.c.attempts + 1,
                    lease_token=lease_token,
                    lease_until=lease_until,
                    started_at=func.coalesce(message_processing.c.started_at, now),
                    last_error_code=None,
                )
                .returning(message_processing.c.attempts)
            ).scalar_one()
            connection.execute(
                update(conversation_messages)
                .where(
                    conversation_messages.c.tenant_id == candidate["tenant_id"],
                    conversation_messages.c.id == candidate["message_id"],
                )
                .values(
                    processing_status=MessageProcessingStatus.PROCESSING.value,
                    version=conversation_messages.c.version + 1,
                )
            )
            connection.execute(
                insert(audit_events).values(
                    tenant_id=candidate["tenant_id"],
                    id=self._ids.new(),
                    action="conversation_message.processing_started",
                    target_type="message_processing",
                    target_id=candidate["processing_id"],
                    actor_type="worker",
                    actor_id="telecom-conversation-worker.v1",
                    category="conversation_processing",
                    correlation_id=candidate["correlation_id"],
                    causation_id=candidate["message_id"],
                    schema_version="audit-event.v1",
                    metadata={"processing_version": candidate["processing_version"]},
                    created_at=now,
                )
            )
        return ProcessingClaim(
            tenant_id=TenantId(candidate["tenant_id"]),
            processing_id=candidate["processing_id"],
            conversation_id=candidate["conversation_id"],
            message_id=candidate["message_id"],
            processing_version=candidate["processing_version"],
            lease_token=lease_token,
            correlation_id=candidate["correlation_id"],
            attempts=processing_row,
        )


class LoggingOutboxEventSink:
    def publish(self, event: ClaimedOutboxEvent) -> None:
        logger.info(
            "conversation outbox event published",
            extra={
                "operation": "conversation_outbox.publish",
                "event_id": str(event.id),
                "outcome": event.event_type,
                "result": "published",
            },
        )


@dataclass(frozen=True, slots=True)
class OutboxPublishResult:
    published: int
    retried: int
    dead: int


class PostgresConversationOutboxPublisher:
    def __init__(
        self,
        database: Database,
        ids: IdGenerator,
        clock: Clock,
        sink: OutboxEventSink,
        *,
        lease_seconds: int,
        max_attempts: int,
    ) -> None:
        self._database = database
        self._ids = ids
        self._clock = clock
        self._sink = sink
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    def publish_batch(self, *, limit: int = 50) -> OutboxPublishResult:
        claimed = self._claim(limit=limit)
        published = 0
        retried = 0
        dead = 0
        for event in claimed:
            try:
                self._sink.publish(event)
                self._complete(event)
                published += 1
            except Exception as error:
                is_dead = isinstance(error, PermanentError) or event.attempts >= self._max_attempts
                self._fail(event, error, dead=is_dead)
                if is_dead:
                    dead += 1
                else:
                    retried += 1
        return OutboxPublishResult(published=published, retried=retried, dead=dead)

    def _claim(self, *, limit: int) -> tuple[ClaimedOutboxEvent, ...]:
        now = self._clock.now()
        lease_until = now + timedelta(seconds=self._lease_seconds)
        claimed: list[ClaimedOutboxEvent] = []
        with self._database.worker_transaction() as connection:
            rows = (
                connection.execute(
                    select(conversation_outbox_events)
                    .where(
                        conversation_outbox_events.c.next_attempt_at <= now,
                        or_(
                            conversation_outbox_events.c.status.in_(("pending", "retry")),
                            and_(
                                conversation_outbox_events.c.status == "publishing",
                                conversation_outbox_events.c.lease_until <= now,
                            ),
                        ),
                    )
                    .order_by(
                        conversation_outbox_events.c.next_attempt_at,
                        conversation_outbox_events.c.id,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            for row in rows:
                lease_token = self._ids.new()
                attempts = row["attempts"] + 1
                connection.execute(
                    update(conversation_outbox_events)
                    .where(
                        conversation_outbox_events.c.tenant_id == row["tenant_id"],
                        conversation_outbox_events.c.id == row["id"],
                    )
                    .values(
                        status="publishing",
                        attempts=attempts,
                        lease_token=lease_token,
                        lease_until=lease_until,
                        last_error=None,
                    )
                )
                claimed.append(
                    ClaimedOutboxEvent(
                        tenant_id=TenantId(row["tenant_id"]),
                        id=row["id"],
                        event_key=row["event_key"],
                        event_type=row["event_type"],
                        aggregate_type=row["aggregate_type"],
                        aggregate_id=row["aggregate_id"],
                        payload=MappingProxyType(dict(row["payload"])),
                        payload_hash=row["payload_hash"],
                        payload_version=row["payload_version"],
                        correlation_id=row["correlation_id"],
                        causation_id=row["causation_id"],
                        lease_token=lease_token,
                        attempts=attempts,
                    )
                )
        return tuple(claimed)

    def _complete(self, event: ClaimedOutboxEvent) -> None:
        now = self._clock.now()
        with self._database.worker_transaction() as connection:
            result = connection.execute(
                update(conversation_outbox_events)
                .where(
                    conversation_outbox_events.c.tenant_id == event.tenant_id.value,
                    conversation_outbox_events.c.id == event.id,
                    conversation_outbox_events.c.status == "publishing",
                    conversation_outbox_events.c.lease_token == event.lease_token,
                )
                .values(
                    status="published",
                    lease_token=None,
                    lease_until=None,
                    published_at=now,
                )
            )
            if result.rowcount != 1:
                raise OutboxPublicationError("outbox publication lease was lost")
            connection.execute(
                insert(audit_events).values(
                    tenant_id=event.tenant_id.value,
                    id=self._ids.new(),
                    action="conversation_outbox.published",
                    target_type="outbox_event",
                    target_id=event.id,
                    actor_type="worker",
                    actor_id="conversation-outbox-publisher.v1",
                    category="outbox",
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                    schema_version="audit-event.v1",
                    metadata={"event_type": event.event_type, "attempts": event.attempts},
                    created_at=now,
                )
            )

    def _fail(self, event: ClaimedOutboxEvent, error: Exception, *, dead: bool) -> None:
        now = self._clock.now()
        delay = min(300, 2**event.attempts)
        with self._database.worker_transaction() as connection:
            result = connection.execute(
                update(conversation_outbox_events)
                .where(
                    conversation_outbox_events.c.tenant_id == event.tenant_id.value,
                    conversation_outbox_events.c.id == event.id,
                    conversation_outbox_events.c.status == "publishing",
                    conversation_outbox_events.c.lease_token == event.lease_token,
                )
                .values(
                    status="dead" if dead else "retry",
                    next_attempt_at=now + timedelta(seconds=delay),
                    lease_token=None,
                    lease_until=None,
                    last_error=type(error).__name__[:300],
                )
            )
            if result.rowcount != 1:
                raise OutboxPublicationError("outbox failure lease was lost")
            connection.execute(
                insert(audit_events).values(
                    tenant_id=event.tenant_id.value,
                    id=self._ids.new(),
                    action="conversation_outbox.failed",
                    target_type="outbox_event",
                    target_id=event.id,
                    actor_type="worker",
                    actor_id="conversation-outbox-publisher.v1",
                    category="outbox",
                    correlation_id=event.correlation_id,
                    causation_id=event.causation_id,
                    schema_version="audit-event.v1",
                    metadata={
                        "event_type": event.event_type,
                        "attempts": event.attempts,
                        "error_type": type(error).__name__,
                        "terminal": dead,
                    },
                    created_at=now,
                )
            )
