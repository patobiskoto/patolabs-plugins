# F117 isolated replay-bundle preparation

`benchmarks/foundry-117/replay_bundles.py` implements only the offline preparation
boundary accepted in FOUNDRY-ADR-0022. It freezes one local fixture snapshot and
pre-registers the complete `2 arms x 3 tiers x 1 replay` matrix before any native
boundary. Every entry names its coordinate, checkout, independent escalation and
receipt roots, provisioned tracker/PR identities, bounded native-session graph, and
derived budget.

## Declared graph and derived preflight

The provisioner does not invent a production `PipelineDriver` session count. Its
caller must supply one versioned graph for each arm. A graph names an entry stage,
all allowed transitions, and a positive `max_visits` bound for every stage. Validation
requires every stage to be reachable from the entry and able to reach a terminal
stage, then derives the replay's native-session cap as the sum of those declared
visit bounds. Cycles are permitted only because every stage remains explicitly
bounded.

The caller also declares the proposed per-call cap. The manifest derives each replay
cap (`per-call cap x native-session cap`), each three-tier arm cap, and the pair cap.
It repeats the normalized graph and derived replay budget in every bundle and stores
the aggregate budget once. Validation recomputes all of them and refuses graph drift,
tier asymmetry, or a changed derived amount. The exact graph and amounts are returned
unchanged in the runner handoff so a future runner can calculate and present the exact
preflight for operator approval. No number in this preparation contract claims to be
a valid production graph bound before such a graph is supplied and reviewed.

## Isolation and identities

Preparation creates a fresh, single-use namespace under the requested destination.
Its generated-by-default 128-bit provisioning identity (or an explicit 128-bit
identity for deterministic offline preparation) and canonical destination derive distinct
tracker-issue and pull-request reservation tokens for every bundle. These tokens are
durable offline identities, not live issue IDs, PR numbers, or URLs. The provisioner
never contacts a tracker or code host, never creates or resets a live issue or PR,
and refuses an existing namespace instead of overwriting it.

Bundle roots are coordinate-derived and mutually non-nested. Checkout, escalation,
and receipt paths are distinct siblings inside each bundle and cannot overlap another
bundle or the frozen snapshot. The destination and snapshot cannot contain one
another. Snapshot symlinks, non-regular files, drift, missing/reused identities, path
changes, graph changes, budget changes, and any existing provisioning namespace all
refuse before a native effect.

The canonical manifest is written last with exclusive creation. An interrupted
preparation is therefore visibly incomplete and cannot be restituted to a runner; it
is never reset or silently reused.

## Offline runner handoff and limits

`provision()` returns a closed `RunnerHandoff` only after all six checkouts match the
same snapshot and every escalation/receipt root is fresh and empty.
`restitute_for_runner()` and `validate_runner_handoff()` re-read the durable manifest,
revalidate every path, provisioned identity, graph, budget, and checkout, and bind the
handoff to the canonical manifest SHA-256. This typed JSON-compatible object is the
offline runner boundary for FOUNDRY-147; it deliberately does not pretend to integrate
with an unmerged or absent native runner.

Nothing in the manifest or handoff is a signature, operator approval, campaign grant,
tracker mutation authority, native-model invocation authority, retry grant, or
comparison result. A later signed plan must bind exact newly created live coordinates
and exact preflight amounts, and the operator must approve those amounts before any
campaign or native effect. Between isolated replays, ADR-0022 permits only the
append-only comparison ledger, an attested escalation decision, and known costs; no
terminal issue, PR, escalation root, receipt space, or session may be reset or reused.
