# Foundry 1.2.0 — release note, 2026-10-11

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation or a version loaded by a host: those states come after the merge
and are recorded where a page can still change, in the root `README.md` and in
`plugins/foundry/README.md`. Both host manifests (Claude Code and Codex) declare
`1.2.0`. Ship-iOS retains its independent `0.3.0` version (see [Ship-iOS](#ship-ios)).
The versionless marketplace catalogues continue to point at `./plugins/foundry` and
`./plugins/ship-ios`.

1.2.0 packages what was merged on `main` after the tag `foundry-v1.1.1`: 11 commits,
PAT-150 to PAT-192, listed [at the end of this page](#merged-issues-in-this-release).
It is a minor version, not a patch, by the maintainer's decision of 2026-10-11, for two
reasons: `query issue` no longer reports a broken ADR-to-issue link, and the Tracker
port gains a method. This page announces **no gain beyond the request counts measured
in [linear-request-cost.md](linear-request-cost.md), under the conditions that page
gives**: no time, cost or quota saving is measured, and no quality improvement is
claimed. Upgrade and rollback instructions are in
[migration-1.2.0.md](migration-1.2.0.md).

## Behaviour changes and risks

Read this section before upgrading. Each item gives what happens and its remedy, as
far as one is established.

### 1. `query issue` no longer reports a broken ADR-to-issue link

`query issue` now builds the ADR index of its payload without validating the
ADR-to-issue links, on every tracker (PAT-190). Linear and GitHub Projects used to
validate them there; YouTrack and DevHub never did.

- **What is no longer reported by `query issue`:** a broken link (a missing reciprocal
  comment, a cited issue that is absent or inaccessible). It used to be projected in
  the `adrs` field as `adr_issue_unavailable` or `conflict`; `query issue` now returns
  the plain index for it. A transport, binding or payload failure while reading a
  cited issue used to fail the whole command and no longer does.
- **Who reports it:** the ADR commands (`query adr`, `query adrs`, `frame`, the ADR
  writes). They keep the validation and still refuse a broken link.
- **What `query issue` still checks:** the version chains, the witnesses, the
  histories, the statuses and the supersession relations, and, on Linear, the metadata
  of each stored relation list (an identifier repeated verbatim or malformed is a
  provider error there). The `conflict` and `adr_unavailable` statuses of the `adrs`
  field remain for those cases.
- **Remedy:** a caller that relied on `query issue` to learn that an ADR-to-issue link
  is broken must run `query adrs` (or `query adr <ADR-ID>`) instead. There is no option
  or configuration key that restores the validation inside `query issue`.

The exact list of reads the index still makes, per tracker:
[linear-tracker.md](linear-tracker.md) and
[ghprojects-tracker.md](ghprojects-tracker.md).

### 2. The `note` field of `query issue` changes

The `note` of the `query issue` payload no longer announces the
`adr_issue_unavailable` status, and now says that this index does not validate
ADR-to-issue links and that the ADR commands of a tracker that has such links do
(`issue` in `tooling/foundry/query.py`). Every other field is identical for a valid
graph. The skills `start-issue`, `resume-issue` and `merge-pr` no longer list the
`adr_issue_unavailable` shape for `adrs` in this payload.

- **Remedy:** a script that compares the whole payload byte for byte, or that matches
  the text of `note` or the `adr_issue_unavailable` status, must be checked.

### 3. Linear read scope: one read per reference; `search` fetches every page first

On Linear, a read-only command reads each reference at most once, and `search`
fetches every page before normalising. `query issue`, `query adrs`, `query backlog` and
`query profile` (and `candidates`, which calls `backlog`) open a read scope on Linear
(PAT-168, `LinearTracker.read_scope()`). Inside it an issue reference is read at most
once per command; a native id and an identifier are two references. The scope is
created empty at the start of the command and dropped when it ends: nothing is kept
between two commands. `query adr <ID>` opens none, and a tracker other than Linear
reads as before.

- **A node changed while the command runs is not seen again.** Two snapshots inside the
  same read-only command (two closed Epics sharing a node in `backlog`, a closed Epic
  that is a child of a closed Epic in `query issue`) are served the shared node from
  the scope without a second read.
- **`search` now fetches every listing page before normalising any node, for every
  caller.** A successful listing, a listing that fails in only one way and a listing
  whose first response announces no next page report exactly what they reported. Only
  when two failures meet in a longer listing does the reported error differ: a failure
  met while fetching (transport, invalid response, missing cursor, page limit, an issue
  of a later page repeating an earlier page) now surfaces before a failure met while
  normalising a node already fetched. A listing that fails while normalising now
  requests every page before reporting its error.
- **With the scope open, a listing whose only failure came from an individual read of a
  node present in the listing no longer fails** (transport, quota): the listing seeded
  that read and it is not sent.
- **The early stop of `search` on a repeated issue** counts every node whose
  `identifier` key is present and hashable, whatever its type (PAT-189); it counted
  strings only since PAT-168.
- **Remedy:** none is needed for a successful read. A script that matches the error
  text of a failed listing must be checked against the error order described in
  [linear-tracker.md](linear-tracker.md). There is no option or configuration key that
  disables the read scope.

The outputs of the commands measured on the real project were byte-identical before
and after PAT-168; which commands, and what was not compared, is in
[Linear request counts](#linear-request-counts-measured-with-their-conditions).

### 4. `LinearReadScopeError` refuses a write or a nested opening in a read scope

While the read scope of item 3 is open, any document that is not a pure read is refused
with `LinearReadScopeError` before it is sent, and opening the scope while it is
already open is refused with the same error (PAT-189; it used to replace the outer
scope silently). `LinearReadScopeError` is a `RuntimeError` that is deliberately not a
`LinearTrackerError`: nothing was sent, and the handlers written for transport or
provider failures do not apply to it.

- No command of this package writes inside the scope or nests it; the refusal concerns
  code that calls `LinearTracker` directly or extends the read-only commands.
- **Remedy:** make the write outside the scope, and open the scope once. A handler that
  catches `LinearTrackerError` does not catch this error.

### 5. Third-party adapters: override `list_adrs_without_issue_links`

The Tracker port gains `Tracker.list_adrs_without_issue_links`
(`tooling/foundry/trackers/base.py`, PAT-190). Its default returns `list_adrs`, which
is right for an adapter whose `list_adrs` validates no such link. The Linear and GitHub
Projects adapters of this package override it.

- **An adapter outside this package whose `list_adrs` validates these links and that
  does not override the method** keeps validating them inside `query issue`. `query
  issue` has no projection left for `AdrIssueUnavailableError`: raised there, it
  propagates out of `query issue` like any unexpected error, where 1.1.1 projected
  `adr_issue_unavailable` (docstring of `AdrIssueUnavailableError`, same file).
- **Remedy:** override `list_adrs_without_issue_links` in that adapter, to skip the
  link validation and nothing else; every ADR command keeps `list_adrs`.

Whether such an adapter exists outside this repository is unknown.

## What 1.2.0 contains since 1.1.1

### Tracker reads

- **`query issue` returns the ADR index without validating the ADR-to-issue links**
  (PAT-190), with the explicit port method of item 5. Items 1 and 2 above.
- **Fewer Linear requests for the read-only commands `query issue`, `query adrs`,
  `query backlog` and `query profile`** (PAT-168), through the read scope of item 3,
  without changing any write path. ADR writes keep exactly their reads.
- **`LinearReadScopeError`**, the refusal of a nested scope and the wider early stop of
  `search` (PAT-189, item 4 and item 3), with tests pinning the error order of
  `search`, the reads of one state transition on the fake transport and one more
  conformance case (`query issue` with a disappeared ADR issue).

### Measures and records, documentation only

- **Linear request cost of the common reads** (PAT-167): the new page
  [linear-request-cost.md](linear-request-cost.md), and a correction in
  [linear-tracker.md](linear-tracker.md): `close-epic --status` reads the Epic twice,
  where the page said once.
- **Why two issues read `done` with a native In Progress, and how they were realigned**
  (PAT-163): [linear-tracker.md](linear-tracker.md), with the existing `issue merge
  <ISSUE-ID> <PR-NUMBER>` replay.
- **The Codex session observed under a newer command-line version, and the cause
  deduced for the earlier refusal of `gpt-6.1-sol`** (PAT-164): the operational note of
  [model-routing.md](model-routing.md). No routing change.
- **First observation window of the review-round rule and the maintainer's decision**
  (PAT-169): [review-rounds.md](review-rounds.md).
- **Closing measure of the 1-hour cache window and the maintainer's decision**
  (PAT-170):
  [pat-134-subagent-cache-1h.md](qualification/pat-134-subagent-cache-1h.md).

The last two are summarised under
[Observation windows in progress](#observation-windows-in-progress).

### Deferred review remarks settled

PAT-150 (from PAT-149), PAT-187 (from PAT-163 and PAT-164), PAT-189 (from PAT-167,
PAT-168 and PAT-190) and PAT-192 (from PAT-169 and PAT-170) settle non-blocking remarks
deferred under rule R9 (b) of `AGENTS.md`. PAT-150, PAT-187 and PAT-192 change wording
only (PAT-150 also one code comment, in `scripts/ci_plan.py`); PAT-189 also carries the
behaviour of item 4 and of the early stop of item 3.

### Where the changes are

From `git diff --stat foundry-v1.1.1..HEAD` on `main` at `c1fe8f9`, before this release
pull request: 23 files, of which 4 under `tooling/` (`query.py`, `trackers/base.py`,
`trackers/ghprojects.py`, `trackers/linear.py`), 3 skills (`merge-pr`, `resume-issue`,
`start-issue`), 5 under `tests/`, 7 under `docs/`, the two READMEs, the CHANGELOG and
`scripts/ci_plan.py`. Nothing changed under `hooks/` or `agents/`, and no model routing
table, effort scope or agent profile changed.

## Linear request counts: measured, with their conditions

The figures below are copied from [linear-request-cost.md](linear-request-cost.md).
They are **request counts**, not durations, costs or quota shares.

Conditions, as that page states them: the real project of this repository, on
2026-10-11, 45 ADRs, in three measures. The first, on a project of 174 issues, gives
figures of the installed Foundry 1.1.1 plugin only. The second, on 177 issues, ran each
command once with the installed 1.1.1 and once with the code of PAT-168, with no
tracker write between the two runs of a pair. The third, on 177 issues, gives the
figures of the code of PAT-190, its "before" being the installed 1.1.1 again. The
"1.1.1" column below is the figure that page gives for the installed 1.1.1 plugin in
those measures; for PAT-51 it comes from the first measure only (174 issues, see the
limits below). The counter is the one of `FOUNDRY_REQUEST_COUNT`, a single total of
HTTP attempts per process, retries included.

| Command | 1.1.1 | after PAT-168 | after PAT-190 |
| --- | --- | --- | --- |
| `query issue`, plain issue with 1 link | 56 | 48 (on PAT-190) | 5 (on PAT-189) |
| `query issue PAT-151`, open Epic with 7 links | 62 | 54 | 11 |
| `query issue PAT-51`, closed Epic with 50 links | 262 | 132 | 99 |
| `query adrs` | 54 | 46 | 46 |
| `query backlog` | 171 | 2 | not measured |
| `query profile intake`, page 1 | 171 | 2 | not measured |

What limits these figures, from the same page:

- The two plain-issue cells were measured on two different issues, each with one link;
  the page keeps one issue per row for that reason.
- For PAT-51, the 1.1.1 figure and output are those of an earlier measure of the same
  day: the 1.1.1 run repeated for the comparison was cut by the quota after 243
  requests.
- Outputs: byte-identical before and after PAT-168 for the six commands. After PAT-190,
  the outputs of the three `query issue` rows were byte-identical to the 1.1.1 outputs
  when they were compared, which was before the `note` field was corrected in the same
  pull request; after that correction only the plain issue was run again (5 requests,
  every field except `note` equal, `note` different as intended). The open and the
  closed Epic were not run again after the correction. The `query adrs` output was not
  compared again after PAT-190, whose change does not touch that path.
- The number of ADR documents pages (3) and of ADR-to-issue pairs (17) that explain the
  1.1.1 figures holds for the project state traced that day only.
- **No figure was measured on the code this version packages.** PAT-189 changed
  `tooling/foundry/trackers/linear.py` after the last measure; its CHANGELOG entry
  states that no output of any command changes. A measure on 1.2.0 itself has not been
  made.
- Not measured after the changes: `query profile intake --all-pages`, any other
  profile, `candidates`. Never measured, before or after: `issue merge`, `issue
  openpr`, `edit comment`, a real `close-epic` attempt. `issue start` was measured
  under 1.1.1 only (13 requests). Wall time was recorded for the 1.1.1 runs of the
  first measure only. Whether a retry happened during the measures, and whether a
  retry counts against the hourly quota, are unknown.

On the fake transports of the tests the counts are **expected**, not measured on a real
project: a plain Linear `query issue` goes from 9 requests to 6 (PAT-168) then 3
(PAT-190) on the fixture of `tests/test_read_scope_cost.py`; on GitHub Projects, `query
issue` goes from 17 commands to 15 on the fixture of
`tests/test_ghprojects_tracker.py`. **No GitHub Projects figure was measured on a real
project.** YouTrack and DevHub never validated these links in `query issue`, so
PAT-190 removes no read there, and the read scope is Linear only.

## What this release does not claim

- No saving of time, cost or subscription quota, and no relation between a request
  count and the Linear hourly quota beyond what
  [linear-request-cost.md](linear-request-cost.md) states.
- No request reduction for a write, for `query milestones`, for `query adr <ID>`, or
  for a tracker other than Linear on a real project.
- No conclusion on Haiku 5.5, on the 1-hour cache or on the review-round rule: none
  of the three observations below is concluded.
- No new model, effort, profile, routing default, tracker variant, CLI verb or option,
  or configuration key.
- No minimum Codex command-line version enforced by Foundry: PAT-187 left that idea to
  `foundry:intake`.
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **`query milestones` still pays the full replay of closed Epics.** It calls `search`
  without the read scope (`milestones` in `tooling/foundry/query.py`); PAT-189 left it
  out of scope, to go through intake. Its request count was not measured.
- **An interrupted `issue merge` can leave a Linear issue in `disagreement` until the
  same command is replayed.** [linear-tracker.md](linear-tracker.md) documents the
  mechanism: `set_state` appends the receipt, then writes the native State, and an
  interrupted second write is repaired by replaying the same merge. A case with the
  same symptom, by a comment on PAT-170 in the tracker and no page of this repository:
  on 2026-10-11 the `issue merge` of PAT-170 failed after the GitHub merge with a
  Linear lifecycle divergence error, the issue read `disagreement`, and replaying the
  same command repaired it. Its cause is unknown: it is not established that it is an
  instance of the mechanism above.
- **The reciprocal comment of each (ADR, issue) pair and the second identity read of a
  reference are still requests** in `query adrs` and in the ADR commands; a node absent
  from the listing is still read individually.
- **The reads of one state transition are unchanged:** on the fake transport, 6
  `issue.read` of the same issue per transition, 8 when the native state changes,
  binding read included (`test_issue_reads_of_one_transition`). The measured total of
  `issue start` on the real project is 13 under 1.1.1; its split is unknown.
- **The realignment of PAT-163 leaves unknowns** named in
  [linear-tracker.md](linear-tracker.md). That page deduces, without observing it,
  that 28 other issues carried the same divergence until they were realigned on
  2026-10-04. Unknown: what ran the pass of 14:50Z to 14:53Z that closed 27 of them,
  whether PAT-52 was realigned by the same means, and why the Linear GitHub
  integration did not move PAT-72 and PAT-73 to Done at the merge.

## Observation windows in progress

Three observations are listed, each with its own state. None is concluded by this
version.

| Change | Rule | State |
| --- | --- | --- |
| Haiku 5.5 / `medium` on the `economy` tier (PAT-ADR-0016) | Window: the first 10 issues delivered by Foundry on the Claude host after the merge of PAT-125 (2026-10-09), or 30 days, whichever comes first. Reference: the last 10 issues delivered before that merge. Immediate trigger: on a conforming host, a `haiku-5.5` profile unavailable or divergent, an executed model other than `claude-haiku-5-5`, or a transmitted effort other than `medium`. Regression trigger: the number of issues with a blocking first-pass review, an escalation or a red CI on the delivered SHA exceeds the reference by at least two. The maintainer may roll back at any time without justification. | **Bound not established here, measure not made, decision not taken.** More than 10 issues were merged on `main` after the merge of PAT-125 (`git log --first-parent`), but which host delivered each is not readable in this repository, so whether the 10-issue bound is reached is not established here; the 30-day bound is not reached at the date of this page. No page of this repository records a count of this window against its reference. The measure and the decision are the subject of PAT-171 (state backlog on the tracker on 2026-10-11). |
| 1-hour cache on the Sonnet 5.5 subagent profiles (PAT-134) | At closure the 5 most recent main conversations are replayed and `rollback_decision` (`cache_ttl_replay.py`) returns `keep`, `roll_back` or `unknown`; fewer than 3 sessions is `unknown`. The maintainer may extend the window by a written decision. | **Extended to 2026-11-09 (PAT-170).** The window reached its first bound at the tenth delivered issue (PAT-146). Only 2 main conversations qualified, so `rollback_decision` returned `unknown` and the change stays without conclusion. The maintainer decided on 2026-10-11 that the 1-hour cache stays and extended the window to the 30-day bound; the measure is made again, with the same rule, when at least 3 sessions qualify. Threshold, selection rule and rollback procedure are unchanged. |
| Review-round rule, `AGENTS.md#R9` (PAT-136, PAT-139, amended by PAT-144) | Second window: from the merge of PAT-144, the first 10 origin issues merged after it, with their follow-up issues. Return trigger: a blocking-level defect found after the merge in a diff merged under rule (b), or, when the window closes, a total number of rounds divided by the number of origin issues that is not below 3.2. | **Second window open (PAT-169).** First window: 39 rounds over 10 origin issues, 3.9, not below 3.2; the maintainer decided on 2026-10-11 to keep the rule as amended and to open the second window. State recorded at `a75e4f7`: 9 of 10 origin issues merged. The rounds of the second window are not counted yet. |

Where each is written: PAT-ADR-0016 and
[pat-125-haiku-55-promotion.md](qualification/pat-125-haiku-55-promotion.md);
[pat-134-subagent-cache-1h.md](qualification/pat-134-subagent-cache-1h.md), section
"Fermeture de la fenêtre (PAT-170, 2026-10-11)"; [review-rounds.md](review-rounds.md),
sections "First window: result (PAT-169)" and "Second window".

Three precisions:

- The Haiku rule above is the one the 1.1.0 release note carries; its signals are
  downstream of exploration and not causally attributable to it.
- The figures of the 1-hour cache measure are indicative and decide nothing, by the
  page that records them. What happens if fewer than 3 sessions qualify on 2026-11-09
  is not decided: the written rule then gives `unknown`.
- By the membership rule of [review-rounds.md](review-rounds.md), an origin issue is
  any merged issue that is not an issue of a companion Epic (the coordinator's reading,
  to be confirmed by the maintainer, as that page says). Under that reading the issue
  of this release, PAT-193, becomes an origin issue of the second window when its pull
  request is merged, and the tenth if no other origin issue is merged before it. This
  is what the rule implies, not a count already made: the list is fixed, and the rounds
  are counted, after that merge, for the maintainer's decision.

## Outside 1.2.0

The maintainer's decision of 2026-10-11, as the PAT-193 issue records it, is to publish
what is merged as 1.2.0 and to let the other issues of the Epics PAT-151 to PAT-154
slip to a later version. The list below is the tracker's list of their unmerged child
issues, read on 2026-10-11. None of their scope is in this package:

- the rest of PAT-151: PAT-63, PAT-162, PAT-165, PAT-166 and PAT-185;
- PAT-152: none. Its child issues on the tracker are PAT-167, PAT-168 and PAT-190,
  all three merged in this package;
- PAT-153 other than PAT-169 and PAT-170: PAT-171 to PAT-176;
- PAT-154: an Epic in state backlog on the tracker, whose child issues are PAT-141,
  PAT-177 and PAT-178; none of them is in the merged history.

Also outside: any reduction of `query milestones` or of the reads of a write, a
minimum Codex version guard, any model or routing change, and any Ship-iOS change.

## Ship-iOS

Ship-iOS stays at `0.3.0`. `git diff --stat foundry-v1.1.1..HEAD -- plugins/ship-ios`
is empty at `c1fe8f9`, and this release pull request changes no file under
`plugins/ship-ios`. `tooling/foundry/query.py`, which declares the
`foundry.release-scope.v1` capability the Ship-iOS bridge reads, did change since the
tag, but not in its `changelog` function: no hunk of that diff touches it, and that
function does not open the read scope of item 3 (read in code). `trackers/linear.py`,
which it reads through, changed; a release-scope read under 1.2.0 was not run. No
bridge run against 1.2.0 is claimed, and the Foundry 1.2.0 and Ship-iOS 0.3.0 pair has
not been read back on a host.

## Release phases and evidence

1. **Before merge (this diff):** both manifests at `1.2.0`, the dated changelog
   section, this page, the migration guide and the `1.2.0` entry of
   `tests/fixtures/release-history.json`; the release contract
   (`tests/test_release_contract.py`), the catalogue validation and the CI commands run
   on the candidate source; independent review on the exact diff, then exact-head
   check-runs plus legacy statuses (`AGENTS.md` R4). PR and merge only through
   `foundry:open-pr` and `foundry:merge-pr` (R1). This page does not supply the review
   or the CI result.
2. **The gated merge is the moment from which a host can receive the version.** The
   marketplace catalogues carry no version and no ref, and the documented `marketplace
   add` command names none. A host that runs an official update after the merge of the
   release pull request is therefore expected to receive manifests saying `1.2.0`,
   before any tag exists. This is derived from those facts and has **not been
   observed**. The same derivation reaches further back: every change this page lists
   has been on `main` since its own merge, while both manifests still said `1.1.1`.
   Whether a manager refreshes a package whose version does not change is **unknown**;
   a host that ran an official update between those merges and this release may
   already carry them under the `1.1.1` label, which is derived too and not observed.
3. **The tag is the named reference, not the delivery.** `foundry-v1.2.0` is to be
   placed on the exact merged commit, after the maintainer's explicit agreement on the
   content of this note. This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a
   new session; read back the version actually loaded, each host separately, with any
   unavailable observation kept as unavailable. The result goes to the ticket and to
   the READMEs, not to this page.
5. **Freeze:** this page, the migration guide and the `1.2.0` changelog section are
   frozen by digest from the release pull request on, as the freeze rule of
   `plugins/foundry/README.md` requires. A later correction goes to an errata page,
   not to these three.

Nothing is declared "tagged" or "installed" by this diff. A pre-merge source test
cannot pass a post-installation criterion.

## Merged issues in this release

Established from `git log --first-parent foundry-v1.1.1..HEAD` on `main` at `c1fe8f9`
(11 commits). Each row is one squash commit and its pull request, in merge order.

| Issue | Commit | PR | Subject |
| --- | --- | --- | --- |
| PAT-150 | `2185595` | #125 | Settle the remarks deferred after the 1.1.1 release (from PAT-149) |
| PAT-163 | `f50ed4c` | #126 | Realign the Linear state of PAT-72 and PAT-73 and explain the divergence |
| PAT-164 | `87bb8eb` | #127 | Record the verified Codex session and the cause of the `gpt-6.1-sol` refusal |
| PAT-167 | `8367180` | #128 | Measure the Linear request cost of reading the backlog, an Epic and its closure |
| PAT-168 | `c3ca3b3` | #129 | Reduce the Linear requests of backlog and Epic reads, from the measure |
| PAT-190 | `ac937ca` | #130 | `query issue`: return the ADR index without revalidating ADR-to-issue links |
| PAT-187 | `6865648` | #131 | Settle the remarks deferred from the reviews of PAT-151 |
| PAT-169 | `ff72512` | #133 | Decide the observation window of the review-round rule (R9) |
| PAT-189 | `d640961` | #132 | Settle the remarks deferred from the reviews of PAT-152 |
| PAT-170 | `a75e4f7` | #134 | Decide the observation window of the 1-hour cache of the Sonnet 5.5 subagents |
| PAT-192 | `c1fe8f9` | #135 | Settle the remarks deferred from the reviews of PAT-153 |

The subjects are translated from the commit subjects, which are in French except
PAT-164. The full entries, with their limits, are the `1.2.0` section of the
[changelog](../CHANGELOG.md). PAT-193 (this release preparation) changes version
metadata and documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`1.1.1` → `1.2.0`) | Updated; stated here and in migration-1.2.0.md. No other manifest key changed by PAT-193 |
| Dated Foundry changelog | `1.2.0 — 2026-10-11` section opened over the existing entries, which are not rewritten; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-1.2.0.md |
| `tests/fixtures/release-history.json` | Updated: the `1.2.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, and the statement that the tag, the installation and the loaded-version readback are not done by the release pull request |
| Ship-iOS manifests, README and changelog | Not necessary: no file under `plugins/ship-ios` is changed and its version does not move |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes, and no rule of the contract states a Foundry version |
| `docs/linear-tracker.md`, `docs/ghprojects-tracker.md`, `docs/linear-request-cost.md`, `docs/model-routing.md`, `docs/review-rounds.md`, `docs/qualification/pat-134-subagent-cache-1h.md` | Not necessary: each already documents, since its own issue, the behaviour or the record this page summarises; PAT-193 changes none of them |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or
skill is changed by PAT-193, and no file under `tooling/` is touched. The mechanical
documentation-status gate is not claimed shipped.
