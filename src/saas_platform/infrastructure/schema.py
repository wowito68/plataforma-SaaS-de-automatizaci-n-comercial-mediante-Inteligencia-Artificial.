from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
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
    Column("category", String(60), nullable=False, server_default="platform"),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True), nullable=True),
    Column("schema_version", String(40), nullable=False, server_default="audit-event.v1"),
    Column("metadata", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_audit_tenant"),
)
Index("ix_audit_tenant_created", audit_events.c.tenant_id, audit_events.c.created_at)

contacts = Table(
    "contacts",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("channel", String(20), nullable=False),
    Column("external_id", String(200), nullable=False),
    Column("normalized_phone", String(20), nullable=True),
    Column("name", String(120), nullable=True),
    Column("kind", String(20), nullable=False),
    Column("preference", String(30), nullable=False),
    Column("do_not_contact", Boolean, nullable=False),
    Column("dnc_recorded_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "channel", "external_id", name="uq_contacts_external"),
    ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_contacts_tenant"),
    CheckConstraint(
        "channel IN ('internal', 'whatsapp', 'sms', 'webchat')",
        name="ck_contacts_channel",
    ),
    CheckConstraint("kind IN ('individual', 'business')", name="ck_contacts_kind"),
    CheckConstraint(
        "preference IN ('unspecified', 'automated_allowed', 'human_only', 'do_not_contact')",
        name="ck_contacts_preference",
    ),
    CheckConstraint(
        "(do_not_contact AND preference = 'do_not_contact' AND dnc_recorded_at IS NOT NULL) "
        "OR (NOT do_not_contact AND preference <> 'do_not_contact')",
        name="ck_contacts_dnc_consistency",
    ),
    CheckConstraint("version > 0", name="ck_contacts_version"),
)

leads = Table(
    "leads",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("contact_id", UUID(as_uuid=True), nullable=False),
    Column("primary_opportunity", String(40), nullable=False),
    Column("secondary_opportunities", JSONB, nullable=False),
    Column("state", String(40), nullable=False),
    Column("current_evaluation_id", UUID(as_uuid=True), nullable=True),
    Column("current_score", Integer, nullable=True),
    Column("current_classification", String(20), nullable=True),
    Column("current_policy_id", UUID(as_uuid=True), nullable=True),
    Column("current_policy_version", Integer, nullable=True),
    Column("current_policy_fingerprint", String(64), nullable=True),
    Column("eligibility", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "id", "contact_id", name="uq_leads_id_contact"),
    ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_leads_tenant"),
    ForeignKeyConstraint(
        ["tenant_id", "contact_id"],
        ["contacts.tenant_id", "contacts.id"],
        name="fk_leads_contact",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "current_evaluation_id", "id"],
        [
            "qualification_evaluations.tenant_id",
            "qualification_evaluations.id",
            "qualification_evaluations.lead_id",
        ],
        name="fk_leads_current_evaluation",
        use_alter=True,
    ),
    CheckConstraint(
        "primary_opportunity IN ('device_purchase', 'device_with_plan', "
        "'plan_subscription', 'new_line', 'portability', 'renewal', "
        "'prepaid_to_postpaid', 'accessory', 'multiple_lines', "
        "'business_account', 'unidentified')",
        name="ck_leads_primary_opportunity",
    ),
    CheckConstraint(
        "jsonb_typeof(secondary_opportunities) = 'array'",
        name="ck_leads_secondary_array",
    ),
    CheckConstraint(
        "state IN ('new', 'in_conversation', 'qualifying', 'incomplete_information', "
        "'qualified', 'quote_requested', 'quote_sent', 'pending_documents', "
        "'pending_validation', 'pending_inventory', 'pending_coverage', "
        "'pending_financing', 'transferred_to_advisor', 'contacted_by_advisor', "
        "'portability_started', 'contracting_started', 'sale_completed', 'lost', "
        "'not_eligible', 'duplicate', 'do_not_contact')",
        name="ck_leads_state",
    ),
    CheckConstraint(
        "current_score IS NULL OR current_score BETWEEN 0 AND 100",
        name="ck_leads_current_score",
    ),
    CheckConstraint(
        "(current_evaluation_id IS NULL AND current_score IS NULL "
        "AND current_classification IS NULL AND current_policy_id IS NULL "
        "AND current_policy_version IS NULL AND current_policy_fingerprint IS NULL) OR "
        "(current_evaluation_id IS NOT NULL AND current_score IS NOT NULL "
        "AND current_classification IS NOT NULL AND current_policy_id IS NOT NULL "
        "AND current_policy_version IS NOT NULL AND current_policy_fingerprint IS NOT NULL)",
        name="ck_leads_evaluation_consistency",
    ),
    CheckConstraint("version > 0", name="ck_leads_version"),
)

conversations = Table(
    "conversations",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("contact_id", UUID(as_uuid=True), nullable=False),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("channel", String(20), nullable=False),
    Column("external_id", String(200), nullable=False),
    Column("state", String(30), nullable=False),
    Column("automation_active", Boolean, nullable=False),
    Column("human_owner_id", String(120), nullable=True),
    Column("last_message_id", UUID(as_uuid=True), nullable=True),
    Column("last_activity_at", DateTime(timezone=True), nullable=False),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    Column("commercial_summary", String(2000), nullable=True),
    Column("processing_message_id", UUID(as_uuid=True), nullable=True),
    Column("processing_lease_token", UUID(as_uuid=True), nullable=True),
    Column("processing_lease_until", DateTime(timezone=True), nullable=True),
    Column("version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "channel", "external_id", name="uq_conversations_external"),
    UniqueConstraint("tenant_id", "id", "channel", name="uq_conversations_id_channel"),
    UniqueConstraint("tenant_id", "id", "lead_id", name="uq_conversations_id_lead"),
    UniqueConstraint(
        "tenant_id",
        "id",
        "contact_id",
        "lead_id",
        name="uq_conversations_identity",
    ),
    ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_conversations_tenant"),
    ForeignKeyConstraint(
        ["tenant_id", "contact_id"],
        ["contacts.tenant_id", "contacts.id"],
        name="fk_conversations_contact",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "lead_id", "contact_id"],
        ["leads.tenant_id", "leads.id", "leads.contact_id"],
        name="fk_conversations_lead_contact",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "last_message_id", "id"],
        [
            "conversation_messages.tenant_id",
            "conversation_messages.id",
            "conversation_messages.conversation_id",
        ],
        name="fk_conversations_last_message",
        use_alter=True,
    ),
    ForeignKeyConstraint(
        ["tenant_id", "processing_message_id", "id"],
        [
            "conversation_messages.tenant_id",
            "conversation_messages.id",
            "conversation_messages.conversation_id",
        ],
        name="fk_conversations_processing_message",
        use_alter=True,
    ),
    CheckConstraint(
        "channel IN ('internal', 'whatsapp', 'sms', 'webchat')",
        name="ck_conversations_channel",
    ),
    CheckConstraint(
        "state IN ('open', 'qualifying', 'waiting_customer', 'waiting_human', "
        "'human_active', 'automated_paused', 'do_not_contact', 'closed')",
        name="ck_conversations_state",
    ),
    CheckConstraint(
        "state NOT IN ('waiting_human', 'human_active', 'automated_paused', "
        "'do_not_contact', 'closed') OR NOT automation_active",
        name="ck_conversations_automation_state",
    ),
    CheckConstraint(
        "(state = 'closed' AND closed_at IS NOT NULL) OR (state <> 'closed' AND closed_at IS NULL)",
        name="ck_conversations_closed_at",
    ),
    CheckConstraint(
        "(processing_message_id IS NULL AND processing_lease_token IS NULL "
        "AND processing_lease_until IS NULL) OR "
        "(processing_message_id IS NOT NULL AND processing_lease_token IS NOT NULL "
        "AND processing_lease_until IS NOT NULL)",
        name="ck_conversations_processing_lease",
    ),
    CheckConstraint("version > 0", name="ck_conversations_version"),
)

conversation_messages = Table(
    "conversation_messages",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("channel", String(20), nullable=False),
    Column("external_message_id", String(200), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("direction", String(20), nullable=False),
    Column("author", String(30), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_type", String(20), nullable=False),
    Column("external_timestamp", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("processing_status", String(30), nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("idempotency_key", String(64), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "channel",
        "external_message_id",
        "event_type",
        name="uq_conversation_messages_external",
    ),
    UniqueConstraint(
        "tenant_id",
        "id",
        "conversation_id",
        name="uq_conversation_messages_identity",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id", "channel"],
        ["conversations.tenant_id", "conversations.id", "conversations.channel"],
        name="fk_conversation_messages_conversation",
    ),
    CheckConstraint(
        "direction IN ('inbound', 'outbound')",
        name="ck_conversation_messages_direction",
    ),
    CheckConstraint(
        "author IN ('customer', 'system', 'human_advisor')",
        name="ck_conversation_messages_author",
    ),
    CheckConstraint(
        "(direction = 'inbound' AND author = 'customer') OR "
        "(direction = 'outbound' AND author <> 'customer')",
        name="ck_conversation_messages_author_direction",
    ),
    CheckConstraint("content_type = 'text'", name="ck_conversation_messages_content_type"),
    CheckConstraint(
        "char_length(content) BETWEEN 1 AND 20000",
        name="ck_conversation_messages_content_length",
    ),
    CheckConstraint(
        "processing_status IN ('pending', 'processing', 'completed', "
        "'completed_degraded', 'blocked_do_not_contact', 'blocked_handoff', "
        "'retryable_failure', 'permanent_failure', 'invalid_extraction')",
        name="ck_conversation_messages_processing_status",
    ),
    CheckConstraint("char_length(fingerprint) = 64", name="ck_messages_fingerprint"),
    CheckConstraint("char_length(idempotency_key) = 64", name="ck_messages_idempotency"),
    CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_messages_metadata_object"),
    CheckConstraint("version > 0", name="ck_conversation_messages_version"),
)
Index(
    "ix_conversation_messages_timeline",
    conversation_messages.c.tenant_id,
    conversation_messages.c.conversation_id,
    conversation_messages.c.external_timestamp,
)

message_processing = Table(
    "message_processing",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("processing_version", Integer, nullable=False),
    Column("status", String(30), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("lease_token", UUID(as_uuid=True), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=True),
    Column("evaluation_id", UUID(as_uuid=True), nullable=True),
    Column("last_error_code", String(100), nullable=True),
    UniqueConstraint(
        "tenant_id",
        "message_id",
        "processing_version",
        name="uq_message_processing_version",
    ),
    UniqueConstraint(
        "tenant_id",
        "id",
        "message_id",
        name="uq_message_processing_identity",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "message_id", "conversation_id"],
        [
            "conversation_messages.tenant_id",
            "conversation_messages.id",
            "conversation_messages.conversation_id",
        ],
        name="fk_message_processing_message",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id"],
        ["extraction_executions.tenant_id", "extraction_executions.id"],
        name="fk_message_processing_extraction",
        use_alter=True,
    ),
    ForeignKeyConstraint(
        ["tenant_id", "evaluation_id"],
        ["qualification_evaluations.tenant_id", "qualification_evaluations.id"],
        name="fk_message_processing_evaluation",
        use_alter=True,
    ),
    CheckConstraint("processing_version > 0", name="ck_message_processing_version"),
    CheckConstraint("attempts >= 0", name="ck_message_processing_attempts"),
    CheckConstraint(
        "status IN ('pending', 'processing', 'completed', 'completed_degraded', "
        "'blocked_do_not_contact', 'blocked_handoff', 'retryable_failure', "
        "'permanent_failure', 'invalid_extraction')",
        name="ck_message_processing_status",
    ),
    CheckConstraint(
        "(lease_token IS NULL AND lease_until IS NULL) OR "
        "(lease_token IS NOT NULL AND lease_until IS NOT NULL)",
        name="ck_message_processing_lease",
    ),
)
Index(
    "ix_message_processing_claim",
    message_processing.c.status,
    message_processing.c.next_attempt_at,
    message_processing.c.lease_until,
)

extraction_executions = Table(
    "extraction_executions",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("processing_id", UUID(as_uuid=True), nullable=False),
    Column("processing_version", Integer, nullable=False),
    Column("idempotency_key", String(64), nullable=False),
    Column("conceptual_key", String(64), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("result_hash", String(64), nullable=False),
    Column("provider", String(40), nullable=False),
    Column("model", String(100), nullable=False),
    Column("prompt_version", String(80), nullable=False),
    Column("schema_version", String(80), nullable=False),
    Column("status", String(20), nullable=False),
    Column("outcome", String(20), nullable=False),
    Column("accepted_payload", JSONB, nullable=False),
    Column("warnings", JSONB, nullable=False),
    Column("failures", JSONB, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("estimated_cost_usd", Numeric(18, 8), nullable=True),
    Column("latency_ms", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True), nullable=False),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_extraction_executions_key"),
    UniqueConstraint("tenant_id", "processing_id", name="uq_extraction_processing"),
    UniqueConstraint("tenant_id", "id", "lead_id", name="uq_extraction_id_lead"),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id", "lead_id"],
        ["conversations.tenant_id", "conversations.id", "conversations.lead_id"],
        name="fk_extraction_conversation_lead",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "message_id", "conversation_id"],
        [
            "conversation_messages.tenant_id",
            "conversation_messages.id",
            "conversation_messages.conversation_id",
        ],
        name="fk_extraction_message",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "processing_id", "message_id"],
        [
            "message_processing.tenant_id",
            "message_processing.id",
            "message_processing.message_id",
        ],
        name="fk_extraction_processing",
    ),
    CheckConstraint("processing_version > 0", name="ck_extraction_processing_version"),
    CheckConstraint(
        "status IN ('completed', 'degraded', 'invalid', 'failed')",
        name="ck_extraction_status",
    ),
    CheckConstraint("outcome IN ('completed', 'degraded')", name="ck_extraction_outcome"),
    CheckConstraint("jsonb_typeof(accepted_payload) = 'object'", name="ck_extraction_payload"),
    CheckConstraint("jsonb_typeof(warnings) = 'array'", name="ck_extraction_warnings"),
    CheckConstraint("jsonb_typeof(failures) = 'array'", name="ck_extraction_failures"),
    CheckConstraint(
        "input_tokens >= 0 AND output_tokens >= 0 AND latency_ms >= 0",
        name="ck_extraction_usage",
    ),
    CheckConstraint(
        "estimated_cost_usd IS NULL OR estimated_cost_usd >= 0",
        name="ck_extraction_cost",
    ),
)

lead_profile_values = Table(
    "lead_profile_values",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("field", String(80), nullable=False),
    Column("value", JSONB, nullable=False),
    Column("value_type", String(20), nullable=False),
    Column("value_hash", String(64), nullable=False),
    Column("application_key", String(64), nullable=False),
    Column("source", String(30), nullable=False),
    Column("source_message_id", UUID(as_uuid=True), nullable=True),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=False),
    Column("confidence", Numeric(5, 4), nullable=False),
    Column("validation_status", String(20), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("confirmed_by", String(120), nullable=True),
    Column("is_current", Boolean, nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_to", DateTime(timezone=True), nullable=True),
    Column("support_count", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "application_key", name="uq_profile_values_application"),
    UniqueConstraint("tenant_id", "id", "lead_id", name="uq_profile_values_identity"),
    ForeignKeyConstraint(
        ["tenant_id", "lead_id"],
        ["leads.tenant_id", "leads.id"],
        name="fk_profile_values_lead",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_message_id"],
        ["conversation_messages.tenant_id", "conversation_messages.id"],
        name="fk_profile_values_message",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id", "lead_id"],
        [
            "extraction_executions.tenant_id",
            "extraction_executions.id",
            "extraction_executions.lead_id",
        ],
        name="fk_profile_values_extraction",
    ),
    CheckConstraint(
        "value_type IN ('string', 'integer', 'boolean', 'decimal')", name="ck_profile_value_type"
    ),
    CheckConstraint("char_length(value_hash) = 64", name="ck_profile_value_hash"),
    CheckConstraint("char_length(application_key) = 64", name="ck_profile_application_key"),
    CheckConstraint(
        "source IN ('customer', 'system_lookup', 'human_advisor', 'import')",
        name="ck_profile_source",
    ),
    CheckConstraint(
        "validation_status IN ('hypothesis', 'accepted', 'confirmed', 'rejected')",
        name="ck_profile_validation_status",
    ),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_profile_confidence"),
    CheckConstraint(
        "(is_current AND valid_to IS NULL) OR (NOT is_current)",
        name="ck_profile_current_validity",
    ),
    CheckConstraint("support_count > 0", name="ck_profile_support_count"),
    CheckConstraint("version > 0", name="ck_profile_version"),
)
Index(
    "uq_profile_values_current",
    lead_profile_values.c.tenant_id,
    lead_profile_values.c.lead_id,
    lead_profile_values.c.field,
    unique=True,
    postgresql_where=lead_profile_values.c.is_current.is_(True),
)
Index(
    "ix_profile_values_history",
    lead_profile_values.c.tenant_id,
    lead_profile_values.c.lead_id,
    lead_profile_values.c.observed_at,
)

lead_profile_conflicts = Table(
    "lead_profile_conflicts",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("field", String(80), nullable=False),
    Column("previous_value_id", UUID(as_uuid=True), nullable=False),
    Column("new_value_id", UUID(as_uuid=True), nullable=False),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(20), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("resolution", String(500), nullable=True),
    Column("detected_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint(
        "tenant_id",
        "extraction_execution_id",
        "field",
        "new_value_id",
        name="uq_profile_conflicts_application",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "previous_value_id", "lead_id"],
        [
            "lead_profile_values.tenant_id",
            "lead_profile_values.id",
            "lead_profile_values.lead_id",
        ],
        name="fk_profile_conflicts_previous",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "new_value_id", "lead_id"],
        [
            "lead_profile_values.tenant_id",
            "lead_profile_values.id",
            "lead_profile_values.lead_id",
        ],
        name="fk_profile_conflicts_new",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id", "lead_id"],
        [
            "extraction_executions.tenant_id",
            "extraction_executions.id",
            "extraction_executions.lead_id",
        ],
        name="fk_profile_conflicts_extraction",
    ),
    CheckConstraint("status IN ('open', 'resolved')", name="ck_profile_conflicts_status"),
    CheckConstraint(
        "(status = 'open' AND resolved_at IS NULL) OR "
        "(status = 'resolved' AND resolved_at IS NOT NULL AND resolution IS NOT NULL)",
        name="ck_profile_conflicts_resolution",
    ),
)
Index(
    "ix_profile_conflicts_open",
    lead_profile_conflicts.c.tenant_id,
    lead_profile_conflicts.c.lead_id,
    lead_profile_conflicts.c.status,
)

lead_commercial_signals = Table(
    "lead_commercial_signals",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("code", String(100), nullable=False),
    Column("source_message_id", UUID(as_uuid=True), nullable=True),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=False),
    Column("confidence", Numeric(5, 4), nullable=False),
    Column("extraction_method", String(20), nullable=False),
    Column("validation_status", String(20), nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("application_key", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "application_key", name="uq_commercial_signals_application"),
    ForeignKeyConstraint(
        ["tenant_id", "lead_id"],
        ["leads.tenant_id", "leads.id"],
        name="fk_commercial_signals_lead",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_message_id"],
        ["conversation_messages.tenant_id", "conversation_messages.id"],
        name="fk_commercial_signals_message",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id", "lead_id"],
        [
            "extraction_executions.tenant_id",
            "extraction_executions.id",
            "extraction_executions.lead_id",
        ],
        name="fk_commercial_signals_extraction",
    ),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_commercial_signals_confidence"),
    CheckConstraint(
        "extraction_method IN ('explicit', 'ai', 'human', 'system', 'import')",
        name="ck_commercial_signals_method",
    ),
    CheckConstraint(
        "validation_status IN ('hypothesis', 'accepted', 'confirmed', 'rejected')",
        name="ck_commercial_signals_status",
    ),
)
Index(
    "ix_commercial_signals_active",
    lead_commercial_signals.c.tenant_id,
    lead_commercial_signals.c.lead_id,
    lead_commercial_signals.c.is_active,
)

qualification_evaluations = Table(
    "qualification_evaluations",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("message_id", UUID(as_uuid=True), nullable=False),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=False),
    Column("processing_version", Integer, nullable=False),
    Column("policy_id", UUID(as_uuid=True), nullable=False),
    Column("policy_version", Integer, nullable=False),
    Column("policy_fingerprint", String(64), nullable=False),
    Column("opportunity", String(40), nullable=False),
    Column("score", Integer, nullable=False),
    Column("subtotal", Integer, nullable=False),
    Column("classification", String(20), nullable=False),
    Column("action", String(50), nullable=False),
    Column("eligibility", String(40), nullable=False),
    Column("state_recommendation", String(40), nullable=True),
    Column("engine_version", String(80), nullable=False),
    Column("dimensions", JSONB, nullable=False),
    Column("contributions", JSONB, nullable=False),
    Column("penalties", JSONB, nullable=False),
    Column("effects", JSONB, nullable=False),
    Column("handoff", JSONB, nullable=False),
    Column("explanation", JSONB, nullable=False),
    Column("missing_information", JSONB, nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("cause", String(40), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True), nullable=False),
    Column("schema_version", String(40), nullable=False),
    UniqueConstraint(
        "tenant_id",
        "message_id",
        "processing_version",
        name="uq_qualification_message_version",
    ),
    UniqueConstraint(
        "tenant_id",
        "extraction_execution_id",
        name="uq_qualification_extraction",
    ),
    UniqueConstraint(
        "tenant_id",
        "id",
        "lead_id",
        name="uq_qualification_identity",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id", "lead_id"],
        ["conversations.tenant_id", "conversations.id", "conversations.lead_id"],
        name="fk_qualification_conversation_lead",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "message_id", "conversation_id"],
        [
            "conversation_messages.tenant_id",
            "conversation_messages.id",
            "conversation_messages.conversation_id",
        ],
        name="fk_qualification_message",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id", "lead_id"],
        [
            "extraction_executions.tenant_id",
            "extraction_executions.id",
            "extraction_executions.lead_id",
        ],
        name="fk_qualification_extraction",
    ),
    CheckConstraint("processing_version > 0", name="ck_qualification_processing_version"),
    CheckConstraint("policy_version > 0", name="ck_qualification_policy_version"),
    CheckConstraint("score BETWEEN 0 AND 100", name="ck_qualification_score"),
    CheckConstraint("subtotal BETWEEN 0 AND 100", name="ck_qualification_subtotal"),
    CheckConstraint("jsonb_typeof(dimensions) = 'array'", name="ck_qualification_dimensions"),
    CheckConstraint("jsonb_typeof(contributions) = 'array'", name="ck_qualification_contributions"),
    CheckConstraint("jsonb_typeof(penalties) = 'array'", name="ck_qualification_penalties"),
    CheckConstraint("jsonb_typeof(effects) = 'array'", name="ck_qualification_effects"),
    CheckConstraint("jsonb_typeof(handoff) = 'object'", name="ck_qualification_handoff"),
    CheckConstraint("jsonb_typeof(explanation) = 'object'", name="ck_qualification_explanation"),
    CheckConstraint("jsonb_typeof(missing_information) = 'array'", name="ck_qualification_missing"),
)
Index(
    "ix_qualification_lead_history",
    qualification_evaluations.c.tenant_id,
    qualification_evaluations.c.lead_id,
    qualification_evaluations.c.evaluated_at,
)

contact_preferences = Table(
    "contact_preferences",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("contact_id", UUID(as_uuid=True), nullable=False),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("preference_type", String(30), nullable=False),
    Column("status", String(20), nullable=False),
    Column("source_message_id", UUID(as_uuid=True), nullable=False),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=False),
    Column("evidence_code", String(100), nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("revoked_by", String(120), nullable=True),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id", "contact_id", "lead_id"],
        [
            "conversations.tenant_id",
            "conversations.id",
            "conversations.contact_id",
            "conversations.lead_id",
        ],
        name="fk_contact_preferences_conversation",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "source_message_id", "conversation_id"],
        [
            "conversation_messages.tenant_id",
            "conversation_messages.id",
            "conversation_messages.conversation_id",
        ],
        name="fk_contact_preferences_message",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id", "lead_id"],
        [
            "extraction_executions.tenant_id",
            "extraction_executions.id",
            "extraction_executions.lead_id",
        ],
        name="fk_contact_preferences_extraction",
    ),
    CheckConstraint("preference_type = 'do_not_contact'", name="ck_contact_preferences_type"),
    CheckConstraint("status IN ('active', 'revoked')", name="ck_contact_preferences_status"),
    CheckConstraint("char_length(evidence_hash) = 64", name="ck_contact_preferences_hash"),
    CheckConstraint(
        "(status = 'active' AND revoked_at IS NULL AND revoked_by IS NULL) OR "
        "(status = 'revoked' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
        name="ck_contact_preferences_revocation",
    ),
    CheckConstraint("version > 0", name="ck_contact_preferences_version"),
)
Index(
    "uq_contact_preferences_active",
    contact_preferences.c.tenant_id,
    contact_preferences.c.contact_id,
    contact_preferences.c.preference_type,
    unique=True,
    postgresql_where=contact_preferences.c.status == "active",
)

handoff_requests = Table(
    "handoff_requests",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("priority", Integer, nullable=False),
    Column("reason", String(500), nullable=False),
    Column("score", Integer, nullable=False),
    Column("classification", String(20), nullable=False),
    Column("summary", String(1000), nullable=False),
    Column("missing_information", JSONB, nullable=False),
    Column("status", String(20), nullable=False),
    Column("assigned_to", String(120), nullable=True),
    Column("source", String(30), nullable=False),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "id",
        "conversation_id",
        name="uq_handoff_requests_identity",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id", "lead_id"],
        ["conversations.tenant_id", "conversations.id", "conversations.lead_id"],
        name="fk_handoff_requests_conversation",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id", "lead_id"],
        [
            "extraction_executions.tenant_id",
            "extraction_executions.id",
            "extraction_executions.lead_id",
        ],
        name="fk_handoff_requests_extraction",
    ),
    CheckConstraint("kind IN ('general', 'specialized')", name="ck_handoff_requests_kind"),
    CheckConstraint("priority BETWEEN 1 AND 1000", name="ck_handoff_requests_priority"),
    CheckConstraint("score BETWEEN 0 AND 100", name="ck_handoff_requests_score"),
    CheckConstraint(
        "status IN ('requested', 'queued', 'assigned', 'accepted', 'resolved', "
        "'cancelled', 'expired')",
        name="ck_handoff_requests_status",
    ),
    CheckConstraint(
        "source IN ('customer', 'extraction', 'scoring')", name="ck_handoff_requests_source"
    ),
    CheckConstraint(
        "jsonb_typeof(missing_information) = 'array'", name="ck_handoff_requests_missing"
    ),
    CheckConstraint("version > 0", name="ck_handoff_requests_version"),
)
Index(
    "uq_handoff_requests_active",
    handoff_requests.c.tenant_id,
    handoff_requests.c.conversation_id,
    unique=True,
    postgresql_where=handoff_requests.c.status.in_(("requested", "queued", "assigned", "accepted")),
)

pending_questions = Table(
    "pending_questions",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("conversation_id", UUID(as_uuid=True), nullable=False),
    Column("lead_id", UUID(as_uuid=True), nullable=False),
    Column("target_field", String(80), nullable=False),
    Column("question", String(180), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("priority", Integer, nullable=False),
    Column("extraction_execution_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("cancelled_at", DateTime(timezone=True), nullable=True),
    Column("cancellation_reason", String(100), nullable=True),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "conversation_id", "lead_id"],
        ["conversations.tenant_id", "conversations.id", "conversations.lead_id"],
        name="fk_pending_questions_conversation",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "extraction_execution_id", "lead_id"],
        [
            "extraction_executions.tenant_id",
            "extraction_executions.id",
            "extraction_executions.lead_id",
        ],
        name="fk_pending_questions_extraction",
    ),
    CheckConstraint("char_length(question) BETWEEN 1 AND 180", name="ck_pending_questions_length"),
    CheckConstraint("priority BETWEEN 1 AND 100", name="ck_pending_questions_priority"),
    CheckConstraint(
        "status IN ('pending', 'consumed', 'cancelled')", name="ck_pending_questions_status"
    ),
    CheckConstraint(
        "(status = 'pending' AND consumed_at IS NULL AND cancelled_at IS NULL) OR "
        "(status = 'consumed' AND consumed_at IS NOT NULL AND cancelled_at IS NULL) OR "
        "(status = 'cancelled' AND consumed_at IS NULL AND cancelled_at IS NOT NULL "
        "AND cancellation_reason IS NOT NULL)",
        name="ck_pending_questions_lifecycle",
    ),
    CheckConstraint("version > 0", name="ck_pending_questions_version"),
)
Index(
    "uq_pending_questions_active",
    pending_questions.c.tenant_id,
    pending_questions.c.conversation_id,
    unique=True,
    postgresql_where=pending_questions.c.status == "pending",
)

conversation_outbox_events = Table(
    "conversation_outbox_events",
    metadata,
    Column("tenant_id", UUID(as_uuid=True), primary_key=True),
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("event_key", String(128), nullable=False),
    Column("event_type", String(100), nullable=False),
    Column("aggregate_type", String(60), nullable=False),
    Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("payload_version", Integer, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("correlation_id", UUID(as_uuid=True), nullable=False),
    Column("causation_id", UUID(as_uuid=True), nullable=False),
    Column("status", String(20), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_token", UUID(as_uuid=True), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("last_error", String(300), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "event_key", name="uq_conversation_outbox_event_key"),
    ForeignKeyConstraint(
        ["tenant_id"],
        ["tenants.id"],
        name="fk_conversation_outbox_tenant",
    ),
    CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_conversation_outbox_payload"),
    CheckConstraint("char_length(payload_hash) = 64", name="ck_conversation_outbox_hash"),
    CheckConstraint("payload_version > 0", name="ck_conversation_outbox_version"),
    CheckConstraint("attempts >= 0", name="ck_conversation_outbox_attempts"),
    CheckConstraint(
        "status IN ('pending', 'publishing', 'retry', 'published', 'dead')",
        name="ck_conversation_outbox_status",
    ),
    CheckConstraint(
        "(lease_token IS NULL AND lease_until IS NULL) OR "
        "(lease_token IS NOT NULL AND lease_until IS NOT NULL)",
        name="ck_conversation_outbox_lease",
    ),
)
Index(
    "ix_conversation_outbox_claim",
    conversation_outbox_events.c.status,
    conversation_outbox_events.c.next_attempt_at,
    conversation_outbox_events.c.lease_until,
)
