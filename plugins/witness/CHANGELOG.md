# Changelog

## Unreleased

## 0.1.0 — 2026-10-18

- PAT-254: first brick. `witness_cli.py prove` resolves a simulator name to a UDID, optionally erases it
  (`--reset`), runs `xcodebuild test`, builds, installs, launches, waits `SETTLE_SECONDS` and takes a
  screenshot, then writes a dated JSON and Markdown report (commit and dirty flag, simulator and runtime,
  test summary, measured durations, paths; the coordinator's token cost is `unknown`). `witness_cli.py compare`
  checks two reports share simulator, runtime, scheme and starting state and writes a before/after report.
  Refuses a main checkout. Only `xcodebuild` and `xcrun simctl` are used. Tests are offline with fakes;
  the real proof is run by the coordinator, not by the tests.
