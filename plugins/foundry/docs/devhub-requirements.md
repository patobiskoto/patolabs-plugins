# DevHub requirements and live conformance suite (PAT-296)

This page is the single exchange point between Foundry and the agent that works on the
DevHub server (separate repository). Foundry owns the **need** as executable tests; the
DevHub side iterates on the server until the suite is green. Each repository owns its own
files: this page and the suite live here, the server lives there.

It is part of Epic PAT-295 (qualify DevHub as the tracker of this repository). It does
not change the cutover mode, which FOUNDRY-ADR-0026 settled: one active tracker per
repository, atomic cutover, selective import of living work and of the ADRs still needed,
no full history migration. Migration needs below (DHN-34 to DHN-36) are stated in that form
only.

Until PAT-323, DevHub was a pilot outside the V1 contract: `registry._V1_TRACKERS` held
`youtrack`, `linear`, `ghprojects` (`registry.py`), and `tracker-contract.v1.json` listed
DevHub "for contrast only" (observed, 2026-10-19). PAT-ADR-0031 (accepted) admits DevHub as
the fourth V1 provider and PAT-323 implements it: `devhub` is in `registry._V1_TRACKERS`,
the contract carries a DevHub column whose cross-tracker ADR import cell, a `gap` owned by PAT-327 at that date, is
`supported` since PAT-327 (`tracker-contract.v1.json`; PAT-323 left two gaps, PAT-324 closed the live-work copy), and the pilot path is retired
(`devhub-tracker.md`, "Project binding and cutover"). DHN-40 is green since the live run of
PAT-323 ("Live run of PAT-323" below).

## Run the suite

The suite runs the adapter `trackers/devhub.py` against a disposable DevHub instance
whose URL you give explicitly. From `plugins/foundry`:

```bash
export DEVHUB_LIVE_URL=http://127.0.0.1:3000
export DEVHUB_LIVE_CONFIRM=I_UNDERSTAND_DEVHUB_LIVE_SUITE_WRITES_TO_A_DISPOSABLE_INSTANCE
export DEVHUB_LIVE_TOKEN=...          # credential dedicated to the disposable instance
export DEVHUB_LIVE_PROOF_SECRET=...   # proof secret dedicated to the disposable instance
python3 -m pytest -q -m devhub_live tests/integration/test_devhub_live_conformance.py -rxXs
```

`-m devhub_live` replaces the default `-m "not integration"` of `pytest.ini`. Without
`DEVHUB_LIVE_URL` every test is skipped with the reason
`DEVHUB_LIVE_URL not set: live DevHub conformance suite not requested`. The public CI
runs do not collect it: `-m tracker_conformance` and the expressions with `not integration`
exclude it, and the runs without `-m` keep the `pytest.ini` default
(`.github/workflows/ci.yml`, `plugins/foundry/README.md`). A `-m` expression without
`not integration` (for example `-m "not historical_fixture"`) replaces the `pytest.ini`
default and collects it: skipped when `DEVHUB_LIVE_URL` is unset, but run against that URL
(writes included) when the four `DEVHUB_LIVE_*` variables are exported, so unset them for
such a run. The suite is not part of `-m tracker_conformance` (`docs/tracker-conformance.md`):
that suite is deterministic over fake transports, this one needs a server.

Reading the result: a passing test is a need met; `xfailed` is a need not met yet (an
expected red, listed below with its owner) that raised the exception its `gap` declares in
`raises=`, any other exception, a setup error included, is a failure or an error, not an
`xfailed` (no `@gap` declares `DevHubTrackerError` any more since PAT-303; `tests/test_devhub_requirements.py`
checks that each remaining `@gap` declares exactly one `raises=`, not that it is the right exception); `XPASS(strict)` reported as a failure
means a gap closed and its test must be turned into a plain need (see "Closing a gap");
`failed` on a need expected green is a regression or a wrong expectation to discuss on this
page.

### Environment variables

| Variable | Role |
| --- | --- |
| `DEVHUB_LIVE_URL` | Origin of the disposable instance. Validated by `trackers.devhub.validate_base_url`: HTTPS, or cleartext HTTP on a numeric loopback literal only. |
| `DEVHUB_LIVE_CONFIRM` | Must be exactly `I_UNDERSTAND_DEVHUB_LIVE_SUITE_WRITES_TO_A_DISPOSABLE_INSTANCE`; anything else skips. |
| `DEVHUB_LIVE_TOKEN` | Bearer token (at least 24 characters, adapter rule). Never printed. |
| `DEVHUB_LIVE_PROOF_SECRET` | HMAC proof secret (at least 32 characters, adapter rule). Never printed. |
| `DEVHUB_LIVE_LOCAL_ACCOUNTS` | Optional. Exactly `1` declares that the target runs with local accounts; only DHN-42 reads it (otherwise it skips). The suite cannot read the instance mode, so this is a declaration, never a detection (`devhub_live_gate.local_accounts_declared`). |
| `DEVHUB_LIVE_WORKFLOW_PUBLISH` | Optional. Path of an executable that publishes a workflow on the disposable instance; only DHN-44 reads it (otherwise it skips). The service credential cannot publish (`publishWorkflow` requires a human actor, observed in DevHub source at `cd23304`). The test runs `<executable> <PROJECT KEY>` with the new `settings` JSON on standard input; see "Live run of PAT-328" for a procedure. |

These names are deliberately distinct from the everyday ones (`DEVHUB_URL`,
`DEVHUB_TRACKER_TOKEN`, `DEVHUB_TRACKER_PROOF_SECRET`, `DEVHUB_SMOKE_*`).

### Production safety (fail closed)

Implemented in `tests/devhub_live_gate.py`, covered offline by
`tests/test_devhub_live_gate.py`:

1. Opt-in is explicit: URL, exact confirmation phrase and both credentials, otherwise skip.
2. The suite builds the adapter from those four values only (`DevHubTracker(url=, token=,
   proof_secret=)`); it never reads `config.env`, the keychain, the registry or the
   repository's tracker marker. DHN-02 replaces `registry.resolve` with a stub, so the real
   registry is never read.
3. It refuses (error, nothing written) a URL with the same origin as `DEVHUB_URL`, and
   credentials equal to `DEVHUB_TRACKER_TOKEN` / `DEVHUB_TRACKER_PROOF_SECRET`, when those
   variables are set in the same environment.
4. Instance marker: before any write, it reads the project catalogue
   (`list_project_catalog`) and refuses unless every visible project key starts with `LV`.
   An unreadable catalogue refuses too. The suite creates only projects whose key starts
   with `LV` followed by eight random characters.

Limits, stated plainly: check 3 only knows what the environment holds, and check 4 only
sees what the token can see. On an instance with local accounts, check 4 proves nothing:
the credential only sees the projects granted to it (observed 2026-10-20 in the source:
`authorizeProject`, `src/access/domain.ts:74-119`, limits a service actor to its granted
projects), so 0 visible projects does not mean an empty instance. The marker is meaningful
only on an instance without local accounts, which is why the procedure below brings one up
and why the run of 2026-10-20 used it. Use credentials created for the disposable
instance. The suite
deletes nothing: every run leaves `LV*` projects behind, so use a throwaway database. DevHub
at `2edcea2` has a project deletion route under `admin/projects`, with its own
`DEVHUB_TRACKER_ADMIN_TOKEN` and a deletion-plan preflight (`2edcea2:docs/TRACKER_API.md:213`,
"Authentication and version"; `docs/devhub-tracker.md`, "Destructive smoke", predates it, and
the suite does not use it). That route refuses (409 `resource_conflict`) a project that holds
an original Epic closure or an issue proof receipt (`2edcea2:src/tracker/project-lifecycle.ts:283-298`,
observed in source, answer 2 of the DevHub agent). The suite's DHN-18, DHN-19 and DHN-22 create
such evidence, so the `LV*` projects of a run cannot be deleted one by one (deduced from
those two facts): the supported cleanup is to drop the whole disposable instance.

## Bring up a disposable instance

Run once on 2026-10-20 (DevHub revision `ff22b3a821fb031b49d1fdb78c43ecb481431c7f`, which is
the head of the unmerged DEVHUB-100 branch and not on DevHub `main`, answer of the DevHub
agent), on the machine of the maintainer, never against the production URL. The current
DevHub reference is `1227841` (`main`, 2026-10-20); the run of PAT-303 at `2edcea2` is in "Live run of
PAT-303", the run of PAT-313 at `1227841` in "Live run of PAT-313". It yields an instance
without local accounts, which the suite needs (limit of check 4 of "Production safety"). Sources: DevHub
`deploy/qa/up.sh` (image and `app.env` generation) and `deploy/dogfood/validate-app-env.sh`
(validation), read-only; both are DevHub files, not Foundry ones.

Prerequisites, stated because this page does not build the image:

- Since 2026-10-20 the DevHub agent reports that `main` (`2edcea2`) has no supported
  throwaway procedure, because `deploy/qa/` (DEVHUB-100) is not merged; it built its own run
  with `git archive <rev>`, `docker build -f deploy/dogfood/Dockerfile`, its own compose
  project and volume on loopback ports, and `down -v` to reset (observed by the DevHub
  agent, not run by Foundry). At `809ca94` and `1227841`, `deploy/qa/` is on `main`
  (DEVHUB-100, merge `809ca94`; observed by listing the archive of `1227841`); the run of
  PAT-313 still built its image with `git archive` and `deploy/dogfood/Dockerfile`, not
  with `deploy/qa/`.
- For the run of 2026-10-20, a DevHub image `devhub-qa:<revision>` (full 40-hex revision)
  had to exist locally; the tag is a local name, so any local tag works in the compose file
  below. Its source then was `deploy/qa/up.sh <revision>` (`compose build app`,
  `up.sh:62-63`). That script is not an image-only step: it also brings up the separate
  compose project `devhub-qa` on `127.0.0.1:3205` and `3206` with local accounts
  (`up.sh:80-103`), writes its env files under `~/.config/devhub-qa` (`up.sh:10,26-50`), and
  stores a test-owner password in the macOS keychain (`up.sh:96`). It needs `pnpm install`
  in the DevHub checkout and a clean tracked tree (`up.sh:18,22`). Do not reuse that env
  directory or that database for the suite. Docker and a Postgres image
  (`postgres:16-alpine`) must be available. Line numbers of `up.sh` in this procedure are
  those of DevHub at `ff22b3a`, a branch head that is not on `main`.
- The commands below are the ones of the 2026-10-20 run, except the hardening lines marked
  below, which were added afterwards from the QA compose file and have not been run.

1. Take the image of the prerequisite above.
2. Use a separate compose project `devhub-lv`, published on `127.0.0.1:3305` only, with its
   own named volume `devhub-lv-db-data`. The compose file used (the lines `cpus` to
   `pids_limit`, `cap_drop` and `security_opt` copy `deploy/qa/compose.yml:10-13,41-44,66-69`
   at `ff22b3a`; they were **not run** here, the run of 2026-10-20 used the file without
   them; the QA file also sets logging, restart and stop-grace options, left out):

```yaml
name: devhub-lv
services:
  db:
    image: postgres:16-alpine
    cpus: "1.0"
    mem_limit: 512m
    memswap_limit: 512m
    pids_limit: 128
    env_file: [ ./env/database.env ]
    volumes: [ "devhub-lv-db-data:/var/lib/postgresql/data" ]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U devhub -d devhub_lv"], interval: 5s, timeout: 3s, retries: 20 }
  app:
    image: devhub-qa:<revision>
    cpus: "2.0"
    mem_limit: 1g
    memswap_limit: 1g
    pids_limit: 128
    init: true
    env_file: [ ./env/app.env ]
    environment: { NODE_ENV: production, TRACKER_ONLY_MODE: "true", DEVHUB_DEPLOY_REVISION: "<revision>" }
    depends_on: { db: { condition: service_healthy } }
    ports: [ "127.0.0.1:3305:3000" ]
    read_only: true
    tmpfs: [ "/tmp:rw,noexec,nosuid,nodev,size=64m,uid=10001,gid=10001" ]
    cap_drop: [ ALL ]
    security_opt: [ "no-new-privileges:true" ]
volumes:
  devhub-lv-db-data: { name: devhub-lv-db-data }
```

3. Put `env/` next to it with mode 700 and keep it out of any repository. It holds
   `database.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, here `devhub` and
   `devhub_lv`) and `app.env`, generated the way `deploy/qa/up.sh` does: `DATABASE_URL`,
   `AUTH_PASSWORD_HASH` (bcrypt of a random password that is discarded), `SESSION_SECRET`,
   `COOKIE_SECURE=true`, `TRACKER_ONLY_MODE=true`, `ALLOW_MOCK_AGENT=false`,
   `DEVHUB_TRACKER_TOKEN`, `DEVHUB_TRACKER_ADMIN_TOKEN`, `DEVHUB_TRACKER_PROOF_SECRET`,
   `PORT=3000`, `HOSTNAME=0.0.0.0`. Validate it with `deploy/dogfood/validate-app-env.sh`.
4. Do no account bootstrap, so `local_accounts` stays false. Check it:
   `select local_accounts from account_workspace` must answer false.
5. Export the suite variables from `app.env` in a subshell, never in your interactive
   shell: `DEVHUB_LIVE_URL=http://127.0.0.1:3305`, the confirmation phrase,
   `DEVHUB_LIVE_TOKEN` = `DEVHUB_TRACKER_TOKEN` and `DEVHUB_LIVE_PROOF_SECRET` =
   `DEVHUB_TRACKER_PROOF_SECRET` of `app.env`. Do not export `DEVHUB_TRACKER_TOKEN` or
   `DEVHUB_TRACKER_PROOF_SECRET` themselves in that subshell: the gate refuses a credential
   equal to them (check 3 of "Production safety"). Then run only the `python3 -m pytest` line
   of "Run the suite", not its `export DEVHUB_LIVE_URL=http://127.0.0.1:3000` line, which
   would replace port 3305. Never print those values.

Reset: remove the compose project and the volume `devhub-lv-db-data`; the suite deletes
nothing, and the project deletion route cannot clean a project that holds Epic closures or
proof receipts (see "Production safety" and answer 2).

## Needs

Status vocabulary: **green** = the test passed against a DevHub instance; **red** = the need
is not met (a strict `xfail` test that names the exact exception of the gap in `raises=`,
never a pass); **unknown** = the test exists and has not been run against a DevHub
instance (DHN-48, added by PAT-330, was `unknown` until "Live run of PAT-330", where it became `green`; DHN-25 and DHN-26 were `unknown` from the adapter of PAT-318 until "Live run of
PAT-318"; DHN-27, DHN-28 and DHN-42 were `unknown` when PAT-303 moved them to `@need` and
became `green` in "Live run of PAT-303").
The owner column names who must act when the need is red. Basis: *adapter* = observed in
`trackers/devhub.py` / `trackers/base.py` (the method is inherited from the `Tracker`
default, checked by script on 2026-10-19); *server* = deduced from the DevHub source
(repository `dev-hub`, paths relative to its root), read-only. Server citations without a commit were
read at commit `68c7ef0` (2026-10-19), the `HEAD` of that checkout at reading time; the files
cited here are identical at the earlier `3fedc57` (`git diff 3fedc57 68c7ef0` on them was
empty that day). The current DevHub reference is `1227841` (`main`, 2026-10-20; it adds to
`2edcea2` the QA environment of DEVHUB-100, merge `809ca94`, and the ordering fix of
DEVHUB-107, merge `1227841`, both verified by ancestry; `git diff 2edcea2 1227841 -- src`
touches six `ORDER BY` clauses of `src/tracker` only). Citations
written `2edcea2:path:line` come from the answer of the DevHub agent (observed in source
there) and the server rows DHN-27, DHN-28 and DHN-42 changed there (DEVHUB-103 merge
`2e5d3ff`, DEVHUB-102 merge `3da571f`, both on `main`; verified by ancestry on 2026-10-20).

First live run (2026-10-20, observed, history): DevHub revision
`ff22b3a821fb031b49d1fdb78c43ecb481431c7f` (head of the unmerged DEVHUB-100 branch, not on
`main`), a disposable instance without local accounts
(`select local_accounts from account_workspace` answered false; procedure in "Bring up a
disposable instance"), result 25 passed and 16 xfailed (41 tests, before DHN-42 existed).
Every `green` other than DHN-27, DHN-28, DHN-42 and the needs set to `green` by a later run (the "Live run of PAT-..." sections below) is a test that passed in that run; every `red` of DHN-01 to DHN-41
raised the exception its `gap` declares in `raises=` (for DHN-27 and DHN-28 that was
`DevHubTrackerError`, which does not by itself show the refusal). The cause of each red is still the one in its Basis
column (adapter: observed in the source; server: deduced from the source, not from the
answers of the instance). DHN-42 was run alone on an instance with local accounts the same
day (`DEVHUB_LIVE_LOCAL_ACCOUNTS=1`): `xfailed`, with the 403 of `POST /projects` observed at
the setup of an earlier attempt on that kind of instance.

DevHub agent run (2026-10-20, observed by the DevHub agent, reported in its answer, not by
Foundry): image built from `2edcea2`, patolabs-plugins `d982b0d`. Instance without local
accounts, all tests except DHN-42: 25 passed, 14 xfailed, 2 failed, 1 skipped; the 2
failed are `XPASS(strict)` of DHN-27 and DHN-28. Instance with local accounts, `-k dhn_42`
with `DEVHUB_LIVE_LOCAL_ACCOUNTS=1`: 1 failed, which is the `XPASS(strict)` of DHN-42. DHN-38
stayed `xfailed`. Foundry reproduced it on its own instances: see "Live run of PAT-303".
Test names are in `tests/integration/test_devhub_live_conformance.py`.

| ID | Need | Test | Status | Owner of the gap | Basis |
| --- | --- | --- | --- | --- | --- |
| DHN-01 | Create or recover a repository-bound project, idempotently | `test_dhn_01_provision_project_is_repository_bound_and_idempotent` | green | - | observed, run of 2026-10-20 |
| DHN-02 | Resolve the project from the canonical repository and refuse a contradicting registry binding | `test_dhn_02_resolve_project_by_canonical_repository` | green | - | observed, run of 2026-10-20 |
| DHN-03 | Refuse an unauthenticated request | `test_dhn_03_unauthenticated_request_is_refused` | green | - | observed, run of 2026-10-20 |
| DHN-04 | Create an issue; read back title, byte-exact body, fields, labels, AC counts, version | `test_dhn_04_create_issue_reads_back_fields_body_and_acceptance_counts` | green | - | observed, run of 2026-10-20 |
| DHN-05 | Update only the named fields under the version check | `test_dhn_05_update_fields_changes_only_the_named_fields` | green | - | observed, run of 2026-10-20 |
| DHN-06 | Refuse a stale version (409 `version_conflict`) without writing | `test_dhn_06_stale_version_is_refused_and_leaves_no_write` | green | - | observed, run of 2026-10-20 |
| DHN-07 | Replay an idempotent write once; conflict (409 `idempotency_conflict`) on a divergent replay | `test_dhn_07_idempotent_replay_returns_one_effect_and_divergence_conflicts` | green | - | observed, run of 2026-10-20 |
| DHN-08 | List a project's issues and narrow them by a free-text query | `test_dhn_08_search_lists_issues_and_narrows_by_query` | green | - | observed, run of 2026-10-20 |
| DHN-09 | Paginate a project listing past 100 issues without loss or repeat | `test_dhn_09_search_paginates_past_one_hundred_issues` | green | - | observed, run of 2026-10-20 |
| DHN-10 | Create a child under a parent; both ends show the link | `test_dhn_10_child_creation_links_both_ends` | green | - | observed, run of 2026-10-20 |
| DHN-11 | `depends-on`, `blocks`, `relates` links round-trip with the inverse on the target | `test_dhn_11_dependency_links_round_trip_with_inverse` | green | - | observed, run of 2026-10-20 |
| DHN-12 | Reparent an existing issue with `subtask-of` and `parent-of` | `test_dhn_12_existing_issue_can_be_reparented_in_both_directions` | green | - | observed, run of 2026-10-20 |
| DHN-13 | Add comments; read them back in order with a creation time | `test_dhn_13_comments_round_trip_in_order` | green | - | observed, run of 2026-10-20 |
| DHN-14 | Return every comment of an issue past 100 | `test_dhn_14_every_comment_is_returned_past_one_hundred` | green | - | observed, run of 2026-10-20; the issue read has no row limit in `src/tracker/store.ts:457` (server, observed; `2edcea2:src/tracker/store.ts:458`, answer 4) |
| DHN-15 | Bounded Epic subgraph projection holding the Epic and its children | `test_dhn_15_epic_subgraph_projection_is_bounded_and_complete` | green | - | observed, run of 2026-10-20 |
| DHN-16 | One public audit receipt per create, update, transition, link, comment | `test_dhn_16_audit_projection_holds_one_receipt_per_operation` | green | - | observed, run of 2026-10-20 |
| DHN-17 | Start path `backlog` to `in-progress`; resume from `blocked` | `test_dhn_17_start_path_and_resume_from_blocked` | green | - | observed, run of 2026-10-20 |
| DHN-18 | `review` only with a valid signed proof (forged secret refused); return to `in-progress` | `test_dhn_18_review_needs_a_valid_proof_and_can_return_to_in_progress` | green | - | observed, run of 2026-10-20 |
| DHN-19 | `done` with merge proof; `done` is terminal; a jump without a valid path is refused (409) | `test_dhn_19_done_is_terminal_and_unproven_jumps_are_refused` | green | - | observed, run of 2026-10-20 |
| DHN-20 | Acceptance-checkbox sync patches only against the expected body; replay is a no-op; stale body conflicts | `test_dhn_20_acceptance_sync_patches_only_with_the_expected_body` | green | - | observed, run of 2026-10-20 |
| DHN-21 | Update an issue body (frame/groom) checked against the expected body | `test_dhn_21_arbitrary_body_update_is_checked_against_the_expected_body` | green | - | adapter, PAT-312: `update_body` reads the issue, refuses a body that differs from the expected one (`TrackerConflictError`), sends the body `PATCH` with the version it read, then re-reads the issue and raises `DevHubTrackerError` `invalid_response` if the body is not the requested one (`trackers/base.py:460-466`) (`trackers/devhub.py`, `devhub-tracker.md`, "Body replacement and ADR relations"); the route is the one DHN-20 uses. An ADR body is not written (`BodyUpdateUnavailableError`). The replay line of the test written by PAT-296 (stale expected body expecting `False`) contradicted that contract (`False` means the requested body is already current; a stale expected body is refused, as in `linear.py:7271-7274`); it was corrected by PAT-312 to replay with the current body (`False`) and to expect `TrackerConflictError` on the stale one. Observed in the live runs of PAT-312 (2026-10-20, disposable instance at `2edcea2`): in the first, the write and the first read passed and only that replay line failed; the corrected test with the read-back then passed (31 passed, 10 xfailed, 1 skipped) |
| DHN-22 | Close an Epic atomically from a receipt; replay; recover the original closure | `test_dhn_22_epic_closure_is_atomic_audited_and_replayable` | green | - | observed, run of 2026-10-20 |
| DHN-23 | Refuse a closure whose graph differs from the receipt | `test_dhn_23_epic_closure_refuses_a_graph_that_differs_from_the_receipt` | green | - | observed, run of 2026-10-20 |
| DHN-24 | Close an Epic over a dropped child by explicit acceptance (`--accept-dropped`, PAT-ADR-0017) | `test_dhn_24_epic_closes_over_a_dropped_child_by_explicit_acceptance` | green | - | PAT-ADR-0030 (framed by PAT-313, accepted after its merge) extends the nominative closure of PAT-ADR-0017 to DevHub. Adapter, PAT-313: `epic_dropped_closure_supported` is `True` and the atomic branch of `write.close_epic` refuses, before the server call, a set named by `--accept-dropped` that is not exactly the dropped direct children it read (`devhub-tracker.md`, "Epic closure over a dropped child"). Server: closes over `done` or `dropped` direct children and checks the receipt children atomically (`1227841:src/tracker/epic-closure-store.ts:182-183`, observed in source); no server change. The live test checks the refusal without the flag, the closure with it and the replay, with children that carry criteria since PAT-316 (the done child's are met, the dropped child's stay open; the test reads both children before the closure and asserts `ac_total == 1` on both and `ac_done == 0` on the dropped one, observed in "Live run of PAT-315"). Observed green in the live runs of PAT-313 and PAT-316 (2026-10-20, disposable instance at `1227841`) |
| DHN-25 | Typed acceptance-override receipt before merge | `test_dhn_25_acceptance_override_leaves_a_typed_receipt` | green | - | adapter, PAT-318: `project_acceptance_override` posts `POST /issues/:id/acceptance-override` with a proof of action `acceptance-override` and reads the `acceptance_override` receipt back; `acceptance_override_projection_supported` is `True` (`devhub-tracker.md`, "Acceptance override and human acceptance"). Implemented from the contract announced by DevHub on 2026-10-20; DEVHUB-106 merged as `f347f38`; observed green in "Live run of PAT-318". The end-to-end `issue merge` with an AC override on DevHub has not been run on a server (unknown); offline, `issue merge` driven through `DevHubTracker` is refused before the code-host merge when the route answers 404 `route_unavailable` (`tests/test_devhub_issue_merge.py`) |
| DHN-26 | Accept a non-code issue by a human decision without a PR (PAT-ADR-0025) | `test_dhn_26_non_code_issue_is_accepted_by_a_human_decision_without_a_pr` | green | - | adapter, PAT-318: `accept_issue` posts `POST /issues/:id/human-acceptance` with a proof of action `human-acceptance` and reads the issue and the `human_acceptance` receipt back, recomputing the decision digest from the object read. The test builds a complete decision document. Implemented from the contract announced by DevHub on 2026-10-20; DEVHUB-106 merged as `f347f38`; observed green in "Live run of PAT-318". This need covers the port operation only; the command `issue accept` and its `--status`, qualified for DevHub by PAT-330 (PAT-ADR-0033), are DHN-48 |
| DHN-27 | Abandon a started issue (`dropped` from `in-progress` or `review`) | `test_dhn_27_started_issue_can_be_abandoned` | green | - | server, observed by the DevHub agent: `2edcea2:src/tracker/workflow.ts:12-17` (`canServiceTransition`) allows `in-progress` and `review` to `dropped` and `backlog` on the V1 service route (DEVHUB-103, merge `2e5d3ff`); XPASS reported at `2edcea2` (answer 5); observed by Foundry in "Live run of PAT-303" |
| DHN-28 | Return a started issue to `backlog` | `test_dhn_28_started_issue_can_return_to_backlog` | green | - | same change as DHN-27; the expected predecessor is the existing `expected_version` (409 `version_conflict`, answer 5); XPASS reported at `2edcea2`; observed by Foundry in "Live run of PAT-303" |
| DHN-29 | Create an ADR as `proposed` with an id `<KEY>-ADR-NNNN`; list it with its exact body | `test_dhn_29_adr_is_created_proposed_and_listed_with_its_exact_body` | green | - | observed, run of 2026-10-20 |
| DHN-30 | ADR status `proposed` to `accepted` to `deprecated` with a signed proof | `test_dhn_30_adr_status_moves_proposed_to_accepted_to_deprecated` | green | - | observed, run of 2026-10-20 |
| DHN-31 | The status route refuses `superseded` (409 `invalid_transition`), the ADR stays `accepted` | `test_dhn_31_status_route_refuses_superseded_and_leaves_the_adr_accepted` | green | - | redefined by PAT-326 after DEVHUB-116 (DevHub `12ca158`): `updateTrackerAdrStatus` throws the 409 after the governance check and before the version check and the proof (`12ca158:src/tracker/store.ts:2365`, read in source); the live test calls the route directly because `DevHubTracker.set_adr_status` refuses `superseded` before any request and points to `supersede_adr` (DHN-32). Until `8028000` the same call succeeded (observed, run of 2026-10-20, original need: `accepted` to `superseded` through the status route); observed green in the live run of PAT-326 (2026-10-21, disposable instance at `12ca158`, "Live run of PAT-326") |
| DHN-32 | Supersede an ADR by a named replacement | `test_dhn_32_adr_is_superseded_by_a_named_replacement` | green | - | adapter, PAT-320: `supersede_adr` of `trackers/devhub.py` posts `POST /adrs/:id/supersede` with a proof of action `adr-supersede` bound to both server ids and versions and reads the link back; `migration_export_adrs` returns `superseded_by` and `supersedes` read from `GET /adr-decisions/adrs/:id` (`devhub-tracker.md`, "Proof-bound ADR supersession"). Server: DEVHUB-108 merged as `6721ad8` (`6721ad8:docs/TRACKER_API.md`, "Proof-bound ADR supersession"; `supersedeTrackerAdr`, `6721ad8:src/tracker/store.ts:2350-2404`; route `6721ad8:src/app/api/tracker/v1/[...path]/route.ts:1555-1573`; observed in source). The test is `@need` since PAT-320 and was observed green on a disposable instance built from `6721ad8` ("Live run of PAT-320"). History: until PAT-320 the test was red (`adapter + server`): at `2edcea2` no route reachable by the service credential named a replacement (`POST /adrs/:id/status` carries none, `/adr-decisions` is limited to an interactive human administrator), question 15 and item 18. After the review of PAT-320 the live test runs in its own project (the unlinked supersession that the status route allowed until `8028000`, DHN-31 then, would make every `supersedes` of the suite project `unknown`, `devhub-tracker.md`, "Proof-bound ADR supersession"), checks the replacement's version through `GET /adrs/:id` before and after, replays the supersession and checked that an unlinked supersession reads `unknown`: these additions passed in "Live run of PAT-315". Since PAT-326 the live test no longer creates an unlinked supersession, which the server refuses since `12ca158` (DHN-31); its `unknown` reading is checked offline only (`tests/test_devhub_tracker.py`). Observed green in "Live run of PAT-326". Known DevHub limits, observed in source: `POST /adrs/:id/status` with `superseded` created an unlinked supersession (DEVHUB-87; true up to `8028000`, refused with 409 `invalid_transition` since DEVHUB-116, `12ca158`, see DHN-31), so the adapter never used it for `supersede_adr`; at `6721ad8` the route did not read the project governance mode (`6721ad8:docs/TRACKER_API.md`, same section); since DEVHUB-110 (`5782d78`) it refuses outside Foundry governance (`8028000:src/tracker/store.ts:2393`, observed in source; adapter side PAT-321, `devhub-tracker.md`, "Governance mode") |
| DHN-33 | An ADR-to-issue relation set on a proposed ADR is still readable once the ADR is accepted | `test_dhn_33_adr_issue_relation_set_before_acceptance_survives_it` | green | - | Requalified by the DevHub human decision of 2026-10-20 (answer 7): DevHub keeps the decided-ADR freeze (DEVHUB-73) and plans no server work; Foundry sets the relations before acceptance. Adapter, PAT-312: `link_adr_issue` reads the ADR (`GET /adr-authoring/adrs/:id`, `devhub-adr-authoring.v1`) and, when it is `proposed`, sends `PATCH /adr-authoring/adrs/:id` with the complete `linked_issue_ids` and the version it read (`trackers/devhub.py`); on a decided ADR it raises `AdrRelationFrozenError` (a `TrackerConflictError`) saying the relation must be set before acceptance or carried by the issue, also when the server answers 403 `authority_boundary` after the read and a second read shows the ADR no longer `proposed` (otherwise the 403 propagates); a relation that already exists is a no-write whatever the status (PAT-315). `read_adr_issue_relations` reads `GET /adrs/:id/relationships` (`devhub-adr-relationships.v1`), so the test no longer calls `_req` directly. Server, observed in source at `2edcea2`: edit `src/tracker/store.ts:1960-1974` (`1966` refuses a decided ADR, trigger `drizzle/0021_adr_decisions.sql:39-48`); envelope `src/tracker/adr-relationships-contract.ts:54-67`; read `src/app/api/tracker/v1/[...path]/route.ts:889`; edit route `src/tracker/adr-authoring-http.ts:47-52`. The issue-side carrier after acceptance is not implemented (unknown). Observed in the live run of PAT-312 (2026-10-20, disposable instance at `2edcea2`): passed. Since that run the test reads the relation through `read_adr_issue_relations`, which refuses a response whose `X-DevHub-Contract` is not `devhub-adr-relationships.v1` (`DevHubTracker._req`, `response_contract`) and reads `items[].id`; the test also passed in the runs of PAT-319 and PAT-320, so the header and the `items[].id` shape hold on a live server (deduced from those checks and those passes, no response captured). |
| DHN-34 | Selective import: a living issue is imported once, found by exact source coordinate, replay-safe | `test_dhn_34_living_issue_is_imported_once_by_exact_source_coordinate` | green | - | adapter, PAT-319: `migration_find_issue` and `migration_import_issue` of `trackers/devhub.py` use `source_ref` through `GET /projects/:key/issues?source_ref=` and `POST /projects/:key/issues` (`devhub-tracker.md`, "Selective import"); a replay of the same content converges on the existing issue, different content or a coordinate held by another issue is `TrackerConflictError`. Server: DEVHUB-105 merged as `b0231d9` (`b0231d9:docs/TRACKER_API.md`, "Selective import (DEVHUB-105)"; `findImportedTrackerIssue` and `createTrackerIssue` of `b0231d9:src/tracker/store.ts`, observed in source). The test is `@need` since PAT-319 and was observed green on a disposable instance built from `b0231d9` ("Live run of PAT-319"). Limits, observed in source: the server accepts only `backlog` or `ready` as creation state (409 `invalid_transition`) (an imported issue in another state is created in `backlog` with a recorded exception); the target path (`migration_preflight`, `migration_supported_attributes`) is PAT-324, row DHN-43 |
| DHN-35 | Selective import: relations between imported issues through the staged port | `test_dhn_35_imported_issues_are_linked_through_the_staged_port` | green | - | adapter, PAT-314: `migration_link_issue` of `trackers/devhub.py` refuses an archived project, checks both ids and posts the link only when the source does not already carry it, in either stored direction (`devhub-tracker.md`, "Staged migration links"); the live test links two issues created normally, because the import itself is DHN-34 (maintainer decision of 2026-10-20), and checks the replay and the inverse end. Observed green in the live run of PAT-314 (2026-10-20, disposable instance at `1227841`) |
| DHN-36 | Selective import: a needed ADR keeps its foreign id (for example `FOUNDRY-ADR-0026`), status and exact body | `test_dhn_36_adr_is_imported_with_its_foreign_id_status_and_exact_body` | green | - | adapter, PAT-319: `import_adr`, `migration_import_adr`, `migration_import_adrs` and `migration_find_adr` of `trackers/devhub.py` use `POST /projects/:key/adrs/import` and the `source_ref` / `foreign_id` lookups; the foreign id is the port `Adr.id` of an imported ADR (`devhub-tracker.md`, "Selective import"). Server: DEVHUB-105 merged as `b0231d9` (`importTrackerAdr` of `b0231d9:src/tracker/store.ts`, observed in source); the import needs no `X-Foundry-Proof`, a deviation declared by DevHub. The test is `@need` since PAT-319 and was observed green on a disposable instance built from `b0231d9` ("Live run of PAT-319"). Limits: the lifecycle routes of the adapter (`set_adr_status`, `link_adr_issue`, `read_adr_issue_relations`) resolve the server id from the project listing by port id and refuse an id of another project (`devhub-tracker.md`, "Selective import"); a non-empty `supersedes` is refused at import, for `migration_import_adr` too (`migration_import_adrs` leaves out one that the predecessor's `superseded_by` covers); an imported ADR whose foreign id does not carry the project key (for example `FOUNDRY-ADR-0026`) is refused by the binding check of those routes, so it cannot change status, be linked or read for relations through the adapter. `import_adr` refuses before the write a `foreign_id` of the form `<KEY>-ADR-NNNN` equal to the server id of a native ADR of the project (two ADRs with one port id, `pagination_repeated` otherwise, for good); the server id of another imported ADR is accepted, its port id being its own foreign id (PAT-315, observed on a disposable instance at `6721ad8`, "Live run of PAT-315"). DEVHUB-111, merged as `16b6888` (announced by the DevHub agent on 2026-10-21): an import whose foreign id is exactly `<KEY>-ADR-NNNN` takes that number when it is free and advances the counter past it, and answers 409 `resource_conflict` when another ADR of the project holds it, which the adapter raises as `TrackerConflictError`; a foreign id of another form still takes the counter number. Observed at `16b6888` ("Live run of PAT-315"); on a server older than `16b6888` (DevHub production runs `6721ad8`, as stated by the DevHub agent on 2026-10-21, not observed) a later native creation taking a number already used as a foreign id is not prevented |
| DHN-37 | Reconcile an interrupted issue creation without recreating it (Epic cadencer) | `test_dhn_37_an_issue_creation_can_be_reconciled_without_recreating_it` | green | - | adapter, PAT-313: `reconcile_issue_creation` of `trackers/devhub.py` reads the issue once and validates it (`devhub-tracker.md`, "Issue creation reconciliation"), so `campaign_cadence.missing_capabilities` no longer reports `issue_creation_reconciliation` (`tests/test_devhub_tracker.py`); the live test exercises it on a parented issue and checks the replay of answer 9; observed green in the live run of PAT-313 (2026-10-20, disposable instance at `1227841`). Limit: the reference must be an issue id, DevHub has no read by key (answers 9 and 16) |
| DHN-38 | Append-only lifecycle capability required by the Epic cadencer (PAT-ADR-0026) | `test_dhn_38_lifecycle_is_append_only_for_the_cadencer` | green | - | adapter, PAT-309: `append_only_lifecycle_supported` is `True` and `read_proof_receipts` reads `GET /issues/:id/proof-receipts` (`trackers/devhub.py`; obligations of the flag and their DevHub source in `devhub-tracker.md`, "Append-only lifecycle receipts"); server `2edcea2:src/tracker/store.ts:1735-1744` and `route.ts:995-1010`. Observed green in the live run of PAT-309 (2026-10-20, disposable instance at `2edcea2`). At `809ca94` the receipt listing ordered ids as text (receipts `10` before `9`), and the adapter refused the read (`pagination_repeated`): observed on a fresh instance, question 17; fixed in DEVHUB-107 (merge `1227841`) and observed green on a fresh instance at `1227841` in the live run of PAT-313, whose first pass crossed the `9` to `10` boundary on one issue. The adapter does not sort receipts. The reader accepts the extended receipt form of DEVHUB-106 since PAT-318; observed green against a server carrying it in "Live run of PAT-318" (`f347f38`) |
| DHN-39 | Read a mapped release scope without approximate discovery | `test_dhn_39_a_mapped_release_scope_is_read_without_approximate_discovery` | green | - | adapter, PAT-309: `read_release_scope` reads `GET /versions/:id` for the Version id stored in `project.extra["release_ids"]` (`trackers/devhub.py`, `devhub-tracker.md`, "Release scope"); the test creates a Version with `POST /projects/:key/versions` and maps its id (answer 11), no placeholder; the Version creation is limited to a service actor on the server (`2edcea2:src/app/api/tracker/v1/[...path]/route.ts:1401`) and the suite credential passed it. Observed green in the live run of PAT-309 (2026-10-20, disposable instance at `2edcea2`) |
| DHN-40 | DevHub accepted as a V1 tracker of the repository | `test_dhn_40_devhub_is_a_v1_tracker_of_the_repository` | green | - | foundry, PAT-323 (PAT-ADR-0031): `registry._V1_TRACKERS` holds `devhub` (`registry.py`); the checkout is resolved through the V1 marker and re-read on the server (`resolve_checkout_project`, `trackers/devhub.py`); the basename pilot path is retired. The test asserts the membership only; DHN-41 covers `verify_project_identity`. Observed green in the live run of PAT-323 (2026-10-21, disposable instance at `8028000`), with `registry bootstrap devhub` run against the same instance ("Live run of PAT-323") |
| DHN-41 | Repository bootstrap: read back the exact project identity before publishing a binding (`repository-bootstrap` of `tracker-contract.v1.json`) | `test_dhn_41_repository_bootstrap_reads_back_the_project_identity` | green | - | adapter, PAT-314: `verify_project_identity` of `trackers/devhub.py` reads `GET /projects/resolve` and compares key, id and canonical repository (`devhub-tracker.md`, "Project identity read-back"); the live test checks the bound project and a foreign id. Since PAT-323 `registry bootstrap devhub` makes this read before publishing (observed in "Live run of PAT-323"). Observed green in the live run of PAT-314 (2026-10-20, disposable instance at `1227841`) |
| DHN-42 | Under local accounts the Foundry service credential can create its project and use it (issue create and read) | `test_dhn_42_service_credential_can_create_and_use_its_project_under_local_accounts` | green | - | server, observed by the DevHub agent: `2edcea2:src/access/domain.ts:139-152` lets the environment service credential (actor `foundry`, `tracker:write`) create a project when an active owner exists, and `2edcea2:src/tracker/store.ts:1341-1350` binds it to that project only and makes every active owner its `admin` (DEVHUB-102, merge `3da571f`); XPASS reported at `2edcea2` on an instance with local accounts (answer 14); history: 403 `insufficient_scope` at `ff22b3a`; observed by Foundry in "Live run of PAT-303" |
| DHN-43 | Selective cutover target: a manifest is captured to DevHub, copied and read back (FOUNDRY-ADR-0026; `cross-tracker-live-work-copy` of `tracker-contract.v1.json`, and the ADR import of a corpus with neither source-only issue references nor `unknown` relations; a corpus with either is DHN-46) | `test_dhn_43_a_manifest_is_captured_copied_and_verified_to_devhub` | green | - | adapter, PAT-324: `migration_preflight`, `migration_attribute_exceptions`, `migration_supported_attributes`, `migration_prepare_adr` and the issue relations read by `migration_export_adrs` in `trackers/devhub.py` (`devhub-tracker.md`, "Selective cutover target"). Observed green in the live run of PAT-324 (2026-10-21, disposable instance at `12ca158`, "Live run of PAT-324"), from a scripted source tracker with an Epic, its child and two ADRs without source-only issue references nor `unknown` relations, so it covers the live-work copy and not the source-only references and `unknown` relations of the ADR import cell (DHN-46, PAT-327); the `cutover` CLI commands and `activate` were not run live. Server limits, observed in source at `12ca158`: a new issue is created in `backlog` or `ready` only, `priority` is `P0` to `P3`, `estimate` 1 to 21, a parent must be an exact `Epic`; the published workflow is read by `GET /api/tracker/workflow/v1/projects/:key` since PAT-328 (DHN-44, question 19) |
| DHN-44 | Selective cutover target: the preflight reads the published workflow and refuses, before any write and naming the value, a `type`, `priority` or `estimate` value (or a creation state) the first import would refuse as disabled (409 `resource_conflict`, `value_disabled` in the source); a refused or malformed workflow read is a refusal naming the cause | `test_dhn_44_the_preflight_refuses_a_value_the_published_workflow_disables` | green | - | adapter, PAT-328: `DevHubTracker._read_workflow` and `_preflight_workflow` of `trackers/devhub.py` (`devhub-tracker.md`, "Selective cutover target"). Green live at `cd23304` ("Live run of PAT-328"). The test publishes a workflow through the executable named by `DEVHUB_LIVE_WORKFLOW_PUBLISH` (the service credential cannot publish) and is skipped without it |
| DHN-45 | Selective cutover target: the preflight refuses, before any write and naming the ADR, an ADR whose replacement is `proposed`, and accepts an `accepted` or `deprecated` replacement (DEVHUB-118) | `test_dhn_45_the_preflight_refuses_a_proposed_adr_replacement` | green | - | adapter, PAT-328: `_preflight_adrs` of `trackers/devhub.py`. Rule read in DevHub source at `cd23304` (`store.ts:2307-2309`, relayed by the DevHub agent). Green live at `cd23304` ("Live run of PAT-328") |

| DHN-46 | Selective cutover target: an ADR corpus with a source-only issue reference and `unknown` relation families is captured, copied, read back exactly and replayed; the server refuses a family sent both known and unknown (DEVHUB-122; `cross-tracker-adr-import` of `tracker-contract.v1.json`) | `test_dhn_46_an_adr_corpus_with_a_source_only_reference_and_unknown_relations_is_copied` | green | - | adapter, PAT-327: `import_adr`, `_prepare_adr_import`, `_snapshot_import_arguments`, `_preflight_adrs` and `migration_export_adrs` of `trackers/devhub.py` (`devhub-tracker.md`, "Source-only references and unknown relations"); server: `source_only_refs` and `unknown_relations` of `POST /projects/:key/adrs/import`, merge `3f0cf4b98e60a04154cf051e4110cbf29094d381` (DEVHUB-122). Green live at `3f0cf4b` ("Live run of PAT-327") |
| DHN-47 | Selective cutover target: `cutover preflight`, `copy` and `activate` run toward a new project of the disposable instance from a temporary git checkout whose canonical repository is the project's registration, with the registry isolated in a temporary `FOUNDRY_DATA`; the published registry entry, the marker and the manifest phase are read back, a manifest for another project is refused with the registry unchanged, and an exact replay of `activate` converges (FOUNDRY-ADR-0026, PAT-ADR-0032; `tombstone-archived-source-after-switch` of `tracker-contract.v1.json`) | `test_dhn_47_a_verified_manifest_is_activated_toward_a_new_devhub_project` | green | - | foundry, PAT-329: `cutover.main` (`activate` path), `registry.cutover_repository_tracker`, `registry.stage_repository_cutover_target` and `DevHubTracker.verify_project_identity` (`devhub-tracker.md`, "Selective cutover target"). Green live at `3f0cf4b` ("Live run of PAT-329"); the offline counterpart is `test_activate_toward_devhub_publishes_the_registry_entry_and_the_marker` in `tests/test_tracker_migration.py`. It refuses to run if the resolved registry path is not under its temporary directory or is under `~/.config/foundry`; the source is a fake YouTrack-named transport registered in that temporary registry, the target is the live adapter. No new environment variable ("Live run of PAT-329" below) |
| DHN-48 | The command `issue accept <ID> --human-verdict=accepted --decision-file=<path>` and its read-only `--status` accept a non-code issue on DevHub (PAT-ADR-0025 conditions, PAT-ADR-0033): `aucune acceptation humaine` before, `→ done · acceptation humaine · devhub-human-acceptance` after one receipt, `accepté` with the decision digest in `--status`, an exact replay converges without a second receipt, another decision is refused; `human-acceptance-decision` of `tracker-contract.v1.json` | `test_dhn_48_the_issue_accept_command_and_its_status_run_against_the_live_adapter` | green | - | adapter, PAT-330: `issue.accept` and `write.issue_acceptance_state` through `DevHubTracker.accept_issue` and `observe_human_acceptance` (`devhub-tracker.md`, "Acceptance override and human acceptance"). Green live at `3f0cf4b` ("Live run of PAT-330"); offline counterparts in `tests/test_devhub_tracker.py` ("PAT-330"). The test injects the tracker and the project binding (`foundry.tracker`, `write.mutation_project`), so it reads and writes no registry; it also asserts that `observe_epic_closure_child` reports the accepted issue `done` with its criteria counted as satisfied; no new environment variable ("Live run of PAT-330" below) |

Counts (equal to the table): 48 needs; 0 red; 0 unknown; 48 green (DHN-01 to DHN-48).

### Live run of PAT-303

Run on 2026-10-20 (observed), DevHub revision `2edcea222eb47e2751b28c7ba461a49f78c09d1c`,
on two instances brought up for this run from an image built by Foundry itself (`git archive`
of that revision, then `docker build -f deploy/dogfood/Dockerfile` with
`DEVHUB_DEPLOY_REVISION` set to it; `GET /api/health` answered that `revision`), with the
compose file and `app.env` of "Bring up a disposable instance" including its hardening
lines, on loopback ports other than 3305, never production:

- Pass 1, instance without local accounts (`select local_accounts from account_workspace`
  answered false), all tests: 27 passed, 14 xfailed, 1 skipped (DHN-42), 0 failed.
  DHN-27 and DHN-28 passed; the 14 `xfailed` were the 14 red rows of the table at that date (before PAT-309).
- Pass 2, instance with local accounts (that query answered true after one owner was
  bootstrapped with DevHub `scripts/accounts-operator.ts`, as `deploy/qa/up.sh` does),
  `DEVHUB_LIVE_LOCAL_ACCOUNTS=1`, `-k dhn_42`: 1 passed, 41 deselected. DHN-42 passed.

DHN-27, DHN-28 and DHN-42 were then set to `green`.

### Live run of PAT-313

Run on 2026-10-20 (observed), patolabs-plugins branch of PAT-313, same procedure as "Live
run of PAT-303" (image built by Foundry from `git archive`, `GET /api/health` answering the
revision, loopback, never production), instances without local accounts, all tests (DHN-42
skipped):

- DevHub `809ca942795cbe9e55502113a56627749299be69`, fresh instance, first pass: 31 passed,
  1 failed, 9 xfailed, 1 skipped. The failure was DHN-38: `read_proof_receipts` raised
  `pagination_repeated` because the server answered receipts `10` then `9` (question 17).
  Seven later passes on the same instance: 32 passed, 9 xfailed, 1 skipped (receipt ids
  then all had two digits).
- DevHub `122784178834e2cd6204db139aa760b89692f3bb`, fresh instance, two passes: 32 passed,
  9 xfailed, 1 skipped each. In the first pass one issue held receipts `9` to `12`.
- Same `1227841` instance, after DHN-24 moved to `@need` (PAT-ADR-0030): 33 passed,
  8 xfailed, 1 skipped.

DHN-37 and DHN-24 were then set to `green`.

### Live run of PAT-314

Run on 2026-10-20 (observed), patolabs-plugins branch of PAT-314, same `1227841` instance,
all tests (DHN-42 skipped), after DHN-35 and DHN-41 moved to `@need`: 35 passed, 6 xfailed,
1 skipped. DHN-35 and DHN-41 were then set to `green`.

### Live run of PAT-316

Run on 2026-10-20 (observed), patolabs-plugins branch of PAT-316, same `1227841` instance
as the last pass of PAT-313, all tests (DHN-42 skipped), with the DHN-24 test giving its
children criteria:

- Without the fix of `write.close_epic`, `-k dhn_24`: 1 failed, the closure raising
  `invalid_response` before the POST.
- With the fix: 33 passed, 8 xfailed, 1 skipped. Counts unchanged.

### Live run of PAT-318

Run on 2026-10-20 (observed), patolabs-plugins branch of PAT-318, on a fresh instance brought
up for this run from DevHub `f347f38574ea0882938b13a76eb38dc9802d5975` (merge of DEVHUB-106,
same build procedure as "Live run of PAT-303"), all tests (DHN-42 skipped, not rerun at this
revision): 37 passed, 4 xfailed, 1 skipped, twice. DHN-25 and DHN-26 were then set to `green`;
DHN-38 passed against a server writing the extended receipt form. The 404 `route_unavailable`
of a server without DEVHUB-106 was not run (DevHub reply, question 12 (e)).

### Live run of PAT-319

Run on 2026-10-21 (observed), patolabs-plugins branch of PAT-319, on a fresh instance brought
up for this run from DevHub `b0231d9a0a8d03912483a014eaaad06ba7fd2f72` (merge of DEVHUB-105,
same build procedure as "Live run of PAT-303"), all tests (DHN-42 skipped, not rerun at this
revision): 39 passed, 2 xfailed (DHN-32, DHN-40), 1 skipped, twice. DHN-34 and DHN-36 were then
set to `green`. The server's 422 on a wrong `body_sha256` was not run (the live test checks the
adapter's local refusal only).

After the correction of the first review round, rerun the same day (observed) on another fresh
instance from the same revision: the same counts, once. A one-off script, not part of the suite,
then imported two `proposed` ADRs whose foreign ids carry the project key in reverse order:
the server gave each the other's number as its id. `link_adr_issue`, `read_adr_issue_relations`
and `set_adr_status` on the second one acted on it and left the first one `proposed` with no
relation (`devhub.py::_adr_route_item`).

### Live run of PAT-320

Run on 2026-10-21 (observed), patolabs-plugins branch of PAT-320, on a fresh instance brought
up for this run from DevHub `6721ad8ed405de91fbd804192e0ac24b18d8cf61` (merge of DEVHUB-108,
same build procedure as "Live run of PAT-303"), all tests (DHN-42 skipped, not rerun at this
revision): 40 passed, 1 xfailed (DHN-40), 1 skipped, twice. DHN-32 was then set to `green`. A
one-off script, not part of the suite, then checked on the same instance that `supersede_adr`
refuses a `proposed` replacement with no write, that a replay sends no second write, and that a
source version the server no longer holds is a `TrackerConflictError`; each was observed.

### Live run of PAT-327

Run on 2026-10-22 (observed) on a fresh disposable instance built from DevHub
`3f0cf4b98e60a04154cf051e4110cbf29094d381` (merge of DEVHUB-122; never production, never an
instance built from `a314c0c`, which lacks it), with the build procedure of "Live run of
PAT-303" and `DEVHUB_LIVE_WORKFLOW_PUBLISH` as in "Live run of PAT-328": 45 passed, 1 skipped
(DHN-42, local accounts only), twice. DHN-46 passed in both runs and was then set to `green`.
The instance was removed (`down -v`).

### Live run of PAT-329

Run on 2026-10-22 (observed) on a fresh disposable instance built from DevHub
`3f0cf4b98e60a04154cf051e4110cbf29094d381` (merge of DEVHUB-122; never production), build
procedure of "Live run of PAT-303", `DEVHUB_LIVE_WORKFLOW_PUBLISH` as in "Live run of PAT-328":
46 passed, 1 skipped (DHN-42, local accounts only), twice. DHN-47 passed in both runs and was
then set to `green`. It sets `FOUNDRY_DATA` itself to a temporary directory
(`registry.data_dir`, `registry.py`); the operator's own registry file was checked unchanged
after the runs (modification time unchanged). The instance was removed (`down -v`).

### Live run of PAT-330

Run on 2026-10-22 (observed) on a fresh disposable instance built from DevHub
`3f0cf4b98e60a04154cf051e4110cbf29094d381` (merge of DEVHUB-122; never production), build
procedure of "Live run of PAT-303", `DEVHUB_LIVE_WORKFLOW_PUBLISH` as in "Live run of PAT-328":
47 passed, 1 skipped (DHN-42, local accounts only), twice. DHN-48 passed in both runs and was
then set to `green`. The instance was removed (`down -v`).

### Live run of PAT-324

On a fresh disposable instance built from DevHub `12ca15849e3d12420db5669eb2fdbd40fb883bf6`
(never production), 2026-10-21, with the build procedure of "Live run of PAT-303": the live
suite gave 42 passed, 1 skipped (DHN-42, local accounts only), observed. DHN-43 captured a
manifest from a scripted source (an Epic, its child and two ADRs, one superseding the other)
to a new DevHub project, copied it, read it back with `verify_targets`, and replayed the copy
onto the same objects. The `cutover` CLI commands, `activate` and a real source tracker were
not part of the run. The instance was then removed (`down -v`).

### Live run of PAT-328

On a fresh disposable instance built from DevHub `cd23304cff209827e9d78dc5c7d1e9583ff91053`
(merge of DEVHUB-118; never production), 2026-10-22, with the build procedure of "Live run of
PAT-303", observed:

1. An executable read the project key as its first argument and the new `settings` JSON on
   standard input, and inserted a revision 2 row into the instance database (columns read in
   `drizzle/0023_workflow_settings.sql` at `cd23304`; the project is found by
   `projects.tracker_ticker`). With the compose file, user and database of the instance:

   ```sh
   #!/bin/sh
   set -eu
   settings=$(cat)
   docker compose -f <compose-file> exec -T db psql -U <user> -d <database> \
     -v ON_ERROR_STOP=1 -v key="$1" -v settings="$settings" <<'SQL' >&2
   INSERT INTO tracker_workflow_revisions
     (project_id, version, settings, actor, credential_identity, preview, before_resources, after_resources)
   SELECT id, 2, :'settings'::jsonb, 'live-test', 'live-test', '{}', '[]', '[]'
   FROM projects WHERE tracker_ticker = :'key';
   SQL
   ```

2. With `DEVHUB_LIVE_WORKFLOW_PUBLISH` naming that executable, the live suite gave 44 passed,
   1 skipped (DHN-42, local accounts only), twice. DHN-44 and DHN-45 are green. After the
   first run the database held one revision 2 for the `LV*` project of DHN-44, with `Bug`
   disabled in `fields.type`, so the workflow was published and not skipped.
3. The instance was then removed (`down -v`).

### Live run of PAT-326

On a fresh disposable instance built from DevHub `12ca15849e3d12420db5669eb2fdbd40fb883bf6`
(merge of DEVHUB-116; never production), 2026-10-21, with the build procedure of "Live run of
PAT-303": the live suite gave 41 passed, 1 skipped (DHN-42, local accounts only), observed.
DHN-31 checked the 409 `invalid_transition` of the status route and that the ADR stayed
`accepted` at the same version. A first run of the same branch failed DHN-32 only: its last
step still superseded an ADR through the status route, now refused; that step was removed
(row DHN-32). The instance was then removed (`down -v`).

### Live run of PAT-323

On a fresh disposable instance built from DevHub `8028000c21861f56b4e49b6422559930a0a368e5`
(never production), 2026-10-21:

- The live suite: 41 passed, 1 skipped (DHN-42, local accounts only). DHN-40 is green.
- `registry bootstrap devhub` was then run once through `tooling/foundry_cli.py`, in a
  throwaway Git checkout whose origin matches a project provisioned on that instance, with an
  isolated `FOUNDRY_DATA` and an empty `FOUNDRY_CONFIG` (observed):
  - a wrong project id is refused naming foreign or contradictory coordinates; nothing is
    written (no registry file, no marker);
  - the exact coordinates publish the registry entry and `.foundry/tracker.json`; an exact
    replay converges;
  - `registry selection --require-v1` returns the DevHub binding, and `FOUNDRY_TRACKER=linear`
    does not replace it;
  - `query backlog` resolves the bound project through the marker;
  - after the project was set to `manual` directly in the disposable database (the human
    conversion route needs an interactive session), a new bootstrap is refused naming the
    mode `manual`, with nothing written.
- The instance was then removed (`down -v`).

### Live run of PAT-315

Run on 2026-10-21 (observed), patolabs-plugins branch of PAT-315, all tests (DHN-42 skipped, not
rerun), on two fresh instances brought up for this run with the build procedure of "Live run of
PAT-303": from DevHub `6721ad8ed405de91fbd804192e0ac24b18d8cf61`, 40 passed, 1 xfailed (DHN-40),
1 skipped, twice; from `16b6888395c65455e220da85f8cc2c3d09a85f1d` (merge of DEVHUB-111), the same
counts, once. These runs include the additions to the tests of DHN-24 and DHN-32. One-off
scripts, not part of the suite, then observed on each instance:

- an import whose foreign id equals the server id of a native ADR is refused with nothing
  written (`TrackerConflictError`, before the write);
- two ADRs imported in reverse order with foreign ids that carry the project key: at `6721ad8`
  the server gave each the other's number, and the three lifecycle routes acted on the right
  ADR; at `16b6888` each kept its own number;
- `supersede_adr` refuses a `proposed` replacement with no write, a replay sends no second
  write, and a stale source version is a `TrackerConflictError`.

At `16b6888` only: an import onto a number held by an ADR imported with a foreign id of another
form was refused by the server (409, `TrackerConflictError`) with nothing written, and after an
import as `<KEY>-ADR-0005` the next native ADR took `<KEY>-ADR-0006`.

### Not covered, on purpose

- The tombstone of an archived *source* (`tracker-contract.v1.json`,
  `tombstone-archived-source-after-switch`): DevHub is the target of the cutover, not its
  source. The DevHub cell covers the archive guard only, offline (PAT-323); no live test.
- A full history migration: excluded by FOUNDRY-ADR-0026.
- The deployed DevHub version: no test; it is question 1.

### Closing a gap

When a red need is met, its strict `xfail` test XPASSes and the run fails. The owner then:
(1) replaces `@gap(...)` by `@need(...)` on that test, after checking that the test really
exercises the behaviour (DHN-26 builds a complete decision document since PAT-318; DHN-33 reads the relation through `read_adr_issue_relations` since PAT-312, and checks the refusal on a decided ADR; PAT-303 checked this for DHN-27, DHN-28 and DHN-42, whose tests exercise the abandon and the return to `backlog` from `in-progress` only: the `review` side of DHN-27 and DHN-28 rests on answer 5, not on a Foundry test); (2) sets the page status
to `unknown`, then `green` after a run against a DevHub instance; (3) updates the counts
above. `tests/test_devhub_requirements.py` keeps the page and the decorators equal.

## Questions for the DevHub agent

Numbered so an answer can cite them. "Unknown" means Foundry has no evidence either way. Each answer below is a reformulation of the reply of the DevHub agent of 2026-10-20 (untracked file outside this repository), whose citations are `<commit>:<path>:<line>`; "observed" and "deduced" are as that reply states them, not re-verified here except where the page says so.

1. **Deployed version**: how can a client read which DevHub build it talks to? DevHub's
   `/api/health` returns a `revision` (`src/app/api/health/route.ts:16-19,43` at `ff22b3a`, same lines at `2edcea2`,
   observed in source; `deploy/qa/up.sh:68-77` reads it), but that value is the
   `DEVHUB_DEPLOY_REVISION` the operator set, or `unknown` when it does not match
   `^[0-9a-f]{7,64}(?:-d[0-9a-f]{12})?$` (a tracker-only instance then reports `degraded`,
   `route.ts:30-33`), not a measure of the build, and the adapter does not read it
   (observed: no `api/health` in this repository's tools). Is it a stable client contract?
   The run of 2026-10-20 knew its revision because the image came from `deploy/qa/up.sh` at
   that revision and `DEVHUB_DEPLOY_REVISION` was set to it.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): `/api/health` returns `revision`, the operator-set `DEVHUB_DEPLOY_REVISION` or `unknown` (`2edcea2:src/app/api/health/route.ts:16-19,43`; `unknown` makes a tracker-only instance not ready, `route.ts:30`; observed in source). The image build takes it as a build argument (`deploy/dogfood/Dockerfile`), so it is what the operator built, not a measured hash (deduced). No ADR declares it a client contract: its stability is unknown. Recommended use: compare it to an expected commit prefix and treat `unknown` as not qualified.
2. **Disposable instance**: what is the supported way to get a throwaway instance
   (database reset, compose profile)? What scopes must `DEVHUB_LIVE_TOKEN` carry for the
   tests above? Is there a way to delete or archive the `LV*` projects a run leaves?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): `main` has no supported throwaway procedure; `deploy/qa/` (DEVHUB-100) is not merged (observed). The DevHub agent used `git archive`, `docker build -f deploy/dogfood/Dockerfile`, its own compose project and `down -v`, which matches "Bring up a disposable instance". Scopes needed: `tracker:read`, `tracker:write`, `tracker:transition`, `tracker:adr`, carried by the environment `DEVHUB_TRACKER_TOKEN` (observed: the suite passes with it). Deletion: `DELETE /admin/projects/:key` with the admin token after the deletion-plan preflight (`2edcea2:docs/TRACKER_API.md:213,295,358`), but a project holding original Epic closures or issue proof receipts answers 409 `resource_conflict` (`2edcea2:src/tracker/project-lifecycle.ts:283-298`, observed in source). Deduced: an `LV*` project that went through DHN-18, DHN-19 or DHN-22 stays, so the cleanup is to drop the whole disposable instance.
3. **DHN-08**: what does the free-text `query` of the issue listing match (title, body,
   id)? The test expects exactly the one issue whose title holds a random token.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Case-insensitive substring of the issue `title` or `readable_id`, not the body (`2edcea2:src/tracker/store.ts:598-612`, `ILIKE` at 605 and 611; observed in source, DHN-08 passes).
4. **DHN-14**: is there any cap on comments, or on the response size, that would truncate
   an issue read? The adapter refuses a response over 8 MiB (`_MAX_RESPONSE_BYTES`).
    Answer (DevHub agent, 2026-10-20, `2edcea2`): The issue read loads every comment with no row limit (`2edcea2:src/tracker/store.ts:458`); per-item limits are 50,000 characters for a comment `text` (`2edcea2:src/tracker/contract.ts:74`) and 200,000 for an issue body (`contract.ts:79,99`); observed in source. No server cap on response size: exceeding the 8 MiB adapter limit is possible with very many large comments (deduced, not run).
5. **DHN-27 / DHN-28**: Foundry abandons and returns to `backlog` issues that are already
   started, with an expected predecessor state. Will DevHub allow `dropped` from
   `in-progress` and `review`, and `backlog` from `in-progress` and `review`, for a
   Foundry-governed project, or is another route intended?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Done in DEVHUB-103 (merge `2e5d3ff`): the V1 service route allows `in-progress` and `review` to `dropped` and `backlog` (`2edcea2:src/tracker/workflow.ts:12-17`, `canServiceTransition`); the canonical table, the human UI and the published workflows are unchanged. The expected predecessor is enforced by `expected_version` (409 `version_conflict`). Observed by the DevHub agent: XPASS of DHN-27 and DHN-28 at `2edcea2`.
6. **DHN-31**: does `POST /adrs/:id/status` accept `superseded` for a Foundry-governed
   project, and with which replacement information?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): `POST /adrs/:id/status` accepts `superseded` only from `accepted` (`2edcea2:src/tracker/contract.ts:274-288`, `assertAdrTransition`; handler `src/tracker/store.ts:2015`; observed in source, DHN-31 passes). It carries no replacement information; naming one (DHN-32) is not on that route.
    Historical since PAT-326: this answer was true up to `8028000`. From DEVHUB-116 (DevHub `12ca158`) the route refuses `superseded` with 409 `invalid_transition`, without effect (`12ca158:src/tracker/contract.ts:118-125`, `12ca158:src/tracker/store.ts:2365`, read in source); only `POST /adrs/:id/supersede` supersedes. DHN-31 is redefined accordingly.
7. **DHN-33**: Foundry appends an ADR-to-issue relation after an ADR is accepted. DevHub
   freezes `linked_issue_ids` once decided (`DEVHUB-73`). Is a relation outside the frozen
   text possible (a relationship route that accepts writes), or must Foundry stop doing it?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Not possible. Once an ADR leaves `proposed`, a trigger refuses any change of `title`, `body` or `linked_issue_ids` (`2edcea2:drizzle/0021_adr_decisions.sql:39-48`), and no relationship route accepts writes on a decided ADR (observed in source). Recommendation of the DevHub agent: set the ADR-to-issue relations before acceptance. Changing this contradicts DEVHUB-73.
    DevHub human decision of 2026-10-20: not done on the server; the decided-ADR freeze (DEVHUB-73) stays, and no DevHub work is planned. Foundry sets ADR-to-issue relations before acceptance. At `2edcea2` nothing structured carries a relation from the issue side after acceptance: an issue link targets an issue id only (`src/tracker/contract.ts:67-71`, `createLinkSchema`; observed in source), and the edit route refuses a decided ADR (`src/tracker/store.ts:1966`). What the decision calls the issue carrying the relation is therefore, deduced, the issue's own text or comment (issues are not frozen, per the decision); that carrier is not implemented or tested by Foundry (unknown). The need DHN-33 is requalified to "set the relation before acceptance, read it after acceptance"; the issue-side carrier is outside that test.
8. **DHN-34 / DHN-36**: for selective import, can an issue and an ADR carry an exact
   foreign source coordinate (for example `linear:issue:PAT-123`) that a lookup returns,
   and can an ADR keep its original id such as `FOUNDRY-ADR-0026` while the server's own
   ids stay `<KEY>-ADR-NNNN`? Foundry needs a replay that creates nothing twice.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Unsupported: ADR ids are generated `<KEY>-ADR-NNNN` (`2edcea2:src/tracker/store.ts:1999`) and neither issues nor ADRs have a foreign source coordinate or a lookup by one (observed in source). It would be a V1 contract extension (DEVHUB-ADR-0003), not done at `2edcea2`.
    DevHub human decision of 2026-10-20: accepted as DEVHUB-105 (merged on 2026-10-21 as `b0231d9`, see the announcement below). Planned at the time: a `source_ref` unique per project on issues and ADRs, with exact lookup `GET /projects/:key/issues?source_ref=`; an identical replay returns the existing resource and a divergent payload a typed 409; an ADR import route carrying the `source_ref`, a `foreign_id` kept as an alias (for example `FOUNDRY-ADR-0026`), the historical status, the source dates, a verified body sha256 (422 when wrong) and the supersession links; an imported ADR is frozen like any decided ADR. The server id stays `<KEY>-ADR-NNNN`, so the adapter must expose the alias as the id, adapter-side by decision.
    DevHub announcement of 2026-10-21 (reformulated; merged on DevHub `main` as `b0231d9`; the routes below were read by Foundry in `b0231d9:docs/TRACKER_API.md` and `b0231d9:src/tracker/store.ts`, observed in source): `source_ref` is optional and immutable on an issue created by `POST /projects/:key/issues`, unique per project; `GET /projects/:key/issues?source_ref=` is an exact lookup of zero or one item; `POST /projects/:key/adrs/import` imports an ADR with `source_ref`, `foreign_id`, status, source dates and a verified `body_sha256`, and `GET /projects/:key/adrs?source_ref=|foreign_id=` looks one up. An identical replay answers 200 and a divergent one 409 `resource_conflict`. Deviation declared by DevHub: the ADR import route takes no `X-Foundry-Proof`, even for a decided status. Foundry observed that the server checks a `foreign_id` only for shape (non-empty, 120 characters at most, no whitespace or control character: `foreignIdSchema`, `b0231d9:src/tracker/contract.ts:95`) and for uniqueness among foreign ids of the project (`b0231d9:src/tracker/store.ts:2256-2259`), so it does not refuse a `foreign_id` shaped like a native id; the adapter keeps the route id apart from the port id for that case (`devhub-tracker.md`, "Selective import"). PAT-319 implements the adapter side; DHN-34 and DHN-36 are green since "Live run of PAT-319".
9. **DHN-37**: what does the server guarantee about an `Idempotency-Key` replay after a
   lost response, and can a client later read the result by key or by title?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): A replay with the same `Idempotency-Key` and payload returns the stored response and status with `replayed: true` (`2edcea2:src/tracker/store.ts:1328-1330`); a different payload answers 409 `idempotency_conflict` (`store.ts:1322`); keys are bound to the credential identity (observed in source, DHN-07 passes). Deduced: after a lost response, replaying the same request with the same key recovers the result without a second effect. No route reads a result by key alone; search by title uses `?query=` (answer 3).
    Foundry, PAT-313: the adapter reconciles by issue id, read-only, not by replay: a create body that names a parent must carry its `parent_version` (`2edcea2:src/tracker/store.ts:1430-1432`, `1553-1557`) and creating the child raises the parent version by one (`store.ts:1494`, observed in source), so a later replay needs the original `parent_version`, which the adapter does not keep after the call (answer 16: an identical replay succeeds even if the parent changed since). The adapter keeps its random per-call key (`DevHubTracker._idempotency_factory`) and its two attempts with that same key inside one call. Consequence: a creation whose response is lost beyond those attempts leaves no reference to reconcile by (unknown whether the campaign then creates twice; it scans the project listing by title first). The `source_ref` lookup of DEVHUB-105 (answer 8, merged as `b0231d9`) would give that reference for an import; new question 16.
10. **DHN-38**: what should "append-only lifecycle" mean on DevHub (review and done proof
    history readable and never rewritten)? Foundry needs to tell a cadencer which receipts
    exist for an issue.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Server side done in DEVHUB-104 (merge `2edcea2`). Each successful proven `review` or `done` transition appends one row to `tracker_issue_proof_receipts` in the same transaction (`2edcea2:src/tracker/store.ts:1735-1744`); an idempotent replay or a refused transition appends nothing; a trigger refuses UPDATE and DELETE (`2edcea2:drizzle/0025_issue_proof_receipts.sql:24-33`). Read: `GET /api/tracker/v1/issues/:id/proof-receipts?cursor=&limit=`, oldest first, scope `tracker:read` (`2edcea2:src/app/api/tracker/v1/[...path]/route.ts:995-1010`, `store.ts:1748-1774`; fields and limits in `2edcea2:docs/TRACKER_API.md`, "Issue proof receipts"). Observed by the DevHub agent in its integration tests. DHN-38 stays a gap, now adapter-only: the flag `append_only_lifecycle_supported` and the reading of the receipts.
    Foundry, PAT-309: the flag and the reading are implemented (`devhub-tracker.md`, "Append-only lifecycle receipts"); one obligation the answer does not mention is that DevHub answers `review -> review` with no new receipt (`2edcea2:src/tracker/store.ts:1686`), which `set_state` handles; green, observed in the live run of PAT-309 (2026-10-20, 29 passed, 12 xfailed, 1 skipped).
11. **DHN-39**: how is a release or Version created and addressed over the tracker v1 API,
    so that a test can map one and read its scope?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Versions exist in V1: `POST /projects/:key/versions`, `GET /versions/:id`, scope replace (`2edcea2:src/tracker/contract.ts:203-243`); ids are `<KEY>-VERSION-N` (`2edcea2:src/tracker/version-store.ts:359`); observed in source. Version names are not unique, so map by id (deduced). No server change is needed: the adapter stores the id and reads `GET /versions/:id` (deduced). DHN-39 stays a gap, adapter-only.
    Foundry, PAT-309: implemented as stated (`devhub-tracker.md`, "Release scope"); creating a Version is a service-actor write (`2edcea2:src/app/api/tracker/v1/[...path]/route.ts:1401`), done by the live test with the suite credential; green, observed in the live run of PAT-309 (2026-10-20, 29 passed, 12 xfailed, 1 skipped).
12. **DHN-25 / DHN-26**: does DevHub intend to carry a typed acceptance-override receipt and
    a human acceptance decision without a PR, or will Foundry keep a comment-based fallback?
    The end-to-end `issue merge` with an AC override on DevHub is unknown.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): No typed acceptance-override receipt and no PR-less human acceptance route exist on DevHub (observed: none under `src/` at `2edcea2`). At that date, whether DevHub would add them was unknown and nothing was planned.
    DevHub human decision of 2026-10-20: accepted as DEVHUB-106 (DevHub backlog, not started, no date): two new actions in the proof-receipts table. `acceptance_override` carries a reason and the bound review context (`pr_url`, `head_sha`, `base_sha`, `review_digest`) and is accepted only in `review`, with the state unchanged. `human_acceptance` carries the decision document `foundry.issue-acceptance-decision.v1` and its digest, and moves the issue to `done` without a PR in the same transaction, only for an actor allowed to decide. Both are readable through `GET /issues/:id/proof-receipts`. Route shapes are fixed at implementation time. Foundry keeps the comment-based fallback until DEVHUB-106 merges.
    DevHub announcement of 2026-10-20 (reformulated; DevHub pull request 95, merged on DevHub `main` as `f347f38`; Foundry has not read its diff): `POST /issues/:id/acceptance-override` takes `reason`, `pr_url`, `head_sha`, `base_sha`, `review_digest` and requires the state `review` and coordinates equal to the recorded review context, one override per review generation, state and version unchanged. `POST /issues/:id/human-acceptance` takes `decision`, validated as the document `foundry.issue-acceptance-decision.v1`, and requires state equal to `from_state` (`backlog`, `ready`, `in-progress`), a body digest equal to the stored body, no Epic, no child, no PR and no existing receipt; one transaction sets `done`, adds one to the version and appends the receipt. Both need the scope `tracker:transition`, a version header, an idempotency key and a proof header (actions `acceptance-override` and `human-acceptance`, fields in `devhub-tracker.md`); a human session is refused (403 `authority_boundary`); an identical replay answers `Idempotency-Replayed: true`; errors stay in the V1 vocabulary. Receipts carry `reason`, `decision_digest`, `decision` (null where unused), four actions, and the four coordinates are null only for `human_acceptance`. The decision comes back as jsonb (order and spacing not kept), so Foundry recomputes the digest. Known DevHub limit DEVHUB-87 (deferred): a string with U+0000 or a lone surrogate fails at insert with a server error. PAT-318 implements the adapter side; DHN-25 and DHN-26 are green since "Live run of PAT-318".
    Questions raised by PAT-318, answered by the DevHub agent on 2026-10-20 (reformulated, read by DevHub on its branch `5b81235`, not by Foundry): (a) the server accepts `^[a-z0-9_-]{3,80}$`, the same set Foundry sends (`issue.py` override reason rule), checked in the request contract and by a database constraint. (b) `done` after an override is accepted with unchecked criteria: the transition does not read them. Exception given by DevHub as deduced, not tested: a published project workflow whose `review` to `done` step requires every criterion checked refuses `done`, probably with 503 `tracker_unavailable`; the default project workflow has no such step. (c) a second override of the same generation with a new idempotency key answers 409 `invalid_transition`; the same key with the same payload is a replay (`Idempotency-Replayed: true`); a new `review` transition opens a new generation that accepts a new override. Foundry reads the receipts first and sends neither (a concurrent writer would surface the 409 as an error, fail-closed). (d) after `human_acceptance`, `pr_url` reads null and `ac_done` / `ac_total` are still counted from the body checkboxes, which the route does not change; the state is `done` and the version one higher; the receipt is the proof. The adapter's read-back checks the state and the receipt, not `ac_done`. (e) a server without DEVHUB-106 answers 404 `route_unavailable` on both routes; the adapter maps it to `TrackerCapabilityUnavailableError` (`devhub.acceptance_override`, `devhub.human_acceptance`), nothing written, and `issue merge --allow-incomplete-ac` then refuses before any merge.
13. **Availability**: after a cutover, every Foundry command of the repository depends on
    the one DevHub instance (deduced: a single tracker per repository, FOUNDRY-ADR-0026).
    What is the backup and restore procedure, and how does a client tell a redeployment
    from an outage? No test of this page covers it.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Backup and restore: `2edcea2:docs/DEVHUB-38-BACKUP-RESTORE.md` (daily capture, off-host copy, one restore exercise on a copy, lines 102-112, 154, 289+). The timers were observed enabled on 2026-10-13; their state today is unknown. A client cannot tell a redeployment from an outage except by polling `/api/health`, which answers `startup: "starting"` or `degraded` before `ok` with the new `revision` (observed by the DevHub agent on its instances; the client rule is deduced).
14. **DHN-42**: on an instance with local accounts, `authorizeProjectCreation` refuses
    any actor that is not a human owner or admin, and `authorizeProject` limits a service
    credential to the projects granted to it (`src/access/domain.ts`, read at `ff22b3a`, before DEVHUB-102).
    How does the Foundry service credential obtain the right to create its repository's
    project and then use it: a grant to the service credential, or a documented procedure
    (for example an owner creating the project and granting it)? Without one, a
    repository cannot be bootstrapped under local accounts.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Done in DEVHUB-102 (merge `3da571f`): under local accounts the environment Foundry service credential (actor `foundry`, `tracker:write`) may create a project when an active owner exists (`2edcea2:src/access/domain.ts:139-152`); in the same transaction the credential is bound to that project only and every active owner becomes its `admin`, audited (`2edcea2:src/tracker/store.ts:1341-1350`). Personal keys, service keys and the legacy `human` actor stay refused. Observed: XPASS of DHN-42 at `2edcea2` (DevHub agent run) and `tests/local-accounts.integration.test.ts` there.
15. **DHN-32**: at `2edcea2` the only route that names a replacement is `POST /adr-decisions/adrs/:id` (action `supersede` with an exact `replacement`), limited to an interactive human administrator, and in a `foundry`-governed project it records an intention without changing the status (observed in source, DHN-32 row). The Foundry credential is an ordinary service credential (deduced). Will DevHub give a credential of that kind a route that moves an accepted ADR to `superseded` and names its replacement in the same transaction (for example a `replacement` on `POST /adrs/:id/status`), or must a human decide each supersession on the DevHub side? Until answered, `supersede_adr` is unavailable on DevHub (unknown whether it will change).
    Answer (DevHub agent, 2026-10-20, `1227841`): No. `POST /adrs/:id/status` with `superseded` (service actor, `tracker:adr`, proof action `adr-status`) only sets the status and its strict schema has no replacement field, so the ADR reads back with `superseded_by` null (`1227841:src/tracker/contract.ts:83`, `store.ts:2015`). `/adr-decisions` with `supersede` and a `replacement` refuses every non-human actor with 403 `authority_boundary` (`adr-decisions-store.ts:43`), and in a `foundry`-governed project even a human decision there records an intention only, with no status change and no supersession edge (`adr-decisions-policy.ts:11`). No DevHub issue exists; an intake for a proof-bound service supersede naming the replacement is the next step on the DevHub side, not started, priority left to the human. DHN-32 stays `adapter + server`.
    Follow-up (DevHub agent, relayed by the maintainer, 2026-10-20; reformulated, DevHub reference not read by Foundry): (a) chosen. DevHub will give the Foundry service credential a proof-bound route, as DEVHUB-108, of the same model as the ADR status proof (bound to the project, both ADRs and their expected versions; scope `tracker:adr`, `If-Match`, `Idempotency-Key`, cycle refusal, replay without a second write). It moves the old ADR from `accepted` to `superseded` and records the link in the same transaction; `superseded_by` (old) and `supersedes` (replacement) read back. DevHub gives DEVHUB-ADR-0010 as the reason; Foundry has not read it (it is not in the DevHub repository at `1227841`). The human path (`/adr-decisions`) stays and is not Foundry's. State of the replacement: Foundry asked (maintainer decision of 2026-10-20) that it already be `accepted` and left unchanged, because the order is `adr accept` then `adr supersede` and the Linear and GitHub Projects adapters refuse a non-accepted replacement (`supersede_adr` of `trackers/linear.py` and `trackers/ghprojects.py`); DevHub's first plan accepted it from `proposed` in the same transaction. DevHub answered: accepted, a `proposed` replacement is refused, no implicit acceptance, status, body and version unchanged. N to 1: the DevHub schema forbids one ADR replacing several (unique index `tracker_adr_replacement_once` on the replacement, `1227841:drizzle/0021_adr_decisions.sql:28`, observed in source); Foundry asked (same decision) to add N to 1 to DEVHUB-108, one call superseding one old ADR and `superseded_by` staying single, for parity with the Linear and GitHub Projects lists of `supersedes`. Confirmed by DevHub on 2026-10-20 (reformulated, DevHub reference not read by Foundry): N to 1 is part of DEVHUB-108; the unique index `tracker_adr_replacement_once` is to be removed and `tracker_adr_superseded_once` stays. Observed in the Linear tracker on 2026-10-20: the only supersessions of the project are 1 to 1 chains (PAT-ADR-0011, 0012, 0013 and PAT-ADR-0019, 0020).
    Follow-up of PAT-320 (read by Foundry in `6721ad8:docs/TRACKER_API.md`, "Proof-bound ADR supersession", and `6721ad8:src/tracker/store.ts:2350-2404`, observed in source; DEVHUB-108 merged as `6721ad8`): `POST /adrs/:id/supersede` takes the strict body `replacement` and `replacement_version`, needs the scope `tracker:adr`, the source version and an idempotency key, and refuses a replacement that is not `accepted` (409 `invalid_transition`), a replacement of another project or equal to the source (409 `resource_conflict`) and a stale version of either ADR (409 `version_conflict`); the replacement is not written. The proof names `replacement_id` and `replacement_version` besides the usual coordinates (`6721ad8:src/tracker/contract.ts:300-302`, `store.ts:1751`). N to 1 is in the server: the unique index on the replacement is dropped (`6721ad8:drizzle/0027_source_refs.sql:52`). Not run by Foundry: any request against `6721ad8` (see "Live run of PAT-320").
16. **DHN-37**: after a creation whose response was lost, can a client read the created issue by the `Idempotency-Key` it sent, or replay the request without knowing the parent version the original carried? Today (answer 9) the replay needs the identical payload, and a child creation raises the parent version, so Foundry cannot replay a parented creation later. Would the `source_ref` of DEVHUB-105 (answer 8, issues included) cover this, and is it planned for issue creation as well as for imports?
    Answer (DevHub agent, 2026-10-20, `1227841`): (a) No read by `Idempotency-Key`. The supported reconciliation is to resend the identical `POST /projects/:key/issues` (same credential identity, key and payload); DevHub answers the stored response with `Idempotency-Replayed: true` without creating again (`1227841:src/tracker/store.ts:1314-1330`). (b) `parent` and `parent_version` go together (`contract.ts:104-107`) and the key hash covers the whole payload: without `parent_version` the replay answers 400, with another value 409 `idempotency_conflict`. An identical replay does not re-check the current parent version and succeeds after the parent changed, returning the original body; it answers 409 when the project governance version or workflow revision changed in between (`store.ts:1328-1329`). (c) `source_ref` does not exist, at creation or later (`createIssueSchema` is strict); it is the scope of DEVHUB-105, last in the order Foundry asked for.
    Foundry, PAT-319: partly answered by DEVHUB-105: `POST /projects/:key/issues` accepts a `source_ref` and `GET /projects/:key/issues?source_ref=` finds the issue (`b0231d9:docs/TRACKER_API.md`, "Selective import (DEVHUB-105)"; `migration_find_issue`); `DevHubTracker.create_issue` sends none, so a normal creation whose response is lost is unchanged (deduced from `create_issue` in `trackers/devhub.py`).
    Foundry, PAT-313: unchanged. The adapter reads by issue id and does not keep the key and payload of a call after it returns, so it does not use the replay of (a) after a lost response (unknown whether keeping them is worth doing; not in the scope of PAT-313).
17. **DHN-38**: at `809ca94`, `GET /issues/:id/proof-receipts` returned receipts `10` before `9` on a fresh instance (observed in the live run of PAT-313) because the listing selects `id::text` and orders by the bare name `id` (`809ca94:src/tracker/store.ts:1756-1758`). The same bare `ORDER BY id` appears in `809ca94:src/tracker/project-lifecycle.ts:175-176`. Can DevHub confirm and order by the numeric column?
    Answer (DevHub agent, 2026-10-20, `1227841`): Confirmed and fixed in DEVHUB-107 (merge `1227841`). PostgreSQL resolves a bare `ORDER BY id` to the `id::text` output column, which that cast names `id` with or without `AS id`. Six queries are now qualified: the proof-receipt listing, the project inventory of `tracker_audit_events`, and four over `foundry_command_events` and `foundry_command_observations`, two of them cursor-paginated (`listCommandEvents`, `listFoundryObservations`); response contracts are unchanged. Its integration tests insert ids across two decimal boundaries, check numeric order and walk with `limit=3` returning each receipt once; they fail on the old ordering (observed by the DevHub agent). Foundry checked the six changed clauses in `git diff 809ca94 1227841 -- src` and observed DHN-38 green at `1227841` (live run of PAT-313).
18. **Order of the DevHub work** (hand-over of 2026-10-20, DevHub reply relayed by the maintainer, reformulated): DEVHUB-106 first (DHN-25, DHN-26), then DEVHUB-105 (DHN-34, DHN-36; its scope matches Foundry's description: `source_ref` unique per project, exact lookup, replay-safe import, an imported ADR keeping the foreign id as an alias, its status and exact body), then DEVHUB-108 (DHN-32; backlog, P1, under DEVHUB-63). No date is known. DevHub will give the merge sha on `main` at each merge. DEVHUB-108 merged as `6721ad8` (announced by DevHub on 2026-10-21; Foundry read the page and code listed in the question 15 follow-up); PAT-320 implements the adapter side.
19. **DHN-43**: for a selective cutover to DevHub, three facts have no carrier or read route at `12ca158` (observed in source, `src/tracker/store.ts`, `docs/TRACKER_API.md`): (a) [at `12ca158`; answered, see below] the service credential could not read which `type`, `priority` and `estimate` values a published workflow enables, so a disabled value was refused only by the first write (409 `value_disabled`, returned as `resource_conflict`); (b) [at `12ca158`; answered, see below] an ADR import carries `linked_issue_ids` of issues of the project only, so an ADR related to an issue outside the living scope (a source-only reference) cannot be imported and the adapter refuses it before any write; (c) [at `12ca158`; answered, see below] an ADR cannot record that a relation is unknown, so a source ADR whose relations are unknown cannot be copied faithfully and is refused. Would DevHub offer a read of the enabled assignment catalogue to a service credential, and a carrier for source-only issue references and for an unknown relation? Until answered, Foundry refused (b) and (c) at preflight rather than approximate them (PAT-ADR-0003, PAT-ADR-0008); the contract cell `cross-tracker-adr-import` stayed `gap`, owned by PAT-327 ("Adaptateur DevHub : importer les ADR avec références source seule et relations inconnues"); (a) was refused only at copy time, after partial writes, until PAT-328.
    Answer (DevHub agent, read in DevHub source at `cd23304`, relayed for PAT-328; not run by Foundry before the live run below): (a) `GET /api/tracker/workflow/v1/projects/:key` answers a bearer service credential holding `tracker:read` on the project (no query parameter, 400 otherwise; `workflow-http.ts`), with `schema_version: "devhub-workflow.v1"`, `version` and `settings.fields.{type,estimate,priority}`, each a list of `{id, value, label, order, enabled}` with a `null` entry, and `settings.statuses` with `canonical` and `enabled`. It answers 200 for a never published project, with `version` 1 and every value enabled. At import (`assertWorkflowAssignments` of `workflow-settings.ts`, `createTrackerIssue` of `store.ts`): with `version` 1 nothing is checked (even a value the read does not list is accepted); with `version` above 1 each of the three fields, an omitted one counting as `null`, must be strictly equal (`===`) to an entry with `enabled: true`, and the initial status (`backlog`, or the creation state) must be one enabled status of that canonical state. Foundry, PAT-328: `migration_preflight` reads it and copies this rule (`devhub-tracker.md`, "Selective cutover target"). Limits that stay: a replay of an already imported `source_ref` with the same content returns the existing issue before any check, and a workflow published between the preflight read and the write can change the outcome (create carries no workflow-version precondition, deduced by the DevHub agent), so the preflight is best effort against a concurrent publish. Replacement status (DEVHUB-118): an ADR import whose `superseded_by` names a `proposed` ADR is refused with 409 `invalid_transition`; `accepted`, `superseded` and `deprecated` are taken (`store.ts:2307-2309`).
    Answer (DevHub agent, DEVHUB-122, merge `3f0cf4b98e60a04154cf051e4110cbf29094d381`, PAT-327; relayed, Foundry read the schema at that revision in `src/tracker/contract.ts:94-134` and `src/tracker/store.ts:377-391`, green live in DHN-46): (b) and (c) have a carrier. `POST /projects/:key/adrs/import` takes the optional `source_only_refs` (up to 200 strings, each 1 to 200 characters without whitespace, control character or unpaired surrogate, none twice, kept byte for byte and in order, never parsed) and `unknown_relations` (a subset of `issues`, `supersedes`, `superseded_by`, no repeat). A family named unknown beside a value for it in the same request (`issues`: a non-empty `linked_issue_ids` or `source_only_refs`; `superseded_by`: the field present; `supersedes`: no field, always allowed) is 400 `invalid_request`, nothing written. `GET /adrs/:id`, the import answer and `GET /adr-decisions/adrs/:id` return both fields in the order sent, `[]` when none; the ADR list items do not carry them. An absent field leaves the request hash and the replay digest unchanged. `unknown_relations` is an import-time declaration that DevHub does not keep consistent with later writes. Foundry, PAT-327: the adapter sends and reads them back (`devhub-tracker.md`, "Source-only references and unknown relations"), and the preflight no longer refuses these corpora. DHN-46 is the live proof, green at `3f0cf4b` ("Live run of PAT-327").

Decided on the DevHub side on 2026-10-20, in the order of item 18 (DEVHUB-106, DEVHUB-105, then DEVHUB-108 for DHN-32, question 15, merged as `6721ad8`): DEVHUB-105 (DHN-34 / DHN-36, answer 8, merged as `b0231d9`) and DEVHUB-106 (DHN-25 / DHN-26, answer 12, merged as `f347f38`). DHN-33 (answer 7) needs no DevHub work: the freeze stays and the test is adapter-side. Adapter-side only, per the DevHub agent: DHN-21 (done, PAT-312), 24 (done, PAT-313 under PAT-ADR-0030, green), 35 (done, PAT-314, green), 37 (done, PAT-313, green), 41 (done, PAT-314, green); DHN-32 was requalified to adapter and server by PAT-312 (question 15) and is adapter-side again since PAT-320 (the DHN-38 flag and DHN-39 are green since PAT-309); DHN-40 is Foundry and adapter (its row).

## Documentation status (AGENTS.md#R5, FOUNDRY-ADR-0018)

| Artifact | Status |
| --- | --- |
| This page (needs, tests, statuses, questions) | Added by PAT-296. |
| Command `pytest -m devhub_live tests/integration/test_devhub_live_conformance.py` and the `devhub_live` marker (`pytest.ini`) | Documented in "Run the suite". |
| Environment variables `DEVHUB_LIVE_URL`, `DEVHUB_LIVE_CONFIRM`, `DEVHUB_LIVE_TOKEN`, `DEVHUB_LIVE_PROOF_SECRET` | Documented in "Environment variables". |
| Safety checks of `tests/devhub_live_gate.py` | Documented in "Production safety"; its module docstring states the local-accounts limit (PAT-300). |
| `docs/devhub-tracker.md`, `docs/tracker-conformance.md` | One pointer line each to this page; no other change. |
| Statuses of DHN-01 to DHN-31 (25 green, first live run of 2026-10-20), new need DHN-42 (red, `server`), `DEVHUB_LIVE_LOCAL_ACCOUNTS`, limit of the instance marker under local accounts, procedure "Bring up a disposable instance" (PAT-300) | Updated in "Needs", "Environment variables", "Production safety", "Bring up a disposable instance" and question 14. |
| Counts, DHN-40 owner and the new need DHN-41, `raises=` of each gap, server citations by repository path (PAT-299) | Updated in "Needs" and "Reading the result". |
| Wording of the procedure "Bring up a disposable instance" (prerequisites, hardening lines not run, suite command), question 1, the "First live run" paragraph (PAT-301) | Updated in those sections; no need, status or count changed. |
| Statuses of DHN-21 and DHN-33 (`@need`, `green` after the live run of PAT-312: first run 30 passed, 1 failed (DHN-21 replay line, corrected), 10 xfailed, 1 skipped; rerun 31 passed, 10 xfailed, 1 skipped), `update_body` read-back, DHN-32 owner (`adapter + server`) and its `raises=` reason, counts (10 red, 0 unknown, 32 green), question 15, "Closing a gap" (PAT-312) | Updated in "Needs", "Closing a gap" and "Questions for the DevHub agent"; adapter code changed (`update_body`, `link_adr_issue`, `read_adr_issue_relations`, `AdrRelationFrozenError`), documented in `devhub-tracker.md`, "Body replacement and ADR relations". `supersede_adr` unchanged. |
| Statuses of DHN-27, DHN-28, DHN-42 (`@need`, `green` after the live run of PAT-303), owner of DHN-38 and DHN-39 (`adapter`), counts (14 red, 0 unknown, 28 green), current DevHub reference `2edcea2`, answers 1 to 14 of the DevHub agent, awaiting-decision marks (DHN-33, DHN-34, DHN-36, question 12), cleanup by dropping the instance, section "Live run of PAT-303" (PAT-303) | Updated in "Production safety", "Bring up a disposable instance", "Needs", "Live run of PAT-303" and "Questions for the DevHub agent". DHN-38 and DHN-39 stay red (adapter-only); no adapter code changed. |
| DHN-33 requalified (test renamed, owner `adapter`), owners of DHN-25, DHN-26, DHN-34, DHN-36 (`adapter + server`, DEVHUB-105 and DEVHUB-106), answers 7, 8 and 12, the paragraph "Decided on the DevHub side on 2026-10-20" (PAT-311) | Updated in "Needs", "Closing a gap" and "Questions for the DevHub agent". Counts unchanged (14 red, 0 unknown, 28 green); no status changed. Live run on a disposable instance at `2edcea2`: 27 passed, 14 xfailed, 1 skipped (DHN-42), as for PAT-303. |
| `append_only_lifecycle_supported`, `read_proof_receipts`, `recover_done_projection`, `set_state` review republication, `read_release_scope` of `trackers/devhub.py`; DHN-38 and DHN-39 (`@need`, owner `-`, `green` after the live run of PAT-309: 29 passed, 12 xfailed, 1 skipped (DHN-42) on a disposable instance at `2edcea2`), counts (12 red, 0 unknown, 30 green), answers 10 and 11 (PAT-309) | Updated in "Needs", "Closing a gap" and "Questions for the DevHub agent"; new sections "Append-only lifecycle receipts" and "Release scope" of `devhub-tracker.md`; one sentence in `campaign-cadence.md`, "Capabilities required". |
| `reconcile_issue_creation` and `epic_dropped_closure_supported` of `trackers/devhub.py`, the PAT-ADR-0030 rule of `write.close_epic`; DHN-37 (`@need`, owner `-`, `green` after the live run of PAT-313), DHN-24 (`@need`, owner `-`, `green` under PAT-ADR-0030), DHN-40 wording, DHN-38 ordering defect at `809ca94`, counts (8 red, 0 unknown, 34 green), current DevHub reference `1227841`, answers 9, 15, 16 and 17, section "Live run of PAT-313" (PAT-313) | Updated in "Bring up a disposable instance", "Needs", "Live run of PAT-313" and "Questions for the DevHub agent"; new sections "Issue creation reconciliation" and "Epic closure over a dropped child" of `devhub-tracker.md`; one sentence in `campaign-cadence.md`, "Capabilities required"; the DevHub scope of `--accept-dropped` in `skills/close-epic/SKILL.md`, `linear-tracker.md`, `tracker-contract.md`, `tracker-contract.v1.json` and `README.md`. |
| `verify_project_identity` and `migration_link_issue` of `trackers/devhub.py`; DHN-35 and DHN-41 (`@need`, owner `-`, `green` after the live run of PAT-314; DHN-35 test links issues created normally), counts (6 red, 0 unknown, 36 green), DHN-34 adapter note, the adapter-side paragraph, section "Live run of PAT-314" (PAT-314) | Updated in "Needs", "Live run of PAT-314" and "Questions for the DevHub agent"; new sections "Project identity read-back" and "Staged migration links" of `devhub-tracker.md`. |
| Atomic Epic closure receipt of `write.close_epic` (children bound by id, version and state only), DHN-24 test children with criteria, section "Live run of PAT-316" (PAT-316) | Updated in "Needs" (row DHN-24) and "Live run of PAT-316", and in `devhub-tracker.md`, "Epic closure over a dropped child"; counts unchanged. |
| `acceptance_override_projection_supported`, `project_acceptance_override`, `recover_acceptance_override`, `accept_issue`, the extended receipt reader and `set_state` review republication of `trackers/devhub.py`; DHN-25 and DHN-26 (`@need`, owner `-`, `green` after "Live run of PAT-318" at `f347f38`), DevHub answers a to e of question 12, N to 1 of DEVHUB-108 confirmed (DHN-32 and question 15), counts (4 red, 0 unknown, 38 green) (PAT-318) | Updated in "Needs", "Live run of PAT-318", "Closing a gap" and "Questions for the DevHub agent"; new section "Acceptance override and human acceptance" of `devhub-tracker.md`; `merge-pr` skill and `tracker-contract.md` wording. |
| DevHub reply of 2026-10-20 (order DEVHUB-106, DEVHUB-105, DEVHUB-108; DHN-32 option a, replacement already `accepted`, N to 1 unknown), rows DHN-25, DHN-26, DHN-32, DHN-34, DHN-36, question 15 and new item 18; DHN-32 test (both ADRs accepted first, replacement unchanged, replacement status and body checked, link read back through `migration_export_adrs`) (PAT-317) | Updated in "Needs" and "Questions for the DevHub agent"; `devhub-tracker.md`, "Body replacement and ADR relations". Counts unchanged (6 red, 0 unknown, 36 green); no status changed. No live run. |
| `import_adr`, `migration_import_adr`, `migration_import_adrs`, `migration_find_adr`, `migration_find_issue`, `migration_import_issue` of `trackers/devhub.py`, the ADR reader (`foreign_id` as `Adr.id`); DHN-34 and DHN-36 (`@need`, owner `-`, `green` after "Live run of PAT-319" at `b0231d9`), counts (2 red, 0 unknown, 40 green) (PAT-319) | Updated in "Needs" and "Live run of PAT-319"; new section "Selective import" of `devhub-tracker.md`; changelog fragment `changelog.d/PAT-319.md`. `tracker-contract.md` and `tracker-contract.v1.json` not changed (DevHub is outside the three V1 provider columns). |
| `supersede_adr`, `migration_export_adrs` and the `_adr_route_items` helper of `trackers/devhub.py`; DHN-32 (`@need`, owner `-`, `green` after "Live run of PAT-320" at `6721ad8`), question 15 follow-up, item 18, counts (1 red, 0 unknown, 41 green) (PAT-320) | Updated in "Needs", "Live run of PAT-320" and "Questions for the DevHub agent"; new section "Proof-bound ADR supersession" of `devhub-tracker.md`; changelog fragment `changelog.d/PAT-320.md`. |
| Review remarks deferred from PAT-309 to PAT-320 (PAT-315): `read_release_scope` (second Version read, numeric order), `link_adr_issue`, `read_adr_issue_relations`, `migration_link_issue`, `import_adr` (foreign id equal to the server id of a native ADR; DEVHUB-111 merged as `16b6888`), `migration_import_adr(s)`, `supersede_adr` (replay first), `migration_export_adrs` (one listing, `supersedes` `unknown` while an unlinked superseded ADR exists), `models.AC_OVERRIDE_REASON`; rows DHN-24, DHN-25, DHN-32, DHN-33, DHN-36, questions 16 and 18, first live run paragraph | Updated in "Needs" and "Questions for the DevHub agent"; `devhub-tracker.md` (sections "Append-only lifecycle receipts", "Release scope", "Staged migration links", "Selective import", "Body replacement and ADR relations", "Proof-bound ADR supersession"); new "Live run of PAT-315", which runs the additions to the live tests of DHN-24 and DHN-32; counts unchanged. |
| `registry._V1_TRACKERS`, `registry._validated_binding_entry`, `setup_project.py`, `trackers/devhub.py` `resolve_checkout_project`, retirement of `mode=pilot`; DHN-40 (green after the live run at `8028000`), counts (0 red, 0 unknown, 42 green); `tracker-contract.md`, `tracker-contract.v1.json` (version 5, DevHub column) (PAT-323) | Updated in the opening paragraph, "Needs" (row DHN-40, counts) and "Not covered, on purpose"; `devhub-tracker.md` ("Project binding and cutover", "Release scope", "Project identity read-back", "Governance mode"); changelog fragment `changelog.d/PAT-323.md`. Live run of PAT-323 at `8028000`: 41 passed, 1 skipped (DHN-42); new section "Live run of PAT-323". |
| `DevHubTracker.set_adr_status` (refuses `superseded` before any request, PAT-326), DHN-31 redefined (test renamed; status route refuses `superseded`, 409 `invalid_transition`), question 6 note, test_dhn_32 comment (PAT-326) | Updated in "Needs" and "Questions for the DevHub agent"; `devhub-tracker.md` "Governance mode", "Selective import" and "Proof-bound ADR supersession" updated; counts unchanged (0 red, 0 unknown, 42 green) on the assumption that the redefined DHN-31 is green, to be confirmed by the live run of PAT-326. |
| `tracker-contract.md`, `tracker-contract.v1.json` | Not changed by PAT-309 to PAT-312 (PAT-313 updates them, row above): DevHub was outside the three V1 provider columns of the contract at that date (`tracker-contract.v1.json`, `devhub` note), and no CLI verb or configuration key changed. |
| `verify_project_identity`, `DevHubGovernanceModeError` and the write guard of `trackers/devhub.py`, `registry.verify_existing_project` message pass-through (PAT-321) | Documented in `devhub-tracker.md`, "Governance mode"; no need, status or count of this page changed (no live test added; the governance route is not yet in `TRACKER_API.md`, DEVHUB-116 unmerged); row DHN-32 now says the supersede route checks the mode since DEVHUB-110 (`5782d78`); live observation at `8028000` in `devhub-tracker.md`, "Governance mode". |
| `migration_preflight`, `migration_attribute_exceptions`, `migration_supported_attributes`, `migration_prepare_adr`, the sorted relations and the issue relations of `migration_export_adrs` of `trackers/devhub.py`; DHN-43 (`@need`, `green` in "Live run of PAT-324"), question 19, counts (0 red, 0 unknown, 43 green) (PAT-324) | Updated in "Needs" and "Questions for the DevHub agent"; new section "Selective cutover target" and the DHN-34 and DHN-36 limits of `devhub-tracker.md`; `tracker-contract.md`, `tracker-contract.v1.json` and `tracker-conformance.md` (the DevHub live-work copy cell is `supported`, the ADR import cell stays a `gap` owned by PAT-327, `_ADMISSION_PENDING_PROVIDERS` kept); changelog fragment `changelog.d/PAT-324.md`. |
| `import_adr`, `_prepare_adr_import`, `_snapshot_import_arguments`, `_preflight_adrs`, `migration_export_adrs`, `_adr_source_relations` and `_ORIGIN_TEXT` of `trackers/devhub.py`; DHN-46 (`@need`, owner `-`, green in "Live run of PAT-327"), question 19 follow-up, counts (0 red, 0 unknown, 46 green) (PAT-327) | Updated in "Needs", the opening paragraph, "Live run of PAT-327" and "Questions for the DevHub agent"; new section "Source-only references and unknown relations" and "Selective cutover target" of `devhub-tracker.md`; `tracker-contract.md` and `tracker-contract.v1.json` (DevHub `cross-tracker-adr-import` cell `supported`), `tracker-conformance.md` (`_ADMISSION_PENDING_PROVIDERS` removed), `README.md` and the two plugin manifests; changelog fragment `changelog.d/PAT-327.md`. |
| `DevHubTracker.migration_preflight` (third read, `_read_workflow`, `_preflight_workflow`, `api_prefix` of `_req`), refusal of a `proposed` ADR replacement in `_preflight_adrs`; DHN-44 and DHN-45 (`@need`, green in "Live run of PAT-328"), `DEVHUB_LIVE_WORKFLOW_PUBLISH`, question 19, counts (0 red, 0 unknown, 45 green) (PAT-328) | Updated in "Needs", "Environment variables" and "Questions for the DevHub agent"; new section "Live run of PAT-328"; `devhub-tracker.md` ("Selective cutover target"); `tracker-contract.v1.json` (evidence of the DevHub `cross-tracker-live-work-copy` cell); changelog fragment `changelog.d/PAT-328.md`. |
| `cutover.main` activation toward DevHub, `registry.cutover_repository_tracker`, `registry.stage_repository_cutover_target`; DHN-47 (`@need`, owner `-`, green in "Live run of PAT-329"), counts (0 red, 0 unknown, 47 green) (PAT-329) | Updated in "Needs" (row DHN-47, counts) and "Live run of PAT-329"; `devhub-tracker.md` ("Selective cutover target"); `tracker-contract.md` and `tracker-contract.v1.json` (DevHub `tombstone-archived-source-after-switch` cell and the note of the `cross-tracker-live-work-copy` cell); `README.md`; changelog fragment `changelog.d/PAT-329.md`. |
| `human_acceptance_supported`, `human_acceptance_source`, `human_acceptance_predecessors`, `observe_human_acceptance`, `observe_epic_closure_child`, the human-decision branch of `_release_issue` and the `acceptance_source` of the issue returned by `accept_issue` in `trackers/devhub.py`, `Tracker.observe_human_acceptance` and `human_acceptance_source` in `trackers/base.py`; DHN-48 (`@need`, `green` in "Live run of PAT-330"), DHN-26 note, counts (0 red, 0 unknown, 48 green) (PAT-330, PAT-ADR-0033) | Updated in "Needs" (rows DHN-26 and DHN-48, counts) and "Live run of PAT-330"; `devhub-tracker.md` ("Acceptance override and human acceptance"); `tracker-contract.md` and `tracker-contract.v1.json` (DevHub `human-acceptance-decision` cell `supported`); changelog fragment `changelog.d/PAT-330.md`. |

Mechanical enforcement: `tests/test_devhub_requirements.py` checks that this table and the
test decorators carry the same ids, tests and red/not-red status, that every `@gap` carries
one `raises=`, that a gate refusal makes every test an error and never an `xfailed` (offline
run, no network), and that this page names the command and variables; it does not prove that
a sentence here is true (FOUNDRY-123 is the separate, not-yet-shipped documentation gate).
