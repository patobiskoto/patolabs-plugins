"""PAT-99: provider-read cost of an Epic read, a graph snapshot and a close-epic.

Counted at the GraphQL transport (one logical ``issue.read`` per wire call; the
fake never fails, so PAT-98 retries add nothing).  Fakes only.
"""

from __future__ import annotations

import collections
import contextlib
import copy

import pytest

from foundry import write
from foundry.models import Project, TransitionContext
from foundry.trackers.base import TrackerConflictError
from foundry.trackers.linear import LinearTracker

from test_epic_closure_transports import FaithfulLinearWire
from test_linear_tracker import PROJECT, STATE_IDS, connection, proof

N_CHILDREN = 31
N_PREREQ = 24
PARENT = "LIN-1"
_REVIEW = TransitionContext(
    pr_url="https://github.com/acme/widgets/pull/2", head_sha="a" * 40,
    base_sha="b" * 40, review_digest="c" * 64,
)


def _native(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _build(n_children=N_CHILDREN, n_prereq=N_PREREQ, *, open_children=()):
    """Epic LIN-1 with ``n_children`` done children, child i<n_prereq depending on
    a distinct done prerequisite (children and prerequisites are all delivered)."""
    wire = FaithfulLinearWire()
    project = Project(
        key=PROJECT.key, id=PROJECT.id,
        extra={**PROJECT.extra, "type_label_ids": {"Epic": "label-epic"}},
    )
    template = wire.issues.pop("LIN-2")
    parent = wire.issues[PARENT]
    parent.update({
        "description": "- [ ] Epic validation",
        "state": {"id": STATE_IDS["in-progress"], "name": "In progress"},
        "labels": connection([{"id": "label-epic", "name": "Epic"}]),
    })
    tracker = LinearTracker(token="test", transport=wire)
    tracker._activate(project)

    def node(identifier, native):
        raw = copy.deepcopy(template)
        raw.update({
            "id": native, "identifier": identifier,
            "description": "- [ ] acceptance",
            "state": {"id": STATE_IDS["in-progress"], "name": "In progress"},
            "labels": connection([]), "comments": connection([]),
            "relations": connection([]), "inverseRelations": connection([]),
            "updatedAt": "2026-09-20T10:01:00Z",
        })
        wire.issues[identifier] = raw
        if identifier in open_children:
            return
        tracker.set_state(identifier, "review", context=_REVIEW, project=project)
        tracker.project_acceptance_proof(
            identifier, "- [ ] acceptance", proof(identifier, "- [ ] acceptance"),
            checked=1, project=project,
        )
        tracker.set_state(
            identifier, "done",
            context=TransitionContext(**{**_REVIEW.__dict__, "merge_sha": "d" * 40}),
            project=project,
        )

    children = []
    for i in range(n_children):
        ident = f"LIN-{100 + i}"
        node(ident, _native(100 + i))
        children.append(ident)
    for i in range(n_prereq):
        ident = f"LIN-{200 + i}"
        node(ident, _native(200 + i))
        wire.issues[children[i]]["inverseRelations"] = connection([{
            "type": "blocks",
            "issue": {"id": _native(200 + i), "identifier": ident},
        }])
    parent["children"] = connection([
        {"id": _native(100 + i), "identifier": c} for i, c in enumerate(children)
    ])
    wire.calls.clear()
    return tracker, wire, project, children


def _reads(wire) -> collections.Counter:
    return collections.Counter(
        variables["id"] for document, variables in wire.calls
        if "FoundryLinearIssue(" in document
    )


def _close(tracker, monkeypatch, project):
    monkeypatch.setattr(write, "mutation_project", lambda _t: project)
    return write.close_epic(
        tracker, PARENT, human_verdict="accepted", issued_at=1_800_000_000_000,
        nonce="nonce_1234567890abcdef",
    )


@pytest.fixture(autouse=True)
def _isolated_epic_intents(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "foundry.trackers.epic_intent.registry.data_dir", lambda: str(tmp_path),
    )


def _total(wire) -> int:
    return sum(_reads(wire).values())


def _unscoped(tracker):
    """The pre-PAT-99 behaviour: no per-snapshot read reuse (baseline measurement)."""
    tracker.graph_snapshot = contextlib.nullcontext


def _record_scopes(tracker, wire, on_exit=None):
    """Wrap graph_snapshot: per scope, the reads it issued; optional exit hook."""
    original = tracker.graph_snapshot
    scopes: list[collections.Counter] = []

    @contextlib.contextmanager
    def wrapped():
        start = len(wire.calls)
        with original():
            yield
        reads = collections.Counter(
            v["id"] for d, v in wire.calls[start:] if "FoundryLinearIssue(" in d
        )
        scopes.append(reads)
        if on_exit is not None:
            on_exit(len(scopes))

    tracker.graph_snapshot = wrapped
    return scopes


def test_snapshot_reads_each_node_once_bounded_by_n_plus_2():
    tracker, wire, project, _children = _build()
    parent = tracker.get_issue(PARENT)
    wire.calls.clear()
    kids, deps = write.bounded_epic_graph_snapshot(tracker, project, parent)
    reads = _reads(wire)
    assert len(kids) == N_CHILDREN and len(deps) == N_PREREQ
    assert max(reads.values()) == 1
    nodes = N_CHILDREN + N_PREREQ
    assert sum(reads.values()) == nodes + 1  # N nodes + the parent, once
    assert sum(reads.values()) <= nodes + 2


def test_snapshot_baseline_without_scope_is_the_pre_fix_cost():
    tracker, wire, project, _children = _build()
    _unscoped(tracker)
    parent = tracker.get_issue(PARENT)
    wire.calls.clear()
    write.bounded_epic_graph_snapshot(tracker, project, parent)
    # Measured before PAT-99: 269 reads for 55 nodes, the parent alone read 80 times.
    assert _total(wire) > 4 * (N_CHILDREN + N_PREREQ)
    assert _reads(wire)[PARENT] > N_CHILDREN


def test_reading_a_closed_epic_costs_n_plus_2_reads(monkeypatch):
    tracker, wire, project, _children = _build()
    _close(tracker, monkeypatch, project)
    wire.calls.clear()
    tracker.get_issue(PARENT)
    nodes = N_CHILDREN + N_PREREQ
    assert _reads(wire)[PARENT] == 2  # the read itself + its one snapshot read
    assert max(c for k, c in _reads(wire).items() if k != PARENT) == 1
    assert _total(wire) <= nodes + 2


def test_closed_epic_read_baseline_without_scope(monkeypatch):
    tracker, wire, project, _children = _build()
    _unscoped(tracker)
    _close(tracker, monkeypatch, project)
    wire.calls.clear()
    tracker.get_issue(PARENT)
    assert _total(wire) > 4 * (N_CHILDREN + N_PREREQ)  # was 270


def test_diagnostic_reads_each_node_at_most_once():
    tracker, wire, project, _children = _build()
    parent = tracker.get_issue(PARENT)
    wire.calls.clear()
    assert write.epic_graph_diagnostic(tracker, project, parent) == []
    reads = _reads(wire)
    assert max(reads.values()) == 1 and PARENT not in reads
    assert sum(reads.values()) == N_CHILDREN + N_PREREQ


def test_refused_snapshot_and_its_diagnostic_share_one_read_per_node():
    tracker, wire, project, children = _build(open_children=("LIN-103",))
    parent = tracker.get_issue(PARENT)
    wire.calls.clear()
    with pytest.raises(SystemExit) as refused:
        write._snapshot_with_diagnostic(tracker, project, parent, frozenset())
    assert children[3] in str(refused.value)
    reads = _reads(wire)
    assert max(reads.values()) == 1
    assert sum(reads.values()) <= N_CHILDREN + N_PREREQ + 2


def test_unreadable_node_is_still_reported_and_never_waivable():
    tracker, wire, project, children = _build()
    parent = tracker.get_issue(PARENT)
    victim = children[5]
    del wire.issues[victim]
    report = write.epic_graph_diagnostic(
        tracker, project, parent, frozenset({victim}),
    )
    item = next(entry for entry in report if entry["id"] == victim)
    assert item["code"] == "read-error" and item["waivable"] is False
    assert item["incomplete"] is True and "NON parcouru" in item["cause"]


def test_close_epic_total_cost_and_every_snapshot_reads_afresh(monkeypatch):
    tracker, wire, project, _children = _build()
    scopes = _record_scopes(tracker, wire)
    _close(tracker, monkeypatch, project)
    nodes = N_CHILDREN + N_PREREQ
    # S0 (client preflight), S1 (pre-write), the closed parent's own replay
    # verification, and S3 (post-write verification): four independent snapshots.
    assert len(scopes) == 4
    for scope in scopes:
        assert max(scope.values()) == 1          # once per snapshot...
        assert len(scope) >= nodes               # ...but each performs its OWN reads
    total = _total(wire)
    # Measured: 1118 reads before PAT-99, 266 after (about 4.75 x (N + 1)).
    assert total <= 5 * (nodes + 1)


def test_a_node_changed_between_two_snapshots_is_refused_by_s1(monkeypatch):
    tracker, wire, project, children = _build()

    def reopen_after_first_snapshot(count):
        if count == 1:  # S0 passed; the node changes before the S1 re-snapshot
            wire.issues[children[7]]["updatedAt"] = "2026-09-21T09:00:00Z"

    scopes = _record_scopes(tracker, wire, reopen_after_first_snapshot)
    with pytest.raises(TrackerConflictError, match="avant écriture"):
        _close(tracker, monkeypatch, project)
    assert len(scopes) == 2 and children[7] in scopes[1]  # S1 read it afresh
    assert not any(
        "foundry-epic-closure" in body["body"] for body in wire.comments.values()
    )


def test_a_node_changed_after_s1_is_caught_by_the_post_write_verification(monkeypatch):
    tracker, wire, project, children = _build()

    def reopen_after_s1(count):
        if count == 2:
            wire.issues[children[9]]["state"] = {
                "id": STATE_IDS["in-progress"], "name": "In progress",
            }

    _record_scopes(tracker, wire, reopen_after_s1)
    with pytest.raises(TrackerConflictError, match="après écriture"):
        _close(tracker, monkeypatch, project)


def test_scope_is_dropped_on_exit_and_not_shared_between_snapshots():
    tracker, wire, project, children = _build()
    parent = tracker.get_issue(PARENT)
    write.bounded_epic_graph_snapshot(tracker, project, parent)
    assert not getattr(tracker._snapshot_scopes, "stack", [])
    wire.calls.clear()
    tracker.get_issue(children[0])
    tracker.get_issue(children[0])
    assert _reads(wire)[children[0]] == 2  # no reuse outside a snapshot scope
    wire.calls.clear()
    with tracker.graph_snapshot():
        tracker.get_issue(children[0])
        with tracker.graph_snapshot():  # a nested scope starts empty
            tracker.get_issue(children[0])
        tracker.get_issue(children[0])
    assert _reads(wire)[children[0]] == 2
