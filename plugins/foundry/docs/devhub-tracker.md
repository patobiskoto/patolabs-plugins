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
writes the single local registry entry. A retry after provider success but before the
registry write therefore recovers the same project. No manual REST call or separate
`registry register` step is required.

`registry register` remains available only for binding a project that was deliberately
created outside this setup flow. It is not the normal new-project path.

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
refreshes the `review` receipt on the exact head/diff that passed the current gates.
Before that gate, rerunning `issue openpr` reuses the one open PR for the exact branch and
base, preserves its body unless a non-empty `--summary-stdin` is explicitly supplied, and
emits a fresh proof-bound `review` transition. It
refuses a closed, wrong-base, or ambiguous candidate instead of creating a duplicate.
If the issue is already `done`, a retry accepts cleanup only when GitHub reports that exact
PR URL as merged with a merge SHA; it neither rewinds the immutable tracker state nor calls
the merge endpoint again.

## Destructive smoke

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
