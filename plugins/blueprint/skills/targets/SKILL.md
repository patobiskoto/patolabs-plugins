---
name: targets
description: >
  Lists or extends the registry of targets the maintainer authorised for analysis.
  USE WHEN the user asks which targets are authorised, or invokes /blueprint:targets (Claude Code)
  or $blueprint:targets (Codex).
argument-hint: "list | add <target> --reason TEXT"
allowed-tools: Bash(python3:*)
---

# blueprint:targets

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<blueprint-root>` from this skill's
absolute path by removing `/skills/targets/SKILL.md`.

List (read-only):

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" targets list
```

`add` writes the registry. Run it only when the maintainer has just named the target and the
reason in this conversation; never infer a target, never add one to make an inventory pass.
Then repeat what was written (date, target, reason).

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" targets add <target> --reason "<reason>"
```
