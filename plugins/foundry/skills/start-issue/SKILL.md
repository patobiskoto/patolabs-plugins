---
name: start-issue
description: >
  Starts an issue: sets its state to in-progress and creates the branch (feat/fix/chore
  by Type), then hands off to the inner implementation loop. USE WHEN the user invokes
  /foundry:start-issue (Claude Code), $foundry:start-issue (Codex), "start ORFEO-42",
  or "on attaque FOUNDRY-42".
argument-hint: "<ISSUE-ID>"
allowed-tools: Bash(python3:*), Bash(git:*)
---

# start-issue

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present, otherwise the Codex fallback. For Codex,
derive
`<foundry-root>` from this skill's absolute path by removing `/skills/start-issue/SKILL.md`. Never pass
the placeholder literally.

## 1. Transition + branch (mechanical)
Precondition: a clean working tree — `start` refuses if tracked files have uncommitted
changes (commit or stash first, and tell the user why).
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue start <ISSUE-ID>
```
In a normal clone this checks out and pulls the default branch before creating
`<type>/<id>-<slug>`. In a linked worktree (including Codex worktrees), it creates the
branch from `origin/<default>` without attempting to check out the default branch in
two worktrees. Before mutating Git, it asks the tracker adapter for the bounded start
transition path. Dev Hub therefore uses `backlog → ready → in-progress`; YouTrack keeps
its direct transition. If a tracker mutation fails after branch creation, Foundry keeps
the branch and prints the exact retry instruction. Rerunning from that branch reads the
fresh tracker state and applies only the remaining transitions.

## 2. Load context, then implement once
Read the issue's description + acceptance criteria to scope the work:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query issue <ISSUE-ID>
```
The `adrs` in that payload are an index of constraints — load the full text of the
ones the issue cites (`query adr <ADR-ID>`), honor them, don't reopen them. If `adrs`
is instead a `{"status": "conflict", ...}` object, the embedded ADR index itself is in
conflict: treat ADR constraints as unknown, not as none, and do not proceed as if the
index were empty.

Before invoking either host, inspect the issue's explicit risk/ADR metadata. If it
contains a listed signal (`security`, `data_migration`, `concurrency`, `public_api`,
`release`, `adr_creation`, or `adr_reconsideration`), record it with
`routing escalation risk` and use the returned floor for the first spawn. Never infer a
risk silently from agent confidence.

Under Claude Code, invoke the logical `foundry:eiffel` agent exactly once and let
the routing hook resolve its model/effort. Its prompt is a bounded task packet, not the
conversation history. Put `FOUNDRY_ROUTE_REQUEST={"issue":"<ISSUE-ID>"}` on its first
line so any persistent escalation floor reaches the actual invocation, followed by these
literal sections:

```text
Goal:
Implement <ISSUE-ID> and its listed AC.
Inputs:
Issue payload, cited ADR IDs, repository paths needed to start.
Constraints:
Accepted ADRs, AGENTS.md/CLAUDE.md, preserve unrelated work, no tracker/PR mutation.
Done when:
Requested changes and proportional tests are green; report files and test results.
```

Wait for it to return; do not implement the same scope concurrently in the coordinator.
Then inspect its diff and run the final repository-level validation yourself.

Under Codex, write that same four-section packet to a scratch file and resolve the
actual invocation before spawning anything:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing codex-plan implementer --issue <ISSUE-ID> --packet-file /path/to/task-packet.txt
```

When host-supplied effective profile metadata reports `model` and/or
`model_reasoning_effort`, append `--profile-model-active` and/or
`--profile-effort-active`; pass presence only, never profile values.

For `mode=subagent`, call the host subagent tool once with every field from `spawn`
unchanged: in particular, keep the explicit `model`, `reasoning_effort`, and
`fork_turns=none`. Wait for it and do not implement concurrently. If the host has no
subagent tool, rerun with `--no-subagent`, show the returned `disclosure`, then follow
`current_context_instructions`; the skill remains usable but no longer claims context
independence or that the resolved model/effort was applied. In either host, when green
invoke `foundry:open-pr`.

Passive observation is a required post-return step, never part of `spawn` or the task
packet. A `current_context` fallback has no distinct host-return boundary and therefore
has no completion capability. On Codex `mode=subagent`, if the plan contains
`telemetry.completion_capability`, wait until the actual host call settles and classify
only its structured host state, never its prose:

- normal return: `--status completed --failure-class none`;
- explicit host failure: `--status failed --failure-class host`;
- explicit timeout: `--status failed --failure-class timeout`;
- explicit cancellation: `--status cancelled --failure-class cancelled`;
- absent or ambiguous trustworthy classification: `--status unknown --failure-class unknown`.

Then consume it exactly once through the resolved CLI path with `telemetry complete
--capability <CAPABILITY>` plus that exact pair. Both flags are mandatory; partial
output followed by an exception uses the structured exception class, or `unknown/unknown`
when it is ambiguous. Telemetry never turns that host result into success and never masks
it. Retain a returned `outcome_capability` privately. After the coordinator's closed
test/review aggregates are known, consume it once with
`telemetry outcome --capability <OUTCOME-CAPABILITY>` plus only controlled aggregate
flags.

Claude's real post-tool hook is completely silent: it privately maps `PostToolUse` to
`completed/none` and `PostToolUseFailure` to `failed/host`, emits no token, context,
instruction, or tool result, and cannot safely hand an aggregate-outcome bearer back to
the coordinator. Timeout/cancellation detail and Claude aggregate outcomes are therefore
unavailable rather than inferred. Missing capabilities, IO failure, or `observed=false` are fail-open and must not
alter retry, escalation, gates, results, tracker actions, or provider invocation.

## 3. Deterministic escalation loop

After a delegated attempt, **only** a conclusively red test run or blocking review
verdict may increment the counter:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation failure <ISSUE-ID> implementer --kind test_red --current-tier <SELECTED-TIER> --idempotency-key <STABLE-FAILURE-ID>
```

`<STABLE-FAILURE-ID>` identifies that exact observed failure; a transport or tool replay
must reuse it, while a distinct failure needs a distinct value. Use `review_blocking` for the first blocking review and
`review_blocking_after_fix` when its correction is blocked again. Rebuild the next
Claude/Codex invocation through the façade so its persistent floor is applied. A
`review_blocking_after_fix` CLI signal must also pass `--root <ROOT> --base <IMMUTABLE-BASE-SHA>`;
Foundry authenticates a terminal blocked review proof before any credited correction.
Every
decision reports role, initial/final tier, counters, and deterministic reason. If the
command exits non-zero with `human_required=true`, STOP and ask the human; never create
another agent. If it reports `technical_blocked`, keep campaign effects stopped and use
the bounded `resume-technical` diagnostic path from `resume-issue`; do not turn that
mechanical block into a human verdict. At most four Foundry subagents may be live
concurrently, and escalation never changes agent type, tools, merge/release authority,
or external-write permission.

Stop and ask only if: an architecture decision isn't settled by the ADRs, the user asked
to just prepare the branch, or there's an external blocker.
