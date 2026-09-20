from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import foundry
import foundry.epic_preview as epic_preview
from foundry.epic_preview import (
    GRAPH_CONTRACT,
    POLICY_CONTRACT,
    EpicPreviewError,
    build_preview,
    preview_epic,
)
from foundry.models import Project
from foundry.routing import RoutingConfigError, RoutingPolicy
from foundry.trackers.devhub import DevHubTracker


_DEFAULT_BUDGET = object()


def node(
    issue_id: str,
    *,
    title: str | None = None,
    state: str = "backlog",
    issue_type: str | None = "Feature",
    depth: int = 1,
    version: int = 1,
    labels: list[str] | None = None,
    ac_done: int = 1,
    ac_total: int = 1,
    priority: str | None = "P1",
    estimate: int | None = 3,
) -> dict:
    return {
        "id": issue_id,
        "title": title or issue_id,
        "state": state,
        "priority": priority,
        "estimate": estimate,
        "milestone": "Pilot",
        "type": issue_type,
        "labels": labels or [],
        "ac_done": ac_done,
        "ac_total": ac_total,
        "pr_url": None,
        "created": 1_788_000_000_000,
        "updated": 1_788_000_000_001,
        "version": version,
        "depth": depth,
    }


def graph(
    children: list[dict] | None = None,
    *,
    dependencies: list[tuple[str, str]] | None = None,
    external_blockers: list[tuple[str, str, str]] | None = None,
    root_id: str = "APP-1",
) -> dict:
    children = children or []
    root = node(root_id, issue_type="Epic", depth=0, ac_done=0, ac_total=0)
    nodes = [root, *children]
    edges = [
        {"type": "subtask-of", "source": child["id"], "target": root_id}
        for child in children
    ]
    edges.extend(
        {"type": "depends-on", "source": source, "target": target}
        for source, target in dependencies or []
    )
    by_id = {item["id"]: item for item in nodes}
    blockers = [
        {
            "issue": source,
            "blocked_by": target,
            "blocker_state": by_id[target]["state"],
            "inside_subgraph": True,
        }
        for source, target in dependencies or []
        if by_id[target]["state"] != "done"
    ]
    blockers.extend(
        {
            "issue": issue,
            "blocked_by": blocker,
            "blocker_state": state,
            "inside_subgraph": False,
        }
        for issue, blocker, state in external_blockers or []
    )
    return {
        "root": root_id,
        "nodes": nodes,
        "edges": edges,
        "blockers": blockers,
        "progress": {
            "scope": "descendants",
            "complete": True,
            "issues": {
                "done": sum(item["state"] == "done" for item in children),
                "terminal": sum(item["state"] in {"done", "dropped"} for item in children),
                "denominator": len(children),
            },
            "acceptance": {
                "done": sum(item["ac_done"] for item in children),
                "denominator": sum(item["ac_total"] for item in children),
                "issues_with_acceptance": sum(item["ac_total"] > 0 for item in children),
            },
        },
        "depth": {"requested": 8, "reached": 1 if children else 0},
        "truncation": {
            "truncated": False,
            "reasons": [],
            "limits": {"nodes": 100, "edges": 400},
        },
        "schema_version": GRAPH_CONTRACT,
    }


def policy(
    *, max_concurrency: int | None = 2, budget: dict | None | object = _DEFAULT_BUDGET,
    floor: str | None = "frontier", expires_at: int = 2_000,
) -> dict:
    if budget is _DEFAULT_BUDGET:
        budget = {"unit": "tokens", "limit": 100_000}
    return {
        "schema_version": POLICY_CONTRACT,
        "host": "codex",
        "issued_at": 1_000,
        "expires_at": expires_at,
        "limits": {
            "max_concurrency": max_concurrency,
            "budget": budget,
        },
        "routing": {
            "implementer_minimum_tier": floor,
            "implementer_minimum_source": "concurrency_risk" if floor else None,
        },
    }


@pytest.fixture
def routing_policy(tmp_path: Path) -> RoutingPolicy:
    return RoutingPolicy(tmp_path, tmp_path / ".foundry" / "model-routing.json")


def test_empty_epic_is_a_stable_read_only_preview(routing_policy):
    source = graph()
    first = build_preview(source, policy(), routing_policy)
    second = build_preview(copy.deepcopy(source), copy.deepcopy(policy()), routing_policy)

    assert first == second
    assert first["preview_digest"] == second["preview_digest"]
    assert first["classifications"]["eligible"] == []
    assert first["classifications"]["blocked"] == []
    assert first["classifications"]["omitted"] == [{
        "id": "APP-1", "state": "backlog", "reason": "epic_root",
    }]
    assert first["waves"] == []
    assert first["authority"] == {
        "tracker": "devhub", "orchestration": "foundry", "gates": "foundry",
        "preview": "read-only", "effects": [],
    }


def test_reopened_child_is_resumable_not_omitted(routing_policy):
    result = build_preview(
        graph([node("APP-2", state="in-progress", version=7)]), policy(), routing_policy,
    )

    assert [item["id"] for item in result["classifications"]["eligible"]] == ["APP-2"]
    assert result["classifications"]["eligible"][0]["reason"] == "ready_or_resumable"
    assert result["snapshot"]["versions"]["APP-2"] == 7
    assert result["waves"] == [{"index": 1, "issues": ["APP-2"]}]


def test_external_blocker_is_explicit_and_never_scheduled(routing_policy):
    result = build_preview(
        graph(
            [node("APP-2")],
            external_blockers=[("APP-2", "APP-99", "in-progress")],
        ),
        policy(), routing_policy,
    )

    assert result["classifications"]["blocked"][0]["reason"] == "external_blocker"
    assert result["classifications"]["blocked"][0]["blocked_by"] == ["APP-99"]
    assert result["waves"] == []


def test_open_acceptance_is_a_human_gate_not_inferred_success(routing_policy):
    result = build_preview(
        graph([node("APP-2", ac_done=1, ac_total=3)]), policy(), routing_policy,
    )

    gates = result["classifications"]["human_gates"]
    assert {item["gate"] for item in gates} == {"acceptance_completion", "human_validation"}
    assert next(item for item in gates if item["gate"] == "acceptance_completion")["reason"] == "2_open"


def test_high_risk_enforces_frontier_implementer_and_risk_gate(routing_policy):
    result = build_preview(
        graph([node("APP-2", labels=["risk:high"])]),
        policy(floor=None),
        routing_policy,
    )

    eligible = result["classifications"]["eligible"][0]
    implementer = next(model for model in eligible["models"] if model["role"] == "implementer")
    assert implementer == {
        "role": "implementer", "tier": "frontier", "model": "gpt-5.6-sol",
        "effort": "high", "minimum_tier": "frontier", "minimum_source": "issue_risk",
    }
    assert any(gate["gate"] == "risk_review" for gate in result["classifications"]["human_gates"])


def test_missing_execution_data_is_blocked_or_reported_unknown(routing_policy):
    result = build_preview(
        graph([node(
            "APP-2", issue_type=None, priority=None, estimate=None,
            ac_done=0, ac_total=0,
        )]),
        policy(max_concurrency=None, budget={"unit": "tokens", "limit": 1}),
        routing_policy,
    )

    assert result["classifications"]["blocked"][0]["reason"] == "missing_issue_type"
    assert result["unknowns"] == [
        "APP-2:acceptance_criteria", "APP-2:estimate", "APP-2:issue_type",
        "APP-2:priority", "APP-2:risk", "policy:max_concurrency",
    ]


def test_missing_concurrency_emits_no_implicitly_unbounded_wave(routing_policy):
    result = build_preview(
        graph([node("APP-2", state="ready"), node("APP-3", state="ready")]),
        policy(max_concurrency=None),
        routing_policy,
    )

    assert [item["id"] for item in result["classifications"]["eligible"]] == [
        "APP-2", "APP-3",
    ]
    assert result["policy"]["limits"]["max_concurrency"] is None
    assert "policy:max_concurrency" in result["unknowns"]
    assert result["waves"] == []


def test_missing_budget_is_preserved_and_reported_unknown(routing_policy):
    result = build_preview(
        graph([node("APP-2", state="ready")]),
        policy(max_concurrency=1, budget=None),
        routing_policy,
    )

    assert result["policy"]["limits"]["budget"] is None
    assert "policy:budget" in result["unknowns"]
    assert result["waves"] == [{"index": 1, "issues": ["APP-2"]}]


def test_internal_dependencies_make_deterministic_capped_waves(routing_policy):
    source = graph(
        [node("APP-4"), node("APP-2"), node("APP-3")],
        dependencies=[("APP-4", "APP-2")],
    )
    result = build_preview(source, policy(max_concurrency=1), routing_policy)

    assert result["waves"] == [
        {"index": 1, "issues": ["APP-2"]},
        {"index": 2, "issues": ["APP-3"]},
        {"index": 3, "issues": ["APP-4"]},
    ]


@pytest.mark.parametrize("mutation", [
    lambda value: value["truncation"].update({"truncated": True, "reasons": ["nodes"]}),
    lambda value: value["progress"]["issues"].update({"denominator": 99}),
    lambda value: value["nodes"][1].pop("version"),
])
def test_truncated_incoherent_or_missing_graph_is_refused(routing_policy, mutation):
    source = graph([node("APP-2")])
    mutation(source)

    with pytest.raises(EpicPreviewError):
        build_preview(source, policy(), routing_policy)


def test_preview_refuses_response_with_depth_below_requested_coordinate(routing_policy):
    source = graph([node("APP-2")])
    source["depth"]["requested"] = 7

    with pytest.raises(EpicPreviewError, match=r"depth\.requested.*8"):
        build_preview(source, policy(), routing_policy)


def test_preview_refuses_response_with_node_cap_below_requested_coordinate(routing_policy):
    source = graph([node("APP-2")])
    source["truncation"]["limits"]["nodes"] = 99

    with pytest.raises(EpicPreviewError, match=r"limits\.nodes.*100"):
        build_preview(source, policy(), routing_policy)


def test_dependency_cycle_is_refused(routing_policy):
    source = graph(
        [node("APP-2"), node("APP-3")],
        dependencies=[("APP-2", "APP-3"), ("APP-3", "APP-2")],
    )

    with pytest.raises(EpicPreviewError, match="cycle"):
        build_preview(source, policy(), routing_policy)


def test_digest_binds_every_material_snapshot_and_policy_change(routing_policy):
    source = graph([node("APP-2"), node("APP-3")])
    original = build_preview(source, policy(), routing_policy)["preview_digest"]

    changed_version = copy.deepcopy(source)
    changed_version["nodes"][1]["version"] = 2
    changed_state = copy.deepcopy(source)
    changed_state["nodes"][1]["state"] = "ready"
    changed_edge = copy.deepcopy(source)
    changed_edge["edges"].append({
        "type": "relates", "source": "APP-2", "target": "APP-3",
    })
    changed_blocker = graph(
        [node("APP-2"), node("APP-3")],
        external_blockers=[("APP-2", "APP-99", "in-progress")],
    )
    changed_acceptance = graph([
        node("APP-2", ac_done=0, ac_total=1), node("APP-3"),
    ])
    changed_risk = graph([
        node("APP-2", labels=["risk:high"]), node("APP-3"),
    ])

    graph_variants = {
        "version": changed_version,
        "state": changed_state,
        "edge": changed_edge,
        "blocker": changed_blocker,
        "acceptance": changed_acceptance,
        "risk": changed_risk,
    }
    for dimension, changed_graph in graph_variants.items():
        digest = build_preview(changed_graph, policy(), routing_policy)["preview_digest"]
        assert digest != original, dimension

    policy_variants = {
        "expiry": policy(expires_at=2_001),
        "budget": policy(budget={"unit": "tokens", "limit": 200_000}),
        "concurrency": policy(max_concurrency=1),
    }
    for dimension, changed_policy in policy_variants.items():
        digest = build_preview(source, changed_policy, routing_policy)["preview_digest"]
        assert digest != original, dimension

    changed_mapping = RoutingPolicy(
        routing_policy.root,
        routing_policy.config_path,
        mapping_overrides={
            "codex": {"frontier": {"model": "codex-frontier-test"}},
        },
    )
    mapping_digest = build_preview(source, policy(), changed_mapping)["preview_digest"]
    assert mapping_digest != original, "mapping"


class ReadOnlyTracker:
    name = "devhub"
    epic_subgraph_supported = True

    def __init__(self, source):
        self.source = source
        self.calls = []

    def get_epic_subgraph(self, project, epic_id, *, depth, nodes):
        self.calls.append((project.key, epic_id, depth, nodes))
        return self.source

    def __getattr__(self, name):
        raise AssertionError(f"unexpected authority: {name}")


def test_preview_cli_refuses_unexecutable_claude_policy(monkeypatch, tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(
        '{"mappings":{"claude":{"balanced":{"model":"untranslated-model"}}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        foundry,
        "tracker",
        lambda: SimpleNamespace(resolve_project=lambda _repository: Project(key="APP", id="1")),
    )
    monkeypatch.setattr(
        epic_preview,
        "preview_epic",
        lambda *_args: pytest.fail("la preview ne doit pas recevoir une policy Claude invalide"),
    )

    with pytest.raises(RoutingConfigError, match="untranslated-model.*traduction hôte"):
        epic_preview.main([
            "APP-1", "--host", "claude", "--issued-at", "1", "--expires-at", "2",
            "--root", str(tmp_path),
        ])


def test_provider_boundary_performs_one_bound_get_and_no_effect(routing_policy):
    tracker = ReadOnlyTracker(graph([node("APP-2")]))
    result = preview_epic(
        tracker, Project(key="APP", id="1"), "APP-1", policy(), routing_policy,
    )

    assert tracker.calls == [("APP", "APP-1", 8, 100)]
    assert result["snapshot"]["root"] == "APP-1"
    assert result["authority"]["effects"] == []


def test_provider_boundary_refuses_response_for_another_epic(routing_policy):
    tracker = ReadOnlyTracker(graph(root_id="APP-9"))

    with pytest.raises(EpicPreviewError, match="racine.*demandé"):
        preview_epic(
            tracker, Project(key="APP", id="1"), "APP-1", policy(), routing_policy,
        )


def test_provider_boundary_refuses_response_for_another_project(routing_policy):
    tracker = ReadOnlyTracker(graph(root_id="OTHER-1"))

    with pytest.raises(EpicPreviewError, match="projet.*binding"):
        preview_epic(
            tracker, Project(key="APP", id="1"), "APP-1", policy(), routing_policy,
        )


def test_devhub_subgraph_transport_is_bound_and_get_only(monkeypatch):
    tracker = DevHubTracker(
        url="https://devhub.example", token="t" * 24, proof_secret="s" * 32,
    )
    calls = []
    monkeypatch.setattr(
        tracker, "_req",
        lambda method, path, **kwargs: calls.append((method, path, kwargs)) or graph(),
    )

    result = tracker.get_epic_subgraph(Project(key="APP", id="1"), "APP-1")

    assert result["root"] == "APP-1"
    assert calls == [(
        "GET", "/projects/APP/subgraph?root=APP-1&depth=8&nodes=100",
        {"response_contract": "devhub-application.v1"},
    )]
