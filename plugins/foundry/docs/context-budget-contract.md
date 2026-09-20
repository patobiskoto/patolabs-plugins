# Experimental context-budget and context-pack contract

FOUNDRY-56 adds an opt-in, provider-orthogonal metadata contract under ADR-0009. It
does not change F44 packet admission, F51 frozen artefacts, routing, model selection,
gates, escalation, merge behaviour, provider calls, local-model calls, retrieval, or
ranking. There is deliberately no broker algorithm here.

`ContextBudget` makes these declared partitions explicit: window, reserved output,
system/role, tools, task, working state, and safety margin. The injectable capacity is
the raw `window - every declared partition` result; it is deliberately not clipped to
zero. Both reserve and injected-context overflow therefore remain observable.

`ContextPack` is a versioned snapshot of caller-supplied metadata. Each
`ContextElement` records provenance, SHA-256 digest, reason, retriever source, optional
score, token size, explicit removal, and truncation. Vocabulary fields use controlled
labels. Several controlled strategy labels are accepted without a preferred order or
selection logic pending F52/F53/F54.

The v1 contract is deliberately bounded: a declared context window and each element
size cannot exceed 1,000,000 tokens; a pack contains at most 256 elements and at most
1,000,000 retrieved tokens. A later compatible capacity requires a new contract
version, rather than an invisible limit change. Reservation overflow and injected
context overflow are distinct: a negative capacity is a reservation overflow; only
actual injected tokens can contribute to the latter.

The telemetry projection contains no context body, evidence ID, reason, provenance,
element digest, score, version, snapshot digest, or strategy. It exposes declared and
used budgets, retrieved/injected tokens, aggregate controlled source mix, truncations,
overflows, and nullable cache read/write observations. `null` cache usage means
unavailable, never zero. Explicit removals (count and tokens), like truncations and
budget overflows, are observable rather than silently repaired. Trusted pack inspection
(`inspection()`) retains the version and snapshot digest in an immutable API; it is not a
packet or telemetry representation.

F44 interoperability is metadata-only through `f44_snapshot_digest(policy,
evidence_ids)`. It derives a stable, canonically serialized snapshot digest from the
F44 policy and opaque evidence identifiers without reading F44 fragments or changing
F44's policy contract.
