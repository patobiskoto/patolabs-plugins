# GitHub Projects tracker — PAT-57 reads

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
`Foundry type`, `Foundry priority`, and `Foundry estimate`. The single-select
option IDs/names must also be unique, and each value must carry the expected
field ID/name and option ID/name pair. The active checkout binding and every
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
Only complete per-line Markdown checkboxes in the current REST Issue body count
toward observed AC progress; comments and split-line fragments do not, and these
markers are not lifecycle acceptance proof.
Only the documented 404 payload `No parent issue found` means no parent; all
other 404s, auth, permission, rate-limit, malformed and pagination failures
stay explicit. The Project normalized-state field is recorded as an observation
with `projection_status=unknown`; PAT-67 owns lifecycle/projection proof.

Native free-text search is refused with
`provider-native-search-query`. ADR index reads are typed `adr_index` refusals
until PAT-58; all writes remain typed capability refusals owned by PAT-66/PAT-67.
