# Foundry 2.3.0 — release note, 2026-10-23

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation, a version loaded by a host or the cutover of this repository:
those states come after the merge and are recorded on the release ticket
(`skills/release/SKILL.md`, step 9), not here. Both host manifests (Claude Code and
Codex) declare `2.3.0`. The versionless marketplace catalogues continue to point at
`./plugins/foundry` and `./plugins/ship-ios`, and also list the plugins named in
"Other plugins of the same catalogue" below.

2.3.0 packages what was merged on `main` after the tag `foundry-v2.2.0`: 34 commits,
PR #223 to #256, listed [at the end of this page](#merged-issues-in-this-release)
(`git log --oneline foundry-v2.2.0..HEAD` at `7289824`). It is a minor release:
`release_kind` (`tests/test_release_contract.py`) returns `minor` for a version whose
patch is zero and whose minor is not, and `release_requirements` then asks for this page
and for the migration guide. This page announces **no gain**: no time, cost, quota or
quality improvement is measured by it or claimed. Upgrade and rollback instructions are
in [migration-2.3.0.md](migration-2.3.0.md).

The release contains the DevHub qualification and import tickets of Epic PAT-295, among the
entries PAT-296 to PAT-332 of the changelog (that range also holds tickets of deferred review
remarks), and the green rehearsal of the
full Linear to DevHub cutover (PAT-332). It does **not** perform the cutover of this repository: that is the
separate ticket PAT-334, and nothing on this page says it is done.

## Behaviour changes and risks

Read this section before upgrading. Each item states what changed and points to the page
or the changelog entry that holds the detail; this page does not restate what a tool
launches, loads or writes.

### 1. DevHub is a V1 provider (PAT-323, PAT-ADR-0031)

`devhub` joins `registry._V1_TRACKERS` and its binding is an ordinary canonical
repository marker. The pilot path is retired. An existing DevHub registry entry that has
`canonical_repo` but no marker fails closed from its checkout; the operator action is in
the `2.3.0` changelog entry of PAT-323 and in [devhub-tracker.md](devhub-tracker.md).
The contract cells are in [tracker-contract.md](tracker-contract.md).

### 2. Cutover toward DevHub covers the whole history (PAT-324, PAT-327, PAT-328, PAT-329, PAT-331, PAT-332, PAT-ADR-0032)

`cutover preflight`, `copy` and `activate` toward DevHub, with terminal and archived
issues, links, comments and ADRs: see [devhub-tracker.md](devhub-tracker.md) ("Selective
cutover target", "Selective import") and the `2.3.0` changelog entries. The rehearsal
report is [qualification/pat-332-cutover-rehearsal.md](qualification/pat-332-cutover-rehearsal.md).
**A cutover toward DevHub needs a DevHub at `72a24e2` or later**
([devhub-tracker.md](devhub-tracker.md), "Corrections from the PAT-332 rehearsal"); Foundry
does not check the server version (same page). PAT-332 also changes the Linear history
export ([linear-tracker.md](linear-tracker.md), "History export of archived links").

### 3. `issue accept` on DevHub (PAT-330, PAT-ADR-0033)

`issue accept` and `issue accept --status` now run on DevHub as well as on Linear; the
conditions are in the `2.3.0` changelog entry of PAT-330 and in
[devhub-tracker.md](devhub-tracker.md). The refusal message for the other trackers
changes wording (same entry).

### 4. `--help` and option handling (PAT-286)

`-h`/`--help` anywhere in the arguments of the verbs named by the PAT-286 changelog entry
prints the usage and writes nothing (`adr create --help` used to create an ADR titled
`--help`). The same entry makes `--ac-override-reason` an option of `issue merge` only,
makes `issue openpr <ISSUE-ID>` refuse a current branch that is not that issue's branch,
and changes `issue wait-ci` (immediate `not_green`, refusal of `nan`/`inf` and of a
repeated option). A script that relied on the old behaviour must be checked against that
entry.

### 5. Campaign cadencer and proof carry (PAT-288, PAT-291, PAT-294, PAT-297)

The cadencer defers the remarks of a ticket with no origin Epic to the companion Epic
`Nits` (PAT-291) and tries the proof carry when the PR base is unchanged (PAT-288,
PAT-294); see [campaign-effects.md](campaign-effects.md), [review-rounds.md](review-rounds.md)
and `AGENTS.md#R9`. The `correct` decision with cause `no_origin_epic` no longer exists
(changelog entry of PAT-291).

### 6. Linear usage limit (PAT-310)

`USAGE_LIMIT_EXCEEDED` is recognised as a durable refusal with a known effect; see the
changelog entry of PAT-310 and [linear-tracker.md](linear-tracker.md).

### 7. DevHub adapter changes

The adapter gains reads, writes and refusals described entry by entry in the changelog
(PAT-309 to PAT-321, PAT-324 to PAT-328) and in [devhub-tracker.md](devhub-tracker.md);
the qualification status of each need is in [devhub-requirements.md](devhub-requirements.md).

## What 2.3.0 contains since 2.2.0

The `2.3.0` section of the [changelog](../CHANGELOG.md) holds the 34 entries, ordered by
issue number descending. `tooling/foundry/routing_facades.py` and
`tooling/foundry/effort_policy.py` are unchanged since `foundry-v2.2.0`
(`git diff --stat foundry-v2.2.0..HEAD` on these paths, empty at `7289824`): no model or
effort vocabulary changes. Any other change to an option or a key is stated by the
changelog entries, which are the authority.

## Other plugins of the same catalogue

One file under `plugins/ship-ios` changed since `foundry-v2.2.0`:
`scripts/changelog_bridge.py`, whose `_V1_TRACKERS` gains `devhub` (PAT-323;
`git diff foundry-v2.2.0..HEAD -- plugins/ship-ios` at `7289824`). Ship-iOS retains its
independent version, which this release does not change. No file under `plugins/gauge`,
`plugins/relay`, `plugins/blueprint` or `plugins/witness` changed (same command on these
paths, empty). Nothing here says that any of them is installed on a host.

## What this release does not claim

- No gain.
- No tag, installation or loaded version (see "Release phases and evidence").
- No cutover of this repository toward DevHub: PAT-334 is not done by this release.
- No run of the host commands of `foundry:release`.
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **Rollback to an earlier plugin version is an untried procedure** (PAT-162); see
  [migration-2.3.0.md](migration-2.3.0.md).
- **A DevHub older than `72a24e2`** refuses part of the cutover (item 2; deduced from the route,
  not run, [devhub-tracker.md](devhub-tracker.md), "Corrections from the PAT-332 rehearsal").
- **The rehearsal is not the cutover:** its figures are in the report cited in item 2.

## Release phases and evidence

1. **This pull request prepares; it does not publish.** It bumps the two manifests,
   opens the `2.3.0 — 2026-10-23` changelog section from the 34 fragments (which it
   deletes), adds this note and the migration guide, and adds the freeze entry. Merge
   only through `foundry:merge-pr` after independent review on the exact diff and
   exact-head CI on two sources (`AGENTS.md` R1, R4). This page does not supply the
   review or the CI result.
2. **The gated merge is the moment from which a host can receive the version.** The
   catalogues carry no version and no ref, so a host that runs an official update after
   the merge is expected to receive manifests saying `2.3.0`, before any tag exists.
   This is derived and **not observed**.
3. **The tag is the named reference, not the delivery.** `foundry-v2.3.0` is to be placed
   on the exact merged commit, on the maintainer's explicit authorization (STOP of
   `skills/release/SKILL.md`). This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a
   new session; read back the version actually loaded, each host separately. The result
   goes to the release ticket, not to this page.
5. **Freeze:** this page, the migration guide and the `2.3.0` changelog section are
   frozen by digest from the release pull request on. A later correction goes to an
   errata page.

Nothing is declared "tagged" or "installed" by this diff.

## Merged issues in this release

| Issue | PR | Subject |
| --- | --- | --- |
| PAT-332 | #256 | rehearse the full Linear to DevHub cutover and fix its five gaps |
| PAT-331 | #255 | Import intégral de l'historique Linear vers DevHub (issues terminales, commentaires, reçus, ADR) |
| PAT-325 | #254 | Review remarks deferred from PAT-295 |
| PAT-330 | #253 | Adaptateur DevHub : ouvrir `issue accept` (acceptation humaine d'une issue non-code) |
| PAT-329 | #252 | qualify cutover activate toward DevHub offline and live (DHN-47) |
| PAT-327 | #251 | carry source-only issue references and unknown ADR relations in the DevHub import |
| PAT-328 | #250 | Adaptateur DevHub : refuser au préflight un remplaçant d'ADR proposed et les valeurs désactivées par le workflow |
| PAT-324 | #249 | Adaptateur DevHub : préflight et attributs de migration pour qu'une bascule puisse viser DevHub |
| PAT-326 | #248 | follow DEVHUB-116 in the DevHub adapter |
| PAT-323 | #247 | Admettre DevHub comme quatrième fournisseur V1 du registre et du contrat de tracker (DHN-40) |
| PAT-321 | #246 | refuse DevHub projects outside Foundry governance |
| PAT-315 | #245 | Review remarks deferred from tickets with no origin Epic |
| PAT-320 | #244 | DevHub proof-bound ADR supersession by a named replacement |
| PAT-319 | #243 | Adaptateur DevHub : import sélectif d'issues par source_ref (DHN-34) et import d'ADR avec identifiant étranger (DHN-36) |
| PAT-318 | #242 | Adaptateur DevHub : reçus étendus de DEVHUB-106, passage forcé des critères (DHN-25) et acceptation humaine sans PR (DHN-26) |
| PAT-317 | #241 | record the DevHub hand-over reply and accept the DHN-32 replacement first |
| PAT-314 | #240 | DevHub project identity read-back and staged migration links |
| PAT-316 | #239 | bind DevHub atomic Epic closure children by id, version and state |
| PAT-313 | #238 | Adaptateur DevHub : clôture d'Epic sur enfant abandonné (DHN-24) et réconciliation de création (DHN-37) |
| PAT-312 | #237 | DevHub body replacement and ADR-to-issue relations |
| PAT-309 | #236 | Adaptateur DevHub : lire les reçus de preuve (DHN-38) et les Versions par id (DHN-39) |
| PAT-311 | #235 | Integrate the DevHub decisions of 2026-10-20 and requalify DHN-33 |
| PAT-310 | #234 | Adaptateur Linear : la limite d'usage (USAGE_LIMIT_EXCEEDED) est masquée en graphql_error à effet inconnu |
| PAT-303 | #233 | Intégrer la réponse DevHub du 2026-10-20 et clore DHN-27, DHN-28, DHN-42 |
| PAT-301 | #232 | Review remarks deferred from PAT-295 |
| PAT-300 | #231 | Consigner le premier passage live DevHub et le besoin de création de projet sous comptes locaux |
| PAT-299 | #230 | Review remarks deferred from PAT-295 |
| PAT-297 | #229 | Review remarks deferred from tickets with no origin Epic |
| PAT-296 | #228 | DevHub requirements page and live conformance suite |
| PAT-294 | #227 | try the proof carry in the cadencer when the PR base is unchanged |
| PAT-291 | #226 | Appliquer PAT-ADR-0029 dans le cadenceur : reporter vers Nits les remarques d'un ticket sans Epic |
| PAT-290 | #225 | Review remarks deferred from tickets with no origin Epic |
| PAT-286 | #224 | Review remarks deferred from PAT-269 |
| PAT-288 | #223 | carry a review proof when main integration leaves the PR base unchanged |

The full entries, with their limits, are the `2.3.0` section of the
[changelog](../CHANGELOG.md). This release preparation changes version metadata and
documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`2.2.0` → `2.3.0`) | Updated; stated here and in migration-2.3.0.md. No other manifest key changed |
| Dated Foundry changelog | `2.3.0 — 2026-10-23` section opened from the 34 fragments, which are deleted; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-2.3.0.md |
| `tests/fixtures/release-history.json` | Updated: the `2.3.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, the statement that the tag, the installation and the loaded-version readback are not done here, the Release rule's version list and the migration-guide pointer in the Foundry README, and the version table row of the root README |
| `scripts/ci_plan.py` comment | Updated: its list of release versions includes 2.3.0; no behaviour changes |
| DevHub provider, `cutover` toward DevHub, `issue accept` on DevHub, `Tracker.migration_epic_only_parent`, `_IMPORT_COMMENT_MAX`, `_IMPORT_EPOCH_MAX`, `--help` handling | Not necessary here: documented by their own pull requests (`docs/devhub-tracker.md`, `docs/devhub-requirements.md`, `docs/tracker-contract.md`, `docs/linear-tracker.md`, `docs/qualification/pat-332-cutover-rehearsal.md`, `docs/ci-wait.md`); this release changes none of them |
| `changelog.d/README.md` | Not necessary: unchanged, the fragment convention is already documented there (PAT-210) |
| Gauge, Relay, Blueprint, Witness and Ship-iOS manifests, READMEs and changelogs | Not necessary: this release preparation changes no file under their plugin directories; their versions do not move (the one Ship-iOS script change of PAT-323 predates it) |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes, and no rule of the contract states a Foundry version |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or skill
instruction is changed by this release preparation, and no file under `tooling/`, `hooks/`
or `agents/` is touched by it. Outside release metadata and these pages, the only changes are one
comment line in `scripts/ci_plan.py`, the release paragraphs of `README.md` and
`plugins/foundry/README.md`, the version table row of the root `README.md`, and in
`plugins/foundry/README.md` the migration-guide pointer and the version list of the Release
rule. The mechanical documentation-status gate is not claimed shipped.
