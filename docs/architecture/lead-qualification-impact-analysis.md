# Lead Qualification Impact Analysis

## Decision

The requested telecommunications qualification capability is broader than one
safe vertical delivery. Following the approved implementation strategy, this
change implements Delivery 1 only: the deterministic qualification domain.
Deliveries 2 through 5 remain explicitly unimplemented.

## Current architecture

The repository contains a modular monolith with two business/technical modules:
`tenancy` and `synthetic_events`. It provides validated configuration, UUIDv7,
PostgreSQL, forced RLS, an outbox/inbox worker, audit rows, structured logs,
Prometheus metrics and an internal development adapter.

There are no modules or data owners yet for conversations, contacts, leads,
campaigns, products, plans, AI providers, prompts, catalogs, coverage,
inventory, financial eligibility or human handoff. The synthetic event is a
walking-skeleton test artifact, not a conversation or lead aggregate.

## Reuse

- `TenantId` and mandatory tenant resolution protect policy ownership.
- Framework-independent errors preserve domain/transport separation.
- UUIDv7 and clock ports can identify and timestamp evaluations.
- Existing RLS, audit, outbox and worker patterns are available when a future
  persisted Lead aggregate has an approved owner and transaction boundary.
- Existing CI, type checking, security scanning and test structure apply.

## Affected architecture

A new `lead_qualification` business module owns the scoring vocabulary and
calculation. It has no dependency on FastAPI, SQLAlchemy, Prometheus or an AI
SDK. The application layer resolves a published scoring policy by tenant and
primary opportunity type through a small port, then delegates to a pure domain
engine.

No existing module is rewritten. No current table is reused to store lead data.
No endpoint is added because there is no approved authenticated Lead or
Conversation resource yet.

## Data impact

Delivery 1 introduces immutable in-memory/domain representations for:

- lead profile snapshots and facts with provenance;
- primary and secondary telecommunications opportunities;
- normalized commercial signals with confidence and validation state;
- tenant/opportunity-scoped scoring policy versions;
- dimension scores, contributions, penalties and decisions;
- qualification results containing policy identity and fingerprint;
- financial eligibility copied from an authorized source, never inferred.

There is no database migration. A later persistence migration must follow the
ownership boundaries approved with the conversational integration and preserve
all policy versions plus evaluation history.

## Risks and mitigations

| Risk | Impact | Mitigation in Delivery 1 |
|---|---|---|
| Generic CRM expansion | Unbounded domain and CRUDs | Only telecom qualification concepts are added |
| AI determines score | Non-reproducible or biased decisions | Engine consumes normalized signals; policy alone awards points |
| Double counting | Inflated intent/budget/urgency | Exclusive rule groups select one strongest contribution |
| Tenant policy leakage | Cross-company decisions | Provider and engine both validate tenant and opportunity |
| Historical drift | Old scores change after edits | Result stores policy ID, version and canonical fingerprint |
| Financial overclaim | False approval statements | Eligibility is a separate supplied status |
| Protected-attribute scoring | Legal/ethical harm | Policy conditions only accept an allowlisted commercial signal vocabulary |
| Premature persistence | Wrong ownership/contracts | No Lead tables until conversation/contact boundaries are approved |

## Delivery plan

1. Delivery 1, implemented now: domain, policy, deterministic engine,
   explanation, application use case and tests.
2. Delivery 2: structured AI extraction schemas, prompts, confidence and provider
   adapter. AI output remains untrusted input to Delivery 1.
3. Delivery 3: conversation/contact/lead ownership, incremental updates, next
   question, state transitions, idempotency and handoff workflow.
4. Delivery 4: persisted policy drafts/publication, tenant administration and
   historical evaluation storage with RLS.
5. Delivery 5: read models, administration UI, business metrics and calibration.

## Acceptance for this delivery

- Scores are clamped to 0..100 and dimensions to their configured maxima.
- Weights total 100 and classification bands cover 0..100 without gaps.
- A policy is published, versioned, tenant-bound and opportunity-bound.
- Identical input and policy produce the same commercial outcome.
- Related intent/budget/urgency signals cannot be summed automatically.
- Penalties, offer blocks and do-not-contact are distinct effects.
- Handoff may be forced independently of score.
- Eligibility is not calculated from commercial score.
- Result explanation contains contributions, penalties, missing information,
  decision rules and the exact policy reference.
- Reference cases A through E and tenant/version isolation are covered by tests.
