# Linear tracker adapter

Foundry's Linear adapter is an explicit, single-provider binding. It never selects
Linear from a repository name or discovers a team, project, state, label, milestone, or
issue from display text. The GraphQL endpoint is fixed to
`https://api.linear.app/graphql`; there is no YouTrack fallback.

## Required binding

Store the credential interactively so it never appears in argv, chat, the registry, or
documentation examples:

```text
python3 <plugin-root>/tooling/foundry_cli.py configure credential --name LINEAR_API_TOKEN
python3 <plugin-root>/tooling/foundry_cli.py configure set --tracker linear --codehost github
```

On non-macOS hosts, supply `LINEAR_API_TOKEN` through a secret manager. Register one
repository basename under the `linear` provider with all of these non-secret values:

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

## Exact support boundary

Supported reads are issue search/read, including explicitly mapped states, milestones,
types, and labels. Supported writes are issue creation (with initial priority, estimate,
state, mapped milestone/type/labels, and optional parent), additive
blocking/dependency/related relations, additive comments, and GitHub PR projection as a
Foundry-marked Linear attachment. These operations do not replace an existing issue
value. Readback validates the provider result, but additive writes are not advertised as
exactly-once: concurrent identical callers can create duplicates.

Linear exposes no compare-and-swap precondition for an existing issue. Field and state
replacement, changing the parent of an existing issue, issue-body replacement, and
acceptance-checkbox synchronization are therefore explicitly unavailable. A local lock,
pre-write read, or post-write readback cannot prevent an external writer from being
overwritten, so none is presented as an anti-overwrite guarantee.

Unsupported capabilities fail explicitly with typed errors: existing-issue replacement,
acceptance synchronization, a true ADR knowledge base, atomic audited non-code Epic
closure, project provisioning, and free-form provider-native search queries. `query
issue` still returns the issue and projects the absent ADR knowledge base as a structured
capability status. Foundry's GitHub PR review, CI, human gate, and merge authority are
unchanged; the Linear attachment is projection only.

This implementation and its controlled transport round-trip do not activate a real
workspace. No Linear binding or live write is performed here. Import, provider proof in
the target workspace, and the atomic cutover remain FOUNDRY-159 work.
