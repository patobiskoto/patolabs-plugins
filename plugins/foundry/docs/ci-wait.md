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
