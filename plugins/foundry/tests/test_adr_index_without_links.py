"""PAT-190: `query issue` builds its embedded ADR index without validating the
ADR-to-issue links; every ADR command still does.  Fakes only, counted at the transport.
"""

from __future__ import annotations

import json

import pytest

from foundry import frame, query, write
from foundry.trackers import linear as linear_module
from foundry.trackers.base import (
    AdrIssueUnavailableError, AdrUnavailableError, TrackerConflictError,
)

from test_read_scope_cost import _OPERATION, _ops, _run, _world  # noqa: F401


def _with_link_validation(tracker, monkeypatch):
    """The index as `query issue` built it before PAT-190: through `list_adrs`."""
    monkeypatch.setattr(tracker, "list_adrs_without_issue_links", tracker.list_adrs)


def test_query_issue_issues_no_pair_validation_request(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    wire.calls.clear()
    _output, ops = _run(wire, query.issue, "LIN-2")
    # No reciprocal-comment read and no ADR issue-relation read: only the requested
    # issue (two references), the ADR document pages.
    assert "FoundryLinearCommentsById" not in ops
    assert dict(sorted(ops.items())) == {
        "FoundryLinearAdrDocuments": 1, "FoundryLinearIssue": 2,
    }
    assert sum(ops.values()) == 3
    # The same state with the validation costs more, and the extra requests are
    # exactly the pair validation (two ADRs cite LIN-2).
    _with_link_validation(tracker, monkeypatch)
    _output, validated = _run(wire, query.issue, "LIN-2")
    assert sum(validated.values()) == 6
    assert validated["FoundryLinearCommentsById"] == 2


@pytest.mark.parametrize(
    ("closed", "issue_id"), [(False, "LIN-2"), (False, "LIN-1"), (True, "LIN-1")],
)
def test_issue_output_is_identical_with_and_without_link_validation(
    monkeypatch, tmp_path, closed, issue_id,
):
    tracker, wire, _project = _world(monkeypatch, tmp_path, closed=closed)
    skipped, _ = _run(wire, query.issue, issue_id)
    _with_link_validation(tracker, monkeypatch)
    validated, _ = _run(wire, query.issue, issue_id)
    assert skipped == validated
    assert len(json.loads(skipped)["adrs"]) == 2


def _break_reciprocal_comment(wire, tracker, project, adr_id, cited):
    comment_id, _body = linear_module._adr_issue_link(
        tracker._binding(project), adr_id, cited
    )
    del wire.comments[comment_id]


def _break_cited_issue(wire, tracker, project, adr_id, cited):
    del wire.issues[cited]


@pytest.mark.parametrize(
    ("break_link", "error", "cited"),
    [
        (_break_reciprocal_comment, TrackerConflictError, "LIN-2"),
        (_break_cited_issue, AdrIssueUnavailableError, "LIN-3"),
    ],
)
def test_broken_link_gives_the_plain_index_in_issue_and_is_still_refused_by_adrs(
    monkeypatch, tmp_path, break_link, error, cited,
):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    monkeypatch.setattr(write, "mutation_project", lambda _t: project)
    created = tracker.create_adr(project, "Cites a dependency", "body")
    tracker.link_adr_issue(created, cited, project=project)
    wire.calls.clear()
    healthy, _ = _run(wire, query.issue, "LIN-2")
    break_link(wire, tracker, project, created.id, cited)

    broken, _ = _run(wire, query.issue, "LIN-2")
    # the embedded index is the plain list, unchanged by the broken link
    assert json.loads(broken)["adrs"] == json.loads(healthy)["adrs"]
    assert isinstance(json.loads(broken)["adrs"], list)
    assert created.id in {a["id"] for a in json.loads(broken)["adrs"]}

    for refused in (query.adrs, lambda: query.adr(created.id),
                    lambda: frame.materialize({"adrs": [], "issues": []})):
        with pytest.raises(error):
            refused()
    with pytest.raises(error):
        tracker.list_adrs(project)


def _rewrite_latest(tracker, wire, project, adr_id, change):
    binding = tracker._binding(project)
    raw = wire.documents[next(k for k, d in wire.documents.items()
                              if adr_id in str(d.get("title", "")) and "witness" not in k)]
    metadata, body = linear_module._parse_adr_document(raw, binding)
    change(metadata)
    raw["title"] = linear_module._adr_document_title(metadata)
    raw["content"] = linear_module._adr_document_content(metadata, body)
    witness_id = linear_module._adr_witness_id(project.id, adr_id, 0)
    wire.documents[witness_id] = linear_module._adr_witness_document(
        binding, metadata, raw
    )


def test_issue_still_reports_a_missing_supersession_target(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    created = tracker.create_adr(project, "Supersedes a ghost", "body")
    _rewrite_latest(
        tracker, wire, project, created.id,
        lambda metadata: metadata["relations"].update(supersedes=["LIN-ADR-0777"]),
    )
    out = query.issue("LIN-2")
    assert out["adrs"] == {
        "status": "adr_unavailable", "tracker": "linear",
        "reason": "ADR unavailable: LIN-ADR-0777",
    }
    with pytest.raises(AdrUnavailableError):
        query.adrs()


def test_issue_still_reports_a_broken_version_chain(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    created = tracker.create_adr(project, "Witness dropped", "body")
    del wire.documents[linear_module._adr_witness_id(project.id, created.id, 0)]
    out = query.issue("LIN-2")
    assert out["adrs"] == {
        "status": "conflict", "tracker": "linear",
        "reason": "Linear ADR version witness is missing",
    }
    with pytest.raises(TrackerConflictError):
        query.adrs()


def test_a_tracker_without_the_capability_lists_as_before(monkeypatch):
    class _Plain:
        def list_adrs(self, project):
            raise AdrIssueUnavailableError("T-ADR-0002", "T-42")

    with pytest.raises(AdrIssueUnavailableError):
        query._adr_index(_Plain(), None, issue_links=False)
