# Migrating from Foundry 0.6.x to 0.7.0

Foundry 0.7.0 keeps one shared implementation and two host packages. Existing tracker,
code-host, registry, routing, and escalation state remains compatible: there is no data
migration, resolver reset, local-model installation, or automatic configuration change.
The local preprocessor and passive telemetry are both disabled unless an operator opts in.

## Install or update Claude Code

For a first installation, run these commands in Claude Code:

```text
/plugin marketplace add patobiskoto/patolabs-plugins
/plugin install foundry@patolabs
```

For an existing 0.6.x installation, refresh the marketplace and update the plugin:

```text
/plugin marketplace update patolabs
/plugin update foundry@patolabs
```

Reload plugin components after the update so hooks and other long-lived integrations
switch to the new cache; starting a new Claude Code session is also sufficient. Then
verify the installed record and run the health check:

```text
/reload-plugins
/plugin list
/foundry:doctor
```

The installed Foundry manifest must report exactly `0.7.0`. For a release verification,
also compare the installed marketplace/plugin source revision with the merged release
commit; a branch working tree or a generated cachebuster is not a release source.

## Install or update Codex

For a first installation, run:

```sh
codex plugin marketplace add patobiskoto/patolabs-plugins
codex plugin add foundry@patolabs
```

Codex refreshes a Git marketplace snapshot separately from its installed plugin cache.
To replace an existing 0.6.x cache with 0.7.0, run:

```sh
codex plugin marketplace upgrade patolabs
codex plugin remove foundry@patolabs
codex plugin add foundry@patolabs
codex plugin list --marketplace patolabs --json
```

Start a new Codex task after reinstalling and run `$foundry:doctor`. The JSON listing and
installed manifest must report exactly `0.7.0`; release verification also compares the
marketplace/plugin source revision with the merged commit. Removing the Codex package
does not remove the shared `~/.config/foundry` configuration, keychain token, registry,
or routing state.

## Shared tracker and code-host configuration

Use `/foundry:configure` in Claude Code or `$foundry:configure` in Codex. Both invoke the
same portable setup:

1. Non-secret `YOUTRACK_URL`, `FOUNDRY_TRACKER`, and `FOUNDRY_CODEHOST` settings are
   written to `~/.config/foundry/config.env` (or the explicit `FOUNDRY_CONFIG` path).
2. On macOS, the permanent YouTrack token is entered invisibly in the operator's terminal
   and stored in Keychain. On other systems it must come from `YOUTRACK_TOKEN` through an
   environment/secret manager. Never put the token in a repository, command argument,
   scratch file, or chat.
3. `configure show` exposes only redacted configured/missing state, and `foundry:doctor`
   checks configuration, tracker authentication, project bindings, code-host resolution,
   host routing, and the optional local provider without completing a model.

The portable resolution precedence remains explicit environment, Claude install option,
macOS Keychain, then Foundry's config file. Foundry runtime data is shared through an
explicit `FOUNDRY_DATA`, otherwise `~/.config/foundry`; host-private plugin data
directories are deliberately not used as a second registry.

## Routing configuration retained from 0.6.x

Existing `.foundry/model-routing.json` files remain schema version 1. The production
policy is unchanged: user request > project override > Foundry default; ordinary roles
may fall back downward, reviewer and architect only upward from their floors; and an
issue allows at most two tier increases before requiring a human. Inspect the resolved
policy without invoking a model:

```sh
python3 tooling/foundry_cli.py routing show
python3 tooling/foundry_cli.py routing show --host codex
```

Model, reasoning effort, and context policy are represented separately for observation
and documentation in 0.7.0, but no adaptive resolver, effort ladder, production shadow
call, or telemetry-fed decision is enabled. See
[`model-routing.md`](model-routing.md) for the complete architecture and host limits.

## Optional local preprocessing

Do nothing to preserve 0.6.x behavior. Local preprocessing has no implicit model and is
disabled until the operator supplies both trusted settings:

```sh
FOUNDRY_LOCAL_SCOUT_ENABLED=1
FOUNDRY_LOCAL_SCOUT_BASE_URL=http://127.0.0.1:11434
```

The repository must separately name an already operator-managed model in
`.foundry/local-scout.json`. A project may tighten limits, but cannot activate the
provider, choose the destination, or authorize cloud fallback. Foundry accepts only an
exact IPv4/IPv6 loopback HTTP endpoint, never downloads weights, starts a daemon, uses a
proxy, grants tools, or trusts the model with provenance or authority.

Failure defaults to `FOUNDRY_LOCAL_SCOUT_ON_FAILURE=error`. An operator may explicitly
choose `cloud_economy`; this only returns one sanitized host-facade plan for an eligible
unavailable/invalid-output failure. It never calls the cloud itself, never applies to a
policy violation or stale input, and never bypasses the production scout route, issue
floor, fallback, escalation, or capability contract. The deprecated
`FOUNDRY_LOCAL_SCOUT_CLOUD_FALLBACK` alias remains compatible throughout 0.x but should
be replaced before 1.0.

The complete privacy boundary, 2/8-second defaults, 10/120-second hard ceilings,
`diff`/`logs`/`tests`/two-pass `code` modes, evidence contract, fallback, and doctor
states are in [`local-scout.md`](local-scout.md).

## Optional passive telemetry

Telemetry remains absent while `FOUNDRY_DATA` is unset. When the operator explicitly
enables that local data root, Foundry may write only the documented private, bounded,
append-only telemetry journal. It is passive, local, fail-open, and never uploads. It
contains no prompt, response, code, path, URL, payload content, free-form error, secret,
or durable repo/user identity, and no observation feeds routing. Export is an explicit
sanitized aggregate operation:

```sh
python3 tooling/foundry_cli.py telemetry export
```

## Expected 0.7.0 limits

- The balanced primary coordinator is guidance, not something Foundry can impose on the
  already-open main conversation: Sonnet 5 / medium for Claude Code, GPT-5.6 Terra /
  medium for Codex.
- Claude alias targets and host-wide overrides can neutralize the resolved model/effort;
  Foundry warns without exposing their values. Codex has no portable per-call turn cap
  and no portable tool mask for its default architect role.
- Local runtime sandboxing and network behavior remain the operator's responsibility.
  Foundry confines its own request and grants local output no product authority.
- The FOUNDRY-35 campaign promotes no local model. All production defaults remain
  unchanged; see [`release-0.7.0.md`](release-0.7.0.md) for the evidence and next test.
