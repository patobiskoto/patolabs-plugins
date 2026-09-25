"""Query tier — rich, normalized JSON. ZERO decisions, ZERO prose.

This is the half of the old tooling that was missing: it hands the model the
material to reason about (the whole graph: links, AC, milestone rollups, ADRs),
instead of a pre-decided verdict. Judgment lives in the skill, not here. The
deterministic priority rank is emitted as ONE field among many — a signal the
skill can override with a reason, never the answer.

List payloads are LEAN (no bodies): `backlog`/`candidates`/`adrs` carry the signal
(fields, links, AC counts, ranks, statuses); the free text comes on demand via
`issue <ID>` and `adr <ADR-ID>` — summary first, detail when it's actually needed.

CLI: PYTHONPATH=…/tooling python3 -m foundry.query \
     <backlog|candidates|milestones|issue|adrs|adr|changelog|profile> [args]
"""
from __future__ import annotations

import hashlib
import json
import sys

import foundry
from foundry import registry
from foundry.trackers.base import (
    IssueUnavailableError,
    TrackerCapabilityUnavailableError,
    TrackerConflictError,
)

_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_TERMINAL_STATES = {"done", "dropped", "fixed"}
# normalized link types (the models.Link contract — adapters translate provider phrases)
# that mean, from the source issue's perspective, "I am blocked until target is done".
# NB: "subtask-of" is deliberately NOT here — a child isn't blocked by its (open) epic;
# the epic is open *because* of its children. Only real dependencies block.
_BLOCKED_ROLES = {"depends-on"}
_UNLOCKS_ROLES = {"blocks", "parent-of"}

# This is a consumer contract, not a YouTrack query language.  The adapter returns
# normalized Issues first; profiles then make an explicit, auditable projection.
PROFILE_CONTRACT_VERSION = "foundry.query-profile.v1"
_PROFILE_LIMITS = {"next-issue": 20, "roadmap": 20, "blockers": 20,
                   "groom": 20, "intake": 20}


def _is_terminal(state):
    """Recognize terminal Foundry and provider states without case coupling."""
    return isinstance(state, str) and state.casefold() in _TERMINAL_STATES


def _rank_key(i):
    return (_PRIORITY_RANK.get(i.priority, 9),
            i.estimate if i.estimate is not None else 1e9,
            i.created if i.created is not None else 1e18,
            i.id)


def _annotate(issues):
    """Add derived signals every judgment skill needs, without deciding anything."""
    state_by_id = {i.id: i.state for i in issues}
    dicts = []
    for i in issues:
        d = i.to_dict()
        blocked_by, unlocks = [], []
        for lk in i.links:
            # unknown target (outside the project) never blocks; a KNOWN issue blocks
            # unless resolved — even when its State field is unset
            known = lk.target in state_by_id
            if (lk.type in _BLOCKED_ROLES and known
                    and not _is_terminal(state_by_id[lk.target])):
                blocked_by.append(lk.target)
            if lk.type in _UNLOCKS_ROLES:
                unlocks.append(lk.target)
        d["blocked_by"] = blocked_by
        d["unblocked"] = not blocked_by
        d["unlocks"] = unlocks
        d["unlocks_count"] = len(unlocks)
        d["ac_ratio"] = round(i.ac_done / i.ac_total, 2) if i.ac_total else None
        dicts.append(d)
    # deterministic rank as a FIELD (a prior, not a verdict)
    order = sorted(range(len(issues)), key=lambda k: _rank_key(issues[k]))
    for rank, k in enumerate(order):
        dicts[k]["rank_index"] = rank
    return dicts


def _project(tr):
    resolver = getattr(tr, "resolve_checkout_project", None)
    if callable(resolver):
        return resolver()
    return tr.resolve_project(registry.repo_basename())


def _lean(d):
    """Strip the heavy free-text fields from a LIST payload — summary first, detail
    on demand (`query issue <ID>` / `query adr <ID>`). ac_done/ac_total keep carrying
    the AC signal without the prose."""
    d.pop("body", None)
    d.pop("comments", None)
    return d


def _snapshot(dicts):
    """Provider-neutral identity of exactly the normalized list observed."""
    encoded = json.dumps([_lean(dict(d)) for d in sorted(dicts, key=lambda d: d["id"])], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode()
    return {"version": "normalized-issue-snapshot-v1",
            "identity": "sha256:" + hashlib.sha256(encoded).hexdigest()}


def _profile_seed(workflow, dicts):
    active = [d for d in dicts if not _is_terminal(d["state"])]
    if workflow == "next-issue":
        # A caller may explicitly focus backlog work.  Do not turn this into a
        # ready-only recommendation before the judgment skill sees the graph.
        return active
    if workflow == "blockers":
        # Proposed-ADR gates and dependency cycles need active context beyond
        # the already-blocked/WIP seeds.
        return active
    # Roadmap needs open work; groom and intake need the active index for triage.
    return active


def _closed_pages(seeds, all_dicts, limit):
    """Build atomic pages of transitive graph components.

    Components are transitive closures, and an oversize component is emitted whole
    with an explicit cap overrun.  Later pages are explicit, stable-snapshot reads.
    """
    by_id = {d["id"]: d for d in all_dicts}
    adjacent = {key: set() for key in by_id}
    for d in all_dicts:
        for link in d["links"]:
            target = link["target"]
            if target in by_id:
                adjacent[d["id"]].add(target)
                adjacent[target].add(d["id"])
    components, seen = [], set()
    for seed in seeds:
        component, todo = set(), [seed["id"]]
        # The full connected component preserves all direct graph facts. It also
        # makes a cross-project/missing target visible in links rather than guessing.
        while todo:
            current = todo.pop()
            if current in component:
                continue
            component.add(current)
            todo.extend(adjacent.get(current, ()) - component)
        frozen = frozenset(component)
        if frozen not in seen:
            seen.add(frozen)
            components.append(component)
    # Components are atomic. A component larger than the nominal page cap is emitted
    # alone: hiding a critical path would be worse than an explicitly reported cap
    # overrun. Later pages make every component reachable with the same snapshot.
    pages, current = [], set()
    for component in components:
        if current and len(current | component) > limit:
            pages.append(current)
            current = set()
        current |= component
        if len(current) >= limit:
            pages.append(current)
            current = set()
    if current:
        pages.append(current)
    return pages


def _slice(values, limit, page):
    """Return one deterministic bounded slice and its page count."""
    start = (page - 1) * limit
    return values[start:start + limit], (len(values) + limit - 1) // limit


def _section_meta(*, total, returned, limit, page, page_count, snapshot,
                  any_limit_exceeded=False):
    """Describe one response section against the same immutable snapshot."""
    return {
        "total_count": total,
        "returned_count": returned,
        "omitted_count": total - returned,
        "limit": limit,
        "page": page,
        "page_count": page_count,
        "next_page": page + 1 if page < page_count else None,
        "available": page < page_count,
        "limit_exceeded": returned > limit,
        "any_page_limit_exceeded": any_limit_exceeded,
        "snapshot_identity": snapshot["identity"],
    }


def _profile_issue(d):
    """Keep every list-level decision signal and omit only provider churn."""
    keys = ("id", "title", "state", "priority", "estimate", "milestone", "type",
            "labels", "ac_done", "ac_total", "links", "pr_url", "blocked_by",
            "unblocked", "unlocks", "unlocks_count", "ac_ratio", "rank_index",
            "created", "updated")
    return {key: d[key] for key in keys}


def _history_index(dicts):
    """Compact terminal recovery index for intake duplicate triage.

    It is deliberately not a substitute for issue drill-down: title/state identify a
    possible match, then the skill loads the body before deciding overlap.
    """
    return [{key: d[key] for key in ("id", "title", "state", "priority", "estimate",
                                     "milestone", "type", "ac_done", "ac_total")}
            for d in dicts if _is_terminal(d["state"])]


def _milestone_rollup(dicts):
    roll = {}
    for d in dicts:
        ms = d["milestone"] or "(sans milestone)"
        row = roll.setdefault(ms, {"done": 0, "open": 0, "est_done": 0.0,
                                   "est_open": 0.0, "open_ids": [],
                                   "blocked_ids": [], "wip_ids": []})
        resolved = _is_terminal(d["state"])
        row["done" if resolved else "open"] += 1
        row["est_done" if resolved else "est_open"] += d["estimate"] or 0
        if not resolved:
            row["open_ids"].append(d["id"])
            if d["blocked_by"]:
                row["blocked_ids"].append(d["id"])
            if d["state"] == "in-progress":
                row["wip_ids"].append(d["id"])
    for row in roll.values():
        total = row["done"] + row["open"]
        row["total"] = total
        row["pct"] = round(100 * row["done"] / total) if total else 0
    return roll


def _profile_milestone_rollup(dicts):
    """Milestone facts without duplicating active IDs already present in graph pages."""
    compact = {}
    for milestone, source in _milestone_rollup(dicts).items():
        row = {key: value for key, value in source.items()
               if key not in {"open_ids", "blocked_ids", "wip_ids"}}
        row["blocked"] = len(source["blocked_ids"])
        row["wip"] = len(source["wip_ids"])
        compact[milestone] = row
    return compact


_SELECTION_CRITERIA = {
    "next-issue": "all non-terminal issues (including backlog focus), ordered by deterministic rank",
    "roadmap": "all non-terminal issues plus complete historical milestone rollup, ordered by deterministic rank",
    "blockers": "all non-terminal issues, retaining proposed-ADR and dependency-gate context",
    "groom": "all non-terminal issues plus compact terminal estimate/status index for comparable-work triage",
    "intake": "all non-terminal issues plus compact terminal title/state recovery index for duplicate triage",
}


def profile(workflow: str, page: int = 1):
    """Bounded, provider-neutral graph for a judgment workflow.

    Bodies and comments are never present. Pagination is intentionally page-shaped
    even though each page is a closed graph projection: callers must ask explicitly
    for a later page or use issue/adr drill-down.
    """
    if workflow not in _PROFILE_LIMITS:
        raise SystemExit("profil inconnu : %s. %s" % (workflow, ", ".join(_PROFILE_LIMITS)))
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise SystemExit("page de profil invalide : entier positif attendu.")
    tr, p = foundry.tracker(), None
    p = _project(tr)
    all_dicts = _annotate(tr.search(p))
    seeds = sorted(_profile_seed(workflow, all_dicts),
                   key=lambda d: (d["rank_index"], d["id"]))
    limit = _PROFILE_LIMITS[workflow]
    graph_pages = _closed_pages(seeds, all_dicts, limit)
    selected = graph_pages[page - 1] if page <= len(graph_pages) else set()
    nodes = [d for d in all_dicts if d["id"] in selected]
    nodes.sort(key=lambda d: (d["rank_index"], d["id"]))
    snapshot = _snapshot(all_dicts)
    graph_total = len(set().union(*graph_pages)) if graph_pages else 0
    graph_overrun = any(len(values) > limit for values in graph_pages)

    history = (sorted(_history_index(all_dicts), key=lambda d: (d["id"], d["title"]))
               if workflow in {"groom", "intake"} else [])
    history_page, history_page_count = _slice(history, limit, page)

    milestone_rollup = (_profile_milestone_rollup(all_dicts)
                        if workflow == "roadmap" else {})
    milestone_items = sorted(milestone_rollup.items(),
                             key=lambda item: (item[0].casefold(), item[0]))
    milestone_page, milestone_page_count = _slice(milestone_items, limit, page)

    overall_page_count = max(1, len(graph_pages), history_page_count,
                             milestone_page_count)
    sections = {
        "issues": _section_meta(
            total=graph_total, returned=len(nodes), limit=limit, page=page,
            page_count=len(graph_pages), snapshot=snapshot,
            any_limit_exceeded=graph_overrun),
        "historical_index": _section_meta(
            total=len(history), returned=len(history_page), limit=limit, page=page,
            page_count=history_page_count, snapshot=snapshot),
        "milestones": _section_meta(
            total=len(milestone_items), returned=len(milestone_page), limit=limit,
            page=page, page_count=milestone_page_count, snapshot=snapshot),
    }
    return {
        "contract": PROFILE_CONTRACT_VERSION,
        "profile": workflow,
        "project": p.key,
        "tracker": tr.name,
        "snapshot": snapshot,
        "selection": {
            "criteria": _SELECTION_CRITERIA[workflow],
            "closure": "complete undirected connected components over normalized links",
            "limit": limit,
            "page": page,
            "snapshot_total_count": len(all_dicts),
            "selected_issue_count": graph_total,
            "excluded_issue_count": len(all_dicts) - graph_total,
            "excluded_issue_retrieval": "query backlog",
        },
        "total_count": len(all_dicts),
        "returned_count": len(nodes),
        "omitted_count": len(all_dicts) - len(nodes),
        "pagination": {"requested_page": page,
                       "next_page": page + 1 if page < overall_page_count else None,
                       "available": page < overall_page_count,
                       "page_count": overall_page_count,
                       "snapshot_identity": snapshot["identity"]},
        "sections": sections,
        "truncated": False,
        "truncation": {"reason": None, "skipped_seed_ids": []},
        "limit_exceeded": graph_overrun,
        "limit_note": "a complete component exceeded the nominal page limit"
                      if graph_overrun else None,
        "issues": [_profile_issue(d) for d in nodes],
        **({"milestones": dict(milestone_page),
            "milestones_note": "Milestone ID lists are derived from all issues pages; rows carry counts only."}
           if workflow == "roadmap" else {}),
        **({"historical_index": history_page,
            "historical_index_note": "Paginated compact terminal recovery index; load query issue <ID> before a quality or duplicate decision."}
           if workflow in {"groom", "intake"} else {}),
        "note": "Bounded normalized graph only; bodies require query issue <ID>, "
                "ADR bodies require query adr <ADR-ID>. The agent keeps judgment.",
    }


def backlog(status: str | None = None):
    tr = foundry.tracker()
    p = _project(tr)
    issues = tr.search(p)
    # annotate over the FULL set, filter after: blocked_by must see targets whose
    # state falls outside the filter (a `ready` issue blocked by an `in-progress` one).
    dicts = _annotate(issues)
    if status:
        dicts = [d for d in dicts if d["state"] == status]
    # rank_index is GLOBAL (whole backlog) so a filtered view may have gaps and no 0;
    # rank_in_view is dense 0..n-1 over exactly what this response returns
    order = sorted(range(len(dicts)), key=lambda k: dicts[k]["rank_index"])
    for rank, k in enumerate(order):
        dicts[k]["rank_in_view"] = rank
    return {"project": p.key, "tracker": tr.name, "count": len(dicts),
            "note": "Lean list: issue bodies live in `query issue <ID>`. rank_index = "
                    "deterministic prior (priority→estimate→created) over the WHOLE "
                    "backlog (may have gaps in a filtered view); rank_in_view = the "
                    "same prior, dense 0..n-1 within this response. Both are priors "
                    "to override with a reason when the graph says smarter.",
            "issues": [_lean(d) for d in dicts]}


def candidates(status: str = "ready"):
    """Issues eligible to start next — lean list (fields, links, AC counts, ranks);
    the chosen issue's full text comes via `query issue <ID>`."""
    return backlog(status)


def milestones():
    tr = foundry.tracker()
    p = _project(tr)
    issues = tr.search(p)
    return {"project": p.key, "tracker": tr.name, "milestones": _milestone_rollup(_annotate(issues)),
            "note": "Reason about risk/trajectory, not just %: WIP, blocked, remaining estimate."}


def _adr_index(tr, p):
    """id / title / status / ref of every ADR — the body only via `query adr <ID>`."""
    return [{k: v for k, v in a.to_dict().items() if k != "body"}
            for a in tr.list_adrs(p)]


def _adr_index_or_capability(tr, p):
    """Keep an issue readable when its tracker has no ADR knowledge base, or when the
    embedded ADR index itself is in conflict.

    Only the provider's typed capability and conflict errors are projected — each as
    its own explicit, distinct status, never as an empty list and never conflated with
    each other. Transport, binding and payload failures still propagate rather than
    being mistaken for either. `query adr`, `query adrs`, `frame` and ADR writes stay
    fail-closed on the same conflict — this projection only keeps the issue payload
    readable; it does not repair or normalize the conflict.
    """
    try:
        return _adr_index(tr, p)
    except TrackerCapabilityUnavailableError as exc:
        return {
            "status": "unavailable",
            "tracker": exc.tracker,
            "capability": exc.capability,
        }
    except TrackerConflictError as exc:
        return {
            "status": "conflict",
            "tracker": tr.name,
            "reason": str(exc),
        }


def issue(issue_id: str):
    tr = foundry.tracker()
    p = _project(tr)
    it = tr.get_issue(issue_id)
    # the requested issue keeps its body + notes; related issues stay lean.
    # Dedupe targets (two links to the same issue = one fetch) and tolerate
    # unavailable ones (deleted / cross-project / forbidden) — backlog tolerates
    # them too. Other failures must remain visible to the caller.
    related = {}
    for lk in it.links:
        if lk.target in related or lk.target == it.id:
            continue
        try:
            related[lk.target] = _lean(tr.get_issue(lk.target).to_dict())
        except IssueUnavailableError:
            related[lk.target] = {"id": lk.target, "error": "issue unavailable"}
    return {"project": p.key, "issue": it.to_dict(), "related": related,
            "note": "adrs is an INDEX when the provider supports an ADR knowledge "
                    "base; otherwise it is a typed capability status, or — if the "
                    "embedded ADR index itself is in conflict — a typed conflict "
                    "status ({\"status\": \"conflict\", ...}). A conflict here does "
                    "not clear on read: `query adr`, `query adrs` and ADR writes stay "
                    "fail-closed on it. Load the full text of constraining ADRs with "
                    "`query adr <ADR-ID>` when available.",
            "adrs": _adr_index_or_capability(tr, p)}


def adrs(adr_id=None):
    """ADR index — retrieval-before-reasoning: scan this BEFORE reopening a decision,
    then load the full text of the relevant ones with `query adr <ID>`. Given an id
    (`query adrs X-ADR-0001`, the natural near-miss of `adr`), delegate to it."""
    if adr_id:
        return adr(adr_id)
    tr = foundry.tracker()
    p = _project(tr)
    return {"project": p.key,
            "note": "Treat `accepted` ADRs as settled. This is an INDEX: load the full "
                    "text of the ADRs touching your topic with `query adr <ADR-ID>`, "
                    "cite one to close a reopened debate, supersede explicitly if it "
                    "must change.",
            "adrs": _adr_index(tr, p)}


def adr(adr_id: str):
    """One full ADR (the drill-down of the `adrs` index)."""
    tr = foundry.tracker()
    p = _project(tr)
    everything = tr.list_adrs(p)  # one fetch, reused by the error path
    match = next((a for a in everything if a.id == adr_id), None)
    if not match:
        known = ", ".join(a.id for a in everything) or "(aucun)"
        raise SystemExit(f"ADR introuvable : {adr_id}. Connus : {known}.")
    return {"project": p.key, "adr": match.to_dict()}


def changelog(milestone: str):
    """Shipped issues of a milestone, grouped by type — the SOURCE of release notes.

    ZERO decisions, ZERO prose, and deliberately LOCALE-NEUTRAL: it emits raw facts
    (id, title, type, labels) only. Turning these into user-facing release notes — in
    French, English, Spanish, whatever locales you ship — is the release tool's job,
    never Foundry's. Only `done` counts (a `dropped` issue never shipped). Platform-
    neutral: any project cutting a version wants "what landed in vX".
    """
    # guard the empty/None milestone: without it, milestone=None would match every
    # done issue whose Milestone field is unset — a silent wrong "release"
    if not milestone:
        raise SystemExit("usage: query changelog <MILESTONE>")
    tr = foundry.tracker()
    p = _project(tr)
    shipped = [i for i in tr.search(p)
               if i.state == "done" and i.milestone == milestone]
    groups: dict[str, list] = {}
    for i in sorted(shipped, key=lambda x: (x.type or "~", x.id)):
        groups.setdefault(i.type or "(sans type)", []).append(
            {"id": i.id, "title": i.title, "labels": i.labels})
    return {"project": p.key, "milestone": milestone, "count": len(shipped),
            "note": "Locale-neutral facts: the `done` issues of this milestone, grouped "
                    "by type (only shipped work — `dropped` is excluded, unlike the "
                    "`milestones` rollup which counts it as resolved). A release tool "
                    "rewrites titles into user-facing notes PER target locale and drops "
                    "internal-only items (chore/test) as needed. count 0 with groups {} "
                    "= nothing shipped under that exact milestone name (check spelling).",
            "groups": groups}


_CMDS = {"backlog": backlog, "candidates": candidates, "milestones": milestones,
         "issue": issue, "adrs": adrs, "adr": adr, "changelog": changelog}
_CMDS["profile"] = profile

_REQUIRE_ARG = {"issue": "<ISSUE-ID>", "adr": "<ADR-ID>", "changelog": "<MILESTONE>",
                "profile": "<next-issue|roadmap|blockers|groom|intake>"}


def serialize_stdout(value: object) -> str:
    """Canonical CLI serialization used by execution and measurement.

    New bounded profiles use compact JSON to avoid spending model context on
    indentation. Existing commands retain their established pretty-printed output.
    """
    if isinstance(value, dict) and value.get("contract") == PROFILE_CONTRACT_VERSION:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backlog"
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    fn = _CMDS.get(cmd)
    if not fn:
        raise SystemExit(f"commande query inconnue : {cmd}. {', '.join(_CMDS)}")
    if arg is None and cmd in _REQUIRE_ARG:
        raise SystemExit(f"usage: query {cmd} {_REQUIRE_ARG[cmd]}")
    if cmd == "profile" and len(sys.argv) > 3:
        try:
            value = fn(arg, int(sys.argv[3]))
        except ValueError:
            raise SystemExit("page de profil invalide : entier positif attendu.") from None
    else:
        value = fn(arg) if arg is not None else fn()
    sys.stdout.write(serialize_stdout(value))
