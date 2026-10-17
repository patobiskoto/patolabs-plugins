---
name: status
description: >
  Shows the current quota counters (Codex, Claude, Linear, GitHub Actions) with source and
  read time; unreadable data is reported as unknown, never zero. USE WHEN the user asks
  for their quotas, rate limits or remaining usage, invokes /gauge:status (Claude Code) or
  $gauge:status (Codex).
argument-hint: "[--json] [--claude-stream PATH] [--claude-statusline-json PATH]"
allowed-tools: Bash(python3:*)
---

# gauge:status

Read-only. Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill
reaches the agent. Codex exposes no plugin-root variable: derive `<gauge-root>` from this
skill's absolute path by removing `/skills/status/SKILL.md`.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<gauge-root>")/tooling/gauge_cli.py" status
```

Add `--json` for machine output, `--claude-stream <path>` or
`--claude-statusline-json <path>` to read Claude from a file you point to. Do NOT add
`--probe-claude` unless the user explicitly asks for it and gives a budget in USD
(`--probe-budget-usd <amount>`): it is the only option that makes a paid call.

Report the output as is. Never invent a value for a counter shown as `unknown`, and never
read it as zero. Do not print or ask for a token, login or account identifier.
