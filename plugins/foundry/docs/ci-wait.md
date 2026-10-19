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
  reads. Both must be numbers > 0 and may not be repeated.
- Zero checks, or checks pending, keep waiting (CI queued after a push); a failing
  check ends the wait at once.
- Output (four lines): `verdict=<green|red|timeout> pr= sha= observed_at=` (UTC,
  `YYYY-MM-DDTHH:MM:SSZ`, taken after the gate reads), `reason=` (the gate's reason),
  `in_progress=[names]`, `failed=[names]`.
- Exit codes: `0` green, `2` red, `3` timeout (`WAIT_CI_EXIT_*`). Any refusal or
  error (unknown flag, unreadable PR) exits `1` through `SystemExit` with a message.
- A push during the wait is not followed: the verdict is for the printed `sha`.
