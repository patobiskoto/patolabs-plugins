"""PLP-311: explicit source numbering and historical rejected ADRs, offline only."""
import copy
import hashlib
from dataclasses import replace

import pytest

from foundry import registry
from foundry.cutover import _registry_target_project
from foundry.migration import capture_manifest, copy_and_verify, load_manifest, save_manifest, verify_targets
from foundry.models import Adr, Issue, Project
from foundry.trackers.base import TrackerConflictError
from foundry.trackers.devhub import DevHubTrackerError
from test_devhub_tracker import (
    BOUND, PROJECT, TERMINAL, ScriptedTracker, _BOUND_REMOTE, _migration_adr,
    _migration_issue, adr_raw, governance_raw, import_adr, issue_raw, receipt_page, workflow_raw,
)
from test_tracker_migration import FakeTransportTracker


NUMBERED = replace(BOUND, extra={**BOUND.extra, "preserve_issue_numbers": True})


@pytest.mark.parametrize("extra", [{}, {"preserve_issue_numbers": False}, {"preserve_issue_numbers": True}])
def test_server_metadata_cannot_activate_or_override_local_number_preservation(extra):
    registered = replace(BOUND, extra={**BOUND.extra, **extra})
    remote = {**_BOUND_REMOTE, "extra": {"preserve_issue_numbers": not extra.get("preserve_issue_numbers", False)}}
    resolved = ScriptedTracker([remote])._read_back_registered(registered, "trame")
    assert resolved.extra.get("preserve_issue_numbers") == extra.get("preserve_issue_numbers")
    assert ("preserve_issue_numbers" in resolved.extra) == ("preserve_issue_numbers" in extra)


def snapshot(number, terminal=False):
    record = copy.deepcopy(TERMINAL if terminal else _migration_issue(number))
    record.update(id=f"TRAME-{number}", source_ref=f"linear:issue:TRAME-{number}")
    return record


def answer(record, **overrides):
    raw = issue_raw(
        id=record["id"], source_ref=record["source_ref"], title=record["title"],
        body=record["body"], state=record["attributes"]["state"],
    )
    if "terminal" in record:
        raw.update(imported_terminal=True, **record["terminal"])
    raw.update(overrides)
    return raw


@pytest.mark.parametrize("terminal", [False, True])
def test_numbered_import_keeps_holes_reverse_order_and_bounds(monkeypatch, terminal):
    monkeypatch.setattr(registry, "load", lambda: {})
    records = [snapshot(number, terminal) for number in (999999, 19, 1)]
    tracker = ScriptedTracker([answer(record) for record in records])
    for record in records:
        assert tracker.migration_import_issue(
            NUMBERED, record, source_ref=record["source_ref"]).id == record["id"]
    assert [call[2]["source_number"] for call in tracker.calls] == [999999, 19, 1]
    assert all(call[1] == "/projects/TRAME/issues" + ("/import" if terminal else "")
               for call in tracker.calls)


@pytest.mark.parametrize("terminal", [False, True])
def test_absent_and_false_option_have_identical_payloads(monkeypatch, terminal):
    monkeypatch.setattr(registry, "load", lambda: {})
    record = snapshot(19, terminal)
    calls = []
    for extra in ({}, {"preserve_issue_numbers": False}):
        tracker = ScriptedTracker([answer(record)])
        tracker.migration_import_issue(replace(PROJECT, extra=extra), record,
                                       source_ref=record["source_ref"])
        calls.append(tracker.calls[0])
    assert calls[0] == calls[1]
    assert "source_number" not in calls[0][2]


@pytest.mark.parametrize("identifier", [
    "OTHER-1", "TRAME-0", "TRAME--1", "TRAME-1000000", "TRAME-01", "TRAME-+1",
    "TRAME-1.0", "TRAME-١", "trame-1", "TRAME-ADR-0001", "TRAME-1\n",
])
def test_preflight_refuses_non_reproducible_numbers_before_any_request(identifier):
    record = {**snapshot(1), "id": identifier, "source_ref": f"linear:issue:{identifier}"}
    tracker = ScriptedTracker([])
    with pytest.raises(TrackerConflictError, match="non reproductible|source_ref de migration invalide"):
        tracker.migration_preflight(NUMBERED, (record,))
    assert tracker.calls == []


@pytest.mark.parametrize("change", [
    {"id": "TRAME-2"}, {"id": None}, {"source_ref": "linear:issue:TRAME-2"},
    {"source_ref": "linear:adr:TRAME-1"}, {"source_ref": "linear:issue:OTHER-1"},
])
def test_preflight_and_direct_import_refuse_mismatched_source_coordinates(monkeypatch, change):
    monkeypatch.setattr(registry, "load", lambda: {})
    record = {**snapshot(1), **change}
    tracker = ScriptedTracker([])
    with pytest.raises(TrackerConflictError):
        tracker.migration_preflight(NUMBERED, (record,))
    with pytest.raises(TrackerConflictError):
        tracker.migration_import_issue(NUMBERED, record, source_ref=record["source_ref"])
    assert tracker.calls == []


def test_preflight_refuses_a_number_repeated_under_another_source():
    record = snapshot(1)
    tracker = ScriptedTracker([])
    with pytest.raises(TrackerConflictError, match="numéro issue source dupliqué"):
        tracker.migration_preflight(NUMBERED, (record, {**record, "source_ref": "youtrack:issue:TRAME-1"}))
    assert tracker.calls == []


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_number_preservation_requires_an_explicit_boolean(value):
    tracker = ScriptedTracker([])
    with pytest.raises(ValueError, match="booléen"):
        tracker.migration_preflight(replace(PROJECT, extra={"preserve_issue_numbers": value}), ())
    assert tracker.calls == []


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("failure", [
    DevHubTrackerError("POST", "/projects/TRAME/issues", 400, "invalid_request"),
    DevHubTrackerError("POST", "/projects/TRAME/issues", 403, "forbidden"),
    DevHubTrackerError("POST", "/projects/TRAME/issues", 409, "resource_conflict"),
])
def test_server_refusal_never_retries_without_source_number(monkeypatch, terminal, failure):
    monkeypatch.setattr(registry, "load", lambda: {})
    record = snapshot(19, terminal)
    tracker = ScriptedTracker([failure])
    with pytest.raises((DevHubTrackerError, TrackerConflictError)):
        tracker.migration_import_issue(NUMBERED, record, source_ref=record["source_ref"])
    assert len(tracker.calls) == 1 and tracker.calls[0][2]["source_number"] == 19


@pytest.mark.parametrize("terminal", [False, True])
def test_import_refuses_a_server_that_ignores_the_requested_number(monkeypatch, terminal):
    monkeypatch.setattr(registry, "load", lambda: {})
    record = snapshot(19, terminal)
    tracker = ScriptedTracker([answer(record, id="TRAME-1")])
    with pytest.raises(TrackerConflictError, match="divergente"):
        tracker.migration_import_issue(NUMBERED, record, source_ref=record["source_ref"])
    assert len(tracker.calls) == 1


def test_manifest_binds_option_and_copy_and_activation_refuse_existing_wrong_number(tmp_path):
    source = FakeTransportTracker("linear", issues=[Issue(
        "TRAME-19", "issue", state="ready", type="Task", priority="P2", estimate=3, body="")])
    tracker = ScriptedTracker([_BOUND_REMOTE, governance_raw(), workflow_raw()])
    manifest = capture_manifest(source, Project("TRAME", "source"), tracker, NUMBERED)
    path = tmp_path / "manifest.json"
    save_manifest(path, manifest)
    loaded = load_manifest(path)
    assert loaded["target"]["project"]["extra"]["preserve_issue_numbers"] is True
    assert loaded["target"]["migration_profile"]["preserve_issue_numbers"] is True
    assert _registry_target_project(NUMBERED).extra["preserve_issue_numbers"] is True
    for action in (copy_and_verify, verify_targets):
        tracker.responses = [receipt_page([issue_raw(id="TRAME-1", source_ref="linear:issue:TRAME-19")])]
        loaded["phase"] = "verified"
        loaded["issues"][0].update(status="verified", target_id="TRAME-1")
        with pytest.raises(TrackerConflictError, match="numéro source"):
            if action is copy_and_verify:
                action(loaded, tracker, NUMBERED, lambda: None)
            else:
                action(loaded, tracker, NUMBERED)
    assert all(call[0] == "GET" for call in tracker.calls)


def test_rejected_adr_import_read_and_export_keep_exact_body_and_unknown_relations(monkeypatch):
    monkeypatch.setattr(registry, "load", lambda: {})
    unknown = ["issues", "supersedes", "superseded_by"]
    body = "  ## Historical decision\r\n\r\nRejected. é\n  "
    raw = adr_raw(status="rejected", body=body, unknown_relations=unknown)
    tracker = ScriptedTracker([raw])
    imported = import_adr(tracker, historical_status="rejected", body=body,
                          expected_source_sha256=hashlib.sha256(body.encode()).hexdigest(),
                          unknown_relations=tuple(unknown))
    assert (imported.status, imported.body) == ("rejected", body)
    assert tracker.calls[0][2]["status"] == "rejected"
    assert tracker.calls[0][2]["body"] == body
    assert tracker.calls[0][2]["unknown_relations"] == unknown
    tracker.responses = [receipt_page([raw]), raw, {
        "schema_version": "devhub-adr-decisions.v1", "adr": {"id": raw["id"]},
        "relations": {"supersedes": None, "superseded_by": None},
    }]
    (exported,) = tracker.migration_export_adrs(PROJECT)
    assert (exported["adr"].status, exported["adr"].body) == ("rejected", body)
    assert exported["relations"] == dict.fromkeys(unknown, "unknown")
    assert not any("relationships" in call[1] for call in tracker.calls)
    record = _migration_adr(1, status="rejected", supersedes="unknown",
                            superseded_by="unknown", issues="unknown")
    tracker._preflight_adrs(PROJECT, [record])


def test_rejected_does_not_expand_native_adr_creation_or_decision_routes():
    tracker = ScriptedTracker([])
    with pytest.raises(ValueError, match="proposed"):
        tracker.create_adr(PROJECT, "title", "body", status="rejected")
    with pytest.raises(ValueError, match="non supporté"):
        tracker.set_adr_status(Adr("TRAME-ADR-0001", "title"), "rejected", project=PROJECT)
    assert tracker.calls == []


def test_rejected_adr_cannot_be_a_supersession_replacement():
    old = _migration_adr(1, status="superseded", superseded_by="FOUNDRY-ADR-0002")
    rejected = _migration_adr(2, status="rejected", supersedes=[old["id"]])
    tracker = ScriptedTracker([])
    with pytest.raises(TrackerConflictError, match="remplacement rejected"):
        tracker.migration_preflight(BOUND, (old, rejected))
    assert tracker.calls == []
