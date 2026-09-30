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
Project normalized-state field is recorded as an observation. PAT-67 adds a
separate Foundry-owned lifecycle channel: a state transition writes a canonical,
hash-bound `foundry-ghprojects-lifecycle.v1` receipt to the exact Issue comment
history, then projects only the target ProjectV2 State. Every receipt binds the
canonical repository, Project id/number/key, Issue key/number/database id/node id and
Project item id. Review and done additionally bind the exact canonical PR URL,
head/base SHA and review digest; done also binds the merge SHA. A native `done` without
that chain is refused as an unauthorised auto-close.

Before the single comment POST, the adapter persists a local pending intent under the
digest of the fully bound receipt. A lost response is reconciled only when the complete
paged history exposes exactly one matching receipt. If a fresh process still observes
zero candidates, the intent remains pending and the operation fails closed without a
second POST; multiple candidates also fail closed. The local intent never grants a
lifecycle state by itself. Exact historical replays converge without regressing newer
review or done evidence.

Acceptance receipts accept only the canonical six-field PAT-56 proof shape and bind one
exact review generation. The criterion identities are recomputed from the current issue
body, so changing checkbox progress preserves the immutable criterion semantics while
editing criterion text invalidates the old proof. `checked` must equal the complete
criterion count; blocked, partial, foreign or malformed proofs never mark AC complete.
Native-state disagreement is observable through `normalized_state`, `native_state` and
`projection_status`; it does not become acceptance authority.

Native free-text search is refused with `provider-native-search-query`. ADR index
reads are typed `adr_index` refusals until PAT-58. The lifecycle transport is implemented
but remains `to_qualify` until the authorized live PR/CI recipe proves it on an exact SHA.

## Bounded PAT-66 writes

`create_issue` posts one private-repository Issue, requires its distinct GraphQL
Issue node ID to attach it once to the exact bound Project, then writes only the
qualified `State`, `Type`, `Priority`, and `Estimate` fields. The complete catalog
and each requested option are checked before creation; an omitted Type is explicitly
set to `Task`, while an unqualified option is refused before the first POST. `Estimate`
accepts an exact signed base-10 integer: booleans, fractional floats and non-finite
floats are refused before an effect, while a negative integer is not given an extra
provider-specific restriction. An optional parent must be the unique complete,
non-ADR delivery item in this exact Project as well as an Issue at the exact bound REST
URL before the child is made. A canonical-repository Issue outside the Project and a
`foundry:adr` support are both refused before the first write. The parent is attached
with its Issue number and the child's distinct integer native Issue ID.
`update_fields` first returns without mutation when every requested field is already
at its target. Otherwise, each field has its own fresh read, one narrow mutation and
immediate authoritative readback before the next field may start. The readback also
compares every other property exposed by the normalized Issue read. A lost mutation
response converges only when that read proves the target and preserves those unrelated
properties; an observed unchanged target keeps the original error, and any other
divergence is a conflict. A partially completed multi-field call therefore leaves only
independently observed effects. A later explicit invocation freshly observes them and
skips targets already applied; there is no automatic rewrite of an ambiguous effect and
no durable field-write intent across invocations. Issue-only `update_body` also rereads after an ambiguous PATCH response, converges
only on the exact requested body with other observable properties preserved, and
skips an already applied body on explicit replay. The native update timestamp and
body-derived checkbox counters are allowed to follow the PATCH. Unrelated fields are never sent in a Project-field
mutation or an Issue-body PATCH.

GitHub's qualified endpoints expose no expected-version/CAS parameter. These are
therefore bounded detection, never CAS or exclusion: a third-party change in the
residual S1→S2 window can still be overwritten and hidden by S3. Lifecycle state writes
compare every observed untargeted property around the narrow Project State mutation,
but that comparison does not close the window. Authentication/permission, rate-limit,
deleted-field, malformed/ambiguous response and transport errors remain explicit.

Issue creation uses a private local intent/observation journal under Foundry's data
directory. Its fingerprint covers the exact binding, title, body, portable fields and
parent; it stores no body or title. The intent is atomically durable before the REST
Issue POST and is serialized by a local `flock`, shared by Claude Code, Codex and
worktrees on the same machine. Every invocation still revalidates GitHub: without an
existing intent only zero exact native candidates permits the initial POST; a pending
intent plus zero candidates is an unknown effect and refuses; one exact canonical,
non-ADR candidate resumes; multiple candidates, or one candidate without a matching
local intent, fail closed. A lost successful Issue response therefore exposes the
known Foundry/native Issue IDs and a later invocation resumes that Issue without a
second Issue POST. Because the public create port has no caller-supplied operation ID,
two intentional creates with the same exact binding and spec on this machine are
indistinguishable from a replay and converge on the same retained intent.

Project attachment, each requested field, and the parent relation are observed
separately. An already-applied target converges without a write; a conflicting present
value refuses; an absent target permits at most one recorded resume attempt. Errors
after the Issue identity is known carry its Foundry ID and integer native Issue ID.
The private recovery scan can observe delivery rows whose create-time Type is
still missing, including another incomplete row; only the exact known candidate
can authorize its creation steps, and a parent must be complete. Ordinary PAT-57
reads retain their strict complete-item contract.

This journal is recovery state, not a provider receipt or authority. It grants no
effect without fresh provider validation, does not coordinate another machine, and
does not make GitHub exactly-once. A third-party identical creation between the
initial zero-candidate read and the Issue POST remains the named S1-to-S2 race; a later
multiple-candidate observation refuses but cannot undo that duplicate. A corrupt or
unavailable journal refuses before the Issue POST. Field writes, relations and comments
retain their own bounded observation rules; free-text comments still cannot safely
replay a lost response without duplicating the note.

`link` proves both endpoints belong to the same canonical repository before it
snapshots/rechecks relations and performs one `sub_issues` (including reparent) or
`dependencies/blocked_by` POST. GitHub requires the integer native Issue ID in these
payloads, which is deliberately kept distinct from the Foundry issue key, Issue
number, Project item ID, Project ID, field ID and select-option ID. `relates` uses the
qualified symmetric `addRelatesTo` GraphQL mutation over the two distinct Issue node
IDs, then reads the complete `relatesTo` connection from the Project item; a truncated,
foreign, duplicate or malformed relation fails closed. Both REST and GraphQL link
mutations reread the two endpoints even after an ambiguous response, without a
second mutation. Both the requested link and its reciprocal endpoint projection
must be observed, including before an already-applied replay can return without
writing. A one-sided relation fails closed and is never automatically repaired.
The endpoints' unrelated
properties preserved; an unchanged result keeps the original transport error and
a divergent result is an explicit conflict. An unavailable readback remains an
unknown effect, without an automatic retry. `add_comment` first proves one
unique, complete, non-ADR delivery item in the active Project as well as the exact bound
REST Issue. A canonical-repository Issue outside that Project and a `foundry:adr`
support are refused before the POST. The one non-authoritative free-text POST validates
a normal response against the exact bound Issue URL and requires an exact
comment-ID/body readback. It cannot safely replay a lost response without duplicating
the note. REST integer IDs and booleans retain their JSON types; UTF-8 text remains
literal.

Lors de `create_issue(parent=...)`, le parent est requalifié dans le Project lié
après les écritures de champs, immédiatement avant l'unique attachement natif.
La reprise partielle et la reprise d'une création terminée vérifient les deux
projections : le parent unique de l'enfant et l'enfant dans les sous-issues du
parent. Une projection asymétrique ferme la reprise sans réparation automatique.
Après un attachement, même si sa réponse est perdue, les deux ressources sont
relues et leurs propriétés et relations non visées doivent être préservées.
Cette détection reste bornée : le risque S1→S2 demeure, sans CAS ni exclusion des
écritures concurrentes.
La création d'une issue, même sans parent, relit également l'identité privée du
Project personnel et son dépôt canonique privé lié avant le journal d'intention
et le premier POST. Un dépôt public, détaché, étranger ou une identité indisponible
refuse la création avant tout effet; un binding local ancien ne vaut pas cette
requalification live.
