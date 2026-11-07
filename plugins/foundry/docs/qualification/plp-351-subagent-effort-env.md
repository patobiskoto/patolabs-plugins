# PLP-351: `CLAUDE_CODE_SUBAGENT_EFFORT` and routed subagent effort

Status: observation recorded; detection shipped. Detector: `detect_claude_overrides`
(`tooling/foundry/routing_facades.py`, constant `CLAUDE_SUBAGENT_EFFORT_OVERRIDE`); the
warning is `HOST_OVERRIDE_NEUTRALIZES_POLICY` (`host_override_warnings`,
`tooling/foundry/routing.py`), value-free like the other overrides.

## Observation, 2026-11-07

Claude Code 2.1.319 (Homebrew cask, upgraded from 2.1.306 the same day), headless `claude -p`
sessions in this repository, one subagent each; recorded `effort` and `model` fields of the
subagent transcript (`~/.claude/projects/<repo>/<session>/subagents/*.jsonl`).

| Subagent | CLAUDE_CODE_SUBAGENT_EFFORT | Profile launched | Recorded model | Recorded effort |
|---|---|---|---|---|
| foundry:lupin (routed) | unset | routed-readonly-medium-haiku-5.5 | claude-haiku-5-5 | medium |
| foundry:lupin (routed) | low | routed-readonly-medium-haiku-5.5 | claude-haiku-5-5 | medium |
| general-purpose (built-in, no effort frontmatter) | unset | general-purpose | claude-opus-5-5 | medium (session effort) |
| general-purpose (built-in, no effort frontmatter) | low | general-purpose | claude-opus-5-5 | low |

- Observed: the variable is honoured for a subagent whose definition carries no effort, and
  does not override the effort frontmatter of a routed profile (as recorded in the transcript).
- Deduced, not observed: the `routed-*-none[-haiku-4.5*]` profiles omit effort frontmatter
  (AGENTS.md#R7), so the variable would give them an effort while Foundry reports transmitted
  effort null.
- Unknown: the effort the API actually received (only the transcript record was read); behaviour
  on other host versions.
