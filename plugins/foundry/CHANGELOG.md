# Changelog

## Unreleased

- PAT-142: CI speed, no change under `tooling/`. The `foundry` job runs the public suite
  in parallel (`pytest -n auto`, `pytest-xdist` installed in the job); a new `plan` job
  runs `scripts/ci_plan.py`, which skips the whole `foundry` job (check `skipped`) when a
  pull request changes only `plugins/ship-ios/` (only when `plan` says `false`), and runs
  everything on a push to `main`, on a release change (a plugin manifest or marketplace
  catalogue is touched), on any doubt, and when `plan` itself fails (`!cancelled()` and
  `!= 'false'` in the condition). `ship-ios` moves from `macos-14` to `ubuntu-24.04` (`ruby` installed if the image
  lacks it). `ship-ios` and `catalogue` still always run. The expected runner of
  `ship-ios` in `tests/test_youtrack_smoke.py` follows. Observed in CI run 38095058779
  (PR head `d640c1a`): `foundry` 5 min 49 (previous push to `main`: 11 min 50), Ruby
  already on the image, 35 `ship-ios` tests pass on Ubuntu; the runner core count and the
  Ruby version are not shown by the log. Not shown by any run (derived from GitHub
  documentation): a failed `plan` makes `foundry` run; the fork pull request case. Also
  fixes the flaky `test_B1_an_interrupted_execution_kills_the_whole_process_group` when
  started from a background job (test fixture only). Documented in the Foundry README.

- PAT-139 (PAT-ADR-0018): BEHAVIOUR CHANGE for every repository that uses the plugin.
  Rule R9 (b) of `AGENTS.md` / `CLAUDE.md` and the `merge-pr`, `intake` and `open-pr`
  skills now send the non-blocking review remarks deferred from the issues of an Epic to
  a companion Epic named `Nits <ORIGIN-EPIC-ID>`, which has no tracker link with the
  origin Epic (no parent, no dependency, no relation), instead of a follow-up issue under
  the origin Epic. Foundry now makes three tracker writes without asking, inside and
  outside an Epic campaign: creating the companion Epic the first time, creating a
  follow-up issue in it (per batch or per theme, never one per PR), and adding deferred
  remarks to such an issue; the coordinator reports them in the PR description. Nothing
  else is pre-authorized: the companion Epic and its issues are neither prioritized nor
  started without the maintainer. The special case "inside an Epic campaign the
  maintainer creates or approves the follow-up issue" is removed. No second-level
  deferral: non-blocking remarks on a PR of a companion Epic are corrected before the
  merge and fully re-reviewed. The origin Epic of an issue is its direct parent Epic. If the
  tracker durably refuses one of the three writes, no substitute write is made and the
  remarks are corrected before the merge; on a transient failure (quota, network) the
  write is retried and the merge waits for it, after a read of the tracker when its
  effect is unknown; a retry that the adapter itself refuses is treated as a durable
  refusal. An issue without an origin Epic gets no
  deferral: its remarks are corrected before the merge and fully re-reviewed. Not mechanical:
  no gate checks the companion Epic. Not coded yet: inside a campaign the authorization has no effect yet,
  because three things are missing (a channel that carries the remarks to the campaign,
  the code that creates the companion Epic and its issues, campaigns usable on the
  repository's tracker); left to a later issue, PAT-141
  ([review rounds](docs/review-rounds.md)). Six tracker conformance cases added, two per provider on its fake
  transport: the three writes, and a project where the `Epic` type is unavailable. Text and tests only; no change under `tooling/`.
- PAT-136: new process rule R9 in `AGENTS.md` / `CLAUDE.md` (author checklist for
  documentation sentences, grouped deferral of non-blocking review remarks to one
  follow-up issue per batch, minimal correction commits), written into the
  `start-issue`, `resume-issue`, `merge-pr`, `review-pr`, `open-pr` and `intake` skills and the
  `maigret` output format (optional "fix before merge" mark on a nit; verdict line and
  JSON object unchanged). Baseline, observation window and return triggers:
  [review rounds](docs/review-rounds.md). Behaviour change for every repository
  that uses the plugin (maintainer decision of 2026-10-10): after a fully validated
  review round, non-blocking remarks are deferred to a batch follow-up issue, and outside
  an Epic campaign the two intake writes for it (follow-up issue and its remarks) are made
  without asking (where the remarks go and which writes are made without asking are
  replaced by the PAT-139 entry above). Text and
  tests only; no change under `tooling/`.

## 1.1.0 — 2026-10-10

Prepared release candidate. This preparation (PAT-135) changes only version metadata
and documentation; the functional changes of 1.1.0 are the entries listed below. The
tag, an official update and the loaded-version readbacks remain pending. See
[release notes](docs/release-1.1.0.md), which open with the breaking changes, and
[upgrade and rollback](docs/migration-1.1.0.md). This dated source entry is not a
claim that 1.1.0 is already tagged or installed. The entries below are those merged
after `foundry-v1.0.0`, kept as written.

- Fixed (PAT-134; second review; tools, tests and documentation, no profile, routing, model, effort, setting or configuration key changes; recorded PAT-125, PAT-132, PAT-133 and PAT-134 results untouched): the documented rollback (emptying `CLAUDE_CACHE_TTL_1H_PINS`) made `foundry.cache_ttl_replay` and `foundry.claude_profile_trial` raise `IndexError` at import, which broke both command lines (PAT-132 / PAT-133 replay and PAT-125 trial mode included) and the collection of four test modules. Both derivations now tolerate an empty tuple: `ROLLBACK_MODEL` and `CACHE_TTL_POLICY` are then `None`, `rollback_decision` returns `unknown` with the new reason `no_model_carries_the_field`, and `--cache-ttl-trial` refuses (exit 2) creating nothing under the work directory; a test performs the first two rollback steps on a copy and imports both modules in a fresh interpreter. The rollback procedure in `docs/qualification/pat-134-subagent-cache-1h.md` now lists every step, file and test, as found by performing it on a copy of the repository and running the CI commands there. Statement of the threshold corrected: it is on the smaller of the two bounds of `usd.entry_reads_not_expired.delta_usd_exact`, in practice `favourable`, not `prudent` (which is the bound most favourable to keeping 1 hour); that quantity is more severe than `delta_usd`, not the most severe reading. Two remaining biases towards `keep` are named as limits of the decision (whole-read repricing on an in-lineage expiry despite a prefix shared with a sibling subagent; the `favourable` gap is not a rigorous lower bound), which a threshold without margin lets flip a marginal case. Also stated: the assumed asymmetry (no Sonnet 5.5 lineage at 1 hour gives `roll_back` even when no Sonnet 5.5 subagent ran), the Python call, keys and `reason` codes of `rollback_decision`, and `log_sha256` as a deviation from the FOUNDRY-ADR-0015 field list for the maintainer to acknowledge. Removed: a `below_minimum` state `cache_ttl_verdict` tested and nothing produced. The decision rule itself is unchanged.
- Added/fixed (PAT-134; offline tools, tests and documentation): the native cache-class trial result of 2026-10-10 is recorded as written in `docs/qualification/pat-134-native-trial.json` (verdict `conforming`: one Sonnet 5.5 read-only subagent 4,768 tokens in the 1-hour class and none in the 5-minute class; Opus 5.5 control 5,301 in the 5-minute class only; one run of two subagents, host 2.1.294; usage credits and quota unknown). Fixed: `--dry-run` of `foundry.claude_profile_trial` (`--cache-ttl-trial` and the PAT-125 default mode, which had the same defect) created the work directory and so made the next real run on that path refuse; it now creates nothing under the work directory. New `foundry.cache_ttl_replay.rollback_decision` (constants `ROLLBACK_MIN_SESSIONS`, `ROLLBACK_MODEL`, derived from `CLAUDE_CACHE_TTL_1H_PINS` through the new `CLAUDE_CACHE_TTL_1H_MODELS`) reads the PAT-134 rollback rule from a replay output: it keeps 1 hour only if the smaller bound of the new `usd.entry_reads_not_expired.delta_usd_exact` (simulated 5-minute cost minus real 1-hour cost, first-request entry reads counted not expired, unrounded; subagent_1h group only) is strictly positive, and is `unknown` when fewer than 3 sessions contribute a priced Sonnet 5.5 lineage observed at 1 hour, such a lineage is unpriced, or an unmodified profile appears at 1 hour; no replay rule changed. A refusal after policy resolution no longer leaves the work directory of the trial created; an ambiguous child request makes the trial's cache class `unknown`. Not observed, no guard: a host older than 2.1.248 might reject a profile carrying the field, which would break every Sonnet 5.5 launch there (unknown); the page `docs/qualification/pat-134-subagent-cache-1h.md` states it and declares the incident.
- Changed (PAT-134; separable commit of the branch; native trial of 2026-10-10 observed the 1-hour class on the modified profile, `docs/qualification/pat-134-native-trial.json`): the ten Sonnet 5.5 versioned profiles (`routed-{readonly,worker}-{low,medium,high,xhigh,max}-sonnet-5.5`) carry `experimental: {cacheTtl: 1h}` (nested map; per the Anthropic documentation read on 2026-10-09, Claude Code 2.1.248 or later, ignored under usage credits; not re-verified, no guard added). New public constant `CLAUDE_CACHE_TTL_1H_PINS` in `routing_facades.py`; `claude_pin_profile_text()` renders exactly this block for these pins and an `experimental` block in a generic template is an invalid template; `claude_invocation_binding()` refuses any divergent profile (field missing, on another pin, another value or key). No other profile, routing, model, effort, setting or configuration key changes. R7 of `AGENTS.md` and `CLAUDE.md` (byte-identical) states it. No gain is announced; effect on the subscription quota: unknown. Observation window and rollback: `docs/qualification/pat-134-subagent-cache-1h.md`.
- Added (PAT-134; tools, tests and documentation only in this part: no profile, routing, model, effort, setting or configuration key changes; no cloud call from the tests; PAT-125, PAT-132 and PAT-133 recorded results untouched): `foundry.claude_profile_trial --cache-ttl-trial`, a separate bounded native trial (one `claude -p` parent, two subagents one after the other: a Sonnet 5.5 read-only profile and an Opus 5.5 worker control; requested / transmitted / observed kept apart; cache-creation tokens per 1-hour / 5-minute class read from the host session logs within FOUNDRY-ADR-0015, fields beyond it named; `conforming` only on 1-hour-only for the Sonnet subagent and 5-minute-only for the control, anything unobserved `unknown`; refuses an existing work directory or result file; result schema `foundry.pat134-cache-ttl-trial.v1`), and `foundry.cache_ttl_replay --subagent-1h-to-5m` (opt-in, with `--host-session`; a subagent lineage whose writes were all observed at 1 hour is replayed towards 5 minutes with the main-conversation rule, as the group `subagent_1h`; default outputs unchanged). The observation window, replay selection, rollback threshold (on the smaller of the two bounds, in practice `favourable`, not the `prudent` one) and exact rollback are written before adoption in `docs/qualification/pat-134-subagent-cache-1h.md`; behaviour on a host older than 2.1.248 and under usage credits is stated as documented / observed / unknown, with no guard. No gain is announced; effect on the subscription quota: unknown. Documentation status (R5): `docs/model-routing.md` and that page.
- Added (PAT-133; offline read-only analysis and tests only, no `claude`, no cloud arm, no model, no `lms`, no network call; no setting, agent profile, routing or launcher change; PAT-132 results and aggregate file untouched): extends `foundry.cache_ttl_replay` to INTERACTIVE host sessions, both directions of the prompt-cache duration. New options `--host-session MAIN_LOG` (repeatable; the subagent logs are the `*.jsonl` under `<session>/subagents/`; replaces `--ledger` and `--session-logs-dir`, no campaign ledger) and `--until ISO_INSTANT` (with `--host-session` only; ignores every later record so a log still being written replays reproducibly); output schema `foundry.cache-ttl-replay.interactive.v1`; exit codes 0 or 2 and the PAT-132 mode, errors and outputs unchanged. Rules written in the docstring and the results document before any figure: direction A (main conversation, observed 1 hour to simulated 5 minutes: the PAT-132 in-session rule per model-alias lineage; a model switch opens a lineage, a compaction is not detected) and direction B (subagents, observed 5 minutes to simulated 1 hour: an expiry re-write is recognised from counters and gaps only, prudent bound = certain expiry and only the prefix part the reference request had read, favourable bound = possible expiry and the whole prefix cached before the gap, gaps over 3,600 s expire under both). Reader per FOUNDRY-ADR-0015: counters, alias, timestamp; fields read beyond its list named (`type`, `message.id`); provenance `host_reported` / `pricing_derived` / `unavailable`; an unpriced lineage (day before the grid, alias without entry, Haiku 5.5) keeps its alias and leaves both sides of every USD comparison; the logical role is not derivable and is not reported. Reader repairs declared in the results document (made after a first run refused most real logs): split on `\n` only, streamed output counts, zeroed copies, ambiguous write classes, last partial line with `--until`. Result on 3 real sessions (1 priced, the grid starts 2026-10-05): main 1h to 5m is a net loss under both bounds (+119.6% to +273.7% list cost, Opus 5.5, 1,368 requests); subagents 5m to 1h is a net gain overall (-3.7% to -6.7%, 4,207 requests) but a net gain for Sonnet 5.5 (-28.8% to -33.1%) and a net loss for Opus 5.5 (+8.0% to +10.2%); nothing is concluded about the subscription quota (effect unknown), no generalisation beyond the sessions replayed, and which change would be justified (per agent profile, Sonnet only, by a change ticket with a native trial) is stated, not done. Review corrections (2026-10-10, declared in the results document, earlier values kept): `requests_found_expired` no longer counts first requests (new key `first_requests_found_expired`), `--until` now freezes the subagent-log count and lists a log without request, robustness keys added, both bounds stated as assuming a context that only grows. Docs: `docs/qualification/pat-133-cache-ttl-interactive-v1.md` and the aggregate file `pat-133-cache-ttl-interactive-v1.json`, section added to `pat-19-launcher-v1.md`. R5 status: new options, constants, result keys and unavailability codes defined in the results document; no CLI verb of `foundry_cli.py`, no routing table, no configuration key changed; the FOUNDRY-123 detector is not shipped, so this status is asserted here and checked in review.
- Added (PAT-132; offline read-only analysis and tests only, no `claude`, no cloud arm, no model, no `lms`, no network call, no campaign mode run by the diff; frozen v1 to v5 protocols, configs, results and reports untouched, no verdict recomputed; no launcher, routing or cache setting changed): replay of the PAT-19 v4 and v5 host session logs (kept off the repository) with the 1-hour prompt cache as recorded against a simulated 5-minute cache. New module `foundry.cache_ttl_replay` (`python3 -m foundry.cache_ttl_replay`: `--ledger` repeatable, `--session-logs-dir`, `--grid`, `--out`; exit 0, or 2 when an input is refused); the simulation rule (a request whose gap to the previous request of its session exceeds 300 s finds the cache expired and rewrites what it read at the 5-minute write price) is written in the docstring and the results document before any figure and gives a prudent and a favourable reading of the gap, never one figure; the reader (FOUNDRY-ADR-0015) extracts only token counters, model alias, timestamps and the message id to count a request once; unusable sessions are listed and excluded from both sides. Result as it came out after the correction below: 104 of 104 cloud sessions replayed, catalogue list cost 25.06 USD with 1-hour writes against 20.92 (prudent) and 19.87 (favourable) with a simulated 5-minute cache, a net gain under both readings (one in-session request over 5 minutes under the prudent one, none under the favourable one; one session undecidable between the bounds); first figures before the correction (19.94 and 19.67) are kept in the aggregate file as `*_before_correction`. Correction of 2026-10-09, made after the first figures were seen (found by the coordinator before review): the first request of a session can read a cache entry written by an earlier session (102 of 104 sessions, 340,392 tokens), which the rule had not priced; prudent counts all of them as expired, favourable keeps the read when the nearest earlier session of the same campaign and model ended at most 300 s before (an assumption, the writing session is not observable); second correction after the independent review: prudent reference of the second request is now the record preceding the first assistant record (104 gaps changed by 1.4 to 15.2 s, no figure moves), a request without cache read or write no longer refreshes the entry (0 such requests), sessions with several model aliases are excluded (none), and the 1.056 USD between the bounds split into 0.787 (first-request reads) and 0.270 (one reviewer request at 388.9 s); nothing is concluded about the subscription quota (effect unknown), and the way to transmit the setting (settings file against the closed child-environment allow-list of AGENTS.md R6) is left open for a change ticket. Docs: `docs/qualification/pat-19-cache-ttl-replay-v1.md` and the aggregate file `pat-19-cache-ttl-replay-v1.json`, section added to `pat-19-launcher-v1.md`. R5 status: new module entry point, options, exit codes, vocabularies and result codes documented there; no `foundry_cli.py` verb or option, routing table or frozen protocol changed; FOUNDRY-123 detector not shipped, status asserted here and checked in review.
- Added/changed (PAT-131, PAT-ADR-0017; Linear only; offline tests on the existing fakes, no tracker write and no network call run by the diff; PAT-ADR-0006 vocabulary unchanged: bounded detection, never CAS, residual S1→S2 risk named). (1) Abandon: `edit transition <ISSUE-ID> dropped <expected-state>` writes only the native State under S1-S4 from `backlog`, `ready`, `blocked`, `in-progress` or `review`; the expected predecessor is mandatory (refused before any provider read) and compared with the native state; refused from native `done`, with a `state-done` receipt, with an Epic closure audit on the issue or with an invalid receipt chain; a replay on an already dropped issue converges without a write, a third state fails closed; no receipt is written, deleted or rewritten; a linked PR is not closed and the output names it. `edit set-field <ISSUE-ID> State dropped` now takes that same guarded path (BEHAVIOUR CHANGE: it was an unguarded grooming write; it is now refused from `done` and cannot be combined with another field). New capability flag `Tracker.guarded_abandon_supported` (`True` on Linear only). (2) Read: a natively cancelled issue whose append-only projection is valid and non-terminal reads `state=dropped`, `normalized_state=dropped`, `projection_status=aligned` instead of a strict-read conflict / observed `disagreement`; with a `done` receipt the conflict is unchanged; the tolerance covers those receipts only: a never-started cancelled issue reads as before (`dropped` / `native-only` with no receipt, strict and observation reads; with advisory `cockpit-evidence` receipts and no lifecycle state receipt, a strict-read conflict and observed `dropped` / `native-only`, both as at `ff9a53a`). BEHAVIOUR CHANGE: a lifecycle write (start, review, done) on a natively cancelled issue is now refused first, with `Linear issue natively dropped; lifecycle write refused`, before any receipt is appended. For a never-started cancelled issue, compared on the fake Linear transport with `ff9a53a`: starting it or sending it to review was already refused there (`Linear native state changed outside lifecycle`), but only after the receipt comment had been appended, which left the issue a strict-read conflict and an observed `in-progress` or `review` / `disagreement`; it is now refused with no write and the issue still reads `dropped` / `native-only`. `done`, an AC proof and an AC override were already refused there before any write; `done` now gets the new message, the other two keep theirs. The same comparison against the real Linear API: unknown, not run. (3) Closure: `issue close-epic <EPIC-ID> --human-verdict=accepted --accept-dropped=ID[,ID...]` closes an Epic whose required graph has abandoned nodes; the named set must be exactly the set of dropped nodes of that graph; a dropped node is never counted as accepted and the prerequisites reached only through it are not required; at least one direct child must be `done`; without the flag Linear keeps its refusal, whose diagnostic now says "abandonné (dropped), acceptable nominativement" and prints the exact command, combined with `--accept-override` when both apply. No receipt field is added: the dropped node stays in the bound graph (id, version, state), so the audit id derives from the set, exact replay converges, a different set is refused, and existing audit ids are unchanged (golden test); `close-epic --status` prints the dropped nodes bound by the receipt. New capability flag `Tracker.epic_dropped_closure_supported` (`True` on Linear only); new public helpers `write.parse_accept_dropped` and `write.epic_receipt_dropped`; `write.transition` and `write.set_field` now return the adapter's result. YouTrack, GitHub Projects and DevHub refuse `--accept-dropped` before any read and keep their current behaviour; the receipt validator (`write._validate_epic_outcome`, new `tracker` argument passed by every caller) admits an abandoned node in a bounded receipt only for a provider declaring `epic_dropped_closure_supported`, and otherwise keeps the refusal it had before this change. Review follow-up, same entry: `transition ... dropped <expected-state>` checks that the named predecessor is one of the five before any read, so a replay naming another value (for example `done`) is refused instead of converging (the check sits in `write.transition`, ahead of the repository-binding read that already calls the provider, and reads the adapter's new `Tracker.abandon_predecessors`, the single definition of the five states, empty on the other providers; the adapter keeps its own check), while a replay naming an admitted one, and `set-field`, still converge without a write; the abandon compares the named predecessor with the native state whatever the projection says, so a started issue moved natively to `backlog`, `ready` or `blocked` is abandoned from that native state; and an abandon is refused before any write on a chain of advisory `cockpit-evidence` receipts with no lifecycle state receipt, whose result a strict read would not read back. Limits named, not fixed: replay divergence when a node is cancelled or reopened after the closure, a pending audit whose graph changes, reopening a started-then-dropped issue, a started issue moved natively to `backlog`, `ready` or `blocked` and not abandoned (strict-read conflict), release scope of a dropped issue, and the unguarded `set-field State done`. R5: `docs/tracker-contract.md` (PAT-ADR-0017 paragraph, Epic closure row), `docs/tracker-contract.v1.json` (Epic closure cells and DevHub note), `docs/linear-tracker.md` (abandoned-issue and Epic paragraphs), `skills/close-epic/SKILL.md`, `README.md`; artefacts documented there: CLI forms `edit transition <ISSUE-ID> dropped <expected-state>`, `edit set-field <ISSUE-ID> State dropped` and `issue close-epic --accept-dropped=ID[,ID...]`, capability flags `guarded_abandon_supported` and `epic_dropped_closure_supported`, adapter vocabulary `abandon_predecessors`; not documented under `docs/`, as Python-level helpers no documented surface cites: `write.parse_accept_dropped`, `write.epic_receipt_dropped`, the `tracker` argument of `write._validate_epic_outcome` and the field `EpicClosureState.dropped` (its value is the documented `close-epic --status` line); no configuration key and no `foundry_cli.py` verb added; the FOUNDRY-123 detector is not shipped, status asserted here. Tests: `test_abandon_and_dropped_closure.py`, `test_issue_close_epic.py`, `tests/fixtures/tracker-conformance-v1.json`.
- Docs (PAT-130; documentation only, no code, no model, no `claude`, no cloud call, no campaign mode run by the diff; frozen v1 to v5 protocols, configs, results and reports untouched (only a pointer line added to the non-frozen bilan and launcher documents), no verdict recomputed or requalified; nothing promoted, no local model or default activated, no bill gain claimed, Epic closure neither decided nor performed): `docs/qualification/pat-19-decision-v2.md` records the PAT-19 decision by use (autonomous local implementation: keep the cloud, v1; local read-only exploration: keep the cloud, gain not demonstrated, v2 to v5, with the PAT-129 price-weighted readings labelled outside the rule; local compression in one call: track abandoned on this corpus on the coordinator's recommendation approved by the maintainer, never measured), with compatibility, quality and economy separated, what is not decided, the fate of PAT-113, PAT-118, PAT-119 and of the c1 draft, and the tracks the measures point to; one pointer line each in `pat-19-local-first-bilan.md` and `pat-19-launcher-v1.md`. R5: no CLI verb or option, config key, public constant or routing table changed; FOUNDRY-123 detector not shipped, status asserted in the document and checked in review.
- Changed, BREAKING (PAT-125, phase 3, PAT-ADR-0016; offline and fake tests only in the diff, no `claude`, no model, no paid invocation run by the diff; cheaper-candidate regime of FOUNDRY-ADR-0019 on list price per token only: no measured gain of quality or cost, no bill or quota saving claimed, no benchmark, replay, comparison or effort matrix; the effort `medium` is the provider default and the maintainer's choice, unmeasured; frozen PAT-19 files untouched): `DEFAULT_MAPPINGS["claude"]["economy"]` goes from Haiku 4.5 / null to `haiku-5.5` / `medium`. Two native compatibility trials of 2026-10-09 are committed unchanged: the first, [`pat-125-native-trial.json`](docs/qualification/pat-125-native-trial.json), keeps its verdict `not_conforming` (fixture round-trip only) and is not reinterpreted; the maintainer then authorised a second trial, the new decision the ADR requires; the second, [`pat-125-native-trial-2.json`](docs/qualification/pat-125-native-trial-2.json), run with `--neutral-fixture`, has the verdict `conforming` (profile, model `claude-haiku-5-5`, effort `medium`, host version 2.1.294 observed by the hook, fixture: all exact). A trial is a compatibility smoke, not the basis of the promotion. The mapping is per tier: the scout and every non-gate role falling back to `economy` get it. Reviewer and architect floors, `balanced`/`frontier`/`apex` and the Codex defaults are unchanged; the short `haiku` alias is not promoted. Breaking 1: a Claude Code host below 2.1.293 loses the default `economy` tier (the Agent call, or the headless launch, is refused with a message naming 2.1.293) until it is updated or the project maps `economy` to Haiku 4.5 with `"effort": null`; there is no automatic fallback. Breaking 2: the `economy` default now carries an effort, so a project mapping `{"model": "haiku-4.5"}` without an `effort` key (or the `haiku` alias), and a direct request for `haiku` / `haiku-4.5` on a tier whose effort is not null, are refused with the message asking for `"effort": null` in `mappings.claude.<tier>`; an economy model-only override to a non-Haiku model now inherits `medium` instead of failing on an inherited null. Breaking 3: an availability list without `haiku-5.5`, such as the formerly documented `FOUNDRY_CLAUDE_AVAILABLE_MODELS=haiku-4.5,sonnet-5.5,opus-5.5`, puts the `economy` target outside the list: the scout is refused even on a conforming host (`Aucun modèle disponible pour le rôle 'scout' sur claude. Niveaux inférieurs essayés : economy.`) and every non-gate downward fallback reaching `economy` ends the same way; add `haiku-5.5` to the list. Breaking 4: an effort-only `economy` override now applies to Haiku 5.5: `{"effort": "low"}` used to give Haiku 4.5 without effort and now silently gives `haiku-5.5` / `low`, declared but not qualified; `{"effort": null}` used to pass and is now refused (`effort null réservé à Haiku 4.5`); name the model too or remove the override. Review fixes in the same change: the Haiku 4.5 effort refusal names the fix of its own source (inherited default, project mapping or user effort) and gives the whole entry `{"model": "haiku-4.5", "effort": null}` when the mapping does not name the model; the hook's below-minimum refusal says that a session resumed after a host update can still carry the old version and that a new session resolves it; the headless runners apply the minimum to a dated snapshot `claude-haiku-5-5-YYYYMMDD` too (the hook refuses such an identifier first for its missing preloaded profile), probe `--version` in the launch's own working directory, and write their unobserved-version notice as one line on standard error at every launch, without any warning filter or other process-wide state; a transcript whose last record exceeds the one-mebibyte tail is a documented cause of `unknown`. The maintainer confirmed the effort on 2026-10-09 (« Médium très bien ») after being told it is the provider default, not a measured choice. The second trial establishes that the marker is in the parent's final answer, not strictly that it transited through the child, and its neutral fixture changes two things at once, so the cause of the first divergence is not isolated. Project rollback: `{"mappings": {"claude": {"economy": {"model": "haiku-4.5", "effort": null}}}}`; product rollback: restore the default by an ordinary PR, the declaration stays. Known unmonitored cost risk: list price times five above 100,000 prompt tokens, and reasoning tokens at `medium`. Observation window, reference and rollback triggers: [`pat-125-haiku-55-promotion.md`](docs/qualification/pat-125-haiku-55-promotion.md). Documentation status (R5): `docs/model-routing.md` (defaults table, PAT-125 section, PAT-16 sentences), R7 of `AGENTS.md`/`CLAUDE.md` (identical), the decision note, the `FOUNDRY_CLAUDE_AVAILABLE_MODELS` option example in `plugin.json`.
- Recorded, fixed and prepared (PAT-125, phase 2, PAT-ADR-0016; offline and fake tests only in the diff, no `claude`, no model, no paid invocation run by the diff; no measured gain, bill or quota saving claimed; frozen PAT-19 files untouched): at that revision the Claude `economy` default was not changed and the incumbent Haiku 4.5 / null was kept, as PAT-ADR-0016 requires after a non-conforming verdict (superseded by the phase 3 entry above, after a second trial authorised by the maintainer). The first bounded native trial of PAT-ADR-0016 was run once by the coordinator on 2026-10-09 from `8de5c1e`; its result is committed unchanged as [`pat-125-native-trial.json`](docs/qualification/pat-125-native-trial.json): launched profile, executed model (`claude-haiku-5-5`), observed effort (`medium`) and host version (2.1.294, observed by the hook itself) exact, fixture check `divergent`, verdict `not_conforming`. The ADR keeps the incumbent in that case and authorises no further trial without a new decision; the reading of the divergence (from the raw stream, not from the tool), what is and is not established, and the three options then put to the maintainer are in [`pat-125-haiku-55-promotion.md`](docs/qualification/pat-125-haiku-55-promotion.md). Fixed: the headless runners (`command_runtime.py`, `campaign_runtime.py`) passed a pin with a minimum host version to `claude` unchecked; they now call `claude_headless_host_version_requirement()` (`routing_facades.py`), which, only for such a pin, asks the `claude` about to be launched its `--version` (local, unpaid, same runner and child environment), raises the same `RoutingConfigError` naming required and observed versions below 2.1.293 before any provider invocation, and on an unreadable answer lets the launch proceed with a notice carrying `CLAUDE_HOST_VERSION_UNOBSERVED` (a `RuntimeWarning` at that revision, a line on standard error since the phase 3 entry above) (never presented as conforming; receipts unchanged; R6 allow-list untouched). Prepared, not run by the diff and authorising nothing by itself: `--neutral-fixture` on `foundry.claude_profile_trial` (a public fixture marker instead of a value named token, a parent told to wait for the agent's answer; default fixture, verdict rule and recorded result unchanged). Documentation status (R5): `docs/model-routing.md` (PAT-125 section: status, headless runners), R7 of `AGENTS.md`/`CLAUDE.md` (identical), the decision note.
- Added (PAT-125, phase 1, PAT-ADR-0016; offline and fake tests only, no `claude`, no model, no paid invocation run by the diff; no benchmark, replay, comparison or effort matrix; no measured gain, bill or quota saving claimed; frozen PAT-19 protocols, configs and results untouched): Claude Haiku 5.5 is DECLARED, the `economy` default is not changed by this phase (see the phase 2 and phase 3 entries above for the trials and the promotion). Declaration: `haiku-5.5` / `claude-haiku-5-5` in `_CLAUDE_MODEL_DECLARATION`, its own effort scope `(claude, haiku-5.5, v1)` `low` to `max` in `default-effort-scopes.json`, ten versioned profiles `routed-{readonly,worker}-{low,medium,high,xhigh,max}-haiku-5.5` (74 generated profiles, was 64), `haiku-5.5` in the telemetry vocabulary (no price row: cost unknown, never zero). Haiku 4.5 and its `20251001` snapshot stay exact effort-free historical pins; the short `haiku` alias is unchanged (unobserved version, `CLAUDE_ALIAS_VERSION_UNOBSERVED`). Minimum host version: `CLAUDE_MODEL_MIN_HOST_VERSION = {"haiku-5.5": (2, 1, 293)}`; the routing hook observes the host version from the top-level `version` of the last versioned record of the session transcript named by its payload (`claude_host_version()`, bounded tail, never the environment, never a `claude` on `PATH`); observed below 2.1.293 the Agent call is denied with a message naming the required and observed versions and nothing is substituted (no fallback to Haiku 4.5, to the alias or to another tier); an unobservable version is `unknown`, said so in the new hook-context key `host_version` and by the new warning `CLAUDE_HOST_VERSION_UNOBSERVED`, and the launch proceeds (trade-off documented). The Haiku 4.5 effort refusal now names the received effort, its source and the fix (`"effort": null` in `mappings.claude.<tier>`). Trial material, not run by the diff: `python3 -m foundry.claude_profile_trial` (`--work-dir`, `--out`, `--claude`, `--parent-model`, `--projects-dir`, `--dry-run`) launches one `claude -p` parent in source mode on an isolated fixture, kills it at twenty minutes, refuses any replay and writes one `foundry.pat125-native-trial.v1` result keeping requested, transmitted and observed apart, an absent observation staying `unknown`. Decision note, trial command, observation window and rollback: [`pat-125-haiku-55-promotion.md`](docs/qualification/pat-125-haiku-55-promotion.md). Both project rollback forms and the restored default are tested offline against the prepared default (`tests/test_claude_haiku55.py`). Documentation status (R5): `docs/model-routing.md` (new PAT-125 section, 74 profiles, configuration example now writes `"effort": null`), R7 of `AGENTS.md`/`CLAUDE.md` (identical), the decision note; `claude_invocation_binding(host_version=...)` is an internal optional argument, not a caller-facing surface.
- Added (PAT-129; offline read-only analysis and tests only, no `claude`, no cloud arm, no model, no `lms`, no campaign mode run by the diff; frozen v1 to v5 protocols, configs, results and reports untouched, no frozen verdict recomputed or requalified): where premium work goes in the PAT-19 v4 and v5 comparisons. New module `foundry.cost_breakdown` (`python3 -m foundry.cost_breakdown`: `--results`, `--ledger`, `--report`, `--grid`, `--streams-dir`, `--session-logs-dir`, `--override-rate`, `--out`): premium tokens by role, token class and model per arm, unweighted and weighted by the new dated price grid `tooling/foundry/pricing-breakdown-v1.json` (Claude 5.5 list prices captured 2026-10-08 from the official pricing page; both cache-write bounds, 5-minute and 1-hour; a model without an entry valid on the session day, or Haiku 5.5 whose price depends on the unrecorded prompt length, stays `unavailable`; `pricing-v1.json` and `cost_attribution.py` untouched), the weighted L over A ratio per accepted task on the v5 paired set as a reading outside the frozen rule, a `task_coverage` block (the arms do not spend premium on the same tasks: arm L spends nothing where its local exploration is contaminated, PR 48 in v4, PR 33 and 42 in v5, so arm totals are only compared on the tasks where both arms spent premium, as a non-decisional reading outside the rule), and, from the raw transcripts kept off the repository, the read/search tool calls of the cloud sessions and the direct weight of the API calls made only of them under the hypothesis that they cost nothing (not a ceiling of the effect of a reduction: it omits the carried context and is a lower-bound classification; aggregates only), a sensitivity block (`--override-rate`) for the Sonnet 5.5 cache-read price that the page (0.10) and the host's own list cost (0.20) disagree on, output tokens per call joined from host logs on permitted fields only (model alias and token counters, via a counters-only reader; any ambiguity makes the role `unavailable`), exit code 1 also when the report's paired accepted counts differ from the records (file still written). Totals equal those of the committed reports. Results, method, limits and unknowns: `docs/qualification/pat-19-cost-breakdown-v1.md` and the two aggregate files `pat-19-cost-breakdown-v1-x4compare-1.json` / `-x5compare-1.json`. API list prices are weights under a subscription, never a bill: no billing gain is announced. R5 status: new module entry point, new price grid and new documents documented in that page and in `pat-19-launcher-v1.md`; no `foundry_cli.py` verb or option, routing table or frozen protocol changed.
- Fixed/added (PAT-128; offline replay and tests only, no `claude`, no cloud arm, no model, no `lms`, no campaign mode run by the diff; frozen v1 to v5 protocols, configs and results untouched, no verdict recomputed, `FROZEN_PROTOCOLS` unchanged): the two instrument defects of the PAT-19 v5 comparison. (1) The two contaminated explorations (PR 42 and PR 33 of `pat-19-x5compare-1`) are explained by an offline replay of the audit on the 86 raw streams kept off the repository ([`pat-19-audit-replay-v5.md`](docs/qualification/pat-19-audit-replay-v5.md), `pat-19-audit-replay-v5.json`, `pat-19-audit-replay-v5-quoted.json`): the answer `Path '<path>' not found` of the omp `read` tool was not recognised as an absent path (only `Path not found: <path>` was), so the path of the failed call and its echo were counted as a read under the work root; hypothesis of the v5 results confirmed. New config key `isolation.audit_absent_path_forms: "quoted_path"` (constants `ABSENT_FORMS_KEY`/`ABSENT_FORMS`, parameter `absent_forms` of `audit_transcript` and `replay_audit`, record field `audit.absent_path_forms` only under the key), accepted only under a protocol after v5 with `audit_revision` 2 and refused under v1 to v5 and the v5 pilot; without it every behaviour, argv, env, bundle and record of v1 to v5 is byte-for-byte unchanged (the replay of the 69 v5 records and of the 32 v4 records without the key reproduces the earlier result). `replay-audit --quoted-not-found` measures the coordinate on a campaign that does not carry it; a replay of a campaign with an audit policy adds `arm_findings_replayed`. (2) The test that depended on the maintainer's real `~/.config/foundry/config.env`: 11 of the 12 v5 task bases predate PAT-104 and lack the isolation fixture, and the error appears in the streams of 6 tasks (PR 24, 26, 27, 37, 38, 42); the probe of the real-config reads was run on the PR 42 base only (`tests/test_doctor.py` and `tests/test_agent_routing.py` there look at the real config, which the native sandbox refuses with EPERM); the current suite is already isolated by PAT-104 for reads and writes, and `tests/conftest.py` now also refuses `os.stat`/`os.lstat` of the real state (`Path.exists()` was the gap: the audit hook has no `stat` event); known gaps: `os.access`, `os.scandir`/`DirEntry.stat`, a relative path given with `dir_fd`, subprocesses. No product code changed for (2). Docs: `docs/qualification/pat-19-audit-replay-v5.md`, "Forme citée du chemin absent (PAT-128)" in `docs/qualification/pat-19-launcher-v1.md`. R5 status: new config key, constants, parameter, CLI option and record fields documented there; no `foundry_cli.py` option, routing table or routing constant changed; FOUNDRY-123 detector not shipped, status asserted here and checked in review.
- Docs/evidence (PAT-127; docs and raw evidence only, no code change, no model load by the diff, no cloud call by the diff; frozen v1 to v5 protocols, configs and v1 to v4 results untouched): real PAT-19 v5 comparison `pat-19-x5compare-1` run on 2026-10-08 (frozen tooling `main` at `d173e17`, Claude Code 2.1.285, arms A and L, candidate `qwen3.6-35b-a3b-mlx-4bit` fixed, the 12 corpus tasks; 12 launches, model reloaded before each task under the maintainer's block mandate of the day, not confirmed one by one; Foundry state backed up off repo before the launch; observed context 262144 despite `-c 65536`; OrbStack and ChatGPT stopped for the campaign, 24 machine preflights all accepted, no exit code 3 or 4). Result: 74 cloud executions (cap 150), 19,308,267 premium billing tokens (A 11,433,953; L 7,874,314); decision `keep_cloud`, `campaign_conclusion` `keep_cloud`, `paired_rule.reason` `not_retained_on_paired_set`, failed criterion economy only, compatibility pass. Paired decided set D = 10 tasks (threshold 9); accepted on D A 5, L 5 (the accepted sets differ on 4 of 10 tasks); premium per accepted task A 1,835,739, L 1,574,863, ratio 0.8579 against 0.85 (14,485 tokens per accepted task above the line); accepted over the 12 tasks A 6, L 5. The two local explorations of PR 42 and PR 33 were flagged `contaminated` (the local model typed a path to a file that does not exist and the `read` tool answered with a wording the audit does not recognise as "not found"; read in the raw streams and the code, cause not replayed): both tasks leave D, no token spent, no verdict recomputed. 17 reviewer verdicts (11 PASS, 6 BLOCK), none unreadable; audit journal 13 records / 21 entries, 49 host-refused calls (Bash 48, Write 1), 10 records echoing a pytest `PermissionError` on the Foundry config file under the native sandbox (effect on outcomes unknown); no pilot stop criterion applied during the campaign. Erratum to protocol v5 section 2 case (ii) carried in the results document (the v2 to v4 quality reading is by bounds; this campaign is in case (ii): `inconclusive` under the v2 to v4 reading of the same records, `keep_cloud` under v5). Non-decisional readings (sensitivity to one task, role breakdown, reviewer share) are labelled as such; no billing gain announced; no promotion, no local model or default activated. Report `docs/qualification/pat-19-exploration-results-v5.md`, bilan v1 to v5 for Epic PAT-87 `docs/qualification/pat-19-local-first-bilan.md` (no decision taken for the maintainer), raw files `docs/qualification/pat-19-runs/x5compare-1/` (the 86 raw streams are not committed; manifest by sha256). R5: report, bilan and raw pieces added, link in the "Protocole v5" section of `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v5 and config v5 untouched. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Fixed and re-frozen (PAT-126 review round 4, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; no new pilot; v1 to v4 and their report output untouched): the review round 4 found the acceptance criteria met and nothing blocking; two findings reopen the fourth freeze (`bbbb8ef`, committed on the branch, never merged, like `b699ed9`, `56634fe` and `e6f90c6` before it); `pat-19-campaign-v5.json` sha256 `fbac092181d373602d94b58ec469b07ed81853520bac4f1dbac7ec7ddad660db` (hash test). No value of the rule changed (0.85, threshold 9, feedback, bounds). Paired rule, order of reading of the zero case (coordinator's decision, last adjustment of that case; report only): on the paired set the acceptance comparison is read BEFORE the zero case, so accepted_L(D) < accepted_A(D) keeps the cloud (`not_retained_on_paired_set`) whatever the zeros, including L accepting no task of D while A accepts at least one - the shape of the v4 result (A 1, L 0), which was read `inconclusive` (`no_accepted_task_in_one_arm_ratio_undefined`) although v2 to v4 gave `keep_cloud` there; that reason remains for A accepting no task of D (both arms at zero, or A at zero and L at least one: no ratio can be computed, never zero, never an infinite ratio read as a pass); new report field `paired_rule.failed_criteria` (`acceptance`, `economy`). Text: the 'remaining asymmetry' of protocol section 2 point 6 and the operator notice were false for a round 0 cut during its cloud execution (the relaunch writes NO record and stops at the same arm of the same task, later arms and tasks are never attempted, the campaign stays `incomplete_campaign` unless it was L's round of the 12th task; `stopped_by_cap` records only follow a cut at a correction round, of a reviewer after a verdict, unreadable tokens or a never-settled start), now written exactly, with a test; the summary 'what v5 changes' lists every case where the verdict label differs from v2 to v4 (cross-checked by comparing both rules on 6000 random synthetic campaigns, not committed); section 2 point 3 (order), point 5, the section 3 table, the config notes (same edits in the unpinned pilot config) and the launcher doc aligned; chronology: order of the zero case adjusted after round 4. The maintainer's explicit agreement on the rule change is still to be recorded on the issue before the merge. Stale test headers ('DRAFT') fixed. `pat-19-golden-check-v5.json` (committed at `5b74996` from the reopened freeze `bbbb8ef`) is removed from the tree again for the coordinator to regenerate from the new freeze commit. Tests in `test_local_first_exploration_v5.py`. R5: report field `paired_rule.failed_criteria`, documented in the protocol (sections 2, 7.4, 8) and the launcher doc; the FOUNDRY-123 detector is not shipped, status asserted here.
- (Superseded: this fourth freeze was REOPENED by the review round 4 and redone, see the entry above; its sha256 and its statement on `pat-19-golden-check-v5.json` are those of that freeze) Fixed and re-frozen (PAT-126 review round 3, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; no new pilot; v1 to v4 and their report output untouched): the third freeze (`e6f90c6`, committed on the branch, never merged, like `b699ed9` and `56634fe` before it, each reopened by a review round) is reopened and redone; `pat-19-campaign-v5.json` sha256 `093b89022edced18f57237b6cdce62a835fdba52a62a4997e21d0d364693860a` (hash test). No value of the rule changed (0.85, threshold 9, feedback, bounds). Shared temp directory (this code runs at each cloud execution; offline tests only): the watch lists top-level NAMES only (`_temp_names`, `os.listdir`; it stat-ed every entry and an entry vanishing meanwhile emptied the listing, so that on the listing made BEFORE every entry became new and a PRE-EXISTING matching entry could be moved, against the frozen sentence 'an entry that existed before is never touched'; on the listing made after, or on a failed move, nothing was moved and the record said `count: 0`). A failed listing made before now moves nothing; `audit.temp_leftovers` gains `watch` (`complete`, `partial`, `unavailable`, `not_applicable`), `not_moved`, `not_inspected`, `unwatched` (`no_tmpdir`, `listing_before_failed`, `listing_after_failed`), and `count` is null, never 0, as soon as one execution of the record was not watched; `_holds_bundle_material` says `None` when it gives up at `TEMP_WALK_BOUND` (20000 entries) or cannot read a directory; `report.temp_leftovers` also lists the records whose watch is partial or unavailable; no `OSError` of the watch aborts a paid execution. Paired rule (report only): the premium of an `interrupted` record that names no cloud execution (`_cut_without_cloud`: a cut local exploration, a cloud round cut before its reservation) counts 0, known, so an interruption that spent nothing, replayed as the operator notice prescribes, no longer makes the campaign `inconclusive` / `premium_total_unknown` with an empty `campaign_level_reasons`; a record with a cloud session and a null total stays unknown. Under v5 `arms.L.quality`/`economy` are `unavailable` and the four `economy_detail` fields `ratio`, `premium_pass`, `premium_per_accepted`, `reference_premium_per_accepted` are null also while the campaign is incomplete (the v2 to v4 readings were still printed then). Text (protocol section 2 and the config notes, same edits in the unpinned pilot config): the v2 to v4 rule was misdescribed in four places ('one undecided task made every verdict unavailable'): an undecided task made the ECONOMY unavailable, hence forbade 'retained', quality stayed read by bounds and compatibility did not depend on it, so `keep_cloud` stayed possible; both directions of the v5 change are written (a quality failure in the best case or a compatibility failure with |D| < 9 gave `keep_cloud` and gives `inconclusive`). Points 3 and 6 say what the code does: when a record's premium is unknown, when `premium_total_unknown` comes with a campaign-level reason and when it does not (a premium the record lacks but the ledger knows, after a cut that followed the settlement: a stated limit, not lifted), what (d) really applies once a cloud replay exists (lenient, no spend at stake; shared `report` code not changed), the precedence of the compatibility readings, and the remaining asymmetry of a cloud execution really cut (tokens unknown to the ledger stop every later cloud execution, `premium_tokens_unmeasurable`: its round is never replayed). True chronology of the threshold 9 (fixed after pilots 1 to 3 and review round 1, before pilot 4; adjusted after rounds 2 and 3; no campaign trial at any point). The maintainer's explicit agreement on the rule change is still to be recorded on the issue before the merge. Operator notice and script: the STOP message names the launcher's exit codes (2 also a tool error, 3 also `premium_tokens_unmeasurable`, 130/143/129/1 an interruption); new 'after a stop' checklist (read `ledger.unknown_spent_work` and `paired_rule.campaign_level_reasons` before relaunching; a tool error or an interruption is replayed once, at round 0 before a verdict only). `pat-19-golden-check-v5.json` (committed at `52ef9d1` from the reopened freeze `e6f90c6`) is removed from the tree again: the coordinator regenerates it from the new freeze commit and commits it after it (no test depends on it). Pilot evidence reports are NOT regenerated. Tests in `test_local_first_exploration_v5.py`. R5: record field `audit.temp_leftovers` (new sub-fields), constants `TEMP_WALK_BOUND` and `TEMP_UNWATCHED`, the report behaviour above, documented in the protocol (sections 2, 6.2, 7.4, 8), the launcher doc and the operator notice; the FOUNDRY-123 detector is not shipped, status asserted here.
- (Superseded: this third freeze was REOPENED by the review round 3 and redone, see the entry above; its sha256 and its statement on `pat-19-golden-check-v5.json` are those of that freeze) Fixed and re-frozen (PAT-126 review round 2, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; no new pilot; v1 to v4 and their report output untouched): the second freeze (`56634fe`, committed on the branch, never merged) is reopened and redone; `pat-19-campaign-v5.json` sha256 `9fe6da84c3739302cdaede9a71ab6713bb5c2b27d0b78994c9fdc2a00d5fb366` (hash test). No value of the rule changed (0.85, threshold 9, feedback, bounds). Paired rule (`_apply_paired_rule`, protocol section 2 rewritten to say exactly what the code does): an UNAVAILABLE compatibility (unmeasured swap, a local exploration lost without a successful replay) now gives `inconclusive` (`compatibility_unavailable`), only a FAILED one gives `keep_cloud` (it was read as a failure); an interruption or a cap on one task makes it an undecided task that leaves D, also when a cloud execution of it was cut with unknown tokens (printed `null`), instead of making the whole campaign `inconclusive`; the campaign-level reasons (a cloud execution never settled or named by no result record, an interrupted or unknown-token execution of a task of D or of no compared task, a start with neither record nor replay) are named and applied inside the rule and give `inconclusive` whatever D says; `decision` and `campaign_conclusion` are the authoritative keys and `paired_rule.verdict` always equals `decision` (it could say `retained` under an `inconclusive` decision), the verdict on D before those reasons being kept in `verdict_before_campaign_level` with `reason_before_campaign_level`, `campaign_level_reasons` and `compatibility`; one cost ratio under v5 (`paired_rule.ratio`; `ratio`, `premium_pass`, `premium_per_accepted` of `arms.L.economy_detail` are `null` with `superseded_by`); `paired_rule.undecided` lists exactly the tasks outside D; a decided `keep_cloud` has no robustness reading, and why. Shared temp directory: `_move_temp_leftovers` moves only top-level NAMES absent before the execution (a pre-existing entry modified during it was moved, then discarded with the attempt) into the launcher's quarantine `<state>/temp-quarantine/<campaign>-<session>/` (`TEMP_QUARANTINE`), under the state directory the sandbox denies to the arms, and nothing is ever deleted; stated limit: an entry created in the window by another process of the same account and matching the signature would be quarantined too. Config: the protocol coordinates `quality_bounds_on_undecided` and `economy_unavailable_cases` described the v2 to v4 rule and are rewritten for v5; notes aligned (same edits in the unpinned pilot config). Text: the new handling of undecided tasks is stated as a CHANGE of the decision rule (relative to v2 to v4 and to the 'rule unchanged' wording of the issue), decided by the coordinator under the mandate of 2026-10-08 and reported to the maintainer, whose explicit agreement is to be recorded on the issue before the merge; the threshold 9 of 12 is three quarters of the corpus, chosen by judgement with no statistical derivation; pilot 1 is void (operator error), not a failed criterion; both earlier freezes were committed on the branch (the text said the first was not); temp-directory uses aligned on the raw streams (calls naming `$TMPDIR`: 6 of 6 host-refused at pilots 2 and 3, 4 of 4 at pilot 4, none ran); host-refused calls 2, 7, 4 and 5; the judge's pristine-bundle fallback (`candidate_breaks_test_loading`, REFUSED 0/0/0) and its limit (a transient judge failure on the candidate becomes REFUSED; the run report re-reads each one) written where 'never REFUSED 0/0/0' stood alone; the cross-task channel 27 -> 24; what changed after pilot 4 and was exercised by no pilot (the frozen-protocols tuple, the `report` code above, which does not run during a campaign execution, and the temp-leftover move policy, which does). `pat-19-golden-check-v5.json` (committed at `4f95b86` from the reopened freeze `56634fe`) is removed from the tree again: the coordinator regenerates it from the new freeze commit and commits it after it (no test depends on it). Pilot evidence reports are NOT regenerated with this tooling (stated in the evidence note). Tests in `test_local_first_exploration_v5.py`. R5: report fields `paired_rule.verdict_before_campaign_level`/`reason_before_campaign_level`/`campaign_level_reasons`/`compatibility`, `economy_detail.superseded_by`, state directory `temp-quarantine/`, constant `TEMP_QUARANTINE`, documented in the protocol (sections 2, 6.2, 8), the launcher doc and the operator notice; the FOUNDRY-123 detector is not shipped, status asserted here.
- (Superseded: this second freeze was REOPENED by the review round 2 and redone, see the entry above; its sha256 and its statement on `pat-19-golden-check-v5.json` are those of that freeze) Frozen (PAT-126, after pilot 4; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; v1 to v4 untouched): the PAT-19 protocol v5 and `pat-19-campaign-v5.json` are FROZEN on 2026-10-08 (machine date), before any trial of the v5 campaign `pat-19-x5compare-1` (sha256 `b8bc2733a67959c3ccbb33756761ac6c15e9f3bb227c558b5de8cbb093651e58` pinned by the hash test, which now covers the configs v1 to v5; the pilot config is outside the campaign and not pinned); `FROZEN_PROTOCOLS` gains `pat-19-protocol-v5` (not its pilot). Pilot 4 (`pat-19-x5pilot-4`, tooling `361f719`, clean shell) PASSED the stop criteria (a) to (f) on PR 27: 1 595 725 premium tokens (A 620 855, L 974 870), A and L accepted, barrier observed, no contaminated record, 2 journal entries (`tool_result:~/.config/foundry/config.env`, echoes of bundle tests failing under the sandbox), 5 host-refused calls, no temp leftover, registry unchanged; `paired_rule` `inconclusive` (|D| < 9) as expected on one task. Its first attempt was refused by the operator script before any model load: three full repository copies (`tmp.*`) left by v3/v4 reviewers on 2026-10-07 sat in the per-user temp directory during pilots 1 to 3 and T2 to T4 (no raw stream names them: a text search, not a proof of non-reading); they were moved to a quarantine outside the repository. Protocol section 7 records the four pilots (1 void, 2 and 3 stop criterion (c), 4 passed), every instrument change, non-decisional readings of PR 27 across the pilots (direction differs; one seen task proves nothing), and what remains true at the freeze (one passing pilot on one task; 11 tasks never played under the final instrument; the run report reads every decisive finding on the transcript). Limits added: the sandbox-test echo in the journal and the earlier campaigns' temp leftovers. Evidence without raw transcripts: `pat-19-runs/x5pilot-4/` (report as produced with `361f719`, replay of the audit with the final policy), notes in `pat-19-v5-pilots-evidence.md`. `pat-19-golden-check-v5.json` is removed from the tree: it is produced from the frozen commit and committed after it (no test depends on it). Operator notice and script: campaign envelope `pat-19-x5compare-1` (150 executions, 75 000 000 tokens, 100 000 s), clean shell, temp-directory check, golden check and interpreter preflight as prerequisites. R5: keys `rules.exploration_comparison.paired_decided_min`, `isolation.audit_policy`, `exploration.comparison_tasks`/`comparison_task_set`, record field `audit.temp_leftovers` (and `audit.policy`/`journal`/`journal_by_role`/`refused_calls`), report keys `paired_rule`, `temp_leftovers`, `audit_journal`, verbs `golden-check` and `replay-audit`; the FOUNDRY-123 detector is not shipped, status asserted here.
- Reopened and fixed (PAT-126 review round 1, 2026-10-08; fake arms and offline tests only, no `claude`/cloud/model/`lms`; v1 to v4 untouched): the freeze is REOPENED, protocol v5 and `pat-19-campaign-v5.json` are back to DRAFT (removed from `FROZEN_PROTOCOLS` and from the sha256 pin) until a fourth pilot `pat-19-x5pilot-4` passes the stop criteria (a) to (f). Text: three false or contradictory sentences fixed (a `mktemp` `cd` is journaled under the policy, not `review_unreadable`, and `mktemp -d` lands in the per-user temp directory, the commonest case of limit 3.6 (b); 7 host-refused calls at pilot 2, not 9; draft leftovers removed, the git warning is stated as observed 0 times), plus: pilot 1's flag disappears once the host-refused call is no longer counted (not an ordinary-gesture over-flag), the section-3.6 class and cross-reference, 'refusé ou contaminé compte 0' bounded to the screening, new/inherited values told apart ('Qui a fixé quoi'), PR 27 being the task of the pilots, T2 to T4 and one of the 12, stale notes in the configs. Code: `_refused_call` counts the permission-rule shapes only for file tools and only as the whole error (a Bash command that printed such a sentence was not refused); `os_denied` is path-boundary safe (a truncated prefix must start with the denied root followed by `/`); the strict judge tells a candidate that broke the loading of the tests (REFUSED `candidate_breaks_test_loading`, a pristine bundle of the task does report) from a dead instrument (error); `golden-check` output carries its provenance (config, manifest, snapshot sha256, tooling commit and dirty flag, date). New v5 decision rule on undecided tasks (`rules.exploration_comparison.paired_decided_min` 9, pinned): paired decided set D, `inconclusive` under 9 of 12, acceptance and premium per accepted task on D, L retained iff accepted_L >= accepted_A, both >= 1, ratio <= 0.85 and compatibility, worst-case robustness reading, otherwise keep the cloud; `report` prints `exploration_comparison.paired_rule`; the v2 to v4 report is unchanged. Shared per-user temp directory: under the native-sandbox key each cloud execution's new top-level temp entries (user-owned, no symbolic link, not `foundry-*` or `pytest-of-*`) that hold a copy of bundle material are MOVED into the attempt directory and recorded `audit.temp_leftovers` (report `temp_leftovers`), deciding nothing; the operator script refuses a `plugins/foundry` directory at the top level of `TMPDIR`; limits written (order A then L, 6 of 6 explicit uses host-refused at pilots 2 and 3). Pilot evidence reports, replays and the golden check regenerated with this tooling (replay: no decisive finding on any reviewer, unchanged). Tests in `test_local_first_exploration_v5.py`. R5: new keys/fields/report keys listed in the protocol, section 8.
- (Superseded: this first freeze was REOPENED by the review round 1 and redone after pilot 4, see the entries above) Frozen (PAT-126 phase 2; fake arms and offline tests only, no `claude`/cloud/model/`lms` run by the diff; v1 to v4 untouched): PAT-19 protocol v5 and `pat-19-campaign-v5.json` FROZEN on 2026-10-08 (machine date), before any trial of the v5 campaign `pat-19-x5compare-1`; `FROZEN_PROTOCOLS` gains `pat-19-protocol-v5` (not its pilot); the hash test pins the configs v1 to v5 (the pilot config `pat-19-campaign-v5-pilot.json` is outside the campaign and not pinned). Last instrument fix (pilot 3, criterion (c) a second time: a PASS lost to `tool_result:<work-root>/priv`, a path echoed and truncated by the reviewer's own `sed`): under `isolation.audit_policy` and an observed barrier, a `tool_result:` finding under a root the OS denies to the shell (or a truncated prefix starting with one) is journaled; outside those roots it stays decisive. Protocol section 7 now records the three pilots (all 2026-10-08: pilot 1 void, operator PATH, 758 999 tokens; pilot 2 criterion (c), two PASS discarded, 1 825 779; pilot 3 criterion (c), one PASS discarded, 1 657 127), every instrument change they caused (interpreter preflight, judge instrument error, `golden-check`, audit policy in three parts), the statement that NO fourth pilot ran after the last fix (effect shown only by offline replay of the three pilots' streams and by tests: no decisive finding on any reviewer; a stated risk of the campaign), non-decisional readings of PR 27, and the final limits (shared temp directory copies, about 150 of ~2150 bundle tests failing under the sandbox with PermissionError on the home (coordinator's figure), host-refused calls per session, the `tool_result` rule). Evidence committed without raw transcripts: `pat-19-runs/x5pilot-1/` to `-3/` (reports regenerated with the freeze tooling from the config each pilot really used, replay-audit with the final policy), `pat-19-golden-check-v5.json` (12/12), notes in `pat-19-v5-pilots-evidence.md`. Operator notice and script: campaign command and envelope for `pat-19-x5compare-1`, clean shell, golden check and interpreter preflight as prerequisites; launcher doc section no longer a draft. R5: keys `exploration.comparison_tasks`/`comparison_task_set`, `isolation.audit_policy`, `binary_version` on cloud drivers, verbs `golden-check` and `replay-audit` (policy), record fields `audit.policy`/`journal`/`journal_by_role`/`refused_calls`, report key `audit_journal`, ledger entry `interpreters`, `FROZEN_PROTOCOLS`; the FOUNDRY-123 detector is not shipped, status asserted here. Mandate date corrected everywhere: 2026-10-08.
- Added (PAT-126, after pilot 2 `pat-19-x5pilot-2`: instrument not usable, stop criterion (c): the two reviewers that carried the only two PASS verdicts were flagged `/<unknown-working-directory>` (one by a command `dontAsk` refused and that never ran; one by a `cd` the audit did not believe after a failing test run, so `../../../scratch` resolved to the work root) and became `review_unreadable`; 1 825 779 premium tokens; fake arms and offline tests only, no `claude`/cloud/model/`lms`; v1 to v4 and every config without the key unchanged): new key `isolation.audit_policy` = `journal_under_observed_barrier` (after v4 only, needs `cloud_native_sandbox` and `audit_revision` 2, pinned for v5 and its pilot, in both DRAFT configs). For a stream whose barrier is `settings_transmitted_version_observed` only: a tool call the host refused as a whole (known shapes: dontAsk, permission rule, reads outside the working directories; an error result) did not run, counts for nothing and moves no directory (`audit.refused_calls`, per tool, no text); a SHELL finding under a root the OS denies to that execution (the `denyRead` list passed minus `allowRead`) or at the unknown working directory goes to `audit.journal`; every other finding (file tool, path the OS does not deny, tool-result path, forbidden command) stays decisive; the reviewer rule is unchanged in form. `contamination` keeps its meaning; `audit` gains `policy`, `journal`, `journal_by_role`, `refused_calls`; `report` gains `audit_journal`; `replay-audit` replays the policy per stream (non-decisional outcome reading). Replay on pilot 2: A 2 reviewer journal-only (5 paths), L 1 reviewer cleared by the refused call; nothing else changes. Protocol 3.6 and 7.3 ter (pilot 2 recorded; pilot 3 will run as `pat-19-x5pilot-3`), launcher doc, operator notice; limits stated (OS enforcement observed by P3/P7/P16 only, shared temp directory and other OS-readable places not covered). Tests in `test_local_first_exploration_v5.py`.
- Added (PAT-126, after the VOID pilot `pat-19-x5pilot-1`: the operator's shell had a Homebrew `python3` without pytest first on PATH, so the judge returned `REFUSED` 0/0/0 and the arms could not run tests; 758 999 premium tokens spent, no reviewer ran; fake arms and offline tests only, no `claude`/cloud/model/`lms`; v1 to v4 behaviour and frozen files untouched): (1) `local_first_corpus.judge(strict_report=True)`, used only under a protocol after v4, raises `JudgeInstrumentError` when pytest writes no junit report (or exits 4/5 with zero tests) instead of a `REFUSED` 0/0/0 verdict: the attempt is a `tool_error` (undecided, no corrector feedback); v1 to v4 keep their historical 0/0/0 `REFUSED` (tested). (2) v5+ preflight `probe_interpreters` before any claim: the launcher's interpreter and the `python3`/`python` on the cloud arm's PATH must import pytest, else exit 2 naming which; resolved paths (home masked) and pytest versions in the ledger's `preflight` entry (`interpreters`). (3) New verb `golden-check` (offline: untouched bundle must be REFUSED with failing tests, merged change must be ACCEPTED, per task of the config's list; JSON, never overwritten). (4) `pat-19-v5-operator.sh` refuses (65, before any model) a `python3` without pytest and prints it; notice says to launch from a clean shell. (5) Draft protocol 7.3 bis records pilot 1 as void and pilot 2 as `pat-19-x5pilot-2` (no new config: the campaign id is read from the envelope). Documented in `pat-19-launcher-v1.md` and the protocol (R5); FOUNDRY-123 detector not shipped.
- Added (PAT-126, phase 1; fake arms and offline tests only, no `claude`, no cloud arm, no model, no `lms`, no campaign mode run by the diff; frozen v1 to v4 protocols, configs and results untouched, sha256 of the four configs checked by the tests; v1 to v4 launcher behaviour, argv, environment, bundles and record shapes unchanged): launcher support for the PAT-19 protocol v5 and a DRAFT protocol, DRAFT campaign and pilot configs and operator material; NOT the freeze (the real pilot of the instrument comes first, then the fixes and the freeze, phase 2). Loader: `pat-19-protocol-v5` and `pat-19-protocol-v5-pilot` (allow-list `pat-19-protocol-vN`, N >= 5, plus the pilot) are PINNED (fixed candidate without screening, bounds 60 steps / 900 s and 2 corrections, `one_task_per_launch`, `correction_feedback` 20/300/200, `isolation.private_attempt_root`, `isolation.cloud_native_sandbox` and `isolation.audit_revision` 2, the task list and label, rule tasks 12 or 1 and ratio 0.85, `binary_version` `claude --version` 2.1.285 on every cloud driver, no cloud explorer, `isolation.allow_read_home` within `[".config/git/ignore"]`); the v4 keys `correction_feedback`, `exploration.fixed_candidate`, `exploration.comparison_task_group` are now also accepted under a protocol after v4 (still refused under v1 to v3 with the same message). New config keys: `exploration.comparison_tasks` (explicit ordered list of corpus PRs, played in that order, each checked against the manifest and the snapshot before any claim; exclusive with `comparison_task_group`) and `exploration.comparison_task_set` (label of `task.set` in every comparison record, ledger entry and report filter; default `comparison`), both only after v4. The pilot (`pat-19-protocol-v5-pilot`, campaign id containing `pilot`) and the campaign cannot share a campaign id (refused at `Runner` construction); arms are A and L only under v5 as under v4. Claude Code version pin: a cloud driver's `binary_version` is checked by `check_binary_version` at the start of `compare-exploration`, before any reservation, once per distinct command (nothing is asked without the key). Git warning under the native sandbox (`unable to access '~/.config/git/ignore'`, echo flagged in 1 reviewer of each of T2 to T4): the v5 configs set `isolation.allow_read_home` to that single file, which feeds the native settings' `sandbox.filesystem.allowRead` only (Read/Edit deny rules on the home, shell `denyRead` and `allowWrite` unchanged; the Read tool stays denied; no effect on the local profile where `.config` is denied last): the effect on `git` is EXPECTED, NOT VERIFIED (cannot run `claude` here), the pilot verifies it. Docs: `pat-19-protocol-v5.md` (French, DRAFT, not frozen; values fixed by the coordinator under the maintainer's mandate of 2026-10-08, none validated one by one; the pilot stop criteria (a) to (f) written before the pilot; known defects handled or written as accepted limits: shared per-user temp directory, reviewer `mktemp` copy, `dontAsk` behaviour, revision 2 over-flags; the PAT-124 carry-over list), `pat-19-campaign-v5.json` (12 tasks 26, 38, 25, 42, 33, 37, 30, 83, 27, 24, 48, 19; label `pat-19-v5`; envelope recommended 150 cloud executions / 75 000 000 premium tokens / 100 000 s, not enforced by the loader) and `pat-19-campaign-v5-pilot.json` (PR 27, label `pat-19-v5-pilot`, 16 / 8 000 000 / 30 000), `pat-19-v5-operator.sh` (modes `compare` and `pilot`, refuses before loading any model a campaign id, a state directory or a Claude Code version of the other mode) and `pat-19-v5-operator.md` (exact pilot command), section "Protocole v5 (PAT-126, brouillon)" of `pat-19-launcher-v1.md`. R5: the artifacts above are documented there; the FOUNDRY-123 detector is not shipped, status asserted here. Tests: `test_local_first_exploration_v5.py` (fake arms; stand-in `claude` and `lms` scripts; 12 tasks one per launch in order with a report including a `review_unreadable`); `test_local_first_audit_revision.py` and `test_local_first_native_sandbox.py` use `pat-19-protocol-v6` as their unpinned later protocol (they used `pat-19-protocol-v5`), and one assertion of the latter now says the v4 keys are accepted after v4.
- PAT-124 review round 3 (two more real LAUNCHER trials recorded, run by the coordinator on 2026-10-08 with the hardened code at `7dddce7`; the diff itself runs no `claude`, no cloud arm, no model; frozen v1 to v4 untouched). Four trials of 2026-10-08, never merged: **T1** manual (outside the launcher, `bypassPermissions`, hand-written settings, evidence not committed), **T2** launcher before the hardening (committed, unchanged), **T3** launcher, `--probe-test plugins/foundry/tests/test_benchmark_evidence.py` (result NOT committed; billing 65 933 + 50 520; P2 `unknown`: pytest answered "3 deselected" because that file is in the opt-in `benchmark_campaign` list of `tests/conftest.py` at that base — a wrong choice of file, not a sandbox effect), **T4** launcher, `--probe-test plugins/foundry/tests/test_process_contract.py` (committed as `docs/qualification/pat-19-native-sandbox-trial-2026-10-08-t4.json`, produced by `native-sandbox-trial-reeval`, never hand-edited; billing 35 593 + 50 661; Claude Code 2.1.285; `dontAsk` asked and seen; barrier `settings_transmitted_version_observed` for both records). Now OBSERVED with the launcher's settings (T3 and T4 unless said): a write outside the attempt refused BY THE OS (P16, a python program, `Operation not permitted`) — so an OS write refusal has been observed with the launcher's settings; the Write tool refused on the bundle's `.claude` by a permission rule (P14) and the shell by the `dontAsk` mode (P15, which therefore does NOT show the OS-level `denyWrite` on `.claude`); the settings read from each execution (`denyWrite` and the `Edit` rules present for both executions); one real test file executed and passing under the sandbox (T4 only: 6 tests of a small file that only reads repository files, not the bundle's suite). Reviewer in T3 and T4: ran, verdict read, no call refused, no `mktemp` copy. STILL not established: a long real task, the bundle's test suite, any version other than 2.1.285, Glob/Grep, the Edit rule, a work root under the home, hot reload of settings, settings above the working directory, the shared per-user temp directory, the reviewer's `mktemp` copy (not used in T2 to T4), the OS-level `denyWrite` on `.claude` for a shell command. Recurring over-flag for the v5 ticket, seen again: the `git` warning on `~/.config/git/ignore` echoed in a tool result is flagged as a home path (reviewer: 1 flag in each of T2, T3, T4). Trial tool: an absent tool the arm did not even attempt is `tool_not_available` when the driver's tool list (`allowed_tools`; offline, the one list of the stream's `init` event) does not hold it, with its own note, instead of `unknown` (T4: Glob, Grep); P2 is `allowed` only on "N passed" with no "N failed"/"N error(s)" in the result (the probe pipes pytest into `tail`, so the exit status is lost); the not-established list adds the bundle's suite when P2 ran one file and the OS-level `denyWrite` on `.claude` when P15 was refused by `dontAsk`; the offline re-evaluation finds the run's nonce from any probe name (T4's arm named no `TOKEN-`), and the two trial verbs report a refusal instead of a traceback when run with `python -m`. Wording: the reviewer's `mktemp` copy "should work" in the per-user temp directory (deduced, never exercised). R5: `pat-19-launcher-v1.md` ("Quatre essais du 2026-10-08", "Essai réel du 2026-10-08", "Réglages de projet de l'arme et durcissement", "Limites supplémentaires", "Révision 2 de l'audit"); artefacts: `declared_tools` parameter of `observe`, note `tool_not_attempted`, second committed result. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- PAT-124 review round 2 (docs, traces and refusals under the key only, trial tool; fake arms and offline tests only, no `claude`, no cloud arm, no model run by the diff; frozen v1 to v4 untouched, no verdict recomputed). Two trials of 2026-10-08 are now named everywhere and never merged: **T1**, the coordinator's MANUAL trial (outside the launcher, `bypassPermissions`, hand-written settings, `claude-haiku-4-5`, throwaway directory; evidence NOT committed, not the launcher's settings; it alone saw the OS refuse a shell write into a sibling directory and the Write tool create a file there) and **T2**, the LAUNCHER trial (verb `native-sandbox-trial`, PR 27, 2 cloud executions, `dontAsk`; the committed JSON; P9, the shell write outside the attempt, was refused by the `dontAsk` mode, not by the OS). An OS-level write refusal had not been observed with the launcher's settings at that point (since observed by P16 in T3 and T4: round 3 above). Written limit: under the key EVERY bundle the launcher builds (local harness and local explorer included, not only cloud ones) has no `.claude` directory nor `.claude` symbolic link, so a task whose legitimate patch or hidden tests touch `.claude/` would be unsolvable or judged on another tree than in v4 (v5/v4 comparability cost); established offline for the 12 tasks of corpus v1: zero path under `.claude/` in the base tree, the merged patch and the protected tests of every task (table in the doc). Under the key: the record of a correction round carries `correction_excluded` (paths dropped from the patch handed to the corrector; same shape as `review_excluded`; absent when empty and always absent without the key); a symbolic link named `.claude` is removed from a bundle (it survived `rmtree`), also after a patch is applied, and the bundle is refused if it cannot be removed; the loader also refuses a cloud driver argv carrying `--add-dir`, `--allowedTools`, `--allowed-tools`, `--mcp-config`, `--plugin-dir` (bare or `=value`; `NATIVE_REFUSED_FLAGS`) or more than one separated `--permission-mode <x>` pair (`--setting-sources`, `--strict-mcp-config`, `--disallowedTools` stay accepted; the list is not shown exhaustive); the in-memory key `_native_trial` is refused in any config file. Trial tool: the result reports the settings each execution received (taken from the execution, `settings_source`, `reviewer_settings_passed_paths_masked`), not a rebuild that lacked `denyWrite` and the `Edit` rule on the bundle's `.claude`; probe P2 now really runs one test file (`--probe-test`, required; `allowed` only on `N passed`) — in T2 it was `pytest --collect-only`, relabelled as a collection in the tool, the JSON and the doc, and "no test actually executed under the sandbox" joins the not-established list; that list is now computed from what a trial played and saw and adds P16 not played, hot reload of settings, settings above the working directory, the shared per-user temp directory and the reviewer's `mktemp` copy not exercised (`reviewer.mktemp_used`). Committed result regenerated by `native-sandbox-trial-reeval` (never hand-edited); it states that its settings predate the round-1 hardening. Doc wording: the file tools "rely on" permissions (no "confined"); shell reads are refused by the OS only on the home, the work root and the launcher's deny list; P7 is an OS refusal, P8 a permission rule. R5: `pat-19-launcher-v1.md` (sections "Exposition des bras cloud", "Révision 2 de l'audit", "Bac à sable natif des bras cloud", "Essai réel du 2026-10-08", "Réglages de projet de l'arme et durcissement", "Limites supplémentaires"); artefacts: verb option `--probe-test`, record field `correction_excluded`, execution key `native_settings`, constants `NATIVE_REFUSED_FLAGS` and `NATIVE_TRIAL_KEY`, result fields `settings_source`, `reviewer_settings_passed_paths_masked`, `reviewer.mktemp_used`. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- PAT-124 review round 1 (docs, hardening under the key only, tool): project settings of the arm (docs: `--settings` beats project/local per key but arrays MERGE across scopes; the sandbox protects `.claude` settings of the working directory against shell commands, no exemption; drivers keep `--setting-sources project,local`, dropping it is not shown safe so it is unchanged and stated): under the key every cloud bundle is built with no `.claude` directory (tree, index, base commit; patches of correctors stripped), the bundle's `.claude` is in `sandbox.filesystem.denyWrite` and an `Edit` deny rule, deny rules on sensitive FILE entries are emitted bare and with `/**`, and the loader refuses a cloud driver argv carrying `--permission-mode=`, `--settings`, `--dangerously-skip-permissions` or `--allow-dangerously-skip-permissions`. Trial tool: only known refusal shapes count (layer `refused_by`: `os_sandbox`, `dontAsk_mode`, `permission_rule`), `judged_on` per write probe, probes P14 to P16 (project settings by Write and shell; an OS-level shell write) added and not played, a state-directory marker keeps a trial and a campaign apart, committed result regenerated offline. Limits written, not fixed: work root under the home not played, OS-refused shell write never observed, shared per-user temp directory channel (v5 decision, R6 lists TMPDIR), open reads (`/Volumes`, `/private/tmp`, `/Users`), comparability cost of `dontAsk`, permission mode checked only if `init` names it. R5: `pat-19-launcher-v1.md` sections "Essai réel du 2026-10-08" and "Réglages de projet de l'arme et durcissement"; artefacts: `protect` parameter of `native_sandbox_settings`, `_strip_claude_dirs`, `NATIVE_TRIAL_MARKER`, probes P14 to P16, result field `refused_by`/`judged_on`/`masked_rules_note`. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Added/fixed (PAT-124; fake arms and offline tests only, no `claude`, no cloud arm, no model, no `lms` run by the diff; frozen v1 to v4 protocols, configs and results untouched): native Bash sandbox for the cloud drivers of the PAT-19 launcher, enabled by the new config key `isolation.cloud_native_sandbox` (boolean; allow-list like `audit_revision`: accepted only under `pat-19-protocol-vN`, N >= 5; refused under v1 to v4, a missing or unknown protocol; without it every driver argv and environment is byte-for-byte unchanged, tested against the v4 config). With it each cloud execution gets `--permission-mode dontAsk` (replacing `bypassPermissions`) and `--settings <json>` appended, with ABSOLUTE paths computed per execution (`"."` does not designate the working directory there): `sandbox.enabled`, `allowUnsandboxedCommands: false`, `failIfUnavailable: true`, `autoAllowBashIfSandboxed`, shell reads denied on the home and the work root and re-allowed on the attempt directory (plus `isolation.allow_read_home`), `permissions.blockReadsOutsideWorkingDirectories`, the attempt directory as an additional working directory, `Read`/`Edit` allow rules on it and `Read`/`Edit` deny rules on the sensitive paths that do not contain it; the child environment gets `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1` ADDED (only under the key; the AGENTS.md R6 allow-list and `FOUNDRY_DATA` are intact; the `claude` process itself is never wrapped). The file-tool hole seen by T1 only, the coordinator's MANUAL trial of 2026-10-08 (outside the launcher, `bypassPermissions`, hand-written settings, evidence not committed, not the launcher's settings: Write created a file in a sibling directory), is addressed by the `dontAsk` mode plus those rules (sources: Claude Code docs, permissions, permission-modes and sandboxing pages); T2, the LAUNCHER trial of 2026-10-08 (the committed result), settled Read, Write and shell outside the attempt (refused; the shell and Write WRITES by the `dontAsk` mode, not by the OS) and `dontAsk` with `autoAllowBashIfSandboxed` (shell works: test collection and `git`, no test executed); it did NOT settle Glob/Grep (absent from the driver, documented best-effort), the Edit rule (not exercised), a shell write refused by the OS rather than by `dontAsk`, a work root under the home, or the Write/shell channels on the bundle's `.claude` (probes P14 to P16 added, not played). The reviewer keeps its instructions; its file tools work in its attempt directory, its shell should also work in the per-user temp directory (the `mktemp` copy: deduced from the documentation, never exercised, no reviewer of T2 to T4 made one). Loader coupling: `audit_revision: 2` is accepted only with the key; `isolation.private_attempt_root` is now accepted under v5 and later (the other v4 keys stay v4-only); every cloud driver must declare `sandbox: false` and a `claude-stream-json` stream. `audit.barrier` (record that ran to completion under revision 2) now says what the launcher verified: `not_verified` < `settings_transmitted` < `settings_transmitted_version_observed` (one Claude Code version in the `init` event and a final `result` that is not an error; a permission mode other than the one asked for gives `not_verified`; weakest of the streams of a record); never "confined"; a record cut by an error carries no `audit`. PAT-123 round-4 remarks: N1 comment, doc and this changelog now say "a record that ran to completion"; N2 L5 and L6 are inherited blind spots of ordinary work (`git -C`, `make -C`, a script the arm wrote, a path built at run time), not deliberate evasion, and the `-C` target is still flagged (tested); N3 known over-flags of ordinary work for the v5 ticket (`source .venv/bin/activate`, `break`/`continue`, `trap`, `cd "$(git rev-parse --show-toplevel)"`, a word `cd` in a commit message or heredoc) are listed and pinned by tests; `break`/`continue` are NOT made inert (ending a loop early is not modelled, not clearly sound in both shells). Launcher trial T2 run by the coordinator on 2026-10-08 (verb `native-sandbox-trial`, PR 27, Claude Code 2.1.285, 2 cloud executions, mode `dontAsk`; not to be confused with the manual trial T1 of the same day, whose evidence is not committed; result `docs/qualification/pat-19-native-sandbox-trial-2026-10-08.json` rebuilt offline by the new verb `native-sandbox-trial-reeval`; classifier fixed: the Read `blockReadsOutsideWorkingDirectories` message is a refusal, `tool_not_available` (Glob/Grep absent from the driver), `not_exercised` (Edit needs a prior Read); shell, Read and Write outside the attempt refused, bundle tests and git allowed, reviewer works; the single reviewer audit flag is the echo of git's `~/.config/git/ignore` warning, a recurring over-flag for v5). Trial tool: verb `native-sandbox-trial` (module `local_first_native_trial`; two real cloud executions through `Runner.cloud_execution`, so envelope, ledger, audit and settings are the campaign's; writes one result file with no raw transcript, session id or home path, unobserved items `unknown`). R5: documented in "Bac à sable natif des bras cloud (PAT-124)" of `pat-19-launcher-v1.md`; artefacts: key `isolation.cloud_native_sandbox`, `isolation.private_attempt_root` under v5+, constants `NATIVE_SANDBOX_KEY`, `NATIVE_PERMISSION_MODE`, `NATIVE_GIT_ENV`, `BARRIERS`, `BARRIER_SETTINGS`, `BARRIER_OBSERVED`, `native_sandbox_settings`, `with_native_sandbox`, `execute_driver(native_settings=)`, launcher verb `native-sandbox-trial`, record field `audit.barrier` values; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Fixed/added (PAT-123; fake arms and offline replay only, no model load, no `lms`, no `claude`, no cloud call; frozen v1 to v4 protocols, configs and results untouched, no verdict recomputed): PAT-19 contamination audit revision 2, enabled by the new config key `isolation.audit_revision` (1 default, 2; allow-list: accepted only under `pat-19-protocol-vN`, N >= 5, so v1 to v4, a missing or an unknown protocol are refused at load; without it every launcher behaviour and record shape of v1 to v4 is unchanged; a v5 that also wants `private_attempt_root` needs a loader change). Revision 2: (1) relative paths are resolved against a SET of candidate directories and flagged as soon as one candidate takes them out of the zone; the rule is one invariant, the candidates contain the real directory, claimed only under nine written assumptions (H1 to H9 in `local_first_runner.py` and the launcher doc: observed Claude Code 2.1.285 carry-over, what a result proves, default shell options, lexing); a `cd` REPLACES the candidates only inside an allow-list grammar (`_simple_script`: simple commands joined by `;`, newline, `&&`, `||`, `|`; no subshell, group, substitution, `&`, reserved word or function) when the result is clean and whole (status 0), nothing before it could end the script or move the shell out of sight, it starts its chain alone with one literal target and the result holds no `cd:` line; everywhere else it only ADDS candidates (`UNKNOWN_CWD` for an unreadable target, a builtin outside the inert allow-list, a built command name, `CDPATH`; a fixed point for loops and functions); a relative path is resolved against every directory from the command that names it to the end of the call (positional arguments of `sh -c`, variables, function arguments), and a command that may RUN in a directory outside the zone is a hit by itself (a bare name such as `cat x` is read there; `cd $X; cat y` is flagged, which revision 1 misses); a `cd` word not read as a command adds `UNKNOWN_CWD`; from one call to the next the end candidates are kept after a clean result and every directory of the call otherwise, the `Shell cwd was reset to ...` line deciding when present; a stream that does not name an observed host version (omp, another Claude Code version) carries nothing and believes no `cd` (stricter fallback, recorded as `audit.host_models: unverified`); a fresh `mktemp -d` directory is deliberately NOT modelled (`cd $T` stays `UNKNOWN_CWD`): written, tested, then removed, because the audit cannot see a symbolic link the arm puts in a directory whose real path it does not know; covered by named regression tests for the three under-flags of the second review (script stopped before its `cd`, `cd` run more than once, positional arguments) and a property test against a real `/bin/bash` and `zsh` under four encoded host behaviours; it over-flags ambiguous lines by design (a `cd` followed by a failing command or a `grep` without match, a loop, a `cd` word in a message, a reviewer working in a `mktemp -d` copy); (2) an omp tool call whose path got `Path not found: <that path>` read nothing: not a hit, recorded apart in `audit.not_found`; (3) a flag in the reviewer's session no longer marks the arm's attempt `contaminated`: it is recorded in `review.contamination`, the judge and review verdicts stay readable, and a flagged reviewer decides nothing whatever it said (`review_unreadable`, loop stopped, findings passed to no corrector), including on an attempt cut after the reviewer's audit (the flag stays on `review.contamination` of the cut record); `report` lists such records under `review_contaminated` (key present only when there is one); (4) `.pytest_cache/` and `.ruff_cache/` are excluded from the captured patch (only these two; `-f` kept). Origin of the `.pytest_cache/` in the v4 reviewer diffs established: the arm's own `pytest` run creates it, the repository `.gitignore` already ignores it, and the instrument's `git add -A -f` capture forced it into the patch (instrument defect, fixed for a future campaign only). New offline mode `python3 -m foundry.local_first_runner replay-audit` (`--work-root-not-sensitive` for the scope measurement): applies revisions 1 and 2 to the raw streams of a finished campaign and prints, per record, recorded / old / new classification, with no driver, model or cloud call, writing a new file and never rewriting a result. Replayed on the 36 v4 streams (`docs/qualification/pat-19-audit-replay-v4.json` and `-scope.json`, note `pat-19-audit-replay-v4.md`; raw streams stay off the repository, the result carries their sha256 and no home path): revision 1 reproduces the 8 recorded flags exactly; revision 2 keeps the 2 real excursions (`find /`, `find` of the work root), removes 4 of the 5 audit errors, turns the fifth (L PR 83 reviewer) into a reviewer flag for an unreadable `cd $T` of a `mktemp -d`, moves the mistyped explorer path apart (2 non-existent paths) and adds one reviewer flag (A PR 27, same cause); the other 23 records stay clean; the replay summary counts reviewer-flagged records apart (`reviewer_flagged_now`: 2) and each stream names its host model. Report output: `informative_arms` names only an informative arm that was played (it printed `["E"]` for the v4 campaign, which has no arm E; output-only, no decision reads it; reports already committed are not recomputed). PAT-122 review nits fixed in `pat-19-exploration-results-v4.md` (exact scope of the "trigger", now backed by the committed `-scope.json`, wrong R6 reference, "10 names" attributed to the diagnosis). Threat model (maintainer's decision, 2026-10-08), written at the top of the "audit revision 2" block and in the launcher doc: the lexical audit is a journal of the accidental excursions of an arm doing its job, not a barrier against an arm that tries to deceive it; the barrier is Claude Code's native Bash sandbox enforced by the operating system (PAT-124); the invariant is claimed only for an arm that is not trying to deceive the audit, under H1 to H9 (H4 no longer says that a program cannot move the shell: it can, by renaming, moving or replacing a directory or symbolic link of the shell's path). Known limits, deliberate evasion, out of scope and not fixed one by one (L1 to L6): a directory or symbolic link moved, renamed, replaced or removed under the shell; `CDPATH` or other `cd` state set through a built name or arithmetic evaluation; profile aliases, functions, hooks and shell options; commands built at run time; a `cd` hidden in a script or interpreter; a path built at run time. One-line hardening only: zsh `print -v` (like `printf -v`) and `integer`/`float` with an expanded argument taint the `cd` that follows; two tests hold today's behaviour for the two L1 forms as known limits, not guarantees. Revision 2 must be enabled only together with the native sandbox; no config key for it exists yet, so the launcher checks nothing and says so: every revision-2 attempt record that ran to completion carries `audit.barrier: "not_verified"` (constant `AUDIT_BARRIER`; a record cut by `_tool_error` carries no `audit`; PAT-124 then added the other two values), never to be read as "confined". The zsh half of the shell-oracle tests is an explicit `skip` where zsh is absent (the host shell is zsh; a CI image may only cover bash); a cut during a flagged implementer is tested under both revisions; the v4 keys and revision 2 cannot be loaded together today (to carry in the v5 ticket). The replay result is unchanged by this alignment (both JSON regenerated identical); acceptance gap stated for the maintainer: 4 of the 5 false flags disappear, the fifth returns as a reviewer flag and 1 reviewer flag is added. R5: documented in "Révision 2 de l'audit et rejeu hors ligne" of `pat-19-launcher-v1.md` and the replay note; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed (launcher CLI verb `replay-audit`, campaign key `isolation.audit_revision`, constants `AUDIT_REVISION`, `FROZEN_PROTOCOLS`, `UNKNOWN_CWD`, `OBSERVED_CLAUDE_CODE`, `AUDIT_BARRIER`, record field `audit.barrier`; operational constraint: revision 2 only with the native sandbox, PAT-124). Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Docs/evidence (PAT-122; docs and raw evidence only, no code change, no model load by the diff, no cloud call by the diff; frozen v1 to v4 protocols, configs and v1 to v3 results untouched): real PAT-19 v4 comparison `pat-19-x4compare-1` run on 2026-10-07 (code `main` at `4adeb2f`, arms A and L, candidate `qwen3.6-35b-a3b-mlx-4bit` fixed, the six tasks of the v3 screening: PR 30, 83, 27, 24, 48, 19; 6 launches, model reloaded before each task under the maintainer's block authorization, not confirmed one by one; Foundry state backed up off repo before the first trial; observed context 262144 despite `-c 65536`). The first launch (19:00) was refused by the preflight (`dedicated_machine_process_over_2gib:OrbStack:2887MiB`, no cloud execution); the maintainer then authorized quitting OrbStack and the campaign was relaunched at 19:56. Result: 30 cloud executions (cap 80), 7,004,823 premium billing tokens (A 4,134,697; L 2,870,126); accepted tasks A 1/6 (PR 27, review PASS), L 0/6; decision `inconclusive`, `campaign_conclusion` `keep_cloud_insufficient_evidence` (L: compatibility pass, quality and economy unavailable), so the cloud is kept by PAT-ADR-0015; no promotion, no local model or default activated, no billing gain announced. The only task decided in both arms is PR 19 (refused in both). The corrector feedback changed test counts in 7 of 12 comparable correction rounds (v3: never); cause not established (no no-feedback arm). 8 of 32 records are `contaminated`: an independent read-only diagnosis (replay of `audit_transcript`, not committed raw streams) reproduced the 8 path lists and finds 5 audit errors (relative tokens resolved against the bundle ignoring `cd`; working directory reset at every Bash call; contamination of the reviewer session marking the whole attempt) and 3 correct flags (a `find /`, a `find` of the work root, a mistyped local-explorer path); according to that diagnosis, the v4 change that made the work root sensitive is what turned two pre-existing audit defects into flags (protocol v4 section 5 residual risk materialised; not replayed on v3). The `find /` also sent 10 file names from the maintainer's home to a cloud session (no content read per the diagnosis): a confinement incident, not an audit defect. The frozen rule counts all 8; no verdict recomputed. Three judge-ACCEPTED/reviewer-BLOCK attempts; a committed `.pytest_cache/` in the diff, responsibility not established. The report prints `informative_arms: ["E"]` although v4 has no arm E (hardcoded in `local_first_runner.py`, report-output defect, not fixed here). Real Foundry registry reported unchanged by the coordinator (off-repo observation; no exit code 4). Report `docs/qualification/pat-19-exploration-results-v4.md`, raw files `docs/qualification/pat-19-runs/x4compare-1/` (the 36 raw streams are not committed; manifest by sha256). Fixing the audit is future work through `foundry:intake`. R5: report and raw pieces added, link in the "Protocole v4" section of `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v4 and config v4 untouched. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Added/fixed (PAT-121; fake arms only, no real model/harness/cloud call; v1, v2 and v3 protocols, configs, results and launcher behaviour untouched): PAT-19 protocol v4 frozen before any trial (`docs/qualification/pat-19-protocol-v4.md`, French) with two changes meant to remove two suspected defects of the comparison instrument (hypotheses of this protocol: the v3 results call the 7 contamination flags probable false positives, not arbitrated, and an instrument defect plausible, not established, only for the identical test counts of PR 26 and 38; nothing is claimed repaired) and replay the downstream measurement: two arms only (A, L; no Haiku arm), candidate fixed to `qwen3.6-35b-a3b-mlx-4bit` with no new screening (same 60 steps / 15 min bounds and reload before each task as v3), comparison on the six tasks of the v3 screening (PR 30, 83, 27, 24, 48, 19; already explored locally in the v3 screening, never played downstream; the exploration is re-run), decision rule unchanged (L retained iff acceptance(L) >= acceptance(A) and premium per accepted task <= 0.85 x A; absent data never zero; insufficient evidence keeps the cloud), cap of 80 cloud executions, stated limits (six tasks, tasks already seen by the explorer, no Haiku arm, nothing said of other task families). Values validated by the maintainer on 2026-10-07 except the corrector feedback (at most 20 failing hidden-test names cut to 200 characters, each message cut to 300 characters, same for both arms; never the test source code (the test module and name are visible, and a message can carry a path under the bundle), with the reserve that the pytest message can carry the assertion expression and the expected values, so a corrector can fit code to the tests, and that a collection error teaches nothing) and the 2 correction rounds, fixed by the coordinator and confirmed by the maintainer on 2026-10-07 before any trial (a different value means a v5). Stated limits also: the candidate and budget were chosen on the localisation score of these same six tasks (0.639), so L's report is probably favourable there; observed context 262144 despite `-c 65536`. Each correction record also carries counters of what the corrector was told (`feedback`: failing tests shown, total, collection failure; no text; key absent when the feedback is off). Under the private root the free text of an explorer report rendered into arm L's statement has the resolved spelling of the work root masked (explorer bundle path to bundle-relative path, else `<work-root>`; another spelling of the same directory, e.g. a symlink, is not masked), because the audit now treats the work root as sensitive and arm A has no report (v1 to v3 rendering unchanged); this masking was decided by the coordinator and reported to the maintainer without explicit validation (corrected wording, PAT-122); stated limit: the corrector and the reviewer each run in their own private root, so an absolute path of an earlier execution left in a patch or output would still be flagged (frequency unknown). Launcher, all four v4 keys off when absent and refused at load outside protocol v4; `compare-exploration` refuses any arm but A and L under v4 before any spend, and `report` of a fixed-candidate campaign says `no_screening`: `correction_feedback` (after a judge refusal the corrector is told the failing hidden tests; `lfc.judge(..., failures=True)` returns them from the junit report, name and message cut, bundle path and judge temp dir masked), `isolation.private_attempt_root` (each attempt in `<work-root>/private-<attempt>/<attempt>/`, so `ls ..` and `ls ../..` show no sibling and raise no contamination flag; the work root and every other attempt are sensitive roots, the rest of the audit is unchanged), `exploration.fixed_candidate` (no screening needed, `screen-exploration` refused, another candidate refused) and `exploration.comparison_task_group` (`screening`: manifest group the comparison tasks come from), new config `pat-19-campaign-v4.json` (loader pins its coordinates) and operator loop `pat-19-v4-operator.sh`/`.md` (the v3 script hardcodes the v3 config and arms A,L,E). PAT-120 review nits: dead `FOUNDRY_DATA` exemption removed from the leak-probe test; `FOUNDRY_DATA` imposed by driver kind (every cloud driver kind, not `home != isolated`) and `isolated_environment` drops any host `FOUNDRY_*` (the loader already refused them); "bras cloud" wording fixed in `pat-19-v3-operator.md` and the R5 line of `pat-19-launcher-v1.md`; the operator backup covers both registry locations (`~/.config/foundry` and `$FOUNDRY_DATA` when exported). R5: documented in the protocol v4, `pat-19-v4-operator.md`, the "Protocole v4" and "Audit de contamination" sections of `pat-19-launcher-v1.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed. Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Fixed/hardening with a declared side effect (PAT-120; fake arms only, no real model/harness/cloud call; frozen v1/v2/v3 protocols and configs untouched, campaign config sha unchanged; applies to every later launch of any protocol): a cloud arm can no longer write the machine's real Foundry registry through the repository's own code or tests (incident of 2026-10-07, PAT-117). The launcher sets `FOUNDRY_DATA` itself to a fresh empty `<attempt scratch>/foundry-data` for every CLOUD execution (implementer, corrections, reviewer, cloud explorer; local arms keep exactly their previous environment), after `env_set`, never from the config or the host; the AGENTS.md R6 variables are untouched. It also fingerprints (sha256 or absent) the REAL `registry.json` at both places (host `FOUNDRY_DATA` when exported, and `<HOME>/.config/foundry`) before and after each cloud execution: on a change the execution is settled in the ledger, the attempt record is `contaminated` (not accepted, never replayed, `billing_total` null) with the fingerprints (never the content), also for the review of a path C local attempt, a `stop` record with reason `foundry_state_changed` is written and the launcher exits 4; it never writes or restores the registry. Comparability limit: `FOUNDRY_DATA` enables the product's telemetry journal, so from PAT-120 the arms' own runs of the repository tests see it set (unlike v1-v3 campaigns); the effect on what they observe is unknown; the judge is unaffected. What the guard misses, the remaining limit (unconfined writes elsewhere in the real HOME, uninventoried test state) and the check that all 12 corpus bases read `FOUNDRY_DATA` first are stated in the launcher doc. Operator doc: back up the Foundry configuration directory before a campaign (`cp -Rp`) and run no registry-writing Foundry command meanwhile. R5: documented in "État Foundry d'un bras cloud" of `pat-19-launcher-v1.md` and `pat-19-v3-operator.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed (launcher internals: `registry_fingerprints`, stop reason, exit code 4). Detector FOUNDRY-123 not shipped: status asserted here, checked in review.
- Docs/evidence (PAT-117; docs and raw evidence only, no code change, no model load by the diff, no cloud call by the diff): real PAT-19 v3 campaigns run on 2026-10-07 (code `main` at `00eee40`, omp 18.6.1, LM Studio 0.4.25+1, model unloaded and reloaded before each one-task launch). Screening `pat-19-x3screen-1` (12 launches, no cloud): both candidates pass the frozen thresholds (qwen3.6-35b-a3b-mlx-4bit mean function recall 0.639, file precision 0.958, 0 refusal; qwen3-coder-30b-a3b-mlx-4bit 0.583 / 0.667, 2 refusals); retained = qwen3.6; v2 on the same six tasks gave 0.25, but budget, candidate set and reload regime changed together and the v3 screening is tuned on those tasks. Comparison `pat-19-x3compare-1` (6 launches, arms A/L/E on the 6 comparison tasks): 51 cloud executions (cap 120), 11,101,326 premium billing tokens; accepted tasks A 0/6, L 0/6, E 1/6; decision `inconclusive`, `campaign_conclusion` `keep_cloud_insufficient_evidence` (L: compatibility pass, quality and economy unavailable), so the cloud is kept by PAT-ADR-0015; no promotion. 7 contamination flags, all of one shape (a cloud arm named the launcher's work root); applied as frozen, a likely false positive that is NOT adjudicated, nothing reclassified. What the data support: reference arm A got no task accepted (3 refused by the hidden tests on all rounds, 3 never judged because of the contamination flags), so the comparison could not measure the downstream effect of exploration; on PR 25 the judge did discriminate (E accepted, A and L refused), and PR 26 and PR 38 have strictly identical test counts across all 9 attempts (a possible instrument defect, reading, not established). Observed model context: 262144 for qwen3.6 and 65536 for qwen3-coder (requested 65536), a fourth uncontrolled variable of the ranking and of the v2 to v3 comparison. Incident: a cloud arm (L, PR 42, 10:03:46) overwrote the maintainer's Foundry registry by running the repository tests (restored the same day; fix tracked in PAT-120); the records are intact but the influence on the attempts played afterwards is unknown; mechanical conclusion unchanged. Two of the seven flags come from the reviewer. Report `docs/qualification/pat-19-exploration-results-v3.md`, raw files `docs/qualification/pat-19-runs/x3screen-1/` and `x3compare-1/` (the 69 raw streams are not committed). R5: report and raw pieces added, link in the "Protocole v3" section of `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v3 and config v3 untouched.
- Added (PAT-116; fake arms only, no real model/harness/cloud call; v1 and v2 protocol, config and results untouched, v1/v2 launcher behaviour unchanged): PAT-19 protocol v3 frozen before any trial (`docs/qualification/pat-19-protocol-v3.md`, French; values and the v2 coordinator rules validated by the maintainer on 2026-10-07): the v2 read-only exploration with three changes that move together (a v2 to v3 screening difference is not attributable to the budget alone): the explorer budget (60 steps, 15 minutes, also for the cloud explorer of arm E), the candidate set (two fast MoE candidates chosen after the v2 results, `qwen3.6-35b-a3b-mlx-4bit` and `qwen3-coder-30b-a3b-mlx-4bit`, both required for a complete screening) and the model reloaded before each task as a rule written in advance; everything else inherited from v2 by reference (prompt, judge, ground truth sha256, thresholds, 0.85 x A rule, cap of 120). Stated limit: the v3 screening replays the same six screening tasks with a budget widened after seeing their results; the evidence that counts is the downstream comparison on the six never-played tasks. Launcher: new config `docs/qualification/pat-19-campaign-v3.json` (schema v2, `protocol` `pat-19-protocol-v3`, loader pins candidates, bounds and the flag) and the optional config key `exploration.one_task_per_launch` (screen-exploration and compare-exploration play at most one undecided task per launch, exit 0 and print `pat19-v3: work_remains=yes|no`; absent = v2 behaviour); operator reload loop `docs/qualification/pat-19-v3-operator.sh` (documented in `pat-19-v3-operator.md`) (the launcher still loads no model; the reload is attested by the operator script and one preflight per task). R5: documented in the protocol v3, the operator doc and the "Protocole v3" section of `pat-19-launcher-v1.md`; no `foundry_cli.py` verb, product configuration key, routing table or public routing constant changed.
- Docs/evidence (PAT-115; docs and raw evidence only, no code change, no cloud call): real PAT-19 v2 exploration screening run on 2026-10-07 (`pat-19-xscreen-1`, code `main` at `3af0932`, 5 candidates x 6 tasks, no cloud): no candidate reaches the frozen threshold (best mean function recall 0.25 < 0.5, no mean file precision >= 0.5), so by the frozen screening rule the campaign stops on "keep the cloud" for read-only exploration; comparison not run, no cloud quota spent. Of the 30 attempts, 23 were cut by a bound (15 time, 8 steps) and 7 answered within the bounds: 1 invalid report (missing keys), 2 empty (zero step) and 4 non-empty, all 4 naming the right file (function recall 1.0, 1.0, 1.0, 0.5); downstream effect not measured; promotion none. The last candidate's result is mixed with an operator intervention (two unload/reload cycles after dedicated-machine preflight refusals, a coordinator decision taken during the campaign by analogy with protocol section 11, maintainer validation requested; the two zero-step answers both follow a reload), see the "Dernier candidat et mémoire de la machine" section. Report `docs/qualification/pat-19-exploration-results-v2.md` and raw files `docs/qualification/pat-19-runs/xscreen-1/` (the 30 raw streams are not committed). The maintainer's validation of the coordinator-set rules of 2026-10-07 is requested in the report. R5: two artefacts added (report, raw pieces) and a link in `pat-19-launcher-v1.md`; no CLI verb, configuration key, public constant or routing table changed; frozen protocol v2 and config v2 untouched.
- Added (PAT-114; the diff's tests use fake arms only, the coordinator ran two real toy trials of the explorer drivers on
  2026-10-06; v1 protocol, campaign config and results untouched): PAT-19 protocol v2 frozen (`docs/qualification/pat-19-protocol-v2.md`, French, values validated by the
  maintainer on 2026-10-06): read-only exploration judged first on localization (local screening, no cloud: retained =
  highest mean FUNCTION recall among candidates with mean file precision >= 0.5, tie on shortest duration, STOP "keep
  the cloud" when the retained function recall is < 0.5, file recall measured but not deciding because each task has a
  single product file shared by 5 of 6 screening tasks; a contaminated or refused attempt counts as 0) then on its downstream effect (arms
  A / L / E on the 6 frozen comparison tasks, premium tokens per accepted task with the explorer included, L retained
  if compatibility, quality (acceptance >= A's) and economy (premium per accepted task <= 0.85 x A's) all pass; E vs A informative; three separate
  verdicts). Review round 3: a start-of-run preflight of `compare-exploration` (ledgered `phase: "start"`) followed by another preflight of the same launch is no longer a false `preflight_without_attempt` (resume after a refused preflight or a void exploration, `--paths L`), and the preflight is not run twice in a row; the screening report gains `campaign_conclusion` (`keep_cloud_insufficient_evidence` when it can no longer be completed under the resume rule) and `not_completable_tasks`; an L/E task with its exploration but no implementer record is undecided (`awaiting_implementer`, `incomplete_campaign`), not rejected; the four verdict-determining rules move to `exploration.protocol_coordinates`; the rules added on 2026-10-07 are dated as set by the coordinator before any corpus trial, their validation by the maintainer being requested in the campaign report; both explorers were re-run for real on the toy repository on 2026-10-07 with the frozen prompt (`final_prompt_trial`). Review round 2: a complete comparison with an `unavailable` verdict and no fail concludes `keep_cloud_insufficient_evidence` (`campaign_conclusion`); the system allow-list of the dedicated-machine check is narrowed to `/System/Library/` (consumer apps under `/System/Applications/` and the Cryptexes are ordinary consumers); the score-determining rules are protocol coordinates; a bound reached by the harness or a start error are handled (refusal / void); the dedicated preflight also runs at the very start of `compare-exploration` when arm L is requested; `main` now returns code 2 with a message (no trace) on a config load error, for v1 configs too. Review round 1 (rules validated 2026-10-06): file precision counts any file the merged diff changed (tests, docs, CHANGELOG
  included); only the first 10 functions of a report count and reach the implementer; dedicated machine free memory >= 35 % (replaces the swap-at-start threshold; the process rule is the real guard) and re-run right before each local exploration; a ground truth loaded from the committed file whose sha256 is in the config; an exploration cut by a bound is refused; the screening needs all five frozen candidates; the threshold is tested before the tie. The launcher gains the modes `screen-exploration` and `compare-exploration`, the type `local_explorer`
  (omp `--tools=read,grep,glob`, bundle read-only in the sandbox profile, 10 min / 25 steps) and `cloud_explorer`
  (Haiku 4.5), a deterministic localization judge (`foundry.local_first_exploration`, ground truth from the merged
  diffs, committed for the 12 tasks in `pat-19-exploration-truth-v2.json`), a dedicated-machine admission at preflight
  (another process over 2 GiB or under 35 % free memory refuses) and the new config `pat-19-campaign-v2.json`; the v1
  modes behave as before. The two explorer drivers were trial-run for real on 2026-10-06 on a toy repository (evidence `pat-19-preflight-v2-2026-10-06.json`) and are `verified: true`; the trial refused omp `--tools=read,grep,find,ls`, so the tool names were corrected to `read,grep,glob` before the freeze (loader accepts only these). The unsandboxed cloud explorer's read-only behaviour is observed, not enforced: the launcher checks the bundle's `git status` before and after each exploration and refuses one that changed it. R5:
  documented in `pat-19-protocol-v2.md` and the "Protocole v2" section of `pat-19-launcher-v1.md` (new CLI verbs
  `screen-exploration`, `compare-exploration`, `preflight --dedicated` of the campaign tool, config schema v2, driver
  types, result paths `XS`/`L`/`E`); no `foundry_cli.py` verb, product configuration key, routing table or public
  routing constant changed.
- Docs/decision (PAT-110; docs only, no code, no model load, no cloud call): PAT-19 v1 decision recorded in
  `docs/qualification/pat-19-decision-v1.md`. Autonomous ticket implementation by a local model: keep the cloud
  (v1 screening 0/30); no adoption, no local model, profile or default activated, no new ADR; v1 is not extended to
  12 tasks. Next usage to qualify: read-only exploration for a cloud implementer (local Lupin) under a protocol v2
  (PAT-114 freezes it, PAT-115 runs it). PAT-87 milestones PAT-88, PAT-89, PAT-90, PAT-91 postponed until the PAT-115
  verdict (none abandoned). PAT-19 stays open. R5: documented in the new decision file, linked from
  `pat-19-screening-results-v1.md` and `pat-19-launcher-v1.md`; no CLI verb, option, public constant or routing table
  changed, no protocol coordinate changed.
- Docs/evidence (PAT-109; the diff contains no code change and no cloud call; the run itself loaded five models): raw results and written report of the real PAT-19 v1
  screening run on 2026-10-06 (`pat-19-screen-1`, code `879e7b7`): 0 accepted of 30 (5 candidates x 6 tasks), so the
  protocol section 4 rule stops the campaign on "keep the cloud" for autonomous ticket implementation; no comparison
  run, no cloud spend. Files: `docs/qualification/pat-19-screening-results-v1.md` and
  `docs/qualification/pat-19-runs/screen-1/` (ledger, results, report, envelope, streams manifest; the 49 MB raw
  streams are not committed). R5: documented in `pat-19-screening-results-v1.md`, linked from `pat-19-launcher-v1.md`
  and `pat-19-protocol-v1.md` (no protocol coordinate changed); no CLI verb, option, public constant or routing table changed.
- Fixed (PAT-112; no model load): the launcher preflight read the LM Studio version from `lms version`, which prints
  only the CLI commit, so every real candidate was refused (`lm_studio_version_differs`). It now reads the app's
  `CFBundleShortVersionString` with the read-only `plutil -extract ... raw` on `/Applications/LM Studio.app/Contents/Info.plist`
  (`LM_STUDIO_VERSION_COMMAND`, in `READ_ONLY_COMMANDS`; `lms version` is no longer allowed); same fact name and
  `contains` comparison against the unchanged frozen 0.4.25; a missing plist refuses as `fact_unavailable:lm_studio_version`.
  R5: documented in `docs/qualification/pat-19-launcher-v1.md` "Préflight"; the public constant `READ_ONLY_COMMANDS`
  swapped `lms version` for the plutil tuple; no CLI verb changed.
- Fixed (PAT-112; fake arms only, no real model/harness/cloud call): the local contamination audit no longer
  hides a sandbox bypass through a system service (`launchctl`, `osascript` are forbidden executables, local and
  cloud; best effort: `crontab`, `at`, an out-of-sandbox `tmux`/`screen` server, `shortcuts run`, `automator` stay unlisted; the "blocked" exemption is decided on the path, not on an observed refusal) and treats the attempt directory
  (the bundle's parent, passed explicitly as `attempt_dir`) as allowed, so `ls ..` is no longer a contamination
  (`ls ../..` still is); a runner-level test proves `sandbox_denied` reaches a local driver's audit and never a
  cloud one. Docs/evidence: the generation_config digest is the only pinned sampling element (effective
  sampling values are unrecorded, limit stated), the 4-bit candidate's missing `loaded_instance_observed` and
  the trials' `pat19-smoke`/`--ttl 1800 -y` load are stated. R5: documented in
  `docs/qualification/pat-19-launcher-v1.md` "Statut documentaire" (`deny_home_trial`,
  `smoke.deny_home_evidence`, `sandbox_denied`, and the new driver key `binary_version`); no CLI verb changed, and the public constant `FORBIDDEN_EXECUTABLES` gained `launchctl` and `osascript`.
- Fixed (PAT-112): the omp version recorded as 18.4.10 was observed before the upgrade; the installed and used one is
  18.6.1 (installed 2026-10-05 17:20, every omp trial ran later), corrected in the evidence, campaign and docs.
  The launcher now runs `omp --version` from PATH and refuses a missing, unparsable or different version
  (driver `binary_version`, checked with the harness executable at the start of `screen`/`compare` and per attempt).
- Fixed (PAT-111, review round 4): a local arm's read that its deny-home sandbox refused (real home outside
  the allow list, or an explicitly denied path) is a blocked attempt, no longer a contamination
  (`audit_transcript(sandbox_denied=...)`); a signal between the bundle discard and the settle of a judged
  local attempt now waits for the record (the attempt is never replayed). Evidence: the four remaining
  candidates passed the toy tool-call trial under the final deny-home profile on 2026-10-05
  (`deny_home_trial` per candidate), plus one cloud control run (heredoc with `gh`/`~/.config` strings,
  audit clean); sampling parameters are declared unpinned. Cloud-only gaps (a call refused by a
  `cloud_bash_deny` rule still counts as contamination; interpreter heredoc bodies are not audited) are
  documented, to be handled before the comparison.
- Fixed (PAT-111, review round 3; fake arms only): the contamination audit no longer reads heredoc bodies given to
  `cat`/`tee`/`python3 -` (a body a shell runs, and the `$(…)` of an unquoted-delimiter body, stay audited),
  `#` comments or separators inside quotes as commands/paths, allows a cloud arm's own Claude Code session
  directory (saved tool outputs), ignores in tool results only the real-home paths the bundle's own files
  contain (`base_literals`, read at bundle build; PR 83's base), expands a local arm's `~` to its isolated HOME, and a cut after the audit keeps
  the contamination on the record (never replayed); the docs no longer claim a cloud arm cannot touch the state files.
- Fixed (PAT-111, review round 2; fake arms only, no real arm run): the contamination audit no longer
  treats text as an access: only path arguments (`path`, `file_path`…, the `pattern` of a Glob/find tool) and
  shell commands are read, not what an arm writes or searches (Edit/Write content, Grep pattern); a tool
  result counts only for a LITERAL absolute path under a sensitive root (`~`/`$HOME` in result text are not
  expanded), and in a command `~` inside quotes or `$HOME` inside single quotes is text
  (`grep -rn '~/.claude' .` is clean; `cat ~/.config/…`, `ls ~/.claude/plugins` stay flagged). A bare `/` is a
  path again as an argument of `find`, `grep -r/-R`, `rg`, `ls`, `du`, `tree`, `cat` (`find / -name x -exec cat
  {} +` is flagged; the heredoc division stays clean). After the arm ran, a git failure taking the patch (stale
  `.git/index.lock`, empty nested repository, unreadable file) or an `OSError` of the judge on a bundle file is
  a `REFUSED` verdict (`candidate_fault`, error recorded, cost kept, never replayed) instead of a void,
  replayable attempt; launcher/environment failures stay void. The reviewer's bundle no longer receives the
  implementer's `.claude/` files (`review_excluded` on the record). A cloud record refused on its `init` tool
  set is still audited (`outcome: contaminated` alongside the `tool_error` status, never replayed). Stated
  limits: the neutral harness's trajectory is not audited (mitigated by the deny-home profile); a local arm can
  signal the launcher (its profile starts from `(allow default)`; the void attempt is replayed at most once
  and listed). Documentation status (AGENTS.md R5): `pat-19-launcher-v1.md` and
  `pat-19-preflight-2026-10-05.json` updated; no CLI option of `foundry_cli.py`, product constant or routing
  table changed.

- Fixed (PAT-111, final real trials): the contamination audit no longer reads a token made only of slashes (`/`, `//`, the division operator of code) as the filesystem root: a legitimate Sonnet 5.5 run whose heredoc held `sum(values) / len(values)` was flagged `/` and would have been recorded `contaminated`. A bare `/` stays a path as an argument of a filesystem reader (see the review-round-2 entry). The final cloud argv (3 drivers) and the deny-home local profile (omp 18.4.10, mini-swe-agent 2.4.6 with `agent.step_limit=40`) are recorded as trial-run on 2026-10-05 in `pat-19-preflight-2026-10-05.json`; the audit is clean on those real streams.

- Changed (PAT-111): launcher corrections left by the PAT-108 reviews in `foundry.local_first_runner`
  (fake arms only; no real arm, no pinning, no `verified` flag changed). SIGTERM, SIGHUP and Ctrl-C are now
  converted for the whole duration of `screen` and `compare` (bundle build, judge, log reading, record
  writing included), not only while a driver runs: the cut attempt leaves an `interrupted` record and a
  `stop`; ledger and results writes are never cut in two; a second signal is never masked; a signal in
  the cleanup of a driver no longer skips the handler restoration or the profile removal; a settled local
  attempt not yet written is recorded as `interrupted`. SIGKILL and power loss remain uncovered (stated in
  `pat-19-launcher-v1.md`). The existing ledger is checked at start against the campaign, manifest and
  envelope digests (a launch refused at preflight no longer lets a modified configuration or envelope
  reuse the campaign id). The rule "economy unavailable as soon as a compared task is undecided" is
  labelled a launcher choice (`non_protocol_choices`). Local attempts are ledgered (`attempt_started`);
  `report` lists every attempt start or passed preflight without a settlement (`ledger.unsettled_starts`),
  warns on the screening (`selected` stays) and makes the comparison `inconclusive`, and refuses a
  comparison whose local candidate is not the one the screening selected (`compare` checks it at start
  when the screening results are in the same state directory; without them the report says so).
  Attempt names carry the launch rank, so a relaunch never overwrites the stream log of a killed attempt.
  A relaunch of `screen`/`compare` under the same campaign id, envelope, config and manifest now
  RESUMES (bounded rule validated by the maintainer on 2026-10-05, before any trial): decided attempts
  (a judge verdict) are skipped and never replayed; an attempt cut before any verdict (`interrupted` or
  `tool_error` without verdict, or an `attempt_started` with no settlement) is void and replayed once on a
  fresh bundle, recorded with `replay_of`; cloud money and the ledger/results invariant are unchanged (a
  void round keeps its cost, each session is named once); `report` lists `void_attempts` and `replays`
  and gives no `selected` while a screening task stays undecided (`undecided_tasks`).

- Changed (PAT-111, review round 1; fake arms only, no real arm run): (1) a cut AFTER a judge verdict or a
  review (local and cloud) now keeps them on the interrupted record, so the attempt is decided and a relaunch
  never replays it (no second chance after a verdict; a cut before the verdict stays void and is replayed
  once); a failure caused by the candidate (git configuration or attributes changed, tree still moving) is a
  `REFUSED` verdict (`candidate_fault`), not a void attempt; a cloud session killed after its settlement and
  before its record is listed in `report.void_attempts`; a contaminated review of path C leaves the task
  undecided with no takeover at first launch and at resume. (2) The unsandboxed cloud arms are documented for
  what they are (bare Claude Code, `bypassPermissions`, real home, open network, implicit credentials, no
  Foundry hook, not the Eiffel/Maigret definitions; the launcher protects neither the tracker, secrets nor
  merge for them): 19 best-effort Bash permission deny rules (`cloud_bash_deny`, data in the campaign config,
  literally in each cloud argv, enforced at load; command-prefix rules, evadable, not a sandbox); the
  post-run audit now covers COMMANDS (`command:` labels, `contamination.commands`), tool results and path
  resolution against the bundle with `cd` tracking (`find ~`, `cd ~ && cat .claude/x`, `src/../../..`); the
  launcher doc and the protocol amendment note are rewritten accordingly and the maintainer's explicit
  acceptance of the residual exposure (2026-10-05) is recorded. (3) Local arms now deny reads under the real home by default
  (`isolation.deny_home_by_default`, true; `isolation.allow_read_home`, empty) with the explicit deny list kept
  as a second layer; unit-tested on the generated profile and under a real `sandbox-exec` with a fake home,
  then tried on 2026-10-05 with the real `omp` 18.4.10 and mini-swe-agent 2.4.6. Also: the preflight refuses a loaded instance with no `modelKey`; the
  neutral harness is given `-c agent.step_limit={max_steps}` and `step_limit_hit` is also set a posteriori
  (attempt refused); `compare` refuses a C/N comparison with no screening results under its campaign id unless
  `--screening-campaign <id>` names a completed matching screening (read-only); the cloud evidence labels the
  command actually tried first (`--disallowedTools Agent` only: 11.6 / 16.3 / 16.5 s) apart from the final
  pinned argv, trial-run afterwards on 2026-10-05 (7.6 / 13.1 / 17.1 s). Documentation status (AGENTS.md R5): `pat-19-launcher-v1.md`, `pat-19-protocol-v1.md`,
  `pat-19-preflight-2026-10-05.json` and the campaign config updated; no CLI option of `foundry_cli.py`,
  product constant or routing table changed.

- Changed (PAT-111, pinning): the five real drivers of the PAT-19 launcher and the five local candidates are
  pinned in `docs/qualification/pat-19-campaign-v1.json` from real toy-task trials (not the corpus) made with
  the maintainer's confirmation of every model load and cloud call; the evidence is committed, sanitised, as
  `pat-19-preflight-2026-10-05.json`. Drivers now `verified: true` with an `evidence` reference (local omp
  18.4.10, neutral mini-swe-agent 2.4.6, Claude Code 2.1.285 for the current implementer `claude-sonnet-5-5`,
  the economy implementer `claude-haiku-4-5-20251001` and the reviewer `claude-opus-5-5`); a verified driver
  without evidence is refused at load. New launcher checks in `foundry.local_first_runner`: the preflight
  reads the loaded instance (`lms ps --json`) and refuses a context below the frozen 65,536, a different
  quantization or model key; `sandbox: false` is accepted only for a cloud driver with a reason and refused for
  any local driver (the cloud drivers run unsandboxed with the AGENTS.md R6 environment, real TMPDIR included,
  because Claude Code cannot authenticate under sandbox-exec); a cloud record is refused (tool error, spend
  kept in the ledger) unless the stream's `system/init` tools are a subset of the driver's `allowed_tools`
  (`Bash`, `Edit`, `Read`, `Write`: observed at the init event of Claude Code 2.1.285 with a 29-name
  `--disallowedTools` list; `--disallowedTools Agent` alone left 25 tools incl. `Task`, web and workflows), and `session_log.layout_verified` is now true on that condition (launcher reading equals the
  host's own usage on three real runs); a post-run audit of every arm's tool calls marks a record
  `contaminated` (undecided, never accepted, never replayed, listed in `report`) when it touched the plugin
  cache, another checkout of this repository or the user's configuration; the neutral harness executable comes
  from the operator variable `PAT19_MINI_BIN` at the pinned version, with its fixed environment, trajectory
  steps and no committed path. Candidates carry LM Studio key, engine, quantization, weights and chat-template
  digests, load command (`-c 65536`) and generation parameters (server defaults, not overridden); Devstral is
  declared unused. Limits stated, not enforced: the cloud and local arms expose different tool sets (recorded,
  not equalised), weights digests are recorded not recomputed; the `lms ps --json` fields (`modelKey`,
  `identifier`, `quantization` {name, bits}, `contextLength`) were observed on 2026-10-05 and are recorded in the
  evidence file (see the review-round-1 entry below for the default-deny of the home and the cloud
  exposure). Documentation status
  (AGENTS.md R5): `pat-19-launcher-v1.md` and `pat-19-protocol-v1.md` updated, no CLI option of
  `foundry_cli.py`, product constant or routing table changed.

- Added (PAT-108): the PAT-19 comparison launcher `foundry.local_first_runner`
  (`python3 -m foundry.local_first_runner {preflight,screen,compare,report}`, documented in
  `docs/qualification/pat-19-launcher-v1.md`, frozen config `docs/qualification/pat-19-campaign-v1.json`).
  It plays a task through three paths from PAT-107 bundles (A current cloud, B economy cloud, C one
  bounded local attempt then cloud review with path A taking over; plus a local-only neutral-harness
  attempt), reads premium tokens per class and role from the host session logs by the session id it
  hands to each cloud execution (unknown stays `null` with a reason, never 0), and records machine
  facts (swap, memory pressure, server memory). A mandatory authorization envelope caps cloud
  executions, tokens and duration (consumption is ledgered before each cloud execution); `screen` can
  never spend cloud; a failed machine preflight refuses to launch and the tool loads no model. The
  candidate runs under a generated `sandbox-exec` profile, a whitelisted environment and an isolated
  HOME; drivers not marked `verified` are refused for a real run (PAT-109 must pin them). `report`
  applies the protocol's pre-registered rules and returns compatibility, quality and economy
  separately, never a promotion. Only fake arms are exercised (dry run). No routing, mapping, role or
  default changed. Review round 1: an interrupted cloud execution (Ctrl-C, SIGTERM, launcher error) kills
  the arm's process group, and a `cloud_started` with no paired `settled` makes tokens unknown and refuses
  any further cloud execution; a local arm cannot read the user's configuration, plugin cache, SSH keys,
  other worktrees or the launcher inputs (explicit read-deny list, a campaign-config coordinate; not a
  default-deny of the home); the patch is taken against the root commit and shows new files; no replay of a
  recorded attempt; results carry `dry_run` and the sha256 of the campaign config, manifest and envelope
  and `report` refuses a mismatch; tool failures are `tool_error` records, an unreadable review is unknown,
  a stop on a cap is `inconclusive`; premium tokens stay unknown until a driver declares its session log
  layout verified; git on a bundle is neutralised and the judged state no longer lives in the candidate's
  tree. The protocol file was amended in place before any trial (candidate 2's repository, three
  downloads), validated by the maintainer on 2026-10-05: see the dated note at the top of
  `pat-19-protocol-v1.md`. `foundry.local_first_corpus` refuses symlinks under the product source
  prefixes; the manifest was regenerated by `verify` (12 of 12 unchanged). Review round 2: the ledger
  and the results can no longer disagree about spent work. An attempt cut by Ctrl-C, SIGTERM/SIGHUP or
  any exception leaves a `status: interrupted` record (premium total `null`) and a `stop` record, and is
  never replayable; each record names its cloud executions (`cloud_sessions`); `report` now requires the
  ledger beside the results and checks that every `cloud_started` is named by exactly one record and
  settled with known tokens: otherwise no economy verdict is given and the decision is `inconclusive`
  (inconsistent files are refused). A cap reached before a correction or a takeover is recorded
  (`stopped_by_cap`) and an undecided task gives no economy verdict; a sandbox configuration error is
  refused before any claim or reservation; signal handlers are restored exactly and a signal during the
  arm's start still kills it; the read-deny list gains credentials and shell history files and the
  generated profile is hidden from the arm; results are synced to disk and a truncated ledger or results
  line is a clean refusal. The only way out of a blocked campaign is a new campaign id and a new envelope.

- Changed (PAT-108): carry-over of the PAT-107 reviews in `foundry.local_first_corpus`: the judge purges
  every `*.pyc`/`__pycache__` of the candidate and redirects bytecode (`PYTHONPYCACHEPREFIX`), refuses a
  candidate inside the developer checkout and a bundle already judged, ignores virtualenv and
  `node_modules` directories the candidate created, and always passes `-c`; `verify` aborts on a tooling
  error instead of replacing the task; `replayability.origin_refs_containing` became a count
  (`origin_refs_containing_count`) so the manifest is reproducible. The 12 tasks are unchanged and
  still judged 12 of 12.

- Added (PAT-107): PAT-19 local-first qualification protocol v1 frozen under
  `docs/qualification/pat-19-protocol-v1.md` and a replayable corpus
  (`docs/qualification/pat-19-corpus-v1.md`): `foundry.local_first_corpus` freezes merged PRs in a
  committed snapshot (tracker statements from the committed, scrubbed
  `pat-19-corpus-statements-v1.json`, sha256 recorded), applies the protocol's inclusion/exclusion
  criteria with a recorded reason per PR (accent-insensitive title rules; manual include/exclude
  overrides with a mandatory reason), draws 6 comparison + 6 screening (`tamis`) tasks with a seeded
  sha256 ranking (the sets never overlap, and are not independent: see the corpus limits), builds a
  bundle as a brand-new one-commit repository at the base tree without the protected tests and with no
  link to the developer repository (no worktree, remote, alternates or merged SHA), and judges a
  candidate mechanically by restoring and running only those tests from the merged SHA in a scrubbed
  environment (temporary HOME, whitelisted variables, `--confcutdir`, REFUSED on harness-file edits or
  symlinks). The judge accepts the merged solution and refuses the base for all 12 drawn tasks. No
  model is run and no cloud/tracker call is made.

- Changed (PAT-106): the Linear ADR readback model recognises one more observed rendering
  (`docs/qualification/pat-106-linear-paragraph-list-observation.json`, native `PAT-ADR-0015`):
  a top-level `- ` list directly after a single-line paragraph line starting with a letter is read
  back with one empty line inserted between them; a list glued to any other non-heading line (pipe,
  `#tag`, digit, `**`, backtick...) is refused. Reads stay additive (the PAT-103 and pre-PAT-94 renderings
  are still accepted) and the pinned profiles are unchanged. A new body is now refused before
  any write, with a precise cause, for the unobserved neighbours: numbered, `*`/`+` or dash-only
  (setext) glued lists, lists glued in or next to a blockquote (the exact quote depth must match
  the line above; a `>` behind 4+ columns, a tab or a list marker, or opened directly under a
  list line, is refused), under a `--` line, under an indented line that is
  not inside a list item (including indented code starting with a marker), indented under a
  paragraph, indented (code) under a heading, thematic break or closing fence, or outside the
  exact shape; the check is a closed whitelist whose default refuses. Replaying `adr create` recognises the orphan Document of
  `PAT-ADR-0015` and writes only its witness. See `docs/linear-adr-body-guide.md`.

- Changed (PAT-103): the Linear ADR readback model and preflight now recognise the blank-line
  rendering observed by a bounded probe
  (`docs/qualification/pat-103-linear-blank-lines-observation.json`): two or more empty lines
  outside fenced code read back as one, a leading empty line and the final newline(s) are
  removed, fenced code is unchanged. A new body is refused before any write for unobserved
  neighbours (whitespace-only or CRLF blank lines, indented or quoted neighbours, a final
  newline after a fence or in an unclosed fence), for a body of empty lines only and for
  every table, whose delimiter row Linear rewrites; the same refusals now also apply before
  any write to the unchanged body of a migrated historical version 0 the model does not predict (status change, link,
  supersession). Pinned `PAT-72`/`PAT-86`/`PAT-16` profiles and the pre-PAT-94 model are
  unchanged.
  New guide: `docs/linear-adr-body-guide.md`.

- Fixed (PAT-101): residuals of the PAT-94 Linear ADR list preflight. A body that starts with an
  empty line next to a list is refused as a gap of two (the Document already adds one); task items
  (`1. [ ] a`, `1. [x] a`) are not plain items of the observed form; an interrupted supersession
  resume no longer refuses a dangling slot the model already validated (only its witness is
  written); the digest-pinned `PAT-72`/`PAT-86`/`PAT-16` profiles now apply before the strict list
  check, so a new ADR identical to a pinned source is accepted. No write path or format changed.
  See `docs/linear-tracker.md`.

- Fixed (PAT-102): residuals of the PAT-95 `close-epic` graph diagnostic. The "Seuls des
  reçus d'override valides bloquent" suggestion appears only when the strict refusal is itself an
  override refusal, otherwise it reads "Parmi les nœuds lus, seuls ...". The diagnostic captures
  the `SystemExit` a tracker (YouTrack) raises for a foreign node, keeps walking and falls back to
  the original refusal. `ValueError` is `foreign-project` only for an identifier or binding error;
  `JSONDecodeError`/`UnicodeError` are a `read-error`. "dérogé" is reserved for an override:
  a `dropped` node is "abandonné". Documentation fixes ("two issue reads", the `SKILL.md`
  cross-reference, one unwrapped line). No write behaviour, audit id or receipt format changed.

- Fixed (PAT-100): `close-epic` interruptions and refusals state the real closure state
  instead of a fixed "le reçu provider permettra la reprise". After a failure it reads,
  read-only, the provider audit and the local intent and says: no audit and an intent store
  actually read and empty (nothing written, re-run safe; any tracker whose intent store is
  not read, e.g. DevHub, is "intention locale non vérifiée", never safe; YouTrack's journal
  is read), intent without visible audit (ambiguous, a re-run is refused until the audit is
  visible, never reposts), audit pending (re-run with the exact `--accept-override` set of
  the audit, never the refused command), audit and Epic done (replay verifies), or state unreadable
  ("état du reçu inconnu", nothing claimed), with the audit id. Without a receipt it names the
  original cause (quota with remaining/reset and no immediate re-run, network/5xx with retry
  count, conflict, or exception type). A provider read error during the strict snapshot is
  intercepted like a conflict. Added `close-epic <EPIC-ID> --status` (read-only: `aucun audit`,
  `audit en attente`, `clos`; exit non-zero only on a failed read). No write, receipt, audit
  identity or replay rule changed. See `docs/linear-tracker.md` and `skills/close-epic/SKILL.md`.

- Changed (PAT-99): reading a closed Epic or taking a graph snapshot reads each node at most
  once per snapshot (`LinearTracker.graph_snapshot()`, used by `bounded_epic_graph_snapshot`
  and the PAT-95 diagnostic): N + 1 reads for N nodes instead of about 5 N (fake graph of 31
  children + 24 prerequisites: snapshot 269 -> 56, closed-Epic read 270 -> 57, diagnostic
  110 -> 55, whole `close-epic` 1118 -> 266). The pre-write S1 and the post-write
  verification stay independent fresh snapshots: no reuse across snapshots, calls or the
  write; closure semantics, receipts and fail-closed detection are unchanged. See
  `docs/linear-tracker.md`.

- Added (PAT-98): the Linear client retries a pure read at most 3 times (deterministic waits
  of 1 s, 2 s, 4 s) on a network failure (`transport_error`) or HTTP 500/502/503/504, never on
  data, binding or authorization errors, and never a write (PAT-ADR-0006 S2). A rate-limit
  failure (HTTP 429, a `RATELIMITED` code, or HTTP 400 with `x-ratelimit-requests-remaining`
  at zero) raises the new `LinearQuotaExhaustedError` carrying the remaining count and the
  reset time, without retry. The number of retries is exposed (`retries`, "after N retries"
  in the message). See `docs/linear-tracker.md` ("Transport errors, read retries and quota").

- Fixed (PAT-105): residuals of the PAT-98 review. A malformed error body, a non-string
  GraphQL error code or an aberrant reset header can no longer replace the typed error; 429
  is no longer in the retryable statuses; a zero remaining header on an HTTP 200 with an
  unrelated GraphQL error is no longer a quota; the quota message names `requests` or
  `complexity`; `tracker.retry_stats` counts retries, recovered and exhausted reads. The
  7 s bound covers waits only: with the 15 s attempt timeout the worst case is about 67 s per
  call. `tracker.rate_limit` and `retry_stats` are diagnostic-only across threads.

- Fixed (PAT-104): the test suite can no longer read or write the maintainer's real
  `~/.config/foundry`. A global autouse fixture in `tests/conftest.py` redirects `HOME` to a
  per-test temporary directory and clears `FOUNDRY_DATA`, `FOUNDRY_CONFIG` and
  `FOUNDRY_EXECUTION_RECEIPTS_DIR`; an audit hook fails any access to the real state
  directory, and `tests/test_state_isolation.py` asserts every resolver is sandboxed.
  Test infrastructure only, no production change.

- Added (PAT-95, PAT-ADR-0014): `close-epic` on Linear accepts the nominative flag
  `--accept-override=ID[,ID...]` (only with `--human-verdict=accepted`) to close an Epic
  whose nodes were delivered under a valid typed `acceptance-override` receipt. Refusals
  now list every non-positive graph node with its cause (read-only) instead of the first
  one, and the generic refusal no longer hides the real cause. The closure receipt binds,
  per waived node, its id, override-receipt digest and reason code (receipts without
  waiver keep their exact historical bytes); exact replay converges, a different set is
  refused, nodes stay `override`, the flag is neither CAS nor acceptance. YouTrack,
  GitHub Projects and DevHub refuse it. New public surface: the flag and
  `Tracker.epic_override_closure_supported`. The node list is carried by refusals of the
  fresh graph read only (not replay, pending-audit, provider-S1, unknown-id-alone or
  snapshot-transport refusals); a node that cannot be read is a `read-error` (sub-graph
  not traversed, the list is a lower bound, re-run first), `foreign-project` is reserved
  for binding refusals and `binding-error` marks a configuration error. Observable
  change: `query issue` `acceptance_coordinates` of a node under override now also carries
  `pr_url`, `head_sha`, `base_sha`, `review_digest`. The id grammar is
  `[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,8}`.
- Fixed (PAT-94): the Linear ADR readback model now recognises the one observed rewrite
  of a spaced top-level ordered list (`1.` to at most `9.`, +1 per item, one plain LF
  line per item, one empty LF line between items: those empty lines are removed). By
  whitelist, a new ADR body (create, body edit, single historical import) holding any
  other empty line in a list context, or two or more empty lines just before or after a
  list-context paragraph (`PAT-72`: Linear collapses them after a list item), is refused
  before any write with cause `unsupported list Markdown`, followed by the first
  offending line number. Status changes, links, supersessions and `import_adr` relations
  re-use the stored previous body without the strict check only when the closed model
  verifies its stored bytes; a historical version 0 whose bytes only the migration probe
  proves is checked as a new body, so such an ADR with an unmodelled list shape is
  refused before any write instead of leaving an orphan Document; an interrupted
  supersession pair is recovered under the same rule. Other recoveries of an interrupted
  write (an existing exact slot) and the probe-pinned migration batch keep the
  pre-PAT-94 checks only. Reads accept the canonical bytes, the PAT-94 rendering or the
  pre-PAT-94 model output. A replay of an interrupted `adr create` recovers only the
  missing witness.
- Documentation only, 2026-10-04: Foundry 1.0.0 is published (tag `foundry-v1.0.0`,
  2026-10-03). The frozen 1.0.0 entry and release documents below keep their shipped
  "candidate" wording; the published state, installed-copy readbacks and the items not
  yet observed are in the
  [final report](docs/qualification/pat-62-final-report.md) and the
  [post-publication observation](docs/qualification/pat-93-post-publication-observation.json).

## 1.0.0 — 2026-10-03

Prepared release candidate; publication, official installation/upgrade and loaded
version/ref readbacks remain pending. See [release notes](docs/release-1.0.0.md)
and [setup/migration](docs/migration-1.0.0.md). This dated source entry is not a
claim that 1.0.0 is already published or installed.

### Added
- The V1 distribution packages for Claude Code and Codex, both declaring 1.0.0,
  based on accepted PAT-61 (PR #72, merge `16cdaa0`). Its six qualified source
  configurations cover YouTrack, Linear and private personal GitHub Projects on
  both hosts; historical producer refs and unknowns remain unchanged.
- Short per-repository setup, official dual-host upgrade from 0.9.0, explicit
  legacy YouTrack binding upgrade, loaded-version verification, and configuration
  rollback limits. Installing a package performs no tracker migration.
- An explicit compatibility matrix with unchanged Ship-iOS 0.3.0 and its portable
  V1 selection/release-scope capability probes; no App Store effect is included.
- The qualified private personal GitHub Projects adapter now carries Foundry-owned
  in-progress/review/done and acceptance receipts bound to the exact repository,
  Project, Issue, item and PR generation. A live private sandbox PR passed independent
  review and exact-SHA CI before merge; native auto-close alone grants no authority
  (PAT-67).
- `query changelog <release>` now emits `foundry.release-scope.v1` from exact
  repository-scoped release mappings on YouTrack, Linear and GitHub Projects. The
  payload keeps Ship-iOS's `milestone`/`count`/`groups` projection and adds factual
  accepted, deviated, unfinished and unavailable classes; a native Done or PR mention
  alone is never delivery proof (PAT-59).

### Fixed
- R1 remains closed on invalid, drifted or ambiguous tracker binding (PAT-42),
  including before forbidden PR/merge/default-branch push commands can execute.
- YouTrack delivery receipts now distinguish accepted, deviated and unavailable
  release facts (PAT-82); older terminal issues without qualified receipts remain
  unavailable, without fabricated backfill.
- Proof-bound `openpr` and `merge` now pass the original predecessor state to
  bounded-state adapters, so a GitHub Projects PR can resume after a partial
  transition without weakening the common transition guard (PAT-67).
- YouTrack now proves a targeted issue or ADR's native project coordinate before every
  write and rejects it when any alias tombstones that provider project (PAT-43). The
  guard runs before milestone setup, command links, body writes, comments, and
  project-explicit issue or ADR creation, so an unrelated or unregistered checkout
  cannot write to the readable archive.

### Changed
- Bounded targeted grooming writes on YouTrack, Linear and GitHub Projects and
  audited non-atomic Epic closure carry PAT-ADR-0006's named S1→S2 overwrite risk;
  read/write/readback is detection, not provider CAS or concurrency exclusion.
  Linear delivery and AC authority remain proof-bound; native checkbox replacement
  remains refused. GitHub ADR Issue supports use an integrity-checked version chain.
- The delivered PAT-15/PAT-16 technically qualified defaults: Claude Haiku 4.5
  without effort, Sonnet 5.5/medium and Opus 5.5/high for frontier/apex; Codex
  GPT-6 Luna/low and GPT-6.1 Sol/medium/high/max. Minimum Claude client 2.1.284 for
  the complete set, actually tested 2.1.285/firstParty/Pro. Existing pins remain
  exact; observed access, effort and source/install limits are in the release notes.
- Package descriptions now announce only qualified provider variants. DevHub remains
  optional outside V1; Fable/Astra and economic superiority are not promoted.
  PAT-17/PAT-18/PAT-19 remain outside V1. Historical 0.9.0 evidence is preserved.

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
