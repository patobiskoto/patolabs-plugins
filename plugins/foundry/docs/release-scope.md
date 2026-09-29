# Portable release scope and factual changelog

`foundry_cli.py query changelog <release>` emits
`foundry.release-scope.v1`. A release is an explicitly mapped provider object. It is
distinct from the repository's product project and from an Epic. The query never
selects a release by a near title, an issue-key prefix, or a team-wide scan.

The payload keeps the historical Ship-iOS fields `milestone`, `count` and `groups`.
`count` and `groups` contain only proof-bound `accepted` and explicitly audited
`deviated` work. The additive `scope_count`, `counts`, `categories` and
`release_scope` fields expose every issue in the native scope:

- `accepted`: an exact merged/done lifecycle receipt is bound to review and
  acceptance proof;
- `deviated`: the same exact delivery receipt is bound to an audited acceptance
  override;
- `unfinished`: the issue has no terminal delivery observation;
- `unavailable`: a native terminal state exists but the proof needed to call it
  delivered is absent or invalid.

Every issue fact contains `id`, `title`, `type`, `state`, `labels`, `disposition` and
provider references. Foundry emits no release-note prose. A native `Done`, a closed
issue, or a pull request that merely mentions the issue never counts as delivery.
Ship-IOS may turn the accepted facts into locale-specific copy, but remains isolated
from tracker adapters and credentials.

`release_scope.coordinates` carries the exact provider identity used for the read:
product/project id plus Linear team and ProjectMilestone UUID, YouTrack bundle and enum
value id, or GitHub Project node id plus canonical repository, Milestone number,
database id and node id.

## Stable provider mappings

The active repository binding is the only mapping authority:

| Provider | Product coordinate | Release mapping |
|---|---|---|
| Linear | project UUID plus an exact member of the project's `teams` connection | existing `milestone_ids`: release name to ProjectMilestone UUID |
| YouTrack | project id/key plus `ms_bundle` | `release_ids`: release name to enum-value id in that bundle |
| GitHub Projects | private personal Project node id/owner/number plus canonical repository | `release_ids`: release name to that repository's Milestone number |

The YouTrack issue search uses the provider's documented braced value syntax,
`project: {<key>} Milestone: {<release>}`, including for names that contain spaces.
Values containing literal braces or non-space whitespace are not escaped or guessed:
the read fails before searching. Every returned issue is then checked independently
against the mapped project id and key, enum bundle id, and enum value id and name.

Provider identity readback verifies each mapped native id and exact name. A missing,
unmapped, inaccessible, mismatched or invalid release raises
`ReleaseScopeUnavailableError`; it never becomes a plausible empty changelog. An
existing mapped release with zero issues is a successful, explicit empty scope.
Issue-list pagination is exhausted and duplicate/stalled pages fail closed. GitHub
also requires every repository-milestone issue to belong to the bound product Project.

## Preparing a new release mapping

Create the native release through the provider's bounded operator UI without assigning
issues, then replace the complete repository binding through `registry update`. Do not
edit `registry.json` or `.foundry/tracker.json` separately. The command uses the current
marker's `configuration_digest`, validates the whole candidate, reads the exact product
and release coordinates, updates matching aliases under one lock, republishes the
marker and reads it back:

```text
foundry_cli.py registry selection --require-v1
foundry_cli.py registry update <tracker> <repo> <KEY> <project-id> \
  <expected-configuration-sha256> <complete-existing-k=v-mapping> \
  milestone_ids='{"v1.0.0":"<LINEAR-PROJECT-MILESTONE-UUID>"}'
foundry_cli.py query changelog v1.0.0
```

For YouTrack or GitHub Projects, replace the last extra with
`release_ids='{"v1.0.0":"<NATIVE-ID>"}'` while preserving every other required
binding extra. The final query must return the same project id, release id and name
before PAT-55 assigns any issue. For `patolabs-plugins`, the native Linear
ProjectMilestone `v1.0.0` (`fad607be-a833-46e0-a60e-db1f97fece36`) was created
without assigning tickets. `registry update` published its mapping coherently in
the PAT worktree; `query changelog v1.0.0` then returned the exact ProjectMilestone
coordinate and an explicit zero-issue scope. The active tracker remains Linear.
The supported update promoted the local marker to schema v2: its immutable cutover
manifest digest remains under `activation.manifest_digest`. The historical cutover
attestation still cites the v1 field name as its original evidence; no cutover input
or attestation was rewritten.

## Live read qualification

The PAT-59 recipe also read the existing YouTrack `RE` project (`0-6`) with the
exact `M1-integrite` enum value (`164-66`) in bundle `163-13`: 20 matching issues
were returned. All 20 classify as `unavailable` because their native terminal
state is not an exact Foundry delivery receipt; this is a factual read, not a
claim that those issues were delivered.

In the private `foundry-v1-ghprojects-sandbox` repository, a synthetic open
repository Milestone `v1.0.0` (number `1`, native database ID `18182410`) was
created with no assigned issues. The GitHub Projects adapter verified the native
Milestone, canonical repository and bound personal Project and returned its exact
zero-issue scope using an explicit temporary candidate mapping. That mapping was
not published to the sandbox registry, and the active tracker of
`patolabs-plugins` was not changed. The sandbox Milestone remains open; no
production release was closed.

## Release completion boundary

`release_scope.native_state` reports a provider state when one exists. `closure`
declares a bounded operator action and `read-release-scope` verification. Foundry does
not merge an Epic, publish an application, or infer production delivery from closing a
release. YouTrack's enum and Linear's qualified ProjectMilestone input expose no native
close/status operation. Their operator path therefore requires zero `unfinished` and
zero `unavailable` items, captures the exact factual payload as the scope-freeze record,
reruns the same read, and performs no native mutation. GitHub's repository Milestone can
be closed by an operator and read back as `closed`. No production application release
is closed by this recipe.
