# KPI export in the DevHub format (PLP-319)

`python3 tooling/foundry_cli.py routing kpi-export ISSUE... --output FILE [--session <claude-main.jsonl>]...
[--codex-session <rollout.jsonl>]... [--repository NS] [--root DIR]` (`tooling/foundry/kpi_export.py`, document
schema `foundry-kpi-export.v1`, records of contract `devhub-observations.v2`, PLP-ADR-0061, DEVHUB-ADR-0017)
writes one local file with the attempt, review-round and escalation records of the listed issues. This verb sends
nothing; the transmission is the separate, explicit verb `routing kpi-publish` described in
[Publication to DevHub](#publication-to-devhub-plp-325).

Guarantees, each proved by a test of `tests/test_kpi_export.py`:

- Read-only and offline: the module only reads the stores listed below and returns bytes; the CLI verb of `routing.py`
  writes the single file named by `--output` (`test_cli_verb_writes_only_the_named_file`,
  `test_same_inputs_same_bytes_and_read_only` compare the state directory before and after,
  `test_module_has_no_network_tracker_or_write_surface` bounds the imports and forbids write, network and
  subprocess calls).
- Deterministic: no clock, JSON with sorted keys and a final newline; the same state and the same session logs give
  the same bytes (`test_same_inputs_same_bytes_and_read_only`). Issue order and duplicates on the command line do not
  matter.
- No gate, routing or merge reads the output: only the CLI verb imports the module
  (`test_only_the_cli_verb_imports_kpi_export`); `kpi_export` itself imports `review_turns` and `token_attribution`, which the importer allow-lists of `tests/test_review_turns.py` and `tests/test_token_attribution.py` now list.
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
transmitter takes `batches[i]` as is and looks up `provenance` by `record_id`.

## Common fields

- `record_id` = `kpi-` + sha256 of issue, kind, `attempt_id` and `source.fingerprint`.
- `source` = `{system: "foundry", producer: "foundry-kpi-export", schema, fingerprint}`; `fingerprint` is the sha256
  of the canonical JSON of the source material of the record (listed per kind in `kpi_export.py`: the invocation
  record fields and tokens, the review-turn fields, the escalation receipt fields); `schema` names the source:
  `foundry-invocation-log`, `review-turns.v1`, `escalation-ledger.v1`.
- `anchor` = `{type: "issue", command_id: null, issue_id}`: no command plan is recorded locally.
- `revision` 1, `supersedes_id` null, always: no source holds a revision. A source that changes after the record was
  published (a completion event, tokens found later) changes the fingerprint and so the `record_id`; the export cannot
  know what was published, so the transmitter must treat such a record as a new revision of the same chain
  (unknown: how DevHub answers `idempotency_conflict` in that case, not observed).
- The `source.fingerprint` of an `attempt` does not include the price grid. An attempt exported with `amount` null
  before a price entry existed (PLP-328 added entries on 2026-10-27) and exported again once priced, or whose `amount`
  changes after a grid change or after its `completeness` turns `partial` (PLP-14, Sonnet 5.5 cache writes), keeps the
  same `record_id` with a different body (deduced from the fingerprint material
  in `_attempts`; `record_id` is not changed). The transmitter must handle that case; how DevHub answers is unknown, not
  observed.
- `started_at`, `observed_at`, `ended_at`: epoch milliseconds converted from source timestamps; the module reads no
  clock. A time no source holds is not invented: the record is omitted (`no_recorded_time`) or `ended_at` is `null`.
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
| `tokens.input`, `cached_input`, `cache_write_input`, `output` | `token_attribution.report(..., per_invocation=...)`, method `exact`, keys `BILLING_TOKEN_KEYS` of `cost_attribution.py` | no `--session`/`--codex-session` given, or the invocation is absent from the logs given |
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

Not exported: the coordinator's own tokens and the tokens of sub-agents without an invocation record (methods `window`,
`approximate`, `overlap`, `unattributed` of `routing token-attribution`). No coordinator attempt is emitted.

Observed on 2026-10-27 on the maintainer machine: the invocation record held 0 entries for the default state directory,
so the real sample below has no `attempt` record; the `exact` link and the amount are established by synthetic tests
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
| `remark_count` | turn `remarks` | no campaign receipt or journal record (reason copied from the turn `unrecorded`) |
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
the `at` of the first routed invocation of the same role and issue at `to_tier`, an upper bound (deduced, said in
`provenance`); with no `decided_at` the record is omitted (`no_recorded_time`) when the invocation record has none.
`attribution` is `exact` (the ledger is keyed by issue).

## Procedure and sample

Run once on a copy of the state:
`python3 tooling/foundry_cli.py routing kpi-export PLP-316 PLP-317 PLP-318 --output <file> --root <worktree>`
prints the counts per kind and writes the file; running it twice produced identical bytes (checked on 2026-10-27).

## Publication to DevHub (PLP-325)

`python3 tooling/foundry_cli.py routing kpi-publish FILE --project KEY [--url URL] [--root DIR]`
(`tooling/foundry/kpi_publish.py`, PLP-ADR-0061) sends each `batches[i]` of a `foundry-kpi-export.v1` file, as is, to
`POST <url>/api/economics/v2/projects/<KEY>/observations:batch` (route and result shape: DevHub `docs/TRACKER_API.md`,
"devhub-observations.v2", read, not run by this change). `provenance`, `omitted` and `counts` are not sent.

Status of each documented artifact (AGENTS.md#R5):

| Artifact | Status |
|---|---|
| verb `routing kpi-publish` and its options | documented here; `routing.py` registers it |
| config key `DEVHUB_METRICS_TOKEN` | secret, resolved by `config.get` in its order: the environment variable, then `CLAUDE_PLUGIN_OPTION_DEVHUB_METRICS_TOKEN`, then the keychain service `patolabs.foundry` (`_KEYCHAIN_SECRETS` in `config.py`), then a line of the local config files (`_load_dev_files`); `write_settings` refuses to write it there. Store it in the keychain with `python3 tooling/foundry_cli.py configure credential --name DEVHUB_METRICS_TOKEN` (interactive, never on the command line); `configure show` reports `configured` or `missing`. Dedicated to this write: `DEVHUB_TRACKER_TOKEN` is never used. DevHub requires the scope `foundry:metrics:write` for a write (`docs/TRACKER_API.md` of DevHub) |
| config key `DEVHUB_URL` | unchanged; `--url` overrides it; validated by `devhub.validate_base_url` (HTTPS outside loopback) |
| capability `kpi_publication_supported` | `trackers/base.py`, default `False`; `True` only in `DevHubTracker` (`trackers/devhub.py`). The active tracker (`foundry._effective_tracker_selection`) is read without instantiating it; any other adapter is refused, exit status 2, nothing sent |

Behaviour, each point proved by a test of `tests/test_kpi_publish.py`:

- Explicit only: `routing.py` is the only importer of `kpi_publish`; no hook, gate, merge or campaign imports it, and
  `kpi_export.py` stays free of network code
  (`test_only_the_cli_verb_imports_the_publisher_and_the_reader_stays_offline`).
- Project key explicit: the document is refused, before any request, if one record `anchor.issue_id` does not start with
  `<KEY>-`, or if a batch is malformed (`test_document_of_another_project_is_refused_before_any_request`).
- Idempotence is DevHub's: records keep their `record_id`, so a second identical publication is answered `replayed`
  per record (`test_publish_reports_created_replayed_and_refusals_and_republication_replays`). What DevHub answers for
  a changed body under the same `record_id` (`idempotency_conflict`, see "Common fields") is reported as a refusal
  with its code; it is not retried or rewritten.
- Report on standard output, JSON `foundry-kpi-publish.v1`: `totals` (`created`, `replayed`, `refused`, `unsent`),
  per-batch entries (`index`, `records`, and `error` when the whole batch failed) and `refusals` (`record_id`, `code`).
  A batch-level failure (`http_<status>_<code>`, `tracker_unavailable`, `contract_mismatch`, `invalid_response`,
  `response_too_large`, `batch_too_large`) counts its records as `unsent`; the next batch is still tried; there is no retry
  (`test_batch_failures_are_reported_and_next_batch_still_sent`). Neither the token nor a server message is printed.
- Exit status: 0 when every record is `created` or `replayed`, 1 when any record is refused or unsent, 2 for a refusal
  before any request. Nothing else in Foundry reads this result; a failure changes no routing, gate, merge or campaign
  (only `routing.py` calls the module; `test_run_exit_status_and_report`).
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
