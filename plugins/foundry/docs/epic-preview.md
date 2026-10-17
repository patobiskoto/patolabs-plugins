# Deterministic Epic execution preview

The Epic preview is a read-only checkpoint between a tracker's Epic graph (Dev Hub or
Linear) and any future Foundry campaign. Foundry alone resolves routing and retains orchestration, review, CI, human and merge gates.
Generating a preview creates no branch, agent, command, tracker mutation, budget
reservation, lease, PR, merge or external effect.

## Input boundary

Two graph contracts are accepted (`GRAPH_CONTRACTS`, `epic_preview.py`):

- `devhub-application.v1`: `DevHubTracker.get_epic_subgraph()` performs one authenticated
  `GET` of the strict projection at its maximum supported depth and node budget.
- `foundry-epic-subgraph.v1` (PAT-ADR-0027): `LinearTracker.get_epic_subgraph()` calls
  `build_neutral_subgraph` (`epic_subgraph.py`), which reads the hierarchy (`parent-of`,
  depth at most `PREVIEW_DEPTH`, at most `PREVIEW_NODE_LIMIT` nodes) and the `depends-on`
  links through `validate_issue_binding` and `get_issue` inside one `graph_snapshot`. It
  has the Dev Hub keys plus `parent_validation_digest` (`write.epic_parent_validation_digest`
  of the Epic); a node `version` may be `null`; `blockers` lists every `depends-on` whose
  target is outside the subgraph, whatever its state, and a `done` one never blocks. A node
  outside the project, a duplicate, a cycle, an exceeded cap or an unreadable node is
  refused (`EpicPreviewError` or the adapter's own error); a nested connection of more than
  100 items is refused by `_connection` (`linear.py`). Reads of external `depends-on`
  targets stop at `PREVIEW_COLLECTION_LIMIT` distinct targets (400, also the ceiling of
  `edges` and `blockers`): the 401st is refused before it is read.

Two Linear conditions refuse the whole preview rather than degrade it (read in code; whether
a real workspace meets them is not observed): an issue estimate of 0 fails node validation
(`_validate_node`, `minimum=1` for `estimate`), and an issue with more than 100 comments
fails the read, because `_ISSUE_FIELDS` requests `comments(first: 100)` and `_connection`
rejects a further page (`nested_pagination_unavailable`).

A tracker whose `epic_subgraph_supported` is false is refused before any read. The
pure `build_preview(graph, policy, routing_policy, tracker_name=...)` boundary then requires:

- one Epic root and a complete, single-parent descendant hierarchy;
- known nodes (versioned for `devhub-application.v1`, version nullable for
  `foundry-epic-subgraph.v1`), canonical in-scope edges and consistent blocker records;
- exact progress/AC rollups, reached depth and explicit non-truncation;
- acyclic hierarchy and dependency graphs;
- an explicit host, issuance time, expiry and optional budget/concurrency ceilings;
- an explicit dynamic implementer floor and controlled source when such a floor exists.

Missing schema keys, unknown states, stale aggregates, duplicate edges, absent
endpoints, cycles and any `depth`, `nodes`, `edges` or `source` truncation fail closed.
Nullable execution data is not filled in: it appears under `unknowns`, and a missing
issue type blocks that issue rather than guessing a code workflow. Zero observed AC is
an explicit human gate and unknown, never inferred completion.

## Output and digest

The `foundry-epic-preview.v1` result contains:

- the graph's own contract, root, canonical snapshot digest and every node version
  (`null` for a versionless node); `authority.tracker` is the `tracker_name` argument of `build_preview` (the CLI passes the tracker's name); omitted, it is `devhub` for `devhub-application.v1` and refused for `foundry-epic-subgraph.v1`;
- the effective routing policy, planned implementer/reviewer tier, model and effort;
- eligible, blocked and omitted summaries plus explicit human gates;
- stable topological waves, split deterministically by the concurrency ceiling;
- budget/concurrency ceilings, expiry, unknowns and the fixed authority boundary;
- `preview_digest`, SHA-256 over every material field above;
- `structure_digest`, outside that material so the preview digest is not changed by it.

Canonical JSON uses sorted object keys and compact UTF-8 encoding. The same graph,
policy and routing mapping therefore produce the same digest. A version, state, edge,
blocker, AC count, risk signal, tier/model mapping, ceiling or expiry change changes the
digest. Consumers must revalidate that digest and expiry before any later effect; the
preview itself cannot perform that effect.

High-risk issue labels (`risk:high`, `risk/high`, `risk-high` or `high-risk`) impose a
`frontier` implementer floor. Critical risk imposes `apex`. An explicit policy floor may
only make that effective floor stricter. Reviewer retains Foundry's existing
`frontier/high` gate floor.

## Structure digest

`structure_digest(graph)` (`epic_preview.py`, tag `STRUCTURE_CONTRACT`) is the SHA-256 of
the canonical JSON of: the root, the sorted node ids with their types, the `subtask-of`
edges, the internal `depends-on` edges, the sorted external blocker pairs
(`issue`, `blocked_by`, without their states), the graph's own contract
(`graph_contract`, its `schema_version`) and the parent component. The same Epic read under
the two graph contracts therefore has two different structure digests. A new child, an
internal edge, a type or an external blocker changes it; a state, a version, a title or a
label does not, nor, for `foundry-epic-subgraph.v1`, an external blocker turning `done`
(tests: `tests/test_epic_subgraph.py`). Whether the `devhub-application.v1` projection lists
a `done` external blocker is unknown here: that projection is read as served and no test or
fixture of this repository shows it. If it omits one, that pair leaves the projection and the
digest changes.
The parent component is the Epic's `parent_validation_digest` for
`foundry-epic-subgraph.v1`; `devhub-application.v1` carries no parent body, so it is the
named absence `{"unavailable": ...}`. Detecting drift from this digest is not implemented
here.

## Read-only command

The CLI deliberately requires stable time coordinates instead of reading the clock:

```text
python3 tooling/foundry_cli.py epic-preview APP-1 \
  --host codex \
  --issued-at 1788200000000 \
  --expires-at 1788203600000 \
  --max-concurrency 2 \
  --budget-unit tokens \
  --budget-limit 100000 \
  --implementer-floor frontier \
  --implementer-floor-source concurrency_risk
```

Omitting a budget or concurrency ceiling is allowed only so the preview can report the
coordinate under `unknowns`; it does not create an implicit unlimited approval. Other
trackers fail with `EpicSubgraphUnavailableError` until they expose an equivalent
complete read contract. On Linear, `FOUNDRY_REQUEST_COUNT=stderr` reports the number of
requests (`REQUEST_COUNT_ENV`, `linear.py`).
