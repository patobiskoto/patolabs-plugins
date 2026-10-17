# Epic campaign effects: start, implement, open the PR, review, correct, defer remarks, merge (PAT-217, PAT-218, PAT-141, PAT-ADR-0026, PAT-ADR-0028)

One pass of the cadencer ([`campaign-cadence.md`](campaign-cadence.md)) computes the one next
step; this page covers its execution (`tooling/foundry/campaign_effects.py`). A pass runs at
most one effect. The Foundry commands below are the only path to the tracker and the code
host; the Dev Hub coordinator (`campaign_coordinator.py`, `campaign_runtime.py`) is not used.
Claude Code is the only host wired: no Codex session is launched. Reviewing and correcting
are executed since PAT-218 (`tooling/foundry/campaign_review.py`); carrying the non-blocking
remarks to a companion Epic is the `defer-remarks` effect of PAT-141
(`tooling/foundry/campaign_defer.py`, section "The defer-remarks effect"). The procedure
below was exercised on test doubles only (`tests/test_campaign_review.py`,
`tests/test_campaign_effects.py`, `tests/test_campaign_defer.py`), never against a real
tracker, code host or `claude`.

## Commands

```text
FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py campaign-cadence step PAT-100 [--root DIR]
FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py campaign-cadence run PAT-100 [--root DIR]
    [--max-steps N] [--poll-seconds S] [--budget-usd B] [--worktrees-dir DIR]
```

`step` is one pass and at most one effect. `run` repeats `step` until the outcome is neither
`executed`, `wait-ci` nor `waiting`, or `max-steps` passes were made (`run`, defaults `DEFAULT_MAX_STEPS`,
`DEFAULT_POLL_SECONDS`; bounds `MAX_STEPS`, `MAX_POLL_SECONDS`, `MAX_BUDGET_USD`). On
`wait-ci` or `waiting` it sleeps `--poll-seconds` and observes again: re-reading is not an effect, and each
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
| `wait-ci`, `wait`, `await-verdict`, `suspend`, `refuse` | none | |
| `review` | claim, packet, one read-only reviewer session, proof: see "The review effect" | ticket worktree |
| `correct` | escalation failure, then one implementer session: see "The correction effect" | ticket worktree |
| `defer-remarks` | no session and no command of its own: reads and `edit` writes, see "The defer-remarks effect" | ticket worktree (the `edit` commands run there) |
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
`issue.py`); the cadencer runs no `git push`. A worktree is never removed: nothing in
`campaign_effects.py`, `campaign_review.py` or `campaign_defer.py` runs `git worktree remove`
or deletes it (searched, PAT-236); a finished ticket keeps its directory under the worktrees
directory until a human removes it, and `ensure_worktree` reuses an existing directory as is.
`--worktrees-dir` is resolved against the current directory when the command starts (`cli`).
For an `implement`, `correct` or `review` pass, `_step` creates the worktree (`ensure_worktree`)
and, for `implement` and `correct`, reads its git directories (`git_dirs`) before the attempt
is claimed: an error there is a `failed` outcome with `phase: plan` and consumes no attempt.

## The implementation session

One fresh headless session per `implement` attempt, launched by `_launch` with the argv of
`session_argv`:

```text
claude --print --output-format json --model <routed model> --max-budget-usd <budget>
  --plugin-dir <plugin root> --name foundry-campaign-<EPIC>-<ID> --setting-sources user
  --strict-mcp-config --mcp-config '{"mcpServers":{}}' [--effort <routed effort>] --session-id <uuid5 of the attempt identity>
  --disallowedTools <DISALLOWED_TOOLS> --permission-mode dontAsk --settings <sandbox JSON>
```

The last two flags come from `with_native_sandbox` (`local_first_runner.py`, PAT-220); the
JSON is `sandbox_settings`, below. A correction session (`campaign_review.py`) is launched by
the same `_launch` and `session_argv`, so it has the same sandbox.

- Model and effort come from `RoutingPolicy.load(root).resolve("implementer", "claude")`,
  translated by `claude_invocation_model` and `claude_effort_parameters`, as in
  `ClaudeCommandEffectProvider._invoke` (`command_runtime.py`); a pin with a minimum host
  version is checked by `claude_headless_host_version_requirement` before the attempt is
  claimed (`session_plan`: `claude --version` runs in the directory the session runs in, the
  ticket worktree, with the session environment, `plan["env"]`; AGENTS.md#R7). The route is
  recorded in the `started` line.
- The prompt (`implement_prompt`) goes through standard input: it carries the issue title
  and body read by the coordinator (`tracker.get_issue`, before the attempt is claimed),
  because the session's Foundry configuration is isolated and resolves no tracker
  credential; the session implements its acceptance criteria, runs the tests, commits locally
  and ends with a pull-request summary. The prompt asks for the exact first line
  `FOUNDRY_SUMMARY_V1` (`SUMMARY_MARK`, PAT-227; see "The summary criterion").
- Budget: `--budget-usd`, default `IMPLEMENT_BUDGET_USD`, at most `MAX_BUDGET_USD`; the
  default is not measured (unknown). Timeout `IMPLEMENT_TIMEOUT_SECONDS`.
- The summary (the session `result`) is saved under `campaign-summaries/` next to the
  journal (`_save_summary`, mode 0600, at most `MAX_SUMMARY_BYTES`) and reaches
  `issue openpr` on standard input, after the criterion below.

### The summary criterion (PAT-227)

Observed 2026-10-17 (`docs/qualification/pat-219-epic-campaign-2026-10-17.md`): the final summary of
an implementation session was the reply to a background-command notification and was published as
the PR body unchanged; the cause is deduced (the session ran its tests as a background command and
`claude --print` returned its last turn), not observed. The criterion is mechanical
(`usable_summary`): once its leading line breaks are dropped, a summary is usable when its first
line, stripped of surrounding whitespace, is `FOUNDRY_SUMMARY_V1` (`SUMMARY_MARK`) and the rest,
stripped, is not empty
(`test_the_summary_criterion_tolerates_blank_lines_and_edge_spaces_around_the_mark`,
`tests/test_campaign_review.py`); the prompts ask the session for that line as its exact first line.
A saved summary file that does not decode as UTF-8 is read as no summary (`_plan`) and published as
`SUMMARY_UNUSABLE_BODY`, and `_save_summary` cuts the text on a character boundary so that its file
decodes. The implementation and correction prompts ask for that line. `published_summary` returns
the summary without that line, or, when it is empty, lacks the line or has nothing after it, the
fixed text `SUMMARY_UNUSABLE_BODY` (`Session summary unusable (no FOUNDRY_SUMMARY_V1 line); see the
review rounds.`); the `open-pr` effect (`_plan`) and `update_pr_body` publish only that, and the
reviewer prompt of a description that is that placeholder says so (`review_prompt`, `placeholder`).
The saved file keeps the summary as the session wrote it. Whether a session that follows the prompt
always prints the line is unknown.

### Authority and its limit

The session receives the child environment of `_claude_child_environment`
(`command_runtime.py`: the `_CLAUDE_CHILD_ENV_ALLOWLIST` of AGENTS.md#R6 plus
`FOUNDRY_RUNTIME_CONFIG_ISOLATED`), so no tracker or code-host token variable passes, and a
Foundry command run by the session does not resolve secrets from host configuration
(`config.get`, `_runtime_config_isolated`). `DISALLOWED_TOOLS` denies, with `Bash(...)` rules
(`DISALLOWED_TOOLS`: a prefix rule, `Bash(git push:*)` and `Bash(gh:*)`, or a rule with a `*`
inside the command, `Bash(python3 *foundry_cli.py issue *)`, written for the two interpreter
spellings `python3` and `python`), `git push`, `gh`, and the Foundry verbs `issue`, `edit`,
`adr` and `campaign-approval approve` (other Foundry verbs that write, such as
`routing record-review-proof`, are not listed); the plugin
guard (`hooks/guard_bash.py`) also denies `gh pr create/merge`, a push to the default branch
and `campaign-approval approve` when the plugin is active, because `--plugin-dir` is passed.

### The native sandbox (PAT-220, PAT-ADR-0026)

`sandbox_settings` (`campaign_effects.py`) builds the `--settings` object with
`native_sandbox_settings` (`local_first_runner.py`, the PAT-124 sandbox, unchanged), with the
ticket worktree as attempt directory and the worktrees directory as work root. Sources are the
code and the tests `test_session_*` and `test_implement_session_runs_in_the_native_sandbox`
(`tests/test_campaign_effects.py`), which read the built settings and pin them; one live
`claude` session was run, once (trial of 2026-10-17, `docs/qualification/pat-220-sandbox-trial-2026-10-17.json`): `claude` 2.1.294,
`claude-haiku-5-5`, effort `low`, work root and data directory under the home, settings built by
`sandbox_settings` before the protection of `~/.gitconfig`, `~/.config/git` and `~/.claude.json`
below. PAT-124 (`CHANGELOG.md`, review round 3, trials of
2026-10-08) observed one OS write refusal outside the attempt directory with the launcher's
settings; it lists as "STILL not established" a work root under the home, the Edit rule,
Glob/Grep, the shared per-user temporary directory and the OS-level `denyWrite` on `.claude`
for a shell command (the trial note `pat-19-native-sandbox-trial-2026-10-08-t4.json` says the
same of a work root under the home). The default work root `<repo>-campaign` is under the home
(`default_worktrees_dir`). The trial answers, observed once: shell and Write-tool writes in a work root under the home
succeeded, and the attempted writes to the worktree `.claude`, the `.git` pointer and, through
the Write tool, the data directory were refused and left unchanged on the filesystem. Which
layer refused (the OS `denyWrite`, the `dontAsk` mode as in PAT-124 P15, or a permission rule)
was not recorded: unknown. Still unknown, not tested: the OS-level `denyWrite` inside an
`allowWrite`, Glob/Grep, the shared temporary directory, reads re-allowed inside the denied
home, the network, other hosts and versions.

- Shell commands run in the Bash sandbox (`enabled`, `failIfUnavailable` true,
  `allowUnsandboxedCommands` false): writes only in the worktree, in the per-user temporary
  directory (the host default, `native_sandbox_settings`) and in the git paths below. The file
  tools rely on permissions (mode `dontAsk`, `Read`/`Edit` allow rules on the worktree only).
- Denied to reads and writes, by sandbox and by `Read`/`Edit` rules: `registry.data_dir()`
  (the proof store `acceptance-proofs`, `campaign-approvals.jsonl`, `campaign-attempts.jsonl`,
  `review-remarks`), the directory and the file of `config.default_config_path()`, `~/.claude`
  and `~/.codex` when it exists. The sandbox also denies shell reads of the whole home, apart
  from what is re-allowed: the git common directory, `~/.gitconfig`, `~/.config/git`, and the
  entries under the home of `sys.prefix`, `sys.base_prefix` and of the `PATH` of the child
  environment (meant to let the tests find their interpreter: deduced, not observed, the trial
  ran no test; not covered: user site-packages such as `pip --user` under `~/Library/Python`,
  the symlink targets of `PATH` entries such as pipx or uv tool shims under `~/.local`, and
  pyenv shims dispatching to a version other than `sys.prefix`). The `Read`/`Edit` rules do not deny the
  home itself when the work root is under it, as the default `<repo>-campaign` is whenever the
  repo is (`native_sandbox_settings` drops a denied path that contains the attempt directory);
  the file tools then hold the rest of the home only by mode `dontAsk` and the absence of an
  allow rule (deduced). The worktree's `.claude` and `.git` pointer file, the denied paths
  above, `~/.gitconfig`, `~/.config/git` and `~/.claude.json` are also in `denyWrite` and in
  the `Edit` deny rules (reads of the first two stay allowed, so a commit finds its identity;
  `core.hooksPath` or `core.fsmonitor` there would run code outside the sandbox). That an
  `allowWrite` of the user scope cannot reopen them is deduced; a refusal that left the path unchanged
  was observed once (refusing layer not recorded) for the worktree `.claude` and `.git` pointer,
  for `~/.config/foundry`, `~/.claude` and the data directory (two shell writes and one
  Write-tool write; steps 3 to 8 and 12 of the trial of 2026-10-17, `docs/qualification/pat-220-sandbox-trial-2026-10-17.json`),
  not for the other paths nor for the three user files added after it. `--setting-sources user`
  keeps the worktree's project and local settings from widening the sandbox (arrays merge across
  scopes, PAT-124).
- Git in a linked worktree writes into the main repository's directory. `git_dirs` reads the
  common directory and the worktree's git directory (`git rev-parse`), and the sandbox
  allows writes to `objects`, `refs`, `logs` of the common directory and to the worktree's own
  git directory only (what a local `git commit` writes: observed once, a `git add` and `git commit` in a linked
  worktree under these settings made the commit, trial of 2026-10-17).
  `hooks`, `config`, `info`, `refs/replace` of the common directory and `commondir`, `gitdir`,
  `config.worktree` of the git directory are denied to writes, so the session cannot plant a
  hook there, change `core.hooksPath` or add a replacement object (observed once: a write into
  the common `hooks` and a `git config core.hooksPath` left the common directory unchanged;
  `commondir`, `config.worktree`, `refs/replace` were not tested). A relative
  `core.hooksPath`, such as the `.githooks` that `doctor` proposes, resolves in the worktree,
  which the session writes. So every subprocess started by `_run`, sessions excepted, gets
  `core.hooksPath=<hooks directory of the main checkout>` and `core.fsmonitor=false` through
  `GIT_CONFIG_COUNT`, appended after existing entries (`campaign_cadence.git_neutral_env`,
  applied by `_run`); the `issue`, `edit` and `routing` commands it runs pass it to their
  git (deduced: they set no environment of their own). `Runtime.hooks_dir` reads that directory
  once per `Runtime` (one `step` or `run` command), from `--root` (`Runtime.root`, the "main
  checkout" of this page), with `git rev-parse --path-format=absolute
  --git-path hooks`: with git 2.54.0 it gave a relative `core.hooksPath` joined to the main
  checkout's top level, an absolute one as is, a `~/` one expanded, `<common dir>/hooks` unset
  (observed once, by hand). The session does not write it when it is the main checkout's
  `.githooks`, the common `hooks` or a global directory outside the paths it writes (deduced
  from the settings above); one inside the worktrees directory
  (`git_hooks_dir_in_campaign_worktrees`), or a `git rev-parse` that fails or answers anything
  but one absolute path (`git_hooks_dir_unreadable`), makes the pass `refuse`
  `git_hooks_dir_refused` before any other git command (`_step`, the error code in
  `evidence.error`), while one in the temporary directory or in a
  git path the session writes is not refused (residual). The read-only git calls of
  `campaign_cadence._git` (`for-each-ref`, `rev-parse`, `rev-list`, which run no hook) keep
  `core.hooksPath=/dev/null`. Observed in `tests/test_campaign_effects.py`, one git version:
  under a relative `core.hooksPath`, a cadencer `git push` from a linked worktree ran the main
  checkout's `pre-push` hook, which refused it, and not the one planted in the worktree, which
  ran without these entries; a commit ran a `pre-commit` hook of a global absolute
  `core.hooksPath`. So the repository's own `pre-push` hook (the AGENTS.md#R1 guard and the
  check of `docs/commit-privacy.md`) and a global hooks directory, when enabled, run on the
  push of `issue openpr` (deduced from those tests; `issue openpr` itself was not run).
- `issue openpr` pushes the branch the worktree HEAD names (`openpr`, `issue.py`), and the session
  can move that HEAD (it writes the worktree's git directory, above). Before an `open-pr`, `review`
  or `defer-remarks` effect (`PUSH_KINDS`: each may run `issue openpr`) and, for the description
  correction (PAT-227), after its session and before its `issue openpr` (`publish_description`),
  `branch_mismatch` reads it with `git rev-parse --abbrev-ref HEAD`; when it is not the ticket's
  branch (`campaign_cadence._branch_for`) or is the default branch (`registry.default_branch`, else
  `main`), the pass returns `suspend` `worktree_branch_mismatch`, claims no attempt and runs no
  `issue openpr` (`test_worktree_head_on_the_default_branch_suspends_open_pr_without_a_push`); after
  a description correction session the attempt is already claimed, so it fails with `error`
  `description_worktree_branch_mismatch` and publishes nothing
  (`test_the_description_is_not_published_when_the_session_moved_the_worktree_head`). The check is
  point-in-time (residual, deduced from the code): `issue openpr` reads the HEAD again when it runs
  (`openpr`, `issue.py`, `rev-parse --abbrev-ref HEAD` then `git push`), after `branch_mismatch`,
  and nothing holds the worktree between the two, so a process that outlives the session (whether
  `claude` ends one it started is unknown) or the reviewer session, which runs in the same worktree,
  unsandboxed ("The reviewer session and its limit"), could move HEAD in that interval (whether the
  reviewer can, unknown: it is limited to `Read`, `Grep`, `Glob` and three read verbs in `dontAsk`
  mode, so moving HEAD needs the host to let through a command outside those rules); a test of the
  interval has not been written. Deduced from the code: in the `review` effect the reviewer session
  runs between `branch_mismatch` (before the claim) and the `issue openpr` of `update_pr_body`, so
  the interval is real for that effect.
- `.gitattributes` and `.lfsconfig` of the worktree are files the session writes (it holds the
  worktree) and that no code of the campaign reads or checks (searched, PAT-236). Deduced from git,
  not observed here: a `.gitattributes` can name a `filter`, `diff` or `merge` driver (and
  `textconv` is a key of a `diff` driver, set in the configuration) but the command of a driver
  comes from the configuration, which the sandbox protects (common `config`, `~/.gitconfig`), so it
  runs only a driver that configuration already defines. If `git-lfs` is installed and its
  `filter.lfs.*` configuration is set (usually by `git lfs install` in `~/.gitconfig`), its filter
  runs outside the sandbox in the git commands of the cadencer; if its `pre-push` and
  `post-checkout` hooks (the latter fired by `git worktree add`) are installed in the repository's
  hooks directory, they run there too, the push of `issue openpr` included. A `.lfsconfig` can
  change the endpoint it contacts. Unknown: whether `git-lfs` is installed on the machine that runs
  the cadencer, and what it would do here; not tested.
- Residual, unknown: if a `denyWrite` on the worktree `.git` pointer, or on `commondir` or
  `config.worktree` of the git directory (inside the allowed git directory), did not hold, the
  session could redirect the git directory to a repository of its own whose configuration
  names a command other than a hook or fsmonitor (for example `core.sshCommand` or
  `credential.helper`, for the push of `issue openpr`), and a later git command of the
  cadencer would run that code outside the sandbox (deduced). The pointer was refused in the trial; `commondir`, `config.worktree` and
  `refs/replace` were not tested.
- Residual, deduced: the `user` scope of the host settings can add `permissions.allow` rules
  (for example `Edit` or `Read` on the home) that the file tools would honour where no deny
  rule exists, which is most of the home when the work root is under it.
- Residual, deduced: the refs of every branch (and `refs/remotes`) are writable inside
  `refs`; the objects are shared by all worktrees; the user scope of the host settings still
  loads (`--setting-sources user`) and can add to the sandbox arrays.
- Stays outside the sandbox: the `claude` process itself (model API network, its own
  credentials and the user's keychain access, `--plugin-dir` reads), the plugin hooks it
  runs, and the network of the shell commands, which the sandbox policy governs and these
  settings do not open (deduced from the settings, which set no network key; not observed).
  A credential kept outside the home or in the keychain is not covered by the read rules.
- Residual, observed from the settings (reads are re-allowed on `<common>/config`, `~/.gitconfig`
  and `~/.config/git` so commits have an identity): the session can read a credential those
  files hold (token in a remote URL, `http.extraHeader`, `url.*.insteadOf`), and its final summary
  becomes the body of the pull request. Measure: `campaign_effects.mask_credentials` runs in
  `_save_summary`, before the summary is stored, so before `issue openpr --summary-stdin`
  and before the review sees it; it masks URL userinfo (`https://user:token@host`), the value of
  an `Authorization` / `Proxy-Authorization` header and a few token shapes (GitHub, GitLab,
  Linear, Slack, `sk-`) with `[REDACTED]` (unit test, observed). Unknown: a credential of
  another shape (a bare password, a token without a known prefix outside a URL or header) is
  not masked; the session's own files and commit contents are not filtered.

The remaining limits, stated: `DISALLOWED_TOOLS` denies, with the prefix rules and the rules
with an inner `*` described above, `git push`, `gh`, and the Foundry verbs `issue`, `edit`,
`adr` and `campaign-approval approve`; a spelling outside those rules (`git -C . push`, an
absolute path to `git` or `gh`, `python3.12 foundry_cli.py issue`) is not denied by them
(deduced from the rule text), and with the network of the shell not opened such a push has no
route (deduced). The sandbox, not these rules, is what keeps the session from writing the
review-proof store, the approval journal and the attempt journal. Whether the host matches
the mid-command wildcards of `DISALLOWED_TOOLS` is not verified here (unknown); the tests pin
the argv, not the host's matching.

## Provider requests of a pass (PAT-228)

`step` records the provider requests of its pass in `StepResult.requests`, printed under
`requests` of each step by `step` and `run`: `observation` (the tracker counter,
`Tracker.request_count`, read before and after `cadence.observe`), `effect` (the same counter
over the rest of the pass, that is the reads made in this process by the plan and the effect,
the deferral included) and `subprocesses` (the sum of the `foundry linear_requests=N` lines
that the Foundry subcommands of the pass printed on standard error, read by `_run` into
`Runtime.subprocess_requests`; they need `FOUNDRY_REQUEST_COUNT=stderr`, and sessions
do not receive it, `_CLAUDE_CHILD_ENV_ALLOWLIST`). A figure that is not counted is `null`: the
counter exists on the Linear adapter only (`LinearTracker.request_count`, the PAT-145 counter
of the process); `Tracker.request_count` returns `None` elsewhere. A pass that stops before the
observation (`attempts_journal_invalid`, `git_hooks_dir_refused`) has `requests` `null`. For an
effect, the same object is the `requests` key of its `finished` line. `run` and `step` also print
a top-level `requests` (`request_summary`): `observation` (observations of all the passes), `wait`
(the part of it spent on passes whose outcome is `wait-ci` or `waiting`) and `effects` (`effect` plus
`subprocesses`). Measured figures: `docs/linear-request-cost.md`, "Epic campaign (PAT-228)".

The counts are held in memory and printed once, when `run` or `step` ends (`cli`). A pass
that runs no effect writes no journal line, so the `requests` of a wait pass (`wait-ci`,
`waiting`) exist only in that output: if `run` is interrupted (signal, crash, closed
terminal), they are lost with the whole output. Only the `requests` of an effect pass
survive, in its `finished` line.

## The attempt journal

Append-only JSONL, `campaign-attempts.jsonl` in `registry.data_dir()` (`journal_path`, the
directory of `campaign-approvals.jsonl`), mode 0600, written under an exclusive `flock`
(`claim`, `finish`). Two kinds of line (`_KEYS`):

- `started`, written before the effect: `attempt`, `epic`, `ticket`, `kind`, `fingerprint`,
  `session_id`, `route`, `at`.
- `finished`, after it: `attempt`, `epic`, `ticket`, `kind`, `status` (`completed` or `failed`),
  `exit_code`,
  `cost_usd`, `duration_ms`, `summary_file`, `branch_head`, `error`, `at`; and, for a
  `review` only, the optional closed key `review` (`REVIEW_KEYS`): `verdict` (`blocked` or
  `validated`), `fix_before_merge`, `non_blocking`, `diff_hash`, `base`, `head`, `carried`,
  `partial`, and the optional `fix_description` (PAT-227: how many of the `fix_before_merge`
  remarks target the description; absent reads as 0); for a `correct` that replaced the PR
  description, the optional closed key `description` (`{head, base}`, `valid_description_field`).
  Every `finished` line may carry the optional closed key `requests` (PAT-228,
  `valid_requests_field`, `REQUEST_KEYS`): `observation`, `effect`, `subprocesses`, see "Provider
  requests of a pass" below. A journal written before PAT-218, PAT-227 or PAT-228 stays valid
  (`_OPTIONAL`).

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

"Implementation finished" is observable through this journal: the last `finished` line of kind
`implement` or `correct`, `completed`, whose `branch_head` is the current branch head
(`last_implementation`), makes the decision `open-pr` when there is no PR and the branch has commits
beyond the remote default branch, and `open-pr` `correction_not_pushed` when a PR is open, its head
is not the branch head and the PR head is an ancestor of the branch head
(`campaign_cadence._ticket_step`; `branch_contains_pr`, observed by `_git_contains` with one
read-only `git merge-base --is-ancestor <PR head> <branch head>`, only when the two heads differ).
When it is not (the PR head moved on the remote only, the two diverged, or the PR head is unknown to
the local repository: the three read `False`), the pass suspends `pr_head_not_in_branch` instead of
pushing; that a push would then be refused by the remote is deduced, not run here. A session that
commits nothing leaves the fingerprint unchanged, so the next `implement` is refused as a replay.

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
   marker line `FOUNDRY_REVIEW_RESULT_V1` (`RESULT_SENTINEL`) and one JSON object. `parse_result`
   accepts the keys `outcomes` (non-empty list of `{id, digest (64 hex), verdict}` with `verdict` in
   `pass`, `fail`, `not_covered`, `contradicted`), `quality` (`mergeable` or `blocked`),
   `fix_before_merge` and `non_blocking` (integers from 0 to `MAX_REMARK_COUNT`), `remarks`
   (PAT-141: a list of at most `MAX_REMARKS` entries `{text, fix_before_merge, target}`, `text`
   non-empty and at most `MAX_REMARK_TEXT` characters, `fix_before_merge` a boolean, `target`
   (PAT-227) `diff` or `description` (`review_remarks.TARGETS`), required from the reviewer; the
   number of `true` entries must equal `fix_before_merge` and the number of `false` entries
   `non_blocking`; `review_remarks.validate_remarks`; a receipt written before PAT-227 has no
   `target` and reads as `diff`), a non-empty report before the marker, and refuses `mergeable` with
   a verdict other than `pass`. The only optional key is `full_review` (PAT-237, `FULL_REVIEW_KEY`:
   a boolean, `true` when a partial review read the whole diff; see "Partial review and proof
   carry"); any other key is refused. Anything else makes the attempt `failed`
   (`reviewer_output_invalid`) and records no proof.
5. The report (the text before the marker, `claim_id` and token replaced by `[redacted]`) is saved
   with `_save_summary`; the list-form file `{"outcomes": [...], "quality": ...}` (exactly those two
   keys: `record-review-proof` refuses any other shape, `routing.py`) is written 0600 and `routing
   record-review-proof --root --base --issue --diff-hash --claim-id --outcomes-file` records the
   proof; the issue criteria are re-read there, so ids and digests that differ from the issue's are
   refused (attempt `failed`, `proof_refused`, claim left active, `campaign-reviews/<attempt>/` kept
   for a human; recovery is `routing recover-review`, a human decision,
   `skills/review-pr/SKILL.md`). The other failures after a granted claim, `claim_malformed`,
   `prepare_refused`, `reviewer_session_failed` and `reviewer_output_invalid`, return the same way:
   `launch_review` has no call that releases the claim, and `campaign-reviews/<attempt>/` is removed
   only once a proof is recorded, so it is kept for them too. The claim stays active in the review
   ledger, and an unchanged diff already claimed whose verdict is unavailable stops the workflow for
   recovery (`docs/model-routing.md`), so the way out is the same human `routing recover-review`;
   for `claim_malformed` the claim identifier may be unreadable, and whether recovery is then
   possible is unknown. The text of each remark is scrubbed like the report before the receipt
   (`scrub_remarks`, PAT-237: `claim_id` and token replaced by `[redacted]`, the credential shapes
   of `mask_credentials` by `[REDACTED]`, the text cut back to `MAX_REMARK_TEXT`), and
   `comment_text` of the deferral masks the credential shapes again, so that a receipt recorded by
   hand is not trusted either. After a recorded proof of a validated round (quality `mergeable`),
   `routing record-review-remarks --root --base --issue --diff-hash --proof-id --remarks-file`
   records the receipt of the remarks (`_record_remarks`; section "The remarks receipt"); a refusal
   leaves the verdict recorded and sets the detail `remarks_receipt_not_recorded`. Then the
   directory (claim, packet, outcomes and remarks files) is removed.
6. PAT-ADR-0022 asks the PR description to say that a partial review took place and that the proof
   relies on the previous round. `update_pr_body` runs `issue openpr <ID> --summary-stdin` on the
   open PR (`_reuse_or_open_pr` replaces the body when a summary is supplied, `issue.py`), with the
   last saved summary among the completed implementation and description corrections
   (`body_summary`, after the criterion above) and a `## Review rounds` section rebuilt from the
   journal (`round_line`: full, partial or carried, verdict, short head sha, and for a partial round
   the sentence on the reliance; a round is recorded partial only when its review was a partial one
   and its result does not carry `full_review`). It is a Foundry command, not a session, and is made
   after a carried proof too. A refusal leaves the verdict recorded and sets the detail
   `pr_body_not_updated`. Without a completed implementation or description correction in the
   journal the body is the rounds section only; with one whose summary file is missing, unreadable
   or unusable it is `SUMMARY_UNUSABLE_BODY` and the section (`body_summary`). After a description
   correction (below) the section also holds one line per correction (`description_lines`).

The `finished` line carries the verdict (`review` key) and `summary_file` (the report). The
step output (`effect.review`, `effect.report_file`) exposes the verdict, the number of remarks
marked "fix before merge" and the number of other remarks. The text of each remark is in the
receipt, not in the journal.

### The remarks receipt (PAT-141, PAT-ADR-0028)

`routing record-review-remarks` (`routing.py`) loads the proof by its identifier with
`AcceptanceProofStore.validated_proof` and writes `review-remarks/<sha256 of the repository
identity>/<proof id>` under `registry.data_dir()` (`ReviewRemarksStore`, `review_remarks.py`;
file 0600, directory 0700, written once). The receipt holds `proof_id`, `issue`, `diff_hash`,
`base`, the `generation` of the proof's review and the list of remarks. It is refused when the
proof is not `mergeable` or when the issue, diff hash or base differ from the proof's; an
identical replay is accepted, a different receipt for the same proof is refused. The proof is
not written or changed. `issue merge` never reads the receipt: neither `issue.py` nor `write.py`
refers to `review_remarks`, and `AcceptanceProofStore.valid_for_merge` and `current_for_sync`
do not either (`tests/test_campaign_defer.py`, `test_the_merge_gate_never_reads_the_receipt`).
A carried proof (PAT-ADR-0021) has no receipt of its own: the deferral of the original diff
hash is looked up by that hash.

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
`routing.py`; FOUNDRY-ADR-0006); `tests/test_campaign_review.py` asserts the argv against that
resolution. The budget is `--budget-usd` (the same value as an implementer session); timeout
`REVIEW_TIMEOUT_SECONDS`; the child environment is that of the implementer session
(`_claude_child_environment`) plus `FOUNDRY_DATA` when it is set (`plan_review`): the read verbs
find the claim under `registry.data_dir()`, which reads that variable (`registry.py`); without it,
deduced, they would look in the default directory. The implementer session does not receive it.
`--setting-sources user --strict-mcp-config --mcp-config` (the flags are listed by the installed
`claude --help`; the last two are used by `campaign_runtime.py`) keep the `.claude/settings*.json`
of the worktree, which the diff or an earlier session may have written, and any project MCP server
out of the reviewer session; the `session_argv` of the implementer session carries the same three
flags (`session_argv`, `campaign_effects.py`). Unlike `campaign_runtime.py`, which passes
`--no-session-persistence`, no session of `campaign_effects.py` (implementer, correction, reviewer)
carries it (PAT-237, 218-14), so its transcript persists: `_parse_session` refuses an output that
does not echo the `--session-id` passed, and the routing hook reads the session transcript to
observe the host version (AGENTS.md#R7). Whether `--no-session-persistence` with `--session-id`
still echoes that id, or leaves such a transcript, is unknown: `campaign_runtime.py` uses both and
never checks it, and no paid call was made to observe it. The prompt (`review_prompt`, standard
input) carries the reviewer contract of `skills/review-pr/SKILL.md` (stages, output, marker), the
exact `read-review` command, and the send text written by `prepare-review`, followed by a sentence
saying that the output contract overrides that text (which asks to load `review-pr` and to return
its JSON; `review_packet.py` is unchanged, for the manual workflow); it carries `claim_id` because
the reviewer needs it to read the diff, and the argv of the session does not.

Why `dontAsk`: a tool call that no rule pre-approves is refused without a prompt, so with the
`--allowedTools` list above the session can only read files, search, and run the three
read verbs (deduced). That a mode refuses what no rule approves is
established for `dontAsk` by the local-first runner (`local_first_runner.py`, `NATIVE_PERMISSION_MODE`,
trials recorded in `CHANGELOG.md`, PAT-124); `--tools` is used by
`campaign_runtime.py`; `--allowedTools` is named in `local_first_runner.py` (a refused flag there). Not run here: whether the host
matches the `Bash(...)` wildcard rules as written, or refuses a compound command that starts
with an allowed prefix, and whether `Read` of the packet directory is allowed through
`--add-dir` (unknown). Remaining limit: the `user` scope is still loaded, so the permission
rules of the user's own settings apply to the reviewer, and its `Read` reaches any file the OS
user can read. The reviewer is not in the native sandbox: `reviewer_argv` carries no
`--settings` (tested), because its three read verbs take the review-deduplication lock
(`ReviewDeduplicator._locked`, `routing.py`, `.ledger.lock` under `review-dedup/` of the data
directory), which the sandbox denies. It runs as the same OS user as the cadencer, in the
ticket worktree, as a discipline boundary: it does not hold the `record-review-proof` verb in
its rules (the coordinator records the proof, as in the manual workflow).

## The correction effect

For a `correct` decision with reason `review_blocked`, `review_fix_before_merge` or
`remarks_not_deferrable` (`campaign_review.plan_correct`, before the attempt is claimed; reason
`review_fix_description`: see "The description correction" below):

1. The report saved for the exact PR head and base is read from the journal; without one (for
   example a proof recorded by hand) the pass fails at the plan phase and nothing is launched.
2. For a blocking review only, `routing escalation failure <ID> implementer --kind review_blocking`
   (`review_blocking_after_fix` only when the blocked PR head is the `branch_head` of a completed
   `correct` attempt: "a correction was reviewed and blocked again", `model-routing.md`)
   `--current-tier <routed tier without the escalation floor> --idempotency-key
   campaign-<EPIC>-<ID>-<kind>-<head> --root <worktree> --base <PR base sha>`. It runs before the
   claim because its answer decides whether and at which tier the session runs; the key and the tier
   sent are stable (the floor is not added: `record_failure` raises the tier to it; when no model is
   available at the tier without the floor, `_tier_without_floor` sends the tier resolved with the
   active floor instead of failing the plan, PAT-237; a replay whose first signal was sent at
   another tier is then refused by the escalation command and the plan fails with `échec d'escalade
   non enregistré`, deduced from `record_failure`), so a replay after `escalated` sends the same
   signal and writes nothing more (`escalation.py`, `record_failure`). Before it, `plan_correct`
   applies the refusals of `claim` (`refusal`: attempt already made, session ceilings), so a signal
   is not recorded for an attempt the claim would refuse. `human_required: true` (the command also
   exits 2) returns `suspend` `human_required`; `action: technical_blocked` returns `suspend`
   `technical_blocked`; any `action` outside `CONTINUE_ACTIONS` (`failure_recorded`, `escalated`,
   `remediation_continued`; for example `authority_ambiguous`) returns `suspend` with the action as
   reason; no session is launched and no attempt is claimed. Otherwise the session runs at the tier
   of `final_tier`. A validated review with remarks marked "fix before merge" records no escalation
   failure (`skills/review-pr/SKILL.md`: a non-blocking verdict touches no escalation state).
3. One fresh implementer session (`session_argv`, the argv of "The implementation session")
   whose prompt (`correct_prompt`) carries the issue and the saved report, asks to fix only the
   blocking findings, or only the remarks marked "fix before merge", or (reason
   `remarks_not_deferrable`) every non-blocking remark, and to follow
   AGENTS.md#R9(c): change as few sentences as possible and re-check every new sentence
   against the code. Except for `remarks_not_deferrable`, the other non-blocking remarks are
   left untouched: the coordinator defers them.
4. The session commits locally only. The next pass sees the branch ahead of the PR head and decides
   `open-pr` `correction_not_pushed` (`suspend` `pr_head_not_in_branch` when the PR head is not in
   the branch, see "The attempt journal"): `issue openpr <ID>` pushes the branch and reuses the open
   PR without replacing its body (`_reuse_or_open_pr`, `issue.py`). That the `review` transition
   accepts a repeat on a ticket already in `review` is not exercised here (unknown). A new head has
   no proof, so the pass after that decides `review`.

## The description correction (PAT-227)

Observed 2026-10-17 (record cited above): a remark marked "fix before merge" that targeted only
the PR description got a correction session that made no commit, so the fingerprint of the next
pass was unchanged and the pass suspended `attempt_already_made`; a new review of the same diff
was not possible (deduced: the review ledger dedups by diff hash, `routing claim-review`).

`decide` (`campaign_cadence._ticket_step`) returns `correct` `review_fix_description` when the
validated review of this head and base has remarks marked "fix before merge" and all of them
target `description` (`TicketFacts.fix_description` equals `fix_before_merge`, from the
`fix_description` key of the `review` line), and no correction has yet replaced the description
for this head and base (`description_corrected`, from the `description` key of a completed
`correct` line, `campaign_review.description_facts`). If any marked remark targets `diff`, the
decision stays `review_fix_before_merge`. It counts as a correction (`MAX_CORRECTIONS`,
`corrections_exhausted`).

The effect (`plan_correct`, `_launch`, `publish_description`) runs one implementer session with
`description_prompt`: no file change, no commit, and a final message that is the whole new
description, starting with `FOUNDRY_SUMMARY_V1`. A usable message replaces the PR body through
`issue openpr <ID> --summary-stdin`, with the `## Review rounds` section and a line saying the
description was corrected per the remark and not re-reviewed because the diff is unchanged; the
`finished` line then carries `description: {head, base}` and the next pass goes on (deferral, CI
wait, merge) as for a validated round with nothing left to fix. A session that fails, an unusable
message (`error` `description_summary_unusable`) or a refused `issue openpr` (`error`
`description_not_published`) fails the attempt; the next pass then suspends
`description_correction_not_settled` (not `attempt_already_made`). Before the session,
`plan_correct` compares the commit the worktree HEAD names (`worktree_head_sha`, one `git rev-parse
HEAD`) with the PR head: if they differ it suspends `description_worktree_head_moved` (evidence
`head` and `pr_head`) with no attempt claimed and no session launched; if the read fails the plan
fails (`phase: plan`) and no attempt is consumed (PAT-237;
`test_a_worktree_head_that_is_not_the_pr_head_launches_no_paid_session`,
`test_an_unreadable_worktree_head_is_its_own_failure_before_and_after_the_session`). Before
publishing, `publish_description` also refuses a worktree HEAD that is not the ticket branch
(`description_worktree_branch_mismatch`, `branch_mismatch`), one whose commit differs from the PR
head observed before the session (`description_session_moved_head`: the session must commit nothing)
and one it cannot read (`description_head_unreadable`: a failed read, not a commit of the session);
nothing is pushed or published (`test_the_description_is_not_published_when_the_session_committed`).
The attempt is `failed`, so no `open-pr` follows. The next pass decides `review_fix_description`
again: with the same fingerprint (HEAD on another branch, or an unreadable HEAD) it suspends
`description_correction_not_settled`; after a session that committed, the branch head, hence the
fingerprint, changed, but the worktree HEAD is not the PR head, so the pass suspends
`description_worktree_head_moved` before any new session until a human brings the worktree back to
the PR head; the local commit stays unpushed.

Residual risk: a description corrected this way is not re-reviewed (deduced from the dedup by
diff hash), and the merge gate never reads the PR description (`issue merge`, `write.py`).
Whether the new description settles the remark is not checked by any gate.

## The defer-remarks effect (PAT-141, PAT-ADR-0028)

Decided by `campaign_cadence._remarks` after a validated review of the exact PR head and base
and before the CI wait: `deferrable` is the `non_blocking` count of that review (the remarks not
marked "fix before merge"). It comes before the `review_fix_before_merge` correction: that
correction changes the head, and the review of the new diff hash need not list the remarks again.
When they cannot be deferred, the `correct` `remarks_not_deferrable` decision corrects every
remark, the marked ones included.

| Facts (`DeferralFacts`) | Decision |
| --- | --- |
| no deferrable remark, or the deferral is `done` for this diff hash | none: the pass goes on to CI and `merge` |
| no origin Epic (the direct parent of the ticket is absent or not an `Epic`; the chain of parents is not walked up) | `correct` `remarks_not_deferrable`, cause `no_origin_epic` |
| the origin Epic is itself a companion Epic (its title starts with `Nits `): no second-level deferral | `correct` `remarks_not_deferrable`, cause `companion_epic` |
| the last attempt ended in a durable refusal | `correct` `remarks_not_deferrable`, cause `refused` |
| the last attempt ended transient and `DEFER_RETRY_SECONDS` have not passed | `wait` `deferral_transient_failure` |
| the last attempt ended transient, the wait is over | `defer-remarks` again (a new attempt: the identity carries the `try` count) |
| `MAX_DEFER_TRIES` attempts made for this diff hash | `suspend` `deferral_retries_exhausted` |
| otherwise | `defer-remarks` `non_blocking_remarks` |

The origin Epic and the companion test are read from the tracker only when deferrable remarks
remain (`origin_of`: `get_issue` of the ticket and of its parent). The effect (`run_deferral`)
launches no session and holds no ceiling of `max_sessions`. Its intent is the `started` line of
the attempt journal, written before the first write (`claim`); the `finished` line carries the
`deferral` key `{state, follow_up, companion, reason, diff_hash, remarks, writes, unknown}`
(`DEFERRAL_KEYS`; `state` is `done`, `refused` or `transient`; `remarks` the number of deferred
remarks; `writes` what this attempt wrote; `unknown` the creations whose effect is unknown, each
`{title, client_id}`). The `foundry linear_requests=N` lines that `FOUNDRY_REQUEST_COUNT=stderr` makes the Linear
adapter print last (`REQUEST_COUNT_ENV`, `trackers/linear.py`) are dropped from every error tail the cadencer reads
(`without_request_count`, `campaign_effects.py`), so the recorded `reason` and the `reconciliation` JSON are those of
the error (observed: campaign PAT-221, first attempt of PAT-222, deduced cause: the last line was taken); `companion`
of a failed attempt is the companion Epic found or created before the failure, `writes` what had gone through
(`launch`, `campaign_defer.py`). Steps, each preceded by a read of the tracker, so that a retry duplicates nothing
that an earlier attempt of this journal or the project listing shows (limit in step 3):

1. Read the receipt for the ticket and the diff hash; absent, or a deferrable count that differs
   from the journal's: durable refusal (`remarks_receipt_missing`, `remarks_receipt_inconsistent`).
2. Find the companion Epic: an issue of type `Epic` titled `Nits <ORIGIN-EPIC-ID>`. First the
   companions that earlier deferral attempts of this Epic journaled (`journal_memory`, the
   `companion` of their `deferral` field, most recent first) are read by identifier (`get_issue`)
   and the first that is still an Epic with that title is kept; otherwise the project is listed
   (`Listing`, `tracker.search`, at most once per attempt, then `get_issue` where `type` is
   absent). Before PAT-228 the project was listed here and again in step 3 when the companion had no unstarted child. More than one in that listing: durable refusal
   `companion_epic_ambiguous` (a companion kept from the journal is not compared with other Epics
   of the same name). None: `edit create-issue` with the field `Type: Epic`, state
   `backlog`, no `parent`; no link to the origin Epic is made.
3. Find the follow-up issue: the unstarted child (`backlog` or `ready`) of the companion Epic
   with the lowest identifier; a started or closed child is never used. Otherwise an issue titled
   `Review remarks deferred from <ORIGIN-EPIC-ID>` in `backlog` or `ready` with no parent is an
   orphan of a creation whose parent link failed: write 4, `edit link <FOLLOW-UP> subtask-of
   <COMPANION>`. This scan reads the listing of step 2; it is skipped (`_find_follow_up`
   `scan_orphans`) only when step 2 did not need the listing (the companion came from the
   journal) and no earlier attempt of this Epic can have created a follow-up (`journal_memory`:
   none wrote `create follow-up issue`, none has `unknown` creations, none lacks a `deferral`
   field on its `finished` line). Residual risk: `journal_memory` reads the journal of this campaign Epic only
   (`epic`, not the origin Epic), so an orphan follow-up of the same origin Epic created outside it is not seen
   in that case and a second follow-up can be created. Otherwise `edit create-issue` with `parent` set to the companion Epic.
4. One `edit comment <FOLLOW-UP>` carrying every deferrable remark, the pull request number,
   the ticket and the review round, and the reference `<ticket>@<first 16 hex of the diff
   hash>`: at most one comment per validated round, never one per remark (a correction
   changes the diff hash, so a later validated round of the same pull request posts its
   own). A remark whose text equals one of the deferrable remarks of the receipt of an
   earlier `done` deferral of the same ticket (another diff hash) is left out
   (`already_deferred`, PAT-237: the partial review that follows a correction may list it
   again); when nothing is left, no comment is posted and the `deferral` field carries
   `remarks: 0`; the attempt then makes no deferral read or write in the tracker (no
   companion lookup, follow-up or comment: `run_deferral` returns before step 2), while step
   5 still runs and the field names the follow-up and companion of the latest earlier `done`
   deferral. The comparison is the exact text: a reworded remark is deferred again, and two
   distinct findings with the same text (for example `nit: typo`) are taken for one, so the
   second is neither deferred nor fixed (known limit). The comment is also skipped when a
   comment of the follow-up already contains that reference (a comment whose answer was
   lost). The tracker may return only the most recent comments (`docs/review-rounds.md`): a
   comment older than that window would be posted again (unknown).
5. `update_pr_body` rewrites the description with the line `Deferred remarks: <FOLLOW-UP-ID>[,
   <FOLLOW-UP-ID>...] (companion Epic <ID>, created by this pull request|already existing; <N>
   remarks; writes: ...)` after the `## Review rounds` section (R9 (b): the writes are reported
   in the PR description). It names every follow-up issue and every write of the attempts of the
   pull request, those of a transient or refused attempt included; the line is kept by the later
   rewrites (`deferral_lines`, from the `deferral` fields of the journal). After a durable
   refusal the same rewrite carries `Remarks not deferred: durable refusal (<reason>) (writes
   made before it: ...); they are corrected before the merge.`, the second parenthesis only when
   an attempt of the pull request made a write.
6. The `finished` line with state `done`; the next pass decides `merge`.

A failed `edit create-issue` is never retried inside the pass. When its standard error is the
structured partial-effect object of `IssueCreationValidationError` (`reconciliation:
required`), or is a quota error whose message names a `client_id=<UUID>`, `edit
reconcile-create <issue ID, else client id> <spec>` runs (read-only) at once; `validation:
accepted` adopts that issue. `_reconcile` tells four answers apart: `accepted`; `absent`
(standard error naming `IssueUnavailableError`, the read found no such issue: the creation did
not happen, nothing is kept in `unknown`, transient `creation_not_observed`, and the next pass
creates again with a new client id); `refused` (standard error naming `TrackerConflictError` or
`validation refused`, or `validation: refused` in the answer or in the creation error object:
the adapter does not replay, `creation_replay_refused`, a durable refusal); anything else is
unreadable and the effect stays unknown: the client id and the title go to the `unknown` key of
the `finished` line (transient), and the next pass reconciles by that client id (`_create`)
before any new creation. The ADR only says a creation is retried after reconciliation
(PAT-ADR-0028); this split is the reading of R9 (b) (`tests/test_campaign_defer.py`).
The bound is `MAX_DEFER_TRIES`. The read of steps 2 and 3 still precedes
the creation and adopts an issue it finds; a companion that the journal names but that was
renamed or retyped since is not trusted (the listing decides). An error object that names neither an issue nor a
client id is a durable refusal (`creation_effect_unreconcilable`).

Classification of a failure (`classify`, `_DURABLE`): an error text that names a capability
unavailable, a binding refusal, a `ValueError`, `SystemExit`, `type_unmapped`,
`invalid_field_options` or an unknown type is a durable refusal; any other error (quota, network,
an unknown cause) is transient. Durable refusal: the `finished` line is `completed` with state
`refused`, no substitute write is made, the next pass decides `correct` and the corrected head
is reviewed again. Transient: the `finished` line is `failed` with error `transient` and the
outcome of the step is `waiting` (not `failed`; the exit code stays 0); `run` sleeps
`--poll-seconds` and observes again. That the patterns match what each real adapter raises is
not exercised here: only the Linear class names are known from the code, the YouTrack and
GitHub Projects texts are not verified (unknown). Constants `DEFER_RETRY_SECONDS` (60) and
`MAX_DEFER_TRIES` (5) are chosen and not measured.

The companion Epic and its follow-up have no link to the Epic of the campaign: the neutral
subgraph (`build_neutral_subgraph`) follows `parent-of` from the campaign's Epic only, so the
structure digest the approval covers and the closure graph (`bounded_epic_graph_snapshot`)
are unchanged by the deferral (`tests/test_campaign_defer.py`).

## Bounds

Constants `MAX_REVIEW_ROUNDS` (3) and `MAX_CORRECTIONS` (2) in `campaign_cadence.py`, chosen and not
measured (unknown), counted per ticket from the attempt journal (`campaign_review.history`): the
recorded, non-carried `review` verdicts, and the `started` `correct` lines. A `review` decision at
three rounds returns `suspend` `review_rounds_exhausted`; a `correct` decision at two corrections
returns `suspend` `corrections_exhausted` (also for remarks to fix: they are never merged over).
`decide` stays pure: the counts are facts of the observation (`TicketFacts`). These two are not the
only suspensions: the others are `human_required`, `technical_blocked` and any other `action`
outside `CONTINUE_ACTIONS` (correction effect), `deferral_retries_exhausted`,
`description_correction_not_settled`, `description_worktree_head_moved`, `attempt_already_made`,
`attempts_journal_invalid`, `ci_failing_not_executed`, `worktree_branch_mismatch`, and those of
`campaign_cadence.decide` (`node_unreadable`, `structure_changed`, `dependency_state_behind`,
`several_in_progress`, `merged_pr_state_behind`, `pr_closed_unmerged`, `pr_head_not_in_branch`,
`pr_ambiguous`, `pr_head_unreadable`, `no_eligible_ticket`). The approval ceilings (`refuse`) also
apply.

## Partial review and proof carry

Decided from the journal by `campaign_review.review_mode`, for the review attempt:

- `partial` (PAT-ADR-0022, `AGENTS.md#R9`): the last recorded round is `validated` and the last
  correction session ended on exactly the head now reviewed. The prompt says so, and
  `--declarations-file` carries the previous verdict, the previous report (at most
  `MAX_PREVIOUS_REPORT_BYTES`) and the commit range `<previous head>..<head>` to the packet as
  unverified coordinator assertions (`docs/review-packet.md`); the reviewer returns a verdict on all
  criteria for the exact diff and may ask for a full review with a reason: a reviewer that read the
  whole diff adds `"full_review": true` to its result (PAT-237), the round is then recorded
  `partial: false` and `round_line` calls it a full review. Without that key the round stays
  partial, whatever the reviewer read (`tests/test_campaign_review.py`,
  `test_a_partial_review_the_reviewer_widened_is_recorded_as_a_full_round`). The prompt
  (`review_prompt`) says that the remarks to correct of the previous round are those marked "fix
  before merge" or, when none of them could be deferred (`remarks_not_deferrable`), every
  non-blocking one, and asks not to list again a remark the correction was not asked to settle, so
  that a later deferral does not carry it a second time. A round after a blocking review is always
  `full`. The proof is the same as a full one and relies on the previous round for the unchanged
  parts; no mechanical check tells them apart (`review-rounds.md`).
- `carry` (PAT-ADR-0021, `review-rounds.md`, "Carrying a terminal proof"): the last round is
  validated with no remark to fix, no correction since, and only the base differs. The effect
  runs `routing carry-review-proof --issue --root --base <new base> --from-base <old base>
  --from-diff-hash <old diff hash>` first; `AcceptanceProofStore.carry` checks the conditions
  (ancestry, clean merge tree, no path changed by both, identical diff bytes) and a refusal
  makes the same attempt go on with a full review. A carried review is recorded with
  `carried: true`, is not a review round and launches no session.
- Anything else is `full`.

## Not executed yet

Correcting a red CI; ADR
selection for the reviewer packet; a Codex session; closing the Epic; the replay of a failed
attempt with the same fingerprint. A failed `implement` session that committed changes the
branch head, so the next pass launches a new session with a new identity: retries are
bounded only by `max_sessions` and `max_sessions_per_issue` of the approval (`refusal`: the
`started` lines of `SESSION_KINDS` of the Epic, over every run, then of the ticket). Each
session is launched with `--max-budget-usd` set to the `--budget-usd` of the run that launches
it (`session_argv`; a review session carries it through `reviewer_argv`, and a review counts
in the ceiling although a carried review launches nothing), so the worst-case spend is at most
the sum of those per-session values, that is at most `max_sessions` times the largest
`--budget-usd` used, itself at most `MAX_BUDGET_USD` (25.0), provided `claude` enforces the cap
(whether it can be exceeded is not verified here: unknown). The absolute bound is
`max_sessions` times `MAX_BUDGET_USD`. `defer-remarks` and `start` launch no session. Tests: `tests/test_campaign_effects.py` (one effect per subprocess, the `claude`
argv, the allowlisted environment, no replay including after a crash, ceilings, merge only on
a `merge` decision) and `tests/test_campaign_review.py` (the review path, the reviewer argv,
strict parsing, escalation, the correction prompt, bounds, partial review, carry, secrets, and
a two-ticket Epic with a blocking first review that reaches `await-verdict` with every proof
produced by a double of the reviewer session), `tests/test_campaign_defer.py` (the deferral
effect: companion Epic and follow-up created once, reuse, started follow-up, ambiguity,
durable refusal and transient wait, reconciliation, orphan attach, the neutral subgraph and
the closure graph unchanged, the merge gate) and `tests/test_review_remarks.py` (the receipt
and the strict parsing of `remarks`).

R5 status (PAT-237): updated `docs/campaign-effects.md` and `docs/campaign-cadence.md`. Candidate
artefacts: the optional key `full_review` of the reviewer result (`FULL_REVIEW_KEY`); the decision
reason `pr_head_not_in_branch`, the key `branch_contains_pr` of an observed ticket and the
`contains` argument of `observe`; the suspension `description_worktree_head_moved` and the error
`description_head_unreadable`; the helpers `scrub_remarks` and `already_deferred`; the public
constant `RESULT_KEYS` (`campaign_review.py`); the sentence of AGENTS.md and CLAUDE.md (R9) on the
comment of the deferral, now "at most one comment per validated round". No CLI verb or option,
configuration key or routing table changed. Code behaviour changed without a new constant: remark
texts scrubbed before the receipt and the tracker comment, the tier sent to the escalation command
when the tier without the floor is unavailable, a remark deferred once is not deferred again (and an
attempt with nothing left touches no tracker), the wording of the partial-review prompt.

R5 status (PAT-236): updated `docs/campaign-effects.md`, `docs/model-routing.md` and
`AGENTS.md`/`CLAUDE.md` R7 (the headless runners that apply
`claude_headless_host_version_requirement()`: three, and the working directory of its `--version`
call). Candidate artefacts: the `Bash(...)` rules of `DISALLOWED_TOOLS`, the refusal
`git_hooks_dir_refused` and its two causes, the keys `epic`, `ticket`, `kind` of a `finished` line,
the `requests` counts of an interrupted run, the worst-case spend, the residues of the branch check
and of `.gitattributes`/`.lfsconfig`. Code behaviour changed without a new constant, CLI verb or
option, configuration key or routing table: `--worktrees-dir` resolved, summary cut on a character
boundary, an undecodable summary file published as `SUMMARY_UNUSABLE_BODY`, git directories read
before the claim, and the `claude --version` probe run in the directory of the session
(`session_plan`).

R5 status (PAT-218): updated `docs/campaign-effects.md`, `docs/campaign-cadence.md`,
`docs/review-rounds.md`, `AGENTS.md`/`CLAUDE.md` R9 ("What is not coded yet"), `README.md`.
Candidate artefacts: new constants `MAX_REVIEW_ROUNDS`, `MAX_CORRECTIONS`, `REVIEWER_TOOLS`,
`RESULT_SENTINEL`, `SESSION_KINDS`; new decision reasons `correction_not_pushed`,
`review_fix_before_merge`, `review_rounds_exhausted`, `corrections_exhausted`, `human_required`,
`technical_blocked`, `ci_failing_not_executed`; the optional journal key `review`; the step output
keys `effect.review` and `effect.report_file`; the step outcome `awaiting_review_execution`
(PAT-217: a `review` or `correct` decision stopped the run) is removed (commit `1a1bacf`, PAT-218:
such a decision is now executed). No CLI verb or option, configuration key or routing table changed.

R5 status (PAT-141): updated `docs/campaign-effects.md`, `docs/campaign-cadence.md`,
`docs/review-rounds.md`, `skills/merge-pr/SKILL.md`, `AGENTS.md`/`CLAUDE.md` R9 ("Inside an Epic
campaign"). Candidate artefacts: the CLI verb `routing record-review-remarks` and its options;
the `remarks` key of the reviewer result; the decision kinds `defer-remarks` and `wait`; the
reasons `non_blocking_remarks`, `remarks_not_deferrable`, `deferral_transient_failure`,
`deferral_retries_exhausted`; the step outcome `waiting`; the constants `DEFER_RETRY_SECONDS`,
`MAX_DEFER_TRIES`, `MAX_REMARKS`, `MAX_REMARK_TEXT`; the optional journal key `deferral`; the
receipt directory `review-remarks`. No configuration key or routing table changed.

R5 status (PAT-228): updated `docs/campaign-effects.md`, `docs/campaign-cadence.md`,
`docs/linear-request-cost.md`, `docs/review-rounds.md`, `AGENTS.md`/`CLAUDE.md` R9 ("Inside an Epic
campaign") and `docs/qualification/pat-219-epic-campaign-2026-10-17.md`.
Candidate artefacts: the optional journal key `requests` and its keys (`REQUEST_KEYS`), the
`requests` field of a step and of a run report, `Tracker.request_count` and
`LinearTracker.request_count`, `Runtime.subprocess_requests`, `request_summary`, and the new
helpers `Listing` and `journal_memory` of the deferral. No CLI verb or option, configuration key
or routing table changed.

R5 status (PAT-227): updated `docs/campaign-effects.md`, `docs/campaign-cadence.md` and
`docs/review-rounds.md`.
Candidate artefacts: the `target` key of a remark (reviewer result, receipt, `routing
record-review-remarks --remarks-file` help); the decision reason `review_fix_description` and
the suspension `description_correction_not_settled`; the constants `SUMMARY_MARK`,
`SUMMARY_UNUSABLE_BODY`, `TARGETS`, `DESCRIPTION_KEY`; the optional journal keys
`fix_description` (in `review`) and `description`; the errors `description_summary_unusable`,
`description_not_published`, `description_worktree_branch_mismatch` and
`description_session_moved_head`. Also updated: the sentence of
`AGENTS.md`/`CLAUDE.md` R9 ("Inside an Epic campaign") on what the correction loop re-reviews,
which the description correction made partly false. No CLI verb or option, configuration key or
routing table changed.
