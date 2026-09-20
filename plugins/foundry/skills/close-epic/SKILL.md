---
name: close-epic
description: >
  Closes a non-code Epic without a PR only through an atomic tracker capability that
  verifies its own AC and the exact terminal child graph. USE WHEN the user invokes
  /foundry:close-epic (Claude Code), $foundry:close-epic (Codex), or asks to close a
  completed non-code Epic.
argument-hint: "<EPIC-ID>"
allowed-tools: Bash(python3:*)
---

# close-epic — provider-audited non-code closure

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. In Codex, derive
`<foundry-root>` from this skill's absolute path by removing
`/skills/close-epic/SKILL.md`. Never pass the placeholder literally.

Run the dedicated mechanical command once:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue close-epic <EPIC-ID>
```

This path performs no Git or code-host operation and never reuses the code-issue
`done` transition. It requires complete Epic AC (or no Epic AC), at least one linked
required child, exact positive parent/child versions, and terminal `done`/`dropped`
children. The tracker must then atomically compare the complete locked graph, advance
the parent, and persist a replayable audit receipt. A concurrent add, reopen, version
change, foreign-project child, missing audit, or unsupported provider stops before a
mutation is claimed successful.

The current Dev Hub and YouTrack adapters deliberately refuse this command:
neither exposes the required atomic graph-and-audit endpoint. Do not emulate it with
queries followed by a normal state write. Once a provider implements the port, rerunning
the exact command after an ambiguous response recovers its durable receipt instead of
issuing a second state transition.
