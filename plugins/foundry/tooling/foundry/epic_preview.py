"""Deterministic, strictly read-only preview of a versioned Dev Hub Epic graph.

This module owns no branch, agent, tracker mutation, budget reservation, command or
external-effect primitive.  It validates one complete projection and derives a plan
that later orchestration may revalidate; it cannot execute that plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from foundry import registry
from foundry.routing import LEVELS, ResolvedRoute, RoutingPolicy
from foundry.trackers.base import EpicSubgraphUnavailableError


GRAPH_CONTRACT = "devhub-application.v1"
POLICY_CONTRACT = "foundry-epic-preview-policy.v1"
PREVIEW_CONTRACT = "foundry-epic-preview.v1"
PREVIEW_DEPTH = 8
PREVIEW_NODE_LIMIT = 100
STATES = {"backlog", "ready", "in-progress", "review", "blocked", "done", "dropped"}
TERMINAL_STATES = {"done", "dropped"}
EDGE_TYPES = {"subtask-of", "depends-on", "relates"}
RISK_LABELS = {
    "risk:low": "low", "risk/low": "low", "risk-low": "low",
    "risk:medium": "medium", "risk/medium": "medium", "risk-medium": "medium",
    "risk:high": "high", "risk/high": "high", "risk-high": "high", "high-risk": "high",
    "risk:critical": "critical", "risk/critical": "critical",
    "risk-critical": "critical", "critical-risk": "critical",
}
ISSUE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-\d+$")


class EpicPreviewError(ValueError):
    """A public fail-closed preview validation error."""


@dataclass(frozen=True)
class ValidatedGraph:
    value: dict
    nodes: Mapping[str, dict]
    dependencies: Mapping[str, tuple[str, ...]]
    external_blockers: Mapping[str, tuple[dict, ...]]


def _fail(message: str) -> None:
    raise EpicPreviewError(message)


def _exact(value, keys: set[str], where: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(f"{where}: contrat incomplet ou clés inconnues")
    return value


def _integer(value, where: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        _fail(f"{where}: entier hors contrat")
    return value


def _nullable_text(value, where: str, maximum: int) -> None:
    if value is not None and (not isinstance(value, str) or not value or len(value) > maximum):
        _fail(f"{where}: texte nullable hors contrat")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _project_key(issue_id: str) -> str:
    if not isinstance(issue_id, str) or ISSUE_ID.fullmatch(issue_id) is None:
        _fail("identifiant issue hors contrat")
    return issue_id.rsplit("-", 1)[0]


def _validate_node(raw, index: int, project_key: str) -> dict:
    where = f"nodes[{index}]"
    node = _exact(raw, {
        "id", "title", "state", "priority", "estimate", "milestone", "type", "labels",
        "ac_done", "ac_total", "pr_url", "created", "updated", "version", "depth",
    }, where)
    if _project_key(node["id"]) != project_key:
        _fail(f"{where}: issue hors projet")
    if not isinstance(node["title"], str) or not 1 <= len(node["title"]) <= 300:
        _fail(f"{where}.title: texte hors contrat")
    if not isinstance(node["state"], str) or node["state"] not in STATES:
        _fail(f"{where}.state: état manquant ou inconnu")
    if (node["priority"] is not None
            and (not isinstance(node["priority"], str)
                 or node["priority"] not in {"P0", "P1", "P2", "P3"})):
        _fail(f"{where}.priority: priorité hors contrat")
    if node["estimate"] is not None:
        _integer(node["estimate"], f"{where}.estimate", minimum=1, maximum=21)
    _nullable_text(node["milestone"], f"{where}.milestone", 120)
    _nullable_text(node["type"], f"{where}.type", 80)
    if (not isinstance(node["labels"], list) or len(node["labels"]) > 50
            or any(not isinstance(label, str) or not label or len(label) > 80
                   for label in node["labels"])):
        _fail(f"{where}.labels: labels hors contrat")
    done = _integer(node["ac_done"], f"{where}.ac_done")
    total = _integer(node["ac_total"], f"{where}.ac_total")
    if done > total:
        _fail(f"{where}: critères d'acceptation incohérents")
    if node["pr_url"] is not None and not isinstance(node["pr_url"], str):
        _fail(f"{where}.pr_url: URL nullable hors contrat")
    for field in ("created", "updated"):
        if node[field] is not None:
            _integer(node[field], f"{where}.{field}")
    _integer(node["version"], f"{where}.version", minimum=1)
    _integer(node["depth"], f"{where}.depth", maximum=8)
    return dict(node)


def _assert_acyclic(vertices: set[str], adjacency: Mapping[str, set[str]], where: str) -> None:
    indegree = {vertex: 0 for vertex in vertices}
    for source, targets in adjacency.items():
        for target in targets:
            indegree[target] += 1
    ready = sorted(vertex for vertex, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        current = ready.pop(0)
        visited += 1
        for target in sorted(adjacency.get(current, ())):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if visited != len(vertices):
        _fail(f"{where}: cycle refusé")


def validate_subgraph(
    raw: object, *, expected_epic_id: str | None = None,
    expected_project_key: str | None = None,
) -> ValidatedGraph:
    """Validate and normalize the strict complete Dev Hub subgraph contract."""
    graph = _exact(raw, {
        "root", "nodes", "edges", "blockers", "progress", "depth", "truncation",
        "schema_version",
    }, "subgraph")
    if graph["schema_version"] != GRAPH_CONTRACT:
        _fail("subgraph.schema_version: contrat Dev Hub non supporté")
    root = graph["root"]
    project_key = _project_key(root)
    if expected_project_key is not None and project_key != expected_project_key:
        _fail("subgraph.root: projet différent du binding demandé")
    if expected_epic_id is not None and root != expected_epic_id:
        _fail("subgraph.root: racine différente de l'Epic demandé")
    if not isinstance(graph["nodes"], list) or not 1 <= len(graph["nodes"]) <= 100:
        _fail("subgraph.nodes: racine versionnée requise")
    nodes: dict[str, dict] = {}
    for index, raw_node in enumerate(graph["nodes"]):
        node = _validate_node(raw_node, index, project_key)
        if node["id"] in nodes:
            _fail("subgraph.nodes: identifiant dupliqué")
        nodes[node["id"]] = node
    root_node = nodes.get(root)
    if root_node is None or root_node["depth"] != 0 or (root_node["type"] or "").lower() != "epic":
        _fail("subgraph.root: Epic racine versionné incohérent")
    if any(node["id"] != root and node["depth"] == 0 for node in nodes.values()):
        _fail("subgraph.nodes: plusieurs racines")

    if not isinstance(graph["edges"], list) or len(graph["edges"]) > 400:
        _fail("subgraph.edges: collection hors contrat")
    edge_keys: set[tuple[str, str, str]] = set()
    parents: dict[str, str] = {}
    hierarchy = {issue_id: set() for issue_id in nodes}
    dependency_order = {issue_id: set() for issue_id in nodes}
    dependencies = {issue_id: set() for issue_id in nodes}
    clean_edges = []
    for index, raw_edge in enumerate(graph["edges"]):
        edge = _exact(raw_edge, {"type", "source", "target"}, f"edges[{index}]")
        if not isinstance(edge["type"], str) or edge["type"] not in EDGE_TYPES:
            _fail(f"edges[{index}].type: relation inconnue")
        source, target = edge["source"], edge["target"]
        if (_project_key(source) != project_key or _project_key(target) != project_key
                or source not in nodes or target not in nodes or source == target):
            _fail(f"edges[{index}]: extrémité absente ou auto-relation")
        key = (edge["type"], source, target)
        if key in edge_keys:
            _fail("subgraph.edges: relation dupliquée")
        edge_keys.add(key)
        clean_edges.append(dict(edge))
        if edge["type"] == "subtask-of":
            if source == root or source in parents:
                _fail("subgraph.edges: parenté multiple ou racine enfant")
            parents[source] = target
            hierarchy[target].add(source)
            if nodes[target]["depth"] + 1 != nodes[source]["depth"]:
                _fail("subgraph.edges: profondeur hiérarchique incohérente")
        elif edge["type"] == "depends-on":
            dependencies[source].add(target)
            dependency_order[target].add(source)
    descendants = set(nodes) - {root}
    if set(parents) != descendants:
        _fail("subgraph.edges: hiérarchie incomplète")
    _assert_acyclic(set(nodes), hierarchy, "subgraph.hierarchy")
    _assert_acyclic(set(nodes), dependency_order, "subgraph.dependencies")

    if not isinstance(graph["blockers"], list) or len(graph["blockers"]) > 400:
        _fail("subgraph.blockers: collection hors contrat")
    blocker_keys = set()
    external: dict[str, list[dict]] = {issue_id: [] for issue_id in nodes}
    observed_internal = set()
    clean_blockers = []
    for index, raw_blocker in enumerate(graph["blockers"]):
        blocker = _exact(raw_blocker, {
            "issue", "blocked_by", "blocker_state", "inside_subgraph",
        }, f"blockers[{index}]")
        issue, blocked_by = blocker["issue"], blocker["blocked_by"]
        if (_project_key(issue) != project_key or _project_key(blocked_by) != project_key
                or issue not in descendants):
            _fail(f"blockers[{index}]: blocker hors scope")
        if (not isinstance(blocker["blocker_state"], str)
                or blocker["blocker_state"] not in STATES
                or blocker["blocker_state"] == "done"):
            _fail(f"blockers[{index}]: état blocker incohérent")
        if type(blocker["inside_subgraph"]) is not bool:
            _fail(f"blockers[{index}].inside_subgraph: booléen requis")
        inside = blocked_by in nodes
        if blocker["inside_subgraph"] != inside:
            _fail(f"blockers[{index}]: appartenance contradictoire")
        key = (issue, blocked_by)
        if key in blocker_keys:
            _fail("subgraph.blockers: blocker dupliqué")
        blocker_keys.add(key)
        clean_blockers.append(dict(blocker))
        if inside:
            if blocked_by not in dependencies[issue] or nodes[blocked_by]["state"] != blocker["blocker_state"]:
                _fail(f"blockers[{index}]: relation ou version d'état incohérente")
            observed_internal.add(key)
        else:
            external[issue].append(dict(blocker))
    expected_internal = {
        (issue, blocked_by)
        for issue, values in dependencies.items()
        for blocked_by in values
        if nodes[blocked_by]["state"] != "done"
    }
    if observed_internal != expected_internal:
        _fail("subgraph.blockers: projection des dépendances incohérente")

    progress = _exact(graph["progress"], {"scope", "complete", "issues", "acceptance"}, "progress")
    issues_progress = _exact(progress["issues"], {"done", "terminal", "denominator"}, "progress.issues")
    acceptance = _exact(
        progress["acceptance"], {"done", "denominator", "issues_with_acceptance"},
        "progress.acceptance",
    )
    expected_issues = {
        "done": sum(nodes[item]["state"] == "done" for item in descendants),
        "terminal": sum(nodes[item]["state"] in TERMINAL_STATES for item in descendants),
        "denominator": len(descendants),
    }
    expected_acceptance = {
        "done": sum(nodes[item]["ac_done"] for item in descendants),
        "denominator": sum(nodes[item]["ac_total"] for item in descendants),
        "issues_with_acceptance": sum(nodes[item]["ac_total"] > 0 for item in descendants),
    }
    if progress["scope"] != "descendants" or type(progress["complete"]) is not bool:
        _fail("subgraph.progress: portée ou complétude incohérente")
    for field, value in issues_progress.items():
        _integer(value, f"progress.issues.{field}")
    for field, value in acceptance.items():
        _integer(value, f"progress.acceptance.{field}")
    if issues_progress != expected_issues or acceptance != expected_acceptance:
        _fail("subgraph.progress: agrégats incohérents")

    depth = _exact(graph["depth"], {"requested", "reached"}, "depth")
    requested = _integer(depth["requested"], "depth.requested", maximum=PREVIEW_DEPTH)
    reached = _integer(depth["reached"], "depth.reached", maximum=8)
    if requested != PREVIEW_DEPTH:
        _fail(f"depth.requested: {PREVIEW_DEPTH} requis par le preview")
    if reached != max(node["depth"] for node in nodes.values()) or reached > requested:
        _fail("subgraph.depth: profondeur incohérente")
    truncation = _exact(graph["truncation"], {"truncated", "reasons", "limits"}, "truncation")
    limits = _exact(truncation["limits"], {"nodes", "edges"}, "truncation.limits")
    node_limit = _integer(
        limits["nodes"], "truncation.limits.nodes",
        minimum=1, maximum=PREVIEW_NODE_LIMIT,
    )
    if node_limit != PREVIEW_NODE_LIMIT:
        _fail(f"truncation.limits.nodes: {PREVIEW_NODE_LIMIT} requis par le preview")
    if limits["edges"] != 400:
        _fail("truncation.limits.edges: plafond inconnu")
    if (truncation["truncated"] is not False or truncation["reasons"] != []
            or progress["complete"] is not True):
        _fail("subgraph: projection tronquée ou incomplète refusée")
    if limits["nodes"] < len(nodes):
        _fail("truncation.limits.nodes: projection incohérente")

    normalized = {
        **graph, "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": sorted(clean_edges, key=lambda item: (item["type"], item["source"], item["target"])),
        "blockers": sorted(clean_blockers, key=lambda item: (item["issue"], item["blocked_by"])),
    }
    return ValidatedGraph(
        normalized, nodes,
        {key: tuple(sorted(value)) for key, value in dependencies.items()},
        {key: tuple(sorted(value, key=lambda item: item["blocked_by"])) for key, value in external.items()},
    )


def _policy_request(raw: object) -> dict:
    policy = _exact(raw, {
        "schema_version", "host", "issued_at", "expires_at", "limits", "routing",
    }, "policy")
    if (policy["schema_version"] != POLICY_CONTRACT
            or not isinstance(policy["host"], str)
            or policy["host"] not in {"claude", "codex"}):
        _fail("policy: version ou hôte non supporté")
    issued = _integer(policy["issued_at"], "policy.issued_at")
    expires = _integer(policy["expires_at"], "policy.expires_at")
    if expires <= issued:
        _fail("policy.expires_at: expiration postérieure requise")
    limits = _exact(policy["limits"], {"max_concurrency", "budget"}, "policy.limits")
    concurrency = limits["max_concurrency"]
    if concurrency is not None:
        _integer(concurrency, "policy.limits.max_concurrency", minimum=1, maximum=100)
    budget = limits["budget"]
    if budget is not None:
        budget = _exact(budget, {"unit", "limit"}, "policy.limits.budget")
        if not isinstance(budget["unit"], str) or not budget["unit"] or len(budget["unit"]) > 32:
            _fail("policy.limits.budget.unit: unité invalide")
        _integer(budget["limit"], "policy.limits.budget.limit", minimum=1)
    routing = _exact(
        policy["routing"], {"implementer_minimum_tier", "implementer_minimum_source"},
        "policy.routing",
    )
    tier, source = routing["implementer_minimum_tier"], routing["implementer_minimum_source"]
    if tier is not None and (not isinstance(tier, str) or tier not in LEVELS):
        _fail("policy.routing.implementer_minimum_tier: tier inconnu")
    if (tier is None) != (source is None):
        _fail("policy.routing: tier et source minimum sont indissociables")
    if source is not None and (not isinstance(source, str) or not source or len(source) > 80):
        _fail("policy.routing.implementer_minimum_source: source invalide")
    return {
        **policy, "limits": {"max_concurrency": concurrency, "budget": budget},
        "routing": dict(routing),
    }


def _route_summary(route: ResolvedRoute) -> dict:
    return {
        "role": route.role, "tier": route.selected_tier, "model": route.model,
        "effort": route.effort, "minimum_tier": route.minimum_tier,
        "minimum_source": route.minimum_source,
    }


def _max_tier(left: str | None, right: str | None) -> str | None:
    values = [value for value in (left, right) if value is not None]
    return max(values, key=LEVELS.index) if values else None


def _risk(node: dict) -> str | None:
    values = {RISK_LABELS[label.strip().lower()] for label in node["labels"]
              if label.strip().lower() in RISK_LABELS}
    if len(values) > 1:
        _fail(f"{node['id']}: signaux de risque contradictoires")
    return next(iter(values), None)


def _classification_item(node: dict, reason: str, models: list[dict] | None = None) -> dict:
    result = {"id": node["id"], "state": node["state"], "reason": reason}
    if models is not None:
        result["models"] = models
    return result


def build_preview(
    raw_graph: object, raw_policy: object, routing_policy: RoutingPolicy,
    *, expected_epic_id: str | None = None, expected_project_key: str | None = None,
) -> dict:
    """Purely validate, classify, route and digest a preview (no I/O or mutation)."""
    graph = validate_subgraph(
        raw_graph,
        expected_epic_id=expected_epic_id,
        expected_project_key=expected_project_key,
    )
    request = _policy_request(raw_policy)
    root = graph.value["root"]
    base_floor = request["routing"]["implementer_minimum_tier"]
    base_source = request["routing"]["implementer_minimum_source"]
    host = request["host"]
    reviewer = routing_policy.resolve("reviewer", host)

    categories: dict[str, tuple[str, str]] = {root: ("omitted", "epic_root")}
    for issue_id in sorted(set(graph.nodes) - {root}):
        node = graph.nodes[issue_id]
        if node["state"] in TERMINAL_STATES:
            categories[issue_id] = ("omitted", f"terminal_{node['state']}")
        elif (node["type"] or "").lower() == "epic":
            categories[issue_id] = ("omitted", "nested_epic")
        elif node["type"] is None:
            categories[issue_id] = ("blocked", "missing_issue_type")
        elif node["state"] == "blocked":
            categories[issue_id] = ("blocked", "tracker_state_blocked")
        elif graph.external_blockers[issue_id]:
            categories[issue_id] = ("blocked", "external_blocker")
        else:
            categories[issue_id] = ("eligible", "ready_or_resumable")

    changed = True
    while changed:
        changed = False
        for issue_id, (category, _reason) in sorted(categories.items()):
            if category != "eligible":
                continue
            for blocker_id in graph.dependencies[issue_id]:
                blocker = graph.nodes[blocker_id]
                if blocker["state"] == "done":
                    continue
                blocker_category = categories[blocker_id][0]
                if blocker_category != "eligible":
                    categories[issue_id] = ("blocked", f"blocked_dependency:{blocker_id}")
                    changed = True
                    break

    eligible, blocked, omitted, human_gates, unknowns = [], [], [], [], []
    routes_by_issue: dict[str, list[dict]] = {}
    for issue_id in sorted(graph.nodes):
        node = graph.nodes[issue_id]
        category, reason = categories[issue_id]
        if issue_id != root and category != "omitted":
            risk = _risk(node)
            risk_floor = "apex" if risk == "critical" else "frontier" if risk == "high" else None
            floor = _max_tier(base_floor, risk_floor)
            source = (
                "issue_risk" if risk_floor is not None and floor == risk_floor
                else base_source
            )
            implementer = routing_policy.resolve(
                "implementer", host, minimum_tier=floor, minimum_source=source,
            )
            routes_by_issue[issue_id] = [_route_summary(implementer), _route_summary(reviewer)]
            human_gates.append({
                "issue": issue_id, "gate": "human_validation",
                "reason": "required_before_merge",
            })
            if node["ac_total"] == 0:
                unknowns.append(f"{issue_id}:acceptance_criteria")
                human_gates.append({
                    "issue": issue_id, "gate": "acceptance_definition",
                    "reason": "no_criteria_observed",
                })
            elif node["ac_done"] < node["ac_total"]:
                human_gates.append({
                    "issue": issue_id, "gate": "acceptance_completion",
                    "reason": f"{node['ac_total'] - node['ac_done']}_open",
                })
            if risk in {"high", "critical"}:
                human_gates.append({
                    "issue": issue_id, "gate": "risk_review", "reason": risk,
                })
            elif risk is None:
                unknowns.append(f"{issue_id}:risk")
            if node["type"] is None:
                unknowns.append(f"{issue_id}:issue_type")
            for field in ("priority", "estimate"):
                if node[field] is None:
                    unknowns.append(f"{issue_id}:{field}")
        item = _classification_item(node, reason, routes_by_issue.get(issue_id))
        if category == "eligible":
            eligible.append(item)
        elif category == "blocked":
            if reason == "external_blocker":
                item["blocked_by"] = [entry["blocked_by"] for entry in graph.external_blockers[issue_id]]
            blocked.append(item)
        else:
            omitted.append(item)

    if request["limits"]["max_concurrency"] is None:
        unknowns.append("policy:max_concurrency")
    if request["limits"]["budget"] is None:
        unknowns.append("policy:budget")

    eligible_ids = {item["id"] for item in eligible}
    prerequisites = {
        issue_id: {blocker for blocker in graph.dependencies[issue_id] if blocker in eligible_ids}
        for issue_id in eligible_ids
    }
    waves = []
    remaining = set(eligible_ids)
    completed: set[str] = set()
    concurrency = request["limits"]["max_concurrency"]
    while remaining and concurrency is not None:
        ready = sorted(
            issue_id for issue_id in remaining if prerequisites[issue_id] <= completed
        )
        if not ready:
            _fail("preview.waves: dépendances non planifiables")
        for offset in range(0, len(ready), concurrency):
            chunk = ready[offset:offset + concurrency]
            waves.append({"index": len(waves) + 1, "issues": chunk})
            completed.update(chunk)
            remaining.difference_update(chunk)

    baseline_implementer = routing_policy.resolve(
        "implementer", host, minimum_tier=base_floor, minimum_source=base_source,
    )
    effective_policy = {
        **request,
        "routing": {
            **request["routing"],
            "roles": {
                "implementer": _route_summary(baseline_implementer),
                "reviewer": _route_summary(reviewer),
            },
        },
    }
    material = {
        "contract": PREVIEW_CONTRACT,
        "snapshot": {
            "contract": GRAPH_CONTRACT,
            "root": root,
            "digest": _digest(graph.value),
            "versions": {issue_id: graph.nodes[issue_id]["version"] for issue_id in sorted(graph.nodes)},
        },
        "policy": effective_policy,
        "classifications": {
            "eligible": eligible, "blocked": blocked, "omitted": omitted,
            "human_gates": sorted(human_gates, key=lambda item: (item["issue"], item["gate"])),
        },
        "waves": waves,
        "unknowns": sorted(set(unknowns)),
        "authority": {
            "tracker": "devhub", "orchestration": "foundry", "gates": "foundry",
            "preview": "read-only", "effects": [],
        },
    }
    return {**material, "preview_digest": _digest(material)}


def preview_epic(tracker, project, epic_id: str, policy: object, routing_policy: RoutingPolicy) -> dict:
    """Read exactly one provider graph, then hand it to the pure preview core."""
    if not getattr(tracker, "epic_subgraph_supported", False):
        raise EpicSubgraphUnavailableError(
            f"sous-graphe Epic indisponible pour le tracker {tracker.name}"
        )
    project_key = getattr(project, "key", None)
    if project_key != _project_key(epic_id):
        _fail("preview.request: Epic hors du projet lié")
    graph = tracker.get_epic_subgraph(
        project, epic_id, depth=PREVIEW_DEPTH, nodes=PREVIEW_NODE_LIMIT,
    )
    return build_preview(
        graph, policy, routing_policy,
        expected_epic_id=epic_id,
        expected_project_key=project_key,
    )


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("entier positif ou nul attendu") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("entier positif ou nul attendu")
    return parsed


def _positive_int(value: str) -> int:
    parsed = _non_negative_int(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("entier strictement positif attendu")
    return parsed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Preview read-only d'un Epic Dev Hub")
    parser.add_argument("epic_id")
    parser.add_argument("--host", required=True, choices=("claude", "codex"))
    parser.add_argument("--issued-at", required=True, type=_non_negative_int)
    parser.add_argument("--expires-at", required=True, type=_positive_int)
    parser.add_argument("--max-concurrency", type=_positive_int)
    parser.add_argument("--budget-limit", type=_positive_int)
    parser.add_argument("--budget-unit")
    parser.add_argument("--implementer-floor", choices=LEVELS)
    parser.add_argument("--implementer-floor-source")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    if (args.budget_limit is None) != (args.budget_unit is None):
        parser.error("--budget-limit et --budget-unit sont requis ensemble")
    if (args.implementer_floor is None) != (args.implementer_floor_source is None):
        parser.error("--implementer-floor et --implementer-floor-source sont requis ensemble")
    from foundry import tracker as tracker_factory

    active_tracker = tracker_factory()
    project = active_tracker.resolve_project(registry.repo_basename(args.root))
    policy = {
        "schema_version": POLICY_CONTRACT,
        "host": args.host,
        "issued_at": args.issued_at,
        "expires_at": args.expires_at,
        "limits": {
            "max_concurrency": args.max_concurrency,
            "budget": (
                {"unit": args.budget_unit, "limit": args.budget_limit}
                if args.budget_limit is not None else None
            ),
        },
        "routing": {
            "implementer_minimum_tier": args.implementer_floor,
            "implementer_minimum_source": args.implementer_floor_source,
        },
    }
    result = preview_epic(
        active_tracker, project, args.epic_id, policy, RoutingPolicy.load(args.root),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
