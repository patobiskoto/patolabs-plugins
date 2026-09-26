# Tracker contract v1 (PAT-53)

This is the versioned, portable functional contract Foundry V1.0.0 holds every tracker
adapter to: YouTrack, Linear and GitHub Projects (`ghprojects`) are the three trackers a
repository can choose, one at a time, per `.foundry/tracker.json` /
`FOUNDRY_TRACKER`. GitHub as a *code host* (PRs, merges, CI) is a separate dimension —
see [Tracker / CodeHost / Ship-iOS boundaries](#tracker--codehost--ship-ios-boundaries)
below — and stays REST-only regardless of which tracker is active.

This document is a **contract, not a feature list**: every claim below is grounded in
the current adapter code, cited by file/function. The companion machine-readable
capability matrix is
[`tracker-contract.v1.json`](tracker-contract.v1.json) (`contract:
"foundry.tracker-contract.v1"`, `version: 1`); [`test_tracker_contract.py`](../tests/test_tracker_contract.py)
pins its schema and cross-checks it against the live `Tracker` ABC and adapter modules.
This is PAT-53, a framing ticket: it records the contract and the matrix, it does not
implement new adapter capability.

**What authorizes this contract's normative rules.** The V1 exit criteria this document
implements (criteria 1-4 cited throughout) come from the PAT-51 epic's acceptance
criteria; where a rule here restates or grounds an already-accepted architectural
decision, it cites that ADR by ID (FOUNDRY-ADR-0002, FOUNDRY-ADR-0017, FOUNDRY-ADR-0026,
PAT-ADR-0001..0003) and does not re-decide it. The one place this document identifies a
decision it cannot make itself — the bounded no-CAS guarantee PAT-69 would need on
YouTrack/Linear — is §4's level-3 paragraph; that one requires a new ADR, explicitly
accepted, before any code lands, per criterion 3 and the broadened durable-invariant
clause at the end of §4.

## 1. Core journeys and the capability matrix

Criterion: every core journey below must reach `supported` on all three trackers before
V1 ships. Every operation the matrix records now carries an explicit `core: true|false`
flag (`tracker-contract.v1.json` `operations[].core`) and the closed status vocabulary is
asymmetric by design:

- On a **core** operation (`core: true`), only `supported` clears V1 exit for a
  provider. `gap` and `to_qualify` both **block** V1 exit for that provider — they name
  a different reason (known-missing implementation vs. unqualified transport), never a
  different severity. `refused` **never** appears on a core operation: an adapter that
  does not support a core capability is recording a gap (or an unqualified cell), not a
  deliberate optional exclusion. `test_tracker_contract.py` pins this: no core cell may
  be `refused`, and every `gap`/`to_qualify` core cell traces to a ticket or an explicit
  qualification owner.
- On a **non-core** operation (`core: false` — an optional capability or one criterion 4
  explicitly excludes from V1), `refused` is the expected steady state for a provider
  that deliberately does not offer it, and it never blocks V1. A non-core operation can
  still be `supported` (an adapter may offer more than V1 requires) or `to_qualify`
  (GitHub Projects v2's real behaviour is simply not probed yet); it should not carry
  `gap`, since nothing optional is a "must close before V1" item by definition.

This closes a review finding against the previous wording, which let a core cell sit at
`refused`/`to_qualify` without blocking V1 exit — that reading would have let, say, a
"refused" cell on a core journey pass silently. It cannot: the vocabulary and the test
below make `refused` structurally impossible on any operation this contract marks core.

| Core journey | Representative `Tracker` ABC ops | YouTrack | Linear | ghprojects |
|---|---|---|---|---|
| Contexte/backlog: identity/project resolution | `resolve_project`, `resolve_checkout_project`, `validate_mutation_*` | **gap** (basename/`PROJECT_REPO`-keyed resolution, not canonical identity — PAT-54) | supported (canonical-identity-only) | `to_qualify` (PAT-65/PAT-54) |
| Contexte/backlog: read | `search`, `get_issue` | supported | supported | `to_qualify` (PAT-65) |
| Frame/intake/groom | `create_issue`, `update_fields`, `update_body`, `add_comment` | supported | **gap** (existing-issue field/body edits, PAT-55) | `to_qualify` (PAT-65/PAT-66) |
| Epics/enfants/dépendances | `create_issue(parent=…)`, `link` | supported | **gap** (reparenting an existing issue, PAT-55); creation and relates/depends-on/blocks are supported | `to_qualify` (PAT-65) |
| Lecture/création/évolution ADR | `list_adrs`, `create_adr`, `set_adr_status` | supported | supported for native ADRs; **gap** for successor versions of imported historical ADRs (PAT-47) | `to_qualify` (PAT-65/PAT-58, storage choice deferred) |
| Start/resume/review/merge | `set_state` | supported | supported | `to_qualify` (PAT-65/PAT-67) |
| État et AC | `sync_acceptance_body` | supported (bounded, not CAS — see §4) | **gap** (in-place checkbox sync, PAT-56); proof-bound AC completeness projection already `supported` (§4 level 2.5) | `to_qualify` (PAT-65) |
| Clôture d'epic | `close_epic`, `get_epic_closure` | **gap** (PAT-69) | **gap** (PAT-69) | `to_qualify` (PAT-65) |
| Périmètre de release/changelog | `search`/`get_issue` via `query.py changelog()` | supported | **gap** (milestones must be mapped in the binding; PAT-59 after PAT-44/PAT-54) | `to_qualify` (PAT-65) |
| Bascule de dépôt par copie fidèle (PAT-64) | `import_adr`, `import_adr_batch`, `validate_mutation_project` | **gap** as import target (PAT-64); tombstone-as-source generalization (PAT-43) | supported as import target (the proven YouTrack→Linear precedent); PAT-64 generalizes it to every pair | `to_qualify` (PAT-65) |

The full matrix — every operation, every cell, and the code citation behind each status —
is [`tracker-contract.v1.json`](tracker-contract.v1.json). A one-line summary here is not
a substitute for it; read the JSON before relying on a specific cell.

**Gap tickets found by reading the current adapters** (see the JSON `evidence` field for
each):

- **PAT-54** — YouTrack's and `ghprojects`' project resolution
  (`registry.resolve()`, `registry.py:755-773`; `registry.entry_for()`,
  `registry.py:699-705`; `registry._matching_registry_bindings()`,
  `registry.py:698-712`, which falls back to basename even on the marker-lookup path)
  key on `registry.repo_basename()` (`registry.py:653-664`) — the Git remote basename,
  or an operator-set `PROJECT_REPO` override — not on
  `registry.canonical_repository_identity()`. Base `Tracker.validate_mutation_repository`
  /`validate_mutation_project` (`base.py:170-184`) are no-ops neither adapter overrides.
  Two repositories that share a basename (a common fork/rename pattern, or a stale
  `PROJECT_REPO`) are not distinguished today; this is a real gap against criterion 2's
  identity guarantee, closed per-provider by PAT-54. Linear is the only provider that
  already resolves and validates by canonical identity alone (§2).
- **PAT-55** — Linear cannot groom an already-created issue: `update_fields` (existing
  issue) and `update_body` (Issue, not Adr) both raise unconditionally
  (`linear.py:2455-2470`, `:2856-2868`), and `link()` refuses `subtask-of`/`parent-of` on
  an existing issue (`linear.py:2770-2774`, `existing-issue-parent-replacement`).
- **PAT-56** — Linear's *in-place issue-body* acceptance-criteria sync is unimplemented:
  `sync_acceptance_body` (`linear.py:2870-2882`) raises `AcceptanceSyncUnavailableError`
  unconditionally — the same existing-issue-body-replacement gap PAT-55 already names,
  not a missing analogue of the ADR body path (Linear's ADR body path is append-only
  versioning, not an in-place overwrite; see §4 level 2). Separately, and already
  `supported` today: Linear projects acceptance-completeness through a proof-bound,
  append-only lifecycle marker instead of an in-place checkbox
  (`acceptance_proof_projection_supported = True`, `linear.py:1117`;
  `project_acceptance_proof`, `linear.py:2510-2562`; `Issue.ac_done` derives from that
  projection in `_to_issue`, `linear.py:2212-2217`, never from the issue's native
  checkbox text). PAT-56 is exactly "sync the native checkbox text in place", nothing
  broader.
- **PAT-69** — Neither YouTrack nor Linear implement `close_epic`/`get_epic_closure`;
  both keep `epic_closure_supported = False` (or unset, which defaults to `False`) and
  `write.close_epic` (`write.py:377`) refuses before any provider call. Only the non-V1
  DevHub adapter proves the port (`devhub.py:220-223,825`). Closing this gap on
  YouTrack/Linear would change the durable invariant `write.py:371`'s docstring names
  ("atomic provider graph operation") to a weaker, explicitly-bounded guarantee — see §4,
  "PAT-69 is blocked pending a new ADR", for why that needs its own accepted decision
  before any PAT-69 code lands.
- **PAT-64** — YouTrack has no `import_adr`/`import_adr_batch` override, so it cannot be
  a *target* of a tracker switch yet; the only proven leg is the YouTrack→Linear
  precedent this repository already lived (FOUNDRY-ADR-0026, PAT-ADR-0001..0003, `registry
  cutover linear`). PAT-64 generalizes that one proven, one-directional leg into a
  source/target-agnostic capability across all three trackers.
- **PAT-43** — YouTrack's *only* existing tombstone check, `validate_legacy_mutation`
  (`youtrack.py:263-283`, using `registry.require_writable_project`,
  `registry.py:776-797`), refuses a write only when the checkout's own historical
  registry binding (basename/`PROJECT_REPO`) is archived. It needs to generalize to
  writes that target an archived project purely by provider identifier — not only
  through that one registry-binding path — so a PAT-64 switch source stays refused for
  every write shape once it becomes an archive.
- **PAT-47** — On Linear, an imported historical ADR whose qualified rendering differs
  from the local model cannot receive any successor version (accept, supersede, link,
  edit): its predecessor is re-read with the strict model and fails closed. Native ADRs
  evolve normally; V1 needs historical decisions to be supersedable too.
- **PAT-59** — Linear release scope depends on milestones mapped in the repository
  binding: an issue in an unmapped `projectMilestone` makes the read fail
  (`linear.py:2204-2205`, `milestone_id_unmapped`), and adding a mapping changes the
  registry binding digest pinned by `.foundry/tracker.json`, so it needs the coherent
  binding update of PAT-44/PAT-54 first. (PAT-59 itself is release-scope/milestones/
  changelog; do not confuse it with PAT-66, `ghprojects`' bounded-write tranche.)

**Explicit refusals (optional, not gaps)**: free native-text `search(query=…)` on Linear
(`provider-native-search-query`), `provision_project` on Linear/ghprojects ("provisioning
administratif complet" is explicitly excluded from V1 core by this contract's criterion
4), the optional `get_epic_subgraph` bounded-projection port on YouTrack/Linear (only the
non-V1 DevHub adapter implements it, for its own wave-preview orchestration — the core
epics/children/dependencies journey does not need it), and the optional
`supersede_adr`/`link_adr_issue` reciprocal-linking enhancement on YouTrack (ADR
evolution itself is fully served by `set_adr_status`, which every V1-core provider
implements).

**`to_qualify` (PAT-65, out of scope here)**: every `ghprojects` cell that needs a real
GraphQL round-trip is unqualified, not guessed. `ghprojects.py` is a deliberate stub:
every `Tracker` method except `resolve_project` raises `NotImplementedError` today.
`resolve_project` itself does not raise — it runs today's shared `registry.resolve()`
lookup — but that lookup is the same basename/`PROJECT_REPO`-keyed mechanism PAT-54 is
closing for YouTrack (§2), and GitHub Projects v2 issues need a repository-identity-
scoped key (§2) that basename-keyed resolution cannot express; its cell is therefore
`to_qualify`, not `supported`, even though it executes without raising today. PAT-65
qualifies what GitHub Projects v2 and GitHub-hosted ADR storage actually support on
authorized test resources; PAT-57/58/66/67 implement the read, ADR-storage,
bounded-write and full-lifecycle tranches once PAT-65 lands. This contract records the
cells as `to_qualify`; it does not decide them.

## 2. Identity model

**Canonical repository identity exists and is precisely defined — but not every
provider path uses it yet.** `registry.canonical_repository_identity()`
(`registry.py:206-260`) normalizes any Git remote form Foundry's GitHub code-host adapter
accepts (HTTPS, URL-style SSH, scp-style SSH, ssh-config aliases) into a credential-free
`host/owner/repo` string, lower-cased, with no DNS resolution and no retained user-info.
`registry.checkout_repository_identity()` (`registry.py:263-272`) reads the *actual*
checkout's `origin` and canonicalizes it — never an inherited environment alias. A
repository's tracker binding is keyed on this identity when a `.foundry/tracker.json`
marker is present (`_TRACKER_MARKER_RELATIVE_PATH`, `registry.py:73`).

This is where the guarantee currently forks by provider, and this contract states that
plainly instead of asserting one uniform behaviour:

- **Linear resolves and validates by canonical identity only.**
  `LinearTracker.resolve_project` (`linear.py:1250-1256`) discards the repository-name
  argument it is handed entirely; `resolve_checkout_project` (`linear.py:1258-1276`)
  and `validate_mutation_repository` (`linear.py:1278-1280`) both go through
  `registry.resolve_canonical_repository()` (`registry.py:800-830`), which matches only
  on `canonical_repo` and fails closed on an ambiguous or unmatched checkout. Linear
  never discovers a project by basename or `PROJECT_REPO`.
- **YouTrack and `ghprojects` still resolve by repository basename (or the
  `PROJECT_REPO` env override), not by canonical identity.**
  `registry.resolve()` (`registry.py:755-773`) and `registry.entry_for()`
  (`registry.py:699-705`) both key their lookup on `registry.repo_basename()`
  (`registry.py:653-664`); `registry._matching_registry_bindings()`
  (`registry.py:698-712`) falls back to that same basename lookup even when it first
  tries a canonical-identity match. Base `Tracker.validate_mutation_repository`/
  `validate_mutation_project` (`base.py:170-184`) are no-op defaults, and neither
  `YouTrackTracker` nor `GitHubProjectsTracker` overrides them. **Two repositories that
  share a basename — a fork, a rename, or an operator's stale `PROJECT_REPO` — are not
  distinguished by these two providers today.** This is a real gap against the
  guarantee below, not a hypothetical: it is why the identity-and-project-resolution
  cell for YouTrack is `gap` (PAT-54) and for `ghprojects` is `to_qualify`
  (PAT-65/PAT-54) in §1's matrix, not `supported`.

**The guarantee this contract commits V1 to — two GitHub issues numbered `#12` in two
different repositories are never interchangeable, and nothing discovers a project or an
issue by name/basename/prefix alone — therefore holds today only for Linear.** PAT-54 is
the ticket that brings YouTrack and (once PAT-65 qualifies transport) `ghprojects` up to
the same canonical-identity-only resolution Linear already proves.

**Normalized issue key.** The pipeline's `Issue.id` (`models.py:22-23`) is the
human-readable `<PROJECT-KEY>-<NUMBER>` form (e.g. `PAT-42`, `FOUNDRY-100`) **for
YouTrack and Linear**, both of which mint one project key per canonical repository
binding, so the key alone is unambiguous inside this pipeline's registry. **This shape is
not sufficient for `ghprojects`**: a GitHub Projects v2 board can aggregate issues from
several repositories into one project, so a bare `<NUMBER>` (or even `<KEY>-<NUMBER>`,
since GitHub issue numbers are per-repository, not per-project) does not, by itself,
name one issue — two repositories inside the same Project v2 can both have a `#12`. The
normalized key for `ghprojects` must therefore be scoped by the issue's own repository
identity, e.g. a `<canonical repo identity>#<number>` form (`host/owner/repo#12`) rather
than a bare `<PROJECT-KEY>-<NUMBER>`; the exact wire format is PAT-65's qualification to
make, not decided here, but the requirement that it carry repository identity — not just
a project-scoped counter — is decided by this contract, because it is what closes the
"two `#12`s are not interchangeable" guarantee for this provider. Until PAT-65 qualifies
that shape, `ghprojects`'s normalized-key cell stays `to_qualify`, never guessed.

**Provider IDs stay distinct from the normalized key.** Every adapter keeps its own
internal identifier private to the adapter: YouTrack's `idReadable` vs. its internal
entity id, Linear's `identifier` (`PAT-42`) vs. its GraphQL node UUID (`_SAFE_ID`,
`linear.py:51` — a generic safe-identifier pattern used to validate several distinct
provider identifier shapes, not specifically a UUID validator), and — once qualified —
GitHub's `(repo, issue number, Project item node id)` triple. The pipeline and skills
only ever see the normalized `Issue`/`Adr`/`Project` dataclasses (`models.py`); a
provider payload never leaks through.

## 3. Tracker / CodeHost / Ship-iOS boundaries

- **Tracker** (`trackers/base.py`) owns issues, ADRs, and their lifecycle state — the
  subject of this contract.
- **CodeHost** (`codehosts/base.py`, `codehosts/github.py`) owns PRs, branches, CI and
  merges, over REST only (see §5's open point on the GraphQL tension). It is a separate
  port with a separate provider choice (`FOUNDRY_CODEHOST`); today GitHub is the only real
  code-host adapter regardless of which tracker is active.
- **Ship-iOS never talks to a Tracker adapter.** `plugins/ship-ios/scripts/changelog_bridge.py`
  is deliberately thin: it locates and shells out to `foundry_cli.py query changelog
  <MILESTONE>` and only consumes that JSON. It has no import of `foundry.trackers`, no
  provider credential, and treats Foundry as an entirely optional input (exit code 3 →
  the caller falls back to a changelog file). This holds for whichever of the three
  trackers is active behind Foundry: Ship-iOS's contract is with Foundry's `query` tier,
  never with a tracker directly.

## 4. Mutation guarantees when the provider has no transaction/CAS

Neither YouTrack nor Linear, as used by Foundry today, exposes a provider-side
compare-and-swap precondition on an issue or ADR body write; whether GitHub Projects v2
offers one is unqualified until PAT-65 and must not be assumed either way. Foundry never
presents its fallback as an exclusion of concurrent writes; the guarantee levels below
name exactly what each level detects and what it cannot prevent. This is a closed
vocabulary of *levels*, not a ranking of "how good" a provider is — a provider can be at
a different level on different operation families, and this section names the family
each citation belongs to explicitly, because a single "body writes" label previously
conflated four different things (single-field writes, one full-body replacement,
append-only versioned documents, and graph closure) that do not share a guarantee.

- **Level −1 — blind write, no detection at all.** No read-before-write, no readback, no
  lock. **YouTrack's grooming/status-field writes are here**: `update_fields`
  (`youtrack.py:345-350`), `set_state` (`youtrack.py:353-355`, which is `update_fields`
  under a different name), and `link` (`youtrack.py:358-364`) are unconditional POSTs —
  no expected-value precondition, no local lock, no readback comparison. A concurrent
  writer's change is silently clobbered and neither writer is told. Label this level
  honestly wherever it is cited: it is strictly weaker than level 1, and "bounded" must
  never be claimed for it.
- **Level 0 — no bounded fallback (`gap`).** The operation is refused outright rather
  than attempted unsafely. Example: Linear's `sync_acceptance_body`
  (in-place issue-body checkbox sync) today (PAT-56, see §1) — refused, not attempted.
- **Level 1 — single read-verify-write-readback, no retry.** YouTrack's `update_body`
  (`youtrack.py:392-448`): a local `flock`-based lock (`_body_lock`) serializes Foundry's
  *own* writers for one resource, then the method reads the current body, refuses with
  `TrackerConflictError` if it already diverges from `expected_body`, performs exactly
  one write, and reads back to confirm the result. **This detects a subset of races
  (Foundry-local, and any non-Foundry writer whose change lands and is observed at the
  right moment) — it does not exclude a concurrent non-Foundry writer from winning
  between the read and the write.** YouTrack's `sync_acceptance_body` reuses this same
  path. This is the honest label the docstrings already carry
  (`youtrack.py:396-402,109-111`); this contract does not weaken or relabel it.
- **Level 2 — read-verify-write plus a byte-exact witness chain, but append-only, never
  an in-place overwrite.** Linear's ADR mutations (`_update_adr_body`,
  `create_adr`/`set_adr_status`/`supersede_adr`/`link_adr_issue`) never overwrite a
  Document in place: each call appends a *new* Document at a deterministic slot id
  derived from `(project_id, adr_id, sequence)` (`_append_adr_versions`,
  `linear.py:3506-3525`; call sites at `linear.py:4769-4770` `supersede_adr`,
  `linear.py:4520-4521` `set_adr_status`, `linear.py:3445-3453` `link_adr_issue`), with a
  version witness carrying a SHA-256 digest of the canonical UTF-8 body
  (PAT-ADR-0002/PAT-ADR-0003). There is no local lock: the concurrency boundary is that a
  concurrent writer appending the *same* next sequence number collides on the provider's
  own document id and one of the two writes fails, rather than silently overwriting the
  other. This is a materially different mechanism from level 1 (append-only deterministic
  slot versioning, not read-verify-write-readback on one mutable value) even though both
  ultimately fail closed on a detected divergence; do not describe it as "level 1 plus a
  witness". **Linear's in-place issue-body edits (AC checkboxes, arbitrary issue text)
  are refused, not attempted at level 1 or 2**, precisely because there is no append-only
  slot for an issue body the way there is for an ADR Document: `linear.py:8-11`'s module
  docstring states this directly ("Linear does not expose a compare-and-swap
  precondition for existing-issue replacement. Those writes are therefore unavailable: a
  local lock or a readback cannot prevent an external writer from being overwritten.").
  Do not read Linear's ADR-body proof as evidence that the same read-verify-write-
  readback pattern is available or safe for issue AC; it explicitly is not, which is
  exactly why `sync_acceptance_body` is refused (PAT-56) rather than attempted the way
  YouTrack attempts it.
- **Level 2.5 — append-only deterministic-id lifecycle markers (state projection,
  resume, acceptance).** Linear's issue lifecycle transitions
  (`set_state`/`_project_lifecycle`) and acceptance projection
  (`project_acceptance_proof`) never rewrite the issue's native state or body in place;
  they append an immutable comment whose id is a UUID deterministically derived from a
  SHA-256 digest of the operation's canonical coordinates
  (`_lifecycle_marker`, `linear.py:1349-1388`: `comment_id =
  str(uuid.UUID(slot_digest[:32], version=4))`). **What this guarantees**: replaying the
  exact same operation (same issue, same operation, same generation) after an ambiguous
  network response is idempotent — the second attempt lands on the same deterministic
  comment id instead of creating a duplicate marker or silently double-transitioning the
  issue, which is precisely what RESUME needs after a session is interrupted mid-write.
  **What this does not guarantee**: it does not exclude a second, *different* writer
  from appending a conflicting marker for a different generation/coordinates in the
  window between Foundry's read and its append — that race is still open, only the
  identical-replay case is closed. Status projection reads this same append-only chain
  (`_lifecycle_projection`) rather than trusting the issue's native state/checkbox field,
  which is why Linear's `Issue.ac_done` and lifecycle state are proof-bound projections,
  not native-field reads (see §1, PAT-56 bullet).
- **Level 3 — provider-verified CAS plus an audited receipt.** DevHub's `close_epic`
  (`devhub.py:825`) is the only adapter today that locks the parent graph, compares an
  exact version/state snapshot, and persists an atomic receipt. It is **not** available
  on any of the three V1 trackers (hence the `epic-closure` gap, PAT-69) and DevHub
  itself is out of V1's tracker set. **Epic closure is not merely another mutation to
  bound**: `write.close_epic`'s docstring (`write.py:371`) already names the current
  invariant as an *atomic provider graph operation*, and FOUNDRY-ADR-0017 makes epic
  closure the carrier of the one human verdict per Epic, with its own timestamped,
  replayable receipt tied to the exact set of terminal children. Defining any
  achievable no-CAS guarantee for PAT-69 on YouTrack/Linear — even a carefully-labelled
  level 1/2.5-style bounded fallback — **changes that durable invariant** for those two
  providers, not merely documents an existing one. **PAT-69 is therefore blocked until a
  new ADR is explicitly accepted** proposing the bounded guarantee it would implement. A
  draft for that ADR — re-verify the complete parent/children graph and AC proofs from a
  fresh read immediately before the single write, append-only deterministic receipt on
  the model of `_lifecycle_marker` above, fail closed on any divergence (a reopened
  child, a version change, a missing audit), and never present this as CAS or as an
  exclusion of concurrent writers — is written up for review at
  `pat53-adr-draft.md` (kept outside this repository's tracked docs; PAT-53 does not
  create it in any tracker). Until that ADR is accepted, `epic-closure` stays `gap` for
  YouTrack and Linear in §1's matrix, and no PAT-69 code may claim CAS or exclusion
  unless the provider actually offers it.

**No durable invariant changes in this section as written.** §4 above documents the
guarantee levels the current code already implements (levels −1, 0, 1, 2, 2.5, and the
level 3 DevHub proves out-of-V1) and labels the weaker ones honestly; by itself it
proposes no new adapter behaviour and does not touch any accepted ADR's decision
(FOUNDRY-ADR-0026, PAT-ADR-0001..0003 already establish the level-1/level-2 pattern for
the Linear cutover). The one place this contract *does* point at a needed durable-
invariant change is the level-3 paragraph above (PAT-69): that is exactly why it is
gated on a new ADR rather than asserted here.

**Any change to a durable invariant — not only a claim that weakens or strengthens an
existing guarantee level — requires a new, explicitly accepted ADR before the code that
implements it.** A change that makes an invariant's *effect* observable differently (for
example: turning an atomic all-or-nothing graph operation into a bounded, explicitly
racy detect-and-fail-closed sequence, even while being scrupulously honest about the
weaker guarantee) is still a change to what callers, reviewers, and downstream tooling
can rely on — it is decided by ADR, not asserted by this contract or implemented as a
quiet code change (criterion 3). This is the same reading criterion 3 already states;
this section makes explicit that "durable invariant" is not limited to the
weaken/strengthen framing of a single CAS claim.

## 5. Optional / excluded scope and the open GraphQL/REST point

**Explicitly optional or excluded from V1 core** (criterion 4): *full administrative*
project provisioning — creating a brand-new provider org/team/board from nothing,
configuring its custom fields/workflow states/permissions — is out of scope; free
native-text search; general (non-PAT-64) inter-tracker migration of terminal/closed
history; and any code host other than GitHub.

**What is explicitly in scope, and not confused with the above**: a *minimal
create-or-recover bootstrap* for a repository's own project on each of the three V1
trackers — the same shape `provision_project`/`resolve_project` already give YouTrack
today (`youtrack.py:113-131`, idempotent create-or-recover) — belongs to PAT-54's
per-repo binding work, alongside the canonical-identity resolution fix §2 and §1
describe. PAT-54 is expected to bring Linear and `ghprojects` (once PAT-65 qualifies
transport) to the same minimal bootstrap Linear's binding today still requires by hand
(`docs/linear-tracker.md` "Required binding"). §1's `project-provisioning` operation row
records today's `supported`/`refused`/`refused` cells for that minimal capability as they
stand now; it is *full* provisioning beyond create-or-recover that criterion 4 excludes,
not the create-or-recover bootstrap itself.

**Open point for PAT-65, not decided here.** GitHub Projects v2 has no REST surface; it
is GraphQL-only (`ghprojects.py` module docstring: "GitHub Projects v2 is GraphQL-only,
which is exactly why the original setup moved off it"). Foundry's code-host rule is REST
only, never GraphQL (`skills/merge-pr/SKILL.md:226`: "Never GraphQL (`gh pr create/merge/
checks`). REST only — the adapter enforces it."). A repository that picks `ghprojects` as
its **tracker** still uses the REST-only GitHub **code-host** adapter for PRs and merges;
only the tracker adapter itself needs GraphQL. This is a real tension between two
GitHub-facing surfaces of the same repository and is recorded for PAT-65 to resolve —
PAT-53 flags it and decides nothing about it.

## 6. Conformance test plan

- **PAT-68 — deterministic conformance suite (fake transport).** A reusable suite the
  `Tracker` port itself must pass for all three providers: core operations, capability
  flags, explicit refusals, pagination, permission errors, conflicts, ambiguous
  responses, and resumption — all against doubles, never real providers. A missing core
  capability must fail the suite for that adapter, not be skipped. This repository's
  [`test_tracker_contract.py`](../tests/test_tracker_contract.py) is a narrower, PAT-53-scoped
  pin (schema + ABC-membership + ticket citation), not PAT-68's suite.
- **PAT-61 — real six-configuration recipe (three trackers × two hosts).** Authorized
  test-resource recipes proving each journey for real against YouTrack, Linear and
  GitHub Projects, in both Claude Code and Codex. Doubles from PAT-68 are never presented
  as this real recipe.
- **PAT-65 — GitHub Projects v2 + ADR-storage qualification**, on authorized test
  resources only, no adapter implementation. Feeds PAT-57/58/66/67's forecast.

## Document status

This is contract **v1**, matching `tracker-contract.v1.json`'s `version: 1`. A change to
any status cell, the operation list, or the closed status vocabulary bumps the JSON
`version` and this heading together; they must never diverge.
