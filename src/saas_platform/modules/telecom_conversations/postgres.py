import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from saas_platform.errors import ConflictError, NotFoundError
from saas_platform.foundation import IdGenerator
from saas_platform.infrastructure.database import Database
from saas_platform.infrastructure.schema import (
    audit_events,
    contact_preferences,
    contacts,
    conversation_messages,
    conversation_outbox_events,
    conversations,
    extraction_executions,
    handoff_requests,
    lead_commercial_signals,
    lead_profile_conflicts,
    lead_profile_values,
    leads,
    message_processing,
    pending_questions,
    qualification_evaluations,
    tenants,
)
from saas_platform.modules.lead_qualification.domain import (
    CommercialSignal,
    EligibilityStatus,
    ExtractionMethod,
    FactConflict,
    FactSource,
    LeadClassification,
    LeadProfile,
    LeadState,
    Opportunity,
    OpportunityType,
    ProfileFact,
    ProfileFactValue,
    ProfileField,
    QualificationResult,
    SignalCode,
    ValidationStatus,
)
from saas_platform.modules.telecom_conversations.domain import (
    Contact,
    ContactKind,
    ContactPreference,
    Conversation,
    ConversationChannel,
    ConversationState,
    HandoffRequest,
    HandoffStatus,
    HandoffType,
    Lead,
    MessageAuthor,
    MessageContentType,
    MessageDirection,
    MessageProcessing,
    MessageProcessingStatus,
    NormalizedConversationMessage,
    ProcessingClaim,
    StoredMessage,
)
from saas_platform.modules.telecom_conversations.errors import (
    ConcurrencyConflictError,
    ConversationPersistenceError,
    IdempotencyConflictError,
    InvalidTenantError,
)
from saas_platform.modules.telecom_conversations.ports import (
    AuditEventData,
    CreatedHandoff,
    CreatedQuestion,
    InsertedMessage,
    OutboxEventData,
    ProfilePersistenceResult,
    ResolvedContact,
    ResolvedConversation,
    StoredExtraction,
)
from saas_platform.modules.telecom_conversations.profile import (
    ProfileApplicationPlan,
    ProfileValueAction,
)
from saas_platform.modules.telecom_extraction.domain import (
    ConversationMessage,
    ConversationRole,
    ExtractionOutcome,
    TelecomExtractionResult,
)
from saas_platform.modules.tenancy.domain import TenantId


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _profile_value(value: ProfileFactValue) -> tuple[object, str]:
    if isinstance(value, bool):
        return value, "boolean"
    if isinstance(value, int):
        return value, "integer"
    if isinstance(value, Decimal):
        return str(value), "decimal"
    return value, "string"


def _decoded_profile_value(value: object, value_type: str) -> ProfileFactValue:
    if value_type == "boolean":
        return bool(value)
    if value_type == "integer":
        return int(str(value))
    if value_type == "decimal":
        return Decimal(str(value))
    return str(value)


class PostgresTenantRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def require_active(self) -> None:
        found = self._connection.execute(
            select(tenants.c.id).where(
                tenants.c.id == self._tenant_id.value,
                tenants.c.status == "active",
            )
        ).scalar_one_or_none()
        if found is None:
            raise InvalidTenantError("active tenant was not found")


class PostgresContactRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def resolve(
        self,
        command: NormalizedConversationMessage,
        *,
        contact_id: UUID,
        now: datetime,
    ) -> ResolvedContact:
        row = (
            self._connection.execute(
                postgres_insert(contacts)
                .values(
                    tenant_id=self._tenant_id.value,
                    id=contact_id,
                    channel=command.channel.value,
                    external_id=command.external_contact_id,
                    normalized_phone=command.normalized_phone,
                    name=command.contact_name,
                    kind=command.contact_kind.value,
                    preference=ContactPreference.UNSPECIFIED.value,
                    do_not_contact=False,
                    dnc_recorded_at=None,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        contacts.c.tenant_id,
                        contacts.c.channel,
                        contacts.c.external_id,
                    ]
                )
                .returning(*contacts.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return ResolvedContact(self._to_domain(row), created=True)

        existing = self._select_external(command.channel, command.external_contact_id, lock=True)
        if existing is None:
            raise ConflictError("concurrent contact resolution could not be completed")
        updates: dict[str, object] = {}
        if existing["normalized_phone"] is None and command.normalized_phone is not None:
            updates["normalized_phone"] = command.normalized_phone
        if existing["name"] is None and command.contact_name is not None:
            updates["name"] = command.contact_name
        if (
            existing["kind"] == ContactKind.INDIVIDUAL.value
            and command.contact_kind is ContactKind.BUSINESS
        ):
            updates["kind"] = command.contact_kind.value
        if updates:
            updates.update(updated_at=now, version=contacts.c.version + 1)
            existing = (
                self._connection.execute(
                    update(contacts)
                    .where(
                        contacts.c.tenant_id == self._tenant_id.value,
                        contacts.c.id == existing["id"],
                        contacts.c.version == existing["version"],
                    )
                    .values(**updates)
                    .returning(*contacts.c)
                )
                .mappings()
                .one_or_none()
            )
            if existing is None:
                raise ConcurrencyConflictError("contact changed during resolution")
        return ResolvedContact(self._to_domain(existing), created=False)

    def get(self, contact_id: UUID, *, lock: bool = False) -> Contact:
        statement = select(contacts).where(
            contacts.c.tenant_id == self._tenant_id.value,
            contacts.c.id == contact_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise NotFoundError("contact was not found")
        return self._to_domain(row)

    def register_do_not_contact(self, contact: Contact, *, now: datetime) -> Contact:
        if contact.do_not_contact:
            return contact
        row = (
            self._connection.execute(
                update(contacts)
                .where(
                    contacts.c.tenant_id == self._tenant_id.value,
                    contacts.c.id == contact.id,
                    contacts.c.version == contact.version,
                )
                .values(
                    preference=ContactPreference.DO_NOT_CONTACT.value,
                    do_not_contact=True,
                    dnc_recorded_at=now,
                    updated_at=now,
                    version=contacts.c.version + 1,
                )
                .returning(*contacts.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConcurrencyConflictError("contact changed while recording do-not-contact")
        return self._to_domain(row)

    def _select_external(
        self,
        channel: ConversationChannel,
        external_id: str,
        *,
        lock: bool,
    ) -> RowMapping | None:
        statement = select(contacts).where(
            contacts.c.tenant_id == self._tenant_id.value,
            contacts.c.channel == channel.value,
            contacts.c.external_id == external_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self._connection.execute(statement).mappings().one_or_none()

    def _to_domain(self, row: RowMapping) -> Contact:
        return Contact(
            id=row["id"],
            tenant_id=self._tenant_id,
            channel=ConversationChannel(row["channel"]),
            external_id=row["external_id"],
            normalized_phone=row["normalized_phone"],
            name=row["name"],
            kind=ContactKind(row["kind"]),
            preference=ContactPreference(row["preference"]),
            do_not_contact=row["do_not_contact"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )


class PostgresLeadRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def get(self, lead_id: UUID, *, lock: bool = False) -> Lead:
        statement = select(leads).where(
            leads.c.tenant_id == self._tenant_id.value,
            leads.c.id == lead_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise NotFoundError("lead was not found")
        return self._to_domain(row)

    def update_after_evaluation(
        self,
        *,
        lead_id: UUID,
        expected_version: int,
        opportunity: Opportunity,
        state: LeadState,
        evaluation: QualificationResult,
        now: datetime,
    ) -> Lead:
        row = (
            self._connection.execute(
                update(leads)
                .where(
                    leads.c.tenant_id == self._tenant_id.value,
                    leads.c.id == lead_id,
                    leads.c.version == expected_version,
                )
                .values(
                    primary_opportunity=opportunity.primary.value,
                    secondary_opportunities=[item.value for item in opportunity.secondary],
                    state=state.value,
                    current_evaluation_id=evaluation.id,
                    current_score=evaluation.score,
                    current_classification=evaluation.classification.value,
                    current_policy_id=evaluation.policy.id,
                    current_policy_version=evaluation.policy.version,
                    current_policy_fingerprint=evaluation.policy.fingerprint,
                    updated_at=now,
                    version=leads.c.version + 1,
                )
                .returning(*leads.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConcurrencyConflictError("lead changed during qualification")
        return self._to_domain(row)

    def _to_domain(self, row: RowMapping) -> Lead:
        return Lead(
            id=row["id"],
            tenant_id=self._tenant_id,
            contact_id=row["contact_id"],
            primary_opportunity=OpportunityType(row["primary_opportunity"]),
            secondary_opportunities=tuple(
                OpportunityType(item) for item in row["secondary_opportunities"]
            ),
            state=LeadState(row["state"]),
            current_evaluation_id=row["current_evaluation_id"],
            current_score=row["current_score"],
            current_classification=(
                LeadClassification(row["current_classification"])
                if row["current_classification"]
                else None
            ),
            current_policy_id=row["current_policy_id"],
            current_policy_version=row["current_policy_version"],
            current_policy_fingerprint=row["current_policy_fingerprint"],
            eligibility=EligibilityStatus(row["eligibility"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )


class PostgresConversationRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def resolve_with_lead(
        self,
        contact: Contact,
        *,
        channel: ConversationChannel,
        external_id: str,
        conversation_id: UUID,
        lead_id: UUID,
        now: datetime,
    ) -> ResolvedConversation:
        existing = self._select_external(channel, external_id, lock=True)
        if existing is not None:
            self._require_contact(existing, contact)
            return ResolvedConversation(
                conversation=self._to_domain(existing),
                lead_id=existing["lead_id"],
                conversation_created=False,
                lead_created=False,
            )

        created = False
        try:
            with self._connection.begin_nested():
                self._connection.execute(
                    insert(leads).values(
                        tenant_id=self._tenant_id.value,
                        id=lead_id,
                        contact_id=contact.id,
                        primary_opportunity=OpportunityType.UNIDENTIFIED.value,
                        secondary_opportunities=[],
                        state=LeadState.NEW.value,
                        current_evaluation_id=None,
                        current_score=None,
                        current_classification=None,
                        current_policy_id=None,
                        current_policy_version=None,
                        current_policy_fingerprint=None,
                        eligibility=EligibilityStatus.NOT_APPLICABLE.value,
                        created_at=now,
                        updated_at=now,
                        version=1,
                    )
                )
                self._connection.execute(
                    insert(conversations).values(
                        tenant_id=self._tenant_id.value,
                        id=conversation_id,
                        contact_id=contact.id,
                        lead_id=lead_id,
                        channel=channel.value,
                        external_id=external_id,
                        state=ConversationState.OPEN.value,
                        automation_active=True,
                        human_owner_id=None,
                        last_message_id=None,
                        last_activity_at=now,
                        opened_at=now,
                        closed_at=None,
                        commercial_summary=None,
                        processing_message_id=None,
                        processing_lease_token=None,
                        processing_lease_until=None,
                        version=1,
                    )
                )
            created = True
        except IntegrityError:
            existing = self._select_external(channel, external_id, lock=True)
            if existing is None:
                raise ConflictError("concurrent conversation resolution failed") from None
            self._require_contact(existing, contact)

        row = existing or self._select_external(channel, external_id, lock=True)
        if row is None:
            raise ConflictError("conversation creation could not be read back")
        return ResolvedConversation(
            conversation=self._to_domain(row),
            lead_id=row["lead_id"],
            conversation_created=created,
            lead_created=created,
        )

    def get(self, conversation_id: UUID, *, lock: bool = False) -> Conversation:
        statement = select(conversations).where(
            conversations.c.tenant_id == self._tenant_id.value,
            conversations.c.id == conversation_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise NotFoundError("conversation was not found")
        return self._to_domain(row)

    def touch_with_message(
        self,
        conversation: Conversation,
        message: StoredMessage,
        *,
        now: datetime,
    ) -> Conversation:
        if message.external_timestamp < conversation.last_activity_at:
            return conversation
        row = (
            self._connection.execute(
                update(conversations)
                .where(
                    conversations.c.tenant_id == self._tenant_id.value,
                    conversations.c.id == conversation.id,
                    conversations.c.version == conversation.version,
                )
                .values(
                    last_message_id=message.id,
                    last_activity_at=message.external_timestamp,
                    version=conversations.c.version + 1,
                )
                .returning(*conversations.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConcurrencyConflictError("conversation changed while recording message")
        return self._to_domain(row)

    def update_after_processing(
        self,
        conversation: Conversation,
        *,
        state: ConversationState,
        automation_active: bool,
        commercial_summary: str | None,
        now: datetime,
    ) -> Conversation:
        del now
        row = (
            self._connection.execute(
                update(conversations)
                .where(
                    conversations.c.tenant_id == self._tenant_id.value,
                    conversations.c.id == conversation.id,
                    conversations.c.version == conversation.version,
                )
                .values(
                    state=state.value,
                    automation_active=automation_active,
                    commercial_summary=commercial_summary,
                    closed_at=(
                        conversation.closed_at if state is ConversationState.CLOSED else None
                    ),
                    version=conversations.c.version + 1,
                )
                .returning(*conversations.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConcurrencyConflictError("conversation changed during processing")
        return self._to_domain(row)

    def _select_external(
        self,
        channel: ConversationChannel,
        external_id: str,
        *,
        lock: bool,
    ) -> RowMapping | None:
        statement = select(conversations).where(
            conversations.c.tenant_id == self._tenant_id.value,
            conversations.c.channel == channel.value,
            conversations.c.external_id == external_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self._connection.execute(statement).mappings().one_or_none()

    @staticmethod
    def _require_contact(row: RowMapping, contact: Contact) -> None:
        if row["contact_id"] != contact.id:
            raise IdempotencyConflictError(
                "external conversation identifier belongs to a different contact"
            )

    def _to_domain(self, row: RowMapping) -> Conversation:
        return Conversation(
            id=row["id"],
            tenant_id=self._tenant_id,
            contact_id=row["contact_id"],
            lead_id=row["lead_id"],
            channel=ConversationChannel(row["channel"]),
            external_id=row["external_id"],
            state=ConversationState(row["state"]),
            automation_active=row["automation_active"],
            human_owner_id=row["human_owner_id"],
            last_message_id=row["last_message_id"],
            last_activity_at=row["last_activity_at"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            commercial_summary=row["commercial_summary"],
            processing_message_id=row["processing_message_id"],
            processing_lease_token=row["processing_lease_token"],
            processing_lease_until=row["processing_lease_until"],
            version=row["version"],
        )


class PostgresMessageRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def find_by_idempotency_key(self, key: str) -> StoredMessage | None:
        row = (
            self._connection.execute(
                select(conversation_messages).where(
                    conversation_messages.c.tenant_id == self._tenant_id.value,
                    conversation_messages.c.idempotency_key == key,
                )
            )
            .mappings()
            .one_or_none()
        )
        return self._to_domain(row) if row is not None else None

    def insert(
        self,
        command: NormalizedConversationMessage,
        *,
        message_id: UUID,
        conversation_id: UUID,
        now: datetime,
    ) -> InsertedMessage:
        fingerprint = command.payload_hash()
        row = (
            self._connection.execute(
                postgres_insert(conversation_messages)
                .values(
                    tenant_id=self._tenant_id.value,
                    id=message_id,
                    conversation_id=conversation_id,
                    channel=command.channel.value,
                    external_message_id=command.external_message_id,
                    event_type=command.event_type,
                    direction=command.direction.value,
                    author=command.author.value,
                    content=command.content,
                    content_type=command.content_type.value,
                    external_timestamp=command.external_timestamp,
                    received_at=now,
                    processing_status=MessageProcessingStatus.PENDING.value,
                    fingerprint=fingerprint,
                    idempotency_key=command.idempotency_key(),
                    metadata=dict(command.metadata),
                    correlation_id=command.correlation_id,
                    causation_id=command.correlation_id,
                    version=1,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        conversation_messages.c.tenant_id,
                        conversation_messages.c.channel,
                        conversation_messages.c.external_message_id,
                        conversation_messages.c.event_type,
                    ]
                )
                .returning(*conversation_messages.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return InsertedMessage(self._to_domain(row), created=True, payload_matches=True)
        existing = (
            self._connection.execute(
                select(conversation_messages).where(
                    conversation_messages.c.tenant_id == self._tenant_id.value,
                    conversation_messages.c.channel == command.channel.value,
                    conversation_messages.c.external_message_id == command.external_message_id,
                    conversation_messages.c.event_type == command.event_type,
                )
            )
            .mappings()
            .one()
        )
        return InsertedMessage(
            self._to_domain(existing),
            created=False,
            payload_matches=existing["fingerprint"] == fingerprint,
        )

    def get(self, message_id: UUID, *, lock: bool = False) -> StoredMessage:
        statement = select(conversation_messages).where(
            conversation_messages.c.tenant_id == self._tenant_id.value,
            conversation_messages.c.id == message_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise NotFoundError("conversation message was not found")
        return self._to_domain(row)

    def list_context(
        self,
        conversation_id: UUID,
        *,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:
        rows = (
            self._connection.execute(
                select(conversation_messages)
                .where(
                    conversation_messages.c.tenant_id == self._tenant_id.value,
                    conversation_messages.c.conversation_id == conversation_id,
                )
                .order_by(
                    conversation_messages.c.external_timestamp.desc(),
                    conversation_messages.c.received_at.desc(),
                )
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return tuple(
            ConversationMessage(
                id=row["id"],
                tenant_id=self._tenant_id,
                role=(
                    ConversationRole.CUSTOMER
                    if row["direction"] == MessageDirection.INBOUND.value
                    else ConversationRole.ADVISOR
                ),
                content=row["content"],
                sent_at=row["external_timestamp"],
            )
            for row in reversed(rows)
        )

    def _to_domain(self, row: RowMapping) -> StoredMessage:
        return StoredMessage(
            id=row["id"],
            tenant_id=self._tenant_id,
            conversation_id=row["conversation_id"],
            channel=ConversationChannel(row["channel"]),
            external_message_id=row["external_message_id"],
            event_type=row["event_type"],
            direction=MessageDirection(row["direction"]),
            author=MessageAuthor(row["author"]),
            content=row["content"],
            content_type=MessageContentType(row["content_type"]),
            external_timestamp=row["external_timestamp"],
            received_at=row["received_at"],
            processing_status=MessageProcessingStatus(row["processing_status"]),
            fingerprint=row["fingerprint"],
            idempotency_key=row["idempotency_key"],
            correlation_id=row["correlation_id"],
            version=row["version"],
        )


class PostgresProcessingRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def create(
        self,
        *,
        processing_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        now: datetime,
    ) -> MessageProcessing:
        row = (
            self._connection.execute(
                insert(message_processing)
                .values(
                    tenant_id=self._tenant_id.value,
                    id=processing_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    processing_version=1,
                    status=MessageProcessingStatus.PENDING.value,
                    attempts=0,
                    lease_token=None,
                    lease_until=None,
                    next_attempt_at=now,
                    started_at=None,
                    completed_at=None,
                    extraction_execution_id=None,
                    evaluation_id=None,
                    last_error_code=None,
                )
                .returning(*message_processing.c)
            )
            .mappings()
            .one()
        )
        return self._to_domain(row)

    def get_for_claim(
        self,
        claim: ProcessingClaim,
        *,
        lock: bool = False,
    ) -> MessageProcessing:
        statement = select(message_processing).where(
            message_processing.c.tenant_id == self._tenant_id.value,
            message_processing.c.id == claim.processing_id,
            message_processing.c.message_id == claim.message_id,
            message_processing.c.processing_version == claim.processing_version,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        if row is None:
            raise NotFoundError("message processing record was not found")
        return self._to_domain(row)

    def get_by_message(self, message_id: UUID) -> MessageProcessing:
        row = (
            self._connection.execute(
                select(message_processing)
                .where(
                    message_processing.c.tenant_id == self._tenant_id.value,
                    message_processing.c.message_id == message_id,
                )
                .order_by(message_processing.c.processing_version.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFoundError("message processing record was not found")
        return self._to_domain(row)

    def complete(
        self,
        claim: ProcessingClaim,
        *,
        status: MessageProcessingStatus,
        extraction_execution_id: UUID | None,
        evaluation_id: UUID | None,
        now: datetime,
    ) -> None:
        result = self._connection.execute(
            update(message_processing)
            .where(
                message_processing.c.tenant_id == self._tenant_id.value,
                message_processing.c.id == claim.processing_id,
                message_processing.c.status == MessageProcessingStatus.PROCESSING.value,
                message_processing.c.lease_token == claim.lease_token,
            )
            .values(
                status=status.value,
                lease_token=None,
                lease_until=None,
                completed_at=now,
                extraction_execution_id=extraction_execution_id,
                evaluation_id=evaluation_id,
                last_error_code=None,
            )
        )
        if result.rowcount != 1:
            raise ConcurrencyConflictError("processing lease is no longer owned")
        self._finish_message_and_conversation(claim, status)

    def fail(
        self,
        claim: ProcessingClaim,
        *,
        status: MessageProcessingStatus,
        error_code: str,
        next_attempt_at: datetime,
        now: datetime,
    ) -> None:
        terminal = status in {
            MessageProcessingStatus.PERMANENT_FAILURE,
            MessageProcessingStatus.INVALID_EXTRACTION,
        }
        result = self._connection.execute(
            update(message_processing)
            .where(
                message_processing.c.tenant_id == self._tenant_id.value,
                message_processing.c.id == claim.processing_id,
                message_processing.c.status == MessageProcessingStatus.PROCESSING.value,
                message_processing.c.lease_token == claim.lease_token,
            )
            .values(
                status=status.value,
                lease_token=None,
                lease_until=None,
                next_attempt_at=next_attempt_at,
                completed_at=now if terminal else None,
                last_error_code=error_code[:100],
            )
        )
        if result.rowcount != 1:
            raise ConcurrencyConflictError("processing failure could not fence stale worker")
        self._finish_message_and_conversation(claim, status)

    def _finish_message_and_conversation(
        self,
        claim: ProcessingClaim,
        status: MessageProcessingStatus,
    ) -> None:
        message_result = self._connection.execute(
            update(conversation_messages)
            .where(
                conversation_messages.c.tenant_id == self._tenant_id.value,
                conversation_messages.c.id == claim.message_id,
            )
            .values(
                processing_status=status.value,
                version=conversation_messages.c.version + 1,
            )
        )
        conversation_result = self._connection.execute(
            update(conversations)
            .where(
                conversations.c.tenant_id == self._tenant_id.value,
                conversations.c.id == claim.conversation_id,
                conversations.c.processing_message_id == claim.message_id,
                conversations.c.processing_lease_token == claim.lease_token,
            )
            .values(
                processing_message_id=None,
                processing_lease_token=None,
                processing_lease_until=None,
                version=conversations.c.version + 1,
            )
        )
        if message_result.rowcount != 1 or conversation_result.rowcount != 1:
            raise ConcurrencyConflictError("processing completion lost its conversation lease")

    def _to_domain(self, row: RowMapping) -> MessageProcessing:
        return MessageProcessing(
            id=row["id"],
            tenant_id=self._tenant_id,
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            processing_version=row["processing_version"],
            status=MessageProcessingStatus(row["status"]),
            attempts=row["attempts"],
            lease_token=row["lease_token"],
            lease_until=row["lease_until"],
            next_attempt_at=row["next_attempt_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            extraction_execution_id=row["extraction_execution_id"],
            evaluation_id=row["evaluation_id"],
            last_error_code=row["last_error_code"],
        )


INTENT_SIGNAL_CODES = frozenset(
    {
        SignalCode.GENERAL_INFORMATION,
        SignalCode.ASKS_PRICE,
        SignalCode.ASKS_MONTHLY_PAYMENT,
        SignalCode.ASKS_AVAILABILITY,
        SignalCode.COMPARES_PRODUCTS,
        SignalCode.ASKS_REQUIREMENTS,
        SignalCode.REQUESTS_QUOTE,
        SignalCode.REQUESTS_PORTABILITY,
        SignalCode.WANTS_CONTRACT_NOW,
        SignalCode.RESEARCH_ONLY,
    }
)
URGENCY_SIGNAL_CODES = frozenset(
    {
        SignalCode.RESEARCH_ONLY,
        SignalCode.PURCHASE_OVER_THREE_MONTHS,
        SignalCode.PURCHASE_OVER_SIX_MONTHS,
        SignalCode.NEXT_MONTH,
        SignalCode.THIS_WEEK,
        SignalCode.IMMEDIATE,
    }
)
BUDGET_SIGNAL_CODES = frozenset(
    {
        SignalCode.BUDGET_UNSPECIFIED,
        SignalCode.BUDGET_INCOMPATIBLE,
        SignalCode.ACCEPTS_ALTERNATIVES,
        SignalCode.BUDGET_COMPATIBLE,
        SignalCode.BUDGET_AND_PAYMENT_DEFINED,
        SignalCode.BUDGET_INCOMPATIBLE_NO_ALTERNATIVES,
    }
)
EXCLUSIVE_SIGNAL_GROUPS = (
    INTENT_SIGNAL_CODES,
    URGENCY_SIGNAL_CODES,
    BUDGET_SIGNAL_CODES,
)


class PostgresProfileRepository:
    def __init__(
        self,
        connection: Connection,
        tenant_id: TenantId,
        ids: IdGenerator,
    ) -> None:
        self._connection = connection
        self._tenant_id = tenant_id
        self._ids = ids

    def load(self, *, lead_id: UUID, conversation_id: UUID) -> LeadProfile:
        lead_row = (
            self._connection.execute(
                select(leads).where(
                    leads.c.tenant_id == self._tenant_id.value,
                    leads.c.id == lead_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if lead_row is None:
            raise NotFoundError("lead profile owner was not found")
        facts = {
            fact.field: fact
            for fact in (
                self._fact(row)
                for row in self._connection.execute(
                    select(lead_profile_values).where(
                        lead_profile_values.c.tenant_id == self._tenant_id.value,
                        lead_profile_values.c.lead_id == lead_id,
                        lead_profile_values.c.is_current.is_(True),
                    )
                )
                .mappings()
                .all()
            )
        }
        conflicts = tuple(
            self._conflict(row)
            for row in self._connection.execute(
                select(lead_profile_conflicts).where(
                    lead_profile_conflicts.c.tenant_id == self._tenant_id.value,
                    lead_profile_conflicts.c.lead_id == lead_id,
                    lead_profile_conflicts.c.status == "open",
                )
            )
            .mappings()
            .all()
        )
        missing: tuple[ProfileField, ...] = ()
        if lead_row["current_evaluation_id"] is not None:
            raw_missing = self._connection.execute(
                select(qualification_evaluations.c.missing_information).where(
                    qualification_evaluations.c.tenant_id == self._tenant_id.value,
                    qualification_evaluations.c.id == lead_row["current_evaluation_id"],
                )
            ).scalar_one_or_none()
            if raw_missing is not None:
                missing = tuple(ProfileField(item) for item in raw_missing)
        return LeadProfile(
            tenant_id=self._tenant_id,
            lead_id=lead_id,
            conversation_id=conversation_id,
            opportunity=Opportunity(
                primary=OpportunityType(lead_row["primary_opportunity"]),
                secondary=tuple(
                    OpportunityType(item) for item in lead_row["secondary_opportunities"]
                ),
            ),
            facts=facts,
            conflicts=conflicts,
            missing_information=missing,
        )

    def apply(
        self,
        *,
        lead_id: UUID,
        execution_id: UUID,
        plan: ProfileApplicationPlan,
        now: datetime,
    ) -> ProfilePersistenceResult:
        changed: list[ProfileField] = []
        conflicts_added = 0
        for decision in plan.decisions:
            if decision.action in {
                ProfileValueAction.DUPLICATE,
                ProfileValueAction.FORBIDDEN,
            }:
                continue
            current = (
                self._connection.execute(
                    select(lead_profile_values)
                    .where(
                        lead_profile_values.c.tenant_id == self._tenant_id.value,
                        lead_profile_values.c.lead_id == lead_id,
                        lead_profile_values.c.field == decision.candidate.field.value,
                        lead_profile_values.c.is_current.is_(True),
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if decision.action is ProfileValueAction.NEW_CURRENT and current is not None:
                raise ConcurrencyConflictError("profile field appeared during extraction")
            previous_id = current["id"] if current is not None else None
            support_count = 1
            if decision.makes_current and current is not None:
                support_count = (
                    current["support_count"] + 1
                    if decision.action is ProfileValueAction.REINFORCE_CURRENT
                    else 1
                )
                self._connection.execute(
                    update(lead_profile_values)
                    .where(
                        lead_profile_values.c.tenant_id == self._tenant_id.value,
                        lead_profile_values.c.id == current["id"],
                        lead_profile_values.c.version == current["version"],
                    )
                    .values(
                        is_current=False,
                        valid_to=now,
                        version=lead_profile_values.c.version + 1,
                    )
                )
            candidate_id = self._insert_fact(
                lead_id=lead_id,
                execution_id=execution_id,
                fact=decision.candidate,
                action=decision.action,
                is_current=decision.makes_current,
                support_count=support_count,
                now=now,
            )
            if decision.makes_current:
                changed.append(decision.candidate.field)
            if decision.creates_conflict:
                if previous_id is None and decision.previous is not None:
                    previous_id = self._find_or_insert_previous(
                        lead_id=lead_id,
                        execution_id=execution_id,
                        fact=decision.previous,
                        now=now,
                    )
                if previous_id is not None:
                    resolved = decision.action is ProfileValueAction.SUPERSEDE_CURRENT
                    inserted = self._connection.execute(
                        postgres_insert(lead_profile_conflicts)
                        .values(
                            tenant_id=self._tenant_id.value,
                            id=self._ids.new(),
                            lead_id=lead_id,
                            field=decision.candidate.field.value,
                            previous_value_id=previous_id,
                            new_value_id=candidate_id,
                            extraction_execution_id=execution_id,
                            status="resolved" if resolved else "open",
                            reason=decision.conflict_reason,
                            resolution=decision.conflict_reason if resolved else None,
                            detected_at=now,
                            resolved_at=now if resolved else None,
                        )
                        .on_conflict_do_nothing(constraint="uq_profile_conflicts_application")
                        .returning(lead_profile_conflicts.c.id)
                    ).scalar_one_or_none()
                    conflicts_added += int(inserted is not None)
        return ProfilePersistenceResult(
            changed_fields=tuple(dict.fromkeys(changed)),
            conflict_count=conflicts_added,
        )

    def persist_signals(
        self,
        *,
        lead_id: UUID,
        execution_id: UUID,
        signals: tuple[CommercialSignal, ...],
        now: datetime,
    ) -> None:
        for signal in signals:
            for group in EXCLUSIVE_SIGNAL_GROUPS:
                if signal.code in group:
                    self._connection.execute(
                        update(lead_commercial_signals)
                        .where(
                            lead_commercial_signals.c.tenant_id == self._tenant_id.value,
                            lead_commercial_signals.c.lead_id == lead_id,
                            lead_commercial_signals.c.code.in_(item.value for item in group),
                            lead_commercial_signals.c.is_active.is_(True),
                        )
                        .values(is_active=False)
                    )
            application_key = _canonical_hash(
                {
                    "execution_id": str(execution_id),
                    "code": signal.code.value,
                    "source_message_id": (
                        str(signal.source_message_id) if signal.source_message_id else None
                    ),
                }
            )
            self._connection.execute(
                postgres_insert(lead_commercial_signals)
                .values(
                    tenant_id=self._tenant_id.value,
                    id=self._ids.new(),
                    lead_id=lead_id,
                    code=signal.code.value,
                    source_message_id=signal.source_message_id,
                    extraction_execution_id=execution_id,
                    confidence=signal.confidence,
                    extraction_method=signal.extraction_method.value,
                    validation_status=signal.validation_status.value,
                    is_active=signal.validation_status
                    in {ValidationStatus.ACCEPTED, ValidationStatus.CONFIRMED},
                    observed_at=signal.observed_at,
                    application_key=application_key,
                    created_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_commercial_signals_application")
            )

    def load_active_signals(self, lead_id: UUID) -> tuple[CommercialSignal, ...]:
        rows = (
            self._connection.execute(
                select(lead_commercial_signals)
                .where(
                    lead_commercial_signals.c.tenant_id == self._tenant_id.value,
                    lead_commercial_signals.c.lead_id == lead_id,
                    lead_commercial_signals.c.is_active.is_(True),
                )
                .order_by(
                    lead_commercial_signals.c.code,
                    lead_commercial_signals.c.confidence.desc(),
                    lead_commercial_signals.c.observed_at.desc(),
                )
            )
            .mappings()
            .all()
        )
        by_code: dict[SignalCode, CommercialSignal] = {}
        for row in rows:
            code = SignalCode(row["code"])
            by_code.setdefault(
                code,
                CommercialSignal(
                    code=code,
                    confidence=Decimal(str(row["confidence"])),
                    source_message_id=row["source_message_id"],
                    observed_at=row["observed_at"],
                    extraction_method=ExtractionMethod(row["extraction_method"]),
                    validation_status=ValidationStatus(row["validation_status"]),
                ),
            )
        return tuple(by_code.values())

    def _insert_fact(
        self,
        *,
        lead_id: UUID,
        execution_id: UUID,
        fact: ProfileFact,
        action: ProfileValueAction,
        is_current: bool,
        support_count: int,
        now: datetime,
    ) -> UUID:
        value, value_type = _profile_value(fact.value)
        value_hash = _canonical_hash({"type": value_type, "value": value})
        application_key = _canonical_hash(
            {
                "execution_id": str(execution_id),
                "field": fact.field.value,
                "source_message_id": (
                    str(fact.source_message_id) if fact.source_message_id else None
                ),
                "value_hash": value_hash,
                "action": action.value,
            }
        )
        inserted = self._connection.execute(
            postgres_insert(lead_profile_values)
            .values(
                tenant_id=self._tenant_id.value,
                id=self._ids.new(),
                lead_id=lead_id,
                field=fact.field.value,
                value=value,
                value_type=value_type,
                value_hash=value_hash,
                application_key=application_key,
                source=fact.source.value,
                source_message_id=fact.source_message_id,
                extraction_execution_id=execution_id,
                confidence=fact.confidence,
                validation_status=fact.validation_status.value,
                observed_at=fact.observed_at,
                confirmed_by=fact.confirmed_by,
                is_current=is_current,
                valid_from=now,
                valid_to=None if is_current else now,
                support_count=support_count,
                version=1,
            )
            .on_conflict_do_nothing(constraint="uq_profile_values_application")
            .returning(lead_profile_values.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            assert isinstance(inserted, UUID)
            return inserted
        existing_id = self._connection.execute(
            select(lead_profile_values.c.id).where(
                lead_profile_values.c.tenant_id == self._tenant_id.value,
                lead_profile_values.c.application_key == application_key,
            )
        ).scalar_one()
        assert isinstance(existing_id, UUID)
        return existing_id

    def _find_or_insert_previous(
        self,
        *,
        lead_id: UUID,
        execution_id: UUID,
        fact: ProfileFact,
        now: datetime,
    ) -> UUID:
        value, value_type = _profile_value(fact.value)
        value_hash = _canonical_hash({"type": value_type, "value": value})
        existing = self._connection.execute(
            select(lead_profile_values.c.id)
            .where(
                lead_profile_values.c.tenant_id == self._tenant_id.value,
                lead_profile_values.c.lead_id == lead_id,
                lead_profile_values.c.field == fact.field.value,
                lead_profile_values.c.value_hash == value_hash,
            )
            .order_by(lead_profile_values.c.observed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            assert isinstance(existing, UUID)
            return existing
        return self._insert_fact(
            lead_id=lead_id,
            execution_id=execution_id,
            fact=fact,
            action=ProfileValueAction.HISTORICAL_ONLY,
            is_current=False,
            support_count=1,
            now=now,
        )

    def _fact(self, row: RowMapping) -> ProfileFact:
        return ProfileFact(
            field=ProfileField(row["field"]),
            value=_decoded_profile_value(row["value"], row["value_type"]),
            source=FactSource(row["source"]),
            source_message_id=row["source_message_id"],
            observed_at=row["observed_at"],
            confidence=Decimal(str(row["confidence"])),
            extraction_method=(
                ExtractionMethod.HUMAN
                if row["source"] == FactSource.HUMAN_ADVISOR.value
                else ExtractionMethod.AI
            ),
            validation_status=ValidationStatus(row["validation_status"]),
            confirmed_by=row["confirmed_by"],
        )

    def _conflict(self, row: RowMapping) -> FactConflict:
        previous = (
            self._connection.execute(
                select(lead_profile_values).where(
                    lead_profile_values.c.tenant_id == self._tenant_id.value,
                    lead_profile_values.c.id == row["previous_value_id"],
                )
            )
            .mappings()
            .one()
        )
        current = (
            self._connection.execute(
                select(lead_profile_values).where(
                    lead_profile_values.c.tenant_id == self._tenant_id.value,
                    lead_profile_values.c.id == row["new_value_id"],
                )
            )
            .mappings()
            .one()
        )
        return FactConflict(
            field=ProfileField(row["field"]),
            previous=self._fact(previous),
            current=self._fact(current),
            reason=row["reason"],
            detected_at=row["detected_at"],
        )


def extraction_result_snapshot(result: TelecomExtractionResult) -> dict[str, object]:
    return {
        "opportunity": {
            "primary": result.accepted.opportunity.opportunity.primary.value,
            "secondary": [item.value for item in result.accepted.opportunity.opportunity.secondary],
            "confidence": str(result.accepted.opportunity.confidence),
        },
        "fields": [
            {
                "field": item.value.field.value,
                "value": str(item.value.value)
                if isinstance(item.value.value, Decimal)
                else item.value.value,
                "source_message_id": str(item.value.source_message_id),
                "confidence": str(item.confidence),
                "treatment": item.treatment.value,
            }
            for item in result.accepted.fields
        ],
        "signals": [
            {
                "code": item.value.code.value,
                "source_message_id": str(item.value.source_message_id),
                "confidence": str(item.confidence),
            }
            for item in result.accepted.signals
        ],
        "controls": [
            {
                "control": item.control.value,
                "source_message_id": str(item.source_message_id),
                "deterministic": item.deterministic,
            }
            for item in result.accepted.controls
        ],
        "contradictions": [
            {
                "field": item.field.value,
                "previous_value": str(item.previous_value),
                "new_value": str(item.new_value),
                "new_source_message_id": str(item.new_source_message_id),
                "resolution": item.resolution,
            }
            for item in result.accepted.contradictions
        ],
        "missing_information": [item.value for item in result.accepted.missing_information],
        "next_question": (
            {
                "target_field": result.accepted.next_question.target_field.value,
                "text": result.accepted.next_question.text,
                "reason": result.accepted.next_question.reason,
                "priority": result.accepted.next_question.priority,
            }
            if result.accepted.next_question
            else None
        ),
        "handoff": {
            "kind": result.accepted.handoff.kind.value,
            "reason_codes": list(result.accepted.handoff.reason_codes),
        },
        "summary": result.accepted.summary[:1000],
        "overall_confidence": str(result.accepted.overall_confidence),
    }


def extraction_result_hash(result: TelecomExtractionResult) -> str:
    return _canonical_hash(extraction_result_snapshot(result))


class PostgresExtractionExecutionRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def find_by_key(
        self,
        idempotency_key: str,
        *,
        lock: bool = False,
    ) -> StoredExtraction | None:
        statement = select(extraction_executions).where(
            extraction_executions.c.tenant_id == self._tenant_id.value,
            extraction_executions.c.idempotency_key == idempotency_key,
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        return self._to_stored(row) if row is not None else None

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
    ) -> StoredExtraction:
        row = (
            self._connection.execute(
                postgres_insert(extraction_executions)
                .values(
                    tenant_id=self._tenant_id.value,
                    id=result.execution_id,
                    lead_id=lead_id,
                    conversation_id=result.conversation_id,
                    message_id=message_id,
                    processing_id=processing_id,
                    processing_version=processing_version,
                    idempotency_key=durable_idempotency_key,
                    conceptual_key=result.idempotency_key,
                    input_hash=input_hash,
                    result_hash=result_hash,
                    provider=result.provider,
                    model=result.model,
                    prompt_version=result.prompt_version,
                    schema_version=result.schema_version,
                    status=(
                        "completed" if result.outcome is ExtractionOutcome.COMPLETED else "degraded"
                    ),
                    outcome=result.outcome.value,
                    accepted_payload=extraction_result_snapshot(result),
                    warnings=list(result.warnings),
                    failures=[
                        {"code": item.code, "retryable": item.retryable}
                        for item in result.recoverable_errors
                    ],
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    estimated_cost_usd=result.usage.estimated_cost_usd,
                    latency_ms=result.latency_ms,
                    started_at=result.executed_at,
                    completed_at=now,
                    applied_at=now,
                    correlation_id=correlation_id,
                    causation_id=message_id,
                )
                .on_conflict_do_nothing(constraint="uq_extraction_executions_key")
                .returning(*extraction_executions.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            existing = self.find_by_key(durable_idempotency_key, lock=True)
            if existing is None:
                raise ConflictError("concurrent extraction conflict could not be resolved")
            if existing.result_hash != result_hash:
                raise IdempotencyConflictError(
                    "extraction idempotency key has a different result hash"
                )
            return existing
        return self._to_stored(row)

    @staticmethod
    def _to_stored(row: RowMapping) -> StoredExtraction:
        return StoredExtraction(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            result_hash=row["result_hash"],
            applied=row["applied_at"] is not None,
        )


class PostgresQualificationEvaluationRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def find_id_by_extraction(self, extraction_execution_id: UUID) -> UUID | None:
        value = self._connection.execute(
            select(qualification_evaluations.c.id).where(
                qualification_evaluations.c.tenant_id == self._tenant_id.value,
                qualification_evaluations.c.extraction_execution_id == extraction_execution_id,
            )
        ).scalar_one_or_none()
        if value is not None:
            assert isinstance(value, UUID)
        return value

    def add(
        self,
        result: QualificationResult,
        *,
        extraction_execution_id: UUID,
        message_id: UUID,
        processing_version: int,
        causation_id: UUID,
    ) -> None:
        self._connection.execute(
            insert(qualification_evaluations).values(
                tenant_id=self._tenant_id.value,
                id=result.id,
                lead_id=result.lead_id,
                conversation_id=result.conversation_id,
                message_id=message_id,
                extraction_execution_id=extraction_execution_id,
                processing_version=processing_version,
                policy_id=result.policy.id,
                policy_version=result.policy.version,
                policy_fingerprint=result.policy.fingerprint,
                opportunity=result.opportunity.primary.value,
                score=result.score,
                subtotal=result.subtotal,
                classification=result.classification.value,
                action=result.action.value,
                eligibility=result.eligibility.value,
                state_recommendation=(
                    result.state_recommendation.value
                    if result.state_recommendation is not None
                    else None
                ),
                engine_version=result.engine_version,
                dimensions=[
                    {
                        "dimension": item.dimension.value,
                        "raw_points": item.raw_points,
                        "points": item.points,
                        "maximum": item.maximum,
                    }
                    for item in result.dimensions
                ],
                contributions=[
                    {
                        "rule_code": item.rule_code,
                        "dimension": item.dimension.value,
                        "points": item.points,
                        "signal_codes": [code.value for code in item.signal_codes],
                        "explanation": item.explanation,
                    }
                    for item in result.contributions
                ],
                penalties=[
                    {
                        "rule_code": item.rule_code,
                        "adjustment": item.adjustment,
                        "effect": item.effect.value,
                        "signal_codes": [code.value for code in item.signal_codes],
                        "explanation": item.explanation,
                        "blocked_offer": item.blocked_offer,
                    }
                    for item in result.penalties
                ],
                effects=[item.value for item in result.effects],
                handoff={
                    "required": result.handoff.required,
                    "specialized": result.handoff.specialized,
                    "reason_codes": list(result.handoff.reason_codes),
                },
                explanation={
                    "summary": result.explanation.summary,
                    "positive_reasons": list(result.explanation.positive_reasons),
                    "penalty_reasons": list(result.explanation.penalty_reasons),
                    "applied_rule_codes": list(result.explanation.applied_rule_codes),
                    "blocked_offers": list(result.explanation.blocked_offers),
                },
                missing_information=[item.value for item in result.explanation.missing_information],
                evaluated_at=result.evaluated_at,
                actor="telecom-conversation-worker.v1",
                cause=result.cause.value,
                correlation_id=result.correlation_id,
                causation_id=causation_id,
                schema_version="qualification-evaluation.v1",
            )
        )


class PostgresHandoffRepository:
    ACTIVE = ("requested", "queued", "assigned", "accepted")

    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def active(
        self,
        conversation_id: UUID,
        *,
        lock: bool = False,
    ) -> HandoffRequest | None:
        statement = select(handoff_requests).where(
            handoff_requests.c.tenant_id == self._tenant_id.value,
            handoff_requests.c.conversation_id == conversation_id,
            handoff_requests.c.status.in_(self.ACTIVE),
        )
        if lock:
            statement = statement.with_for_update()
        row = self._connection.execute(statement).mappings().one_or_none()
        return self._to_domain(row) if row is not None else None

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
    ) -> CreatedHandoff:
        row = (
            self._connection.execute(
                postgres_insert(handoff_requests)
                .values(
                    tenant_id=self._tenant_id.value,
                    id=handoff_id,
                    conversation_id=conversation_id,
                    lead_id=lead_id,
                    kind=kind.value,
                    priority=priority,
                    reason=reason[:500],
                    score=score,
                    classification=classification,
                    summary=summary[:1000],
                    missing_information=[item.value for item in missing_information],
                    status=HandoffStatus.REQUESTED.value,
                    assigned_to=None,
                    source=source,
                    extraction_execution_id=extraction_execution_id,
                    created_at=now,
                    updated_at=now,
                    version=1,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        handoff_requests.c.tenant_id,
                        handoff_requests.c.conversation_id,
                    ],
                    index_where=handoff_requests.c.status.in_(self.ACTIVE),
                )
                .returning(*handoff_requests.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return CreatedHandoff(self._to_domain(row), created=True)
        existing = self.active(conversation_id, lock=True)
        if existing is None:
            raise ConflictError("concurrent handoff conflict could not be resolved")
        return CreatedHandoff(existing, created=False)

    def cancel_active(
        self,
        conversation_id: UUID,
        *,
        reason: str,
        now: datetime,
    ) -> int:
        del reason
        result = self._connection.execute(
            update(handoff_requests)
            .where(
                handoff_requests.c.tenant_id == self._tenant_id.value,
                handoff_requests.c.conversation_id == conversation_id,
                handoff_requests.c.status.in_(self.ACTIVE),
            )
            .values(
                status=HandoffStatus.CANCELLED.value,
                updated_at=now,
                version=handoff_requests.c.version + 1,
            )
        )
        return result.rowcount

    def transition(
        self,
        handoff: HandoffRequest,
        target: HandoffStatus,
        *,
        assigned_to: str | None,
        now: datetime,
    ) -> HandoffRequest:
        row = (
            self._connection.execute(
                update(handoff_requests)
                .where(
                    handoff_requests.c.tenant_id == self._tenant_id.value,
                    handoff_requests.c.id == handoff.id,
                    handoff_requests.c.version == handoff.version,
                )
                .values(
                    status=target.value,
                    assigned_to=assigned_to,
                    updated_at=now,
                    version=handoff_requests.c.version + 1,
                )
                .returning(*handoff_requests.c)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ConcurrencyConflictError("handoff changed during transition")
        return self._to_domain(row)

    def _to_domain(self, row: RowMapping) -> HandoffRequest:
        return HandoffRequest(
            id=row["id"],
            tenant_id=self._tenant_id,
            conversation_id=row["conversation_id"],
            lead_id=row["lead_id"],
            kind=HandoffType(row["kind"]),
            priority=row["priority"],
            reason=row["reason"],
            status=HandoffStatus(row["status"]),
            assigned_to=row["assigned_to"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )


class PostgresPendingQuestionRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def recently_asked(
        self,
        conversation_id: UUID,
        *,
        limit: int,
    ) -> tuple[ProfileField, ...]:
        values = self._connection.execute(
            select(pending_questions.c.target_field)
            .where(
                pending_questions.c.tenant_id == self._tenant_id.value,
                pending_questions.c.conversation_id == conversation_id,
            )
            .order_by(pending_questions.c.created_at.desc())
            .limit(limit)
        ).scalars()
        return tuple(dict.fromkeys(ProfileField(item) for item in values))

    def cancel_pending(
        self,
        conversation_id: UUID,
        *,
        reason: str,
        now: datetime,
    ) -> int:
        result = self._connection.execute(
            update(pending_questions)
            .where(
                pending_questions.c.tenant_id == self._tenant_id.value,
                pending_questions.c.conversation_id == conversation_id,
                pending_questions.c.status == "pending",
            )
            .values(
                status="cancelled",
                cancelled_at=now,
                cancellation_reason=reason[:100],
                version=pending_questions.c.version + 1,
            )
        )
        return result.rowcount

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
    ) -> CreatedQuestion:
        row = self._connection.execute(
            postgres_insert(pending_questions)
            .values(
                tenant_id=self._tenant_id.value,
                id=question_id,
                conversation_id=conversation_id,
                lead_id=lead_id,
                target_field=target_field.value,
                question=question,
                reason=reason[:500],
                priority=priority,
                extraction_execution_id=extraction_execution_id,
                status="pending",
                created_at=now,
                consumed_at=None,
                cancelled_at=None,
                cancellation_reason=None,
                version=1,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    pending_questions.c.tenant_id,
                    pending_questions.c.conversation_id,
                ],
                index_where=pending_questions.c.status == "pending",
            )
            .returning(pending_questions.c.id)
        ).scalar_one_or_none()
        if row is not None:
            return CreatedQuestion(row, created=True)
        existing = self._connection.execute(
            select(pending_questions.c.id).where(
                pending_questions.c.tenant_id == self._tenant_id.value,
                pending_questions.c.conversation_id == conversation_id,
                pending_questions.c.status == "pending",
            )
        ).scalar_one()
        return CreatedQuestion(existing, created=False)


class PostgresContactPreferenceRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

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
    ) -> bool:
        inserted = self._connection.execute(
            postgres_insert(contact_preferences)
            .values(
                tenant_id=self._tenant_id.value,
                id=preference_id,
                contact_id=contact_id,
                conversation_id=conversation_id,
                lead_id=lead_id,
                preference_type="do_not_contact",
                status="active",
                source_message_id=source_message_id,
                extraction_execution_id=extraction_execution_id,
                evidence_code="explicit_customer_request",
                evidence_hash=evidence_hash,
                recorded_at=now,
                revoked_at=None,
                revoked_by=None,
                correlation_id=correlation_id,
                version=1,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    contact_preferences.c.tenant_id,
                    contact_preferences.c.contact_id,
                    contact_preferences.c.preference_type,
                ],
                index_where=contact_preferences.c.status == "active",
            )
            .returning(contact_preferences.c.id)
        ).scalar_one_or_none()
        return inserted is not None


class PostgresAuditRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def append(self, event: AuditEventData, *, event_id: UUID, now: datetime) -> None:
        self._connection.execute(
            insert(audit_events).values(
                tenant_id=self._tenant_id.value,
                id=event_id,
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                category=event.category,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                schema_version=event.schema_version,
                metadata=dict(event.metadata),
                created_at=now,
            )
        )


class PostgresOutboxRepository:
    def __init__(self, connection: Connection, tenant_id: TenantId) -> None:
        self._connection = connection
        self._tenant_id = tenant_id

    def add(self, event: OutboxEventData, *, event_id: UUID, now: datetime) -> bool:
        payload = dict(event.payload)
        payload_hash = _canonical_hash(payload)
        inserted = self._connection.execute(
            postgres_insert(conversation_outbox_events)
            .values(
                tenant_id=self._tenant_id.value,
                id=event_id,
                event_key=event.event_key,
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                payload=payload,
                payload_hash=payload_hash,
                payload_version=event.payload_version,
                occurred_at=now,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                status="pending",
                attempts=0,
                next_attempt_at=now,
                lease_token=None,
                lease_until=None,
                last_error=None,
                published_at=None,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    conversation_outbox_events.c.tenant_id,
                    conversation_outbox_events.c.event_key,
                ]
            )
            .returning(conversation_outbox_events.c.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return True
        existing_hash = self._connection.execute(
            select(conversation_outbox_events.c.payload_hash).where(
                conversation_outbox_events.c.tenant_id == self._tenant_id.value,
                conversation_outbox_events.c.event_key == event.event_key,
            )
        ).scalar_one()
        if existing_hash != payload_hash:
            raise IdempotencyConflictError("outbox event key was reused with a different payload")
        return False


class PostgresConversationUnitOfWork:
    def __init__(
        self,
        engine: Engine,
        tenant_id: TenantId,
        ids: IdGenerator,
    ) -> None:
        self._engine = engine
        self.tenant_id = tenant_id
        self._ids = ids
        self._connection: Connection | None = None
        self._transaction: Any = None
        self._committed = False
        self.tenants: PostgresTenantRepository
        self.contacts: PostgresContactRepository
        self.leads: PostgresLeadRepository
        self.conversations: PostgresConversationRepository
        self.messages: PostgresMessageRepository
        self.processing: PostgresProcessingRepository
        self.profiles: PostgresProfileRepository
        self.extractions: PostgresExtractionExecutionRepository
        self.evaluations: PostgresQualificationEvaluationRepository
        self.handoffs: PostgresHandoffRepository
        self.questions: PostgresPendingQuestionRepository
        self.preferences: PostgresContactPreferenceRepository
        self.audit: PostgresAuditRepository
        self.outbox: PostgresOutboxRepository

    def __enter__(self) -> "PostgresConversationUnitOfWork":
        connection = self._engine.connect()
        self._connection = connection
        self._transaction = connection.begin()
        connection.execute(
            text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
            {"tenant_id": str(self.tenant_id.value)},
        )
        self.tenants = PostgresTenantRepository(connection, self.tenant_id)
        self.contacts = PostgresContactRepository(connection, self.tenant_id)
        self.leads = PostgresLeadRepository(connection, self.tenant_id)
        self.conversations = PostgresConversationRepository(connection, self.tenant_id)
        self.messages = PostgresMessageRepository(connection, self.tenant_id)
        self.processing = PostgresProcessingRepository(connection, self.tenant_id)
        self.profiles = PostgresProfileRepository(connection, self.tenant_id, self._ids)
        self.extractions = PostgresExtractionExecutionRepository(connection, self.tenant_id)
        self.evaluations = PostgresQualificationEvaluationRepository(connection, self.tenant_id)
        self.handoffs = PostgresHandoffRepository(connection, self.tenant_id)
        self.questions = PostgresPendingQuestionRepository(connection, self.tenant_id)
        self.preferences = PostgresContactPreferenceRepository(connection, self.tenant_id)
        self.audit = PostgresAuditRepository(connection, self.tenant_id)
        self.outbox = PostgresOutboxRepository(connection, self.tenant_id)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        del exc_type, traceback
        try:
            if self._transaction is not None and self._transaction.is_active:
                self._transaction.rollback()
        finally:
            if self._connection is not None:
                self._connection.close()
        if isinstance(exc_value, SQLAlchemyError):
            raise ConversationPersistenceError(
                "conversation database operation failed"
            ) from exc_value

    def commit(self) -> None:
        if self._transaction is None or not self._transaction.is_active:
            raise RuntimeError("unit of work has no active transaction")
        try:
            self._transaction.commit()
            self._committed = True
        except SQLAlchemyError as error:
            if self._transaction.is_active:
                self._transaction.rollback()
            raise ConversationPersistenceError("conversation transaction commit failed") from error

    def rollback(self) -> None:
        if self._transaction is not None and self._transaction.is_active:
            self._transaction.rollback()


class PostgresConversationUnitOfWorkFactory:
    def __init__(self, database: Database, ids: IdGenerator) -> None:
        self._database = database
        self._ids = ids

    def create(
        self,
        tenant_id: TenantId,
        *,
        worker: bool = False,
    ) -> PostgresConversationUnitOfWork:
        engine = self._database.worker_engine if worker else self._database.runtime_engine
        return PostgresConversationUnitOfWork(engine, tenant_id, self._ids)
