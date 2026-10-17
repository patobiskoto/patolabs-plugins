# Linear request cost of the common reads (PAT-167)

This page records how many Linear requests the common read commands cost on the real
project, where the code reads the same thing several times inside one command, and which
reductions a follow-up ticket (PAT-168) could consider. It decides nothing. Three kinds
of statement are kept apart: **measured** (the table, and the two traces under it),
**deduced** (arithmetic on the measured figures) and **read in code** (symbols named
below, not run under a per-operation trace, except the two traces (`query issue`, `query
backlog`) described under "Conditions of the measure"). Where nothing supports an
answer, it says **unknown**.

Documentation status (R5, `AGENTS.md#R5`): new page, no CLI verb, option, configuration
key or constant changes. PAT-189 corrected statements of this page in place (dated
below); the history of those corrections is in the changelog. The counter it uses is the
PAT-145 one, documented in [`linear-tracker.md`](linear-tracker.md) ("Request counter
(PAT-145)").

## Conditions of the measure

- Date: 2026-10-11, between 13:37 and 13:42 (Europe/Paris; the output files of that
  measure are timed 13:37:58 to 13:41:16).
- Tool: the installed Foundry 1.1.1 plugin, with `FOUNDRY_TRACKER=linear` and
  `FOUNDRY_REQUEST_COUNT=stderr`, one process per command, run from a linked worktree
  at `origin/main` commit `87bb8eb`.
- Project size at that time (Linear connector listing and `query adrs`): 174 issues
  (identifiers PAT-6 to PAT-187 with gaps) and 45 ADRs in the index.
- The code read for the sections below is the tree at commit `87bb8eb`. Checked at
  PAT-189: `plugins/foundry/tooling` and `plugins/foundry/hooks` are byte-identical
  between the tag `foundry-v1.1.1` and `87bb8eb` (`git diff --quiet`), and the installed
  1.1.1 `tooling` directory is identical to the tag's (`diff -rq`, `__pycache__`
  excluded). Every function cited from `trackers/linear.py`, `write.py` and `query.py`
  is therefore the one the measure ran, for the sections "Measured", "Deduced" and
  "Repetitions located in code".
- `linear_requests` is the total number of HTTP attempts of the process, retries
  included. It is a single total: there is no per-operation breakdown in the counter.
- Per-operation traces (two commands, `query issue PAT-168` (a plain issue, one link:
  its parent) and `query backlog`, by the coordinator, after the table): 2026-10-11
  about 14:02 to 14:06 (Europe/Paris), project of 176 issues and 45 ADRs, installed
  Foundry 1.1.1, run through a wrapper script that patches `LinearTracker._graphql_once`
  to tally its `operation` argument and the id variable, then runs the unmodified CLI
  module. Read-only commands only. Quota exhaustion cut a third trace, `query issue
  PAT-87`, at 130 requests (125 `issue.read`, 3 `adr.list`, 2 link reads); it is
  incomplete and not used.

## Measured

All figures of this section and of the next one are those of Foundry 1.1.1, that is
before PAT-168 and before PAT-190.

| Command | `linear_requests` | wall time | stdout bytes |
|---|---|---|---|
| `issue start PAT-167` (backlog to in-progress, linked worktree) | 13 | not recorded | not recorded |
| `query issue PAT-167` (plain issue, 1 link: its parent) | 56 | 9 s | 14,497 |
| `query adrs` (45 ADRs) | 54 | 9 s | 10,455 |
| `query issue PAT-151` (open Epic, 7 links) | 62 | 13 s | 20,000 |
| `query issue PAT-87` (open Epic, 15 links, one child is the closed Epic PAT-19) | 145 | 29 s | 42,385 |
| `query issue PAT-51` (closed Epic, 50 links) | 262 | 52 s | 209,215 |
| `query profile intake` (page 1) | 171 | 34 s | 114,763 |
| `query backlog` | 171 | 34 s | 273,409 |
| `issue close-epic PAT-151 --status` (no closure intent on disk) | 2 | under 1 s | 28 |

Earlier figures of the same day, on another state of the project, recorded by the
coordinator and not re-measured: `query issue` of an Epic about 145; `query profile
intake --all-pages` 144 (the PAT-145 page); a `close-epic` attempt 150 to 220. A real
closure attempt is a write and was not replayed.

Trace of `query issue PAT-168` (56 requests, **measured**, conditions above): 36
`issue.read` on 28 distinct ids (PAT-16 and PAT-23 each read three times by identifier
and three times by native id), 3 `adr.list`, 17 `adr.issue-link.read`. The sequence
starts with 2 `issue.read` (the issue and its parent), then the 3 `adr.list` pages, then
the pairs. Reading: the ADR index is 3 pages + 17 (ADR, issue) pairs x (2 `issue.read` +
1 `adr.issue-link.read`) = 54. Of the 36 `issue.read`, 34 belong to the pairs and 28 ids
are distinct over the 36, so, deduced, 8 of the 34 pair reads repeat an earlier one. The
trace is one command on one project state: 3 pages and 17 pairs hold for that state
only.

Trace of `query backlog` (171 requests, **measured**, same conditions): 2 `issue.search`
and 169 `issue.read`, on 98 distinct ids (PAT-10, PAT-12, PAT-21, PAT-23 and PAT-24 are
read three times each). So 71 of the 169 individual reads repeat an id already read in
the same command (169 - 98). That these reads are the closed-Epic replays is not shown
by the trace (code reading, below); that they read issues the two listing pages of the
same command had just returned is presumed by the coordinator, not checked here.

## Deduced by arithmetic

- `query issue PAT-167`: 56 = 54 + 1 + 1. `query issue PAT-151`: 62 = 54 + 1 + 7. 54
  is the cost of `query adrs` alone, so the embedded ADR index looks like a fixed 54
  requests on every `query issue`: 96 % of the plain issue read (54 of 56) and 87 % of
  PAT-151 (54 of 62). The other terms are the issue itself (1) and one read per linked
  issue.
- `query issue PAT-87`: 54 + 1 + 15 = 70; the 75 other requests are not attributed by
  any measure. `query issue PAT-51`: 54 + 1 + 50 = 105; the 157 others are not
  attributed by any measure. The code reading below proposes a cause; it is not proven
  by a trace.
- `query backlog` and `query profile intake` cost the same 171, as the code predicts
  (one `search` each, see below).
- 174 issues at `first: 100` per page (`_ISSUES_QUERY`) is two `issue.search` pages,
  which the trace of `query backlog` measures (2 `issue.search`); the other 169 of the
  171 requests are `issue.read`, measured by the same trace, so they are not the listing
  itself.

## Repetitions located in code

Symbols are in `plugins/foundry/tooling/foundry/trackers/linear.py` unless said
otherwise. Each statement describes the code **before PAT-168 and before PAT-190**
(Foundry 1.1.1, commit `87bb8eb`), as it stood when PAT-167 wrote it; the sections
"After PAT-168" and "After PAT-190" below say what changed. Each statement was verified
by reading the named function; the only per-operation traces are the two
described under "Conditions of the measure".

### `query issue` (`query.py::issue`)

1. The requested issue is read with `observe_issue`, then each distinct link target is
   read once with `observe_issue` (targets are deduplicated and the issue itself is
   skipped): `1 + R` requests for R distinct targets. This matches 56 and 62 above.
2. Before PAT-190, `_adr_index_or_capability` then called `list_adrs` for the embedded
   index, every time, whatever the issue (since PAT-190 it calls
   `list_adrs_without_issue_links`, which skips point 3). `list_adrs` goes through
   `_adr_snapshot`: `_adr_documents` (the `adr.list` pages, page size 100),
   `_adr_chain`, then `_validate_adr_graph`.
3. Before PAT-168 and PAT-190, `_validate_adr_graph`, for every `(ADR, issue)` pair
   in the ADR relations, called `_resolve_adr_issue_reference` and then `_read_comment`
   (the reciprocal comment). `_resolve_adr_issue_reference` calls `_read_raw` on the
   reference, then `_read_raw` again on every coordinate of the corroborated identity
   that differs from the reference. A pair that passes has a canonical identifier as its
   reference (the check `canonical_id != issue_id` refuses anything else), so exactly
   one coordinate differs, the native id: a valid pair costs exactly three requests, two
   `issue.read` and one comment read (read in code, and confirmed by the trace). Outside
   a `graph_snapshot()` scope, `_read_raw` is one `issue.read` request each time. No
   scope was opened around `list_adrs`, and the `issue_refs` list is built per ADR
   without deduplicating the referenced issue across ADRs: an issue referenced by
   several ADRs is read again for each pair. `_adr_documents` pages through the
   project's Documents with the operation `adr.list`; what it lists is Documents (ADR
   versions and their witnesses), not ADRs, so 45 ADRs do not imply one page. The
   measured 54 is the number of `adr.list` pages plus three times the pairs; the trace
   gives 3 pages and 17 pairs for a plain issue (54 = 3 + 17 x 3) and 28 distinct issue
   ids over 36 `issue.read`.
4. The related issues of `query issue` (point 1) and the requested issue are normalized
   by `_to_issue` (unchanged). When the native state is `done` and a comment starts with
   `Foundry Epic closure audit (append-only).`, `_to_issue` calls
   `_verified_epic_closure`, which runs `_closure_from_raw`, then
   `bounded_epic_graph_snapshot` (`foundry/write.py`) over the Epic's graph. Per
   `linear-tracker.md` ("Provider-read cost of a graph snapshot (PAT-99)"), one snapshot
   costs N + 1 reads for N nodes. So, before PAT-168, every closed Epic with an audit
   that appears as the issue or as a link target cost a graph replay, on top of the read
   of the Epic itself. This is the code-reading cause proposed for the 75 (PAT-87,
   through the closed Epic PAT-19) and 157 (PAT-51) unattributed requests. The replay of
   PAT-19 and PAT-51 was not counted separately: **unknown** how many of those requests
   it is.

### `query backlog` and `query profile <name>`

1. Before PAT-168 and still after it (the read scope does not change the number of
   `search` calls), both call `search` exactly once per process: the docstring of
   `_profile_computation` says "Compute everything a profile page needs, ONCE (one
   tracker search)". `query profile <name> <page>` computes, then renders one page, so
   each page asked by a new process pays the search again; only `--all-pages` renders
   every page from one computation. This explains why `query backlog` and one page of
   `query profile intake` both cost 171.
2. Before PAT-168, `search` paged through `_ISSUES_QUERY` and called `_to_issue` with
   `observe_lifecycle=True` for every node of the project, page by page. Each node whose
   native state is `done` and which carries an Epic closure audit comment triggers the
   replay described above (`_verified_epic_closure` and `bounded_epic_graph_snapshot`),
   one snapshot per closed Epic, with no sharing between two snapshots
   (`graph_snapshot()` opens a private scope). The trace of `query backlog` measures the
   two listing pages and the 169 individual `issue.read`; that these reads are the
   closed-Epic replays is code reading: it is consistent with the trace and with nothing
   else found in `search`. How many closed Epics with an audit the project holds, and
   their size, is **unknown**.
3. Before PAT-168, a node read in the replay of one closed Epic was read again in the
   replay of another closed Epic that contained it, or in the listing itself: the scope
   of `graph_snapshot()` is created empty per snapshot (still true of a snapshot; the
   read scope of PAT-168 now shares those reads inside a read-only command).

### `issue start`, and the state transitions in general

1. `set_state` reads the issue (`_read_raw`) and its lifecycle projection, writes the
   lifecycle receipt (`_project_lifecycle`), then calls `_project_native_state`, which
   reads the issue (`_read_raw`) and returns at once when the native state already
   matches; otherwise it reads the issue again (`_read_raw`, the fresh read before the
   bounded write) and runs `_bounded_issue_update`. `set_state` ends with another
   `_read_raw` and `_lifecycle_projection`. The write path first runs
   `write.issue_binding`, which calls `validate_issue_binding`: one more `_read_raw` of
   the same issue before `set_state` starts. PAT-167 did not read the reads inside
   `_project_lifecycle` and `_bounded_issue_update`; PAT-189 counted them on the fake
   transport and pinned the counts in `test_issue_reads_of_one_transition`
   (`tests/test_read_scope_cost.py`; **expected**, one request per wire call, not the
   real project's). In `set_state` alone, `LIN-1` to `in-progress`, native state already
   matching: 5 `issue.read`, 1 lifecycle comment read, 1 lifecycle comment create.
   `LIN-1` to `review`, native state changing: 7 `issue.read`, 1 lifecycle comment read,
   1 lifecycle comment create, 1 `issueUpdate`. Adding the binding read: 6 and 8
   `issue.read` of the same issue per transition. The measured total for `issue start`
   on the real project is 13; its split is **unknown**.
2. Some of these reads are a detection of a concurrent write. The sentence of
   [`linear-tracker.md`](linear-tracker.md) ("capture the targeted expected snapshot,
   re-read it immediately before one `issueUpdate`, then verify readback") is written
   for the grooming field, description and parent replacements under PAT-ADR-0006, not
   for the native State. The code that applies the same sequence to the native State is
   `_project_native_state` (docstring "Project only State through S1-S5 after its
   durable receipt exists"): a first read and comparison, a fresh read immediately
   before the write, the bounded write and its readback in `_bounded_issue_update`.
   Which reads are redundant and which are that guarantee is a design question, not
   settled here.
   When the readback of the `review` State shows the proven PAT-230 regression, that
   path adds two `issue.state-history` reads (`_review_regression_proven`, called by the
   readback and again by the strict `_lifecycle_projection` that `set_state` runs after
   it; deduced from the code, not counted by a test) and no second `issueUpdate`.

### `issue close-epic`

`--status` goes through `epic_closure_state` (`write.py`), which reads the local intent
with `_read_local_intent` (no request) and the provider state of the Epic with
`read_epic_closure_state_with_id`. That function calls `validate_issue_binding`, which
does one `_read_raw` of the Epic, then does its own `_read_raw` of the same Epic: the
same issue is read twice, which matches the 2 requests measured. Its docstring announces
one read ("One issue read, no graph snapshot, no write"): the second is a repetition.
The cost of a real closure was **not measured**. The PAT-99 paragraph of
`linear-tracker.md` documents the closure as four snapshots (S0 preflight, pre-write S1,
the replay check of the closed parent, post-write S3) plus a fixed pre-validation,
measured on the fake transport only (266 requests for 55 nodes); the real-project figure
of 150 to 220 above is an earlier, non-reproduced note.

### The only cache, and its limit

Before PAT-168 (see the section "After PAT-168 lot 1" below for what changed),
`graph_snapshot()` was the only read reuse found: inside one snapshot, `_read_raw`
reuses a successful `issue.read` of an id. It is private to one snapshot and is not
opened around `list_adrs`, around `search` as a whole, or between two reads of `query
issue`. `linear-tracker.md` then stated: "Nothing is cached across calls, so a node
added, reopened or modified between two snapshots is still detected exactly as before
(PAT-ADR-0006, PAT-ADR-0014)." (the wording before PAT-168; the current one bounds it to
snapshots outside the same read-only command).

## After PAT-168 lot 1 (read-only commands)

Status of the statements below: the fake-transport figures are **expected** (counted at
the GraphQL transport by the test `tests/test_read_scope_cost.py`, one request per wire
call, retries excluded); they are not the real project's. The "Measured" table above
describes the code before this change.

What changed, in `trackers/linear.py` and `query.py`: `query issue`, `query adrs`,
`query backlog` and `query profile` open a read scope in which an issue reference is
read at most once per command (a native id and an identifier are two references, so an
issue resolved by both, when no listing seeded it, still costs two reads); `search`
fetches all pages first (for every caller) and seeds that scope with the listing, so the
closed-Epic replay is served from it. Nothing else: the reciprocal comment is still read
per pair, the two identity reads are kept, every write path is untouched, outputs are
identical.

Fixture of the test (an Epic with one closed child and one dependency, two ADRs citing
the same issue; 3 issues in the listing):

| Command | requests before | requests after |
|---|---|---|
| `query issue` (plain issue) | 9 | 6 |
| `query issue` (open Epic) | 9 | 6 |
| `query issue` (closed Epic) | 12 | 7 |
| `query adrs` | 7 | 5 |
| `query backlog` (closed Epic in the listing) | 4 | 1 |
| `query profile` (closed Epic in the listing) | 4 | 1 |

Real project, **measured** by the coordinator on 2026-10-11 between 14:30 and 14:59
(Europe/Paris, times of the output files): each command run once with the installed
Foundry 1.1.1 ("before") and once with the code of this change ("after"), same counter,
no tracker write between the two runs of a pair. Project at that time: 177 issues (the
174 of the first measure plus three created since) and 45 ADRs.

| Command | before | after | outputs |
|---|---|---|---|
| `query issue PAT-190` (plain issue, 1 link) | 56 | 48 | byte-identical (15,521 bytes) |
| `query adrs` | 54 | 46 | byte-identical (10,455 bytes) |
| `query issue PAT-151` (open Epic, 7 links) | 62 | 54 | byte-identical (20,000 bytes) |
| `query backlog` | 171 | 2 | byte-identical (276,699 bytes) |
| `query profile intake` (page 1) | 171 | 2 | byte-identical (114,763 bytes) |
| `query issue PAT-51` (closed Epic, 50 links) | 262 | 132 | byte-identical (209,215 bytes), see below |

For PAT-51 the "before" figure and output are those of the first measure of the same day
(13:39, table "Measured"): the "before" run repeated at 14:58 was cut by the quota after
243 requests. The comparison of outputs is therefore made against the 13:39 output; no
issue of that Epic's graph was written in between by the coordinator, and the two
outputs are identical. Not measured after the change: `query profile intake
--all-pages`, any other profile, `candidates`.

Limits that remain by design: the reciprocal comment of each (ADR, issue) pair and the
second identity read of a reference are still requests; a node absent from the listing
(for example outside the project binding) is still read individually; a node changed
during the command is not seen again, as with any single read.

## After PAT-190 (`query issue` without ADR-issue link validation)

Status: the fake-transport figures are **expected** (counted at the GraphQL transport by
`tests/test_read_scope_cost.py` and `tests/test_adr_index_without_links.py`, retries
excluded); they are not the real project's. The real-project measure follows the table.

What changed: the ADR index embedded in the `query issue` payload is built without
resolving the issues that ADRs declare and without reading the reciprocal comment of
each (ADR, issue) pair. Version chains, witnesses, statuses and supersession relations
are still checked, and the output for a valid graph is unchanged except the `note` field
(see below). `query adrs`, `query adr`, `frame` and the ADR writes keep the full
validation.

Same fixture as the PAT-168 section:

| Command | requests after PAT-168 | requests after PAT-190 |
|---|---|---|
| `query issue` (plain issue) | 6 | 3 |
| `query issue` (open Epic) | 6 | 3 |
| `query issue` (closed Epic) | 7 | 4 |
| `query adrs` | 5 | 5 |

The three remaining requests of the plain issue are the read of the requested issue
(LIN-2), the read of its linked issue (LIN-3, a dependency; asserted on the variables
of the two calls by `tests/test_adr_index_without_links.py`) and one page of ADR
documents.

GitHub Projects, fake gh transport of `tests/test_ghprojects_tracker.py` (**expected**,
counted by command, **not measured on a real project**; two ADRs citing one delivery
issue, `query issue` of another issue): 17 commands before PAT-190, 15 after. The two
removed are the Project listing and the native read of the cited issue, which only the
link validation made (once, although two ADRs cite it); `query adrs` is unchanged.

Real project, **measured** by the coordinator on 2026-10-11 between 16:01 and 16:03
(Europe/Paris, times of the output files), 177 issues and 45 ADRs: "before" is the
installed Foundry 1.1.1 (the code before PAT-168), "after PAT-168" the figures of the
section above, "after PAT-190" the code of this change, same counter.

| Command | before | after PAT-168 | after PAT-190 | outputs |
|---|---|---|---|---|
| `query issue PAT-189` (plain issue, 1 link) | 56 | not measured | 5 | byte-identical to the 1.1.1 output of the same minute (17,870 bytes) |
| `query issue PAT-151` (open Epic, 7 links) | 62 | 54 | 11 | byte-identical to the 1.1.1 output of the same minute (20,000 bytes) |
| `query issue PAT-51` (closed Epic, 50 links) | 262 | 132 | 99 | byte-identical to the 1.1.1 output of 13:39 (209,215 bytes) |
| `query adrs` | 54 | 46 | 46 | not compared again (path unchanged by PAT-190) |

The "after PAT-168" cell of the first row reads "not measured" on purpose: the 48 of a
plain issue with one link was measured on PAT-190 in the section above, not on PAT-189,
and a cell of this table keeps one issue per row.

These real outputs were compared before the `note` field of `query issue` was corrected
(same PR). After that correction the coordinator ran `query issue PAT-189` again with
the final code (5 requests): every field except `note` equals the 1.1.1 output of 16:02,
and `note` differs as intended. The open and the closed Epic were not run again after
the correction.

The 5 requests of a plain issue are the issue, its linked issue and the three `adr.list`
pages. For PAT-51 the 1.1.1 side was not run again (262 requests); its output of 13:39
is the reference, as in the section above.

## Not measured

- After the changes: `query issue PAT-87` and `issue close-epic PAT-151 --status`.
  Their earlier measurements above (145 and 2 requests respectively) have no after
  measurement in this page; neither is a measured before/after comparison.
- `issue merge`, `issue openpr`, `edit comment`, a real `close-epic` attempt.
- Any per-operation breakdown other than the traces of one `query issue` of a plain
  issue and of `query backlog` (2026-10-11); the counter itself is a single total.
- Whether any retry happened during the measures, and whether a retry counts against
  the hourly quota (already stated unknown in the PAT-145 section).
- The number of ADR-issue pairs and of `adr.list` pages on any state other than the one
  traced on 2026-10-11 (3 pages, 17 pairs), the number of closed Epics with an audit,
  and the size of each.

## Candidate reductions for PAT-168

Written by PAT-167 on the code before PAT-168 and before PAT-190: the figures and
constraints below are those of that code, not of the current one. Listed by the largest
measured or deduced figure, not decided. Every candidate must keep the outputs
byte-identical for the same tracker state and keep no state between two commands unless
an architecture decision (an accepted ADR) allows it. That rule is the maintainer's
constraint for PAT-168; this page found no accepted sentence that grants such a state.

| # | Candidate | Starting figure | Constraint found | Decision needed |
|---|---|---|---|---|
| 1 | Do not replay closed Epics inside list reads (`search`) and related reads of `query issue`, or replay each once per command | 169 of 171 (measured for `backlog`: 169 `issue.read`; that they are replays is code reading; `profile` deduced from the same `search`); up to 75 (PAT-87) and 157 (PAT-51) of 145 and 262 (code reading) | `linear-tracker.md`, on the Epic closure audit: "A valid non-code Epic closure audit supplies terminal **state** authority to `get_issue`, `observe_issue`, `query issue`, backlog and the groom profile", and "Reads verify [...] the complete current children/transitive dependency graph with its qualified acceptance evidence"; same file ("Provider-read cost of a graph snapshot (PAT-99)"): "Nothing is cached across calls ... (PAT-ADR-0006, PAT-ADR-0014)" (wording before PAT-168, since reworded in that file); docstring of `_verified_epic_closure`: "Read-only terminal authority shared by ordinary reads and Epic replay" | Yes, a maintainer or architecture decision: skipping a replay may change what a list reports for a disagreeing closed Epic, so identical output is not guaranteed by construction |
| 2 | Share issue reads inside one `query issue` between the embedded ADR validation, the requested issue and its links (one scope per command, dropped at exit) | 51 of the 54 fixed requests are the 17 pairs x 3 (trace of 2026-10-11, before PAT-168 and PAT-190) | `linear-tracker.md` on ADR issue relations: "Every issue relation is re-read and must belong to the configured team and project, and the issue must retain its deterministic reciprocal Foundry comment"; scope rule of `graph_snapshot()`: "never shared with another scope" | Likely a maintainer decision (a scope per command is not state kept between commands, but it widens the PAT-99 scope rule) |
| 3 | Deduplicate the `(ADR, issue)` reads in `_validate_adr_graph` across ADRs | The issue reads of each repeated reference (the reciprocal comment read stays); the trace records 36 `issue.read` on 28 distinct ids, so, deduced, 8 of the 34 pair reads repeat an earlier one | Same sentence as 2 (the reciprocal comment is per pair and must stay) | Only the issue reads, not the comment reads: probably within PAT-168 without a new decision, to be confirmed |
| 4 | Reduce the back-to-back `_read_raw` of one transition (`set_state`, `_project_native_state`) | 6 `issue.read` of one issue per transition, 8 when the native state changes, binding read included (fake-transport count pinned by a test at PAT-189); the share of 13 (`issue start`) is **unknown** | `linear-tracker.md` ("re-read it immediately before one `issueUpdate`, then verify readback", written for grooming replacements under PAT-ADR-0006) and `_project_native_state`, which applies the same sequence to the native State; the fresh pre-write read and the readback must stay | The first read and the lifecycle read may be mergeable; the others are the documented guarantee. Needs a maintainer reading of PAT-ADR-0006 |
| 5 | Make `query issue` skip the embedded ADR index, or return it on request | 54 of 56 (96 %) of a plain issue read | `linear-tracker.md`: "The issue payload's `adrs` field is a project-level retrieval index, never a delivery relation"; the note of `query.py::issue`; the ADR retrieval rule (`AGENTS.md#R2`) | Yes: it changes the output, so it conflicts with "byte-identical outputs" unless the maintainer accepts a new option |
| 6 | Merge the reads of `issue merge`, `openpr`, `edit comment`, a real `close-epic` | **Not measured** | Same as 4 for writes | Measure first |
| 7 | Drop the second read of the Epic in `read_epic_closure_state_with_id` (`close-epic --status`) | 1 of 2 (measured total, repetition read in code) | None found: the docstring already promises one read | No |

## Closed child Epic inside an Epic closure (PAT-185; deduced from the code, not measured)

When `close-epic` or its replay reads a required child that is an Epic closed under an audit,
that child is verified twice per snapshot of the parent's graph: once by `_to_issue` when it
normalizes the child (`_verified_epic_closure`, for a native `done` with an audit comment) and once
by `write._closed_child_epic` (`get_epic_closure`). Each verification reads the child's own graph
again (N + 1 reads for N nodes, "Provider-read cost of a graph snapshot (PAT-99)" in
`linear-tracker.md`), and the replay of the parent repeats both. The read scope of PAT-168 is
opened by `query issue`, `query adrs`, `query backlog` and `query profile` only (section above), so
it does not share these reads. How many requests this costs on a real project is **unknown**: no
figure was measured, and no change is made here.
