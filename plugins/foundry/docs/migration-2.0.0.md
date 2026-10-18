# Foundry 2.0.0 — upgrade and rollback, 2026-10-18

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 1.5.0 and what is known about going back. It states nothing about
a tag, an installation or a version loaded by a host: those states come after the
merge and are recorded on the release ticket (`skills/release/SKILL.md`, step 9),
not here. Both Foundry manifests must report `2.0.0`; Ship-iOS remains independently `0.3.0`.
The catalogues have no version key. See [release-2.0.0.md](release-2.0.0.md) for what
the version contains. **2.0.0 is a milestone chosen by the maintainer, not a breaking
change:** no changelog entry of this release removes a verb, an option or a
configuration key, so the migration is the usual update commands below and nothing else
is required (read from the `2.0.0` changelog section when this guide was written).
A first installation and a first repository are unchanged: follow
[migration-1.0.0.md](migration-1.0.0.md). An upgrade from 1.4.0 or earlier must also read
[migration-1.5.0.md](migration-1.5.0.md) and the guides it points to: their checks are not
repeated here and are not changed by 2.0.0.

The catalogues name no ref, so an official update is expected to deliver 2.0.0 from the
merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges, and whether a manager refreshes a package whose version does not change is
unknown: a host updated before this release may already carry them under the `1.5.0`
label (derived, not observed).

## Before upgrading from 1.5.0

Two checks, for what the release note announces. Neither edits a cache.

1. **You run Epic campaigns or a Claude Code session with the Bash guard.**
   `hooks/guard_bash.py` is the only file under `hooks/`, `agents/` and `skills/` that
   changed since `foundry-v1.5.0` (`git diff --stat foundry-v1.5.0..HEAD` on those
   directories at `ec86938`); reload components to pick it up. The command shapes denied
   and the approval rules are in the changelog entry of PAT-234 and in
   [campaign-approval.md](campaign-approval.md) ("Commands", "An agent cannot approve
   (layers and limit)"). An approval recorded under 1.5.0 is not rewritten by a package
   change; [campaign-approval.md](campaign-approval.md) states that such a record keeps its own
   `expires_at`, at most `MAX_TTL` (7 days) after its approval (documented there, not run for
   this guide).
2. **Scripts that parse Foundry output.** The replay hint of `issue accept --status` and
   the unknown-flag message of `issue close-epic` changed (changelog entries of PAT-239 and
   PAT-242). `campaign-approval` and `campaign-cadence` emit new or replaced codes, among them
   `journal_invalid`, `review_proof_integrity`, `review_state_without_branch_or_pr`,
   `pr_head_not_in_branch`, `description_worktree_head_moved` and
   `description_head_unreadable` (which replaces `description_session_moved_head`); the
   changelog entries of PAT-234, PAT-235 and PAT-237 list them.

No project data, routing policy, credential or configuration key needs to change. The
changelog entries name no new configuration key. Retain a private snapshot of the
non-secret configuration, the registry, the repository marker and the routing policy
(including its absence) before replacing the package, as for 1.0.0. Credentials stay in
their secret store. The shared Foundry state lives under `FOUNDRY_DATA` or
`~/.config/foundry`, independently of any host plugin cache; the 1.0.0 guide
([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement preserves the
shared data, and does not migrate project data. That has not been observed for an update
from 1.5.0 to 2.0.0. The local files created by a campaign are those of
[migration-1.5.0.md](migration-1.5.0.md) ("New local files"). This release adds one, written
only when `foundry.rollback_native_trial` runs: the reservation ledger `LEDGER_NAME`
(`pat198-native-rollback-ledger.jsonl`) and its `.lock`, in `registry.data_dir()`
(`tooling/foundry/rollback_native_trial.py`; protocol
`docs/qualification/pat-198-native-rollback-protocol.md`). Keep it: it holds the
reservations of the native trials already run. The two new on-demand tools of the release note
(`foundry.rollback_native_trial`, `scripts/skill_evals.py`) write nothing during an
upgrade (deduced from their being run on demand; not run for this guide).

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 1.5.0 to
2.0.0: such a run cannot exist before the merge, and none of these commands was run in
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
versions; their own README and CHANGELOG hold their installation and behaviour, and
none of them is required by this upgrade.

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
are those of 1.5.0 on both hosts: no routing default changes (release note, "What 2.0.0
contains since 1.5.0"). `routing show` and `doctor` bind nothing and observe no host
version, as the 1.1.0 guide says.

These commands were not run for this guide: they need an installed 2.0.0, which does not
exist at the time of writing.

## Verify the version actually loaded

After reload or a new task, verify and retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `2.0.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the release ticket, not here
(`skills/release/SKILL.md`, step 9).

## Rollbacks

### Without leaving 2.0.0

No per-project key or option returns to the 1.5.0 behaviours listed in the release note.
A correction of a 2.0.0 behaviour is an ordinary Foundry pull request delivered by a
later version; never a hand-edited cache.

### Return to 1.5.0

**Not tried.** No command for it has been run, and none is established by this guide.
That includes the rollback to an earlier plugin version, which stays an untried
procedure tracked by PAT-162: a rollback through an official manager is not qualified.
The form of the rollback is the one of [migration-1.5.0.md](migration-1.5.0.md)
("Return to 1.4.0"), with 1.5.0 as the target: reinstall 1.5.0 through the
official manager, never by editing a cache.

What is known:

- The tag `foundry-v1.5.0` exists in this repository (`git tag -l`, read when this guide
  was written); it points to commit `cf8da792561eaa4dcdb8c8ac46c7097e92fb2c24`
  (`git rev-parse foundry-v1.5.0^{}` and `git ls-remote --tags origin 'foundry-v1.5.0^{}'`, read when this guide was written).
- No manager command that installs a named tag of this marketplace is established in
  this repository; whether either manager accepts a tag was not exercised
  ([migration-1.3.0.md](migration-1.3.0.md), "Return to 1.2.0").
- Records written under 2.0.0 are not removed by a package change. How a restored 1.5.0
  reads them is unknown.

What is unknown:

- Whether an official manager of either host can install 1.5.0 once `main` carries
  2.0.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.

## Known unknowns carried by this release

- **PAT-162:** plugin rollback: untried, see above.
- **Gauge:** the real display of its alert on Claude Code and Codex is not observed
  (release note, "What this release does not claim").
- **PAT-234:** the reading of a 1.5.0 approval under the new expiry cap is documented in
  campaign-approval.md and not run for this guide, see check 1.
