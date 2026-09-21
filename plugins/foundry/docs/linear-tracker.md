# Linear tracker adapter

Foundry's Linear adapter is an explicit, single-provider binding. Runtime reads, queries,
proofs, and writes select the binding by comparing the actual checkout's credential-free
canonical Git remote with `canonical_repo`; `PROJECT_REPO` and repository basenames are
never identity evidence for Linear. It never discovers a team, project, state, label,
milestone, or issue from display text. The GraphQL endpoint is fixed to
`https://api.linear.app/graphql`; there is no YouTrack fallback.

## Required binding

Store the credential interactively so it never appears in argv, chat, the registry, or
documentation examples:

```text
python3 <plugin-root>/tooling/foundry_cli.py configure credential --name LINEAR_API_TOKEN
python3 <plugin-root>/tooling/foundry_cli.py configure set --tracker linear --codehost github
```

On non-macOS hosts, supply `LINEAR_API_TOKEN` through a secret manager. Registry entries
still have a local alias key for storage, but runtime selection ignores that alias and
matches the checkout's canonical remote. Register the `linear` binding with all of these
non-secret values:

- `key`: the Foundry/project ticker used in normalized issue identifiers;
- `id`: the Linear project model UUID;
- `canonical_repo`: exact `host/owner/repository` identity;
- `team_id`: the Linear team model UUID;
- `state_ids`: JSON object mapping every Foundry state (`backlog`, `ready`,
  `in-progress`, `review`, `blocked`, `done`, `dropped`) to a distinct Linear workflow
  state UUID.

Optional JSON maps `milestone_ids`, `type_label_ids`, and `label_ids` bind normalized
values to stable Linear model UUIDs. Every label or milestone returned by Linear must
have an ID in the corresponding map; missing, overlapping, or unknown IDs are binding
errors. Normalized values come from these maps, never from mutable display names. A
create using an unmapped value is refused and the adapter never searches by name.

`registry register` receives scalar extras as `k=v`. Pass each required map as a
shell-quoted JSON object, for example
`state_ids='{"backlog":"<uuid>","ready":"<uuid>"}'`. A value beginning with `{`
must decode to a JSON object; malformed structured input is rejected before the
registry is written. This keeps a manually provisioned Linear binding explicit without
ever placing a credential in the command or registry.

## Exact support boundary

Supported reads are issue search/read, including explicitly mapped states, milestones,
types, labels, and a deliberately closed relation vocabulary. Hierarchy projects a
Linear parent as `subtask-of/inward` and each child as `parent-of/outward`. For a native
`blocks` edge, its source projects `blocks/inward` and its target projects
`depends-on/outward`; native `related` projects `relates/outward` from its source and
`relates/inward` from its target. Those are the only native relation kinds Foundry reads:
`duplicate`, `similar`, or any other kind fails the entire issue/search normalization
through a sanitized Linear provider error instead of returning partial issue data.

Supported writes are issue creation (with initial priority, estimate, state, mapped
milestone/type/labels, and optional parent), non-replacing blocking/dependency/related
relations, and comments. These supported writes round-trip only through the normalized
relations above; duplicate/similar writes are not exposed.

These writes have deliberately narrow delivery semantics. Creation sends one
`issueCreate` with a fresh client UUID and then reads the returned issue; retrying the
command creates a new request and can duplicate the issue. A relation call first observes
whether the relation exists, then sends at most one `issueRelationCreate` and reads back;
concurrent or ambiguous callers can still duplicate a relation. A comment call sends one
`commentCreate` and reads back that returned comment ID; retry after an ambiguous outcome
can duplicate the comment. None of these operations is advertised as exactly-once.

Linear exposes no compare-and-swap precondition for an existing issue. Field and state
replacement, changing the parent of an existing issue, issue-body replacement, and
acceptance-checkbox synchronization are therefore explicitly unavailable. A local lock,
pre-write read, or post-write readback cannot prevent an external writer from being
overwritten, so none is presented as an anti-overwrite guarantee.

Unsupported capabilities fail explicitly with typed errors: existing-issue replacement,
acceptance synchronization, GitHub PR projection, a true ADR knowledge base, atomic
audited non-code Epic closure, project provisioning, and free-form provider-native search
queries. `query issue` still returns the issue and projects the absent ADR knowledge base
as a structured capability status. Because `issue openpr` requires both a `review` state
transition and PR projection, and `issue merge` requires a `done` state transition, both
commands preflight and refuse before any GitHub push, PR creation/update, merge, or branch
deletion when Linear is active. Foundry does not advertise a gated Linear PR lifecycle.

This implementation and its controlled transport round-trip do not activate a real
workspace. No Linear binding or live write is performed here. Import, target-workspace
validation, and the atomic cutover remain FOUNDRY-159 work.
