# witness

Proves a change of an iOS app in the simulator, or of a local web page in a headless browser, for Claude Code
and Codex. iOS uses `xcodebuild`, `xcrun simctl` and `xcrun xcresulttool` only; web uses a Chromium-family
browser command line. No tool specific to an agent host is used on either platform: in particular no
agent-host browser tool. Stdlib-only Python. Version 0.2.0 (PAT-254, PAT-255, decision of PAT-182).

```
python3 plugins/witness/tooling/witness_cli.py prove --project P --scheme S --simulator NAME [--runtime R] [--label L] [--out DIR] [--skip-tests] [--test-plan NAME] [--only-testing T]... [--reset | --state-description TEXT] [--navigation FILE.json]
python3 plugins/witness/tooling/witness_cli.py web --project DIR --url http://localhost:PORT/PATH [--viewport WxH] [--label L] [--out DIR]
python3 plugins/witness/tooling/witness_cli.py compare --before REPORT.json --after REPORT.json [--out DIR]
```

Skills: `/witness:prove`, `/witness:web`, `/witness:compare` (Claude Code); `$witness:prove`, `$witness:web`, `$witness:compare` (Codex).

Exit codes (`EXIT_*` in `tooling/witness_cli.py`): 0 done, 2 usage, 3 refused (main checkout, simulator
not found or ambiguous, invalid navigation file, non-local URL, no browser, incomparable proofs), 5 a step failed, including a `reset` refused because the simulator
is not shut down (a report is still written), 6 the tests result is `failed` or `unknown` (the screenshot is
taken and the report written).

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
- `--reset` runs `simctl erase` only if the simulator state is `Shutdown`; witness never shuts one down, and
  `ensure_booted` leaves it booted. For a before/after pair with `--reset`, run
  `xcrun simctl shutdown <UDID>` yourself before each proof after the first.
- The test summary is read from the xcodebuild output: `** TEST SUCCEEDED **` / `** TEST FAILED **` and the last
  `Executed N tests, with F failures` line (`summarize`); a field it cannot read is `unknown`.
- Every external process has a timeout (`TIMEOUT_*` constants in `xcode.py`, `simulator.py`, `checkout.py`, `web.py`);
  `SETTLE_SECONDS` is a fixed wait, not an observed readiness.

### Scripted navigation (`--navigation`, PAT-255)

`--navigation FILE.json` reaches a given screen before the capture. The file lives in the app's repository
(versioned) and is validated by `navigation.load` (`tooling/witness/navigation.py`, schema
`witness-navigation.v1`, at most `MAX_BYTES`). Two mechanisms, both Apple tools only (`simctl` has no tap
command):

```json
{"schema": "witness-navigation.v1", "screen": "Session", "mechanism": "deeplink", "url": "myscheme://session"}
{"schema": "witness-navigation.v1", "screen": "Session", "mechanism": "uitest",
 "test": "UITarget/Class/testName", "attachment": "optional name filter"}
```

- `deeplink`: after `launch` and `settle`, `xcrun simctl openurl <UDID> <url>` (`simulator.open_url`), then a
  second `SETTLE_SECONDS` wait, then the screenshot. It needs an app that declares a URL scheme.
- `uitest`: the navigation is an XCUITest you provide. Witness runs it as the only test
  (`xcodebuild test -only-testing:<test> -resultBundlePath <out>/navigation-*.xcresult`, `xcode.run_tests`),
  then extracts the image with `xcrun xcresulttool export attachments` (`xcode.export_screenshot`). The test must
  attach a screenshot (`XCTAttachment(screenshot:)`, kept with `lifetime = .keepAlways`); a test that only writes a
  file elsewhere (for example a Fastlane `snapshot()` helper) attaches nothing and fails the step. With several
  image attachments, `attachment` (a substring of the attachment name) is required: witness never guesses. The
  steps are `reset`, `boot`, `uitest`, `extract-screenshot`; there is no separate build, install or launch (the
  test run does them); `--skip-tests`, `--test-plan` and `--only-testing` are refused (exit 2) and the tests
  selection is recorded as `navigation-uitest`. Tests failed or unknown still exits 6 with the screenshot.
- `screen` is recorded in the report and never verified: witness cannot read the screen. The sha256 of the file
  bytes identifies the navigation (`navigation.identity`).

## web (PAT-255)

`web` (`tooling/witness/web.py`) opens a local page in a headless browser started with a fresh temporary
`--user-data-dir` (removed afterwards, so no user profile, cookie or extension is involved) and takes the
screenshot at a fixed viewport (default `DEFAULT_VIEWPORT`, scale factor 1):

- Only `http://localhost[:port]/...` and `http://127.0.0.1[:port]/...` without credentials are accepted
  (`check_url`, `LOCAL_HOSTS`); anything else is refused (exit 3) before any process runs.
- A Chromium-family browser is required: Google Chrome, Chromium, Brave or Edge. `locate_browser` uses
  `WITNESS_BROWSER` if set (and refuses if it is not an executable, no fallback), else `KNOWN_BROWSERS` (macOS
  install paths), else `PATH_NAMES` on `PATH`; otherwise it refuses with a message. The command line is
  `--headless=new --user-data-dir=<tmp> --window-size=W,H --screenshot=<png> --virtual-time-budget=<VIRTUAL_TIME_BUDGET_MS> <url>`
  plus fixed flags listed in `capture`.
- The checkout guard and the commit/dirty flag apply to `--project` (the directory of the web app, in a linked
  worktree or outside git). The server is not started or stopped by witness: you serve the page.
- Host equivalence (AC 2): the browser command line is identical under Claude Code and Codex. No host-specific
  browser tool (for example a Chrome extension tool of an agent host) is used, because it has no counterpart on
  the other host; a page that needs a login, a non-local host, or interaction is out of scope.
- Steps: a single `screenshot`. The report has `platform: "web"`, `url`, `viewport`, `browser` (path, version
  read with `--version` or `unknown`, profile `fresh temporary`), `navigation.path` (URL path and query).

### Where it may run (AC 4)

`checkout.guard` refuses a project whose git dir equals its common git dir (a main checkout). It accepts a linked
worktree (`git worktree add`) or a directory outside any git repository. Output (default: a new directory in the
system temp dir, `--out`) holds the derived data, logs, screenshot and reports. The simulator used is named in the
report and on the first output line. Erasing and installing do change the named simulator: that is why
`--reset` is explicit.

### Report

`report-<label>-<UTC stamp>.json` and `.md` (`tooling/witness/report.py`, schema `witness-proof.v1`; PAT-255 added the
fields `platform` and `navigation`, a 0.1.0 report without `platform` is read as `ios`): generation time
(UTC), project name, scheme, checkout kind, commit and dirty flag of the project's checkout (`unknown` outside git),
simulator name, UDID and runtime, starting state (`reset`, `described` with its text, or `unspecified`, plus `tests_selection`: `skipped`,
`only-testing`, `test-plan`, `test-plan+only-testing` or `all`), test summary,
steps with status and seconds, paths of logs and screenshot, sha256 of the screenshot. The coordinator's token cost
is always the text `TOKEN_COST_UNKNOWN`: witness does not measure it.

## compare

`compare.check` requires the two reports to share the platform (`ios` or `web`) and the navigation identity
(`compare.nav_identity`: iOS mechanism and navigation file sha256, or none on both; web URL path and query). For
iOS it then requires the simulator UDID, the runtime, the scheme and the starting state
(`reset` on both, or `described` with the same text) and the same `tests_selection` (a test run installs and
launches the app after the reset, deduced, not observed). For web it requires the same viewport and browser
version string (a different browser can render differently, deduced); a proof with state `unspecified`, or without a screenshot, is
refused (exit 3). The result (`compare-<UTC stamp>.json`/`.md`, schema `witness-compare.v1`) lists both commits, test
results, durations and screenshots, and `screenshots_identical` from the recorded sha256 (`unknown` if one is missing).
It notes a shared commit and a dirty or unknown checkout.

## Documentation status (R5, FOUNDRY-ADR-0018)

| Artifact | Status |
|---|---|
| CLI verbs `prove`, `web`, `compare` and their options, `--navigation` | documented here; skills `witness:prove`, `witness:web`, `witness:compare` |
| Navigation file schema `witness-navigation.v1`, `WITNESS_BROWSER`, `KNOWN_BROWSERS`, `LOCAL_HOSTS`, `DEFAULT_VIEWPORT`, `VIRTUAL_TIME_BUDGET_MS` | documented here |
| Exit codes, constants `SETTLE_SECONDS`, `TIMEOUT_*`, report schemas, report fields `platform`, `navigation`, `browser`, `viewport`, `url` | documented here |
| Real-simulator behaviour (boot time, screenshot timing, `Executed` line shape on other Xcode versions), `openurl` on an app, `xcresulttool` manifest shape on other Xcode versions, UI test attachment lifetime | not covered by the tests (fakes); the coordinator's real runs are the only observation, unknown otherwise |
| Web: headless run of the local read-only probe (page served by `http.server`, Chrome 155, 1280x800 PNG, about 1.2 s) | observed once at implementation, on a copy; other browsers and versions unknown; dynamic pages may need more than `VIRTUAL_TIME_BUDGET_MS` (unknown) |
| Token cost of the trials | not measured by witness (`TOKEN_COST_UNKNOWN`); the trial records are the coordinator's (AC 3) |
| Foundry docs | not necessary: no Foundry CLI, constant or schema changed; only its README CI job list and a changelog fragment |
