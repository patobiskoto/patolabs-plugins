---
name: review-pr
description: >
  Reviews a branch or PR diff at blank context before merge in two stages: acceptance
  criteria compliance, then bugs, regressions, conventions, ADRs, scope, security, and
  tests. Strictly read-only. USE WHEN merge-pr needs its independent review gate or the
  user asks for a Foundry pre-merge review or an explicitly authorized advisory postmerge review.
allowed-tools: Read, Grep, Glob, Bash(git:*), Bash(gh:*), Bash(python3:*)
---

# review-pr — independent two-stage merge gate

## Explicit advisory postmerge mode

For an explicitly authorized postmerge audit, load `docs/postmerge-review.md` first.
Its CLI/schema/procedure sources are `postmerge_review.py::{prepare,read,record}`
and `routing.py`'s `postmerge-review` parser. Default ports refuse providers other
than Linear (`postmerge_review.py::ports`). Use that separate preparation path;
never rearm an original claim or call `record-review-proof` for an advisory report.
After the direct human decision, pass the exact audit ticket, repository, PR,
base/head/merge SHAs and diff hash through `--authorization-file`; generic authority
or a previous decision for a different attempt does not suffice (PAT-ADR-0020,
Human authorization; accepted, supersedes PAT-ADR-0019).
Documentation status for PAT-202: updated advisory descriptor, native evidence,
publication, input failures, publishable strings, legacy isolation and reader restrictions;
sources are `docs/postmerge-review.md` and
`postmerge_review.py::{prepare,native_evidence,record,restituted_reports}`.
PAT-ADR-0020 is accepted and supersedes PAT-ADR-0019 (PAT-ADR-0020, Decision).
Run `prepare` once, then launch the private `invocation.json`'s `launch_arguments`
with its `tool` in a new child (`postmerge_review.py::prepare`): Codex uses
`spawn_agent` with `fork_turns=none`; Claude uses logical `foundry:maigret` through
`Agent` and its actual routing hook, without `resume` (`invocation`).
Claude preparation requires explicit compatible `CLAUDE_PLUGIN_ROOT` and safe
root/packet paths before consumption (`advisory_plugin`, `prepare`).
Transcribe actual logical call arguments, correlated Claude hook context when
applicable, child identity and distinct return; the stable route comparison has
exactly the nine fields documented in the page (`native_evidence`, `CLAUDE_STABLE_FIELDS`).
A prepared plan is insufficient, and absent native model/effort/profile provenance
remains unknown (PAT-ADR-0020, Independence).
The child reads through the packet's exact `routing postmerge-review read`
command; all guarded Bash readers reject CR/LF, including the premerge readers
(`prepare`; `guard_routed_readonly.py::allows_verified_diff`). Review historical
criteria then quality and return findings/evidence (PAT-ADR-0020, Separation).
The coordinator records once through `record --packet-dir <private packet>`;
the tracker retains the complete publishable report, structured evidence and
metadata, followed by a distinct exact-readback confirmation (`record`).
An altered terminal stops without retry or compensation and leaves authority
consumed without verdict; a new authorization is required (PAT-ADR-0020, Altered
terminal receipt). Tracker-only restitution checks the report and confirmation
hashes (`restituted_reports`); private files retain raw native sources and snapshots
(`record`, `prepare`).
Submit defects to `foundry:intake`
without implicit edits, merge, closure, publication or promotion. This branch ends
here. The normal premerge path below retains its existing claim and deduplication.

This skill has a caller-side orchestration step followed by the independent premerge
review. Never launch a premerge reviewer before the claim below.

## Caller-side orchestration — before delegation

1. Load the issue acceptance criteria. Resolve the PR through GitHub REST and retain its
   exact 40-lowercase-hex `.base.sha` as `<BASE-SHA>`. Every claim, read and proof
   command must use that immutable value; a branch name or mutable remote ref is not an
   equivalent coordinate. Before the first claim, generate a fresh secret
   `<CLAIM-ATTEMPT-TOKEN>` with `secrets.token_hex(32)` (exactly 64 lowercase hex
   characters). Retain it privately until the claim command returns a complete JSON
   result; never include it in a packet, log, or reviewer message.
2. Unless the caller already supplied a claimed `diff_hash`, atomically claim the exact
   review diff now, in the current context. Both hosts use:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing claim-review --host <HOST> --issue <ISSUE-ID> --git-diff --root <ROOT> --base <BASE-SHA> --claim-attempt-token <CLAIM-ATTEMPT-TOKEN> > /private/path/claim.json
```


Use `codex` or `claude` for `<HOST>`; before the claim on Codex, pass the same
explicit `--tier`, `--model`, `--effort`, repeated `--available-model` observations,
and value-free `--profile-model-active` / `--profile-effort-active` signals that
apply to the later `codex-plan` call (`routing.py::main`; `docs/model-routing.md`,
"Cross-host review deduplication").
The Codex preflight, historical omission of `--host`, and profile warning semantics
are documented in `docs/model-routing.md` at that same section.

If that process is interrupted after publication but before returning any JSON, replay
the exact command with the same `<CLAIM-ATTEMPT-TOKEN>` and append
`--replay-interrupted`. Authenticated replays reconstruct the same capability without a
ledger write, so another interruption can be replayed again identically. Never use the
replay flag after a complete result. A normal retry without the flag remains a
capability-free duplicate.

If `should_run` is `false`, stop before creating a subagent: an unchanged diff already
has an owner on Claude or Codex. If true, retain the returned `diff_hash`, `generation`,
and secret `claim_id` until the verdict is recorded. Never disclose `claim_id` to a
second caller. The command preflights the issue's reviewer route before reserving the
diff, so a pending or invalid technical-remediation route cannot leave an active claim.
After a consumed implementer or reviewer remediation route, that preflight binds the first exact
diff hash to the reviewer exception. Retrying that hash is idempotent. A different hash
is refused unless the first claim has a canonical terminal proof for this exact issue,
whose quality is `mergeable` and whose acceptance criteria all pass. In that sole case,
the ledger atomically records one audited rearm and binds the new hash; every later
different hash remains refused before a reviewer claim.

Build the packet with the read-only command after a successful claim. Keep the complete
claim JSON in a private file (use `umask 077` before redirecting claim output there).
Reuse a caller's existing packet only for these exact active coordinates. Select the
accepted ADRs that constrain the review with repeated `--adr`; their full texts become
separate files, rather than a large inline task packet. Optional
`--declarations-file /private/path/declarations.json` carries a JSON object of coordinator
assertions; it never becomes tool-read evidence or external authority.

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing prepare-review --issue <ISSUE-ID> --pr <PR-NUMBER> --root <ROOT> --base <BASE-SHA> --claim-file /private/path/claim.json --output-dir /private/path/review-packet --adr <ADR-ID>
```

The command checks the PR's immutable base/head SHAs and the active claim before writing
`coordinates.json` and `reviewer-send.txt`; its stdout contains only a capability-free
summary. Send `reviewer-send.txt` only to the designated reviewer. That file supplies
`diff_hash`, `claim_id`, `root`, and `base` and references the packet files. Keep the
whole directory private; never paste its contents into a public PR, tracker note or log.
The provenance, bounds, file permissions and error output are documented in
`docs/review-packet.md`. Packet generation makes no tracker or PR writes and grants no
authority to bypass the independent review or the merge gates.

Under Codex, supply the generated send file to the façade with the already active claim:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing codex-plan reviewer --issue <ISSUE-ID> --packet-file /private/path/review-packet/reviewer-send.txt --git-diff --root <ROOT> --base <BASE-SHA> --diff-hash <DIFF-HASH> --claim-id <CLAIM-ID>
```

This uses the same common ledger and validates the existing owner, without making a
second claim. Append the value-free `--profile-model-active` / `--profile-effort-active`
flags when host-supplied effective profile metadata reports those keys.

3. Only now, if a subagent is available, launch a fresh one. On Codex pass every field
   of `spawn` unchanged, including explicit `model`, `reasoning_effort`, and
   `fork_turns=none`. Determine availability before calling `codex-plan`; if unavailable,
   include `--no-subagent` in that first plan call, perform the same read-only
   review from `current_context_instructions`, and display its disclosure that
   blank-context independence was lost. Launch one reviewer for this active claim;
   a second plan does not create a second review owner.

4. A Codex `current_context` fallback has no distinct host-return boundary and no
   completion capability. After an actual `mode=subagent` reviewer call settles,
   classify only its structured host state:

   - normal return: `--status completed --failure-class none`;
   - explicit host failure: `--status failed --failure-class host`;
   - explicit timeout: `--status failed --failure-class timeout`;
   - explicit cancellation: `--status cancelled --failure-class cancelled`;
   - absent/ambiguous trustworthy class: `--status unknown --failure-class unknown`.

   Consume
   an optional plan `telemetry.completion_capability` exactly once through the resolved
   CLI path with `telemetry complete --capability <CAPABILITY>` plus that exact mandatory
   pair. Partial output followed by an exception uses the structured exception class, or
   `unknown/unknown` when ambiguous; telemetry never changes or masks the host result.
   Keep a returned outcome capability private and, after closed aggregate review/test
   state is known, consume it once through
   `telemetry outcome --capability <OUTCOME-CAPABILITY>` with controlled aggregate flags
   only. Claude's hook is completely silent and privately maps only `PostToolUse` to
   `completed/none` and `PostToolUseFailure` to `failed/host`; timeout/cancellation
   detail and aggregate outcomes are unavailable at that host boundary, never inferred.
   Tokens never enter the reviewer packet. Missing state, IO failure, replay, or
   `observed=false` cannot change diff ownership, verdict, escalation, or any gate.

If the owner stopped before returning a verdict, recovery is a human-only CAS; there is
no timeout. Ask for an explicit non-empty audit reason, then use the exact hash and
generation returned by the common verdict:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing recover-review --diff-hash <DIFF-HASH> --generation <GENERATION> --claim-id <ACTIVE-CLAIM-ID> --root <ROOT> --base <BASE-SHA> --reason "<HUMAN-REASON>"
```

Claude and Codex use this same command. In one ledger-locked section, it checks the active
claim capability and original immutable `root`/`base` coordinates, rehashes that exact Git
diff, then creates the replacement generation. Only a successful recovery owns the new
`claim_id`; a concurrent stale generation, changed coordinates or diff, incomplete proof,
or any completed review is
refused. A canonical proof left before the ledger terminalizes is also incomplete and
blocks recovery. Do not put diff content, credentials, or other secrets in the audit
reason.

Under Codex, pass that successful recovery's exact capability back to the spawn façade:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing codex-plan reviewer --issue <ISSUE-ID> --packet-file /path/to/review-packet.txt --git-diff --root <ROOT> --base <BASE-SHA> --diff-hash <DIFF-HASH> --claim-id <NEW-CLAIM-ID>
```

The façade refuses a stale/completed owner, a changed Git diff, or a changed Git HEAD
(including an empty commit). A deduplicated caller
without that capability never receives it. The spawned reviewer must still use
`read-review`; the plan itself does not expose diff bytes.

## Independent reviewer method
1. Read the project's `AGENTS.md` / `CLAUDE.md` and referenced accepted ADRs first.
2. Load the issue acceptance criteria supplied by the caller.
3. Before any diff access, enforce this authoritative capability gate:

```json
{"required_before_diff_read":["diff_hash","claim_id"],"on_missing":"STOP"}
```

4. Read the diff only through the common verifier. It keeps the shared ledger lock from
the active claim, coordinate, and immutable-HEAD checks through the current exact diff hash and byte
emission, refusing a stale/mismatched claim before it emits the review input. A
Git-coordinated record with no immutable `head` is invalid and fails before any bytes are
read; terminal legacy-proof compatibility never grants an active reviewer capability:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing read-review --diff-hash <DIFF-HASH> --claim-id <CLAIM-ID> --root <ROOT> --base <BASE-SHA>
```

The routed message supplies the exact `root` and `base` verifier coordinates. Reuse them
unchanged; the verifier has no process-default fallback.

5. Read the touched files around the diff for context.

## Stage 1 — acceptance criteria
For every AC, rule **covered**, **not covered**, or **contradicted**, with `file:line`
evidence. Any not-covered or contradicted AC without explicit justification is blocking.
If no AC were supplied, say so explicitly.

## Stage 2 — code quality
Look for bugs and regressions, convention or accepted-ADR breaches, scope overrun,
security problems, and missing or misleading tests.

## Output
Start with an AC table, then findings ordered by severity:

- blocking — must fix before merge;
- nit — worthwhile but non-blocking;
- OK — only genuinely verified strengths.

A nit may carry the optional mark "fix before merge" in this human-readable output. The
mark changes neither the verdict line nor the JSON object below.

End with exactly: `AC: PASS|BLOCK · QUALITY: OK to merge|BLOCK`.

In addition to that human-readable verdict, return a separate JSON object with exactly
`outcomes` and `quality`. `outcomes` is an ordered array of
`{"id":"ac-N","digest":"<sha256>","verdict":"pass|fail|not_covered|contradicted"}`;
`quality` is `mergeable` or `blocked`. This JSON is the only review result eligible for
an AC proof: caller prose and the table are never parsed as evidence.

After that complete verdict returns, the caller makes this generation terminal before
continuing, whether the verdict passed or blocked. Use the proof command below: it
atomically binds the proof to the active claim and terminalizes that generation.

First save only that JSON object to a private scratch file and materialize the evidence
while the claim is still active. This command re-reads the current issue AC and exact
diff, fails closed, and prints only a redacted proof identifier:

```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" routing record-review-proof --root <ROOT> --base <BASE-SHA> --issue <ISSUE-ID> --diff-hash <DIFF-HASH> --claim-id <CLAIM-ID> --outcomes-file /private/path/review-proof.json
```

Completion by an old owner after recovery is refused. A completed hash remains
deduplicated forever. `complete-review` is deprecated and fails closed without terminalizing
anything; `record-review-proof` is the only terminal path.

After a terminal `mergeable` verdict, if the PR branch later only integrates the main
branch, the caller tries `routing carry-review-proof` before claiming a new review; the
conditions and the refusal behaviour are in `docs/review-rounds.md` ("Carrying a
terminal proof across an integration of main", PAT-ADR-0021), and the command is shown in
`foundry:merge-pr` step 2b. A refused carry means a full review of the new diff.

After the read-only reviewer returns, the caller records a blocking verdict (never the
reviewer itself) through `routing escalation failure <ISSUE-ID> <CORRECTION-ROLE>
--kind review_blocking --current-tier <SELECTED-TIER> --idempotency-key <STABLE-REVIEW-EFFECT-ID>`.
The exact same review replay must reuse that stable identifier. If this was the review of an
attempted correction, use `review_blocking_after_fix` with the same immutable
`--root <ROOT> --base <BASE-SHA>` coordinates used by the review proof; Foundry rechecks
that the proof is terminal and blocked before consuming a credit. A non-blocking verdict does not
touch escalation state. Stop immediately if the deterministic decision reports
`human_required=true`. A `technical_blocked` result stays fail-closed for campaign
effects and follows the bounded local diagnostic path; it is not a human-only verdict.
Continue a correction loop only if that command's JSON (not the review prose) reports
`action=remediation_continued` for the exact correction role and an active or
just-exhausted machine-readable remediation authorization. Never infer a retry or human
authority from prose or a prior plan.
