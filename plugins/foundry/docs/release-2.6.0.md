# Foundry 2.6.0 — release note, 2026-10-27

**Source candidate prepared under PLP-327.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.6.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.6.0.md](migration-2.6.0.md).

## Scope and sources

The `2.6.0` section of the [CHANGELOG](../CHANGELOG.md) contains PLP-319, PLP-318,
PLP-317 and PLP-316 (Epic PLP-315, PLP-ADR-0061), assembled byte-for-byte by
[`assemble`](../scripts/changelog_assemble.py). This preparation removes those four
fragments and leaves `Unreleased` empty.

| Packaged artifact | Behaviour and evidence source |
| --- | --- |
| Local invocation record, `routing review-invocations` | [Local invocation record](model-routing.md#local-invocation-record-and-reviewed-diff-attribution-plp-316); [invocation_log.py](../tooling/foundry/invocation_log.py), [route_agent.py](../hooks/route_agent.py), [observe_agent.py](../hooks/observe_agent.py); [test_invocation_log.py](../tests/test_invocation_log.py) |
| `routing token-attribution` | [Rattachement des tokens](cost-attribution.md#rattachement-des-tokens-à-une-issue-et-à-un-rôle); [token_attribution.py](../tooling/foundry/token_attribution.py); [test_token_attribution.py](../tests/test_token_attribution.py) |
| `routing review-turns` | [Per-turn data of an issue](review-rounds.md#per-turn-data-of-an-issue-plp-318); [review_turns.py](../tooling/foundry/review_turns.py); [test_review_turns.py](../tests/test_review_turns.py) |
| `routing kpi-export` | [kpi-export.md](kpi-export.md); [kpi_export.py](../tooling/foundry/kpi_export.py); [test_kpi_export.py](../tests/test_kpi_export.py) |

The four commands are read-only and offline; none publishes to a tracker. The invocation
record is written by the routing hooks and the Codex façade; no gate, routing decision or
merge reads it (`test_no_decision_path_reads_the_log`).

## Publication status

Release merge, tag `foundry-v2.6.0`, host installation, and listed / installed / loaded
version readbacks on Claude Code and Codex are not done or claimed by this preparation.
The merge, tag and host-update STOPs each require their own explicit agreement
([release skill](../skills/release/SKILL.md), steps 5–7); readbacks and their release-ticket
record are specified by steps 7–9. This page supplies no review verdict or CI result.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.6.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.5.0` to `2.6.0`; no other manifest field changed |
| Dated changelog and PLP-316 to PLP-319 fragments | Updated: byte-identical entries assembled into `2.6.0 — 2026-10-27`; fragments removed |
| Release note and migration guide | New: this page and [migration-2.6.0.md](migration-2.6.0.md) |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.6.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.6.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; the source documentation of PLP-316 to PLP-319 is linked above |
