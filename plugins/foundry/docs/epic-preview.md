# Deterministic Epic execution preview

The Epic preview is a read-only checkpoint between Dev Hub's Tracker graph and any
future Foundry campaign. Dev Hub remains the tracker and graph-contract owner. Foundry
alone resolves routing and retains orchestration, review, CI, human and merge gates.
Generating a preview creates no branch, agent, command, tracker mutation, budget
reservation, lease, PR, merge or external effect.

## Input boundary

`DevHubTracker.get_epic_subgraph()` performs one authenticated `GET` of the strict
`devhub-application.v1` projection at its maximum supported depth and node budget. The
pure `build_preview(graph, policy, routing_policy)` boundary then requires:

- one versioned Epic root and a complete, single-parent descendant hierarchy;
- known versioned nodes, canonical in-scope edges and consistent blocker records;
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

- the Dev Hub contract, root, canonical snapshot digest and every node version;
- the effective routing policy, planned implementer/reviewer tier, model and effort;
- eligible, blocked and omitted summaries plus explicit human gates;
- stable topological waves, split deterministically by the concurrency ceiling;
- budget/concurrency ceilings, expiry, unknowns and the fixed authority boundary;
- `preview_digest`, SHA-256 over every material field above.

Canonical JSON uses sorted object keys and compact UTF-8 encoding. The same graph,
policy and routing mapping therefore produce the same digest. A version, state, edge,
blocker, AC count, risk signal, tier/model mapping, ceiling or expiry change changes the
digest. Consumers must revalidate that digest and expiry before any later effect; the
preview itself cannot perform that effect.

High-risk issue labels (`risk:high`, `risk/high`, `risk-high` or `high-risk`) impose a
`frontier` implementer floor. Critical risk imposes `apex`. An explicit policy floor may
only make that effective floor stricter. Reviewer retains Foundry's existing
`frontier/high` gate floor.

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
complete versioned read contract.
