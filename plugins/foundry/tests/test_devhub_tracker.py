import base64
import copy
import hashlib
import hmac
import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

import foundry
import foundry.trackers.devhub as devhub_module
from foundry import adr, edit, frame, query, write
from foundry.devhub_smoke import _require_audit_receipts
from foundry.models import (
    Adr,
    EpicClosureChild,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Project,
    TransitionContext,
)
from foundry.trackers.devhub import (
    DevHubTracker,
    DevHubTrackerError,
    _SameOriginRedirectHandler,
)
from foundry.trackers.base import TrackerConflictError


TOKEN = "tracker-token-at-least-twenty-four"
SECRET = "proof-secret-at-least-thirty-two-characters"
PROJECT = Project(key="TRAME", id="42")
EPIC_CLOSURE_CONTRACT = "devhub-epic-closure.v1"


def issue_raw(**overrides):
    value = {
        "id": "TRAME-1",
        "title": "Pilot",
        "state": "in-progress",
        "priority": "P0",
        "estimate": 5,
        "milestone": "POC",
        "type": "Feature",
        "labels": ["pilot"],
        "ac_done": 0,
        "ac_total": 1,
        "links": [],
        "pr_url": "https://github.com/acme/trame/pull/1",
        "body": "- [ ] pilot",
        "comments": [],
        "created": 1,
        "updated": 2,
        "version": 3,
    }
    value.update(overrides)
    return value


def closure_receipt_raw():
    return {
        "project_key": "TRAME",
        "project_id": "42",
        "parent_id": "TRAME-9",
        "parent_version": 4,
        "parent_type": "Epic",
        "parent_ac_done": 2,
        "parent_ac_total": 2,
        "children": [
            {"id": "TRAME-10", "version": 2, "state": "done"},
            {"id": "TRAME-11", "version": 3, "state": "dropped"},
        ],
        "issued_at": 12_000,
        "nonce": "closure_nonce_123456",
    }


def epic_closure_raw(*, replayed=True):
    return {
        "schema_version": EPIC_CLOSURE_CONTRACT,
        "outcome": {
            "receipt": closure_receipt_raw(),
            "closed_parent_version": 5,
            "audit_id": "17",
            "replayed": replayed,
        },
    }


def test_issue_projection_retains_provider_concurrency_version():
    assert DevHubTracker._to_issue(issue_raw(version=17)).version == 17


class ScriptedTracker(DevHubTracker):
    def __init__(self, responses):
        super().__init__(
            url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
            now_ms=lambda: 1_000_000, nonce_factory=lambda: "canonical_nonce_123456",
            idempotency_factory=lambda: "idempotency123456",
        )
        self.responses = list(responses)
        self.calls = []

    def _req(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {path}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def closure_receipt_model():
    return EpicClosureReceipt(
        project_key="TRAME", project_id="42", parent_id="TRAME-9",
        parent_version=4, parent_type="Epic", parent_ac_done=2,
        parent_ac_total=2,
        children=(
            EpicClosureChild("TRAME-10", 2, "done"),
            EpicClosureChild("TRAME-11", 3, "dropped"),
        ),
        issued_at=12_000, nonce="closure_nonce_123456",
    )


def test_epic_closure_posts_exact_original_receipt_with_stable_replay_identity():
    tracker = ScriptedTracker([epic_closure_raw(replayed=False)])
    receipt = closure_receipt_model()

    outcome = tracker.close_epic(PROJECT, receipt)

    assert outcome == EpicClosureOutcome(receipt, 5, "17", replayed=False)
    request = {
        "schema_version": EPIC_CLOSURE_CONTRACT,
        "receipt": closure_receipt_raw(),
    }
    encoded = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    assert tracker.calls == [(
        "POST",
        "/projects/TRAME/epics/TRAME-9/closure",
        request,
        {
            "version": 4,
            "idempotent": True,
            "idempotency_key": f"foundry-epic-close-{hashlib.sha256(encoded).hexdigest()}",
            "response_contract": EPIC_CLOSURE_CONTRACT,
        },
    )]
    assert tracker.epic_closure_supported is True


def test_epic_closure_reader_returns_the_same_original_receipt_read_only():
    tracker = ScriptedTracker([epic_closure_raw()])

    outcome = tracker.get_epic_closure(PROJECT, "TRAME-9")

    assert outcome == EpicClosureOutcome(
        closure_receipt_model(), 5, "17", replayed=True,
    )
    assert tracker.calls == [(
        "GET",
        "/projects/TRAME/epics/TRAME-9/closure",
        None,
        {"response_contract": EPIC_CLOSURE_CONTRACT},
    )]


def test_epic_closure_reader_returns_none_only_for_typed_absence():
    absent = DevHubTrackerError(
        "GET", "/projects/TRAME/epics/TRAME-9/closure", 404,
        "epic_closure_unavailable",
    )
    tracker = ScriptedTracker([absent])
    assert tracker.get_epic_closure(PROJECT, "TRAME-9") is None

    conflict = DevHubTrackerError(
        "GET", "/projects/TRAME/epics/TRAME-9/closure", 409,
        "epic_closure_conflict",
    )
    tracker = ScriptedTracker([conflict])
    with pytest.raises(DevHubTrackerError, match="epic_closure_conflict"):
        tracker.get_epic_closure(PROJECT, "TRAME-9")

    wrong_absence = DevHubTrackerError(
        "GET", "/projects/TRAME/epics/TRAME-9/closure", 404,
        "route_unavailable",
    )
    tracker = ScriptedTracker([wrong_absence])
    with pytest.raises(DevHubTrackerError, match="route_unavailable"):
        tracker.get_epic_closure(PROJECT, "TRAME-9")


@pytest.mark.parametrize(("path", "value"), [
    (("unexpected",), "member"),
    (("schema_version",), "devhub-epic-closure.v0"),
    (("outcome", "receipt", "project_id"), "41"),
    (("outcome", "receipt", "parent_id"), "OTHER-9"),
    (("outcome", "receipt", "parent_version"), True),
    (("outcome", "receipt", "parent_type"), "Feature"),
    (("outcome", "receipt", "parent_ac_done"), 1),
    (("outcome", "receipt", "children", 0, "state"), "open"),
    (("outcome", "receipt", "issued_at"), 9_007_199_254_740_992),
    (("outcome", "receipt", "nonce"), "short"),
    (("outcome", "closed_parent_version"), 6),
    (("outcome", "audit_id"), "invalid audit"),
    (("outcome", "replayed"), False),
], ids=[
    "unknown-member", "wrong-contract", "cross-project-id", "cross-project-epic",
    "boolean-version", "wrong-parent-type", "incomplete-ac", "open-child",
    "unsafe-time", "invalid-nonce", "non-unit-version-advance",
    "invalid-audit", "not-replayed",
])
def test_epic_closure_parser_rejects_malformed_or_mismatched_envelopes(
    path, value,
):
    raw = copy.deepcopy(epic_closure_raw())
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    tracker = ScriptedTracker([raw])

    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        tracker.get_epic_closure(PROJECT, "TRAME-9")


def test_epic_closure_rejects_noncanonical_or_oversized_child_sets():
    reversed_children = epic_closure_raw()
    reversed_children["outcome"]["receipt"]["children"].reverse()
    tracker = ScriptedTracker([reversed_children])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        tracker.get_epic_closure(PROJECT, "TRAME-9")

    oversized = epic_closure_raw()
    oversized["outcome"]["receipt"]["children"] = [
        {"id": f"TRAME-{index}", "version": 1, "state": "done"}
        for index in range(100, 201)
    ]
    tracker = ScriptedTracker([oversized])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        tracker.get_epic_closure(PROJECT, "TRAME-9")


def test_epic_closure_post_refuses_a_changed_provider_receipt():
    changed = epic_closure_raw(replayed=False)
    changed["outcome"]["receipt"]["issued_at"] += 1
    tracker = ScriptedTracker([changed])

    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        tracker.close_epic(PROJECT, closure_receipt_model())

    assert len(tracker.calls) == 1


def test_epic_closure_refuses_cross_project_request_before_http():
    tracker = ScriptedTracker([])

    with pytest.raises(SystemExit, match="Binding DevHub refusé"):
        tracker.get_epic_closure(Project(key="OTHER", id="99"), "TRAME-9")

    assert tracker.calls == []


def decode_proof(token):
    encoded, signature = token.split(".")
    expected = hmac.new(SECRET.encode(), encoded.encode(), hashlib.sha256).digest()
    padding = "=" * (-len(signature) % 4)
    assert hmac.compare_digest(base64.urlsafe_b64decode(signature + padding), expected)
    payload_padding = "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded + payload_padding))


def test_factory_instantiates_devhub_without_changing_other_providers(monkeypatch):
    monkeypatch.setattr(
        "foundry.registry.repository_tracker_binding", lambda _cwd=None: None,
    )
    monkeypatch.setattr(foundry.config, "tracker_name", lambda: "devhub")
    monkeypatch.setattr(
        foundry.config, "require_public", lambda key: "http://127.0.0.1:3000",
    )
    monkeypatch.setattr(foundry.config, "require", lambda key: {
        "DEVHUB_TRACKER_TOKEN": TOKEN,
        "DEVHUB_TRACKER_PROOF_SECRET": SECRET,
    }[key])

    assert isinstance(foundry.tracker(), DevHubTracker)
    assert foundry.tracker("ghprojects").name == "ghprojects"


def test_resolution_cross_checks_registry_and_remote(monkeypatch):
    registered = Project(
        key="TRAME", id="42",
        extra={"canonical_repo": "github.com/acme/trame"},
    )
    monkeypatch.setattr("foundry.trackers.devhub.registry.resolve", lambda *_: registered)
    tracker = ScriptedTracker([{
        "key": "TRAME", "id": "42",
        "extra": {"canonical_repo": "github.com/acme/trame"},
    }])

    assert tracker.resolve_project("trame").key == "TRAME"
    assert "repo=github.com%2Facme%2Ftrame" in tracker.calls[0][1]

    contradictory = ScriptedTracker([{"key": "OTHER", "id": "42", "extra": {}}])
    with pytest.raises(SystemExit, match="contradictoire"):
        contradictory.resolve_project("trame")


def test_resolution_transmits_only_sanitized_legacy_canonical_repository(monkeypatch):
    registered = Project(
        key="TRAME", id="42",
        extra={
            "canonical_repo": (
                "https://credential-user:PLAINTEXT_SENTINEL@github.com/Acme/Trame.git"
            ),
        },
    )
    monkeypatch.setattr("foundry.trackers.devhub.registry.resolve", lambda *_: registered)
    tracker = ScriptedTracker([{
        "key": "TRAME", "id": "42",
        "extra": {"canonical_repo": "github.com/acme/trame"},
    }])

    assert tracker.resolve_project("trame").key == "TRAME"

    request_path = tracker.calls[0][1]
    assert request_path == "/projects/resolve?repo=github.com%2Facme%2Ftrame"
    assert "credential-user" not in request_path
    assert "PLAINTEXT_SENTINEL" not in request_path


def test_resolution_refuses_invalid_legacy_canonical_repository_before_request(
    monkeypatch,
):
    registered = Project(
        key="TRAME", id="42",
        extra={
            "canonical_repo": (
                "https://credential-user:PLAINTEXT_SENTINEL@github.com/acme/trame"
                "?token=PLAINTEXT_SENTINEL"
            ),
        },
    )
    monkeypatch.setattr("foundry.trackers.devhub.registry.resolve", lambda *_: registered)
    tracker = ScriptedTracker([])

    with pytest.raises(SystemExit) as error:
        tracker.resolve_project("trame")

    assert "canonical_repo" in str(error.value)
    assert "PLAINTEXT_SENTINEL" not in str(error.value)
    assert tracker.calls == []


def test_same_basename_different_owner_refuses_before_any_mutation(monkeypatch):
    registered = Project(
        key="TRAME", id="42",
        extra={"canonical_repo": "github.com/acme/trame"},
    )
    tracker = ScriptedTracker([])
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "trame")
    monkeypatch.setattr(
        write.registry, "checkout_repository_identity",
        lambda: "github.com/other-owner/trame",
    )
    monkeypatch.setattr(
        "foundry.trackers.devhub.registry.resolve", lambda *_args: registered,
    )

    with pytest.raises(SystemExit, match="canonical_repo"):
        write.set_field(tracker, "TRAME-1", "Priority", "P1")

    assert tracker.calls == []


def _foreign_same_basename_tracker(monkeypatch):
    registered = Project(
        key="TRAME", id="42",
        extra={"canonical_repo": "github.com/acme/trame"},
    )
    tracker = ScriptedTracker([])
    monkeypatch.setattr(foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "trame")
    monkeypatch.setattr(
        write.registry, "checkout_repository_identity",
        lambda: "github.com/other-owner/trame",
    )
    monkeypatch.setattr(
        "foundry.trackers.devhub.registry.resolve", lambda *_args: registered,
    )
    return tracker


def test_create_issue_refuses_same_basename_foreign_origin_before_tracker_mutation(
    monkeypatch,
):
    tracker = _foreign_same_basename_tracker(monkeypatch)

    with pytest.raises(SystemExit, match="canonical_repo"):
        edit.create_issue(json.dumps({"title": "Foreign checkout"}))

    assert tracker.calls == []


def test_create_adr_refuses_same_basename_foreign_origin_before_tracker_mutation(
    monkeypatch,
):
    tracker = _foreign_same_basename_tracker(monkeypatch)
    monkeypatch.setattr(adr.sys, "stdin", io.StringIO("Decision"))

    with pytest.raises(SystemExit, match="canonical_repo"):
        adr.create("Boundary")

    assert tracker.calls == []


def test_frame_refuses_same_basename_foreign_origin_before_tracker_mutation(monkeypatch):
    tracker = _foreign_same_basename_tracker(monkeypatch)
    spec = {
        "adrs": [{"title": "Boundary", "body": "Decision"}],
        "epic": {"title": "Pilot"},
        "issues": [{"title": "First slice"}],
    }

    with pytest.raises(SystemExit, match="canonical_repo"):
        frame.materialize(spec)

    assert tracker.calls == []


@pytest.mark.parametrize("checkout", [
    "https://github.com/acme/trame.git",
    "git@github.com:acme/trame.git",
    "ssh://work/acme/trame.git",
    "work:acme/trame.git",
])
def test_matching_remote_forms_pass_mutation_binding(monkeypatch, checkout):
    registered = Project(
        key="TRAME", id="42",
        extra={"canonical_repo": "github.com/acme/trame"},
    )
    tracker = ScriptedTracker([{
        "key": "TRAME", "id": "42",
        "extra": {"canonical_repo": "github.com/acme/trame"},
    }])
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "trame")
    monkeypatch.setattr(
        write.registry, "_expand_ssh_alias",
        lambda host: "github.com" if host == "work" else pytest.fail(host),
    )
    monkeypatch.setattr(
        write.registry, "checkout_repository_identity",
        lambda: write.registry.canonical_repository_identity(checkout),
    )
    monkeypatch.setattr(
        "foundry.trackers.devhub.registry.resolve", lambda *_args: registered,
    )

    project = write.mutation_project(tracker)

    assert project.key == "TRAME"
    assert tracker.calls[0][:2] == (
        "GET", "/projects/resolve?repo=github.com%2Facme%2Ftrame",
    )


@pytest.mark.parametrize("checkout", [
    "ssh://github.com-evil/acme/trame.git",
    "git@github.com-evil:acme/trame.git",
])
def test_hostile_ssh_hostname_refuses_before_any_tracker_effect(monkeypatch, checkout):
    registered = Project(
        key="TRAME", id="42",
        extra={"canonical_repo": "github.com/acme/trame"},
    )
    tracker = ScriptedTracker([])
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "trame")
    monkeypatch.setattr(
        write.registry, "checkout_repository_identity",
        lambda: write.registry.canonical_repository_identity(checkout),
    )
    monkeypatch.setattr(
        "foundry.trackers.devhub.registry.resolve", lambda *_args: registered,
    )

    with pytest.raises(SystemExit, match="canonical_repo"):
        write.set_field(tracker, "TRAME-1", "Priority", "P1")

    assert tracker.calls == []


def test_search_consumes_every_bounded_page_without_duplicates():
    tracker = ScriptedTracker([
        {"items": [issue_raw(body=None, comments=None)],
         "page": {"schema_version": "devhub-application.v1", "returned_count": 1,
                  "truncation": True, "cursor": 4}},
        {"items": [issue_raw(id="TRAME-2", title="Second", version=1, body=None, comments=None)],
         "page": {"schema_version": "devhub-application.v1", "returned_count": 1,
                  "truncation": False, "cursor": None}},
    ])

    issues = tracker.search(Project(key="TRAME", id="42"))

    assert [item.id for item in issues] == ["TRAME-1", "TRAME-2"]
    assert "cursor=0" in tracker.calls[0][1]
    assert "cursor=4" in tracker.calls[1][1]


def test_project_catalog_uses_its_own_contract_and_is_not_a_mutation_binding():
    first = {
        "id": "41", "key": "TRAME", "title": "Trame", "state": "active",
        "freshness_at": 10,
    }
    second = {
        "id": "42", "key": "OTHER", "title": "Other", "state": "active",
        "freshness_at": None,
    }
    tracker = ScriptedTracker([
        {
            "schema_version": "devhub-project-catalog.v1", "items": [first],
            "page": {"returned_count": 1, "truncation": True, "cursor": "next"},
        },
        {
            "schema_version": "devhub-project-catalog.v1", "items": [second],
            "page": {"returned_count": 1, "truncation": False, "cursor": None},
        },
    ])

    assert tracker.list_project_catalog("Trame") == [first, second]
    assert "query=Trame" in tracker.calls[0][1]
    assert "cursor=next" in tracker.calls[1][1]
    assert all(call[3]["response_contract"] == "devhub-project-catalog.v1"
               for call in tracker.calls)
    assert all("canonical_repo" not in item for item in [first, second])


def test_catalog_rejects_a_silent_continuation_or_unexpected_binding_data():
    tracker = ScriptedTracker([{
        "schema_version": "devhub-project-catalog.v1",
        "items": [{
            "id": "41", "key": "TRAME", "title": "Trame", "state": "active",
            "freshness_at": None, "canonical_repo": "github.com/acme/trame",
        }],
        "page": {"returned_count": 1, "truncation": False, "cursor": None},
    }])

    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        tracker.list_project_catalog()


def test_search_graph_drives_blockers_next_and_roadmap(monkeypatch):
    tracker = ScriptedTracker([{
        "items": [
            issue_raw(
                id="TRAME-1", title="Blocked", state="ready", milestone="Pilot",
                body=None, comments=None,
                links=[{"type": "depends-on", "direction": "outward", "target": "TRAME-2"}],
            ),
            issue_raw(
                id="TRAME-2", title="Dependency", state="in-progress", milestone="Pilot",
                body=None, comments=None,
                links=[{"type": "depends-on", "direction": "inward", "target": "TRAME-1"}],
            ),
        ],
        "page": {"schema_version": "devhub-application.v1", "returned_count": 2,
                 "truncation": False, "cursor": None},
    }])
    issues = tracker.search(PROJECT)

    assert issues[0].links[0].type == "depends-on"
    assert issues[1].links[0].type == "blocks"
    annotated = {item["id"]: item for item in query._annotate(issues)}
    assert annotated["TRAME-1"]["blocked_by"] == ["TRAME-2"]
    assert annotated["TRAME-2"]["unlocks"] == ["TRAME-1"]

    graph_tracker = SimpleNamespace(
        name="devhub",
        resolve_project=lambda _repo: PROJECT,
        search=lambda _project: issues,
    )
    monkeypatch.setattr("foundry.query.foundry.tracker", lambda: graph_tracker)
    monkeypatch.setattr("foundry.query.registry.repo_basename", lambda: "trame")

    backlog = {item["id"]: item for item in query.backlog()["issues"]}
    candidates = {item["id"]: item for item in query.candidates()["issues"]}
    roadmap = query.milestones()["milestones"]["Pilot"]
    assert backlog["TRAME-1"]["blocked_by"] == ["TRAME-2"]
    assert backlog["TRAME-2"]["unlocks"] == ["TRAME-1"]
    assert candidates["TRAME-1"]["unblocked"] is False
    assert roadmap["blocked_ids"] == ["TRAME-1"]


def test_review_done_and_adr_proofs_are_bound_and_signed():
    reviewed = issue_raw(state="review", version=4)
    done = issue_raw(state="done", version=5)
    tracker = ScriptedTracker([
        issue_raw(), reviewed,
        reviewed, done,
        {"items": [{"id": "TRAME-ADR-0001", "title": "Boundary", "status": "proposed",
                    "ref": "1", "version": 1}],
         "page": {"schema_version": "devhub-application.v1", "returned_count": 1,
                  "truncation": False, "cursor": None}},
        {"id": "TRAME-ADR-0001", "title": "Boundary", "status": "accepted",
         "body": "Decision", "ref": "1", "version": 2},
    ])
    review = TransitionContext(
        pr_url=issue_raw()["pr_url"], head_sha="a" * 40,
        base_sha="d" * 40, review_digest="b" * 64,
    )
    done_context = TransitionContext(
        pr_url=review.pr_url, head_sha=review.head_sha, base_sha=review.base_sha,
        review_digest=review.review_digest,
        merge_sha="c" * 40,
    )

    tracker.set_state("TRAME-1", "review", context=review, project=PROJECT)
    tracker.set_state("TRAME-1", "done", context=done_context, project=PROJECT)
    tracker.set_adr_status(
        Adr(id="TRAME-ADR-0001", title="Boundary"), "accepted", project=PROJECT,
    )

    review_call, done_call, adr_call = tracker.calls[1], tracker.calls[3], tracker.calls[5]
    review_proof = decode_proof(review_call[3]["proof"])
    done_proof = decode_proof(done_call[3]["proof"])
    adr_proof = decode_proof(adr_call[3]["proof"])
    assert review_proof == {
        "action": "review", "contract_version": "devhub-tracker.v1",
        "expected_version": 3, "expires_at": 1_300_000,
        "head_sha": "a" * 40, "base_sha": "d" * 40,
        "nonce": "canonical_nonce_123456",
        "pr_url": review.pr_url, "project_key": "TRAME",
        "resource_id": "TRAME-1", "review_digest": "b" * 64,
    }
    assert done_proof["merge_sha"] == "c" * 40
    assert done_proof["expected_version"] == 4
    assert adr_proof["target_status"] == "accepted"
    assert all(call[3]["idempotent"] is True for call in (review_call, done_call, adr_call))


class FakeResponse:
    status = 200
    headers = {"X-DevHub-Contract": "devhub-tracker.v1"}

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return json.dumps(self.payload).encode()


def test_http_transport_sends_bearer_but_never_exposes_provider_body(monkeypatch):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/fail"):
                raise urllib.error.HTTPError(
                    request.full_url, 503, "no", {},
                    io.BytesIO(b'{"error":{"code":"tracker_unavailable","message":"SENTINEL_SECRET"}}'),
                )
            return FakeResponse({"ok": True})

    monkeypatch.setattr("foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener())
    tracker = DevHubTracker(url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET)

    assert tracker._req("GET", "/ok") == {"ok": True}
    assert requests[0][0].get_header("Authorization") == f"Bearer {TOKEN}"
    with pytest.raises(DevHubTrackerError) as captured:
        tracker._req("GET", "/fail")
    assert captured.value.status == 503
    assert captured.value.code == "tracker_unavailable"
    assert "SENTINEL_SECRET" not in str(captured.value)
    assert TOKEN not in str(captured.value)
    assert SECRET not in str(captured.value)


def test_application_routes_require_the_published_contract_without_fallback(monkeypatch):
    requests = []

    class ApplicationResponse(FakeResponse):
        headers = {"X-DevHub-Contract": "devhub-application.v1"}

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return ApplicationResponse({"ok": True})

    monkeypatch.setattr("foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener())
    tracker = DevHubTracker(url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET)

    assert tracker._application_req("GET", "/projects/resolve?repo=github.com%2Facme%2Ftrame") == {"ok": True}
    assert requests[0].get_header("Authorization") == f"Bearer {TOKEN}"

    class LegacyOpener:
        def open(self, _request, timeout):
            return FakeResponse({"ok": True})

    monkeypatch.setattr("foundry.trackers.devhub.urllib.request.build_opener", lambda *_: LegacyOpener())
    with pytest.raises(DevHubTrackerError, match="contract_mismatch"):
        tracker._application_req("GET", "/projects/resolve?repo=github.com%2Facme%2Ftrame")


def test_epic_closure_absence_requires_its_versioned_error_contract(monkeypatch):
    class Opener:
        def __init__(self, contract):
            self.contract = contract

        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 404, "no",
                {"X-DevHub-Contract": self.contract},
                io.BytesIO(json.dumps({
                    "schema_version": EPIC_CLOSURE_CONTRACT,
                    "error": {
                        "code": "epic_closure_unavailable",
                        "message": "Epic closure unavailable",
                    },
                }).encode()),
            )

    tracker = DevHubTracker(
        url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
    )
    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.build_opener",
        lambda *_args: Opener("devhub-application.v1"),
    )
    with pytest.raises(DevHubTrackerError, match="contract_mismatch"):
        tracker.get_epic_closure(PROJECT, "TRAME-9")

    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.build_opener",
        lambda *_args: Opener(EPIC_CLOSURE_CONTRACT),
    )
    assert tracker.get_epic_closure(PROJECT, "TRAME-9") is None


def test_remote_https_and_canonical_loopback_http_are_allowed():
    assert DevHubTracker(
        url="https://devhub.example", token=TOKEN, proof_secret=SECRET,
    ).url == "https://devhub.example"
    assert DevHubTracker(
        url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
    ).url == "http://127.0.0.1:3000"
    assert DevHubTracker(
        url="http://[::1]:3000", token=TOKEN, proof_secret=SECRET,
    ).url == "http://[::1]:3000"


@pytest.mark.parametrize("url", [
    "http://devhub.example:3000",
    "http://192.0.2.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1.evil.example:3000",
    "http://127.1:3000",
    "http://2130706433:3000",
    "http://0177.0.0.1:3000",
    "http://[::ffff:127.0.0.1]:3000",
    "http://[::1%25lo0]:3000",
])
def test_remote_or_deceptive_http_is_refused_before_credentials_or_requests(
    monkeypatch, url,
):
    credential_reads = []
    request_builds = []
    monkeypatch.setattr(
        "foundry.trackers.devhub.config.require",
        lambda key: credential_reads.append(key) or "must-not-be-read",
    )
    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.Request",
        lambda *_args, **_kwargs: request_builds.append(True),
    )

    with pytest.raises(ValueError, match="HTTPS"):
        DevHubTracker(url=url)

    assert credential_reads == []
    assert request_builds == []


def test_configured_url_is_validated_before_any_secret_resolution(monkeypatch):
    secret_reads = []
    monkeypatch.setattr(
        "foundry.trackers.devhub.config.require_public",
        lambda key: "http://devhub.example:3000",
    )
    monkeypatch.setattr(
        "foundry.trackers.devhub.config.require",
        lambda key: secret_reads.append(key) or "must-not-be-read",
    )

    with pytest.raises(ValueError, match="HTTPS"):
        DevHubTracker()

    assert secret_reads == []


def test_redirect_handler_refuses_cross_origin_without_forwarding_secret():
    handler = _SameOriginRedirectHandler()
    request = urllib.request.Request("https://devhub.example/api")
    request.add_header("Authorization", f"Bearer {TOKEN}")

    with pytest.raises(DevHubTrackerError, match="cross_origin_redirect"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/steal")


def test_redirect_handler_refuses_a_same_origin_remote_http_request():
    handler = _SameOriginRedirectHandler()
    request = urllib.request.Request("http://devhub.example/api")
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("X-Foundry-Proof", "proof-sentinel-must-not-leak")

    with pytest.raises(DevHubTrackerError, match="cross_origin_redirect") as captured:
        handler.redirect_request(request, None, 302, "Found", {}, "/redirected")

    rendered = f"{captured.value!s}\n{captured.value!r}"
    assert TOKEN not in rendered
    assert "proof-sentinel-must-not-leak" not in rendered


@pytest.mark.parametrize(("source", "target"), [
    ("https://devhub.example/api", "/redirected"),
    ("http://127.0.0.1:3000/api", "http://127.0.0.1:3000/redirected"),
    ("http://[::1]:3000/api", "http://[::1]:3000/redirected"),
])
def test_redirect_preserves_secrets_only_for_a_valid_same_origin(source, target):
    handler = _SameOriginRedirectHandler()
    request = urllib.request.Request(source)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("X-Foundry-Proof", "proof-sentinel")

    redirected = handler.redirect_request(request, None, 302, "Found", {}, target)

    assert redirected.get_header("Authorization") == f"Bearer {TOKEN}"
    assert redirected.get_header("X-foundry-proof") == "proof-sentinel"


def test_redirect_refuses_https_downgrade_before_forwarding_secrets(monkeypatch):
    sent = []
    proof = "proof-sentinel-must-not-leak"

    class RedirectingOpener:
        def __init__(self, handler):
            self.handler = handler

        def open(self, request, timeout):
            sent.append(request)
            redirected = self.handler.redirect_request(
                request, None, 302, "Found", {},
                "http://devhub.example/api/tracker/v1/steal",
            )
            sent.append(redirected)
            raise AssertionError("unsafe redirect must not be followed")

    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.build_opener",
        lambda handler: RedirectingOpener(handler),
    )
    tracker = DevHubTracker(
        url="https://devhub.example", token=TOKEN, proof_secret=SECRET,
    )

    with pytest.raises(DevHubTrackerError, match="cross_origin_redirect") as captured:
        tracker._req("POST", "/redirect", {"value": "body-sentinel"}, proof=proof)

    assert [request.full_url for request in sent] == [
        "https://devhub.example/api/tracker/v1/redirect",
    ]
    rendered = f"{captured.value!s}\n{captured.value!r}"
    assert TOKEN not in rendered
    assert SECRET not in rendered
    assert proof not in rendered


def test_port_methods_round_trip_normalized_shapes():
    created = issue_raw(state="backlog", version=1)
    updated = issue_raw(priority="P1", labels=["one", "two"], version=2)
    target = issue_raw(id="TRAME-2", version=5)
    linked = issue_raw(priority="P1", labels=["one", "two"], version=3)
    adr = {"id": "TRAME-ADR-0001", "title": "Boundary", "status": "proposed",
           "body": "Decision", "ref": "9", "version": 1}
    tracker = ScriptedTracker([
        created, created,
        created, updated, updated,
        updated, target, linked,
        linked, {"id": 1, "text": "Progress", "created": 3, "version": 4},
        {"items": [{k: v for k, v in adr.items() if k != "body"}],
         "page": {"schema_version": "devhub-application.v1", "returned_count": 1,
                  "truncation": False, "cursor": None}},
        adr, adr,
    ])
    project = Project(key="TRAME", id="42")

    assert tracker.create_issue(
        project, "Pilot", "- [ ] pilot",
        fields={"State": "backlog", "Priority": "P0", "Type": "Feature"},
        parent="TRAME-9",
    ).state == "backlog"
    assert tracker.update_fields(
        "TRAME-1", {"Priority": "P1", "Labels": ["one", "two"]}, project=project,
    ).labels == ["one", "two"]
    tracker.link("TRAME-1", "depends-on", "TRAME-2", project=project)
    tracker.add_comment("TRAME-1", "Progress", project=project)
    assert tracker.list_adrs(project)[0].id == "TRAME-ADR-0001"
    assert tracker.create_adr(project, "Boundary", "Decision").status == "proposed"

    assert tracker.calls[1][2]["parent"] == "TRAME-9"
    assert tracker.calls[1][2]["parent_version"] == 1
    assert tracker.calls[3][3]["version"] == 1
    assert tracker.calls[7][2] == {
        "type": "depends-on", "target": "TRAME-2", "target_version": 5,
    }
    assert tracker.calls[7][3]["version"] == 2
    assert tracker.calls[9][2] == {"text": "Progress"}
    assert tracker.calls[9][3]["version"] == 3


def test_ambiguous_mutation_retries_once_with_the_same_idempotency_key(monkeypatch):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request, timeout))
            if len(requests) == 1:
                raise urllib.error.URLError("response lost")
            return FakeResponse({"ok": True})

    monkeypatch.setattr("foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener())
    tracker = DevHubTracker(
        url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
        idempotency_factory=lambda: "stable-retry-key",
    )

    assert tracker._req("POST", "/comments", {"text": "one"}, idempotent=True) == {"ok": True}
    assert len(requests) == 2
    assert requests[0][0].get_header("Idempotency-key") == requests[1][0].get_header("Idempotency-key")


def test_malformed_shapes_and_unbounded_pagination_fail_closed():
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        DevHubTracker._to_issue(issue_raw(labels="not-a-list"))

    pages = []
    next_cursor = 0
    for page in range(11):
        start = page * 100 + 1
        items = [issue_raw(id=f"TRAME-{number}") for number in range(start, start + 100)]
        next_cursor += 100
        pages.append({
            "items": items,
            "page": {"schema_version": "devhub-application.v1",
                     "returned_count": len(items), "truncation": True,
                     "cursor": next_cursor},
        })
    tracker = ScriptedTracker(pages)

    with pytest.raises(DevHubTrackerError, match="pagination_limit"):
        tracker.search(Project(key="TRAME", id="42"))


def test_every_id_mutation_requires_the_current_project_binding():
    tracker = ScriptedTracker([])
    adr = Adr(id="TRAME-ADR-0001", title="Boundary")
    unbound = [
        lambda: tracker.update_fields("TRAME-1", {"Priority": "P1"}),
        lambda: tracker.set_state("TRAME-1", "ready"),
        lambda: tracker.link("TRAME-1", "depends-on", "TRAME-2"),
        lambda: tracker.add_comment("TRAME-1", "Progress"),
        lambda: tracker.set_adr_status(adr, "accepted"),
    ]
    for mutation in unbound:
        with pytest.raises(ValueError, match="binding projet"):
            mutation()

    other = Project(key="OTHER", id="99")
    mismatched = [
        lambda: tracker.update_fields("TRAME-1", {"Priority": "P1"}, project=other),
        lambda: tracker.set_state("TRAME-1", "ready", project=other),
        lambda: tracker.link("TRAME-1", "depends-on", "TRAME-2", project=other),
        lambda: tracker.add_comment("TRAME-1", "Progress", project=other),
        lambda: tracker.set_adr_status(adr, "accepted", project=other),
        lambda: tracker.create_issue(PROJECT, "Child", "", parent="OTHER-2"),
        lambda: tracker.link("TRAME-1", "depends-on", "OTHER-2", project=PROJECT),
    ]
    for mutation in mismatched:
        with pytest.raises(SystemExit, match="Binding DevHub refusé"):
            mutation()
    assert tracker.calls == []


def test_write_seam_resolves_binding_without_provider_name_branch(monkeypatch):
    events = []

    def resolve(repo):
        events.append(("resolve", repo))
        return PROJECT

    tracker = SimpleNamespace(
        requires_mutation_binding=True,
        bounded_transition_proofs=True,
        resolve_project=resolve,
        validate_issue_binding=lambda _project, *_ids: None,
        validate_adr_binding=lambda _project, *_ids: None,
        update_fields=lambda issue_id, fields, project=None: events.append(
            ("update", issue_id, fields, project)
        ),
        set_state=lambda issue_id, state, context=None, project=None: events.append(
            ("state", issue_id, state, context, project)
        ),
        link=lambda src, kind, dst, project=None: events.append(
            ("link", src, kind, dst, project)
        ),
        add_comment=lambda issue_id, text, project=None: events.append(
            ("comment", issue_id, text, project)
        ),
        set_adr_status=lambda adr, status, project=None: events.append(
            ("adr", adr.id, status, project)
        ),
    )
    monkeypatch.setattr(write.registry, "repo_basename", lambda: "trame")

    write.set_field(tracker, "TRAME-1", "Priority", "P1")
    write.transition(tracker, "TRAME-1", "ready")
    write.link(tracker, "TRAME-1", "depends-on", "TRAME-2")
    write.add_comment(tracker, "TRAME-1", "Progress")
    write.set_adr_status(tracker, Adr(id="TRAME-ADR-0001", title="Boundary"), "accepted")

    assert [event[0] for event in events].count("resolve") == 5
    assert all(event[-1] == PROJECT for event in events if event[0] != "resolve")

    before = list(events)
    with pytest.raises(SystemExit, match="flux mécanique openpr"):
        write.transition(tracker, "TRAME-1", "review")
    assert events == before


def test_adapter_never_derives_review_proof_from_local_git():
    tracker = ScriptedTracker([])

    with pytest.raises(ValueError, match="preuve review incomplète"):
        tracker.set_state("TRAME-1", "review", project=PROJECT)

    assert tracker.calls == []


def test_link_and_comment_send_fresh_if_match_and_propagate_stale_version():
    conflict = DevHubTrackerError("POST", "/issues/TRAME-1/comments", 409, "version_conflict")
    tracker = ScriptedTracker([issue_raw(version=7), conflict])

    with pytest.raises(DevHubTrackerError) as captured:
        tracker.add_comment("TRAME-1", "stale", project=PROJECT)

    assert captured.value.status == 409
    assert captured.value.code == "version_conflict"
    assert tracker.calls[1][3]["version"] == 7


def test_link_sends_canonical_header_and_retries_with_one_idempotency_key(monkeypatch):
    requests = []

    class ApplicationResponse(FakeResponse):
        headers = {"X-DevHub-Contract": "devhub-application.v1"}

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.URLError("response lost after provider write")
            return ApplicationResponse({"id": "TRAME-1", "version": 8})

    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener(),
    )
    tracker = DevHubTracker(
        url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
    )
    monkeypatch.setattr(tracker, "_require_issue_binding", lambda *_args: "TRAME")
    versions = {"TRAME-1": 7, "TRAME-2": 11}
    monkeypatch.setattr(
        tracker, "_bound_issue_raw",
        lambda issue_id, _project: {"version": versions[issue_id]},
    )

    tracker.link("TRAME-1", "depends-on", "TRAME-2", project=PROJECT)

    assert len(requests) == 2
    assert all(request.get_header("X-devhub-version") == "7" for request in requests)
    assert all(request.get_header("If-match") == '"7"' for request in requests)
    assert [json.loads(request.data) for request in requests] == [
        {"type": "depends-on", "target": "TRAME-2", "target_version": 11},
        {"type": "depends-on", "target": "TRAME-2", "target_version": 11},
    ]
    replay_keys = [request.get_header("Idempotency-key") for request in requests]
    assert replay_keys[0] == replay_keys[1]
    assert replay_keys[0].startswith("foundry-")


@pytest.mark.parametrize(("status", "code"), [
    (428, "version_required"),
    (400, "invalid_request"),
    (409, "version_conflict"),
])
def test_versioned_mutation_preserves_explicit_version_refusals(
    monkeypatch, status, code,
):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            raise urllib.error.HTTPError(
                request.full_url, status, "version refused",
                {"X-DevHub-Contract": "devhub-application.v1"},
                io.BytesIO(json.dumps({"error": {"code": code}}).encode()),
            )

    monkeypatch.setattr(
        "foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener(),
    )
    tracker = DevHubTracker(
        url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET,
    )

    with pytest.raises(DevHubTrackerError) as captured:
        tracker._application_req(
            "POST", "/issues/TRAME-1/links",
            {"type": "depends-on", "target": "TRAME-2"},
            version=7, idempotent=True, idempotency_key="foundry-link-refusal-0001",
        )

    assert (captured.value.status, captured.value.code) == (status, code)
    assert len(requests) == 1
    assert requests[0].get_header("X-devhub-version") == "7"
    assert requests[0].get_header("If-match") == '"7"'


def test_acceptance_sync_uses_exact_body_version_and_idempotency_boundary():
    expected = "- [ ] first\n- [ ] second\n"
    updated = "- [x] first\n- [ ] second\n"
    tracker = ScriptedTracker([
        issue_raw(body=expected, version=7),
        issue_raw(body=updated, version=8),
    ])

    assert tracker.sync_acceptance_body(
        "TRAME-1", expected, updated, "a" * 64, project=PROJECT,
    ) is True
    assert tracker.calls == [
        ("GET", "/issues/TRAME-1", None, {
            "response_contract": "devhub-application.v1",
        }),
        ("PATCH", "/issues/TRAME-1", {"body": updated}, {
            "version": 7, "idempotent": True,
            "idempotency_key": f"foundry-ac-{'a' * 64}",
            "response_contract": "devhub-application.v1",
        }),
    ]


def test_acceptance_sync_is_idempotent_and_refuses_changed_body_before_patch():
    expected = "- [ ] contract\n"
    updated = "- [x] contract\n"
    already = ScriptedTracker([issue_raw(body=updated, version=8)])

    assert already.sync_acceptance_body(
        "TRAME-1", expected, updated, "b" * 64, project=PROJECT,
    ) is False
    assert [call[0] for call in already.calls] == ["GET"]

    changed = ScriptedTracker([issue_raw(body="human edit\n", version=9)])
    with pytest.raises(TrackerConflictError, match="corps DevHub modifié"):
        changed.sync_acceptance_body(
            "TRAME-1", expected, updated, "b" * 64, project=PROJECT,
        )
    assert [call[0] for call in changed.calls] == ["GET"]


def test_acceptance_sync_maps_provider_version_conflict_to_bounded_conflict():
    conflict = DevHubTrackerError(
        "PATCH", "/issues/TRAME-1", 409, "version_conflict",
    )
    tracker = ScriptedTracker([
        issue_raw(body="- [ ] contract\n", version=7), conflict,
    ])

    with pytest.raises(TrackerConflictError, match="version DevHub modifiée"):
        tracker.sync_acceptance_body(
            "TRAME-1", "- [ ] contract\n", "- [x] contract\n", "c" * 64,
            project=PROJECT,
        )


def test_transport_preserves_403_scope_code_without_provider_details(monkeypatch):
    sentinel = "PRIVATE_SCOPE_DETAIL"

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 403, "forbidden",
                {"X-DevHub-Contract": "devhub-tracker.v1"},
                io.BytesIO(json.dumps({
                    "error": {"code": "insufficient_scope", "message": sentinel},
                }).encode()),
            )

    monkeypatch.setattr("foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener())
    tracker = DevHubTracker(url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET)

    with pytest.raises(DevHubTrackerError) as captured:
        tracker._req("GET", "/projects/TRAME/audit")

    assert (captured.value.status, captured.value.code) == (403, "insufficient_scope")
    assert sentinel not in str(captured.value)


def test_explicit_idempotency_key_detects_divergent_replay(monkeypatch):
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if len(requests) == 1:
                return FakeResponse({"id": 1, "version": 2})
            raise urllib.error.HTTPError(
                request.full_url, 409, "conflict",
                {"X-DevHub-Contract": "devhub-tracker.v1"},
                io.BytesIO(b'{"error":{"code":"idempotency_conflict"}}'),
            )

    monkeypatch.setattr("foundry.trackers.devhub.urllib.request.build_opener", lambda *_: Opener())
    tracker = DevHubTracker(url="http://127.0.0.1:3000", token=TOKEN, proof_secret=SECRET)
    key = "fixed-divergent-replay-key"
    tracker._req(
        "POST", "/issues/TRAME-1/comments", {"text": "first"}, version=1,
        idempotent=True, idempotency_key=key,
    )

    with pytest.raises(DevHubTrackerError, match="idempotency_conflict"):
        tracker._req(
            "POST", "/issues/TRAME-1/comments", {"text": "different"}, version=1,
            idempotent=True, idempotency_key=key,
        )

    assert [request.get_header("Idempotency-key") for request in requests] == [key, key]
    assert requests[0].data != requests[1].data


def test_search_refuses_empty_advancing_page_and_hard_caps_page_count(monkeypatch):
    empty = ScriptedTracker([{
        "items": [],
        "page": {"schema_version": "devhub-application.v1", "returned_count": 0,
                 "truncation": True, "cursor": 1},
    }])
    with pytest.raises(DevHubTrackerError, match="pagination_empty_page"):
        empty.search(PROJECT)

    monkeypatch.setattr(devhub_module, "_MAX_SEARCH_PAGES", 2)
    pages = [
        {"items": [issue_raw(id=f"TRAME-{index}")],
         "page": {"schema_version": "devhub-application.v1", "returned_count": 1,
                  "truncation": True, "cursor": index}}
        for index in (1, 2)
    ]
    capped = ScriptedTracker(pages)
    with pytest.raises(DevHubTrackerError, match="pagination_page_limit"):
        capped.search(PROJECT)
    assert len(capped.calls) == 2


def test_public_audit_round_trips_exact_schema_and_refuses_empty_page():
    first = {
        "id": "10", "operation": "issue.comment", "resource": "issue:TRAME-1",
        "resource_version": 7, "result": "success", "created": 1_000,
    }
    second = {
        "id": "11", "operation": "issue.transition", "resource": "issue:TRAME-1",
        "resource_version": 8, "result": "success", "created": 1_001,
    }
    tracker = ScriptedTracker([
        {"items": [first], "page": {"schema_version": "devhub-application.v1",
                                     "returned_count": 1, "truncation": True,
                                     "cursor": "10"}},
        {"items": [second], "page": {"schema_version": "devhub-application.v1",
                                      "returned_count": 1, "truncation": False,
                                      "cursor": None}},
    ])

    assert tracker.audit(PROJECT, resource="issue:TRAME-1") == [first, second]
    assert "cursor=10" in tracker.calls[1][1]

    empty = ScriptedTracker([{
        "items": [],
        "page": {"schema_version": "devhub-application.v1", "returned_count": 0,
                 "truncation": True, "cursor": "12"},
    }])
    with pytest.raises(DevHubTrackerError, match="pagination_empty_page"):
        empty.audit(PROJECT)


def test_smoke_audit_verification_fails_on_missing_receipt():
    resource = "issue:TRAME-1"
    events = [{
        "id": "1", "operation": "issue.comment", "resource": resource,
        "resource_version": 2, "result": "success", "created": 1,
    }]

    _require_audit_receipts(events, resource=resource, operations={"issue.comment"})
    with pytest.raises(RuntimeError, match="issue.transition"):
        _require_audit_receipts(
            events, resource=resource, operations={"issue.comment", "issue.transition"},
        )
