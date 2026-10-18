"""Registry of authorised targets (PAT-252): date, target, reason; written only by `targets add`."""
from __future__ import annotations

import datetime
import json
import os
import tempfile
from pathlib import Path

VERSION = 1


class RegistryError(Exception):
    pass


def canonical(target: str) -> str:
    return os.path.realpath(os.path.expanduser(target))


def load(path: Path) -> list[dict]:
    """Entries; [] when the file does not exist; RegistryError when it exists but is unreadable."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["targets"]
        ok = data.get("version") == VERSION and isinstance(entries, list) and all(
            isinstance(e, dict) and all(isinstance(e.get(k), str) and e[k] for k in ("date", "target", "reason"))
            for e in entries)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise RegistryError("the registry file is unreadable or malformed; nothing is authorised")
    return entries


def find(path: Path, target: str) -> dict | None:
    want = canonical(target)
    return next((e for e in load(path) if e["target"] == want), None)


def add(path: Path, target: str, reason: str, date: str | None = None) -> dict:
    if not reason.strip():
        raise RegistryError("a reason is required")
    if not Path(target).expanduser().exists():
        raise RegistryError("the target does not exist")
    date = date or datetime.datetime.now().astimezone().date().isoformat()
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise RegistryError("the date must be YYYY-MM-DD") from None
    entries = load(path)
    entry = {"date": date, "target": canonical(target), "reason": reason.strip()}
    if any(e["target"] == entry["target"] for e in entries):
        raise RegistryError("the target is already authorised")
    entries.append(entry)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".targets-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": VERSION, "targets": entries}, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return entry
