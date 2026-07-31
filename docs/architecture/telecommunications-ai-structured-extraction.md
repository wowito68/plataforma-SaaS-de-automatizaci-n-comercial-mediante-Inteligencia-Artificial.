# Telecommunications AI Structured Extraction

## Purpose

Delivery 2 converts a bounded set of telecom conversation messages into typed,
validated and explainable commercial evidence. The language model proposes; the
application validates; Delivery 1 alone scores.

Responsibilities include all 11 existing opportunities, product/device, plan,
portability/line and business fields, intent, urgency, signals, special controls,
missing information, contradictions, a commercial summary, one next question and
a suggested handoff.

It excludes durable Lead/Conversation storage, WhatsApp, customer responses,
state transitions, advisor assignment, catalogs, coverage, inventory, financing,
RAG, tools, agents, multiple providers, UI and policy administration.

## Architecture and flow

```mermaid
flowchart LR
    M[Messages + existing LeadProfile] --> C[Context selection + redaction]
    C --> P[CommercialExtractionProvider]
    P --> O[Provider structured result]
    O --> S[Strict schema validation]
    S --> V[Semantic authority validation]
    V --> N[Normalization]
    N --> F[Confidence resolution]
    F --> R[Contradiction reconciliation]
    R --> A[Accepted extraction]
    A --> U[Immutable profile update proposal]
    U --> Q[QualificationRequest]
    Q --> D[Delivery 1 deterministic scoring]
```

The stages are explicit:

| Stage | Type | Trust |
|---|---|---|
| Provider | `ProviderExtractionResponse` | Untrusted structured payload and bounded metadata |
| Validated | `ValidatedExtraction` | Strict syntax/enums/bounds only |
| Normalized | `NormalizedExtraction` | Units, references and authority boundaries checked |
| Accepted | `AcceptedExtraction` | Local confidence, conflicts, missing data, question and handoff resolved |
| Final | `TelecomExtractionResult` | Update proposal, `QualificationRequest`, audit, usage and failures |

## Port and adapters

`CommercialExtractionProvider.extract_commercial_data` accepts only normalized
messages, allowed profile context, versions, expected language, correlation and
idempotency metadata. It exposes no SDK type.

`OpenAICommercialExtractionAdapter` uses the official synchronous Responses API:

- one call with `responses.parse` and `ProviderExtractionPayload`;
- model configurable, default `gpt-5.6-terra`;
- non-streaming, low reasoning effort and `store=False`;
- SDK `max_retries=0`; timeout at client construction;
- token usage, model, latency and completion status returned;
- optional cost estimate only when rates are configured.

`ScriptedCommercialExtractionAdapter` returns queued results/errors and records
requests. Application and reference tests never require internet or credentials.

## Strict schema

`ProviderExtractionPayload` and every nested model use strict Pydantic types and
`extra=forbid`; OpenAI's generated strict JSON Schema has
`additionalProperties=false`. All object properties are required, with nullable
values where absence is meaningful.

Each commercial field includes typed scalar value, original text, source
message, evidence, observed/inferred/unknown state, confidence and optional
`MXN`/`USD` currency. Opportunity, intent, urgency, controls and handoff include
their own evidence, source and confidence. Lengths, lists, quantities, dates and
confidence are bounded.

There are deliberately no schema properties for score, weights, thresholds,
eligibility, financing approval, state transition or policy. Coverage/inventory
fields and compatibility/availability authority signals are rejected during
semantic normalization even though Delivery 1 knows those types for authorized
system lookups.

## Prompt strategy and versions

MVP uses one call for latency, cost and operational simplicity. Six definitions
share `telecom-extraction.v1` and schema `telecom-extraction-result.v1`:

1. opportunity classification;
2. structured fields and commercial signals;
3. contradiction detection;
4. special controls;
5. commercial summary;
6. next-question proposal.

Each definition records identifier, purpose, variables, restrictions, minimal
example and update policy. A changed behavior requires a new immutable version
and regression fixtures. One schema-repair retry is allowed after invalid output;
it repairs structure only and may not invent facts. Transient retries do not use
the repair instruction.

Messages are serialized as JSON data under instructions that forbid obeying
customer attempts to change score, permissions, policy, tenant, eligibility or
internal prompts. No chain-of-thought is requested or stored.

## Context and privacy

`ExtractionContextPreparer` enforces one tenant, sorts chronologically and sends
only the latest relevant messages under configurable count, character and age
limits. If all are old, the newest message is retained. Previous summary has a
separate bound. Email, phone, RFC/CURP-like identifiers are redacted.

Allowed context contains current opportunity, non-sensitive profile facts with
validation/provenance, and open conflict field names. It excludes tenant IDs,
coverage, inventory, customer financing claims, policies, secrets,
administrative data and unrelated documents.

## Validation and normalization

Validation order is fixed:

1. JSON and strict schema;
2. every evidence message belongs to the selected tenant context;
3. currency appears only with money and is explicit;
4. quantities/terms are non-negative integers;
5. booleans and ISO dates have their expected types;
6. authoritative coverage/inventory/compatibility signals are removed;
7. whitespace, case, brands, currency decimals and dates are normalized;
8. authoritative claims embedded in the summary are replaced with neutral text.

Unknown or invalid output never updates a profile.

## Confidence and provenance

Default thresholds are centralized and configurable:

| Final confidence | Treatment | Profile/scoring effect |
|---:|---|---|
| `>=0.85` with observed evidence | Autoaccept | `accepted` fact; accepted signal may reach scoring |
| `>=0.65` | Provisional | `hypothesis` fact; signal excluded from scoring |
| `>=0.40` | Confirmation required | Retained as rejected-stage evidence; ask when relevant |
| `<0.40` | Rejected | No profile or scoring effect |
| Contradicted | Contradicted | Preserve both versions; no silent replacement |

The resolver adjusts model confidence for verbatim evidence, inferred/unknown
state, repeated support and contradiction. Intent, urgency, controls and handoff
also require matching evidence; only autoaccepted intent/urgency create Delivery
1 signals.

Every accepted fact retains field, normalized value, original text, source
message, evidence, AI method, final confidence, observation time and validation
status. The profile keeps source and confidence; the full in-memory result/audit
keeps original text and evidence until durable ownership is designed.

## Contradictions and controlled update

Different values for the same field in selected messages, or a different value
against the existing profile, produce `ExtractionContradiction`. It records both
values/source/evidence where available, confidence, conservative resolution and
one clarification question. The existing fact remains active. A manually
confirmed value is explicitly protected.

`ExtractionDomainMapper` creates a new immutable `LeadProfile` and a
`QualificationRequest`. Autoaccepted facts become `accepted`; provisional facts
become `hypothesis`; contradicted/low values do not replace facts. Only accepted
signals are mapped. Caller-supplied eligibility is copied unchanged. The mapper
has no field through which provider score, policy or state can pass.

The mapper does not execute scoring. A caller may pass the resulting
`QualificationRequest` to `EvaluateLeadQualification`, preserving the Delivery 1
authority boundary.

## Missing information and next question

Relevant required fields are defined per opportunity. Device, plan, portability,
multiple-line and business flows use different sets; irrelevant fields are not
marked missing.

The provider may propose one question, but local selection requires its target
to be the highest-priority unresolved conflict/missing field and not recently
asked. It rejects content over 180 characters, multiple questions and requests
for phone/email/password/payment-card/government identifiers. A safe deterministic
template replaces an invalid proposal. No question is returned after DNC, an
explicit human request or `handoff_confirmed=true`.

## Controls and handoff

Local phrase detection runs before the provider for unequivocal DNC, human
request and prompt injection. It is intentionally retained through invalid
output, timeout or outage. Provider controls require exact source evidence and
high confidence.

DNC suppresses question and handoff and maps to the existing `DO_NOT_CONTACT`
signal. Human request suggests general handoff. Fraud/complaint and business or
volume requests suggest specialized handoff. Urgent portability suggests general
handoff. A provider suggestion is accepted only with evidence and high confidence.
These remain suggestions; real assignment/pausing is Delivery 3.

## Errors and fallback

SDK exceptions map to internal timeout, rate-limit, temporary, authentication,
permanent, refused, empty, invalid and incomplete errors. No SDK exception crosses
the port. Temporary and invalid-schema failures may retry once with exponential
backoff; permanent failures do not. The same idempotency key is reused.

After exhaustion, the result is `degraded`, the existing profile remains intact,
failures are explicit, and no extraction is invented. A locally detected DNC or
human request remains effective.

## Idempotency and audit

SHA-256 covers tenant, conversation, ordered selected message IDs, prompt version,
schema version and operation. New messages or versions produce a new key; retry
and duplicate input produce the same key. This is conceptual only: Delivery 3
must persist execution state and apply profile changes atomically.

`ExtractionAuditRecord` can reconstruct considered message IDs, versions,
provider/model, raw structured payload in memory, validations, accepted/rejected
fields, contradictions, proposed updates, handoff reasons and normalized failures.
It stores neither chain-of-thought nor hidden model reasoning.

## Observability and configuration

Prometheus records lifecycle outcomes, errors (including timeout/rate limit),
schema repairs, duration, confidence, accepted field count, contradictions, DNC,
handoff, input/output tokens and optional cost. Labels are limited to controlled
provider, model, prompt and outcome/error values. Tenant, conversation, message,
phone and content are never labels.

JSON logs include request/correlation/tenant context plus execution, provider,
model, prompt/schema, duration, outcome, error type and tokens. They never include
API key, prompt, message content or raw structured payload.

AI is disabled by default. `OPENAI_API_KEY` is required only when enabled.
Production rejects content logging. Context limits, timeout, retry count, token
limit, confidence and optional rates live in `Settings`. Temperature is omitted
because the selected reasoning model and strict parse path do not require it.

## Reference behavior

| Case | Accepted extraction |
|---|---|
| A | Portability + device/plan, keep number, 600 MXN, Samsung, financing preference, this week, handoff |
| B | Device exploration, no invented budget/brand, relevant missing fields, one question |
| C | Galaxy S25 Ultra and immediate intent; inventory remains unknown |
| D | Business account, 40 lines, devices, next month, specialized handoff |
| E | DNC, no question, no handoff; survives invalid provider output |

Additional fixtures cover ambiguity, budget contradiction, intent change, human
request, prompt injection, customer financing claim and assumed stock/coverage.

## Persistence and debt

No migration is created. An in-memory result correctly validates the capability,
while a table now would invent Lead/Conversation ownership, retention and replay
semantics. Delivery 3 must add tenant-scoped execution/dedupe records, incremental
profile versions and atomic application with RLS. Live-provider quality/cost
calibration is also pending before production traffic.
