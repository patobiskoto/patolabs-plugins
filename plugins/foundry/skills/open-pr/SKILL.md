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

In a repository that keeps `changelog.d/` (this monorepo: `plugins/foundry/changelog.d/`),
a PR other than a release PR carries its changelog entry as one fragment
`changelog.d/<ISSUE-ID>.md` and never edits `## Unreleased` (PAT-210;
`plugins/foundry/changelog.d/README.md`).

Write a real PR summary from the diff (what changed and why, not the issue title
restated) to a scratch file (Write tool), then run — the command must START with
python3 to match allowed-tools, so use a `<` redirect, never an `echo … |` pipe. The
id is resolved from the branch name (`<ticker>-<n>`) if not given. When the id is given, the
current branch must be that issue's branch (`<type>/<ISSUE-ID>-…`, the shape `issue start`
creates); otherwise it refuses before any push (`openpr`, `tooling/foundry/issue.py`, PAT-286):
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue openpr <ISSUE-ID> --summary-stdin < /path/to/summary.md
```
This pushes the branch (`_push_branch`, `tooling/foundry/issue.py`) with
`--force-with-lease=refs/heads/<branch>:<sha>`, where `<sha>` is what this clone last knew
(`refs/remotes/origin/<branch>`, no fetch first) or empty when that ref is absent (the remote
branch must not exist), so a rebased branch goes through. It is refused, nothing overwritten,
when the remote holds commits this clone has not fetched: fetch, integrate, retry. Residual risk (deduced from the lease semantics, not observed): commits fetched but not integrated (tracking ref advanced by `git pull --ff-only` or a background fetch) pass the lease and are overwritten. It never
pushes the default branch; there is no bare `--force`. It then first resolves PRs for its exact head. With no candidate it
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
If a review round has already happened, the summary keeps the per-round line described in
`foundry:merge-pr` and cites the follow-up issue that holds the deferred non-blocking
remarks, with the companion Epic when this PR's deferral created it (rule R9 (b)).
When ready to ship, invoke `foundry:merge-pr <ISSUE-ID> <pr#>`.
