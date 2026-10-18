# Changelog

## Unreleased

## 0.2.0 — 2026-10-18

- PAT-255: scripted navigation and web. `prove --navigation FILE.json` reaches a screen by a versioned
  deep link (`xcrun simctl openurl`) or a caller-provided UI test (`xcodebuild test -only-testing`, screenshot
  extracted from the `.xcresult` with `xcrun xcresulttool export attachments`) before the capture. New verb `web`
  screenshots a local page (`localhost` / `127.0.0.1` only) in a headless Chromium-family browser with a fresh
  temporary profile, the same command line under Claude Code and Codex (`WITNESS_BROWSER` or known install
  paths; no agent-host browser tool). Reports gain `platform` and `navigation`; `compare` also refuses
  proofs of different platform, navigation (file hash or URL path), viewport or browser version. New skill
  `witness:web`. Real trials per platform are recorded by the coordinator, with unknowns stated.

## 0.1.0 — 2026-10-18

- PAT-254: first brick. `witness_cli.py prove` resolves a simulator name to a UDID, optionally erases it
  (`--reset`), runs `xcodebuild test`, builds, installs, launches, waits `SETTLE_SECONDS` and takes a
  screenshot, then writes a dated JSON and Markdown report (commit and dirty flag, simulator and runtime,
  test summary, measured durations, paths; the coordinator's token cost is `unknown`). `witness_cli.py compare`
  checks two reports share simulator, runtime, scheme and starting state and writes a before/after report.
  Refuses a main checkout. Only `xcodebuild` and `xcrun simctl` are used. Tests are offline with fakes;
  the real proof is run by the coordinator, not by the tests.
