# Process contract: rule R9, fewer review rounds (PAT-136, PAT-139)

Since PLP-362 the repository contract (`AGENTS.md`, identical in `CLAUDE.md`, at the
repository root) keeps the checklist (a) and the correction rule (c) verbatim and a short version of (b) to limit the
context loaded at every session. The heading `R9` stays in the contract, so `AGENTS.md#R9`,
`AGENTS.md#R9(a)`, `AGENTS.md#R9(b)` and `AGENTS.md#R9(c)` citations still resolve. The text
below is moved verbatim from `AGENTS.md` R9; it is the detail of the rule, and the contract's
short version is a summary of it. Paths are relative to the repository root.


## R9 — Fewer review rounds: author checklist, grouped deferral, minimal corrections (PAT-136, PAT-139)

This rule applies to the implementer, to every correction commit, and to the
coordinator when it writes files itself. It changes nothing about the reviewer's minimum
tier (FOUNDRY-ADR-0006), the first full review, or the full review after any blocking
round. Baseline, observation window and return triggers are in
`plugins/foundry/docs/review-rounds.md`.

**(a) Checklist before handing work back.** The author checks each point on every
sentence of documentation it adds or changes, and reports for each sentence that falls
under points 1 to 5 its source (constant, `file:line`, or page):

1. A sentence about what a tool launches, loads, calls or writes cites its constant,
   file or page; otherwise it points to that page without summarising it.
2. A cause is stated as `observed`, `deduced` or `unknown`.
3. A rule, threshold or quantity is copied from the code or the source report, then
   searched for across the rest of the repository to correct statements it made false.
4. A comparison names the compared sets and checks that they are equal.
5. A documented procedure has been run once, on a copy.
6. No proper name or user name appears in any file, tests included.

**(b) Non-blocking remarks: grouped deferral to a companion Epic (PAT-ADR-0018).** After
a review round that is fully validated, non-blocking remarks are not corrected in the PR.
The remarks deferred from the issues of an Epic go to a companion Epic, named `Nits`
followed by the identifier of the origin Epic (`Nits <ORIGIN-EPIC-ID>`). The origin Epic
of an issue is its direct parent Epic: an issue under a sub-Epic uses that sub-Epic. The
two Epics have no tracker link between them: no parent, no dependency, no relation. What
relates them is that name and a mention in text, nothing else. The remarks deferred from
an issue with no origin Epic go to the companion Epic of the project, named exactly `Nits`
(PAT-ADR-0029, which amends the maintainer decision of 2026-10-10 that gave such an issue
no deferral); the rules of this paragraph apply to it unchanged. Inside the companion Epic
the remarks are held by a single follow-up issue, which gathers all the deferred remarks
of the origin Epic. In `Nits`, which has no origin Epic, that issue gathers the remarks of
every issue of the project with no origin Epic. If that issue is already started or
closed, a new follow-up issue is created: no remark is added to a started or closed issue
(maintainer decision of 2026-10-11). A follow-up issue that already holds 30 remarks or more is
full (PLP-ADR-0065, which amends PAT-ADR-0018 and PAT-ADR-0029): it is treated like a started or
closed one, so no remark is added to it and the next deferral creates a new follow-up issue in the
same companion Epic (write 2 below). A remark is one `- ` list item of a deferral comment; the
count is made over the deferral comments of the issue, in the same format for the coordinator and
for the `defer-remarks` effect. A deferral that would cross 30 is not split: it goes whole into an
issue that is not yet full. A full issue stays open. Before the pull request of a minor or major
release opened through `foundry:release`, the coordinator lists the open follow-up issues of the
project (companion Epics `Nits <ID>` and `Nits`) and stops for the maintainer's triage: each remark
is kept (an ordinary issue) or dropped; a follow-up issue whose remarks are all triaged goes to
`dropped`, and a companion Epic whose children are all done or dropped is closed. That triage is a
human verdict (FOUNDRY-ADR-0014), a step of the skill and not a gate. The follow-up issue that receives the remarks of a
PR is cited in that PR's description before the merge. These three cases are still
corrected before the merge; the
rest of this rule adds two more (the PR of an issue of a companion Epic, `Nits` included,
and a durable refusal of one of the writes), corrected the same way. The
first three cases are:

- a remark the reviewer marks "fix before merge";
- a remark on a page frozen after publication (release note, migration guide, a
  CHANGELOG section of a published version);
- a missing or generic documentation status under R5 and FOUNDRY-ADR-0018, which is never
  a deferrable remark.

There is no second-level deferral: the non-blocking remarks left by the PR of an issue
that belongs to a companion Epic, `Nits` included, are corrected before the merge. On every pull request,
the review that follows the correction of the non-blocking remarks of a validated round
(every criterion `pass` and quality `mergeable`, even with remarks marked "fix before
merge") covers what changed since that round, not the whole diff (PAT-ADR-0022). It stays
full after a blocking round, when the change since the previous round goes beyond
correcting the remarks it carried, and when the reviewer asks for it with a reason. This
holds for every non-blocking remark, the three cases above included. The partial-review
packet gives the reviewer the previous round's verdict and remarks, the commit range
since that round, and the full diff through the shared verifier; the reviewer checks that
each correction settles its remark without a new defect and that nothing else changed,
and returns a verdict on all criteria for the exact diff. The previous
round's verdict and remarks are declarations of the coordinator that the reviewer cannot
confront with any byte. The commit range is read from the bytes (PAT-283):
`routing prepare-review --previous-head <SHA>` records the head the previous round saw and
makes `reviewer-send.txt` say that the review is partial and give the `--since <SHA>`
argument to add to the `routing read-review` command of the reviewer's role contract, which emits `git diff <SHA>..HEAD` under the claim
checks of the full read (`claimed_review_diff` and `git_range_diff`,
`plugins/foundry/tooling/foundry/routing.py`) and refuses a `--since` that is not a full
40-hex ancestor of HEAD (after a rebase the review is full). The coordinator still chooses
which head it passes: the tool checks that it is an ancestor, not that it is the previous
round's head. On every provider, whether the criteria of the issue are checked in the
tracker or not, the merge gate asks for a review proof covering all criteria for the
exact diff (`valid_for_merge`, called once by `merge` in
`plugins/foundry/tooling/foundry/issue.py`; under the human override, any completed proof
on the same coordinates, `current_for_sync`; PLP-ADR-0064), so after such a partial review that proof
relies on the previous round for the unchanged parts, and the PR description says so;
no mechanical check distinguishes a partial from a full review, and the merge gate and
the proof format are not modified by this rule (PAT-ADR-0018, PAT-ADR-0022).

Four writes are authorized in advance, without human confirmation, inside and outside
an Epic campaign (FOUNDRY-ADR-0013, FOUNDRY-ADR-0016), in every repository where the
plugin is installed. For these four writes this rule gives the confirmation that
`foundry:intake` requires before a write ("3. Apply (human-confirmed)"):

1. create the companion Epic, the first time;
2. create a follow-up issue in it;
3. add deferred remarks to such an issue;
4. attach to the companion Epic a follow-up issue whose creation succeeded but whose
   parent link was not set, with `edit link <FOLLOW-UP-ID> subtask-of <COMPANION-EPIC-ID>`
   (the case, described in `plugins/foundry/docs/review-rounds.md`, "Portability of the
   three writes", where creating an issue under a parent makes two writes and the second
   failed).

The coordinator reports these writes in the PR description. They apply to `Nits` as to
any companion Epic (PAT-ADR-0029). Nothing else is
pre-authorized: the companion Epic and its issues are neither prioritized nor started
without the maintainer (deciding to treat them, and when, is a human verdict under
FOUNDRY-ADR-0014), and every other intake write keeps its confirmation.

The rule names no tracker. Its four writes are operations of the portable contract
`plugins/foundry/docs/tracker-contract.md`, whose table "Core journey" marks the three
rows below `supported` in each of its provider columns:

- row "Frame/intake/groom: create, comment" (`create_issue`, `add_comment`) carries
  writes 1 and 3;
- row "Epics/enfants/dépendances: child creation, relations" (`create_issue(parent=…)`,
  `link(depends-on|blocks|relates)`) carries write 2;
- row "Epics/enfants/dépendances: reparent an existing issue" (`link(subtask-of|parent-of)`)
  carries write 4.

When one of the four writes does not go through, two cases are told apart:

- durable refusal (the project cannot carry the write, for example it has no `Epic`
  type): the coordinator makes no substitute write: the remarks of that round are
  corrected before the merge and then re-reviewed as stated above, and the refusal is
  reported in the PR description;
- transient failure (request quota exhausted, network failure): the coordinator waits
  and retries the write before the merge, and the merge waits for it. It does not correct
  the remarks instead and does not merge first. When the failure leaves the effect of the
  write unknown (a network failure), it first reads the tracker to see whether the write
  happened, and retries only if it did not, so that a retry creates no duplicate. If
  the adapter itself refuses the retry (it cannot replay the write safely), the case is
  treated as a durable refusal: the remarks of that round are corrected before the merge
  and then re-reviewed as stated above, and the refusal is reported in the PR description.

Both are operating choices of this contract (maintainer decision of 2026-10-10; the
refused retry is the coordinator's reading of those two decisions), not decisions of
PAT-ADR-0018; the re-review they mention is the one of PAT-ADR-0022, not part of those
decisions.

PAT-ADR-0018 decides for Epics only; PAT-ADR-0029 extends it to an issue that has no
origin Epic, with `Nits` as destination, and amends the maintainer decision of 2026-10-10
that gave such an issue no deferral. That includes an issue whose direct parent is not an
Epic: the chain of parents is not walked up (maintainer decision of 2026-10-11).

The Foundry skills apply this grouped deferral and this standing authorization in every
repository where the plugin is installed, by the maintainer's decision of 2026-10-10;
this contract is where the rule is recorded. Checklist (a) and correction rule (c) live in
a repository's contract file, so they apply where a repository carries them; deferral (b)
is applied by the Foundry skills, which ship with the plugin, so it applies wherever the
plugin is installed (maintainer decision of 2026-10-11).

**(c) Correction commits.** A correction commit changes as few sentences as possible, and
every new sentence is re-checked against the code before the work is handed back.

**What only the reviewer can judge.** No mechanical check covers: that a sentence cites
the right source and is true; that a procedure was really run; that a correction commit
changed "as few sentences as possible"; that the coordinator rightly classed a remark as
deferrable. The reviewer judges through its "fix before merge" mark; the coordinator then
applies the two other cases (a page frozen after publication, a missing or generic
documentation status), and nobody re-reads that second step before the merge and no gate
checks it: an accepted risk (maintainer decision of 2026-10-11). In an Epic campaign the
reviewer contract (`review_contract`, `plugins/foundry/tooling/foundry/campaign_review.py`)
asks the reviewer to mark those two cases "fix before merge", so the correction loop takes
them; nothing checks that it did.
`plugins/foundry/tests/test_skills.py` and `plugins/foundry/tests/test_process_contract.py`
prove only that these rules are written in the skills and in this contract, not that they
are followed (FOUNDRY-ADR-0018: a mechanical check never proves that documentation is
true). Likewise, neither the "fix before merge" mark nor the existence of the follow-up
issue is seen by any gate: the review proof stays `quality=mergeable`, so the merge
command does not refuse a diff because a marked remark was left uncorrected or because
the follow-up issue does not exist. The checklist is an obligation of the author judged
by the reviewer, not a gate. In the same way no gate checks that the companion Epic of
(b) exists, that it has no tracker link with the origin Epic, or that a PR of a companion
Epic deferred nothing: this is judged in review. Nothing checks either that the fourth
write was made when needed, that the companion Epic has a single follow-up issue, or that
the review after the correction of a validated round was limited to what changed.

**Inside an Epic campaign (PAT-141, PAT-ADR-0028).** The four writes of (b) are made by
the coordinator that applies the skills and, in a campaign run by the cadencer of
PAT-ADR-0026 (`plugins/foundry/tooling/foundry/campaign_effects.py`), by its
`defer-remarks` effect (`plugins/foundry/tooling/foundry/campaign_defer.py`), which
launches no session. The channel is a local receipt distinct from the review proof. The
reviewer session prints its remarks in a `remarks` key, each marked `fix_before_merge`
true or false (`parse_result` in `plugins/foundry/tooling/foundry/campaign_review.py`).
After `record-review-proof`, the review effect runs `record-review-remarks`
(`plugins/foundry/tooling/foundry/routing.py`), which writes the receipt bound to the
proof identifier, the diff hash and the review generation
(`plugins/foundry/tooling/foundry/review_remarks.py`). The `record-review-proof` command
still accepts exactly the keys `outcomes` and `quality`, and neither `issue.py` nor
`write.py` refers to the receipt, so the merge gate never reads it
(`plugins/foundry/tests/test_campaign_defer.py`). After a validated review whose remarks
are not marked "fix before merge", the cadencer decides `defer-remarks` before `merge`
when the direct parent Epic of the ticket is not itself a companion Epic (a title
`Nits <ID>` or exactly `Nits`, `_COMPANION_NAME`); a ticket with no origin Epic defers to the
companion Epic named exactly `Nits` (PAT-291, PAT-ADR-0029; `NO_ORIGIN_COMPANION`,
`NO_ORIGIN_FOLLOW_UP_TITLE` in `campaign_defer.py`), with the same writes, reads and refusals. The
effect reads the tracker before each write, makes the writes through `edit create-issue`,
`edit link` and `edit comment`, posts at most one comment per validated round, never one per
remark, cites every follow-up issue in the PR description and records the deferral in
its attempt journal. A follow-up issue holding `MAX_FOLLOW_UP_REMARKS` (30) remarks or more, counted by
`remark_count` over the comments that open with `DEFERRAL_HEADER` (both in `campaign_defer.py`), is
skipped by `_find_follow_up` like a started one, so the effect creates a new follow-up issue; a
full issue that already carries the comment of the deferral being retried stays usable, so a retry
posts nothing twice (PLP-ADR-0065). The count only sees the comments the adapter's `get_issue` returns (the Linear adapter keeps the 10 most recent ordinary ones, so the cap may never apply there; DevHub returns every comment, observed, DHN-14 in `docs/devhub-requirements.md`; the other adapters unknown): `docs/review-rounds.md`, "Reading the deferred-remarks debt". The following are corrected by the correction loop and
re-reviewed: a remark marked "fix before merge" (except when every such remark of the round
targets the PR description alone: the correction then replaces the description, which is not
re-reviewed because the diff is unchanged, PAT-227, `plugins/foundry/docs/campaign-effects.md`),
a ticket of a companion Epic,
`Nits` included, and a durable refusal (capability absent, `Epic` type refused,
several companion Epics with the same name, found only when the effect lists the project (`_find_companion`, not when `journal_memory` names the companion), an adapter that refuses to replay a
creation). The orphan scan is skipped when the journal names the companion and every earlier attempt of the campaign Epic ended `done` without creating a follow-up (`journal_memory`, `_find_follow_up`; an attempt that ended otherwise may have created one whose answer was lost, and makes the scan run): an orphan follow-up of the same origin Epic created outside that journal (by hand, or by a campaign on another Epic with the same origin) is then not seen, and a second follow-up can be created; under `Nits` the follow-up, and so an unseen orphan, is shared by every ticket of the project with no origin Epic, including those deferred by hand. A transient failure, including a creation that the read shows did not
happen, makes the campaign wait and retry, up to `MAX_DEFER_TRIES` attempts in total for the diff, the
first included (`campaign_defer.py`), then suspend; the wait lasts `DEFER_RETRY_SECONDS`, or until the
`reset_at` that a quota error reports when that is later, at most `MAX_RESET_WAIT_SECONDS` after the
attempt; a creation that failed on quota with a `reset_at` is reconciled by the next pass, not at once. Not changed by PAT-141: the coordinator of
`plugins/foundry/tooling/foundry/campaign_runtime.py` (DevHub) reads only the structured
proof in its review step (`_review_observation`) and has no issue-creation primitive: its
mutating steps are `start`, `open-pr`, `merge`, `close-epic` and `sync-parent-acceptance`
(`_MUTATING_STEPS`), and the `CampaignPipeline` protocol
(`plugins/foundry/tooling/foundry/campaign_coordinator.py`) declares no method that
creates an issue. The campaign preview exists for two providers: `preview_epic`
(`plugins/foundry/tooling/foundry/epic_preview.py`) refuses a tracker whose adapter does
not set `epic_subgraph_supported`, which defaults to `False`
(`plugins/foundry/tooling/foundry/trackers/base.py`) and is set to `True` by the DevHub
and Linear adapters (`plugins/foundry/tooling/foundry/trackers/devhub.py`,
`plugins/foundry/tooling/foundry/trackers/linear.py`). No check proves that a failure is
classed durable or transient rightly: the cadencer reads the text of the error
(`_DURABLE` in `campaign_defer.py`) and treats any other error as transient, bounded by
the same ceiling.
