"""PAT-131 / PAT-ADR-0017: abandon an issue, read it, close an Epic that has some."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from foundry import edit as edit_cli
from foundry import issue as issue_cli
from foundry import write
from foundry.models import EpicClosureChild, EpicClosureDependency, TransitionContext
from foundry.trackers.base import Tracker, TrackerConflictError
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker

from test_epic_closure_transports import (  # noqa: F401  (autouse intent isolation)
    _isolated_youtrack_epic_closure_intents,
    _linear_graph,
)
from test_linear_tracker import STATE_IDS, connection, proof

COORDS = {"issued_at": 1_800_000_000_000, "nonce": "nonce_1234567890abcdef"}
REVIEW = TransitionContext(
    pr_url="https://github.com/acme/widgets/pull/7", head_sha="a" * 40,
    base_sha="b" * 40, review_digest="c" * 64,
)
DONE = TransitionContext(**{**REVIEW.__dict__, "merge_sha": "d" * 40})


def _graph(monkeypatch, **kwargs):
    tracker, wire, project = _linear_graph(**kwargs)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    return tracker, wire, project


def _add(wire, number, *, state="ready", child_of="LIN-1", required_by=None):
    """Add LIN-<number>, optionally a child of the Epic and a prerequisite of a node."""
    issue_id = f"LIN-{number}"
    raw = copy.deepcopy(wire.issues["LIN-2"])
    raw.update({
        "id": f"00000000-0000-4000-8000-{number:012d}", "identifier": issue_id,
        "state": {"id": STATE_IDS[state], "name": state},
        "updatedAt": "2026-09-20T10:01:00Z", "comments": connection([]),
        "relations": connection([]), "inverseRelations": connection([]),
    })
    wire.issues[issue_id] = raw
    if child_of:
        wire.issues[child_of]["children"]["nodes"].append(
            {"id": raw["id"], "identifier": issue_id}
        )
    if required_by:
        wire.issues[required_by]["inverseRelations"]["nodes"].append(
            {"type": "blocks", "issue": {"id": raw["id"], "identifier": issue_id}}
        )
    return issue_id


def _native(wire, issue_id, state):
    wire.issues[issue_id]["state"] = {"id": STATE_IDS[state], "name": state}


def _started(tracker, wire, project, number, *, upto="in-progress", **kwargs):
    """A child Foundry started (and optionally sent to review), still open."""
    issue_id = _add(wire, number, **kwargs)
    tracker.set_state(issue_id, "in-progress", project=project)
    if upto == "review":
        tracker.set_state(issue_id, "review", context=REVIEW, project=project)
    return issue_id


def _abandon(tracker, project, issue_id, expected):
    return tracker.set_state(
        issue_id, "dropped", context=TransitionContext(expected_state=expected),
        project=project,
    )


def _writes(wire, start=0):
    return [
        call for call in wire.calls[start:]
        if "Create" in call[0] or "Update" in call[0] or "Delete" in call[0]
    ]


def _audits(wire):
    return [
        row for row in wire.comments.values()
        if "foundry-epic-closure.v1" in row["body"]
    ]


def _close(tracker, dropped=None, overrides=None, **kwargs):
    return write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", accept_dropped=dropped,
        accept_overrides=overrides, **{**COORDS, **kwargs},
    )


def _refused(tracker, wire, match, dropped=None, overrides=None):
    before = len(wire.calls)
    with pytest.raises((SystemExit, TrackerConflictError), match=match) as excinfo:
        _close(tracker, dropped, overrides)
    assert not _writes(wire, before) and not _audits(wire)
    return str(excinfo.value)


# --- AC 2 : abandon an existing issue (S1-S4, bounded detection) -----------

@pytest.mark.parametrize("source", ("backlog", "ready", "blocked", "in-progress", "review"))
def test_abandon_succeeds_from_every_open_state_with_one_native_write(monkeypatch, source):
    tracker, wire, project = _graph(monkeypatch)
    if source in {"in-progress", "review"}:
        issue_id = _started(tracker, wire, project, 5, upto=source)
    else:
        issue_id = _add(wire, 5, state=source)
    receipts = copy.deepcopy(wire.issues[issue_id]["comments"])
    before = len(wire.calls)

    result = _abandon(tracker, project, issue_id, source)

    writes = _writes(wire, before)
    assert [call[1]["input"] for call in writes] == [{"stateId": STATE_IDS["dropped"]}]
    assert "FoundryLinearIssueUpdate" in writes[0][0]
    # No receipt is written, deleted or rewritten.
    assert wire.issues[issue_id]["comments"] == receipts
    assert (result.state, result.native_state) == ("dropped", "dropped")
    assert result.pr_url == (REVIEW.pr_url if source == "review" else None)
    # S3: the last provider call is the verification read, after the single write.
    assert "FoundryLinearIssueUpdate" not in wire.calls[-1][0]


def test_abandon_succeeds_from_blocked_after_a_foundry_start(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    _native(wire, issue_id, "blocked")
    result = _abandon(tracker, project, issue_id, "blocked")
    assert (result.state, result.projection_status) == ("dropped", "aligned")


def test_abandon_requires_the_expected_state_before_any_provider_read(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _add(wire, 5)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="état attendu obligatoire"):
        write.transition(tracker, issue_id, "dropped")
    with pytest.raises(TrackerConflictError, match="expected predecessor"):
        tracker.set_state(issue_id, "dropped", project=project)
    with pytest.raises(TrackerConflictError, match="expected predecessor"):
        _abandon(tracker, project, issue_id, "done")
    assert not _writes(wire, before)
    assert wire.issues[issue_id]["state"]["id"] == STATE_IDS["ready"]


def test_abandon_fails_closed_on_a_third_state_without_any_write(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="native state is in-progress, expected ready"):
        _abandon(tracker, project, issue_id, "ready")
    assert not _writes(wire, before)


@pytest.mark.parametrize("case", (
    "native-done", "native-done-no-receipt", "done-receipt-native-regressed",
    "epic-closed", "epic-audit-pending", "invalid-chain",
))
def test_abandon_is_refused_from_done_and_from_an_invalid_chain(monkeypatch, case):
    tracker, wire, project = _graph(monkeypatch)
    issue_id, expected, match = "LIN-2", "in-progress", "native state is done"
    if case == "native-done-no-receipt":
        issue_id = _add(wire, 5, state="done")
    elif case == "done-receipt-native-regressed":
        _native(wire, "LIN-2", "in-progress")
        match = "state-done receipt present"
    elif case == "epic-closed":
        _close(tracker)
        issue_id = "LIN-1"
    elif case == "epic-audit-pending":
        _close(tracker)
        _native(wire, "LIN-1", "in-progress")
        issue_id, match = "LIN-1", "Epic closure audit present"
    elif case == "invalid-chain":
        issue_id = _started(tracker, wire, project, 5)
        node = wire.issues[issue_id]["comments"]["nodes"][0]
        node["id"] = "00000000-0000-4000-8000-0000000000ff"
        match = "comment id invalid"
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match=match):
        _abandon(tracker, project, issue_id, expected)
    with pytest.raises(TrackerConflictError, match=match):
        tracker.update_fields(issue_id, {"State": "dropped"}, project=project)
    assert not _writes(wire, before)


def test_abandon_replay_converges_without_a_second_effect(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    _abandon(tracker, project, issue_id, "in-progress")
    before = len(wire.calls)
    again = _abandon(tracker, project, issue_id, "in-progress")
    assert again.state == "dropped" and not _writes(wire, before)
    # A third state met at replay fails closed, never a second transition.
    _native(wire, issue_id, "ready")
    with pytest.raises(TrackerConflictError, match="native state is ready"):
        _abandon(tracker, project, issue_id, "in-progress")
    assert not _writes(wire, before)


def test_abandon_lost_response_converges_by_readback_without_retry(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    original = wire.__call__

    def transport(document, variables):
        result = original(document, variables)
        if "FoundryLinearIssueUpdate" in document:
            raise OSError("response lost after provider commit")
        return result

    tracker._transport = transport
    before = len(wire.calls)
    assert _abandon(tracker, project, issue_id, "in-progress").state == "dropped"
    assert len(_writes(wire, before)) == 1


def test_abandon_fails_closed_when_the_readback_diverges(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    original = wire.__call__

    def transport(document, variables):
        result = original(document, variables)
        if "FoundryLinearIssueUpdate" in document:
            _native(wire, issue_id, "ready")  # an external writer wins after S2
        return result

    tracker._transport = transport
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="divergent after bounded write"):
        _abandon(tracker, project, issue_id, "in-progress")
    assert len(_writes(wire, before)) == 1  # no automatic retry


def test_set_field_state_dropped_takes_the_same_guarded_path(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    receipts = copy.deepcopy(wire.issues[issue_id]["comments"])
    before = len(wire.calls)
    result = write.set_field(tracker, issue_id, "State", "dropped")
    assert [call[1]["input"] for call in _writes(wire, before)] == [
        {"stateId": STATE_IDS["dropped"]}
    ]
    assert (result.state, result.pr_url) == ("dropped", REVIEW.pr_url)
    assert wire.issues[issue_id]["comments"] == receipts
    # Refused from done through that door too, and never batched with other fields.
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="native state is done"):
        write.set_field(tracker, "LIN-2", "State", "dropped")
    with pytest.raises(ValueError, match="written alone"):
        tracker.update_fields(
            _add(wire, 6), {"State": "dropped", "Priority": "P0"}, project=project,
        )
    assert not _writes(wire, before)


def test_set_field_state_dropped_refuses_a_drift_between_its_two_reads(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _add(wire, 5)
    original, reads = tracker._read_raw, []

    def drifting(read_id):
        reads.append(read_id)
        if len(reads) == 2:
            _native(wire, issue_id, "backlog")
        return original(read_id)

    tracker._read_raw = drifting
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="native state is backlog, expected ready"):
        tracker.update_fields(issue_id, {"State": "dropped"}, project=project)
    assert not _writes(wire, before)


@pytest.mark.parametrize("command", ("transition", "set-field"))
def test_edit_cli_names_the_pr_the_abandon_leaves_open(monkeypatch, capsys, command):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    monkeypatch.setattr(edit_cli.foundry, "tracker", lambda: tracker)
    if command == "transition":
        edit_cli.transition(issue_id, "dropped", "review")
    else:
        edit_cli.set_field(issue_id, "State", "dropped")
    out = capsys.readouterr().out
    assert "PR liée non fermée par l'abandon" in out and REVIEW.pr_url in out


def test_edit_cli_stays_silent_about_a_pr_when_there_is_none(monkeypatch, capsys):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _add(wire, 5)
    monkeypatch.setattr(edit_cli.foundry, "tracker", lambda: tracker)
    edit_cli.transition(issue_id, "dropped", "ready")
    assert "PR liée" not in capsys.readouterr().out


@pytest.mark.parametrize("history", ("never-started", "in-progress", "review"))
def test_lifecycle_writes_stay_refused_on_a_natively_dropped_issue(monkeypatch, history):
    tracker, wire, project = _graph(monkeypatch)
    if history == "never-started":
        issue_id = _add(wire, 5, state="dropped")
    else:
        issue_id = _started(tracker, wire, project, 5, upto=history)
        _abandon(tracker, project, issue_id, history)
    snapshot = copy.deepcopy(wire.issues[issue_id])
    before = len(wire.calls)
    for attempt in (
        lambda: tracker.set_state(issue_id, "in-progress", project=project),
        lambda: tracker.set_state(issue_id, "review", context=REVIEW, project=project),
        lambda: tracker.set_state(issue_id, "done", context=DONE, project=project),
        lambda: tracker.project_acceptance_proof(
            issue_id, "- [ ] acceptance", proof(issue_id, "- [ ] acceptance"),
            checked=1, project=project,
        ),
        lambda: tracker.project_acceptance_override(
            issue_id, "synthetic-proof", REVIEW, project=project,
        ),
    ):
        with pytest.raises(TrackerConflictError):
            attempt()
    assert not _writes(wire, before)
    assert wire.issues[issue_id] == snapshot


def test_only_linear_declares_the_guarded_abandon_capability(monkeypatch):
    assert LinearTracker.guarded_abandon_supported is True
    for provider in (Tracker, YouTrackTracker, GitHubProjectsTracker, DevHubTracker):
        assert provider.guarded_abandon_supported is False

    class Legacy:
        name = "legacy"
        calls = []

        def set_state(self, issue_id, state):
            self.calls.append((issue_id, state))

    monkeypatch.setattr(write, "issue_binding", lambda *_a: None)
    # Other providers keep their current transition: no expected state is asked.
    write.transition(Legacy(), "DEMO-1", "dropped")
    assert Legacy.calls == [("DEMO-1", "dropped")]


# --- AC 3 : a native dropped prevails on read over a non-terminal projection --

@pytest.mark.parametrize("upto", ("in-progress", "review"))
def test_started_then_cancelled_issue_reads_dropped_without_disagreement(monkeypatch, upto):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto=upto)
    _native(wire, issue_id, "dropped")  # cancelled by hand in Linear
    views = (
        tracker.get_issue(issue_id), tracker.observe_issue(issue_id),
        next(item for item in tracker.search(project) if item.id == issue_id),
    )
    for seen in views:
        assert (seen.state, seen.normalized_state, seen.native_state,
                seen.projection_status) == ("dropped", "dropped", "dropped", "aligned")
        assert seen.pr_url == (REVIEW.pr_url if upto == "review" else None)
    # The receipts are kept as they were.
    assert len(wire.issues[issue_id]["comments"]["nodes"]) == (2 if upto == "review" else 1)


def test_never_started_cancelled_issue_still_reads_native_only_dropped(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _add(wire, 5, state="dropped")
    seen = tracker.get_issue(issue_id)
    assert (seen.state, seen.normalized_state, seen.projection_status) == (
        "dropped", None, "native-only",
    )


def test_done_receipt_with_a_native_cancel_stays_a_conflict(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _native(wire, "LIN-2", "dropped")
    with pytest.raises(TrackerConflictError, match="changed outside lifecycle"):
        tracker.get_issue("LIN-2")
    seen = tracker.observe_issue("LIN-2")
    assert (seen.state, seen.native_state, seen.projection_status) == (
        "done", "dropped", "disagreement",
    )


def test_invalid_receipt_chain_with_a_native_cancel_is_not_read_as_dropped(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    _native(wire, issue_id, "dropped")
    wire.issues[issue_id]["comments"]["nodes"][0]["id"] = (
        "00000000-0000-4000-8000-0000000000ff"
    )
    with pytest.raises(TrackerConflictError):
        tracker.get_issue(issue_id)
    seen = tracker.observe_issue(issue_id)
    assert (seen.state, seen.projection_status) == (None, "unknown")


# --- AC 4 : close an Epic whose required graph has abandoned nodes -----------

def test_unnamed_dropped_child_keeps_the_refusal_and_gets_the_exact_command(monkeypatch):
    tracker, wire, project = _graph(monkeypatch, dependency=True)
    first = _started(tracker, wire, project, 5)
    _abandon(tracker, project, first, "in-progress")
    second = _add(wire, 6, state="dropped")
    message = _refused(tracker, wire, "LIN-5 est abandonné, pas accepté")
    assert "LIN-5 (enfant) : abandonné (dropped), acceptable nominativement" in message
    assert "LIN-6 (enfant) : abandonné (dropped), acceptable nominativement" in message
    assert "2 sur 4" in message
    assert (
        "`issue close-epic LIN-1 --human-verdict=accepted "
        f"--accept-dropped={first},{second}`"
    ) in message
    # Naming only a part of the abandoned set is refused the same way.
    partial = _refused(tracker, wire, "LIN-6 est abandonné", dropped=("LIN-5",))
    assert "LIN-5 (enfant) [nommé]" in partial
    assert "--accept-dropped=LIN-5,LIN-6`" in partial


def test_named_dropped_nodes_close_the_epic_and_are_never_counted_accepted(monkeypatch):
    tracker, wire, project = _graph(monkeypatch, dependency=True)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    _abandon(tracker, project, issue_id, "review")

    closed = _close(tracker, dropped=("LIN-5",))

    node = next(child for child in closed.receipt.children if child.id == "LIN-5")
    assert node == EpicClosureChild(id="LIN-5", version=node.version, state="dropped")
    assert (node.acceptance_status, node.ac_done, node.ac_total) == (None, 0, 0)
    assert closed.receipt.accepted_overrides == ()
    assert write.epic_receipt_dropped(closed.receipt) == ("LIN-5",)
    # No new receipt field: the abandoned node lives in the bound graph itself.
    plain = replace(closed.receipt, children=closed.receipt.children[:1])
    assert set(closed.receipt.to_dict()) == set(plain.to_dict())
    assert "accepted_overrides" not in closed.receipt.to_dict()
    assert len(_audits(wire)) == 1 and '"state":"dropped"' in _audits(wire)[0]["body"]
    parent = tracker.get_issue("LIN-1")
    assert (parent.state, parent.projection_status) == ("done", "aligned")
    # The abandoned issue itself is untouched: still dropped, receipts kept.
    assert tracker.get_issue("LIN-5").state == "dropped"
    state = write.epic_closure_state(tracker, "LIN-1")
    assert (state.kind, state.dropped, state.waived) == ("closed", ("LIN-5",), ())


def test_prerequisites_reached_only_through_a_dropped_node_are_not_required(monkeypatch):
    tracker, wire, project = _graph(monkeypatch, dependency=True)
    # LIN-2 depends on LIN-5 (abandoned), which depends on LIN-6 (never done).
    _add(wire, 5, state="dropped", child_of=None, required_by="LIN-2")
    _add(wire, 6, state="ready", child_of=None, required_by="LIN-5")

    closed = _close(tracker, dropped=("LIN-5",))

    edges = {(edge.source_id, edge.target.id) for edge in closed.receipt.dependencies}
    assert edges == {("LIN-2", "LIN-3"), ("LIN-2", "LIN-5")}
    assert all(edge.source_id != "LIN-5" for edge in closed.receipt.dependencies)


def test_prerequisite_reachable_by_another_path_stays_required(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    _add(wire, 5, state="dropped", child_of=None, required_by="LIN-2")
    _add(wire, 6, state="ready", child_of=None, required_by="LIN-5")
    wire.issues["LIN-3"]["inverseRelations"]["nodes"].append(
        {"type": "blocks", "issue": {"id": wire.issues["LIN-6"]["id"], "identifier": "LIN-6"}}
    )
    message = _refused(tracker, wire, "LIN-6 n'est pas terminal", dropped=("LIN-5",))
    assert "LIN-6 (dépendance) : n'est pas terminal" in message
    # A non nominative blocker remains: no command is suggested.
    assert "Commande exacte" not in message


@pytest.mark.parametrize("named, match", (
    (("LIN-5", "LIN-3"), "ne sont pas des nœuds abandonnés du graphe requis : LIN-3"),
    (("LIN-5", "LIN-9"), "ne sont pas des nœuds abandonnés du graphe requis : LIN-9"),
    (("LIN-5", "LIN-6"), "ne sont pas des nœuds abandonnés du graphe requis : LIN-6"),
))
def test_named_set_must_be_exactly_the_dropped_nodes_of_the_required_graph(
    monkeypatch, named, match,
):
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    _add(wire, 5, state="dropped")
    # LIN-6 is abandoned too, but only reachable through LIN-5: not in the required graph.
    _add(wire, 6, state="dropped", child_of=None, required_by="LIN-5")
    message = _refused(tracker, wire, match, dropped=named)
    assert "`issue close-epic LIN-1 --human-verdict=accepted --accept-dropped=LIN-5`" in message
    assert _close(tracker, dropped=("LIN-5",)).receipt.children[1].state == "dropped"


def test_naming_a_node_that_is_not_dropped_is_refused_without_any_dropped_node(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    _refused(tracker, wire, "graphe requis : LIN-3", dropped=("LIN-3",))


def test_exact_replay_converges_and_a_different_set_is_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _add(wire, 5, state="dropped")
    first = _close(tracker, dropped=("LIN-5",))
    before = len(wire.calls)
    again = write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", accept_dropped=("LIN-5",),
    )
    assert again.replayed and again.receipt == first.receipt
    assert again.audit_id == first.audit_id
    for other in (None, ("LIN-5", "LIN-2"), ("LIN-2",)):
        with pytest.raises(SystemExit, match="ensemble --accept-dropped différent"):
            write.close_epic(
                tracker, "LIN-1", human_verdict="accepted", accept_dropped=other,
            )
    assert len(_audits(wire)) == 1 and not _writes(wire, before)


def test_audit_identity_derives_from_the_dropped_set(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _add(wire, 5, state="dropped")
    receipt = _close(tracker, dropped=("LIN-5",)).receipt
    without = replace(receipt, children=receipt.children[:1])
    other = replace(receipt, children=(
        receipt.children[0], replace(receipt.children[1], id="LIN-6"),
    ))
    ids = {LinearTracker._epic_closure_audit(item)[0] for item in (receipt, without, other)}
    assert len(ids) == 3


# Golden values computed on the pre-PAT-131 code (ff9a53a): receipts already written
# keep their identifier, with and without a PAT-ADR-0014 waiver.

def test_existing_plain_closure_audit_id_is_unchanged(monkeypatch):
    tracker, _wire, _project = _graph(monkeypatch)
    plain = _close(tracker)
    canonical = json.dumps(
        plain.receipt.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    assert hashlib.sha256(canonical.encode("ascii")).hexdigest() == (
        "b8c7c7ecad8a1d0cb43b2d32f80a75b58b3275378bdcd73293f5ee07a44e7ad5"
    )
    assert plain.audit_id == (
        "linear:epic:7956f03232777f24d87df1e7ca31b8ecca2c060cb7f7e10020e1801b0fa57a0a"
    )


def test_existing_waiver_closure_audit_id_is_unchanged(monkeypatch):
    tracker, _wire, _project = _graph(
        monkeypatch, acceptance="override", dependency=True, transitive=True,
    )
    assert _close(tracker, overrides=("LIN-2",)).audit_id == (
        "linear:epic:0cbb6f21dc10d2fdf0f55c99c2c68ec105b199862848c90cea2dbdb8176c70c4"
    )


def test_pending_audit_refuses_another_dropped_set_then_resumes_the_exact_one(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    _add(wire, 5, state="dropped")
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
        _close(tracker, dropped=("LIN-5",))
    assert len(_audits(wire)) == 1
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="autre ensemble --accept-dropped"):
        write.close_epic(tracker, "LIN-1", human_verdict="accepted")
    assert not _writes(wire, before)
    state = write.epic_closure_state(tracker, "LIN-1")
    assert (state.kind, state.dropped) == ("pending", ("LIN-5",))
    resumed = write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", accept_dropped=("LIN-5",),
    )
    assert write.epic_receipt_dropped(resumed.receipt) == ("LIN-5",)
    assert len(_audits(wire)) == 1


def test_override_and_dropped_combine_in_the_hint_and_in_the_closure(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch, acceptance="override", dependency=True)
    _add(wire, 5, state="dropped")
    message = _refused(tracker, wire, "LIN-2")
    assert (
        "`issue close-epic LIN-1 --human-verdict=accepted --accept-override=LIN-2 "
        "--accept-dropped=LIN-5`"
    ) in message
    # Each flag only names its own case.
    _refused(tracker, wire, "LIN-5 est abandonné", overrides=("LIN-2", "LIN-5"))
    with pytest.raises(SystemExit, match="à la fois"):
        _close(tracker, dropped=("LIN-5",), overrides=("LIN-5",))
    closed = _close(tracker, dropped=("LIN-5",), overrides=("LIN-2",))
    assert [item.node_id for item in closed.receipt.accepted_overrides] == ["LIN-2"]
    assert write.epic_receipt_dropped(closed.receipt) == ("LIN-5",)


def test_cli_parses_the_flag_and_status_names_the_dropped_set(monkeypatch, capsys):
    tracker, wire, _project = _graph(monkeypatch)
    _add(wire, 5, state="dropped")
    monkeypatch.setattr(issue_cli.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue_cli, "_observe_receipt", lambda *_a, **_k: "")
    before = len(wire.calls)
    for flags in (
        {"--human-verdict=accepted", "--accept-dropped="},
        {"--human-verdict=accepted", "--accept-dropped=LIN-5", "--accept-dropped=LIN-6"},
        {"--human-verdict=accepted", "--accept-dropped=lin-5"},
        {"--human-verdict=accepted", "--accept-dropped=*"},
        {"--accept-dropped=LIN-5"},
        {"--status", "--accept-dropped=LIN-5"},
    ):
        with pytest.raises(SystemExit):
            issue_cli.close_epic("LIN-1", flags=flags)
    assert len(wire.calls) == before
    with pytest.raises(SystemExit) as excinfo:
        issue_cli.close_epic("LIN-1", flags={"--human-verdict=accepted"})
    assert "--accept-dropped=LIN-5`" in str(excinfo.value)
    issue_cli.close_epic(
        "LIN-1", flags={"--human-verdict=accepted", "--accept-dropped=LIN-5"},
    )
    issue_cli.close_epic("LIN-1", flags={"--status"})
    out = capsys.readouterr().out
    assert "nœuds abandonnés liés au reçu : LIN-5" in out
    # A rerun without the set names the audit's exact command.
    with pytest.raises(SystemExit) as excinfo:
        issue_cli.close_epic("LIN-1", flags={"--human-verdict=accepted"})
    assert (
        "issue close-epic LIN-1 --human-verdict=accepted --accept-dropped=LIN-5"
    ) in str(excinfo.value)


def test_node_cancelled_after_the_closure_makes_the_replay_diverge(monkeypatch):
    # Documented limit (PAT-ADR-0017): fail closed, no recovery path is added.
    tracker, wire, _project = _graph(monkeypatch, dependency=True)
    _add(wire, 5, state="dropped")
    _close(tracker, dropped=("LIN-5",))
    _native(wire, "LIN-5", "ready")
    with pytest.raises(TrackerConflictError, match="divergent au rejeu"):
        write.close_epic(
            tracker, "LIN-1", human_verdict="accepted", accept_dropped=("LIN-5",),
        )


# --- AC 5 : what stays refused, even with a legitimate --accept-dropped set ---

def _with_dropped(monkeypatch, **kwargs):
    tracker, wire, project = _graph(monkeypatch, **kwargs)
    _add(wire, 5, state="dropped")
    return tracker, wire, project


def test_still_refused_non_terminal_node(monkeypatch):
    tracker, wire, _project = _with_dropped(monkeypatch)
    _add(wire, 6, state="ready")
    _refused(tracker, wire, "LIN-6 n'est pas terminal", dropped=("LIN-5",))


def test_still_refused_unknown_or_incomplete_proof(monkeypatch):
    tracker, wire, _project = _with_dropped(monkeypatch, acceptance="override")
    wire.issues["LIN-2"]["comments"]["nodes"] = [
        row for row in wire.issues["LIN-2"]["comments"]["nodes"]
        if ":acceptance-override:" not in row["body"]
    ]
    _refused(tracker, wire, "LIN-2", dropped=("LIN-5",))


def test_still_refused_zero_criteria(monkeypatch):
    tracker, wire, _project = _with_dropped(monkeypatch, acceptance="override")
    wire.issues["LIN-2"]["description"] = "no checklist at all"
    message = _refused(
        tracker, wire, "LIN-2.*aucun critère", dropped=("LIN-5",), overrides=("LIN-2",),
    )
    assert "Commande exacte" not in message


def test_still_refused_override_without_its_nominative_waiver(monkeypatch):
    tracker, wire, _project = _with_dropped(monkeypatch, acceptance="override")
    _refused(tracker, wire, "LIN-2", dropped=("LIN-5",))
    # --accept-dropped never stands for a waiver: the override is not a dropped node.
    _refused(tracker, wire, "LIN-2", dropped=("LIN-2", "LIN-5"))


def test_still_refused_foreign_project_node(monkeypatch):
    tracker, wire, _project = _with_dropped(monkeypatch, dependency=True)
    wire.issues["LIN-3"]["project"] = {"id": "other-project", "name": "Other"}
    _refused(tracker, wire, "hors projet|issue_outside_binding|binding", dropped=("LIN-5",))


@pytest.mark.parametrize("mutation", ("reopened-dropped", "dropped-later", "changed"))
def test_still_refused_graph_changed_since_the_read(monkeypatch, mutation):
    tracker, wire, _project = _with_dropped(monkeypatch)
    _add(wire, 6, state="ready")
    _native(wire, "LIN-6", "dropped")
    original = tracker.close_epic

    def mutate_then_close(project_arg, receipt):
        if mutation == "reopened-dropped":
            _native(wire, "LIN-5", "ready")
        elif mutation == "dropped-later":
            _native(wire, "LIN-6", "ready")
        else:
            wire.issues["LIN-5"]["updatedAt"] = "2026-09-20T10:09:00Z"
        return original(project_arg, receipt)

    tracker.close_epic = mutate_then_close
    _refused(tracker, wire, "divergent avant écriture", dropped=("LIN-5", "LIN-6"))


def test_still_refused_epic_without_any_accepted_direct_child(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    wire.issues["LIN-1"]["children"]["nodes"].clear()
    _add(wire, 5, state="dropped")
    _add(wire, 6, state="dropped")
    unnamed = _refused(tracker, wire, "LIN-5 est abandonné")
    assert "Tous les enfants directs sont abandonnés" in unnamed
    assert "Commande exacte" not in unnamed
    named = _refused(
        tracker, wire, "aucun enfant direct n'est done", dropped=("LIN-5", "LIN-6"),
    )
    assert "Tous les enfants directs sont abandonnés" in named


def test_still_refused_dropped_epic(monkeypatch):
    tracker, wire, _project = _with_dropped(monkeypatch)
    _native(wire, "LIN-1", "dropped")
    _refused(tracker, wire, "Epic dropped ne peut pas être clôturé", dropped=("LIN-5",))


def test_validator_refuses_a_forged_dropped_receipt(monkeypatch):
    tracker, wire, project = _graph(monkeypatch, dependency=True)
    _add(wire, 5, state="dropped")
    closed = _close(tracker, dropped=("LIN-5",))
    parent = tracker.get_issue("LIN-1")
    good = closed.receipt
    live, gone = good.children

    def check(receipt, match):
        with pytest.raises(SystemExit, match=match):
            write._validate_epic_outcome(
                replace(closed, receipt=receipt), project=project, parent=parent,
                expected=None,
            )

    # An abandoned coordinate never carries a proof, and is never a waiver.
    check(replace(good, children=(live, replace(
        gone, ac_done=1, ac_total=1, acceptance_status="accepted",
    ))), "coordonnée abandonnée")
    # At least one direct child must be done, replay included.
    check(replace(good, children=(gone,), dependencies=()), "aucun enfant direct n'est done")
    # The sub-graph of an abandoned node is never bound as required.
    check(replace(good, dependencies=(*good.dependencies, EpicClosureDependency(
        source_id="LIN-5", target=live,
    ))), "prérequis d'un nœud abandonné")


# --- AC 6 : Linear only -------------------------------------------------------

def test_only_linear_declares_the_dropped_closure_capability():
    assert LinearTracker.epic_dropped_closure_supported is True
    for provider in (Tracker, YouTrackTracker, GitHubProjectsTracker, DevHubTracker):
        assert provider.epic_dropped_closure_supported is False


def test_dropped_flag_requires_the_accepted_verdict_before_any_read(monkeypatch):
    tracker, wire, _project = _with_dropped(monkeypatch)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="verdict"):
        write.close_epic(tracker, "LIN-1", accept_dropped=("LIN-5",))
    with pytest.raises(SystemExit, match="verdict"):
        write.close_epic(
            tracker, "LIN-1", human_verdict="rejected", accept_dropped=("LIN-5",),
        )
    assert len(wire.calls) == before
