"""Tracker-neutral Epic subgraph (``foundry-epic-subgraph.v1``, PAT-ADR-0027).

Built from portable reads only (``get_issue``, ``Issue.links``,
``validate_issue_binding``) inside one graph snapshot scope; it performs no write.
A truncated, cyclic, duplicated, out-of-project or unreadable graph is refused, never
returned partially.  The result is plain provider data: ``epic_preview.validate_subgraph``
still validates it before any use.
"""
from __future__ import annotations

from foundry.epic_preview import (
    NEUTRAL_GRAPH_CONTRACT,
    PREVIEW_DEPTH,
    PREVIEW_NODE_LIMIT,
    TERMINAL_STATES,
    EpicPreviewError,
    _project_key,
)


def _fail(message: str) -> None:
    raise EpicPreviewError(message)


def _node(issue, depth: int) -> dict:
    estimate = issue.estimate
    if isinstance(estimate, float) and estimate.is_integer():
        estimate = int(estimate)
    return {
        "id": issue.id, "title": issue.title, "state": issue.state,
        "priority": issue.priority, "estimate": estimate, "milestone": issue.milestone,
        "type": issue.type, "labels": list(issue.labels),
        "ac_done": issue.ac_done, "ac_total": issue.ac_total, "pr_url": issue.pr_url,
        "created": issue.created, "updated": issue.updated,
        "version": issue.version, "depth": depth,
    }


def _targets(issue, link_type: str) -> list[str]:
    targets = [link.target for link in issue.links if link.type == link_type]
    if any(not isinstance(item, str) or not item for item in targets):
        _fail(f"{issue.id}: lien {link_type} invalide")
    if len(targets) != len(set(targets)):
        _fail(f"{issue.id}: liens {link_type} dupliqués")
    return sorted(targets)


def build_neutral_subgraph(
    tracker, project, epic_id: str, *,
    depth: int = PREVIEW_DEPTH, nodes: int = PREVIEW_NODE_LIMIT,
) -> dict:
    """Read the hierarchy and ``depends-on`` edges of one Epic (read-only, fail closed)."""
    from foundry.write import _graph_snapshot_scope, epic_parent_validation_digest

    if (type(depth) is not int or not 0 <= depth <= PREVIEW_DEPTH
            or type(nodes) is not int or not 1 <= nodes <= PREVIEW_NODE_LIMIT):
        raise ValueError("budget de sous-graphe invalide")
    project_key = _project_key(epic_id)
    if getattr(project, "key", None) != project_key:
        _fail("subgraph.root: projet différent du binding demandé")
    with _graph_snapshot_scope(tracker):
        tracker.validate_issue_binding(project, epic_id)
        root = tracker.get_issue(epic_id)
        if root.id != epic_id:
            _fail("subgraph.root: identité contradictoire")
        issues = {epic_id: root}
        depths = {epic_id: 0}
        frontier = [epic_id]
        while frontier:
            next_frontier = []
            for parent_id in frontier:
                for child_id in _targets(issues[parent_id], "parent-of"):
                    if _project_key(child_id) != project_key:
                        _fail(f"{child_id}: issue hors projet")
                    if child_id in issues:
                        _fail(f"{child_id}: nœud dupliqué ou cycle de hiérarchie")
                    if depths[parent_id] + 1 > depth:
                        _fail("subgraph.depth: profondeur maximale dépassée")
                    if len(issues) + 1 > nodes:
                        _fail("subgraph.nodes: plafond de nœuds dépassé")
                    tracker.validate_issue_binding(project, epic_id, child_id)
                    child = tracker.get_issue(child_id)
                    if child.id != child_id:
                        _fail("subgraph.nodes: identité contradictoire")
                    if any(target != parent_id for target in _targets(child, "subtask-of")):
                        _fail(f"{child_id}: parent contradictoire")
                    issues[child_id] = child
                    depths[child_id] = depths[parent_id] + 1
                    next_frontier.append(child_id)
            frontier = next_frontier

        edges = [
            {"type": "subtask-of", "source": issue_id, "target": parent_id}
            for parent_id in issues for issue_id in _targets(issues[parent_id], "parent-of")
        ]
        blockers = []
        external_state: dict[str, str | None] = {}
        for source_id in sorted(issues):
            for target_id in _targets(issues[source_id], "depends-on"):
                if target_id == source_id:
                    _fail(f"{source_id}: auto-dépendance refusée")
                if target_id in issues:
                    edges.append({"type": "depends-on", "source": source_id, "target": target_id})
                    if issues[target_id].state != "done":
                        blockers.append({
                            "issue": source_id, "blocked_by": target_id,
                            "blocker_state": issues[target_id].state, "inside_subgraph": True,
                        })
                    continue
                if _project_key(target_id) != project_key:
                    _fail(f"{target_id}: blocker hors projet")
                if target_id not in external_state:
                    tracker.validate_issue_binding(project, epic_id, target_id)
                    external = tracker.get_issue(target_id)
                    if external.id != target_id:
                        _fail("subgraph.blockers: identité contradictoire")
                    external_state[target_id] = external.state
                blockers.append({
                    "issue": source_id, "blocked_by": target_id,
                    "blocker_state": external_state[target_id], "inside_subgraph": False,
                })

    descendants = [issues[key] for key in issues if key != epic_id]
    return {
        "root": epic_id,
        "nodes": [_node(issues[key], depths[key]) for key in sorted(issues)],
        "edges": sorted(edges, key=lambda e: (e["type"], e["source"], e["target"])),
        "blockers": sorted(blockers, key=lambda b: (b["issue"], b["blocked_by"])),
        "progress": {
            "scope": "descendants",
            "complete": True,
            "issues": {
                "done": sum(item.state == "done" for item in descendants),
                "terminal": sum(item.state in TERMINAL_STATES for item in descendants),
                "denominator": len(descendants),
            },
            "acceptance": {
                "done": sum(item.ac_done for item in descendants),
                "denominator": sum(item.ac_total for item in descendants),
                "issues_with_acceptance": sum(item.ac_total > 0 for item in descendants),
            },
        },
        "depth": {"requested": PREVIEW_DEPTH, "reached": max(depths.values())},
        "truncation": {
            "truncated": False, "reasons": [],
            "limits": {"nodes": PREVIEW_NODE_LIMIT, "edges": 400},
        },
        "parent_validation_digest": epic_parent_validation_digest(root),
        "schema_version": NEUTRAL_GRAPH_CONTRACT,
    }
