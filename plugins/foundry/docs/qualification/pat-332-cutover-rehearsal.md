# PAT-332 — Rehearsal of the full Linear → DevHub cutover of this repository

Date: 2026-10-23 (times in Paris local time). Scope: the whole Linear history of this repository (PAT-ADR-0032),
through `cutover preflight`, `cutover copy` and `cutover activate` (`tooling/foundry/cutover.py`), toward a
disposable DevHub instance, never production. Every figure below is observed in this rehearsal unless marked
otherwise.

## Setup

- Source: the Linear project of this repository, read live (no cache) from a `git clone --no-local` of `main`
  carrying the code of this branch (`migration.py`, `trackers/base.py`, `trackers/devhub.py`,
  `trackers/linear.py`). The clone's push URL points to a non-existent path.
- Write guard: a wrapper replaced `LinearTracker._graphql_once` to log each operation and refuse any GraphQL
  document that is a mutation, before sending it. Result: 1950 operations logged, 0 mutation.
- Target: a disposable DevHub built from `72a24e2` (DEVHUB-131), project key `PLP` (maintainer decision of
  2026-10-23), provisioned by `provision_project` and checked by `verify_project_identity`. Earlier iterations
  used `890e338`.
- Isolation: `FOUNDRY_DATA` pointed to a scratch registry holding only this repository's Linear entry; the real
  `~/.config/foundry` was backed up and never written.

## Final run (02:52–02:59)

| Step | Result | Duration | Linear requests |
|---|---|---|---|
| `preflight` | manifest captured, 309 issues, 60 ADRs | 176 s | 975 |
| `copy` | phase `verified` | 95 s | 0 |
| `activate` | tracker `devhub`, project `PLP` | 188 s | 975 |

Linear requests by operation (whole run): `issue.read` 1824, `adr.issue-link.read` 92, `issue.history` 26,
`adr.list` 8. `activate` recaptures the source (`source_is_unchanged`), hence a second 975. Linear's quota is
2500 requests per hour (response headers `x-ratelimit-requests-*`), so preflight and activate must run in the
same fresh hour; a copy between them costs none.

After activation, from the clone: `query adrs` 60, `query backlog` 309, `query issue PLP-1` readable,
`doctor` with no red line.

## Manifest

- Issues: 309. By state: done 278, backlog 21, dropped 7, in-progress 3. By type: Task 187, Bug 51, Epic 36,
  Feature 31, none 4.
- Links: 978 copied (relates 252, parent-of 234, subtask-of 234, depends-on 129, blocks 129), each read back
  from both ends.
- Comments: 2106 copied, 0 comment exception. The two longest are Epic closure receipts of 56409 and 54970
  characters; they were exceptions under the earlier 50000 bound.
- Issue exceptions (82 issues carry at least one): `archived` 49, `source_dates` 24 (living issues), `milestone`
  8, `state` 3 (in-progress issues are created in backlog), `links` 2 (one parent link whose parent has no type,
  named on both ends).
- ADRs: 60 (accepted 53, deprecated 3, superseded 3, proposed 1), no ADR exception. The port id of each
  imported ADR is its foreign id, so `PAT-ADR-NNNN` and `FOUNDRY-ADR-NNNN` stay readable as such. With key
  `PLP`, no foreign id matches `PLP-ADR-NNNN`, so DevHub numbers every ADR from its counter (deduced from
  `_origin_adr_number`).
- Identifiers: issues change identifier (for example the source `PAT-7` became `PLP-308`); bodies and
  comments keep their source references unchanged, as PAT-ADR-0032 decides.

## Gaps found and corrected in this PR

Each is described with its code and tests in `docs/devhub-tracker.md`, "Corrections from the PAT-332
rehearsal":

1. Archived Epic children and archived relations were missing from the Linear history export (first preflight
   failed on the Epic graph replay of a closed archived companion Epic; ten `relates` links were one-sided).
2. A parent link whose parent is not an `Epic` was refused by DevHub; it is now a named `links` exception.
3. ADR import order: `FOUNDRY-ADR-0001` took number 1, then `PAT-ADR-0001` was refused (409) on a target
   keyed `PAT`; numbered ADRs are now written first.
4. Source date bound aligned on DevHub's (9999-12-31T23:59:59.999Z).
5. Comment bound aligned on DevHub's 200000 (DEVHUB-131); the two closure receipts above are now copied.

## Named, not corrected

- Cost: 912 `issue.read` per capture for 309 issues, dominated by Epic graph re-reads (deduced from the
  operation counts). It fits one quota hour with about 550 requests to spare.
- The cutover needs a DevHub at `72a24e2` or later; Foundry does not check the server version. Production
  runs `72a24e2` since 2026-10-23 (stated by DevHub).
- A cycle of parent or dependency links still fails closed (none in this corpus).
- Whether every archived comment and threaded reply is included is unknown (follow-up PAT-335).
