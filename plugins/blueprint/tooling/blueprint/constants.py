"""Static constants and predefined objects read from decompiled getters and initialisers (PAT-260).

Source: the pseudo-code of the app's static property getters (`static <Type>.<name>.getter` once demangled) and, for
a lazily initialised value, of the matching one-time initialiser (`<name>_WZ`). A value is reported only when the code is
a plain literal; anything else is `unknown` with the reason. Pseudo-code is Ghidra output: a decoded value is as sound as
that output (deduced), and the evidence is the Ghidra address of the function it was read from.
"""
from __future__ import annotations

import codecs
import re
import struct

STATIC_GETTER = re.compile(r"^static (.+)\.([A-Za-z_][A-Za-z0-9_]*)\.getter : (.+)$")
_RETURN_LIT = re.compile(r"^\s*return\s+(0x[0-9a-fA-F]+|-?\d+)\s*;\s*$")
_STR_CALL = re.compile(r'builtinStringLiteral\w*\s*\(\s*("(?:[^"\\]|\\.)*"|\w+)\s*,\s*(0x[0-9a-fA-F]+|\d+)')
_STORE_LIT = re.compile(r"\]\s*=\s*(-?\d+|0x[0-9a-fA-F]+)\s*;|\)\s*=\s*(-?\d+|0x[0-9a-fA-F]+)\s*;")
_INIT_CALL = re.compile(r"(__s(?:(?!builtinStringLiteral)\w)+cfC)\s*\(([^;]*)\)\s*;", re.DOTALL)
FLOATS = {"Swift.Double": ("d", 16), "CoreGraphics.CGFloat": ("d", 16), "Swift.Float": ("f", 8)}
INTS = {"Swift.Int": 64, "Swift.Int64": 64, "Swift.Int32": 32, "Swift.Int16": 16, "Swift.Int8": 8,
        "Swift.UInt": -64, "Swift.UInt64": -64, "Swift.UInt32": -32, "Swift.UInt16": -16, "Swift.UInt8": -8}


def body(code: str) -> str:
    i, j = code.find("{"), code.rfind("}")
    return code[i + 1:j] if 0 <= i < j else ""


def _num(s: str) -> int:
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def decode_literal(lit: str, typ: str) -> tuple[object | None, str | None]:
    """(value, None) or (None, reason) for a literal returned by a getter of the given demangled type."""
    n = _num(lit)
    if typ in FLOATS:
        fmt, digits = FLOATS[typ]
        if lit.lower().startswith("0x") and len(lit) - 2 <= digits:
            raw = n.to_bytes(digits // 2, "big")
            return struct.unpack(">" + fmt, raw)[0], None
        return (0.0, None) if n == 0 else (None, "decimal literal for a floating-point type")
    if typ in INTS:
        bits = abs(INTS[typ])
        n &= (1 << bits) - 1
        return (n - (1 << bits) if INTS[typ] > 0 and n >= 1 << (bits - 1) else n), None
    if typ == "Swift.Bool":
        return (n != 0), None
    return None, f"literal returned for the type {typ}, not decoded"


def string_literals(code: str, symbol_texts: dict[str, str | None]) -> list[dict]:
    """The string literals passed to Swift's string-literal builder, in order. A literal the decompiler printed as a
    label (non-ASCII text) is the text the export script read from the binary at the label's address (`symbol_texts`),
    already checked there against the length given in the call."""
    out = []
    for m in _STR_CALL.finditer(code):
        arg, length = m.group(1), _num(m.group(2))
        if arg.startswith('"'):
            try:
                text = codecs.decode(arg[1:-1], "unicode_escape")
            except (UnicodeDecodeError, ValueError):
                text = None
            how = "inline"
        else:
            text = symbol_texts.get(arg)
            how = "bytes read from the binary at the label's address"
        if text is None:
            out.append({"value": "unknown", "reason": "string not resolved from the pseudo-code or the binary"})
        elif len(text.encode("utf-8")) != length:
            out.append({"value": "unknown", "reason": f"length {length} differs from the resolved string's"})
        else:
            out.append({"value": text, "resolved_from": how})
    return out


def _init_symbol(getter: str, name: str) -> str | None:
    key = f"{len(name)}{name}"
    i = getter.rfind(key)
    return getter[:i + len(key)] + "_WZ" if i >= 0 else None


def extract(entries: list[dict]) -> dict:
    """{"status", "items", "unknown_count"}: one item per static getter of the app, sorted by path."""
    by_symbol = {e["symbol"]: e for e in entries}
    by_c_name = {"__s" + e["symbol"][3:]: e for e in entries if e["symbol"].startswith("_$s")}
    items = []
    previews = 0
    for e in entries:
        m = STATIC_GETTER.match(e.get("demangled") or "")
        if not m or e["status"] != "ok":
            continue
        if "PreviewRegistry" in m.group(1) or "$s" in m.group(1):
            previews += 1  # compiler-generated SwiftUI preview records, not app constants
            continue
        path, name, typ = m.group(1), m.group(2), m.group(3)
        item = {"name": f"{path}.{name}", "type": typ, "value": "unknown", "reason": None,
                "evidence": [e["evidence"]]}
        lit = _RETURN_LIT.match(body(e["pseudo_code"]).strip())
        init = by_symbol.get(_init_symbol(e["symbol"], name) or "")
        if lit:
            v, why = decode_literal(lit.group(1), typ)
            if why:
                item["reason"] = why
            else:
                item["value"] = v
        elif init is not None and init["status"] == "ok":
            item["evidence"].append(init["evidence"])
            _from_initialiser(item, init, typ, by_c_name)
        else:
            item["reason"] = "the getter is not a plain literal and no readable one-time initialiser was found"
        items.append(item)
    items.sort(key=lambda i: i["name"])
    return {"status": "ok" if items else "unknown",
            "reason": None if items else "no static property getter was found in the analysed functions",
            "items": items, "unknown_count": sum(1 for i in items if i["value"] == "unknown"),
            "excluded_preview_records": previews}


def _from_initialiser(item: dict, init: dict, typ: str, by_c_name: dict) -> None:
    code = init["pseudo_code"]
    lits = string_literals(code, init.get("string_literals") or {})
    call = _INIT_CALL.search(code)
    if typ == "Swift.String" and len(lits) == 1 and not call:
        if lits[0]["value"] == "unknown":
            item["reason"] = lits[0]["reason"]
        else:
            item["value"] = lits[0]["value"]
        return
    if not lits and not call:
        item["reason"] = "the one-time initialiser holds no string literal or constructor call"
        return
    stores = [_num(a or b) for a, b in _STORE_LIT.findall(code)]
    ctor = by_c_name.get(call.group(1)) if call else None
    item["value"] = {"kind": "object", "string_literals": [s["value"] for s in lits],
                     "integer_literals_in_order": stores,
                     "constructor": (ctor or {}).get("demangled") or "unknown",
                     "mapping_to_parameters": "unknown"}
    item["reason"] = ("an object built at first use: the literals are listed in code order, and which literal feeds which "
                      "parameter is not established")
