# Model-routing pilot results v1

Protocol: [`FOUNDRY-MR-PILOT-v1`](model-routing-pilot-v1.md)  
Pilot issue: FOUNDRY-11  
Status: `COMPLETE`

Only fill cells and append invocation rows. Do not change table columns, formulas, or
protocol text during P-01…P-10. No prompt, response, diff, environment value, account
identifier, or secret belongs in this file.

## Frozen price snapshot

Complete every price cell, source, and timestamp before P-01. Add the declared primary
model if it is not already listed; adding one after P-01 invalidates the series.

| host | model | uncached_input_usd_per_mtok | cache_read_usd_per_mtok | cache_write_standard_usd_per_mtok | cache_write_extended_usd_per_mtok | output_usd_per_mtok | source | captured_at_utc |
|---|---|---:|---:|---:|---:|---:|---|---|
| claude | haiku-4.5 | 1.00 | 0.10 | 1.25 | 2.00 | 5.00 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-08-17T21:04:21Z |
| claude | sonnet-5 | 2.00 | 0.20 | 2.50 | 4.00 | 10.00 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-08-17T21:04:21Z |
| claude | opus-5 | 5.00 | 0.50 | 6.25 | 10.00 | 25.00 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-08-17T21:04:21Z |
| claude | fable-5 | 10.00 | 1.00 | 12.50 | 20.00 | 50.00 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-08-17T21:04:21Z |
| codex | gpt-5.6-luna | 0.20 | 0.02 | 0.25 | N/A | 1.20 | https://developers.openai.com/api/docs/models/gpt-5.6-luna | 2026-08-17T21:04:21Z |
| codex | gpt-5.6-terra | 2.00 | 0.20 | 2.50 | N/A | 12.00 | https://developers.openai.com/api/docs/models/gpt-5.6-terra | 2026-08-17T21:04:21Z |
| codex | gpt-5.6-sol | 5.00 | 0.50 | 6.25 | N/A | 30.00 | https://developers.openai.com/api/docs/models/gpt-5.6-sol | 2026-08-17T21:04:21Z |

## Invocation ledger

One row per main-loop or delegated invocation. `component` is exactly `main_loop` or
`subagent`; main-loop rows use role `coordinator`.

| slot | issue_id | issue_type | estimate | host | component | role | model | effort | escalation_count | failure_signal | uncached_input_tokens | cache_read_tokens | cache_write_standard_tokens | cache_write_extended_tokens | output_tokens | retries | actual_cost_usd | review_verdict | evidence_note |
|---|---|---|---:|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| P-01 | FOUNDRY-13 | fix | 5 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 0 | none | 71780 | 7252480 | 0 | 0 | 15765 | 0 | 4.45809000 | N/A | local Codex usage delta; 54 model calls aggregated from issue start to done |
| P-01 | FOUNDRY-13 | fix | 5 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 39759 | 100608 | 0 | 0 | 1006 | 0 | 0.01117116 | N/A | routed economy scout; 3 model calls aggregated |
| P-01 | FOUNDRY-13 | fix | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | review_blocking | 56101 | 412416 | 0 | 0 | 5702 | 0 | 0.65777300 | BLOCK | claimed diff 20b45f9; 13 model calls aggregated |
| P-01 | FOUNDRY-13 | fix | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | none | 55677 | 425472 | 0 | 0 | 6085 | 1 | 0.67367100 | PASS | claimed corrected diff ca0f540; 15 model calls aggregated |
| P-02 | FOUNDRY-14 | fix | 5 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 0 | none | 45078 | 4535040 | 0 | 0 | 6371 | 0 | 2.68404000 | N/A | local Codex usage delta; 25 model calls aggregated from issue start to done |
| P-02 | FOUNDRY-14 | fix | 5 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 45315 | 9984 | 0 | 0 | 771 | 0 | 0.01018788 | N/A | routed economy scout; 1 model call |
| P-02 | FOUNDRY-14 | fix | 5 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | none | 41321 | 435456 | 0 | 0 | 4944 | 0 | 0.22906120 | N/A | routed balanced implementer; 15 model calls aggregated |
| P-02 | FOUNDRY-14 | fix | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | none | 59008 | 414464 | 0 | 0 | 6230 | 0 | 0.68917200 | PASS | claimed diff 101ae11; 13 model calls aggregated |
| P-03 | FOUNDRY-15 | fix | 3 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 0 | none | 118383 | 3669248 | 0 | 0 | 12840 | 0 | 2.81173900 | N/A | local Codex usage delta; 60 model calls aggregated from issue start to done |
| P-03 | FOUNDRY-15 | fix | 3 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | none | 34364 | 616704 | 0 | 0 | 5933 | 0 | 0.26326480 | N/A | local Codex usage delta; initial invocation; 19 model calls aggregated |
| P-03 | FOUNDRY-15 | fix | 3 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | review_blocking | 13724 | 451072 | 0 | 0 | 2583 | 1 | 0.14865840 | N/A | local Codex usage delta; review correction; 10 model calls aggregated |
| P-03 | FOUNDRY-15 | fix | 3 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | review_blocking | 63834 | 433408 | 0 | 0 | 7171 | 0 | 0.75100400 | BLOCK | claimed diff d7826e2; malformed-suite fail-open found; 15 model calls aggregated |
| P-03 | FOUNDRY-15 | fix | 3 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | none | 87415 | 694528 | 0 | 0 | 7768 | 1 | 1.01737900 | PASS | claimed corrected diff 65898b0; 19 model calls aggregated |
| P-04 | FOUNDRY-16 | fix | 3 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 0 | none | 55768 | 3438336 | 0 | 0 | 8570 | 0 | 2.25510800 | N/A | local Codex usage delta; 29 model calls aggregated from issue start to done |
| P-04 | FOUNDRY-16 | fix | 3 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 40333 | 181504 | 0 | 0 | 1980 | 0 | 0.01407268 | N/A | routed economy scout; 8 model calls aggregated |
| P-04 | FOUNDRY-16 | fix | 3 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | none | 35172 | 448256 | 0 | 0 | 5195 | 0 | 0.22233520 | N/A | routed balanced implementer; 13 model calls aggregated |
| P-04 | FOUNDRY-16 | fix | 3 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | none | 68126 | 475392 | 0 | 0 | 6096 | 0 | 0.76120600 | PASS | claimed diff 4b2a3fa; 15 model calls aggregated |
| P-05 | FOUNDRY-17 | feature | 5 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 1 | none | 100019 | 12470016 | 0 | 0 | 18541 | 0 | 7.29133300 | N/A | local Codex usage delta; 69 model calls aggregated from issue start to done |
| P-05 | FOUNDRY-17 | feature | 5 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | none | 54441 | 935680 | 0 | 0 | 8932 | 0 | 0.40320200 | N/A | local Codex usage delta; initial invocation; 21 model calls aggregated |
| P-05 | FOUNDRY-17 | feature | 5 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | review_blocking | 27524 | 834560 | 0 | 0 | 7373 | 1 | 0.31043600 | N/A | local Codex usage delta; review correction; 12 model calls aggregated |
| P-05 | FOUNDRY-17 | feature | 5 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 43391 | 188672 | 0 | 0 | 2123 | 0 | 0.01499924 | N/A | routed economy scout; 7 model calls aggregated |
| P-05 | FOUNDRY-17 | feature | 5 | codex | subagent | implementer | gpt-5.6-sol | high | 1 | none | 55473 | 736512 | 0 | 0 | 5782 | 2 | 0.81908100 | N/A | auto-escalated frontier implementer; 19 model calls aggregated |
| P-05 | FOUNDRY-17 | feature | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | review_blocking | 91798 | 1068800 | 0 | 0 | 9661 | 0 | 1.28322000 | BLOCK | claimed diff 4f3a9c8; stale-resume and free-text-secret blockers; 21 model calls |
| P-05 | FOUNDRY-17 | feature | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | review_blocking_after_fix | 72616 | 438016 | 0 | 0 | 9105 | 1 | 0.85523800 | BLOCK | claimed corrected diff 009d6fd; argparse stderr leak found; 13 model calls |
| P-05 | FOUNDRY-17 | feature | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 1 | none | 106984 | 729344 | 0 | 0 | 10934 | 2 | 1.22761200 | PASS | claimed corrected diff 3c957ca; 19 model calls aggregated |
| P-06 | FOUNDRY-18 | feature | 5 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 1 | none | 224615 | 4891392 | 0 | 0 | 24747 | 0 | 4.31118100 | N/A | local Codex usage delta; 69 model calls aggregated from issue start to done |
| P-06 | FOUNDRY-18 | feature | 5 | codex | subagent | implementer | gpt-5.6-sol | high | 1 | none | 109400 | 3444480 | 0 | 0 | 26219 | 0 | 3.05581000 | N/A | local Codex usage delta; initial invocation; 53 model calls aggregated |
| P-06 | FOUNDRY-18 | feature | 5 | codex | subagent | implementer | gpt-5.6-sol | high | 1 | review_blocking | 59740 | 689920 | 0 | 0 | 8062 | 1 | 0.88552000 | N/A | local Codex usage delta; review correction; 17 model calls aggregated |
| P-06 | FOUNDRY-18 | feature | 5 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 44757 | 245760 | 0 | 0 | 1844 | 0 | 0.01607940 | N/A | routed economy scout; 9 model calls aggregated |
| P-06 | FOUNDRY-18 | feature | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 1 | review_blocking | 75577 | 840704 | 0 | 0 | 9010 | 0 | 1.06853700 | BLOCK | claimed diff a286a91; recovered Codex spawn path missing; 20 model calls aggregated |
| P-06 | FOUNDRY-18 | feature | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 1 | none | 105980 | 1560064 | 0 | 0 | 11919 | 1 | 1.66750200 | PASS | claimed corrected diff 24a75a1; 26 model calls aggregated |
| P-07 | FOUNDRY-19 | feature | 5 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 0 | none | 104086 | 7538176 | 0 | 0 | 16637 | 0 | 4.78862800 | N/A | local Codex usage delta; 54 model calls aggregated from issue start to done |
| P-07 | FOUNDRY-19 | feature | 5 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | none | 59611 | 1068288 | 0 | 0 | 10265 | 0 | 0.45605960 | N/A | local Codex usage delta; initial/pre-review refinement invocation; 23 model calls aggregated |
| P-07 | FOUNDRY-19 | feature | 5 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | review_blocking | 38389 | 320512 | 0 | 0 | 3768 | 1 | 0.18609640 | N/A | local Codex usage delta; review correction; 12 model calls aggregated |
| P-07 | FOUNDRY-19 | feature | 5 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 49029 | 247808 | 0 | 0 | 2200 | 0 | 0.01740196 | N/A | routed economy scout; 9 model calls aggregated |
| P-07 | FOUNDRY-19 | feature | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | review_blocking | 61460 | 583936 | 0 | 0 | 8211 | 0 | 0.84559800 | BLOCK | claimed diff 462380b; warnings and health degradation hidden; 15 model calls aggregated |
| P-07 | FOUNDRY-19 | feature | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | none | 82519 | 1040896 | 0 | 0 | 10662 | 1 | 1.25290300 | PASS | claimed corrected diff 076480c; 22 model calls aggregated |
| P-08 | FOUNDRY-20 | chore | 5 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 2 | none | 415716 | 22331904 | 0 | 0 | 53404 | 0 | 14.84665200 | N/A | local Codex usage delta; 212 model calls aggregated from issue start to done, including two human-approved resumes |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 55205 | 213504 | 0 | 0 | 2548 | 0 | 0.01836868 | N/A | routed economy scout; 7 model calls aggregated before security-risk escalation |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | implementer | gpt-5.6-sol | high | 1 | review_blocking | 99083 | 2206720 | 0 | 0 | 27834 | 0 | 2.43379500 | N/A | security-risk escalated frontier implementation; 40 model calls aggregated |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 1 | review_blocking | 69679 | 542464 | 0 | 0 | 15714 | 0 | 1.09104700 | BLOCK | claimed diff 00cc85a; skip, cleanup, target-config, and PR-secret blockers; 13 model calls |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | implementer | gpt-5.6-sol | high | 1 | review_blocking_after_fix | 63082 | 1005056 | 0 | 0 | 10505 | 1 | 1.13308800 | N/A | frontier correction for first blocking review; 22 model calls aggregated |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 1 | review_blocking_after_fix | 68841 | 659712 | 0 | 0 | 16729 | 1 | 1.17593100 | BLOCK | claimed diff e5cb4de; ambient production override and job-level secret blockers; 15 model calls |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | implementer | gpt-5.6-sol | max | 2 | review_blocking_after_fix | 52194 | 572416 | 0 | 0 | 9802 | 2 | 0.84123800 | N/A | auto-escalated apex correction; 16 model calls aggregated |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 2 | review_blocking_after_fix | 97191 | 1192960 | 0 | 0 | 17189 | 2 | 1.59810500 | BLOCK | claimed diff d554596; persistent-runner ambient token exposure found; 20 model calls |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | implementer | gpt-5.6-sol | max | 2 | review_blocking_after_fix | 47030 | 518400 | 0 | 0 | 8078 | 3 | 0.73669000 | N/A | first human-approved apex retry neutralizing all ambient smoke inputs; 15 model calls |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 2 | review_blocking_after_fix | 75312 | 564992 | 0 | 0 | 17688 | 3 | 1.18969600 | BLOCK | claimed diff b2a68b4; cleanup target and cross-origin redirect blockers; 13 model calls |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | implementer | gpt-5.6-sol | max | 2 | none | 115604 | 3923968 | 0 | 0 | 39763 | 4 | 3.73289400 | N/A | second human-approved apex retry; project-marker cleanup validation and redirect guard; 56 model calls |
| P-08 | FOUNDRY-20 | chore | 5 | codex | subagent | reviewer | gpt-5.6-sol | high | 2 | none | 106610 | 1488640 | 0 | 0 | 19472 | 4 | 1.86153000 | PASS | claimed corrected diff 74c47ee; 25 model calls aggregated |
| P-09 | FOUNDRY-21 | chore | 3 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 0 | none | 200112 | 11717376 | 0 | 0 | 29694 | 0 | 7.75006800 | N/A | local Codex usage delta; 99 model calls aggregated from issue start to done |
| P-09 | FOUNDRY-21 | chore | 3 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | none | 55208 | 750592 | 0 | 0 | 9873 | 0 | 0.37901040 | N/A | local Codex usage delta; initial invocation; 24 model calls aggregated |
| P-09 | FOUNDRY-21 | chore | 3 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | review_blocking | 32095 | 506880 | 0 | 0 | 6729 | 1 | 0.24631400 | N/A | local Codex usage delta; review correction; 16 model calls aggregated |
| P-09 | FOUNDRY-21 | chore | 3 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 34974 | 113408 | 0 | 0 | 2185 | 0 | 0.01188496 | N/A | routed economy scout; 6 model calls aggregated |
| P-09 | FOUNDRY-21 | chore | 3 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | review_blocking | 38408 | 326656 | 0 | 0 | 9327 | 0 | 0.63517800 | BLOCK | claimed diff 56bfcfa; tautological hook parity and unconfined paths found; 12 model calls aggregated |
| P-09 | FOUNDRY-21 | chore | 3 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | none | 54603 | 681216 | 0 | 0 | 8301 | 1 | 0.86265300 | PASS | claimed corrected diff 16307f5; 21 model calls aggregated |
| P-10 | FOUNDRY-22 | chore | 2 | codex | main_loop | coordinator | gpt-5.6-sol | xhigh | 0 | none | 79875 | 8681216 | 0 | 0 | 15265 | 0 | 5.19793300 | N/A | local Codex usage delta; 57 model calls aggregated from issue start to done |
| P-10 | FOUNDRY-22 | chore | 2 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | none | 22362 | 315392 | 0 | 0 | 4670 | 0 | 0.16384240 | N/A | local Codex usage delta; initial invocation; 12 model calls aggregated |
| P-10 | FOUNDRY-22 | chore | 2 | codex | subagent | implementer | gpt-5.6-terra | medium | 0 | review_blocking | 16421 | 191488 | 0 | 0 | 2894 | 1 | 0.10586760 | N/A | local Codex usage delta; review correction; 8 model calls aggregated |
| P-10 | FOUNDRY-22 | chore | 2 | codex | subagent | scout | gpt-5.6-luna | low | 0 | none | 34067 | 107264 | 0 | 0 | 1538 | 0 | 0.01080428 | N/A | routed economy scout; 6 model calls aggregated |
| P-10 | FOUNDRY-22 | chore | 2 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | review_blocking | 46345 | 383744 | 0 | 0 | 8335 | 0 | 0.67364700 | BLOCK | claimed diff 010aa0b; whitespace normalization contradicted AC2; 13 model calls aggregated |
| P-10 | FOUNDRY-22 | chore | 2 | codex | subagent | reviewer | gpt-5.6-sol | high | 0 | none | 38295 | 539392 | 0 | 0 | 6268 | 1 | 0.64921100 | PASS | claimed corrected diff ad6b874; 17 model calls aggregated |

## Per-issue summary

| slot | issue_id | issue_type | estimate | host | primary_model | primary_effort | main_loop_cost_usd | subagent_cost_usd | actual_cost_usd | counterfactual_cost_usd | savings_usd | savings_pct | escalation_count | retries | review_verdict | valid |
|---|---|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| P-01 | FOUNDRY-13 | fix | 5 | codex | gpt-5.6-sol | xhigh | 4.45809000 | 1.34261516 | 5.80070516 | 6.06881300 | 0.26810784 | 4.42 | 0 | 1 | PASS | yes |
| P-02 | FOUNDRY-14 | fix | 5 | codex | gpt-5.6-sol | xhigh | 2.68404000 | 0.92842108 | 3.61246108 | 4.20056200 | 0.58810092 | 14.00 | 0 | 0 | PASS | yes |
| P-03 | FOUNDRY-15 | fix | 3 | codex | gpt-5.6-sol | xhigh | 2.81173900 | 2.18030620 | 4.99204520 | 5.60993000 | 0.61788480 | 11.01 | 0 | 1 | PASS | yes |
| P-04 | FOUNDRY-16 | fix | 3 | codex | gpt-5.6-sol | xhigh | 2.25510800 | 0.99761388 | 3.25272188 | 3.92396900 | 0.67124712 | 17.11 | 0 | 0 | PASS | yes |
| P-05 | FOUNDRY-17 | feature | 5 | codex | gpt-5.6-sol | xhigh | 7.29133300 | 4.91378824 | 12.20512124 | 13.63556000 | 1.43043876 | 10.49 | 1 | 2 | PASS | yes |
| P-06 | FOUNDRY-18 | feature | 5 | codex | gpt-5.6-sol | xhigh | 4.31118100 | 6.69344840 | 11.00462940 | 11.39053500 | 0.38590560 | 3.39 | 1 | 1 | PASS | yes |
| P-07 | FOUNDRY-19 | feature | 5 | codex | gpt-5.6-sol | xhigh | 4.78862800 | 2.75805896 | 7.54668696 | 8.92756800 | 1.38088104 | 15.47 | 0 | 1 | PASS | yes |
| P-08 | FOUNDRY-20 | chore | 5 | codex | gpt-5.6-sol | xhigh | 14.84665200 | 15.81238268 | 30.65903468 | 31.09988300 | 0.44084832 | 1.42 | 2 | 4 | PASS | yes |
| P-09 | FOUNDRY-21 | chore | 3 | codex | gpt-5.6-sol | xhigh | 7.75006800 | 2.13504036 | 9.88510836 | 11.10833400 | 1.22322564 | 11.01 | 0 | 1 | PASS | yes |
| P-10 | FOUNDRY-22 | chore | 2 | codex | gpt-5.6-sol | xhigh | 5.19793300 | 1.60337228 | 6.80130528 | 7.46517300 | 0.66386772 | 8.89 | 0 | 1 | PASS | yes |

## Pilot totals and decision

| valid_issues | main_loop_cost_usd | subagent_cost_usd | actual_cost_usd | counterfactual_cost_usd | savings_usd | savings_pct | target_met |
|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 56.39477200 | 39.36504724 | 95.75981924 | 103.43032700 | 7.67050776 | 7.42 | no |

Final decision after P-10: `ADJUST` — keep model-aware subagent routing, but do not
generalize the −40% claim. The frozen protocol measured 7.42% overall savings; the main
loop still accounts for 58.89% of actual cost, while review gates and deterministic
retries correctly remain on frontier models. A separate v2 experiment must target the
recommended main-loop profile and retry reduction without rewriting P-01…P-10. All ten
issues merged with a final PASS review and no known post-merge regression at pilot close. AC6: `INCONCLUSIVE` — no frozen pre-pilot baseline exists for missed ACs, retries, or regressions, so no causal comparison can be made. Final PASS reviews and no known regression are observations, not proof that these outcomes did not increase. V2 must freeze that baseline before comparison.
Stratified mix summary: `4 fix, 3 feature, 3 chore; estimates: 1×2, 3×3, 6×5; six issues ≥5`.
