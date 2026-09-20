# Migrating Foundry 0.7.x to 0.8.0

Foundry 0.8.0 is one shared implementation distributed through two host packages. It
adds the structured AC-proof merge path and publishes the fail-closed F46/F47 benchmark
decision. It does **not** migrate tracker data, reset the registry, enable telemetry or
local preprocessing, or change a model mapping, route, effort, context policy, default,
gate, fallback, escalation rule, or capability.

The two package manifests are the version authority and must both report `0.8.0`. The
Claude and Codex catalogue schemas have no plugin-version field: they retain only their
valid source pointers to `./plugins/foundry`. Do not add a catalogue `version` key.

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
`0.8.0`; also bind release verification to the merged source revision, not a branch
working tree or a generated development cachebuster.

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

The installed Codex manifest must identify `0.8.0`; the versionless marketplace source
listing must point at that same merged revision. Removing/re-adding the package does not
remove the shared `~/.config/foundry` configuration, macOS Keychain token, registry,
routing state, or telemetry directory.

## Shared configuration and doctor

Run `/foundry:configure` on Claude Code or `$foundry:configure` on Codex. Both façades
write only non-secret settings to `~/.config/foundry/config.env` (or the explicit
`FOUNDRY_CONFIG` path), use the same registry under explicit `FOUNDRY_DATA` or
`~/.config/foundry`, and resolve the same macOS Keychain token. On other systems the
token must come from `YOUTRACK_TOKEN` through an environment or secret manager. Never
put it in a repository, command argument, scratch file, or chat.

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

## Roll back both hosts to the exact 0.7.0 source

Rollback is a package-source operation, not a data migration. Use a trusted, detached
checkout of the exact published 0.7.0 source commit `5627a31` (or a later signed 0.7.0
release ref if one is published). Do not use a moving branch, edit an installed cache,
or synthesize a catalogue version. Before changing either host, verify both manifests in
that checkout report exactly `0.7.0` and preserve the checkout for audit.

```sh
git clone https://github.com/patobiskoto/patolabs-plugins.git /tmp/patolabs-plugins-0.8.0
git -C /private/path/claude-plugins-0.7.0 checkout --detach 5627a31
python3 -c 'import json,pathlib; r=pathlib.Path("/private/path/claude-plugins-0.7.0/plugins/foundry"); assert {json.loads((r/p/"plugin.json").read_text())["version"] for p in (".claude-plugin",".codex-plugin")} == {"0.7.0"}'
```

Claude Code has no version selector in its update command. Remove only the package and
marketplace source, add the verified detached checkout as the marketplace, then install
and start a new session:

```sh
claude plugin uninstall foundry@patolabs
claude plugin marketplace remove patolabs
claude plugin marketplace add /private/path/claude-plugins-0.7.0
claude plugin install foundry@patolabs
claude plugin list --json
```

Codex can use the same verified checkout. Replace only its package and marketplace
snapshot, then start a new task:

```sh
codex plugin remove foundry@patolabs
codex plugin marketplace remove patolabs
codex plugin marketplace add /private/path/claude-plugins-0.7.0
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

After either rollback, run the host's 0.7.0 `foundry:doctor` form and re-check the
installed manifest. Shared config, token, registry, and telemetry remain in place. A
0.8.0 structured review proof is bound to its original AC/diff/HEAD coordinates and must
not be treated as a reusable 0.7.0 merge waiver; re-review the current diff or use the
older explicit human override path. To return to 0.8.0, remove the detached marketplace,
add/refresh the normal `patobiskoto/patolabs-plugins` source, and repeat the appropriate
upgrade procedure above.

## Post-upgrade behavior to expect

- The merge workflow accepts unchecked tracker AC only with a current structured proof
  bound to the exact issue AC digest, local `HEAD`, base diff, and active reviewer claim;
  malformed or stale evidence fails closed. The audited human override remains explicit.
- Main-profile guidance, mappings, reviewer/architect floors, ordinary fallback,
  escalation ceiling, gates, and capabilities are unchanged.
- Local preprocessing remains disabled and non-privileged. No 0.8.0 result promotes a
  local model or runtime. See [`release-0.8.0.md`](release-0.8.0.md).
