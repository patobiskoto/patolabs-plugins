# Epic campaign effects: start, implement, open the PR, review, correct, merge (PAT-217, PAT-218, PAT-ADR-0026)

One pass of the cadencer ([`campaign-cadence.md`](campaign-cadence.md)) computes the one next
step; this page covers its execution (`tooling/foundry/campaign_effects.py`). A pass runs at
most one effect. The Foundry commands below are the only path to the tracker and the code
host; the Dev Hub coordinator (`campaign_coordinator.py`, `campaign_runtime.py`) is not used.
Claude Code is the only host wired: no Codex session is launched. Reviewing and correcting
are executed since PAT-218 (`tooling/foundry/campaign_review.py`); carrying the non-blocking
remarks to a companion Epic is PAT-141 and is not done. The procedure below was exercised on
test doubles only (`tests/test_campaign_review.py`, `tests/test_campaign_effects.py`), never
against a real tracker, code host or `claude`.

## Commands

```text
FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py campaign-cadence step PAT-100 [--root DIR]
FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py campaign-cadence run PAT-100 [--root DIR]
    [--max-steps N] [--poll-seconds S] [--budget-usd B] [--worktrees-dir DIR]
```

`step` is one pass and at most one effect. `run` repeats `step` until the outcome is neither
`executed` nor `wait-ci`, or `max-steps` passes were made (`run`, defaults `DEFAULT_MAX_STEPS`,
`DEFAULT_POLL_SECONDS`; bounds `MAX_STEPS`, `MAX_POLL_SECONDS`, `MAX_BUDGET_USD`). On
`wait-ci` it sleeps `--poll-seconds` and observes again: re-reading is not an effect, and each
such pass counts as a step. The output is one JSON object (`EFFECTS_CONTRACT`) with the
outcome of each pass. The exit code is 2 for a missing capability, 1 when an effect failed,
0 otherwise. An approval must exist (`campaign-approval.md`); without a valid one the pass
returns `refuse` and launches nothing.

## Effects

| Decision | Effect (one subprocess) | Where |
| --- | --- | --- |
| `start` | `foundry_cli.py issue start <ID>` after the worktree is ensured | ticket worktree |
| `implement` | one `claude --print` session, see below | ticket worktree |
| `open-pr` | `foundry_cli.py issue openpr <ID> --summary-stdin` (the flag only when a summary was saved and no PR exists); also the push of a correction (`correction_not_pushed`, without the flag) | ticket worktree |
| `merge` | `foundry_cli.py issue merge <ID> <PR>`; never an `--allow-*` flag | ticket worktree |
| `wait-ci`, `await-verdict`, `suspend`, `refuse` | none | |
| `review` | claim, packet, one read-only reviewer session, proof: see "The review effect" | ticket worktree |
| `correct` | escalation failure, then one implementer session: see "The correction effect" | ticket worktree |
| `correct` for `ci_failing` | none: the pass returns `suspend` `ci_failing_not_executed` (no effect reads CI logs) | |

The merge gates are those of `issue merge` (`issue.py`): CI proven green on two sources
(FOUNDRY-ADR-0002, `write.ci_gate`) and the review proof of the exact diff
(`valid_for_merge`). `step` calls `issue merge` only on a `merge` decision, which
`campaign_cadence` returns only with a valid proof and green CI observed in the same pass;
there is no per-ticket human validation. The Epic stops at `await-verdict`; nothing closes it.

The worktree of a ticket is a linked git worktree at `<repo>-campaign/<ID>` next to the
repository (`default_worktrees_dir`, or `--worktrees-dir`), created by `git worktree add`
(`ensure_worktree`): a local git operation, neither a tracker nor a code-host write. It is
detached at the remote default branch for a new ticket, or on the existing ticket branch.
`issue start` then creates the branch from it, as it does in any linked worktree
(`_prepare_branch`, `issue.py`). `issue openpr` pushes the branch itself (`openpr`,
`issue.py`); the cadencer runs no `git push`.

## The implementation session

One fresh headless session per `implement` attempt, launched by `_launch` with the argv of
`session_argv`:

```text
claude --print --output-format json --model <routed model> --max-budget-usd <budget>
  --permission-mode auto --plugin-dir <plugin root> --name foundry-campaign-<EPIC>-<ID>
  [--effort <routed effort>] --session-id <uuid5 of the attempt identity>
  --disallowedTools <DISALLOWED_TOOLS>
```

- Model and effort come from `RoutingPolicy.load(root).resolve("implementer", "claude")`,
  translated by `claude_invocation_model` and `claude_effort_parameters`, as in
  `ClaudeCommandEffectProvider._invoke` (`command_runtime.py`); a pin with a minimum host
  version is checked by `claude_headless_host_version_requirement` before the attempt is
  claimed. The route is recorded in the `started` line.
- The prompt (`implement_prompt`) goes through standard input: it carries the issue title
  and body read by the coordinator (`tracker.get_issue`, before the attempt is claimed),
  because the session's Foundry configuration is isolated and resolves no tracker
  credential; the session implements its acceptance criteria, run the tests, commit locally, end with a
  pull-request summary. The session may run the tests.
- Budget: `--budget-usd`, default `IMPLEMENT_BUDGET_USD`, at most `MAX_BUDGET_USD`; the
  default is not measured (unknown). Timeout `IMPLEMENT_TIMEOUT_SECONDS`.
- The summary (the session `result`) is saved under `campaign-summaries/` next to the
  journal (`_save_summary`, mode 0600, at most `MAX_SUMMARY_BYTES`) and reaches
  `issue openpr` on standard input.

### Authority and its limit

The session receives the child environment of `_claude_child_environment`
(`command_runtime.py`: the `_CLAUDE_CHILD_ENV_ALLOWLIST` of AGENTS.md#R6 plus
`FOUNDRY_RUNTIME_CONFIG_ISOLATED`), so no tracker or code-host token variable passes, and a
Foundry command run by the session does not resolve secrets from host configuration
(`config.get`, `_runtime_config_isolated`). `DISALLOWED_TOOLS` denies, by prefix rules only, `git push`,
`gh`, and the Foundry verbs `issue`, `edit`, `adr` and `campaign-approval approve` (other
Foundry verbs that write, such as `routing record-review-proof`, are not listed); the plugin
guard (`hooks/guard_bash.py`) also denies `gh pr create/merge`, a push to the default branch
and `campaign-approval approve` when the plugin is active, because `--plugin-dir` is passed.

The limit, stated: the session runs as the same OS user as the cadencer. A token kept in the
user's Foundry configuration file or keychain, or any credential of that user, is readable by
a command the session writes outside the Foundry code paths; a plain spelling outside the
prefix rules (`git -C . push`, an absolute path to `git` or `gh`) is not denied (deduced);
and the session can write the local Foundry state of `registry.data_dir()`, including the
review-proof store, the approval journal and the attempt journal, so a forged review proof
would let the cadencer merge without a real review. This is a discipline boundary,
not a sandbox (the framing of `guard_bash.py`, whose `bash -c` indirection is out of scope by
design). Whether the host matches the mid-command wildcards of `DISALLOWED_TOOLS` is not
verified here (unknown); the tests pin the argv, not the host's matching.

## The attempt journal

Append-only JSONL, `campaign-attempts.jsonl` in `registry.data_dir()` (`journal_path`, the
directory of `campaign-approvals.jsonl`), mode 0600, written under an exclusive `flock`
(`claim`, `finish`). Two kinds of line (`_KEYS`):

- `started`, written before the effect: `attempt`, `epic`, `ticket`, `kind`, `fingerprint`,
  `session_id`, `route`, `at`.
- `finished`, after it: `attempt`, `status` (`completed` or `failed`), `exit_code`,
  `cost_usd`, `duration_ms`, `summary_file`, `branch_head`, `error`, `at`; and, for a
  `review` only, the optional closed key `review` (`REVIEW_KEYS`): `verdict` (`blocked` or
  `validated`), `fix_before_merge`, `non_blocking`, `diff_hash`, `base`, `head`, `carried`,
  `partial`. A journal written before PAT-218 stays valid (`_OPTIONAL`).

The `attempt` identity is the sha256 of the Epic, the ticket, the step kind and the
fingerprint (`identity`): tracker state, branch head sha, PR number and PR head sha
(`fingerprint`). `claim` reads and appends under one lock: if a `started` line with the same
identity exists, nothing is launched and the pass returns `suspend` `attempt_already_made`.
That includes an attempt that crashed between `started` and `finished`, and a failed one: an
effect that may have happened is never replayed. The way out is human: change what the
fingerprint observes (for example finish the work on the branch), or leave the attempt
failed; no command forgets an attempt, and removing a line by hand is not supported. An
unreadable or malformed line makes the pass `suspend` `attempts_journal_invalid` before any
read of the tracker.

Ceilings: `claim` counts the `started` lines of kinds `SESSION_KINDS` (`implement`, `review`,
`correct`) of the Epic against
`max_sessions`, and of the ticket against `max_sessions_per_issue`, both from the approval
record re-checked at the moment of the effect; over a ceiling the pass returns `refuse`
`session_ceiling_reached` or `session_ceiling_per_issue_reached` before any launch. A reviewer
session counts like an implementer session, and a `review` attempt counts even when it ends
without a session (proof carried, claim not granted): the count errs on the safe side. A ticket
that goes through implement, three reviews and two corrections uses six sessions, so the
per-issue ceiling of the approval must allow them (`campaign-approval.md`).

"Implementation finished" is observable through this journal: the last `finished` line of
kind `implement` or `correct`, `completed`, whose `branch_head` is the current branch head
(`last_implementation`), makes the decision `open-pr` when there is no PR and the branch has
commits beyond the remote default branch, and `open-pr` `correction_not_pushed` when a PR is
open and its head is not the branch head (`campaign_cadence._ticket_step`). A session that commits nothing leaves the fingerprint
unchanged, so the next `implement` is refused as a replay.

## The review effect

One pass for a `review` decision (`campaign_review.launch_review`), all inside one `started` /
`finished` pair, every step a Foundry command run as a subprocess of this plugin's
`foundry_cli.py` in the ticket worktree:

1. If the previous round allows it, `routing carry-review-proof` (see "Partial review and
   proof carry"); a success ends the effect with no session.
2. `routing claim-review --host claude --issue <ID> --git-diff --root <worktree> --base <PR
   base sha> --claim-attempt-token <token>`, the token being `secrets.token_hex(32)`
   generated for this pass and kept nowhere. If the answer is not `should_run: true` (another
   owner, or a terminal claim), no session is launched and the attempt is `failed`
   (`claim_not_granted`). The claim runs the reviewer escalation preflight itself
   (`routing.py`, `claim-review` branch).
3. The complete claim JSON, which holds `claim_id`, is written to
   `campaign-reviews/<first 16 hex of the attempt>/claim.json` next to the journal (directory
   0700, file 0600), then `routing prepare-review --issue --pr --root --base --claim-file
   --output-dir <that directory>/packet` writes the private packet
   (`docs/review-packet.md`). No `--adr` is passed: the campaign selects no ADR text, so the
   reviewer sees the ADR index (`adr-index.json`) and the repository files only; that is a
   limit, not a decision.
4. One fresh `claude --print` reviewer session, see below. Its final message must end with the
   marker line `FOUNDRY_REVIEW_RESULT_V1` (`RESULT_SENTINEL`) and one JSON object. `parse_result` accepts exactly the keys
   `outcomes` (non-empty list of `{id, digest (64 hex), verdict}` with `verdict` in `pass`,
   `fail`, `not_covered`, `contradicted`), `quality` (`mergeable` or `blocked`),
   `fix_before_merge` and `non_blocking` (integers from 0 to `MAX_REMARK_COUNT`), a non-empty
   report before the marker, and refuses `mergeable` with a verdict other than `pass`. Anything
   else makes the attempt `failed` (`reviewer_output_invalid`) and records no proof.
5. The report (the text before the marker, `claim_id` and token replaced by `[redacted]`) is
   saved with `_save_summary`; the list-form file `{"outcomes": [...], "quality": ...}` (exactly
   those two keys: `record-review-proof` refuses any other shape, `routing.py`) is written 0600
   and `routing record-review-proof --root --base --issue --diff-hash --claim-id
   --outcomes-file` records the proof; the issue criteria are re-read there, so ids and digests
   that differ from the issue's are refused (attempt `failed`, `proof_refused`, claim left
   active, `campaign-reviews/<attempt>/` kept for a human; recovery is `routing recover-review`,
   a human decision, `skills/review-pr/SKILL.md`). After a recorded proof the directory
   (claim, packet, outcomes file) is removed.
6. PAT-ADR-0022 asks the PR description to say that a partial review took place and that the
   proof relies on the previous round. `update_pr_body` runs `issue openpr <ID>
   --summary-stdin` on the open PR (`_reuse_or_open_pr` replaces the body when a summary is
   supplied, `issue.py`), with the saved implementation summary and a `## Review rounds`
   section rebuilt from the journal (`round_line`: full, partial or carried, verdict, short
   head sha, and for a partial round the sentence on the reliance). It is a Foundry command,
   not a session, and is made after a carried proof too. A refusal leaves the verdict
   recorded and sets the detail `pr_body_not_updated`. Without a saved summary the body is
   the rounds section only.

The `finished` line carries the verdict (`review` key) and `summary_file` (the report). The
step output (`effect.review`, `effect.report_file`) exposes the verdict, the number of remarks
marked "fix before merge" and the number of other remarks. The text of the non-blocking
remarks without the mark stays in the report file: carrying it to a companion Epic is PAT-141
and is not done here.

### The reviewer session and its limit

```text
claude --print --output-format json --model <routed model> --max-budget-usd <budget>
  --permission-mode dontAsk --tools Read,Grep,Glob,Bash --add-dir <packet directory>
  --setting-sources user --strict-mcp-config --mcp-config '{"mcpServers":{}}'
  --name foundry-campaign-<EPIC>-<ID>-review [--effort <routed effort>]
  --session-id <uuid5 of the attempt identity>
  --allowedTools Read Grep Glob "Bash(python3 <cli> routing read-review *)"
  "Bash(python3 <cli> routing search-review *)" "Bash(python3 <cli> routing list-review *)"
```

(`reviewer_argv`). Model and effort come from `RoutingPolicy.resolve("reviewer", "claude",
minimum_tier=<escalation floor>)`: the reviewer is a gate role, so the route cannot be below
`GATE_FLOORS["reviewer"]` (`frontier`) and effort `GATE_EFFORT_FLOORS["reviewer"]` (`high`,
`routing.py`; FOUNDRY-ADR-0006); `tests/test_campaign_review.py` asserts the argv against
that resolution. The budget is `--budget-usd` (the same value as an implementer session);
timeout `REVIEW_TIMEOUT_SECONDS`; the child environment is that of the implementer session
(`_claude_child_environment`) plus `FOUNDRY_DATA` when it is set (`plan_review`): the read
verbs find the claim under `registry.data_dir()`, which reads that variable (`registry.py`);
without it, deduced, they would look in the default directory. The implementer session does
not receive it. `--setting-sources user --strict-mcp-config --mcp-config` (the flags are listed
by the installed `claude --help`; the last two are used by `campaign_runtime.py`) keep the
`.claude/settings*.json` of the worktree, which the diff or an earlier session may have
written, and any project MCP server out of the reviewer session; the `session_argv` of the
implementer session is unchanged, because the `DISALLOWED_TOOLS` deny rules stay in force
whatever the scopes allow (deduced, not run). The prompt (`review_prompt`, standard input) carries the
reviewer contract of `skills/review-pr/SKILL.md` (stages, output, marker), the exact
`read-review` command, and the send text written by `prepare-review`, followed by a sentence
saying that the output contract overrides that text (which asks to load `review-pr` and to
return its JSON; `review_packet.py` is unchanged, for the manual workflow); it carries `claim_id`
because the reviewer needs it to read the diff, and the argv of the session does not.

Why `dontAsk`: a tool call that no rule pre-approves is refused without a prompt, so with the
`--allowedTools` list above the session can only read files, search, and run the three
read verbs (deduced). That a mode refuses what no rule approves is
established for `dontAsk` by the local-first runner (`local_first_runner.py`, `NATIVE_PERMISSION_MODE`,
trials recorded in `CHANGELOG.md`, PAT-124); the flags `--tools` and `--allowedTools` are used
by `campaign_runtime.py` and named in `local_first_runner.py`. Not run here: whether the host
matches the `Bash(...)` wildcard rules as written, or refuses a compound command that starts
with an allowed prefix, and whether `Read` of the packet directory is allowed through
`--add-dir` (unknown). Remaining limit: the `user` scope is still loaded, so the permission
rules of the user's own settings apply to the reviewer, and its `Read` reaches any file the OS
user can read. The session runs as the same OS user as the cadencer and in the ticket
worktree: the limit stated for the implementer session applies, a discipline boundary and not
a sandbox, and the reviewer does not hold the `record-review-proof` verb in its rules (the
coordinator records the proof, as in the manual workflow).

## The correction effect

For a `correct` decision with reason `review_blocked` or `review_fix_before_merge`
(`campaign_review.plan_correct`, before the attempt is claimed):

1. The report saved for the exact PR head and base is read from the journal; without one (for
   example a proof recorded by hand) the pass fails at the plan phase and nothing is launched.
2. For a blocking review only, `routing escalation failure <ID> implementer --kind
   review_blocking` (`review_blocking_after_fix` only when the blocked PR head is the
   `branch_head` of a completed `correct` attempt: "a correction was reviewed and blocked
   again", `model-routing.md`) `--current-tier <routed tier without the escalation floor> --idempotency-key campaign-<EPIC>-<ID>-<kind>-<head>
   --root <worktree> --base <PR base sha>`. It runs before the claim because its answer decides
   whether and at which tier the session runs; the key and the tier sent are stable (the floor is not
   added: `record_failure` raises the tier to it), so a replay after `escalated` sends the same
   signal and writes nothing more (`escalation.py`, `record_failure`). Before it, `plan_correct` applies the refusals of
   `claim` (`refusal`: attempt already made, session ceilings), so a signal is not recorded for
   an attempt the claim would refuse. `human_required: true` (the command also exits
   2) returns `suspend` `human_required`; `action: technical_blocked` returns `suspend`
   `technical_blocked`; any `action` outside `CONTINUE_ACTIONS` (`failure_recorded`,
   `escalated`, `remediation_continued`; for example `authority_ambiguous`) returns `suspend`
   with the action as reason; no session is launched and no attempt is claimed. Otherwise the
   session runs at the tier of `final_tier`. A validated review with remarks marked "fix before merge"
   records no escalation failure (`skills/review-pr/SKILL.md`: a non-blocking verdict touches
   no escalation state).
3. One fresh implementer session (`session_argv`, the argv of "The implementation session")
   whose prompt (`correct_prompt`) carries the issue and the saved report, asks to fix only the
   blocking findings, or only the remarks marked "fix before merge", and to follow
   AGENTS.md#R9(c): change as few sentences as possible and re-check every new sentence
   against the code. The other non-blocking remarks are left untouched.
4. The session commits locally only. The next pass sees the branch ahead of the PR head and
   decides `open-pr` `correction_not_pushed`: `issue openpr <ID>` pushes the branch and reuses
   the open PR without replacing its body (`_reuse_or_open_pr`, `issue.py`). That the `review`
   transition accepts a repeat on a ticket already in `review` is not exercised here (unknown).
   A new head has no proof, so the pass after that decides `review`.

## Bounds

Constants `MAX_REVIEW_ROUNDS` (3) and `MAX_CORRECTIONS` (2) in `campaign_cadence.py`, chosen
and not measured (unknown), counted per ticket from the attempt journal (`campaign_review.history`):
the recorded, non-carried `review` verdicts, and the `started` `correct` lines. A `review`
decision at three rounds returns `suspend` `review_rounds_exhausted`; a `correct` decision at
two corrections returns `suspend` `corrections_exhausted` (also for remarks to fix: they are
never merged over). `decide` stays pure: the counts are facts of the observation
(`TicketFacts`). Together with `human_required` these are the three ways the loop ends in a
suspension that waits for a human; the approval ceilings (`refuse`) also apply.

## Partial review and proof carry

Decided from the journal by `campaign_review.review_mode`, for the review attempt:

- `partial` (PAT-ADR-0022, `AGENTS.md#R9`): the last recorded round is `validated` and the
  last correction session ended on exactly the head now reviewed. The prompt says so, and
  `--declarations-file` carries the previous verdict, the previous report (at most
  `MAX_PREVIOUS_REPORT_BYTES`) and the commit range `<previous head>..<head>` to the packet
  as unverified coordinator assertions (`docs/review-packet.md`); the reviewer returns a verdict
  on all criteria for the exact diff and may ask for a full review with a reason. A round after
  a blocking review is always `full`. The proof is the same as a full one and relies on the
  previous round for the unchanged parts; no mechanical check tells them apart (`review-rounds.md`).
- `carry` (PAT-ADR-0021, `review-rounds.md`, "Carrying a terminal proof"): the last round is
  validated with no remark to fix, no correction since, and only the base differs. The effect
  runs `routing carry-review-proof --issue --root --base <new base> --from-base <old base>
  --from-diff-hash <old diff hash>` first; `AcceptanceProofStore.carry` checks the conditions
  (ancestry, clean merge tree, no path changed by both, identical diff bytes) and a refusal
  makes the same attempt go on with a full review. A carried review is recorded with
  `carried: true`, is not a review round and launches no session.
- Anything else is `full`.

## Not executed yet

Carrying the non-blocking remarks to a companion Epic (PAT-141); correcting a red CI; ADR
selection for the reviewer packet; a Codex session; closing the Epic; the replay of a failed
attempt with the same fingerprint. A failed `implement` session that committed changes the
branch head, so the next pass launches a new session with a new identity: retries are
bounded only by `max_sessions` and `max_sessions_per_issue`, so the worst-case spend is that
ceiling times `--budget-usd`. Tests: `tests/test_campaign_effects.py` (one effect per subprocess, the `claude`
argv, the allowlisted environment, no replay including after a crash, ceilings, merge only on
a `merge` decision) and `tests/test_campaign_review.py` (the review path, the reviewer argv,
strict parsing, escalation, the correction prompt, bounds, partial review, carry, secrets, and
a two-ticket Epic with a blocking first review that reaches `await-verdict` with every proof
produced by a double of the reviewer session).

R5 status (PAT-218): updated `docs/campaign-effects.md`, `docs/campaign-cadence.md`,
`docs/review-rounds.md`, `AGENTS.md`/`CLAUDE.md` R9 ("What is not coded yet"),
`README.md`. Candidate artefacts: new constants `MAX_REVIEW_ROUNDS`, `MAX_CORRECTIONS`,
`REVIEWER_TOOLS`, `RESULT_SENTINEL`, `SESSION_KINDS`; new decision reasons
`correction_not_pushed`, `review_fix_before_merge`, `review_rounds_exhausted`,
`corrections_exhausted`, `human_required`, `technical_blocked`, `ci_failing_not_executed`;
the optional journal key `review`. No CLI verb or option, configuration key or routing table
changed.
