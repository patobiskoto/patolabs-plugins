# Tracker contract v1 (PAT-53)

This is the versioned, portable functional contract Foundry V1.0.0 holds every tracker
adapter to. A repository chooses exactly one tracker among YouTrack, Linear and GitHub
Projects (`ghprojects`) through a versioned `.foundry/tracker.json`, and runs
the same journeys in Claude Code and Codex. GitHub as a *code host* (PRs, merges, CI) is
a separate dimension (§3). `FOUNDRY_TRACKER` remains only a host setting used by
explicit setup and historical tooling; it never substitutes for a repository binding.

Every claim below is grounded in the current adapter code, cited by file and line. The
machine-readable capability matrix is [`tracker-contract.v1.json`](tracker-contract.v1.json)
(`contract: "foundry.tracker-contract.v1"`, `version: 2`);
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
  deliberately does not offer it and never blocks V1; `gap` is not allowed. A non-core
  `to_qualify` is temporary: before V1 exit it must resolve to `supported` or to
  `refused` through a typed refusal, never a bare `NotImplementedError`.
- Every core `gap` and `to_qualify` cell, and every non-core `to_qualify` cell, names its
  owner ticket (`ticket`). A cell whose
  closure changes a durable invariant also carries `blocked_by_adr` (§4.3).

`test_tracker_contract.py` enforces the first two rules and the owner tickets, and checks the `blocked_by_adr` cells against the invariants listed in §4.3; only a few cells are tied to adapter code by the test, the rest are grounded by their cited evidence.

| Core journey | Representative `Tracker` ops | YouTrack | Linear | ghprojects |
|---|---|---|---|---|
| Contexte/backlog: identity and project resolution | `resolve_project`, `resolve_checkout_project`, `validate_mutation_*` | supported | supported | supported (binding only; workflow qualification remains PAT-65) |
| Contexte/backlog: repository bootstrap (bind an existing verified project) | `verify_project_identity`, `resolve_checkout_project` | supported | supported | supported (binding only) |
| Contexte/backlog: read | `search`, `get_issue` | supported | supported (unknown mapping fails closed; `registry update` resumes) | `to_qualify` PAT-57 |
| Frame/intake/groom: create, comment | `create_issue`, `add_comment` | supported | supported | `to_qualify` PAT-66 |
| Frame/intake/groom: evolve an existing issue | `update_fields`, `update_body` | **gap** PAT-55 (blind field writes, §4) | **gap** PAT-55, blocked by ADR | `to_qualify` PAT-66 |
| Epics/enfants/dépendances: child creation, relations | `create_issue(parent=…)`, `link(depends-on\|blocks\|relates)` | supported | supported | `to_qualify` PAT-66 |
| Epics/enfants/dépendances: reparent an existing issue | `link(subtask-of\|parent-of)` | **gap** PAT-55 (blind command) | **gap** PAT-55, blocked by ADR | `to_qualify` PAT-66 |
| Lecture/création/évolution ADR | `list_adrs`, `create_adr`, `set_adr_status` | supported | supported for native ADRs; **gap** PAT-47 for successors of imported historical ADRs | `to_qualify` PAT-58 |
| Start/resume/review/merge | `set_state` | **gap** PAT-56 (blind, not replay-safe) | supported (`in-progress`/`review`/`done`) | `to_qualify` PAT-67 |
| État et AC | `sync_acceptance_body` | supported (level 1, §4) | **gap** PAT-56, blocked by ADR | `to_qualify` PAT-67 |
| Clôture d'epic | `close_epic`, `get_epic_closure` | **gap** PAT-69, blocked by ADR | **gap** PAT-69, blocked by ADR | `to_qualify` PAT-69 (after PAT-65) |
| Périmètre de release/changelog | `search` via `query.py changelog()` | supported | **gap** PAT-59 | `to_qualify` PAT-59 |
| Bascule par copie fidèle (PAT-64): ADR import target | `import_adr`, `import_adr_batch` | **gap** PAT-64 | supported (PAT-23 ADR import) | `to_qualify` PAT-64 |
| Bascule par copie fidèle (PAT-64): live-work copy | `create_issue`, `link`, `add_comment`, `set_state` | **gap** PAT-64 | **gap** PAT-64 | `to_qualify` PAT-64 |
| Bascule (PAT-64): archived source refuses writes | `validate_mutation_project` | **gap** PAT-43 | supported | `to_qualify` PAT-64 |

The JSON is authoritative for every cell and its evidence; read it before relying on a
cell.

**Remaining gaps found in the current adapters:**
- **PAT-55** — Grooming an existing issue. YouTrack `update_fields` and `link` are
  unconditional POSTs (`youtrack.py:345-364`, level −1). Linear refuses: `update_fields`
  (`linear.py:2792-2807`), `update_body` for an issue (`linear.py:3193-3205`), `link`
  with `subtask-of`/`parent-of` (`linear.py:3107-3111`), and `set_state` for any state
  other than `in-progress`/`review`/`done` (`linear.py:2818-2821`), all under the module
  invariant `linear.py:8-11`.
- **PAT-56** — States and AC. YouTrack `set_state` is `update_fields` under another name
  (`youtrack.py:353-356`): blind and not replay-safe. Linear `sync_acceptance_body`
  refuses unconditionally (`linear.py:3207-3219`); Linear already projects AC
  completeness through a proof-bound append-only marker (`project_acceptance_proof`,
  `linear.py:2847-2899`; `Issue.ac_done` derives from it, `linear.py:2548-2554`).
- **PAT-69** — Neither YouTrack nor Linear implements `close_epic`/`get_epic_closure`;
  `write.close_epic` refuses before any provider call (`write.py:377`). Only the non-V1
  DevHub adapter implements the port (`devhub.py:220-223`, `825`).
- **PAT-64** — Only the ADR half of a switch has adapter code, and only with Linear as
  target (`import_adr`/`import_adr_batch`, PAT-ADR-0001..0003). YouTrack cannot be an ADR
  import target. No adapter copies live work faithfully: Linear's `create_issue` sends a
  random client id (`linear.py:2762`), so a replay duplicates the issue, and nothing
  copies lifecycle receipts, acceptance proofs or comments. This repository's
  YouTrack→Linear live-work move was a private operator-side selective migration, and
  `registry cutover` performs no provider I/O (`linear-tracker.md`). PAT-64 must provide
  both halves for every pair.
- **PAT-43** — YouTrack's only tombstone check, `validate_legacy_mutation`
  (`youtrack.py:263-283`, via `registry.require_writable_project`, `registry.py:1392-1413`),
  refuses a write only when the checkout's own registry binding is archived or shares
  its project with an archived alias; it must refuse every write addressed to an
  archived project by provider identifier.
- **PAT-47** — On Linear, an imported historical ADR whose qualified rendering differs
  from the local model cannot receive a successor version (accept, supersede, link,
  edit).
- **PAT-59** — Linear release scope reads the same milestone mapping: an issue in an
  unmapped `projectMilestone` fails the read (`linear.py:2541-2542`). The PAT-54
  `registry update` path can now publish the missing mapping coherently; PAT-59 still
  owns the release-scope semantics and live provider qualification.

**Explicit refusals (non-core):** free native-text `search(query=…)` on Linear
(`linear.py:2589-2592`, `provider-native-search-query`); full administrative
provisioning on Linear and `ghprojects`; `get_epic_subgraph` on YouTrack and Linear
(optional projection used only by DevHub); `supersede_adr`/`link_adr_issue` on YouTrack
(ADR evolution is served by `set_adr_status`); the typed acceptance-override receipt on
YouTrack (free-text audit note fallback, `base.py:213-228`).

**`to_qualify` (`ghprojects`).** PAT-54 supplies the canonical checkout binding and
read-only existing-project probe. The ten workflow methods `search`, `get_issue`,
`create_issue`, `update_fields`, `set_state`, `link`,
`add_comment`, `list_adrs`, `create_adr` and `set_adr_status`, each raising
`NotImplementedError` (`ghprojects.py:22-27`, `110-138`). Everything else is inherited
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
(`registry.py:219-273`) normalizes HTTPS, URL-style SSH, scp-style SSH and ssh-config
alias remotes into a credential-free, lower-cased `host/owner/repo`, without DNS
resolution. `registry.checkout_repository_identity()` (`registry.py:276-285`) applies it
to the checkout's actual `origin`, never to an environment alias. A
`.foundry/tracker.json` marker (`registry.py:73`) binds a repository to one tracker
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
(`registry.py:397-665`). The default `Tracker.resolve_checkout_project()` consumes that
selection for YouTrack and `ghprojects` (`base.py:142-172`); Linear keeps its exact
canonical registry resolution and provider-level team/project assertions
(`linear.py:1587-1617`, `2353-2363`). `foundry.effective_tracker_name()` is the shared
selection used by both the implicit factory and doctor (`foundry/__init__.py:15-43`,
`doctor.py:214-230`), so a repository V1 binding wins even when the host setting names
the DevHub pilot. Before using a host-selected DevHub pilot, the factory and doctor
validate the local selection: an existing legacy binding wins, and ambiguous,
interrupted or archived bindings refuse. The canonical DevHub pilot is represented
explicitly as `mode=pilot`, including historical DevHub markers, outside the three
V1 trackers; `--require-v1` refuses it.
A valid marker cannot mask another active canonical or matching legacy provider
binding: the published binding reader and mapping update refuse that ambiguity
before effects (`registry.py:354-369`, `486-522`, `1118-1199`). Archived bindings
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

**Normalized issue key.** `Issue.id` (`models.py:22-23`) is the provider's
human-readable identifier (YouTrack `idReadable`, Linear `identifier`), of the form
`<PREFIX>-<NUMBER>`. It is unique within one provider instance but it is not a
repository identity: historical registry entries use the repository name, homonyms use
an internal deterministic key, and several aliases may point at one provider project
(`register_alias`, `registry.py:1488-1547`;
`require_writable_project`, `registry.py:1392-1413`, treats them as one project).
The key is therefore interpreted only together with the resolved binding. Linear
enforces the provider project on each hydrated issue (`_assert_issue_project`); YouTrack
gets the repository project before each V1 mutation, while its provider-level
issue/project assertion remains part of later adapter hardening. For
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
- **Level 2 — append-only versions at deterministic ids.** Foundry never overwrites a Linear ADR
  Document (a human edit in Linear is detected by the witness, not prevented): each version is created at `_adr_document_id(project_id, adr_id,
  sequence)` (`linear.py:344-345`) by `_create_exact_adr_document`
  (`linear.py:3635-3672`), which reads the slot, creates it with that client id, and
  verifies the readback byte-exactly, failing closed if the slot holds other content.
  `_append_adr_versions` (`linear.py:3843-3862`) refuses when the chain head moved before
  the append and verifies after it. Call sites: `create_adr` calls
  `_create_adr_document` directly (`linear.py:3960`); `set_adr_status`
  (`linear.py:4857-4858`), `supersede_adr` (`linear.py:4892-4904`), `link_adr_issue`
  (`linear.py:3966-4009`) and `_update_adr_body` (`linear.py:5106-5107`) append. The
  witness carries the SHA-256 of the canonical UTF-8 body (PAT-ADR-0002). Two writers of
  the same slot with different content are detected by the exact readback, relying on
  the provider refusing a second create with an existing id.
- **Level 2.5 — append-only lifecycle markers.** Linear `set_state` and
  `project_acceptance_proof` append a comment whose id derives from the SHA-256 of
  `(schema, operation, issue[, generation])` (`_lifecycle_marker`,
  `linear.py:1687-1725`); `_project_lifecycle` (`linear.py:2172-2340`) reads, appends
  once, and reads back. An identical replay converges on the existing comment
  (`linear.py:2251-2262`). A generation enters the slot only for `state-review`,
  `acceptance` and `acceptance-override`; for `state-in-progress`, `state-done` and
  `cockpit-evidence` a different payload maps to the same id and is refused
  (`linear.py:2263-2265`, `2283-2290`), so each issue records one start and one done
  marker. A different writer appending between Foundry's read and append is detected by
  the readback (`linear.py:2337-2338`), not excluded.
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
| Resume | S5 on every replayable write (not met for `set_state` — PAT-56); free-text notes carry no state, a duplicate after an ambiguous replay is tolerated, never silently retried | met for lifecycle markers; `add_comment` (`linear.py:3158-3191`) follows the free-text rule |
| Epic closure | Fresh read of the full parent/children graph and AC proofs, one write, append-only receipt at a deterministic id bound to the exact set of terminal children and carrying the FOUNDRY-ADR-0017 human verdict, readback, fail closed on any divergence — PAT-69, blocked by ADR | same — PAT-69, blocked by ADR |

Creation of new records (issue, relation, comment, ADR) is outside S5: YouTrack and
Linear creations are not replay-idempotent today (provider-assigned or random ids, e.g.
`linear.py:2762`); V1 requires S2 and S4 for them. GitHub Projects' targets are set by
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
map; GitHub Projects reads the exact owner/number/node id and the linked canonical
repository. A foreign coordinate, unavailable provider or incomplete binding refuses
before the registry or marker changes (`registry.py:1586-1731`, `956-1062`;
`youtrack.py:247-258`; `linear.py:1573-1585`; `ghprojects.py:36-104`). The GitHub query follows GitHub's documented
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
performs the same provider readback, adds `canonical_repo`, and publishes a marker. It
does not alter another alias or any archive tombstone. New bindings use marker schema 2
with `activation.kind=bootstrap`; upgraded bindings use `upgrade`. Neither carries a
migration-manifest digest. Existing schema-1 migration markers remain readable;
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
replay (`registry.py:397-483`, `1118-1199`). Archive tombstones and unrelated bindings
are preserved.

The non-core `full-administrative-provisioning` row records YouTrack's existing
provisioning as an optional capability and Linear/`ghprojects` as refused. Setup refuses
an occupied provider/basename slot before constructing the provider, including a foreign
canonical homonym, and rechecks absence under the publication lock. Use the existing-project
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
