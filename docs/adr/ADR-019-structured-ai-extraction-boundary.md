# ADR-019: Structured AI extraction as an untrusted capability boundary

- **Status:** Accepted for Telecommunications Delivery 2
- **Context:** Delivery 1 needs typed commercial evidence from free-form
  conversations, while scoring, eligibility, coverage, inventory and confirmed
  profile data must remain authoritative outside the language model. There is no
  durable Lead or Conversation owner yet.
- **Options:** Let the model return a complete qualification decision; use an
  agent framework; make several specialized model calls; use one strict
  structured call behind a small port and validate it locally; postpone AI.
- **Decision:** Use one non-streaming structured extraction call for the MVP.
  Six versioned prompt responsibilities compile into one immutable instruction.
  The provider implements `CommercialExtractionProvider` and returns a
  provider-neutral payload plus metadata. The application independently applies
  strict syntax, semantic authority, normalization, confidence, contradiction,
  next-question and mapping rules before Delivery 1 sees any signal.
- **Provider:** The first adapter uses the official OpenAI Responses API with a
  configurable model, `responses.parse`, `store=False`, SDK retries disabled,
  timeout and one application-controlled retry. Offline tests use a scripted
  adapter and SDK fakes.
- **Authority:** Provider schemas contain no score, policy, eligibility, state
  transition, coverage confirmation or inventory confirmation. Unknown fields
  fail. Confirmed facts cannot be replaced. DNC is rechecked locally and has
  priority even in degraded mode.
- **Confidence:** Declared confidence is adjusted using source evidence,
  observed/inferred state, repeated support and contradictions. Only
  autoaccepted signals enter `QualificationRequest`; provisional facts remain
  hypotheses and low-confidence values are rejected.
- **Persistence:** Keep the result and audit record in memory for Delivery 2.
  Generate a stable conceptual idempotency key, but defer durable deduplication
  and application to the Lead transaction until Delivery 3 defines ownership,
  retention and RLS.
- **Consequences:** The provider is replaceable without a generic AI framework;
  schema and prompt versions are auditable; latency/cost stays near one call.
  Domain code does not import the SDK. A retry after invalid output is a second
  call and uses a bounded schema-repair instruction.
- **Risks:** No live-provider accuracy or cost calibration is claimed. In-memory
  audit and conceptual idempotency do not survive restart. Model/version pricing
  must be configured by operations rather than embedded in code.
- **Review:** Revisit when Delivery 3 adds persistence, before production traffic,
  or when changing provider, prompt/schema major version, retention or data region.
