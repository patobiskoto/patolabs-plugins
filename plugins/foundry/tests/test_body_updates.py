"""Bounded tracker body writes use fakes only; no test reaches a provider."""
from __future__ import annotations

import multiprocessing
import os
import queue
from types import SimpleNamespace

import pytest

from foundry import edit, registry, write
from foundry.models import Adr, Issue
from foundry.routing import acceptance_criteria, acceptance_digest
from foundry.trackers.base import (
    BodyUpdateUnavailableError,
    Tracker,
    TrackerConflictError,
)
from foundry.trackers.youtrack import YouTrackTracker


@pytest.fixture(autouse=True)
def isolated_body_locks(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "foundry-data"))


class ScriptedYouTrack(YouTrackTracker):
    """A stateful in-memory YouTrack wire fake recording each attempted request."""

    def __init__(self, *, issue_body="old", article_body="old", after_write=None):
        self.issue_body = issue_body
        self.article_body = article_body
        self.after_write = after_write
        self.calls = []

    def get_issue(self, issue_id):
        self.calls.append(("GET", f"/issues/{issue_id}"))
        return Issue(id=issue_id, title="issue", body=self.issue_body)

    def _req(self, method, path, body=None, fields=None, top=None):
        self.calls.append((method, path, body, fields))
        if path == "/articles/A-1" and method == "GET":
            return {"content": self.article_body}
        if method == "POST" and path.startswith("/issues/"):
            self.issue_body = body["description"]
            if self.after_write is not None:
                self.issue_body = self.after_write
            return {"idReadable": path.rsplit("/", 1)[1]}
        if method == "POST" and path == "/articles/A-1":
            self.article_body = body["content"]
            if self.after_write is not None:
                self.article_body = self.after_write
            return None
        raise AssertionError((method, path, body, fields))


def _writes(tracker):
    return [call for call in tracker.calls if call[0] == "POST"]


def _hold_body_lock(data_dir, ready, release):
    """Child entrypoint proving flock coordinates separate local CLI processes."""
    os.environ["FOUNDRY_DATA"] = data_dir
    tracker = object.__new__(YouTrackTracker)
    with tracker._body_lock("issue", "T-1"):
        ready.put("locked")
        release.wait(5)


def test_body_lock_serializes_two_local_processes(tmp_path):
    context = multiprocessing.get_context("fork")
    ready, release = context.Queue(), context.Event()
    first = context.Process(target=_hold_body_lock, args=(str(tmp_path), ready, release))
    second = context.Process(target=_hold_body_lock, args=(str(tmp_path), ready, release))
    first.start()
    try:
        assert ready.get(timeout=3) == "locked"
        second.start()
        with pytest.raises(queue.Empty):
            ready.get(timeout=0.1)
        release.set()
        assert ready.get(timeout=3) == "locked"
        first.join(3)
        second.join(3)
        assert first.exitcode == second.exitcode == 0
    finally:
        release.set()
        for process in (first, second):
            if process.pid is not None:
                process.join(3)
                if process.is_alive():
                    process.terminate()
                    process.join(3)
                process.close()
        ready.close()
        ready.join_thread()


def test_edit_body_preserves_crlf_and_stale_snapshot(monkeypatch, tmp_path):
    expected_path, updated_path = tmp_path / "expected.md", tmp_path / "updated.md"
    expected_path.write_bytes(b"- [ ] old\r\n")
    updated_path.write_bytes(b"- [ ] new\r\n")
    calls = []
    tracker = SimpleNamespace(
        requires_mutation_binding=False,
        update_body=lambda resource, expected, updated, **_kwargs: calls.append(
            (resource.id, expected, updated)
        ) or True,
    )
    monkeypatch.setattr(edit.foundry, "tracker", lambda: tracker)

    edit.body("T-1", str(expected_path), str(updated_path))

    assert calls == [("T-1", "- [ ] old\r\n", "- [ ] new\r\n")]


def _proof(issue_id, body, verdicts):
    criteria = acceptance_criteria(body)
    return {
        "proof_id": "a" * 64,
        "issue": {
            "id": issue_id,
            "ac_digest": acceptance_digest(criteria),
            "criteria": [{**criterion, "verdict": verdict}
                         for criterion, verdict in zip(criteria, verdicts)],
        },
    }


def test_youtrack_update_body_refuses_prewrite_divergence_without_write():
    tracker = ScriptedYouTrack(issue_body="human edit")

    with pytest.raises(TrackerConflictError, match="modifié"):
        tracker.update_body(Issue(id="T-1", title="issue"), "old", "new")

    assert _writes(tracker) == []


def test_youtrack_update_body_refuses_postwrite_divergence_without_retry():
    tracker = ScriptedYouTrack(issue_body="old", after_write="other client")

    with pytest.raises(TrackerConflictError, match="après écriture"):
        tracker.update_body(Issue(id="T-1", title="issue"), "old", "new")

    assert _writes(tracker) == [
        ("POST", "/issues/T-1", {"description": "new"}, "idReadable"),
    ]


def test_youtrack_update_body_noop_skips_write():
    tracker = ScriptedYouTrack(issue_body="already current")

    assert tracker.update_body(
        Issue(id="T-1", title="issue"), "already current", "already current",
    ) is False
    assert _writes(tracker) == []


def test_youtrack_update_body_writes_and_reads_back_exact_issue_body():
    tracker = ScriptedYouTrack(issue_body="old")

    assert tracker.update_body(Issue(id="T-1", title="issue"), "old", "new") is True
    assert tracker.issue_body == "new"
    assert _writes(tracker) == [
        ("POST", "/issues/T-1", {"description": "new"}, "idReadable"),
    ]


def test_youtrack_update_body_refuses_stale_snapshot_even_when_desired_is_current():
    tracker = ScriptedYouTrack(issue_body="current")

    with pytest.raises(TrackerConflictError, match="modifié"):
        tracker.update_body(Issue(id="T-1", title="issue"), "stale", "current")
    assert _writes(tracker) == []


def test_youtrack_acceptance_sync_handles_body_without_markers_as_noop():
    tracker = ScriptedYouTrack(issue_body="plain body")

    with pytest.raises(ValueError, match="preuve invalide"):
        tracker.sync_acceptance_body("T-1", "plain body", "plain body", {"proof_id": "a" * 64})
    assert _writes(tracker) == []


def test_youtrack_acceptance_sync_refuses_prose_diff_outside_markers():
    tracker = ScriptedYouTrack(issue_body="- [ ] one\n")

    with pytest.raises(ValueError, match="hors marqueurs"):
        tracker.sync_acceptance_body(
            "T-1", "- [ ] one\n", "- [x] changed prose\n",
            _proof("T-1", "- [ ] one\n", ["pass"]),
        )
    assert _writes(tracker) == []


def test_youtrack_update_body_handles_adr_article():
    tracker = ScriptedYouTrack(article_body="old ADR")
    adr = Adr(id="T-ADR-0001", title="decision", body="old ADR", ref="A-1")

    assert tracker.update_body(adr, "old ADR", "new ADR") is True
    assert tracker.article_body == "new ADR"
    assert _writes(tracker) == [
        ("POST", "/articles/A-1", {"content": "new ADR"}, None),
    ]


def test_write_sync_acceptance_reports_exact_checked_count(monkeypatch):
    tracker = ScriptedYouTrack(issue_body="- [ ] one\n- [ ] two\n")
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    proof = _proof("T-1", tracker.issue_body, ["pass", "fail"])

    assert write.sync_acceptance(tracker, "T-1", tracker.issue_body, proof) == {
        "status": "updated", "checked": 1, "audit": "provider",
    }


_ARCHIVED_REGISTRY = {"youtrack": {
    "claude-plugins": {"key": "FOUNDRY", "id": "0-3", "ms_bundle": "163-7"},
    "patolabs-plugins": {
        "key": "FOUNDRY", "id": "0-3", "ms_bundle": "163-7", "archive": True,
    },
    "OrfeoApp": {"key": "ORFEO", "id": "0-1"},
    "orfeo-alias": {"key": "ORFEO", "id": "0-1"},
}}


@pytest.mark.parametrize("checkout", ("claude-plugins", "patolabs-plugins"))
def test_youtrack_archive_stays_readable_but_write_tier_refuses_before_effect(
    monkeypatch, checkout,
):
    tracker = ScriptedYouTrack()
    monkeypatch.setattr(registry, "load", lambda: _ARCHIVED_REGISTRY)
    monkeypatch.setattr(registry, "repo_basename", lambda _cwd=None: checkout)
    monkeypatch.setattr(
        registry,
        "checkout_repository_identity",
        lambda _cwd=None: pytest.fail("YouTrack writes keep legacy resolution"),
    )

    # Both the archived alias and a sibling alias of the same native project are
    # refused before any provider request.
    with pytest.raises(SystemExit, match="archiv"):
        write.set_field(tracker, "FOUNDRY-159", "Priority", "P1")
    assert tracker.calls == []


@pytest.mark.parametrize("checkout", ("OrfeoApp", "orfeo-alias", "unregistered-repo"))
def test_youtrack_writes_keep_legacy_resolution_outside_a_tombstone(
    monkeypatch, checkout,
):
    # Mixed-case names, PROJECT_REPO-style aliases and unregistered checkouts keep
    # the historical behaviour: no canonical-remote lookup, no new refusal.
    tracker = ScriptedYouTrack()
    monkeypatch.setattr(registry, "load", lambda: _ARCHIVED_REGISTRY)
    monkeypatch.setattr(registry, "repo_basename", lambda _cwd=None: checkout)
    monkeypatch.setattr(
        registry,
        "checkout_repository_identity",
        lambda _cwd=None: pytest.fail("YouTrack writes keep legacy resolution"),
    )

    assert write.mutation_project(tracker) is None
    assert write.update_issue_body(tracker, "ORFEO-7", "old", "new")
    assert tracker.calls


def test_unsupported_tracker_body_write_fails_explicitly():
    with pytest.raises(BodyUpdateUnavailableError, match="abstract"):
        Tracker.update_body(
            SimpleNamespace(name="abstract"), Issue(id="T-1", title="issue"), "old", "new",
        )


@pytest.mark.parametrize("marker", ["-", "*", "+"])
def test_acceptance_sync_preserves_crlf_and_only_checks_proven_criteria(marker):
    original = f"{marker} [ ] one\r\n{marker} [ ] two\r\n"
    tracker = ScriptedYouTrack(issue_body=original)
    proof = _proof("T-1", original, ["pass", "fail"])
    wanted = original.replace("[ ] one", "[x] one")
    assert tracker.sync_acceptance_body("T-1", original, wanted, proof)
    assert tracker.issue_body == wanted
    assert write.sync_acceptance(tracker, "T-1", wanted, proof) == {
        "status": "unchanged", "checked": 0,
    }


def test_edit_body_refuses_snapshot_changed_while_preparing_amendment(monkeypatch, tmp_path):
    expected, updated = tmp_path / "expected.md", tmp_path / "updated.md"
    expected.write_bytes(b"old\r\n")
    updated.write_bytes(b"wanted\r\n")
    tracker = ScriptedYouTrack(issue_body="human edit\r\n")
    monkeypatch.setattr(edit.foundry, "tracker", lambda: tracker)
    with pytest.raises(TrackerConflictError):
        edit.body("T-1", str(expected), str(updated))
    assert _writes(tracker) == []
