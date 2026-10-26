# Foundry 2.5.0 — release note, 2026-10-26

**Source candidate prepared under PLP-314.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.5.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.5.0.md](migration-2.5.0.md).

## Scope and sources

The `2.5.0` section of the [CHANGELOG](../CHANGELOG.md) contains only PLP-311,
assembled byte-for-byte by [`assemble`](../scripts/changelog_assemble.py).
This preparation removes that fragment and leaves `Unreleased` empty.

| Packaged artifact | Behaviour and evidence source |
| --- | --- |
| Explicit same-key issue numbering, `Project.extra.preserve_issue_numbers` | [Explicit source issue numbers](devhub-tracker.md#explicit-source-issue-numbers-plp-311-live-qualification-pending); `DevHubTracker._preserve_issue_numbers`, `_snapshot_issue_number`, `migration_preflight`, `migration_import_issue`, `_import_terminal_issue` and `migration_find_issue` in [devhub.py](../tooling/foundry/trackers/devhub.py); `_validated_binding_entry` in [registry.py](../tooling/foundry/registry.py) |
| Historical ADR `rejected` read/import vocabulary; native decision vocabulary unchanged | [Selective import](devhub-tracker.md#selective-import-pat-319-dhn-34-dhn-36); `_ADR_IMPORT_STATES`, `_ADR_STATES`, `_to_adr`, `_prepare_adr_import`, `create_adr` and `set_adr_status` in [devhub.py](../tooling/foundry/trackers/devhub.py) |
| Offline coverage | [test_devhub_migration_identity.py](../tests/test_devhub_migration_identity.py) and [test_repository_binding_v1.py](../tests/test_repository_binding_v1.py) |
| Required server qualification | DHN-50 in [devhub-requirements.md](devhub-requirements.md#qualification-of-source-numbers-and-historical-rejected-adrs-plp-311), still recorded there as `unknown`; `test_dhn_50_explicit_source_numbers_and_rejected_adr_round_trip` in [test_devhub_live_conformance.py](../tests/integration/test_devhub_live_conformance.py) |

The fragment retains its original qualification status. This source preparation
supplies no production qualification, server deployment or real-project migration.
Before real use, the server extension DEVHUB-133 must be deployed and the target
qualified for DHN-50; see the [migration prerequisites](migration-2.5.0.md#prerequisites-and-data).

## Publication status

Release merge, tag `foundry-v2.5.0`, host installation, and listed / installed / loaded
version readbacks on Claude Code and Codex are not done or claimed by this preparation.
The merge, tag and host-update STOPs each require their own explicit agreement
([release skill](../skills/release/SKILL.md), steps 5–7); readbacks and their release-ticket
record are specified by steps 7–9. This page supplies no review verdict or CI result.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.5.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.4.0` to `2.5.0`; no other manifest field changed |
| Dated changelog and PLP-311 fragment | Updated: byte-identical entry assembled into `2.5.0 — 2026-10-26`; fragment removed |
| Release note and migration guide | New: this page and [migration-2.5.0.md](migration-2.5.0.md), including qualification and publication limits |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.5.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.5.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; PLP-311's existing source documentation is linked above |
