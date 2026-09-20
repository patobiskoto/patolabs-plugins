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
why the tracker must stay authoritative over the merge path.

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
  (`plugins/foundry/tooling/foundry/routing.py:40`), itself validated from
  `plugins/foundry/tooling/foundry/effort_policy.py`.
- The built-in canonical-model-to-Agent-alias table is a declaration, not a chain of
  per-model code paths: `_CLAUDE_MODEL_DECLARATION` lists `(canonical, alias,
  accepted_spellings)` tuples (e.g. `("sonnet-5", "sonnet", ("claude-sonnet-5",))`);
  `_CLAUDE_MODEL_IDS` (canonical → alias) and `_CLAUDE_POLICY_MODELS` (any accepted
  spelling → canonical) are both derived from it
  (`plugins/foundry/tooling/foundry/routing_facades.py:64-73`).
- Resolving a policy model to what Claude's Agent tool actually invokes is
  `claude_invocation_model()` (`routing_facades.py:809-828`): normalize the requested
  spelling to its canonical name via `claude_policy_model()`; if that canonical name is
  one of the built-in models, return its built-in alias; otherwise fall back to the
  project's own `claude_models` declaration (`.foundry/model-routing.json`, loaded
  through `RoutingPolicy.load()` or an explicit `project_models` mapping).
- **Effect of a model that resolves to neither the built-in table nor the project's
  `claude_models`**: `claude_invocation_model()` raises `RoutingConfigError` naming the
  unresolved canonical model. Resolution fails closed — it never guesses, silently
  substitutes, or falls back to an arbitrary model.
- A project cannot use `claude_models` to weaken a built-in model's alias, and any
  custom Claude effort level it declares must be one of the Agent-executable profiles in
  `CLAUDE_EXECUTABLE_EFFORTS` (`low`, `medium`, `high`, `xhigh`, `max`); otherwise
  `_load_claude_policy()` (`routing_facades.py:839-889`) fails at load, before any
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
