"""Pure-logic tests — no network, no secrets (so CI runs them green anywhere).

These pin the invariants that matter: the CI merge gate verdict, the deterministic
ranking prior, the derived graph signals (including across a status filter), the
link-normalization contract of the YouTrack adapter, repo-url parsing, and AC parsing.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tooling"))

import foundry
from foundry import query, registry, write
from foundry.codehosts.github import GitHubCodeHost
from foundry.models import Adr, Check, Issue, Link, Project, PullRequest
from foundry.trackers.base import (
    AdrUnavailableError,
    IssueUnavailableError,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)
from foundry.trackers.youtrack import YouTrackTracker


class _FakeCodeHost:
    def __init__(self, checks, statuses=None, ci=False):
        self._checks = checks
        self._statuses = statuses or []
        self._ci = ci  # a check-suite exists (post-push window)

    def check_runs(self, repo, sha):
        return self._checks

    def commit_statuses(self, repo, sha):
        return self._statuses

    def ci_expected(self, repo, sha):
        return self._ci


class _FakeTracker:
    name = "fake"

    def __init__(self, issues, adrs=None):
        self._issues = issues
        self._adrs = adrs or []

    def resolve_project(self, repo):
        return Project(key="T", id="0-0")

    def search(self, project, query=""):
        return self._issues

    def get_issue(self, issue_id):
        # same contract as the real adapter: a typed error on an unreachable issue
        it = next((i for i in self._issues if i.id == issue_id), None)
        if it is None:
            raise IssueUnavailableError(issue_id)
        return it

    def list_adrs(self, project):
        return self._adrs


def test_ci_gate_all_green_passes():
    ch = _FakeCodeHost([Check("build", "completed", "success"),
                        Check("test", "completed", "success")])
    v = write.ci_gate(ch, "o/r", "sha")
    assert v["passed"] is True and v["total"] == 2


def test_ci_gate_failing_blocks():
    ch = _FakeCodeHost([Check("test", "completed", "failure")])
    v = write.ci_gate(ch, "o/r", "sha")
    assert v["passed"] is False and "test" in v["failing"]


def test_ci_gate_pending_blocks():
    ch = _FakeCodeHost([Check("build", "in_progress", None)])
    v = write.ci_gate(ch, "o/r", "sha")
    assert v["passed"] is False and "build" in v["pending"]


def test_ci_gate_zero_checks_blocks():
    # an empty check list proves nothing green (CI not started yet, or no CI at all):
    # green means PROVEN green, so this must refuse
    v = write.ci_gate(_FakeCodeHost([]), "o/r", "sha")
    assert v["passed"] is False and v["waived"] is False
    assert v["total"] == 0 and not v["failing"] and not v["pending"]


def test_ci_gate_waiver_passes_zero_checks_only():
    # the ONLY waiver: allow_no_ci on zero checks, marked waived for the audit trail
    v = write.ci_gate(_FakeCodeHost([]), "o/r", "sha", allow_no_ci=True)
    assert v["passed"] is True and v["waived"] is True
    # failing or pending checks are NEVER waivable
    red = write.ci_gate(_FakeCodeHost([Check("test", "completed", "failure")]),
                        "o/r", "sha", allow_no_ci=True)
    assert red["passed"] is False and red["waived"] is False
    pend = write.ci_gate(_FakeCodeHost([Check("b", "in_progress", None)]),
                         "o/r", "sha", allow_no_ci=True)
    assert pend["passed"] is False and pend["waived"] is False


def test_ci_gate_neutral_and_skipped_pass_alongside_a_success():
    ch = _FakeCodeHost([Check("build", "completed", "success"),
                        Check("lint", "completed", "neutral"),
                        Check("optional", "completed", "skipped")])
    assert write.ci_gate(ch, "o/r", "sha")["passed"] is True


def test_ci_gate_reads_legacy_commit_statuses():
    # a CI reporting only via the legacy status API (Jenkins…) must gate the merge:
    # red legacy status blocks even with ZERO check-runs
    red = _FakeCodeHost([], statuses=[Check("jenkins/build", "completed", "failure")])
    v = write.ci_gate(red, "o/r", "sha")
    assert v["passed"] is False and "jenkins/build" in v["failing"]
    # ... and is never waivable
    assert write.ci_gate(red, "o/r", "sha", allow_no_ci=True)["passed"] is False
    # a green legacy status alone IS proof
    green = _FakeCodeHost([], statuses=[Check("jenkins/build", "completed", "success")])
    assert write.ci_gate(green, "o/r", "sha")["passed"] is True
    # pending legacy status → wait
    pend = _FakeCodeHost([], statuses=[Check("jenkins/build", "in_progress", None)])
    assert write.ci_gate(pend, "o/r", "sha")["passed"] is False


def test_ci_gate_waiver_requires_both_sources_empty():
    v = write.ci_gate(_FakeCodeHost([], statuses=[]), "o/r", "sha", allow_no_ci=True)
    assert v["passed"] is True and v["waived"] is True
    v2 = write.ci_gate(
        _FakeCodeHost([], statuses=[Check("j", "completed", "failure")]),
        "o/r", "sha", allow_no_ci=True)
    assert v2["passed"] is False and v2["waived"] is False


def test_ci_gate_waiver_refused_in_post_push_window():
    # zero runs, zero statuses, but a check-suite exists: CI is queued, not absent —
    # --allow-no-ci must not slip through that window
    v = write.ci_gate(_FakeCodeHost([], ci=True), "o/r", "sha", allow_no_ci=True)
    assert v["passed"] is False and v["waived"] is False
    assert "check-suite" in v["reason"]


def test_ci_gate_all_skipped_is_not_proof():
    # path-filtered workflows: N check-runs, all skipped — nothing executed,
    # nothing proved green. Not waivable either (--allow-no-ci is zero-check only).
    ch = _FakeCodeHost([Check("a", "completed", "skipped"),
                        Check("b", "completed", "neutral")])
    assert write.ci_gate(ch, "o/r", "sha")["passed"] is False
    assert write.ci_gate(ch, "o/r", "sha", allow_no_ci=True)["passed"] is False


def test_validate_state_rejects_garbage():
    try:
        write.validate_state("nope")
        assert False, "should have raised"
    except SystemExit:
        pass


def test_rank_prior_orders_p0_before_p3():
    issues = [Issue(id="A", title="a", priority="P3", estimate=1, created=1),
              Issue(id="B", title="b", priority="P0", estimate=8, created=2)]
    ann = query._annotate(issues)
    by_id = {d["id"]: d for d in ann}
    assert by_id["B"]["rank_index"] < by_id["A"]["rank_index"]


def test_annotate_blocked_and_unlocks():
    issues = [
        Issue(id="A", title="a", state="ready",
              links=[Link(type="depends-on", direction="outward", target="B")]),
        Issue(id="B", title="b", state="in-progress",
              links=[Link(type="blocks", direction="inward", target="A")]),
    ]
    ann = {d["id"]: d for d in query._annotate(issues)}
    assert ann["A"]["blocked_by"] == ["B"] and ann["A"]["unblocked"] is False
    assert ann["B"]["unlocks"] == ["A"] and ann["B"]["unlocks_count"] == 1


def test_known_issue_with_unset_state_still_blocks():
    # a KNOWN issue blocks until resolved even when its State field is unset;
    # only targets outside the project (unknown ids) never block
    issues = [
        Issue(id="DEP", title="d", state=None),
        Issue(id="A", title="a", state="ready",
              links=[Link(type="depends-on", direction="outward", target="DEP")]),
        Issue(id="B", title="b", state="ready",
              links=[Link(type="depends-on", direction="outward", target="OTHER-1")]),
    ]
    ann = {d["id"]: d for d in query._annotate(issues)}
    assert ann["A"]["blocked_by"] == ["DEP"]
    assert ann["B"]["blocked_by"] == []


def test_youtrack_fixed_dependency_does_not_block_case_insensitive():
    issues = [
        Issue(id="DEP", title="d", state="fIxEd"),
        Issue(id="A", title="a", state="ready",
              links=[Link(type="depends-on", direction="outward", target="DEP")]),
    ]
    ann = {d["id"]: d for d in query._annotate(issues)}
    assert ann["A"]["blocked_by"] == [] and ann["A"]["unblocked"] is True


def test_subtask_of_epic_does_not_block_child():
    # a child is NOT blocked by its (open) epic — only real deps block
    issues = [
        Issue(id="EPIC", title="e", state="backlog"),
        Issue(id="CHILD", title="c", state="ready",
              links=[Link(type="subtask-of", direction="inward", target="EPIC")]),
    ]
    ann = {d["id"]: d for d in query._annotate(issues)}
    assert ann["CHILD"]["blocked_by"] == [] and ann["CHILD"]["unblocked"] is True


def test_status_filter_still_sees_blockers_outside_it(monkeypatch):
    # regression: a `ready` issue depending on an `in-progress` one must stay blocked
    # in the filtered view — blocked_by is computed over the FULL graph, filter after
    issues = [
        Issue(id="DEP", title="dep", state="in-progress"),
        Issue(id="A", title="a", state="ready",
              links=[Link(type="depends-on", direction="outward", target="DEP")]),
    ]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    out = query.backlog("ready")
    assert out["count"] == 1
    (a,) = out["issues"]
    assert a["id"] == "A" and a["blocked_by"] == ["DEP"] and a["unblocked"] is False
    # rank_in_view is dense over the returned view even when rank_index has gaps
    assert a["rank_in_view"] == 0


def test_rank_in_view_dense_and_ordered(monkeypatch):
    issues = [
        Issue(id="A", title="a", state="ready", priority="P2"),
        Issue(id="B", title="b", state="in-progress", priority="P0"),  # filtered out
        Issue(id="C", title="c", state="ready", priority="P1"),
    ]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    out = query.backlog("ready")
    by_id = {d["id"]: d for d in out["issues"]}
    # global ranks have a gap (B holds rank 0), the view ranks are dense 0..1
    assert sorted(d["rank_in_view"] for d in out["issues"]) == [0, 1]
    assert by_id["C"]["rank_in_view"] == 0 and by_id["A"]["rank_in_view"] == 1
    assert min(d["rank_index"] for d in out["issues"]) > 0


def test_lean_lists_full_detail_on_demand(monkeypatch):
    # summary first, detail on demand: list payloads carry no free text; the
    # requested issue / ADR keeps its body
    issues = [
        Issue(id="A", title="a", state="ready", body="long spec text",
              comments=[{"text": "note", "created": 1}],
              links=[Link(type="relates", direction="outward", target="B")]),
        Issue(id="B", title="b", state="ready", body="other long text"),
    ]
    adrs = [Adr(id="T-ADR-0001", title="T-ADR-0001 — décision", status="accepted",
                body="full adr text", ref="a-1")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues, adrs))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    for d in query.backlog()["issues"]:
        assert "body" not in d and "comments" not in d
        assert "ac_done" in d  # the AC signal survives the slimming

    out = query.issue("A")
    assert out["issue"]["body"] == "long spec text"          # target keeps its text
    assert out["issue"]["comments"]                          # ... and its notes
    assert "body" not in out["related"]["B"]                 # related stay lean
    assert all("body" not in a for a in out["adrs"])         # ADR index only

    assert all("body" not in a for a in query.adrs()["adrs"])
    assert query.adr("T-ADR-0001")["adr"]["body"] == "full adr text"
    # the natural near-miss `query adrs <ID>` delegates to the drill-down
    assert query.adrs("T-ADR-0001")["adr"]["body"] == "full adr text"
    try:
        query.adr("T-ADR-9999")
        assert False, "should have raised"
    except SystemExit as e:
        assert "T-ADR-0001" in str(e)  # the error lists known ids


def test_bounded_profile_is_provider_neutral_closed_and_explicit(monkeypatch):
    # Includes an active PR/WIP, a historical Fixed dependency, parent/child and an
    # inaccessible cross-project dependency.  No body may escape the list contract.
    issues = [
        Issue(id="EPIC", title="parent", state="backlog", body="private"),
        Issue(id="A", title="ready", state="ready", priority="P0", body="private",
              links=[Link(type="depends-on", direction="outward", target="DEP"),
                     Link(type="subtask-of", direction="inward", target="EPIC"),
                     Link(type="depends-on", direction="outward", target="FIXED"),
                     Link(type="depends-on", direction="outward", target="OTHER-9")]),
        Issue(id="DEP", title="wip", state="in-progress", pr_url="https://pr",
              created=1, updated=2,
              links=[Link(type="parent-of", direction="outward", target="CHILD")]),
        Issue(id="CHILD", title="child", state="backlog"),
        Issue(id="FIXED", title="old", state="Fixed"),
    ]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    out = query.profile("next-issue")
    assert out["contract"] == query.PROFILE_CONTRACT_VERSION
    assert out["snapshot"]["identity"].startswith("sha256:")
    assert out["total_count"] == 5
    assert out["returned_count"] == len(out["issues"])
    assert out["omitted_count"] == 5 - len(out["issues"])
    assert out["pagination"]["available"] is False
    assert all("body" not in d and "comments" not in d for d in out["issues"])
    ids = {d["id"] for d in out["issues"]}
    # Complete component closure keeps all known parents/children/dependencies.
    assert {"A", "DEP", "EPIC", "CHILD", "FIXED"} <= ids
    assert any(link["target"] == "OTHER-9" for d in out["issues"] for link in d["links"])
    dep = next(d for d in out["issues"] if d["id"] == "DEP")
    assert dep["created"] == 1 and dep["updated"] == 2
    assert "version" not in dep


def test_bounded_profile_paginates_complete_components_without_losing_edges(monkeypatch):
    issues = [Issue(id=f"I{n}", title="x", state="ready", priority="P1",
                    links=[Link(type="relates", direction="outward", target=f"I{n + 1}")]
                    if n % 2 == 0 else []) for n in range(40)]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    first = query.profile("next-issue")
    assert first["pagination"]["available"] is True
    pages = [first]
    while pages[-1]["pagination"]["next_page"]:
        pages.append(query.profile("next-issue", pages[-1]["pagination"]["next_page"]))
    assert {d["id"] for page in pages for d in page["issues"]} == {i.id for i in issues}
    for out in pages:
        ids = {d["id"] for d in out["issues"]}
        assert all(link["target"] not in {i.id for i in issues} or link["target"] in ids
                   for d in out["issues"] for link in d["links"])


def test_issue_tolerates_unreachable_related(monkeypatch):
    # a link to a deleted / cross-project issue must not crash query issue —
    # backlog tolerates unknown targets, the drill-down must too. Duplicate links
    # to the same target collapse into one entry.
    issues = [
        Issue(id="A", title="a", state="ready", body="text",
              links=[Link(type="relates", direction="outward", target="GONE-1"),
                     Link(type="depends-on", direction="outward", target="B"),
                     Link(type="relates", direction="outward", target="B")]),
        Issue(id="B", title="b", state="ready", body="b text"),
    ]
    class _RecordingTracker(_FakeTracker):
        def __init__(self, issues):
            super().__init__(issues)
            self.calls = []

        def get_issue(self, issue_id):
            self.calls.append(issue_id)
            return super().get_issue(issue_id)

    tracker = _RecordingTracker(issues)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    out = query.issue("A")
    assert out["related"]["GONE-1"] == {"id": "GONE-1", "error": "issue unavailable"}
    assert out["related"]["B"]["title"] == "b" and "body" not in out["related"]["B"]
    assert len(out["related"]) == 2
    assert tracker.calls.count("B") == 1


def test_issue_propagates_unexpected_related_error(monkeypatch):
    class _BrokenRelatedTracker(_FakeTracker):
        def get_issue(self, issue_id):
            if issue_id == "B":
                raise RuntimeError("provider outage with sensitive detail")
            return super().get_issue(issue_id)

    issues = [
        Issue(id="A", title="a", links=[Link(type="relates", direction="outward", target="B")]),
        Issue(id="B", title="b"),
    ]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _BrokenRelatedTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    with pytest.raises(RuntimeError, match="provider outage"):
        query.issue("A")


def test_issue_propagates_unavailable_primary_error(monkeypatch):
    class _UnavailablePrimaryTracker(_FakeTracker):
        def get_issue(self, issue_id):
            raise IssueUnavailableError(issue_id)

    monkeypatch.setattr(foundry, "tracker", lambda name=None: _UnavailablePrimaryTracker([]))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    with pytest.raises(IssueUnavailableError):
        query.issue("A")


def test_issue_projects_embedded_adr_index_conflict_as_distinct_status(monkeypatch):
    # A conflict in the embedded ADR index (e.g. an ADR version without a matching
    # witness) must not sink the whole issue read, must not surface as an empty
    # list, and must not be conflated with a capability-unavailable tracker.
    class _ConflictedAdrTracker(_FakeTracker):
        def list_adrs(self, project):
            raise TrackerConflictError("Linear ADR version witness diverged")

    issues = [Issue(id="A", title="a", state="ready", body="text")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _ConflictedAdrTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    out = query.issue("A")
    assert out["issue"]["id"] == "A"
    assert out["adrs"] == {
        "status": "conflict",
        "tracker": "fake",
        "reason": "Linear ADR version witness diverged",
    }


def test_issue_genuine_empty_adr_index_stays_an_empty_list(monkeypatch):
    issues = [Issue(id="A", title="a", state="ready", body="text")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues, []))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    out = query.issue("A")
    assert out["adrs"] == []


def test_issue_projects_adr_capability_unavailable_distinct_from_conflict(monkeypatch):
    class _NoAdrCapabilityTracker(_FakeTracker):
        def list_adrs(self, project):
            raise TrackerCapabilityUnavailableError("fake", "adr_index")

    issues = [Issue(id="A", title="a", state="ready", body="text")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _NoAdrCapabilityTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    out = query.issue("A")
    assert out["adrs"] == {
        "status": "unavailable",
        "tracker": "fake",
        "capability": "adr_index",
    }


def test_issue_projects_embedded_adr_index_unavailable_adr_as_distinct_status(monkeypatch):
    # A missing supersession target in the embedded ADR index (AdrUnavailableError)
    # must not sink the whole issue read, must not surface as an empty list, and must
    # not be conflated with either the conflict or the capability-unavailable status.
    class _UnavailableAdrTracker(_FakeTracker):
        def list_adrs(self, project):
            raise AdrUnavailableError("T-ADR-0002")

    issues = [Issue(id="A", title="a", state="ready", body="text")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _UnavailableAdrTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    out = query.issue("A")
    assert out["issue"]["id"] == "A"
    assert out["adrs"] == {
        "status": "adr_unavailable",
        "tracker": "fake",
        "reason": "ADR unavailable: T-ADR-0002",
    }


def test_issue_propagates_unexpected_adr_index_error(monkeypatch):
    # Transport, binding and payload failures must still propagate — only the typed
    # capability-unavailable and conflict errors get projected into the payload.
    class _BrokenAdrTracker(_FakeTracker):
        def list_adrs(self, project):
            raise RuntimeError("provider outage with sensitive detail")

    issues = [Issue(id="A", title="a", state="ready", body="text")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _BrokenAdrTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    with pytest.raises(RuntimeError, match="provider outage"):
        query.issue("A")


def test_query_adrs_still_fails_closed_on_embedded_index_conflict(monkeypatch):
    # AC2: `query adr`, `query adrs`, `frame` and ADR writes remain fail-closed on the
    # same conflict — only the `issue` payload gets a projected status.
    class _ConflictedAdrTracker(_FakeTracker):
        def list_adrs(self, project):
            raise TrackerConflictError("Linear ADR version witness diverged")

    monkeypatch.setattr(foundry, "tracker", lambda name=None: _ConflictedAdrTracker([]))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    with pytest.raises(TrackerConflictError):
        query.adrs()
    with pytest.raises(TrackerConflictError):
        query.adr("T-ADR-0001")


def test_query_adrs_still_fails_closed_on_unavailable_adr(monkeypatch):
    # AC1: `query adr`, `query adrs` remain fail-closed on a missing supersession
    # target too — only the `issue` payload gets a projected status.
    class _UnavailableAdrTracker(_FakeTracker):
        def list_adrs(self, project):
            raise AdrUnavailableError("T-ADR-0002")

    monkeypatch.setattr(foundry, "tracker", lambda name=None: _UnavailableAdrTracker([]))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")

    with pytest.raises(AdrUnavailableError):
        query.adrs()
    with pytest.raises(AdrUnavailableError):
        query.adr("T-ADR-0001")


def test_changelog_groups_shipped_issues(monkeypatch):
    issues = [
        Issue(id="A", title="feat: dark mode", state="done", milestone="v1.2",
              type="Feature", labels=["UX"]),
        Issue(id="B", title="fix: crash au démarrage", state="done", milestone="v1.2",
              type="Bug"),
        Issue(id="C", title="feat: en cours", state="in-progress", milestone="v1.2",
              type="Feature"),                                   # not done → excluded
        Issue(id="D", title="feat: abandonné", state="dropped", milestone="v1.2",
              type="Feature"),                                   # dropped never shipped
        Issue(id="E", title="feat: autre version", state="done", milestone="v1.1",
              type="Feature"),                                   # other milestone
        Issue(id="F", title="fix: état YouTrack", state="Fixed", milestone="v1.2",
              type="Bug"),                                       # terminal, but not shipped
    ]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    out = query.changelog("v1.2")
    assert out["count"] == 2
    assert set(out["groups"]) == {"Feature", "Bug"}
    assert [i["id"] for i in out["groups"]["Feature"]] == ["A"]
    assert out["groups"]["Feature"][0]["labels"] == ["UX"]
    assert [i["id"] for i in out["groups"]["Bug"]] == ["B"]
    # facts only — no free text / no locale-specific rewriting leaked in
    assert "body" not in out["groups"]["Feature"][0]


def test_changelog_unknown_milestone_is_empty(monkeypatch):
    issues = [Issue(id="A", title="x", state="done", milestone="v1.2", type="Feature")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    out = query.changelog("v9.9")
    assert out["count"] == 0 and out["groups"] == {}


def test_changelog_empty_milestone_refuses(monkeypatch):
    # None/"" must NOT silently collect the unset-milestone done issues
    issues = [Issue(id="A", title="x", state="done", milestone=None, type="Feature")]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    for bad in (None, ""):
        try:
            query.changelog(bad)
            assert False, "should have raised"
        except SystemExit:
            pass


def test_changelog_typeless_issue_grouped(monkeypatch):
    issues = [Issue(id="A", title="x", state="done", milestone="v1.2", type=None)]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    out = query.changelog("v1.2")
    assert out["count"] == 1 and list(out["groups"]) == ["(sans type)"]


def test_milestones_populate_blocked_ids(monkeypatch):
    issues = [
        Issue(id="DEP", title="dep", state="in-progress", milestone="v1", estimate=3),
        Issue(id="A", title="a", state="ready", milestone="v1", estimate=2,
              links=[Link(type="depends-on", direction="outward", target="DEP")]),
    ]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    v1 = query.milestones()["milestones"]["v1"]
    assert v1["blocked_ids"] == ["A"] and v1["wip_ids"] == ["DEP"]


def test_milestones_roll_up_terminal_states_case_insensitive(monkeypatch):
    issues = [
        Issue(id="A", title="done", state="DONE", milestone="v1", estimate=1),
        Issue(id="B", title="dropped", state="DrOpPeD", milestone="v1", estimate=2),
        Issue(id="C", title="fixed", state="Fixed", milestone="v1", estimate=3),
        Issue(id="D", title="open", state="ready", milestone="v1", estimate=5),
    ]
    monkeypatch.setattr(foundry, "tracker", lambda name=None: _FakeTracker(issues))
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "x")
    v1 = query.milestones()["milestones"]["v1"]
    assert v1["done"] == 3 and v1["open"] == 1 and v1["pct"] == 75
    assert v1["est_done"] == 6 and v1["est_open"] == 5
    assert v1["open_ids"] == ["D"]


def test_youtrack_links_emit_normalized_types():
    # the Tracker contract: Link.type is the normalized form, never a provider phrase
    raw = {"links": [
        {"direction": "OUTWARD",
         "linkType": {"name": "Depend", "sourceToTarget": "is required for",
                      "targetToSource": "depends on"},
         "issues": [{"idReadable": "X-1"}]},
        {"direction": "INWARD",
         "linkType": {"name": "Depend", "sourceToTarget": "is required for",
                      "targetToSource": "depends on"},
         "issues": [{"idReadable": "X-2"}]},
        {"direction": "INWARD",
         "linkType": {"name": "Subtask", "sourceToTarget": "parent for",
                      "targetToSource": "subtask of"},
         "issues": [{"idReadable": "X-3"}]},
    ]}
    types = {lk.target: lk.type for lk in YouTrackTracker._links(raw)}
    assert types == {"X-1": "blocks", "X-2": "depends-on", "X-3": "subtask-of"}


def test_suite_signals_ci_grace_window():
    import datetime
    f = GitHubCodeHost._suite_signals_ci
    now = datetime.datetime(2026, 7, 2, 12, 0, tzinfo=datetime.timezone.utc)
    fresh = (now - datetime.timedelta(minutes=2)).isoformat()
    stale = (now - datetime.timedelta(days=3)).isoformat()
    # active CI signals → refuse the waiver
    assert f({"status": "in_progress", "latest_check_runs_count": 0}, now) is True
    assert f({"status": "completed", "latest_check_runs_count": 3}, now) is True
    assert f({"status": "queued", "latest_check_runs_count": 0,
              "created_at": fresh}, now) is True          # post-push window
    # no CI ran, none coming → waiver possible
    assert f({"status": "completed", "latest_check_runs_count": 0}, now) is False
    assert f({"status": "queued", "latest_check_runs_count": 0,
              "created_at": stale}, now) is False         # schedule-only steady state
    # undatable queued suite: err on the refusing side
    assert f({"status": "queued", "latest_check_runs_count": 0}, now) is True


def test_ci_expected_paginates_until_active_suite_on_last_page(monkeypatch):
    host = GitHubCodeHost()
    calls = []
    inert = {"status": "completed", "latest_check_runs_count": 0,
             "created_at": "2026-01-01T00:00:00Z"}

    def fake_gh(*args, **_kwargs):
        path = args[1]
        calls.append(path)
        if path.endswith("&page=1"):
            return json.dumps({"total": 101, "batch": [
                dict(inert, id=i) for i in range(1, 101)
            ]})
        if path.endswith("&page=2"):
            return json.dumps({"total": 101, "batch": [{
                "id": 101, "status": "in_progress",
                "latest_check_runs_count": 0, "created_at": None,
            }]})
        raise AssertionError(path)

    monkeypatch.setattr(host, "_gh", fake_gh)
    assert host.ci_expected("owner/repo", "sha") is True
    assert len(calls) == 2
    assert calls[0].endswith("&page=1") and calls[1].endswith("&page=2")


def test_ci_expected_returns_false_after_all_inert_pages(monkeypatch):
    host = GitHubCodeHost()
    inert = {"status": "completed", "latest_check_runs_count": 0,
             "created_at": "2026-01-01T00:00:00Z"}

    def fake_gh(*args, **_kwargs):
        path = args[1]
        if path.endswith("&page=1"):
            return json.dumps({"total": 101, "batch": [
                dict(inert, id=i) for i in range(1, 101)
            ]})
        if path.endswith("&page=2"):
            return json.dumps({"total": 101, "batch": [dict(inert, id=101)]})
        raise AssertionError(path)

    monkeypatch.setattr(host, "_gh", fake_gh)
    assert host.ci_expected("owner/repo", "sha") is False


@pytest.mark.parametrize("second_page", [
    {"total": 101, "batch": [{"id": 1, "status": "completed",
                                 "latest_check_runs_count": 0}]},
    {"total": 101, "batch": []},
])
def test_ci_expected_rejects_incomplete_or_repeated_pages(monkeypatch, second_page):
    host = GitHubCodeHost()

    def fake_gh(*args, **_kwargs):
        path = args[1]
        if path.endswith("&page=1"):
            return json.dumps({"total": 101, "batch": [
                {"id": i, "status": "completed", "latest_check_runs_count": 0}
                for i in range(1, 101)
            ]})
        if path.endswith("&page=2"):
            return json.dumps(second_page)
        raise AssertionError(path)

    monkeypatch.setattr(host, "_gh", fake_gh)
    with pytest.raises(RuntimeError):
        host.ci_expected("owner/repo", "sha")


def test_ci_expected_propagates_second_page_provider_error(monkeypatch):
    host = GitHubCodeHost()

    def fake_gh(*args, **_kwargs):
        if args[1].endswith("&page=1"):
            return json.dumps({"total": 101, "batch": [
                {"id": i, "status": "completed", "latest_check_runs_count": 0}
                for i in range(1, 101)
            ]})
        raise RuntimeError("gh: API rate limit exceeded (HTTP 403)")

    monkeypatch.setattr(host, "_gh", fake_gh)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        host.ci_expected("owner/repo", "sha")


@pytest.mark.parametrize("response", ["not json", json.dumps({"total": 1})])
def test_ci_expected_rejects_invalid_page_response(monkeypatch, response):
    host = GitHubCodeHost()
    monkeypatch.setattr(host, "_gh", lambda *_args, **_kwargs: response)
    with pytest.raises(RuntimeError, match="Invalid check-suites response"):
        host.ci_expected("owner/repo", "sha")


@pytest.mark.parametrize("suite", [
    {"id": 1, "status": None, "latest_check_runs_count": 0},
    {"id": 1, "status": "unknown", "latest_check_runs_count": 0},
    {"id": 1, "status": "completed", "latest_check_runs_count": None},
    {"id": 1, "status": "completed"},
    {"id": 1, "status": "completed", "latest_check_runs_count": -1},
    {"id": 1, "status": "completed", "latest_check_runs_count": True},
    {"id": 1, "status": "queued", "latest_check_runs_count": 0,
     "created_at": 123},
])
def test_ci_expected_rejects_invalid_suite_records(monkeypatch, suite):
    host = GitHubCodeHost()
    response = json.dumps({"total": 1, "batch": [suite]})
    monkeypatch.setattr(host, "_gh", lambda *_args, **_kwargs: response)
    with pytest.raises(RuntimeError, match="Invalid check-suite record"):
        host.ci_expected("owner/repo", "sha")


def test_check_runs_falls_back_to_suites_and_keeps_latest_per_app(monkeypatch):
    host = GitHubCodeHost()
    calls = []

    def fake_gh(*args, **_kwargs):
        path = args[1]
        calls.append(path)
        if "/commits/sha/check-runs" in path:
            raise RuntimeError("gh: Not Found (HTTP 404)")
        if "/commits/sha/check-suites" in path:
            return json.dumps({"total": 3, "batch": [
                {"id": 10, "app": "github-actions"},
                {"id": 20, "app": "github-actions"},
                {"id": 30, "app": "other-ci"},
            ]})
        if "/check-suites/10/check-runs" in path:
            return json.dumps({"total": 1, "batch": [
                {"id": 100, "name": "test", "status": "completed",
                 "conclusion": "failure"},
            ]})
        if "/check-suites/20/check-runs" in path:
            return json.dumps({"total": 2, "batch": [
                {"id": 200, "name": "test", "status": "completed",
                 "conclusion": "success"},
                {"id": 201, "name": "lint", "status": "in_progress",
                 "conclusion": None},
            ]})
        if "/check-suites/30/check-runs" in path:
            return json.dumps({"total": 1, "batch": [
                {"id": 300, "name": "test", "status": "completed",
                 "conclusion": "success"},
            ]})
        raise AssertionError(path)

    monkeypatch.setattr(host, "_gh", fake_gh)
    checks = host.check_runs("owner/repo", "sha")
    by_name = [(check.name, check.status, check.conclusion) for check in checks]
    assert by_name == [
        ("test", "completed", "success"),
        ("lint", "in_progress", None),
        ("test", "completed", "success"),
    ]
    assert any("/commits/sha/check-suites" in call for call in calls)


def test_check_runs_does_not_mask_non_404_errors(monkeypatch):
    host = GitHubCodeHost()

    def fake_gh(*_args, **_kwargs):
        raise RuntimeError("gh: API rate limit exceeded (HTTP 403)")

    monkeypatch.setattr(host, "_gh", fake_gh)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        host.check_runs("owner/repo", "sha")


def test_parse_owner_repo_https_ssh_and_alias():
    f = GitHubCodeHost.parse_owner_repo
    assert f("https://github.com/patobiskoto/foundry.git") == "patobiskoto/foundry"
    assert f("git@github.com:patobiskoto/foundry.git") == "patobiskoto/foundry"
    assert f("ssh://my-alias/patobiskoto/foundry.git") == "patobiskoto/foundry"
    # scp-style ssh-config aliases: host doesn't contain github.com at all
    assert f("work:patobiskoto/foundry.git") == "patobiskoto/foundry"
    assert f("git@github.com-work:patobiskoto/foundry.git") == "patobiskoto/foundry"


def test_get_pr_preserves_head_and_merge_receipt_shas(monkeypatch):
    host = GitHubCodeHost()
    monkeypatch.setattr(
        host, "_gh",
        lambda *_args, **_kwargs: json.dumps({
            "number": 12,
            "html_url": "https://github.com/acme/demo/pull/12",
            "head": "feat/demo",
            "base": "main",
            "base_sha": "c" * 40,
            "sha": "a" * 40,
            "merged": True,
            "merge_sha": "b" * 40,
        }),
    )

    pull_request = host.get_pr("acme/demo", 12)

    assert pull_request.sha == "a" * 40
    assert pull_request.base_sha == "c" * 40
    assert pull_request.merge_sha == "b" * 40
    assert pull_request.merged is True


def test_list_prs_filters_exact_head_and_preserves_candidate_state(monkeypatch):
    host = GitHubCodeHost()
    calls = []
    monkeypatch.setattr(
        host, "_gh",
        lambda *args, **_kwargs: calls.append(args) or json.dumps([{
            "number": 12,
            "html_url": "https://github.com/acme/demo/pull/12",
            "head": "fix/demo-7",
            "base": "main",
            "base_sha": "c" * 40,
            "sha": "a" * 40,
            "state": "closed",
            "merged": False,
            "merge_sha": None,
        }]),
    )

    candidates = host.list_prs("acme/demo", "fix/demo-7")

    assert len(candidates) == 1
    assert candidates[0].state == "closed"
    assert candidates[0].base == "main"
    assert "head=acme%3Afix%2Fdemo-7" in calls[0][1]
    assert "page=1" in calls[0][1]


def test_update_pr_uses_patch_then_returns_fresh_coordinates(monkeypatch):
    host = GitHubCodeHost()
    calls = []
    fresh = PullRequest(
        number=12, url="https://github.com/acme/demo/pull/12",
        head="fix/demo-7", base="main", base_sha="c" * 40,
        sha="a" * 40,
    )

    monkeypatch.setattr(host, "_gh", lambda *args, **_kwargs: calls.append(args) or "{}")
    monkeypatch.setattr(host, "get_pr", lambda *_args: fresh)

    result = host.update_pr("acme/demo", 12, "new title", "new body")

    assert result is fresh
    assert calls[0][:4] == ("api", "-X", "PATCH", "repos/acme/demo/pulls/12")


def test_open_pr_preserves_exact_base_sha(monkeypatch):
    host = GitHubCodeHost()
    monkeypatch.setattr(
        host, "_gh",
        lambda *_args, **_kwargs: json.dumps({
            "number": 12,
            "html_url": "https://github.com/acme/demo/pull/12",
            "head": "a" * 40,
            "base_sha": "c" * 40,
        }),
    )

    pull_request = host.open_pr(
        "acme/demo", "feat/demo", "main", "title", "body",
    )

    assert pull_request.sha == "a" * 40
    assert pull_request.base_sha == "c" * 40


def test_adr_prefix_is_anchored_no_suffix_cross_match():
    # a project key that is a SUFFIX of another must not see its ADRs:
    # "TOC-ADR" must not match inside "MDTOC-ADR-0001 — …"
    class _FakeArticles(YouTrackTracker):
        def __init__(self, articles):  # bypass config/network
            self._articles = articles

        def _req(self, method, path, body=None, fields=None, top=None):
            assert method == "GET" and path.startswith("/admin/projects/")
            return self._articles

    articles = [
        {"idReadable": "A-1", "summary": "MDTOC-ADR-0001 — décision mdtoc",
         "content": "statut : `accepted`"},
        {"idReadable": "A-2", "summary": "TOC-ADR-0001 — décision toc",
         "content": "statut : `proposed`"},
    ]
    yt = _FakeArticles(articles)
    toc = [a.id for a in yt.list_adrs(Project(key="TOC", id="0-1"))]
    mdtoc = [a.id for a in yt.list_adrs(Project(key="MDTOC", id="0-2"))]
    assert toc == ["TOC-ADR-0001"]
    assert mdtoc == ["MDTOC-ADR-0001"]


def test_ac_counts_matches_routing_checkbox_grammar():
    body = (
        "## Critères\n"
        "- [x] dash done\n"
        "- [ ] dash todo\n"
        "  * [X] asterisk done\n"
        "  * [ ] asterisk todo\n"
        "    + [x] plus done\n"
        "    + [ ] plus todo\n"
    )
    done, total = YouTrackTracker._ac_counts(body)
    assert (done, total) == (3, 6)


@pytest.mark.parametrize("marker", ["-", "*", "+"])
@pytest.mark.parametrize("mark", ["x", " "])
@pytest.mark.parametrize(
    "body_template",
    [
        "{marker} [{mark}]\ncriterion\n",
        "{marker}\n [{mark}] criterion\n",
    ],
)
def test_ac_counts_rejects_checkboxes_assembled_across_lines(
    marker, mark, body_template,
):
    body = body_template.format(marker=marker, mark=mark)
    assert YouTrackTracker._ac_counts(body) == (0, 0)


def test_parse_adr_md_frontmatter_and_fallback():
    from foundry.setup_project import _parse_adr_md
    fm = ('---\ntype: adr\ntitle: "Ma décision — durable"\nstatus: accepted\n---\n\n'
          '# Corps\n\ntexte')
    title, status, body = _parse_adr_md(fm, "X-ADR-0001-ma-decision.md")
    assert title == "Ma décision — durable" and status == "accepted"
    assert body.startswith("# Corps")
    # no frontmatter → first heading, default status
    title2, status2, _ = _parse_adr_md("# Juste un titre\n\ncorps", "f.md")
    assert title2 == "Juste un titre" and status2 == "proposed"


def test_import_adrs_keys_idempotence_by_id_not_mutable_title(tmp_path):
    from foundry.models import Adr, Project
    from foundry.setup_project import import_adrs

    (tmp_path / "DEMO-ADR-0001-old-title.md").write_text(
        "---\ntitle: New title\nstatus: accepted\n---\n\n# New title\n",
        encoding="utf-8",
    )
    (tmp_path / "DEMO-ADR-0002-next.md").write_text(
        "---\ntitle: Next decision\nstatus: proposed\n---\n\n# Next decision\n",
        encoding="utf-8",
    )

    class FakeTracker:
        def __init__(self):
            self.adrs = [Adr(id="DEMO-ADR-0001", title="DEMO-ADR-0001 — Old title",
                             status="accepted")]

        def list_adrs(self, _project):
            return list(self.adrs)

        def create_adr(self, _project, title, body, status="proposed"):
            adr = Adr(id=f"DEMO-ADR-{len(self.adrs) + 1:04d}", title=title,
                      status=status, body=body)
            self.adrs.append(adr)
            return adr

    tracker = FakeTracker()
    import_adrs(tracker, Project(key="DEMO", id="0-1"), tmp_path)

    assert [a.id for a in tracker.adrs] == ["DEMO-ADR-0001", "DEMO-ADR-0002"]
    assert tracker.adrs[1].title == "Next decision"


def test_import_adrs_refuses_a_numbering_gap_before_writing(tmp_path):
    from foundry.models import Adr, Project
    from foundry.setup_project import import_adrs

    (tmp_path / "DEMO-ADR-0003-gap.md").write_text(
        "---\ntitle: Gap\nstatus: proposed\n---\n\n# Gap\n", encoding="utf-8"
    )

    class FakeTracker:
        def __init__(self):
            self.created = []

        def list_adrs(self, _project):
            return [Adr(id="DEMO-ADR-0001", title="One", status="accepted")]

        def create_adr(self, *_args, **_kwargs):
            self.created.append(True)
            raise AssertionError("preflight should reject before writing")

    tracker = FakeTracker()
    with pytest.raises(RuntimeError, match="numérotation ADR non contiguë"):
        import_adrs(tracker, Project(key="DEMO", id="0-1"), tmp_path)
    assert tracker.created == []


def test_import_adrs_refuses_unnumbered_file_before_any_write(tmp_path):
    from foundry.models import Project
    from foundry.setup_project import import_adrs

    (tmp_path / "00-not-an-adr-id.md").write_text("# Legacy\n", encoding="utf-8")
    (tmp_path / "DEMO-ADR-0001-first.md").write_text("# First\n", encoding="utf-8")

    class FakeTracker:
        def __init__(self):
            self.created = []

        def list_adrs(self, _project):
            return []

        def create_adr(self, *_args, **_kwargs):
            self.created.append(True)
            raise AssertionError("preflight should reject before writing")

    tracker = FakeTracker()
    with pytest.raises(RuntimeError, match="fichier ADR sans identifiant"):
        import_adrs(tracker, Project(key="DEMO", id="0-1"), tmp_path)
    assert tracker.created == []
