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
  `claude` they are about to launch its `--version` (local, unpaid, same runner, child
  environment and working directory), refuse below the minimum, and on an unreadable
  answer launch after writing one line with the same code to standard error, at every
  launch. There a dated snapshot `claude-haiku-5-5-YYYYMMDD` carries the same minimum;
  the hook refuses such an identifier first because no preloaded pinned profile exists
  for it. `routing show`/`resolve` and `doctor` bind nothing and observe no host
  version.
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

## R8 — Using this contract in review

`foundry:maigret`'s "project conventions" quality criterion is this document. A finding
that cites a convention breach must point at a rule here (`AGENTS.md#R<n>` /
`CLAUDE.md#R<n>`) or at an accepted ADR by ID — never an unwritten preference. A diff
that does not touch any rule of this contract passes this criterion without further comment.

## R9 — Fewer review rounds: author checklist, grouped deferral, minimal corrections (PAT-136, PAT-139)

This rule applies to the implementer, to every correction commit, and to the
coordinator when it writes files itself. It changes nothing about the reviewer's minimum
tier (FOUNDRY-ADR-0006), the first full review, or the full review after any blocking
round. Baseline, observation window and return triggers are in
`plugins/foundry/docs/review-rounds.md`.

**(a) Checklist before handing work back.** The author checks each point on every
sentence of documentation it adds or changes, and reports for each sentence that falls
under points 1 to 5 its source (constant, `file:line`, or page):

1. A sentence about what a tool launches, loads, calls or writes cites its constant,
   file or page; otherwise it points to that page without summarising it.
2. A cause is stated as `observed`, `deduced` or `unknown`.
3. A rule, threshold or quantity is copied from the code or the source report, then
   searched for across the rest of the repository to correct statements it made false.
4. A comparison names the compared sets and checks that they are equal.
5. A documented procedure has been run once, on a copy.
6. No proper name or user name appears in any file, tests included.

**(b) Non-blocking remarks: grouped deferral to a companion Epic (PAT-ADR-0018).** After
a review round that is fully validated, non-blocking remarks are not corrected in the PR.
The remarks deferred from the issues of an Epic go to a companion Epic, named `Nits`
followed by the identifier of the origin Epic (`Nits <ORIGIN-EPIC-ID>`). The origin Epic
of an issue is its direct parent Epic: an issue under a sub-Epic uses that sub-Epic. The
two Epics
have no tracker link between them: no parent, no dependency, no relation. What relates
them is that name and a mention in text, nothing else. Inside the companion Epic the
remarks are held by follow-up issues, one per batch or per theme, never one per PR. The
follow-up issue that receives the remarks of a PR is cited in that PR's description
before the merge. Three cases are still corrected before the merge and then fully
re-reviewed, as before:

- a remark the reviewer marks "fix before merge";
- a remark on a page frozen after publication (release note, migration guide, a
  CHANGELOG section of a published version);
- a missing or generic documentation status under R5 and FOUNDRY-ADR-0018, which is never
  a deferrable remark.

There is no second-level deferral: the non-blocking remarks left by the PR of an issue
that belongs to a companion Epic are corrected before the merge and then fully
re-reviewed.

Three writes are authorized in advance, without human confirmation, inside and outside
an Epic campaign (FOUNDRY-ADR-0013, FOUNDRY-ADR-0016), in every repository where the
plugin is installed. For these three writes this rule gives the confirmation that
`foundry:intake` requires before a write ("3. Apply (human-confirmed)"):

1. create the companion Epic, the first time;
2. create a follow-up issue in it;
3. add deferred remarks to such an issue.

The coordinator reports these writes in the PR description. Nothing else is
pre-authorized: the companion Epic and its issues are neither prioritized nor started
without the maintainer (deciding to treat them, and when, is a human verdict under
FOUNDRY-ADR-0014), and every other intake write keeps its confirmation.

The rule names no tracker. Its three writes are operations of the portable contract
`plugins/foundry/docs/tracker-contract.md`, whose table "Core journey" marks both rows
below `supported` in each of its three provider columns:

- row "Frame/intake/groom: create, comment" (`create_issue`, `add_comment`) carries
  writes 1 and 3;
- row "Epics/enfants/dépendances: child creation, relations" (`create_issue(parent=…)`,
  `link(depends-on|blocks|relates)`) carries write 2.

When one of the three writes does not go through, two cases are told apart:

- durable refusal (the project cannot carry the write, for example it has no `Epic`
  type): the coordinator makes no substitute write: the remarks of that round are
  corrected before the merge and then fully re-reviewed, and the refusal is reported in
  the PR description;
- transient failure (request quota exhausted, network failure): the coordinator waits
  and retries the write before the merge, and the merge waits for it. It does not correct
  the remarks instead and does not merge first. When the failure leaves the effect of the
  write unknown (a network failure), it first reads the tracker to see whether the write
  happened, and retries only if it did not, so that a retry creates no duplicate. If
  the adapter itself refuses the retry (it cannot replay the write safely), the case is
  treated as a durable refusal: the remarks of that round are corrected before the merge
  and then fully re-reviewed, and the refusal is reported in the PR description.

Both are operating choices of this contract (maintainer decision of 2026-10-10; the
refused retry is the coordinator's reading of those two decisions), not decisions of
PAT-ADR-0018.

PAT-ADR-0018 decides for Epics only. An issue that has no origin Epic gets no deferral:
its non-blocking remarks are corrected before the merge and then fully re-reviewed
(maintainer decision of 2026-10-10).

The Foundry skills apply this grouped deferral and this standing authorization in every
repository where the plugin is installed, by the maintainer's decision of 2026-10-10;
this contract is where the rule is recorded.

**(c) Correction commits.** A correction commit changes as few sentences as possible, and
every new sentence is re-checked against the code before the work is handed back.

**What only the reviewer can judge.** No mechanical check covers: that a sentence cites
the right source and is true; that a procedure was really run; that a correction commit
changed "as few sentences as possible"; that a remark was rightly classed as deferrable.
`plugins/foundry/tests/test_skills.py` and `plugins/foundry/tests/test_process_contract.py`
prove only that these rules are written in the skills and in this contract, not that they
are followed (FOUNDRY-ADR-0018: a mechanical
check never proves that documentation is true). Likewise, neither the "fix before merge"
mark nor the existence of the follow-up issue is seen by any gate: the review proof stays
`quality=mergeable`, so the merge command does not refuse a diff because a marked remark
was left uncorrected or because the follow-up issue does not exist. The checklist is an obligation of the
author judged by the reviewer, not a gate. In the same way no gate checks that the
companion Epic of (b) exists, that it has no tracker link with the origin Epic, or that
a PR of a companion Epic deferred nothing: this is judged in review.

**What is not coded yet.** The three writes of (b) are made by the coordinator that
applies the skills. An Epic campaign does not receive the reviewer's prose: its review
step (`_review_observation` in `plugins/foundry/tooling/foundry/campaign_runtime.py`)
reads only the structured proof stored by `AcceptanceProofStore`
(`plugins/foundry/tooling/foundry/routing.py`), and the `record-review-proof` command of
that file accepts exactly the keys `outcomes` and `quality`. So the non-blocking remarks
of a validated review are neither seen nor recorded by a campaign today, and none of the
steps that follow the review step (`ci`, `human-gate` in `_GATE_STEPS`, then `merge`)
reads or corrects them. The campaign coordinator has no issue-creation primitive: the
mutating steps of its runner are `start`, `open-pr`, `merge`, `close-epic` and
`sync-parent-acceptance` (`_MUTATING_STEPS`,
`plugins/foundry/tooling/foundry/campaign_runtime.py`), and the `CampaignPipeline`
protocol (`plugins/foundry/tooling/foundry/campaign_coordinator.py`) declares no
method that creates an issue. The campaign preview exists for one provider only:
`preview_epic` (`plugins/foundry/tooling/foundry/epic_preview.py`) refuses a tracker
whose adapter does not set `epic_subgraph_supported`, which defaults to `False`
(`plugins/foundry/tooling/foundry/trackers/base.py`) and is set to `True` only by
the DevHub adapter (`plugins/foundry/tooling/foundry/trackers/devhub.py`). So inside
an Epic campaign the authorization of (b) has no effect yet. Three things are missing: a
channel that carries the remarks to the campaign, the code that creates the companion
Epic and its issues, and campaigns usable on the repository's tracker. They are left to
a later issue under the same Epic, PAT-141.
