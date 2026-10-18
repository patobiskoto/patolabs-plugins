---
name: compare
description: >
  Compares a before and an after proof report of witness: same simulator, runtime and starting state, both
  commits and screenshots. USE WHEN the user asks to compare two iOS proofs, or invokes /witness:compare
  (Claude Code) or $witness:compare (Codex).
argument-hint: "--before REPORT.json --after REPORT.json"
allowed-tools: Bash(python3:*)
---

# witness:compare

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches the
agent. Codex exposes no plugin-root variable: derive `<witness-root>` from this skill's
absolute path by removing `/skills/compare/SKILL.md`.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<witness-root>")/tooling/witness_cli.py" compare --before <before.json> --after <after.json>
```

Exit 3 means the two proofs are not comparable (different simulator, scheme, starting state or tests
selection): show the reason, do not work around it. Present the two
screenshots and both commits; `screenshots identical` compares sha256 only and says nothing about
meaning.
