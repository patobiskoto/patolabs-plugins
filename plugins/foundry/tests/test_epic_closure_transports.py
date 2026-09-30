"""Provider-transport proofs for PAT-69's bounded Epic closure."""

from __future__ import annotations

import copy

import pytest

from foundry import write
from foundry.models import Project, TransitionContext
from foundry.trackers.base import TrackerConflictError
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker

from test_linear_tracker import LinearWire, PROJECT, STATE_IDS, connection, proof


@pytest.fixture(autouse=True)
def _isolated_youtrack_epic_closure_intents(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "foundry.trackers.youtrack.registry.data_dir", lambda: str(tmp_path),
    )


class FaithfulLinearWire(LinearWire):
    """The existing GraphQL double, with provider timestamps advancing on writes."""

    def _apply(self, issue, values):
        super()._apply(issue, values)
        issue["updatedAt"] = "2026-09-20T10:02:00Z"

    def __call__(self, document, variables):
        result = super().__call__(document, variables)
        if "FoundryLinearCommentCreate" in document:
            issue = self._by_native(variables["input"]["issueId"])
            issue["updatedAt"] = "2026-09-20T10:02:00Z"
        return result


def _linear_graph(*, acceptance="proof", dependency=False):
    wire = FaithfulLinearWire()
    project = Project(
        key=PROJECT.key,
        id=PROJECT.id,
        extra={**PROJECT.extra, "type_label_ids": {"Epic": "label-epic"}},
    )
    parent, child = wire.issues["LIN-1"], wire.issues["LIN-2"]
    parent.update({"description": "- [ ] Epic validation", "state": {"id": STATE_IDS["in-progress"], "name": "In progress"}})
    parent["labels"] = connection([{"id": "label-epic", "name": "Epic"}])
    parent["children"] = connection([{"id": child["id"], "identifier": "LIN-2"}])
    child.update({"description": "- [ ] acceptance", "state": {"id": STATE_IDS["in-progress"], "name": "In progress"}})
    child["labels"] = connection([])
    tracker = LinearTracker(token="test", transport=wire)
    tracker._activate(project)
    review = TransitionContext(
        pr_url="https://github.com/acme/widgets/pull/2", head_sha="a" * 40,
        base_sha="b" * 40, review_digest="c" * 64,
    )
    def deliver(issue_id, *, accepted=True):
        tracker.set_state(issue_id, "review", context=review, project=project)
        if accepted:
            tracker.project_acceptance_proof(
                issue_id, "- [ ] acceptance", proof(issue_id, "- [ ] acceptance"),
                checked=1, project=project,
            )
        else:
            tracker.project_acceptance_override(
                issue_id, "synthetic-proof", review, project=project,
            )
        tracker.set_state(
            issue_id, "done", context=TransitionContext(
                **{**review.__dict__, "merge_sha": "d" * 40},
            ), project=project,
        )

    deliver("LIN-2", accepted=acceptance == "proof")
    if dependency:
        extra = copy.deepcopy(wire.issues["LIN-2"])
        extra.update({
            "id": "00000000-0000-4000-8000-000000000003",
            "identifier": "LIN-3",
            "state": {"id": STATE_IDS["in-progress"], "name": "In progress"},
            "updatedAt": "2026-09-20T10:01:00Z",
            "comments": connection([]),
            "relations": connection([]),
            "inverseRelations": connection([]),
        })
        wire.issues["LIN-3"] = extra
        wire.issues["LIN-2"]["inverseRelations"] = connection([{
            "type": "blocks",
            "issue": {"id": extra["id"], "identifier": "LIN-3"},
        }])
        deliver("LIN-3")
    return tracker, wire, project


def test_linear_bounded_close_roundtrips_through_graphql_and_replays(monkeypatch):
    tracker, wire, project = _linear_graph()
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)

    closed = write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )
    replay = write.close_epic(tracker, "LIN-1", human_verdict="accepted")

    assert closed.closed_parent_version > closed.receipt.parent_version
    assert (closed.receipt.parent_ac_done, closed.receipt.parent_ac_total) == (0, 1)
    assert closed.receipt.parent_validation_digest == write.epic_parent_validation_digest(
        tracker.observe_issue("LIN-1")
    )
    assert replay.replayed is True
    assert sum("foundry-epic-closure.v1" in item["body"] for item in wire.comments.values()) == 1
    # Two setup transitions belong to the child; replay adds no parent update.
    assert sum("FoundryLinearIssueUpdate" in call[0] for call in wire.calls) == 3


def test_linear_epic_without_own_validation_criteria_refuses_before_effect(monkeypatch):
    tracker, wire, project = _linear_graph()
    wire.issues["LIN-1"]["description"] = ""
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    before = len(wire.calls)

    with pytest.raises(SystemExit, match="critères propres"):
        write.close_epic(tracker, "LIN-1", human_verdict="accepted")

    assert not any(
        "Create" in document or "Update" in document
        for document, _args in wire.calls[before:]
    )


def test_linear_lost_comment_response_recovers_exact_deterministic_audit(monkeypatch):
    tracker, wire, project = _linear_graph()
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    original = wire.__call__
    lost = {"value": True}

    def response_lost(document, variables):
        result = original(document, variables)
        if "FoundryLinearCommentCreate" in document and lost["value"]:
            lost["value"] = False
            raise OSError("response lost after provider commit")
        return result

    tracker._transport = response_lost
    closed = write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )
    assert closed.audit_id.startswith("linear:epic:")
    assert sum("foundry-epic-closure.v1" in item["body"] for item in wire.comments.values()) == 1


@pytest.mark.parametrize("mutation", ("reopened", "version", "added"))
def test_linear_done_replay_rechecks_complete_current_graph(monkeypatch, mutation):
    tracker, wire, project = _linear_graph()
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    write.close_epic(
        tracker, "LIN-1", human_verdict="accepted", issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )
    if mutation == "added":
        extra = copy.deepcopy(wire.issues["LIN-2"])
        extra.update({"id": "00000000-0000-4000-8000-000000000003", "identifier": "LIN-3"})
        wire.issues["LIN-3"] = extra
        wire.issues["LIN-1"]["children"]["nodes"].append({"id": extra["id"], "identifier": "LIN-3"})
    elif mutation == "reopened":
        wire.issues["LIN-2"]["state"] = {"id": STATE_IDS["review"], "name": "Review"}
    else:
        wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T10:03:00Z"
    with pytest.raises(TrackerConflictError, match="graphe Epic Linear divergent au rejeu"):
        write.close_epic(tracker, "LIN-1", human_verdict="accepted")


class _YouTrackWire(YouTrackTracker):
    def __init__(self):
        super().__init__(url="https://youtrack.invalid", token="test")
        self.calls = []
        self.rows = {
            "YT-1": self._row("YT-1", "Epic", "in-progress", 10),
            "YT-2": self._row("YT-2", "Feature", "done", 9),
        }
        self.rows["YT-1"]["links"] = [{"linkType": {"name": "parent for"}, "direction": "OUTWARD", "issues": [{"idReadable": "YT-2"}]}]

    @staticmethod
    def _row(identifier, kind, state, updated):
        return {"idReadable": identifier, "summary": identifier, "description": "- [x] acceptance", "created": 1, "updated": updated,
                "project": {"id": "p", "shortName": "YT"}, "links": [], "comments": [],
                "customFields": [{"name": "State", "value": {"name": state}}, {"name": "Type", "value": {"name": kind}}]}

    def _req(self, method, path, body=None, fields=None, top=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "GET" and path.startswith("/issues/"):
            return copy.deepcopy(self.rows[path.split("/")[2]])
        if method == "POST" and path.startswith("/issues/") and not path.endswith("/comments"):
            row = self.rows[path.split("/")[2]]
            row["customFields"][0]["value"] = {"name": "done"}
            row["updated"] += 1
            return {"idReadable": row["idReadable"]}
        if method == "POST" and path.endswith("/comments"):
            row = self.rows[path.split("/")[2]]
            row["comments"].append({"text": body["text"], "created": 2})
            return {"id": "comment"}
        raise AssertionError((method, path, body, fields, top))


def test_youtrack_close_audits_before_targeted_parent_write_and_replays(monkeypatch):
    tracker = _YouTrackWire()
    project = Project(key="YT", id="p")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)

    closed = write.close_epic(
        tracker, "YT-1", human_verdict="accepted", issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )
    replay = write.close_epic(tracker, "YT-1", human_verdict="accepted")
    effects = [(method, path) for method, path, _body in tracker.calls if method == "POST"]
    assert effects == [("POST", "/issues/YT-1/comments"), ("POST", "/issues/YT-1")]
    assert replay.replayed and closed.audit_id.startswith("foundry-epic-closure.v1:")


@pytest.mark.parametrize("explicit_replay", [False, True])
def test_youtrack_hidden_audit_effect_blocks_replay_without_second_post(
    monkeypatch, explicit_replay,
):
    tracker = _YouTrackWire()
    project = Project(key="YT", id="p")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    original = tracker._req
    lost = {"value": True}

    def hidden_comment_effect(method, path, body=None, fields=None, top=None):
        result = original(method, path, body, fields, top)
        if method == "POST" and path == "/issues/YT-1/comments" and lost["value"]:
            lost["value"] = False
            # The provider accepted the append, but its subsequent projection does
            # not expose that effect to Foundry, so the response cannot be resolved.
            tracker.rows["YT-1"]["comments"].clear()
            raise OSError("comment response lost and effect not observable")
        return result

    tracker._req = hidden_comment_effect
    with pytest.raises(OSError, match="effect not observable"):
        write.close_epic(
            tracker, "YT-1", human_verdict="accepted",
            issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
        )
    replay = _YouTrackWire()
    replay.rows = tracker.rows
    replay_coordinates = (
        {"issued_at": 1_800_000_000_000, "nonce": "nonce_1234567890abcdef"}
        if explicit_replay else {}
    )
    with pytest.raises(TrackerConflictError, match="no second POST"):
        write.close_epic(
            replay, "YT-1", human_verdict="accepted",
            **replay_coordinates,
        )

    assert sum(
        method == "POST" and path == "/issues/YT-1/comments"
        for method, path, _body in [*tracker.calls, *replay.calls]
    ) == 1
    assert not any(
        method == "POST" and path == "/issues/YT-1"
        for method, path, _body in [*tracker.calls, *replay.calls]
    )


def test_youtrack_lost_audit_response_recovers_immediately_visible_effect(
    monkeypatch,
):
    tracker = _YouTrackWire()
    project = Project(key="YT", id="p")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    original = tracker._req
    lost = {"value": True}

    def response_lost_after_append(method, path, body=None, fields=None, top=None):
        result = original(method, path, body, fields, top)
        if method == "POST" and path == "/issues/YT-1/comments" and lost["value"]:
            lost["value"] = False
            raise OSError("comment response lost after provider append")
        return result

    tracker._req = response_lost_after_append
    closed = write.close_epic(
        tracker, "YT-1", human_verdict="accepted",
        issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
    )
    replayed = write.close_epic(tracker, "YT-1", human_verdict="accepted")

    assert closed.receipt == replayed.receipt
    assert replayed.replayed is True
    assert sum(
        method == "POST" and path == "/issues/YT-1/comments"
        for method, path, _body in tracker.calls
    ) == 1


def test_youtrack_delayed_audit_visibility_resumes_exact_receipt_without_repost(
    monkeypatch,
):
    tracker = _YouTrackWire()
    project = Project(key="YT", id="p")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    original = tracker._req
    hidden = {"comment": None}

    def delayed_comment_projection(method, path, body=None, fields=None, top=None):
        result = original(method, path, body, fields, top)
        if method == "POST" and path == "/issues/YT-1/comments":
            hidden["comment"] = tracker.rows["YT-1"]["comments"].pop()
            raise OSError("comment response lost before projection caught up")
        return result

    tracker._req = delayed_comment_projection
    with pytest.raises(OSError, match="projection caught up"):
        write.close_epic(
            tracker, "YT-1", human_verdict="accepted",
            issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
        )
    assert hidden["comment"] is not None

    tracker.rows["YT-1"]["comments"].append(hidden["comment"])
    replay = _YouTrackWire()
    replay.rows = tracker.rows
    closed = write.close_epic(replay, "YT-1", human_verdict="accepted")

    assert closed.receipt.nonce == "nonce_1234567890abcdef"
    assert sum(
        method == "POST" and path == "/issues/YT-1/comments"
        for method, path, _body in [*tracker.calls, *replay.calls]
    ) == 1
    assert sum(
        method == "POST" and path == "/issues/YT-1"
        for method, path, _body in [*tracker.calls, *replay.calls]
    ) == 1


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
@pytest.mark.parametrize("phase", ("pending", "done"))
def test_epic_parent_procedure_change_blocks_replay_without_second_write(
    monkeypatch, provider, phase,
):
    if provider == "linear":
        tracker, wire, project = _linear_graph()
        parent_id = "LIN-1"
        original = wire.__call__

        def fail_state(document, variables):
            if (
                phase == "pending"
                and "FoundryLinearIssueUpdate" in document
                and variables.get("id") == wire.issues[parent_id]["id"]
            ):
                raise OSError("state not applied")
            return original(document, variables)

        tracker._transport = fail_state
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        parent_id = "YT-1"
        original = tracker._req

        def fail_state(method, path, body=None, fields=None, top=None):
            if phase == "pending" and method == "POST" and path == "/issues/YT-1":
                raise OSError("state not applied")
            return original(method, path, body, fields, top)

        tracker._req = fail_state
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)

    if phase == "pending":
        with pytest.raises(Exception):
            write.close_epic(
                tracker, parent_id, human_verdict="accepted",
                issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
            )
    else:
        write.close_epic(
            tracker, parent_id, human_verdict="accepted",
            issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
        )

    if provider == "linear":
        wire.issues[parent_id]["description"] = "- [ ] Changed Epic validation"
        tracker._transport = original
    else:
        wire.rows[parent_id]["description"] = "- [x] Changed acceptance"
        tracker._req = original
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="modifié depuis le verdict humain"):
        write.close_epic(tracker, parent_id, human_verdict="accepted")
    if provider == "linear":
        assert not any(
            "Create" in document or "Update" in document
            for document, _args in wire.calls[before:]
        )
    else:
        assert not any(method == "POST" for method, _path, _body in wire.calls[before:])


@pytest.mark.parametrize("mutation", ("reopened", "version", "added"))
def test_youtrack_done_replay_rechecks_complete_current_graph(monkeypatch, mutation):
    tracker = _YouTrackWire()
    project = Project(key="YT", id="p")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    write.close_epic(
        tracker, "YT-1", human_verdict="accepted", issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )
    if mutation == "added":
        tracker.rows["YT-3"] = tracker._row("YT-3", "Feature", "done", 8)
        tracker.rows["YT-1"]["links"][0]["issues"].append({"idReadable": "YT-3"})
    elif mutation == "reopened":
        tracker.rows["YT-2"]["customFields"][0]["value"] = {"name": "review"}
    else:
        tracker.rows["YT-2"]["updated"] += 1
    with pytest.raises(TrackerConflictError, match="graphe Epic YouTrack divergent au rejeu"):
        write.close_epic(tracker, "YT-1", human_verdict="accepted")


def test_linear_bounded_closure_refuses_a_child_delivered_under_override(monkeypatch):
    tracker, wire, project = _linear_graph(acceptance="override")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    before = len(wire.calls)
    with pytest.raises((SystemExit, TrackerConflictError), match="dérog|override|preuve"):
        write.close_epic(
            tracker, "LIN-1", human_verdict="accepted", issued_at=1_800_000_000_000,
            nonce="nonce_1234567890abcdef",
        )
    assert not any("Create" in document or "Update" in document for document, _args in wire.calls[before:])


def test_youtrack_bounded_closure_refuses_open_child_dependency(monkeypatch):
    tracker = _YouTrackWire()
    project = Project(key="YT", id="p")
    tracker.rows["YT-3"] = tracker._row("YT-3", "Feature", "in-progress", 8)
    tracker.rows["YT-2"]["links"] = [{
        "linkType": {"sourceToTarget": "depends on"}, "direction": "OUTWARD",
        "issues": [{"idReadable": "YT-3"}],
    }]
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    assert tracker.get_issue("YT-2").links[0].type == "depends-on"
    before = len(tracker.calls)
    with pytest.raises((SystemExit, TrackerConflictError), match="dépend|dependency|terminal"):
        write.close_epic(
            tracker, "YT-1", human_verdict="accepted", issued_at=1_800_000_000_000,
            nonce="nonce_1234567890abcdef",
        )
    assert not any(method == "POST" for method, _path, _body in tracker.calls[before:])


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
def test_bounded_closure_recovers_state_response_lost_without_second_write(
    monkeypatch, provider,
):
    if provider == "linear":
        tracker, wire, project = _linear_graph()
        original = wire.__call__
        lost = {"value": True}

        def transport(document, variables):
            result = original(document, variables)
            if (
                "FoundryLinearIssueUpdate" in document
                and variables.get("id") == wire.issues["LIN-1"]["id"]
                and lost["value"]
            ):
                lost["value"] = False
                raise OSError("state response lost after apply")
            return result

        tracker._transport = transport
        parent_id = "LIN-1"
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        original = tracker._req
        lost = {"value": True}

        def request(method, path, body=None, fields=None, top=None):
            result = original(method, path, body, fields, top)
            if method == "POST" and path == "/issues/YT-1" and lost["value"]:
                lost["value"] = False
                raise OSError("state response lost after apply")
            return result

        tracker._req = request
        parent_id = "YT-1"
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)

    closed = write.close_epic(
        tracker, parent_id, human_verdict="accepted",
        issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
    )
    replay = write.close_epic(tracker, parent_id, human_verdict="accepted")

    assert closed.receipt == replay.receipt
    assert replay.replayed is True
    if provider == "linear":
        parent_updates = [
            call for call in wire.calls
            if "FoundryLinearIssueUpdate" in call[0]
            and call[1].get("id") == wire.issues["LIN-1"]["id"]
        ]
        assert len(parent_updates) == 1
    else:
        assert sum(
            method == "POST" and path == "/issues/YT-1"
            for method, path, _body in wire.calls
        ) == 1


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
def test_pending_audit_refuses_changed_original_predecessor(monkeypatch, provider):
    if provider == "linear":
        tracker, wire, project = _linear_graph()
        original = wire.__call__

        def fail_state(document, variables):
            if (
                "FoundryLinearIssueUpdate" in document
                and variables.get("id") == wire.issues["LIN-1"]["id"]
            ):
                raise OSError("state not applied")
            return original(document, variables)

        tracker._transport = fail_state
        parent_id = "LIN-1"
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        original = tracker._req

        def fail_state(method, path, body=None, fields=None, top=None):
            if method == "POST" and path == "/issues/YT-1":
                raise OSError("state not applied")
            return original(method, path, body, fields, top)

        tracker._req = fail_state
        parent_id = "YT-1"
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    with pytest.raises(Exception):
        write.close_epic(
            tracker, parent_id, human_verdict="accepted",
            issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
        )

    if provider == "linear":
        wire.issues[parent_id]["state"] = {
            "id": STATE_IDS["blocked"], "name": "Blocked",
        }
        wire.issues[parent_id]["updatedAt"] = "2026-09-20T10:03:00Z"
        tracker._transport = original
    else:
        wire.rows[parent_id]["customFields"][0]["value"] = {"name": "blocked"}
        wire.rows[parent_id]["updated"] += 1
        tracker._req = original
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="prédécesseur original"):
        write.close_epic(tracker, parent_id, human_verdict="accepted")
    if provider == "linear":
        assert not any("Create" in call[0] or "Update" in call[0] for call in wire.calls[before:])
    else:
        assert not any(method == "POST" for method, _path, _body in wire.calls[before:])


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
def test_pending_audit_resumes_exact_receipt_without_duplicate_effect(
    monkeypatch, provider,
):
    if provider == "linear":
        tracker, wire, project = _linear_graph()
        original = wire.__call__
        blocked = {"value": True}

        def transport(document, variables):
            if (
                blocked["value"]
                and "FoundryLinearIssueUpdate" in document
                and variables.get("id") == wire.issues["LIN-1"]["id"]
            ):
                raise OSError("state not applied")
            return original(document, variables)

        tracker._transport = transport
        parent_id = "LIN-1"
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        original = tracker._req
        blocked = {"value": True}

        def request(method, path, body=None, fields=None, top=None):
            if blocked["value"] and method == "POST" and path == "/issues/YT-1":
                raise OSError("state not applied")
            return original(method, path, body, fields, top)

        tracker._req = request
        parent_id = "YT-1"
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    with pytest.raises(Exception):
        write.close_epic(
            tracker, parent_id, human_verdict="accepted",
            issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
        )
    pending = tracker.get_pending_epic_closure(project, parent_id)
    assert pending is not None
    blocked["value"] = False

    closed = write.close_epic(tracker, parent_id, human_verdict="accepted")
    assert closed.receipt == pending
    if provider == "linear":
        assert sum(
            "foundry-epic-closure.v1" in item["body"]
            for item in wire.comments.values()
        ) == 1
    else:
        assert sum(
            "foundry-epic-closure.v1" in item["text"]
            for item in wire.rows[parent_id]["comments"]
        ) == 1


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
@pytest.mark.parametrize("phase", ("pending", "done"))
def test_dependency_coordinate_change_is_refused_on_resume(
    monkeypatch, provider, phase,
):
    if provider == "linear":
        tracker, wire, project = _linear_graph(dependency=True)
        parent_id = "LIN-1"
        if phase == "pending":
            original = wire.__call__

            def fail_state(document, variables):
                if (
                    "FoundryLinearIssueUpdate" in document
                    and variables.get("id") == wire.issues[parent_id]["id"]
                ):
                    raise OSError("state not applied")
                return original(document, variables)

            tracker._transport = fail_state
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        parent_id = "YT-1"
        tracker.rows["YT-3"] = tracker._row("YT-3", "Feature", "done", 8)
        tracker.rows["YT-2"]["links"] = [{
            "linkType": {"sourceToTarget": "depends on"}, "direction": "OUTWARD",
            "issues": [{"idReadable": "YT-3"}],
        }]
        if phase == "pending":
            original = tracker._req

            def fail_state(method, path, body=None, fields=None, top=None):
                if method == "POST" and path == "/issues/YT-1":
                    raise OSError("state not applied")
                return original(method, path, body, fields, top)

            tracker._req = fail_state
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    if phase == "pending":
        with pytest.raises(Exception):
            write.close_epic(
                tracker, parent_id, human_verdict="accepted",
                issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
            )
        if provider == "linear":
            tracker._transport = original
        else:
            tracker._req = original
    else:
        write.close_epic(
            tracker, parent_id, human_verdict="accepted",
            issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
        )

    if provider == "linear":
        wire.issues["LIN-3"]["updatedAt"] = "2026-09-20T10:03:00Z"
    else:
        wire.rows["YT-3"]["updated"] += 1
    with pytest.raises(TrackerConflictError, match="graphe Epic|dépend"):
        write.close_epic(tracker, parent_id, human_verdict="accepted")


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
def test_unknown_child_acceptance_refuses_without_mutation(monkeypatch, provider):
    if provider == "linear":
        tracker, wire, project = _linear_graph()
        # Remove the qualified acceptance receipt while retaining terminal state.
        wire.issues["LIN-2"]["comments"]["nodes"] = [
            row for row in wire.issues["LIN-2"]["comments"]["nodes"]
            if '"operation":"acceptance"' not in row["body"]
        ]
        parent_id = "LIN-1"
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        wire.rows["YT-2"]["description"] = "- [ ] acceptance"
        parent_id = "YT-1"
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    before = len(wire.calls)
    with pytest.raises((SystemExit, TrackerConflictError), match="preuve|acceptance"):
        write.close_epic(tracker, parent_id, human_verdict="accepted")
    if provider == "linear":
        assert not any("Create" in call[0] or "Update" in call[0] for call in wire.calls[before:])
    else:
        assert not any(method == "POST" for method, _path, _body in wire.calls[before:])


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
@pytest.mark.parametrize("corruption", ("malformed", "duplicate"))
def test_closure_audit_corruption_fails_closed(monkeypatch, provider, corruption):
    if provider == "linear":
        tracker, wire, project = _linear_graph()
        parent_id = "LIN-1"
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        parent_id = "YT-1"
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)
    write.close_epic(
        tracker, parent_id, human_verdict="accepted",
        issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
    )

    if provider == "linear":
        rows = wire.issues[parent_id]["comments"]["nodes"]
        audit = next(row for row in rows if "Foundry Epic closure audit" in row["body"])
        if corruption == "malformed":
            audit["body"] += "\nextra"
        else:
            rows.append({**copy.deepcopy(audit), "id": "duplicate-audit"})
    else:
        rows = wire.rows[parent_id]["comments"]
        audit = next(row for row in rows if "Foundry Epic closure audit" in row["text"])
        if corruption == "malformed":
            audit["text"] += "\nextra"
        else:
            rows.append(copy.deepcopy(audit))
    with pytest.raises(TrackerConflictError, match="audit de clôture.*(malformé|divergent|dupliqué)"):
        write.close_epic(tracker, parent_id, human_verdict="accepted")


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
def test_foreign_project_binding_refuses_before_effect(monkeypatch, provider):
    if provider == "linear":
        tracker, wire, _project = _linear_graph()
        foreign = Project(key="OTHER", id="foreign", extra={})
        parent_id = "LIN-1"
    else:
        tracker = _YouTrackWire()
        wire = tracker
        foreign = Project(key="OTHER", id="foreign")
        parent_id = "YT-1"
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: foreign)
    before = len(wire.calls)
    with pytest.raises((SystemExit, ValueError, TrackerConflictError, RuntimeError)):
        write.close_epic(tracker, parent_id, human_verdict="accepted")
    if provider == "linear":
        assert not any("Create" in call[0] or "Update" in call[0] for call in wire.calls[before:])
    else:
        assert not any(method == "POST" for method, _path, _body in wire.calls[before:])


@pytest.mark.parametrize("provider", ("linear", "youtrack"))
@pytest.mark.parametrize("phase", ("prewrite", "postwrite"))
def test_graph_version_change_around_parent_write_fails_closed(
    monkeypatch, provider, phase,
):
    if provider == "linear":
        tracker, wire, project = _linear_graph()
        parent_id = "LIN-1"
        original = wire.__call__
        parent_reads = {"value": 0}

        def transport(document, variables):
            if (
                phase == "prewrite"
                and "query FoundryLinearIssue" in document
                and variables.get("id") == "LIN-1"
            ):
                parent_reads["value"] += 1
                if parent_reads["value"] == 10:
                    wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T10:03:00Z"
            result = original(document, variables)
            if (
                phase == "postwrite"
                and "FoundryLinearIssueUpdate" in document
                and variables.get("id") == wire.issues[parent_id]["id"]
            ):
                wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T10:03:00Z"
            return result

        tracker._transport = transport
    else:
        tracker = _YouTrackWire()
        wire = tracker
        project = Project(key="YT", id="p")
        parent_id = "YT-1"
        original = tracker._req
        parent_reads = {"value": 0}

        def request(method, path, body=None, fields=None, top=None):
            if phase == "prewrite" and method == "GET" and path == "/issues/YT-1":
                parent_reads["value"] += 1
                if parent_reads["value"] == 10:
                    wire.rows["YT-2"]["updated"] += 1
            result = original(method, path, body, fields, top)
            if phase == "postwrite" and method == "POST" and path == "/issues/YT-1":
                wire.rows["YT-2"]["updated"] += 1
            return result

        tracker._req = request
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: project)

    with pytest.raises(TrackerConflictError, match="graphe Epic.*(avant|après) écriture"):
        write.close_epic(
            tracker, parent_id, human_verdict="accepted",
            issued_at=1_800_000_000_000, nonce="nonce_1234567890abcdef",
        )
    if phase == "prewrite":
        if provider == "linear":
            assert not any(
                "FoundryLinearCommentCreate" in call[0]
                and "Foundry Epic closure audit" in call[1]["input"]["body"]
                for call in wire.calls
            )
        else:
            assert not any(method == "POST" for method, _path, _body in wire.calls)
