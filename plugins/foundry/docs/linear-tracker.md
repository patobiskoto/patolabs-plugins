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
priority, labels and parent remain unchanged.

Each receipt uses a deterministic client-supplied Linear comment UUID derived from its
canonical payload. Foundry reads before creation, rereads that exact comment after the
effect and rereads the issue projection. An identical marker is a replay no-op. A second
marker for the same operation, a different coordinate, malformed content, duplicate
projection, changed native state or divergent readback refuses. If the provider accepted
the deterministic comment but its response was interrupted, retry recovers it by exact
ID. This is idempotence for the Foundry lifecycle comments, not a claim that arbitrary
Linear comments or issue creation are exactly once.

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
- Foundry concurrency guarantee: one canonical receipt per issue and operation; replay
  is idempotent and any competing/different projection fails closed.
- Unsupported provider capability: replacement of native state, priority, description,
  checkbox, labels, parent or PR field, plus atomic audited Epic closure. These remain
  typed refusals rather than best-effort read/write sequences.

Operationally, Linear's web board continues to show its native state and unchecked body;
the proven state and AC completion are visible through Foundry queries and the audit
comments. Editing a receipt, changing the native state, duplicating a marker or exceeding
the bounded 100-comment projection makes reads fail closed. Deleting the only receipt can
make the derived fact disappear because Linear supplies no immutable append log; the next
write probes its deterministic comment ID but an ordinary read cannot prove that a row
was deleted. The marker hash detects modification but is not a Foundry signature: the
workspace's Linear authorization remains the trust boundary. Linear permits comment
update/deletion, so “append-only” describes Foundry's write discipline, not
provider-enforced immutability. Epic closure remains unavailable because it requires a
provider-atomic parent/child audit.

This implementation and its controlled transport round-trip do not activate a real
workspace. No Linear binding or live write is performed here. Import, target-workspace
validation, and the atomic cutover remain FOUNDRY-159 work.
