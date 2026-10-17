# PAT-198: native qualification of the eight rollback lines of PAT-17 (protocol)

Status: sections 1 to 8 were written 2026-10-17, BEFORE any native call, and are kept as that protocol. The four
Claude lines were then run once each on 2026-10-17 and the Codex lines were not: see section 9 (amendment) and the
matrix [`pat-198-native-rollback-2026-10-17.json`](pat-198-native-rollback-2026-10-17.json). Whoever fills it records
what a run showed, never what was expected. Section 10 (2026-10-18) is the amendment written before the Codex calls;
it withdraws the Codex usage-limit statement of sections 3 and 9, kept below as written.

## 1. What this is and is not

PAT-17 ([record](pat-17-config-rollback-2026-10-11.json), [decisions](../model-decisions-2026-10-11.md),
[migration](../model-migration-2026-10.md)) showed that eight rollback configurations resolve through the CLI and
build plans, with zero native calls (`evidence_kind: configuration_only`, `native_calls: 0`). That is configuration
compatibility. It says nothing about whether the account can still run those models. PAT-198 asks one question per
line: when this line is launched natively once, what did the host actually run?

Not a benchmark, not a comparison between lines or with the current models, not a promotion, not a statement about
quota or cost. An old model that does not answer is declared unavailable for this account on this date. Nothing is
substituted, no baseline is invented, no default is changed by a result.

## 2. Scope: the eight lines (configurations to examine, availability NOT asserted)

| Line id | Role (tier) | Model / effort | Surface |
| --- | --- | --- | --- |
| `codex-scout` | scout (economy) | `gpt-5.6-luna` / `low` | `codex exec` |
| `codex-implementer` | implementer (balanced) | `gpt-5.6-terra` / `medium` | `codex exec` |
| `codex-reviewer` | reviewer (frontier) | `gpt-5.6-sol` / `high` | `codex exec` |
| `codex-architect` | architect (apex) | `gpt-5.6-sol` / `max` | `codex exec` |
| `claude-scout` | scout (economy) | `haiku-4.5` / none (`not_applicable`) | routed Agent launch |
| `claude-implementer` | implementer (balanced) | `sonnet-5` / `medium` | routed Agent launch |
| `claude-reviewer` | reviewer (frontier) | `opus-5` / `high` | routed Agent launch |
| `claude-architect` | architect (apex) | `fable-5` / `high` | routed Agent launch |

Source of the eight pairs: `ROLLBACK` in `tooling/foundry/rollback_native_trial.py`, equal to the `rollback` of each
row of the PAT-17 record (checked by `tests/test_rollback_native_trial.py`).

## 3. Surface

Claude: the surface PAT-125 used (`tooling/foundry/claude_profile_trial.py`, reused as-is). One headless `claude -p`
parent loads this checkout (`--plugin-dir`), is told to call the Agent tool once with the logical role
(`foundry:lupin`, `foundry:eiffel`, `foundry:maigret`, `foundry:vauban`), and the `PreToolUse` routing hook rewrites
that call to the pinned profile named in section 5. The fixture lives outside any git repository and carries its own
disposable project policy mapping only the role's tier to the line's model and effort. The child environment is the
R6 allow-list (`AGENTS.md#R6`), never under `sandbox-exec`. The child reads one public marker file and returns it.

Codex: `codex exec --ephemeral --ignore-user-config --ignore-rules --sandbox read-only --json --model <model>
-c model_reasoning_effort="<effort>"`, packet on standard input (the argv shape of the measurement harness,
`measurement_harness_v2.py`, plus `--skip-git-repo-check -C <work>` in the plan of `codex_plan`). The coordinator cannot run
Codex before 2026-10-23 (usage limit, deduced from the maintainer's statement, not observed here), and the tool does
not implement a real Codex run: `--dry-run` prints the plan, a real Codex run is refused. Codex rows stay `not_run`
until a later step adds and exercises that run; this page then gets a dated amendment before any Codex call.

## 4. Authorised budget, ceilings, stops

- Budget (maintainer, standing authorisation recorded in PAT-198): at most eight paid native calls, one per line,
  no retry. The matrix field `native_calls` counts them; the tool refuses past 8.
- One call per line: the tool refuses a line whose row is not `not_run`. It claims the row (`status: started`)
  BEFORE the call, so a killed, failed or timed-out run also blocks a second one. A new work directory or result path
  does not bypass this. Two further guards do not rest on the matrix of one checkout. An exclusive lock file
  `<matrix>.lock` (created with `O_EXCL` next to the matrix before it is loaded) and a second one,
  `pat198-native-rollback-ledger.jsonl.lock`, next to the ledger below (taken right after it, so it is common to every
  matrix copy), are both held from before the checks to the end of the run and removed then. A second launch that finds
  either file, on the same matrix or on another copy of it, is refused before any check or launch; a lock left by a
  killed run also refuses, and only the maintainer removes it. A
  reservation ledger, `pat198-native-rollback-ledger.jsonl` in the Foundry data directory (`registry.data_dir()`, outside
  any checkout, append-only, one line per reservation, written before the call; its location follows `FOUNDRY_DATA`, else the default data directory), refuses a line already reserved even if
  the matrix was copied, reverted, stashed or read from another worktree, and refuses past eight lines. Retrying a line
  needs a new maintainer decision, a hand edit of the matrix and of the ledger, recorded here. `FOUNDRY_DATA` must stay the same
  (or unset) for all eight runs: the ledger guarantee holds only then, since another value points at another ledger
  (each result records `ledger.path` as `<data_dir>/<name>` and `ledger.data_dir_from`, no absolute path). Run all
  lines from one checkout, one after the other, and commit the matrix right after each run.
- Per-call spend ceiling: `--max-budget-usd <AMOUNT>` is passed to `claude -p` (the flag exists in the installed host's
  `--help`, read 2026-10-17; whether it covers the child's spend is unknown). The tool refuses a real run without it.
  `<AMOUNT>` is the maintainer's figure; none is fixed here.
- Time ceiling: 20 minutes, the process group is killed (`TIMEOUT_SECONDS`). One parent, one child expected, no
  concurrency. More than one child is `not_conforming`, even with an unavailability text.
- Stop conditions: any line whose host answer is a refusal, an unavailable model or a divergent model is recorded
  and the sequence goes on with the next line only if the coordinator decides so; nothing is retried or replaced.
  Stop the whole sequence on: an authentication failure (OAuth), a usage-limit message, or a real-policy digest
  change (`policy_guard: CHANGED`). Run the Claude lines one after the other, never in parallel.

## 5. The exact command per Claude line

From the root of the checkout (scratch outside any git repository; `<AMOUNT>` from the maintainer):

```
S=$(mktemp -d)
PYTHONPATH=plugins/foundry/tooling python3 -m foundry.rollback_native_trial --line claude-scout \
  --work-dir "$S/claude-scout" --out "$S/claude-scout.json" --max-budget-usd <AMOUNT> --dry-run
```

Remove `--dry-run` for the real call. Same with `--line claude-implementer`, `claude-reviewer`, `claude-architect`.
Expected pinned profile and wire model per line (computed offline by the dry run, `claude_invocation_binding`):

| Line | Profile | Wire model | Effort transmitted |
| --- | --- | --- | --- |
| `claude-scout` | `routed-readonly-none-haiku-4.5` | `claude-haiku-4-5` | none (`not_applicable`) |
| `claude-implementer` | `routed-worker-medium-sonnet-5` | `claude-sonnet-5` | `medium` |
| `claude-reviewer` | `routed-readonly-high-opus-5` | `claude-opus-5` | `high` |
| `claude-architect` | `routed-readonly-high-fable-5` | `claude-fable-5` | `high` |

The command works on a checkout whose HEAD is committed and clean (the result records `source.commit` and `dirty`,
read before the lock and the claim are written, so `dirty: true` means something other than this run changed the
checkout; committing the matrix after each run keeps the next one clean).

## 6. What a run writes, and restoration

Written by a real run, and only these:

1. the work directory (outside the repository): fixture, raw stream `stream.ndjson`, `stderr.txt`;
2. the result file `--out` (schema `foundry.pat198-native-trial.v1`);
3. the row of its line in the matrix, plus `native_calls` (a first write before the call, a second after it), the
   lock files `<matrix>.lock` and `<ledger>.lock` during the run, and one line in the reservation ledger (Foundry data directory);
4. host-side session logs under the host's own projects directory (written by the host; the tool only reads them).

A dry run writes nothing. The user's real policy files are never touched: the fixture carries its own policy and the
tool never writes a policy anywhere else. To make that checkable, the tool takes the sha256 of each guarded policy
file (always the `.foundry/model-routing.json` of the checkout holding the module, the only routing policy Foundry reads
(`routing.py`; there is no user-level one); `--guard-policy` adds files and never replaces it, and a worktree run does
not guard the main checkout's policy unless it is passed) before and after the call, `absent` for a missing file, and records both. A difference makes the line `not_conforming` and the
row `policy_guard: CHANGED`. Restoration therefore needs no action when the guard says `unchanged`. If it says
`CHANGED`, restore the file from its backup or from version control and note it here. A recorded or reserved row is
never undone without a maintainer decision (section 4). Delete the work directory when done (it holds a raw stream).

## 7. Observed against unknown

Four values stay separate, per line, in the result and the matrix:

- resolved route: the policy value the fixture resolved to (`requested`), computed offline;
- transmitted parameters: the profile, wire model and effort the checkout selects (`transmitted`), computed offline,
  plus the hook's own statement when the host kept it in the parent log (`hook`);
- native observation: models from the child's assistant records, agent type from the child's `.meta.json`, efforts
  only if the host logs `effort`/`perTurnEffort`, host version from the logs. Absent = `unknown`. For `haiku-4.5`
  an absent effort never proves non-applicability, and a logged `effort: null` counts as absent; if the host logs a
  real effort for it (for example the parent's, inherited), it differs from the expected none and the effort is
  `divergent`, so the line is `not_conforming` (current behaviour; whether the host does so is unknown);
- account availability: `available_observed` only when the child's model was observed (exact or dated snapshot);
  `unavailable_declared` only when no real model was observed and a masked host text (parent error, child API-error
  record, parent final reply, stderr) matches a model-unavailable pattern (a heuristic, `_UNAVAILABLE`, each phrase tied to the word "model"; the texts and the
  parent's reply are always kept so a reader can overrule it); otherwise `unknown`. A child record with model
  `<synthetic>` or `isApiErrorMessage` is read as a host API error, never as a model (that record shape is deduced from
  Claude Code, not observed here).

Fixture compliance (`fixture_compliance`: the parent's reply carries the marker) is reported apart and never decides the
verdict, which rests on the resolved, transmitted and observed model and effort. Role contracts expected to refuse
the trivial read-the-marker task: `maigret` certainly (its method STOPs without `diff_hash`, `claim_id`, `root`, `base`),
so `claude-reviewer` is expected `divergent` on that point; `vauban`, `lupin` and `eiffel` do not STOP on a missing
coordinate (`agents/`), so `claude-architect`, `claude-scout` and `claude-implementer` are expected to comply, but
`vauban` is scoped to an architectural question and what it does with this task is unknown.

Verdicts: `conforming` (everything exact), `not_conforming` (any divergent value, wrong process exit, several
children, changed guard), `unavailable` (only when nothing but the process exit diverges; a divergent profile or several children is
`not_conforming`, and a divergent profile sets the account availability back to `unknown`), `unknown` (anything not observed, a dated snapshot included). Errors are kept
verbatim after masking: secrets, e-mail addresses, opaque tokens of 32 characters or more, home directory, 2000
characters at most.

Known limits, stated before any run: one call is not a sample; the role contracts of `maigret` and `vauban` may
constrain the fixture task (what the host does then is recorded, not predicted: unknown); the fixture task reads one
file, so it exercises launch and model identity, not review or architecture quality; the budget flag's coverage of
the child is unknown.

## 8. Documentation status (R5)

Updated: this page (new), the matrix file (new), `model-routing.md` (pointer), `changelog.d/PAT-198.md`, and the new
module `foundry.rollback_native_trial` (CLI `--line`, `--work-dir`, `--out`, `--matrix`, `--claude`, `--parent-model`,
`--projects-dir`, `--max-budget-usd`, `--guard-policy`, `--dry-run`). PAT-245 adds `--codex` and `--codex-sessions-dir`
and the Codex run (section 10; `model-routing.md` pointer, `changelog.d/PAT-245.md`). Not changed, with reason: `claude_profile_trial`
(imported helpers only, no surface changed); the PAT-17 record, the release notes and published changelog sections
(frozen historical proofs, left untouched).

## 9. Runs (2026-10-17), amendment written after the calls

Four native calls, one per Claude line, run one after the other by the coordinator from one checkout, the matrix
committed after each run (`native_calls: 4`). Per-call ceiling `--max-budget-usd 1`; total authorised by the
maintainer for these four lines: 4 USD. `FOUNDRY_DATA` unset for all four (each result records
`ledger.data_dir_from: default`). Guarded policies: the `.foundry/model-routing.json` of the checkout and, through
`--guard-policy`, that of the main checkout; both `absent` before and after every call (`policy_guard: unchanged`).
Host observed: Claude Code 2.1.302. Costs are the `total_cost_usd` of each parent stream (observed; whether it
covers the child is unknown, section 4).

| Line | Verdict | Observed model / effort | Availability | Fixture | Cost (USD) |
| --- | --- | --- | --- | --- | --- |
| `claude-scout` | `unknown` | `claude-haiku-4-5-20251001` (dated snapshot) / `unknown` (not logged) | `available_observed` | `exact` | 0.116 |
| `claude-implementer` | `conforming` | `claude-sonnet-5` / `medium` | `available_observed` | `exact` | 0.132 |
| `claude-reviewer` | `conforming` | `claude-opus-5` / `high` | `available_observed` | `exact` | 0.149 |
| `claude-architect` | `conforming` | `claude-fable-5` / `high` | `available_observed` | `exact` | 0.193 |

Total about 0.59 USD. Observed against section 7: `maigret` did not refuse the trivial task, so `claude-reviewer`'s
`fixture_compliance` is `exact`, not the predicted `divergent`; the cause is unknown. The four Codex lines were not
run (`not_run`, the reason in each row's `limits`): the tool does not run a real Codex call, and Codex is under a
usage limit until 2026-10-23 (maintainer's session log, not observed here). The remaining four calls of the standing
authorisation are left to the follow-up issue PAT-245.

## 10. Codex lines (2026-10-18), amendment before the Codex calls

Written BEFORE any Codex call (PAT-245). Sections 1 to 8 stand; this section fixes only what the Codex lines add. The
remaining four calls of the standing authorisation (4 of 8) are the four Codex lines, one each, no retry.

**Correction.** Sections 3 and 9 say Codex is under a usage limit until 2026-10-23. That came from a stale
2026-10-13 session log and is withdrawn. A `codex app-server` `account/rateLimits/read` of 2026-10-18 showed the weekly
window at about 1 to 4 per cent used (the coordinator's reading; this page's author did not repeat it). Whether a
call succeeds stays unknown until it is made; a usage-limit text in a run is a spent call and stops the sequence.

**Surface and argv.** `codex exec` (local `codex-cli 0.162.1`), from a fresh work directory outside any git
repository, packet on standard input from `packet.txt` of the work directory:

```
codex exec --ignore-user-config --ignore-rules --sandbox read-only --json --skip-git-repo-check -C <work>
  --model <model> -c model_reasoning_effort="<effort>" -
```

That is `codex_command_v2` of `measurement_harness_v2.py` (`--ephemeral --ignore-user-config --ignore-rules --sandbox
read-only --json --model <model> -c model_reasoning_effort="<effort>" -`) with two differences, both deliberate:
`--skip-git-repo-check -C <work>` (the work directory is not a repository), and NO `--ephemeral`, because
`--ephemeral` persists no session file and the session file is the only source of the model and effort that the host
recorded (below). The cost: the host writes a session file under its own sessions directory (host side, like the
Claude project logs); the tool only reads it. That the host writes only that file is unknown: it may also write logs,
state, or a refreshed token in the authentication file under `CODEX_HOME` (deduced, not observed; the tool reads,
hashes and copies none of it). The packet asks to read `fixture/marker.txt` (a public marker) and reply
with it; no Foundry routing, role contract or policy is in this path, the model and effort are carried by the command
line, so `resolved_route` and `transmitted` are the same pair and the "disposable policy" is `--ignore-user-config
--ignore-rules` plus an empty work directory.

**Isolation of the account.** The child keeps the real Codex home for authentication only (`--ignore-user-config`:
"Do not load `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME`", `codex exec --help`). Its environment is the R6
allow-list of the Claude child (`HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `TMPDIR`, `USER`) plus `CODEX_HOME` if set;
no API key or other variable is forwarded, so authentication through an environment key would not work (the local
session is by the Codex home, deduced, not tested). The tool never reads, hashes or copies the authentication file;
it records the sha256 of `<CODEX_HOME>/config.toml` before and after (`policy_guard`, with the guarded policies of
section 6), and a change makes the line `not_conforming`. Before the call a present `config.toml` is copied to
`<work>.backup/config.toml`, a sibling outside the `-C` directory the model runs in, which must not exist and must be
outside any git repository like the work directory (never printed, never in the result). If the guard reports `CHANGED`, copy that file back
over `<CODEX_HOME>/config.toml`, check the sha256 against the `before` digest of the result, and note it in the
"Runs" amendment; `auth.json` is never copied, so a change of it has no restoration here (stop the sequence). Delete
the work and backup directories afterwards (the copy may hold the user's settings). A `codex` binary that is not found or not
executable is refused before the reservation and the matrix write.

**Ceilings and stops.** One call per line, no retry; the same matrix claim, matrix lock, ledger lock and
out-of-checkout ledger as section 4, shared with the Claude lines (total 8). `codex exec` has no spend flag (none in
`codex exec --help`, read 2026-10-18): the ceiling is the time bound of section 4 (20 minutes, process group killed)
and the usage the stream reports; `--max-budget-usd` does not apply and is recorded as `null`. Stop the sequence on an
authentication failure, a usage-limit or rate-limit text (`usage_limit_text`), or a changed guard. An unavailable,
divergent or refused answer is recorded and nothing is retried or replaced.

**Observation (what is observed, what stays unknown).**

- Stream (`--json`): `thread.started` (the thread id locates the session file and is never written to the result),
  `item.completed` of type `agent_message` (the reply), `turn.completed` with `usage` (integer counters kept as
  reported), `turn.failed` / `error` messages (kept after masking). The event names are those parsed by
  `measurement_harness_v2.py`; the tool was not run against a real stream before this amendment.
- Session file (`<CODEX_HOME>/sessions/**/rollout-*-<thread id>.jsonl`): `turn_context` records `model` and `effort`,
  `session_meta` the `cli_version`. Their shape was read on a local session of 2026-10-18 (keys only). A model and an
  effort count as observed ONLY if the stream shows `turn.completed`; otherwise they are kept apart as
  `unconfirmed_models` and the line has no observed model. Exactly one session file must match, else unknown.
- Unknown by construction: whether `codex exec` retries HTTP or stream requests internally within the one call ("no
  retry" holds at the level of this tool only); whether the backend served the model the host recorded; the effort level `max` on
  `gpt-5.6-sol` (if the host records another label, the effort is `divergent` and that is the result); account
  availability beyond this date; any rate-limit state (not read from the session file).
- Account availability: `available_observed` only with an exact or dated-snapshot model from a completed turn;
  `unavailable_declared` only when no real model was observed, the stream shows no `turn.completed`, no
  usage-limit text is present, and a masked text matches a pattern anchored on the requested model identifier
  (for example `gpt-5.6-luna` ... `does not exist`), not on the word "model": a local refusal of
  `model_reasoning_effort`, or a completed turn whose session file is missing or ambiguous, stays `unknown`;
  otherwise `unknown`. Verdict rules are those of section 7 (profile and children do not apply: `agent_types`
  is `not_applicable`).

**Commands.** From the root of the checkout, scratch outside any git repository, tool `foundry.rollback_native_trial`,
`<line>` one of `codex-scout`, `codex-implementer`, `codex-reviewer`, `codex-architect`:

```
S=$(mktemp -d)
PYTHONPATH=plugins/foundry/tooling python3 -m foundry.rollback_native_trial --line <line> \
  --work-dir "$S/<line>" --out "$S/<line>.json" [--dry-run]
```

Options added for Codex: `--codex` (binary, default `codex`), `--codex-sessions-dir` (default
`<CODEX_HOME or ~/.codex>/sessions`). A real run writes the same items as section 6 (work directory with `fixture/`,
`packet.txt`, `stream.ndjson`, `stderr.txt`; result file; matrix row and `native_calls`; locks; one ledger line) and
the host's session file. Run the four lines one after the other, commit the matrix after each, and add a dated
"Runs" amendment after the calls, as section 9 did for Claude.
