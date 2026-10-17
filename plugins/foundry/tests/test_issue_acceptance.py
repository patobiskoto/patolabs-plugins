"""PAT-206 / PAT-ADR-0025: accept a non-code issue by an explicit human decision."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid

import pytest

import foundry
from foundry import acceptance_decision as decisions
from foundry import issue as issue_cli
from foundry import postmerge_review as pm
from foundry import write
from foundry.advisory_schema import MAX_COMMENT_BYTES
from foundry.routing import acceptance_criteria
from foundry.trackers.base import (
    IssueUnavailableError,
    Tracker,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearBindingError, LinearTracker, LinearTrackerError
from foundry.trackers.youtrack import YouTrackTracker

from test_abandon_and_dropped_closure import (  # noqa: F401  (autouse intent isolation)
    COORDS,
    REVIEW,
    _add,
    _cockpit,
    _graph,
    _isolated_youtrack_epic_closure_intents,
    _native,
    _started,
    _writes,
)
from test_linear_tracker import STATE_IDS, connection

BODY = "## Criteria\n\n- [ ] first criterion\n- [ ] second criterion\n"
SOURCE = "linear-human-acceptance"


def _issue(wire, number=5, *, state="ready", body=BODY, child_of=None):
    issue_id = _add(wire, number, state=state, child_of=child_of)
    wire.issues[issue_id]["description"] = body
    return issue_id


def _doc(issue_id, body=BODY, *, from_state="ready", evidence=None, unknowns=None, **over):
    criteria = acceptance_criteria(body)
    document = {
        "schema": decisions.SCHEMA,
        "issue": issue_id,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "ac_digest": hashlib.sha256(
            json.dumps(criteria, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "from_state": from_state,
        "authority": "maintainer-direct",
        "decided_on": "2026-10-16",
        "verdict": "accepted",
        "delivery": "none",
        "criteria": [
            {
                **criterion,
                "verdict": "pass",
                "evidence": copy.deepcopy(evidence or [{"type": "issue", "id": "LIN-2"}]),
                "unknowns": list(unknowns or []),
            }
            for criterion in criteria
        ],
    }
    document.update(over)
    return document


def _data(document):
    return json.dumps(document, separators=(",", ":")).encode()


def _accept(tracker, issue_id, document, verdict="accepted"):
    return write.accept_issue(tracker, issue_id, verdict, _data(document))


def _receipts(wire, issue_id):
    return [
        node for node in wire.issues[issue_id]["comments"]["nodes"]
        if "acceptance-decision" in node["body"]
    ]


def _receipt_payload(wire, issue_id):
    [node] = _receipts(wire, issue_id)
    return json.loads(node["body"].splitlines()[2].removeprefix("coordinates: "))["payload"]


def _advisory(wire, issue_id, *, confirm=True, isolate=False, report_hash=None):
    """Synthetic advisory records read back by ``restituted_reports``."""
    coordinates = {
        "repository": "acme/widgets", "pr": 1, "base": "a" * 40, "head": "b" * 40,
        "merge": "c" * 40, "diff_hash": "d" * 64,
    }
    attempt_id = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    attempt = {
        "schema": pm.SCHEMA, "type": "authorized_attempt", "issue": issue_id,
        "attempt_id": attempt_id, "coordinates": coordinates, "packet_hash": "e" * 64,
        "authorization": {
            "id": hashlib.sha256(uuid.uuid4().bytes).hexdigest(), "issue": issue_id,
            "coordinates": coordinates,
        },
    }
    report = {"verdict": "advisory"}
    receipt = {
        "schema": pm.TERMINAL_SCHEMA, "type": "advisory_report", "issue": issue_id,
        "attempt_id": attempt_id, "coordinates": coordinates, "packet_hash": "e" * 64,
        "report_hash": pm.digest(report), "report": report,
    }
    records = [attempt]
    if isolate:
        records.append({
            "schema": pm.TERMINAL_SCHEMA, "type": "isolated_terminal",
            "issue": issue_id, "attempt_id": attempt_id,
        })
    records.append(receipt)
    if confirm:
        records.append({
            "schema": pm.TERMINAL_SCHEMA, "type": "advisory_confirmation",
            "issue": issue_id, "attempt_id": attempt_id,
            "receipt_hash": pm.digest(receipt),
            "comment_hash": hashlib.sha256(pm.comment_text(receipt).encode()).hexdigest(),
            "report_hash": receipt["report_hash"],
        })
    for record in records:
        if isolate and record["type"] == "isolated_terminal":
            text = pm.TERMINAL_PREFIX + attempt_id + "\n{broken"
        else:
            text = pm.comment_text(record)
        wire.issues[issue_id]["comments"]["nodes"].append(
            {"id": f"advisory-{uuid.uuid4()}", "body": text,
             "createdAt": "2026-09-25T10:00:00Z"}
        )
    return {
        "type": "advisory-report", "issue": issue_id, "attempt_id": attempt_id,
        "report_hash": report_hash or receipt["report_hash"],
    }


def _no_effect(wire, before):
    assert not _writes(wire, before)


# --- pure refusals: before any provider read -------------------------------------

def _mut(**changes):
    def apply(document):
        document.update(changes)
    return apply


def _crit(index, **changes):
    def apply(document):
        document["criteria"][index].update(changes)
    return apply


_PURE = {
    "schema": _mut(schema="foundry.other.v1"),
    "other-issue": _mut(issue="LIN-6"),
    "no-authority": lambda d: d.pop("authority"),
    "other-authority": _mut(authority="coordinator"),
    "no-date": lambda d: d.pop("decided_on"),
    "bad-date": _mut(decided_on="2026-13-40"),
    "no-body-digest": lambda d: d.pop("body_sha256"),
    "short-digest": _mut(ac_digest="abc"),
    "verdict-other": _mut(verdict="rejected"),
    "delivery": _mut(delivery="pr"),
    "extra-key": _mut(extra=1),
    "from-review": _mut(from_state="review"),
    "from-done": _mut(from_state="done"),
    "no-criteria": _mut(criteria=[]),
    "criterion-not-pass": _crit(0, verdict="fail"),
    "no-evidence": _crit(0, evidence=[]),
    "evidence-checkbox": _crit(0, evidence=[{"type": "checkbox", "checked": True}]),
    "evidence-native-state": _crit(0, evidence=[{"type": "native-state", "state": "done"}]),
    "evidence-extra-key": _crit(0, evidence=[{"type": "issue", "id": "LIN-2", "x": 1}]),
    "evidence-self": _crit(0, evidence=[{"type": "issue", "id": "LIN-5"}]),
    "no-unknowns-key": lambda d: d["criteria"][0].pop("unknowns"),
    "unknown-not-text": _crit(0, unknowns=[1]),
    "unknown-empty": _crit(0, unknowns=[""]),
    "criterion-extra-key": _crit(0, note="x"),
}


@pytest.mark.parametrize("case", sorted(_PURE))
def test_invalid_documents_are_refused_before_any_provider_read(monkeypatch, case):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    document = _doc(issue_id)
    _PURE[case](document)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="Acceptation refusée"):
        _accept(tracker, issue_id, document)
    assert len(wire.calls) == before


def test_duplicate_keys_nonfinite_numbers_and_oversize_are_refused_before_any_read(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    text = json.dumps(_doc(issue_id))
    before = len(wire.calls)
    for data in (
        text.replace('"verdict": "accepted"', '"verdict": "accepted", "verdict": "accepted"', 1),
        text[:-1] + ', "x": NaN}',
        b"not json",
        b"\xff\xfe",
        text.encode() + b" " * MAX_COMMENT_BYTES,
    ):
        payload = data.encode() if isinstance(data, str) else data
        with pytest.raises(SystemExit, match="Acceptation refusée"):
            write.accept_issue(tracker, issue_id, "accepted", payload)
    assert len(wire.calls) == before


@pytest.mark.parametrize("verdict", (None, "rejected", "Accepted", ""))
def test_a_missing_or_different_verdict_is_refused_before_any_read(monkeypatch, verdict):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="verdict humain explicite"):
        _accept(tracker, issue_id, _doc(issue_id), verdict=verdict)
    assert len(wire.calls) == before


def test_the_document_issue_must_be_the_argument_issue(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    other = _issue(wire, 6)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="pas LIN-5"):
        _accept(tracker, issue_id, _doc(other))
    assert len(wire.calls) == before


@pytest.mark.parametrize("provider", (YouTrackTracker, GitHubProjectsTracker, DevHubTracker))
def test_other_providers_refuse_before_any_read(provider):
    assert provider.human_acceptance_supported is False
    assert provider.human_acceptance_predecessors == frozenset()
    with pytest.raises(TrackerCapabilityUnavailableError, match="human-acceptance"):
        write.validate_acceptance_request(provider, "LIN-5", "accepted", _data(_doc("LIN-5")))
    with pytest.raises(TrackerCapabilityUnavailableError, match="human-acceptance"):
        provider.accept_issue(object.__new__(provider), "LIN-5", {})
    with pytest.raises(TrackerCapabilityUnavailableError, match="human-acceptance"):
        write.issue_acceptance_state(provider, "LIN-5")
    assert Tracker.human_acceptance_supported is False
    assert LinearTracker.human_acceptance_supported is True
    assert LinearTracker.human_acceptance_predecessors == frozenset(
        {"backlog", "ready", "in-progress"}
    )


# --- success, receipt, reading ------------------------------------------------------

def test_acceptance_writes_one_receipt_then_one_state_and_reads_done_aligned_accepted(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _issue(wire, body=BODY + "\n## Notes\nfree\n")
    document = _doc(issue_id, BODY + "\n## Notes\nfree\n", unknowns=["native view missing"])
    before = len(wire.calls)
    seen = _accept(tracker, issue_id, document)
    writes = _writes(wire, before)
    assert [("Comment" in call[0]) for call in writes] == [True, False]
    assert len(writes) == 2
    assert (seen.state, seen.native_state, seen.projection_status) == (
        "done", "done", "aligned",
    )
    assert (seen.acceptance_status, seen.acceptance_source) == ("accepted", SOURCE)
    assert (seen.ac_done, seen.ac_total, seen.pr_url) == (2, 2, None)
    coordinates = json.loads(seen.acceptance_coordinates)
    assert set(coordinates) == {"body_digest", "checked", "decision_digest", "native_state_id"}
    assert coordinates["decision_digest"] == decisions.decision_digest(document)
    assert coordinates["body_digest"] == document["body_sha256"]
    for read in (tracker.get_issue(issue_id), tracker.observe_issue(issue_id)):
        assert (read.state, read.projection_status, read.acceptance_source) == (
            "done", "aligned", SOURCE,
        )
    # Unknowns are copied verbatim into the receipt, never turned into evidence.
    payload = _receipt_payload(wire, issue_id)
    assert payload["decision"] == document
    assert payload["decision"]["criteria"][0]["unknowns"] == ["native view missing"]
    assert wire.issues[issue_id]["state"]["id"] == STATE_IDS["done"]


def test_receipt_carries_no_pr_commit_or_review_coordinate(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    _accept(tracker, issue_id, _doc(issue_id))
    payload = _receipt_payload(wire, issue_id)
    assert set(payload) == {"native_state_id", "decision", "decision_digest"}
    text = json.dumps(payload)
    for key in ("pr_url", "head_sha", "base_sha", "merge_sha", "review_digest", "merge"):
        assert key not in text
    assert payload["native_state_id"] == STATE_IDS["done"]


@pytest.mark.parametrize("from_state", ("backlog", "ready", "in-progress"))
def test_every_admitted_state_can_be_accepted(monkeypatch, from_state):
    tracker, wire, project = _graph(monkeypatch)
    if from_state == "in-progress":
        issue_id = _started(tracker, wire, project, 5)
        wire.issues[issue_id]["description"] = BODY
    else:
        issue_id = _issue(wire, state=from_state)
    seen = _accept(tracker, issue_id, _doc(issue_id, from_state=from_state))
    assert (seen.state, seen.acceptance_source) == ("done", SOURCE)


def test_a_pat195_shaped_decision_keeps_its_unknowns_verbatim(monkeypatch):
    body = (
        "## Acceptance criteria\n\n"
        "- [ ] The exact historical diff is re-read at its immutable coordinates.\n"
        "- [ ] One independent review is attempted by the routed reviewer profile.\n"
        "- [ ] The report covers the criteria, then bugs, regressions and security.\n"
        "- [ ] The report is kept under this ticket; defects go to intake.\n"
    )
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire, body=body, state="in-progress")
    evidence = [_advisory(wire, issue_id), {"type": "issue", "id": "LIN-2"}]
    unknowns = {
        0: ["historical version of the PR description missing"],
        1: ["native observations absent"],
        2: [],
        3: ["native observations absent", "historical version of the PR description missing"],
    }
    document = _doc(issue_id, body, from_state="in-progress", evidence=evidence)
    for index, items in unknowns.items():
        document["criteria"][index]["unknowns"] = items
    advisory_before = [
        node["body"] for node in wire.issues[issue_id]["comments"]["nodes"]
        if node["body"].startswith(pm.PREFIX)
    ]
    seen = _accept(tracker, issue_id, document)
    assert (seen.state, seen.ac_done, seen.ac_total) == ("done", 4, 4)
    payload = _receipt_payload(wire, issue_id)
    assert [c["unknowns"] for c in payload["decision"]["criteria"]] == [
        unknowns[i] for i in range(4)
    ]
    assert all(c["evidence"] == evidence for c in payload["decision"]["criteria"])
    # The advisory records are byte-unchanged and gave no delivery or merge effect.
    assert advisory_before and advisory_before == [
        node["body"] for node in wire.issues[issue_id]["comments"]["nodes"]
        if node["body"].startswith(pm.PREFIX)
    ]


# --- S1 refusals: no effect ---------------------------------------------------------

def _refused(tracker, wire, issue_id, document, match):
    before = len(wire.calls)
    with pytest.raises((TrackerConflictError, SystemExit), match=match):
        _accept(tracker, issue_id, document)
    _no_effect(wire, before)


def test_an_epic_is_sent_to_close_epic(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    wire.issues[issue_id]["labels"] = connection([{"id": "label-epic", "name": "Epic"}])
    _refused(tracker, wire, issue_id, _doc(issue_id), "issue close-epic")


def test_an_issue_with_children_is_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    _add(wire, 6, child_of=issue_id)
    _refused(tracker, wire, issue_id, _doc(issue_id), "has children")


def test_an_issue_without_criterion_is_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    document = _doc(issue_id)
    wire.issues[issue_id]["description"] = "no checkbox here"
    _refused(tracker, wire, issue_id, document, "no acceptance criterion")


@pytest.mark.parametrize("kind", ("review", "done", "acceptance-override", "cockpit"))
def test_pr_or_cockpit_receipts_send_the_issue_to_merge(monkeypatch, kind):
    tracker, wire, project = _graph(monkeypatch)
    if kind == "cockpit":
        issue_id = _issue(wire)
        _cockpit(tracker, wire, issue_id)
    else:
        issue_id = _started(tracker, wire, project, 5, upto="review")
        wire.issues[issue_id]["description"] = BODY
        if kind == "acceptance-override":
            tracker.project_acceptance_override(
                issue_id, "synthetic-reason", REVIEW, project=project,
            )
        _native(wire, issue_id, "in-progress")
    _refused(tracker, wire, issue_id, _doc(issue_id, from_state="in-progress"
                                           if kind != "cockpit" else "ready"),
             "issue merge")


@pytest.mark.parametrize("native", ("review", "blocked", "done", "dropped"))
def test_an_inadmissible_native_state_is_refused(monkeypatch, native):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire, state=native)
    _refused(tracker, wire, issue_id, _doc(issue_id), "not admitted|natively dropped")


def test_a_native_state_different_from_the_decision_is_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire, state="backlog")
    _refused(tracker, wire, issue_id, _doc(issue_id, from_state="ready"), "made from ready")


def test_a_pr_free_issue_with_a_linked_epic_closure_audit_is_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    wire.issues[issue_id]["comments"]["nodes"].append(
        {"id": "audit", "body": "Foundry Epic closure audit (append-only).\nx"}
    )
    _refused(tracker, wire, issue_id, _doc(issue_id), "Epic closure audit present")


@pytest.mark.parametrize("drift", ("body", "reordered", "count", "criterion-digest"))
def test_a_drift_between_decision_and_body_is_refused(monkeypatch, drift):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    document = _doc(issue_id)
    if drift == "body":
        wire.issues[issue_id]["description"] = BODY + "changed\n"
    elif drift == "reordered":
        document["criteria"].reverse()
    elif drift == "count":
        document["criteria"].pop()
    else:
        document["criteria"][0]["digest"] = "0" * 64
    _refused(tracker, wire, issue_id, document, "corps a changé|diffèrent")


# --- evidence ------------------------------------------------------------------------

def test_a_confirmed_advisory_report_of_the_issue_is_accepted_evidence(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    evidence = [_advisory(wire, issue_id)]
    assert _accept(tracker, issue_id, _doc(issue_id, evidence=evidence)).state == "done"


@pytest.mark.parametrize("fault", ("unconfirmed", "isolated", "other-hash"))
def test_an_unconfirmed_isolated_or_other_hash_report_is_refused(monkeypatch, fault):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    if fault == "unconfirmed":
        evidence = [_advisory(wire, issue_id, confirm=False)]
    elif fault == "isolated":
        evidence = [_advisory(wire, issue_id, isolate=True)]
    else:
        evidence = [_advisory(wire, issue_id, report_hash="9" * 64)]
    _refused(tracker, wire, issue_id, _doc(issue_id, evidence=evidence),
             "rapport consultatif confirmé introuvable")


def test_evidence_naming_an_issue_outside_the_project_is_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    foreign = _add(wire, 7, child_of=None)
    wire.issues[foreign]["project"] = {"id": "another-project"}
    before = len(wire.calls)
    with pytest.raises(LinearBindingError, match="issue_outside_binding"):
        _accept(tracker, issue_id, _doc(
            issue_id, evidence=[{"type": "issue", "id": foreign}],
        ))
    _no_effect(wire, before)
    assert not _receipts(wire, issue_id)


def test_evidence_naming_a_missing_issue_is_refused(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    before = len(wire.calls)
    with pytest.raises(IssueUnavailableError):
        _accept(tracker, issue_id, _doc(
            issue_id, evidence=[{"type": "issue", "id": "LIN-99"}],
        ))
    _no_effect(wire, before)


# --- replay, partial effect, divergence ---------------------------------------------

def test_an_exact_replay_converges_without_a_second_write(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    document = _doc(issue_id)
    _accept(tracker, issue_id, document)
    before = len(wire.calls)
    seen = _accept(tracker, issue_id, document)
    _no_effect(wire, before)
    assert (seen.state, seen.acceptance_source) == ("done", SOURCE)
    assert len(_receipts(wire, issue_id)) == 1


def test_a_different_decision_after_the_receipt_is_refused_without_a_write(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    _accept(tracker, issue_id, _doc(issue_id))
    receipt = copy.deepcopy(_receipts(wire, issue_id))
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="different decision"):
        _accept(tracker, issue_id, _doc(issue_id, unknowns=["another unknown"]))
    _no_effect(wire, before)
    assert _receipts(wire, issue_id) == receipt


def test_an_ambiguous_creation_is_resumed_by_its_deterministic_id(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    interrupted = []

    def transport(document, variables):
        result = wire(document, variables)
        if "FoundryLinearCommentCreate" in document and not interrupted:
            interrupted.append(1)
            raise LinearTrackerError("lifecycle.comment.create", 503, "transport_error")
        return result

    tracker._transport = transport
    seen = _accept(tracker, issue_id, _doc(issue_id))
    assert interrupted and seen.state == "done"
    assert len(_receipts(wire, issue_id)) == 1


def test_a_partial_effect_is_refused_strictly_then_resumed_by_the_same_command(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    document = _doc(issue_id)
    real = tracker._transport or wire

    def failing(doc, variables):
        if "FoundryLinearIssueUpdate" in doc:
            raise LinearTrackerError("issue.update.lifecycle-state", 503, "transport_error")
        return real(doc, variables)

    tracker._transport = failing
    with pytest.raises(LinearTrackerError):
        _accept(tracker, issue_id, document)
    assert len(_receipts(wire, issue_id)) == 1
    assert wire.issues[issue_id]["state"]["id"] == STATE_IDS["ready"]
    with pytest.raises(TrackerConflictError, match="changed outside lifecycle"):
        tracker.get_issue(issue_id)
    state = write.issue_acceptance_state(tracker, issue_id)
    assert (state.kind, state.native_state) == ("receipt-only", "ready")
    receipt = copy.deepcopy(_receipts(wire, issue_id))

    tracker._transport = real
    before = len(wire.calls)
    seen = _accept(tracker, issue_id, document)
    assert [("Update" in call[0]) for call in _writes(wire, before)] == [True]
    assert (seen.state, seen.acceptance_source) == ("done", SOURCE)
    assert _receipts(wire, issue_id) == receipt
    assert write.issue_acceptance_state(tracker, issue_id).kind == "accepted"


def test_a_third_party_state_change_between_receipt_and_projection_stops(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)

    def transport(doc, variables):
        result = wire(doc, variables)
        if "FoundryLinearCommentCreate" in doc:
            _native(wire, issue_id, "blocked")
        return result

    tracker._transport = transport
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError):
        _accept(tracker, issue_id, _doc(issue_id))
    assert not [c for c in _writes(wire, before) if "Update" in c[0]]
    assert wire.issues[issue_id]["state"]["id"] == STATE_IDS["blocked"]


def test_a_divergent_readback_stops_without_retry(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)

    def transport(doc, variables):
        result = wire(doc, variables)
        if "FoundryLinearIssueUpdate" in doc:
            wire.issues[issue_id]["description"] = BODY + "edited after the decision\n"
        return result

    tracker._transport = transport
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        _accept(tracker, issue_id, _doc(issue_id))
    assert len([c for c in _writes(wire, before) if "Update" in c[0]]) == 1
    assert len(_receipts(wire, issue_id)) == 1


def test_a_body_modified_after_the_decision_makes_it_stale(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    _accept(tracker, issue_id, _doc(issue_id))
    wire.issues[issue_id]["description"] = BODY.replace("first", "1st")
    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        tracker.get_issue(issue_id)
    seen = tracker.observe_issue(issue_id)
    assert (seen.acceptance_status, seen.projection_status) == ("unknown", "unknown")


def test_ticking_a_box_after_the_decision_makes_it_stale(monkeypatch):
    """PAT-239 (206-8): body_sha256 binds the whole body, checkboxes included."""
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    _accept(tracker, issue_id, _doc(issue_id))
    wire.issues[issue_id]["description"] = BODY.replace("- [ ] first", "- [x] first")
    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        tracker.get_issue(issue_id)


def test_a_decision_receipt_next_to_a_pr_receipt_reads_as_conflict(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _issue(wire)
    _accept(tracker, issue_id, _doc(issue_id))
    _cockpit(tracker, wire, issue_id)
    with pytest.raises(TrackerConflictError, match="conflicts with PR receipts"):
        tracker.get_issue(issue_id)


def test_a_duplicate_decision_receipt_reads_as_conflict(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    _accept(tracker, issue_id, _doc(issue_id))
    [node] = _receipts(wire, issue_id)
    wire.issues[issue_id]["comments"]["nodes"].append(dict(node, id="other"))
    with pytest.raises(TrackerConflictError):
        tracker.get_issue(issue_id)


def test_a_receipt_beyond_the_size_bound_is_refused_before_any_write(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    document = _doc(issue_id)
    base = len(decisions.canonical(document).encode("ascii"))
    # Fits the document bound, but not once the receipt envelope is added.
    document["criteria"][0]["unknowns"] = ["x" * (MAX_COMMENT_BYTES - base - 40)]
    assert len(decisions.canonical(document).encode("ascii")) <= decisions.MAX_BYTES
    _refused(tracker, wire, issue_id, document, "size bound")
    assert not _receipts(wire, issue_id)


# --- reading by Epic closure and release --------------------------------------------

def test_epic_closure_counts_the_decision_as_acceptance_and_replays(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    child = _issue(wire, child_of="LIN-1")
    _accept(tracker, child, _doc(child))
    closed = write.close_epic(tracker, "LIN-1", human_verdict="accepted", **COORDS)
    [node] = [c for c in closed.receipt.children if c.id == child]
    assert (node.acceptance_status, node.acceptance_source) == ("accepted", SOURCE)
    assert json.loads(node.acceptance_coordinates)["decision_digest"]
    replay = write.close_epic(tracker, "LIN-1", human_verdict="accepted")
    assert replay.replayed is True
    # PAT-ADR-0023: a version that only advances is accepted on replay.
    wire.issues[child]["updatedAt"] = "2026-09-21T10:00:00Z"
    assert write.close_epic(tracker, "LIN-1", human_verdict="accepted").replayed is True


def test_the_release_scope_reads_the_decision_as_accepted_with_its_reference(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _issue(wire)
    document = _doc(issue_id)
    _accept(tracker, issue_id, document)
    item = tracker._release_issue(wire.issues[issue_id], project)
    assert item.disposition == "accepted"
    assert item.references["acceptance_decision_digest"] == decisions.decision_digest(document)
    assert "pr_url" not in item.references and "merge_sha" not in item.references


# --- CLI -----------------------------------------------------------------------------

def _cli(monkeypatch, tmp_path, tracker, issue_id, document=None, extra=()):
    monkeypatch.setattr(foundry, "tracker", lambda *_a, **_k: tracker)
    path = tmp_path / "decision.json"
    if document is not None:
        path.write_bytes(_data(document))
    return issue_cli.accept(
        issue_id,
        flags=["--human-verdict=accepted", f"--decision-file={path}", *extra],
    )


def test_cli_accepts_then_reads_the_status(monkeypatch, tmp_path, capsys):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    monkeypatch.setattr(foundry, "tracker", lambda *_a, **_k: tracker)
    issue_cli.accept(issue_id, flags=["--status"])
    assert "aucune acceptation humaine" in capsys.readouterr().out
    _cli(monkeypatch, tmp_path, tracker, issue_id, _doc(issue_id))
    assert "→ done · acceptation humaine" in capsys.readouterr().out
    issue_cli.accept(issue_id, flags=["--status"])
    assert "accepté" in capsys.readouterr().out


def test_cli_status_is_read_only_and_takes_no_other_flag(monkeypatch, tmp_path):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    monkeypatch.setattr(foundry, "tracker", lambda *_a, **_k: tracker)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="lecture seule"):
        issue_cli.accept(issue_id, flags=["--status", "--human-verdict=accepted"])
    with pytest.raises(SystemExit, match="lecture seule"):
        issue_cli.accept(issue_id, flags=["--status", f"--decision-file={tmp_path}/x"])
    assert len(wire.calls) == before
    issue_cli.accept(issue_id, flags=["--status"])
    _no_effect(wire, before)


@pytest.mark.parametrize("flags", (
    ["--human-verdict=accepted", "--human-verdict=accepted", "--decision-file={f}"],
    ["--human-verdict=accepted", "--decision-file={f}", "--decision-file={f}"],
    ["--decision-file={f}"],
    ["--human-verdict=accepted"],
    ["--human-verdict=accepted", "--decision-file={f}.missing"],
))
def test_cli_refuses_repeated_absent_or_unreadable_flags_before_any_read(
    monkeypatch, tmp_path, flags,
):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire)
    monkeypatch.setattr(foundry, "tracker", lambda *_a, **_k: tracker)
    path = tmp_path / "decision.json"
    path.write_bytes(_data(_doc(issue_id)))
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="Acceptation refusée"):
        issue_cli.accept(issue_id, flags=[f.format(f=path) for f in flags])
    assert len(wire.calls) == before


def test_cli_reports_a_provider_refusal_with_the_status_command(monkeypatch, tmp_path):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire, state="blocked")
    with pytest.raises(SystemExit, match="issue accept LIN-5 --status") as raised:
        _cli(monkeypatch, tmp_path, tracker, issue_id, _doc(issue_id))
    assert "selon ce que dit `--status`" in str(raised.value)


def test_cli_flag_filter_knows_the_accept_flags():
    assert "accept" in issue_cli._KNOWN_FLAGS
    assert issue_cli._ACCEPT_FLAG_PREFIXES == ("--human-verdict=", "--decision-file=")


def test_cli_status_ties_the_replay_hint_to_the_decision_state(monkeypatch, capsys):
    """PAT-239 (206-1): `relance` is unconditional only for `done`."""
    monkeypatch.setattr(foundry, "tracker", lambda *_a, **_k: object())
    for native, hint in (
        ("blocked", "pas un état de reprise"),
        ("in-progress", "encore celui de la décision"),
        ("done", "aucun second reçu)"),
    ):
        monkeypatch.setattr(
            write, "issue_acceptance_state",
            lambda *_a, native=native: write.AcceptanceState("receipt-only", "d" * 64, native),
        )
        issue_cli.accept("LIN-5", flags=["--status"])
        out = capsys.readouterr().out
        assert hint in out
        assert ("encore celui de la décision" in out) is (native == "in-progress")
        assert ("pas un état de reprise" in out) is (native == "blocked")


def test_replay_refuses_a_native_state_other_than_the_decision_from_state(monkeypatch):
    """PAT-239 (206-1): from_state ready, native in-progress (both in FROM_STATES)."""
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _issue(wire, state="ready")
    _accept(tracker, issue_id, _doc(issue_id, from_state="ready"))
    wire.issues[issue_id]["state"]["id"] = STATE_IDS["in-progress"]
    with pytest.raises(TrackerConflictError, match="expected ready"):
        _accept(tracker, issue_id, _doc(issue_id, from_state="ready"))


def test_cli_unknown_flag_for_accept_lists_the_real_flags(monkeypatch):
    """PAT-239 (206-7): no `(aucun)` for a command that has flags."""
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["issue.py", "accept", "LIN-5", "--bogus"])
    with pytest.raises(SystemExit, match="--decision-file=<chemin>, --human-verdict"):
        runpy.run_path(issue_cli.__file__, run_name="__main__")


def test_the_linear_predecessors_are_the_decision_from_states():
    """PAT-239 (206-4): one set, shared, value unchanged."""
    from foundry import acceptance_decision as decisions
    from foundry.trackers import linear

    assert linear._HUMAN_ACCEPTANCE_PREDECESSORS is decisions.FROM_STATES
    assert decisions.FROM_STATES == frozenset({"backlog", "ready", "in-progress"})


def test_accept_allowed_text_names_every_accept_flag():
    """PAT-239: `_ACCEPT_ALLOWED` is pinned to `_ACCEPT_FLAG_PREFIXES` and `--status`."""
    for flag in (*issue_cli._ACCEPT_FLAG_PREFIXES, "--status"):
        assert flag in issue_cli._ACCEPT_ALLOWED


def test_cli_unknown_flag_for_close_epic_lists_the_real_flags(monkeypatch):
    """PAT-242: no `(aucun)` for `close-epic`, which accepts four flags."""
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["issue.py", "close-epic", "LIN-5", "--bogus"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(issue_cli.__file__, run_name="__main__")
    message = str(raised.value)
    assert "(aucun)" not in message
    for flag in (*issue_cli._CLOSE_EPIC_FLAG_PREFIXES, "--status"):
        assert flag in message
    # the help text covers exactly the parser's flags (as PAT-239 did for `accept`)
    listed = {part.split("=")[0] for part in issue_cli._CLOSE_EPIC_ALLOWED.split(", ")
              if part.startswith("--")}
    assert {flag.rstrip("=") for flag in (*issue_cli._CLOSE_EPIC_FLAG_PREFIXES, "--status")} == listed
