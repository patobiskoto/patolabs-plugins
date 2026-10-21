"""`issue merge` driven end to end through `DevHubTracker` over a scripted transport.

PAT-309 (no criterion under the override flag) and PAT-318 (refusal before the code-host
merge when the DEVHUB-106 route is absent). No network: every request is answered by
`RoutedDevHub`, and the code host is a local fake.
"""
from types import SimpleNamespace

import pytest

from foundry import issue, write
from foundry.models import Project
from foundry.routing import RoutingConfigError, review_diff_hash
from foundry.trackers.devhub import DevHubTracker, DevHubTrackerError

TOKEN = "tracker-token-at-least-twenty-four"
SECRET = "proof-secret-at-least-thirty-two-characters"
PROJECT = Project(key="TRAME", id="42")
PR_URL = "https://github.com/acme/trame/pull/1"
HEAD, BASE, DIFF = "a" * 40, "d" * 40, b"exact-diff"
FLAGS = {"--allow-incomplete-ac", "--ac-override-reason=human_confirmed"}
PAGE_V1 = "devhub-application.v1"


class RoutedDevHub(DevHubTracker):
    """Answers each request by route from `issue_raw` and `receipts`; records the calls."""

    def __init__(self, *, ac_total, override=None):
        super().__init__(
            url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
            now_ms=lambda: 1_000_000, nonce_factory=lambda: "canonical_nonce_123456",
            idempotency_factory=lambda: "idempotency123456",
        )
        self.issue_raw = {
            "id": "TRAME-1", "title": "Pilot", "state": "review", "priority": "P0",
            "estimate": 5, "milestone": "POC", "type": "Feature", "labels": [],
            "ac_done": 0, "ac_total": ac_total, "links": [], "pr_url": PR_URL,
            "body": "- [ ] pilot" if ac_total else "no criterion", "comments": [],
            "created": 1, "updated": 2, "version": 3,
        }
        self.receipts = [{
            "id": "1", "issue": "TRAME-1", "project": "TRAME", "action": "review",
            "issue_version": 3, "pr_url": PR_URL, "head_sha": HEAD, "base_sha": BASE,
            "review_digest": review_diff_hash(DIFF), "merge_sha": None,
            "reason": None, "decision_digest": None, "decision": None,
            "proof_nonce": "nonce_000000000001", "actor": "foundry", "created": 5,
        }]
        self.override = override
        self.calls = []

    def _req(self, method, path, body=None, **kwargs):
        self.calls.append((method, path))
        if method == "GET" and path == "/issues/TRAME-1":
            return dict(self.issue_raw)
        if method == "GET" and path.startswith("/issues/TRAME-1/proof-receipts"):
            return {"items": list(self.receipts), "page": {
                "schema_version": PAGE_V1, "returned_count": len(self.receipts),
                "truncation": False, "cursor": None}}
        if method == "POST" and path == "/issues/TRAME-1/acceptance-override":
            raise self.override
        raise AssertionError(f"unexpected request: {method} {path}")


def _harness(monkeypatch, tracker):
    events = []
    pr = SimpleNamespace(
        number=1, sha=HEAD, head="feat/trame-1", base="main", base_sha=BASE,
        state="open", merged=False, url=PR_URL,
    )
    codehost = SimpleNamespace(
        name="github", resolve_repo=lambda: "acme/trame", get_pr=lambda *_: pr,
        merge_pr=lambda *_a, **_k: events.append("merge"),
        delete_branch=lambda *_: events.append("delete"),
    )
    monkeypatch.setattr(issue.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(issue.foundry, "codehost", lambda: codehost)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)
    monkeypatch.setattr(write, "ci_gate", lambda *_a, **_k: {
        "passed": True, "waived": False, "total": 1, "pending": 0, "failing": 0})
    monkeypatch.setattr(issue, "git_head", lambda: HEAD)
    monkeypatch.setattr(issue, "git_diff", lambda *_a, **_k: DIFF)
    monkeypatch.setattr(issue, "repository_identity", lambda: "acme/trame")
    monkeypatch.setattr(issue, "_observe_receipt", lambda *_a: None)

    class Store:
        def __init__(self, _repository):
            pass

        def valid_for_merge(self, **_kwargs):
            raise RoutingConfigError("no proof")

    monkeypatch.setattr(issue, "AcceptanceProofStore", Store)
    return events


def test_merge_refuses_an_issue_with_no_criterion_even_under_the_override_flag(monkeypatch):
    tracker = RoutedDevHub(ac_total=0)
    events = _harness(monkeypatch, tracker)

    with pytest.raises(SystemExit, match="aucun critère d’acceptation"):
        issue.merge("TRAME-1", "1", flags=FLAGS)

    assert events == []  # no code-host merge, no branch deletion
    assert all(method == "GET" for method, _path in tracker.calls)  # nothing written


def test_override_merge_is_refused_before_the_code_host_merge_when_the_route_is_absent(
        monkeypatch):
    absent = DevHubTrackerError(
        "POST", "/issues/TRAME-1/acceptance-override", 404, "route_unavailable")
    tracker = RoutedDevHub(ac_total=1, override=absent)
    events = _harness(monkeypatch, tracker)

    with pytest.raises(SystemExit, match="reçu typé d'override AC impossible"):
        issue.merge("TRAME-1", "1", flags=FLAGS)

    assert events == []
    assert ("POST", "/issues/TRAME-1/acceptance-override") in tracker.calls
    assert [call for call in tracker.calls if call[0] == "POST"] == [
        ("POST", "/issues/TRAME-1/acceptance-override")]  # no state write either
