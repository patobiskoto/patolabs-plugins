# Foundry 2.3.0 — upgrade and rollback, 2026-10-23

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 2.2.0 and what is known about going back. It states nothing about
a tag, an installation, a version loaded by a host or the cutover of this repository:
those states come after the merge and are recorded on the release ticket
(`skills/release/SKILL.md`, step 9), not here. Both Foundry manifests must report
`2.3.0`. The catalogues have no version key. See [release-2.3.0.md](release-2.3.0.md)
for what the version contains. Apart from the DevHub pilot path that PAT-323 retires (item 1 below),
no entry of the `2.3.0` changelog section was read as removing a verb, an option or a
configuration key; the changes that can affect an existing user are the checks of the
next section (read from the `2.3.0` changelog section when this guide was written).
A first installation and a first repository are unchanged: follow
[migration-1.0.0.md](migration-1.0.0.md). An upgrade from 2.1.1 or earlier must also
read [migration-2.2.0.md](migration-2.2.0.md); an upgrade from 2.0.0 must also read
[migration-2.1.0.md](migration-2.1.0.md) (Python 3.11 minimum); an upgrade from 1.5.0 or
earlier must also read [migration-2.0.0.md](migration-2.0.0.md) and the guides it points
to: their checks are not repeated here and are not changed by 2.3.0.

The catalogues name no ref, so an official update is expected to deliver 2.3.0 from the
merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges (PR #223 to #256), and whether a manager refreshes a package whose version
does not change is unknown: a host updated before this release may already carry them
under the `2.2.0` label (derived, not observed).

## Before upgrading from 2.2.0: what to check

These checks are for the release note items 1 to 4. They edit nothing.

- **DevHub bindings (item 1).** An existing DevHub registry entry that has `canonical_repo`
  but no marker fails closed from its checkout; the fix is `registry bootstrap devhub`,
  described in the changelog entry of PAT-323 and in [devhub-tracker.md](devhub-tracker.md).
  Verify, before upgrading, whether any registry entry uses the `devhub` tracker; the
  registry lives under `~/.config/foundry` or `FOUNDRY_DATA`.
- **A cutover toward DevHub (item 2).** It needs a DevHub at `72a24e2` or later
  ([devhub-tracker.md](devhub-tracker.md), "Corrections from the PAT-332 rehearsal"). This
  release does not run any cutover of this repository.
- **`issue accept` (item 3).** The refusal for YouTrack and GitHub Projects is unchanged in
  effect; its message changes (changelog entry of PAT-330). Verify that no script matches
  the old message text.
- **`--help` and options (item 4).** `-h`/`--help` now prints the usage in the verbs named by
  the changelog entry of PAT-286, `--ac-override-reason` is accepted by `issue merge` only,
  and `issue openpr <ISSUE-ID>` refuses a branch that is not that issue's. Verify that no
  wrapper relied on the previous behaviour.

No project data, routing policy, credential or configuration key needs to change.
Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, and does not migrate project data. That has not been observed
for an update from 2.2.0 to 2.3.0.

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 2.2.0 to
2.3.0: such a run cannot exist before the merge, and none of these commands was run in
this release.

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
open keeps what it loaded.

Codex refreshes the Git snapshot separately from the installed package; start a new
task afterwards:

```sh
codex plugin marketplace upgrade patolabs
codex plugin remove foundry@patolabs
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

No cache editing or generated development cachebuster is installation evidence.
Ship-iOS needs no action: its version does not change. The other plugins of the
catalogue (Gauge, Relay, Blueprint, Witness) are separate installs with their own
versions and none of them is required by this upgrade.

## Configuration and doctor after the upgrade

No new credential or configuration key is required by this guide. `/foundry:configure`
(Claude Code) and `$foundry:configure` (Codex) remain the way to change the trusted
configuration. Then check the binding, the routes and the interpreter of the hooks with
the installed CLI, from the intended checkout (`<foundry-root>` is the actually loaded
plugin root, as in the 1.0.0 guide):

```sh
python3 <foundry-root>/tooling/foundry_cli.py configure show
python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
python3 <foundry-root>/tooling/foundry_cli.py doctor
python3 <foundry-root>/tooling/foundry_cli.py routing show --host claude
python3 <foundry-root>/tooling/foundry_cli.py routing show --host codex
```

The equivalent doctor skills are `/foundry:doctor` and `$foundry:doctor`. No model or
effort vocabulary file changes (release note, "What 2.3.0 contains since 2.2.0").
`routing show` and `doctor` bind nothing and observe no host version, as the 1.1.0
guide says.

These commands were not run for this guide: they need an installed 2.3.0, which does not
exist at the time of writing.

## Verify the version actually loaded

After reload or a new task, verify and retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `2.3.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the release ticket, not here
(`skills/release/SKILL.md`, step 9).

## Rollbacks

### Without leaving 2.3.0

No per-project key or option returns to the 2.2.0 behaviours listed in the release note.
A correction of a 2.3.0 behaviour is an ordinary Foundry pull request delivered by a
later version; never a hand-edited cache.

### Return to 2.2.0

**Not tried.** No command for it has been run, and none is established by this guide.
That includes the rollback to an earlier plugin version, which stays an untried
procedure tracked by PAT-162: a rollback through an official manager is not qualified.
Whatever form a return takes, it goes through the official manager, never by editing a
cache.

What is known:

- The tag `foundry-v2.2.0` exists in this repository (`git tag --list 'foundry-v2.2.0'`,
  read when this guide was written); the commit it points to was not recorded here.
- No manager command that installs a named tag of this marketplace is established in
  this repository; whether either manager accepts a tag was not exercised
  ([migration-1.3.0.md](migration-1.3.0.md), "Return to 1.2.0").
- A DevHub project, a registry entry or a marker written under 2.3.0 stays in place after
  a package change. How a restored 2.2.0 treats a `devhub` binding is unknown.

What is unknown:

- Whether an official manager of either host can install 2.2.0 once `main` carries
  2.3.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.

## Known unknowns carried by this release

- **PAT-162:** plugin rollback: untried, see above.
- **Cutover of this repository toward DevHub:** not part of this release; its outcome is
  unknown here.
