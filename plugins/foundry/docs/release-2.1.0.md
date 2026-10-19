# Foundry 2.1.0 — release note, 2026-10-19

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation or a version loaded by a host: those states come after the merge
and are recorded on the release ticket (`skills/release/SKILL.md`, step 9), not
here. Both host manifests (Claude Code and Codex) declare `2.1.0`. The versionless
marketplace catalogues continue to point at `./plugins/foundry` and `./plugins/ship-ios`,
and also list the plugins named in "Other plugins of the same catalogue" below.

2.1.0 packages what was merged on `main` after the tag `foundry-v2.0.0`: 2 commits,
PR #209 and PR #210, listed [at the end of this page](#merged-issues-in-this-release)
(`git log --oneline foundry-v2.0.0..HEAD` at `abbab67`). It is a minor release:
`release_kind` (`tests/test_release_contract.py`) returns `minor` for a version whose
patch is zero and whose minor is not, and `release_requirements` then asks for this page
and for the migration guide. This page announces **no gain**: no time, cost, quota or
quality improvement is measured by it or claimed. Upgrade and rollback instructions are
in [migration-2.1.0.md](migration-2.1.0.md).

## Behaviour changes and risks

Read this section before upgrading. Each item states what changed and points to the page
or the changelog entry that holds the detail; this page does not restate what a tool
launches, loads or writes.

### 1. Python 3.11 is the declared minimum, checked by the CLI and the hooks (PAT-265)

The minimum is declared once, as `MINIMUM_PYTHON` in `tooling/python_floor.py`. **The
Python minimum is 3.11, not 3.10**, although the title of the PAT-265 pull request says
3.10. What the CLI, the hooks and `doctor` do with an older interpreter is in the
`2.1.0` changelog entry of PAT-265 and in the "Python" paragraph of
[README.md](../README.md) ("Install or upgrade"); this page does not paraphrase them.
Consequences for an upgrade:

- The `python3` that the host resolves for hooks must be 3.11 or later. Under an older
  interpreter the hooks that import `foundry` are inactive, including the `guard_bash`
  R1 deny and the PAT-215 `campaign-approval approve` deny, and every hook call prints
  a visible `systemMessage` instead (README "Python" paragraph). This is the risk of the
  release: a host whose `python3` is older loses those denials until the interpreter is
  fixed. Whether the hooks worked under 3.10 before this change is unknown (changelog
  entry of PAT-265).
- `tooling/foundry_cli.py` exits non-zero under an older interpreter, so a script that
  runs it with a `python3` below 3.11 now fails where it may have run before. Whether it
  ran correctly under 3.10 is unknown.
- Internal failures of the hooks listed in the PAT-265 entry stay fail-open
  (FOUNDRY-ADR-0005) but are no longer silent: a `systemMessage` naming the exception
  type appears on the call. A session that showed nothing before may now show it.
- `doctor` prints a `Hook Python` line and fails below the minimum
  (`skills/doctor/SKILL.md`; `tooling/foundry/doctor.py`). A `doctor` that was green on
  2.0.0 can be red on 2.1.0 for this line alone.
- How Codex displays the hook `systemMessage` has not been observed (README "Python"
  paragraph).

### 2. CI gains a job under the minimum Python (PAT-265)

`.github/workflows/ci.yml` gains the job `foundry-python-min`; the job `foundry` and its
required name are unchanged (changelog entry of PAT-265). This changes the repository's
CI, not the installed plugin.

### 3. Credential masking of campaign summaries (PAT-226)

`campaign_effects.mask_credentials` also masks a JSON-quoted `Authorization` /
`Proxy-Authorization` header; the earlier header pattern is unchanged (changelog entry of
PAT-226, documented in [campaign-effects.md](campaign-effects.md)). The same pull request
settles documentation remarks in [campaign-approval.md](campaign-approval.md),
[campaign-effects.md](campaign-effects.md) and [campaign-cadence.md](campaign-cadence.md).

## What 2.1.0 contains since 2.0.0

The `2.1.0` section of the [changelog](../CHANGELOG.md) holds the 2 entries, ordered by
issue number descending. No CLI verb or option, configuration key, routing default, model
or effort changes.

## Other plugins of the same catalogue

None of the two merged pull requests touches a file under `plugins/gauge`,
`plugins/relay`, `plugins/blueprint`, `plugins/witness` or `plugins/ship-ios`
(`git diff --stat 1083e80..HEAD` at `abbab67`, read when this page was written). Their
versions do not move; their own README and CHANGELOG hold their behaviour, and nothing
here says that any of them is installed on a host. Ship-iOS retains its independent
version, which this release does not change.

## What this release does not claim

- No gain, and no claim that the Python check protects against anything beyond what the
  PAT-265 entry states.
- No tag, installation or loaded version (see "Release phases and evidence").
- No claim that `python3` resolves to 3.11 or later on any given machine: the pages only
  tell how to check it ([migration-2.1.0.md](migration-2.1.0.md)).
- No run of the host commands of `foundry:release`.
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **Rollback to an earlier plugin version is an untried procedure** (PAT-162); see
  [migration-2.1.0.md](migration-2.1.0.md).
- **Gauge alert display on the hosts, sandbox of campaign sessions, campaign cost, Codex
  not wired into campaign effects:** unchanged from the 2.0.0 note
  ([release-2.0.0.md](release-2.0.0.md), "What this release does not claim" and "Known
  limits"); this release does not change them.

## Release phases and evidence

1. **This pull request prepares; it does not publish.** It bumps the two manifests,
   opens the `2.1.0 — 2026-10-19` changelog section from the 2 fragments (which it
   deletes), adds this note and the migration guide, and adds the freeze entry. Merge
   only through `foundry:merge-pr` after independent review on the exact diff and
   exact-head CI on two sources (`AGENTS.md` R1, R4). This page does not supply the
   review or the CI result.
2. **The gated merge is the moment from which a host can receive the version.** The
   catalogues carry no version and no ref, so a host that runs an official update after
   the merge is expected to receive manifests saying `2.1.0`, before any tag exists.
   This is derived and **not observed**.
3. **The tag is the named reference, not the delivery.** `foundry-v2.1.0` is to be placed
   on the exact merged commit, on the maintainer's explicit authorization (STOP of
   `skills/release/SKILL.md`). This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a
   new session; read back the version actually loaded, each host separately. The result
   goes to the release ticket, not to this page.
5. **Freeze:** this page, the migration guide and the `2.1.0` changelog section are
   frozen by digest from the release pull request on. A later correction goes to an
   errata page.

Nothing is declared "tagged" or "installed" by this diff.

## Merged issues in this release

| Issue | PR | Subject |
| --- | --- | --- |
| PAT-265 | #209 | Declare Python 3.11 as the minimum and make internal hook failures visible |
| PAT-226 | #210 | Settle the deferred remarks of PAT-221 |

The full entries, with their limits, are the `2.1.0` section of the
[changelog](../CHANGELOG.md). This release preparation changes version metadata and
documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`2.0.0` → `2.1.0`) | Updated; stated here and in migration-2.1.0.md. No other manifest key changed |
| Dated Foundry changelog | `2.1.0 — 2026-10-19` section opened from the 2 fragments, which are deleted; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-2.1.0.md |
| `tests/fixtures/release-history.json` | Updated: the `2.1.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, the statement that the tag, the installation and the loaded-version readback are not done here, the Release rule's version list in the Foundry README, and the version table row of the root README |
| `scripts/ci_plan.py` comment | Updated: its list of release versions includes 2.1.0; no behaviour changes |
| `MINIMUM_PYTHON`, `Hook Python` line, `hook-python.json`, hook `systemMessage` | Not necessary here: documented by PAT-265 in README "Install or upgrade" and `skills/doctor/SKILL.md`; this release changes none of them |
| `mask_credentials`, exit codes of `campaign-cadence` | Not necessary here: documented by PAT-226 in `docs/campaign-effects.md` and `docs/campaign-cadence.md`; this release changes none of them |
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
