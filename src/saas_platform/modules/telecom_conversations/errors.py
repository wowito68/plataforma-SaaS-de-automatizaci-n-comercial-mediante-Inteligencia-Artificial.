from saas_platform.errors import (
    ConflictError,
    InfrastructureError,
    NotFoundError,
    PermanentError,
    TransientError,
    ValidationError,
)


class InvalidTenantError(NotFoundError):
    code = "invalid_tenant"


class CrossTenantReferenceError(NotFoundError):
    code = "cross_tenant_reference"


class DuplicateMessageError(ConflictError):
    code = "duplicate_message"


class IdempotencyConflictError(ConflictError):
    code = "idempotency_conflict"


class ConcurrencyConflictError(ConflictError):
    code = "concurrency_conflict"


class ConversationClosedError(ConflictError):
    code = "conversation_closed"


class DoNotContactError(ConflictError):
    code = "do_not_contact"


class AutomationPausedError(ConflictError):
    code = "automation_paused"


class PolicyUnavailableError(PermanentError):
    code = "policy_unavailable"


class InvalidExtractionError(ValidationError):
    code = "invalid_extraction"


class TemporaryExtractionError(TransientError):
    code = "temporary_extraction_failure"


class PermanentProcessingError(PermanentError):
    code = "permanent_processing_failure"


class InvalidStateTransitionError(ConflictError):
    code = "invalid_state_transition"


class DuplicateHandoffError(ConflictError):
    code = "duplicate_handoff"


class ConversationPersistenceError(InfrastructureError):
    code = "conversation_persistence_error"


class OutboxPublicationError(TransientError):
    code = "outbox_publication_error"
