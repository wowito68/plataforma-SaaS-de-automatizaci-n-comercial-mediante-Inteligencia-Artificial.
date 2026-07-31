# ADR-018: Deterministic telecommunications scoring policy

- **Status:** Accepted for Lead Qualification Delivery 1
- **Context:** Commercial conversations will eventually be interpreted by AI,
  but qualification must be explainable, auditable, tenant-specific and
  reproducible. The repository has no Lead or Conversation aggregate yet.
- **Options:** Let the language model assign a score; adopt an enterprise rules
  platform; implement a small deterministic domain policy; postpone all work.
- **Decision:** Implement a pure domain scoring engine driven by immutable,
  validated `ScoringPolicy` versions. AI may later emit normalized, confidence-
  rated signals but cannot emit the authoritative score. Related rules use
  exclusive groups and every result records policy ID, version and fingerprint.
- **Policy ownership:** Policies are scoped to one tenant and one primary
  opportunity type. A provider port resolves the published version. Persistent
  publication and administration belong to Delivery 4.
- **Eligibility:** Financial eligibility is an independent supplied value and is
  never derived from score.
- **Dependencies:** Python standard library and existing domain primitives only;
  no third-party rule engine or AI SDK.
- **Consequences:** Rules remain transparent and testable. New normalized signal
  vocabulary requires deliberate domain review, while points, caps, thresholds,
  penalties and decisions can vary through policy instances without changing
  the engine.
- **Risks:** Until Delivery 4, policy persistence/publication is only a port and
  results are not stored. Until Delivery 2, signals are supplied by tests or
  trusted adapters rather than extracted from conversations.
- **Review:** Before implementing policy persistence, AI extraction or the first
  production tenant configuration.
