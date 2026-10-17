# PAT-198: native qualification of the eight rollback lines of PAT-17 (protocol)

Status: sections 1 to 8 were written 2026-10-17, BEFORE any native call, and are kept as that protocol. The four
Claude lines were then run once each on 2026-10-17 and the Codex lines were not: see section 9 (amendment) and the
matrix [`pat-198-native-rollback-2026-10-17.json`](pat-198-native-rollback-2026-10-17.json). Whoever fills it records
what a run showed, never what was expected.

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
`--projects-dir`, `--max-budget-usd`, `--guard-policy`, `--dry-run`). Not changed, with reason: `claude_profile_trial`
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
