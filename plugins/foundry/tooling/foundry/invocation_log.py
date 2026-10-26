"""Local, append-only record of routed implementer and reviewer invocations (PLP-316).

Write side: one ``routed`` event per routed invocation that carries an issue
(``FOUNDRY_ROUTE_REQUEST``), plus a ``completed`` event on the Claude post-tool callback.
Read side: ``invocations_for_diff`` attributes the implementer/corrector invocations to a
reviewed diff, or returns ``None`` when that cannot be determined.

Nothing here is read by a gate, by routing or by a merge, and the review-proof format is
untouched (``tests/test_invocation_log.py`` asserts the import graph). Every write is
fail-open: an I/O or git error returns ``False`` and changes nothing else. The stored
fields are a closed list (``_FIELDS``); never a prompt, path, command, or secret.
Unlike the opt-in telemetry journal (``FOUNDRY_DATA``), the file lives in the always-on
state directory ``registry.data_dir()``, beside the escalation and review-dedup stores.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from foundry import registry

ROLES = ("implementer", "reviewer")
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_INVOCATION_ID = re.compile(r"[0-9a-f]{16,64}\Z")
_FIELDS = (
    "event", "id", "issue", "host", "role", "model_requested", "effort", "tier",
    "at", "status", "head", "base", "correction", "failure_class",
)
_SAFE_OPEN_FLAGS = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_MAX_BYTES = 8 * 1024 * 1024  # a larger file is neither appended to nor read: fail-open


def claude_invocation_id(correlation: str) -> str:
    """Opaque id derived from the host ``tool_use_id`` (never stored itself)."""
    return hashlib.sha256(f"claude:{correlation}".encode("utf-8")).hexdigest()[:32]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _path(repository: str, state_dir: str | os.PathLike | None) -> Path:
    namespace = hashlib.sha256(repository.encode("utf-8")).hexdigest()
    root = Path(state_dir) if state_dir is not None else Path(registry.data_dir())
    return root / "model-routing" / "invocations" / f"{namespace}.jsonl"


def _repository(root: str | os.PathLike | None) -> str:
    from foundry.routing import repository_identity

    return repository_identity(root)


def _head(root: str | os.PathLike | None) -> str | None:
    try:
        from foundry.routing import git_head

        return git_head(root)
    except Exception:
        return None


def _append(path: Path, event: Mapping[str, object]) -> None:
    line = (json.dumps({k: event.get(k) for k in _FIELDS}, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _SAFE_OPEN_FLAGS, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_BYTES:
            raise OSError("invocation log unusable")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, line)
    finally:
        os.close(descriptor)


def _read(path: Path) -> list[dict]:
    try:
        descriptor = os.open(path, os.O_RDONLY | _SAFE_OPEN_FLAGS)
    except FileNotFoundError:
        return []
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError("invocation log unusable")
        data = handle.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise OSError("invocation log too large")
    events = []
    for raw in data.decode("utf-8").splitlines():
        try:
            item = json.loads(raw)
        except ValueError:
            continue  # a torn or foreign line is skipped, never repaired
        if isinstance(item, dict):
            events.append(item)
    return events


def record_routed(
    route,
    *,
    issue_id: str | None,
    invocation_id: str,
    root: str | os.PathLike | None,
    base: str | None = None,
    correction: bool = False,
    state_dir: str | os.PathLike | None = None,
) -> bool:
    """Append one ``routed`` event; False (never an exception) when not recorded."""
    try:
        role = getattr(route, "role", None)
        if (role not in ROLES or not isinstance(issue_id, str)
                or _ISSUE_ID.fullmatch(issue_id) is None
                or _INVOCATION_ID.fullmatch(invocation_id) is None):
            return False
        head = _head(root) if role == "reviewer" else None
        _append(_path(_repository(root), state_dir), {
            "event": "routed", "id": invocation_id, "issue": issue_id,
            "host": route.host, "role": role, "model_requested": route.model,
            "effort": route.effort, "tier": route.selected_tier, "at": _now(),
            "status": "routed", "head": head,
            "base": base if isinstance(base, str) and _SHA.fullmatch(base) else None,
            "correction": bool(correction) if role == "implementer" else None,
        })
        return True
    except Exception:
        return False


def record_completion(
    invocation_id: str,
    *,
    status: str,
    failure_class: str,
    root: str | os.PathLike | None,
    state_dir: str | os.PathLike | None = None,
) -> bool:
    """Append a ``completed`` event, only for an id this log already recorded."""
    try:
        if _INVOCATION_ID.fullmatch(invocation_id) is None:
            return False
        path = _path(_repository(root), state_dir)
        if not any(e.get("event") == "routed" and e.get("id") == invocation_id
                   for e in _read(path)):
            return False
        _append(path, {"event": "completed", "id": invocation_id, "at": _now(),
                       "status": status, "failure_class": failure_class})
        return True
    except Exception:
        return False


def _routed_with_status(events: list[dict]) -> list[dict]:
    """``routed`` events in append order, status replaced by the last completion."""
    completions = {e["id"]: e for e in events if e.get("event") == "completed"}
    result = []
    for event in events:
        if event.get("event") != "routed":
            continue
        done = completions.get(event.get("id"))
        item = {k: event.get(k) for k in _FIELDS if k != "event"}
        if done is not None:
            item["status"] = done.get("status")
            item["failure_class"] = done.get("failure_class")
        result.append(item)
    return result


def invocations_for_diff(
    issue_id: str,
    head: str,
    base: str | None = None,
    *,
    root: str | os.PathLike | None = None,
    repository: str | None = None,
    state_dir: str | os.PathLike | None = None,
) -> dict | None:
    """Implementer/corrector invocations attributable to the reviewed ``head``.

    Rule (append order, never clock order): take the first recorded reviewer
    invocation of ``issue_id`` whose head is ``head`` (and whose recorded base, when
    both are known, equals ``base``). Its window opens after the latest earlier
    reviewer invocation of the issue with a different head, if any, and closes at that
    reviewer. The implementer invocations of the issue inside the window are returned
    (``correction`` true marks a credited correction plan claimed at routing); ``None``
    in place of an empty list. The whole result is ``None``
    when no such reviewer invocation exists, the log is unreadable, or an input is not
    valid. Limits: an implementer run outside the recorded routing path, or in a window
    holding commits of another author, is not seen; a window can hold invocations whose
    work was discarded. The result names the invocations, not proof they made the diff.
    """
    try:
        if (not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None
                or not isinstance(head, str) or _SHA.fullmatch(head) is None
                or (base is not None and (not isinstance(base, str)
                                          or _SHA.fullmatch(base) is None))):
            return None
        path = _path(repository or _repository(root), state_dir)
        records = [r for r in _routed_with_status(_read(path)) if r.get("issue") == issue_id]
        review_index = None
        for index, record in enumerate(records):
            if record["role"] != "reviewer" or record.get("head") != head:
                continue
            if base is not None and record.get("base") not in (None, base):
                continue
            review_index = index
            break
        if review_index is None:
            return None
        window_start = 0
        for index in range(review_index - 1, -1, -1):
            record = records[index]
            if record["role"] == "reviewer" and record.get("head") != head:
                window_start = index + 1
                break
        produced = [r for r in records[window_start:review_index]
                    if r["role"] == "implementer"] or None
        return {
            "issue": issue_id, "head": head, "base": base,
            "reviewer": records[review_index], "invocations": produced,
        }
    except Exception:
        return None
