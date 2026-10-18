"""The rebuild-grade analysis document of one authorised target (PAT-260).

One Ghidra analysis (`ghidra.ensure_analysis`), one batch export of every app function with its pseudo-code, the Swift
type descriptors and their fields, and the references to the strings that name bundle resources. REA is used only to
demangle Swift symbols. Evidence of an item is the slice sha256 and the address (Ghidra evidence, not a REA id); its
`evidence_id` is derived from those two values (`ghidra_evidence`).
"""
from __future__ import annotations

import re
import time
from collections import Counter
from pathlib import Path

from . import ghidra, groups, inventory, paths, pins, proc, resources

FORMAT = 1
MAX_CODE_CHARS = 30000


class AnalysisError(Exception):
    pass


def ghidra_evidence(slice_sha256: str, address: str, program_id: str | None = None) -> dict:
    addr = address.lower()
    ev = {"evidence_id": f"gh_{slice_sha256[:16]}_{addr.removeprefix('0x')}",
          "source": "ghidra", "slice_sha256": slice_sha256, "address": address}
    if program_id:
        ev["ghidra_program_id"] = program_id
    return ev


def app_module(inv: dict) -> str | None:
    """The Swift module that owns most plain Swift classes of the inventory, else None."""
    c = Counter()
    for it in (inv.get("types") or {}).get("items") or []:
        if isinstance(it, dict) and it.get("kind") == "objc-class" and isinstance(it.get("name"), str):
            p = groups.split_swift_class(it["name"])
            if p:
                c[p[0]] += 1
    return min(c.items(), key=lambda kv: (-kv[1], kv[0]))[0] if c else None


def symbol_prefix(module: str) -> str:
    return f"_$s{len(module)}{module}"


def owner_of(symbol: str, module: str) -> str | None:
    """The first type name after the module in a Swift symbol, None when the symbol uses a substitution there."""
    pre = symbol_prefix(module)
    if not symbol.startswith(pre):
        return None
    m = re.match(r"(\d+)", symbol[len(pre):])
    if not m:
        return None
    n, rest = int(m.group(1)), symbol[len(pre) + m.end():]
    return rest[:n] if 0 < n <= len(rest) else None


def demangle(rea: inventory.Rea, slice_path: Path, symbols: list[str]) -> tuple[dict[str, str], str | None]:
    """symbol -> demangled text for every symbol REA could demangle; and a reason when a batch failed."""
    out: dict[str, str] = {}
    why = None
    todo = sorted({s for s in symbols if s.startswith("_$s")})
    for i in range(0, len(todo), pins.DEMANGLE_BATCH):
        batch = todo[i:i + pins.DEMANGLE_BATCH]
        doc, reason = rea.call(["demangle-swift", str(slice_path), *batch], pins.TIMEOUT_DEMANGLE)
        rows = ((doc or {}).get("normalized_result") or {}).get("symbols")
        if doc is None or not isinstance(rows, list):
            why = reason or "demangle answer not readable"
            continue
        for r in rows:
            if isinstance(r, dict) and r.get("status") == "demangled" and isinstance(r.get("output"), str):
                out[r["input"]] = r["output"]
    return out, why


def build(inv: dict, slice_path: Path, data: Path, *, module: str | None = None, runner=None,
          clock=time.monotonic) -> dict:
    runner = runner or proc.run
    t0 = clock()
    module = module or app_module(inv)
    if not module:
        raise AnalysisError("no Swift module could be derived from the inventory; pass --module")
    sha = inv["target"]["arm64_sha256"]
    marker = ghidra.ensure_analysis(data, slice_path, sha, runner, clock)
    xr_addrs = resources.string_addresses(inv)
    out_dir = paths.analyses(data) / f"{inv['target']['name']}-{inv['target']['sha256'][:16]}"
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    xr_file = out_dir / "xrefs-input.txt"
    xr_file.write_text("".join(a + "\n" for a in xr_addrs), encoding="utf-8")
    include = "^" + re.escape(symbol_prefix(module))
    raw, export_seconds = ghidra.export(
        data, marker, out_dir / "ghidra-export.json", include=include,
        data_pattern=include + r".*vpZ$", xrefs_file=xr_file if xr_addrs else None, runner=runner, clock=clock)
    prog = raw.get("program_id")
    entries = []
    for f in raw.get("functions") or []:
        code = f.get("pseudo_code")
        e = {"address": f["address"], "symbol": f["symbol"], "owner": owner_of(f["symbol"], module),
             "size": f.get("size"), "status": "ok" if isinstance(code, str) else "unknown",
             "reason": None if isinstance(code, str) else (f.get("error") or "not decompiled"),
             "string_literals": f.get("string_literals") or {}, "pseudo_code": code[:MAX_CODE_CHARS] if isinstance(code, str) else None,
             "truncated": isinstance(code, str) and len(code) > MAX_CODE_CHARS,
             "evidence": ghidra_evidence(sha, f["address"], prog)}
        entries.append(e)
    entries.sort(key=lambda e: (int(e["address"], 16), e["symbol"]))
    td = clock()
    names, why = demangle(inventory.Rea(data, runner), slice_path, [e["symbol"] for e in entries])
    demangle_seconds = round(clock() - td, 1)
    for e in entries:
        e["demangled"] = names.get(e["symbol"])
    types = [{**t, "evidence": ghidra_evidence(sha, t["address"], prog)} for t in raw.get("swift_types") or []]
    unknown: dict[str, str] = {}
    if why:
        unknown["demangle"] = f"some symbols are not demangled: {why}"
    if raw.get("swift_types_status") != "ok":
        unknown["swift_types"] = str(raw.get("swift_types_status"))
    if not xr_addrs:
        unknown["resource_references"] = "no inventory string matches a resource file name"
    xrefs = []
    for x in raw.get("xrefs") or []:
        fns = [{**f, "demangled": names.get(f["name"])} for f in x.get("functions") or []]
        xrefs.append({"address": x["address"], "functions": fns})
    return {
        "format": FORMAT,
        "source": {k: inv["target"].get(k) for k in ("name", "sha256", "arm64_sha256", "analysed_binary")},
        "tools": {"ghidra": raw.get("ghidra_version"), "rea": pins.REA_VERSION, "jdk": pins.JDK_VERSION},
        "ghidra": {"program_id": prog, "project": ghidra.project_dir(data, sha).name, "program": marker["program"]},
        "module": module,
        "timing": {"analysis_seconds": marker["analysis_seconds"], "analysis_reused": marker["reused"],
                   "export_seconds": export_seconds, "demangle_seconds": demangle_seconds,
                   "total_seconds": round(clock() - t0, 1)},
        "functions": {"total": raw.get("functions_total"), "app": raw.get("functions_matched"),
                      "decompiled": raw.get("decompiled"), "failed": raw.get("decompile_failed"),
                      "truncated": sum(1 for e in entries if e["truncated"]),
                      "decompile_seconds": raw.get("decompile_seconds")},
        "entries": entries,
        "swift_types": types,
        "data_symbols": raw.get("data_symbols") or [],
        "xrefs": xrefs,
        "unknown_parts": unknown,
    }
