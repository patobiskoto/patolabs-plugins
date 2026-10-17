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
`max_turns: 3`, `allowed_tools: [Skill]`, no tracker and no Bash. Each prompt is written
from the `USE WHEN` trigger of the skill (`skills/<skill>/SKILL.md`, `description`).

The only grader is `type: tool_used` on the `Skill` tool, with an `input_match` regular
expression on the `skill` field, namespace optional. `tests/test_skill_evals.py` checks that
each `prompt.md` frontmatter uses only the keys it lists in `ALLOWED_PROMPT_KEYS` and that
each grader type is one of `DETERMINISTIC_GRADERS` (`regex`, `tool_used`, `tool_order`,
`file_exists`): the `llm` and `baseline` types are refused by that test because they call a
judge model.

## Run it

```text
python3 scripts/skill_evals.py --dry-run                 # plan and exact argv, nothing launched
python3 scripts/skill_evals.py --max-cost-usd 2          # launch (terminal only)
python3 scripts/skill_evals.py --read evals/results/skill-evals.json   # re-read a result
```

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

`interpret` reads `aggregate-result.json` as written by the 2.1.302 binary (deduced from the
field names it contains: `schemaVersion` 1, `partial`, `partialReason`, `costUsd`,
`claudeVersion`, `cases[].aggregates.score`, `cases[].arms.with[].graders`). Per case:

- `pass`: score at or above the suite `threshold` (1.0), no run error, run not partial;
- `regression to examine`: score below the threshold. Look at the trace and the skill
  description first; the cause may be the skill, the prompt, the model or noise (with
  `runs: 1`, one sample, no variance estimate). The launcher prints no promotion, removal or
  change;
- `unknown`: partial run (cost ceiling, interruption, credential rejected), run error, case
  absent, or an unreadable or unexpected `schemaVersion`. Never counted as pass.

Exit codes of the launcher: 0 all pass, 1 at least one regression, 2 unknown, 3 launch refused.
Those of `claude plugin eval` itself are not used for the verdict.

## Hooks during an eval

Deduced, not observed: the plugin hooks run during an eval (`hooks/hooks.json`), but they do not
fail these cases. `route_agent.py` and `observe_agent.py` match `Agent|Task` only and
`guard_bash.py` and `guard_routed_readonly.py` match `Bash` only, and no case uses those tools.
`session_start.py` stays silent when the working directory has no registry entry
(`registry.entry_for`), which is expected in an isolated working directory.

## Unknown

- Whether a run bills the subscription or API credits.
- The default judge model is `haiku` (`--judge-model` in the help); no case uses a judge, so
  none should be called. Not observed.
- Whether `Skill` calls appear in the trace under `foundry:<skill>` or `<skill>`: the
  `input_match` accepts both; no run has been made.
- Codex: `claude plugin eval` is a Claude Code command; no equivalent is provided.
- No eval has been run when this page was written: it is not shown that the five prompts
  make the model invoke their skill.
