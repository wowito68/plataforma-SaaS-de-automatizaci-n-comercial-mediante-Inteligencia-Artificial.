import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from saas_platform.errors import ValidationError
from saas_platform.modules.lead_qualification.domain import (
    EligibilityStatus,
    LeadClassification,
    LeadState,
    OpportunityType,
)
from saas_platform.modules.telecom_conversations.errors import InvalidStateTransitionError
from saas_platform.modules.tenancy.domain import TenantId

type MessageMetadataValue = str | int | bool

EXTERNAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:@/+~=-]+$")
METADATA_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9]{10,15}$")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{name} must include timezone information")


def _require_version(value: int, name: str = "version") -> None:
    if value <= 0:
        raise ValidationError(f"{name} must be positive")


def _normalize_external_id(value: str, name: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 200 or not EXTERNAL_ID_PATTERN.fullmatch(normalized):
        raise ValidationError(f"{name} is invalid")
    return normalized


class ContactKind(StrEnum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class ContactPreference(StrEnum):
    UNSPECIFIED = "unspecified"
    AUTOMATED_ALLOWED = "automated_allowed"
    HUMAN_ONLY = "human_only"
    DO_NOT_CONTACT = "do_not_contact"


class ConversationChannel(StrEnum):
    INTERNAL = "internal"
    WHATSAPP = "whatsapp"
    SMS = "sms"
    WEBCHAT = "webchat"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageAuthor(StrEnum):
    CUSTOMER = "customer"
    SYSTEM = "system"
    HUMAN_ADVISOR = "human_advisor"


class MessageContentType(StrEnum):
    TEXT = "text"


class ConversationState(StrEnum):
    OPEN = "open"
    QUALIFYING = "qualifying"
    WAITING_CUSTOMER = "waiting_customer"
    WAITING_HUMAN = "waiting_human"
    HUMAN_ACTIVE = "human_active"
    AUTOMATED_PAUSED = "automated_paused"
    DO_NOT_CONTACT = "do_not_contact"
    CLOSED = "closed"


class TransitionActor(StrEnum):
    CUSTOMER = "customer"
    SYSTEM = "system"
    WORKER = "worker"
    HUMAN = "human"


class MessageProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_DEGRADED = "completed_degraded"
    BLOCKED_DO_NOT_CONTACT = "blocked_do_not_contact"
    BLOCKED_HANDOFF = "blocked_handoff"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    INVALID_EXTRACTION = "invalid_extraction"


class ProcessingOutcome(StrEnum):
    COMPLETED = "completed"
    COMPLETED_DEGRADED = "completed_degraded"
    DUPLICATE = "duplicate"
    BLOCKED_DO_NOT_CONTACT = "blocked_do_not_contact"
    BLOCKED_HANDOFF = "blocked_handoff"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    CONCURRENCY_CONFLICT = "concurrency_conflict"
    INVALID_EXTRACTION = "invalid_extraction"


class HandoffType(StrEnum):
    GENERAL = "general"
    SPECIALIZED = "specialized"


class HandoffStatus(StrEnum):
    REQUESTED = "requested"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    @property
    def active(self) -> bool:
        return self in {
            HandoffStatus.REQUESTED,
            HandoffStatus.QUEUED,
            HandoffStatus.ASSIGNED,
            HandoffStatus.ACCEPTED,
        }


class PendingQuestionStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    RETRY = "retry"
    PUBLISHED = "published"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class NormalizedConversationMessage:
    tenant_id: TenantId
    channel: ConversationChannel
    external_message_id: str
    external_contact_id: str
    direction: MessageDirection
    author: MessageAuthor
    content: str
    content_type: MessageContentType
    external_timestamp: datetime
    correlation_id: UUID
    external_conversation_id: str | None = None
    event_type: str = "message.received"
    metadata: Mapping[str, MessageMetadataValue] = field(default_factory=dict)
    normalized_phone: str | None = None
    contact_name: str | None = None
    contact_kind: ContactKind = ContactKind.INDIVIDUAL

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "external_message_id",
            _normalize_external_id(self.external_message_id, "external_message_id"),
        )
        object.__setattr__(
            self,
            "external_contact_id",
            _normalize_external_id(self.external_contact_id, "external_contact_id"),
        )
        if self.external_conversation_id is not None:
            object.__setattr__(
                self,
                "external_conversation_id",
                _normalize_external_id(
                    self.external_conversation_id,
                    "external_conversation_id",
                ),
            )
        event_type = self.event_type.strip()
        if not 1 <= len(event_type) <= 64 or not METADATA_KEY_PATTERN.fullmatch(event_type):
            raise ValidationError("event_type is invalid")
        object.__setattr__(self, "event_type", event_type)
        content = self.content.strip()
        if not content:
            raise ValidationError("message content cannot be empty")
        if len(content) > 20_000:
            raise ValidationError("message content exceeds 20000 characters")
        object.__setattr__(self, "content", content)
        _require_aware(self.external_timestamp, "external_timestamp")
        if self.direction is MessageDirection.INBOUND and self.author is not MessageAuthor.CUSTOMER:
            raise ValidationError("inbound messages must be authored by the customer")
        if self.direction is MessageDirection.OUTBOUND and self.author is MessageAuthor.CUSTOMER:
            raise ValidationError("outbound messages cannot be authored by the customer")

        metadata = dict(self.metadata)
        if len(metadata) > 20:
            raise ValidationError("message metadata accepts at most 20 entries")
        for key, value in metadata.items():
            if not METADATA_KEY_PATTERN.fullmatch(key):
                raise ValidationError("message metadata key is invalid")
            if isinstance(value, str) and len(value) > 256:
                raise ValidationError("message metadata string exceeds 256 characters")
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

        if self.normalized_phone is not None:
            phone = re.sub(r"[\s().-]", "", self.normalized_phone)
            if not PHONE_PATTERN.fullmatch(phone):
                raise ValidationError("normalized_phone is invalid")
            object.__setattr__(self, "normalized_phone", phone)
        if self.contact_name is not None:
            name = self.contact_name.strip()
            if not 1 <= len(name) <= 120:
                raise ValidationError("contact_name must contain between 1 and 120 characters")
            object.__setattr__(self, "contact_name", name)

    @property
    def conversation_reference(self) -> str:
        return self.external_conversation_id or self.external_contact_id

    def canonical_payload(self) -> dict[str, object]:
        return {
            "tenant_id": str(self.tenant_id.value),
            "channel": self.channel.value,
            "external_message_id": self.external_message_id,
            "external_contact_id": self.external_contact_id,
            "external_conversation_id": self.conversation_reference,
            "event_type": self.event_type,
            "direction": self.direction.value,
            "author": self.author.value,
            "content": self.content,
            "content_type": self.content_type.value,
            "external_timestamp": self.external_timestamp.astimezone(UTC).isoformat(),
            "metadata": dict(self.metadata),
            "normalized_phone": self.normalized_phone,
            "contact_name": self.contact_name,
            "contact_kind": self.contact_kind.value,
        }

    def payload_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def idempotency_key(self) -> str:
        encoded = json.dumps(
            {
                "tenant_id": str(self.tenant_id.value),
                "channel": self.channel.value,
                "external_message_id": self.external_message_id,
                "event_type": self.event_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Contact:
    id: UUID
    tenant_id: TenantId
    channel: ConversationChannel
    external_id: str
    normalized_phone: str | None
    name: str | None
    kind: ContactKind
    preference: ContactPreference
    do_not_contact: bool
    created_at: datetime
    updated_at: datetime
    version: int

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "contact created_at")
        _require_aware(self.updated_at, "contact updated_at")
        _require_version(self.version)
        if self.do_not_contact != (self.preference is ContactPreference.DO_NOT_CONTACT):
            raise ValidationError("contact DNC flag and preference must agree")


@dataclass(frozen=True, slots=True)
class Lead:
    id: UUID
    tenant_id: TenantId
    contact_id: UUID
    primary_opportunity: OpportunityType
    secondary_opportunities: tuple[OpportunityType, ...]
    state: LeadState
    current_evaluation_id: UUID | None
    current_score: int | None
    current_classification: LeadClassification | None
    current_policy_id: UUID | None
    current_policy_version: int | None
    current_policy_fingerprint: str | None
    eligibility: EligibilityStatus
    created_at: datetime
    updated_at: datetime
    version: int

    def __post_init__(self) -> None:
        _require_aware(self.created_at, "lead created_at")
        _require_aware(self.updated_at, "lead updated_at")
        _require_version(self.version)
        if self.primary_opportunity in self.secondary_opportunities:
            raise ValidationError("primary opportunity cannot also be secondary")
        if len(set(self.secondary_opportunities)) != len(self.secondary_opportunities):
            raise ValidationError("secondary opportunities must be unique")
        if self.current_score is not None and not 0 <= self.current_score <= 100:
            raise ValidationError("current lead score must be between 0 and 100")
        evaluation_fields = (
            self.current_score,
            self.current_classification,
            self.current_policy_id,
            self.current_policy_version,
            self.current_policy_fingerprint,
        )
        if self.current_evaluation_id is None and any(
            item is not None for item in evaluation_fields
        ):
            raise ValidationError("current evaluation fields require current_evaluation_id")


@dataclass(frozen=True, slots=True)
class Conversation:
    id: UUID
    tenant_id: TenantId
    contact_id: UUID
    lead_id: UUID
    channel: ConversationChannel
    external_id: str
    state: ConversationState
    automation_active: bool
    human_owner_id: str | None
    last_message_id: UUID | None
    last_activity_at: datetime
    opened_at: datetime
    closed_at: datetime | None
    commercial_summary: str | None
    processing_message_id: UUID | None
    processing_lease_token: UUID | None
    processing_lease_until: datetime | None
    version: int

    def __post_init__(self) -> None:
        _require_aware(self.last_activity_at, "conversation last_activity_at")
        _require_aware(self.opened_at, "conversation opened_at")
        if self.closed_at is not None:
            _require_aware(self.closed_at, "conversation closed_at")
        if self.processing_lease_until is not None:
            _require_aware(self.processing_lease_until, "conversation processing_lease_until")
        _require_version(self.version)
        lease_fields = (
            self.processing_message_id,
            self.processing_lease_token,
            self.processing_lease_until,
        )
        if any(item is None for item in lease_fields) and any(
            item is not None for item in lease_fields
        ):
            raise ValidationError("conversation processing lease fields must be set together")
        if (
            self.state
            in {
                ConversationState.WAITING_HUMAN,
                ConversationState.HUMAN_ACTIVE,
                ConversationState.AUTOMATED_PAUSED,
                ConversationState.DO_NOT_CONTACT,
                ConversationState.CLOSED,
            }
            and self.automation_active
        ):
            raise ValidationError("conversation state requires automation to be inactive")
        if self.state is ConversationState.CLOSED and self.closed_at is None:
            raise ValidationError("closed conversation requires closed_at")


@dataclass(frozen=True, slots=True)
class StoredMessage:
    id: UUID
    tenant_id: TenantId
    conversation_id: UUID
    channel: ConversationChannel
    external_message_id: str
    event_type: str
    direction: MessageDirection
    author: MessageAuthor
    content: str
    content_type: MessageContentType
    external_timestamp: datetime
    received_at: datetime
    processing_status: MessageProcessingStatus
    fingerprint: str
    idempotency_key: str
    correlation_id: UUID
    version: int

    def __post_init__(self) -> None:
        _require_aware(self.external_timestamp, "message external_timestamp")
        _require_aware(self.received_at, "message received_at")
        _require_version(self.version)
        if len(self.fingerprint) != 64 or len(self.idempotency_key) != 64:
            raise ValidationError("message hashes must be SHA-256 values")


@dataclass(frozen=True, slots=True)
class MessageProcessing:
    id: UUID
    tenant_id: TenantId
    conversation_id: UUID
    message_id: UUID
    processing_version: int
    status: MessageProcessingStatus
    attempts: int
    lease_token: UUID | None
    lease_until: datetime | None
    next_attempt_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    extraction_execution_id: UUID | None
    evaluation_id: UUID | None
    last_error_code: str | None

    def __post_init__(self) -> None:
        _require_version(self.processing_version, "processing_version")
        if self.attempts < 0:
            raise ValidationError("processing attempts cannot be negative")
        _require_aware(self.next_attempt_at, "processing next_attempt_at")
        for value, name in (
            (self.lease_until, "processing lease_until"),
            (self.started_at, "processing started_at"),
            (self.completed_at, "processing completed_at"),
        ):
            if value is not None:
                _require_aware(value, name)
        if (self.lease_token is None) != (self.lease_until is None):
            raise ValidationError("processing lease token and expiry must be set together")


@dataclass(frozen=True, slots=True)
class IngestConversationResult:
    contact: Contact
    conversation: Conversation
    lead: Lead
    message: StoredMessage
    contact_created: bool
    conversation_created: bool
    lead_created: bool
    message_created: bool

    @property
    def duplicate(self) -> bool:
        return not self.message_created


@dataclass(frozen=True, slots=True)
class ProcessingClaim:
    tenant_id: TenantId
    processing_id: UUID
    conversation_id: UUID
    message_id: UUID
    processing_version: int
    lease_token: UUID
    correlation_id: UUID
    attempts: int


@dataclass(frozen=True, slots=True)
class ProcessConversationResult:
    tenant_id: TenantId
    processing_id: UUID
    message_id: UUID
    outcome: ProcessingOutcome
    extraction_execution_id: UUID | None = None
    evaluation_id: UUID | None = None
    handoff_id: UUID | None = None
    question_id: UUID | None = None
    profile_changed: bool = False


@dataclass(frozen=True, slots=True)
class HandoffRequest:
    id: UUID
    tenant_id: TenantId
    conversation_id: UUID
    lead_id: UUID
    kind: HandoffType
    priority: int
    reason: str
    status: HandoffStatus
    assigned_to: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    def __post_init__(self) -> None:
        if not 1 <= self.priority <= 1000:
            raise ValidationError("handoff priority must be between 1 and 1000")
        if not self.reason.strip() or len(self.reason) > 500:
            raise ValidationError("handoff reason is invalid")
        _require_aware(self.created_at, "handoff created_at")
        _require_aware(self.updated_at, "handoff updated_at")
        _require_version(self.version)


class ConversationStateMachine:
    _allowed: Mapping[ConversationState, frozenset[ConversationState]] = {
        ConversationState.OPEN: frozenset(
            {
                ConversationState.QUALIFYING,
                ConversationState.WAITING_HUMAN,
                ConversationState.AUTOMATED_PAUSED,
                ConversationState.DO_NOT_CONTACT,
                ConversationState.CLOSED,
            }
        ),
        ConversationState.QUALIFYING: frozenset(
            {
                ConversationState.WAITING_CUSTOMER,
                ConversationState.WAITING_HUMAN,
                ConversationState.AUTOMATED_PAUSED,
                ConversationState.DO_NOT_CONTACT,
                ConversationState.CLOSED,
            }
        ),
        ConversationState.WAITING_CUSTOMER: frozenset(
            {
                ConversationState.QUALIFYING,
                ConversationState.WAITING_HUMAN,
                ConversationState.AUTOMATED_PAUSED,
                ConversationState.DO_NOT_CONTACT,
                ConversationState.CLOSED,
            }
        ),
        ConversationState.WAITING_HUMAN: frozenset(
            {
                ConversationState.HUMAN_ACTIVE,
                ConversationState.QUALIFYING,
                ConversationState.AUTOMATED_PAUSED,
                ConversationState.DO_NOT_CONTACT,
                ConversationState.CLOSED,
            }
        ),
        ConversationState.HUMAN_ACTIVE: frozenset(
            {
                ConversationState.WAITING_CUSTOMER,
                ConversationState.QUALIFYING,
                ConversationState.AUTOMATED_PAUSED,
                ConversationState.DO_NOT_CONTACT,
                ConversationState.CLOSED,
            }
        ),
        ConversationState.AUTOMATED_PAUSED: frozenset(
            {
                ConversationState.WAITING_HUMAN,
                ConversationState.HUMAN_ACTIVE,
                ConversationState.QUALIFYING,
                ConversationState.DO_NOT_CONTACT,
                ConversationState.CLOSED,
            }
        ),
        ConversationState.DO_NOT_CONTACT: frozenset({ConversationState.OPEN}),
        ConversationState.CLOSED: frozenset({ConversationState.OPEN}),
    }

    def transition(
        self,
        current: ConversationState,
        target: ConversationState,
        *,
        actor: TransitionActor,
        cause: str,
    ) -> ConversationState:
        if not cause.strip():
            raise InvalidStateTransitionError("conversation transition requires a cause")
        if target not in self._allowed[current]:
            raise InvalidStateTransitionError(
                f"conversation cannot transition from {current.value} to {target.value}"
            )
        if current in {ConversationState.DO_NOT_CONTACT, ConversationState.CLOSED}:
            if actor is not TransitionActor.HUMAN:
                raise InvalidStateTransitionError(
                    "reopening a terminal conversation requires a human actor"
                )
            required_cause = (
                "explicit_consent_restored"
                if current is ConversationState.DO_NOT_CONTACT
                else "manual_reopen"
            )
            if cause != required_cause:
                raise InvalidStateTransitionError(f"reopening requires cause {required_cause}")
        if target is ConversationState.HUMAN_ACTIVE and actor is not TransitionActor.HUMAN:
            raise InvalidStateTransitionError("human_active requires a human actor")
        return target


class LeadStateMachine:
    _allowed: Mapping[LeadState, frozenset[LeadState]] = {
        LeadState.NEW: frozenset(
            {
                LeadState.IN_CONVERSATION,
                LeadState.QUALIFYING,
                LeadState.INCOMPLETE_INFORMATION,
                LeadState.QUALIFIED,
                LeadState.DUPLICATE,
                LeadState.DO_NOT_CONTACT,
            }
        ),
        LeadState.IN_CONVERSATION: frozenset(
            {
                LeadState.QUALIFYING,
                LeadState.INCOMPLETE_INFORMATION,
                LeadState.QUALIFIED,
                LeadState.TRANSFERRED_TO_ADVISOR,
                LeadState.LOST,
                LeadState.DO_NOT_CONTACT,
            }
        ),
        LeadState.QUALIFYING: frozenset(
            {
                LeadState.INCOMPLETE_INFORMATION,
                LeadState.QUALIFIED,
                LeadState.PENDING_VALIDATION,
                LeadState.TRANSFERRED_TO_ADVISOR,
                LeadState.LOST,
                LeadState.DO_NOT_CONTACT,
            }
        ),
        LeadState.INCOMPLETE_INFORMATION: frozenset(
            {
                LeadState.QUALIFYING,
                LeadState.QUALIFIED,
                LeadState.TRANSFERRED_TO_ADVISOR,
                LeadState.LOST,
                LeadState.DO_NOT_CONTACT,
            }
        ),
        LeadState.QUALIFIED: frozenset(
            {
                LeadState.QUALIFYING,
                LeadState.INCOMPLETE_INFORMATION,
                LeadState.QUOTE_REQUESTED,
                LeadState.TRANSFERRED_TO_ADVISOR,
                LeadState.LOST,
                LeadState.DO_NOT_CONTACT,
            }
        ),
        LeadState.QUOTE_REQUESTED: frozenset(
            {
                LeadState.QUOTE_SENT,
                LeadState.TRANSFERRED_TO_ADVISOR,
                LeadState.LOST,
                LeadState.DO_NOT_CONTACT,
            }
        ),
        LeadState.TRANSFERRED_TO_ADVISOR: frozenset(
            {
                LeadState.CONTACTED_BY_ADVISOR,
                LeadState.QUALIFYING,
                LeadState.QUALIFIED,
                LeadState.LOST,
                LeadState.DO_NOT_CONTACT,
            }
        ),
        LeadState.CONTACTED_BY_ADVISOR: frozenset(
            {
                LeadState.QUOTE_REQUESTED,
                LeadState.PORTABILITY_STARTED,
                LeadState.CONTRACTING_STARTED,
                LeadState.LOST,
                LeadState.DO_NOT_CONTACT,
            }
        ),
    }

    def transition(
        self,
        current: LeadState,
        target: LeadState,
        *,
        actor: TransitionActor,
        cause: str,
    ) -> LeadState:
        if not cause.strip():
            raise InvalidStateTransitionError("lead transition requires a cause")
        if current is target:
            return current
        if target is LeadState.DO_NOT_CONTACT and current not in {
            LeadState.SALE_COMPLETED,
            LeadState.DUPLICATE,
        }:
            return target
        if current is LeadState.DO_NOT_CONTACT:
            if actor is not TransitionActor.HUMAN or cause != "explicit_consent_restored":
                raise InvalidStateTransitionError(
                    "leaving do_not_contact requires explicit human consent restoration"
                )
            if target is not LeadState.IN_CONVERSATION:
                raise InvalidStateTransitionError(
                    "restored consent returns the lead to in_conversation"
                )
            return target
        if target not in self._allowed.get(current, frozenset()):
            raise InvalidStateTransitionError(
                f"lead cannot transition from {current.value} to {target.value}"
            )
        return target


class HandoffStateMachine:
    _allowed: Mapping[HandoffStatus, frozenset[HandoffStatus]] = {
        HandoffStatus.REQUESTED: frozenset(
            {HandoffStatus.QUEUED, HandoffStatus.CANCELLED, HandoffStatus.EXPIRED}
        ),
        HandoffStatus.QUEUED: frozenset(
            {HandoffStatus.ASSIGNED, HandoffStatus.CANCELLED, HandoffStatus.EXPIRED}
        ),
        HandoffStatus.ASSIGNED: frozenset(
            {
                HandoffStatus.ACCEPTED,
                HandoffStatus.QUEUED,
                HandoffStatus.CANCELLED,
                HandoffStatus.EXPIRED,
            }
        ),
        HandoffStatus.ACCEPTED: frozenset({HandoffStatus.RESOLVED, HandoffStatus.CANCELLED}),
        HandoffStatus.RESOLVED: frozenset(),
        HandoffStatus.CANCELLED: frozenset(),
        HandoffStatus.EXPIRED: frozenset(),
    }

    def transition(self, current: HandoffStatus, target: HandoffStatus) -> HandoffStatus:
        if target not in self._allowed[current]:
            raise InvalidStateTransitionError(
                f"handoff cannot transition from {current.value} to {target.value}"
            )
        return target
