# Bounded query profiles

`foundry_cli.py query profile <next-issue|roadmap|blockers|groom|intake>` exposes a
versioned, provider-neutral `foundry.query-profile.v1` envelope. It is additive:
unprofiled `backlog`, `candidates`, `milestones`, `issue`, `adrs` and `adr` preserve
their existing compatibility contracts.

Each profile returns normalized lean issues and never returns `body` or `comments`.
Full issue text remains exclusively `query issue <ID>` and an ADR body exclusively
`query adr <ADR-ID>`. The CLI provides signals and graph facts; an agent makes every
recommendation, roadmap and grooming judgment.

The envelope always publishes `snapshot` (version and SHA-256 identity), `selection`,
`total_count`, `returned_count`, `omitted_count`, `pagination`, `sections`, `truncated`
and `truncation`. `sections.issues`, `sections.historical_index`, and
`sections.milestones` each publish their own total/returned/omitted counts, page, limit,
page count, continuation, overrun state, and the same snapshot identity. A single numeric
page advances all applicable sections; call `query profile <workflow> <page>` while the
top-level `pagination.next_page` is present. Empty section slices on later pages are
normal when another section has more pages. Combine results only when every snapshot
identity matches.

A profile includes complete connected components over known normalized links, so a
returned issue never has a known parent, child or dependency silently absent.
Unknown/cross-project targets remain visible as links. Components are atomic pages. A
component larger than the nominal limit is returned whole and marked in both
`limit_exceeded` and `sections.issues`; `any_page_limit_exceeded` keeps that fact visible
throughout traversal. This is not truncation because the graph remains complete.
`truncated` remains false unless a future contract actually removes returned material.
Large `omitted_ids` lists are not returned: exact selection counts are in `selection` and
`sections`, while `query backlog` is the explicit full-list retrieval path.

Selection is workflow-specific and exact in `selection.criteria`: `next-issue` keeps
all non-terminal work, including an explicit `backlog` focus; `blockers` also keeps the
active graph needed to reason about proposed-ADR gates; `roadmap` paginates the full
milestone rollup (including completed historical trajectory); and `intake` includes a
paginated compact terminal index; `groom` receives the same paginated historical
estimates/statuses for comparison.
Those indexes are candidate recovery only: load `query issue <ID>` before calling
historical work a duplicate or judging its quality. Profile issue rows retain
links, AC progress, PR, blocked/unblocked signals, `created`, `updated`, and the global
deterministic prior `rank_index`. Profiles do not claim a page-local `rank_in_view`;
`rank_index` remains comparable across every page of one snapshot. Provider concurrency
`version` is omitted because it is not a list-level judgment input. Milestone rows carry
aggregate `blocked` and `wip` counts rather than unbounded nested ID lists. Exact active
IDs and their states, milestones, and blocker relations are derived from the union of all
`issues` pages. Historical IDs are recovered from every `historical_index` slice.

## Audit measurements

The profile CLI uses compact JSON; existing query commands keep their established
pretty-printed format. `foundry.query_measure.measure_sequence()` calls that same
canonical serializer and emits a named row for every individual command/page, including
serialized bytes, lines, nullable token metrics, observed tracker adapter calls, nullable
HTTP calls, and duration. Workflow aggregates are exact sums of those command rows.
`null` means unavailable; it is never zero and never a billable-token claim. The
versioned FOUNDRY-74 evidence uses a sanitized synthetic graph
because the live backlog is private. It measures the real initial sequences: backlog for
next-issue/groom; milestones + backlog + ADR index for roadmap; backlog + ADR index for
blockers/intake. Updated rows count every profile page, roadmap's embedded rollup, and
the required ADR indexes. Optional detail drill-downs are outside both fixed sequences.

The read-only YouTrack smoke is opt-in: set `FOUNDRY_QUERY_SMOKE=1` and
`FOUNDRY_QUERY_SMOKE_PROJECT_KEY=<key>` alongside the normal secret configuration, then
run `cd plugins/foundry && python3 -m pytest -m integration
tests/integration/test_real_youtrack_query_profile_smoke.py`.
It uses a small configurable `FOUNDRY_QUERY_SMOKE_PAGE_SIZE` (default 10), records real
`$skip` offsets while delegating every request to YouTrack, and asserts multiple distinct
pages plus normalized active/historical fields. It creates, updates and deletes nothing.

Reproduce the checked-in sanitized evidence from the repository root with:

```bash
python3 plugins/foundry/benchmarks/foundry-74/measure-v1.py > /tmp/foundry-74-baseline.json
```

Compare that JSON with `benchmarks/foundry-74/baseline-v1.json`; local durations vary,
while command identities, byte/line totals, call counts, reductions and corpus counts are
deterministic and covered by the contract tests.
