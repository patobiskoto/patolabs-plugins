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

DevHub is still a pilot outside the V1 contract: `registry._V1_TRACKERS` holds
`youtrack`, `linear`, `ghprojects` (`registry.py`), and `tracker-contract.v1.json` lists
DevHub "for contrast only" (observed, 2026-10-19). DHN-40 states the Foundry-side
prerequisite that follows, with DHN-41.

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
`xfailed` (no `@gap` declares `DevHubTrackerError` any more since PAT-303; each remaining
`raises=` is checked by `tests/test_devhub_requirements.py`); `XPASS(strict)` reported as a failure
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
DevHub reference is `2edcea2` (`main`); the run of PAT-303 at `2edcea2` is in "Live run of
PAT-303". It yields an instance
without local accounts, which the suite needs (DHN-42 explains why). Sources: DevHub
`deploy/qa/up.sh` (image and `app.env` generation) and `deploy/dogfood/validate-app-env.sh`
(validation), read-only; both are DevHub files, not Foundry ones.

Prerequisites, stated because this page does not build the image:

- Since 2026-10-20 the DevHub agent reports that `main` (`2edcea2`) has no supported
  throwaway procedure, because `deploy/qa/` (DEVHUB-100) is not merged; it built its own run
  with `git archive <rev>`, `docker build -f deploy/dogfood/Dockerfile`, its own compose
  project and volume on loopback ports, and `down -v` to reset (observed by the DevHub
  agent, not run by Foundry).
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
instance (none at present: DHN-27, DHN-28 and DHN-42, `unknown` when PAT-303 moved them to
`@need`, became `green` in "Live run of PAT-303").
The owner column names who must act when the need is red. Basis: *adapter* = observed in
`trackers/devhub.py` / `trackers/base.py` (the method is inherited from the `Tracker`
default, checked by script on 2026-10-19); *server* = deduced from the DevHub source
(repository `dev-hub`, paths relative to its root), read-only. Server citations without a commit were
read at commit `68c7ef0` (2026-10-19), the `HEAD` of that checkout at reading time; the files
cited here are identical at the earlier `3fedc57` (`git diff 3fedc57 68c7ef0` on them was
empty that day). The current DevHub reference is `2edcea2` (`main`, 2026-10-20): citations
written `2edcea2:path:line` come from the answer of the DevHub agent (observed in source
there) and the server rows DHN-27, DHN-28 and DHN-42 changed there (DEVHUB-103 merge
`2e5d3ff`, DEVHUB-102 merge `3da571f`, both on `main`; verified by ancestry on 2026-10-20).

First live run (2026-10-20, observed, history): DevHub revision
`ff22b3a821fb031b49d1fdb78c43ecb481431c7f` (head of the unmerged DEVHUB-100 branch, not on
`main`), a disposable instance without local accounts
(`select local_accounts from account_workspace` answered false; procedure in "Bring up a
disposable instance"), result 25 passed and 16 xfailed (41 tests, before DHN-42 existed).
Every `green` other than DHN-27, DHN-28 and DHN-42 is a test that passed in that run; every `red` of DHN-01 to DHN-41
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
| DHN-21 | Update an issue body (frame/groom) checked against the expected body | `test_dhn_21_arbitrary_body_update_is_checked_against_the_expected_body` | red | adapter | adapter: `update_body` inherited, `BodyUpdateUnavailableError`; the body `PATCH` route exists (used by DHN-20) |
| DHN-22 | Close an Epic atomically from a receipt; replay; recover the original closure | `test_dhn_22_epic_closure_is_atomic_audited_and_replayable` | green | - | observed, run of 2026-10-20 |
| DHN-23 | Refuse a closure whose graph differs from the receipt | `test_dhn_23_epic_closure_refuses_a_graph_that_differs_from_the_receipt` | green | - | observed, run of 2026-10-20 |
| DHN-24 | Close an Epic over a dropped child by explicit acceptance (`--accept-dropped`, PAT-ADR-0017) | `test_dhn_24_epic_closes_over_a_dropped_child_by_explicit_acceptance` | red | adapter | adapter: `epic_dropped_closure_supported` is `False` and `write.py` refuses the flag without it; the DevHub closure itself already admits `dropped` children (`src/tracker/epic-closure-store.ts:182`, server, observed) |
| DHN-25 | Typed acceptance-override receipt before merge | `test_dhn_25_acceptance_override_leaves_a_typed_receipt` | red | adapter | adapter: `project_acceptance_override` inherited, `TrackerCapabilityUnavailableError`; the end-to-end `issue merge` with an AC override on DevHub has not been run (unknown); no typed receipt route on DevHub, whether it will be added is unknown (answer 12) |
| DHN-26 | Accept a non-code issue by a human decision without a PR (PAT-ADR-0025) | `test_dhn_26_non_code_issue_is_accepted_by_a_human_decision_without_a_pr` | red | adapter | adapter: `accept_issue` inherited. The test passes a minimal decision document; the first implementation must complete it; no PR-less acceptance route on DevHub (answer 12) |
| DHN-27 | Abandon a started issue (`dropped` from `in-progress` or `review`) | `test_dhn_27_started_issue_can_be_abandoned` | green | - | server, observed by the DevHub agent: `2edcea2:src/tracker/workflow.ts:12-17` (`canServiceTransition`) allows `in-progress` and `review` to `dropped` and `backlog` on the V1 service route (DEVHUB-103, merge `2e5d3ff`); XPASS reported at `2edcea2` (answer 5); observed by Foundry in "Live run of PAT-303" |
| DHN-28 | Return a started issue to `backlog` | `test_dhn_28_started_issue_can_return_to_backlog` | green | - | same change as DHN-27; the expected predecessor is the existing `expected_version` (409 `version_conflict`, answer 5); XPASS reported at `2edcea2`; observed by Foundry in "Live run of PAT-303" |
| DHN-29 | Create an ADR as `proposed` with an id `<KEY>-ADR-NNNN`; list it with its exact body | `test_dhn_29_adr_is_created_proposed_and_listed_with_its_exact_body` | green | - | observed, run of 2026-10-20 |
| DHN-30 | ADR status `proposed` to `accepted` to `deprecated` with a signed proof | `test_dhn_30_adr_status_moves_proposed_to_accepted_to_deprecated` | green | - | observed, run of 2026-10-20 |
| DHN-31 | `accepted` to `superseded` through the status route in Foundry mode | `test_dhn_31_accepted_adr_can_be_marked_superseded_through_the_status_route` | green | - | observed, run of 2026-10-20: it passed; `assertAdrTransition` allows it (`src/tracker/contract.ts:268-277`, server, observed; `2edcea2:src/tracker/contract.ts:274-288`, answer 6) but `docs/DEVHUB-73-ADR-DECISIONS.md` limits Foundry projects to recorded intents for decisions (which applies to the status route stays unknown) |
| DHN-32 | Supersede an ADR by a named replacement | `test_dhn_32_adr_is_superseded_by_a_named_replacement` | red | adapter | adapter: `supersede_adr` inherited, `TrackerCapabilityUnavailableError` |
| DHN-33 | Append a reciprocal ADR-to-issue relation to a decided ADR | `test_dhn_33_decided_adr_gains_a_reciprocal_issue_relation` | red | adapter + server | adapter: `link_adr_issue` inherited; server: decided ADR links are frozen (`docs/DEVHUB-73-ADR-DECISIONS.md`, observed in the document, not run; trigger `2edcea2:drizzle/0021_adr_decisions.sql:39-48`, answer 7); awaiting a human decision on the DevHub side, no commitment |
| DHN-34 | Selective import: a living issue is imported once, found by exact source coordinate, replay-safe | `test_dhn_34_living_issue_is_imported_once_by_exact_source_coordinate` | red | adapter + server | adapter: `migration_find_issue`, `migration_import_issue` inherited; server: no source coordinate on issues (`2edcea2`, answer 8, observed in source); awaiting a human decision on the DevHub side (V1 contract extension), no commitment |
| DHN-35 | Selective import: relations between imported issues through the staged port | `test_dhn_35_imported_issues_are_linked_through_the_staged_port` | red | adapter | adapter: `migration_link_issue` inherited |
| DHN-36 | Selective import: a needed ADR keeps its foreign id (for example `FOUNDRY-ADR-0026`), status and exact body | `test_dhn_36_adr_is_imported_with_its_foreign_id_status_and_exact_body` | red | adapter + server | adapter: `import_adr`, `migration_import_adrs` inherited; server: ADR ids are generated `<KEY>-ADR-NNNN` (`src/tracker/store.ts:1950`, observed; `2edcea2:src/tracker/store.ts:1999`, answer 8); awaiting a human decision on the DevHub side (V1 contract extension), no commitment |
| DHN-37 | Reconcile an interrupted issue creation without recreating it (Epic cadencer) | `test_dhn_37_an_issue_creation_can_be_reconciled_without_recreating_it` | red | adapter | adapter: `reconcile_issue_creation` inherited; `campaign_cadence.missing_capabilities` reports `issue_creation_reconciliation` |
| DHN-38 | Append-only lifecycle capability required by the Epic cadencer (PAT-ADR-0026) | `test_dhn_38_lifecycle_is_append_only_for_the_cadencer` | red | adapter | adapter: `append_only_lifecycle_supported` is `False` (script, 2026-10-19). Server side now exists (DEVHUB-104, merge `2edcea2`): `GET /api/tracker/v1/issues/:id/proof-receipts` (answer 10); what remains is adapter-only (set the flag and read the receipts), so the gap stays |
| DHN-39 | Read a mapped release scope without approximate discovery | `test_dhn_39_a_mapped_release_scope_is_read_without_approximate_discovery` | red | adapter | adapter: `read_release_scope` inherited, `ReleaseScopeUnavailableError`; DevHub needs no server change: the adapter stores the Version id `<KEY>-VERSION-N` and reads `GET /versions/:id` (answer 11, deduced), so the gap stays |
| DHN-40 | DevHub accepted as a V1 tracker of the repository | `test_dhn_40_devhub_is_a_v1_tracker_of_the_repository` | red | foundry + adapter | foundry: `registry._V1_TRACKERS` excludes `devhub` (`registry.py:101`); adapter: DevHub still resolves a checkout through the basename pilot path (`resolve_checkout_project`, `trackers/devhub.py`), with no live test (DHN-41 covers `verify_project_identity` only); the Epic cadencer also needs DHN-37 and DHN-38 |
| DHN-41 | Repository bootstrap: read back the exact project identity before publishing a binding (`repository-bootstrap` of `tracker-contract.v1.json`) | `test_dhn_41_repository_bootstrap_reads_back_the_project_identity` | red | adapter | adapter: `verify_project_identity` inherited from `trackers/base.py`, `ProjectProvisioningUnavailableError`; the test covers it only, the `resolve_checkout_project` pilot path of DHN-40 has no live test |
| DHN-42 | Under local accounts the Foundry service credential can create its project and use it (issue create and read) | `test_dhn_42_service_credential_can_create_and_use_its_project_under_local_accounts` | green | - | server, observed by the DevHub agent: `2edcea2:src/access/domain.ts:139-152` lets the environment service credential (actor `foundry`, `tracker:write`) create a project when an active owner exists, and `2edcea2:src/tracker/store.ts:1341-1350` binds it to that project only and makes every active owner its `admin` (DEVHUB-102, merge `3da571f`); XPASS reported at `2edcea2` on an instance with local accounts (answer 14); history: 403 `insufficient_scope` at `ff22b3a`; observed by Foundry in "Live run of PAT-303" |

Counts (equal to the table): 42 needs; 14 red (DHN-21, DHN-24 to DHN-26, DHN-32 to DHN-41);
0 unknown; 28 green (DHN-01 to DHN-20, DHN-22, DHN-23, DHN-27 to DHN-31, DHN-42).

### Live run of PAT-303

Run on 2026-10-20 (observed), DevHub revision `2edcea222eb47e2751b28c7ba461a49f78c09d1c`,
on two instances brought up for this run from an image built by Foundry itself (`git archive`
of that revision, then `docker build -f deploy/dogfood/Dockerfile` with
`DEVHUB_DEPLOY_REVISION` set to it; `GET /api/health` answered that `revision`), with the
compose file and `app.env` of "Bring up a disposable instance" including its hardening
lines, on loopback ports other than 3305, never production:

- Pass 1, instance without local accounts (`select local_accounts from account_workspace`
  answered false), all tests: 27 passed, 14 xfailed, 1 skipped (DHN-42), 0 failed.
  DHN-27 and DHN-28 passed; the 14 `xfailed` are the 14 red rows of the table.
- Pass 2, instance with local accounts (that query answered true after one owner was
  bootstrapped with DevHub `scripts/accounts-operator.ts`, as `deploy/qa/up.sh` does),
  `DEVHUB_LIVE_LOCAL_ACCOUNTS=1`, `-k dhn_42`: 1 passed, 41 deselected. DHN-42 passed.

DHN-27, DHN-28 and DHN-42 were then set to `green`.

### Not covered, on purpose

- The tombstone of an archived *source* (`tracker-contract.v1.json`,
  `tombstone-archived-source-after-switch`): DevHub is the target of the cutover, not its
  source.
- A full history migration: excluded by FOUNDRY-ADR-0026.
- The deployed DevHub version: no test; it is question 1.

### Closing a gap

When a red need is met, its strict `xfail` test XPASSes and the run fails. The owner then:
(1) replaces `@gap(...)` by `@need(...)` on that test, after checking that the test really
exercises the behaviour (DHN-26 and DHN-39 use placeholder inputs; PAT-303 checked this for DHN-27, DHN-28 and DHN-42); (2) sets the page status
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
7. **DHN-33**: Foundry appends an ADR-to-issue relation after an ADR is accepted. DevHub
   freezes `linked_issue_ids` once decided (`DEVHUB-73`). Is a relation outside the frozen
   text possible (a relationship route that accepts writes), or must Foundry stop doing it?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Not possible. Once an ADR leaves `proposed`, a trigger refuses any change of `title`, `body` or `linked_issue_ids` (`2edcea2:drizzle/0021_adr_decisions.sql:39-48`), and no relationship route accepts writes on a decided ADR (observed in source). Recommendation of the DevHub agent: set the ADR-to-issue relations before acceptance. Changing this contradicts DEVHUB-73 and awaits a human decision on the DevHub side; no commitment.
8. **DHN-34 / DHN-36**: for selective import, can an issue and an ADR carry an exact
   foreign source coordinate (for example `linear:issue:PAT-123`) that a lookup returns,
   and can an ADR keep its original id such as `FOUNDRY-ADR-0026` while the server's own
   ids stay `<KEY>-ADR-NNNN`? Foundry needs a replay that creates nothing twice.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Unsupported: ADR ids are generated `<KEY>-ADR-NNNN` (`2edcea2:src/tracker/store.ts:1999`) and neither issues nor ADRs have a foreign source coordinate or a lookup by one (observed in source). It would be a V1 contract extension (DEVHUB-ADR-0003), not done; it awaits a human decision on the DevHub side; no commitment.
9. **DHN-37**: what does the server guarantee about an `Idempotency-Key` replay after a
   lost response, and can a client later read the result by key or by title?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): A replay with the same `Idempotency-Key` and payload returns the stored response and status with `replayed: true` (`2edcea2:src/tracker/store.ts:1328-1330`); a different payload answers 409 `idempotency_conflict` (`store.ts:1322`); keys are bound to the credential identity (observed in source, DHN-07 passes). Deduced: after a lost response, replaying the same request with the same key recovers the result without a second effect. No route reads a result by key alone; search by title uses `?query=` (answer 3).
10. **DHN-38**: what should "append-only lifecycle" mean on DevHub (review and done proof
    history readable and never rewritten)? Foundry needs to tell a cadencer which receipts
    exist for an issue.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Server side done in DEVHUB-104 (merge `2edcea2`). Each successful proven `review` or `done` transition appends one row to `tracker_issue_proof_receipts` in the same transaction (`2edcea2:src/tracker/store.ts:1735-1744`); an idempotent replay or a refused transition appends nothing; a trigger refuses UPDATE and DELETE (`2edcea2:drizzle/0025_issue_proof_receipts.sql:24-33`). Read: `GET /api/tracker/v1/issues/:id/proof-receipts?cursor=&limit=`, oldest first, scope `tracker:read` (`2edcea2:src/app/api/tracker/v1/[...path]/route.ts:995-1010`, `store.ts:1748-1774`; fields and limits in `2edcea2:docs/TRACKER_API.md`, "Issue proof receipts"). Observed by the DevHub agent in its integration tests. DHN-38 stays a gap, now adapter-only: the flag `append_only_lifecycle_supported` and the reading of the receipts.
11. **DHN-39**: how is a release or Version created and addressed over the tracker v1 API,
    so that a test can map one and read its scope?
    Answer (DevHub agent, 2026-10-20, `2edcea2`): Versions exist in V1: `POST /projects/:key/versions`, `GET /versions/:id`, scope replace (`2edcea2:src/tracker/contract.ts:203-243`); ids are `<KEY>-VERSION-N` (`2edcea2:src/tracker/version-store.ts:359`); observed in source. Version names are not unique, so map by id (deduced). No server change is needed: the adapter stores the id and reads `GET /versions/:id` (deduced). DHN-39 stays a gap, adapter-only.
12. **DHN-25 / DHN-26**: does DevHub intend to carry a typed acceptance-override receipt and
    a human acceptance decision without a PR, or will Foundry keep a comment-based fallback?
    The end-to-end `issue merge` with an AC override on DevHub is unknown.
    Answer (DevHub agent, 2026-10-20, `2edcea2`): No typed acceptance-override receipt and no PR-less human acceptance route exist on DevHub (observed: none under `src/` at `2edcea2`). Whether DevHub will add them is unknown and nothing is planned in its tracker; awaiting a human decision on the DevHub side, no commitment. Foundry keeps the comment-based fallback for now.
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

Still open on the DevHub side, awaiting a human decision, no commitment: DHN-33 (answer 7), DHN-34 / DHN-36 (answer 8) and the question of typed receipts (answer 12). Adapter-side only, per the DevHub agent: DHN-21, 24, 25, 26, 32, 35, 37, 41, the DHN-38 flag, DHN-39 and DHN-40.

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
| Statuses of DHN-27, DHN-28, DHN-42 (`@need`, `green` after the live run of PAT-303), owner of DHN-38 and DHN-39 (`adapter`), counts (14 red, 0 unknown, 28 green), current DevHub reference `2edcea2`, answers 1 to 14 of the DevHub agent, awaiting-decision marks (DHN-33, DHN-34, DHN-36, question 12), cleanup by dropping the instance, section "Live run of PAT-303" (PAT-303) | Updated in "Production safety", "Bring up a disposable instance", "Needs", "Live run of PAT-303" and "Questions for the DevHub agent". DHN-38 and DHN-39 stay red (adapter-only); no adapter code changed. |
| `trackers/devhub.py`, `tracker-contract.md`, `tracker-contract.v1.json` | Not changed: no adapter behaviour, CLI verb or contract cell changed. |

Mechanical enforcement: `tests/test_devhub_requirements.py` checks that this table and the
test decorators carry the same ids, tests and red/not-red status, that every `@gap` carries
one `raises=`, that a gate refusal makes every test an error and never an `xfailed` (offline
run, no network), and that this page names the command and variables; it does not prove that
a sentence here is true (FOUNDRY-123 is the separate, not-yet-shipped documentation gate).
