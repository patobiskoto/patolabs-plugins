# Foundry 1.4.0 — upgrade and rollback, 2026-10-15

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 1.3.0 and what is known about going back. It states nothing about
a tag, an installation or a version loaded by a host: those states come after the
merge and are recorded on the release ticket (`skills/release/SKILL.md`, step 9),
not here. Both Foundry manifests must report `1.4.0`; Ship-iOS remains independently `0.3.0`.
The catalogues have no version key. See [release-1.4.0.md](release-1.4.0.md) for what
the version contains and for its behaviour changes. A first installation and a first
repository are unchanged: follow [migration-1.0.0.md](migration-1.0.0.md). An upgrade
from 1.2.0 or earlier must also read [migration-1.3.0.md](migration-1.3.0.md) and the
guides it points to: their checks are not repeated here and are not changed by 1.4.0.

The catalogues name no ref, so an official update is expected to deliver 1.4.0 from the
merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges, and whether a manager refreshes a package whose version does not change is
unknown: a host updated before this release may already carry them under the `1.3.0`
label (derived, not observed).

## Before upgrading from 1.3.0

Seven checks, for what the release note announces. None of them edits a cache.

1. **A hand-edited global registry.** `registry.load()` now raises
   `registry.RegistryFileError` for a provider or an entry that is not a JSON object;
   it was treated as unregistered before (release note, item 6; changelog entry of
   PAT-63). Check the registry under `~/.config/foundry` (or `FOUNDRY_DATA`) with the
   installed CLI after the upgrade (`registry selection --require-v1`, below). An
   unreadable registry still fails open at the R1 guard. The registry is now read as UTF-8
   (changelog entry of PAT-205).
2. **Scripts that read the Linear status projection.** An issue at native `backlog`
   over a valid non-terminal chain now reads `backlog` / `aligned` instead of
   `disagreement` (release note, item 1). A script that matched the old reading must be
   adapted.
3. **Callers of the review and merge skills.** The review that follows a validated
   round is partial on every pull request, and `routing carry-review-proof` may carry a
   proof across a main-branch integration that touches no file of the PR (item 2).
   Follow `skills/merge-pr/SKILL.md` and `skills/review-pr/SKILL.md`; the merge gate and
   the proof format are not modified.
4. **Coordinators of the advisory post-merge review.** `prepare` now writes the report
   budget into the reviewer message and refuses a non-positive room (item 5); drop any
   hand-written budget instruction.
5. **Contributors.** A pull request adds `plugins/foundry/changelog.d/<ISSUE-ID>.md`
   (format in `plugins/foundry/changelog.d/README.md`) and never edits `## Unreleased`
   in `CHANGELOG.md`. The `1.4.0` section is ordered by issue number descending, not in
   merge order (item 7). The helper `scripts/changelog_assemble.py` now refuses a fragment
   with several list items and a malformed version or date (item 7).
6. **Callers of `routing claim-review` and `edit create-issue`.** `routing claim-review`
   without `--host` still defaults to `claude` and now writes one line to standard error
   naming the default and the skipped Codex precheck; a script that treats any standard
   error output as a failure must be adapted (release note, item 10; changelog entry of
   PAT-205). The issue-creation validation of `edit create-issue` also qualifies the
   `(AGENTS.md#R<n>)` form that Linear autolinks (same entry); its real Linear rendering
   was not observed.
7. **Parsers of review output.** The per-round line of the PR description gains
   `review <full|partial>` (`skills/merge-pr/SKILL.md`), and `routing review-rounds` gains a
   `tokens.scope` key in its JSON (release note, items 2 and 10; changelog entry of
   PAT-207). A parser must accept both.

No project data, routing policy, credential or configuration key needs to change. The
changelog entries name no configuration key. No environment read was searched for in the code for this guide; the release note
claims none.

Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, and does not migrate project data. That has not been
observed for an update from 1.3.0 to 1.4.0.

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 1.3.0 to
1.4.0: such a run cannot exist before the merge.

Claude Code:

```text
/plugin marketplace update patolabs
/plugin update foundry@patolabs
/reload-plugins
/plugin list
```

The terminal equivalents are `claude plugin marketplace update patolabs`,
`claude plugin update foundry@patolabs -y`, and `claude plugin list --json`. Reload
components or start a new session before checking the loaded plugin: a session already
open keeps what it loaded. 1.4.0 changes no file under `agents/`.

Codex refreshes the Git snapshot separately from the installed package; start a new
task afterwards:

```sh
codex plugin marketplace upgrade patolabs
codex plugin remove foundry@patolabs
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

No cache editing or generated development cachebuster is installation evidence.
Ship-iOS needs no action: its version does not change.

## Configuration and doctor after the upgrade

No new credential or configuration key is required. `/foundry:configure` (Claude Code)
and `$foundry:configure` (Codex) remain the way to change the trusted configuration.
Then check the binding and the routes with the installed CLI, from the intended
checkout (`<foundry-root>` is the actually loaded plugin root, as in the 1.0.0 guide):

```sh
python3 <foundry-root>/tooling/foundry_cli.py configure show
python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
python3 <foundry-root>/tooling/foundry_cli.py doctor
python3 <foundry-root>/tooling/foundry_cli.py routing show --host claude
python3 <foundry-root>/tooling/foundry_cli.py routing show --host codex
```

The equivalent doctor skills are `/foundry:doctor` and `$foundry:doctor`. The routes
are those of 1.3.0 on both hosts: PAT-171 changes no routing default. `routing show` and
`doctor` bind nothing and observe no host version, as the 1.1.0 guide says.

These commands were not run for this guide: they need an installed 1.4.0, which does not
exist at the time of writing. The new verbs have no installed-plugin check here either;
their procedures are in [review-rounds.md](review-rounds.md) (`routing carry-review-proof`,
`routing review-rounds`), [linear-tracker.md](linear-tracker.md) (`edit transition ...
backlog`, Epic closure, historical ADR links) and [postmerge-review.md](postmerge-review.md),
each described by its page. Exercise them read-only first; `edit transition` and `adr
link-issue` write, so they are not part of a readback.

## Verify the version actually loaded

After reload or a new task, verify and retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `1.4.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the release ticket, not here
(`skills/release/SKILL.md`, step 9).

## Rollbacks

### Without leaving 1.4.0

No per-project key or option returns to the 1.3.0 behaviours listed in the release
note. A correction of a 1.4.0 behaviour is an ordinary Foundry pull request delivered by
a later version; never a hand-edited cache. Putting an issue back to `in-progress`
after a `backlog` return is done by `issue start`, as the changelog entry of PAT-165
states; the returned issue's linked PR is neither closed nor modified.

### Return to 1.3.0

**Not tried.** No command for it has been run, and none is established by this guide.
That includes the rollback to an earlier plugin version, which stays an untried
procedure tracked by PAT-162 (still open): a rollback through an official manager is not
qualified.

What is known:

- The tag `foundry-v1.3.0` exists in this repository (`git tag`, read when this guide
  was written); `git ls-remote --tags origin 'foundry-v1.3.0^{}'` lists commit `0c38fe0`, the merge commit
  of PR #145.
- No manager command that installs a named tag of this marketplace is established in
  this repository; whether either manager accepts a tag was not exercised
  ([migration-1.3.0.md](migration-1.3.0.md), "Return to 1.2.0").
- Under a restored 1.3.0, `routing carry-review-proof`, `routing review-rounds`, the
  `backlog` target of `edit transition`, the skill `foundry:release` and the helpers
  `scripts/changelog_assemble.py`, `scripts/release_freeze_entry.py` and
  `scripts/codex_profile_provenance.py` do not exist
  (deduced from the changelog entries, not tested as a downgrade).
- Records written under 1.4.0 are not removed by a package change: a `proof` carried by
  `carry-review-proof` may hold an optional `carried_from` key (changelog entry of
  PAT-173), and an issue returned to the backlog carries no new receipt (PAT-165). How
  a restored 1.3.0 reads a proof with that key, or an issue at native `backlog`, is
  unknown.

What is unknown:

- Whether an official manager of either host can install 1.3.0 once `main` carries
  1.4.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.

## Known unknowns carried by this release

- **PAT-196:** the Codex reviewer profile loaded was observed for one session only (host
  `0.162.1`, [qualification/pat-196-codex-reviewer-provenance.md](qualification/pat-196-codex-reviewer-provenance.md));
  another host version or session is not covered, and whether a read-only native sandbox
  allows the Foundry ledger lock is unknown. Remarks deferred from PAT-196: PAT-212.
- **PAT-198:** native availability of the rollback profiles of PAT-17: unknown.
- **PAT-162:** plugin rollback: untried, see above.
- **PAT-206:** closure of issues that carry no code change: not settled, so PAT-195
  stays open.
- **PAT-209:** three historical ADR links remain.
- **PAT-201:** the real Linear version-1 rendering after a link is deduced, not observed.
- **PAT-205:** the real Linear rendering of the `(AGENTS.md#R<n>)` qualification is not
  observed, as for PAT-201.
- **PAT-205, PAT-207:** settled earlier deferred remarks (release note, item 10); the
  remarks deferred from PAT-196 (PAT-212) are not corrected by this release.
