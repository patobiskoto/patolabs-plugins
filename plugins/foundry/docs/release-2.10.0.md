# Foundry 2.10.0 — release note, 2026-11-11

**Source candidate prepared under PLP-369.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.10.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.10.0.md](migration-2.10.0.md). This note covers the Claude Code and Codex hosts.

## Behaviour change to read first

`issue merge` now refuses a code merge, on every provider, unless the local proof store holds
a completed review proof bound to the exact issue criteria, head, base and diff
(PLP-368, PLP-ADR-0064). On DevHub, Linear and GitHub Projects, checked acceptance boxes no
longer replace that proof, and the override `--allow-incomplete-ac --ac-override-reason=<code>`
requires one too. The rule, its refusals and its tests are in
[review-rounds.md](review-rounds.md#merge-gate-since-plp-368-plp-adr-0064); what to do when a
merge is refused is in [migration-2.10.0.md](migration-2.10.0.md#what-changes-for-a-user).

## Scope and sources

The `2.10.0` section of the [CHANGELOG](../CHANGELOG.md) contains PLP-368, PLP-362, PLP-361,
PLP-360, PLP-359, PLP-358 and PLP-357, assembled byte-for-byte by
[`assemble`](../scripts/changelog_assemble.py). This preparation removes those seven fragments
and leaves `Unreleased` empty.

| Issue | Packaged artifact | Behaviour and evidence source |
| --- | --- | --- |
| PLP-368 | Merge gate of `issue merge` | [Merge gate since PLP-368](review-rounds.md#merge-gate-since-plp-368-plp-adr-0064); [issue.py](../tooling/foundry/issue.py); [merge-pr skill](../skills/merge-pr/SKILL.md) |
| PLP-361 | Cap of a follow-up issue (`MAX_FOLLOW_UP_REMARKS`, `remark_count`); deferral comment format; triage STOP of `foundry:release` | [Reading the deferred-remarks debt](review-rounds.md#reading-the-deferred-remarks-debt-plp-361-plp-adr-0065); [campaign_defer.py](../tooling/foundry/campaign_defer.py); [merge-pr skill](../skills/merge-pr/SKILL.md); [release skill](../skills/release/SKILL.md), step 4 |
| PLP-357 | `coordinator` attempt records in `routing kpi-export` and in the publication at `issue merge` | [Coordinator session (PLP-357)](kpi-export.md#coordinator-session-plp-357); [kpi_export.py](../tooling/foundry/kpi_export.py); [token_attribution.py](../tooling/foundry/token_attribution.py) |
| PLP-358 | Status, end time and alias price of attempts in `routing kpi-export` | [Status, alias price and review type outside a campaign (PLP-358)](kpi-export.md#status-alias-price-and-review-type-outside-a-campaign-plp-358); [kpi_export.py](../tooling/foundry/kpi_export.py) |
| PLP-362 | Process contract `AGENTS.md` / `CLAUDE.md` and its detail pages | [process-contract-r1-r6.md](process-contract-r1-r6.md); [process-contract-r7.md](process-contract-r7.md); [process-contract-r9.md](process-contract-r9.md); [session-context-load.md](session-context-load.md) |
| PLP-359, PLP-360 | Documentation only | [review-rounds.md](review-rounds.md) |

PLP-ADR-0064 and PLP-ADR-0065 are the decisions behind PLP-368 and PLP-361; their text is in
the tracker (`query adr <ADR-ID>`), not in this repository.

## Publication status

Release merge, tag `foundry-v2.10.0`, GitHub Release, host installation, and listed /
installed / loaded version readbacks on Claude Code and Codex are not done or claimed by this
preparation. The merge, tag, GitHub Release and host-update STOPs each require their own
explicit agreement ([release skill](../skills/release/SKILL.md), steps 5–8); readbacks and
their release-ticket record are specified by steps 7–10. This page supplies no review verdict
or CI result. The triage of the open follow-up issues required before this PR
([release skill](../skills/release/SKILL.md), step 4) is recorded on the release ticket
PLP-369, not here.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.10.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.9.1` to `2.10.0`; no other manifest field changed |
| Dated changelog and the seven fragments | Updated: byte-identical entries assembled into `2.10.0 — 2026-11-11`; fragments removed |
| Release note and migration guide | New: this page and [migration-2.10.0.md](migration-2.10.0.md) |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.9.1` marked published and tagged, `2.10.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.10.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; the source documentation of each issue is linked above |
