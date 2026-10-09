# Process contract & documentation doctrine

This file is the versioned process contract for the `claude-plugins` monorepo. It
implements the "referential" half of FOUNDRY-ADR-0018: the contract that used to live
only in host configuration on one machine now lives in the repository, so it is
identical for a fresh clone with no user configuration (no `~/.claude`, no
`~/.config/foundry`), for Claude Code, and for Codex.

**Dual-host, single source.** Per FOUNDRY-ADR-0005, Claude Code reads `CLAUDE.md` and
Codex reads `AGENTS.md`. This repository keeps one implementation of the contract: the
two files are byte-identical, enforced by
`plugins/foundry/tests/test_process_contract.py`. Edit this file (`AGENTS.md`); copy the
result verbatim into `CLAUDE.md`. Never let the two diverge.

**Accepted ADRs are settled.** This contract states operating rules and points at the
ADR that decided them; it does not re-argue or restate any ADR decision. Load the full
text before touching an area an ADR governs:

```
python3 plugins/foundry/tooling/foundry_cli.py query adrs
python3 plugins/foundry/tooling/foundry_cli.py query adr <ADR-ID>
```

Rules below are numbered (`R1`, `R2`, …) so a review finding can cite one precisely,
e.g. "violates AGENTS.md#R1" — this is the concrete anchor the `foundry:maigret`
reviewer contract's "project conventions (AGENTS.md/CLAUDE.md)" criterion checks
against.

## R1 — PR and merge only through the Foundry skills

Every PR opens through `foundry:open-pr` and merges through `foundry:merge-pr`
(`/foundry:<skill>` in Claude Code, `$foundry:<skill>` in Codex) — never `gh pr create`,
`gh pr merge`, or a direct push to the default branch. In a repo where the Foundry
plugin is active, this is not just policy: `gh pr create` / `gh pr merge` and a direct
push to the default branch are denied by the `PreToolUse` Bash guard
(`plugins/foundry/hooks/guard_bash.py::deny_reason`), and a git `pre-push` hook in
`plugins/foundry/.githooks/` gives the same guarantee outside either agent (enable it
with `git config core.hooksPath plugins/foundry/.githooks`). See FOUNDRY-ADR-0001 for
why the tracker must stay authoritative over the merge path. The guard's usual fail-open
on internal errors has one exception (PAT-42): an invalid, drifted, or ambiguous repo
tracker binding (`registry.entry_for()` raising `ValueError`) still denies these same
commands, naming the binding error, instead of silently reopening the guard in exactly
the state where every other Foundry command already fails closed.

## R2 — Scan the ADR index before an architecture choice

Before deciding anything that shapes architecture, scan the ADR index and load the full
text of any ADR that touches the subject:

```
python3 plugins/foundry/tooling/foundry_cli.py query adrs
python3 plugins/foundry/tooling/foundry_cli.py query adr <ADR-ID>
```

Treat every `accepted` ADR as settled: do not re-litigate it. Supersede a decision only
through an explicit new ADR, never through silent drift in code or in this file. This is
FOUNDRY-ADR-0001's retrieval-before-reasoning mechanism.

## R3 — An idea in flight goes through intake, not straight to code

A new idea that surfaces mid-task (scope not already covered by the current issue's
acceptance criteria) goes through `foundry:intake` — never straight into an
implementation diff. `foundry:intake` decides whether it becomes an issue, a refinement,
an ADR, or is rejected against an existing ADR.

## R4 — CI green means proven green, on two sources

A reviewer or a merge decision may cite "CI is green" only when it is proven green per
FOUNDRY-ADR-0002: the merge gate reads both check-runs and the legacy commit-status API,
requires at least one real `success`, and refuses zero checks or an all-`skipped` run.
The only override is an explicit, audited `--allow-no-ci`, and only when both sources are
empty and nothing signals active CI for the commit. The gate pins the merge to the exact
sha it verified. This is enforced in `plugins/foundry/tooling/foundry/write.py`, not in
prose.

## R5 — Documentation doctrine (FOUNDRY-ADR-0018)

FOUNDRY-ADR-0018 decided that documentation is judged by explicit status per touched
documented artifact, not by the presence of a diff under `docs/`. The operating rule
this contract adds:

- **What must carry a status**: CLI verbs/options, configuration keys, public constants
  and vocabularies (for example the Claude model translation table in
  `plugins/foundry/tooling/foundry/routing_facades.py`), operational constraints (for
  example the OAuth machine-binding constraint in R6), symbols already cited in
  `docs/`, and surfaces a skill describes to a caller.
- **Where**: the relevant file under `plugins/foundry/docs/` (or this contract, for a
  process/operating rule with no natural docs page), plus a one-line pointer in the PR
  or issue saying what was updated.
- **What counts as an acceptable "not necessary"**: a reason tied to the specific
  candidate artifact — e.g. "internal refactor, no public constant/CLI surface
  changed" — never a generic or empty reason, and never a blanket "not necessary" that
  covers an artifact the reviewer can independently show is real and undocumented.
- **Current enforcement state, honestly**: the deterministic candidate detector and the
  merge-time status gate described by FOUNDRY-ADR-0018 are a separate, not-yet-shipped
  issue (FOUNDRY-123). Until it ships, this status is asserted in the PR description and
  checked by the reviewer against this rule, not machine-enforced. Do not claim
  mechanical enforcement that does not exist yet — that is exactly the false-convention
  failure FOUNDRY-ADR-0018 exists to prevent.

## R6 — The Claude Code OAuth identity is bound to the machine (FOUNDRY-101)

Claude Code's OAuth token refresh depends on a stable local identity: the child process
Foundry launches for a Claude session only forwards a fixed allow-list of environment
variables — `HOME`, `LANG`, `LC_ALL`, `LOGNAME`, `PATH`, `TMPDIR`, `USER`
(`plugins/foundry/tooling/foundry/command_runtime.py::_CLAUDE_CHILD_ENV_ALLOWLIST`,
FOUNDRY-101). Strip or spoof those, and the host's OAuth refresh can fail.

**Operational consequence**: any scheduler or runner that drives a Claude Code host
session — including the deterministic cadencer of FOUNDRY-ADR-0017 — must run on the
same authenticated developer machine, with that machine's real user/session identity
intact. It cannot be relocated to an arbitrary remote or ephemeral CI runner without
first re-establishing that OAuth identity there. FOUNDRY-ADR-0017 names this constraint
explicitly as the reason its cadencer runs on the development machine rather than a
remote server.

## R7 — Claude model resolution at CLI boundaries (current state)

FOUNDRY-100 introduced translation of Claude models at CLI boundaries; FOUNDRY-125
opened what was originally a closed, fixed vocabulary. Document the **current**
behaviour, not the pre-FOUNDRY-125 closed table:

- The accepted effort levels for a Claude route are not a hardcoded tuple: `EFFORTS` is
  derived from the declared scope,
  `DEFAULT_EFFORT_SCOPES["claude"]["default"].levels`
  (`plugins/foundry/tooling/foundry/routing.py`), itself validated from
  `plugins/foundry/tooling/foundry/effort_policy.py`.
- `_CLAUDE_MODEL_DECLARATION` declares canonical versions and their explicit Agent
  identifiers, including historical Sonnet/Opus/Fable 5, Sonnet/Opus 5.5, Fable 5.1,
  Haiku 5.5 and Haiku 4.5 (including its explicit `20251001` snapshot). Versioned policy models
  select a preloaded versioned profile whose frontmatter carries the declared full
  identifier. The Agent tool wire omits `model` for these pins: the observed 2.1.285
  Agent schema accepts only `haiku`, `sonnet`, `opus`, `fable` there. Historical pins
  never become latest aliases.
  Short `haiku`, `sonnet`, `opus`, `fable` aliases preserve host alias intent and emit
  `CLAUDE_ALIAS_VERSION_UNOBSERVED`; they are not evidence of a precise version.
- `claude_invocation_model()` normalizes full IDs to declared canonical names with
  `claude_policy_model()`, resolves a built-in explicit ID or short alias, and otherwise
  uses the project's own `claude_models` declaration. A version pin and a short alias are
  distinct availability identities; an alias observation cannot satisfy a pinned version.
  `claude_invocation_binding()` binds declared pins to preloaded static profiles, checks
  them against the deterministic capability/effort template, and fails closed on an
  absent/divergent profile. Project translations to a built-in full ID use that pin;
  translations to an accepted short alias retain the generic profile/wire path. A custom
  full ID without a shipped profile declaration is diagnosed, never silently aliased.
- Claude Haiku 4.5 accepts an explicit null project effort as model-specific
  `not_applicable`. Legacy mapping `low` stays requested historical intent; the hook
  selects the corresponding `routed-<capability>-none[-<version>]`, omits effort
  frontmatter and exposes transmitted
  effort null. Explicit user effort is rejected. Null has no rank and cannot satisfy
  reviewer/architect floors or another model's effort scope. Requested, transmitted and
  observed efforts stay separate; missing native observation remains unknown.
- Claude Haiku 5.5 (`haiku-5.5` / `claude-haiku-5-5`, PAT-125, PAT-ADR-0016) is a
  canonical pin with its own effort scope (`low` to `max`) and one versioned profile per
  capability and effort; it always needs an effort and never inherits the Haiku 4.5
  rule above. Haiku 4.5 stays an exact historical pin and never becomes an alias of it;
  the short `haiku` alias is not promoted. The shipped `economy` default is still Haiku
  4.5 / null. The one native trial of PAT-ADR-0016 ran on 2026-10-09 and its recorded
  verdict is `not_conforming` (fixture check only; profile, model, effort and host
  version observed exact): the incumbent is kept and the promotion to `haiku-5.5` /
  `medium` is not applied, pending a maintainer decision
  (`plugins/foundry/docs/qualification/pat-125-haiku-55-promotion.md`).
- `CLAUDE_MODEL_MIN_HOST_VERSION` (`routing_facades.py`) requires Claude Code 2.1.293
  for the `haiku-5.5` pin. The routing hook observes the host version with
  `claude_host_version()`: the top-level `version` of the last versioned record of the
  session transcript named by its payload, never the environment and never a `claude`
  found on `PATH`. An observed lower version makes `claude_invocation_binding()` raise
  `RoutingConfigError` naming the required version: the launch is denied and nothing is
  substituted, neither Haiku 4.5 nor an alias. An unobservable version is `unknown`:
  the launch proceeds with `host_version.status = "unknown"` and the
  `CLAUDE_HOST_VERSION_UNOBSERVED` warning, never reported as conforming. The headless
  runners (`command_runtime.py`, `campaign_runtime.py`) apply the same rule through
  `claude_headless_host_version_requirement()`: for such a pin only, they ask the
  `claude` they are about to launch its `--version` (local, unpaid, same runner and
  child environment), refuse below the minimum, and on an unreadable answer launch
  with a `RuntimeWarning` carrying the same code. `routing show`/`resolve` and `doctor`
  bind nothing and observe no host version.
- **Effect of a model that resolves to neither the built-in table nor the project's
  `claude_models`**: `claude_invocation_model()` raises `RoutingConfigError` naming the
  unresolved canonical model. Resolution fails closed — it never guesses, silently
  substitutes, or falls back to an arbitrary model.
- A project cannot use `claude_models` to weaken a built-in model's alias, and any
  custom Claude effort level it declares must be one of the Agent-executable profiles in
  `CLAUDE_EXECUTABLE_EFFORTS` (`low`, `medium`, `high`, `xhigh`, `max`); otherwise
  `_load_claude_policy()` (`routing_facades.py`) fails at load, before any
  invocation.

The full schema, availability-driven fallback, and cross-host review-deduplication
contract are documented in `plugins/foundry/docs/model-routing.md`; this rule is the
current-state summary that AC on this contract requires, not a replacement for that
document.

## R8 — Using this contract in review

`foundry:maigret`'s "project conventions" quality criterion is this document. A finding
that cites a convention breach must point at a rule here (`AGENTS.md#R<n>` /
`CLAUDE.md#R<n>`) or at an accepted ADR by ID — never an unwritten preference. A diff
that does not touch any rule above passes this criterion without further comment.
