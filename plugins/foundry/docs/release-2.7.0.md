# Foundry 2.7.0 — release note, 2026-10-28

**Source candidate prepared under PLP-333.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.7.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.7.0.md](migration-2.7.0.md). This note covers the Claude Code and Codex hosts.

## Scope and sources

The `2.7.0` section of the [CHANGELOG](../CHANGELOG.md) contains PLP-334, PLP-332, PLP-330, PLP-329,
PLP-328, PLP-326 and PLP-14, assembled byte-for-byte by
[`assemble`](../scripts/changelog_assemble.py). This preparation removes those seven
fragments and leaves `Unreleased` empty.

| Issue | Packaged artifact | Behaviour and evidence source |
| --- | --- | --- |
| PLP-326 | Optional `decided_at` on the escalation failure receipt; observed escalation time in `routing kpi-export` | [`escalation` record](kpi-export.md#escalation); [escalation.py](../tooling/foundry/escalation.py); [kpi_export.py](../tooling/foundry/kpi_export.py); [test_kpi_export.py](../tests/test_kpi_export.py) |
| PLP-328 | Price grid entries for the routed Claude 5.5 and Fable 5.1 models; threshold pricing applied per request | [Entrées Claude 5.5 et Fable 5.1 de la grille](cost-attribution.md#entrées-claude-55-et-fable-51-de-la-grille-plp-328); [kpi-export.md](kpi-export.md); [pricing-v1.json](../tooling/foundry/pricing-v1.json); [cost_attribution.py](../tooling/foundry/cost_attribution.py); [kpi_export.py](../tooling/foundry/kpi_export.py); [token_attribution.py](../tooling/foundry/token_attribution.py); [test_cost_attribution.py](../tests/test_cost_attribution.py) |
| PLP-329 | Background Agent launch recorded `unknown`/`unknown` in the local invocation record | [Local invocation record](model-routing.md#local-invocation-record-and-reviewed-diff-attribution-plp-316); [kpi-export.md](kpi-export.md); [observe_agent.py](../hooks/observe_agent.py); [test_invocation_log.py](../tests/test_invocation_log.py) |
| PLP-330 | Test-only: pytest 9.2 compatibility of the mark-expression parser | [conftest.py](../tests/conftest.py); no packaged runtime change |
| PLP-332 | DevHub `edit transition <ID> dropped\|backlog <expected-state>` written only when the state read equals the expected one | [Guarded `dropped` and `backlog` transitions](devhub-tracker.md#guarded-dropped-and-backlog-transitions-plp-332); [devhub.py](../tooling/foundry/trackers/devhub.py); [test_devhub_tracker.py](../tests/test_devhub_tracker.py); [close-epic skill](../skills/close-epic/SKILL.md) |
| PLP-334 | DevHub transition refusals name their cause; read-back of the `dropped`/`backlog` answer; CI pins `pytest` and `pytest-xdist` | [Guarded `dropped` and `backlog` transitions](devhub-tracker.md#guarded-dropped-and-backlog-transitions-plp-332); [devhub.py](../tooling/foundry/trackers/devhub.py); [test_devhub_tracker.py](../tests/test_devhub_tracker.py); [ci.yml](../../../.github/workflows/ci.yml) |
| PLP-14 | Sonnet 5.5 amount with cache writes marked `partial`; DevHub governance refusal message narrowed in `registry` and `setup_project`; documentation corrections | [kpi-export.md](kpi-export.md); [devhub-tracker.md](devhub-tracker.md); [kpi_export.py](../tooling/foundry/kpi_export.py); [registry.py](../tooling/foundry/registry.py); [setup_project.py](../tooling/foundry/setup_project.py); [test_kpi_export.py](../tests/test_kpi_export.py) |

That the real hook payload of a background launch carries the `isAsync` / `async_launched`
marker that `_is_background_launch` reads is deduced from transcripts, not observed in a
hook payload (`hooks/observe_agent.py`, docstring of `_is_background_launch`).

One compatibility consequence, for a return to 2.6.0 or a host left on it, is stated in
[migration-2.7.0.md](migration-2.7.0.md), "Return to 2.6.0".

## Publication status

Release merge, tag `foundry-v2.7.0`, host installation, and listed / installed / loaded
version readbacks on Claude Code and Codex are not done or claimed by this preparation.
The merge, tag and host-update STOPs each require their own explicit agreement
([release skill](../skills/release/SKILL.md), steps 5–7); readbacks and their release-ticket
record are specified by steps 7–9. This page supplies no review verdict or CI result.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.7.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.6.0` to `2.7.0`; no other manifest field changed |
| Dated changelog and PLP-14, PLP-326, PLP-328, PLP-329, PLP-330, PLP-332, PLP-334 fragments | Updated: byte-identical entries assembled into `2.7.0 — 2026-10-28`; fragments removed |
| Release note and migration guide | New: this page and [migration-2.7.0.md](migration-2.7.0.md) |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.7.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.7.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; the source documentation of PLP-14, PLP-326, PLP-328, PLP-329, PLP-332 and PLP-334 is linked above |
