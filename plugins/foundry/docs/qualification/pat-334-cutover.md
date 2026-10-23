# PAT-334 — Cutover of this repository from Linear to production DevHub

Date: 2026-10-23 (times in Paris local time). Decision: PAT-ADR-0032. Procedure: `cutover preflight`, `cutover
copy` and `cutover activate` (`tooling/foundry/cutover.py`) of the installed Foundry 2.3.0, run from the main
checkout, toward the production DevHub, project key `PLP` (id `8`). The issue PAT-334 was copied as `PLP-2`;
its work after activation is tracked there. Every figure below is observed in this run unless marked otherwise.

## Prerequisites (PAT-ADR-0032)

- Production DevHub revision `72a24e2a696acea5f2634eaa95285b5b17110ab5`, read on its health endpoint before the
  run.
- Live needs: green twice on a disposable instance built from that revision (48 passed, 1 skipped, the skip
  being the local-account case of DHN-42), on 2026-10-23.
- `cutover activate` toward DevHub qualified offline and live (PAT-329): the offline test of `tests/test_tracker_migration.py`
  and the live need DHN-47, green twice on a disposable instance built from DevHub `3f0cf4b` (`CHANGELOG.md`,
  2.3.0 entry PAT-329; `docs/release-2.3.0.md`, merged issue PAT-329; `docs/devhub-requirements.md`, "Live run
  of PAT-329").
- Rehearsal: `docs/qualification/pat-332-cutover-rehearsal.md`.
- Foundry 2.3.0 installed on Claude Code and Codex (release PAT-333, `docs/release-2.3.0.md`).
- Before the run: `~/.config/foundry` copied to a dated backup (the registry digest was compared equal). The
  production project was provisioned by `provision_project` and read back by `verify_project_identity`; before
  that, the repository had no binding on the server (404 on the project resolver).

## Run

| Step | Time | Result | Duration | Linear requests |
|---|---|---|---|---|
| `preflight` | 07:38 | manifest captured, 309 issues, 60 ADRs | 197 s | 975 |
| `copy` | 07:42 | phase `verified` | 1240 s | 0 |
| `activate` | 08:03 | tracker `devhub`, project `PLP` | 440 s | 975 |

The `copy` and `activate` durations are much longer than in the rehearsal (95 s and 188 s against a local
instance); the cause is unknown (network latency to the production server is the likely one, not measured).

Write guard: the same wrapper as the rehearsal replaced `LinearTracker._graphql_once` to log each operation and
refuse any GraphQL mutation before sending it. Result: 1950 operations logged (`issue.read` 1824,
`adr.issue-link.read` 92, `issue.history` 26, `adr.list` 8), 0 mutation. After activation, the main checkout
resolves `devhub` through `.foundry/tracker.json`, so no Foundry command of this repository addresses Linear
any more (deduced from the marker resolution, `docs/devhub-tracker.md`).

## Manifest

- Issues: 309, all `verified`. By state: done 280, backlog 20, dropped 7, in-progress 2 (PAT-295 → PLP-36 and
  PAT-18 → PLP-297, created in backlog with a `state` exception). By type: Task 187, Bug 51, Epic 36, Feature
  31, none 4.
- Links: 978 (relates 252, parent-of 234, subtask-of 234, depends-on 129, blocks 129).
- Comments: 2120, 0 comment exception.
- Issue exceptions (80 issues carry at least one): `archived` 49, `source_dates` 22, `milestone` 8, `state` 2,
  `links` 2.
- Manifest digest: `sha256:d60e78a1ac3e9dfb24be4e0da4b4adccb6206c9dfdfa6a17c461ccb904987241`, the one
  `activate` bound in `.foundry/tracker.json`.
- ADRs: 60, all `verified` (accepted 53, deprecated 3, superseded 3, proposed 1), no ADR exception. 27 of them
  are `FOUNDRY-ADR-*` ADRs (observed by the coordinator in the manifest). That count equals the 27 historical
  Foundry ADRs that `docs/linear-tracker.md` ("ADR authority after the historical import") says the DevHub
  cutover imported; only the number was compared, not the identifiers.
- The differences from the rehearsal (2120 comments against 2106, two issues done since) come from the work
  done between the two captures.

## Checks after activation

- Claude Code, installed CLI, main checkout: `doctor` shows `Config — tracker=devhub`, `PLP … — 309 issues` and
  `Repo courant → projet — patolabs-plugins → PLP` green; `query issue PLP-2` reads the issue.
- Codex: the CLI of the Codex installation (`codex plugin list`) gives the same `doctor` lines and reads
  `PLP-2`. A headless Codex session was not run (refused by the local permission policy), so the reading from
  a Codex session itself is not verified.
- Registry (observed by the coordinator, comparing the backup registry taken before the run with the registry
  after it): `activate` changed the Linear registry entry of this repository only by adding `archive: true`; the
  entry stays, archived.
- `doctor` also shows three red lines for older DevHub registry entries of other repositories
  (`foundry-e2e-sandbox-20260903`, `foundry-e2e-sandbox-20260906`, `trame`), present in the registry before
  this run and unrelated to it; they were left untouched.
- `source_ref`: `PAT-177`, drawn at random among the manifest's issues, was found by
  `migration_find_issue(linear:issue:PAT-177)` as `PLP-146`, the target named by the manifest, with the same
  title.
- First production use: `issue start PLP-2` moved PLP-2 to in-progress on DevHub and created the branch of
  this change.
