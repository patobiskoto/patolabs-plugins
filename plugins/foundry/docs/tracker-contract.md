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

## 1. Core journeys and the capability matrix

Criterion: every core journey below must be `supported` on all three trackers before V1
ships, or have every non-`supported` cell be either `gap` (blocking, with the ticket that
closes it) or `refused`/`to_qualify` with an explicit reason — never a silent omission.

| Core journey | Representative `Tracker` ABC ops | YouTrack | Linear | ghprojects |
|---|---|---|---|---|
| Contexte/backlog | `resolve_project`, `search`, `get_issue` | supported | supported | `to_qualify` (PAT-65) |
| Frame/intake/groom | `create_issue`, `update_fields`, `update_body`, `add_comment` | supported | **gap** (existing-issue field/body edits, PAT-55) | `to_qualify` (PAT-65/PAT-66) |
| Epics/enfants/dépendances | `create_issue(parent=…)`, `link` | supported | **gap** (reparenting an existing issue, PAT-55); creation and relates/depends-on/blocks are supported | `to_qualify` (PAT-65) |
| Lecture/création/évolution ADR | `list_adrs`, `create_adr`, `set_adr_status` | supported | supported for native ADRs; **gap** for successor versions of imported historical ADRs (PAT-47) | `to_qualify` (PAT-65/PAT-58, storage choice deferred) |
| Start/resume/review/merge | `set_state` | supported | supported | `to_qualify` (PAT-65/PAT-67) |
| État et AC | `sync_acceptance_body` | supported (bounded, not CAS — see §3) | **gap** (PAT-56) | `to_qualify` (PAT-65) |
| Clôture d'epic | `close_epic`, `get_epic_closure` | **gap** (PAT-69) | **gap** (PAT-69) | `to_qualify` (PAT-65) |
| Périmètre de release/changelog | `search`/`get_issue` via `query.py changelog()` | supported | **gap** (milestones must be mapped in the binding; PAT-59 after PAT-44/PAT-54) | `to_qualify` (PAT-65) |
| Bascule de dépôt par copie fidèle (PAT-64) | `import_adr`, `import_adr_batch`, `validate_mutation_project` | **gap** as import target (PAT-64); tombstone-as-source generalization (PAT-43) | supported as import target (the proven YouTrack→Linear precedent); PAT-64 generalizes it to every pair | `to_qualify` (PAT-65) |

The full matrix — every operation, every cell, and the code citation behind each status —
is [`tracker-contract.v1.json`](tracker-contract.v1.json). A one-line summary here is not
a substitute for it; read the JSON before relying on a specific cell.

**Gap tickets found by reading the current adapters** (see the JSON `evidence` field for
each):

- **PAT-55** — Linear cannot groom an already-created issue: `update_fields` (existing
  issue) and `update_body` (Issue, not Adr) both raise unconditionally
  (`linear.py:2455-2470`, `:2856-2868`), and `link()` refuses `subtask-of`/`parent-of` on
  an existing issue (`linear.py:2770-2774`, `existing-issue-parent-replacement`).
- **PAT-56** — Linear's acceptance-criteria sync has no bounded fallback at all:
  `sync_acceptance_body` (`linear.py:2870-2882`) raises `AcceptanceSyncUnavailableError`
  unconditionally, even though Linear already proves the same bounded
  read-verify-write-readback pattern for ADR bodies (`_update_adr_body`).
- **PAT-69** — Neither YouTrack nor Linear implement `close_epic`/`get_epic_closure`;
  both keep `epic_closure_supported = False` (or unset, which defaults to `False`) and
  `write.close_epic` (`write.py:377`) refuses before any provider call. Only the non-V1
  DevHub adapter proves the port (`devhub.py:220-223,825`).
- **PAT-64** — YouTrack has no `import_adr`/`import_adr_batch` override, so it cannot be
  a *target* of a tracker switch yet; the only proven leg is the YouTrack→Linear
  precedent this repository already lived (FOUNDRY-ADR-0026, PAT-ADR-0001..0003, `registry
  cutover linear`). PAT-64 generalizes that one proven, one-directional leg into a
  source/target-agnostic capability across all three trackers.
- **PAT-43** — YouTrack's archived-project tombstone needs to generalize to writes that
  target an archived project purely by identifier (not only through registry binding),
  so a PAT-64 switch source stays refused for every write shape once it becomes an
  archive.
- **PAT-47** — On Linear, an imported historical ADR whose qualified rendering differs
  from the local model cannot receive any successor version (accept, supersede, link,
  edit): its predecessor is re-read with the strict model and fails closed. Native ADRs
  evolve normally; V1 needs historical decisions to be supersedable too.
- **PAT-59** — Linear release scope depends on milestones mapped in the repository
  binding: an issue in an unmapped `projectMilestone` makes the read fail
  (`linear.py:2204-2205`, `milestone_id_unmapped`), and adding a mapping changes the
  registry binding digest pinned by `.foundry/tracker.json`, so it needs the coherent
  binding update of PAT-44/PAT-54 first.

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
GraphQL round-trip is unqualified, not guessed. `ghprojects.py` is a deliberate stub: every
`Tracker` method except `resolve_project` (which only does provider-agnostic registry
lookup) raises `NotImplementedError` today. PAT-65 qualifies what GitHub Projects v2 and
GitHub-hosted ADR storage actually support on authorized test resources; PAT-57/58/66/67
implement the read, ADR-storage, bounded-write and full-lifecycle tranches once PAT-65
lands. This contract records the cells as `to_qualify`; it does not decide them.

## 2. Identity model

**Canonical repository identity.** `registry.canonical_repository_identity()`
(`registry.py:206-260`) normalizes any Git remote form Foundry's GitHub code-host adapter
accepts (HTTPS, URL-style SSH, scp-style SSH, ssh-config aliases) into a credential-free
`host/owner/repo` string, lower-cased, with no DNS resolution and no retained user-info.
`registry.checkout_repository_identity()` (`registry.py:263-272`) reads the *actual*
checkout's `origin` and canonicalizes it — never an inherited environment alias. A
repository's tracker binding is keyed on this identity (`.foundry/tracker.json`,
`_TRACKER_MARKER_RELATIVE_PATH`, `registry.py:73`), and every provider's
`validate_mutation_repository`/`validate_mutation_project` hook (`base.py`) is the seam
that refuses a mutation whose checkout does not match its registered identity. This is
why **two GitHub issues numbered `#12` in two different repositories are never
interchangeable**: nothing in Foundry discovers a project or an issue by repository name,
basename, or numeric prefix alone — resolution always goes through the canonical
`host/owner/repo` identity first (`resolve_checkout_project`, `registry.resolve()`).

**Normalized issue key.** The pipeline's `Issue.id` (`models.py:22-23`) is always the
human-readable `<PROJECT-KEY>-<NUMBER>` form (e.g. `PAT-42`, `FOUNDRY-100`), the same
shape across all three trackers, never a provider-internal identifier.

**Provider IDs stay distinct from the normalized key.** Every adapter keeps its own
internal identifier private to the adapter: YouTrack's `idReadable` vs. its internal
entity id, Linear's `identifier` (`PAT-42`) vs. its GraphQL node UUID (validated by
`_SAFE_ID`, `linear.py:51`), and — once qualified — GitHub's `(repo, issue number, Project
item node id)` triple. The pipeline and skills only ever see the normalized `Issue`/`Adr`/
`Project` dataclasses (`models.py`); a provider payload never leaks through.

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
offers one is unqualified until PAT-65 and must not be assumed either way. Foundry never presents its fallback as an exclusion of
concurrent writes; the guarantee levels below name exactly what each level detects and
what it cannot prevent.

- **Level 0 — no bounded fallback (`gap`).** Example: Linear's `sync_acceptance_body`
  today (PAT-56) — the operation is refused outright rather than attempted unsafely.
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
- **Level 2 — read-verify-write-readback plus a byte-exact witness chain.** Linear's ADR
  body path (`_update_adr_body`, and `create_adr`/`set_adr_status`/`supersede_adr`) adds
  a version witness with a SHA-256 digest of the canonical UTF-8 body
  (PAT-ADR-0002/PAT-ADR-0003) on top of the same level-1 pattern, so a *detected*
  divergence is provably attributable, but the underlying race window versus a
  non-Foundry writer is the same as level 1.
- **Level 3 — provider-verified CAS plus an audited receipt.** DevHub's `close_epic`
  (`devhub.py:825`) is the only adapter today that locks the parent graph, compares an
  exact version/state snapshot, and persists an atomic receipt. It is **not** available
  on any of the three V1 trackers (hence the `epic-closure` gap, PAT-69) and DevHub
  itself is out of V1's tracker set.

**No durable invariant changes here.** This section documents the guarantee levels the
current code already implements and labels the weaker ones honestly; it proposes no new
behaviour and does not touch any accepted ADR's decision (FOUNDRY-ADR-0026,
PAT-ADR-0001..0003 already establish the level-1/level-2 pattern for the Linear cutover).
**No ADR draft accompanies this contract**, because it changes no durable invariant: it
records the levels the adapters already implement. A future change that *weakens* an existing guarantee level, or that claims a stronger level
than a provider actually offers, must go through a new, explicitly accepted ADR before
the code changes (criterion 3) — this contract is not that ADR, and none of PAT-55/56/69
may claim CAS unless the provider actually offers it.

## 5. Optional / excluded scope and the open GraphQL/REST point

**Explicitly optional or excluded from V1 core** (criterion 4): full administrative
project provisioning beyond `resolve_project`/`provision_project`'s minimal
create-or-recover contract, free native-text search, general (non-PAT-64) inter-tracker
migration of terminal/closed history, and any code host other than GitHub.

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
