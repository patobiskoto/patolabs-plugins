# Epic campaign cadence: the dry next step (PAT-216, PAT-ADR-0026, PAT-ADR-0027)

One pass of the cadencer of an Epic campaign re-reads the tracker, git and the PR, and
returns exactly one decision. This page covers that computation only
(`tooling/foundry/campaign_cadence.py`). Nothing here executes the decision: no tracker
write, no git mutation, no PR mutation, no session launch. Executing a decision is
[`campaign-effects.md`](campaign-effects.md) (PAT-217, and PAT-218 for `review` and
`correct`). The state lives in the tracker, git and the PR; the module
keeps none. The frozen Dev Hub coordinator (`campaign_coordinator.py`,
`campaign_runtime.py`) is not used.

## Command

```text
FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py campaign-cadence next PAT-100 [--root DIR]
```

It prints one JSON object (`CADENCE_CONTRACT`): the observation (capabilities, structure
digest observed and approved, approval status, the ticket in focus, the tickets) and the
`decision`. The same observation gives byte-identical output (`report`: sorted keys). The
exit code is 2 when a capability is missing, and 0 once a decision is printed. An error that
`observe` does not convert into `node_unreadable` (a network error, an error of the code host,
the `SystemExit` of a tracker binding) is not caught by `main`: the command ends with that
error and a non-zero exit code other than 2 (traceback, or the message of the `SystemExit`).

## Decision table

`decide` is pure over an `Observation`. The first matching row wins.

| Situation | Decision |
| --- | --- |
| a required capability is not declared | `refuse` `capability_missing` |
| a node is unreadable in strict read (`validate_subgraph`, adapter read error) | `suspend` `node_unreadable` |
| approval refusal `structure_digest_mismatch` (observed digest differs from the approved one) | `suspend` `structure_changed` |
| a started or done ticket has an unfinished in-graph `depends-on` (precaution, see below) | `suspend` `dependency_state_behind` |
| several tickets in `in-progress`/`review` | `suspend` `several_in_progress` |
| the ticket's PR is merged but the tracker state is not `done`; PR closed unmerged; several open PRs; head sha unreadable | `suspend` `merged_pr_state_behind`, `pr_closed_unmerged`, `pr_ambiguous`, `pr_head_unreadable` |
| no ticket in progress and none startable, but some not terminal | `suspend` `no_eligible_ticket` |
| any other approval refusal (`absent`, `expired`, `journal_invalid`, `preview_digest_mismatch`) | `refuse` `approval_<code>`, with `blocked_step` = the step the pass would return once approved |
| every required ticket `done`/`dropped` | `await-verdict` |
| ticket `backlog`/`ready`, dependencies `done`, first by numeric id | `start` |
| `in-progress`, branch present, no PR, the last implementation attempt ended on this branch head and the branch has commits beyond the remote default branch | `open-pr` |
| `in-progress`, branch present, no PR, otherwise | `implement` |
| `in-progress`, no branch, no PR | `start` (resume) |
| open PR whose head is not the branch head, the last implementation or correction attempt ended on the branch head | `open-pr` `correction_not_pushed` |
| open PR, no review proof on the exact diff, or diff unobservable, fewer than `MAX_REVIEW_ROUNDS` (3) recorded rounds | `review` |
| the same, at `MAX_REVIEW_ROUNDS` recorded rounds | `suspend` `review_rounds_exhausted` |
| open PR, review proof not mergeable, fewer than `MAX_CORRECTIONS` (2) corrections started | `correct` `review_blocked` |
| valid review whose report on this head and base has remarks marked "fix before merge" (`fix_before_merge` > 0), fewer than `MAX_CORRECTIONS` corrections, and nothing to decide about the deferrable remarks (the rows below come first) | `correct` `review_fix_before_merge` |
| either `correct` at `MAX_CORRECTIONS` corrections | `suspend` `corrections_exhausted` |
| valid review with deferrable remarks (`DeferralFacts.deferrable` > 0, not marked "fix before merge"), no origin Epic, or the origin Epic is a companion Epic, or the last deferral ended in a durable refusal, fewer than `MAX_CORRECTIONS` corrections | `correct` `remarks_not_deferrable` |
| the same remarks, an origin Epic that is not a companion Epic, deferral not done, last attempt transient and inside `DEFER_RETRY_SECONDS` | `wait` `deferral_transient_failure` |
| the same remarks, `MAX_DEFER_TRIES` attempts made | `suspend` `deferral_retries_exhausted` |
| the same remarks, deferral not done otherwise | `defer-remarks` `non_blocking_remarks` |
| valid review, CI checks pending or none proven | `wait-ci` |
| valid review, CI failing | `correct` `ci_failing` |
| valid review, CI green | `merge` |

The remarks facts (`TicketFacts.deferral`, PAT-141) come from the attempt journal and the receipt
state through the `deferral` callable of `observe` (`campaign_defer.deferral_facts`) and, only
when deferrable remarks remain, from two `get_issue` reads through the `origin` callable
(`campaign_defer.origin_of`); without them (the `next` verb) they are absent and no deferral is
decided. The steps of the effect are in [`campaign-effects.md`](campaign-effects.md), section
"The defer-remarks effect".

The three counts (`fix_before_merge`, `review_rounds`, `corrections`) are facts of
`TicketFacts` for the ticket in focus, read from the attempt journal by the `history`
callable passed to `observe` (`campaign_review.history`); without it (the `next` verb) they are
0 and the bounds never fire. `step` also turns a `correct` `ci_failing` into `suspend`
`ci_failing_not_executed` (`campaign-effects.md`); `decide` itself still returns `correct`.
A ticket already `done` costs no PR read: the next ticket is chosen instead.
"Implementation finished" (or a correction finished) is not observable from the tracker,
git and the PR alone, so `open-pr` reads one more fact: the local attempt journal of
[`campaign-effects.md`](campaign-effects.md) (`last_implementation`, `campaign_effects.py`),
passed to `observe` as the `implemented` callable; without it (the `next` verb) the field is
false and the decision stays `implement`. "Required
tickets" are the nodes `epic_preview.classify_nodes` does not classify `omitted` (the
classification `build_preview` uses; the function was extracted from it, unchanged).

## Observation and cost

Order: capabilities (flags only), one `get_epic_subgraph` (plus `validate_subgraph` and
`structure_digest`), the approval journal, then, for the one ticket in focus only,
`git for-each-ref`, `git rev-parse` and `git rev-list --count` (read-only, `_git`), `list_prs`
(or `get_pr` from the tracker PR link) and `get_pr` for each PR that is not open, one
`get_issue` for its body, the proof store, and `ci_gate` (`write.py`, both CI sources),
the last one only when the review is valid. The approval is checked with the preview digest
of the latest journal record of the Epic, for two reasons: the digest cannot be recomputed
here, because the versions of the nodes it covers change at every write of the campaign
(each state, comment or link it writes), and the policy that produced it is not observable.
The structure digest is the observed one, so a structure change is detected exactly. As the
checked preview digest is always one of the journal's own records, `preview_digest_mismatch`
is unreachable on this path (an Epic without a record gives `absent` first).

The re-read of a closed PR is needed because the jq of `GitHubCodeHost.list_prs` selects
`merged`; deduced, not re-verified here: the list endpoint of the code host returns
`merged_at`, not `merged`, so the list cannot tell merged from closed unmerged
(`test_closed_pr_from_a_list_without_merged_is_re_read`).

Cost in tracker requests: the subgraph read (`epic_subgraph.build_neutral_subgraph`, one
`get_issue` per node plus binding validation) plus at most one `get_issue`, plus two more
`get_issue` when the review is valid and deferrable remarks remain (`origin_of`). To measure a
pass: `FOUNDRY_REQUEST_COUNT=stderr FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py
campaign-cadence next <EPIC>` (`REQUEST_COUNT_ENV`, `trackers/linear.py`). The measured
figure is recorded by the issue, not here (unknown until measured).

## Capabilities required (PAT-ADR-0026)

`missing_capabilities` requires `epic_subgraph_supported` and
`append_only_lifecycle_supported` (`trackers/base.py`) and an adapter that overrides
`Tracker.reconcile_issue_creation`. There is no declarative flag for the third one: that is
a gap, so the override is the test. Linear sets the two flags and overrides the method
(`trackers/linear.py`); a tracker missing any of them is refused before any read, including
before the binding is resolved by the CLI.

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
edge and approve again). Contradictions on PR and git facts are
read for the ticket in focus only. A merged PR whose tracker `done` projection is missing
suspends; replaying `issue merge` recovers it (`issue.py`, `recover_done_projection`), and
that is a human decision here.

## Approval and review proof

The approval status is one input: without a valid approval the pass still observes
everything and returns `refuse` with the step it would take. The review proof is read with
`AcceptanceProofStore.valid_for_merge` and `current_for_sync` (`routing.py`) on the exact
diff `git diff <base>...<head>` of the PR (`routing.git_diff`); the store may create its
review directory and `.ledger.lock` in the private data directory and wait on that lock
(`ReviewDeduplicator._locked`, `routing.py`). Tests:
`tests/test_campaign_cadence.py` (one per state, drift, approval, capability, idempotence,
fakes that fail on any effect).
