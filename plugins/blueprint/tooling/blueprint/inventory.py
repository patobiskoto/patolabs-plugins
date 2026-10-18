"""Reproducible inventory of an authorised target, with REA evidence (PAT-252).

Every section is {"status": "ok" | "unknown", "reason": ..., "items": [...]}. What cannot be
read is `unknown`, never empty-and-ok. The document has no timestamp, no absolute path and
sorted lists, so the same target and tools give the same bytes.
"""
from __future__ import annotations

import hashlib
import json
import plistlib
import struct
from pathlib import Path

from . import paths, pins, proc, setup

FORMAT = 1
MAX_STRINGS = 50000
MAX_FILES = 5000
STRING_PATTERN = "."  # regex matching every analysed string

_FAT = {0xCAFEBABE, 0xCAFEBABF}
_MH_64_LE = 0xFEEDFACF
_CPU_ARM64 = 0x0100000C


class InventoryError(Exception):
    pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def macho_kind(p: Path) -> str:
    """'universal' | 'arm64' | 'other-macho' | 'not-macho'."""
    try:
        with p.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return "not-macho"
    if len(head) < 8:
        return "not-macho"
    if struct.unpack(">I", head[:4])[0] in _FAT:
        return "universal"
    if struct.unpack("<I", head[:4])[0] == _MH_64_LE:
        return "arm64" if struct.unpack("<I", head[4:8])[0] == _CPU_ARM64 else "other-macho"
    return "not-macho"


def locate_binary(target: Path) -> tuple[Path, str, list[Path], Path]:
    """(binary to analyse, note, bundle files, main executable). A directory is an app bundle: its main
    executable, or `<exec>.debug.dylib` when present (Debug iOS build keeps the code there)."""
    if target.is_file():
        return target, "file", [], target
    if not target.is_dir():
        raise InventoryError("the target is neither a file nor a directory")
    try:
        info = plistlib.loads((target / "Info.plist").read_bytes())
        exe = info["CFBundleExecutable"]
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        raise InventoryError("cannot read CFBundleExecutable from Info.plist") from None
    if not isinstance(exe, str) or "/" in exe:
        raise InventoryError("invalid CFBundleExecutable")
    debug = target / f"{exe}.debug.dylib"
    binary = debug if debug.is_file() else target / exe
    if not binary.is_file():
        raise InventoryError("the bundle executable does not exist")
    root = target.resolve()
    for b in (binary, target / exe):
        if b.is_file() and not b.resolve().is_relative_to(root):
            raise InventoryError("the bundle executable resolves outside the bundle (symbolic link)")
    files = sorted(p for p in target.rglob("*") if p.is_file() and not p.is_symlink())
    return binary, "bundle-debug-dylib" if binary == debug else "bundle-executable", files, target / exe


def thin(binary: Path, work: Path, runner=proc.run) -> tuple[Path, str]:
    """The arm64 slice: the binary itself when already thin arm64, else `lipo -thin arm64`."""
    kind = macho_kind(binary)
    if kind == "arm64":
        return binary, "already arm64"
    if kind != "universal":
        raise InventoryError(f"not an arm64 or universal Mach-O ({kind})")
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    out = work / (binary.name + ".arm64")
    rc, _, err = runner(["lipo", str(binary), "-thin", "arm64", "-output", str(out)], pins.TIMEOUT_LIPO)
    if rc != 0 or not out.is_file():
        raise InventoryError(f"no arm64 slice could be extracted ({err.strip()[-200:] or rc})")
    return out, "thinned from universal"


def unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason, "items": []}


def ok(items: list[dict], **extra) -> dict:
    return {"status": "ok", "reason": None, "items": items, **extra}


def sort_items(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda i: json.dumps(i, sort_keys=True))


class Rea:
    """One pinned REA call: JSON in, (document, None) or (None, reason). Always bounded."""

    def __init__(self, data: Path, runner=None):
        self.data, self.runner = data, runner or proc.run

    def call(self, args: list[str], timeout: float = pins.TIMEOUT_REA) -> tuple[dict | None, str | None]:
        cmd = [str(paths.rea_bin(self.data)), *args, "--format", "json"]
        rc, out, err = self.runner(cmd, timeout, env=setup.tool_env(self.data))
        if rc is None:
            return None, err
        try:
            doc = json.loads(out)
        except ValueError:
            return None, f"REA exit {rc}, output is not JSON"
        if rc != 0 or not isinstance(doc, dict) or "error" in doc:
            code = doc.get("code") if isinstance(doc, dict) else None
            return None, f"REA refused or failed (exit {rc}, code {code or 'unknown'})"
        return doc, None


def subject_sha256(doc: dict) -> str | None:
    """`subject.digest.sha256` of a REA answer; None when any level is absent, null or not an object."""
    subject = doc.get("subject")
    digest = subject.get("digest") if isinstance(subject, dict) else None
    sha = digest.get("sha256") if isinstance(digest, dict) else None
    return sha if isinstance(sha, str) else None


def _ev(doc: dict, address=None, file_offset=None) -> dict:
    return {"rea_evidence_id": doc.get("evidence_id"), "target_sha256": subject_sha256(doc),
            "address": address, "file_offset": file_offset}


def _loc(rec: dict) -> tuple:
    loc = rec.get("location") if isinstance(rec.get("location"), dict) else {}
    return loc.get("address"), loc.get("file_offset")


def section_analysis(rea: Rea, binary: Path) -> dict:
    doc, why = rea.call(["analyze", str(binary), "--provider", "ghidra"])
    if doc is None:
        return unknown(why or "analysis failed")
    n = doc.get("normalized_result")
    if not isinstance(n, dict):
        return unknown("analysis answer has no normalized result")
    prov = (doc.get("provider") or {})
    return ok([], procedure_count=n.get("procedure_count", "unknown"), string_count=n.get("string_count", "unknown"),
              segment_count=n.get("segment_count", "unknown"), provider=prov.get("id", "unknown"),
              provider_version=prov.get("version", "unknown"), evidence=_ev(doc))


def section_types(rea: Rea, binary: Path) -> dict:
    doc, why = rea.call(["inspect-native-dispatch-metadata", str(binary)])
    res = ((doc or {}).get("normalized_result") or {}).get("result")
    if doc is None or not isinstance(res, dict):
        return unknown(why or "dispatch metadata not readable")
    keys = ("objc_classes", "objc_protocols", "swift_conformances")
    absent = [k for k in keys if not isinstance(res.get(k), list)]
    if len(absent) == len(keys):
        return unknown("the answer holds none of " + ", ".join(keys))
    items = []
    for c in res.get("objc_classes") or []:
        items.append({"kind": "objc-class", "name": c.get("name"), "super": c.get("super_class"),
                      "evidence": _ev(doc, *_loc(c))})
    for p in res.get("objc_protocols") or []:
        items.append({"kind": "objc-protocol", "name": p.get("name"), "evidence": _ev(doc, *_loc(p))})
    for c in res.get("swift_conformances") or []:
        items.append({"kind": "swift-conformance", "name": c.get("type_name"), "protocol": c.get("protocol_name"),
                      "module": c.get("module") or "unknown", "evidence": _ev(doc, *_loc(c))})
    partial = [f.get("facet") for f in res.get("coverage") or [] if f.get("status") != "complete"]
    return ok(sort_items(items), incomplete_facets=sorted(x for x in partial if x), absent_keys=absent)


def section_strings(rea: Rea, binary: Path) -> dict:
    doc, why = rea.call(["search", str(binary), "--kind", "strings", "--mode", "regex", "--pattern", STRING_PATTERN])
    rows = (doc or {}).get("normalized_result")
    if doc is None or not isinstance(rows, list):
        return unknown(why or "strings not readable")
    items = [{"value": r.get("value"), "evidence": _ev(doc, r.get("address"))}
             for r in rows if isinstance(r, dict) and isinstance(r.get("value"), str)]
    items = sort_items(items)
    return ok(items[:MAX_STRINGS], total=len(items), truncated=len(items) > MAX_STRINGS,
              dropped_rows=len(rows) - len(items))


def section_modules(binary: Path, runner=proc.run) -> dict:
    rc, out, err = runner(["otool", "-L", str(binary)], pins.TIMEOUT_OTOOL)
    if rc != 0:
        return unknown(f"otool -L failed ({err.strip()[-100:] or rc})")
    sha = sha256_file(binary)
    items = []
    for line in out.splitlines()[1:]:
        name = line.strip().split(" (", 1)[0]
        if name:
            items.append({"name": name, "evidence": {"source": "otool -L", "target_sha256": sha}})
    return ok(sort_items(items))


def section_resources(rea: Rea, app: Path | None, files: list[Path], main: Path) -> dict:
    items: list[dict] = []
    parts: dict[str, str] = {}
    for p in files[:MAX_FILES]:
        items.append({"kind": "file", "path": p.relative_to(app).as_posix(), "size": p.stat().st_size,
                      "evidence": {"source": "sha256", "sha256": sha256_file(p)}})
    if len(files) > MAX_FILES:
        parts["files"] = f"unknown beyond the first {MAX_FILES} files"
    if app is None:
        parts["files"] = "unknown: the target is a single file, not a bundle"
    for op, key in (("inspect-asset-catalog", "asset_catalogs"), ("decode-interface-builder", "interface_builder")):
        # These two read the bundle's resources: REA answers unsupported_target for the executable and reads the
        # catalogs when given the .app bundle (both observed 2026-10-18).
        doc, why = rea.call([op, str(app if app is not None else main)])
        n = (doc or {}).get("normalized_result")
        if doc is None or not isinstance(n, dict):
            parts[key] = f"unknown: {why or 'not readable'}"
            continue
        field = "catalogs" if key == "asset_catalogs" else "documents"
        rows = n.get(field)
        if not isinstance(rows, list):
            parts[key] = f"unknown: the answer has no `{field}` list"
            continue
        for r in rows:
            if isinstance(r, dict):
                items.append({"kind": key, "path": r.get("path"), "record_count": r.get("record_count", "unknown"),
                              "evidence": _ev(doc) | {"sha256": r.get("sha256", "unknown")}})
    return ok(sort_items(items), unknown_parts=parts)


def build(target: Path, entry: dict, data: Path, runner=None) -> dict:
    """The inventory document. Raises InventoryError when no binary can be analysed at all."""
    runner = runner or proc.run
    binary, note, files, main = locate_binary(target)
    orig_sha = sha256_file(binary)
    work = paths.work(data) / orig_sha[:16]
    slice_path, slice_note = thin(binary, work, runner)
    rea = Rea(data, runner)
    app = target if target.is_dir() else None
    return {
        "format": FORMAT,
        "authorisation": {"date": entry["date"], "reason": entry["reason"]},
        "target": {"name": target.name, "kind": note, "analysed_binary": binary.name,
                   "sha256": orig_sha, "arm64_slice": slice_note, "arm64_sha256": sha256_file(slice_path)},
        "tools": {"rea": pins.REA_VERSION, "ghidra": pins.GHIDRA_VERSION, "jdk": pins.JDK_VERSION},
        "analysis": section_analysis(rea, slice_path),
        "modules": section_modules(slice_path, runner),
        "types": section_types(rea, slice_path),
        "ui_strings": section_strings(rea, slice_path),
        "resources": section_resources(rea, app, files, main),
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(doc: dict, out: Path) -> None:
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".part")
    tmp.write_text(render(doc), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(out)

