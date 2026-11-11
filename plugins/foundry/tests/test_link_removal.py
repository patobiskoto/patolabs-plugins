"""PLP-363: `edit unlink` removes a depends-on/blocks link; other trackers refuse."""
import json
import urllib.error

import pytest

from foundry import edit, write
from foundry.trackers.base import Tracker, TrackerCapabilityUnavailableError
from foundry.trackers.devhub import DevHubTracker, DevHubTrackerError
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker

from test_devhub_tracker import (
    PROJECT, SECRET, TOKEN, FakeResponse, ScriptedTracker, issue_raw,
)

CONTRACT = json.loads(
    (__import__("pathlib").Path(__file__).resolve().parents[1]
     / "docs" / "tracker-contract.v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("link_type", ["depends-on", "blocks"])
def test_devhub_unlink_sends_the_delete_with_both_versions(link_type):
    tracker = ScriptedTracker([
        issue_raw(version=7), issue_raw(id="TRAME-9", version=11), issue_raw(version=8),
    ])

    tracker.unlink("TRAME-1", link_type, "TRAME-9", project=PROJECT)

    method, path, body, kwargs = tracker.calls[-1]
    assert (method, path) == ("DELETE", "/issues/TRAME-1/links")
    assert body == {"type": link_type, "target": "TRAME-9", "target_version": 11}
    assert kwargs["version"] == 7 and kwargs["idempotent"] is True


@pytest.mark.parametrize("link_type", ["relates", "subtask-of", "parent-of", "nope"])
def test_devhub_unlink_refuses_other_types_before_any_request(link_type):
    tracker = ScriptedTracker([])

    with pytest.raises(ValueError):
        tracker.unlink("TRAME-1", link_type, "TRAME-9", project=PROJECT)

    assert tracker.calls == []


@pytest.mark.parametrize(("status", "code"), [
    (409, "version_conflict"), (409, "resource_conflict"), (400, "graph_conflict"),
    (404, "issue_unavailable"), (428, "version_required"), (409, "idempotency_conflict"),
])
def test_devhub_unlink_propagates_the_server_refusals_unchanged(status, code):
    refusal = DevHubTrackerError("DELETE", "/issues/TRAME-1/links", status, code)
    tracker = ScriptedTracker([issue_raw(version=7), issue_raw(id="TRAME-9"), refusal])

    with pytest.raises(DevHubTrackerError) as captured:
        tracker.unlink("TRAME-1", "depends-on", "TRAME-9", project=PROJECT)

    assert (captured.value.status, captured.value.code) == (status, code)


def test_devhub_unlink_headers_contract_and_one_key_across_a_retry(monkeypatch):
    requests = []

    class ApplicationResponse(FakeResponse):
        headers = {"X-DevHub-Contract": "devhub-application.v1"}

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.URLError("response lost after provider write")
            return ApplicationResponse({"id": "TRAME-1", "version": 9})

    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener())
    tracker = DevHubTracker(url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET)
    monkeypatch.setattr(tracker, "_require_issue_binding", lambda *_args: "TRAME")
    versions = {"TRAME-1": 7, "TRAME-2": 11}
    monkeypatch.setattr(
        tracker, "_bound_issue_raw", lambda issue_id, _project: {"version": versions[issue_id]})

    tracker.unlink("TRAME-1", "blocks", "TRAME-2", project=PROJECT)

    assert len(requests) == 2
    assert {request.get_method() for request in requests} == {"DELETE"}
    assert all(request.full_url.endswith("/api/tracker/v1/issues/TRAME-1/links")
               for request in requests)
    assert all(request.get_header("X-devhub-version") == "7" for request in requests)
    assert [json.loads(request.data) for request in requests] == [
        {"type": "blocks", "target": "TRAME-2", "target_version": 11}] * 2
    keys = [request.get_header("Idempotency-key") for request in requests]
    assert keys[0] == keys[1] and keys[0].startswith("foundry-")


def test_devhub_unlink_refuses_a_contract_mismatch(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            return FakeResponse({"id": "TRAME-1"})  # devhub-tracker.v1, not application

    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener())
    tracker = DevHubTracker(url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET)
    monkeypatch.setattr(tracker, "_require_issue_binding", lambda *_args: "TRAME")
    monkeypatch.setattr(tracker, "_bound_issue_raw", lambda *_args: {"version": 1})

    with pytest.raises(DevHubTrackerError) as captured:
        tracker.unlink("TRAME-1", "depends-on", "TRAME-2", project=PROJECT)

    assert captured.value.code == "contract_mismatch"


@pytest.mark.parametrize("provider", [YouTrackTracker, LinearTracker, GitHubProjectsTracker])
def test_other_providers_do_not_declare_link_removal(provider):
    assert provider.link_removal_supported is False
    assert provider.unlink is Tracker.unlink


def test_write_unlink_refuses_without_the_capability_and_calls_nothing():
    class Stub:
        name = "stub"
        link_removal_supported = False

        def unlink(self, *args, **kwargs):
            raise AssertionError("must not be called")

    with pytest.raises(TrackerCapabilityUnavailableError):
        write.unlink(Stub(), "A-1", "depends-on", "A-2")


def test_base_unlink_default_refuses():
    with pytest.raises(TrackerCapabilityUnavailableError):
        Tracker.unlink(type("T", (), {"name": "t"})(), "A-1", "depends-on", "A-2")


def test_cli_dispatch_calls_the_tracker_and_reports(monkeypatch, capsys):
    calls = []

    class Stub:
        name = "devhub"
        link_removal_supported = True

        def unlink(self, *args, **kwargs):
            calls.append(args)

    monkeypatch.setattr(edit.foundry, "tracker", lambda: Stub())
    monkeypatch.setattr(write, "issue_binding", lambda *_a, **_k: None)

    assert edit.main(["unlink", "A-1", "blocks", "A-2"]) == 0
    assert calls == [("A-1", "blocks", "A-2")]
    assert "A-1 blocks A-2" in capsys.readouterr().out


def test_cli_refuses_cleanly_on_an_unsupported_tracker(monkeypatch):
    class Stub:
        name = "linear"
        link_removal_supported = False

        def unlink(self, *args, **kwargs):
            raise AssertionError("must not be called")

    monkeypatch.setattr(edit.foundry, "tracker", lambda: Stub())

    with pytest.raises(SystemExit) as refused:
        edit.main(["unlink", "A-1", "depends-on", "A-2"])
    assert "linear" in str(refused.value) and "rien n'a été écrit" in str(refused.value)


def test_cli_refuses_a_non_removable_type_before_any_call(monkeypatch):
    class Stub:
        name = "devhub"
        link_removal_supported = True

        def unlink(self, *args, **kwargs):
            raise AssertionError("must not be called")

    monkeypatch.setattr(edit.foundry, "tracker", lambda: Stub())
    with pytest.raises(SystemExit):
        edit.main(["unlink", "A-1", "relates", "A-2"])


def test_cli_usage_lists_unlink_and_help_prints_its_entry(capsys):
    assert edit.main(["unlink", "--help"]) == 0
    assert "foundry.edit unlink <SRC-ID> <link-type> <DST-ID>" in capsys.readouterr().out


def test_contract_json_cells_follow_the_declared_flags():
    row = next(op for op in CONTRACT["operations"] if op["id"] == "dependency-removal")
    assert row["core"] is False and row["tracker_abc"] == ["unlink(depends-on|blocks)"]
    flags = {"youtrack": YouTrackTracker, "linear": LinearTracker,
             "ghprojects": GitHubProjectsTracker, "devhub": DevHubTracker}
    assert set(row["cells"]) == set(flags)
    for name, cls in flags.items():
        expected = "supported" if cls.link_removal_supported else "refused"
        assert row["cells"][name]["status"] == expected, name
    doc = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "docs" / "tracker-contract.md").read_text(encoding="utf-8")
    assert "`edit unlink <SRC-ID> <link-type> <DST-ID>`" in doc
