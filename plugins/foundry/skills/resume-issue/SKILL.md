---
name: resume-issue
description: >
  Resumes work on an issue mid-flight without a human re-briefing: reloads the issue
  (AC state, ADR constraints, progress notes), the git state (branch, log, diff), and
  the PR if one exists, then continues from where the last session stopped. USE WHEN
  the user invokes /foundry:resume-issue (Claude Code), $foundry:resume-issue (Codex),
  "reprends FOUNDRY-42", "où on en était", or a session restarts mid-issue.
argument-hint: "<ISSUE-ID>"
allowed-tools: Bash(python3:*), Bash(git:*), Bash(gh:*)
---

# resume-issue — reprendre sans re-briefing

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present, otherwise the Codex fallback. For Codex,
derive
`<foundry-root>` from this skill's absolute path by removing `/skills/resume-issue/SKILL.md`. Never pass
the placeholder literally.

## 1. Reload the tracker context
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query issue <ISSUE-ID>
```
The payload carries the AC (`- [ ]` vs `- [x]`), the linked issues (lean — bodies via
`query issue <ID>` if needed), the ADR index (constraints — `query adr <ADR-ID>` for
the text of the cited ones, honor them), the PR URL if one was opened, and the last
progress notes (`comments`) — the memory of what the previous session did and decided.

## 2. Reload the working state
```bash
git rev-parse --abbrev-ref HEAD
git log --oneline -10
git status --short && git diff --stat
```
Cross-check: does the branch match the issue (`<type>/<id>-<slug>`)? Are there commits
beyond origin? Uncommitted work? If the issue has a `pr_url`, check the PR's state and
CI before touching anything.

## 3. Inspect the human stop before any delegation

Before reconstructing or continuing, inspect the shared escalation state:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation show <ISSUE-ID>
```

If `human_required=true`, stop and require an explicit human authorization and one of the
public audit codes `remediation_reviewed`, `risk_accepted`, or `manual_retry_approved`.
This path is valid only for a typed strategy/product decision or an attested durable
ambiguity. Do not infer it from `halted=true`, a retry limit, review prose, or a failed
test. Take the exact `halt_generation` returned by `show`; it binds this authorization
to the observed human stop. Only the controlled code is stored—never include a secret,
prompt, environment value, provider output, or other sensitive data.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation resume <ISSUE-ID> --reason remediation_reviewed --halt-generation <HALT-GENERATION-FROM-SHOW> --remediation-credits <1..3>
```

The optional `--remediation-credits` is an explicit human-authorized window of 1..3
post-fix blocking reviews. It is bound by CAS to this issue, exact generation, and the
one correction role recorded in the machine-readable ledger; if that role is absent or
ambiguous, the command fails closed. Continue automatically only when the later
`routing escalation failure` JSON says `action=remediation_continued` and its
`remediation_authorization.state` remains valid. Never infer permission from a reviewer
paragraph. `resume` never renews escalation budget, lowers a role floor, or adds tools,
network, tracker, or merge authority.

To cancel an active window, use only the original authorization generation from `show`:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation cancel-remediation <ISSUE-ID> --halt-generation <HALT-GENERATION-FROM-SHOW>
```

If `technical_blocked=true`, do not use `resume` or ask for a human verdict merely
because of that mechanical stop. Keep all
campaign effects stopped, perform one bounded local diagnostic, derive its non-secret
digest, then resume only that local diagnostic turn with the exact generation:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation resume-technical <ISSUE-ID> --halt-generation <HALT-GENERATION-FROM-SHOW> --diagnostic-digest <64-HEX-DIGEST>
```

This is one recorded diagnostic for the exact stop generation, creates no provider or
campaign grant, and its replay is idempotent. It does not reopen ordinary delegation.
Build a correction route only after atomically claiming the exact stopped role and
generation once:
`routing escalation claim-technical-route <ISSUE-ID> <RECORDED-ROLE>
--halt-generation <HALT-GENERATION-FROM-SHOW> --route-id <STABLE-LOCAL-ROUTE-ID>`.
The replay reuses that exact ID; another role or ID is refused. Then Codex uses
`routing codex-plan ... --issue <ISSUE-ID> --technical-remediation
--technical-remediation-id <STABLE-LOCAL-ROUTE-ID>`; Claude sets
`FOUNDRY_ROUTE_REQUEST={"issue":"<ISSUE-ID>","technical_remediation":true,
"technical_remediation_id":"<STABLE-LOCAL-ROUTE-ID>"}` as the first prompt line.
Codex returns a local plan with no spawn; the Claude Agent hook refuses this envelope,
because the remediation receipt is never an authority to call a provider. A reviewer
route can only unlock its separately claimed fresh review; a provider invocation,
including review, must independently acquire fresh capacity and revalidate its campaign
authority, budget, snapshot, and gates. Do not rebuild an ordinary route from this
response. For a released v1 stop
without a terminal classification, first run
`reclassify-legacy-terminal` with the exact generation. It accepts either an exact
failure receipt, or a receipt-free v1 ledger frozen into a causal snapshot digest; mixed
or incomplete evidence returns `authority_ambiguous` and remains fail-closed. Request a
human verdict only if the facts actually establish durable ambiguity or a strategy/product
decision.

If a human independently makes an explicit `strategy_decision` after inspecting the
technical evidence, record that verdict against the same stopped issue and generation;
only then may the human authorize one bounded retry window. For PAT-22 at generation 4,
with its three preserved technical-diagnostic audit records, the recovery is:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation verdict PAT-22 implementer --current-tier apex --category strategy_decision
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation resume PAT-22 --reason manual_retry_approved --halt-generation 4 --remediation-credits 1
```

This is a human strategy authorization, not a fourth technical route: it preserves the
technical audit and apex floor, remains bound to PAT-22/generation 4, and creates no
provider, campaign, PR, CI, or merge effect. A stale generation, different issue, replay,
or exhausted one-credit window remains fail-closed.

If that one credit is consumed and a later review creates a fresh technical stop,
use the exact new generation with `resume-technical` only for its one local diagnostic.
The exhausted human window remains exhausted; the validator requires a contiguous
technical audit suffix and never turns the local receipt into a provider, campaign,
PR, CI, or merge permission. A separate ordinary gate is still required for shipping.

Cancellation re-halts the issue technically. Exhaustion, another role, another signal,
stale state, or an incompatible generation stay fail-closed and do not by themselves
create a human verdict.

If `show` reports `halted=false` and
`remediation_authorization.state=exhausted`, ordinary `resume` is deliberately invalid:
the issue is not halted. After a human explicitly authorizes a new bounded window, rearm
only that exact exhausted authorization with its recorded role and original generation:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing escalation rearm-remediation <ISSUE-ID> <RECORDED-ROLE> --reason manual_retry_approved --halt-generation <AUTHORIZATION-GENERATION-FROM-SHOW> --remediation-credits <1..3>
```

Success is `action=remediation_rearmed`, never `resumed`. The CAS transition accepts only
the just-exhausted, non-halted window at that exact issue, role, and generation. Active,
cancelled, invalidated, malformed, stale, differently attributed, or already re-halted
state is refused atomically. `remediation_rearm_audit` records only the controlled reason,
UTC date, role, generation, granted credits, and the prior exhausted window link. The
issue-level consumption audit remains append-only. Rearming does not reset counters,
renew escalation limits, lower a floor, or expand any capability or authority.

## 4. Reconstruct, announce, continue
State in 2-3 sentences: what's done (AC checked, commits), what's in flight (uncommitted
diff, unchecked AC), and the single next action. Then CONTINUE the work — that's the
point of this skill. Ask only if the reconstruction contradicts itself (e.g. branch
missing, notes say done but AC unchecked).

If implementation remains, Claude Code invokes `foundry:eiffel` exactly once with
a bounded packet containing the literal `Goal:`, `Inputs:`, `Constraints:`, and
`Done when:` sections, preceded by
`FOUNDRY_ROUTE_REQUEST={"issue":"<ISSUE-ID>"}`. Include only the remaining AC, relevant diff/paths, cited ADRs,
and the next validation — never the full conversation. Wait for it and do not duplicate
its implementation in the coordinator; inspect the returned diff and perform final
repository-level validation afterwards. Under Codex, write the same packet to a scratch
file and run:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing codex-plan implementer --issue <ISSUE-ID> --packet-file /path/to/task-packet.txt
```

Append the value-free `--profile-model-active` / `--profile-effort-active` signals when
those keys are present in host-supplied effective profile metadata.

For `mode=subagent`, pass the returned `spawn` object unchanged to the host subagent
tool and wait. Never omit its explicit `model`, `reasoning_effort`, or
`fork_turns=none`. If no subagent tool exists, rerun with `--no-subagent`, display the
returned loss-of-independence `disclosure`, and follow `current_context_instructions`.

Passive observation is required only after the invocation returns. A Codex
`current_context` fallback has no distinct host-return boundary and no completion
capability. For `mode=subagent`, classify the actual structured host return, never prose:

- normal return: `--status completed --failure-class none`;
- explicit host failure: `--status failed --failure-class host`;
- explicit timeout: `--status failed --failure-class timeout`;
- explicit cancellation: `--status cancelled --failure-class cancelled`;
- absent or ambiguous trustworthy classification: `--status unknown --failure-class unknown`.

Consume an optional plan `telemetry.completion_capability`
exactly once through the resolved CLI path with `telemetry complete --capability
<CAPABILITY>` plus that exact mandatory pair. Partial output followed by an exception
uses its structured class, or `unknown/unknown` when ambiguous; telemetry never converts
the host result to success or masks it. Retain a returned `outcome_capability` privately.
Once aggregate test/review state is known, consume it once through `telemetry outcome
--capability <OUTCOME-CAPABILITY>` with controlled flags only.

Claude's post-tool hook emits no output or context. Privately it records `PostToolUse` as
`completed/none` and `PostToolUseFailure` as `failed/host`; the host boundary exposes no
trustworthy timeout/cancellation detail and the silent hook cannot return an aggregate
outcome bearer, so those Claude values remain unavailable. Tokens never enter task packets;
absence, IO failure, replay, or `observed=false` cannot change retry, escalation, gates,
or results.

Preserve any role floor already recorded. Only `test_red`, `review_blocking`, or
`review_blocking_after_fix` may be added with `routing escalation failure` and a stable
idempotency key for that exact observed failure; agent doubt,
timeouts, prose judgments, or an incomplete task do not count. A
`human_required=true` decision halts only for the three valid human categories:
strategy decision, product decision, or attested durable ambiguity. A
`technical_blocked` decision follows the bounded local-diagnostic path above and never
grants a campaign effect. Never exceed the returned parallel limit or broaden a role's
tools/external authority when its model tier rises.

## 5. Leave a trail for the next session
At every natural pause (blocking question, end of session, before open-pr), write the
progress note to a scratch file and post it:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit comment <ISSUE-ID> < /path/to/note.md
```
A good note: what was done, what was decided (and why), the exact next step.
