# Waiting for the CI of a PR: `issue wait-ci` (PAT-274)

R5 status (AGENTS.md#R5, FOUNDRY-ADR-0018): this page documents the new CLI verb
`issue wait-ci`, its options, defaults and exit codes. Source: `wait_ci` and the
`WAIT_CI_*` constants in `tooling/foundry/issue.py`.

```
python3 tooling/foundry_cli.py issue wait-ci <PR-NUMBER> [--timeout=SECONDS] [--interval=SECONDS]
```

- Read-only: `wait_ci` calls `get_pr` once, then `write.ci_gate(..., allow_no_ci=False)`
  on the head sha it read, in a loop (`tooling/foundry/issue.py`). It reads no tracker,
  never merges and writes nothing. The gate is the one of FOUNDRY-ADR-0002 and R4,
  unchanged: both check sources, at least one real `success`; the `--allow-no-ci`
  waiver does not exist here.
- `--timeout` (default `WAIT_CI_DEFAULT_TIMEOUT` = 900 s) is the maximum wait;
  `--interval` (default `WAIT_CI_DEFAULT_INTERVAL` = 15 s) is the delay between two
  reads. Both must be finite numbers > 0 (`nan` and `inf` are refused) and may not be
  repeated, even with the same value (`_wait_ci_seconds`, PAT-286).
- Zero checks, or checks pending, keep waiting (CI queued after a push); a failing
  check ends the wait at once. When every check is complete and none failed nor
  concluded `success` (all `neutral` or `skipped`, for example workflows path-filtered),
  the run is not provably green (`write.ci_gate`, FOUNDRY-ADR-0002): the verb answers at
  once with `verdict=not_green` and exit `2`, without waiting for the timeout. Treating
  that run as final is deduced, not observed: in a repository with several workflows, a
  suite still queued with no check-run yet is not seen, so `not_green` can come before
  that suite reports. The merge gate is unaffected (`write.ci_gate` refuses it too).
- Output (four lines): `verdict=<green|red|not_green|timeout> pr= sha= observed_at=` (UTC,
  `YYYY-MM-DDTHH:MM:SSZ`, taken after the gate reads), `reason=` (the gate's reason),
  `in_progress=[names]`, `failed=[names]`.
- Exit codes: `0` green, `2` red or not green (`not_green`), `3` timeout (`WAIT_CI_EXIT_*`).
  This `3` is the timeout of `wait-ci`; it is not the exit code 3 of `issue merge`
  (`EXIT_LOCAL_CLEANUP_FAILED`). A refusal raised as `SystemExit` with a message (unknown
  flag, invalid or repeated option) exits `1`. An error that `wait_ci` does not catch (a
  `RuntimeError` or `TypeError` from the code host, a `ValueError` from a non-numeric
  `<PR-NUMBER>`) ends the process with a Python traceback and exit `1` (deduced from the
  interpreter's behaviour on an uncaught exception, checked on the non-numeric PR number in
  `tests/test_issue_wait_ci.py`), not through `SystemExit`.
- A push during the wait is not followed: the verdict is for the printed `sha`.

## Limits of the CI gate's check-run reading (PLP-336)

R5 status (AGENTS.md#R5, FOUNDRY-ADR-0018): documents the check-run deduplication of
`GitHubCodeHost.check_runs` (`tooling/foundry/codehosts/github.py`,
`_newest_per_app_and_name`), which feeds `write.ci_gate` for `issue wait-ci` and
`issue merge`, and also `evidence_plane.capture_ci_receipts` (`foundry-ci-receipt.v1`,
source `check_runs`) and the `check_runs` of the review packet (`review_packet.py`): the
counts of those two follow the deduplicated set too. The gate semantics of FOUNDRY-ADR-0002 are unchanged.

Observed on 2026-10-29 (read-only `gh api`, a private repository of the maintainer, PR of
2026-10-28, head sha carrying 3 runs of one workflow `ci`, each in its own check suite:
two `workflow_dispatch` runs and one `pull_request` run):

- `commits/{sha}/check-runs` with the default filter returned `total_count` 20: 7 + 7 + 6
  check-runs, one group per suite; the same check names (`check`, `tracker-planning`, ...)
  appeared once per suite, all from app `github-actions`.
- the same endpoint with `filter=all` returned `total_count` 40; the 20 extra were earlier
  attempts inside the same suites, among them `cancelled` and `failure` runs.
- Conclusion, observed for the structure: the default filter supersedes reruns within one
  check suite only, not distinct check suites (distinct workflow runs) on the same sha. The
  earlier belief that the aggregate endpoint is latest-only across runs was wrong. That an
  older run's `cancelled` check was what blocked the gate at the time of the original report
  is deduced, not observed (the state at that moment was not captured).

What `check_runs` now does, on the aggregate route and on the check-suites fallback alike:
keep, for each (app, check name), the run with the highest check-run id.

- Deduplicated: the same app and the same check name across suites or workflow runs of one
  sha; the highest id wins, a newer red run still blocks. Reruns inside a suite are already
  superseded by GitHub's default filter.
- Not deduplicated: the same check name published by two different apps (both kept, so a
  red one from either app blocks); commit statuses (the combined status endpoint already
  returns the latest per context, `commit_statuses`; no cross-source merge by name,
  FOUNDRY-ADR-0002).
- Residual risk, deduced: two different workflows of the same app that publish the same job
  name collapse to the newest; a red one with the lower id is then not seen. "Newest" is the
  highest id, not a timestamp (same rule as before on the fallback route).
  This is a gate opening in a real case, not only a theoretical one. Adding the workflow
  identity to the key is a behaviour change left to intake (AGENTS.md#R3), not made here.
- The jq expression of `_check_run_records` that names the app (`.app.slug // .app.name // "unknown"`)
  is run for real in `tests/test_pure.py`
  (`test_check_run_records_jq_expression_names_the_app_by_slug_then_name_then_unknown`) with the system
  `jq`, not with the jq embedded in `gh` (unknown whether both agree on `//` for a null `app`; the
  expression uses only the alternative operator).
- The counts above are given as counts; the raw API answer of 2026-10-29 was not kept and cannot
  be reproduced after the fact.
