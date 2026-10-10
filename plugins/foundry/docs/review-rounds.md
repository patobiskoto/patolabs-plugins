# Review rounds: baseline, observation window and return triggers (PAT-136, PAT-139)

The rule itself is `AGENTS.md#R9` (identical in `CLAUDE.md`); this page records how
its effect is measured. Nothing in that measurement is computed by Foundry: the counting
below is done by hand. Its last two sections (PAT-139) cite the code that bounds rule
(b): what no tool checks, what is not coded yet, and what each tracker provider carries.

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
  that the deferral does not hide its cost. Since PAT-139 these follow-up issues are the
  issues of the companion Epic (`AGENTS.md#R9` (b)); their own non-blocking remarks are
  corrected before the merge, so they open no further follow-up issue.
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

## What is not mechanical and what is not coded yet (PAT-139)

Rule (b) of `AGENTS.md#R9` sends the deferred remarks of an Epic to a companion Epic and
authorizes three tracker writes in advance (PAT-ADR-0018). This section lists what the
tools do not check and do not do.

Not mechanical:

- No gate checks that the companion Epic exists, that it has no tracker link with the
  origin Epic (no parent, no dependency, no relation), or that a remark was rightly
  classed as deferrable. This is judged in review.
- No gate checks that a PR of a companion Epic deferred nothing (no second-level
  deferral).
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

The three writes are `create_issue` with the field `Type: Epic` and no parent,
`create_issue(parent=…)` and `add_comment`. [`tracker-contract.md`](tracker-contract.md),
table "Core journey", marks the rows "Frame/intake/groom: create, comment" and
"Epics/enfants/dépendances: child creation, relations" `supported` for YouTrack, Linear
and `ghprojects`.

`tests/test_tracker_conformance_v1.py` runs them once per provider, on a fake transport,
never on a real tracker: it creates an Epic without parent next to an existing origin
Epic, creates a child issue under it, adds a comment to that child, and checks that the
origin Epic was neither written nor linked. No provider needed a fallback for these
three writes on its fake transport. A second test per provider covers a project where
the `Epic` type is not available:

| Provider | Where the `Epic` type comes from | When it is missing |
|---|---|---|
| YouTrack | a value of the project's `Type` field, sent by name (`_cf_write`, `tooling/foundry/trackers/youtrack.py`) | the adapter does not check before it posts; the provider's answer is not verified. The test simulates a refusal and shows that no later write follows |
| Linear | the `Epic` entry of the binding's `type_label_ids` | the adapter refuses with `type_unmapped` before the create mutation (`tooling/foundry/trackers/linear.py`) |
| `ghprojects` | the `Epic` option of the Project's "Foundry type" field | the adapter refuses with `field_option:type:Epic` before the issue is created (`_write_catalog`, `tooling/foundry/trackers/ghprojects.py`) |

What the coordinator does then is in `AGENTS.md#R9` (b): this is a durable refusal, so
no substitute write, the remarks are corrected before the merge and fully re-reviewed,
and the refusal is reported in the PR description.

A transient failure is the other case of `AGENTS.md#R9` (b): the coordinator waits and
retries the write before the merge. No adapter retries a write by itself in the code
read here; what each one gives the coordinator to recognise the case:

| Provider | Quota exhausted | Network failure |
|---|---|---|
| YouTrack | no quota-specific error in `tooling/foundry/trackers/youtrack.py`: an HTTP failure is raised as `_YouTrackHTTPError` with its status; reset time not verified | not verified |
| Linear | `LinearQuotaExhaustedError` (`tooling/foundry/trackers/linear.py`), with the reset time in `reset_at` and `reset_at_ms`; never retried by the adapter ([`linear-tracker.md`](linear-tracker.md), "Transport errors, read retries and quota") | `transport_error`; a write is never retried by the adapter (same section) |
| `ghprojects` | reason `rate_limited` (`_run` in `tooling/foundry/trackers/ghprojects.py`); reset time not verified | reason `transport_failed`; `_rest_write` never retries |

Not verified: whether a write that failed on the network had an effect. Retrying it can
then create a second issue or a second comment.

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
