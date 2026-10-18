---
name: check
description: >
  Warns when a quota counter (Codex, Claude, Linear, GitHub Actions) reaches its configured
  threshold; an unreadable counter is reported as inconnu, never as below threshold. Never
  blocks. USE WHEN the user asks to check quota thresholds, or before starting a campaign,
  or invokes /gauge:check (Claude Code) or $gauge:check (Codex).
argument-hint: "[--config PATH]"
allowed-tools: Bash(python3:*)
---

# gauge:check

Read-only, always exit 0. Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before
this skill reaches the agent. Codex exposes no plugin-root variable: derive `<gauge-root>`
from this skill's absolute path by removing `/skills/check/SKILL.md`.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<gauge-root>")/tooling/gauge_cli.py" check
```

Report the output as is. A line saying `inconnu` means the value could not be read: say so,
never treat it as below the threshold. Thresholds and the configuration file location are
in the plugin README. Do not print or ask for a token, login or account identifier.
