"""PAT-95 / PAT-ADR-0014: audited, nominative waiver for Epic closure on Linear."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from foundry import issue as issue_cli
from foundry import write
from foundry.models import EpicClosureReceipt, Issue, Link
from foundry.trackers.base import Tracker, TrackerConflictError
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker

from test_epic_closure_transports import (  # noqa: F401  (autouse intent isolation)
    _isolated_youtrack_epic_closure_intents,
    _linear_graph,
)
from test_issue_close_epic import AtomicEpicTracker, BoundedEpicTracker
from test_linear_tracker import STATE_IDS

COORDS = {"issued_at": 1_800_000_000_000, "nonce": "nonce_1234567890abcdef"}


def _graph(monkeypatch, **kwargs):
    tracker, wire, project = _linear_graph(acceptance="override", **kwargs)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    return tracker, wire, project


def _close(tracker, accept=("LIN-2",), **kwargs):
    return write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", accept_overrides=accept,
        **{**COORDS, **kwargs},
    )


def _writes(wire, start=0):
    return [
        call for call in wire.calls[start:]
        if "Create" in call[0] or "Update" in call[0]
    ]


def _audits(wire):
    return [
        row for row in wire.comments.values()
        if "foundry-epic-closure.v1" in row["body"]
    ]


def _forge_override(wire, issue_id, mutate):
    """Re-encode the override comment with a coherent marker/id (hostile receipt)."""
    nodes = wire.issues[issue_id]["comments"]["nodes"]
    for node in nodes:
        if ":acceptance-override:" in node["body"]:
            value = json.loads(node["body"].splitlines()[2].removeprefix("coordinates: "))
            mutate(value["payload"])
            marker, body, comment_id = LinearTracker._lifecycle_marker(
                "acceptance-override", issue_id, value["payload"],
            )
            node["body"], node["id"] = body, comment_id
            return
    raise AssertionError("no override receipt")


# --- AC 2 : flag grammar, refused before any provider read -----------------

@pytest.mark.parametrize("raw", (
    "", ",", "LIN-2,", ",LIN-2", "lin-2", "LIN-2,LIN-2", "*", "LIN-*", "LIN-2, LIN-3",
    " LIN-2", "LIN-0", "LIN-02", "LIN_2", "LIN-2;LIN-3",
))
def test_flag_grammar_refused_before_any_provider_read(monkeypatch, raw):
    tracker, wire, _project = _graph(monkeypatch)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="accept-override"):
        write.close_epic(
            tracker, "LIN-1", human_verdict="accepted", accept_overrides=raw, **COORDS,
        )
    assert len(wire.calls) == before


def test_flag_requires_accepted_verdict_before_any_read(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="verdict"):
        write.close_epic(tracker, "LIN-1", accept_overrides=("LIN-2",))
    with pytest.raises(SystemExit, match="verdict"):
        write.close_epic(
            tracker, "LIN-1", human_verdict="rejected", accept_overrides=("LIN-2",),
        )
    assert len(wire.calls) == before


def test_cli_refuses_ambiguous_or_empty_flag_before_tracker_work(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    monkeypatch.setattr(issue_cli.foundry, "tracker", lambda: tracker)
    before = len(wire.calls)
    for flags in (
        {"--human-verdict=accepted", "--accept-override="},
        {"--human-verdict=accepted", "--accept-override=LIN-2", "--accept-override=LIN-3"},
        {"--human-verdict=accepted", "--accept-override=lin-2"},
        {"--accept-override=LIN-2"},
    ):
        with pytest.raises(SystemExit):
            issue_cli.close_epic("LIN-1", flags=flags)
    assert len(wire.calls) == before


# --- AC 1 : complete read-only diagnostic ----------------------------------

def test_refusal_without_flag_lists_every_blocking_node_with_its_cause(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch, dependency=True, transitive=True)
    # LIN-4 was marked done natively outside the lifecycle (the PAT-52 incident).
    wire.issues["LIN-4"]["comments"]["nodes"] = []
    before = len(wire.calls)
    with pytest.raises(SystemExit) as excinfo:
        write.close_epic(tracker, "LIN-1", human_verdict="accepted", **COORDS)
    message = str(excinfo.value)
    assert "LIN-2 (enfant)" in message and "override valide" in message
    assert "LIN-4 (dépendance)" in message
    assert "Linear native state changed outside lifecycle" in message
    assert "LIN-3" not in message.split("Nœuds du graphe", 1)[1]
    assert "2 sur 3" in message
    assert not _writes(wire, before)


def test_diagnostic_never_stops_at_a_node_whose_read_raises(monkeypatch):
    tracker, wire, project = _graph(monkeypatch, dependency=True)
    wire.issues["LIN-3"]["comments"]["nodes"] = []
    report = write.epic_graph_diagnostic(
        tracker, project, tracker.get_issue("LIN-1"),
    )
    assert [(item["id"], item["code"]) for item in report] == [
        ("LIN-2", "override"), ("LIN-3", "read-error"),
    ]
    assert "native state changed outside lifecycle" in report[1]["cause"]
    assert report[0]["waivable"] and not report[1]["waivable"]


def test_diagnostic_reports_foreign_project_non_terminal_and_zero_criteria(monkeypatch):
    tracker, wire, project = _graph(monkeypatch, dependency=True, transitive=True)
    wire.issues["LIN-3"]["state"] = {"id": STATE_IDS["review"], "name": "Review"}
    wire.issues["LIN-4"]["project"] = {"id": "other-project", "name": "Other"}
    report = {
        item["id"]: item["code"] for item in write.epic_graph_diagnostic(
            tracker, project, tracker.get_issue("LIN-1"),
        )
    }
    assert report["LIN-2"] == "override"
    assert report["LIN-3"] in {"non-terminal", "read-error"}
    # LIN-4 is only reachable through the read-failing LIN-3 when the latter raises;
    # classify the pure node-level causes directly as well.
    base = dict(id="N-1", title="", state="done", ac_total=2, ac_done=0, version=3)
    cases = {
        "dropped": Issue(**{**base, "state": "dropped"}),
        "non-terminal": Issue(**{**base, "state": "review"}),
        "zero-criteria": Issue(**{**base, "ac_total": 0}),
        "unknown": Issue(**base),
    }
    parent = Issue(id="P-1", title="", links=[Link("parent-of", "outward", "N-1")])

    class _Tracker:
        node = None
        foreign = False

        def validate_issue_binding(self, *_a):
            if self.foreign:
                raise ValueError("foreign project")

        def get_issue(self, _id):
            return self.node

    for code, node in cases.items():
        fake = _Tracker()
        fake.node = node
        found = write.epic_graph_diagnostic(fake, None, parent)
        assert [(item["id"], item["code"]) for item in found] == [("N-1", code)]
    fake = _Tracker()
    fake.foreign = True
    assert write.epic_graph_diagnostic(fake, None, parent)[0]["code"] == "foreign-project"


def test_cli_does_not_mask_the_real_conflict_cause(monkeypatch):
    class Boom:
        name = "fixture"

    monkeypatch.setattr(issue_cli.foundry, "tracker", lambda: Boom())

    def raise_conflict(*_a, **_k):
        raise TrackerConflictError("Linear native state changed outside lifecycle")

    monkeypatch.setattr(issue_cli.write, "close_epic", raise_conflict)
    with pytest.raises(SystemExit) as excinfo:
        issue_cli.close_epic("LIN-1", flags={"--human-verdict=accepted"})
    assert "Linear native state changed outside lifecycle" in str(excinfo.value)
    assert "a changé" in str(excinfo.value)


# --- AC 2/4 : nominal flow, receipt, replay --------------------------------

def test_nominal_waiver_binds_the_set_and_leaves_status_override(monkeypatch):
    tracker, wire, project = _graph(monkeypatch, dependency=True, transitive=True)
    closed = _close(tracker)
    override = closed.receipt.accepted_overrides
    assert [(item.node_id, item.reason) for item in override] == [("LIN-2", "synthetic-proof")]
    node = next(child for child in closed.receipt.children if child.id == "LIN-2")
    assert node.acceptance_status == "override"
    assert override[0].receipt_digest == write._epic_override_evidence(node)[0]
    assert len(_audits(wire)) == 1
    audit = _audits(wire)[0]["body"]
    assert '"accepted_overrides":[{"node_id":"LIN-2"' in audit
    # The Epic is aligned and readable; the child's own status is never requalified.
    parent = tracker.get_issue("LIN-1")
    assert (parent.state, parent.projection_status) == ("done", "aligned")
    assert tracker.get_issue("LIN-2").acceptance_status == "override"
    assert closed.receipt.parent_ac_done == 0


def test_exact_replay_converges_without_second_audit_or_write(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    first = _close(tracker)
    before = len(wire.calls)
    again = write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", accept_overrides=("LIN-2",),
    )
    assert again.replayed and again.receipt == first.receipt
    assert again.audit_id == first.audit_id
    assert len(_audits(wire)) == 1 and not _writes(wire, before)


@pytest.mark.parametrize("accept", (None, ("LIN-2", "LIN-3")))
def test_replay_with_a_different_set_is_refused(monkeypatch, accept):
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    _close(tracker)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="ensemble --accept-override différent"):
        write.close_epic(
            tracker, "LIN-1", human_verdict="accepted", accept_overrides=accept,
        )
    assert len(_audits(wire)) == 1 and not _writes(wire, before)


def test_receipt_identity_derives_from_the_waived_set(monkeypatch):
    tracker, _wire, _project = _graph(monkeypatch)
    first = _close(tracker)
    plain = replace(first.receipt, accepted_overrides=())
    other = replace(
        first.receipt,
        accepted_overrides=(replace(first.receipt.accepted_overrides[0], reason="other-reason"),),
    )
    ids = {
        LinearTracker._epic_closure_audit(item)[0]
        for item in (first.receipt, plain, other)
    }
    assert len(ids) == 3


def test_receipt_without_waiver_keeps_its_historical_wire_shape(monkeypatch):
    tracker, wire, project = _linear_graph()
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    closed = write.close_epic(tracker, "LIN-1", human_verdict="accepted", **COORDS)
    assert "accepted_overrides" not in closed.receipt.to_dict()
    assert "accepted_overrides" not in _audits(wire)[0]["body"]
    assert closed.receipt.accepted_overrides == ()
    # A plain rerun after a plain closure still converges.
    assert write.close_epic(tracker, "LIN-1", human_verdict="accepted").replayed


def test_lost_audit_response_recovers_the_exact_waiver_audit(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    original = wire.__call__
    lost = {"value": True}

    def transport(document, variables):
        result = original(document, variables)
        if "FoundryLinearCommentCreate" in document and lost["value"]:
            lost["value"] = False
            raise OSError("response lost after provider commit")
        return result

    tracker._transport = transport
    closed = _close(tracker)
    assert closed.receipt.accepted_overrides[0].node_id == "LIN-2"
    assert len(_audits(wire)) == 1


def test_pending_audit_refuses_another_set_then_resumes_the_exact_one(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    original = wire.__call__
    fail = {"value": True}

    def transport(document, variables):
        if (
            "FoundryLinearIssueUpdate" in document and fail["value"]
            and variables.get("id") == wire.issues["LIN-1"]["id"]
        ):
            fail["value"] = False
            raise OSError("state write lost")
        return original(document, variables)

    tracker._transport = transport
    with pytest.raises(Exception):
        _close(tracker)
    assert len(_audits(wire)) == 1
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="autre ensemble"):
        write.close_epic(tracker, "LIN-1", human_verdict="accepted")
    assert not _writes(wire, before)
    resumed = write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", accept_overrides=("LIN-2",),
    )
    assert resumed.receipt.accepted_overrides[0].node_id == "LIN-2"
    assert len(_audits(wire)) == 1


# --- AC 3 : never acceptable, one hostile test each ------------------------

def _refused(tracker, wire, match, accept=("LIN-2",)):
    before = len(wire.calls)
    with pytest.raises((SystemExit, TrackerConflictError), match=match):
        _close(tracker, accept)
    assert not _writes(wire, before)
    assert not _audits(wire)


def test_hostile_id_outside_the_graph(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _refused(tracker, wire, "hors du graphe.*LIN-9", ("LIN-2", "LIN-9"))


def test_hostile_named_node_is_accepted_not_overridden(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    _refused(tracker, wire, "LIN-3.*accepté, pas dérogé", ("LIN-2", "LIN-3"))


def test_hostile_unnamed_override_node_still_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    wire.issues["LIN-3"]  # LIN-3 stays accepted; name nothing for LIN-2
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="LIN-2"):
        write.close_epic(tracker, "LIN-1", human_verdict="accepted", **COORDS)
    assert not _writes(wire, before)


def test_hostile_unknown_proof_override_receipt_absent(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    wire.issues["LIN-2"]["comments"]["nodes"] = [
        row for row in wire.issues["LIN-2"]["comments"]["nodes"]
        if ":acceptance-override:" not in row["body"]
    ]
    _refused(tracker, wire, "LIN-2")


def test_hostile_zero_criteria(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    wire.issues["LIN-2"]["description"] = "no checklist at all"
    _refused(tracker, wire, "LIN-2.*aucun critère")


def test_hostile_non_terminal_node(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    wire.issues["LIN-2"]["state"] = {"id": STATE_IDS["review"], "name": "Review"}
    _refused(tracker, wire, "LIN-2")


def test_hostile_dropped_node_is_never_waivable():
    node = Issue(
        id="LIN-2", title="", state="dropped", version=3, ac_done=0, ac_total=1,
        acceptance_status="override", acceptance_source="linear-acceptance-override",
        acceptance_coordinates="{}",
    )
    with pytest.raises(SystemExit, match="LIN-2 est dérogé"):
        write._bounded_epic_node(node, role="enfant", waivable=frozenset({"LIN-2"}))


def test_hostile_foreign_project_node(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    wire.issues["LIN-3"]["project"] = {"id": "other-project", "name": "Other"}
    _refused(tracker, wire, "LIN-3.*hors projet ou binding invalide")


def test_hostile_malformed_override_receipt(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    for node in wire.issues["LIN-2"]["comments"]["nodes"]:
        if ":acceptance-override:" in node["body"]:
            node["body"] = node["body"].replace("synthetic-proof", "SYNTHETIC PROOF")
    _refused(tracker, wire, "LIN-2")


def test_hostile_override_bound_to_another_generation(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _forge_override(wire, "LIN-2", lambda payload: payload.update(review_generation=2))
    _refused(tracker, wire, "LIN-2")


def test_hostile_override_bound_to_another_diff(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _forge_override(wire, "LIN-2", lambda payload: payload.update(head_sha="e" * 40))
    _refused(tracker, wire, "LIN-2")


@pytest.mark.parametrize("mutation", ("added", "reopened", "changed"))
def test_hostile_graph_change_after_the_read_fails_closed(monkeypatch, mutation):
    tracker, wire, project = _graph(monkeypatch)
    original = tracker.close_epic

    def mutate_then_close(project_arg, receipt):
        if mutation == "added":
            extra = copy.deepcopy(wire.issues["LIN-2"])
            extra.update({
                "id": "00000000-0000-4000-8000-000000000009", "identifier": "LIN-9",
                "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
                "state": {"id": STATE_IDS["in-progress"], "name": "In progress"},
            })
            wire.issues["LIN-9"] = extra
            wire.issues["LIN-1"]["children"]["nodes"].append(
                {"id": extra["id"], "identifier": "LIN-9"}
            )
        elif mutation == "reopened":
            wire.issues["LIN-2"]["state"] = {"id": STATE_IDS["review"], "name": "Review"}
        else:
            wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T10:09:00Z"
        return original(project_arg, receipt)

    tracker.close_epic = mutate_then_close
    _refused(
        tracker, wire,
        "divergent avant écriture|native state changed outside lifecycle",
    )


def test_replay_rechecks_the_waived_node_after_closure(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _close(tracker)
    wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T10:09:00Z"
    with pytest.raises(TrackerConflictError, match="divergent au rejeu"):
        write.close_epic(
            tracker, "LIN-1", human_verdict="accepted", accept_overrides=("LIN-2",),
        )


def test_forged_receipt_set_is_refused_by_the_validator(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    closed = _close(tracker)
    parent = tracker.get_issue("LIN-1")
    good = closed.receipt
    wrong_digest = replace(
        good,
        accepted_overrides=(replace(good.accepted_overrides[0], receipt_digest="0" * 64),),
    )
    with pytest.raises(SystemExit, match="incohérente"):
        write._validate_epic_outcome(
            replace(closed, receipt=wrong_digest), project=project, parent=parent,
            expected=None,
        )
    ghost = replace(good, accepted_overrides=(
        *good.accepted_overrides,
        replace(good.accepted_overrides[0], node_id="LIN-8"),
    ))
    with pytest.raises(SystemExit, match="absente du graphe"):
        write._validate_epic_outcome(
            replace(closed, receipt=ghost), project=project, parent=parent, expected=None,
        )
    no_waiver = replace(good, accepted_overrides=())
    with pytest.raises(SystemExit, match="sans attestation nominative"):
        write._validate_epic_outcome(
            replace(closed, receipt=no_waiver), project=project, parent=parent,
            expected=None,
        )


# --- AC 5 : Linear only ------------------------------------------------------

@pytest.mark.parametrize("fixture", (AtomicEpicTracker, BoundedEpicTracker))
def test_other_trackers_refuse_the_flag_before_any_read(monkeypatch, fixture):
    tracker = fixture()
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: tracker.project)
    with pytest.raises(SystemExit, match="n'est qualifié que pour Linear"):
        write.close_epic(
            tracker, "DEMO-1", human_verdict="accepted", accept_overrides=("DEMO-2",),
        )
    assert tracker.get_calls == [] and tracker.binding_calls == []
    assert tracker.close_calls == 0


def test_only_linear_declares_the_override_closure_capability():
    assert LinearTracker.epic_override_closure_supported is True
    for provider in (Tracker, YouTrackTracker, GitHubProjectsTracker, DevHubTracker):
        assert provider.epic_override_closure_supported is False


def test_unqualified_provider_without_the_attribute_fails_closed(monkeypatch):
    class Bare:
        name = "bare"
        bounded_epic_closure_supported = True

    with pytest.raises(SystemExit, match="Linear"):
        write.close_epic(
            Bare(), "X-1", human_verdict="accepted", accept_overrides=("X-2",),
        )


def test_receipt_dataclass_default_is_empty():
    fields = EpicClosureReceipt.__dataclass_fields__
    assert fields["accepted_overrides"].default == ()
