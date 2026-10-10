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
note but never changes tracker checkboxes or AC text. On a tracker with the typed
override port (currently Linear), the authority is instead a deterministic append-only
`acceptance-override` lifecycle receipt bound to the current review generation, PR URL,
head/base SHAs, diff digest and reason code, written before the code-host merge; the
issue then reads `done` with AC still incomplete. If an issue was merged under override
without that receipt and now fails with “done proof lacks matching acceptance”, re-run
exactly the same `issue merge <ID> <PR> --allow-incomplete-ac --ac-override-reason=<code>`:
it appends only the missing receipt bound to the existing done receipt, never merges or
rewrites anything, and a second replay is a no-op. YouTrack/DevHub keep the prose note.
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" query issue <ISSUE-ID>
```
The payload's `adrs` are only an index. Before review, load the full text of every cited
accepted ADR with `query adr <ADR-ID>`; a tracker-only ADR cannot be recovered by the
read-only Claude reviewer after it starts. If `adrs` is instead a
`{"status": "conflict", ...}`, `{"status": "adr_unavailable", ...}`, or
`{"status": "adr_issue_unavailable", ...}` object, the embedded ADR index is in
conflict, refers to an ADR absent from it, or has an unavailable declared ADR issue
relation — treat it
as unresolved, not as "no ADRs", and carry that into step 5.

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

### Grouped deferral of non-blocking remarks (rule R9 (b))
This is the behaviour of the Foundry skills in every repository where the plugin is
installed (maintainer decision of 2026-10-10); it is recorded as rule R9 in the Foundry
monorepo's `AGENTS.md` / `CLAUDE.md` and decided by its PAT-ADR-0018, and this skill's
text is what applies it.
After a review round that is fully validated (`AC: PASS` and `QUALITY: OK to merge`),
non-blocking remarks are not corrected in the PR. The remarks deferred from the issues
of an Epic go to a companion Epic, named `Nits` followed by the identifier of the origin
Epic (`Nits <ORIGIN-EPIC-ID>`). The origin Epic of an issue is its direct parent Epic:
an issue under a sub-Epic uses that sub-Epic. Give the two Epics no tracker link: no parent, no
dependency, no relation. What relates them is that name and a mention in text, nothing
else. Inside the companion Epic, put the remarks in a follow-up issue, one per batch or
per theme, never one per PR, and cite that issue in the PR description before the merge.
Three cases are still corrected before the merge and then fully re-reviewed:

- a remark the reviewer marks "fix before merge" (see `foundry:review-pr`);
- a remark on a page frozen after publication (release note, migration guide, a
  CHANGELOG section of a published version);
- a missing or generic documentation status under FOUNDRY-ADR-0018 (rule R5 where the
  repository's contract carries it).

There is no second-level deferral: when the PR belongs to an issue of a companion Epic,
correct its non-blocking remarks before the merge and re-review in full. Whether a remark
is rightly classed as deferrable is judged by the reviewer.

Three writes are authorized in advance by this skill's rule, without human confirmation,
inside and outside an Epic campaign (FOUNDRY-ADR-0013, FOUNDRY-ADR-0016), in every
repository where the plugin is installed, as an exception to the confirmation
`foundry:intake` requires before a write:

1. create the companion Epic, the first time: `edit create-issue` (command in
   `foundry:intake`, section 3) with the title `Nits <ORIGIN-EPIC-ID>`, the field
   `"Type": "Epic"` and no `parent`;
2. create a follow-up issue in it: `edit create-issue` with `parent` set to the
   companion Epic;
3. add deferred remarks to such an issue: `edit comment` (command in `foundry:open-pr`).

Perform them without asking and report them in the PR description: the companion Epic
when this PR created it, the follow-up issue ID and the number of deferred remarks.
Nothing else is pre-authorized: the companion Epic and its issues are neither
prioritized nor started without the maintainer (a human verdict under FOUNDRY-ADR-0014),
and every other intake write keeps its confirmation.

The rule names no tracker. Its writes are operations of the portable contract
`docs/tracker-contract.md`, whose table "Core journey" marks both rows below `supported`
in each of its three provider columns:

- row "Frame/intake/groom: create, comment" (`create_issue`, `add_comment`) carries
  writes 1 and 3;
- row "Epics/enfants/dépendances: child creation, relations" (`create_issue(parent=…)`,
  `link(depends-on|blocks|relates)`) carries write 2.

When one of the three writes does not go through, tell two cases apart:

- durable refusal (the project cannot carry the write, for example it has no `Epic`
  type): make no substitute write: correct the remarks of that round before the merge,
  re-review in full, and report the refusal in the PR description;
- transient failure (request quota exhausted, network failure): wait and retry the write
  before the merge; the merge waits for it. Do not correct the remarks instead and do not
  merge first. When the failure leaves the effect of the write unknown (a network
  failure), first read the tracker to see whether the write happened, and retry only if
  it did not, so that a retry creates no duplicate. If the adapter itself refuses the
  retry (it cannot replay the write safely), treat the case as a durable refusal:
  correct the remarks of that round before the merge, re-review in full, and report the
  refusal in the PR description.

An issue that has no origin Epic gets no deferral: correct its non-blocking remarks
before the merge and re-review in full. Inside an Epic campaign the authorization has no
effect yet. Three things are missing: a channel that carries the remarks to the campaign,
the code that creates the companion Epic and its issues, and campaigns usable on the
repository's tracker (`docs/review-rounds.md`, "What is not mechanical and what is not
coded yet"); they are left to a later issue, PAT-141.

The PR description carries one line per review round, kept current through
`foundry:open-pr` (that skill describes how a supplied summary replaces the PR body):

```text
round <N>; <validated|blocked>; follows <first|blocking|remarks>; <K> remarks deferred, follow-up <ISSUE-ID|none>
```

Baseline, observation window and return triggers: `docs/review-rounds.md`.

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
moved since the verdict, it refuses and you re-run the gate), sets the issue `done`,
then deletes the branch and cleans up local. If GitHub already reports the exact PR
merged, it repairs only the missing tracker transition and never calls the merge endpoint
again.

### If a gate blocks
🔴 review finding or failed human test → put the issue back in the loop, fix, retry:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit transition <ISSUE-ID> in-progress review
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

### Correction commits
Rule R9 (a) checklist and (c) minimal-change rule, of a repository that carries them,
apply to every correction commit, including one written by the coordinator itself:
change as few sentences as possible and re-check each new sentence against the code
before the next review. This is judged by the reviewer; no check enforces it.

### 5. Accept the framed ADRs
After the merge, promote only each `proposed` ADR that the exact delivery issue frames
with its unique `**Cadre (ADR) :** <ADR-ID>[, ...]` line. The `query issue` payload's
`adrs` field is a project retrieval index, never an issue-to-ADR relation; it cannot
authorize promotion. Use the bounded automated path, which verifies the issue body
before any ADR provider mutation:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" adr accept <ADR-ID> --framed-by <ISSUE-ID>
```
If there is no single unambiguous frame citation for the exact ADR, STOP: do not infer
one from a native relation or from the index. `adr accept <ADR-ID>` without
`--framed-by` remains the separate explicit human decision path. If the `query issue`
payload's `adrs` is a `{"status": "conflict", ...}`,
`{"status": "adr_unavailable", ...}`, or `{"status": "adr_issue_unavailable", ...}`
object rather than a list, do not conclude "no
proposed ADR to accept" — the index is unreadable, not empty. Acceptance stays blocked
until the conflict or unavailable ADR is resolved (`query adr`/`query adrs` still fail
closed on either); say so explicitly rather than silently skipping this step.

## Anti-rules
- Never GraphQL (`gh pr create/merge/checks`). REST only — the adapter enforces it.
- Never bypass the CI gate. Never transition workflow state before `merged=true`.
  The only pre-merge administrative exception is the proof-bound, provider-audited AC
  checkbox synchronization defined in step 1; it grants no transition or merge authority.
