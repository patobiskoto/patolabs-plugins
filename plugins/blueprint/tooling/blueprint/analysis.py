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
MAX_CODE_CHARS = 200000  # above the longest SwiftUI `body` observed on the 2026-10-18 target (73725 characters)


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


# Swift standard-library substitutions that can name the extended type (`Sa` = Array, ...).
_STD_TYPES = {"Sa": "Array", "Sb": "Bool", "Sd": "Double", "Sf": "Float", "Si": "Int", "Su": "UInt", "SS": "String",
              "Sh": "Set", "SD": "Dictionary", "Sq": "Optional"}


_ACCESSORS = {"getter", "setter", "modify", "read", "unsafeMutableAddressor", "unsafeAddressor", "modify2", "read2"}


def extension_of(symbol: str, module: str) -> dict | None:
    """`{"extended_type", "member", "related_types"}` when the symbol is a member of an extension, declared in the app
    module, of a type of another module: `_$s<extended type><len><module>[<requirements>]E<member>...`. The extended type
    must be a clean mangled type (`_extended_type`): a standard substitution or `<len><name>[kind]` repeated, so that a
    type which only mentions the module as a generic argument is not taken for it. `<requirements>` (a constrained
    extension, ending in `l` before `E`) gives `related_types`: the names it carries (`Array where Element == T` gives
    `T`). `extended_type` is its last name (None for a substitution not in `_STD_TYPES`); `member` is the first name after
    `E` (the leading `0` of a word-substitution form skipped; None when unreadable)."""
    if not symbol.startswith("_$s") or symbol.startswith(symbol_prefix(module)):
        return None
    key = f"{len(module)}{module}"
    i = symbol.find(key, 3)
    while i >= 0:
        head = symbol[3:i]
        rest = symbol[i + len(key):]
        m = re.match(r"(?:([A-Za-z0-9_]{1,80}?)l)?E(?=0?\d)", rest)
        ok, typ = _extended_type(head) if head and m else (False, None)
        if ok:
            reqs = m.group(1) or ""
            after = rest[m.end():]
            mm = re.match(r"0?(\d+)", after)
            member = None
            if mm:
                n, tail = int(mm.group(1)), after[mm.end():]
                member = tail[:n] if 0 < n <= len(tail) else None
            return {"extended_type": typ, "member": member, "related_types": _names(reqs)}
        i = symbol.find(key, i + 1)
    return None


def _names(text: str) -> list[str]:
    """The `<len><name>` identifiers of a mangled fragment, in order."""
    out, pos = [], 0
    while pos < len(text):
        m = re.match(r"(\d+)", text[pos:])
        if not m:
            pos += 1
            continue
        n, start = int(m.group(1)), pos + m.end()
        if n == 0 or start + n > len(text):
            break
        out.append(text[start:start + n])
        pos = start + n
    return out


def _extended_type(head: str) -> tuple[bool, str | None]:
    """(clean, last name): `Sa` -> Array; `10Foundation4DateV` -> Date; `7SwiftUI10ShapeStyleP` -> ShapeStyle. Not clean
    when anything else (generic arguments, operators, nested forms) is left over."""
    if re.fullmatch(r"S[A-Za-z]", head):
        return True, _STD_TYPES.get(head)
    names, pos = [], 0
    if head.startswith("So") and head[2:3].isdigit():
        pos = 2  # an Objective-C imported type (`So7UIColorC`)
    while pos < len(head):
        m = re.match(r"(\d+)", head[pos:])
        if not m:
            return False, None
        n, start = int(m.group(1)), pos + m.end()
        if n == 0 or start + n > len(head):
            return False, None
        names.append(head[start:start + n])
        pos = start + n
        if pos < len(head) and head[pos] in "VCOPa" and (pos + 1 == len(head) or head[pos + 1].isdigit()):
            pos += 1
    return (True, names[-1]) if names else (False, None)


def refine_member(ext: dict, demangled: str | None) -> dict:
    """The member name read from the demangled text when it extends the mangled one (a word-substitution form such as
    `daysWithC0` is cut in the symbol): `(extension in M):Swift.Array<A where A == M.T>.daysWithActivity.getter` gives
    `daysWithActivity`; and the requirement types kept are those the text qualifies with the extension's module `M`
    (the symbol does not say which module a substituted name belongs to). Otherwise `ext` unchanged."""
    m = re.search(r"\(extension in (\w+)\):(.*)", demangled or "")
    if not m:
        return ext
    ext = {**ext, "related_types": [t for t in ext.get("related_types") or [] if f"{m.group(1)}.{t}" in demangled]}
    if not ext.get("member"):
        return ext
    rest = m.group(2)
    while re.search(r"<[^<>]*>", rest):
        rest = re.sub(r"<[^<>]*>", "", rest)
    head = re.match(r"[\w.]+", rest)
    parts = [x for x in (head.group(0).split(".") if head else []) if x not in _ACCESSORS]
    name = parts[-1] if parts else ""
    return {**ext, "member": name} if name.startswith(ext["member"]) and len(name) > len(ext["member"]) else ext


_FLOAT_TYPE = re.compile(r"CoreGraphics\.CGFloat|Swift\.Double|Swift\.Float")


def float_calls(raw: list, names: dict[str, str]) -> list[dict]:
    """Calls with a constant in a floating-point register (kept as is) or an integer register that holds a Double bit
    pattern (kept only when the callee's demangled signature mentions CGFloat, Double or Float, since a large integer
    could be anything else). Keyed by the callee, mangled and demangled; the register is not mapped to a parameter."""
    out = []
    for c in raw:
        text = names.get(c["callee"])
        ints = c.get("integer_registers_as_double") or {}
        keep_ints = ints if text and _FLOAT_TYPE.search(text) else {}
        if not c.get("floats") and not keep_ints:
            continue
        item = {"address": c["address"], "callee": c["callee"], "callee_demangled": text}
        if c.get("floats"):
            item["floats"] = c["floats"]
        if keep_ints:
            item["integer_registers_as_double"] = keep_ints
        out.append(item)
    return out


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
    # The app module's own symbols, and the members of its extensions of other modules' types: the module name followed
    # by `E`, or by generic requirements ending in `l` and then `E`, and a name (`extension_of` keeps only the real ones).
    include = ("^" + re.escape(symbol_prefix(module)) + "|^_\\$s.*" + re.escape(f"{len(module)}{module}")
               + r"(?:[A-Za-z0-9_]{1,80}?l)?E\d")
    raw, export_seconds = ghidra.export(
        data, marker, out_dir / "ghidra-export.json", include=include,
        data_pattern=f"(?:{include}).*vpZ$", xrefs_file=xr_file if xr_addrs else None, runner=runner, clock=clock)
    prog = raw.get("program_id")
    entries = []
    for f in raw.get("functions") or []:
        if not f["symbol"].startswith(symbol_prefix(module)) and extension_of(f["symbol"], module) is None:
            continue  # matched the include pattern only by chance
        code = f.get("pseudo_code")
        e = {"address": f["address"], "symbol": f["symbol"], "owner": owner_of(f["symbol"], module),
             "extension": extension_of(f["symbol"], module), "call_sites": f.get("call_sites") or [], "_float_calls": f.get("float_calls") or [],
             "size": f.get("size"), "status": "ok" if isinstance(code, str) else "unknown",
             "reason": None if isinstance(code, str) else (f.get("error") or "not decompiled"),
             "string_literals": f.get("string_literals") or {}, "pseudo_code": code[:MAX_CODE_CHARS] if isinstance(code, str) else None,
             "truncated": isinstance(code, str) and len(code) > MAX_CODE_CHARS,
             "evidence": ghidra_evidence(sha, f["address"], prog)}
        entries.append(e)
    entries.sort(key=lambda e: (int(e["address"], 16), e["symbol"]))
    td = clock()
    callees = sorted({c["callee"] for e in entries for c in e["_float_calls"]})
    names, why = demangle(inventory.Rea(data, runner), slice_path, [e["symbol"] for e in entries] + callees)
    demangle_seconds = round(clock() - td, 1)
    for e in entries:
        e["demangled"] = names.get(e["symbol"])
        if e["extension"]:
            e["extension"] = refine_member(e["extension"], e["demangled"])
        e["float_calls"] = float_calls(e.pop("_float_calls"), names)
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
        "functions": {"total": raw.get("functions_total"), "app": len(entries),
                      "decompiled": sum(e["status"] == "ok" for e in entries),
                      "failed": sum(e["status"] != "ok" for e in entries),
                      "truncated": sum(1 for e in entries if e["truncated"]),
                      "decompile_seconds": raw.get("decompile_seconds")},
        "entries": entries,
        "swift_types": types,
        "data_symbols": raw.get("data_symbols") or [],
        "xrefs": xrefs,
        "unknown_parts": unknown,
    }
