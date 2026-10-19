---
name: release
description: >
  Chains the steps of a Foundry publication: scope, release branch and PR that satisfy the
  release contract (freeze entry included), then three separate STOPs for merge, tag and
  host update (each needs its own explicit agreement), and the readback record on the
  release ticket. USE WHEN the user invokes
  /foundry:release (Claude Code), $foundry:release (Codex), "publie Foundry", "prépare la
  release", or "release 1.4.0".
argument-hint: "<RELEASE-ISSUE-ID> <VERSION>"
allowed-tools: Bash(python3:*), Bash(git rm:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(git add:*), Bash(git commit:*), Bash(git fetch:*), Bash(git ls-remote:*)
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
from memory. This skill names no marketplace and no user path: the only marketplace name
written is in the strings that the migration guide must contain (step 2).

Contract this skill serves: `tests/test_release_contract.py`
(`release_requirements`, `changelog_sections`, `release_documents`, `freeze_entry`) and the
"Release rule" and "Freeze rule" of `README.md`. The skill never merges, tags or updates a
host by itself: steps 5, 6 and 7 are STOPs. The host commands of the paragraph above and of
steps 6 to 8 were not run
when this skill was written (documented from the 1.3.0 publication, ticket PAT-199, PR
#145); treat their output as the authority, not this text.

## 1. Scope
List the fragments `plugins/foundry/changelog.d/<ISSUE-ID>.md` (one unreleased entry each;
`## Unreleased` in `CHANGELOG.md` stays empty, PAT-210). Decide the kind with
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
Start the release ticket through `foundry:start-issue`, then work in its worktree. Order
of operations: bump the manifests; assemble and paste the CHANGELOG section; `git rm` the
fragments; write the release note and the migration guide (minor); update the READMEs;
compute the freeze entry last.
- bump `version` in both `plugins/foundry/.claude-plugin/plugin.json` and
  `plugins/foundry/.codex-plugin/plugin.json` (equal, plain semver, no `+` suffix);
- open `## <version> — <date>` (the date is the publication date in Europe/Paris) between the
  empty `## Unreleased` and the previous section. Print its heading and entries from the
  fragments with the helper of the release worktree, from its repository root:
```bash
python3 plugins/foundry/scripts/changelog_assemble.py <version> <date>
```
  It is read-only (`assemble`, `scripts/changelog_assemble.py`): it prints the heading, a blank
  line, then the fragments byte-identical in issue number descending order. Paste it into
  `CHANGELOG.md`, put a header paragraph naming the behaviour changes between the heading and
  the first entry, with one blank line on each side of it (and one blank line between the
  last entry and the previous section's heading), and delete the fragments (`git rm plugins/foundry/changelog.d/<ISSUE-ID>.md`) in
  the same PR. `changelog.d/README.md` stays. Leave `## Unreleased` empty. The tool was run
  once on a copy of the worktree (`changelog.d` of PAT-210) and printed the entries moved out
  of `## Unreleased`. The CHANGELOG steps only (version `9.9.9`, header paragraph, paste,
  delete the fragments leaving `README.md`) were run once on a copy (PAT-207): in
  `pytest -q tests/test_changelog_assemble.py tests/test_release_contract.py -m "not
  historical_fixture"` only three tests of `test_release_contract.py` failed:
  `test_current_version_is_declared_once_and_both_manifests_carry_it`,
  `test_published_releases_stay_frozen_except_declared_exceptions` (`KeyError: '9.9.9'`) and
  `test_frozen_release_check_reports_a_rewrite_of_a_version_without_exception`. They fail
  because that copy did not bump the manifests or add the freeze entry (deduced from the
  tests' subjects); none failed in `tests/test_changelog_assemble.py`, whose shipped-fragments
  test accepts zero fragments;
- for a minor, write the release note and the migration guide (writing rules in step 3).
  `test_current_release_satisfies_the_requirements_of_its_kind` requires: the release note
  `docs/release-<version>.md` names the version, "Claude Code" and "Codex"; the migration
  guide `docs/migration-<version>.md` names the version, contains `~/.config/foundry`, the
  word "verify", and each of `/plugin marketplace update patolabs`,
  `/plugin update foundry@patolabs`, `/reload-plugins`, `/foundry:configure`,
  `/foundry:doctor`, `codex plugin marketplace upgrade patolabs`,
  `codex plugin remove foundry@patolabs`, `codex plugin add foundry@patolabs`,
  `$foundry:configure`, `$foundry:doctor` (the test writes the marketplace name `patolabs`;
  the coordinator checks it against the host's `<marketplace>`); both pages are cited by
  their path in `plugins/foundry/README.md`. A patch needs neither page, only the CHANGELOG
  section and both manifest bumps;
- in both `README.md` files (repository root and `plugins/foundry/`), say what the PR does
  and that the tag, the host installation and the loaded-version readback are not done and
  not claimed; also add the new version to the list of release pull requests in the "Release
  rule" of `plugins/foundry/README.md` and in the comment above `RELEASE_MANIFEST_NAMES` in
  `scripts/ci_plan.py` (repository root), which both name the versions released so far;
- compute the freeze entry last, once no frozen page changes any more. Run the helper of
  the release worktree, never the installed plugin copy (it reads the CHANGELOG and docs of
  its own copy, `PLUGIN` in `scripts/release_freeze_entry.py`), from the repository root of
  the release worktree:
```bash
python3 plugins/foundry/scripts/release_freeze_entry.py <version> --date <date>
```
  It imports `tests/test_release_contract.py` (`import pytest` at its top), so the `python3`
  that runs it must be able to import `pytest`; CI runs it with `sys.executable`, which has it.
  It is read-only: it prints the entry computed by `freeze_entry` and `changelog_sections`;
  paste it under `releases` in `tests/fixtures/release-history.json`. It refuses a version
  with no published CHANGELOG section and a date that differs from the section's. It was
  run once from the repository root of the PAT-166 worktree with
  `python3 plugins/foundry/scripts/release_freeze_entry.py 1.3.0 --date 2026-10-15` and
  printed the entry already in the fixture.
- the PR description gives the documentation status under R5 (`AGENTS.md#R5`) for each
  touched documented artifact, and, except the release PR (it carries the dated section and no fragment), the change
  carries its changelog entry: one fragment `plugins/foundry/changelog.d/<ISSUE-ID>.md`,
  never an edit of `## Unreleased` (PAT-210).

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
Agreement, for steps 5, 6 and 7: each STOP needs its own explicit agreement from the
maintainer in this conversation, asked after the result of the previous step is shown and
waited for. Invoking this skill, an agreement given for another step, or a standing
instruction phrased generally does not count, unless the maintainer explicitly said it
covers that step. When such an advance agreement and the agreement asked after the previous
result differ, the one asked after the result prevails.

Do not merge without the maintainer's explicit agreement in this conversation. Criteria
that can only be met after the merge (tag, installation, loaded-version readback) are
merged with the audited `--allow-incomplete-ac --ac-override-reason=<code>` of
`foundry:merge-pr`, and recorded on the release ticket.

## 6. STOP — tag
Do not tag without the maintainer's explicit agreement, and only after the CI of the merge
commit is proven green (`AGENTS.md#R4`). To wait for the CI of a release PR before the
merge, use the read-only `issue wait-ci` (`docs/ci-wait.md`; it gates the PR head sha, not
the merge commit), never `gh pr checks` or `gh run watch`:
```bash
python3 "$(test -n "${CLAUDE_PLUGIN_ROOT}" && printf %s "${CLAUDE_PLUGIN_ROOT}" || printf %s "<foundry-root>")/tooling/foundry_cli.py" issue wait-ci <PR-NUMBER>
```
Then create an annotated tag `foundry-v<version>` on the merge commit and push it with an
explicit refspec, never a bare `git push`:
```bash
git fetch origin
git tag -a foundry-v<version> -m "Foundry <version>" <merge-commit>
git push origin refs/tags/foundry-v<version>
git ls-remote --tags origin 'foundry-v<version>' 'foundry-v<version>^{}'
```
`allowed-tools` lists exact `git` prefixes and neither `git tag` nor `git push`, so the host's
own permission prompt applies to the tag and to its push unless the user's own permission settings
already allow it (for example a global `Bash(git:*)` rule), on top of this STOP (deduced from the
prefix rule, not run; the `PreToolUse` Bash guard lets a tag push through, `hooks/guard_bash.py`).
`git fetch origin` first, so the merge commit is present locally. For an annotated tag, `refs/tags/foundry-v<version>` lists the tag object and
`refs/tags/foundry-v<version>^{}` lists the commit: record the `^{}` value as the tagged
commit and compare it with the merge commit.

## 7. STOP — host update
Do not update any host without the maintainer's explicit agreement; the update changes the
installation of the maintainer's machine. `claude` and `codex` are not in `allowed-tools`,
so the host's own permission prompt applies. Claude Code, then read back:
```text
claude plugin marketplace update <marketplace>
claude plugin update foundry@<marketplace> -y
claude plugin list --json
```
Codex, then read back:
```text
codex plugin marketplace upgrade <marketplace>
codex plugin remove foundry@<marketplace>
codex plugin add foundry@<marketplace>
codex plugin list --marketplace <marketplace> --json
```
(the form of the migration guides, e.g. `docs/migration-1.3.0.md`; whether its output shows the
installation path is not verified, otherwise read the path from the host's own file listing.)
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
commit (the `^{}` line of `ls-remote`), listed version per host, installed-manifest version per host,
loaded version per host, verbs exercised, and what was not verified. A readback that failed
or was skipped is written as such, not omitted.
