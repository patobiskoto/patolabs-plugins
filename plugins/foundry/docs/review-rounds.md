# Review rounds: baseline, observation window and return triggers (PAT-136)

The rule itself is `AGENTS.md#R9` (identical in `CLAUDE.md`); this page only records how
its effect is measured. It describes no tool behaviour: nothing here is computed by
Foundry, and the counting below is done by hand.

## Baseline

Pull requests 102 to 111 (10 PRs) had 32 review rounds, i.e. 3.2 rounds per PR. 22 of
them came after the first round: 11 after a blocking round and 11 after the correction of
non-blocking remarks.

Source: the coordinator summaries in the PR descriptions and issue comments, and git
history to locate each faulty sentence. Review reports are not kept, so they are not a
source.

## What is counted

- Review rounds per issue, split into: first round, rounds after a blocking round, rounds
  after remarks.
- Plus the rounds of the follow-up issues, counted separately and added to the total, so
  that the deferral does not hide its cost.
- Counted by hand from the per-round lines of the PR descriptions (format in
  `plugins/foundry/skills/merge-pr/SKILL.md`). Foundry's review generation number is not
  used: it was observed to be 4 for 3 reviews on PAT-125 and on PAT-132.

## Observation window

The first 10 issues delivered under the rule, with their follow-up issues. If a follow-up
issue is not delivered when the window closes, the result is called "incomplete" and the
gain stays unknown.

## Return to the previous practice

Previous practice: correct before the merge, then a full re-review. The maintainer
decides, in either of these cases:

- a blocking-level defect is found after the merge in a diff merged under rule (b);
- when the window closes, the total number of rounds (issues plus follow-up issues) per
  issue is not below 3.2.

## Limits

- The deferral moves part of the rounds to the follow-up issue instead of removing them;
  the net gain is unknown until measured.
- Under rule (b), an old defect is only seen if someone comes across it later. Zero
  defects found during the window does not prove zero defects merged.
- Accepted risk: among the 11 full re-reviews done after the correction of remarks, one
  caught an old defect that two full reviews had missed (a statement that two arms cover
  the same 6 tasks, when one arm covered 5). Rule (b) gives up that extra look. That defect
  is of the kind targeted by checklist point 4; nothing shows the checklist would have
  prevented it.
- Presence tests in `plugins/foundry/tests/test_skills.py` and
  `plugins/foundry/tests/test_process_contract.py` prove that the rules are written, not
  that they are followed. Which parts only the reviewer can judge is listed
  in `AGENTS.md#R9`.
- No gate sees the "fix before merge" mark or the existence of the follow-up issue. The
  review proof keeps `quality=mergeable` (`quality` is only `mergeable` or `blocked`:
  `skills/review-pr/SKILL.md`, Output section; `tooling/foundry/evidence_plane.py:233`),
  so the merge command does not refuse a diff because a marked remark was left
  uncorrected or because the follow-up issue does not exist.
