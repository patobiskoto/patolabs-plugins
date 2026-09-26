# Migrating Foundry 0.8.x to 0.9.0

Foundry 0.9.0 is one shared implementation distributed through two host packages. It
completes the Linear ADR adapter (versioned ADR Documents, the completed historical ADR
import, and this repository's own cutover to Linear project PAT), and adds a typed
override receipt with recovery replay for a human AC override merge on Linear. It does
**not** migrate tracker data, reset the registry, enable telemetry or local
preprocessing, or change a model mapping, route, effort, context policy, default, gate,
fallback, escalation rule, or capability.

The two package manifests are the version authority and must both report `0.9.0`. The
Claude and Codex catalogue schemas have no plugin-version field: they retain only their
valid source pointers to `./plugins/foundry`. Do not add a catalogue `version` key.

## If your installation still points at a former private marketplace

`foundry@patolabs` now installs from `github.com/patobiskoto/patolabs-plugins` on both
hosts. If your installation still points at a former private marketplace source, switch
it once before the ordinary upgrade below.

Claude Code:

```text
claude plugin marketplace remove patolabs
claude plugin marketplace add patobiskoto/patolabs-plugins
```

Codex:

```sh
codex plugin marketplace remove patolabs
codex plugin marketplace add https://github.com/patobiskoto/patolabs-plugins.git
```

Then continue with the upgrade commands below. An installation already pointed at
`patobiskoto/patolabs-plugins` skips straight to the upgrade.

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
`PAT` (PAT-10); that marker takes precedence over any host-global tracker default. To
verify the cutover took effect after upgrading, run doctor from the checkout and confirm
it resolves `tracker=linear` from the marker rather than a global default or an
environment override:

```sh
python3 tooling/foundry_cli.py doctor
```

A Linear-bound repository whose marker resolves correctly reports `tracker=linear` in
that doctor output. If it instead reports `youtrack` or `devhub`, the marker at
`.foundry/tracker.json` is missing, unreadable, or shadowed by an explicit
`FOUNDRY_TRACKER` environment override in the current shell; clear the override and
re-run doctor before treating any Linear query or write as authoritative. See
[`docs/linear-tracker.md`](linear-tracker.md) for the complete marker and registry
contract.

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

## Roll back both hosts to the exact 0.8.1 source

Rollback is a package-source operation, not a data migration. Use a trusted, detached
checkout of the exact published 0.8.1 source commit or a later signed 0.8.1 release ref
if one is published. Do not use a moving branch, edit an installed cache, or synthesize a
catalogue version. Before changing either host, verify both manifests in that checkout
report exactly `0.8.1` and preserve the checkout for audit:

```sh
git clone https://github.com/patobiskoto/patolabs-plugins.git /tmp/patolabs-plugins-0.8.1
git -C /tmp/patolabs-plugins-0.8.1 checkout --detach <0.8.1-commit>
python3 -c 'import json,pathlib; r=pathlib.Path("/tmp/patolabs-plugins-0.8.1/plugins/foundry"); assert {json.loads((r/p/"plugin.json").read_text())["version"] for p in (".claude-plugin",".codex-plugin")} == {"0.8.1"}'
```

Claude Code has no version selector in its update command. Remove only the package and
marketplace source, add the verified detached checkout as the marketplace, then install
and start a new session:

```sh
claude plugin uninstall foundry@patolabs
claude plugin marketplace remove patolabs
claude plugin marketplace add /tmp/patolabs-plugins-0.8.1
claude plugin install foundry@patolabs
claude plugin list --json
```

Codex can use the same verified checkout. Replace only its package and marketplace
snapshot, then start a new task:

```sh
codex plugin remove foundry@patolabs
codex plugin marketplace remove patolabs
codex plugin marketplace add /tmp/patolabs-plugins-0.8.1
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

After either rollback, run the host's 0.8.1 `foundry:doctor` form and re-check the
installed manifest. Shared config, token, registry, and telemetry remain in place. The
Linear tracker marker, versioned ADR Documents, and `acceptance-override` receipts
written under 0.9.0 are read-compatible historical data on 0.8.1; rolling back does not
delete them, it only removes the 0.9.0 code that reads and extends them. To return to
0.9.0, remove the detached marketplace, add/refresh the normal
`patobiskoto/patolabs-plugins` source, and repeat the appropriate upgrade procedure
above.

## Post-upgrade behavior to expect

- Linear stores ADRs as versioned, witness-bound project Documents; a batch import is
  pre-qualified against a non-authoritative readback before it proceeds, and an unknown
  relation family is reported as an explicit `missing_relations` value, never an omitted
  or silently-empty one.
- `query issue` reports an explicit `conflict` status instead of an empty index when the
  embedded ADR index disagrees with itself.
- A human AC override merge on Linear produces a typed `acceptance-override` receipt;
  an interruption after the receipt is written replays from that receipt instead of
  re-deciding the override.
- This repository's `.foundry/tracker.json` marker resolves `tracker=linear`; verify with
  `foundry:doctor` after upgrading.
- Atomic Epic closure remains unavailable in the Linear adapter, unchanged from 0.8.x.
- Main-profile guidance, mappings, reviewer/architect floors, ordinary fallback,
  escalation ceiling, gates, and capabilities are unchanged.
- Local preprocessing remains disabled and non-privileged. No 0.9.0 result promotes a
  local model or runtime. See [`release-0.9.0.md`](release-0.9.0.md).
