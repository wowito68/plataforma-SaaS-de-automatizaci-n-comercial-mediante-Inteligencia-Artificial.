from dataclasses import dataclass

from saas_platform.config import Settings
from saas_platform.foundation import SystemClock, Uuid7Generator
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.postgres_queue import (
    PostgresOutboxDispatcher,
    PostgresQueue,
    SyntheticEventWorker,
)
from saas_platform.modules.lead_qualification.application import EvaluateLeadQualification
from saas_platform.modules.lead_qualification.scoring import LeadScoringEngine
from saas_platform.modules.synthetic_events.application import (
    AcceptSyntheticEvent,
    GetSyntheticEvent,
    ProcessSyntheticEvent,
)
from saas_platform.modules.synthetic_events.postgres import PostgresSyntheticEventStore
from saas_platform.modules.telecom_conversations.application import (
    GetMessageProcessing,
    IngestConversationMessage,
    ProcessConversationMessage,
)
from saas_platform.modules.telecom_conversations.policy import (
    CodeDefaultScoringPolicyProvider,
)
from saas_platform.modules.telecom_conversations.postgres import (
    PostgresConversationUnitOfWorkFactory,
)
from saas_platform.modules.telecom_conversations.postgres_jobs import (
    LoggingOutboxEventSink,
    PostgresConversationOutboxPublisher,
    PostgresMessageJobClaimer,
)
from saas_platform.modules.telecom_conversations.worker import ConversationWorker
from saas_platform.modules.telecom_extraction.composition import (
    build_openai_extraction_use_case,
)


@dataclass(slots=True)
class Container:
    settings: Settings
    database: Database
    accept_synthetic_event: AcceptSyntheticEvent
    get_synthetic_event: GetSyntheticEvent
    worker: SyntheticEventWorker
    ingest_conversation_message: IngestConversationMessage
    get_message_processing: GetMessageProcessing
    process_conversation_message: ProcessConversationMessage | None
    conversation_worker: ConversationWorker

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
        conversation_uow = PostgresConversationUnitOfWorkFactory(database, ids)
        conversation_processor: ProcessConversationMessage | None = None
        if settings.ai_extraction_enabled:
            conversation_processor = ProcessConversationMessage(
                conversation_uow,
                build_openai_extraction_use_case(
                    settings,
                    id_generator=ids,
                    clock=clock,
                ),
                EvaluateLeadQualification(
                    CodeDefaultScoringPolicyProvider(),
                    LeadScoringEngine(),
                    ids,
                    clock,
                ),
                ids,
                clock,
                max_attempts=settings.conversation_processing_max_attempts,
            )
        conversation_outbox = PostgresConversationOutboxPublisher(
            database,
            ids,
            clock,
            LoggingOutboxEventSink(),
            lease_seconds=settings.conversation_outbox_lease_seconds,
            max_attempts=settings.conversation_outbox_max_attempts,
        )
        conversation_worker = ConversationWorker(
            PostgresMessageJobClaimer(
                database,
                ids,
                clock,
                lease_seconds=settings.conversation_processing_lease_seconds,
            ),
            conversation_processor,
            conversation_outbox,
            message_batch_size=settings.conversation_worker_batch_size,
            outbox_batch_size=settings.conversation_outbox_batch_size,
        )
        return cls(
            settings=settings,
            database=database,
            accept_synthetic_event=AcceptSyntheticEvent(event_store),
            get_synthetic_event=GetSyntheticEvent(event_store),
            worker=worker,
            ingest_conversation_message=IngestConversationMessage(conversation_uow, ids, clock),
            get_message_processing=GetMessageProcessing(conversation_uow),
            process_conversation_message=conversation_processor,
            conversation_worker=conversation_worker,
        )

    def close(self) -> None:
        self.database.dispose()
