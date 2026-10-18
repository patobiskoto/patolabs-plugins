---
name: write
description: >
  Writes the verified, secret-free resume state of the current session (summary,
  decisions, blockers, pitfalls, file pointers, next step) to a local owner-only file
  outside git tracking. USE WHEN the user asks to save, hand over or prepare the resume
  state of a session, ends a session, or invokes /relay:write (Claude Code) or
  $relay:write (Codex).
argument-hint: "[draft JSON path]"
allowed-tools: Bash(python3:*)
---

# relay:write

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable: derive `<relay-root>` from this skill's
absolute path by removing `/skills/write/SKILL.md`.

1. Draft the state as a JSON object in a scratch file, with exactly these keys (format
   and rules: `plugins/relay/README.md`): `summary`, `next_step` (required strings);
   `decisions`, `blockers`, `pitfalls`, `files`, `issues`, `prs` (lists of strings);
   `branch`, `commit` (optional, the values you believe are current).
2. Never put a token, password or key in the draft. A draft that looks like it holds one is
   refused and nothing is written; remove the value and retry.
3. Run:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<relay-root>")/tooling/relay_cli.py" write --draft <draft.json>
```

Foundry is found through `RELAY_FOUNDRY_CLI` (path of `foundry_cli.py`) when set, else next
to this plugin (order: `plugins/relay/README.md`, "Verification").

Report the output as is, including every `divergence` and `unknown` line: do not describe
an `unknown` as verified. If the tool refuses the draft, fix the draft; never bypass the
tool by writing the file yourself.
