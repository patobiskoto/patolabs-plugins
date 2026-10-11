"""PAT-168: provider-read cost of the read-only query commands, and its limits.

Fakes only.  Counted at the GraphQL transport, by operation name.  The OUTPUT
assertions are characterisation: they were written before the read scope existed and
must not change with it; only the request counts do.
"""

from __future__ import annotations

import collections
import contextlib
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from foundry import query, write
from foundry.models import TransitionContext
from foundry.trackers.linear import LinearTrackerError

from test_epic_closure_transports import _linear_graph
from test_linear_tracker import STATE_IDS

_REVIEW = TransitionContext(
    pr_url="https://github.com/acme/widgets/pull/2", head_sha="a" * 40,
    base_sha="b" * 40, review_digest="c" * 64,
)
_OPERATION = re.compile(r"(?:query|mutation)\s+(\w+)")


def _ops(wire, start=0) -> collections.Counter:
    return collections.Counter(
        _OPERATION.search(document).group(1) for document, _v in wire.calls[start:]
    )


def _adrs(tracker, project, count=2):
    """``count`` ADRs that all cite LIN-2 (the same issue reference, resolved per ADR)."""
    for number in range(count):
        tracker.import_adr(
            project, adr_id=f"LIN-ADR-009{number}", title=f"Cites LIN-2 #{number}",
            body="old", historical_status="deprecated",
            source_ref=f"YT-ADR-9{number}", source_created=1, source_updated=2,
            expected_source_sha256=hashlib.sha256(b"old").hexdigest(),
            issue_refs=("LIN-2",), supersedes=(), superseded_by=None,
        )


_WORLDS = iter(range(10_000))


def _world(monkeypatch, tmp_path, *, closed: bool):
    # A fresh local audit-intent directory per world: the fixtures share one Epic id.
    home = tmp_path / f"world-{next(_WORLDS)}"
    monkeypatch.setattr(
        "foundry.trackers.epic_intent.registry.data_dir", lambda: str(home),
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(home))
    tracker, wire, project = _linear_graph(dependency=True)
    _adrs(tracker, project)
    if closed:
        tracker.set_state("LIN-1", "in-progress", project=project)
        wire.issues["LIN-1"]["updatedAt"] = "2026-09-20T10:01:30Z"
        monkeypatch.setattr(write, "mutation_project", lambda _t: project)
        write.close_epic(
            tracker, "LIN-1", human_verdict="accepted", issued_at=1_800_000_000_000,
            nonce="nonce_1234567890abcdef",
        )
    monkeypatch.setattr(query.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(query, "_project", lambda _t: project)
    return tracker, wire, project


def _run(wire, function, *args):
    start = len(wire.calls)
    value = function(*args)
    return json.dumps(value, sort_keys=True), _ops(wire, start)


# Request counts of the read-only commands.  Recorded BEFORE the change (PAT-168 lot 1)
# on this fixture (2 ADRs citing LIN-2, an Epic LIN-1 with child LIN-2 and dependency
# LIN-3), then updated by the read scope; each value is the sum over all operations.
# Before the change: issue-plain 9, issue-open-epic 9, issue-closed-epic 12, adrs 7,
# backlog 4, profile 4.  After the read scope and the listing seed (below).
EXPECTED_TOTALS = {
    "issue-plain": 6,
    "issue-open-epic": 6,
    "issue-closed-epic": 7,
    "adrs": 5,
    "backlog": 1,
    "profile": 1,
}


# Identical on origin/main (checked at PAT-168): the read scope never serves a write.
EXPECTED_LINK_ISSUE_OPS = {
    "FoundryLinearAdrDocumentById": 4,
    "FoundryLinearAdrDocumentCreate": 2,
    "FoundryLinearAdrDocuments": 3,
    "FoundryLinearCommentCreate": 1,
    "FoundryLinearCommentsById": 9,
    "FoundryLinearIssue": 16,
}


_COMMANDS = (
    ("issue-plain", False, query.issue, ("LIN-2",)),
    ("issue-open-epic", False, query.issue, ("LIN-1",)),
    ("issue-closed-epic", True, query.issue, ("LIN-1",)),
    ("adrs", False, query.adrs, ()),
    ("backlog", True, query.backlog, ()),
    ("profile", True, query.profile, ("next-issue",)),
)


def test_request_counts_per_read_command(monkeypatch, tmp_path):
    totals = {}
    for label, closed, function, args in _COMMANDS:
        _tracker, wire, _project = _world(monkeypatch, tmp_path, closed=closed)
        wire.calls.clear()
        _output, ops = _run(wire, function, *args)
        totals[label] = sum(ops.values())
    assert totals == EXPECTED_TOTALS


def test_outputs_are_identical_with_and_without_the_read_scope(monkeypatch, tmp_path):
    for label, closed, function, args in _COMMANDS:
        _tracker, wire, _project = _world(monkeypatch, tmp_path, closed=closed)
        scoped, scoped_ops = _run(wire, function, *args)
        with monkeypatch.context() as patch:
            patch.setattr(query, "_read_scope", lambda _tr: contextlib.nullcontext())
            plain, plain_ops = _run(wire, function, *args)
        assert scoped == plain, label
        assert sum(plain_ops.values()) >= sum(scoped_ops.values()), label


def test_list_read_shows_closed_epic_with_reopened_child_as_unknown(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=True)
    listed = {i.id: i for i in tracker.search(project)}
    assert (listed["LIN-1"].state, listed["LIN-1"].projection_status) == ("done", "aligned")

    # The child is reopened and modified after the Epic's closure.
    wire.issues["LIN-2"]["state"] = {"id": STATE_IDS["in-progress"], "name": "In progress"}
    wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T10:09:00Z"
    listed = {i.id: i for i in tracker.search(project)}
    assert (listed["LIN-1"].state, listed["LIN-1"].projection_status) == (None, "unknown")

    rows = {row["id"]: row for row in query.backlog()["issues"]}
    assert rows["LIN-1"]["state"] is None
    profile = query.profile("next-issue")
    assert any(row["id"] == "LIN-1" for row in profile["issues"])


def test_nothing_is_kept_between_two_commands(monkeypatch, tmp_path):
    tracker, wire, _project = _world(monkeypatch, tmp_path, closed=True)
    first, _ = _run(wire, query.backlog)
    start = len(wire.calls)
    second, _ = _run(wire, query.backlog)
    assert second == first
    assert getattr(tracker._read_scopes, "scope", None) is None
    assert _ops(wire, start)["FoundryLinearIssues"] == 1  # listed again, not remembered
    # A change made between two commands is seen by the second one.
    wire.issues["LIN-2"]["updatedAt"] = "2026-09-20T10:09:00Z"
    third, _ = _run(wire, query.backlog)
    assert json.loads(third)["issues"] != json.loads(first)["issues"]


def test_scope_is_dropped_when_the_command_fails(monkeypatch, tmp_path):
    tracker, wire, _project = _world(monkeypatch, tmp_path, closed=False)
    with pytest.raises(RuntimeError):
        with tracker.read_scope():
            raise RuntimeError("boom")
    assert getattr(tracker._read_scopes, "scope", None) is None


def test_failed_read_is_not_remembered(monkeypatch, tmp_path):
    tracker, wire, _project = _world(monkeypatch, tmp_path, closed=False)
    with tracker.read_scope():
        wire.calls.clear()
        for _ in range(2):
            with pytest.raises(Exception):
                tracker._read_raw("LIN-404")
        assert _ops(wire)["FoundryLinearIssue"] == 2
        assert tracker._read_raw("LIN-2")["identifier"] == "LIN-2"
        assert tracker._read_raw("LIN-2")["identifier"] == "LIN-2"
        assert _ops(wire)["FoundryLinearIssue"] == 3


def test_listing_seeds_the_scope_and_absent_issues_are_read_individually(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    with tracker.read_scope():
        wire.calls.clear()
        listed = tracker.search(project)
        assert _ops(wire) == {"FoundryLinearIssues": 1}
        assert tracker._read_raw("LIN-2")["identifier"] == "LIN-2"
        assert tracker._read_raw(wire.issues["LIN-2"]["id"])["identifier"] == "LIN-2"
        assert _ops(wire) == {"FoundryLinearIssues": 1}
        assert len(listed) == 3 and _ops(wire) == {"FoundryLinearIssues": 1}
        # A node the listing did not carry costs an individual read, as before.
        with pytest.raises(Exception):
            tracker._read_raw("LIN-404")
        assert _ops(wire)["FoundryLinearIssue"] == 1


def test_a_write_cannot_run_inside_a_read_scope(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    wire.calls.clear()
    with tracker.read_scope():
        tracker.search(project)
        with pytest.raises(RuntimeError, match="read-only command scope"):
            tracker.create_adr(project, "Written inside a scope", "body")
        with pytest.raises(RuntimeError, match="read-only command scope"):
            tracker.set_state("LIN-1", "review", context=_REVIEW, project=project)
    assert not any(_is_write(document) for document, _v in wire.calls)
    # Outside the scope the same write is sent again.
    tracker.create_adr(project, "Written outside a scope", "body")
    assert any(_is_write(document) for document, _v in wire.calls)


def _is_write(document):
    return document.lstrip().startswith("mutation")


def test_only_the_read_only_query_commands_open_a_read_scope():
    root = Path(__file__).parents[1] / "tooling" / "foundry"
    openers = sorted(
        str(path.relative_to(root)) for path in root.rglob("*.py")
        if "read_scope" in path.read_text(encoding="utf-8")
    )
    assert openers == ["query.py", "trackers/linear.py"]
    query_source = (root / "query.py").read_text(encoding="utf-8")
    for name in ("set_state", "close_epic", "merge", "update", "create_adr", "comment"):
        assert f"tr.{name}" not in query_source


def test_adr_write_keeps_its_exact_reads(monkeypatch, tmp_path):
    """An ADR write that runs ``_adr_snapshot`` reads exactly what it read before PAT-168."""
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    created = tracker.create_adr(project, "Linked later", "body")
    wire.calls.clear()
    tracker.link_adr_issue(created, "LIN-2", project=project)
    assert dict(sorted(_ops(wire).items())) == EXPECTED_LINK_ISSUE_OPS


class _PagedListing:
    """Transport wrapper serving the listing in pages of ``size`` (cursor = offset)."""

    def __init__(self, wire, size, *, replay_first_page=False):
        self.wire, self.size, self.replay = wire, size, replay_first_page
        self.calls = wire.calls

    def __getattr__(self, name):
        return getattr(self.wire, name)

    def __call__(self, document, variables):
        if "FoundryLinearIssues" not in document:
            return self.wire(document, variables)
        everything = self.wire(document, variables)["data"]["issues"]["nodes"]
        start = 0 if self.replay or not variables["after"] else int(variables["after"])
        end = start + self.size
        return {"data": {"issues": {
            "nodes": copy.deepcopy(everything[start:end]),
            "pageInfo": {"hasNextPage": self.replay or end < len(everything),
                         "endCursor": str(end if not self.replay else start + 1
                                          + len(self.wire.calls))},
        }}}


def _paged(monkeypatch, tmp_path, size, **kwargs):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=True)
    tracker._transport = _PagedListing(wire, size, **kwargs)
    return tracker, wire, project


def test_search_stops_at_the_second_request_when_a_page_is_re_served(monkeypatch, tmp_path):
    tracker, wire, project = _paged(monkeypatch, tmp_path, 2, replay_first_page=True)
    wire.calls.clear()
    with pytest.raises(LinearTrackerError) as caught:
        tracker.search(project)
    assert caught.value.code == "pagination_stalled"
    assert _ops(wire) == {"FoundryLinearIssues": 2}


def _serve_listing(tracker, pages, cursors):
    """Serve ``pages`` (lists of nodes) in call order, whatever ``after`` is."""
    served = iter(range(len(pages)))
    original = tracker._transport

    def transport(document, variables):
        result = original(document, variables)
        if "FoundryLinearIssues" in document:
            index = next(served)
            result = {"data": {"issues": {
                "nodes": copy.deepcopy(pages[index]),
                "pageInfo": {"hasNextPage": index + 1 < len(pages),
                             "endCursor": cursors[index]},
            }}}
        return result

    tracker._transport = transport


def _listing_nodes(wire, tracker, project):
    document = next(d for d, _v in wire.calls if "FoundryLinearIssues" in d)
    variables = next(v for d, v in wire.calls if "FoundryLinearIssues" in d)
    return wire(document, variables)["data"]["issues"]["nodes"]


def test_search_continues_on_a_repeated_cursor_with_different_nodes(monkeypatch, tmp_path):
    """As before PAT-168: only a repeated issue stalls a listing, not a repeated cursor."""
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=True)
    expected = [i.to_dict() for i in tracker.search(project)]
    nodes = _listing_nodes(wire, tracker, project)
    assert len(nodes) == 3
    _serve_listing(tracker, [[node] for node in nodes], ["same", "same", "same"])
    wire.calls.clear()
    assert [i.to_dict() for i in tracker.search(project)] == expected
    assert _ops(wire)["FoundryLinearIssues"] == 3


def test_one_page_duplicate_does_not_hide_an_earlier_normalization_error(
    monkeypatch, tmp_path,
):
    """One page [A, B, B], A invalid: the error of A is reported, as before PAT-168."""
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    tracker.search(project)
    first, second = _listing_nodes(wire, tracker, project)[:2]
    del first["title"]
    plain = tracker._transport
    _serve_listing(tracker, [[first, second, second]], [None])
    wire.calls.clear()
    with pytest.raises(LinearTrackerError) as caught:
        tracker.search(project)
    assert (caught.value.operation, caught.value.code) == ("normalize", "invalid_response")
    assert _ops(wire)["FoundryLinearIssues"] == 1
    # Without the invalid node the duplicate of the same page is still refused.
    tracker._transport = plain
    _serve_listing(tracker, [[second, second]], [None])
    with pytest.raises(LinearTrackerError) as caught:
        tracker.search(project)
    assert (caught.value.operation, caught.value.code) == (
        "issue.search", "pagination_stalled",
    )


def test_closed_epic_replay_is_served_from_a_later_listing_page(monkeypatch, tmp_path):
    tracker, wire, project = _paged(monkeypatch, tmp_path, 1)  # LIN-1 alone on page 1
    wire.calls.clear()
    with tracker.read_scope():
        listed = {i.id: i for i in tracker.search(project)}
    assert _ops(wire) == {"FoundryLinearIssues": 3}  # LIN-2 and LIN-3 never read alone
    assert (listed["LIN-1"].state, listed["LIN-1"].projection_status) == ("done", "aligned")


def test_a_second_search_in_the_same_scope_lists_again_and_reads_nothing(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=True)
    with tracker.read_scope():
        first = [i.to_dict() for i in tracker.search(project)]
        wire.calls.clear()
        second = [i.to_dict() for i in tracker.search(project)]
    assert second == first
    assert _ops(wire) == {"FoundryLinearIssues": 1}


def test_a_graph_snapshot_nested_in_a_read_scope_gets_its_own_copies(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    with tracker.read_scope():
        tracker.search(project)
        wire.calls.clear()
        with tracker.graph_snapshot():
            raw = tracker._read_raw("LIN-2")
            raw["title"] = "mutated by a caller"
            assert tracker._read_raw("LIN-2")["title"] == wire.issues["LIN-2"]["title"]
        assert tracker._read_raw("LIN-2")["title"] == wire.issues["LIN-2"]["title"]
    assert _ops(wire) == {}
    # Dropped on exit: the next snapshot outside the scope reads again.
    with tracker.graph_snapshot():
        tracker._read_raw("LIN-2")
        tracker._read_raw("LIN-2")
    assert _ops(wire) == {"FoundryLinearIssue": 1}
