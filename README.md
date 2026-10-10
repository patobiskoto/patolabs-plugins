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

**Published on 2026-10-03:** Foundry **1.0.0** on both hosts (tag `foundry-v1.0.0`),
compatible with Ship-iOS **0.3.0**. The [release notes](plugins/foundry/docs/release-1.0.0.md)
and the [setup and upgrade guide](plugins/foundry/docs/migration-1.0.0.md) are frozen
as shipped in the tag and still read "candidate"; the published state, the
clean-install and installed-pair readbacks on Claude Code and Codex, and what is not
observed (a public 0.9.0 → 1.0.0 upgrade, a model turn loading the plugins) are in the
[final report](plugins/foundry/docs/qualification/pat-62-final-report.md).

**Prepared, not yet published or verified installed:** Foundry **1.1.0** (both host
manifests declare it; Ship-iOS stays at **0.3.0**). Its
[release notes](plugins/foundry/docs/release-1.1.0.md) open with the breaking changes
(Claude Code 2.1.293 minimum for the default `economy` tier, among others) and its
[upgrade and rollback guide](plugins/foundry/docs/migration-1.1.0.md) gives the exact
remedies. Until its tag exists, the published version is 1.0.0.

## Public scope

This repository is published under [Apache-2.0](LICENSE). It is maintained by the
Patolabs team, but it is **not accepting external contributions at this stage**: do not
open a pull request or look for a GitHub Issues backlog. GitHub Issues are disabled and
the product backlog is kept out of this repository.

To report a vulnerability, use [GitHub private vulnerability reporting](SECURITY.md).
Do not disclose security details in a pull request, commit, or public discussion.

Foundry V1 covers YouTrack, Linear, and **private personal GitHub Projects bound to a
private repository**, with the GitHub code-host adapter. Public/organization Projects
and GitHub Apps are unqualified. DevHubTracker remains an optional adapter outside this
V1 matrix. Each checkout selects one explicit tracker/project binding by its canonical
Git remote. The fail-closed Linear issue adapter requires explicit repository/team/project/
state/type/label/milestone IDs selected from the actual checkout's canonical Git
remote. Linear supports bounded grooming of fields, body and parent; its delivery
state, PR and AC authority use deterministic append-only receipts, with targeted native
state synchronization. Native AC checkbox replacement remains unavailable. Linear ADRs
are versioned, witness-bound project Documents; GitHub ADRs use reserved Issue supports
and integrity-checked version comments. Epic closure is audited and non-atomic on the
three V1 trackers. Bounded detection does not exclude an external overwrite between
the fresh read and write (S1→S2). A cockpit Evidence Plane envelope is advisory and
has no state, AC or merge authority.
This repository itself is bound to Linear (project PAT) for issues and ADRs; the
historical YouTrack project is designated a read-only origin archive (see the cutover
incident record in `plugins/foundry/docs/linear-cutover-operations.json`). ChatGPT MCP activation
remains separate planned work.

## Plugins

| Plugin | Owns | In one line |
|---|---|---|
| [**foundry**](plugins/foundry/) | idea → merge | ADR-backed framing, roadmap and gated delivery on the three qualified tracker variants. 1.0.0 (published). |
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
distribution, not versioning, per accepted FOUNDRY-ADR-0004/0005 (retrievable through
`query adr <ADR-ID>` from this bound checkout).

## Develop

```
cd plugins/foundry && pip install pytest ruff && pytest -q -m "not integration" tests  # Foundry: pure logic
```

Foundry is piloted with its own method (Linear project `PAT`, bound by `.foundry/tracker.json`); the pipeline runs in-repo via
`python3 plugins/foundry/tooling/foundry_cli.py <module>`. Runtime configuration resolves
from environment variables, Claude plugin options, the macOS keychain, then
`~/.config/foundry/config.env`.
