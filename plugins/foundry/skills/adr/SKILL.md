---
name: adr
description: >
  Records an Architecture Decision Record in the tracker's knowledge base (ticker derived
  from the current project), or accepts an existing one. USE WHEN a durable architectural
  decision is made, or the user invokes /foundry:adr (Claude Code), $foundry:adr
  (Codex), "note une ADR", or "on décide que".
argument-hint: "<title> | accept <ADR-ID> | supersede <ADR-ID> <REPLACEMENT-ID> | link-issue <ADR-ID> <ISSUE-ID>"
allowed-tools: Bash(python3:*)
---

# adr

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/adr/SKILL.md`. Never pass the placeholder literally.

An ADR is for a **durable, structural** decision (framework, data model, migration,
cross-cutting constraint, costly-to-reverse trade-off) — not a local, reversible choice.

## Create
Write the body (Contexte / Décision / Conséquences / Alternatives écartées) to a scratch
file (Write tool), then feed it in — start the command with python3 (allowed-tools
prefix match), never with an `echo … |` pipe:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr create "<title>" < /path/to/body.md
```
Created as `proposed`. It becomes `accepted` when the work it frames merges (via the
`foundry:merge-pr` skill) or explicitly:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr accept <ADR-ID>
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr edit <ADR-ID> /path/to/body-read.md /path/to/amended-body.md
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr supersede <ADR-ID> <REPLACEMENT-ID>
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr link-issue <ADR-ID> <ISSUE-ID>
```

On Linear, each command above uses project-scoped version Documents plus a separate
deterministic witness Document. Supersession writes and verifies both reciprocal ADR
links. A missing pair, one-sided link, out-of-project issue relation, or provider outage
fails closed; the skill never falls back to YouTrack or Git. Historical import is a
separate operator/cutover capability and is not performed by this skill.

## Rule
Don't contradict an `accepted` ADR — supersede it with a new one that references it.
Before any architecture choice, scan the index (`query adrs`) then load the full text
of the ADRs touching the topic (`query adr <ADR-ID>`).
