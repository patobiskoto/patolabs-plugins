# Foundry 1.1.0 — upgrade and rollback, 2026-10-10

**Candidate guide; the tag and the official installed-version checks are pending.**
The catalogues name no ref, so an official update is expected to deliver 1.1.0 from
the merge of the release pull request, before its tag exists: 1.1.0 is effectively
distributed from that merge (derived, not observed; see the phases of the release
note, which also define "tagged" and "verified installed"). The changes themselves
have been on `main` since their own merges, and whether a manager refreshes a package
whose version does not change is unknown: a host updated before this release may
already carry them under the `1.0.0` label (derived, not observed). Do the checks
below before any update.
Both Foundry manifests must report `1.1.0`; Ship-iOS remains independently `0.3.0`. The
catalogues have no version key. See [release-1.1.0.md](release-1.1.0.md) for what the
version contains, its breaking changes and the pre-merge versus post-installation
evidence. A first installation and a first repository are unchanged: follow
[migration-1.0.0.md](migration-1.0.0.md).

## Before upgrading from 1.0.0

Three checks avoid the refusals described in the release note. None of them edits a
cache. They do not cover the cost risk of Haiku 5.5 (item 7 of the release note),
which Foundry does not monitor.

1. **Claude Code version.** The default `economy` tier needs Claude Code 2.1.293 or
   later. Read the version of the host actually used (`claude --version`; a binary on
   `PATH` is not proof of the desktop executor). Below 2.1.293, update Claude Code
   first, or prepare the Haiku 4.5 mapping of the
   [rollback section](#return-to-haiku-45-for-one-project). A host that stays below
   2.1.293 with that mapping still loads the Sonnet 5.5 profiles and their cache
   field: below 2.1.248 the behaviour is unknown and unguarded (item 5 of the release
   note), and the mapping does nothing about it. Only a host update removes that
   unknown.
2. **Project routing policy.** If `.foundry/model-routing.json` has an entry under
   `mappings.claude.economy`, read it against items 2 and 3 of the release note: a
   model-only `haiku-4.5` or `haiku` entry is refused, a model-only entry naming
   another model now inherits `medium`, and an effort-only entry now applies to
   Haiku 5.5.
3. **Availability list.** If `FOUNDRY_CLAUDE_AVAILABLE_MODELS` is set (or the plugin
   option of the same name), add `haiku-5.5` to it. Without it the `economy` tier is
   unavailable for the scout and for every non-gate role that falls back to it, and
   nothing is substituted.

Retain a private snapshot of the non-secret configuration, the registry, the repository
marker and the routing policy (including its absence) before replacing the package, as
for 1.0.0. Credentials stay in their secret store. The shared Foundry state lives under
`FOUNDRY_DATA` or `~/.config/foundry`, independently of any host plugin cache; the 1.0.0
guide ([migration-1.0.0.md](migration-1.0.0.md)) states that package replacement
preserves the shared data, configuration, credentials, registry, receipts and routing
state, and does not migrate project data. That has not been observed for an update from
1.0.0 to 1.1.0.

## Official upgrade

These are the sequences of [migration-1.0.0.md](migration-1.0.0.md), unchanged. A public
1.0.0 → 1.1.0 upgrade has not been run: it cannot be before the merge, and its
outcome on each host is recorded on the ticket afterwards.

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

## Configuration and doctor after the upgrade

No new credential or configuration key is required. `/foundry:configure` (Claude Code)
and `$foundry:configure` (Codex) remain the way to change the trusted configuration;
1.1.0 does not ask for it to be re-run. Then check the binding and the routes with the
installed CLI, from the intended checkout (`<foundry-root>` is the actually loaded
plugin root, as in the 1.0.0 guide):

```sh
python3 <foundry-root>/tooling/foundry_cli.py configure show
python3 <foundry-root>/tooling/foundry_cli.py registry selection --require-v1
python3 <foundry-root>/tooling/foundry_cli.py doctor
python3 <foundry-root>/tooling/foundry_cli.py routing show --host claude
python3 <foundry-root>/tooling/foundry_cli.py routing show --host codex
```

The equivalent doctor skills are `/foundry:doctor` and `$foundry:doctor`. On an
unmodified project, `routing show --host claude` now reports `haiku-5.5` / `medium`
for `economy`; the Codex routes are unchanged. **`routing show` and `doctor` bind
nothing and observe no host version:** they do not certify the 2.1.293 minimum. That
minimum is applied when a subagent is actually launched.

## Verify the version actually loaded

After reload or a new task, retain for each host:

- The host binary, path and version actually invoked.
- The official manager listing, the marketplace revision, the loaded skill and hook
  root and both manifest versions at that resolved root. They must identify Foundry
  `1.1.0` and the source revision it was resolved from. A manager listing or a source checkout alone
  is insufficient when a running session still loaded an old cache; if the loaded ref
  is unavailable, report it unavailable and leave the check open.
- The installed CLI readbacks above, never this repository's source CLI in their place.

Nothing is "installed" until these readbacks exist. They are made by the coordinator
after the merge, the tag and an official update, and recorded on the ticket.

## Rollbacks

### Return to Haiku 4.5 for one project

Write the incumbent explicitly in `.foundry/model-routing.json`, merged with any
existing policy:

```json
{"mappings": {"claude": {"economy": {"model": "haiku-4.5", "effort": null}}}}
```

It selects `routed-<capability>-none-haiku-4.5`, transmits no effort and carries no
host version requirement. The `effort` key must be present and null: the same mapping
without it is refused, and a null effort without the model would apply to Haiku 5.5
and is refused too. Run `routing show --host claude` to read it back. This is covered
offline by `tests/test_claude_haiku55.py`; see [model routing](model-routing.md).

Restoring Haiku 4.5 as the product default is a different act: an ordinary Foundry
pull request restoring `DEFAULT_MAPPINGS["claude"]["economy"]`, not a user action.

### Remove the 1-hour cache from the Sonnet 5.5 profiles

The field is carried by the shipped agent profiles, so there is no project key or
Foundry setting that removes it, and editing an installed profile in a plugin cache is
not a supported procedure. Its removal is a product rollback: an ordinary Foundry pull
request of four steps (empty `CLAUDE_CACHE_TTL_1H_PINS`, regenerate the profiles,
update three tests, update the sentences that assert the field), delivered by a later
version. Every step, file and test is listed in
[the PAT-134 page](qualification/pat-134-subagent-cache-1h.md), section "Règle
d'observation et de retour arrière (écrite avant l'adoption)", with its dry run on a
copy of the repository.

That page also reports the provider's documented precedence, in which a user's own
host cache settings override the profile field. Foundry does not set, test or
recommend them; their effect was not observed.

### Return to 1.0.0

**To be confirmed once 1.1.0 is tagged.** No manager command that installs a named tag of
this marketplace is established in this repository. What is established: the tag
`foundry-v1.0.0` exists in the public source; the Claude Code manager refused a commit
SHA as a ref when PAT-93 tried it; whether either manager accepts a tag was not
exercised ([pat-62-final-report.md](qualification/pat-62-final-report.md)). The
package downgrade observed for 1.0.0 used a temporary local marketplace and targeted
a prepublication baseline, not a published version. Until a command is verified, the
supported way back from a specific change of 1.1.0 is the per-project mapping above
or a later corrective version; never a hand-edited cache.

If a 1.0.0 package is restored, three points are known from the source and were not
tested as a downgrade:

- 1.0.0 does not declare `haiku-5.5`. A project policy that names it must be removed
  or changed first; model resolution fails closed on an undeclared model.
- An availability list extended with `haiku-5.5` for 1.1.0 can stay or be reverted;
  how 1.0.0 treats an unknown name in that list was not checked: unknown.
- 1.0.0 has no rule for an abandoned node (PAT-ADR-0017 is new in 1.1.0). How it reads
  an Epic closed with `--accept-dropped`, or an issue abandoned after it was started,
  was not tested: unknown. Receipts and tracker data are never rewritten to suit an
  older reader.

Keep all newer receipts, reviews, telemetry and qualification history, as in the
[1.0.0 guide](migration-1.0.0.md); do not restore an old registry wholesale over newer
bindings.
