# Evidence Plane envelope

`foundry.evidence_plane` is a read-only, content-free projection for a cockpit. It
composes facts Foundry already knows into `foundry-evidence-envelope.v1` and returns
only `GO`, `STOP`, or `UNKNOWN`. It does not open or merge a PR, alter a tracker,
check an acceptance box, run tests, push code, deploy, or grant any permission. A
`GO` is advisory and cannot replace the merge, review, CI, tracker, or human gates.

## Bound coordinates and sources

Every envelope binds the canonical `host/owner/repository` identity (for example
`github.com/patobiskoto/patolabs-plugins`), issue id, acceptance-criteria
digest, PR number, exact base and head SHAs, and exact diff digest. The evidence section
contains:

- the canonical Foundry acceptance-review proof, including its proof id, AC digest,
  quality and exact base/head/diff coordinates;
- a content-free `foundry-test-receipt.v1`, integrity-bound to repository, issue,
  head, diff, terminal status and a result digest; it never carries a command, path,
  output, prompt, provider credential or secret;
- one closed, versioned `foundry-ci-receipt.v1` for each GitHub source: `check_runs`
  and legacy `commit_statuses`. A receipt is integrity-bound to its non-interchangeable
  source name, canonical repository, exact head SHA, explicit observation instant,
  bounded counts and normalized-observation digest. Names and raw provider payloads are
  discarded. The builder may normalize one explicitly fresh provider read into a
  receipt; envelope composition never receives raw `Check` values and cannot relabel an
  observation made for another SHA or source.

Claude Code and Codex call the same builder and verifier. No host hook or prompt is part
of the trust boundary, and the two facades produce the same canonical content outside
observation identifiers or times.

## `GO`, `STOP`, and `UNKNOWN`

`GO` requires a fresh, integrity-valid envelope on the current repository, issue, AC,
PR, base, head and diff; an approved all-AC review; a passed exact-coordinate test
receipt; both fresh CI source receipts successfully observed for that exact canonical
repository and head; at least one real CI `success` across
them; and no pending or failing check. This mirrors the two-source proof semantics of
FOUNDRY-ADR-0002 without becoming the merge gate.

`STOP` means the available evidence contradicts the requested coordinates or a required
gate: moved base, wrong head/diff/AC, blocked review, failed tests, pending/failing CI,
zero checks, or all-neutral/all-skipped checks. `UNKNOWN` preserves absence and
uncertainty: incomplete or invalid receipts, missing/unsupported CI source, pending or
unavailable tests, stale/future observations, and replays. Missing facts are never
converted into a positive or negative claim.

Envelope, terminal test, and each CI-source receipt observations are fresh for at most
15 minutes, with at most 60 seconds of tolerated future clock skew. Older or future
evidence remains `UNKNOWN`; composing an old receipt into a fresh envelope does not
refresh that fact. A cross-repository or wrong-SHA CI receipt is `STOP`; a
source-mismatched, missing, stale, future, malformed, or replayed receipt cannot become
`GO`.

The verifier receives the current coordinates and an optional set of already-consumed
envelope digests. A repeated digest is reported as `UNKNOWN/replay`; a changed base,
head, diff, issue or AC is `STOP`. The envelope itself is hash-bound for integrity, but
is not signed and makes no claim that an external reality is independently attested.
Its inputs must still come from the normal Foundry source adapters and stores.

This envelope complements the exact-SHA delivery receipt of FOUNDRY-ADR-0011. Neither
object can satisfy, bypass, weaken, or retroactively change CI, merge, tracker `done`,
deployment, rollback, or any provider authority.
