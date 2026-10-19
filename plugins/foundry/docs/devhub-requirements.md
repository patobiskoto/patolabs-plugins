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
prerequisite that follows.

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
`DEVHUB_LIVE_URL not set: live DevHub conformance suite not requested`; the public CI
command excludes `integration` (`.github/workflows/ci.yml`), and the full local command
(`-m "not historical_fixture"`) reaches the suite only in that skipped form. The suite is
not part of `-m tracker_conformance` (`docs/tracker-conformance.md`): that suite is
deterministic over fake transports, this one needs a server.

Reading the result: a passing test is a need met; `xfailed` is a need not met yet (an
expected red, listed below with its owner); `XPASS(strict)` reported as a failure means a
gap closed and its test must be turned into a plain need (see "Closing a gap"); `failed`
on a need expected green is a regression or a wrong expectation to discuss on this page.

### Environment variables

| Variable | Role |
| --- | --- |
| `DEVHUB_LIVE_URL` | Origin of the disposable instance. Validated by `trackers.devhub.validate_base_url`: HTTPS, or cleartext HTTP on a numeric loopback literal only. |
| `DEVHUB_LIVE_CONFIRM` | Must be exactly `I_UNDERSTAND_DEVHUB_LIVE_SUITE_WRITES_TO_A_DISPOSABLE_INSTANCE`; anything else skips. |
| `DEVHUB_LIVE_TOKEN` | Bearer token (at least 24 characters, adapter rule). Never printed. |
| `DEVHUB_LIVE_PROOF_SECRET` | HMAC proof secret (at least 32 characters, adapter rule). Never printed. |

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
sees what the token can see. Use credentials created for the disposable instance. DevHub v1
has no delete endpoint (`docs/devhub-tracker.md`, "Destructive smoke"): every run leaves
`LV*` projects behind, so use a throwaway database.

## Needs

Status vocabulary: **green** = the test passed against a DevHub instance; **red** = the
need is not met (a strict `xfail` test, never a pass); **unknown** = the test exists and has
not been run against a DevHub instance. The owner column names who must act when the need
is red. Basis: *adapter* = observed in `trackers/devhub.py` / `trackers/base.py` (the
method is inherited from the `Tracker` default, checked by script on 2026-10-19);
*server* = deduced from the DevHub source at commit `3fedc57`, read-only, not run.

No test has been run against a DevHub instance when this page was written: every
`unknown` is unknown for that reason, and every `red` is deduced from code, not observed on
a server. Test names are in `tests/integration/test_devhub_live_conformance.py`.

| ID | Need | Test | Status | Owner of the gap | Basis |
| --- | --- | --- | --- | --- | --- |
| DHN-01 | Create or recover a repository-bound project, idempotently | `test_dhn_01_provision_project_is_repository_bound_and_idempotent` | unknown | - | not run |
| DHN-02 | Resolve the project from the canonical repository and refuse a contradicting registry binding | `test_dhn_02_resolve_project_by_canonical_repository` | unknown | - | not run |
| DHN-03 | Refuse an unauthenticated request | `test_dhn_03_unauthenticated_request_is_refused` | unknown | - | not run |
| DHN-04 | Create an issue; read back title, byte-exact body, fields, labels, AC counts, version | `test_dhn_04_create_issue_reads_back_fields_body_and_acceptance_counts` | unknown | - | not run |
| DHN-05 | Update only the named fields under the version check | `test_dhn_05_update_fields_changes_only_the_named_fields` | unknown | - | not run |
| DHN-06 | Refuse a stale version (409 `version_conflict`) without writing | `test_dhn_06_stale_version_is_refused_and_leaves_no_write` | unknown | - | not run |
| DHN-07 | Replay an idempotent write once; conflict (409 `idempotency_conflict`) on a divergent replay | `test_dhn_07_idempotent_replay_returns_one_effect_and_divergence_conflicts` | unknown | - | not run |
| DHN-08 | List a project's issues and narrow them by a free-text query | `test_dhn_08_search_lists_issues_and_narrows_by_query` | unknown | - | not run |
| DHN-09 | Paginate a project listing past 100 issues without loss or repeat | `test_dhn_09_search_paginates_past_one_hundred_issues` | unknown | - | not run |
| DHN-10 | Create a child under a parent; both ends show the link | `test_dhn_10_child_creation_links_both_ends` | unknown | - | not run |
| DHN-11 | `depends-on`, `blocks`, `relates` links round-trip with the inverse on the target | `test_dhn_11_dependency_links_round_trip_with_inverse` | unknown | - | not run |
| DHN-12 | Reparent an existing issue with `subtask-of` and `parent-of` | `test_dhn_12_existing_issue_can_be_reparented_in_both_directions` | unknown | - | not run |
| DHN-13 | Add comments; read them back in order with a creation time | `test_dhn_13_comments_round_trip_in_order` | unknown | - | not run |
| DHN-14 | Return every comment of an issue past 100 | `test_dhn_14_every_comment_is_returned_past_one_hundred` | unknown | - | not run; the issue read has no row limit in `store.ts:457` (server, observed) |
| DHN-15 | Bounded Epic subgraph projection holding the Epic and its children | `test_dhn_15_epic_subgraph_projection_is_bounded_and_complete` | unknown | - | not run |
| DHN-16 | One public audit receipt per create, update, transition, link, comment | `test_dhn_16_audit_projection_holds_one_receipt_per_operation` | unknown | - | not run |
| DHN-17 | Start path `backlog` to `in-progress`; resume from `blocked` | `test_dhn_17_start_path_and_resume_from_blocked` | unknown | - | not run |
| DHN-18 | `review` only with a valid signed proof (forged secret refused); return to `in-progress` | `test_dhn_18_review_needs_a_valid_proof_and_can_return_to_in_progress` | unknown | - | not run |
| DHN-19 | `done` with merge proof; `done` is terminal; a jump without a valid path is refused (409) | `test_dhn_19_done_is_terminal_and_unproven_jumps_are_refused` | unknown | - | not run |
| DHN-20 | Acceptance-checkbox sync patches only against the expected body; replay is a no-op; stale body conflicts | `test_dhn_20_acceptance_sync_patches_only_with_the_expected_body` | unknown | - | not run |
| DHN-21 | Update an issue body (frame/groom) checked against the expected body | `test_dhn_21_arbitrary_body_update_is_checked_against_the_expected_body` | red | adapter | adapter: `update_body` inherited, `BodyUpdateUnavailableError`; the body `PATCH` route exists (used by DHN-20) |
| DHN-22 | Close an Epic atomically from a receipt; replay; recover the original closure | `test_dhn_22_epic_closure_is_atomic_audited_and_replayable` | unknown | - | not run |
| DHN-23 | Refuse a closure whose graph differs from the receipt | `test_dhn_23_epic_closure_refuses_a_graph_that_differs_from_the_receipt` | unknown | - | not run |
| DHN-24 | Close an Epic over a dropped child by explicit acceptance (`--accept-dropped`, PAT-ADR-0017) | `test_dhn_24_epic_closes_over_a_dropped_child_by_explicit_acceptance` | red | adapter | adapter: `epic_dropped_closure_supported` is `False` and `write.py` refuses the flag without it; the DevHub closure itself already admits `dropped` children (`epic-closure-store.ts:182`, server, observed) |
| DHN-25 | Typed acceptance-override receipt before merge | `test_dhn_25_acceptance_override_leaves_a_typed_receipt` | red | adapter | adapter: `project_acceptance_override` inherited, `TrackerCapabilityUnavailableError`; the end-to-end `issue merge` with an AC override on DevHub has never been run (unknown) |
| DHN-26 | Accept a non-code issue by a human decision without a PR (PAT-ADR-0025) | `test_dhn_26_non_code_issue_is_accepted_by_a_human_decision_without_a_pr` | red | adapter | adapter: `accept_issue` inherited. The test passes a minimal decision document; the first implementation must complete it |
| DHN-27 | Abandon a started issue (`dropped` from `in-progress` or `review`) | `test_dhn_27_started_issue_can_be_abandoned` | red | server | server, deduced: `workflow.ts` transitions give `in-progress` only `review`, `blocked`, and `review` only `in-progress`, `blocked`, `done` |
| DHN-28 | Return a started issue to `backlog` | `test_dhn_28_started_issue_can_return_to_backlog` | red | server | server, deduced: same table, no edge to `backlog` from `in-progress` or `review` |
| DHN-29 | Create an ADR as `proposed` with an id `<KEY>-ADR-NNNN`; list it with its exact body | `test_dhn_29_adr_is_created_proposed_and_listed_with_its_exact_body` | unknown | - | not run |
| DHN-30 | ADR status `proposed` to `accepted` to `deprecated` with a signed proof | `test_dhn_30_adr_status_moves_proposed_to_accepted_to_deprecated` | unknown | - | not run |
| DHN-31 | `accepted` to `superseded` through the status route in Foundry mode | `test_dhn_31_accepted_adr_can_be_marked_superseded_through_the_status_route` | unknown | - | not run; `assertAdrTransition` allows it (`contract.ts:269`, server, observed) but `DEVHUB-73-ADR-DECISIONS.md` limits Foundry projects to recorded intents for decisions (unknown which applies to the status route) |
| DHN-32 | Supersede an ADR by a named replacement | `test_dhn_32_adr_is_superseded_by_a_named_replacement` | red | adapter | adapter: `supersede_adr` inherited, `TrackerCapabilityUnavailableError` |
| DHN-33 | Append a reciprocal ADR-to-issue relation to a decided ADR | `test_dhn_33_decided_adr_gains_a_reciprocal_issue_relation` | red | adapter + server | adapter: `link_adr_issue` inherited; server: decided ADR links are frozen (`DEVHUB-73-ADR-DECISIONS.md`, observed in the document, not run) |
| DHN-34 | Selective import: a living issue is imported once, found by exact source coordinate, replay-safe | `test_dhn_34_living_issue_is_imported_once_by_exact_source_coordinate` | red | adapter + server | adapter: `migration_find_issue`, `migration_import_issue` inherited; server: no source coordinate on issues known (unknown) |
| DHN-35 | Selective import: relations between imported issues through the staged port | `test_dhn_35_imported_issues_are_linked_through_the_staged_port` | red | adapter | adapter: `migration_link_issue` inherited |
| DHN-36 | Selective import: a needed ADR keeps its foreign id (for example `FOUNDRY-ADR-0026`), status and exact body | `test_dhn_36_adr_is_imported_with_its_foreign_id_status_and_exact_body` | red | adapter + server | adapter: `import_adr`, `migration_import_adrs` inherited; server: ADR ids are generated `<KEY>-ADR-NNNN` (`store.ts:1950`, observed) |
| DHN-37 | Reconcile an interrupted issue creation without recreating it (Epic cadencer) | `test_dhn_37_an_issue_creation_can_be_reconciled_without_recreating_it` | red | adapter | adapter: `reconcile_issue_creation` inherited; `campaign_cadence.missing_capabilities` reports `issue_creation_reconciliation` |
| DHN-38 | Append-only lifecycle capability required by the Epic cadencer (PAT-ADR-0026) | `test_dhn_38_lifecycle_is_append_only_for_the_cadencer` | red | adapter + server | adapter: `append_only_lifecycle_supported` is `False` (script, 2026-10-19); what DevHub must provide for it is a question below |
| DHN-39 | Read a mapped release scope without approximate discovery | `test_dhn_39_a_mapped_release_scope_is_read_without_approximate_discovery` | red | adapter + server | adapter: `read_release_scope` inherited, `ReleaseScopeUnavailableError`; how a release is mapped on DevHub is a question below |
| DHN-40 | DevHub accepted as a V1 tracker of the repository | `test_dhn_40_devhub_is_a_v1_tracker_of_the_repository` | red | foundry | `registry._V1_TRACKERS` excludes `devhub`; the Epic cadencer also needs DHN-37 and DHN-38 |

Counts (equal to the table): 40 needs; 15 red (DHN-21, DHN-24 to DHN-28, DHN-32 to DHN-40);
25 unknown, DHN-31 among them (its expectation is doubtful); 0 green.

### Not covered, on purpose

- The tombstone of an archived *source* (`tracker-contract.v1.json`,
  `tombstone-archived-source-after-switch`): DevHub is the target of the cutover, not its
  source.
- A full history migration: excluded by FOUNDRY-ADR-0026.
- The deployed DevHub version: no test; it is question 1.

### Closing a gap

When a red need is met, its strict `xfail` test XPASSes and the run fails. The owner then:
(1) replaces `@gap(...)` by `@need(...)` on that test, after checking that the test really
exercises the behaviour (DHN-26 and DHN-39 use placeholder inputs); (2) sets the page status
to `unknown`, then `green` after a run against a DevHub instance; (3) updates the counts
above. `tests/test_devhub_requirements.py` keeps the page and the decorators equal.

## Questions for the DevHub agent

Numbered so an answer can cite them. "Unknown" means Foundry has no evidence either way.

1. **Deployed version**: how can a client read which DevHub build it talks to? Foundry
   knows of no version endpoint (unknown); the page cannot say which commit the first live
   run covers.
2. **Disposable instance**: what is the supported way to get a throwaway instance
   (database reset, compose profile)? What scopes must `DEVHUB_LIVE_TOKEN` carry for the
   tests above? Is there a way to delete or archive the `LV*` projects a run leaves?
3. **DHN-08**: what does the free-text `query` of the issue listing match (title, body,
   id)? The test expects exactly the one issue whose title holds a random token.
4. **DHN-14**: is there any cap on comments, or on the response size, that would truncate
   an issue read? The adapter refuses a response over 8 MiB (`_MAX_RESPONSE_BYTES`).
5. **DHN-27 / DHN-28**: Foundry abandons and returns to `backlog` issues that are already
   started, with an expected predecessor state. Will DevHub allow `dropped` from
   `in-progress` and `review`, and `backlog` from `in-progress` and `review`, for a
   Foundry-governed project, or is another route intended?
6. **DHN-31**: does `POST /adrs/:id/status` accept `superseded` for a Foundry-governed
   project, and with which replacement information?
7. **DHN-33**: Foundry appends an ADR-to-issue relation after an ADR is accepted. DevHub
   freezes `linked_issue_ids` once decided (`DEVHUB-73`). Is a relation outside the frozen
   text possible (a relationship route that accepts writes), or must Foundry stop doing it?
8. **DHN-34 / DHN-36**: for selective import, can an issue and an ADR carry an exact
   foreign source coordinate (for example `linear:issue:PAT-123`) that a lookup returns,
   and can an ADR keep its original id such as `FOUNDRY-ADR-0026` while the server's own
   ids stay `<KEY>-ADR-NNNN`? Foundry needs a replay that creates nothing twice.
9. **DHN-37**: what does the server guarantee about an `Idempotency-Key` replay after a
   lost response, and can a client later read the result by key or by title?
10. **DHN-38**: what should "append-only lifecycle" mean on DevHub (review and done proof
    history readable and never rewritten)? Foundry needs to tell a cadencer which receipts
    exist for an issue.
11. **DHN-39**: how is a release or Version created and addressed over the tracker v1 API,
    so that a test can map one and read its scope?
12. **DHN-25 / DHN-26**: does DevHub intend to carry a typed acceptance-override receipt and
    a human acceptance decision without a PR, or will Foundry keep a comment-based fallback?
    The end-to-end `issue merge` with an AC override on DevHub is unknown.
13. **Availability**: after a cutover, every Foundry command of the repository depends on
    the one DevHub instance (deduced: a single tracker per repository, FOUNDRY-ADR-0026).
    What is the backup and restore procedure, and how does a client tell a redeployment
    from an outage? No test of this page covers it.

## Documentation status (AGENTS.md#R5, FOUNDRY-ADR-0018)

| Artifact | Status |
| --- | --- |
| This page (needs, tests, statuses, questions) | Added by PAT-296. |
| Command `pytest -m devhub_live tests/integration/test_devhub_live_conformance.py` and the `devhub_live` marker (`pytest.ini`) | Documented in "Run the suite". |
| Environment variables `DEVHUB_LIVE_URL`, `DEVHUB_LIVE_CONFIRM`, `DEVHUB_LIVE_TOKEN`, `DEVHUB_LIVE_PROOF_SECRET` | Documented in "Environment variables". |
| Safety checks of `tests/devhub_live_gate.py` | Documented in "Production safety". |
| `docs/devhub-tracker.md`, `docs/tracker-conformance.md` | One pointer line each to this page; no other change. |
| `trackers/devhub.py`, `tracker-contract.md`, `tracker-contract.v1.json` | Not changed: no adapter behaviour, CLI verb or contract cell changed. |

Mechanical enforcement: `tests/test_devhub_requirements.py` checks that this table and the
test decorators carry the same ids, tests and red/not-red status, and that this page names
the command and variables; it does not prove that a sentence here is true (FOUNDRY-123 is
the separate, not-yet-shipped documentation gate).
