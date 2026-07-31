"""Create the Iteration 1 multi-tenant walking-skeleton schema.

Revision ID: 20260731_0001
Revises:
Create Date: 2026-07-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260731_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_SETTING = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
TENANT_TABLES = (
    "tenants",
    "synthetic_events",
    "outbox_items",
    "queue_messages",
    "consumer_receipts",
    "audit_events",
)


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_tenants_status"),
        sa.CheckConstraint("version > 0", name="ck_tenants_version"),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )
    op.create_table(
        "synthetic_events",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('accepted', 'processed', 'failed')",
            name="ck_synthetic_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_synthetic_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_synthetic_events_tenant",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_synthetic_events"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_synthetic_events_tenant_idempotency",
        ),
    )
    op.create_table(
        "outbox_items",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=60), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
        sa.CheckConstraint("status IN ('pending', 'published')", name="ck_outbox_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "aggregate_id"],
            ["synthetic_events.tenant_id", "synthetic_events.id"],
            name="fk_outbox_synthetic_event",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_outbox_items"),
        sa.UniqueConstraint(
            "tenant_id",
            "aggregate_id",
            "event_type",
            name="uq_outbox_aggregate_event",
        ),
    )
    op.create_index(
        "ix_outbox_pending_available",
        "outbox_items",
        ["status", "available_at"],
    )
    op.create_table(
        "queue_messages",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_outbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=100), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_queue_attempts"),
        sa.CheckConstraint(
            "status IN ('available', 'leased', 'completed', 'dead')",
            name="ck_queue_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_outbox_id"],
            ["outbox_items.tenant_id", "outbox_items.id"],
            name="fk_queue_outbox",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_queue_messages"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_outbox_id",
            name="uq_queue_source_outbox",
        ),
    )
    op.create_index(
        "ix_queue_claim",
        "queue_messages",
        ["status", "available_at", "lease_until"],
    )
    op.create_table(
        "consumer_receipts",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumer_name", sa.String(length=100), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            ["queue_messages.tenant_id", "queue_messages.id"],
            name="fk_consumer_receipt_queue",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "consumer_name",
            "message_id",
            name="pk_consumer_receipts",
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=60), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=120), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_audit_tenant"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_audit_events"),
    )
    op.create_index(
        "ix_audit_tenant_created",
        "audit_events",
        ["tenant_id", "created_at"],
    )

    for table in TENANT_TABLES:
        column = "id" if table == "tenants" else "tenant_id"
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_tenant_isolation ON {table} "
                f"USING ({column} = {TENANT_SETTING}) "
                f"WITH CHECK ({column} = {TENANT_SETTING})"
            )
        )

    op.execute("GRANT SELECT ON tenants TO app_runtime")
    op.execute("GRANT SELECT, INSERT ON synthetic_events TO app_runtime")
    op.execute("GRANT INSERT ON outbox_items, audit_events TO app_runtime")

    op.execute("GRANT SELECT ON tenants TO app_worker")
    op.execute("GRANT SELECT, UPDATE ON synthetic_events, outbox_items TO app_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON queue_messages TO app_worker")
    op.execute("GRANT SELECT, INSERT ON consumer_receipts, audit_events TO app_worker")


def downgrade() -> None:
    op.drop_index("ix_audit_tenant_created", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("consumer_receipts")
    op.drop_index("ix_queue_claim", table_name="queue_messages")
    op.drop_table("queue_messages")
    op.drop_index("ix_outbox_pending_available", table_name="outbox_items")
    op.drop_table("outbox_items")
    op.drop_table("synthetic_events")
    op.drop_table("tenants")
