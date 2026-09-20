# Foundry 0.8.0 release notes

Release decision: **publish the shared Claude Code/Codex package, including structured
AC proofs and the FOUNDRY-46/FOUNDRY-47 evidence, with production disposition `keep`.** Foundry 0.8.0
does not activate a new resolver or local candidate and does not change any production
model mapping, reasoning effort, context policy, route, default, gate, fallback,
escalation signal, capability, or authority.

The two package manifests report exactly `0.8.0`. The Claude and Codex catalogues keep
their supported, versionless source-pointer schemas and both resolve to the same
`plugins/foundry` implementation, as required by FOUNDRY-ADR-0005.

## Runtime addition: structured AC proof (FOUNDRY-39)

The pre-merge reviewer now returns a separate structured object containing ordered AC
outcomes and one quality verdict. `routing record-review-proof` validates and atomically
binds it while the review claim is active to:

- every current issue AC id and digest, in order;
- local `HEAD`, the exact base ref, and the exact base diff hash;
- the reviewer role, active claim capability, and current recovery generation;
- all-AC `pass` plus `quality=mergeable` for a proof eligible to satisfy the merge gate.

Stale, malformed, mixed, incomplete, legacy, recovered-owner, or changed-diff evidence
fails closed. The proof identifier is redacted and the private append-only record is not
tracker progress: it never checks boxes or rewrites AC text. The explicit
`--allow-incomplete-ac --ac-override-reason=<public-audit-code>` human path remains
available and audited. This changes the AC evidence consumed by merge; it does not alter
CI, review quality, human-test, or merge-SHA gates.

## Recommended main profile and unchanged escalation contract

For ordinary feature, fix, and chore coordination, the recommended main profile remains:

| Host | Recommended main profile | Frontier reviewer floor | Architect floor |
|---|---|---|---|
| Claude Code | Sonnet 5 / medium | Opus 5 / high | Fable 5 / high |
| Codex | GPT-5.6 Terra / medium | GPT-5.6 Sol / high | GPT-5.6 Sol / max |

This is operator guidance. Foundry can resolve delegated roles, but **cannot force the
model or effort of the already-open main conversation**. Move an issue-scoped role to
frontier only for an existing deterministic escalation signal or explicit disclosed
risk; architect remains reserved for explicit architecture work. Reviewer never falls
below frontier/high and architect never below apex/high. The existing maximum of two
tier increases per issue remains shared across both hosts; exhaustion requires a human
and does not grant extra tools or authority.

FOUNDRY-ADR-0006 mappings and fallbacks remain normative. FOUNDRY-ADR-0008 keeps model,
effort, and context as separate observable dimensions, but observation is not activation.

## FOUNDRY-46 v2 benchmark evidence, separate from activation

The frozen F61 v2 campaign contains 108 exact coordinates. Its reports are evidence
artifacts, not runtime inputs: they do not control the main conversation, resolver,
fallback, local provider, gate, or any later invocation.

| Published scope | Claude | Codex | Weighted cross-host aggregate |
|---|---:|---:|---:|
| Attempts | 60 | 48 | `null` |
| Host terminal counts | 0 complete / 40 failed / 20 unknown | 6 complete / 7 failed / 35 unknown | `null` |
| Host version observed | `null` | `null` | `null` |
| Cumulative/estimated/provider cost | `null` | `null` | `null` |
| Main-loop/subagent allocation | `null` | `null` | `null` |
| Tests before/after, review, downstream success, security regression | `null` | `null` | `null` |
| Host verdict | `inconclusive` | `inconclusive` | forbidden without frozen weights |
| Production disposition | `keep` | `keep` | `keep` |

The six Codex terminal completions are not downstream product successes. The Claude and
Codex host versions were unobserved, so diagnostics are not attributed to the expected
frozen runtime contracts. Missing cost, allocation, test, review, downstream, security,
or aggregation evidence is `null`, never zero. Comparisons remain within one host/family/
policy scope; ordinal effort and raw host counts are not cross-provider units.

The historical **7.42%** result came from an older fixed-token/fixed-invocation price
snapshot. It is noncausal, excludes the complete cache/retry/correction/escalation and
main-loop allocation boundary, and is not a savings, cost-reduction, or performance
claim for 0.8.0.

## No local promotion

FOUNDRY-ADR-0007 keeps local preprocessing optional, disabled by default, untrusted,
evidence-bound, and non-privileged. No local model becomes a host, role, tier, reviewer,
architect, gate, fallback owner, or source of product authority in 0.8.0.

A future local recommendation is prohibited unless a candidate-agnostic, versioned
protocol identifies the exact model/checksum and quantization, runtime/version, hardware,
prompt/template, limits, repetitions, and parameters; executes the real end-to-end
wrapper-to-cloud pipeline; preserves provenance and adversarial safety invariants; and
meets frozen downstream success, correction, security, and complete cumulative-cost
thresholds. Any unavailable metric remains `null`. Even passing evidence could recommend
an exact operator-managed profile only; activation or a changed default additionally
requires an accepted ADR. A live success, intrinsic score, historical price estimate,
unobserved runtime, missing downstream result, or degraded gate always means no promotion.

## Upgrade, verification, and rollback

Use [`migration-0.8.0.md`](migration-0.8.0.md) for the tested Claude Code and Codex
command contracts: marketplace/package upgrade, reload/new-task boundary, shared
`foundry:configure`, read-only `foundry:doctor`, value-free host-override diagnosis,
manifest/source-revision verification, and exact-ref rollback. Source-development
cachebuster instructions remain development-only; this release does not edit or reinstall
an installed cache.

## Evidence index and limits

- FOUNDRY-ADR-0005: one implementation, two host packages.
- FOUNDRY-ADR-0006: unchanged semantic mappings, floors, fallbacks, and escalation.
- FOUNDRY-ADR-0007: local provider remains non-privileged and promotion-gated.
- FOUNDRY-ADR-0008: observation remains separate from activation; unknown is not zero.
- [`release-0.8.0-decision.md`](release-0.8.0-decision.md): executable F47 `keep`
  decision and promotion requirements.
- [`REPORT-v2.md`](../benchmarks/foundry-46/report-v2/pilot-20260825/REPORT-v2.md) and
  [`results-v2.json`](../benchmarks/foundry-46/report-v2/pilot-20260825/results-v2.json):
  frozen per-host evidence and nullable fields.
- [`model-routing-pilot-results-v1.md`](model-routing-pilot-results-v1.md) and
  [`model-routing-pilot-v1.md`](model-routing-pilot-v1.md): the historical 7.42% price
  snapshot and its non-causal boundary.
- [`release-0.7.0.md`](release-0.7.0.md) and
  [`migration-0.7.0.md`](migration-0.7.0.md): immutable historical 0.7.0 publication
  evidence, not rewritten as 0.8.0 results.
