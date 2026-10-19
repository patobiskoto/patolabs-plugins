# Foundry 2.1.0 — upgrade and rollback, 2026-10-19

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 2.0.0 and what is known about going back. It states nothing about
a tag, an installation or a version loaded by a host: those states come after the
merge and are recorded on the release ticket (`skills/release/SKILL.md`, step 9),
not here. Both Foundry manifests must report `2.1.0`. The catalogues have no version
key. See [release-2.1.0.md](release-2.1.0.md) for what the version contains. No
changelog entry of this release removes a verb, an option or a configuration key, so the
migration is the usual update commands below plus one check of the interpreter that runs
the hooks (read from the `2.1.0` changelog section when this guide was written).
A first installation and a first repository are unchanged: follow
[migration-1.0.0.md](migration-1.0.0.md). An upgrade from 1.5.0 or earlier must also read
[migration-2.0.0.md](migration-2.0.0.md) and the guides it points to: their checks are
not repeated here and are not changed by 2.1.0.

The catalogues name no ref, so an official update is expected to deliver 2.1.0 from the
merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges (PR #209 and PR #210), and whether a manager refreshes a package whose version
does not change is unknown: a host updated before this release may already carry them
under the `2.0.0` label (derived, not observed).

## Before upgrading from 2.0.0: verify the Python that runs the hooks

One check, for what the release note announces (item 1). It edits nothing.

Foundry now declares Python 3.11 as its minimum, as `MINIMUM_PYTHON` in
`tooling/python_floor.py`. Hooks run the `python3` that the host resolves
(`hooks/hooks.json`; README "Install or upgrade", paragraph "Python"), not an interpreter
Foundry chooses. Verify, in the environment the host gives its hooks:

```sh
python3 --version
```

It must report 3.11 or later. If it does not:

- The hooks that import `foundry` are inactive and each hook call prints a visible
  `systemMessage`; that includes the `guard_bash` R1 deny and the PAT-215
  `campaign-approval approve` deny (README "Python" paragraph, changelog entry of
  PAT-265). The hooks stay fail-open (FOUNDRY-ADR-0005): the call is not denied.
- `tooling/foundry_cli.py` exits non-zero with a message naming the interpreter, its
  version and the minimum (README "Python" paragraph).
- Make `python3` resolve to 3.11 or later first on the `PATH` of the host, then start a
  new session. `skills/doctor/SKILL.md` gives the same remedy for a red `Hook Python`
  line.

After upgrading, `foundry_cli.py doctor` prints a `Hook Python` line (path, version,
source, minimum), read from the record that a `SessionStart` hook wrote, else from
`python3` on doctor's own `PATH`; the second source is not proof of the host's own
`PATH` (`hook_python_diagnostic`, `tooling/foundry/doctor.py`). Only a line whose source
says it was observed by a hook shows the interpreter the host actually used. How Codex
displays the hook `systemMessage` has not been observed.

No project data, routing policy, credential or configuration key needs to change.
Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, and does not migrate project data. That has not been observed
for an update from 2.0.0 to 2.1.0. This release adds one local file in that directory,
`hook-python.json` (`OBSERVED_FILE`, `tooling/python_floor.py`), written by the
`session_start.py` hook; it holds an interpreter path, a version and a UTC time, and no
secret by construction of `record_interpreter` (read, not run for this guide).

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 2.0.0 to
2.1.0: such a run cannot exist before the merge, and none of these commands was run in
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
are those of 2.0.0 on both hosts: no routing default changes (release note, "What 2.1.0
contains since 2.0.0"). `routing show` and `doctor` bind nothing and observe no host
version, as the 1.1.0 guide says. The `python3` that runs these commands is the one on
your shell `PATH`: if it is older than 3.11 the CLI exits non-zero, as described above.

These commands were not run for this guide: they need an installed 2.1.0, which does not
exist at the time of writing.

## Verify the version actually loaded

After reload or a new task, verify and retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `2.1.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place,
  and the `Hook Python` line of `doctor`, with its source.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the release ticket, not here
(`skills/release/SKILL.md`, step 9).

## Rollbacks

### Without leaving 2.1.0

No per-project key or option returns to the 2.0.0 behaviours listed in the release note.
If the hooks are inactive because of the interpreter, the remedy is the one above: make
`python3` resolve to 3.11 or later. A correction of a 2.1.0 behaviour is an ordinary
Foundry pull request delivered by a later version; never a hand-edited cache.

### Return to 2.0.0

**Not tried.** No command for it has been run, and none is established by this guide.
That includes the rollback to an earlier plugin version, which stays an untried
procedure tracked by PAT-162: a rollback through an official manager is not qualified.
[migration-2.0.0.md](migration-2.0.0.md) ("Return to 1.5.0") is in the same state for
1.5.0. Whatever form a return takes, it goes through the official manager, never by
editing a cache.

What is known:

- The tag `foundry-v2.0.0` exists in this repository (`git tag -l`, read when this guide
  was written); it points to commit `1083e80f4477c386d72519703629ecf758994a31`
  (`git rev-parse foundry-v2.0.0^{}` and `git ls-remote --tags origin 'foundry-v2.0.0^{}'`,
  read when this guide was written).
- No manager command that installs a named tag of this marketplace is established in
  this repository; whether either manager accepts a tag was not exercised
  ([migration-1.3.0.md](migration-1.3.0.md), "Return to 1.2.0").
- Records written under 2.1.0, among them `hook-python.json`, are not removed by a
  package change. How a restored 2.0.0 reads them is unknown.

What is unknown:

- Whether an official manager of either host can install 2.0.0 once `main` carries
  2.1.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.

## Known unknowns carried by this release

- **PAT-162:** plugin rollback: untried, see above.
- **Hook behaviour under Python 3.10 or older before this release:** unknown (changelog
  entry of PAT-265).
- **Codex display of the hook `systemMessage`:** not observed (README "Python"
  paragraph).
