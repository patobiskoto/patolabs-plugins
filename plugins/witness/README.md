# witness

Proves a change of an iOS app in the simulator for Claude Code and Codex, with `xcodebuild` and
`xcrun simctl` only (no tool specific to a host). Stdlib-only Python. Version 0.1.0 (PAT-254, decision of PAT-182).

```
python3 plugins/witness/tooling/witness_cli.py prove --project P --scheme S --simulator NAME [--runtime R] [--label L] [--out DIR] [--skip-tests] [--test-plan NAME] [--only-testing T]... [--reset | --state-description TEXT]
python3 plugins/witness/tooling/witness_cli.py compare --before REPORT.json --after REPORT.json [--out DIR]
```

Skills: `/witness:prove`, `/witness:compare` (Claude Code); `$witness:prove`, `$witness:compare` (Codex).

Exit codes (`EXIT_*` in `tooling/witness_cli.py`): 0 done, 2 usage, 3 refused (main checkout, simulator
not found or ambiguous, reset impossible, incomparable proofs), 5 a step failed (a report is still written),
6 tests failed (the screenshot is taken and the report written).

## prove

Steps, in order, each timed with `time.monotonic` (`prove` in `tooling/witness/prove.py`): `reset` (only with
`--reset`), `test` (unless `--skip-tests`), `build`, `find-app`, `boot`, `install`, `launch`, `settle`,
`screenshot`.

- Commands: `xcodebuild ... -destination id=<UDID> -derivedDataPath <out>/derived-data test|build`
  (`tooling/witness/xcode.py`), then `xcrun simctl erase|boot|bootstatus|install|launch|io screenshot`
  (`tooling/witness/simulator.py`). The name is resolved to a UDID with `simctl list devices --json` because
  a name destination once failed to match an existing device (observed in the PAT-182 trial, cause unknown).
- A name matching several simulators is refused unless `--runtime` selects one (`resolve`).
- The built app is the single `*.app` under `<derived>/Build/Products/*-iphonesimulator/` (the one named like the
  scheme if several; `*-Runner.app` ignored); its bundle id is `CFBundleIdentifier` of its `Info.plist`
  (`find_app`).
- `--reset` runs `simctl erase` only if the simulator state is `Shutdown`; witness never shuts one down.
- The test summary is read from the xcodebuild output: `** TEST SUCCEEDED **` / `** TEST FAILED **` and the last
  `Executed N tests, with F failures` line (`summarize`); a field it cannot read is `unknown`.
- Every external process has a timeout (`TIMEOUT_*` constants in `xcode.py`, `simulator.py`, `checkout.py`);
  `SETTLE_SECONDS` is a fixed wait, not an observed readiness.

### Where it may run (AC 4)

`checkout.guard` refuses a project whose git dir equals its common git dir (a main checkout). It accepts a linked
worktree (`git worktree add`) or a directory outside any git repository. Output (default: a new directory in the
system temp dir, `--out`) holds the derived data, logs, screenshot and reports. The simulator used is named in the
report and on the first output line. Erasing and installing do change the named simulator: that is why
`--reset` is explicit.

### Report

`report-<label>-<UTC stamp>.json` and `.md` (`tooling/witness/report.py`, schema `witness-proof.v1`): generation time
(UTC), project name, scheme, checkout kind, commit and dirty flag of the project's checkout (`unknown` outside git),
simulator name, UDID and runtime, starting state (`reset`, `described` with its text, or `unspecified`), test summary,
steps with status and seconds, paths of logs and screenshot, sha256 of the screenshot. The coordinator's token cost
is always the text `TOKEN_COST_UNKNOWN`: witness does not measure it.

## compare

`compare.check` requires the two reports to share the simulator UDID, the runtime, the scheme and the starting state
(`reset` on both, or `described` with the same text); a proof with state `unspecified`, or without a screenshot, is
refused (exit 3). The result (`compare-<UTC stamp>.json`/`.md`, schema `witness-compare.v1`) lists both commits, test
results, durations and screenshots, and `screenshots_identical` from the recorded sha256 (`unknown` if one is missing).
It notes a shared commit and a dirty or unknown checkout.

## Documentation status (R5, FOUNDRY-ADR-0018)

| Artifact | Status |
|---|---|
| CLI verbs `prove`, `compare` and their options | documented here; skills `witness:prove`, `witness:compare` |
| Exit codes, constants `SETTLE_SECONDS`, `TIMEOUT_*`, report schemas | documented here |
| Real-simulator behaviour (boot time, screenshot timing, `Executed` line shape on other Xcode versions) | not covered by the tests (fakes); the coordinator's first real run is the only observation, unknown otherwise |
| Foundry docs | not necessary: no Foundry CLI, constant or schema changed; only its README CI job list and a changelog fragment |
