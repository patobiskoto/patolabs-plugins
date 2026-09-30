"""Machine-local guard for an ambiguous Epic audit append.

This journal only prevents a second local POST while the first effect is unknown.
It is not provider CAS, cross-machine exclusion, or exactly-once delivery.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator

from foundry import registry
from foundry.models import Project
from foundry.trackers.base import TrackerConflictError


_SCHEMA = "foundry-epic-audit-intent.v1"
_AUDIT_ID = re.compile(r"(?:linear|github):epic:[0-9a-f]{64}\Z")


class EpicAuditIntent:
    def __init__(self, provider: str, project: Project, parent_id: str,
                 *, state_dir: Path | None = None):
        if provider not in {"linear", "ghprojects"}:
            raise ValueError("unsupported Epic audit intent provider")
        base = state_dir if state_dir is not None else Path(registry.data_dir())
        self.directory = base / f"{provider}-epic-closure-intents"
        scope = json.dumps(
            [provider, project.id, project.key, parent_id],
            separators=(",", ":"), ensure_ascii=True,
        )
        self.fingerprint = hashlib.sha256(scope.encode("ascii")).hexdigest()

    def _path(self) -> Path:
        return self.directory / f"{self.fingerprint}.json"

    @contextmanager
    def lock(self) -> Iterator[None]:
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            descriptor = os.open(
                self.directory / f".{self.fingerprint}.lock",
                os.O_RDWR | os.O_CREAT, 0o600,
            )
        except OSError as exc:
            raise TrackerConflictError("Epic audit local intent unavailable") from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def read(self) -> dict[str, str] | None:
        try:
            record = json.loads(self._path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise TrackerConflictError("Epic audit local intent invalid") from exc
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "fingerprint", "audit_id", "state"}
            or record.get("schema") != _SCHEMA
            or record.get("fingerprint") != self.fingerprint
            or not isinstance(record.get("audit_id"), str)
            or _AUDIT_ID.fullmatch(record["audit_id"]) is None
            or record.get("state") not in {"pending", "complete"}
        ):
            raise TrackerConflictError("Epic audit local intent invalid")
        return record

    def write(self, audit_id: str, state: str) -> None:
        if _AUDIT_ID.fullmatch(audit_id) is None or state not in {"pending", "complete"}:
            raise ValueError("invalid Epic audit intent")
        payload = json.dumps(
            {"schema": _SCHEMA, "fingerprint": self.fingerprint,
             "audit_id": audit_id, "state": state},
            sort_keys=True, separators=(",", ":"),
        )
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.directory,
                prefix=f".{self.fingerprint}.", delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.chmod(temporary_name, 0o600)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._path())
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise TrackerConflictError("Epic audit local intent unavailable") from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def clear(self) -> None:
        """Release an intent only after the provider definitively refused the POST."""
        try:
            self._path().unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise TrackerConflictError("Epic audit local intent unavailable") from exc
