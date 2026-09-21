# ChatGPT MCP preflight (FOUNDRY-158)

Status: **partial, account-observed preflight**.  This record is deliberately not an
activation of the proposed ChatGPT cockpit or a tracker cutover.  FOUNDRY-ADR-0026 and
FOUNDRY-ADR-0027 are `proposed`; the accepted authority and CI constraints remain
FOUNDRY-ADR-0013 and FOUNDRY-ADR-0002.

## Observation boundary

The following was observed from the connected ChatGPT account on 2026-09-21.  It is a
snapshot of this runtime, not a promise about another account, plan, region, or future
connection.  Identifiers, account profile data, and private URLs are intentionally not
recorded here.

- The GitHub connector was present.  Authenticated profile and accessible-repository
  reads succeeded.  Metadata for the public target repository and the combined commit
  status endpoint also responded successfully.
- Its exposed GitHub surface includes PR fetch/diff and combined-status reads, plus a
  review-comment publication operation.  The connector catalogue also exposes GitHub
  issue creation, branch/ref/file writes, PR creation, and merge operations.
- The Linear connector was present.  Team listing and bounded issue/project searches
  succeeded.  Its exposed surface includes issue, project, and initiative save
  operations, including issue priority, state, parent, project, and relation fields.
- No connector response exposed a plan quota, monetary cost, regional placement, or
  an OAuth-style list of granted read/write scopes.  Those attributes are **unknown**;
  an exposed operation or successful read does not prove a write grant.

The connector catalogue therefore cannot by itself enforce ADR-0027's intended
GitHub allow-list.  In particular, `create issue` and `merge pull request` operations
are exposed in this account runtime.  The cockpit needs a separate fail-closed
allow-list that permits only GitHub reads and review comments; it must never infer that
these operations are unavailable from the account connection alone.

## Controlled-target check

After explicit owner authorization, the minimum dedicated Linear artifacts were created
in the observed team.  Each name starts `FOUNDRY-158 MCP TEST —`; the project summary
and both issue bodies mark them as disposable qualification artifacts retained for
audit.  No pre-existing backlog item was read or changed, and no artifact was deleted,
archived, transferred, or bulk-edited.

The controlled Linear sequence succeeded and was read back through the connector:

1. create a project with one observed team;
2. create a parent issue in that project, initially medium priority and `Todo`;
3. create a child issue in that project with the parent reference, initially low
   priority and `Todo`;
4. update that child to high priority and `In Progress`, append a bounded evidence
   note, and add a `relatedTo` link to the parent; and
5. read the child, parent, and project.  Readback confirmed the project association,
   parent reference, explicit related link, high priority, and `In Progress` transition
   (including the `Todo` to `In Progress` state history).

The first project-detail read used an invalid argument name and the connector rejected
it with schema validation before any side effect.  Repeating the read with its required
`query` argument succeeded.  All authorized Linear creates and updates succeeded; no
write permission was refused or partially applied.

No disposable GitHub PR was designated for publishing a review comment.  No PR fetch,
diff read, or review comment was attempted against an arbitrary existing PR, because a
comment is an external mutation and would not be a controlled scenario.

To continue the proof, a human must provide:

1. a dedicated non-production Linear workspace/team and disposable project or issue;
2. a disposable GitHub PR in the public target repository, with explicit permission to
   publish the test review comment; and
3. a sanitized receipt format that binds the Linear issue, PR, exact SHA/diff, both CI
   evidence sources, test result, and review verdict before an acceptance update.

## Acceptance-criterion status

| AC | Status | Evidence or blocker |
| --- | --- | --- |
| MCP tools, scopes, limits | Partial | The connector families and successful bounded reads above are observed. Write scopes, plan limits, costs, and region are unknown. |
| Controlled Linear lifecycle | Met | Authorized test project plus parent and child issues prove creation, update, project association, parentage, relation, priority, and workflow transition, each with sanitized readback. |
| GitHub PR/diff/checks and review comment | Blocked | Status endpoint read is observed, but no disposable PR was designated and no comment was published. |
| Reject incomplete receipt for AC check | Unproven | No receipt-gated cockpit implementation or controlled receipt exercise was available. |
| Complete receipt updates AC and readback | Blocked | It depends on the preceding receipt contract and an authorized disposable tracker target. |
| Human confirmation for broad actions; no GitHub issue/push/merge | Blocked | The connected catalogue exposes GitHub issue creation and merge.  No cockpit allow-list or confirmation gate was observed. |
| Document operating mode and limits without sensitive data | Met locally | This document records only sanitized observations and explicit unknowns; it does not claim activation. |

## Operating limits for any follow-up

Until the controlled scenarios and receipt contract are proven, treat every tracker
mutation and acceptance update as unavailable.  Keep GitHub issue creation, pushes,
and merges outside the cockpit's permitted operation set.  A CI claim must obey
FOUNDRY-ADR-0002: both check-runs and legacy commit statuses are required, with at
least one real success and no pending or failing result.  Ambiguous or incomplete
evidence leaves the criterion unchecked.
