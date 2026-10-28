# patolabs — Claude Code + Codex plugins

The **patolabs** marketplace: a single monorepo of Claude Code and Codex plugins that
industrialize how a project goes from a rough idea to a shipped release. Add the
marketplace once, get every plugin.

Claude Code:

```
/plugin marketplace add patobiskoto/patolabs-plugins
/plugin install foundry@patolabs
/plugin install ship-ios@patolabs
/plugin install gauge@patolabs
```

Codex:

```
codex plugin marketplace add patobiskoto/patolabs-plugins
codex plugin add foundry@patolabs
codex plugin add ship-ios@patolabs
codex plugin add gauge@patolabs
```

The skill names are identical. Invoke them as `/foundry:frame` in Claude Code and
`$foundry:frame` in Codex (same rule for `ship-ios` and `gauge`).

**Published on 2026-10-03:** Foundry **1.0.0** on both hosts (tag `foundry-v1.0.0`),
compatible with Ship-iOS **0.3.0**. The [release notes](plugins/foundry/docs/release-1.0.0.md)
and the [setup and upgrade guide](plugins/foundry/docs/migration-1.0.0.md) are frozen
as shipped in the tag and still read "candidate"; the published state, the
clean-install and installed-pair readbacks on Claude Code and Codex, and what is not
observed (a public 0.9.0 → 1.0.0 upgrade, a model turn loading the plugins) are in the
[final report](plugins/foundry/docs/qualification/pat-62-final-report.md).

**Tagged, installed state observed per host:** Foundry **1.1.0** (both host
manifests declared it at the tag; Ship-iOS stays at **0.3.0**). Its
[release notes](plugins/foundry/docs/release-1.1.0.md) open with the breaking changes
(Claude Code 2.1.293 minimum for the default `economy` tier, among others) and its
[upgrade and rollback guide](plugins/foundry/docs/migration-1.1.0.md) gives the
update sequence and the rollbacks, each as far as it is established. Both are frozen as
shipped in the tag; the release note's wording "not tagged, not verified installed" and the guide's
"Candidate guide; the tag and the official installed-version checks are pending" are
superseded by the statuses below and the [errata](plugins/foundry/docs/release-1.1.0-errata.md).
Statuses, with their source (the readbacks recorded on the tracker issue PAT-135):

- **Tag:** `foundry-v1.1.0` points at `04af359`, the commit that merged the release pull
  request.
- **Claude Code:** observed loaded by a new session of Claude Code 2.1.294.
- **Codex:** observed listed at 1.1.0 by the manager; a new Codex session has **not** been
  verified.
- **Not verified:** the Foundry 1.1.0 / Ship-iOS 0.3.0 pair on a host, `configure show`,
  `registry selection --require-v1`, and a real Sonnet 5.5 subagent run under 1.1.0.
- **Return to 1.0.0 through an official manager:** not tried, see the
  [errata](plugins/foundry/docs/release-1.1.0-errata.md).

**Patch release 1.1.1:** it sets both Foundry host manifests to **1.1.1** (Ship-iOS
stays at **0.3.0**). Its content is the `1.1.1` section of the
[CHANGELOG](plugins/foundry/CHANGELOG.md); as a patch it has neither a release note nor
a migration guide (`release_requirements` in
`plugins/foundry/tests/test_release_contract.py`). That section opens with a change of
behaviour for every repository that uses the plugin: after a fully validated review
round, the Foundry skills write to the tracker without asking, in four bounded cases
(rule R9 (b) of `AGENTS.md`). Statuses, as recorded on the tracker issue PAT-149 on
2026-10-11:

- **Tag:** `foundry-v1.1.1` points at `343be14`, the commit that merged the release pull
  request (read back on the remote).
- **Claude Code:** Claude Code 2.1.294, the official manager reported "updated from
  1.1.0 to 1.1.1" and lists 1.1.1; a new non-interactive session started after the
  update loads 1.1.1 (the session reported that the context injected by its start-up
  hook cites the 1.1.1 plugin root). A new interactive session was not observed.
- **Codex:** Codex CLI 0.155.1, the official manager lists 1.1.1, installed and enabled.
  A new Codex session was refused before any model turn (the service's message: the
  model is not supported when using Codex with a ChatGPT account), after the
  session-start hook ran. Observed on 2026-10-11 under PAT-164, separately from the
  refusal and not a repeat of it: the refused session took its model from the top-level
  `model` of `~/.codex/config.toml`, not from Foundry's routing; and after the CLI was
  upgraded to 0.162.1, a new non-interactive session with the same model (`gpt-6.1-sol`)
  started normally and reported the 1.1.1 plugin root (see the Codex invocation façade
  section of `plugins/foundry/docs/model-routing.md` for the refusal).
- **Not verified:** the Foundry 1.1.1 / Ship-iOS 0.3.0 pair on a host, `configure show`,
  `registry selection --require-v1`, and a real Sonnet 5.5 subagent run under 1.1.1.
  The return to 1.1.0 or 1.0.0 through an official manager is not tried.

**Release 1.2.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **1.2.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`1.2.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), the
[release notes](plugins/foundry/docs/release-1.2.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-1.2.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. It is a minor version
because `query issue` no longer reports a broken ADR-to-issue link (the ADR commands
still refuse it) and the Tracker port gains a method; the release notes open with these
behaviour changes and quote the Linear request counts of
`plugins/foundry/docs/linear-request-cost.md` with their conditions, and no other gain.
Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages and the freeze entry. The two pages are frozen from that pull request on.
- **Observed after publication on 2026-10-11 (PAT-194):** the local and remote tag
  `foundry-v1.2.0` point at `81724f1`, the merge of PAT-193 (PR 136). Claude Code's
  local installation metadata records 1.2.0 at that commit, last updated at
  `17:12:12.981Z`; its cached manifest also reads 1.2.0. The Codex manager lists
  1.2.0, installed and enabled. This Codex session's supplied Foundry skill paths
  identify the 1.2.0 cache, whose manifest reads 1.2.0: loaded version observed here.
  The update commands themselves and the version loaded by a Claude session were
  not observed; the latter remains unknown. Selected evidence and sources are in
  [the observation record](plugins/foundry/docs/qualification/pat-194-post-publication-observation.json).
- **Not verified:** the Foundry 1.2.0 / Ship-iOS 0.3.0 pair on a host, and a Linear
  request count on 1.2.0 itself (the counts quoted were measured before the last change
  to the Linear adapter). The return to 1.1.1 through an official manager is not tried.

**Release 1.3.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **1.3.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`1.3.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), the
[release notes](plugins/foundry/docs/release-1.3.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-1.3.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. It is a minor version
because it adds CLI verbs (`routing prepare-review`, `search-review`, `list-review`,
`postmerge-review`, `edit reconcile-create`) and changes the review path of the
`review-pr` and `merge-pr` skills; the release notes open with these behaviour changes
and claim no gain. Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages and the freeze entry, with the status sentences of `docs/postmerge-review.md`
  and `skills/review-pr/SKILL.md` (PAT-ADR-0020 accepted, supersedes PAT-ADR-0019) and
  one comment line of `scripts/ci_plan.py`. The two pages are frozen from that pull request on.
- **Not done by it, and not claimed here:** the tag, the update of the installed plugin
  on Claude Code and on Codex, and the readback of the version a host actually loads. Now
  known: the tag `foundry-v1.3.0` exists on `origin` (`git ls-remote --tags origin
  'foundry-v1.3.0^{}'`, read 2026-10-15; commit `0c38fe0cb51d0c29b311550f4cd33163fbb0d0b8`,
  the squash commit of PR #145, an ancestor of `main`). The installation and the
  loaded-version readback are not stated here; they are recorded on PAT-199.
- **Not verified:** the Foundry 1.3.0 / Ship-iOS 0.3.0 pair on a host, the native
  provenance of the Codex reviewer profile (PAT-196), the native availability of the
  eight rollback profiles (PAT-198), the maximum native Linear comment size (only 12,294
  bytes probed), and a Linear request count on 1.3.0. The return to 1.2.0 through an
  official manager is not tried (PAT-162).

**Release 1.4.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **1.4.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`1.4.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), assembled from the fifteen
fragments of `plugins/foundry/changelog.d/` (which it deletes), the
[release notes](plugins/foundry/docs/release-1.4.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-1.4.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. It is a minor version
because it adds CLI verbs (`routing carry-review-proof`, `routing review-rounds`, the
`backlog` target of `edit transition`) and a skill (`foundry:release`), and changes
behaviour; the release notes open with these behaviour changes and claim no gain.
Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages, the freeze entry, the removal of the fragments and one comment line of
  `scripts/ci_plan.py`. The two pages are frozen from that pull request on.
- **Not done by it, and not claimed here:** the tag `foundry-v1.4.0`, the update of the
  installed plugin on Claude Code and on Codex, and the readback of the version a host
  actually loads. They come after the merge; the tag and the host update each need the
  maintainer's explicit authorization (STOPs 6 and 7), the readback does not (step 8), and
  all are recorded on the release ticket (`skills/release/SKILL.md`, steps 6 to 9). Now
  known: the tag `foundry-v1.4.0` exists on `origin` (`git ls-remote --tags origin
  'foundry-v1.4.0^{}'`, read 2026-10-16; commit `b2e2c0cb71ee6418a4b17af29257398f43c6958e`,
  the squash commit of PR #158); the installation and the readbacks are recorded on PAT-211,
  not restated here, and the version Claude Code loads is not verified there.
- **Not verified:** the Foundry 1.4.0 / Ship-iOS 0.3.0 pair on a host, the Codex
  reviewer profile provenance beyond the one session recorded by PAT-196 (host `0.162.1`),
  the native availability of the rollback profiles (PAT-198), the Linear rendering of
  PAT-201's links and of the PAT-205 `(AGENTS.md#R<n>)` qualification. The return to
  1.3.0 through an official manager is not tried (PAT-162).

**Release 1.5.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **1.5.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`1.5.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), assembled from the nineteen
fragments of `plugins/foundry/changelog.d/` (which it deletes), the
[release notes](plugins/foundry/docs/release-1.5.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-1.5.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. It is a minor version
because it adds CLI verbs (`campaign-approval approve|status|check`, `campaign-cadence
next` and `step|run`, `issue accept`, `routing record-review-remarks`) and changes
behaviour (campaign sessions in the native sandbox, the Linear `issue openpr` State
readback, the Epic preview on Linear); the release notes open with these behaviour changes
and claim no gain. Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages, the freeze entry, the removal of the fragments and one comment line of
  `scripts/ci_plan.py`. The two pages are frozen from that pull request on.
- **Not done by it, and not claimed here:** the tag `foundry-v1.5.0`, the update of the
  installed plugin on Claude Code and on Codex, and the readback of the version a host
  actually loads. They come after the merge; the tag and the host update each need the
  maintainer's explicit authorization, and all are recorded on the release ticket
  (`skills/release/SKILL.md`, steps 6 to 9).
- **Not verified:** the Foundry 1.5.0 / Ship-iOS 0.3.0 pair on a host, the native sandbox
  of campaign sessions beyond the one live trial of PAT-220 (refusing layer not recorded),
  the cause of the Linear State return of PAT-230 (deduced), and the cost of a campaign
  (PAT-219). The return to 1.4.0 through an official manager is not tried (PAT-162).

**Release 2.0.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **2.0.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`2.0.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), assembled from the eighteen
fragments of `plugins/foundry/changelog.d/` (which it deletes), the
[release notes](plugins/foundry/docs/release-2.0.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-2.0.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. The number 2.0.0 is a
milestone chosen by the maintainer on 2026-10-18; the fragments record no breaking change.
The release notes open with the behaviour changes and claim no gain. Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages, the freeze entry, the removal of the fragments and one comment line of
  `scripts/ci_plan.py`. The two pages are frozen from that pull request on.
- **Not done by it, and not claimed here:** the tag `foundry-v2.0.0`, the update of the
  installed plugin on Claude Code and on Codex, and the readback of the version a host
  actually loads. They come after the merge; the tag and the host update each need the
  maintainer's explicit authorization, and all are recorded on the release ticket
  (`skills/release/SKILL.md`, steps 6 to 9).
- **Not verified:** the Foundry 2.0.0 / Ship-iOS 0.3.0 pair on a host, the real display of
  the Gauge alert on Claude Code and Codex (to be observed after the merge), and the return
  to 1.5.0 through an official manager (not tried, PAT-162).

**Release 2.1.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **2.1.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`2.1.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), assembled from the two
fragments of `plugins/foundry/changelog.d/` (which it deletes), the
[release notes](plugins/foundry/docs/release-2.1.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-2.1.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. The release notes open
with the behaviour changes (Python 3.11 as the declared minimum, visible hook failures)
and claim no gain. Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages, the freeze entry, the removal of the fragments and one comment line of
  `scripts/ci_plan.py`. The two pages are frozen from that pull request on.
- **Not done by it, and not claimed here:** the tag `foundry-v2.1.0`, the update of the
  installed plugin on Claude Code and on Codex, and the readback of the version a host
  actually loads. They come after the merge; the tag and the host update each need the
  maintainer's explicit authorization, and all are recorded on the release ticket
  (`skills/release/SKILL.md`, steps 6 to 9).
- **Not verified:** the Foundry 2.1.0 / Ship-iOS 0.3.0 pair on a host, the interpreter a
  host resolves as `python3` for hooks, and the return to 2.0.0 through an official
  manager (not tried, PAT-162).

**Release 2.1.1, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **2.1.1** (Ship-iOS stays at **0.3.0**) and carries the dated
`2.1.1` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), assembled from the one
fragment of `plugins/foundry/changelog.d/` (which it deletes), and the freeze entry of
`plugins/foundry/tests/fixtures/release-history.json`. It is a patch: it adds no release
note and no migration guide (the
[2.1.0 release notes](plugins/foundry/docs/release-2.1.0.md) and
[upgrade and rollback guide](plugins/foundry/docs/migration-2.1.0.md) still describe 2.1.0
and are untouched). The section names the behaviour change (re-execution of the CLI and
the four hooks that import `foundry` under a supported Python found in a closed list,
PAT-267) and claims no gain. Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the freeze
  entry, the removal of the fragment and one comment line of `scripts/ci_plan.py`.
- **Not done by it, and not claimed here:** the tag `foundry-v2.1.1`, the update of the
  installed plugin on Claude Code and on Codex, and the readback of the version a host
  actually loads. They come after the merge; the tag and the host update each need the
  maintainer's explicit authorization, and all are recorded on the release ticket
  (`skills/release/SKILL.md`, steps 6 to 9).
- **Not verified:** the Foundry 2.1.1 / Ship-iOS 0.3.0 pair on a host, the interpreter a
  host resolves as `python3` for hooks, and the return to 2.1.0 through an official
  manager (not tried, PAT-162).

**Release 2.2.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **2.2.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`2.2.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), assembled from the eight
fragments of `plugins/foundry/changelog.d/` (which it deletes), the
[release notes](plugins/foundry/docs/release-2.2.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-2.2.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. The release notes open
with the behaviour changes (`issue wait-ci`, the exit code 3 of `issue merge`, the lease
push of `issue openpr`, the partial-review options, the `Nits` companion Epic, the refusal
of other `*.md` names in a new Linear ADR body) and claim no gain. Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages, the freeze entry, the removal of the fragments and one comment line of
  `scripts/ci_plan.py`. The two pages are frozen from that pull request on.
- **Not done by it, and not claimed here:** the tag `foundry-v2.2.0`, the update of the
  installed plugin on Claude Code and on Codex, and the readback of the version a host
  actually loads. They come after the merge; the tag and the host update each need the
  maintainer's explicit authorization, and all are recorded on the release ticket
  (`skills/release/SKILL.md`, steps 6 to 9).
- **Not verified:** the Foundry 2.2.0 / Ship-iOS 0.3.0 pair on a host, and the return to
  2.1.1 through an official manager (not tried, PAT-162).

**Release 2.3.0, prepared by its release pull request:** that pull request sets both
Foundry host manifests to **2.3.0** (Ship-iOS stays at **0.3.0**) and carries the dated
`2.3.0` section of the [CHANGELOG](plugins/foundry/CHANGELOG.md), assembled from the 34
fragments of `plugins/foundry/changelog.d/` (which it deletes), the
[release notes](plugins/foundry/docs/release-2.3.0.md), the
[upgrade and rollback guide](plugins/foundry/docs/migration-2.3.0.md) and the freeze
entry of `plugins/foundry/tests/fixtures/release-history.json`. The release notes open
with the behaviour changes (DevHub as a V1 provider, the full-history cutover toward
DevHub, `issue accept` on DevHub, the `--help` handling, the cadencer changes) and claim
no gain. Statuses:

- **Done by the release pull request:** the manifests, the CHANGELOG section, the two
  pages, the freeze entry, the removal of the fragments and one comment line of
  `scripts/ci_plan.py`. The two pages are frozen from that pull request on.
- **Not done by it, and not claimed here:** the tag `foundry-v2.3.0`, the update of the
  installed plugin on Claude Code and on Codex, the readback of the version a host
  actually loads, and the cutover of this repository toward DevHub (PAT-334). They come
  after the merge; the tag and the host update each need the maintainer's explicit
  authorization, and all are recorded on the release ticket
  (`skills/release/SKILL.md`, steps 6 to 9).
- **Not verified:** the Foundry 2.3.0 / Ship-iOS 0.3.0 pair on a host, and the return to
  2.2.0 through an official manager (not tried, PAT-162).

**Release 2.7.0, minor source candidate prepared under PLP-333:** both Foundry
manifests declare **2.7.0**. This preparation carries PLP-330, PLP-329, PLP-328 and
PLP-326 into the dated [CHANGELOG](plugins/foundry/CHANGELOG.md), removes their fragments,
adds the [release note](plugins/foundry/docs/release-2.7.0.md),
[upgrade and rollback guide](plugins/foundry/docs/migration-2.7.0.md) and freeze entry,
and updates the README references and CI release-version comment.
Behaviour sources are in [kpi-export.md](plugins/foundry/docs/kpi-export.md) and the pages
it links. The tag `foundry-v2.7.0`, host installation and listed/installed/loaded-version
readbacks are not done or claimed here.
Merge, tag and host update retain separate agreements
([release skill](plugins/foundry/skills/release/SKILL.md), steps 5–7).

## Public scope

This repository is published under [Apache-2.0](LICENSE). It is maintained by the
Patolabs team, but it is **not accepting external contributions at this stage**: do not
open a pull request or look for a GitHub Issues backlog. GitHub Issues are disabled and
the product backlog is kept out of this repository.

To report a vulnerability, use [GitHub private vulnerability reporting](SECURITY.md).
Do not disclose security details in a pull request, commit, or public discussion.

Foundry V1 covers YouTrack, Linear, and **private personal GitHub Projects bound to a
private repository**, with the GitHub code-host adapter. Public/organization Projects
and GitHub Apps are unqualified. DevHubTracker remains an optional adapter outside this
V1 matrix. Each checkout selects one explicit tracker/project binding by its canonical
Git remote. The fail-closed Linear issue adapter requires explicit repository/team/project/
state/type/label/milestone IDs selected from the actual checkout's canonical Git
remote. Linear supports bounded grooming of fields, body and parent; its delivery
state, PR and AC authority use deterministic append-only receipts, with targeted native
state synchronization. Native AC checkbox replacement remains unavailable. Linear ADRs
are versioned, witness-bound project Documents; GitHub ADRs use reserved Issue supports
and integrity-checked version comments. Epic closure is audited and non-atomic on
YouTrack, Linear and GitHub Projects (DevHub closes atomically). Bounded detection does not exclude an external overwrite between
the fresh read and write (S1→S2). A cockpit Evidence Plane envelope is advisory and
has no state, AC or merge authority.
This repository itself is bound to DevHub (project PLP) for issues and ADRs since the
cutover of 2026-10-23 (`plugins/foundry/docs/qualification/pat-334-cutover.md`); its former
Linear project PAT is no longer bound, and the historical YouTrack project
is designated a read-only origin archive (see the cutover incident record in
`plugins/foundry/docs/linear-cutover-operations.json`). ChatGPT MCP activation
remains separate planned work.

## Plugins

| Plugin | Owns | In one line |
|---|---|---|
| [**foundry**](plugins/foundry/) | idea → merge | ADR-backed framing, roadmap and gated delivery on the four qualified tracker variants. Version 2.7.0 (both host manifests; source candidate). |
| [**ship-ios**](plugins/ship-ios/) | merge → live | The iOS release loop: locale-neutral changelog → per-locale release notes → Xcode Cloud build → TestFlight gate → App Store submit. |
| [**gauge**](plugins/gauge/) | quotas | Read-only gauge of the Codex, Claude, Linear and GitHub Actions quota counters; unreadable data is unknown, never zero. |
| [**relay**](plugins/relay/) | session hand-over | Writes a verified, secret-free resume state at the end of a session, as an owner-only local file outside git tracking, and reloads it re-verified at session start. |
| [**blueprint**](plugins/blueprint/) | authorised-target inventory | Prepares pinned REA, Ghidra and JDK without a global install, refuses any target absent from the maintainer's registry, writes a reproducible inventory with REA evidence, groups it, decompiles through one reusable Ghidra analysis, measures coverage (constants, data model, resources, screens) and turns a product-language spec into Foundry payloads; unreadable facts are unknown. |
| [**witness**](plugins/witness/) | iOS and local web change proof | Runs the tests of an Xcode project on a named simulator, installs and launches the app, reaches a screen by a versioned deep link or UI test navigation, takes a screenshot and writes a dated report, with `xcodebuild` and `xcrun` (plus a read-only `git` check); does the same for a local web page in a headless Chromium-family browser with a fresh profile; compares a before and an after proof. |

They **compose by data, not code**: Foundry emits a locale-neutral changelog
(`query changelog <milestone>`); ship-ios turns it into per-locale release notes and
drives the store. ship-ios never depends on Foundry — no Foundry, it takes a changelog
file instead.

## Layout

```
.claude-plugin/marketplace.json   Claude Code catalogue
.agents/plugins/marketplace.json Codex catalogue
plugins/
  foundry/     .claude-plugin/ .codex-plugin/ tooling/ skills/ hooks/ agents/ tests/
  ship-ios/    .claude-plugin/ .codex-plugin/ skills/ templates/ scripts/ tests/
  gauge/       .claude-plugin/ .codex-plugin/ hooks/ skills/ tooling/ tests/
  relay/       .claude-plugin/ .codex-plugin/ hooks/ skills/ tooling/ tests/
  blueprint/   .claude-plugin/ .codex-plugin/ skills/ tooling/ tests/
  witness/     .claude-plugin/ .codex-plugin/ skills/ tooling/ tests/
.github/workflows/ci.yml          one CI: a `plan` job, one job per plugin (foundry, plus foundry-python-min under the minimum Python, ship-ios, gauge, relay, blueprint, witness)
                                  and the catalogue validation
```

Each plugin keeps its own `version`, `CHANGELOG`, and lifecycle — the monorepo unifies
distribution, not versioning, per accepted FOUNDRY-ADR-0004/0005 (retrievable through
`query adr <ADR-ID>` from this bound checkout).

## Develop

```
cd plugins/foundry && pip install pytest ruff && pytest -q -m "not integration" tests  # Foundry: pure logic
```

Parallel run (`pytest-xdist`), which pull requests the CI skips `foundry` for, and the
release rule: see the "Which CI jobs run" part of [`plugins/foundry/README.md`](plugins/foundry/README.md).

Foundry is piloted with its own method (DevHub project `PLP`, bound by `.foundry/tracker.json`); the pipeline runs in-repo via
`python3 plugins/foundry/tooling/foundry_cli.py <module>`. Runtime configuration resolves
from environment variables, Claude plugin options, the macOS keychain, then
`~/.config/foundry/config.env`.
