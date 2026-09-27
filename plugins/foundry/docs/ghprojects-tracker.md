# GitHub Projects tracker — PAT-57 reads, PAT-66 core writes

PAT-57 supports only an explicitly bound **private personal** Project V2 and
its linked canonical repository.  The binding contains `owner`, Project
`number`, Project node ID and `canonical_repo`. `verify_project_identity` uses
the qualified `user(login:)` shape, rejects GraphQL partial errors, requires the
exact private project and exhausts at most ten repository pages to find the
canonical repository. The matching repository must expose a distinct native
node ID, be private, and have the same personal `User` owner as the Project
binding. Organization-owned repositories, public repositories/projects and
inferred-owner variants are not qualified.

`search(project)` reads bounded GraphQL Project item pages and requires one
unique ID and qualified data type for each field: `Foundry normalized state`,
`Foundry type`, `Foundry priority`, and `Foundry estimate`. Field and
single-select option IDs must be non-empty and unique, and each qualified value
must carry the expected field ID/name and option ID/name pair. Missing or
malformed field, label, field-value, or `pageInfo` connections fail closed. The
active checkout binding and every
caller-supplied key, Project ID, owner, number and canonical repository are
compared before transport. Each Issue then proves distinct Project item, Issue
node and private repository node IDs; repeated item, content, normalized Issue
or cursor coordinates fail closed. The declared state and type catalogs must
exactly match the seven Foundry states and the four types `Bug`, `Feature`,
`Task`, and `Epic`. The qualified priority catalog is a non-empty subset of
`P0` through `P3` (the sandbox exposes `P1` and `P2`); priority and
estimate may be absent. A draft, PR, foreign issue, deleted field, duplicate
field or partial response fails closed. A Project field, Issue-label, or item
field-value connection that reports a second page is refused rather than read
as a silently truncated authority. The reserved `foundry:adr` label excludes
that ADR support item only from delivery reads.

`get_issue(GHQUAL-<number>)` scopes the number to the active binding, then
reads the exact Issue body, timestamps, comments, parent, paged sub-issues and
both dependency directions through bounded REST pages. Every raw Issue target
must prove its bound repository URL, API/HTML URL, native ID, number and
non-PR type before its number becomes a Foundry ID; a same-number foreign
target is therefore an error, never an alias. Child→parent is represented as
`subtask-of`/`inward`; parent→child is `parent-of`/`outward`. Raw UTF-8
body/comment values are retained and ISO timestamps become epoch milliseconds.
REST Issue labels must be a complete list of distinct, non-empty label names;
missing or malformed labels are refused before ADR discrimination. Timestamps
must include an explicit timezone offset; dates and local times without an
offset are refused instead of inheriting the host timezone.
Only complete per-line Markdown checkboxes in the current REST Issue body count
toward observed AC progress; comments and split-line fragments do not, and these
markers are not lifecycle acceptance proof.
An issue proven absent from the complete bound Project read, or whose exact
Issue endpoint then returns 404, raises the portable `IssueUnavailableError`;
`query issue` can therefore preserve an unavailable related target as a link
plus an explicit error. This classification is target-specific: global auth or
permission failures, malformed payloads, foreign relation URIs, transport and
pagination failures stay explicit. Only the documented parent-endpoint 404
payload `No parent issue found` means that a present issue has no parent. The
Project normalized-state field is recorded as an observation with
`projection_status=unknown`; PAT-67 owns lifecycle/projection proof.

Native free-text search is refused with `provider-native-search-query`. ADR index
reads are typed `adr_index` refusals until PAT-58 and lifecycle projection through
`set_state` remains a PAT-67 refusal.

## Bounded PAT-66 writes

`create_issue` posts one private-repository Issue, requires its distinct GraphQL
Issue node ID to attach it once to the exact bound Project, then writes only the
qualified `State`, `Type`, `Priority`, and `Estimate` fields. The complete catalog
and each requested option are checked before creation; an omitted Type is explicitly
set to `Task`, while an unqualified option is refused before the first POST. An optional parent
is proved through its exact bound REST URL before the child is made and is attached
with the parent's issue number and the child's distinct integer native issue ID.
`update_fields` and issue-only `update_body` reread their targeted snapshot just
before one narrow mutation and reread it afterwards.  Unrelated fields are never
sent in a Project-field mutation or an Issue-body PATCH.

GitHub's qualified endpoints expose no expected-version/CAS parameter. These are
therefore bounded detection, never CAS or exclusion: a third-party change between
the fresh read and the write can still be overwritten. Authentication/permission,
rate-limit, deleted-field, malformed/ambiguous response and transport errors remain
explicit. The adapter never retries a create, field write, relation or comment after
a response loss. Once a create succeeded but a later attachment, field, or relation
step fails, that known partial effect is reported for operator recovery rather than
creating another Issue.

`link` proves both endpoints belong to the same canonical repository before it
snapshots/rechecks relations and performs one `sub_issues` (including reparent) or
`dependencies/blocked_by` POST. GitHub requires the integer native Issue ID in these
payloads, which is deliberately kept distinct from the Foundry issue key, Issue
number, Project item ID, Project ID, field ID and select-option ID. `relates` uses the
qualified symmetric `addRelatesTo` GraphQL mutation over the two distinct Issue node
IDs, then reads the complete `relatesTo` connection from the Project item; a truncated,
foreign, duplicate or malformed relation fails closed. `add_comment` is one non-authoritative free-text POST; it validates a normal
response against the exact bound Issue URL and requires an exact comment-ID/body
readback. It cannot safely replay a lost response without duplicating it. REST integer
IDs and booleans retain their JSON types; UTF-8 text remains literal.
