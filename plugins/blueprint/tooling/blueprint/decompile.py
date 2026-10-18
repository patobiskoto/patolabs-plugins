"""Targeted decompilation of the entry points of one group, with REA evidence (PAT-253).

One `rea search --kind procedures` finds the group's procedures; the entry points are the
Swift functions among them (`entry_points`); then one `rea decompile` per chosen address. Count,
per-call time and total time are bounded; what is not read is `unknown` with the reason.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from . import inventory, paths, pins, proc

FORMAT = 1
MAX_CODE_CHARS = 30000


# Swift Observation macro boilerplate and accessor coroutines that also end in `F`.
BOILERPLATE_METHODS = ("access", "withMutation", "shouldNotifyObservers")
BOILERPLATE_MARKERS = ("$defer", ".resume.")


class DecompileError(Exception):
    pass


def slice_for(target: Path, inv: dict, data: Path, runner=proc.run) -> Path:
    """The arm64 slice the inventory was made from; refused when it is not the same bytes."""
    binary, _, _, _ = inventory.locate_binary(target)
    want = inv["target"].get("sha256")
    if inventory.sha256_file(binary) != want:
        raise DecompileError("the target is not the one this inventory was made from (sha256 differs)")
    sl, _ = inventory.thin(binary, paths.work(data) / want[:16], runner)
    if inventory.sha256_file(sl) != inv["target"].get("arm64_sha256"):
        raise DecompileError("the arm64 slice differs from the inventory's; run `inventory` again")
    return sl


def search_pattern(module: str, classes: list[str]) -> str:
    """Regex for the Swift-mangled prefix of the methods of the classes: `_$s<len><module><len><class>C`."""
    return "|".join(re.escape(f"_$s{len(module)}{module}{len(c)}{c}C") for c in classes)


def entry_points(rows: list, module: str, classes: list[str]) -> list[dict]:
    """Functions (mangled name ending in `F`) and initialisers (`fC`) of the classes, by address.
    Accessors, closures, coroutine
    helpers (`BOILERPLATE_MARKERS`) and Observation boilerplate (`BOILERPLATE_METHODS`) are left out."""
    prefixes = {f"_$s{len(module)}{module}{len(c)}{c}C": c for c in classes}
    out, seen = [], set()
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("value"), str) or not isinstance(r.get("address"), str):
            continue
        v = r["value"]
        pre = next((p for p in prefixes if v.startswith(p)), None)
        if pre is None or r["address"] in seen:
            continue
        rest = v[len(pre):]
        m = re.match(r"(\d+)", rest)
        if not m or not rest.endswith(("F", "fC")):
            continue
        n = int(m.group(1))
        method = rest[m.end(): m.end() + n]
        if method in BOILERPLATE_METHODS or any(x in rest for x in BOILERPLATE_MARKERS):
            continue
        seen.add(r["address"])
        out.append({"address": r["address"], "symbol": v, "method": method,
                    "class": prefixes[pre]})
    return sorted(out, key=lambda e: (int(e["address"], 16), e["symbol"]))


def _evidence(doc: dict, address: str | None) -> dict:
    return {"rea_evidence_id": doc.get("evidence_id"),
            "target_sha256": (doc.get("subject") or {}).get("digest", {}).get("sha256"), "address": address}


def search_group(rea: inventory.Rea, sl: Path, group: dict,
                 timeout: float = pins.TIMEOUT_REA) -> tuple[list[dict], dict | None, str | None]:
    """(entry points, evidence of the search, unknown reason)."""
    hint = group["search_hint"]
    if "module" not in hint:
        return [], None, hint.get("unknown", "no symbol search hint")
    doc, why = rea.call(["search", str(sl), "--kind", "procedures", "--mode", "regex",
                         "--pattern", search_pattern(hint["module"], hint["classes"])], timeout)
    rows = (doc or {}).get("normalized_result")
    if doc is None or not isinstance(rows, list):
        return [], None, why or "procedure search not readable"
    return entry_points(rows, hint["module"], hint["classes"]), _evidence(doc, None), None


def decompile_one(rea: inventory.Rea, sl: Path, address: str, timeout: float) -> dict:
    doc, why = rea.call(["decompile", str(sl), address], timeout)
    code = (doc or {}).get("normalized_result")
    if doc is None or not isinstance(code, str):
        return {"address": address, "status": "unknown", "reason": why or "no pseudo-code in the answer",
                "pseudo_code": None, "evidence": None}
    return {"address": address, "status": "ok", "reason": None, "pseudo_code": code[:MAX_CODE_CHARS],
            "truncated": len(code) > MAX_CODE_CHARS, "evidence": _evidence(doc, address)}


def run(rea: inventory.Rea, sl: Path, *, group: dict | None, entry: str | None, list_only: bool,
        maximum: int, budget: float, clock=time.monotonic) -> dict:
    start = clock()
    doc: dict = {"format": FORMAT, "group": group["name"] if group else None, "entries": [],
                 "unknown_parts": {}}
    if entry is not None:
        chosen, candidates = [{"address": entry, "symbol": None, "method": None}], []
    else:
        # The search counts against the run budget too, so `--budget` bounds the whole run.
        candidates, ev, why = search_group(rea, sl, group, min(pins.TIMEOUT_REA, budget))
        doc["search_evidence"] = ev
        if why:
            doc["unknown_parts"]["entry_points"] = why
        chosen = candidates[:maximum]
        doc["candidates"] = {"total": len(candidates), "listed": candidates[:200],
                             "chosen": len(chosen), "truncated": len(candidates) > len(chosen)}
    if list_only:
        return doc
    for c in chosen:
        left = budget - (clock() - start)
        if left < 1:
            doc["unknown_parts"]["budget"] = f"stopped before {c['address']}: the {budget:g}s budget is spent"
            break
        e = decompile_one(rea, sl, c["address"], min(pins.TIMEOUT_DECOMPILE, left))
        e.update({"symbol": c["symbol"], "method": c["method"]})
        doc["entries"].append(e)
    return doc


def from_analysis(an: dict, inv: dict, *, group: dict | None, entry: str | None, list_only: bool,
                  maximum: int) -> dict:
    """The same document as `run`, read from the saved analysis: no Ghidra and no REA call (PAT-260). The pseudo-code and
    evidence are those of the one analysis (`analysis.build`); the evidence is Ghidra's (slice sha256 and address)."""
    if (an.get("source") or {}).get("sha256") != inv["target"].get("sha256") or \
            an["source"].get("arm64_sha256") != inv["target"].get("arm64_sha256"):
        raise DecompileError("the analysis was not made from this inventory's target (sha256 differs)")
    by_addr = {int(e["address"], 16): e for e in an["entries"]}
    doc: dict = {"format": FORMAT, "group": group["name"] if group else None, "entries": [], "unknown_parts": {},
                 "from_analysis": {"ghidra_program_id": an["ghidra"]["program_id"], "ghidra_runs": 0}}
    if entry is not None:
        chosen, candidates = [{"address": entry, "symbol": None, "method": None}], []
    else:
        hint = group["search_hint"]
        if "module" not in hint:
            doc["unknown_parts"]["entry_points"] = hint.get("unknown", "no symbol search hint")
            candidates = []
        else:
            rows = [{"value": e["symbol"], "address": e["address"]} for e in an["entries"]]
            candidates = entry_points(rows, hint["module"], hint["classes"])
        chosen = candidates[:maximum]
        doc["candidates"] = {"total": len(candidates), "listed": candidates[:200],
                             "chosen": len(chosen), "truncated": len(candidates) > len(chosen)}
    if list_only:
        return doc
    for c in chosen:
        try:
            e = by_addr.get(int(c["address"], 16))
        except ValueError:
            e = None
        if e is None:
            doc["entries"].append({"address": c["address"], "status": "unknown", "pseudo_code": None, "evidence": None,
                                   "reason": "no app function at this address in the analysis", "symbol": c["symbol"],
                                   "method": c["method"]})
            continue
        doc["entries"].append({"address": c["address"], "status": e["status"], "reason": e["reason"],
                               "pseudo_code": e["pseudo_code"], "truncated": e["truncated"],
                               "evidence": e["evidence"], "symbol": c["symbol"] or e["symbol"],
                               "method": c["method"]})
    return doc
