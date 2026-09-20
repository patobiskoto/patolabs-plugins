---
name: blockers
description: >
  Surfaces what's stuck and why: blocked issues, stale in-progress work with an open PR,
  dependency cycles, and decisions still `proposed` that gate work. USE WHEN the user
  invokes /foundry:blockers (Claude Code), $foundry:blockers (Codex), "qu'est-ce qui
  coince", "blockers", or "what's stuck".
allowed-tools: Bash(python3:*)
---

# blockers — what's stuck and why

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/blockers/SKILL.md`. Never pass the placeholder literally.

## 1. Load the graph + frame
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query profile blockers
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query adrs
```

## 2. Reason
Check `sections.issues`, `limit_exceeded`, and `truncated` first. Follow every top-level
`pagination.next_page` with `query profile blockers <page>` and union the issue rows before
asserting coverage; an oversized connected component is complete but explicitly exceeds
the nominal limit.
Require one matching `pagination.snapshot_identity` across pages; if it changes, restart
from page 1 and do not combine the old and new graph.
- **Blocked**: issues with non-empty `blocked_by` — name the blocker and its state.
- **Stale WIP**: `in-progress` with a PR (`pr_url`) whose `updated` timestamp has not moved.
- **Dependency cycles**: follow `links`; report any cycle.
- **Gating decisions**: an ADR still `proposed` that work depends on — the index gives
  id/status; load the text (`query adr <ID>`) only if the gating needs explaining.

## 3. Report actionably
For each blocker: the chain, the root cause, and the single next action that clears it.
Order by how much each unblocks.
