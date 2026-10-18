"""Bundle resources with their fingerprint and known usage (PAT-260).

Usage is known only when a string of the binary equals the file name or its stem (a name match: it shows the name is
in the code, not how the file is used). Anything else is `unknown`, never `unused`.
"""
from __future__ import annotations

import os

IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp", ".svg", ".tiff", ".bmp", ".pdf"}
SOUNDS = {".mp3", ".m4a", ".wav", ".caf", ".aif", ".aiff", ".aac", ".flac", ".ogg"}
ASSET_CATALOG = {".car"}
MAX_USAGE = 5  # usage strings kept per resource by default; `rebuild` states the total and whether it cut (`usage_truncated`)


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGES:
        return "image"
    if ext in SOUNDS:
        return "sound"
    if ext in ASSET_CATALOG:
        return "asset-catalog"
    return "other"


def names_of(path: str) -> set[str]:
    base = os.path.basename(path)
    return {base, os.path.splitext(base)[0]} - {""}


def files(inv: dict) -> list[dict]:
    """The bundle files of an inventory: path, size, sha256."""
    res = inv.get("resources")
    if not isinstance(res, dict) or res.get("status") != "ok":
        return []
    out = []
    for it in res.get("items") or []:
        ev = it.get("evidence") if isinstance(it, dict) else None
        if isinstance(it, dict) and it.get("kind") == "file" and isinstance(ev, dict) and ev.get("sha256"):
            out.append({"path": it["path"], "size": it.get("size"), "sha256": ev["sha256"]})
    return sorted(out, key=lambda f: f["path"])


def string_matches(inv: dict, limit: int | None = MAX_USAGE) -> dict[str, list[dict]]:
    """path -> inventory strings equal to the file name or stem, by address (at most `limit`, all when None)."""
    strings = (inv.get("ui_strings") or {}).get("items") or []
    by_value: dict[str, list[dict]] = {}
    for s in strings:
        if isinstance(s, dict) and isinstance(s.get("value"), str):
            by_value.setdefault(s["value"], []).append(s)
    found: dict[str, list[dict]] = {}
    for f in files(inv):
        hits = [s for n in sorted(names_of(f["path"])) for s in by_value.get(n, [])]
        uniq = {(s["evidence"].get("address"), s["value"]): s for s in hits if isinstance(s.get("evidence"), dict)}
        if uniq:
            found[f["path"]] = [uniq[k] for k in sorted(uniq, key=lambda k: (str(k[0]), k[1]))][:limit]
    return found


def string_addresses(inv: dict) -> list[str]:
    return sorted({s["evidence"]["address"] for v in string_matches(inv).values() for s in v
                   if isinstance(s["evidence"].get("address"), str)})


def evidence_id(sha256: str) -> str:
    return "res_" + sha256
