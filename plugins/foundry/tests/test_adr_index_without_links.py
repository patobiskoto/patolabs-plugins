"""PAT-190: `query issue` builds its embedded ADR index without validating the
ADR-to-issue links; every ADR command still does.  Fakes only, counted at the transport.
"""

from __future__ import annotations

import collections
import json
import subprocess

import pytest

import foundry
from foundry import frame, query, write
from foundry.registry import RepositoryTrackerBinding
from foundry.trackers import linear as linear_module
from foundry.trackers.base import (
    AdrIssueUnavailableError, AdrUnavailableError, Tracker, TrackerConflictError,
)
from foundry.trackers.devhub import DevHubTracker
from foundry.trackers.ghprojects import GitHubProjectsTracker
from foundry.trackers.linear import LinearTracker, LinearTrackerError
from foundry.trackers.youtrack import YouTrackTracker

from test_ghprojects_tracker import (
    PROJECT as GH_PROJECT, _AdrCycleTransport, _rest_issue,
)
from test_read_scope_cost import _OPERATION, _run, _world


def _with_link_validation(tracker, monkeypatch):
    """The index as `query issue` built it before PAT-190: through `list_adrs`."""
    monkeypatch.setattr(tracker, "list_adrs_without_issue_links", tracker.list_adrs)


def test_query_issue_issues_no_pair_validation_request(monkeypatch, tmp_path):
    tracker, wire, project = _world(monkeypatch, tmp_path, closed=False)
    wire.calls.clear()
    _output, ops = _run(wire, query.issue, "LIN-2")
    # No reciprocal-comment read and no ADR issue-relation read: only the requested
    # issue LIN-2, its linked issue LIN-3 (a dependency) and one page of ADR documents.
    assert "FoundryLinearCommentsById" not in ops
    assert dict(sorted(ops.items())) == {
        "FoundryLinearAdrDocuments": 1, "FoundryLinearIssue": 2,
    }
    assert sum(ops.values()) == 3
    # The two issue reads are those two issues, by the variables of the calls.
    assert [
        variables for document, variables in wire.calls
        if _OPERATION.search(document).group(1) == "FoundryLinearIssue"
    ] == [{"id": "LIN-2"}, {"id": "LIN-3"}]
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
    assert json.loads(skipped) == json.loads(validated)
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


def test_issue_note_no_longer_announces_the_adr_issue_status(monkeypatch, tmp_path):
    _tracker, _wire, _project = _world(monkeypatch, tmp_path, closed=False)
    note = query.issue("LIN-2")["note"]
    assert "adr_issue_unavailable" not in note
    assert note.endswith(
        "ADR-to-issue links are not validated by this index; the ADR commands of a "
        "tracker that has such links do. Load the full text of constraining ADRs with "
        "`query adr <ADR-ID>` when available."
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


def test_cited_issue_transport_failure_fails_adrs_and_not_issue(monkeypatch, tmp_path):
    tracker, wire, _project = _world(monkeypatch, tmp_path, closed=False)
    cited = {"LIN-2", wire.issues["LIN-2"]["id"]}  # both ADRs of the fixture cite LIN-2

    def failing(document, variables):
        if (
            _OPERATION.search(document).group(1) == "FoundryLinearIssue"
            and variables["id"] in cited
        ):
            wire.calls.append((document, variables))
            raise OSError("connection reset")
        return wire(document, variables)

    tracker._transport = failing
    wire.calls.clear()
    with pytest.raises(LinearTrackerError) as caught:
        query.adrs()
    assert (caught.value.operation, caught.value.code) == ("issue.read", "transport_error")
    assert {"id": "LIN-2"} in [variables for _document, variables in wire.calls]

    # LIN-3 has no link: `query issue` reads it and one page of ADR documents only.
    wire.calls.clear()
    out = query.issue("LIN-3")
    assert [variables for _document, variables in wire.calls] == [
        {"id": "LIN-3"}, {"projectId": tracker._binding(_project)["project_id"],
                          "after": None},
    ]
    assert len(out["adrs"]) == 2 and isinstance(out["adrs"], list)


def test_port_default_is_list_adrs_and_link_validating_adapters_override_it():
    class _Plain:  # the default of the port, on a tracker that validates no link
        list_adrs_without_issue_links = Tracker.list_adrs_without_issue_links

        def list_adrs(self, project):
            raise AdrIssueUnavailableError("T-ADR-0002", "T-42")

    with pytest.raises(AdrIssueUnavailableError):
        query._adr_index(_Plain(), None, issue_links=False)
    overriding = {
        adapter for adapter in (
            LinearTracker, GitHubProjectsTracker, YouTrackTracker, DevHubTracker,
        ) if "list_adrs_without_issue_links" in vars(adapter)
    }
    assert overriding == {LinearTracker, GitHubProjectsTracker}


def test_a_link_validating_adapter_without_the_override_fails_query_issue_end_to_end(
    monkeypatch,
):
    """PAT-189 (PAT-190 remark 2): the claim of ``AdrIssueUnavailableError``.

    A tracker whose ``list_adrs`` validates the links and which keeps the port default
    gets no projection in ``query issue``: the error leaves the command.
    """
    class _Issue:
        id, links = "T-1", ()

        def to_dict(self):
            return {"id": "T-1"}

    class _NoOverride:
        name = "fake"
        list_adrs_without_issue_links = Tracker.list_adrs_without_issue_links

        def observe_issue(self, issue_id):
            return _Issue()

        def list_adrs(self, project):
            raise AdrIssueUnavailableError("T-ADR-0002", "T-42")

    tracker = _NoOverride()
    project = type("P", (), {"key": "T"})()
    monkeypatch.setattr(query.foundry, "tracker", lambda: tracker)
    monkeypatch.setattr(query, "_project", lambda _t: project)
    with pytest.raises(AdrIssueUnavailableError, match="T-ADR-0002 -> T-42"):
        query.issue("T-1")


# ---- GitHub Projects (fake gh transport of test_ghprojects_tracker) -----------------


class _Counting:
    """Records every gh command of a transport as (kind, method, path-or-operation)."""

    def __init__(self, provider):
        self.provider, self.commands = provider, []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        path = command[-1]
        if command[2] != "graphql":  # relations the ADR transport does not serve
            if "/dependencies/" in path or "/sub_issues" in path:
                return subprocess.CompletedProcess(command, 0, "[]", "")
            if "/parent" in path:
                return subprocess.CompletedProcess(
                    command, 1, json.dumps({"message": "No parent issue found", "status": "404"}),
                    "404",
                )
        return self.provider(command, **kwargs)

    def clear(self):
        self.commands.clear()


def _mk(path):
    path.mkdir()
    return path


def _gh_world(monkeypatch, tmp_path, *, adrs=2):
    binding = RepositoryTrackerBinding(
        tracker="ghprojects", repository=GH_PROJECT.extra["canonical_repo"],
        project=GH_PROJECT, registry_binding_digest="sha256:" + "1" * 64,
        migration_manifest_digest=None, configuration_digest="sha256:" + "2" * 64,
        activation_kind="bootstrap",
    )
    monkeypatch.setattr(
        "foundry.trackers.ghprojects.registry.repository_tracker_selection",
        lambda cwd=None: {"tracker": "ghprojects", "mode": "v1", "binding": binding},
    )
    provider = _AdrCycleTransport()
    runner = _Counting(provider)
    tracker = GitHubProjectsTracker(runner=runner, state_dir=tmp_path)
    created = []
    for number in range(adrs):
        adr = tracker.create_adr(GH_PROJECT, f"Decision {number}", "source")
        tracker.link_adr_issue(adr, "GHQUAL-1", GH_PROJECT)
        created.append(adr)
    # a second delivery issue, so that the cited GHQUAL-1 is not the requested one
    provider.issues[50] = {**_rest_issue(50), "title": "other", "node_id": "node-50"}
    provider.project_numbers.add(50)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(query, "_project", lambda _t: GH_PROJECT)
    runner.clear()
    return tracker, provider, runner, created


def _without_note(serialized):
    payload = json.loads(serialized)
    payload.pop("note")
    return payload


def _gh_kinds(runner):
    return collections.Counter(
        "graphql" if command[2] == "graphql" else command[-1].split("?")[0]
        for command in runner.commands
    )


def test_gh_query_issue_issues_no_link_validation_read(monkeypatch, tmp_path):
    tracker, provider, runner, created = _gh_world(monkeypatch, _mk(tmp_path / "after"))
    skipped = json.dumps(query.issue("GHQUAL-50"), sort_keys=True)
    after = _gh_kinds(runner)
    # PAT-189: "before" is measured on a SECOND, fresh instance (its own provider, runner
    # and state directory), so no local cache of the first call can lower it.
    tracker, provider, runner, created = _gh_world(monkeypatch, _mk(tmp_path / "before"))
    monkeypatch.setattr(tracker, "list_adrs_without_issue_links", tracker.list_adrs)
    validated = json.dumps(query.issue("GHQUAL-50"), sort_keys=True)
    before = _gh_kinds(runner)
    assert _without_note(skipped) == _without_note(validated)
    assert len(json.loads(skipped)["adrs"]) == 2
    # Before: 17 requests; after: 15.  The two skipped are the project listing and the
    # native read of the cited issue (once, for the two ADRs citing it).
    assert (sum(before.values()), sum(after.values())) == (17, 15)
    assert before - after == collections.Counter({
        "graphql": 1, f"repos/{provider.repo}/issues/1": 1,
    })
    assert not after - before


def test_gh_broken_link_gives_the_plain_index_and_adrs_still_refuses(
    monkeypatch, tmp_path,
):
    tracker, provider, runner, created = _gh_world(monkeypatch, tmp_path)
    healthy = query.issue("GHQUAL-50")["adrs"]
    provider.project_numbers.remove(1)
    plain = query.issue("GHQUAL-50")["adrs"]
    assert plain == healthy and isinstance(plain, list)
    with pytest.raises(AdrIssueUnavailableError):
        query.adrs()
    with pytest.raises(AdrIssueUnavailableError):
        tracker.list_adrs(GH_PROJECT)


@pytest.mark.parametrize(
    ("status", "message"), [("404", "Not Found"), ("403", "Resource not accessible")],
)
def test_gh_cited_issue_not_found_gives_the_plain_index(
    monkeypatch, tmp_path, status, message,
):
    tracker, provider, runner, created = _gh_world(monkeypatch, tmp_path)
    healthy = query.issue("GHQUAL-50")["adrs"]
    original = runner.provider
    issue_path = f"repos/{provider.repo}/issues/1"

    def failing(command, **kwargs):
        if command[2] != "graphql" and command[-1] == issue_path:
            return subprocess.CompletedProcess(
                command, 1, json.dumps({"message": message, "status": status}), status,
            )
        return original(command, **kwargs)

    runner.provider = failing
    assert query.issue("GHQUAL-50")["adrs"] == healthy
    with pytest.raises(AdrIssueUnavailableError):
        query.adrs()


def test_gh_history_check_stays_in_query_issue(monkeypatch, tmp_path):
    tracker, provider, runner, created = _gh_world(monkeypatch, tmp_path)
    provider.comments[int(created[0].ref)] = []
    out = query.issue("GHQUAL-50")
    assert out["adrs"] == {
        "status": "conflict", "tracker": "ghprojects",
        "reason": "ADR GitHub history missing",
    }
    with pytest.raises(TrackerConflictError, match="history missing"):
        query.adrs()
