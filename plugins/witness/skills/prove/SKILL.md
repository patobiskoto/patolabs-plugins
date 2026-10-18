---
name: prove
description: >
  Proves an iOS app change in a named simulator: tests, build, install, launch, screenshot and a dated
  report, with xcodebuild and xcrun simctl only. USE WHEN the user asks to prove, check or screenshot an
  iOS change in the simulator, or invokes /witness:prove (Claude Code) or $witness:prove (Codex).
argument-hint: "--project P --scheme S --simulator NAME [--label before|after] [--reset | --state-description TEXT]"
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
   be shut down) or `--state-description "<text>"`.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<witness-root>")/tooling/witness_cli.py" prove --project <P> --scheme <S> --simulator "<NAME>" --label before --reset
```

3. Report the printed outcome and the two report paths as they are. A proof whose tests failed still
   has its screenshot and exits 6: say so, never call it proved. Never state a token cost: the report
   says it is unknown.
