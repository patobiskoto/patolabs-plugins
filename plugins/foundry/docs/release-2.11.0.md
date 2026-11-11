# Foundry 2.11.0 — release note, 2026-11-11

**Source candidate prepared under PLP-378.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.11.0`.
The minor-release requirements are `release_kind` and `release_requirements` in
[test_release_contract.py](../tests/test_release_contract.py); upgrade references are in
[migration-2.11.0.md](migration-2.11.0.md). This note covers the Claude Code and Codex hosts.

## Behaviour change to read first

`edit unlink <SRC-ID> <link-type> <DST-ID>` is a new CLI verb (PLP-363): it removes a
`depends-on` or `blocks` dependency on DevHub only. `unlink` in
[write.py](../tooling/foundry/write.py) refuses it before any tracker call on a tracker that does
not declare `link_removal_supported`; the capability per provider is in
[tracker-contract.md](tracker-contract.md). Four behaviours also change: the DevHub audit
accepts a `null` `reason` (PLP-374), a deferral replay and the follow-up cap (PLP-372), the text
of an `issue merge` refusal and of the override audit (PLP-371), and the amounts and groups of
`routing kpi-export` (PLP-370). What each means for a user is in
[migration-2.11.0.md](migration-2.11.0.md#what-changes-for-a-user).

## Scope and sources

The `2.11.0` section of the [CHANGELOG](../CHANGELOG.md) contains PLP-376, PLP-374, PLP-373,
PLP-372, PLP-371, PLP-370 and PLP-363, assembled byte-for-byte by
[`assemble`](../scripts/changelog_assemble.py). This preparation removes those seven fragments
and leaves `Unreleased` empty.

| Issue | Packaged artifact | Behaviour and evidence source |
| --- | --- | --- |
| PLP-363 | `edit unlink` | [edit.py](../tooling/foundry/edit.py); [write.py](../tooling/foundry/write.py); [base.py](../tooling/foundry/trackers/base.py); [devhub.py](../tooling/foundry/trackers/devhub.py); [tracker-contract.md](tracker-contract.md) |
| PLP-374 | DevHub audit with a `null` `reason` | [devhub.py](../tooling/foundry/trackers/devhub.py) (`DevHubTracker.audit`) |
| PLP-372 | Follow-up choice of a deferral and cap with an empty reference | [campaign_defer.py](../tooling/foundry/campaign_defer.py) (`_find_follow_up`, `_carries`) |
| PLP-371 | Refusal and override audit text of `issue merge`; merge-pr skill and R9 wording | [issue.py](../tooling/foundry/issue.py); [Merge gate since PLP-368](review-rounds.md#merge-gate-since-plp-368-plp-adr-0064); [merge-pr skill](../skills/merge-pr/SKILL.md); [process-contract-r9.md](process-contract-r9.md) |
| PLP-370 | Coordinator dedup, provenance and served-model reason in `routing kpi-export` | [kpi_export.py](../tooling/foundry/kpi_export.py); [token_attribution.py](../tooling/foundry/token_attribution.py); [kpi-export.md](kpi-export.md); [cost-attribution.md](cost-attribution.md) |
| PLP-373, PLP-376 | Documentation only | [tracker-contract.md](tracker-contract.md); [devhub-tracker.md](devhub-tracker.md); [review-rounds.md](review-rounds.md); [process-contract-r9.md](process-contract-r9.md); [release skill](../skills/release/SKILL.md) |

## Publication status

Release merge, tag `foundry-v2.11.0`, GitHub Release, host installation, and listed /
installed / loaded version readbacks on Claude Code and Codex are not done or claimed by this
preparation. The merge, tag, GitHub Release and host-update STOPs each require their own
explicit agreement ([release skill](../skills/release/SKILL.md), steps 5–8); readbacks and
their release-ticket record are specified by steps 7–10. This page supplies no review verdict
or CI result. The triage of the open follow-up issues required before this PR
([release skill](../skills/release/SKILL.md), step 4) is recorded on the release ticket
PLP-378, not here.

The changelog section and these release pages are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.11.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status |
| --- | --- |
| Both manifest versions | Updated from `2.10.0` to `2.11.0`; no other manifest field changed |
| Dated changelog and the seven fragments | Updated: byte-identical entries assembled into `2.11.0 — 2026-11-11`; fragments removed |
| Release note and migration guide | New: this page and [migration-2.11.0.md](migration-2.11.0.md) |
| Root and Foundry READMEs | Updated: source-candidate status, version, release/migration pointers and release-version list |
| `scripts/ci_plan.py` release-version comment | Updated: `2.10.0` marked published and tagged, `2.11.0` added; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: `2.11.0` entry added; earlier entries retained |
| CLI, configuration and behaviour implementation | No change in this preparation; the source documentation of each issue is linked above |
