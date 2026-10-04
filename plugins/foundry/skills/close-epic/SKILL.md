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

When a refusal lists nodes delivered under an audited `acceptance-override`, a human
may accept them nominatively on Linear only (PAT-ADR-0014):

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue close-epic <EPIC-ID> --human-verdict=accepted --accept-override=<ID>[,<ID>...]
```

The list is exact (no wildcard, duplicate, empty or lower-case id) and requires
`--human-verdict=accepted`; it is validated before any provider read. Only a terminal
node whose sole insufficiency is a valid typed override receipt is waivable. Unknown or
incomplete proof, zero criteria, a non-terminal, dropped or foreign-project node, an
absent, malformed, other-generation or other-diff override receipt, and any node
added, reopened or changed since the graph read still refuse. A refusal raised by the
fresh graph read (a node's proof, a changed graph, a foreign or invalid node) lists the
non-positive nodes with their cause, read-only; a replay of an already closed Epic, a
pending-audit refusal, a provider refusal after the read, a named id outside the graph
raised on its own, and a transport error during the snapshot do not carry that list. The
list is a lower bound when a node could not be read (`read-error`: its sub-graph was not
traversed; re-run first, do not edit links) and then never suggests an attestation.
`foreign-project` means a binding refusal only. The receipt also binds each waived
node's id, override-receipt digest and reason code; replay with the same set
converges, a different set is refused, and each node stays `override` (never
accepted). The flag is neither a CAS nor an acceptance. YouTrack, GitHub Projects and
DevHub refuse it. Never invent the list: the maintainer attests it.

This path performs no Git or code-host operation and never reuses the code-issue
`done` transition. It requires an explicit human `accepted` verdict and nonempty Epic
validation criteria. For a non-code Epic, that verdict validates its own criteria;
Linear's unchecked native checkboxes are not a code-PR acceptance proof. The receipt
binds a digest of the Epic's exact need and test procedure, and every pending or done
replay refuses a changed procedure. It also requires
at least one linked required child, and qualified positive AC evidence for every child
and transitive dependency. Zero criteria, an unknown proof, an override, or a dropped
node never count as acceptance (an override is only ever waived nominatively, below). The receipt binds the original parent predecessor, the
exact direct-child set, every dependency edge, each node version/state/AC snapshot and
the provider's acceptance coordinates. DevHub retains its atomic transaction. YouTrack,
Linear and the qualified private personal-Project GitHub profile use PAT-ADR-0006's
weaker fresh-read, one-parent-write, deterministic
append-only audit and readback sequence. A concurrent external write in the S1→S2 window
can be overwritten and escape detection; this path is neither CAS nor a transaction. A
concurrent add, reopen, version change, foreign-project node, missing audit, unknown
verdict, or unsupported provider stops without a success claim. GitHub owners,
organization projects, repositories and field catalogs outside the exact qualified
binding still refuse through the identity and scope preflights. Re-running after an
ambiguous audit response reads the exact durable audit. YouTrack, Linear and GitHub
retain a machine-local intent before the append: if the effect remains invisible,
replay fails closed instead of posting the audit again, including one with a new
timestamp and nonce. The Epic-scoped local lock covers the audit, targeted parent
State write and readback, so two local replays sharing this state directory do not
send a second parent transition. This does not coordinate different machines or
exclude an external write in the S1→S2 window; it is neither provider CAS nor an
exactly-once guarantee.
