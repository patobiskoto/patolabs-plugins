# Review rounds: baseline, observation window and return triggers (PAT-136, PAT-139)

The rule itself is `AGENTS.md#R9` (identical in `CLAUDE.md`); this page records how
its effect is measured. Nothing in that measurement is computed by Foundry: the counting
below is done by hand. The last point of "Limits" and the last two sections (PAT-139)
cite the code that bounds rule (b): what the merge command does not refuse, what no tool
checks, what is not coded yet, and what each tracker provider carries. Paths are relative
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
- Counted by hand from the per-round lines of the PR descriptions (format in
  `skills/merge-pr/SKILL.md`). Foundry's review generation number is not
  used: it was observed to be 4 for 3 reviews on PAT-125 and on PAT-132.

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

Result: complete in the sense of the paragraph above, because the three follow-up issues
are delivered (merged in PRs 118, 123 and 125). One count is a lower bound (PAT-143,
below).

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
  PAT-143 left out): 24 + 8 = 32 rounds over 8 origin issues, i.e. 4.0. This is the
  same computation as the coordinator's first whole-window figure, which left PAT-142
  and PAT-143 out because their rounds were believed unknown: the two "4.0" of the
  decision below are one figure.
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
  review after non-blocking remarks on an issue of a companion Epic) are PAT-146
  (1 round), PAT-149 (2) and the follow-up issues PAT-148 (2) and PAT-150 (2): 7 rounds
  over 2 origin issues, i.e. 3.5. PAT-137 and PAT-147 were merged before PAT-144, so a
  figure of 3.0 (12 rounds over 4 origin issues) that groups them with the later issues
  mixes both states of the rule. Two issues are too few to conclude anything.
  PAT-148 also holds the remarks deferred from PAT-137 and PAT-147 (CHANGELOG, entry
  PAT-148), merged before PAT-144; its 2 rounds are counted here in full against 2
  origin issues. Without PAT-148 the figure is 5 rounds over 2 origin issues, i.e. 2.5:
  on which side of 3.2 this subset falls depends on that attribution.

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

Start: the merge of PAT-144 (PR 121). Content: the first 10 origin issues merged after
it, with their follow-up issues, under the rule as amended by PAT-144. Same count (the
section "What is counted"), same result vocabulary (complete or incomplete), same
return triggers and same threshold of 3.2.

An issue belongs to the window by its merge instant; all its rounds are counted,
including those run before the merge of PAT-144. So PAT-146 and PAT-149, with PAT-148
and PAT-150, belong to both windows and their rounds are counted in each.

Coordinator's reading, to be confirmed by the maintainer: an origin issue is any merged
issue that is not an issue of a companion Epic, whether or not it has an origin Epic.
The window closes at the merge of its tenth origin issue; the maintainer then decides
(section "Return to the previous practice" below).

State on `origin/main` at `6865648` (2026-10-11), from `git log --first-parent`:

- origin issues counted so far, 7 of 10: PAT-146, PAT-149, PAT-163, PAT-164, PAT-167,
  PAT-168, PAT-190;
- follow-up issues delivered: PAT-148, PAT-150 and PAT-187 (PAT-187 settles the remarks
  deferred by PAT-163 and PAT-164);
- PAT-189 holds the remarks deferred by PAT-167, PAT-168 and PAT-190 (cited in their PR
  descriptions); it is not delivered on 2026-10-11 (stated by the coordinator, not
  checked in the repository), so it counts as not delivered;
- PAT-148 is counted in full although it also holds remarks of PAT-137 and PAT-147,
  merged before the start.

The rounds of the second window are not counted on this page yet; they are counted when
the window closes.

## Return to the previous practice

Previous practice: correct before the merge, then a full re-review. The maintainer
decides, in either of these cases:

- a blocking-level defect is found after the merge in a diff merged under rule (b);
- when the window closes, the total number of rounds (issues plus follow-up issues)
  divided by the number of origin issues is not below 3.2, the figure of the baseline
  (maintainer decision of 2026-10-11).

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

## What is not mechanical and what is not coded yet (PAT-139)

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
  The rest is judged in review.
- No gate checks that a PR of a companion Epic deferred nothing (no second-level
  deferral), that the fourth write was made when needed, that the companion Epic has a
  single follow-up issue, or that the review after a correction was limited to what
  changed. The merge gate is not modified: when the criteria of the issue are not all
  checked in the tracker, and also when they are all checked on a tracker whose adapter
  sets `delivery_receipt_supported` if the issue has a binding (`write.issue_binding`),
  it asks for a review proof of the exact diff (`valid_for_merge`, called twice by
  `merge` in `tooling/foundry/issue.py`), and after
  such a partial review that proof relies on the previous round for the unchanged parts.
- Nothing checks that only one companion Epic carries a given name. The name and a
  mention in text are the only relation between the two Epics, and no file under
  `tooling/` or `hooks/` contains the word `Nits`.

Not coded yet:

- An Epic campaign does not receive the reviewer's prose. Its review step
  (`_review_observation` in `tooling/foundry/campaign_runtime.py`) reads only the
  structured proof stored by `AcceptanceProofStore` (`tooling/foundry/routing.py`), and
  the `record-review-proof` command of that file accepts exactly the keys `outcomes` and
  `quality`. So the non-blocking remarks of a validated review are neither seen nor
  recorded by a campaign today, and none of the steps that follow the review step (`ci`,
  `human-gate` in `_GATE_STEPS`, then `merge`) reads or corrects them.
- The campaign coordinator has no issue-creation primitive. The mutating steps of its
  runner are `start`, `open-pr`, `merge`, `close-epic` and `sync-parent-acceptance`
  (`_MUTATING_STEPS`, `tooling/foundry/campaign_runtime.py`), and the
  `CampaignPipeline` protocol (`tooling/foundry/campaign_coordinator.py`) declares
  no method that creates an issue.
- The campaign preview exists for one provider only. `preview_epic`
  (`tooling/foundry/epic_preview.py`) refuses a tracker whose adapter does not set
  `epic_subgraph_supported`; the attribute defaults to `False`
  (`tooling/foundry/trackers/base.py`) and only the DevHub adapter sets it to `True`
  (`tooling/foundry/trackers/devhub.py`).
- So inside an Epic campaign the authorization has no effect yet. Three things are
  missing: a channel that carries the remarks to the campaign, the code that creates the
  companion Epic and its issues, and campaigns usable on the repository's tracker. They
  are left to a later issue under the same Epic, PAT-141.

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
corrected before the merge and fully re-reviewed, and the refusal is reported in the PR
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
