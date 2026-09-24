# Linear tracker adapter

Foundry's Linear adapter is an explicit, single-provider binding. Runtime reads, queries,
proofs, and writes select the binding by comparing the actual checkout's credential-free
canonical Git remote with `canonical_repo`; `PROJECT_REPO` and repository basenames are
never identity evidence for Linear. It never discovers a team, project, state, label,
milestone, or issue from display text. The GraphQL endpoint is fixed to
`https://api.linear.app/graphql`; there is no YouTrack fallback.

## Required binding

Store the credential interactively so it never appears in argv, chat, the registry, or
documentation examples:

```text
python3 <plugin-root>/tooling/foundry_cli.py configure credential --name LINEAR_API_TOKEN
python3 <plugin-root>/tooling/foundry_cli.py configure set --tracker linear --codehost github
```

On non-macOS hosts, supply `LINEAR_API_TOKEN` through a secret manager. Registry entries
still have a local alias key for storage, but runtime selection ignores that alias and
matches the checkout's canonical remote. Register the `linear` binding with all of these
non-secret values:

- `key`: the Foundry/project ticker used in normalized issue identifiers;
- `id`: the Linear project model UUID;
- `canonical_repo`: exact `host/owner/repository` identity;
- `team_id`: the Linear team model UUID;
- `state_ids`: JSON object mapping every Foundry state (`backlog`, `ready`,
  `in-progress`, `review`, `blocked`, `done`, `dropped`) to a distinct Linear workflow
  state UUID.

Optional JSON maps `milestone_ids` and `label_ids` bind normalized values to stable
Linear model UUIDs. `type_label_ids` is required for a registered Linear binding and
contains exactly `Epic`, `Feature`, `Bug`, and `Task`; the live qualification record
must additionally attest that these labels share Linear's exclusive `Type` group. Every
label or milestone returned by Linear must have an ID in the corresponding map; missing,
overlapping, or unknown IDs are binding errors. Normalized values come from these maps,
never from mutable display names. A create using an unmapped value is refused and the
adapter never searches by name. Keys in the optional maps cannot be URL- or
credential-shaped, and every map value must be a UUID.

`registry register` receives scalar extras as `k=v`. Pass each required map as a
shell-quoted JSON object. For Linear, the command rejects before writing unless the
project ID, team ID, seven exact state IDs, and four exact type-label IDs are UUIDs and
paired with an already canonical repository identity. The local repository alias,
project ID, team ID, and every UUID in the state, type, milestone, and label maps must
be globally distinct. `registry alias` reapplies this validation to a Linear binding
against the target alias before it writes, so a UUID-shaped alias cannot bypass that
uniqueness rule. It also refuses unrecognized extras: a token, endpoint, workspace URL,
or future setting cannot become a silent registry field.

Only a `tracker=linear` registration JSON-decodes `state_ids`, `type_label_ids`,
`milestone_ids`, and `label_ids`; each must decode to an object. Malformed or non-object
structured input is rejected before the registry is written. Every non-Linear provider
retains the historical scalar-extra contract, including for JSON-looking `k=v` values.

## Qualification evidence, distinct from registration

A structurally valid local binding is not evidence that a Linear workspace was observed,
and it never activates the provider. Record the qualification outside the public source
tree and registry: workspace/project/team readback, all seven state UUIDs with observed
labels, the four Type label UUIDs with their exclusive group, selected estimate scale,
empty or observed milestones, allowed operations, and the qualification-artifact check.
The record may contain provider identifiers and observations, but never a token, account
data, or private URL. FOUNDRY-159 alone may activate a verified binding or perform a
cutover; FOUNDRY-162 creates neither a migration nor a dual-write path.

## Exact support boundary

Supported reads are issue search/read, including explicitly mapped states, milestones,
types, labels, and a deliberately closed relation vocabulary. Hierarchy projects a
Linear parent as `subtask-of/inward` and each child as `parent-of/outward`. For a native
`blocks` edge, its source projects `blocks/inward` and its target projects
`depends-on/outward`; native `related` projects `relates/outward` from its source and
`relates/inward` from its target. Those are the only native relation kinds Foundry reads:
`duplicate`, `similar`, or any other kind fails the entire issue/search normalization
through a sanitized Linear provider error instead of returning partial issue data.

Supported writes are issue creation (with initial priority, estimate, state, mapped
milestone/type/labels, and optional parent), non-replacing blocking/dependency/related
relations, and comments. These supported writes round-trip only through the normalized
relations above; duplicate/similar writes are not exposed.

These writes have deliberately narrow delivery semantics. Creation sends one
`issueCreate` with a fresh client UUID and then reads the returned issue; retrying the
command creates a new request and can duplicate the issue. A relation call first observes
whether the relation exists, then sends at most one `issueRelationCreate` and reads back;
concurrent or ambiguous callers can still duplicate a relation. A comment call sends one
`commentCreate` and reads back that returned comment ID; retry after an ambiguous outcome
can duplicate the comment. None of these operations is advertised as exactly-once.

Linear exposes no compare-and-swap precondition for an existing issue. Field and state
replacement, changing the parent of an existing issue, issue-body replacement, and
acceptance-checkbox synchronization are therefore explicitly unavailable. A local lock,
pre-write read, or post-write readback cannot prevent an external writer from being
overwritten, so none is presented as an anti-overwrite guarantee.

Unsupported capabilities fail explicitly with typed errors: existing-issue replacement,
a true ADR knowledge base, atomic
audited non-code Epic closure, project provisioning, and free-form provider-native search
queries. `query issue` still returns the issue and projects the absent ADR knowledge base
as a structured capability status.

## Append-only lifecycle guarantee

For `start`, `openpr` and `merge`, Foundry uses no Linear replacement field. It writes
separate bounded comments for the projected `in-progress`, `review`, reviewed-AC and
`done` facts. The review receipt carries the exact PR URL, base SHA, head SHA and diff
digest. The AC receipt embeds the complete canonical all-pass review proof and binds it
to the byte-exact unchanged description. The done receipt repeats the review coordinates
and adds the exact merge SHA. Foundry queries validate these comments before deriving the
effective lifecycle state, PR URL or AC completion; Linear's native state, description,
priority, labels and parent are never replaced by Foundry. Native checked boxes are
deliberately not counted as proven completion: without the matching append-only review
receipt, Foundry reports them incomplete and requires the structured proof before merge.

Each receipt uses a deterministic client-supplied Linear comment UUID derived from its
canonical operation slot. Singleton operations use one issue+operation slot; review and
AC operations use one issue+operation+generation slot. The canonical payload remains in
the marker digest and body. Two writers proposing different payloads for one slot race on
the same provider-enforced UUID, so at most one can be created. Foundry reads before
creation, rereads that exact comment after the
effect and rereads the issue projection. An identical marker is a replay no-op. A
competing marker for a singleton operation or the same review generation, malformed
content, a broken generation chain, unauthorized native-state drift or divergent
readback refuses.
If the provider accepted
the deterministic comment but its response was interrupted, retry recovers it by exact
ID. A corrected PR creates the next review generation, chained to the digest of the
previous projection; its AC receipt is bound to that generation and supersedes older AC
evidence. Merge projects and reloads the current head's generation before AC validation,
then revalidates its state/PR/AC projection before the final exact-coordinate GitHub
read. Concurrent forks at one generation fail closed. This is idempotence for the
Foundry lifecycle comments, not a claim that arbitrary
Linear comments or issue creation are exactly once.

Exact comment lookup uses Linear's filtered `comments` connection with
`id == <deterministic UUID>` and `first: 1`; it does not use `comment(id:)`, whose
not-found response is a GraphQL error rather than a nullable absence. Only a valid empty
connection means “absent”. More than one row, a row with another ID, missing or malformed
pagination metadata, or `hasNextPage=true` fails closed before creation or recovery.
When the exact ID exists, its byte-exact body and bound issue must still match; otherwise
the slot is treated as a collision, never as a replay or an absent comment.

Native workflow snapshots may legitimately evolve while those receipts accumulate.
Foundry first validates every receipt's shape, integrity, review generation, AC proof and
merge coordinates, then orders their native-state IDs by lifecycle causality:
`state-in-progress`, each review generation and its acceptance proof, then `state-done`.
Every ID must belong to the repository's explicit Linear binding. Repeated IDs and
forward movement through `backlog|ready → in-progress → review → done` are accepted;
skipped stages are allowed, but regressions and movement through `blocked`, `dropped` or
an unmapped state are not. The issue's present native state must be the last durable
snapshot. A review or acceptance write may record one next forward native snapshot, but
an ordinary query remains fail-closed until that receipt exists.

Native `done` is stricter: it is never interpreted as Foundry completion by itself. A
normal read accepts it only when a valid `state-done` receipt snapshots that exact state,
matches the latest reviewed generation and its AC proof, and carries the exact merge
SHA. This includes issues with zero Foundry lifecycle comments: search and direct reads
fail closed instead of exposing native `done` through the native-state fallback.
Zero-receipt nonterminal issues keep their explicitly mapped native state. The
bounded `state-done` write path may recover the interval after GitHub has moved the native
issue to Done only when the reviewed AC proof is already durable; its readback must then
include the valid done receipt. Replays retain the receipt's original native-state
snapshot so a later legitimate snapshot cannot change a deterministic comment slot.

There is one observation-only exception for GitHub's delayed Linear `start` automation.
After an exact, durable `state-review` receipt whose last native snapshot is
`backlog` or `ready`, a normal Foundry read may observe native `in-progress` and still
project the already-receipted `review`, PR coordinates and AC result. It creates,
rewrites and infers no receipt. Native `done`, `blocked`, `dropped`, a different source
state, an unmapped state, or no valid review receipt still fail closed. This does not
authorize review, AC, CI, Done or merge: their existing exact-coordinate gates reread
the current projection and remain unchanged.

The cockpit evidence path is deliberately separate. Only a complete
`foundry-evidence-envelope.v1` that the shared verifier classifies `GO` can be projected;
the comment binds the issue, AC, PR, base/head/diff, review proof, test receipt and both CI
source receipts, then follows the same exact-ID readback. Missing, stale, failed or
otherwise incomplete evidence causes no provider write. Even a complete cockpit comment
is advisory: it never changes the projected lifecycle, checks an AC, authorizes a merge
or replaces Foundry's review, test and CI gates.

### Provider capability versus concurrency guarantee

- Linear provider capability: additive `commentCreate` with a caller-supplied ID and
  exact comment/issue readback.
- Foundry concurrency guarantee: one canonical receipt per issue and singleton operation,
  and one chained canonical receipt per review generation; replay is idempotent and any
  competing projection at the same generation fails closed.
- Unsupported provider capability: replacement of native state, priority, description,
  checkbox, labels, parent or PR field, plus atomic audited Epic closure. These remain
  typed refusals rather than best-effort read/write sequences.

## GitHub merge automation interlock

Linear may link a GitHub pull request to more than one issue.  A team-level Git
automation rule mapping the `merge` event to a workflow state of type `completed`
would consequently complete every linked issue when any one of those PRs lands.  That
is not delivery evidence for the other issues.

Immediately before its irreversible GitHub `merge_pr` call, the Linear adapter reads
the active bound team's `gitAutomationStates`.  A `merge` mapping to any `completed`
workflow state, an unavailable/malformed response, or a response requiring another
page refuses the merge before GitHub is called.  This is a read-only preflight; it does
not change Linear configuration and does not claim an atomic compare-and-swap over it.
An automation with `state: null` is a valid no-action rule.  The existing `start` and
`review` automations, and a `merge` mapping to Linear's distinct `duplicate` state, are
not prohibited by this check.

For a Foundry-managed Linear project, keep the team `merge -> Done` automation
disabled.  Foundry alone projects `done`, and only after its exact merged PR receipt,
current acceptance proof, review and CI gates.  The GitHub/Linear integration can still
link PRs; linking is not authority to complete every linked issue.

### Incident record and bounded rollback procedure

The Pato incident that motivated this interlock was PR #14 for PAT-21, merged at
`2026-09-23T14:49:20Z`.  At `14:49:22Z`, Linear's GitHub automation moved PAT-10 from
native In Progress (`49aa24ba-848c-4ccf-87c3-35569ad000ec`) to Done
(`f888eba8-d8b8-4a0c-911f-9dcbf0beb57e`; history event
`210e188f-c7a3-44f1-ba79-8ba862a05d5a`).  PAT-10's PR #12 was still draft, its ten
acceptance criteria were all open, and its Foundry lifecycle receipt was only
`state-review`.  This distinguishes a merged prerequisite from delivery of PAT-10.

The repaired team configuration has no `merge -> Done` git automation state; its
linking, start, and review rules remain in place.  PAT-10 was restored to its native
In Progress state and Foundry projects it as `review`; this was a guarded
preflight/readback repair, not a compare-and-swap `issueUpdate` guarantee (Linear does
not expose one).

PAT-27 exposed the complementary post-review case: after PR #18's durable
`state-review` receipt captured native Backlog, GitHub's delayed `start` integration
moved the native issue to In Progress. Before PAT-28, this made ordinary reads fail
closed and an authorized operator performed a bounded manual rollback to Backlog.
After PAT-28, no rollback is needed for precisely that receipt-backed
`backlog|ready -> in-progress` observation; Foundry continues to project review. There
is still no Linear CAS for a native-state repair. The regression recipe uses the fake
transport in `tests/test_linear_tracker.py`: publish a review receipt, mutate only the
fake native state after that receipt, assert review/PR/AC are unchanged and comments
are byte-for-byte untouched; then assert Done, Blocked, a non-backlog source and an
absent review receipt are refused. It performs no YouTrack or live Linear write.

If an authorized operator must roll back the configuration change, first stop all
Foundry merge attempts, record the exact current team automation IDs and workflow-state
IDs, and have the Linear administrator restore only the previously removed `merge`
mapping.  Read the active team's configuration back, then run the Foundry preflight
regression: it must refuse while that mapping targets a completed state.  Do not resume
Foundry merges until a separately authorized change removes that mapping again and a
fresh readback shows no `merge -> completed` rule.  This is a procedure only; Foundry
does not execute this rollback.

Operationally, Linear's web board continues to show its native state and unchecked body;
the proven state and AC completion are visible through Foundry queries and the audit
comments. Editing a receipt, changing native state without the bounded forward receipt
chain above, duplicating a marker or exceeding the bounded 100-comment projection makes
reads fail closed. Deleting the only receipt can make the derived fact disappear because
Linear supplies no immutable append log; the next write probes its deterministic comment
ID but an ordinary read cannot prove that a row was deleted. The marker hash detects
modification but is not a Foundry signature: the workspace's Linear authorization remains
the trust boundary. Linear permits comment update/deletion, so “append-only” describes
Foundry's write discipline, not provider-enforced immutability. Epic closure remains
unavailable because it requires a provider-atomic parent/child audit.

This implementation and its controlled transport round-trip do not activate a real
workspace. No Linear binding or live write is performed here. Import, target-workspace
validation, and the atomic cutover remain FOUNDRY-159 work.
