# Delivery 3 Impact Analysis

## Baseline and decision gate

Delivery 3 adds the first durable telecommunications conversation boundary. The
pre-change gate completed on 2026-07-31 with a clean worktree:

| Check | Evidence |
|---|---|
| Full suite | `265 passed`; global coverage `95.90%` |
| Ruff | Format and lint passed for 82 files |
| Mypy | Strict mode passed for 61 source/test files |
| PostgreSQL | Compose PostgreSQL 16 service healthy |
| Alembic | `20260731_0001 (head)` |

The only warning is the already registered Starlette/httpx transition (TD-004).
No unrelated failure blocks the delivery.

The selected design adds a bounded context named `telecom_conversations`. It
owns Contact, Lead, Conversation and persistent message processing. It composes,
but does not rewrite, `telecom_extraction` and `lead_qualification`.

## Current state

- Tenancy, a synthetic walking skeleton, PostgreSQL RLS, transactional outbox,
  leases, audit and API/worker composition already exist.
- Persistence uses SQLAlchemy Core, Psycopg and Alembic; no ORM entities or
  generic CRUD repositories exist.
- The worker role currently uses `BYPASSRLS` to discover global work (TD-002),
  while runtime operations set `app.current_tenant_id` per transaction.
- Delivery 1 owns immutable profiles, deterministic scoring and tenant-scoped
  policy contracts. Its scoring engine and policy rules remain authoritative.
- Delivery 2 owns untrusted structured extraction, local validation, confidence,
  contradictions, DNC detection, question selection and profile proposals.
  Its in-memory idempotency key includes tenant, conversation, selected messages,
  prompt, schema and operation.
- No durable Contact, Lead, Conversation, telecom message, profile, extraction,
  evaluation, handoff or pending question currently exists.

## Affected modules

| Area | Impact |
|---|---|
| `modules/telecom_conversations` | New domain, ports, use cases, PostgreSQL adapters, UoW and worker |
| `infrastructure/schema.py` | Adds the Delivery 3 SQLAlchemy Core table metadata |
| `infrastructure/database.py` | Exposes explicit connection/UoW construction without repository commits |
| `bootstrap.py` | Composes ingest/query paths, optional extraction processing and outbox publication |
| `entrypoints/api.py` | Adds development-only normalized internal ingest/query adapters |
| `entrypoints/worker.py` | Runs telecom claims/processing and outbox publication alongside the existing worker |
| `config.py` / `.env.example` | Adds bounded processing, outbox and internal-adapter settings |
| `observability.py` | Adds low-cardinality conversation, processing and outbox metrics/log fields |
| Alembic | Adds one revision after `20260731_0001`; existing revisions remain unchanged |
| Tests/documentation | Adds unit, PostgreSQL, migration, concurrency and A-F evidence |

`lead_qualification/scoring.py`, its weights, classifications and default rule
definitions are not modified. The provider schema cannot gain score, eligibility,
state, coverage or inventory authority.

## Reused contracts

- `TenantId`, `Clock`, `IdGenerator` and existing application error hierarchy.
- `LeadProfile`, `ProfileFact`, `FactConflict`, `Opportunity`, `CommercialSignal`,
  `QualificationRequest`, `QualificationResult` and `LeadState`.
- `EvaluateLeadQualification`, `ScoringPolicyProvider` and
  `build_default_telecom_policy`.
- `ExtractTelecomConversation`, `CommercialExtractionProvider`, structured result
  stages, prompt/schema versions and local DNC/human safeguards.
- PostgreSQL transactions, composite tenant keys, forced RLS, `SKIP LOCKED`, JSON
  logging, Prometheus and the current process composition style.

The code-defined base scoring policy remains the explicitly approved fallback
from Delivery 1. A tenant/opportunity-scoped provider materializes it with a
stable policy ID and version; policy persistence/administration remains Delivery
4 and TD-007.

## New components

- Pure Contact, Conversation and Handoff domain types plus explicit Conversation
  and Lead state transition services.
- `IngestConversationMessage` for provider-neutral normalized input.
- `ProcessConversationMessage` for extraction, incremental application and
  deterministic evaluation.
- `IncrementalLeadProfileApplier` for same/new/conflicting/corrected values.
- Tenant-bound semantic repositories and an explicit
  `PostgresConversationUnitOfWork`; no generic CRUD repository.
- A global worker-only job claimer and durable per-conversation lease.
- A PostgreSQL outbox publisher with an injected event sink, leases, retries,
  stable IDs and poison-event handling.
- Read models for processing and a bounded Lead snapshot; no administrative CRUD.

## Aggregates and ownership

| Aggregate/root | Owned state and invariant |
|---|---|
| Contact | Identity per tenant/channel, contact preference and durable DNC |
| Conversation | Contact/Lead association, state, automation and one processing lease |
| Lead | Opportunity, commercial state and pointer to the current immutable evaluation |
| MessageProcessing | One processing version per accepted message and at-most-once application |
| QualificationEvaluation | Immutable historical scoring snapshot |
| HandoffRequest | One active request per conversation with valid transitions |

Message and profile-value rows are owned records inside the Conversation/Lead
processing boundary, not independent aggregate roots. Audit and outbox are
append-only transactional records. A table is not treated as an aggregate merely
because it is persisted.

## Persistent entities

The planned revision adds only records needed by the requested flows:

- `contacts`, `leads`, `conversations`, `conversation_messages`;
- `message_processing` and `extraction_executions`;
- `lead_profile_values`, `lead_profile_conflicts` and
  `lead_commercial_signals`;
- `qualification_evaluations`;
- `contact_preferences`, `handoff_requests`, `pending_questions`;
- `conversation_outbox_events`.

The existing `audit_events` table is extended with category, causation and
schema-version metadata and protected by an append-only trigger. Evaluations are
also trigger-protected from update/delete. Structured JSON is limited to bounded
snapshots (secondary opportunities, evaluation details, event payloads and safe
metadata). Commercial profile facts use typed/history rows rather than an opaque
profile document or one table per field.

No policy, catalog, inventory, coverage, financing, response, advisor assignment,
campaign or CRM tables are introduced.

## Transaction boundaries

### Ingest

One runtime tenant transaction validates the active tenant, resolves or creates
Contact/Conversation/Lead, inserts the message and processing row, updates the
conversation cursor, appends audit events, writes
`ConversationMessageIngested`, and commits. Repositories never commit.

### Processing

Processing deliberately avoids holding a database transaction during a network
call:

1. A short worker transaction claims one due message with `FOR UPDATE SKIP
   LOCKED`, writes a lease token on its Conversation and marks processing active.
2. A tenant-scoped read transaction builds the immutable extraction command.
3. The provider call runs without an open database transaction.
4. One final worker UoW locks Conversation, Contact, Lead and processing state,
   verifies the lease/version and atomically persists execution, profile history,
   conflicts, signals, immutable evaluation, DNC/handoff/question/state changes,
   audit and outbox before commit.

A crash before step 4 leaves no partial commercial effects. The lease expires and
the same processing version can retry. A crash after step 4 sees completed durable
idempotency records and cannot apply again.

### Outbox publication

Claim and publication acknowledgement use separate short transactions. Delivery
is at least once; the event ID/key is stable for consumer deduplication. A crash
after sink delivery and before acknowledgement may redeliver but cannot create a
second outbox record.

## Idempotency strategy

| Effect | Durable key/constraint |
|---|---|
| Message ingest | tenant + channel + external message ID + event type; SHA-256 payload hash |
| Extraction | Delivery 2 key + processing version; unique per tenant |
| Profile value/signal | extraction + field/code + source/value fingerprint |
| Evaluation | unique message + processing version and unique extraction execution |
| Handoff | partial unique active request per tenant/conversation |
| Pending question | partial unique pending question per tenant/conversation |
| Outbox | stable event key unique per tenant and canonical payload hash |

Same key/same hash returns the existing result. Same key/different hash commits a
redacted audit conflict and then raises an explicit application conflict. Pending,
retryable, completed and permanent states are durable. Authorized reprocessing
requires a new processing version; ordinary retries reuse the current version.

## Concurrency strategy

- `SELECT FOR UPDATE SKIP LOCKED` claims jobs and outbox rows.
- A lease token and expiry stored on Conversation serialize all messages for that
  conversation across processes without a distributed coordinator.
- Message-processing lease tokens fence stale workers.
- Contact, Lead and Conversation have positive version columns and conditional
  updates; stale expected versions produce a domain concurrency conflict.
- Unique/partial indexes close races for message IDs, extraction applications,
  evaluations, active handoffs and pending questions.
- Final processing rechecks DNC and active handoff after acquiring locks.
- DNC cancels active handoffs/questions and leaves automation stopped, so final
  state is safe even when the request was ingested while earlier work was active.

PostgreSQL advisory locks are not selected: the durable Conversation lease is
sufficient and survives transaction boundaries. No distributed coordinator is
added.

## Multi-tenant strategy and RLS

Delivery 3 uses all three evaluated layers:

1. Tenant context is mandatory in commands, UoWs and repository construction.
2. Every business table has non-null `tenant_id`, composite unique keys and
   composite foreign keys that prevent cross-tenant references; repositories
   have no unscoped `find_by_id` business method.
3. Forced PostgreSQL RLS is enabled consistently with revision 0001.

Runtime transactions set `app.current_tenant_id`. The worker's existing global
discovery role retains `BYPASSRLS` only for claims/publication and every
subsequent business query still includes tenant predicates. This preserves the
known TD-002 risk rather than silently claiming least privilege. Replacing it
with a security-definer claim function or managed queue remains tied to TD-001/2
before production.

Unknown and cross-tenant IDs return the same not-found behavior, preventing IDOR
existence disclosure.

## Audit strategy

New audit records always include tenant, actor, category, target, timestamp,
correlation ID, causation ID and schema version. Metadata contains controlled
codes, counts, versions and hashes only. It excludes full phone numbers, message
content, prompts, raw provider responses, secrets and chain-of-thought.

Database grants and a trigger make the log append-only. Idempotency conflicts are
committed before the application returns an error. Publication acknowledgement
also appends an audit event.

## Outbox strategy

Outbox creation shares the exact state-change transaction. Events have a stable
key, versioned minimal payload, correlation/causation IDs, due time, lease,
attempt count, summarized error and terminal dead state. The existing worker runs
a bounded publisher with exponential backoff and an injected sink; the default
sink records safe structured publication metadata because external messaging is
outside scope.

The event set is limited to the documented operational/audit stream:
`ConversationMessageIngested`, `TelecomExtractionAccepted`,
`LeadProfileUpdated`, `QualificationEvaluationCreated`,
`LeadClassificationChanged`, `DoNotContactRegistered`, `HandoffRequested` and
`ConversationAutomationPaused`.

## Migration strategy

One additive revision follows `20260731_0001`. It creates PKs, composite FKs,
checks, indexes, partial unique constraints, RLS policies, grants and append-only
triggers. It contains no tenant or production data. Downgrade removes only
Delivery 3 objects/alterations. Tests cover empty upgrade, direct upgrade from
0001 with retained data, downgrade to 0001 and re-upgrade.

## Risks and controls

| Risk | Control |
|---|---|
| Holding locks during provider latency | Durable lease; provider call outside SQL transaction |
| Stale extraction after concurrent change | Lead/conversation version fencing and final recheck |
| Duplicate provider call after crash | Allowed; durable application/evaluation remains at most once |
| DNC races with scoring/handoff | Conversation serialization, final recheck, DNC cancellation and absolute state |
| Cross-tenant relation | Composite FKs, tenant UoW, predicates, forced RLS and tests |
| Mutable historical score/audit | Append-only triggers plus insert-only grants |
| JSON becoming an untyped profile | Typed profile history rows; JSON only for bounded snapshots |
| Worker global privilege | Existing documented TD-002; no expansion to runtime role |
| Wrong scoring policy | Tenant/opportunity provider validation and persisted version/fingerprint |
| Raw sensitive data in telemetry | No content/high-cardinality metric labels; controlled audit payloads |
| Outbox redelivery | Stable event key/ID and consumer idempotency contract |

## Applicable ADRs

- ADR-016: keep Python 3.12, SQLAlchemy Core, Alembic and Psycopg.
- ADR-017: reuse PostgreSQL leases/`SKIP LOCKED`; still provisional.
- ADR-018: deterministic scoring and tenant/opportunity policy remain exclusive.
- ADR-019: extraction remains an untrusted provider boundary.
- Delivery 3 will add focused ADRs for the split processing transaction/UoW,
  durable idempotency/concurrency/outbox, and retaining forced RLS.

## Scope and exclusions

In scope: durable Contact/Lead/Conversation/Message/Profile, provenance,
conflicts, extraction execution, immutable evaluations, DNC, handoff, pending
question, state transitions, idempotency, concurrency, UoW, audit, outbox,
worker, internal normalized adapters, metrics, migrations, tests and retention
documentation.

Excluded: productive WhatsApp/Twilio/webhooks, outbound customer responses,
advisor assignment algorithms, external catalog/inventory/coverage/financing,
RAG, embeddings, UI, complete OIDC/RBAC, policy administration, campaigns,
billing, CRM expansion, external notifications, agents and Delivery 4.

## Test strategy

- Pure unit tests cover commands, hashes, state machines, handoff transitions and
  every incremental-profile decision, including forbidden fields and confirmed
  facts.
- PostgreSQL integration tests cover ingest replay/conflict, tenant isolation,
  atomic rollback, immutable evaluation/history, DNC, handoff, question and
  outbox behavior.
- Real concurrent transactions cover two workers/same message, two messages/one
  conversation, DNC versus scoring, active handoff and optimistic conflicts.
- Migration tests cover base, previous head, downgrade/re-upgrade, constraints,
  indexes, RLS and retained seed data.
- Scripted extraction fixtures cover reference cases A-F without credentials or
  network and assert Delivery 1 remains the scoring authority.
- Final regression runs coverage, Ruff, strict Mypy, build, secret scan, audit,
  PostgreSQL, migrations, API/worker health/readiness/metrics and outbox.

Coverage targets are global `>=95%`, new code `>=90%` and critical transactional
paths `>=95%`.
