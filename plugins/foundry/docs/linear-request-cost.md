# Linear request cost of the common reads (PAT-167)

This page records how many Linear requests the common read commands cost on the real
project, where the code reads the same thing several times inside one command, and
which reductions a follow-up ticket (PAT-168) could consider. It decides nothing.
Three kinds of statement are kept apart: **measured** (the table), **deduced**
(arithmetic on the measured figures) and **read in code** (symbols named below, not
run under a per-operation trace). Where nothing supports an answer, it says
**unknown**.

Documentation status (R5, `AGENTS.md#R5`): new page, no CLI verb, option,
configuration key or constant changes. The counter it uses is the PAT-145 one,
documented in [`linear-tracker.md`](linear-tracker.md) ("Request counter (PAT-145)").

## Conditions of the measure

- Date: 2026-10-11, between 13:50 and 14:05 (Europe/Paris).
- Tool: the installed Foundry 1.1.1 plugin, with `FOUNDRY_TRACKER=linear` and
  `FOUNDRY_REQUEST_COUNT=stderr`, one process per command, run from a linked worktree
  at `origin/main` commit `87bb8eb`.
- Project size at that time (Linear connector listing and `query adrs`): 174 issues
  (identifiers PAT-6 to PAT-187 with gaps) and 45 ADRs in the index.
- `linear_requests` is the total number of HTTP attempts of the process, retries
  included. It is a single total: there is no per-operation breakdown.

## Measured

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
  so the other 169 of the 171 requests are not the listing itself (deduced, assuming
  the provider returned the two pages the arithmetic implies).

## Repetitions located in code

Symbols are in `plugins/foundry/tooling/foundry/trackers/linear.py` unless said
otherwise. Each statement was verified by reading the named function; none was checked
by a per-operation trace.

### `query issue` (`query.py::issue`)

1. The requested issue is read with `observe_issue`, then each distinct link target is
   read once with `observe_issue` (targets are deduplicated and the issue itself is
   skipped): `1 + R` requests for R distinct targets. This matches 56 and 62 above.
2. `_adr_index_or_capability` then calls `list_adrs` for the embedded index, every
   time, whatever the issue. `list_adrs` goes through `_adr_snapshot`:
   `_adr_documents` (the `adr.list` pages, page size 100), `_adr_chain`, then
   `_validate_adr_graph`.
3. `_validate_adr_graph`, for every `(ADR, issue)` pair in the ADR relations, calls
   `_resolve_adr_issue_reference` and then `_read_comment` (the reciprocal comment).
   `_resolve_adr_issue_reference` calls `_read_raw` on the reference, then `_read_raw`
   again on every coordinate of the corroborated identity that differs from the
   reference; whether a valid pair always has exactly one such coordinate (which would
   make a pair cost exactly three requests) was not established for this page. Outside a
   `graph_snapshot()` scope, `_read_raw` is one `issue.read` request each time. No scope
   is opened around `list_adrs`, and the `issue_refs` list is built per ADR without
   deduplicating the referenced issue across ADRs: an issue referenced by several ADRs
   is read again for each pair. `_adr_documents` pages through the project's Documents
   with the operation `adr.list`; what it lists is Documents (ADR versions and their
   witnesses), not ADRs, so 45 ADRs do not imply one page. The measured 54 is therefore
   the number of `adr.list` pages plus the pair reads; the number of pages and the
   number of pairs in the project are both **unknown**, so the split of 54 is not
   established.
4. The related issues of `query issue` (point 1) and the requested issue are
   normalized by `_to_issue`. When the native state is `done` and a comment starts
   with `Foundry Epic closure audit (append-only).`, `_to_issue` calls
   `_verified_epic_closure`, which runs `_closure_from_raw`, then
   `bounded_epic_graph_snapshot` (`foundry/write.py`) over the Epic's graph. Per
   `linear-tracker.md` ("Provider-read cost of a graph snapshot (PAT-99)"), one
   snapshot costs N + 1 reads for N nodes. So every closed Epic with an audit that
   appears as the issue or as a link target costs a graph replay, on top of the read
   of the Epic itself. This is the code-reading cause proposed for the 75 (PAT-87,
   through the closed Epic PAT-19) and 157 (PAT-51) unattributed requests. The replay
   of PAT-19 and PAT-51 was not counted separately: **unknown** how many of those
   requests it is.

### `query backlog` and `query profile <name>`

1. Both call `search` exactly once per process: the docstring of `_profile_computation`
   says "Compute everything a profile page needs, ONCE (one tracker search)". `query
   profile <name> <page>` computes, then renders one page, so each page asked by a new
   process pays the search again; only `--all-pages` renders every page from one
   computation. This explains why `query backlog` and one page of `query profile intake`
   both cost 171.
2. `search` pages through `_ISSUES_QUERY` and calls `_to_issue` with
   `observe_lifecycle=True` for every node of the project. Each node whose native
   state is `done` and which carries an Epic closure audit comment triggers the replay
   described above (`_verified_epic_closure` and `bounded_epic_graph_snapshot`), one
   snapshot per closed Epic, with no sharing between two snapshots (`graph_snapshot()`
   opens a private scope). With two listing pages, the 169 other requests are
   consistent with this reading and with nothing else found in `search`; how many
   closed Epics with an audit the project holds, and their size, is **unknown**.
3. A node read in the replay of one closed Epic is read again in the replay of another
   closed Epic that contains it, or in the listing itself: the scope of
   `graph_snapshot()` is created empty per snapshot.

### `issue start`, and the state transitions in general

1. `set_state` reads the issue (`_read_raw`) and its lifecycle projection, writes the
   lifecycle receipt (`_project_lifecycle`), then calls `_project_native_state`, which
   reads the issue (`_read_raw`) and returns at once when the native state already
   matches; otherwise it reads the issue again (`_read_raw`, the fresh read before the
   bounded write) and runs `_bounded_issue_update`. `set_state` ends with another
   `_read_raw` and `_lifecycle_projection`. That is three direct `_read_raw` of the same
   issue in one transition, four when the native state changes, before counting those
   inside `_project_lifecycle` and `_bounded_issue_update`, which were not read for this
   page. The measured total for `issue start` is 13; its split is **unknown**.
2. Some of these reads are the documented detection of a concurrent write:
   [`linear-tracker.md`](linear-tracker.md) ("capture the targeted expected snapshot,
   re-read it immediately before one `issueUpdate`, then verify readback"). Which reads
   are redundant and which are that guarantee is a design question, not settled here.

### `issue close-epic`

`--status` goes through `epic_closure_state` (`write.py`), which reads the local intent
with `_read_local_intent` (no request) and the provider state of the Epic with
`read_epic_closure_state_with_id`. That function calls `validate_issue_binding`, which
does one `_read_raw` of the Epic, then does its own `_read_raw` of the same Epic: the
same issue is read twice, which matches the 2 requests measured. Its docstring announces
one read ("One issue read, no graph snapshot, no write"): the second is a repetition.
The PAT-100 paragraph of `linear-tracker.md` said "one `issue.read`"; it is corrected
with this page. The cost of a real closure was **not measured**. The PAT-99 paragraph of
`linear-tracker.md` documents the closure as four snapshots (S0 preflight, pre-write S1,
the replay check of the closed parent, post-write S3) plus a fixed pre-validation,
measured on the fake transport only (266 requests for 55 nodes); the real-project figure
of 150 to 220 above is an earlier, non-reproduced note.

### The only cache, and its limit

`graph_snapshot()` is the only read reuse found: inside one snapshot, `_read_raw`
reuses a successful `issue.read` of an id. It is private to one snapshot and is not
opened around `list_adrs`, around `search` as a whole, or between two reads of `query
issue`. `linear-tracker.md` states: "Nothing is cached across calls, so a node added,
reopened or modified between two snapshots is still detected exactly as before
(PAT-ADR-0006, PAT-ADR-0014)."

## Not measured

- `issue merge`, `issue openpr`, `edit comment`, a real `close-epic` attempt.
- Any per-operation breakdown: the counter is a single total.
- Whether any retry happened during the measures, and whether a retry counts against
  the hourly quota (already stated unknown in the PAT-145 section).
- The number of ADR-issue pairs, the number of `adr.list` pages, the number of closed
  Epics with an audit, and the size of each.

## Candidate reductions for PAT-168

Listed by the largest measured or deduced figure, not decided. Every candidate must
keep the outputs byte-identical for the same tracker state and keep no state between
two commands unless an architecture decision (an accepted ADR) allows it. That rule is
the maintainer's constraint for PAT-168; this page found no accepted sentence that
grants such a state.

| # | Candidate | Starting figure | Constraint found | Decision needed |
|---|---|---|---|---|
| 1 | Do not replay closed Epics inside list reads (`search`) and related reads of `query issue`, or replay each once per command | 169 of 171 (deduced) for `backlog`/`profile`; up to 75 (PAT-87) and 157 (PAT-51) of 145 and 262 (code reading) | `linear-tracker.md`, on the Epic closure audit: "A valid non-code Epic closure audit supplies terminal **state** authority to `get_issue`, `observe_issue`, `query issue`, backlog and the groom profile", and "Reads verify [...] the complete current children/transitive dependency graph with its qualified acceptance evidence"; same file ("Provider-read cost of a graph snapshot (PAT-99)"): "Nothing is cached across calls ... (PAT-ADR-0006, PAT-ADR-0014)"; docstring of `_verified_epic_closure`: "Read-only terminal authority shared by ordinary reads and Epic replay" | Yes, a maintainer or architecture decision: skipping a replay may change what a list reports for a disagreeing closed Epic, so identical output is not guaranteed by construction |
| 2 | Share issue reads inside one `query issue` between the embedded ADR validation, the requested issue and its links (one scope per command, dropped at exit) | Part of the 54 fixed requests; size **unknown** (number of pairs not known) | `linear-tracker.md` on ADR issue relations: "Every issue relation is re-read and must belong to the configured team and project, and the issue must retain its deterministic reciprocal Foundry comment"; scope rule of `graph_snapshot()`: "never shared with another scope" | Likely a maintainer decision (a scope per command is not state kept between commands, but it widens the PAT-99 scope rule) |
| 3 | Deduplicate the `(ADR, issue)` reads in `_validate_adr_graph` across ADRs | The issue reads of each repeated reference (the reciprocal comment read stays); the number of repetitions is **unknown** | Same sentence as 2 (the reciprocal comment is per pair and must stay) | Only the issue reads, not the comment reads: probably within PAT-168 without a new decision, to be confirmed |
| 4 | Reduce the back-to-back `_read_raw` of one transition (`set_state`, `_project_native_state`) | 3 reads of one issue per transition, 4 when the native state changes (code reading); the share of 13 (`issue start`) is **unknown** | `linear-tracker.md`: "re-read it immediately before one `issueUpdate`, then verify readback" (bounded detection, PAT-ADR-0006); the fresh pre-write read and the readback must stay | The first read and the lifecycle read may be mergeable; the others are the documented guarantee. Needs a maintainer reading of PAT-ADR-0006 |
| 5 | Make `query issue` skip the embedded ADR index, or return it on request | 54 of 56 (96 %) of a plain issue read | `linear-tracker.md`: "The issue payload's `adrs` field is a project-level retrieval index, never a delivery relation"; the note of `query.py::issue`; the ADR retrieval rule (`AGENTS.md#R2`) | Yes: it changes the output, so it conflicts with "byte-identical outputs" unless the maintainer accepts a new option |
| 6 | Merge the reads of `issue merge`, `openpr`, `edit comment`, a real `close-epic` | **Not measured** | Same as 4 for writes | Measure first |
| 7 | Drop the second read of the Epic in `read_epic_closure_state_with_id` (`close-epic --status`) | 1 of 2 (measured total, repetition read in code) | None found: the docstring already promises one read | No |
