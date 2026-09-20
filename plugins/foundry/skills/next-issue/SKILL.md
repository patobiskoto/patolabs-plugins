---
name: next-issue
description: >
  Recommends the single most valuable issue to start next by REASONING over the backlog
  graph (dependencies, what's half-done, what unblocks the most, milestone focus, AC
  state) — not by a rigid priority sort. The deterministic priority rank is one signal it
  can override with a stated reason. USE WHEN the user invokes /foundry:next-issue
  (Claude Code), $foundry:next-issue (Codex), "quoi faire maintenant", "next", or asks
  what to pick up.
argument-hint: "[status, default: ready]"
allowed-tools: Bash(python3:*)
---

# next-issue — pick by reasoning, not by sort

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/next-issue/SKILL.md`. Never pass the placeholder literally.

## 1. Load the graph
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query profile next-issue
```
Load the bounded closed graph on purpose: it includes every non-terminal issue,
including `backlog` work, with linked dependency/parent/child closure. Inspect `selection`,
`sections.issues`, `limit_exceeded`, and `truncated` before reasoning; follow the top-level
`pagination.next_page` with `query profile next-issue <page>`, union every issue slice,
and exhaust it before a verdict. Then request drill-down by ID where needed. If the user
passed a status ($1), treat it as the focus, not as a data filter. All pages must have
the same `pagination.snapshot_identity`; if it changes, discard the partial set and
restart at page 1 rather than combining two tracker snapshots. Each issue carries
priority, estimate, milestone, `ac_done/ac_total`, `ac_ratio`, `links`,
`blocked_by`, `unblocked`, `unlocks_count`, and the deterministic
priority→estimate→created prior as `rank_index`.

## 2. Reason (this is the whole point)
`rank_index` is a **prior, not a verdict**. Weigh the real signals:
- **Unblocked?** Never recommend something with a non-empty `blocked_by`.
- **Half-done** (`in-progress`, high `ac_ratio`) usually beats starting cold — finish it.
- **Unlocks the most** (`unlocks_count`) advances the milestone fastest.
- **Milestone focus**: prefer the current milestone's critical path.
- Only then fall back to the priority prior.

## 3. Recommend with justification
State the pick AND why, especially when you diverge from `rank_index`:
> "rank_index=0 is FOUNDRY-42 (P1), but FOUNDRY-51 is 80% AC-complete and unblocks two
> subtasks in the current milestone — I recommend FOUNDRY-51." Then list 2-3 runners-up.

## 4. Offer to start
Propose `foundry:start-issue <ID>`. Do not start without confirmation.
