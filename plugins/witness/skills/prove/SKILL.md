---
name: prove
description: >
  Proves an iOS app change in a named simulator: tests, build, install, launch, screenshot and a dated
  report, with xcodebuild and xcrun simctl only. USE WHEN the user asks to prove, check or screenshot an
  iOS change in the simulator, or invokes /witness:prove (Claude Code) or $witness:prove (Codex).
argument-hint: "--project P --scheme S --simulator NAME [--label before|after] [--reset | --state-description TEXT] [--navigation FILE.json]"
allowed-tools: Bash(python3:*)
---

# witness:prove

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<witness-root>` from this skill's
absolute path by removing `/skills/prove/SKILL.md`.

1. The project must be in a linked git worktree or a directory outside any repository, never the
   maintainer's main checkout (the command refuses it). Name the simulator you will use.
2. For a before/after proof, run it twice (`--label before` at the old commit, `--label after` at the new
   one) with the same simulator and the same starting state: `--reset` (erases the simulator, which must
   be shut down) or `--state-description "<text>"`. Witness never shuts a simulator down and leaves it booted:
   with `--reset`, run `xcrun simctl shutdown <UDID>` yourself between the two proofs. Run both with the same
   test options (`compare` refuses a different tests selection).

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<witness-root>")/tooling/witness_cli.py" prove --project <P> --scheme <S> --simulator "<NAME>" --label before --reset
```

3. To capture a given screen, add `--navigation <file>` (a versioned `witness-navigation.v1` JSON of the app's
   repository: `deeplink` with a `url`, or `uitest` with a `test` identifier and an optional `attachment` name
   filter; see the README). Use the same file for the before and the after proof: `compare` refuses a different
   navigation file content. A `uitest` navigation replaces the test run, so do not pass `--skip-tests`,
   `--test-plan` or `--only-testing` with it. The screen name in the file is recorded, not verified: look at
   the screenshot before saying the right screen was reached.
4. Report the printed outcome and the two report paths as they are. A proof whose tests failed still
   has its screenshot and exits 6: say so, never call it proved. Never state a token cost: the report
   says it is unknown.
