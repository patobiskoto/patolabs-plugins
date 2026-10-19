# Foundry 2.2.0 — upgrade and rollback, 2026-10-19

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 2.1.1 and what is known about going back. It states nothing about
a tag, an installation or a version loaded by a host: those states come after the
merge and are recorded on the release ticket (`skills/release/SKILL.md`, step 9),
not here. Both Foundry manifests must report `2.2.0`. The catalogues have no version
key. See [release-2.2.0.md](release-2.2.0.md) for what the version contains. No
changelog entry of this release removes a verb, an option or a configuration key, so the
migration is the usual update commands below plus the checks of the next section (read
from the `2.2.0` changelog section when this guide was written).
A first installation and a first repository are unchanged: follow
[migration-1.0.0.md](migration-1.0.0.md). An upgrade from 2.0.0 must also read
[migration-2.1.0.md](migration-2.1.0.md) (Python 3.11 minimum); an upgrade from 1.5.0 or
earlier must also read [migration-2.0.0.md](migration-2.0.0.md) and the guides it points
to: their checks are not repeated here and are not changed by 2.2.0.

The catalogues name no ref, so an official update is expected to deliver 2.2.0 from the
merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges (PR #214 to #221), and whether a manager refreshes a package whose version
does not change is unknown: a host updated before this release may already carry them
under the `2.1.1` label (derived, not observed).

## Before upgrading from 2.1.1: what to check

These checks are for the release note items 1, 2, 4, 5 and 6. They edit nothing.

- **Scripts that test `issue merge` exit codes (item 1).** Exit 3
  (`EXIT_LOCAL_CLEANUP_FAILED`, `tooling/foundry/issue.py`) is new; see
  `skills/merge-pr/SKILL.md`, step 4, and the changelog entry of PAT-272. Verify that no
  script treats every nonzero code as a refused merge.
- **Rebased branches (item 2).** `issue openpr` now pushes with a lease (changelog entry
  of PAT-273). Verify that no wrapper around it relied on the previous refusal.
- **Reviewer guard and `read-review --since` (item 4).** The guard that accepts the option
  is `hooks/guard_routed_readonly.py` of the installed plugin. Deduced from its code, not
  run: an installed plugin older than 2.2.0 refuses `--since`, so update the plugin on
  the host that runs partial reviews before using it.
- **Companion Epic `Nits` (item 5).** The Foundry skills may create the companion Epic
  `Nits` and its follow-up issue in any repository where the plugin is installed, for a
  ticket with no origin Epic, without a human confirmation (`AGENTS.md#R9` (b),
  PAT-ADR-0029). If a repository must not carry it, decide before upgrading; the
  campaign cadencer does not do this deferral.
- **Linear ADR body (item 6).** A new body holding another `*.md` name outside code is
  refused before any write ([linear-adr-body-guide.md](linear-adr-body-guide.md)).

No project data, routing policy, credential or configuration key needs to change.
Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, and does not migrate project data. That has not been observed
for an update from 2.1.1 to 2.2.0.

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 2.1.1 to
2.2.0: such a run cannot exist before the merge, and none of these commands was run in
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

No new credential or configuration key is required. `/foundry:configure` (Claude Code)
and `$foundry:configure` (Codex) remain the way to change the trusted configuration.
Then check the binding, the routes and the interpreter of the hooks with the installed
CLI, from the intended checkout (`<foundry-root>` is the actually loaded plugin root, as
in the 1.0.0 guide):

```sh
python3 <foundry-root>/tooling/foundry_cli.py configure show
python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
python3 <foundry-root>/tooling/foundry_cli.py doctor
python3 <foundry-root>/tooling/foundry_cli.py routing show --host claude
python3 <foundry-root>/tooling/foundry_cli.py routing show --host codex
```

The equivalent doctor skills are `/foundry:doctor` and `$foundry:doctor`. The routes
are those of 2.1.1 on both hosts: no routing default changes (release note, "What 2.2.0
contains since 2.1.1"). `routing show` and `doctor` bind nothing and observe no host
version, as the 1.1.0 guide says.

These commands were not run for this guide: they need an installed 2.2.0, which does not
exist at the time of writing.

## Verify the version actually loaded

After reload or a new task, verify and retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `2.2.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the release ticket, not here
(`skills/release/SKILL.md`, step 9).

## Rollbacks

### Without leaving 2.2.0

No per-project key or option returns to the 2.1.1 behaviours listed in the release note.
A correction of a 2.2.0 behaviour is an ordinary Foundry pull request delivered by a
later version; never a hand-edited cache.

### Return to 2.1.1

**Not tried.** No command for it has been run, and none is established by this guide.
That includes the rollback to an earlier plugin version, which stays an untried
procedure tracked by PAT-162: a rollback through an official manager is not qualified.
Whatever form a return takes, it goes through the official manager, never by editing a
cache.

What is known:

- The tag `foundry-v2.1.1` exists in this repository; it points to commit
  `933417b23f3f8d9ebd3417b7f6209ac2346d5cf6` (`git rev-parse 'foundry-v2.1.1^{}'` and
  `git ls-remote --tags origin 'foundry-v2.1.1^{}'`, read when this guide was written).
- No manager command that installs a named tag of this marketplace is established in
  this repository; whether either manager accepts a tag was not exercised
  ([migration-1.3.0.md](migration-1.3.0.md), "Return to 1.2.0").
- A companion Epic `Nits` created under 2.2.0 stays in the tracker after a package
  change. How a restored 2.1.1 treats it is unknown.

What is unknown:

- Whether an official manager of either host can install 2.1.1 once `main` carries
  2.2.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.

## Known unknowns carried by this release

- **PAT-162:** plugin rollback: untried, see above.
- **Behaviour of an installed plugin older than 2.2.0 under `--since`:** deduced from the
  guard code, not run.
