# Tracker contract v1 (PAT-53)

This is the versioned, portable functional contract Foundry V1.0.0 holds every tracker
adapter to. A repository chooses exactly one tracker among YouTrack, Linear and GitHub
Projects (`ghprojects`) through `.foundry/tracker.json` / `FOUNDRY_TRACKER`, and runs
the same journeys in Claude Code and Codex. GitHub as a *code host* (PRs, merges, CI) is
a separate dimension (§3).

Every claim below is grounded in the current adapter code, cited by file and line. The
machine-readable capability matrix is [`tracker-contract.v1.json`](tracker-contract.v1.json)
(`contract: "foundry.tracker-contract.v1"`, `version: 1`);
[`test_tracker_contract.py`](../tests/test_tracker_contract.py) pins its schema and
cross-checks it against the `Tracker` ABC and the adapter modules. This contract records
capabilities and requirements; it changes no adapter behaviour.

**Authority.** The exit criteria (1-4) come from the PAT-51 epic's acceptance criteria.
Where a rule restates an accepted decision it cites the ADR (FOUNDRY-ADR-0002,
FOUNDRY-ADR-0013, FOUNDRY-ADR-0017, FOUNDRY-ADR-0026, PAT-ADR-0001..0003) and does not
re-decide it. The decisions this contract cannot make itself — changes to durable
write invariants — are listed in §4.3 and gated on one consolidated ADR.

## 1. Core journeys and the capability matrix

Every operation in the JSON carries `core: true|false` and one status per provider from a
closed vocabulary:

- On a **core** operation only `supported` clears V1 exit. `gap` (known-missing or
  below the §4.2 target) and `to_qualify` (real provider behaviour not probed) both
  **block** V1 exit for that provider. `refused` never appears on a core operation.
- On a **non-core** operation `refused` is the expected state for a provider that
  deliberately does not offer it and never blocks V1; `supported` and `to_qualify` are
  allowed; `gap` is not.
- Every core `gap` and `to_qualify` cell names its owner ticket (`ticket`). A cell whose
  closure changes a durable invariant also carries `blocked_by_adr` (§4.3).

`test_tracker_contract.py` enforces these three rules.

| Core journey | Representative `Tracker` ops | YouTrack | Linear | ghprojects |
|---|---|---|---|---|
| Contexte/backlog: identity and project resolution | `resolve_project`, `resolve_checkout_project`, `validate_mutation_*` | **gap** PAT-54 (basename-keyed, §2) | supported | `to_qualify` PAT-54 |
| Contexte/backlog: repository bootstrap (create-or-recover the binding) | `provision_project`, `resolve_checkout_project` | **gap** PAT-54 (only path is full provisioning, §5) | **gap** PAT-54 (none) | `to_qualify` PAT-54 |
| Contexte/backlog: read | `search`, `get_issue` | supported | **gap** PAT-54 (one unmapped state/label/milestone fails the whole read) | `to_qualify` PAT-57 |
| Frame/intake/groom: create, comment | `create_issue`, `add_comment` | supported | supported | `to_qualify` PAT-66 |
| Frame/intake/groom: evolve an existing issue | `update_fields`, `update_body` | **gap** PAT-55 (blind field writes, §4) | **gap** PAT-55, blocked by ADR | `to_qualify` PAT-66 |
| Epics/enfants/dépendances: child creation, relations | `create_issue(parent=…)`, `link(depends-on\|blocks\|relates)` | supported | supported | `to_qualify` PAT-66 |
| Epics/enfants/dépendances: reparent an existing issue | `link(subtask-of\|parent-of)` | **gap** PAT-55 (blind command) | **gap** PAT-55, blocked by ADR | `to_qualify` PAT-66 |
| Lecture/création/évolution ADR | `list_adrs`, `create_adr`, `set_adr_status` | supported | supported for native ADRs; **gap** PAT-47 for successors of imported historical ADRs | `to_qualify` PAT-58 |
| Start/resume/review/merge | `set_state` | **gap** PAT-56 (blind, not replay-safe) | supported (`in-progress`/`review`/`done`) | `to_qualify` PAT-67 |
| État et AC | `sync_acceptance_body` | supported (level 1, §4) | **gap** PAT-56, blocked by ADR | `to_qualify` PAT-67 |
| Clôture d'epic | `close_epic`, `get_epic_closure` | **gap** PAT-69, blocked by ADR | **gap** PAT-69, blocked by ADR | `to_qualify` PAT-65 |
| Périmètre de release/changelog | `search` via `query.py changelog()` | supported | **gap** PAT-59 | `to_qualify` PAT-59 |
| Bascule par copie fidèle (PAT-64): import target | `import_adr`, `import_adr_batch` | **gap** PAT-64 | supported (YouTrack→Linear precedent) | `to_qualify` PAT-64 |
| Bascule (PAT-64): archived source refuses writes | `validate_mutation_project` | **gap** PAT-43 | supported | `to_qualify` PAT-64 |

The JSON is authoritative for every cell and its evidence; read it before relying on a
cell.

**Gaps found in the current adapters:**

- **PAT-54** — YouTrack and `ghprojects` resolve by repository basename, not canonical
  identity (§2). No provider has a minimal create-or-recover bootstrap bound to
  canonical identity (§5). Linear's backlog read fails as a whole when one issue carries
  a state, label or `projectMilestone` absent from the binding (`_to_issue`,
  `linear.py:2141-2142`, `2156-2157`, `2204-2205`, applied to every page node by
  `search()`, `linear.py:2278-2279`); adding a mapping changes the registry binding
  digest the marker pins (`registry.py:412-413`), so it needs the coherent binding
  update of PAT-44/PAT-54.
- **PAT-55** — Grooming an existing issue. YouTrack `update_fields` and `link` are
  unconditional POSTs (`youtrack.py:345-364`, level −1). Linear refuses: `update_fields`
  (`linear.py:2455-2470`), `update_body` for an issue (`linear.py:2856-2868`), `link`
  with `subtask-of`/`parent-of` (`linear.py:2770-2774`), and `set_state` for any state
  other than `in-progress`/`review`/`done` (`linear.py:2481-2484`), all under the module
  invariant `linear.py:8-11`.
- **PAT-56** — States and AC. YouTrack `set_state` is `update_fields` under another name
  (`youtrack.py:353-356`): blind and not replay-safe. Linear `sync_acceptance_body`
  refuses unconditionally (`linear.py:2870-2882`); Linear already projects AC
  completeness through a proof-bound append-only marker (`project_acceptance_proof`,
  `linear.py:2510-2562`; `Issue.ac_done` derives from it, `linear.py:2211-2217`).
- **PAT-69** — Neither YouTrack nor Linear implements `close_epic`/`get_epic_closure`;
  `write.close_epic` refuses before any provider call (`write.py:377`). Only the non-V1
  DevHub adapter implements the port (`devhub.py:220-223`, `825`).
- **PAT-64** — YouTrack has no `import_adr`/`import_adr_batch` override, so it cannot be a
  switch target. The proven leg is YouTrack→Linear (FOUNDRY-ADR-0026,
  PAT-ADR-0001..0003); PAT-64 generalizes it to every pair.
- **PAT-43** — YouTrack's only tombstone check, `validate_legacy_mutation`
  (`youtrack.py:263-283`, via `registry.require_writable_project`, `registry.py:776-797`),
  refuses a write only when the checkout's own registry binding is archived or shares
  its project with an archived alias; it must refuse every write addressed to an
  archived project by provider identifier.
- **PAT-47** — On Linear, an imported historical ADR whose qualified rendering differs
  from the local model cannot receive a successor version (accept, supersede, link,
  edit).
- **PAT-59** — Linear release scope reads the same milestone mapping: an issue in an
  unmapped `projectMilestone` fails the read (`linear.py:2204-2205`).

**Explicit refusals (non-core):** free native-text `search(query=…)` on Linear
(`linear.py:2252-2255`, `provider-native-search-query`); full administrative
provisioning on Linear and `ghprojects`; `get_epic_subgraph` on YouTrack and Linear
(optional projection used only by DevHub); `supersede_adr`/`link_adr_issue` on YouTrack
(ADR evolution is served by `set_adr_status`); the typed acceptance-override receipt on
YouTrack (free-text audit note fallback, `base.py:213-228`).

**`to_qualify` (`ghprojects`).** `ghprojects.py` overrides `resolve_project` — a
`registry.resolve()` basename lookup (`ghprojects.py:30-32`) — and the ten abstract
methods `search`, `get_issue`, `create_issue`, `update_fields`, `set_state`, `link`,
`add_comment`, `list_adrs`, `create_adr` and `set_adr_status`, each raising
`NotImplementedError` (`ghprojects.py:19-24`, `34-62`). Everything else is inherited
from `base.py` unchanged: optional ports raise their typed unavailability error
(`EpicClosureUnavailableError`, `BodyUpdateUnavailableError`,
`AcceptanceSyncUnavailableError`, `ProjectProvisioningUnavailableError`,
`EpicSubgraphUnavailableError`, `TrackerCapabilityUnavailableError`); the
`validate_*` checks and `preflight_issue_operation` are no-ops; `resolve_checkout_project`
falls back to the basename lookup (`base.py:135-152`). PAT-65 qualifies GitHub
Projects v2 and GitHub-hosted ADR storage on authorized test resources; each cell's
`ticket` names the tranche that implements it (PAT-54, 57, 58, 59, 64, 66, 67) or PAT-65
where only qualification is known.

## 2. Identity model

**Canonical repository identity.** `registry.canonical_repository_identity()`
(`registry.py:206-260`) normalizes HTTPS, URL-style SSH, scp-style SSH and ssh-config
alias remotes into a credential-free, lower-cased `host/owner/repo`, without DNS
resolution. `registry.checkout_repository_identity()` (`registry.py:263-272`) applies it
to the checkout's actual `origin`, never to an environment alias. A
`.foundry/tracker.json` marker (`registry.py:73`) binds a repository to one tracker
binding under that identity.

**Linear resolves and validates by canonical identity only.** `resolve_project` discards
its repository-name argument (`linear.py:1250-1256`); `resolve_checkout_project` and
`validate_mutation_repository` (`linear.py:1258-1280`) go through
`registry.resolve_canonical_repository()` (`registry.py:800-830`), which matches only
`canonical_repo` and fails closed on an ambiguous or missing match. Every issue Linear
reads or writes is asserted to belong to the bound team and project UUIDs
(`_assert_issue_project`, `linear.py:2016-2026`).

**YouTrack and `ghprojects` still resolve by basename.** Without a marker,
`registry.resolve()` (`registry.py:725-773`) looks the entry up by the name it is given,
which callers take from `registry.repo_basename()` (`registry.py:653-664`: the
`PROJECT_REPO` override or the remote's last path segment); `registry.entry_for()`
(`registry.py:667-705`) does the same. With a marker,
`registry._matching_registry_bindings()` (`registry.py:298-311`) prefers a
`canonical_repo` match but falls back to the basename entry. YouTrack mutations resolve
no binding at all: `write.mutation_project` returns `None` for a provider without
`requires_mutation_binding` after `validate_legacy_mutation` (`write.py:50-56`), and
YouTrack addresses issues directly by id (`youtrack.py:290-301`, `345-369`) with the
base no-op `validate_issue_binding` (`base.py:164-165`). Two repositories sharing a
basename, or a stale `PROJECT_REPO`, are therefore not distinguished by these providers;
PAT-54 closes this.

**The guarantee V1 requires** — two issues `#12` in two repositories are never
interchangeable, and no project or issue is discovered by name, basename, key or prefix
alone — holds today only for Linear.

**Normalized issue key.** `Issue.id` (`models.py:22-23`) is the provider's
human-readable identifier (YouTrack `idReadable`, Linear `identifier`), of the form
`<PREFIX>-<NUMBER>`. It is unique within one provider instance but it is not a
repository identity: registry entries are keyed by repository name and several aliases
may point at one provider project (`register_alias`, `registry.py:865-922`;
`require_writable_project`, `registry.py:776-797`, treats them as one project).
The key is therefore interpreted only together with the resolved binding: Linear
enforces this (`_assert_issue_project`); YouTrack does not yet (PAT-54). For
`ghprojects`, a GitHub issue number is per repository and one Project v2 can hold issues
from several repositories, so its normalized key must carry the issue's repository
identity (for example `host/owner/repo#12`); PAT-65 qualifies the wire format, this
contract fixes the requirement.

**Provider identifiers stay inside the adapter**: YouTrack's internal entity id, Linear's
GraphQL node ids (validated by the generic `_SAFE_ID` pattern, `linear.py:51`), and — once
qualified — GitHub's `(repository, issue number, Project item node id)`. The pipeline and
skills only see the normalized `Issue`/`Adr`/`Project` models (`models.py`).

## 3. Tracker / CodeHost / Ship-iOS boundaries

- **Tracker** (`trackers/base.py`) owns issues, ADRs and their lifecycle state.
- **CodeHost** (`codehosts/base.py`, `codehosts/github.py`) owns PRs, branches, CI and
  merges, over REST only; it is a separate provider choice (`FOUNDRY_CODEHOST`), and
  GitHub is its only adapter whatever the tracker.
- **Ship-iOS never talks to a Tracker adapter.** `plugins/ship-ios/scripts/changelog_bridge.py`
  shells out to `foundry_cli.py query changelog <MILESTONE>` and consumes only that JSON;
  it imports no tracker module and holds no provider credential, and exits 3 when
  Foundry is absent so the caller falls back to a changelog file.

## 4. Mutation guarantees without a provider transaction/CAS

Neither YouTrack nor Linear, as used by Foundry, offers a provider-side compare-and-swap
on an issue or ADR write; GitHub Projects v2 is unqualified (PAT-65) and nothing is
assumed. A read before the write plus a readback **detects** some concurrent writes; it
never **excludes** them, and no text in Foundry may say otherwise.

### 4.1 Levels implemented today

- **Level −1 — blind write.** YouTrack `update_fields`, `set_state` and `link`
  (`youtrack.py:345-364`) are unconditional POSTs: no expected-value check, no lock, no
  compared readback. A concurrent change is silently overwritten. `add_comment`
  (`youtrack.py:366-369`) is a non-idempotent POST: a replay creates a second comment.
- **Level 0 — refused.** Linear existing-issue replacement (`update_fields`, `update_body`
  for an issue, `sync_acceptance_body`, reparenting `link`, `set_state` outside
  `in-progress`/`review`/`done`; §1 PAT-55/PAT-56).
- **Level 1 — one read-verify-write-readback, no retry.** YouTrack `update_body`
  (`youtrack.py:392-448`): a local `flock` (`_body_lock`, `youtrack.py:371-390`)
  serializes Foundry's own processes, then one read refuses a divergence from
  `expected_body`, one write, one readback. A non-Foundry writer landing between the read
  and the write still wins (`youtrack.py:109-111`, `396-402`). `sync_acceptance_body`
  (`youtrack.py:450-466`) and `set_adr_status` (`youtrack.py:550-558`) use this path.
- **Level 2 — append-only versions at deterministic ids.** Linear ADR Documents are
  never overwritten: each version is created at `_adr_document_id(project_id, adr_id,
  sequence)` (`linear.py:337-338`) by `_create_exact_adr_document`
  (`linear.py:3298-3335`), which reads the slot, creates it with that client id, and
  verifies the readback byte-exactly, failing closed if the slot holds other content.
  `_append_adr_versions` (`linear.py:3506-3525`) refuses when the chain head moved before
  the append and verifies after it. Call sites: `create_adr` calls
  `_create_adr_document` directly (`linear.py:3623`); `set_adr_status`
  (`linear.py:4520-4521`), `supersede_adr` (`linear.py:4555-4567`), `link_adr_issue`
  (`linear.py:3629-3672`) and `_update_adr_body` (`linear.py:4769-4770`) append. The
  witness carries the SHA-256 of the canonical UTF-8 body (PAT-ADR-0002). Two writers of
  the same slot with different content are detected by the exact readback, relying on
  the provider refusing a second create with an existing id.
- **Level 2.5 — append-only lifecycle markers.** Linear `set_state` and
  `project_acceptance_proof` append a comment whose id derives from the SHA-256 of
  `(schema, operation, issue[, generation])` (`_lifecycle_marker`,
  `linear.py:1349-1388`); `_project_lifecycle` (`linear.py:1835-2003`) reads, appends
  once, and reads back. An identical replay converges on the existing comment
  (`linear.py:1914-1925`). A generation enters the slot only for `state-review`,
  `acceptance` and `acceptance-override`; for `state-in-progress`, `state-done` and
  `cockpit-evidence` a different payload maps to the same id and is refused
  (`linear.py:1926-1928`, `1946-1953`), so each issue records one start and one done
  marker. A different writer appending between Foundry's read and append is detected by
  the readback (`linear.py:2000-2001`), not excluded.
- **Level 3 — provider transaction plus receipt.** Only DevHub's `close_epic`
  (`devhub.py:825`), outside V1.

### 4.2 V1 target guarantees without CAS

Every mutation of an existing record on a provider without CAS must meet this floor:

- **S1** fresh read of the expected snapshot (the values being changed, or the whole
  body) immediately before the write; refuse before any effect on divergence;
- **S2** exactly one write, no automatic or silent retry;
- **S3** readback verification after the write;
- **S4** fail closed — typed conflict error, no success reported — on any divergence or
  on an ambiguous response the readback cannot resolve;
- **S5** replay safety: a write that RESUME may repeat after an ambiguous response
  converges on the already-applied result (observed by reading it, or through a
  deterministic id derived from its canonical coordinates) or fails closed; never a
  duplicate, a double transition or an overwrite;
- **S6** prefer an append-only record at a deterministic id over in-place replacement
  wherever the provider allows it.

**Residual risk, stated as such:** an external write landing between S1 and S2 is
overwritten and S3 does not see it. The floor detects divergence before S1 and after S2;
it never excludes the S1→S2 window, and it is labelled "bounded detection", never
"exclusion", "lock" or "CAS". A local lock only serializes Foundry processes on one
machine.

| Journey family | YouTrack (V1 minimum) | Linear (V1 minimum) |
|---|---|---|
| Grooming: fields, body, parent of an existing issue | S1-S4 on every write (body: met, level 1; fields and parent: not met, level −1 — PAT-55) | S1-S4 in-place replacement, which reverses `linear.py:8-11` — PAT-55, blocked by ADR (§4.3) |
| AC state | S1-S4 on the checkbox body (met, level 1) | Either the existing proof-bound append-only projection is declared the V1 AC authority (S5/S6 met, no in-place write, invariant kept), or in-place checkbox sync under S1-S4 (reverses `linear.py:8-11`); the ADR chooses — PAT-56, blocked by ADR |
| Status projection | S1-S5 with the expected predecessor state re-read before `set_state` (not met — PAT-56) | `in-progress`/`review`/`done`: met (level 2.5); other states belong to grooming (PAT-55) |
| Resume | S5 on every replayable write (not met for `set_state` — PAT-56); free-text notes carry no state, a duplicate after an ambiguous replay is tolerated, never silently retried | met for lifecycle markers; `add_comment` (`linear.py:2821-2854`) follows the free-text rule |
| Epic closure | Fresh read of the full parent/children graph and AC proofs, one write, append-only receipt at a deterministic id bound to the exact set of terminal children and carrying the FOUNDRY-ADR-0017 human verdict, readback, fail closed on any divergence — PAT-69, blocked by ADR | same — PAT-69, blocked by ADR |

Creation of new records (issue, relation, comment, ADR) is outside S5: YouTrack and
Linear creations are not replay-idempotent today (provider-assigned or random ids, e.g.
`linear.py:2425`); V1 requires S2 and S4 for them. GitHub Projects' targets are set by
PAT-65: absent a qualified provider precondition, this floor applies.

### 4.3 Durable invariants and the pending ADR

Any change to a durable invariant — including one that makes an existing guarantee
observably weaker, stronger or differently shaped — requires a new, explicitly accepted
ADR before the code (criterion 3). Three V1 gaps change one:

- **PAT-55 (Linear)** and **PAT-56 (Linear)** — in-place replacement of an existing
  Linear issue reverses the module invariant "Linear does not expose a compare-and-swap
  precondition for existing-issue replacement. Those writes are therefore unavailable: a
  local lock or a readback cannot prevent an external writer from being overwritten."
  (`linear.py:8-11`).
- **PAT-69 (YouTrack, Linear)** — the port requires closure to "lock the parent graph …
  then persist both `done` and a replayable audit receipt in one transaction"
  (`base.py:262-276`); `write.close_epic` calls it "an atomic provider graph operation"
  (`write.py:371-376`). FOUNDRY-ADR-0017 requires the timestamped human-verdict receipt
  bound to the exact set of terminal children; it does not decide atomicity.

All three are **blocked until one consolidated ADR, "Garanties d'écriture sans CAS pour
les trackers V1" — ADR à créer et accepter (proposée dans le cadre de PAT-53) — is
explicitly accepted**; their cells carry `blocked_by_adr`. The YouTrack legs of PAT-55
and PAT-56 raise blind writes to the §4.2 floor without reversing a stated invariant and
are not gated, unless they change the authority of YouTrack's native State field, which
falls under the same ADR.

## 5. Optional / excluded scope and the open GraphQL/REST point

**Excluded from V1 core** (criterion 4): full administrative provisioning (creating a
provider organization/team/board, or instance-global custom fields, bundles, workflow
and permissions); free native-text search; general inter-tracker migration beyond the
PAT-64 switch; code hosts other than GitHub.

**In scope, and distinct from the above: repository bootstrap** (core row
`repository-bootstrap`, PAT-54) — create or recover the repository's own project and
register a binding keyed by canonical identity, never discovering a project by name or
key alone. Today:

- YouTrack's only path is `setup_project.setup` (`setup_project.py:94-122`) →
  `provision_project` (`youtrack.py:115-120`) → `provision_youtrack_project`
  (`youtrack_provisioning.py:90-182`). That is full administrative provisioning: it
  recovers an existing project by `shortName` alone (`youtrack_provisioning.py:55-61`),
  adds values to instance-global State/Priority/Type bundles
  (`youtrack_provisioning.py:29-52`, `104-120`) and creates global custom fields. It
  does not forward `canonical_repository` (`youtrack.py:115-120`), and `setup` registers
  the binding under the operator-supplied repository name (`setup_project.py:118`).
- Linear has no `provision_project`; `setup` refuses (`setup_project.py:96-101`) and the
  binding is registered by hand (`docs/linear-tracker.md`, "Required binding").
- `ghprojects` inherits the base refusal (`base.py:114-128`).

The non-core `full-administrative-provisioning` row records YouTrack's existing
provisioning as an optional capability and Linear/`ghprojects` as refused.

**Open point for PAT-65.** GitHub Projects v2 is GraphQL-only (`ghprojects.py` module
docstring). Foundry's code-host rule is REST only (`skills/merge-pr/SKILL.md:226`:
"Never GraphQL (`gh pr create/merge/checks`). REST only — the adapter enforces it.").
A repository choosing `ghprojects` keeps the REST-only code-host adapter for PRs and
merges; only its tracker adapter would need GraphQL. PAT-65 resolves this tension; this
contract does not.

## 6. Conformance test plan

- **PAT-68 — deterministic conformance suite (fake transport)** for all three providers:
  core operations, capability flags, explicit refusals, pagination, permission errors,
  conflicts, ambiguous responses and resumption, against doubles only. A missing core
  capability fails the suite for that adapter; it is never skipped.
  [`test_tracker_contract.py`](../tests/test_tracker_contract.py) is PAT-53's narrower
  pin (schema, ABC membership, owners, code-tied flags), not PAT-68's suite.
- **PAT-61 — real recipe, three trackers × two hosts**: authorized test resources proving
  each journey against YouTrack, Linear and GitHub Projects in Claude Code and Codex.
  PAT-68's doubles are never presented as this recipe.
- **PAT-65 — GitHub Projects v2 and ADR-storage qualification** on authorized test
  resources, feeding PAT-57/58/66/67.

## Document status

This is contract **v1**, matching `tracker-contract.v1.json`'s `version: 1`. A change to
any status cell, the operation list or the closed status vocabulary bumps the JSON
`version` and this heading together.
