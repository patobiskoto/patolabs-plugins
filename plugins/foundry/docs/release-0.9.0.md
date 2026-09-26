# Foundry 0.9.0 release notes

Release decision: **publish the shared Claude Code/Codex package, including the Linear
tracker adapter, its versioned ADR Documents, the completed historical ADR import, and
the repository's own cutover to Linear, with production disposition `keep`.** Foundry
0.9.0 keeps every 0.8.x model mapping, reasoning effort, route, and routing gate
unchanged; the CI gate semantics of FOUNDRY-ADR-0002 are unchanged too. It adds the
Linear tracker adapter (base adapter plus versioned ADR Documents, the repository
tracker marker, and the typed AC-override receipt), an opt-in DevHubTracker v1, the
Dev Hub command worker, the Epic execution preview, the Epic-closure contract, and
`rearm-remediation` — described below.

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
both hosts — provided the marketplace source is already
`patobiskoto/patolabs-plugins`. An installation still pointed at a former private
marketplace does **not** receive 0.9.0 from this cycle: updating against a source that
never published 0.9.0 cannot produce it. See [`migration-0.9.0.md`](migration-0.9.0.md)
for the exact dual-host commands, including the one-time switch away from a former
private marketplace that such an installation needs first.

## Linear tracker adapter: base adapter, versioned ADR Documents, and completed historical import

0.9.0 introduces the fail-closed Linear tracker adapter itself — issue reads, creation,
and non-replacing relations/comments; existing issue field/state/parent/body
replacement stays typed-unavailable because Linear exposes no atomic anti-overwrite
precondition — and, in the same release, completes its ADR half. None of this existed
in 0.8.x:

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

**Atomic Epic closure is unavailable** in the new Linear adapter, matching YouTrack's
existing posture; the new opt-in DevHubTracker v1 (below) reports the same capability
unavailable too. No adapter emulates it with a racy read/write.

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
now produces a typed `acceptance-override` receipt (PAT-49). Recovery is **not**
automatic; in both cases below, the operator re-runs the exact same
`issue merge <ID> <PR> --allow-incomplete-ac --ac-override-reason=<code>` command:

- If the receipt was already written and the merge was interrupted before completion
  was observed, replaying that same command is a no-op: Foundry reads the existing
  receipt instead of asking for a second human override decision.
- For an issue merged under override before this receipt existed at all (the PAT-10
  shape: review state, the historical free-text audit note, then done, no receipt),
  re-running the same command backfills exactly the missing override receipt bound to
  the already-merged PR/head/base/generation, once the done receipt and current native
  state match; it performs no new merge.

This does not weaken the override path's audit requirement or turn recovery into a
second, independent human decision; it makes both the interrupted and the backfill case
resumable from one deterministic, idempotent command instead of an ad hoc repair.

## Lifecycle and remediation stability (PAT-21, PAT-24, PAT-26–PAT-32)

A round of fixes stabilizes the Linear-tracked bounded-remediation and lifecycle paths
this same release introduces above: blocking premature GitHub closure of a
Linear-tracked issue, tolerating a Backlog-to-In-Progress transition after a PR is
already attested, making remediation usable after a review on an already-consumed
technical route (including controlled resumption of an exhausted diagnostic window and
rearming across a PR base advance), allowing exactly one new review after a credited
correction on a consumed technical route, and proving the provider handoff stays
idempotent with valid capability after a crash. None of these change the escalation
ceiling, tier floors, or the human-stop contract of FOUNDRY-ADR-0006.

## Also in 0.9.0: Dev Hub command worker, Epic preview, Epic closure, and rearm-remediation

Beyond the Linear work above, this release also adds:

- An outbound **Dev Hub command worker** with exact claim/heartbeat bindings, monotonic
  local receipts, and a concrete Claude execution path whose model floor and dollar
  ceiling are fixed before the effect; a host-shared SQLite ledger reserves observed
  budget and concurrency atomically across worker processes before launch.
- A read-only, deterministic **Epic execution preview** over Dev Hub's complete
  versioned subgraph: classifications, human gates, dependency waves and a stable digest,
  with no branch, agent, command, budget, tracker-mutation, or merge authority.
- A provider-neutral, non-code **Epic closure** command and Tracker contract, with a
  receipt binding the exact project/parent version/AC/child set; DevHub Tracker v1 and
  YouTrack both report this capability unavailable rather than emulate it.
- `rearm-remediation`, a dedicated audit-bound transition for a just-exhausted bounded
  correction window, preserving counters and tier floors and requiring a controlled
  human reason.
- An opt-in **DevHubTracker v1** provider (normalized project/issue/link/comment/ADR
  operations, bounded HMAC receipts, versioned idempotent writes); YouTrack remains the
  default and no routing, gate, review, or merge authority moves to Dev Hub.

See `CHANGELOG.md`'s 0.9.0 section for the complete list of changes in each area.

## Public main-branch protection (PAT-12)

The public repository's `main` branch protection is **read back** from the actual
GitHub branch protection rules — an operational snapshot, not a GitHub-signed
attestation (see [`docs/public-repository-security.md`](public-repository-security.md))
— closing the gap where the repository being public was assumed protected rather than
verified. This complements, and does not replace, the `PreToolUse`
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
repository resolves `tracker=linear` from its `.foundry/tracker.json` marker. That same
guide also states plainly why there is **no rollback below 0.9.0** for this or any other
Linear-bound repository, and what a downgrade implies for a repository that is not.

## Evidence index and limits

- FOUNDRY-ADR-0005: one implementation, two host packages.
- FOUNDRY-ADR-0006: unchanged semantic mappings, floors, fallbacks, and escalation.
- FOUNDRY-ADR-0007: local provider remains non-privileged and promotion-gated.
- [`docs/linear-tracker.md`](linear-tracker.md): the complete Linear adapter contract,
  including versioned ADR Documents, `missing_relations`, `plan_adr_batch_qualification`,
  and the repository cutover marker/registry/operations log.
- [`release-0.8.0.md`](release-0.8.0.md) and [`migration-0.8.0.md`](migration-0.8.0.md):
  immutable historical 0.8.0 publication evidence, not rewritten as 0.9.0 results.
