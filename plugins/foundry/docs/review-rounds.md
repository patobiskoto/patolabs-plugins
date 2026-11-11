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
  (`rounds - validated_rounds`), including a `fail`, `contradicted` or `not_covered` criterion
  under `mergeable`: `outcome: blocked` therefore does not mean the merge was refused or bypassed;
  the merge path is read elsewhere ("Ten merged issues read `blocked`" below).
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
  window; the text labels them "all logs passed, not filtered by issue or round" (the per-issue and
  per-role attachment is the separate `routing token-attribution`, see
  [cost-attribution.md](cost-attribution.md), "Rattachement des tokens à une issue et à un rôle"). A Claude log is the coordinator; the `*.jsonl`
  under `<dir>/<session>/subagents/` are the sub-agents (by position only, as in
  `cache_ttl_replay.py`). A Codex rollout is reported as `codex_unclassified`: the
  coordinator/sub-agent split is not implemented in `review_rounds.py`; whether a rollout
  carries enough to make it is unknown. A class with no readable log is `null`
  (unknown), never 0; sub-agent logs not found under that path are not counted, so a
  sub-agent total is a lower bound. `unreadable_logs` is one count over all classes: a class
  that has some unreadable logs is a lower bound too. A Claude request with no terminal snapshot is
  read, not rejected (PLP-344, [cost-attribution.md](cost-attribution.md), "Complétude Claude"): its class
  then carries `lower_bound_requests` (the number of such requests, key present only when above 0, printed
  by the text output) and its counters are lower bounds too. Whether an older Claude host wrote
  sub-agent records into the main log, which would then be counted as coordinator, is
  unknown.
- Output holds counts only: no claim or proof identifier, prompt or log content.

## Per-turn data of an issue (PLP-318)

`python3 tooling/foundry_cli.py routing review-turns ISSUE [--repository NS] [--root DIR]`
(`review_turns.py`, schema `review-turns.v1`, JSON with sorted keys) lists one entry per
review turn of the issue: a turn is a non-carried acceptance proof, as in the report above.
It is read-only and offline: it reads the proof store, the review ledger, the campaign remarks
receipt, the campaign attempts journal (`campaign_effects.read_attempts`, file
`campaign-attempts.jsonl` of the state directory) and the PLP-316 invocation record and writes nothing
(`tests/test_review_turns.py::test_read_only_and_content_free` compares the state directory,
journal included, before and after). No gate, routing or merge reads it; only the CLI verb and the offline export `kpi_export.py` (PLP-319, itself run by the merge step `kpi_merge.py`, PLP-343) import it
(`test_only_the_cli_verb_imports_review_turns`); it changes neither the proof format nor
`record-review-proof`. Top-level keys: `schema`, `issue`, `repository`, `sources`, `carried_proofs` (count of carried proofs of the issue, not turns), `unreadable_proofs` and `turns`. Per turn, with the source of each field (`sources` emits one entry per field, `unrecorded` excepted; `test_sources_cover_every_emitted_field`):

- `rank`: order of the ledger `completed_at` bound to the proof diff hash, then
  `generation`; a turn without a recorded time ranks after the timed ones.
- `head`, `base`: the proof `coordinates`.
- `generation`: the proof `review.generation`.
- `started_at`, `completed_at`: `claim.started_at` and `completed_at` of the review ledger record bound to the proof diff hash (PLP-319 reads `started_at` for [kpi-export.md](kpi-export.md)).
- `verdict` (`validated` when quality is `mergeable` and every criterion passes, else
  `blocked`, as `review_rounds`), `quality`, `criteria_pass`, `criteria`: the proof.
- `remarks`: the number of remarks of the receipt `review-remarks/<namespace>/<proof id>`
  (`review_remarks.py`), checked against the receipt schema version and the proof's issue, diff
  hash and generation. Without a receipt, the count `fix_before_merge + non_blocking` of the
  matching campaign journal record (see `type`), which is the same number
  (`campaign_review.py`, remark sums checked at parse time); a readable receipt always wins, and
  an unreadable one is not replaced by the journal count: `remarks` stays `null` with
  `receipt_unreadable` (PLP-321). `remarks` counts only remarks (`fix_before_merge + non_blocking`):
  the blocking findings of a blocked round are counted nowhere, so a blocked turn's `remarks` is not
  the number of its findings.
- `reviewer_model`, `reviewer_effort`, `reviewer_host`: the first reviewer record of the
  invocation log for this issue and this head (`invocation_log.invocations_for_diff`); this is
  the model requested at routing, not an observation of the model that ran.
- `type` (`full` or `partial`): `review.partial` of the campaign attempts journal record
  (`kind` `review`, `finished`, `ticket` the issue, `review.carried` false, `review.diff_hash` and
  `review.head` equal to the proof's; `campaign_review.py` `REVIEW_KEYS`, written by the
  review effect, `docs/campaign-effects.md`, "Partial review and proof carry"). Records with
  `carried` true describe a carried proof, which is not a turn here, and are ignored. Outside a
  campaign nothing records it: `prepare-review --previous-head` writes the declared head only to
  `coordinates.json` of the packet directory the caller chose (`review_packet.py`,
  `prepare_review_packet`), so a manual review has `type` `null`. The journal is read for the
  whole state directory, not per repository namespace: it carries a ticket identifier only.

A value no local source records is `null` and the turn's `unrecorded` maps the field to a
reason: `no_campaign_record` (type: no matching journal record, e.g. outside a campaign),
`attempts_journal_unreadable` (any journal line outside the contract, or an I/O error) or
`ambiguous` (matching records that disagree on `partial` or the remark counts); `no_campaign_receipt`
(remarks: no receipt and no matching journal record; the receipt is written only for a `mergeable`
quality, so a blocked turn never has one, `review_remarks.py`, `campaign_review.py`, but its journal
count fills `remarks`) or `receipt_unreadable`; without a receipt, `remarks` takes the journal reason
`attempts_journal_unreadable` or `ambiguous` when that is why the journal gives no count;
`no_reviewer_record_for_head` (review before PLP-316, a Codex current context, or a head with
no routed reviewer) or `invocation_log_unreadable` (probed after a failed lookup, deduced: a log that changes between the
lookup and the probe could still give the wrong reason). Limits: two proofs of the same head share
the first reviewer record (deduced from the lookup rule of `invocations_for_diff`); the
escalation ledger is not read, since it holds no per-turn field the proofs lack (deduced
from reading `escalation.py`, not exhaustively checked); a proof file that cannot be read is
counted in `unreadable_proofs` for the whole repository namespace, as above. The output holds
no proof, claim or invocation identifier and no remark text.

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

### Second window: result (PLP-359, counted on 2026-11-10)

The state above is kept as written; this section counts the window. The tenth origin issue
is PAT-193 (PR 136, `81724f1`, the first origin issue merged after PAT-170 in `git log
--first-parent origin/main`). The sixth follow-up issue is PAT-194 (PR 137): it settles the
remarks deferred by PAT-193 (PR title). That it belongs to the companion Epic is deduced
from that title and from its round line "follow-up none": the tracker of October 11 (ids
`PAT-n`) can no longer be read, so its parent is unknown. All 16 pull requests below were
merged on 2026-10-11 (`gh pr view`, `mergedAt`).

Rounds per issue, read from the per-round lines (format of `skills/merge-pr/SKILL.md`) of the
PR descriptions with `gh pr view` on 2026-11-10; the split is "follows first|blocking|remarks"
as in the first window:

| Issue | PR | Rounds | First | After blocking | After remarks |
|---|---|---|---|---|---|
| PAT-146 | 122 | 1 | 1 | 0 | 0 |
| PAT-149 | 124 | 2 | 1 | 1 | 0 |
| PAT-163 | 126 | 2 | 1 | 1 | 0 |
| PAT-164 | 127 | 1 | 1 | 0 | 0 |
| PAT-167 | 128 | 3 | 1 | 2 | 0 |
| PAT-168 | 129 | 3 | 1 | 2 | 0 |
| PAT-190 | 130 | 3 | 1 | 2 | 0 |
| PAT-169 | 133 | 3 | 1 | 2 | 0 |
| PAT-170 | 134 | 2 | 1 | 1 | 0 |
| PAT-193 | 136 | 3 | 1 | 0 | 2 |
| Ten origin issues | | 23 | 10 | 11 | 2 |
| PAT-148 (follow-up) | 123 | 2 | 1 | 0 | 1 |
| PAT-150 (follow-up) | 125 | 2 | 1 | 0 | 1 |
| PAT-187 (follow-up) | 131 | 2 | 1 | 0 | 1 |
| PAT-189 (follow-up) | 132 | 4 | 1 | 1 | 2 |
| PAT-192 (follow-up) | 135 | 2 | 1 | 0 | 1 |
| PAT-194 (follow-up) | 137 | 1 | 1 | 0 | 0 |
| Six follow-up issues | | 13 | 6 | 1 | 6 |
| Sixteen PRs | | 36 | 16 | 12 | 8 |

Cross-check (observed on 2026-11-10): `routing review-rounds <ISSUE-ID> --json` on the local
proof store, which still holds these proofs under their `PAT-n` ids, gives the same `rounds`,
`validated_rounds` and `blocked_rounds` for all 16 issues.

Result: complete in the sense of "Observation window", because the six follow-up issues
that settle the deferred remarks of the ten origin issues were delivered (remarks of PAT-146
settled by PAT-148, of PAT-149 by PAT-150, of PAT-163 and PAT-164 by PAT-187, of PAT-167,
PAT-168 and PAT-190 by PAT-189, of PAT-169 and PAT-170 by PAT-192, of PAT-193 by PAT-194). 36 rounds over 10 origin issues, i.e.
3.6 rounds per origin issue (arithmetic of the table), which is not below 3.2: the second
return trigger of "Return to the previous practice" is met as written. The first trigger
(a blocking-level defect found after the merge in a diff merged under rule (b)) was not
searched for here: unknown.

- Attribution, as in the first window: PAT-148 is counted in full although it also holds
  the remarks of PAT-137 and PAT-147 (merged before the start) and of PAT-146. Without it,
  34 rounds over 10 origin issues, i.e. 3.4. PAT-146 and PAT-149 with PAT-148 and PAT-150
  belong to both windows; counting neither of the four, 29 rounds over 8 origin issues, i.e.
  3.6 (3.625). No attribution brings the figure below 3.2.
- What the window measures. All 16 pull requests were merged on 2026-10-11, before the
  extension of the partial review to every pull request (PAT-172, PR 147, merged on
  2026-10-15, see the section "Partial review on every pull request"). It measures
  rule (b) as amended by PAT-144, not the current rule; PAT-193 itself ran full
  re-reviews after its rounds 1 and 2 (its PR description). The effect of the extension on
  the number of rounds is unknown and not counted.
- Not comparable one to one: the baseline (PRs 102 to 111) and this window are different
  issues; the figure says nothing on the cause of the difference (unknown). The rounds
  per origin issue are counted by hand from declarations in PR descriptions, checked
  against the proof store only for their totals.
- For scale only, not a window figure: on 2026-11-10, the 25 issues of the local proof store
  with the latest review completion have 52 rounds, 17 of them after an already validated
  round (`routing review-rounds`, 2.1 rounds per issue, no follow-up issue added).

## Cost of a round that follows a validated round (PLP-359, measured on 2026-11-10)

What is measured: the wall-clock duration and the reviewer tokens of the review rounds whose
previous round on the same issue was validated (`mergeable`, every criterion `pass`; the
rounds that `AGENTS.md#R9` (b) reviews partially). It is a proxy: no local source says that
these rounds were partial. Their `type` is unrecorded (`no_campaign_record` for the 28 rounds
of the set: `routing kpi-export` omits their `review_round` records, so they were run outside a
campaign, deduced), and whether a packet was prepared with `--previous-head` was not looked at: unknown.

Sets, all from the local proof store of this repository (44 `PLP-n` issues, 91 rounds,
2026-10-23 to 2026-11-07):

- P: the 28 rounds that follow a validated round, over 22 issues.
- F: the 22 first rounds of those same 22 issues (the same issues as P, checked equal).
- B, a second reference: the 19 rounds that follow a blocked round, over 13 issues. The
  rule makes them full reviews.

Method and commands (run from `plugins/foundry/`, results kept outside the repository):

1. `python3 tooling/foundry_cli.py routing review-rounds PLP-<n> --root <repo root> --json`
   for each issue: `per_round` gives `validated`, `started_at` (claim start) and
   `completed_at`; the duration of a round is their difference. The stored ledger order gives
   the previous round.
2. `python3 tooling/foundry_cli.py routing kpi-export <the 44 issues> --root <repo root>
   --output <file>`: the `attempt` records of role `reviewer` carry `tokens` (`input`,
   `cached_input`, `cache_write_input`, `output`, [kpi-export.md](kpi-export.md)). A round
   is paired with the reviewer `attempt` of its issue whose `started_at` lies between the
   claim start and the completion of the round. This pairing is a rule of this page, not a
   recorded link (deduced): it gave exactly one attempt for 69 of the 91 rounds and none for
   22, no attempt was paired twice and none was left over; the paired attempts start 19 to
   97 seconds after the claim start.
3. Statistics are medians, with minimum and maximum, over the rounds of a set that have the
   value. The cache read figure is the volume re-read at every request, not new text.

Unknown, stated per set (unknown is never counted as zero):

- Tokens could not be paired for 22 rounds, all completed between 2026-10-23 and
  2026-10-27: PLP-1, PLP-2, PLP-310 to PLP-314, PLP-316 to PLP-319 and PLP-327 (no reviewer
  `attempt` in the window; the cause is unknown, the dates are those of the issues that introduced the
  invocation record, PLP-316 and after, deduced). That is 7 of the 28 rounds of P, 7 of the 22
  of F and 3 of the 19 of B; the statistics on tokens are over the 21, 15 and 16 others.
- Most token figures are lower bounds: a request of the log with no terminal snapshot is
  counted with the largest value observed (`lower_bound_requests`, [kpi-export.md](kpi-export.md),
  "Tokens that are a lower bound"). 18 of the 21 rounds of P, 13 of the 15 of F and all 16 of B
  are in that case; the size of the error is unknown and may differ between the sets, so the
  ratios below are not reliable. Only 5 paired rounds have exact tokens: PLP-350 rounds 1 to 3
  and PLP-353 rounds 1 and 2.

Results (P against F; B for reference):

| | Rounds | With tokens | Duration, median (min to max) | Cache read, median | Cache write, median | Input, median | Output, median |
|---|---|---|---|---|---|---|---|
| P, after a validated round | 28 | 21 | 219 s (123 to 4809) | 531,284 | 73,949 | 26 | 1,655 |
| F, first round of the same issues | 22 | 15 | 463 s (230 to 869) | 1,311,794 | 105,517 | 44 | 4,527 |
| B, after a blocked round | 19 | 16 | 379 s (104 to 23628) | 789,910 | 77,562 | 32 | 2,630 |

- Duration: the median of P is 0.47 of that of F (219 s against 463 s); 23 of the 28 rounds
  of P are shorter than the first round of the same issue. The means (441 s for P, 513 s for
  F) are driven by one round of P, PLP-332 round 3 (4809 s), and, in B, by PLP-328 round 2
  (23628 s): a duration is the span from claim to completion, so it includes any wait
  inside it.
- Tokens, in the 21 pairs where both the round of P and the first round of its issue have
  tokens (15 issues), a round of P over the first round of its issue: median ratio 0.43 of
  cache read (16 of 21 below 1), 0.61 of cache write, 0.62 of input, 0.43 of output.
  Lower bounds on both sides.
- On the exact pairs only (2 issues): PLP-350 rounds 2 and 3 read 884,696 and 757,083 cache
  tokens against 725,444 for round 1, and wrote 9,034 and 9,646 output tokens against 11,047;
  PLP-353 round 2 read 612,961 against 1,859,300 and wrote 10,093 against 17,961. So a
  round after a validated one was not cheaper on cache read in one of the two issues; two
  issues are too few to conclude.
- What is not measured: the cost of the alternative, a full re-review of the same
  correction (the figure for B is another set of issues, not that), the coordinator's own
  work (building the packet, correcting, writing the description), the cost of a longer
  chain of rounds, and any effect on the defects merged.

The twelve most recent rounds of P (reviewer tokens `input` / cache read / cache write /
`output`, `*` for a lower bound; duration of the first round of the same issue in the last
column):

| Round | Completed | Duration | Input | Cache read | Cache write | Output | First round |
|---|---|---|---|---|---|---|---|
| PLP-324 r2 | 2026-10-30 | 208 s | 26* | 540,433* | 65,171* | 1,367* | 454 s |
| PLP-321 r2 | 2026-10-30 | 454 s | 50* | 1,876,558* | 111,135* | 4,802* | 869 s |
| PLP-345 r2 | 2026-10-30 | 191 s | 20* | 465,759* | 83,708* | 1,628* | 295 s |
| PLP-29 r2 | 2026-10-30 | 148 s | 22* | 387,711* | 54,041* | 1,350* | 393 s |
| PLP-337 r2 | 2026-10-30 | 243 s | 22* | 531,284* | 85,182* | 1,382* | 824 s |
| PLP-337 r3 | 2026-10-30 | 185 s | 18* | 378,549* | 76,752* | 1,655* | 824 s |
| PLP-337 r4 | 2026-10-30 | 192 s | 18* | 342,561* | 74,050* | 2,358* | 824 s |
| PLP-337 r5 | 2026-10-30 | 178 s | 20* | 482,024* | 83,886* | 2,774* | 824 s |
| PLP-346 r3 | 2026-10-30 | 212 s | 26* | 530,744* | 63,259* | 1,140* | 275 s |
| PLP-350 r2 | 2026-11-02 | 175 s | 38 | 884,696 | 84,193 | 9,034 | 230 s |
| PLP-350 r3 | 2026-11-02 | 201 s | 34 | 757,083 | 64,630 | 9,646 | 230 s |
| PLP-353 r2 | 2026-11-07 | 322 s | 30 | 612,961 | 60,660 | 10,093 | 488 s |

The tokens of all 21 rounds of P with tokens: 648 input, 17,870,865 cache read, 1,613,534
cache write and 71,566 output in total, over 10,108 s of the same rounds (lower bounds). This is
the reviewer only, at the model requested at routing (`opus-5.5` for all 69 paired
attempts, [kpi-export.md](kpi-export.md): the model served is not recorded).

## Decision on rule R9 after these two measures (PLP-359)

Decision of the maintainer (2026-11-10), taken on the figures of the two sections above:
rule R9 is kept as it is. The second return trigger is met as written (3.6, not below 3.2)
and does not lead to the return to the previous practice. Reasons given with the decision:
the second window was merged before the extension of the partial review to every pull
request (PAT-172, PR 147), so it does not measure the current rule; the 25 issues with the
latest review completion have 2.1 rounds per issue (no follow-up issue added, so not the
window figure); a round that follows a validated round has a median duration of 219 s
against 463 s for the first round of the same issues. No third window is counted by hand:
the rounds are read with `routing review-rounds` (section "Per-issue report"). `AGENTS.md`,
`CLAUDE.md`, PAT-ADR-0018 and PAT-ADR-0022 are not changed; a later change to either ADR
still goes through a new ADR.

## Return to the previous practice

Previous practice: correct before the merge, then a full re-review. The maintainer
decides, in either of these cases:

- a blocking-level defect is found after the merge in a diff merged under rule (b);
- when the window closes, the total number of rounds (issues plus follow-up issues)
  divided by the number of origin issues is not below 3.2, the figure of the baseline
  (maintainer decision of 2026-10-11).

## A ticket with no origin Epic defers to `Nits` (PAT-283, PAT-ADR-0029)

Rule (b) of `AGENTS.md#R9` gave an issue with no origin Epic no deferral (maintainer
decision of 2026-10-10): every non-blocking remark was corrected before the merge and
re-reviewed, so the rule had no stopping criterion. Observed: PR 209 (PAT-265) took five
rounds, four of them for docstring sentences; PAT-ADR-0029 cites the same observation on a
consuming project (2026-10-19). Since PAT-ADR-0029 such an issue defers its non-blocking
remarks after a validated round to the companion Epic of the project named exactly `Nits`,
with the rules of PAT-ADR-0018 (no tracker link, one open follow-up issue, a new one when
it is started or closed, the four writes authorized in advance). The PR of an issue of any
companion Epic, `Nits` included, corrects its remarks. The effect on the number of rounds
is unknown and not counted here. Since PAT-291 the campaign cadencer defers such a ticket
(`defer-remarks`, `campaign_defer.run_deferral`) and recognizes the title `Nits` as a
companion Epic (`campaign_defer._COMPANION_NAME`), so a ticket of `Nits` is corrected, never
deferred again (`docs/campaign-effects.md`).

## Reading the deferred-remarks debt (PLP-361, PLP-ADR-0065)

The deferred remarks are not counted by any tool; the debt is read in the tracker, by hand.
List the project (`query backlog`, `skills/merge-pr/SKILL.md`, "Commands to look and to read
back"): the companion Epics are the issues titled `Nits <ORIGIN-EPIC-ID>` and exactly `Nits`
(`COMPANION_PREFIX`, `NO_ORIGIN_COMPANION` in `campaign_defer.py`); their follow-up issues are
their children. For each open follow-up issue count the remarks: the `- ` list items of the
comments that open with `Non-blocking review remarks deferred from` (`DEFERRAL_HEADER`,
`remark_count` in `campaign_defer.py`); other comments count for nothing. From 30 remarks
(`MAX_FOLLOW_UP_REMARKS`) the issue is full and receives no more: the next deferral creates a new
follow-up issue in the same companion Epic, and the full one stays open. A follow-up issue created
before PLP-ADR-0065 whose remarks already count 30 or more by this rule is full at once. The count only sees the comments the adapter's `get_issue` returns: the Linear adapter keeps the 10 most recent ordinary comments (`trackers/linear.py`, normalisation of `comments`), so there a follow-up issue may never reach 30 and the cap may never apply (deduced from the code, not observed); whether the other adapters return every comment is unknown. Remarks written in another format (another header, numbered items) are not counted either. The
cap therefore bounds only the remarks it counts; it is not a bound on the debt.

Triage before a minor or major release: `skills/release/SKILL.md`, step 4, stops for the
maintainer's verdict before the release PR. A repository that does not release through
`foundry:release` has only the cap; its triage stays manual. Unknown: whether the cap alone
keeps the debt contained there (PLP-ADR-0065).

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
the full diff through the shared verifier (the range is read with `routing read-review
--since`, PAT-283, `docs/review-packet.md`); the reviewer checks that each correction
settles its remark without a new defect and that nothing else changed, and returns a
verdict on all criteria for the exact diff. The proof has the same form as a full one,
relies on the previous round for the unchanged parts, and the PR description says so. No
mechanical check distinguishes a partial from a full review; the merge gate and the proof
format are unchanged. The rule is in `AGENTS.md#R9` (b), `skills/merge-pr/SKILL.md` and
`skills/review-pr/SKILL.md`. The partial and full review times quoted above are one
observation each (the cost of a round that follows a validated round is measured in the
section "Cost of a round that follows a validated round"); the effect of this extension on the
number of rounds is unknown and is not counted on this page. The only figure on what giving up the extra full look costs is
the one in "Limits" (one of 11 full re-reviews after the correction of remarks caught an
old defect); it comes from that section, written before this extension, and is not
re-counted for it. The previous round's verdict and remarks that the packet carries
are declarations of the coordinator, not checked against any byte. The commit range is
read from the bytes since PAT-283 (`routing prepare-review --previous-head`, `routing
read-review --since`: `docs/review-packet.md`); the tool checks that the head passed is an
ancestor of HEAD, not that it is the head of the previous round. Before PAT-283 the range
was a declaration too, and the send text did not say that the review was partial.

## A blocked diff stays deduplicated (PLP-341)

- Observed in the code (not run) (`routing_facades.py`, the `mode=deduplicated` plan: "ce hash de diff possède une
  review terminée et reste dédupliqué."; `review_packet.py`, `RoutingConfigError("active private
  claim required")` when the claim does not have `should_run` true; `routing.py`,
  `_assert_recoverable`: "une review terminée est terminale et ne peut pas être récupérée."):
  once a terminal proof with `quality=blocked` is recorded, a new round on an identical diff is
  refused. `prepare-review` fails with "active private claim required" and `recover-review`
  refuses the completed review. That message applies with the same HEAD; with a different HEAD
  (for example after an empty commit), `claim-review` refuses earlier through `_assert_current_head`
  (`routing.py`): "le HEAD a changé depuis le claim : réservez le nouveau diff avant de relancer un
  reviewer.", instead of returning `should_run=false` (read in the code, not run). Only a correction that changes the diff gives a new hash and so
  a new round (`review_diff_hash` is the SHA-256 of the `git diff --binary` bytes, `routing.py`).
- Deduced from `git_diff` and `review_diff_hash` (the PR description is not an input): a block
  that needs no diff change, for example a false PR description, can be corrected in the PR body
  but the correction does not change the hash, so the blocked proof cannot be replaced by a new
  review of the same diff. What remains possible is a commit that changes the diff (an empty
  commit leaves `base...head` and so the hash unchanged, deduced from `git_diff`). In a
  campaign, the description-only correction of PAT-227 (`docs/campaign-effects.md`, "The
  description correction") is decided for a validated round with remarks marked "fix before
  merge", not for a blocked one (deduced from `campaign_cadence._ticket_step`: the `review_blocked`
  branch, `correct`, comes before the `review_fix_description` branch; the value
  `review_fix_description` is `DESCRIPTION_REASON` in `campaign_review.py`). A blocked ticket of a campaign
  gets `correct`/`review_blocked`, bounded by `_bounded` (`MAX_CORRECTIONS`); a correction that does
  not change the diff leaves the `blocked` proof, so `correct` recurs up to that bound (deduced from
  `_ticket_step` and `_bounded`, not run). Whether a blocked proof can be superseded without a diff change is unknown.

## Ten merged issues read `blocked` (PLP-360, observed on 2026-11-10)

Question: ten issues that reached the default branch read `outcome: blocked` in `routing review-rounds`
(`validated` is `quality == "mergeable" and all_pass`, `review_rounds.py:76`; `outcome` is read from the
last round, `review_rounds.py:140`). Answer, observed on the local proof store and on the trackers and
pull requests: in all ten, the last round is `mergeable`; what makes it `blocked` is a criterion that is not
`pass`. The merge went through the explicit human override of `issue merge`
(`--allow-incomplete-ac --ac-override-reason=<code>`, `_ac_override_reason` and the `except` branch of
the AC step in `tooling/foundry/issue.py`), not around the gate. In the code read for this check
(`3079cee`, before PLP-368; the current rule is in "Merge gate since PLP-368" below), that branch was reached
only when the tracker criteria were not all checked and `AcceptanceProofStore.valid_for_merge` (`routing.py`,
`quality` mergeable and every criterion `pass`) refused. What it then required depended on the adapter
(`issue.py`). With `delivery_receipt_supported` and an issue binding (set by `trackers/youtrack.py` only,
`False` in `trackers/base.py`), `current_for_sync` (`routing.py`) had to find one completed proof on the exact
head, base and diff. With `acceptance_override_projection_supported` (`trackers/devhub.py` and
`trackers/linear.py`; the ten issues are on DevHub today, their tracker at merge time was not checked), no
review proof was read: `write.project_acceptance_override` wrote, before the merge, a typed receipt with the
public reason code, bound to the review coordinates held by the tracker (`docs/devhub-tracker.md`), and the
audit comment was best-effort. Without that second capability the audit comment had to be written before the
merge.

Table, last round only (verdict letters: p `pass`, f `fail`, c `contradicted`, n `not_covered`).
"Last round head = PR head" was compared with `gh pr view --json headRefOid` for all ten. None
of the ten has a carried proof (`carried_proofs` 0 for each).

| Issue | Rounds | Last round | Merge path (reason code of the audit comment) | Established by |
|---|---|---|---|---|
| PLP-355 | 1 | mergeable, 1p 2n | audited override (`release-post-merge-steps`): tag and installation criteria are post-merge | proof store, `query issue` audit comment, PR head |
| PLP-348 | 2 | mergeable, 1p 3n | same | same |
| PLP-340 | 1 | mergeable, 1p 2n | same | same |
| PLP-339 | 2 | mergeable, 1p 2n | same | same |
| PLP-333 | 5 | mergeable, 1p 2n | same | same |
| DEVHUB-202 | 1 | mergeable, 2p 1n | audited override (`post-deploy-prod-readback`): production readback after deploy | same, plus the round line of the PR description |
| DEVHUB-199 | 3 (first `blocked`: 1p 1f 3n) | mergeable, 2p 3n | audited override (`release-post-merge-ac`): two post-merge criteria, three not coverable by the diff | same, plus the three round lines of the PR description |
| RE-495 | 3 (first two `blocked`) | mergeable, 3p 1n | audited override (`owner-device-test-validated`): device test recorded on the issue before the merge | same |
| RE-472 | 7 (two `blocked`, one of them after four `mergeable`) | mergeable, 4p 1f | audited override (`maintainer-waiver-serial-memory-20261028`): the failed criterion is the one the PR description records as waived | same, plus the round lines of the PR description |
| RE-436 | 5 (two `blocked`) | mergeable, 3p 1c 1n | audited override (`maintainer-inclusion-beta-checks-20261028`): the contradicted criterion is the accepted deviation the PR description records | same |

Consequences:

- Cause (deduced from the table and `review_rounds.py:76`): `validated` requires every criterion `pass`,
  so any criterion that cannot be met by the diff (`not_covered`) or that the maintainer waives (`fail`,
  `contradicted`) reads `blocked` although the reviewer's `quality` is `mergeable`. The report is not wrong
  by its own definition (the section "Per-issue report" above); its label does not tell the override path from
  a refused merge. `outcome` and `blocked_rounds` count the legitimate path as blocked, and the counts of
  the windows of this page, which use `validated_rounds` / `blocked_rounds`, are read with that limit.
  No output key changed.
- To tell a refused merge from an overridden one, read `per_round` (`quality`, `all_pass`) together with the audit
  comment of the issue: `quality: mergeable` with `all_pass: false` and an `Audit merge: override humain
  explicite` comment is the override path, and so is `quality: blocked` on the last round with such a comment
  (the override does not read `quality`). On a tracker that projects the typed receipt the comment is
  best-effort and the receipt is the authority; it was not read here. A last round that is not validated with
  neither comment nor receipt must be investigated; it is not by itself an unauthorised merge: before
  PLP-368, when the tracker criteria were all checked, `issue merge` read a review proof only with
  `delivery_receipt_supported` and a binding (`issue.py` at `3079cee`), and the last proof can belong to a
  pull request closed without merge ("Per-issue report").
- No merge without a validated proof or an audited override was found among the ten (observed).
  Not established here: the typed `acceptance-override` receipt of each issue (`query issue` returned no
  `acceptance_status` for any of the ten, so the receipt, if any, is unread: unknown); and the CI
  gate of the two other repositories, which this check did not re-run for DEVHUB-202,
  RE-495, RE-472 and RE-436 (unknown; CI of the merge commit is recorded on the issue for PLP-355,
  PLP-348, PLP-340, PLP-339, PLP-333 and DEVHUB-199).
- At `3079cee` (before PLP-368), on a tracker with `delivery_receipt_supported` the override branch accepted
  any completed proof on the exact coordinates, whatever its `quality` (`current_for_sync` does not read it);
  on the other trackers it read no review proof at all (deduced from the code, not exercised). The control
  was the typed receipt where the adapter projects it, the audit comment elsewhere. It is the documented human
  escape hatch; in the ten cases a `mergeable` proof existed on the PR head (table above), although an
  adapter of that kind did not require one.

## Merge gate since PLP-368 (PLP-ADR-0064)

`issue merge` (`merge` in `tooling/foundry/issue.py`) now reads the local proof store
(`AcceptanceProofStore`, `routing.py`, under `registry.data_dir()`) on every provider before it calls the
code-host merge, whether the tracker criteria are checked or not:

- without the override, it requires one proof `mergeable` with every criterion `pass` on the exact issue
  criteria, head, base and diff (`valid_for_merge`). Otherwise it refuses and names the cause; when the
  criteria are all checked the message also points to the override;
- under `--allow-incomplete-ac --ac-override-reason=<code>`, when that proof is refused, it requires one
  completed proof on the same coordinates, whatever its `quality` (`mergeable` or `blocked`) and its
  per-criterion verdicts (`current_for_sync`), and refuses without one. The typed receipt where the adapter
  sets `acceptance_override_projection_supported`, the audit comment elsewhere, is then written before the
  merge, as before;
- the local `HEAD` must equal the PR head: on the providers that set `bounded_transition_proofs`, or
  `delivery_receipt_supported` with a binding, the merge is refused before the proof is read, as before; on
  any other path the proof lookup fails, and the override is refused too;
- a pull request already merged (`pr.merged`) calls no merge and, with every criterion checked, needs no new
  proof; YouTrack with a binding still reads one for its delivery receipt. With unchecked criteria the block in
  `issue.py` (`ac_complete` false) still requires `valid_for_merge` or the override, as for any merge.

Consequences (deduced from the code): a review recorded on another machine or under another data directory
is not seen, and the override does not get around that; an issue with no readable criterion cannot be merged,
because `acceptance_criteria` (`routing.py`) refuses an empty list, so no proof can exist for it;
`current_for_sync` validates every marker of the proof store (`_validated_proof`) before filtering by issue, so a
corrupt marker of any issue blocks every merge, the override included (`ProofIntegrityError`). The refusal for
lack of proof names the data directory read (`registry.data_dir`: `FOUNDRY_DATA`, else `~/.config/foundry`), to
explain a proof recorded elsewhere. Under the override with every criterion checked, the audit text no longer
says the criteria are incomplete. Tests:
`tests/test_merge_review_proof_gate.py`.

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
  changed. The merge gate is not modified by this rule: on every provider, whether the
  criteria of the issue are checked in the tracker or not (PLP-ADR-0064), it asks for a
  review proof of the exact diff (`valid_for_merge`, called once by `merge` in
  `tooling/foundry/issue.py`; under the human override, any completed proof on the same
  coordinates, `current_for_sync`), and after
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
- A remark marked "fix before merge", a ticket of a
  companion Epic, `Nits` included (no second-level deferral) and a durable refusal are
  corrected by the correction loop of the campaign and re-reviewed as stated in "Partial review on every
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
3. The new base differs from `--from-base`, or equals it while the old reviewed head
   does not contain it (PAT-288: the PR was reviewed against a base the branch did not
   yet contain, then integrated it; `tests/test_review_carry.py::test_an_unchanged_base_not_contained_in_the_reviewed_head_is_carried`).
   With an unchanged base that the old head already contains there is no integration of
   the main branch, so nothing is carried even if the head moved to the same tree. The
   old base is an ancestor of the new base, and the new base is an ancestor of the
   current head.
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
and by `evidence_plane._review_evidence` (optional key, closed four-key shape). Since PLP-368
(PLP-ADR-0064) the merge gate asks for a proof on every provider, whether the criteria
are checked or not (`merge` in `tooling/foundry/issue.py`), as the context of
PAT-ADR-0021 states it; before, it asked only when the criteria were not all checked, or
when the adapter set `delivery_receipt_supported` and the issue had a binding.

What this does not cover: a conflict resolution, an edit after the integration, a
change of the issue criteria, or a main branch change to a file of the PR. Tests:
`tests/test_review_carry.py`.

## Portability of the three writes (PAT-139)

The first three writes of `AGENTS.md#R9` (b) are `create_issue` with the field
`Type: Epic` and no parent, `create_issue(parent=…)` and `add_comment`.
[`tracker-contract.md`](tracker-contract.md),
table "Core journey", marks the rows "Frame/intake/groom: create, comment" and
"Epics/enfants/dépendances: child creation, relations" `supported` for YouTrack, Linear,
`ghprojects` and DevHub (PAT-323).

`tests/test_tracker_conformance_v1.py` runs them once per provider, on a double of the
provider, never on a real tracker: it creates an Epic without parent next to an existing
origin Epic, creates a child issue under it, adds a comment to that child, and checks
that the origin Epic was neither written nor linked. The double is not the same seam for
the four providers:

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
- DevHub: the HTTP method `_req` is replaced (a subclass of the scripted tracker of
  `tests/test_devhub_tracker.py`); `create_issue`, `add_comment` and `get_issue` of
  `tooling/foundry/trackers/devhub.py` run as written. `create_issue(parent=…)` is one POST
  that carries the parent, not two writes.

No provider needed a fallback for these three writes on its double. A second test per
provider covers a project where the `Epic` type is not available:

| Provider | Where the `Epic` type comes from | When it is missing |
|---|---|---|
| YouTrack | a value of the project's `Type` field, sent by name (`_cf_write`, `tooling/foundry/trackers/youtrack.py`) | the adapter does not check before it posts; the provider's answer is not verified. The test makes the double raise `_YouTrackHTTPError`, the error `_req` raises on an HTTP failure: it shows that `create_issue` lets that error out unchanged and sends no further request (no readback, no link). It does not show what a real project answers |
| Linear | the `Epic` entry of the binding's `type_label_ids` | the adapter refuses with `type_unmapped` before the create mutation (`tooling/foundry/trackers/linear.py`) |
| DevHub | the `Type` field value sent by name (`_FIELD_MAP`, `tooling/foundry/trackers/devhub.py`) | the adapter does not check before it posts; the provider's answer is not verified. The test makes the double raise `DevHubTrackerError` (status 400), the error `_req` raises on an HTTP failure: it shows that `create_issue` lets that error out and sends no further request. It does not show what a real project answers |
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
