from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

tenants = Table(
    "tenants",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),
    CheckConstraint("version > 0", name="ck_tenants_version"),
)

synthetic_events = Table(
    "synthetic_events",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("idempotency_key", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "idempotency_key",
        name="uq_synthetic_events_tenant_idempotency",
    ),
    ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_synthetic_events_tenant"),
    CheckConstraint("status IN ('accepted', 'processed', 'failed')", name="ck_synthetic_status"),
    CheckConstraint("version > 0", name="ck_synthetic_version"),
)

outbox_items = Table(
    "outbox_items",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("aggregate_type", String(60), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("event_type", String(100), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "tenant_id",
        "aggregate_id",
        "event_type",
        name="uq_outbox_aggregate_event",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "aggregate_id"],
        ["synthetic_events.tenant_id", "synthetic_events.id"],
        name="fk_outbox_synthetic_event",
    ),
    CheckConstraint("status IN ('pending', 'published')", name="ck_outbox_status"),
    CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
)
Index("ix_outbox_pending_available", outbox_items.c.status, outbox_items.c.available_at)

queue_messages = Table(
    "queue_messages",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source_outbox_id", UUID(as_uuid=True), nullable=False),
    Column("message_type", String(100), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("last_error_type", String(100), nullable=True),
    UniqueConstraint("tenant_id", "source_outbox_id", name="uq_queue_source_outbox"),
    ForeignKeyConstraint(
        ["tenant_id", "source_outbox_id"],
        ["outbox_items.tenant_id", "outbox_items.id"],
        name="fk_queue_outbox",
    ),
    CheckConstraint(
        "status IN ('available', 'leased', 'completed', 'dead')",
        name="ck_queue_status",
    ),
    CheckConstraint("attempts >= 0", name="ck_queue_attempts"),
)
Index(
    "ix_queue_claim",
    queue_messages.c.status,
    queue_messages.c.available_at,
    queue_messages.c.lease_until,
)

consumer_receipts = Table(
    "consumer_receipts",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("consumer_name", String(100), primary_key=True),
    Column("message_id", UUID(as_uuid=True), primary_key=True),
    Column("payload_hash", String(64), nullable=False),
    Column("processed_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "message_id"],
        ["queue_messages.tenant_id", "queue_messages.id"],
        name="fk_consumer_receipt_queue",
    ),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("action", String(100), nullable=False),
    Column("target_type", String(60), nullable=False),
    Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("actor_type", String(40), nullable=False),
    Column("actor_id", String(120), nullable=True),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_audit_tenant"),
)
Index("ix_audit_tenant_created", audit_events.c.tenant_id, audit_events.c.created_at)
