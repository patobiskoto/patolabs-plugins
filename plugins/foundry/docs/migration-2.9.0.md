# Foundry 2.9.0 — upgrade and rollback, 2026-10-30

**Guide for the PLP-348 source candidate, from 2.8.1.** See the
[release note](release-2.9.0.md) for scope and preparation status.
Tag, GitHub Release, host installation and listed / installed / loaded versions are not
established by this preparation.

## Prerequisites and data

Preserve `~/.config/foundry` (or the explicit `FOUNDRY_DATA` directory), including
ledgers, receipts and review history; the existing
[data and rollback rules](migration-1.0.0.md#overrides-and-rollback-scope) remain the
reference. One new local file is written there: the ledger of what `routing kpi-publish`
published, `<state dir>/kpi-publish/<hash>.json` (`PublishedLedger`, `kpi_publish.py`;
[kpi-export.md](kpi-export.md#revisions-plp-342)).

## What changes for a user

- **`issue merge` publishes KPI records.** After the `done` transition of a successful
  merge, `issue merge` publishes the KPI records of the merged issue when the tracker of the
  merge declares `kpi_publication_supported` (DevHub only). It prints one line on standard
  output or one warning on standard error, never gates, and leaves the merge output and exit
  code unchanged. The step, its output lines, the cases where it does not run and its limits
  are in [kpi-export.md](kpi-export.md#publication-at-merge-plp-343) (`_publish_kpi_after_merge`
  in `issue.py`, `kpi_merge.py`; PLP-ADR-0063). A campaign runs `issue merge`, so it publishes
  there too (same page).
- **Configuration it needs.** The URL `DEVHUB_URL` and the secret `DEVHUB_METRICS_TOKEN`
  (scope `foundry:metrics:write`), resolved as for `routing kpi-publish`
  ([kpi-export.md](kpi-export.md#publication-to-devhub-plp-325)). Without them the step stops
  before any log is read and prints one warning on standard error
  (`kpi_merge.publish_after_merge`, `kpi_publish.endpoint`). A DevHub repository that already
  has both keys set publishes at its next merge after the upgrade without any new command
  (deduced from `issue.merge` and `publish_after_merge`, not run); to avoid it, leave
  `DEVHUB_METRICS_TOKEN` unset.
- **Manifest option.** The Claude Code manifest now declares `DEVHUB_METRICS_TOKEN`
  (`userConfig`, `sensitive`) in [plugin.json](../.claude-plugin/plugin.json) (PLP-337);
  `configure credential --name DEVHUB_METRICS_TOKEN` stays the other way to store it. The
  Codex manifest declares no such option (read: it has no `userConfig`).
- **`routing kpi-export` finds the logs.** Without `--session` and `--codex-session` it now
  searches the Claude and Codex session logs of the issues itself; the explicit options
  disable the search ([kpi-export.md](kpi-export.md#finding-the-session-logs-plp-342),
  `kpi_sessions.discover`). Nothing found or ambiguous stays `unavailable`.
- **`routing kpi-publish` revisions.** A record whose source changed is sent as a revision
  (`revision` + 1, `supersedes_id`); the report gains `revised`
  ([kpi-export.md](kpi-export.md#revisions-plp-342)). Limits for records published before
  this release are listed there.
- **Export output.** `routing kpi-export` can mark tokens as a lower bound and list more
  attempts in `omitted` (`no_recorded_time`); see
  [kpi-export.md](kpi-export.md#tokens-that-are-a-lower-bound-plp-344) and the PLP-321, PLP-344
  and PLP-345 entries of the [CHANGELOG](../CHANGELOG.md).
- **`foundry:release`.** It has a fourth STOP, GitHub Release, after the tag
  ([release skill](../skills/release/SKILL.md), step 7); the steps after it move up by one,
  and the record on the release ticket gains the Release URL and state.

## Official host procedures: references, not executed here

The strings below reference the existing
[official upgrade procedures](migration-1.0.0.md#official-installation-or-upgrade)
and [configuration/doctor guidance](migration-2.3.0.md#configuration-and-doctor-after-the-upgrade).
They are examples, not procedures run for 2.9.0 on either host. The host-update STOP is in the
[release skill](../skills/release/SKILL.md#8-stop--host-update). The marketplace name
`patolabs` is the one read on 2026-10-28 from both hosts' marketplace listings for
[migration-2.7.0.md](migration-2.7.0.md); it was not read again for this page.

| Host | Existing procedure strings |
| --- | --- |
| Claude Code | `/plugin marketplace update patolabs`; `/plugin update foundry@patolabs`; `/reload-plugins`; `/foundry:configure`; `/foundry:doctor` |
| Codex | `codex plugin marketplace upgrade patolabs`; `codex plugin remove foundry@patolabs`; `codex plugin add foundry@patolabs`; `$foundry:configure`; `$foundry:doctor` |

To verify the version after an authorized update, use the
[loaded-version evidence requirements](migration-1.0.0.md#verify-the-version-actually-loaded)
and [release readbacks](../skills/release/SKILL.md), steps 7–10, with `2.9.0` as the
expected version. Manager listing, installed manifest and session-loaded version
remain separate observations per host; none is supplied by this preparation.

## Return to 2.8.1: not qualified

An official-manager downgrade to 2.8.1 has not been exercised here, and no reverse
migration is provided. Deduced from the diff between the tag `foundry-v2.8.1` and this
candidate, not run: 2.8.1 does not publish at merge, so a return stops that publication; the
ledger `<state dir>/kpi-publish/<hash>.json` written by 2.9.0 stays on disk, and whether
2.8.1 ignores it is not checked. Records already published to DevHub stay there; a return
does not withdraw them.
