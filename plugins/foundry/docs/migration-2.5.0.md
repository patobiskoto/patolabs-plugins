# Foundry 2.5.0 — upgrade and rollback, 2026-10-26

**Guide for the PLP-314 source candidate, from 2.4.0.** See the
[release note](release-2.5.0.md) for scope and preparation status.
Tag, host installation, listed / installed / loaded versions, server deployment and
real-project migration are not established by this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. This preparation changes no operator configuration or real ledger.

The server prerequisite is the DEVHUB-133 extension
([PR #121](https://github.com/patobiskoto/dev-hub/pull/121)); a merged prerequisite
does not establish its deployment on the target. Before real use, verify the target's
deployment and qualify DHN-50 as described in
[devhub-requirements.md](devhub-requirements.md#qualification-of-source-numbers-and-historical-rejected-adrs-plp-311).
That page still records DHN-50 as `unknown`; this release preparation does not update
that qualification record or authorize a cutover.

The option, binding retention, exact-coordinate checks, server-refusal behaviour and
historical ADR vocabulary are documented in
[Explicit source issue numbers](devhub-tracker.md#explicit-source-issue-numbers-plp-311-live-qualification-pending)
and [Selective import](devhub-tracker.md#selective-import-pat-319-dhn-34-dhn-36).
Use those contracts for migration planning; no real migration is performed here.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.5.0 on either host or on the isolated
source copy. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#7-stop--host-update).

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 7–9, with `2.5.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.4.0: not qualified

An official-manager downgrade to 2.4.0 has not been exercised here, and no reverse
migration is provided by this preparation. Replacing a package does not establish
compatibility with a migration manifest or binding carrying `preserve_issue_numbers`,
or with a server corpus containing historical `rejected` ADRs; that compatibility is
unverified here. Preserve those records and their evidence under the
[rollback rules](migration-1.0.0.md#overrides-and-rollback-scope).
