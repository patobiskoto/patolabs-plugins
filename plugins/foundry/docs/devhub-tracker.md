# DevHubTracker provider

Foundry can use Dev Hub as its Tracker source of truth while retaining all execution,
routing, review, CI and merge authority. The adapter targets the published V1
boundaries under `/api/tracker/v1` and does not change any judgment skill:

| Surface | Exact contract | Purpose |
| --- | --- | --- |
| Application routes | `devhub-application.v1` | Project resolution, issues, ADRs, mutations and audit reads |
| Project discovery | `devhub-project-catalog.v1` | Bounded, read-only project catalogue; never a repository binding |
| Original Epic closure | `devhub-epic-closure.v1` | Atomic closure plus replayable proof of the exact pre-closure graph |
| Terminal recovery | `devhub-foundry-terminal-receipt.v1` | Project-bound recovery of an existing terminal reconciliation |
| Governance mode | `devhub-governance.v1` | Read-only project governance mode (`manual` or `foundry`), see "Governance mode" |
| Proof envelope | `devhub-tracker.v1` | The signed Foundry proof payload verified for guarded transitions |

Foundry rejects a response whose header or pagination schema does not match the
contract of the requested route. It never falls back from the catalogue to project
resolution: only the application route returning `canonical_repo` can confirm a
repository-bound mutation target.

## Configuration

Write only the non-secret settings:

```bash
python3 tooling/foundry_cli.py configure set \
  --tracker devhub --codehost github --devhub-url http://127.0.0.1:3000
```

### Transport prerequisites

Use HTTPS for every deployed or remote Dev Hub origin. Cleartext HTTP is accepted only
for a canonical numeric IPv4 or IPv6 loopback literal (for example
`http://127.0.0.1:3000` or `http://[::1]:3000`) for a local POC or test service. DNS names
such as `localhost`, alternate numeric encodings, IPv4-mapped IPv6 and scoped IPv6 forms
are refused rather than resolved or guessed.

The adapter validates this transport boundary before loading credentials or constructing
a request. Redirects must remain on the same validated origin; a cross-origin redirect,
an HTTPS downgrade or a redirect to non-loopback HTTP is refused before bearer or proof
headers can be forwarded. Doctor's file fallback reads only the public provider selector
and URL until that validation succeeds; it never bulk-parses unrelated secret entries.

For every versioned Application V1 mutation, Foundry sends the canonical
`X-DevHub-Version: <positive decimal>` header. It also sends the complete quoted
`If-Match: "<same decimal>"` form during the documented compatibility period. The two
values must therefore agree: missing, malformed, stale or contradictory preconditions
remain typed provider refusals, never an unversioned fallback. Idempotent link replays
retain their same `Idempotency-Key`; a rejected version precondition is not retried as a
new mutation.

### Versioned link diagnosis (FOUNDRY-98)

The original `428 version_required` on `DevHubTracker.link` was a transport mismatch:
Foundry emitted only the compatibility `If-Match` ETag while Application V1 names
`X-DevHub-Version` as its canonical source precondition. Link creation also carries the
bound target issue's `target_version` in its JSON body, so Dev Hub can lock and compare
both endpoints before inserting the relation. The adapter now emits both matching source
forms and the exact target version. This is not a fallback or an authority change: the
server still rejects an absent, malformed, stale or contradictory version before it can
create a link, and an ambiguous client retry reuses the exact same idempotency key.

On macOS, store the two independent credentials through hidden prompts:

```bash
python3 tooling/foundry_cli.py configure credential --name DEVHUB_TRACKER_TOKEN
python3 tooling/foundry_cli.py configure credential --name DEVHUB_TRACKER_PROOF_SECRET
```

On Linux/CI, inject those names through the host secret manager. Never write them in
`config.env`, the registry, the repository or command arguments. Credential fields in
`configure show` report only `configured`/`missing`, and `doctor` reports only that the
endpoint is configured.

Legacy plaintext secret fallback remains readable for compatibility, but it is read only
after the transport preflight. `configure set` fails closed rather than rewriting a
`config.env` that still contains a `TOKEN` or `SECRET` key: move that value to Keychain,
the environment, or the host secret manager, remove the plaintext line, then retry.

The opt-in local preprocessor already expunges DevHub credentials supplied through the
environment. Expurgation of DevHub-only values resolved solely from the macOS keychain
requires a separately versioned local-boundary requalification (FOUNDRY-76); do not use
that combination until it lands. This does not affect the normal cloud roles or the
DevHubTracker adapter.

## Project binding and cutover

For a new project, run the provider-neutral setup command from the repository checkout:

```bash
python3 tooling/foundry_cli.py setup_project "My Project" <TICKER> <repo-basename> \
  [--import-adrs docs/adr]
```

The DevHub capability reads and canonicalizes the checkout's `origin`, resolves an
existing project by that identity, or creates a minimal project through the tracker API
with a deterministic idempotency key. It confirms the repository binding before Foundry
writes the single local registry entry and, since PAT-323, the repository marker
(`setup_project.py` calls `registry.bootstrap_repository_binding` for a V1 provider whose
project carries `canonical_repo`). A retry after provider success but before the
registry write therefore recovers the same project. No manual REST call or separate
`registry register` step is required.

DevHub is a V1 provider since PAT-ADR-0031 (PAT-323): its binding is the ordinary canonical
repository marker (`.foundry/tracker.json`), never deduced from a repository name, and the
former pilot path (basename resolution, `mode=pilot`, host-global selection of a bound
checkout) is retired. To bind a project that was created outside the setup flow, run
`python3 tooling/foundry_cli.py registry bootstrap devhub <repo> <KEY> <project-id>` from the
checkout: it reads the exact project back (`verify_project_identity`, including the
governance mode, "Governance mode") and publishes the entry and the marker only if both
hold. `registry register` alone writes an entry without a marker, which fails closed.

An existing DevHub registry entry that carries `canonical_repo` but has no marker (for
example one written by `registry register` before PAT-323) is classified, deduced from
`registry.repository_tracker_selection`, as an interrupted V1 publication: every command
from that checkout refuses with "Binding tracker V1 sans marqueur" and nothing is promoted
silently. Run the same `registry bootstrap devhub` command with the entry's own key, id and
`canonical_repo=<host/owner/repo>`; when the entry is identical it only writes the missing
marker. An entry without `canonical_repo` is legacy and is upgraded with `registry upgrade
devhub <repo>`. An entry whose repository has no local checkout is untouched.

Run `doctor` before the first write. It resolves the canonical repository remotely and
compares the credential-free `host/owner/repo` identity of the actual `origin` remote with
the registered `canonical_repo`. HTTPS, URL-style SSH, scp-style SSH and ssh-config
aliases are normalized without DNS resolution. A missing, contradictory, or
same-basename/different-owner binding is refused before mutation. Cut over only a new
project with no prior tracker writes; Foundry performs no dual-write and does not
migrate YouTrack data.

`registry register` canonicalizes authenticated remote forms to credential-free
`host/owner/repo` before persistence. Legacy records are sanitized in memory before
display or transport; an identity that cannot be canonicalized fails closed without
echoing its raw value.

Every mutation addressed by issue or ADR id re-resolves that current-repository binding
before it writes. The DevHub adapter then checks that every id belongs to the bound ticker
(both ends of a link included). A CLI invocation from another repository therefore fails
before a tracker mutation, PR creation, merge or cleanup can act on the foreign id.

Rollback is configuration-only **before the first DevHub write**: restore
`FOUNDRY_TRACKER=youtrack` and its previous registry binding. After a DevHub issue or ADR
exists, do not switch back silently; frame an explicit migration decision instead.

## Starting work without partial state

`issue start` preflights the provider workflow before creating or checking out a branch.
For Dev Hub, `backlog` resolves to `ready → in-progress`, while `ready`, `review`, and
`blocked` resolve directly to `in-progress`; terminal states fail before Git is touched.
If the tracker becomes unavailable after the branch mutation, the branch is deliberately
kept as the recovery coordinate. A retry from its worktree reloads the issue and executes
only the remaining transitions. This forward recovery avoids deleting a branch that may
already contain user work. The default tracker contract remains a direct transition, so
the YouTrack workflow is unchanged.

## Proof-bound transitions

The adapter signs short-lived HMAC envelopes for `review`, `done` and ADR lifecycle
changes. `review` binds PR URL, exact PR base, PR head and exact diff digest. `done` binds
the same review coordinates plus the merge receipt SHA. Foundry's existing mechanical open-PR,
review proof and CI gate remain authoritative; Dev Hub only verifies and stores the
receipt. A mismatch or unavailable proof authority fails closed.

Lean search keeps Dev Hub's stored edge type and direction. At the adapter boundary,
inward asymmetric edges are normalized to the current issue's role (`depends-on` ↔
`blocks`, `subtask-of` ↔ `parent-of`) before Foundry computes `blocked_by`, `unlocks`,
next-issue candidates, roadmap rollups or blocker reports.

The adapter never derives review evidence from its own working directory. Only the
mechanical `issue openpr` and `issue merge` paths may supply `TransitionContext`; a generic
`edit transition ... review|done` is refused. Both paths require the exact 40-hex PR base
commit returned by GitHub's REST API, include that SHA in the signed receipt, and hash the
binary diff from that immutable SHA.
Merge reuses those exact bytes and exact base coordinate for any structured AC proof;
missing, invalid, or stale base coordinates fail closed.

## Append-only lifecycle receipts (PAT-309, DHN-38)

`DevHubTracker.append_only_lifecycle_supported` is `True` (`trackers/devhub.py`), so
`campaign_cadence.missing_capabilities` no longer reports it; since PAT-313 it reports
nothing for DevHub (`test_devhub_declares_the_append_only_lifecycle_the_cadencer_reads`,
"Issue creation reconciliation" below). What the flag makes
Foundry call is the set of `getattr(tr, "append_only_lifecycle_supported", ...)` branches of
`issue.py`; the page `devhub-requirements.md`, "Needs", row DHN-38, keeps the status.

- `read_proof_receipts(issue_id, project=None)` reads
  `GET /api/tracker/v1/issues/:id/proof-receipts?cursor=&limit=` (route and fields: DevHub
  `docs/TRACKER_API.md`, "Issue proof receipts", read at `2edcea2`). It validates every item
  against the `proofReceiptSchema` keys, the issue and project of the request, the
  `merge_sha` rule (`done` only), the ascending ids and the unique nonces, follows the
  cursor up to `_MAX_RECEIPT_PAGES` pages of 100 and `_MAX_RECEIPT_ITEMS` items, and fails
  closed (`DevHubTrackerError`) otherwise. Since PAT-318 the item has the extended form
  (16 keys: the 13 above plus `reason`, `decision_digest`, `decision`) and four known
  actions, with the per-action null rules of "Acceptance override and human acceptance"
  below.
- With the flag, `issue openpr` no longer writes the `GitHub PR` field (`issue.py:492-494`):
  the issue's `pr_url`, which `DevHubTracker` reads only from `raw["pr_url"]`
  (`trackers/devhub.py:577`) and which `issue.py:607` and `issue.py:883`, `recover_done_projection`
  and the release check compare with the PR URL, comes from the review proof. DevHub's proven
  transition runs `pr_url = COALESCE($3, pr_url)` with the proof's `pr_url`
  (`2edcea2:src/tracker/store.ts:1714-1716`, after `consumeProof` requires a non-empty one,
  `store.ts:1690-1698`; observed in source, observed live in the run of PAT-309 by
  `test_dhn_38_lifecycle_is_append_only_for_the_cadencer`, which makes the review
  transitions without writing the field and sees `pr_url` follow each new PR URL; offline
  mirror: `test_review_transition_proof_sets_the_issue_pr_url_without_a_field_write`).
  `issue merge`
  publishes the current PR generation as a `review` transition before reading the
  projection (`issue.py`). DevHub answers `review -> review` with the stored issue and no
  receipt (`2edcea2:src/tracker/store.ts:1686`, observed in source), which would leave the
  head of an earlier round bound to the issue and refuse the later `done` (`store.ts:1700-1712`).
  `set_state` therefore reads the receipts when the issue is already in `review`: the same
  PR URL, head, base and diff digest as the latest receipt other than an `acceptance_override`
  one (`DevHubTracker.set_state`) is a no-op, any other
  generation goes through `in-progress` (the refresh `issue merge` makes for adapters
  without the flag) and then `review`, which appends a new receipt.
- Operational change: with the flag, `issue merge` refuses an issue with no acceptance
  criterion (`ac_total < 1`) before any GitHub merge (`issue.py:598-605`), also under `--allow-incomplete-ac`
  (`tests/test_devhub_issue_merge.py`); a DevHub issue
  with an empty body can no longer be merged through Foundry.
- `recover_done_projection` is read-only: it returns `True` only when the issue is `done`
  on that PR and exactly one `done` receipt carries the coordinates after a `review`
  receipt of the same head, base and digest. DevHub sets the state and appends the receipt in
  one transaction (`2edcea2:src/tracker/store.ts:1714-1744`), so no native State is left to
  repair; `recover_review_projection` is not defined (it is called only after a
  `TrackerConflictError`, which `get_issue` does not raise here). Replaying `issue merge` on
  an issue already `done` goes through it (`issue.py:608-616`), so an issue that reached `done`
  before DEVHUB-104 (no `done` receipt) is refused on replay with "coordonnées du reçu done
  exact divergentes" (deduced from `recover_done_projection` and `issue.py:608-616`, not run).
  `read_proof_receipts` requires the next cursor to equal the id of the last receipt
  returned, as the server returns it (`6721ad8:src/tracker/store.ts:1954-1985`, observed in
  source).
- Not provided, by decision of this issue, and not required by `issue.py` for the flag:
  a start receipt (DevHub receipts no start transition). The typed acceptance-override
  receipt, absent when PAT-309 shipped, is written since PAT-318 (section below).
  `observe_issue` stays the `base.py` default: `normalized_state`, `native_state` and
  `projection_status` stay `None` for DevHub.

## Acceptance override and human acceptance (PAT-318, DHN-25, DHN-26)

Both operations use routes announced by DevHub on 2026-10-20 as DEVHUB-106 (DevHub pull
request 95, merged on DevHub `main` as `f347f38`; `devhub-requirements.md`, question 12).
The adapter is written from that announcement; DHN-25 and DHN-26 passed against an instance
at `f347f38` (`devhub-requirements.md`, "Live run of PAT-318").

- `project_acceptance_override(issue, reason, context)` (`DevHubTracker`, flag
  `acceptance_override_projection_supported = True`, which `issue.py` reads before the
  merge). It reads the issue (must be in `review`) and the receipts, and refuses with
  `TrackerConflictError` unless the four coordinates equal the latest `review` receipt
  and that generation has no `done` receipt. An override already following that review with
  the same reason returns `False` without a request; another reason is refused. Otherwise
  it sends `POST /issues/:id/acceptance-override` with the body `reason`, `pr_url`,
  `head_sha`, `base_sha`, `review_digest`, the issue version, an idempotency key and a proof
  of action `acceptance-override` over `project_key`, `resource_id`, `expected_version`, the
  four coordinates and `reason`, then reads the receipts back and requires one new
  `acceptance_override` receipt. The announced server effect keeps state and version.
  `set_state(review)` ignores override receipts when it decides whether the generation is
  the same. `recover_acceptance_override` raises `TrackerConflictError`: DevHub has no
  recovery after the merge. On a DevHub that lacks the route the override merge is refused
  before any code-host merge (`issue.py`, "reçu typé d'override AC impossible";
  `tests/test_devhub_issue_merge.py`).
- `accept_issue(issue, decision, project=, verify_evidence=)` validates the decision
  (`acceptance_decision.validate`), reads the issue and the receipts, and refuses with
  `TrackerConflictError` a state other than the decision's `from_state`, any existing
  receipt, an Epic, a child, a PR, no criterion, a body or criteria drift
  (`acceptance_decision.check_against_body`) and a failing `verify_evidence`. An existing
  `human_acceptance` receipt of the same digest converges without a request; another digest is
  refused. Otherwise it sends `POST /issues/:id/human-acceptance` with the body `decision`
  and a proof of action `human-acceptance` over `project_key`, `resource_id`,
  `expected_version` and `decision_digest` (`acceptance_decision.decision_digest`), then
  requires the issue `done` and one receipt whose object equals the decision. The command
  `issue accept` and its `--status` stay Linear-only: `human_acceptance_supported` is
  `False` for DevHub (PAT-ADR-0025, "Fournisseurs"; the status reads `linear-human-acceptance`).
- Both: a 404 `route_unavailable`, the answer of a server without DEVHUB-106 (DevHub reply,
  question 12 (e)), becomes `TrackerCapabilityUnavailableError` (`devhub.acceptance_override`,
  `devhub.human_acceptance`; `_route_unavailable`); a 409 `version_conflict` becomes
  `TrackerConflictError`; any other error keeps its
  frozen V1 code (`invalid_transition`, `resource_conflict`, `proof_required`,
  `invalid_proof`, `proof_replayed`, announced; no dedicated code).
- Receipt reader (`_to_proof_receipt`, `_PROOF_RECEIPT_KEYS`): exact key set; actions
  `review`, `done`, `acceptance_override`, `human_acceptance`; `reason` set only for
  `acceptance_override` (public code `[a-z0-9_-]{3,80}`, `models.AC_OVERRIDE_REASON`, shared with `issue.py`);
  `decision_digest` and `decision` set only for `human_acceptance`, where the four
  coordinates and `merge_sha` are null; every other action needs the four coordinates and
  null `reason`, `decision_digest`, `decision`; `merge_sha` set only for `done`. The
  `decision` is stored as jsonb, so the reader recomputes its digest from the object read
  and validates it as the decision document, never comparing bytes. Any other shape raises
  `invalid_response`.
- Not done here: `read_release_scope` still classes a `done` issue without a `done` receipt
  `unavailable`, so an issue accepted by `human_acceptance` or merged under an override is
  not read as `accepted` in a release scope.
- Known limit announced by DevHub (DEVHUB-87, deferred): a decision string with U+0000 or a
  lone surrogate passes validation and fails at insert (a server error, not a 400).

## Release scope (PAT-309, DHN-39)

`read_release_scope(project, release)` maps `release` through
`project.extra["release_ids"][release]`, the Version id `<KEY>-VERSION-N` (a name is not
unique, `devhub-requirements.md` question 11), and reads `GET /api/tracker/v1/versions/:id`
(shape: `versionSchema`, DevHub `src/tracker/application-contract.ts`, read at `2edcea2`).
It refuses with `ReleaseScopeUnavailableError` an unmapped release, an id of another
project, an absent or unreadable Version, a Version whose `name` differs from the release,
an incomplete scope, and a scope whose `total_count` exceeds `direct_count`, because the
Version publishes only its direct issue ids and listing the Epic descendants would be
approximate discovery. It never lists Versions to find one by name. It reads the issues in
the numeric order of their number, then reads the Version again and refuses with the reason
`coordinates_changed` a Version that differs from the first read (`DevHubTracker._read_version`;
the same reason as `ghprojects.py`). Each direct issue is
`accepted` only if it is `done`, all its criteria are checked and one `done` receipt
(preceded by a `review` receipt of the same coordinates) binds its PR; `unfinished` while
it is not `done` or `dropped`; `unavailable` otherwise, which includes a `done` issue with
unchecked criteria (no typed override receipt exists to call it `deviated`). Foundry has no
code that creates a Version or writes the mapping: the registry validates `release_ids` for
YouTrack, GitHub Projects and, since PAT-323, DevHub (`registry._validated_binding_entry`).

## Issue creation reconciliation (PAT-313, DHN-37)

`reconcile_issue_creation(project, issue_id, title, body, fields, parent)` of
`trackers/devhub.py` overrides `Tracker.reconcile_issue_creation`, which is what
`campaign_cadence.missing_capabilities` tests (`campaign_cadence.py`, "Capabilities
required" in `campaign-cadence.md`). It is the same port as Linear's (`edit reconcile-create`,
`edit.py::reconcile_create`, `write.reconcile_issue_creation`): one `GET /issues/:id`, no
write, no replay. It compares the project binding of the id, the identity, the title, the
byte-exact body, the parent (the `subtask-of` link, whichever direction DevHub stores) and
each requested field (`Labels` as a set), and returns `validation: accepted`,
`automatic_retry: false`. A difference raises `TrackerConflictError` ("validation refused;
no retry", the text `campaign_defer.py::_REFUSED` reads) naming the diverging properties and
no value; an absent or forbidden issue raises `IssueUnavailableError`.

Limit (`devhub-requirements.md`, answers 9 and 16): the reference must be an issue id of
the bound project. DevHub has no read by `Idempotency-Key`; it recovers a lost answer only
through an identical replay (same credential, key and payload, including the original
`parent_version`), and the adapter keeps neither key nor payload after the call. `DevHubTracker.create_issue` therefore keeps a random
per-call key and never raises `IssueCreationValidationError`; a client identifier handed to
`reconcile-create` is refused before any read (the binding check or the id parse,
`DevHubTracker._project_key`). Unknown: what the campaign does after a creation whose
answer is lost beyond the two same-key attempts of one call.

## Epic closure over a dropped child (PAT-313, DHN-24)

The DevHub server closes an Epic whose direct children are all `done` or `dropped`, and
checks in the same transaction that the children of the receipt (id, version, state) are
exactly those it reads (`1227841:src/tracker/epic-closure-store.ts:182-183`, observed in
source). PAT-ADR-0030 extends the nominative closure of PAT-ADR-0017 to DevHub: the
adapter declares `epic_dropped_closure_supported`, and the atomic branch of
`write.close_epic` refuses before the server call when the set named by `--accept-dropped`
is not exactly the set of `dropped` direct children it read, with the exact command in
the refusal. `write.validate_epic_closure_request` requires `--human-verdict=accepted`
with the flag. A named dropped child owes no acceptance proof (its AC are not checked);
a child that is not dropped keeps the AC check. Replaying a closed Epic needs the same
named set, read back from the dropped children of the server receipt
(`write._closure_dropped`). Limits (PAT-ADR-0030): the named set and the verdict are
checked by Foundry, not stored by the server, so a client that bypasses Foundry can still
close over a dropped child, and the receipt does not tell a named child from one admitted
by another client. Unlike PAT-ADR-0017 on Linear, PAT-ADR-0030 requires no `done` direct
child: an Epic whose direct children are all `dropped` and named closes
(`test_named_atomic_closure_needs_no_done_child`, fixture only). Behaviour change: a closure over a dropped child without the
flag, accepted before PAT-313, is now refused, including the `close-epic` step of the
frozen DevHub coordinator (`campaign_runtime.py`), which passes no flag. The atomic receipt
binds each child by id, version and state only, the coordinates the server checks; the AC
of a child are checked on Foundry's read, before the call, and are not in the receipt
(PAT-316, `write.close_epic`). Before PAT-316 the receipt carried the AC counters, which
`DevHubTracker.close_epic` drops when it rebuilds the wire receipt, so a closure with a child
that had criteria failed before the POST (`invalid_response`; observed live on `1227841`).
Row DHN-24 of `devhub-requirements.md`.

## Project identity read-back (PAT-314, DHN-41)

`verify_project_identity(project)` makes one `GET /projects/resolve` on the project's
`canonical_repo` and returns `True` only when the server answers the same key, id and
canonical repository. A missing or non-canonical `canonical_repo` and a 404 return `False`
without guessing; any other error is raised. It is the read-back that the
`repository-bootstrap` journey of `tracker-contract.v1.json` asks for before a binding is
published. `registry bootstrap devhub` reaches it since PAT-323 (`devhub` is in `registry._V1_TRACKERS`).
Row DHN-41 of `devhub-requirements.md`. Since PAT-321 it also reads the project governance
mode once the identity holds: see "Governance mode".

## Governance mode (PAT-321)

Source: the stable contract `devhub-governance.v1` (`src/tracker/governance-contract.ts:6`
at DevHub `8028000`) and the refusal `assertFoundryGovernance` (`src/tracker/store.ts:1768-1771`
at `8028000`), both read in source, not run. DevHub documents the refusal in `TRACKER_API.md`
since DEVHUB-116 (`12ca158:docs/TRACKER_API.md:320-323`), which was not merged when this
section was written (PAT-321); that merge is what PAT-326 follows.

A project is in mode `manual` or `foundry`; DevHub refuses Foundry writes (proof-bound
transitions and the routes of this page) outside `foundry`, with 403 `authority_boundary`.
That code is shared with other authority refusals (for example a decided ADR, see
`link_adr_issue`), so the adapter never names a governance cause from the code alone.

- Identity read-back. `verify_project_identity` (`trackers/devhub.py`), after the identity
  checks succeed, reads `GET /api/tracker/v1/governance/projects/<KEY>` through `_req` with
  `response_contract` `devhub-governance.v1` (`_governance_mode`; scope `tracker:read`,
  readable whatever the mode, as stated by the DevHub agent on 2026-10-21, not observed). It
  accepts exactly `schema_version` `devhub-governance.v1`, `project` equal to the key and
  `mode` in `manual`, `foundry` (`_GOVERNANCE_MODES`); anything else is
  `invalid_response`. Mode `manual` raises `DevHubGovernanceModeError` (a
  `TrackerConflictError`) naming the project and the mode, not `False`, which stays an
  identity mismatch. A 404 `route_unavailable` (server without the route) keeps the previous
  behaviour: the mode is unknown and nothing is refused. Any other error is raised.
- Guard on writes. `_governance_guard` wraps `POST /issues/:id/state` (both calls of
  `set_state`), `/acceptance-override`, `/human-acceptance`, `POST /adrs/:id/status`,
  `POST /adrs/:id/supersede`, `POST /projects/:key/adrs/import` and the Epic closure
  `POST /projects/:key/epics/:id/closure`. On a 403 `authority_boundary` it re-reads the
  mode once: not `foundry` raises `DevHubGovernanceModeError`; `foundry`, an unknown mode or
  a failing read re-raises the original error. No governance read precedes a write. The
  `link_adr_issue` handling of the same code is unchanged. Since DEVHUB-110 (DevHub merge `5782d78`) the five proof-bound routes above, supersede
  included, refuse outside Foundry governance (`assertFoundryGovernance`,
  `8028000:src/tracker/store.ts:1768`, called at `:1782`, `:1864`, `:1914`, `:2357`,
  `:2393`; observed in source, not run). Since DEVHUB-116 (`12ca158`), `POST
  /projects/:key/adrs/import` and `POST /projects/:key/epics/:id/closure` are documented to
  refuse outside Foundry governance with 403 `authority_boundary` too
  (`12ca158:docs/TRACKER_API.md:320-323`, route table; read in source, not run); the adapter
  wraps both already. On the status route governance is checked before the
  `superseded` refusal (409), so a Manual project gets 403 first (see "Proof-bound ADR
  supersession").
- Callers. `registry.verify_existing_project` turns a `TrackerConflictError` of the
  read-back into a `ValueError` carrying its message (any other exception keeps the generic
  message), so a binding fails closed naming the mode; `registry bootstrap devhub` reaches
  this path since PAT-323. `doctor.registered_project_diagnostic` calls `verify_project_identity` only for
  `ghprojects`, so `doctor` does not read the governance mode of a DevHub project.

Tests: `tests/test_devhub_tracker.py`, section "PAT-321" (offline, scripted transport).
Observed on 2026-10-21 on a disposable instance built from DevHub `8028000` (never
production): the live suite passed (40 passed, 1 xfailed DHN-40, 1 skipped DHN-42), so the
identity read-back of DHN-41 read the governance route of a `foundry` project. A one-off
script then set that project to `manual` directly in the disposable database (`projects`
columns `governance_mode` and `governance_origin`; the human conversion route needs an
interactive session, `convertGovernance` in `src/tracker/governance-store.ts`):
`verify_project_identity` raised `DevHubGovernanceModeError` naming `manual`, and
`set_state` on an issue of that project, refused by DevHub with 403 `authority_boundary`,
raised the same error.

## Staged migration links (PAT-314, DHN-35)

`migration_link_issue(project, src, type, dst)` is the link primitive of a migration
target (`migration.copy_and_verify`). It refuses an archived project before any request
(`registry.require_writable_project`), checks both ids against the project, reads the
source and, only when the source does not already carry the link, reads the target and posts
the link (two reads and one POST, `_post_link`; `link` makes the same POST).
DevHub stores one relation and shows its inverse on the other end (`depends-on` on one
side, `blocks` on the other), so a replay, or the migration linking the other end, makes
no second write. The import itself is described in "Selective import" below (PAT-319);
the live test of PAT-314 links two issues created normally, by decision for PAT-314.
Row DHN-35 of `devhub-requirements.md`.

## Selective import (PAT-319, DHN-34, DHN-36)

Routes of DEVHUB-105, merged in DevHub as `b0231d9` (its page `docs/TRACKER_API.md`, section
"Selective import (DEVHUB-105)"). The adapter is `trackers/devhub.py`; the offline tests are in
`tests/test_devhub_tracker.py` (PAT-319 block). A `source_ref` is 1 to 200 characters, a
`foreign_id` 1 to 120, both without whitespace or control character
(`_ORIGIN_TEXT`, `_SOURCE_REF_MAX`, `_FOREIGN_ID_MAX`); an invalid one is a `ValueError` before any
request.

**Issues.** `migration_import_issue(project, snapshot, source_ref=...)` refuses an archived
project (`registry.require_writable_project`) and always sends one `POST
/projects/:key/issues` with the title, body and fields of the snapshot and `source_ref`; it does
no lookup first, so the server's content comparison is never bypassed. The fields sent are the
non-null `type`, `priority`, `estimate` and `state` of the snapshot attributes. DevHub answers
201 for a first import and 200 with the existing issue for a replay of the same content; a
different content, or a coordinate held by another issue, is 409 `resource_conflict`, which
becomes `TrackerConflictError`. Any other error is raised unchanged. The answer must carry the same
`source_ref` and body, the project's key and the trimmed title, otherwise `TrackerConflictError`.
The server accepts only `backlog` or `ready` as state at creation (409 `invalid_transition`,
raised unchanged). Relations are not sent: use `migration_link_issue`.
`migration_find_issue(project, source_ref)` reads `GET /projects/:key/issues?source_ref=`
(zero or one body-free item) then `GET /issues/:id`; a list that is not exactly the coordinate
asked (several items, a missing or different `source_ref`, another project, a truncated page)
is `DevHubTrackerError` `invalid_response`, which is also what a server that ignores the
filter produces.

**Origin number bound (DEVHUB-116).** An imported ADR keeps its `foreign_id` number as the
server number only up to 999999 (`MAX_ORIGIN_ADR_NUMBER`, `12ca158:src/tracker/store.ts:2232`, applied by `originAdrNumber`, `:2239-2247`;
observed in source, not run); beyond it `foreign_id` stays an alias and the number comes from
the server counter. The adapter sends the same body and reads the answer as before.

**ADRs.** `import_adr` (which `migration_import_adr` calls) sends one `POST
/projects/:key/adrs/import` with the strict body: `source_ref`, `foreign_id` (the snapshot id),
`title`, `body`, `body_sha256`, `status` and the optional `source_created`, `source_updated`,
`superseded_by`, `linked_issue_ids`. The sha-256 of the UTF-8 body is compared with
`expected_source_sha256` before any request (`TrackerConflictError`); the server checks it
again (422 `invalid_request`). The status must be one of `proposed`, `accepted`, `deprecated`,
`superseded`; any other (for example `rejected`) is a `ValueError`. `superseded_by` is the foreign
id of the replacement: the adapter looks it up (`?foreign_id=`) and sends its `source_ref`;
an unknown replacement is `TrackerConflictError` before the import is written, and the
parameter requires the status `superseded`. `issue_refs` become `linked_issue_ids` (issues of the
bound project). A non-empty `supersedes` raises
`TrackerCapabilityUnavailableError` before any request (`adr_import_supersedes`): DevHub stores the
edge from the replaced ADR, so it is written when that ADR is imported with its `superseded_by`.
`migration_import_adr`, which passes a known `supersedes` of the snapshot to `import_adr`, is
refused the same way; `migration_import_adrs` leaves it out only when every predecessor it names
is in the corpus and names this ADR as its `superseded_by`.
Source-only issue references have no DevHub carrier and raise
`TrackerCapabilityUnavailableError`. The call carries a scope `tracker:adr` credential and no
`X-Foundry-Proof` header (a deviation declared by DevHub). Replay is 200; a divergent content, or a
`foreign_id` held by another `source_ref`, is 409 `resource_conflict` (`TrackerConflictError`);
a 404 `route_unavailable` is `TrackerCapabilityUnavailableError` (`migration_adr_import`). The
answer must repeat the `source_ref`, `foreign_id`, status, body and source dates, otherwise
`TrackerConflictError`. The answer does not echo `superseded_by` or `linked_issue_ids`
(`publicAdr`, `b0231d9:src/tracker/store.ts`, observed in source; checked at `6721ad8:src/tracker/store.ts:374-386`):
the adapter cannot verify them from it, and the server compares them in the replay digest, so a
replay with other relations is 409. `import_adr` also reads the project listing before the write
when the foreign id has the form `<KEY>-ADR-NNNN` and refuses with `TrackerConflictError` one that
equals the server id of a native ADR of the project (one with no `foreign_id`), whose port id it
would duplicate; the server id of another imported ADR is accepted, since that ADR's port id is its
own foreign id (observed on a disposable instance at `6721ad8`, "Live run of PAT-315");
on a server carrying DEVHUB-111 (merge `16b6888`) an import as `<KEY>-ADR-NNNN` keeps that number
and the server refuses one that is taken (409 `resource_conflict`, `TrackerConflictError`); on an
older server a native creation later taking a number already used as a foreign id is not
prevented (`devhub-requirements.md`, row DHN-36). `migration_import_adrs` writes
each replacement before the ADR it replaces (the server refuses a replacement not yet imported)
and returns the ADRs in input order; a cycle is `TrackerConflictError`. Before its first write it
validates every snapshot locally (status, source-only issue references, `supersedes`, body digest,
shapes: `_prepare_adr_import`), so a corpus with one invalid ADR writes nothing.

**Reading.** `_to_adr` reads the additive keys `source_ref`, `foreign_id`, `source_created`,
`source_updated` (absent or null on an ADR created normally; a wrongly typed value is
`invalid_response`) and sets `Adr.id` to the foreign id when there is one; the server id
`<KEY>-ADR-NNNN` stays the route identity. `migration_find_adr(project, source_ref)` (or the
keyword `foreign_id=`, or both, combined by AND on the server) reads `GET
/projects/:key/adrs?source_ref=|foreign_id=` then `GET /adrs/:id`. `list_adrs` therefore indexes an
imported ADR by its foreign id; two ADRs with the same port id (a foreign id equal to a native id of
the project) make the listing fail closed (`pagination_repeated`). An ADR imported with a foreign
id that does not carry the project key (for example `FOUNDRY-ADR-0026`) is refused by the binding
check of the routes below (`_require_adr_binding`): it cannot change status, be linked or read for
relations through the adapter. The routes that act on an ADR
(`set_adr_status`, `link_adr_issue`, `read_adr_issue_relations`) refuse an id of another project
before any request, then resolve the server id from the project listing by port id
(`_adr_route_item`), never from `Adr.id`: a foreign id equal to the server id of another ADR of the
project (observed possible: the server checks a `foreign_id` for shape and uniqueness only,
`b0231d9:src/tracker/store.ts:2256-2259`) therefore reaches its own ADR, and an id absent from the
listing is `invalid_response`, nothing written. Cost: one listing before each of the three routes.

The whole-repository path (preflight, attributes, expected relations) is described in "Selective
cutover target" below. Rows DHN-34 and DHN-36 of `devhub-requirements.md`.

## Selective cutover target (PAT-324, DHN-43)

DevHub is a valid target of `cutover preflight|copy|activate devhub <target-config.json>
<manifest>` (FOUNDRY-ADR-0026; `tooling/foundry/cutover.py`, `tooling/foundry/migration.py`).
The target configuration is `{"key": "<KEY>", "id": "<project id>", "extra": {"canonical_repo":
"<host/owner/repo>"}}`: `canonical_repo` is the repository identity of the checkout being switched
and must be the one the DevHub project is registered under, since `migration_preflight` reads the
project back with `verify_project_identity`. The procedure below was run offline through
`capture_manifest` with a scripted transport (`test_capture_manifest_to_a_devhub_target_records_the_exceptions_and_the_profile`);
against a server, the live test of DHN-43 ran `capture_manifest`, `copy_and_verify` and `verify_targets` from a scripted source on a disposable instance at `12ca158` (observed, "Live run of PAT-324" in `devhub-requirements.md`); the `cutover` CLI commands and `activate` were not run against a server.

DevHub is a target for living work and for an ADR corpus whose relations it can record, not for every ADR corpus: the contract cell `cross-tracker-adr-import` stays a `gap` owned by PAT-327, because DevHub at `12ca158` has no carrier for source-only issue references nor for an `unknown` relation (observed in source; `devhub-requirements.md`, question 19) and the target must keep both (PAT-ADR-0008, PAT-ADR-0003). The preflight below refuses such a corpus, so the cutover fails closed. For this repository the impact is deduced, not run: the Linear adapter exports `unknown` for each family of an ADR's `missing_relations` (`trackers/linear.py`, `migration_export_adrs`), FOUNDRY-ADR-0026 declares missing relations, and `migration.capture_manifest` exports every ADR, so a preflight of this repository toward DevHub would be refused.

**`migration_preflight`** (`trackers/devhub.py`) writes nothing and returns the same value for the
same target: `{"kind": "devhub-source-ref-v1", "issue_coordinate": "source_ref", "adr_coordinate":
"source_ref+foreign_id", "creation_states": ["backlog", "ready"]}`. The value is part of the manifest
digest (`migration._source_view`), which `source_is_unchanged` compares again at activation, so it
holds no timestamp and no server value. It first decides locally, before its two reads, what the
server would refuse only after the first write (limits from `issueFieldsSchema`, `createIssueSchema`
and `importAdrSchema` of `12ca158:src/tracker/contract.ts`, and `createTrackerIssue`,
`createTrackerLink` of `12ca158:src/tracker/store.ts`, observed in source):

- the source coordinates: valid, unique (`TrackerConflictError`);
- issue title and body: the title is already trimmed and has 300 UTF-16 units at most, the body 200000
  at most (`_IMPORT_TITLE_MAX`, `_IMPORT_BODY_MAX`); a source title with surrounding space cannot be read
  back equal, because the server trims it;
- the living graph, over the issues of the manifest only: a link to oneself, an issue with several
  parents, a parent whose `type` is not exactly `Epic` (or whose `type` is excepted), a hierarchy cycle and
  a dependency cycle are refused (`createTrackerLink`: "parent must be an Epic", "issue already has a
  parent", "hierarchy cycle refused", "dependency cycle refused");
- the ADR corpus: a status DevHub lacks (`rejected`), source-only issue references (no carrier,
  `devhub-requirements.md`, question 19; PAT-327), a relation that is `unknown` at the source (DevHub reads back a known value, never `unknown`,
  PAT-ADR-0003 and PAT-ADR-0008), a replacement outside the corpus, a `superseded` status without a replacement or
  the reverse, a `supersedes` list that is not the exact reciprocal of the `superseded_by` links, a cycle,
  more than 50 issue relations (`_ADR_LINK_MAX`), and everything `_prepare_adr_import` refuses
  (body digest, shapes).

Then it calls `verify_project_identity` (one `GET /projects/resolve`, then one `GET /governance/projects/:key`):
a project that is not the exact key, id and repository, or that is outside Foundry governance
(`DevHubGovernanceModeError`), is refused. It does not probe whether the server carries DEVHUB-105
(an older server refuses the first import) nor the workflow catalogue (below); both are unknown at preflight.

**`migration_supported_attributes`** is the seven attributes of `migration._ISSUE_ATTRIBUTES`; the four
field values are sent by `migration_import_issue`, and `parent`, `children` and `dependencies` are the links
written by `migration_link_issue`. **`migration_attribute_exceptions`** names, per issue, each value DevHub
cannot reproduce, so the manifest records it and `copy_and_verify` neither sends nor compares it:
`priority` outside `P0` to `P3`, `estimate` that is not an integer from 1 to 21, `type` empty or longer than
80 UTF-16 units, and a `state` other than `backlog` or `ready` (the server creates an issue in those two
states only, `createTrackerIssue`: an imported `in-progress` or `review` issue is therefore created in
`backlog`, with the exception recorded). A published project workflow may disable a value
(`assertWorkflowAssignments`, 409 `value_disabled`, returned as `resource_conflict`): the service credential
has no read route for that catalogue, so such a value is refused at copy time and is unknown at preflight
(`devhub-requirements.md`, question 19). This is below what the other providers do: they name every
unavailable value as an exception in the manifest before the first effect, whereas DevHub refuses a
workflow-disabled value only at copy time, after partial writes. The live-work copy cell stays
`supported` with that named gap; question 19 is the follow-up.

**ADR readback.** `migration_prepare_adr` returns the snapshot with `supersedes` sorted and the issue relations
sorted by key then number, and `migration_export_adrs` returns the same order, because DevHub does not
document the order it reads relations back in (unknown) and `copy_and_verify` compares lists. Since PAT-324
`migration_export_adrs` also reads the issue relations of each ADR (`GET /adrs/:id/relationships`, one more
read per ADR, shared with `read_adr_issue_relations`): they are no longer `unknown`. `migration_qualify_adrs`
is the port default (`None`): the ADR codec needs no qualification, and `migration_import_adrs` refuses one.
Order of the copy: issues, their links, then the ADRs, each replacement before the ADR it replaces
("ADRs" above).

## Body replacement and ADR relations (PAT-312, DHN-21, DHN-32, DHN-33)

`update_body(issue, expected_body, updated_body)` reads the issue, raises
`TrackerConflictError` when its body differs from `expected_body`, returns `False` when
it already equals `updated_body`, and otherwise sends the body-only `PATCH` of
"Acceptance checkbox synchronization" with the version it read (`If-Match`) and an
idempotency key, then re-reads the issue and raises `DevHubTrackerError` (`invalid_response`) if the body is not the requested one; a 409 `version_conflict` becomes `TrackerConflictError`. An ADR body is
not written: `BodyUpdateUnavailableError`.

`link_adr_issue(adr, issue)` reads `GET /api/tracker/v1/adr-authoring/adrs/:id`
(`devhub-adr-authoring.v1`) and, for a `proposed` ADR, sends `PATCH` on the same path with
the complete `linked_issue_ids` (the edit route replaces the set) and the version it
read. It is a no-write when the relation exists, whatever the ADR status (the check precedes the
status check, as a replay on Linear). A decided ADR is frozen by the server
(403 `authority_boundary`, `2edcea2:src/tracker/store.ts:1966`): the adapter raises
`AdrRelationFrozenError` (a `TrackerConflictError`) before any write for a new relation. When
the server answers that 403 after the read, the adapter reads the ADR again and names a freeze
only if it is no longer `proposed`; otherwise the 403 propagates as `DevHubTrackerError`: the
code `authority_boundary` is not tied to a freeze alone (deduced; the contract lists it among the
ADR authoring errors, `6721ad8:src/tracker/adr-authoring-contract.ts:55`). The relation must be set before
acceptance. The CLI `adr link-issue <ADR-ID> <ISSUE-ID>` (`adr.py:166-173`, through
`write.link_adr_issue`) reaches this method, so it works on DevHub for a `proposed` ADR
(deduced from that code path, not run through the CLI).
`read_adr_issue_relations(adr)` reads `GET /api/tracker/v1/adrs/:id/relationships`
(`devhub-adr-relationships.v1`), also for a decided ADR. It requires the ADR id of the page
to be the requested one, a boolean `truncation`, a `returned_count` equal to the item count, no
repeated id, a numeric cursor that advances, and a null cursor on the last page, as the ADR
listing does.

`supersede_adr` is described in "Proof-bound ADR supersession" below (PAT-320).

## Proof-bound ADR supersession (PAT-320, DHN-32)

Source: DEVHUB-108, merge `6721ad8` (`6721ad8:docs/TRACKER_API.md`, "Proof-bound ADR supersession"; `supersedeTrackerAdr`, `6721ad8:src/tracker/store.ts:2350-2404`; observed in source; observed green on a disposable instance built from `6721ad8`: `devhub-requirements.md`, "Live run of PAT-320").

`supersede_adr(adr, replacement_id)` (`trackers/devhub.py`) resolves both port ids to their server item with one project listing (`_adr_route_items`, same rule as `_adr_route_item`). Before any write it raises `TrackerConflictError` when the two are the same ADR, when the source is neither `accepted` nor `superseded`, or when it is `accepted` and the replacement is not. A source already `superseded` returns without a write when `GET /adr-decisions/adrs/:id` already names that replacement as `superseded_by`, whatever the replacement's status now is (checked before the `accepted` test of the replacement), and is a `TrackerConflictError` otherwise. It then sends `POST /api/tracker/v1/adrs/:id/supersede` with the body `replacement` and `replacement_version` (server ids, versions read in the listing), the source version (`X-DevHub-Version` and `If-Match`), an idempotency key and `X-Foundry-Proof`: action `adr-supersede`, `target_status` `superseded`, `resource_id` and `expected_version` of the source, plus `replacement_id` and `replacement_version` (`_proof`, `extra`; fields `6721ad8:src/tracker/contract.ts:300-302`, checked by `consumeProof`, `store.ts:1751`). The proof payload is sorted JSON, base64url, signed by HMAC-SHA256 as for the other actions; the server decodes it as JSON and validates it with a strict schema (`verifyProof`, `6721ad8:src/tracker/contract.ts:408-442`), so key order is not part of the contract. A 409 `version_conflict`, `invalid_transition` or `resource_conflict` becomes `TrackerConflictError`, a 404 `route_unavailable` (server without DEVHUB-108) becomes `TrackerCapabilityUnavailableError` (`adr_supersession`), any other error propagates. After the POST the adapter reads `GET /adr-decisions/adrs/:id` (`devhub-adr-decisions.v1`, scope `tracker:read`) and raises `DevHubTrackerError` (`invalid_response`) when `relations.superseded_by` is not the replacement. The status route (`POST /adrs/:id/status` with `superseded`) is never used: up to `8028000` it created an unlinked supersession (DEVHUB-87); since `12ca158` it refuses `superseded` (below).

`migration_export_adrs(project)` reads one project listing, then the same document for each ADR, and returns `superseded_by` and `supersedes` (the latter from `supersedes_all` when present, else from `supersedes`, as port ids: the foreign id for an imported ADR). A non-superseded ADR without a link reports `superseded_by: None`; a `superseded` ADR with no recorded link (set through the status route, possible up to `8028000`; refused since `12ca158`) keeps `unknown` (PAT-ADR-0003). While the project holds such an ADR other than the entry itself, its replacement is unknown and could be any ADR, so `supersedes` of the entry is `unknown`, not `[]` and not a partial list (PAT-ADR-0008); otherwise it is the list read. `issues` is read from `GET /adrs/:id/relationships` (PAT-324, "Selective cutover target"); `supersedes` and `issues` are sorted. N to 1 reads back as several entries in `supersedes_all` of the replacement (`6721ad8:src/tracker/adr-decisions-store.ts:20-22`).

`DevHubTracker.set_adr_status(adr, "superseded")` raises `ValueError` before any request, naming `supersede_adr` (`adr supersede`) (PAT-326): since DEVHUB-116 the status route answers 409 `invalid_transition` to `superseded` and supersedes nothing (`12ca158:src/tracker/store.ts:2365`, `12ca158:docs/TRACKER_API.md`, route table; observed in source, run by DHN-31). The server checks governance first, so a Manual project gets 403 `authority_boundary`, not the 409 (`12ca158:src/tracker/store.ts:2363-2365`, observed in source, not run). The ADR id parsing of the adapter (`_adr_project_key`: `<KEY>-ADR-` then at least four digits) is unaffected: a number above 999999 is numbered by the server counter and still has at least four digits.

Limits: at `6721ad8` the route did not check the project governance mode; since DEVHUB-110 (`5782d78`) it refuses outside Foundry governance with 403 `authority_boundary` (`8028000:src/tracker/store.ts:2393`, observed in source), reported by the adapter as described in "Governance mode"; the id of an ADR imported with a foreign id that is not shaped `<KEY>-ADR-NNNN` is refused by the binding check before any request, as for the other ADR methods (`_require_adr_binding`).

## Acceptance checkbox synchronization

When a current, mergeable structured review proof covers every acceptance criterion,
`issue merge` may synchronize the tracker before the code-host mutation. The projection
is deliberately narrow: it preserves the issue body byte-for-byte except for changing
the Markdown marker of an unchecked criterion from `[ ]` to `[x]`, and only when that
criterion's structured verdict is `pass`. It never derives criteria from reviewer prose,
adds comments as criteria, or modifies unrelated fields.

Dev Hub implements this port with a fresh issue read followed by a body-only `PATCH`
using the exact returned version (`If-Match`) and an idempotency key. A concurrent body
edit or `version_conflict` fails before merge with a reload-and-review recovery message;
an already-applied body is a no-op. The deterministic idempotency key
`foundry-ac-<proof-id>` binds Dev Hub's transactional operation receipt and normal
`issue.update` audit to the durable structured proof. The separate human-facing merge
comment is best-effort: its failure cannot erase that provider receipt or force a retry
which would skip the correlation. The final merge output reports both `AC sync` and an
unavailable convenience note separately from the CI verdict.

YouTrack is explicitly unsupported for automatic acceptance synchronization. Its public
issue update endpoint accepts description changes but documents no atomic expected-body
or expected-version precondition. Foundry therefore does not emulate safety with a
read-then-write race: the merge stops and asks for manual checkbox completion. This is a
provider capability limitation, not a change to routing, review, CI, or merge authority.

## Non-code Epic closure capability

Foundry exposes a separate `issue close-epic <EPIC-ID>` command and Tracker port for
closing an Epic whose own AC are complete (or absent) and whose complete required-child
set is terminal. The receipt binds the registered project, parent id/version/type/AC,
the canonical child id/version/state set, an issuance timestamp, and a nonce. A provider
must lock and compare that complete graph, change the parent to `done`, and persist a
replayable audit in one transaction. The ordinary proof-bound code-issue `done`
transition remains exclusively tied to the exact reviewed and merged PR receipt.

Dev Hub exposes that operation at
`POST /api/tracker/v1/projects/:project/epics/:epic/closure`. Foundry sends the exact
`devhub-epic-closure.v1` receipt with the open parent version in `If-Match` and a
deterministic idempotency key. Dev Hub locks and compares the parent plus all direct
children, closes the parent once, and returns the original receipt, closed parent
version and audit id. Between 1 and 100 canonical direct children are supported; the
client never truncates the graph. An exact retry returns the same audit and receipt
with `replayed: true`, while stale versions, changed graphs, changed AC, cross-project
ids and divergent replays fail closed.

`GET` on the same route is the read-only recovery path. It returns only that original
closure receipt and never consults command events, terminal reconciliation or the
separate DEVHUB-32 terminal-receipt projection. Only the exact typed
`404 epic_closure_unavailable` means no receipt exists; a wrong contract, malformed
payload or any other provider error is surfaced. A Dev Hub server predating this
contract therefore remains explicitly unsupported rather than falling back to a
read-then-write emulation. YouTrack is likewise unavailable because its public
contract cannot atomically compare the parent plus the complete versioned child set.

The remote branch is deleted only after the `done` receipt is durable, so a tracker
outage after a successful code-host merge remains recoverable. On retry, Foundry reads
GitHub's existing merge receipt SHA and finishes the tracker transition without issuing
a second merge. If the PR head changed during remediation, the mechanical merge step
refreshes the `review` receipt on the exact head/diff it is merging; with
`append_only_lifecycle_supported` it republishes that generation before the gates that
follow, not after them (`issue.py:676-680`).
Before that gate, rerunning `issue openpr` reuses the one open PR for the exact branch and
base, preserves its body unless a non-empty `--summary-stdin` is explicitly supplied, and
emits a fresh proof-bound `review` transition. It
refuses a closed, wrong-base, or ambiguous candidate instead of creating a duplicate.
If the issue is already `done`, a retry accepts cleanup only when GitHub reports that exact
PR URL as merged with a merge SHA; it neither rewinds the immutable tracker state nor calls
the merge endpoint again.

## Destructive smoke

The live conformance suite against a disposable instance, with the list of Foundry's needs towards
DevHub, is described in `docs/devhub-requirements.md` (PAT-296); the smoke below is a separate, older check.

The real-provider smoke is skipped unless all coordinates and the exact confirmation
are supplied. It creates persistent disposable project/issue/ADR rows; DevHub v1 has no
delete endpoint. Run it only against a disposable database or with a unique ticker:

```bash
export DEVHUB_SMOKE_CONFIRM=I_UNDERSTAND_DEVHUB_SMOKE_CREATES_DATA
export DEVHUB_SMOKE_PROJECT_KEY=SMK123
export DEVHUB_SMOKE_REPOSITORY=github.com/foundry-smoke/smk123
python3 tooling/foundry_cli.py devhub-smoke \
  --key "$DEVHUB_SMOKE_PROJECT_KEY" --repository "$DEVHUB_SMOKE_REPOSITORY"
```

The smoke checks the full public lifecycle and reads, round-trips a link and a comment,
requires `409 version_conflict` for a stale `If-Match`, requires divergent idempotency
replay to fail, and races two writes at one version so exactly one wins. It then reads
`GET /projects/:key/audit` through the configured transport and fails unless its own issue
and ADR receipts are present in the sanitized public projection.

## Terminal campaign reconciliation

The normal command worker publishes a typed `completed` event only after the ordinary
F102/F83 path has produced a real original Epic closure outcome. Its
`epic_closure` receipt carries the audit id, closed parent version, canonical child
versions/states and a digest over those coordinates. The command lifecycle and the
passive execution-receipt journal are separate append-only surfaces: publishing one
does not manufacture or replace the other. Their bounded child collections both
accept 1 through 100 entries, matching the original closure contract.

For a native campaign receipt carrying an `attempt_id`, the terminal provider reuses
the F102 verification path to re-read the original DevHub closure and bind its exact
coordinates to the `completed` event. F91 is populated best-effort as a separate
passive observation: its absence, corruption or write outage neither downgrades nor
delays that event. If the original closure endpoint itself is temporarily unavailable,
the completed provider effect stays durable and the worker leaves the command
non-terminal without launching or resuming an agent and without another close or
merge. A later scan can retry publication while the same lease remains live.

Once the lease expires, DevHub's ordinary claim path still revalidates the pre-close
preview and can refuse the claim as `digest_conflict`; this client never bypasses that
authority. Before attempting any claim, the native campaign provider may use the
dedicated `late-terminal-publications` contract only when durable campaign evidence
re-proves the exact original F83 closure. That contract atomically appends the missing
typed `completed` fact and terminal reconciliation; it never claims or renews a lease,
runs or resumes an agent, closes or merges anything, or relaxes a budget. Missing or
contradictory original proof defers publication and is never reconstructed from parent
`done` state, F91 or a projection. Only a genuine older campaign receipt with no
attempt coordinate retains the legacy terminal event shape. Other command providers
keep their existing behavior.

`campaign-terminal` remains the evidence-only FOUNDRY-103 bridge to DevHub's
`POST /commands/:id/terminal-reconciliations` for the historical F89 recovery shape.
It is not a claim, resume or closure path. The command accepts the exact campaign
directory, F102 acceptance mapping and id of an already-persisted extended `completed`
event:

```bash
python3 tooling/foundry_cli.py campaign-terminal \
  --campaign-dir /absolute/path/to/command_559b71e5-b2b9-4086-ae96-f9ccf650d2e8 \
  --mapping /absolute/path/to/f89-mapping.json \
  --actor operator-f103 \
  --terminal-event-id event-terminal-f89
```

Before its only remote mutation it re-runs F102's no-follow ledger verification, checks
all three immutable merge receipts, binds every frozen command input, rereads the
current parent, AC, direct-child graph and child versions/states, verifies the provider
closure audit, and matches the complete closure coordinate and digest in the terminal
event. Missing or changed evidence fails closed. The deterministic reconciliation id is
also the `Idempotency-Key`, so an exact replay returns the one append-only receipt while
a divergent payload conflicts. The response parser requires `provider_effects: 0` and
keeps unavailable `cost_cents`/`duration_ms` as `null`; it never estimates economics.

`DevHubTracker.get_epic_closure()` consumes only the authenticated, project-bound
`GET /projects/:project/epics/:epic/closure` reader. It requires the exact
`devhub-epic-closure.v1` header and response, then independently revalidates the
project, parent transition, original nonce/date, complete ordered child set and audit
coordinate before returning the normalized `EpicClosureOutcome`. A typed absence
returns no outcome; malformed, cross-project, stale or conflicting responses fail
closed. In particular, the DEVHUB-32 terminal-receipt projection is never accepted as
an original closure proof.

The checked-in host smoke exercises the public adapters against a disposable loopback
Dev Hub database. Both modes require 1 through 100 terminal direct children and one
`requested` command bound to the open Epic version. In nominal mode, leave
`DEVHUB_CLOSURE_SMOKE_LATE` unset and prepare an open Epic whose AC are complete (or
absent). In late mode, set `DEVHUB_CLOSURE_SMOKE_LATE=1` and instead prepare an open
Epic with exactly one literal unchecked marker `- [ ] smoke acceptance`; every other AC
must already be complete, and the command must be bound to the Epic version before that
marker is synchronized. Zero or multiple exact markers fail before the acceptance
mutation. Supply the public project id (a decimal string), fresh closure
timestamp/nonce, and a worker/attempt coordinate. The smoke claims that fixture command,
appends `accepted`/`running`, closes the Epic through `DevHubTracker`, reads and replays
the same original receipt through a fresh client, rejects a contradictory receipt,
publishes the typed terminal event and reconciles it twice:

```bash
export DEVHUB_CLOSURE_SMOKE_CONFIRM=I_UNDERSTAND_CLOSURE_SMOKE_MUTATES_DISPOSABLE_LOOPBACK_DATA
export DEVHUB_CLOSURE_SMOKE_URL=http://127.0.0.1:3119
export DEVHUB_CLOSURE_SMOKE_TRACKER_TOKEN=...          # tracker transition/read scope
export DEVHUB_CLOSURE_SMOKE_PROOF_SECRET=...
export DEVHUB_CLOSURE_SMOKE_COMMAND_TOKEN=...          # command claim/event scope
export DEVHUB_CLOSURE_SMOKE_PROJECT_KEY=SMK123
export DEVHUB_CLOSURE_SMOKE_PROJECT_ID=123
export DEVHUB_CLOSURE_SMOKE_EPIC_ID=SMK123-1
export DEVHUB_CLOSURE_SMOKE_ISSUED_AT=1788566400000
export DEVHUB_CLOSURE_SMOKE_NONCE=closure_smoke_nonce_001
export DEVHUB_CLOSURE_SMOKE_COMMAND_ID=command-id
export DEVHUB_CLOSURE_SMOKE_WORKER_ID=worker-f103
export DEVHUB_CLOSURE_SMOKE_ATTEMPT_ID=attempt-f103
pytest -q plugins/foundry/tests/integration/test_real_terminal_reconciliation_smoke.py
```

For the late fixture only, add the mode flag before running that same command:

```bash
export DEVHUB_CLOSURE_SMOKE_LATE=1
```

Only a numeric HTTP loopback URL is accepted, and without the exact confirmation the
test is skipped. The fixture setup may create disposable rows through Dev Hub's public
API, but the parent must remain open: the closure under test is performed only through
the real adapter, never SQL or a synthetic `ClosureTracker`. Neither the terminal
reconciliation nor the smoke invokes a provider/agent, opens a PR, merges, overrides a
gate, rewrites policy/snapshots, or reserves budget. Unavailable cost and duration stay
`null` rather than being invented as zero.

The preserved F89E2E historical fixture has no original closure receipt and no typed
completed event. It therefore remains incomplete and is not a release gate or a
successful reconciliation example. `campaign-terminal` refuses it before remote
mutation instead of deriving an original proof from its DEVHUB-32 projection.
