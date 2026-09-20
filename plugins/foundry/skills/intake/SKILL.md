---
name: intake
description: >
  Captures an idea that surfaced mid-flight WITHOUT re-validating settled ground: brief
  brainstorm, then decide — new issue, refine an existing one, new ADR, or rejected
  because an accepted ADR already settled it (cite it). USE WHEN the user has a new idea,
  invokes /foundry:intake (Claude Code), $foundry:intake (Codex), "j'ai une idée", "et
  si on ajoutait", or "note ça".
argument-hint: "[the idea]"
allowed-tools: Bash(python3:*)
---

# intake — feed the backlog without reopening decisions

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/intake/SKILL.md`. Never pass the placeholder literally.

## 1. Retrieval BEFORE reasoning (non-negotiable)
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query adrs
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query profile intake
```
The ADR payload is an index — load the full text of any ADR whose title touches the
idea (`query adr <ADR-ID>`) before ruling. If an **accepted** ADR already settles
this idea → say so, cite the ADR, and stop
(unless the user explicitly wants to supersede it via a new ADR). This is the mechanism
that stops re-litigating validated points.
Read the profile selection, truncation, and per-section counts. An omitted candidate is
not evidence of absence: follow the top-level `pagination.next_page` with `query profile
intake <page>`, union both `issues` and `historical_index` slices, and exhaust them before
ruling out a duplicate or related work. Then load bodies explicitly as required.
Require the same `pagination.snapshot_identity` on every page; restart at page 1 on a
change instead of combining stale and fresh results.

## 2. Brief brainstorm, then classify
Short, focused. Then pick exactly one:
- **New issue** — crisp title + testable AC + fields.
- **Refine existing** — load it first (`query issue <ID>`: the backlog list has no
  text), then sharpen its AC / scope / estimate. Rule out duplicates by title from
  the list, confirmed against the loaded AC.
- **New ADR** — the idea is a durable decision (or supersedes one).
- **Reject** — an accepted ADR closes it; cite it.

## 3. Apply (human-confirmed)
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit create-issue '<json>'
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit set-field <ID> "<Field>" "<value>"
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit body <ID> /path/to/body-read.md /path/to/amended-body.md
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr create "<title>" < /path/to/body.md
```
Confirm before writing. Prefer refining a duplicate over creating a near-copy. For the
ADR body, Write it to a scratch file and `<`-redirect it (commands must start with
python3 to match allowed-tools — no `echo … |` pipes).
