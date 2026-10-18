---
name: setup
description: >
  Prepares the pinned REA, JDK and Ghidra in the blueprint data directory, never globally.
  USE WHEN the user asks to prepare the reverse-engineering tooling, or invokes /blueprint:setup
  (Claude Code) or $blueprint:setup (Codex).
argument-hint: "[--plan|--yes]"
allowed-tools: Bash(python3:*)
---

# blueprint:setup

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<blueprint-root>` from this skill's
absolute path by removing `/skills/setup/SKILL.md`.

1. Run the plan (it downloads and writes nothing) and show it to the maintainer in full:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" setup --plan
```

2. Do nothing more until the maintainer explicitly agrees to these downloads and writes.
   Only then run the same command with `--yes`. A checksum mismatch stops the step and
   deletes the file; never work around it. Never run `rea setup` or install anything globally.
