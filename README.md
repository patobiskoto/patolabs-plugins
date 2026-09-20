# patolabs — Claude Code + Codex plugins

The **patolabs** marketplace: a single monorepo of Claude Code and Codex plugins that
industrialize how a project goes from a rough idea to a shipped release. Add the
marketplace once, get every plugin.

Claude Code:

```
/plugin marketplace add patobiskoto/patolabs-plugins
/plugin install foundry@patolabs
/plugin install ship-ios@patolabs
```

Codex:

```
codex plugin marketplace add patobiskoto/patolabs-plugins
codex plugin add foundry@patolabs
codex plugin add ship-ios@patolabs
```

The skill names are identical. Invoke them as `/foundry:frame` in Claude Code and
`$foundry:frame` in Codex (same rule for `ship-ios`).

## Plugins

| Plugin | Owns | In one line |
|---|---|---|
| [**foundry**](plugins/foundry/) | idea → merge | Guided brainstorm → ADRs + precise issues → value-first roadmap → gated execution, over a pluggable tracker (YouTrack) + code-host (GitHub). Judgment in the skills, invariants in the code. |
| [**ship-ios**](plugins/ship-ios/) | merge → live | The iOS release loop: locale-neutral changelog → per-locale release notes → Xcode Cloud build → TestFlight gate → App Store submit. |

They **compose by data, not code**: Foundry emits a locale-neutral changelog
(`query changelog <milestone>`); ship-ios turns it into per-locale release notes and
drives the store. ship-ios never depends on Foundry — no Foundry, it takes a changelog
file instead.

## Layout

```
.claude-plugin/marketplace.json   Claude Code catalogue
.agents/plugins/marketplace.json Codex catalogue
plugins/
  foundry/     .claude-plugin/ .codex-plugin/ tooling/ skills/ hooks/ agents/ tests/
  ship-ios/    .claude-plugin/ .codex-plugin/ skills/ templates/ scripts/ tests/
.github/workflows/ci.yml          one CI: tests foundry, validates ship-ios + the catalogue
```

Each plugin keeps its own `version`, `CHANGELOG`, and lifecycle — the monorepo unifies
distribution, not versioning. See `plugins/foundry/docs/adr/FOUNDRY-ADR-0004` for why this
is a monorepo (and what it deliberately does not change).

## Develop

```
cd plugins/foundry && pip install pytest ruff && pytest -q -m "not integration" tests  # Foundry: pure logic
```

Foundry is piloted with its own method (project `FOUNDRY`); the pipeline runs in-repo via
`python3 plugins/foundry/tooling/foundry_cli.py <module>`. Runtime configuration resolves
from environment variables, Claude plugin options, the macOS keychain, then
`~/.config/foundry/config.env`.
