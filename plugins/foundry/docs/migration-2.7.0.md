# Foundry 2.7.0 — upgrade and rollback, 2026-10-28

**Guide for the PLP-333 source candidate, from 2.6.0.** See the
[release note](release-2.7.0.md) for scope and preparation status.
Tag, host installation and listed / installed / loaded versions are not established by
this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. No configuration key is added or changed (none of the four
`changelog.d` fragments of this release names one).

After the upgrade, every escalation failure receipt written for an `escalated` decision
carries `decided_at` ([`escalation` record](kpi-export.md#escalation)). Receipts written
before the upgrade have none and stay readable. A background Claude Agent launch is
recorded `unknown`/`unknown` in the local invocation record from then on; records already
written are not rewritten.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.7.0 on either host. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#7-stop--host-update). The marketplace name
`patolabs` was read on 2026-10-28 from both hosts' marketplace listings (`claude plugin marketplace list`, `codex plugin marketplace list`).

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 7–9, with `2.7.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.6.0: not qualified, and unsafe once a new escalation receipt exists

An official-manager downgrade to 2.6.0 has not been exercised here, and no reverse
migration is provided. Foundry 2.6.0 and earlier accept only the four failure receipt keys
(`set(receipt) != _FAILURE_RECEIPT_KEYS` in their `escalation.py`), so they treat an
escalation ledger holding a receipt with `decided_at` as invalid, and routing fails closed
for that issue (deduced from the source of the tags `foundry-v2.0.0`, `foundry-v2.3.0`, `foundry-v2.5.0` and `foundry-v2.6.0`, which all carry that check; other earlier versions not checked; not run; sources: [kpi-export.md](kpi-export.md#escalation)
and the PLP-326 fragment, assembled in the CHANGELOG). The same holds when one host runs
2.6.0 and the other 2.7.0 against the same data directory, once a new escalation receipt
exists. Do not return to 2.6.0 or earlier, nor leave one host on it, after the first new
escalation receipt; preserve the data under the
[rollback rules](migration-1.0.0.md#overrides-and-rollback-scope).
