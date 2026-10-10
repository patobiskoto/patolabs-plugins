# Foundry 1.1.0 — release candidate, 2026-10-10

**Prepared, not yet published or verified installed.** Both host manifests (Claude Code
and Codex) declare `1.1.0`. Ship-iOS retains its independent `0.3.0` version (see
[Ship-iOS](#ship-ios)). The versionless marketplace catalogues continue to point at
`./plugins/foundry` and `./plugins/ship-ios`. No tag exists for this version at the time
of writing, and nothing here is a claim that an installed host loads it.

1.1.0 packages what was merged on `main` after the tag `foundry-v1.0.0`: 37 commits,
PAT-93 to PAT-134, listed [at the end of this page](#merged-issues-in-this-release).
This page states what changed and what an upgrade can break. It announces **no gain, no
saving and no quality improvement**: the model and cache changes are adopted under
observation, the cost figures in the linked pages are simulations at list price, the
effect on a subscription quota is unknown, and no local model role was qualified.
Upgrade and rollback instructions are in [migration-1.1.0.md](migration-1.1.0.md).

## Breaking changes and risks

Read this section before upgrading. Each item gives what happens and its exact remedy.

### 1. Claude Code 2.1.293 minimum for the default `economy` tier

The Claude default of the `economy` tier is now the pin `haiku-5.5` at effort `medium`
(PAT-125, PAT-ADR-0016). It was `haiku-4.5` without effort in 1.0.0. The tier is held
by the scout role (Lupin) and by every non-gate role that falls back down to `economy`.
`CLAUDE_MODEL_MIN_HOST_VERSION` (`tooling/foundry/routing_facades.py`) requires Claude
Code 2.1.293 for that pin.

- **Observed below 2.1.293:** the routing hook denies the Agent call with a message
  naming the required and the observed version. Nothing is substituted: not Haiku 4.5,
  not the short `haiku` alias, not another tier, even when
  `FOUNDRY_CLAUDE_AVAILABLE_MODELS` lists them. The headless runners refuse the same
  way before any provider invocation.
- **Version not observable:** the launch proceeds with `host_version.status =
  "unknown"` and the `CLAUDE_HOST_VERSION_UNOBSERVED` warning. It is never reported as
  conforming, and an older host is then not stopped by Foundry.
- `routing show`, `routing resolve` and `doctor` bind nothing and observe no host
  version: a green doctor does not certify the minimum.
- **Remedy:** update Claude Code to 2.1.293 or later and start a new session (a session
  resumed after a host update can still report the old version). Or return to Haiku 4.5
  for the project with the exact mapping of item 2.

The client tested for 1.0.0 was 2.1.285, which is below this minimum. Both PAT-125
native trials and the PAT-134 trial ran on 2.1.294. How the version is observed and
every limit: [model routing](model-routing.md), section on the Haiku 5.5 promotion, and
AGENTS.md R7.

### 2. An `economy` mapping to `haiku-4.5` without an explicit null `effort` is refused

A project mapping that names only the model inherits the tier's new default effort
`medium`, which Haiku 4.5 does not accept. Resolution fails with `Haiku 4.5 : effort
rejeté, non applicable (reçu : 'medium', source : default)` and names the fix. A direct
request for `haiku-4.5` or for the `haiku` alias on that tier fails the same way.

- **Remedy:** write the whole entry in `.foundry/model-routing.json`:

  ```json
  {"mappings": {"claude": {"economy": {"model": "haiku-4.5", "effort": null}}}}
  ```

### 3. An effort-only `economy` override now applies to Haiku 5.5

`{"effort": "low"}` under `mappings.claude.economy` used to give Haiku 4.5 without
effort; it now gives `haiku-5.5` / `low`, a declared but not qualified profile, without
any warning. `{"effort": null}` alone used to pass and is now refused (`effort null
réservé à Haiku 4.5`).

- **Remedy:** name the model too (item 2 for Haiku 4.5), or remove the override.

### 4. An availability list without `haiku-5.5` makes the tier unavailable

With `FOUNDRY_CLAUDE_AVAILABLE_MODELS` set and lacking `haiku-5.5`, the `economy`
target is outside the list and the scout is refused (`RoutingUnavailableError`:
`Aucun modèle disponible pour le rôle 'scout' sur claude. Niveaux inférieurs essayés :
economy.`), even on a conforming host. A listed Haiku 4.5 is never substituted. The
example value the 1.0.0 manifest documented, `haiku-4.5,sonnet-5.5,opus-5.5`, is such a
list.

- **Remedy:** add `haiku-5.5` to the list, or unset the variable if availability is
  not constrained. To keep Haiku 4.5 instead, use the mapping of item 2.

### 5. Unknown behaviour of a host older than 2.1.248 with the Sonnet 5.5 profiles

The ten Sonnet 5.5 versioned profiles now carry `experimental: {cacheTtl: 1h}`
(PAT-134). Per the provider documentation read for PAT-134, the field requires Claude
Code 2.1.248. **Foundry adds no guard:** Sonnet 5.5 has no entry in
`CLAUDE_MODEL_MIN_HOST_VERSION`. Whether an older host ignores the field, rejects the
profile or accepts it is **unknown**: not documented for that case, not observed. If it
rejected the profile, every Sonnet 5.5 subagent launch would fail on that host; Sonnet
5.5 is the default of the `balanced` tier.

- 1.0.0 already declared (without enforcing it) a Claude Code minimum of 2.1.284 for
  its retained profile set, which is above 2.1.248. The field was observed honoured
  once, on 2.1.294, for one of the ten profiles.
- **Remedy:** update Claude Code. There is no per-project key that removes the field;
  removing it is the product rollback described, step by step, in
  [the PAT-134 page](qualification/pat-134-subagent-cache-1h.md).

### 6. `edit set-field <ID> State dropped` is now guarded on Linear

In 1.0.0 it was an unguarded grooming write. It now takes the same guarded path as the
new `edit transition <ID> dropped <expected-state>` (PAT-131, PAT-ADR-0017): it is
refused when the native state is `done`, when a `state-done` receipt exists, when an
Epic closure audit is present on the issue, when the receipt chain is invalid, or when
the chain holds only advisory `cockpit-evidence` receipts; and it can no longer be
combined with another field in the same command. A lifecycle write (start, review,
done) on a natively cancelled issue is now refused first, before any receipt is
appended. YouTrack, GitHub Projects and DevHub keep their behaviour.

- **Remedy:** abandon an issue with `edit transition <ID> dropped <expected-state>`,
  naming the native state it is in (`backlog`, `ready`, `blocked`, `in-progress` or
  `review`), and set other fields in a separate command. A refused case is a real
  refusal, not a bug to work around: see [the tracker contract](tracker-contract.md)
  and [the Linear page](linear-tracker.md).

### Other stricter behaviours on Linear

- A new ADR body is refused before any write when its list or blank-line Markdown is
  outside the renderings observed on Linear (PAT-94, PAT-101, PAT-103, PAT-106). How to
  write a body that passes: [linear-adr-body-guide.md](linear-adr-body-guide.md).
- A rate-limit failure now raises the typed `LinearQuotaExhaustedError`, without retry
  (PAT-98, PAT-105).

## What 1.1.0 contains since 1.0.0

### Claude model routing

- **Haiku 5.5 / `medium` is the Claude `economy` default** (PAT-125, PAT-ADR-0016).
  `haiku-5.5` (`claude-haiku-5-5`) is a canonical pin with its own effort scope (`low`
  to `max`) and one versioned profile per capability and effort; only `medium` is
  retained, the other four are declared and not qualified. Haiku 4.5 stays an exact
  historical pin and the short `haiku` alias is not promoted. The basis is the cheaper
  candidate regime of FOUNDRY-ADR-0019 on list price per token only; the effort
  `medium` is unmeasured. Two native compatibility trials are recorded as they came
  out, the first `not_conforming`, the second `conforming`:
  [pat-125-haiku-55-promotion.md](qualification/pat-125-haiku-55-promotion.md).
  Reviewer and architect floors, the `balanced`, `frontier` and `apex` tiers and the
  Codex defaults do not change.
- **1-hour prompt cache on the ten Sonnet 5.5 subagent profiles, under observation**
  (PAT-134). No routing, model, effort, user setting or configuration key changes. One
  native trial of two subagents observed the 1-hour class on one profile; it shows
  nothing about the other nine profiles, about usage credits, about cost or about
  quota: [pat-134-subagent-cache-1h.md](qualification/pat-134-subagent-cache-1h.md).

The current-state summary is AGENTS.md R7; the full contract is
[model-routing.md](model-routing.md).

### Tracker: Linear

- **Abandon an issue and close an Epic that has abandoned nodes** (PAT-131,
  PAT-ADR-0017, Linear only): `edit transition <ID> dropped <expected-state>`; a
  natively cancelled issue with a valid non-terminal projection reads `dropped`;
  `issue close-epic <EPIC-ID> --human-verdict=accepted --accept-dropped=ID[,ID...]`
  closes an Epic whose required graph has abandoned nodes, the named set being exactly
  the dropped nodes. A dropped node is never counted as accepted. Bounded detection,
  never CAS.
- **Epic closure with a nominative audited waiver** (PAT-95, PAT-ADR-0014):
  `--accept-override=ID[,ID...]`, and refusals that list every non-positive node with
  its cause (residuals: PAT-102).
- **Closure state named after an interruption** and `close-epic <EPIC-ID> --status`
  (PAT-100).
- **One read per node for an Epic graph** (PAT-99) and **bounded retries of pure reads
  with a typed exhausted quota** (PAT-98, PAT-105).
- **ADR bodies:** the readback model recognises the Linear renderings observed by
  probe and refuses the others before any write (PAT-94, PAT-101, PAT-103, PAT-106).

Exact contracts and limits: [tracker-contract.md](tracker-contract.md) and
[linear-tracker.md](linear-tracker.md).

### Offline measurement tools

These read existing session logs and ledgers. They call no model and no provider.

- `python3 -m foundry.cost_breakdown` (PAT-129): where premium tokens went in the
  PAT-19 v4 and v5 comparisons, unweighted and weighted by a dated list-price grid:
  [pat-19-cost-breakdown-v1.md](qualification/pat-19-cost-breakdown-v1.md).
- `python3 -m foundry.cache_ttl_replay` (PAT-132, extended to interactive sessions by
  PAT-133 and to 1-hour subagent lineages by PAT-134): replays recorded sessions with
  a simulated other cache lifetime, always as a prudent and a favourable bound:
  [pat-19-cache-ttl-replay-v1.md](qualification/pat-19-cache-ttl-replay-v1.md),
  [pat-133-cache-ttl-interactive-v1.md](qualification/pat-133-cache-ttl-interactive-v1.md).
- `python3 -m foundry.claude_profile_trial` (PAT-125, PAT-134): the bounded native
  trial tool, run by hand by the coordinator, never by the tests.

Their figures are simulations at list price on a small number of sessions. They are a
weight, never a bill, and say nothing about a subscription quota.

### Local-first qualification (PAT-19): concluded, no local role qualified

The package now ships the comparison launcher and its corpus tooling
(`foundry.local_first_runner`, `foundry.local_first_corpus`; PAT-107, PAT-108, PAT-111,
PAT-112, PAT-120, PAT-121, PAT-123, PAT-124, PAT-126, PAT-128) and the frozen protocols
v1 to v5 with their recorded results (PAT-109, PAT-110, PAT-114 to PAT-117, PAT-122,
PAT-127). These are qualification instruments, not a product feature: 1.1.0 activates
no local model, profile or default. The decision by use, recorded by PAT-130, is to
keep the cloud for autonomous local implementation and for local read-only exploration
(gain not demonstrated), and to abandon the one-call local compression track on this
corpus without measuring it:
[pat-19-decision-v2.md](qualification/pat-19-decision-v2.md) and
[pat-19-local-first-bilan.md](qualification/pat-19-local-first-bilan.md).

### Test isolation and 1.0.0 follow-up

- The test suite can no longer read or write the real Foundry state directory
  (PAT-104). Test infrastructure only.
- PAT-93 recorded the publication and public installation of 1.0.0:
  [pat-62-final-report.md](qualification/pat-62-final-report.md).

## What this release does not claim

- No measured gain in quality or cost, no bill or quota saving, for Haiku 5.5 or for
  the 1-hour cache.
- No qualification of the Haiku 5.5 profiles other than `medium`, of the nine Sonnet
  5.5 profiles the PAT-134 trial did not launch, or of any host version other than the
  one observed.
- No local model role, no new tracker variant, no new Codex default.
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Observation windows this version carries

| Change | Rule | Where it is written |
| --- | --- | --- |
| Haiku 5.5 / `medium` on `economy` (PAT-ADR-0016) | Window: the first 10 issues delivered by Foundry on the Claude host after the merge of PAT-125 (2026-10-09), or 30 days, whichever comes first. Reference: the last 10 issues delivered before that merge. Immediate trigger: on a conforming host, a `haiku-5.5` profile unavailable or divergent, an executed model other than `claude-haiku-5-5`, or a transmitted effort other than `medium`. Regression trigger: the number of issues with a blocking first-pass review, an escalation or a red CI on the delivered SHA exceeds the reference by at least two. The maintainer may roll back at any time without justification. | PAT-ADR-0016; [pat-125-haiku-55-promotion.md](qualification/pat-125-haiku-55-promotion.md) |
| 1-hour cache on the Sonnet 5.5 profiles (PAT-134) | Window: from the delivery of the version that carries the change to the first 10 issues delivered after it, or 30 days, whichever comes first; not extended without a written decision. At closure the 5 most recent main conversations are replayed and `rollback_decision` (`cache_ttl_replay.py`) returns `keep`, `roll_back` or `unknown`. It keeps 1 hour only if the smaller bound of `usd.entry_reads_not_expired.delta_usd_exact` is strictly positive; it returns `roll_back` when no Sonnet 5.5 lineage is observed at 1 hour; fewer than 3 contributing sessions is `unknown`. | [pat-134-subagent-cache-1h.md](qualification/pat-134-subagent-cache-1h.md) |

The PAT-ADR-0016 window counts from the merge of PAT-125, so it is already running
when 1.1.0 is published. The PAT-134 page starts its window "at the delivery of the
version that carries the change"; 1.1.0 is the first version that carries it. The
instant actually retained as its start is recorded on the ticket by the coordinator,
not decided by this page. The signals of the first window are downstream of
exploration and not causally attributable to it; the second rule has two named biases
towards `keep` and no margin. Both justify a rollback, not a conclusion about the
model or the cache.

## Outside 1.1.0

By decision of the maintainer (2026-10-10), 1.1.0 is published now and the postponed
children of Epic PAT-87 slip to a later version: **PAT-88, PAT-89, PAT-90, PAT-91,
PAT-17 and PAT-18**. None of their scope is in this package. Also outside: any local
model promotion, a cockpit, a new tracker, a general data migration and any App Store
submission.

## Ship-iOS

Ship-iOS stays at `0.3.0`. Since `foundry-v1.0.0`, the only changes under
`plugins/ship-ios` are two documentation edits made by PAT-93 (the 1.0.0 compatibility
note in its README and changelog). No bridge, skill, template, script or App Store API
changed, so its version does not move. `tooling/foundry/query.py`, which declares the
`foundry.release-scope.v1` capability the Ship-iOS bridge reads, is unchanged since the
tag; the Linear adapter it reads through did change (PAT-131: a started-then-dropped
issue reads as unfinished in a release scope, a never-started one as unavailable). The
installed Foundry 1.1.0 and Ship-iOS 0.3.0 pair has not been read back, and no bridge
run against 1.1.0 is claimed; that is a post-publication check.

## Release phases and evidence

1. **Before merge (this diff):** both manifests at `1.1.0`, the dated changelog
   section, this page and the migration guide; the release contract
   (`tests/test_release_contract.py`), the catalogue validation and the CI commands
   run on the candidate source; independent review on the exact diff, then exact-head
   check-runs plus legacy statuses (AGENTS.md R4). PR and merge only through
   `foundry:open-pr` and `foundry:merge-pr` (R1). This page does not supply the review
   or the CI result.
2. **After the gated merge:** the tag `foundry-v1.1.0` is placed on the exact merged
   commit, after the maintainer's explicit agreement on the content of this note. An
   unpushed tag or a branch is not a published release.
3. **After publication:** update through each host's official manager, without editing
   any cache; reload or start a new session; read back the host binary and version
   actually used, the manager listing, the loaded plugin root and both manifest
   versions at that root, then the installed CLI `doctor`, binding and route reads.
   The result is recorded on the ticket, each host separately, with any unavailable
   observation kept as unavailable.
4. **Freeze:** once published, this page, the migration guide and the `1.1.0`
   changelog section become frozen release evidence; their digests are added to
   `tests/fixtures/release-history.json` after publication, as PAT-93 did for 1.0.0.
   The test that requires the entry is excluded from CI (`historical_fixture`).

Nothing is declared "installed" by this diff. A pre-merge source test cannot pass a
post-installation criterion.

## Merged issues in this release

Established from `git log foundry-v1.0.0..HEAD` on `main` (37 commits). Each row is one
squash commit and its pull request.

| Issue | Commit | PR | Subject |
| --- | --- | --- | --- |
| PAT-93 | `b777331` | #74 | Record the publication and public installation of 1.0.0 |
| PAT-94 | `3ef030a` | #75 | Linear ADR bodies: recognise the spaced ordered list rendering, refuse the others |
| PAT-95 | `7b2f854` | #76 | Epic closure with a nominative audited waiver (PAT-ADR-0014) |
| PAT-104 | `8361cd2` | #77 | Isolate the test suite from the real Foundry state |
| PAT-98 | `5981991` | #78 | Bounded read retries and typed exhausted quota on Linear |
| PAT-99 | `6875740` | #79 | One read per node for an Epic graph |
| PAT-100 | `8145693` | #80 | Epic closure: name the real closure state after an interruption |
| PAT-105 | `5749f21` | #81 | Residuals of the PAT-98 review |
| PAT-102 | `f740beb` | #82 | Residuals of the PAT-95 review |
| PAT-101 | `58d3df8` | #83 | Residuals of the PAT-94 review |
| PAT-103 | `832cbbb` | #84 | Linear ADR bodies: blank-line renderings, body-writing guide |
| PAT-106 | `de52901` | #85 | Linear ADR bodies: list glued to a paragraph, ADR index repair |
| PAT-107 | `e2f8041` | #86 | PAT-19 protocol v1 frozen, replayable corpus |
| PAT-108 | `326e435` | #87 | PAT-19 three-path comparison launcher |
| PAT-111 | `b66702f` | #88 | Launcher preconditions, pinned drivers and candidates |
| PAT-112 | `879e7b7` | #89 | Two remaining biases of the local audit |
| PAT-109 | `13eb98d` | #90 | PAT-19 v1 screening run and its three verdicts |
| PAT-110 | `be4cb11` | #91 | PAT-19 decision after the v1 screening |
| PAT-114 | `3af0932` | #92 | PAT-19 protocol v2 frozen (local read-only exploration) |
| PAT-115 | `d3cfb99` | #93 | PAT-19 v2 screening and comparison run |
| PAT-116 | `00eee40` | #94 | PAT-19 protocol v3 frozen |
| PAT-117 | `9bb9e7f` | #95 | PAT-19 v3 screening and comparison run |
| PAT-120 | `2085be6` | #96 | Isolate the Foundry state of the launcher's cloud arms |
| PAT-121 | `4adeb2f` | #97 | PAT-19 protocol v4 frozen, comparison instrument repaired |
| PAT-122 | `fd6e00f` | #98 | PAT-19 v4 comparison run |
| PAT-123 | `90eeb0e` | #99 | Contamination audit repaired, proven by offline replay |
| PAT-124 | `130fa05` | #100 | Native sandbox for the launcher's cloud arms |
| PAT-126 | `d173e17` | #101 | PAT-19 protocol v5 frozen after instrument pilots |
| PAT-127 | `964288a` | #102 | PAT-19 v5 comparison run and local-first report |
| PAT-128 | `a961868` | #103 | Two instrument defects found by v5 |
| PAT-129 | `b6a86de` | #104 | Offline premium-work breakdown, price-weighted reading |
| PAT-125 | `72ee294` | #105 | Declare Haiku 5.5, its effort profiles, promote it on `economy` with rollback |
| PAT-130 | `ff9a53a` | #106 | Record the decision by use that concludes PAT-19 |
| PAT-131 | `1f1121a` | #107 | Abandon an issue, close an Epic with abandoned nodes, on Linear |
| PAT-132 | `e599f79` | #108 | Offline replay: 5-minute against 1-hour cache on played sessions |
| PAT-133 | `6e16acc` | #109 | Offline cache-lifetime measurement on interactive sessions |
| PAT-134 | `63df165` | #110 | 1-hour prompt cache on the Sonnet 5.5 profiles, under observation |

The full entries, with their limits and review rounds, are the `1.1.0` section of the
[changelog](../CHANGELOG.md). PAT-135 (this release preparation) changes version
metadata and documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`1.0.0` → `1.1.0`) | Updated; stated here and in migration-1.1.0.md. No other manifest key changed by PAT-135 |
| Dated Foundry changelog | `1.1.0 — 2026-10-10` section opened over the existing entries, which are not rewritten; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-1.1.0.md |
| Root and Foundry README | Updated: pointers to these two pages, with the "prepared, not yet published" status |
| Ship-iOS manifests, README and changelog | Not necessary: no file under `plugins/ship-ios` is changed by PAT-135 and its version does not move; its 1.0.0 compatibility statements remain true as written |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes; R7 already describes the Haiku 5.5 and cache state this release packages |
| `tests/fixtures/release-history.json` | Not necessary before publication: the `1.1.0` freeze is added after publication (phase 4) |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or
skill is changed by PAT-135. The mechanical documentation-status gate is not claimed
shipped.
