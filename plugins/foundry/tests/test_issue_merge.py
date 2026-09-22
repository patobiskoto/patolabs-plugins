"""merge() must REPORT the squash SHA that landed on the default branch.

The ship-ios release flow tags exactly that commit (a tag on the local pre-merge
commit would point outside the default branch after a squash merge). The merge
endpoint returns the sha; if the CLI doesn't print it, the release skill has no
deterministic way to obtain it — origin/<default> is racy, another PR may land
in between.
"""
import hashlib
import io
from types import SimpleNamespace

import pytest

from foundry import issue, write
from foundry.models import Project, TransitionContext
from foundry.routing import (
    RoutingConfigError,
    acceptance_criteria,
    acceptance_digest,
)
from foundry.trackers.base import Tracker, TrackerConflictError
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.youtrack import YouTrackTracker


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("backlog", ("ready", "in-progress")),
        ("ready", ("in-progress",)),
        ("in-progress", ()),
        ("review", ("in-progress",)),
        ("blocked", ("in-progress",)),
    ],
)
def test_devhub_start_preflight_matches_its_constrained_workflow(current, expected):
    assert DevHubTracker.start_transition_path(object(), current) == expected


@pytest.mark.parametrize("terminal", ["done", "dropped", "unknown"])
def test_devhub_start_preflight_refuses_terminal_or_unknown_states(terminal):
    with pytest.raises(ValueError, match="non démarrable"):
        DevHubTracker.start_transition_path(object(), terminal)


def test_default_tracker_start_preflight_preserves_youtrack_direct_transition():
    assert YouTrackTracker.start_transition_path(object(), "backlog") == ("in-progress",)
    assert YouTrackTracker.start_transition_path(object(), "in-progress") == ()
    assert Tracker.start_transition_path(object(), "ready") == ("in-progress",)


@pytest.mark.parametrize("provider_class", [YouTrackTracker, DevHubTracker])
def test_existing_real_provider_openpr_path_is_unchanged_by_noop_preflight(
    monkeypatch, provider_class,
):
    events = []
    tracker = object.__new__(provider_class)
    tracker.get_issue = lambda _issue_id: events.append("tracker:get-issue") or SimpleNamespace(
        title="Pilot", type="Feature",
    )
    branch = "feat/demo-7-pilot"
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12",
        sha="a" * 40, base_sha="c" * 40, head=branch, base="main",
        state="open",
    )
    codehost = SimpleNamespace(
        name="github",
        resolve_repo=lambda: events.append("codehost:resolve") or "acme/demo",
        list_prs=lambda *_args: events.append("codehost:list-prs") or [],
        open_pr=lambda *_args: events.append("codehost:open-pr") or pull_request,
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(
        Tracker, "preflight_issue_operation",
        lambda self, operation: events.append(f"tracker:preflight:{self.name}:{operation}"),
    )
    monkeypatch.setattr(
        write, "issue_binding", lambda *_args: events.append("tracker:binding"),
    )

    def command(*args, **_kwargs):
        if args[-2:] == ("--abbrev-ref", "HEAD"):
            return branch
        if args[:2] == ("git", "push"):
            events.append("codehost:push")
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(issue, "_sh", command)
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_args: "ignored")
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: b"exact-diff")
    monkeypatch.setattr(
        write, "set_field",
        lambda *_args, **_kwargs: events.append("tracker:set-pr"),
    )
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, _issue_id, state, context=None: events.append(
            f"tracker:transition:{state}"
        ),
    )

    issue.openpr("DEMO-7")

    assert events[:7] == [
        "tracker:binding",
        f"tracker:preflight:{tracker.name}:openpr",
        "tracker:get-issue",
        "codehost:resolve",
        "codehost:push",
        "codehost:list-prs",
        "codehost:open-pr",
    ]
    if tracker.bounded_transition_proofs:
        assert events[-2:] == ["tracker:set-pr", "tracker:transition:review"]
    else:
        assert events[-2:] == ["tracker:transition:review", "tracker:set-pr"]


@pytest.mark.parametrize("provider_class", [YouTrackTracker, DevHubTracker])
def test_existing_real_provider_merge_path_is_unchanged_by_noop_preflight(
    monkeypatch, provider_class,
):
    events = []
    tracker = object.__new__(provider_class)
    tracker.get_issue = lambda _issue_id: events.append("tracker:get-issue") or SimpleNamespace(
        id="DEMO-7", state="review", body="", ac_done=0, ac_total=0,
    )
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12",
        sha="a" * 40, base_sha="c" * 40, head="feat/demo-7-pilot",
        base="main", merged=False,
    )
    landed = SimpleNamespace(
        sha="b" * 40, head=pull_request.head, merged=True,
    )
    codehost = SimpleNamespace(
        name="github",
        resolve_repo=lambda: events.append("codehost:resolve") or "acme/demo",
        get_pr=lambda *_args: events.append("codehost:get-pr") or pull_request,
        merge_pr=lambda *_args, **_kwargs: events.append("codehost:merge") or landed,
        delete_branch=lambda *_args: events.append("codehost:delete-branch"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(
        Tracker, "preflight_issue_operation",
        lambda self, operation: events.append(f"tracker:preflight:{self.name}:{operation}"),
    )
    monkeypatch.setattr(
        write, "issue_binding", lambda *_args: events.append("tracker:binding"),
    )
    monkeypatch.setattr(
        write, "ci_gate", lambda *_args, **_kwargs: events.append("codehost:ci") or {
            "passed": True, "waived": False, "total": 1,
            "pending": [], "failing": [],
        },
    )
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, _issue_id, state, context=None: events.append(
            f"tracker:transition:{state}"
        ),
    )
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_args: "ignored")
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: b"exact-diff")
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")

    issue.merge("DEMO-7", "12")

    assert events[:5] == [
        "tracker:binding",
        f"tracker:preflight:{tracker.name}:merge",
        "codehost:resolve",
        "codehost:get-pr",
        "tracker:get-issue",
    ]
    assert events.index(f"tracker:preflight:{tracker.name}:merge") < events.index(
        "codehost:merge"
    )
    if tracker.bounded_transition_proofs:
        assert events.index("tracker:transition:done") < events.index(
            "codehost:delete-branch"
        )
    else:
        assert events.index("codehost:delete-branch") < events.index(
            "tracker:transition:done"
        )


def test_issue_start_preflights_before_branch_then_applies_provider_path(monkeypatch):
    events = []
    tracker = SimpleNamespace(
        get_issue=lambda _id: SimpleNamespace(
            title="Pilot", type="Feature", state="backlog",
        ),
        start_transition_path=lambda state: events.append(("preflight", state))
        or ("ready", "in-progress"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue, "_sh", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(write, "issue_binding", lambda *_args: events.append("binding"))
    monkeypatch.setattr(
        issue, "_prepare_branch",
        lambda branch: events.append(("branch", branch)) or "linked-worktree",
    )
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, _issue_id, state: events.append(("transition", state)),
    )

    issue.start("DEMO-7")

    assert events == [
        "binding",
        ("preflight", "backlog"),
        ("branch", "feat/demo-7-pilot"),
        ("transition", "ready"),
        ("transition", "in-progress"),
    ]


def test_issue_start_retry_resumes_after_partial_tracker_transition(monkeypatch):
    events = []
    tracker = SimpleNamespace(state="backlog")
    tracker.get_issue = lambda _id: SimpleNamespace(
        title="Pilot", type="Feature", state=tracker.state,
    )
    tracker.start_transition_path = lambda state: DevHubTracker.start_transition_path(
        object(), state,
    )
    branch_modes = iter(("linked-worktree", "existing"))
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue, "_sh", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    monkeypatch.setattr(issue, "_prepare_branch", lambda _branch: next(branch_modes))

    fail_once = True

    def transition(_tracker, _issue_id, state):
        nonlocal fail_once
        events.append(state)
        if state == "in-progress" and fail_once:
            fail_once = False
            raise RuntimeError("provider unavailable")
        tracker.state = state

    monkeypatch.setattr(write, "transition", transition)

    with pytest.raises(SystemExit, match=r"relance exactement `issue start DEMO-7`"):
        issue.start("DEMO-7")
    assert tracker.state == "ready"

    issue.start("DEMO-7")

    assert events == ["ready", "in-progress", "in-progress"]
    assert tracker.state == "in-progress"


def test_issue_start_already_in_progress_reuses_branch_without_transition(monkeypatch):
    tracker = SimpleNamespace(
        get_issue=lambda _id: SimpleNamespace(
            title="Pilot", type="Feature", state="in-progress",
        ),
        start_transition_path=lambda _state: (),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue, "_sh", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    monkeypatch.setattr(issue, "_prepare_branch", lambda _branch: "existing")
    monkeypatch.setattr(
        write, "transition",
        lambda *_args: (_ for _ in ()).throw(AssertionError("duplicate transition")),
    )

    issue.start("DEMO-7")


def test_issue_start_refused_preflight_does_not_create_branch(monkeypatch):
    events = []
    tracker = SimpleNamespace(
        get_issue=lambda _id: SimpleNamespace(
            title="Pilot", type="Feature", state="done",
        ),
        start_transition_path=lambda _state: (_ for _ in ()).throw(ValueError("terminal")),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue, "_sh", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(write, "issue_binding", lambda *_args: None)
    monkeypatch.setattr(issue, "_prepare_branch", lambda _branch: events.append("branch"))

    with pytest.raises(SystemExit, match="avant toute mutation Git"):
        issue.start("DEMO-7")

    assert events == []


def test_prepare_branch_reuses_an_existing_local_branch(monkeypatch):
    events = []

    def command(*args, **_kwargs):
        if args[-2:] == ("--abbrev-ref", "HEAD"):
            return "main"
        if args[-2:] == ("--verify", "refs/heads/fix/demo-7"):
            return "a" * 40
        if args[-2:] == ("checkout", "fix/demo-7"):
            events.append("checkout")
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(issue, "_sh", command)

    assert issue._prepare_branch("fix/demo-7") == "existing"
    assert events == ["checkout"]


def test_prepare_branch_reports_exact_recovery_when_owned_by_another_worktree(
    monkeypatch,
):
    def command(*args, **_kwargs):
        if args[-2:] == ("--abbrev-ref", "HEAD"):
            return "main"
        if args[-2:] == ("--verify", "refs/heads/fix/demo-7"):
            return "a" * 40
        if args[-2:] == ("checkout", "fix/demo-7"):
            raise SystemExit("already checked out")
        raise AssertionError(args)

    monkeypatch.setattr(issue, "_sh", command)

    with pytest.raises(SystemExit, match="reprends `issue start` depuis ce worktree"):
        issue._prepare_branch("fix/demo-7")


def test_merge_reports_the_merged_sha(monkeypatch, capsys):
    head = SimpleNamespace(sha="headsha1234", head="chore/demo-7-release", merged=False)
    landed = SimpleNamespace(sha="a1b2c3d4e5f6a7b8", head=head.head, merged=True)
    ch = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        get_pr=lambda repo, n: head,
        merge_pr=lambda repo, n, method="squash", sha=None: landed,
        delete_branch=lambda repo, branch: None,
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: SimpleNamespace(
        get_issue=lambda _id: SimpleNamespace(id="DEMO-7", body="", ac_done=0, ac_total=0),
    ))
    monkeypatch.setattr(issue.foundry, "codehost", lambda: ch)
    monkeypatch.setattr(write, "ci_gate", lambda *a, **k: {
        "passed": True, "waived": False, "total": 2, "pending": 0, "failing": 0})
    monkeypatch.setattr(write, "transition", lambda *a, **k: None)
    monkeypatch.setattr(issue, "_cleanup_branch", lambda branch: "main-checkout")

    issue.merge("DEMO-7", "12")

    assert "a1b2c3d4e5f6a7b8" in capsys.readouterr().out


def test_proof_bound_tracker_persists_done_receipt_before_branch_delete(monkeypatch):
    events = []
    diff_bases = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(
            id="DEMO-7", state="review", body="", ac_done=0, ac_total=0,
        ),
    )
    head = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="release", merged=False,
        base_sha="c" * 40,
        url="https://github.com/acme/demo/pull/12",
    )
    landed = SimpleNamespace(sha="b" * 40, head=head.head, merged=True)
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo", get_pr=lambda *_: head,
        merge_pr=lambda *_args, **_kwargs: landed,
        delete_branch=lambda *_args: events.append(("delete",)),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(
        issue, "git_diff",
        lambda *args, **kwargs: diff_bases.append(kwargs.get("base")) or b"exact-diff",
    )
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": True, "waived": False, "total": 1, "pending": [], "failing": [],
    })

    def transition(_tracker, issue_id, state, context=None):
        events.append(("transition", issue_id, state, context))

    monkeypatch.setattr(write, "transition", transition)
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")

    issue.merge("DEMO-7", "12")

    assert [event[0] for event in events] == ["transition", "transition", "transition", "delete"]
    assert [event[2] for event in events[:3]] == ["in-progress", "review", "done"]
    assert events[1][3].merge_sha is None
    context = events[2][3]
    assert isinstance(context, TransitionContext)
    assert context.head_sha == "a" * 40
    assert context.base_sha == "c" * 40
    assert context.review_digest == hashlib.sha256(b"exact-diff").hexdigest()
    assert context.merge_sha == "b" * 40
    assert diff_bases == ["c" * 40]


def test_proof_bound_retry_uses_existing_merge_receipt_without_merging_twice(monkeypatch):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(
            id="DEMO-7", state="review", body="", ac_done=0, ac_total=0,
        ),
    )
    pr = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="main", merged=True,
        base_sha="c" * 40,
        merge_sha="b" * 40, url="https://github.com/acme/demo/pull/12",
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo", get_pr=lambda *_: pr,
        merge_pr=lambda *_args, **_kwargs: events.append(("merge-again",)),
        delete_branch=lambda *_args: events.append(("delete",)),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(issue, "git_diff", lambda *args, **kwargs: b"exact-diff")
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": True, "waived": False, "total": 1, "pending": [], "failing": [],
    })
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, _issue_id, state, context=None: events.append(("transition", state, context)),
    )
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")

    issue.merge("DEMO-7", "12")

    assert "merge-again" not in [event[0] for event in events]
    done = next(event for event in events if event[:2] == ("transition", "done"))
    assert done[2].base_sha == "c" * 40
    assert done[2].merge_sha == "b" * 40
    assert events[-1] == ("delete",)


def test_already_done_exact_pr_retry_only_finishes_cleanup(monkeypatch, capsys):
    events = []
    pr_url = "https://github.com/acme/demo/pull/12"
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(
            id="DEMO-7", state="done", pr_url=pr_url, body="", ac_done=1, ac_total=1,
        ),
    )
    pr = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="main", merged=True,
        base_sha="c" * 40,
        merge_sha="b" * 40, url=pr_url,
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo", get_pr=lambda *_: pr,
        merge_pr=lambda *_args, **_kwargs: events.append(("merge-again",)),
        delete_branch=lambda *_args: events.append(("delete",)),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: (_ for _ in ()).throw(AssertionError("git")))
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("diff")))
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CI")))
    monkeypatch.setattr(write, "transition", lambda *_args, **_kwargs: events.append(("rewind",)))
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: events.append(("cleanup",)) or "linked-worktree")

    issue.merge("DEMO-7", "12")

    assert events == [("delete",), ("cleanup",)]
    assert "déjà done" in capsys.readouterr().out


def test_already_done_retry_refuses_mismatched_pr_receipt(monkeypatch):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(
            id="DEMO-7", state="done", pr_url="https://github.com/acme/demo/pull/11",
            body="", ac_done=1, ac_total=1,
        ),
    )
    pr = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="main", merged=True,
        base_sha="c" * 40,
        merge_sha="b" * 40, url="https://github.com/acme/demo/pull/12",
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo", get_pr=lambda *_: pr,
        merge_pr=lambda *_args, **_kwargs: events.append(("merge",)),
        delete_branch=lambda *_args: events.append(("delete",)),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)

    with pytest.raises(SystemExit, match="reçu exact"):
        issue.merge("DEMO-7", "12")
    assert events == []


def test_proof_bound_openpr_records_pr_before_review_with_exact_coordinates(monkeypatch):
    events = []
    diff_bases = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(title="Pilot", type="Feature"),
    )
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12", sha="a" * 40,
        base_sha="c" * 40,
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        list_prs=lambda *_args: [],
        open_pr=lambda *_args: pull_request,
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_sh", lambda *args, **_kwargs: (
        "feat/demo-7-pilot" if args[-2:] == ("--abbrev-ref", "HEAD") else ""
    ))
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(
        issue, "git_diff",
        lambda *args, **kwargs: diff_bases.append(kwargs.get("base")) or b"exact-open-pr-diff",
    )
    monkeypatch.setattr(
        write, "set_field",
        lambda _tracker, issue_id, name, value: events.append(("field", issue_id, name, value)),
    )
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, issue_id, state, context=None: events.append(
            ("transition", issue_id, state, context)
        ),
    )

    issue.openpr("DEMO-7", base="release")

    assert [event[0] for event in events] == ["field", "transition"]
    context = events[1][3]
    assert context.pr_url == pull_request.url
    assert context.head_sha == "a" * 40
    assert context.base_sha == "c" * 40
    assert context.review_digest == hashlib.sha256(b"exact-open-pr-diff").hexdigest()
    assert diff_bases == ["c" * 40]


@pytest.mark.parametrize("base_sha", [None, "origin/main", "C" * 40, "c" * 39])
def test_proof_bound_openpr_rejects_invalid_api_base_before_tracker_write(
    monkeypatch, base_sha,
):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(title="Pilot", type="Feature"),
    )
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12",
        sha="a" * 40, base_sha=base_sha,
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        list_prs=lambda *_args: [],
        open_pr=lambda *_args: pull_request,
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_sh", lambda *args, **_kwargs: (
        "feat/demo-7-pilot" if args[-2:] == ("--abbrev-ref", "HEAD") else ""
    ))
    monkeypatch.setattr(
        issue, "git_head", lambda: (_ for _ in ()).throw(AssertionError("head")),
    )
    monkeypatch.setattr(
        issue, "git_diff", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("diff")),
    )
    monkeypatch.setattr(
        write, "set_field", lambda *_args, **_kwargs: events.append("field"),
    )
    monkeypatch.setattr(
        write, "transition", lambda *_args, **_kwargs: events.append("transition"),
    )

    with pytest.raises(SystemExit, match="SHA de base GitHub"):
        issue.openpr("DEMO-7")

    assert events == []


def test_openpr_reuses_existing_pr_and_preserves_required_body_markers(
    monkeypatch, capsys,
):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=False,
        get_issue=lambda _id: SimpleNamespace(title="Pilot", type="Feature"),
    )
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12", sha="a" * 40,
        base_sha="c" * 40, head="feat/demo-7-pilot", base="main",
        state="open", merged=False,
    )

    def update_pr(_repo, number, title, body):
        events.append(("update", number, title, body))
        return pull_request

    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        list_prs=lambda *_args: [pull_request],
        update_pr=update_pr,
        open_pr=lambda *_args: events.append(("open",)),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_sh", lambda *args, **_kwargs: (
        "feat/demo-7-pilot" if args[-2:] == ("--abbrev-ref", "HEAD") else ""
    ))
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue.sys, "stdin", io.StringIO("## Summary\n- Correctif relu"))
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, issue_id, state: events.append(("transition", issue_id, state)),
    )
    monkeypatch.setattr(
        write, "set_field",
        lambda _tracker, issue_id, name, value: events.append(
            ("field", issue_id, name, value)
        ),
    )

    issue.openpr("DEMO-7", flags={"--summary-stdin"})

    assert not [event for event in events if event[0] == "open"]
    update = next(event for event in events if event[0] == "update")
    assert "## Summary\n- Correctif relu" in update[3]
    assert "Closes tracker issue **DEMO-7**." in update[3]
    assert "Generated through [patolabs Foundry]" in update[3]
    assert [event[0] for event in events[-2:]] == ["transition", "field"]
    assert "PR #12 réutilisée" in capsys.readouterr().out


def test_openpr_reuse_without_supplied_summary_preserves_existing_body(monkeypatch):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=False,
        get_issue=lambda _id: SimpleNamespace(title="Pilot", type="Feature"),
    )
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12", sha="a" * 40,
        base_sha="c" * 40, head="feat/demo-7-pilot", base="main",
        state="open", merged=False,
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        list_prs=lambda *_args: [pull_request],
        get_pr=lambda *_args: events.append("read") or pull_request,
        update_pr=lambda *_args: events.append("update"),
        open_pr=lambda *_args: events.append("open"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_sh", lambda *args, **_kwargs: (
        "feat/demo-7-pilot" if args[-2:] == ("--abbrev-ref", "HEAD") else ""
    ))
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, issue_id, state: events.append(("transition", issue_id, state)),
    )
    monkeypatch.setattr(
        write, "set_field",
        lambda _tracker, issue_id, name, value: events.append(
            ("field", issue_id, name, value)
        ),
    )

    issue.openpr("DEMO-7")

    assert events[0] == "read"
    assert "update" not in events
    assert "open" not in events
    assert [event[0] for event in events[-2:]] == ["transition", "field"]


def test_proof_bound_openpr_reuse_refreshes_current_review_coordinates(monkeypatch):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(title="Pilot", type="Feature"),
    )
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12", sha="a" * 40,
        base_sha="c" * 40, head="feat/demo-7-pilot", base="main",
        state="open", merged=False,
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        list_prs=lambda *_args: [pull_request],
        get_pr=lambda *_args: pull_request,
        open_pr=lambda *_args: events.append(("open",)),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_sh", lambda *args, **_kwargs: (
        "feat/demo-7-pilot" if args[-2:] == ("--abbrev-ref", "HEAD") else ""
    ))
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(issue, "git_diff", lambda **kwargs: b"current-remediation-diff")
    monkeypatch.setattr(
        write, "set_field",
        lambda _tracker, issue_id, name, value: events.append(
            ("field", issue_id, name, value)
        ),
    )
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, issue_id, state, context=None: events.append(
            ("transition", issue_id, state, context)
        ),
    )

    issue.openpr("DEMO-7")

    assert not [event for event in events if event[0] == "open"]
    context = events[-1][3]
    assert context.head_sha == "a" * 40
    assert context.base_sha == "c" * 40
    assert context.review_digest == hashlib.sha256(b"current-remediation-diff").hexdigest()


@pytest.mark.parametrize(
    ("candidates", "message"),
    [
        ([SimpleNamespace(number=12, state="open", base="release")], "cible 'release'"),
        ([SimpleNamespace(number=12, state="closed", base="main")], "PR fermée"),
        ([
            SimpleNamespace(number=12, state="open", base="main"),
            SimpleNamespace(number=13, state="open", base="main"),
        ], "PR ambiguë"),
    ],
)
def test_openpr_refuses_wrong_base_closed_or_ambiguous_candidates(
    monkeypatch, candidates, message,
):
    events = []
    ch = SimpleNamespace(
        list_prs=lambda *_args: candidates,
        get_pr=lambda *_args: events.append("read"),
        update_pr=lambda *_args: events.append("update"),
        open_pr=lambda *_args: events.append("open"),
    )

    with pytest.raises(SystemExit, match=message):
        issue._reuse_or_open_pr(ch, "acme/demo", "feat/demo-7", "main", "title", "body")

    assert events == []


def test_openpr_refuses_reused_pr_that_changes_coordinates_during_update():
    candidate = SimpleNamespace(
        number=12, state="open", head="feat/demo-7", base="main",
    )
    moved = SimpleNamespace(
        number=12, state="closed", head="feat/demo-7", base="main",
    )
    ch = SimpleNamespace(
        list_prs=lambda *_args: [candidate],
        update_pr=lambda *_args: moved,
        open_pr=lambda *_args: (_ for _ in ()).throw(AssertionError("open")),
    )

    with pytest.raises(SystemExit, match="modifiée pendant la reprise"):
        issue._reuse_or_open_pr(ch, "acme/demo", "feat/demo-7", "main", "title", "body")


def test_openpr_reuse_is_retryable_after_tracker_interruption(monkeypatch):
    transitions = 0
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(title="Pilot", type="Feature"),
    )
    pull_request = SimpleNamespace(
        number=12, url="https://github.com/acme/demo/pull/12", sha="a" * 40,
        base_sha="c" * 40, head="feat/demo-7-pilot", base="main",
        state="open", merged=False,
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo",
        list_prs=lambda *_args: [pull_request],
        get_pr=lambda *_args: events.append("read") or pull_request,
        open_pr=lambda *_args: events.append("open"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_sh", lambda *args, **_kwargs: (
        "feat/demo-7-pilot" if args[-2:] == ("--abbrev-ref", "HEAD") else ""
    ))
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: b"same-diff")
    monkeypatch.setattr(write, "set_field", lambda *_args: events.append("field"))

    def transition(*_args, **_kwargs):
        nonlocal transitions
        transitions += 1
        if transitions == 1:
            raise RuntimeError("tracker unavailable")
        events.append("review")

    monkeypatch.setattr(write, "transition", transition)

    with pytest.raises(RuntimeError, match="tracker unavailable"):
        issue.openpr("DEMO-7")
    issue.openpr("DEMO-7")

    assert events.count("read") == 2
    assert "update" not in events
    assert "open" not in events
    assert events[-1] == "review"


@pytest.mark.parametrize("base_sha", [None, "origin/main", "C" * 40, "c" * 39])
def test_proof_bound_merge_rejects_invalid_api_base_before_gates_or_effects(
    monkeypatch, base_sha,
):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(
            id="DEMO-7", state="review", body="", ac_done=0, ac_total=0,
        ),
    )
    pull_request = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="main", base_sha=base_sha,
        merged=False, url="https://github.com/acme/demo/pull/12",
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo", get_pr=lambda *_args: pull_request,
        merge_pr=lambda *_args, **_kwargs: events.append("merge"),
        delete_branch=lambda *_args: events.append("delete"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(
        issue, "git_diff", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("diff")),
    )
    monkeypatch.setattr(
        write, "ci_gate", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("CI")),
    )
    monkeypatch.setattr(
        write, "transition", lambda *_args, **_kwargs: events.append("transition"),
    )

    with pytest.raises(SystemExit, match="SHA de base GitHub"):
        issue.merge("DEMO-7", "12")

    assert events == []


def test_proof_bound_merge_refuses_a_base_that_moves_after_validation(monkeypatch):
    events = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: SimpleNamespace(
            id="DEMO-7", state="review", body="", ac_done=0, ac_total=0,
        ),
    )
    original = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="main", base_sha="c" * 40,
        merged=False, url="https://github.com/acme/demo/pull/12",
    )
    stale = SimpleNamespace(**{**vars(original), "base_sha": "d" * 40})
    reads = iter((original, stale))
    codehost = SimpleNamespace(
        resolve_repo=lambda: "acme/demo", get_pr=lambda *_args: next(reads),
        merge_pr=lambda *_args, **_kwargs: events.append("merge"),
        delete_branch=lambda *_args: events.append("delete"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: b"exact-diff")
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": True, "waived": False, "total": 1, "pending": [], "failing": [],
    })
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, _issue_id, state, context=None: events.append((state, context)),
    )

    with pytest.raises(SystemExit, match="coordonnées GitHub.*changé"):
        issue.merge("DEMO-7", "12")

    assert events == []


def test_proof_bound_coordinate_reread_is_last_network_call_before_merge(monkeypatch):
    network = []
    tracker = SimpleNamespace(
        bounded_transition_proofs=True,
        get_issue=lambda _id: network.append("tracker:get-issue") or SimpleNamespace(
            id="DEMO-7", state="review", body="- [ ] pilot", ac_done=0, ac_total=1,
        ),
    )
    pr = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="main", base_sha="c" * 40,
        merged=False, url="https://github.com/acme/demo/pull/12",
    )
    landed = SimpleNamespace(sha="b" * 40, head=pr.head, merged=True)
    pr_reads = 0

    def get_pr(*_args):
        nonlocal pr_reads
        pr_reads += 1
        network.append(f"codehost:get-pr:{pr_reads}")
        return pr

    codehost = SimpleNamespace(
        resolve_repo=lambda: network.append("codehost:resolve-repo") or "acme/demo",
        get_pr=get_pr,
        merge_pr=lambda *_args, **_kwargs: network.append("codehost:merge") or landed,
        delete_branch=lambda *_args: network.append("codehost:delete-branch"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(
        write, "issue_binding",
        lambda *_args: network.append("tracker:binding"),
    )
    monkeypatch.setattr(issue, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: b"exact-diff")
    monkeypatch.setattr(issue, "repository_identity", lambda: "acme/demo")
    monkeypatch.setattr(
        write, "add_comment",
        lambda *_args, **_kwargs: network.append("tracker:audit"),
    )
    monkeypatch.setattr(
        write, "sync_acceptance",
        lambda *_args, **_kwargs: network.append("tracker:sync") or {
            "status": "updated", "checked": 1,
        },
    )
    monkeypatch.setattr(
        write, "ci_gate",
        lambda *_args, **_kwargs: network.append("codehost:ci") or {
            "passed": True, "waived": False, "total": 1,
            "pending": [], "failing": [],
        },
    )
    monkeypatch.setattr(
        write, "transition",
        lambda _tracker, _issue_id, state, context=None: network.append(
            f"tracker:transition:{state}"
        ),
    )
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "linked-worktree")

    class Store:
        def __init__(self, _repository):
            pass

        def valid_for_merge(self, **_kwargs):
            return {"proof_id": "d" * 64}

    monkeypatch.setattr(issue, "AcceptanceProofStore", Store)

    issue.merge("DEMO-7", "12")

    assert network == [
        "tracker:binding",
        "codehost:resolve-repo",
        "codehost:get-pr:1",
        "tracker:get-issue",
        "tracker:sync",
        "tracker:audit",
        "codehost:ci",
        "codehost:get-pr:2",
        "tracker:transition:in-progress",
        "tracker:transition:review",
        "codehost:get-pr:3",
        "codehost:merge",
        "tracker:transition:done",
        "codehost:delete-branch",
    ]


def test_openpr_binding_guard_runs_before_push_or_pr_creation(monkeypatch):
    events = []

    def refuse(_project, *issue_ids):
        events.append(("binding", issue_ids))
        raise SystemExit("foreign issue")

    tracker = SimpleNamespace(
        requires_mutation_binding=True,
        resolve_project=lambda repo: events.append(("resolve", repo))
        or Project(key="DEMO", id="1"),
        validate_issue_binding=refuse,
        get_issue=lambda _id: events.append(("get-issue",)),
    )
    codehost = SimpleNamespace(
        resolve_repo=lambda: events.append(("resolve-repo",)),
        open_pr=lambda *_args: events.append(("open-pr",)),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "demo")
    monkeypatch.setattr(issue, "_sh", lambda *args, **_kwargs: (
        "feat/demo-7-pilot" if args[-2:] == ("--abbrev-ref", "HEAD")
        else events.append(("push",))
    ))

    with pytest.raises(SystemExit, match="foreign issue"):
        issue.openpr("OTHER-7")

    assert events == [("resolve", "demo"), ("binding", ("OTHER-7",))]


def _merge_proof(body, proof_id="d" * 64):
    criteria = acceptance_criteria(body)
    return {
        "proof_id": proof_id,
        "issue": {
            "id": "DEMO-7",
            "ac_digest": acceptance_digest(criteria),
            "criteria": [{**item, "verdict": "pass"} for item in criteria],
        },
    }


def _incomplete_merge_harness(monkeypatch, *, proof=None, proof_error=None,
                              comment_error=None, sync_error=None,
                              sync_supported=True,
                              body="- [ ] current contract\n"):
    """Install deterministic local fakes; no network or tracker writes are possible."""
    events = []
    current = SimpleNamespace(id="DEMO-7", body=body, ac_done=0, ac_total=1)

    def add_comment(issue_id, text):
        events.append(("comment", issue_id, text))
        if comment_error:
            raise comment_error

    def sync_acceptance_body(
        issue_id, expected_body, updated_body, proof_id, **_kwargs,
    ):
        events.append(("sync", issue_id, expected_body, updated_body, proof_id))
        if sync_error:
            raise sync_error
        return True

    tracker = SimpleNamespace(
        name="test",
        acceptance_sync_supported=sync_supported,
        get_issue=lambda _id: current,
        add_comment=add_comment,
        sync_acceptance_body=sync_acceptance_body,
    )
    head = SimpleNamespace(
        sha="a" * 40, head="feat/demo-7", base="main", base_sha="c" * 40,
        merged=False, url="https://github.com/acme/demo/pull/12",
    )
    landed = SimpleNamespace(sha="b" * 40, head=head.head, merged=True)

    def merge_pr(*_args, **_kwargs):
        events.append(("merge",))
        return landed

    codehost = SimpleNamespace(resolve_repo=lambda: "acme/demo", get_pr=lambda *_: head,
                               merge_pr=merge_pr, delete_branch=lambda *_: events.append(("delete",)))
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "git_head", lambda: head.sha)
    monkeypatch.setattr(issue, "git_diff", lambda *args, **kwargs: b"exact-diff")
    monkeypatch.setattr(issue, "repository_identity", lambda: "acme/demo")
    monkeypatch.setattr(issue, "_cleanup_branch", lambda _branch: "main-checkout")
    monkeypatch.setattr(write, "ci_gate", lambda *_args, **_kwargs: {
        "passed": True, "waived": False, "total": 1, "pending": 0, "failing": 0})
    monkeypatch.setattr(write, "transition", lambda *_args: events.append(("done",)))

    class Store:
        def __init__(self, _repository):
            pass

        def valid_for_merge(self, **kwargs):
            events.append(("proof", kwargs))
            if proof_error:
                raise proof_error
            return proof

    monkeypatch.setattr(issue, "AcceptanceProofStore", Store)
    return events, current


def test_incomplete_ac_current_proof_is_audited_before_merge(monkeypatch):
    proof = _merge_proof("- [ ] current contract\n")
    events, _current = _incomplete_merge_harness(monkeypatch, proof=proof)

    issue.merge("DEMO-7", "12")

    assert [event[0] for event in events] == [
        "proof", "sync", "comment", "merge", "delete", "done",
    ]
    assert events[1][2:] == (
        "- [ ] current contract\n", "- [x] current contract\n", proof,
    )
    assert proof["proof_id"] in events[2][2]
    assert events[0][1] == {
        "issue_id": "DEMO-7", "issue_body": "- [ ] current contract\n",
        "head": "a" * 40, "diff": b"exact-diff", "base": "c" * 40,
    }


@pytest.mark.parametrize("marker", ["-", "*", "+"])
def test_youtrack_merge_checks_proven_criteria_before_codehost_merge(
    monkeypatch, tmp_path, marker,
):
    body = f"  {marker} [ ] current contract\r\n"
    events, current = _incomplete_merge_harness(
        monkeypatch, proof=_merge_proof(body), body=body,
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = object.__new__(YouTrackTracker)
    tracker.requires_mutation_binding = False
    current = tracker._to_issue({
        "idReadable": "DEMO-7", "summary": "Current contract", "description": body,
    })
    assert (current.ac_done, current.ac_total) == (0, 1)
    monkeypatch.setattr(tracker, "get_issue", lambda _id: current)

    def request(method, path, payload=None, fields=None):
        assert method == "POST"
        if path == "/issues/DEMO-7":
            current.body = payload["description"]
            events.append(("body-write", current.body))
            return {"idReadable": "DEMO-7"}
        assert path == "/issues/DEMO-7/comments"
        events.append(("comment", payload["text"]))
        return {"id": "comment-1"}

    monkeypatch.setattr(tracker, "_req", request)
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    issue.merge("DEMO-7", "12")

    assert current.body == f"  {marker} [x] current contract\r\n"
    assert [event[0] for event in events] == [
        "proof", "body-write", "comment", "merge", "delete", "done",
    ]


def test_youtrack_merge_multiline_pseudo_checkbox_cannot_hide_unchecked_ac(
    monkeypatch, tmp_path,
):
    body = "* [x]\n- [ ] current contract\n"
    events, _current = _incomplete_merge_harness(
        monkeypatch, proof=_merge_proof(body), body=body,
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path))
    tracker = object.__new__(YouTrackTracker)
    tracker.requires_mutation_binding = False
    current = tracker._to_issue({
        "idReadable": "DEMO-7", "summary": "Current contract", "description": body,
    })
    assert (current.ac_done, current.ac_total) == (0, 1)
    monkeypatch.setattr(tracker, "get_issue", lambda _id: current)

    def request(method, path, payload=None, fields=None):
        assert method == "POST"
        if path == "/issues/DEMO-7":
            current.body = payload["description"]
            events.append(("body-write", current.body))
            return {"idReadable": "DEMO-7"}
        assert path == "/issues/DEMO-7/comments"
        events.append(("comment", payload["text"]))
        return {"id": "comment-1"}

    monkeypatch.setattr(tracker, "_req", request)
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    issue.merge("DEMO-7", "12")

    assert current.body == "* [x]\n- [x] current contract\n"
    assert [event[0] for event in events] == [
        "proof", "body-write", "comment", "merge", "delete", "done",
    ]


@pytest.mark.parametrize("proof_error", [
    RoutingConfigError("HEAD périmé"), RoutingConfigError("diff périmé"),
    RoutingConfigError("AC modifiée"), RoutingConfigError("preuve non mergeable"),
])
def test_incomplete_ac_stale_or_non_mergeable_proof_blocks_before_codehost(
    monkeypatch, proof_error,
):
    events, _current = _incomplete_merge_harness(monkeypatch, proof_error=proof_error)

    with pytest.raises(SystemExit, match="preuve structurée invalide"):
        issue.merge("DEMO-7", "12")

    assert [event[0] for event in events] == ["proof"]


def test_human_facing_audit_failure_cannot_orphan_provider_receipt(monkeypatch, capsys):
    events, _current = _incomplete_merge_harness(
        monkeypatch, proof=_merge_proof("- [ ] current contract\n", "e" * 64),
        comment_error=RuntimeError("unavailable"),
    )

    issue.merge("DEMO-7", "12")

    assert [event[0] for event in events] == [
        "proof", "sync", "comment", "merge", "delete", "done",
    ]
    assert "AC sync: updated/1/note-audit-indisponible" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("sync_supported", "sync_error", "message"),
    [
        (False, None, "ne sait pas synchroniser les AC atomiquement"),
        (True, TrackerConflictError("changed"), "corps ou la version"),
    ],
)
def test_acceptance_sync_unavailable_or_conflicting_blocks_before_merge(
    monkeypatch, sync_supported, sync_error, message,
):
    events, _current = _incomplete_merge_harness(
        monkeypatch,
        proof=_merge_proof("- [ ] current contract\n"),
        sync_supported=sync_supported,
        sync_error=sync_error,
    )

    with pytest.raises(SystemExit, match=message):
        issue.merge("DEMO-7", "12")

    expected = ["proof"] if not sync_supported else ["proof", "sync"]
    assert [event[0] for event in events] == expected


def test_human_ac_override_requires_public_reason_and_preserves_tracker_ac(monkeypatch):
    events, current = _incomplete_merge_harness(
        monkeypatch, proof_error=RoutingConfigError("no proof"),
    )
    original = (current.body, current.ac_done, current.ac_total)

    with pytest.raises(SystemExit, match="reason=<code-public>"):
        issue.merge("DEMO-7", "12", flags={"--allow-incomplete-ac"})
    assert [event[0] for event in events] == ["proof"]

    issue.merge("DEMO-7", "12", flags={"--allow-incomplete-ac", "--ac-override-reason=human_confirmed"})

    assert [event[0] for event in events] == ["proof", "proof", "comment", "merge", "delete", "done"]
    assert "human_confirmed" in events[2][2]
    assert (current.body, current.ac_done, current.ac_total) == original
