---
name: release
description: >
  Chains the steps of a Foundry publication: scope, release branch and PR that satisfy the
  release contract (freeze entry included), then three separate STOPs for merge, tag and
  host update, and the readback record on the release ticket. USE WHEN the user invokes
  /foundry:release (Claude Code), $foundry:release (Codex), "publie Foundry", "prépare la
  release", or "release 1.4.0".
argument-hint: "<RELEASE-ISSUE-ID> <VERSION>"
allowed-tools: Bash(python3:*), Bash(git:*), Bash(claude:*), Bash(codex:*)
---

# release — prepare, then stop before every irreversible step

Claude Code replaces the exact `${CLAUDE_PLUGIN_ROOT}` token before this skill reaches
the agent. Codex exposes no plugin-root variable to skill commands. Each command selects
the replaced Claude path when present; otherwise it uses the Codex fallback. In Codex,
derive `<foundry-root>` from this skill's absolute path by removing
`/skills/release/SKILL.md`. Never pass the placeholder literally.

`<marketplace>` is the name under which each host knows this repository's catalogue
(`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`); the coordinator
reads it from the host (`claude plugin marketplace list`, `codex plugin marketplace list`)
and never guesses it. Paths of the installed plugin are read from the host, never typed
from memory. This skill names no marketplace and no user path.

Contract this skill serves: `tests/test_release_contract.py`
(`release_requirements`, `changelog_sections`, `release_documents`, `freeze_entry`) and the
"Release rule" and "Freeze rule" of `README.md`. The skill never merges, tags or updates a
host by itself: steps 5, 6 and 7 are STOPs. The host commands of steps 6 to 8 were not run
when this skill was written (documented from the 1.3.0 publication, ticket PAT-199, PR
#145); treat their output as the authority, not this text.

## 1. Scope
List the entries under `## Unreleased` in `CHANGELOG.md`. Decide the kind with
`release_kind` (`tests/test_release_contract.py`): **patch** when only fixes, **minor** when
a CLI verb or option, a configuration key or a behaviour changes. The kind fixes the
requirements (`release_requirements`): a patch needs the CHANGELOG section and both
manifest bumps; a minor also needs `docs/release-<version>.md` and
`docs/migration-<version>.md`. Record the scope (version, kind, entries) on the release
ticket with `edit comment`:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" edit comment <RELEASE-ISSUE-ID> < /path/to/scope.md
```

## 2. Release branch
Start the release ticket through `foundry:start-issue`, then in the working tree:
- bump `version` in both `plugins/foundry/.claude-plugin/plugin.json` and
  `plugins/foundry/.codex-plugin/plugin.json` (equal, plain semver, no `+` suffix);
- turn `## Unreleased` into `## <version> — <date>` (the date is the publication date in
  Europe/Paris) and leave an empty `## Unreleased` above it. The section opens with a
  header paragraph naming the behaviour changes; the entries below are kept byte-identical
  and in order;
- for a minor, write the release note and the migration guide (writing rules in step 3);
- in both `README.md` files (repository root and `plugins/foundry/`), say what the PR does
  and that the tag, the host installation and the loaded-version readback are not done and
  not claimed;
- compute the freeze entry last, once no frozen page changes any more:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/scripts/release_freeze_entry.py" <version> --date <date>
```
  It is read-only: it prints the entry computed by `freeze_entry` and `changelog_sections`;
  paste it under `releases` in `tests/fixtures/release-history.json`. It refuses a version
  with no published CHANGELOG section and a date that differs from the section's. It was
  run once on the repository at 1.3.0 and printed the entry already in the fixture.
- the PR description gives the documentation status under R5 (`AGENTS.md#R5`) for each
  touched documented artifact, and the CHANGELOG carries its entry.

## 3. Writing rules for frozen pages
Release note, migration guide and the published CHANGELOG section are frozen by digest.
- Never summarise in prose what a tool launches, loads, calls or writes: cite the
  constant, the function or the page.
- Never claim a tag, an installation or a readback before it exists; mark what is deduced
  and what was not run.
- Any later edit of one of these pages changes its digest: recompute the freeze entry
  (step 2) and replace the old one.

## 4. Pull request and review
Open the PR with `foundry:open-pr` and review it with `foundry:review-pr`, merge only
through `foundry:merge-pr`. Expect several rounds on frozen pages: a false sentence there
is "fix before merge" (`AGENTS.md#R9(b)`), never deferred.

## 5. STOP — merge
Do not merge without the maintainer's explicit agreement in this conversation. Criteria
that can only be met after the merge (tag, installation, loaded-version readback) are
merged with the audited `--allow-incomplete-ac --ac-override-reason=<code>` of
`foundry:merge-pr`, and recorded on the release ticket.

## 6. STOP — tag
Do not tag without the maintainer's explicit agreement, and only after the CI of the merge
commit is proven green (`AGENTS.md#R4`). Then create an annotated tag `foundry-v<version>`
on the merge commit and push it with an explicit refspec, never a bare `git push`:
```bash
git tag -a foundry-v<version> -m "Foundry <version>" <merge-commit>
git push origin refs/tags/foundry-v<version>
git ls-remote --tags origin foundry-v<version>
```
The `ls-remote` line is the readback: record the commit it lists.

## 7. STOP — host update
Do not update any host without the maintainer's explicit agreement; the update changes the
installation of the maintainer's machine. Claude Code, then read back:
```bash
claude plugin marketplace update <marketplace>
claude plugin update foundry@<marketplace> -y
claude plugin list --json
```
Codex, then read back:
```bash
codex plugin marketplace upgrade <marketplace>
codex plugin remove foundry@<marketplace>
codex plugin add foundry@<marketplace>
codex plugin list
```
Read the installed `plugin.json` of each host (path taken from the listing) and compare its
`version` with the released one. The listed version and the installed manifest are two
readbacks; neither proves the version a session loads.

## 8. Loaded version
Open a new headless session on each host (`claude -p ...`, `codex exec ...`) asked, without
tools, to quote the installed Foundry path; the version in that path is the loaded
version. Then exercise the new verbs of the release read-only with the installed CLI
(`foundry_cli.py query ...`); never a write. Anything not run is reported as not verified.

## 9. Record on the release ticket
Write one `edit comment` on the release ticket, using the command of step 1, with: tag and
commit (from `ls-remote`), listed version per host, installed-manifest version per host,
loaded version per host, verbs exercised, and what was not verified. A readback that failed
or was skipped is written as such, not omitted.
