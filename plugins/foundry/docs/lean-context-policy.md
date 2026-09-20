# Lean-context policy contract

FOUNDRY-44 defines two shared, versioned policies: `baseline-v1` and `lean-v1`.
Both collect the allowed sources in this exact order: diff, focused tree, symbols,
imports, references, literal `rg` results, tests, then logs. Baseline permits eight
items and 8 KiB aggregate per source (48 KiB total); lean permits four items and 2 KiB
aggregate per source (12 KiB total). Selection sorts each source by controlled evidence
ID and truncates at the aggregate per-source then global cap. Shipped policy maps are
immutable, and only their registered object identities or exact known string identifiers
can be used to build a packet. Equal-but-distinct public policy values never carry packet
authority, and public policy construction snapshots its source map into an immutable map.
Every fragment remains strict UTF-8 after either cap is applied; a cap that intersects a
multi-byte code point backs up to the preceding complete point without exceeding its byte
budget.

The policy is observation-only. It does not select a route, model, effort, fallback,
gate, or escalation. Local output remains untrusted: `capture_context()` accepts only
the existing registered `DiffCapture`, `CodeManifest`, and `CodeEvidenceBundle`
artifacts, whose wrapper provenance and snapshot checks have already passed. A code
bundle must match the exact registered manifest digest and still pass its snapshot
freshness verifier; merely sharing a root is insufficient. Multi-signal manifest entries
expand to each supported source in canonical order with source-derived opaque IDs. A public
dataclass or a string marker cannot designate arbitrary bytes as materialized.

`ContextPacket` and its telemetry projection contain policy/version, opaque evidence
IDs, counts, truncation, collected bytes, sent bytes, files read, and compression only.
They never contain raw excerpts, code, paths, digests, commands, host output, or user
data. Packets, nested metric maps, and telemetry projections are registered immutable
values; reconstructed or mutated public values are rejected. The separate `cloud_fragments(packet)` capability returns only the registered
fragments selected by that exact packet, after per-source and global truncation. Raw
wrapper material stays inspectable through the originating `ContextCapture`, never via
the packet; consequently a cloud sender cannot use a packet to retrieve a larger raw
body than its declared budget.

For a cacheable Claude session, the declared reasoning effort is immutable. Cache-read
and cache-creation tokens are separate nullable observations; unavailable is never
recorded as zero. `build_host_context()` is the shared offline Claude/Codex façade: both
hosts apply the same packet policy and fragments, while Claude may attach those cache
observations and Codex keeps the Claude-specific metrics explicitly unavailable. The
façade never invokes either host or a local model. This policy contract is host-neutral
and leaves ADR-0006 routing and all reviewer/architect gates unchanged.
