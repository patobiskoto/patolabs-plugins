---
name: open-pr
description: >
  Opens the PR for the current branch via REST (never gh pr create), sets the tracker
  state to review, and records the PR link on the issue. USE WHEN the user invokes
  /foundry:open-pr (Claude Code), $foundry:open-pr (Codex), "ouvre la PR", "PR ready",
  after implementation is green.
argument-hint: "[ISSUE-ID] [base-branch]"
allowed-tools: Bash(python3:*), Bash(git:*), Bash(gh:*)
---

# open-pr

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/open-pr/SKILL.md`. Never pass the placeholder literally.

PR creation goes through Foundry (never Superpowers' auto-PR, never `gh pr create`), so
the tracker stays in sync. The issue id is derived from the branch name if omitted.

Write a real PR summary from the diff (what changed and why, not the issue title
restated) to a scratch file (Write tool), then run — the command must START with
python3 to match allowed-tools, so use a `<` redirect, never an `echo … |` pipe. The
id is resolved from the branch name (`<ticker>-<n>`) if not given:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue openpr <ISSUE-ID> --summary-stdin < /path/to/summary.md
```
This pushes the branch and first resolves PRs for its exact head. With no candidate it
opens the PR through REST. With one open candidate on the requested base it reuses that
PR. A non-empty `--summary-stdin` explicitly replaces its canonical body while preserving
Foundry's required closing markers; without that supplied summary, Foundry preserves the
existing body and only refreshes the `review` transition on the current head/base/diff.
A closed candidate, a wrong base, or several open candidates fails with an explicit recovery instruction;
Foundry never creates a duplicate silently. The command then writes the PR URL onto the
issue. If work remains (review round expected, AC unchecked), leave a progress note so a
future session can resume cold:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit comment <ISSUE-ID> < /path/to/note.md
```
When ready to ship, invoke `foundry:merge-pr <ISSUE-ID> <pr#>`.
