"""PAT-168: provider-read cost of the read-only query commands, and its limits.

Fakes only.  Counted at the GraphQL transport, by operation name.  The OUTPUT
assertions are characterisation: they were written before the read scope existed and
must not change with it; only the request counts do.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path

import pytest

from foundry import query, write
from foundry.models import TransitionContext

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


OUTPUT_SHA256 = {
    "issue-plain": "ad2d8b5a8dc7b9e197e1804e8823a353bfb8e1ddb1c22a689b71a6e681bd1a8d",
    "issue-open-epic": "e0d3c66806ba43681d96f232dcbd0be8fa7a878b01bdbaefee2ff7791c5a15f9",
    "issue-closed-epic": "a2ab4bf01a5194a532632f46caaf520860fa47597d8999338e92f687d21825c0",
    "adrs": "6583df8de057b632d83c0f5067e817643ef411a93ec7802dfcbdc3450b7e0642",
    "backlog": "5d9a237da75aaeedca30d9fa5451ab86d2d7d7d3004aedb92d99856b538cb1de",
    "profile": "d3b183ccdd0bcf6c6a5acb2747e788f6087501f449ac1c871e8668caeccc13a7",
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


def _measure(monkeypatch, tmp_path):
    totals = {}
    outputs = {}
    for label, closed, function, args in (
        ("issue-plain", False, query.issue, ("LIN-2",)),
        ("issue-open-epic", False, query.issue, ("LIN-1",)),
        ("issue-closed-epic", True, query.issue, ("LIN-1",)),
        ("adrs", False, query.adrs, ()),
        ("backlog", True, query.backlog, ()),
        ("profile", True, query.profile, ("next-issue",)),
    ):
        _tracker, wire, _project = _world(monkeypatch, tmp_path, closed=closed)
        wire.calls.clear()
        outputs[label], ops = _run(wire, function, *args)
        totals[label] = (sum(ops.values()), dict(sorted(ops.items())))
    return totals, outputs


def test_request_counts_per_read_command(monkeypatch, tmp_path):
    totals, outputs = _measure(monkeypatch, tmp_path)
    assert {k: v[0] for k, v in totals.items()} == EXPECTED_TOTALS
    # Characterisation: the bytes of each output, recorded before the change.
    assert {k: hashlib.sha256(v.encode()).hexdigest() for k, v in outputs.items()} \
        == OUTPUT_SHA256


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
