import importlib.util
import json
import statistics
from pathlib import Path

import foundry
import pytest
from foundry import query, registry
from foundry.models import Issue
from foundry.query_measure import measure_sequence


MEASURE_SCRIPT = (Path(__file__).parents[1] / "benchmarks" / "foundry-74" /
                  "measure-v1.py")
BASELINE = MEASURE_SCRIPT.with_name("baseline-v1.json")


class _Tracker:
    name = "fixture"

    def __init__(self, issues):
        self.issues = issues
        self.calls = 0

    def resolve_project(self, _repo):
        from foundry.models import Project
        return Project("T", "fixture-project")

    def search(self, _project):
        self.calls += 1
        return self.issues

    def list_adrs(self, _project):
        self.calls += 1
        return []


def _measurement_module():
    spec = importlib.util.spec_from_file_location("foundry_74_measure_v1", MEASURE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _without_durations(value):
    if isinstance(value, dict):
        return {key: _without_durations(item) for key, item in value.items()
                if key != "duration_ms"}
    if isinstance(value, list):
        return [_without_durations(item) for item in value]
    return value


@pytest.mark.historical_fixture(
    reason="requires the omitted FOUNDRY-74 historical measurement script",
)
def test_historical_measurement_command_order_matches_skill_workflows():
    measurement = _measurement_module()
    expected = {
        "next-issue": ["query backlog"],
        "roadmap": ["query milestones", "query backlog", "query adrs"],
        "blockers": ["query backlog", "query adrs"],
        "groom": ["query backlog"],
        "intake": ["query adrs", "query backlog"],
    }
    observed = {
        workflow: [command for command, _call in measurement._before(workflow)]
        for workflow in expected
    }
    assert observed == expected


@pytest.mark.historical_fixture(
    reason="requires the omitted FOUNDRY-74 historical measurement script",
)
def test_reproducible_five_workflow_measurement_exceeds_reduction_target():
    evidence = _measurement_module().run()
    results = evidence["results"]
    reductions = [1 - result["after"]["serialized_bytes"] /
                  result["before"]["serialized_bytes"] for result in results]
    assert statistics.median(reductions) >= .60
    for result in results:
        assert result["tokenizer"] is None
        assert result["before"]["tracker_calls"] >= 1
        assert result["after"]["tracker_calls"] >= 1
        assert result["before"]["http_calls"] is None
        assert result["after"]["estimated_tokens"] is None
        assert result["before"]["duration_ms"] >= 0
        assert result["before"]["command_count"] in {1, 2, 3}
        for side in ("before", "after"):
            aggregate = result[side]
            assert aggregate["serialized_bytes"] == sum(
                row["serialized_bytes"] for row in aggregate["commands"])
            assert aggregate["lines"] == sum(row["lines"] for row in aggregate["commands"])
            assert aggregate["tracker_calls"] == sum(
                row["tracker_calls"] for row in aggregate["commands"])
            assert all(row["command"].startswith("query ") for row in aggregate["commands"])


@pytest.mark.historical_fixture(
    reason="requires the omitted FOUNDRY-74 measurement script and baseline",
)
def test_versioned_baseline_matches_fresh_generator_except_duration():
    fresh = _measurement_module().run()
    checked_in = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert _without_durations(fresh) == _without_durations(checked_in)


def test_measurement_executes_lazy_page_commands_inside_each_command_metric():
    calls = []

    def commands():
        assert calls == []
        yield "query profile sample", lambda: calls.append("page-1") or {"page": 1}
        assert calls == ["page-1"]
        yield "query profile sample 2", lambda: calls.append("page-2") or {"page": 2}

    result = measure_sequence("sample", lambda: (), commands)
    assert calls == ["page-1", "page-2"]
    assert [row["command"] for row in result["after"]["commands"]] == [
        "query profile sample", "query profile sample 2"]


def test_profile_snapshot_is_stable_when_provider_list_order_changes(monkeypatch):
    class ReorderingTracker(_Tracker):
        def search(self, project):
            self.calls += 1
            return self.issues if self.calls % 2 else list(reversed(self.issues))

    issues = [Issue(f"T-{n}", "active", state="backlog", priority="P1", created=1)
              for n in range(25)]
    tracker = ReorderingTracker(issues)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "fixture")
    first, second = query.profile("next-issue"), query.profile("next-issue", 2)
    assert first["pagination"]["snapshot_identity"] == second["pagination"]["snapshot_identity"]


def test_profile_snapshot_and_section_coordinates_change_with_updated_date(monkeypatch):
    issue = Issue("T-1", "active", state="backlog", created=1, updated=2)
    tracker = _Tracker([issue])
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "fixture")
    first = query.profile("next-issue")
    issue.updated = 3
    second = query.profile("next-issue")
    assert first["snapshot"]["identity"] != second["snapshot"]["identity"]
    assert second["sections"]["issues"]["snapshot_identity"] == second["snapshot"]["identity"]


def _profile_pages(workflow):
    page = 1
    seen = set()
    while page:
        assert page not in seen
        seen.add(page)
        result = query.profile(workflow, page)
        yield result
        page = result["pagination"]["next_page"]


def test_large_terminal_history_is_retrievable_in_bounded_sections(monkeypatch):
    issues = [Issue("T-active", "Active", state="backlog")]
    issues.extend(Issue(f"T-{n:04d}", f"History {n}", state="done")
                  for n in range(1000))
    tracker = _Tracker(issues)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "fixture")
    for workflow in ("groom", "intake"):
        recovered = set()
        snapshots = set()
        for result in _profile_pages(workflow):
            section = result["sections"]["historical_index"]
            assert section["total_count"] == 1000
            assert section["returned_count"] <= section["limit"] == 20
            assert section["snapshot_identity"] == result["snapshot"]["identity"]
            assert "omitted_ids" not in result
            recovered.update(row["id"] for row in result["historical_index"])
            snapshots.add(result["snapshot"]["identity"])
        assert recovered == {f"T-{n:04d}" for n in range(1000)}
        assert len(snapshots) == 1


def test_many_milestones_and_one_busy_milestone_stay_bounded(monkeypatch):
    issues = [Issue(f"T-{n:04d}", f"History {n}", state="done",
                    milestone=f"M-{n:04d}") for n in range(1000)]
    issues.extend(Issue(f"A-{n:03d}", f"Active {n}", state="backlog",
                        milestone="Active milestone") for n in range(100))
    tracker = _Tracker(issues)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "fixture")

    milestones, active_ids = {}, set()
    for result in _profile_pages("roadmap"):
        section = result["sections"]["milestones"]
        assert section["total_count"] == 1001
        assert section["returned_count"] <= section["limit"] == 20
        assert "omitted_ids" not in result
        for row in result["milestones"].values():
            assert not ({"open_ids", "blocked_ids", "wip_ids"} & row.keys())
        milestones.update(result["milestones"])
        active_ids.update(row["id"] for row in result["issues"])
    assert len(milestones) == 1001
    assert active_ids == {f"A-{n:03d}" for n in range(100)}
    assert milestones["Active milestone"]["open"] == 100


def test_all_terminal_backlog_has_one_safe_empty_graph_page(monkeypatch):
    tracker = _Tracker([Issue(f"T-{n}", f"Done {n}", state="done",
                              milestone="Past") for n in range(45)])
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "fixture")

    for workflow in ("next-issue", "blockers"):
        result = query.profile(workflow)
        assert result["issues"] == []
        assert result["sections"]["issues"] == {
            "total_count": 0, "returned_count": 0, "omitted_count": 0,
            "limit": 20, "page": 1, "page_count": 0, "next_page": None,
            "available": False, "limit_exceeded": False,
            "any_page_limit_exceeded": False,
            "snapshot_identity": result["snapshot"]["identity"],
        }
        assert result["pagination"]["page_count"] == 1
        assert result["pagination"]["next_page"] is None

    assert {name for page in _profile_pages("roadmap")
            for name in page["milestones"]} == {"Past"}
    for workflow in ("groom", "intake"):
        assert {row["id"] for page in _profile_pages(workflow)
                for row in page["historical_index"]} == {
                    f"T-{n}" for n in range(45)}


def test_case_distinct_milestones_keep_page_boundaries_when_tracker_reorders(monkeypatch):
    class ReorderingTracker(_Tracker):
        def search(self, project):
            self.calls += 1
            return self.issues if self.calls % 2 else list(reversed(self.issues))

    names = [f"a{n:02d}" for n in range(19)] + ["Z", "z"]
    tracker = ReorderingTracker([
        Issue(f"T-{n}", "Past work", state="done", milestone=name)
        for n, name in enumerate(names)
    ])
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "fixture")
    pages = list(_profile_pages("roadmap"))
    assert len({page["snapshot"]["identity"] for page in pages}) == 1
    observed = [name for page in pages for name in page["milestones"]]
    assert len(observed) == len(set(observed)) == len(names)
    assert set(observed) == set(names)


def test_oversized_component_is_whole_and_union_covers_every_active_issue(monkeypatch):
    from foundry.models import Link

    linked = [Issue(f"L-{n:02d}", f"Linked {n}", state="backlog",
                    links=([Link("relates", "outward", f"L-{n - 1:02d}")]
                           if n else [])) for n in range(25)]
    standalone = [Issue(f"S-{n:02d}", f"Standalone {n}", state="ready")
                  for n in range(22)]
    tracker = _Tracker(linked + standalone)
    monkeypatch.setattr(foundry, "tracker", lambda name=None: tracker)
    monkeypatch.setattr(registry, "repo_basename", lambda cwd=None: "fixture")

    pages = list(_profile_pages("next-issue"))
    issue_ids = {row["id"] for result in pages for row in result["issues"]}
    assert issue_ids == {issue.id for issue in linked + standalone}
    assert len(pages[0]["issues"]) == 25
    assert pages[0]["sections"]["issues"]["limit_exceeded"] is True
    assert all(result["limit_exceeded"] is True for result in pages)
    assert all(result["truncated"] is False for result in pages)
