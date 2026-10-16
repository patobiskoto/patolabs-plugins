# Epic campaign effects: start, implement, open the PR, merge (PAT-217, PAT-ADR-0026)

One pass of the cadencer ([`campaign-cadence.md`](campaign-cadence.md)) computes the one next
step; this page covers its execution (`tooling/foundry/campaign_effects.py`). A pass runs at
most one effect. The Foundry commands below are the only path to the tracker and the code
host; the Dev Hub coordinator (`campaign_coordinator.py`, `campaign_runtime.py`) is not used.
Claude Code is the only host wired: no Codex session is launched. Reviewing and correcting
are not executed here (PAT-218).

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
| `open-pr` | `foundry_cli.py issue openpr <ID> --summary-stdin` (the flag only when a summary was saved) | ticket worktree |
| `merge` | `foundry_cli.py issue merge <ID> <PR>`; never an `--allow-*` flag | ticket worktree |
| `wait-ci`, `await-verdict`, `suspend`, `refuse` | none | |
| `review`, `correct` | none: outcome `awaiting_review_execution`, `run` stops (PAT-218) | |

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
(`config.get`, `_runtime_config_isolated`). `DISALLOWED_TOOLS` denies `git push`, `gh`, and
the Foundry write verbs `issue`, `edit`, `adr` and `campaign-approval approve`; the plugin
guard (`hooks/guard_bash.py`) also denies `gh pr create/merge`, a push to the default branch
and `campaign-approval approve` when the plugin is active, because `--plugin-dir` is passed.

The limit, stated: the session runs as the same OS user as the cadencer. A token kept in the
user's Foundry configuration file or keychain, or any credential of that user, is readable by
a command the session writes outside the Foundry code paths. This is a discipline boundary,
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
  `cost_usd`, `duration_ms`, `summary_file`, `branch_head`, `error`, `at`.

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

Ceilings: `claim` counts the `started` lines of kind `implement` of the Epic against
`max_sessions`, and of the ticket against `max_sessions_per_issue`, both from the approval
record re-checked at the moment of the effect; over a ceiling the pass returns `refuse`
`session_ceiling_reached` or `session_ceiling_per_issue_reached` before any launch. Only
`implement` launches a session (`SESSION_KINDS`).

"Implementation finished" is observable through this journal: the last `finished` line of
kind `implement`, `completed`, whose `branch_head` is the current branch head, with commits
beyond the remote default branch, makes the decision `open-pr` (`last_implementation`,
`campaign_cadence._ticket_step`). A session that commits nothing leaves the fingerprint
unchanged, so the next `implement` is refused as a replay.

## Not executed yet

`review` and `correct` (PAT-218); a Codex session; closing the Epic; any retry of a failed
attempt. Tests: `tests/test_campaign_effects.py` (one effect per subprocess, the `claude`
argv, the allowlisted environment, no replay including after a crash, ceilings, merge only on
a `merge` decision, and a two-ticket Epic that reaches `await-verdict` on a tracker double,
with the review proof and the CI result injected).
