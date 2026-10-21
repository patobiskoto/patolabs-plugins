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

from integration.test_real_terminal_reconciliation_smoke import _original_closure_wire
import foundry
import foundry.trackers.devhub as devhub_module
from foundry import acceptance_decision as decisions
from foundry import adr, edit, frame, query, write
from foundry.routing import acceptance_criteria, acceptance_digest
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
from foundry.trackers.base import TrackerCapabilityUnavailableError, TrackerConflictError


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


def test_legacy_epic_closure_receipt_keeps_devhub_wire_shape():
    receipt = closure_receipt_model()

    assert receipt.to_dict() == closure_receipt_raw()
    assert set(receipt.to_dict()) == {
        "project_key", "project_id", "parent_id", "parent_version",
        "parent_type", "parent_ac_done", "parent_ac_total", "children",
        "issued_at", "nonce",
    }


def test_late_terminal_smoke_serializes_outcome_receipt_on_legacy_wire():
    outcome = EpicClosureOutcome(
        closure_receipt_model(), closed_parent_version=5, audit_id="17", replayed=True,
    )

    wire = _original_closure_wire(outcome)
    assert wire == {
        "schema_version": EPIC_CLOSURE_CONTRACT,
        "outcome": {
            "receipt": closure_receipt_raw(),
            "closed_parent_version": 5,
            "audit_id": "17",
            "replayed": True,
        },
    }
    assert DevHubTracker._to_epic_closure_outcome(
        wire, project=PROJECT, parent_id=outcome.receipt.parent_id,
    ) == outcome


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


def test_epic_closure_over_children_that_carry_criteria_binds_id_version_and_state_only():
    # PAT-316: the children read by write.close_epic carry criteria (the done child's met,
    # the dropped child's open) but the wire receipt binds only what the server checks.
    receipt = closure_receipt_model()
    assert [child.state for child in receipt.children] == ["done", "dropped"]
    tracker = ScriptedTracker([epic_closure_raw(replayed=False)])
    assert tracker.close_epic(PROJECT, receipt).receipt == receipt
    sent = tracker.calls[0][2]["receipt"]["children"]
    assert sent == [
        {"id": "TRAME-10", "version": 2, "state": "done"},
        {"id": "TRAME-11", "version": 3, "state": "dropped"},
    ]
    assert all(set(child) == {"id", "version", "state"} for child in sent)
    # a receipt that still carries criteria counters is not the wire receipt: refused unsent
    with_counters = EpicClosureReceipt(
        project_key="TRAME", project_id="42", parent_id="TRAME-9", parent_version=4,
        parent_type="Epic", parent_ac_done=2, parent_ac_total=2,
        children=(
            EpicClosureChild("TRAME-10", 2, "done", ac_done=1, ac_total=1),
            EpicClosureChild("TRAME-11", 3, "dropped", ac_done=0, ac_total=1),
        ),
        issued_at=12_000, nonce="closure_nonce_123456",
    )
    refused = ScriptedTracker([])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        refused.close_epic(PROJECT, with_counters)
    assert refused.calls == []


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
        "foundry.registry.repository_tracker_selection",
        lambda _cwd=None, **_kwargs: {"mode": "unbound", "binding": None},
    )
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


def test_edit_set_field_labels_keeps_devhub_portable_list(monkeypatch):
    before = issue_raw(labels=["pilot"], version=1)
    after = issue_raw(labels=["api", "backend"], version=2)
    tracker = ScriptedTracker([before, after, after])
    monkeypatch.setattr(edit.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: PROJECT)

    edit.set_field("TRAME-1", "Labels", " api, backend ,, ")

    assert tracker.calls[1][2] == {
        "fields": {"labels": ["api", "backend"]},
    }


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


# --- PAT-309: DHN-38 proof receipts and DHN-39 Version scope -----------------------------

PAGE_V1 = "devhub-application.v1"


def receipt_raw(receipt_id, action="review", **overrides):
    value = {
        "id": str(receipt_id), "issue": "TRAME-1", "project": "TRAME", "action": action,
        "issue_version": 4, "pr_url": "https://github.com/acme/trame/pull/1",
        "head_sha": "a" * 40, "base_sha": "d" * 40, "review_digest": "b" * 64,
        "merge_sha": "c" * 40 if action == "done" else None,
        "proof_nonce": f"nonce_{receipt_id:0>12}", "actor": "foundry", "created": 5,
        "reason": None, "decision_digest": None, "decision": None,
    }
    value.update(overrides)
    return value


def receipt_page(items, *, cursor=None):
    return {"items": items, "page": {
        "schema_version": PAGE_V1, "returned_count": len(items),
        "truncation": cursor is not None, "cursor": cursor,
    }}


def test_devhub_declares_the_append_only_lifecycle_the_cadencer_reads():
    from foundry import campaign_cadence
    tracker = ScriptedTracker([])
    assert tracker.append_only_lifecycle_supported is True
    # DHN-37 (PAT-313): the reconciliation override is declared too, so nothing is missing.
    assert campaign_cadence.missing_capabilities(tracker) == ()


def test_proof_receipts_are_read_oldest_first_across_pages_and_validated():
    tracker = ScriptedTracker([
        receipt_page([receipt_raw(1)], cursor="1"),
        receipt_page([receipt_raw(2, "done")]),
    ])
    receipts = tracker.read_proof_receipts("TRAME-1", project=PROJECT)
    assert [item["id"] for item in receipts] == ["1", "2"]
    assert receipts[1]["merge_sha"] == "c" * 40
    assert tracker.calls[0][1].endswith("/issues/TRAME-1/proof-receipts?cursor=0&limit=100")
    assert "cursor=1" in tracker.calls[1][1]


@pytest.mark.parametrize("pages, code", [
    ([receipt_page([receipt_raw(1, project="OTHER")])], "invalid_response"),
    ([receipt_page([receipt_raw(1, issue="TRAME-2")])], "invalid_response"),
    ([receipt_page([receipt_raw(1, merge_sha="c" * 40)])], "invalid_response"),
    ([receipt_page([receipt_raw(1, "done", merge_sha=None)])], "invalid_response"),
    ([receipt_page([receipt_raw(1, head_sha="A" * 40)])], "invalid_response"),
    # PAT-318: the extended form refuses every other shape
    ([receipt_page([{k: v for k, v in receipt_raw(1).items() if k != "reason"}])],
     "invalid_response"),
    ([receipt_page([receipt_raw(1, extra_key=None)])], "invalid_response"),
    ([receipt_page([receipt_raw(1, "unknown_action")])], "invalid_response"),
    ([receipt_page([receipt_raw(1, reason="maintainer-override")])], "invalid_response"),
    ([receipt_page([receipt_raw(1, decision_digest="e" * 64)])], "invalid_response"),
    ([receipt_page([receipt_raw(1, decision={})])], "invalid_response"),
    ([receipt_page([receipt_raw(1, "acceptance_override")])], "invalid_response"),
    ([receipt_page([receipt_raw(1, "acceptance_override", reason="No")])], "invalid_response"),
    ([receipt_page([receipt_raw(1, "acceptance_override", reason="maintainer-override",
                                review_digest=None)])], "invalid_response"),
    ([receipt_page([receipt_raw(1, "acceptance_override", reason="maintainer-override",
                                merge_sha="c" * 40)])], "invalid_response"),
    ([receipt_page([receipt_raw(2), receipt_raw(1, proof_nonce="nonce_other_000000")])],
     "pagination_repeated"),
    ([receipt_page([receipt_raw(1)], cursor="9")], "pagination_stalled"),
    ([receipt_page([], cursor="1")], "pagination_empty_page"),
    ([{"items": [], "page": {"schema_version": "x", "returned_count": 0,
                             "truncation": False, "cursor": None}}], "invalid_response"),
])
def test_malformed_foreign_or_stalled_proof_receipts_fail_closed(pages, code):
    tracker = ScriptedTracker(pages)
    with pytest.raises(DevHubTrackerError, match=code):
        tracker.read_proof_receipts("TRAME-1", project=PROJECT)


def test_review_on_an_already_reviewed_issue_republishes_only_a_new_generation():
    review = TransitionContext(
        pr_url=issue_raw()["pr_url"], head_sha="a" * 40,
        base_sha="d" * 40, review_digest="b" * 64,
    )
    same = ScriptedTracker([issue_raw(state="review", version=4),
                            receipt_page([receipt_raw(1)])])
    same.set_state("TRAME-1", "review", context=review, project=PROJECT)
    assert [call[0] for call in same.calls] == ["GET", "GET"]  # no write, no new receipt

    newer = TransitionContext(
        pr_url=review.pr_url, head_sha="e" * 40, base_sha=review.base_sha,
        review_digest=review.review_digest,
    )
    tracker = ScriptedTracker([
        issue_raw(state="review", version=4), receipt_page([receipt_raw(1)]),
        issue_raw(state="in-progress", version=5),
        issue_raw(state="in-progress", version=5),
        issue_raw(state="review", version=6),
    ])
    tracker.set_state("TRAME-1", "review", context=newer, project=PROJECT)
    posts = [call for call in tracker.calls if call[0] == "POST"]
    assert [call[2] for call in posts] == [{"state": "in-progress"}, {"state": "review"}]
    assert posts[0][3]["version"] == 4 and "proof" not in posts[0][3]
    assert posts[1][3]["version"] == 5
    assert decode_proof(posts[1][3]["proof"])["head_sha"] == "e" * 40


def test_review_transition_proof_sets_the_issue_pr_url_without_a_field_write():
    # Mirrors DevHub `2edcea2:src/tracker/store.ts:1714-1716`: the proven transition
    # stores `pr_url = COALESCE(proof.pr_url, pr_url)`; no `GitHub PR` field is written.
    stored = issue_raw(state="in-progress", pr_url=None, version=3)

    class ServerLike(ScriptedTracker):
        def _req(self, method, path, body=None, **kwargs):
            self.calls.append((method, path, body, kwargs))
            if method == "POST" and body == {"state": "in-progress"}:
                stored.update(state="in-progress", version=stored["version"] + 1)
            elif method == "POST":
                stored.update(
                    state=body["state"], version=stored["version"] + 1,
                    pr_url=decode_proof(kwargs["proof"])["pr_url"],
                )
            return dict(stored)

    tracker = ServerLike([])
    first = TransitionContext(
        pr_url="https://github.com/acme/trame/pull/1", head_sha="a" * 40,
        base_sha="d" * 40, review_digest="b" * 64,
    )
    tracker.set_state("TRAME-1", "review", context=first, project=PROJECT)
    assert tracker.get_issue("TRAME-1").pr_url == first.pr_url
    assert not any("GitHub PR" in repr(call[2]) for call in tracker.calls)


def test_done_projection_is_confirmed_only_by_the_exact_receipt_chain():
    done = issue_raw(state="done", version=6)
    coordinates = {
        "pr_url": done["pr_url"], "head_sha": "a" * 40, "base_sha": "d" * 40,
        "merge_sha": "c" * 40,
    }
    chain = receipt_page([receipt_raw(1), receipt_raw(2, "done")])
    assert ScriptedTracker([done, chain]).recover_done_projection(
        "TRAME-1", project=PROJECT, **coordinates) is True
    # no project, other merge sha, not done, or no review receipt before the done one
    assert ScriptedTracker([]).recover_done_projection(
        "TRAME-1", project=None, **coordinates) is False
    assert ScriptedTracker([done, chain]).recover_done_projection(
        "TRAME-1", project=PROJECT, **{**coordinates, "merge_sha": "9" * 40}) is False
    assert ScriptedTracker([issue_raw(state="review")]).recover_done_projection(
        "TRAME-1", project=PROJECT, **coordinates) is False
    assert ScriptedTracker([done, receipt_page([receipt_raw(2, "done")])]
                           ).recover_done_projection(
        "TRAME-1", project=PROJECT, **coordinates) is False


def version_raw(**overrides):
    value = {
        "id": "TRAME-VERSION-3", "project": "TRAME", "name": "v1", "objective": "",
        "kind": "code", "status": "active", "starts_at": None, "target_at": None,
        "version": 2,
        "scope": {"direct": ["TRAME-2", "TRAME-1", "TRAME-3"], "direct_count": 3,
                  "total_count": 3, "complete": True},
    }
    value.update(overrides)
    return value


def release_project(mapping):
    return Project(key="TRAME", id="42", extra={"release_ids": mapping})


def test_release_scope_orders_issues_by_number_and_refuses_a_version_that_moved():
    from foundry.trackers.base import ReleaseScopeUnavailableError

    scope_ids = {"direct": ["TRAME-10", "TRAME-2"], "direct_count": 2, "total_count": 2,
                 "complete": True}
    version = version_raw(scope=scope_ids)
    unfinished = [issue_raw(id=name, state="ready", pr_url=None) for name in ("TRAME-2", "TRAME-10")]
    tracker = ScriptedTracker([version, *unfinished, version])
    scope = tracker.read_release_scope(release_project({"v1": "TRAME-VERSION-3"}), "v1")
    assert [item.id for item in scope.issues] == ["TRAME-2", "TRAME-10"]
    assert [call[1] for call in tracker.calls[1:3]] == ["/issues/TRAME-2", "/issues/TRAME-10"]
    for moved in (version_raw(scope=scope_ids, version=3),
                  version_raw(scope=scope_ids, status="released"),
                  version_raw(scope={**scope_ids, "direct": ["TRAME-10", "TRAME-5"]})):
        changed = ScriptedTracker([version, *unfinished, moved])
        with pytest.raises(ReleaseScopeUnavailableError) as caught:
            changed.read_release_scope(release_project({"v1": "TRAME-VERSION-3"}), "v1")
        assert caught.value.reason == "coordinates_changed"


def test_release_scope_reads_the_mapped_version_id_and_classifies_each_issue():
    tracker = ScriptedTracker([
        version_raw(),
        issue_raw(id="TRAME-1", state="done", ac_done=1, ac_total=1),
        receipt_page([receipt_raw(1), receipt_raw(2, "done")]),
        issue_raw(id="TRAME-2", state="review", pr_url=None),
        issue_raw(id="TRAME-3", state="done", ac_done=0, ac_total=1),
        receipt_page([receipt_raw(3, issue="TRAME-3"),
                      receipt_raw(4, "done", issue="TRAME-3")]),
        version_raw(),  # re-read after the issue reads
    ])
    scope = tracker.read_release_scope(release_project({"v1": "TRAME-VERSION-3"}), "v1")
    assert tracker.calls[0][:2] == ("GET", "/versions/TRAME-VERSION-3")
    assert [call[1] for call in tracker.calls if "/versions" in call[1]] == [
        "/versions/TRAME-VERSION-3"] * 2  # the id twice, never a listing by name
    assert scope.release_id == "TRAME-VERSION-3" and scope.native_state == "active"
    assert [(item.id, item.disposition) for item in scope.issues] == [
        ("TRAME-1", "accepted"), ("TRAME-2", "unfinished"), ("TRAME-3", "unavailable")]
    assert scope.issues[0].references["merge_sha"] == "c" * 40
    assert scope.coordinates["version_id"] == "TRAME-VERSION-3"


@pytest.mark.parametrize("mapping, responses, reason", [
    ({}, [], "unmapped"),
    ({"v1": "v1"}, [], "invalid_mapping"),
    ({"v1": "OTHER-VERSION-3"}, [], "invalid_mapping"),
    ({"v1": "TRAME-VERSION-3"}, [DevHubTrackerError("GET", "/versions/x", 404, "version_unavailable")],
     "inaccessible_or_absent"),
    ({"v1": "TRAME-VERSION-3"}, [version_raw(id="TRAME-VERSION-4")], "invalid_response"),
    ({"v1": "TRAME-VERSION-3"}, [version_raw(project="OTHER")], "invalid_response"),
    ({"v1": "TRAME-VERSION-3"}, [version_raw(name="other")], "mapping_mismatch"),
    ({"v1": "TRAME-VERSION-3"},
     [version_raw(scope={"direct": ["TRAME-1"], "direct_count": 1, "total_count": 4,
                         "complete": True})], "indirect_scope_unlisted"),
    ({"v1": "TRAME-VERSION-3"},
     [version_raw(scope={"direct": ["TRAME-1"], "direct_count": 1, "total_count": 1,
                         "complete": False})], "invalid_response"),
    ({"v1": "TRAME-VERSION-3"},
     [version_raw(scope={"direct": ["OTHER-1"], "direct_count": 1, "total_count": 1,
                         "complete": True})], "invalid_response"),
])
def test_release_scope_refuses_unmapped_foreign_or_unlisted_scopes(mapping, responses, reason):
    from foundry.trackers.base import ReleaseScopeUnavailableError
    tracker = ScriptedTracker(responses)
    with pytest.raises(ReleaseScopeUnavailableError) as caught:
        tracker.read_release_scope(release_project(mapping), "v1")
    assert caught.value.reason == reason


# --- PAT-312: DHN-21 update_body, DHN-33 link_adr_issue, DHN-32 supersede_adr ---------

def test_update_body_checks_expected_body_then_patches_with_the_read_version():
    from foundry.models import Issue

    tracker = ScriptedTracker([
        issue_raw(body="before\n", version=4),
        issue_raw(body="after\n", version=5),
        issue_raw(body="after\n", version=5),
    ])
    issue = Issue(id="TRAME-1", title="Pilot", state="backlog")

    assert tracker.update_body(issue, "before\n", "after\n", project=PROJECT) is True
    assert [call[:2] for call in tracker.calls] == [
        ("GET", "/issues/TRAME-1"), ("PATCH", "/issues/TRAME-1"), ("GET", "/issues/TRAME-1"),
    ]
    assert tracker.calls[1][2] == {"body": "after\n"}
    assert tracker.calls[1][3]["version"] == 4


def test_update_body_refuses_a_divergent_body_and_reports_a_current_one():
    from foundry.models import Issue
    from foundry.trackers.base import BodyUpdateUnavailableError

    issue = Issue(id="TRAME-1", title="Pilot", state="backlog")
    changed = ScriptedTracker([issue_raw(body="human edit\n")])
    with pytest.raises(TrackerConflictError, match="corps DevHub modifié"):
        changed.update_body(issue, "before\n", "after\n", project=PROJECT)
    assert [call[0] for call in changed.calls] == ["GET"]

    current = ScriptedTracker([issue_raw(body="after\n")])
    assert current.update_body(issue, "after\n", "after\n", project=PROJECT) is False
    assert [call[0] for call in current.calls] == ["GET"]

    conflict = ScriptedTracker([
        issue_raw(body="before\n"),
        DevHubTrackerError("PATCH", "/issues/TRAME-1", 409, "version_conflict"),
    ])
    with pytest.raises(TrackerConflictError, match="version DevHub modifiée"):
        conflict.update_body(issue, "before\n", "after\n", project=PROJECT)

    unread = ScriptedTracker([
        issue_raw(body="before\n"), issue_raw(body="before\n"), issue_raw(body="not after\n"),
    ])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        unread.update_body(issue, "before\n", "after\n", project=PROJECT)

    with pytest.raises(BodyUpdateUnavailableError):
        ScriptedTracker([]).update_body(
            Adr(id="TRAME-ADR-0001", title="t"), "a", "b", project=PROJECT,
        )


def adr_listing(*ids):
    return receipt_page([
        {"id": i, "title": "Choice", "status": "proposed", "ref": "1", "version": 2}
        for i in ids])


def adr_document(status="proposed", linked=(), version=2):
    return {
        "schema_version": "devhub-adr-authoring.v1",
        "adr": {
            "id": "TRAME-ADR-0001", "title": "Choice", "status": status,
            "body": "## Context\n", "version": version,
        },
        "linked_issue_ids": list(linked),
        "editable": status == "proposed",
    }


def test_link_adr_issue_edits_a_proposed_adr_with_the_whole_relation_set():
    tracker = ScriptedTracker([
        adr_listing("TRAME-ADR-0001"),
        adr_document(linked=["TRAME-3"]), adr_document(linked=["TRAME-3", "TRAME-1"], version=3),
    ])
    adr = Adr(id="TRAME-ADR-0001", title="Choice")

    result = tracker.link_adr_issue(adr, "TRAME-1", project=PROJECT)

    assert (result.id, result.status) == ("TRAME-ADR-0001", "proposed")
    assert tracker.calls[1][:2] == ("GET", "/adr-authoring/adrs/TRAME-ADR-0001")
    method, path, body, kwargs = tracker.calls[2]
    assert (method, path) == ("PATCH", "/adr-authoring/adrs/TRAME-ADR-0001")
    assert body == {
        "schema_version": "devhub-adr-authoring.v1", "title": "Choice",
        "body": "## Context\n", "linked_issue_ids": ["TRAME-3", "TRAME-1"],
    }
    assert kwargs["version"] == 2 and kwargs["idempotent"] is True
    assert kwargs["response_contract"] == "devhub-adr-authoring.v1"


def test_link_adr_issue_is_a_no_write_when_the_relation_exists():
    tracker = ScriptedTracker([adr_listing("TRAME-ADR-0001"), adr_document(linked=["TRAME-1"])])
    tracker.link_adr_issue(Adr(id="TRAME-ADR-0001", title="Choice"), "TRAME-1", project=PROJECT)
    assert [call[0] for call in tracker.calls] == ["GET", "GET"]


def test_link_adr_issue_on_a_decided_adr_is_a_typed_refusal_before_any_write():
    from foundry.trackers.devhub import AdrRelationFrozenError

    adr = Adr(id="TRAME-ADR-0001", title="Choice")
    accepted = ScriptedTracker([adr_listing("TRAME-ADR-0001"), adr_document(status="accepted")])
    with pytest.raises(AdrRelationFrozenError, match="avant l'acceptation"):
        accepted.link_adr_issue(adr, "TRAME-1", project=PROJECT)
    assert [call[0] for call in accepted.calls] == ["GET", "GET"]

    refusal = DevHubTrackerError(
        "PATCH", "/adr-authoring/adrs/TRAME-ADR-0001", 403, "authority_boundary")
    raced = ScriptedTracker([
        adr_listing("TRAME-ADR-0001"), adr_document(), refusal,
        adr_document(status="accepted"),
    ])
    with pytest.raises(AdrRelationFrozenError, match="accepted"):
        raced.link_adr_issue(adr, "TRAME-1", project=PROJECT)
    assert issubclass(AdrRelationFrozenError, TrackerConflictError)
    # a 403 `authority_boundary` on a still proposed ADR is not a freeze: the error stands
    credential = ScriptedTracker([
        adr_listing("TRAME-ADR-0001"), adr_document(), refusal, adr_document()])
    with pytest.raises(DevHubTrackerError, match="authority_boundary") as caught:
        credential.link_adr_issue(adr, "TRAME-1", project=PROJECT)
    assert not isinstance(caught.value, AdrRelationFrozenError)


def test_link_adr_issue_replay_on_a_decided_adr_is_a_no_write_and_a_body_is_required():
    adr = Adr(id="TRAME-ADR-0001", title="Choice")
    accepted = ScriptedTracker([
        adr_listing("TRAME-ADR-0001"), adr_document(status="accepted", linked=["TRAME-1"])])
    assert accepted.link_adr_issue(adr, "TRAME-1", project=PROJECT).status == "accepted"
    assert [call[0] for call in accepted.calls] == ["GET", "GET"]
    no_body = adr_document()
    del no_body["adr"]["body"]
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        ScriptedTracker([adr_listing("TRAME-ADR-0001"), no_body]).link_adr_issue(
            adr, "TRAME-1", project=PROJECT)
    wrong_answer = adr_document(linked=["TRAME-1"])
    wrong_answer["adr"]["id"] = "TRAME-ADR-0002"
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        ScriptedTracker([adr_listing("TRAME-ADR-0001"), adr_document(), wrong_answer]
                        ).link_adr_issue(adr, "TRAME-1", project=PROJECT)


def test_link_adr_issue_refuses_a_foreign_project_issue():
    with pytest.raises(ValueError):
        ScriptedTracker([]).link_adr_issue(
            Adr(id="TRAME-ADR-0001", title="Choice"), "OTHER-1", project=PROJECT,
        )


def test_read_adr_issue_relations_pages_through_the_relationships_route():
    def page(ids, cursor):
        return {
            "schema_version": "devhub-adr-relationships.v1",
            "adr": {"id": "TRAME-ADR-0001", "version": 4},
            "items": [{"id": i} for i in ids],
            "page": {"returned_count": len(ids), "cursor": cursor, "truncation": cursor is not None},
        }

    tracker = ScriptedTracker([
        adr_listing("TRAME-ADR-0001"), page(["TRAME-1"], 1), page(["TRAME-2"], None)])
    ids = tracker.read_adr_issue_relations(
        Adr(id="TRAME-ADR-0001", title="Choice"), project=PROJECT,
    )
    assert ids == ["TRAME-1", "TRAME-2"]
    assert tracker.calls[1][1] == "/adrs/TRAME-ADR-0001/relationships?cursor=0&limit=100"
    assert tracker.calls[2][1].endswith("cursor=1&limit=100")
    assert tracker.calls[1][3]["response_contract"] == "devhub-adr-relationships.v1"


def test_read_adr_issue_relations_refuses_loose_pagination():
    def page(ids, cursor, **over):
        value = {
            "schema_version": "devhub-adr-relationships.v1",
            "adr": {"id": "TRAME-ADR-0001", "version": 4},
            "items": [{"id": i} for i in ids],
            "page": {"returned_count": len(ids), "cursor": cursor,
                     "truncation": cursor is not None},
        }
        for key, change in over.items():
            value[key] = change
        return value

    adr = Adr(id="TRAME-ADR-0001", title="Choice")
    cases = {
        "invalid_response": [
            page(["TRAME-1"], None, adr={"id": "TRAME-ADR-0002", "version": 4}),
            page(["TRAME-1"], None, page={"returned_count": 1, "cursor": None, "truncation": 0}),
            page(["TRAME-1"], None, page={"returned_count": 2, "cursor": None, "truncation": False}),
            page(["TRAME-1"], None, page={"returned_count": 1, "cursor": 3, "truncation": False}),
            page(["not-an-issue"], None),
        ],
    }
    for expected, pages in cases.items():
        for first in pages:
            tracker = ScriptedTracker([adr_listing("TRAME-ADR-0001"), first])
            with pytest.raises(DevHubTrackerError, match=expected):
                tracker.read_adr_issue_relations(adr, project=PROJECT)
    repeated = ScriptedTracker([
        adr_listing("TRAME-ADR-0001"), page(["TRAME-1"], 1), page(["TRAME-1"], None)])
    with pytest.raises(DevHubTrackerError, match="pagination_repeated"):
        repeated.read_adr_issue_relations(adr, project=PROJECT)
    empty = ScriptedTracker([adr_listing("TRAME-ADR-0001"), page([], 1)])
    with pytest.raises(DevHubTrackerError, match="pagination_empty_page"):
        empty.read_adr_issue_relations(adr, project=PROJECT)
    stalled = ScriptedTracker([adr_listing("TRAME-ADR-0001"), page(["TRAME-1"], 0)])
    with pytest.raises(DevHubTrackerError, match="pagination_stalled"):
        stalled.read_adr_issue_relations(adr, project=PROJECT)


def _supersede_listing(old_status="accepted", new_status="accepted", old_version=3, new_version=5):
    return receipt_page([
        {"id": "TRAME-ADR-0001", "title": "Old", "status": old_status, "version": old_version},
        {"id": "TRAME-ADR-0002", "title": "New", "status": new_status, "version": new_version},
    ])


def _decision_document(adr_id, supersedes=None, superseded_by=None, supersedes_all=None):
    relations = {"supersedes": supersedes, "superseded_by": superseded_by}
    if supersedes_all is not None:
        relations["supersedes_all"] = supersedes_all
    return {"schema_version": "devhub-adr-decisions.v1", "project": "TRAME",
            "adr": {"id": adr_id}, "relations": relations}


OLD, NEW = Adr(id="TRAME-ADR-0001", title="Old"), "TRAME-ADR-0002"


def test_supersede_adr_posts_the_named_replacement_with_a_proof_bound_to_both_adrs():
    tracker = ScriptedTracker([
        _supersede_listing(), {"id": "TRAME-ADR-0001", "version": 4},
        _decision_document("TRAME-ADR-0001", superseded_by=NEW),
    ])
    tracker.supersede_adr(OLD, NEW, project=PROJECT)
    method, path, body, kwargs = tracker.calls[1]
    assert (method, path) == ("POST", "/adrs/TRAME-ADR-0001/supersede")
    assert body == {"replacement": NEW, "replacement_version": 5}
    assert kwargs["version"] == 3 and kwargs["idempotent"] is True
    proof = decode_proof(kwargs["proof"])
    assert proof["action"] == "adr-supersede" and proof["target_status"] == "superseded"
    assert (proof["resource_id"], proof["expected_version"]) == ("TRAME-ADR-0001", 3)
    assert (proof["replacement_id"], proof["replacement_version"]) == (NEW, 5)
    assert tracker.calls[2][:2] == ("GET", "/adr-decisions/adrs/TRAME-ADR-0001")
    assert tracker.calls[2][3]["response_contract"] == "devhub-adr-decisions.v1"
    assert [call[1] for call in tracker.calls].count("/adrs/TRAME-ADR-0001/status") == 0


@pytest.mark.parametrize("old,new", [("accepted", "proposed"), ("proposed", "accepted"),
                                     ("deprecated", "accepted"), ("accepted", "superseded")])
def test_supersede_adr_refuses_an_unaccepted_side_before_any_write(old, new):
    tracker = ScriptedTracker([_supersede_listing(old, new)])
    with pytest.raises(TrackerConflictError):
        tracker.supersede_adr(OLD, NEW, project=PROJECT)
    assert [call[0] for call in tracker.calls] == ["GET"]
    same = ScriptedTracker([_supersede_listing()])
    with pytest.raises(TrackerConflictError):
        same.supersede_adr(OLD, "TRAME-ADR-0001", project=PROJECT)
    assert [call[0] for call in same.calls] == ["GET"]


def test_supersede_adr_replays_without_a_write_and_refuses_another_replacement():
    done = ScriptedTracker([
        _supersede_listing("superseded"), _decision_document("TRAME-ADR-0001", superseded_by=NEW)])
    done.supersede_adr(OLD, NEW, project=PROJECT)
    assert [call[0] for call in done.calls] == ["GET", "GET"]
    other = ScriptedTracker([
        _supersede_listing("superseded"), _decision_document("TRAME-ADR-0001")])
    with pytest.raises(TrackerConflictError):
        other.supersede_adr(OLD, NEW, project=PROJECT)
    assert [call[0] for call in other.calls] == ["GET", "GET"]


def test_supersede_adr_replay_is_a_no_write_even_when_the_replacement_has_moved_on():
    # A by B, then B by C: B is superseded now, yet A -> B is still recorded and replays
    moved = ScriptedTracker([
        _supersede_listing("superseded", "superseded"),
        _decision_document("TRAME-ADR-0001", superseded_by=NEW)])
    moved.supersede_adr(OLD, NEW, project=PROJECT)
    assert [call[0] for call in moved.calls] == ["GET", "GET"]
    # another recorded replacement is still refused, whatever the replacement's status
    other = ScriptedTracker([
        _supersede_listing("superseded", "superseded"),
        _decision_document("TRAME-ADR-0001", superseded_by="TRAME-ADR-0009")])
    with pytest.raises(TrackerConflictError):
        other.supersede_adr(OLD, NEW, project=PROJECT)
    assert [call[0] for call in other.calls] == ["GET", "GET"]


def test_supersede_adr_maps_version_errors_and_a_missing_route_and_checks_the_link():
    from foundry.trackers.base import TrackerCapabilityUnavailableError

    for code in ("version_conflict", "invalid_transition", "resource_conflict"):
        tracker = ScriptedTracker([
            _supersede_listing(), DevHubTrackerError("POST", "/adrs/x/supersede", 409, code)])
        with pytest.raises(TrackerConflictError):
            tracker.supersede_adr(OLD, NEW, project=PROJECT)
    missing = ScriptedTracker([
        _supersede_listing(), DevHubTrackerError("POST", "/adrs/x/supersede", 404, "route_unavailable")])
    with pytest.raises(TrackerCapabilityUnavailableError):
        missing.supersede_adr(OLD, NEW, project=PROJECT)
    forbidden = ScriptedTracker([
        _supersede_listing(), DevHubTrackerError("POST", "/adrs/x/supersede", 403, "invalid_proof")])
    with pytest.raises(DevHubTrackerError, match="invalid_proof"):
        forbidden.supersede_adr(OLD, NEW, project=PROJECT)
    unlinked = ScriptedTracker([
        _supersede_listing(), {}, _decision_document("TRAME-ADR-0001")])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        unlinked.supersede_adr(OLD, NEW, project=PROJECT)


def test_supersede_adr_uses_server_ids_for_swapped_imported_ids():
    # port id TRAME-ADR-0011 is server TRAME-ADR-0013 (and conversely)
    tracker = ScriptedTracker([
        _swapped_listing(), {}, _decision_document("TRAME-ADR-0013", superseded_by="TRAME-ADR-0011")])
    tracker.supersede_adr(Adr(id="TRAME-ADR-0011", title="A"), "TRAME-ADR-0013", project=PROJECT)
    _, path, body, kwargs = tracker.calls[1]
    assert path == "/adrs/TRAME-ADR-0013/supersede"
    assert body == {"replacement": "TRAME-ADR-0011", "replacement_version": 7}
    proof = decode_proof(kwargs["proof"])
    assert (proof["resource_id"], proof["replacement_id"]) == ("TRAME-ADR-0013", "TRAME-ADR-0011")


def _export_tracker(items, documents):
    """Listing, then for each ADR its detail and its decision document, in listing order."""
    responses = [receipt_page(items)]
    for item, document in zip(items, documents):
        responses += [dict(item, body="b"), document]
    return ScriptedTracker(responses)


def test_migration_export_adrs_reads_supersession_relations_back():
    items = [
        {"id": "TRAME-ADR-0001", "title": "Old", "status": "superseded", "version": 3},
        {"id": "TRAME-ADR-0002", "title": "New", "status": "accepted", "version": 5},
        {"id": "TRAME-ADR-0003", "title": "Third", "status": "accepted", "version": 2},
    ]
    tracker = _export_tracker(items, [
        _decision_document("TRAME-ADR-0001", superseded_by=NEW),
        _decision_document(NEW, supersedes="TRAME-ADR-0001", supersedes_all=["TRAME-ADR-0001"]),
        _decision_document("TRAME-ADR-0003"),
    ])
    relations = {entry["adr"].id: entry["relations"]
                 for entry in tracker.migration_export_adrs(PROJECT)}
    assert relations["TRAME-ADR-0001"]["superseded_by"] == NEW
    assert relations[NEW]["supersedes"] == ["TRAME-ADR-0001"]
    assert relations["TRAME-ADR-0003"] == {
        "supersedes": [], "superseded_by": None, "issues": "unknown"}
    # one project listing only
    assert [call[1] for call in tracker.calls].count("/projects/TRAME/adrs?cursor=0&limit=100") == 1


def test_migration_export_adrs_never_claims_supersedes_while_a_superseded_adr_is_unlinked():
    # TRAME-ADR-0004 became superseded through the status route: no recorded link, so its
    # replacement is unknown and could be any other ADR (PAT-ADR-0008).
    items = [
        {"id": "TRAME-ADR-0003", "title": "Third", "status": "accepted", "version": 2},
        {"id": "TRAME-ADR-0004", "title": "Fourth", "status": "superseded", "version": 2},
    ]
    tracker = _export_tracker(items, [
        _decision_document("TRAME-ADR-0003"), _decision_document("TRAME-ADR-0004")])
    relations = {entry["adr"].id: entry["relations"]
                 for entry in tracker.migration_export_adrs(PROJECT)}
    assert relations["TRAME-ADR-0003"]["supersedes"] == "unknown"
    assert relations["TRAME-ADR-0003"]["superseded_by"] is None
    assert relations["TRAME-ADR-0004"] == {
        "supersedes": [], "superseded_by": "unknown", "issues": "unknown"}
    # an unknown replacement is not the entry itself: with no other unlinked ADR, [] is known
    only = _export_tracker(items[1:], [_decision_document("TRAME-ADR-0004")])
    assert only.migration_export_adrs(PROJECT)[0]["relations"]["supersedes"] == []
    # a predecessor outside the listing cannot be mapped: invalid_response, not a KeyError
    broken = _export_tracker(items[:1], [
        _decision_document("TRAME-ADR-0003", supersedes="TRAME-ADR-0099")])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        broken.migration_export_adrs(PROJECT)


def created_raw(**overrides):
    value = dict(
        id="TRAME-7", title="Created", state="backlog", priority="P3", type="Task",
        labels=[], estimate=None, milestone=None, pr_url=None, body="body",
        links=[{"type": "subtask-of", "direction": "outward", "target": "TRAME-1"}],
    )
    value.update(overrides)
    return issue_raw(**value)


def test_reconcile_issue_creation_validates_an_existing_issue_with_one_read():
    tracker = ScriptedTracker([created_raw()])
    result = tracker.reconcile_issue_creation(
        PROJECT, "TRAME-7", "Created", "body", {"Priority": "P3", "Type": "Task"}, "TRAME-1",
    )
    assert [call[:2] for call in tracker.calls] == [("GET", "/issues/TRAME-7")]
    assert result["validation"] == "accepted" and result["issue_id"] == "TRAME-7"
    assert result["automatic_retry"] is False and result["comparison"] == "exact"
    assert result["source_sha256"] == hashlib.sha256(b"body").hexdigest()


def test_reconcile_issue_creation_accepts_the_inverse_parent_link_and_no_parent():
    inward = created_raw(links=[{"type": "parent-of", "direction": "inward", "target": "TRAME-1"}])
    assert ScriptedTracker([inward]).reconcile_issue_creation(
        PROJECT, "TRAME-7", "Created", "body", None, "TRAME-1",
    )["validation"] == "accepted"
    assert ScriptedTracker([created_raw(links=[])]).reconcile_issue_creation(
        PROJECT, "TRAME-7", "Created", "body", None, None,
    )["validation"] == "accepted"


@pytest.mark.parametrize("args, diverged", [
    (("Other", "body", None, "TRAME-1"), "title"),
    (("Created", "other", None, "TRAME-1"), "body"),
    (("Created", "body", None, "TRAME-2"), "parent"),
    (("Created", "body", None, None), "parent"),
    (("Created", "body", {"Priority": "P0"}, "TRAME-1"), "priority"),
])
def test_reconcile_issue_creation_refuses_a_divergent_issue_without_values(args, diverged):
    tracker = ScriptedTracker([created_raw()])
    with pytest.raises(TrackerConflictError, match="validation refused; no retry") as caught:
        tracker.reconcile_issue_creation(PROJECT, "TRAME-7", *args)
    assert f"diverged: {diverged}" in str(caught.value)
    assert "other" not in str(caught.value).split("diverged:")[1]
    assert [call[0] for call in tracker.calls] == ["GET"]


def test_reconcile_issue_creation_reports_an_absent_issue_and_refuses_a_foreign_binding():
    from foundry.trackers.base import IssueUnavailableError
    absent = DevHubTrackerError("GET", "/issues/TRAME-7", 404, "issue_unavailable")
    with pytest.raises(IssueUnavailableError):
        ScriptedTracker([absent]).reconcile_issue_creation(PROJECT, "TRAME-7", "Created", "body")
    foreign = ScriptedTracker([])
    with pytest.raises(SystemExit):
        foreign.reconcile_issue_creation(PROJECT, "OTHER-7", "Created", "body")
    with pytest.raises(SystemExit):  # a client identifier is not an issue id of the project
        foreign.reconcile_issue_creation(
            PROJECT, "3f2c1d4e-0000-4000-8000-000000000000", "Created", "body",
        )
    assert foreign.calls == []


# --- PAT-314: DHN-41 project identity read-back, DHN-35 staged migration link ---

BOUND = Project(key="TRAME", id="42", extra={"canonical_repo": "github.com/acme/trame"})


@pytest.mark.parametrize(("remote", "expected"), (
    ({"key": "TRAME", "id": "42", "canonical_repo": "github.com/acme/trame"}, True),
    ({"key": "TRAME", "id": "43", "canonical_repo": "github.com/acme/trame"}, False),
    ({"key": "OTHER", "id": "42", "canonical_repo": "github.com/acme/trame"}, False),
    ({"key": "TRAME", "id": "42", "canonical_repo": "github.com/acme/other"}, False),
    ({"key": "TRAME", "id": "42"}, False),
))
def test_verify_project_identity_reads_back_key_id_and_repository(remote, expected):
    tracker = ScriptedTracker([remote])
    assert tracker.verify_project_identity(BOUND) is expected
    assert [call[:2] for call in tracker.calls] == [
        ("GET", "/projects/resolve?repo=github.com%2Facme%2Ftrame")]


def test_verify_project_identity_is_false_without_a_repository_or_on_404():
    tracker = ScriptedTracker([])
    assert tracker.verify_project_identity(PROJECT) is False
    assert tracker.calls == []
    absent = ScriptedTracker([DevHubTrackerError("GET", "/projects/resolve", 404, "not_found")])
    assert absent.verify_project_identity(BOUND) is False


def test_verify_project_identity_raises_other_errors():
    tracker = ScriptedTracker([DevHubTrackerError("GET", "/projects/resolve", 503, "unavailable")])
    with pytest.raises(DevHubTrackerError):
        tracker.verify_project_identity(BOUND)


def test_migration_link_issue_posts_one_link_then_replays_without_write(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    source = issue_raw(id="TRAME-1", version=3)
    target = issue_raw(id="TRAME-2", version=5)
    tracker = ScriptedTracker([source, target, {"ok": True}])
    tracker.migration_link_issue(PROJECT, "TRAME-1", "depends-on", "TRAME-2")
    assert [(call[0], call[1]) for call in tracker.calls] == [
        ("GET", "/issues/TRAME-1"), ("GET", "/issues/TRAME-2"),
        ("POST", "/issues/TRAME-1/links")]  # the source is read once
    method, path, body, kwargs = tracker.calls[-1]
    assert (method, path) == ("POST", "/issues/TRAME-1/links")
    assert body == {"type": "depends-on", "target": "TRAME-2", "target_version": 5}
    assert kwargs["version"] == 3

    linked = issue_raw(id="TRAME-1", version=4, links=[
        {"type": "depends-on", "direction": "outward", "target": "TRAME-2"}])
    replay = ScriptedTracker([linked])
    replay.migration_link_issue(PROJECT, "TRAME-1", "depends-on", "TRAME-2")
    assert [call[0] for call in replay.calls] == ["GET"]


def test_migration_link_issue_sees_the_inverse_stored_link_as_present(monkeypatch):
    # The migration links both ends of a relation; the second call must not post.
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    inverse = issue_raw(id="TRAME-2", links=[
        {"type": "depends-on", "direction": "inward", "target": "TRAME-1"}])
    tracker = ScriptedTracker([inverse])
    tracker.migration_link_issue(PROJECT, "TRAME-2", "blocks", "TRAME-1")
    assert [call[0] for call in tracker.calls] == ["GET"]


def test_migration_link_issue_refuses_a_foreign_id_or_an_unsupported_type_before_any_request(
        monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    for src, dst in (("OTHER-1", "TRAME-2"), ("TRAME-1", "OTHER-2")):
        tracker = ScriptedTracker([])
        with pytest.raises(SystemExit, match="Binding DevHub refusé"):
            tracker.migration_link_issue(PROJECT, src, "depends-on", dst)
        assert tracker.calls == []
    tracker = ScriptedTracker([])
    with pytest.raises(ValueError):
        tracker.migration_link_issue(PROJECT, "TRAME-1", "duplicates", "TRAME-2")
    assert tracker.calls == []


def test_migration_link_issue_refuses_an_archived_project_before_any_request(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {
        "devhub": {"old": {"archive": True, "key": "TRAME", "id": "42"}}})
    tracker = ScriptedTracker([])
    with pytest.raises(SystemExit, match="archive"):
        tracker.migration_link_issue(PROJECT, "TRAME-1", "depends-on", "TRAME-2")
    assert tracker.calls == []


# --- PAT-318: DHN-25 acceptance override and DHN-26 human acceptance -------------------
# Contract announced by DevHub on 2026-10-20 (DEVHUB-106, merged as f347f38).

REVIEW = TransitionContext(
    pr_url=issue_raw()["pr_url"], head_sha="a" * 40, base_sha="d" * 40,
    review_digest="b" * 64,
)
OVERRIDE = "maintainer-override"


def override_receipt(receipt_id, reason=OVERRIDE, **overrides):
    return receipt_raw(receipt_id, "acceptance_override", reason=reason, **overrides)


def test_override_receipt_and_human_acceptance_receipt_are_read_with_their_null_rules():
    decision = human_decision()
    digest = decisions.decision_digest(decision)
    human = receipt_raw(
        3, "human_acceptance", pr_url=None, head_sha=None, base_sha=None,
        review_digest=None, decision_digest=digest, decision=decision,
    )
    # jsonb does not keep key order: the digest is recomputed from the object read
    human["decision"] = dict(reversed(list(decision.items())))
    tracker = ScriptedTracker([receipt_page([receipt_raw(1), override_receipt(2), human])])
    receipts = tracker.read_proof_receipts("TRAME-1", project=PROJECT)
    assert [item["action"] for item in receipts] == [
        "review", "acceptance_override", "human_acceptance"]
    assert receipts[1]["reason"] == OVERRIDE


@pytest.mark.parametrize("change", [
    {"pr_url": "https://github.com/acme/trame/pull/1"},   # coordinates null only here
    {"merge_sha": "c" * 40},
    {"reason": OVERRIDE},
    {"decision_digest": "e" * 64},                         # digest differs from the object
    {"decision": None},
    {"decision": {"schema": "foundry.issue-acceptance-decision.v1"}},
])
def test_human_acceptance_receipt_fails_closed_on_any_other_shape(change):
    decision = human_decision()
    base = dict(
        pr_url=None, head_sha=None, base_sha=None, review_digest=None,
        decision_digest=decisions.decision_digest(decision), decision=decision,
    )
    item = receipt_raw(1, "human_acceptance", **{**base, **change})
    tracker = ScriptedTracker([receipt_page([item])])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        tracker.read_proof_receipts("TRAME-1", project=PROJECT)


def test_override_receipt_does_not_make_a_review_a_new_generation():
    tracker = ScriptedTracker([
        issue_raw(state="review", version=4),
        receipt_page([receipt_raw(1), override_receipt(2)]),
    ])
    tracker.set_state("TRAME-1", "review", context=REVIEW, project=PROJECT)
    assert [call[0] for call in tracker.calls] == ["GET", "GET"]


def test_acceptance_override_posts_the_strict_body_with_a_bound_proof_and_reads_back():
    tracker = ScriptedTracker([
        issue_raw(state="review", version=4),
        receipt_page([receipt_raw(1)]),
        {"id": "2"},
        receipt_page([receipt_raw(1), override_receipt(2)]),
    ])
    assert tracker.acceptance_override_projection_supported is True
    assert tracker.project_acceptance_override(
        "TRAME-1", OVERRIDE, REVIEW, project=PROJECT) is True
    method, path, body, kwargs = tracker.calls[2]
    assert (method, path) == ("POST", "/issues/TRAME-1/acceptance-override")
    assert body == {
        "reason": OVERRIDE, "pr_url": REVIEW.pr_url, "head_sha": "a" * 40,
        "base_sha": "d" * 40, "review_digest": "b" * 64,
    }
    assert kwargs["version"] == 4 and kwargs["idempotent"] is True
    assert decode_proof(kwargs["proof"]) == {
        "action": "acceptance-override", "contract_version": "devhub-tracker.v1",
        "expected_version": 4, "expires_at": 1_300_000, "nonce": "canonical_nonce_123456",
        "project_key": "TRAME", "resource_id": "TRAME-1", "reason": OVERRIDE,
        "pr_url": REVIEW.pr_url, "head_sha": "a" * 40, "base_sha": "d" * 40,
        "review_digest": "b" * 64,
    }


def test_acceptance_override_replay_writes_nothing_and_another_reason_is_refused():
    state = [issue_raw(state="review", version=4),
             receipt_page([receipt_raw(1), override_receipt(2)])]
    tracker = ScriptedTracker(list(state))
    assert tracker.project_acceptance_override(
        "TRAME-1", OVERRIDE, REVIEW, project=PROJECT) is False
    assert all(call[0] == "GET" for call in tracker.calls)
    other = ScriptedTracker(list(state))
    with pytest.raises(TrackerConflictError, match="autre motif"):
        other.project_acceptance_override("TRAME-1", "other-reason", REVIEW, project=PROJECT)
    assert all(call[0] == "GET" for call in other.calls)


@pytest.mark.parametrize("issue, receipts, match", [
    (issue_raw(state="in-progress"), [receipt_raw(1)], "pas en review"),
    (issue_raw(state="review"), [], "dernière review"),
    (issue_raw(state="review"), [receipt_raw(1, head_sha="e" * 40)], "dernière review"),
    (issue_raw(state="review"), [receipt_raw(1), receipt_raw(2, "done")], "déjà close"),
])
def test_acceptance_override_refuses_before_any_write(issue, receipts, match):
    tracker = ScriptedTracker([issue, receipt_page(receipts)])
    with pytest.raises(TrackerConflictError, match=match):
        tracker.project_acceptance_override("TRAME-1", OVERRIDE, REVIEW, project=PROJECT)
    assert all(call[0] == "GET" for call in tracker.calls)


def test_acceptance_override_refuses_invalid_input_without_a_request():
    tracker = ScriptedTracker([])
    with pytest.raises(ValueError):
        tracker.project_acceptance_override("TRAME-1", "No", REVIEW, project=PROJECT)
    with pytest.raises(ValueError):
        tracker.project_acceptance_override(
            "TRAME-1", OVERRIDE,
            TransitionContext(pr_url=REVIEW.pr_url, head_sha="a" * 40, base_sha="d" * 40,
                              review_digest="b" * 64, merge_sha="c" * 40),
            project=PROJECT)
    assert tracker.calls == []


def test_acceptance_override_maps_a_version_conflict_and_refuses_a_divergent_readback():
    stale = DevHubTrackerError("POST", "/x", 409, "version_conflict")
    tracker = ScriptedTracker([issue_raw(state="review", version=4),
                               receipt_page([receipt_raw(1)]), stale])
    with pytest.raises(TrackerConflictError, match="version DevHub"):
        tracker.project_acceptance_override("TRAME-1", OVERRIDE, REVIEW, project=PROJECT)
    other = DevHubTrackerError("POST", "/x", 409, "resource_conflict")
    tracker = ScriptedTracker([issue_raw(state="review", version=4),
                               receipt_page([receipt_raw(1)]), other])
    with pytest.raises(DevHubTrackerError, match="resource_conflict"):
        tracker.project_acceptance_override("TRAME-1", OVERRIDE, REVIEW, project=PROJECT)
    tracker = ScriptedTracker([issue_raw(state="review", version=4),
                               receipt_page([receipt_raw(1)]), {},
                               receipt_page([receipt_raw(1)])])
    with pytest.raises(TrackerConflictError, match="relecture"):
        tracker.project_acceptance_override("TRAME-1", OVERRIDE, REVIEW, project=PROJECT)


def test_acceptance_override_recovery_is_refused_because_devhub_has_none():
    with pytest.raises(TrackerConflictError, match="pas de reprise"):
        ScriptedTracker([]).recover_acceptance_override(
            "TRAME-1", OVERRIDE, pr_url="u", head_sha="a" * 40, base_sha="d" * 40,
            merge_sha="c" * 40, project=PROJECT)


HUMAN_BODY = "- [ ] decided offline\n- [ ] second"


def human_decision(body=HUMAN_BODY, **over):
    criteria = acceptance_criteria(body)
    document = {
        "schema": decisions.SCHEMA, "issue": "TRAME-1",
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "ac_digest": acceptance_digest(criteria), "from_state": "ready",
        "authority": "maintainer-direct", "decided_on": "2026-10-20",
        "verdict": "accepted", "delivery": "none",
        "criteria": [
            {**item, "verdict": "pass", "evidence": [{"type": "issue", "id": "TRAME-2"}],
             "unknowns": []}
            for item in criteria
        ],
    }
    document.update(over)
    return document


def human_receipt(receipt_id, decision, **overrides):
    return receipt_raw(
        receipt_id, "human_acceptance", pr_url=None, head_sha=None, base_sha=None,
        review_digest=None, decision_digest=decisions.decision_digest(decision),
        decision=decision, **overrides,
    )


def ready_issue(**over):
    value = dict(state="ready", pr_url=None, body=HUMAN_BODY, ac_total=2, version=7)
    value.update(over)
    return issue_raw(**value)


def test_accept_issue_posts_the_decision_and_verifies_the_recomputed_digest():
    decision = human_decision()
    digest = decisions.decision_digest(decision)
    stored = human_receipt(1, decision)
    stored["decision"] = dict(reversed(list(decision.items())))  # jsonb order
    seen = []
    tracker = ScriptedTracker([
        ready_issue(), receipt_page([]), {"ok": True},
        ready_issue(state="done", version=8), receipt_page([stored]),
    ])
    accepted = tracker.accept_issue(
        "TRAME-1", decision, project=PROJECT, verify_evidence=lambda: seen.append(1))
    assert accepted.state == "done" and seen == [1]
    method, path, body, kwargs = tracker.calls[2]
    assert (method, path, body) == (
        "POST", "/issues/TRAME-1/human-acceptance", {"decision": decision})
    assert kwargs["version"] == 7 and kwargs["idempotent"] is True
    assert decode_proof(kwargs["proof"]) == {
        "action": "human-acceptance", "contract_version": "devhub-tracker.v1",
        "expected_version": 7, "expires_at": 1_300_000, "nonce": "canonical_nonce_123456",
        "project_key": "TRAME", "resource_id": "TRAME-1", "decision_digest": digest,
    }
    assert tracker.human_acceptance_supported is False  # `issue accept` stays Linear-only


def test_accept_issue_exact_replay_converges_without_a_post_and_a_different_one_is_refused():
    decision = human_decision()
    done = ready_issue(state="done", version=8)
    tracker = ScriptedTracker([done, receipt_page([human_receipt(1, decision)])])
    assert tracker.accept_issue("TRAME-1", decision, project=PROJECT).state == "done"
    assert all(call[0] == "GET" for call in tracker.calls)
    different = human_decision(decided_on="2026-10-21")
    tracker = ScriptedTracker([done, receipt_page([human_receipt(1, decision)])])
    with pytest.raises(TrackerConflictError, match="different decision"):
        tracker.accept_issue("TRAME-1", different, project=PROJECT)
    assert all(call[0] == "GET" for call in tracker.calls)


@pytest.mark.parametrize("issue, receipts, over, match", [
    (ready_issue(state="in-progress"), [], {}, "native state is in-progress"),
    (ready_issue(type="Epic"), [], {}, "Epic"),
    (ready_issue(links=[{"type": "parent-of", "direction": "outward", "target": "TRAME-9"}]),
     [], {}, "children"),
    (ready_issue(pr_url="https://github.com/acme/trame/pull/1"), [], {}, "linked to a PR"),
    (ready_issue(), [receipt_raw(1)], {}, "PR receipts"),
    (ready_issue(ac_total=0, ac_done=0), [], {}, "no acceptance criterion"),
    (ready_issue(body=HUMAN_BODY + "\n- [ ] drift"), [], {}, "body|criteria|corps"),
    (ready_issue(), [], {"issue": "TRAME-9"}, "pas TRAME-1"),
])
def test_accept_issue_refuses_before_any_write(issue, receipts, over, match):
    decision = human_decision(**over)
    tracker = ScriptedTracker([issue, receipt_page(receipts)])
    with pytest.raises(TrackerConflictError, match=match):
        tracker.accept_issue("TRAME-1", decision, project=PROJECT)
    assert all(call[0] == "GET" for call in tracker.calls)


def test_accept_issue_refuses_on_failed_evidence_and_on_divergent_readback():
    decision = human_decision()

    def bad_evidence():
        raise decisions.AcceptanceDecisionError("preuve introuvable")

    tracker = ScriptedTracker([ready_issue(), receipt_page([])])
    with pytest.raises(TrackerConflictError, match="preuve introuvable"):
        tracker.accept_issue("TRAME-1", decision, project=PROJECT,
                             verify_evidence=bad_evidence)
    assert all(call[0] == "GET" for call in tracker.calls)
    other = human_decision(decided_on="2026-10-21")
    tracker = ScriptedTracker([
        ready_issue(), receipt_page([]), {},
        ready_issue(state="done", version=8), receipt_page([human_receipt(1, other)]),
    ])
    with pytest.raises(TrackerConflictError, match="read-back divergent"):
        tracker.accept_issue("TRAME-1", decision, project=PROJECT)


def test_accept_issue_maps_a_version_conflict_to_a_bounded_conflict():
    stale = DevHubTrackerError("POST", "/x", 409, "version_conflict")
    tracker = ScriptedTracker([ready_issue(), receipt_page([]), stale])
    with pytest.raises(TrackerConflictError, match="version DevHub"):
        tracker.accept_issue("TRAME-1", human_decision(), project=PROJECT)


def test_a_server_older_than_devhub_106_is_a_named_unavailable_capability():
    absent = DevHubTrackerError("POST", "/x", 404, "route_unavailable")
    tracker = ScriptedTracker([issue_raw(state="review", version=4),
                               receipt_page([receipt_raw(1)]), absent])
    with pytest.raises(TrackerCapabilityUnavailableError, match="devhub.acceptance_override"):
        tracker.project_acceptance_override("TRAME-1", OVERRIDE, REVIEW, project=PROJECT)
    tracker = ScriptedTracker([ready_issue(), receipt_page([]), absent])
    with pytest.raises(TrackerCapabilityUnavailableError, match="devhub.human_acceptance"):
        tracker.accept_issue("TRAME-1", human_decision(), project=PROJECT)


# --- PAT-319: DHN-34 selective issue import and DHN-36 ADR import ---------------------
# Contract announced by DevHub on 2026-10-21 (DEVHUB-105, merged as b0231d9).

REF = "linear:issue:PAT-1001"
SNAPSHOT = {
    "kind": "issue", "source_ref": REF, "id": "PAT-1001", "title": "Imported",
    "body": "- [ ] one\n",
    "attributes": {"type": "Task", "priority": "P2", "estimate": None, "state": "backlog"},
}


def conflict(code="resource_conflict"):
    return DevHubTrackerError("POST", "/projects/TRAME/issues", 409, code)


def test_migration_import_issue_posts_the_coordinate_and_the_non_null_fields(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    tracker = ScriptedTracker([issue_raw(
        id="TRAME-7", title="Imported", body="- [ ] one\n", source_ref=REF)])
    issue = tracker.migration_import_issue(PROJECT, SNAPSHOT, source_ref=REF)
    method, path, body, kwargs = tracker.calls[0]
    assert (method, path) == ("POST", "/projects/TRAME/issues")
    assert body == {
        "title": "Imported", "body": "- [ ] one\n", "source_ref": REF,
        "fields": {"type": "Task", "priority": "P2", "state": "backlog"},
    }
    assert kwargs["idempotent"] is True
    assert issue.id == "TRAME-7" and len(tracker.calls) == 1


def test_migration_import_issue_fails_closed_on_conflict_and_on_a_divergent_answer(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    with pytest.raises(TrackerConflictError, match="source_ref"):
        ScriptedTracker([conflict()]).migration_import_issue(PROJECT, SNAPSHOT, source_ref=REF)
    with pytest.raises(DevHubTrackerError) as other:
        ScriptedTracker([conflict("invalid_transition")]).migration_import_issue(
            PROJECT, SNAPSHOT, source_ref=REF)
    assert other.value.code == "invalid_transition"
    for change in ({"source_ref": None}, {"source_ref": "other:ref"}, {"body": "x"},
                   {"title": "Other"}, {"id": "OTHER-7"}):
        raw = {**issue_raw(id="TRAME-7", title="Imported", body="- [ ] one\n", source_ref=REF),
               **change}
        with pytest.raises(TrackerConflictError, match="divergente"):
            ScriptedTracker([raw]).migration_import_issue(PROJECT, SNAPSHOT, source_ref=REF)


@pytest.mark.parametrize("ref", ["", "a b", "a\tb", "a\x7fb", "x" * 201, "é\u0085", None])
def test_migration_issue_ports_refuse_an_invalid_source_ref_before_any_request(monkeypatch, ref):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    tracker = ScriptedTracker([])
    with pytest.raises(ValueError):
        tracker.migration_find_issue(PROJECT, ref)
    with pytest.raises(ValueError):
        tracker.migration_import_issue(PROJECT, {**SNAPSHOT, "source_ref": ref}, source_ref=ref)
    assert tracker.calls == []


def test_migration_import_issue_refuses_an_archived_project_before_any_request(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {
        "devhub": {"old": {"archive": True, "key": "TRAME", "id": "42"}}})
    tracker = ScriptedTracker([])
    with pytest.raises(SystemExit, match="archive"):
        tracker.migration_import_issue(PROJECT, SNAPSHOT, source_ref=REF)
    assert tracker.calls == []


def test_migration_find_issue_reads_by_exact_source_ref_then_by_id():
    listed = issue_raw(id="TRAME-7", source_ref=REF)
    detail = issue_raw(id="TRAME-7", title="Imported", body="b", source_ref=REF)
    tracker = ScriptedTracker([receipt_page([listed]), detail])
    found = tracker.migration_find_issue(PROJECT, REF)
    assert tracker.calls[0][:2] == (
        "GET", "/projects/TRAME/issues?source_ref=linear%3Aissue%3APAT-1001")
    assert tracker.calls[1][:2] == ("GET", "/issues/TRAME-7")
    assert (found.id, found.body) == ("TRAME-7", "b")
    assert ScriptedTracker([receipt_page([])]).migration_find_issue(PROJECT, REF) is None


@pytest.mark.parametrize("answer", [
    receipt_page([issue_raw(id="TRAME-7", source_ref=REF), issue_raw(id="TRAME-8", source_ref=REF)]),
    receipt_page([issue_raw(id="TRAME-7")]),                       # filter ignored
    receipt_page([issue_raw(id="TRAME-7", source_ref="other")]),
    receipt_page([issue_raw(id="OTHER-7", source_ref=REF)]),        # other project
    receipt_page([issue_raw(id="TRAME-7", source_ref=REF)], cursor=1),
    {"items": [], "page": {"schema_version": "x", "returned_count": 0, "truncation": False}},
    [],
])
def test_migration_find_issue_refuses_an_answer_that_is_not_the_exact_coordinate(answer):
    with pytest.raises(DevHubTrackerError) as error:
        ScriptedTracker([answer]).migration_find_issue(PROJECT, REF)
    assert error.value.code == "invalid_response"


def test_migration_find_issue_refuses_a_detail_that_lost_the_coordinate():
    tracker = ScriptedTracker([
        receipt_page([issue_raw(id="TRAME-7", source_ref=REF)]),
        issue_raw(id="TRAME-7", source_ref=None),
    ])
    with pytest.raises(DevHubTrackerError) as error:
        tracker.migration_find_issue(PROJECT, REF)
    assert error.value.code == "invalid_response"


ADR_SRC = "linear:adr:FOUNDRY-ADR-0026"
ADR_BODY = "## Decision\n\nOne tracker per repository.\n"
ADR_SHA = hashlib.sha256(ADR_BODY.encode()).hexdigest()


def adr_raw(**overrides):
    value = {
        "id": "TRAME-ADR-0004", "title": "FOUNDRY-ADR-0026 - single tracker",
        "status": "accepted", "body": ADR_BODY, "ref": "9", "version": 1,
        "created": 1, "updated": 2, "source_ref": ADR_SRC,
        "foreign_id": "FOUNDRY-ADR-0026", "source_created": 5, "source_updated": 6,
    }
    value.update(overrides)
    return value


def import_adr(tracker, **overrides):
    arguments = dict(
        adr_id="FOUNDRY-ADR-0026", title="FOUNDRY-ADR-0026 - single tracker", body=ADR_BODY,
        historical_status="accepted", source_ref=ADR_SRC, source_created=5,
        source_updated=6, expected_source_sha256=ADR_SHA,
    )
    arguments.update(overrides)
    return tracker.import_adr(PROJECT, **arguments)


def test_imported_adr_exposes_its_foreign_id_as_id_and_keeps_the_server_id_as_route():
    adr_model = DevHubTracker._to_adr(adr_raw())
    assert (adr_model.id, adr_model.status, adr_model.body) == (
        "FOUNDRY-ADR-0026", "accepted", ADR_BODY)
    native = DevHubTracker._to_adr(adr_raw(foreign_id=None, source_ref=None))
    assert native.id == "TRAME-ADR-0004"
    legacy = {key: value for key, value in adr_raw().items()
              if key not in {"source_ref", "foreign_id", "source_created", "source_updated"}}
    assert DevHubTracker._to_adr(legacy).id == "TRAME-ADR-0004"
    for change in ({"foreign_id": 3}, {"foreign_id": "a b"}, {"source_ref": 3},
                   {"source_created": -1}, {"source_updated": True}):
        with pytest.raises(DevHubTrackerError):
            DevHubTracker._to_adr(adr_raw(**change))


def test_import_adr_posts_the_strict_body_and_returns_the_foreign_id(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    tracker = ScriptedTracker([adr_raw()])
    result = import_adr(tracker)
    method, path, body, kwargs = tracker.calls[0]
    assert (method, path) == ("POST", "/projects/TRAME/adrs/import")
    assert body == {
        "source_ref": ADR_SRC, "foreign_id": "FOUNDRY-ADR-0026",
        "title": "FOUNDRY-ADR-0026 - single tracker", "body": ADR_BODY,
        "body_sha256": ADR_SHA, "status": "accepted",
        "source_created": 5, "source_updated": 6,
    }
    assert kwargs["idempotent"] is True and "proof" not in kwargs
    assert (result.id, result.status, result.body) == ("FOUNDRY-ADR-0026", "accepted", ADR_BODY)


def test_import_adr_sends_the_replacement_source_ref_and_the_issue_links(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    replacement = adr_raw(id="TRAME-ADR-0005", foreign_id="FOUNDRY-ADR-0030",
                          source_ref="linear:adr:FOUNDRY-ADR-0030")
    tracker = ScriptedTracker([
        receipt_page([replacement]),
        adr_raw(status="superseded"),
    ])
    import_adr(tracker, historical_status="superseded", superseded_by="FOUNDRY-ADR-0030",
               issue_refs=("TRAME-1",))
    assert tracker.calls[0][:2] == (
        "GET", "/projects/TRAME/adrs?foreign_id=FOUNDRY-ADR-0030")
    body = tracker.calls[1][2]
    assert body["superseded_by"] == "linear:adr:FOUNDRY-ADR-0030"
    assert body["linked_issue_ids"] == ["TRAME-1"] and "supersedes" not in body
    # a known `supersedes` relation the server cannot carry is refused unsent, not dropped
    from foundry.trackers.base import TrackerCapabilityUnavailableError
    refused = ScriptedTracker([])
    with pytest.raises(TrackerCapabilityUnavailableError):
        import_adr(refused, supersedes=("FOUNDRY-ADR-0001",))
    assert refused.calls == []
    import_adr(ScriptedTracker([adr_raw()]), supersedes=())
    # replacement not imported yet: refused before the import is written
    missing = ScriptedTracker([receipt_page([])])
    with pytest.raises(TrackerConflictError, match="remplacement"):
        import_adr(missing, historical_status="superseded", superseded_by="FOUNDRY-ADR-0030")
    assert len(missing.calls) == 1


@pytest.mark.parametrize("overrides, error", [
    ({"historical_status": "rejected"}, ValueError),
    ({"adr_id": "a b"}, ValueError),
    ({"adr_id": "x" * 121}, ValueError),
    ({"source_ref": "a b"}, ValueError),
    ({"superseded_by": "FOUNDRY-ADR-0030"}, ValueError),         # status is accepted
    ({"issue_refs": ("OTHER-1",)}, ValueError),
    ({"source_created": -1}, ValueError),
    ({"expected_source_sha256": "0" * 64}, TrackerConflictError),
    ({"source_only_issue_refs": ("linear:issue:PAT-1",)}, TrackerCapabilityUnavailableError),
])
def test_import_adr_refuses_before_any_request(monkeypatch, overrides, error):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    tracker = ScriptedTracker([])
    with pytest.raises(error):
        import_adr(tracker, **overrides)
    assert tracker.calls == []


def test_import_adr_maps_conflict_and_missing_route_and_checks_the_answer(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    path = "/projects/TRAME/adrs/import"
    with pytest.raises(TrackerConflictError, match="foreign_id"):
        import_adr(ScriptedTracker([DevHubTrackerError("POST", path, 409, "resource_conflict")]))
    with pytest.raises(TrackerCapabilityUnavailableError, match="migration_adr_import"):
        import_adr(ScriptedTracker([DevHubTrackerError("POST", path, 404, "route_unavailable")]))
    with pytest.raises(DevHubTrackerError) as invalid:
        import_adr(ScriptedTracker([DevHubTrackerError("POST", path, 422, "invalid_request")]))
    assert invalid.value.code == "invalid_request"
    for change in ({"body": "x"}, {"status": "proposed"}, {"foreign_id": "OTHER"},
                   {"source_ref": "other"}, {"source_updated": 7}, {"title": "x"}):
        with pytest.raises(TrackerConflictError, match="divergente"):
            import_adr(ScriptedTracker([adr_raw(**change)]))


def test_import_adr_refuses_a_foreign_id_equal_to_the_server_id_of_another_adr(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})
    native = {"id": "TRAME-ADR-0007", "title": "Native", "status": "accepted", "version": 1,
              "source_ref": None, "foreign_id": None}
    tracker = ScriptedTracker([receipt_page([native])])
    with pytest.raises(TrackerConflictError, match="identifiant serveur"):
        import_adr(tracker, adr_id="TRAME-ADR-0007")
    assert [call[0] for call in tracker.calls] == ["GET"]  # nothing written
    # the same import replayed (the server id was taken by this very source_ref) goes on
    same = {**native, "source_ref": ADR_SRC, "foreign_id": "TRAME-ADR-0007"}
    replay = ScriptedTracker([receipt_page([same]), adr_raw(
        id="TRAME-ADR-0007", foreign_id="TRAME-ADR-0007")])
    assert import_adr(replay, adr_id="TRAME-ADR-0007").id == "TRAME-ADR-0007"
    # the server id of another imported ADR is no clash (its port id is its foreign id)
    other = {**native, "source_ref": "linear:adr:other", "foreign_id": "TRAME-ADR-0002"}
    swapped = ScriptedTracker([receipt_page([other]), adr_raw(
        id="TRAME-ADR-0008", foreign_id="TRAME-ADR-0007")])
    assert import_adr(swapped, adr_id="TRAME-ADR-0007").id == "TRAME-ADR-0007"
    assert [call[0] for call in swapped.calls] == ["GET", "POST"]
    # a free number, and a foreign id that does not carry the project key, need no listing
    free = ScriptedTracker([receipt_page([native]), adr_raw(
        id="TRAME-ADR-0008", foreign_id="TRAME-ADR-0009")])
    import_adr(free, adr_id="TRAME-ADR-0009")
    assert free.calls[0][0] == "GET" and free.calls[1][0] == "POST"


def test_migration_import_adr_refuses_a_known_supersedes_and_a_corpus_checks_it_first(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})

    def snap(number, status="accepted", supersedes="unknown", superseded_by=None, **extra):
        return {
            "id": f"FOUNDRY-ADR-{number:04d}", "source_ref": f"linear:adr:{number}",
            "title": f"ADR {number}", "body": f"body {number}\n", "status": status,
            "relations": {"supersedes": supersedes, "superseded_by": superseded_by,
                          "issues": []},
            "source_created": None, "source_updated": None, **extra,
        }

    single = ScriptedTracker([])
    with pytest.raises(TrackerCapabilityUnavailableError):
        single.migration_import_adr(
            PROJECT, snap(2, supersedes=["FOUNDRY-ADR-0001"]), source_ref="linear:adr:2")
    assert single.calls == []
    # the whole corpus is refused before its first write
    for bad in (snap(3, status="rejected"),
                snap(3, source_only_issue_refs=["linear:issue:PAT-1"]),
                snap(3, supersedes=["FOUNDRY-ADR-0001"]),        # not named by that ADR
                snap(3, supersedes=["FOUNDRY-ADR-0099"])):       # outside the corpus
        tracker = ScriptedTracker([])
        with pytest.raises((ValueError, TrackerCapabilityUnavailableError)):
            tracker.migration_import_adrs(PROJECT, (snap(1), bad))
        assert tracker.calls == []
    # a supersedes that the predecessor's own `superseded_by` covers is not an error
    old, new = snap(1, "superseded", superseded_by="FOUNDRY-ADR-0002"), snap(
        2, supersedes=["FOUNDRY-ADR-0001"])

    def answer(number, status="accepted"):
        return adr_raw(
            id=f"TRAME-ADR-{number:04d}", title=f"ADR {number}", body=f"body {number}\n",
            status=status, source_ref=f"linear:adr:{number}",
            foreign_id=f"FOUNDRY-ADR-{number:04d}", source_created=None, source_updated=None)

    tracker = ScriptedTracker([answer(2), receipt_page([answer(2)]), answer(1, "superseded")])
    assert len(tracker.migration_import_adrs(PROJECT, (old, new))) == 2
    assert all("supersedes" not in call[2] for call in tracker.calls if call[0] == "POST")


def test_migration_find_adr_reads_by_source_ref_or_foreign_id():
    item = {key: value for key, value in adr_raw().items() if key != "body"}
    by_ref = ScriptedTracker([receipt_page([item]), adr_raw()])
    assert by_ref.migration_find_adr(PROJECT, ADR_SRC).id == "FOUNDRY-ADR-0026"
    assert by_ref.calls[0][1] == (
        "/projects/TRAME/adrs?source_ref=linear%3Aadr%3AFOUNDRY-ADR-0026")
    assert by_ref.calls[1][1] == "/adrs/TRAME-ADR-0004"
    by_alias = ScriptedTracker([receipt_page([item]), adr_raw()])
    assert by_alias.migration_find_adr(PROJECT, foreign_id="FOUNDRY-ADR-0026").body == ADR_BODY
    assert by_alias.calls[0][1] == "/projects/TRAME/adrs?foreign_id=FOUNDRY-ADR-0026"
    assert ScriptedTracker([receipt_page([])]).migration_find_adr(PROJECT, ADR_SRC) is None
    with pytest.raises(ValueError):
        ScriptedTracker([]).migration_find_adr(PROJECT)
    with pytest.raises(DevHubTrackerError):
        ScriptedTracker([receipt_page([{**item, "foreign_id": "OTHER"}])]).migration_find_adr(
            PROJECT, ADR_SRC, foreign_id="FOUNDRY-ADR-0026")


def test_migration_import_adrs_writes_replacements_first_and_answers_in_input_order(monkeypatch):
    monkeypatch.setattr("foundry.trackers.devhub.registry.load", lambda: {})

    def snapshot(number, status="accepted", superseded_by=None):
        return {
            "id": f"FOUNDRY-ADR-{number:04d}", "source_ref": f"linear:adr:{number}",
            "title": f"ADR {number}", "body": f"body {number}\n", "status": status,
            "relations": {"supersedes": "unknown", "superseded_by": superseded_by,
                          "issues": ["TRAME-1"]},
            "source_created": None, "source_updated": None,
        }

    def answer(number, status="accepted"):
        return adr_raw(
            id=f"TRAME-ADR-{number:04d}", title=f"ADR {number}", body=f"body {number}\n",
            status=status, source_ref=f"linear:adr:{number}",
            foreign_id=f"FOUNDRY-ADR-{number:04d}", source_created=None, source_updated=None)

    old, new = snapshot(1, "superseded", "FOUNDRY-ADR-0002"), snapshot(2)
    tracker = ScriptedTracker([
        answer(2), receipt_page([answer(2)]), answer(1, "superseded")])
    result = tracker.migration_import_adrs(PROJECT, (old, new))
    assert [item.id for item in result] == ["FOUNDRY-ADR-0001", "FOUNDRY-ADR-0002"]
    posts = [call[2]["source_ref"] for call in tracker.calls if call[0] == "POST"]
    assert posts == ["linear:adr:2", "linear:adr:1"]
    with pytest.raises(TrackerConflictError, match="cycle"):
        ScriptedTracker([]).migration_import_adrs(PROJECT, (
            snapshot(1, "superseded", "FOUNDRY-ADR-0002"),
            snapshot(2, "superseded", "FOUNDRY-ADR-0001")))
    with pytest.raises(TrackerConflictError, match="qualification"):
        ScriptedTracker([]).migration_import_adrs(PROJECT, (new,), qualification={})


def test_adr_lifecycle_operations_keep_the_server_id_binding_for_an_imported_adr():
    # An id of another project (a foreign id of that shape included) is refused unsent.
    tracker = ScriptedTracker([])
    foreign = Adr(id="FOUNDRY-ADR-0026", title="t")
    with pytest.raises(SystemExit, match="Binding DevHub refusé"):
        tracker.set_adr_status(foreign, "deprecated", project=PROJECT)
    with pytest.raises(SystemExit, match="Binding DevHub refusé"):
        tracker.read_adr_issue_relations(foreign, project=PROJECT)
    assert tracker.calls == []


def _swapped_listing():
    # A: foreign id equal to the server id of B, and conversely (replacements imported first).
    a = {"id": "TRAME-ADR-0013", "title": "A", "status": "accepted", "version": 3,
         "foreign_id": "TRAME-ADR-0011", "source_ref": "linear:adr:11"}
    b = {"id": "TRAME-ADR-0011", "title": "B", "status": "accepted", "version": 7,
         "foreign_id": "TRAME-ADR-0013", "source_ref": "linear:adr:13"}
    return receipt_page([a, b])


def test_adr_routes_use_the_server_id_of_the_adr_the_port_id_names_when_ids_are_swapped():
    adr_a = Adr(id="TRAME-ADR-0011", title="A")

    status = ScriptedTracker([_swapped_listing(), {}])
    status.set_adr_status(adr_a, "deprecated", project=PROJECT)
    method, path, body, kwargs = status.calls[1]
    assert path == "/adrs/TRAME-ADR-0013/status" and kwargs["version"] == 3
    assert decode_proof(kwargs["proof"])["resource_id"] == "TRAME-ADR-0013"

    doc = adr_document()
    doc["adr"].update(id="TRAME-ADR-0013", foreign_id="TRAME-ADR-0011")
    linked = dict(doc, linked_issue_ids=["TRAME-1"])
    link = ScriptedTracker([_swapped_listing(), doc, linked])
    link.link_adr_issue(adr_a, "TRAME-1", project=PROJECT)
    assert [call[1] for call in link.calls[1:]] == [
        "/adr-authoring/adrs/TRAME-ADR-0013", "/adr-authoring/adrs/TRAME-ADR-0013"]

    rel = ScriptedTracker([_swapped_listing(), {
        "schema_version": "devhub-adr-relationships.v1",
        "adr": {"id": "TRAME-ADR-0013", "version": 4}, "items": [],
        "page": {"returned_count": 0, "cursor": None, "truncation": False}}])
    rel.read_adr_issue_relations(adr_a, project=PROJECT)
    assert rel.calls[1][1].startswith("/adrs/TRAME-ADR-0013/relationships")


def test_adr_routes_refuse_an_adr_absent_from_the_listing_and_a_swapped_document():
    absent = Adr(id="TRAME-ADR-0099", title="x")
    for call in (
        lambda t: t.set_adr_status(absent, "deprecated", project=PROJECT),
        lambda t: t.link_adr_issue(absent, "TRAME-1", project=PROJECT),
        lambda t: t.read_adr_issue_relations(absent, project=PROJECT),
    ):
        tracker = ScriptedTracker([_swapped_listing()])
        with pytest.raises(DevHubTrackerError, match="invalid_response"):
            call(tracker)
        assert len(tracker.calls) == 1
    # the authoring document answers another ADR than the resolved one: nothing is written
    other = adr_document()
    other["adr"]["id"] = "TRAME-ADR-0011"
    tracker = ScriptedTracker([_swapped_listing(), other])
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        tracker.link_adr_issue(Adr(id="TRAME-ADR-0011", title="A"), "TRAME-1", project=PROJECT)
    assert [call[0] for call in tracker.calls] == ["GET", "GET"]


def test_server_supplied_malformed_adr_ids_and_cursors_are_invalid_response():
    item = adr_raw()
    for bad in ("garbage", "OTHER-ADR-0004"):
        with pytest.raises(DevHubTrackerError, match="invalid_response"):
            ScriptedTracker([receipt_page([{**item, "id": bad}])]).migration_find_adr(
                PROJECT, ADR_SRC)
    with pytest.raises(TrackerConflictError):
        import_adr(ScriptedTracker([adr_raw(id="garbage")]))
    with pytest.raises(DevHubTrackerError, match="invalid_response"):
        ScriptedTracker([receipt_page([item], cursor=None) | {
            "page": {"schema_version": PAGE_V1, "returned_count": 1,
                     "truncation": False, "cursor": 3}}]).migration_find_adr(PROJECT, ADR_SRC)
