"""PAT-296: live DevHub conformance suite against a disposable instance.

Foundry owns the need as executable tests; ``docs/devhub-requirements.md`` is the single
exchange page (need, test, status, questions).  Every test below carries one need id
(``@need`` or ``@gap``) and the page lists the same ids: ``tests/test_devhub_requirements.py``
checks that the two stay equal.

Safety (``tests/devhub_live_gate.py``): the suite is skipped unless ``DEVHUB_LIVE_URL``, the
exact ``DEVHUB_LIVE_CONFIRM`` phrase and dedicated credentials are exported; it refuses a
production-looking target and an instance that holds a project it did not create.  That
refusal proves nothing on an instance with local accounts: DHN-42 alone targets one
(``DEVHUB_LIVE_LOCAL_ACCOUNTS=1``, a declaration, never a detection) and its target must be
disposable by construction.  The suite never reads the user's configuration or registry.
Without the URL every test is skipped.

A need DevHub (or the adapter) does not cover yet is a ``@gap`` test: strict ``xfail``
carrying the need id and ``raises=`` the exact exception of the current gap, so a setup
failure or another exception is reported as a failure or an error, never ``xfailed``.  It
is never a pass; when the gap closes the test XPASSes, which strict mode reports as a
failure, so the owner must turn it into a plain ``@need`` test and update the page.  Run
it with the command in ``docs/devhub-requirements.md``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass, replace

import pytest

import devhub_live_gate as gate
from foundry import campaign_cadence, registry, write
from foundry.models import (
    EpicClosureChild,
    EpicClosureReceipt,
    Project,
    TransitionContext,
)
from foundry.trackers.base import (
    IssueUnavailableError,
    ReleaseScopeUnavailableError,
    TrackerConflictError,
)
from foundry.trackers.devhub import AdrRelationFrozenError, DevHubTracker, DevHubTrackerError

pytestmark = [pytest.mark.integration, pytest.mark.devhub_live]


def need(need_id: str):
    """Mark a test as the proof of one need expected green."""
    def decorate(function):
        function.devhub_need = need_id
        return function
    return decorate


def gap(need_id: str, owner: str, reason: str, *, raises):
    """Mark a test as the expected-red proof of one need (strict xfail, never a pass).

    ``raises`` is the exact exception the current adapter or server answer produces, so
    an unrelated failure (gate refusal at setup, signature bug) is a failure or an error,
    never xfailed.
    """
    def decorate(function):
        function.devhub_need = need_id
        return pytest.mark.xfail(
            strict=True, raises=raises, reason=f"{need_id} [{owner}] {reason}",
        )(function)
    return decorate


@dataclass(frozen=True)
class Suite:
    tracker: DevHubTracker
    project: Project
    repository: str
    config: gate.LiveConfig


@pytest.fixture(scope="session")
def suite() -> Suite:
    try:
        config, reason = gate.read_environment()
    except gate.GateRefusal as refusal:
        pytest.fail(f"live DevHub suite refused: {refusal}", pytrace=False)
    if config is None:
        pytest.skip(reason)
    tracker = gate.build_tracker(config)
    try:
        gate.assert_disposable_instance(tracker)
    except gate.GateRefusal as refusal:
        pytest.fail(f"live DevHub suite refused: {refusal}", pytrace=False)
    key = gate.new_project_key()
    repository = f"github.com/foundry-live/{key.lower()}"
    try:
        project = tracker.provision_project("Foundry live conformance", key, repository)
    except DevHubTrackerError as error:  # never let a setup failure look like a gap
        pytest.fail(f"live DevHub suite setup failed ({error.code})", pytrace=False)
    return Suite(tracker=tracker, project=project, repository=repository, config=config)


@pytest.fixture(scope="session")
def local_accounts_suite() -> Suite:
    """DHN-42 only: the target is declared to run with local accounts.

    Same gate as ``suite`` (a refusal is an error before the declaration is read), but no
    project is created here: on such an instance that creation is the need under test.
    """
    try:
        config, reason = gate.read_environment()
    except gate.GateRefusal as refusal:
        pytest.fail(f"live DevHub suite refused: {refusal}", pytrace=False)
    if config is None:
        pytest.skip(reason)
    if not gate.local_accounts_declared():
        pytest.skip(
            f"{gate.ENV_LOCAL_ACCOUNTS}=1 not set: DHN-42 is only checked on an instance "
            "with local accounts"
        )
    tracker = gate.build_tracker(config)
    try:
        gate.assert_disposable_instance(tracker)
    except gate.GateRefusal as refusal:
        pytest.fail(f"live DevHub suite refused: {refusal}", pytrace=False)
    key = gate.new_project_key()
    repository = f"github.com/foundry-live/{key.lower()}"
    # Placeholder project (nothing is created): the test provisions the real one.
    return Suite(
        tracker=tracker, project=Project(key=key, id=""), repository=repository,
        config=config,
    )


# --- helpers -------------------------------------------------------------------------

def _unique(label: str) -> str:
    return f"{label} {secrets.token_hex(6)}"


def _create(suite: Suite, title: str, body: str = "", *, parent=None, **fields):
    base = {"State": "backlog", "Priority": "P3", "Type": "Task"}
    base.update(fields)
    return suite.tracker.create_issue(
        suite.project, title, body, fields=base, parent=parent,
    )


def _digests(issue_id: str) -> dict:
    def part(tag):
        return hashlib.sha256(f"{issue_id}:{tag}".encode()).hexdigest()
    return {
        "head": part("head")[:40], "base": part("base")[:40],
        "digest": part("diff"), "merge": part("merge")[:40],
    }


def _pr_url(suite: Suite) -> str:
    return f"https://github.com/foundry-live/{suite.project.key.lower()}/pull/1"


def _review_context(suite: Suite, issue_id: str) -> TransitionContext:
    d = _digests(issue_id)
    return TransitionContext(
        pr_url=_pr_url(suite), head_sha=d["head"], base_sha=d["base"],
        review_digest=d["digest"],
    )


def _done_context(suite: Suite, issue_id: str) -> TransitionContext:
    d = _digests(issue_id)
    return TransitionContext(
        pr_url=_pr_url(suite), head_sha=d["head"], base_sha=d["base"],
        review_digest=d["digest"], merge_sha=d["merge"],
    )


def _start(suite: Suite, issue_id: str, from_state: str = "backlog") -> None:
    for state in suite.tracker.start_transition_path(from_state):
        suite.tracker.set_state(issue_id, state, project=suite.project)


def _to_review(suite: Suite, issue_id: str) -> None:
    _start(suite, issue_id)
    suite.tracker.update_fields(issue_id, {"GitHub PR": _pr_url(suite)}, project=suite.project)
    suite.tracker.set_state(
        issue_id, "review", context=_review_context(suite, issue_id), project=suite.project,
    )


def _to_done(suite: Suite, issue_id: str) -> None:
    _to_review(suite, issue_id)
    suite.tracker.set_state(
        issue_id, "done", context=_done_context(suite, issue_id), project=suite.project,
    )


def _refused(call, *, statuses=range(400, 500), code: str | None = None) -> DevHubTrackerError:
    with pytest.raises(DevHubTrackerError) as caught:
        call()
    assert caught.value.status in statuses, (caught.value.status, caught.value.code)
    if code is not None:
        assert caught.value.code == code
    return caught.value


def _has_link(issue, link_type: str, target: str) -> bool:
    return any(item.type == link_type and item.target == target for item in issue.links)


def _closure_receipt(suite: Suite, epic_id: str, child_ids, states=None) -> EpicClosureReceipt:
    tracker = suite.tracker
    epic = tracker._issue_raw(epic_id)
    children = []
    for child_id in sorted(child_ids):
        raw = tracker._issue_raw(child_id)
        children.append(EpicClosureChild(
            id=child_id, version=raw["version"],
            state=(states or {}).get(child_id, raw["state"]),
        ))
    return EpicClosureReceipt(
        project_key=suite.project.key, project_id=suite.project.id, parent_id=epic_id,
        parent_version=epic["version"], parent_type=epic.get("type") or "Epic",
        parent_ac_done=epic["ac_done"], parent_ac_total=epic["ac_total"],
        children=tuple(children), issued_at=int(time.time() * 1000),
        nonce=secrets.token_urlsafe(24),
    )


# --- project and identity -------------------------------------------------------------

@need("DHN-01")
def test_dhn_01_provision_project_is_repository_bound_and_idempotent(suite):
    key = gate.new_project_key()
    repository = f"github.com/foundry-live/{key.lower()}"
    first = suite.tracker.provision_project("Foundry live provisioning", key, repository)
    again = suite.tracker.provision_project("Foundry live provisioning", key, repository)
    assert (first.key, first.id) == (again.key, again.id) and first.key == key
    assert first.extra == {"canonical_repo": repository}


@need("DHN-02")
def test_dhn_02_resolve_project_by_canonical_repository(suite, monkeypatch):
    registered = Project(
        key=suite.project.key, id=suite.project.id,
        extra={"canonical_repo": suite.repository},
    )
    monkeypatch.setattr(registry, "resolve", lambda tracker, repo, cwd=None: registered)
    resolved = suite.tracker.resolve_project("live")
    assert (resolved.key, resolved.id) == (suite.project.key, suite.project.id)
    contradicting = Project(
        key=suite.project.key, id="999999999", extra={"canonical_repo": suite.repository},
    )
    monkeypatch.setattr(registry, "resolve", lambda tracker, repo, cwd=None: contradicting)
    with pytest.raises(SystemExit):
        suite.tracker.resolve_project("live")


@need("DHN-03")
def test_dhn_03_unauthenticated_request_is_refused(suite):
    stranger = DevHubTracker(
        url=suite.config.url, token=secrets.token_urlsafe(32),
        proof_secret=secrets.token_urlsafe(32),
    )
    _refused(stranger.list_project_catalog, statuses={401, 403})


# --- issues ---------------------------------------------------------------------------

@need("DHN-04")
def test_dhn_04_create_issue_reads_back_fields_body_and_acceptance_counts(suite):
    body = "Context\n\n- [ ] first criterion\n- [x] second criterion\n"
    title = _unique("live create")
    created = _create(
        suite, title, body, Priority="P2", Estimate=3, Type="Task", Labels=["foundry-live"],
    )
    assert re.fullmatch(rf"{suite.project.key}-[0-9]+", created.id)
    read = suite.tracker.get_issue(created.id)
    assert (read.title, read.body, read.state) == (title, body, "backlog")
    assert (read.priority, read.estimate, read.type) == ("P2", 3, "Task")
    assert "foundry-live" in read.labels
    assert (read.ac_done, read.ac_total) == (1, 2)
    assert isinstance(read.version, int) and read.version >= 1


@need("DHN-05")
def test_dhn_05_update_fields_changes_only_the_named_fields(suite):
    issue = _create(suite, _unique("live update"), "body", Priority="P3", Estimate=2)
    before = suite.tracker.get_issue(issue.id)
    updated = suite.tracker.update_fields(
        issue.id, {"Priority": "P1", "Estimate": 5}, project=suite.project,
    )
    assert (updated.priority, updated.estimate) == ("P1", 5)
    assert (updated.title, updated.body, updated.type) == (before.title, before.body, before.type)
    assert updated.version > before.version


@need("DHN-06")
def test_dhn_06_stale_version_is_refused_and_leaves_no_write(suite):
    tracker = suite.tracker
    issue = _create(suite, _unique("live stale"), "body")
    version = tracker._issue_raw(issue.id)["version"]
    tracker.add_comment(issue.id, "advance the version", project=suite.project)
    _refused(
        lambda: tracker._application_req(
            "POST", f"/issues/{issue.id}/comments", {"text": "stale must not persist"},
            version=version, idempotent=True, idempotency_key=f"live-stale-{secrets.token_hex(6)}",
        ),
        statuses={409}, code="version_conflict",
    )
    texts = [item["text"] for item in tracker.get_issue(issue.id).comments]
    assert "stale must not persist" not in texts


@need("DHN-07")
def test_dhn_07_idempotent_replay_returns_one_effect_and_divergence_conflicts(suite):
    tracker = suite.tracker
    issue = _create(suite, _unique("live replay"), "body")
    version = tracker._issue_raw(issue.id)["version"]
    key = f"live-replay-{secrets.token_hex(6)}"
    path = f"/issues/{issue.id}/comments"
    first = tracker._application_req(
        "POST", path, {"text": "once"}, version=version, idempotent=True, idempotency_key=key,
    )
    replay = tracker._application_req(
        "POST", path, {"text": "once"}, version=version, idempotent=True, idempotency_key=key,
    )
    assert replay == first
    _refused(
        lambda: tracker._application_req(
            "POST", path, {"text": "different"}, version=version, idempotent=True,
            idempotency_key=key,
        ),
        statuses={409}, code="idempotency_conflict",
    )
    texts = [item["text"] for item in tracker.get_issue(issue.id).comments]
    assert texts.count("once") == 1 and "different" not in texts


@need("DHN-08")
def test_dhn_08_search_lists_issues_and_narrows_by_query(suite):
    marker = f"needle{secrets.token_hex(5)}"
    wanted = _create(suite, f"live search {marker}", "body")
    other = _create(suite, _unique("live search other"), "body")
    listed = {item.id for item in suite.tracker.search(suite.project)}
    assert {wanted.id, other.id} <= listed
    narrowed = {item.id for item in suite.tracker.search(suite.project, marker)}
    assert narrowed == {wanted.id}


@need("DHN-09")
def test_dhn_09_search_paginates_past_one_hundred_issues(suite):
    for index in range(105):
        _create(suite, f"live page {index} {secrets.token_hex(3)}", "")
    listed = suite.tracker.search(suite.project)
    ids = [item.id for item in listed]
    assert len(ids) == len(set(ids)) and len(ids) >= 105


@need("DHN-10")
def test_dhn_10_child_creation_links_both_ends(suite):
    epic = _create(suite, _unique("live epic"), "", Type="Epic")
    child = _create(suite, _unique("live child"), "- [ ] done", parent=epic.id)
    assert _has_link(suite.tracker.get_issue(child.id), "subtask-of", epic.id)
    assert _has_link(suite.tracker.get_issue(epic.id), "parent-of", child.id)


@need("DHN-11")
def test_dhn_11_dependency_links_round_trip_with_inverse(suite):
    inverse = {"depends-on": "blocks", "blocks": "depends-on", "relates": "relates"}
    for link_type, back in inverse.items():
        source = _create(suite, _unique(f"live {link_type} src"), "")
        target = _create(suite, _unique(f"live {link_type} dst"), "")
        suite.tracker.link(source.id, link_type, target.id, project=suite.project)
        assert _has_link(suite.tracker.get_issue(source.id), link_type, target.id), link_type
        assert _has_link(suite.tracker.get_issue(target.id), back, source.id), link_type


@need("DHN-12")
def test_dhn_12_existing_issue_can_be_reparented_in_both_directions(suite):
    epic = _create(suite, _unique("live reparent epic"), "", Type="Epic")
    by_child = _create(suite, _unique("live reparent child"), "")
    by_parent = _create(suite, _unique("live reparent child 2"), "")
    suite.tracker.link(by_child.id, "subtask-of", epic.id, project=suite.project)
    suite.tracker.link(epic.id, "parent-of", by_parent.id, project=suite.project)
    epic_read = suite.tracker.get_issue(epic.id)
    assert _has_link(epic_read, "parent-of", by_child.id)
    assert _has_link(epic_read, "parent-of", by_parent.id)
    assert _has_link(suite.tracker.get_issue(by_child.id), "subtask-of", epic.id)
    assert _has_link(suite.tracker.get_issue(by_parent.id), "subtask-of", epic.id)


@need("DHN-13")
def test_dhn_13_comments_round_trip_in_order(suite):
    issue = _create(suite, _unique("live comments"), "")
    for text in ("first note", "second note"):
        suite.tracker.add_comment(issue.id, text, project=suite.project)
    comments = suite.tracker.get_issue(issue.id).comments
    assert [item["text"] for item in comments] == ["first note", "second note"]
    assert all(type(item["created"]) is int for item in comments)


@need("DHN-14")
def test_dhn_14_every_comment_is_returned_past_one_hundred(suite):
    issue = _create(suite, _unique("live many comments"), "")
    for index in range(105):
        suite.tracker.add_comment(issue.id, f"note {index}", project=suite.project)
    comments = suite.tracker.get_issue(issue.id).comments
    assert [item["text"] for item in comments] == [f"note {index}" for index in range(105)]


@need("DHN-15")
def test_dhn_15_epic_subgraph_projection_is_bounded_and_complete(suite):
    epic = _create(suite, _unique("live subgraph"), "", Type="Epic")
    children = [_create(suite, _unique("live node"), "", parent=epic.id) for _ in range(2)]
    graph = suite.tracker.get_epic_subgraph(suite.project, epic.id, depth=2, nodes=10)
    text = json.dumps(graph)
    assert epic.id in text and all(child.id in text for child in children)


@need("DHN-16")
def test_dhn_16_audit_projection_holds_one_receipt_per_operation(suite):
    tracker = suite.tracker
    issue = _create(suite, _unique("live audit"), "")
    other = _create(suite, _unique("live audit other"), "")
    tracker.update_fields(issue.id, {"Priority": "P2"}, project=suite.project)
    tracker.set_state(issue.id, "ready", project=suite.project)
    tracker.link(issue.id, "relates", other.id, project=suite.project)
    tracker.add_comment(issue.id, "audited", project=suite.project)
    events = tracker.audit(suite.project, resource=f"issue:{issue.id}")
    seen = {event["operation"] for event in events if event["result"] == "success"}
    assert {
        "issue.create", "issue.update", "issue.transition", "issue.link", "issue.comment",
    } <= seen


# --- lifecycle ------------------------------------------------------------------------

@need("DHN-17")
def test_dhn_17_start_path_and_resume_from_blocked(suite):
    tracker = suite.tracker
    issue = _create(suite, _unique("live start"), "")
    _start(suite, issue.id)
    assert tracker.get_issue(issue.id).state == "in-progress"
    tracker.set_state(issue.id, "blocked", project=suite.project)
    assert tracker.get_issue(issue.id).state == "blocked"
    _start(suite, issue.id, "blocked")
    assert tracker.get_issue(issue.id).state == "in-progress"


@need("DHN-18")
def test_dhn_18_review_needs_a_valid_proof_and_can_return_to_in_progress(suite):
    tracker = suite.tracker
    issue = _create(suite, _unique("live review"), "")
    _to_review(suite, issue.id)
    assert tracker.get_issue(issue.id).state == "review"
    tracker.set_state(issue.id, "in-progress", project=suite.project)
    assert tracker.get_issue(issue.id).state == "in-progress"

    forged = DevHubTracker(
        url=suite.config.url, token=suite.config.token,
        proof_secret=secrets.token_urlsafe(32),
    )
    victim = _create(suite, _unique("live forged review"), "")
    _start(suite, victim.id)
    tracker.update_fields(victim.id, {"GitHub PR": _pr_url(suite)}, project=suite.project)
    _refused(lambda: forged.set_state(
        victim.id, "review", context=_review_context(suite, victim.id), project=suite.project,
    ))
    assert tracker.get_issue(victim.id).state == "in-progress"


@need("DHN-19")
def test_dhn_19_done_is_terminal_and_unproven_jumps_are_refused(suite):
    tracker = suite.tracker
    issue = _create(suite, _unique("live done"), "")
    _to_done(suite, issue.id)
    assert tracker.get_issue(issue.id).state == "done"
    _refused(
        lambda: tracker.set_state(issue.id, "in-progress", project=suite.project),
        statuses={409},
    )
    jumper = _create(suite, _unique("live jump"), "")
    _refused(lambda: tracker.set_state(
        jumper.id, "review", context=_review_context(suite, jumper.id), project=suite.project,
    ), statuses={409})


@need("DHN-20")
def test_dhn_20_acceptance_sync_patches_only_with_the_expected_body(suite):
    tracker = suite.tracker
    body = "Goal\n\n- [ ] one\n- [ ] two\n"
    updated = body.replace("[ ]", "[x]")
    issue = _create(suite, _unique("live ac sync"), body)
    proof_id = hashlib.sha256(issue.id.encode()).hexdigest()
    assert tracker.sync_acceptance_body(
        issue.id, body, updated, proof_id, project=suite.project,
    ) is True
    read = tracker.get_issue(issue.id)
    assert (read.body, read.ac_done, read.ac_total) == (updated, 2, 2)
    assert tracker.sync_acceptance_body(
        issue.id, body, updated, proof_id, project=suite.project,
    ) is False
    stale_proof = hashlib.sha256(f"{issue.id}:stale".encode()).hexdigest()
    with pytest.raises(TrackerConflictError):
        tracker.sync_acceptance_body(
            issue.id, body, updated + "x", stale_proof, project=suite.project,
        )


@need("DHN-21")
def test_dhn_21_arbitrary_body_update_is_checked_against_the_expected_body(suite):
    tracker = suite.tracker
    body, replacement = "before\n", "after\n"
    issue = _create(suite, _unique("live update body"), body)
    assert tracker.update_body(issue, body, replacement, project=suite.project) is True
    assert tracker.get_issue(issue.id).body == replacement
    assert tracker.update_body(issue, replacement, replacement, project=suite.project) is False
    with pytest.raises(TrackerConflictError):
        tracker.update_body(issue, body, "other\n", project=suite.project)
    assert tracker.get_issue(issue.id).body == replacement


# --- Epic closure ---------------------------------------------------------------------

@need("DHN-22")
def test_dhn_22_epic_closure_is_atomic_audited_and_replayable(suite):
    tracker = suite.tracker
    epic = _create(suite, _unique("live closure"), "", Type="Epic")
    child = _create(suite, _unique("live closure child"), "", parent=epic.id)
    _to_done(suite, child.id)
    receipt = _closure_receipt(suite, epic.id, [child.id])
    outcome = tracker.close_epic(suite.project, receipt)
    assert outcome.receipt == receipt and outcome.replayed is False
    assert outcome.closed_parent_version == receipt.parent_version + 1
    assert tracker.get_issue(epic.id).state == "done"
    replay = tracker.close_epic(suite.project, receipt)
    assert replay.replayed is True and replay.audit_id == outcome.audit_id
    recovered = tracker.get_epic_closure(suite.project, epic.id)
    assert recovered is not None and recovered.audit_id == outcome.audit_id


@need("DHN-23")
def test_dhn_23_epic_closure_refuses_a_graph_that_differs_from_the_receipt(suite):
    tracker = suite.tracker
    epic = _create(suite, _unique("live closure refuse"), "", Type="Epic")
    child = _create(suite, _unique("live open child"), "", parent=epic.id)
    lying = _closure_receipt(suite, epic.id, [child.id], states={child.id: "done"})
    _refused(lambda: tracker.close_epic(suite.project, lying), statuses={409})
    assert tracker.get_issue(epic.id).state != "done"
    assert tracker.get_epic_closure(suite.project, epic.id) is None


@need("DHN-24")
def test_dhn_24_epic_closes_over_a_dropped_child_by_explicit_acceptance(suite, monkeypatch):
    tracker = suite.tracker
    assert tracker.epic_dropped_closure_supported is True
    epic = _create(suite, _unique("live dropped closure"), "", Type="Epic")
    # PAT-316: both children carry criteria; the dropped one keeps them open.
    done_child = _create(suite, _unique("live done child"), "- [x] met", parent=epic.id)
    dropped_child = _create(
        suite, _unique("live dropped child"), "- [ ] abandoned", parent=epic.id,
    )
    _to_done(suite, done_child.id)
    tracker.set_state(dropped_child.id, "dropped", project=suite.project)
    # Starting point of the test: both children carry one criterion, the dropped one open.
    done_read, dropped_read = tracker.get_issue(done_child.id), tracker.get_issue(dropped_child.id)
    assert (done_read.ac_total, dropped_read.ac_total) == (1, 1)
    assert dropped_read.ac_done == 0
    monkeypatch.setattr(write, "mutation_project", lambda _tracker: suite.project)
    # PAT-ADR-0030: Foundry refuses before the atomic call until the child is named.
    with pytest.raises(SystemExit, match="doit nommer exactement"):
        write.close_epic(tracker, epic.id)
    assert tracker.get_issue(epic.id).state != "done"
    outcome = write.close_epic(
        tracker, epic.id, human_verdict="accepted", accept_dropped=(dropped_child.id,),
    )
    assert {(child.id, child.state) for child in outcome.receipt.children} == {
        (done_child.id, "done"), (dropped_child.id, "dropped")}
    assert tracker.get_issue(epic.id).state == "done"
    assert write.close_epic(
        tracker, epic.id, human_verdict="accepted", accept_dropped=(dropped_child.id,),
    ).replayed is True


@need("DHN-25")
def test_dhn_25_acceptance_override_leaves_a_typed_receipt(suite):
    # PAT-318: route of DEVHUB-106 (DevHub merge f347f38).
    tracker = suite.tracker
    assert tracker.acceptance_override_projection_supported is True
    issue = _create(suite, _unique("live override"), "- [ ] unchecked")
    _to_review(suite, issue.id)
    context = _review_context(suite, issue.id)
    version = tracker.get_issue(issue.id).version
    assert tracker.project_acceptance_override(
        issue.id, "maintainer-override", context, project=suite.project,
    ) is True
    after = tracker.get_issue(issue.id)
    assert (after.state, after.version) == ("review", version)  # state and version unchanged
    receipts = tracker.read_proof_receipts(issue.id, project=suite.project)
    assert [item["action"] for item in receipts] == ["review", "acceptance_override"]
    assert receipts[-1]["reason"] == "maintainer-override"
    assert (receipts[-1]["head_sha"], receipts[-1]["review_digest"]) == (
        context.head_sha, context.review_digest)
    # one override per review generation: the exact replay writes nothing
    assert tracker.project_acceptance_override(
        issue.id, "maintainer-override", context, project=suite.project,
    ) is False
    with pytest.raises(TrackerConflictError):
        tracker.project_acceptance_override(
            issue.id, "another-reason", context, project=suite.project,
        )


def _human_decision(issue_id: str, body: str, evidence_id: str, from_state="backlog") -> dict:
    from foundry.routing import acceptance_criteria, acceptance_digest

    criteria = acceptance_criteria(body)
    return {
        "schema": "foundry.issue-acceptance-decision.v1", "issue": issue_id,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "ac_digest": acceptance_digest(criteria), "from_state": from_state,
        "authority": "maintainer-direct", "decided_on": "2026-10-20",
        "verdict": "accepted", "delivery": "none",
        "criteria": [
            {**item, "verdict": "pass", "evidence": [{"type": "issue", "id": evidence_id}],
             "unknowns": []}
            for item in criteria
        ],
    }


@need("DHN-26")
def test_dhn_26_non_code_issue_is_accepted_by_a_human_decision_without_a_pr(suite):
    # PAT-318 / PAT-ADR-0025: route of DEVHUB-106 (DevHub merge f347f38).
    body = "- [ ] decided offline"
    issue = _create(suite, _unique("live human acceptance"), body)
    cited = _create(suite, _unique("live cited evidence"), "")
    decision = _human_decision(issue.id, body, cited.id)
    accepted = suite.tracker.accept_issue(issue.id, decision, project=suite.project)
    assert accepted.state == "done"
    (receipt,) = suite.tracker.read_proof_receipts(issue.id, project=suite.project)
    assert receipt["action"] == "human_acceptance"
    assert (receipt["pr_url"], receipt["head_sha"], receipt["base_sha"],
            receipt["review_digest"]) == (None, None, None, None)
    assert receipt["decision"] == decision
    # an exact replay converges; another decision is refused
    assert suite.tracker.accept_issue(issue.id, decision, project=suite.project).state == "done"
    other = {**decision, "decided_on": "2026-10-21"}
    with pytest.raises(TrackerConflictError):
        suite.tracker.accept_issue(issue.id, other, project=suite.project)


@need("DHN-27")
def test_dhn_27_started_issue_can_be_abandoned(suite):
    issue = _create(suite, _unique("live abandon"), "")
    _start(suite, issue.id)
    suite.tracker.set_state(issue.id, "dropped", project=suite.project)
    assert suite.tracker.get_issue(issue.id).state == "dropped"


@need("DHN-28")
def test_dhn_28_started_issue_can_return_to_backlog(suite):
    issue = _create(suite, _unique("live return"), "")
    _start(suite, issue.id)
    suite.tracker.set_state(issue.id, "backlog", project=suite.project)
    assert suite.tracker.get_issue(issue.id).state == "backlog"


# --- ADR ------------------------------------------------------------------------------

def _new_adr(suite: Suite, label: str):
    return suite.tracker.create_adr(
        suite.project, _unique(label), f"## Context\n\n{label}\n", "proposed",
    )


@need("DHN-29")
def test_dhn_29_adr_is_created_proposed_and_listed_with_its_exact_body(suite):
    adr = _new_adr(suite, "live adr create")
    assert re.fullmatch(rf"{suite.project.key}-ADR-[0-9]{{4,}}", adr.id)
    assert adr.status == "proposed"
    listed = {item.id: item for item in suite.tracker.list_adrs(suite.project)}
    assert listed[adr.id].title == adr.title
    assert listed[adr.id].body == "## Context\n\nlive adr create\n"


@need("DHN-30")
def test_dhn_30_adr_status_moves_proposed_to_accepted_to_deprecated(suite):
    tracker = suite.tracker
    adr = _new_adr(suite, "live adr status")
    tracker.set_adr_status(adr, "accepted", project=suite.project)
    statuses = {item.id: item.status for item in tracker.list_adrs(suite.project)}
    assert statuses[adr.id] == "accepted"
    tracker.set_adr_status(adr, "deprecated", project=suite.project)
    statuses = {item.id: item.status for item in tracker.list_adrs(suite.project)}
    assert statuses[adr.id] == "deprecated"


@need("DHN-31")
def test_dhn_31_status_route_refuses_superseded_and_leaves_the_adr_accepted(suite):
    tracker = suite.tracker
    adr = _new_adr(suite, "live adr superseded refused")
    tracker.set_adr_status(adr, "accepted", project=suite.project)
    version = tracker._application_req("GET", f"/adrs/{adr.id}")["version"]
    # The adapter refuses `superseded` locally (PAT-326); call the server directly.
    proof = tracker._proof(action="adr-status", project_key=suite.project.key,
                           resource_id=adr.id, version=version, target_status="superseded")
    _refused(
        lambda: tracker._application_req(
            "POST", f"/adrs/{adr.id}/status", {"status": "superseded"},
            version=version, proof=proof, idempotent=True,
        ),
        statuses={409}, code="invalid_transition",
    )
    statuses = {item.id: item.status for item in tracker.list_adrs(suite.project)}
    assert statuses[adr.id] == "accepted"
    assert tracker._application_req("GET", f"/adrs/{adr.id}")["version"] == version


@need("DHN-32")
def test_dhn_32_adr_is_superseded_by_a_named_replacement(suite):
    tracker = suite.tracker
    # Own project: historically (up to DevHub 8028000) the status route could leave an
    # unlinked superseded ADR in the suite project, which makes every `supersedes` there
    # `unknown` (PAT-320); kept isolated, the route being refused since DEVHUB-116.
    key = gate.new_project_key()
    repository = f"github.com/foundry-live/{key.lower()}"
    project = tracker.provision_project("Foundry live supersession", key, repository)
    own = replace(suite, project=project, repository=repository)
    old, new = _new_adr(own, "live adr old"), _new_adr(own, "live adr new")
    # Foundry's order is `adr accept` then `adr supersede`: both ADRs are accepted first,
    # and the replacement keeps its status, body and version (the Linear and GitHub
    # Projects adapters refuse a non-accepted replacement).
    tracker.set_adr_status(old, "accepted", project=project)
    tracker.set_adr_status(new, "accepted", project=project)
    before = {item.id: item for item in tracker.list_adrs(project)}[new.id]
    # `Adr` carries no version: the detail route `GET /adrs/:id` does.
    version_before = tracker._application_req("GET", f"/adrs/{new.id}")["version"]
    tracker.supersede_adr(old, new.id, project=project)
    after = {item.id: item for item in tracker.list_adrs(project)}
    assert after[old.id].status == "superseded"
    assert (after[new.id].status, after[new.id].body) == ("accepted", before.body)
    assert tracker._application_req("GET", f"/adrs/{new.id}")["version"] == version_before
    # `Adr` carries no relation: the narrowest port read of the named link is
    # `migration_export_adrs`, whose `relations` hold `superseded_by` / `supersedes`.
    # The DevHub adapter reads both ends from `GET /adr-decisions/adrs/:id` (PAT-320).
    relations = {
        entry["adr"].id: entry["relations"]
        for entry in tracker.migration_export_adrs(project)
    }
    assert relations[old.id]["superseded_by"] == new.id
    assert relations[new.id]["supersedes"] == [old.id]
    # A replay writes nothing (also once the replacement has left `accepted`, unit-tested).
    tracker.supersede_adr(old, new.id, project=project)
    # An unlinked supersession can no longer be created since DEVHUB-116 (DHN-31); its
    # `unknown` reading, still possible for ADRs superseded before, is unit-tested.


@need("DHN-33")
def test_dhn_33_adr_issue_relation_set_before_acceptance_survives_it(suite):
    tracker = suite.tracker
    adr = _new_adr(suite, "live adr link")
    issue = _create(suite, _unique("live adr linked issue"), "")
    tracker.link_adr_issue(adr, issue.id, project=suite.project)
    tracker.set_adr_status(adr, "accepted", project=suite.project)
    assert tracker.read_adr_issue_relations(adr, project=suite.project) == [issue.id]
    other = _create(suite, _unique("live adr late issue"), "")
    with pytest.raises(AdrRelationFrozenError):
        tracker.link_adr_issue(adr, other.id, project=suite.project)
    assert tracker.read_adr_issue_relations(adr, project=suite.project) == [issue.id]


# --- selective import (FOUNDRY-ADR-0026) ----------------------------------------------

def _issue_snapshot(source_ref: str, title: str, body: str) -> dict:
    return {
        "kind": "issue", "source_ref": source_ref, "id": source_ref.rsplit(":", 1)[1],
        "title": title, "body": body,
        "attributes": {"type": "Task", "priority": "P2", "estimate": None, "state": "backlog"},
        "links": [], "acceptance": {"checkboxes": [" "], "projection": {}},
        "digest": "", "target_id": None, "status": "pending", "exceptions": [],
    }


@need("DHN-34")
def test_dhn_34_living_issue_is_imported_once_by_exact_source_coordinate(suite):
    tracker = suite.tracker
    ref = f"linear:issue:PAT-{secrets.randbelow(10**6) + 1000}"
    title, body = _unique("live imported"), "- [ ] imported criterion\n"
    assert tracker.migration_find_issue(suite.project, ref) is None
    snapshot = _issue_snapshot(ref, title, body)
    first = tracker.migration_import_issue(suite.project, snapshot, source_ref=ref)
    again = tracker.migration_import_issue(suite.project, snapshot, source_ref=ref)
    assert again.id == first.id
    found = tracker.migration_find_issue(suite.project, ref)
    assert found is not None and (found.id, found.title, found.body) == (first.id, title, body)
    assert [item.id for item in tracker.search(suite.project, title)] == [first.id]
    # a divergent replay fails closed and writes nothing
    with pytest.raises(TrackerConflictError):
        tracker.migration_import_issue(
            suite.project, _issue_snapshot(ref, title, body + "- [ ] other\n"), source_ref=ref,
        )
    assert [item.id for item in tracker.search(suite.project, title)] == [first.id]


@need("DHN-35")
def test_dhn_35_imported_issues_are_linked_through_the_staged_port(suite):
    # The import itself is DHN-34 (server source_ref, DEVHUB-105); the two issues are
    # created normally, as decided for PAT-314.
    tracker = suite.tracker
    source = _create(suite, _unique("live link source"))
    target = _create(suite, _unique("live link target"))
    tracker.migration_link_issue(suite.project, source.id, "depends-on", target.id)
    linked = tracker.get_issue(source.id)
    assert _has_link(linked, "depends-on", target.id)
    # Replay, and the inverse end the migration also links: no second relation.
    tracker.migration_link_issue(suite.project, source.id, "depends-on", target.id)
    tracker.migration_link_issue(suite.project, target.id, "blocks", source.id)
    assert tracker.get_issue(source.id).links == linked.links
    assert _has_link(tracker.get_issue(target.id), "blocks", source.id)


def _adr_snapshot(number: int, status: str, body: str, superseded_by=None) -> dict:
    adr_id = f"FOUNDRY-ADR-{number:04d}"
    return {
        "kind": "adr", "source_ref": f"linear:adr:{adr_id}", "id": adr_id,
        "title": f"{adr_id} - single tracker", "body": body, "status": status,
        "relations": {"supersedes": "unknown", "superseded_by": superseded_by, "issues": []},
        "source_created": 1_700_000_000_000, "source_updated": 1_700_000_000_001,
    }


@need("DHN-36")
def test_dhn_36_adr_is_imported_with_its_foreign_id_status_and_exact_body(suite):
    tracker = suite.tracker
    old_number = secrets.randbelow(4000) + 1000
    body = "## Decision\n\nOne tracker per repository.\n"
    replacement = _adr_snapshot(old_number + 5000, "accepted", body)
    old = _adr_snapshot(
        old_number, "superseded", body + "Superseded.\n", replacement["id"],
    )
    # replacements first: the corpus lists the replaced ADR first on purpose
    imported = tracker.migration_import_adrs(suite.project, (old, replacement))
    assert [item.id for item in imported] == [old["id"], replacement["id"]]
    listed = {item.id: item for item in tracker.list_adrs(suite.project)}
    for snapshot in (old, replacement):
        adr = listed[snapshot["id"]]
        assert (adr.status, adr.body) == (snapshot["status"], snapshot["body"])
        by_ref = tracker.migration_find_adr(suite.project, snapshot["source_ref"])
        by_alias = tracker.migration_find_adr(suite.project, foreign_id=snapshot["id"])
        assert by_ref is not None and by_alias is not None
        assert (by_ref.id, by_ref.body) == (snapshot["id"], snapshot["body"])
        assert by_alias.id == by_ref.id and by_alias.ref == by_ref.ref
    assert tracker.migration_find_adr(suite.project, "linear:adr:absent") is None
    # a faithful replay converges on the same ADR; a divergent one fails closed
    again = tracker.migration_import_adr(
        suite.project, old, source_ref=old["source_ref"],
    )
    assert again.ref == listed[old["id"]].ref
    assert len([item for item in tracker.list_adrs(suite.project)
                if item.id == old["id"]]) == 1
    with pytest.raises(TrackerConflictError):
        tracker.migration_import_adr(
            suite.project, {**old, "title": "changed"}, source_ref=old["source_ref"],
        )
    # a body whose hash is wrong is refused before any write (the server also answers 422)
    with pytest.raises(TrackerConflictError):
        tracker.import_adr(
            suite.project, adr_id=f"FOUNDRY-ADR-{old_number + 1:04d}", title="bad hash",
            body=body, historical_status="accepted",
            source_ref="linear:adr:bad-hash", source_created=None, source_updated=None,
            expected_source_sha256="0" * 64,
        )


@need("DHN-43")
def test_dhn_43_a_manifest_is_captured_copied_and_verified_to_devhub(suite):
    # PAT-324: DevHub as the target of the provider-neutral cutover path, in its own project
    # (the copy writes issues and ADRs whose graph is compared as a whole).
    import copy as copy_module

    from foundry.migration import capture_manifest, copy_and_verify, verify_targets
    from foundry.models import Adr, Issue, Link
    from test_tracker_migration import FakeTransportTracker

    tracker = suite.tracker
    key = gate.new_project_key()
    repository = f"github.com/foundry-live/{key.lower()}"
    project = tracker.provision_project("Foundry live cutover target", key, repository)
    epic = Issue("PAT-1", "live cutover epic", state="ready", type="Epic", priority="P1",
                 estimate=8, body="- [ ] first\n",
                 links=[Link("parent-of", "outward", "PAT-2")])
    child = Issue("PAT-2", "live cutover child", state="in-progress", type="Task",
                  priority="P2", estimate=3, body="- [x] done\n- [ ] open\n",
                  links=[Link("subtask-of", "outward", "PAT-1")])
    adrs = [Adr(f"FOUNDRY-ADR-{number:04d}", f"ADR {number}", status, f"body {number}\n")
            for number, status in ((1, "superseded"), (2, "accepted"))]
    source = FakeTransportTracker("linear", issues=[epic, child], adrs=adrs, adr_relations={
        adrs[0].id: {"supersedes": [], "superseded_by": adrs[1].id, "issues": []},
        adrs[1].id: {"supersedes": [adrs[0].id], "superseded_by": None, "issues": ["PAT-2"]},
    })
    source_project = Project("T", "source")
    manifest = capture_manifest(source, source_project, tracker, project)
    assert manifest["target"]["migration_profile"]["kind"] == "devhub-source-ref-v1"
    # a state DevHub cannot create is an explicit exception, never an approximation
    assert {item["id"]: [entry["attribute"] for entry in item["exceptions"]]
            for item in manifest["issues"]} == {"PAT-1": [], "PAT-2": ["state"]}
    copy_and_verify(manifest, tracker, project, lambda: None)
    assert manifest["phase"] == "verified"
    verify_targets(manifest, tracker, project)
    imported = tracker.get_issue(manifest["issues"][1]["target_id"])
    assert imported.state == "backlog" and imported.body == child.body
    # the profile is deterministic, and a second copy converges on the same objects
    assert capture_manifest(source, source_project, tracker, project)["source_digest"] == (
        manifest["source_digest"])
    replay = copy_module.deepcopy(manifest)
    copy_and_verify(replay, tracker, project, lambda: None)
    assert [item["target_id"] for item in replay["issues"]] == [
        item["target_id"] for item in manifest["issues"]]


@need("DHN-44")
def test_dhn_44_the_preflight_refuses_a_value_the_published_workflow_disables(suite):
    # PAT-328: a service credential cannot publish a workflow (``publishWorkflow`` needs a
    # human actor), so the disposable instance's operator provides an executable.  It is
    # called as ``<executable> <PROJECT KEY>`` with the new ``settings`` JSON on stdin.
    from foundry.migration import capture_manifest
    from foundry.models import Issue
    from test_tracker_migration import FakeTransportTracker

    publisher = os.environ.get(gate.ENV_WORKFLOW_PUBLISH)
    if not publisher:
        pytest.skip(
            f"{gate.ENV_WORKFLOW_PUBLISH} not set: DHN-44 needs an executable that publishes "
            "a workflow on the disposable instance (docs/devhub-requirements.md)")
    tracker = suite.tracker
    key = gate.new_project_key()
    project = tracker.provision_project(
        "Foundry live workflow target", key, f"github.com/foundry-live/{key.lower()}")
    path = f"/projects/{key}"
    workflow = {"response_contract": "devhub-workflow.v1", "api_prefix": "/api/tracker/workflow/v1"}
    unpublished = tracker._req("GET", path, **workflow)
    assert unpublished["version"] == 1  # never published: the import checks nothing

    def captured(**fields):
        issue = Issue("PAT-1", "live workflow issue", state="ready", body="- [ ] one\n", **fields)
        return capture_manifest(
            FakeTransportTracker("linear", issues=[issue]), Project("T", "source"), tracker, project)

    captured(type="Bug", priority="P3", estimate=8)  # accepted while unpublished
    settings = unpublished["settings"]
    for field, disabled in (("type", "Bug"), ("priority", "P3"), ("estimate", 8)):
        for entry in settings["fields"][field]:
            if entry["value"] == disabled:
                entry["enabled"] = False
    subprocess.run(
        [publisher, key], input=json.dumps(settings), text=True, check=True,
        capture_output=True, timeout=120)
    published = tracker._req("GET", path, **workflow)
    assert published["version"] > 1
    captured(type="Task", priority="P2", estimate=3)  # enabled values still pass
    for fields, shown in (
        ({"type": "Bug", "priority": "P2", "estimate": 3}, "type='Bug'"),
        ({"type": "Task", "priority": "P3", "estimate": 3}, "priority='P3'"),
        ({"type": "Task", "priority": "P2", "estimate": 8}, "estimate='8'"),
    ):
        with pytest.raises(TrackerConflictError, match="valeur désactivée") as refused:
            captured(**fields)
        assert shown in str(refused.value)
    assert tracker.search(project) == []  # every refusal came before any write


@need("DHN-45")
def test_dhn_45_the_preflight_refuses_a_proposed_adr_replacement(suite):
    # PAT-328: DevHub answers 409 invalid_transition to a replacement stored as ``proposed``
    # (``cd23304``); a replacement accepted, deprecated or superseded is taken.
    from foundry.migration import capture_manifest
    from foundry.models import Adr
    from test_tracker_migration import FakeTransportTracker

    tracker = suite.tracker
    key = gate.new_project_key()
    project = tracker.provision_project(
        "Foundry live replacement target", key, f"github.com/foundry-live/{key.lower()}")

    def captured(status):
        old, new = (Adr(f"FOUNDRY-ADR-{number:04d}", f"ADR {number}", state, f"body {number}\n")
                    for number, state in ((1, "superseded"), (2, status)))
        relations = {
            old.id: {"supersedes": [], "superseded_by": new.id, "issues": []},
            new.id: {"supersedes": [old.id], "superseded_by": None, "issues": []},
        }
        return capture_manifest(
            FakeTransportTracker("linear", adrs=[old, new], adr_relations=relations),
            Project("T", "source"), tracker, project)

    for status in ("accepted", "deprecated"):
        assert captured(status)["target"]["migration_profile"]["kind"] == "devhub-source-ref-v1"
    with pytest.raises(TrackerConflictError, match=r"proposed.*FOUNDRY-ADR-0002"):
        captured("proposed")
    assert tracker.list_adrs(project) == []  # refused before any write


@need("DHN-46")
def test_dhn_46_an_adr_corpus_with_a_source_only_reference_and_unknown_relations_is_copied(suite):
    # PAT-327 (DEVHUB-122): ``source_only_refs`` and ``unknown_relations`` of the ADR import
    # route.  The source holds an ADR with an issue left behind (a source-only reference) and
    # an ADR whose three relation families are unknown; both must read back exactly.
    import copy as copy_module

    from foundry.migration import capture_manifest, copy_and_verify, verify_targets
    from foundry.models import Adr, Issue
    from test_tracker_migration import FakeTransportTracker

    tracker = suite.tracker
    key = gate.new_project_key()
    repository = f"github.com/foundry-live/{key.lower()}"
    project = tracker.provision_project("Foundry live source relations target", key, repository)
    child = Issue("PAT-2", "live relations child", state="ready", type="Task", priority="P2",
                  estimate=3, body="- [ ] open\n")
    adrs = [Adr(f"FOUNDRY-ADR-{number:04d}", f"ADR {number}", "accepted", f"body {number}\n")
            for number in (1, 2)]
    source = FakeTransportTracker("linear", issues=[child], adrs=adrs, adr_relations={
        # PAT-9 is not in the living scope: it becomes a source-only reference
        adrs[0].id: {"supersedes": [], "superseded_by": None, "issues": ["PAT-2", "PAT-9"]},
        adrs[1].id: {"supersedes": "unknown", "superseded_by": "unknown", "issues": "unknown"},
    })
    manifest = capture_manifest(source, Project("T", "source"), tracker, project)
    by_id = {record["id"]: record for record in manifest["adrs"]}
    assert by_id[adrs[0].id]["source_only_issue_refs"] == ["linear:issue:PAT-9"]
    assert by_id[adrs[1].id]["relations"] == {
        "supersedes": "unknown", "superseded_by": "unknown", "issues": "unknown"}
    copy_and_verify(manifest, tracker, project, lambda: None)
    verify_targets(manifest, tracker, project)
    exported = {item["adr"].id: item for item in tracker.migration_export_adrs(project)}
    assert exported[adrs[0].id]["source_only_issue_refs"] == ["linear:issue:PAT-9"]
    assert exported[adrs[1].id]["relations"] == {
        "supersedes": "unknown", "superseded_by": "unknown", "issues": "unknown"}
    assert "source_only_issue_refs" not in exported[adrs[1].id]
    # the stored bytes, order included (``GET /adrs/:id``), and ``[]`` when none was sent
    stored = {}
    for adr in adrs:
        item = tracker._adr_lookup(project, foreign_id=adr.id)
        stored[adr.id] = tracker._application_req("GET", f"/adrs/{item['id']}")
    assert stored[adrs[0].id]["source_only_refs"] == ["linear:issue:PAT-9"]
    assert stored[adrs[0].id]["unknown_relations"] == []
    assert stored[adrs[1].id]["source_only_refs"] == []
    assert stored[adrs[1].id]["unknown_relations"] == ["issues", "supersedes", "superseded_by"]
    # a replay converges; the server refuses a family sent both known and unknown (400), the
    # adapter refuses it before the request, and neither writes
    before = len(tracker.list_adrs(project))
    copy_and_verify(copy_module.deepcopy(manifest), tracker, project, lambda: None)
    assert len(tracker.list_adrs(project)) == before
    body = "conflict\n"
    request = {
        "source_ref": "linear:adr:live-conflict", "foreign_id": "FOUNDRY-ADR-9999",
        "title": "conflict", "body": body,
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "status": "accepted", "source_only_refs": ["linear:issue:PAT-9"],
        "unknown_relations": ["issues"],
    }
    with pytest.raises(DevHubTrackerError) as refused, tracker._governance_guard(key):
        tracker._application_req(
            "POST", f"/projects/{key}/adrs/import", request, idempotent=True)
    assert refused.value.status == 400
    assert len(tracker.list_adrs(project)) == before
    with pytest.raises(ValueError):
        tracker.import_adr(
            project, adr_id="FOUNDRY-ADR-9999", title="conflict", body=body,
            historical_status="accepted", source_ref="linear:adr:live-conflict",
            source_created=None, source_updated=None,
            expected_source_sha256=request["body_sha256"],
            source_only_issue_refs=("linear:issue:PAT-9",), unknown_relations=("issues",))


# --- Epic cadencer (PAT-ADR-0026) -----------------------------------------------------

@need("DHN-37")
def test_dhn_37_an_issue_creation_can_be_reconciled_without_recreating_it(suite):
    tracker = suite.tracker
    assert "issue_creation_reconciliation" not in campaign_cadence.missing_capabilities(tracker)
    epic = _create(suite, _unique("live reconcile epic"), "", Type="Epic")
    title, body = _unique("live reconcile"), "body\n- [ ] one"
    issue = _create(suite, title, body, parent=epic.id)
    fields = {"Type": "Task", "Priority": "P3"}

    def count():
        return len(tracker.search(suite.project, title))

    before, version = count(), tracker.get_issue(issue.id).version
    result = tracker.reconcile_issue_creation(
        suite.project, issue.id, title, body, fields, epic.id,
    )
    assert result["validation"] == "accepted" and result["issue_id"] == issue.id
    assert result["automatic_retry"] is False
    # read-only: no second issue, no write on the issue (version unchanged)
    assert count() == before == 1
    assert tracker.get_issue(issue.id).version == version
    # a different creation is refused, an absent one is reported, never recreated
    for args in ((title + " x", body, fields, epic.id), (title, body + "x", fields, epic.id),
                 (title, body, fields, None), (title, body, {"Priority": "P0"}, epic.id)):
        with pytest.raises(TrackerConflictError, match="validation refused"):
            tracker.reconcile_issue_creation(suite.project, issue.id, *args)
    with pytest.raises(IssueUnavailableError):
        tracker.reconcile_issue_creation(
            suite.project, f"{suite.project.key}-99999", title, body, fields, None,
        )
    assert count() == before
    # basis of answer 9: replaying the same key and payload returns the stored issue
    path = f"/projects/{suite.project.key}/issues"
    request = {"title": _unique("live replay"), "body": "", "fields": {"type": "Task"},
               "parent": None}
    key = f"foundry-live-{secrets.token_hex(8)}"
    first = tracker._application_req(
        "POST", path, request, idempotent=True, idempotency_key=key,
    )
    again = tracker._application_req(
        "POST", path, request, idempotent=True, idempotency_key=key,
    )
    assert first["id"] == again["id"]
    assert len(tracker.search(suite.project, request["title"])) == 1


@need("DHN-38")
def test_dhn_38_lifecycle_is_append_only_for_the_cadencer(suite):
    tracker = suite.tracker
    assert tracker.append_only_lifecycle_supported is True
    assert "append_only_lifecycle_supported" not in campaign_cadence.missing_capabilities(
        tracker,
    )
    issue = _create(suite, _unique("live receipts"), "")
    assert tracker.read_proof_receipts(issue.id, project=suite.project) == ()
    # No `GitHub PR` field write here: the review proof alone must set the issue pr_url.
    _start(suite, issue.id)
    context = _review_context(suite, issue.id)
    assert tracker.get_issue(issue.id).pr_url in (None, "")
    tracker.set_state(issue.id, "review", context=context, project=suite.project)
    assert tracker.get_issue(issue.id).pr_url == context.pr_url
    coordinates = (context.pr_url, context.head_sha, context.base_sha, context.review_digest)

    def read():
        return tracker.read_proof_receipts(issue.id, project=suite.project)

    (first,) = read()
    assert first["action"] == "review" and first["merge_sha"] is None
    assert (first["pr_url"], first["head_sha"], first["base_sha"], first["review_digest"]) == coordinates
    # the same generation appends nothing; a new head returns through in-progress
    tracker.set_state(issue.id, "review", context=context, project=suite.project)
    assert read() == (first,)
    newer = TransitionContext(
        pr_url=context.pr_url, head_sha="e" * 40, base_sha=context.base_sha,
        review_digest=context.review_digest,
    )
    tracker.set_state(issue.id, "review", context=newer, project=suite.project)
    assert tracker.get_issue(issue.id).state == "review"
    assert [(item["action"], item["head_sha"]) for item in read()] == [
        ("review", context.head_sha), ("review", newer.head_sha)]
    # a new PR generation (new pr_url) updates the issue pr_url from the proof alone
    newest = TransitionContext(
        pr_url=context.pr_url.rsplit("/", 1)[0] + "/2", head_sha="f" * 40,
        base_sha=context.base_sha, review_digest=context.review_digest,
    )
    tracker.set_state(issue.id, "review", context=newest, project=suite.project)
    assert tracker.get_issue(issue.id).pr_url == newest.pr_url
    done = TransitionContext(
        pr_url=newest.pr_url, head_sha=newest.head_sha, base_sha=newest.base_sha,
        review_digest=newest.review_digest, merge_sha=_digests(issue.id)["merge"],
    )
    tracker.set_state(issue.id, "done", context=done, project=suite.project)
    receipts = read()
    assert [item["action"] for item in receipts] == ["review", "review", "review", "done"]
    assert [int(item["id"]) for item in receipts] == sorted(int(item["id"]) for item in receipts)
    assert receipts[-1]["merge_sha"] == done.merge_sha
    assert len({item["proof_nonce"] for item in receipts}) == 4
    assert tracker.recover_done_projection(
        issue.id, pr_url=done.pr_url, head_sha=done.head_sha, base_sha=done.base_sha,
        merge_sha=done.merge_sha, project=suite.project,
    ) is True
    assert tracker.recover_done_projection(
        issue.id, pr_url=done.pr_url, head_sha=done.head_sha, base_sha=done.base_sha,
        merge_sha="9" * 40, project=suite.project,
    ) is False


@need("DHN-39")
def test_dhn_39_a_mapped_release_scope_is_read_without_approximate_discovery(suite):
    tracker = suite.tracker
    body = "Goal\n\n- [ ] one\n"
    shipped = _create(suite, _unique("live release shipped"), body)
    tracker.sync_acceptance_body(
        shipped.id, body, body.replace("[ ]", "[x]"),
        hashlib.sha256(shipped.id.encode()).hexdigest(), project=suite.project,
    )
    _to_done(suite, shipped.id)
    pending = _create(suite, _unique("live release pending"), "")
    name = _unique("live-release").replace(" ", "-")
    created = tracker._application_req(
        "POST", f"/projects/{suite.project.key}/versions",
        {"name": name, "kind": "code", "scope": [
            {"issue_id": item, "issue_version": tracker.get_issue(item).version}
            for item in (shipped.id, pending.id)
        ]},
        idempotent=True,
    )
    version_id = created["id"]
    assert re.fullmatch(rf"{suite.project.key}-VERSION-[1-9][0-9]*", version_id)
    mapped = Project(
        key=suite.project.key, id=suite.project.id, extra={"release_ids": {name: version_id}},
    )
    scope = tracker.read_release_scope(mapped, name)
    assert scope.release == name and scope.release_id == version_id
    assert scope.project_key == suite.project.key
    assert {item.id: item.disposition for item in scope.issues} == {
        shipped.id: "accepted", pending.id: "unfinished"}
    # the name alone never finds a Version: only the stored id does
    with pytest.raises(ReleaseScopeUnavailableError) as unmapped:
        tracker.read_release_scope(suite.project, name)
    assert unmapped.value.reason == "unmapped"


# --- Foundry-side prerequisite --------------------------------------------------------

@need("DHN-40")
def test_dhn_40_devhub_is_a_v1_tracker_of_the_repository(suite):
    assert "devhub" in registry._V1_TRACKERS


@need("DHN-41")
def test_dhn_41_repository_bootstrap_reads_back_the_project_identity(suite):
    assert suite.tracker.verify_project_identity(suite.project) is True
    foreign = replace(suite.project, id=suite.project.id + "0")
    assert suite.tracker.verify_project_identity(foreign) is False


@need("DHN-42")
def test_dhn_42_service_credential_can_create_and_use_its_project_under_local_accounts(
    local_accounts_suite,
):
    suite = local_accounts_suite
    project = suite.tracker.provision_project(
        "Foundry live conformance", suite.project.key, suite.repository,
    )
    created = _create(
        Suite(suite.tracker, project, suite.repository, suite.config),
        _unique("live local accounts"),
    )
    assert suite.tracker.get_issue(created.id).id == created.id
