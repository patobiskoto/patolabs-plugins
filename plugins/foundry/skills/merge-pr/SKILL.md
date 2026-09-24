---
name: merge-pr
description: >
  Merges a PR through the exit gates — code review, GREEN CI (enforced in code, not
  prose), human test — then squash-merges via REST, sets the issue done, and accepts the
  ADRs it framed. USE WHEN the user invokes /foundry:merge-pr (Claude Code),
  $foundry:merge-pr (Codex), "merge la PR", or "on merge".
argument-hint: "<ISSUE-ID> <PR-NUMBER>"
allowed-tools: Bash(python3:*), Bash(git:*), Bash(gh:*)
---

# merge-pr — gated, in order

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/merge-pr/SKILL.md`. Never pass the placeholder literally.

### 1. Acceptance criteria
Load the issue; if AC are unchecked, the mechanical merge gate accepts them only with a
current structured AC proof. It revalidates the current issue AC digest, local `HEAD`,
exact diff, and immutable PR base SHA. For a tracker with an atomic acceptance-sync
contract, Foundry then changes only unchecked Markdown markers whose structured verdict
is `pass`, behind the provider's exact body/version boundary. It reports this
administrative synchronization separately from CI and writes an audit note containing
only the proof identifier before attempting the code-host merge. A stale proof, a body
or version conflict, an unsupported provider, a malformed/mixed/legacy proof, or any
silent truncation fails closed before merge. For a provider without this concurrency
contract (currently YouTrack), check the boxes manually and rerun. The explicit human fallback is
`--allow-incomplete-ac --ac-override-reason=<public-audit-code>`; it records an audit
note but never changes tracker checkboxes or AC text.
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query issue <ISSUE-ID>
```
The payload's `adrs` are only an index. Before review, load the full text of every cited
accepted ADR with `query adr <ADR-ID>`; a tracker-only ADR cannot be recovered by the
read-only Claude reviewer after it starts.

### 2. Code review (blank-context, two stages)
Before launching anything, build a bounded packet with the literal sections `Goal:`
(two-stage review), `Inputs:` (every AC, the full text of every cited accepted ADR, and
the PR number), `Constraints:` (blank context, read diff only through `routing
read-review`, never edit), and `Done when:` (`AC: PASS|BLOCK` and `QUALITY: OK to
merge|BLOCK`). The branch must already include the current base.

Resolve the PR through GitHub REST before claiming the review and retain `.base.sha` as
`<BASE-SHA>`. It must be exactly 40 lowercase hexadecimal characters. Use that immutable
SHA in every claim, verifier, proof, and merge-validation coordinate below; never use a
local branch name or mutable `origin/<base>` ref. Before the first claim, generate a
fresh secret `<CLAIM-ATTEMPT-TOKEN>` with `secrets.token_hex(32)` (exactly 64 lowercase
hex characters). Retain it privately until the claim command returns complete JSON;
never include it in a packet, log, or reviewer message.

Under Claude Code, atomically claim the exact `<BASE-SHA>...HEAD` diff in the shared
cross-host ledger:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing claim-review --issue <ISSUE-ID> --git-diff --root <ROOT> --base <BASE-SHA> --claim-attempt-token <CLAIM-ATTEMPT-TOKEN>
```

If that process is interrupted after publication but before returning any JSON, replay
the exact command with the same `<CLAIM-ATTEMPT-TOKEN>` and append
`--replay-interrupted`. Authenticated replays reconstruct the same capability without a
ledger write, so another interruption can be replayed again identically. Never use the
replay flag after a complete result; a normal retry without it remains a capability-free
duplicate.

When `should_run` is `true`, execute the `foundry:review-pr` contract exactly once and
pass it the returned `diff_hash`, `generation`, and `claim_id`; invoke the logical
`foundry:maigret` agent exactly once; the routing hook enforces its frontier gate and
read-only profile. Precede its packet with
`FOUNDRY_ROUTE_REQUEST={"issue":"<ISSUE-ID>"}` so an escalated reviewer floor persists.

Under Codex, write the packet to a scratch file. The Codex façade performs that same
common claim, injects its exact hash into the reviewer message, and resolves the actual
frontier-or-higher invocation:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing codex-plan reviewer --issue <ISSUE-ID> --packet-file /path/to/review-packet.txt --git-diff --root <ROOT> --base <BASE-SHA> --claim-attempt-token <CLAIM-ATTEMPT-TOKEN>
```

Append `--profile-model-active` / `--profile-effort-active` when host-supplied effective
profile metadata reports those keys; the façade warns without receiving their values.

For `mode=subagent`, pass every `spawn` field unchanged to the host subagent tool and
instruct it to load `<foundry-root>/skills/review-pr/SKILL.md`; the explicit
`fork_turns=none`, `model`, and `reasoning_effort` are gate inputs. Detect host subagent
availability before this command. If no tool exists, include `--no-subagent` in this
first and only claim/plan call, display the returned disclosure of lost blank-context
independence, and follow `current_context_instructions` strictly read-only. Never run the
command once without that flag and then retry: the first call already owns the diff hash.

Immediately after the reviewer returns, complete passive observation before acting on
its verdict. A Codex `current_context` fallback has no distinct host-return boundary and
no completion capability. For `mode=subagent`, classify only the structured host return:

- normal return: `--status completed --failure-class none`;
- explicit host failure: `--status failed --failure-class host`;
- explicit timeout: `--status failed --failure-class timeout`;
- explicit cancellation: `--status cancelled --failure-class cancelled`;
- absent or ambiguous trustworthy classification: `--status unknown --failure-class unknown`.

Consume an optional plan `telemetry.completion_capability`
once through the resolved CLI path using `telemetry complete --capability <CAPABILITY>`
plus that exact mandatory pair. Partial output followed by an exception uses its
structured class, or `unknown/unknown` when ambiguous; telemetry never converts it to
success or masks it. Retain a returned private `outcome_capability`. Once closed
review/test counters are known, consume it once using
`telemetry outcome --capability <OUTCOME-CAPABILITY>` plus controlled aggregate flags only. Tokens
never enter the reviewer packet.

Claude's post-tool hook emits no output/context/bearer. It privately records
`PostToolUse` as `completed/none` and `PostToolUseFailure` as `failed/host`; the callback
does not expose trustworthy timeout/cancellation detail, and aggregate outcomes cannot
be correlated without interfering with model context, so both remain unavailable.
Missing state, IO failure, replay, or `observed=false`
cannot affect review ownership, verdict, escalation, CI/human gates, or merge result.

On either host, if the common verdict is `should_run=false` / `mode=deduplicated`, **do
not run another reviewer**. Reuse the preserved verdict from the caller/session that owns
the claim; if no verdict is available, STOP and ask the human how to recover rather than
silently duplicating the gate.

There is no automatic expiry. With explicit human confirmation that the current owner
was abandoned, collect a non-empty, non-secret audit reason and CAS the exact generation
through the shared host-neutral CLI:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing recover-review --diff-hash <DIFF-HASH> --generation <GENERATION> --claim-id <ACTIVE-CLAIM-ID> --root <ROOT> --base <BASE-SHA> --reason "<HUMAN-REASON>"
```

Only continue with the new `claim_id` returned by a successful recovery. A concurrent
recovery of the same generation, a canonical proof left before terminalization, and
recovery of a completed review are refused.
Under Codex, rerun the reviewer plan with the returned capability:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing codex-plan reviewer --issue <ISSUE-ID> --packet-file /path/to/review-packet.txt --git-diff --root <ROOT> --base <BASE-SHA> --diff-hash <DIFF-HASH> --claim-id <NEW-CLAIM-ID>
```

This path rechecks the current Git diff and active ownership before emitting a spawn;
the reviewer must still read through `read-review` before returning its verdict.
Stage 1 checks each AC against the diff; stage 2 is the quality pass. A complete verdict
must also return the exact structured JSON required by `foundry:review-pr`: ordered
`outcomes` bound to every supplied AC id/digest, plus `quality`. Save only that object in
a private scratch file. While the claim is still active, atomically materialize and bind
the proof before acting on pass/block; an old `claim_id` cannot bind a recovered
generation:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing record-review-proof --root <ROOT> --base <BASE-SHA> --issue <ISSUE-ID> --diff-hash <DIFF-HASH> --claim-id <CLAIM-ID> --outcomes-file /private/path/review-proof.json
```

Do not call `complete-review` afterwards: it is a deprecated compatibility command that
fails closed without mutating the ledger. `record-review-proof` is the only successful
terminal path because it binds evidence to that generation. A pass is all AC `pass`
with `quality=mergeable`; `AC: BLOCK` or a blocking finding records its exact failing
outcomes with `quality=blocked`, then STOP. An explicit host failure, timeout,
cancellation, or unknown return has no trustworthy complete verdict: retain its terminal
failure classification, do not fabricate a proof or mark the claim completed, and use
the human recovery protocol if work later resumes. Do not run a second reviewer workflow
on the same diff.

### 3. Human test — recommended
For any UI/runtime surface: derive a 3-5 step test procedure from the diff, rebuild/
deploy, and STOP for explicit validation. Exception: pure refactor (justify in the merge).

### 4. Merge (CI gate enforced in code)
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue merge <ISSUE-ID> <PR-NUMBER>
```
`foundry.issue merge` enforces the CI gate in code (ADR-0002) — an invariant, not an
instruction you could skip. It reads BOTH sources (check-runs + legacy commit-statuses,
so a red Jenkins blocks too) and refuses unless at least one check concluded `success`
with nothing red or pending (`neutral`/`skipped` are fine alongside a success, never
alone). Zero checks right after a push means the CI is queued — WAIT and retry, don't
override; the gate refuses `--allow-no-ci` while a fresh check-suite is queued.
`--allow-no-ci` is only for a repo that genuinely has no CI at all, and only after
asking the human. It squash-merges (REST, pinned to the CI-gated sha — if the head
moved since the verdict, it refuses and you re-run the gate), deletes the branch, sets
the issue `done`, and cleans up local.

### If a gate blocks
🔴 review finding or failed human test → put the issue back in the loop, fix, retry:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit transition <ISSUE-ID> in-progress
```
Record the blocking verdict for the role that must correct it (normally implementer)
with `routing escalation failure ... --kind review_blocking --idempotency-key <STABLE-REVIEW-EFFECT-ID>`.
Reuse the identifier only for a replay of that exact observed review. If the correction is
reviewed and blocked again, use the deterministic
`review_blocking_after_fix` signal with `--root <ROOT> --base <BASE-SHA>` from the
terminal blocked review proof; the CLI revalidates those immutable coordinates before
consuming a remediation credit. Use the selected tier
reported by that role's last route as `--current-tier`. If the decision reports
`human_required=true`, STOP rather than starting a third correction loop. A
`technical_blocked` result instead requires its bounded local diagnostic and never grants
campaign, merge, release, edit, or external-write authority. Review escalation changes
model/effort only.

### 5. Accept the framed ADRs
If the issue cited `proposed` ADRs (see its body / the `query issue` payload), promote
them now — the decision is validated by shipping:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr accept <ADR-ID>
```

## Anti-rules
- Never GraphQL (`gh pr create/merge/checks`). REST only — the adapter enforces it.
- Never bypass the CI gate. Never transition workflow state before `merged=true`.
  The only pre-merge administrative exception is the proof-bound, provider-audited AC
  checkbox synchronization defined in step 1; it grants no transition or merge authority.
