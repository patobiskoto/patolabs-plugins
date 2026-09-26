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
provider write. Because the pinned readback covers the metadata header, including
`origin.batch_sha256`, this recovery matches only a replay of the byte-identical
original manifest; any other manifest diverges from the slot before effects. The witness retains the original UTF-8 source bytes, so ordinary ADR
readers receive the historical Markdown byte-for-byte rather than the provider's
lossy rendering.
Every historical batch record must carry a qualification profile of the complete
provider bytes of both Documents the import will create, observed before import on
non-authoritative probe Documents in a separate Linear qualification project:

- `qualification_project_id`: one UUID for the whole batch, never the ADR project;
- `expected_linear_document_content`/`expected_linear_document_sha256` and
  `document_probe_id`: the complete version-0 Document (metadata header and body) as
  Linear returned it;
- `expected_linear_witness_content`/`expected_linear_witness_sha256` and
  `witness_probe_id`: the complete witness derived from that version readback, as Linear
  returned it.

The recovery-only `FOUNDRY-ADR-0001` record carries only the witness fields: its
surviving slot is its own version evidence. `plan_adr_batch_qualification(project,
records, observed_documents=None)` is the read-only planner the private campaign uses.
For records without profile fields it returns each exact canonical version Document,
its deterministic ID and title, and the probe title `[Foundry qualification probe]
<ADR-ID> v0000 document <sha256>` naming those canonical bytes. That title is the
operator's attestation of what the probe was created from, not provider proof; the
guarantee rests on the probe's exact returned content and the assumption below. Given
the observed version readbacks, it also returns each canonical witness and its
`... witness <sha256>` probe title. It performs no provider write, and the import sends
exactly the bytes it plans.

Controlled recipe: plan; create each canonical version on a probe in the qualification
project and read it back; plan again with those readbacks; create and read back each
canonical witness likewise; store the probe IDs and readbacks only in the private
migration receipt; then import. Before any batch effect, including reciprocal comments,
the adapter reads every probe by ID. It refuses the batch unless each probe exists, is
not archived, belongs to the declared qualification project, bears the exact title for
the canonical bytes, and holds exactly the profiled bytes. It also refuses a
qualification project equal to the ADR project or mixed across records, duplicate
probe IDs, a probe ID equal to an ADR slot, a digest mismatch, a version readback
whose metadata header is not the one qualified serialization of this exact metadata,
and a witness readback outside its closed modelled serialization. It never falls back
to a local Markdown renderer. The adapter does not judge the human readability of a
probed body: the campaign reviews each observed rendering and records it in the private
receipt before import. During import, the version and witness readbacks must
equal the profiled bytes exactly.

This proves, before any write to the ADR project, how Linear returned the identical
complete content. It rests on one explicit assumption: Linear renders identical
content identically in both projects. If Linear diverges at import, the version is
already written: the post-write check stops the batch before its witness. That stop is
the last defense, not pre-write proof; the unwitnessed slot then needs its own exact
recovery qualification before any further import. The probe Documents are
non-authoritative evidence only, are never ADRs and are retained rather than silently
deleted. Their IDs and private bytes belong in the private migration receipt, never
this repository. Once an ADR's witness exists, ordinary reads use the witness-held
source body and the bound provider content digest, not the private manifest or probe
Documents.

This probe-bound readback is accepted at read time only for a batch-imported
historical version 0 (`origin.kind` `migration` with `origin.batch_sha256`): its witness
binds the qualified provider bytes and the exact source. Every native ADR and every
later version keep the closed serialization check below, so a readable-body edit is
refused even if the witness digest is recomputed to match. For those historical
Documents the trade-off is explicit: a coordinated external edit of the readable body
and its witness digest is not detectable by the adapter, as PAT-ADR-0002 already states
for coordinated edits. A later typed version (accept, supersede, link, edit) of a
historical ADR whose qualified readback differs from the local serialization model
currently fails closed: its predecessor is read with the strict model.
Exact-slot verification still checks
the deterministic ID, title, project, archive state, canonical metadata encoding, and
body digest. Witnesses bind the exact version bytes returned by Linear, including the
inserted backslash, and later versions bind the same exact readback bytes through
`previous_sha256`. Extra backslashes, whitespace, altered delimiters, re-encoded JSON, or
body changes therefore remain conflicts or invalid provider responses. A source-body edit,
base64 re-encoding, digest edit, readable-body edit, or transformation outside the closed
serialization rule fails closed.

The pre-existing interrupted native `PAT-ADR-0001` version-0 slot is recoverable only by replaying
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

`query adr <ADR-ID>`, `query adrs`, `frame`, and every ADR write stay fail-closed on any
of the conflicts above — none of them repair or normalize on read. `query issue <ID>` is
the one exception: its `adrs` field only keeps the issue payload itself readable when the
embedded index read raises a typed `TrackerConflictError` (e.g. a version without a
matching witness). It projects that as an explicit, distinct status —
`{"status": "conflict", "tracker": <tracker name>, "reason": <fixed conflict message>}` —
the adapter's conflict messages are static literals (e.g. "Linear ADR version witness is
missing"), never interpolated or sanitized provider text —
never an empty list and never the `{"status": "unavailable", ...}` capability shape used
when a tracker has no ADR knowledge base at all. Transport, binding, and other provider
errors from the embedded index still propagate out of `query issue` unchanged.

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
accepts a finite manifest of 1–100 historical snapshots. Existing complete-batch
callers keep passing all three relation fields explicitly. A partial-source manifest
instead adds `missing_relations` to every record as a canonical sorted tuple drawn only
from `issues`, `superseded_by`, and `supersedes`; complete and partial record shapes
cannot be mixed in one batch. Every family named there must carry its empty native
placeholder (`()`, `None`, or `()` respectively), while an unlisted empty field remains
known-empty; a `superseded` record may have `superseded_by=None` only when
`superseded_by` is listed. `missing_relations` belongs to the base historical record,
never to the qualification profile: `plan_adr_batch_qualification` and the import
validate it identically before any probe read or write. The normalized provenance,
including those flags, is part of the planned version bytes, the full-manifest digest
and exact slot readback, so unknown and known-empty never replay as one another. It
constructs every deterministic version-0 Document,
witness and reciprocal issue comment, validates the full hypothetical graph before
the first write, then creates the exact slots additively. Ordinary reads fail closed
through a partial batch; each version-0 migration origin persists a SHA-256 digest of
the complete normalized manifest, propagated to later versions. An exact replay can
complete matching slots regardless of record order; a subset or changed manifest
collides before effects, while unrelated conflicts are also refused. This is not a
provider-atomic transaction and is not permission to migrate the entire archive. The
PAT-23 source exposes no authoritative typed historical relation graph, so its
unavailable families remain explicit provenance only: lexical `FOUNDRY-N` mentions in
byte-exact bodies do not create Linear issue links, and an `accepted` source status is
never changed to `superseded` by inference. PAT-23 owns the authorized manifest and
audit; PAT-10 owns cutover.

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
   timestamps, compute the SHA-256 of the byte-exact body, and map only authoritative
   typed issue relations to an already qualified target-project issue; record an
   unavailable relation family in canonical `missing_relations`, never by mining body
   text;
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

PAT-22 itself ran none of these steps. They were later run for this repository by
PAT-23 (historical import and audit) and PAT-10 (binding cutover). The witness is independent as a second provider object, not an
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

The provider implementation was first proven without activating a real workspace.
FOUNDRY-159 then activated `github.com/patobiskoto/patolabs-plugins` on Linear after a
selective live migration and provider readback. The versioned manifest and credential-
free operations record are respectively
`linear-selective-migration-manifest.json` and `linear-cutover-operations.json`; they
record the exact source snapshot, migrated IDs, readback digests and cutover binding.

## Repository-scoped activation and atomic cutover

`FOUNDRY_TRACKER` remains the compatibility default for repositories without a
versioned binding. A repository that has completed a provider cutover carries
`.foundry/tracker.json`; that marker takes precedence over the host-global default for
every normal Foundry lifecycle command. Supplying a different provider explicitly from
that checkout is refused rather than becoming an archive-write escape hatch.

The marker is resolved from the Git root, including when a command starts in a nested
directory. Its v1 schema is closed and contains the canonical remote identity, provider,
project key/UUID, SHA-256 of the complete credential-free registry binding, SHA-256 of
the selective migration manifest, and a canonical configuration SHA-256 over those
fields. It contains no token or endpoint. Symlinks, files above 16 KiB, malformed JSON,
unknown fields, unsupported providers, moved origins, stale registry data and digest
divergence fail closed.

Activation is available through:

```text
foundry_cli.py registry cutover linear <KEY> <PROJECT-UUID> sha256:<MANIFEST-DIGEST>
```

The target binding must already exist in the shared registry and, for Linear, must carry
the exact canonical repository plus the complete team/state/type mapping. The command
does no provider I/O: migration and provider readback are prerequisites. It prepares the
marker, atomically archives the one old binding for this repository in the registry,
then atomically publishes the marker. A filesystem interruption can leave the checkout
temporarily unavailable, never dual-writable; replaying the exact command completes the
transition idempotently. Multiple source bindings, a different existing marker, or a
changed target binding are refused.

The complete local publication transaction is serialized by a process-shared registry
lock. Marker absence, target binding, source archival, marker replacement and exact
readback are evaluated within that lock. Two concurrent calls with different manifest
digests therefore cannot both succeed: the first exact publication wins, while the
second observes and refuses the already-active different binding. This is a local
Foundry CAS boundary; it does not claim a distributed lock against tools that bypass
Foundry and edit its state files directly.

The manifest's `evidence.source_snapshot_digest` hashes canonical compact JSON containing, in
manifest order, each issue's source ID/state/priority/estimate/type/AC count/body digest
and PR URL plus each ADR's source ID/status/body digest. Target identifiers and readback
evidence are deliberately excluded from that source snapshot.

For the live cutover, the whole-file SHA-256 supplied to `registry cutover` is the
immutable digest of the private operator input:
`sha256:e15d28556317cd664c9f2467cf9d69df3673315e9bb42535b545afa11208df77`.
It remains bound verbatim in `.foundry/tracker.json`; the private input is not published
because it contained the source tenant URL. The versioned public manifest is a redacted
derivative whose `source.issue_url_reference` is replaced by the non-addressable archive
marker. Its distinct whole-file digest is
`sha256:4589727201ae44a00d0b47c92fca38a6fcc7a5bbde7eba0e8668a07e60d721dc`.
`linear-cutover-operations.json` records both roles, both digests, the redacted field and
the derivation edge; a generic `manifest_digest` is intentionally not used for both.

The private `claude-plugins` binding remains resolvable for reads. The archived
`patolabs-plugins` alias is also a project-wide mutation tombstone for its matching
YouTrack key and native project ID. In the current public Foundry implementation,
YouTrack writes keep their historical resolution (checkout basename or `PROJECT_REPO`
alias, case preserved; an unregistered checkout resolves no binding). Before any issue or
ADR lifecycle effect, the write tier refuses only when that binding is archived or
addresses a native project another alias archived. Consequently, queries through a
historical alias still read terminal history and ADRs, while a lifecycle write through
any alias of that same YouTrack project fails closed. The public checkout independently
rejects an explicit YouTrack override through its Linear marker.

### ADR authority after the historical import

The selective manifest of 2026-09-22 predates the Linear ADR adapter: its `adrs` entries
(`decision: archive-reference`) and `adr_reference_reason` record that, at cutover time,
decisions were referenced in the YouTrack archive. That manifest is the immutable
cutover input bound by the marker and is not rewritten. Its ADR clause is superseded by
accepted PAT-ADR-0001 and the audited PAT-23 import: the Linear project PAT now holds
the 27 historical Foundry ADRs as witness-bound Documents, beside the native PAT ADRs.
For this repository, Linear is the only ADR reference; YouTrack is solely the read-only
origin archive, and Foundry has no Git-catalogue or YouTrack fallback for ADR reads.
`linear-cutover-operations.json` records this under `adr_authority` (batch digest,
audit digest, qualification project, count and superseded clause) without publishing
any private source body or receipt.

This is an operational guarantee of the current versioned Foundry paths, not a claim
that the YouTrack server revoked write credentials. Direct REST calls, bespoke adapter
calls that bypass the write tier, or arbitrary execution of obsolete code from the
historical repository are outside this guarantee. The historical GitHub repository is
separately private and archived; no server-side YouTrack mutation was performed or is
attested by this cutover record.

Rollback is permitted only before the first post-cutover Linear lifecycle write and
requires a separately reviewed recovery that restores one provider while keeping the
other unavailable. After any such write, recovery is forward-only: preserve both audit
histories and repair Linear. For this repository that window is closed: Linear lifecycle
writes and the PAT-23 ADR import happened after the cutover, so YouTrack can never become
its active tracker again; an uncertain Linear state suspends the workflow instead. Removing the marker or re-enabling YouTrack while Linear is
writable is not a rollback; it is forbidden dual-write.

For the live PAT-10 cutover, this boundary was documented in an append-only local
session receipt at `2026-09-22T10:22:43.917Z`, before the cutover at
`2026-09-22T14:10:47Z`. The credential-free operations log records the receipt digest;
the private receipt itself is deliberately excluded from the public repository.
The historical ADR import is complete under PAT-23: its audit and gaps are recorded on
PAT-23 in Linear, and its private receipts stay outside this repository. Public `main`
branch protection is documented in `public-repository-security.md` (PAT-12).
