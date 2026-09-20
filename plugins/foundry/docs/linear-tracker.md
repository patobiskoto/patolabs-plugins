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
values to stable Linear model UUIDs. A write using an unmapped value is refused; the
adapter never searches by name to make it succeed.

## Exact support boundary

Supported reads and proof-bounded writes are issue search/read, create, priority,
estimate, explicitly mapped milestone/type/labels, state transition, parent/child,
blocking/dependency/related relations, comments, issue body replacement, acceptance
checkbox synchronization, and GitHub PR projection as a Foundry-marked Linear
attachment. Every supported write does a binding read, one intended mutation, and an
exact readback while local Foundry writers are serialized. Linear exposes no CAS
precondition, so an external writer can still race; any observed divergence raises a
conflict and is never retried.

Unsupported capabilities fail explicitly: a true ADR knowledge base, atomic audited
non-code Epic closure, project provisioning, and free-form provider-native search
queries. Foundry's GitHub PR review, CI, human gate, and merge authority are unchanged;
the Linear attachment is projection only.

This implementation and its controlled transport round-trip do not activate a real
workspace. No Linear binding or live write is performed here. Import, provider proof in
the target workspace, and the atomic cutover remain FOUNDRY-159 work.
