# Tracker contract v1 (PAT-53)

This is the versioned, portable functional contract Foundry V1.0.0 holds every tracker
adapter to. A repository chooses exactly one tracker among YouTrack, Linear and GitHub
Projects (`ghprojects`) through a versioned `.foundry/tracker.json`, and runs
the same journeys in Claude Code and Codex. GitHub as a *code host* (PRs, merges, CI) is
a separate dimension (§3). `FOUNDRY_TRACKER` remains only a host setting used by
explicit setup and historical tooling; it never substitutes for a repository binding.

Every claim below is grounded in the current adapter code, cited by module and symbol. The
machine-readable capability matrix is [`tracker-contract.v1.json`](tracker-contract.v1.json)
(`contract: "foundry.tracker-contract.v1"`, `version: 2`);
[`test_tracker_contract.py`](../tests/test_tracker_contract.py) pins its schema and
cross-checks it against the `Tracker` ABC and the adapter modules. This contract records
capabilities and requirements; it changes no adapter behaviour.

The host entrypoints are identical: Claude Code invokes `/foundry:groom` or
`/foundry:intake`, while Codex invokes `$foundry:groom` or `$foundry:intake`. Both skills
apply confirmed changes through `foundry_cli.py edit set-field`, `edit body`,
`edit create-issue`, and the shared `write.py`/`Tracker` ports; neither host calls a
provider API directly.

The normalized `set-field` syntax is identical on both hosts. `Estimate` takes a base-10
integer. `Labels` takes one comma-separated argument such as `"api,backend"`; surrounding
space and empty segments are ignored, and `""` means an empty label list. The CLI converts
that argument to the portable list before calling the shared write port. Linear turns the
list into label-ID deltas, DevHub sends it as its native JSON list, and YouTrack joins it
to the historical comma-separated custom-field string (`[]` becomes `""`) before POST.

**Authority.** The exit criteria (1-4) come from the PAT-51 epic's acceptance criteria.
Where a rule restates an accepted decision it cites the ADR (FOUNDRY-ADR-0002,
FOUNDRY-ADR-0013, FOUNDRY-ADR-0017, FOUNDRY-ADR-0026, PAT-ADR-0001..0003,
PAT-ADR-0006) and does not re-decide it. Accepted durable-write invariants are recorded
in §4.3.

## 1. Core journeys and the capability matrix

Every operation in the JSON carries `core: true|false` and one status per provider from a
closed vocabulary:

- On a **core** operation only `supported` clears V1 exit. `gap` (known-missing or
  below the §4.2 target) and `to_qualify` (real provider behaviour not probed) both
  **block** V1 exit for that provider. `refused` never appears on a core operation.
- On a **non-core** operation `refused` is the expected state for a provider that
  deliberately does not offer it and never blocks V1; `gap` is not allowed. A non-core
  `to_qualify` is temporary: before V1 exit it must resolve to `supported` or to
  `refused` through a typed refusal, never a bare `NotImplementedError`.
- Every core `gap` and `to_qualify` cell, and every non-core `to_qualify` cell, names its
  owner ticket (`ticket`). A cell carries `blocked_by_adr` only while an unaccepted ADR
  prevents its implementation (§4.3).

`test_tracker_contract.py` enforces the first two rules and the owner tickets, and checks the `blocked_by_adr` cells against the invariants listed in §4.3; only a few cells are tied to adapter code by the test, the rest are grounded by their cited evidence.

| Core journey | Representative `Tracker` ops | YouTrack | Linear | ghprojects |
|---|---|---|---|---|
| Contexte/backlog: identity and project resolution | `resolve_project`, `resolve_checkout_project`, `validate_mutation_*` | supported | supported | supported (binding only; workflow qualification remains PAT-65) |
| Contexte/backlog: repository bootstrap (bind an existing verified project) | `verify_project_identity`, `resolve_checkout_project` | supported | supported | supported (binding only) |
| Contexte/backlog: read | `search`, `get_issue` | supported | supported (unknown mapping fails closed; `registry update` resumes) | `to_qualify` PAT-57 |
| Frame/intake/groom: create, comment | `create_issue`, `add_comment` | supported | supported | `to_qualify` PAT-66 |
| Frame/intake/groom: evolve an existing issue | `update_fields`, `update_body` | supported (bounded detection, §4) | supported (bounded detection, §4) | `to_qualify` PAT-66 |
| Epics/enfants/dépendances: child creation, relations | `create_issue(parent=…)`, `link(depends-on\|blocks\|relates)` | supported | supported | `to_qualify` PAT-66 |
| Epics/enfants/dépendances: reparent an existing issue | `link(subtask-of\|parent-of)` | supported (bounded detection, §4) | supported (bounded detection, §4) | `to_qualify` PAT-66 |
| Lecture/création/évolution ADR | `list_adrs`, `create_adr`, `set_adr_status` | supported | supported for native ADRs and successors of batch-qualified historical ADRs (PAT-47) | `to_qualify` PAT-58 |
| Start/resume/review/merge | `set_state` | supported (bounded predecessor projection, §4) | supported (`in-progress`/`review`/`done`, native State plus receipt) | `to_qualify` PAT-67 |
| État et AC | `sync_acceptance_body` | supported (level 1, §4) | supported (append-only proof projection, §4) | `to_qualify` PAT-67 |
| Clôture d'epic | `close_epic`, `get_epic_closure` | **gap** PAT-69, implementation authorized by PAT-ADR-0006 | **gap** PAT-69, implementation authorized by PAT-ADR-0006 | `to_qualify` PAT-69 (after PAT-65) |
| Périmètre de release/changelog | `search` via `query.py changelog()` | supported | **gap** PAT-59 | `to_qualify` PAT-59 |
| Bascule par copie fidèle (PAT-64): ADR import target | `import_adr`, `import_adr_batch` | **gap** PAT-64 | supported (PAT-23 ADR import) | `to_qualify` PAT-64 |
| Bascule par copie fidèle (PAT-64): live-work copy | `create_issue`, `link`, `add_comment`, `set_state` | **gap** PAT-64 | **gap** PAT-64 | `to_qualify` PAT-64 |
| Bascule (PAT-64): archived source refuses writes | mutation ports plus provider target preflight | supported | supported | `to_qualify` PAT-64 |

The JSON is authoritative for every cell and its evidence; read it before relying on a
cell.

**Remaining gaps found in the current adapters:**
- **PAT-55** — Grooming an existing issue is supported on YouTrack and Linear under
  PAT-ADR-0006: each targeted field/body/parent operation captures an expected snapshot,
  re-reads it immediately before its one write, and verifies readback. This is bounded
  detection only: an external S1→S2 write can still be overwritten and is never called
  CAS, exclusion, or a distributed lock. Linear label/type changes use provider deltas,
  preserving unrelated labels where the API permits it. The portable YouTrack field
  vocabulary is `State`, `Priority`, `Type`, `Milestone`, `Estimate`, `Labels` and
  `GitHub PR`; clearing one with JSON `null` is a typed refusal because the existing
  payload path cannot prove a clear. A marker-bound V1 call also refuses an arbitrary
  native custom field rather than reporting a false snapshot/readback. The historical
  direct-adapter custom-field escape hatch remains available without that bounded
  guarantee and is outside the core contract's deliberately non-universal editor.
  GitHub Projects remains PAT-66.
- **PAT-56** — States and AC. YouTrack receives the fresh predecessor state from the
  shared `write.transition` tier, re-reads it before one targeted native projection, and
  converges only when an exact replay finds the target already present; a third state
  fails closed. This is bounded detection, not CAS. Linear keeps native checkbox
  replacement refused, while `write.sync_acceptance` uses its proof-bound append-only
  projection (`project_acceptance_proof`, `linear.py`) as the V1 AC authority; native
  checkboxes and external state automation never count as positive acceptance evidence.
- **PAT-69** — Neither YouTrack nor Linear implements `close_epic`/`get_epic_closure`;
  `write.close_epic` refuses before any provider call (`write.py`). Only the non-V1
  DevHub adapter implements the port (`devhub.py`).
- **PAT-64** — Only the ADR half of a switch has adapter code, and only with Linear as
  target (`import_adr`/`import_adr_batch`, PAT-ADR-0001..0003). YouTrack cannot be an ADR
  import target. No adapter copies live work faithfully: Linear's `create_issue` sends a
  random client id (`linear.py`), so a replay duplicates the issue, and nothing
  copies lifecycle receipts, acceptance proofs or comments. This repository's
  YouTrack→Linear live-work move was a private operator-side selective migration, and
  `registry cutover` performs no provider I/O (`linear-tracker.md`). PAT-64 must provide
  both halves for every pair.
- **PAT-59** — Linear release scope reads the same milestone mapping: an issue in an
  unmapped `projectMilestone` fails the read (`linear.py`). The PAT-54
  `registry update` path can now publish the missing mapping coherently; PAT-59 still
  owns the release-scope semantics and live provider qualification.

**Closed by PAT-43 — YouTrack archive tombstone.** Every targeted issue or ADR mutation
reads `project(id,shortName)` from the provider before its first effect and refuses when
either exact native id or exact project key matches an archived YouTrack binding
(`youtrack.py`). Explicit issue/ADR creation checks the supplied native project
before milestone setup or creation; child creation also proves its existing parent first
(`youtrack.py`). Field/state writes, both sides of a link, comments,
body writes and ADR status changes all pass through that preflight
(`youtrack.py`). Matching is exact, never prefix-based;
active cross-project links and parents keep each native coordinate independent, and an
unregistered checkout can still write to an active project. For a repository with a V1
marker, the factory enables the shared mutation binding and `validate_issue_binding`
checks every issue endpoint against that canonical native project before the write tier
calls the mutation. `edit create-issue` also uses that write tier: it validates an existing
parent against the canonical project before the adapter can prepare a Milestone or POST
the child. The YouTrack adapter repeats that comparison when its
`requires_mutation_binding` flag is active, so a direct bound call has the same refusal.
Direct unbound adapter calls and marker-free legacy resolution retain the historical
cross-project capability. This provider-bound guard complements the
checkout-bound legacy check (`youtrack.py`); it does not claim a shared
`validate_mutation_project` override or change the no-CAS guarantees in §4. A
Milestone field update preserves `ms_bundle` only from an explicit or historical
project whose exact native key and id corroborate that preflight target. A foreign
mapping is not consumed, and contradictory matching mappings refuse before either POST
before the Milestone enum or issue POST (`youtrack.py`).

**Explicit refusals (non-core):** free native-text `search(query=…)` on Linear
(`linear.py`, `provider-native-search-query`); full administrative
provisioning on Linear and `ghprojects`; `get_epic_subgraph` on YouTrack and Linear
(optional projection used only by DevHub); `supersede_adr`/`link_adr_issue` on YouTrack
(ADR evolution is served by `set_adr_status`); the typed acceptance-override receipt on
YouTrack (free-text audit note fallback, `base.py`).

**`to_qualify` (`ghprojects`).** PAT-54 supplies the canonical checkout binding and
read-only existing-project probe. The ten workflow methods `search`, `get_issue`,
`create_issue`, `update_fields`, `set_state`, `link`,
`add_comment`, `list_adrs`, `create_adr` and `set_adr_status`, each raising
`NotImplementedError` (`ghprojects.py`). Everything else is inherited
from `base.py` unchanged: optional ports raise their typed unavailability error
(`EpicClosureUnavailableError`, `BodyUpdateUnavailableError`,
`AcceptanceSyncUnavailableError`, `ProjectProvisioningUnavailableError`,
`EpicSubgraphUnavailableError`, `TrackerCapabilityUnavailableError`); the
`validate_*` checks and `preflight_issue_operation` are no-ops. PAT-65 qualifies GitHub
Projects v2 and GitHub-hosted ADR storage on authorized test resources; each cell's
`ticket` names the tranche that implements it (PAT-57, 58, 59, 64, 66, 67) or PAT-65
where only qualification is known.

## 2. Identity model

**Canonical repository identity.** `registry.canonical_repository_identity()`
(`registry.py`) normalizes HTTPS, URL-style SSH, scp-style SSH and ssh-config
alias remotes into a credential-free, lower-cased `host/owner/repo`, without DNS
resolution. `registry.checkout_repository_identity()` (`registry.py`) applies it
to the checkout's actual `origin`, never to an environment alias. A
`.foundry/tracker.json` marker (`registry.py`) binds a repository to one tracker
binding under that identity.

The registry keeps the historical basename key when it is available. If another
repository on the same provider already owns that basename, bootstrap stores the new
binding under an internal deterministic key composed of the basename and the SHA-256 of
the canonical identity. This storage key is never selection authority: marker reads,
factory/doctor resolution and updates match `canonical_repo`. A basename lookup remains
only the compatibility fallback for a legacy entry without `canonical_repo`, so
`first/same` and `second/same` can both be active without sharing a binding.

**One checkout selection for all three adapters.**
`repository_tracker_selection()` reads the marker from the Git root and validates its
canonical origin, provider, project coordinates and registry digest
(`registry.py`). The default `Tracker.resolve_checkout_project()` consumes that
selection for YouTrack and `ghprojects` (`base.py`); Linear delegates to that
same selection before activation (`linear.py`) and keeps provider-level
team/project assertions (`linear.py`). `foundry.effective_tracker_name()` is the shared
selection used by both the implicit factory and doctor (`foundry/__init__.py`,
`doctor.py`), so a repository V1 binding wins even when the host setting names
the DevHub pilot. Before using a host-selected DevHub pilot, the factory and doctor
validate the local selection: an existing legacy binding wins, and ambiguous,
interrupted or archived bindings refuse. The canonical DevHub pilot is represented
explicitly as `mode=pilot`, including historical DevHub markers, outside the three
V1 trackers; `--require-v1` refuses it.
A valid marker cannot mask another active canonical or matching legacy provider
binding: the published binding reader and mapping update refuse that ambiguity
before effects (`registry.py`). Archived bindings
are excluded from the active-provider check. Only a proven unbound checkout or a
host diagnostic outside Git can use the global
pilot setting. A V1 canonical registry entry whose marker is absent is an interrupted
or moved publication and fails before provider access. `PROJECT_REPO` and
`FOUNDRY_TRACKER` cannot change that result. The administrative setup boundary alone
passes the configured provider explicitly, but only after
`repository_tracker_selection(allow_unbound=True)` has proved that the checkout has no
marker, canonical binding, legacy basename binding or matching tombstone. An existing
legacy binding or an interrupted V1 publication is refused before provider selection,
credentials, provisioning or registry writes. A genuinely new repository retains the
historical explicit provisioning flow; normal implicit factory calls stay closed.

**Legacy is explicit and bounded.** With no marker and no canonical V1 entry, Foundry
accepts legacy mode only when the actual remote basename has exactly one historical
active registry entry and that entry has no `canonical_repo`. `registry selection`
reports `mode=legacy`; `registry selection --require-v1` refuses it and points to
`registry upgrade`. An unregistered repository, two legacy providers for one basename,
or a canonical entry without its marker is never treated as a new legacy repository.
DevHub remains the separately identified internal pilot outside the three-provider V1
matrix.

**Normalized issue key.** `Issue.id` (`models.py`) is the provider's
human-readable identifier (YouTrack `idReadable`, Linear `identifier`), of the form
`<PREFIX>-<NUMBER>`. It is unique within one provider instance but it is not a
repository identity: historical registry entries use the repository name, homonyms use
an internal deterministic key, and several aliases may point at one provider project
(`register_alias`, `registry.py`;
`require_writable_project`, `registry.py`, treats them as one project).
The readable key is therefore never project authority on its own. Linear enforces the
provider project on each hydrated issue (`_assert_issue_project`); before each targeted
YouTrack mutation, the adapter reads the issue or article's native project id/key and
checks both exact coordinates against the archived bindings (`youtrack.py`).
A V1 marker also activates `requires_mutation_binding`; the common write tier resolves
the canonical repository project and `validate_issue_binding` rejects any issue or link
endpoint whose native id/key differs. Marker-free legacy calls keep their native
cross-project behavior.
Explicit creation checks the supplied native project. For
`ghprojects`, a GitHub issue number is per repository and one Project v2 can hold issues
from several repositories, so its normalized key must carry the issue's repository
identity (for example `host/owner/repo#12`); PAT-65 qualifies the wire format, this
contract fixes the requirement.

**Provider identifiers stay inside the adapter**: YouTrack's internal entity id, Linear's
GraphQL node ids (validated by the generic `_SAFE_ID` pattern, `linear.py`), and — once
qualified — GitHub's `(repository, issue number, Project item node id)`. The pipeline and
skills only see the normalized `Issue`/`Adr`/`Project` models (`models.py`).

## 3. Tracker / CodeHost / Ship-iOS boundaries

- **Tracker** (`trackers/base.py`) owns issues, ADRs and their lifecycle state.
- **CodeHost** (`codehosts/base.py`, `codehosts/github.py`) owns PRs, branches, CI and
  merges, over REST only; it is a separate provider choice (`FOUNDRY_CODEHOST`), and
  GitHub is its only adapter whatever the tracker.
- **Ship-iOS never talks to a Tracker adapter.** `plugins/ship-ios/scripts/changelog_bridge.py`
  first shells out to `registry selection` (optionally `--require-v1`), then to
  `foundry_cli.py query changelog <MILESTONE>` from the same checkout and consumes only that JSON;
  it imports no tracker module and holds no provider credential, and exits 3 when
  Foundry is absent so the caller falls back to a changelog file.

## 4. Mutation guarantees without a provider transaction/CAS

Neither YouTrack nor Linear, as used by Foundry, offers a provider-side compare-and-swap
on an issue or ADR write; GitHub Projects v2 is unqualified (PAT-65) and nothing is
assumed. A read before the write plus a readback **detects** some concurrent writes; it
never **excludes** them, and no text in Foundry may say otherwise.

### 4.1 Levels implemented today

- **Level 0 — refused.** Linear native acceptance-checkbox replacement and atomic Epic
  closure on YouTrack/Linear remain typed refusals. The append-only Linear acceptance
  projection is the V1 authority selected by PAT-ADR-0006.
- **Level 1 — bounded read-verify-write-readback, no retry.** YouTrack portable field, body and
  relation mutations and Linear field/body/parent replacement capture only the targeted
  expected properties, read again before one write, and compare readback. YouTrack
  Milestone enum creation is a distinct existing capability: the issue snapshot is
  checked before that first possible effect and rechecked before the issue POST. A local
  body `flock` serializes only Foundry processes on one machine. None of these mechanisms
  excludes an external writer in S1→S2.
- **Level 2 — append-only versions at deterministic ids.** Foundry never overwrites a Linear ADR
  Document (a human edit in Linear is detected by the witness, not prevented): each version is created at `_adr_document_id(project_id, adr_id,
  sequence)` (`linear.py`) by `_create_exact_adr_document`
  (`linear.py`), which reads the slot, creates it with that client id, and
  verifies the readback byte-exactly, failing closed if the slot holds other content.
  `_append_adr_versions` (`linear.py`) refuses when the chain head moved before
  the append and verifies after it. Call sites: `create_adr` calls
  `_create_adr_document` directly (`linear.py`); `set_adr_status`
  (`linear.py`), `supersede_adr` (`linear.py`), `link_adr_issue`
  (`linear.py`) and `_update_adr_body` (`linear.py`) append. The
  witness carries the SHA-256 of the canonical UTF-8 body (PAT-ADR-0002). Two writers of
  the same slot with different content are detected by the exact readback, relying on
  the provider refusing a second create with an existing id.
- **Level 2.5 — append-only lifecycle markers plus native State.** Linear `set_state`
  first appends its deterministic receipt, then projects only the target native State
  through the S1-S5 bounded `issueUpdate` path; an exact, valid surviving receipt can
  repair only its missing native State effect. `project_acceptance_proof` appends a comment
  whose id derives from the SHA-256 of
  `(schema, operation, issue[, generation])` (`_lifecycle_marker`,
  `linear.py`); `_project_lifecycle` (`linear.py`) reads, appends
  once, and reads back. An identical replay converges on the existing comment
  (`linear.py`). A generation enters the slot only for `state-review`,
  `acceptance` and `acceptance-override`; for `state-in-progress`, `state-done` and
  `cockpit-evidence` a different payload maps to the same id and is refused
  (`linear.py`), so each issue records one start and one done
  marker. A different writer appending between Foundry's read and append is detected by
  the readback (`linear.py`), not excluded.

  Query observations expose `normalized_state`, `native_state`, and
  `projection_status`. A valid receipt with a divergent native State remains
  readable as `disagreement`; an invalid or incomplete receipt chain is `unknown`
  and has no normalized state, including when the native State says `done`.
- **Level 3 — provider transaction plus receipt.** Only DevHub's `close_epic`
  (`devhub.py`), outside V1.

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
| Grooming: fields, body, parent of an existing issue | S1-S4 on every portable write; targeted fields and both parent endpoints are read again before one write | S1-S4 in-place replacement under PAT-ADR-0006; only targeted inputs are sent and labels use deltas |
| AC state | S1-S4 on the checkbox body (met, level 1) | PAT-ADR-0006 declares the proof-bound append-only projection the V1 authority (S5/S6); native checkbox replacement remains refused |
| Status projection | S1-S5: `write.transition` supplies a fresh predecessor coordinate, `set_state` re-reads it before one native State write, and only a retry from the target converges; another state fails closed | `in-progress`/`review`/`done`: one targeted native State projection under S1-S5 plus its append-only receipt; disagreement is observable and a bare native terminal state is never positive proof |
| Resume | S5 on every replayable transition through the predecessor coordinate; free-text notes carry no state, a duplicate after an ambiguous replay is tolerated, never silently retried | exact native/receipt recovery completes only the missing effect; `add_comment` (`linear.py`) follows the free-text rule |
| Epic closure | Fresh read of the full parent/children graph and AC proofs, one write, append-only receipt at a deterministic id bound to the exact set of terminal children and carrying the FOUNDRY-ADR-0017 human verdict, readback, fail closed on any divergence — authorized by PAT-ADR-0006, implementation PAT-69 | same — authorized by PAT-ADR-0006, implementation PAT-69 |

Issue creation and free-text comments are outside S5: YouTrack and Linear issue creates
use provider-assigned or fresh client ids, and ambiguous free-text comment replay can
duplicate a non-authoritative note. Linear issue relations now use a deterministic UUID
derived from their canonical project/type/endpoints. Linear's qualified schema defines
the supplied UUIDv4 as the native relation identifier and `issueRelation(id)` as a lookup
by its unique identifier. Success and ambiguous recovery verify that exact ID, type and
both endpoints. A replay with endpoint projections still absent may send another create
request only at that same identity; an existing slot converges by exact lookup, while an
unavailable, missing, permission-denied, malformed or mismatched lookup fails closed.
This proves S5 for the relation port without claiming general idempotence or CAS. GitHub
Projects' targets are set by PAT-65: absent a qualified provider precondition, this
floor applies.

### 4.3 Accepted durable-invariant decision

PAT-ADR-0006, **Garanties d'écriture sans CAS pour les trackers V1**, is accepted. It
explicitly amends Linear's former blanket refusal for PAT-55 grooming fields, body and
parent under S1-S4 and the named S1→S2 risk. It keeps Linear native checkbox replacement
refused and selects the existing append-only proof projection as V1 AC authority for
PAT-56. It also authorizes PAT-69's bounded Epic-closure shape while leaving that
implementation outside PAT-55. The machine-readable contract therefore has no pending
ADR entry or `blocked_by_adr` cell; remaining `gap` cells are implementation work owned
by their tickets. A future stronger or weaker guarantee still requires another accepted
ADR before code.

## 5. Optional / excluded scope and the open GraphQL/REST point

**Excluded from V1 core** (criterion 4): full administrative provisioning (creating a
provider organization/team/board, or instance-global custom fields, bundles, workflow
and permissions); free native-text search; general inter-tracker migration beyond the
PAT-64 switch; code hosts other than GitHub.

**In scope, and distinct from the above: repository bootstrap** (core row
`repository-bootstrap`, PAT-54) binds an existing provider project after a readback; it
never creates an organization, team, project, board, field, workflow or permission.

```text
foundry_cli.py registry bootstrap <tracker> <repo> <KEY> <project-id> [k=v …]
foundry_cli.py registry upgrade <tracker> <legacy-repo>
foundry_cli.py registry selection [--require-v1]
foundry_cli.py registry update <tracker> <repo> <KEY> <project-id> \
  <expected-configuration-sha256> [complete k=v …]
```

`bootstrap` derives the canonical identity from the checkout rather than from an
argument. Before the local publication, YouTrack reads the exact native project id/key;
Linear reads the exact project id, team UUID and team key after validating every UUID
map; YouTrack V1 extras are limited to `canonical_repo` and optional non-empty
`ms_bundle`. Undeclared keys, including credential keys, refuse before provider readback
or persistence. GitHub Projects reads the exact owner/number/node id and the linked canonical
repository. A foreign coordinate, unavailable provider or incomplete binding refuses
before the registry or marker changes (`registry.py`;
`youtrack.py`; `linear.py`; `ghprojects.py`). The GitHub query follows GitHub's documented
organization/user `projectV2(number:)` lookup and `ProjectV2.repositories` connection;
it is capped at ten 100-repository pages and needs only `read:project` permission
([GitHub Projects API guide](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects),
[ProjectV2 reference](https://docs.github.com/en/graphql/reference/projects)). This is
an offline-tested binding probe, not PAT-65's live workflow qualification.

If the provider's basename slot already belongs to a different canonical repository,
bootstrap publishes the new entry under its deterministic disambiguated storage key.
Exact replay finds that entry by `canonical_repo`; it does not overwrite the older
binding or any alias/tombstone. The storage key is registry-internal and changes neither marker schema.
Schema 1 and schema 2 have distinct wire formats but preserve the same canonical
repository-to-tracker binding semantics.

`upgrade` starts only from the checkout basename's exact active historical entry,
performs the same provider readback, retains that exact verified candidate, and compares
it with the current legacy entry under the publication lock before adding `canonical_repo`
and publishing a marker. A concurrent change is refused without publishing it as V1. It
does not alter another alias or any archive tombstone. New bindings use marker schema 2
with `activation.kind=bootstrap`; upgraded bindings use `upgrade`. Neither carries a
migration-manifest digest. Existing schema-1 migration markers remain readable.

Every adapter resolves again through the strict shared checkout selection before
activation, including Linear; a factory created before an interrupted update cannot
consume the divergent registry mappings.

`registry cutover` now emits schema 2 with `activation.kind=migration` and is the only
path that requires `activation.manifest_digest`.

`update` is the coherent recovery for reviewed field, label and milestone mapping
changes. Its input is the complete replacement binding plus the marker's expected
`configuration_digest`, not a partial cache edit. Under the same local registry lock it
compares the marker, old registry digest and complete candidate, refuses a concurrent
winner, updates every active alias for that canonical checkout together, publishes the
matching marker and reads both back. After the exact local compare succeeds, the CLI
uses only the provider identity readback port, independently of the normal factory.
This narrow read-only probe permits interrupted publication recovery; ordinary commands
continue refusing the divergent marker. Exact
replay completes an interruption between registry and marker publication. This is a
local compare-and-publish boundary only; it neither mutates provider data nor claims a
provider CAS. Marker structure, schema version, activation vocabulary and every digest
are parsed by the same strict reader before either file can be written, including during
replay (`registry.py`). Bootstrap also validates the marker
path before registry publication: dangling marker symlinks, symlinked `.foundry`
directories and non-directory parents refuse without replacing existing paths. Archive tombstones and unrelated bindings
are preserved.

The hook lookup `entry_for()` uses the same strict checkout selection. Multiple active
legacy bindings are refused instead of selecting `FOUNDRY_TRACKER`; SessionStart reports
the invalid binding and the Bash guard denies R1 commands. A foreign canonical homonym
does not inherit another repository’s Foundry context. Explicit legacy aliases outside
V1 remain usable only when their active binding is unique.

The non-core `full-administrative-provisioning` row records YouTrack's existing
provisioning as an optional capability and Linear/`ghprojects` as refused. Setup refuses
a repository argument different from the verified checkout and an occupied
basename slot across all providers before constructing the provider, including a foreign
canonical homonym. It rechecks the complete checkout selection across providers under the
publication lock, refusing a concurrently added binding or tombstone. Use the existing-project
bootstrap path for homonyms; administrative setup never replaces their bindings.

**Open workflow point for PAT-65.** GitHub Projects v2 is GraphQL-only (`ghprojects.py`
module docstring). Foundry's code-host rule is REST only (`skills/merge-pr/SKILL.md:226`:
"Never GraphQL (`gh pr create/merge/checks`). REST only — the adapter enforces it.").
A repository choosing `ghprojects` keeps the REST-only code-host adapter for PRs and
merges. Its tracker adapter uses one read-only GraphQL probe for PAT-54 binding and will
use GraphQL for tracker operations only as PAT-65 and the owner tickets qualify them.

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

This is contract **v1**, matching `tracker-contract.v1.json`'s `version: 2`. A change to
any status cell, the operation list or the closed status vocabulary bumps the JSON
`version` and this heading together.
