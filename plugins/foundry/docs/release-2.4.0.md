# Foundry 2.4.0 — release note, 2026-10-25

**Source candidate prepared under PLP-313.** The [Claude Code manifest](../.claude-plugin/plugin.json)
and [Codex manifest](../.codex-plugin/plugin.json) declare `2.4.0`.
The minor-release requirements are defined by `release_kind` and `release_requirements`
in [test_release_contract.py](../tests/test_release_contract.py).
Upgrade references and the rollback limitation are in
[migration-2.4.0.md](migration-2.4.0.md).

## Scope and behaviour sources

The `2.4.0` section of the [CHANGELOG](../CHANGELOG.md) contains the four fragments
PLP-312, PLP-310, PLP-2 and PLP-1, in the order of
[`fragment_paths` and `assemble`](../scripts/changelog_assemble.py), with their bytes
preserved. This preparation removes those four fragments and leaves `Unreleased` empty.

| Entry | Behaviour or evidence source |
| --- | --- |
| PLP-312: `routing escalation complete-technical` | [Completion contract and offline copy recipe](model-routing.md#complete-a-reviewed-technical-diagnostic-before-remaining-qualification); `EscalationStore.complete_technical_remediation` in [escalation.py](../tooling/foundry/escalation.py) and `ReviewDeduplicator.validated_technical_completion` in [routing.py](../tooling/foundry/routing.py) |
| PLP-310: post-cutover observations | Dated [PAT-334 report](qualification/pat-334-cutover.md); these are earlier observations, not 2.4.0 host qualification |
| PLP-2: repository cutover record | Dated [PAT-334 report](qualification/pat-334-cutover.md) |
| PLP-1: Linear comment-reply exception and deferred review remarks | The PLP-1 changelog entry and the "Comment exceptions" item in [DevHub documentation](devhub-tracker.md) |

The completion contract keeps `not_covered` non-passing and reports
`product_accepted=false`, `provider_effect_allowed=false` and
`campaign_restart_allowed=false`
([model-routing.md](model-routing.md#complete-a-reviewed-technical-diagnostic-before-remaining-qualification)).
Remaining application qualification uses the authority and gates named by that
contract; this release preparation supplies no consumer-application qualification result.
PLP-311 and GitHub Release creation (PLP-29) are outside PLP-313's preparation scope.

## Preparation and later evidence

| State | Status for 2.4.0 |
| --- | --- |
| Source candidate | Both manifests, the dated changelog, these two pages, README pointers, the CI comment and the freeze entry |
| Release merge | Pending independent review, CI at the exact commit and the maintainer's separate agreement |
| Tag `foundry-v2.4.0` | Not created by this preparation |
| Host update and listed / installed / loaded versions | Not run or established for 2.4.0 on Claude Code or Codex |
| Live consumer qualification | Not run |
| Official rollback to 2.3.0 | Not run; compatibility is limited as described in the migration guide |

The merge, tag and host-update STOPs each require their own explicit agreement
([release skill](../skills/release/SKILL.md), steps 5–7); later readbacks belong on the
release ticket (steps 8–9). This page supplies neither a review verdict nor a CI result.
The two pages and the changelog section are frozen by `freeze_entry` in
[test_release_contract.py](../tests/test_release_contract.py) and the `2.4.0` entry of
[release-history.json](../tests/fixtures/release-history.json).

## Documentation status (AGENTS.md#R5)

| Touched artifact | Status / source |
| --- | --- |
| Both manifest versions, `2.3.0` → `2.4.0` | Updated here and in the migration guide; no other manifest field changes |
| Dated changelog and four retired fragments | Updated: the `2.4.0 — 2026-10-25` section, assembled without rewriting the entries |
| Release note and migration guide | New: this page and [migration-2.4.0.md](migration-2.4.0.md) |
| Root and Foundry READMEs | Updated: prepared-release links/statuses, current manifest version and migration pointer; the Foundry release list includes 2.4.0 |
| `scripts/ci_plan.py` release-version comment | Updated: 2.4.0 added to the list; executable code unchanged |
| `tests/fixtures/release-history.json` | Updated: 2.4.0 entry only; earlier entries retained |
| CLI verbs/options and configuration keys | No change in this preparation; the packaged completion command is already documented in [model-routing.md](model-routing.md#complete-a-reviewed-technical-diagnostic-before-remaining-qualification), including its authority limits |
| Host upgrade and data/rollback constraints | Updated in [migration-2.4.0.md](migration-2.4.0.md), with unexecuted host procedures and the ledger compatibility limit explicit |

No functional code, skill instruction or other plugin is changed by this preparation.
