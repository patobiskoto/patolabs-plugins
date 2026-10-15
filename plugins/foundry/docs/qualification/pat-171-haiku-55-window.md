# Haiku 5.5 observation window: measurement (PAT-171)

Measurement prescribed by PAT-ADR-0016, section "Observation de production et retour
arrière", as restated in
[`pat-125-haiku-55-promotion.md`](pat-125-haiku-55-promotion.md) ("Observation de
production et retour arrière"). This page reports the numbers, whether the regression
trigger fires, and the decision below.

Decision (2026-10-15): keep `haiku-5.5` / `medium` as the Claude `economy` default; the window
is closed. Taken by the coordinator under the maintainer's delegation of 2026-10-14; the
maintainer may reverse it at any time without justification (PAT-ADR-0016). Grounds: the
regression trigger does not fire under any reading (difference at most +1, threshold +2). The
immediate trigger is unknown, not absent: no launch evidence was recorded in this window. No
gain in quality, cost or quota is claimed. No code or mapping changes.

The delegation of 2026-10-14 has no trace in this repository: it is recorded only in the
coordinator's local session memory, outside the repository, and nothing here quotes the
maintainer's words. Consulting the tracker for a comment that records it was not done
(unknown whether one exists).

## Window and reference

- Promotion merge: commit `72ee294`, PR #105 (PAT-125), 2026-10-09 08:28:09 +0200
  (`git log --first-parent origin/main`, read on 2026-10-15).
- Delivered issue: a pull request merged into the default branch whose first-parent
  commit is titled `[PAT-n] ... (#N)`. Window: the ten first such commits after `72ee294`.
  Reference: the ten last before it. Every first-parent commit in both ranges carries a
  `[PAT-n]` title, so nothing was skipped (checked on 2026-10-15: `git log --first-parent
  72ee294..f5dffa6` lists exactly ten commits and the ten before `72ee294` all carry the
  title), and the ten PRs of each set belong to ten distinct issues. PR #115 is not in the
  window: it is the trial pull request of PAT-143, closed without merge
  (`gh pr view 115 --json state,mergedAt`: `CLOSED`, `mergedAt` null), so it has no
  first-parent commit; PR #116 is the one merged for PAT-143.
- Window end: the tenth issue (PAT-143, PR #116) merged on 2026-10-11 02:31, well before
  the thirty-day bound (2026-11-08); the ten-issue term was reached first.
- Host: deduced `Claude` for all 20 issues. Source: every squashed commit message of the
  20 merge commits carries `Co-Authored-By: Claude ...` trailers, and none names Codex
  (`git show -s --format=%B <sha>`); the only Codex words in the PR descriptions (#111, #114)
  are the Codex plugin manifests, not an author. The GitHub author field is the same account for all 20
  and does not tell the host. A trailer shows who wrote commits, not which host ran the
  review or the exploration, so this stays `deduced`. No issue is `unknown`, so the counts
  with and without unknown-host issues are the same (10 and 10).

## Sources and definitions

- Reference and window membership: the ADR reads the reference "in the acceptance proofs and
  the tracker"; this page does not read the tracker. The delivered issues are identified from
  the first-parent titles of the default branch and the signals from the local proof and
  escalation stores. Whether a tracker read would change the two sets is not checked here
  (unknown).
- Proof store: `python3 plugins/foundry/tooling/foundry_cli.py routing review-rounds <ISSUE> --json`
  (fields `rounds`, `blocked_rounds`, `per_round[0].validated`, `unreadable_proofs`; PAT-175)
  and `routing escalation show <ISSUE>` (fields `total_escalations`, `resume_count`), both
  read-only, local data directory. All 20 issues returned a proof (`unreadable_proofs` 0,
  `carried_proofs` 0).
- Round 1 `blocking`: the first round is not `validated` (`validated` = every criterion
  `pass` and quality `mergeable`), so an acceptance block counts like a quality block. PAT-143
  is such a case (quality `mergeable`, acceptance blocked), matching its PR description
  (#116, "Round 1 ... acceptance blocked on one point").
- CI on head: `gh pr view <N> --json statusCheckRollup,headRefOid,commits`. In all 20 PRs
  the head sha equals the last commit of the PR. The rollup merges check-runs and legacy
  statuses; the 20 PRs have at least one `success` and no failing check. In PR #116 the
  `foundry` job is `skipped` by the selective CI of PAT-142; its `plan`, `ship-ios` and
  `catalogue` checks succeeded. That the merge gate's own two-source read (FOUNDRY-ADR-0002)
  agreed is not re-run here. The ADR words the regression signal as a red CI "on the
  delivered SHA"; this page reads the CI of the PR head, the sha the merge pins and verifies
  (`AGENTS.md#R4`, FOUNDRY-ADR-0002), not a check on the squash commit created on the
  default branch, which was not read.
- Resumptions: `resume_count` of the escalation state; correction cycles: `blocked_rounds`.
  Escalations: `total_escalations` (a `deterministic_failures` count without escalation is
  not counted).
- Cross-check against the PR descriptions (round lines read with `gh pr view <N> --json body`):
  the first-round verdict of the 13 issues whose description has a round 1 line (PAT-127,
  PAT-128, PAT-129 and the ten of the window) matches the proof. For PAT-132, PAT-133,
  PAT-135 and PAT-143 the description names one round fewer than the proof (2 against 3,
  1 against 2, 5 against 6, 3 against 4); the proof is used, the cause is unknown. The
  descriptions of the PRs #95 to #101 (PAT-117, PAT-120 to PAT-124, PAT-126) carry no round
  line, so for those seven issues the proof is the only source.

## Reference (ten issues before the promotion, newest first)

| Issue | PR | Merge sha | Merged (local) | Head sha | CI on head | Round 1 | Rounds / blocked | Resumptions | Escalations |
|---|---|---|---|---|---|---|---|---|---|
| PAT-129 | #104 | `b6a86de` | 2026-10-09 03:33 | `dc6ad6651` | foundry success, ship-ios success, catalogue success | blocking | 4 / 2 | 0 | 1 |
| PAT-128 | #103 | `a961868` | 2026-10-09 01:49 | `d745491d3` | foundry success, ship-ios success, catalogue success | validated | 2 / 0 | 0 | 0 |
| PAT-127 | #102 | `964288a` | 2026-10-08 23:28 | `dc4f5bd6d` | foundry success, ship-ios success, catalogue success | blocking | 3 / 2 | 0 | 1 |
| PAT-126 | #101 | `d173e17` | 2026-10-08 19:03 | `fcf482e88` | foundry success, ship-ios success, catalogue success | blocking | 5 / 3 | 0 | 2 |
| PAT-124 | #100 | `130fa05` | 2026-10-08 10:23 | `a49258200` | foundry success, ship-ios success, catalogue success | blocking | 4 / 2 | 0 | 1 |
| PAT-123 | #99 | `90eeb0e` | 2026-10-08 07:58 | `32f324cf9` | foundry success, ship-ios success, catalogue success | blocking | 4 / 3 | 0 | 2 |
| PAT-122 | #98 | `fd6e00f` | 2026-10-07 21:44 | `4cff8553c` | foundry success, ship-ios success, catalogue success | validated | 2 / 0 | 0 | 0 |
| PAT-121 | #97 | `4adeb2f` | 2026-10-07 16:09 | `0f18e8313` | foundry success, ship-ios success, catalogue success | validated | 5 / 0 | 0 | 0 |
| PAT-120 | #96 | `2085be6` | 2026-10-07 13:24 | `9509e7750` | foundry success, ship-ios success, catalogue success | validated | 1 / 0 | 0 | 0 |
| PAT-117 | #95 | `9bb9e7f` | 2026-10-07 12:20 | `fccba3fc7` | foundry success, ship-ios success, catalogue success | blocking | 3 / 2 | 0 | 1 |

## Window (ten issues after the promotion, oldest first)

| Issue | PR | Merge sha | Merged (local) | Head sha | CI on head | Round 1 | Rounds / blocked | Resumptions | Escalations |
|---|---|---|---|---|---|---|---|---|---|
| PAT-130 | #106 | `ff9a53a` | 2026-10-09 09:25 | `a65599d02` | foundry success, ship-ios success, catalogue success | blocking | 3 / 1 | 0 | 0 |
| PAT-131 | #107 | `1f1121a` | 2026-10-09 16:29 | `e1bba76ab` | foundry success, ship-ios success, catalogue success | validated | 3 / 0 | 0 | 1 |
| PAT-132 | #108 | `e599f79` | 2026-10-10 00:00 | `a363ec29c` | foundry success, ship-ios success, catalogue success | validated | 3 / 0 | 0 | 0 |
| PAT-133 | #109 | `6e16acc` | 2026-10-10 07:01 | `d1b1aca2c` | foundry success, ship-ios success, catalogue success | validated | 2 / 0 | 0 | 0 |
| PAT-134 | #110 | `63df165` | 2026-10-10 09:53 | `351d553d4` | foundry success, ship-ios success, catalogue success | blocking | 3 / 2 | 0 | 1 |
| PAT-135 | #111 | `04af359` | 2026-10-10 12:42 | `3cac7bee5` | foundry success, ship-ios success, catalogue success | blocking | 6 / 4 | 0 | 2 |
| PAT-136 | #112 | `2b6270a` | 2026-10-10 18:53 | `936bd1fd1` | foundry success, ship-ios success, catalogue success | validated | 3 / 0 | 0 | 0 |
| PAT-139 | #113 | `0814db2` | 2026-10-10 23:11 | `0b53fa221` | foundry success, ship-ios success, catalogue success | validated | 3 / 0 | 0 | 1 |
| PAT-142 | #114 | `fc30d09` | 2026-10-11 02:14 | `acb9b8619` | plan success, ship-ios success, catalogue success, foundry success | blocking | 4 / 1 | 0 | 0 |
| PAT-143 | #116 | `f5dffa6` | 2026-10-11 02:31 | `14336fcf9` | plan success, ship-ios success, catalogue success, foundry skipped | blocking | 4 / 1 | 0 | 0 |

## Totals

| Signal | Reference | Window | Window minus reference |
|---|---|---|---|
| Issues with a blocking round 1 | 6 | 5 | -1 |
| Issues with at least one escalation | 6 | 4 | -2 |
| Issues with a red CI on the delivered sha | 0 | 0 | 0 |
| Issues with at least one of the three | 6 | 7 | +1 |
| Sum of the three counts | 12 | 9 | -3 |
| Rounds in total | 33 | 34 | +1 |
| Blocked rounds (correction cycles) in total | 14 | 9 | -5 |
| Escalations in total | 8 | 5 | -3 |
| Resumptions in total | 0 | 0 | 0 |

Issues with at least one of the three: reference PAT-117, PAT-123, PAT-124, PAT-126,
PAT-127, PAT-129; window PAT-130, PAT-131, PAT-134, PAT-135, PAT-139, PAT-142, PAT-143.

## Trigger evaluation

- Regression trigger (PAT-ADR-0016: over the window, the number of issues that had a
  blocking review at first pass, an escalation, or a red CI on the delivered sha exceeds
  the reference's by at least two). The rule can be read as a count of issues with any of
  the three signals, or per signal. Window minus reference is +1 at most (7 against 6 issues
  with any signal) and -1, -2, 0 per signal, so the difference is below +2 under each
  reading: the regression trigger does not fire.
- Immediate trigger (a `haiku-5.5` profile unavailable or divergent, an observed executed
  model different from `claude-haiku-5-5`, an observed transmitted effort different from
  `medium`, on a host at the required version): `unknown`. No routing hook output, PR text
  or issue text read for this page records a `haiku-5.5` launch, its executed model, its
  transmitted effort or a host version in this window; session transcripts were not read.
  `unknown` is not "no trigger".

## Unknowns and limits

- Whether `haiku-5.5` served any exploration in these 10 issues is unknown: the signals
  above are downstream of exploration and not causally attributable (PAT-ADR-0016), and
  nothing read here ties a signal to the model.
- The two sets differ in kind (the reference is mostly PAT-19 campaign work, the window mostly
  documentation, measurement and process issues); no comparison of difficulty was made.
- Cost, quota and quality of exploration are not measured by this window (PAT-ADR-0016
  signals do not include cost).
- Host of the review and the exploration per issue: not observed (see Host above).
- Proof counts versus PR descriptions: see Sources and definitions.
- The data directory read is the local one; a proof recorded elsewhere would be missing, and
  the count would then read as `unknown`, not zero. None was missing here.
