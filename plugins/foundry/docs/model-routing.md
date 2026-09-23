# Model routing contract

Foundry routes delegated work through semantic roles rather than provider model names.
The shared deterministic contract lives in `tooling/foundry/routing.py` and
`tooling/foundry/escalation.py`; Claude Code and Codex façades must call it instead of
reproducing precedence, fallback, or escalation logic.

## Defaults and project configuration

The levels, from cheapest to strongest, are `economy`, `balanced`, `frontier`, and
`apex`. Defaults are scout=economy, implementer=balanced, coordinator=balanced,
reviewer=frontier, and architect=apex. The provider mappings implement
FOUNDRY-ADR-0006.

| Tier | Claude Code | Codex | Default role / minimum gate |
|---|---|---|---|
| `economy` | Haiku 4.5 / low | GPT-5.6 Luna / low | scout |
| `balanced` | Sonnet 5 / medium | GPT-5.6 Terra / medium | implementer, coordinator |
| `frontier` | Opus 5 / high | GPT-5.6 Sol / high | reviewer minimum |
| `apex` | Fable 5 / high | GPT-5.6 Sol / max | architect minimum |

The recommended primary coordinator for ordinary feature/fix/chore work is the balanced
profile: Sonnet 5 / medium in Claude Code or GPT-5.6 Terra / medium in Codex. This is
operator guidance, not an enforceable main-conversation setting. Frontier is appropriate
when the main loop must absorb a disclosed no-subagent fallback or genuinely
cross-cutting risk; apex remains reserved for explicit architecture decisions.

## Model, effort, and context are separate

FOUNDRY-ADR-0008 represents a resolved invocation as three independent dimensions:

- `model` selects provider capability;
- `reasoning_effort` selects an ordinal budget within one declared
  host/model-family/policy scope;
- `context_policy` defines deterministic collection, filtering, truncation, and the
  bounded packet given to that role.

The tier table above remains the production resolution in 0.7.0. Separation makes the
dimensions observable and testable; it does not add an adaptive resolver, an effort
ladder, an automatic context transition, a production shadow call, or a telemetry-fed
decision. The ADR-0006 precedence, fallbacks, gate floors, deterministic signals, and
two-tier-increase issue cap remain unchanged. Context is bounded by each host façade:
Claude receives the four-section packet described below, and Codex receives
`fork_turns=none` plus the same role contract. Neither inherits arbitrary main-loop
history.

A repository may commit `.foundry/model-routing.json`:

```json
{
  "version": 1,
  "roles": {
    "implementer": "frontier"
  },
  "mappings": {
    "codex": {
      "frontier": {
        "model": "gpt-5.6-sol",
        "effort": "max"
      }
    },
    "claude": {
      "economy": {
        "model": "haiku-4.5"
      }
    }
  }
}
```

Mapping overrides may set `model`, `effort`, or both. Resolution is field-aware and
strictly ordered: explicit user request, project configuration, Foundry default. Invalid
keys, roles, levels, hosts, efforts, JSON, or a gate below its floor fail with the exact
configuration path and expected values.

Efforts are declared per `(host, model family, policy version)` scope. A project may add
an `effort_scopes` declaration when it introduces a model family; levels are ordered only
inside that one scope. For example, a six-level family can expose `xhigh` while keeping
`ultra` explicitly inadmissible with its authority reason:

```json
{
  "effort_scopes": {
    "codex": {
      "gpt-6-astra": {
        "version": 1,
        "levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "inadmissible": {
          "ultra": "automatic task delegation is inadmissible pending a Foundry ADR"
        }
      }
    }
  }
}
```

`ultra` is inadmissible by default pending the separate delegation-authority ADR.
Unknown efforts name both the rejected level and the levels accepted by the relevant
scope. Claude host translations are also declarative: `claude_models` may map a new
canonical policy model to its Agent wire alias; a missing translation fails explicitly.

Built-in Claude models accept the current aliases, Foundry canonical names, and current
full IDs. A project model declared in `claude_models` keeps its configured canonical name
in the resolved route and telemetry, while the Agent wire receives its declared alias.

That diagnostic detail belongs to the internal/direct module boundary: calling
`foundry.routing.main()` directly (including development use through
`python -m foundry.routing`) preserves the precise configuration error so developers can locate the
invalid field. The shared public wrapper, `foundry_cli.py routing`, deliberately crosses
a stricter boundary: it replaces routing configuration and availability failures with
the generic, value-free message `Configuration de routage invalide.` on standard error
and exits non-zero. The wrapper never echoes the rejected value or any secret; callers
that need detailed diagnostics must use the internal development boundary in a trusted
environment.

If an explicitly requested model is unavailable, fallback uses the configured model of
the next eligible tier. An explicit reasoning effort is independent of model availability
and remains authoritative across that fallback.

Inspect the effective policy without invoking a model or revealing an override value:

```bash
python3 tooling/foundry_cli.py routing show
python3 tooling/foundry_cli.py routing show --host codex
python3 tooling/foundry_cli.py routing resolve reviewer --host claude
```

## Passive local telemetry

`tooling/foundry/telemetry.py` defines schema version 1 for two linked, append-only
events: `invocation_completed` and `run_outcome`. Their `run_id` is a random ephemeral
128-bit value; it is not derived from a repository, issue, thread, machine, or user.
The observer owns this opaque linking capability: callers cannot provide an ID. The
shared lifecycle stores only a validated projection of the already-resolved route before
execution and emits nothing until the invocation has returned. Claude's `PreToolUse`
hook binds that projection to a keyed private callback entry. The real `PostToolUse` /
`PostToolUseFailure` hook consumes it once and emits the private journal event, but is
otherwise completely silent: no stdout/stderr, hook output, `additionalContext`, bearer,
or telemetry instruction reaches the model. It maps only the trustworthy callback name:
`PostToolUse` is `completed/none`, and `PostToolUseFailure` is `failed/host`. The current
Claude hook boundary exposes no reliable finer timeout/cancellation class. Because a
silent hook has no safe private return channel to the coordinator, Claude aggregate
`run_outcome` correlation is unavailable rather than fabricated.

If Claude denies Agent permission before the Agent starts, neither post-tool event may
arrive. The private pending callback is then an intentionally bounded orphan: it records
no prompt or denial detail and is removed/refused by the existing 24-hour expiry or
bounded cleanup. This documents the FOUNDRY-32 lifecycle; it adds no event or inferred
failed invocation.

Codex `codex-plan` returns an optional completion capability outside `spawn` only for a
real `mode=subagent` invocation. The active skill consumes it after the actual host call
settles and must pass both controlled flags: `completed/none` for a normal return,
`failed/host` for an explicit host failure, `failed/timeout` for an explicit timeout,
`cancelled/cancelled` for an explicit cancellation, or `unknown/unknown` when the host
supplies no trustworthy typed classification. A partial result followed by an exception
uses the structured exception class, or `unknown/unknown` when ambiguous. There is no
success default. A `current_context` fallback has no distinct host-return boundary, so
planning does not falsely mark the still-running main loop complete. Neither capability
enters `spawn` or an agent task packet. The adapters do not resolve routes, read project
files, run tests, call a model, or feed any current/future decision.

An invocation records only host/kind; nullable role/tier; a canonical model alias or
`unknown`; effort, scope/context and resolution policy; controlled routing signals;
provenanced nullable counters; separate costs; and controlled status/failure class. A
run outcome records only aggregate test counters/state, correction cycles, review state,
severity totals and violation totals; every counter has the same nullable provenance
contract, so observed zero is distinct from unavailable. It deliberately has no routing
or global identity fields beyond the ephemeral link.

When `FOUNDRY_DATA` is unset it is disabled. When set, journal files are private,
bounded and retained only under `$FOUNDRY_DATA/telemetry`; the shared `FOUNDRY_DATA`
root is never chmodded or otherwise mutated by telemetry. Failed validation or IO is
silently fail-open. The schema is a strict whitelist: it excludes prompts/responses,
code, names/paths/digests, commands/output/comments, URLs/authors, free errors/stacks,
and secrets/PII. Unknown metrics remain `{ "value": null, "provenance": "unknown" }`;
zero is therefore observed, never inferred. Estimated and provider-reported costs stay
separate. A provider-reported value is an observation, not an actual or billed cost.

Pending, keyed callback-link, and outcome capabilities are random/HMAC-keyed, private,
one-time, and limited to 256 entries per state class. Creation, link replacement,
cleanup, and consumption all hold one stable private sidecar lock and use no-follow
directory-relative file operations, so the bound and one-shot claim hold across
processes without symlink/TOCTOU reads. Every consume checks the record's persisted
`created_ns` while holding that lock. A record one nanosecond before 24 hours remains
valid; at or after exactly 24 hours it is atomically removed and refused even if no later
creation or cleanup occurred. They contain only the whitelisted route projection,
random linkage, and creation time—never host input/output or global identity. The
in-process `TelemetryRun` constructor is observer-only and immutable; outcome
authorization additionally requires the exact issued object or the persisted bearer
capability. Replay, mutation, a foreign observer, caller-chosen IDs, symlinks, and
malformed/partial records fail without journaling the supplied value. Interrupted short
journal appends are delimited as malformed standalone records; capability/key writes are
atomic and recoverable; completed records are never truncated or rewritten, and later
valid events remain readable.

The lifecycle commands are intentionally aggregate-only:

```bash
python3 tooling/foundry_cli.py telemetry complete --capability <POST-SPAWN-CAPABILITY> \
  --status completed --failure-class none
python3 tooling/foundry_cli.py telemetry outcome --capability <OUTCOME-CAPABILITY> \
  --tests-state passed --tests-passed 12 --tests-failed 0 --review-state passed
```

The currently supported host boundaries do not deterministically expose actual token
usage, wall duration, packet bytes, files touched, estimated cost, or provider-reported
cost. Those metrics therefore remain null/unknown rather than being sampled or inferred;
the private pending timestamp is used only for expiry, not presented as provider run
duration. Aggregate counters are recorded only when a bearer is safely available and the
caller already knows them. A `fallback_path` containing only the selected first attempt
records zero fallback steps; host override count includes only
`HOST_OVERRIDE_NEUTRALIZES_POLICY` warnings.

There is no upload or raw-event reader. `python3 tooling/foundry_cli.py telemetry export`
returns only low-cardinality sanitized aggregates and never run IDs or metric values.

`routing resolve` also accepts explicit `--tier`, `--model`, and `--effort` requests,
repeatable `--available-model` availability observations, and repeatable
`--host-override` names supplied by a façade. The JSON records every source, complete
fallback candidate list, actually attempted path, gate floor, availability status, and
warning.

## Fallback invariants

- Non-gate roles normally walk downward from their resolved tier and emit
  `MODEL_FALLBACK_DOWN` when they move. An active escalation floor bounds that walk;
  from the floor itself they search upward and never silently undo the escalation.
- `reviewer` has a `frontier` floor and `architect` an `apex` floor. They only walk
  upward, emit `GATE_MODEL_FALLBACK_UP`, and fail loudly when no candidate at or above
  the floor is available. A project or user request below a gate floor is invalid.
- Both gates also have a `high` effort floor. A direct user model is rejected for a
  gate because an arbitrary provider name cannot be classified against its semantic
  floor; request an allowed tier or override that tier's project mapping instead.
  User effort and resolved project/default effort below `high` fail loudly.
- Host overrides do not silently win. A façade detects its host-specific signal and
  passes only its name to `host_override_warnings`; the shared contract emits
  `HOST_OVERRIDE_NEUTRALIZES_POLICY` and never includes the value.

## Deterministic issue escalation

Escalation is a locked, host-shared state machine in
`tooling/foundry/escalation.py`, namespaced by hashed repository identity and issue ID
under the shared Foundry data directory. It never reads agent prose or confidence. The
only failure signals are:

- `test_red`: a test run concluded red;
- `review_blocking`: a reviewer returned a blocking verdict;
- `review_blocking_after_fix`: a correction was reviewed and blocked again.

Two ordinary deterministic failures for the same role raise its minimum tier once. The
`review_blocking_after_fix` signal raises it immediately because the earlier blocking
review and attempted correction already establish the two-attempt sequence. The role's
floor persists for the rest of that issue. A different role has independent counters;
a different issue starts with empty state, so no explicit destructive reset is needed.

Explicit issue/ADR/user metadata may also record a deterministic trigger. Security,
data migration, concurrency, public API, and release impose at least `frontier`; ADR
creation or reconsideration imposes `apex`; an explicit user escalation raises one tier.
Signals are supplied deliberately by the workflow, never inferred silently.

```bash
python3 tooling/foundry_cli.py routing escalation show FOUNDRY-42
python3 tooling/foundry_cli.py routing escalation failure FOUNDRY-42 implementer \
  --kind test_red --current-tier balanced --idempotency-key <STABLE-FAILURE-ID>
python3 tooling/foundry_cli.py routing escalation risk FOUNDRY-42 implementer \
  --kind security --current-tier balanced
python3 tooling/foundry_cli.py routing escalation request FOUNDRY-42 implementer \
  --current-tier balanced
python3 tooling/foundry_cli.py routing escalation resume FOUNDRY-42 \
  --reason remediation_reviewed --halt-generation <HALT-GENERATION-FROM-SHOW>
```

Each failure signal has a stable idempotency key, reused unchanged when the same
effect is replayed. Each decision reports the role, initial/final tier, issue and role
escalation counts, failure counters, and exact deterministic reason. At most two actual
tier increases are allowed per issue. A third required increase, or a failed role
already at `apex`, marks the whole issue `technical_blocked`: both host façades refuse
new delegation and any campaign/provider effect. The only permitted next step is the
bounded local `resume-technical` diagnostic for that exact halt generation; it creates
neither a human authorization nor a campaign restart grant. `human_required` is emitted
only for a typed strategy/product decision or an attested durable ambiguity. State
updates use an OS file lock, so simultaneous Claude/Codex signals cannot lose an
increment.

If a previously rearmed human remediation window was invalidated by a deterministic
signal, its consumed credits, forfeited credits, and audits remain intact and inactive.
Each following `technical_blocked` generation can still open its one exact,
contiguous `resume-technical` diagnostic; this preserves history without reviving that
window or granting a campaign, provider, reservation, PR, CI, or merge effect.

```bash
python3 tooling/foundry_cli.py routing escalation resume-technical FOUNDRY-42 \
  --halt-generation <HALT-GENERATION-FROM-SHOW> \
  --diagnostic-digest <BOUNDED-LOCAL-DIAGNOSTIC-DIGEST>

python3 tooling/foundry_cli.py routing escalation claim-technical-route FOUNDRY-42 \
  reviewer --halt-generation <HALT-GENERATION-FROM-SHOW> \
  --route-id <STABLE-LOCAL-ROUTE-ID>

python3 tooling/foundry_cli.py routing codex-plan reviewer \
  --issue FOUNDRY-42 --technical-remediation \
  --technical-remediation-id <STABLE-LOCAL-ROUTE-ID> \
  --packet-file <BOUNDED-PACKET>
```

After `resume-technical`, ordinary Claude/Codex routes remain refused. The explicit
`claim-technical-route` CAS operation binds the single recorded local diagnostic route
to one stable identifier and its exact stopped role. A retry uses the same identifier;
a second identifier or another role is refused. Only then do the Claude/Codex façades
recognize `technical_remediation=true` plus that identifier. Codex returns a
`local_diagnostic` plan with no `spawn`; the Claude Agent hook refuses the same
envelope because an Agent call would be a provider invocation. The route neither
authorizes nor launches a provider invocation, and it cannot consume or reuse campaign
budget/capacity. A reviewer may consume only its own technical route as a local
diagnostic: this requires no Git diff or review claim and returns no spawn, provider,
campaign, push, PR, CI, or merge authority. The route never finances a review.

After either an exact reviewer or implementer route is consumed, it may unlock one fresh
review only.
Foundry first validates the claim, current Git diff, and immutable root/base coordinates
while holding the issue lock. It then binds that exact hash in the same escalation audit
event. A pending route, a different role or route identity, a stale generation, a
distinct hash, or a coordinate mismatch is refused without a claim or binding. This
never authorizes a provider invocation or campaign effect.

### PAT-10 recovery: separate authorized work from the final cutover

PAT-10 is a final Linear cutover and acceptance issue.  A consumed technical-local
route on PAT-10 is not a deadlock-breaking provider capability: it remains a local
diagnostic receipt and reports `provider_effect_allowed=false` and
`fresh_review_required=true`.  In particular, it cannot be reinterpreted as authority
to implement the Linear adapter, import ADR data, run a migration, or resume a campaign.

The recovery choice is to separate those prerequisites into independently authorized
issues, then keep PAT-10 as the final cutover/acceptance issue.  This follows the
issue-scoped state machine and FOUNDRY-ADR-0001's normal gated pipeline, preserves
FOUNDRY-ADR-0013's bounded coordinator authority, and respects
FOUNDRY-ADR-0014: technical exhaustion is `technical_blocked`, not a fabricated human
verdict.  It also preserves FOUNDRY-ADR-0026: the adapter and the selective import are
pre-cutover work; PAT-10 alone validates and publishes the final repository binding
once its acceptance criteria are independently proven. "Selective" concerns ticket
history, not the required 27-ADR reference corpus. A local or registry binding that
already resolves to Linear is evidence to reconcile, not proof of the final cutover.

Do not add a "fresh capability" transition to PAT-10.  Such a transition would turn a
technical receipt into new provider authority and would bypass the normal per-issue
authorization, review, CI, and human gates.  It is therefore not an implementation
option without a new accepted ADR.

Both options were evaluated against the same frozen recovery input used by the
offline test: PAT-10 at halt generation `1`, local route
`pat10-local-route-0001`, ready diff `a×64`, AC digest `b×64`, and authority
`local_diagnostic` with `provider_effect_allowed=false`. The representative
prerequisite coordinates are PAT-22 generation `1` / diff `c×64` / AC digest
`d×64`, then PAT-23 generation `1` / AC digest `e×64`; these are fixture values,
not claims about the live issues.

| Option on those coordinates | Authority transition | Outcome |
| --- | --- | --- |
| Separate PAT-22 then PAT-23 | Keep the PAT-10 receipt bound to PAT-10 generation `1`, diff `a×64`, AC `b×64`; obtain distinct issue-scoped authority and proof for PAT-22 (`c×64`, `d×64`) and PAT-23 (`e×64`). | Chosen: a failed or stale PAT-10 final claim grants nothing, while the independent migration can resume and produce its own read-back. PAT-10 is reviewed only after those proofs exist. |
| Add a fresh provider capability to PAT-10 | On that same PAT-10 generation `1`, diff `a×64`, AC `b×64`, promote `pat10-local-route-0001` from `local_diagnostic` to provider-write authority. | Rejected: this changes the receipt's authority rather than correcting the exhausted technical route. It would need a new accepted ADR and implementation before it could be considered; none is implied by PAT-21. |

Operator recipe (the coordinator creates and authorizes the follow-up issues; this
recipe performs no tracker/provider write itself):

1. Preserve PAT-10's halted ledger and its local route/audit as evidence.  If a local
   diagnostic is still needed, replay only the identical `resume-technical` inputs and
   route ID for its current halt generation; a stale generation or a new route ID must
   remain refused.
2. Use PAT-22, the bounded adapter implementation issue whose acceptance criteria prove the
   Linear provider surface independently.  Move only the reviewable adapter code from
   the separate PAT-10 checkout into that issue's worktree; do not copy a PAT-10
   technical receipt or claim into it.  Its ordinary implementation, review, CI, and
   merge flow starts under that new issue's authority.
3. Use PAT-23, the bounded ADR-import issue dependent on merged PAT-22.
   Its acceptance criteria name the 27 required ADRs, a stable source snapshot/digest,
   idempotent import behavior, and Linear read-back evidence. Before each Linear write,
   this issue must have its own current authorization and project/operation preflight;
   a PAT-10 technical receipt is not such authorization. It must not publish PAT-10's
   final binding, write both trackers, or migrate terminal ticket history.
4. After both follow-ups have independent review and delivery proof, re-read PAT-10's
   AC and current binding.  Obtain a fresh PAT-10 review claim for the exact current
   diff; changed or stale diffs are refused, and a replay only recovers the same claim.
   Run PAT-10's normal final cutover/acceptance gates then.

What remains is deliberately explicit: PAT-22 and PAT-23 are created in Linear but
remain unstarted; each needs its own authorization and gates. PAT-22 must deliver its
provider proof, and PAT-23 must migrate and read back the 27 ADRs. PAT-10 remains open until those proofs
exist and its own final cutover AC pass.  This recovery guidance does not close PAT-10.

The executable offline recovery recipe is:

```bash
cd plugins/foundry
pytest -q \
  tests/test_escalation.py::test_pat10_recovery_isolated_from_separately_authorized_follow_up
```

It must report `1 passed`. The fixture fixes all comparison coordinates: PAT-10 halt
generation `1`, its local route authority/ID, ready local-diff hash, and AC digest; PAT-22
generation `1`, ready adapter-diff hash, independent delivery authority, and AC digest;
and PAT-23 generation `1`, independent migration authority, and AC digest. It first
proves the actual PAT-10 ledger path (`resume-technical` → atomic local-route claim →
Codex `local_diagnostic` with no spawn), then keeps the ready local diff unable to claim
the PAT-10 final review because PAT-22 delivery and PAT-23 read-back are absent. Only a
stateful provider double may resume the separately pending PAT-23 import with the exact
PAT-22/PAT-23 coordinates and return the fixed 27-ADR read-back. The double records
only `resume` then `read_back`; it receives neither the PAT-10 receipt nor provider
authority from the routing plan.

This is deliberately an offline provider-double proof: it mutates neither a workspace
nor Linear and does not assert that a real migration or final PAT-10 review happened.
Those remain PAT-23's independently authorized provider/read-back evidence and PAT-10's
later exact-diff review/CI/human gates, respectively. Existing exact-diff and AC guards remain separate:
`test_codex_recovered_reviewer_refuses_a_changed_git_diff` and
`test_structured_ac_proof_is_exact_redacted_and_fails_closed_when_stale`.
`test_technical_resume_is_atomic_and_idempotent_under_concurrency` covers replay.
The import issue must still bring its own live authorization and provider read-back
before any real workspace migration.

### Audited human resume

When `escalation show` reports `human_required=true`, only a human may lift that exact
stop. Inspect the issue first, then pass its positive `halt_generation` unchanged to
`routing escalation resume <ISSUE-ID> --halt-generation ...`. The human also selects one
public controlled audit code: `remediation_reviewed`, `risk_accepted`, or
`manual_retry_approved`. A `technical_blocked` halt instead uses the bounded local
`resume-technical` path described above; it never consumes human authority. Never
include secrets, prompts, environment values, provider output, or other sensitive data:
only the selected code is retained.

Resume changes only `halted`, clears its active `halted_reason`, and writes the
version-1-compatible audit fields `resume_count`, `last_resumed_at`,
`last_resume_reason`, and `last_resumed_halt_generation`. `halt_generation` is monotonic:
each distinct new stop increments it, so a delayed or duplicate resume cannot clear a
later stop. It never resets `total_escalations`, failure counters, role floors, or
per-role escalation counts. Thus a subsequent signal that requires an increase after the
issue budget is exhausted halts immediately again as `technical_blocked`.
Older version-1 state files without these fields remain readable; their generation is
reported as 1 when halted and 0 otherwise, then persisted when resumed or halted.

### Bounded remediation window

A human can append `--remediation-credits 1..3` to that CAS-bound `resume`. The ledger
then records only the controlled resume code, timestamps/counters, the exact halted
generation, and the one unambiguous stopped correction role. It exposes
`remediation_authorization.state`, `maximum_credits`, and `remaining_credits` in
`escalation show` and both host plans. Only a subsequent
`review_blocking_after_fix` for that exact role atomically consumes one credit under the
same file lock. It does not renew the two-escalation limit, change a tier floor, or grant
capability.

Every consumption is also appended to the issue-level `consumption_audit`. Each event
has exactly four bounded fields: the fixed consumption `code`, a UTC `at` timestamp, the
controlled correction `role`, and the authorization `halt_generation`. The current
window keeps the exact same events, while the issue-level sequence survives ordinary
resume, replacement by a newly credited window, exhaustion, invalidation, and
cancellation. Events are chronological and cannot contain prompts, prose, diffs,
provider output, secrets, or other free-form input.

`halt_generation` is the identity of the human-resumed stop, not the ordinal
`resume_count`: bounded technical resumes may advance the former without incrementing
the latter. Normalization therefore accepts events only on generations actually cleared
by a human resume and rejects technical-only generations.

An exhausted authorization remains coherent without a halt only at its authorization
generation G, allowing the exact just-exhausted correction/reviewer routing decision.
The next signal stops the issue at exactly G+1. An exhausted window at any other
generation, any other signal/role while credits remain, a stale or malformed ledger, or
`routing escalation cancel-remediation ... --halt-generation ...` fails closed and
requires a human. The whole version-1 ledger envelope is validated before any field is
read or mutated, and `escalation show` is rebuilt only from validated fields. Released
version-1 files with no remediation fields remain readable with an empty durable audit;
unknown fields and hostile values are rejected without being echoed. Skills use this
JSON decision, never review prose, to continue.

The coherent `halted=false`, `state=exhausted` case is rearmed only by a separate human
transition; ordinary `resume` stays restricted to an actual halt:

```bash
python3 tooling/foundry_cli.py routing escalation rearm-remediation FOUNDRY-42 implementer \
  --reason manual_retry_approved --halt-generation <ORIGINAL-G> \
  --remediation-credits <1..3>
```

The positional role must equal the exhausted authorization's recorded role and
`--halt-generation` must equal both its original generation and the current non-halted
generation. The locked CAS refuses active, cancelled, invalidated, malformed, differently
attributed, stale, or already re-halted state without changing the ledger. Success is the
distinct action `remediation_rearmed`, never `resumed`; it replaces only the current
window with a fresh 1..3-credit active window.

Every successful rearm appends one bounded `remediation_rearm_audit` event with the fixed
code `remediation_window_rearmed`, UTC `at`, controlled public `reason`, role, original
halt generation, granted credits, and the exhausted window's `armed_at` plus original
maximum as its link. The previous consumption events stay unchanged in the issue-level
`consumption_audit`; together with the link and rearm timestamp they keep the replaced
window's exact audit interval accessible. Both Claude and Codex plans expose the same
current authorization and rearm audit. The transition does not touch resume counts,
escalation/failure counters, tier floors, limits, tools, providers, network, tracker,
merge, or release authority, and it can never run automatically.

Skills pass the issue ID in the Claude request envelope or Codex `--issue` flag. The
shared resolver applies the stored tier as a non-demotable floor. A direct model or a
lower user tier cannot bypass it, and availability fallback cannot cross it. Escalation
changes only model/effort: agent type, tool capability, read-only status, merge/release
authority, and external-write permissions stay unchanged. The workflow contract caps
concurrency at four live delegated agents. Every shipped skill is tighter: it launches
at most one role at a time and waits for it. Plans expose the cap, and escalation never
increases the number of agents or their authority.

## Cross-host review deduplication

`review_diff_hash` computes SHA-256 over the exact diff bytes. `ReviewDeduplicator`
atomically claims that key under the shared Foundry state directory
(`FOUNDRY_DATA`, otherwise `~/.config/foundry`) in a repository namespace. The first
Claude or Codex process receives `should_run=true`; every later claim for the unchanged
diff receives `false`. A changed diff has a different key and is eligible for review.
There is deliberately no time-based expiry.

Façades must claim immediately before launching the reviewer. The host-neutral record
stores the diff hash, `in_progress`/`completed` state, monotonic generation, current
opaque claim id, timestamps, and recovery audit history; it never stores the diff,
prompt, or model value. Legacy `{diff_hash}` markers migrate fail-closed to `completed`.
Before the first claim, the caller generates and privately retains a fresh 256-bit
attempt token (`secrets.token_hex(32)`). The shared CLI entrypoint is:

```bash
python3 tooling/foundry_cli.py routing claim-review --issue FOUNDRY-42 --git-diff --root <root> --base <base-sha> --claim-attempt-token <claim-attempt-token>
```

It returns `should_run=true` once with `generation` and a private `claim_id`, then
`false` without that capability for the unchanged bytes. The repository namespace
defaults to the Git origin URL and is persisted only as a hash. The issue reviewer route
is preflighted before the diff is claimed. File-supplied diffs are rejected by the
parser before any preflight, claim, authorization binding, or ledger write.

If the process is interrupted after the fresh marker is durably published but before
the JSON result reaches the caller, the exact command may be replayed with the same
secret token and `--replay-interrupted`. Under the ledger lock, that explicit replay
verifies the persisted SHA-256 token digest and reconstructs the same `claim_id` with
HMAC-SHA-256 over the token, diff hash, and immutable coordinates. It performs no write,
so any later replay of the same interrupted delivery returns the same logical owner.
The token itself is never persisted. A normal duplicate without the token and replay
flag never receives an active capability.

For the one-review exception after a consumed implementer or reviewer remediation route, the issue
lock encloses the Git claim validation and the later audit binding. The technical slot is
bound when the review ledger returns a claim that is still `in_progress`. A fresh owner
receives `should_run=true`; an unchanged active owner returns idempotent
`should_run=false`, but still reserves the technical slot so a different diff cannot
create a second active reviewer. This duplicate does not claim that the caller launched
another reviewer and carries no launch capability. Only a claim already `completed`
before technical binding is the harmless `should_run=false` no-op: it does not set
`review_diff_hash`/`review_claimed_at` and leaves the slot available for the one corrected
diff. The same distinction applies to the bounded rearm path after terminal proof. A retry
of an already bound hash can recover an interrupted local handoff; another hash or another
coordinate is refused before a second reviewer can own it.

Ledgers written before this rule may contain a phantom binding to a review that was already
terminal when the technical generation recorded it. Reconciliation is deliberately narrow:
under the same issue lock, Foundry validates the canonical structured proof, issue, diff,
review generation and claim digest, plus the immutable root/base coordinates. The review
ledger's `completed_at` must be strictly earlier than the technical
`review_claimed_at`; equality is ambiguous and fails closed. Only then may an
`in_progress` claim replace the phantom hash, whether it is freshly executable or an
already-active duplicate without a second launch capability. The audit records both timestamps, the
terminal proof id and quality, the old/new hashes, and
`terminal_before_binding_reconciliation`. It is append-bounded to one replacement. A
blocked proof remains blocked and is never promoted to mergeable; it proves chronology only.
Active claims, missing proof, late or ambiguous terminal evidence, coordinate drift,
deduplicated replacement claims, concurrent or second replacements all refuse without a
partial issue-ledger mutation. This repair does not touch escalation/remediation counters,
floors, budgets, providers, campaigns, tracker, PR, CI or merge authority. The ordinary
post-binding rearm remains restricted to an intact mergeable all-pass proof.

Both `foundry:merge-pr` and direct `foundry:review-pr` claim before launching a
reviewer. The nested review receives the already-claimed hash so it does not claim a
second time. If an unchanged diff is already claimed and its verdict is unavailable,
the workflow stops for recovery instead of executing a duplicate review.

Recovery is an explicit human compare-and-swap; it requires the exact hash, the current
active claim capability, the public generation observed by the human, immutable review
coordinates, and a non-empty audit reason. In one ledger-locked section, the CLI verifies
the stored `root`/`base` coordinates, recomputes that exact diff and hash, then creates
the replacement generation. The first concurrent recovery creates a new generation and
claim id; stale attempts, changed coordinates or diff,
incomplete proofs (including a canonical proof left before ledger terminalization), and
completed records are refused. The reason is persisted for audit but not echoed by the
CLI:

```bash
python3 tooling/foundry_cli.py routing recover-review --diff-hash <hash> --generation <n> --claim-id <active-claim-id> --root <root> --base <base-sha> --reason "<human reason>"
```

On Codex, the new owner then presents the returned capability to the reviewer façade:

```bash
python3 tooling/foundry_cli.py routing codex-plan reviewer --issue FOUNDRY-42 --packet-file /tmp/review.txt --git-diff --root <root> --base <base-sha> --diff-hash <hash> --claim-id <new-id>
```

That path recomputes the Git diff hash and checks that the supplied claim is still the
active generation before emitting a frontier/high spawn. Stale owners, completed reviews,
and changed diffs fail closed; callers that do not possess the capability remain on the
redacted deduplication path.

The reviewer reads through `routing read-review --diff-hash <hash> --claim-id <id>
--root <root> --base <base-sha>`.
That command retains the ledger lock from the current active-generation and immutable
coordinate checks through the current diff hash and byte emission, so a recovery cannot
deliver bytes to its replaced owner. It rejects a stale or invented claim. After a
complete verdict, the caller runs `routing record-review-proof` with the exact structured
AC and quality outcomes while the claim is still active. That operation atomically binds
the proof and terminalizes the generation; `complete-review` is a deprecated compatibility
command that fails without reading or mutating the ledger. A
pre-recovery owner cannot bind evidence to the replacement generation. Claude's
packaged reviewer and the portable Codex review skill share these rules.

## Claude Code invocation façade

Claude Code plugin-agent frontmatter is static once the plugin is cached. Foundry never
writes a literal `model:` into an agent definition, because doing so would make the
project policy look configurable while the cached definition kept winning. This façade
is validated against Claude Code 2.1.224; an older runtime without Agent `updatedInput`,
`max_turns`, or agent `effort` support is not compatible.

The runtime path is deliberately explicit:

1. A skill invokes a logical identity: `foundry:lupin`, `foundry:eiffel`,
   `foundry:maigret`, or `foundry:vauban`.
   Their stable semantic routing keys remain respectively `scout`, `implementer`,
   `reviewer`, and `architect`; these keys are the only values accepted by the routing
   CLI, configuration, escalation, and telemetry. The prior Claude identifiers using
   those semantic keys remain deterministic compatibility aliases during the migration.
2. The `PreToolUse` hook on `Agent|Task` loads `.foundry/model-routing.json`, then
   applies user request > project config > Foundry defaults through the common policy.
3. The hook rewrites the actual Agent input with the resolved `model`, a bounded
   `max_turns`, and an internal `foundry:routed-<capability>-<effort>` agent.
4. Claude Code spawns that rewritten invocation. The routing pre-hook's telemetry-free
   `additionalContext` exposes the selected tier/model/effort, sources, fallback path,
   availability status, and warnings; the post-hook emits no context at all.

### Bounded real-smoke harness

For a bounded Claude smoke or reviewer probe, do not pass the global CLI filter
`--tools Agent`. Claude applies that filter to the spawned Agent too, so it removes the
reviewer's guarded `Bash` capability and creates a false routing/review block. The smoke
harness may reduce the system prompt, MCP surface, and budget, but must leave built-in
tools unfiltered. The routed role/profile and its existing guards then enforce the
subagent capability actually under test.

The static/dynamic boundary is therefore:

| Concern | Static in agent frontmatter | Dynamic per invocation |
|---|---:|---:|
| Provider model | never | yes, from resolved policy |
| Reasoning effort | internal profile only | profile chosen from resolved policy |
| Tool capability | read-only or worker profile | profile chosen from role |
| Turn limit | no | yes, caller value capped by role maximum |
| Task context | no | bounded four-section packet |

Effort needs a static internal profile because Claude Code's interactive Agent tool can
override `model` and `max_turns` per call but does not expose an invocation-level effort
field. `max_turns` is the Agent tool wire key; `maxTurns` belongs only to an
AgentDefinition/frontmatter concept and is never emitted in `updatedInput`. The hook reads
the caller value, defaults it to the role ceiling when absent, caps it deterministically,
and rejects non-positive/non-integer values fail-closed. The internal profiles contain no
provider model. Calling one directly is refused by its prompt marker; skills call only
logical roles.

Task packets must contain the literal `Goal:`, `Inputs:`, `Constraints:`, and
`Done when:` sections. The hook rejects missing sections and oversized packets rather
than forwarding complete conversation history. Caps are scout 4,000 chars / 10 turns,
implementer 12,000 / 50, reviewer 16,000 / 24, and architect 10,000 / 30.

An explicit user request may be sent as the strict first line of the Agent prompt:

```text
FOUNDRY_ROUTE_REQUEST={"tier":"frontier","effort":"max"}
Goal:
...
Inputs:
...
Constraints:
...
Done when:
...
```

Only `tier`, `model`, `effort`, and the workflow-supplied `issue` identifier are
accepted. An explicit Agent `model` input is also treated as a user model request;
conflicting values fail loudly. Built-in policy models accept current aliases, canonical
Foundry names, and full IDs (for example `opus`, `opus-5`, and `claude-opus-5`). A custom
canonical model must have a `claude_models` translation, and outgoing Agent input uses
that declared alias.

Claude aliases deliberately follow the current Claude Code CLI version instead of
pinning an exact provider ID. Where supported, administrators can pin the alias targets
with `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
`ANTHROPIC_DEFAULT_OPUS_MODEL`, and `ANTHROPIC_DEFAULT_FABLE_MODEL`. This differs from
Codex's explicit versioned model IDs, which remain pinned by shared policy.

Claude Code does not expose an authoritative, synchronous list of models enabled for the
current organization to a command hook. Set the non-secret CSV
`FOUNDRY_CLAUDE_AVAILABLE_MODELS` (or the equivalent
`CLAUDE_PLUGIN_OPTION_FOUNDRY_CLAUDE_AVAILABLE_MODELS`) when availability is constrained.
Each CSV entry accepts the built-in alias, canonical name, or full ID spellings, plus
custom canonical names declared by the project. The common policy then performs and reports ordinary
downward or gate upward fallback before spawn. Without it, `availability_probed=false`
is visible and an unexpected provider rejection remains a loud Agent failure; Foundry
does not pretend it observed availability.

`CLAUDE_CODE_SUBAGENT_MODEL` has higher host precedence than the injected Agent model,
and `CLAUDE_CODE_EFFORT_LEVEL` outranks subagent frontmatter effort. The alias targets
can also be repointed by `ANTHROPIC_DEFAULT_HAIKU_MODEL`,
`ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, and
`ANTHROPIC_DEFAULT_FABLE_MODEL`. Any of these signals can neutralize the policy. The
façade detects active signals, never reads values into output, and emits one value-free
`HOST_OVERRIDE_NEUTRALIZES_POLICY` warning per signal. Remove the variables to restore
enforcement.

Invalid policy or a gate with no eligible model is an explicit, visible denial. Internal
hook failures remain fail-open as required by ADR-0005: hooks are discipline, never a
security boundary. An unrouted logical entrypoint has only `Read` and self-stops without
the routed marker. Routed read-only profiles omit editing tools; their defense-in-depth
Bash guard allows only the claimed-diff verifier during normal operation.

## Host detector seam

`tooling/foundry/routing_facades.py` supplies the two concrete detection adapters:
Claude detects `CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL`, and the four
`ANTHROPIC_DEFAULT_*_MODEL` alias-target signals listed above; Codex inspects the effective
profile mapping for `model` and `model_reasoning_effort`. They return signal names only.
The shared `host_override_warnings` function owns the value-free warning decision.
Claude's invocation mechanism is described above.

## Codex invocation façade

Codex skills write the same four-section bounded task packet to a scratch file, then ask
the façade for an invocation descriptor:

```bash
python3 tooling/foundry_cli.py routing codex-plan implementer --packet-file /tmp/packet.txt
python3 tooling/foundry_cli.py routing codex-plan scout --packet-file /tmp/packet.txt
python3 tooling/foundry_cli.py routing codex-plan architect --packet-file /tmp/packet.txt
python3 tooling/foundry_cli.py routing codex-plan reviewer --issue FOUNDRY-42 --packet-file /tmp/review.txt --git-diff --root <root> --base <base-sha> --claim-attempt-token <claim-attempt-token>
```

The JSON `spawn` object is the actual host-tool input, not a recommendation. Skills pass
all of it unchanged: `agent_type`, `task_name`, explicit `model`, explicit
`reasoning_effort`, `message`, and `fork_turns="none"`. The explicit model and effort
are important because Codex documents that explicit spawn values outrank
`agents.default_subagent_model` and `agents.default_subagent_reasoning_effort`. A fresh
fork prevents the child from inheriting coordinator turns: only the bounded packet is
passed to it. This also makes the reviewer genuinely blank-context.

`role` remains Foundry's stable permission, routing and telemetry key; `agent_identity`
is its French display identity (Lupin, Eiffel, Maigret or Vauban). Each `codex-plan`
also returns an opaque 16-hex-character `execution_id`. The host-facing `task_name` is
`foundry_<identity>_<execution_id>` (or a valid caller prefix plus that suffix), never
the identity alone. This keeps receipts auditable while making two invocations of the
same logical role distinct in one long Codex task. The generated name is bounded to 64
characters and contains no task content. A residual host collision is a failed/unknown
host invocation to report and retry deliberately; it must never be reclassified as the
current-context fallback or as an independent review.

The portable role mapping is scout→`explorer`, implementer→`worker`,
reviewer→`reviewer`, architect→`default`. The bounded message adds a role contract and
forbids nested delegation. `reviewer` uses Codex's host-enforced read-only reviewer role.
Codex exposes no portable per-call tool restriction for `default`, so the architect's
read-only constraint is prompt-enforced; the architect never writes the ADR itself and
the coordinator remains responsible for materialization.

`FOUNDRY_CODEX_AVAILABLE_MODELS` optionally supplies a non-secret CSV availability
observation. Without it, `availability_probed=false` is explicit. With it, the common
policy emits visible downward fallback for ordinary roles, upward-only fallback for
gates, or a loud failure when no gate-safe model is available.

When Codex exposes an active effective profile, the skill passes presence only via
`--profile-model-active` and/or `--profile-effort-active`; values never enter the plan.
These become the shared `profile.model` and `profile.model_reasoning_effort` warning
signals. Current Codex documentation says the explicit spawn values take precedence
over subagent defaults; the warning remains defense-in-depth for an outer profile,
managed configuration, or host version that neutralizes those explicit values.

Review planning is deliberately coupled to the common ledger. `codex-plan reviewer`
requires a valid `--issue` and `--git-diff`, preflights that issue, resolves the
repository root and base once, requires the caller's fresh `--claim-attempt-token` before
a new claim, then claims those exact bytes. The explicit `--replay-interrupted` path
reruns full packet and route preflight before it reconstructs the interrupted claim. The
reviewer message receives the SHA-256, active claim id, plus those same
value-safe verifier coordinates; `read-review` and `record-review-proof` compare both
coordinates with the persisted claim before reading or terminalizing it, so claim and
verification cannot silently use different worktrees or bases. An arbitrary `--diff-file` or manual
repository namespace is deliberately not accepted on this spawn path. A common verdict
of `should_run=false` produces no `spawn`, so a skill cannot accidentally create the
reviewer before deduplication. In a consumed technical-remediation generation, a completed
duplicate is also a no-op for the issue authorization ledger; an in-progress duplicate is
recorded as the slot owner without granting a second launch capability. Only a later
corrected diff's fresh executable claim can occupy a slot left free by a completed
duplicate.

If the Codex host has no subagent tool, use `--no-subagent`. The plan then has
`mode=current_context`, no `spawn`, usable `current_context_instructions`, and an
explicit disclosure that model/effort application and context independence were lost.
For an already claimed diff it returns `mode=deduplicated` instead: no fallback may run
a second review. Reviewer availability must therefore be determined before its first
plan call; do not claim once and retry with `--no-subagent`.

## CI contract and frozen pilot

The repository CI runs `tests/test_routing_contract.py` as an explicit invocation gate.
For every logical role it verifies the resolved provider model, effort/profile, Claude
turn cap and frontmatter tool boundary, plus the Codex fresh-context descriptor, host
capability role, and injected role contract. It also
proves that provider models and turn caps are absent from agent frontmatter, so project
overrides still reach the actual invocation. The broader routing suites cover mappings,
field-level precedence, host overrides, ordinary and gate fallback, exact-diff review
deduplication, persistent escalation, ceilings, and concurrent counter updates.

The MVP is measured under the immutable manual
[`FOUNDRY-MR-PILOT-v1`](model-routing-pilot-v1.md) protocol. Its separate
[`results sheet`](model-routing-pilot-results-v1.md) fixes the invocation and per-issue
schemas while allowing data cells to be filled. The protocol reprices observed tokens
at the primary model for the counterfactual, separates main-loop/subagent cost, states
the non-equivalence bias, and invalidates the series if its rules change during P-01…P-10.
No routing code reads or writes this sheet, and no automatic usage telemetry is added.

The implementation follows the official Codex documentation for
[subagents](https://developers.openai.com/codex/multi-agent),
[configuration precedence](https://developers.openai.com/codex/config-reference), and
[GPT-5.6 model selection](https://developers.openai.com/api/docs/guides/latest-model).
