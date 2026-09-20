---
name: roadmap
description: >
  Builds a value-first roadmap: reasons over the backlog graph to find the smallest slice
  that demonstrates the product's value and the order to ship it in, respecting
  dependencies and ADRs — not a done/open count. Narrates risk, WIP, blocked work and
  trajectory. USE WHEN the user invokes /foundry:roadmap (Claude Code),
  $foundry:roadmap (Codex), "roadmap", "quelle v1", "par où démontrer la valeur", or
  wants milestone/sprint planning.
allowed-tools: Bash(python3:*)
---

# roadmap — sequence by value, don't just count

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/roadmap/SKILL.md`. Never pass the placeholder literally.

## 1. Load rollup + graph + frame
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query profile roadmap
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query adrs
```

## 2. Reason about trajectory (not %)
Follow the top-level `pagination.next_page` with `query profile roadmap <page>` until
exhausted before conclusions. Union every `issues` and `milestones` slice and verify their
per-section counts. Milestone rows carry trajectory plus aggregate WIP/blocked counts;
derive the exact active IDs from the complete issue union. For each milestone represented:
remaining estimate, WIP, blocked items, and the dependency chain. Identify **the critical
path**: the minimal ordered set of issues that,
once done, demonstrates the product's value. Respect ADR constraints (the `adrs` payload
is an index — `query adr <ID>` for the text of the ones shaping the slice) and link
ordering.
Reject mixed snapshots: each page's `pagination.snapshot_identity` must equal page 1;
otherwise restart retrieval from page 1 before reasoning.

## 3. Deliver a narrated roadmap
- The **value slice**: "these N issues, in this order, prove X." Explain why each earns
  its place and what it unblocks.
- **Risks**: what's blocked, stale WIP according to `updated`, milestones with lopsided remaining estimate, a
  decision still `proposed` gating work.
- **Recommendation**: which issues to promote `backlog → ready` for the next push.

## 4. Offer to apply
Propose promoting the chosen slice (human-confirmed), then apply each:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit transition <ID> ready
```
Never promote without confirmation.
