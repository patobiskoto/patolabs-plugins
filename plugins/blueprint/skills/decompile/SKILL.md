---
name: decompile
description: >
  Groups an inventory into candidate screens and features, then decompiles the entry points of
  one group of an authorised target with REA evidence. USE WHEN the user asks to group an
  inventory or decompile a screen or feature, or invokes /blueprint:decompile (Claude Code) or
  $blueprint:decompile (Codex).
argument-hint: "<target> --inventory FILE (--group NAME | --entry ADDR) [--list] [--max N]"
allowed-tools: Bash(python3:*)
---

# blueprint:decompile

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<blueprint-root>` from this skill's
absolute path by removing `/skills/decompile/SKILL.md`.

1. Group (offline, reads only the inventory written by `blueprint:inventory`):

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" groups <inventory.json> --output <groups.json>
```

Read `groups.json`. Report what is in `ungrouped` and `unknown_parts` as such; never present a
type or string that is not in a group as belonging to one.

2. List the entry-point candidates of one group (one REA search, about a minute), then decompile a
chosen few. Each REA call re-analyses the binary (about 52 s each in the 2026-10-18 trial), so keep
`--max` small:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" decompile <target> --inventory <inventory.json> --group <NAME> --list
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" decompile <target> --inventory <inventory.json> --entry <0xADDRESS>
```

The command refuses (exit 3) a target absent from the registry: report the refusal and stop; do
not add the target yourself. Exit 4: run `blueprint:setup`. Exit 5: the target is not the one the
inventory describes, or REA failed. A `status: unknown` entry or an `unknown_parts` key says why
something was not read: report it as unknown, never as empty. The pseudo-code is Ghidra output with
mangled Swift names, not source. REA evidence stays on the machine except what you send to the
model of this session under the maintainer's account.
