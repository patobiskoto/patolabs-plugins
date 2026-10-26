# Foundry 2.4.0 — upgrade and rollback, 2026-10-25

**Guide for the PLP-313 source candidate, from 2.3.0.** See the
[release note](release-2.4.0.md) for scope and preparation status.
Host update, tag, listed version, installed manifest version, loaded version and live
consumer qualification are not established for 2.4.0 by this preparation.

## State and technical completion

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. This preparation changes no operator configuration or real ledger.

The `routing escalation complete-technical` contract and its isolated copy recipe are
in [model-routing.md](model-routing.md#complete-a-reviewed-technical-diagnostic-before-remaining-qualification).
Its `provider_effect_allowed=false`, `campaign_restart_allowed=false` and
`product_accepted=false` results do not authorize external effects or accept uncovered
criteria (same contract). A source test of that recipe is not live qualification of a
consumer application.

## Official host procedures: references, not executed here

The command strings below identify the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and the [2.3.0 configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They were not run for this preparation, on either host; an installation or downgrade
exercise remains unverified. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#7-stop--host-update).

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 7–9, with `2.4.0` as the
expected version. The manager listing, installed manifest and version loaded by a
session remain separate observations for each host; none is supplied by this source
candidate. Earlier upgrade prerequisites remain in
[migration-2.3.0.md](migration-2.3.0.md) and its linked guides.

## Return to 2.3.0: not qualified

The official-manager rollback remains untried (PAT-162), as recorded in
[migration-2.3.0.md](migration-2.3.0.md#rollbacks); this guide establishes no command
for installing an older marketplace tag.

**Ledger incompatibility, deduced from the source:** `EscalationStore._new_state` and
`EscalationStore._normalize_state` in [escalation.py](../tooling/foundry/escalation.py)
include `technical_completion_audit`, even when it is empty; `_locked` serializes the
normalized state when a ledger is created or changed. The 2.3.0 source at
`foundry-v2.3.0:plugins/foundry/tooling/foundry/escalation.py`
(`_LEDGER_OPTIONAL_KEYS`, `EscalationStore._normalize_state`) does not allow this key
and rejects unknown root keys. Thus a ledger created or rewritten by this source can
be rejected by 2.3.0 even when no technical diagnostic has been completed. A read-only
`status` normalization alone does not write that field (`EscalationStore.status`).

Compatibility after such a write is not established by replacing the package; an
official host downgrade has not been exercised. No reverse migration is provided by
this release preparation. Preserve the newer ledger and its audits; deleting
`technical_completion_audit` or restoring an older ledger over newer evidence is not a
rollback procedure qualified here ([preservation rules](migration-1.0.0.md#overrides-and-rollback-scope)).
