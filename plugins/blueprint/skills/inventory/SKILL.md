---
name: inventory
description: >
  Produces a reproducible inventory (modules, types, UI strings, resources) of an authorised
  target with pinned REA evidence. USE WHEN the user asks to inventory an authorised target, or
  invokes /blueprint:inventory (Claude Code) or $blueprint:inventory (Codex).
argument-hint: "<target> [--output FILE]"
allowed-tools: Bash(python3:*)
---

# blueprint:inventory

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<blueprint-root>` from this skill's
absolute path by removing `/skills/inventory/SKILL.md`.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" inventory <target>
```

The command refuses (exit 3) a target absent from the registry: report the refusal and stop;
do not add the target yourself. It exits 4 when the tooling is not prepared (run
`blueprint:setup`). Read the written JSON: a section whose `status` is `unknown` says why;
report it as unknown, never as empty. REA evidence stays on the machine except what you send
to the model of this session under the maintainer's account.
