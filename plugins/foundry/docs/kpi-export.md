# KPI export in the DevHub format (PLP-319)

`python3 tooling/foundry_cli.py routing kpi-export ISSUE... --output FILE [--session <claude-main.jsonl>]...
[--codex-session <rollout.jsonl>]... [--repository NS] [--root DIR]` (`tooling/foundry/kpi_export.py`, document
schema `foundry-kpi-export.v1`, records of contract `devhub-observations.v2`, PLP-ADR-0061, DEVHUB-ADR-0017)
writes one local file with the attempt, review-round and escalation records of the listed issues. Without `--session` and
`--codex-session`, the CLI verb searches the session logs itself ([Finding the session logs](#finding-the-session-logs-plp-342)).
This verb sends
nothing; the transmission is the separate verb `routing kpi-publish` described in
[Publication to DevHub](#publication-to-devhub-plp-325), which a successful `issue merge` also runs for the merged
issue ([Publication at merge](#publication-at-merge-plp-343)).

Guarantees, each proved by a test of `tests/test_kpi_export.py`:

- Read-only and offline: the module only reads the stores listed below and returns bytes; the CLI verb of `routing.py`
  writes the single file named by `--output` (`test_cli_verb_writes_only_the_named_file`,
  `test_same_inputs_same_bytes_and_read_only` compare the state directory before and after,
  `test_module_has_no_network_tracker_or_write_surface` bounds the imports and forbids write, network and
  subprocess calls).
- Deterministic: no clock, JSON with sorted keys and a final newline; the same state and the same session logs give
  the same bytes (`test_same_inputs_same_bytes_and_read_only`). Issue order and duplicates on the command line do not
  matter.
- No gate, routing or merge reads the output: only the CLI verb and the merge step `kpi_merge.py` (PLP-343) import the
  module (`test_only_the_cli_verb_and_the_merge_step_import_kpi_export`); `kpi_export` itself imports `review_turns` and `token_attribution`, which the importer allow-lists of `tests/test_review_turns.py` and `tests/test_token_attribution.py` now list.
- Content-free: no prompt, path, command, session or tool-use identifier, proof identifier or remark text
  (`test_output_is_content_free`).

## Output document

| Key | Content |
|---|---|
| `batches` | list of `{schema_version: "devhub-observations.v2", records: [...]}` of at most `BATCH_SIZE` (100) records each, the batch size DevHub accepts; each batch is a ready request body for `POST /observations:batch`. Records are sorted by issue, kind (`attempt`, `review_round`, `escalation`), `started_at`, `record_id`. |
| `provenance` | by `record_id`: `from` maps every non-null field path to its source, `null` maps every null field path to a reason. It is outside the records because the DevHub record is `strict`. |
| `omitted` | records that were not emitted, each with `issue`, `kind`, `reason` (and `rank`, `role`). |
| `counts`, `issues`, `export_schema` | records per kind, the sorted issue list, the schema id. |

Records are not stored as JSON Lines because the DevHub batch body is an object `{schema_version, records}`; a
transmitter takes `batches[i]` as is (`kpi-publish` rewrites three fields of a record when a published ledger holds its chain, see "Publication to DevHub") and looks up `provenance` by `record_id`.

## Common fields

- `record_id` = `kpi-` + sha256 of issue, kind, `attempt_id` and `source.fingerprint`.
- `source` = `{system: "foundry", producer: "foundry-kpi-export", schema, fingerprint}`; `fingerprint` is the sha256
  of the canonical JSON of the source material of the record (listed per kind in `kpi_export.py`: the invocation
  record fields and tokens, the review-turn fields, the escalation receipt fields); `schema` names the source:
  `foundry-invocation-log`, `review-turns.v1`, `escalation-ledger.v1`.
- `anchor` = `{type: "issue", command_id: null, issue_id}`: no command plan is recorded locally.
- `revision` 1, `supersedes_id` null, always, in the export (`provenance.null`, not `from`, PLP-321): no source holds a revision and the export cannot know
  what was published. A source that changes after publication (a completion event, tokens found later) changes the
  fingerprint and so the `record_id`; the revision is then decided by the publisher, which keeps what it published
  ([Revisions](#revisions-plp-342)).
- The `source.fingerprint` of an `attempt` does not include the price grid. An attempt exported with `amount` null
  before a price entry existed (PLP-328 added entries on 2026-10-27) and exported again once priced, or whose `amount`
  changes after a grid change or after its `completeness` turns `partial` (PLP-14, Sonnet 5.5 cache writes), keeps the
  same `record_id` with a different body (deduced from the fingerprint material
  in `_attempts`; `record_id` is not changed). The publisher does not treat it as a revision, since the fingerprint is
  unchanged (and DevHub refuses a revision whose fingerprint is already published, `idempotency_conflict`, PLP-342, DevHub
  source read, not run): DevHub answers `idempotency_conflict` for the same `record_id` with a different body (same
  source, read, not run) and the publisher reports it as a refusal.
- `started_at`, `observed_at`, `ended_at`: epoch milliseconds converted from source timestamps; the module reads no
  clock. A time no source holds is not invented: the record is omitted (`no_recorded_time`; an attempt whose `at` is not a readable timestamp gets an `omitted` entry `{issue, kind: "attempt", reason: "no_recorded_time"}` since PLP-321, without `rank` or `role`) or `ended_at` is `null`.
- Unknown is `null`, never `0`.

## `attempt`

One record per `routed` event of the PLP-316 invocation record for the issue (`invocation_log.records_by_id`,
[model-routing.md](model-routing.md), "Local invocation record"). `attempt_id` = `at-` + 32 hex of sha256 of the
issue and the opaque invocation id (the invocation id itself is not emitted).

| Field | Source | Null when |
|---|---|---|
| `role` | record `role`; `corrector` when the implementer record has `correction` true (a claim made at routing, so `attribution` is `approximate`) | never |
| `attribution` | `exact`: the record carries the issue; `approximate` for `corrector` (the format requires `window` or `approximate`) | never |
| `host`, `tier`, `effort`, `model.configured` | record `host`, `tier`, `effort`, `model_requested` (the model requested at routing) | not a valid `id` or not recorded |
| `model.served` | none | always: the served model is not recorded locally |
| `status`, `failure_class` | last `completed` event: `completed`/`none`, `failed`/`host` (`hooks/observe_agent.py`); no completion event is `unknown`/`unknown` (raw log status `routed`, every Codex attempt included; `provenance`: "no completion event"); a background Agent launch is also `unknown`/`unknown` (raw log status `unknown`, written by `hooks/observe_agent.py`; PLP-329, [model-routing.md](model-routing.md), "Local invocation record"), and its `provenance` says "background launch" (`_attempts` in `kpi_export.py` branches on the raw status, PLP-14) | never |
| `started_at`, `observed_at` | `at` of the `routed` event | never |
| `ended_at` | none: the completion time is not kept by the reader, so a background launch has none either | always |
| `tokens.input`, `cached_input`, `cache_write_input`, `output` | `token_attribution.report(..., per_invocation=...)`, method `exact`, keys `BILLING_TOKEN_KEYS` of `cost_attribution.py`; a lower bound when `lower_bound_requests` is above 0 (see "Tokens that are a lower bound" below) | no log given or found (see [Finding the session logs](#finding-the-session-logs-plp-342)), or the invocation is absent from those logs |
| `tokens.reasoning_output` | none: the per-invocation attachment carries the billing keys only; it is part of `output` and never enters a total | always |
| `amount` | see below | no tokens, no price, or a threshold entry (`haiku-5.5`) whose per-request counts are missing |

`amount` is `{amount_micros, currency: "USD", unit: "micros", method: "estimated", completeness: "complete" or "partial",
rate_version}`, never a billed amount and never `provider_reported`: it applies the frozen price grid
`pricing-v1.json` (`cost_attribution.price_for`, FOUNDRY-ADR-0015, [cost-attribution.md](cost-attribution.md)) for the
host, the requested model and the day of `at` to the four billing token counts. Rates of the grid are per million
tokens, so micro-USD is `tokens * rate` (`_price_chunk` multiplies the same way); the sum is rounded half up to an
integer string. `rate_version` = `pricing-v1-` + the first 12 hex of the sha256 of the grid entry used, so a changed
rate changes the version. The grid entries are billed as `subscription` (`billing_mode`): the amount is an
API-equivalent estimate. It is `null` when the requested model has no entry for that day (observed in
`pricing-v1.json` on 2026-10-27: the Claude entries are `haiku-4.5`, `sonnet-5`, `opus-5`, `fable-5`, `sonnet-5.5`,
`haiku-5.5`, `opus-5.5`, `fable-5.1`; the last four are effective from 2026-10-27, so an attempt of one of them dated
earlier, or a model outside this list, is not priced), when the entry has a context threshold (`context_threshold_tokens`) and the per-request counts of the invocation
are not available (a codex row without request granularity, `request_granularity` false in `cost_attribution.py`;
`_amount` in `kpi_export.py` returns the reason `threshold_pricing_needs_per_request_tokens`), or when tokens are
null. Of the Claude entries only `haiku-5.5` has a threshold (100000 tokens): the export prices it request by
request, with `over_threshold_rates` for a request whose context exceeds the threshold (`over_threshold` in
`cost_attribution.py`, the rule of `_price_chunk`), and sums the requests before rounding. The 1h cache-write rate of the Anthropic page is not a key of the grid
(`cost_attribution.load_price_grid` accepts the four rate keys only), so only the 5m write rate is applied to
`cache_write_input`. The host-log reader sums the 5m and 1h cache writes of a request into that one count
(`_normalized_claude_usage` in `cost_attribution.py`), so the export cannot tell them apart. The Sonnet 5.5 profiles ask
for 1h cache writes (`CLAUDE_CACHE_TTL_1H_PINS`, `routing_facades.py`, PAT-134). The rule (`_amount`, PLP-14): for a
Claude attempt whose grid entry is one of those pins and whose `cache_write_input` is above zero, `completeness` is
`partial` (DevHub's enum is `complete`, `partial`, `unknown`: `src/tracker/observation-contract.ts:29` in the DevHub
repository, read, not run), and `provenance` says the amount is a lower bound; otherwise it is `complete`. The number is
unchanged. The remaining error is an underestimate (deduced): the Anthropic 1h write rate is 2 times the base input rate
against 1.25 times for 5m (not re-verified for PLP-14), which is 4.0 against 2.5 USD per million tokens for `sonnet-5.5`
(the 2.5 is the grid value, `pricing-v1.json`); the 1h rate is not a grid key and is not applied. Whether the 1h cache
lifetime applies on a given host or subscription state is unknown (R7 of the process contract).

### Tokens that are a lower bound (PLP-344)

A Claude request of the invocation's log can have no record with a non-null `stop_reason` (observed on 2026-10-29 in
subagent logs of Claude Code 2.1.286, 2.1.299 and 2.1.304; cause unknown, host side). `read_host_log` counts it with the
largest value observed for each counter and the slice carries `lower_bound_requests`
([cost-attribution.md](cost-attribution.md), "Complétude Claude"). The export then says so in three places, only for
an attempt whose slice has `lower_bound_requests` above 0 (`_attempts` in `kpi_export.py`):

- `provenance.from` of each of `tokens.input`, `cached_input`, `cache_write_input` and `output` ends with
  "lower bound: N request(s) of the log have no terminal snapshot, counted with the largest value observed". The values
  themselves are unchanged: the DevHub record is `strict` and has no field for this (see `provenance` in "Output document"), so no value is flagged inside the record.
- `amount.completeness` is `partial` (the value already used for a lower-bound amount in the 1h cache-write case above) and the `provenance` of `amount` adds "the tokens are a lower bound".
- The fingerprint material gains `tokens_lower_bound_requests` = N, so the `source.fingerprint` and the `record_id` of that
  attempt differ from those of an exact reading, and the publisher sends it as a revision of a record it already
  published ("Publication to DevHub"). An attempt with no such request has the same material, fingerprint and
  `record_id` as before (`test_lower_bound_tokens_are_said_in_the_provenance_and_change_only_that_record`).

Before PLP-344 such a log was rejected whole and the tokens were `null` (`unavailable`).

Not exported: the coordinator's own tokens and the tokens of sub-agents without an invocation record (methods `window`,
`approximate`, `overlap`, `unattributed` of `routing token-attribution`). No coordinator attempt is emitted.

Observed on 2026-10-27 on the maintainer machine: the invocation record held 0 entries for the default state directory,
so no `attempt` record was exported on that run (no sample of it is committed here); the `exact` link and the amount are established by synthetic tests
only (`test_attempts_tokens_and_estimated_amount`).

## `review_round`

One record per non-carried review turn of `routing review-turns` ([review-rounds.md](review-rounds.md), "Per-turn data
of an issue"). `attempt_id` = `rr-` + 32 hex of sha256 of issue, head and rank, as DEVHUB-ADR-0017 derives it from
(issue, head, rank). No tokens and no amount: the reviewer cost belongs to the `attempt` of role `reviewer`.

| Field | Source | Null when |
|---|---|---|
| `head_sha`, `base_sha`, `rank` | turn `head`, `base`, `rank` | `base_sha`: the proof base is not a 40-hex sha |
| `verdict` | turn `verdict` (`validated` becomes `mergeable`) | never |
| `ac_total`, `ac_pass` | turn `criteria`, `criteria_pass` | never |
| `remark_count` | turn `remarks` | the turn `remarks` is `null`: no campaign receipt and no journal count, an unreadable receipt, or an unreadable or ambiguous journal (reason copied from the turn `unrecorded`, [review-rounds.md](review-rounds.md), "Per-turn data of an issue") |
| `review_type` | turn `type` (campaign journal); else `full` for rank 1 and for a turn that follows a `blocked` turn, deduced from AGENTS.md#R9 (the first review and the review after a blocking round are full) | the record is omitted instead (`review_type_unrecorded:<reason>`): the format forbids `null`, and a turn after a validated one may be partial or full, which no local source tells outside a campaign |
| `reviewer_model` | turn `reviewer_model` (model requested) | no reviewer invocation recorded for the head |
| `implementer_model` | `model_requested` of the implementer invocations of the window before that reviewer invocation (`invocation_log.invocations_for_diff`), when they all name the same model | no window, none, or several models |
| `pr_number` | `fingerprint.pr` of the `started` `review` record of the campaign attempts journal whose `fingerprint.pr_head` equals the head, when exactly one number matches | outside a campaign, unreadable journal, or ambiguous |
| `started_at`, `ended_at`, `observed_at` | turn `started_at` (claim start) and `completed_at` of the review ledger | the record is omitted when `completed_at` is missing; a missing claim start is replaced by `completed_at` (deduced, said in `provenance`) |

`review_turns.py` gained the `started_at` field for this export (`SOURCES["started_at"]`).

## `escalation`

One record per `escalated` decision kept in the failure receipts of the issue's escalation ledger
(`EscalationStore`, `escalation.py`; `MAX_FAILURE_RECEIPTS` bounds the receipts kept). The ledger is read without
lock and not modified, through the module's own validation (`_normalize_state`). `role`, `signal`, `from_tier`
(`initial_tier`), `to_tier` (`final_tier`) and `rank` (`role_escalations`) are copied from the decision; the signals
exported are `test_red`, `review_blocking`, `review_blocking_after_fix` (`FAILURE_KINDS`). `attempt_id` = `es-` + 32
hex of sha256 of issue, role and rank. No free-text reason.

Limits: a receipt exists only for a failure recorded with an idempotency key, and an escalation by risk floor
(`RISK_FLOORS`) or explicit request leaves no per-event receipt (observed: ledgers with `total_escalations` above zero
and no receipt), so those are not exported. A failure receipt of an `escalated` decision written since PLP-326 carries
`decided_at`, the UTC time read from the clock (`_now_utc`, `escalation.py`) when the receipt was written; it is an
audit field that no routing or escalation decision reads
(`tests/test_kpi_export.py::test_no_decision_path_reads_decided_at`). Older receipts have none and stay readable. The
reverse does not hold. The check `set(receipt) != _FAILURE_RECEIPT_KEYS` (the four keys `role`, `kind`, `current_tier`,
`decision`), which makes `_normalize_state` raise the invalid-ledger error, was read in `escalation.py` of each of the
eight tags `foundry-v2.0.0`, `2.1.0`, `2.1.1`, `2.2.0`, `2.3.0`, `2.4.0`, `2.5.0` and `2.6.0` (`git show <tag>:...`); no
other version was checked, the 1.x tags included. In those versions a ledger holding a receipt with `decided_at` is
therefore invalid and routing fails closed for that issue (deduced from that code, not run). Do not go back to such a
version, or run one on another host, once a new escalation receipt exists.
`started_at` = `observed_at` is that `decided_at` (observed, said in `provenance`); for a receipt without it, it is
the `at` of the first routed invocation of the same role and issue at `to_tier`, an estimate (deduced, said in
`provenance`; neither a lower nor an upper bound, since the role may have run at `to_tier` before the decision, and the
`from_tier` invocations after it are not used); with no `decided_at` the record is omitted (`no_recorded_time`) when the invocation record has none.
`attribution` is `exact` (the ledger is keyed by issue).

## Finding the session logs (PLP-342)

When neither `--session` nor `--codex-session` is given, `routing kpi-export` calls `kpi_sessions.discover`
(`tooling/foundry/kpi_sessions.py`, PLP-ADR-0062); the pure reader `kpi_export.py` is unchanged and still receives
explicit paths. Explicit options disable the search. The search reads only:

- the invocation record of the issues (`invocation_log.records_by_id`) for the invocation ids to look for;
- Claude: `~/.claude/projects/<slug>/*.jsonl` (`CLAUDE_PROJECTS`), where `<slug>` is the path of a checkout of this
  repository with every non-alphanumeric character replaced by `-` (`claude_slug`; the same rule as the directory names
  observed under `~/.claude/projects` on 2026-10-29). The checkouts are the worktrees listed by `git worktree list`
  (`repository_roots`), else `--root`. A main log is selected when a log of its `subagents/` directory has a
  `toolUseId` (`.meta.json`) whose invocation id is one of the issue's (`token_attribution._meta_id`);
- Codex: `~/.codex/sessions/**/rollout-*.jsonl` (`CODEX_SESSIONS`), selected when the rollout's `agent_path`
  execution id is one of the issue's (`token_attribution._codex_meta`) and the `cwd` of its `session_meta` is a
  checkout path or below it.

An invocation id found in more than one log is ambiguous: no log that holds it is selected, so the other invocations
of the issue held by those logs stay unavailable too (`_unambiguous`;
`test_a_log_holding_an_ambiguous_id_is_not_selected_for_its_other_ids`). Nothing found
or ambiguous leaves `tokens` null and the `amount` null, the same `unavailable` as without a log; the module never
guesses and the CLI test checks that its output holds no path (`test_cli_without_session_uses_the_discovery_and_tokens_stay_unavailable_when_nothing_is_found`).
Besides the closed-vocabulary identifiers of `token_attribution`, the Codex search reads the `cwd` path of the
`session_meta` (`_codex_cwd`), only to keep rollouts of this repository; it is never emitted. Residual limits: a Codex coordinator log without an
invocation of the issue is not selected (its requests are not attributed `exact` anyway); a Claude session whose
project directory is a checkout that git no longer lists is not searched; the search reads the first line of every
rollout file under `~/.codex/sessions` (cost unmeasured).

## Procedure

Run once on a copy of the state: copy the state directory (`registry.data_dir()`, `~/.config/foundry` by default), then
`FOUNDRY_DATA=<copy> python3 tooling/foundry_cli.py routing kpi-export <ISSUE>... --output <file> --root <worktree>`
(`<ISSUE>...` are the issues to export; the run of 2026-10-27 named PLP-316, PLP-317 and PLP-318) prints the counts per kind and writes the file; running it twice produced identical bytes (checked on 2026-10-27).

## Publication to DevHub (PLP-325)

`python3 tooling/foundry_cli.py routing kpi-publish FILE --project KEY [--url URL] [--root DIR]`
(`tooling/foundry/kpi_publish.py`, PLP-ADR-0061) sends each `batches[i]` of a `foundry-kpi-export.v1` file to
`POST <url>/api/economics/v2/projects/<KEY>/observations:batch` (route and result shape: DevHub `docs/TRACKER_API.md`,
"devhub-observations.v2", read, not run by this change). `provenance`, `omitted` and `counts` are not sent.

Status of each documented artifact (AGENTS.md#R5):

| Artifact | Status |
|---|---|
| verb `routing kpi-publish` and its options | documented here; `routing.py` registers it |
| config key `DEVHUB_METRICS_TOKEN` | secret, resolved by `config.get` in its order: the environment variable, then `CLAUDE_PLUGIN_OPTION_DEVHUB_METRICS_TOKEN`, then the keychain service `patolabs.foundry` (`_KEYCHAIN_SECRETS` in `config.py`), then a line of the local config files (`_load_dev_files`); `write_settings` refuses to write it there. Store it in the keychain with `python3 tooling/foundry_cli.py configure credential --name DEVHUB_METRICS_TOKEN` (interactive, never on the command line); `configure show` reports `configured` or `missing`. Dedicated to this write: `DEVHUB_TRACKER_TOKEN` is never used. DevHub requires the scope `foundry:metrics:write` for a write (`docs/TRACKER_API.md` of DevHub) |
| config key `DEVHUB_URL` | unchanged; `--url` overrides it; validated by `devhub.validate_base_url` (HTTPS outside loopback) |
| local file `<state dir>/kpi-publish/<hash>.json` (`PublishedLedger`, `kpi_publish.py`) | new, PLP-342: what was published per chain, see [Revisions](#revisions-plp-342); state dir is `registry.data_dir()` (`FOUNDRY_DATA`, else `~/.config/foundry`) |
| verb `routing kpi-export`, search of the session logs | documented in [Finding the session logs](#finding-the-session-logs-plp-342) |
| capability `kpi_publication_supported` | `trackers/base.py`, default `False`; `True` only in `DevHubTracker` (`trackers/devhub.py`). The active tracker (`foundry._effective_tracker_selection`) is read without instantiating it; any other adapter is refused, exit status 2, nothing sent |

Behaviour, each point proved by a test of `tests/test_kpi_publish.py`:

- Explicit only: `routing.py` and, since PLP-343, `kpi_merge.py` are the only importers of `kpi_publish`; no hook, gate
  or campaign imports it, and `kpi_export.py` stays free of network code
  (`test_only_the_cli_verb_and_the_merge_step_import_the_publisher_and_the_reader_stays_offline`).
- Project key explicit: the document is refused, before any request, if one record `anchor.issue_id` does not start with
  `<KEY>-`, or if a batch is malformed (`test_document_of_another_project_is_refused_before_any_request`).
- Idempotence is DevHub's: records keep their `record_id`, so a second identical publication is answered `replayed`
  per record (`test_publish_reports_created_replayed_and_refusals_and_republication_replays`). What DevHub answers for
  a changed body under the same `record_id` (`idempotency_conflict`, see "Common fields") is reported as a refusal
  with its code; it is not retried or rewritten.
- Report on standard output, JSON `foundry-kpi-publish.v1`: `totals` (`created`, `replayed`, `refused`, `unsent`),
  `revised` (PLP-343: how many of the `created` records carry a `supersedes_id`, `publish` in `kpi_publish.py`),
  per-batch entries (`index`, `records`, `error` when the whole batch failed, and `ledger_error` set to `published ledger not written` when saving the published ledger after the batch raised `OSError`, `publish` in `kpi_publish.py`; it does not change `totals`) and `refusals` (`record_id`, `code`).
  A batch-level failure (`http_<status>_<code>`, `tracker_unavailable`, `contract_mismatch`, `invalid_response`,
  `response_too_large`, `batch_too_large`) counts its records as `unsent`; the next batch is still tried; there is no retry
  (`test_batch_failures_are_reported_and_next_batch_still_sent`). Neither the token nor a server message is printed.
- Exit status: 0 when every record is `created` or `replayed`, 1 when any record is refused or unsent, 2 for a refusal
  before any request. Nothing else in Foundry reads this result; a failure changes no routing, gate, merge or campaign
  (`routing.py` calls the module; `test_run_exit_status_and_report`; the merge step does not use the exit status, see
  [Publication at merge](#publication-at-merge-plp-343)).
- Bounds: at most `MAX_BATCH_RECORDS` (100) records and `MAX_BATCH_BYTES` (1 MiB) per batch, redirects are never
  followed, `timeout` 10 s per request.

The tests use a simulated loopback server whose answers follow the DevHub document cited above. Observed once on
2026-10-28, on a disposable DevHub instance built from DevHub `9c5e504` on `127.0.0.1` (procedure of
`devhub-requirements.md`, "Bring up a disposable instance"; project, anchor issues and a credential with
`tracker:read` and `foundry:metrics:write` seeded by SQL), with a real export of 3 batches (100, 100, 38 records) anchored on issues `DEVHUB-<n>`, published with `--project DEVHUB`:
the first run reported 238 `created`, exit status 0; the identical second run 238 `replayed`, exit status 0; the
database then held 14 `attempt` and 224 `review_round` records. A wrong token gave `http_401_unauthenticated` on each
batch, 238 `unsent`, exit status 1; `--project PLP` on that file was refused before any request, exit status 2. Not
observed: a per-record refusal from the real server (covered by the simulated server only) and a changed body under
the same `record_id`.

### Revisions (PLP-342)

Foundry keeps locally what it published (`PublishedLedger`, `kpi_publish.py`, schema `foundry-kpi-published.v1`):
one file per DevHub URL and project, one entry per chain `kind|issue|attempt_id` holding the `record_id`, `revision`,
`supersedes_id`, `source.fingerprint`, `started_at` and `observed_at` of the record last answered `created` or `replayed`
(an `error` or an unsent batch never updates it; the file is replaced atomically after each batch, mode 0600; it holds
no secret, path or content: `test_ledger_is_updated_from_created_and_replayed_answers_only`). Before sending, each
record is compared with its chain entry (`PublishedLedger.revise`):

- no entry: sent as exported (revision 1);
- same fingerprint: sent with the revision, `supersedes_id` and `observed_at` of the entry, so a re-publication of a
  revision stays identical and is `replayed`;
- other fingerprint: sent with `revision` = entry + 1, `supersedes_id` = the entry's `record_id` and `observed_at`
  = the larger of the two; the `record_id` is already new, being derived from the fingerprint. It is never a second
  independent record (`test_a_changed_source_is_published_as_a_revision_and_an_unchanged_one_replays`).

DevHub's rules for a revision (chain key kind, anchor, `attempt_id`; revision = previous + 1; `supersedes_id` = previous
`record_id`; same `started_at`, `source.system` and `source.producer`; `observed_at` not earlier; fingerprint not already
published; answers `correction_conflict` or `idempotency_conflict` otherwise) were read from the DevHub source and
confirmed by DevHub for this change, not run live here; the tests use a simulated server that applies a reduced form of
them. The batches are sent in order and records are applied by DevHub in send order; the export emits one record per chain (deduced: `attempt_id` is derived from the identity of the source, one fingerprint per source), so a revision never shares a request with its predecessor.

Limits, stated as such:

- Loss of the ledger: an unchanged record is republished as revision 1 and answered `replayed`; a record that was
  already revised is sent again as revision 1 and refused `idempotency_conflict` (same `record_id` as the revision,
  different body, deduced from the DevHub rule above;
  `test_loss_of_the_ledger_republishes_an_unchanged_record_as_replayed_and_a_revised_one_is_refused`). No read-back from
  `GET /observations?issue=` is implemented.
- Records published before PLP-342 have no entry: a source changed since then is sent as revision 1 and DevHub
  refuses it as `correction_conflict`, reported in `refusals`. An unchanged one is `replayed` and gets an entry.
- A source that returns to an earlier fingerprint, or a changed `amount` with an unchanged fingerprint (price grid
  update), is refused `idempotency_conflict`: not a revision.
- A revision whose `started_at` differs from the previous (an escalation start that is an estimate deduced from the
  first routed invocation) is sent as is and refused `correction_conflict` if DevHub applies the rule.
- Two concurrent publications to the same project have no lock: last writer wins on the ledger file (not exercised).

## Publication at merge (PLP-343)

A successful `issue merge` counts as the explicit request to read the session logs and to publish, for the merged
issue only (PLP-ADR-0063, which amends PLP-ADR-0061 and FOUNDRY-ADR-0015 on that single point; the rest of both is
unchanged). The step is `_publish_kpi_after_merge` in `tooling/foundry/issue.py`, calling
`kpi_merge.publish_after_merge` (`tooling/foundry/kpi_merge.py`) in the `issue merge` process, never in a hook
(FOUNDRY-ADR-0008).

Status of each documented artifact (AGENTS.md#R5):

| Artifact | Status |
|---|---|
| step after the `done` transition of `issue merge` | new, PLP-343; it runs before the local cleanup report, so it also runs when the merge exits 3 (`EXIT_LOCAL_CLEANUP_FAILED`); it does not run when the merge is refused, nor on the resume path of an already merged PR (that path returns earlier; catch up with the manual verbs) |
| what it does | `kpi_sessions.discover` for the issue, `kpi_export.build`, `kpi_publish.validate_document`, `kpi_publish.endpoint` (URL and token, the same resolution as `routing kpi-publish`), `kpi_publish.publish` with the `PublishedLedger`; the project key is the part of the issue id before the first `-`; the export is held in memory, no file is written besides the ledger |
| capability | the tracker instance of the merge must have `kpi_publication_supported` true (`trackers/base.py`); otherwise nothing is published and nothing is printed |
| output | on success exactly one line on standard output, `kpi: created=N revised=N replayed=N refused=N not_sent=N` (`kpi_merge.summary`; `created` excludes the `revised` ones, `not_sent` is `unsent` of the report); when `refused` or `not_sent` is above zero the same line followed by `; run ... to catch up` goes to standard error as the one warning. A failure before any request gives one warning line `kpi: publication skipped (<reason>); run ...`. Lines hold counts and fixed words: no path, URL, token or server message (`test_the_step_never_raises_whatever_fails_inside_it`, `test_an_unexpected_exception_becomes_a_warning_without_detail`) |
| exit code and merge output | unchanged: the step catches every exception and `SystemExit`, including an unexpected one (`_publish_kpi_after_merge`, `kpi_merge.publish_after_merge`); only `KeyboardInterrupt` passes |
| tests | `tests/test_kpi_merge.py`: simulated loopback server, fake token, no real keychain, no real logs; `tests/conftest.py::_no_kpi_publication_after_merge` replaces the step by a no-op in every other test |

Cases covered by `tests/test_kpi_merge.py`: success, replay and revision; no capability; token absent
(`test_token_absent_is_one_warning_and_nothing_is_sent`); tracker unreachable
(`test_tracker_unreachable_is_a_warning_with_the_count_not_sent`); refusal
(`test_a_refusal_is_a_warning_with_the_count_refused`); unexpected exception; document of another project; the wiring in
`merge` (`test_merge_runs_the_step_after_done_and_a_failing_step_changes_neither_output_nor_exit`,
`test_merge_exit_code_is_unchanged_when_the_real_step_explodes`).

Limits, stated as such:

- Figures can be partial at merge (for example a completion event written later): a later manual
  `routing kpi-export` then `routing kpi-publish` sends a revision ([Revisions](#revisions-plp-342)); nothing
  publishes the issue again by itself after its merge.
- Extra cost of a merge: one local log search and one HTTP request per batch, no model call (deduced from the imports
  of `kpi_export.py` and `kpi_publish.py`; not measured).
- The merge step has no retry and no timeout other than the 10 s per request of `publish`; the log search is not
  bounded in time (cost unmeasured, see [Finding the session logs](#finding-the-session-logs-plp-342)).
- Not run against a real DevHub here: the tests use the simulated server.
