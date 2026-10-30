# Foundry 2.9.0 — release note, 2026-10-30

**Source candidate prepared under PLP-348.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.9.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.9.0.md](migration-2.9.0.md). This note covers the Claude Code and Codex hosts.

## Scope and sources

The `2.9.0` section of the [CHANGELOG](../CHANGELOG.md) contains PLP-345, PLP-344, PLP-343,
PLP-342, PLP-341, PLP-338, PLP-337, PLP-324, PLP-322, PLP-321 and PLP-29, assembled
byte-for-byte by [`assemble`](../scripts/changelog_assemble.py). This preparation removes those
eleven fragments and leaves `Unreleased` empty.

| Issue | Packaged artifact | Behaviour and evidence source |
| --- | --- | --- |
| PLP-343 | Publication of the KPI records at the end of `issue merge` | [Publication at merge (PLP-343)](kpi-export.md#publication-at-merge-plp-343); [kpi_merge.py](../tooling/foundry/kpi_merge.py); `_publish_kpi_after_merge` in [issue.py](../tooling/foundry/issue.py); [test_kpi_merge.py](../tests/test_kpi_merge.py) |
| PLP-342 | `routing kpi-export` finds the session logs; `routing kpi-publish` revisions and local ledger | [Finding the session logs (PLP-342)](kpi-export.md#finding-the-session-logs-plp-342); [Revisions (PLP-342)](kpi-export.md#revisions-plp-342); [kpi_sessions.py](../tooling/foundry/kpi_sessions.py); [kpi_publish.py](../tooling/foundry/kpi_publish.py) |
| PLP-337 | Manifest option `DEVHUB_METRICS_TOKEN`; order of reads in `issue merge`; `release_notes.py` message and standard-error line; documentation and test corrections | [Claude Code manifest](../.claude-plugin/plugin.json); [kpi-export.md](kpi-export.md); [release_notes.py](../scripts/release_notes.py); [ci-wait.md](ci-wait.md); [cost-attribution.md](cost-attribution.md); [review-rounds.md](review-rounds.md) |
| PLP-344, PLP-345 | Lower-bound token counting for Claude logs without terminal snapshot; parent `toolUseResult.usage` for the last request | [Tokens that are a lower bound (PLP-344)](kpi-export.md#tokens-that-are-a-lower-bound-plp-344); [cost-attribution.md](cost-attribution.md); [cost_attribution.py](../tooling/foundry/cost_attribution.py); [token_attribution.py](../tooling/foundry/token_attribution.py) |
| PLP-321 | `kpi-export`, `token-attribution` and `review-turns` corrections; documentation corrections | [kpi-export.md](kpi-export.md); [cost-attribution.md](cost-attribution.md); [model-routing.md](model-routing.md); [review-rounds.md](review-rounds.md) |
| PLP-29 | New GitHub Release STOP in `foundry:release`; read-only helper `release_notes.py` | [release skill](../skills/release/SKILL.md), step 7; [release_notes.py](../scripts/release_notes.py) |
| PLP-341, PLP-322, PLP-324 | Documentation only | [review-rounds.md](review-rounds.md); [devhub-tracker.md](devhub-tracker.md); [PLP-324 qualification](qualification/plp-324-subagent-terminal-snapshots.md) |
| PLP-338 | Tests only | `tests/` |

The publication at merge is run by `issue merge` itself, not by a hook, and holds no gate:
its step, outputs and limits are in
[kpi-export.md](kpi-export.md#publication-at-merge-plp-343). Its run against a real DevHub
server is not part of this preparation (same page, "Limits").

## Publication status

Release merge, tag `foundry-v2.9.0`, GitHub Release, host installation, and listed /
installed / loaded version readbacks on Claude Code and Codex are not done or claimed by this
preparation. The merge, tag, GitHub Release and host-update STOPs each require their own
explicit agreement ([release skill](../skills/release/SKILL.md), steps 5–8); readbacks and
their release-ticket record are specified by steps 7–10. This page supplies no review verdict
or CI result.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.9.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.8.1` to `2.9.0`; no other manifest field changed |
| Dated changelog and the eleven fragments | Updated: byte-identical entries assembled into `2.9.0 — 2026-10-30`; fragments removed |
| Release note and migration guide | New: this page and [migration-2.9.0.md](migration-2.9.0.md) |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.8.0` marked published and tagged, `2.9.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.9.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; the source documentation of each issue is linked above |
