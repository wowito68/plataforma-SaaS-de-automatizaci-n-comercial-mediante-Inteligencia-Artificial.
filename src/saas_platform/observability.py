import contextvars
import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from prometheus_client import Counter, Gauge, Histogram

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
tenant_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tenant_id", default=None
)

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "password",
        "secret",
        "token",
        "api_key",
        "document",
        "prompt",
        "message_content",
    }
)

HTTP_REQUESTS = Counter(
    "saas_http_requests_total",
    "HTTP requests handled by the API",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "saas_http_request_duration_seconds",
    "HTTP request duration",
    ("method", "route"),
)
SYNTHETIC_EVENTS = Counter(
    "saas_synthetic_events_total",
    "Synthetic events accepted by result",
    ("result",),
)
OUTBOX_DISPATCHES = Counter(
    "saas_outbox_dispatches_total",
    "Outbox dispatch attempts",
    ("result",),
)
WORKER_MESSAGES = Counter(
    "saas_worker_messages_total",
    "Queue messages processed by result",
    ("result",),
)
OUTBOX_PENDING = Gauge("saas_outbox_pending", "Pending outbox items observed by the worker")
CONVERSATION_MESSAGES_INGESTED = Counter(
    "saas_conversation_messages_ingested_total",
    "Normalized conversation messages ingested by result",
    ("result",),
)
CONVERSATION_MESSAGES_PROCESSED = Counter(
    "saas_conversation_messages_processed_total",
    "Conversation messages processed by outcome",
    ("outcome",),
)
CONVERSATION_PROCESSING_DURATION = Histogram(
    "saas_conversation_processing_duration_seconds",
    "Conversation message processing duration",
    ("outcome",),
)
TELECOM_EXTRACTION_APPLICATIONS = Counter(
    "saas_telecom_extraction_applications_total",
    "Durable extraction applications by result",
    ("result",),
)
LEAD_PROFILE_CONFLICTS = Counter(
    "saas_lead_profile_conflicts_total",
    "Commercial profile conflicts persisted",
)
QUALIFICATION_EVALUATIONS = Counter(
    "saas_qualification_evaluations_total",
    "Deterministic qualification evaluations by classification",
    ("classification",),
)
LEAD_CLASSIFICATION_CHANGES = Counter(
    "saas_lead_classification_changes_total",
    "Current lead classification changes",
    ("previous", "current"),
)
DO_NOT_CONTACT_REGISTRATIONS = Counter(
    "saas_do_not_contact_registrations_total",
    "Durable do-not-contact registrations by result",
    ("result",),
)
HANDOFF_REQUESTS = Counter(
    "saas_handoff_requests_total",
    "Durable handoff requests by result",
    ("result",),
)
CONVERSATION_CONCURRENCY_CONFLICTS = Counter(
    "saas_conversation_concurrency_conflicts_total",
    "Conversation concurrency conflicts by operation",
    ("operation",),
)
CONVERSATION_RETRIES = Counter(
    "saas_conversation_retries_total",
    "Conversation retries by operation",
    ("operation",),
)
CONVERSATION_OUTBOX_EVENTS = Counter(
    "saas_conversation_outbox_events_total",
    "Conversation outbox publication attempts by result",
    ("result",),
)
CONVERSATION_OUTBOX_PENDING = Gauge(
    "saas_conversation_outbox_pending",
    "Pending or retryable conversation outbox events",
)
CONVERSATION_ROLLBACKS = Counter(
    "saas_conversation_rollbacks_total",
    "Conversation unit-of-work rollbacks by operation",
    ("operation",),
)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None:
        normalized_key = key.lower()
        if any(
            normalized_key == sensitive
            or normalized_key.startswith(f"{sensitive}_")
            or normalized_key.endswith(f"_{sensitive}")
            for sensitive in SENSITIVE_KEYS
        ):
            return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, *, environment: str, service: str) -> None:
        super().__init__()
        self.environment = environment
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "environment": self.environment,
            "service": self.service,
            "module": record.name,
            "request_id": request_id_var.get(),
            "correlation_id": correlation_id_var.get(),
            "tenant_id": tenant_id_var.get(),
        }
        for field in (
            "user_id",
            "conversation_id",
            "message_id",
            "message_processing_id",
            "causation_id",
            "event_id",
            "operation",
            "error_type",
            "result",
            "execution_id",
            "extraction_id",
            "evaluation_id",
            "provider",
            "model",
            "prompt_version",
            "schema_version",
            "duration_ms",
            "outcome",
            "input_tokens",
            "output_tokens",
        ):
            if hasattr(record, field):
                payload[field] = redact(getattr(record, field), field)
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else "Unknown"
        return json.dumps(redact(payload), separators=(",", ":"), ensure_ascii=True)


def configure_logging(*, environment: str, service: str, level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(environment=environment, service=service))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def bind_context(
    *,
    request_id: UUID | str | None = None,
    correlation_id: UUID | str | None = None,
    tenant_id: UUID | str | None = None,
) -> list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]]:
    bound: list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]] = []
    for variable, value in (
        (request_id_var, request_id),
        (correlation_id_var, correlation_id),
        (tenant_id_var, tenant_id),
    ):
        if value is not None:
            bound.append((variable, variable.set(str(value))))
    return bound


def reset_context(
    bound: list[tuple[contextvars.ContextVar[str | None], contextvars.Token[str | None]]],
) -> None:
    for variable, token in reversed(bound):
        variable.reset(token)
