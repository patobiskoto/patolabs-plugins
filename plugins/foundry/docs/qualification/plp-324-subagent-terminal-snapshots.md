# PLP-324 - Claude subagent logs without a terminal record: is the usage complete?

PLP-324 (Epic PLP-315). Frame: FOUNDRY-ADR-0015 (offline reader of host session logs; **not amended** by this page), AGENTS.md R5 and R9. Follows PLP-344, which made `read_host_log` accept a request with no terminal snapshot and count it as a marked lower bound ([cost-attribution.md](../cost-attribution.md), "Complétude Claude"; [kpi-export.md](../kpi-export.md)).

Legend. **observed**: read on the local logs by the procedure below. **deduced**: inferred from observed figures. **unknown**: not established. Only aggregate counts appear here; no path, session identifier, message content or name.

## Question and answer

Is the usage of a Claude subagent request that has no terminal record (no record with a non-null `stop_reason`) complete?

- Input, cache-creation and cache-read counters: **complete on every comparison made** (observed, 3,466 of 3,466 requests, and 69 of 71 parent comparisons; the two differences are listed below); for requests that have neither a terminal record nor a parent figure this is **deduced** (see "Decision and limits").
- `output_tokens`: **not complete** (observed). The non-terminal records carry an early streaming figure, not the final output. The reader's rule (largest `output_tokens` seen, marked `lower_bound_requests`) is therefore the right one and cannot be promoted to an exact rule.
- Cause of the missing terminal record: **unknown** (host side; the logs only show that the records stop before the final one).

Consequence for the acceptance criteria. AC2 first branch ("complete: accept through an amendment of FOUNDRY-ADR-0015") does not apply, so no amendment is made and no reader code changes. Its second branch ("otherwise the limit is documented and the tokens stay unreadable") is **superseded** by the maintainer decision of 2026-10-29 realised by PLP-344: these requests are no longer unreadable, they are read with input and cache counted and output as a lower bound, marked. This page is the measurement that decision lacked; it confirms that the output side must stay a lower bound.

## Procedure (observed, 2026-10-30, read-only, ad hoc script outside the repository)

Scope: the local Claude Code project directory of this repository: 29 entries, 719 subagent logs (each with its `*.meta.json`), main session logs. A request is the set of `assistant` records with the same (`requestId`, `message.id`) that carry `message.usage`, as in the reader. A request is terminal when at least one of its records has a non-null `stop_reason`. The "maximum" of a counter is its largest value over the request's records.

Independent source: for a subagent invocation, the main log holds a tool result whose `toolUseResult` has `agentId`, `totalTokens` and a `usage` object, matched to the subagent log through the `toolUseId` of its `*.meta.json`. 106 invocations had such a result (613 logs had none in the main logs present: not compared).

## Findings

1. **Extent (observed).** Subagent logs: 16,604 requests, 11,674 without terminal record (70%); logs: 8 with only terminal requests, 527 mixed, 184 with only non-terminal requests. Main session logs: 8,870 requests, 2 without terminal record. Share of requests without terminal record by `version` field of the records, subagent logs (non-terminal / all): 2.1.280 0/221, 2.1.281 510/875, 2.1.294 1,067/1,619, 2.1.295 2,560/3,865, 2.1.299 5,023/6,742, 2.1.304 2,515/3,283. (These per-version counts come from a scan of all 719 files and total 16,605 requests, one more than the first scan; the difference is not explained: unknown.)
2. **Streaming snapshots (observed).** A non-terminal request has 1 record (2,645 requests), 2 (7,933), 3 (889), 4 (166) or 5 or more (42) in the scan of all files; their `output_tokens` maximum has median 8, 90th percentile 20, maximum 126, whereas the `output_tokens` of terminal requests has median 331.
3. **Parent-reported figure: what it is (observed, deduced).** In all 106 results, `totalTokens` equals the sum of the four counters of the result's own `usage` (observed, 106 of 106), `usage.iterations` holds one entry (observed), and `totalTokens` differs from the sum over the whole subagent log in 106 of 106 (observed). Deduced: it describes the last request of the subagent, not the invocation. The compared sets for the counter test below are therefore the parent `usage` and the maximum counters of the last request of the subagent log (the last (`requestId`, `message.id`) in file order).
4. **Last request without terminal record vs parent figure (observed, 71 invocations).** Input tokens equal in 71 of 71; cache-creation equal in 69 and log greater in 2; cache-read equal in 69 and log greater in 2; `output_tokens` of the log **lower than the parent figure in 71 of 71** (difference 1,110 to 14,124, median 3,142; log figure 0.0% to 1.6% of the parent figure, median 0.2%). Cause of the 2 cache differences: unknown (not explained; the parent figure might describe another request than the last one in file order).
5. **Last request with terminal record vs parent figure (observed, 35 invocations).** Input equal in 35; cache-creation equal in 33, greater in 1, lower in 1; cache-read equal in 33, greater in 2; `output_tokens` equal in 33, greater in 1, lower in 1. So with a terminal record the log reproduces the parent figure in 33 of 35 cases on every counter (`totalTokens` equals the log sum in 33), and the two exceptions are not explained: unknown.
6. **Same request, non-terminal vs terminal records (observed, 3,466 requests having both).** The non-terminal maximum of each input and cache counter equals the terminal one in 3,466 of 3,466. The non-terminal maximum `output_tokens` is lower than the terminal one in 3,466 of 3,466; summed, 30,429 against 3,053,564 (1.0%).
7. **Size of the lower bound (observed, deduced).** The `output_tokens` maximum summed over the 11,675 non-terminal requests of the all-files scan is 121,082, against 3,450,995 over the 4,930 terminal requests. Deduced from findings 4 and 6: the true output of a non-terminal request is typically far above its recorded maximum (by two orders of magnitude on the last requests compared); how far on requests that are not last is **unknown** (only the last request has an independent figure).

## AC3 - method `exact` on a real session (observed, 2026-10-30)

`routing token-attribution --session <the most recent main log>` (a live session, so its figures grow; the coordinator saw 34 `exact` slices earlier the same day) returned (first run) `unreadable_logs` {coordinator 0, subagent 0}, 171 subagent logs read, 77 slices and 7,656 requests, of which `lower_bound_requests` 3,217 (slice counts by method and presence of the lower-bound mark):

| method | scope | lower bound marked | slices | requests |
|---|---|---|---|---|
| exact | subagent | yes | 35 | 1,201 |
| approximate | subagent | yes | 6 | 227 |
| overlap | subagent | yes | 12 | 734 |
| overlap | coordinator | yes | 1 | 73 |
| overlap | coordinator | no | 13 | 494 |
| unattributed | subagent | yes | 5 | 2,087 |
| unattributed | coordinator | no | 2 | 2,782 |
| window | coordinator | no | 3 | 59 |

The table and the totals come from two successive runs on the live log: the first gave 7,656 requests and 1,200 `exact` requests, the second the table (7,657 requests, 1,201 `exact`); deduced: the session grew between them. Method `exact` is therefore observed on real Claude data (35 slices), which closes the gap stated in [cost-attribution.md](../cost-attribution.md) ("le lien `exact` n'a pas été observé sur des données réelles"; wording before PLP-324, since rewritten by this same change). Every `exact` slice carries the lower-bound mark: no `exact` slice has an exact token total on this session (observed). The two earlier main logs tested hold no subagent log (1 request each, `unattributed`).

## Decision and limits

- The reader and FOUNDRY-ADR-0015 stay unchanged. The documented limit is: the `output_tokens` of a request without terminal record is a lower bound. Input and cache complete: **observed** on the 3,466 requests that also have a terminal record (finding 6) and on the last request of 71 invocations (finding 4, with the two cache differences listed there); **deduced**, not observed, for every other request without a terminal record (not last, no terminal record: no independent figure exists for it, finding 7).
- Any KPI amount computed from these slices underestimates the output component; the size of the underestimate on non-last requests is unknown (finding 7).
- Limits of the comparison: one project directory, one user; the parent figure is the only independent source and covers the last request only; 106 of 719 invocations could be matched; the meaning of the 2+2 unexplained differences is unknown.
- Fields read outside the list of FOUNDRY-ADR-0015 (declared, as PAT-134 declared its own, [pat-134-subagent-cache-1h.md](pat-134-subagent-cache-1h.md)): the ad hoc measurement script read `toolUseResult` of the main logs, `toolUseId` of the `*.meta.json`, `version` and `stop_reason` of the records, beyond counters, model alias, timestamp and session identifier. Only aggregate counts were committed, on this page; nothing else was kept.
- Not replayable from the repository (limit): the measurement script is not committed (a script reading the user's own session logs is not shipped), so the figures above cannot be recomputed from this repository; the procedure section describes it and nothing more.
- Reproduction: the procedure is described, not shipped as a script; no test reads real logs. The reader behaviour on synthetic non-terminal requests is covered by the PLP-344 tests (`tests/test_cost_attribution.py`).

## Documentation status (R5)

Updated: [cost-attribution.md](../cost-attribution.md), "Complétude Claude" and the `exact` observation sentence (pointers and the now-false "exact not observed" claim); changelog fragment `PLP-324`. No CLI verb, key or constant changed.
