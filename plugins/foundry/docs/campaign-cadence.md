# Epic campaign cadence: the dry next step (PAT-216, PAT-ADR-0026, PAT-ADR-0027)

One pass of the cadencer of an Epic campaign re-reads the tracker, git and the PR, and
returns exactly one decision. This page covers that computation only
(`tooling/foundry/campaign_cadence.py`). Nothing here executes the decision: no tracker
write, no git mutation, no PR mutation, no session launch. Executing a decision is
[`campaign-effects.md`](campaign-effects.md) (PAT-217, and PAT-218 for `review` and
`correct`). The state lives in the tracker, git, the PR, the local attempt journal
(`campaign_effects.journal_facts`, read-only here), the approval journal and the review
ledger with its acceptance-proof store (`routing.py`); the module keeps none. The frozen Dev
Hub coordinator (`campaign_coordinator.py`, `campaign_runtime.py`) is not used.

## Command

```text
FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py campaign-cadence next PAT-100 [--root DIR]
```

It prints one JSON object (`CADENCE_CONTRACT`): the observation (capabilities, structure
digest observed and approved, approval status, the ticket in focus, the tickets) and the
`decision`. The same observation gives byte-identical output (`report`: sorted keys). The
exit code is 2 when a capability is missing, and 0 once a decision is printed (an invalid
attempt journal prints `suspend` `attempts_journal_invalid`, as `step` does). An error that
`observe` does not convert into `node_unreadable` (a network error, an error of the code
host, the `SystemExit` of a tracker binding) is not caught by `main`: the command ends with
that error and a non-zero exit code other than 2 (traceback, or the message of the
`SystemExit`).

## Decision table

`decide` is pure over an `Observation`. The first matching row wins, except that every
`suspend` and every `await-verdict` is returned before the approval refusal row (`decide`),
including the `suspend` rows listed below it; every other step, `wait-ci` and `wait`
included, is refused for lack of approval.

| Situation | Decision |
| --- | --- |
| a required capability is not declared | `refuse` `capability_missing` |
| a node is unreadable in strict read (`validate_subgraph`, adapter read error) | `suspend` `node_unreadable` |
| approval refusal `structure_digest_mismatch` (observed digest differs from the approved one) | `suspend` `structure_changed` |
| a started or done ticket has an unfinished in-graph `depends-on` (precaution, see below) | `suspend` `dependency_state_behind` |
| several tickets in `in-progress`/`review` | `suspend` `several_in_progress` |
| the ticket's PR is merged but the tracker state is not `done`; PR closed unmerged; several open PRs; head sha unreadable | `suspend` `merged_pr_state_behind`, `pr_closed_unmerged`, `pr_ambiguous`, `pr_head_unreadable` |
| no ticket in progress and none startable, but some not terminal | `suspend` `no_eligible_ticket` |
| every required ticket `done`/`dropped`, whatever the approval status (no effect is requested any more, PAT-235) | `await-verdict` `all_tickets_terminal` |
| any other approval refusal (`absent`, `expired`, `journal_invalid`; `preview_digest_mismatch` is unreachable on this path, see below) | `refuse` `approval_<code>`, with `blocked_step` = the step the pass would return once approved |
| ticket `backlog`/`ready`, dependencies `done`, first by numeric id | `start` |
| `in-progress`, branch present, no PR, the last implementation attempt ended on this branch head and the branch has commits beyond the remote default branch | `open-pr` |
| `in-progress`, branch present, no PR, otherwise | `implement` |
| `in-progress`, no branch, no PR | `start` `in_progress_branch_absent` (resume) |
| `review`, no branch, no PR (a review state implies a PR) | `suspend` `review_state_without_branch_or_pr` |
| open PR whose head is not the branch head, the last implementation or correction attempt ended on the branch head, and the PR head is an ancestor of the branch head (`branch_contains_pr`, PAT-237) | `open-pr` `correction_not_pushed` |
| the same, the PR head is not an ancestor of the branch head (moved on the remote only, diverged, or unknown to the local repository) | `suspend` `pr_head_not_in_branch` |
| open PR, no review proof on the exact diff, or diff unobservable, fewer than `MAX_REVIEW_ROUNDS` (3) recorded rounds | `review` |
| the same, at `MAX_REVIEW_ROUNDS` recorded rounds | `suspend` `review_rounds_exhausted` |
| open PR, the review ledger or a proof is corrupt, invalid or inconsistent (`ProofIntegrityError`, `routing.py`), which is not "no review" | `suspend` `review_proof_integrity` |
| open PR, review proof not mergeable, fewer than `MAX_CORRECTIONS` (2) corrections started | `correct` `review_blocked` |
| valid review whose report on this head and base has remarks marked "fix before merge" (`fix_before_merge` > 0), fewer than `MAX_CORRECTIONS` corrections, and nothing to decide about the deferrable remarks (the rows below come first) | `correct` `review_fix_before_merge` |
| the same, every such remark targets the description (`fix_description` = `fix_before_merge`, PAT-227) and no correction has replaced the description for this head and base | `correct` `review_fix_description` |
| the same, the description already replaced for this head and base | none of the two rows above: the rows below apply |
| either `correct` at `MAX_CORRECTIONS` corrections | `suspend` `corrections_exhausted` |
| valid review with deferrable remarks (`DeferralFacts.deferrable` > 0, not marked "fix before merge"), no origin Epic, or the origin Epic is a companion Epic, or the last deferral ended in a durable refusal, fewer than `MAX_CORRECTIONS` corrections | `correct` `remarks_not_deferrable` |
| the same remarks, an origin Epic that is not a companion Epic, deferral not done, last attempt transient and inside `DEFER_RETRY_SECONDS` | `wait` `deferral_transient_failure` |
| the same remarks, `MAX_DEFER_TRIES` attempts made | `suspend` `deferral_retries_exhausted` |
| the same remarks, deferral not done otherwise | `defer-remarks` `non_blocking_remarks` |
| valid review, CI with no known failure and checks pending or none proven | `wait-ci` |
| valid review, CI with a failing check, even while other checks are pending (PAT-235) | `correct` `ci_failing` |
| valid review, CI green | `merge` |

The `reason` codes of the decisions that do not suspend (`_ticket_step`, `_remarks`,
`_step`): `start` `eligible_dependencies_done` or `in_progress_branch_absent`; `implement`
`started_without_pr`; `open-pr` `implemented_without_pr` or `correction_not_pushed`;
`review` `review_missing` or `review_unobservable`; `correct` `review_blocked`,
`review_fix_before_merge`, `review_fix_description`, `remarks_not_deferrable` or
`ci_failing`; `defer-remarks` `non_blocking_remarks`; `wait` `deferral_transient_failure`;
`wait-ci` `ci_pending` or `ci_not_proven`; `merge` `review_valid_ci_green`; `await-verdict`
`all_tickets_terminal`.

The remarks facts (`TicketFacts.deferral`, PAT-141) come from the attempt journal and the
receipt state through the `deferral` callable of `observe` (`campaign_defer.deferral_facts`)
and, only when deferrable remarks remain, from two `get_issue` reads through the `origin`
callable (`campaign_defer.origin_of`). The `next` verb passes them like `step` does
(`campaign_effects.journal_facts`, PAT-235), so it announces `defer-remarks` where `step`
will do it; `observe` called without them leaves them absent and no deferral is decided. The
steps of the effect are in [`campaign-effects.md`](campaign-effects.md), section "The
defer-remarks effect".

The three counts (`fix_before_merge`, `review_rounds`, `corrections`) are facts of
`TicketFacts` for the ticket in focus, read from the attempt journal by the `history`
callable passed to `observe` (`campaign_review.history`, through `journal_facts` for `next`
and `step`); `observe` called without it leaves them at 0 and the bounds never fire. `step`
also turns a `correct` `ci_failing` into `suspend` `ci_failing_not_executed`
(`campaign-effects.md`); `decide` itself still returns `correct`.
A ticket already `done` costs no PR read: the next ticket is chosen instead.
"Implementation finished" (or a correction finished) is not observable from the tracker,
git and the PR alone, so `open-pr` reads one more fact: the local attempt journal of
[`campaign-effects.md`](campaign-effects.md) (`last_implementation`, `campaign_effects.py`),
passed to `observe` as the `implemented` callable (also by `next`, through `journal_facts`);
`observe` called without it leaves the field false and the decision stays `implement`. "Required
tickets" are the nodes `epic_preview.classify_nodes` does not classify `omitted` (the
classification `build_preview` uses; the function was extracted from it, unchanged).

## Observation and cost

Order: capabilities (flags only), one `get_epic_subgraph` (plus `validate_subgraph` and
`structure_digest`), the approval journal, then, for the one ticket in focus only, `git
for-each-ref`, `git rev-parse` and `git rev-list --count` (read-only, `_git`), `list_prs`
(or `get_pr` from the tracker PR link when no branch is found) and `get_pr` for each PR that
is not open, one `get_issue` for its body, the proof store, and `ci_gate` (`write.py`, both
CI sources), the last one only when the review is valid. The approval is checked with the
preview digest of the latest journal record of the Epic, for two reasons: the digest cannot
be recomputed here, because it covers the version of every node (`snapshot.versions`,
`epic_preview.py`), and these versions change when the campaign writes a node (deduced, not
observed on a tracker), and the policy that produced it is not observable. The structure
digest is the observed one, so a structure change is detected exactly. As the checked
preview digest is always one of the journal's own records, `preview_digest_mismatch` is
unreachable on this path (an Epic without a record gives `absent` first).

The re-read of a closed PR is needed because the jq of `GitHubCodeHost.list_prs` selects
`merged`; deduced, not re-verified here: the list endpoint of the code host returns
`merged_at`, not `merged`, so the list cannot tell merged from closed unmerged
(`test_closed_pr_from_a_list_without_merged_is_re_read`).

Cost in tracker requests: the subgraph read (`epic_subgraph.build_neutral_subgraph`, one
`get_issue` per node plus binding validation) plus at most one `get_issue`, plus two more
`get_issue` when the review is valid and deferrable remarks remain (`origin_of`). To measure
a pass: `FOUNDRY_REQUEST_COUNT=stderr FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py
campaign-cadence next <EPIC>` (`REQUEST_COUNT_ENV`, `trackers/linear.py`). The measured
figures (PAT-228: 19 requests for an Epic of 17 tickets, about one request per node) are in
`docs/linear-request-cost.md`, "Epic campaign (PAT-228)"; the effects `run` also reports the
requests of each pass (`docs/campaign-effects.md`, "Provider requests of a pass"). Every
pass, `wait-ci` included, reads the whole subgraph: the structure digest is checked on every
observation (`campaign_cadence.py`, `observe`; PAT-ADR-0027 defines the drift but not when
it is checked; deduced: a pass cannot know before observing whether it leads to an effect).
PAT-228 leaves that unchanged. The cost of the worst pass (valid review, deferrable remarks,
`origin_of`) and the share of the single `get_issue` of the review read are not measured:
unknown.

The `ticket` objects of the output (`asdict` of `TicketFacts`, `campaign_cadence.py`) carry,
beside the fields of the first version of `CADENCE_CONTRACT` (PAT-216), the fields added
since: `branch_sha`, `ahead`, `implemented` (PAT-217), `fix_before_merge`, `review_rounds`,
`corrections` (PAT-218), `deferral` (PAT-141), `fix_description`, `description_corrected`
(PAT-227), `branch_contains_pr` (PAT-237: `true` or `false` for the ticket in focus when its last
attempt ended on the branch head and the branch head differs from the head of its open PR; `null`
otherwise). `CADENCE_CONTRACT` is unchanged, so a reader that validates the object strictly
must accept them. `review` also takes the value `integrity`. At most one branch is read per
ticket: `_branch_for` takes the first name in sorted order when several branches match the
ticket, with no ambiguity signal (a limit, covered by
`test_branch_names_match_the_issue_id_exactly_and_the_first_sorted_one_wins`).

## Capabilities required (PAT-ADR-0026)

`missing_capabilities` requires `epic_subgraph_supported` and
`append_only_lifecycle_supported` (`trackers/base.py`) and an adapter that overrides
`Tracker.reconcile_issue_creation`. There is no declarative flag for the third one: that is
a gap, so the override is the test. Linear sets the two flags and overrides the method
(`trackers/linear.py`); a tracker missing any of them is refused before any read of the Epic or
resolution of the project: `main` calls `_context`, which first calls `foundry.tracker()`
(without `--root`), then checks the flags.

## Drift and its limit (PAT-ADR-0027)

A change of the structure digest, a node unreadable in strict read, and a node that goes
back are drift: the pass suspends with the motive above. A version or state that moves
forward is not drift (`test_advancing_state_and_version_are_not_drift`).

Limit, stated: the cadencer keeps no previous state, so a regression is detected only from
contradictions visible in one observation: a started or done ticket with an unfinished
`depends-on` in the graph, a merged PR against a tracker state that is not `done`, several
tickets in progress. A node that went back without contradicting another fact (for example
`review` back to `ready`) is not detectable here. `dependency_state_behind` is a precaution,
not drift in the sense of PAT-ADR-0027: without a previous state, a dependency that went
back cannot be told from a dependent that went ahead of it, so it also fires on an
out-of-order forward progress; the way out is human (finish the dependency, or remove the
edge and approve again). Contradictions on PR and git facts are read for the ticket in focus
only. A merged PR whose tracker `done` projection is missing suspends; replaying `issue merge`
recovers it (`issue.py`, `recover_done_projection`), and that is a human decision here.

## Approval and review proof

The approval status is one input: without a valid approval the pass still observes
everything and returns `refuse` with the step it would take. The review proof is read with
`AcceptanceProofStore.valid_for_merge` and `current_for_sync` (`routing.py`) on the exact
diff `git diff <base>...<head>` of the PR (`routing.git_diff`); the store may create its
review directory and `.ledger.lock` in the private data directory and wait on that lock
(`ReviewDeduplicator._locked`, `routing.py`). It may also rewrite a ledger marker that holds
only the legacy `{"diff_hash": ...}` into a completed record: `completed_proof_binding`
calls `_record_for`, which calls `_read_record` with `migrate_bare_legacy=True`
(`routing.py`). A persisted ledger record or proof that cannot be read, or whose shape,
claim publication, claim id, coordinates, head or content is invalid or inconsistent, raises
`ProofIntegrityError` (a subclass of `RoutingConfigError`; `_read_record`,
`_completed_proof_binding_from_record`, `completed_proof_binding`, proof validation in
`routing.py`) and `_review_status` returns `integrity`: `suspend` `review_proof_integrity`.
`current_for_sync` validates every proof marker before it filters by issue, so a corrupt
proof of another issue suspends the pass for the ticket in focus too (`issue merge` already
fails closed in the same state, as `valid_for_merge` calls it). An advisory or terminal
postmerge record found in the proof directory is the same `ProofIntegrityError`, requalified
at the read path (`current_for_sync`), not in `_validate_proof`, which tracker adapters also
call on caller input. An absent ledger marker, or an absent, stale, ambiguous or
non-mergeable proof, stays a plain `RoutingConfigError` (`none` or `blocked`). Known limit:
while the acceptance-proof directory does not exist, `current_for_sync` raises a plain
`RoutingConfigError` before it reads the ledger, so a corrupt ledger marker is then read as
no review. Tests: `tests/test_campaign_cadence.py` (one per state, drift, approval,
capability, idempotence, fakes that fail on any effect).
