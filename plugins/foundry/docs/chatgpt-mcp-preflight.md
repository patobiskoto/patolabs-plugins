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

An owner-designated controlled GitHub PR was then read through the connector.  Metadata
and the direct diff endpoint both returned the documented preflight-file change.  The
PR-triggered workflow first reported `in_progress`; a later bounded read reported its
terminal `completed/success` conclusion.  The legacy combined-status endpoint responded
successfully with no status entries.  This is an observation of the connector's check
reads, not a standalone claim that Foundry's CI gate is green: ADR-0002 still requires
both evidence sources under its own gate semantics.

With the explicit authorization for that PR, the connector published exactly one
neutral `COMMENT` review.  Its body only says that the PR metadata, diff, and checks
were read for FOUNDRY-158 and that the cockpit observation is bounded; it expressly
disclaims approval, request-for-changes, and merge evidence.  The review-list and
discussion readbacks both contained that exact comment, and the review readback state
was `COMMENTED`.  No GitHub issue, push, merge, setting, or other GitHub mutation was
performed.

The remaining proof needs a sanitized receipt format that binds the Linear issue, PR,
exact SHA/diff, both CI evidence sources, test result, and review verdict before an
acceptance update can be attempted.

## Acceptance-criterion status

| AC | Status | Evidence or blocker |
| --- | --- | --- |
| MCP tools, scopes, limits | Partial | The connector families and successful bounded reads above are observed. Write scopes, plan limits, costs, and region are unknown. |
| Controlled Linear lifecycle | Met | Authorized test project plus parent and child issues prove creation, update, project association, parentage, relation, priority, and workflow transition, each with sanitized readback. |
| GitHub PR/diff/checks and review comment | Met | An owner-designated PR's metadata, direct diff, and checks were read; after its workflow reached terminal success, exactly one neutral `COMMENT` review was published and read back. |
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
