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
_EXTENSION_PATH = re.compile(r"^\(extension in (\w+)\):(.+)$")
_INIT_CALL = re.compile(r"(__s(?:(?!builtinStringLiteral)\w)+cfC)\s*\(([^;]*)\)\s*;", re.DOTALL)
FLOATS = {"Swift.Double": ("d", 16), "CoreGraphics.CGFloat": ("d", 16), "Swift.Float": ("f", 8)}
INTS = {"Swift.Int": 64, "Swift.Int64": 64, "Swift.Int32": 32, "Swift.Int16": 16, "Swift.Int8": 8,
        "Swift.UInt": -64, "Swift.UInt64": -64, "Swift.UInt32": -32, "Swift.UInt16": -16, "Swift.UInt8": -8}


def body(code: str) -> str:
    i, j = code.find("{"), code.rfind("}")
    return code[i + 1:j] if 0 <= i < j else ""


_DISCRIMINATOR = re.compile(r"\((\w+) in (_[0-9A-F]{32})\)")


def readable_name(name: str) -> tuple[str, str | None]:
    """(name without the private-scope discriminator, the discriminator or None). Swift names a private type
    `(Type in _<32 hex>)`: the hash is not product language; the readable name is `Type` (PAT-260)."""
    found = _DISCRIMINATOR.findall(name)
    return _DISCRIMINATOR.sub(r"\1", name), (found[0][1] if found else None)


def split_args(text: str) -> list[str]:
    """Top-level comma-separated arguments of a call, parentheses and quotes respected."""
    out, depth, cur, quote = [], 0, "", False
    for i, ch in enumerate(text):
        if quote:
            quote = not (ch == '"' and text[i - 1] != "\\")
        elif ch == '"':
            quote = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


NOT_A_LITERAL = "not a literal"
IMMEDIATE = "instruction immediate"
LITERAL = "pseudo-code literal"


def call_arguments(args_text: str, registers: dict | None = None) -> tuple[list, list]:
    """(values, sources): each argument of the constructor call in order. The value is its integer when the pseudo-code
    passes a plain integer literal (source "pseudo-code literal"); else, when `registers` (the call site's x0..x7 constants
    from the export, `{"x5": 5}`) holds the register of the same position, that immediate (source "instruction
    immediate": the decompiler's n-th argument is the n-th integer argument register, deduced); else "not a literal"
    (source None). A literal that contradicts the register's constant makes the registers unusable for this call."""
    args = split_args(args_text)
    regs = registers or {}
    lits = [_num(a) if re.fullmatch(r"-?\d+|0x[0-9a-fA-F]+", a) else None for a in args]
    if any(v is not None and f"x{i}" in regs and regs[f"x{i}"] != v for i, v in enumerate(lits)):
        regs = {}
    out, src = [], []
    for i, v in enumerate(lits):
        if v is not None:
            out.append(v)
            src.append(LITERAL)
        elif f"x{i}" in regs and i < 8:
            out.append(_signed64(regs[f"x{i}"]))
            src.append(IMMEDIATE)
        else:
            out.append(NOT_A_LITERAL)
            src.append(None)
    return out, src


def _signed64(n: int) -> int:
    n &= (1 << 64) - 1
    return n - (1 << 64) if n >= 1 << 63 else n


# Integer registers used by a parameter of the Swift arm64 convention: x0..x7 take the first integer arguments, a String
# takes two (pointer, length), a floating-point value none (it goes to a d register). A resilient Foundation value type is
# passed by address, in one register (deduced: its argument in the pseudo-code of the target is a stack address).
_REGISTERS = {**{t: 1 for t in INTS}, "Swift.Bool": 1, "Swift.String": 2, **{t: 0 for t in FLOATS},
              "Foundation.UUID": 1}
_BY_ADDRESS = {"Foundation.UUID"}
_INIT_PARAMS = re.compile(r"init\((.*)\)(?: -> [^()]*)?\s*$", re.DOTALL)


def constructor_parameters(demangled: str | None, registers: dict) -> tuple[list | None, str | None]:
    """(parameters, None) or (None, reason): each declared parameter of the constructor with the integer registers its
    type takes and, for an integer or Bool parameter whose register holds a constant at the call, that value. The layout
    stops being deducible at the first type of unknown width (reason) and after x7 (the rest is on the stack)."""
    m = _INIT_PARAMS.search(demangled or "")
    if not m:
        return None, "the constructor's declared parameters were not found in its demangled name"
    params, reg = [], 0
    for text in split_args(m.group(1)):
        label, _, typ = text.rpartition(": ") if ": " in text else ("", "", text)
        typ = typ.strip()
        width = _REGISTERS.get(typ)
        item = {"label": label or None, "type": typ, "registers": [], "value": NOT_A_LITERAL, "source": None}
        params.append(item)
        if width is None:
            item["reason"] = f"unknown register layout for the type {typ}: this and the next parameters are not mapped"
            return params, "the register layout of a parameter type is not known"
        if typ in FLOATS:
            item["reason"] = "passed in a floating-point register, not read"
            continue
        if reg + width > 8:
            item["reason"] = "passed on the stack (after x7) or split between x7 and the stack, not read"
            reg = 8
            continue
        item["registers"] = [f"x{reg + k}" for k in range(width)]
        if typ in _BY_ADDRESS:
            item["reason"] = "passed by address"
        elif typ == "Swift.String":
            item["reason"] = "a String takes a pointer and a length register: its text is not read here"
        elif item["registers"][0] in registers:
            raw = registers[item["registers"][0]]
            if typ == "Swift.Bool":
                item["value"], item["source"] = bool(raw & 0xff), IMMEDIATE
            else:
                bits = abs(INTS[typ])
                n = raw & ((1 << bits) - 1)
                item["value"] = n - (1 << bits) if INTS[typ] > 0 and n >= 1 << (bits - 1) else n
                item["source"] = IMMEDIATE
        else:
            item["reason"] = "the register holds no constant at the call"
        reg += width
    return params, None


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
        ext = _EXTENSION_PATH.match(path)
        if ext:  # a static member declared in the app's extension of another module's type
            path = ext.group(2)
        shown, scope = readable_name(f"{path}.{name}")
        item = {"name": shown, "type": typ, "value": "unknown", "reason": None,
                "evidence": [e["evidence"]]}
        if scope:
            item["private_scope"] = scope
        if ext:
            item["extension_in_module"] = ext.group(1)
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
    sites = [c for c in init.get("call_sites") or [] if call and c["callee"] == "_$s" + call.group(1)[3:]]
    regs = sites[0]["registers"] if len(sites) == 1 else {}
    values, sources = call_arguments(call.group(2), regs) if call else ("unknown", [])
    params, why = constructor_parameters((ctor or {}).get("demangled"), regs) if len(sites) == 1 else (
        None, "no single call site of the constructor was exported" if call else None)
    item["value"] = {"kind": "object", "string_literals": [s["value"] for s in lits],
                     "integer_literals_in_order": stores,
                     "constructor_call_arguments": values,
                     "constructor_call_argument_sources": sources,
                     "constructor": (ctor or {}).get("demangled") or "unknown",
                     "mapping_to_parameters": "unknown" if params is None else "constructor_parameters (deduced)"}
    if params is not None:
        item["value"]["constructor_parameters"] = params
        if why:
            item["value"]["constructor_parameters_note"] = why
    item["reason"] = ("an object built at first use: `integer_literals_in_order` lists only the integers stored on the stack "
                      "(a partial list); `constructor_call_arguments` lists the arguments of the constructor call in order, "
                      "an integer when the pseudo-code passes a literal or the instructions before the call move an immediate into the "
                      "argument register of that position (`constructor_call_argument_sources`), \"not a literal\" otherwise; "
                      "`constructor_parameters`, when present, maps the argument registers to the declared parameters by the "
                      "Swift arm64 convention as far as it is deducible, else which literal feeds which parameter is not established")
