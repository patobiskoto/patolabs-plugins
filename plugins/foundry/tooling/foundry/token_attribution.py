"""Offline attachment of host-log token slices to a Foundry issue and role (PLP-317).

Read-only and replayable: the same logs and the same local invocation record give the
same result. No network, no tracker write, no clock, no routing or gate input. Tokens come
only from ``cost_attribution.read_host_log`` (FOUNDRY-ADR-0015, ``timestamps=True`` adds
the per-request timestamp). Beside them the module extracts only closed-vocabulary
identifiers: an issue id, a role, a Foundry invocation id, a timestamp. No prompt, path,
command, host session id or secret is retained or emitted.

Methods, strongest first:

* ``exact``: a subagent log whose host-visible id (Claude ``toolUseId`` of its ``.meta.json``,
  hashed with ``invocation_log.claude_invocation_id``; Codex ``agent_path`` suffix, the
  16-hex execution id) is in the local invocation record, which carries the issue.
* ``window``: coordinator requests inside exactly one ``issue start`` -> first later
  ``issue merge`` window of the same coordinator log (the Bash/exec tool calls).
* ``approximate``: requests of a subagent without invocation record that fall inside exactly
  one window of its parent coordinator log (history before the record existed).
* ``overlap``: requests inside two or more windows; reported under the issue set, never split.
* ``unattributed``: everything else, with a ``reason``.

A role comes from the invocation record, else from the Foundry packet header of a Claude
subagent (``Resolved role:``), else from the default Codex agent name; otherwise ``null``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from foundry import invocation_log
from foundry.cost_attribution import BILLING_TOKEN_KEYS, CostAttributionError, read_host_log
from foundry.routing_facades import AGENT_IDENTITIES

SCHEMA = "token-attribution.v1"
METHODS = ("exact", "window", "approximate", "overlap", "unattributed")
SCOPE = "all logs passed; windows are per coordinator log and a window needs both its start and its merge call"
_ISSUE = r"[A-Z][A-Z0-9_]*-[1-9][0-9]*"
_WINDOW_CALL = re.compile(r"foundry_cli\.py[\"'\\]*\s+issue\s+(start|merge)\s+(" + _ISSUE + r")(?![A-Za-z0-9_-])")
_ROLE_LINE = re.compile(r"Resolved role: (architect|implementer|reviewer|scout)\Z")
_TOOL_USE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CODEX_SUFFIX = re.compile(r"(.*)_([0-9a-f]{16})\Z")
_ROLES = tuple(sorted(AGENT_IDENTITIES))
_CODEX_NAMES = {f"foundry_{identity.lower()}": role for role, identity in AGENT_IDENTITIES.items()}


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _json_lines(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                yield item


def _calls(host: str, path: Path) -> list[tuple[datetime, str, str]]:
    """``(time, start|merge, issue)`` of the coordinator's own issue start/merge calls."""
    found = []
    for item in _json_lines(path):
        when, texts = _time(item.get("timestamp")), []
        if when is None:
            continue
        if host == "claude" and item.get("type") == "assistant":
            message = item.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            for block in blocks if isinstance(blocks, list) else ():
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") == "Bash" and isinstance(block.get("input"), dict)
                        and isinstance(block["input"].get("command"), str)):
                    texts.append(block["input"]["command"])
        elif host == "codex" and item.get("type") == "response_item":
            payload = item.get("payload")
            if isinstance(payload, dict) and payload.get("type") in {"function_call", "custom_tool_call"}:
                texts.extend(x for x in (payload.get("arguments"), payload.get("input")) if isinstance(x, str))
        for text in texts:
            found.extend((when, m.group(1), m.group(2)) for m in _WINDOW_CALL.finditer(text))
    return found


def _windows(calls: list[tuple[datetime, str, str]]) -> list[dict]:
    """One window per issue: first start, first later merge (``end`` None when unclosed)."""
    result = []
    for issue in sorted({c[2] for c in calls}):
        mine = sorted((c for c in calls if c[2] == issue), key=lambda c: (c[0], c[1] != "start"))
        starts = [c[0] for c in mine if c[1] == "start"]
        if not starts:
            continue
        ends = [c[0] for c in mine if c[1] == "merge" and c[0] >= starts[0]]
        result.append({"issue": issue, "start": starts[0], "end": ends[0] if ends else None})
    return result


def _classify(when: datetime | None, windows: list[dict]) -> tuple[str, object]:
    """('one', issue) | ('overlap', issues) | ('none', reason)."""
    if when is None:
        return "none", "no_request_timestamp"
    inside = sorted(w["issue"] for w in windows
                    if w["end"] is not None and w["start"] <= when <= w["end"])
    # An unclosed window may still be open: it is never ignored, only never trusted alone.
    still_open = sorted(w["issue"] for w in windows if w["end"] is None and w["start"] <= when)
    if len(inside) == 1 and not still_open:
        return "one", inside[0]
    if inside and (len(inside) > 1 or still_open):
        return "overlap", sorted(inside + still_open)
    if still_open:
        return "none", "window_unclosed"
    return "none", "no_window"


def _meta_id(path: Path) -> str | None:
    try:
        meta = json.loads(path.with_name(path.stem + ".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tool_use = meta.get("toolUseId") if isinstance(meta, dict) else None
    if isinstance(tool_use, str) and _TOOL_USE_ID.fullmatch(tool_use):
        return invocation_log.claude_invocation_id(tool_use)
    return None


def _packet_role(path: Path) -> str | None:
    """Role token of the Foundry routed packet header; nothing else of the prompt is kept."""
    try:
        first = next(_json_lines(path), None)
    except (OSError, UnicodeDecodeError):
        return None
    message = first.get("message") if first else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        content = next((b.get("text") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)), None)
    if not isinstance(content, str):
        return None
    lines = content.split("\n", 2)
    match = _ROLE_LINE.fullmatch(lines[1]) if len(lines) > 1 and lines[0] == "FOUNDRY_ROUTED_AGENT_V1" else None
    return match.group(1) if match else None


def _codex_meta(path: Path) -> dict | None:
    """Closed subset of the first ``session_meta``: id, parent id, agent name parts."""
    try:
        first = next(_json_lines(path), None)
    except (OSError, UnicodeDecodeError):
        return None
    payload = first.get("payload") if first and first.get("type") == "session_meta" else None
    if not isinstance(payload, dict):
        return None
    sid = payload.get("session_id", payload.get("id"))
    parent = payload.get("parent_thread_id")
    agent_path = payload.get("agent_path")
    leaf = agent_path.rsplit("/", 1)[-1] if isinstance(agent_path, str) else None
    split = _CODEX_SUFFIX.fullmatch(leaf) if leaf else None
    return {"id": sid if isinstance(sid, str) else None,
            "parent": parent if isinstance(parent, str) else None,
            "subagent": payload.get("thread_source") == "subagent" or parent is not None,
            "prefix": split.group(1) if split else None,
            "execution_id": split.group(2) if split else None}


def _claude_units(paths: list[str]) -> list[dict]:
    units, seen = [], set()
    for raw in paths:
        main = Path(raw)
        if main not in seen:
            seen.add(main)
            units.append({"host": "claude", "path": main, "parent": str(main), "subagent": False})
        for sub in sorted((main.parent / main.stem / "subagents").rglob("*.jsonl")):
            if sub not in seen:
                seen.add(sub)
                units.append({"host": "claude", "path": sub, "parent": str(main), "subagent": True,
                              "invocation_id": _meta_id(sub), "header_role": _packet_role(sub)})
    return units


def _codex_units(paths: list[str]) -> list[dict]:
    units, seen = [], set()
    for raw in paths:
        path = Path(raw)
        if path in seen:
            continue
        seen.add(path)
        meta = _codex_meta(path) or {"id": None, "parent": None, "subagent": False,
                                     "prefix": None, "execution_id": None}
        units.append({"host": "codex", "path": path, "subagent": meta["subagent"],
                      "key": meta["id"] or str(path), "parent": meta["parent"],
                      "invocation_id": meta["execution_id"], "name": meta["prefix"]})
    return units


def report(*, claude_sessions=(), codex_sessions=(), root=None, repository=None,
           state_dir=None) -> dict:
    """Attach every readable token slice of the given logs; see the module docstring."""
    records = invocation_log.records_by_id(root=root, repository=repository, state_dir=state_dir)
    units = _claude_units([str(p) for p in claude_sessions]) + _codex_units([str(p) for p in codex_sessions])
    unreadable, rows_of, lost_parents = {"coordinator": 0, "subagent": 0}, {}, set()
    for index, unit in enumerate(units):
        try:
            rows_of[index] = read_host_log(unit["host"], unit["path"], timestamps=True)
        except (CostAttributionError, OSError, UnicodeDecodeError):
            unreadable["subagent" if unit["subagent"] else "coordinator"] += 1
            if not unit["subagent"]:
                lost_parents.add((unit["host"], unit["parent"] if unit["host"] == "claude" else unit["key"]))
    # Windows are per coordinator log, keyed by what its subagents name as parent.
    windows: dict[tuple[str, str], list[dict]] = {}
    for index, unit in enumerate(units):
        if unit["subagent"] or index not in rows_of:
            continue
        key = (unit["host"], unit["parent"] if unit["host"] == "claude" else unit["key"])
        try:
            windows[key] = _windows(_calls(unit["host"], unit["path"]))
        except (OSError, UnicodeDecodeError):
            windows[key] = []
    slices: dict[tuple, dict] = {}

    def add(row_tokens: dict, requests: int, *, issue, method, role, role_source, host, scope,
            reason=None, issues=None) -> None:
        key = (method, issue or "", role or "", role_source or "", host, scope, reason or "",
               tuple(issues or ()))
        entry = slices.setdefault(key, {
            "issue": issue, "method": method, "role": role, "role_source": role_source,
            "host": host, "scope": scope, "reason": reason, "issues": list(issues) if issues else None,
            "requests": 0, "tokens": dict.fromkeys(BILLING_TOKEN_KEYS, 0)})
        entry["requests"] += requests
        for name in BILLING_TOKEN_KEYS:
            entry["tokens"][name] += row_tokens[name]

    for index, unit in enumerate(units):
        host, subagent = unit["host"], unit["subagent"]
        role = role_source = record = None
        if subagent:
            record = (records or {}).get(unit.get("invocation_id") or "")
            if record is not None and record.get("host") != host:
                record = None
            if record is not None and record.get("role") in _ROLES:
                role, role_source = record["role"], "invocation_record"
            elif host == "claude" and unit.get("header_role"):
                role, role_source = unit["header_role"], "packet_header"
            elif host == "codex" and unit.get("name") in _CODEX_NAMES:
                role, role_source = _CODEX_NAMES[unit["name"]], "agent_name"
        scope = "subagent" if subagent else "coordinator"
        common = {"role": role, "role_source": role_source, "host": host, "scope": scope}
        parent_windows = windows.get((host, unit["parent"] or "")) if subagent else None
        if not subagent:
            parent_windows = windows.get((host, unit["parent"] if host == "claude" else unit["key"]), [])
        for row in rows_of.get(index, ()):
            if "tokens" not in row:
                continue
            if record is not None:
                add(row["tokens"], len(row["token_requests"]), issue=record["issue"], method="exact", **common)
                continue
            requests, times = row["token_requests"], row.get("request_at", [])
            for position, tokens in enumerate(requests):
                when = _time(times[position]) if len(times) == len(requests) else None
                kind, value = _classify(when, parent_windows or [])
                if kind == "one":
                    add(tokens, 1, issue=value, method="approximate" if subagent else "window", **common)
                elif kind == "overlap":
                    add(tokens, 1, issue=None, method="overlap", issues=value, **common)
                else:
                    if value == "no_window" and subagent and (host, unit["parent"] or "") in lost_parents:
                        value = "parent_unreadable"
                    add(tokens, 1, issue=None, method="unattributed", reason=value, **common)
    ordered = [slices[k] for k in sorted(slices, key=lambda k: (METHODS.index(k[0]),) + k[1:])]
    totals = {"requests": sum(s["requests"] for s in ordered),
              "tokens": {n: sum(s["tokens"][n] for s in ordered) for n in BILLING_TOKEN_KEYS}}
    listed = []
    for (host, _), items in sorted(windows.items(), key=lambda kv: kv[0]):
        for w in items:
            others = sorted(o["issue"] for o in items if o is not w
                            and (o["end"] is None or w["start"] <= o["end"])
                            and (w["end"] is None or o["start"] <= w["end"]))
            listed.append({"host": host, "issue": w["issue"], "closed": w["end"] is not None,
                           "start": w["start"].isoformat().replace("+00:00", "Z"),
                           "end": w["end"].isoformat().replace("+00:00", "Z") if w["end"] else None,
                           "overlaps_with": others})
    listed.sort(key=lambda w: (w["issue"], w["host"], w["start"]))
    return {"schema": SCHEMA, "scope": SCOPE,
            "invocation_log": "unreadable" if records is None else "read",
            "invocation_records": None if records is None else len(records),
            "unreadable_logs": unreadable, "windows": listed, "slices": ordered, "totals": totals}
