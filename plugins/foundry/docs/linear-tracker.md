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
data, or private URL. PAT-23 owns the bounded historical ADR import; PAT-10 owns
the final verified repository cutover. PAT-22 activates neither binding nor dual-write.

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
atomic audited non-code Epic closure, project provisioning, and free-form provider-native
search queries. ADRs are stored as project-scoped Linear Documents, never substituted by
a Git catalogue or YouTrack read. A document has a deterministic UUIDv4 client ID for its
`(project, ADR, version)` slot, a closed metadata header and body/content digests. Every
version also has a second deterministic UUIDv4 project Document: its witness binds the version
ID, the exact content hash returned by Linear, and (for newly written witnesses) the exact
UTF-8 source body as canonical base64 plus its SHA-256. The version remains readable Markdown;
the witness is the durable, reversible source representation. Reads require the pair in both
directions. An isolated deletion
of the only version, the head version, or its witness is therefore a conflict rather than
an empty/older history. The two creates are not a Linear transaction: an interrupted
response is recovered only by exact deterministic-ID readback; a process stop or provider
failure between them leaves a detectable incomplete pair and all normal ADR reads fail
closed. Replaying the byte-identical native creation can complete exactly one surviving
`proposed` version-0 slot after validating every other pair and relation; a different
title/body, more than one incomplete pair, or an orphan witness remains fail-closed. Legacy
witnesses without the source field remain compatible only when the readable body itself still
matches its declared source digest byte-for-byte.

Linear's Markdown readback escapes the closing delimiter of these ADR HTML comments:
the canonical version separator sent as `\n-->\n\n` is returned as
`\n\\-->\n\n`, and the witness suffix sent as `\n-->` is returned as
`\n\\-->`. In the version comment header it also escapes JSON array brackets as
`\\[` and `\\]`. The observed PAT-ADR-0001 readback also serializes its three column-0
`- ` list markers as `* `. The adapter models that rewrite only for column-0 markers outside
fenced code. It recognizes backtick and tilde fence openers of at least three characters, with
up to three leading spaces and an optional info string, and only a same-character closing fence
at least as long as its opener. A `- ` literal inside such a fence and a source `* ` marker stay
byte-exact; a dash thematic break such as `- - -` is preserved too. A provider response that
rewrites any of those is rejected. The bounded model does not parse raw HTML: a line outside a
fence that begins with `<` after up to three spaces is rejected before any provider write,
including reciprocal issue-link comments, batch comments and version creation. The sole
exception is the versioned `foundry-adr-0012-v1` profile: its exact source-body SHA-256
is `69bd2cb2be04313de06dee865187b5e0e68eaed00298a7ce2be124f6ca1e96e5`, and its
only accepted readback SHA-256 is
`3bbab7c31d737bb97320ea74644f8acd6201cf7ec0a8cb7e1a09cd554c49246c`. It records
the separately qualified FOUNDRY-ADR-0012 rendering only: blank lines inserted between
its four headings and angle-bracket placeholders, one top-level dash marker changed to
a star marker, and its final newline removed. The adapter does not use this as a generic
HTML rule. Any source-byte variation or other raw HTML remains rejected before effects;
the source bytes must not be changed to make it fit.
The adapter accepts canonical content or exactly the
complete supported serialization for a known source candidate. It never treats `- ` and `* ` as
equivalent source bytes: the witness-retained source and its digest distinguish them even when
their readable Markdown is identical. It does not apply a reverse rewrite to provider content
or normalize any other Markdown variant.
`FOUNDRY-ADR-0001` version 0 has a separate recovery-only qualification: source-body
SHA-256 `eea144009b8ee8ff5846051ed70fe35d1cf920a78cb4de0ba74d2d616f8535db`
and its existing Linear Document content SHA-256
`9d723a7a64225d930531d968f78dba108f940eb7091d7110f8b12c037d29b193` form one
closed pair. This does not describe a renderer and cannot create a new Document from
the historical source. It permits only exact-ID readback of that surviving slot and
creation of its missing witness; a missing slot or any byte difference fails before a
provider write. The witness retains the original UTF-8 source bytes, so ordinary ADR
readers receive the historical Markdown byte-for-byte rather than the provider's
lossy rendering.
Every other historical batch record must carry a private, exact readback profile:
the source digest, the complete body bytes returned by a non-ADR Linear probe, and
the SHA-256 of those returned bytes. The adapter verifies every profile and composes
the already-qualified ADR header serialization with that exact body before any batch
write. A missing profile, a digest mismatch, or a different returned version byte
refuses the batch; it never falls back to a local Markdown renderer. The probe
Documents are non-authoritative evidence only, are not ADRs, and are retained rather
than silently deleted. Their IDs and private source/readback bytes belong in the
private migration receipt, never this repository. Once an ADR's witness exists,
ordinary reads use the witness-held source body and the bound provider content digest,
not the private manifest or probe Documents.
The probes contain standalone bodies. They do not prove Linear will render the body
identically beneath an ADR metadata header; if that context changes the readback,
the attempted version remains unwitnessed and the batch stops. Recovery then needs
a separately qualified exact-slot profile before any further import.
Exact-slot verification still checks
the deterministic ID, title, project, archive state, canonical metadata encoding, and
body digest. Witnesses bind the exact version bytes returned by Linear, including the
inserted backslash, and later versions bind the same exact readback bytes through
`previous_sha256`. Extra backslashes, whitespace, altered delimiters, re-encoded JSON, or
body changes therefore remain conflicts or invalid provider responses. A source-body edit,
base64 re-encoding, digest edit, readable-body edit, or transformation outside the closed
serialization rule fails closed.

The pre-existing interrupted `FOUNDRY-ADR-0001` version-0 slot is recoverable only by replaying
the exact original source body. Recovery checks its deterministic Document ID and title,
metadata and source digest, project, archive state, and the exact provider serialization before
creating only the missing witness. It never updates or deletes the surviving version and never
allocates a second version ID; a native `* ` source that merely renders the same is rejected.

Normal reads also fail closed on a later-version partial write; only the matching typed
operation may complete its exact missing witness after hypothetical whole-graph validation.
The public `adr accept`, `adr edit`, `adr supersede`, and `adr link-issue` commands
resolve a witnessed predecessor through the mutation-only port, so an incomplete
pair cannot make the recovery command unreachable. This port never repairs on read:
the typed operation must prove the exact candidate before appending or completing
its witness. Recovery may inspect the closed metadata of the single unwitnessed version,
but that lossy readback is never used as its source body. Status and link retries derive
the source byte-for-byte from the intact predecessor witness; edit retries use the exact
requested updated body after matching the expected predecessor; supersession derives each
side from its own intact predecessor witness. The derived deterministic slot must match the
surviving ID, title, metadata, and the one accepted provider serialization before a source-
bearing witness can be created. An orphan witness, a foreign or additional incomplete
version, an edited readback, an unknown serialization, or a different typed request remains
a conflict with no repair effect. After an `adr edit` version and witness are both durable,
replaying the
same expected and updated files reconstructs that exact successor from its witnessed
predecessor and returns unchanged without another provider write. That no-op proof
requires an intact chain: changed files, a damaged witness, or a missing predecessor
witness remain conflicts before any effect. A missing witness on the exact derived
successor still follows the bounded interrupted-pair recovery above. A non-object entry
in Linear's document list is an invalid provider response, not an empty ADR index.

Reads also reject holes, forks, archive/deletion, metadata edits and project mismatch.
Each additive version must have exactly one typed delta: body, status, source
supersession, one reciprocal `supersedes` addition, or one canonical ADR↔issue link;
combined deltas fail closed even
when a matching witness exists.
`supersedes` and `superseded_by` must be reciprocal in the latest project snapshot;
self-links, duplicates, missing ADRs, and more than 100 relations of either kind are
refused. Before an import can write anything, every supplied issue reference is resolved
through Linear and corroborated by both its returned readable identifier and native ID.
Case variants and native UUID aliases are stored only as the canonical readable identifier;
two inputs that resolve to the same native issue are a conflict, not two relations. The
100-link bound applies before resolution and to the resulting canonical identity set.
Reads and replay reject noncanonical stored aliases or malformed provider identities.
Every issue relation is re-read and must belong to the configured team and project, and
the issue must retain its deterministic reciprocal Foundry comment bound to the project,
ADR, canonical readable issue ID, and exact native issue ID. Its deterministic client ID
is UUIDv4, as Linear requires.
The comment is created additively before the imported or native ADR
version, uses exact-ID readback, and is replay-safe; an interrupted link can leave a
harmless orphan comment but cannot expose a one-sided ADR relation. A missing issue/ADR
has its own typed unavailable error; a transport or GraphQL failure remains a provider
error and is never reclassified as a missing relation.
Supersession appends both sides. These multiple Document creates are provider-additive but
not provider-atomic. The exact command replay returns idempotently when both sides are
present; if the replacement side completed and the source side did not, the same command
may append only the missing source after checking both complete chains and the hypothetical
reciprocal graph. If that exact source Document exists but its witness is missing,
the replay verifies the byte-exact source candidate before completing only its witness.
If the replacement-side version exists without its witness, the same command first
derives its source from the replacement's intact predecessor witness, verifies both
hypothetical sides and the complete reciprocal graph, completes that exact witness, then
appends the source. The source-side missing-witness case is proven by the same complete
hypothetical graph before its witness is written; no legacy witness without canonical source
is synthesized during recovery.
A different pair or a second incomplete slot remains fail-closed. No incomplete graph is
accepted as a read.

Writes append a version rather than updating a document. Native creation starts
`proposed`; status transitions and supersession are typed and constrained. The provider-
neutral `link_adr_issue` port is exposed as `adr link-issue <ADR-ID> <ISSUE-ID>` and
`frame` links newly created issues to their constraining ADRs when the provider supports
that port. The one-relation append stores the canonical readable Linear identifier and
requires the deterministic reciprocal comment; replay of the exact link is idempotent.
If its version was written but its witness was interrupted, replay first validates the
exact version, reciprocal comment and hypothetical full graph, then completes only the
missing witness. A changed target or another incomplete slot cannot authorize repair.
The provider-
neutral historical-import port is distinct from native creation and defaults to a typed
unsupported capability on trackers that do not implement it. Linear records the source
reference, timestamps, source digest, historical status, relations, and exact target issue
scope without invoking acceptance. Omitted relation arguments remain explicit in
`origin.missing_relations`; an omitted value is never presented as a known-empty
source relation, and an exact replay cannot replace it with a newly asserted empty
value. In particular, a historical `superseded` status with an unknown successor is
preserved as `superseded` only when `superseded_by` is omitted and recorded as unknown;
an explicitly known-empty successor is invalid. The production import must pass all
three relation arguments explicitly after
source qualification. A related ADR must already be present and accepted
where it serves as a replacement; arbitrary mutually referencing batches are not seeded
by the single-record port. The separate `import_adr_batch(project, records)` port
accepts a finite manifest of 1–100 complete historical snapshots with all three
relation fields explicit. It constructs every deterministic version-0 Document,
witness and reciprocal issue comment, validates the full hypothetical graph before
the first write, then creates the exact slots additively. Ordinary reads fail closed
through a partial batch; each version-0 migration origin persists a SHA-256 digest of
the complete normalized manifest, propagated to later versions. An exact replay can
complete matching slots regardless of record order; a subset or changed manifest
collides before effects, while unrelated conflicts are also refused. This is not a
provider-atomic transaction and is not permission to migrate the entire archive. PAT-23 owns the
authorized manifest and audit; PAT-10 owns cutover.
When import stops after version 0 (with or without its witness) but before reciprocal
relation versions, an exact replay preflights the completed hypothetical graph before
adding the missing witness and versions. It also completes a multi-relation import that
stopped after the first reciprocal version, including when that exact version exists but
its witness does not. The replay requires that this is the only incomplete version pair
and that it is one of the reciprocal versions derived from the byte-identical import;
other graph conflicts and a changed source snapshot remain fail-closed. This recovery is
asymmetric because each version is written before its witness: a present version with its
exact witness missing may be completed, while a present witness whose matching version
Document is missing is an orphan and is refused before any `DocumentCreate` or
`CommentCreate` effect. Batch reciprocal comments have a separate, earlier write order:
they are all created before the first batch Document. Therefore an exact replay may fill
a missing reciprocal comment only while no Document slot from that manifest exists. Once
a version or witness is durable, an absent deterministic comment is treated as an
external deletion and the replay refuses before any `DocumentCreate` or `CommentCreate`
effect; it never invokes the comment-creation path or recreates the relation. A deletion
interleaved after graph validation is caught by the final snapshot read. On a completed
batch this produces no creation effect; on an advancing partial batch, missing Documents
may already have been created before that final conflict, with no atomic rollback.
For Linear `frame`, every `constrained_by` reference is resolved before creating any
ADR, epic or issue; an unknown, ambiguous, unreadable, deprecated, or superseded
reference cannot leave an issue with only its first reciprocal link. Indexes, incoming
titles, and existing ADR IDs must name exactly one ADR: a collision is refused rather
than selecting whichever alias was inserted last. Only `proposed` and `accepted` ADRs
are active constraints. Provider multi-object failure after this preflight remains
non-atomic and must not be mistaken for a completed frame.

### Controlled production recipe

The fake transport is the executable proof shipped by this repository. A production
operator must separately, under the cutover authority:

1. qualify the exact Linear workspace/team/project and record the stable IDs described
   above; keep the existing repository binding unchanged;
2. export only the authorized live YouTrack ADR closure, retain each source reference and
   timestamps, compute the SHA-256 of the byte-exact body, and map every issue relation to
   an already qualified target-project issue;
3. validate the entire authorized manifest offline with the same closed metadata and
   relation rules; unresolved or out-of-scope references stop the run;
4. in a maintenance window, use the single-record port for independently seedable
   records and the batch port for a closed reciprocal group, then read the complete
   Linear ADR index back and compare IDs,
   status, provenance, body digests, reciprocal relations, version/witness pairs, team and
   project IDs;
5. only after that independent readback may PAT-10 atomically change the repository
   binding. Do not dual-write, accept imported ADRs implicitly, or delete the YouTrack
   archive.

No step above was run by PAT-22. This repository performs no real ADR write, historical
import, or binding cutover. The witness is independent as a second provider object, not an
immutable external transparency log. A workspace actor able to delete both a version and
its witness can erase that pair without a surviving anchor; deleting every ADR and every
witness is information-theoretically indistinguishable from a project that never had an
ADR. Detecting coordinated erasure requires a separately decided external durable anchor.

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
rewrites and infers no receipt. The `state-in-progress` step replayed by Foundry's
merge path is a no-op once any durable review generation exists, including after a
corrected PR adds a later review on native `in-progress`. This avoids a late start
receipt being reordered before earlier reviews; the following exact review and CI
gates remain mandatory. Native `done`, `blocked`, `dropped`, a different source
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
transport in `tests/test_linear_tracker.py`: publish a review receipt with and without
a prior start receipt, mutate only the fake native state after review, replay the
start and review steps that `issue.merge` uses, add a corrected PR review generation,
replay again, and assert review/PR/AC and comments remain byte-for-byte unchanged;
then assert Done, Blocked, a non-backlog source and an
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
validation, and the atomic cutover remain PAT-23/PAT-10 work.
