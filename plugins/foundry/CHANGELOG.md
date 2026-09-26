# Changelog

## Unreleased

## 0.9.0 — 2026-09-26

### Added
- A fail-closed Linear tracker adapter using explicit repository, team, project,
  workflow-state, label, and milestone identifiers, resolved from the actual checkout's
  canonical Git remote rather than an environment alias or basename. It supports strict
  issue reads, creation, and non-replacing relations/comments without promising
  exactly-once delivery. Existing issue field/state/parent/body replacement stays
  typed-unavailable because Linear exposes no atomic anti-overwrite precondition.
  `issue openpr` and `issue merge` instead use deterministic-ID, append-only comments;
  Foundry validates and projects their PR/state/AC receipts without changing native
  Linear fields. Complete cockpit Evidence Plane envelopes are append-only advisory
  audit records and never state, AC, CI or merge authority. Linear stores ADRs as
  versioned, witness-bound project Documents (PAT-22); atomic Epic closure remains
  unavailable; this repository is cut over to Linear project PAT (PAT-10).
- `query issue` reports an explicit `conflict` status instead of presenting a
  conflicted embedded ADR index as empty, so a caller can tell "no ADR" apart from
  "the index disagrees with itself" (PAT-41).
- Tolerant Markdown serialization handling for Linear's own rendering of the ADR
  delimiter and angle-bracket content, so its reformatting never masks a real
  alteration, while byte-exact source bodies stay verifiable through witnesses
  (PAT-37/PAT-38/PAT-39).
- `plan_adr_batch_qualification`, a read-only planner that performs no provider write
  and returns the exact version Document and witness bytes, and probe titles, a
  historical batch import would create; the import itself reads every non-authoritative
  qualification probe by ID and checks its complete readback before any effect
  (PAT-40).
- Explicit `missing_relations` on the records of a partial-source historical ADR
  manifest, so each unknown relation family is listed in a canonical, sorted tuple
  rather than omitted or presented as silently empty, and the audited import of the
  repository's 27 historical ADRs into Linear (no private data), which declared all
  three families unknown (PAT-23).
- A repository-scoped `.foundry/tracker.json` marker that takes precedence over the
  host-global tracker default, a `registry cutover` command that binds a project by its
  exact manifest digest, an archive tombstone recording the former YouTrack binding, and
  an operations log capturing `adr_authority` and `incidents` for the cutover (PAT-10).
- A typed `acceptance-override` receipt for a human AC override merge on Linear, with
  recovery replay so an interrupted override merge can resume from its exact receipt
  instead of re-deciding the override (PAT-49).
- Public-repository main-branch protection, read back from the actual GitHub branch
  protection rules rather than assumed (PAT-12).
- A dedicated, audit-bound `rearm-remediation` transition for a just-exhausted bounded
  correction window that is not yet halted. It CAS-binds the issue, recorded role,
  original halt generation, controlled human reason, and 1..3 new credits; preserves
  counters, tier floors, and every consumption event; appends a public link to the
  exhausted window; and exposes identical state through Claude and Codex without
  weakening ordinary halted-only resume.
- An outbound DevHub command worker with exact claim/heartbeat bindings, bounded live
  leases, command-ID ambiguity recovery, monotonic local receipts, last-moment gate and
  authority revalidation, and a concrete Claude execution path whose model floor and
  dollar ceiling are fixed before the effect. A host-shared SQLite ledger now reserves
  observed budget and concurrency atomically across worker processes before launch,
  settles terminal cost durably, and keeps ambiguous/crashed allocations fail-closed.
  The command credential remains limited to claim/event calls and is removed from the
  spawned runtime environment.
- A deterministic, read-only Epic execution preview over Dev Hub's complete versioned
  subgraph. It fails closed on truncated, inconsistent, cyclic, or schema-invalid
  projections; classifies eligible, blocked, omitted and human-gated work; derives
  concurrency-capped dependency waves; resolves planned model tiers with risk floors;
  and binds the snapshot, effective policy, limits, expiry and unknowns in one stable
  digest. The preview exposes no branch, agent, command, budget reservation, tracker
  mutation, merge, or other execution authority.
- A dedicated provider-neutral non-code Epic closure command and Tracker contract. Its
  receipt binds the exact project, parent version and AC, complete canonical child
  id/version/state set, timestamp, and nonce; a supporting provider must verify and
  audit the graph atomically and expose the receipt for interruption recovery. The code
  issue merge-receipt gate is unchanged. DevHub Tracker v1 and YouTrack explicitly
  report this capability unavailable rather than emulate it with a racy read/write.
- An opt-in DevHubTracker v1 provider with normalized project, issue, link, comment,
  and ADR operations; bounded HMAC receipts for review/done/ADR transitions; versioned
  idempotent writes; repository-bound mutation guards; hard-capped pagination; sanitized
  public audit verification; and an explicitly destructive real-provider smoke covering
  optimistic concurrency, replay and link/comment round trips. Mutation binding compares
  the actual credential-free Git remote identity to `canonical_repo`; review and AC
  proofs use GitHub's exact immutable PR base SHA and reject a stale coordinate; doctor
  validates the transport from a narrow public-config read before parsing or reading
  DevHub credentials. Configuration and registry writes validate and canonicalize URLs
  and repository identities before persistence, while legacy unsafe values stay redacted
  and fail closed before transport. An already-done retry
  performs cleanup without a second merge or illegal rewind. YouTrack remains the
  default and no routing, gate, review, or merge authority moves to Dev Hub.

### Fixed
- Linear lifecycle fixes: blocking premature GitHub closure of a Linear-tracked issue
  (PAT-26) and tolerating a Backlog-to-In-Progress transition after a PR is already
  attested (PAT-28).
- Tracker-neutral escalation and bounded-remediation fixes: controlled resumption after
  exhausted technical diagnostics (PAT-27); rearming an exhausted window after a
  technical diagnostic generation (PAT-29); making remediation usable after a review on
  an already-consumed technical route (PAT-30); allowing exactly one new review after a
  credited correction on such a route (PAT-31); and rearming a corrected review across a
  PR base advance (PAT-32).
- Documented and tested contracts without a product-code change: resuming the Linear
  cutover after technical remediation without widening its permissions (PAT-21), and an
  offline test double proving the provider handoff stays idempotent and capability
  stays valid after a crash (PAT-24).

## 0.8.1 — 2026-09-13

### Added
- `query profile <workflow>`, a bounded query projection for `next-issue`, `roadmap`,
  `blockers`, `groom`, and `intake`. `next-issue` now costs 29 761 bytes instead of the
  138 165 bytes of the previous `query backlog` call it replaced for that workflow.
- `cost-attribution`, an offline host-log cost attribution command reading a versioned,
  dated price grid. PROVENANCE is aligned on FOUNDRY-ADR-0008, exposes `billing_mode`,
  and aggregates by epic, issue, project, and model.
- Cost per position within a session and session comparison curves, plus
  `plan_usage_percent` on Codex.

### Fixed
- A Claude host log containing records without a reasoning breakdown is priced instead
  of aborting the whole log; `reader_completeness` reports what is affected.
- Codex cost over-counting: `total_token_usage` is read as cumulative per session, not
  summed per message.

## 0.8.0 — 2026-08-29

### Added
- A structured acceptance-criteria proof for the pre-merge gate. The reviewer returns
  ordered AC outcomes and one quality verdict; Foundry binds them atomically to the
  current issue AC digest, local `HEAD`, exact base diff, active review claim, and
  reviewer generation. Stale, malformed, mixed, or legacy evidence fails closed. The
  explicit human `--allow-incomplete-ac` path remains audited and never edits tracker AC.
- The versioned FOUNDRY-46 dual-host benchmark evidence and FOUNDRY-47 publication
  decision. The 108-coordinate v2 matrix remains separate from product activation:
  Claude and Codex are both `inconclusive`, every unavailable cost/downstream/test/
  review/allocation field remains `null`, and the unweighted cross-host aggregate is
  `null` and forbidden.

### Changed
- Published one shared Foundry implementation as two aligned 0.8.0 host packages. The
  Claude and Codex catalogues keep their supported source-pointer schemas and therefore
  deliberately contain no invented `version` field; version authority remains in the
  two package manifests.
- Updated install, upgrade, configuration, doctor, host-override diagnosis, source
  verification, and exact-ref rollback guidance for both Claude Code and Codex.

### Unchanged production contract
- The recommended ordinary main profile remains Sonnet 5 / medium on Claude Code and
  GPT-5.6 Terra / medium on Codex. Foundry cannot force the already-open main
  conversation. Reviewer stays at least frontier/high, architect at least apex/high,
  and the existing maximum of two tier increases per issue remains in force.
- No model, effort, context policy, route, default, gate, fallback, escalation signal,
  or local authority changes in 0.8.0. No local candidate is promoted. The historical
  7.42% price-snapshot observation is noncausal and is not a savings claim.

## 0.7.0 — 2026-08-22

### Added
- An operator-controlled local preprocessing provider, disabled by default and confined
  to an explicit loopback-only OpenAI-compatible endpoint. The local model is untrusted,
  evidence-bound, read-only, outside Foundry's roles and tiers, and can never approve a
  gate, write to the repository, select a cloud model, or gain merge/release authority.
- Shared `diff`, `logs`, and `tests` capture modes with immutable bounded snapshots,
  sensitive-path filtering, secret redaction, typed locators, wrapper-owned evidence,
  stale-input detection, and strict response-schema validation. The two-pass `code` mode
  exposes a body-free manifest first, then a frozen evidence bundle; it cannot add an
  adaptive third pass.
- Trusted local-scout policy for explicit model and loopback destination selection,
  conservative 2-second connection and 8-second total defaults, hard 10/120-second
  ceilings, project limits that may only tighten budgets, and an opt-in
  `cloud_economy` failure plan. Policy violations and stale input always stop; the
  fallback never performs the cloud call itself and remains subject to the production
  ADR-0006 scout route.
- A read-only doctor view for local preprocessing. It reports
  `disabled`/`configured`/`available`/`unavailable`/`invalid policy` using at most one
  bounded loopback TCP connection and never completes a model or reveals its endpoint,
  identifier, or secrets.
- Passive opt-in routing telemetry with private one-time completion/outcome capabilities
  and a bounded append-only local journal. It is fail-open, never uploads, never records
  prompts, responses, code, paths, URLs, free-form errors, or secrets, and never feeds a
  current or future routing decision.
- The frozen, candidate-agnostic FOUNDRY-35 benchmark campaign, including raw evidence,
  aggregate results, a deterministic post-run addendum, attestation, nullable cost
  semantics, and offline reproduction. No local candidate is promoted: all four failed
  the mandatory product-schema and downstream-correctness gates, achieved only about
  0.32% median cloud-input reduction against a 40% threshold, and had no applicable
  immutable public price, so all cost reductions remain `null` rather than zero.

### Changed
- Model, reasoning effort, and context policy are documented and observed as separate
  dimensions while the production resolver remains exactly the ADR-0006 tier mapping.
  There is no adaptive routing, reasoning ladder, production shadow call, or new routing
  signal in 0.7.0.
- Issue escalation can expose a human-authorized remediation window of one to three
  credits after the existing stop. Credits apply only to the exact stopped correction
  role and `review_blocking_after_fix` signal, are durably audited with controlled
  values, and do not renew the two-escalation cap, lower a floor, or widen capability.
- Claude's logical-agent façade now matches the real Agent wire: short provider aliases,
  dynamic `model` and `max_turns`, static effort-only execution profiles, strict bounded
  task packets, current availability normalization, and explicit host-override warnings.
- The recommended main coordinator remains the balanced profile: Sonnet 5 / medium on
  Claude Code and GPT-5.6 Terra / medium on Codex. Reviewer and architect retain their
  frontier/high and apex/high-or-max floors and upward-only fallback.
- Installation, upgrade, 0.6.x migration, dual-host configuration, architecture, local
  privacy/fallback behavior, benchmark results, limits, and the next required experiment
  are now collected in the README and the 0.7.0 migration/release guides.

### Fixed
- Wired opt-in passive telemetry to Claude's real post-Agent callbacks and Codex's
  required post-spawn skill lifecycle, with private bounded one-time capabilities,
  consume-time 24-hour expiry, one cross-process lock for atomic bounds/replacement/
  consumption, replay/forgery and symlink resistance, correct fallback/override
  counters, and recovery from interrupted append/capability fragments. Claude's real
  hook is now entirely silent and records only its coarse callback status; Codex skills
  must pass an explicit truthful success/failure/timeout/cancellation/unknown class.
  Observation cannot change route, packet, spawn, retry, escalation, gates, results, or
  provider behavior.

## 0.6.2 — 2026-08-20

### Fixed
- Removed the redundant Claude manifest declaration of the conventional
  `hooks/hooks.json` file, which Claude Code already loads automatically and rejected
  as a duplicate during real 0.6.1 installation. The dual-host validator now models
  Claude's automatic hook discovery and optional additional hook file independently
  from Codex while preserving path confinement and semantic-divergence checks.

## 0.6.1 — 2026-08-20

### Added
- An explicitly opt-in real YouTrack smoke test covering the public issue/link/state/
  comment/ADR cycle, with disposable-project guards, per-run audit markers,
  secret-safe diagnostics, provider-local idempotent cleanup, and a separate
  variable-gated CI job.
- A host-neutral semantic model-routing contract with four tiers and five role
  defaults, project and user overrides, field-level precedence, visible downward
  fallback for ordinary roles, and upward-only fallback for reviewer/architect gates.
- A read-only `routing show`/`routing resolve` command, atomic cross-host review
  deduplication keyed by the exact diff hash, and a value-free common warning contract
  for host overrides that can neutralize the resolved policy.
- Claude Code logical roles for scout, implementer, reviewer, and architect, dynamically
  rewritten at Agent invocation time so project/user model policy survives static plugin
  frontmatter. Bounded task packets and turns, static effort-only execution profiles,
  explicit availability fallback, and a fail-closed Bash guard preserve gate/read-only
  guarantees.
- A Codex invocation façade that produces exact fresh-context subagent descriptors with
  resolved model and reasoning effort, explicit availability fallback, value-free
  profile-override warnings, common diff-hash review deduplication before spawn, and a
  disclosed current-context fallback when subagents are unavailable.
- Deterministic cross-host escalation state keyed by repository, issue, and role: only
  red tests/blocking reviews or explicit risk/ADR/user signals count; floors persist for
  the issue, two tier increases are allowed, and further work halts for a human. Dynamic
  floors cannot be demoted by direct model/tier requests or availability fallback and
  never expand agent capabilities or external authority.
- A CI-pinned resolved-invocation contract, minimal project-routing example, documented
  role/primary-profile/host limits, and frozen manual `FOUNDRY-MR-PILOT-v1` worksheet
  for measuring main-loop and subagent cost without automatic telemetry.

### Fixed
- Milestone rollups and dependency analysis now treat Foundry `done`/`dropped` and
  YouTrack `Fixed` as terminal without depending on provider capitalization, while
  changelog generation remains restricted to work explicitly marked `done`.
- Fall back from GitHub's unavailable aggregate check-runs endpoint to paginated
  check-suite runs without weakening or waiving the merge gate.
- Repo renames can now add a safe registry alias without provisioning a duplicate
  tracker project; `doctor` groups aliases by distinct project identity.
- Bootstrap ADR import requires and is idempotent by filename ID rather than mutable
  title, and preflights unnumbered files and numbering gaps before any tracker write,
  preventing shifted ADR IDs.

## 0.6.0 — 2026-07-21

### Added
- Native Codex packaging (`.codex-plugin/plugin.json`) beside the unchanged Claude Code
  package, with one shared set of skills, hooks, tooling, tests, and release history.
- `foundry:configure`: host-neutral non-secret configuration, interactive macOS Keychain
  token storage, redacted diagnostics, and an install marker sibling plugins can resolve.
- Portable `foundry:review-pr` skill mirroring the Claude reviewer agent's two-stage
  acceptance-criteria + quality gate.
- Linked-worktree-aware issue start/merge behavior for Codex-managed worktrees.

### Changed
- Skill commands use Claude's deterministic `CLAUDE_PLUGIN_ROOT` expansion when present,
  with a `SKILL.md`-path fallback required by Codex (which exposes plugin-root variables
  to hooks, but not to skill-initiated shell commands). Docs and hook guidance expose
  both Claude `/foundry:*` and Codex `$foundry:*` invocation forms.
- Configuration now resolves direct environment variables, Claude plugin options,
  Keychain, then the Foundry config file. Registry data lives in one shared path —
  `FOUNDRY_DATA` when explicitly set, else `~/.config/foundry` — and deliberately
  ignores the hosts' plugin-private `PLUGIN_DATA`/`CLAUDE_PLUGIN_DATA` dirs: hosts
  inject those into hook commands but not skill commands, which would split the
  registry into two silently diverging views.
- Restored non-empty Claude Code argument hints after verifying that Codex 0.144.6
  installs and invokes skills carrying the unknown key without a related warning; the
  shared validator now accepts only short, quoted, one-line hint strings.
- `SessionStart` hook matcher covers `resume` too: a resumed session gets the Foundry
  contract injected exactly like a fresh one.
- `issue merge` now reports the squash SHA that landed on the default branch
  (`sha mergé <sha>`): release tooling (ship-ios) tags exactly that commit, and
  `origin/<default>` would be racy if another PR lands in between.

## 0.5.0 — 2026-07-02

### Added
- **`query changelog <milestone>`** — the shipped (`done`) issues of a milestone,
  grouped by type, as locale-neutral facts (id / title / type / labels). It is the
  clean SOURCE a release tool consumes to write user-facing release notes per target
  locale; Foundry emits facts, never prose, never a hardcoded language. Platform- and
  locale-neutral: any project cutting a version wants "what landed in vX". `dropped`
  issues are excluded (they never shipped).

## 0.4.1 — 2026-07-02

### Fixed
- `list_adrs`: the ADR prefix match is ANCHORED and escaped (`re.match` +
  `re.escape`) — a project key that is a suffix of another ("TOC-ADR" inside
  "MDTOC-ADR-0001") cross-matched and polluted the index/numbering. Found by
  external blank-context review (F1); suffix case pinned by test. The
  instance-wide article scan and its 1000 ceiling are documented inline
  (proper server-side scoping tracked in #17; secret-gated adapter smoke in #18).

## 0.4.0 — 2026-07-02

### Changed (token posture: summary first, detail on demand)
- **Lean list payloads**: `query backlog`/`candidates` no longer carry issue bodies
  (fields, links, AC counts, ranks and statuses carry the reasoning signal); the
  free text comes via `query issue <ID>`. In `query issue`, related issues are lean
  too — only the requested issue keeps its body and progress notes.
- **ADR index + drill-down**: `query adrs` returns id/title/status/ref only; new
  `query adr <ADR-ID>` returns one full ADR. Retrieval-before-reasoning becomes
  constant-cost: scan the index always, load only the ADRs touching the topic.
  The `adrs` section of `query issue` is the index as well.
- Skills updated to the index → detail pattern (frame, intake, adr, blockers,
  roadmap, start-issue, resume-issue; groom and intake's refine path explicitly
  drill down before ruling on AC text — never judge unloaded text).
- **FOUNDRY-ADR-0003** records the decision and amends ADR-0001's "JSON riche" /
  "on charge tout" clauses (inline notes + `amended_by`), matching the ADR-0002
  precedent — the lean posture no longer silently contradicts the founding ADR.

### Hardened in review
- `query issue` tolerates unreachable related issues (deleted / cross-project →
  marked entry instead of a crash) and dedupes link targets; `query adr`/`issue`
  without an id exit with a clean usage line; `query adrs <ID>` (the natural
  near-miss) delegates to the drill-down; the ADR miss path no longer re-fetches
  the whole knowledge base; SessionStart contract teaches index → detail.

## 0.3.1 — 2026-07-02

### Fixed
- `ci_expected` no longer deadlocks the `--allow-no-ci` waiver: a stale
  queued-empty check-suite (schedule-only workflows keep one forever) doesn't
  count as active CI — only suites in progress, with runs, or queued for less
  than 15 minutes (the post-push window) refuse the waiver.
- `issue merge` no longer recommends `--allow-no-ci` in the very message that
  refuses it.
- Docs aligned with the gate's reality: merge-pr skill (two sources, ≥1 success),
  CodeHost port header (three methods), waiver wording (active-CI signal, not
  "no suite exists"), issue CLI docstring; ADR-0001 ↔ ADR-0002 now cross-linked
  (`amends`/`amended_by` + inline note on the amended clause); ADR-0002 documents
  that legacy-only CIs have an undetectable post-push window.

## 0.3.0 — 2026-07-01

### Added
- **CI gate reads both sources** (ADR-0002, closes the Jenkins blind spot): new
  `CodeHost.commit_statuses()` (legacy status API, paginated) merged with check-runs
  in `ci_gate` — a red legacy status blocks even with zero check-runs, and
  `--allow-no-ci` waives only when BOTH sources are empty AND no active check-suite
  signals CI for the commit (`CodeHost.ci_expected` — the post-push window where CI
  is queued but invisible is not waivable). Pagination of both reads terminates on the
  response's own `total_count`, never on batch size. Port change: code-host
  adapters must now implement `commit_statuses` and `ci_expected`.
- **FOUNDRY-ADR-0002** — the gate semantics, decided and written down: green means
  proven green (≥ 1 `success`, nothing red/pending, `neutral`/`skipped` tolerated
  alongside, never alone); amends ADR-0001's literal "all success" clause.
- **`rank_in_view`** in query payloads: dense 0..n-1 rank over the returned view,
  alongside the global (gappy-when-filtered) `rank_index`.

## 0.2.0 — 2026-07-01

### Added
- **Plugin hooks** (`hooks/hooks.json`) — the process becomes active without invoking a
  skill, in registered repos only:
  - `SessionStart` injects the Foundry contract (PR/merge only through the pipeline,
    ADRs settled unless superseded, which skills to use) into every session.
  - `PreToolUse` (Bash) denies `gh pr create` / `gh pr merge` and `git push` targeting
    the default branch — explicit refspec or bare push from it. The anti-rules are now
    mechanisms; unregistered repos are untouched and the guard fails open.
- **Two-stage pre-merge review**: the `reviewer` agent first verifies each acceptance
  criterion against the diff (covered / not covered / contradicted), then does the
  quality pass; `merge-pr` feeds it the AC and blocks on either stage.
- **Mid-flight memory**: `Tracker.add_comment` + `edit comment <ID> < note.md`,
  recent progress notes surfaced in `query issue` (`comments`), and a new
  `/foundry:resume-issue` skill that reconstructs tracker + git + PR state and
  continues without a human re-briefing.
- `setup_project … --import-adrs <dir>`: replicates repo bootstrap ADRs into the
  tracker KB, idempotent — closes ADR-0001's "répliqué en KB au provisioning".
- **Skill linter in CI** (`tests/test_skills.py`): every SKILL.md command must start
  with a binary its `allowed-tools` covers (no env-var prefixes, no `echo … |` pipes,
  no `${N:-…}`), and foundry invocations must go through the launcher.

### Hardened in review
- Guard: shlex tokenization (quoted text never trips it; `git -C`/`gh -R` global
  flags can't bypass it), `+main`/`--all`/`--mirror`/`--repo=` forms denied,
  {main, master} protected when the default branch is undeterminable, repo identity
  from the git remote across ALL trackers, cheap prefilter before any IO.
- CI gate: `passed` now requires at least one check-run concluding `success` —
  all-`skipped`/`neutral` (path-filtered workflows) proves nothing and refuses.
- `openpr` derives the issue id only from the `<type>/<ticker>-<n>-…` branch shape;
  head-moved detection matches `HTTP 409`, not any '409' substring; `import_adrs`
  dedups by exact title; SessionStart inlines the real launcher path and skips
  `resume`; docs aligned (keychain claims, Milestone enum, registry shape).

## 0.1.1 — 2026-07-01

### Fixed
- `query`: `blocked_by`/`unblocked` computed over the full graph before status
  filtering (a `ready` issue blocked by an `in-progress` one was reported unblocked);
  `milestones()` populates `blocked_ids`; a known issue with an unset State still
  blocks its dependents.
- YouTrack adapter emits normalized `Link.type` (`depends-on`, `blocks`, …) per the
  `models.Link` contract; the query tier keys off normalized forms.
- CI gate: zero check-runs refuses (empty proves nothing green); `--allow-no-ci`
  waives the zero-check case only, decided inside `ci_gate` (`waived` marker);
  merges are pinned to the gated sha (409 if the head moved); `check_runs` paginates.
- Skills invoke `python3 …/tooling/foundry_cli.py` (launcher) so commands match
  `allowed-tools: Bash(python3:*)` prefix rules; `< file` redirects instead of pipes.
- Assorted: scp-style ssh aliases in `parse_owner_repo`, strict CLI flags, issue id
  derived from the branch in `openpr`, `$top` on YouTrack list endpoints, dirty-tree
  guard in `start`, `.githooks/pre-push` shipped, marketplace source `./`.

## 0.1.0 — 2026-07-01

- Bootstrap: pipeline (frame → roadmap → next/start/open-pr/merge-pr → intake),
  query/write tiers, YouTrack + GitHub adapters (+ ghprojects stub), registry,
  reviewer agent, pure-logic tests, own-method dogfooding (FOUNDRY-ADR-0001).
