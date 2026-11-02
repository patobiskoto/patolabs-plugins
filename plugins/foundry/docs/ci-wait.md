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
`_newest_per_workflow`, key extended by PLP-347), which feeds `write.ci_gate` for `issue wait-ci` and
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

What `check_runs` now does, on the aggregate route and on the check-suites fallback alike
(`_newest_per_workflow`, PLP-347): keep, for each (app, workflow, check name), the run with
the highest check-run id. The workflow is the one behind the run's check suite
(`check_suite.id` of the check-run, matched to `check_suite_id` and `workflow_id` of
`actions/runs?head_sha=<sha>`, `_workflow_by_suite`).
Observed on 2026-11-02 (read-only `gh api`, head sha of PR #296 of this repository): one
workflow run whose `check_suite_id` equals the `check_suite.id` of each of its check-runs, with
its `workflow_id`.

- Deduplicated: the same app, the same workflow and the same check name across suites or
  workflow runs of one sha; the highest id wins ("newest" is the highest id, not a
  timestamp), a newer red run still blocks. Reruns inside a suite are already superseded by
  GitHub's default filter. An app other than GitHub Actions has no workflow: its key is
  (app, check name), as in PLP-336.
- Not deduplicated: the same check name published by two different apps, or by two different
  workflows of one app (all kept, so a red one blocks); a GitHub Actions run whose suite is
  absent from the workflow runs read (or whose lookup answered 404): it is kept apart
  by its own id, the fail-closed choice; commit statuses (the combined status endpoint
  already returns the latest per context, `commit_statuses`; no cross-source merge by name,
  FOUNDRY-ADR-0002).
- Extra API calls: none when no two check-runs share (app, name); otherwise one
  `actions/runs` call per `_PER_PAGE` (100) workflow runs of the sha. Non-404 errors of that
  call propagate like those of the check-run reads. That the workflow-run list is complete
  for the sha (GitHub documents a cap on `actions/runs` results) is not checked here: unknown;
  a suite missing from the list leaves its Actions run unmapped, kept apart by its own id
  (`("unmapped", run["id"])` in `_newest_per_workflow`), which errs on the blocking side.
- Permission: the `actions/runs` lookup needs read access to Actions for the token `gh` uses
  (for a fine-grained token, "Actions: read"; from GitHub's REST documentation, not re-read
  here). A token without it most likely gets 403 (deduced, not observed), which is not a 404
  and so propagates out of `_workflow_by_suite` and `check_runs`; neither `issue wait-ci` nor
  `issue merge` catches it around `write.ci_gate` (`tooling/foundry/issue.py`), nor does the
  campaign `ci_gate` effect (`tooling/foundry/campaign_runtime.py`), so the gate does not open.
  `routing prepare-review` turns it into a `RoutingConfigError` (`review_packet.py`) and the CI
  capture of `evidence_plane.py` lets it propagate. The lookup is made only when two
  check-runs share (app, name).
- Residual risk, deduced: a rerun of one workflow that GitHub files under a different
  `workflow_id` (a workflow file renamed or recreated between runs) is read as a distinct
  workflow, so an old red run of the previous file keeps blocking; this errs on the blocking side.
- The jq expression of `_check_run_records` that names the app (`.app.slug // .app.name // "unknown"`)
  is run for real in `tests/test_pure.py`
  (`test_check_run_records_jq_expression_names_the_app_by_slug_then_name_then_unknown`) with the system
  `jq`, not with the jq embedded in `gh` (unknown whether both agree on `//` for a null `app`; the
  expression uses only the alternative operator).
- The counts above are given as counts; the raw API answer of 2026-10-29 was not kept and cannot
  be reproduced after the fact.
