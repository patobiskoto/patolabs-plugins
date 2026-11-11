# Foundry 2.10.0 — upgrade and rollback, 2026-11-11

**Guide for the PLP-369 source candidate, from 2.9.1.** See the
[release note](release-2.10.0.md) for scope and preparation status.
Tag, GitHub Release, host installation and listed / installed / loaded versions are not
established by this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. From this release the review proofs kept there are required by every code merge
(next section): a merge run with another data directory, or on another machine, does not see
them. No new file, option or configuration key is added by this release (read from the diff
between the tag `foundry-v2.9.1` and this candidate: no `add_argument` and no environment read
is added under `tooling/`).

## What changes for a user

- **`issue merge` requires a recorded review proof on every provider (PLP-368,
  PLP-ADR-0064).** Before the code-host merge it reads the local proof store and requires a
  completed review proof for the exact issue criteria, head, base and diff. On DevHub, Linear
  and GitHub Projects, checking every acceptance box no longer replaces it: the proof must then
  be `mergeable` with every criterion `pass`. The override
  `--allow-incomplete-ac --ac-override-reason=<code>` now requires a completed proof on the same
  coordinates too, whatever its quality and verdicts. Rule, refusals and tests:
  [review-rounds.md](review-rounds.md#merge-gate-since-plp-368-plp-adr-0064).
- **When a merge is refused.** Run the review of `foundry:merge-pr`
  ([merge-pr skill](../skills/merge-pr/SKILL.md), step 2) on the current head of the PR, with
  the same data directory as the merge, so that `routing record-review-proof` records the
  proof; then run `issue merge` again. If a criterion can only be met after the merge, use the
  override, which needs that recorded proof as well. An issue with no readable acceptance
  criterion cannot be merged (same page). A PR whose review was recorded on another machine
  needs a review recorded on the merging machine.
- **Follow-up issues of deferred remarks are capped at 30 remarks (PLP-361,
  PLP-ADR-0065).** A follow-up issue holding 30 remarks or more is full: neither the
  `foundry:merge-pr` deferral nor the campaign `defer-remarks` effect adds to it, and the next
  deferral creates a new follow-up issue in the same companion Epic (`MAX_FOLLOW_UP_REMARKS`,
  `remark_count` in [campaign_defer.py](../tooling/foundry/campaign_defer.py)). Remarks are
  counted only in deferral comments of the new format described in the
  [merge-pr skill](../skills/merge-pr/SKILL.md) and in
  [review-rounds.md](review-rounds.md#reading-the-deferred-remarks-debt-plp-361-plp-adr-0065);
  older comments are not counted.
- **`foundry:release` stops for a triage before a minor or major release PR.** The
  maintainer triages each open deferred remark (kept as an issue, or dropped)
  ([release skill](../skills/release/SKILL.md), step 4). No code refuses a release without it.
- **KPI export.** `routing kpi-export`, and the publication at `issue merge`, emit one
  `coordinator` attempt per issue and host session (PLP-357,
  [kpi-export.md](kpi-export.md#coordinator-session-plp-357)) and set the status, end time and
  alias price of attempts from the host log (PLP-358,
  [kpi-export.md](kpi-export.md#status-alias-price-and-review-type-outside-a-campaign-plp-358)).
  A changed attempt is published as a revision; contract `devhub-observations.v2` is unchanged.
- **Process contract.** In this monorepo `AGENTS.md` / `CLAUDE.md` keep short rules R1 to R9;
  the full text is in [process-contract-r1-r6.md](process-contract-r1-r6.md),
  [process-contract-r7.md](process-contract-r7.md) and
  [process-contract-r9.md](process-contract-r9.md) (PLP-362). Other repositories are not
  affected.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.10.0 on either host. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#8-stop--host-update). The marketplace name
`patolabs` is the one read on 2026-10-28 from both hosts' marketplace listings for
[migration-2.7.0.md](migration-2.7.0.md); it was not read again for this page.

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 8–10, with `2.10.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.9.1: not qualified

An official-manager downgrade to 2.9.1 has not been exercised here, and no reverse
migration is provided. Deduced from the diff between the tag `foundry-v2.9.1` and this
candidate, not run: 2.9.1 merges on DevHub, Linear and GitHub Projects without reading a
review proof when every acceptance box is checked, so a return removes the PLP-368 gate; it
does not apply the follow-up cap; KPI records already published by 2.10.0 stay on DevHub, and
a later 2.9.1 publication emits no `coordinator` attempt.
