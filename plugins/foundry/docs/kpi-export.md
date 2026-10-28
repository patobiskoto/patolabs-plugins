# KPI export in the DevHub format (PLP-319)

`python3 tooling/foundry_cli.py routing kpi-export ISSUE... --output FILE [--session <claude-main.jsonl>]...
[--codex-session <rollout.jsonl>]... [--repository NS] [--root DIR]` (`tooling/foundry/kpi_export.py`, document
schema `foundry-kpi-export.v1`, records of contract `devhub-observations.v2`, PLP-ADR-0061, DEVHUB-ADR-0017)
writes one local file with the attempt, review-round and escalation records of the listed issues. Nothing is sent:
the transmission to DevHub is a separate step of the maintainer.

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

Records are not sent as JSON Lines because the DevHub batch body is an object `{schema_version, records}`; a
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
| `status`, `failure_class` | last `completed` event: `completed`/`none`, `failed`/`host` (`hooks/observe_agent.py`); no completion event is `unknown`/`unknown` | never |
| `started_at`, `observed_at` | `at` of the `routed` event | never |
| `ended_at` | none: the completion time is not kept by the reader | always |
| `tokens.input`, `cached_input`, `cache_write_input`, `output` | `token_attribution.report(..., per_invocation=...)`, method `exact`, keys `BILLING_TOKEN_KEYS` of `cost_attribution.py` | no `--session`/`--codex-session` given, or the invocation is absent from the logs given |
| `tokens.reasoning_output` | none: the per-invocation attachment carries the billing keys only; it is part of `output` and never enters a total | always |
| `amount` | see below | no tokens, no price, or threshold pricing |

`amount` is `{amount_micros, currency: "USD", unit: "micros", method: "estimated", completeness: "complete",
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
`cache_write_input`.

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
(`tests/test_kpi_export.py::test_no_decision_path_reads_decided_at`). Older receipts have none and stay readable. The reverse does not hold: Foundry 2.6.0 and earlier accept only the four receipt keys (`set(receipt) != _FAILURE_RECEIPT_KEYS` in their `escalation.py`), so they treat a ledger holding a receipt with `decided_at` as invalid and routing fails closed for that issue (deduced from that code, not run); do not go back to such a version, or run one on another host, once a new escalation receipt exists.
`started_at` = `observed_at` is that `decided_at` (observed, said in `provenance`); for a receipt without it, it is
the `at` of the first routed invocation of the same role and issue at `to_tier`, an upper bound (deduced, said in
`provenance`); with no `decided_at` the record is omitted (`no_recorded_time`) when the invocation record has none.
`attribution` is `exact` (the ledger is keyed by issue).

## Procedure and sample

Run once on a copy of the state:
`python3 tooling/foundry_cli.py routing kpi-export PLP-316 PLP-317 PLP-318 --output <file> --root <worktree>`
prints the counts per kind and writes the file; running it twice produced identical bytes (checked on 2026-10-27).
