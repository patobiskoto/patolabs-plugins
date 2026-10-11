# Foundry 1.2.0 — upgrade and rollback, 2026-10-11

**This guide is written for the release pull request and is frozen from it.** It says
how to upgrade from 1.1.1 and what is known about going back. It states nothing about
a tag, an installation or a version loaded by a host: those states come after the
merge and are recorded in the root `README.md` and in `plugins/foundry/README.md`.
Both Foundry manifests must report `1.2.0`; Ship-iOS remains independently `0.3.0`.
The catalogues have no version key. See [release-1.2.0.md](release-1.2.0.md) for what
the version contains and for its behaviour changes. A first installation and a first
repository are unchanged: follow [migration-1.0.0.md](migration-1.0.0.md). An upgrade
from 1.0.0 must also read [migration-1.1.0.md](migration-1.1.0.md): its checks (the
Claude Code minimum for the default `economy` tier, the `economy` mappings, the
availability list) are not repeated here and are not changed by 1.2.0.

The catalogues name no ref, so an official update is expected to deliver 1.2.0 from
the merge of the release pull request (derived, not observed; see "Release phases and
evidence" in the release note). The changes themselves have been on `main` since their
own merges, and whether a manager refreshes a package whose version does not change is
unknown: a host updated before this release may already carry them under the `1.1.1`
label (derived, not observed).

## Before upgrading from 1.1.1

Three checks, for what the release note announces. None of them edits a cache.

1. **Callers of `query issue`.** A script, skill or habit that relied on the `adrs`
   field of `query issue` to learn that an ADR-to-issue link is broken (the
   `adr_issue_unavailable` status, or a `conflict` raised for such a link) no longer
   learns it there: run `query adrs` or `query adr <ADR-ID>`, which still refuse a
   broken link. A script that compares the whole payload, or the text of its `note`
   field, must be checked (items 1 and 2 of the release note).
2. **A tracker adapter outside this package.** If its `list_adrs` validates
   ADR-to-issue links, it must override `Tracker.list_adrs_without_issue_links` (item 5
   of the release note). The adapters shipped in this package need nothing.
3. **Code that calls the Linear adapter directly.** A write or a second opening inside
   `LinearTracker.read_scope()` is refused with `LinearReadScopeError`, which a handler
   of `LinearTrackerError` does not catch; a script that matches the error text of a
   failed `search` listing must be checked against the error order of
   [linear-tracker.md](linear-tracker.md) (items 3 and 4 of the release note).

No project data, routing policy, credential or configuration key needs to change:
the PAT-190 entry of the CHANGELOG states that it adds no CLI option and no
configuration key, and the diff of `tooling/` since `foundry-v1.1.1` reads no new
environment variable (result of a text search).

Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, configuration, credentials, registry, receipts and routing
state, and does not migrate project data. That has not been observed for an update
from 1.1.1 to 1.2.0.

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged, through
the official manager of each host. This guide records no run of them from 1.1.1 to
1.2.0: such a run cannot exist before the merge.

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
open keeps the agent profiles it loaded.

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

## Codex command-line version for the default model (PAT-164)

`gpt-6.1-sol` is the Codex target of the `balanced`, `frontier` and `apex` tiers
(`DEFAULT_MAPPINGS` in `tooling/foundry/routing.py`); the `economy` tier targets
`gpt-6-luna`. 1.2.0 does not change these targets. What was observed on 2026-10-11, on
one machine and one ChatGPT login, as [model-routing.md](model-routing.md) records it
(operational note of the Codex invocation façade):

| Codex CLI | Observation |
| --- | --- |
| 0.155.1 | A non-interactive session asking for `gpt-6.1-sol` was refused with status 400, "not supported when using Codex with a ChatGPT account"; the model list cached by that CLI did not contain the model. `gpt-6-sol` and `gpt-6-luna` were accepted |
| 0.160.0 | The model list fetched by this CLI for the same account contained `gpt-6.1-sol`. No session is recorded under this version |
| 0.162.1 | The same request started normally |

- **Lowest version with an observed started session: 0.162.1.**
- **Minimum version that is offered the model: unknown**, above 0.155.1 and at most
  0.160.0.
- The cause is deduced, not proven beyond these observations: the refusal came from
  the outdated CLI, not from the account nor from Foundry's routing. The refused
  session took its model from the top-level `model` of `~/.codex/config.toml`, not from
  Foundry's routing.
- A real Foundry delegation under Codex (a subagent spawn) was not run in that check.
- **Foundry enforces no minimum Codex version.** When this status 400 appears, read
  `codex --version` and the cached model list before concluding that the account or
  the routing is at fault, and upgrade the Codex CLI.

## Configuration and doctor after the upgrade

No new credential or configuration key is required. `/foundry:configure` (Claude Code)
and `$foundry:configure` (Codex) remain the way to change the trusted configuration;
1.2.0 does not ask for it to be re-run. Then check the binding and the routes with the
installed CLI, from the intended checkout (`<foundry-root>` is the actually loaded
plugin root, as in the 1.0.0 guide):

```sh
python3 <foundry-root>/tooling/foundry_cli.py configure show
python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
python3 <foundry-root>/tooling/foundry_cli.py doctor
python3 <foundry-root>/tooling/foundry_cli.py routing show --host claude
python3 <foundry-root>/tooling/foundry_cli.py routing show --host codex
```

The equivalent doctor skills are `/foundry:doctor` and `$foundry:doctor`. The routes
are those of 1.1.1 on both hosts: 1.2.0 changes no routing table. As the 1.1.0 guide
says, `routing show` and `doctor` bind nothing and observe no host version: a green
doctor does not certify the Claude Code minimum of that guide. No Codex CLI version is
read by `tooling/foundry/doctor.py` either (result of a text search, not a proof), so
it says nothing about the section above.

## Verify the version actually loaded

After reload or a new task, retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `1.2.0` and the source revision it was resolved from. A manager listing or a source
  checkout alone is insufficient when a running session still loaded an old cache; if
  the loaded ref is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made after the merge, the
tag and an official update, and recorded on the ticket and in the READMEs, not here.

## Rollbacks

### Without leaving 1.2.0

No per-project key or option returns to the 1.1.1 behaviour of `query issue`, of
`search` or of the read scope. The ADR-to-issue link validation is not lost: it is in
`query adrs`, `query adr`, `frame` and the ADR writes. A correction of a 1.2.0
behaviour is an ordinary Foundry pull request delivered by a later version; never a
hand-edited cache.

The rollbacks that 1.1.0 documents remain as written in
[migration-1.1.0.md](migration-1.1.0.md) and are not changed by 1.2.0: the per-project
return to Haiku 4.5, and the product rollback of the 1-hour cache of the Sonnet 5.5
profiles.

### Return to 1.1.1

**Not tried.** No command for it has been run, and none is established by this guide.

What is known:

- The tag `foundry-v1.1.1` exists and points at `343be14`, the commit that merged the
  1.1.1 release pull request (`plugins/foundry/README.md`).
- No manager command that installs a named tag of this marketplace is established in
  this repository. The Claude Code manager refused a commit SHA as a ref when PAT-93
  tried it; whether either manager accepts a tag was not exercised
  ([migration-1.1.0.md](migration-1.1.0.md), section "Return to 1.0.0").
- The return to 1.1.0 or to 1.0.0 through an official manager is not tried either
  (`plugins/foundry/README.md`).
- By their CHANGELOG entries, the changes of 1.2.0 under `tooling/` are on read paths:
  PAT-168 changes no write path, PAT-189 changes no output of any command and no write
  path, and PAT-190 changes what `query issue` reads and reports. Deduced from those
  entries, not tested as a downgrade: 1.2.0 writes nothing to the tracker or to the
  shared state that 1.1.1 would have written differently, so a restored 1.1.1 package
  is expected to need no data change.
- Under a restored 1.1.1, `query issue` validates the ADR-to-issue links again: a
  broken link reads as `adr_issue_unavailable` or `conflict` again, and the request
  counts of 1.1.1 in [linear-request-cost.md](linear-request-cost.md) apply again.

What is unknown:

- Whether an official manager of either host can install 1.1.1 once `main` carries
  1.2.0, and with which command.
- How each manager behaves when the version it is offered is lower than the one
  installed.
- Anything about a downgrade that only a run would show: none was made.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.
