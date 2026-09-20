# Delivery posture audit

`foundry_cli.py delivery-audit` compares exactly two registered pilot projects: the
`FOUNDRY` project represented by the active Foundry checkout and the first other
registered project with a canonical GitHub repository binding (stable sort by project
key and repository). It is an inventory, not a gate and not a remediation tool.

The Foundry pilot is derived from the active checkout's canonical Git remote. An optional
`--foundry-repo` value is accepted only when it canonicalizes to that same checkout;
raw remote strings and any user-info never enter a report.

```bash
python3 tooling/foundry_cli.py delivery-audit
python3 tooling/foundry_cli.py delivery-audit --json-only
```

The command performs only `gh api -X GET` calls. It does not write GitHub, the tracker,
the registry, configuration, secrets, branches, workflows, or release state. Its JSON
is deterministic (`sort_keys`, compact encoding) and contains no provider response body,
credential, token, URL with user info, or raw error text.

Each observation has a GitHub REST provenance and exactly one classification:
`present`, `absent-proven`, `inaccessible`, `unsupported`, `disabled`, or `unknown`.
`absent-proven` is reserved for an accessible endpoint with explicit zero evidence (for
example no workflows, releases, or artifacts). Branch-protection and security-control
404 responses remain `unknown`: access and routing ambiguity prevent either response
from proving absence. This audit does not infer `disabled` or `unsupported` unless
provider evidence supports that classification. Auth, permission and rate-limit
failures are `inaccessible`; malformed or unavailable responses are `unknown`. Neither
of those, nor `disabled` or `unsupported`, is a security conclusion.

The inventory covers explicitly labelled CI workflows, exact-default-branch protection,
Dependabot and secret-scanning endpoint availability, release and Actions-artifact
inventories, and explicitly labelled smoke workflows. A workflow not labelled `smoke`
is deliberately not inferred to be one. Workflow discovery is marked `unknown` if its
single response is incomplete (`total_count` differs from the returned workflow list),
or if any returned workflow lacks a non-empty textual `name`, `path`, or `state`;
neither case is reported as an absence. A workflow is active only when its GitHub
`state` is `active`; an inventory containing only `disabled_*` states is `disabled`,
including for an explicitly labelled smoke workflow. Secret-scanning requests use
GitHub's `hide_secret=true` option, so the provider does not return secret values. The
`reusable_controls` list is a prioritized decision aid only: priority 1 means present
in both pilots, priority 2 in one, priority 3 in neither. Disabled workflows do not
count as present; the list implements no shared control.

GitHub's workflow endpoint also lists release, maintenance and arbitrary Actions
automation. CI is therefore reported `present` only for a workflow explicitly labelled
`CI` or `continuous integration` in its name or path. A complete non-empty inventory
without such a label remains `unknown`: it is not evidence either that CI exists or
that it is absent. The same active/disabled state rules then apply to the labelled CI
subset.
