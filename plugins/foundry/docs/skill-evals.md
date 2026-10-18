# Skill-selection evals (PAT-184)

A small suite replays five Foundry skills on fixed prompts and checks, without any model
judgement, that the model invoked the expected skill. It is run on demand by the operator, with
a budget the operator authorises first. It is a regression signal, not a gate: nothing here
promotes, removes or changes a skill, a model or a routing policy.

It builds no runner. It uses the `claude plugin eval` command of Claude Code (observed:
`claude plugin eval --help` on 2.1.302). Codex is not covered (see "Unknown").

## What is replayed

`evals/<case>/prompt.md` plus `evals/<case>/graders/skill-invoked.md`, one case per skill:
`open-pr`, `merge-pr`, `start-issue`, `intake`, `resume-issue`. Each case has `runs: 1`,
`max_turns: 1` and `allowed_tools: [Skill]`; no case needs a tracker or Bash. Each prompt is written
from the `USE WHEN` trigger of the skill (`skills/<skill>/SKILL.md`, `description`).

The only grader is `type: tool_used` on the `Skill` tool, with an `input_match` regular
expression (`"skill"\s*:\s*"(?:[\w-]+:)?<skill>"`) matched against the JSON serialisation of the tool
input, namespace optional; `tests/test_skill_evals.py` runs it on serialised inputs. `tests/test_skill_evals.py` checks that
each `prompt.md` frontmatter uses only the keys it lists in `ALLOWED_PROMPT_KEYS` and that
each grader type is one of `DETERMINISTIC_GRADERS` (`regex`, `tool_used`, `tool_order`,
`file_exists`): the `llm` and `baseline` types are refused by that test because they call a
judge model.

## Run it

```text
python3 scripts/skill_evals.py --dry-run                 # plan and exact argv, nothing launched
python3 scripts/skill_evals.py --max-cost-usd 2          # launch (terminal only)
python3 scripts/skill_evals.py --read <result file>      # re-read a result, launch nothing
```

`--out <path>` sets the result file passed as `--json` (default `DEFAULT_OUT`, `skill-evals.json`
in a `foundry-skill-evals` folder of the system temporary directory, `tempfile.gettempdir()`);
`main` deletes that file before a launch, so a stale result is never read. The default is outside
`evals/` on purpose: `claude plugin eval` reads that folder, what it would do with an extra
sub-folder without `prompt.md` is unknown, and from an installed plugin the folder is a cache.
`tests/test_skill_evals.py` checks that `DEFAULT_OUT` is outside the plugin. The path is predictable: where the temporary directory is shared (typically `/tmp` on Linux), another local user could pre-create that folder or a symlink; on macOS `TMPDIR` is per user (not tested here). Pass `--out` in a private folder in that case.

The launched command is built by `build_argv` in `scripts/skill_evals.py`:
`claude plugin eval <plugin dir> --model <model> --ablation none --runs 1 --no-publish
--json <out> --max-cost-usd <amount> --trust-plugin`. The default model is `DEFAULT_MODEL`
(`haiku`, a short alias); `--model` changes it. Whether the eval command accepts that alias is
unknown.

Budget: the maintainer, as the operator, authorises the amount. The launcher refuses without
`--max-cost-usd`, refuses under an agent-session marker (`CLAUDECODE`, `CODEX_*`, same markers as
`campaign_approval.agent_markers`) or without a terminal, and launches only once the amount is
retyped on `/dev/tty` (`confirm_budget`). An agent session therefore cannot launch it by a
cooperative call; a deliberate bypass (unsetting the marker, calling `claude plugin eval`
directly) is not stopped. Per `claude plugin eval --help`, the ceiling is a list-price estimate
checked before each run starts and a run already started finishes, so one run in flight (default
concurrency 1) can exceed it. How far is unknown.

## What the result means

`interpret` reads the file written by `--json` (observed 2026-10-17: `--read` classified its five
cases, so it has the shape below; that it is the tool's `aggregate-result.json` is deduced, not
observed). Fields read: `schemaVersion` 1, `partial`, `partialReason`, `costUsd`, `claudeVersion`,
`cases[].aggregates.score`, `cases[].arms.with[].error` and `cases[].arms.with[].graders[].passed`
(observed: the run error text and the grader flag appeared in that run). Also read but not shown by
that observation: `suite.threshold` and `graders[].name`; their presence is deduced from the field
names of the 2.1.302 binary and from `tests/fixtures/skill_evals_*.json`, which are written by hand
here, not produced by the tool. Per case:

- `pass`: score at or above `PASS_THRESHOLD` (1.0, `scripts/skill_evals.py`) and every grader
  `passed`, no run error (one exception, below), run not partial. A lower `suite.threshold` in the
  result never relaxes this; a higher one applies;
- `regression to examine`: score below the threshold. Look at the trace and the skill
  description first; the cause may be the skill, the prompt, the model or noise (with
  `runs: 1`, one sample, no variance estimate). The launcher prints no promotion, removal or
  change;
- `unknown`: partial run whose score reaches the threshold (cost ceiling, interruption, credential rejected; a partial
  run below the threshold is a regression to examine, `_case_verdict`), run error, case
  absent, no run recorded, no score (absent or NaN), no suite case found (no `prompt.md`), a score at or above the
  threshold without every grader passed, or an
  unreadable result, unexpected `schemaVersion` or unexpected shape. Never counted as pass.

The one run error that does not make a case `unknown`: `Reached maximum number of turns (N)`
(`MAX_TURNS_ERROR`), when every grader of the case passed. Observed 2026-10-17 (see "Observed run"):
every run of a `max_turns: 1` case ended on `exit 1: Reached maximum number of turns (1)` after its
grader passed. Cause: deduced, the single turn is spent on the `Skill` call. Any other error text,
and this one with a failed or missing grader, stays `unknown`.

Exit codes of the launcher: 0 all pass, 1 at least one regression, 2 unknown (a crash included:
`claude` not startable, unreadable prompt frontmatter, unexpected result shape), 3 launch refused.
Those of `claude plugin eval` itself are not used for the verdict.

## Observed run

One run, 2026-10-17, authorised by the maintainer (ceiling 2 USD), `main` at `e493b75`, Claude Code
2.1.302, `--model haiku --ablation none --runs 1`; the figures below were reported by the
coordinator of PAT-184 and the raw result file is not kept in this repository. Reported cost
0.0153 USD (as `costUsd`, a list-price estimate, not a bill), 12 s, `partial: false`. For each of
the five cases the `skill-invoked` grader passed (score 1): on `haiku`, each prompt triggers its
skill. Each run also carried the max-turns error above, which the earlier launcher read as
`unknown`. One run per case: a sample of one, no variance estimate.

## Hooks during an eval

Deduced, not observed: the plugin hooks run during an eval (`hooks/hooks.json`), but they do not
fail these cases. `route_agent.py` and `observe_agent.py` match `Agent|Task` only and
`guard_bash.py` and `guard_routed_readonly.py` match `Bash` only, and the cases allow only `Skill`
(unless a loaded skill reopens Bash, which is unknown: see the "Unknown" bullet on `allowed-tools`;
`guard_bash.py` would then run).
`session_start.py` stays silent when the working directory has no registry entry
(`registry.entry_for`), which is expected in an isolated working directory.

## Unknown

- Whether a run bills the subscription or API credits.
- The default judge model is `haiku` (`--judge-model` in the help); no case uses a judge, so
  none should be called. Not observed.
- Whether `Skill` calls appear in the trace under `foundry:<skill>` or `<skill>`: the
  `input_match` accepts both; the observed run does not say which.
- Whether a loaded skill's own `allowed-tools` reopens Bash inside an eval run is unknown. All
  five target skills declare `Bash(python3:*)`, four add `Bash(git:*)`, and `open-pr`, `merge-pr`
  and `resume-issue` add `Bash(gh:*)` (`skills/<skill>/SKILL.md`), so a case could reach `git` or
  `gh` with the operator's credentials. Deduced, not observed: `max_turns: 1` leaves the model no
  turn after the `Skill` call that the grader checks (consistent with the max-turns error observed
  on every run; it does not show that no tool ran in that turn).
- Codex: `claude plugin eval` is a Claude Code command; no equivalent is provided.
- Other models than `haiku`, other versions of Claude Code, and runs beyond one per case: not
  observed.
- Whether `max_turns: 2` would remove the max-turns error without reopening Bash: not tried
  (it would be a paid run); the launcher accepts the error instead.
