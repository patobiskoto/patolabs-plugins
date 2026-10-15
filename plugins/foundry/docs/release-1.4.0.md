# Foundry 1.4.0 — release note, 2026-10-15

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation or a version loaded by a host: those states come after the merge
and are recorded where a page can still change, in the root `README.md` and in
`plugins/foundry/README.md`. Both host manifests (Claude Code and Codex) declare
`1.4.0`. Ship-iOS retains its independent `0.3.0` version. The versionless marketplace
catalogues continue to point at `./plugins/foundry` and `./plugins/ship-ios`.

1.4.0 packages what was merged on `main` after the tag `foundry-v1.3.0`: 12 commits,
PR #146 to #157, listed [at the end of this page](#merged-issues-in-this-release). It is
a minor version, not a patch, because it adds CLI verbs and one skill and changes
behaviour (see `release_kind`, `tests/test_release_contract.py`); the version number comes
from the brief of PAT-211, not from a decision recorded in this repository. This page
announces **no gain**: no time, cost, quota or quality improvement is measured by it or
claimed. Upgrade and rollback instructions are in
[migration-1.4.0.md](migration-1.4.0.md).

## Behaviour changes and risks

Read this section before upgrading. Each item gives what happens and its remedy, as
far as one is established.

### 1. A started or in-review issue can be put back to the backlog (Linear)

`edit transition <ISSUE-ID> backlog <expected-state>` (PAT-165) is a new target of the
guarded transition. `<expected-state>` is mandatory, `in-progress` or `review`, and is
compared with the native state read. Refusals, the absence of a new receipt, the reading
of an issue at native `backlog` and the accepted risks are in
[linear-tracker.md](linear-tracker.md) and in PAT-ADR-0024, accepted just before the merge of PR #151
(status read on the tracker on 2026-10-15). Other providers keep their behaviour (changelog entry of PAT-165).

- **Reading change:** an issue at native `backlog` over a valid non-terminal chain now
  reads `backlog` / `aligned` instead of `disagreement` (changelog entry of PAT-165). A
  script that treated that `disagreement` as a signal must be checked.
- **Remedy:** none needed unless such a script exists; `edit set-field <ISSUE-ID> State
  backlog` stays an unguarded grooming write.

### 2. Review rounds: a partial review everywhere, a carried proof, a per-issue report

- **Partial re-review on every pull request (PAT-172).** The review that follows the
  correction of the non-blocking remarks of a validated round covers what changed since
  that round, on every pull request, not only on those of a companion Epic
  (PAT-ADR-0022; `AGENTS.md#R9(b)`, [review-rounds.md](review-rounds.md)). It stays full
  after a blocking round, when the change goes beyond the corrections, and when the
  reviewer asks for it with a reason. No tooling, merge gate or proof format changes, and
  no mechanical check tells a partial review from a full one.
- **`routing carry-review-proof` (PAT-173).** Carries a terminal `mergeable` all-pass
  review proof across a conflict-free integration of the main branch that touches no file
  of the PR, without a new round (PAT-ADR-0021; `AcceptanceProofStore.carry`,
  `tooling/foundry/routing.py`). Any other case is refused and a full review stays
  required. Steps are in `skills/merge-pr/SKILL.md` (step 2b) and `skills/review-pr/SKILL.md`.
- **`routing review-rounds <ISSUE-ID>` (PAT-175).** Read-only; reports the review rounds,
  the duration and, with `--session` / `--codex-session`, tokens per class. What is
  `unknown` and why is in the section "Per-issue report" of
  [review-rounds.md](review-rounds.md); this page repeats none of it.
- **Remedy:** a caller of the review or merge skills follows the updated skills; a
  documented count "by hand" in older material is superseded by the page above.

### 3. Epic closure: a closed child Epic and a benign native update

- **A closed child Epic (PAT-185).** Closing an Epic whose required child is itself an
  Epic already closed under a valid closure audit is accepted instead of refused
  (`write._closed_child_epic`). A child Epic that is not closed, or whose audit is
  absent, invalid or divergent from its current graph, is still refused.
- **A native update that changes nothing of substance (PAT-208, PAT-ADR-0023).** A closed
  Epic stays readable: `write.epic_closure_graph_unchanged` accepts a node of equal or
  newer version when the fields listed in the changelog entry are unchanged and the graph
  shape is equal. Linear and YouTrack only; GitHub Projects keeps the exact comparison.
  Write-path comparisons and `override` nodes stay exact.
- **Where documented:** [linear-tracker.md](linear-tracker.md),
  [tracker-contract.md](tracker-contract.md), `skills/close-epic/SKILL.md`.
- **Remedy:** none. These two changes only accept cases that were refused or read as
  divergent; the refusals listed in the entries remain.

### 4. Historical ADR links on Linear

`adr link-issue` accepts the issue links of the two historical bodies `FOUNDRY-ADR-0002`
and `FOUNDRY-ADR-0018` (PAT-201), through a source-digest-pinned profile
(`_PAT_201_PROFILES`, `trackers/linear.py`). Any other or altered body stays refused;
`FOUNDRY-ADR-0003` stays refused. Census and limits:
[qualification/pat-201-historical-adr-links.md](qualification/pat-201-historical-adr-links.md).
The version-1 rendering that Linear returns after a link is **deduced, not observed**
(changelog entry of PAT-201). Three historical ADR links remain open (PAT-209).

### 5. Advisory post-merge review: the report budget is written by code

`prepare` of `routing postmerge-review` (PAT-203) measures the empty v2 terminal receipt
with the shipped serializer before consuming the authorization, writes the report budget
into the reviewer message, and refuses before any write when the room or the budget is not
positive. The formula, the constant `REPORT_MARGIN_PERCENT` (`advisory_schema.py`) and
the limits are in [postmerge-review.md](postmerge-review.md). The budget is a request,
not an enforced host bound (changelog entry of PAT-203). Remedy: a coordinator that
copied the budget instruction by hand no longer needs to.

### 6. The R1 guard names a malformed global registry; the registry loader is stricter

`hooks/guard_bash.py` names the real cause of a fail-closed denial (PAT-63): a malformed
global registry now yields a message naming the Foundry global registry and its path,
instead of the invalid-binding wording (docstring of `guard_bash.py`; `AGENTS.md#R1`).
**Behaviour change for every caller:** `registry.load()` raises `registry.RegistryFileError`
(a `ValueError` subclass) for a provider or an entry that is not an object, which was
silently treated as unregistered. An unreadable registry (`OSError`) still fails open
(changelog entry of PAT-63). **Remedy:** a hand-edited registry whose provider or entry is
not an object must be corrected; it was ignored before and is refused now (deduced from
the entry, not run).

### 7. A release skill and two read-only helpers

- **`foundry:release` (`skills/release/SKILL.md`, PAT-166).** Chains scope, release
  branch and PR, then three separate STOPs (merge, tag, host update), the loaded-version
  check and the record on the release ticket. Its host commands were not run when it was
  written; this release preparation is the first full run of its steps 2 and 3.
- **`scripts/release_freeze_entry.py <version> [--date]` (PAT-166)** prints the entry
  computed by `freeze_entry` (`tests/test_release_contract.py`) and writes nothing.
- **`scripts/changelog_assemble.py <version> <date> [--dir]` (PAT-210)** prints the
  dated section from `changelog.d/<ISSUE-ID>.md` fragments, issue number descending, and
  writes nothing. From this release a pull request adds a fragment and never edits
  `## Unreleased`; `changelog.d/README.md` states the format. **Ordering consequence:**
  the entries of the `1.4.0` section are in issue number order, not in merge order as in
  `1.3.0`.

### 8. A measurement, no change

PAT-171 records the observation window of Haiku 5.5 in
[qualification/pat-171-haiku-55-window.md](qualification/pat-171-haiku-55-window.md),
linked from [qualification/pat-125-haiku-55-promotion.md](qualification/pat-125-haiku-55-promotion.md).
It changes no code, model, effort, routing or configuration; the decision (keep
`haiku-5.5` / `medium`, window closed) was taken by the coordinator under the
maintainer's delegation and is reversible by the maintainer (that page).

## What 1.4.0 contains since 1.3.0

- Tracker and Epics: PAT-165, PAT-185, PAT-208, PAT-201 (items 1, 3, 4).
- Review path and process: PAT-172, PAT-173, PAT-175, PAT-203 (items 2, 5).
- Guard and registry: PAT-63 (item 6).
- Release tooling: PAT-166, PAT-210 (item 7).
- Measurement: PAT-171 (item 8).

### Where the changes are

From `git diff --name-only foundry-v1.3.0..HEAD` on `main` at `7e1e707`, before this
release pull request: 58 files, of which 10 under `plugins/foundry/tooling/`, 1 under
`hooks/`, 4 skills (`close-epic`, `merge-pr`, `release`, `review-pr`), 17 under `tests/`,
8 under `docs/`, 2 under `plugins/foundry/scripts/`, 13 under `changelog.d/`, `plugins/foundry/README.md`,
and `AGENTS.md` / `CLAUDE.md`. Nothing changed under `agents/` or
`plugins/ship-ios`.

## What this release does not claim

- No saving of time, cost or quota, and no quality improvement, for any item.
- No new model, effort, profile frontmatter, routing default or tracker variant.
- No native observation of the Linear rendering of PAT-201's links after a write, and no
  run of the host commands of `foundry:release`.
- No tag, installation or loaded version (see "Release phases and evidence").
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **Native provenance of the Codex reviewer profile and its sandbox: unknown** (PAT-196).
- **Native availability of the rollback profiles of PAT-17: unknown** (PAT-198).
- **Rollback to an earlier plugin version is an untried procedure** (PAT-162); see
  [migration-1.4.0.md](migration-1.4.0.md).
- **Closure of issues that carry no code change: not settled by this release** (PAT-206,
  open; consequently PAT-195 stays open).
- **Three historical ADR links remain** (PAT-209).
- **Deferred remarks** of the companion Epics are held by PAT-205 and PAT-207; they are
  not corrected by this release.

## Release phases and evidence

1. **This pull request prepares; it does not publish.** It bumps the two manifests,
   opens the `1.4.0 — 2026-10-15` changelog section from the twelve fragments (which it
   deletes), adds this note and the migration guide, and adds the freeze entry. Merge
   only through `foundry:merge-pr` after independent review on the exact diff and exact-head
   CI on two sources (`AGENTS.md` R1, R4). This page does not supply the review or the CI
   result.
2. **The gated merge is the moment from which a host can receive the version.** The
   catalogues carry no version and no ref, so a host that runs an official update after
   the merge is expected to receive manifests saying `1.4.0`, before any tag exists. This
   is derived and **not observed**; whether a manager refreshes a package whose version
   does not change is **unknown**.
3. **The tag is the named reference, not the delivery.** `foundry-v1.4.0` is to be placed
   on the exact merged commit, on the maintainer's explicit authorization (STOP of
   `skills/release/SKILL.md`). This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a new
   session; read back the version actually loaded, each host separately. The result goes
   to the release ticket and to the READMEs, not to this page.
5. **Freeze:** this page, the migration guide and the `1.4.0` changelog section are
   frozen by digest from the release pull request on. A later correction goes to an
   errata page.

Nothing is declared "tagged" or "installed" by this diff.

## Merged issues in this release

Established from `git log --first-parent foundry-v1.3.0..HEAD` on `main` at `7e1e707`
(12 commits). Each row is one squash commit, in merge order; the issue is the one named
in the subject, `none in subject` where the subject names none (the fragment names it).

| Issue | Commit | PR | Subject |
| --- | --- | --- | --- |
| PAT-173 | `30cf030` | #146 | Stop requiring a full round when only the main branch was integrated |
| none in subject (PAT-172) | `0c30d05` | #147 | Extend partial re-review to every pull request |
| PAT-185 | `690bad9` | #148 | Close an Epic whose child is an Epic already closed by waiver |
| PAT-208 | `4cf5ed7` | #149 | Keep a closed Epic readable after a benign native node update |
| PAT-166 | `70d69ba` | #150 | Write a release skill that chains the steps of a publication |
| PAT-165 | `1b50b40` | #151 | Let Foundry return an issue to the backlog |
| none in subject (PAT-63) | `8f39c05` | #152 | Name the global registry when the R1 guard fails closed on it |
| PAT-175 | `765d165` | #153 | Report review rounds, duration and tokens per issue |
| PAT-201 | `11846a9` | #154 | Allow historical ADR links with an attested unchanged body |
| none in subject (PAT-203) | `4f2d371` | #155 | Compute the advisory report budget in code |
| PAT-171 | `99c6901` | #156 | Settle the Haiku 5.5 observation window on the exploration role |
| PAT-210 | `7e1e707` | #157 | Remove CHANGELOG conflicts between parallel pull requests |

The subjects of the PAT-173, PAT-185, PAT-208, PAT-166, PAT-165, PAT-175, PAT-201,
PAT-171 and PAT-210 rows are translated from French commit subjects. The full entries,
with their limits, are the `1.4.0` section of the [changelog](../CHANGELOG.md). This
release preparation changes version metadata and documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`1.3.0` → `1.4.0`) | Updated; stated here and in migration-1.4.0.md. No other manifest key changed |
| Dated Foundry changelog | `1.4.0 — 2026-10-15` section opened from the twelve fragments, which are deleted; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-1.4.0.md |
| `tests/fixtures/release-history.json` | Updated: the `1.4.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, the statement that the tag, the installation and the loaded-version readback are not done here, and the Release rule's version list in the Foundry README |
| `scripts/ci_plan.py` comment | Updated: its list of release versions includes 1.4.0; no behaviour changes |
| `changelog.d/README.md` | Not necessary: unchanged, the fragment convention is already documented there (PAT-210) |
| Ship-iOS manifests, README and changelog | Not necessary: no file under `plugins/ship-ios` is changed and its version does not move |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes, and no rule of the contract states a Foundry version |
| `docs/linear-tracker.md`, `tracker-contract.md`, `review-rounds.md`, `postmerge-review.md`, qualification pages | Not necessary: each already documents, since its own issue, the surface this page summarises; this release changes none of them |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or skill
instruction is changed by this release preparation, and no file under `tooling/`, `hooks/`
or `agents/` is touched. Outside release metadata and these pages, the only change is one
comment line in `scripts/ci_plan.py`. The mechanical documentation-status gate is not
claimed shipped.
