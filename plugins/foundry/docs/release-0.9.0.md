# Foundry 0.9.0 release notes

Release decision: **publish the shared Claude Code/Codex package, including the Linear
tracker adapter, its versioned ADR Documents, the completed historical ADR import, and
the repository's own cutover to Linear, with production disposition `keep`.** Foundry
0.9.0 keeps every 0.8.x model mapping, reasoning effort, context policy, route, default,
routing gate, fallback and escalation signal unchanged. It adds tracker capabilities
(Linear ADR Documents, the repository tracker marker, the typed AC-override receipt)
described below; the CI gate semantics of FOUNDRY-ADR-0002 are unchanged.

The two package manifests report exactly `0.9.0`. The Claude and Codex catalogues keep
their supported, versionless source-pointer schemas and both resolve to the same
`plugins/foundry` implementation, as required by FOUNDRY-ADR-0005.

## Host migration: same-version updates that did not refresh code

Claude Code and Codex now install `foundry@patolabs` from
`github.com/patobiskoto/patolabs-plugins`. Between 0.8.1 and 0.9.0, some installations
ran `/plugin update foundry@patolabs` or `codex plugin marketplace upgrade patolabs`
against a source that already reported `0.8.1` and therefore did not refresh the
installed code. 0.9.0 is a version bump specifically so that a fresh
`/plugin update foundry@patolabs` / `codex plugin remove foundry@patolabs` +
`codex plugin add foundry@patolabs` cycle actually replaces the installed package on
both hosts, whichever marketplace it currently points at. See
[`migration-0.9.0.md`](migration-0.9.0.md) for the exact dual-host commands, including
the one-time switch away from a former private marketplace if an installation still
points at one.

## Linear tracker adapter: versioned ADR Documents and completed historical import

Building on the fail-closed Linear tracker adapter (issue reads, creation, and
non-replacing relations/comments; existing issue field/state/parent/body replacement
stays typed-unavailable because Linear exposes no atomic anti-overwrite precondition),
0.9.0 completes the ADR half of that adapter:

- Linear stores ADRs as **versioned, witness-bound project Documents** (PAT-22). Each
  surviving slot carries its own version evidence, and Markdown serialization handling
  tolerates Linear's own reformatting of the ADR delimiter and angle-bracket content
  without masking a real alteration; byte-exact source bodies stay verifiable through
  witnesses (PAT-37/PAT-38/PAT-39).
- `plan_adr_batch_qualification` reads back and qualifies every historical ADR Document
  as complete on a non-authoritative probe before a batch import proceeds (PAT-40).
- Every historical ADR record carries an explicit `missing_relations` field: an unknown
  relation family (for example a superseding ADR not itself imported) is a canonical,
  sorted, typed-unavailable tuple, never an omitted or silently-empty value (PAT-23).
- The repository's 27 historical ADRs are imported into Linear under audit, carrying no
  private data (PAT-23).
- `query issue` reports an explicit `conflict` status instead of presenting a
  conflicted embedded ADR index as empty (PAT-41).

**Atomic Epic closure remains unavailable** in the Linear adapter, unchanged from
0.8.x's DevHub/YouTrack posture: no adapter emulates it with a racy read/write.

## This repository is cut over to Linear project PAT

`plugins/foundry` (this repository) is itself cut over from YouTrack to Linear project
`PAT` (PAT-10):

- A repository-scoped `.foundry/tracker.json` marker now takes precedence over the
  host-global tracker default, so a checkout of this repository resolves `tracker=linear`
  without any per-session environment override.
- `registry cutover` binds a project to Linear by its exact manifest digest
  (`sha256:<MANIFEST-DIGEST>`), rather than a mutable name or URL.
- The former YouTrack binding is preserved as an archive tombstone, not deleted.
- An operations log records the cutover's `adr_authority` decision (batch digest, import
  scope) and any `incidents` under `linear-cutover-operations.json`.

See [`docs/linear-tracker.md`](linear-tracker.md) for the complete adapter contract.

## Human AC override: typed receipt and recovery replay

A human `--allow-incomplete-ac --ac-override-reason=<public-audit-code>` merge on Linear
now produces a typed `acceptance-override` receipt. If the override merge is interrupted
after the receipt is written but before completion is observed, recovery replays from
that exact receipt instead of re-deciding the override (PAT-49). This does not weaken the
override path's audit requirement; it makes the interrupted case resumable without a
second human decision.

## Lifecycle and remediation stability (PAT-21, PAT-24, PAT-26–PAT-32)

A round of fixes stabilizes the Linear-tracked bounded-remediation and lifecycle paths
carried over from the 0.8.x Linear cutover work: blocking premature GitHub closure of a
Linear-tracked issue, tolerating a Backlog-to-In-Progress transition after a PR is
already attested, making remediation usable after a review on an already-consumed
technical route (including controlled resumption of an exhausted diagnostic window and
rearming across a PR base advance), allowing exactly one new review after a credited
correction on a consumed technical route, and proving the provider handoff stays
idempotent with valid capability after a crash. None of these change the escalation
ceiling, tier floors, or the human-stop contract of FOUNDRY-ADR-0006.

## Public main-branch protection (PAT-12)

The public repository's `main` branch protection is attested against the actual GitHub
branch protection rules, closing the gap where the repository being public was assumed
protected rather than verified. This complements, and does not replace, the `PreToolUse`
Bash guard and pre-push hook that already deny a direct push to the default branch
(AGENTS.md/CLAUDE.md#R1).

## Recommended main profile and unchanged escalation contract

Unchanged from 0.8.0:

| Host | Recommended main profile | Frontier reviewer floor | Architect floor |
|---|---|---|---|
| Claude Code | Sonnet 5 / medium | Opus 5 / high | Fable 5 / high |
| Codex | GPT-5.6 Terra / medium | GPT-5.6 Sol / high | GPT-5.6 Sol / max |

This is operator guidance. Foundry can resolve delegated roles, but **cannot force the
model or effort of the already-open main conversation**. Reviewer never falls below
frontier/high and architect never below apex/high. The existing maximum of two tier
increases per issue remains shared across both hosts; exhaustion requires a human and
does not grant extra tools or authority.

FOUNDRY-ADR-0006 mappings and fallbacks remain normative. FOUNDRY-ADR-0008 keeps model,
effort, and context as separate observable dimensions, but observation is not activation.

## No local promotion

FOUNDRY-ADR-0007 keeps local preprocessing optional, disabled by default, untrusted,
evidence-bound, and non-privileged. No local model becomes a host, role, tier, reviewer,
architect, gate, fallback owner, or source of product authority in 0.9.0.

## Upgrade, verification, and rollback

Use [`migration-0.9.0.md`](migration-0.9.0.md) for the tested Claude Code and Codex
command contracts: switching an existing installation away from a former private
marketplace, marketplace/package upgrade, reload/new-task boundary, shared
`foundry:configure`, read-only `foundry:doctor`, and verification that a Linear-bound
repository resolves `tracker=linear` from its `.foundry/tracker.json` marker.

## Evidence index and limits

- FOUNDRY-ADR-0005: one implementation, two host packages.
- FOUNDRY-ADR-0006: unchanged semantic mappings, floors, fallbacks, and escalation.
- FOUNDRY-ADR-0007: local provider remains non-privileged and promotion-gated.
- [`docs/linear-tracker.md`](linear-tracker.md): the complete Linear adapter contract,
  including versioned ADR Documents, `missing_relations`, `plan_adr_batch_qualification`,
  and the repository cutover marker/registry/operations log.
- [`release-0.8.0.md`](release-0.8.0.md) and [`migration-0.8.0.md`](migration-0.8.0.md):
  immutable historical 0.8.0 publication evidence, not rewritten as 0.9.0 results.
