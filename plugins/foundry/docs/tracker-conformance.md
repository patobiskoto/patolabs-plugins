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
is absent from the checkout. Future PAT-58 and PAT-67 cases are added only when those
adapter implementations and tests have actually landed.

After the PAT-66 GitHub core-write integration, all 41 currently `supported` core cells
have executable coverage. The six newly active GitHub cells exercise issue creation and
replay, bounded evolution, exact comment readback, child creation, reciprocal reparenting,
and dependency/relation replay through the real adapter with faked transport.

The manifest also pins coverage of the failure classes required by PAT-68:
capabilities, explicit refusals, pagination, permissions, conflicts, ambiguous
effects and replay/resumption. Its adversarial matrix includes repository switching,
near-colliding repository identities, another product's ADR authority, contradictory
global provider configuration, missing credentials, unavailable ADR relations,
registry drift and archived tracker state.

The capability/refusal case invokes all three real adapter classes with transports that
fail if touched and verifies their common typed, side-effect-free refusal of the still
missing Epic-closure port. That behavioral assertion does not make the missing core
capability pass: the final gate reports it as a blocker for each provider.

## Current gate state

The structural and executable-provider checks pass, but the exact public CI command is
expected to remain red until the provider work is complete. It currently selects 91
tests: 90 pass and the single aggregate gate fails on these 16 contract cells:

| Owner | Remaining core cells |
|---|---|
| PAT-58 | GitHub ADR read, create and status evolution (3) |
| PAT-59 | Linear and GitHub release/changelog scope (2) |
| PAT-64 | GitHub archived-source tombstone; YouTrack/GitHub ADR import; YouTrack/Linear/GitHub live-work copy (6) |
| PAT-67 | GitHub lifecycle transitions and acceptance sync (2) |
| PAT-69 | Epic closure on YouTrack, Linear and GitHub (3) |

This red result is the AC3 behavior. A ticket owner or `gap`/`to_qualify` status never
turns an absent core capability into a pass.

## Evidence boundary

These are deterministic doubles and regression tests. They prove the portable
adapter behavior without external side effects; they are **not** evidence that a real
provider accepted the same operations, and the current red gate means the complete
portable contract has not yet been met.

The six real configurations — three trackers times Claude Code/Codex — remain the
PAT-61 recipe. PAT-68 must never be cited as a substitute for that recipe.
