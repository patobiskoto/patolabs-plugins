---
name: show
description: >
  Prints the last resume state written by relay:write, with the verification results
  recorded at write time. USE WHEN the user asks what the saved session state says, or
  invokes /relay:show (Claude Code) or $relay:show (Codex).
argument-hint: "[--json]"
allowed-tools: Bash(python3:*)
---

# relay:show

Read-only. Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill
reaches the agent. Codex exposes no plugin-root variable: derive `<relay-root>` from this
skill's absolute path by removing `/skills/show/SKILL.md`.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<relay-root>")/tooling/relay_cli.py" show
```

Add `--json` for machine output. The verification shown is the one made at write time, not
a fresh one: use `relay:load` (`relay_cli.py load`) to re-verify it against the current
state before relying on it.
