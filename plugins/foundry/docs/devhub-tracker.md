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
  `human_acceptance` receipt of the same digest converges without a request only when the issue
  is `done` and that receipt passes `_valid_human_decision` (the only receipt, still matching
  the body); otherwise, or with another digest, the replay is refused. Otherwise it sends
  `POST /issues/:id/human-acceptance` with the body `decision` and a proof of action
  `human-acceptance` over `project_key`, `resource_id`, `expected_version` and `decision_digest` (`acceptance_decision.decision_digest`), then
  requires the issue `done` and one receipt whose object equals the decision. Since PAT-330
  (PAT-ADR-0033, which amends the "Fournisseurs" paragraph of PAT-ADR-0025) the command
  `issue accept` and its `--status` run on DevHub with the conditions of PAT-ADR-0025:
  `human_acceptance_supported` is `True`, `human_acceptance_predecessors` is
  `acceptance_decision.FROM_STATES`, and `write.validate_acceptance_request` refuses before any
  read exactly as for Linear. The returned issue carries `acceptance_source`
  `devhub-human-acceptance` (`human_acceptance_source`) and the decision digest in
  `acceptance_coordinates`. `--status` reads through `observe_human_acceptance`: the issue and
  its receipts (`read_proof_receipts`); exactly one `human_acceptance` receipt gives `accepté`
  when the issue is `done` (and `reçu écrit, état non projeté` otherwise, whose hint on DevHub
  says that any replay without `done` is refused, `_accept_status` in `issue.py`), none gives
  `aucune acceptation humaine`, and a DevHub error on the receipt read gives `inconnu`, as do
  two `human_acceptance` receipts, a decision next to any other receipt (`review`, `done`,
  `acceptance_override`) and a body or criteria changed after the decision
  (`DevHubTracker._valid_human_decision`, `acceptance_decision.check_against_body`; the
  conflicts of `linear-tracker.md`, "Reading").
  `get_issue` and `observe_issue` are unchanged: they do not expose the human acceptance
  (offline: `tests/test_devhub_tracker.py`, PAT-330; live: DHN-48, green at `3f0cf4b`,
  `devhub-requirements.md`, "Live run of PAT-330").
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
- Human decision in the release scope and the Epic closure (PAT-330, PAT-ADR-0025):
  `read_release_scope` (`_release_issue`) reads a `done` issue with no PR (`pr_url` empty), at least one criterion and
  whose only receipt is one `human_acceptance` receipt, still matching the body, as `accepted`, with the references `acceptance_decision_digest`
  (the key Linear emits) and `proof_receipt_id`. For the atomic Epic closure,
  `write.close_epic` reads each direct child through `observe_epic_closure_child` (default:
  `get_issue`); DevHub adds one receipt read only for a `done` child with no `pr_url` whose criteria are not all
  checked (a child with a PR is returned unchanged, as `_release_issue` does not take a decision for it either, PAT-325), and a single `human_acceptance` receipt (the only receipt, still matching the body, else
  the issue is returned unchanged) then marks it accepted
  (`acceptance_source` `devhub-human-acceptance`) and counts its criteria as satisfied, as the
  decision passed each one. The server does not check the boxes on a human acceptance: at `3f0cf4b`
  `recordHumanAcceptance` updates the state, `completion_kind` and version, not the body
  (`src/tracker/store.ts`; deduced from source, DHN-48 asserts only the counts). The atomic closure receipt binds id, version and
  state only (PAT-316), so it carries no acceptance source: a limit against the condition of
  PAT-ADR-0025 ("Lecture") that the Epic closure audit binds the acceptance source, which on
  DevHub only Foundry's read before the call checks. Still not done: a `done` issue
  merged under an override (`acceptance_override` receipt) stays `unavailable` in a release
  scope. A `done` issue whose PR-bound `review` and `done` receipts match but that also carries a
  `human_acceptance` receipt is `unavailable` in a release scope, as Linear and `_valid_human_decision`
  read that mix as a conflict (PAT-325). The whole closure, a child accepted by decision included, runs
  offline through the real `write.close_epic` and `DevHubTracker.close_epic` with only HTTP faked
  (`test_epic_closure_end_to_end_over_a_child_accepted_by_a_human_decision`); no live test closes an Epic over
  such a child. Decision of PAT-325: `observe_issue` keeps not exposing the acceptance (one more receipt
  read per issue), the two limits above being the ones PAT-ADR-0033 "Conséquences" accepts.
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

## Guarded `dropped` and `backlog` transitions (PLP-332)

`edit transition <ID> dropped|backlog <expected-state>` builds a `TransitionContext` that carries
only `expected_state` (`edit.py::transition`). `DevHubTracker.set_state` (`trackers/devhub.py`,
`_EXPECTED_STATE_TARGETS`) then reads the issue once (`_bound_issue_raw`) and sends the
`POST /issues/:id/state` only when the state read equals the expected state; otherwise it raises
`TrackerConflictError` naming both, with no write (`DevHub dropped refused: native state is
in-progress, expected ready`). Before PLP-332 any such context was refused with `preuve de transition
inattendue`. This is bounded detection, not a CAS (PAT-ADR-0006): an edit landing between the read
and the write is not detected by this check. The write carries the version read (`version=` in
`set_state`); whether the server refuses a stale version on this route is not observed here, so
the window is stated as not closed. Without an expected state the command writes unguarded, as
before. Any other context stays refused with `ValueError` naming the state and the cause: a proof
field (`pr_url`, `head_sha`, `base_sha`, `review_digest`, `merge_sha`) on `dropped`/`backlog`, or any
context on `ready`, `in-progress` or `blocked`, or a context without `expected_state` on `dropped`/`backlog`. `review` and `done`
are unchanged. Offline tests: `tests/test_devhub_tracker.py` (`test_expected_state_guards_a_dropped_or_backlog_transition`,
`test_divergent_state_refuses_a_dropped_or_backlog_transition_without_write`,
`test_no_expected_state_still_writes_dropped_unguarded`,
`test_any_other_transition_context_is_refused_with_its_cause`); no live test.

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
since DEVHUB-116 (`12ca158:docs/TRACKER_API.md`, route table rows of the Epic closure and of the import, lines 320 and 322), which was not merged when this
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
  (`12ca158:docs/TRACKER_API.md`, route table rows at lines 320 and 322; read in source, not run); the adapter
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
`foreign_id` 1 to 120, both without whitespace, control character or unpaired surrogate
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
`superseded_by`, `linked_issue_ids`, and, since DEVHUB-122 (merge `3f0cf4b98e60a04154cf051e4110cbf29094d381`,
"Source-only references and unknown relations" below), `source_only_refs` and `unknown_relations`. The sha-256 of the UTF-8 body is compared with
`expected_source_sha256` before any request (`TrackerConflictError`); the server checks it
again (422 `invalid_request`). The status must be one of `proposed`, `accepted`, `deprecated`,
`superseded` or `rejected` (`_ADR_IMPORT_STATES`, `_prepare_adr_import`); any other is a `ValueError`.
`rejected` is historical import/read vocabulary only: `_to_adr` reads it, while `create_adr`
still requires `proposed` and `set_adr_status` keeps `_ADR_STATES` unchanged (PLP-311). `superseded_by` is the foreign
id of the replacement: the adapter looks it up (`?foreign_id=`) and sends its `source_ref`;
an unknown replacement is `TrackerConflictError` before the import is written, and the
parameter requires the status `superseded`. `issue_refs` become `linked_issue_ids` (issues of the
bound project). A non-empty `supersedes` raises
`TrackerCapabilityUnavailableError` before any request (`adr_import_supersedes`): DevHub stores the
edge from the replaced ADR, so it is written when that ADR is imported with its `superseded_by`.
`migration_import_adr`, which passes a known `supersedes` of the snapshot to `import_adr`, is
refused the same way; `migration_import_adrs` leaves it out only when every predecessor it names
is in the corpus and names this ADR as its `superseded_by`.
Source-only issue references and unknown relation families are carried by DEVHUB-122
("Source-only references and unknown relations" below). The call carries a scope `tracker:adr` credential and no
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
validates every snapshot locally (status, source-only references, declared unknown families,
`supersedes`, body digest, shapes: `_prepare_adr_import`), so a corpus with one invalid ADR writes nothing.

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

### Source-only references and unknown relations (PAT-327, DEVHUB-122)

Source: DEVHUB-122, merge `3f0cf4b98e60a04154cf051e4110cbf29094d381` (`docs/TRACKER_API.md`, "ADR import" > "Source-only
references and unknown relations"; `importAdrSchema`, `MAX_ADR_SOURCE_ONLY_REFS`, `ADR_RELATION_FAMILIES` of
`3f0cf4b:src/tracker/contract.ts:94-134`; `publicAdr`, `3f0cf4b:src/tracker/store.ts:377-391`; observed in source, and green live
in DHN-46 at `3f0cf4b`, "Live run of PAT-327" of `devhub-requirements.md`). The DevHub agent reported production at
`a314c0c`, without DEVHUB-122 (relayed, not verified here), so an older server is possible (below).

- `import_adr` takes `source_only_issue_refs` and `unknown_relations`. It sends them as `source_only_refs` and
  `unknown_relations`, each only when non-empty (an empty list is the same content on the server, and omitting the key keeps
  the request hash and replay digest of an older client). The references are opaque and kept in the order sent, byte
  for byte; `migration_export_adrs` returns them as `source_only_issue_refs` in the order stored (key absent when none).
- `_snapshot_import_arguments` declares a family unknown when the snapshot's relation is exactly `unknown`, in the order
  `issues`, `supersedes`, `superseded_by`. An unknown family sends no value for itself (`supersedes` has no field on a
  single import, `superseded_by` and `issue_refs` are left out).
- Refused locally, before any request, what the server answers 400 `invalid_request` to (`_prepare_adr_import`, so also
  by `migration_preflight` and before the first write of `migration_import_adrs`): more than 200 references (`_ADR_SOURCE_ONLY_MAX`),
  a repeated one, one the origin text rule refuses (1 to 200 UTF-16 units, no whitespace, control character or unpaired
  surrogate: `_ORIGIN_TEXT`, `_SOURCE_REF_MAX`), a family named twice or other than the three of `_ADR_UNKNOWN_FAMILIES`, `issues` declared
  unknown beside a non-empty `issue_refs` or `source_only_issue_refs`, and `superseded_by` declared unknown beside a value.
  `_ORIGIN_TEXT` also refuses an unpaired surrogate in a `source_ref` or `foreign_id` since DEVHUB-122.
- The answer must carry both fields exactly as sent, an absent key counting as empty; otherwise `TrackerConflictError`.
- A server older than DEVHUB-122 answers 400 to a request that carries either key (strict schema, deduced from the
  `.strict()` of `importAdrSchema` at `12ca158`): the preflight does not probe it (unknown at preflight), the first such
  import fails with that `DevHubTrackerError`.
- A `superseded` ADR whose replacement is `unknown` is accepted by the preflight and imported as `superseded` without `superseded_by` (`unknown_relations` names `superseded_by`). That the server accepts it (201) was relayed by the DevHub agent on 2026-10-22 and deduced from its code at `3f0cf4b`, with no dedicated server test; Foundry's own proof is the offline test `test_migration_preflight_accepts_source_only_references_and_unknown_relations` (`tests/test_devhub_tracker.py`, which has such an ADR) and the last part of the live test of DHN-46, added by PAT-325 and observed green at `3f0cf4b` (`devhub-requirements.md`, "Live run of PAT-325").
- Limit: `unknown_relations` is a declaration fixed at import time. DevHub does not keep it consistent with later writes (a later
  import naming the ADR in `superseded_by`, `supersede_adr`, a revision of the `linked_issue_ids` of a `proposed`
  imported ADR: deduced by DevHub from its code, not tested). `migration_export_adrs` exports a declared family as `unknown`
  whatever it holds now and does not read its values, so the readback of a cutover compares the declaration, not
  the later state.

The whole-repository path (preflight, attributes, expected relations) is described in "Selective
cutover target" below. Rows DHN-34 and DHN-36 of `devhub-requirements.md`.

## Selective cutover target (PAT-324, DHN-43)

DevHub is a qualified target of `cutover preflight|copy devhub <target-config.json>
<manifest>` (FOUNDRY-ADR-0026; `tooling/foundry/cutover.py`, `tooling/foundry/migration.py`).
`cutover activate devhub` publishes a registry entry staged with `canonical_repo`
(`registry.cutover_repository_tracker`, `registry.py`) and replaces the repository marker. It is qualified offline
since PAT-329: `test_activate_toward_devhub_publishes_the_registry_entry_and_the_marker` runs `preflight`, `copy`
and `activate` through `cutover.main` with `FOUNDRY_DATA` isolated and checks the published entry
(`{"key", "id", "canonical_repo"}` under `devhub`), the archived source, the marker and the manifest phase; and
`test_activate_toward_devhub_refuses_a_divergent_manifest_and_publishes_nothing` checks that a manifest for another
project or tracker, a manifest not `verified` and an edited manifest are refused with the registry unchanged
(`tests/test_tracker_migration.py`; the target adapter there is a fake transport named `devhub`, so the offline test
does not exercise `verify_project_identity`). Its live proof is DHN-47 (`tests/integration/test_devhub_live_conformance.py`,
`devhub-requirements.md`): green at DevHub `3f0cf4b` ("Live run of PAT-329"). Both the offline test and DHN-47 replace `cutover._target`; the real construction `foundry.tracker("devhub", cwd="/")` is covered separately, offline, by `test_cutover_target_is_constructed_outside_the_checkout`.
The target configuration is `{"key": "<KEY>", "id": "<project id>", "extra": {"canonical_repo":
"<host/owner/repo>"}}`: `canonical_repo` is the repository identity of the checkout being switched
and must be the one the DevHub project is registered under, since `migration_preflight` reads the
project back with `verify_project_identity`. The procedure below was run offline through
`capture_manifest` with a scripted transport (`test_capture_manifest_to_a_devhub_target_records_the_exceptions_and_the_profile`);
against a server, the live test of DHN-43 ran `capture_manifest`, `copy_and_verify` and `verify_targets` from a scripted source on a disposable instance at `12ca158` (observed, "Live run of PAT-324" in `devhub-requirements.md`); the `cutover` CLI commands and `activate` were not run against a server in that run (DHN-47 of PAT-329 runs them, status in "Live run of PAT-329" of `devhub-requirements.md`).

DevHub is a target for living work and for an ADR corpus, source-only issue references and `unknown` relation families included, since DEVHUB-122 (`3f0cf4b`) and PAT-327 ("Source-only references and unknown relations" above): the contract cell `cross-tracker-adr-import` is `supported` (`tracker-contract.v1.json`), its live proof is DHN-46 (`devhub-requirements.md`, "Live run of PAT-327"). Two corpora stay refused by the preflight for two different reasons: a `supersedes` that is known while a `superseded` ADR of the corpus has an unknown replacement is refused because no readback of it could match (the export gives `unknown` to every other `supersedes`, PAT-ADR-0008), whereas `issues` declared unknown beside source-only references is refused because the server refuses that combination (it is not a readback matter). For this repository the impact is deduced, not run: the Linear adapter exports `unknown` for each family of an ADR's `missing_relations` (`trackers/linear.py`, `migration_export_adrs`), FOUNDRY-ADR-0026 declares missing relations, and `migration.capture_manifest` exports every ADR; the DevHub preflight no longer refuses those families, but a preflight of this repository toward DevHub has not been run.

**`migration_preflight`** (`trackers/devhub.py`) writes nothing and returns the same value for the
same target; without number preservation ("Explicit source issue numbers" below), its profile is: `{"kind": "devhub-source-ref-v1", "issue_coordinate": "source_ref", "adr_coordinate":
"source_ref+foreign_id", "creation_states": ["backlog", "ready"]}`. The value is part of the manifest
digest (`migration._source_view`), which `source_is_unchanged` compares again at activation, so it
holds no timestamp and no server value. It first decides locally, before its three reads, what the
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
- the ADR corpus: a status outside `_ADR_IMPORT_STATES`, a source-only reference list or an unknown-family declaration the
  server would refuse (PAT-327, "Source-only references and unknown relations" above), a known `supersedes` while a
  `superseded` ADR of the corpus has an unknown replacement (PAT-ADR-0008), a replacement outside the corpus, a `superseded` status with a replacement known to be absent (`None`; one whose replacement is `unknown` is accepted, `_preflight_adrs`) or
  the reverse (a known replacement on an ADR whose status is not `superseded`), a `supersedes` list that is not the exact reciprocal of the `superseded_by` links, a cycle,
  more than 50 issue relations (`_ADR_LINK_MAX`), and everything `_prepare_adr_import` refuses
  (body digest, shapes);
- an ADR whose replacement (`superseded_by`) is `proposed` or `rejected` in the corpus
  (`_preflight_adrs`, DevHub `src/tracker/store.ts::importTrackerAdr`): DevHub refuses it with 409
  `invalid_transition` (observed in source; the `proposed` refusal was qualified by DEVHUB-118
  at `cd23304`, while the historical `rejected` extension awaits DHN-50), and the refusal
  names the ADR; an `accepted`, `superseded` or `deprecated` replacement is accepted (PAT-328).

Then it calls `verify_project_identity` (one `GET /projects/resolve`, then one `GET /governance/projects/:key`):
a project that is not the exact key, id and repository, or that is outside Foundry governance
(`DevHubGovernanceModeError`), is refused. Third read (PAT-328): `GET /api/tracker/workflow/v1/projects/:key`
(`devhub-workflow.v1`, `_read_workflow`, `_preflight_workflow` of `trackers/devhub.py`; the route, its shape and the
import rule are DevHub's, read in source at `cd23304` and relayed by the DevHub agent, `devhub-requirements.md`
question 19). The adapter validates the answer fail closed (unknown `schema_version`, other project, malformed
lists: refusal naming `invalid_response`), and a refused read (403, 404, unavailable) is a refusal naming the cause.
It then copies the import rule: while `version` is 1 (never published) nothing is checked, because the server checks
nothing; above 1 every issue of the manifest must have each of `type`, `priority` and `estimate` strictly equal
(case-sensitive, no trim, `estimate` an integer) to an `enabled` entry of the field, an omitted or excepted
attribute counting as `null` (which needs an enabled `null` entry), and its creation state (`state` if it is
`backlog` or `ready`, otherwise `backlog`) must be the one status of that canonical state, enabled. Exactly one is counted among all the statuses of the workflow, enabled or not: a canonical state with one enabled and one disabled status is refused too, because the server counts both (`resolveStatus` of `src/tracker/workflow-settings.ts` at `3f0cf4b`, read in source, not run; `test_migration_preflight_refuses_a_creation_state_with_one_enabled_and_one_disabled_status`). Otherwise the
preflight refuses, before any write, naming the field, the value and the source coordinate. A disabled value is a
preflight refusal, not an exception of the manifest (`migration_attribute_exceptions`): an exception records a value
the target cannot represent and the copy sends `null` instead, whereas the workflow is an administrator's choice
the cutover must not silently approximate (PAT-ADR-0003, PAT-ADR-0008). It does not probe whether the server carries
DEVHUB-105 (an older server refuses the first import): unknown at preflight.
Limits that stay (DevHub agent, `cd23304`): a replay of an already imported `source_ref` with the same content returns
the existing issue before any check, and a workflow published between this read and the write can change the
outcome (create has no workflow-version precondition, deduced), so the check is best effort against a concurrent
publish. The preflight is a gate before the first effect, not a lock.

**`migration_supported_attributes`** is the seven attributes of `migration._ISSUE_ATTRIBUTES`; the four
field values are sent by `migration_import_issue`, and `parent`, `children` and `dependencies` are the links
written by `migration_link_issue`. **`migration_attribute_exceptions`** names, per issue, each value DevHub
cannot reproduce, so the manifest records it and `copy_and_verify` neither sends nor compares it:
`priority` outside `P0` to `P3`, `estimate` that is not an integer from 1 to 21, `type` empty or longer than
80 UTF-16 units, and a `state` other than `backlog` or `ready` (the server creates an issue in those two
states only, `createTrackerIssue`: an imported `in-progress` or `review` issue is therefore created in
`backlog`, with the exception recorded; an absent `state` is excepted the same way, since the server would create the issue in `backlog` and no readback equals `None`, PAT-325). An absent (`None`) `type`, `priority` or `estimate` is not excepted: `migration_import_issue` omits it and the server stores `null` for an omitted field (`fields.priority ?? null` and its two siblings in `createTrackerIssue`, `src/tracker/store.ts` at `3f0cf4b`; deduced from source, then observed live by DHN-43, `devhub-requirements.md`, "Live run of PAT-325"), which is what the readback compares; `test_migration_import_issue_omits_every_absent_attribute` and `test_migration_attribute_exceptions_name_each_value_devhub_cannot_reproduce` (`tests/test_devhub_tracker.py`) cover that path offline; no live test exercises an absent `state`. A value that a published project workflow disables is not an exception: the preflight above refuses it
(PAT-328). The other providers do otherwise: a value their target cannot represent is a manifest exception (for example Linear, `migration_attribute_exceptions` of `trackers/linear.py`). The live-work copy cell is `supported`;
DHN-44 and DHN-45 (`devhub-requirements.md`) are the live proof, green at `cd23304` ("Live run of PAT-328").

**ADR readback.** `migration_prepare_adr` returns the snapshot with `supersedes` sorted and the issue relations
sorted by key then number, and `migration_export_adrs` returns the same order, because DevHub does not
document the order it reads relations back in (unknown) and `copy_and_verify` compares lists. Since PAT-324
`migration_export_adrs` also reads the issue relations of each ADR (`GET /adrs/:id/relationships`, one more
read per ADR, shared with `read_adr_issue_relations`): they are no longer `unknown`, except for an ADR that declared
them so at import (PAT-327, whose `issues` are then not read). `migration_qualify_adrs`
is the port default (`None`): the ADR codec needs no qualification, and `migration_import_adrs` refuses one.
Order of the copy: issues, their links, their comments ("Terminal issue import"), then the ADRs, each replacement before the ADR it replaces
("ADRs" above).

## Explicit source issue numbers (PLP-311; live qualification pending)

The target configuration can opt in with `Project.extra.preserve_issue_numbers: true`;
`DevHubTracker._preserve_issue_numbers` requires a boolean and defaults to false.
Synthetic target configuration (replace the target id with the disposable project's id):

```json
{"key":"ARCH","id":"target-id","extra":{"canonical_repo":"github.com/example/archive","preserve_issue_numbers":true}}
```

With the option true, `_migration_issue_number` and `_snapshot_issue_number` require an
exact source coordinate `<tracker>:issue:<target-key>-<number>` matching the snapshot id,
with canonical ASCII digits, no leading zero, and a number in `1..999999`
(`_IMPORT_ISSUE_NUMBER_MAX`); `migration_preflight` also refuses repeated numbers before
its identity/workflow reads.
For example, source ids `ARCH-19`, `ARCH-7`, `ARCH-1` request numbers `19`, `7`, `1` in
that order, including holes (`tests/test_devhub_migration_identity.py`).
`migration_import_issue` adds `source_number` beside `source_ref` for living creates;
`_import_terminal_issue` adds it for terminal imports.
With the option absent or false, neither method adds the member; the existing payloads
are unchanged (`test_absent_and_false_option_have_identical_payloads`).
`capture_manifest` records the option in the target project and the deterministic
`migration_preflight` profile, both covered by `migration._source_view`;
`cutover._registry_target_project` retains it and `registry._validated_binding_entry`
validates and preserves it in the target binding.
`_read_back_registered` retains this local option when `resolve_checkout_project` re-reads
the server identity; server metadata cannot enable or override it.
Both import methods compare the exact returned id, and `migration_find_issue` compares
the id derived from `source_ref`, so both resumed copy and activation readback refuse a
sequentially renumbered issue (`copy_and_verify`, `verify_targets` in `migration.py`).
A server refusal propagates without dropping `source_number` or retrying sequentially
(`test_server_refusal_never_retries_without_source_number`).
The preflight does not detect whether a server supports this extension; live support
is unknown until DHN-50 passes on a disposable instance carrying the extension
(`tests/integration/test_devhub_live_conformance.py`, `docs/devhub-requirements.md`).
The server contract requires `source_ref`, a service credential and Foundry governance
for `source_number` (observed in DevHub `src/tracker/contract.ts::sourceNumberSchema` and
`src/tracker/store.ts::assertSourceNumberAuthority`); Foundry sends the number only through the migration methods above,
not native `create_issue`.

Historical ADR `rejected` keeps exact body bytes and explicit `unknown` relation families
through `_prepare_adr_import`, `_to_adr` and `migration_export_adrs`; `_preflight_adrs`
accepts that status but refuses a `proposed` or `rejected` supersession replacement.
This extension adds no native decision target (`_ADR_STATES`, `create_adr`,
`set_adr_status`); live qualification of the historical round trip is pending DHN-50.

## Terminal issue import (PAT-331, DHN-49)

The manifest of a cutover toward DevHub covers every issue of the source project, `done` and
`dropped` ones and archived ones included, because `DevHubTracker.migration_terminal_import_supported` is
true (`trackers/base.py`, `capture_manifest` in `migration.py`); a target that leaves it false keeps the
living-only scope of FOUNDRY-ADR-0026. PAT-ADR-0032 frames this and is `proposed` at the time of
writing. A terminal record carries `terminal` (`pr_url` and the three source dates) and `pending` (an empty list for DevHub since stage 3).

- Source: `migration_export_issues` lists the issues (`trackers/base.py`; Linear overrides it in
  `trackers/linear.py` with `includeArchived` and the native `completedAt` of a `done` issue or
  `canceledAt` of a `dropped` one, `None` when Linear has none). An archived issue gets the manifest
  exception `archived` (the import route has no archived state). Source dates are `Issue.created` and
  `Issue.updated`.
- Write: `migration_import_issue` posts a record carrying `terminal` to `POST /projects/:key/issues/import`
  (`_terminal_import_body`, `_import_terminal_issue`). Request shape and replay rules: the section "Terminal
  issue import" of the DevHub contract (`TRACKER_API.md`, DEVHUB-126, merge `defda07`). The `fields` object
  always holds `type`, `priority` and `estimate` (`null` when absent or excepted), because the server
  digests it as sent; `milestone` and `labels` are never sent, as in the living import; they are named manifest
  exceptions when the source has a value (see "Labels and milestone, and Epic closure over imported children" below). `pr_url` and each date are omitted when `None`.
  A replay with the same content converges, other content is `TrackerConflictError`.
- Readback: `Issue.import_history` holds the three dates of an issue the server marks as an imported terminal
  issue (`_to_issue`); `copy_and_verify` and `verify_targets` compare state, `pr_url` and the three dates
  exactly (`_terminal_matches`, `migration.py`).
- Preflight: dates that the route refuses (updated or completed before created, negative, not an integer)
  and a `pr_url` over 500 units are refused before any write (`_preflight_issues`); the workflow status of
  `done` or `dropped` is checked like a creation state (`_preflight_workflow`).
- Links (PAT-331 stage 2, DEVHUB-127, merge `bd282fc`): the links
  of a terminal record are copied, replayed and compared like a living issue's, through
  `migration_link_issue` (same pre-read of the source links, so a replay posts nothing; versions are sent as for
  a living link) and the `copy_and_verify` / `verify_targets` link loops of `migration.py`. A link bumps the
  version of both ends and nothing else, so the readback does not compare versions and the frozen facts still
  compare exactly (`_terminal_matches`). The server's refusal of an existing edge under a new idempotency key
  is not handled specially: the pre-read makes it unreachable in a sequential run (deduced, not run). Offline
  tests: `test_migration_link_issue_links_an_imported_terminal_issue_with_current_versions`
  (`tests/test_devhub_tracker.py`), `test_terminal_copy_links_terminal_records_and_is_not_ready_while_comments_are_pending`
  (`tests/test_tracker_migration.py`). Run live by DHN-49 (passed at `bd282fc` and `a57ce72`, `devhub-requirements.md`, "Live run of PAT-331").
- Comments (PAT-331 stage 3, DEVHUB-128, merge `a57ce72`; DevHub reported its production at `a57ce72` on 2026-10-22, not checked here): the
  comments of every captured issue, living or terminal, Foundry lifecycle receipts included (on Linear they
  are comments), are copied one by one, for a target that sets `migration_comments_supported` (`trackers/base.py`, `False` by default; `True` on DevHub only). Contract: the section "Comment import" of the DevHub contract
  (`TRACKER_API.md`). Source: `migration_export_issues` entries carry `comments` (`trackers/base.py`); the port
  default carries no `comments` key, and `capture_manifest` then records an exception `comments` ("not copied; the
  source does not export comments") instead of an empty list, which does not keep `ready_for_cutover` false; Linear
  nests them in the history listing (`_ISSUES_HISTORY_QUERY`, `_history_comments`, `_history_comment` in
  `trackers/linear.py`), so the export adds no request per issue and one request per further page for an
  issue with more than `_HISTORY_COMMENTS_PAGE` comments (`_ISSUE_COMMENTS_HISTORY_QUERY`); the listing page
  is `_HISTORY_ISSUES_PAGE` issues, smaller than before. Whether Linear accepts that nesting under its
  query-complexity bound, and whether `comments` of an issue includes threaded replies, is unknown until a
  real run. Author: the `name`, else the `displayName`, of the user, else the bot actor's `name`, else the
  external user's; none means no author. A comment has the coordinate `<source tracker>:comment:<id>`
  (`capture_manifest` / `_capture_comments`, `migration.py`) and the four members `source_ref`, `text`,
  `source_author` and `source_created` (the Linear `createdAt`, whole epoch milliseconds,
  `_epoch_ms_exact`).
- Comment exceptions, never approximated: `DevHubTracker.migration_comment_exception` returns a reason for a
  comment that `importCommentSchema` of DEVHUB-128 would refuse (empty or blank text, text over
  `_IMPORT_COMMENT_MAX` UTF-16 units, NUL or unpaired surrogate, missing or blank author, author over
  `_IMPORT_AUTHOR_MAX` units or holding a control character, date that is not an integer from 0 to `_IMPORT_EPOCH_MAX`, so
  a negative date is refused as well as one after 9999-12-31T23:59:59.999Z, see "Corrections from the PAT-332 rehearsal").
  A reply in a Linear comment thread is a named exception too, whatever the target (`COMMENT_REPLY_EXCEPTION`,
  `_capture_comments` in `migration.py`; `_history_comment` in `trackers/linear.py` reads the `parent` of the comment,
  `_HISTORY_COMMENT_FIELDS`): the thread is not reproduced and the reply is not flattened into the issue. Such a
  comment is not written: the record carries a `comment_exceptions` entry with its coordinate, the reason
  (never the content) and a digest of its content. Like the attribute exceptions (`labels`, `milestone`,
  `archived`) it does not by itself keep `ready_for_cutover` false. `_preflight_issues` refuses before any write
  a comment that reaches the copy outside these limits, or a duplicated `source_ref` on one issue.
- Write: after the issues and their links, `copy_and_verify` reads each issue, imports only the comments not
  already present with identical members, through `migration_import_comment` (one
  `POST /issues/:id/comments/import`, no `If-Match`; the `Idempotency-Key` is a hash of the issue and the four
  members, so a replay sends the same key and a changed content a new one). A 409 `resource_conflict` makes
  the adapter read the issue again: the same four members under the `source_ref` is success, anything else is
  `TrackerConflictError`. Each first import raises the version of the issue (no state, field or frozen fact
  changes, per the contract).
- Readback: `_to_issue` adds `source_ref`, `source_author` and `source_created` to the entries of
  `Issue.comments` that carry them (all three or none, else `invalid_response`). `copy_and_verify` and
  `verify_targets` compare the imported comments of the target (`_imported_comments`, `_comments_match`) as a
  set keyed by `source_ref` with those of the manifest: equal members, no missing and no extra imported
  comment; native comments of the target are not compared (observed: `migration.py` and `cutover.py` never call
  `add_comment`). DevHub orders comments by source date, then
  insertion, so the comparison does not depend on the import order. The DevHub web timeline shows an imported
  comment under the importing service at the insertion date (DEVHUB-130, API correct).
- Nothing is pending any more: `migration_pending_terminal_items` is empty (`DevHubTracker`), and
  `migration.ready_for_cutover` is true once every record and ADR is verified. The generic mechanism of
  `migration.py` is unchanged: a target that names an item there still has `ready_for_cutover` false, and one
  that names `links` has them neither written nor compared (test
  `test_links_of_terminal_records_stay_unwritten_while_the_target_names_them_pending`). An imported receipt is
  never a new review or merge proof (PAT-ADR-0032). Offline tests: the `comment` tests of
  `tests/test_devhub_tracker.py` and `test_manifest_records_comments_of_living_and_terminal_issues_and_names_exceptions`,
  `test_comment_copy_is_read_back_exactly_idempotent_and_makes_the_cutover_ready`,
  `test_verify_targets_compares_imported_comments_exactly` and the Linear export tests of
  `tests/test_tracker_migration.py`.
- Epic closure and release-scope reads over an imported terminal child: see "Labels and milestone, and Epic
  closure over imported children" below; the Epic closure over such children passed live in DHN-49.
- Points to watch in the real-corpus rehearsal (PAT-332): a terminal record whose updated or completed date
  precedes its created date is refused by `_preflight_issues` (fail closed) instead of becoming a named
  exception, so one such issue stops the whole copy (deduced from the code, not observed on a real corpus);
  and a cycle of parent or dependency links between issues of the manifest (`_preflight_issues`, deduced from
  the code, not observed on a real corpus). A parent that is not an `Epic` is no longer one of them: see
  "Corrections from the PAT-332 rehearsal" below. Comments: the rehearsal read 20 comments of an archived
  Linear issue through the history listing (observed in the rehearsal, reported by the coordinator, not
  re-run here); whether archived comments or threaded replies are all included is unknown. Offline test of the
  reply exception: `test_a_reply_in_a_thread_is_a_named_comment_exception_and_a_top_level_one_is_kept`
  (`tests/test_tracker_migration.py`). The real cutover of this repository (PAT-334) was captured before this
  exception existed, so a reply in its corpus, if any, was imported flattened into `PLP`; whether there was any
  is unknown. The history query with `parent { id }` has not run against the Linear API (unknown).
- Points to watch, ADRs (PAT-332): `migration_export_adrs` (`trackers/linear.py`) exports only the last version of
  each ADR version chain (`chains[adr_id][-1]`), so the earlier versions are not copied and are not named as
  exceptions; and `source_created` / `source_updated` come from the `origin` of that last version, and the `origin` of
  an ADR created in Linear is `{"kind": "native"}` (`trackers/linear.py`, ADR metadata validation), which carries
  neither, so they are `None` and the ADR reaches DevHub without source dates (deduced from the code, not
  observed on a real corpus). Neither is a manifest exception today.
- Points to watch, strict read (PAT-332): the history export reads every issue, archived or not, through
  `_export_issue`, which calls `_to_issue` without `observe_lifecycle`, so one issue refused by that strict read
  stops the whole export (fails closed), and so the capture. Among these refusals: `LinearBindingError` with `state_id_unmapped`,
  `label_id_unmapped` or `milestone_id_unmapped` (the state, a label or the milestone of the issue is not in the
  binding), a `TrackerConflictError` from the lifecycle projection (State and receipts disagree, malformed
  marker) and the same error from the Epic closure audit (`_verified_epic_closure`); the list is not exhaustive (for
  example `type_labels_ambiguous`, a closure audit on an issue that is not `done`, the `invalid_response` of
  normalisation and the project check `_assert_issue_project`). Deduced from the code, not
  observed on a real corpus; no such issue becomes a named exception.
- Scope toward a target without terminal import: `capture_manifest` skips an archived source issue when
  `migration_terminal_import_supported` is false, as `search` did, so FOUNDRY-ADR-0026's living-only scope
  is unchanged there. `migration_export_issues` of Linear still reads the comments of every issue whatever
  the target (they are nested in the listing page, `_ISSUES_HISTORY_QUERY`, with one further request per
  `_HISTORY_COMMENTS_PAGE` comments beyond the first page of an issue), so that request cost also falls on
  a target that copies none (`migration_comments_supported` false).

Offline tests: `tests/test_devhub_tracker.py` (terminal import, readback, preflight),
`tests/test_tracker_migration.py` (manifest, copy, readback, Linear export). Live: DHN-49 in
`devhub-requirements.md`, `green` ("Live run of PAT-331").

### Corrections from the PAT-332 rehearsal (PAT-332)

Five gaps observed by the rehearsal of this repository's Linear corpus toward disposable DevHub instances built from
`890e338`, then `72a24e2` for the final run (reported by the coordinator in
`qualification/pat-332-cutover-rehearsal.md`; the offline tests below do not re-run them).

- Archived links (observed): the Linear history export read each issue with connections that leave out archived
  issues, so a closed archived `Nits` Epic exported with no child and ten `relates` links between archived
  issues were one-sided. The export now reads each issue with `_ISSUE_HISTORY_QUERY`
  (`includeArchived: true` on `children`, `relations`, `inverseRelations`); see `linear-tracker.md`, "History
  export of archived links (PAT-332)".
- Non-Epic parent (observed: DevHub refused a link whose parent had no type, `createTrackerLink`, "parent must be
  an Epic"): `Tracker.migration_epic_only_parent` (`trackers/base.py`, `False` by default; `True` on
  `DevHubTracker`). When it is true, `capture_manifest` makes each captured `parent-of` / `subtask-of` link whose
  parent `type` is not exactly `Epic`, or whose `type` is excepted by `migration_attribute_exceptions`, an entry
  of the `links` exception of BOTH issues, with cause `parent is not an Epic on the target` (same
  `{type, direction, target, cause}` shape as the links outside the manifest). The link is removed from the
  record's `links`, so `copy_and_verify` and `verify_targets` neither write nor compare it, and the exception does
  not keep `ready_for_cutover` false. The `_preflight_issues` refusal "parent non Epic refusé par DevHub" stays
  for a link still present (defensive). Tests: `test_a_non_epic_parent_link_is_a_named_exception_on_both_ends_and_is_not_copied`,
  `test_an_epic_parent_link_is_kept_and_a_target_without_the_member_keeps_every_link`,
  `test_a_parent_whose_type_is_excepted_by_the_target_is_not_an_epic_there` (`tests/test_tracker_migration.py`).
- ADR numbering order (observed: `FOUNDRY-ADR-0001` took number 1, then `PAT-ADR-0001` was refused by the 409
  mapped in `import_adr`): the rule is that of `originAdrNumber`, DevHub `890e338` (DEVHUB-111, DEVHUB-116), as
  stated by DevHub and read in its message, not re-run: a foreign id byte-identical to `<KEY>-ADR-NNNN`, NNNN
  being the number from 1 to 999999 left-padded to 4 digits and not over-padded, keeps that number; any other
  foreign id takes the project counter, and after each insert the counter becomes the greater of itself and the
  number plus one; a number already held answers 409 `resource_conflict`. `migration_import_adrs` therefore
  writes first the ADRs that keep their number (`_origin_adr_number`), then the others, each replacement still
  before the ADR it replaces (`_numbering_order` over `_replacement_order`); answers stay in input order. Before
  the first write, and in `_preflight_adrs`, `_numbering_order` simulates that numbering from an empty project
  and raises `TrackerConflictError` naming the ADR that would claim a held number (for example an unnumbered
  replacement forced before the numbered ADR it replaces, which takes its number). The simulation assumes an
  empty project; a number already held by an ADR present on the target is still refused by the server only.
  Documented limit (deduced from the code, not observed): `_numbering_order` picks one order and does not search
  the others, so an unnumbered replacement forced before the numbered ADR it replaces can take a counter that a
  numbered ADR written later needs, and the corpus is refused (fails closed, before any write) although another
  order would number cleanly (`TRAME-ADR-0005` replaced by an unnumbered ADR, plus `TRAME-ADR-0001`: refused,
  while `TRAME-ADR-0001`, the replacement, `TRAME-ADR-0005` is not). Writing the numbered ADRs in decreasing
  order does not settle that example. Test of the limit:
  `test_numbering_order_documented_limit_refuses_a_corpus_that_another_order_would_take`.
  The ADRs that do not keep their number get server numbers above those held and keep their foreign id as port
  id. Tests: `test_origin_adr_number_follows_the_server_rule`,
  `test_migration_import_adrs_writes_the_adrs_that_keep_their_number_first`,
  `test_numbering_order_refuses_an_adr_claiming_a_number_already_held_naming_it` (`tests/test_devhub_tracker.py`).
- Source date bound (stated by DevHub `890e338`, DEVHUB-130, read in its message, not re-run): a source date above
  9999-12-31T23:59:59.999Z answers 400 `invalid_request`. `_IMPORT_EPOCH_MAX` (`trackers/devhub.py`) is now
  253402300799999 ms (it was 8640000000000000, the ECMAScript date limit) and bounds the comment date
  (`migration_comment_exception`), the terminal issue dates (`_preflight_issues`) and the ADR dates (`_prepare_adr_import`, called by `import_adr`),
  each refused before any write. Tests: `test_the_comment_date_bound_is_the_last_millisecond_of_year_9999`,
  `test_the_adr_source_date_bound_is_checked_before_any_write`.
- Comment length (observed: two Epic closure receipts of the corpus, 54970 and 56409 characters, were
  comment exceptions under the 50000 bound): DevHub raised `MAX_COMMENT_TEXT` to 200000 for native and imported
  comments (DEVHUB-131, merge `72a24e2`, `src/tracker/contract.ts:82`, read in source). `_IMPORT_COMMENT_MAX`
  (`trackers/devhub.py`) is now 200000. A DevHub older than `72a24e2` refuses an imported comment over 50000
  characters with 400 and the copy stops before activation (deduced from the route, not run): the cutover
  needs a DevHub at `72a24e2` or later. Foundry does not check the server version. DevHub also caps a request
  body at 1 MiB, so a text near 200000 characters made mostly of characters JSON-escaped as `\u00XX` can be
  refused as too large (stated by DevHub, not run). Test: `test_comment_exception_names_each_limit_without_quoting_the_content`.

### Labels and milestone, and Epic closure over imported children (PAT-331, decisions of 2026-10-22)

- Living issue PR link and source dates: a living record has no `terminal` member, so its `pr_url` and
  its `Issue.created` / `Issue.updated` are not sent. `capture_manifest` records an exception `pr_url` and an
  exception `source_dates` (value `{"created", "updated"}`), each carrying the source value, only when the
  value is not `None`, for every target. DevHub's living create schema can hold `pr_url` in `fields`
  (`createIssueSchema`, `src/tracker/contract.ts` at `a57ce72`), but a DevHub issue with a PR link is
  refused the acceptance-by-decision path ("the issue is linked to a PR; use `issue merge`",
  `trackers/devhub.py`), so sending it would change how the living issue can be closed (deduced, not run); the create route has no date member. Like labels and milestone these exceptions do not
  by themselves keep `ready_for_cutover` false. Offline test:
  `test_manifest_names_pr_url_and_source_dates_of_a_living_issue_as_exceptions`; the live test sets both on
  its living issue (DHN-49, green live, `devhub-requirements.md`, "Live run of PAT-331").
- Links that no target writes (PAT-331 review): `capture_manifest` records, for every target, an exception `links`
  whose reason lists, as JSON `{type, direction, target, cause}`, each link of the issue to an issue outside the
  manifest (cause `target outside the manifest`: the Linear issue read has no project filter on `parent`,
  `children`, `relations` and `inverseRelations`, `_ISSUE_FIELDS` in `trackers/linear.py`, and a target without
  terminal import leaves terminal issues out) and each source relation with no Foundry link type (cause
  `relation type without a target equivalent`, the `uncaptured_links` of `migration_export_issues`,
  `trackers/base.py`). The Linear history export reads each issue through `_export_issue`, which sets aside a
  relation of a type other than `blocks` and `related` (`duplicate`, `similar`) there; every other Linear read
  still fails with `unsupported_relation_type` (`_relation_link`). These links are neither written nor compared
  (`copy_and_verify`, `verify_targets`), and the exception does not keep `ready_for_cutover` false. Offline tests:
  `test_links_outside_the_manifest_and_relations_without_a_link_type_are_named_exceptions`,
  `test_living_only_target_names_the_links_to_terminal_issues_it_leaves_out` (`tests/test_tracker_migration.py`),
  `test_relation_reads_refuse_unsupported_native_types_without_partial_issue` (`tests/test_linear_tracker.py`).
  DHN-49 adds a link outside the manifest and a link between two imported terminal issues (a `done` Epic and
  its `done` child); that extension is green live (`devhub-requirements.md`, "Live run of PAT-331").
- Labels and milestone: no target copies them. `capture_manifest` (`migration.py`) records, for every
  target and every issue (living or terminal), an exception `labels` when `Issue.labels` is not empty and an
  exception `milestone` when `Issue.milestone` is not `None`, each reason carrying the source value; an
  issue without them gets neither, so its digest is unchanged. The Linear source fills both fields
  (`normalize` in `trackers/linear.py`: `labels` without the type labels, `milestone` by the project's
  `milestone_ids`). Offline test: `test_manifest_names_labels_and_milestone_as_exceptions_only_when_the_source_has_them`.
- Epic closure: `observe_epic_closure_child` (`trackers/devhub.py`) reads the detail of a child; when the
  server marks it `imported_terminal` (`Issue.import_history`) and its state is `done` or `dropped`, it
  counts without a new proof. A `done` one reads accepted, its criteria counted as satisfied, with the
  acceptance source `DevHubTracker.imported_terminal_acceptance_source` (`devhub-imported-terminal`), distinct
  from `devhub-human-acceptance` and from any receipt; its proof stays in the source tracker. A `dropped`
  one carries the same source and `write.close_epic` leaves it out of the set that `--accept-dropped` must
  name (PAT-ADR-0030 still applies to every other abandoned child). The detail read is the only way used to
  know a child is imported: the Epic subgraph read is not consulted for it. The atomic receipt still binds
  `{id, version, state}` read at closure time. Per the DEVHUB-127 delivery
  (relayed, tested on the server, and run by Foundry in DHN-49), the server accepts such children, and a link bumps a
  child's version; the version is read at closure time, so a snapshot taken before linking is not used. The
  extension of DHN-49 closes a living Epic over an imported `done` and an imported `dropped` child through
  `write.close_epic`; passed live ("Live run of PAT-331").
- Release scope: `_release_issue` reads an imported `done` issue as `unavailable`, with a reference
  `unavailable_reason` naming the import, never as accepted.
- Offline tests: `tests/test_devhub_tracker.py` (`test_closure_child_read_counts_an_imported_terminal_child_without_a_new_proof`,
  `test_epic_closure_accepts_imported_terminal_children_without_a_named_closure`,
  `test_epic_closure_still_names_a_dropped_child_that_was_not_imported`,
  `test_release_scope_never_reads_an_imported_terminal_issue_as_accepted`).

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

`migration_export_adrs(project)` reads one project listing, then the same document for each ADR, and returns `superseded_by` and `supersedes` (the latter from `supersedes_all` when present, else from `supersedes`, as port ids: the foreign id for an imported ADR). A non-superseded ADR without a link reports `superseded_by: None`; a `superseded` ADR with no recorded link (set through the status route, possible up to `8028000`; refused since `12ca158`) keeps `unknown` (PAT-ADR-0003). While the project holds such an ADR other than the entry itself, its replacement is unknown and could be any ADR, so `supersedes` of the entry is `unknown`, not `[]` and not a partial list (PAT-ADR-0008); otherwise it is the list read. `issues` is read from `GET /adrs/:id/relationships` (PAT-324, "Selective cutover target"); `supersedes` and `issues` are sorted. A family the ADR declared unknown at import (DEVHUB-122, PAT-327) is exported as `unknown`
whatever the document holds. N to 1 reads back as several entries in `supersedes_all` of the replacement (`6721ad8:src/tracker/adr-decisions-store.ts:20-22`).

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
