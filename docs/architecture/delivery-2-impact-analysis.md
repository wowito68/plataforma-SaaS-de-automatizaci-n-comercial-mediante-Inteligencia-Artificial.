# Delivery 2 Impact Analysis

## Decision

Delivery 2 adds only structured AI extraction for telecommunications. It turns a
bounded conversation fragment into validated proposals that can feed Delivery
1. It does not add a Conversation or Lead aggregate, WhatsApp, a public API,
durable extraction storage, state transitions, catalogs, or scoring rules.

The Delivery 1 scoring engine remains unchanged and is still the only component
that awards points, applies policy effects, classifies a lead, or recommends a
state.

## Affected modules

| Area | Impact |
|---|---|
| `modules/telecom_extraction` | New capability: context, port, schema, prompt, processing, mapper and adapters |
| `modules/lead_qualification/domain.py` | Extends only `ProfileField` with requested telecom vocabulary |
| `config.py` / `.env.example` | Adds opt-in provider, limits, confidence and cost settings |
| `observability.py` | Allows bounded extraction metadata in JSON logs |
| Documentation and tests | Adds architecture, ADR, report, reference and adversarial coverage |
| Scoring policy/engine | No change |
| Database/API/worker | No change |

## Reused components

- `TenantId` as the mandatory isolation boundary.
- `LeadProfile`, `ProfileFact`, `FactConflict`, `CommercialSignal`,
  `OpportunityType`, `ProfileField` and `QualificationRequest` from Delivery 1.
- `EligibilityStatus` as caller-owned input, never provider output.
- `Clock`, `IdGenerator`, application errors, structured logging and Prometheus.
- The existing strict configuration, uv lock, Ruff, Mypy and Pytest conventions.

## New components

- `CommercialExtractionProvider`, a provider-neutral capability port.
- Explicit provider, validated, normalized, accepted and final result stages.
- A strict Pydantic structured-output schema with unknown fields forbidden.
- Six versioned prompt responsibilities compiled into one provider call.
- Context selection, PII redaction and deterministic DNC/injection safeguards.
- Semantic normalization for currency, quantities, booleans and dates.
- Central confidence resolution, contradiction reconciliation and missing-field rules.
- Controlled next-question and handoff validation.
- `ExtractionDomainMapper`, which proposes an immutable profile update and creates
  a `QualificationRequest` without invoking or modifying scoring.
- OpenAI Responses API and deterministic scripted adapters.
- In-memory audit record, stable conceptual idempotency key and bounded metrics.

## Dependencies

| Dependency | Purpose | Alternatives | Reason | Risk |
|---|---|---|---|---|
| `openai>=2.45,<3` | Official Responses API structured-output adapter | Raw HTTP, another provider SDK | Official typed client, supported `responses.parse`, normalized SDK errors | Provider/API and major-version coupling isolated behind the port |
| `pydantic>=2.13,<3` | Strict provider schema and JSON Schema generation | Dataclasses plus manual validation, `jsonschema` | Already foundational through Settings; typed bounds and `extra=forbid` | Major-version schema behavior changes |

No agent framework, RAG library, workflow engine, broker, database or second AI
SDK is introduced.

## Risks and controls

| Risk | Control |
|---|---|
| Model assigns score or eligibility | Schema has no such fields; extras fail; mapper accepts caller eligibility only |
| Fake coverage/inventory | Fields and authoritative signals are blocked semantically |
| Prompt injection | Messages are JSON data under immutable instructions; local detector records manipulation |
| Cross-tenant evidence | Command validates every message tenant; provider may reference only selected message IDs |
| Overwrite confirmed fact | Reconciler preserves it and creates a conflict requiring clarification |
| Confidence fabrication | Evidence, state, support and contradictions adjust declared confidence locally |
| False DNC negative during provider failure | Explicit local phrase detection runs before the provider and survives degraded mode |
| Repeated effects | Stable key covers tenant, conversation, messages, prompt, schema and operation |
| Sensitive logging | No prompts/messages/raw payloads are logged; IDs are not metric labels |
| Provider outage/cost | Timeout, one bounded retry, explicit degraded result, token/cost telemetry |

## ADRs

- ADR-018 remains authoritative for deterministic scoring.
- ADR-019 records the new structured-output, one-call, provider-port and
  no-persistence decision.
- ADR-016 remains provisional for the Python stack.

## Scope and exclusions

Implemented: the 11 opportunities, requested product/plan/portability/business
fields, signals, controls, provenance, confidence, contradictions, relevant
missing information, one next question, suggested handoff, DNC, OpenAI and
scripted adapters, audit, telemetry and mapping to Delivery 1.

Excluded: durable Lead/Conversation/extraction records, webhook or messaging
flows, response sending, real handoff assignment, state machine, financing,
coverage/inventory/catalog integrations, tenant administration, UI, RAG,
agents, tools and multiple providers.

## Test strategy

- Strict schema tests reject unknown fields, invented score/eligibility, invalid
  enums/types/ranges/currency, excessive text and missing provenance.
- Pure processing tests cover normalization, confidence bands, confirmed facts,
  contradictions, relevant missing fields, question safety and mapper authority.
- Scripted application tests cover A-E, ambiguity, intent change, human request,
  prompt injection, financial claims, fake stock/coverage and every fallback.
- OpenAI adapter tests use SDK response/error types offline; no key or network.
- Full regression includes PostgreSQL, API/worker health, metrics, build, lint,
  typing, secret scan and dependency audit.
