"""PAT-165: put a started or in-review issue back to the backlog (guarded)."""

from __future__ import annotations

import copy

import pytest

from foundry import edit as edit_cli
from foundry import issue
from foundry import write
from foundry.models import TransitionContext
from foundry.trackers.base import (
    Tracker,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker
from foundry.trackers.youtrack import YouTrackTracker

from test_abandon_and_dropped_closure import (  # noqa: F401
    DONE,
    REVIEW,
    _add,
    _cockpit,
    _close,
    _graph,
    _isolated_youtrack_epic_closure_intents,
    _native,
    _started,
    _writes,
)
from test_issue_merge import _youtrack_transport
from test_linear_tracker import STATE_IDS


def _back(tracker, project, issue_id, expected):
    return tracker.set_state(
        issue_id, "backlog", context=TransitionContext(expected_state=expected),
        project=project,
    )


@pytest.mark.parametrize("source", ("in-progress", "review"))
def test_return_to_backlog_from_a_started_issue_with_one_native_write(monkeypatch, source):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto=source)
    receipts = copy.deepcopy(wire.issues[issue_id]["comments"])
    before = len(wire.calls)
    seen = _back(tracker, project, issue_id, source)
    assert (seen.state, seen.projection_status) == ("backlog", "aligned")
    assert len(_writes(wire, before)) == 1
    assert wire.issues[issue_id]["state"]["id"] == STATE_IDS["backlog"]
    # Append-only: no receipt added, rewritten or deleted.
    assert wire.issues[issue_id]["comments"] == receipts


def test_return_refuses_a_drift_of_the_expected_state(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="native state is review, expected in-progress"):
        _back(tracker, project, issue_id, "in-progress")
    # A third state fails closed too.
    _native(wire, issue_id, "ready")
    with pytest.raises(TrackerConflictError, match="native state is ready, expected review"):
        _back(tracker, project, issue_id, "review")
    assert not _writes(wire, before)


@pytest.mark.parametrize("expected", ("backlog", "ready", "blocked", "done", "dropped", "x"))
def test_return_refuses_an_inadmissible_expected_state_before_any_read(monkeypatch, expected):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    snapshot = copy.deepcopy(wire.issues[issue_id])
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="Retour au backlog refusé"):
        write.transition(
            tracker, issue_id, "backlog",
            context=TransitionContext(expected_state=expected),
        )
    with pytest.raises(TrackerConflictError, match="expected predecessor"):
        _back(tracker, project, issue_id, expected)
    assert len(wire.calls) == before and wire.issues[issue_id] == snapshot


def test_return_requires_the_expected_state(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    before = len(wire.calls)
    with pytest.raises(SystemExit, match="Retour au backlog refusé"):
        write.transition(tracker, issue_id, "backlog")
    with pytest.raises(TrackerConflictError, match="expected predecessor"):
        tracker.set_state(issue_id, "backlog", project=project)
    assert len(wire.calls) == before


@pytest.mark.parametrize("case", (
    "native-done", "done-receipt-native-regressed", "epic-closed",
    "epic-audit-pending", "invalid-chain",
))
def test_return_is_refused_from_done_a_done_receipt_a_closure_audit_or_a_bad_chain(
    monkeypatch, case,
):
    tracker, wire, project = _graph(monkeypatch)
    issue_id, match = "LIN-2", "native state is done"
    if case == "done-receipt-native-regressed":
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
        wire.issues[issue_id]["comments"]["nodes"][0]["id"] = (
            "00000000-0000-4000-8000-0000000000ff"
        )
        match = "comment id invalid"
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match=match):
        _back(tracker, project, issue_id, "in-progress")
    assert not _writes(wire, before)


def test_return_is_refused_on_a_cockpit_only_receipt_chain(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _add(wire, 5, state="in-progress")
    _cockpit(tracker, wire, issue_id)
    before = len(wire.calls)
    with pytest.raises(TrackerConflictError, match="without a lifecycle state receipt"):
        _back(tracker, project, issue_id, "in-progress")
    assert not _writes(wire, before)


def test_return_replay_converges_without_a_second_effect(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    _back(tracker, project, issue_id, "review")
    snapshot = copy.deepcopy(wire.issues[issue_id])
    before = len(wire.calls)
    for expected in ("review", "in-progress"):
        again = _back(tracker, project, issue_id, expected)
        assert (again.state, again.projection_status) == ("backlog", "aligned")
    assert not _writes(wire, before) and wire.issues[issue_id] == snapshot


@pytest.mark.parametrize("upto", ("in-progress", "review"))
def test_returned_issue_reads_backlog_without_disagreement(monkeypatch, upto):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto=upto)
    _back(tracker, project, issue_id, upto)
    views = (
        tracker.get_issue(issue_id), tracker.observe_issue(issue_id),
        next(item for item in tracker.search(project) if item.id == issue_id),
    )
    for seen in views:
        assert (seen.state, seen.normalized_state, seen.native_state,
                seen.projection_status) == ("backlog", "backlog", "backlog", "aligned")
        assert seen.pr_url == (REVIEW.pr_url if upto == "review" else None)


def test_a_done_receipt_with_a_native_backlog_stays_a_conflict(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    _native(wire, "LIN-2", "backlog")
    with pytest.raises(TrackerConflictError, match="changed outside lifecycle"):
        tracker.get_issue("LIN-2")
    assert tracker.observe_issue("LIN-2").projection_status != "aligned"


def test_a_never_started_backlog_issue_still_reads_native_only(monkeypatch):
    tracker, wire, _project = _graph(monkeypatch)
    issue_id = _add(wire, 5, state="backlog")
    seen = tracker.get_issue(issue_id)
    assert (seen.state, seen.projection_status) == ("backlog", "native-only")


def test_a_returned_in_progress_issue_can_be_started_again_by_replaying_its_receipt(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5)
    _back(tracker, project, issue_id, "in-progress")
    receipts = copy.deepcopy(wire.issues[issue_id]["comments"])
    tracker.set_state(issue_id, "in-progress", project=project)
    seen = tracker.get_issue(issue_id)
    assert (seen.state, seen.projection_status) == ("in-progress", "aligned")
    assert wire.issues[issue_id]["comments"] == receipts


class _GateReached(Exception):
    """Raised by the code-host stub once ``openpr`` passed its state gate."""


@pytest.mark.parametrize("upto", ("in-progress", "review"))
def test_start_after_a_return_writes_native_in_progress_then_open_pr_is_allowed(
    monkeypatch, upto,
):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto=upto)
    _back(tracker, project, issue_id, upto)
    receipts = copy.deepcopy(wire.issues[issue_id]["comments"])
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "issue_binding", lambda *_a: project)
    monkeypatch.setattr(issue, "_prepare_branch", lambda _b: "linked-worktree")
    monkeypatch.setattr(issue, "_sh", lambda *_a, **_k: "")
    before = len(wire.calls)

    issue.start(issue_id)

    assert wire.issues[issue_id]["state"]["id"] == STATE_IDS["in-progress"]
    assert len(_writes(wire, before)) == 1
    assert wire.issues[issue_id]["comments"] == receipts
    seen = tracker.observe_issue(issue_id)
    assert (seen.normalized_state, seen.native_state) == (upto, "in-progress")
    assert seen.projection_status == ("disagreement" if upto == "review" else "aligned")

    def shell(*args, **_kwargs):
        if args == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return f"feat/{issue_id.lower()}-x"
        return ""

    def reached():
        raise _GateReached

    monkeypatch.setattr(issue, "_sh", shell)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: type(
        "CodeHost", (), {"resolve_repo": staticmethod(reached)})())
    with pytest.raises(_GateReached):
        issue.openpr(issue_id)


def test_open_pr_is_still_refused_on_a_returned_issue_that_was_not_restarted(monkeypatch):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    _back(tracker, project, issue_id, "review")
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "issue_binding", lambda *_a: project)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: type(
        "CodeHost", (), {"resolve_repo": staticmethod(lambda: pytest.fail("pushed"))})())
    monkeypatch.setattr(
        issue, "_sh", lambda *_a, **_k: f"feat/{issue_id.lower()}-x",
    )
    with pytest.raises(SystemExit, match="Ouverture PR refusée"):
        issue.openpr(issue_id)


@pytest.mark.parametrize("upto", ("review", "in-progress"))
def test_edit_cli_names_the_linked_pr_and_leaves_it_untouched(monkeypatch, capsys, upto):
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto=upto)
    monkeypatch.setattr(edit_cli.foundry, "tracker", lambda: tracker)
    # PAT-205: instrument the code host itself; any call to it fails the test.
    monkeypatch.setattr(edit_cli.foundry, "codehost", lambda *_a, **_k: pytest.fail("code host used"))
    before = len(wire.calls)
    edit_cli.transition(issue_id, "backlog", upto)
    out = capsys.readouterr().out
    calls = [c for c in wire.calls[before:] if "pull" in repr(c).lower()]
    assert not calls
    if upto == "review":
        assert "PR liée ni fermée ni modifiée" in out and REVIEW.pr_url in out
    else:
        assert "PR liée" not in out


# --- providers: only Linear declares the guarded path ------------------------

def test_only_linear_declares_the_guarded_backlog_return():
    assert LinearTracker.guarded_backlog_return_supported is True
    assert LinearTracker.backlog_return_predecessors == frozenset({"in-progress", "review"})
    for provider in (Tracker, YouTrackTracker, GitHubProjectsTracker, DevHubTracker):
        assert provider.guarded_backlog_return_supported is False
        assert provider.backlog_return_predecessors == frozenset()


@pytest.mark.parametrize("expected", ("in-progress", "review"))
def test_youtrack_keeps_its_bounded_backlog_transition(monkeypatch, expected):
    """Non-regression: base 4cf5ed7 wrote YouTrack State through youtrack.set_state."""
    native = {"state": expected}
    tracker, posts = _youtrack_transport(monkeypatch, native)
    monkeypatch.setattr(write, "issue_binding", lambda *_a: None)
    write.transition(
        tracker, "DEMO-7", "backlog", context=TransitionContext(expected_state=expected),
    )
    assert native["state"] == "backlog" and len(posts) == 1
    # A drifted predecessor is still refused by the adapter, with no write.
    native["state"] = "ready"
    with pytest.raises(TrackerConflictError, match="modifié avant transition"):
        write.transition(
            tracker, "DEMO-7", "backlog",
            context=TransitionContext(expected_state=expected),
        )
    assert native["state"] == "ready" and len(posts) == 1


@pytest.mark.parametrize("expected", ("in-progress", "review"))
def test_github_projects_keeps_its_own_refusal_before_any_read(monkeypatch, expected):
    tracker = object.__new__(GitHubProjectsTracker)

    def boom(*_a, **_k):
        raise AssertionError("provider read")

    monkeypatch.setattr(write, "issue_binding", lambda *_a: None)
    monkeypatch.setattr(GitHubProjectsTracker, "_authoritative_binding", boom)
    monkeypatch.setattr(GitHubProjectsTracker, "_native_issue_read", boom)
    with pytest.raises(TrackerCapabilityUnavailableError) as excinfo:
        write.transition(
            tracker, "DEMO-1", "backlog",
            context=TransitionContext(expected_state=expected),
        )
    assert excinfo.value.capability == "set_state"


def test_other_backlog_transitions_keep_their_current_path(monkeypatch):
    class Legacy:
        name = "legacy"
        calls = []

        def set_state(self, issue_id, state, **kwargs):
            self.calls.append((issue_id, state))

    monkeypatch.setattr(write, "issue_binding", lambda *_a: None)
    write.transition(Legacy(), "DEMO-1", "backlog")
    write.transition(
        Legacy(), "DEMO-1", "backlog", context=TransitionContext(expected_state="ready"),
    )
    assert Legacy.calls == [("DEMO-1", "backlog")] * 2


def test_strict_read_between_start_and_open_pr_fails_closed_after_a_return_from_review(monkeypatch):
    """PAT-205: the limit of PAT-ADR-0024 "Reprise", pinned (documented in linear-tracker.md).

    After review -> backlog -> in-progress, ``_review_regression_proven`` does not recognise
    the history, so a strict read refuses while ``observe_issue`` (which ``issue open-pr``
    replays) reads a disagreement.  A return from ``in-progress`` has no such window.
    """
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    _back(tracker, project, issue_id, "review")
    tracker.set_state(issue_id, "in-progress", project=project)
    with pytest.raises(TrackerConflictError, match="changed outside lifecycle"):
        tracker.get_issue(issue_id)
    assert tracker.observe_issue(issue_id).projection_status == "disagreement"


def test_devhub_refuses_a_proof_in_a_backlog_context():
    """PLP-332: only a bare expected state is admitted for ``backlog``; the cause is named."""
    tracker = object.__new__(DevHubTracker)
    with pytest.raises(ValueError, match="contexte de transition inattendu pour l'état 'backlog'"):
        tracker.set_state("DEMO-1", "backlog", context=TransitionContext(pr_url="https://x/pull/1", expected_state="review"))


@pytest.mark.parametrize("upto", ("in-progress", "review"))
def test_open_pr_after_a_restart_projects_review_to_the_end(monkeypatch, upto):
    """PAT-205: the PR opening goes past ``resolve_repo`` down to the ``review`` projection."""
    from types import SimpleNamespace

    from foundry.models import PullRequest

    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto=upto)
    _back(tracker, project, issue_id, upto)
    tracker.set_state(issue_id, "in-progress", project=project)
    pr = PullRequest(
        number=7, url=REVIEW.pr_url, head=f"feat/{issue_id.lower()}-x", base="main",
        base_sha=REVIEW.base_sha, sha=REVIEW.head_sha,
    )
    codehost = SimpleNamespace(
        name="github", resolve_repo=lambda: "acme/widgets",
        list_prs=lambda *_a: [], open_pr=lambda *_a: pr,
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(write, "issue_binding", lambda *_a: project)
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "git_head", lambda: REVIEW.head_sha)
    monkeypatch.setattr(issue, "git_diff", lambda **_kw: b"diff")
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_a: "observed")
    monkeypatch.setattr(
        issue, "_sh",
        lambda *args, **_kw: pr.head if args[-2:] == ("--abbrev-ref", "HEAD") else "",
    )

    issue.openpr(issue_id)

    seen = tracker.get_issue(issue_id)
    assert (seen.state, seen.native_state, seen.projection_status) == (
        "review", "review", "aligned",
    )
    assert seen.pr_url == REVIEW.pr_url


def test_set_field_state_backlog_stays_an_unguarded_grooming_write(monkeypatch):
    """PAT-205: pins the documented limit; only ``edit transition`` carries the guards."""
    tracker, wire, project = _graph(monkeypatch)
    issue_id = _started(tracker, wire, project, 5, upto="review")
    # No expected state is asked, none is compared: the grooming write goes through.
    write.set_field(tracker, issue_id, "State", "backlog")
    assert wire.issues[issue_id]["state"]["id"] == STATE_IDS["backlog"]
