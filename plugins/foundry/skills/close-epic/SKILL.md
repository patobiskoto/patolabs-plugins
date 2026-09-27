---
name: close-epic
description: >
  Closes a non-code Epic without a PR only through a qualified audited tracker capability that
  verifies its own AC and the exact terminal child graph. USE WHEN the user invokes
  /foundry:close-epic (Claude Code), $foundry:close-epic (Codex), or asks to close a
  completed non-code Epic.
argument-hint: "<EPIC-ID>"
allowed-tools: Bash(python3:*)
---

# close-epic — provider-audited non-code closure

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. In Codex, derive
`<foundry-root>` from this skill's absolute path by removing
`/skills/close-epic/SKILL.md`. Never pass the placeholder literally.

Run the dedicated mechanical command once:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue close-epic <EPIC-ID> --human-verdict=accepted
```

This path performs no Git or code-host operation and never reuses the code-issue
`done` transition. It requires an explicit human `accepted` verdict, complete Epic AC,
at least one linked required child, and qualified positive AC evidence for every child
and transitive dependency. Zero criteria, an unknown proof, an override, or a dropped
node never count as acceptance. The receipt binds the original parent predecessor, the
exact direct-child set, every dependency edge, each node version/state/AC snapshot and
the provider's acceptance coordinates. DevHub retains its atomic transaction. YouTrack
and Linear use PAT-ADR-0006's weaker fresh-read, one-parent-write, deterministic
append-only audit and readback sequence. A concurrent external write in the S1→S2 window
can be overwritten and escape detection; this path is neither CAS nor a transaction. A
concurrent add, reopen, version change, foreign-project node, missing audit, unknown
verdict, or unsupported provider stops without a success claim.

GitHub Projects remains unqualified and refuses honestly. Re-running after an ambiguous
response reads the exact durable audit and never adds a second parent transition.
