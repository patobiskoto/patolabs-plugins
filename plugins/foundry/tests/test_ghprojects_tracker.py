"""Transport-bound regression tests for GitHub Projects tracker slices."""
import hashlib
import json
import subprocess
import threading
from copy import deepcopy

import pytest

import foundry
from foundry import frame, issue, query, write
from foundry.models import Adr, Issue, Link, Project, PullRequest, TransitionContext
from foundry.routing import acceptance_criteria, acceptance_digest
from foundry.registry import RepositoryTrackerBinding
from foundry.trackers.base import (
    AdrIssueUnavailableError,
    IssueUnavailableError,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)
from foundry.trackers.ghprojects import (
    GitHubProjectsPartialCreateConflict,
    GitHubProjectsPartialCreateError,
    GitHubProjectsTracker,
    GitHubProjectsTrackerError,
    _CreateCandidate,
    _Binding,
)


PROJECT = Project(
    key="GHQUAL", id="PVT_project",
    extra={"owner": "patobiskoto", "number": "7",
           "canonical_repo": "github.com/patobiskoto/foundry-v1-ghprojects-sandbox"},
)
LIFECYCLE_SCOPE = {
    "repository": "patobiskoto/foundry-v1-ghprojects-sandbox",
    "project_id": "PVT_project",
    "project_number": 7,
    "project_key": "GHQUAL",
    "issue_id": "GHQUAL-1",
    "issue_number": 1,
    "native_issue_id": 1001,
    "issue_node_id": "issue-node-1",
    "project_item_id": "item-1",
}


def _canonical_acceptance_proof(
    body: str,
    *,
    issue_id: str = "GHQUAL-1",
    generation: int = 1,
    head: str = "a" * 40,
    base: str = "b" * 40,
    diff_hash: str = "c" * 64,
    quality: str = "mergeable",
) -> dict:
    criteria = acceptance_criteria(body)
    proof = {
        "schema_version": 1,
        "issue": {
            "id": issue_id,
            "ac_digest": acceptance_digest(criteria),
            "criteria": [
                {**criterion, "verdict": "pass"} for criterion in criteria
            ],
        },
        "review": {
            "role": "reviewer",
            "generation": generation,
            "claim_digest": "d" * 64,
        },
        "coordinates": {
            "head": head,
            "base": base,
            "diff_hash": diff_hash,
        },
        "quality": quality,
    }
    proof["proof_id"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return proof


@pytest.fixture(autouse=True)
def _qualified_active_binding(monkeypatch):
    """Exercise the public authority gate against a complete V1 binding."""
    binding = RepositoryTrackerBinding(
        tracker="ghprojects",
        repository=PROJECT.extra["canonical_repo"],
        project=PROJECT,
        registry_binding_digest="sha256:" + "1" * 64,
        migration_manifest_digest=None,
        configuration_digest="sha256:" + "2" * 64,
        activation_kind="bootstrap",
    )
    monkeypatch.setattr(
        "foundry.trackers.ghprojects.registry.repository_tracker_selection",
        lambda cwd=None: {"tracker": "ghprojects", "mode": "v1", "binding": binding},
    )


def _page(item_id, number, cursor, more, *, adr=False):
    labels = [{"name": "foundry:adr"}] if adr else []
    return {"data": {"user": {"projectV2": {
        "id": "PVT_project", "number": 7, "public": False,
        "fields": {"nodes": [
            {"id": "state", "name": "Foundry normalized state", "dataType": "SINGLE_SELECT",
             "options": [{"id": f"state-{name}", "name": name} for name in (
                 "backlog", "ready", "in-progress", "review", "blocked", "done", "dropped",
             )]},
            {"id": "type", "name": "Foundry type", "dataType": "SINGLE_SELECT",
             "options": [{"id": f"type-{name.casefold()}", "name": name}
                         for name in ("Bug", "Feature", "Task", "Epic")]},
            {"id": "priority", "name": "Foundry priority", "dataType": "SINGLE_SELECT",
             "options": [{"id": f"priority-{name.casefold()}", "name": name}
                         for name in ("P1", "P2")]},
            {"id": "estimate", "name": "Foundry estimate", "dataType": "NUMBER"},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        "items": {"nodes": [{"id": item_id, "type": "ISSUE", "content": {
            "__typename": "Issue", "id": f"issue-node-{number}", "number": number,
            "title": f"issue {number}", "body": "UTF-8 é", "repository": {
                "id": "repo-node", "nameWithOwner": "patobiskoto/foundry-v1-ghprojects-sandbox",
                "isPrivate": True, "owner": {"__typename": "User", "login": "patobiskoto"},
            },
            "labels": {"nodes": labels, "pageInfo": {"hasNextPage": False, "endCursor": None}},
            "relatesTo": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }, "fieldValues": {"nodes": [
            {"__typename": "ProjectV2ItemFieldSingleSelectValue",
             "field": {"id": "state", "name": "Foundry normalized state"},
             "optionId": "state-ready", "name": "ready"},
            {"__typename": "ProjectV2ItemFieldSingleSelectValue",
             "field": {"id": "type", "name": "Foundry type"},
             "optionId": "type-task", "name": "Task"},
            {"__typename": "ProjectV2ItemFieldSingleSelectValue",
             "field": {"id": "priority", "name": "Foundry priority"},
             "optionId": "priority-p1", "name": "P1"},
            {"__typename": "ProjectV2ItemFieldNumberValue",
             "field": {"id": "estimate", "name": "Foundry estimate"}, "number": 3.0},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}}], "pageInfo": {"hasNextPage": more, "endCursor": cursor}},
    }}}}


def _rest_issue(number):
    repo = "patobiskoto/foundry-v1-ghprojects-sandbox"
    api = f"https://api.github.com/repos/{repo}"
    return {
        "id": 1000 + number, "number": number, "body": "UTF-8 é", "labels": [],
        "repository_url": api, "url": f"{api}/issues/{number}",
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "created_at": "2026-09-27T12:00:00Z", "updated_at": "2026-09-27T12:01:00Z",
    }


class _LifecycleTransport:
    """Stateful raw gh transport for the public PAT-67 vertical path."""

    def __init__(self, *, comment_loss: str | None = None):
        self.state = "ready"
        self.body = "## Critères d’acceptation\n\n- [ ] Exact criterion\n"
        self.comments = []
        self.comment_posts = 0
        self.state_writes = 0
        self.comment_loss = comment_loss

    def _project(self):
        page = _page("item-1", 1, None, False)
        item = page["data"]["user"]["projectV2"]["items"]["nodes"][0]
        item["content"]["body"] = self.body
        state = next(
            value for value in item["fieldValues"]["nodes"]
            if value["field"]["id"] == "state"
        )
        state.update(name=self.state, optionId=f"state-{self.state}")
        return page

    @staticmethod
    def _response(command, payload, *, returncode=0, stderr=""):
        return subprocess.CompletedProcess(
            command, returncode, json.dumps(payload) if payload is not None else "", stderr,
        )

    def __call__(self, command, **_kwargs):
        if command[2] == "graphql":
            query = next(value for value in command if value.startswith("query="))
            if query.startswith("query=mutation"):
                self.state_writes += 1
                for candidate in (
                    "in-progress", "review", "done", "ready", "blocked",
                    "backlog", "dropped",
                ):
                    if f'singleSelectOptionId:"state-{candidate}"' in query:
                        self.state = candidate
                        break
                return self._response(command, {"data": {
                    "updateProjectV2ItemFieldValue": {
                        "projectV2Item": {"id": "item-1"},
                    },
                }})
            return self._response(command, self._project())

        method, path = command[3], command[4]
        if method == "POST" and path.endswith("/comments"):
            self.comment_posts += 1
            body = next(
                value.removeprefix("body=") for value in command
                if value.startswith("body=")
            )
            row = {
                "id": 2000 + self.comment_posts,
                "body": body,
                "created_at": f"2026-09-27T12:{self.comment_posts:02d}:00Z",
            }
            if self.comment_loss == "applied":
                self.comments.append(row)
                return self._response(command, None, returncode=1,
                                      stderr="synthetic lost response")
            if self.comment_loss == "hidden":
                return self._response(command, None, returncode=1,
                                      stderr="synthetic lost response")
            self.comments.append(row)
            return self._response(command, row)

        path = command[-1]
        if path.endswith("/parent"):
            return self._response(
                command,
                {"message": "No parent issue found", "status": "404"},
                returncode=1,
                stderr="gh: No parent issue found (HTTP 404)",
            )
        if "/comments?" in path:
            page = int(path.rsplit("page=", 1)[1])
            start = (page - 1) * 100
            return self._response(
                command, deepcopy(self.comments[start:start + 100]),
            )
        if "/dependencies/" in path or "/sub_issues?" in path:
            return self._response(command, [])
        if path.endswith("/issues/1"):
            raw = _rest_issue(1)
            raw["body"] = self.body
            raw["node_id"] = "issue-node-1"
            return self._response(command, raw)
        raise AssertionError(f"unexpected raw transport call: {command}")


class _StateWriteFailsOnce(_LifecycleTransport):
    """Lose one targeted State mutation after its receipt has been persisted."""

    def __init__(self):
        super().__init__()
        self.fail_state = None

    def __call__(self, command, **kwargs):
        if (
            self.fail_state
            and command[2] == "graphql"
            and any(
                value.startswith("query=mutation")
                and f'singleSelectOptionId:"state-{self.fail_state}"' in value
                for value in command
            )
        ):
            self.fail_state = None
            return self._response(
                command, {"errors": [{"message": "synthetic State failure"}]},
                returncode=1, stderr="synthetic State failure",
            )
        return super().__call__(command, **kwargs)


def test_search_pages_graphql_and_excludes_only_reserved_adr_support():
    replies = [_page("item-1", 1, "next", True), _page("item-adr", 3, None, False, adr=True)]

    def runner(command, **kwargs):
        path = command[-1]
        if "/comments" in path or "/dependencies/" in path or "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        if "/issues/1" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    issues = GitHubProjectsTracker(runner=runner).search(PROJECT)
    assert [(issue.id, issue.title, issue.state, issue.estimate) for issue in issues] == [
        ("GHQUAL-1", "issue 1", "ready", 3.0)
    ]


def test_get_issue_uses_bound_graphql_coordinates_and_expected_rest_paths():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(_page("item-1", 1, None, False)), "")
        path = command[-1]
        if path == "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1":
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    issue = GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")

    assert issue.id == "GHQUAL-1"
    graphql = calls[0]
    assert "login=patobiskoto" in graphql
    assert "number=7" in graphql
    assert "optionId" in graphql[4]
    assert "repository{id nameWithOwner isPrivate" in graphql[4]
    rest_paths = [command[-1] for command in calls[1:]]
    assert rest_paths == [
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/comments?per_page=100&page=1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/parent",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/dependencies/blocked_by?per_page=100&page=1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/dependencies/blocking?per_page=100&page=1",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/sub_issues?per_page=100&page=1",
    ]


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("create_issue", ("title", "body")),
        ("update_fields", ("GHQUAL-1", {"Priority": "P1"})),
        ("update_body", (Issue(id="GHQUAL-1", title="x"), "old", "new")),
        ("link", ("GHQUAL-1", "depends-on", "GHQUAL-2")),
        ("add_comment", ("GHQUAL-1", "note")),
    ],
)
def test_pat66_foreign_project_refuses_before_any_transport(operation, args):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        raise AssertionError("foreign binding must fail before transport")

    foreign = Project(
        key="GHQUAL", id="PVT_project",
        extra={"owner": "patobiskoto", "number": "7",
               "canonical_repo": "github.com/patobiskoto/other-repository"},
    )
    method = getattr(GitHubProjectsTracker(runner=runner), operation)
    with pytest.raises(GitHubProjectsTrackerError, match="foreign_project_binding"):
        if operation == "create_issue":
            method(foreign, *args)
        else:
            method(*args, project=foreign)
    assert calls == []


def test_pat67_lifecycle_receipt_precedes_targeted_state_and_exact_replay(
    monkeypatch, tmp_path,
):
    """The common start/review path gets one owned receipt, never a free note."""
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    state = {"value": "ready", "comments": []}
    binding = _Binding("patobiskoto", 7, "PVT_project", "patobiskoto/foundry-v1-ghprojects-sandbox", "GHQUAL")

    def read(_issue_id):
        return Issue(id="GHQUAL-1", title="issue", state=state["value"], body="body",
                     comments=deepcopy(state["comments"]))

    def post(_method, _path, payload, _operation):
        state["comments"].append({"text": payload["body"], "created": 0})
        return {"id": 99, "body": payload["body"]}

    monkeypatch.setattr(tracker, "_authoritative_binding", lambda _project: binding)
    monkeypatch.setattr(tracker, "_native_issue_read", read)
    monkeypatch.setattr(tracker, "_native_issue", lambda *_args: (1, 1001))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_args: ("item-1", "issue-node-1"))
    monkeypatch.setattr(tracker, "_rest_write", post)
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_args: ("item-1", "node-1"))
    monkeypatch.setattr(tracker, "_write_catalog", lambda *_args: {"state": object()})
    monkeypatch.setattr(tracker, "_set_project_field", lambda *_args: state.update(value="in-progress"))

    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)

    assert state["value"] == "in-progress"
    assert len(state["comments"]) == 1
    assert "foundry-ghprojects-lifecycle.v1:state-in-progress:" in state["comments"][0]["text"]


def test_pat67_target_state_drift_before_receipt_fails_closed(tmp_path):
    class TargetDriftTransport(_LifecycleTransport):
        def __init__(self):
            super().__init__()
            self.project_reads = 0

        def __call__(self, command, **kwargs):
            if (
                command[2] == "graphql"
                and not any(value.startswith("query=mutation") for value in command)
            ):
                self.project_reads += 1
                if self.project_reads == 4:
                    self.state = "in-progress"
            return super().__call__(command, **kwargs)

    transport = TargetDriftTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)

    with pytest.raises(TrackerConflictError, match="before lifecycle append"):
        tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)

    assert transport.comment_posts == 0
    assert transport.state_writes == 0


def test_pat67_native_done_without_owned_receipt_fails_closed(monkeypatch):
    tracker = GitHubProjectsTracker()
    monkeypatch.setattr(
        tracker, "_native_issue_read",
        lambda _issue_id: Issue(id="GHQUAL-1", title="issue", state="done", body="body"),
    )

    with pytest.raises(TrackerConflictError, match="outside lifecycle"):
        tracker.get_issue("GHQUAL-1")


def test_pat67_new_review_generation_and_old_start_replay_never_regress(
    monkeypatch, tmp_path,
):
    """A new HEAD advances review evidence; an old start receipt stays historical."""
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    binding = _Binding("patobiskoto", 7, "PVT_project", "patobiskoto/foundry-v1-ghprojects-sandbox", "GHQUAL")
    original = TransitionContext(
        pr_url="https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox/pull/1",
        head_sha="a" * 40, base_sha="b" * 40, review_digest="c" * 64,
        expected_state="review",
    )
    review = tracker._state_payload("review", original)
    review.update(
        generation=1,
        previous_projection_digest=None,
        source_state="in-progress",
        source_digest="f" * 64,
    )
    state = {"comments": [
        {"text": tracker._lifecycle_marker("state-in-progress", "GHQUAL-1", {
            "state": "in-progress", "source_state": "ready",
            "source_digest": "e" * 64,
        }, LIFECYCLE_SCOPE)[1]},
        {"text": tracker._lifecycle_marker(
            "state-review", "GHQUAL-1", review, LIFECYCLE_SCOPE,
        )[1]},
    ]}

    def read(_issue):
        return Issue(id="GHQUAL-1", title="issue", state="review", body="body", comments=deepcopy(state["comments"]))

    def post(_method, _path, payload, _operation):
        state["comments"].append({"text": payload["body"]})
        return {"id": 1, "body": payload["body"]}

    monkeypatch.setattr(tracker, "_authoritative_binding", lambda _project: binding)
    monkeypatch.setattr(tracker, "_native_issue_read", read)
    monkeypatch.setattr(tracker, "_native_issue", lambda *_args: (1, 1001))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_args: ("item-1", "issue-node-1"))
    monkeypatch.setattr(tracker, "_rest_write", post)
    fresh = TransitionContext(
        pr_url=original.pr_url, head_sha="d" * 40, base_sha=original.base_sha,
        review_digest="e" * 64, expected_state="review",
    )
    tracker.set_state("GHQUAL-1", "review", fresh, PROJECT)
    tracker.set_state("GHQUAL-1", "in-progress", TransitionContext(expected_state="ready"), PROJECT)

    assert len(state["comments"]) == 3
    latest = tracker._decode_lifecycle_comment("GHQUAL-1", state["comments"][-1]["text"])
    assert latest is not None and latest[1]["generation"] == 2


def test_pat67_projection_rejects_foreign_pr_and_corrupt_acceptance(monkeypatch):
    tracker = GitHubProjectsTracker()
    binding = _Binding("patobiskoto", 7, "PVT_project", "patobiskoto/foundry-v1-ghprojects-sandbox", "GHQUAL")
    bad_review = {
        "state": "review", "generation": 1, "previous_projection_digest": None,
        "pr_url": "https://github.com/patobiskoto/other/pull/1", "head_sha": "a" * 40,
        "base_sha": "b" * 40, "review_digest": "c" * 64,
    }
    bad_review.update(source_state="in-progress", source_digest="d" * 64)
    comments = [{"text": tracker._lifecycle_marker(
        "state-review", "GHQUAL-1", bad_review, LIFECYCLE_SCOPE,
    )[1]}]
    monkeypatch.setattr(tracker, "_native_issue_read", lambda _issue: Issue(
        id="GHQUAL-1", title="issue", state="review", body="- [ ] AC", comments=deepcopy(comments),
    ))
    monkeypatch.setattr(tracker, "_binding", lambda _project: binding)
    monkeypatch.setattr(tracker, "_native_issue", lambda *_args: (1, 1001))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_args: ("item-1", "issue-node-1"))

    with pytest.raises(TrackerConflictError, match="state proof malformed"):
        tracker.get_issue("GHQUAL-1")


def _review_context(*, generation=1, expected_state="in-progress"):
    offset = chr(ord("a") + generation - 1)
    return TransitionContext(
        pr_url="https://github.com/patobiskoto/foundry-v1-ghprojects-sandbox/pull/1",
        head_sha=offset * 40,
        base_sha="b" * 40,
        review_digest=offset * 64,
        expected_state=expected_state,
    )


def _done_context(review: TransitionContext):
    return TransitionContext(
        pr_url=review.pr_url,
        head_sha=review.head_sha,
        base_sha=review.base_sha,
        review_digest=review.review_digest,
        merge_sha="f" * 40,
        expected_state="review",
    )


def test_pat67_raw_transport_full_lifecycle_and_replays(tmp_path, monkeypatch):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    monkeypatch.setattr(
        "foundry.registry.checkout_repository_identity",
        lambda cwd=None: PROJECT.extra["canonical_repo"],
    )

    write.transition(
        tracker, "GHQUAL-1", "in-progress",
        context=TransitionContext(expected_state="ready"),
    )
    write.transition(tracker, "GHQUAL-1", "review", context=review)
    proof = _canonical_acceptance_proof(
        transport.body,
        head=review.head_sha,
        base=review.base_sha,
        diff_hash=review.review_digest,
    )
    assert write.sync_acceptance(
        tracker, "GHQUAL-1", transport.body, proof,
    )["status"] == "proof-projected"
    write.transition(
        tracker, "GHQUAL-1", "done", context=_done_context(review),
    )

    observed = tracker.get_issue("GHQUAL-1")
    assert (observed.state, observed.ac_done, observed.ac_total) == ("done", 1, 1)
    assert observed.projection_status == "aligned"
    listed = tracker.search(PROJECT)[0]
    assert (listed.state, listed.ac_done, listed.ac_total) == ("done", 1, 1)
    assert listed.projection_status == "aligned"
    assert transport.comment_posts == 4
    assert transport.state_writes == 3

    write.transition(
        tracker, "GHQUAL-1", "in-progress",
        context=TransitionContext(expected_state="ready"),
    )
    write.transition(tracker, "GHQUAL-1", "review", context=review)
    assert write.sync_acceptance(
        tracker, "GHQUAL-1", transport.body, proof,
    )["status"] == "proof-already-projected"
    write.transition(
        tracker, "GHQUAL-1", "done", context=_done_context(review),
    )
    assert transport.comment_posts == 4
    assert transport.state_writes == 3


def test_pat67_exact_review_receipt_repairs_only_missing_state(tmp_path):
    transport = _StateWriteFailsOnce()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    review = _review_context()
    transport.fail_state = "review"

    with pytest.raises(GitHubProjectsTrackerError):
        tracker.set_state("GHQUAL-1", "review", review, PROJECT)

    assert transport.state == "in-progress"
    assert tracker.observe_issue("GHQUAL-1").projection_status == "disagreement"
    with pytest.raises(TrackerConflictError):
        tracker.get_issue("GHQUAL-1")
    receipt_count = transport.comment_posts
    GitHubProjectsTracker(runner=transport, state_dir=tmp_path).set_state(
        "GHQUAL-1", "review", review, PROJECT,
    )
    assert tracker.get_issue("GHQUAL-1").state == "review"
    assert transport.comment_posts == receipt_count
    assert transport.state_writes == 2


def test_pat67_public_start_replays_only_durable_partial_receipt(tmp_path, monkeypatch):
    transport = _StateWriteFailsOnce()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    transport.fail_state = "in-progress"
    with pytest.raises(GitHubProjectsTrackerError):
        tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    receipt_count = transport.comment_posts
    branches = []
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue, "_sh", lambda *_args: "")
    monkeypatch.setattr(
        issue, "_prepare_branch", lambda branch: branches.append(branch) or "existing",
    )

    issue.start("GHQUAL-1")

    assert tracker.get_issue("GHQUAL-1").state == "in-progress"
    assert transport.comment_posts == receipt_count
    assert transport.state_writes == 1
    assert len(branches) == 1


def test_pat67_public_start_refuses_native_only_in_progress(tmp_path, monkeypatch):
    transport = _LifecycleTransport()
    transport.state = "in-progress"
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    branches = []
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue, "_sh", lambda *_args: "")
    monkeypatch.setattr(
        issue, "_prepare_branch", lambda branch: branches.append(branch),
    )

    with pytest.raises(SystemExit, match="sans reçu Foundry"):
        issue.start("GHQUAL-1")

    assert branches == []
    assert transport.comment_posts == 0
    assert transport.state_writes == 0


def test_pat67_public_start_refuses_native_drift_after_receipt_before_branch(
    tmp_path, monkeypatch,
):
    transport = _StateWriteFailsOnce()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    transport.fail_state = "in-progress"
    with pytest.raises(GitHubProjectsTrackerError):
        tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    transport.state = "review"
    branches = []
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue, "_sh", lambda *_args: "")
    monkeypatch.setattr(
        issue, "_prepare_branch", lambda branch: branches.append(branch),
    )

    with pytest.raises(TrackerConflictError):
        issue.start("GHQUAL-1")

    assert branches == []
    assert transport.state == "review"


@pytest.mark.parametrize("native_state", ["review", "done", "dropped"])
def test_pat67_public_start_refuses_invalid_predecessor_before_branch(
    tmp_path, monkeypatch, native_state,
):
    transport = _LifecycleTransport()
    transport.state = native_state
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    branches = []
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue, "_sh", lambda *_args: "")
    monkeypatch.setattr(
        issue, "_prepare_branch", lambda branch: branches.append(branch),
    )

    with pytest.raises((SystemExit, TrackerConflictError)):
        issue.start("GHQUAL-1")

    assert branches == []
    assert transport.comment_posts == 0
    assert transport.state_writes == 0


def test_pat67_zero_ac_refuses_before_codehost_merge_or_done_receipt(
    tmp_path, monkeypatch,
):
    transport = _LifecycleTransport()
    transport.body = "No acceptance criteria"
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    receipt_count = transport.comment_posts
    with pytest.raises(TrackerConflictError, match="matching acceptance"):
        tracker.set_state("GHQUAL-1", "done", _done_context(review), PROJECT)
    assert transport.comment_posts == receipt_count
    pr = PullRequest(
        number=1, url=review.pr_url, head="feat/ghqual-1", base="main",
        base_sha=review.base_sha, sha=review.head_sha,
    )
    codehost = type("CodeHost", (), {
        "name": "github",
        "resolve_repo": staticmethod(
            lambda: PROJECT.extra["canonical_repo"].removeprefix("github.com/")
        ),
        "get_pr": staticmethod(lambda *_args: pr),
        "merge_pr": staticmethod(lambda *_args: pytest.fail("merge attempted")),
    })()
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_args: None)

    with pytest.raises(SystemExit, match="aucun critère d’acceptation"):
        issue.merge("GHQUAL-1", "1")

    assert transport.comment_posts == receipt_count
    assert transport.state_writes == 2


def test_pat67_done_recovery_requires_exact_merged_pr_receipt(tmp_path):
    transport = _StateWriteFailsOnce()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    done = _done_context(review)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            transport.body, head=review.head_sha, base=review.base_sha,
            diff_hash=review.review_digest,
        ),
        checked=1, project=PROJECT,
    )
    transport.fail_state = "done"

    with pytest.raises(GitHubProjectsTrackerError):
        tracker.set_state("GHQUAL-1", "done", done, PROJECT)

    assert transport.state == "review"
    assert tracker.observe_issue("GHQUAL-1").normalized_state == "done"
    receipt_count = transport.comment_posts
    assert not tracker.recover_done_projection(
        "GHQUAL-1", pr_url=done.pr_url, head_sha="0" * 40,
        base_sha=done.base_sha, merge_sha=done.merge_sha, project=PROJECT,
    )
    assert transport.state == "review"
    assert tracker.recover_done_projection(
        "GHQUAL-1", pr_url=done.pr_url, head_sha=done.head_sha,
        base_sha=done.base_sha, merge_sha=done.merge_sha, project=PROJECT,
    )
    assert tracker.get_issue("GHQUAL-1").state == "done"
    assert transport.comment_posts == receipt_count
    assert transport.state_writes == 3


def test_pat67_public_merge_retry_repairs_missing_done_state(tmp_path, monkeypatch):
    transport = _StateWriteFailsOnce()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    done = _done_context(review)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            transport.body, head=review.head_sha, base=review.base_sha,
            diff_hash=review.review_digest,
        ),
        checked=1, project=PROJECT,
    )
    transport.fail_state = "done"
    with pytest.raises(GitHubProjectsTrackerError):
        tracker.set_state("GHQUAL-1", "done", done, PROJECT)
    receipt_count = transport.comment_posts
    pr = PullRequest(
        number=1, url=review.pr_url, head="feat/ghqual-1", base="main",
        base_sha=review.base_sha, sha=review.head_sha, merged=True,
        merge_sha=done.merge_sha,
    )
    events = []
    codehost = type("CodeHost", (), {
        "name": "github",
        "resolve_repo": staticmethod(lambda: PROJECT.extra["canonical_repo"].removeprefix("github.com/")),
        "get_pr": staticmethod(lambda *_args: pr),
        "merge_pr": staticmethod(lambda *_args: pytest.fail("duplicate merge")),
        "delete_branch": staticmethod(lambda *_args: events.append("delete")),
    })()
    monkeypatch.setattr(write, "issue_binding", lambda *_args: PROJECT)
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_args: None)
    monkeypatch.setattr(
        issue, "_cleanup_branch", lambda *_args: events.append("cleanup") or "linked-worktree",
    )
    monkeypatch.setattr(issue, "git_head", lambda: pytest.fail("git on replay"))

    issue.merge("GHQUAL-1", "1")

    assert tracker.get_issue("GHQUAL-1").state == "done"
    assert transport.comment_posts == receipt_count
    assert transport.state_writes == 3
    assert events == ["delete", "cleanup"]


def test_pat67_exact_receipt_refuses_changed_source_before_repair(tmp_path):
    transport = _StateWriteFailsOnce()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    review = _review_context()
    transport.fail_state = "review"
    with pytest.raises(GitHubProjectsTrackerError):
        tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    receipt_count = transport.comment_posts
    transport.body += "\nUnrelated edit\n"

    with pytest.raises(TrackerConflictError, match="exact receipt source changed"):
        tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    assert transport.state == "in-progress"
    assert transport.comment_posts == receipt_count


def test_pat67_release_classifies_only_aligned_done_proof(tmp_path, monkeypatch):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    done = _done_context(review)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            transport.body, head=review.head_sha, base=review.base_sha,
            diff_hash=review.review_digest,
        ),
        checked=1, project=PROJECT,
    )
    tracker.set_state("GHQUAL-1", "done", done, PROJECT)
    project = Project(
        key=PROJECT.key, id=PROJECT.id,
        extra={**PROJECT.extra, "release_ids": {"v1.0.0": "12"}},
    )
    snapshot = tracker._native_issue_read("GHQUAL-1")
    repo = "patobiskoto/foundry-v1-ghprojects-sandbox"
    original_rest = tracker._rest
    milestone = {
        "id": 1200, "node_id": "MI_node", "number": 12,
        "title": "v1.0.0", "state": "open",
        "url": f"https://api.github.com/repos/{repo}/milestones/12",
        "html_url": f"https://github.com/{repo}/milestone/12",
    }
    monkeypatch.setattr(
        tracker, "_rest",
        lambda path, operation: milestone if path.endswith("/milestones/12")
        else original_rest(path, operation),
    )
    original_rows = tracker._rows
    release_row = {
        "id": 1001, "node_id": "issue-node-1", "number": 1,
        "state": "closed", "milestone": {"number": 12, "title": "v1.0.0"},
        "repository_url": f"https://api.github.com/repos/{repo}",
        "url": f"https://api.github.com/repos/{repo}/issues/1",
        "html_url": f"https://github.com/{repo}/issues/1",
    }
    monkeypatch.setattr(
        tracker, "_rows",
        lambda path, operation: [release_row] if "milestone=12" in path
        else original_rows(path, operation),
    )
    monkeypatch.setattr(tracker, "_search_raw", lambda *_args: [deepcopy(snapshot)])
    monkeypatch.setattr(tracker, "_hydrate_issue", lambda current, *_args: current)

    accepted = tracker.read_release_scope(project, "v1.0.0").issues[0]
    assert accepted.disposition == "accepted"
    assert accepted.references["merge_sha"] == done.merge_sha
    assert accepted.references["acceptance_proof_id"]

    snapshot.state = "review"
    disagreed = tracker.read_release_scope(project, "v1.0.0").issues[0]
    assert disagreed.disposition == "unavailable"
    assert "merge_sha" not in disagreed.references


def test_pat67_raw_transport_new_review_acceptance_generation_after_prior_acceptance(
    tmp_path,
):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    first = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", first, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            transport.body, head=first.head_sha, base=first.base_sha,
            diff_hash=first.review_digest,
        ),
        checked=1, project=PROJECT,
    )

    second = _review_context(generation=2, expected_state="review")
    tracker.set_state("GHQUAL-1", "review", second, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            # Review claims are keyed by diff, so the first claim for a new
            # diff is generation 1 even when this is lifecycle review 2.
            transport.body, generation=1, head=second.head_sha,
            base=second.base_sha, diff_hash=second.review_digest,
        ),
        checked=1, project=PROJECT,
    )
    tracker.set_state("GHQUAL-1", "done", _done_context(second), PROJECT)

    assert tracker.get_issue("GHQUAL-1").state == "done"
    assert transport.comment_posts == 6
    assert transport.state_writes == 3


def test_pat67_return_to_older_review_sha_requires_new_acceptance(tmp_path):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    first = _review_context()
    second = _review_context(generation=2, expected_state="review")
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    for review in (first, second):
        tracker.set_state("GHQUAL-1", "review", review, PROJECT)
        tracker.project_acceptance_proof(
            "GHQUAL-1", transport.body,
            _canonical_acceptance_proof(
                transport.body, head=review.head_sha, base=review.base_sha,
                diff_hash=review.review_digest,
            ),
            checked=1, project=PROJECT,
        )
    returned = _review_context(expected_state="review")
    tracker.set_state("GHQUAL-1", "review", returned, PROJECT)
    observed = tracker.get_issue("GHQUAL-1")
    assert (observed.ac_done, observed.ac_total) == (0, 1)
    scope = tracker._current_lifecycle_scope("GHQUAL-1", tracker._binding(PROJECT))
    rows = tracker._lifecycle_rows(
        "GHQUAL-1", tracker._native_issue_read("GHQUAL-1"), scope,
    )
    assert [payload["generation"] for payload, _ in rows["state-review"]] == [1, 2, 3]
    with pytest.raises(TrackerConflictError, match="matching acceptance"):
        tracker.set_state("GHQUAL-1", "done", _done_context(returned), PROJECT)
    assert transport.state == "review"


def test_pat67_definitive_auth_rejection_clears_local_intent_for_retry(tmp_path):
    class ExpiredCredentialTransport(_LifecycleTransport):
        def __init__(self):
            super().__init__()
            self.fail_next_post = True
            self.auth_down = False
            self.posts_attempted = 0

        def __call__(self, command, **kwargs):
            if command[2:4] == ["-X", "POST"] and command[4].endswith("/comments"):
                self.posts_attempted += 1
                if self.fail_next_post:
                    self.fail_next_post = False
                    self.auth_down = True
                    return self._response(
                        command, {"message": "Bad credentials", "status": "401"},
                        returncode=1, stderr="gh: HTTP 401",
                    )
            if self.auth_down and command[2] == "graphql":
                return self._response(
                    command, {"message": "Bad credentials", "status": "401"},
                    returncode=1, stderr="gh: HTTP 401",
                )
            return super().__call__(command, **kwargs)

    transport = ExpiredCredentialTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    with pytest.raises(GitHubProjectsTrackerError) as refused:
        tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    assert refused.value.reason == "authentication_failed"
    assert transport.posts_attempted == 1
    assert transport.comment_posts == 0

    transport.auth_down = False
    GitHubProjectsTracker(runner=transport, state_dir=tmp_path).set_state(
        "GHQUAL-1", "in-progress", project=PROJECT,
    )
    assert transport.posts_attempted == 2
    assert transport.comment_posts == 1
    assert tracker.get_issue("GHQUAL-1").state == "in-progress"


def test_pat67_rate_limit_keeps_pending_intent_until_receipt_visible(tmp_path):
    class RateLimitedTransport(_LifecycleTransport):
        def __init__(self):
            super().__init__()
            self.posts_attempted = 0
            self.delayed_body = None

        def __call__(self, command, **kwargs):
            if command[2:4] == ["-X", "POST"] and command[4].endswith("/comments"):
                self.posts_attempted += 1
                self.delayed_body = next(
                    value.removeprefix("body=") for value in command
                    if value.startswith("body=")
                )
                return self._response(
                    command, {"message": "rate limit", "status": "429"},
                    returncode=1, stderr="gh: HTTP 429",
                )
            return super().__call__(command, **kwargs)

    transport = RateLimitedTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    with pytest.raises(GitHubProjectsTrackerError) as limited:
        tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    assert limited.value.reason == "effect_unknown"
    with pytest.raises(GitHubProjectsTrackerError) as replay:
        tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    assert replay.value.reason == "effect_unknown"
    assert transport.posts_attempted == 1

    transport.comments.append({
        "id": 2001, "body": transport.delayed_body,
        "created_at": "2026-09-27T12:01:00Z",
    })
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    assert transport.posts_attempted == 1
    assert tracker.get_issue("GHQUAL-1").state == "in-progress"


@pytest.mark.parametrize("partial_first", [False, True])
def test_pat67_common_openpr_publishes_corrected_head_as_next_review(
    tmp_path,
    monkeypatch,
    partial_first,
):
    transport = _StateWriteFailsOnce() if partial_first else _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    repository = "patobiskoto/foundry-v1-ghprojects-sandbox"
    branch = "feat/ghqual-1-corrected-review"
    current = {
        "diff": b"first review diff",
        "opened": False,
        "sha": "a" * 40,
    }

    monkeypatch.setattr(
        "foundry.registry.checkout_repository_identity",
        lambda cwd=None: PROJECT.extra["canonical_repo"],
    )
    write.transition(
        tracker,
        "GHQUAL-1",
        "in-progress",
        context=TransitionContext(expected_state="ready"),
    )

    def pull_request():
        return PullRequest(
            number=1,
            url=f"https://github.com/{repository}/pull/1",
            head=branch,
            base="main",
            base_sha="b" * 40,
            sha=current["sha"],
        )

    def list_prs(_repo, _branch):
        return [pull_request()] if current["opened"] else []

    def open_pr(*_args):
        current["opened"] = True
        return pull_request()

    codehost = type("CodeHost", (), {
        "name": "github",
        "resolve_repo": staticmethod(lambda: repository),
        "list_prs": staticmethod(list_prs),
        "open_pr": staticmethod(open_pr),
        "get_pr": staticmethod(lambda *_args: pull_request()),
    })()

    def shell(*args, **_kwargs):
        if args == ("git", "rev-parse", "--abbrev-ref", "HEAD"):
            return branch
        if args == ("git", "push", "-u", "origin", branch):
            return ""
        raise AssertionError(f"unexpected shell call: {args}")

    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(issue, "_sh", shell)
    monkeypatch.setattr(issue, "_default_branch", lambda: "main")
    monkeypatch.setattr(issue, "git_head", lambda: current["sha"])
    monkeypatch.setattr(issue, "git_diff", lambda **_kwargs: current["diff"])

    if partial_first:
        transport.fail_state = "review"
        with pytest.raises(GitHubProjectsTrackerError):
            issue.openpr("GHQUAL-1")
    else:
        issue.openpr("GHQUAL-1")
    current.update(sha="c" * 40, diff=b"corrected review diff")
    issue.openpr("GHQUAL-1")
    issue.openpr("GHQUAL-1")

    observed = tracker.get_issue("GHQUAL-1")
    binding = tracker._binding(PROJECT)
    rows = tracker._lifecycle_rows(
        "GHQUAL-1",
        observed,
        tracker._current_lifecycle_scope("GHQUAL-1", binding),
    )
    reviews = rows["state-review"]
    assert [(payload["generation"], payload["head_sha"]) for payload, _body in reviews] == [
        (1, "a" * 40),
        (2, "c" * 40),
    ]
    assert observed.state == "review"
    assert transport.comment_posts == 3
    assert transport.state_writes == 2


@pytest.mark.parametrize("outcome", ["applied", "hidden"])
def test_pat67_raw_transport_lost_comment_response_is_never_reposted(
    tmp_path, outcome,
):
    transport = _LifecycleTransport(comment_loss=outcome)
    first = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    if outcome == "applied":
        first.set_state("GHQUAL-1", "in-progress", project=PROJECT)
        assert transport.state == "in-progress"
    else:
        with pytest.raises(GitHubProjectsTrackerError, match="effect_unknown"):
            first.set_state("GHQUAL-1", "in-progress", project=PROJECT)

    second = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    if outcome == "applied":
        second.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    else:
        with pytest.raises(GitHubProjectsTrackerError, match="effect_unknown"):
            second.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    assert transport.comment_posts == 1


@pytest.mark.parametrize(
    ("field", "foreign_value"),
    [
        ("project_id", "PVT_foreign"),
        ("repository", "patobiskoto/foreign-repository"),
        ("native_issue_id", 9999),
        ("issue_node_id", "issue-node-foreign"),
        ("project_item_id", "item-foreign"),
    ],
)
def test_pat67_projection_rejects_receipt_copied_from_foreign_coordinates(
    tmp_path, field, foreign_value,
):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    decoded = tracker._decode_lifecycle_comment(
        "GHQUAL-1", transport.comments[0]["body"],
    )
    assert decoded is not None
    operation, payload, _body = decoded
    foreign = {**LIFECYCLE_SCOPE, field: foreign_value}
    transport.comments[0]["body"] = tracker._lifecycle_marker(
        operation, "GHQUAL-1", payload, foreign,
    )[1]

    with pytest.raises(TrackerConflictError, match="comment malformed"):
        tracker.get_issue("GHQUAL-1")


def test_pat67_projection_rejects_marker_without_canonical_scope(tmp_path):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    body = transport.comments[0]["body"]
    coordinates = json.loads(body.splitlines()[2].removeprefix("coordinates: "))
    coordinates.pop("scope")
    encoded = json.dumps(
        coordinates, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    marker = (
        "foundry-ghprojects-lifecycle.v1:state-in-progress:"
        + hashlib.sha256(encoded.encode("ascii")).hexdigest()
    )
    transport.comments[0]["body"] = (
        f"Foundry lifecycle proof (append-only).\nmarker: {marker}\n"
        f"coordinates: {encoded}"
    )

    with pytest.raises(TrackerConflictError, match="comment malformed"):
        tracker.get_issue("GHQUAL-1")


def test_pat67_acceptance_rejects_partial_count_and_noncanonical_proof(tmp_path):
    transport = _LifecycleTransport()
    transport.body += "- [ ] Second criterion\n"
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    proof = _canonical_acceptance_proof(
        transport.body, head=review.head_sha, base=review.base_sha,
        diff_hash=review.review_digest,
    )

    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        tracker.project_acceptance_proof(
            "GHQUAL-1", transport.body, proof, checked=1, project=PROJECT,
        )
    legacy = {
        key: value for key, value in proof.items()
        if key in {"issue", "coordinates", "quality"}
    }
    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        tracker.project_acceptance_proof(
            "GHQUAL-1", transport.body, legacy, checked=2, project=PROJECT,
        )
    assert transport.comment_posts == 2


def test_pat67_acceptance_survives_checkbox_progress_but_not_ac_text_edit(tmp_path):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            transport.body, head=review.head_sha, base=review.base_sha,
            diff_hash=review.review_digest,
        ),
        checked=1, project=PROJECT,
    )

    transport.body = transport.body.replace("- [ ]", "- [x]")
    observed = tracker.get_issue("GHQUAL-1")
    assert observed.ac_done == 1
    assert observed.acceptance_status == "accepted"
    assert observed.acceptance_source == "ghprojects-acceptance-proof"
    assert json.loads(observed.acceptance_coordinates) == {
        "body_digest": tracker._acceptance_body_digest(transport.body),
        "checked": 1,
        "proof_id": _canonical_acceptance_proof(
            transport.body,
            head=review.head_sha,
            base=review.base_sha,
            diff_hash=review.review_digest,
        )["proof_id"],
        "review_generation": 1,
    }
    transport.body = transport.body.replace("Exact criterion", "Edited criterion")
    with pytest.raises(TrackerConflictError, match="malformed or stale"):
        tracker.get_issue("GHQUAL-1")


@pytest.mark.parametrize(
    "corruption",
    ["legacy", "blocked", "foreign_issue", "claim", "checked"],
)
def test_pat67_read_revalidates_every_acceptance_field(tmp_path, corruption):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            transport.body, head=review.head_sha, base=review.base_sha,
            diff_hash=review.review_digest,
        ),
        checked=1, project=PROJECT,
    )
    operation, payload, _body = tracker._decode_lifecycle_comment(
        "GHQUAL-1", transport.comments[-1]["body"],
    )
    proof = payload["proof"]
    if corruption == "legacy":
        payload["proof"] = {
            key: value for key, value in proof.items()
            if key in {"issue", "coordinates", "quality"}
        }
    else:
        if corruption == "blocked":
            proof["quality"] = "blocked"
        elif corruption == "foreign_issue":
            proof["issue"]["id"] = "GHQUAL-2"
        elif corruption == "claim":
            proof["review"]["claim_digest"] = "not-a-digest"
        elif corruption == "checked":
            payload["checked"] = 0
        if corruption != "checked":
            canonical = dict(proof)
            canonical.pop("proof_id")
            proof["proof_id"] = hashlib.sha256(
                json.dumps(
                    canonical, sort_keys=True, separators=(",", ":"),
                ).encode(),
            ).hexdigest()
    transport.comments[-1]["body"] = tracker._lifecycle_marker(
        operation, "GHQUAL-1", payload, LIFECYCLE_SCOPE,
    )[1]

    with pytest.raises(TrackerConflictError, match="acceptance proof"):
        tracker.get_issue("GHQUAL-1")


@pytest.mark.parametrize("corruption", ["duplicate", "hole", "out_of_order"])
def test_pat67_full_history_rejects_duplicate_hole_and_out_of_order(
    tmp_path, corruption,
):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body,
        _canonical_acceptance_proof(
            transport.body, head=review.head_sha, base=review.base_sha,
            diff_hash=review.review_digest,
        ),
        checked=1, project=PROJECT,
    )
    if corruption == "duplicate":
        duplicate = deepcopy(transport.comments[-1])
        duplicate["id"] += 100
        transport.comments.append(duplicate)
    elif corruption == "hole":
        operation, payload, _body = tracker._decode_lifecycle_comment(
            "GHQUAL-1", transport.comments[1]["body"],
        )
        payload["generation"] = 2
        transport.comments[1]["body"] = tracker._lifecycle_marker(
            operation, "GHQUAL-1", payload, LIFECYCLE_SCOPE,
        )[1]
    else:
        transport.comments[1], transport.comments[2] = (
            transport.comments[2], transport.comments[1]
        )

    with pytest.raises(TrackerConflictError):
        tracker.get_issue("GHQUAL-1")


def test_pat67_lifecycle_reads_receipt_beyond_first_comment_page(tmp_path):
    transport = _LifecycleTransport()
    transport.comments = [
        {
            "id": index,
            "body": f"ordinary note {index}",
            "created_at": "2026-09-27T11:00:00Z",
        }
        for index in range(1, 101)
    ]
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)

    observed = tracker.get_issue("GHQUAL-1")
    assert observed.state == "in-progress"
    assert transport.comment_posts == 1


@pytest.mark.parametrize("phase", ["comment", "state"])
def test_pat67_state_write_rejects_untargeted_property_drift(tmp_path, phase):
    class DriftingTransport(_LifecycleTransport):
        def __call__(self, command, **kwargs):
            is_comment = (
                command[2] != "graphql" and command[3] == "POST"
                and command[4].endswith("/comments")
            )
            is_state = (
                command[2] == "graphql"
                and any(value.startswith("query=mutation") for value in command)
            )
            response = super().__call__(command, **kwargs)
            if phase == "comment" and is_comment:
                self.body += "\nexternal body drift"
            if phase == "state" and is_state:
                self.body += "\nexternal body drift"
            return response

    transport = DriftingTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    with pytest.raises(TrackerConflictError, match="source changed|untargeted"):
        tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    assert transport.comment_posts == 1
    assert transport.state_writes == (1 if phase == "state" else 0)


def test_pat67_original_expected_state_is_not_replaced_by_fresh_read(tmp_path):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    with pytest.raises(TrackerConflictError, match="predecessor unavailable"):
        tracker.set_state(
            "GHQUAL-1", "in-progress",
            TransitionContext(expected_state="backlog"), PROJECT,
        )
    assert transport.comment_posts == transport.state_writes == 0


def test_pat67_conflicting_acceptance_same_generation_refuses_before_post(tmp_path):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    proof = _canonical_acceptance_proof(
        transport.body, head=review.head_sha, base=review.base_sha,
        diff_hash=review.review_digest,
    )
    tracker.project_acceptance_proof(
        "GHQUAL-1", transport.body, proof, checked=1, project=PROJECT,
    )
    conflicting = deepcopy(proof)
    conflicting["review"]["claim_digest"] = "e" * 64
    canonical = dict(conflicting)
    canonical.pop("proof_id")
    conflicting["proof_id"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()

    with pytest.raises(TrackerConflictError, match="conflicts with this review"):
        tracker.project_acceptance_proof(
            "GHQUAL-1", transport.body, conflicting,
            checked=1, project=PROJECT,
        )
    assert transport.comment_posts == 3


def test_pat67_acceptance_refuses_native_state_disagreement_before_post(tmp_path):
    transport = _LifecycleTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)
    transport.state = "in-progress"

    with pytest.raises(TrackerConflictError, match="aligned review"):
        tracker.project_acceptance_proof(
            "GHQUAL-1", transport.body,
            _canonical_acceptance_proof(
                transport.body, head=review.head_sha, base=review.base_sha,
                diff_hash=review.review_digest,
            ),
            checked=1, project=PROJECT,
        )
    assert transport.comment_posts == 2


def test_pat67_acceptance_detects_untargeted_state_drift_after_comment(tmp_path):
    class DriftingTransport(_LifecycleTransport):
        def __call__(self, command, **kwargs):
            response = super().__call__(command, **kwargs)
            if (
                command[2] != "graphql"
                and command[3] == "POST"
                and command[4].endswith("/comments")
                and self.comment_posts == 3
            ):
                self.state = "blocked"
            return response

    transport = DriftingTransport()
    tracker = GitHubProjectsTracker(runner=transport, state_dir=tmp_path)
    review = _review_context()
    tracker.set_state("GHQUAL-1", "in-progress", project=PROJECT)
    tracker.set_state("GHQUAL-1", "review", review, PROJECT)

    with pytest.raises(TrackerConflictError, match="untargeted properties"):
        tracker.project_acceptance_proof(
            "GHQUAL-1", transport.body,
            _canonical_acceptance_proof(
                transport.body, head=review.head_sha, base=review.base_sha,
                diff_hash=review.review_digest,
            ),
            checked=1, project=PROJECT,
        )
    assert transport.comment_posts == 3


def _write_issue(*, priority="P1", estimate=None, body="body", links=()):
    return Issue(
        id="GHQUAL-1", title="issue 1", priority=priority, estimate=estimate,
        body=body, links=list(links),
    )


class _FieldWriteTransport:
    """Stateful GitHub transport for per-field S1/S2/S3 adverse paths."""

    def __init__(
        self, *, priority="P1", estimate=3, lost_response_applies=None,
        drift_after_priority=False,
    ):
        self.priority = priority
        self.estimate = estimate
        self.body = "UTF-8 é"
        self.lost_response_applies = lost_response_applies
        self.drift_after_priority = drift_after_priority
        self.failure_sent = False
        self.events = []
        self.mutation_fields = []

    def _project_payload(self):
        payload = _page("item-1", 1, None, False)
        values = payload["data"]["user"]["projectV2"]["items"]["nodes"][0][
            "fieldValues"
        ]["nodes"]
        priority = next(row for row in values if row["field"]["id"] == "priority")
        priority["name"] = self.priority
        priority["optionId"] = f"priority-{self.priority.casefold()}"
        estimate = next(row for row in values if row["field"]["id"] == "estimate")
        estimate["number"] = self.estimate
        return payload

    def _apply(self, field):
        if field == "priority":
            self.priority = "P2"
            if self.drift_after_priority:
                self.body = "third-party body"
        elif field == "estimate":
            self.estimate = 5
        else:
            pytest.fail(f"unexpected field mutation: {field}")

    def __call__(self, command, **kwargs):
        if command[2] == "graphql":
            query = next(arg for arg in command if arg.startswith("query="))
            if "updateProjectV2ItemFieldValue" in query:
                field = next(
                    arg.removeprefix("field=")
                    for arg in command if arg.startswith("field=")
                )
                self.events.append(f"write:{field}")
                self.mutation_fields.append(field)
                if self.lost_response_applies is not None and not self.failure_sent:
                    self.failure_sent = True
                    if self.lost_response_applies:
                        self._apply(field)
                    return subprocess.CompletedProcess(
                        command, 1, "", "synthetic transport failure",
                    )
                self._apply(field)
                payload = {
                    "data": {
                        "updateProjectV2ItemFieldValue": {
                            "projectV2Item": {"id": "item-1"},
                        },
                    },
                }
            else:
                self.events.append("read:project")
                payload = self._project_payload()
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        path = command[-1]
        if path == "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1":
            payload = _rest_issue(1)
            payload["body"] = self.body
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")


def test_pat66_targeted_field_write_rereads_and_preserves_unrelated_values(monkeypatch):
    tracker = GitHubProjectsTracker()
    reads = iter([_write_issue(priority="P1"), _write_issue(priority="P1"),
                  _write_issue(priority="P2")])
    writes = []
    monkeypatch.setattr(tracker, "get_issue", lambda issue_id: next(reads))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_: ("item-1", "node-1"))
    monkeypatch.setattr(tracker, "_field_catalog", lambda *_: {"priority": type("F", (), {"id": "priority", "data_type": "SINGLE_SELECT", "options": {"P2": "p2"}})()})
    monkeypatch.setattr(tracker, "_set_project_field", lambda *args: writes.append(args))

    updated = tracker.update_fields("GHQUAL-1", {"Priority": "P2"}, project=PROJECT)

    assert updated.priority == "P2"
    assert len(writes) == 1
    assert writes[0][-1] == "P2"


def test_pat66_drift_before_field_write_has_no_effect(monkeypatch):
    tracker = GitHubProjectsTracker()
    reads = iter([_write_issue(priority="P1"), _write_issue(priority="P2")])
    writes = []
    monkeypatch.setattr(tracker, "get_issue", lambda issue_id: next(reads))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_: ("item-1", "node-1"))
    monkeypatch.setattr(
        tracker, "_field_catalog",
        lambda *_: {
            "priority": type(
                "F", (), {
                    "id": "priority", "data_type": "SINGLE_SELECT",
                    "options": {"P2": "p2"},
                },
            )(),
        },
    )
    monkeypatch.setattr(tracker, "_set_project_field", lambda *args: writes.append(args))

    with pytest.raises(TrackerConflictError, match="avant écriture"):
        tracker.update_fields("GHQUAL-1", {"Priority": "P2"}, project=PROJECT)
    assert writes == []


def test_pat66_field_already_at_target_has_no_mutation():
    transport = _FieldWriteTransport(priority="P2")
    tracker = GitHubProjectsTracker(runner=transport)

    updated = tracker.update_fields(
        "GHQUAL-1", {"Priority": "P2"}, project=PROJECT,
    )

    assert updated.priority == "P2"
    assert transport.mutation_fields == []


@pytest.mark.parametrize("applied", [True, False])
def test_pat66_ambiguous_field_write_is_observed_once_without_rewrite(
    applied,
):
    transport = _FieldWriteTransport(lost_response_applies=applied)
    tracker = GitHubProjectsTracker(runner=transport)

    if applied:
        updated = tracker.update_fields(
            "GHQUAL-1", {"Priority": "P2"}, project=PROJECT,
        )
        assert updated.priority == "P2"
    else:
        with pytest.raises(GitHubProjectsTrackerError, match="transport_failed"):
            tracker.update_fields(
                "GHQUAL-1", {"Priority": "P2"}, project=PROJECT,
            )
    assert transport.mutation_fields == ["priority"]
    mutation = transport.events.index("write:priority")
    assert "read:project" in transport.events[mutation + 1:]


def test_pat66_multi_field_write_observes_each_effect_before_the_next():
    transport = _FieldWriteTransport()
    tracker = GitHubProjectsTracker(runner=transport)

    updated = tracker.update_fields(
        "GHQUAL-1", {"Priority": "P2", "Estimate": 5}, project=PROJECT,
    )

    assert (updated.priority, updated.estimate) == ("P2", 5)
    assert transport.mutation_fields == ["priority", "estimate"]
    priority_write = transport.events.index("write:priority")
    estimate_write = transport.events.index("write:estimate")
    assert "read:project" in transport.events[priority_write + 1:estimate_write]
    assert "read:project" in transport.events[estimate_write + 1:]


def test_pat66_unrelated_drift_after_one_field_stops_multi_field_write():
    transport = _FieldWriteTransport(drift_after_priority=True)
    tracker = GitHubProjectsTracker(runner=transport)

    with pytest.raises(TrackerConflictError, match="divergent après écriture"):
        tracker.update_fields(
            "GHQUAL-1", {"Priority": "P2", "Estimate": 5}, project=PROJECT,
        )

    assert transport.mutation_fields == ["priority"]


def test_pat66_comment_response_loss_is_one_post_without_retry(monkeypatch):
    tracker = GitHubProjectsTracker()
    writes = []
    monkeypatch.setattr(tracker, "_native_issue", lambda *_: (1, 1001))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_: ("item-1", "node-1"))
    def lost(*args):
        writes.append(args)
        raise GitHubProjectsTrackerError("issue.comment", "transport_failed")
    monkeypatch.setattr(tracker, "_rest_write", lost)

    with pytest.raises(GitHubProjectsTrackerError, match="transport_failed"):
        tracker.add_comment("GHQUAL-1", "non-authoritative note", project=PROJECT)
    assert len(writes) == 1
    assert writes[0][0:2] == ("POST", "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/comments")


@pytest.mark.parametrize(
    ("project_item_number", "adr"),
    [(2, False), (1, True)],
    ids=["outside-project", "adr-support"],
)
def test_pat66_comment_requires_active_project_delivery_item_before_post(
    project_item_number, adr,
):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[2] == "graphql":
            payload = _page(
                f"item-{project_item_number}", project_item_number, None, False,
                adr=adr,
            )
        else:
            assert command[-1].endswith("/issues/1")
            payload = _rest_issue(1)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(IssueUnavailableError):
        GitHubProjectsTracker(runner=runner).add_comment(
            "GHQUAL-1", "must stay absent", project=PROJECT,
        )

    assert not any(
        "-X" in command and command[command.index("-X") + 1] == "POST"
        for command in calls
    )


def test_pat66_create_known_partial_attachment_never_reposts_issue(monkeypatch, tmp_path):
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    writes = []
    raw = _rest_issue(4)
    raw.update({"node_id": "issue-node-4", "title": "partial", "body": "body"})
    def rest_write(*args):
        writes.append(args)
        return raw
    monkeypatch.setattr(tracker, "_rest_write", rest_write)
    monkeypatch.setattr(tracker, "_create_candidates", lambda *_: [])
    monkeypatch.setattr(tracker, "_partial_project_issue", lambda *_: None)
    monkeypatch.setattr(tracker, "_add_project_item", lambda *_: (_ for _ in ()).throw(
        GitHubProjectsTrackerError("project.item_create", "transport_failed")
    ))
    monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
    monkeypatch.setattr(
        tracker, "_write_catalog", lambda *_: {
            "type": type("F", (), {"id": "type", "data_type": "SINGLE_SELECT", "options": {"Task": "type-task"}})(),
        },
    )

    with pytest.raises(GitHubProjectsTrackerError, match="partial_create:item") as exc:
        tracker.create_issue(PROJECT, "partial", "body")
    assert exc.value.partial_issue_id == "GHQUAL-4"
    assert exc.value.partial_native_issue_id == 1004
    assert len(writes) == 1
    assert writes[0][0:2] == ("POST", "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues")


def test_pat66_rest_write_preserves_native_relation_json_types():
    def runner(command, **kwargs):
        transmitted = {}
        for index, arg in enumerate(command):
            if arg in {"-f", "-F"}:
                key, value = command[index + 1].split("=", 1)
                transmitted[key] = json.loads(value) if arg == "-F" else value
        assert type(transmitted["sub_issue_id"]) is int
        assert type(transmitted["replace_parent"]) is bool
        return subprocess.CompletedProcess(command, 0, "{}", "")

    GitHubProjectsTracker(runner=runner)._rest_write(
        "POST", "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/sub_issues",
        {"sub_issue_id": 123, "replace_parent": True}, "issue.link_write",
    )


def test_pat66_create_refuses_unqualified_priority_before_issue_post(monkeypatch):
    tracker = GitHubProjectsTracker()
    writes = []
    monkeypatch.setattr(tracker, "_rest_write", lambda *args: writes.append(args))
    monkeypatch.setattr(
        tracker, "_field_catalog", lambda *_: {
            "type": type("F", (), {"data_type": "SINGLE_SELECT", "options": {"Task": "type-task"}})(),
            "priority": type("F", (), {"data_type": "SINGLE_SELECT", "options": {"P1": "priority-p1"}})(),
        },
    )

    with pytest.raises(TrackerCapabilityUnavailableError, match="field_option:priority:P3"):
        tracker.create_issue(PROJECT, "title", "body", {"Priority": "P3"})
    assert writes == []


@pytest.mark.parametrize("value", [True, 1.5, float("nan"), float("inf"), float("-inf")])
def test_pat66_create_refuses_non_exact_integer_estimate_before_effect(value, tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        raise AssertionError("invalid estimate reached transport")

    tracker = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    with pytest.raises(GitHubProjectsTrackerError, match="invalid_field_value:estimate"):
        tracker.create_issue(PROJECT, "title", "body", {"Estimate": value})
    assert calls == []


def test_pat66_estimate_keeps_exact_negative_integer_and_graphql_integer(monkeypatch, tmp_path):
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    assert tracker._portable_fields({"Estimate": -3}) == {"estimate": -3}
    calls = []
    monkeypatch.setattr(
        tracker, "_graphql_mutation",
        lambda query, variables, operation: calls.append((query, variables, operation))
        or {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "item-1"}}},
    )
    field = type("F", (), {"id": "estimate", "data_type": "NUMBER", "options": {}})()
    tracker._set_project_field("item-1", PROJECT.id, field, -3)
    assert "{number:-3}" in calls[0][0]
    assert "-3.0" not in calls[0][0]


@pytest.mark.parametrize("value", [3.5, float("nan"), float("inf")])
def test_pat66_read_refuses_non_integer_project_estimate(value):
    payload = _page("item-1", 1, None, False)
    values = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"]
    next(row for row in values if row["field"]["id"] == "estimate")["number"] = value

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="invalid_estimate"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


def test_pat66_create_candidate_scan_is_bounded_canonical_and_excludes_adr():
    calls = []
    delivery = _rest_issue(4)
    delivery.update({"node_id": "issue-node-4", "title": "same", "body": "body"})
    adr = _rest_issue(5)
    adr.update({
        "node_id": "issue-node-5", "title": "same", "body": "body",
        "labels": [{"name": "foundry:adr"}],
    })
    other = _rest_issue(6)
    other.update({"node_id": "issue-node-6", "title": "other", "body": "body"})

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, json.dumps([delivery, adr, other]), "",
        )

    tracker = GitHubProjectsTracker(runner=runner)
    candidates = tracker._create_candidates(tracker._binding(PROJECT), "same", "body")
    assert candidates == [_CreateCandidate("GHQUAL-4", 4, 1004, "issue-node-4")]
    assert calls[0][-1].endswith("issues?state=all&per_page=100&page=1")


@pytest.mark.parametrize("parent_shape", ["absent", "adr"])
def test_pat66_create_parent_must_be_delivery_item_before_any_write(
    parent_shape, tmp_path,
):
    calls = []
    raw = _rest_issue(9)
    raw["node_id"] = "issue-node-9"
    page = _page("item-9", 9, None, False, adr=parent_shape == "adr")
    if parent_shape == "absent":
        page["data"]["user"]["projectV2"]["items"]["nodes"] = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[2] == "graphql":
            payload = page
        elif command[-1].endswith("/issues/9"):
            payload = raw
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    tracker = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    expected = "candidate_is_adr_support" if parent_shape == "adr" else "issue unavailable"
    with pytest.raises((GitHubProjectsTrackerError, IssueUnavailableError), match=expected):
        tracker.create_issue(PROJECT, "child", "body", parent="GHQUAL-9")
    assert not any("POST" in command for command in calls)


def test_pat66_parent_preflight_tolerates_unrelated_partial_item_without_weakening_reads():
    page = _page("item-1", 1, None, False)
    project = page["data"]["user"]["projectV2"]
    partial = deepcopy(project["items"]["nodes"][0])
    partial["id"] = "item-4"
    partial["content"]["id"] = "issue-node-4"
    partial["content"]["number"] = 4
    partial["content"]["title"] = "partial"
    partial["fieldValues"]["nodes"] = [
        value for value in partial["fieldValues"]["nodes"]
        if value["field"]["id"] != "type"
    ]
    project["items"]["nodes"].append(partial)
    raw = _rest_issue(1)
    raw["node_id"] = "issue-node-1"

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(page), "")
        path = command[-1]
        if path.endswith("/issues/1"):
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    tracker = GitHubProjectsTracker(runner=runner)
    assert tracker._create_parent("GHQUAL-1", tracker._binding(PROJECT)) == 1
    with pytest.raises(GitHubProjectsTrackerError, match="invalid_type"):
        tracker.search(PROJECT)


def _install_create_harness(monkeypatch, tracker, provider, *, failing_phase=None):
    candidate = _CreateCandidate("GHQUAL-4", 4, 1004, "issue-node-4")
    catalog = {
        "type": type(
            "F", (), {
                "id": "type", "data_type": "SINGLE_SELECT",
                "options": {"Task": "type-task"},
            },
        )(),
    }

    monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
    monkeypatch.setattr(tracker, "_write_catalog", lambda *_: catalog)
    monkeypatch.setattr(
        tracker, "_create_candidates",
        lambda *_: [candidate] if provider.get("issue") else [],
    )
    monkeypatch.setattr(tracker, "_create_parent", lambda *_: 1)
    def parent_snapshot(*_):
        provider.setdefault("parent_observations", []).append(provider["field_posts"])
        links = [Link("parent-of", "outward", "GHQUAL-4")] if provider.get("parent") else []
        return 1, Issue(id="GHQUAL-1", title="parent", type="Epic", links=links)

    monkeypatch.setattr(tracker, "_create_parent_snapshot", parent_snapshot)

    def rest_write(method, path, payload, operation):
        if path.endswith("/issues"):
            provider["issue_posts"] += 1
            provider["issue"] = True
            if provider.pop("lose_issue_response", False):
                raise GitHubProjectsTrackerError("issue.create", "transport_failed")
            raw = _rest_issue(4)
            raw.update({
                "node_id": candidate.content_id,
                "title": "partial",
                "body": "body",
            })
            return raw
        if path.endswith("/sub_issues"):
            provider["parent_posts"] += 1
            if failing_phase == "parent" and provider["parent_posts"] == 1:
                raise GitHubProjectsTrackerError("issue.parent_create", "transport_failed")
            provider["parent"] = True
            return {}
        raise AssertionError((method, path, payload, operation))

    def partial_project(*_):
        if not provider.get("item"):
            return None
        issue = Issue(
            id=candidate.issue_id,
            title="partial",
            body="body",
            type=provider["fields"].get("type"),
        )
        return "item-4", issue

    def add_item(*_):
        provider["item_posts"] += 1
        if failing_phase == "item" and provider["item_posts"] == 1:
            raise GitHubProjectsTrackerError("project.item_create", "transport_failed")
        provider["item"] = True
        return "item-4"

    def set_field(item_id, project_id, field, value):
        provider["field_posts"] += 1
        if failing_phase == "field" and provider["field_posts"] == 1:
            raise GitHubProjectsTrackerError("project.field_write", "transport_failed")
        provider["fields"][field.id] = value

    def readback(_binding, observed_candidate):
        assert provider.get("item") and provider["fields"].get("type") == "Task"
        links = [Link("subtask-of", "inward", "GHQUAL-1")] if provider.get("parent") else []
        return Issue(
            id=observed_candidate.issue_id, title="partial", body="body",
            type="Task", links=links,
        )

    monkeypatch.setattr(tracker, "_rest_write", rest_write)
    monkeypatch.setattr(tracker, "_partial_project_issue", partial_project)
    monkeypatch.setattr(tracker, "_add_project_item", add_item)
    monkeypatch.setattr(tracker, "_set_project_field", set_field)
    monkeypatch.setattr(tracker, "_created_issue_readback", readback)


def _create_provider_state(**extra):
    return {
        "issue": False,
        "issue_posts": 0,
        "item": False,
        "item_posts": 0,
        "field_posts": 0,
        "parent_posts": 0,
        "parent": False,
        "fields": {},
        **extra,
    }


def test_pat66_lost_issue_response_replays_in_second_instance_without_duplicate(
    monkeypatch, tmp_path,
):
    provider = _create_provider_state(lose_issue_response=True)
    first = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, first, provider)

    with pytest.raises(GitHubProjectsPartialCreateError, match="issue_response_lost"):
        first.create_issue(PROJECT, "partial", "body")

    second = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, second, provider)
    created = second.create_issue(PROJECT, "partial", "body")

    assert created.id == "GHQUAL-4"
    assert provider["issue_posts"] == 1
    journal = next((tmp_path / "ghprojects-create-intents").glob("*.json"))
    stored = json.loads(journal.read_text(encoding="utf-8"))
    assert set(stored) == {
        "adr_id", "attempt", "content_id", "fingerprint", "issue_id", "native_id",
        "schema", "state", "step",
    }
    assert "partial" not in journal.read_text(encoding="utf-8")
    assert "body" not in journal.read_text(encoding="utf-8")


def test_pat66_unknown_zero_candidate_stays_fail_closed_across_instances(
    monkeypatch, tmp_path,
):
    provider = _create_provider_state()

    def install(tracker):
        monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
        monkeypatch.setattr(tracker, "_write_catalog", lambda *_: {
            "type": type("F", (), {
                "id": "type", "data_type": "SINGLE_SELECT",
                "options": {"Task": "type-task"},
            })(),
        })
        monkeypatch.setattr(tracker, "_create_candidates", lambda *_: [])
        def lost(*_):
            provider["issue_posts"] += 1
            raise GitHubProjectsTrackerError("issue.create", "transport_failed")
        monkeypatch.setattr(tracker, "_rest_write", lost)

    first = GitHubProjectsTracker(state_dir=tmp_path)
    install(first)
    with pytest.raises(GitHubProjectsTrackerError, match="create_effect_unknown"):
        first.create_issue(PROJECT, "partial", "body")
    second = GitHubProjectsTracker(state_dir=tmp_path)
    install(second)
    with pytest.raises(GitHubProjectsTrackerError, match="create_effect_unknown"):
        second.create_issue(PROJECT, "partial", "body")
    assert provider["issue_posts"] == 1


def test_pat66_stderr_permission_hint_does_not_clear_pending_create(
    monkeypatch, tmp_path,
):
    posts = []
    raw = {
        **_rest_issue(4),
        "node_id": "issue-node-4",
        "title": "partial",
        "body": "body",
    }

    def runner(command, **kwargs):
        assert command[:5] == [
            "gh", "api", "-X", "POST",
            "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues",
        ]
        posts.append(command)
        # A non-zero gh exit can carry an Issue-shaped stdout after the server
        # applied the POST.  The incidental digits in stderr do not prove that
        # the provider rejected the request before any effect.
        return subprocess.CompletedProcess(
            command, 1, json.dumps(raw), "transport closed after 403 bytes",
        )

    def install(tracker):
        monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
        monkeypatch.setattr(tracker, "_write_catalog", lambda *_: {
            "type": type("F", (), {
                "id": "type", "data_type": "SINGLE_SELECT",
                "options": {"Task": "type-task"},
            })(),
        })
        monkeypatch.setattr(tracker, "_create_candidates", lambda *_: [])

    first = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    install(first)
    with pytest.raises(GitHubProjectsTrackerError, match="create_effect_unknown"):
        first.create_issue(PROJECT, "partial", "body")

    journal = next((tmp_path / "ghprojects-create-intents").glob("*.json"))
    assert json.loads(journal.read_text(encoding="utf-8"))["state"] == "pending"

    second = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    install(second)
    with pytest.raises(GitHubProjectsTrackerError, match="create_effect_unknown"):
        second.create_issue(PROJECT, "partial", "body")

    assert len(posts) == 1


def test_pat66_multiple_reconciliation_candidates_never_repost(monkeypatch, tmp_path):
    provider = _create_provider_state()
    one = _CreateCandidate("GHQUAL-4", 4, 1004, "issue-node-4")
    two = _CreateCandidate("GHQUAL-5", 5, 1005, "issue-node-5")
    observations = 0

    def install(tracker):
        monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
        monkeypatch.setattr(tracker, "_write_catalog", lambda *_: {
            "type": type("F", (), {
                "id": "type", "data_type": "SINGLE_SELECT",
                "options": {"Task": "type-task"},
            })(),
        })
        def candidates(*_):
            nonlocal observations
            observations += 1
            return [] if observations == 1 else [one, two]
        monkeypatch.setattr(tracker, "_create_candidates", candidates)
        def lost(*_):
            provider["issue_posts"] += 1
            raise GitHubProjectsTrackerError("issue.create", "transport_failed")
        monkeypatch.setattr(tracker, "_rest_write", lost)

    first = GitHubProjectsTracker(state_dir=tmp_path)
    install(first)
    with pytest.raises(GitHubProjectsTrackerError, match="multiple_create_candidates"):
        first.create_issue(PROJECT, "partial", "body")
    second = GitHubProjectsTracker(state_dir=tmp_path)
    install(second)
    with pytest.raises(GitHubProjectsTrackerError, match="multiple_create_candidates"):
        second.create_issue(PROJECT, "partial", "body")
    assert provider["issue_posts"] == 1


@pytest.mark.parametrize("phase", ["item", "field", "parent"])
def test_pat66_known_partial_step_has_one_bounded_resume(
    phase, monkeypatch, tmp_path,
):
    provider = _create_provider_state()
    first = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, first, provider, failing_phase=phase)
    parent = "GHQUAL-1" if phase == "parent" else None
    with pytest.raises(GitHubProjectsPartialCreateError) as exc:
        first.create_issue(PROJECT, "partial", "body", parent=parent)
    assert exc.value.partial_issue_id == "GHQUAL-4"
    assert exc.value.partial_native_issue_id == 1004

    second = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, second, provider, failing_phase=phase)
    created = second.create_issue(PROJECT, "partial", "body", parent=parent)
    assert created.id == "GHQUAL-4"
    assert provider["issue_posts"] == 1
    assert provider[f"{phase}_posts"] == 2


def test_pat66_partial_retry_exhaustion_stops_third_effect(monkeypatch, tmp_path):
    provider = _create_provider_state()

    def install(tracker):
        _install_create_harness(monkeypatch, tracker, provider)
        def always_lost(*_):
            provider["item_posts"] += 1
            raise GitHubProjectsTrackerError("project.item_create", "transport_failed")
        monkeypatch.setattr(tracker, "_add_project_item", always_lost)

    first = GitHubProjectsTracker(state_dir=tmp_path)
    install(first)
    with pytest.raises(GitHubProjectsPartialCreateError, match="partial_create:item"):
        first.create_issue(PROJECT, "partial", "body")
    second = GitHubProjectsTracker(state_dir=tmp_path)
    install(second)
    with pytest.raises(GitHubProjectsPartialCreateError, match="partial_create:item"):
        second.create_issue(PROJECT, "partial", "body")
    third = GitHubProjectsTracker(state_dir=tmp_path)
    install(third)
    with pytest.raises(GitHubProjectsTrackerError, match="partial_retry_exhausted:item"):
        third.create_issue(PROJECT, "partial", "body")
    assert provider["issue_posts"] == 1
    assert provider["item_posts"] == 2


def test_pat66_unowned_unique_candidate_is_ambiguous_without_write(monkeypatch, tmp_path):
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    candidate = _CreateCandidate("GHQUAL-4", 4, 1004, "issue-node-4")
    writes = []
    monkeypatch.setattr(tracker, "_create_candidates", lambda *_: [candidate])
    monkeypatch.setattr(tracker, "_rest_write", lambda *args: writes.append(args))
    monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
    monkeypatch.setattr(tracker, "_write_catalog", lambda *_: {
        "type": type("F", (), {
            "id": "type", "data_type": "SINGLE_SELECT",
            "options": {"Task": "type-task"},
        })(),
    })
    with pytest.raises(GitHubProjectsTrackerError, match="unowned_create_candidate"):
        tracker.create_issue(PROJECT, "partial", "body")
    assert writes == []


def test_pat66_corrupt_or_unavailable_intent_store_refuses_before_post(
    monkeypatch, tmp_path,
):
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    binding = tracker._binding(PROJECT)
    fingerprint = tracker._create_fingerprint(
        binding, "partial", "body", {"type": "Task"}, None,
    )
    tracker._create_intent_dir.mkdir(parents=True)
    tracker._intent_path(fingerprint).write_text("not-json", encoding="utf-8")
    writes = []
    monkeypatch.setattr(tracker, "_rest_write", lambda *args: writes.append(args))
    monkeypatch.setattr(tracker, "verify_project_identity", lambda *_: True)
    monkeypatch.setattr(tracker, "_write_catalog", lambda *_: {
        "type": type("F", (), {
            "id": "type", "data_type": "SINGLE_SELECT",
            "options": {"Task": "type-task"},
        })(),
    })
    with pytest.raises(GitHubProjectsTrackerError, match="invalid_local_intent"):
        tracker.create_issue(PROJECT, "partial", "body")
    assert writes == []

    blocked = tmp_path / "blocked-state"
    blocked.write_text("file", encoding="utf-8")
    unavailable = GitHubProjectsTracker(state_dir=blocked)
    monkeypatch.setattr(unavailable, "_rest_write", lambda *args: writes.append(args))
    monkeypatch.setattr(unavailable, "verify_project_identity", lambda *_: True)
    monkeypatch.setattr(unavailable, "_write_catalog", lambda *_: {
        "type": type("F", (), {
            "id": "type", "data_type": "SINGLE_SELECT",
            "options": {"Task": "type-task"},
        })(),
    })
    with pytest.raises(GitHubProjectsTrackerError, match="local_intent_unavailable"):
        unavailable.create_issue(PROJECT, "partial", "body")
    assert writes == []


def test_pat66_create_intent_lock_serializes_instances(tmp_path):
    fingerprint = "a" * 64
    first = GitHubProjectsTracker(state_dir=tmp_path)
    second = GitHubProjectsTracker(state_dir=tmp_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def hold_first():
        with first._create_intent_lock(fingerprint):
            first_entered.set()
            assert release_first.wait(timeout=2)

    def enter_second():
        assert first_entered.wait(timeout=2)
        with second._create_intent_lock(fingerprint):
            second_entered.set()

    first_thread = threading.Thread(target=hold_first)
    second_thread = threading.Thread(target=enter_second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(timeout=2)
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered.is_set()


def test_pat66_completed_replay_refuses_drift_without_rewriting(monkeypatch, tmp_path):
    provider = _create_provider_state()
    first = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, first, provider)
    first.create_issue(PROJECT, "partial", "body")
    writes = (provider["item_posts"], provider["field_posts"], provider["parent_posts"])

    replay = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, replay, provider)
    monkeypatch.setattr(
        replay, "_created_issue_readback",
        lambda _binding, candidate: Issue(
            id=candidate.issue_id, title="partial", body="body", type="Bug",
        ),
    )
    with pytest.raises(GitHubProjectsTrackerError, match="completed_create_drift"):
        replay.create_issue(PROJECT, "partial", "body")
    assert writes == (
        provider["item_posts"], provider["field_posts"], provider["parent_posts"],
    )


def test_pat66_comment_readback_requires_bound_issue_and_exact_comment(monkeypatch):
    tracker = GitHubProjectsTracker()
    monkeypatch.setattr(tracker, "_native_issue", lambda *_: (1, 1001))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda *_: ("item-1", "node-1"))
    monkeypatch.setattr(
        tracker, "_rest_write", lambda *_: {
            "id": 44, "body": "note é",
            "issue_url": "https://api.github.com/repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1",
        },
    )
    monkeypatch.setattr(tracker, "_rows", lambda *_: [{"id": 44, "body": "note é"}])

    tracker.add_comment("GHQUAL-1", "note é", project=PROJECT)

    monkeypatch.setattr(tracker, "_rows", lambda *_: [{"id": 44, "body": "other"}])
    with pytest.raises(TrackerConflictError, match="commentaire GitHub divergent"):
        tracker.add_comment("GHQUAL-1", "note é", project=PROJECT)


def test_pat66_relates_uses_distinct_issue_node_ids_and_symmetric_readback(monkeypatch):
    tracker = GitHubProjectsTracker()
    initial = _write_issue(links=[])
    observed = _write_issue(links=[Link("relates", "outward", "GHQUAL-2")])
    destination = Issue(id="GHQUAL-2", title="issue 2", priority="P1", body="body")
    observed_destination = deepcopy(destination)
    observed_destination.links = [Link("relates", "outward", "GHQUAL-1")]
    reads = iter([initial, destination, initial, destination, observed, observed_destination])
    calls = []
    monkeypatch.setattr(tracker, "_native_issue", lambda issue, *_: (1 if issue.endswith("1") else 2, 1001 if issue.endswith("1") else 1002))
    monkeypatch.setattr(tracker, "get_issue", lambda issue: next(reads))
    monkeypatch.setattr(tracker, "_item_coordinate", lambda issue, *_: ("item-1" if issue.endswith("1") else "item-2", "node-1" if issue.endswith("1") else "node-2"))
    monkeypatch.setattr(tracker, "_graphql_mutation", lambda query, variables, operation: calls.append((query, variables, operation)) or {"addRelatesTo": {"issue": {"id": "node-1"}, "relatedIssue": {"id": "node-2"}}})

    tracker.link("GHQUAL-1", "relates", "GHQUAL-2", project=PROJECT)

    assert len(calls) == 1
    assert calls[0][1] == {"issue": "node-1", "related": "node-2"}


@pytest.mark.parametrize("reason", ["authentication_failed", "permission_denied", "rate_limited"])
def test_pat66_transport_write_failures_remain_explicit_and_unretried(reason):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        status = 429 if reason == "rate_limited" else (401 if reason == "authentication_failed" else 403)
        return subprocess.CompletedProcess(command, 1, json.dumps({"status": status, "message": reason}), reason)
    tracker = GitHubProjectsTracker(runner=runner)
    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        tracker._rest_write("POST", "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/comments", {"body": "x"}, "issue.comment")
    assert len(calls) == 1


@pytest.mark.parametrize("labels", ["missing", None, {}, [{}], ["foundry:adr"],
                                   [{"name": ""}], [{"name": "x"}, {"name": "x"}]])
def test_get_issue_refuses_ambiguous_rest_labels_before_other_reads(labels):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[2] == "graphql":
            payload = _page("item-1", 1, None, False)
        else:
            assert command[-1].endswith("/issues/1")
            payload = _rest_issue(1)
            if labels == "missing":
                payload.pop("labels")
            else:
                payload["labels"] = labels
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="invalid_labels"):
        GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")
    assert len(calls) == 2


def test_rest_adr_discrimination_remains_explicit_after_graphql_read():
    def runner(command, **kwargs):
        if command[2] == "graphql":
            payload = _page("item-1", 1, None, False)
        else:
            assert command[-1].endswith("/issues/1")
            payload = _rest_issue(1)
            payload["labels"] = [{"name": "foundry:adr"}, {"name": "preserve"}]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    from foundry.trackers.base import TrackerCapabilityUnavailableError

    with pytest.raises(TrackerCapabilityUnavailableError, match="adr_issue_read"):
        GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")


def _adr_metadata(*, sequence=0, previous_comment_id=None, previous_sha256=None):
    import hashlib
    body = "source UTF-8 é"
    return {
        "schema": "foundry-ghprojects-adr.v1", "project_id": PROJECT.id,
        "repository": "patobiskoto/foundry-v1-ghprojects-sandbox",
        "id": "GHQUAL-ADR-0001", "title": "Décision", "status": "proposed",
        "sequence": sequence, "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "previous_comment_id": previous_comment_id, "previous_sha256": previous_sha256,
        "relations": {"issues": [], "supersedes": [], "superseded_by": None},
    }, body


def test_pat58_snapshot_verifies_current_utf8_source_and_complete_chain(monkeypatch):
    tracker = GitHubProjectsTracker()
    metadata, body = _adr_metadata()
    comment = {"id": 123, "body": tracker._adr_comment(metadata, body)}
    issue = _rest_issue(1)
    issue.update({"title": tracker._adr_head_title(
                      "GHQUAL-ADR-0001", "Décision", metadata, body, 123,
                  ), "body": body,
                  "labels": [{"name": "foundry:adr"}]})
    monkeypatch.setattr(tracker, "_adr_supports", lambda project: [("GHQUAL-ADR-0001", "Décision", 1)])
    monkeypatch.setattr(tracker, "_rows", lambda path, operation: [comment])
    monkeypatch.setattr(tracker, "_rest", lambda path, operation: issue)

    assert tracker.list_adrs(PROJECT) == [
        Adr("GHQUAL-ADR-0001", "Décision", "proposed", body, "1")
    ]


@pytest.mark.parametrize("tamper", ["source", "digest", "missing-comment"])
def test_pat58_snapshot_refuses_tampered_or_missing_history(monkeypatch, tamper):
    tracker = GitHubProjectsTracker()
    metadata, body = _adr_metadata()
    if tamper == "digest":
        metadata["body_sha256"] = "0" * 64
    comment = {"id": 123, "body": tracker._adr_comment(metadata, body)}
    issue = _rest_issue(1)
    issue.update({"title": tracker._adr_head_title(
                      "GHQUAL-ADR-0001", "Décision", metadata, body, 123,
                  ), "body": "changed" if tamper == "source" else body,
                  "labels": [{"name": "foundry:adr"}]})
    monkeypatch.setattr(tracker, "_adr_supports", lambda project: [("GHQUAL-ADR-0001", "Décision", 1)])
    monkeypatch.setattr(tracker, "_rows", lambda path, operation: [] if tamper == "missing-comment" else [comment])
    monkeypatch.setattr(tracker, "_rest", lambda path, operation: issue)

    with pytest.raises(TrackerConflictError):
        tracker.list_adrs(PROJECT)


class _AdrCycleTransport:
    """A raw gh transport for the public ADR lifecycle and its replay boundary."""

    repo = "patobiskoto/foundry-v1-ghprojects-sandbox"

    def __init__(self):
        self.issues = {1: {**_rest_issue(1), "title": "delivery", "node_id": "node-1"}}
        self.comments, self.project_numbers, self.next_number, self.next_comment = {}, {1}, 2, 100
        self.patches = []

    def _issue(self, number):
        return deepcopy(self.issues[number])

    def _project(self):
        page = _page("item-1", 1, None, False)
        nodes = []
        for number in sorted(self.project_numbers):
            issue = self.issues[number]
            row = deepcopy(page["data"]["user"]["projectV2"]["items"]["nodes"][0])
            row["id"] = f"item-{number}"
            content = row["content"]
            content.update({"id": issue["node_id"], "number": number,
                            "title": issue["title"], "body": issue["body"],
                            "labels": {"nodes": deepcopy(issue["labels"]),
                                       "pageInfo": {"hasNextPage": False, "endCursor": None}}})
            nodes.append(row)
        page["data"]["user"]["projectV2"]["items"]["nodes"] = nodes
        return page

    def __call__(self, command, **kwargs):
        if command[2] == "graphql":
            query = next(value for value in command if value.startswith("query="))
            if "repositories(first:100" in query:
                payload = {"data": {"user": {"projectV2": {
                    "id": PROJECT.id, "number": 7, "public": False,
                    "repositories": {"nodes": [{"id": "repo-node", "nameWithOwner": self.repo,
                        "isPrivate": True, "owner": {"__typename": "User", "login": "patobiskoto"}}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None}},
                }}}}
            elif "addProjectV2ItemById" in query:
                content = next(value.removeprefix("content=") for value in command if value.startswith("content="))
                number = next(number for number, issue in self.issues.items() if issue["node_id"] == content)
                self.project_numbers.add(number)
                payload = {"data": {"addProjectV2ItemById": {"item": {"id": f"item-{number}"}}}}
            else:
                payload = self._project()
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        method, path = command[command.index("-X") + 1], command[command.index("-X") + 2]
        if method == "POST" and path.endswith("/issues"):
            title = next(value.removeprefix("title=") for value in command if value.startswith("title="))
            body = next(value.removeprefix("body=") for value in command if value.startswith("body="))
            number = self.next_number
            self.next_number += 1
            issue = {**_rest_issue(number), "title": title, "body": body,
                     "node_id": f"node-{number}"}
            self.issues[number] = issue
            return subprocess.CompletedProcess(command, 0, json.dumps(issue), "")
        if method == "POST" and path.endswith("/labels"):
            number = int(path.split("/")[-2])
            self.issues[number]["labels"].append({"name": "foundry:adr"})
            return subprocess.CompletedProcess(command, 0, json.dumps(self.issues[number]["labels"]), "")
        if method == "PATCH" and "/issues/" in path:
            number = int(path.split("/")[-1])
            for value in command:
                if value.startswith("body="):
                    self.issues[number]["body"] = value.removeprefix("body=")
                    self.patches.append(number)
                elif value.startswith("title="):
                    self.issues[number]["title"] = value.removeprefix("title=")
            return subprocess.CompletedProcess(command, 0, json.dumps(self._issue(number)), "")
        if method == "POST" and path.endswith("/comments"):
            number = int(path.split("/")[-2])
            body = next(value.removeprefix("body=") for value in command if value.startswith("body="))
            self.next_comment += 1
            comment = {"id": self.next_comment, "body": body}
            self.comments.setdefault(number, []).append(comment)
            return subprocess.CompletedProcess(command, 0, json.dumps(comment), "")
        if "/comments" in path:
            number = int(path.split("/issues/")[1].split("/")[0])
            return subprocess.CompletedProcess(command, 0, json.dumps(self.comments.get(number, [])), "")
        if "issues?state=all" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([self._issue(n) for n in self.issues]), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(command, 1, json.dumps({"message": "No parent issue found", "status": "404"}), "404")
        if "/issues/" in path:
            number = int(path.split("/issues/")[1].split("?")[0].split("/")[0])
            return subprocess.CompletedProcess(command, 0, json.dumps(self._issue(number)), "")
        return subprocess.CompletedProcess(command, 0, "[]", "")


def test_pat58_public_raw_transport_full_cycle_and_completed_replay(tmp_path):
    provider = _AdrCycleTransport()
    first = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    first_adr = first.create_adr(PROJECT, "Décision", "source UTF-8 é")
    # A fresh process reuses only its durable coordinate and must add no support.
    replay = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    assert replay.create_adr(PROJECT, "Décision", "source UTF-8 é") == first_adr
    second_adr = replay.create_adr(PROJECT, "Remplacement", "second source")
    replay.set_adr_status(first_adr, "accepted", PROJECT)
    accepted_first = replay.adr_for_mutation(PROJECT, first_adr.id)
    assert accepted_first is not None
    assert replay.update_body(accepted_first, "source UTF-8 é", "source v2 UTF-8 é", PROJECT)
    edited_first = replay.adr_for_mutation(PROJECT, first_adr.id)
    assert edited_first is not None
    replay.link_adr_issue(edited_first, "GHQUAL-1", PROJECT)
    replay.set_adr_status(second_adr, "accepted", PROJECT)
    replay.supersede_adr(replay.adr_for_mutation(PROJECT, first_adr.id), second_adr.id, PROJECT)
    current_second = replay.adr_for_mutation(PROJECT, second_adr.id)
    assert current_second is not None
    replay.set_adr_status(current_second, "deprecated", PROJECT)
    adrs = {adr.id: adr for adr in replay.list_adrs(PROJECT)}
    assert adrs[first_adr.id].status == "superseded"
    assert adrs[second_adr.id].status == "deprecated"
    # Only the source edit patches an Issue body; statuses and relations append history.
    assert provider.patches == [int(first_adr.ref)]


def test_pat58_unobserved_create_effect_is_never_posted_twice(tmp_path):
    provider = _AdrCycleTransport()
    posts = 0

    def runner(command, **kwargs):
        nonlocal posts
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if method == "POST" and path.endswith("/issues"):
                posts += 1
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
        return provider(command, **kwargs)

    for _ in range(2):
        with pytest.raises(GitHubProjectsTrackerError, match="create_effect_unknown"):
            GitHubProjectsTracker(runner=runner, state_dir=tmp_path).create_adr(
                PROJECT, "Perdue", "source",
            )
    assert posts == 1


@pytest.mark.parametrize("copies", [1, 2])
def test_pat58_lost_create_response_reconciles_only_one_exact_candidate(
    tmp_path, copies,
):
    provider = _AdrCycleTransport()
    posts = 0

    def runner(command, **kwargs):
        nonlocal posts
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if method == "POST" and path.endswith("/issues"):
                posts += 1
                for _ in range(copies):
                    provider(command, **kwargs)
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
        return provider(command, **kwargs)

    tracker = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    if copies == 1:
        assert tracker.create_adr(PROJECT, "Perdue", "source").id == "GHQUAL-ADR-0002"
    else:
        with pytest.raises(TrackerConflictError, match="ambiguous"):
            tracker.create_adr(PROJECT, "Perdue", "source")
    assert posts == 1


def test_pat58_old_pat66_intent_without_adr_id_remains_readable(tmp_path):
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    fingerprint = "a" * 64
    old = tracker._intent_record(fingerprint, "pending")
    old.pop("adr_id")
    tracker._create_intent_dir.mkdir(parents=True)
    tracker._intent_path(fingerprint).write_text(json.dumps(old), encoding="utf-8")

    assert tracker._read_create_intent(fingerprint) == old


def test_pat58_initial_version_lost_response_resumes_by_observation_only(tmp_path):
    provider = _AdrCycleTransport()
    comment_posts = 0
    interrupt_readback = False

    def runner(command, **kwargs):
        nonlocal comment_posts, interrupt_readback
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if method == "POST" and path.endswith("/comments"):
                comment_posts += 1
                provider(command, **kwargs)
                interrupt_readback = True
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
            if method == "GET" and "/comments" in path and interrupt_readback:
                interrupt_readback = False
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
        return provider(command, **kwargs)

    first = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    with pytest.raises(GitHubProjectsTrackerError, match="transport_failed"):
        first.create_adr(PROJECT, "Interrompue", "source")
    replay = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    assert replay.create_adr(PROJECT, "Interrompue", "source").id == "GHQUAL-ADR-0002"
    assert comment_posts == 1
    assert len(provider.comments[2]) == 1


def test_pat58_unobserved_initial_version_is_not_reposted(tmp_path):
    provider = _AdrCycleTransport()
    comment_posts = 0

    def runner(command, **kwargs):
        nonlocal comment_posts
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if method == "POST" and path.endswith("/comments"):
                comment_posts += 1
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
        return provider(command, **kwargs)

    for _ in range(2):
        with pytest.raises(GitHubProjectsTrackerError, match="adr:version_effect_unknown"):
            GitHubProjectsTracker(runner=runner, state_dir=tmp_path).create_adr(
                PROJECT, "Interrompue", "source",
            )
    assert comment_posts == 1


def test_pat58_status_write_refuses_stale_source_snapshot_before_comment(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    stale = tracker.create_adr(PROJECT, "Décision", "source")
    tracker.set_adr_status(stale, "accepted", PROJECT)
    accepted = tracker.adr_for_mutation(PROJECT, stale.id)
    assert accepted is not None
    tracker.update_body(accepted, "source", "source v2", PROJECT)
    before = provider.next_comment

    with pytest.raises(TrackerConflictError, match="stale"):
        tracker.set_adr_status(stale, "deprecated", PROJECT)
    assert provider.next_comment == before


@pytest.mark.parametrize("effect", ["none", "one", "two"])
def test_pat58_lost_status_comment_never_creates_a_second_version(tmp_path, effect):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    posts = 0

    def runner(command, **kwargs):
        nonlocal posts
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if method == "POST" and path.endswith("/comments"):
                posts += 1
                for _ in range({"none": 0, "one": 1, "two": 2}[effect]):
                    provider(command, **kwargs)
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
        return provider(command, **kwargs)

    interrupted = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    if effect == "one":
        interrupted.set_adr_status(adr, "accepted", PROJECT)
        assert interrupted.adr_for_mutation(PROJECT, adr.id).status == "accepted"
    else:
        with pytest.raises((GitHubProjectsTrackerError, TrackerConflictError)):
            interrupted.set_adr_status(adr, "accepted", PROJECT)
        with pytest.raises((GitHubProjectsTrackerError, TrackerConflictError)):
            GitHubProjectsTracker(runner=runner, state_dir=tmp_path).set_adr_status(
                adr, "accepted", PROJECT,
            )
    assert posts == 1


def test_pat58_interrupted_head_commitment_resumes_without_second_comment(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    fail_head = True

    def runner(command, **kwargs):
        nonlocal fail_head
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if (
                fail_head
                and method == "PATCH"
                and "/issues/" in path
                and any(value.startswith("title=") for value in command)
            ):
                fail_head = False
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
        return provider(command, **kwargs)

    with pytest.raises(GitHubProjectsTrackerError, match="transport_failed"):
        GitHubProjectsTracker(runner=runner, state_dir=tmp_path).set_adr_status(
            adr, "accepted", PROJECT,
        )

    GitHubProjectsTracker(runner=provider, state_dir=tmp_path).set_adr_status(
        adr, "accepted", PROJECT,
    )
    assert len(provider.comments[int(adr.ref)]) == 2
    assert tracker.adr_for_mutation(PROJECT, adr.id).status == "accepted"


def test_pat58_chain_digest_detects_predecessor_metadata_tamper(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source UTF-8 é")
    tracker.set_adr_status(adr, "accepted", PROJECT)
    first = provider.comments[int(adr.ref)][0]
    encoded, source = first["body"][len("<!-- foundry-ghprojects-adr.v1\n"):].split(
        "\n-->\n\n", 1,
    )
    metadata = json.loads(encoded)
    metadata["relations"]["issues"] = ["GHQUAL-1"]
    first["body"] = tracker._adr_comment(metadata, source)

    with pytest.raises(TrackerConflictError, match="predecessor"):
        tracker.list_adrs(PROJECT)


def test_pat58_head_commitment_detects_deleted_last_version(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    tracker.set_adr_status(adr, "accepted", PROJECT)

    provider.comments[int(adr.ref)].pop()

    with pytest.raises(TrackerConflictError, match="head commitment divergent"):
        tracker.list_adrs(PROJECT)


def test_pat58_head_commitment_detects_legal_latest_metadata_edit(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    latest = provider.comments[int(adr.ref)][-1]
    encoded, source = latest["body"][len("<!-- foundry-ghprojects-adr.v1\n"):].split(
        "\n-->\n\n", 1,
    )
    metadata = json.loads(encoded)
    metadata["relations"]["issues"] = ["GHQUAL-1"]
    latest["body"] = tracker._adr_comment(metadata, source)

    with pytest.raises(TrackerConflictError, match="head commitment divergent"):
        tracker.list_adrs(PROJECT)


def test_pat58_legacy_support_without_head_commitment_requires_migration(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    provider.issues[int(adr.ref)]["title"] = f"[{adr.id}] {adr.title}"

    with pytest.raises(TrackerConflictError, match="legacy support requires migration"):
        tracker.list_adrs(PROJECT)


@pytest.mark.parametrize("failure", ["removed", "deleted", "inaccessible"])
def test_pat58_read_classifies_unavailable_linked_issue(tmp_path, failure):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    tracker.link_adr_issue(adr, "GHQUAL-1", PROJECT)

    if failure == "removed":
        provider.project_numbers.remove(1)

    def runner(command, **kwargs):
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if method == "GET" and path == f"repos/{provider.repo}/issues/1":
                if failure == "deleted":
                    return subprocess.CompletedProcess(
                        command, 1, json.dumps({"message": "Not Found", "status": "404"}), "404",
                    )
                if failure == "inaccessible":
                    return subprocess.CompletedProcess(
                        command, 1,
                        json.dumps({"message": "Resource not accessible", "status": "403"}),
                        "403",
                    )
        return provider(command, **kwargs)

    with pytest.raises(AdrIssueUnavailableError, match=f"{adr.id} -> GHQUAL-1"):
        GitHubProjectsTracker(runner=runner, state_dir=tmp_path).list_adrs(PROJECT)


def test_pat58_frame_materializes_native_adr_issue_relation(tmp_path, monkeypatch):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    monkeypatch.setattr(foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)
    monkeypatch.setattr(
        tracker, "create_issue",
        lambda *_args, **_kwargs: tracker._search_raw(PROJECT)[0],
    )

    created = frame.materialize({
        "adrs": [{"title": "Frame decision", "body": "Decision"}],
        "issues": [{
            "title": "Implement decision", "body": "- [ ] Done",
            "constrained_by": [0],
        }],
    })

    adr_id = created["adrs"][0]
    assert created["issues"] == ["GHQUAL-1"]
    _binding, snapshot = tracker._adr_snapshot(PROJECT)
    assert snapshot[adr_id][1]["relations"]["issues"] == ["GHQUAL-1"]


def test_pat58_distinct_concurrent_requests_allocate_distinct_ids(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    provider = _AdrCycleTransport()

    def create(index):
        return GitHubProjectsTracker(runner=provider, state_dir=tmp_path).create_adr(
            PROJECT, f"Décision {index}", f"source {index}",
        ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = set(pool.map(create, (1, 2)))
    assert ids == {"GHQUAL-ADR-0002", "GHQUAL-ADR-0003"}


def test_pat58_completed_replay_rejects_native_coordinate_drift(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    provider.issues[int(adr.ref)]["node_id"] = "foreign-node"

    with pytest.raises((GitHubProjectsTrackerError, TrackerConflictError)):
        GitHubProjectsTracker(runner=provider, state_dir=tmp_path).create_adr(
            PROJECT, "Décision", "source",
        )


def test_pat58_read_detects_removed_reserved_support_label(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    provider.issues[int(adr.ref)]["labels"] = []

    with pytest.raises(TrackerConflictError, match="reserved support label missing"):
        tracker.list_adrs(PROJECT)


def test_pat58_read_detects_support_removed_from_bound_project(tmp_path):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    provider.project_numbers.remove(int(adr.ref))

    with pytest.raises(TrackerConflictError, match="missing from bound Project"):
        tracker.list_adrs(PROJECT)


@pytest.mark.parametrize(
    ("failure", "reason"),
    [("deleted", "not_found"), ("inaccessible", "permission_denied")],
)
def test_pat58_read_surfaces_unavailable_support_with_project_footprint(
    tmp_path, failure, reason,
):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    adr = tracker.create_adr(PROJECT, "Décision", "source")
    support_number = int(adr.ref)

    def runner(command, **kwargs):
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if method == "GET" and "issues?state=all" in path and failure == "deleted":
                visible = [
                    provider._issue(number)
                    for number in provider.issues
                    if number != support_number
                ]
                return subprocess.CompletedProcess(command, 0, json.dumps(visible), "")
            if method == "GET" and path == f"repos/{provider.repo}/issues/{support_number}":
                status = "404" if failure == "deleted" else "403"
                message = "Not Found" if failure == "deleted" else "Resource not accessible"
                return subprocess.CompletedProcess(
                    command, 1, json.dumps({"message": message, "status": status}), status,
                )
        return provider(command, **kwargs)

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner, state_dir=tmp_path).list_adrs(PROJECT)


def test_pat58_unrelated_repository_issue_with_adr_like_title_is_not_authority(
    tmp_path,
):
    provider = _AdrCycleTransport()
    provider.issues[2] = {
        **_rest_issue(2),
        "title": "[GHQUAL-ADR-9999] ordinary delivery issue",
        "node_id": "node-2",
    }
    provider.next_number = 3

    assert GitHubProjectsTracker(
        runner=provider, state_dir=tmp_path,
    ).list_adrs(PROJECT) == []


def test_pat58_first_support_on_native_issue_eight_uses_adr_eight(tmp_path):
    provider = _AdrCycleTransport()
    for number in range(2, 8):
        provider.issues[number] = {
            **_rest_issue(number),
            "title": f"ordinary issue {number}",
            "node_id": f"node-{number}",
        }
    provider.next_number = 8

    adr = GitHubProjectsTracker(
        runner=provider, state_dir=tmp_path,
    ).create_adr(PROJECT, "Décision", "source")

    assert adr.id == "GHQUAL-ADR-0008"
    assert adr.ref == "8"


def test_pat58_allocation_does_not_reuse_id_after_unobservable_whole_deletion(
    tmp_path,
):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    deleted = tracker.create_adr(PROJECT, "Décision supprimée", "source supprimée")
    deleted_number = int(deleted.ref)
    provider.project_numbers.remove(deleted_number)
    del provider.issues[deleted_number]
    provider.comments.pop(deleted_number)

    replacement = GitHubProjectsTracker(
        runner=provider, state_dir=tmp_path / "fresh-machine",
    ).create_adr(PROJECT, "Décision suivante", "nouvelle source")

    assert deleted.id == "GHQUAL-ADR-0002"
    assert replacement.id == "GHQUAL-ADR-0003"
    assert replacement.ref == "3"


def test_pat58_two_visible_provisional_candidates_keep_distinct_native_ids(
    tmp_path,
):
    provider = _AdrCycleTransport()
    for number, fingerprint in ((2, "a" * 64), (3, "b" * 64)):
        provider.issues[number] = {
            **_rest_issue(number),
            "title": f"[foundry-adr-create:v1:{fingerprint}]",
            "body": f"source {number}",
            "node_id": f"node-{number}",
        }
    provider.next_number = 4
    first_machine = GitHubProjectsTracker(
        runner=provider, state_dir=tmp_path / "first",
    )
    second_machine = GitHubProjectsTracker(
        runner=provider, state_dir=tmp_path / "second",
    )
    binding = first_machine._binding(PROJECT)

    assert {
        first_machine._stable_adr_id(binding, 2, {}),
        second_machine._stable_adr_id(binding, 3, {}),
    } == {"GHQUAL-ADR-0002", "GHQUAL-ADR-0003"}


def test_pat58_visible_id_space_exhaustion_refuses_before_issue_effect(
    tmp_path, monkeypatch,
):
    provider = _AdrCycleTransport()
    tracker = GitHubProjectsTracker(runner=provider, state_dir=tmp_path)
    exhausted = Adr(
        "GHQUAL-ADR-9999", "Dernière", "accepted", "source", "9999",
    )
    monkeypatch.setattr(
        tracker,
        "_adr_snapshot",
        lambda _project: (tracker._binding(PROJECT), {
            exhausted.id: (exhausted, {}, 1),
        }),
    )

    with pytest.raises(GitHubProjectsTrackerError, match="adr_id_space_exhausted"):
        tracker.create_adr(PROJECT, "Impossible", "source")

    assert provider.next_number == 2
    assert not list((tmp_path / "ghprojects-create-intents").glob("*.json"))


@pytest.mark.parametrize("effect", ["none", "applied"])
def test_pat58_identity_write_response_loss_is_observed_without_repost(
    tmp_path, effect,
):
    provider = _AdrCycleTransport()
    identity_writes = 0

    def runner(command, **kwargs):
        nonlocal identity_writes
        if command[2] != "graphql":
            method, path = command[command.index("-X") + 1:command.index("-X") + 3]
            if (
                method == "PATCH"
                and "/issues/" in path
                and any(value.startswith("title=[GHQUAL-ADR-") for value in command)
                and provider.issues[int(path.rsplit("/", 1)[1])]["title"].startswith(
                    "[foundry-adr-create:v1:"
                )
            ):
                identity_writes += 1
                if effect == "applied":
                    provider(command, **kwargs)
                return subprocess.CompletedProcess(command, 1, "", "transport failed")
        return provider(command, **kwargs)

    tracker = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    if effect == "applied":
        adr = tracker.create_adr(PROJECT, "Décision", "source")
        assert GitHubProjectsTracker(
            runner=runner, state_dir=tmp_path,
        ).create_adr(PROJECT, "Décision", "source") == adr
    else:
        for _ in range(2):
            with pytest.raises(
                GitHubProjectsTrackerError, match="adr:identity_effect_unknown",
            ):
                tracker.create_adr(PROJECT, "Décision", "source")
    assert identity_writes == 1


@pytest.mark.parametrize("value", ["2026-09-27", "2026-09-27T12:00:00",
                                   "2026-09-27 12:00:00"])
def test_timestamp_refuses_dates_and_times_without_explicit_timezone(value):
    with pytest.raises(GitHubProjectsTrackerError, match="invalid_timestamp"):
        GitHubProjectsTracker._timestamp(value, "issue.read")


@pytest.mark.parametrize("value", ["2026-09-27T12:00:00Z",
                                   "2026-09-27T14:00:00+02:00",
                                   "2026-09-27T07:00:00-05:00"])
def test_timestamp_normalizes_explicit_offsets_to_same_epoch(value):
    assert GitHubProjectsTracker._timestamp(value, "issue.read") == 1790510400000


@pytest.mark.parametrize("read", ["search", "get_issue"])
def test_pat67_public_reads_do_not_trust_native_acceptance_checkboxes(read):
    body = (
        "## Critères d'acceptation\n\n"
        "- [x] lower-case done\n"
        "* [X] upper-case done\n"
        "+ [ ] still open\n"
    )

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(
                command, 0, json.dumps(_page("item-1", 1, None, False)), "",
            )
        path = command[-1]
        if path.endswith("/issues/1"):
            raw = _rest_issue(1)
            raw["body"] = body
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    tracker = GitHubProjectsTracker(runner=runner)
    issue = tracker.search(PROJECT)[0] if read == "search" else tracker.get_issue("GHQUAL-1")

    assert issue.body == body
    assert (issue.ac_done, issue.ac_total) == (0, 3)


def test_pat67_search_rejects_native_auto_close_without_owned_receipt(tmp_path):
    transport = _LifecycleTransport()
    transport.state = "done"

    with pytest.raises(TrackerConflictError, match="outside lifecycle"):
        GitHubProjectsTracker(runner=transport, state_dir=tmp_path).search(PROJECT)


def test_get_issue_ignores_comments_and_split_line_pseudo_checkboxes():
    body = "No acceptance criteria here.\n- [x]\nnot one criterion\n-\n [ ] neither is this\n"

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(
                command, 0, json.dumps(_page("item-1", 1, None, False)), "",
            )
        path = command[-1]
        if path.endswith("/issues/1"):
            raw = _rest_issue(1)
            raw["body"] = body
            return subprocess.CompletedProcess(command, 0, json.dumps(raw), "")
        if "/comments" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                "id": 88,
                "body": "- [X] a comment checkbox is not issue acceptance progress",
                "created_at": "2026-09-27T12:02:00Z",
            }]), "")
        if path.endswith("/parent"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "No parent issue found", "status": "404"}),
                "gh: No parent issue found (HTTP 404)",
            )
        return subprocess.CompletedProcess(command, 0, "[]", "")

    issue = GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")

    assert issue.body == body
    assert (issue.ac_done, issue.ac_total) == (0, 0)
    assert issue.comments[0]["text"].startswith("- [X]")


def test_search_refuses_duplicate_cursor_item_and_graphql_partial_data():
    replies = [_page("item-1", 1, "next", True), _page("item-1", 1, None, False)]

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    with pytest.raises(GitHubProjectsTrackerError, match="pagination_stalled"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)

    def partial(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps({"data": {}, "errors": [{"message": "denied"}]}), "")

    with pytest.raises(GitHubProjectsTrackerError, match="ambiguous_graphql_response"):
        GitHubProjectsTracker(runner=partial).search(PROJECT)


def test_search_refuses_repeated_cursor_even_when_items_are_distinct():
    replies = [_page("item-1", 1, "same", True), _page("item-2", 2, "same", True)]

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    with pytest.raises(GitHubProjectsTrackerError, match="pagination_stalled"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


def test_search_refuses_missing_or_ambiguous_qualified_field():
    payload = _page("item-1", 1, None, False)
    payload["data"]["user"]["projectV2"]["fields"]["nodes"].append(
        {"id": "another-state", "name": "Foundry normalized state"}
    )

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="missing_or_ambiguous_field:state"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("coordinate", ["field", "option"])
def test_search_refuses_empty_authority_ids_even_when_values_match(coordinate):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    state_field = next(field for field in project["fields"]["nodes"] if field["id"] == "state")
    state_value = project["items"]["nodes"][0]["fieldValues"]["nodes"][0]
    if coordinate == "field":
        state_field["id"] = ""
        state_value["field"]["id"] = ""
        reason = "missing_or_ambiguous_field:state"
    else:
        next(option for option in state_field["options"] if option["name"] == "ready")["id"] = ""
        state_value["optionId"] = ""
        reason = "invalid_field_options:state"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize("semantic", ["state", "priority", "estimate"])
def test_search_refuses_qualified_optional_field_value_without_id(semantic):
    payload = _page("item-1", 1, None, False)
    values = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"]
    next(value for value in values if value["field"]["id"] == semantic)["field"].pop("id")

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="incoherent_field_value"):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize("malformation", ["null_node", "null_field", "empty_field_id"])
def test_search_refuses_malformed_qualified_field_value_coordinates(malformation):
    payload = _page("item-1", 1, None, False)
    values = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"]
    priority_index = next(
        index for index, value in enumerate(values) if value["field"]["id"] == "priority"
    )
    if malformation == "null_node":
        values[priority_index] = None
    elif malformation == "null_field":
        values[priority_index]["field"] = None
    else:
        values[priority_index]["field"]["id"] = ""

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match="incoherent_field_value"):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize(
    "failure", ["missing_field", "missing_value", "missing_option", "duplicate_option"],
)
def test_search_refuses_incomplete_or_ambiguous_field_contract(failure):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if failure == "missing_field":
        project["fields"]["nodes"] = [
            field for field in project["fields"]["nodes"] if field["id"] != "type"
        ]
        reason = "missing_or_ambiguous_field:type"
    elif failure == "missing_value":
        project["items"]["nodes"][0]["fieldValues"]["nodes"] = [
            value for value in project["items"]["nodes"][0]["fieldValues"]["nodes"]
            if value["field"]["id"] != "type"
        ]
        reason = "invalid_type"
    elif failure == "missing_option":
        state_field = next(field for field in project["fields"]["nodes"] if field["id"] == "state")
        state_field["options"] = [
            option for option in state_field["options"] if option["name"] != "blocked"
        ]
        reason = "invalid_field_options:state"
    else:
        type_field = next(field for field in project["fields"]["nodes"] if field["id"] == "type")
        type_field["options"].append({"id": "type-task", "name": "Bug"})
        reason = "invalid_field_options:type"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("coordinate", ["field_name", "option_id"])
def test_search_refuses_incoherent_field_value_coordinates(coordinate):
    payload = _page("item-1", 1, None, False)
    value = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"][0]
    if coordinate == "field_name":
        value["field"]["name"] = "Foundry type"
        reason = "incoherent_field_value"
    else:
        value["optionId"] = "state-other"
        reason = "invalid_option_value:state"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"type": "DRAFT_ISSUE", "content": {"__typename": "DraftIssue"}}, "unsupported_item_type"),
        ({"type": "PULL_REQUEST", "content": {"__typename": "PullRequest"}}, "unsupported_item_type"),
        ({"project.public": True}, "foreign_or_unqualified_project"),
        ({"content.repository.nameWithOwner": "patobiskoto/foreign"}, "foreign_or_unqualified_repository"),
        ({"content.repository.isPrivate": False}, "foreign_or_unqualified_repository"),
        ({"content.repository.owner.__typename": "Organization"}, "foreign_or_unqualified_repository"),
    ],
)
def test_search_refuses_unsupported_or_unqualified_project_items(mutation, reason):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    item = project["items"]["nodes"][0]
    for path, value in mutation.items():
        parts = path.split(".")
        target = project if parts[0] == "project" else item
        if parts[0] == "project":
            parts = parts[1:]
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("duplicate", "reason"),
    [
        ("number", "duplicate_issue_id"),
        ("content", "duplicate_issue_content_id"),
    ],
)
def test_search_refuses_duplicate_issue_coordinates(duplicate, reason):
    payload = _page("item-1", 1, None, False)
    first = payload["data"]["user"]["projectV2"]["items"]["nodes"][0]
    second = deepcopy(first)
    second["id"] = "item-2"
    if duplicate == "number":
        second["content"]["id"] = "issue-node-other"
    else:
        second["content"]["number"] = 2
    payload["data"]["user"]["projectV2"]["items"]["nodes"].append(second)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("collision", ["project_issue", "field_option"])
def test_search_refuses_conflated_native_identifier_namespaces(collision):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if collision == "project_issue":
        project["items"]["nodes"][0]["content"]["id"] = "PVT_project"
        reason = "conflated_native_ids"
    else:
        project["fields"]["nodes"][0]["options"][0]["id"] = "type"
        reason = "missing_or_ambiguous_field:type"

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


def test_search_refuses_field_catalog_drift_between_pages():
    first = _page("item-1", 1, "next", True)
    second = _page("item-2", 2, None, False)
    second["data"]["user"]["projectV2"]["fields"]["nodes"][0]["options"][0]["id"] = "state-ready-v2"
    second["data"]["user"]["projectV2"]["items"]["nodes"][0]["fieldValues"]["nodes"][0]["optionId"] = "state-ready-v2"
    replies = [first, second]

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(replies.pop(0)), "")

    with pytest.raises(GitHubProjectsTrackerError, match="ambiguous_field_catalog"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("coordinate", ["key", "project_id", "owner", "number", "repository"])
def test_search_refuses_foreign_input_binding_before_transport(coordinate):
    foreign = deepcopy(PROJECT)
    if coordinate == "key":
        foreign.key = "OTHER"
    elif coordinate == "project_id":
        foreign.id = "PVT_foreign"
    elif coordinate == "owner":
        foreign.extra["owner"] = "foreign"
        foreign.extra["canonical_repo"] = "github.com/foreign/foundry-v1-ghprojects-sandbox"
    elif coordinate == "number":
        foreign.extra["number"] = "8"
    else:
        foreign.extra["canonical_repo"] = "github.com/patobiskoto/foreign"

    def runner(command, **kwargs):
        pytest.fail(f"foreign binding reached transport: {command}")

    with pytest.raises(GitHubProjectsTrackerError, match="foreign_project_binding"):
        GitHubProjectsTracker(runner=runner).search(foreign)


def test_search_refuses_foreign_active_provider_before_transport(monkeypatch):
    monkeypatch.setattr(
        "foundry.trackers.ghprojects.registry.repository_tracker_selection",
        lambda: {"tracker": "linear", "mode": "legacy", "project": PROJECT},
    )

    def runner(command, **kwargs):
        pytest.fail(f"foreign provider reached transport: {command}")

    with pytest.raises(GitHubProjectsTrackerError, match="foreign_tracker"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("stderr", "body", "reason"),
    [
        ("gh: HTTP 401", {}, "authentication_failed"),
        ("gh: HTTP 403", {}, "permission_denied"),
        ("", {"status": "429", "message": "Too many requests"}, "rate_limited"),
    ],
)
def test_search_classifies_bounded_transport_failures(stderr, body, reason):
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, json.dumps(body), stderr)

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


def test_search_classifies_transport_launch_failure():
    def runner(command, **kwargs):
        raise OSError("synthetic transport launch failure")

    with pytest.raises(GitHubProjectsTrackerError, match="transport_failed"):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize("connection, reason", [
    ("fields", "truncated_fields"),
    ("labels", "truncated_or_invalid_labels"),
    ("fieldValues", "truncated_field_values"),
])
def test_search_refuses_truncated_authority_connections(connection, reason):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if connection == "fields":
        project[connection]["pageInfo"]["hasNextPage"] = True
    elif connection == "labels":
        project["items"]["nodes"][0]["content"][connection]["pageInfo"]["hasNextPage"] = True
    else:
        project["items"]["nodes"][0][connection]["pageInfo"]["hasNextPage"] = True

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner).search(PROJECT)


@pytest.mark.parametrize(
    ("connection", "malformation", "reason"),
    [
        ("fields", "missing", "missing_fields"),
        ("fields", "page_info", "truncated_fields"),
        ("labels", "missing", "truncated_or_invalid_labels"),
        ("labels", "page_info", "truncated_or_invalid_labels"),
        ("fieldValues", "missing", "invalid_field_values"),
        ("fieldValues", "page_info", "truncated_field_values"),
    ],
)
def test_search_refuses_missing_or_malformed_authority_connections(
    connection, malformation, reason,
):
    payload = _page("item-1", 1, None, False)
    project = payload["data"]["user"]["projectV2"]
    if connection == "fields":
        parent = project
    elif connection == "labels":
        parent = project["items"]["nodes"][0]["content"]
    else:
        parent = project["items"]["nodes"][0]
    if malformation == "missing":
        parent.pop(connection)
    else:
        parent[connection]["pageInfo"] = []

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    with pytest.raises(GitHubProjectsTrackerError, match=reason):
        GitHubProjectsTracker(runner=runner)._search_raw(PROJECT)


@pytest.mark.parametrize("unavailable", ["not_in_project", "rest_404"])
def test_get_issue_raises_typed_unavailable_only_after_target_absence_is_proven(unavailable):
    payload = _page("item-1", 1, None, False)

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if unavailable == "rest_404" and command[-1].endswith("/issues/1"):
            return subprocess.CompletedProcess(
                command, 1, json.dumps({"message": "Not Found", "status": "404"}),
                "gh: Not Found (HTTP 404)",
            )
        raise AssertionError(command)

    issue_id = "GHQUAL-2" if unavailable == "not_in_project" else "GHQUAL-1"
    with pytest.raises(IssueUnavailableError) as exc:
        GitHubProjectsTracker(runner=runner).get_issue(issue_id)
    assert exc.value.issue_id == issue_id
    assert str(exc.value) == f"issue unavailable: {issue_id}"


def test_get_issue_keeps_ambiguous_permission_failure_visible():
    payload = _page("item-1", 1, None, False)

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[-1].endswith("/issues/1"):
            return subprocess.CompletedProcess(
                command, 1,
                json.dumps({"message": "Resource not accessible", "status": "403"}),
                "gh: Resource not accessible (HTTP 403)",
            )
        raise AssertionError(command)

    with pytest.raises(GitHubProjectsTrackerError, match="permission_denied") as exc:
        GitHubProjectsTracker(runner=runner).get_issue("GHQUAL-1")
    assert exc.value.operation == "issue.read"


def test_query_issue_keeps_unavailable_parent_and_dependency_as_related_errors(monkeypatch):
    payload = _page("item-2", 2, None, False)

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        path = command[-1]
        if "issues?state=all" in path:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        if "/dependencies/blocked_by" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(3)]), "")
        if "/comments" in path or "/dependencies/blocking" in path or "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        raise AssertionError(path)

    tracker = GitHubProjectsTracker(runner=runner)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)

    out = query.issue("GHQUAL-2")

    assert {(link["type"], link["target"]) for link in out["issue"]["links"]} == {
        ("subtask-of", "GHQUAL-1"),
        ("depends-on", "GHQUAL-3"),
    }
    assert out["related"] == {
        "GHQUAL-1": {"id": "GHQUAL-1", "error": "issue unavailable"},
        "GHQUAL-3": {"id": "GHQUAL-3", "error": "issue unavailable"},
    }


def test_query_issue_still_refuses_foreign_relation_uri_before_aliasing(monkeypatch):
    payload = _page("item-2", 2, None, False)
    foreign_parent = _rest_issue(1)
    foreign_parent["repository_url"] = "https://api.github.com/repos/patobiskoto/foreign"
    foreign_parent["url"] = "https://api.github.com/repos/patobiskoto/foreign/issues/1"
    foreign_parent["html_url"] = "https://github.com/patobiskoto/foreign/issues/1"

    def runner(command, **kwargs):
        if command[2] == "graphql":
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        path = command[-1]
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(foreign_parent), "")
        if "/comments" in path or "/dependencies/" in path or "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, "[]", "")
        raise AssertionError(path)

    tracker = GitHubProjectsTracker(runner=runner)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)

    with pytest.raises(GitHubProjectsTrackerError, match="foreign_issue_coordinate"):
        query.issue("GHQUAL-2")


def test_hydration_refuses_foreign_relation_before_number_aliasing():
    foreign_parent = _rest_issue(1)
    foreign_parent["repository_url"] = "https://api.github.com/repos/patobiskoto/foreign-test-only"
    foreign_parent["url"] = "https://api.github.com/repos/patobiskoto/foreign-test-only/issues/1"
    foreign_parent["html_url"] = "https://github.com/patobiskoto/foreign-test-only/issues/1"

    def runner(command, **kwargs):
        path = command[-1]
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(foreign_parent), "")
        return subprocess.CompletedProcess(command, 0, "[]", "")

    tracker = GitHubProjectsTracker(runner=runner)
    with pytest.raises(GitHubProjectsTrackerError, match="foreign_issue_coordinate"):
        tracker._hydrate_issue(Issue(id="GHQUAL-2", title="child"), tracker._binding(PROJECT))


def test_hydration_reads_complete_native_hierarchy_and_dependency_directions():
    def runner(command, **kwargs):
        path = command[-1]
        if path.endswith("/issues/2"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(2)), "")
        if path.endswith("/issues/2/parent"):
            return subprocess.CompletedProcess(command, 0, json.dumps(_rest_issue(1)), "")
        if "/dependencies/blocked_by" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(3)]), "")
        if "/dependencies/blocking" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(4)]), "")
        if "/sub_issues" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([_rest_issue(5)]), "")
        if "/comments" in path:
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                "id": 88, "body": "commentaire é", "created_at": "2026-09-27T12:02:00Z",
            }]), "")
        raise AssertionError(path)

    tracker = GitHubProjectsTracker(runner=runner)
    issue = tracker._hydrate_issue(Issue(id="GHQUAL-2", title="child"), tracker._binding(PROJECT))
    assert [(link.type, link.direction, link.target) for link in issue.links] == [
        ("subtask-of", "inward", "GHQUAL-1"),
        ("depends-on", "outward", "GHQUAL-3"),
        ("blocks", "outward", "GHQUAL-4"),
        ("parent-of", "outward", "GHQUAL-5"),
    ]
    assert issue.comments == [{"text": "commentaire é", "created": 1790510520000}]
    assert issue.created == 1790510400000
    assert issue.updated == 1790510460000


class _BodyRelationTransport:
    """Real adapter transport boundaries, with an explicitly injected lost reply."""

    def __init__(self, operation, outcome):
        self.operation, self.outcome = operation, outcome
        self.body = "UTF-8 é"
        self.priority = "P1"
        self.related = False
        self.external_link = False
        self.writes = 0
        self.events = []

    def _project_payload(self):
        page = _page("item-1", 1, None, False)
        nodes = page["data"]["user"]["projectV2"]["items"]["nodes"]
        other = _page("item-2", 2, None, False)["data"]["user"]["projectV2"]["items"]["nodes"][0]
        nodes.append(other)
        priority = next(v for v in nodes[0]["fieldValues"]["nodes"] if v["field"]["id"] == "priority")
        priority.update(name=self.priority, optionId=f"priority-{self.priority.lower()}")
        if self.related and self.operation == "graphql-link":
            for source, target in ((nodes[0], nodes[1]), (nodes[1], nodes[0])):
                content = target["content"]
                source["content"]["relatesTo"]["nodes"] = [{
                    "id": content["id"], "number": content["number"],
                    "repository": content["repository"],
                }]
        return page

    def __call__(self, command, **kwargs):
        graphql = command[2] == "graphql"
        query = next((v for v in command if v.startswith("query=")), "")
        mutation = (graphql and "query=mutation" in query) or (
            not graphql and command[3] != "GET"
        )
        if mutation:
            self.writes += 1
            self.events.append("write")
            if self.outcome in {"applied", "unrelated", "unrelated_links", "read_unavailable"}:
                if self.operation == "body":
                    self.body = next(v.removeprefix("body=") for v in command if v.startswith("body="))
                else:
                    self.related = True
            if self.outcome == "divergent":
                if self.operation == "body":
                    self.body = "external body"
                else:
                    self.priority = "P2"
            if self.outcome == "unrelated":
                self.priority = "P2"
            if self.outcome == "unrelated_links":
                self.external_link = True
            return subprocess.CompletedProcess(command, 1, "", "synthetic lost response")
        self.events.append("read")
        if self.writes and self.outcome == "read_unavailable":
            return subprocess.CompletedProcess(command, 1, "", "synthetic read unavailable")
        if graphql:
            payload = self._project_payload()
        else:
            path = command[-1]
            if path.endswith("/parent"):
                return subprocess.CompletedProcess(
                    command, 1,
                    json.dumps({"message": "No parent issue found", "status": "404"}),
                    "gh: No parent issue found (HTTP 404)",
                )
            number = 2 if "/issues/2" in path else 1
            if path.endswith(f"/issues/{number}"):
                payload = _rest_issue(number)
                if number == 1:
                    payload["body"] = self.body
            elif number == 1 and "/dependencies/blocking" in path and self.external_link:
                payload = [_rest_issue(3)]
            elif "/dependencies/" in path and self.related and self.operation == "rest-link":
                reverse = number == 2 and "/blocking" in path
                forward = number == 1 and "/blocked_by" in path
                payload = [_rest_issue(1 if reverse else 2)] if reverse or forward else []
            else:
                payload = []
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


@pytest.mark.parametrize("operation", ["body", "rest-link", "graphql-link"])
@pytest.mark.parametrize("outcome", ["applied", "unchanged", "divergent", "unrelated", "unrelated_links", "read_unavailable"])
def test_pat66_body_and_links_observe_ambiguous_write_once(operation, outcome):
    transport = _BodyRelationTransport(operation, outcome)
    tracker = GitHubProjectsTracker(runner=transport)
    initial = tracker.get_issue("GHQUAL-1")

    def call():
        if operation == "body":
            return tracker.update_body(initial, initial.body, "desired é", project=PROJECT)
        return tracker.link(
            "GHQUAL-1", "depends-on" if operation == "rest-link" else "relates",
            "GHQUAL-2", project=PROJECT,
        )

    if outcome == "applied":
        call()
        call()  # An explicit completed replay observes the target, never writes.
    elif outcome in {"unchanged", "read_unavailable"}:
        with pytest.raises(GitHubProjectsTrackerError, match="transport_failed"):
            call()
    else:
        with pytest.raises(TrackerConflictError, match="divergent après écriture"):
            call()
    assert transport.writes == 1
    assert "read" in transport.events[transport.events.index("write") + 1:]


def test_pat69_parent_link_accepts_only_the_derived_epic_version_change(
    monkeypatch,
):
    tracker = GitHubProjectsTracker()
    binding = tracker._binding(PROJECT)
    parent = Issue(
        id="GHQUAL-1", title="Epic", state="ready", type="Epic", version=101,
        priority="P2", estimate=3, milestone="V1", labels=["preserve"],
        body="Exact parent body",
        links=[
            Link("parent-of", "outward", "GHQUAL-9"),
            Link("relates", "outward", "GHQUAL-8"),
        ],
    )
    child = Issue(
        id="GHQUAL-2", title="Delivered", state="done", type="Task", version=202,
        ac_done=1, ac_total=1,
        acceptance_status="accepted",
        acceptance_source="ghprojects-acceptance-proof",
        acceptance_coordinates='{"proof_id":"accepted"}',
        links=[Link("depends-on", "outward", "GHQUAL-7")],
    )
    linked_parent = deepcopy(parent)
    linked_parent.version = 303
    linked_parent.links.append(Link("parent-of", "outward", "GHQUAL-2"))
    linked_child = deepcopy(child)
    linked_child.links.append(Link("subtask-of", "inward", "GHQUAL-1"))
    reads = iter(
        [parent, child, deepcopy(parent), deepcopy(child), linked_parent, linked_child]
    )
    writes = []
    monkeypatch.setattr(tracker, "_authoritative_binding", lambda _project: binding)
    monkeypatch.setattr(
        tracker, "_native_issue",
        lambda issue_id, _binding, _operation: (
            (1, 1001) if issue_id == "GHQUAL-1" else (2, 1002)
        ),
    )
    monkeypatch.setattr(tracker, "get_issue", lambda _issue_id: deepcopy(next(reads)))
    monkeypatch.setattr(
        tracker, "_rest_write",
        lambda *args: writes.append(args) or {},
    )

    tracker.link("GHQUAL-1", "parent-of", "GHQUAL-2", project=PROJECT)

    assert len(writes) == 1
    assert writes[0][0:2] == (
        "POST",
        "repos/patobiskoto/foundry-v1-ghprojects-sandbox/issues/1/sub_issues",
    )


@pytest.mark.parametrize(
    ("attribute", "changed"),
    [
        ("title", "Drifted Epic"),
        ("body", "Drifted body"),
        ("labels", ["preserve", "foreign"]),
        ("priority", "P1"),
        ("estimate", 5),
        ("milestone", "V2"),
        ("links", [Link("relates", "outward", "GHQUAL-10")]),
    ],
)
def test_pat69_parent_link_refuses_unrelated_drift_after_derived_version_change(
    monkeypatch, attribute, changed,
):
    tracker = GitHubProjectsTracker()
    binding = tracker._binding(PROJECT)
    parent = Issue(
        id="GHQUAL-1", title="Epic", state="ready", type="Epic", version=101,
        priority="P2", estimate=3, milestone="V1", labels=["preserve"],
        body="Exact parent body",
        links=[Link("relates", "outward", "GHQUAL-8")],
    )
    child = Issue(
        id="GHQUAL-2", title="Delivered", state="done", type="Task", version=202,
        ac_done=1, ac_total=1,
        acceptance_status="accepted",
        acceptance_source="ghprojects-acceptance-proof",
        acceptance_coordinates='{"proof_id":"accepted"}',
    )
    linked_parent = deepcopy(parent)
    linked_parent.version = 303
    linked_parent.links.append(Link("parent-of", "outward", "GHQUAL-2"))
    if attribute == "links":
        linked_parent.links.extend(changed)
    else:
        setattr(linked_parent, attribute, changed)
    linked_child = deepcopy(child)
    linked_child.links = [Link("subtask-of", "inward", "GHQUAL-1")]
    reads = iter(
        [parent, child, deepcopy(parent), deepcopy(child), linked_parent, linked_child]
    )
    monkeypatch.setattr(tracker, "_authoritative_binding", lambda _project: binding)
    monkeypatch.setattr(
        tracker, "_native_issue",
        lambda issue_id, _binding, _operation: (
            (1, 1001) if issue_id == "GHQUAL-1" else (2, 1002)
        ),
    )
    monkeypatch.setattr(tracker, "get_issue", lambda _issue_id: deepcopy(next(reads)))
    monkeypatch.setattr(tracker, "_rest_write", lambda *_args: {})

    with pytest.raises(TrackerConflictError, match="lien GitHub divergent"):
        tracker.link("GHQUAL-1", "parent-of", "GHQUAL-2", project=PROJECT)


def test_pat69_parent_link_does_not_ignore_a_non_epic_version_drift(monkeypatch):
    tracker = GitHubProjectsTracker()
    binding = tracker._binding(PROJECT)
    parent = Issue(
        id="GHQUAL-1", title="Epic", state="ready", type="Epic", version=101,
    )
    child = Issue(
        id="GHQUAL-2", title="Delivered", state="done", type="Task", version=202,
        ac_done=1, ac_total=1,
        acceptance_status="accepted",
        acceptance_source="ghprojects-acceptance-proof",
        acceptance_coordinates='{"proof_id":"accepted"}',
    )
    linked_parent = deepcopy(parent)
    linked_parent.version = 303
    linked_parent.links.append(Link("parent-of", "outward", "GHQUAL-2"))
    linked_child = deepcopy(child)
    linked_child.version = 404
    linked_child.links.append(Link("subtask-of", "inward", "GHQUAL-1"))
    reads = iter(
        [parent, child, deepcopy(parent), deepcopy(child), linked_parent, linked_child]
    )
    monkeypatch.setattr(tracker, "_authoritative_binding", lambda _project: binding)
    monkeypatch.setattr(
        tracker, "_native_issue",
        lambda issue_id, _binding, _operation: (
            (1, 1001) if issue_id == "GHQUAL-1" else (2, 1002)
        ),
    )
    monkeypatch.setattr(tracker, "get_issue", lambda _issue_id: deepcopy(next(reads)))
    monkeypatch.setattr(tracker, "_rest_write", lambda *_args: {})

    with pytest.raises(TrackerConflictError, match="lien GitHub divergent"):
        tracker.link("GHQUAL-1", "parent-of", "GHQUAL-2", project=PROJECT)


class _AsymmetricRelatesTransport(_BodyRelationTransport):
    def __init__(self, present_on, *, existing=False, normal_response=False):
        super().__init__("graphql-link", "applied")
        self.present_on = present_on
        self.related = existing
        self.normal_response = normal_response

    def _project_payload(self):
        payload = super()._project_payload()
        nodes = payload["data"]["user"]["projectV2"]["items"]["nodes"]
        if self.related:
            absent = nodes[1] if self.present_on == "source" else nodes[0]
            absent["content"]["relatesTo"]["nodes"] = []
        return payload

    def __call__(self, command, **kwargs):
        response = super().__call__(command, **kwargs)
        if self.normal_response and command[2] == "graphql" and any(
            arg.startswith("query=mutation") for arg in command
        ):
            return subprocess.CompletedProcess(command, 0, json.dumps({"data": {
                "addRelatesTo": {"issue": {"id": "issue-node-1"},
                                 "relatedIssue": {"id": "issue-node-2"}},
            }}), "")
        return response


@pytest.mark.parametrize("present_on", ["source", "destination"])
def test_pat66_asymmetric_relates_replay_refuses_before_mutation(present_on):
    transport = _AsymmetricRelatesTransport(present_on, existing=True)
    tracker = GitHubProjectsTracker(runner=transport)
    with pytest.raises(TrackerConflictError, match="asymétrique avant écriture"):
        tracker.link("GHQUAL-1", "relates", "GHQUAL-2", project=PROJECT)
    assert transport.writes == 0


@pytest.mark.parametrize("present_on", ["source", "destination"])
@pytest.mark.parametrize("normal_response", [True, False])
def test_pat66_relates_requires_both_projections_after_mutation(
    present_on, normal_response,
):
    transport = _AsymmetricRelatesTransport(
        present_on, normal_response=normal_response,
    )
    tracker = GitHubProjectsTracker(runner=transport)
    with pytest.raises(TrackerConflictError, match="divergent après écriture"):
        tracker.link("GHQUAL-1", "relates", "GHQUAL-2", project=PROJECT)
    assert transport.writes == 1
    assert "read" in transport.events[transport.events.index("write") + 1:]


@pytest.mark.parametrize("lost_response", [False, True])
@pytest.mark.parametrize("missing_side", ["child", "parent"])
def test_pat66_create_parent_refuses_one_sided_write_and_replay(
    monkeypatch, tmp_path, lost_response, missing_side,
):
    provider = _create_provider_state()
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, tracker, provider)
    original_write = tracker._rest_write
    original_child = tracker._created_issue_readback
    original_parent = tracker._create_parent_snapshot

    def write(*args):
        result = original_write(*args)
        if args[1].endswith("/sub_issues") and lost_response:
            raise GitHubProjectsTrackerError("issue.parent_create", "transport_failed")
        return result

    def child(*args):
        result = original_child(*args)
        if missing_side == "child":
            result.links = []
        return result

    def parent(*args):
        number, result = original_parent(*args)
        if missing_side == "parent":
            result.links = []
        return number, result

    monkeypatch.setattr(tracker, "_rest_write", write)
    monkeypatch.setattr(tracker, "_created_issue_readback", child)
    monkeypatch.setattr(tracker, "_create_parent_snapshot", parent)
    for _ in range(2):
        with pytest.raises(GitHubProjectsPartialCreateConflict, match="asymétrique"):
            tracker.create_issue(PROJECT, "partial", "body", parent="GHQUAL-1")
    assert provider["parent_posts"] == 1
    stored = json.loads(next((tmp_path / "ghprojects-create-intents").glob("*.json")).read_text())
    assert stored["state"] != "complete"


def test_pat66_create_parent_requalified_after_fields_before_attachment(monkeypatch, tmp_path):
    provider = _create_provider_state()
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, tracker, provider)

    def parent(*_):
        assert provider["field_posts"] == 1
        raise GitHubProjectsTrackerError("issue.parent_prewrite", "incomplete_project_parent")

    monkeypatch.setattr(tracker, "_create_parent_snapshot", parent)
    with pytest.raises(GitHubProjectsPartialCreateError, match="incomplete_project_parent"):
        tracker.create_issue(PROJECT, "partial", "body", parent="GHQUAL-1")
    assert provider["issue_posts"] == provider["field_posts"] == 1
    assert provider["parent_posts"] == 0


@pytest.mark.parametrize("lost_response", [False, True])
def test_pat66_create_parent_preserves_properties_and_reconciles_lost_response(
    monkeypatch, tmp_path, lost_response,
):
    provider = _create_provider_state()
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, tracker, provider)
    original = tracker._rest_write

    def write(*args):
        result = original(*args)
        if args[1].endswith("/sub_issues") and lost_response:
            raise GitHubProjectsTrackerError("issue.parent_create", "transport_failed")
        return result

    monkeypatch.setattr(tracker, "_rest_write", write)
    assert tracker.create_issue(PROJECT, "partial", "body", parent="GHQUAL-1").id == "GHQUAL-4"
    assert tracker.create_issue(PROJECT, "partial", "body", parent="GHQUAL-1").id == "GHQUAL-4"
    assert provider["parent_posts"] == 1
    assert all(value == 1 for value in provider["parent_observations"])


def test_pat66_completed_creation_refuses_missing_reciprocal_parent(monkeypatch, tmp_path):
    provider = _create_provider_state()
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, tracker, provider)
    tracker.create_issue(PROJECT, "partial", "body", parent="GHQUAL-1")
    monkeypatch.setattr(tracker, "_create_parent_snapshot", lambda *_: (
        1, Issue(id="GHQUAL-1", title="parent", type="Epic"),
    ))
    with pytest.raises(GitHubProjectsPartialCreateError, match="completed_create_drift"):
        tracker.create_issue(PROJECT, "partial", "body", parent="GHQUAL-1")
    assert provider["parent_posts"] == 1


@pytest.mark.parametrize("endpoint", ["child", "parent"])
def test_pat66_create_parent_detects_unrelated_property_write(monkeypatch, tmp_path, endpoint):
    provider = _create_provider_state()
    tracker = GitHubProjectsTracker(state_dir=tmp_path)
    _install_create_harness(monkeypatch, tracker, provider)
    original_child = tracker._created_issue_readback
    original_parent = tracker._create_parent_snapshot

    def child(*args):
        result = original_child(*args)
        if provider["parent"] and endpoint == "child":
            result.priority = "P1"
        return result

    def parent(*args):
        number, result = original_parent(*args)
        if provider["parent"] and endpoint == "parent":
            result.priority = "P1"
        return number, result

    monkeypatch.setattr(tracker, "_created_issue_readback", child)
    monkeypatch.setattr(tracker, "_create_parent_snapshot", parent)
    with pytest.raises(GitHubProjectsPartialCreateConflict, match="propriétés non visées"):
        tracker.create_issue(PROJECT, "partial", "body", parent="GHQUAL-1")
    assert provider["parent_posts"] == 1


@pytest.mark.parametrize("drift", [
    "public_repository", "unlinked_repository", "foreign_owner", "public_project",
    "deleted_project", "malformed_identity", "permission", "rate_limit",
])
def test_pat66_create_live_repository_scope_refuses_before_journal_and_post(tmp_path, drift):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        assert command[1:3] == ["api", "graphql"]
        query = command[command.index("-f") + 1]
        page = _page("item-1", 1, None, False)
        if "repositories(first:" in query:
            if drift in {"permission", "rate_limit"}:
                return subprocess.CompletedProcess(command, 1, "", "403 permission" if drift == "permission" else "429 rate limit")
            raw = page["data"]["user"]["projectV2"]
            repository = deepcopy(raw["items"]["nodes"][0]["content"]["repository"])
            raw["repositories"] = {"nodes": [repository], "pageInfo": {"hasNextPage": False, "endCursor": None}}
            if drift == "public_repository":
                repository["isPrivate"] = False
            elif drift == "unlinked_repository":
                raw["repositories"]["nodes"] = []
            elif drift == "foreign_owner":
                repository["owner"]["__typename"] = "Organization"
            elif drift == "public_project":
                raw["public"] = True
            elif drift == "deleted_project":
                page["data"]["user"]["projectV2"] = None
            elif drift == "malformed_identity":
                raw["repositories"] = {"nodes": None}
        return subprocess.CompletedProcess(command, 0, json.dumps(page), "")

    tracker = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    with pytest.raises(GitHubProjectsTrackerError):
        tracker.create_issue(PROJECT, "partial", "body")
    assert len(calls) == 2
    assert not (tmp_path / "ghprojects-create-intents").exists()
    assert not any("-X" in command for command in calls)


def test_pat66_create_qualified_live_scope_precedes_first_issue_write(monkeypatch, tmp_path):
    events = []

    def runner(command, **kwargs):
        events.append("live_scope")
        page = _page("item-1", 1, None, False)
        raw = page["data"]["user"]["projectV2"]
        raw["repositories"] = {
            "nodes": [deepcopy(raw["items"]["nodes"][0]["content"]["repository"])],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(page), "")

    tracker = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    provider = _create_provider_state()
    _install_create_harness(monkeypatch, tracker, provider)
    monkeypatch.setattr(tracker, "verify_project_identity", GitHubProjectsTracker.verify_project_identity.__get__(tracker))
    original = tracker._rest_write

    def write(*args):
        events.append("write")
        assert events[0] == "live_scope"
        return original(*args)

    monkeypatch.setattr(tracker, "_rest_write", write)
    assert tracker.create_issue(PROJECT, "partial", "body").id == "GHQUAL-4"
    assert provider["issue_posts"] == 1
    assert events == ["live_scope", "write"]


class _EpicClosureTracker(GitHubProjectsTracker):
    """Exercise the bounded Epic path with observable comment and State effects."""

    def __init__(self, tmp_path, *, comment_loss=None, state_loss=None):
        super().__init__(state_dir=tmp_path)
        self.parent = Issue(
            id="GHQUAL-1", title="Release Epic", type="Epic", state="in-progress",
            body="- [ ] Human validation", ac_total=1,
            links=[Link("parent-of", "outward", "GHQUAL-2")],
        )
        self.child = Issue(
            id="GHQUAL-2", title="Delivered child", type="Task", state="done",
            body="- [ ] Reviewed criterion", ac_done=1, ac_total=1,
            links=[Link("subtask-of", "inward", "GHQUAL-1")],
            acceptance_status="accepted", acceptance_source="ghprojects-review-proof",
            acceptance_coordinates='{"proof_id":"synthetic-accepted"}',
        )
        self.comment_posts = 0
        self.state_writes = 0
        self.comment_loss = comment_loss
        self.state_loss = state_loss
        self.identity_results = []
        self.identity_checks = 0

    def verify_project_identity(self, project):
        assert project == PROJECT
        self.identity_checks += 1
        if self.identity_results:
            return self.identity_results.pop(0)
        return True

    def _native_issue_read(self, issue_id):
        assert issue_id == "GHQUAL-1"
        return deepcopy(self.parent)

    def get_issue(self, issue_id):
        if issue_id == "GHQUAL-2":
            return deepcopy(self.child)
        return super().get_issue(issue_id)

    def _current_lifecycle_scope(self, issue_id, binding):
        return {**LIFECYCLE_SCOPE, "issue_id": issue_id}

    def _native_issue(self, issue_id, binding, operation):
        return 1, 1001

    def _item_coordinate(self, issue_id, binding):
        return "item-1", "issue-node-1"

    def _write_catalog(self, binding, fields):
        return {"state": object()}

    def _rest_write(self, method, path, body, operation):
        assert method == "POST" and path.endswith("/issues/1/comments")
        self.comment_posts += 1
        if self.comment_loss != "hidden":
            self.parent.comments.append({"text": body["body"], "created": 1})
        if self.comment_loss:
            raise GitHubProjectsTrackerError(operation, "lost_response")
        return {"id": self.comment_posts, "body": body["body"]}

    def _set_project_field(self, item_id, project_id, field, value):
        assert value == "done"
        self.state_writes += 1
        if self.state_loss != "hidden":
            self.parent.state = "done"
        if self.state_loss:
            self.state_loss = None
            raise GitHubProjectsTrackerError("project.field_write", "lost_response")


@pytest.mark.parametrize("lost", [None, "comment", "state"])
def test_pat69_ghprojects_epic_closure_replays_without_second_effect(
    monkeypatch, tmp_path, lost,
):
    tracker = _EpicClosureTracker(
        tmp_path,
        comment_loss="applied" if lost == "comment" else None,
        state_loss="applied" if lost == "state" else None,
    )
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)

    closed = write.close_epic(
        tracker, "GHQUAL-1", issued_at=123,
        nonce="synthetic_nonce_123456", human_verdict="accepted",
    )
    replayed = write.close_epic(tracker, "GHQUAL-1", human_verdict="accepted")

    assert closed.receipt == replayed.receipt
    assert closed.replayed is False and replayed.replayed is True
    assert tracker.get_issue("GHQUAL-1").state == "done"
    assert tracker.comment_posts == tracker.state_writes == 1


def test_pat69_ghprojects_epic_closure_is_enabled_after_live_qualification(
    monkeypatch, tmp_path,
):
    tracker = _EpicClosureTracker(tmp_path)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)

    assert tracker.bounded_epic_closure_supported is True
    outcome = write.close_epic(
        tracker, "GHQUAL-1", issued_at=123,
        nonce="synthetic_nonce_123456", human_verdict="accepted",
    )
    assert outcome.replayed is False
    assert tracker.comment_posts == tracker.state_writes == 1


@pytest.mark.parametrize("detached_before", ["comment", "state"])
def test_pat69_ghprojects_detached_repository_refuses_before_each_write(
    monkeypatch, tmp_path, detached_before,
):
    tracker = _EpicClosureTracker(tmp_path)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)
    tracker.identity_results = (
        [False] if detached_before == "comment" else [True, False]
    )

    with pytest.raises(
        GitHubProjectsTrackerError,
        match=f"epic-closure\\.{detached_before}_prewrite.*unqualified_repository_project",
    ):
        write.close_epic(
            tracker, "GHQUAL-1", issued_at=123,
            nonce="synthetic_nonce_123456", human_verdict="accepted",
        )

    assert tracker.comment_posts == (detached_before == "state")
    assert tracker.state_writes == 0

    tracker.identity_results = [True, True]
    closed = write.close_epic(
        tracker, "GHQUAL-1", issued_at=123,
        nonce="synthetic_nonce_123456", human_verdict="accepted",
    )

    assert closed.receipt.nonce == "synthetic_nonce_123456"
    assert tracker.comment_posts == tracker.state_writes == 1
    assert tracker.identity_checks == 3


def test_pat69_state_projection_preserves_every_untargeted_parent_property(
    monkeypatch, tmp_path,
):
    tracker = _EpicClosureTracker(tmp_path)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)
    tracker.parent.priority = "P2"
    tracker.parent.estimate = 3
    tracker.parent.milestone = "V1"
    tracker.parent.labels = ["preserve"]
    tracker.parent.body = "Exact parent body\n\n- [ ] Human validation"
    tracker.parent.links.append(Link("relates", "outward", "GHQUAL-9"))
    before = {
        name: deepcopy(getattr(tracker.parent, name))
        for name in (
            "title", "body", "labels", "links", "priority", "estimate",
            "milestone", "type",
        )
    }

    write.close_epic(
        tracker, "GHQUAL-1", issued_at=123,
        nonce="synthetic_nonce_123456", human_verdict="accepted",
    )

    assert {
        name: getattr(tracker.parent, name) for name in before
    } == before
    assert tracker.parent.state == "done"


@pytest.mark.parametrize(
    ("attribute", "changed"),
    [
        ("title", "Drifted Epic"),
        ("body", "Drifted body"),
        ("labels", ["foreign"]),
        ("priority", "P1"),
        ("estimate", 5),
        ("milestone", "V2"),
        ("type", "Task"),
        ("links", Link("relates", "outward", "GHQUAL-9")),
    ],
)
def test_pat69_state_projection_refuses_untargeted_parent_drift(
    monkeypatch, tmp_path, attribute, changed,
):
    tracker = _EpicClosureTracker(tmp_path)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)
    original = tracker._set_project_field

    def state_write_with_drift(*args):
        original(*args)
        if attribute == "links":
            tracker.parent.links.append(changed)
        else:
            setattr(tracker.parent, attribute, changed)

    monkeypatch.setattr(tracker, "_set_project_field", state_write_with_drift)

    with pytest.raises(TrackerConflictError, match="untargeted properties changed"):
        write.close_epic(
            tracker, "GHQUAL-1", issued_at=123,
            nonce="synthetic_nonce_123456", human_verdict="accepted",
        )
    assert tracker.comment_posts == tracker.state_writes == 1


@pytest.mark.parametrize("replay_coordinates", [
    {"issued_at": 123, "nonce": "synthetic_nonce_123456"},
    {"issued_at": 124, "nonce": "synthetic_nonce_123457"},
    {},  # Normal caller replay regenerates timestamp and nonce.
])
def test_pat69_ghprojects_unknown_comment_effect_never_reposts(
    monkeypatch, tmp_path, replay_coordinates,
):
    tracker = _EpicClosureTracker(tmp_path, comment_loss="hidden")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)

    with pytest.raises(GitHubProjectsTrackerError, match="lost_response"):
        write.close_epic(
            tracker, "GHQUAL-1", issued_at=123,
            nonce="synthetic_nonce_123456", human_verdict="accepted",
        )
    with pytest.raises(TrackerConflictError, match="no second POST"):
        write.close_epic(
            tracker, "GHQUAL-1", human_verdict="accepted", **replay_coordinates,
        )
    assert tracker.comment_posts == 1 and tracker.state_writes == 0


def test_pat69_ghprojects_pending_state_effect_replays_without_new_comment(
    monkeypatch, tmp_path,
):
    tracker = _EpicClosureTracker(tmp_path, state_loss="hidden")
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)

    with pytest.raises(GitHubProjectsTrackerError, match="lost_response"):
        write.close_epic(
            tracker, "GHQUAL-1", issued_at=123,
            nonce="synthetic_nonce_123456", human_verdict="accepted",
        )
    outcome = write.close_epic(tracker, "GHQUAL-1", human_verdict="accepted")
    assert outcome.receipt.nonce == "synthetic_nonce_123456"
    assert tracker.comment_posts == 1 and tracker.state_writes == 2


def test_pat69_ghprojects_epic_refuses_unknown_or_changed_child(monkeypatch, tmp_path):
    tracker = _EpicClosureTracker(tmp_path)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)
    tracker.child.acceptance_status = "override"
    with pytest.raises(SystemExit, match="dérogée"):
        write.close_epic(tracker, "GHQUAL-1", human_verdict="accepted")
    assert tracker.comment_posts == tracker.state_writes == 0

    tracker.child.acceptance_status = "accepted"
    write.close_epic(
        tracker, "GHQUAL-1", issued_at=123,
        nonce="synthetic_nonce_123456", human_verdict="accepted",
    )
    tracker.child.title = "Changed after closure"
    with pytest.raises(TrackerConflictError, match="graph changed"):
        tracker.get_epic_closure(PROJECT, "GHQUAL-1")
    assert tracker.comment_posts == tracker.state_writes == 1


@pytest.mark.parametrize("drift", ["title", "comment", "duplicate_audit", "child_link"])
def test_pat69_ghprojects_closed_epic_replay_detects_graph_or_parent_drift(
    monkeypatch, tmp_path, drift,
):
    tracker = _EpicClosureTracker(tmp_path)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)
    write.close_epic(
        tracker, "GHQUAL-1", issued_at=123,
        nonce="synthetic_nonce_123456", human_verdict="accepted",
    )
    if drift == "title":
        tracker.parent.title = "Different title"
    elif drift == "comment":
        tracker.parent.comments.append({"text": "foreign note", "created": 2})
    elif drift == "duplicate_audit":
        tracker.parent.comments.append(deepcopy(tracker.parent.comments[0]))
    else:
        tracker.parent.links.append(Link("parent-of", "outward", "GHQUAL-3"))

    with pytest.raises(TrackerConflictError):
        tracker.get_issue("GHQUAL-1")
    assert tracker.comment_posts == tracker.state_writes == 1
