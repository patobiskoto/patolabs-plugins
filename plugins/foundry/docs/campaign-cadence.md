# Epic campaign cadence: the dry next step (PAT-216, PAT-ADR-0026, PAT-ADR-0027)

One pass of the cadencer of an Epic campaign re-reads the tracker, git and the PR, and
returns exactly one decision. This page covers that computation only
(`tooling/foundry/campaign_cadence.py`). Nothing here executes the decision: no tracker
write, no git mutation, no PR mutation, no session launch (executing a step and launching
a review are other issues). The state lives in the tracker, git and the PR; the module
keeps none. The frozen Dev Hub coordinator (`campaign_coordinator.py`,
`campaign_runtime.py`) is not used.

## Command

```text
FOUNDRY_TRACKER=linear python3 tooling/foundry_cli.py campaign-cadence next PAT-100 [--root DIR]
```

It prints one JSON object (`CADENCE_CONTRACT`): the observation (capabilities, structure
digest observed and approved, approval status, the ticket in focus, the tickets) and the
`decision`. The same observation gives byte-identical output (`report`: sorted keys). The
exit code is 2 when a capability is missing, 0 otherwise.

## Decision table

`decide` is pure over an `Observation`. The first matching row wins.

| Situation | Decision |
| --- | --- |
| a required capability is not declared | `refuse` `capability_missing` |
| a node is unreadable in strict read (`validate_subgraph`, adapter read error) | `suspend` `node_unreadable` |
| approval refusal `structure_digest_mismatch` (observed digest differs from the approved one) | `suspend` `structure_changed` |
| a started or done ticket has an unfinished in-graph `depends-on` | `suspend` `dependency_state_behind` |
| several tickets in `in-progress`/`review` | `suspend` `several_in_progress` |
| the ticket's PR is merged but the tracker state is not `done`; PR closed unmerged; several open PRs; head sha unreadable | `suspend` `merged_pr_state_behind`, `pr_closed_unmerged`, `pr_ambiguous`, `pr_head_unreadable` |
| no ticket in progress and none startable, but some not terminal | `suspend` `no_eligible_ticket` |
| any other approval refusal (`absent`, `expired`, `journal_invalid`, `preview_digest_mismatch`) | `refuse` `approval_<code>`, with `blocked_step` = the step the pass would return once approved |
| every required ticket `done`/`dropped` | `await-verdict` |
| ticket `backlog`/`ready`, dependencies `done`, first by numeric id | `start` |
| `in-progress`, branch present, no PR | `implement` |
| `in-progress`, no branch, no PR | `start` (resume) |
| open PR, no review proof on the exact diff, or diff unobservable | `review` |
| open PR, review proof not mergeable | `correct` `review_blocked` |
| valid review, CI checks pending or none proven | `wait-ci` |
| valid review, CI failing | `correct` `ci_failing` |
| valid review, CI green | `merge` |

A ticket already `done` costs no PR read: the next ticket is chosen instead.
`implement` also covers opening the PR: "implementation finished" is not deterministically
observable from the tracker, git and the PR, so there is no `open-pr` decision. "Required
tickets" are the nodes `epic_preview.classify_nodes` does not classify `omitted` (the
classification `build_preview` uses; the function was extracted from it, unchanged).

## Observation and cost

Order: capabilities (flags only), one `get_epic_subgraph` (plus `validate_subgraph` and
`structure_digest`), the approval journal, then, for the one ticket in focus only,
`git for-each-ref` (read-only), `list_prs` (or `get_pr` from the tracker PR link), one
`get_issue` for its body, the proof store, and `ci_gate` (`write.py`, both CI sources),
the last one only when the review is valid. The approval is checked with the preview digest
of the latest journal record of the Epic, because the policy that produced the preview
digest is not observable; the structure digest is the observed one, so a structure change
is detected exactly.

Cost in tracker requests: the subgraph read (`epic_subgraph.build_neutral_subgraph`, one
`get_issue` per node plus binding validation) plus at most one `get_issue`. To measure a
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
`review` back to `ready`) is not detectable here. Contradictions on PR and git facts are
read for the ticket in focus only. A merged PR whose tracker `done` projection is missing
suspends; replaying `issue merge` recovers it (`issue.py`, `recover_done_projection`), and
that is a human decision here.

## Approval and review proof

The approval status is one input: without a valid approval the pass still observes
everything and returns `refuse` with the step it would take. The review proof is read with
`AcceptanceProofStore.valid_for_merge` and `current_for_sync` (`routing.py`) on the exact
diff `git diff <base>...<head>` of the PR (`routing.git_diff`); the store may open its
ledger lock file in the private data directory when that directory exists. Tests:
`tests/test_campaign_cadence.py` (one per state, drift, approval, capability, idempotence,
fakes that fail on any effect).
