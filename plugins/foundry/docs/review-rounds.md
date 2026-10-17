# Review rounds: baseline, observation window and return triggers (PAT-136, PAT-139)

The rule itself is `AGENTS.md#R9` (identical in `CLAUDE.md`); this page records how
its effect is measured. Since PAT-175 the per-issue count of rounds is computed by
`routing review-rounds` (section "Per-issue report"); the baseline and window tables below
are still counted by hand (see "What is counted"). The last point of "Limits" and the last two sections (PAT-139)
cite the code that bounds rule (b): what the merge command does not refuse, what no tool
checks, how an Epic campaign carries the remarks, and what each tracker provider carries. Paths are relative
to `plugins/foundry/`, except `AGENTS.md` and `CLAUDE.md` at the repository root and
Markdown link targets, which are relative to this page.

## Baseline

Pull requests 102 to 111 (10 PRs) had 32 review rounds, i.e. 3.2 rounds per PR. 22 of
them came after the first round: 11 after a blocking round and 11 after the correction of
non-blocking remarks. These ten PRs are ten distinct issues (PAT-127, PAT-128, PAT-129,
PAT-125, PAT-130, PAT-131, PAT-132, PAT-133, PAT-134, PAT-135; checked with `gh pr view`
on 2026-10-11), so 3.2 rounds per PR is also 3.2 rounds per issue.

Source: the coordinator summaries in the PR descriptions and issue comments, and git
history to locate each faulty sentence. Review reports are not kept, so they are not a
source.

## What is counted

- Review rounds per issue, split into: first round, rounds after a blocking round, rounds
  after remarks.
- Plus the rounds of the follow-up issues, counted separately and added to the total, so
  that the deferral does not hide its cost. Since PAT-139 these follow-up issues are the
  issues of the companion Epic (`AGENTS.md#R9` (b)); their own non-blocking remarks are
  corrected before the merge, so they open no further follow-up issue.
- Counted, for the tables below, by hand from the per-round lines of the PR descriptions
  (format in `skills/merge-pr/SKILL.md`); `routing review-rounds <ISSUE-ID>` gives the total
  round count and the `validated_rounds` / `blocked_rounds` counts, and only for reviews recorded in the data directory and repository
  namespace it reads (see "Per-issue report"); it does not compute the first / after blocking /
  after remarks split nor the deferred remarks. Foundry's review generation number is not
  used: it was observed to be 4 for 3 reviews on PAT-125 and on PAT-132.

## Per-issue report

`python3 tooling/foundry_cli.py routing review-rounds <ISSUE-ID> [--session <main-log.jsonl>]...
[--codex-session <rollout.jsonl>]... [--repository R] [--root DIR] [--json]` (code:
`tooling/foundry/review_rounds.py`, verb in `routing.py`) writes nothing and calls no tracker.

- Rounds and outcome: the terminal proofs of the issue in `AcceptanceProofStore`, one round
  per non-carried proof; carried proofs (`carry-review-proof`) are counted apart and are not
  rounds. `outcome` is `validated` when the last round is `mergeable` with every criterion
  `pass`, `blocked` otherwise, `unknown` with no proof. `validated_rounds` counts the rounds
  that are `mergeable` with every criterion `pass`; `blocked_rounds` is every other round
  (`rounds - validated_rounds`), including a `fail` criterion under `mergeable`.
  Scope: one data directory (`FOUNDRY_DATA` or `~/.config/foundry`, `registry.data_dir()`) and
  one repository namespace (`--repository`). A review recorded on another machine, under
  another `FOUNDRY_DATA` or namespace is not seen; with no proof of the issue there, `rounds`,
  `validated_rounds` and `blocked_rounds` are `null` (unknown, never 0) and the text says
  `unknown: no non-carried review proof in <data dir> for <repository>`; the same holds when
  the issue has only carried proofs (`carried_proofs` is then above 0 and `rounds` is `null`).
  Rounds are ordered by the ledger `completed_at` of `ReviewDeduplicator`; a round with no
  readable `completed_at` is ordered last and can therefore decide `outcome` (edge case, not
  handled). The issue identifier is compared exactly, case included: `pat-175` finds no
  proof of `PAT-175` and the text gives no hint. The store holds proofs, not pull request
  states: the proofs of a pull request closed without merge for the same issue count as
  rounds.
- Output keys (`report()` in `tooling/foundry/review_rounds.py`): `issue`, `repository`,
  `data_dir`; `rounds`, `validated_rounds`, `blocked_rounds`, `carried_proofs`,
  `unreadable_proofs`, `outcome`; `duration_seconds`, `duration_rounds_timed`; `per_round`,
  one entry per round with `round`, `generation`, `quality`, `all_pass`, `validated`,
  `criteria`, `started_at`, `completed_at`; `tokens` with `coordinator`, `subagents`,
  `codex_unclassified`, `unreadable_logs` and `scope` (the label of what the totals cover).
  `unreadable_proofs` counts the proof files of the whole repository namespace that cannot
  be read, whatever their issue (the issue of an unreadable file is unknown), so a non-zero
  value does not say that a round of this issue is missing.
- Duration: earliest ledger `claim.started_at` to latest `completed_at` among the rounds
  that have both, `null` (unknown) when none does; `duration_rounds_timed` says how many.
  A claim recovered after an abandoned one reports the latest claim's start (deduced from
  the ledger record shape), so the duration can be shorter than the elapsed time.
- Tokens: only from logs passed with `--session` / `--codex-session`, read by
  `cost_attribution.read_host_log` (outside the invocation path, FOUNDRY-ADR-0015). Per class:
  input, cache_read, cache_write, output. They are the full totals of the logs passed (the
  session's sub-agents included, every issue worked in it), not filtered by issue or round
  window; the text labels them "all logs passed, not filtered by issue or round". A Claude log is the coordinator; the `*.jsonl`
  under `<dir>/<session>/subagents/` are the sub-agents (by position only, as in
  `cache_ttl_replay.py`). A Codex rollout is reported as `codex_unclassified`: the
  coordinator/sub-agent split is not implemented in `review_rounds.py`; whether a rollout
  carries enough to make it is unknown. A class with no readable log is `null`
  (unknown), never 0; sub-agent logs not found under that path are not counted, so a
  sub-agent total is a lower bound. `unreadable_logs` is one count over all classes: a class
  that has some unreadable logs is a lower bound too. Whether an older Claude host wrote
  sub-agent records into the main log, which would then be counted as coordinator, is
  unknown.
- Output holds counts only: no claim or proof identifier, prompt or log content.

## Observation window

The first 10 issues delivered under the rule, with their follow-up issues. If a follow-up
issue is not delivered when the window closes, the result is called "incomplete" and the
gain stays unknown.

### First window: result (PAT-169)

Which ten issues. This page does not date the start of the rule; this reading is
deduced: the rule starts at the merge of PAT-136 (PR 112, 2026-10-10), the first
pull request that carries it. The ten origin issues merged from then on, in merge
order, are PAT-136, PAT-139, PAT-142, PAT-143, PAT-145, PAT-137, PAT-147, PAT-144,
PAT-146 and PAT-149. Their follow-up issues are PAT-138, PAT-148 and PAT-150.

Result: complete in the sense of the paragraph above, because the three follow-up
issues were delivered when the window was counted on 2026-10-11 (merged in PRs 118, 123
and 125; PR 125 was merged after the tenth origin issue, see the closing rule under
"Second window"). One count is a lower bound (PAT-143, below).

Rounds per issue, observed in the PR descriptions (read with `gh pr view` on
2026-10-11; "line" is a line in the format of `skills/merge-pr/SKILL.md`, "prose" a
free-text `Round N` line of the description, written before that format was used):

| Issue | PR | Rounds | First | After blocking | After remarks | Kind of source |
|---|---|---|---|---|---|---|
| PAT-136 | 112 | 3 | 1 | 0 | 2 | line |
| PAT-139 | 113 | 3 | 1 | 0 | 2 | line |
| PAT-142 | 114 | 4 | not split (prose) | not split (prose) | not split (prose) | prose |
| PAT-143 | 116 | 3 (lower bound) | not split (prose) | not split (prose) | not split (prose) | prose |
| PAT-145 | 119 | 6 | 1 | 0 | 5 | line |
| PAT-137 | 117 | 2 | 1 | 1 | 0 | line |
| PAT-147 | 120 | 3 | 1 | 0 | 2 | line |
| PAT-144 | 121 | 4 | 1 | 0 | 3 | line |
| PAT-146 | 122 | 1 | 1 | 0 | 0 | line |
| PAT-149 | 124 | 2 | 1 | 1 | 0 | line |
| PAT-138 (follow-up) | 118 | 4 | 1 | 1 | 2 | line |
| PAT-148 (follow-up) | 123 | 2 | 1 | 0 | 1 | line |
| PAT-150 (follow-up) | 125 | 2 | 1 | 0 | 1 | line |
| Eleven PRs with lines | | 32 | 11 | 3 | 18 | line |

The split is read from "follows first|blocking|remarks" in the lines of the eleven PRs
that carry them (PRs 112, 113, 117 to 125; the sum 11 + 3 + 18 = 32 equals the
24 + 8 rounds of the second bullet below). On PR 120, round 3 is marked "follows
remarks" although the PR itself says it followed a merge of `origin/main`, not
remarks. PRs 114 and 116 have no line in the format: their split is not given.

- Origin issues: 31 rounds. Follow-up issues: 8 rounds. Total 39 rounds over 10 origin
  issues, i.e. 3.9 rounds per origin issue (arithmetic of the table).
- Counting only the eight origin issues that carry lines in the format (PAT-142 and
  PAT-143 left out): 24 + 8 = 32 rounds over 8 origin issues, i.e. 4.0 (all 8 follow-up
  rounds are charged to these eight; which origin issues PAT-138 serves is not stated
  here). This is the same computation as the coordinator's first whole-window figure,
  which left PAT-142 and PAT-143 out because their rounds were believed unknown: the two
  "4.0" of the decision below are one figure.
- Unknown: the rounds of PAT-143 in PR 115, a trial pull request closed without merge
  that carries no round line. The 3 counted for PAT-143 are those of PR 116, the merged
  one, so the true figure is at least 3. A higher figure can only raise the average,
  which is already above 3.2.
- Both figures are not below 3.2, so the return trigger on the number of rounds (the
  second of the two below) is met on the whole window.
- A round is not a cost. One partial review took about 30 seconds and one full review
  about 3 minutes, one observation each, not a measure; the cost of a round was not
  measured in this window.
- Of these issues, the ones merged after PAT-144 (PR 121, 2026-10-11 08:54 UTC+2, the
  amendment of the rule: a single follow-up issue per companion Epic, and a partial
  review after non-blocking remarks on an issue of a companion Epic) are PAT-146 (1
  round), PAT-149 (2) and the follow-up issues PAT-148 (2) and PAT-150 (2): 7 rounds
  over 2 origin issues, i.e. 3.5. PAT-137 and PAT-147 were merged before PAT-144, so a
  figure of 3.0 (12 rounds over 4 origin issues) that groups them with the later issues
  mixes both states of the rule. Two issues are too few to conclude anything. PAT-148
  also holds the remarks deferred from PAT-137 and PAT-147 (CHANGELOG, entry PAT-148),
  merged before PAT-144; its 2 rounds are counted here in full against 2 origin issues.
  Without PAT-148 the figure is 5 rounds over 2 origin issues, i.e. 2.5: on which side
  of 3.2 this subset falls depends on that attribution. 2.5 is a lower bound and 3.5 an
  upper bound, since PAT-148 also holds remarks of PAT-146.

Decision of the maintainer (2026-10-11), in two steps the same day:

- Decided first, on the coordinator's figures 4.0 (whole window) and 3.0 (PAT-137,
  PAT-147, PAT-146 and PAT-149 with PAT-148 and PAT-150). The 3.0 wrongly included
  PAT-137 and PAT-147, merged before the amendment of PAT-144.
- Observed afterwards: the count was corrected to 3.9 (whole window, ten origin issues)
  and 3.5 (the two origin issues merged after PAT-144, too few to conclude), against the
  3.2 baseline. The reservation on the attribution of PAT-148 (2.5 without it) was
  written after the confirmation below, which was given on "3.5 against 3.2"; the
  coordinator reported it to the maintainer afterwards (2026-10-11). No answer of the
  maintainer on it is recorded here.
- Decided, confirmed by the maintainer on the corrected figures the same day: keep the
  rule as amended by PAT-144 and open a second window, defined below. The 3.2 threshold
  and the return triggers are not changed; they apply as they are to the second window.
  The first window therefore did not lead to the return to the previous practice.

### Second window

Start: the merge of PAT-144 (PR 121); PAT-144 itself is not in it. Content: the first 10
origin issues merged after it, with their follow-up issues, under the rule as amended by
PAT-144. Same count (the section "What is counted"), same result vocabulary (complete or
incomplete), same return triggers and same threshold of 3.2.

An issue belongs to the window by its merge instant; all its rounds are counted,
including those run before the merge of PAT-144. So PAT-146 and PAT-149, with PAT-148
and PAT-150, belong to both windows and their rounds are counted in each.

Coordinator's reading, to be confirmed by the maintainer: an origin issue is any merged
issue that is not an issue of a companion Epic, whether or not it has an origin Epic
(first window: PAT-138 is a follow-up issue although companion Epics did not exist yet).
The list of origin issues is fixed at the merge of the tenth; the window closes, and the
result is called complete or incomplete, when the count is made for the maintainer's
decision, at a date the maintainer chooses after that merge (first window: counted by
the coordinator and decided by the maintainer on 2026-10-11); the maintainer then
decides (section "Return to the previous practice" below).

State on `origin/main` at `a75e4f7` (2026-10-11 18:13 UTC+2), from
`git log --first-parent`. It is a state at that commit, not kept up to date by later
pull requests:

- origin issues merged so far, 9 of 10, in merge order: PAT-146, PAT-149, PAT-163,
  PAT-164, PAT-167, PAT-168, PAT-190, PAT-169 (PR 133) and PAT-170 (PR 134);
- follow-up issues delivered: PAT-148, PAT-150, PAT-187 (it settles the remarks
  deferred by PAT-163 and PAT-164) and PAT-189 (PR 132, merged the same day after
  PAT-190; it settles the remarks deferred by PAT-167, PAT-168 and PAT-190);
- PAT-192 is a follow-up issue, not an origin issue: it holds the remarks deferred by
  PAT-169 and PAT-170, and it is not delivered as long as its pull request is not
  merged;
- PAT-148 will be counted in full although it also holds remarks of PAT-137 and
  PAT-147, merged before the start.

The rounds of the second window are not counted on this page yet; they are counted
after the merge of the tenth origin issue, for that decision.

## Return to the previous practice

Previous practice: correct before the merge, then a full re-review. The maintainer
decides, in either of these cases:

- a blocking-level defect is found after the merge in a diff merged under rule (b);
- when the window closes, the total number of rounds (issues plus follow-up issues)
  divided by the number of origin issues is not below 3.2, the figure of the baseline
  (maintainer decision of 2026-10-11).

## Partial review on every pull request (PAT-172, PAT-ADR-0022)

Until PAT-172, only the PR of an issue of a companion Epic had a partial review after the
correction of non-blocking remarks (maintainer decision of 2026-10-11; PR 121, which
merged that amendment, is cited in the observation window above); every other PR had a full re-review. The
history above and in "Return to the previous practice" is kept as measured under that
rule. Now, on every pull request, the review that follows the correction of the
non-blocking remarks of a validated round covers what changed since that round, not the
whole diff. A validated round is one where every criterion is `pass` and quality is
`mergeable`, even with remarks marked "fix before merge". The review stays full after a
blocking round, when the change since the previous round goes beyond correcting the
remarks it carried, and when the reviewer asks for it with a reason. The packet gives the
reviewer the previous round's verdict and remarks, the commit range since that round, and
the full diff through the shared verifier; the reviewer checks that each correction
settles its remark without a new defect and that nothing else changed, and returns a
verdict on all criteria for the exact diff. The proof has the same form as a full one,
relies on the previous round for the unchanged parts, and the PR description says so. No
mechanical check distinguishes a partial from a full review; the merge gate and the proof
format are unchanged. The rule is in `AGENTS.md#R9` (b), `skills/merge-pr/SKILL.md` and
`skills/review-pr/SKILL.md`. The partial and full review times quoted above are one
observation each; the effect of this extension on the number of rounds is unknown and is
not counted on this page. The only figure on what giving up the extra full look costs is
the one in "Limits" (one of 11 full re-reviews after the correction of remarks caught an
old defect); it comes from that section, written before this extension, and is not
re-counted for it. The commit range and the previous round's remarks that the packet
carries are declarations of the coordinator, not checked against the bytes (see
`skills/review-pr/SKILL.md`).

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
- Presence tests in `tests/test_skills.py` and `tests/test_process_contract.py` prove
  that the rules are written, not that they are followed. Which parts only the reviewer
  can judge is listed in `AGENTS.md#R9`.
- No gate sees the "fix before merge" mark or the existence of the follow-up issue. The
  review proof keeps `quality=mergeable` (`quality` is only `mergeable` or `blocked`:
  `skills/review-pr/SKILL.md`, Output section; `_review_evidence` in
  `tooling/foundry/evidence_plane.py`),
  so the merge command does not refuse a diff because a marked remark was left
  uncorrected or because the follow-up issue does not exist.

## What is not mechanical and how a campaign carries the remarks (PAT-139, PAT-141)

Rule (b) of `AGENTS.md#R9` sends the deferred remarks of an Epic to a companion Epic and
authorizes four tracker writes in advance (PAT-ADR-0018). This section lists what the
tools do not check and do not do.

Not mechanical:

- No gate checks that the companion Epic exists, that it has no tracker link with the
  origin Epic (no parent, no dependency, no relation), or that the coordinator rightly
  classed a remark as deferrable. The reviewer judges through its "fix before merge"
  mark; the coordinator then applies the two other cases (a page frozen after
  publication, a missing or generic documentation status), and nobody re-reads that
  second step before the merge: an accepted risk (maintainer decision of 2026-10-11).
  In an Epic campaign the reviewer contract (`review_contract`, `campaign_review.py`) asks
  the reviewer to mark those two cases "fix before merge", so the correction loop takes
  them; nothing checks that it did. The rest is judged in review.
- No gate checks that a PR of a companion Epic deferred nothing (no second-level
  deferral), that the fourth write was made when needed, that the companion Epic has a
  single follow-up issue, or that the review after a correction was limited to what
  changed. The merge gate is not modified: when the criteria of the issue are not all
  checked in the tracker, and also when they are all checked on a tracker whose adapter
  sets `delivery_receipt_supported` if the issue has a binding (`write.issue_binding`),
  it asks for a review proof of the exact diff (`valid_for_merge`, called twice by
  `merge` in `tooling/foundry/issue.py`), and after
  such a partial review that proof relies on the previous round for the unchanged parts.
- Nothing checks, outside the cadencer, that only one companion Epic carries a given
  name. The name and a mention in text are the only relation between the two Epics. In a
  campaign, the `defer-remarks` effect refuses a second Epic of the same name as a
  durable refusal only when `_find_companion` lists the project, not when it keeps a
  companion that `journal_memory` names (`tooling/foundry/campaign_defer.py`, PAT-228), and that
  file is the only one under `tooling/` or `hooks/` that contains the word `Nits`.
- No check proves that the cadencer classes a failure rightly as durable or transient: it
  reads the text of the error (`_DURABLE` in `tooling/foundry/campaign_defer.py`) and
  treats any other error as transient, bounded by `MAX_DEFER_TRIES`.

In an Epic campaign (PAT-141, PAT-ADR-0028), run by the cadencer of PAT-ADR-0026
(`tooling/foundry/campaign_effects.py`), the four writes are made by the `defer-remarks`
effect (`tooling/foundry/campaign_defer.py`); the page `campaign-effects.md` describes its
steps, its journal record and its refusals:

- The channel is a local receipt distinct from the review proof. The reviewer session
  prints a `remarks` key, each remark marked `fix_before_merge` true or false
  (`parse_result`, `tooling/foundry/campaign_review.py`); after `record-review-proof` the
  review effect runs `record-review-remarks` (`tooling/foundry/routing.py`), which writes
  the receipt bound to the proof identifier, the diff hash and the review generation
  (`tooling/foundry/review_remarks.py`). The `record-review-proof` command still accepts
  exactly the keys `outcomes` and `quality`, and neither `tooling/foundry/issue.py` nor
  `tooling/foundry/write.py` refers to the receipt, so the merge gate never reads it
  (`tests/test_campaign_defer.py`).
- A remark marked "fix before merge", a ticket with no origin Epic, a ticket of a
  companion Epic (no second-level deferral) and a durable refusal are corrected by the
  correction loop of the campaign and re-reviewed as stated in "Partial review on every
  pull request"; a transient failure makes the campaign wait and retry.
  Exception (PAT-227): when every remark of the round marked "fix before merge" targets the
  PR description alone, the correction replaces the description and is not re-reviewed, the
  diff being unchanged (`plugins/foundry/docs/campaign-effects.md`, "The description
  correction").
- The companion Epic and its follow-up issue are outside the subgraph of the campaign's
  Epic: no tracker link joins them to it, so the structure digest of the approval and the
  closure graph of the origin Epic do not include them (`tests/test_campaign_defer.py`).

Not changed by PAT-141:

- The review step of the DevHub coordinator (`_review_observation` in
  `tooling/foundry/campaign_runtime.py`) reads only the structured proof stored by
  `AcceptanceProofStore` (`tooling/foundry/routing.py`); the non-blocking remarks of a
  validated review are neither seen nor recorded by that coordinator, and none of the
  steps that follow its review step (`ci`, `human-gate` in `_GATE_STEPS`, then `merge`)
  reads or corrects them.
- The campaign coordinator has no issue-creation primitive. The mutating steps of its
  runner are `start`, `open-pr`, `merge`, `close-epic` and `sync-parent-acceptance`
  (`_MUTATING_STEPS`, `tooling/foundry/campaign_runtime.py`), and the
  `CampaignPipeline` protocol (`tooling/foundry/campaign_coordinator.py`) declares
  no method that creates an issue.
- The campaign preview exists for two providers. `preview_epic`
  (`tooling/foundry/epic_preview.py`) refuses a tracker whose adapter does not set
  `epic_subgraph_supported`; the attribute defaults to `False`
  (`tooling/foundry/trackers/base.py`) and the DevHub and Linear adapters set it to `True`
  (`tooling/foundry/trackers/devhub.py`, `tooling/foundry/trackers/linear.py`).

## Carrying a terminal proof across an integration of main (PAT-173)

Decision: PAT-ADR-0021 (read it with `foundry_cli.py query adr PAT-ADR-0021`). Command:
`routing carry-review-proof --issue <ID> --root <ROOT> --base <NEW-BASE-SHA> --from-base
<OLD-BASE-SHA> --from-diff-hash <OLD-DIFF-HASH>` (`main`, `tooling/foundry/routing.py`);
the checks are `AcceptanceProofStore.carry` in that file and run at carry time. Condition
1 is checked outside the lock held for conditions 2 to 6
(`ReviewDeduplicator.validated_completed_proof_binding`, which takes and releases the
ledger lock itself, then the criteria digest); the lock is then held for conditions 2 to 6, and under it the ledger entry is read again and
must still name the original proof. All of them must hold; otherwise the command raises
`RoutingConfigError` and writes nothing, and a full review stays required. `--base` is the
PR's base SHA as read on the code host, the one `issue merge` uses. The conditions are:

1. The original proof is terminal in the ledger, for the same issue, `quality=mergeable`
   and every criterion `pass` (`ReviewDeduplicator.validated_completed_proof_binding`),
   and the digest of the criteria of the issue as it is now is equal to the digest the
   proof covers (compared: `acceptance_criteria(issue body)` against the `id`/`digest`
   pairs of the proof).
2. `--from-base` is the base recorded in the original proof, and the diff recomputed
   from that base to the head recorded in the proof has the hash `--from-diff-hash`.
3. The new base differs from `--from-base` (with an unchanged base there is no
   integration of the main branch, so nothing is carried even if the head moved to the
   same tree); the old base is an ancestor of the new base, and the new base is an
   ancestor of the current head.
4. The tree of the current head is exactly the tree printed by `git merge-tree
   --write-tree` of the old reviewed head with the new base, which must be conflict free.
5. No path changed by the PR (`old base...old head`) is changed by the main branch
   (`old base` to `new base`); renames are not followed (`--no-renames`), so both
   paths count.
6. The diff of the current head against the new base, computed by the same `git_diff`
   as every review on the head read under the lock (`git_diff(root, base, new_head)`, not
   on a head that could move meanwhile), has the hash `--from-diff-hash`. This byte identity of the reviewed
   diff is checked, not deduced from conditions 1 to 5: they do not imply it when the
   recorded base is not an ancestor of the reviewed head, so a different hash is refused.

A rebase of the PR onto the new base whose tree equals the tree of the clean merge of the
reviewed head with the new base (condition 4) is carried too, as the
conditions above are met (`tests/test_review_carry.py::test_a_rebase_onto_the_new_base_with_the_same_tree_is_carried`).

On success the command appends a new proof (`AcceptanceProofStore._write`, never
overwritten), whose `carried_from` key names the original proof identifier, head, base and
diff hash, and whose `coordinates` hold the new head and base; it keeps the original
review generation and claim digest. It then rebinds the ledger entry of the diff hash to
the new root, base, head and proof, keeping the previous coordinates and the original
proof identifier in that entry's `carried` list (`ReviewDeduplicator` record,
`review-dedup/` state). Because the proof is bound to the new exact coordinates,
`valid_for_merge` accepts it through its existing checks, and a later `claim-review` of
that hash with the new coordinates is deduplicated. The command touches neither the
original proof file, nor the escalation state, nor the merge gate. Repeating the same
command writes nothing and prints `"carried": false`; that replay path also checks that
`--from-base` equals the base recorded in the `carried_from` of the carried proof, and
recomputes the diff on the head read under the lock.

A proof carrying `carried_from` is accepted by the Linear projection
(`LinearTracker._validate_acceptance_projection`), by `execution_receipts.review_receipt`
and by `evidence_plane._review_evidence` (optional key, closed four-key shape). The merge
gate asks for a proof only when the criteria are not all checked, or when the adapter sets
`delivery_receipt_supported` and the issue has a binding (`merge` in
`tooling/foundry/issue.py`); the context of PAT-ADR-0021 states it without these
conditions, and the ADR is not edited here.

What this does not cover: a conflict resolution, an edit after the integration, a
change of the issue criteria, or a main branch change to a file of the PR. Tests:
`tests/test_review_carry.py`.

## Portability of the three writes (PAT-139)

The first three writes of `AGENTS.md#R9` (b) are `create_issue` with the field
`Type: Epic` and no parent, `create_issue(parent=…)` and `add_comment`.
[`tracker-contract.md`](tracker-contract.md),
table "Core journey", marks the rows "Frame/intake/groom: create, comment" and
"Epics/enfants/dépendances: child creation, relations" `supported` for YouTrack, Linear
and `ghprojects`.

`tests/test_tracker_conformance_v1.py` runs them once per provider, on a double of the
provider, never on a real tracker: it creates an Epic without parent next to an existing
origin Epic, creates a child issue under it, adds a comment to that child, and checks
that the origin Epic was neither written nor linked. The double is not the same seam for
the three providers:

- YouTrack: the HTTP method `_req` is replaced; `create_issue`, `link` and `add_comment`
  of `tooling/foundry/trackers/youtrack.py` run as written;
- Linear: the `transport=` argument of `LinearTracker` is replaced; the adapter runs as
  written;
- `ghprojects`: not the `runner=` seam. Thirteen methods of the adapter are replaced,
  twelve private and the public `verify_project_identity` (the list is in
  `_GitHubCompanionProvider.install` in that test file; it includes `_field_catalog`,
  `_rest_write`, `_add_project_item` and `_set_project_field`), so the links read back
  and the absence of a write to the origin Epic are read from the double, not produced
  by the adapter. What runs as written is the create-intent journal, the create and resume
  sequence, the parent pairing checks and `_write_catalog` (over the doubled
  `_field_catalog`). Only the test of a Project without `Epic` option goes through the
  `runner=` seam.

No provider needed a fallback for these three writes on its double. A second test per
provider covers a project where the `Epic` type is not available:

| Provider | Where the `Epic` type comes from | When it is missing |
|---|---|---|
| YouTrack | a value of the project's `Type` field, sent by name (`_cf_write`, `tooling/foundry/trackers/youtrack.py`) | the adapter does not check before it posts; the provider's answer is not verified. The test makes the double raise `_YouTrackHTTPError`, the error `_req` raises on an HTTP failure: it shows that `create_issue` lets that error out unchanged and sends no further request (no readback, no link). It does not show what a real project answers |
| Linear | the `Epic` entry of the binding's `type_label_ids` | the adapter refuses with `type_unmapped` before the create mutation (`tooling/foundry/trackers/linear.py`) |
| `ghprojects` | the `Epic` option of the Project's "Foundry type" field | the adapter refuses with `invalid_field_options:type` when it reads the Project, before any write (`_field_map`, `tooling/foundry/trackers/ghprojects.py`), because it requires the options of that field to be exactly the declared set; a read of that Project is refused the same way |

What the coordinator does when the `Epic` type is missing (the table above) is in
`AGENTS.md#R9` (b): this is a durable refusal, so no substitute write, the remarks are
corrected before the merge and re-reviewed as stated in "Partial review on every pull
request", and the refusal is reported in the PR
description.

This paragraph is about another failure, not about the missing `Epic` type. On YouTrack,
`create_issue` with a parent makes two writes on the issue itself: a POST to `/issues`,
then `link(..., "subtask-of", parent)` (`link` in
`tooling/foundry/trackers/youtrack.py`), which posts a command to `/commands`. Before
them, when `fields` carries a `Milestone` and the project has a milestone bundle,
`_prewrite` has `_ensure_milestone` post that value to the bundle. The tests run it with
a failure of the second write: the created issue stays, without its parent link, and
`create_issue` removes nothing. The fourth write of `AGENTS.md#R9` (b) repairs it: `edit
link <FOLLOW-UP-ID> subtask-of <COMPANION-EPIC-ID>`, with the same arguments as the call
in `create_issue` (maintainer decision of 2026-10-11). `tests/test_tracker_conformance_v1.py`
does not cover this fourth write.

A transient failure is the other case of `AGENTS.md#R9` (b), next to the durable refusal
of a missing `Epic` type: the coordinator waits and retries the write before the merge.
When the failure leaves the effect of the write unknown (a network failure), it first
reads the tracker to see whether the write happened, and retries only if it did not.
That reading is what prevents a second issue or a second comment: no adapter retries a
write by itself in the code read here, and a retry after an unknown outcome does this,
per provider (files under `tooling/foundry/trackers/`):

| Provider | Retry of `create_issue` | Retry of `add_comment` |
|---|---|---|
| YouTrack | a plain second create: `create_issue` (`youtrack.py`) posts to `/issues` and takes the identifier the provider returns, with no client key. When the issue was created and only the parent link failed, a second call creates a second issue (the fourth write of `AGENTS.md#R9` (b) attaches the existing issue instead) | a plain second comment: `add_comment` (`youtrack.py`) posts with no identifier and no readback |
| Linear | a plain second create: `create_issue` (`linear.py`) sends a fresh `uuid.uuid4()` identifier at each call | a plain second comment: `add_comment` (`linear.py`) sends no client identifier; its readback checks the comment it has just created, not an earlier one |
| `ghprojects` | on the same data directory, `create_issue` (`ghprojects.py`) keeps a pending intent written before its only issue POST. If the issue then exists once, the second call resumes it and creates no duplicate. If no matching issue exists, `_observe_create_candidate` raises `create_effect_unknown`: from the first call itself, when the issue POST has failed and the read of the candidates that follows it finds none (the error of the POST is then kept only as the context of the exception raised), and from every later call, whatever made the POST fail. If that read itself fails, the error of the read comes out instead; if it finds exactly one issue, the first call raises `partial_create:issue_response_lost`. No code in the file removes a create intent: no recovery path was found. From another data directory, or once the intent is gone, an existing matching issue is refused with `unowned_create_candidate`, and with no matching issue the call is a plain create | a plain second comment: `add_comment` (`ghprojects.py`) posts once, reads back the comment it created and never looks for an earlier one |

[`tracker-contract.md`](tracker-contract.md) states the same for issue creation ("a
replay with zero candidates remains unknown and refuses") and for comments ("Free-text
comments remain outside S5"). So on `ghprojects` a failed creation that had no effect
cannot be retried with the same title, body, fields and parent: the adapter refuses,
and `AGENTS.md#R9` (b) then treats the case as a durable refusal. What the reading
cannot settle: a write whose effect becomes visible only after the reading, and a
second writer that makes the same write between the reading and the retry.

What each adapter gives the coordinator to recognise a transient failure:

| Provider | Quota exhausted | Network failure |
|---|---|---|
| YouTrack | no quota-specific error in `tooling/foundry/trackers/youtrack.py`: an HTTP failure is raised as `_YouTrackHTTPError` with its status; reset time not verified | not verified |
| Linear | `LinearQuotaExhaustedError` (`tooling/foundry/trackers/linear.py`), with the reset time in `reset_at` and `reset_at_ms`; never retried by the adapter ([`linear-tracker.md`](linear-tracker.md), "Transport errors, read retries and quota") | `transport_error`; a write is never retried by the adapter (same section) |
| `ghprojects` | reason `rate_limited` (`_run` in `tooling/foundry/trackers/ghprojects.py`), raised for the reads and for the POST of `add_comment`; reset time not verified. For the issue POST of `create_issue`, what the coordinator receives depends on the read of the candidates that follows a failed POST (`_observe_create_candidate`, operation `issue.create_reconcile`): if that read fails too, its own reason comes out (`rate_limited` when the quota is still exhausted), and `rate_limited` of the POST is only the context; if that read succeeds and finds no issue, the call raises `create_effect_unknown` (see the previous table); if it finds exactly one, `partial_create:issue_response_lost`. The same read also runs once before the POST: if it fails, its reason comes out and no POST is made | reason `transport_failed`, which `_run` also gives to every `gh` failure it does not classify, and to a `gh` process that cannot run or times out: it does not tell a network failure from a refusal by the provider. For the issue POST of `create_issue`, the same three cases apply as in the previous cell: the reason of the failed read of the candidates (`transport_failed` when the network is still down) comes out if that read fails too; if it succeeds, `create_effect_unknown` when it finds no issue and `partial_create:issue_response_lost` when it finds exactly one. `_rest_write` never retries |

Limits, as stated by PAT-ADR-0018 at its date and not lifted by these tests:

- The rule has been exercised on a real tracker with Linear only.
- Not verified: that an `Epic` value exists in the `Type` field of every YouTrack
  project.
- `ghprojects` is qualified on one profile only, a private personal Project
  ([`tracker-contract.md`](tracker-contract.md), "Core journey").
- A comment can be duplicated when a write is replayed after an ambiguous answer:
  [`tracker-contract.md`](tracker-contract.md) keeps free-text comments outside its
  replay guarantee ("Free-text comments remain outside S5").
- Not verified: whether adding a comment to an issue changes its version.
