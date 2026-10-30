# Changelog

## Unreleased

## 2.9.0 — 2026-10-30

Minor release for publishing KPI records at merge and for finding the session logs: a
successful `issue merge` now reads the session logs of the merged issue and publishes its
KPI records to a tracker that declares `kpi_publication_supported`, never as a gate
(PLP-343, PLP-ADR-0063); `routing kpi-export` without `--session` or `--codex-session`
now searches the logs itself and `routing kpi-publish` sends a changed record as a revision
(PLP-342); the Claude plugin manifest declares the `DEVHUB_METRICS_TOKEN` option (PLP-337);
`foundry:release` gains a GitHub Release STOP (PLP-29). The other entries change readers,
documentation or tests. Preparation status and upgrade references are in
`docs/release-2.9.0.md` and `docs/migration-2.9.0.md`.

- PLP-345: the last request of a Claude subagent log with no terminal snapshot takes its counters from the parent's `toolUseResult.usage` when the link is strict (one result for the `agentId`, same `toolUseId` as the `*.meta.json`, equal input counters), so it is no longer a lower bound and a fully completed `routing kpi-export` attempt is no longer `partial` (a revision for DevHub); any other case keeps the PLP-344 lower bound with a closed refusal reason in `diagnostics`; see `docs/cost-attribution.md` and `docs/kpi-export.md`.
- PLP-344: a Claude log with requests that have no terminal snapshot (no record with a `stop_reason`, observed in subagent logs of Claude Code 2.1.286, 2.1.299 and 2.1.304) is no longer rejected whole: `read_host_log` counts those requests with the largest value observed for each counter and marks the slice `lower_bound_requests`; `routing token-attribution`, `rounds-report` and `routing kpi-export` propagate the marker (the KPI `provenance` says "lower bound" and the amount is `partial`); an identity change, a conflicting terminal usage or an incomplete log stay blocking; see `docs/cost-attribution.md` and `docs/kpi-export.md`.
- PLP-343: a successful `issue merge` now reads the session logs of the merged issue and publishes its KPI records to a tracker that declares the capability (PLP-ADR-0063): one summary line (`kpi: created=… revised=… replayed=… refused=… not_sent=…`) or one warning line, never a gate, merge output and exit code unchanged; `routing kpi-publish` reports `revised`; see `docs/kpi-export.md`, "Publication at merge".
- PLP-342: `routing kpi-export` without `--session`/`--codex-session` now finds the Claude and Codex session logs of the issues itself (only in this repository's host log directories, only logs holding an invocation recorded for the issue; none or ambiguous stays `unavailable`), and `routing kpi-publish` keeps a local ledger of what it published and sends a record whose source changed as a revision (`revision` + 1, `supersedes_id`) instead of an independent record; see `docs/kpi-export.md`.
- PLP-341: Documented that a terminal `blocked` proof keeps its diff deduplicated (`prepare-review` refuses with "active private claim required", `recover-review` refuses a completed review, a new round needs a diff-changing correction) and what is observed or deduced for a block that needs no diff change; see `docs/review-rounds.md`.
- PLP-338: Made two flaky tests deterministic (`test_pat111_ac3_a_comparison_with_an_unsettled_local_start_is_inconclusive` pins the measured `wall_seconds` its decision compares; `test_real_total_deadline_bounds_headers_with_frozen_accounting_clock` uses a 1 s real deadline so the worker reaches `getresponse()` first); tests only.
- PLP-337: nits deferred from earlier tickets: the Claude plugin manifest declares the `DEVHUB_METRICS_TOKEN` option (sensitive); `issue merge` reads the DevHub URL and token before searching the session logs; `git worktree list` of the log search has a 10 s timeout; `scripts/release_notes.py` prints one line on standard error when it cuts the body and refuses a malformed version with a message; documentation and test corrections (`docs/kpi-export.md`, `docs/ci-wait.md`, `docs/cost-attribution.md`, `docs/review-rounds.md`).
- PLP-324: Qualified Claude subagent logs without a terminal record on real local logs (`docs/qualification/plp-324-subagent-terminal-snapshots.md`): input and cache counters match the terminal snapshot, `output_tokens` does not, so the PLP-344 lower bound stays; method `exact` observed on a real session. Documentation only, no reader change.
- PLP-322: `docs/devhub-tracker.md` no longer says Foundry does not migrate YouTrack data; a project with history goes through `cutover preflight|copy|activate devhub`, `registry bootstrap devhub` stays for a project with no prior writes.
- PLP-321: `routing kpi-export` now lists an attempt without a readable time in `omitted` (`no_recorded_time`), puts `supersedes_id` under `provenance.null`, names the escalation time an estimate and no longer fails on a non-hashable `pr` in the campaign journal; `routing token-attribution` counts one log once whatever the path spelling; `routing review-turns` keeps `receipt_unreadable` instead of the journal count; documentation corrections in `docs/model-routing.md`, `docs/cost-attribution.md`, `docs/review-rounds.md`, `docs/kpi-export.md` and the PLP-324 qualification page.
- PLP-29: `foundry:release` gains a STOP "GitHub Release" after the tag (steps after it move up by one): `gh release create foundry-v<version> --verify-tag --latest` publishes the `## <version>` section of `CHANGELOG.md` printed by the new read-only helper `scripts/release_notes.py` (cut at a line boundary below 125,000 characters, with a link to the CHANGELOG at the tag), and the Release URL, state and Latest mark join the readback recorded on the release ticket (`skills/release/SKILL.md`).

## 2.8.1 — 2026-10-29

Patch release: the CI gate of `issue merge` and `issue wait-ci` no longer blocks on
check-runs of an older workflow run on the same sha when a newer run of the same check is
green (PLP-336). The deduplication rule and its limits are in `docs/ci-wait.md`.

- PLP-336: The CI gate no longer blocks on an obsolete `cancelled` or failed check-run of an older workflow run on the same sha: `check_runs` keeps the newest run per (app, check name) on the aggregate route as it already did on the fallback; the limits are in `docs/ci-wait.md`.

## 2.8.0 — 2026-10-28

Minor release for publishing KPI records to DevHub: the new explicit verb
`routing kpi-publish` sends a `routing kpi-export` file to DevHub with the new secret
`DEVHUB_METRICS_TOKEN` and the new adapter capability `kpi_publication_supported`
(PLP-325). PLP-335 changes documentation only. Preparation status and upgrade references
are in `docs/release-2.8.0.md` and `docs/migration-2.8.0.md`.

- PLP-335: the async-launch marker (`_is_background_launch`, `hooks/observe_agent.py`) is now recorded as observed in a real `PostToolUse` hook payload (2026-10-28, Foundry 2.7.0, background Agent), no longer as deduced; which of its two keys carried it stays not observed; the cause it relies on, `PostToolUse` firing at launch for a background Agent, is now recorded as observed too; no code change (`docs/model-routing.md`).
- PLP-325: new explicit verb `routing kpi-publish FILE --project KEY` sends the batches of a `foundry-kpi-export.v1` file to DevHub `observations:batch` and reports `created`/`replayed`/refused per record; it needs the new secret `DEVHUB_METRICS_TOKEN` (stored with `configure credential --name DEVHUB_METRICS_TOKEN`, or the environment) and the new adapter capability `kpi_publication_supported` (DevHub only); no hook, gate, merge or campaign uses it (`docs/kpi-export.md`).

## 2.7.0 — 2026-10-28

Minor release for the escalation time, the KPI export readers and DevHub transitions:
the escalation failure receipt gains an optional `decided_at` (PLP-326), the price grid
covers the Claude 5.5 and Fable 5.1 models routed by Foundry (PLP-328), a Claude Agent
launched in the background is recorded `unknown` instead of `completed` (PLP-329), a
Sonnet 5.5 amount holding cache writes is marked `partial` (PLP-14), and on DevHub
`edit transition <ID> dropped|backlog <expected-state>` works and the answer of a
`dropped`/`backlog` write is read back (PLP-332, PLP-334). A return to 2.6.0, or a host left
on it, is unsafe once a new escalation receipt exists (`docs/migration-2.7.0.md`, "Return to
2.6.0"). PLP-330 changes tests only. Preparation status and upgrade references are in
`docs/release-2.7.0.md` and `docs/migration-2.7.0.md`.

- PLP-334: on DevHub, `set_state` now refuses an unexpected transition context with a message naming its actual cause (the state admits no context, a proof field was passed, or the expected state is missing) and, after a `dropped`/`backlog` POST whose answer carries a `state` other than the target, raises `TrackerConflictError`; CI installs `pytest==9.2.0` and `pytest-xdist==3.8.0` instead of unpinned versions (`docs/devhub-tracker.md`, "Guarded `dropped` and `backlog` transitions", `.github/workflows/ci.yml`).
- PLP-332: on DevHub, `edit transition <ID> dropped|backlog <expected-state>` no longer fails with `preuve de transition inattendue`: the issue is re-read and written only if its state is the expected one, otherwise it is refused naming both states (bounded check, not a CAS, PAT-ADR-0006); any other context is refused with a message naming its cause (`docs/devhub-tracker.md`, `trackers/devhub.py::set_state`).
- PLP-330: `tests/conftest.py` accepts both return shapes of pytest's mark-expression parser (a bare `ast.Expression` up to pytest 9.1, a tuple from 9.2), so collection no longer fails with `AttributeError` on pytest 9.2.
- PLP-329: a Claude Agent launched in the background is no longer recorded `completed` in the local invocation record (PLP-316) at launch: `hooks/observe_agent.py` records `unknown`/`unknown` when `tool_response` carries `isAsync` true or `status` `async_launched` (deduced that the hook payload carries the marker seen in transcripts), and `routing kpi-export` emits that attempt as `unknown` with no end time and a provenance saying why; the real end stays unobserved (no hook wired). A synchronous launch is unchanged (`docs/model-routing.md`, `docs/kpi-export.md`).
- PLP-328: the frozen price grid `pricing-v1.json` now prices the Claude models `sonnet-5.5`, `haiku-5.5`, `opus-5.5` and `fable-5.1` (Anthropic pricing page read on 2026-10-27, effective from that day; earlier validity unknown), so `routing kpi-export` gives an `estimated` amount for their attempts, `haiku-5.5` included: its 100000-token threshold is applied request by request (`over_threshold` in `cost_attribution.py`), and the amount stays `null` with reason `threshold_pricing_needs_per_request_tokens` only when per-request counts are missing (a codex row without request granularity). Existing entries and their `rate_version` are unchanged. `load_price_grid` no longer rejects a declared new version (`claude-sonnet-5-5`) as overlapping the historical wildcard (`claude-sonnet-5-*`); `price_for` also stops any `claude-sonnet-5-5-<suffix>` (dated or not) from matching it (`docs/cost-attribution.md`, `docs/kpi-export.md`).
- PLP-326: every escalation receipt written from now on carries `decided_at`, the UTC time observed when the escalated decision was recorded (`escalation.py`); `routing kpi-export` uses it as the observed time of the `escalation` record and keeps the deduced upper bound, marked `deduced`, for older receipts. No routing or escalation decision reads it (`docs/kpi-export.md`). The tags foundry-v2.0.0, 2.1.0, 2.1.1, 2.2.0, 2.3.0, 2.4.0, 2.5.0 and 2.6.0 reject a ledger holding such a receipt as invalid (the check was read in each tag, not run; other versions not checked), so routing fails closed for that issue there: do not go back to such a version, or run one on another host, once a new escalation receipt exists.
- PLP-14: `routing kpi-export` marks the `amount` of a Claude attempt on Sonnet 5.5 `partial` (a lower bound) when it holds cache-write tokens, because the 1h cache writes those profiles ask for are priced at the 5m rate (the number is unchanged; other models stay `complete`), and an attempt with no completion event (every Codex attempt) no longer gets a provenance that mentions a background launch; `registry.verify_existing_project` and `setup_project.setup` now carry the message of the DevHub governance refusal only, any other adapter error keeps the generic message; the `docs/devhub-tracker.md` governance section, the `kpi-export.md` pricing, fingerprint and escalation compatibility statements and the "prepared" wording for published releases are corrected (`docs/kpi-export.md`, `docs/devhub-tracker.md`).

## 2.6.0 — 2026-10-27

Minor release for the local invocation record and the offline KPI readers of Epic
PLP-315 (PLP-ADR-0061): `routing review-invocations`, `routing token-attribution`,
`routing review-turns` and `routing kpi-export` (PLP-316 to PLP-319). Behaviour sources
are in `docs/model-routing.md`, `docs/cost-attribution.md`, `docs/review-rounds.md` and
`docs/kpi-export.md`; preparation status and upgrade references are in
`docs/release-2.6.0.md` and `docs/migration-2.6.0.md`.

- PLP-319: add `routing kpi-export ISSUE... --output FILE`, a read-only offline command that writes the attempt, review-round and escalation records of issues in the `devhub-observations.v2` format, with value-by-value provenance and an `estimated` amount carrying its `rate_version` (`docs/kpi-export.md`); `routing token-attribution` keeps its output and `routing review-turns` gains `started_at`.
- PLP-318: add `routing review-turns ISSUE`, a read-only offline command that lists the review turns of an issue from the local acceptance proofs: rank, head, base, verdict, remark count (campaign receipt, else the campaign attempts journal), `type` (full or partial, from the campaign attempts journal only) and requested reviewer model (PLP-316 invocation record); any unrecorded value is `null` with a reason (`docs/review-rounds.md`).
- PLP-317: add `routing token-attribution`, a read-only offline command that attaches the token slices read from Claude Code and Codex session logs to a Foundry issue with a method (`exact` through the PLP-316 invocation record, `window` from the coordinator's `issue start` to `issue merge` calls, `approximate` for unrecorded history, declared `overlap`, or `unattributed` with a reason) and to a Foundry role (`null` when not observable); `read_host_log` gains an opt-in `timestamps` argument and its default output is unchanged (`docs/cost-attribution.md`).
- PLP-316: record locally, beside the escalation and review-dedup stores, the issue, host, role, requested model, effort, tier, status and reviewed head of each routed implementer and reviewer invocation (Claude and Codex), and add `routing review-invocations` to attribute the implementer and correction invocations to a reviewed head (`null` when undeterminable); nothing in routing, gates or merge reads it and the review proof is unchanged (`docs/model-routing.md`).

## 2.5.0 — 2026-10-26

Minor release for explicit same-key source issue numbers and historical ADR
`rejected` read/import support (PLP-311). Behaviour sources and qualification limits
are in `docs/devhub-tracker.md`, "Explicit source issue numbers" and "Selective
import"; preparation status and upgrade references are in `docs/release-2.5.0.md`
and `docs/migration-2.5.0.md`.

- PLP-311: DevHub migration can explicitly preserve same-key issue numbers with
  `Project.extra.preserve_issue_numbers`; the preflight and readback require exact ids,
  and server refusals keep the requested numbering (`DevHubTracker._snapshot_issue_number`,
  `migration_import_issue`, `_import_terminal_issue`, `migration_find_issue`).
  Historical ADR `rejected` is supported by read/import without adding a native decision
  target (`_ADR_IMPORT_STATES`, `_to_adr`, `_prepare_adr_import`; `_ADR_STATES` unchanged).
  Disposable-instance qualification is pending DHN-50 (`docs/devhub-requirements.md`).

## 2.4.0 — 2026-10-25

Minor release for the reviewed technical-diagnostic completion command
(`routing escalation complete-technical`, PLP-312; contract and authority limits in
`docs/model-routing.md`, "Complete a reviewed technical diagnostic before remaining
qualification"), the Linear comment-reply exception (PLP-1;
`docs/devhub-tracker.md`, "Comment exceptions"), and the dated cutover records PLP-2
and PLP-310 (`docs/qualification/pat-334-cutover.md`). Release scope, preparation
status and upgrade/rollback limits are in `docs/release-2.4.0.md` and
`docs/migration-2.4.0.md`.

- PLP-312: close a consumed technical diagnostic through `routing escalation complete-technical`
  after its canonical review proves `quality=mergeable` with only `pass`/`not_covered`
  criteria, so remaining qualification can be prepared under the preserved routing floors
  (`EscalationStore.complete_technical_remediation`, `ReviewDeduplicator.validated_technical_completion`).
  The audited transition keeps uncovered criteria non-passing and grants no provider,
  campaign or product acceptance authority; see the completion procedure in `docs/model-routing.md`.
- PLP-310: the PAT-334 cutover report now records the headless Codex session run after activation (Foundry 2.3.0 CLI of the Codex installation, `doctor` green, `PLP-2` read) and the later quarantine of the three red registry entries; see `docs/qualification/pat-334-cutover.md`.
- PLP-2: this repository is bound to DevHub project `PLP` after its cutover from Linear on 2026-10-23 (PAT-334, PAT-ADR-0032); the run, its figures and its checks are in `docs/qualification/pat-334-cutover.md`.
- PLP-1: review remarks deferred from PAT-331 (Epic `Nits PAT-295`) are settled. Behaviour change: the Linear history export reads the `parent` of each comment (`_HISTORY_COMMENT_FIELDS`), and `capture_manifest` names a reply in a comment thread as a `comment_exceptions` entry (`COMMENT_REPLY_EXCEPTION`, `migration.py`) toward any target instead of flattening it; a top-level comment is unchanged, and the exception does not keep `ready_for_cutover` false. `DevHubTracker._numbering_order` keeps its single-order choice, now documented as a limit (a corpus can be refused although another order would number cleanly; fails closed before any write). Tests: the reply exception and the `parent` field, the real call order of `migration_import_adrs`, the ADR source date bound in `_prepare_adr_import`, the documented numbering limit. Documentation status (R5): `docs/devhub-tracker.md` ("Comment exceptions", points to watch of PAT-332 for ADR version chains and source dates and for the strict read of one issue, "Corrections from the PAT-332 rehearsal": numbering limit and date bound citation), `docs/qualification/pat-334-cutover.md` (prerequisite, registry entry, 27 `FOUNDRY-ADR-*`), `docs/qualification/pat-332-cutover-rehearsal.md` (quota observed), `docs/linear-tracker.md` (paragraph reflow, no word changed) and the root `README.md` (four qualified tracker variants) updated; `AGENTS.md`/`CLAUDE.md` not touched.

## 2.3.0 — 2026-10-23

Minor release. DevHub is admitted as the fourth V1 provider of the registry and of the tracker contract (`registry bootstrap devhub`, `setup_project`, PAT-323, PAT-ADR-0031) and the DevHub pilot path is retired (PAT-323); `cutover` carries the terminal and archived issues, their links and the comments of every issue toward DevHub (PAT-331, PAT-ADR-0032), after the rehearsal of PAT-332 (`docs/qualification/pat-332-cutover-rehearsal.md`), which needs a DevHub at `72a24e2` or later (`docs/devhub-tracker.md`, "Corrections from the PAT-332 rehearsal"); `issue accept` and `issue accept --status` run on DevHub (PAT-330, PAT-ADR-0033); the DevHub adapter gains the routes listed in the entries of PAT-309 to PAT-321 and PAT-324 to PAT-328; `-h`/`--help` prints the usage and writes nothing in the verbs named by PAT-286, which also makes `--ac-override-reason` an option of `issue merge` only and has `issue openpr <ISSUE-ID>` refuse a current branch that is not that issue's branch; the campaign cadencer defers the remarks of a ticket with no origin Epic to `Nits` (PAT-291) and tries the proof carry when the PR base is unchanged (PAT-294, PAT-288); the Linear usage limit is a durable refusal (PAT-310). The cutover of this repository toward DevHub is not done by this release.

- PAT-332: five corrections found by the rehearsal of the Linear cutover of this repository toward disposable DevHub instances (`890e338`, then `72a24e2`). The Linear history export reads children, relations and inverse relations with `includeArchived: true` (`_ISSUE_HISTORY_QUERY`, history export only; `get_issue` unchanged), so archived Epics keep their children and archived links are two-sided. New port member `Tracker.migration_epic_only_parent` (default `False`, `True` on DevHub): `capture_manifest` records a `parent-of` / `subtask-of` link whose parent is not an `Epic` (or has its type excepted) as a `links` exception on both ends with cause `parent is not an Epic on the target`, and neither writes nor compares it. `DevHubTracker.migration_import_adrs` writes first the ADRs that keep their number (`<KEY>-ADR-NNNN`), then the others, replacements still first, and refuses before the first write, naming the ADR, one that would claim a held number. `_IMPORT_EPOCH_MAX` is now 253402300799999 (9999-12-31T23:59:59.999Z) and also bounds terminal issue and ADR source dates. `_IMPORT_COMMENT_MAX` is now 200000 (DevHub `MAX_COMMENT_TEXT`, DEVHUB-131, `72a24e2`); the cutover needs a DevHub at `72a24e2` or later. Documentation status (R5): `docs/devhub-tracker.md` ("Corrections from the PAT-332 rehearsal") and `docs/linear-tracker.md` ("History export of archived links") updated; the new port member is named there. Rehearsal report: `docs/qualification/pat-332-cutover-rehearsal.md`.
- PAT-331: the cutover manifest now covers terminal (`done`, `dropped`) and archived issues of the source when the target is DevHub, imported frozen through the DevHub terminal import route with an exact readback of state, `pr_url` and source dates; their links are copied, read back and replayed like those of living issues, and the comments of every issue (living or terminal, Foundry lifecycle receipts included) are copied through the DevHub comment import route and read back exactly as a set keyed by source reference, a comment DevHub cannot store unchanged being a named manifest exception, so nothing is pending any more and the cutover can be ready (stages 1 to 3; live need DHN-49 green). An imported terminal child of a living DevHub Epic counts as terminal for the Epic closure (`done`: accepted with the source `devhub-imported-terminal`; `dropped`: no named closure owed) and an imported `done` issue reads `unavailable` in a release scope; the manifest names an exception `labels` / `milestone` (with the source value) for every issue that has one, since no target copies them, and likewise `pr_url` / `source_dates` for a living issue that has them (a terminal issue carries both), and `links` for a link to an issue outside the manifest or a source relation with no link type, and `comments` when the source exports none toward a target that copies them; an archived source issue is left out toward a target without terminal import.
- PAT-330: `issue accept` and `issue accept --status` run on DevHub, under the conditions of PAT-ADR-0025 as amended by PAT-ADR-0033 (Linear and DevHub qualified, YouTrack and GitHub Projects still refuse before any read). `DevHubTracker` declares `human_acceptance_supported = True`, `human_acceptance_predecessors` (`acceptance_decision.FROM_STATES`) and `human_acceptance_source = "devhub-human-acceptance"` (`trackers/devhub.py`); the new port method `Tracker.observe_human_acceptance` (`trackers/base.py`, default `observe_issue`) backs `--status` on DevHub, from the issue and its typed `human_acceptance` receipt, and `write.issue_acceptance_state` compares the source with `tracker.human_acceptance_source` (Linear keeps `linear-human-acceptance`). An issue accepted by decision on DevHub is also counted by the Epic closure (`write.close_epic` reads children through the new optional `observe_epic_closure_child`, one extra receipt read only for a `done` child with unchecked criteria) and read `accepted` by `read_release_scope` with the references `acceptance_decision_digest` and `proof_receipt_id`. On DevHub, two decision receipts, a decision next to a PR receipt and a body or criteria changed after the decision are never read as accepted: `--status` reads `inconnu`, the Epic closure keeps the criteria unchecked and the release scope reads `unavailable` (`DevHubTracker._valid_human_decision`); the release rule also requires an empty `pr_url` and at least one criterion. The refusal message now reads "seuls Linear et DevHub sont qualifiés". `get_issue` and `observe_issue` are unchanged. The `human-acceptance-decision` cell of DevHub in `docs/tracker-contract.v1.json` is `supported`; offline tests in `tests/test_devhub_tracker.py`. The live need DHN-48 (`tests/integration/test_devhub_live_conformance.py`) drives the command and its `--status` against the live adapter and is green on a disposable instance built from DevHub `3f0cf4b` (`docs/devhub-requirements.md`, "Live run of PAT-330").
- PAT-329: `cutover activate` toward DevHub is qualified offline (FOUNDRY-ADR-0026, PAT-ADR-0031): `tests/test_tracker_migration.py` runs `preflight`, `copy` and `activate` through `cutover.main` with an isolated registry (`FOUNDRY_DATA`) and checks the published `devhub` entry (`key`, `id`, `canonical_repo`), the archived source, the marker and the fail-closed refusal of a divergent manifest. The live test DHN-47 (`tests/integration/test_devhub_live_conformance.py`) runs the same flow toward a new project of a disposable instance with a temporary registry; it is green twice on a disposable instance built from DevHub `3f0cf4b` (`docs/devhub-requirements.md`, "Live run of PAT-329"). No adapter, registry or CLI code changed.
- PAT-328: `DevHubTracker.migration_preflight` now refuses, before any write, two cases DevHub refused only during the copy (FOUNDRY-ADR-0026). (1) An ADR whose replacement (`superseded_by`) is `proposed` in the corpus is refused, naming the ADR (DevHub answers 409 `invalid_transition`, DEVHUB-118; an `accepted`, `superseded` or `deprecated` replacement is accepted). (2) The preflight makes a third read, `GET /api/tracker/workflow/v1/projects/:key` (`devhub-workflow.v1`), and refuses, naming the field, the value and the source coordinate, a `type`, `priority` or `estimate` value, or a creation state, that the published workflow disables; the rule is DevHub's import rule as relayed by its agent from source at `cd23304` (nothing is checked while the workflow `version` is 1; above, an omitted value counts as `null` and the match is strict). A refused or malformed workflow read is a refusal naming the cause. The disabled value is a preflight refusal, not an entry of `migration_attribute_exceptions`. Limits that stay: a publish between the read and the write, and a replay of an already imported `source_ref`, can change the server's answer (best effort). `_req` gains an `api_prefix` keyword for the workflow route. Live suite: DHN-44 (workflow, needs `DEVHUB_LIVE_WORKFLOW_PUBLISH`, an executable that publishes a workflow on the disposable instance) and DHN-45 (proposed replacement), both green on a disposable instance built from DevHub `cd23304` ("Live run of PAT-328" in `docs/devhub-requirements.md`; counts 45 needs, 0 red, 0 unknown, 45 green). Documentation status (R5): `docs/devhub-tracker.md` ("Selective cutover target"), `docs/devhub-requirements.md` (DHN-44, DHN-45, environment variable, question 19, counts) and the evidence of the DevHub `cross-tracker-live-work-copy` cell of `docs/tracker-contract.v1.json` updated; `tests/fixtures/tracker-conformance-v1.json` gains two cases.
- PAT-327: the DevHub ADR import now carries the source-only issue references and the `unknown` relation families of an ADR (FOUNDRY-ADR-0026, PAT-ADR-0003, PAT-ADR-0008), through the two fields of DEVHUB-122 (merge `3f0cf4b98e60a04154cf051e4110cbf29094d381`, `docs/TRACKER_API.md` of DevHub). `DevHubTracker.import_adr` sends `source_only_refs` and `unknown_relations` (each only when non-empty) and refuses an answer that does not carry both exactly as sent; `migration_export_adrs` returns the references in the order stored (`source_only_issue_refs`) and exports a family declared unknown as `unknown`; `migration_preflight` no longer refuses a corpus for holding either, and refuses locally what the server answers 400 to (more than 200 or repeated or malformed references, a family named twice or both known and unknown) and a known `supersedes` beside a `superseded` ADR whose replacement is unknown, which no readback could match. `_ORIGIN_TEXT` refuses an unpaired surrogate in a `source_ref` or `foreign_id`, as the server does since DEVHUB-122. The cell `cross-tracker-adr-import` of the DevHub column of `tracker-contract.v1.json` is `supported` (all 19 core cells), and `_ADMISSION_PENDING_PROVIDERS` of `tests/test_tracker_conformance_v1.py` is removed. Limits: `unknown_relations` is an import-time declaration that DevHub does not keep consistent with later writes, and a server older than DEVHUB-122 answers 400 to a request carrying either field (not probed at preflight). The live suite gains DHN-46; green twice on a disposable instance built from `3f0cf4b` (`docs/devhub-requirements.md`, "Live run of PAT-327"). Documentation status (R5): `docs/devhub-tracker.md` ("Source-only references and unknown relations", "Selective cutover target", "Selective import"), `docs/devhub-requirements.md` (DHN-46, question 19, counts 46 needs, 0 red, 0 unknown, 46 green), `docs/tracker-contract.md`, `docs/tracker-contract.v1.json`, `docs/tracker-conformance.md`, `README.md` and the two plugin manifests updated.
- PAT-326: `DevHubTracker.set_adr_status` (`trackers/devhub.py`) refuses `superseded` with a `ValueError` before any request, naming `supersede_adr` (`adr supersede`), because DevHub `12ca158` (DEVHUB-116) answers the status route 409 `invalid_transition` to it; live need DHN-31 is redefined (the status route refuses `superseded`, the ADR stays `accepted`; test `test_dhn_31_status_route_refuses_superseded_and_leaves_the_adr_accepted`). Documentation status (R5): `docs/devhub-tracker.md` ("Governance mode": 403 `authority_boundary` on `POST /projects/:key/adrs/import` and the Epic closure, governance checked before the 409; "Selective import": origin number bound 999999; "Proof-bound ADR supersession") and `docs/devhub-requirements.md` (row and question 6 of DHN-31, history row; counts unchanged, 42 green, observed in the new "Live run of PAT-326" at DevHub `12ca158`; row DHN-32: the live test no longer creates an unlinked supersession) updated; `tracker-contract.v1.json`: the DevHub evidence of `set_adr_status` no longer claims `superseded`. No CLI verb or configuration key changed.
- PAT-325: review remarks deferred from the DevHub qualification (Epic PAT-295, tickets PAT-323 to PAT-330) are corrected. Behaviour changes: `setup_project.setup` relays the message of a `TrackerConflictError` raised by `verify_project_identity` only and names a generic cause for any other error, as `registry.verify_existing_project` does; `DevHubTracker.migration_attribute_exceptions` records an exception for an absent `state` (the server would create the issue in `backlog`, which no readback equals), while an absent `type`, `priority` or `estimate` stays unexcepted because the server stores `null` for an omitted field (deduced from DevHub source at `3f0cf4b`); `_read_workflow` names the real route `/api/tracker/workflow/v1/projects/<KEY>` in its `invalid_response`; `_release_issue` reads a `human_acceptance` receipt next to PR-bound `review` and `done` receipts as `unavailable`; `observe_epic_closure_child` returns a child that carries a `pr_url` unchanged, without reading a decision; `write.issue_acceptance_state` states its source guard clearly (no behaviour change). `.claude-plugin/plugin.json` no longer calls `devhub` "outside V1 qualification" in the `FOUNDRY_TRACKER` description. Tests: setup refusal, absent attributes, workflow route name, one enabled and one disabled status of a creation state (refused, as the server counts both), Epic closure end to end over a child accepted by decision, `cutover._target` construction, an isolation guard with `os.sep`. Live tests `DHN-43` (a third issue with no type, priority or estimate) and `DHN-46` (a `superseded` ADR with an `unknown` replacement) are extended and passed live twice on a disposable instance at DevHub `3f0cf4b`. Documentation status (R5): `docs/devhub-tracker.md` (governance-mode citations, "Selective import", "Source-only references and unknown relations", "Selective cutover target", "Acceptance override and human acceptance") and `docs/devhub-requirements.md` (rows DHN-43, DHN-44, DHN-46, DHN-47, PAT-326 history row, table layout, "Live run of PAT-325") updated; `docs/tracker-contract.md` and `docs/tracker-contract.v1.json` need no change (no described cell behaviour changed); `AGENTS.md`/`CLAUDE.md` not touched. The fragment `changelog.d/PAT-323.md` now cites the R9 wording only (it is unreleased).
- PAT-324: DevHub is a qualified target of the `preflight` and `copy` steps of a selective cutover (FOUNDRY-ADR-0026); `activate` toward DevHub is not qualified (`docs/devhub-tracker.md`, "Selective cutover target"). `DevHubTracker` now implements `migration_preflight` (no write; local refusal of what the server would refuse after the first write: invalid or duplicate source coordinates, unrepresentable titles and bodies, a living graph with a non-`Epic` parent, several parents or a cycle, an ADR corpus with a status DevHub lacks (`rejected`), source-only issue references, `unknown` relations or non-reciprocal supersession; then `verify_project_identity`, which also refuses a project outside Foundry governance; deterministic profile `devhub-source-ref-v1`), declares `migration_supported_attributes` (the seven attributes of the cutover manifest), `migration_attribute_exceptions` (`priority` outside `P0`-`P3`, `estimate` outside 1-21, `type` empty or over 80 UTF-16 units, and a `state` other than `backlog`/`ready`, recorded as explicit exceptions instead of approximated) and `migration_prepare_adr` (canonical relation order). `migration_export_adrs` now reads the issue relations of each ADR (`GET /adrs/:id/relationships`, one more read per ADR) instead of reporting them `unknown`, and sorts `supersedes` and `issues`; `migration.verify_targets` now calls `migration_export_adrs` once per pass instead of once per ADR. The cell `cross-tracker-live-work-copy` of the DevHub column of `tracker-contract.v1.json` is `supported` (18 of 19 core cells); `cross-tracker-adr-import` stays a `gap` owned by PAT-327 (DevHub at `12ca158` has no carrier for source-only issue references nor for an `unknown` relation, so the preflight refuses such a corpus), `_ADMISSION_PENDING_PROVIDERS` of `tests/test_tracker_conformance_v1.py` is kept, switching this repository to DevHub would be refused by that preflight (deduced, not run: `docs/devhub-tracker.md`, "Selective cutover target"), and the live suite gains DHN-43 (a manifest captured, copied and read back on a disposable instance, green in the live run of PAT-324 at DevHub `12ca158`). Not probed, unknown: whether the server carries DEVHUB-105 and which `type`/`priority`/`estimate` values a published workflow disables (refused only at copy time, after partial writes, below the other providers, which name such values as exceptions in the manifest); `devhub-requirements.md` question 19 asks DevHub for a catalogue read and a carrier for source-only references and unknown relations. Documentation status (R5): `docs/devhub-tracker.md` ("Selective cutover target", "Selective import", "Proof-bound ADR supersession"), `docs/devhub-requirements.md` (DHN-43, DHN-34 limits, question 19, counts 43 needs, 0 red, 0 unknown, 43 green), `docs/tracker-contract.md`, `docs/tracker-contract.v1.json`, `docs/tracker-conformance.md` and `README.md` updated.
- PAT-323: DevHub is admitted as the fourth V1 provider of the registry and of the tracker contract (PAT-ADR-0031): `devhub` joins `registry._V1_TRACKERS`, so a DevHub binding is an ordinary canonical repository marker and `registry bootstrap devhub <repo> <KEY> <project-id>` publishes entry and marker only after `verify_project_identity` (exact identity, governance mode `foundry`), refusing otherwise naming the cause; a DevHub binding accepts no extra beyond `canonical_repo` and `release_ids`; `setup_project` now publishes the marker with the entry, after the same `verify_project_identity` read-back; a DevHub binding keeps its `release_ids` through the server read-back (`_read_back_registered`). The DevHub pilot path is retired: `mode=pilot`, the "Binding tracker V1 requis : le dépôt utilise le pilote DevHub." refusal and the basename resolution of `DevHubTracker.resolve_checkout_project` are removed (it now resolves through the shared marker selection, then re-reads the project on the server); the host-selected `FOUNDRY_TRACKER=devhub` name stays for an unbound checkout and outside Git as a diagnostic and setup selector only. Operator action: an existing DevHub registry entry that has `canonical_repo` but no marker no longer resolves as a pilot and fails closed with "Binding tracker V1 sans marqueur" from its checkout (an entry without `canonical_repo` is legacy; an entry used outside a matching checkout is not affected); republish it with `registry bootstrap devhub` and the entry's own key, id and `canonical_repo=<host/owner/repo>`, which only writes the missing marker. `tracker-contract.v1.json` (version 5) and `tracker-contract.md` set `devhub` to `v1_core: true` with one cell per operation: 17 of its 19 core cells `supported`, `cross-tracker-adr-import` and `cross-tracker-live-work-copy` `gap` (no `migration_preflight`, owner ticket PAT-324), so DevHub is not repository-ready and no repository is switched. Documentation status (R5): `docs/tracker-contract.md`, `docs/tracker-contract.v1.json`, `docs/devhub-tracker.md` ("Project binding and cutover" and three sentences), `docs/devhub-requirements.md` (DHN-40 green in the live run of PAT-323 at DevHub `8028000`, with `registry bootstrap devhub` run against the same disposable instance; counts 0 red, 0 unknown, 42 green), `docs/linear-tracker.md`, `docs/review-rounds.md`, `skills/merge-pr/SKILL.md`, `AGENTS.md`/`CLAUDE.md` (R9 wording "provider columns") and `README.md` updated; the offline conformance gate exempts DevHub's ticketed blockers only (`_ADMISSION_PENDING_PROVIDERS` in `tests/test_tracker_conformance_v1.py`).
- PAT-321: the DevHub adapter (`trackers/devhub.py`) refuses a project whose governance mode is not `foundry` (`manual`): `verify_project_identity` reads `GET /governance/projects/<KEY>` (`devhub-governance.v1`, read strictly, a 404 `route_unavailable` keeps the previous behaviour as unknown mode) once the identity holds and raises the new `DevHubGovernanceModeError` (a `TrackerConflictError`) naming the mode; a 403 `authority_boundary` on a proof-bound write route (state, acceptance override, human acceptance, ADR status, supersession, ADR import, Epic closure) re-reads the mode once and raises the same error when it is not `foundry`, else re-raises the original error; `registry.verify_existing_project` carries the message of a `TrackerConflictError` instead of the generic one. Documentation status (R5): `docs/devhub-tracker.md` (new section "Governance mode", new row in the contract table, sentence in "Project identity read-back") and `docs/devhub-requirements.md` (history row; row DHN-32: the supersede route checks the mode since DEVHUB-110, DevHub merge `5782d78`) updated; observed on a disposable instance built from DevHub `8028000` (identity refused and a state write refused, both naming `manual`); `tracker-contract.md` and `tracker-contract.v1.json` not changed (DevHub is outside the three V1 provider columns); the route is not yet in DevHub's `TRACKER_API.md` (DEVHUB-116 unmerged), stated in the page.
- PAT-320: the DevHub adapter (`trackers/devhub.py`) implements `supersede_adr` on the route of DEVHUB-108 (DevHub merge `6721ad8`): it refuses a source or a replacement that is not `accepted` before any write, posts `POST /adrs/:id/supersede` with a proof of action `adr-supersede` bound to both server ids and versions (`replacement_id`, `replacement_version`), maps a version error to `TrackerConflictError` and reads the link back from `GET /adr-decisions/adrs/:id`; `migration_export_adrs` returns `superseded_by` and `supersedes` read back instead of `unknown` (a `superseded` ADR with no recorded link stays `unknown`); a source already superseded by that same replacement returns without a write, whatever the replacement's status is by then. The live test of DHN-32 moves from `@gap` to `@need`; it passed against an instance built from `6721ad8` (status `green`). Documentation status (R5): `docs/devhub-requirements.md` (row DHN-32, counts 1 red, 0 unknown, 41 green, question 15 follow-up, item 18, history row, new "Live run of PAT-320") and `docs/devhub-tracker.md` (new section "Proof-bound ADR supersession", sentence "not implemented" replaced) updated; `docs/tracker-contract.md` and `docs/tracker-contract.v1.json` not changed (DevHub is outside the three V1 provider columns).
- PAT-319: the DevHub adapter (`trackers/devhub.py`) uses the routes of DEVHUB-105 (DevHub merge `b0231d9`). `migration_find_issue` (`GET /projects/:key/issues?source_ref=`) and `migration_import_issue` (`POST /projects/:key/issues` with `source_ref`) no longer raise `TrackerCapabilityUnavailableError` (DHN-34): a faithful replay converges on the existing issue, a divergent content or a taken coordinate is a `TrackerConflictError`. `import_adr`, `migration_import_adr`, `migration_import_adrs` and `migration_find_adr` use `POST /projects/:key/adrs/import` and the `source_ref` / `foreign_id` lookups (DHN-36): the foreign id, status and exact body are kept (`body_sha256` checked locally and by the server), the foreign id is the port `Adr.id`, and replacements are imported first. The live tests of DHN-34 and DHN-36 move from `@gap` to `@need`; both passed against an instance built from `b0231d9` (status `green`). Documentation status (R5): `docs/devhub-requirements.md` (rows DHN-34 and DHN-36, counts 2 red, 0 unknown, 40 green, history row) and `docs/devhub-tracker.md` (new section "Selective import", "Staged migration links" sentence corrected) updated; `docs/tracker-contract.md` and `docs/tracker-contract.v1.json` not changed (DevHub is outside the three V1 provider columns). `set_adr_status`, `link_adr_issue` and `read_adr_issue_relations` resolve the server id from the project listing by port id (one listing more per call), and `import_adr` refuses a non-empty `supersedes`.
- PAT-318: the DevHub adapter (`trackers/devhub.py`) reads the extended proof-receipt form of DEVHUB-106 (DevHub merge `f347f38`): 16 keys, the actions `review`, `done`, `acceptance_override` and `human_acceptance`, with per-action null rules, and still fails closed on any other shape. `project_acceptance_override` (DHN-25; `acceptance_override_projection_supported` is now `True`, so `issue merge --allow-incomplete-ac` on DevHub writes the typed receipt before the merge and is refused before any merge if the server lacks the route) and `accept_issue` (DHN-26, port operation only; `human_acceptance_supported` stays `False`, `issue accept` stays Linear-only) use the new routes with a bound proof and a read-back that recomputes the decision digest. The live tests of DHN-25 and DHN-26 move from `@gap` to `@need` and passed against an instance at `f347f38` (status `green`); `docs/devhub-requirements.md` records N to 1 of DEVHUB-108 as confirmed. Documentation status (R5): `docs/devhub-requirements.md`, `docs/devhub-tracker.md`, `docs/tracker-contract.md` and `skills/merge-pr/SKILL.md` updated; `docs/tracker-contract.v1.json` not changed (DevHub is outside its three V1 provider columns). Counts: 4 red, 0 unknown, 38 green.
- PAT-317: no change to any tool behaviour. `docs/devhub-requirements.md` records the DevHub reply of 2026-10-20 (work order DEVHUB-106, DEVHUB-105, DEVHUB-108; DHN-32 answered by option a, a proof-bound service route as DEVHUB-108, the replacement already accepted and left unchanged, N to 1 requested and its acceptance unknown) in the rows DHN-25, DHN-26, DHN-32, DHN-34, DHN-36, in question 15 and in a new item 18, and `docs/devhub-tracker.md` points to it. The live test of DHN-32 accepts both ADRs before `supersede_adr`, checks the replacement keeps its status and body and reads `superseded_by` / `supersedes` back through `migration_export_adrs`; it stays red until DEVHUB-108 merges. Documentation status (R5): `docs/devhub-requirements.md` and `docs/devhub-tracker.md` updated; no CLI verb, configuration key or contract cell changed. Counts unchanged (6 red, 36 green).
- PAT-316: on DevHub, closing an Epic failed before the server call (`invalid_response`) as soon as a child had acceptance criteria, because `write.close_epic` put each child's AC counters in the atomic receipt and `DevHubTracker.close_epic` rebuilds the wire receipt without them. The atomic receipt now binds each child by id, version and state only, the coordinates the server checks; the AC are still checked on Foundry's read before the call, and a dropped child named under PAT-ADR-0030 still owes none. The live DHN-24 test now gives its children criteria. Documentation status (R5): `docs/devhub-tracker.md` ("Epic closure over a dropped child") and `docs/devhub-requirements.md` (row DHN-24, "Live run of PAT-316", status table) updated; no CLI verb, configuration key or public constant changed.
- PAT-315: review remarks deferred from PAT-309 to PAT-320 are corrected. DevHub adapter (`trackers/devhub.py`): `read_release_scope` re-reads the Version and fails `coordinates_changed` when it moved, and orders issues numerically; `link_adr_issue` checks membership before status and re-reads after a 403; `read_adr_issue_relations` reads its envelope strictly; `migration_link_issue` makes two reads and one write; `import_adr` refuses before the write a foreign id equal to the server id of a native ADR of the project (the server id of another imported ADR stays accepted), and a 409 on a taken number of DEVHUB-111 (DevHub merge `16b6888`) is a `TrackerConflictError`; `migration_import_adrs` validates the whole batch before its first write and leaves out `supersedes` only when every predecessor names the ADR in `superseded_by`; `migration_export_adrs` reads one listing and reports `supersedes` `unknown` while the project holds an unlinked superseded ADR; `supersede_adr` checks a replay first. New public constant `models.AC_OVERRIDE_REASON`, the reason-code pattern shared by `issue merge --ac-override-reason` and the DevHub adapter (pattern unchanged). The live suite and one-off scripts passed on disposable instances built from `6721ad8` and `16b6888`. Documentation status (R5): `docs/devhub-requirements.md` (rows DHN-24, DHN-25, DHN-32, DHN-33, DHN-36, questions 16 and 18, new "Live run of PAT-315", history row), `docs/devhub-tracker.md`, `docs/linear-tracker.md` (quota checked before the usage-limit code), `skills/close-epic/SKILL.md` and the fragments of PAT-310, PAT-311 and PAT-320 updated; `models.AC_OVERRIDE_REASON` is described by its comment in `models.py`; no CLI verb or configuration key changed; `docs/tracker-contract.md` not changed (DevHub is outside the three V1 provider columns).
- PAT-314: the DevHub adapter gains `verify_project_identity` (reads `GET /projects/resolve` and compares key, id and canonical repository; `False` on a missing repository or a 404) and `migration_link_issue` (refuses an archived project, posts the link only when the source does not already carry it in either stored direction, so a replay or the inverse end makes no second write). DHN-35 and DHN-41 move from `@gap` to `@need` and are `green` after the live run of PAT-314 (6 red, 0 unknown, 36 green); by decision, the DHN-35 live test links issues created normally, the import itself staying DHN-34. `registry bootstrap` still refuses DevHub before the identity read (DHN-40). Documentation status (R5): `docs/devhub-requirements.md` (rows, counts, "Live run of PAT-314", status table) and `docs/devhub-tracker.md` (two new sections) updated; no CLI verb or configuration key changed.
- PAT-313: the DevHub adapter overrides `reconcile_issue_creation` (one read of the issue, no write, no replay; compares project binding, identity, title, byte-exact body, parent and requested fields; a difference raises `TrackerConflictError`, an absent issue `IssueUnavailableError`), so `campaign_cadence.missing_capabilities` reports nothing for DevHub. The reference must be an issue id: DevHub has no read by `Idempotency-Key`, and its identical replay needs the original key and payload, which the adapter does not keep (answer 16). DHN-37 moves from `@gap` to `@need` and is green in the live run on DevHub `1227841`, the new reference. That run also recorded a receipt ordering defect at `809ca94`, fixed by DevHub in `1227841` (question 17). DHN-24 is green under PAT-ADR-0030 (proposed with this PR), which extends the nominative closure of PAT-ADR-0017 to DevHub: the adapter declares `epic_dropped_closure_supported`, and `write.close_epic` refuses a DevHub closure over a dropped direct child until `--human-verdict=accepted --accept-dropped` names exactly the dropped direct children (refusal before the atomic server call, with the exact command). This is stricter than before: a DevHub closure that passed over a dropped child without the flag is now refused. A named dropped child owes no acceptance proof. Counts after the live run on DevHub `1227841`: 8 red, 0 unknown, 34 green. Documentation status (R5): `docs/devhub-requirements.md` (reference, rows, counts, answers 9, 15, 16 and 17, "Live run of PAT-313", status table), `docs/devhub-tracker.md` (two new sections), `docs/campaign-cadence.md`, `skills/close-epic/SKILL.md`, `docs/linear-tracker.md`, `docs/tracker-contract.md`, `docs/tracker-contract.v1.json` and `README.md` updated; no CLI verb or configuration key changed.
- PAT-312: the DevHub adapter gains `update_body` (issue body replaced through the existing body `PATCH`, refused when the current body differs from the expected one), `link_adr_issue` (sets the ADR-to-issue relation on a proposed ADR through the ADR edit route; on a decided ADR it raises the typed `AdrRelationFrozenError`, the relation must be set before acceptance) and `read_adr_issue_relations`. `supersede_adr` stays unavailable: at `2edcea2` the only route naming a replacement is limited to an interactive human administrator, so DHN-32 is requalified to `adapter + server` with a new question for the DevHub agent. DHN-21 and DHN-33 move from `@gap` to `@need` and are `green` after the live run of PAT-312 (10 red, 0 unknown, 32 green); the replay line of the DHN-21 test contradicted the `update_body` contract and was corrected, and `update_body` now re-reads the issue after its write. Documentation status (R5): `docs/devhub-requirements.md` (rows, counts, question 15, status table) and `docs/devhub-tracker.md` (new section) updated; no CLI verb or configuration key changed.
- PAT-311: `docs/devhub-requirements.md` integrates the DevHub human decisions of 2026-10-20. DHN-33 is requalified (DevHub keeps the decided-ADR freeze and plans no server work; Foundry sets ADR-to-issue relations before acceptance) and its live test is renamed `test_dhn_33_adr_issue_relation_set_before_acceptance_survives_it`, owner `adapter`; DHN-25 and DHN-26 now point to DEVHUB-106 and DHN-34 and DHN-36 to DEVHUB-105 (owner `adapter + server`, still red, backlog not started); answers 7, 8 and 12 and the paragraph "Decided on the DevHub side on 2026-10-20" are completed. Documentation status (R5): the page and its status table updated; no adapter code, CLI verb or configuration key changed; counts unchanged.
- PAT-310: the Linear adapter now recognises the GraphQL (or HTTP error body) code `USAGE_LIMIT_EXCEEDED`, observed 2026-10-20 once the free-plan issue limit was reached, as `LinearUsageLimitError` (code `usage_limit_exceeded`) instead of `graphql_error`: a durable refusal with a known effect (`creation=not_created`, no read-back, no retry) whose message names the workspace usage limit and copies nothing from the response; `campaign_defer.py::_DURABLE` classes it durable so the remarks are corrected before the merge. Documentation status (R5): `docs/linear-tracker.md` and `docs/campaign-effects.md` ("Classification of a failure") updated; no CLI verb or configuration key changed.
- PAT-309: the DevHub adapter reads `GET /issues/:id/proof-receipts` (`read_proof_receipts`) and sets `append_only_lifecycle_supported` (DHN-38); `set_state` republishes a new PR generation of an issue already in `review` through `in-progress`, because DevHub answers `review -> review` with no new receipt, and `recover_done_projection` confirms the exact done receipt chain without writing. `read_release_scope` reads `GET /versions/:id` for the Version id `<KEY>-VERSION-N` stored in `project.extra["release_ids"]` (DHN-39), refusing a name mismatch and a scope that lists Epic descendants it does not publish. DHN-38 and DHN-39 move from `@gap` to `@need` and are green in a live run on a disposable instance at DevHub `2edcea2` (29 passed, 12 xfailed, 1 skipped). Documentation status (R5): `devhub-tracker.md` (two new sections), `devhub-requirements.md` (rows, counts, answers 10 and 11, status table) and `campaign-cadence.md` updated; `tracker-contract.md` and `tracker-contract.v1.json` not changed (DevHub is outside the three V1 provider columns, no CLI verb or configuration key changed); not provided: a start receipt and a typed acceptance-override receipt (DEVHUB-106).
- PAT-303: no change to any tool behaviour. The live DevHub suite turns DHN-27, DHN-28 and DHN-42 from `@gap` into `@need` (the DevHub server now covers them, per the DevHub agent, DEVHUB-103 and DEVHUB-102); `docs/devhub-requirements.md` takes `2edcea2` as the current DevHub reference, records the answers to questions 1 to 14, marks DHN-33, DHN-34/36 and question 12 as awaiting a human decision on the DevHub side, and keeps DHN-38 and DHN-39 as adapter-only gaps. The three statuses are `green` after the live run of PAT-303 at `2edcea2` (27 passed, 14 xfailed, 1 skipped without local accounts; DHN-42 passed with local accounts), and the owner of DHN-38 and DHN-39 becomes `adapter`. Documentation status (R5): `docs/devhub-requirements.md` updated (status table row added); no CLI verb, configuration key or contract cell changed.
- PAT-301: no change to any tool behaviour. Handles the seven non-blocking remarks deferred from PAT-300 (six corrected, one kept as is), in documentation and test docstrings only. The docstring of `tests/devhub_live_gate.py` now says that "without local accounts" applies to the 41 tests of the `suite` fixture and that the DHN-42 target must be disposable by construction; the docstring of `tests/integration/test_devhub_live_conformance.py` says that the refusal of an instance holding a project the suite did not create applies to every test but proves nothing on an instance with local accounts, the target of DHN-42 alone (`DEVHUB_LIVE_LOCAL_ACCOUNTS=1`). In `docs/devhub-requirements.md`, question 1 no longer says Foundry built the image and cites the `revision` of DevHub `/api/health` (read at `ff22b3a`); the procedure "Bring up a disposable instance" states its prerequisites (the image comes from DevHub `deploy/qa/up.sh`, whose side effects are listed), points to the `pytest` line only and tells not to export the everyday credentials, and the compose file gains the resource limits, `cap_drop` and `no-new-privileges` of the QA compose file, which were not run; the "First live run" paragraph is rewrapped. The wording "has not been run" of DHN-25 is kept: it is accurate. Documentation status (R5): `docs/devhub-requirements.md` updated (status table row added); no CLI verb, configuration key or contract cell changed.
- PAT-300: no change to any tool behaviour. Records the first live run of the DevHub conformance suite (2026-10-20, DevHub revision `ff22b3a821fb031b49d1fdb78c43ecb481431c7f`, instance without local accounts, 25 passed and 16 xfailed): the 25 needs that passed are `green` in `docs/devhub-requirements.md` (42 needs; 17 red; 0 unknown; 25 green). New red need DHN-42 (owner `server`, question 14): under local accounts the Foundry service credential cannot create and use its project (observed `POST /projects` 403 `insufficient_scope`); its test `test_dhn_42_*` skips unless `DEVHUB_LIVE_LOCAL_ACCOUNTS=1` declares such a target (`devhub_live_gate.local_accounts_declared`, offline-tested). The page and the docstring of `tests/devhub_live_gate.py` state that the `LV` marker check proves nothing when the credential only sees granted projects, and the page documents the disposable instance without local accounts that was run once. Documentation status (R5): `docs/devhub-requirements.md` updated (statuses, counts, environment variable, procedure, question 14); no CLI verb, configuration key or contract cell changed.
- PAT-299: no change to any tool behaviour. Corrects the remarks deferred from PAT-296 on `docs/devhub-requirements.md` and `tests/integration/test_devhub_live_conformance.py`: every `@gap` now names the exact exception of its gap in `raises=` (a gate refusal is an error and any other exception a failure or an error, never an `xfailed`, except that the `DevHubTrackerError` of DHN-27 and DHN-28 is also raised by a transport error, an invalid response or a refused issue creation or start, stated on the page; checked offline by `tests/test_devhub_requirements.py`), the "Run the suite" sentences about the CI and local commands cite their source and the case of exported `DEVHUB_LIVE_*` variables, DHN-40 names the adapter side of the V1 promotion, the new need DHN-41 covers `repository-bootstrap` (`verify_project_identity`; 41 needs, 16 red), and the server citations give their path in the DevHub repository (read at `68c7ef0`). Documentation status (R5): `docs/devhub-requirements.md` updated; no CLI verb, configuration key or contract cell changed.
- PAT-297: the `Nits` companion Epic created by the cadencer now has a body that fits the tickets with no origin Epic; `campaign_cadence.observe` refuses a `deferral` given without `origin` (`ValueError`) instead of reading the ticket as having no origin Epic; documented the wider orphan-scan residual risk under `Nits` and the case of several `subtask-of` parents in `docs/campaign-effects.md` and `AGENTS.md#R9`; added tests, rewrapped lines. No CLI verb, option or public constant changed.
- PAT-296: no change to any tool behaviour. Adds `docs/devhub-requirements.md`, the page of Foundry's 40 needs towards DevHub (test, status green/red/unknown, owner of each gap, questions for the DevHub side), and a live conformance suite `tests/integration/test_devhub_live_conformance.py` that runs the `trackers/devhub.py` adapter against a disposable DevHub instance (`pytest -m devhub_live`, the new marker of `pytest.ini`). It is skipped without `DEVHUB_LIVE_URL`, refuses a production-looking target (same origin or credentials as the everyday `DEVHUB_*` variables, an instance holding a project whose key does not start with `LV`) and reads no configuration or registry; unmet needs are strict `xfail` tests. Documentation status (R5): `docs/devhub-requirements.md` (new), one pointer line in `docs/devhub-tracker.md` and `docs/tracker-conformance.md`; no CLI verb, configuration key or contract cell changed.
- PAT-294: the Epic campaign cadencer now tries the proof carry when the base of the PR did not change and the reviewed head did (the PAT-288 case), instead of running a full review; `review_mode` (`tooling/foundry/campaign_review.py`) no longer requires the base to differ, the same `routing carry-review-proof` decides the refusals (including "rien à reporter" when the reviewed head already contained the base) and a refusal still falls back to a full review. Documentation status (R5): `docs/campaign-effects.md` ("Partial review and proof carry") updated; no CLI verb, key or public constant changed.
- PAT-291: behaviour change, the campaign cadencer now decides `defer-remarks` for a ticket with no origin Epic (the direct parent is absent or not an `Epic`) after a validated review without a "fix before merge" remark, towards the companion Epic named exactly `Nits` (PAT-ADR-0029), with the same four writes, prior reads and refusals as for an Epic; its follow-up issue is titled `Review remarks deferred from tickets with no origin Epic`. The `correct` decision with cause `no_origin_epic` no longer exists; a ticket of `Nits` or `Nits <ID>` is still corrected. Documented in `AGENTS.md#R9`, `docs/campaign-effects.md`, `docs/campaign-cadence.md` and `docs/review-rounds.md`.
- PAT-290: Tests only, no behaviour change: `routing carry-review-proof` is now tested for an unchanged PR base that changes a file of the PR (refused by the diff-hash identity check), for a replay of such a carry (`carried: false`) and for a head that did not integrate that base (refused); the routing comment names the guard (deferred review remarks of PAT-288).
- PAT-288: `routing carry-review-proof` no longer refuses with "rien à reporter" when `--base` equals `--from-base` and the reviewed head did not contain that base: if the new head is the clean merge of the reviewed head with it, the proof is carried under the same PAT-ADR-0021 checks (observed on PAT-272, PR #217); an unchanged base already contained in the reviewed head is still refused (`AcceptanceProofStore.carry`, `tooling/foundry/routing.py`). Documentation status (R5): `docs/review-rounds.md` condition 3 updated; the `merge-pr` and `review-pr` skills do not state that the base must change, so unchanged; no CLI verb, key or public constant changed.
- PAT-286: review remarks deferred from PAT-269 corrected. Behaviour changes: `-h`/`--help` anywhere in the arguments of `adr`, `issue`, `frame`, `setup_project`, `cutover`, `registry` and `query` now prints the usage and writes nothing (`adr create --help` used to create an ADR titled `--help`; `issue start -h` took `-h` as an issue id); the per-command help of `edit` keeps the continuation lines of its usage entry and the usage of an unknown `edit` command goes to standard error (exit code 2); `issue wait-ci` answers at once `verdict=not_green` (exit 2) when every check is complete and none succeeded, refuses `nan`/`inf` and any repeated `--timeout`/`--interval`; `--ac-override-reason` is accepted by `issue merge` only; `issue openpr <ISSUE-ID>` refuses a current branch that is not that issue's branch; `claimed_review_diff` refuses `since` with a snapshot consumer; the manifest of a partial review packet adds `coordinator_declared_fields` for `previous_head`; a Linear ADR body whose `AGENTS.md#R<n>` has a tab before it is refused like any other unmodelled context, and the refusal of a `*.md` name quotes the whole token as written. Documentation status (R5): `docs/ci-wait.md`, `docs/review-packet.md`, `docs/linear-tracker.md`, `docs/linear-adr-body-guide.md`, the `edit.py` CLI docstring, the `merge-pr`, `review-pr`, `release` and `open-pr` skills, `docs/campaign-effects.md`, `agents/maigret.md`, AGENTS.md/CLAUDE.md R9(b) and the 2.2.0 paragraphs of both READMEs updated; the frozen 2.2.0 release notes, migration guide and CHANGELOG section are untouched.

## 2.2.0 — 2026-10-19

Minor release. New read-only verb `issue wait-ci` (exit codes 0 green, 2 red, 3 timeout, 1 on a refusal or an error; `docs/ci-wait.md`, PAT-274); new options `routing read-review --since` and `routing prepare-review --previous-head` (PAT-283); new exit code 3 of `issue merge` (`EXIT_LOCAL_CLEANUP_FAILED`, PAT-272); `issue openpr` pushes an issue branch with `--force-with-lease` (PAT-273); `edit <command> --help` prints the usage (PAT-275); a new Linear ADR body holding another `*.md` name outside code is refused before any write (PAT-284); a ticket with no origin Epic defers its non-blocking remarks to the per-project companion Epic `Nits` (PAT-ADR-0029, PAT-283; the campaign cadencer still corrects such a ticket); `GitHubCodeHost.ci_expected` accepts every documented check-suite status and `ci.json` carries `observed_at` (PAT-270). No routing default, model or effort changes. This dated section is not a claim that 2.2.0 is already tagged or installed, and it claims no gain.

- PAT-284: the Linear ADR body model renders the bare `AGENTS.md#R<n>` reference as Linear stores it (`[AGENTS.md#R<n>](<http://AGENTS.md#R<n>>)`, observed on the orphan PAT-ADR-0029 Document), so replaying `adr create` with the exact title and body completes that Document; a new ADR body holding any other `*.md` name outside code (`CLAUDE.md#R<n>`, `AGENTS.md#R9(b)`, a bare `x.md`) is refused before any write with a message naming it. Documentation status (R5): `docs/linear-adr-body-guide.md` and `docs/linear-tracker.md` updated; no CLI verb, key or public constant changed.
- PAT-283: a ticket with no origin Epic now defers its non-blocking remarks, after a validated round, to the per-project companion Epic named exactly `Nits` (PAT-ADR-0029, which amends the maintainer decision of 2026-10-10 and extends PAT-ADR-0018: same rules, same four standing writes, no second-level deferral for any companion Epic); `AGENTS.md#R9`, `CLAUDE.md#R9`, the `merge-pr`, `review-pr` and `intake` skills and `docs/review-rounds.md` are updated, and `campaign_defer._COMPANION_NAME` also recognizes the exact title `Nits` (the campaign cadencer does not yet defer a ticket with no origin Epic into `Nits`: it still corrects it, `docs/campaign-effects.md`). New options `routing read-review --since <SHA>` (emits `git diff <SHA>..HEAD` under the claim checks, refuses a SHA that is not a full 40-hex ancestor of HEAD) and `routing prepare-review --previous-head <SHA>` (the packet says the review is partial and gives the `--since <SHA>` argument to add to the `routing read-review` command of the reviewer's role contract), allowed by the read-only reviewer guard; the commit range of a partial review is now read from the bytes. Documentation status (R5): `docs/review-packet.md`, `docs/review-rounds.md`, `docs/campaign-effects.md`, `agents/maigret.md` updated; public constant `_COMPANION_NAME` widened.
- PAT-275: `edit <command> --help` (for example `edit create-issue --help`) now prints the usage and exits 0 instead of failing on a JSON parse error, and an unknown `edit` command exits 2 with a message instead of a traceback (`tooling/foundry/edit.py`, `tests/test_edit_help.py`).
- PAT-274: new read-only verb `issue wait-ci <PR#> [--timeout=SECONDS] [--interval=SECONDS]` polls `write.ci_gate` (unchanged, `--allow-no-ci` never applied) on the PR head sha and prints the verdict, the checks in progress or failed and `observed_at`; exit `0` green, `2` red, `3` timeout; the skills `merge-pr`, `review-pr` and `release` cite it instead of `gh pr checks`. Documentation status (R5): `docs/ci-wait.md` added for the verb, its options and exit codes; no other CLI verb, key or public constant changed.
- PAT-273: `issue openpr` pushes the issue branch with `--force-with-lease=refs/heads/<branch>:<sha>`, where `<sha>` is what the clone last knew (`refs/remotes/origin/<branch>`, no fetch first) or empty when that ref is absent, so a rebased branch goes through while remote commits the clone never fetched (or a remote branch created elsewhere) are refused with a message and nothing is overwritten; the default branch is never pushed (`_push_branch`, `issue.py`; no bare `--force`; `hooks/guard_bash.py` unchanged). Documentation status (R5): `skills/open-pr/SKILL.md` and `docs/campaign-effects.md` updated; no CLI verb, key or public constant changed.
- PAT-272: `issue merge` no longer ends in the generic error exit when the squash-merge and the `done` transition are durable but the local `git checkout <default>` / `git pull --ff-only` fails: it prints the success line, then reports the cleanup failure on standard error with the command to run, and exits with the new code 3 (`EXIT_LOCAL_CLEANUP_FAILED`), distinct from a refused merge (exit 1). Documentation status (R5): `skills/merge-pr/SKILL.md` step 4 documents the exit code; no other CLI verb, option or key changed, merge gate (FOUNDRY-ADR-0002) unchanged.
- PAT-271: `foundry_cli.py routing` prints `Configuration de routage invalide : <cause>` (the message of the `RoutingConfigError` / `RoutingUnavailableError`, exit code 2 unchanged) instead of the generic text, and `prepare-review` prints `Préparation du paquet de review refusée : <cause>` for a `RoutingConfigError`; `read-review` and `prepare-review` now name a short or ref base, a base or head differing from the PR, a superseded or absent ADR, a CI observation impossible because of the provider, and the HEAD or diff changed since the claim, each with the expected action; no message carries `claim_id`, attempt token or diff bytes. Documentation status (R5): `docs/model-routing.md` and `docs/review-packet.md` updated; no CLI verb, option, key or public constant added.
- PAT-270: `GitHubCodeHost.ci_expected` accepts every documented GitHub check-suite status (`queued`, `in_progress`, `waiting`, `requested`, `pending`, `completed`) instead of crashing `prepare-review` right after a push; any non-terminal status other than a stale queued-empty suite is active CI and refuses `--allow-no-ci`, an unknown suite status is refused with an error that names it (a check run with an unknown status counts as pending in `write.ci_gate`, fail-closed, without naming the status), and `ci.json` carries `observed_at` (UTC). Documentation status (R5): `docs/review-packet.md` updated for `observed_at`; no other CLI verb, key or public constant changed.

## 2.1.1 — 2026-10-19

Patch release. One behaviour change, no new verb or option: below `MINIMUM_PYTHON`,
`tooling/foundry_cli.py` and the four hooks that import `foundry` re-run themselves under a
supported interpreter found in a closed list (`reexec_if_unsupported`,
`tooling/python_floor.py`) instead of stopping at once (PAT-267). No routing default,
model or effort changes. There is no release note and no migration guide for this patch.
This dated section is not a claim that 2.1.1 is already tagged or installed. The entry
below is the fragment present after `foundry-v2.1.0`, kept as written.

- PAT-267: below `MINIMUM_PYTHON`, `tooling/foundry_cli.py` and the `route_agent`, `guard_bash`, `observe_agent` and `session_start` hooks now look for a supported interpreter in a closed list and re-run the same script with it (`reexec_if_unsupported` in `tooling/python_floor.py`, `os.execv`, same arguments, stdin read by nobody before the exec). Order: `python3.N` for N from 20 down to 11, then `python3`, resolved on the current `PATH`; then `FIXED_PATHS` (`/opt/homebrew/bin/python3`, `/usr/local/bin/python3`); then `~/.local/bin/python3.N`. Each candidate is checked by a subprocess bounded by `PROBE_TIMEOUT_SECONDS` (stdin `/dev/null`); no other directory is scanned and no environment variable chooses the interpreter. `FOUNDRY_PYTHON_REEXEC` stops any loop and, with `FOUNDRY_PYTHON_REEXEC_FROM`, is removed from the environment of the re-executed process; any unexpected error in the search falls back the same way; with no candidate, or if `os.execv` fails, the 2.1.0 behaviour is unchanged (visible `systemMessage` and exit 0 for hooks, FOUNDRY-ADR-0005; non-zero exit for the CLI). `session_start` now records after the re-execution; `hook-python.json` gains the optional `reexec_from` (records without it stay readable) and `doctor`'s `Hook Python` line adds `re-executed from <path> Python <version>`. Observed cause: PAT-267 issue, 2026-10-19 (host PATH fallback gives Python 3.9.6; for Codex the mechanism is unknown). R5 documentation status: `README.md` ("Install or upgrade", Python), `docs/model-routing.md` (fail-open paragraph) and `skills/doctor/SKILL.md` updated; `hooks/hooks.json` unchanged; the constants and `reexec_if_unsupported` are documented in the README.

## 2.1.0 — 2026-10-19

Minor release. Behaviour changes without a new verb or option: Foundry declares Python
3.11 as its minimum (`MINIMUM_PYTHON`, `tooling/python_floor.py`), `tooling/foundry_cli.py`
exits non-zero under an older interpreter, the hooks that import `foundry` become inactive
with a visible `systemMessage` under it, internal hook failures stay fail-open but are no
longer silent, and `doctor` prints a `Hook Python` line (PAT-265); `mask_credentials` also
masks a JSON-quoted `Authorization` / `Proxy-Authorization` header (PAT-226). No routing
default, model or effort changes. Release note: `docs/release-2.1.0.md`; upgrade guide:
`docs/migration-2.1.0.md`. This dated section is not a claim that 2.1.0 is already tagged
or installed. The entries below are the fragments present after `foundry-v2.0.0`, kept as
written.

- PAT-265: Foundry declares Python 3.11 as its minimum (`MINIMUM_PYTHON`, `tooling/python_floor.py`, one declaration). `tooling/foundry_cli.py` exits non-zero with a message naming the interpreter, its version and the minimum, before importing any `foundry` module; the `route_agent`, `guard_bash`, `observe_agent` and `session_start` hooks print one `systemMessage` and stay fail-open under an older interpreter. Observed cause: on a host where `python3` was 3.9.6, `zip(..., strict=True)` raised `TypeError` in `route_agent.py`, whose `except Exception` swallowed it and the logical agent then self-stopped with a misleading message. Internal failures of `route_agent`, `guard_bash`, `guard_routed_readonly`, `observe_agent` and `session_start` are still fail-open (FOUNDRY-ADR-0005, PAT-42 fail-closed branch unchanged) but now print a `systemMessage` naming the exception type only; the previously silent paths are no longer silent. `doctor` adds a `Hook Python` line (path, version, source) and fails below the minimum; `session_start.py` writes `hook-python.json` in the data directory for it. `guard_bash.py` gains `from __future__ import annotations` (its `str | None` annotations did not import on 3.9). `.github/workflows/ci.yml` gains a ninth job, `foundry-python-min`, which runs the public pure-logic suite under Python 3.11 with the same plan and fork conditions as `foundry`; the job `foundry` (3.13) and its required name are unchanged. R5 documentation status: `README.md` ("Install or upgrade" and the CI jobs list), the root `README.md` CI line and `docs/model-routing.md` (fail-open paragraph) updated; `hooks/hooks.json` unchanged. Under Python 3.10 or older the four hooks that import `foundry`, including the `guard_bash` R1 deny and the PAT-215 `campaign-approval approve` deny, are now inactive with that visible message on every hook call; whether they worked under 3.10 before is unknown. `hook-python.json` also carries a `recorded_at` UTC time shown by `doctor`, whose `Hook Python` line now prints before the `Config` preflight; `route_agent` also reports an unreadable or non-object payload as a visible fail-open message. `skills/doctor/SKILL.md` lists the new line and its remedy.
- PAT-226: Settle the non-blocking remarks deferred from PAT-221 (PAT-222, PAT-223, PAT-224): `campaign_effects.mask_credentials` now also masks a JSON-quoted `Authorization` / `Proxy-Authorization` header (`"Authorization": "Bearer x"`; the previous header pattern is unchanged and still applied first; a second pattern, applied after it, masks the JSON-quoted form; unit test); `docs/campaign-approval.md`, `docs/campaign-effects.md` and `docs/campaign-cadence.md` state their sources, status labels and exit codes precisely. R5: `mask_credentials` documented in `docs/campaign-effects.md`; exit codes of `campaign-cadence next` / `step` / `run` documented in `docs/campaign-cadence.md` and `docs/campaign-effects.md`; no other public surface changed.

## 2.0.0 — 2026-10-18

Major release by version number only: 2.0.0 is a milestone chosen by the maintainer on
2026-10-18, and no entry below records a breaking change (release kind `major` per
`release_kind`, `tests/test_release_contract.py`). It packages what was merged after
`foundry-v1.5.0`. New surfaces: `python3 -m foundry.rollback_native_trial` with its
protocol and dated matrix, Claude and Codex lines (PAT-198, PAT-245, PAT-244), and the
on-demand skill replay `scripts/skill_evals.py` with `evals/` (PAT-184). Behaviour changes
without a new verb: `campaign-approval approve` caps the approval at the preview's
`policy.expires_at` and refuses more inputs, and the Claude Bash guard denies a wider set of
`campaign-approval approve` command shapes (PAT-234); the campaign next step, effects, review,
correction and deferral decide differently in the cases listed by PAT-235, PAT-236, PAT-237
and PAT-238; `issue accept --status` wording and the `issue accept` and `issue close-epic` unknown-flag messages change
(PAT-239, PAT-242), and `campaign_effects._save_summary` writes a lone surrogate as `?`
instead of raising `UnicodeEncodeError` (PAT-242). PAT-240, PAT-241, PAT-246, PAT-248, PAT-251, PAT-252 and PAT-254 correct
documentation, a code comment, tests or the CI jobs list. The same
catalogue also carries other plugins merged in this period, with their own versions and
changelogs (see the release note). Release note: `docs/release-2.0.0.md`; upgrade guide:
`docs/migration-2.0.0.md`. This dated section is not a claim that 2.0.0 is already tagged
or installed. The entries below are the fragments present after `foundry-v1.5.0`, kept as
written.

- PAT-254: Corrected the CI jobs list of `README.md`: `.github/workflows/ci.yml` now has an eighth job, `witness` (offline tests of the new `plugins/witness`), which runs like `gauge`, `relay` and `blueprint`, is not conditional on the `plan` job and carries the fork guard; the guard test `test_required_checks_keep_their_names_and_fork_guard` still covers four of the eight jobs. No Foundry code, constant or CLI changed.
- PAT-252: Corrected the CI jobs list of `README.md`: `.github/workflows/ci.yml` now has a seventh job, `blueprint` (offline tests of the new `plugins/blueprint`), which runs like `gauge` and `relay`, is not conditional on the `plan` job and carries the fork guard; the guard test `test_required_checks_keep_their_names_and_fork_guard` still covers four of the seven jobs. No Foundry code, constant or CLI changed.
- PAT-251: Corrected the CI jobs list of `plugins/foundry/README.md`: the guard test `test_required_checks_keep_their_names_and_fork_guard` (`scripts/test_ci_plan.py`) now also asserts the fork guard of `gauge`, so it covers five of the eight jobs (`plan`, `foundry`, `ship-ios`, `gauge`, `catalogue`); `relay`, `blueprint` and `witness` stay unasserted. No Foundry code, constant or CLI changed.
- PAT-248: Corrected the CI jobs list of `README.md`: `.github/workflows/ci.yml` now has a sixth job, `relay` (offline tests of the new `plugins/relay`), which runs like `gauge`, is not conditional on the `plan` job and carries the fork guard; the guard test `test_required_checks_keep_their_names_and_fork_guard` still covers four of the six jobs. No Foundry code, constant or CLI changed.
- PAT-246: Corrected the CI jobs list of `README.md`: `.github/workflows/ci.yml` now has a fifth job, `gauge` (offline tests of the new `plugins/gauge`), which runs like `ship-ios` and `catalogue`, is not conditional on the `plan` job and carries the fork guard; the guard test `test_required_checks_keep_their_names_and_fork_guard` still covers four of the five jobs. No Foundry code, constant or CLI changed.
- PAT-245: `python3 -m foundry.rollback_native_trial` can now run a Codex rollback line (`codex exec --json`, one call per line, no retry, same locks, out-of-checkout ledger and matrix claim as the Claude lines; model and effort observed only from the host session file of a completed turn, else unknown; options `--codex`, `--codex-sessions-dir`). Section 10 of the PAT-198 protocol fixes the surface, ceilings and observation before any call and withdraws the stale Codex usage-limit statement. The four Codex lines were then run once each on 2026-10-18 (section 11 of that protocol): all four `conforming` and `available_observed`, `native_calls: 8`; token counts are recorded, the money cost is unknown.
- PAT-244: Settled the remarks deferred from the reviews of PAT-198 and PAT-245 and of PAT-184. `foundry.rollback_native_trial`: `--max-budget-usd` must be finite; masking applies the previous named-secret rule (a name containing `TOKEN`, `SECRET`, `KEY`, `PASSWORD` or `AUTH`, now also `PASSWD`, any case, before `=` or `:`) but leaves the words of `_KEPT_WORDS` (`monkey`, `monkeys`, `donkey`, `turkey`, `hockey`, `jockey`, `whiskey`, `keyboard`, `keyboards`, `authentication`, `author`, `authors`, `tokenizer`, `tokenizers`, `tokenize`, `secretary`, `secretaries`) unless their first two letters are upper case, and still masks the whole value up to the next whitespace (`&` and the rest of a URL query included), and now covers short OpenAI identifiers; `_UNAVAILABLE` ignores a bare `unknown`, and reads a new `unknown model` branch only as a whole word (Claude) or before the requested identifier (Codex), so the phrase `unknown model_reasoning_effort` alone is never read as an unavailable model; availability goes back to `unknown` with several children; `agent_types` is `unknown` when unreadable; results carry `ledger.data_dir_fingerprint`. `scripts/skill_evals.py`: the observed `Reached maximum number of turns` error no longer makes a passed case `unknown` (graders checked run by run), a threshold below 1.0 never relaxes a pass, crashes exit 2, the default result path moves out of `evals/`. Documentation: section 12 of `docs/qualification/pat-198-native-rollback-protocol.md` (dated amendment, no recorded value rewritten) and `docs/skill-evals.md` (observed run of 2026-10-17).
- PAT-242: Fixed three defects found while settling PAT-214. `issue close-epic` with an unknown flag now lists its four flags (`--status`, `--human-verdict=`, `--accept-override=`, `--accept-dropped=`) instead of `(aucun)`. `campaign_effects._save_summary` and `campaign_review._write_private` write a lone surrogate as `?` (the file stays valid UTF-8): `_save_summary` raised `UnicodeEncodeError` on it, and the `_write_private` change is defensive (its callers pass ASCII JSON today). `docs/campaign-effects.md` names the residue of a directory-dependent shim at every later `claude` start in the worktree (deduced, not observed).
- PAT-241: Settled the deferred review remarks on the pinned historical ADR bodies in the Linear adapter (PAT-209). The comment of `_PAT_201_PROFILES` now states exactly what `pre` and `post` rewrite (the split bold spans of `FOUNDRY-ADR-0015` and `-0017`, `\[…\]`, the table separator line, the autolink). New tests prove that a status change keeps the stored body of each PAT-209 pinned ADR, that `create_adr` accepts the exact bodies of `FOUNDRY-ADR-0003` and `-0015` as new bodies and refuses that of `-0017`; a no-op `replace` was removed from an existing test. Documentation: `docs/linear-tracker.md` no longer says that every other multiline inline construction, or only two complete sources, is refused or recognised, and names the pinned bodies of `FOUNDRY-ADR-0015` and `-0017`; `docs/qualification/pat-201-historical-adr-links.md` says which bodies `create_adr` accepts and no longer attributes the wording "multi-line inline code" to the PAT-201 census.
- PAT-240: Corrected the PAT-219 campaign qualification record and the Linear request-cost page (set comparisons, per-step wording, node counting 56 with the Epic, source of the observed effect counts, PAT-ADR-0027 wording), added clarifications to the PAT-220 sandbox trial record, and made the shadow reads of `test_campaign_request_cost.py` fail loudly.
- PAT-239: settle deferred review remarks on the human acceptance of a non-code issue
  (PAT-206) and the Linear review-state regression tolerance (PAT-230). `issue accept --status`
  says "relance" unconditionally only for native `done`; for `backlog`, `ready` or
  `in-progress` it adds "si l'état natif est encore celui de la décision" (the replay refuses
  a native state other than the decision's `from_state`), and any other state is named as
  not a replay state (`_ACCEPT_RETRY_STATES`). An unknown flag of `accept` lists its real flags,
  `acceptance_decision.FROM_STATES` is the single set of admitted predecessors (shared with
  `trackers/linear.py`), the review projection checks the review receipt chain before
  choosing the latest receipt (one shared `_check_review_chain`), and the readback looks the
  history up by the known issue id.
  Documentation status (R5): updated `docs/linear-tracker.md` (replay limits, digest
  exactness, whole-body binding, structural evidence check, refusal list, "at or after" rule);
  no other CLI verb, option or configuration key changed.
- PAT-238: Settled the deferred review remarks on the campaign deferral of non-blocking remarks. A parent Epic is a companion Epic only when its title is `Nits ` followed by an identifier (`_COMPANION_NAME`), not when it merely starts with `Nits `; the public refusal reason is masked for credentials (`classify`, `deferral_lines`); a quota error's `reset_at` delays the retry of the deferral until that time, at most `MAX_RESET_WAIT_SECONDS` after the attempt (`reset_of`, `deferral_facts`), and a creation or read that failed on quota with a `reset_at` is reconciled by the next pass instead of at once, so the time is not lost (`_create`, `_reconcile`); the dead `automatic_retry` branch of the creation is removed; a refused replay of a created companion Epic now names it; the orphan scan also runs after any earlier attempt that did not end `done`; only the deferral plan carries the tracker and the project. Documentation: `AGENTS.md`/`CLAUDE.md` R9 now say `MAX_DEFER_TRIES` attempts in total and state the orphan-scan condition exactly, `docs/campaign-effects.md` cites the right source for the comment window and names the read-after-write duplicate risk as deduced. New tests cover each behaviour, the request-counter suffix at three more sites, and the receipt/proof and approved-structure assertions were made discriminating.
- PAT-237: Settled the deferred review remarks on campaign review and correction. A correction whose PR head moved only on the remote (the PR head is not an ancestor of the branch head) now suspends `pr_head_not_in_branch` instead of deciding `open-pr`; a partial review whose reviewer read the whole diff can say so with the optional result key `full_review` and is then recorded as a full round; remark texts are scrubbed (claim capability, token, credential shapes) before the receipt and the tracker comment; a remark already deferred in an earlier round of the same ticket is not deferred again and the partial-review prompt no longer says every remark was marked "fix before merge"; a description correction is not launched when the worktree HEAD is not the PR head (`description_worktree_head_moved`) and an unreadable HEAD is `description_head_unreadable`, not `description_session_moved_head`; the escalation signal of a correction falls back to the floored tier when the tier without the floor is unavailable; the documentation states that the sessions of `campaign_effects.py` keep their transcript, unlike `campaign_runtime.py`, and that the effect of `--no-session-persistence` on the echoed session id is unknown. New tests cover each behaviour and the authority limit of `campaign_review.py`; `docs/campaign-effects.md` and `docs/campaign-cadence.md` document the claim left active after every failure past the claim, the summary criterion as coded, and the R5 statuses of PAT-218 and PAT-227 are corrected.
- PAT-236: Settled the deferred review remarks on the campaign effects and the sandbox. `--worktrees-dir` is resolved to an absolute path, `claude --version` of a pinned model now runs in the directory (and with the environment) of the session it precedes, a saved session summary is cut on a character boundary (it always decodes as UTF-8) and an undecodable summary file is published as the unusable-summary text instead of failing the plan, and the git directories of the ticket worktree are read before the attempt is claimed (an unreadable answer no longer consumes an attempt). New tests: concurrent claims of one identity grant exactly one, a correction launched through `step` carries the sandbox settings, and one for each of the five behaviours above. `AGENTS.md#R7` names the three headless runners that check the host version and the working directory each uses; `docs/campaign-effects.md` completes the `finished` keys, the matching rule of `DISALLOWED_TOOLS`, the worst-case spend, the point-in-time branch check, `.gitattributes`/`.lfsconfig`, `git_hooks_dir_refused`, worktrees never removed and the request counts lost on interruption.
- PAT-235: Settled the deferred review remarks on the campaign cadencer's dry next step. `decide` returns `await-verdict` before an expired or absent approval refusal, a CI with a known failure is `red` without waiting for the other checks (green still needs the two sources proven, FOUNDRY-ADR-0002), a persisted review ledger record or proof that is unreadable, invalid or inconsistent (including an invalid claim publication, claim id, coordinates or head) suspends with `review_proof_integrity` (new `ProofIntegrityError`, a `RoutingConfigError` subclass raised on the read path of the persisted record, instead of being read as no review; an advisory or terminal record in the proof directory is the same error; an absent marker, a corrupt marker while the proof directory does not exist, or an absent, stale or ambiguous proof, stays a plain `RoutingConfigError`), a `review` ticket with no branch and no PR suspends with `review_state_without_branch_or_pr` instead of restarting, and `campaign-cadence next` reads the same attempt-journal facts as `step` (`campaign_effects.journal_facts`) so it announces `defer-remarks`. Documentation of `docs/campaign-cadence.md` completed (reason codes, additive output fields, proof-store read side effect), plus the missing test cases.
- PAT-234: Settle the deferred review remarks on campaign approval and Epic preview. `campaign-approval approve` now caps the approval at the approved preview's `policy.expires_at`, shows the retained expiry before the confirmation, refuses an expired preview or one without a valid `policy.expires_at`, refuses a naive `now`, reads the journal under a shared lock, completes a partial `os.write` and reports an unwritable journal as a `journal_invalid` JSON refusal. The Claude Bash guard now denies the union of two rules at any position of a segment: the PAT-215 adjacency (`campaign-approval`, `campaign_approval` or `foundry.campaign_approval` immediately followed by `approve`, so a plain `grep` of those words is still denied on purpose) and a `foundry_cli` word then `campaign-approval` then `approve`, or the module word then `approve`, whatever the wrappers or flags. `build_preview` no longer defaults `tracker_name` to `devhub` for a `foundry-epic-subgraph.v1` graph, and external `depends-on` reads stop at `PREVIEW_COLLECTION_LIMIT` (400) before the refusal. Documentation: the approval layers and refusal codes, the structure digest's `graph_contract`, and the Linear estimate and comment limits.
- PAT-198: Added the protocol and the dated matrix for the native qualification of the eight PAT-17 rollback lines (`docs/qualification/pat-198-native-rollback-protocol.md`, `pat-198-native-rollback-2026-10-17.json`) and the tool `python3 -m foundry.rollback_native_trial` (one call per line, no retry, enforced by the matrix, an exclusive lock and an out-of-checkout reservation ledger; `--dry-run`; real policy digested before and after). Four native calls were made on 2026-10-17, one per Claude line: `claude-implementer`, `claude-reviewer` and `claude-architect` are `conforming`, `claude-scout` is `unknown` (dated snapshot, no effort logged). The four Codex lines were not run by this change (follow-up PAT-245). The PAT-17 configuration-only record is unchanged.
- PAT-184: Added an on-demand suite that replays five Foundry skills (`open-pr`, `merge-pr`, `start-issue`, `intake`, `resume-issue`) on fixed prompts through `claude plugin eval`, in `evals/`, graded only by `tool_used` on the `Skill` tool (no judge model). `scripts/skill_evals.py` prints the plan, refuses to launch without `--max-cost-usd` retyped on the terminal (and under an agent-session marker), and reports a gap as a "regression to examine" (partial or unreadable results as "unknown"), never as a promotion or removal. Documentation: `docs/skill-evals.md` (billing, judge model, hooks and Codex coverage are stated as unknown or not covered); no eval had been run when this was written (the first run, of 2026-10-17, is documented under PAT-244, `docs/skill-evals.md` "Observed run").

## 1.5.0 — 2026-10-17

Minor release. It packages the Epic campaign: the human approval of a campaign and the
steps that run it, and it changes behaviour, all merged after `foundry-v1.4.0`. New CLI surfaces:
`campaign-approval approve|status|check` (PAT-215), `campaign-cadence next` (PAT-216) and
`campaign-cadence step|run` (PAT-217, PAT-218, PAT-141), `issue accept` with `--status`
(PAT-206, Linear only) and `routing record-review-remarks` (PAT-141). `epic-preview` is an
existing verb that now works on Linear (PAT-178). Behaviour changes without a new verb: the
implementation and correction sessions of a campaign run in the native Claude Code sandbox
(PAT-220); the summary of a campaign session is filtered for credentials (PAT-223);
`campaign-approval approve` validates the whole record before any append (PAT-222);
on Linear, `issue openpr` tolerates the State going back to In Progress right after the
write only when the `stateHistory` proves it, with no second write (PAT-230); a failed `defer-remarks` keeps its real error (PAT-229); a campaign
remark that targets only the PR description is corrected without a new review (PAT-227);
the campaign reports its Linear requests and skips redundant lookups (PAT-228); `adr
link-issue` accepts three more historical ADR bodies (PAT-209). PAT-219 records a real
campaign and changes no code; PAT-224 and PAT-212 correct documentation and a helper
script; PAT-232 fixes a test. Release note: `docs/release-1.5.0.md`; upgrade guide:
`docs/migration-1.5.0.md`. This dated section is not a claim that 1.5.0 is already tagged
or installed. The entries below are the fragments present after `foundry-v1.4.0`, kept as
written.

- PAT-232: `tests/test_campaign_approval.py::test_cli_status_and_check` approved at a fixed
  time (2026-10-16T12:00Z) while the CLI `check` reads the real clock, so it failed once that
  approval expired (CI of `main` red from 2026-10-17). The test now approves at the real
  clock. Documentation status (R5): not necessary, tests only; no CLI verb, configuration key
  or documented behaviour changed.
- PAT-230: on Linear, `issue openpr` (state review) no longer fails when the native State
  goes back from In Review to In Progress right after Foundry's write (observed: 292 ms
  later; deduced: by the team's `start` Git automation, `stateHistory` has no author).
  The bounded readback accepts that regression only when the `stateHistory` proves In
  Progress, then Review after the latest review receipt, then In Progress (the PAT-76
  verification); no second write and no retry (PAT-ADR-0006, S2), other divergences
  still fail.
  Documentation status (R5): updated `docs/linear-tracker.md` and
  `docs/linear-request-cost.md` (artefact: bounded State readback for `review`). No CLI
  verb, option or configuration key changed.
- PAT-229: a failed `defer-remarks` attempt keeps the real error and its partial writes. The
  `foundry linear_requests=N` lines printed at exit when `FOUNDRY_REQUEST_COUNT=stderr` are
  dropped (`without_request_count`, `campaign_effects.py`) before the classification, the
  reading of the error JSON and the `detail` tails of `campaign_defer.py`,
  `campaign_effects.py` and `campaign_review.py`; the `deferral` of a failed attempt now
  carries `companion` (found or created before the failure). Documentation status (R5):
  updated `docs/campaign-effects.md` (deferral field and error tails); no CLI verb or
  configuration key changed; `AGENTS.md` R9 unchanged (still true: the cadencer reads the
  text of the error, `_DURABLE`).
- PAT-228: the Epic campaign now reports the Linear requests of each pass (`requests` on every
  step of `campaign-cadence step|run`, a `requests` summary of observation, wait and effects, and an
  optional `requests` key on the `finished` line of the attempt journal; new `Tracker.request_count`,
  `null` where a tracker does not count). The deferral of remarks no longer lists the whole project
  twice per attempt: it lists it at most once, and not at all when an earlier attempt of the Epic
  journaled the companion Epic and none can have created a follow-up. Measured on the GraphQL
  double with one closed Epic of 55 nodes: 59 or 116 requests for the lookups before, 59 then 3
  after; a replayed campaign splits 35 observation requests (12 spent waiting) and 60 effect requests
  (117 before). Limit: an orphan follow-up created outside the journal of the campaign Epic is not
  seen when the scan is skipped (`docs/linear-request-cost.md`, "Epic campaign (PAT-228)"). The observation of a pass that
  waits is unchanged (PAT-ADR-0027).
  Documentation status (R5): updated `docs/campaign-effects.md`, `docs/campaign-cadence.md`,
  `docs/linear-request-cost.md`, `docs/review-rounds.md`, `AGENTS.md`/`CLAUDE.md` R9 (a duplicate
  companion Epic is refused only when the project is listed); artefacts listed in `docs/campaign-effects.md` ("R5 status
  (PAT-228)"). No CLI verb, option or configuration key changed.
- PAT-227: in an Epic campaign, a remark marked "fix before merge" that targets only the PR
  description no longer ends in `suspend attempt_already_made`. The reviewer marks each remark
  with a `target` (`diff` or `description`; a receipt without it reads as `diff`). When every
  marked remark targets the description, the `correct` effect (reason `review_fix_description`)
  asks a session for a new description only; it replaces the PR body with the `## Review
  rounds` section and a line saying it was not re-reviewed (the diff is unchanged), and the
  pass goes on. A failed or unusable correction suspends `description_correction_not_settled`.
  Implementation and correction summaries must start with the line `FOUNDRY_SUMMARY_V1`; a
  summary without it is never published as the PR body, a fixed marked text is. Residual risk:
  a description corrected this way is not re-reviewed, and the merge gate never reads it.
  Documentation status (R5): updated `docs/campaign-effects.md`, `docs/campaign-cadence.md`,
  `docs/review-rounds.md` and the R9 sentence of `AGENTS.md`/`CLAUDE.md` ("Inside an Epic
  campaign") on what the correction loop re-reviews.
- PAT-224: three statements of the campaign documentation corrected against the code.
  `docs/campaign-cadence.md`: the exit code of `next` is not "0 otherwise" (errors that
  `observe` does not convert, such as network, code host or binding `SystemExit`, end the
  command with another code); the approval check uses the journal's preview digest because
  the node versions change when the campaign writes a node (deduced), so
  `preview_digest_mismatch` is unreachable there. `docs/campaign-effects.md` ("Bounds"): the list of suspensions is
  complete. Documentation status (R5): updated `docs/campaign-cadence.md` and
  `docs/campaign-effects.md`; artefacts `campaign-cadence next` exit code, approval check,
  suspension reasons. No CLI verb or configuration key changed.
- PAT-223: the summary of a campaign session is filtered before it is stored and sent as the
  pull-request body: URL userinfo, `Authorization` header values and well-known token shapes
  become `[REDACTED]` (`campaign_effects.mask_credentials`, in `_save_summary`). The session can
  still read `<common>/config` and `~/.gitconfig`; a credential of an unknown shape is not
  masked (unknown). Documentation status (R5): updated `docs/campaign-effects.md` ("The native
  sandbox"); artefact `mask_credentials`. No CLI verb or configuration key changed.
- PAT-222: `campaign-approval approve` now validates the complete record (including the Epic
  against the issue-identifier format) before any append and refuses without writing, so a
  fabricated preview with an invalid root can no longer make the journal unreadable for every
  Epic. Documentation status (R5): `docs/campaign-approval.md` updated (refusal list).
- PAT-220: the implementation and correction sessions of an Epic campaign now run in the
  native Claude Code sandbox (PAT-124 settings, `--permission-mode dontAsk`, `--settings`,
  `--setting-sources user`; the old `--permission-mode auto` is gone). Shell writes are
  limited to the ticket worktree, the temporary directory and the git paths of a local
  commit (`objects`, `refs`, `logs`, the worktree git directory); the Foundry data directory
  (proof store, approval and attempt journals, remarks receipts), the Foundry configuration,
  `~/.claude` and `~/.codex` are denied to reads and writes, and `hooks`, `config`, `info`,
  `refs/replace` of the git common directory stay unwritable. The reviewer session stays
  outside (its read verbs take a lock in the data directory). No MCP server (`--strict-mcp-
  config`). Outside the sandbox: the `claude` process and its network, the keychain.
  `~/.gitconfig`, `~/.config/git` and `~/.claude.json` are denied to writes. Tests read the
  built settings; one live session (2026-10-17, `docs/qualification/pat-220-sandbox-
  trial-2026-10-17.json`) observed once, with the refusing layer not recorded, the refusals
  of writes to the proof store and attempt journal, `~/.config/foundry`, `~/.claude`, the
  worktree `.claude` and `.git` pointer, the common `hooks` and `core.hooksPath`, and a `git
  commit` in the worktree. Not tested, unknown: Glob/Grep, reads, `commondir`,
  `config.worktree`, `refs/replace`, the network, the three user files above, other hosts
  and versions. A hook under a relative `core.hooksPath` sits in the worktree the session
  writes, so every subprocess of the cadencer's `_run`, sessions excepted, gets the hooks
  directory of the main checkout (`Runtime.hooks_dir`, `git rev-parse --git-path hooks`
  from the repository root) and `core.fsmonitor=false` through `GIT_CONFIG_COUNT`
  (`campaign_cadence.git_neutral_env`): a hook planted in the worktree does not run; the
  repository's `pre-push` hook (AGENTS.md#R1 guard, `docs/commit-privacy.md` check) and
  a global absolute hooks directory still run (tests, one git version). A hooks directory
  inside the worktrees directory makes the pass `refuse` `git_hooks_dir_refused`. Before
  `open-pr`, `review` and `defer-remarks` (`PUSH_KINDS`), a worktree HEAD that is not the
  ticket's branch or is the default branch makes the pass `suspend`
  `worktree_branch_mismatch`, with no push.
  Documentation status (R5): updated `docs/campaign-effects.md` ("The native sandbox");
  artefacts `sandbox_settings`, `git_dirs`, `session_argv(settings)`, `git_neutral_env`,
  `Runtime.hooks_dir`, `branch_mismatch`, the constant `PUSH_KINDS`, the reasons
  `git_hooks_dir_refused` and `worktree_branch_mismatch`. Not necessary for AGENTS.md#R1
  (and CLAUDE.md) and `docs/commit-privacy.md`: they stay true for campaign pushes, whose
  git runs the repository's own `pre-push` hook (`Runtime.hooks_dir`, test
  `test_cadencer_push_runs_the_main_checkout_hook_never_one_planted_in_the_worktree`). No
  CLI verb or configuration key changed.
- PAT-219: first real Epic campaign on this repository, recorded in
  `docs/qualification/pat-219-epic-campaign-2026-10-17.md`. The qualification Epic PAT-221
  (three tickets) reached `await-verdict` in three runs and was closed with
  `close-epic --human-verdict=accepted`. Two tickets merged through the campaign; one was
  finished by the coordinator after a `suspend` `attempt_already_made`. Sessions cost about
  5.0 USD. Linear requests of the cadencer process: 1723 for 14 steps (run 1), 13 for 3
  (run 2), 479 for 12 (run 3), far above the PAT-177 estimate of about 75 per ticket. The
  effects counted by their subcommand are close to their estimates (`start` and the first
  `open-pr` inside, the re-push below, `merge` above); where the excess comes from is unknown (deduced lead: the reads of the deferral
  effect). Deviations filed as PAT-227 to PAT-230. Documentation status (R5): new
  qualification record. The criterion "the documentation no longer says that deferral has
  no effect in a campaign" was met by PAT-141 (AGENTS.md#R9 "Inside an Epic campaign",
  `docs/review-rounds.md`, `skills/merge-pr`); no other page changed. No CLI verb or
  configuration key changed.
- PAT-218: `campaign-cadence step` and `run` now review and correct. A `review` claims the
  exact diff, writes the private packet, runs one fresh read-only headless Claude reviewer
  at the routed reviewer tier (FOUNDRY-ADR-0006 floor; `dontAsk`, Read/Grep/Glob and the
  three `routing` read verbs, user settings only, no project MCP server), parses its
  verdict strictly (closed schema, else no
  proof) and records the proof with `record-review-proof`. A blocking review records the
  escalation failure (`human_required` suspends the campaign) and gives the findings to one
  fresh implementer session; the commit is pushed with `issue openpr`, then reviewed again.
  A round after a validated review with remarks marked "fix before merge" is a partial
  review (PAT-ADR-0022); a clean merge of main after a validated review tries
  `carry-review-proof` first (PAT-ADR-0021). At most 3 review rounds and 2 corrections per
  ticket (`review_rounds_exhausted`, `corrections_exhausted`); each review also writes its
  rounds in the PR description (PAT-ADR-0022); reviewer and correction
  sessions count against the approval ceilings. The claim capability stays in 0600 private
  files and never reaches the journal or the output. Non-blocking remarks stay in the saved
  report and the step output: carrying them to a companion Epic is PAT-141. Red-CI
  correction, ADR selection for the packet and Codex are not done, and the reviewer runs as
  the same OS user (a discipline boundary, not a sandbox). Documentation status (R5):
  updated `docs/campaign-effects.md`, `docs/campaign-cadence.md`, `docs/review-rounds.md`,
  `AGENTS.md`/`CLAUDE.md` R9, README.
- PAT-217: New `campaign-cadence step <EPIC>` and `run <EPIC>` execute the next step of an
  approved Epic campaign (PAT-ADR-0026), at most one effect per pass: `issue start` in a
  linked worktree outside the repository, one fresh headless Claude session at the routed
  implementer tier (budget-bounded, push, `gh` and the Foundry `issue`/`edit`/`adr` verbs
  denied by prefix rules, allowlisted environment), `issue openpr`, and `issue merge`, which
  keeps its own CI and review-proof gates. A local attempt journal never launches the same
  attempt twice and counts sessions against the approval ceilings. `decide` gains `open-pr`;
  a closed PR is now re-read with `get_pr` before it is called merged. Review and correction
  are not executed (PAT-218), Codex is not wired, and the session runs as the same OS user
  and can write the local Foundry state, review proofs included (a discipline boundary, not
  a sandbox). Documentation status (R5): new `docs/campaign-effects.md`, updated
  `docs/campaign-cadence.md`, `docs/review-rounds.md`, `AGENTS.md`/`CLAUDE.md` R9, README.
- PAT-216: New `campaign-cadence next <EPIC>` computes, read-only, the one next step of an
  Epic campaign (PAT-ADR-0026): it re-reads the tracker, git and the PR, and returns one of
  `start`, `implement`, `review`, `correct`, `wait-ci`, `merge`, `await-verdict`, `suspend`
  (drift of PAT-ADR-0027, with a motive) or `refuse` (missing capability or approval), the
  same bytes for the same observation. It produces no effect on the tracker, git or the PR
  (the review store may create its local lock file) and requires the declared tracker
  capabilities before any read. `epic_preview.classify_nodes` is extracted from
  `build_preview`, unchanged. Documentation status (R5): new `docs/campaign-cadence.md` and
  `README.md` pointer; the cost per pass is measured by the issue, not stated here.
- PAT-215: New `campaign-approval` verbs (`approve`, `status`, `check`) record the human
  approval of an Epic campaign (PAT-ADR-0026): an append-only journal outside the
  repository (`registry.data_dir()`) binds the Epic, the exact preview digest, the
  structure digest, an expiry and session ceilings; `check` refuses a different digest, an
  expired or an absent approval before any effect. An agent cannot approve: the
  confirmation is read from `/dev/tty`, agent-session markers refuse, and the Claude
  `PreToolUse` Bash guard denies the command; a same-user process can still append to the
  journal directly. No tracker is read or written. Documentation status (R5): new
  `docs/campaign-approval.md`, `README.md` pointer, and one sentence in `AGENTS.md#R1`
  (a guard denial in every repository, before any registry read).
- PAT-212: Settle the remarks deferred from the PAT-196 reviews and the 1.4.0 release
  review. `scripts/codex_profile_provenance.py` no longer fails on an absent profile with
  `--rollout` or on a JSON line that is not an object, passes `foundry_versions` through
  `_token`, and no longer echoes values containing `/` or `:`;
  `docs/qualification/pat-196-codex-reviewer-provenance.md` records the observed-run facts
  (`codex --version`, cache listing, `collaboration_mode.settings` model and reasoning
  effort), the exact native command and its directory, and the limits of "matched" and of
  the built-in source; both READMEs state the `foundry-v1.4.0` tag as now known.
  Documentation status (R5): the helper's options are unchanged, the qualification page and
  READMEs are updated; no routing constant, model or effort vocabulary, or configuration key
  changed.
- PAT-209: `adr link-issue` now accepts the exact stored bodies of `FOUNDRY-ADR-0003`,
  `FOUNDRY-ADR-0015` and `FOUNDRY-ADR-0017` through three more digest-pinned entries of
  `_PAT_201_PROFILES`; any other or altered body stays refused before any write, and
  `FOUNDRY-ADR-0017` (a table) stays refused as a new body. Documentation status (R5): the
  private constant is documented in `docs/qualification/pat-201-historical-adr-links.md`
  and `docs/linear-tracker.md`; no CLI verb, option or configuration key changed.
- PAT-206: New `issue accept <ID> --human-verdict=accepted --decision-file=<path>` and
  read-only `issue accept <ID> --status` (Linear only, PAT-ADR-0025) accept an issue with
  no code and no PR by one closed human decision: per criterion a `pass` verdict, evidence
  from a closed list (a confirmed advisory report or an issue of the same project) and the
  unknowns copied verbatim. It appends one `acceptance-decision` lifecycle receipt, then
  projects `done`; the read-back must read done, aligned and accepted (source
  `linear-human-acceptance`). Epic closure counts it as accepted and the release scope
  reads it `accepted` with `acceptance_decision_digest`. YouTrack, GitHub Projects and
  DevHub refuse before any read. Documentation status (R5): CLI verb and flags, the
  decision-file keys and the receipt are documented in `docs/linear-tracker.md`, the
  capability row in `docs/tracker-contract.md` and `tracker-contract.v1.json` (`version`
  now 4); the sequence is run on the test double only, never on a live workspace.
- PAT-178: The Epic preview now works on Linear. `LinearTracker` sets
  `epic_subgraph_supported` and builds the neutral `foundry-epic-subgraph.v1` (PAT-ADR-0027)
  from `get_issue` reads (`tooling/foundry/epic_subgraph.py`); `devhub-application.v1` is
  still accepted and its preview digest is unchanged. The preview result gains
  `structure_digest` (ids, types, hierarchy, internal `depends-on` edges, external blocker
  pairs and the Epic validation digest), outside the digested material, and
  `authority.tracker` carries the tracker name. Documentation status (R5):
  `docs/epic-preview.md`, `docs/tracker-contract.md` and `.v1.json`, `docs/review-rounds.md`
  and the R9 paragraph of `AGENTS.md`/`CLAUDE.md` are updated; no CLI verb, option or
  configuration key changed.
- PAT-141: an Epic campaign now carries the non-blocking remarks of a validated review
  (PAT-ADR-0028). The reviewer's result gains a `remarks` key, each remark marked "fix
  before merge" or not, with counts that must agree. `routing record-review-remarks` writes
  a local receipt bound to the proof id, the diff hash and the review generation, beside
  the unchanged proof; `issue merge` never reads it. The new `defer-remarks` effect, before
  `merge`, finds or creates the companion Epic `Nits <ORIGIN-EPIC-ID>` with no link to the
  origin Epic, finds or creates one open follow-up issue under it, posts one comment per
  validated round, never one per remark, and cites every follow-up in the PR description.
  It reads the tracker before every write, so a retry duplicates nothing. A durable
  refusal (capability absent, `Epic` type refused, two companions of the same name, a
  creation the adapter refuses to replay), a ticket with no origin Epic and a ticket of a
  companion Epic make the campaign correct the remarks before the merge; a transient
  failure, or a creation the read shows did not happen, makes it wait and retry (`wait`,
  outcome `waiting`; 5 attempts at most). Rule R9,
  `merge-pr` and `docs/review-rounds.md` no longer say the authorization has no effect in a
  campaign. Documentation status (R5): updated `docs/campaign-effects.md`,
  `docs/campaign-cadence.md`, `docs/review-rounds.md`, `skills/merge-pr/SKILL.md`,
  `AGENTS.md`/`CLAUDE.md` R9, README; the new CLI verb, journal key, decision kinds and
  constants are listed in `docs/campaign-effects.md`.

## 1.4.0 — 2026-10-15

Minor release. Most entries below change behaviour. New CLI surfaces: `routing
carry-review-proof` (PAT-173), `routing review-rounds` (PAT-175) and the target
`backlog` of `edit transition <ISSUE-ID> backlog <expected-state>` on Linear (PAT-165,
with PAT-ADR-0024); it adds the skill `foundry:release` and the read-only helpers
`scripts/release_freeze_entry.py` (PAT-166), `scripts/changelog_assemble.py`
(PAT-210) and `scripts/codex_profile_provenance.py` (PAT-196). Behaviour changes without
a new verb: the review that follows the correction of the non-blocking remarks of a
validated round is partial on every pull request (PAT-172, process contract); closing an
Epic accepts a required child Epic already closed under a valid audit (PAT-185); a closed
Epic stays readable after a native update of a node that changes nothing of substance, on
Linear and YouTrack (PAT-208); `adr link-issue` accepts the issue links of two historical
ADR bodies (PAT-201) and refuses before any write a source whose stored version the model
does not prove (PAT-205); the advisory post-merge `prepare` writes the report budget into
the reviewer message and refuses a non-positive room (PAT-203); the R1 guard names the
global registry when that registry makes it fail closed, and `registry.load()` refuses a
provider or an entry that is not an object (PAT-63) and reads the registry as UTF-8
(PAT-205). PAT-205 also changes the issue-creation validation of `edit create-issue`
(`(AGENTS.md#R<n>)` autolink qualified), adds an optional `causes` key to an existing
error payload, and makes `routing claim-review` without `--host` write one line to standard
error. PAT-207 hardens `routing carry-review-proof`, adds the `tokens.scope` marker to
`routing review-rounds`, and makes `changelog_assemble.py` refuse a fragment with several
list items and a malformed version or date. PAT-171 records a measurement and changes no
routing default, model or effort; PAT-196 changes no model, effort, routing or
configuration. From this release, unreleased entries are written as fragments
`changelog.d/<ISSUE-ID>.md` and this section is assembled from them in the order of
issue number descending, not in merge order (PAT-210). Release note:
`docs/release-1.4.0.md`; upgrade guide: `docs/migration-1.4.0.md`. This dated section is
not a claim that 1.4.0 is already tagged or installed. The entries below are the
fragments present after `foundry-v1.3.0`, kept as written.

- PAT-210: unreleased changelog entries now live in `changelog.d/<ISSUE-ID>.md`, one fragment
  per issue, so pull requests open in parallel no longer conflict on `CHANGELOG.md`.
  `## Unreleased` stays an empty heading. New read-only helper
  `scripts/changelog_assemble.py <version> <date> [--dir]` prints the dated section from the
  fragments (issue number descending); the `release` skill pastes it into `CHANGELOG.md` and
  deletes the fragments in the release pull request. Published sections and freeze entries
  are unchanged. Documentation status (R5): `skills/release/SKILL.md`, `changelog.d/README.md`
  and `README.md` updated; new helper script documented there; no CLI verb, configuration
  key or public constant changed.
- PAT-208: a closed Epic stays readable after a native update of one of its nodes that
  changes nothing of substance (PAT-ADR-0023, accepted). The audit still records the native
  version; on read and on done replay, `write.epic_closure_graph_unchanged` accepts a node
  whose version is equal or newer when id, state, AC counters, acceptance status, source and
  coordinates are unchanged, and the graph shape is equal (Linear and YouTrack; GitHub
  Projects keeps the exact comparison because its node version is an opaque digest of the
  node snapshot that does not change on a timestamp-only update). A version going back, a
  changed field or an added/removed link still refuses; a `dropped` node follows the same
  rule as the others, an `override` node and every write-path comparison stay exact.
  Documentation status (R5): updated `docs/linear-tracker.md` (new paragraph), the Epic
  closure row of `docs/tracker-contract.md` and the three bounded-provider evidence entries
  of `docs/tracker-contract.v1.json`; no CLI verb, option, configuration key or public
  constant changed (the new function is internal to the closure comparison and covered by
  the `docs/linear-tracker.md` paragraph).
- PAT-207: settles the non-blocking remarks deferred from PAT-173, PAT-175, PAT-171, PAT-172,
  PAT-203 and PAT-210. `routing carry-review-proof` now recomputes the final diff on the head it
  read under the lock, also checks `--from-base` on a replay, and refuses an unchanged base
  ("nothing to carry") even when the head moved to the same tree. A proof carrying
  `carried_from` is accepted by the Linear acceptance projection, `review_receipt` and the
  passive evidence publisher (optional key, closed four-key shape). `_git_run` no longer raises
  `UnicodeDecodeError` on a non-UTF-8 path. `routing review-rounds` gains a `tokens.scope`
  marker in its JSON, says "no non-carried review proof" when only carried proofs exist, and
  `AcceptanceProofStore.validated_proof` / `ReviewDeduplicator.valid_utc_timestamp` become the
  public names it reads. The per-round line of the PR description gains `review <full|partial>`
  (`merge-pr`). `postmerge-review prepare` estimates the report budget with a timestamp that
  always has microseconds, and its mismatch message names the hook context. `changelog_assemble.py`
  refuses a fragment with several list items and a malformed version or date. Corrections to
  `docs/review-rounds.md`, `docs/postmerge-review.md`, `docs/qualification/pat-171-haiku-55-window.md`,
  `changelog.d/README.md`, `AGENTS.md#R9` (and `CLAUDE.md`), and the `release`, `merge-pr`,
  `open-pr` and `review-pr` skills (release step 2 names the release-rule version list and the
  order of operations). Documentation status (R5): updated pages above for the carry command,
  the `review-rounds` output keys and scope, the report budget and the PAT-171 sources; the
  per-round line format in `merge-pr` is a surface a skill describes and is updated; no other
  CLI verb, option or configuration key changed.
- PAT-205: settles the review remarks deferred from PAT-197, PAT-201, PAT-63, PAT-165, PAT-166,
  PAT-185, PAT-204 and PAT-208. Behaviour changes: the issue-creation validation of
  `edit create-issue` (`_issue_creation_rendering`, `trackers/linear.py`) cuts lines on LF only,
  keeps exact the lines of a multi-line inline code span and of an HTML comment, and qualifies the
  `(AGENTS.md#R<n>)` form that Linear autolinks (it returned `validation: refused` on PAT-209);
  `IssueCreationValidationError.details` gains an optional `causes` key (sanitized operation,
  status, code and `reset_at` of a failed create call or read-back) and `reconcile-create`'s
  refusal names the diverging properties (`diverged: title, priority`), never their values (the
  `create-issue` refusal does not); `adr link-issue` refuses
  before any write a PAT-201 source whose stored version the model does not prove;
  `routing claim-review` without `--host` still defaults to `claude` but now writes one line to
  standard error naming the default and the skipped Codex precheck (stdout unchanged);
  `registry.load()` reads the registry as UTF-8; `foundry.tracker()` no longer reports an invalid
  global registry as a repository binding; `epic_graph_diagnostic` lists a closed child Epic
  named in `--accept-override`; the `release` skill lists exact `git` prefixes (no `git tag`, no
  `git push`) in `allowed-tools`. The rest is documentation and tests. Documentation status (R5):
  `docs/linear-tracker.md`, `docs/linear-request-cost.md`, `docs/tracker-contract.md` and its
  JSON, `docs/model-routing.md`, `docs/qualification/pat-201-historical-adr-links.md`, the
  `review-pr`, `merge-pr` and `release` skills and `README.md` updated; no CLI verb, option or
  configuration key changed (the `causes` key is an addition to an existing error payload).
- PAT-203: the advisory postmerge `prepare` now measures the empty v2 terminal receipt with
  the shipped serializer (prefix and header included) before consuming the authorization,
  and writes the report budget `floor(room * 75%)` (`room = MAX_COMMENT_BYTES - size`) into
  the reviewer message by code instead of a coordinator instruction. It refuses, before any
  write, when `room` or the budget is not positive. `record` and the estimate share one
  `terminal_receipt` builder. Documentation status (R5): updated
  `docs/postmerge-review.md` and the advisory section of `skills/review-pr/SKILL.md`; new
  public constant `REPORT_MARGIN_PERCENT` (`advisory_schema.py`) documented there; no CLI
  verb or option changed. The budget is a request, not an enforced host bound.
- PAT-201: `adr link-issue` now accepts the issue links of the two historical native bodies
  `FOUNDRY-ADR-0002` and `FOUNDRY-ADR-0018`, previously refused before any write with
  `unsupported strong inline-code Markdown in ADR body`. A source-digest-pinned profile
  (`_PAT_201_PROFILES`, `trackers/linear.py`) binds each complete source to the exact bytes
  Linear stored for its version 0 (the version-1 rendering is deduced, not observed); the bodies, statuses and proofs are not rewritten, and any other or altered
  body stays refused. `FOUNDRY-ADR-0003` has the same refusal and remains refused (not in
  scope). Census and limits: `docs/qualification/pat-201-historical-adr-links.md`.
  Documentation status (R5): that page and `docs/linear-tracker.md` updated; no CLI verb,
  option or configuration key changed.
- PAT-196: procedure to establish which reviewer profile a new Codex session loads, in
  `docs/qualification/pat-196-codex-reviewer-provenance.md`, with the read-only helper
  `scripts/codex_profile_provenance.py` (prints hashes, key names and short fields, never
  content; tests on synthetic fixtures) and pointers from `examples/codex-reviewer.toml` and
  `docs/reviewer-queries.md`. The one native session is run by the coordinator and recorded
  in the page. No model, effort, routing or configuration change. Documentation status (R5):
  new page and new script options documented; no existing CLI verb, configuration key or
  public constant changed.
- PAT-185: closing an Epic whose required child is itself an Epic already closed under a
  valid closure audit (possibly with nominative waivers and dropped nodes) is accepted
  instead of refused with "la preuve AC de enfant ... est inconnue" (`write._closed_child_epic`,
  `tooling/foundry/write.py`, via `get_epic_closure`). A child Epic that is not closed, or
  whose audit is absent, invalid or divergent from its current graph, is still refused.
  Decision recorded in `docs/linear-tracker.md` under PAT-ADR-0014 and PAT-ADR-0017, no new
  ADR. Documentation status (R5): updated `docs/linear-tracker.md` (new paragraph),
  `skills/close-epic/SKILL.md`, and the Epic closure rows of `docs/tracker-contract.md` and
  `docs/tracker-contract.v1.json` for the three bounded providers; no CLI verb or option, configuration key changed; new
  internal constants `_EPIC_CLOSED_STATUS`/`_EPIC_CLOSED_SOURCE` are covered by the
  `docs/linear-tracker.md` paragraph.
- PAT-175: new read-only verb `routing review-rounds <ISSUE-ID>` (`tooling/foundry/review_rounds.py`)
  reports, for an issue, its review rounds and outcome from the local proof store, the
  duration from the review ledger timestamps and, for the session logs passed with
  `--session` / `--codex-session`, tokens per class (Claude only split between coordinator and
  sub-agents, Codex is `codex_unclassified`; full totals of the logs passed, not filtered by
  issue); with no proof in the local data directory and repository namespace the round counts are
  `unknown`; anything unreadable is `unknown`. Documentation status (R5): new section
  "Per-issue report" in `docs/review-rounds.md`; the window tables there stay counted by hand
  and the first / after blocking / after remarks split is not computed by the verb; no other
  CLI verb, key or constant changed.
- PAT-173: `routing carry-review-proof` carries a terminal `mergeable` all-pass review
  proof across a conflict-free integration of the main branch that touches no file of the
  PR, without a new review round (PAT-ADR-0021; `AcceptanceProofStore.carry`,
  `tooling/foundry/routing.py`; `git_diff` gains an optional `head`; a proof may carry an
  optional `carried_from` key). Any other case is refused and a full review stays required.
  Documentation status (R5): updated `docs/review-rounds.md` (new section),
  `skills/merge-pr/SKILL.md` step 2b, `skills/review-pr/SKILL.md`; the new CLI verb is
  documented there. `AGENTS.md`/`CLAUDE.md` unchanged: R9 does not contradict the new
  behaviour. Merge gate and escalation unchanged.
- PAT-172: the review that follows the correction of the non-blocking remarks of a
  validated round (every criterion `pass`, quality `mergeable`) is now partial on every
  pull request, not only on those of a companion Epic (PAT-ADR-0022). It stays full after
  a blocking round, when the change goes beyond correcting the remarks, and when the
  reviewer asks for it with a reason. Process contract and documentation only: no tooling,
  merge gate or proof format change, and no mechanical check tells a partial review from a
  full one. Documentation status (R5): updated `AGENTS.md`/`CLAUDE.md` R9 (b),
  `skills/merge-pr/SKILL.md`, `skills/review-pr/SKILL.md` and `docs/review-rounds.md`
  (new section); no CLI verb, configuration key or public constant changed, so none to
  document; PAT-ADR-0018's deferral writes unchanged.
- PAT-171: measurement of the PAT-ADR-0016 observation window for Haiku 5.5 (first ten issues
  delivered after the PAT-125 merge against the ten before it), recorded in
  `docs/qualification/pat-171-haiku-55-window.md` and linked from
  `docs/qualification/pat-125-haiku-55-promotion.md`. Read-only measurement from the local proof
  store (`routing review-rounds`, `routing escalation show`), `git log` and `gh pr view`; no
  code, model, effort, routing or configuration change, no model call. The regression trigger does not
  fire; decision recorded in the page: keep `haiku-5.5` / `medium`, window closed (taken by the
  coordinator under the maintainer's delegation, reversible by the maintainer). Documentation status (R5): new page and one link added; no CLI verb,
  option, configuration key or public constant changed.
- PAT-166: new skill `foundry:release` (`skills/release/SKILL.md`) and read-only helper
  `scripts/release_freeze_entry.py <version> [--date]`. The skill chains scope, release
  branch (both manifests, dated CHANGELOG section, release note and migration guide for a
  minor, freeze entry) and PR, then three separate STOPs (merge, tag, host update) that
  need the maintainer's explicit agreement, then the loaded-version check and the record on
  the release ticket. The helper prints the entry computed by `freeze_entry` of
  `tests/test_release_contract.py` and writes nothing. Documentation status (R5): updated
  the skills table of `README.md` (new skill row) and the skill page itself, which
  documents the helper; no existing CLI verb, option, configuration key or public
  constant changed. The host commands the skill lists were not run when it was written;
  the helper was run once from the repository root of the PAT-166 worktree with
  `python3 plugins/foundry/scripts/release_freeze_entry.py 1.3.0 --date 2026-10-15` and
  printed the entry already in `tests/fixtures/release-history.json`.
- PAT-165: Foundry can put a started or in-review issue back to the backlog:
  `edit transition <ISSUE-ID> backlog <expected-state>` (Linear), a new target of the
  guarded transition of PAT-ADR-0017 under PAT-ADR-0006, with its read rule decided by
  PAT-ADR-0024 (status `accepted`; it amends the
  "Projection des statuts" section of PAT-ADR-0006 for `backlog`). `<expected-state>` is mandatory
  and is `in-progress` or `review`, compared with the native state read; it is refused on
  a drift, from `done`, with a `state-done` receipt, with an Epic closure audit or with an
  invalid receipt chain, and an issue already at `backlog` converges without a write. No
  receipt is added (no `state-backlog` receipt); an issue at native `backlog` over a valid
  non-terminal chain now reads `backlog` / `aligned` instead of `disagreement`. Accepted
  risks (PAT-ADR-0024): a Foundry return, a hand move in Linear and an interrupted `issue
  start` from the backlog read alike; a pull request merged outside Foundry with no
  `state-done` receipt is not detected; `edit set-field <ISSUE-ID> State backlog` stays
  an unguarded grooming write. `issue start` on a returned issue rewrites the native
  State `in-progress` without a new receipt, so `issue open-pr` is allowed again. The
  linked PR is neither closed nor modified and the command names it. The other providers
  keep their behaviour for these transitions (YouTrack writes the State through its
  bounded `set_state`, GitHub Projects refuses `backlog` in its own `set_state`, DevHub
  keeps its own check). Documentation status (R5): updated `docs/linear-tracker.md`
  (new paragraph, one limit sentence corrected), `docs/tracker-contract.md` (new paragraph,
  Status projection row, known-limits sentence) and the status-projection evidence of the
  three providers in `docs/tracker-contract.v1.json`; new CLI target documented in the
  `foundry.edit` docstring; new constants `_BACKLOG_RETURN_PREDECESSORS`,
  `guarded_backlog_return_supported` and `backlog_return_predecessors` are covered by the
  `docs/linear-tracker.md` paragraph.
- PAT-63: the R1 guard (`hooks/guard_bash.py`) now names the real cause of a fail-closed
  denial. A malformed global registry (invalid JSON or encoding, non-object root, invalid
  `canonical_repo`, and now a provider or an entry that is not an object, which was silently
  treated as unregistered) raises `registry.RegistryFileError` (a `ValueError` subclass from
  `registry.load()`), and the denial says "registre global Foundry" with the registry path
  instead of "binding tracker Foundry invalide"; a broken repo binding keeps the old wording.
  Deduced from the code, pinned by a test: the guard path never reads `config.env`/
  `$FOUNDRY_CONFIG`, so a non-UTF-8 config file changes nothing; an unreadable registry (OSError) still fails open.
  Behaviour change: `registry.load()` now refuses a non-object provider or entry for every
  caller. Documentation status (R5): updated the `guard_bash.py` docstring, the hooks
  paragraph of `README.md` and AGENTS.md/CLAUDE.md R1 (identical); no CLI verb, option,
  configuration key or public constant changed.

## 1.3.0 — 2026-10-15

Minor release. Entries below change behaviour. New CLI surfaces: `routing
prepare-review` (PAT-174), `routing search-review` and `routing list-review`
(PAT-176), `routing postmerge-review prepare|read|record` (PAT-200, PAT-202) and
`edit reconcile-create` (PAT-197); it adds `scripts/check_model_rollback.py`
(required `--out`, PAT-17). `routing claim-review` gains `--host` and, for `--host codex`, route options
that are checked before any reservation (PAT-204); the `review-pr` and `merge-pr`
skills now reserve the diff with `routing claim-review` on both hosts before preparing
the reviewer packet. The routed read-only Claude Bash guard accepts the claimed readers
and the advisory reader and refuses any command containing CR or LF
(`hooks/guard_routed_readonly.py`). On Linear, issue creation admits only an exact or
qualified-rendering body, `edit create-issue` exits nonzero with typed partial-effect
details on standard error, a quota error on the creation request stays typed and one on
the read-back is reported as a partial effect (PAT-197); advisory comments
survive the ten-comment display window (PAT-200). The Tracker port gains
`reconcile_issue_creation`, which refuses by default, and the CodeHost pull request
model gains an optional `body` (PAT-197, PAT-174). No routing default, model or effort
changes (PAT-17). Release note: `docs/release-1.3.0.md`; upgrade guide:
`docs/migration-1.3.0.md`. This dated section is not a claim that 1.3.0 is already
tagged or installed. The entries below are those merged after `foundry-v1.2.0`, kept as
written.

- PAT-197: qualified issue-body rendering and typed partial creation effects
  (`linear.py::create_issue`, `_issue_creation_rendering`); explicit read-only
  `edit reconcile-create` validates an existing issue without recreation or overwrite
  (`write.reconcile_issue_creation`, `LinearTracker.reconcile_issue_creation`).
  R5: comparison bounds, effect vocabulary, portable port and CLI documented in
  `docs/linear-tracker.md`; intake caller updated in `skills/intake/SKILL.md`.

- PAT-204: `routing claim-review --host codex` validates the reviewer facade before
  a new reservation or interrupted-publication replay; route refusal leaves the
  review ledger unchanged (`routing.py::main`; `routing_facades.py::codex_review_preflight`).
  R5: host compatibility and route/profile inputs documented in `docs/model-routing.md`
  and the `review-pr` / `merge-pr` caller skills.

- PAT-202: replaces the advisory contract with observable logical Claude call and
  correlated hook evidence, complete publishable tracker reports and separate
  exact-readback confirmations (`postmerge_review.py::{native_evidence,record,append}`).
  New v2 terminals remain distinct from historical v1 receipts and restore report
  hashes without private packets (`advisory_schema.py`; `restituted_reports`).
  Every comment is locally bounded to 12,294 UTF-8 bytes including its prefix;
  the native maximum remains unknown (`MAX_COMMENT_BYTES`; PAT-202 dated probe).
  Codex reuses shared packet/profile/floor validation without a premerge claim
  (`routing_facades.py::codex_spawn_plan`). Preparation refuses implicit Claude
  plugin selection and unsafe reader paths before consumption, and unreadable
  packets stop without verdict (`advisory_plugin`, `prepare`, `record`).
  R5: descriptor/evidence, v2 vocabulary/confirmation/restitution, comment bound,
  Git prerequisites/attributes, plugin/path selection, Codex profile signals and
  action effect statuses updated in `docs/postmerge-review.md` and `review-pr`.

- PAT-200: adds advisory `routing postmerge-review prepare|read|record`, exact
  historical diff checks and append-only tracker attempts/reports, separate from
  original claims and delivery proofs. Default tracker support is Linear-only;
  advisory receipts survive its recent-comment display window (`linear.py::_to_issue`).
  Native evidence is structurally checked
  coordinator transcription, not direct host observation (`postmerge_review.py`).
  Claude uses logical `foundry:maigret` through the shared facade; receipts bind
  private packets by digest and comments use the normal write tier
  (`postmerge_review.py::{invocation,public_attempt,append}`).
  R5: CLI, receipt vocabulary, selected-plugin identity, advisory hash/reader,
  failure statuses, private report binding and role policy updated in
  `docs/postmerge-review.md` and the advisory branch of `review-pr`.

- PAT-17: consolidates existing model decisions for four roles on each host in
  `docs/model-decisions-2026-10-11.md`. Eight configuration rollback exercises pass
  on disposable policies through CLI resolution and constructed facade plans;
  native backend acceptance remains unknown. Adds an explicit Claude incumbent
  mapping and `scripts/check_model_rollback.py --out`, preserves historical reports,
  and records the observed PAT-164 refusal without changing routing defaults.

- PAT-176: the reviewer can independently search literal text and list regular
  tracked paths at its active claim's immutable HEAD through `routing search-review`
  and `routing list-review`. For premerge, the Claude Bash guard allows these claimed readers
  and the existing diff verifier. Query limits, evidence scope and the prompt-only
  Codex compatibility template are documented in `docs/reviewer-queries.md`.

- PAT-174: `routing prepare-review` generates a private bounded reviewer packet and
  send text with the four verifier coordinates, after checking the immutable PR
  base/head and active claim. Tool-read facts and unverified coordinator declarations
  are separate; no tracker/PR writes or host/model launches occur. `review-pr` and
  `merge-pr` use this command. The CodeHost PR model carries an optional description
  (`body`), read by GitHub `get_pr`. Options, limits and private outputs are documented
  in `docs/review-packet.md` (R5: updated); gates and claim recovery remain unchanged.

- PAT-194: documentation only; settles deferred PAT-193 remarks. Both README files
  record the observed 1.2.0 tag, installed versions and loaded Codex session version,
  with Claude's loaded session version unknown; sources are in
  `docs/qualification/pat-194-post-publication-observation.json`. The Release rule's
  version list and the `scripts/ci_plan.py` comment now include 1.2.0.
  `docs/linear-tracker.md` distinguishes the removed merge-to-Done automation from
  the unobserved configuration at the PAT-72/PAT-73 merges;
  `docs/linear-request-cost.md` explicitly marks PAT-87 and `close-epic --status`
  without after measurements. No tool behaviour or frozen release evidence changes.

## 1.2.0 — 2026-10-11

Minor release. Entries below change behaviour. `query issue` no longer reports a broken
ADR-to-issue link (Linear and GitHub Projects used to; YouTrack and DevHub never did):
it returns the plain ADR index, without the `adr_issue_unavailable` or `conflict` status
it used to project for such a link, and the ADR commands (`query adr`, `query adrs`,
`frame`, the ADR writes) still refuse it; the `note` field of its payload changes
accordingly (PAT-190). The Tracker port gains `list_adrs_without_issue_links`: an
adapter whose `list_adrs` validates ADR-to-issue links must override it (PAT-190). On
Linear, the read-only commands `query issue`, `query adrs`, `query backlog` and `query
profile` read each issue reference at most once per command, and `search` fetches every
listing page before normalizing any node, which changes the error reported when two
failures meet in one listing (PAT-168); a write or a nested opening inside a read scope
is refused with `LinearReadScopeError` (PAT-189). The Linear request counts are in
`docs/linear-request-cost.md`, with their conditions. Release note:
`docs/release-1.2.0.md`; upgrade guide: `docs/migration-1.2.0.md`. This dated section is
not a claim that 1.2.0 is already tagged or installed. The entries below are those
merged after `foundry-v1.1.1`, kept as written.

- PAT-192: documentation only, no behaviour of any tool changes; settles the
  non-blocking remarks deferred from the reviews of PAT-169 (3) and PAT-170 (4). In
  `docs/review-rounds.md`: the closing of the second window names the right actors
  (counted for the maintainer's decision, at a date the maintainer chooses after the
  merge of the tenth origin issue); the first window's "complete" says when it is
  judged; the state of the second window is rewritten as a state at `a75e4f7`, 9 of 10
  origin issues merged, PAT-189 delivered (it was recorded as not delivered), PAT-192
  not delivered until this pull request is merged. "counted" no longer carries two
  meanings there ("origin issues merged so far", "PAT-148 will be counted in full"), and
  the PAT-169 entry below now says "the 7 origin issues already merged" (the state when
  PAT-169 was written) instead of "already counted". In
  `docs/qualification/pat-134-subagent-cache-1h.md`: the raw replay file is said not to
  be versioned (the earlier claim that it carries host paths and session identifiers was
  false), the meaning of the session rank, the lower bound of the replay, and the cases
  where three designated sessions are not enough, including `roll_back` when no subagent
  lineage is observed at 1 hour. R5: the documented artifacts are those two pages; no
  CLI, option, key or constant changes. No remark is set aside.

- PAT-170: documentation only, no behaviour of any tool changes; records the measure
  made when the observation window of the 1-hour cache on the Sonnet 5.5 subagent
  profiles (PAT-134) reached its first bound, the tenth delivered issue, and the
  maintainer's decision. Only 2 main conversations qualified (one of them contributes a
  1-hour Sonnet 5.5 lineage), so `rollback_decision` returned `unknown` (fewer than 3
  sessions) and the change stays without conclusion; the indicative figures are recorded
  as such. The maintainer decided on 2026-10-11 that the 1-hour cache stays and extended
  the window to the 30-day bound, 2026-11-09; the measure is made again with the same
  rule when at least 3 sessions qualify. Threshold, selection rule and rollback
  procedure are unchanged. Written in `docs/qualification/pat-134-subagent-cache-1h.md`
  and `docs/model-routing.md`. R5: the documented artifacts are those two pages (the
  observation window of PAT-134); no CLI, option, key, constant or profile changes.

- PAT-189: settles the non-blocking remarks deferred from the reviews of PAT-167,
  PAT-168 and PAT-190. Behaviour: opening `LinearTracker.read_scope()` while it is
  already open is now refused with `LinearReadScopeError` (it used to replace the outer
  scope silently; no command nests it), and the refusal of a write inside a read scope
  raises that same class instead of a bare `RuntimeError` (still a `RuntimeError`, and
  deliberately not a `LinearTrackerError`: nothing was sent and the transport handlers
  must not apply). The early stop of `search` on a page that repeats an earlier issue
  now counts every node whose `identifier` key is present, whatever its type, provided
  it is hashable (a list or an object is not counted and fails afterwards as it did
  before, outside the early stop: `TypeError` on the normalized issue), where it counted
  strings only since PAT-168; the code before PAT-168 compared `Issue.id`
  (`raw["identifier"]`) after normalization, which refuses a node with no `identifier`
  key and accepts any other value, so a node without the key is still not counted.
  Checked by running the code at 8367180 on the same pages (7 and None repeated:
  `pagination_stalled` at the second request; key missing: `normalize` /
  `invalid_response`; `["x"]`: `TypeError`). No output of any command and no write path
  changes. Tests: fixed order of the errors of `search`, repeated non-string
  `identifier`, missing `identifier` key and non-hashable `identifier`, nested scope,
  second `search` in a scope, the reads of one transition, `query issue` end to end for
  an adapter that validates ADR links without overriding the port default, the GitHub
  Projects "before" measured on a second instance, and one conformance case (`query
  issue` with a disappeared ADR issue). Documentation (R5):
  `docs/linear-request-cost.md` (cost per ADR-issue pair established by the
  coordinator's per-operation trace of 2026-10-11, the coordinator's `query backlog`
  trace recorded as measured, transition reads pinned on the fake transport, statements
  dated "before PAT-168" and "before PAT-190", identity of the measured tooling with the
  code read, PAT-190 table row), `docs/linear-tracker.md` (error order of `search` with
  the read scope, nested scope, second `search`, `LinearReadScopeError`),
  `docs/ghprojects-tracker.md` (exact list of reads of the ADR index of `query issue`),
  `docs/tracker-conformance.md` (count of 132 tests). The history note that
  `docs/linear-request-cost.md` carried about the PAT-100 paragraph now lives in the
  PAT-167 entry. The time of the first measure had been corrected by PAT-168 already.
  `query milestones`, which still pays the full replay of closed Epics, is out of scope
  and goes through intake.

- PAT-169: documentation only, no behaviour of any tool changes; records the first
  observation window of rule R9 and the maintainer's decision. `docs/review-rounds.md`
  gets the count per issue with its PR sources (39 rounds over 10 origin issues, 3.9;
  4.0 on the 8 issues with lines in the format; PAT-143 is a lower bound), the result
  "complete", the decision of 2026-10-11 (keep the rule as amended by PAT-144, open a
  second window from the merge of PAT-144, same 3.2 threshold and triggers; first taken
  on the figures 4.0 and 3.0, the latter wrongly including PAT-137 and PAT-147, then
  confirmed by the maintainer the same day on the corrected 3.9 and 3.5; a reservation
  written after that confirmation: 3.5 charges PAT-148 in full although it also holds
  remarks of PAT-137 and PAT-147, 2.5 without it), and the definition of the second
  window with the 7 origin issues already merged. R5: the documented artifact is that
  page; `AGENTS.md`, `CLAUDE.md` and `skills/merge-pr` state neither the window nor its
  status, so they are unchanged; no CLI, option, key or constant changes.

- PAT-187: documentation only, no behaviour of any tool changes; settles the
  non-blocking remarks deferred from the reviews of PAT-163 and PAT-164.
  `docs/linear-tracker.md` limits "the same divergence" to the 28 issues completed on
  2026-10-04, names the four already Done earlier and adds an explicit Unknown on the
  GitHub integration. Both READMEs now carry the same Codex status of 1.1.1 (the refusal
  as the service's message, the model source, a separate observation instead of
  "observed again"); `docs/model-routing.md` states the interval between the two cache
  reads, names `~/.codex/config.toml`, and points the tier table at the operational
  note. The PAT-164 entry below now says "reported" instead of "loads". Set aside: a
  minimum Codex version guard is a new idea, left to `foundry:intake`. R5: the
  documented artifacts are the pages above; no CLI, option, key or constant changes.

- PAT-190: `query issue` returns the ADR index without validating the ADR-to-issue
  links, on every tracker (Linear and GitHub Projects used to validate them; YouTrack
  and DevHub never did). The ADR commands (`query adr`, `query adrs`, `frame`, the ADR
  writes) keep the validation and still refuse a broken link. Behaviour changes: a
  broken link (missing reciprocal comment, cited issue absent or inaccessible) is no
  longer projected as `adr_issue_unavailable` or `conflict` by `query issue`, which
  returns the plain index; a transport, binding or payload failure while reading a
  cited issue no longer fails `query issue`. The reads its index still makes, and whose
  failure still fails it, are per tracker: on Linear, the listing of ADR documents and
  nothing else; on GitHub Projects, the repository Issue listing (one request per page
  of 100), the Project item listing (one GraphQL request per page) and, for each ADR,
  its support Issue read twice (once to identify the corpus, once more by the
  head-commitment check) and its comment history (one request per page of 100); a
  support recognized in the Project but not in the repository listing (absent from it,
  or present there without the reserved label and without a head) is read once before
  the conflict is raised (corrected by PAT-189: this list used to read as exhaustive).
  The port is explicit: `Tracker.list_adrs_without_issue_links` (default: `list_adrs`),
  overridden by the Linear and GitHub Projects adapters, the two whose `list_adrs`
  validates these links. The `note` of the `query issue` payload no longer announces the
  `adr_issue_unavailable` status and says that the ADR commands of a tracker that has
  such links validate them; every other field is identical for a valid graph. The
  skills `start-issue`, `resume-issue` and `merge-pr` no longer list that shape for
  `adrs` in this payload. Version chain, witness, history, status and supersession
  checks stay in `query issue`, and so does, on Linear, the metadata check of each
  stored relation list (an identifier repeated verbatim or malformed is a provider
  error there, not a link the ADR commands alone detect). Fake
  transport: a plain Linear `query issue` goes from 6 to 3 requests, GitHub Projects
  from 17 to 15 commands; measured on the real Linear project in
  `docs/linear-request-cost.md`. R5: documented in `docs/linear-tracker.md`,
  `docs/ghprojects-tracker.md` and `docs/linear-request-cost.md`; no CLI option or
  configuration key added.

- PAT-168: fewer Linear requests for the read-only commands `query issue`, `query adrs`,
  `query backlog` and `query profile`, without changing any output or any write path. A
  read scope, open only during one of those commands and dropped at its end, reads each
  issue reference at most once (a native id and an identifier are two references, so an
  issue resolved by both in `query issue` or `query adrs` still costs two reads), and
  `search` seeds it with its listing so a closed Epic's replay is served from that
  listing. Measured on the real project (outputs byte-identical): `query backlog` and an
  intake profile page 171 -> 2, closed Epic PAT-51 262 -> 132, plain issue 56 -> 48;
  `docs/linear-request-cost.md` records them. Behaviour change of `search`, for every
  caller: it now fetches every listing page before normalizing any node. A successful
  listing, a listing that fails in only one way and a listing whose first response
  announces no next page report exactly what they reported; a re-served page is still
  stopped at the second listing request. Only when two failures meet in a longer listing
  does the reported error differ: a failure met while fetching (transport, invalid
  response, missing cursor, page limit, or an issue of a later page repeating an earlier
  page) now surfaces before a failure met while normalizing a node already fetched. A
  listing that fails while normalizing now requests every page before reporting its
  error. With the read scope open, a listing whose only failure came from an individual
  read, by the closed-Epic replay, of a node present in the listing (transport, quota)
  no longer fails, because the listing seeded that read and it is not sent (corrected by
  PAT-189: `docs/linear-tracker.md` said "with or without the layer", and this entry did
  not state the exception). Inside one command, two snapshots no longer re-read a node
  they share. ADR writes keep exactly their reads. `docs/linear-tracker.md` states what
  is shared inside a command and what is not.

- PAT-167: documentation only, no behaviour of any tool changes. New page
  `docs/linear-request-cost.md` records, as measured on 2026-10-11 on the real project
  (174 issues, 45 ADRs, Foundry 1.1.1), the number of Linear requests of `query issue`,
  `query adrs`, `query backlog`, `query profile intake`, `close-epic --status` and
  `issue start`, the reads that the code repeats inside one command, and the candidate
  reductions for the follow-up ticket PAT-168, with the sentences that may constrain
  each. `docs/linear-tracker.md` points to it from "Request counter (PAT-145)", and its
  PAT-100 paragraph is corrected: `close-epic --status` reads the Epic twice (two
  requests measured, both `issue.read` by code reading), where it said once. Nothing
  is reduced.

- PAT-164: documentation only, no behaviour of any tool changes. Records, as observed on
  2026-10-11, that a new Codex session reported the 1.1.1 plugin root under Codex CLI
  0.162.1, and that the earlier refusal of `gpt-6.1-sol` (status 400, "not supported
  when using Codex with a ChatGPT account") happened under the outdated CLI 0.155.1,
  whose cached model list did not contain it. `docs/model-routing.md` gets an
  operational note; both READMEs update their Codex status. No routing change.

- PAT-163: documentation only, no behaviour of any tool changes. `docs/linear-tracker.md`
  records why PAT-72 and PAT-73 read `done` with a native In Progress (both merged
  before PAT-56 added the native State projection; until then only receipts were
  written), the existing `issue merge <ISSUE-ID> <PR-NUMBER>` replay that realigned them
  on 2026-10-11, and why a merge that completes today cannot leave that state.

- PAT-150: wording and one code comment only, no behaviour of any tool changes; settles
  the non-blocking remarks deferred from PAT-149. Both READMEs record the 1.1.1 tag and
  per-host statuses instead of pointing at the tracker. `plugins/foundry/README.md` lists
  1.1.1 among the releases the CI release rule is derived from (also the comment in
  `scripts/ci_plan.py`) and stops calling the 1.0.0 guide "current". The root README
  stops calling foundry "1.0.0 (published)" and orders its status paragraphs
  chronologically.

## 1.1.1 — 2026-10-11

Patch release. Entries below change behaviour for every repository that uses the
plugin: after a fully validated review round, the Foundry skills write to the tracker
without asking, in four bounded cases (rule R9 (b), PAT-ADR-0018; entries PAT-136,
PAT-139 and PAT-144). Two additions are also in this section: the option
`query profile <name> --all-pages` and the variable `FOUNDRY_REQUEST_COUNT` (PAT-145).
No release note and no migration guide: a patch requires neither
(`release_requirements` in `tests/test_release_contract.py`). This dated section is not
a claim that 1.1.1 is already tagged or installed. The entries below are those merged
after `foundry-v1.1.0`, kept as written.

- PAT-148: wording and tests only, no behaviour of any tool changes; settles the
  non-blocking remarks deferred from PAT-137, PAT-147 and PAT-146. Errata page: `screen`
  reaches `_bundle` through `local_attempt` only (read in `Runner.screen` and
  `Runner.compare`), the twenty-minute bound kills the `claude -p` parent, counts removed.
  `intake` skill: the removal command refuses an empty argument, and the skill says what
  to do when `--all-pages` names a directory or the removal fails; a failure of
  `--all-pages` after the directory is created and before any page is written, whose
  message does not name the directory (tracker error, quota), still leaves an empty
  temporary directory (not changed: that needs a change of `--all-pages`).
  `test_release_contract.py`: tests of the crossed paths of the frozen-release check,
  exact fault lists, a synthetic frozen map, and a shape check of
  `FROZEN_DOCUMENT_EXCEPTIONS`; the reason of the absent
  benchmark artifact is now worded as deduced. `plugins/foundry/README.md` states the
  freeze rule once.
  Documentation status (R5): `docs/release-1.1.0-errata.md`, `docs/query-profiles.md`,
  `skills/intake` and `plugins/foundry/README.md` updated; no CLI verb, option or
  constant changed.

- PAT-146: tests only, no behaviour of any tool changes. A variant of the frozen-release
  check now runs in CI: it passes with three named exceptions (`docs/migration-0.7.0.md`
  and `docs/migration-0.8.0.md`, digests pinned in the test; and the absent
  `benchmarks/foundry-46/report-v2/pilot-20260825/results-v2.json`), and fails on a
  stale exception or on a rewrite of any other frozen document. The strict test stays
  marked `historical_fixture` and excluded from CI. The cause of the three faults is
  deduced, not observed: the public snapshot omits unsanitized historical documents
  (FOUNDRY-ADR-0024). `tests/fixtures/release-history.json` is unchanged. Consequence
  for the next release: the CI test also fails while a published CHANGELOG section has
  no entry in that fixture (`_assert_frozen_coverage`), so the pull request that
  publishes a version must carry its entry.
  Documentation status (R5): `docs/release-1.1.0-errata.md` updated; no CLI verb, option
  or constant changed.

- PAT-144: wording, one docstring and presence tests only, no behaviour of any tool
  changes; the texts of rule R9 follow the maintainer's decisions of 2026-10-11 and the
  amended PAT-ADR-0018. A fourth write is pre-authorized: attaching to the companion Epic
  a follow-up issue whose creation succeeded but whose parent link was not set
  (`edit link <FOLLOW-UP-ID> subtask-of <COMPANION-EPIC-ID>`); the conformance tests
  (`tests/test_tracker_conformance_v1.py`) do not cover it. One follow-up issue per
  companion Epic, never added to once started or closed (a new one is created). On the PR
  of an issue of a companion Epic, the review after the correction of non-blocking remarks
  covers what changed since the previous round; after a blocking round it stays full; the
  merge gate is not modified and checks none of this. An issue whose direct parent is not
  an Epic gets no deferral. The scope of (a), (b) and (c) is explained; the classing of a
  remark as deferrable is stated in its two steps, the second an accepted risk; the
  return trigger of `docs/review-rounds.md` has one unit (rounds per origin issue), and
  its baseline is ten PRs of ten distinct issues. The module docstring of
  `foundry/edit.py` names the exception of the four writes and says it does not cover the
  writes of `intake`. Earlier entries below keep describing the state at their date.
  Documentation status (R5): `AGENTS.md` / `CLAUDE.md` R9 (identical),
  `skills/merge-pr`, `skills/intake`, `docs/review-rounds.md` and the docstring of
  `foundry/edit.py` updated; no CLI verb, option or constant changed.

- PAT-147: the `intake` skill now asks to remove the `--all-pages` directory once the pages
  are read, including when the intake stops midway; `docs/query-profiles.md` states that
  cleaning it is the caller's job. Skill text, documentation and assertions added to an
  existing test only; the code of `--all-pages` is unchanged.
- PAT-137: 1.1.0 release paperwork, no change under `tooling/`; the frozen 1.1.0 pages
  (`docs/release-1.1.0.md`, `docs/migration-1.1.0.md`, the 1.1.0 section below) are
  unchanged. `tests/fixtures/release-history.json` freezes 1.1.0 from the tree of the tag
  `foundry-v1.1.0`; the new `docs/release-1.1.0-errata.md` (not frozen, and ignored by
  the frozen-release test through its `-errata.md` suffix) records the corrections of the
  launcher `--dry-run` sentence, of the twenty-minute bound and of three unwrapped lines,
  and the return to 1.0.0 as not tried. Both READMEs now say what is observed per host.
  The `--dry-run` sentence of `docs/qualification/pat-125-haiku-55-promotion.md` is
  corrected against `claude_profile_trial.py`.

- PAT-145: fewer Linear requests for an intake replay. `FOUNDRY_REQUEST_COUNT` (`stderr`
  or a file path) reports the Linear HTTP attempts of a process, retries included, on
  exit (`trackers/linear.py`). `query profile <name> --all-pages <DIR>` writes every page
  from one computation of the snapshot, byte-identical to `query profile <name> <n>`, and
  prints a small manifest; the directory is checked before the snapshot is computed; the
  `intake` skill uses it. No cache between invocations; real request figures are not
  measured in this entry.
- PAT-138: non-blocking remarks of PAT-136 and PAT-139, wording and tests only. The one
  change under `tooling/` is the module docstring of `foundry/edit.py`, which now names
  the exception of the three writes pre-authorized by R9 (b). Rule R9 (b) and the
  `merge-pr` skill no longer present the list of corrections before the merge as
  exhaustive; `merge-pr` names no issue of this repository, names the existing commands
  (`query backlog`, `query issue`) to look for a `Nits <ORIGIN-EPIC-ID>` Epic and to read
  the tracker back, and says that after a validated round the coordinator, not the
  reviewer, classes the remarks. The `resume-issue` packet of a delegated correction
  carries rule (c). `docs/review-rounds.md` is corrected for `ghprojects` (the first
  `create_issue` call itself raises `create_effect_unknown` when the read of the
  candidates that follows a failed issue POST succeeds and finds none, and the error of
  that read comes out when it fails too; `transport_failed` is also the default reason),
  for the seams of the conformance tests, and says that `create_issue` with a parent
  makes two writes on the issue itself on YouTrack (a `Milestone` value may be posted to
  the project's milestone bundle before them). Documentation status (R5):
  `docs/review-rounds.md`, `AGENTS.md` / `CLAUDE.md` R9, the skills `merge-pr`,
  `resume-issue`, `review-pr` and the docstring of `foundry/edit.py` updated; no CLI verb,
  option or constant changed.

- PAT-142: CI speed, no change under `tooling/`. The `foundry` job runs the public suite
  in parallel (`pytest -n auto`, `pytest-xdist` installed in the job); a new `plan` job
  runs `scripts/ci_plan.py`, which skips the whole `foundry` job (check `skipped`) when a
  pull request changes only `plugins/ship-ios/` (only when `plan` says `false`), and runs
  everything on a push to `main`, on a release change (a plugin manifest or marketplace
  catalogue is touched), on any doubt, and when `plan` itself fails (`!cancelled()` and
  `!= 'false'` in the condition). `ship-ios` moves from `macos-14` to `ubuntu-24.04` (`ruby` installed if the image
  lacks it). `ship-ios` and `catalogue` still always run. The expected runner of
  `ship-ios` in `tests/test_youtrack_smoke.py` follows. Observed in CI run 38095058779
  (PR head `d640c1a`): `foundry` 5 min 49 (previous push to `main`: 11 min 50), Ruby
  already on the image, 35 `ship-ios` tests pass on Ubuntu; the runner core count and the
  Ruby version are not shown by the log. Not shown by any run (derived from GitHub
  documentation): a failed `plan` makes `foundry` run; the fork pull request case. Also
  fixes the flaky `test_B1_an_interrupted_execution_kills_the_whole_process_group` when
  started from a background job (test fixture only). Documented in the Foundry README.

- PAT-139 (PAT-ADR-0018): BEHAVIOUR CHANGE for every repository that uses the plugin.
  Rule R9 (b) of `AGENTS.md` / `CLAUDE.md` and the `merge-pr`, `intake` and `open-pr`
  skills now send the non-blocking review remarks deferred from the issues of an Epic to
  a companion Epic named `Nits <ORIGIN-EPIC-ID>`, which has no tracker link with the
  origin Epic (no parent, no dependency, no relation), instead of a follow-up issue
  under the origin Epic. Foundry now makes three tracker writes without asking, inside
  and outside an Epic campaign: creating the companion Epic the first time, creating a
  follow-up issue in it (per batch or per theme, never one per PR), and adding deferred
  remarks to such an issue; the coordinator reports them in the PR description. Nothing
  else is pre-authorized: the companion Epic and its issues are neither prioritized nor
  started without the maintainer. The special case "inside an Epic campaign the
  maintainer creates or approves the follow-up issue" is removed. No second-level
  deferral: non-blocking remarks on a PR of a companion Epic are corrected before the
  merge and fully re-reviewed. The origin Epic of an issue is its direct parent Epic. If
  the tracker durably refuses one of the three writes, no substitute write is made, the
  remarks are corrected before the merge and fully re-reviewed, and the refusal is
  reported in the PR description; on a transient failure (quota, network) the write is
  retried and the merge waits for it, after a read of the tracker when its effect is
  unknown; a retry that the adapter itself refuses is treated as a durable refusal (same
  handling). An issue without an origin Epic gets no deferral: its remarks are corrected
  before the merge and fully re-reviewed. Not mechanical: no gate checks the companion
  Epic. Not coded yet: inside a campaign the authorization has no effect yet, because
  three things are missing (a channel that carries the remarks to the campaign, the code
  that creates the companion Epic and its issues, campaigns usable on the repository's
  tracker); left to a later issue, PAT-141 ([review rounds](docs/review-rounds.md)). Six
  tracker conformance cases added, two per provider on a double of the provider
  (YouTrack: `_req` replaced; Linear: `transport=`; GitHub Projects: thirteen methods of
  the adapter replaced, twelve private and the public `verify_project_identity`, not the
  `runner=` seam, except for the case of a Project without `Epic` option, which goes
  through `runner=`): the three writes, and a project where the `Epic` type is
  unavailable. Text and tests only; no change under `tooling/`.
- PAT-136: new process rule R9 in `AGENTS.md` / `CLAUDE.md` (author checklist for
  documentation sentences, grouped deferral of non-blocking review remarks to one
  follow-up issue per batch, minimal correction commits), written into the
  `start-issue`, `resume-issue`, `merge-pr`, `review-pr`, `open-pr` and `intake` skills and the
  `maigret` output format (optional "fix before merge" mark on a nit; verdict line and
  JSON object unchanged). Baseline, observation window and return triggers:
  [review rounds](docs/review-rounds.md). Behaviour change for every repository
  that uses the plugin (maintainer decision of 2026-10-10): after a fully validated
  review round, non-blocking remarks are deferred to a batch follow-up issue, and outside
  an Epic campaign the two intake writes for it (follow-up issue and its remarks) are made
  without asking (where the remarks go and which writes are made without asking are
  replaced by the PAT-139 entry above). Text and
  tests only; no change under `tooling/`.

## 1.1.0 — 2026-10-10

Prepared release candidate. This preparation (PAT-135) changes only version metadata
and documentation; the functional changes of 1.1.0 are the entries listed below. The
tag, an official update and the loaded-version readbacks remain pending. See
[release notes](docs/release-1.1.0.md), which open with the breaking changes, and
[upgrade and rollback](docs/migration-1.1.0.md). This dated source entry is not a
claim that 1.1.0 is already tagged or installed. The entries below are those merged
after `foundry-v1.0.0`, kept as written.

- Fixed (PAT-134; second review; tools, tests and documentation, no profile, routing, model, effort, setting or configuration key changes; recorded PAT-125, PAT-132, PAT-133 and PAT-134 results untouched): the documented rollback (emptying `CLAUDE_CACHE_TTL_1H_PINS`) made `foundry.cache_ttl_replay` and `foundry.claude_profile_trial` raise `IndexError` at import, which broke both command lines (PAT-132 / PAT-133 replay and PAT-125 trial mode included) and the collection of four test modules. Both derivations now tolerate an empty tuple: `ROLLBACK_MODEL` and `CACHE_TTL_POLICY` are then `None`, `rollback_decision` returns `unknown` with the new reason `no_model_carries_the_field`, and `--cache-ttl-trial` refuses (exit 2) creating nothing under the work directory; a test performs the first two rollback steps on a copy and imports both modules in a fresh interpreter. The rollback procedure in `docs/qualification/pat-134-subagent-cache-1h.md` now lists every step, file and test, as found by performing it on a copy of the repository and running the CI commands there. Statement of the threshold corrected: it is on the smaller of the two bounds of `usd.entry_reads_not_expired.delta_usd_exact`, in practice `favourable`, not `prudent` (which is the bound most favourable to keeping 1 hour); that quantity is more severe than `delta_usd`, not the most severe reading. Two remaining biases towards `keep` are named as limits of the decision (whole-read repricing on an in-lineage expiry despite a prefix shared with a sibling subagent; the `favourable` gap is not a rigorous lower bound), which a threshold without margin lets flip a marginal case. Also stated: the assumed asymmetry (no Sonnet 5.5 lineage at 1 hour gives `roll_back` even when no Sonnet 5.5 subagent ran), the Python call, keys and `reason` codes of `rollback_decision`, and `log_sha256` as a deviation from the FOUNDRY-ADR-0015 field list for the maintainer to acknowledge. Removed: a `below_minimum` state `cache_ttl_verdict` tested and nothing produced. The decision rule itself is unchanged.
- Added/fixed (PAT-134; offline tools, tests and documentation): the native cache-class trial result of 2026-10-10 is recorded as written in `docs/qualification/pat-134-native-trial.json` (verdict `conforming`: one Sonnet 5.5 read-only subagent 4,768 tokens in the 1-hour class and none in the 5-minute class; Opus 5.5 control 5,301 in the 5-minute class only; one run of two subagents, host 2.1.294; usage credits and quota unknown). Fixed: `--dry-run` of `foundry.claude_profile_trial` (`--cache-ttl-trial` and the PAT-125 default mode, which had the same defect) created the work directory and so made the next real run on that path refuse; it now creates nothing under the work directory. New `foundry.cache_ttl_replay.rollback_decision` (constants `ROLLBACK_MIN_SESSIONS`, `ROLLBACK_MODEL`, derived from `CLAUDE_CACHE_TTL_1H_PINS` through the new `CLAUDE_CACHE_TTL_1H_MODELS`) reads the PAT-134 rollback rule from a replay output: it keeps 1 hour only if the smaller bound of the new `usd.entry_reads_not_expired.delta_usd_exact` (simulated 5-minute cost minus real 1-hour cost, first-request entry reads counted not expired, unrounded; subagent_1h group only) is strictly positive, and is `unknown` when fewer than 3 sessions contribute a priced Sonnet 5.5 lineage observed at 1 hour, such a lineage is unpriced, or an unmodified profile appears at 1 hour; no replay rule changed. A refusal after policy resolution no longer leaves the work directory of the trial created; an ambiguous child request makes the trial's cache class `unknown`. Not observed, no guard: a host older than 2.1.248 might reject a profile carrying the field, which would break every Sonnet 5.5 launch there (unknown); the page `docs/qualification/pat-134-subagent-cache-1h.md` states it and declares the incident.
- Changed (PAT-134; separable commit of the branch; native trial of 2026-10-10 observed the 1-hour class on the modified profile, `docs/qualification/pat-134-native-trial.json`): the ten Sonnet 5.5 versioned profiles (`routed-{readonly,worker}-{low,medium,high,xhigh,max}-sonnet-5.5`) carry `experimental: {cacheTtl: 1h}` (nested map; per the Anthropic documentation read on 2026-10-09, Claude Code 2.1.248 or later, ignored under usage credits; not re-verified, no guard added). New public constant `CLAUDE_CACHE_TTL_1H_PINS` in `routing_facades.py`; `claude_pin_profile_text()` renders exactly this block for these pins and an `experimental` block in a generic template is an invalid template; `claude_invocation_binding()` refuses any divergent profile (field missing, on another pin, another value or key). No other profile, routing, model, effort, setting or configuration key changes. R7 of `AGENTS.md` and `CLAUDE.md` (byte-identical) states it. No gain is announced; effect on the subscription quota: unknown. Observation window and rollback: `docs/qualification/pat-134-subagent-cache-1h.md`.
- Added (PAT-134; tools, tests and documentation only in this part: no profile, routing, model, effort, setting or configuration key changes; no cloud call from the tests; PAT-125, PAT-132 and PAT-133 recorded results untouched): `foundry.claude_profile_trial --cache-ttl-trial`, a separate bounded native trial (one `claude -p` parent, two subagents one after the other: a Sonnet 5.5 read-only profile and an Opus 5.5 worker control; requested / transmitted / observed kept apart; cache-creation tokens per 1-hour / 5-minute class read from the host session logs within FOUNDRY-ADR-0015, fields beyond it named; `conforming` only on 1-hour-only for the Sonnet subagent and 5-minute-only for the control, anything unobserved `unknown`; refuses an existing work directory or result file; result schema `foundry.pat134-cache-ttl-trial.v1`), and `foundry.cache_ttl_replay --subagent-1h-to-5m` (opt-in, with `--host-session`; a subagent lineage whose writes were all observed at 1 hour is replayed towards 5 minutes with the main-conversation rule, as the group `subagent_1h`; default outputs unchanged). The observation window, replay selection, rollback threshold (on the smaller of the two bounds, in practice `favourable`, not the `prudent` one) and exact rollback are written before adoption in `docs/qualification/pat-134-subagent-cache-1h.md`; behaviour on a host older than 2.1.248 and under usage credits is stated as documented / observed / unknown, with no guard. No gain is announced; effect on the subscription quota: unknown. Documentation status (R5): `docs/model-routing.md` and that page.
- Added (PAT-133; offline read-only analysis and tests only, no `claude`, no cloud arm, no model, no `lms`, no network call; no setting, agent profile, routing or launcher change; PAT-132 results and aggregate file untouched): extends `foundry.cache_ttl_replay` to INTERACTIVE host sessions, both directions of the prompt-cache duration. New options `--host-session MAIN_LOG` (repeatable; the subagent logs are the `*.jsonl` under `<session>/subagents/`; replaces `--ledger` and `--session-logs-dir`, no campaign ledger) and `--until ISO_INSTANT` (with `--host-session` only; ignores every later record so a log still being written replays reproducibly); output schema `foundry.cache-ttl-replay.interactive.v1`; exit codes 0 or 2 and the PAT-132 mode, errors and outputs unchanged. Rules written in the docstring and the results document before any figure: direction A (main conversation, observed 1 hour to simulated 5 minutes: the PAT-132 in-session rule per model-alias lineage; a model switch opens a lineage, a compaction is not detected) and direction B (subagents, observed 5 minutes to simulated 1 hour: an expiry re-write is recognised from counters and gaps only, prudent bound = certain expiry and only the prefix part the reference request had read, favourable bound = possible expiry and the whole prefix cached before the gap, gaps over 3,600 s expire under both). Reader per FOUNDRY-ADR-0015: counters, alias, timestamp; fields read beyond its list named (`type`, `message.id`); provenance `host_reported` / `pricing_derived` / `unavailable`; an unpriced lineage (day before the grid, alias without entry, Haiku 5.5) keeps its alias and leaves both sides of every USD comparison; the logical role is not derivable and is not reported. Reader repairs declared in the results document (made after a first run refused most real logs): split on `\n` only, streamed output counts, zeroed copies, ambiguous write classes, last partial line with `--until`. Result on 3 real sessions (1 priced, the grid starts 2026-10-05): main 1h to 5m is a net loss under both bounds (+119.6% to +273.7% list cost, Opus 5.5, 1,368 requests); subagents 5m to 1h is a net gain overall (-3.7% to -6.7%, 4,207 requests) but a net gain for Sonnet 5.5 (-28.8% to -33.1%) and a net loss for Opus 5.5 (+8.0% to +10.2%); nothing is concluded about the subscription quota (effect unknown), no generalisation beyond the sessions replayed, and which change would be justified (per agent profile, Sonnet only, by a change ticket with a native trial) is stated, not done. Review corrections (2026-10-10, declared in the results document, earlier values kept): `requests_found_expired` no longer counts first requests (new key `first_requests_found_expired`), `--until` now freezes the subagent-log count and lists a log without request, robustness keys added, both bounds stated as assuming a context that only grows. Docs: `docs/qualification/pat-133-cache-ttl-interactive-v1.md` and the aggregate file `pat-133-cache-ttl-interactive-v1.json`, section added to `pat-19-launcher-v1.md`. R5 status: new options, constants, result keys and unavailability codes defined in the results document; no CLI verb of `foundry_cli.py`, no routing table, no configuration key changed; the FOUNDRY-123 detector is not shipped, so this status is asserted here and checked in review.
- Added (PAT-132; offline read-only analysis and tests only, no `claude`, no cloud arm, no model, no `lms`, no network call, no campaign mode run by the diff; frozen v1 to v5 protocols, configs, results and reports untouched, no verdict recomputed; no launcher, routing or cache setting changed): replay of the PAT-19 v4 and v5 host session logs (kept off the repository) with the 1-hour prompt cache as recorded against a simulated 5-minute cache. New module `foundry.cache_ttl_replay` (`python3 -m foundry.cache_ttl_replay`: `--ledger` repeatable, `--session-logs-dir`, `--grid`, `--out`; exit 0, or 2 when an input is refused); the simulation rule (a request whose gap to the previous request of its session exceeds 300 s finds the cache expired and rewrites what it read at the 5-minute write price) is written in the docstring and the results document before any figure and gives a prudent and a favourable reading of the gap, never one figure; the reader (FOUNDRY-ADR-0015) extracts only token counters, model alias, timestamps and the message id to count a request once; unusable sessions are listed and excluded from both sides. Result as it came out after the correction below: 104 of 104 cloud sessions replayed, catalogue list cost 25.06 USD with 1-hour writes against 20.92 (prudent) and 19.87 (favourable) with a simulated 5-minute cache, a net gain under both readings (one in-session request over 5 minutes under the prudent one, none under the favourable one; one session undecidable between the bounds); first figures before the correction (19.94 and 19.67) are kept in the aggregate file as `*_before_correction`. Correction of 2026-10-09, made after the first figures were seen (found by the coordinator before review): the first request of a session can read a cache entry written by an earlier session (102 of 104 sessions, 340,392 tokens), which the rule had not priced; prudent counts all of them as expired, favourable keeps the read when the nearest earlier session of the same campaign and model ended at most 300 s before (an assumption, the writing session is not observable); second correction after the independent review: prudent reference of the second request is now the record preceding the first assistant record (104 gaps changed by 1.4 to 15.2 s, no figure moves), a request without cache read or write no longer refreshes the entry (0 such requests), sessions with several model aliases are excluded (none), and the 1.056 USD between the bounds split into 0.787 (first-request reads) and 0.270 (one reviewer request at 388.9 s); nothing is concluded about the subscription quota (effect unknown), and the way to transmit the setting (settings file against the closed child-environment allow-list of AGENTS.md R6) is left open for a change ticket. Docs: `docs/qualification/pat-19-cache-ttl-replay-v1.md` and the aggregate file `pat-19-cache-ttl-replay-v1.json`, section added to `pat-19-launcher-v1.md`. R5 status: new module entry point, options, exit codes, vocabularies and result codes documented there; no `foundry_cli.py` verb or option, routing table or frozen protocol changed; FOUNDRY-123 detector not shipped, status asserted here and checked in review.
- Added/changed (PAT-131, PAT-ADR-0017; Linear only; offline tests on the existing fakes, no tracker write and no network call run by the diff; PAT-ADR-0006 vocabulary unchanged: bounded detection, never CAS, residual S1→S2 risk named). (1) Abandon: `edit transition <ISSUE-ID> dropped <expected-state>` writes only the native State under S1-S4 from `backlog`, `ready`, `blocked`, `in-progress` or `review`; the expected predecessor is mandatory (refused before any provider read) and compared with the native state; refused from native `done`, with a `state-done` receipt, with an Epic closure audit on the issue or with an invalid receipt chain; a replay on an already dropped issue converges without a write, a third state fails closed; no receipt is written, deleted or rewritten; a linked PR is not closed and the output names it. `edit set-field <ISSUE-ID> State dropped` now takes that same guarded path (BEHAVIOUR CHANGE: it was an unguarded grooming write; it is now refused from `done` and cannot be combined with another field). New capability flag `Tracker.guarded_abandon_supported` (`True` on Linear only). (2) Read: a natively cancelled issue whose append-only projection is valid and non-terminal reads `state=dropped`, `normalized_state=dropped`, `projection_status=aligned` instead of a strict-read conflict / observed `disagreement`; with a `done` receipt the conflict is unchanged; the tolerance covers those receipts only: a never-started cancelled issue reads as before (`dropped` / `native-only` with no receipt, strict and observation reads; with advisory `cockpit-evidence` receipts and no lifecycle state receipt, a strict-read conflict and observed `dropped` / `native-only`, both as at `ff9a53a`). BEHAVIOUR CHANGE: a lifecycle write (start, review, done) on a natively cancelled issue is now refused first, with `Linear issue natively dropped; lifecycle write refused`, before any receipt is appended. For a never-started cancelled issue, compared on the fake Linear transport with `ff9a53a`: starting it or sending it to review was already refused there (`Linear native state changed outside lifecycle`), but only after the receipt comment had been appended, which left the issue a strict-read conflict and an observed `in-progress` or `review` / `disagreement`; it is now refused with no write and the issue still reads `dropped` / `native-only`. `done`, an AC proof and an AC override were already refused there before any write; `done` now gets the new message, the other two keep theirs. The same comparison against the real Linear API: unknown, not run. (3) Closure: `issue close-epic <EPIC-ID> --human-verdict=accepted --accept-dropped=ID[,ID...]` closes an Epic whose required graph has abandoned nodes; the named set must be exactly the set of dropped nodes of that graph; a dropped node is never counted as accepted and the prerequisites reached only through it are not required; at least one direct child must be `done`; without the flag Linear keeps its refusal, whose diagnostic now says "abandonné (dropped), acceptable nominativement" and prints the exact command, combined with `--accept-override` when both apply. No receipt field is added: the dropped node stays in the bound graph (id, version, state), so the audit id derives from the set, exact replay converges, a different set is refused, and existing audit ids are unchanged (golden test); `close-epic --status` prints the dropped nodes bound by the receipt. New capability flag `Tracker.epic_dropped_closure_supported` (`True` on Linear only); new public helpers `write.parse_accept_dropped` and `write.epic_receipt_dropped`; `write.transition` and `write.set_field` now return the adapter's result. YouTrack, GitHub Projects and DevHub refuse `--accept-dropped` before any read and keep their current behaviour; the receipt validator (`write._validate_epic_outcome`, new `tracker` argument passed by every caller) admits an abandoned node in a bounded receipt only for a provider declaring `epic_dropped_closure_supported`, and otherwise keeps the refusal it had before this change. Review follow-up, same entry: `transition ... dropped <expected-state>` checks that the named predecessor is one of the five before any read, so a replay naming another value (for example `done`) is refused instead of converging (the check sits in `write.transition`, ahead of the repository-binding read that already calls the provider, and reads the adapter's new `Tracker.abandon_predecessors`, the single definition of the five states, empty on the other providers; the adapter keeps its own check), while a replay naming an admitted one, and `set-field`, still converge without a write; the abandon compares the named predecessor with the native state whatever the projection says, so a started issue moved natively to `backlog`, `ready` or `blocked` is abandoned from that native state; and an abandon is refused before any write on a chain of advisory `cockpit-evidence` receipts with no lifecycle state receipt, whose result a strict read would not read back. Limits named, not fixed: replay divergence when a node is cancelled or reopened after the closure, a pending audit whose graph changes, reopening a started-then-dropped issue, a started issue moved natively to `backlog`, `ready` or `blocked` and not abandoned (strict-read conflict), release scope of a dropped issue, and the unguarded `set-field State done`. R5: `docs/tracker-contract.md` (PAT-ADR-0017 paragraph, Epic closure row), `docs/tracker-contract.v1.json` (Epic closure cells and DevHub note), `docs/linear-tracker.md` (abandoned-issue and Epic paragraphs), `skills/close-epic/SKILL.md`, `README.md`; artefacts documented there: CLI forms `edit transition <ISSUE-ID> dropped <expected-state>`, `edit set-field <ISSUE-ID> State dropped` and `issue close-epic --accept-dropped=ID[,ID...]`, capability flags `guarded_abandon_supported` and `epic_dropped_closure_supported`, adapter vocabulary `abandon_predecessors`; not documented under `docs/`, as Python-level helpers no documented surface cites: `write.parse_accept_dropped`, `write.epic_receipt_dropped`, the `tracker` argument of `write._validate_epic_outcome` and the field `EpicClosureState.dropped` (its value is the documented `close-epic --status` line); no configuration key and no `foundry_cli.py` verb added; the FOUNDRY-123 detector is not shipped, status asserted here. Tests: `test_abandon_and_dropped_closure.py`, `test_issue_close_epic.py`, `tests/fixtures/tracker-conformance-v1.json`.
- Docs (PAT-130; documentation only, no code, no model, no `claude`, no cloud call, no campaign mode run by the diff; frozen v1 to v5 protocols, configs, results and reports untouched (only a pointer line added to the non-frozen bilan and launcher documents), no verdict recomputed or requalified; nothing promoted, no local model or default activated, no bill gain claimed, Epic closure neither decided nor performed): `docs/qualification/pat-19-decision-v2.md` records the PAT-19 decision by use (autonomous local implementation: keep the cloud, v1; local read-only exploration: keep the cloud, gain not demonstrated, v2 to v5, with the PAT-129 price-weighted readings labelled outside the rule; local compression in one call: track abandoned on this corpus on the coordinator's recommendation approved by the maintainer, never measured), with compatibility, quality and economy separated, what is not decided, the fate of PAT-113, PAT-118, PAT-119 and of the c1 draft, and the tracks the measures point to; one pointer line each in `pat-19-local-first-bilan.md` and `pat-19-launcher-v1.md`. R5: no CLI verb or option, config key, public constant or routing table changed; FOUNDRY-123 detector not shipped, status asserted in the document and checked in review.
- Changed, BREAKING (PAT-125, phase 3, PAT-ADR-0016; offline and fake tests only in the diff, no `claude`, no model, no paid invocation run by the diff; cheaper-candidate regime of FOUNDRY-ADR-0019 on list price per token only: no measured gain of quality or cost, no bill or quota saving claimed, no benchmark, replay, comparison or effort matrix; the effort `medium` is the provider default and the maintainer's choice, unmeasured; frozen PAT-19 files untouched): `DEFAULT_MAPPINGS["claude"]["economy"]` goes from Haiku 4.5 / null to `haiku-5.5` / `medium`. Two native compatibility trials of 2026-10-09 are committed unchanged: the first, [`pat-125-native-trial.json`](docs/qualification/pat-125-native-trial.json), keeps its verdict `not_conforming` (fixture round-trip only) and is not reinterpreted; the maintainer then authorised a second trial, the new decision the ADR requires; the second, [`pat-125-native-trial-2.json`](docs/qualification/pat-125-native-trial-2.json), run with `--neutral-fixture`, has the verdict `conforming` (profile, model `claude-haiku-5-5`, effort `medium`, host version 2.1.294 observed by the hook, fixture: all exact). A trial is a compatibility smoke, not the basis of the promotion. The mapping is per tier: the scout and every non-gate role falling back to `economy` get it. Reviewer and architect floors, `balanced`/`frontier`/`apex` and the Codex defaults are unchanged; the short `haiku` alias is not promoted. Breaking 1: a Claude Code host below 2.1.293 loses the default `economy` tier (the Agent call, or the headless launch, is refused with a message naming 2.1.293) until it is updated or the project maps `economy` to Haiku 4.5 with `"effort": null`; there is no automatic fallback. Breaking 2: the `economy` default now carries an effort, so a project mapping `{"model": "haiku-4.5"}` without an `effort` key (or the `haiku` alias), and a direct request for `haiku` / `haiku-4.5` on a tier whose effort is not null, are refused with the message asking for `"effort": null` in `mappings.claude.<tier>`; an economy model-only override to a non-Haiku model now inherits `medium` instead of failing on an inherited null. Breaking 3: an availability list without `haiku-5.5`, such as the formerly documented `FOUNDRY_CLAUDE_AVAILABLE_MODELS=haiku-4.5,sonnet-5.5,opus-5.5`, puts the `economy` target outside the list: the scout is refused even on a conforming host (`Aucun modèle disponible pour le rôle 'scout' sur claude. Niveaux inférieurs essayés : economy.`) and every non-gate downward fallback reaching `economy` ends the same way; add `haiku-5.5` to the list. Breaking 4: an effort-only `economy` override now applies to Haiku 5.5: `{"effort": "low"}` used to give Haiku 4.5 without effort and now silently gives `haiku-5.5` / `low`, declared but not qualified; `{"effort": null}` used to pass and is now refused (`effort null réservé à Haiku 4.5`); name the model too or remove the override. Review fixes in the same change: the Haiku 4.5 effort refusal names the fix of its own source (inherited default, project mapping or user effort) and gives the whole entry `{"model": "haiku-4.5", "effort": null}` when the mapping does not name the model; the hook's below-minimum refusal says that a session resumed after a host update can still carry the old version and that a new session resolves it; the headless runners apply the minimum to a dated snapshot `claude-haiku-5-5-YYYYMMDD` too (the hook refuses such an identifier first for its missing preloaded profile), probe `--version` in the launch's own working directory, and write their unobserved-version notice as one line on standard error at every launch, without any warning filter or other process-wide state; a transcript whose last record exceeds the one-mebibyte tail is a documented cause of `unknown`. The maintainer confirmed the effort on 2026-10-09 (« Médium très bien ») after being told it is the provider default, not a measured choice. The second trial establishes that the marker is in the parent's final answer, not strictly that it transited through the child, and its neutral fixture changes two things at once, so the cause of the first divergence is not isolated. Project rollback: `{"mappings": {"claude": {"economy": {"model": "haiku-4.5", "effort": null}}}}`; product rollback: restore the default by an ordinary PR, the declaration stays. Known unmonitored cost risk: list price times five above 100,000 prompt tokens, and reasoning tokens at `medium`. Observation window, reference and rollback triggers: [`pat-125-haiku-55-promotion.md`](docs/qualification/pat-125-haiku-55-promotion.md). Documentation status (R5): `docs/model-routing.md` (defaults table, PAT-125 section, PAT-16 sentences), R7 of `AGENTS.md`/`CLAUDE.md` (identical), the decision note, the `FOUNDRY_CLAUDE_AVAILABLE_MODELS` option example in `plugin.json`.
- Recorded, fixed and prepared (PAT-125, phase 2, PAT-ADR-0016; offline and fake tests only in the diff, no `claude`, no model, no paid invocation run by the diff; no measured gain, bill or quota saving claimed; frozen PAT-19 files untouched): at that revision the Claude `economy` default was not changed and the incumbent Haiku 4.5 / null was kept, as PAT-ADR-0016 requires after a non-conforming verdict (superseded by the phase 3 entry above, after a second trial authorised by the maintainer). The first bounded native trial of PAT-ADR-0016 was run once by the coordinator on 2026-10-09 from `8de5c1e`; its result is committed unchanged as [`pat-125-native-trial.json`](docs/qualification/pat-125-native-trial.json): launched profile, executed model (`claude-haiku-5-5`), observed effort (`medium`) and host version (2.1.294, observed by the hook itself) exact, fixture check `divergent`, verdict `not_conforming`. The ADR keeps the incumbent in that case and authorises no further trial without a new decision; the reading of the divergence (from the raw stream, not from the tool), what is and is not established, and the three options then put to the maintainer are in [`pat-125-haiku-55-promotion.md`](docs/qualification/pat-125-haiku-55-promotion.md). Fixed: the headless runners (`command_runtime.py`, `campaign_runtime.py`) passed a pin with a minimum host version to `claude` unchecked; they now call `claude_headless_host_version_requirement()` (`routing_facades.py`), which, only for such a pin, asks the `claude` about to be launched its `--version` (local, unpaid, same runner and child environment), raises the same `RoutingConfigError` naming required and observed versions below 2.1.293 before any provider invocation, and on an unreadable answer lets the launch proceed with a notice carrying `CLAUDE_HOST_VERSION_UNOBSERVED` (a `RuntimeWarning` at that revision, a line on standard error since the phase 3 entry above) (never presented as conforming; receipts unchanged; R6 allow-list untouched). Prepared, not run by the diff and authorising nothing by itself: `--neutral-fixture` on `foundry.claude_profile_trial` (a public fixture marker instead of a value named token, a parent told to wait for the agent's answer; default fixture, verdict rule and recorded result unchanged). Documentation status (R5): `docs/model-routing.md` (PAT-125 section: status, headless runners), R7 of `AGENTS.md`/`CLAUDE.md` (identical), the decision note.
- Added (PAT-125, phase 1, PAT-ADR-0016; offline and fake tests only, no `claude`, no model, no paid invocation run by the diff; no benchmark, replay, comparison or effort matrix; no measured gain, bill or quota saving claimed; frozen PAT-19 protocols, configs and results untouched): Claude Haiku 5.5 is DECLARED, the `economy` default is not changed by this phase (see the phase 2 and phase 3 entries above for the trials and the promotion). Declaration: `haiku-5.5` / `claude-haiku-5-5` in `_CLAUDE_MODEL_DECLARATION`, its own effort scope `(claude, haiku-5.5, v1)` `low` to `max` in `default-effort-scopes.json`, ten versioned profiles `routed-{readonly,worker}-{low,medium,high,xhigh,max}-haiku-5.5` (74 generated profiles, was 64), `haiku-5.5` in the telemetry vocabulary (no price row: cost unknown, never zero). Haiku 4.5 and its `20251001` snapshot stay exact effort-free historical pins; the short `haiku` alias is unchanged (unobserved version, `CLAUDE_ALIAS_VERSION_UNOBSERVED`). Minimum host version: `CLAUDE_MODEL_MIN_HOST_VERSION = {"haiku-5.5": (2, 1, 293)}`; the routing hook observes the host version from the top-level `version` of the last versioned record of the session transcript named by its payload (`claude_host_version()`, bounded tail, never the environment, never a `claude` on `PATH`); observed below 2.1.293 the Agent call is denied with a message naming the required and observed versions and nothing is substituted (no fallback to Haiku 4.5, to the alias or to another tier); an unobservable version is `unknown`, said so in the new hook-context key `host_version` and by the new warning `CLAUDE_HOST_VERSION_UNOBSERVED`, and the launch proceeds (trade-off documented). The Haiku 4.5 effort refusal now names the received effort, its source and the fix (`"effort": null` in `mappings.claude.<tier>`). Trial material, not run by the diff: `python3 -m foundry.claude_profile_trial` (`--work-dir`, `--out`, `--claude`, `--parent-model`, `--projects-dir`, `--dry-run`) launches one `claude -p` parent in source mode on an isolated fixture, kills it at twenty minutes, refuses any replay and writes one `foundry.pat125-native-trial.v1` result keeping requested, transmitted and observed apart, an absent observation staying `unknown`. Decision note, trial command, observation window and rollback: [`pat-125-haiku-55-promotion.md`](docs/qualification/pat-125-haiku-55-promotion.md). Both project rollback forms and the restored default are tested offline against the prepared default (`tests/test_claude_haiku55.py`). Documentation status (R5): `docs/model-routing.md` (new PAT-125 section, 74 profiles, configuration example now writes `"effort": null`), R7 of `AGENTS.md`/`CLAUDE.md` (identical), the decision note; `claude_invocation_binding(host_version=...)` is an internal optional argument, not a caller-facing surface.
- Added (PAT-129; offline read-only analysis and tests only, no `claude`, no cloud arm, no model, no `lms`, no campaign mode run by the diff; frozen v1 to v5 protocols, configs, results and reports untouched, no frozen verdict recomputed or requalified): where premium work goes in the PAT-19 v4 and v5 comparisons. New module `foundry.cost_breakdown` (`python3 -m foundry.cost_breakdown`: `--results`, `--ledger`, `--report`, `--grid`, `--streams-dir`, `--session-logs-dir`, `--override-rate`, `--out`): premium tokens by role, token class and model per arm, unweighted and weighted by the new dated price grid `tooling/foundry/pricing-breakdown-v1.json` (Claude 5.5 list prices captured 2026-10-08 from the official pricing page; both cache-write bounds, 5-minute and 1-hour; a model without an entry valid on the session day, or Haiku 5.5 whose price depends on the unrecorded prompt length, stays `unavailable`; `pricing-v1.json` and `cost_attribution.py` untouched), the weighted L over A ratio per accepted task on the v5 paired set as a reading outside the frozen rule, a `task_coverage` block (the arms do not spend premium on the same tasks: arm L spends nothing where its local exploration is contaminated, PR 48 in v4, PR 33 and 42 in v5, so arm totals are only compared on the tasks where both arms spent premium, as a non-decisional reading outside the rule), and, from the raw transcripts kept off the repository, the read/search tool calls of the cloud sessions and the direct weight of the API calls made only of them under the hypothesis that they cost nothing (not a ceiling of the effect of a reduction: it omits the carried context and is a lower-bound classification; aggregates only), a sensitivity block (`--override-rate`) for the Sonnet 5.5 cache-read price that the page (0.10) and the host's own list cost (0.20) disagree on, output tokens per call joined from host logs on permitted fields only (model alias and token counters, via a counters-only reader; any ambiguity makes the role `unavailable`), exit code 1 also when the report's paired accepted counts differ from the records (file still written). Totals equal those of the committed reports. Results, method, limits and unknowns: `docs/qualification/pat-19-cost-breakdown-v1.md` and the two aggregate files `pat-19-cost-breakdown-v1-x4compare-1.json` / `-x5compare-1.json`. API list prices are weights under a subscription, never a bill: no billing gain is announced. R5 status: new module entry point, new price grid and new documents documented in that page and in `pat-19-launcher-v1.md`; no `foundry_cli.py` verb or option, routing table or frozen protocol changed.
- Fixed/added (PAT-128; offline replay and tests only, no `claude`, no cloud arm, no model, no `lms`, no campaign mode run by the diff; frozen v1 to v5 protocols, configs and results untouched, no verdict recomputed, `FROZEN_PROTOCOLS` unchanged): the two instrument defects of the PAT-19 v5 comparison. (1) The two contaminated explorations (PR 42 and PR 33 of `pat-19-x5compare-1`) are explained by an offline replay of the audit on the 86 raw streams kept off the repository ([`pat-19-audit-replay-v5.md`](docs/qualification/pat-19-audit-replay-v5.md), `pat-19-audit-replay-v5.json`, `pat-19-audit-replay-v5-quoted.json`): the answer `Path '<path>' not found` of the omp `read` tool was not recognised as an absent path (only `Path not found: <path>` was), so the path of the failed call and its echo were counted as a read under the work root; hypothesis of the v5 results confirmed. New config key `isolation.audit_absent_path_forms: "quoted_path"` (constants `ABSENT_FORMS_KEY`/`ABSENT_FORMS`, parameter `absent_forms` of `audit_transcript` and `replay_audit`, record field `audit.absent_path_forms` only under the key), accepted only under a protocol after v5 with `audit_revision` 2 and refused under v1 to v5 and the v5 pilot; without it every behaviour, argv, env, bundle and record of v1 to v5 is byte-for-byte unchanged (the replay of the 69 v5 records and of the 32 v4 records without the key reproduces the earlier result). `replay-audit --quoted-not-found` measures the coordinate on a campaign that does not carry it; a replay of a campaign with an audit policy adds `arm_findings_replayed`. (2) The test that depended on the maintainer's real `~/.config/foundry/config.env`: 11 of the 12 v5 task bases predate PAT-104 and lack the isolation fixture, and the error appears in the streams of 6 tasks (PR 24, 26, 27, 37, 38, 42); the probe of the real-config reads was run on the PR 42 base only (`tests/test_doctor.py` and `tests/test_agent_routing.py` there look at the real config, which the native sandbox refuses with EPERM); the current suite is already isolated by PAT-104 for reads and writes, and `tests/conftest.py` now also refuses `os.stat`/`os.lstat` of the real state (`Path.exists()` was the gap: the audit hook has no `stat` event); known gaps: `os.access`, `os.scandir`/`DirEntry.stat`, a relative path given with `dir_fd`, subprocesses. No product code changed for (2). Docs: `docs/qualification/pat-19-audit-replay-v5.md`, "Forme citée du chemin absent (PAT-128)" in `docs/qualification/pat-19-launcher-v1.md`. R5 status: new config key, constants, parameter, CLI option and record fields documented there; no `foundry_cli.py` option, routing table or routing constant changed; FOUNDRY-123 detector not shipped, status asserted here and checked in review.
- Docs/evidence (PAT-127; docs and raw evidence only, no code change, no model load by the diff, no cloud call by the diff; frozen v1 to v5 protocols, configs and v1 to v4 results untouched): real PAT-19 v5 comparison `pat-19-x5compare-1` run on 2026-10-08 (frozen tooling `main` at `d173e17`, Claude Code 2.1.285, arms A and L, candidate `qwen3.6-35b-a3b-mlx-4bit` fixed, the 12 corpus tasks; 12 launches, model reloaded before each task under the maintainer's block mandate of the day, not confirmed one by one; Foundry state backed up off repo before the launch; observed context 262144 despite `-c 65536`; OrbStack and ChatGPT stopped for the campaign, 24 machine preflights all accepted, no exit code 3 or 4). Result: 74 cloud executions (cap 150), 19,308,267 premium billing tokens (A 11,433,953; L 7,874,314); decision `keep_cloud`, `campaign_conclusion` `keep_cloud`, `paired_rule.reason` `not_retained_on_paired_set`, failed criterion economy only, compatibility pass. Paired decided set D = 10 tasks (threshold 9); accepted on D A 5, L 5 (the accepted sets differ on 4 of 10 tasks); premium per accepted task A 1,835,739, L 1,574,863, ratio 0.8579 against 0.85 (14,485 tokens per accepted task above the line); accepted over the 12 tasks A 6, L 5. The two local explorations of PR 42 and PR 33 were flagged `contaminated` (the local model typed a path to a file that does not exist and the `read` tool answered with a wording the audit does not recognise as "not found"; read in the raw streams and the code, cause not replayed): both tasks leave D, no token spent, no verdict recomputed. 17 reviewer verdicts (11 PASS, 6 BLOCK), none unreadable; audit journal 13 records / 21 entries, 49 host-refused calls (Bash 48, Write 1), 10 records echoing a pytest `PermissionError` on the Foundry config file under the native sandbox (effect on outcomes unknown); no pilot stop criterion applied during the campaign. Erratum to protocol v5 section 2 case (ii) carried in the results document (the v2 to v4 quality reading is by bounds; this campaign is in case (ii): `inconclusive` under the v2 to v4 reading of the same records, `keep_cloud` under v5). Non-decisional readings (sensitivity to one task, role breakdown, reviewer share) are labelled as such; no billing gain announced; no promotion, no local model or default activated. Report `docs/qualification/pat-19-exploration-results-v5.md`, bilan v1 to v5 for Epic PAT-87 `docs/qualification/pat-19-local-first-bilan.md` (no decision taken for the maintainer), raw files `docs/qualification/pat-19-runs/x5compare-1/` (the 86 raw streams are not committed; manifest by sha256). R5: report, bilan and raw pieces added, link in the "Protocole v5" section of `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v5 and config v5 untouched. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Fixed and re-frozen (PAT-126 review round 4, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; no new pilot; v1 to v4 and their report output untouched): the review round 4 found the acceptance criteria met and nothing blocking; two findings reopen the fourth freeze (`bbbb8ef`, committed on the branch, never merged, like `b699ed9`, `56634fe` and `e6f90c6` before it); `pat-19-campaign-v5.json` sha256 `fbac092181d373602d94b58ec469b07ed81853520bac4f1dbac7ec7ddad660db` (hash test). No value of the rule changed (0.85, threshold 9, feedback, bounds). Paired rule, order of reading of the zero case (coordinator's decision, last adjustment of that case; report only): on the paired set the acceptance comparison is read BEFORE the zero case, so accepted_L(D) < accepted_A(D) keeps the cloud (`not_retained_on_paired_set`) whatever the zeros, including L accepting no task of D while A accepts at least one - the shape of the v4 result (A 1, L 0), which was read `inconclusive` (`no_accepted_task_in_one_arm_ratio_undefined`) although v2 to v4 gave `keep_cloud` there; that reason remains for A accepting no task of D (both arms at zero, or A at zero and L at least one: no ratio can be computed, never zero, never an infinite ratio read as a pass); new report field `paired_rule.failed_criteria` (`acceptance`, `economy`). Text: the 'remaining asymmetry' of protocol section 2 point 6 and the operator notice were false for a round 0 cut during its cloud execution (the relaunch writes NO record and stops at the same arm of the same task, later arms and tasks are never attempted, the campaign stays `incomplete_campaign` unless it was L's round of the 12th task; `stopped_by_cap` records only follow a cut at a correction round, of a reviewer after a verdict, unreadable tokens or a never-settled start), now written exactly, with a test; the summary 'what v5 changes' lists every case where the verdict label differs from v2 to v4 (cross-checked by comparing both rules on 6000 random synthetic campaigns, not committed); section 2 point 3 (order), point 5, the section 3 table, the config notes (same edits in the unpinned pilot config) and the launcher doc aligned; chronology: order of the zero case adjusted after round 4. The maintainer's explicit agreement on the rule change is still to be recorded on the issue before the merge. Stale test headers ('DRAFT') fixed. `pat-19-golden-check-v5.json` (committed at `5b74996` from the reopened freeze `bbbb8ef`) is removed from the tree again for the coordinator to regenerate from the new freeze commit. Tests in `test_local_first_exploration_v5.py`. R5: report field `paired_rule.failed_criteria`, documented in the protocol (sections 2, 7.4, 8) and the launcher doc; the FOUNDRY-123 detector is not shipped, status asserted here.
- (Superseded: this fourth freeze was REOPENED by the review round 4 and redone, see the entry above; its sha256 and its statement on `pat-19-golden-check-v5.json` are those of that freeze) Fixed and re-frozen (PAT-126 review round 3, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; no new pilot; v1 to v4 and their report output untouched): the third freeze (`e6f90c6`, committed on the branch, never merged, like `b699ed9` and `56634fe` before it, each reopened by a review round) is reopened and redone; `pat-19-campaign-v5.json` sha256 `093b89022edced18f57237b6cdce62a835fdba52a62a4997e21d0d364693860a` (hash test). No value of the rule changed (0.85, threshold 9, feedback, bounds). Shared temp directory (this code runs at each cloud execution; offline tests only): the watch lists top-level NAMES only (`_temp_names`, `os.listdir`; it stat-ed every entry and an entry vanishing meanwhile emptied the listing, so that on the listing made BEFORE every entry became new and a PRE-EXISTING matching entry could be moved, against the frozen sentence 'an entry that existed before is never touched'; on the listing made after, or on a failed move, nothing was moved and the record said `count: 0`). A failed listing made before now moves nothing; `audit.temp_leftovers` gains `watch` (`complete`, `partial`, `unavailable`, `not_applicable`), `not_moved`, `not_inspected`, `unwatched` (`no_tmpdir`, `listing_before_failed`, `listing_after_failed`), and `count` is null, never 0, as soon as one execution of the record was not watched; `_holds_bundle_material` says `None` when it gives up at `TEMP_WALK_BOUND` (20000 entries) or cannot read a directory; `report.temp_leftovers` also lists the records whose watch is partial or unavailable; no `OSError` of the watch aborts a paid execution. Paired rule (report only): the premium of an `interrupted` record that names no cloud execution (`_cut_without_cloud`: a cut local exploration, a cloud round cut before its reservation) counts 0, known, so an interruption that spent nothing, replayed as the operator notice prescribes, no longer makes the campaign `inconclusive` / `premium_total_unknown` with an empty `campaign_level_reasons`; a record with a cloud session and a null total stays unknown. Under v5 `arms.L.quality`/`economy` are `unavailable` and the four `economy_detail` fields `ratio`, `premium_pass`, `premium_per_accepted`, `reference_premium_per_accepted` are null also while the campaign is incomplete (the v2 to v4 readings were still printed then). Text (protocol section 2 and the config notes, same edits in the unpinned pilot config): the v2 to v4 rule was misdescribed in four places ('one undecided task made every verdict unavailable'): an undecided task made the ECONOMY unavailable, hence forbade 'retained', quality stayed read by bounds and compatibility did not depend on it, so `keep_cloud` stayed possible; both directions of the v5 change are written (a quality failure in the best case or a compatibility failure with |D| < 9 gave `keep_cloud` and gives `inconclusive`). Points 3 and 6 say what the code does: when a record's premium is unknown, when `premium_total_unknown` comes with a campaign-level reason and when it does not (a premium the record lacks but the ledger knows, after a cut that followed the settlement: a stated limit, not lifted), what (d) really applies once a cloud replay exists (lenient, no spend at stake; shared `report` code not changed), the precedence of the compatibility readings, and the remaining asymmetry of a cloud execution really cut (tokens unknown to the ledger stop every later cloud execution, `premium_tokens_unmeasurable`: its round is never replayed). True chronology of the threshold 9 (fixed after pilots 1 to 3 and review round 1, before pilot 4; adjusted after rounds 2 and 3; no campaign trial at any point). The maintainer's explicit agreement on the rule change is still to be recorded on the issue before the merge. Operator notice and script: the STOP message names the launcher's exit codes (2 also a tool error, 3 also `premium_tokens_unmeasurable`, 130/143/129/1 an interruption); new 'after a stop' checklist (read `ledger.unknown_spent_work` and `paired_rule.campaign_level_reasons` before relaunching; a tool error or an interruption is replayed once, at round 0 before a verdict only). `pat-19-golden-check-v5.json` (committed at `52ef9d1` from the reopened freeze `e6f90c6`) is removed from the tree again: the coordinator regenerates it from the new freeze commit and commits it after it (no test depends on it). Pilot evidence reports are NOT regenerated. Tests in `test_local_first_exploration_v5.py`. R5: record field `audit.temp_leftovers` (new sub-fields), constants `TEMP_WALK_BOUND` and `TEMP_UNWATCHED`, the report behaviour above, documented in the protocol (sections 2, 6.2, 7.4, 8), the launcher doc and the operator notice; the FOUNDRY-123 detector is not shipped, status asserted here.
- (Superseded: this third freeze was REOPENED by the review round 3 and redone, see the entry above; its sha256 and its statement on `pat-19-golden-check-v5.json` are those of that freeze) Fixed and re-frozen (PAT-126 review round 2, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; no new pilot; v1 to v4 and their report output untouched): the second freeze (`56634fe`, committed on the branch, never merged) is reopened and redone; `pat-19-campaign-v5.json` sha256 `9fe6da84c3739302cdaede9a71ab6713bb5c2b27d0b78994c9fdc2a00d5fb366` (hash test). No value of the rule changed (0.85, threshold 9, feedback, bounds). Paired rule (`_apply_paired_rule`, protocol section 2 rewritten to say exactly what the code does): an UNAVAILABLE compatibility (unmeasured swap, a local exploration lost without a successful replay) now gives `inconclusive` (`compatibility_unavailable`), only a FAILED one gives `keep_cloud` (it was read as a failure); an interruption or a cap on one task makes it an undecided task that leaves D, also when a cloud execution of it was cut with unknown tokens (printed `null`), instead of making the whole campaign `inconclusive`; the campaign-level reasons (a cloud execution never settled or named by no result record, an interrupted or unknown-token execution of a task of D or of no compared task, a start with neither record nor replay) are named and applied inside the rule and give `inconclusive` whatever D says; `decision` and `campaign_conclusion` are the authoritative keys and `paired_rule.verdict` always equals `decision` (it could say `retained` under an `inconclusive` decision), the verdict on D before those reasons being kept in `verdict_before_campaign_level` with `reason_before_campaign_level`, `campaign_level_reasons` and `compatibility`; one cost ratio under v5 (`paired_rule.ratio`; `ratio`, `premium_pass`, `premium_per_accepted` of `arms.L.economy_detail` are `null` with `superseded_by`); `paired_rule.undecided` lists exactly the tasks outside D; a decided `keep_cloud` has no robustness reading, and why. Shared temp directory: `_move_temp_leftovers` moves only top-level NAMES absent before the execution (a pre-existing entry modified during it was moved, then discarded with the attempt) into the launcher's quarantine `<state>/temp-quarantine/<campaign>-<session>/` (`TEMP_QUARANTINE`), under the state directory the sandbox denies to the arms, and nothing is ever deleted; stated limit: an entry created in the window by another process of the same account and matching the signature would be quarantined too. Config: the protocol coordinates `quality_bounds_on_undecided` and `economy_unavailable_cases` described the v2 to v4 rule and are rewritten for v5; notes aligned (same edits in the unpinned pilot config). Text: the new handling of undecided tasks is stated as a CHANGE of the decision rule (relative to v2 to v4 and to the 'rule unchanged' wording of the issue), decided by the coordinator under the mandate of 2026-10-08 and reported to the maintainer, whose explicit agreement is to be recorded on the issue before the merge; the threshold 9 of 12 is three quarters of the corpus, chosen by judgement with no statistical derivation; pilot 1 is void (operator error), not a failed criterion; both earlier freezes were committed on the branch (the text said the first was not); temp-directory uses aligned on the raw streams (calls naming `$TMPDIR`: 6 of 6 host-refused at pilots 2 and 3, 4 of 4 at pilot 4, none ran); host-refused calls 2, 7, 4 and 5; the judge's pristine-bundle fallback (`candidate_breaks_test_loading`, REFUSED 0/0/0) and its limit (a transient judge failure on the candidate becomes REFUSED; the run report re-reads each one) written where 'never REFUSED 0/0/0' stood alone; the cross-task channel 27 -> 24; what changed after pilot 4 and was exercised by no pilot (the frozen-protocols tuple, the `report` code above, which does not run during a campaign execution, and the temp-leftover move policy, which does). `pat-19-golden-check-v5.json` (committed at `4f95b86` from the reopened freeze `56634fe`) is removed from the tree again: the coordinator regenerates it from the new freeze commit and commits it after it (no test depends on it). Pilot evidence reports are NOT regenerated with this tooling (stated in the evidence note). Tests in `test_local_first_exploration_v5.py`. R5: report fields `paired_rule.verdict_before_campaign_level`/`reason_before_campaign_level`/`campaign_level_reasons`/`compatibility`, `economy_detail.superseded_by`, state directory `temp-quarantine/`, constant `TEMP_QUARANTINE`, documented in the protocol (sections 2, 6.2, 8), the launcher doc and the operator notice; the FOUNDRY-123 detector is not shipped, status asserted here.
- (Superseded: this second freeze was REOPENED by the review round 2 and redone, see the entry above; its sha256 and its statement on `pat-19-golden-check-v5.json` are those of that freeze) Frozen (PAT-126, after pilot 4; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; v1 to v4 untouched): the PAT-19 protocol v5 and `pat-19-campaign-v5.json` are FROZEN on 2026-10-08 (machine date), before any trial of the v5 campaign `pat-19-x5compare-1` (sha256 `b8bc2733a67959c3ccbb33756761ac6c15e9f3bb227c558b5de8cbb093651e58` pinned by the hash test, which now covers the configs v1 to v5; the pilot config is outside the campaign and not pinned); `FROZEN_PROTOCOLS` gains `pat-19-protocol-v5` (not its pilot). Pilot 4 (`pat-19-x5pilot-4`, tooling `361f719`, clean shell) PASSED the stop criteria (a) to (f) on PR 27: 1 595 725 premium tokens (A 620 855, L 974 870), A and L accepted, barrier observed, no contaminated record, 2 journal entries (`tool_result:~/.config/foundry/config.env`, echoes of bundle tests failing under the sandbox), 5 host-refused calls, no temp leftover, registry unchanged; `paired_rule` `inconclusive` (|D| < 9) as expected on one task. Its first attempt was refused by the operator script before any model load: three full repository copies (`tmp.*`) left by v3/v4 reviewers on 2026-10-07 sat in the per-user temp directory during pilots 1 to 3 and T2 to T4 (no raw stream names them: a text search, not a proof of non-reading); they were moved to a quarantine outside the repository. Protocol section 7 records the four pilots (1 void, 2 and 3 stop criterion (c), 4 passed), every instrument change, non-decisional readings of PR 27 across the pilots (direction differs; one seen task proves nothing), and what remains true at the freeze (one passing pilot on one task; 11 tasks never played under the final instrument; the run report reads every decisive finding on the transcript). Limits added: the sandbox-test echo in the journal and the earlier campaigns' temp leftovers. Evidence without raw transcripts: `pat-19-runs/x5pilot-4/` (report as produced with `361f719`, replay of the audit with the final policy), notes in `pat-19-v5-pilots-evidence.md`. `pat-19-golden-check-v5.json` is removed from the tree: it is produced from the frozen commit and committed after it (no test depends on it). Operator notice and script: campaign envelope `pat-19-x5compare-1` (150 executions, 75 000 000 tokens, 100 000 s), clean shell, temp-directory check, golden check and interpreter preflight as prerequisites. R5: keys `rules.exploration_comparison.paired_decided_min`, `isolation.audit_policy`, `exploration.comparison_tasks`/`comparison_task_set`, record field `audit.temp_leftovers` (and `audit.policy`/`journal`/`journal_by_role`/`refused_calls`), report keys `paired_rule`, `temp_leftovers`, `audit_journal`, verbs `golden-check` and `replay-audit`; the FOUNDRY-123 detector is not shipped, status asserted here.
- Reopened and fixed (PAT-126 review round 1, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms`; v1 to v4 untouched): the freeze is REOPENED, protocol v5 and `pat-19-campaign-v5.json` are back to DRAFT (removed from `FROZEN_PROTOCOLS` and from the sha256 pin) until a fourth pilot `pat-19-x5pilot-4` passes the stop criteria (a) to (f). Text: three false or contradictory sentences fixed (a `mktemp` `cd` is journaled under the policy, not `review_unreadable`, and `mktemp -d` lands in the per-user temp directory, the commonest case of limit 3.6 (b); 7 host-refused calls at pilot 2, not 9; draft leftovers removed, the git warning is stated as observed 0 times), plus: pilot 1's flag disappears once the host-refused call is no longer counted (not an ordinary-gesture over-flag), the section-3.6 class and cross-reference, 'refusé ou contaminé compte 0' bounded to the screening, new/inherited values told apart ('Qui a fixé quoi'), PR 27 being the task of the pilots, T2 to T4 and one of the 12, stale notes in the configs. Code: `_refused_call` counts the permission-rule shapes only for file tools and only as the whole error (a Bash command that printed such a sentence was not refused); `os_denied` is path-boundary safe (a truncated prefix must start with the denied root followed by `/`); the strict judge tells a candidate that broke the loading of the tests (REFUSED `candidate_breaks_test_loading`, a pristine bundle of the task does report) from a dead instrument (error); `golden-check` output carries its provenance (config, manifest, snapshot sha256, tooling commit and dirty flag, date). New v5 decision rule on undecided tasks (`rules.exploration_comparison.paired_decided_min` 9, pinned): paired decided set D, `inconclusive` under 9 of 12, acceptance and premium per accepted task on D, L retained iff accepted_L >= accepted_A, both >= 1, ratio <= 0.85 and compatibility, worst-case robustness reading, otherwise keep the cloud; `report` prints `exploration_comparison.paired_rule`; the v2 to v4 report is unchanged. Shared per-user temp directory: under the native-sandbox key each cloud execution's new top-level temp entries (user-owned, no symbolic link, not `foundry-*` or `pytest-of-*`) that hold a copy of bundle material are MOVED into the attempt directory and recorded `audit.temp_leftovers` (report `temp_leftovers`), deciding nothing; the operator script refuses a `plugins/foundry` directory at the top level of `TMPDIR`; limits written (order A then L, 6 of 6 explicit uses host-refused at pilots 2 and 3). Pilot evidence reports, replays and the golden check regenerated with this tooling (replay: no decisive finding on any reviewer, unchanged). Tests in `test_local_first_exploration_v5.py`. R5: new keys/fields/report keys listed in the protocol, section 8.
- (Superseded: this first freeze was REOPENED by the review round 1 and redone after pilot 4, see the entries above) Frozen (PAT-126 phase 2; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; v1 to v4 untouched): PAT-19 protocol v5 and `pat-19-campaign-v5.json` FROZEN on 2026-10-08 (machine date), before any trial of the v5 campaign `pat-19-x5compare-1`; `FROZEN_PROTOCOLS` gains `pat-19-protocol-v5` (not its pilot); the hash test pins the configs v1 to v5 (the pilot config `pat-19-campaign-v5-pilot.json` is outside the campaign and not pinned). Last instrument fix (pilot 3, criterion (c) a second time: a PASS lost to `tool_result:<work-root>/priv`, a path echoed and truncated by the reviewer's own `sed`): under `isolation.audit_policy` and an observed barrier, a `tool_result:` finding under a root the OS denies to the shell (or a truncated prefix starting with one) is journaled; outside those roots it stays decisive. Protocol section 7 now records the three pilots (all 2026-10-08: pilot 1 void, operator PATH, 758 999 tokens; pilot 2 criterion (c), two PASS discarded, 1 825 779; pilot 3 criterion (c), one PASS discarded, 1 657 127), every instrument change they caused (interpreter preflight, judge instrument error, `golden-check`, audit policy in three parts), the statement that NO fourth pilot ran after the last fix (effect shown only by offline replay of the three pilots' streams and by tests: no decisive finding on any reviewer; a stated risk of the campaign), non-decisional readings of PR 27, and the final limits (shared temp directory copies, about 150 of ~2150 bundle tests failing under the sandbox with PermissionError on the home (coordinator's figure), host-refused calls per session, the `tool_result` rule). Evidence committed without raw transcripts: `pat-19-runs/x5pilot-1/` to `-3/` (reports regenerated with the freeze tooling from the config each pilot really used, replay-audit with the final policy), `pat-19-golden-check-v5.json` (12/12), notes in `pat-19-v5-pilots-evidence.md`. Operator notice and script: campaign command and envelope for `pat-19-x5compare-1`, clean shell, golden check and interpreter preflight as prerequisites; launcher doc section no longer a draft. R5: keys `exploration.comparison_tasks`/`comparison_task_set`, `isolation.audit_policy`, `binary_version` on cloud drivers, verbs `golden-check` and `replay-audit` (policy), record fields `audit.policy`/`journal`/`journal_by_role`/`refused_calls`, report key `audit_journal`, ledger entry `interpreters`, `FROZEN_PROTOCOLS`; the FOUNDRY-123 detector is not shipped, status asserted here. Mandate date corrected everywhere: 2026-10-08.
- Added (PAT-126, after pilot 2 `pat-19-x5pilot-2`: instrument not usable, stop criterion (c): the two reviewers that carried the only two PASS verdicts were flagged `/<unknown-working-directory>` (one by a command `dontAsk` refused and that never ran; one by a `cd` the audit did not believe after a failing test run, so `../../../scratch` resolved to the work root) and became `review_unreadable`; 1 825 779 premium tokens; fake arms and offline tests only, no `claude`/cloud/model/`lms`; v1 to v4 and every config without the key unchanged): new key `isolation.audit_policy` = `journal_under_observed_barrier` (after v4 only, needs `cloud_native_sandbox` and `audit_revision` 2, pinned for v5 and its pilot, in both DRAFT configs). For a stream whose barrier is `settings_transmitted_version_observed` only: a tool call the host refused as a whole (known shapes: dontAsk, permission rule, reads outside the working directories; an error result) did not run, counts for nothing and moves no directory (`audit.refused_calls`, per tool, no text); a SHELL finding under a root the OS denies to that execution (the `denyRead` list passed minus `allowRead`) or at the unknown working directory goes to `audit.journal`; every other finding (file tool, path the OS does not deny, tool-result path, forbidden command) stays decisive; the reviewer rule is unchanged in form. `contamination` keeps its meaning; `audit` gains `policy`, `journal`, `journal_by_role`, `refused_calls`; `report` gains `audit_journal`; `replay-audit` replays the policy per stream (non-decisional outcome reading). Replay on pilot 2: A 2 reviewer journal-only (5 paths), L 1 reviewer cleared by the refused call; nothing else changes. Protocol 3.6 and 7.3 ter (pilot 2 recorded; pilot 3 will run as `pat-19-x5pilot-3`), launcher doc, operator notice; limits stated (OS enforcement observed by P3/P7/P16 only, shared temp directory and other OS-readable places not covered). Tests in `test_local_first_exploration_v5.py`.
- Added (PAT-126, after the VOID pilot `pat-19-x5pilot-1`: the operator's shell had a Homebrew `python3` without pytest first on PATH, so the judge returned `REFUSED` 0/0/0 and the arms could not run tests; 758 999 premium tokens spent, no reviewer ran; fake arms and offline tests only, no `claude`/cloud/model/`lms`; v1 to v4 behaviour and frozen files untouched): (1) `local_first_corpus.judge(strict_report=True)`, used only under a protocol after v4, raises `JudgeInstrumentError` when pytest writes no junit report (or exits 4/5 with zero tests) instead of a `REFUSED` 0/0/0 verdict: the attempt is a `tool_error` (undecided, no corrector feedback); v1 to v4 keep their historical 0/0/0 `REFUSED` (tested). (2) v5+ preflight `probe_interpreters` before any claim: the launcher's interpreter and the `python3`/`python` on the cloud arm's PATH must import pytest, else exit 2 naming which; resolved paths (home masked) and pytest versions in the ledger's `preflight` entry (`interpreters`). (3) New verb `golden-check` (offline: untouched bundle must be REFUSED with failing tests, merged change must be ACCEPTED, per task of the config's list; JSON, never overwritten). (4) `pat-19-v5-operator.sh` refuses (65, before any model) a `python3` without pytest and prints it; notice says to launch from a clean shell. (5) Draft protocol 7.3 bis records pilot 1 as void and pilot 2 as `pat-19-x5pilot-2` (no new config: the campaign id is read from the envelope). Documented in `pat-19-launcher-v1.md` and the protocol (R5); FOUNDRY-123 detector not shipped.
- Added (PAT-126, phase 1; fake arms and offline tests only, no `claude`, no cloud arm, no model, no `lms`, no campaign mode run by the diff; frozen v1 to v4 protocols, configs and results untouched, sha256 of the four configs checked by the tests; v1 to v4 launcher behaviour, argv, environment, bundles and record shapes unchanged): launcher support for the PAT-19 protocol v5 and a DRAFT protocol, DRAFT campaign and pilot configs and operator material; NOT the freeze (the real pilot of the instrument comes first, then the fixes and the freeze, phase 2). Loader: `pat-19-protocol-v5` and `pat-19-protocol-v5-pilot` (allow-list `pat-19-protocol-vN`, N >= 5, plus the pilot) are PINNED (fixed candidate without screening, bounds 60 steps / 900 s and 2 corrections, `one_task_per_launch`, `correction_feedback` 20/300/200, `isolation.private_attempt_root`, `isolation.cloud_native_sandbox` and `isolation.audit_revision` 2, the task list and label, rule tasks 12 or 1 and ratio 0.85, `binary_version` `claude --version` 2.1.285 on every cloud driver, no cloud explorer, `isolation.allow_read_home` within `[".config/git/ignore"]`); the v4 keys `correction_feedback`, `exploration.fixed_candidate`, `exploration.comparison_task_group` are now also accepted under a protocol after v4 (still refused under v1 to v3 with the same message). New config keys: `exploration.comparison_tasks` (explicit ordered list of corpus PRs, played in that order, each checked against the manifest and the snapshot before any claim; exclusive with `comparison_task_group`) and `exploration.comparison_task_set` (label of `task.set` in every comparison record, ledger entry and report filter; default `comparison`), both only after v4. The pilot (`pat-19-protocol-v5-pilot`, campaign id containing `pilot`) and the campaign cannot share a campaign id (refused at `Runner` construction); arms are A and L only under v5 as under v4. Claude Code version pin: a cloud driver's `binary_version` is checked by `check_binary_version` at the start of `compare-exploration`, before any reservation, once per distinct command (nothing is asked without the key). Git warning under the native sandbox (`unable to access '~/.config/git/ignore'`, echo flagged in 1 reviewer of each of T2 to T4): the v5 configs set `isolation.allow_read_home` to that single file, which feeds the native settings' `sandbox.filesystem.allowRead` only (Read/Edit deny rules on the home, shell `denyRead` and `allowWrite` unchanged; the Read tool stays denied; no effect on the local profile where `.config` is denied last): the effect on `git` is EXPECTED, NOT VERIFIED (cannot run `claude` here), the pilot verifies it. Docs: `pat-19-protocol-v5.md` (French, DRAFT, not frozen; values fixed by the coordinator under the maintainer's mandate of 2026-10-08, none validated one by one; the pilot stop criteria (a) to (f) written before the pilot; known defects handled or written as accepted limits: shared per-user temp directory, reviewer `mktemp` copy, `dontAsk` behaviour, revision 2 over-flags; the PAT-124 carry-over list), `pat-19-campaign-v5.json` (12 tasks 26, 38, 25, 42, 33, 37, 30, 83, 27, 24, 48, 19; label `pat-19-v5`; envelope recommended 150 cloud executions / 75 000 000 premium tokens / 100 000 s, not enforced by the loader) and `pat-19-campaign-v5-pilot.json` (PR 27, label `pat-19-v5-pilot`, 16 / 8 000 000 / 30 000), `pat-19-v5-operator.sh` (modes `compare` and `pilot`, refuses before loading any model a campaign id, a state directory or a Claude Code version of the other mode) and `pat-19-v5-operator.md` (exact pilot command), section "Protocole v5 (PAT-126, brouillon)" of `pat-19-launcher-v1.md`. R5: the artifacts above are documented there; the FOUNDRY-123 detector is not shipped, status asserted here. Tests: `test_local_first_exploration_v5.py` (fake arms; stand-in `claude` and `lms` scripts; 12 tasks one per launch in order with a report including a `review_unreadable`); `test_local_first_audit_revision.py` and `test_local_first_native_sandbox.py` use `pat-19-protocol-v6` as their unpinned later protocol (they used `pat-19-protocol-v5`), and one assertion of the latter now says the v4 keys are accepted after v4.
- PAT-124 review round 3 (two more real LAUNCHER trials recorded, run by the coordinator on 2026-10-08 with the hardened code at `7dddce7`; the diff itself runs no `claude`, no cloud arm, no model; frozen v1 to v4 untouched). Four trials of 2026-10-08, never merged: **T1** manual (outside the launcher, `bypassPermissions`, hand-written settings, evidence not committed), **T2** launcher before the hardening (committed, unchanged), **T3** launcher, `--probe-test plugins/foundry/tests/test_benchmark_evidence.py` (result NOT committed; billing 65 933 + 50 520; P2 `unknown`: pytest answered "3 deselected" because that file is in the opt-in `benchmark_campaign` list of `tests/conftest.py` at that base — a wrong choice of file, not a sandbox effect), **T4** launcher, `--probe-test plugins/foundry/tests/test_process_contract.py` (committed as `docs/qualification/pat-19-native-sandbox-trial-2026-10-08-t4.json`, produced by `native-sandbox-trial-reeval`, never hand-edited; billing 35 593 + 50 661; Claude Code 2.1.285; `dontAsk` asked and seen; barrier `settings_transmitted_version_observed` for both records). Now OBSERVED with the launcher's settings (T3 and T4 unless said): a write outside the attempt refused BY THE OS (P16, a python program, `Operation not permitted`) — so an OS write refusal has been observed with the launcher's settings; the Write tool refused on the bundle's `.claude` by a permission rule (P14) and the shell by the `dontAsk` mode (P15, which therefore does NOT show the OS-level `denyWrite` on `.claude`); the settings read from each execution (`denyWrite` and the `Edit` rules present for both executions); one real test file executed and passing under the sandbox (T4 only: 6 tests of a small file that only reads repository files, not the bundle's suite). Reviewer in T3 and T4: ran, verdict read, no call refused, no `mktemp` copy. STILL not established: a long real task, the bundle's test suite, any version other than 2.1.285, Glob/Grep, the Edit rule, a work root under the home, hot reload of settings, settings above the working directory, the shared per-user temp directory, the reviewer's `mktemp` copy (not used in T2 to T4), the OS-level `denyWrite` on `.claude` for a shell command. Recurring over-flag for the v5 ticket, seen again: the `git` warning on `~/.config/git/ignore` echoed in a tool result is flagged as a home path (reviewer: 1 flag in each of T2, T3, T4). Trial tool: an absent tool the arm did not even attempt is `tool_not_available` when the driver's tool list (`allowed_tools`; offline, the one list of the stream's `init` event) does not hold it, with its own note, instead of `unknown` (T4: Glob, Grep); P2 is `allowed` only on "N passed" with no "N failed"/"N error(s)" in the result (the probe pipes pytest into `tail`, so the exit status is lost); the not-established list adds the bundle's suite when P2 ran one file and the OS-level `denyWrite` on `.claude` when P15 was refused by `dontAsk`; the offline re-evaluation finds the run's nonce from any probe name (T4's arm named no `TOKEN-`), and the two trial verbs report a refusal instead of a traceback when run with `python -m`. Wording: the reviewer's `mktemp` copy "should work" in the per-user temp directory (deduced, never exercised). R5: `pat-19-launcher-v1.md` ("Quatre essais du 2026-10-08", "Essai réel du 2026-10-08", "Réglages de projet de l'arme et durcissement", "Limites supplémentaires", "Révision 2 de l'audit"); artefacts: `declared_tools` parameter of `observe`, note `tool_not_attempted`, second committed result. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- PAT-124 review round 2 (docs, traces and refusals under the key only, trial tool; fake arms and offline tests only, no `claude`, no cloud arm, no model run by the diff; frozen v1 to v4 untouched, no verdict recomputed). Two trials of 2026-10-08 are now named everywhere and never merged: **T1**, the coordinator's MANUAL trial (outside the launcher, `bypassPermissions`, hand-written settings, `claude-haiku-4-5`, throwaway directory; evidence NOT committed, not the launcher's settings; it alone saw the OS refuse a shell write into a sibling directory and the Write tool create a file there) and **T2**, the LAUNCHER trial (verb `native-sandbox-trial`, PR 27, 2 cloud executions, `dontAsk`; the committed JSON; P9, the shell write outside the attempt, was refused by the `dontAsk` mode, not by the OS). An OS-level write refusal had not been observed with the launcher's settings at that point (since observed by P16 in T3 and T4: round 3 above). Written limit: under the key EVERY bundle the launcher builds (local harness and local explorer included, not only cloud ones) has no `.claude` directory nor `.claude` symbolic link, so a task whose legitimate patch or hidden tests touch `.claude/` would be unsolvable or judged on another tree than in v4 (v5/v4 comparability cost); established offline for the 12 tasks of corpus v1: zero path under `.claude/` in the base tree, the merged patch and the protected tests of every task (table in the doc). Under the key: the record of a correction round carries `correction_excluded` (paths dropped from the patch handed to the corrector; same shape as `review_excluded`; absent when empty and always absent without the key); a symbolic link named `.claude` is removed from a bundle (it survived `rmtree`), also after a patch is applied, and the bundle is refused if it cannot be removed; the loader also refuses a cloud driver argv carrying `--add-dir`, `--allowedTools`, `--allowed-tools`, `--mcp-config`, `--plugin-dir` (bare or `=value`; `NATIVE_REFUSED_FLAGS`) or more than one separated `--permission-mode <x>` pair (`--setting-sources`, `--strict-mcp-config`, `--disallowedTools` stay accepted; the list is not shown exhaustive); the in-memory key `_native_trial` is refused in any config file. Trial tool: the result reports the settings each execution received (taken from the execution, `settings_source`, `reviewer_settings_passed_paths_masked`), not a rebuild that lacked `denyWrite` and the `Edit` rule on the bundle's `.claude`; probe P2 now really runs one test file (`--probe-test`, required; `allowed` only on `N passed`) — in T2 it was `pytest --collect-only`, relabelled as a collection in the tool, the JSON and the doc, and "no test actually executed under the sandbox" joins the not-established list; that list is now computed from what a trial played and saw and adds P16 not played, hot reload of settings, settings above the working directory, the shared per-user temp directory and the reviewer's `mktemp` copy not exercised (`reviewer.mktemp_used`). Committed result regenerated by `native-sandbox-trial-reeval` (never hand-edited); it states that its settings predate the round-1 hardening. Doc wording: the file tools "rely on" permissions (no "confined"); shell reads are refused by the OS only on the home, the work root and the launcher's deny list; P7 is an OS refusal, P8 a permission rule. R5: `pat-19-launcher-v1.md` (sections "Exposition des bras cloud", "Révision 2 de l'audit", "Bac à sable natif des bras cloud", "Essai réel du 2026-10-08", "Réglages de projet de l'arme et durcissement", "Limites supplémentaires"); artefacts: verb option `--probe-test`, record field `correction_excluded`, execution key `native_settings`, constants `NATIVE_REFUSED_FLAGS` and `NATIVE_TRIAL_KEY`, result fields `settings_source`, `reviewer_settings_passed_paths_masked`, `reviewer.mktemp_used`. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- PAT-124 review round 1 (docs, hardening under the key only, tool): project settings of the arm (docs: `--settings` beats project/local per key but arrays MERGE across scopes; the sandbox protects `.claude` settings of the working directory against shell commands, no exemption; drivers keep `--setting-sources project,local`, dropping it is not shown safe so it is unchanged and stated): under the key every cloud bundle is built with no `.claude` directory (tree, index, base commit; patches of correctors stripped), the bundle's `.claude` is in `sandbox.filesystem.denyWrite` and an `Edit` deny rule, deny rules on sensitive FILE entries are emitted bare and with `/**`, and the loader refuses a cloud driver argv carrying `--permission-mode=`, `--settings`, `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions`. Trial tool: only known refusal shapes count (layer `refused_by`: `os_sandbox`, `dontAsk_mode`, `permission_rule`), `judged_on` per write probe, probes P14 to P16 (project settings by Write and shell; an OS-level shell write) added and not played, a state-directory marker keeps a trial and a campaign apart, committed result regenerated offline. Limits written, not fixed: work root under the home not played, OS-refused shell write never observed, shared per-user temp directory channel (v5 decision, R6 lists TMPDIR), open reads (`/Volumes`, `/private/tmp`, `/Users`), comparability cost of `dontAsk`, permission mode checked only if `init` names it. R5: `pat-19-launcher-v1.md` sections "Essai réel du 2026-10-08" and "Réglages de projet de l'arme et durcissement"; artefacts: `protect` parameter of `native_sandbox_settings`, `_strip_claude_dirs`, `NATIVE_TRIAL_MARKER`, probes P14 to P16, result field `refused_by`/`judged_on`/`masked_rules_note`. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Added/fixed (PAT-124; fake arms and offline tests only, no `claude`, no cloud arm, no model, no `lms` run by the diff; frozen v1 to v4 protocols, configs and results untouched): native Bash sandbox for the cloud drivers of the PAT-19 launcher, enabled by the new config key `isolation.cloud_native_sandbox` (boolean; allow-list like `audit_revision`: accepted only under `pat-19-protocol-vN`, N >= 5; refused under v1 to v4, a missing or unknown protocol; without it every driver argv and environment is byte-for-byte unchanged, tested against the v4 config). With it each cloud execution gets `--permission-mode dontAsk` (replacing `bypassPermissions`) and `--settings <json>` appended, with ABSOLUTE paths computed per execution (`"."` does not designate the working directory there): `sandbox.enabled`, `allowUnsandboxedCommands: false`, `failIfUnavailable: true`, `autoAllowBashIfSandboxed`, shell reads denied on the home and the work root and re-allowed on the attempt directory (plus `isolation.allow_read_home`), `permissions.blockReadsOutsideWorkingDirectories`, the attempt directory as an additional working directory, `Read`/`Edit` allow rules on it and `Read`/`Edit` deny rules on the sensitive paths that do not contain it; the child environment gets `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1` ADDED (only under the key; the AGENTS.md R6 allow-list and `FOUNDRY_DATA` are intact; the `claude` process itself is never wrapped). The file-tool hole seen by T1 only, the coordinator's MANUAL trial of 2026-10-08 (outside the launcher, `bypassPermissions`, hand-written settings, evidence not committed, not the launcher's settings: Write created a file in a sibling directory), is addressed by the `dontAsk` mode plus those rules (sources: Claude Code docs, permissions, permission-modes and sandboxing pages); T2, the LAUNCHER trial of 2026-10-08 (the committed result), settled Read, Write and shell outside the attempt (refused; the shell and Write WRITES by the `dontAsk` mode, not by the OS) and `dontAsk` with `autoAllowBashIfSandboxed` (shell works: test collection and `git`, no test executed); it did NOT settle Glob/Grep (absent from the driver, documented best-effort), the Edit rule (not exercised), a shell write refused by the OS rather than by `dontAsk`, a work root under the home, or the Write/shell channels on the bundle's `.claude` (probes P14 to P16 added, not played). The reviewer keeps its instructions; its file tools work in its attempt directory, its shell should also work in the per-user temp directory (the `mktemp` copy: deduced from the documentation, never exercised, no reviewer of T2 to T4 made one). Loader coupling: `audit_revision: 2` is accepted only with the key; `isolation.private_attempt_root` is now accepted under v5 and later (the other v4 keys stay v4-only); every cloud driver must declare `sandbox: false` and a `claude-stream-json` stream. `audit.barrier` (record that ran to completion under revision 2) now says what the launcher verified: `not_verified` < `settings_transmitted` < `settings_transmitted_version_observed` (one Claude Code version in the `init` event and a final `result` that is not an error; a permission mode other than the one asked for gives `not_verified`; weakest of the streams of a record); never "confined"; a record cut by an error carries no `audit`. PAT-123 round-4 remarks: N1 comment, doc and this changelog now say "a record that ran to completion"; N2 L5 and L6 are inherited blind spots of ordinary work (`git -C`, `make -C`, a script the arm wrote, a path built at run time), not deliberate evasion, and the `-C` target is still flagged (tested); N3 known over-flags of ordinary work for the v5 ticket (`source .venv/bin/activate`, `break`/`continue`, `trap`, `cd "$(git rev-parse --show-toplevel)"`, a word `cd` in a commit message or heredoc) are listed and pinned by tests; `break`/`continue` are NOT made inert (ending a loop early is not modelled, not clearly sound in both shells). Launcher trial T2 run by the coordinator on 2026-10-08 (verb `native-sandbox-trial`, PR 27, Claude Code 2.1.285, 2 cloud executions, mode `dontAsk`; not to be confused with the manual trial T1 of the same day, whose evidence is not committed; result `docs/qualification/pat-19-native-sandbox-trial-2026-10-08.json` rebuilt offline by the new verb `native-sandbox-trial-reeval`; classifier fixed: the Read `blockReadsOutsideWorkingDirectories` message is a refusal, `tool_not_available` (Glob/Grep absent from the driver), `not_exercised` (Edit needs a prior Read); shell, Read and Write outside the attempt refused, bundle tests and git allowed, reviewer works; the single reviewer audit flag is the echo of git's `~/.config/git/ignore` warning, a recurring over-flag for v5). Trial tool: verb `native-sandbox-trial` (module `local_first_native_trial`; two real cloud executions through `Runner.cloud_execution`, so envelope, ledger, audit and settings are the campaign's; writes one result file with no raw transcript, session id or home path, unobserved items `unknown`). R5: documented in "Bac à sable natif des bras cloud (PAT-124)" of `pat-19-launcher-v1.md`; artefacts: key `isolation.cloud_native_sandbox`, `isolation.private_attempt_root` under v5+, constants `NATIVE_SANDBOX_KEY`, `NATIVE_PERMISSION_MODE`, `NATIVE_GIT_ENV`, `BARRIERS`, `BARRIER_SETTINGS`, `BARRIER_OBSERVED`, `native_sandbox_settings`, `with_native_sandbox`, `execute_driver(native_settings=)`, launcher verb `native-sandbox-trial`, record field `audit.barrier` values; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Fixed/added (PAT-123; fake arms and offline replay only, no model load, no `lms`, no `claude`, no cloud call; frozen v1 to v4 protocols, configs and results untouched, no verdict recomputed): PAT-19 contamination audit revision 2, enabled by the new config key `isolation.audit_revision` (1 default, 2; allow-list: accepted only under `pat-19-protocol-vN`, N >= 5, so v1 to v4, a missing or an unknown protocol are refused at load; without it every launcher behaviour and record shape of v1 to v4 is unchanged; a v5 that also wants `private_attempt_root` needs a loader change). Revision 2: (1) relative paths are resolved against a SET of candidate directories and flagged as soon as one candidate takes them out of the zone; the rule is one invariant, the candidates contain the real directory, claimed only under nine written assumptions (H1 to H9 in `local_first_runner.py` and the launcher doc: observed Claude Code 2.1.285 carry-over, what a result proves, default shell options, lexing); a `cd` REPLACES the candidates only inside an allow-list grammar (`_simple_script`: simple commands joined by `;`, newline, `&&`, `||`, `|`; no subshell, group, substitution, `&`, reserved word or function) when the result is clean and whole (status 0), nothing before it could end the script or move the shell out of sight, it starts its chain alone with one literal target and the result holds no `cd:` line; everywhere else it only ADDS candidates (`UNKNOWN_CWD` for an unreadable target, a builtin outside the inert allow-list, a built command name, `CDPATH`; a fixed point for loops and functions); a relative path is resolved against every directory from the command that names it to the end of the call (positional arguments of `sh -c`, variables, function arguments), and a command that may RUN in a directory outside the zone is a hit by itself (a bare name such as `cat x` is read there; `cd $X; cat y` is flagged, which revision 1 misses); a `cd` word not read as a command adds `UNKNOWN_CWD`; from one call to the next the end candidates are kept after a clean result and every directory of the call otherwise, the `Shell cwd was reset to ...` line deciding when present; a stream that does not name an observed host version (omp, another Claude Code version) carries nothing and believes no `cd` (stricter fallback, recorded as `audit.host_models: unverified`); a fresh `mktemp -d` directory is deliberately NOT modelled (`cd $T` stays `UNKNOWN_CWD`): written, tested, then removed, because the audit cannot see a symbolic link the arm puts in a directory whose real path it does not know; covered by named regression tests for the three under-flags of the second review (script stopped before its `cd`, `cd` run more than once, positional arguments) and a property test against a real `/bin/bash` and `zsh` under four encoded host behaviours; it over-flags ambiguous lines by design (a `cd` followed by a failing command or a `grep` without match, a loop, a `cd` word in a message, a reviewer working in a `mktemp -d` copy); (2) an omp tool call whose path got `Path not found: <that path>` read nothing: not a hit, recorded apart in `audit.not_found`; (3) a flag in the reviewer's session no longer marks the arm's attempt `contaminated`: it is recorded in `review.contamination`, the judge and review verdicts stay readable, and a flagged reviewer decides nothing whatever it said (`review_unreadable`, loop stopped, findings passed to no corrector), including on an attempt cut after the reviewer's audit (the flag stays on `review.contamination` of the cut record); `report` lists such records under `review_contaminated` (key present only when there is one); (4) `.pytest_cache/` and `.ruff_cache/` are excluded from the captured patch (only these two; `-f` kept). Origin of the `.pytest_cache/` in the v4 reviewer diffs established: the arm's own `pytest` run creates it, the repository `.gitignore` already ignores it, and the instrument's `git add -A -f` capture forced it into the patch (instrument defect, fixed for a future campaign only). New offline mode `python3 -m foundry.local_first_runner replay-audit` (`--work-root-not-sensitive` for the scope measurement): applies revisions 1 and 2 to the raw streams of a finished campaign and prints, per record, recorded / old / new classification, with no driver, model or cloud call, writing a new file and never rewriting a result. Replayed on the 36 v4 streams (`docs/qualification/pat-19-audit-replay-v4.json` and `-scope.json`, note `pat-19-audit-replay-v4.md`; raw streams stay off the repository, the result carries their sha256 and no home path): revision 1 reproduces the 8 recorded flags exactly; revision 2 keeps the 2 real excursions (`find /`, `find` of the work root), removes 4 of the 5 audit errors, turns the fifth (L PR 83 reviewer) into a reviewer flag for an unreadable `cd $T` of a `mktemp -d`, moves the mistyped explorer path apart (2 non-existent paths) and adds one reviewer flag (A PR 27, same cause); the other 23 records stay clean; the replay summary counts reviewer-flagged records apart (`reviewer_flagged_now`: 2) and each stream names its host model. Report output: `informative_arms` names only an informative arm that was played (it printed `["E"]` for the v4 campaign, which has no arm E; output-only, no decision reads it; reports already committed are not recomputed). PAT-122 review nits fixed in `pat-19-exploration-results-v4.md` (exact scope of the "trigger", now backed by the committed `-scope.json`, wrong R6 reference, "10 names" attributed to the diagnosis). Threat model (maintainer's decision, 2026-10-08), written at the top of the "audit revision 2" block and in the launcher doc: the lexical audit is a journal of the accidental excursions of an arm doing its job, not a barrier against an arm that tries to deceive it; the barrier is Claude Code's native Bash sandbox enforced by the operating system (PAT-124); the invariant is claimed only for an arm that is not trying to deceive the audit, under H1 to H9 (H4 no longer says that a program cannot move the shell: it can, by renaming, moving or replacing a directory or symbolic link of the shell's path). Known limits, deliberate evasion, out of scope and not fixed one by one (L1 to L6): a directory or symbolic link moved, renamed, replaced or removed under the shell; `CDPATH` or other `cd` state set through a built name or arithmetic evaluation; profile aliases, functions, hooks and shell options; commands built at run time; a `cd` hidden in a script or interpreter; a path built at run time. One-line hardening only: zsh `print -v` (like `printf -v`) and `integer`/`float` with an expanded argument taint the `cd` that follows; two tests hold today's behaviour for the two L1 forms as known limits, not guarantees. Revision 2 must be enabled only together with the native sandbox; no config key for it exists yet, so the launcher checks nothing and says so: every revision-2 attempt record that ran to completion carries `audit.barrier: "not_verified"` (constant `AUDIT_BARRIER`; a record cut by `_tool_error` carries no `audit`; PAT-124 then added the other two values), never to be read as "confined". The zsh half of the shell-oracle tests is an explicit `skip` where zsh is absent (the host shell is zsh; a CI image may only cover bash); a cut during a flagged implementer is tested under both revisions; the v4 keys and revision 2 cannot be loaded together today (to carry in the v5 ticket). The replay result is unchanged by this alignment (both JSON regenerated identical); acceptance gap stated for the maintainer: 4 of the 5 false flags disappear, the fifth returns as a reviewer flag and 1 reviewer flag is added. R5: documented in "Révision 2 de l'audit et rejeu hors ligne" of `pat-19-launcher-v1.md` and the replay note; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed (launcher CLI verb `replay-audit`, campaign key `isolation.audit_revision`, constants `AUDIT_REVISION`, `FROZEN_PROTOCOLS`, `UNKNOWN_CWD`, `OBSERVED_CLAUDE_CODE`, `AUDIT_BARRIER`, record field `audit.barrier`; operational constraint: revision 2 only with the native sandbox, PAT-124). Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Docs/evidence (PAT-122; docs and raw evidence only, no code change, no model load by the diff, no cloud call by the diff; frozen v1 to v4 protocols, configs and v1 to v3 results untouched): real PAT-19 v4 comparison `pat-19-x4compare-1` run on 2026-10-07 (code `main` at `4adeb2f`, arms A and L, candidate `qwen3.6-35b-a3b-mlx-4bit` fixed, the six tasks of the v3 screening: PR 30, 83, 27, 24, 48, 19; 6 launches, model reloaded before each task under the maintainer's block authorization, not confirmed one by one; Foundry state backed up off repo before the first trial; observed context 262144 despite `-c 65536`). The first launch (19:00) was refused by the preflight (`dedicated_machine_process_over_2gib:OrbStack:2887MiB`, no cloud execution); the maintainer then authorized quitting OrbStack and the campaign was relaunched at 19:56. Result: 30 cloud executions (cap 80), 7,004,823 premium billing tokens (A 4,134,697; L 2,870,126); accepted tasks A 1/6 (PR 27, review PASS), L 0/6; decision `inconclusive`, `campaign_conclusion` `keep_cloud_insufficient_evidence` (L: compatibility pass, quality and economy unavailable), so the cloud is kept by PAT-ADR-0015; no promotion, no local model or default activated, no billing gain announced. The only task decided in both arms is PR 19 (refused in both). The corrector feedback changed test counts in 7 of 12 comparable correction rounds (v3: never); cause not established (no no-feedback arm). 8 of 32 records are `contaminated`: an independent read-only diagnosis (replay of `audit_transcript`, not committed raw streams) reproduced the 8 path lists and finds 5 audit errors (relative tokens resolved against the bundle ignoring `cd`; working directory reset at every Bash call; contamination of the reviewer session marking the whole attempt) and 3 correct flags (a `find /`, a `find` of the work root, a mistyped local-explorer path); according to that diagnosis, the v4 change that made the work root sensitive is what turned two pre-existing audit defects into flags (protocol v4 section 5 residual risk materialised; not replayed on v3). The `find /` also sent 10 file names from the maintainer's home to a cloud session (no content read per the diagnosis): a confinement incident, not an audit defect. The frozen rule counts all 8; no verdict recomputed. Three judge-ACCEPTED/reviewer-BLOCK attempts; a committed `.pytest_cache/` in the diff, responsibility not established. The report prints `informative_arms: ["E"]` although v4 has no arm E (hardcoded in `local_first_runner.py`, report-output defect, not fixed here). Real Foundry registry reported unchanged by the coordinator (off-repo observation; no exit code 4). Report `docs/qualification/pat-19-exploration-results-v4.md`, raw files `docs/qualification/pat-19-runs/x4compare-1/` (the 36 raw streams are not committed; manifest by sha256). Fixing the audit is future work through `foundry:intake`. R5: report and raw pieces added, link in the "Protocole v4" section of `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v4 and config v4 untouched. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Added/fixed (PAT-121; fake arms only, no real model/harness/cloud call; v1, v2 and v3 protocols, configs, results and launcher behaviour untouched): PAT-19 protocol v4 frozen before any trial (`docs/qualification/pat-19-protocol-v4.md`, French) with two changes meant to remove two suspected defects of the comparison instrument (hypotheses of this protocol: the v3 results call the 7 contamination flags probable false positives, not arbitrated, and an instrument defect plausible, not established, only for the identical test counts of PR 26 and 38; nothing is claimed repaired) and replay the downstream measurement: two arms only (A, L; no Haiku arm), candidate fixed to `qwen3.6-35b-a3b-mlx-4bit` with no new screening (same 60 steps / 15 min bounds and reload before each task as v3), comparison on the six tasks of the v3 screening (PR 30, 83, 27, 24, 48, 19; already explored locally in the v3 screening, never played downstream; the exploration is re-run), decision rule unchanged (L retained iff acceptance(L) >= acceptance(A) and premium per accepted task <= 0.85 x A; absent data never zero; insufficient evidence keeps the cloud), cap of 80 cloud executions, stated limits (six tasks, tasks already seen by the explorer, no Haiku arm, nothing said of other task families). Values validated by the maintainer on 2026-10-07 except the corrector feedback (at most 20 failing hidden-test names cut to 200 characters, each message cut to 300 characters, same for both arms; never the test source code (the test module and name are visible, and a message can carry a path under the bundle), with the reserve that the pytest message can carry the assertion expression and the expected values, so a corrector can fit code to the tests, and that a collection error teaches nothing) and the 2 correction rounds, fixed by the coordinator and confirmed by the maintainer on 2026-10-07 before any trial (a different value means a v5). Stated limits also: the candidate and budget were chosen on the localisation score of these same six tasks (0.639), so L's report is probably favourable there; observed context 262144 despite `-c 65536`. Each correction record also carries counters of what the corrector was told (`feedback`: failing tests shown, total, collection failure; no text; key absent when the feedback is off). Under the private root the free text of an explorer report rendered into arm L's statement has the resolved spelling of the work root masked (explorer bundle path to bundle-relative path, else `<work-root>`; another spelling of the same directory, e.g. a symlink, is not masked), because the audit now treats the work root as sensitive and arm A has no report (v1 to v3 rendering unchanged); this masking was decided by the coordinator and reported to the maintainer without explicit validation (corrected wording, PAT-122); stated limit: the corrector and the reviewer each run in their own private root, so an absolute path of an earlier execution left in a patch or output would still be flagged (frequency unknown). Launcher, all four v4 keys off when absent and refused at load outside protocol v4; `compare-exploration` refuses any arm but A and L under v4 before any spend, and `report` of a fixed-candidate campaign says `no_screening`: `correction_feedback` (after a judge refusal the corrector is told the failing hidden tests; `lfc.judge(..., failures=True)` returns them from the junit report, name and message cut, bundle path and judge temp dir masked), `isolation.private_attempt_root` (each attempt in `<work-root>/private-<attempt>/<attempt>/`, so `ls ..` and `ls ../..` show no sibling and raise no contamination flag; the work root and every other attempt are sensitive roots, the rest of the audit is unchanged), `exploration.fixed_candidate` (no screening needed, `screen-exploration` refused, another candidate refused) and `exploration.comparison_task_group` (`screening`: manifest group the comparison tasks come from), new config `pat-19-campaign-v4.json` (loader pins its coordinates) and operator loop `pat-19-v4-operator.sh`/`.md` (the v3 script hardcodes the v3 config and arms A,L,E). PAT-120 review nits: dead `FOUNDRY_DATA` exemption removed from the leak-probe test; `FOUNDRY_DATA` imposed by driver kind (every cloud driver kind, not `home != isolated`) and `isolated_environment` drops any host `FOUNDRY_*` (the loader already refused them); "bras cloud" wording fixed in `pat-19-v3-operator.md` and the R5 line of `pat-19-launcher-v1.md`; the operator backup covers both registry locations (`~/.config/foundry` and `$FOUNDRY_DATA` when exported). R5: documented in the protocol v4, `pat-19-v4-operator.md`, the "Protocole v4" and "Audit de contamination" sections of `pat-19-launcher-v1.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Fixed/hardening with a declared side effect (PAT-120; fake arms only, no real model/harness/cloud call; frozen v1/v2/v3 protocols and configs untouched, campaign config sha unchanged; applies to every later launch of any protocol): a cloud arm can no longer write the machine's real Foundry registry through the repository's own code or tests (incident of 2026-10-07, PAT-117). The launcher sets `FOUNDRY_DATA` itself to a fresh empty `<attempt scratch>/foundry-data` for every CLOUD execution (implementer, corrections, reviewer, cloud explorer; local arms keep exactly their previous environment), after `env_set`, never from the config or the host; the AGENTS.md R6 variables are untouched. It also fingerprints (sha256 or absent) the REAL `registry.json` at both places (host `FOUNDRY_DATA` when exported, and `<HOME>/.config/foundry`) before and after each cloud execution: on a change the execution is settled in the ledger, the attempt record is `contaminated` (not accepted, never replayed, `billing_total` null) with the fingerprints (never the content), also for the review of a path C local attempt, a `stop` record with reason `foundry_state_changed` is written and the launcher exits 4; it never writes or restores the registry. Comparability limit: `FOUNDRY_DATA` enables the product's telemetry journal, so from PAT-120 the arms' own runs of the repository tests see it set (unlike v1-v3 campaigns); the effect on what they observe is unknown; the judge is unaffected. What the guard misses, the remaining limit (unconfined writes elsewhere in the real HOME, uninventoried test state) and the check that all 12 corpus bases read `FOUNDRY_DATA` first are stated in the launcher doc. Operator doc: back up the Foundry configuration directory before a campaign (`cp -Rp`) and run no registry-writing Foundry command meanwhile. R5: documented in "État Foundry d'un bras cloud" of `pat-19-launcher-v1.md` and `pat-19-v3-operator.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed (launcher internals: `registry_fingerprints`, stop reason, exit code 4). Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Docs/evidence (PAT-117; docs and raw evidence only, no code change, no model load by the diff, no cloud call by the diff): real PAT-19 v3 campaigns run on 2026-10-07 (code `main` at `00eee40`, omp 18.6.1, LM Studio 0.4.25+1, model unloaded and reloaded before each one-task launch). Screening `pat-19-x3screen-1` (12 launches, no cloud): both candidates pass the frozen thresholds (qwen3.6-35b-a3b-mlx-4bit mean function recall 0.639, file precision 0.958, 0 refusal; qwen3-coder-30b-a3b-mlx-4bit 0.583 / 0.667, 2 refusals); retained = qwen3.6; v2 on the same six tasks gave 0.25, but budget, candidate set and reload regime changed together and the v3 screening is tuned on those tasks. Comparison `pat-19-x3compare-1` (6 launches, arms A/L/E on the 6 comparison tasks): 51 cloud executions (cap 120), 11,101,326 premium billing tokens; accepted tasks A 0/6, L 0/6, E 1/6; decision `inconclusive`, `campaign_conclusion` `keep_cloud_insufficient_evidence` (L: compatibility pass, quality and economy unavailable), so the cloud is kept by PAT-ADR-0015; no promotion. 7 contamination flags, all of one shape (a cloud arm named the launcher's work root); applied as frozen, a likely false positive that is NOT adjudicated, nothing reclassified. What the data support: reference arm A got no task accepted (3 refused by the hidden tests on all rounds, 3 never judged because of the contamination flags), so the comparison could not measure the downstream effect of exploration; on PR 25 the judge did discriminate (E accepted, A and L refused), and PR 26 and PR 38 have strictly identical test counts across all 9 attempts (a possible instrument defect, reading, not established). Observed model context: 262144 for qwen3.6 and 65536 for qwen3-coder (requested 65536), a fourth uncontrolled variable of the ranking and of the v2 to v3 comparison. Incident: a cloud arm (L, PR 42, 10:03:46) overwrote the maintainer's Foundry registry by running the repository tests (restored the same day; fix tracked in PAT-120); the records are intact but the influence on the attempts played afterwards is unknown; mechanical conclusion unchanged. Two of the seven flags come from the reviewer. Report `docs/qualification/pat-19-exploration-results-v3.md`, raw files `docs/qualification/pat-19-runs/x3screen-1/` and `x3compare-1/` (the 69 raw streams are not committed). R5: report and raw pieces added, link in the "Protocole v3" section of `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v3 and config v3 untouched.
- Added (PAT-116; fake arms only, no real model/harness/cloud call; v1 and v2 protocol, config and results untouched, v1/v2 launcher behaviour unchanged): PAT-19 protocol v3 frozen before any trial (`docs/qualification/pat-19-protocol-v3.md`, French; values and the v2 coordinator rules validated by the maintainer on 2026-10-07): the v2 read-only exploration with three changes that move together (a v2 to v3 screening difference is not attributable to the budget alone): the explorer budget (60 steps, 15 minutes, also for the cloud explorer of arm E), the candidate set (two fast MoE candidates chosen after the v2 results, `qwen3.6-35b-a3b-mlx-4bit` and `qwen3-coder-30b-a3b-mlx-4bit`, both required for a complete screening) and the model reloaded before each task as a rule written in advance; everything else inherited from v2 by reference (prompt, judge, ground truth sha256, thresholds, 0.85 x A rule, cap of 120). Stated limit: the v3 screening replays the same six screening tasks with a budget widened after seeing their results; the evidence that counts is the downstream comparison on the six never-played tasks. Launcher: new config `docs/qualification/pat-19-campaign-v3.json` (schema v2, `protocol` `pat-19-protocol-v3`, loader pins candidates, bounds and the flag) and the optional config key `exploration.one_task_per_launch` (screen-exploration and compare-exploration play at most one undecided task per launch, exit 0 and print `pat19-v3: work_remains=yes|no`; absent = v2 behaviour); operator reload loop `docs/qualification/pat-19-v3-operator.sh` (documented in `pat-19-v3-operator.md`) (the launcher still loads no model; the reload is attested by the operator script and one preflight per task). R5: documented in the protocol v3, the operator doc and the "Protocole v3" section of `pat-19-launcher-v1.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed.
- Docs/evidence (PAT-115; docs and raw evidence only, no code change, no cloud call): real PAT-19 v2 exploration screening run on 2026-10-07 (`pat-19-xscreen-1`, code `main` at `3af0932`, 5 candidates x 6 tasks, no cloud): no candidate reaches the frozen threshold (best mean function recall 0.25 < 0.5, no mean file precision >= 0.5), so by the frozen screening rule the campaign stops on "keep the cloud" for read-only exploration; comparison not run, no cloud quota spent. Of the 30 attempts, 23 were cut by a bound (15 time, 8 steps) and 7 answered within the bounds: 1 invalid report (missing keys), 2 empty (zero step) and 4 non-empty, all 4 naming the right file (function recall 1.0, 1.0, 1.0, 0.5); downstream effect not measured; promotion none. The last candidate's result is mixed with an operator intervention (two unload/reload cycles after dedicated-machine preflight refusals, a coordinator decision taken during the campaign by analogy with protocol section 11, maintainer validation requested; the two zero-step answers both follow a reload), see the "Dernier candidat et mémoire de la machine" section. Report `docs/qualification/pat-19-exploration-results-v2.md` and raw files `docs/qualification/pat-19-runs/xscreen-1/` (the 30 raw streams are not committed). The maintainer's validation of the coordinator-set rules of 2026-10-07 is requested in the report. R5: two artefacts added (report, raw pieces) and a link in `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v2 and config v2 untouched.
- Added (PAT-114; the diff's tests use fake arms only, the coordinator ran two real toy trials of the explorer drivers on
  2026-10-06; v1 protocol, campaign config and results untouched): PAT-19 protocol v2 frozen (`docs/qualification/pat-19-protocol-v2.md`, French, values validated by the
  maintainer on 2026-10-06): read-only exploration judged first on localization (local screening, no cloud: retained =
  highest mean FUNCTION recall among candidates with mean file precision >= 0.5, tie on shortest duration, STOP "keep
  the cloud" when the retained function recall is < 0.5, file recall measured but not deciding because each task has a
  single product file shared by 5 of 6 screening tasks; a contaminated or refused attempt counts as 0) then on its downstream effect (arms
  A / L / E on the 6 frozen comparison tasks, premium tokens per accepted task with the explorer included, L retained
  if compatibility, quality (acceptance >= A's) and economy (premium per accepted task <= 0.85 x A's) all pass; E vs A informative; three separate
  verdicts). Review round 3: a start-of-run preflight of `compare-exploration` (ledgered `phase: "start"`) followed by another preflight of the same launch is no longer a false `preflight_without_attempt` (resume after a refused preflight or a void exploration, `--paths L`), and the preflight is not run twice in a row; the screening report gains `campaign_conclusion` (`keep_cloud_insufficient_evidence` when it can no longer be completed under the resume rule) and `not_completable_tasks`; an L/E task with its exploration but no implementer record is undecided (`awaiting_implementer`, `incomplete_campaign`), not rejected; the four verdict-determining rules move to `exploration.protocol_coordinates`; the rules added on 2026-10-07 are dated as set by the coordinator before any corpus trial, their validation by the maintainer being requested in the campaign report; both explorers were re-run for real on the toy repository on 2026-10-07 with the frozen prompt (`final_prompt_trial`). Review round 2: a complete comparison with an `unavailable` verdict and no fail concludes `keep_cloud_insufficient_evidence` (`campaign_conclusion`); the system allow-list of the dedicated-machine check is narrowed to `/System/Library/` (consumer apps under `/System/Applications/` and the Cryptexes are ordinary consumers); the score-determining rules are protocol coordinates; a bound reached by the harness or a start error are handled (refusal / void); the dedicated preflight also runs at the very start of `compare-exploration` when arm L is requested; `main` now returns code 2 with a message (no trace) on a config load error, for v1 configs too. Review round 1 (rules validated 2026-10-06): file precision counts any file the merged diff changed (tests, docs, CHANGELOG
  included); only the first 10 functions of a report count and reach the implementer; dedicated machine free memory >= 35 % (replaces the swap-at-start threshold; the process rule is the real guard) and re-run right before each local exploration; a ground truth loaded from the committed file whose sha256 is in the config; an exploration cut by a bound is refused; the screening needs all five frozen candidates; the threshold is tested before the tie. The launcher gains the modes `screen-exploration` and `compare-exploration`, the type `local_explorer`
  (omp `--tools=read,grep,glob`, bundle read-only in the sandbox profile, 10 min / 25 steps) and `cloud_explorer`
  (Haiku 4.5), a deterministic localization judge (`foundry.local_first_exploration`, ground truth from the merged
  diffs, committed for the 12 tasks in `pat-19-exploration-truth-v2.json`), a dedicated-machine admission at preflight
  (another process over 2 GiB or under 35 % free memory refuses) and the new config `pat-19-campaign-v2.json`; the v1
  modes behave as before. The two explorer drivers were trial-run for real on 2026-10-06 on a toy repository (evidence `pat-19-preflight-v2-2026-10-06.json`) and are `verified: true`; the trial refused omp `--tools=read,grep,find,ls`, so the tool names were corrected to `read,grep,glob` before the freeze (loader accepts only these). The unsandboxed cloud explorer's read-only behaviour is observed, not enforced: the launcher checks the bundle's `git status` before and after each exploration and refuses one that changed it. R5:
  documented in `pat-19-protocol-v2.md` and the "Protocole v2" section of `pat-19-launcher-v1.md` (new CLI verbs
  `screen-exploration`, `compare-exploration`, `preflight --dedicated` of the campaign tool, config schema v2, driver
  types, result paths `XS`/`L`/`E`); no `foundry_cli.py` verb, product configuration key, routing table or public
  routing constant changed.
- Docs/decision (PAT-110; docs only, no code, no model load, no cloud call): PAT-19 v1 decision recorded in
  `docs/qualification/pat-19-decision-v1.md`. Autonomous ticket implementation by a local model: keep the cloud
  (v1 screening 0/30); no adoption, no local model, profile or default activated, no new ADR; v1 is not extended to
  12 tasks. Next usage to qualify: read-only exploration for a cloud implementer (local Lupin) under a protocol v2
  (PAT-114 freezes it, PAT-115 runs it). PAT-87 milestones PAT-88, PAT-89, PAT-90, PAT-91 postponed until the PAT-115
  verdict (none abandoned). PAT-19 stays open. R5: documented in the new decision file, linked from
  `pat-19-screening-results-v1.md` and `pat-19-launcher-v1.md`; no CLI verb, option, public constant or routing table
  changed, no protocol coordinate changed.
- Docs/evidence (PAT-109; the diff contains no code change and no cloud call; the run itself loaded five models): raw results and written report of the real PAT-19 v1
  screening run on 2026-10-06 (`pat-19-screen-1`, code `879e7b7`): 0 accepted of 30 (5 candidates x 6 tasks), so the
  protocol section 4 rule stops the campaign on "keep the cloud" for autonomous ticket implementation; no comparison
  run, no cloud spend. Files: `docs/qualification/pat-19-screening-results-v1.md` and
  `docs/qualification/pat-19-runs/screen-1/` (ledger, results, report, envelope, streams manifest; the 49 MB raw
  streams are not committed). R5: documented in `pat-19-screening-results-v1.md`, linked from `pat-19-launcher-v1.md`
  and `pat-19-protocol-v1.md` (no protocol coordinate changed); no CLI verb, option, public constant or routing table changed.
- Fixed (PAT-112; no model load): the launcher preflight read the LM Studio version from `lms version`, which prints
  only the CLI commit, so every real candidate was refused (`lm_studio_version_differs`). It now reads the app's
  `CFBundleShortVersionString` with the read-only `plutil -extract ... raw` on `/Applications/LM Studio.app/Contents/Info.plist`
  (`LM_STUDIO_VERSION_COMMAND`, in `READ_ONLY_COMMANDS`; `lms version` is no longer allowed); same fact name and
  `contains` comparison against the unchanged frozen 0.4.25; a missing plist refuses as `fact_unavailable:lm_studio_version`.
  R5: documented in `docs/qualification/pat-19-launcher-v1.md` "Préflight"; the public constant `READ_ONLY_COMMANDS`
  swapped `lms version` for the plutil tuple; no CLI verb changed.
- Fixed (PAT-112; fake arms only, no real model/harness/cloud call): the local contamination audit no longer
  hides a sandbox bypass through a system service (`launchctl`, `osascript` are forbidden executables, local and
  cloud; best effort: `crontab`, `at`, an out-of-sandbox `tmux`/`screen` server, `shortcuts run`, `automator` stay unlisted; the "blocked" exemption is decided on the path, not on an observed refusal) and treats the attempt directory
  (the bundle's parent, passed explicitly as `attempt_dir`) as allowed, so `ls ..` is no longer a contamination
  (`ls ../..` still is); a runner-level test proves `sandbox_denied` reaches a local driver's audit and never a
  cloud one. Docs/evidence: the generation_config digest is the only pinned sampling element (effective
  sampling values are unrecorded, limit stated), the 4-bit candidate's missing `loaded_instance_observed` and
  the trials' `pat19-smoke`/`--ttl 1800 -y` load are stated. R5: documented in
  `docs/qualification/pat-19-launcher-v1.md` "Statut documentaire" (`deny_home_trial`,
  `smoke.deny_home_evidence`, `sandbox_denied`, and the new driver key `binary_version`); no CLI verb changed, and the public constant `FORBIDDEN_EXECUTABLES` gained `launchctl` and `osascript`.
- Fixed (PAT-112): the omp version recorded as 18.4.10 was observed before the upgrade; the installed and used one is
  18.6.1 (installed 2026-10-05 17:20, every omp trial ran later), corrected in the evidence, campaign and docs.
  The launcher now runs `omp --version` from PATH and refuses a missing, unparsable or different version
  (driver `binary_version`, checked with the harness executable at the start of `screen`/`compare` and per attempt).
- Fixed (PAT-111, review round 4): a local arm's read that its deny-home sandbox refused (real home outside
  the allow list, or an explicitly denied path) is a blocked attempt, no longer a contamination
  (`audit_transcript(sandbox_denied=...)`); a signal between the bundle discard and the settle of a judged
  local attempt now waits for the record (the attempt is never replayed). Evidence: the four remaining
  candidates passed the toy tool-call trial under the final deny-home profile on 2026-10-05
  (`deny_home_trial` per candidate), plus one cloud control run (heredoc with `gh`/`~/.config` strings,
  audit clean); sampling parameters are declared unpinned. Cloud-only gaps (a call refused by a
  `cloud_bash_deny` rule still counts as contamination; interpreter heredoc bodies are not audited) are
  documented, to be handled before the comparison.
- Fixed (PAT-111, review round 3; fake arms only): the contamination audit no longer reads heredoc bodies given to
  `cat`/`tee`/`python3 -` (a body a shell runs, and the `$(…)` of an unquoted-delimiter body, stay audited),
  `#` comments or separators inside quotes as commands/paths, allows a cloud arm's own Claude Code session
  directory (saved tool outputs), ignores in tool results only the real-home paths the bundle's own files
  contain (`base_literals`, read at bundle build; PR 83's base), expands a local arm's `~` to its isolated HOME, and a cut after the audit keeps
  the contamination on the record (never replayed); the docs no longer claim a cloud arm cannot touch the state files.
- Fixed (PAT-111, review round 2; fake arms only, no real arm run): the contamination audit no longer
  treats text as an access: only path arguments (`path`, `file_path`…, the `pattern` of a Glob/find tool) and
  shell commands are read, not what an arm writes or searches (Edit/Write content, Grep pattern); a tool
  result counts only for a LITERAL absolute path under a sensitive root (`~`/`$HOME` in result text are not
  expanded), and in a command `~` inside quotes or `$HOME` inside single quotes is text
  (`grep -rn '~/.claude' .` is clean; `cat ~/.config/…`, `ls ~/.claude/plugins` stay flagged). A bare `/` is a
  path again as an argument of `find`, `grep -r/-R`, `rg`, `ls`, `du`, `tree`, `cat` (`find / -name x -exec cat
  {} +` is flagged; the heredoc division stays clean). After the arm ran, a git failure taking the patch (stale
  `.git/index.lock`, empty nested repository, unreadable file) or an `OSError` of the judge on a bundle file is
  a `REFUSED` verdict (`candidate_fault`, error recorded, cost kept, never replayed) instead of a void,
  replayable attempt; launcher/environment failures stay void. The reviewer's bundle no longer receives the
  implementer's `.claude/` files (`review_excluded` on the record). A cloud record refused on its `init` tool
  set is still audited (`outcome: contaminated` alongside the `tool_error` status, never replayed). Stated
  limits: the neutral harness's trajectory is not audited (mitigated by the deny-home profile); a local arm can
  signal the launcher (its profile starts from `(allow default)`; the void attempt is replayed at most once
  and listed). Documentation status (AGENTS.md R5): `pat-19-launcher-v1.md` and
  `pat-19-preflight-2026-10-05.json` updated; no CLI option of `foundry_cli.py`, product constant or routing
  table changed.

- Fixed (PAT-111, final real trials): the contamination audit no longer reads a token made only of slashes (`/`, `//`, the division operator of code) as the filesystem root: a legitimate Sonnet 5.5 run whose heredoc held `sum(values) / len(values)` was flagged `/` and would have been recorded `contaminated`. A bare `/` stays a path as an argument of a filesystem reader (see the review-round-2 entry). The final cloud argv (3 drivers) and the deny-home local profile (omp 18.4.10, mini-swe-agent 2.4.6 with `agent.step_limit=40`) are recorded as trial-run on 2026-10-05 in `pat-19-preflight-2026-10-05.json`; the audit is clean on those real streams.

- Changed (PAT-111): launcher corrections left by the PAT-108 reviews in `foundry.local_first_runner`
  (fake arms only; no real arm, no pinning, no `verified` flag changed). SIGTERM, SIGHUP and Ctrl-C are now
  converted for the whole duration of `screen` and `compare` (bundle build, judge, log reading, record
  writing included), not only while a driver runs: the cut attempt leaves an `interrupted` record and a
  `stop`; ledger and results writes are never cut in two; a second signal is never masked; a signal in
  the cleanup of a driver no longer skips the handler restoration or the profile removal; a settled local
  attempt not yet written is recorded as `interrupted`. SIGKILL and power loss remain uncovered (stated in
  `pat-19-launcher-v1.md`). The existing ledger is checked at start against the campaign, manifest and
  envelope digests (a launch refused at preflight no longer lets a modified configuration or envelope
  reuse the campaign id). The rule "economy unavailable as soon as a compared task is undecided" is
  labelled a launcher choice (`non_protocol_choices`). Local attempts are ledgered (`attempt_started`);
  `report` lists every attempt start or passed preflight without a settlement (`ledger.unsettled_starts`),
  warns on the screening (`selected` stays) and makes the comparison `inconclusive`, and refuses a
  comparison whose local candidate is not the one the screening selected (`compare` checks it at start
  when the screening results are in the same state directory; without them the report says so).
  Attempt names carry the launch rank, so a relaunch never overwrites the stream log of a killed attempt.
  A relaunch of `screen`/`compare` under the same campaign id, envelope, config and manifest now
  RESUMES (bounded rule validated by the maintainer on 2026-10-05, before any trial): decided attempts
  (a judge verdict) are skipped and never replayed; an attempt cut before any verdict (`interrupted` or
  `tool_error` without verdict, or an `attempt_started` with no settlement) is void and replayed once on a
  fresh bundle, recorded with `replay_of`; cloud money and the ledger/results invariant are unchanged (a
  void round keeps its cost, each session is named once); `report` lists `void_attempts` and `replays`
  and gives no `selected` while a screening task stays undecided (`undecided_tasks`).

- Changed (PAT-111, review round 1; fake arms only, no real arm run): (1) a cut AFTER a judge verdict or a
  review (local and cloud) now keeps them on the interrupted record, so the attempt is decided and a relaunch
  never replays it (no second chance after a verdict; a cut before the verdict stays void and is replayed
  once); a failure caused by the candidate (git configuration or attributes changed, tree still moving) is a
  `REFUSED` verdict (`candidate_fault`), not a void attempt; a cloud session killed after its settlement and
  before its record is listed in `report.void_attempts`; a contaminated review of path C leaves the task
  undecided with no takeover at first launch and at resume. (2) The unsandboxed cloud arms are documented for
  what they are (bare Claude Code, `bypassPermissions`, real home, open network, implicit credentials, no
  Foundry hook, not the Eiffel/Maigret definitions; the launcher protects neither the tracker, secrets nor
  merge for them): 19 best-effort Bash permission deny rules (`cloud_bash_deny`, data in the campaign config,
  literally in each cloud argv, enforced at load; command-prefix rules, evadable, not a sandbox); the
  post-run audit now covers COMMANDS (`command:` labels, `contamination.commands`), tool results and path
  resolution against the bundle with `cd` tracking (`find ~`, `cd ~ && cat .claude/x`, `src/../../..`); the
  launcher doc and the protocol amendment note are rewritten accordingly and the maintainer's explicit
  acceptance of the residual exposure (2026-10-05) is recorded. (3) Local arms now deny reads under the real home by default
  (`isolation.deny_home_by_default`, true; `isolation.allow_read_home`, empty) with the explicit deny list kept
  as a second layer; unit-tested on the generated profile and under a real `sandbox-exec` with a fake home,
  then tried on 2026-10-05 with the real `omp` 18.4.10 and mini-swe-agent 2.4.6. Also: the preflight refuses a loaded instance with no `modelKey`; the
  neutral harness is given `-c agent.step_limit={max_steps}` and `step_limit_hit` is also set a posteriori
  (attempt refused); `compare` refuses a C/N comparison with no screening results under its campaign id unless
  `--screening-campaign <id>` names a completed matching screening (read-only); the cloud evidence labels the
  command actually tried first (`--disallowedTools Agent` only: 11.6 / 16.3 / 16.5 s) apart from the final
  pinned argv, trial-run afterwards on 2026-10-05 (7.6 / 13.1 / 17.1 s). Documentation status (AGENTS.md R5): `pat-19-launcher-v1.md`, `pat-19-protocol-v1.md`,
  `pat-19-preflight-2026-10-05.json` and the campaign config updated; no CLI option of `foundry_cli.py`,
  product constant or routing table changed.

- Changed (PAT-111, pinning): the five real drivers of the PAT-19 launcher and the five local candidates are
  pinned in `docs/qualification/pat-19-campaign-v1.json` from real toy-task trials (not the corpus) made with
  the maintainer's confirmation of every model load and cloud call; the evidence is committed, sanitised, as
  `pat-19-preflight-2026-10-05.json`. Drivers now `verified: true` with an `evidence` reference (local omp
  18.4.10, neutral mini-swe-agent 2.4.6, Claude Code 2.1.285 for the current implementer `claude-sonnet-5-5`,
  the economy implementer `claude-haiku-4-5-20251001` and the reviewer `claude-opus-5-5`); a verified driver
  without evidence is refused at load. New launcher checks in `foundry.local_first_runner`: the preflight
  reads the loaded instance (`lms ps --json`) and refuses a context below the frozen 65,536, a different
  quantization or model key; `sandbox: false` is accepted only for a cloud driver with a reason and refused for
  any local driver (the cloud drivers run unsandboxed with the AGENTS.md R6 environment, real TMPDIR included,
  because Claude Code cannot authenticate under sandbox-exec); a cloud record is refused (tool error, spend
  kept in the ledger) unless the stream's `system/init` tools are a subset of the driver's `allowed_tools`
  (`Bash`, `Edit`, `Read`, `Write`: observed at the init event of Claude Code 2.1.285 with a 29-name
  `--disallowedTools` list; `--disallowedTools Agent` alone left 25 tools incl. `Task`, web and workflows), and `session_log.layout_verified` is now true on that condition (launcher reading equals the
  host's own usage on three real runs); a post-run audit of every arm's tool calls marks a record
  `contaminated` (undecided, never accepted, never replayed, listed in `report`) when it touched the plugin
  cache, another checkout of this repository or the user's configuration; the neutral harness executable comes
  from the operator variable `PAT19_MINI_BIN` at the pinned version, with its fixed environment, trajectory
  steps and no committed path. Candidates carry LM Studio key, engine, quantization, weights and chat-template
  digests, load command (`-c 65536`) and generation parameters (server defaults, not overridden); Devstral is
  declared unused. Limits stated, not enforced: the cloud and local arms expose different tool sets (recorded,
  not equalised), weights digests are recorded not recomputed; the `lms ps --json` fields (`modelKey`,
  `identifier`, `quantization` {name, bits}, `contextLength`) were observed on 2026-10-05 and are recorded in the
  evidence file (see the review-round-1 entry below for the default-deny of the home and the cloud
  exposure). Documentation status
  (AGENTS.md R5): `pat-19-launcher-v1.md` and `pat-19-protocol-v1.md` updated, no CLI option of
  `foundry_cli.py`, product constant or routing table changed.

- Added (PAT-108): the PAT-19 comparison launcher `foundry.local_first_runner`
  (`python3 -m foundry.local_first_runner {preflight,screen,compare,report}`, documented in
  `docs/qualification/pat-19-launcher-v1.md`, frozen config `docs/qualification/pat-19-campaign-v1.json`).
  It plays a task through three paths from PAT-107 bundles (A current cloud, B economy cloud, C one
  bounded local attempt then cloud review with path A taking over; plus a local-only neutral-harness
  attempt), reads premium tokens per class and role from the host session logs by the session id it
  hands to each cloud execution (unknown stays `null` with a reason, never 0), and records machine
  facts (swap, memory pressure, server memory). A mandatory authorization envelope caps cloud
  executions, tokens and duration (consumption is ledgered before each cloud execution); `screen` can
  never spend cloud; a failed machine preflight refuses to launch and the tool loads no model. The
  candidate runs under a generated `sandbox-exec` profile, a whitelisted environment and an isolated
  HOME; drivers not marked `verified` are refused for a real run (PAT-109 must pin them). `report`
  applies the protocol's pre-registered rules and returns compatibility, quality and economy
  separately, never a promotion. Only fake arms are exercised (dry run). No routing, mapping, role or
  default changed. Review round 1: an interrupted cloud execution (Ctrl-C, SIGTERM, launcher error) kills
  the arm's process group, and a `cloud_started` with no paired `settled` makes tokens unknown and refuses
  any further cloud execution; a local arm cannot read the user's configuration, plugin cache, SSH keys,
  other worktrees or the launcher inputs (explicit read-deny list, a campaign-config coordinate; not a
  default-deny of the home); the patch is taken against the root commit and shows new files; no replay of a
  recorded attempt; results carry `dry_run` and the sha256 of the campaign config, manifest and envelope
  and `report` refuses a mismatch; tool failures are `tool_error` records, an unreadable review is unknown,
  a stop on a cap is `inconclusive`; premium tokens stay unknown until a driver declares its session log
  layout verified; git on a bundle is neutralised and the judged state no longer lives in the candidate's
  tree. The protocol file was amended in place before any trial (candidate 2's repository, three
  downloads), validated by the maintainer on 2026-10-05: see the dated note at the top of
  `pat-19-protocol-v1.md`. `foundry.local_first_corpus` refuses symlinks under the product source
  prefixes; the manifest was regenerated by `verify` (12 of 12 unchanged). Review round 2: the ledger
  and the results can no longer disagree about spent work. An attempt cut by Ctrl-C, SIGTERM/SIGHUP or
  any exception leaves a `status: interrupted` record (premium total `null`) and a `stop` record, and is
  never replayable; each record names its cloud executions (`cloud_sessions`); `report` now requires the
  ledger beside the results and checks that every `cloud_started` is named by exactly one record and
  settled with known tokens: otherwise no economy verdict is given and the decision is `inconclusive`
  (inconsistent files are refused). A cap reached before a correction or a takeover is recorded
  (`stopped_by_cap`) and an undecided task gives no economy verdict; a sandbox configuration error is
  refused before any claim or reservation; signal handlers are restored exactly and a signal during the
  arm's start still kills it; the read-deny list gains credentials and shell history files and the
  generated profile is hidden from the arm; results are synced to disk and a truncated ledger or results
  line is a clean refusal. The only way out of a blocked campaign is a new campaign id and a new envelope.

- Changed (PAT-108): carry-over of the PAT-107 reviews in `foundry.local_first_corpus`: the judge purges
  every `*.pyc`/`__pycache__` of the candidate and redirects bytecode (`PYTHONPYCACHEPREFIX`), refuses a
  candidate inside the developer checkout and a bundle already judged, ignores virtualenv and
  `node_modules` directories the candidate created, and always passes `-c`; `verify` aborts on a tooling
  error instead of replacing the task; `replayability.origin_refs_containing` became a count
  (`origin_refs_containing_count`) so the manifest is reproducible. The 12 tasks are unchanged and
  still judged 12 of 12.

- Added (PAT-107): PAT-19 local-first qualification protocol v1 frozen under
  `docs/qualification/pat-19-protocol-v1.md` and a replayable corpus
  (`docs/qualification/pat-19-corpus-v1.md`): `foundry.local_first_corpus` freezes merged PRs in a
  committed snapshot (tracker statements from the committed, scrubbed
  `pat-19-corpus-statements-v1.json`, sha256 recorded), applies the protocol's inclusion/exclusion
  criteria with a recorded reason per PR (accent-insensitive title rules; manual include/exclude
  overrides with a mandatory reason), draws 6 comparison + 6 screening (`tamis`) tasks with a seeded
  sha256 ranking (the sets never overlap, and are not independent: see the corpus limits), builds a
  bundle as a brand-new one-commit repository at the base tree without the protected tests and with no
  link to the developer repository (no worktree, remote, alternates or merged SHA), and judges a
  candidate mechanically by restoring and running only those tests from the merged SHA in a scrubbed
  environment (temporary HOME, whitelisted variables, `--confcutdir`, REFUSED on harness-file edits or
  symlinks). The judge accepts the merged solution and refuses the base for all 12 drawn tasks. No
  model is run and no cloud/tracker call is made.

- Changed (PAT-106): the Linear ADR readback model recognises one more observed rendering
  (`docs/qualification/pat-106-linear-paragraph-list-observation.json`, native `PAT-ADR-0015`):
  a top-level `- ` list directly after a single-line paragraph line starting with a letter is read
  back with one empty line inserted between them; a list glued to any other non-heading line (pipe,
  `#tag`, digit, `**`, backtick...) is refused. Reads stay additive (the PAT-103 and pre-PAT-94 renderings
  are still accepted) and the pinned profiles are unchanged. A new body is now refused before
  any write, with a precise cause, for the unobserved neighbours: numbered, `*`/`+` or dash-only
  (setext) glued lists, lists glued in or next to a blockquote (the exact quote depth must match
  the line above; a `>` behind 4+ columns, a tab or a list marker, or opened directly under a
  list line, is refused), under a `--` line, under an indented line that is
  not inside a list item (including indented code starting with a marker), indented under a
  paragraph, indented (code) under a heading, thematic break or closing fence, or outside the
  exact shape; the check is a closed whitelist whose default refuses. Replaying `adr create` recognises the orphan Document of
  `PAT-ADR-0015` and writes only its witness. See `docs/linear-adr-body-guide.md`.

- Changed (PAT-103): the Linear ADR readback model and preflight now recognise the blank-line
  rendering observed by a bounded probe
  (`docs/qualification/pat-103-linear-blank-lines-observation.json`): two or more empty lines
  outside fenced code read back as one, a leading empty line and the final newline(s) are
  removed, fenced code is unchanged. A new body is refused before any write for unobserved
  neighbours (whitespace-only or CRLF blank lines, indented or quoted neighbours, a final
  newline after a fence or in an unclosed fence), for a body of empty lines only and for
  every table, whose delimiter row Linear rewrites; the same refusals now also apply before
  any write to the unchanged body of a migrated historical version 0 the model does not predict (status change, link,
  supersession). Pinned `PAT-72`/`PAT-86`/`PAT-16` profiles and the pre-PAT-94 model are
  unchanged.
  New guide: `docs/linear-adr-body-guide.md`.

- Fixed (PAT-101): residuals of the PAT-94 Linear ADR list preflight. A body that starts with an
  empty line next to a list is refused as a gap of two (the Document already adds one); task items
  (`1. [ ] a`, `1. [x] a`) are not plain items of the observed form; an interrupted supersession
  resume no longer refuses a dangling slot the model already validated (only its witness is
  written); the digest-pinned `PAT-72`/`PAT-86`/`PAT-16` profiles now apply before the strict list
  check, so a new ADR identical to a pinned source is accepted. No write path or format changed.
  See `docs/linear-tracker.md`.

- Fixed (PAT-102): residuals of the PAT-95 `close-epic` graph diagnostic. The "Seuls des
  reçus d'override valides bloquent" suggestion appears only when the strict refusal is itself an
  override refusal, otherwise it reads "Parmi les nœuds lus, seuls ...". The diagnostic captures
  the `SystemExit` a tracker (YouTrack) raises for a foreign node, keeps walking and falls back to
  the original refusal. `ValueError` is `foreign-project` only for an identifier or binding error;
  `JSONDecodeError`/`UnicodeError` are a `read-error`. "dérogé" is reserved for an override:
  a `dropped` node is "abandonné". Documentation fixes ("two issue reads", the `SKILL.md`
  cross-reference, one unwrapped line). No write behaviour, audit id or receipt format changed.

- Fixed (PAT-100): `close-epic` interruptions and refusals state the real closure state
  instead of a fixed "le reçu provider permettra la reprise". After a failure it reads,
  read-only, the provider audit and the local intent and says: no audit and an intent store
  actually read and empty (nothing written, re-run safe; any tracker whose intent store is
  not read, e.g. DevHub, is "intention locale non vérifiée", never safe; YouTrack's journal
  is read), intent without visible audit (ambiguous, a re-run is refused until the audit is
  visible, never reposts), audit pending (re-run with the exact `--accept-override` set of
  the audit, never the refused command), audit and Epic done (replay verifies), or state unreadable
  ("état du reçu inconnu", nothing claimed), with the audit id. Without a receipt it names the
  original cause (quota with remaining/reset and no immediate re-run, network/5xx with retry
  count, conflict, or exception type). A provider read error during the strict snapshot is
  intercepted like a conflict. Added `close-epic <EPIC-ID> --status` (read-only: `aucun audit`,
  `audit en attente`, `clos`; exit non-zero only on a failed read). No write, receipt, audit
  identity or replay rule changed. See `docs/linear-tracker.md` and `skills/close-epic/SKILL.md`.

- Changed (PAT-99): reading a closed Epic or taking a graph snapshot reads each node at most
  once per snapshot (`LinearTracker.graph_snapshot()`, used by `bounded_epic_graph_snapshot`
  and the PAT-95 diagnostic): N + 1 reads for N nodes instead of about 5 N (fake graph of 31
  children + 24 prerequisites: snapshot 269 -> 56, closed-Epic read 270 -> 57, diagnostic
  110 -> 55, whole `close-epic` 1118 -> 266). The pre-write S1 and the post-write
  verification stay independent fresh snapshots: no reuse across snapshots, calls or the
  write; closure semantics, receipts and fail-closed detection are unchanged. See
  `docs/linear-tracker.md`.

- Added (PAT-98): the Linear client retries a pure read at most 3 times (deterministic waits
  of 1 s, 2 s, 4 s) on a network failure (`transport_error`) or HTTP 500/502/503/504, never on
  data, binding or authorization errors, and never a write (PAT-ADR-0006 S2). A rate-limit
  failure (HTTP 429, a `RATELIMITED` code, or HTTP 400 with `x-ratelimit-requests-remaining`
  at zero) raises the new `LinearQuotaExhaustedError` carrying the remaining count and the
  reset time, without retry. The number of retries is exposed (`retries`, "after N retries"
  in the message). See `docs/linear-tracker.md` ("Transport errors, read retries and quota").

- Fixed (PAT-105): residuals of the PAT-98 review. A malformed error body, a non-string
  GraphQL error code or an aberrant reset header can no longer replace the typed error; 429
  is no longer in the retryable statuses; a zero remaining header on an HTTP 200 with an
  unrelated GraphQL error is no longer a quota; the quota message names `requests` or
  `complexity`; `tracker.retry_stats` counts retries, recovered and exhausted reads. The
  7 s bound covers waits only: with the 15 s attempt timeout the worst case is about 67 s per
  call. `tracker.rate_limit` and `retry_stats` are diagnostic-only across threads.

- Fixed (PAT-104): the test suite can no longer read or write the maintainer's real
  `~/.config/foundry`. A global autouse fixture in `tests/conftest.py` redirects `HOME` to a
  per-test temporary directory and clears `FOUNDRY_DATA`, `FOUNDRY_CONFIG` and
  `FOUNDRY_EXECUTION_RECEIPTS_DIR`; an audit hook fails any access to the real state
  directory, and `tests/test_state_isolation.py` asserts every resolver is sandboxed.
  Test infrastructure only, no production change.

- Added (PAT-95, PAT-ADR-0014): `close-epic` on Linear accepts the nominative flag
  `--accept-override=ID[,ID...]` (only with `--human-verdict=accepted`) to close an Epic
  whose nodes were delivered under a valid typed `acceptance-override` receipt. Refusals
  now list every non-positive graph node with its cause (read-only) instead of the first
  one, and the generic refusal no longer hides the real cause. The closure receipt binds,
  per waived node, its id, override-receipt digest and reason code (receipts without
  waiver keep their exact historical bytes); exact replay converges, a different set is
  refused, nodes stay `override`, the flag is neither CAS nor acceptance. YouTrack,
  GitHub Projects and DevHub refuse it. New public surface: the flag and
  `Tracker.epic_override_closure_supported`. The node list is carried by refusals of the
  fresh graph read only (not replay, pending-audit, provider-S1, unknown-id-alone or
  snapshot-transport refusals); a node that cannot be read is a `read-error` (sub-graph
  not traversed, the list is a lower bound, re-run first), `foreign-project` is reserved
  for binding refusals and `binding-error` marks a configuration error. Observable
  change: `query issue` `acceptance_coordinates` of a node under override now also carries
  `pr_url`, `head_sha`, `base_sha`, `review_digest`. The id grammar is
  `[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,8}`.
- Fixed (PAT-94): the Linear ADR readback model now recognises the one observed rewrite
  of a spaced top-level ordered list (`1.` to at most `9.`, +1 per item, one plain LF
  line per item, one empty LF line between items: those empty lines are removed). By
  whitelist, a new ADR body (create, body edit, single historical import) holding any
  other empty line in a list context, or two or more empty lines just before or after a
  list-context paragraph (`PAT-72`: Linear collapses them after a list item), is refused
  before any write with cause `unsupported list Markdown`, followed by the first
  offending line number. Status changes, links, supersessions and `import_adr` relations
  re-use the stored previous body without the strict check only when the closed model
  verifies its stored bytes; a historical version 0 whose bytes only the migration probe
  proves is checked as a new body, so such an ADR with an unmodelled list shape is
  refused before any write instead of leaving an orphan Document; an interrupted
  supersession pair is recovered under the same rule. Other recoveries of an interrupted
  write (an existing exact slot) and the probe-pinned migration batch keep the
  pre-PAT-94 checks only. Reads accept the canonical bytes, the PAT-94 rendering or the
  pre-PAT-94 model output. A replay of an interrupted `adr create` recovers only the
  missing witness.
- Documentation only, 2026-10-04: Foundry 1.0.0 is published (tag `foundry-v1.0.0`,
  2026-10-03). The frozen 1.0.0 entry and release documents below keep their shipped
  "candidate" wording; the published state, installed-copy readbacks and the items not
  yet observed are in the
  [final report](docs/qualification/pat-62-final-report.md) and the
  [post-publication observation](docs/qualification/pat-93-post-publication-observation.json).

## 1.0.0 — 2026-10-03

Prepared release candidate; publication, official installation/upgrade and loaded
version/ref readbacks remain pending. See [release notes](docs/release-1.0.0.md)
and [setup/migration](docs/migration-1.0.0.md). This dated source entry is not a
claim that 1.0.0 is already published or installed.

### Added
- The V1 distribution packages for Claude Code and Codex, both declaring 1.0.0,
  based on accepted PAT-61 (PR #72, merge `16cdaa0`). Its six qualified source
  configurations cover YouTrack, Linear and private personal GitHub Projects on
  both hosts; historical producer refs and unknowns remain unchanged.
- Short per-repository setup, official dual-host upgrade from 0.9.0, explicit
  legacy YouTrack binding upgrade, loaded-version verification, and configuration
  rollback limits. Installing a package performs no tracker migration.
- An explicit compatibility matrix with unchanged Ship-iOS 0.3.0 and its portable
  V1 selection/release-scope capability probes; no App Store effect is included.
- The qualified private personal GitHub Projects adapter now carries Foundry-owned
  in-progress/review/done and acceptance receipts bound to the exact repository,
  Project, Issue, item and PR generation. A live private sandbox PR passed independent
  review and exact-SHA CI before merge; native auto-close alone grants no authority
  (PAT-67).
- `query changelog <release>` now emits `foundry.release-scope.v1` from exact
  repository-scoped release mappings on YouTrack, Linear and GitHub Projects. The
  payload keeps Ship-iOS's `milestone`/`count`/`groups` projection and adds factual
  accepted, deviated, unfinished and unavailable classes; a native Done or PR mention
  alone is never delivery proof (PAT-59).

### Fixed
- R1 remains closed on invalid, drifted or ambiguous tracker binding (PAT-42),
  including before forbidden PR/merge/default-branch push commands can execute.
- YouTrack delivery receipts now distinguish accepted, deviated and unavailable
  release facts (PAT-82); older terminal issues without qualified receipts remain
  unavailable, without fabricated backfill.
- Proof-bound `openpr` and `merge` now pass the original predecessor state to
  bounded-state adapters, so a GitHub Projects PR can resume after a partial
  transition without weakening the common transition guard (PAT-67).
- YouTrack now proves a targeted issue or ADR's native project coordinate before every
  write and rejects it when any alias tombstones that provider project (PAT-43). The
  guard runs before milestone setup, command links, body writes, comments, and
  project-explicit issue or ADR creation, so an unrelated or unregistered checkout
  cannot write to the readable archive.

### Changed
- Bounded targeted grooming writes on YouTrack, Linear and GitHub Projects and
  audited non-atomic Epic closure carry PAT-ADR-0006's named S1→S2 overwrite risk;
  read/write/readback is detection, not provider CAS or concurrency exclusion.
  Linear delivery and AC authority remain proof-bound; native checkbox replacement
  remains refused. GitHub ADR Issue supports use an integrity-checked version chain.
- The delivered PAT-15/PAT-16 technically qualified defaults: Claude Haiku 4.5
  without effort, Sonnet 5.5/medium and Opus 5.5/high for frontier/apex; Codex
  GPT-6 Luna/low and GPT-6.1 Sol/medium/high/max. Minimum Claude client 2.1.284 for
  the complete set, actually tested 2.1.285/firstParty/Pro. Existing pins remain
  exact; observed access, effort and source/install limits are in the release notes.
- Package descriptions now announce only qualified provider variants. DevHub remains
  optional outside V1; Fable/Astra and economic superiority are not promoted.
  PAT-17/PAT-18/PAT-19 remain outside V1. Historical 0.9.0 evidence is preserved.

## 0.9.0 — 2026-09-26

### Added
- A fail-closed Linear tracker adapter using explicit repository, team, project,
  workflow-state, label, and milestone identifiers, resolved from the actual checkout's
  canonical Git remote rather than an environment alias or basename. It supports strict
  issue reads, creation, and non-replacing relations/comments without promising
  exactly-once delivery. Existing issue field/state/parent/body replacement stays
  typed-unavailable because Linear exposes no atomic anti-overwrite precondition.
  `issue openpr` and `issue merge` instead use deterministic-ID, append-only comments;
  Foundry validates and projects their PR/state/AC receipts without changing native
  Linear fields. Complete cockpit Evidence Plane envelopes are append-only advisory
  audit records and never state, AC, CI or merge authority. Linear stores ADRs as
  versioned, witness-bound project Documents (PAT-22); atomic Epic closure remains
  unavailable; this repository is cut over to Linear project PAT (PAT-10).
- `query issue` reports an explicit `conflict` status instead of presenting a
  conflicted embedded ADR index as empty, so a caller can tell "no ADR" apart from
  "the index disagrees with itself" (PAT-41).
- Tolerant Markdown serialization handling for Linear's own rendering of the ADR
  delimiter and angle-bracket content, so its reformatting never masks a real
  alteration, while byte-exact source bodies stay verifiable through witnesses
  (PAT-37/PAT-38/PAT-39).
- `plan_adr_batch_qualification`, a read-only planner that performs no provider write
  and returns the exact version Document and witness bytes, and probe titles, a
  historical batch import would create; the import itself reads every non-authoritative
  qualification probe by ID and checks its complete readback before any effect
  (PAT-40).
- Explicit `missing_relations` on the records of a partial-source historical ADR
  manifest, so each unknown relation family is listed in a canonical, sorted tuple
  rather than omitted or presented as silently empty, and the audited import of the
  repository's 27 historical ADRs into Linear (no private data), which declared all
  three families unknown (PAT-23).
- A repository-scoped `.foundry/tracker.json` marker that takes precedence over the
  host-global tracker default, a `registry cutover` command that binds a project by its
  exact manifest digest, an archive tombstone recording the former YouTrack binding, and
  an operations log capturing `adr_authority` and `incidents` for the cutover (PAT-10).
- A typed `acceptance-override` receipt for a human AC override merge on Linear, with
  recovery replay so an interrupted override merge can resume from its exact receipt
  instead of re-deciding the override (PAT-49).
- Public-repository main-branch protection, read back from the actual GitHub branch
  protection rules rather than assumed (PAT-12).
- A dedicated, audit-bound `rearm-remediation` transition for a just-exhausted bounded
  correction window that is not yet halted. It CAS-binds the issue, recorded role,
  original halt generation, controlled human reason, and 1..3 new credits; preserves
  counters, tier floors, and every consumption event; appends a public link to the
  exhausted window; and exposes identical state through Claude and Codex without
  weakening ordinary halted-only resume.
- An outbound DevHub command worker with exact claim/heartbeat bindings, bounded live
  leases, command-ID ambiguity recovery, monotonic local receipts, last-moment gate and
  authority revalidation, and a concrete Claude execution path whose model floor and
  dollar ceiling are fixed before the effect. A host-shared SQLite ledger now reserves
  observed budget and concurrency atomically across worker processes before launch,
  settles terminal cost durably, and keeps ambiguous/crashed allocations fail-closed.
  The command credential remains limited to claim/event calls and is removed from the
  spawned runtime environment.
- A deterministic, read-only Epic execution preview over Dev Hub's complete versioned
  subgraph. It fails closed on truncated, inconsistent, cyclic, or schema-invalid
  projections; classifies eligible, blocked, omitted and human-gated work; derives
  concurrency-capped dependency waves; resolves planned model tiers with risk floors;
  and binds the snapshot, effective policy, limits, expiry and unknowns in one stable
  digest. The preview exposes no branch, agent, command, budget reservation, tracker
  mutation, merge, or other execution authority.
- A dedicated provider-neutral non-code Epic closure command and Tracker contract. Its
  receipt binds the exact project, parent version and AC, complete canonical child
  id/version/state set, timestamp, and nonce; a supporting provider must verify and
  audit the graph atomically and expose the receipt for interruption recovery. The code
  issue merge-receipt gate is unchanged. DevHub Tracker v1 and YouTrack explicitly
  report this capability unavailable rather than emulate it with a racy read/write.
- An opt-in DevHubTracker v1 provider with normalized project, issue, link, comment,
  and ADR operations; bounded HMAC receipts for review/done/ADR transitions; versioned
  idempotent writes; repository-bound mutation guards; hard-capped pagination; sanitized
  public audit verification; and an explicitly destructive real-provider smoke covering
  optimistic concurrency, replay and link/comment round trips. Mutation binding compares
  the actual credential-free Git remote identity to `canonical_repo`; review and AC
  proofs use GitHub's exact immutable PR base SHA and reject a stale coordinate; doctor
  validates the transport from a narrow public-config read before parsing or reading
  DevHub credentials. Configuration and registry writes validate and canonicalize URLs
  and repository identities before persistence, while legacy unsafe values stay redacted
  and fail closed before transport. An already-done retry
  performs cleanup without a second merge or illegal rewind. YouTrack remains the
  default and no routing, gate, review, or merge authority moves to Dev Hub.

### Fixed
- Linear lifecycle fixes: blocking premature GitHub closure of a Linear-tracked issue
  (PAT-26) and tolerating a Backlog-to-In-Progress transition after a PR is already
  attested (PAT-28).
- Tracker-neutral escalation and bounded-remediation fixes: controlled resumption after
  exhausted technical diagnostics (PAT-27); rearming an exhausted window after a
  technical diagnostic generation (PAT-29); making remediation usable after a review on
  an already-consumed technical route (PAT-30); allowing exactly one new review after a
  credited correction on such a route (PAT-31); and rearming a corrected review across a
  PR base advance (PAT-32).
- Documented and tested contracts without a product-code change: resuming the Linear
  cutover after technical remediation without widening its permissions (PAT-21), and an
  offline test double proving the provider handoff stays idempotent and capability
  stays valid after a crash (PAT-24).

## 0.8.1 — 2026-09-13

### Added
- `query profile <workflow>`, a bounded query projection for `next-issue`, `roadmap`,
  `blockers`, `groom`, and `intake`. `next-issue` now costs 29 761 bytes instead of the
  138 165 bytes of the previous `query backlog` call it replaced for that workflow.
- `cost-attribution`, an offline host-log cost attribution command reading a versioned,
  dated price grid. PROVENANCE is aligned on FOUNDRY-ADR-0008, exposes `billing_mode`,
  and aggregates by epic, issue, project, and model.
- Cost per position within a session and session comparison curves, plus
  `plan_usage_percent` on Codex.

### Fixed
- A Claude host log containing records without a reasoning breakdown is priced instead
  of aborting the whole log; `reader_completeness` reports what is affected.
- Codex cost over-counting: `total_token_usage` is read as cumulative per session, not
  summed per message.

## 0.8.0 — 2026-08-29

### Added
- A structured acceptance-criteria proof for the pre-merge gate. The reviewer returns
  ordered AC outcomes and one quality verdict; Foundry binds them atomically to the
  current issue AC digest, local `HEAD`, exact base diff, active review claim, and
  reviewer generation. Stale, malformed, mixed, or legacy evidence fails closed. The
  explicit human `--allow-incomplete-ac` path remains audited and never edits tracker AC.
- The versioned FOUNDRY-46 dual-host benchmark evidence and FOUNDRY-47 publication
  decision. The 108-coordinate v2 matrix remains separate from product activation:
  Claude and Codex are both `inconclusive`, every unavailable cost/downstream/test/
  review/allocation field remains `null`, and the unweighted cross-host aggregate is
  `null` and forbidden.

### Changed
- Published one shared Foundry implementation as two aligned 0.8.0 host packages. The
  Claude and Codex catalogues keep their supported source-pointer schemas and therefore
  deliberately contain no invented `version` field; version authority remains in the
  two package manifests.
- Updated install, upgrade, configuration, doctor, host-override diagnosis, source
  verification, and exact-ref rollback guidance for both Claude Code and Codex.

### Unchanged production contract
- The recommended ordinary main profile remains Sonnet 5 / medium on Claude Code and
  GPT-5.6 Terra / medium on Codex. Foundry cannot force the already-open main
  conversation. Reviewer stays at least frontier/high, architect at least apex/high,
  and the existing maximum of two tier increases per issue remains in force.
- No model, effort, context policy, route, default, gate, fallback, escalation signal,
  or local authority changes in 0.8.0. No local candidate is promoted. The historical
  7.42% price-snapshot observation is noncausal and is not a savings claim.

## 0.7.0 — 2026-08-22

### Added
- An operator-controlled local preprocessing provider, disabled by default and confined
  to an explicit loopback-only OpenAI-compatible endpoint. The local model is untrusted,
  evidence-bound, read-only, outside Foundry's roles and tiers, and can never approve a
  gate, write to the repository, select a cloud model, or gain merge/release authority.
- Shared `diff`, `logs`, and `tests` capture modes with immutable bounded snapshots,
  sensitive-path filtering, secret redaction, typed locators, wrapper-owned evidence,
  stale-input detection, and strict response-schema validation. The two-pass `code` mode
  exposes a body-free manifest first, then a frozen evidence bundle; it cannot add an
  adaptive third pass.
- Trusted local-scout policy for explicit model and loopback destination selection,
  conservative 2-second connection and 8-second total defaults, hard 10/120-second
  ceilings, project limits that may only tighten budgets, and an opt-in
  `cloud_economy` failure plan. Policy violations and stale input always stop; the
  fallback never performs the cloud call itself and remains subject to the production
  ADR-0006 scout route.
- A read-only doctor view for local preprocessing. It reports
  `disabled`/`configured`/`available`/`unavailable`/`invalid policy` using at most one
  bounded loopback TCP connection and never completes a model or reveals its endpoint,
  identifier, or secrets.
- Passive opt-in routing telemetry with private one-time completion/outcome capabilities
  and a bounded append-only local journal. It is fail-open, never uploads, never records
  prompts, responses, code, paths, URLs, free-form errors, or secrets, and never feeds a
  current or future routing decision.
- The frozen, candidate-agnostic FOUNDRY-35 benchmark campaign, including raw evidence,
  aggregate results, a deterministic post-run addendum, attestation, nullable cost
  semantics, and offline reproduction. No local candidate is promoted: all four failed
  the mandatory product-schema and downstream-correctness gates, achieved only about
  0.32% median cloud-input reduction against a 40% threshold, and had no applicable
  immutable public price, so all cost reductions remain `null` rather than zero.

### Changed
- Model, reasoning effort, and context policy are documented and observed as separate
  dimensions while the production resolver remains exactly the ADR-0006 tier mapping.
  There is no adaptive routing, reasoning ladder, production shadow call, or new routing
  signal in 0.7.0.
- Issue escalation can expose a human-authorized remediation window of one to three
  credits after the existing stop. Credits apply only to the exact stopped correction
  role and `review_blocking_after_fix` signal, are durably audited with controlled
  values, and do not renew the two-escalation cap, lower a floor, or widen capability.
- Claude's logical-agent façade now matches the real Agent wire: short provider aliases,
  dynamic `model` and `max_turns`, static effort-only execution profiles, strict bounded
  task packets, current availability normalization, and explicit host-override warnings.
- The recommended main coordinator remains the balanced profile: Sonnet 5 / medium on
  Claude Code and GPT-5.6 Terra / medium on Codex. Reviewer and architect retain their
  frontier/high and apex/high-or-max floors and upward-only fallback.
- Installation, upgrade, 0.6.x migration, dual-host configuration, architecture, local
  privacy/fallback behavior, benchmark results, limits, and the next required experiment
  are now collected in the README and the 0.7.0 migration/release guides.

### Fixed
- Wired opt-in passive telemetry to Claude's real post-Agent callbacks and Codex's
  required post-spawn skill lifecycle, with private bounded one-time capabilities,
  consume-time 24-hour expiry, one cross-process lock for atomic bounds/replacement/
  consumption, replay/forgery and symlink resistance, correct fallback/override
  counters, and recovery from interrupted append/capability fragments. Claude's real
  hook is now entirely silent and records only its coarse callback status; Codex skills
  must pass an explicit truthful success/failure/timeout/cancellation/unknown class.
  Observation cannot change route, packet, spawn, retry, escalation, gates, results, or
  provider behavior.

## 0.6.2 — 2026-08-20

### Fixed
- Removed the redundant Claude manifest declaration of the conventional
  `hooks/hooks.json` file, which Claude Code already loads automatically and rejected
  as a duplicate during real 0.6.1 installation. The dual-host validator now models
  Claude's automatic hook discovery and optional additional hook file independently
  from Codex while preserving path confinement and semantic-divergence checks.

## 0.6.1 — 2026-08-20

### Added
- An explicitly opt-in real YouTrack smoke test covering the public issue/link/state/
  comment/ADR cycle, with disposable-project guards, per-run audit markers,
  secret-safe diagnostics, provider-local idempotent cleanup, and a separate
  variable-gated CI job.
- A host-neutral semantic model-routing contract with four tiers and five role
  defaults, project and user overrides, field-level precedence, visible downward
  fallback for ordinary roles, and upward-only fallback for reviewer/architect gates.
- A read-only `routing show`/`routing resolve` command, atomic cross-host review
  deduplication keyed by the exact diff hash, and a value-free common warning contract
  for host overrides that can neutralize the resolved policy.
- Claude Code logical roles for scout, implementer, reviewer, and architect, dynamically
  rewritten at Agent invocation time so project/user model policy survives static plugin
  frontmatter. Bounded task packets and turns, static effort-only execution profiles,
  explicit availability fallback, and a fail-closed Bash guard preserve gate/read-only
  guarantees.
- A Codex invocation façade that produces exact fresh-context subagent descriptors with
  resolved model and reasoning effort, explicit availability fallback, value-free
  profile-override warnings, common diff-hash review deduplication before spawn, and a
  disclosed current-context fallback when subagents are unavailable.
- Deterministic cross-host escalation state keyed by repository, issue, and role: only
  red tests/blocking reviews or explicit risk/ADR/user signals count; floors persist for
  the issue, two tier increases are allowed, and further work halts for a human. Dynamic
  floors cannot be demoted by direct model/tier requests or availability fallback and
  never expand agent capabilities or external authority.
- A CI-pinned resolved-invocation contract, minimal project-routing example, documented
  role/primary-profile/host limits, and frozen manual `FOUNDRY-MR-PILOT-v1` worksheet
  for measuring main-loop and subagent cost without automatic telemetry.

### Fixed
- Milestone rollups and dependency analysis now treat Foundry `done`/`dropped` and
  YouTrack `Fixed` as terminal without depending on provider capitalization, while
  changelog generation remains restricted to work explicitly marked `done`.
- Fall back from GitHub's unavailable aggregate check-runs endpoint to paginated
  check-suite runs without weakening or waiving the merge gate.
- Repo renames can now add a safe registry alias without provisioning a duplicate
  tracker project; `doctor` groups aliases by distinct project identity.
- Bootstrap ADR import requires and is idempotent by filename ID rather than mutable
  title, and preflights unnumbered files and numbering gaps before any tracker write,
  preventing shifted ADR IDs.

## 0.6.0 — 2026-07-21

### Added
- Native Codex packaging (`.codex-plugin/plugin.json`) beside the unchanged Claude Code
  package, with one shared set of skills, hooks, tooling, tests, and release history.
- `foundry:configure`: host-neutral non-secret configuration, interactive macOS Keychain
  token storage, redacted diagnostics, and an install marker sibling plugins can resolve.
- Portable `foundry:review-pr` skill mirroring the Claude reviewer agent's two-stage
  acceptance-criteria + quality gate.
- Linked-worktree-aware issue start/merge behavior for Codex-managed worktrees.

### Changed
- Skill commands use Claude's deterministic `CLAUDE_PLUGIN_ROOT` expansion when present,
  with a `SKILL.md`-path fallback required by Codex (which exposes plugin-root variables
  to hooks, but not to skill-initiated shell commands). Docs and hook guidance expose
  both Claude `/foundry:*` and Codex `$foundry:*` invocation forms.
- Configuration now resolves direct environment variables, Claude plugin options,
  Keychain, then the Foundry config file. Registry data lives in one shared path —
  `FOUNDRY_DATA` when explicitly set, else `~/.config/foundry` — and deliberately
  ignores the hosts' plugin-private `PLUGIN_DATA`/`CLAUDE_PLUGIN_DATA` dirs: hosts
  inject those into hook commands but not skill commands, which would split the
  registry into two silently diverging views.
- Restored non-empty Claude Code argument hints after verifying that Codex 0.144.6
  installs and invokes skills carrying the unknown key without a related warning; the
  shared validator now accepts only short, quoted, one-line hint strings.
- `SessionStart` hook matcher covers `resume` too: a resumed session gets the Foundry
  contract injected exactly like a fresh one.
- `issue merge` now reports the squash SHA that landed on the default branch
  (`sha mergé <sha>`): release tooling (ship-ios) tags exactly that commit, and
  `origin/<default>` would be racy if another PR lands in between.

## 0.5.0 — 2026-07-02

### Added
- **`query changelog <milestone>`** — the shipped (`done`) issues of a milestone,
  grouped by type, as locale-neutral facts (id / title / type / labels). It is the
  clean SOURCE a release tool consumes to write user-facing release notes per target
  locale; Foundry emits facts, never prose, never a hardcoded language. Platform- and
  locale-neutral: any project cutting a version wants "what landed in vX". `dropped`
  issues are excluded (they never shipped).

## 0.4.1 — 2026-07-02

### Fixed
- `list_adrs`: the ADR prefix match is ANCHORED and escaped (`re.match` +
  `re.escape`) — a project key that is a suffix of another ("TOC-ADR" inside
  "MDTOC-ADR-0001") cross-matched and polluted the index/numbering. Found by
  external blank-context review (F1); suffix case pinned by test. The
  instance-wide article scan and its 1000 ceiling are documented inline
  (proper server-side scoping tracked in #17; secret-gated adapter smoke in #18).

## 0.4.0 — 2026-07-02

### Changed (token posture: summary first, detail on demand)
- **Lean list payloads**: `query backlog`/`candidates` no longer carry issue bodies
  (fields, links, AC counts, ranks and statuses carry the reasoning signal); the
  free text comes via `query issue <ID>`. In `query issue`, related issues are lean
  too — only the requested issue keeps its body and progress notes.
- **ADR index + drill-down**: `query adrs` returns id/title/status/ref only; new
  `query adr <ADR-ID>` returns one full ADR. Retrieval-before-reasoning becomes
  constant-cost: scan the index always, load only the ADRs touching the topic.
  The `adrs` section of `query issue` is the index as well.
- Skills updated to the index → detail pattern (frame, intake, adr, blockers,
  roadmap, start-issue, resume-issue; groom and intake's refine path explicitly
  drill down before ruling on AC text — never judge unloaded text).
- **FOUNDRY-ADR-0003** records the decision and amends ADR-0001's "JSON riche" /
  "on charge tout" clauses (inline notes + `amended_by`), matching the ADR-0002
  precedent — the lean posture no longer silently contradicts the founding ADR.

### Hardened in review
- `query issue` tolerates unreachable related issues (deleted / cross-project →
  marked entry instead of a crash) and dedupes link targets; `query adr`/`issue`
  without an id exit with a clean usage line; `query adrs <ID>` (the natural
  near-miss) delegates to the drill-down; the ADR miss path no longer re-fetches
  the whole knowledge base; SessionStart contract teaches index → detail.

## 0.3.1 — 2026-07-02

### Fixed
- `ci_expected` no longer deadlocks the `--allow-no-ci` waiver: a stale
  queued-empty check-suite (schedule-only workflows keep one forever) doesn't
  count as active CI — only suites in progress, with runs, or queued for less
  than 15 minutes (the post-push window) refuse the waiver.
- `issue merge` no longer recommends `--allow-no-ci` in the very message that
  refuses it.
- Docs aligned with the gate's reality: merge-pr skill (two sources, ≥1 success),
  CodeHost port header (three methods), waiver wording (active-CI signal, not
  "no suite exists"), issue CLI docstring; ADR-0001 ↔ ADR-0002 now cross-linked
  (`amends`/`amended_by` + inline note on the amended clause); ADR-0002 documents
  that legacy-only CIs have an undetectable post-push window.

## 0.3.0 — 2026-07-01

### Added
- **CI gate reads both sources** (ADR-0002, closes the Jenkins blind spot): new
  `CodeHost.commit_statuses()` (legacy status API, paginated) merged with check-runs
  in `ci_gate` — a red legacy status blocks even with zero check-runs, and
  `--allow-no-ci` waives only when BOTH sources are empty AND no active check-suite
  signals CI for the commit (`CodeHost.ci_expected` — the post-push window where CI
  is queued but invisible is not waivable). Pagination of both reads terminates on the
  response's own `total_count`, never on batch size. Port change: code-host
  adapters must now implement `commit_statuses` and `ci_expected`.
- **FOUNDRY-ADR-0002** — the gate semantics, decided and written down: green means
  proven green (≥ 1 `success`, nothing red/pending, `neutral`/`skipped` tolerated
  alongside, never alone); amends ADR-0001's literal "all success" clause.
- **`rank_in_view`** in query payloads: dense 0..n-1 rank over the returned view,
  alongside the global (gappy-when-filtered) `rank_index`.

## 0.2.0 — 2026-07-01

### Added
- **Plugin hooks** (`hooks/hooks.json`) — the process becomes active without invoking a
  skill, in registered repos only:
  - `SessionStart` injects the Foundry contract (PR/merge only through the pipeline,
    ADRs settled unless superseded, which skills to use) into every session.
  - `PreToolUse` (Bash) denies `gh pr create` / `gh pr merge` and `git push` targeting
    the default branch — explicit refspec or bare push from it. The anti-rules are now
    mechanisms; unregistered repos are untouched and the guard fails open.
- **Two-stage pre-merge review**: the `reviewer` agent first verifies each acceptance
  criterion against the diff (covered / not covered / contradicted), then does the
  quality pass; `merge-pr` feeds it the AC and blocks on either stage.
- **Mid-flight memory**: `Tracker.add_comment` + `edit comment <ID> < note.md`,
  recent progress notes surfaced in `query issue` (`comments`), and a new
  `/foundry:resume-issue` skill that reconstructs tracker + git + PR state and
  continues without a human re-briefing.
- `setup_project … --import-adrs <dir>`: replicates repo bootstrap ADRs into the
  tracker KB, idempotent — closes ADR-0001's "répliqué en KB au provisioning".
- **Skill linter in CI** (`tests/test_skills.py`): every SKILL.md command must start
  with a binary its `allowed-tools` covers (no env-var prefixes, no `echo … |` pipes,
  no `${N:-…}`), and foundry invocations must go through the launcher.

### Hardened in review
- Guard: shlex tokenization (quoted text never trips it; `git -C`/`gh -R` global
  flags can't bypass it), `+main`/`--all`/`--mirror`/`--repo=` forms denied,
  {main, master} protected when the default branch is undeterminable, repo identity
  from the git remote across ALL trackers, cheap prefilter before any IO.
- CI gate: `passed` now requires at least one check-run concluding `success` —
  all-`skipped`/`neutral` (path-filtered workflows) proves nothing and refuses.
- `openpr` derives the issue id only from the `<type>/<ticker>-<n>-…` branch shape;
  head-moved detection matches `HTTP 409`, not any '409' substring; `import_adrs`
  dedups by exact title; SessionStart inlines the real launcher path and skips
  `resume`; docs aligned (keychain claims, Milestone enum, registry shape).

## 0.1.1 — 2026-07-01

### Fixed
- `query`: `blocked_by`/`unblocked` computed over the full graph before status
  filtering (a `ready` issue blocked by an `in-progress` one was reported unblocked);
  `milestones()` populates `blocked_ids`; a known issue with an unset State still
  blocks its dependents.
- YouTrack adapter emits normalized `Link.type` (`depends-on`, `blocks`, …) per the
  `models.Link` contract; the query tier keys off normalized forms.
- CI gate: zero check-runs refuses (empty proves nothing green); `--allow-no-ci`
  waives the zero-check case only, decided inside `ci_gate` (`waived` marker);
  merges are pinned to the gated sha (409 if the head moved); `check_runs` paginates.
- Skills invoke `python3 …/tooling/foundry_cli.py` (launcher) so commands match
  `allowed-tools: Bash(python3:*)` prefix rules; `< file` redirects instead of pipes.
- Assorted: scp-style ssh aliases in `parse_owner_repo`, strict CLI flags, issue id
  derived from the branch in `openpr`, `$top` on YouTrack list endpoints, dirty-tree
  guard in `start`, `.githooks/pre-push` shipped, marketplace source `./`.

## 0.1.0 — 2026-07-01

- Bootstrap: pipeline (frame → roadmap → next/start/open-pr/merge-pr → intake),
  query/write tiers, YouTrack + GitHub adapters (+ ghprojects stub), registry,
  reviewer agent, pure-logic tests, own-method dogfooding (FOUNDRY-ADR-0001).
