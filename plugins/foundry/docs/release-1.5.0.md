# Foundry 1.5.0 — release note, 2026-10-17

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation or a version loaded by a host: those states come after the merge
and are recorded on the release ticket (`skills/release/SKILL.md`, step 9), not
here. Both host manifests (Claude Code and Codex) declare `1.5.0`. Ship-iOS retains its
independent `0.3.0` version. The versionless marketplace catalogues continue to point
at `./plugins/foundry` and `./plugins/ship-ios`.

1.5.0 packages what was merged on `main` after the tag `foundry-v1.4.0`: 19 commits,
PR #162 to #180, listed [at the end of this page](#merged-issues-in-this-release). It is a
minor version, not a patch, because it adds CLI verbs and changes behaviour:
`skills/release/SKILL.md` step 1 calls for a minor when a CLI verb or option, a
configuration key or a behaviour changes. The version number comes from the brief of
PAT-233, not from a decision recorded in this repository. This page announces **no
gain**: no time, cost, quota or quality improvement is measured by it or claimed. Upgrade
and rollback instructions are in [migration-1.5.0.md](migration-1.5.0.md).

## Behaviour changes and risks

Read this section before upgrading. Each item states what changed and points to the page
or the changelog entry that holds the detail; this page does not restate what a tool
launches, loads or writes.

### 1. Epic campaigns: approval, next step, effects (PAT-215, PAT-216, PAT-217, PAT-218, PAT-141)

New verbs: `campaign-approval approve|status|check` (PAT-215,
[campaign-approval.md](campaign-approval.md)), `campaign-cadence next` (PAT-216,
[campaign-cadence.md](campaign-cadence.md)) and `campaign-cadence step|run` (PAT-217,
PAT-218, PAT-141, [campaign-effects.md](campaign-effects.md)), and `routing
record-review-remarks` (PAT-141).

- **A campaign needs a human approval first.** `campaign-approval approve` reads its
  confirmation from the terminal and is refused to an agent session; the Claude
  `PreToolUse` Bash guard denies it in every repository, registered or not
  ([campaign-approval.md](campaign-approval.md), "An agent cannot approve (layers and
  limit)"; `AGENTS.md#R1`). The limits of that refusal are on that page.
- **`campaign-cadence step|run` runs real effects** on the tracker, git and the code host
  once an approval exists: the list of effects and their bounds are in
  [campaign-effects.md](campaign-effects.md), sections "Effects" and "Bounds". Claude
  Code is the only host wired there.
- **New local files** in the Foundry data directory: the page and constant for each are
  listed in [migration-1.5.0.md](migration-1.5.0.md), "New local files".

### 2. Campaign sessions run in the native Claude Code sandbox (PAT-220, PAT-223)

The implementation and correction sessions of a campaign are confined by the native
sandbox settings described in [campaign-effects.md](campaign-effects.md) ("The native
sandbox"). What one live session observed, and what was not tested, is in the changelog
entry of PAT-220 and in
[qualification/pat-220-sandbox-trial-2026-10-17.json](qualification/pat-220-sandbox-trial-2026-10-17.json):
the refusing layer was not recorded and several surfaces are unknown. The reviewer session
stays outside the sandbox. PAT-223 filters credential shapes out of the stored summary
(`campaign_effects.mask_credentials`); a credential of an unknown shape is not masked
(unknown).

### 3. Linear: `start` Git automation and `issue openpr` (PAT-230)

On Linear, `issue openpr` no longer fails when the native State returns from In Review to
In Progress right after the write; the exact acceptance rule is in
[linear-tracker.md](linear-tracker.md) (PAT-230 paragraph). That the team's `start` Git
automation causes it is deduced, not observed; no Linear configuration is changed by this
release.

### 4. The Epic preview works on Linear (PAT-178)

`epic-preview` is an existing verb. On Linear it now builds the neutral
`foundry-epic-subgraph.v1` ([epic-preview.md](epic-preview.md)). The result gains
`structure_digest`; `authority.tracker` already existed in 1.4.0 and now carries the
tracker name (changelog entry of PAT-178).

### 5. Acceptance of an issue with no code (PAT-206)

`issue accept <ID> --human-verdict=accepted --decision-file=<path>` and read-only `issue
accept <ID> --status` exist for Linear only; other providers refuse before any read. The
decision-file keys and the receipt are in [linear-tracker.md](linear-tracker.md). The
sequence ran on the test double only, never on a live workspace.

### 6. Smaller changes

- **Reporting (PAT-228):** every step of `campaign-cadence step|run` reports `requests`;
  details and the stated limit are in [linear-request-cost.md](linear-request-cost.md)
  ("Epic campaign (PAT-228)").
- **Remarks (PAT-229, PAT-227):** a failed `defer-remarks` keeps its real error; in an Epic
  campaign, when every "fix before merge" remark targets only the PR description, the description is
  replaced without a new review, a residual risk stated in the changelog entry of PAT-227.
- **Approval record (PAT-222):** `approve` validates the whole record before appending.
- **Historical ADR links (PAT-209):** `adr link-issue` accepts three more stored bodies
  ([qualification/pat-201-historical-adr-links.md](qualification/pat-201-historical-adr-links.md)).
- **Helper script (PAT-212):** `scripts/codex_profile_provenance.py` is changed (changelog
  entry of PAT-212).
- **Documentation and tests only:** PAT-219
  ([qualification/pat-219-epic-campaign-2026-10-17.md](qualification/pat-219-epic-campaign-2026-10-17.md)),
  PAT-224, PAT-232.

## What 1.5.0 contains since 1.4.0

The `1.5.0` section of the [changelog](../CHANGELOG.md) holds the 19 entries, ordered by
issue number descending. No configuration key, routing default, model or effort changes.

## What this release does not claim

- No gain: PAT-219 records Linear request counts and cost of one real campaign and
  states that where the excess comes from is unknown.
- No claim that a campaign is safe against a same-user process: the changelog entries
  of PAT-215, PAT-217 and PAT-218 state the limits.
- No run of the host commands of `foundry:release`, and no tag, installation or loaded
  version (see "Release phases and evidence").
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **Sandbox of campaign sessions:** observed once, refusing layer not recorded; Glob/Grep,
  reads, `commondir`, `config.worktree`, `refs/replace`, the network and the three user
  files named in the PAT-220 entry are not tested (unknown).
- **Campaign cost:** far above the PAT-177 estimate of Linear requests in the one recorded campaign
  (PAT-219); the cause is unknown.
- **Codex** is not wired into campaign effects.
- **Rollback to an earlier plugin version is an untried procedure** (PAT-162); see
  [migration-1.5.0.md](migration-1.5.0.md).
- **Native availability of the rollback profiles of PAT-17: unknown** (PAT-198).

## Release phases and evidence

1. **This pull request prepares; it does not publish.** It bumps the two manifests,
   opens the `1.5.0 — 2026-10-17` changelog section from the 19 fragments (which it
   deletes), adds this note and the migration guide, and adds the freeze entry. Merge
   only through `foundry:merge-pr` after independent review on the exact diff and
   exact-head CI on two sources (`AGENTS.md` R1, R4). This page does not supply the
   review or the CI result.
2. **The gated merge is the moment from which a host can receive the version.** The
   catalogues carry no version and no ref, so a host that runs an official update after
   the merge is expected to receive manifests saying `1.5.0`, before any tag exists.
   This is derived and **not observed**.
3. **The tag is the named reference, not the delivery.** `foundry-v1.5.0` is to be placed
   on the exact merged commit, on the maintainer's explicit authorization (STOP of
   `skills/release/SKILL.md`). This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a
   new session; read back the version actually loaded, each host separately. The result
   goes to the release ticket, not to this page.
5. **Freeze:** this page, the migration guide and the `1.5.0` changelog section are
   frozen by digest from the release pull request on. A later correction goes to an
   errata page.

Nothing is declared "tagged" or "installed" by this diff.

## Merged issues in this release

Established from `git log --first-parent foundry-v1.4.0..HEAD` on `main` at `cffe064`
(19 commits). Each row is one squash commit, in merge order; the issue is the one named
in the subject, `none in subject` where the subject names none (the fragment names it).
Subjects are translated from French where the commit subject is French.

| Issue | Commit | PR | Subject |
| --- | --- | --- | --- |
| PAT-212 | `0e49858` | #162 | Settle the deferred remarks of PAT-153 from PAT-196 |
| none in subject (PAT-209) | `eb2d0db` | #163 | Qualify the historical bodies of FOUNDRY-ADR-0003, 0015 and 0017 for issue links |
| PAT-206 | `f068132` | #164 | Accept a non-code issue by explicit human decision |
| PAT-178 | `a3560bb` | #165 | Provide the Epic preview for Linear |
| PAT-215 | `0c926c8` | #166 | Record the human approval of an Epic campaign |
| PAT-216 | `90cfbe2` | #167 | Compute the next step of an Epic campaign without effect |
| PAT-217 | `de5a21c` | #168 | Execute campaign effects: start, implement, open the PR, merge |
| PAT-218 | `1a1bacf` | #169 | Review and correct in a campaign |
| PAT-141 | `ddcda4e` | #170 | Let Epic campaigns carry non-blocking remarks and create the companion Epic |
| PAT-220 | `1be0a23` | #171 | Confine the campaign implementation session in the native sandbox |
| PAT-222 | `e5467a2` | #172 | Validate the approval record before appending it to the journal |
| PAT-223 | `d8ff602` | #173 | Mask credentials in the campaign session summary |
| PAT-224 | `0f20e16` | #174 | Correct three statements of the campaign documentation |
| PAT-219 | `0389985` | #175 | Qualify a real Epic campaign on this repository |
| PAT-230 | `2c464e3` | #176 | Tolerate in a campaign the State return set by the Linear GitHub automation on PR open |
| PAT-229 | `625d254` | #177 | Keep the real error and partial writes of a failed remarks deferral |
| PAT-227 | `af11256` | #178 | Correct in a campaign a remark that targets only the PR description |
| PAT-232 | `986c49c` | #179 | Approve at the real clock in the approval CLI test |
| PAT-228 | `cffe064` | #180 | Measure then reduce the Linear requests of an Epic campaign |

The full entries, with their limits, are the `1.5.0` section of the
[changelog](../CHANGELOG.md). This release preparation changes version metadata and
documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`1.4.0` → `1.5.0`) | Updated; stated here and in migration-1.5.0.md. No other manifest key changed |
| Dated Foundry changelog | `1.5.0 — 2026-10-17` section opened from the 19 fragments, which are deleted; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-1.5.0.md |
| `tests/fixtures/release-history.json` | Updated: the `1.5.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, the statement that the tag, the installation and the loaded-version readback are not done here, and the Release rule's version list in the Foundry README |
| `scripts/ci_plan.py` comment | Updated: its list of release versions includes 1.5.0; no behaviour changes |
| `changelog.d/README.md` | Not necessary: unchanged, the fragment convention is already documented there (PAT-210) |
| Ship-iOS manifests, README and changelog | Not necessary: no file under `plugins/ship-ios` is changed and its version does not move |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes, and no rule of the contract states a Foundry version |
| `docs/campaign-*.md`, `docs/linear-tracker.md`, `docs/linear-request-cost.md`, `docs/epic-preview.md`, qualification pages | Not necessary: each already documents, since its own issue, the surface this page summarises; this release changes none of them |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or skill
instruction is changed by this release preparation, and no file under `tooling/`, `hooks/`
or `agents/` is touched. Outside release metadata and these pages, the only changes are one
comment line in `scripts/ci_plan.py`, the release paragraphs of `README.md` and
`plugins/foundry/README.md`, the version table row of the root `README.md`, and in
`plugins/foundry/README.md` the migration-guide pointer and the version list of the Release rule. The mechanical documentation-status gate is not
claimed shipped.
