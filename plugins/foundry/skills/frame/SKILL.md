---
name: frame
description: >
  Guided deep brainstorm that FRAMES a raw idea, then materializes it in the tracker as
  ADRs (the frame) + an epic + precise, unambiguous issues with testable acceptance
  criteria, each citing the ADRs that constrain it. Loads existing ADRs first so settled
  decisions are never reopened. USE WHEN starting a new product / feature / epic from a
  rough idea, or when the user invokes /foundry:frame (Claude Code), $foundry:frame
  (Codex), "cadrons ça", or "transforme en issues".
argument-hint: "[rough idea or feature title]"
allowed-tools: Bash(python3:*)
---

# frame — genesis → historization

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/frame/SKILL.md`. Never pass the placeholder literally.

Phase 0 of the Foundry pipeline. You turn a rough idea into a **framed, historized**
backlog. The brainstorm is yours (judgment); the write is mechanical (`foundry.frame`).

## 0. Retrieval BEFORE reasoning (non-negotiable)
Load the decisions already made so you never re-litigate them — index first, then the
full text of ONLY the ADRs whose title/status touch the idea:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query adrs
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query adr <ADR-ID>
```
Treat the loaded `accepted` ADRs as **settled constraints**. If the new idea touches
one, work *within* it (or explicitly propose to supersede it — never silently reopen).

Under Claude Code, when the idea touches an existing codebase, invoke one logical
`foundry:lupin` agent for bounded read-only retrieval. Give it only `Goal:`, `Inputs:`,
`Constraints:`, and `Done when:` (question, likely paths, loaded ADRs, evidence expected),
never the conversation history. Treat its cited findings as inputs to the brainstorm,
not as a product decision. Under Codex, write that packet to a scratch file, run the
Codex façade, then pass its `spawn` object unchanged to the host subagent tool:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing codex-plan scout --packet-file /path/to/scout-packet.txt
```

Append `--profile-model-active` / `--profile-effort-active` when host-supplied effective
profile metadata reports those keys; never pass their values.

The descriptor explicitly sets the resolved model/effort and `fork_turns=none`. If no
subagent tool exists, rerun with `--no-subagent`, show the returned disclosure that
independence was lost, and follow `current_context_instructions` read-only.

For every returned scout or architect call, complete passive observation before using
its result. A Codex `current_context` fallback has no distinct host-return boundary and
no completion capability. For `mode=subagent`, classify only the structured host return:

- normal return: `--status completed --failure-class none`;
- explicit host failure: `--status failed --failure-class host`;
- explicit timeout: `--status failed --failure-class timeout`;
- explicit cancellation: `--status cancelled --failure-class cancelled`;
- absent or ambiguous trustworthy classification: `--status unknown --failure-class unknown`.

Consume an optional plan `telemetry.completion_capability`
once through the resolved CLI path using `telemetry complete --capability <CAPABILITY>`
plus that exact mandatory pair. Partial output followed by an exception uses its
structured class, or `unknown/unknown` when ambiguous; telemetry neither creates a false
success nor masks the host result. Retain a returned outcome capability privately and
consume it once when aggregate test/review state is known through `telemetry outcome
--capability <OUTCOME-CAPABILITY>` with controlled flags only.

Claude's post-tool hook remains silent and privately maps only `PostToolUse` to
`completed/none` and `PostToolUseFailure` to `failed/host`. It exposes neither a bearer
nor instructions; timeout/cancellation detail and aggregate outcomes are unavailable at
that host boundary rather than inferred. Tokens never enter task packets. Absence, IO failure, replay, or `observed=false` changes no brainstorm, spawn,
retry, escalation, gate, or result.

If Superpowers is installed, you may run its `brainstorming` skill for the divergent
phase; otherwise run the guided brainstorm below. Either way, you finish here.

## 1. Guided brainstorm (deep, one question at a time)
Do NOT dump a plan. Interrogate the idea until it is unambiguous:
- One question at a time. Explore alternatives explicitly; name trade-offs.
- Pin down: the value it proves, the smallest slice that demonstrates it, the boundaries
  (what's explicitly OUT), the durable decisions (→ ADRs), the risks.
- Stop when a competent stranger could build it without asking you anything.

`foundry:vauban` is an apex escalation, not a routine brainstorm participant. Under
Claude Code invoke it only when convergence has exposed a durable decision that needs a
new ADR or an explicit supersession. Its bounded packet uses the same four literal
sections and includes the accepted ADR text plus the precise decision/trade-offs to
resolve. It remains read-only: the coordinator drafts the ADR and the human still
approves the spec. Do not invoke it merely to estimate, split, or word ordinary issues.
Under Codex, resolve that same packet with `routing codex-plan architect --packet-file
/path/to/architect-packet.txt`, then execute `spawn` unchanged so apex model/effort and
fresh context reach the actual call. Use the same explicit `--no-subagent` disclosure
path and value-free profile signals when delegation is unavailable.
An ADR creation or reconsideration is an explicit `adr_creation` /
`adr_reconsideration` apex risk when the brainstorm belongs to an existing issue. Record
it before delegation; do not infer other risk signals from confidence. Never have more
than four Foundry subagents live concurrently.

## 2. Converge to a spec (show it, get a yes)
Draft the spec and present it for approval before writing anything:
- **ADRs**: the structural, durable decisions (framework, data model, boundary…). One
  decision each, in the Contexte/Décision/Conséquences/Alternatives form.
- **Epic**: the umbrella. **Issues**: each with a crisp title and a `## Critères
  d'acceptation` section of testable `- [ ]` items — the AC *is* the contract against
  ambiguity. Set Priority, Estimate (Fibonacci 1-21), Milestone, Type. Link each to the
  ADRs that constrain it via `constrained_by`.

Wait for explicit human approval of the spec.

## 3. Materialize (mechanical)
Write the approved spec JSON to a scratch file (Write tool), then feed it to the bridge
— the command must START with python3 to match the allowed-tools rule, so use a `<`
redirect, never an `echo … |` pipe:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" frame < /path/to/spec.json
```
Spec shape: `{ "adrs":[{title,body,status}], "epic":{title,body,fields},
"issues":[{title,body,fields:{Priority,Estimate,Milestone,Type,State},constrained_by:[adr title|index]}] }`.
New issues default to `State: backlog` — sequencing them is the `foundry:roadmap`
skill's job.

## 4. Hand off
Report what was created and suggest `foundry:roadmap` to order the slice by value.

## Rules
- Never write to the tracker before the human approves the spec.
- Every issue ships with testable AC. No AC → not ready to materialize.
- ADRs are created `proposed`; they become `accepted` at merge of the work they framed.
