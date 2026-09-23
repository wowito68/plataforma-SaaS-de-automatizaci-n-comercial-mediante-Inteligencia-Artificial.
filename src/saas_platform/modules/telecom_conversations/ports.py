from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Self
from uuid import UUID

from saas_platform.modules.lead_qualification.domain import (
    CommercialSignal,
    LeadProfile,
    LeadState,
    Opportunity,
    ProfileField,
    QualificationRequest,
    QualificationResult,
)
from saas_platform.modules.telecom_conversations.domain import (
    Contact,
    Conversation,
    ConversationChannel,
    ConversationState,
    HandoffRequest,
    HandoffStatus,
    HandoffType,
    Lead,
    MessageProcessing,
    MessageProcessingStatus,
    NormalizedConversationMessage,
    ProcessingClaim,
    StoredMessage,
)
from saas_platform.modules.telecom_conversations.profile import ProfileApplicationPlan
from saas_platform.modules.telecom_extraction.domain import (
    ConversationMessage,
    TelecomExtractionCommand,
    TelecomExtractionResult,
)
from saas_platform.modules.tenancy.domain import TenantId


@dataclass(frozen=True, slots=True)
class ResolvedContact:
    value: Contact
    created: bool


@dataclass(frozen=True, slots=True)
class ResolvedConversation:
    conversation: Conversation
    lead_id: UUID
    conversation_created: bool
    lead_created: bool


@dataclass(frozen=True, slots=True)
class InsertedMessage:
    value: StoredMessage
    created: bool
    payload_matches: bool


@dataclass(frozen=True, slots=True)
class ProfilePersistenceResult:
    changed_fields: tuple[ProfileField, ...]
    conflict_count: int


@dataclass(frozen=True, slots=True)
class CreatedHandoff:
    value: HandoffRequest
    created: bool


@dataclass(frozen=True, slots=True)
class CreatedQuestion:
    id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class StoredExtraction:
    id: UUID
    idempotency_key: str
    result_hash: str
    applied: bool


@dataclass(frozen=True, slots=True)
class AuditEventData:
    action: str
    category: str
    target_type: str
    target_id: UUID
    actor_type: str
    actor_id: str | None
    correlation_id: UUID
    causation_id: UUID
    metadata: Mapping[str, object]
    schema_version: str = "audit-event.v1"


@dataclass(frozen=True, slots=True)
class OutboxEventData:
    event_key: str
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: Mapping[str, object]
    correlation_id: UUID
    causation_id: UUID
    payload_version: int = 1


@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    tenant_id: TenantId
    id: UUID
    event_key: str
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    payload: Mapping[str, object]
    payload_hash: str
    payload_version: int
    correlation_id: UUID
    causation_id: UUID
    lease_token: UUID
    attempts: int


@dataclass(frozen=True, slots=True)
class ProcessingContext:
    contact: Contact
    conversation: Conversation
    lead_id: UUID
    lead_version: int
    lead_state: LeadState
    profile: LeadProfile
    message: StoredMessage
    processing: MessageProcessing
    messages: tuple[ConversationMessage, ...]
    recently_asked_fields: tuple[ProfileField, ...]
    active_handoff: HandoffRequest | None
    eligibility_value: str


class TenantRepository(Protocol):
    def require_active(self) -> None: ...


class ContactRepository(Protocol):
    def resolve(
        self,
        command: NormalizedConversationMessage,
        *,
        contact_id: UUID,
        now: datetime,
    ) -> ResolvedContact: ...

    def get(self, contact_id: UUID, *, lock: bool = False) -> Contact: ...

    def register_do_not_contact(
        self,
        contact: Contact,
        *,
        now: datetime,
    ) -> Contact: ...


class LeadRepository(Protocol):
    def get(self, lead_id: UUID, *, lock: bool = False) -> Lead: ...

    def update_after_evaluation(
        self,
        *,
        lead_id: UUID,
        expected_version: int,
        opportunity: Opportunity,
        state: LeadState,
        evaluation: QualificationResult,
        now: datetime,
    ) -> Lead: ...


class ConversationRepository(Protocol):
    def resolve_with_lead(
        self,
        contact: Contact,
        *,
        channel: ConversationChannel,
        external_id: str,
        conversation_id: UUID,
        lead_id: UUID,
        now: datetime,
    ) -> ResolvedConversation: ...

    def get(self, conversation_id: UUID, *, lock: bool = False) -> Conversation: ...

    def touch_with_message(
        self,
        conversation: Conversation,
        message: StoredMessage,
        *,
        now: datetime,
    ) -> Conversation: ...

    def update_after_processing(
        self,
        conversation: Conversation,
        *,
        state: ConversationState,
        automation_active: bool,
        commercial_summary: str | None,
        now: datetime,
    ) -> Conversation: ...


class MessageRepository(Protocol):
    def find_by_idempotency_key(self, key: str) -> StoredMessage | None: ...

    def insert(
        self,
        command: NormalizedConversationMessage,
        *,
        message_id: UUID,
        conversation_id: UUID,
        now: datetime,
    ) -> InsertedMessage: ...

    def get(self, message_id: UUID, *, lock: bool = False) -> StoredMessage: ...

    def list_context(
        self, conversation_id: UUID, *, limit: int
    ) -> tuple[ConversationMessage, ...]: ...


class ProcessingRepository(Protocol):
    def create(
        self,
        *,
        processing_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        now: datetime,
    ) -> MessageProcessing: ...

    def get_for_claim(self, claim: ProcessingClaim, *, lock: bool = False) -> MessageProcessing: ...

    def get_by_message(self, message_id: UUID) -> MessageProcessing: ...

    def complete(
        self,
        claim: ProcessingClaim,
        *,
        status: MessageProcessingStatus,
        extraction_execution_id: UUID | None,
        evaluation_id: UUID | None,
        now: datetime,
    ) -> None: ...

    def fail(
        self,
        claim: ProcessingClaim,
        *,
        status: MessageProcessingStatus,
        error_code: str,
        next_attempt_at: datetime,
        now: datetime,
    ) -> None: ...


class ProfileRepository(Protocol):
    def load(self, *, lead_id: UUID, conversation_id: UUID) -> LeadProfile: ...

    def apply(
        self,
        *,
        lead_id: UUID,
        execution_id: UUID,
        plan: ProfileApplicationPlan,
        now: datetime,
    ) -> ProfilePersistenceResult: ...

    def persist_signals(
        self,
        *,
        lead_id: UUID,
        execution_id: UUID,
        signals: tuple[CommercialSignal, ...],
        now: datetime,
    ) -> None: ...

    def load_active_signals(self, lead_id: UUID) -> tuple[CommercialSignal, ...]: ...


class ExtractionExecutionRepository(Protocol):
    def find_by_key(
        self, idempotency_key: str, *, lock: bool = False
    ) -> StoredExtraction | None: ...

    def add(
        self,
        result: TelecomExtractionResult,
        *,
        durable_idempotency_key: str,
        input_hash: str,
        result_hash: str,
        lead_id: UUID,
        message_id: UUID,
        processing_id: UUID,
        processing_version: int,
        correlation_id: UUID,
        now: datetime,
    ) -> StoredExtraction: ...


class QualificationEvaluationRepository(Protocol):
    def find_id_by_extraction(self, extraction_execution_id: UUID) -> UUID | None: ...

    def add(
        self,
        result: QualificationResult,
        *,
        extraction_execution_id: UUID,
        message_id: UUID,
        processing_version: int,
        causation_id: UUID,
    ) -> None: ...


class HandoffRepository(Protocol):
    def active(self, conversation_id: UUID, *, lock: bool = False) -> HandoffRequest | None: ...

    def create(
        self,
        *,
        handoff_id: UUID,
        conversation_id: UUID,
        lead_id: UUID,
        kind: HandoffType,
        priority: int,
        reason: str,
        score: int,
        classification: str,
        summary: str,
        missing_information: tuple[ProfileField, ...],
        source: str,
        extraction_execution_id: UUID,
        now: datetime,
    ) -> CreatedHandoff: ...

    def cancel_active(
        self,
        conversation_id: UUID,
        *,
        reason: str,
        now: datetime,
    ) -> int: ...

    def transition(
        self,
        handoff: HandoffRequest,
        target: HandoffStatus,
        *,
        assigned_to: str | None,
        now: datetime,
    ) -> HandoffRequest: ...


class PendingQuestionRepository(Protocol):
    def recently_asked(self, conversation_id: UUID, *, limit: int) -> tuple[ProfileField, ...]: ...

    def cancel_pending(
        self,
        conversation_id: UUID,
        *,
        reason: str,
        now: datetime,
    ) -> int: ...

    def create(
        self,
        *,
        question_id: UUID,
        conversation_id: UUID,
        lead_id: UUID,
        target_field: ProfileField,
        question: str,
        reason: str,
        priority: int,
        extraction_execution_id: UUID,
        now: datetime,
    ) -> CreatedQuestion: ...


class ContactPreferenceRepository(Protocol):
    def register_do_not_contact(
        self,
        *,
        preference_id: UUID,
        contact_id: UUID,
        conversation_id: UUID,
        lead_id: UUID,
        source_message_id: UUID,
        extraction_execution_id: UUID,
        evidence_hash: str,
        correlation_id: UUID,
        now: datetime,
    ) -> bool: ...


class AuditRepository(Protocol):
    def append(self, event: AuditEventData, *, event_id: UUID, now: datetime) -> None: ...


class OutboxRepository(Protocol):
    def add(self, event: OutboxEventData, *, event_id: UUID, now: datetime) -> bool: ...


class ConversationUnitOfWork(Protocol):
    tenant_id: TenantId
    tenants: TenantRepository
    contacts: ContactRepository
    leads: LeadRepository
    conversations: ConversationRepository
    messages: MessageRepository
    processing: ProcessingRepository
    profiles: ProfileRepository
    extractions: ExtractionExecutionRepository
    evaluations: QualificationEvaluationRepository
    handoffs: HandoffRepository
    questions: PendingQuestionRepository
    preferences: ContactPreferenceRepository
    audit: AuditRepository
    outbox: OutboxRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ConversationUnitOfWorkFactory(Protocol):
    def create(self, tenant_id: TenantId, *, worker: bool = False) -> ConversationUnitOfWork: ...


class MessageJobClaimer(Protocol):
    def claim_next(self) -> ProcessingClaim | None: ...


class TelecomExtractionUseCase(Protocol):
    def execute(self, command: TelecomExtractionCommand) -> TelecomExtractionResult: ...


class QualificationEvaluator(Protocol):
    def execute(self, request: QualificationRequest) -> QualificationResult: ...


class OutboxEventSink(Protocol):
    def publish(self, event: ClaimedOutboxEvent) -> None: ...
