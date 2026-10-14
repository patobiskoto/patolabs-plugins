# Foundry 1.3.0 — release note, 2026-10-15

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation or a version loaded by a host: those states come after the merge
and are recorded where a page can still change, in the root `README.md` and in
`plugins/foundry/README.md`. Both host manifests (Claude Code and Codex) declare
`1.3.0`. Ship-iOS retains its independent `0.3.0` version (see [Ship-iOS](#ship-ios)).
The versionless marketplace catalogues continue to point at `./plugins/foundry` and
`./plugins/ship-ios`.

1.3.0 packages what was merged on `main` after the tag `foundry-v1.2.0`: 8 commits,
PAT-17 to PAT-204, listed [at the end of this page](#merged-issues-in-this-release).
It is a minor version, not a patch, because it adds CLI verbs and changes the review path
of two skills; the version number comes from the brief of PAT-199, not from a decision
recorded in this repository. This page announces **no gain**: no time, cost, quota or quality improvement is
measured by it or claimed, and PAT-17 changes no routing default. Upgrade and rollback
instructions are in [migration-1.3.0.md](migration-1.3.0.md).

## Behaviour changes and risks

Read this section before upgrading. Each item gives what happens and its remedy, as
far as one is established.

### 1. The pre-merge review reserves the diff first, then prepares a private packet

`review-pr` and `merge-pr` now run `routing claim-review --host <HOST>` on both hosts
before the reviewer packet exists, then `routing prepare-review` (PAT-174, PAT-204). The
steps and options are in the two skills; the packet, its bounds and its private output
are in [review-packet.md](review-packet.md). Under Codex the skills now hand the
generated send file and the existing claim to `routing codex-plan reviewer`, where the
façade used to perform the claim itself.

- **`routing claim-review --host`:** `codex` or `claude`. Omitting it keeps the
  historical `claude` path, and the Codex route options (`--tier`, `--model`,
  `--effort`, `--available-model`, `--profile-model-active`, `--profile-effort-active`)
  are refused with `--host claude` (`routing.py`, `claim-review` branch of `main`;
  [model-routing.md](model-routing.md), "Cross-host review deduplication").
- **Codex route checked before any reservation (PAT-204):** `claim-review --host codex`
  validates the reviewer route through `routing_facades.py::codex_review_preflight`
  before a new reservation or the replay of an interrupted publication; a refused route
  leaves the review ledger unchanged. A claim whose marker is already coordinated keeps
  its short circuit (`ReviewDeduplicator.git_claim_requires_preflight`).
- **`routing prepare-review`** reads the tracker and the code host and writes no tracker
  or PR state ([review-packet.md](review-packet.md)). Its output directory must not
  exist; it is private (modes in that page).
- **Remedy:** a Codex caller of the review skills must pass `--host codex` and the same
  route inputs it will give `codex-plan`; a script that called `codex-plan` for a
  first claim must claim first (deduced from the text of the two skills, not run).
  Nothing is done automatically for a custom script.

### 2. The reviewer can search and list the claimed snapshot; its Bash guard widens

`routing search-review` and `routing list-review` query regular tracked blobs of the
actively claimed HEAD (PAT-176); limits (`MAX_TEXT_CHARS`, `MAX_BLOB_BYTES`,
`MAX_MATCHES`), the evidence scope and the host-enforcement discussion are in
[reviewer-queries.md](reviewer-queries.md). The routed read-only Claude guard
(`hooks/guard_routed_readonly.py::allows_verified_diff`) now accepts these two verbs, and
the advisory reader of item 3, next to `routing read-review`, and refuses any command
containing CR or LF, the premerge readers included. The `maigret` agent and the routed
read-only profiles carry the matching instruction text.

- **Profiles:** `git diff foundry-v1.2.0..HEAD -- agents` changes body lines only, in 44
  files (43 routed read-only profiles and `maigret.md`); the diff contains no
  frontmatter line, so no model, effort or tool list changes there. The eight `xhigh`
  routed read-only profiles also lose the words "do not edit" from their body text and
  gain the same Bash sentence as the others.
- **Codex:** the reviewer role is selected with a read-only prompt contract; the page
  states that this selection does not prove native sandbox or tool enforcement, and the
  shipped template is `examples/codex-reviewer.toml`. Whether the native sandbox
  allows the ledger lock is unknown ([reviewer-queries.md](reviewer-queries.md)).
- **Remedy:** a project that customised its Codex reviewer role to require `gh pr
  diff` must compare it with the template (procedure in the page above).

### 3. A new, separate post-merge advisory review

`routing postmerge-review prepare|read|record` (PAT-200, PAT-202) prepares, reads and
records an advisory review of an already merged diff, separately from original claims and
delivery proofs. It needs a direct human authorization for the exact audit and ends in
findings submitted to `foundry:intake`; it merges, closes and promotes nothing. The CLI,
the receipt vocabulary, the native-evidence rules and the publication bound are in
[postmerge-review.md](postmerge-review.md) and in the advisory branch of `review-pr`; this
page repeats none of them.

- **Linear only by default:** the default coordinator ports refuse any other provider
  (`postmerge_review.py::ports`).
- **Comment bound:** every comment is bounded locally to `MAX_COMMENT_BYTES` (12,294
  UTF-8 bytes including its prefix, `advisory_schema.py`). The native maximum is
  unknown: the page records one probe of 12,294 bytes.
- **Linear comment retention:** advisory-prefixed comments are now kept beyond the ten
  recent ordinary comments of `LinearTracker._to_issue`; deduced from that code, not run:
  an issue read can return more comments than before.
- **Decision record:** PAT-ADR-0020 is accepted and supersedes PAT-ADR-0019, both
  statuses read from the tracker after PAT-202's merge; the acceptance was the
  maintainer's explicit decision, recorded on PAT-202, because that issue carried no
  framing line for `merge-pr` to accept it automatically.
- **Native provenance is not established.** The page records a structurally checked
  coordinator transcription, not a direct host observation, and no native sandbox
  qualification (PAT-196).

### 4. Linear issue creation admits only qualified renderings

`LinearTracker.create_issue` (PAT-197) reads the created issue back by its client UUID
and validates project, identity, title, body and requested fields
(`_validate_issue_creation`); the body must equal the source bytes or its
qualified rendering (`_issue_creation_body_matches`, `_issue_creation_rendering`), whose
exact rules are in [linear-tracker.md](linear-tracker.md), section "Issue creation
validation and explicit reconciliation (PAT-197)". Before, the description had to equal
the body exactly (the removed condition `(raw.get("description") or "") != body`).

- **Partial effects are typed:** a creation that cannot be validated raises
  `IssueCreationValidationError` (`trackers/base.py`), and `edit create-issue` exits
  nonzero printing its `details` as JSON on standard error, with no retry. A script that
  read only the exit code and the text of standard output must be checked.
- **A quota error stays typed:** it propagates with its `reset_at` and names the
  `client_id`; no read follows (`LinearTracker.create_issue`).
- **`edit reconcile-create <ISSUE-ID> <creation-spec.json>`** is read-only: it validates
  an existing issue against the original spec, without recreation or overwrite.
- **Port:** `Tracker.reconcile_issue_creation` refuses by default
  (`TrackerCapabilityUnavailableError`); only the Linear adapter implements it.
  `foundry:intake` points to the procedure.
- **Remedy:** after a failed creation, preserve the original spec and run the
  reconciliation; never rerun `create-issue` blindly.

### 5. Model decisions and a rollback script, no routing change

PAT-17 adds [model-decisions-2026-10-11.md](model-decisions-2026-10-11.md), the
qualification record `pat-17-config-rollback-2026-10-11.json`, the example
`examples/claude-pre-promotion-rollback.json` and the option `--out` of
`scripts/check_model_rollback.py`. The eight configuration rollbacks passed on
disposable policies through CLI resolution and constructed plans; no host model was
launched, so native backend acceptance of those eight profiles is **unknown** (PAT-198).
`DEFAULT_MAPPINGS` and the role defaults are unchanged.

### 6. Port additions for adapters outside this package

`PullRequest` gains an optional `body` (`models.py`), filled by the GitHub code host
(`codehosts/github.py::get_pr`, per the PAT-174 entry); another adapter that does not set
it leaves `None` (the field default). `prepare-review` writes the whole pull request
into `pr.json` (`review_packet.py`), so such a packet carries no description (deduced
from the default, not run). The Tracker port gains
`reconcile_issue_creation` (item 4), with a default that refuses.

## What 1.3.0 contains since 1.2.0

- Review path: PAT-174 (`prepare-review`), PAT-176 (`search-review`, `list-review`),
  PAT-204 (Codex preflight), items 1 and 2.
- Advisory path: PAT-200 and PAT-202, item 3.
- Tracker: PAT-197, item 4.
- Decisions: PAT-17, item 5.
- Documentation only: PAT-194 (post-publication observations of 1.2.0, the release rule's
  version list and the `scripts/ci_plan.py` comment; its record is
  `docs/qualification/pat-194-post-publication-observation.json`).

`LinearTracker.read_scope` is now also opened by `routing prepare-review`
([linear-tracker.md](linear-tracker.md)); no request-count gain is measured for that
command ([linear-request-cost.md](linear-request-cost.md)).

### Where the changes are

From `git diff --name-only foundry-v1.2.0..HEAD` on `main` at `3647df4`, before this
release pull request: 87 files, of which 13 under `plugins/foundry/tooling/`, 2 under
`hooks/`, 44 under `agents/`, 3 skills (`intake`, `merge-pr`, `review-pr`), 9 under
`tests/`, 9 under `docs/`, 1 under `plugins/foundry/scripts/`, 2 under `examples/`, and
4 others (the two READMEs, the CHANGELOG, `scripts/ci_plan.py`). Nothing changed under
`plugins/ship-ios`.

## What this release does not claim

- No saving of time, cost or quota, and no quality improvement, for any item.
- No new model, effort, profile frontmatter, routing default or tracker variant.
- No native observation of the eight rollback profiles ([model-decisions-2026-10-11.md](model-decisions-2026-10-11.md)),
  of the Codex reviewer profile provenance, or of a reviewer that used the new queries:
  [reviewer-queries.md](reviewer-queries.md) says its fixture run is deterministic
  capability evidence, not a native reviewer observation.
- No tag, installation or loaded version (see "Release phases and evidence").
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **Native provenance of the Codex reviewer profile and its sandbox: unknown** (PAT-196).
- **Native availability of the eight rollback profiles: unknown** (PAT-198).
- **Maximum native Linear comment size: unknown**; only 12,294 bytes were probed
  (PAT-202, [postmerge-review.md](postmerge-review.md)).
- **Closure of issues that carry no code change: not settled by this release**
  (PAT-206, open; nothing in this repository describes it).
- **Reservation before route check, Claude side:** PAT-204 closes this order defect for
  Codex only. A Claude analogue is suspected (the brief of this release calls it
  proposed): it is not a ticket and was not investigated here.
- **Real reconciliation of two creations** (PAT-195, PAT-196) remains pending delivery
  ([linear-tracker.md](linear-tracker.md)).
- **Rollback to an earlier plugin version is an untried procedure** (PAT-162), see the
  guide.
- **The Linear request counts of [linear-request-cost.md](linear-request-cost.md) were
  not re-measured for this release.**
- The observation windows listed in [release-1.2.0.md](release-1.2.0.md) were not
  re-read for this release; that page states them as of 2026-10-11.

## Outside 1.3.0

Not in this package: the issues named under "Known limits" and any scope the
maintainer has not merged. Also outside: any routing or model change, any Ship-iOS
change.

## Ship-iOS

Ship-iOS stays at `0.3.0`. `git diff --stat foundry-v1.2.0..HEAD -- plugins/ship-ios` is
empty at `3647df4`, and this release pull request changes no file under
`plugins/ship-ios`. `tooling/foundry/query.py`, which declares the
`foundry.release-scope.v1` capability the Ship-iOS bridge reads, changed since the tag by
one docstring hunk of `_read_scope` only (`git diff foundry-v1.2.0..HEAD`).
`trackers/linear.py`, which it reads through, changed; a release-scope read under 1.3.0
was not run. No bridge run against 1.3.0 is claimed, and the Foundry 1.3.0 and Ship-iOS
0.3.0 pair has not been read back on a host.

## Release phases and evidence

1. **Before merge (this diff):** both manifests at `1.3.0`, the dated changelog section,
   this page, the migration guide and the `1.3.0` entry of
   `tests/fixtures/release-history.json`; the release contract
   (`tests/test_release_contract.py`) and the CI commands run on the candidate source;
   independent review on the exact diff, then exact-head check-runs plus legacy statuses
   (`AGENTS.md` R4). PR and merge only through `foundry:open-pr` and `foundry:merge-pr`
   (R1). This page does not supply the review or the CI result.
2. **The gated merge is the moment from which a host can receive the version.** The
   catalogues carry no version and no ref. A host that runs an official update after the
   merge is therefore expected to receive manifests saying `1.3.0`, before any tag
   exists. This is derived and **not observed**; whether a manager refreshes a package
   whose version does not change is **unknown**, as for 1.2.0.
3. **The tag is the named reference, not the delivery.** `foundry-v1.3.0` is to be placed
   on the exact merged commit, after the maintainer's explicit agreement on the content
   of this note. This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a
   new session; read back the version actually loaded, each host separately. The result
   goes to the ticket and to the READMEs, not to this page.
5. **Freeze:** this page, the migration guide and the `1.3.0` changelog section are
   frozen by digest from the release pull request on. A later correction goes to an
   errata page.

Nothing is declared "tagged" or "installed" by this diff.

## Merged issues in this release

Established from `git log --first-parent foundry-v1.2.0..HEAD` on `main` at `3647df4`
(8 commits). Each row is one squash commit, in merge order; the PR number is given only
where the commit subject carries it.

| Issue | Commit | PR | Subject |
| --- | --- | --- | --- |
| PAT-194 | `91ffa19` | #137 | Record Foundry 1.2.0 post-publication observations |
| PAT-174 | `7a8a629` | #138 | Prepare private verified review packets |
| PAT-176 | `216204c` | #139 | Give the reviewer a read-only search of the repository |
| PAT-17 | `7c8d10f` | #140 | Decide model profiles per host and prove their rollback |
| PAT-200 | `e23501c` | not in subject | Add audited post-merge advisory reviews |
| PAT-202 | `51ae0d7` | not in subject | Qualify advisory evidence and retain reports |
| PAT-204 | `6da30ea` | not in subject | Preflight Codex reviewer routes before claims |
| PAT-197 | `3647df4` | #144 | Validate rendered Linear issue creations and reconcile partial effects |

The subjects of PAT-176 and PAT-17 are translated from French commit subjects. The full
entries, with their limits, are the `1.3.0` section of the [changelog](../CHANGELOG.md).
This release preparation changes version metadata and documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`1.2.0` → `1.3.0`) | Updated; stated here and in migration-1.3.0.md. No other manifest key changed |
| Dated Foundry changelog | `1.3.0 — 2026-10-15` section opened over the eight existing entries, which are not rewritten; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-1.3.0.md |
| `tests/fixtures/release-history.json` | Updated: the `1.3.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, the statement that the tag, the installation and the loaded-version readback are not done here, the migration list, and the Release rule's version list |
| `scripts/ci_plan.py` comment | Updated: its list of release versions includes 1.3.0; no behaviour changes |
| Ship-iOS manifests, README and changelog | Not necessary: no file under `plugins/ship-ios` is changed and its version does not move |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes, and no rule of the contract states a Foundry version |
| `docs/review-packet.md`, `reviewer-queries.md`, `postmerge-review.md`, `linear-tracker.md`, `model-routing.md`, `model-decisions-2026-10-11.md` | Not necessary: each already documents, since its own issue, the surface this page summarises; this release changes none of them |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or skill
is changed by this release preparation, and no file under `tooling/`, `hooks/`, `skills/`
or `agents/` is touched. The mechanical documentation-status gate is not claimed shipped.
