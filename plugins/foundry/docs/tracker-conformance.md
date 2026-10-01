# Tracker conformance suite (PAT-68)

PAT-68 turns the V1 Tracker contract into a deterministic public test suite without
copying one fake provider harness three times.

## What the suite is

The executable index is
`tests/fixtures/tracker-conformance-v1.json`. Each entry points to an existing
unit test that already exercises a concrete Tracker adapter or the shared repository
binding boundary over fake/in-memory transport. During collection, `conftest.py`
marks those tests with `tracker_conformance`.

Run the exact suite with:

```bash
pytest -q -m tracker_conformance tests
```

Public CI runs that command explicitly. No selected case is an integration test and
the suite does not call a real YouTrack, Linear or GitHub resource.

## Contract coupling

`test_tracker_conformance_v1.py` reads the same
`docs/tracker-contract.v1.json` used by PAT-53 and enforces three outcomes:

- every **core + supported** provider cell has at least one selected executable
  conformance case;
- a test cannot label several providers unless it actually executes the reviewed
  shared scenario for each of them;
- every core `gap` or `to_qualify` cell makes the public conformance command fail,
  even when it has a PAT owner ticket.

A blocking cell is therefore neither skipped nor represented as a passing case. The
owner ticket explains what must land; it is not a green exception. When an owner turns
a cell into `supported`, its executable provider test must be present in the same merged
surface or the gate still fails.

The manifest contains no dormant `pending_ticket` selectors and never names a test that
is absent from the checkout. The PAT-58, PAT-59, PAT-64, PAT-67 and PAT-69 cases were
added only after their adapter implementations and tests landed on `main`.

All 57 core provider cells (19 per tracker) are now `supported` and have selected
executable coverage. The newer cases exercise ADR versions, release scopes, cutover
imports and replay, bounded Epic closure, lifecycle and acceptance receipts, and the
archived-project write guard through the real adapters with fake transports.

The manifest also pins coverage of the failure classes required by PAT-68:
capabilities, explicit refusals, pagination, permissions, conflicts, ambiguous
effects and replay/resumption. Its adversarial matrix includes repository switching,
near-colliding repository identities, another product's ADR authority, contradictory
global provider configuration, missing credentials, unavailable ADR relations,
registry drift and archived tracker state.

The capability case checks the three adapters' bounded Epic-closure declaration. Their
distinct atomic DevHub closure capability remains unavailable. The selected provider
tests exercise bounded closure and replay, while separate refusal cases continue to
verify unavailable optional capabilities without provider side effects.

## Current gate state

On the PAT-64 merge baseline, the exact public command selects 108 passing tests. The
aggregate gate reports no missing supported cell and no core `gap` or `to_qualify`.
The guard is still adversarially tested: deleting a supported case or marking a covered
core cell `to_qualify` makes the command fail. A ticket owner or matrix label alone
never turns an absent core capability into a pass.

## Evidence boundary

These are deterministic doubles and regression tests. They prove the portable
adapter behavior without external side effects; they are **not** evidence that a real
provider accepted the same operations. The green deterministic gate does not replace
the real-provider recipe.

The six real configurations — three trackers times Claude Code/Codex — remain the
PAT-61 recipe. PAT-68 must never be cited as a substitute for that recipe.
