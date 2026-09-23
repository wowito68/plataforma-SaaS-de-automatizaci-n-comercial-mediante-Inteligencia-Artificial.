import logging
from dataclasses import dataclass
from time import perf_counter

from saas_platform.modules.telecom_conversations.application import (
    ProcessConversationMessage,
)
from saas_platform.modules.telecom_conversations.domain import ProcessingOutcome
from saas_platform.modules.telecom_conversations.ports import MessageJobClaimer
from saas_platform.modules.telecom_conversations.postgres_jobs import (
    PostgresConversationOutboxPublisher,
)
from saas_platform.observability import (
    CONVERSATION_CONCURRENCY_CONFLICTS,
    CONVERSATION_MESSAGES_PROCESSED,
    CONVERSATION_PROCESSING_DURATION,
    CONVERSATION_RETRIES,
    bind_context,
    reset_context,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConversationWorkerCycleResult:
    claimed: int
    completed: int
    failed: int
    blocked: int
    conflicts: int
    outbox_published: int
    outbox_retried: int
    outbox_dead: int


class ConversationWorker:
    """Run bounded PostgreSQL-backed conversation and outbox work."""

    def __init__(
        self,
        claimer: MessageJobClaimer,
        processor: ProcessConversationMessage | None,
        outbox: PostgresConversationOutboxPublisher,
        *,
        message_batch_size: int,
        outbox_batch_size: int,
    ) -> None:
        self._claimer = claimer
        self._processor = processor
        self._outbox = outbox
        self._message_batch_size = message_batch_size
        self._outbox_batch_size = outbox_batch_size

    def run_cycle(self) -> ConversationWorkerCycleResult:
        claimed = 0
        completed = 0
        failed = 0
        blocked = 0
        conflicts = 0

        if self._processor is not None:
            for _ in range(self._message_batch_size):
                claim = self._claimer.claim_next()
                if claim is None:
                    break
                claimed += 1
                started = perf_counter()
                bound = bind_context(
                    tenant_id=claim.tenant_id.value,
                    correlation_id=claim.correlation_id,
                )
                try:
                    result = self._processor.execute(claim)
                    outcome = result.outcome
                    CONVERSATION_MESSAGES_PROCESSED.labels(outcome=outcome.value).inc()
                    CONVERSATION_PROCESSING_DURATION.labels(outcome=outcome.value).observe(
                        perf_counter() - started
                    )
                    if outcome in {
                        ProcessingOutcome.COMPLETED,
                        ProcessingOutcome.COMPLETED_DEGRADED,
                        ProcessingOutcome.DUPLICATE,
                    }:
                        completed += 1
                    elif outcome in {
                        ProcessingOutcome.BLOCKED_DO_NOT_CONTACT,
                        ProcessingOutcome.BLOCKED_HANDOFF,
                    }:
                        blocked += 1
                    elif outcome is ProcessingOutcome.CONCURRENCY_CONFLICT:
                        conflicts += 1
                        CONVERSATION_CONCURRENCY_CONFLICTS.labels(
                            operation="message_processing"
                        ).inc()
                    else:
                        failed += 1
                    if outcome is ProcessingOutcome.RETRYABLE_FAILURE:
                        CONVERSATION_RETRIES.labels(operation="message_processing").inc()
                except Exception as error:
                    failed += 1
                    CONVERSATION_MESSAGES_PROCESSED.labels(outcome="unexpected_failure").inc()
                    CONVERSATION_PROCESSING_DURATION.labels(
                        outcome="unexpected_failure"
                    ).observe(perf_counter() - started)
                    logger.exception(
                        "unexpected conversation worker failure",
                        extra={
                            "operation": "conversation_worker.process",
                            "message_id": str(claim.message_id),
                            "message_processing_id": str(claim.processing_id),
                            "error_type": type(error).__name__,
                            "outcome": "unexpected_failure",
                            "result": "lease_recovery_pending",
                        },
                    )
                finally:
                    reset_context(bound)

        published = self._outbox.publish_batch(limit=self._outbox_batch_size)
        return ConversationWorkerCycleResult(
            claimed=claimed,
            completed=completed,
            failed=failed,
            blocked=blocked,
            conflicts=conflicts,
            outbox_published=published.published,
            outbox_retried=published.retried,
            outbox_dead=published.dead,
        )
