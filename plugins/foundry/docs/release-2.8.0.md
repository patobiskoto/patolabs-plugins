# Foundry 2.8.0 — release note, 2026-10-28

**Source candidate prepared under PLP-339.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.8.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.8.0.md](migration-2.8.0.md). This note covers the Claude Code and Codex hosts.

## Scope and sources

The `2.8.0` section of the [CHANGELOG](../CHANGELOG.md) contains PLP-335 and PLP-325, assembled
byte-for-byte by [`assemble`](../scripts/changelog_assemble.py). This preparation removes those
two fragments and leaves `Unreleased` empty.

| Issue | Packaged artifact | Behaviour and evidence source |
| --- | --- | --- |
| PLP-325 | New verb `routing kpi-publish`; new secret `DEVHUB_METRICS_TOKEN`; new adapter capability `kpi_publication_supported` | [Publication to DevHub (PLP-325)](kpi-export.md#publication-to-devhub-plp-325); [kpi_publish.py](../tooling/foundry/kpi_publish.py); [routing.py](../tooling/foundry/routing.py); [config.py](../tooling/foundry/config.py); [configure.py](../tooling/foundry/configure.py); [base.py](../tooling/foundry/trackers/base.py); [devhub.py](../tooling/foundry/trackers/devhub.py); [test_kpi_publish.py](../tests/test_kpi_publish.py) |
| PLP-335 | Documentation only: the async-launch marker of `_is_background_launch` recorded as observed | [Local invocation record](model-routing.md#local-invocation-record-and-reviewed-diff-attribution-plp-316); docstring of `_is_background_launch` in [observe_agent.py](../hooks/observe_agent.py); no code change |

`routing kpi-publish` is explicit only: `routing.py` is its only importer and no hook, gate,
merge or campaign calls it ([kpi-export.md](kpi-export.md#publication-to-devhub-plp-325)).
Its run against a real DevHub server is not part of this preparation.

## Publication status

Release merge, tag `foundry-v2.8.0`, host installation, and listed / installed / loaded
version readbacks on Claude Code and Codex are not done or claimed by this preparation.
The merge, tag and host-update STOPs each require their own explicit agreement
([release skill](../skills/release/SKILL.md), steps 5–7); readbacks and their release-ticket
record are specified by steps 7–9. This page supplies no review verdict or CI result.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.8.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.7.0` to `2.8.0`; no other manifest field changed |
| Dated changelog and PLP-325, PLP-335 fragments | Updated: byte-identical entries assembled into `2.8.0 — 2026-10-28`; fragments removed |
| Release note and migration guide | New: this page and [migration-2.8.0.md](migration-2.8.0.md) |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.7.0` marked published and tagged, `2.8.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.8.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; the source documentation of PLP-325 and PLP-335 is linked above |
