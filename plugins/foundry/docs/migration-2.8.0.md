# Foundry 2.8.0 — upgrade and rollback, 2026-10-28

**Guide for the PLP-339 source candidate, from 2.7.0.** See the
[release note](release-2.8.0.md) for scope and preparation status.
Tag, host installation and listed / installed / loaded versions are not established by
this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. `routing kpi-publish` reads its input file and writes no local file
([kpi_publish.py](../tooling/foundry/kpi_publish.py)).

One configuration key is added: the secret `DEVHUB_METRICS_TOKEN`, used only by
`routing kpi-publish`. Its resolution order, its storage with
`configure credential --name DEVHUB_METRICS_TOKEN` and the DevHub scope it needs are in
[kpi-export.md](kpi-export.md#publication-to-devhub-plp-325). Nothing else reads it, and
no hook, gate, merge or campaign runs `routing kpi-publish` (same page). A missing token
changes nothing until the verb is run; the verb then refuses with exit status 2 and sends
nothing.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.8.0 on either host. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#7-stop--host-update). The marketplace name
`patolabs` is the one read on 2026-10-28 from both hosts' marketplace listings for
[migration-2.7.0.md](migration-2.7.0.md); it was not read again for this page.

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 7–9, with `2.8.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.7.0: not qualified

An official-manager downgrade to 2.7.0 has not been exercised here, and no reverse
migration is provided. Deduced from the diff between the tag `foundry-v2.7.0` and this
candidate, not run: 2.7.0 has no `routing kpi-publish` verb and does not know the key
`DEVHUB_METRICS_TOKEN`, so a token stored in the keychain stays there unused; this release
adds no field to any ledger or receipt that 2.7.0 reads. Records already published to DevHub
stay there; a return to 2.7.0 does not withdraw them.
