---
name: rebuild
description: >
  Runs one Ghidra analysis of an authorised target, links screens to Witness captures, and builds the
  rebuild-grade analysis (measured coverage, constants, persistent data model, resources, screens), then helps write
  the format 2 spec. USE WHEN the user asks for a spec detailed enough to rebuild an app, a coverage report, constants,
  a data model or a resource inventory, or invokes /blueprint:rebuild (Claude Code) or $blueprint:rebuild (Codex).
argument-hint: "<target> --inventory FILE"
allowed-tools: Bash(python3:*)
---

# blueprint:rebuild

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<blueprint-root>` from this skill's
absolute path by removing `/skills/rebuild/SKILL.md`.

Prerequisite: `blueprint:inventory` has produced `<inventory.json>` for the target.

1. Analyse once. The command refuses (exit 3) a target absent from the registry: report the refusal and
stop; do not add the target yourself. Exit 4: run `blueprint:setup`. It imports and analyses the binary in Ghidra
only if that has not been done for these exact bytes, then exports every app function:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" analyze <target> --inventory <inventory.json> --output <analysis.json>
```

Report the times it prints (analysis, export, total) as measured. Every later `decompile` of this target uses
`--analysis <analysis.json>` and starts no process.

2. Screens. Blueprint never runs the simulator. If the app can be launched, the maintainer (or the coordinator)
captures each screen with `witness_cli.py prove --navigation FILE.json` (skill `witness:prove`), then you write a
manifest `{"format": 1, "screens": [{"name": "...", "report": "path/to/report-....json"}]}` and validate it:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" screens <manifest.json> --output <screens.json>
```

If the target cannot be launched, skip this step: the screens are then reported without capture (`unknown`).

3. Rebuild (offline):

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<blueprint-root>")/tooling/blueprint_cli.py" rebuild <analysis.json> --inventory <inventory.json> [--screens <manifest.json>] --output <rebuild.json>
```

Read `rebuild.json`. State the coverage numbers as printed and list `coverage.not_covered`; never present an
`unknown` constant, property type, resource usage or navigation as known.

4. Write the spec in format 2 (see `blueprint:spec` and the README of the plugin, "spec format 2"): the features as
before plus `coverage`, `constants`, `data_model`, `resources`, `screens`. Copy the coverage numbers from
`rebuild.json`, and the evidence ids (`gh_`, `res_`, `wit_`, `ev_`) from the files you read. Then
`spec check <spec.json> <rebuild.json> <inventory.json> [<screens.json>] [<decompilation.json> ...]`.

REA and Ghidra evidence stays on the machine except what you send to the model of this session under the
maintainer's account.
