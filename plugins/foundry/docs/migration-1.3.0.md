# Foundry 1.3.0 — upgrade and rollback, 2026-10-15

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 1.2.0 and what is known about going back. It states nothing about
a tag, an installation or a version loaded by a host: those states come after the
merge and are recorded in the root `README.md` and in `plugins/foundry/README.md`.
Both Foundry manifests must report `1.3.0`; Ship-iOS remains independently `0.3.0`.
The catalogues have no version key. See [release-1.3.0.md](release-1.3.0.md) for what
the version contains and for its behaviour changes. A first installation and a first
repository are unchanged: follow [migration-1.0.0.md](migration-1.0.0.md). An upgrade
from 1.1.1 or earlier must also read [migration-1.2.0.md](migration-1.2.0.md) and
[migration-1.1.0.md](migration-1.1.0.md): their checks (the Claude Code minimum for the
default `economy` tier, the `economy` mappings, the availability list, the Codex
command-line version for `gpt-6.1-sol`) are not repeated here and are not changed by
1.3.0.

The catalogues name no ref, so an official update is expected to deliver 1.3.0 from the
merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges, and whether a manager refreshes a package whose version does not change is
unknown: a host updated before this release may already carry them under the `1.2.0`
label (derived, not observed).

## Before upgrading from 1.2.0

Four checks, for what the release note announces. None of them edits a cache.

1. **Callers of the review skills under Codex.** The skills now claim with
   `routing claim-review --host codex` and the route options, then build the packet
   with `routing prepare-review`, then call `routing codex-plan reviewer` with the
   generated send file (item 1 of the release note). A script of your own that called
   `codex-plan` for its first claim must be changed to claim first (deduced from the
   text of the two skills; no custom script was run).
2. **A customised Codex reviewer role.** If it mandates `gh pr diff`, compare it with
   `examples/codex-reviewer.toml` as [reviewer-queries.md](reviewer-queries.md)
   describes (item 2).
3. **Scripts that create Linear issues.** `edit create-issue` can now exit nonzero with
   a JSON object on standard error for an unvalidated creation; follow the
   reconciliation procedure of [linear-tracker.md](linear-tracker.md) instead of
   rerunning the command (item 4).
4. **Code that implements a port outside this package.** A tracker adapter inherits a
   `reconcile_issue_creation` that refuses; a code host that does not set the optional
   `PullRequest.body` leaves it `None` (item 6).

No project data, routing policy, credential or configuration key needs to change. The
CHANGELOG entries name new CLI verbs and options only, not a configuration key. One
environment input is new for one path: preparing a Claude advisory post-merge review
refuses to run without an explicit `CLAUDE_PLUGIN_ROOT` (`postmerge_review.py`,
`advisory_plugin`; [postmerge-review.md](postmerge-review.md)); no other environment
read was added by `git diff foundry-v1.2.0..HEAD -- tooling hooks` apart from `HOME`,
`TMPDIR` and a `GIT_NO_LAZY_FETCH` set for child processes, all three in
`postmerge_review.py`, and a call `codex_available_models(os.environ)` there, whose own
reads are not among the added lines (result of a text search of the added lines, not a
proof).

Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, and does not migrate project data. That has not been
observed for an update from 1.2.0 to 1.3.0.

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 1.2.0 to
1.3.0: such a run cannot exist before the merge.

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
open keeps the agent profiles it loaded, and 1.3.0 changes the body text of 44 agent
files (item 2 of the release note).

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
are those of 1.2.0 on both hosts: PAT-17 changes no routing default
([model-decisions-2026-10-11.md](model-decisions-2026-10-11.md)). `routing show` and
`doctor` bind nothing and observe no host version, as the 1.1.0 guide says.

These commands were not run for this guide: they need an installed 1.3.0, which does not
exist at the time of writing. The new verbs have no installed-plugin check here either;
their disposable procedures are in [review-packet.md](review-packet.md),
[reviewer-queries.md](reviewer-queries.md), [postmerge-review.md](postmerge-review.md)
and [linear-tracker.md](linear-tracker.md), each described by its page.

## Verify the version actually loaded

After reload or a new task, retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `1.3.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the ticket and in the READMEs, not here.

## Rollbacks

### Without leaving 1.3.0

No per-project key or option returns to the 1.2.0 review path or to the 1.2.0 Linear
creation check. A correction of a 1.3.0 behaviour is an ordinary Foundry pull request
delivered by a later version; never a hand-edited cache.

The model rollback that PAT-17 exercises is a configuration change, not a plugin
rollback: [model-decisions-2026-10-11.md](model-decisions-2026-10-11.md) gives eight
rows and `python3 plugins/foundry/scripts/check_model_rollback.py --out <file>`, which
that page says builds plans on disposable copies and launches no host model. The
rollbacks of the 1.1.0 guide (per-project return to Haiku 4.5, the 1-hour cache) remain
as written in [migration-1.1.0.md](migration-1.1.0.md).

### Return to 1.2.0

**Not tried.** No command for it has been run, and none is established by this guide.
That includes the rollback to an earlier plugin version, which stays an untried
procedure tracked by PAT-162.

What is known:

- The tag `foundry-v1.2.0` exists and points at `81724f1`, the commit that merged the
  1.2.0 release pull request (`plugins/foundry/README.md`, PAT-194 record).
- No manager command that installs a named tag of this marketplace is established in
  this repository; whether either manager accepts a tag was not exercised
  ([migration-1.2.0.md](migration-1.2.0.md), "Return to 1.1.1").
- Deduced from the CHANGELOG entries, not tested as a downgrade: 1.3.0 adds records
  that 1.2.0 does not know, namely advisory post-merge comments on a Linear audit
  issue, and issues created under the qualified rendering. A restored 1.2.0 package
  would read the former as ordinary comments, within its ten-comment window (deduced
  from `LinearTracker._to_issue` before PAT-200). Neither is removed by a package
  change.
- Under a restored 1.2.0, `routing prepare-review`, `search-review`, `list-review`,
  `postmerge-review` and `edit reconcile-create` do not exist, and the skills of that
  version claim as they did before (deduced from the diff since the tag).

What is unknown:

- Whether an official manager of either host can install 1.2.0 once `main` carries
  1.3.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.
