# Evidence Plane envelope

`foundry.evidence_plane` is a read-only, content-free projection for a cockpit. It
composes facts Foundry already knows into `foundry-evidence-envelope.v1` and returns
only `GO`, `STOP`, or `UNKNOWN`. It does not open or merge a PR, alter a tracker,
check an acceptance box, run tests, push code, deploy, or grant any permission. A
`GO` is advisory and cannot replace the merge, review, CI, tracker, or human gates.

## Bound coordinates and sources

Every envelope binds the canonical `owner/repository`, issue id, acceptance-criteria
digest, PR number, exact base and head SHAs, and exact diff digest. The evidence section
contains:

- the canonical Foundry acceptance-review proof, including its proof id, AC digest,
  quality and exact base/head/diff coordinates;
- a content-free `foundry-test-receipt.v1`, integrity-bound to repository, issue,
  head, diff, terminal status and a result digest; it never carries a command, path,
  output, prompt, provider credential or secret;
- separate normalized observations for GitHub check-runs and legacy commit statuses.
  Names and raw provider payloads are discarded; only bounded counts and the exact head
  remain.

Claude Code and Codex call the same builder and verifier. No host hook or prompt is part
of the trust boundary, and the two facades produce the same canonical content outside
observation identifiers or times.

## `GO`, `STOP`, and `UNKNOWN`

`GO` requires a fresh, integrity-valid envelope on the current repository, issue, AC,
PR, base, head and diff; an approved all-AC review; a passed exact-coordinate test
receipt; both CI sources successfully observed; at least one real CI `success` across
them; and no pending or failing check. This mirrors the two-source proof semantics of
FOUNDRY-ADR-0002 without becoming the merge gate.

`STOP` means the available evidence contradicts the requested coordinates or a required
gate: moved base, wrong head/diff/AC, blocked review, failed tests, pending/failing CI,
zero checks, or all-neutral/all-skipped checks. `UNKNOWN` preserves absence and
uncertainty: incomplete or invalid receipts, missing/unsupported CI source, pending or
unavailable tests, stale/future observations, and replays. Missing facts are never
converted into a positive or negative claim.

Envelope and terminal test observations are fresh for at most 15 minutes, with at most
60 seconds of tolerated future clock skew. Older evidence remains `UNKNOWN`; composing
an old test receipt into a fresh envelope does not refresh the test fact.

The verifier receives the current coordinates and an optional set of already-consumed
envelope digests. A repeated digest is reported as `UNKNOWN/replay`; a changed base,
head, diff, issue or AC is `STOP`. The envelope itself is hash-bound for integrity, but
is not signed and makes no claim that an external reality is independently attested.
Its inputs must still come from the normal Foundry source adapters and stores.

This envelope complements the exact-SHA delivery receipt of FOUNDRY-ADR-0011. Neither
object can satisfy, bypass, weaken, or retroactively change CI, merge, tracker `done`,
deployment, rollback, or any provider authority.
