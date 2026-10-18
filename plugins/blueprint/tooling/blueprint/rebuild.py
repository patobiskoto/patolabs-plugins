"""Rebuild-grade analysis: measured coverage, constants, persistent data model, resources, screens (PAT-260).

Offline: reads the analysis document (`analysis.build`), the inventory and, optionally, the normalised screens document
(`screens.build`). Nothing is launched. Every number is a count over what those files hold; what they do not hold is
listed in `coverage.not_covered`, never silently dropped.
"""
from __future__ import annotations

import re

from . import analysis, constants, groups, inventory, resources

FORMAT = 1
MODEL_MARKER_FIELD = "_$backingData"  # field added by the SwiftData @Model macro (observed on the 2026-10-18 target)
_GETTER = re.compile(r"^(?:\w+)\.(\w+)\.(\w+)\.getter : (.+)$")
_MIGRATION = re.compile(r"Schema|Migration")


def detect_groups(an: dict) -> dict[str, dict]:
    """Groups by role-suffix stem over the Swift types of the image, with their function counts."""
    found: dict[str, dict] = {}
    for t in an["swift_types"]:
        name = t.get("name")
        r = groups.role_of(name) if isinstance(name, str) and not name.startswith("$") else None
        if r is None:
            continue
        g = found.setdefault(r[0], {"name": r[0], "kind": "feature", "types": [], "functions": 0, "analysed": 0})
        if r[1] == "screen":
            g["kind"] = "screen"
        if name not in g["types"]:
            g["types"].append(name)
    stems = sorted(found)
    for e in an["entries"]:
        r = groups.role_of(e["owner"]) if e.get("owner") else None
        stem = r[0] if r and r[0] in found else extension_group(e, stems)
        if stem:
            g = found.setdefault(stem, {"name": stem, "kind": "feature", "types": [], "functions": 0, "analysed": 0,
                                        "extension_only": True})
            g["functions"] += 1
            g["analysed"] += e["status"] == "ok"
    for g in found.values():
        g["types"].sort()
    return found


def extension_group(e: dict, stems: list[str]) -> str | None:
    """The group of a function defined in the app's extension of another module's type: the related group stem
    (`groups.extension_stem`), else the first type named by its generic requirements (`Array where Element == T` joins
    the group `T`), else `groups.EXTENSIONS_GROUP`; None for any other function."""
    ext = e.get("extension")
    if e.get("owner") or not ext:
        return None
    return groups.extension_stem(ext, stems) or next(iter(ext.get("related_types") or []), None) \
        or groups.EXTENSIONS_GROUP


def extension_counts(an: dict, found: dict[str, dict]) -> dict:
    """Functions of the app's extensions of other modules' types: detected, analysed, per group they were attributed to."""
    ext = [e for e in an["entries"] if not e.get("owner") and e.get("extension")]
    stems = sorted(n for n, g in found.items() if not g.get("extension_only"))
    per: dict[str, int] = {}
    for e in ext:
        g = extension_group(e, stems)
        per[g] = per.get(g, 0) + 1
    return {"detected": len(ext), "analysed": sum(e["status"] == "ok" for e in ext),
            "in_related_group": sum(n for g, n in per.items() if g != groups.EXTENSIONS_GROUP),
            "in_extensions_group": per.get(groups.EXTENSIONS_GROUP, 0), "per_group": dict(sorted(per.items()))}


def _count(detected: list, analysed: list) -> dict:
    return {"detected": len(detected), "analysed": len(analysed),
            "not_analysed": sorted(set(detected) - set(analysed))}


def coverage(an: dict, found: dict[str, dict], scr: dict | None, consts: dict, res: dict) -> dict:
    allg = sorted(found)
    screens = sorted(n for n, g in found.items() if g["kind"] == "screen")
    ok_g = [n for n in allg if found[n]["analysed"] > 0]
    ok_s = [n for n in screens if found[n]["analysed"] > 0]
    entries = an["entries"]
    fn_ok = [e for e in entries if e["status"] == "ok"]
    f = an["functions"]
    cap = captured_screens(found, scr)
    c = {"groups": _count(allg, ok_g), "screens": _count(screens, ok_s),
         "functions": {"detected": len(entries), "analysed": len(fn_ok),
                       "failed": len(entries) - len(fn_ok), "truncated": f["truncated"],
                       "binary_total": f["total"], "outside_app_module": (f["total"] or 0) - len(entries),
                       "in_extensions_of_other_modules_types": extension_counts(an, found)},
         "screens_captured": {"detected": len(screens), "captured": len(cap),
                              "not_captured": sorted(set(screens) - set(cap))},
         "timing": an["timing"]}
    nc = []
    for kind in ("groups", "screens"):
        if c[kind]["not_analysed"]:
            nc.append(f"{kind} with no analysed function: " + ", ".join(c[kind]["not_analysed"]))
    if c["functions"]["failed"]:
        nc.append(f"{c['functions']['failed']} app function(s) not decompiled")
    if c["functions"]["truncated"]:
        nc.append(f"{c['functions']['truncated']} pseudo-code text(s) cut at {analysis.MAX_CODE_CHARS} characters")
    if c["functions"]["outside_app_module"]:
        nc.append(f"{c['functions']['outside_app_module']} function(s) of the binary are outside the app module "
                  "(frameworks glue, Objective-C bridges, runtime) and are not analysed")
    ext = c["functions"]["in_extensions_of_other_modules_types"]
    nc.append(f"{ext['detected']} function(s) defined in the app's extensions of types of another module are exported and "
              f"counted in `functions.in_extensions_of_other_modules_types` ({ext['analysed']} decompiled); a symbol whose extension "
              "context does not read `<extended type><module>[<requirements>]E` is not recognised and stays in the outside-app-module count")
    if c["screens_captured"]["not_captured"]:
        nc.append("screens without a captured screenshot: " + ", ".join(c["screens_captured"]["not_captured"]))
    if consts["unknown_count"]:
        nc.append(f"{consts['unknown_count']} static constant(s) with an unknown value")
    unk_res = sum(1 for r in res["items"] if r["usage"] == "unknown")
    if unk_res:
        nc.append(f"{unk_res} bundle file(s) with an unknown usage")
    nc.append("static analysis only: behaviour, timings at run time and navigation are not observed unless a screen "
              "capture says so")
    for k, why in an["unknown_parts"].items():
        nc.append(f"{k}: {why}")
    c["not_covered"] = nc
    return c


def captured_screens(found: dict[str, dict], scr: dict | None) -> dict[str, dict]:
    """Detected screen name -> capture, matching the manifest name to the group stem or one of its type names."""
    out: dict[str, dict] = {}
    for cap in (scr or {}).get("screens") or []:
        want = cap["name"].lower()
        for n, g in found.items():
            if g["kind"] == "screen" and want in {n.lower(), *(t.lower() for t in g["types"])}:
                out.setdefault(n, cap)
    return out


def data_model(an: dict) -> dict:
    """SwiftData models: classes carrying the macro's backing-data field, with stored properties and getter types."""
    getters: dict[tuple, str] = {}
    for e in an["entries"]:
        m = _GETTER.match(e.get("demangled") or "")
        if m and e["demangled"].startswith(an["module"] + "."):
            getters.setdefault((m.group(1), m.group(2)), m.group(3))
    items, migration = [], []
    for t in an["swift_types"]:
        name = t.get("name") or ""
        if name.startswith("$"):
            continue
        if _MIGRATION.search(name):
            migration.append({"name": name, "kind": t["kind"], "evidence": t["evidence"]})
        fields = t.get("fields") or []
        if t["kind"] != "class" or not any(f.get("name") == MODEL_MARKER_FIELD for f in fields):
            continue
        props = []
        for f in fields:
            fname = f.get("name") or ""
            if fname.startswith("_$") or not fname:
                continue
            prop = fname.lstrip("_")
            typ = getters.get((name, prop))
            props.append({"name": prop, "type": typ or "unknown",
                          "type_source": "getter signature" if typ else "unknown: stored as a macro placeholder type",
                          "evidence": t["evidence"]})
        items.append({"name": name, "properties": props, "constraints_and_relationships": "unknown",
                      "evidence": t["evidence"]})
    items.sort(key=lambda i: i["name"])
    migration.sort(key=lambda i: i["name"])
    return {"status": "ok" if items else "unknown",
            "reason": None if items else f"no class with the field {MODEL_MARKER_FIELD} was found",
            "detection": f"a class with the field {MODEL_MARKER_FIELD} (SwiftData macro output, deduced)",
            "items": items, "schema_and_migration_types": migration}


def resource_section(an: dict, inv: dict) -> dict:
    files = resources.files(inv)
    matches = resources.string_matches(inv)
    xr = {x["address"]: x["functions"] for x in an["xrefs"]}
    sha = an["source"]["arm64_sha256"]
    items = []
    for f in files:
        usage = []
        for s in matches.get(f["path"], []):
            addr = s["evidence"].get("address")
            refs = xr.get(addr) or []
            usage.append({"string": s["value"], "string_address": addr,
                          "referenced_from": [{"function": r.get("demangled") or r["name"],
                                               "evidence": analysis.ghidra_evidence(sha, r["address"])}
                                              for r in refs],
                          "evidence": s["evidence"]})
        items.append({**f, "kind": resources.kind_of(f["path"]), "usage": usage or "unknown",
                      "usage_basis": "a binary string equals the file name or stem (name match, not proof of use)",
                      "evidence_id": resources.evidence_id(f["sha256"])})
    unknown_parts = {}
    if not files:
        unknown_parts["files"] = "the inventory lists no bundle file"
    if any(i["kind"] == "asset-catalog" for i in items):
        unknown_parts["asset_catalog_contents"] = "the inventory holds the record count of each catalog, not its images"
    return {"status": "ok" if files else "unknown", "reason": None if files else "no bundle files", "items": items,
            "unknown_parts": unknown_parts}


def build(an: dict, inv: dict, scr: dict | None = None) -> dict:
    if an.get("format") != analysis.FORMAT or inv.get("format") != inventory.FORMAT:
        raise ValueError("unexpected analysis or inventory format")
    if an["source"].get("arm64_sha256") != (inv.get("target") or {}).get("arm64_sha256"):
        raise ValueError("the analysis and the inventory describe different slices (arm64 sha256 differs)")
    found = detect_groups(an)
    consts = constants.extract(an["entries"])
    res = resource_section(an, inv)
    cap = captured_screens(found, scr)
    cov = coverage(an, found, scr, consts, res)
    screens_items = [{"name": n, "types": found[n]["types"], "functions_analysed": found[n]["analysed"],
                      "capture": cap[n]["evidence_id"] if n in cap else "unknown"}
                     for n in sorted(found) if found[n]["kind"] == "screen"]
    matched = {c["name"] for c in cap.values()}
    unmatched = sorted(c["name"] for c in (scr or {}).get("screens") or [] if c["name"] not in matched)
    return {"format": FORMAT, "source": an["source"], "tools": an["tools"], "ghidra": an["ghidra"],
            "coverage": cov, "constants": consts, "data_model": data_model(an), "resources": res,
            "screens": {"status": "ok", "detected": screens_items, "captures": (scr or {}).get("screens", []),
                        "captures_not_matched_to_a_detected_screen": unmatched,
                        "navigation": "unknown: only screens that a navigation file reaches are captured; links between "
                                      "screens are not extracted"},
            "groups": [{"name": n, "kind": g["kind"], "types": g["types"], "functions": g["functions"],
                        "analysed": g["analysed"]} for n, g in sorted(found.items())]}
