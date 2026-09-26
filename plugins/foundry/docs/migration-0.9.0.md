# Migrating Foundry 0.8.x to 0.9.0

Foundry 0.9.0 is one shared implementation distributed through two host packages. It
introduces the fail-closed Linear tracker adapter itself and, in the same release,
completes its ADR half (versioned ADR Documents, the completed historical ADR import),
cuts this repository over to Linear project PAT, and adds a typed override receipt with
recovery replay for a human AC override merge on Linear — none of that existed in 0.8.x.
It does **not** migrate tracker data, reset the registry, enable telemetry or local
preprocessing, or change a model mapping, route, reasoning effort, context policy,
default, gate, or fallback; CI gate semantics (FOUNDRY-ADR-0002) are unchanged. It does
add new tracker and workflow capabilities — see
[`release-0.9.0.md`](release-0.9.0.md) for the complete list — none of which removes or
weakens an existing one.

The two package manifests are the version authority and must both report `0.9.0`. The
Claude and Codex catalogue schemas have no plugin-version field: they retain only their
valid source pointers to `./plugins/foundry`. Do not add a catalogue `version` key.

## If your installation still points at a former private marketplace

`foundry@patolabs` now installs from `github.com/patobiskoto/patolabs-plugins` on both
hosts. If your installation still points at a former private marketplace source, switch
it once before the ordinary upgrade below.

Removing a marketplace source uninstalls the plugins it provided, so this switch is not
an update: it must end with a fresh `install`/`add`, not the "existing installation"
upgrade commands below.

Claude Code:

```text
claude plugin marketplace remove patolabs
claude plugin marketplace add patobiskoto/patolabs-plugins
claude plugin install foundry@patolabs
```

Codex:

```sh
codex plugin marketplace remove patolabs
codex plugin marketplace add https://github.com/patobiskoto/patolabs-plugins.git
codex plugin add foundry@patolabs
```

The Codex command above spells the source as an explicit Git URL because it is
replacing an entirely different marketplace source; the fresh-install command further
below spells the same source as the bare `patobiskoto/patolabs-plugins` shorthand
Codex resolves against GitHub by default. Both forms name the same repository.

Then run `/foundry:configure` and `/foundry:doctor` (Claude Code) or `$foundry:configure`
and `$foundry:doctor` (Codex) to confirm the fresh install. An installation already
pointed at `patobiskoto/patolabs-plugins` skips straight to the ordinary upgrade below.

## Upgrade Claude Code

From an existing `foundry@patolabs` installation, refresh the marketplace, update the
package, reload the long-lived plugin components, and inspect the installed record:

```text
/plugin marketplace update patolabs
/plugin update foundry@patolabs
/reload-plugins
/plugin list
/foundry:configure
/foundry:doctor
```

The equivalent non-interactive package inspection is `claude plugin list --json` in a
terminal. A fresh installation still uses:

```text
/plugin marketplace add patobiskoto/patolabs-plugins
/plugin install foundry@patolabs
/foundry:configure
/foundry:doctor
```

The update is not active in an already-running process until plugin components are
reloaded or a new Claude Code session starts. The installed manifest must report exactly
`0.9.0`; also bind release verification to the merged source revision, not a branch
working tree or a generated development cachebuster.

A same-version update against a marketplace that already reported `0.8.1` did not
refresh the installed code before this release; `/plugin update foundry@patolabs`
against the `0.9.0` source now replaces the installed package.

## Upgrade Codex

Codex keeps its Git marketplace snapshot separate from the installed plugin cache.
Refresh the snapshot, replace the package, inspect the source listing, then start a new
task so the new skill and hook sources are loaded:

```sh
codex plugin marketplace upgrade patolabs
codex plugin remove foundry@patolabs
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

In that new task, run `$foundry:configure` and `$foundry:doctor`. A fresh installation
uses:

```sh
codex plugin marketplace add patobiskoto/patolabs-plugins
codex plugin add foundry@patolabs
```

The installed Codex manifest must identify `0.9.0`; the versionless marketplace source
listing must point at that same merged revision. Removing/re-adding the package does not
remove the shared `~/.config/foundry` configuration, macOS Keychain token, registry,
routing state, or telemetry directory.

## Shared configuration and doctor

Run `/foundry:configure` on Claude Code or `$foundry:configure` on Codex. Both façades
write only non-secret settings to `~/.config/foundry/config.env` (or the explicit
`FOUNDRY_CONFIG` path), use the same registry under explicit `FOUNDRY_DATA` or
`~/.config/foundry`, and resolve the same macOS Keychain token. On other systems the
token must come from `YOUTRACK_TOKEN` / `LINEAR_API_TOKEN` through an environment or
secret manager. Never put it in a repository, command argument, scratch file, or chat.

`configure show` exposes only configured/missing state. Doctor is read-only and checks
configuration, tracker authentication, project binding, code-host resolution, both host
route matrices, reviewer/architect floors, override-name presence, and optional local
provider state without invoking a model:

```sh
python3 tooling/foundry_cli.py configure show
python3 tooling/foundry_cli.py doctor
python3 tooling/foundry_cli.py routing show --host claude
python3 tooling/foundry_cli.py routing show --host codex
```

Run `/foundry:doctor` on Claude Code or `$foundry:doctor` on Codex for the equivalent
skill-driven check.

## Verify the Linear cutover after upgrading in this repository

This repository carries a `.foundry/tracker.json` marker that binds it to Linear project
`PAT` (PAT-10); that marker takes precedence over any host-global tracker default. Verify
the *installed* 0.9.0 package's cutover, not this checkout's own source tree — the
checkout is not what either host actually loaded. Run doctor from this repository's root
through the installed plugin:

```text
# Claude Code, from this repository root
/foundry:doctor
```

```sh
# Codex, from this repository root
$foundry:doctor
```

A Linear-bound repository whose marker resolves correctly reports `tracker=linear` in
that doctor output; the marker always wins over an explicit `FOUNDRY_TRACKER`
environment override naming a different provider — `tracker_name_for_checkout` uses the
repository binding whenever the marker file is present at all, so a present, valid
marker is never silently shadowed. A **malformed, moved, or unreadable** marker is
refused, not silently ignored: doctor's first check prints a red `Config` error instead
of any tracker name, because `repository_tracker_binding` raises before ever falling
back to a default (`tooling/foundry/registry.py`). Only a **missing** marker file (no
`.foundry/tracker.json` at all) falls through to the host-global default, which is
`youtrack` unless `FOUNDRY_TRACKER` says otherwise. So: if doctor reports
`tracker=youtrack` or `tracker=devhub` for this repository, the marker file is missing
outright; if doctor instead prints a red `Config` error, the marker is present but
invalid. Either way, restore `.foundry/tracker.json` from version control before
treating any Linear query or write as authoritative — never delete it and never set
`FOUNDRY_TRACKER` to work around it. See [`docs/linear-tracker.md`](linear-tracker.md)
for the complete marker and registry contract.

## Diagnose host overrides without exposing values

Project overrides remain in `.foundry/model-routing.json`; precedence remains explicit
user request, project configuration, then Foundry default. Compare both `routing show`
outputs with the committed project file before changing anything.

Claude doctor automatically detects the supported host-wide model/effort and alias-target
override names and reports names only, never their values. Codex cannot portably inspect
the effective parent profile from inside a skill; when that profile contains a `model`
or `model_reasoning_effort` override, pass only presence flags:

```sh
python3 tooling/foundry_cli.py doctor --profile-model-active --profile-effort-active
```

An override warning means the host may neutralize the resolved recommendation; it does
not authorize Foundry to rewrite the host profile. The recommended ordinary main profile
is Sonnet 5 / medium on Claude Code or GPT-5.6 Terra / medium on Codex, but the plugin
cannot force the model of the already-open main conversation. Reviewer remains at least
frontier/high, architect at least apex/high, and at most two tier increases are available
per issue before human intervention.

## Rollback

Rollback is a package-source downgrade, not a data migration, and it interacts with the
Linear cutover very differently depending on whether the repository is Linear-bound.

### No rollback below 0.9.0 for a Linear-bound repository

This repository — and any other repository carrying a `.foundry/tracker.json` marker
bound to Linear — has **no rollback path below 0.9.0**. Every Foundry version before
0.9.0 has no Linear adapter, no repository marker, and no acceptance-override receipt at
all, so it cannot "read" any of them; concretely, pre-0.9.0 code never consults
`.foundry/tracker.json` and instead resolves the host-global default tracker
(`tooling/foundry/registry.py` around L422-428). That is exactly what happened here on
2026-09-26: a Foundry command run from a `main` checkout without the marker resolved the
host default and wrote seven issues into the archived YouTrack project (recorded as
incident `pat10-youtrack-write-2026-09-26` under `incidents` in
[`docs/linear-cutover-operations.json`](linear-cutover-operations.json)).
[`docs/linear-tracker.md`](linear-tracker.md) records that, for this repository, the
rollback window closed the moment the first post-cutover Linear lifecycle write and the
PAT-23 ADR import happened: recovery is forward-only, and **removing the marker or
re-enabling YouTrack while Linear is writable is not a rollback — it is forbidden
dual-write.** Do not do either.

If a genuine defect in 0.9.0 code needs to be backed out, fix forward instead: the
Linear cutover marker, versioned ADR Documents, and typed override receipts are
read-compatible historical data regardless of which Foundry version is installed, but
Foundry does not offer, and this repository cannot use, a supported downgrade path that
runs an earlier codebase against it.

### Other repositories

A repository that never carried a `.foundry/tracker.json` marker (still on YouTrack,
DevHub, or the host default) is not bound by the paragraph above, but this guide still
cannot hand it a verified downgrade command: there is no published, tagged 0.8.1 commit
or signed release ref in this public repository to pin (0.8.1 predates this mirror's
tagging discipline), and a bare manifest-version check cannot by itself establish that
an arbitrary commit whose manifests happen to say `0.8.1` is a trustworthy published
0.8.1 source. In practice, downgrading such a repository means locating your own
trusted pre-0.9.0 checkout (for example a local clone made before this upgrade),
verifying both its manifests report the version you actually intend to run, and
repeating the marketplace remove/add and install/reinstall steps above against that
checkout instead of `patobiskoto/patolabs-plugins`. Shared config, token, registry, and
telemetry remain in place either way. To return to 0.9.0 afterward, remove that
detached marketplace source, add/refresh the normal `patobiskoto/patolabs-plugins`
source, and repeat the appropriate upgrade procedure above.

## Post-upgrade behavior to expect

- Linear stores ADRs as versioned, witness-bound project Documents; a batch import is
  pre-qualified against a non-authoritative readback before it proceeds, and an unknown
  relation family is reported as an explicit `missing_relations` value, never an omitted
  or silently-empty one.
- `query issue` reports an explicit `conflict` status instead of an empty index when the
  embedded ADR index disagrees with itself.
- A human AC override merge on Linear produces a typed `acceptance-override` receipt.
  Recovery is not automatic: re-running the exact same override command replays from an
  existing receipt (interrupted case) or backfills a missing one for a pre-existing
  override merge (PAT-10 shape) instead of asking for a second human decision.
- This repository's `.foundry/tracker.json` marker resolves `tracker=linear`; verify with
  `foundry:doctor` after upgrading.
- Atomic Epic closure is unavailable in the new Linear adapter, matching YouTrack's
  existing posture; no adapter emulates it with a racy read/write.
- Model mappings, reasoning efforts, routes, reviewer/architect floors, ordinary
  fallback, escalation ceiling, and CI gate semantics are unchanged. 0.9.0 adds new
  tracker and workflow capabilities (see [`release-0.9.0.md`](release-0.9.0.md)); it does
  not remove or weaken any existing one.
- Local preprocessing remains disabled and non-privileged. No 0.9.0 result promotes a
  local model or runtime. See [`release-0.9.0.md`](release-0.9.0.md).
