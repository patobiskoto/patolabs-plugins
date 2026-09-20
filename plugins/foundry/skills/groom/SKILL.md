---
name: groom
description: >
  Reasons about backlog HEALTH, not just missing fields: is the estimate plausible (vs
  similar closed issues), is there scope creep, are there duplicates, is an issue too big
  to split, are AC testable, is WIP stale. Proposes concrete fixes. USE WHEN the user
  invokes /foundry:groom (Claude Code), $foundry:groom (Codex), "grooming", "nettoie
  le backlog", or "audit backlog".
allowed-tools: Bash(python3:*)
---

# groom — judge the backlog, don't just flag blanks

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/groom/SKILL.md`. Never pass the placeholder literally.

## 1. Load the backlog, shortlist, then drill down
The backlog list is LEAN (fields, links, `ac_done/ac_total` counts — no text). Use it
to shortlist suspects, then load the full text of each suspect BEFORE judging it:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query profile groom
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query issue <ID>
```
Never rule on AC quality, scope or duplication from a title alone.
The profile publishes snapshot, selection, truncation and per-section counts: report those
limits before claiming coverage. Follow the top-level `pagination.next_page` with `query
profile groom <page>` and union both `issues` and `historical_index` slices before
shortlisting, then drill into relevant IDs explicitly.
Use the paginated compact terminal estimate/status index to identify comparable work;
load `query issue <ID>` before judging scope or AC quality from that history.
Only combine pages with the same `pagination.snapshot_identity`; a changed identity means
the state moved, so restart page 1 before making a quality judgment.

## 2. Reason about quality (beyond presence of fields)
From the list: missing fields, estimate sanity (vs closed issues of similar scope),
`ac_total` of 0, and stale WIP using `updated`. From the drilled-down text:
- **AC testable?** empty, vague or unverifiable `- [ ]` items.
- **Too big**: high estimate + broad title + many AC → propose a split into subtasks.
- **Scope creep**: AC that drifted beyond the title's promise.
- **Duplicates / overlap**: near-identical titles from the list, CONFIRMED by
  overlapping AC in the bodies → propose merge.

## 3. Report, then fix one at a time (human-confirmed)
Present findings grouped. For each, propose a concrete action and apply on confirmation:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit set-field <ID> "Estimate" 5
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit body <ID> /path/to/body-read.md /path/to/amended-body.md  # scope / AC refinement
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit create-issue '<json>'   # split
```
One confirmation per change. Never batch-write silently.
