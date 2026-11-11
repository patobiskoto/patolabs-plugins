# Foundry 2.11.0 — upgrade and rollback, 2026-11-11

**Guide for the PLP-378 source candidate, from 2.10.0.** See the
[release note](release-2.11.0.md) for scope and preparation status.
Tag, GitHub Release, host installation and listed / installed / loaded versions are not
established by this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. No new data file or configuration key is added by this release (read from the diff
between the tag `foundry-v2.10.0` and this candidate: no environment read and no
`add_argument` is added under `tooling/`; the new verb `unlink` is registered in `_COMMANDS` of
[edit.py](../tooling/foundry/edit.py)). Whether a configuration key outside `tooling/` changed is
unknown: it was not searched.

## What changes for a user

- **New verb `edit unlink` works on DevHub only (PLP-363).**
  `edit unlink <SRC-ID> <link-type> <DST-ID>` removes a `depends-on` or `blocks` dependency
  ([edit.py](../tooling/foundry/edit.py)). On every other tracker `unlink` in
  [write.py](../tooling/foundry/write.py) refuses it before any tracker call
  (`link_removal_supported`); another link type is refused as well. The capability per provider
  is in [tracker-contract.md](tracker-contract.md); a 409 `resource_conflict` on a rerun after a
  lost response is explained in [devhub-tracker.md](devhub-tracker.md) (PLP-376).
- **DevHub audit accepts a `null` `reason` (PLP-374).** `DevHubTracker.audit` in
  [devhub.py](../tooling/foundry/trackers/devhub.py) keeps it as `None`; an empty or other
  non-string `reason` is still refused as `invalid_response`.
- **Deferral follow-up choice (PLP-372).** A replayed deferral goes to the follow-up issue that
  already carries its reference, and an empty reference no longer lifts the 30-remark cap
  (`_find_follow_up`, `_carries` in [campaign_defer.py](../tooling/foundry/campaign_defer.py)).
  `_find_follow_up` now requires `scan_orphans` and `ref`: a caller outside this repository
  that omits them fails (deduced from the signature, not run).
- **Merge refusal and override audit text (PLP-371).** The `issue merge` refusal for lack of
  review proof names the data directory read (`_proof_data_dir_note` in
  [issue.py](../tooling/foundry/issue.py)); the override audit text no longer says the
  acceptance criteria are incomplete when every box is checked. The gate itself is unchanged
  ([review-rounds.md](review-rounds.md#merge-gate-since-plp-368-plp-adr-0064)).
- **KPI export (PLP-370).** Two main logs of one host session id no longer count the same
  coordinator request twice, the provenance of `amount` and the reason of an absent or invalid
  served model change as written in the CHANGELOG section; rule and limits are in
  [kpi-export.md](kpi-export.md) and [cost-attribution.md](cost-attribution.md). Amounts
  published by an earlier version are not recomputed by this guide (unknown whether a
  republication replaces them).
- **Documentation only (PLP-373, PLP-376).** No tool change.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.11.0 on either host. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#8-stop--host-update). The marketplace name
`patolabs` was read again on 2026-11-11 from both hosts' marketplace listings
(`claude plugin marketplace list`, `codex plugin marketplace list`).

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 8–10, with `2.11.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.10.0: not qualified

An official-manager downgrade to 2.10.0 has not been exercised here, and no reverse
migration is provided. Deduced from the diff between the tag `foundry-v2.10.0` and this
candidate, not run: 2.10.0 has no `unlink` entry in `_COMMANDS` of `edit.py`, so a return
removes the verb; it keeps the `reason` check that refuses a `null` audit reason and the
cap behaviour of an empty reference described above.
