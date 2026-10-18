---
name: spec
description: >
  Writes a product-language specification from groups and decompiled entry points, every point
  citing REA evidence or saying unknown, then turns it into Foundry Epic and ticket payloads
  after the maintainer confirms. USE WHEN the user asks for a specification of an authorised
  target, or invokes /blueprint:spec (Claude Code) or $blueprint:spec (Codex).
argument-hint: "<spec.json> <inventory.json> [decompilation.json ...]"
allowed-tools: Bash(python3:*)
---

# blueprint:spec

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<blueprint-root>` from this skill's
absolute path by removing `/skills/spec/SKILL.md`.

1. Write the spec yourself from the `groups` output and the decompilations (`blueprint:decompile`),
as JSON:

```json
{"format": 1, "title": "...", "summary": "...",
 "features": [{"name": "...", "summary": "...",
               "points": [{"text": "...", "evidence": ["ev_..."]},
                          {"text": "...", "evidence": "unknown"}]}]}
```

Write in product language: what the user sees and does, never a symbol name, mangled name or
address. A point states only what a cited evidence id shows; what you could not establish gets
`"evidence": "unknown"`. Do not infer from names alone and do not fill a gap with a guess. Take
the ids from the files you read (`rea_evidence_id` / `evidence_id`).

A spec detailed enough to rebuild the app uses format 2 with the sections `coverage`, `constants`, `data_model`,
`resources`, `screens`: see `blueprint:rebuild` and the README of the plugin ("spec format 2").

2. Check it. The check refuses a point whose id is absent from the evidence files you pass, a point
with neither evidence nor `unknown`, and a text that leaks a mangled symbol or a raw address
(heuristic only: it does not prove the text is true, which you and the reviewer judge):

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" spec check <spec.json> <inventory.json> [<decompilation.json> ...]
```

3. Produce the payloads. This writes files and prints the Foundry commands; it never calls the
tracker:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" spec to-foundry <spec.json> <inventory.json> [<decompilation.json> ...] [--output DIR]
```

4. Stop and show the maintainer the payloads and the commands. This is the `foundry:intake` rule
("3. Apply (human-confirmed)"): run nothing against the tracker without their explicit
confirmation, and the commands are run by the coordinating Foundry skill, not by this one. After
the Epic is created, run `to-foundry` again with `--epic-id <ID>` to produce the ticket payloads
(they need the Epic identifier), and ask for confirmation again before the tickets. A feature with
no evidenced point is refused: there is nothing testable to put in an acceptance criterion.
