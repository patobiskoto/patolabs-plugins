# DevHub command worker

Foundry consumes the `devhub-foundry-command.v1` protocol through an outbound,
bounded pull worker. DevHub remains the intention store: it never calls an incoming
Foundry endpoint and never receives shell commands, paths, credentials, runtime
arguments, gate overrides, or merge primitives.

The worker uses a dedicated `DEVHUB_COMMAND_TOKEN`. Its DevHub service actor must
have only:

- `foundry:command:claim` for listing, claiming and heartbeating commands;
- `foundry:command:event` for command resolution and append-only outcomes.

Publishing previews may use a separate credential with only
`foundry:command:preview`. None of these scopes implies Tracker reads/writes,
transitions, Git, runtime, review, CI, push, PR or merge authority. Do not reuse the
broader `DEVHUB_TRACKER_TOKEN` for the worker.

Store it without placing the secret in argv or a plaintext config file:

```bash
python3 tooling/foundry_cli.py configure credential --name DEVHUB_COMMAND_TOKEN
```

Before every new or resumed effect, Foundry re-observes the exact project, Epic,
planning Version, preview/snapshot/policy digests and approval windows. It also
requires the tests, independent-review and human-test gates. Effective cost and
concurrency caps are the minimum of the approved command caps and current local
caps/budget; the routing floor may only stay equal or rise.

The claim and every heartbeat may advance only lease/projection facts. The worker
compares every approved input (IDs, digests, scope, limits, schedule and creation
identity) exactly and rejects any rewrite. A heartbeat is usable only when its lease
and heartbeat both advance and the resulting lease is still live. Proxy timeouts and
HTTP 5xx responses to POST are unknown results, resolved through the command or event
identity without repeating the mutation.

Each claimed command is journaled in a private SQLite receipt store before launch.
The DevHub command ID is the provider effect identity. After a crash or ambiguous
transport result, the worker resolves that identity first. A proven running effect
is resumed, a proven terminal effect is reported, and an unresolved effect remains
`unknown`; it is never launched a second time merely because a request timed out.
A terminal receipt can only be replayed identically or enriched with previously-null
telemetry; it cannot become running/paused again and is never passed to `resume`.

### Late terminal fact after lease expiry (FOUNDRY-103 / DEVHUB-33)

Before trying to claim an expired command, the native campaign provider checks an
existing close intent against the original Tracker closure and both durable
acceptance/primitive journals. A committed close can complete its local primitive
proof after an interruption, but this path never enters the campaign executor.
Missing or contradictory originals defer publication; F91 and issue state alone
cannot replace that proof.

The dedicated `POST /commands/:id/late-terminal-publications` contract
(`devhub-foundry-late-terminal-publication.v1`) sends the exact original closure,
attempt, old lease, next sequence and nullable economics. DevHub atomically appends
the missing extended `completed` fact and terminal reconciliation. No lease is
claimed or renewed; no child, review, merge, close, routing decision or reservation
is dispatched. Ordinary claim/event gates remain unchanged. Stable IDs and original
closure issue time permit one byte-equivalent retry after an ambiguous response.
Response command/event/Epic/digest/economics bindings are checked before success.

If the local command already had its completed outcome, it is preserved. If the
process died before that local outcome was recorded, the remote terminal fact does
not fabricate local timing, cost, or a completed coordinator run: unknown economics
remain `null`, and existing conservative host reservations are not relaxed by this
publication-only operation. This is factual reconciliation, not campaign resumption.

The opt-in loopback smoke
`tests/integration/test_real_terminal_reconciliation_smoke.py` accepts
`DEVHUB_CLOSURE_SMOKE_LATE=1` to test real 15-second lease expiry, original receipt
recovery, atomic terminal publication and exact replay through public HTTP adapters.
Both modes require an explicitly disposable open Epic, 1 through 100 terminal direct
children, and one `requested` command bound to the open Epic version. Nominal mode
leaves the flag unset and requires complete or absent AC. Late mode requires exactly
one literal unchecked marker `- [ ] smoke acceptance`, with every other AC complete;
the command binding is the Epic version before that marker is synchronized. Zero or
multiple exact markers are rejected before the acceptance mutation. Neither mode
performs cloud/PR/merge work.

## Concrete bounded runtime

`BoundedExecutionPath` asks the execution provider for its worst-case tier, cost and
concurrency before any effect. It rejects an under-floor tier or an over-cap resource
request, then commits the cost ceiling and concurrency units to one host-shared SQLite
ledger with `BEGIN IMMEDIATE`. The same transaction includes the fresh externally
observed active-concurrency and remaining-budget signals plus every local active
reservation and every settled cost not yet reflected by that observation. The command
ID/provider effect ID is the durable key; a second process cannot double-own it or
overbook a host limit between the final check and provider launch.

The concrete runtime reloads its authority after that initial reservation and hands the
exact fresh observation back to the guard before spawning the provider process. A second
`BEGIN IMMEDIATE` transaction reconciles that observation with every ledger row and the
current reservation. Any tightened external capacity or malformed ledger row refuses the
effect; aggregates never coerce or skip unreadable state or numeric values.

A terminal result settles actual cost and releases concurrency. Missing terminal cost
charges the full reserved ceiling. Non-terminal or ambiguous results retain their
allocation; process death never expires capacity by itself. After the owner TTL, only a
proven receipt for that exact non-terminal effect permits takeover. Terminal resolution
after restart settles the old reservation. Thus an unresolved crash can reduce capacity
but can never create capacity or authorize a blind retry.

The production outbound entry point now invokes the trusted campaign coordinator. It
reserves the whole effective campaign cost and available concurrency before creating
per-issue worktrees. The coordinator alone invokes the existing `issue start`,
`openpr`, structured review proof, CI gate, explicit human-test receipt, `merge`, and
FOUNDRY-83 `close-epic` primitives. A missing review or human receipt suspends the
campaign under the same effect identity; it never becomes an inferred pass.

Suspension remains a distinct, resumable Foundry ledger state with its exact bounded
reason (approved blocker, review, human gate, host, ambiguous effect, CI gate, budget
or policy). DevHub receives only its existing non-terminal `paused` projection; no
Foundry-only status is added to DevHub's closed command schema. A later scan may only
resume the same proven command effect and must still satisfy the local reason's exact
recovery or human-authorization gate. Neither journal contains a prompt, command,
path, provider payload, credential or raw failure.

DevHub v1 has no human resume request. Consequently, a human-confirmed pause stays
paused: Foundry does not infer a restart. For a locally authorized recovery, Foundry
first revalidates the same effect without crossing an effect boundary, then projects
its proven `running` fact. Only a later scan, after DevHub has recorded that fact,
continues the effect. An unchanged paused result is not appended again because
`paused -> paused` is not a DevHub transition. If the proof is missing, ambiguous or
rejected, both ledgers remain paused.

For a paused review, CI or human gate, this revalidation is limited to the exact
durable gate identity: it may re-read its proof or status and record a completed
receipt, but cannot start a child, dispatch a merge or infer a human approval. A human
may explicitly renew an expired gate only for the same campaign binding, PR head,
review and CI digests; both approval receipts remain journaled. Conversely, a merge
already engaged at its irreversible boundary is reconciled from its exact PR receipt
before current approval/host facts are consulted. This can confirm a pause or cancel,
but can never dispatch another merge.

Blocker IDs approved in the preview remain immutable and visible. Before each
observation, including this first-phase recovery, Foundry re-reads every exact blocker
from the active tracker and records its state plus a tracker freshness coordinate.
Only `done` or `dropped` with an `updated` timestamp or version is resolution proof;
unknown, reopened, malformed or stale facts keep the campaign suspended before any
child effect.

After a human has tested the exact PR head, the operator records the bounded gate
against that campaign, issue, attempt, completed review and green CI receipts:

```bash
python3 tooling/foundry_cli.py campaign-gate command-123 DEVHUB-21 \
  --attempt 1 --actor operator-1 --valid-seconds 3600 \
  --state-dir /absolute/private/command-worker/DEVHUB/campaign-runtime
```

The command refuses missing or mismatched review/CI/head coordinates and only emits
their digest-bound receipt. It is the explicit human continuation required before
the trusted coordinator may revalidate every gate and invoke `merge`; there is no
automatic human approval or ungated merge.

An implementation that explicitly returns `blocked` remains suspended under its
settled work identity. After resolving the blocker, an operator may authorize exactly
one distinct next attempt by binding the existing `manual_retry_approved` signal to
that campaign, issue, attempt and blocked proof:

```bash
python3 tooling/foundry_cli.py campaign-retry command-123 DEVHUB-21 \
  --attempt 1 --actor operator-1 \
  --state-dir /absolute/private/command-worker/DEVHUB/campaign-runtime
```

The command refuses a non-blocked, ambiguous, already-engaged or mismatched effect.
Without this append-only local authorization, repeated outbound scans only observe the
same blocker and never launch another implementation identity.

A separate authorization exists exclusively for the first `review` receipt whose
status is `blocked`. It cannot authorize an implementation blocker, a waiting review,
or a later `review_blocking_after_fix` signal:

```bash
python3 tooling/foundry_cli.py campaign-review-remediation command-123 DEVHUB-21 \
  --attempt 1 --actor operator-1 --valid-seconds 3600 \
  --state-dir /absolute/private/command-worker/DEVHUB/campaign-runtime \
  --root /absolute/project/repository
```

The command derives the exact review effect, blocked proof, campaign binding and
current implementation tier from `campaign.sqlite3`; none of the PR HEAD, base, diff
or proof coordinates can be supplied by the operator. Before its local transaction can
insert the grant or resume the campaign, the trusted primitive independently re-reads
the one current open PR, its branch HEAD, base SHA, exact binary diff, issue AC and
canonical blocked review proof. Those facts must reproduce both the exact
`primitive-receipts.sqlite3` row (including PR number, HEAD and base) and the exact
campaign effect. A changed HEAD, diff, base, PR identity/state, missing or changed
proof, ambiguous provider response, or mismatched role/effect fails closed with no
authorization insert and no campaign transition.

The campaign ledger stays write-locked across that bounded authoritative read and the
local compare-and-set. Two consecutive authoritative observations must reproduce the
same stored coordinates: a concurrent local effect update cannot race the grant, and
a provider change observed during the bounded read fails closed. The TTL must be
between 60 and 86400 seconds and must fit inside the campaign approval window. An
identical, unexpired replay performs the same fresh revalidation and returns the same
authorization; changed, expired, already-remediated or cross-effect coordinates fail
closed.

This command records only the content-free authorization and changes the campaign from
`suspended` to `running` in one local transaction. It launches no implementation,
provider call, PR, review, CI, human gate or merge. A later normal command-worker scan
consumes the one-shot receipt and, through the unchanged pipeline, may create attempt
`N+1` at the same or a higher tier. Any review that blocks after that correction uses
the existing `review_blocking_after_fix` policy and receives no implicit authorization.
The F89R2 AC7 operational qualification remains explicitly deferred until this change
has merged and the resulting plugin has been installed; this local command and its
tests do not attest a real campaign execution.

Claude Code is used only for one bounded implementation proposal at a time because its
`--max-budget-usd` flag enforces the attempt ceiling in the host. That child runs with
`Read,Edit,Write,Grep,Glob` only, with safe mode, no shell, plugin, MCP server, session
persistence or Foundry configuration. Its environment is rebuilt from the fixed,
non-secret allow-list `HOME`, `LANG`, `LC_ALL`, `PATH` and `TMPDIR`, plus the fixed
isolation marker. No other ambient variable is inherited, including unknown future
code-host, tracker, DevHub or provider credentials. It cannot start an issue, open or
merge a PR, approve a gate, or close the parent.

The host reservation ledger lives once under
`command-worker/host-reservations.sqlite3`, shared by every project worker; project
receipts, primitive receipts, implementation proposal digests and campaign ledgers
remain isolated in their project subdirectories.

The runtime re-reads a short-lived local authority document named
`<command-id>.json`. That document has contract `foundry-command-authority.v3`; it
contains the exact command references, the full approved preview plan, identifiable
approval state/window, digest-bound policy, host availability, fresh local limits and
an explicit `provider_invocation_ceiling_cents` distinct from the aggregate campaign
budget. Foundry recomputes the preview and policy digests and refuses stale, revoked,
expired or unavailable observations. A v2 or missing-cap authority is recognized only
to suspend before effects; Foundry never derives the missing ceiling from the campaign
budget. No DevHub-supplied command, path, model or runtime flag exists.

Run one bounded outbound scan with explicit local paths:

```bash
python3 tooling/foundry_cli.py command-worker DEVHUB \
  --worker-id foundry-worker-1 \
  --root /absolute/trusted/project/root \
  --authority-dir /absolute/private/authority-snapshots
```

The spawned implementation process inherits only the fixed environment allow-list;
it does not inherit `DEVHUB_COMMAND_TOKEN`, any known provider token, or any
unrecognized ambient credential. Only the pull worker can claim/heartbeat and append
command events. The outer campaign identity is atomically journaled before any child
effect, and every trusted primitive or proposal is journaled under deterministic
campaign/issue/attempt/work coordinates.
