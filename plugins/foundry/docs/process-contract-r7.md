# Process contract: rule R7, Claude model resolution at CLI boundaries (current state)

Since PLP-362 the repository contract (`AGENTS.md`, identical in `CLAUDE.md`, at the
repository root) keeps a short version of rule R7 to limit the context loaded at every
session. The heading `R7` stays in the contract, so `AGENTS.md#R7` citations still resolve.
The text below is moved verbatim from `AGENTS.md` R7; it is the detail of the rule, and the
contract's short version is a summary of it. Paths are relative to the repository root.


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
  Haiku 5.5 and Haiku 4.5 (including its explicit `20251001` snapshot). Versioned
  policy models select a preloaded versioned profile whose frontmatter carries the
  declared full identifier. The Agent tool wire omits `model` for these pins: the
  observed 2.1.285 Agent schema accepts only `haiku`, `sonnet`, `opus`, `fable` there.
  Historical pins never become latest aliases.
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
  the short `haiku` alias is not promoted. `haiku-5.5` / `medium` is the shipped Claude
  `economy` default, per tier: every non-gate role falling back to `economy` gets it
  too. The effort `medium` is unmeasured, and no gain or saving is claimed. A project
  returns to the incumbent by writing `{"model": "haiku-4.5", "effort": null}` under
  `mappings.claude.economy`; the same mapping without the `effort` key, and a direct
  request for `haiku-4.5` or `haiku`, inherit `medium` and are refused with that fix.
  An effort-only `economy` override now applies to Haiku 5.5, and an availability list
  (`FOUNDRY_CLAUDE_AVAILABLE_MODELS`) that lacks `haiku-5.5` makes the tier unavailable
  (`RoutingUnavailableError`), with no substitution by a listed Haiku 4.5.
  Both native trials of 2026-10-09 (first `not_conforming`, kept as recorded; second
  `conforming`, authorised by the maintainer) are in
  `plugins/foundry/docs/qualification/pat-125-haiku-55-promotion.md`.
- `CLAUDE_MODEL_MIN_HOST_VERSION` (`routing_facades.py`) requires Claude Code 2.1.293 for
  the `haiku-5.5` pin. The routing hook observes the host version with
  `claude_host_version()`: the top-level `version` of the last versioned record of the
  session transcript named by its payload, never the environment and never a `claude` found
  on `PATH`. An observed lower version makes `claude_invocation_binding()` raise
  `RoutingConfigError` naming the required version: the launch is denied and nothing is
  substituted, neither Haiku 4.5 nor an alias. An unobservable version is `unknown`: the
  launch proceeds with `host_version.status = "unknown"` and the
  `CLAUDE_HOST_VERSION_UNOBSERVED` warning, never reported as conforming. The three headless
  runners (`command_runtime.py`, `campaign_runtime.py` and the effects of the Epic campaign
  cadencer, `campaign_effects.py::session_plan`) apply the same rule through
  `claude_headless_host_version_requirement()`: for such a pin only, they ask the `claude`
  they are about to launch its `--version` (local, unpaid, same runner, same child
  environment as the launch up to the two receipt variables that `command_runtime.py` adds
  after the probe, and working directory: the command root in `command_runtime.py`, the
  worktree in `campaign_runtime.py`, the ticket worktree in `campaign_effects.py`, PAT-236),
  refuse below the minimum, and on an unreadable answer launch after writing one line with
  the same code to standard error, at every launch. In these runners a dated snapshot
  `claude-haiku-5-5-YYYYMMDD` carries the same minimum; the hook refuses such an identifier
  first because no preloaded pinned profile exists for it. `routing show`/`resolve` and
  `doctor` bind nothing and observe no host version.
- The ten Sonnet 5.5 versioned profiles (`CLAUDE_CACHE_TTL_1H_PINS`, `routing_facades.py`, PAT-134) carry the
  subagent prompt-cache lifetime as a nested map, `experimental: {cacheTtl: 1h}`, and no other profile does. Per the
  Anthropic documentation read on 2026-10-09 (not re-verified), the field needs Claude Code 2.1.248 or later and `1h`
  is ignored while the subscription draws usage credits; behaviour on an older host and under credits is not
  observed here, and no guard is added. Named risk: a host older than 2.1.248 might reject a profile carrying the
  field, which would break every Sonnet 5.5 subagent launch there (Sonnet 5.5 has no host minimum); unknown.
  `claude_pin_profile_text()` renders exactly this block for these pins and
  `claude_invocation_binding()` refuses any divergent pinned profile (field missing, on another pin, another value or
  key; a short-alias route reads no profile file). Rollback: empty `CLAUDE_CACHE_TTL_1H_PINS`, regenerate the profiles and update the tests and sentences that assert the
  field (every step in the page below; both tools still import with the tuple empty). It changes no model, effort or routing; the effect on the subscription quota is unknown. The observation
  window and the rollback are in `plugins/foundry/docs/qualification/pat-134-subagent-cache-1h.md`.
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
