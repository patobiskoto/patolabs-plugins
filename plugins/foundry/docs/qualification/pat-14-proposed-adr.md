# Proposed ADR — bounded Codex GPT-6 qualification and promotion

> PAT-ADR-0010: proposed in Linear, awaiting explicit human approval.
> This file is a review copy; Linear remains authoritative.

## Context

The accepted mappings in ADR-0006/0008 and promotion policy ADR-0019 remain
authoritative. PAT-15 requests a bounded Codex-only migration before V1 while
Claude and full cross-host qualification remain post-V1.

## Decision

Amend FOUNDRY-ADR-0006/0008 and the promotion prerequisite in ADR-0019 only enough to permit PAT-14's Codex candidates:
Luna/low economy, GPT-6.1 Sol/medium balanced, Sol/high frontier and Sol/max
apex. Defaults remain GPT-5.6 until deterministic checks, bounded host smoke,
independent review, exact-SHA CI and per-host rollback have passed. Native qualification is bounded to four subscription-included smoke sessions,
one per tier, twenty minutes each, zero qualification retries and zero
API/additional credits. Explicit host identity, preserved floors and structured
results are required. Unknown subscription savings are documented, not inferred.
This urgent V1 technical-promotion exception to ADR-0019 permits qualified
Codex promotion without an economic benchmark before M4/PAT-17; it does not
promote Claude or close PAT-17.

ADR-0019 governs an ordinary role-local comparison. A candidate proven cheaper
uses its no-extra-benchmark regime; an equal, unknown, or dearer candidate needs
a pre-registered comparison and signed campaign authority. The proposed but
unauthorized envelope is Codex only: 10 USD total, 2 USD per call, one attempt
per pair, expiry 24 h after signature. `gpt-6-astra` is a motivated bounded comparison
or escalation only, never a default. ADR-0020--0022 apply only to the distinct
FOUNDRY-140 pipeline.

No routing/default change, automatic promotion, authority expansion, `ultra`,
Claude change or paid invocation is authorized by this proposal. Unknown price or quota prohibits an economic-gain claim. Unknown executed
identity, floor compliance or outcome retains the holder. Paid benchmark
authority remains separate and absent.

## Consequences

The change may proceed only after explicit approval of this decision and the
PAT-14 contract. It introduces no new routing authority, automatic promotion,
paid-trial authorization or guarantee of subscription savings. Historical
proofs retain their original source SHA and telemetry.

## Alternatives rejected

Changing defaults from a model announcement alone lacks execution evidence.
Requiring full cross-host PAT-17 before the urgent Codex change contradicts
the explicitly scoped V1 sequencing. Replacing pinned old configurations
silently violates compatibility.
