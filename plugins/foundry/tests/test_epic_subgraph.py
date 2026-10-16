"""PAT-178: neutral Epic subgraph, structure digest and capability gate (no network)."""
from __future__ import annotations

import contextlib
import copy
from pathlib import Path

import pytest

from foundry.epic_preview import (
    NEUTRAL_GRAPH_CONTRACT,
    EpicPreviewError,
    build_preview,
    preview_epic,
    structure_digest,
    validate_subgraph,
)
from foundry.epic_subgraph import build_neutral_subgraph
from foundry.models import Issue, Link, Project
from foundry.routing import RoutingPolicy
from foundry.trackers.base import EpicSubgraphUnavailableError
from foundry.trackers.linear import LinearTracker, LinearTrackerError, _connection

import test_epic_preview as shared

PROJECT = Project(key="APP", id="1")


def issue(issue_id, *, parent=None, children=(), depends=(), state="backlog",
          issue_type="Feature", version=1, title=None, labels=()):
    links = [Link("parent-of", "outward", child) for child in children]
    if parent:
        links.append(Link("subtask-of", "inward", parent))
    links += [Link("depends-on", "outward", target) for target in depends]
    return Issue(
        id=issue_id, title=title or issue_id, state=state, priority="P1", estimate=3,
        milestone="Pilot", type=issue_type, labels=list(labels), ac_done=1, ac_total=1,
        links=links, pr_url=None, body=f"body {issue_id}", created=1, updated=2,
        version=version,
    )


class FakeLinear:
    """Linear-like: versionless-capable, snapshot scope, binding check, read log."""

    name = "linear"
    epic_subgraph_supported = True

    def __init__(self, issues, foreign=()):
        self.issues = {item.id: item for item in issues}
        self.foreign = set(foreign)
        self.reads = []
        self.scopes = 0

    @contextlib.contextmanager
    def graph_snapshot(self):
        self.scopes += 1
        yield

    def validate_issue_binding(self, project, *issue_ids):
        for issue_id in issue_ids:
            if issue_id in self.foreign or issue_id not in self.issues:
                raise RuntimeError("issue_outside_binding")

    def get_issue(self, issue_id):
        self.reads.append(issue_id)
        return self.issues[issue_id]

    def get_epic_subgraph(self, project, epic_id, *, depth=8, nodes=100):
        return build_neutral_subgraph(self, project, epic_id, depth=depth, nodes=nodes)


def base_issues(**overrides):
    items = {
        "APP-1": issue("APP-1", children=("APP-2", "APP-3"), issue_type="Epic"),
        "APP-2": issue("APP-2", parent="APP-1"),
        "APP-3": issue("APP-3", parent="APP-1", depends=("APP-2", "APP-9")),
        "APP-9": issue("APP-9", state="review"),
    }
    items.update(overrides)
    return list(items.values())


def digest_of(tracker):
    return structure_digest(validate_subgraph(
        tracker.get_epic_subgraph(PROJECT, "APP-1"),
        expected_epic_id="APP-1", expected_project_key="APP",
    ))


@pytest.fixture
def routing_policy(tmp_path: Path) -> RoutingPolicy:
    return RoutingPolicy(tmp_path, tmp_path / ".foundry" / "model-routing.json")


def test_neutral_contract_round_trip_and_preview(routing_policy):
    tracker = FakeLinear(base_issues())
    result = preview_epic(tracker, PROJECT, "APP-1", shared.policy(), routing_policy)
    assert result["snapshot"]["contract"] == NEUTRAL_GRAPH_CONTRACT
    assert result["authority"]["tracker"] == "linear"
    assert len(result["structure_digest"]) == 64
    assert result["snapshot"]["versions"]["APP-2"] == 1
    assert tracker.scopes == 1
    assert [item["id"] for item in result["classifications"]["blocked"]] == ["APP-3"]


def test_versionless_node_keeps_null_version(routing_policy):
    tracker = FakeLinear(base_issues(**{"APP-2": issue("APP-2", parent="APP-1", version=None)}))
    result = preview_epic(tracker, PROJECT, "APP-1", shared.policy(), routing_policy)
    assert result["snapshot"]["versions"]["APP-2"] is None


def test_structure_digest_changes_with_structure():
    original = digest_of(FakeLinear(base_issues()))
    added_child = base_issues(**{
        "APP-1": issue("APP-1", children=("APP-2", "APP-3", "APP-4"), issue_type="Epic"),
        "APP-4": issue("APP-4", parent="APP-1"),
    })
    added_edge = base_issues(**{"APP-2": issue("APP-2", parent="APP-1", depends=("APP-9",))})
    changed_type = base_issues(**{"APP-2": issue("APP-2", parent="APP-1", issue_type="Bug")})
    added_blocker = base_issues(**{
        "APP-3": issue("APP-3", parent="APP-1", depends=("APP-2", "APP-9", "APP-8")),
        "APP-8": issue("APP-8"),
    })
    internal_edge = base_issues(**{
        "APP-2": issue("APP-2", parent="APP-1", depends=()),
        "APP-3": issue("APP-3", parent="APP-1", depends=("APP-9",)),
    })
    for name, items in {
        "child": added_child, "type": changed_type, "external": added_blocker,
        "internal edge removed": internal_edge, "external edge from another node": added_edge,
    }.items():
        assert digest_of(FakeLinear(items)) != original, name
    # a new INTERNAL edge: APP-2 -> APP-4 where APP-4 is a child
    with_child = {"APP-1": issue("APP-1", children=("APP-2", "APP-3", "APP-4"), issue_type="Epic"),
                  "APP-4": issue("APP-4", parent="APP-1")}
    base = digest_of(FakeLinear(base_issues(**with_child)))
    linked = base_issues(**with_child, **{"APP-2": issue("APP-2", parent="APP-1", depends=("APP-4",))})
    assert digest_of(FakeLinear(linked)) != base


def test_structure_digest_ignores_state_version_title_labels_and_done_blocker():
    original = digest_of(FakeLinear(base_issues()))
    moved = base_issues(**{
        "APP-2": issue("APP-2", parent="APP-1", state="in-progress", version=99,
                       title="renamed", labels=("risk:high",)),
        "APP-9": issue("APP-9", state="done", version=7),
    })
    assert digest_of(FakeLinear(moved)) == original


def test_parent_validation_digest_is_part_of_structure():
    original = digest_of(FakeLinear(base_issues()))
    edited = issue("APP-1", children=("APP-2", "APP-3"), issue_type="Epic")
    edited.body = "other need"
    assert digest_of(FakeLinear(base_issues(**{"APP-1": edited}))) != original


def test_preview_digest_still_covers_state_and_structure_digest_is_outside_it(routing_policy):
    first = preview_epic(FakeLinear(base_issues()), PROJECT, "APP-1", shared.policy(), routing_policy)
    moved = base_issues(**{"APP-2": issue("APP-2", parent="APP-1", state="done")})
    second = preview_epic(FakeLinear(moved), PROJECT, "APP-1", shared.policy(), routing_policy)
    assert first["preview_digest"] != second["preview_digest"]
    assert first["structure_digest"] == second["structure_digest"]


def test_devhub_preview_digest_is_unchanged_and_parent_is_a_named_absence(routing_policy):
    source = shared.graph(
        [shared.node("APP-2"), shared.node("APP-3", state="in-progress", version=4)],
        dependencies=[("APP-3", "APP-2")],
        external_blockers=[("APP-3", "APP-9", "review")],
    )
    result = build_preview(source, shared.policy(), routing_policy)
    assert result["preview_digest"] == (
        "6975a10c835b93ff636e263fab05a5c1bfcf8762df357c57b1a61874ea40a0ca"
    )
    assert result["snapshot"]["digest"] == (
        "cc69b0b751d9149a4d105018a1b3326d9917573556ccc9ea8432e0e32762429d"
    )
    assert result["snapshot"]["contract"] == "devhub-application.v1"
    assert result["authority"]["tracker"] == "devhub"
    other = copy.deepcopy(source)
    other["nodes"][2]["state"] = "review"
    assert (build_preview(other, shared.policy(), routing_policy)["structure_digest"]
            == result["structure_digest"])


def test_unknown_contract_is_refused():
    source = shared.graph()
    source["schema_version"] = "other.v1"
    with pytest.raises(EpicPreviewError, match="contrat"):
        validate_subgraph(source)


def test_more_than_100_nodes_is_refused():
    kids = tuple(f"APP-{n}" for n in range(2, 103))  # 101 children + root
    items = [issue("APP-1", children=kids, issue_type="Epic")]
    items += [issue(k, parent="APP-1") for k in kids]
    with pytest.raises(EpicPreviewError, match="plafond"):
        FakeLinear(items).get_epic_subgraph(PROJECT, "APP-1")


def test_hierarchy_deeper_than_8_is_refused():
    items = [issue("APP-1", children=("APP-2",), issue_type="Epic")]
    for n in range(2, 11):
        items.append(issue(f"APP-{n}", parent=f"APP-{n - 1}", children=(f"APP-{n + 1}",)))
    items.append(issue("APP-11", parent="APP-10"))
    with pytest.raises(EpicPreviewError, match="profondeur"):
        FakeLinear(items).get_epic_subgraph(PROJECT, "APP-1")


def test_node_outside_project_is_refused():
    foreign_id = FakeLinear(base_issues(**{
        "APP-1": issue("APP-1", children=("OTHER-2",), issue_type="Epic"),
        "OTHER-2": issue("OTHER-2", parent="APP-1"),
    }))
    with pytest.raises(EpicPreviewError, match="hors projet"):
        foreign_id.get_epic_subgraph(PROJECT, "APP-1")
    bound = FakeLinear(base_issues(), foreign={"APP-3"})
    with pytest.raises(RuntimeError, match="outside_binding"):
        bound.get_epic_subgraph(PROJECT, "APP-1")


def test_cycles_and_duplicates_are_refused():
    hierarchy = base_issues(**{"APP-2": issue("APP-2", parent="APP-1", children=("APP-1",))})
    with pytest.raises(EpicPreviewError, match="cycle|dupliqué"):
        FakeLinear(hierarchy).get_epic_subgraph(PROJECT, "APP-1")
    twice = base_issues(**{"APP-2": issue("APP-2", parent="APP-1", children=("APP-3",))})
    with pytest.raises(EpicPreviewError, match="dupliqué|contradictoire"):
        FakeLinear(twice).get_epic_subgraph(PROJECT, "APP-1")
    loop = base_issues(**{
        "APP-2": issue("APP-2", parent="APP-1", depends=("APP-3",)),
        "APP-3": issue("APP-3", parent="APP-1", depends=("APP-2",)),
    })
    with pytest.raises(EpicPreviewError, match="cycle"):
        validate_subgraph(FakeLinear(loop).get_epic_subgraph(PROJECT, "APP-1"))
    selfdep = base_issues(**{"APP-2": issue("APP-2", parent="APP-1", depends=("APP-2",))})
    with pytest.raises(EpicPreviewError, match="auto-dépendance"):
        FakeLinear(selfdep).get_epic_subgraph(PROJECT, "APP-1")


def test_unreadable_node_fails_closed():
    tracker = FakeLinear(base_issues())
    del tracker.issues["APP-2"]
    with pytest.raises(RuntimeError, match="outside_binding"):
        tracker.get_epic_subgraph(PROJECT, "APP-1")


def test_nested_connection_over_100_is_refused_by_the_linear_parser():
    page = {"nodes": [{"identifier": f"APP-{n}"} for n in range(100)],
            "pageInfo": {"hasNextPage": True, "endCursor": "x"}}
    with pytest.raises(LinearTrackerError, match="nested_pagination_unavailable"):
        _connection(page, "normalize.children")


def test_linear_declares_the_capability():
    assert LinearTracker.epic_subgraph_supported is True


def test_tracker_without_capability_refuses_before_any_read(routing_policy):
    class Silent:
        name = "youtrack"
        epic_subgraph_supported = False

        def __getattr__(self, attr):
            raise AssertionError(f"tracker touched: {attr}")

    with pytest.raises(EpicSubgraphUnavailableError):
        preview_epic(Silent(), PROJECT, "APP-1", shared.policy(), routing_policy)
