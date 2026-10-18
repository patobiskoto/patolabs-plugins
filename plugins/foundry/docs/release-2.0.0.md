# Foundry 2.0.0 — release note, 2026-10-18

**This page is written for the release pull request and is frozen from it.** It says
what the version contains and what an upgrade can change. It states nothing about a
tag, an installation or a version loaded by a host: those states come after the merge
and are recorded on the release ticket (`skills/release/SKILL.md`, step 9), not
here. Both host manifests (Claude Code and Codex) declare `2.0.0`. Ship-iOS retains its
independent `0.3.0` version. The versionless marketplace catalogues continue to point
at `./plugins/foundry` and `./plugins/ship-ios`, and also list the plugins named in
"Other plugins of the same catalogue" below.

2.0.0 packages what was merged on `main` after the tag `foundry-v1.5.0`: 26 commits,
PR #182 to #207, listed [at the end of this page](#merged-issues-in-this-release).
**The number 2.0.0 is a milestone chosen by the maintainer on 2026-10-18, not the
consequence of a breaking change.** `release_kind` (`tests/test_release_contract.py`)
returns `major` for a version whose minor and patch are zero, and
`release_requirements` then asks for this page and for the migration guide, as for a
minor. The changelog fragments of this release record no breaking change: no entry
removes a CLI verb, option or configuration key, and none changes a routing default
(read from the 18 fragments when this page was written; the 18 entries of the `2.0.0`
changelog section are those fragments). This page announces **no gain**: no time, cost,
quota or quality improvement is measured by it or claimed. Upgrade and rollback
instructions are in [migration-2.0.0.md](migration-2.0.0.md).

## Behaviour changes and risks

Read this section before upgrading. Each item states what changed and points to the page
or the changelog entry that holds the detail; this page does not restate what a tool
launches, loads or writes.

### 1. Approval of an Epic campaign and the Claude Bash guard (PAT-234)

`campaign-approval approve` is stricter and the `PreToolUse` Bash guard denies a wider
set of command shapes. The exact rules, refusal codes and the deliberate denial of a
plain `grep` of the guarded words are in the changelog entry of PAT-234 and in
[campaign-approval.md](campaign-approval.md) ("Commands" and "An agent cannot approve
(layers and limit)"); `AGENTS.md#R1` states the denial. `hooks/guard_bash.py` is the only
file under `hooks/`, `agents/` and `skills/` that changed since `foundry-v1.5.0`
(`git diff --stat foundry-v1.5.0..HEAD` on those directories at `ec86938`), so a Claude
Code session has to reload components to pick it up.

### 2. The campaign next step, effects, review, correction and deferral (PAT-235 to PAT-238, PAT-242)

Cases where `campaign-cadence next|step|run` now decide differently (reason codes such as
`review_proof_integrity`, `pr_head_not_in_branch`, `description_worktree_head_moved`), the
companion-Epic title rule (`_COMPANION_NAME`) and the retry wait after a quota error are in
the changelog entries of PAT-235, PAT-236, PAT-237, PAT-238 and PAT-242, and in
[campaign-cadence.md](campaign-cadence.md) and [campaign-effects.md](campaign-effects.md).
The optional result key `full_review` is named by the entry of PAT-237. No campaign ran
on a live workspace for this release preparation.

### 3. Small verb changes (PAT-239, PAT-242)

`issue accept <ID> --status` words its replay hint by native state, and `issue close-epic`
with an unknown flag now lists its four flags (changelog entries of PAT-239 and PAT-242;
[linear-tracker.md](linear-tracker.md)).

### 4. New tools, on demand (PAT-184, PAT-198, PAT-245, PAT-244)

- `python3 -m foundry.rollback_native_trial` qualifies the rollback profile lines of PAT-17
  natively, one call per line. Its protocol, the dated matrix and the recorded results
  are in `docs/qualification/pat-198-native-rollback-protocol.md` and
  `docs/qualification/pat-198-native-rollback-2026-10-17.json`; the Codex lines and the
  amendments of PAT-244 are in sections 10 to 12 of the protocol. The values observed are
  those recorded there and in the changelog entries; this page does not restate them.
- `scripts/skill_evals.py` replays five skills on fixed prompts under `evals/`;
  see [skill-evals.md](skill-evals.md) for its billing, judge model and coverage
  statements. Neither tool runs during an upgrade.

### 5. Documentation, tests and code comments only

PAT-240, PAT-241, PAT-246, PAT-248, PAT-251, PAT-252 and PAT-254 (changelog entries).

## What 2.0.0 contains since 1.5.0

The `2.0.0` section of the [changelog](../CHANGELOG.md) holds the 18 entries, ordered by
issue number descending. No configuration key, routing default, model or effort changes.

## Other plugins of the same catalogue

The commits below also merged these plugins. Each has its own version, README and
changelog; this page does not summarise their behaviour beyond the description in their
own manifest, and does not promote them.

| Plugin | Version (manifest) | Manifest description | Pointers |
| --- | --- | --- | --- |
| Gauge | 0.2.1 | Read-only quota gauge for Codex, Claude, Linear and GitHub Actions counters. | [README](../../gauge/README.md), [CHANGELOG](../../gauge/CHANGELOG.md) |
| Relay | 0.2.1 | Writes a verified, secret-free session resume state as a local owner-only file outside git tracking, and reloads it re-verified at session start. | [README](../../relay/README.md), [CHANGELOG](../../relay/CHANGELOG.md) |
| Blueprint | 0.3.1 | Prepares pinned REA/Ghidra tooling and works from a registry of authorised targets; the full description is in its manifest. | [README](../../blueprint/README.md), [CHANGELOG](../../blueprint/CHANGELOG.md) |
| Witness | 0.2.1 | Proves a change of an iOS app in the simulator or of a local web page in a headless browser; the full description is in its manifest. | [README](../../witness/README.md), [CHANGELOG](../../witness/CHANGELOG.md) |
| Ship-iOS | 0.3.0, unchanged | See its manifest and [README](../../ship-ios/README.md). | [CHANGELOG](../../ship-ios/CHANGELOG.md) |

Versions are those of the `.claude-plugin/plugin.json` of each plugin on `main` at
`ec86938`; their `.codex-plugin/plugin.json` carry the same version (read when this
page was written). Nothing here says that any of them is installed on a host.

## What this release does not claim

- No gain, and no claim of safety of a campaign against a same-user process: the
  changelog entries of PAT-215, PAT-217 and PAT-218 (in the `1.5.0` section) state the
  limits.
- No tag, installation or loaded version (see "Release phases and evidence").
- **Gauge alert display on the hosts:** the real display of the Gauge alert in Claude Code
  and in Codex was not observed for this release; it is to be observed after the merge
  (PAT-251, `AGENTS.md#R9`). Unknown until then.
- No run of the host commands of `foundry:release`.
- The mechanical documentation-status gate of FOUNDRY-ADR-0018 (FOUNDRY-123) is not
  shipped.

## Known limits

- **Rollback to an earlier plugin version is an untried procedure** (PAT-162); see
  [migration-2.0.0.md](migration-2.0.0.md).
- **Sandbox of campaign sessions, campaign cost, Codex not wired into campaign effects:**
  unchanged from the 1.5.0 note ([release-1.5.0.md](release-1.5.0.md), "Known limits").
- **Native rollback lines:** the recorded `unknown` values (for example the dated
  snapshot of `claude-scout`) are those of the PAT-198 changelog entry; not restated here.

## Release phases and evidence

1. **This pull request prepares; it does not publish.** It bumps the two manifests,
   opens the `2.0.0 — 2026-10-18` changelog section from the 18 fragments (which it
   deletes), adds this note and the migration guide, and adds the freeze entry. Merge
   only through `foundry:merge-pr` after independent review on the exact diff and
   exact-head CI on two sources (`AGENTS.md` R1, R4). This page does not supply the
   review or the CI result.
2. **The gated merge is the moment from which a host can receive the version.** The
   catalogues carry no version and no ref, so a host that runs an official update after
   the merge is expected to receive manifests saying `2.0.0`, before any tag exists.
   This is derived and **not observed**.
3. **The tag is the named reference, not the delivery.** `foundry-v2.0.0` is to be placed
   on the exact merged commit, on the maintainer's explicit authorization (STOP of
   `skills/release/SKILL.md`). This page does not say that it exists.
4. **Verified installed, after the merge, the tag and an official update:** update
   through each host's official manager, without editing any cache; reload or start a
   new session; read back the version actually loaded, each host separately. The result
   goes to the release ticket, not to this page.
5. **Freeze:** this page, the migration guide and the `2.0.0` changelog section are
   frozen by digest from the release pull request on. A later correction goes to an
   errata page.

Nothing is declared "tagged" or "installed" by this diff.

## Merged issues in this release

Established from `git log --first-parent foundry-v1.5.0..HEAD` on `main` at `ec86938`
(26 commits). Each row is one squash commit, in merge order (oldest first); the issue is
the one named in the subject. Subjects are translated from French. The commits of
Gauge, Relay, Blueprint and Witness are part of "Other plugins of the same catalogue".

| Issue | PR | Subject |
| --- | --- | --- |
| PAT-240 | #182 | Correct the qualification records and the Linear request-cost page |
| PAT-234 | #183 | Settle the deferred remarks of campaign approval and Epic preview |
| PAT-239 | #184 | Settle the deferred remarks of human acceptance and the Linear State return |
| PAT-235 | #185 | Settle the deferred remarks of the next-step computation of a campaign |
| PAT-236 | #186 | Settle the deferred remarks of the campaign effects and the sandbox |
| PAT-237 | #187 | Settle the deferred remarks of review and correction in a campaign |
| PAT-238 | #188 | Settle the deferred remarks of the deferral of non-blocking remarks in a campaign |
| PAT-241 | #189 | Settle the remarks of the PAT-214 body (PAT-209, pinned ADR bodies) |
| PAT-242 | #190 | Correct three defects found while settling PAT-214 |
| PAT-184 | #191 | Replay the Foundry skills at each new model to detect a regression |
| PAT-198 | #192 | Natively qualify the eight rollback profiles of PAT-17 |
| PAT-246 | #193 | Gauge: read the Claude, Codex, Linear and GitHub Actions quotas |
| PAT-245 | #194 | Natively qualify the four Codex rollback lines (follow-up of PAT-198) |
| PAT-247 | #195 | Gauge: warn before an adjustable threshold in Claude Code and Codex |
| PAT-248 | #196 | Relay: write a verified, secret-free resume state |
| PAT-249 | #197 | Relay: reload the resume state at startup, Claude Code and Codex |
| PAT-252 | #198 | Blueprint: prepare the pinned REA/Ghidra tooling and inventory an authorised target |
| PAT-253 | #199 | Blueprint: assemble a sourced functional specification and hand it to Foundry |
| PAT-254 | #200 | Witness: prove an iOS app change in the simulator |
| PAT-255 | #201 | Witness: scripted navigation and proof in the browser |
| PAT-260 | #202 | Blueprint: rebuild specification, measured coverage of an app |
| PAT-262 | #203 | Settle the deferred remarks of PAT-160 |
| PAT-257 | #204 | Settle the deferred remarks of PAT-158 |
| PAT-251 | #205 | Settle the deferred remarks of PAT-157 |
| PAT-259 | #206 | Settle the deferred remarks of PAT-159 |
| PAT-244 | #207 | Settle the deferred remarks of PAT-18 |

The full entries, with their limits, are the `2.0.0` section of the
[changelog](../CHANGELOG.md). This release preparation changes version metadata and
documentation only.

## Documentation status (AGENTS.md#R5)

| Touched public artifact | Status / pointer |
| --- | --- |
| Both Foundry manifest versions (`1.5.0` → `2.0.0`) | Updated; stated here and in migration-2.0.0.md. No other manifest key changed |
| Dated Foundry changelog | `2.0.0 — 2026-10-18` section opened from the 18 fragments, which are deleted; empty `Unreleased` above it |
| Release note and migration guide | New: this page and migration-2.0.0.md |
| `tests/fixtures/release-history.json` | Updated: the `2.0.0` entry, with the digests of the changelog section and of the two pages |
| Root and Foundry README | Updated: pointers to these two pages, the statement that the tag, the installation and the loaded-version readback are not done here, the Release rule's version list in the Foundry README, and the version table row of the root README |
| `scripts/ci_plan.py` comment | Updated: its list of release versions includes 2.0.0; no behaviour changes |
| `changelog.d/README.md` | Not necessary: unchanged, the fragment convention is already documented there (PAT-210) |
| Gauge, Relay, Blueprint, Witness and Ship-iOS manifests, READMEs and changelogs | Not necessary: no file under `plugins/gauge`, `plugins/relay`, `plugins/blueprint`, `plugins/witness` or `plugins/ship-ios` is changed by this release preparation; their versions do not move |
| `AGENTS.md` / `CLAUDE.md` | Not necessary: no numbered rule changes, and no rule of the contract states a Foundry version |
| `docs/campaign-*.md`, `docs/linear-tracker.md`, `docs/skill-evals.md`, qualification pages | Not necessary: each already documents, since its own issue, the surface this page points to; this release changes none of them |

No CLI verb or option, configuration key, runtime constant, hook, agent profile or skill
instruction is changed by this release preparation, and no file under `tooling/`, `hooks/`
or `agents/` is touched. Outside release metadata and these pages, the only changes are one
comment line in `scripts/ci_plan.py`, the release paragraphs of `README.md` and
`plugins/foundry/README.md`, the version table row of the root `README.md`, and in
`plugins/foundry/README.md` the migration-guide pointer and the version list of the Release
rule. The mechanical documentation-status gate is not claimed shipped.
