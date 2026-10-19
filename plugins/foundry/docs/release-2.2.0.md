# Foundry 2.2.0 — release note, 2026-10-19

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation or a version loaded by a host: those states come after the merge
and are recorded on the release ticket (`skills/release/SKILL.md`, step 9), not
here. Both host manifests (Claude Code and Codex) declare `2.2.0`. The versionless
marketplace catalogues continue to point at `./plugins/foundry` and `./plugins/ship-ios`,
and also list the plugins named in "Other plugins of the same catalogue" below.

2.2.0 packages what was merged on `main` after the tag `foundry-v2.1.1`: 8 commits,
PR #214 to #221, listed [at the end of this page](#merged-issues-in-this-release)
(`git log --oneline foundry-v2.1.1..HEAD` at `0e52632`). It is a minor release:
`release_kind` (`tests/test_release_contract.py`) returns `minor` for a version whose
patch is zero and whose minor is not, and `release_requirements` then asks for this page
and for the migration guide. This page announces **no gain**: no time, cost, quota or
quality improvement is measured by it or claimed. Upgrade and rollback instructions are
in [migration-2.2.0.md](migration-2.2.0.md).

## Behaviour changes and risks

Read this section before upgrading. Each item states what changed and points to the page
or the changelog entry that holds the detail; this page does not restate what a tool
launches, loads or writes.

### 1. New exit code 3 of `issue merge` (PAT-272)

`EXIT_LOCAL_CLEANUP_FAILED` (`tooling/foundry/issue.py`) is a new exit code of
`issue merge`, distinct from a refused merge (exit 1). A script that tests the exit code
of `issue merge` must handle 3; what it means is in the `2.2.0` changelog entry of
PAT-272 and in `skills/merge-pr/SKILL.md`, step 4.

### 2. `issue openpr` pushes a rebased branch with a lease (PAT-273)

The push rule is in the `2.2.0` changelog entry of PAT-273 (`_push_branch`,
`tooling/foundry/issue.py`) and in `skills/open-pr/SKILL.md`. A rebased branch that was
refused before can now go through; a remote state the clone never fetched is refused.

### 3. New read-only verb `issue wait-ci` (PAT-274)

Syntax, defaults and exit codes `0`, `2` and `3` are in [ci-wait.md](ci-wait.md). The
skills `merge-pr`, `review-pr` and `release` cite it instead of `gh pr checks`. A script
that reads an exit code `3` must not confuse the `issue wait-ci` timeout with the
`issue merge` code of item 1: they are two different verbs.

### 4. Partial review options and the read-only reviewer guard (PAT-283)

`routing read-review --since <SHA>` and `routing prepare-review --previous-head <SHA>` are
new; see the `2.2.0` changelog entry of PAT-283 and [review-packet.md](review-packet.md).
The read-only reviewer guard that accepts `read-review --since` is
`hooks/guard_routed_readonly.py`, shipped by the plugin. **Deduced, not run:** a reviewer
session running under an installed plugin older than 2.2.0 uses the older guard, which
does not accept `--since`, so a partial review there is refused until the plugin is
updated.

### 5. Companion Epic `Nits` for a ticket with no origin Epic (PAT-283, PAT-ADR-0029)

The rule is in `AGENTS.md#R9` (b), [review-rounds.md](review-rounds.md) and
PAT-ADR-0029. The four writes it pre-authorizes can now create the companion Epic named
`Nits` in any repository where the plugin is installed. The campaign cadencer still
corrects such a ticket instead of deferring it ([campaign-effects.md](campaign-effects.md)).

### 6. Linear ADR body: other `*.md` names refused (PAT-284)

A new ADR body holding another `*.md` name outside code is refused before any write; see
[linear-adr-body-guide.md](linear-adr-body-guide.md) and
[linear-tracker.md](linear-tracker.md).

### 7. Smaller changes

- Check-suite statuses and `observed_at` in `ci.json` (PAT-270, [review-packet.md](review-packet.md)).
- Routing and review error messages (PAT-271, [model-routing.md](model-routing.md)).
- `edit <command> --help` (PAT-275, `2.2.0` changelog entry).

## What 2.2.0 contains since 2.1.1

The `2.2.0` section of the [changelog](../CHANGELOG.md) holds the 8 entries, ordered by
issue number descending. No configuration key, routing default, model or effort changes.

## Other plugins of the same catalogue

None of the merged pull requests touches a file under `plugins/gauge`, `plugins/relay`,
`plugins/blueprint`, `plugins/witness` or `plugins/ship-ios`
(`git diff --stat foundry-v2.1.1..HEAD` on these paths, empty at `0e52632`). Their
versions do not move, and nothing here says that any of them is installed on a host.
Ship-iOS retains its independent version, which this release does not change.

## What this release does not claim

- No gain.
- No tag, installation or loaded version (see "Release phases and evidence").
- No run of the host commands of `foundry:release`.
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **Rollback to an earlier plugin version is an untried procedure** (PAT-162); see
  [migration-2.2.0.md](migration-2.2.0.md).
- **Campaign deferral into `Nits`:** not done by the cadencer (item 5).

## Release phases and evidence

1. **This pull request prepares; it does not publish.** It bumps the two manifests,
   opens the `2.2.0 — 2026-10-19` changelog section from the 8 fragments (which it
   deletes), adds this note and the migration guide, and adds the freeze entry. Merge
   only through `foundry:merge-pr` after independent review on the exact diff and
   exact-head CI on two sources (`AGENTS.md` R1, R4). This page does not supply the
   review or the CI result.
2. **The gated merge is the moment from which a host can receive the version.** The
   catalogues carry no version and no ref, so a host that runs an official update after
   the merge is expected to receive manifests saying `2.2.0`, before any tag exists.
   This is derived and **not observed**.
3. **The tag is the named reference, not the delivery.** `foundry-v2.2.0` is to be placed
   on the exact merged commit, on the maintainer's explicit authorization (STOP of
   `skills/release/SKILL.md`). This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a
   new session; read back the version actually loaded, each host separately. The result
   goes to the release ticket, not to this page.
5. **Freeze:** this page, the migration guide and the `2.2.0` changelog section are
   frozen by digest from the release pull request on. A later correction goes to an
   errata page.

Nothing is declared "tagged" or "installed" by this diff.

## Merged issues in this release

| Issue | PR | Subject |
| --- | --- | --- |
| PAT-283 | #221 | Decide a stop criterion for non-blocking remarks of a ticket with no Epic (ADR) |
| PAT-275 | #220 | Fix the minor defects of the 2026-10-19 audit (`create-issue` help, duplicate ADR title, frozen version paths) |
| PAT-274 | #218 | Wait for the CI of a PR with a read-only Foundry command |
| PAT-273 | #219 | Push a rebased branch from `issue openpr` with `--force-with-lease` |
| PAT-272 | #217 | Do not end in error when the merge is done and only the local `git pull` fails |
| PAT-271 | #215 | Show the real cause of `read-review`, `prepare-review` and routing launcher errors |
| PAT-284 | #216 | Linear ADR body: `AGENTS.md#R<n>` reference and refusal of other `*.md` names |
| PAT-270 | #214 | Accept every GitHub check-suite status in `ci_expected` |

The full entries, with their limits, are the `2.2.0` section of the
[changelog](../CHANGELOG.md). This release preparation changes version metadata and
documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`2.1.1` → `2.2.0`) | Updated; stated here and in migration-2.2.0.md. No other manifest key changed |
| Dated Foundry changelog | `2.2.0 — 2026-10-19` section opened from the 8 fragments, which are deleted; empty `Unreleased` above it. The PAT-283 entry sentence on the packet text was corrected before assembly |
| Release note and migration guide | New: this page and migration-2.2.0.md |
| `tests/fixtures/release-history.json` | Updated: the `2.2.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, the statement that the tag, the installation and the loaded-version readback are not done here, the Release rule's version list and the migration-guide pointer in the Foundry README, and the version table row of the root README |
| `scripts/ci_plan.py` comment | Updated: its list of release versions includes 2.2.0; no behaviour changes |
| `issue wait-ci`, `EXIT_LOCAL_CLEANUP_FAILED`, `--force-with-lease` push, `read-review --since`, `prepare-review --previous-head`, `edit <command> --help`, `observed_at`, `_COMPANION_NAME` | Not necessary here: documented by their own pull requests (`docs/ci-wait.md`, `skills/merge-pr/SKILL.md`, `skills/open-pr/SKILL.md`, `docs/review-packet.md`, `docs/review-rounds.md`, `docs/campaign-effects.md`); this release changes none of them |
| `changelog.d/README.md` | Not necessary: unchanged, the fragment convention is already documented there (PAT-210) |
| Gauge, Relay, Blueprint, Witness and Ship-iOS manifests, READMEs and changelogs | Not necessary: no file under their plugin directories is changed by this release preparation; their versions do not move |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes, and no rule of the contract states a Foundry version |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or skill
instruction is changed by this release preparation, and no file under `tooling/`, `hooks/`
or `agents/` is touched. Outside release metadata and these pages, the only changes are one
comment line in `scripts/ci_plan.py`, the release paragraphs of `README.md` and
`plugins/foundry/README.md`, the version table row of the root `README.md`, and in
`plugins/foundry/README.md` the migration-guide pointer and the version list of the Release
rule. The mechanical documentation-status gate is not claimed shipped.
