# Foundry

**The foundation.** A Claude Code + Codex plugin that industrializes how a project goes
from a rough idea to shipped work — the same, repeatable, on every project:

> idea → **guided brainstorm** → **ADRs + precise issues** → **value-first roadmap** →
> **gated execution loop** → **intake** of new ideas — without ever re-validating what's
> already settled.

Foundry owns the **outer loop** (what to do, the source of truth, the decision memory,
the exit gates). The **inner loop** (design → plan → implement) is delegated to Superpowers
or Plan mode. Its gated PR lifecycle uses YouTrack, DevHubTracker, or a Linear
append-only proof projection with the GitHub **code-host** adapter. Linear never replaces
an existing issue field without provider CAS: Foundry derives lifecycle state, PR link
and reviewed AC from deterministic comments while Linear's native fields stay unchanged.
Switching to another tracker or code-host later remains an adapter change, not a rewrite.

## Why

The skills this replaces were *too binary*: they sorted and counted, they didn't reason —
because the judgment was baked into Python and the skill just relayed a verdict. Foundry
inverts that:

- **Query tier** (`foundry.query`) returns rich, normalized JSON — the whole graph
  (links, AC, milestone rollups, ADRs). Zero decisions. List payloads are **lean**
  (no bodies): the signal for reasoning is always there, the free text loads on
  demand (`query issue <ID>`, `query adr <ADR-ID>`) — retrieval stays constant-cost
  whatever the backlog size.
- **Skills reason** over that JSON and recommend, with justification. The deterministic
  priority rank is *one signal they can override*, not the answer.
- **Write tier** (`foundry.write`) holds the invariants that must never be skipped — most
  importantly, **CI-green-before-merge is enforced in code**, not in prose. Green means
  *proven* green (ADR-0002): the gate reads check-runs **and** the legacy commit-status
  API, requires at least one real `success`, refuses zero checks or all-`skipped`
  (override only with an explicit `--allow-no-ci` when both sources are empty and
  nothing signals active CI for the commit), and the merge is pinned to the exact sha
  the gate verified.

And the mechanism that stops re-litigating decisions: **retrieval before reasoning** —
every skill that could reopen a question loads the accepted ADRs first and treats them as
settled unless explicitly superseded.

## Install or upgrade

Fresh Claude Code install:

```
/plugin marketplace add patobiskoto/patolabs-plugins
/plugin install foundry@patolabs
```

Upgrade an existing Claude Code installation, then reload plugin components so hooks and
other long-lived integrations switch to the new cache:

```
/plugin marketplace update patolabs
/plugin update foundry@patolabs
/reload-plugins
```

Fresh Codex install:

```
codex plugin marketplace add patobiskoto/patolabs-plugins
codex plugin add foundry@patolabs
```

Codex refreshes a Git marketplace snapshot separately from its installed plugin cache.
Upgrade and reinstall Foundry, then start a new Codex task:

```
codex plugin marketplace upgrade patolabs
codex plugin remove foundry@patolabs
codex plugin add foundry@patolabs
```

After either installation, run `/foundry:configure` and `/foundry:doctor` in Claude Code,
or `$foundry:configure` and `$foundry:doctor` in Codex. Non-secret settings are shared in
`~/.config/foundry/config.env`; credentials belong in the macOS keychain (or a secret
manager-backed `YOUTRACK_TOKEN` / `LINEAR_API_TOKEN` on other systems), never in a
repository or chat. Both runtimes also accept explicit provider and `FOUNDRY_*`
environment variables. Linear's exact capability and binding contract is documented in
[`docs/linear-tracker.md`](docs/linear-tracker.md).

The current dual-host upgrade, trusted configuration, override diagnosis, verification,
and exact-ref rollback are in
[`docs/migration-0.8.0.md`](docs/migration-0.8.0.md). The former
[`0.7.0 migration`](docs/migration-0.7.0.md) remains historical release evidence.
Foundry never installs, downloads, starts, or selects a local model during an upgrade.

Dev Hub Epic orchestration starts with a deterministic read-only preview. It consumes
the provider's complete versioned subgraph, resolves the effective Foundry routing and
limits, and emits classifications, human gates, dependency waves, explicit unknowns and
a stable digest without acquiring execution authority. The API and command contract are
documented in [`docs/epic-preview.md`](docs/epic-preview.md).

Approved Dev Hub commands are consumed by an outbound, lease-based worker with strict
identity recovery, durable monotonic receipts and an atomic host-shared reservation of
budget and concurrency before effect. Its authority snapshot and operator entry point are documented in
[`docs/devhub-command-worker.md`](docs/devhub-command-worker.md).

### After merging this change

Reinstall/refresh from the merged marketplace snapshot; do not edit an installed cache.
The cachebuster commands below are only for a local source-development marketplace and
are not part of a published 0.8.0 upgrade or rollback.

```text
# Claude Code
/plugin marketplace update patolabs
/plugin update foundry@patolabs
/reload-plugins

# Codex, when the marketplace entry points at this local checkout
python3 ~/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py \
  /absolute/path/to/claude-plugins/plugins/foundry
python3 ~/.codex/skills/.system/plugin-creator/scripts/read_marketplace_name.py
codex plugin add foundry@<marketplace-name-printed-above>
```

If Foundry is installed from a non-local marketplace, update that marketplace through its
normal release flow, then run `codex plugin add foundry@<marketplace-name>`. Start a new
task after the Codex command so it loads the merged plugin version.

Skills keep one shared implementation. Use `/foundry:<skill>` in Claude Code and
`$foundry:<skill>` in Codex.

## The pipeline

| Skill | Phase |
|---|---|
| `foundry:configure` | configure the runtime without exposing secrets in chat |
| `foundry:frame` | genesis — guided brainstorm → materialize ADRs + epic + precise issues |
| `foundry:roadmap` | sequence the smallest slice that proves value |
| `foundry:next-issue` | pick the most valuable unblocked issue (reasoned) |
| `foundry:start-issue` → `foundry:open-pr` → `foundry:merge-pr` | gated execution |
| `foundry:close-epic` | provider-audited closure of a completed non-code Epic, without a PR |
| `foundry:resume-issue` | resume mid-flight work from tracker notes + git state |
| `foundry:intake` | new idea → issue / refine / ADR / rejected-by-ADR |
| `foundry:groom` · `foundry:blockers` | backlog health · what's stuck |
| `foundry:adr` · `foundry:doctor` | record decisions · check wiring |

The 0.8.0 merge gate can consume a structured reviewer proof when tracker AC remain
unchecked. `routing record-review-proof` binds ordered AC outcomes and the quality
verdict to the current issue AC digest, local `HEAD`, exact diff, immutable PR base SHA,
active claim, and review generation before merge. Stale-base, malformed, mixed, or
changed-diff evidence fails closed. The proof never edits tracker AC, and the explicit
human incomplete-AC override remains separate and audited.

## Model-aware delegation

Delegated roles resolve through one shared semantic policy: `economy`, `balanced`,
`frontier`, and `apex`. Project overrides live in `.foundry/model-routing.json`; an
explicit user request wins over that file, which wins over Foundry defaults. Reviewer
and architect gates only fall back upward and fail rather than silently downgrade.

| Agent identity | Stable semantic role key | Default tier | Purpose and capability |
|---|---|---|---|
| **Lupin** (`foundry:lupin` in Claude) | `scout` | `economy` | bounded repository exploration, read-only |
| **Eiffel** (`foundry:eiffel` in Claude) | `implementer` | `balanced` | one bounded implementation loop, worker tools |
| coordinator | `coordinator` | `balanced` | main conversation/orchestration; not delegated by the façades |
| **Maigret** (`foundry:maigret` in Claude) | `reviewer` | `frontier` minimum | blank-context merge gate, read-only |
| **Vauban** (`foundry:vauban` in Claude) | `architect` | `apex` minimum | explicit ADR decision support, read-only contract |

The semantic keys are deliberately stable: routing configuration, escalation, telemetry,
permissions, and Codex host-role selection continue to use `scout`, `implementer`,
`reviewer`, and `architect`. Claude's logical identities and Codex task names use Lupin,
Eiffel, Maigret, and Vauban. For a non-breaking transition, the former Claude logical
identifiers (`foundry:scout`, `foundry:implementer`, `foundry:reviewer`, and
`foundry:architect`) are deterministic aliases to those identities; new source calls use
the identities above. The frozen FOUNDRY-35 local-fallback plan is the sole internal
compatibility exception: it retains `foundry:scout` so its historical evidence hashes
remain verifiable, and the hook resolves it to Lupin's unchanged `scout` contract.

For ordinary feature/fix/chore work, use a **balanced primary profile** for the main
coordinator: Sonnet 5 / medium on Claude Code or GPT-5.6 Terra / medium on Codex. Move
the main loop to frontier only when it must absorb a disclosed no-subagent fallback or
coordinate genuinely cross-cutting risk. Keep apex for explicit architecture decisions.
This is guidance, not enforcement: Foundry controls delegated invocations but cannot
select the model of the existing conversation.

A minimal project override is:

```json
{
  "version": 1,
  "roles": {
    "implementer": "frontier"
  }
}
```

Copy the committed [`examples/model-routing.json`](examples/model-routing.json) to
`.foundry/model-routing.json` and adjust only the roles or mappings the project needs.
Built-in Claude mapping models accept current Agent aliases, canonical Foundry names, or
their current full Claude IDs. Projects may declare additional canonical-model-to-Agent-
alias translations with `claude_models`; the façade resolves them before invocation.

Inspect the effective, read-only policy with
`python3 tooling/foundry_cli.py routing show`. The full schema, fallback guarantees,
cross-host diff-hash review deduplication, and host-override warning contract are in
[`docs/model-routing.md`](docs/model-routing.md).

The optional local diff/code preprocessor is deliberately outside that routing policy: it is
disabled by default, produces evidence-bound proposals only, and never acts as a gate or
cloud fallback owner. Its two-pass code mode exposes only a body-free manifest on the
first call, then wrapper-owned frozen evidence on the second. Its trusted configuration,
hard network/data bounds, and shared Claude/Codex `local-scout` / `local-code` commands are documented in
[`docs/local-scout.md`](docs/local-scout.md).

The candidate-agnostic, controlled local-preprocessor campaign lives in
[`benchmarks/foundry-35`](benchmarks/foundry-35). It is evidence only: it cannot
activate a local mode, influence routing, or confer gate authority. Its completed v4
campaign, tracked raw evidence, historical frozen aggregate, post-run addendum,
attestation, and fail-closed recommendation are indexed by
[`FINAL-RECOMMENDATION-v4.md`](benchmarks/foundry-35/FINAL-RECOMMENDATION-v4.md).
The immutable-record versus corrected-publication boundary, raw-trace linkage, packet-null
semantics, and offline reproduction command are specified by the versioned
[`RESULTS-CONTRACT-v4.md`](benchmarks/foundry-35/RESULTS-CONTRACT-v4.md).
The current [`0.8.0 release notes`](docs/release-0.8.0.md) publish the separate
FOUNDRY-46 Claude and Codex evidence and the FOUNDRY-47 `keep` decision. The frozen v2
matrix is `inconclusive` on both hosts: unavailable cost, allocation, test, review,
downstream, security, host-version, and weighted-aggregate data remain `null`, never
zero. The historical 7.42% price-snapshot observation is noncausal and is not a savings
claim. No local model is promoted, and every production resolver, default, gate,
fallback, mapping, and authority remains unchanged. The
[`0.7.0 release report`](docs/release-0.7.0.md) remains immutable historical evidence.

On Claude Code, skills invoke logical roles and a `PreToolUse` hook applies the resolved
model, effort profile, capability, and turn cap to the actual Agent call. Agent
frontmatter never fixes a model, so committed project overrides remain effective despite
Claude's static plugin cache. Bounded task packets keep subagents from inheriting the
whole main loop.

On Codex, `routing codex-plan` produces the exact host subagent descriptor. Skills pass
its explicit model/effort and `fork_turns=none` unchanged, so Lupin, Eiffel, Maigret,
and Vauban start with the resolved policy and a bounded fresh context. The Maigret plan
claims the common diff hash before spawn and returns no invocation for an
already-owned diff. Hosts without subagents use a disclosed current-context fallback
instead of silently claiming independence.

The French name is a logical identity, not the long-lived Codex task path. Every Codex
descriptor carries that identity plus an opaque bounded execution suffix in `task_name`;
two Eiffel invocations therefore never intentionally reuse `foundry_eiffel`. A host-side
collision remains an explicit failed/unknown host result, never evidence of an
independent completion or a reason to silently fall back to the coordinator context.

Escalation is deterministic and issue-scoped. Only red tests, blocking reviews, or an
explicit risk/ADR/user signal can raise a persistent role floor; free-form agent
judgment never increments it. Two tier increases are allowed per issue; a later
deterministic failure at Apex halts into a bounded local technical diagnostic, not an
automatic human verdict or provider retry. A human may explicitly resume a genuine
human-required stop at the exact halted
generation returned by `routing escalation show` with `routing escalation resume
<ISSUE-ID> --reason remediation_reviewed --halt-generation <N>`. Only the controlled
public codes `remediation_reviewed`, `risk_accepted`, and `manual_retry_approved` are
stored; never include a secret. Resume is audited but does not renew escalation budget
or lower a floor, so the next exhausted-budget signal halts again with a new generation.
A just-exhausted credit window that still has `halted=false` is never a successful normal
resume. A human may instead run the dedicated CAS command `routing escalation
rearm-remediation <ISSUE> <RECORDED-ROLE> --reason <PUBLIC-CODE> --halt-generation
<ORIGINAL-G> --remediation-credits <1..3>`. If a later local diagnostic has advanced
the current generation to `H`, the same command must also pass
`--current-halt-generation <H>`; `H` must be non-halted and backed by a claimed local
route for the same role. The original exhausted authorization still binds `G`; the
route alone never grants a credit. The command returns `remediation_rearmed`, preserves
all counters, floors and consumption events, and appends a bounded public
`remediation_rearm_audit` link to the exhausted window. Both host plans expose the same
authorization and audit; stale, active, cancelled, invalidated, differently attributed,
or malformed state is refused atomically.
A new issue starts from its normal resolved policy. Model escalation never expands tools or
merge/release/external-write authority.

Host limits are intentionally visible:

- **Claude Code:** plugin frontmatter is cached and static, so model and the Agent-wire
  `max_turns` are injected by the routing hook; `maxTurns` is frontmatter/AgentDefinition
  terminology and is never an `updatedInput` key. Claude receives a declared Agent alias;
  built-in policy accepts aliases, canonical names, and full IDs, while custom models use
  the project's `claude_models` declaration. Where supported, `ANTHROPIC_DEFAULT_HAIKU_MODEL`,
  `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, and
  `ANTHROPIC_DEFAULT_FABLE_MODEL` can pin alias targets; Codex model IDs remain explicitly
  version-pinned. Effort is
  selected through a static effort-only internal profile. Hooks require local trust,
  remain fail-open on internal errors, and host-wide model/effort or alias-target
  environment overrides can neutralize the policy; Foundry detects all six signals and
  warns without exposing their values.
- **Codex:** explicit `model`, `reasoning_effort`, and `fork_turns=none` are passed to the
  subagent call. Codex exposes no portable per-call turn cap, and no portable tool mask
  for the `default` architect role, whose read-only constraint is therefore prompt-level.
  Without a subagent tool, Foundry discloses that model application and blank-context
  independence are lost before using the current context.

The ten-issue measurement uses the frozen manual
[`FOUNDRY-MR-PILOT-v1`](docs/model-routing-pilot-v1.md) protocol and its separate
[`results sheet`](docs/model-routing-pilot-results-v1.md). It records aggregate usage and
cost only. Passive local telemetry is opt-in through `FOUNDRY_DATA`: it writes a
private append-only journal under `$FOUNDRY_DATA/telemetry`, never uploads, never
changes routing, and records no prompts, responses, code, paths, URLs, persistent
identifiers, errors, or secrets. Each linked pair uses an observer-generated ephemeral
one-time capability. Claude completion is observed privately by a completely silent
post-Agent hook: it records only `completed/none` for `PostToolUse` or `failed/host` for
`PostToolUseFailure`, emits no context/bearer/instruction, and therefore cannot safely
correlate a later aggregate outcome. Codex skills consume the plan's out-of-band
capability only after a real subagent returns and must explicitly classify success, host
failure, timeout, cancellation, or an untrustworthy/unknown return; current-context
fallbacks have no completion boundary and are not recorded as completed. Codex may then
consume the returned outcome capability after aggregate results are known. All pending,
link, and outcome state is private, atomically bounded to 256 per class across processes,
and refused/removed at consume when its persisted creation time is at least 24 hours old.
An Agent permission denial can leave only a bounded private pending callback; without a
post-tool event Foundry infers no outcome, and normal 24-hour cleanup removes it.
The current host boundaries expose no deterministic actual usage, duration, packet size,
files-touched, or cost values; those fields stay null/unknown rather than fabricated.
State is absent when telemetry is disabled. Export only sanitized aggregates with
`python3 tooling/foundry_cli.py telemetry export`.

### Offline cost attribution

cost-attribution is a separate, explicit, post-hoc reader for a host JSONL log. It
does not enable telemetry, call a provider, resolve routing, update the tracker, or
write a journal. It extracts only the canonical model, event date, and input, cached
input, cache-write input, output, and reasoning-output counters; reasoning remains a
subset of output. It returns aggregates and never returns prompts, responses, source
paths, filenames, code, commands, test output, or secrets.

It streams the caller-selected JSONL and fails closed when a native usage event lacks
its required counter, date, model, or session identity; it never turns such a record
into an apparent zero-cost session. Claude keeps a request billable when its reasoning
counter is absent or exceeds output: only `reasoning_output_tokens` becomes
`unavailable`, and a mixed group propagates that value instead of inventing a partial
total or zero. `reader_completeness` reports the billable-record count plus typed,
content-free counts for missing and incoherent reasoning. `<synthetic>` Claude rows are
excluded separately as non-API records and do not degrade reasoning completeness.
Present-but-malformed counters still fail closed, and a Claude log with no billable
record fails explicitly. Missing pricing still makes cost unavailable independently of
reader coverage. Codex selects one cumulative counter series for the entire session:
`total_token_usage` when present, otherwise `thread_token_usage`. It detects resets and
computes deltas on the raw native counters before splitting them into model/date buckets.
Because native `input_tokens` includes cached input, the reader subtracts
`cached_input_tokens` from `input_tokens` and charges the cached category once.
Request-level usage is used only when neither cumulative series is present.
`turn_token_usage` alone cannot attest a request or session total; without a session
cumulative counter, such incomplete usage is explicitly rejected as unavailable.
Compatible cumulative data can still produce totals, but request positions and
context-dependent costs remain `unavailable`
when the required granularity is absent.

The public price grid is versioned and dated. A session selects the effective entry
for its date by exact model or declared prefix alias. Missing prices retain the observed
model and report cost as unavailable, never zero. The output lists those observed,
unpriced models alongside the model vocabulary derived from the local grid; it makes no
network scrape or discovery call. Context-threshold entries expose their separately
calculated excess cost and every aggregate keeps the applied tier(s). Project attribution
requires an exact registered Claude path slug or Codex `session_meta.cwd` in an explicit
`--registry-snapshot`; unresolved
input remains unavailable. The snapshot is an input to replay, so changing the live
registry cannot change a historical result. Epic and issue
fields are supported aggregation dimensions but are intentionally not inferred from
host logs: pass an explicit local session-attribution JSON when they are known. A saved
`/v1/models` response can also be supplied to list unpriced observed models; the reader
never performs that request itself. Subscription entries report API-equivalent amounts
with `billing_mode: subscription`, never as an invoice.

The additive `session_curves` output exposes observed per-request ranks, incremental
costs and cumulative costs; cumulative-only host data remains explicitly unavailable for
that curve while its compatible total remains available. `--session-comparisons` accepts
caller-declared opaque equal-work groups to compare one observed long session with an
ordered set of observed short sessions, including their actual restart cost. It never
simulates a split. Codex may retain a valid plan-window percentage without plan, limit or
credit metadata; Claude reports it as unavailable and those snapshots are never summed or
averaged. Quota-only notifications remain separately dated metadata: they never create a
priced model/date row, reuse cumulative tokens, count a cost session, or make a cost curve
incomplete. Primary and secondary windows remain distinct, and a repeated cumulative
token snapshot makes only the position curve unavailable. See [the cost-attribution contract](docs/cost-attribution.md).

## Architecture

```
skills/            reasoning (judgment)  — call query, reason, propose writes
tooling/foundry_cli.py   launcher the skills invoke (python3 <cli> <module> …) —
                   commands must START with python3 to match allowed-tools rules
tooling/foundry/
  query.py         query tier: rich JSON, zero decisions
  write.py         write tier: setters + invariants (CI gate, legal states)
  frame.py issue.py edit.py adr.py …   CLI entrypoints (python -m foundry.<x>)
  routing.py       shared semantic model policy, fallbacks, override warnings,
                   and cross-host review deduplication
  escalation.py    locked per-issue counters, risk floors, ceiling, and human stop
  trackers/        Tracker port + youtrack/devhub (real) + ghprojects (stub)
  codehosts/       CodeHost port + github (real)
  registry.py      repo → project map (runtime data dir, then ~/.config/foundry)
  config.py        env → Claude options → keychain → config-file fallback
hooks/             SessionStart + PreToolUse workflow guards and Claude logical-agent
                   routing (dynamic model/turns, static effort profiles)
agents/             Claude logical roles + internal read-only/worker effort profiles;
                   no agent frontmatter fixes a provider model
skills/review-pr/  portable two-stage review contract (AC compliance, then quality)
docs/adr/          Foundry's own ADRs (it's piloted with its own method)
docs/model-routing-pilot-*.md   frozen manual measurement protocol + results sheet
```

## Providers (pluggable)

Active tracker/code-host are **parameters** (`FOUNDRY_TRACKER` / `FOUNDRY_CODEHOST`), not
constants. `trackers/base.py` and `codehosts/base.py` are the ports. YouTrack and
DevHubTracker v1 are real adapters; `trackers/ghprojects.py` remains a deliberate stub.
See [`docs/devhub-tracker.md`](docs/devhub-tracker.md) for the isolated pilot cutover.

Every Tracker implements the common normalized issue and ADR read/write surface.
Capabilities that are not universal stay explicit and default-off on the port. Project
provisioning is one such capability: YouTrack and DevHub implement it; the GitHub
Projects stub refuses it before any provider or registry mutation. The setup command
never branches on a concrete provider.

## Provision a project

```
python3 tooling/foundry_cli.py setup_project "My Project" MYKEY <repo-basename> \
    [--import-adrs docs/adr]
```
Asks the configured Tracker to create or recover its project, registers the normalized
result, and optionally replicates the repo's bootstrap ADRs into the tracker KB. YouTrack
preserves its Foundry field-vocabulary setup. DevHub discovers the credential-free
canonical identity from the current checkout's `origin`, creates the minimal
repository-bound project, and records that identity without a manual REST step. A
provider without the optional capability fails with recovery instructions before any
mutation. Retrying after an interruption recovers the same provider project and replaces
the same single registry entry rather than creating duplicates.
If an existing project moves to a repo with a different basename, preserve the old
binding and add the new name without provisioning another tracker project:

```
python3 tooling/foundry_cli.py registry alias youtrack <old-repo> <new-repo>
```

ADR bootstrap import is keyed by the required `<KEY>-ADR-NNNN` ID in each filename,
not its mutable title, and refuses an unnumbered file or numbering gap before writing
anything.

## Develop Foundry (dogfooding)

Foundry is piloted with its own tools (project `FOUNDRY`). In-repo, config falls back to
`~/.config/foundry/config.env` (or the legacy `~/.config/orfeo-poc/youtrack.env`), so the
tooling runs without installing the plugin:

The destructive real-provider integration is separate and explicitly gated. See
[`docs/youtrack-smoke.md`](docs/youtrack-smoke.md) for its disposable-project guards,
runner variables, command, and idempotent cleanup contract.

```
PYTHONPATH=tooling python3 -m foundry.doctor
pytest -q -m "not integration" tests  # pure-logic tests, no network; benchmark/POC tests excluded, see below
pytest -q tests/test_routing_contract.py  # resolved host invocation + pilot contract
```

Benchmark-campaign and proof-of-concept tests (evidence under `benchmarks/`) carry the
`benchmark_campaign` marker and are excluded from the default run above. See
[`docs/benchmark-campaign-tests.md`](docs/benchmark-campaign-tests.md) to replay them and
for why they are out of the default suite (FOUNDRY-ADR-0019).

The pipeline invariants are enforced by **plugin hooks** in registered repos: a
`SessionStart` hook injects the Foundry contract into every session, and `PreToolUse`
hooks route logical Claude agents and deny `gh pr create` / `gh pr merge` and direct
pushes to the default branch
(pointing to the `foundry:open-pr` / `foundry:merge-pr` skills instead). The workflow
guard is a no-op outside registered repos and routing ignores non-Foundry agents. The
hooks remain fail-open on internal errors per ADR-0005; explicit policy violations can
still be denied. Logical entrypoints have a minimal `Read` capability and self-stop if
routing did not attach its marker. Codex discovers the same `hooks/hooks.json`; users
must still approve/trust hooks according to their local Codex policy. In Codex linked
worktrees, `start-issue` creates the issue branch from the default branch without trying
to check that default branch out in the worktree, and `merge-pr` delegates local worktree
cleanup to Codex. Belt-and-braces for pushes made outside either agent:
a `git` pre-push hook ships in `.githooks/` — enable it with
`git config core.hooksPath .githooks`.
