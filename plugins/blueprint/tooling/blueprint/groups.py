"""Candidate screens and features from an inventory, offline and deterministic (PAT-253).

Only the inventory is read; no REA call. A type is tied to a group by the role suffix of its
name (`SUFFIX_ROLES`); a UI string by a case-insensitive occurrence of the group name. What
cannot be tied goes to the explicit `ungrouped` bucket with the reason: nothing is dropped, and
`accounting` lets a reader check that the string counts add up.
"""
from __future__ import annotations

import json
import re

from . import inventory

FORMAT = 1
MAX_GROUP_STRINGS = 40
MAX_UNGROUPED_STRINGS = 200
MIN_STEM_FOR_STRINGS = 4
MIN_STRING = 2
MAX_STRING = 120

# (suffix, role): longest suffix first, so `ViewModel` wins over `View`.
SUFFIX_ROLES = (("ViewModel", "screen"), ("Coordinator", "feature"), ("Controller", "screen"),
                ("Repository", "feature"), ("Provider", "feature"), ("Manager", "feature"),
                ("Service", "feature"), ("Screen", "screen"), ("Client", "feature"),
                ("Store", "feature"), ("View", "screen"))


class GroupsError(Exception):
    pass


def split_swift_class(name: str) -> tuple[str, str] | None:
    """(module, class) of a plain Objective-C runtime name `_TtC<len><module><len><class>`; None for
    anything else (nested, private or generic types are not parsed)."""
    if not name.startswith("_TtC"):
        return None
    rest, parts = name[4:], []
    while rest and len(parts) < 2:
        m = re.match(r"(\d+)", rest)
        if not m:
            return None
        n, rest = int(m.group(1)), rest[m.end():]
        if n == 0 or n > len(rest):
            return None
        parts.append(rest[:n])
        rest = rest[n:]
    return (parts[0], parts[1]) if len(parts) == 2 and not rest else None


def role_of(name: str) -> tuple[str, str] | None:
    """(stem, role) when the name ends with a role suffix and keeps a non-empty stem."""
    for suffix, role in SUFFIX_ROLES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], role
    return None


def human_string(value: str) -> bool:
    """A string a person could read on a screen; symbol names, paths and keys are not."""
    if not (MIN_STRING <= len(value) <= MAX_STRING) or not value.isprintable():
        return False
    if value[0] in "_@/$%.<" or any(c in value for c in "@$^{}\\") or "/" in value or value.endswith(".swift"):
        return False
    if " " not in value and re.fullmatch(r"[A-Za-z0-9_.]+", value) and (
            re.search(r"[a-z][A-Z]|[0-9_]", value)):
        return False  # a camelCase or digit-bearing identifier, not a label
    return any(c.isalpha() for c in value) and (" " in value or value[0].isupper() or not value.isascii())


def _section(inv: dict, key: str) -> dict:
    s = inv.get(key)
    if not isinstance(s, dict) or not isinstance(s.get("items"), list) or s.get("status") not in ("ok", "unknown"):
        raise GroupsError(f"the inventory has no readable `{key}` section")
    return s


def _uniq(evs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in evs:
        k = json.dumps(e, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return sorted(out, key=lambda e: json.dumps(e, sort_keys=True))


def build(inv: dict) -> dict:
    if inv.get("format") != inventory.FORMAT or not isinstance(inv.get("target"), dict):
        raise GroupsError("not a blueprint inventory (unexpected format)")
    types, strings = _section(inv, "types"), _section(inv, "ui_strings")
    unknown_parts = {k: s.get("reason") or "unknown" for k, s in (("types", types), ("ui_strings", strings))
                     if s["status"] != "ok"}

    if strings.get("truncated"):
        unknown_parts["ui_strings_truncated"] = "the inventory kept only part of the strings"
    # One entry per type name: Swift conformance and Objective-C class records of one type are merged.
    by_name: dict[str, dict] = {}
    ungrouped_types: list[dict] = []
    for it in types["items"]:
        kind, name = it.get("kind"), it.get("name")
        if not isinstance(name, str) or not isinstance(it.get("evidence"), dict):
            ungrouped_types.append({"name": str(name), "kind": str(kind), "reason": "unreadable record",
                                    "evidence": it.get("evidence")})
            continue
        module = None
        if kind == "objc-class":
            parsed = split_swift_class(name)
            if parsed is None:
                ungrouped_types.append({"name": name, "kind": kind, "evidence": it["evidence"],
                                        "reason": "nested, private or non-Swift class name"})
                continue
            module, name = parsed
        elif kind == "objc-protocol":
            ungrouped_types.append({"name": name, "kind": kind, "evidence": it["evidence"],
                                    "reason": "protocol: not tied to a screen or feature by its name"})
            continue
        t = by_name.setdefault(name, {"name": name, "module": "unknown", "kinds": set(), "protocols": set(),
                                      "evidence": []})
        if module:
            t["module"] = module
        t["kinds"].add(kind)
        if it.get("protocol"):
            t["protocols"].add(it["protocol"])
        t["evidence"].append(it["evidence"])

    groups: dict[str, dict] = {}
    for name in sorted(by_name):
        t = by_name[name]
        member = {"name": name, "module": t["module"], "kinds": sorted(t["kinds"]),
                  "protocols": sorted(t["protocols"]), "evidence": _uniq(t["evidence"])}
        r = role_of(name)
        if r is None:
            ungrouped_types.append({"name": name, "kind": "type", "evidence": member["evidence"],
                                    "reason": "no screen or feature role suffix in the name"})
            continue
        stem, role = r
        g = groups.setdefault(stem, {"name": stem, "kind": "feature", "members": []})
        if role == "screen":
            g["kind"] = "screen"
        g["members"].append(member)

    # Strings: the longest group name found in the string wins; ties go to the first name in order.
    stems = sorted((s for s in groups if len(s) >= MIN_STEM_FOR_STRINGS), key=lambda s: (-len(s), s))
    assigned: dict[str, list[dict]] = {s: [] for s in groups}
    ungrouped_strings: list[dict] = []
    not_user_facing = 0
    for it in strings["items"]:
        v = it.get("value") if isinstance(it, dict) else None
        if not isinstance(v, str) or not human_string(v):
            not_user_facing += 1
            continue
        low = v.lower()
        hit = next((s for s in stems if s.lower() in low), None)
        rec = {"value": v, "evidence": it.get("evidence")}
        (assigned[hit] if hit else ungrouped_strings).append(rec)

    out_groups = []
    for stem in sorted(groups):
        g = groups[stem]
        classes = sorted(m["name"] for m in g["members"] if m["module"] != "unknown")
        modules = sorted({m["module"] for m in g["members"] if m["module"] != "unknown"})
        hint = ({"module": modules[0], "classes": classes} if len(modules) == 1 else
                {"unknown": "no class of this group has a known module"
                 if not modules else "classes of several modules"})
        ss = sorted(assigned[stem], key=lambda r: r["value"])
        out_groups.append({**g, "search_hint": hint,
                           "ui_strings": {"total": len(ss), "items": ss[:MAX_GROUP_STRINGS],
                                          "truncated": len(ss) > MAX_GROUP_STRINGS}})
    us = sorted(ungrouped_strings, key=lambda r: r["value"])
    ungrouped_types.sort(key=lambda r: (r["kind"], r["name"]))
    return {
        "format": FORMAT,
        "source": {k: inv["target"].get(k) for k in ("name", "sha256", "arm64_sha256", "analysed_binary")},
        "unknown_parts": unknown_parts,
        "groups": out_groups,
        "ungrouped": {"types": ungrouped_types,
                      "ui_strings": {"total": len(us), "items": us[:MAX_UNGROUPED_STRINGS],
                                     "truncated": len(us) > MAX_UNGROUPED_STRINGS}},
        "accounting": {
            "ui_strings_in_inventory": len(strings["items"]),
            "ui_strings_in_groups": sum(g["ui_strings"]["total"] for g in out_groups),
            "ui_strings_ungrouped": len(us),
            "ui_strings_not_user_facing": not_user_facing,
            "note": "in_groups + ungrouped + not_user_facing = in_inventory; not_user_facing are symbol names, paths, keys and fragments "
                    "(heuristic `human_string`), kept in the inventory only",
        },
    }


def find(doc: dict, name: str) -> dict | None:
    return next((g for g in doc["groups"] if g["name"] == name), None)
