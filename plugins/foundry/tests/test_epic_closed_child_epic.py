"""PAT-185: an Epic whose child is an Epic already closed under a valid audit."""

from __future__ import annotations

import copy
import json

import pytest

from foundry import write
from foundry.trackers.base import TrackerConflictError

from test_epic_closure_transports import _linear_graph
from test_linear_tracker import STATE_IDS, connection

COORDS = {"issued_at": 1_800_000_000_000, "nonce": "nonce_1234567890abcdef"}
OUTER = {"issued_at": 1_800_000_000_001, "nonce": "nonce_outer_1234567890ab"}


def _two_levels(monkeypatch, *, close_inner=True):
    """LIN-5 (outer Epic) has the Epic LIN-1 as its only child; LIN-1 has LIN-2."""
    tracker, wire, project = _linear_graph()
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    if close_inner:
        write.close_epic(tracker, "LIN-1", human_verdict="accepted", **COORDS)
    outer = copy.deepcopy(wire.issues["LIN-1"])
    outer.update({
        "id": "00000000-0000-4000-8000-000000000005",
        "identifier": "LIN-5",
        "state": {"id": STATE_IDS["in-progress"], "name": "In progress"},
        "updatedAt": "2026-09-20T10:01:30Z",
        "comments": connection([]),
        "relations": connection([]),
        "inverseRelations": connection([]),
        "children": connection([{
            "id": wire.issues["LIN-1"]["id"], "identifier": "LIN-1",
        }]),
        "description": "- [ ] Outer validation",
    })
    wire.issues["LIN-5"] = outer
    return tracker, wire, project


def _close_outer(tracker, **kwargs):
    return write.close_epic(
        tracker, "LIN-5", human_verdict="accepted", **{**OUTER, **kwargs},
    )


def _assert_not_closed(wire):
    assert not any(
        "foundry-epic-closure.v1" in node["body"]
        for node in wire.issues["LIN-5"]["comments"]["nodes"]
    )


def _audit(wire, issue_id):
    return next(
        node for node in wire.issues[issue_id]["comments"]["nodes"]
        if "foundry-epic-closure.v1" in node["body"]
    )


def test_a_child_epic_closed_under_a_valid_audit_lets_the_parent_close(monkeypatch):
    tracker, _wire, _project = _two_levels(monkeypatch)
    outcome = _close_outer(tracker)
    (child,) = outcome.receipt.children
    assert (child.id, child.state) == ("LIN-1", "done")
    assert child.acceptance_status == "epic-closed"
    assert json.loads(child.acceptance_coordinates)["audit_id"]
    assert write.epic_closure_state(tracker, "LIN-5").kind == "closed"
    # Replay stays idempotent and re-validates the child's audit.
    assert write.close_epic(tracker, "LIN-5", human_verdict="accepted").replayed


def test_a_child_epic_done_without_audit_is_refused(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch)
    wire.issues["LIN-1"]["comments"]["nodes"] = [
        node for node in wire.issues["LIN-1"]["comments"]["nodes"]
        if "foundry-epic-closure.v1" not in node["body"]
    ]
    with pytest.raises((SystemExit, TrackerConflictError), match="LIN-1"):
        _close_outer(tracker)
    _assert_not_closed(wire)


def test_a_child_epic_with_a_tampered_audit_is_refused(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch)
    node = _audit(wire, "LIN-1")
    node["body"] = node["body"].replace("accepted", "rejected")
    with pytest.raises((SystemExit, TrackerConflictError), match="LIN-1"):
        _close_outer(tracker)
    _assert_not_closed(wire)


def test_a_child_epic_not_closed_is_refused_as_non_terminal(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch, close_inner=False)
    with pytest.raises(SystemExit, match="LIN-1 n'est pas terminal"):
        _close_outer(tracker)
    _assert_not_closed(wire)


def test_a_child_epic_whose_graph_changed_since_its_audit_is_refused(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch)
    # PAT-ADR-0023: a version that goes back (or any real change) is refused.
    wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T09:00:00Z"
    with pytest.raises((SystemExit, TrackerConflictError), match="LIN-1"):
        _close_outer(tracker)
    _assert_not_closed(wire)


def test_a_child_epic_whose_node_version_only_advanced_still_serves(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch)
    wire.issues["LIN-2"]["updatedAt"] = "2026-09-21T10:00:00Z"
    outcome = _close_outer(tracker)
    assert outcome.receipt.children[0].acceptance_status == "epic-closed"
    assert write.close_epic(tracker, "LIN-5", human_verdict="accepted").replayed


def test_a_tracker_without_epic_closure_audit_keeps_the_child_epic_unknown():
    class Bare:
        name = "bare"

    issue = write.Issue(
        id="X-1", title="", state="done", type="Epic", ac_done=0, ac_total=1, version=3,
    )
    with pytest.raises(SystemExit, match="capacité"):
        write._bounded_epic_node(issue, role="enfant", tracker=Bare())


# --- PAT-205: the refusal codes of ``write._closed_child_epic`` and the remaining paths ---

@pytest.mark.parametrize("change,cause", [
    (lambda outcome: {"receipt": {"parent_id": "LIN-9"}}, "audit de clôture ambigu ou sans verdict humain"),
    (lambda outcome: {"receipt": {"human_verdict": "rejected"}}, "audit de clôture ambigu ou sans verdict humain"),
    (lambda outcome: {"audit_id": "not a safe audit id"}, "audit de clôture ambigu ou sans verdict humain"),
    (lambda outcome: {"audit_id": None}, "audit de clôture ambigu ou sans verdict humain"),
])
def test_closed_child_epic_audit_with_a_wrong_parent_verdict_or_id_shape_is_refused(
    monkeypatch, change, cause,
):
    import dataclasses

    tracker, _wire, project = _two_levels(monkeypatch)
    real = tracker.get_epic_closure(project, "LIN-1")
    patch = change(real)
    altered = dataclasses.replace(
        real,
        **({"audit_id": patch["audit_id"]} if "audit_id" in patch else {}),
        **({"receipt": dataclasses.replace(real.receipt, **patch["receipt"])}
           if "receipt" in patch else {}),
    )
    monkeypatch.setattr(tracker, "get_epic_closure", lambda *_a, **_k: altered)
    issue = tracker.get_issue("LIN-1")
    assert write._closed_child_epic(issue, tracker, project) == (None, cause)
    # The valid audit is the control: the same call accepts it.
    monkeypatch.undo()
    assert write._closed_child_epic(issue, tracker, project)[0] == real.audit_id


def test_a_closed_child_epic_named_in_accept_override_keeps_the_historical_refusal(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch)
    with pytest.raises(
        (SystemExit, TrackerConflictError), match="--accept-override LIN-1 .*inacceptable",
    ):
        _close_outer(tracker, accept_overrides=("LIN-1",))
    _assert_not_closed(wire)


def test_a_closed_child_epic_named_in_accept_dropped_is_refused(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch)
    with pytest.raises(
        (SystemExit, TrackerConflictError), match="--accept-dropped .*pas des nœuds abandonnés.*LIN-1",
    ):
        _close_outer(tracker, accept_dropped=("LIN-1",))
    _assert_not_closed(wire)


def test_a_closed_epic_two_levels_down_serves_through_its_own_audit(monkeypatch):
    """LIN-6 -> LIN-5 (closed under its audit) -> LIN-1 (closed under its audit)."""
    tracker, wire, project = _two_levels(monkeypatch)
    _close_outer(tracker)
    top = copy.deepcopy(wire.issues["LIN-5"])
    top.update({
        "id": "00000000-0000-4000-8000-000000000006",
        "identifier": "LIN-6",
        "state": {"id": STATE_IDS["in-progress"], "name": "In progress"},
        "updatedAt": "2026-09-20T10:01:45Z",
        "comments": connection([]),
        "children": connection([{
            "id": wire.issues["LIN-5"]["id"], "identifier": "LIN-5",
        }]),
        "description": "- [ ] Top validation",
    })
    wire.issues["LIN-6"] = top
    outcome = write.close_epic(
        tracker, "LIN-6", human_verdict="accepted",
        issued_at=1_800_000_000_002, nonce="nonce_top_1234567890abcd",
    )
    (child,) = outcome.receipt.children
    assert (child.id, child.acceptance_status) == ("LIN-5", "epic-closed")
    assert write.close_epic(tracker, "LIN-6", human_verdict="accepted").replayed


def test_the_parent_replay_refuses_after_the_child_graph_was_modified(monkeypatch):
    tracker, wire, _project = _two_levels(monkeypatch)
    _close_outer(tracker)
    assert write.close_epic(tracker, "LIN-5", human_verdict="accepted").replayed
    # A version that goes back in the child Epic's graph (PAT-ADR-0023).
    wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T09:00:00Z"
    with pytest.raises(TrackerConflictError, match="graphe Epic Linear divergent au rejeu"):
        write.close_epic(tracker, "LIN-5", human_verdict="accepted")


def test_the_diagnostic_reports_a_closed_child_epic_named_in_accept_override(monkeypatch):
    tracker, _wire, project = _two_levels(monkeypatch)
    parent = tracker.get_issue("LIN-5")
    assert write.epic_graph_diagnostic(tracker, project, parent) == []
    (item,) = write.epic_graph_diagnostic(
        tracker, project, parent, accept=frozenset({"LIN-1"}),
    )
    assert (item["id"], item["code"], item["named"]) == ("LIN-1", "unknown", True)
    assert "--accept-override" in item["cause"]
