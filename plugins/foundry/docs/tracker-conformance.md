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
`docs/tracker-contract.v1.json` used by PAT-53 and enforces two different outcomes:

- every **core + supported** provider cell has at least one selected executable
  conformance case;
- every other core cell must remain an explicit `gap` or `to_qualify` cell with
  a PAT owner ticket.

A blocking cell is therefore not silently skipped and is never represented as a
passing conformance case. When PAT-58, PAT-59, PAT-64, PAT-67 or PAT-69 turns one of
those cells into `supported`, the conformance manifest must gain executable coverage
for that provider/operation pair or the public suite fails.

The manifest also pins coverage of the failure classes required by PAT-68:
capabilities, explicit refusals, pagination, permissions, conflicts, ambiguous
effects and replay/resumption. Its adversarial matrix includes repository switching,
near-colliding repository identities, another product's ADR authority, contradictory
global provider configuration, missing credentials, unavailable ADR relations,
registry drift and archived tracker state.

## Evidence boundary

These are deterministic doubles and regression tests. They prove the portable
contract and fail-closed behavior without external side effects; they are **not**
evidence that a real provider accepted the same operations.

The six real configurations — three trackers times Claude Code/Codex — remain the
PAT-61 recipe. PAT-68 must never be cited as a substitute for that recipe.
