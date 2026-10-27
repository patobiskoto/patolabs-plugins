# Foundry 2.6.0 — upgrade and rollback, 2026-10-27

**Guide for the PLP-327 source candidate, from 2.5.0.** See the
[release note](release-2.6.0.md) for scope and preparation status.
Tag, host installation and listed / installed / loaded versions are not established by
this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. No configuration key is added or changed.

After the upgrade, routed implementer and reviewer invocations that carry an issue are
appended to a new local file under the data directory, described in
[Local invocation record](model-routing.md#local-invocation-record-and-reviewed-diff-attribution-plp-316).
Invocations made before the upgrade are not recorded and cannot be recovered:
`routing review-turns` reports their reviewer model as `null` with a reason, and
`routing kpi-export` emits no `attempt` record for them.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.6.0 on either host. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#7-stop--host-update).

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 7–9, with `2.6.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.5.0: not qualified

An official-manager downgrade to 2.5.0 has not been exercised here, and no reverse
migration is provided. Foundry 2.5.0 has no `invocation_log` module (deduced from its
source tree): the invocation record written by 2.6.0 would stay on disk, unread and no
longer appended to. That behaviour is not verified. Preserve the record under the
[rollback rules](migration-1.0.0.md#overrides-and-rollback-scope).
