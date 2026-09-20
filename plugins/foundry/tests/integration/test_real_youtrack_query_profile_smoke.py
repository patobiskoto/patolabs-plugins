"""Read-only production smoke for normalized YouTrack list pagination.

It is deliberately separate from the historical create/update smoke.  Opt in only
with an explicit project key and the normal YouTrack secret configuration.
"""
from __future__ import annotations

import os
import urllib.parse

import pytest

import foundry
from foundry import query
from foundry.models import Project
from foundry.trackers.youtrack import YouTrackTracker


pytestmark = pytest.mark.integration


def test_real_youtrack_query_profile_read_only_smoke(monkeypatch):
    key = os.environ.get("FOUNDRY_QUERY_SMOKE_PROJECT_KEY")
    if not key or os.environ.get("FOUNDRY_QUERY_SMOKE") != "1":
        pytest.skip("set FOUNDRY_QUERY_SMOKE=1 and FOUNDRY_QUERY_SMOKE_PROJECT_KEY")
    tracker = YouTrackTracker()
    page_size = int(os.environ.get("FOUNDRY_QUERY_SMOKE_PAGE_SIZE", "10"))
    assert page_size > 0
    offsets = []
    observed_issues = []
    original_req = tracker._req
    original_search = tracker.search

    def observed_req(method, path, *args, **kwargs):
        # Delegate to the real authenticated request; this only records pagination
        # coordinates and never substitutes a host response.
        if method == "GET" and path.startswith("/issues?"):
            offsets.append(int(urllib.parse.parse_qs(path.partition("?")[2]).get("$skip", ["-1"])[0]))
        return original_req(method, path, *args, **kwargs)

    tracker._req = observed_req
    project = Project(key=key, id="read-only")

    def observed_search(actual_project, query_text=""):
        issues = original_search(actual_project, query_text, page_size=page_size)
        observed_issues.extend(issues)
        return issues

    monkeypatch.setattr(tracker, "search", observed_search)
    monkeypatch.setattr(tracker, "resolve_project", lambda _repo: project)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(query.registry, "repo_basename", lambda cwd=None: "read-only")
    result = query.profile("blockers")
    issues = observed_issues
    assert isinstance(issues, list)
    # These are normalized provider fields, including active and historical states
    # when the configured project contains them.  The test makes no tracker writes.
    assert all(issue.id and isinstance(issue.links, list) for issue in issues)
    states = {issue.state.casefold() for issue in issues if issue.state}
    assert states & {"ready", "backlog", "in-progress", "open"}
    assert states & {"done", "fixed", "dropped"}
    assert len(offsets) >= 2, "smoke project must exercise more than one real YouTrack page"
    assert offsets == sorted(set(offsets)) and offsets[0] == 0
    assert result["contract"] == query.PROFILE_CONTRACT_VERSION
    assert result["snapshot"]["identity"].startswith("sha256:")
    assert result["total_count"] == len(issues)
    assert "omitted_ids" not in result
    issue_section = result["sections"]["issues"]
    assert issue_section["returned_count"] == len(result["issues"])
    assert issue_section["snapshot_identity"] == result["snapshot"]["identity"]
    assert all({"created", "updated", "links", "ac_done", "ac_total"} <= issue.keys()
               for issue in result["issues"])
