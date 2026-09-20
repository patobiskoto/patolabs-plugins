"""Explicit one-off requalification for the historical FOUNDRY-89 campaign.

The command records a new audit.  It never fabricates the missing v4->v5
primitive receipt and never calls either the acceptance patch or Epic close.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import struct
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import foundry
from foundry import write
from foundry.campaign_coordinator import (
    CAMPAIGN_CONTRACT,
    LEDGER_CONTRACT,
    LEGACY_F89_BUDGET_SPENT,
    LEGACY_F89_BINDING_DIGEST,
    LEGACY_F89_CAMPAIGN_ID,
    LEGACY_F89_CHILDREN,
    LEGACY_F89_PARENT_ID,
    CampaignError,
    CampaignCoordinator,
    CampaignSpec,
    CampaignStore,
    EffectEnvelope,
    LegacyParentRequalification,
    _digest,
    _safe_digest,
    _safe_identifier,
    _sqlite_identity_window,
    _verify_guarded_sqlite_descriptors,
)
from foundry.campaign_runtime import CampaignEffectStore
from foundry.routing import acceptance_criteria, acceptance_digest


class LegacyCampaignRequalificationError(RuntimeError):
    """The exact historical evidence is absent or contradictory."""


_LEGACY_DATABASES = ("campaign.sqlite3", "primitive-receipts.sqlite3")
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm")
_SQLITE_HEADER = b"SQLite format 3\0"
_MAX_LEGACY_SQLITE_BYTES = 64 * 1024 * 1024
_MAX_SQLITE_RECORD_BYTES = 1024 * 1024
_MAX_SQLITE_RECORD_COLUMNS = 128
_MAX_BTREE_PAGES = 4096
_MAX_BTREE_CELLS_PER_PAGE = 1024
_MAX_BTREE_CELLS = 16_384
_MAX_OVERFLOW_PAGES = 4096
_MAX_WAL_FRAMES = 16_384


def _raw_path_without_links(path: str | Path, label: str) -> Path:
    """Validate every lexical component before any canonical resolution."""
    candidate = Path(path).expanduser()
    if ".." in candidate.parts:
        raise LegacyCampaignRequalificationError(f"{label} ambigu")
    raw = candidate if candidate.is_absolute() else Path.cwd() / candidate
    for component in (*reversed(raw.parents), raw):
        try:
            mode = os.lstat(component).st_mode
        except OSError as exc:
            raise LegacyCampaignRequalificationError(f"{label} absent") from exc
        if stat.S_ISLNK(mode):
            raise LegacyCampaignRequalificationError(f"{label} lie symboliquement")
    return raw


def _open_no_follow(path: Path, *, directory: bool, label: str) -> int:
    """Open an absolute path one component at a time without following links."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise LegacyCampaignRequalificationError(f"{label} non securisable")
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    parent_flags = os.O_RDONLY | no_follow | close_on_exec | directory_flag
    final_flags = os.O_RDONLY | no_follow | close_on_exec
    if directory:
        final_flags |= directory_flag
    parent_descriptor = None
    descriptor = None
    try:
        parent_descriptor = os.open("/", parent_flags)
        for component in path.parts[1:-1]:
            child = os.open(component, parent_flags, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
            parent_descriptor = child
        descriptor = os.open(path.name, final_flags, dir_fd=parent_descriptor)
        mode = os.fstat(descriptor).st_mode
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise LegacyCampaignRequalificationError(f"{label} invalide") from exc
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    expected = stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)
    if not expected:
        os.close(descriptor)
        raise LegacyCampaignRequalificationError(f"{label} invalide")
    return descriptor


def _identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


def _sidecar_state(
    directory_descriptor: int, name: str,
) -> tuple[int, int, int] | None:
    """Inspect one literal sidecar name without resolving a link target."""
    try:
        observed = os.stat(
            name, dir_fd=directory_descriptor, follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LegacyCampaignRequalificationError(
            "journal auxiliaire legacy F89 invalide"
        ) from exc
    if stat.S_ISLNK(observed.st_mode):
        raise LegacyCampaignRequalificationError(
            "journal auxiliaire legacy F89 lie symboliquement"
        )
    if not stat.S_ISREG(observed.st_mode):
        raise LegacyCampaignRequalificationError(
            "journal auxiliaire legacy F89 invalide"
        )
    return _identity(observed)


def _descriptor_fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _read_stable_descriptor(descriptor: int, label: str) -> bytes:
    """Read one retained regular file without invoking SQLite or changing it."""
    before = os.fstat(descriptor)
    if (not stat.S_ISREG(before.st_mode) or before.st_size < 0
            or before.st_size > _MAX_LEGACY_SQLITE_BYTES):
        raise LegacyCampaignRequalificationError(f"{label} invalide")
    chunks = []
    offset = 0
    while offset < before.st_size:
        chunk = os.pread(descriptor, min(1024 * 1024, before.st_size - offset), offset)
        if not chunk:
            raise LegacyCampaignRequalificationError(f"{label} invalide")
        chunks.append(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    if _descriptor_fingerprint(after) != _descriptor_fingerprint(before):
        raise LegacyCampaignRequalificationError(f"{label} modifie")
    return b"".join(chunks)


def _open_existing_sidecar(
    directory_descriptor: int, name: str,
) -> tuple[int | None, tuple[int, int, int] | None]:
    """Retain an existing sidecar read-only; absence remains absence."""
    expected = _sidecar_state(directory_descriptor, name)
    if expected is None:
        return None, None
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise LegacyCampaignRequalificationError(
            "journal auxiliaire legacy F89 non securisable"
        )
    try:
        descriptor = os.open(
            name, os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_descriptor,
        )
    except OSError as exc:
        raise LegacyCampaignRequalificationError(
            "journal auxiliaire legacy F89 modifie"
        ) from exc
    if _identity(os.fstat(descriptor)) != expected:
        os.close(descriptor)
        raise LegacyCampaignRequalificationError(
            "identite journal auxiliaire legacy F89 modifiee"
        )
    return descriptor, expected


def _require_absent_rollback_journals(directory_descriptor: int) -> None:
    """Reject either fixed rollback journal name without following links."""
    for database in _LEGACY_DATABASES:
        name = f"{database}-journal"
        try:
            os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise LegacyCampaignRequalificationError(
                "journal rollback legacy F89 non verifiable"
            ) from exc
        raise LegacyCampaignRequalificationError(
            "journal rollback legacy F89 present"
        )


def _validate_wal_database_header(descriptor: int) -> None:
    """Require the exact canonical SQLite WAL read/write header modes."""
    try:
        before = os.fstat(descriptor)
        header = os.pread(descriptor, 100, 0)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise LegacyCampaignRequalificationError(
            "entete journal legacy F89 non verifiable"
        ) from exc
    if (_descriptor_fingerprint(before) != _descriptor_fingerprint(after)
            or len(header) != 100 or header[:16] != _SQLITE_HEADER):
        raise LegacyCampaignRequalificationError(
            "entete journal legacy F89 invalide"
        )
    if header[18:20] != bytes((2, 2)):
        raise LegacyCampaignRequalificationError(
            "mode WAL journal legacy F89 invalide"
        )
    encoded_page_size = int.from_bytes(header[16:18], "big")
    page_size = 65_536 if encoded_page_size == 1 else encoded_page_size
    page_count = int.from_bytes(header[28:32], "big")
    if (page_size < 512 or page_size > 65_536
            or page_size & (page_size - 1)
            or before.st_size < page_size
            or before.st_size > _MAX_LEGACY_SQLITE_BYTES
            or before.st_size % page_size
            or page_size - header[20] < 480
            or header[21:24] != bytes((64, 32, 32))
            or not 1 <= page_count <= min(
                before.st_size // page_size, _MAX_BTREE_PAGES,
            )
            or header[72:92] != bytes(20)):
        raise LegacyCampaignRequalificationError(
            "entete journal legacy F89 non canonique"
        )


def _sqlite_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        if offset >= len(payload):
            raise ValueError("varint SQLite tronque")
        byte = payload[offset]
        offset += 1
        if index == 8:
            return (value << 8) | byte, offset
        value = (value << 7) | (byte & 0x7f)
        if byte < 0x80:
            return value, offset
    raise ValueError("varint SQLite invalide")


def _wal_checksum(
    payload: bytes, byteorder: str, seed: tuple[int, int] = (0, 0),
) -> tuple[int, int]:
    if len(payload) % 8:
        raise ValueError("checksum WAL invalide")
    first, second = seed
    for offset in range(0, len(payload), 8):
        left = int.from_bytes(payload[offset:offset + 4], byteorder)
        right = int.from_bytes(payload[offset + 4:offset + 8], byteorder)
        first = (first + left + second) & 0xffffffff
        second = (second + right + first) & 0xffffffff
    return first, second


class _ReadOnlySQLiteSnapshot:
    """Minimal immutable reader for a SQLite table b-tree plus committed WAL."""

    def __init__(self, database: bytes, wal: bytes):
        if len(database) < 100 or database[:16] != _SQLITE_HEADER:
            raise ValueError("entete SQLite invalide")
        encoded_page_size = int.from_bytes(database[16:18], "big")
        self.page_size = 65_536 if encoded_page_size == 1 else encoded_page_size
        if (self.page_size < 512 or self.page_size > 65_536
                or self.page_size & (self.page_size - 1)
                or len(database) % self.page_size):
            raise ValueError("taille de page SQLite invalide")
        self.usable_size = self.page_size - database[20]
        if (self.usable_size < 480 or database[18:20] != bytes((2, 2))
                or database[21:24] != bytes((64, 32, 32))):
            raise ValueError("format SQLite invalide")
        self._database = database
        self._main_pages = len(database) // self.page_size
        self._wal_pages: dict[int, bytes] = {}
        header_pages = int.from_bytes(database[28:32], "big")
        self.page_count = header_pages or self._main_pages
        if (self.page_count > self._main_pages
                or self.page_count > _MAX_BTREE_PAGES):
            raise ValueError("taille SQLite invalide")
        if wal:
            self._apply_wal(wal)
        logical_header = self.page(1)
        if (logical_header[:16] != _SQLITE_HEADER
                or int.from_bytes(logical_header[16:18], "big")
                not in {1, self.page_size}
                or logical_header[18:20] != bytes((2, 2))
                or self.page_size - logical_header[20] != self.usable_size
                or logical_header[21:24] != bytes((64, 32, 32))
                or int.from_bytes(logical_header[28:32], "big")
                != self.page_count
                or int.from_bytes(logical_header[44:48], "big")
                not in {1, 2, 3, 4}
                or logical_header[56:60] != b"\0\0\0\1"
                or logical_header[72:92] != bytes(20)):
            raise ValueError("entete SQLite logique invalide")

    def _apply_wal(self, wal: bytes) -> None:
        if len(wal) < 32:
            raise ValueError("entete WAL invalide")
        magic = int.from_bytes(wal[:4], "big")
        if magic not in {0x377F0682, 0x377F0683}:
            raise ValueError("magic WAL invalide")
        if (int.from_bytes(wal[4:8], "big") != 3_007_000
                or int.from_bytes(wal[8:12], "big") != self.page_size):
            raise ValueError("format WAL invalide")
        byteorder = "big" if magic & 1 else "little"
        checksum = _wal_checksum(wal[:24], byteorder)
        if checksum != (
            int.from_bytes(wal[24:28], "big"),
            int.from_bytes(wal[28:32], "big"),
        ):
            raise ValueError("checksum entete WAL invalide")
        salts = wal[16:24]
        frame_size = 24 + self.page_size
        frames: list[tuple[int, bytes]] = []
        committed_frames = 0
        committed_pages = 0
        offset = 32
        while offset + frame_size <= len(wal):
            if len(frames) >= _MAX_WAL_FRAMES:
                raise ValueError("frames WAL hors borne")
            header = wal[offset:offset + 24]
            page = wal[offset + 24:offset + frame_size]
            if header[8:16] != salts:
                break
            observed = _wal_checksum(header[:8] + page, byteorder, checksum)
            if observed != (
                int.from_bytes(header[16:20], "big"),
                int.from_bytes(header[20:24], "big"),
            ):
                break
            checksum = observed
            page_number = int.from_bytes(header[:4], "big")
            if page_number < 1:
                break
            frames.append((page_number, page))
            transaction_size = int.from_bytes(header[4:8], "big")
            if transaction_size:
                committed_frames = len(frames)
                committed_pages = transaction_size
            offset += frame_size
        if not committed_frames:
            return
        if (committed_pages < 1
                or committed_pages > _MAX_BTREE_PAGES
                or committed_pages * self.page_size > _MAX_LEGACY_SQLITE_BYTES):
            raise ValueError("taille WAL invalide")
        for page_number, page in frames[:committed_frames]:
            if page_number <= committed_pages:
                self._wal_pages[page_number] = page
        self.page_count = committed_pages

    def page(self, page_number: int) -> bytes:
        if not 1 <= page_number <= self.page_count:
            raise ValueError("page SQLite hors borne")
        if page_number in self._wal_pages:
            return self._wal_pages[page_number]
        if page_number > self._main_pages:
            raise ValueError("page SQLite absente")
        offset = (page_number - 1) * self.page_size
        return self._database[offset:offset + self.page_size]

    def _leaf_cell(
        self, page: bytes, pointer: int,
    ) -> tuple[int, int, int, int, int | None]:
        """Parse bounded leaf metadata without materializing its record."""
        payload_size, cursor = _sqlite_varint(page, pointer)
        _row_id, cursor = _sqlite_varint(page, cursor)
        if payload_size > _MAX_SQLITE_RECORD_BYTES:
            raise ValueError("payload SQLite hors borne")
        maximum_local = self.usable_size - 35
        if payload_size <= maximum_local:
            local_size = payload_size
        else:
            minimum_local = ((self.usable_size - 12) * 32 // 255) - 23
            candidate = minimum_local + (
                (payload_size - minimum_local) % (self.usable_size - 4)
            )
            local_size = candidate if candidate <= maximum_local else minimum_local
        local_end = cursor + local_size
        if local_end > self.usable_size:
            raise ValueError("payload SQLite tronque")
        remaining = payload_size - local_size
        if not remaining:
            return pointer, local_end, payload_size, cursor, None
        if local_end + 4 > self.usable_size:
            raise ValueError("overflow SQLite absent")
        overflow = int.from_bytes(
            page[local_end:local_end + 4], "big",
        )
        if overflow == 0:
            raise ValueError("overflow SQLite absent")
        return pointer, local_end + 4, payload_size, cursor, overflow

    def _leaf_payload(
        self, page: bytes, cell: tuple[int, int, int, int, int | None],
        claimed_overflow: set[int],
    ) -> bytes:
        _pointer, _cell_end, payload_size, cursor, overflow = cell
        local_size = _cell_end - cursor - (4 if overflow is not None else 0)
        result = bytearray(page[cursor:cursor + local_size])
        remaining = payload_size - local_size
        if not remaining:
            return bytes(result)
        required_pages = (remaining + self.usable_size - 5) // (self.usable_size - 4)
        if required_pages > min(_MAX_OVERFLOW_PAGES, self.page_count):
            raise ValueError("overflow SQLite hors borne")
        visited = set()
        while remaining:
            if overflow in visited or overflow in claimed_overflow:
                raise ValueError("cycle ou partage overflow SQLite")
            visited.add(overflow)
            claimed_overflow.add(overflow)
            overflow_page = self.page(overflow)
            take = min(remaining, self.usable_size - 4)
            result.extend(overflow_page[4:4 + take])
            remaining -= take
            overflow = int.from_bytes(overflow_page[:4], "big")
            if remaining and overflow == 0:
                raise ValueError("overflow SQLite tronque")
        if overflow != 0:
            raise ValueError("overflow SQLite surnumeraire")
        return bytes(result)

    @staticmethod
    def _serial_length(serial_type: int) -> int:
        sizes = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8, 7: 8, 8: 0, 9: 0}
        if serial_type in sizes:
            return sizes[serial_type]
        if serial_type in {10, 11}:
            raise ValueError("type serie SQLite reserve")
        return (serial_type - 12) // 2

    @classmethod
    def _record(cls, payload: bytes) -> tuple[object, ...]:
        if len(payload) > _MAX_SQLITE_RECORD_BYTES:
            raise ValueError("record SQLite hors borne")
        header_size, cursor = _sqlite_varint(payload, 0)
        if header_size < cursor or header_size > len(payload):
            raise ValueError("entete record SQLite invalide")
        serial_types = []
        while cursor < header_size:
            if len(serial_types) >= _MAX_SQLITE_RECORD_COLUMNS:
                raise ValueError("colonnes record SQLite hors borne")
            serial_type, cursor = _sqlite_varint(payload, cursor)
            serial_types.append(serial_type)
        if cursor != header_size:
            raise ValueError("entete record SQLite tronque")
        values = []
        body = header_size
        for serial_type in serial_types:
            length = cls._serial_length(serial_type)
            if length > _MAX_SQLITE_RECORD_BYTES:
                raise ValueError("champ record SQLite hors borne")
            end = body + length
            if end > len(payload):
                raise ValueError("record SQLite tronque")
            raw = payload[body:end]
            body = end
            if serial_type == 0:
                value = None
            elif 1 <= serial_type <= 6:
                value = int.from_bytes(raw, "big", signed=True)
            elif serial_type == 7:
                value = struct.unpack(">d", raw)[0]
            elif serial_type == 8:
                value = 0
            elif serial_type == 9:
                value = 1
            elif serial_type % 2:
                value = raw.decode("utf-8")
            else:
                value = raw
            values.append(value)
        if body != len(payload):
            raise ValueError("record SQLite surnumeraire")
        return tuple(values)

    def table_rows(self, root_page: int) -> tuple[tuple[object, ...], ...]:
        rows = []
        pending = [root_page]
        visited = set()
        scheduled = {root_page}
        claimed_overflow: set[int] = set()
        total_cells = 0
        while pending:
            if len(visited) >= _MAX_BTREE_PAGES:
                raise ValueError("pages btree SQLite hors borne")
            page_number = pending.pop()
            if page_number in visited:
                raise ValueError("cycle btree SQLite")
            visited.add(page_number)
            page = self.page(page_number)
            header = 100 if page_number == 1 else 0
            page_type = page[header]
            if page_type not in {0x05, 0x0d}:
                raise ValueError("page btree SQLite invalide")
            header_size = 12 if page_type == 0x05 else 8
            cell_count = int.from_bytes(page[header + 3:header + 5], "big")
            if cell_count > _MAX_BTREE_CELLS_PER_PAGE:
                raise ValueError("cellules SQLite hors borne")
            total_cells += cell_count
            if total_cells > _MAX_BTREE_CELLS:
                raise ValueError("table SQLite hors borne")
            pointer_start = header + header_size
            if pointer_start + 2 * cell_count > self.usable_size:
                raise ValueError("cellules SQLite hors borne")
            content_start = int.from_bytes(page[header + 5:header + 7], "big")
            if content_start == 0:
                content_start = 65_536
            pointer_end = pointer_start + 2 * cell_count
            if not pointer_end <= content_start <= self.usable_size:
                raise ValueError("contenu cellules SQLite invalide")
            pointers = [
                int.from_bytes(page[offset:offset + 2], "big")
                for offset in range(pointer_start, pointer_start + 2 * cell_count, 2)
            ]
            if (len(set(pointers)) != len(pointers)
                    or any(pointer < content_start or pointer >= self.usable_size
                           for pointer in pointers)):
                raise ValueError("pointeur SQLite invalide")
            if page_type == 0x0d:
                cells = [self._leaf_cell(page, pointer) for pointer in pointers]
                spans = sorted((cell[0], cell[1]) for cell in cells)
                if any(end <= start for start, end in spans) or any(
                    right[0] < left[1] for left, right in zip(spans, spans[1:])
                ):
                    raise ValueError("cellules SQLite chevauchantes")
                if len(rows) + len(cells) > _MAX_BTREE_CELLS:
                    raise ValueError("lignes SQLite hors borne")
                for cell in cells:
                    payload = self._leaf_payload(page, cell, claimed_overflow)
                    rows.append(self._record(payload))
                continue
            children = []
            spans = []
            for pointer in pointers:
                if pointer + 4 > self.usable_size:
                    raise ValueError("enfant btree SQLite invalide")
                _key, cell_end = _sqlite_varint(page, pointer + 4)
                if cell_end > self.usable_size:
                    raise ValueError("enfant btree SQLite invalide")
                spans.append((pointer, cell_end))
                children.append(int.from_bytes(page[pointer:pointer + 4], "big"))
            spans.sort()
            if any(end <= start for start, end in spans) or any(
                right[0] < left[1] for left, right in zip(spans, spans[1:])
            ):
                raise ValueError("cellules SQLite chevauchantes")
            children.append(int.from_bytes(page[header + 8:header + 12], "big"))
            if any(not 1 <= child <= self.page_count for child in children):
                raise ValueError("enfant btree SQLite hors borne")
            if (len(set(children)) != len(children)
                    or any(child in scheduled for child in children)
                    or len(scheduled) + len(children) > _MAX_BTREE_PAGES):
                raise ValueError("graphe btree SQLite invalide")
            scheduled.update(children)
            pending.extend(reversed(children))
        return tuple(rows)


def _snapshot_table_rows(
    snapshot: _ReadOnlySQLiteSnapshot, table: str, *, required: bool = True,
) -> tuple[tuple[object, ...], ...] | None:
    """Resolve one literal table root from the bounded immutable schema."""
    matches = tuple(
        row for row in snapshot.table_rows(1)
        if len(row) >= 2 and row[1] == table
    )
    if not matches and not required:
        return None
    if (len(matches) != 1 or len(matches[0]) != 5
            or matches[0][0] != "table" or matches[0][2] != table
            or type(matches[0][3]) is not int
            or not 1 <= matches[0][3] <= snapshot.page_count
            or not isinstance(matches[0][4], str)):
        raise ValueError(f"schema {table} SQLite invalide")
    return snapshot.table_rows(matches[0][3])


def _legacy_f89_spec_from_snapshot(snapshot: _ReadOnlySQLiteSnapshot) -> CampaignSpec:
    rows = _snapshot_table_rows(snapshot, "campaigns")
    assert rows is not None
    matches = tuple(
        row for row in rows
        if len(row) >= 4 and row[0] == LEGACY_F89_CAMPAIGN_ID
    )
    if len(matches) != 1:
        raise ValueError("campagne legacy F89 absente")
    row = matches[0]
    if (row[1] != LEDGER_CONTRACT or not isinstance(row[2], str)
            or not isinstance(row[3], str)):
        raise ValueError("binding campagne legacy F89 invalide")
    encoded = json.loads(row[3])
    if not isinstance(encoded, dict):
        raise ValueError("binding campagne legacy F89 invalide")
    encoded_digest = _digest(encoded)
    raw = dict(encoded)
    if set(raw) != {"contract", *CampaignSpec.__dataclass_fields__}:
        raise ValueError("binding campagne legacy F89 invalide")
    contract = raw.pop("contract")
    raw["blockers"] = tuple(raw["blockers"])
    raw["waves"] = tuple(tuple(wave) for wave in raw["waves"])
    raw["dependencies"] = tuple(tuple(edge) for edge in raw["dependencies"])
    spec = CampaignSpec(**raw)
    if (contract != CAMPAIGN_CONTRACT
            or row[2] != LEGACY_F89_BINDING_DIGEST
            or row[2] != spec.binding_digest
            or row[2] != encoded_digest
            or spec.campaign_id != LEGACY_F89_CAMPAIGN_ID
            or spec.command_id != LEGACY_F89_CAMPAIGN_ID
            or spec.epic_id != LEGACY_F89_PARENT_ID
            or tuple(sorted(issue for wave in spec.waves for issue in wave))
            != LEGACY_F89_CHILDREN):
        raise ValueError("binding campagne legacy F89 invalide")
    return spec


def _stable_database_snapshot(
    directory_descriptor: int, database_descriptor: int, database_name: str,
) -> _ReadOnlySQLiteSnapshot:
    """Read one committed database+WAL image without entering SQLite."""
    if database_name not in _LEGACY_DATABASES:
        raise LegacyCampaignRequalificationError("journal legacy F89 inattendu")
    wal_descriptor = None
    wal_identity = None
    try:
        _validate_wal_database_header(database_descriptor)
        _require_absent_rollback_journals(directory_descriptor)
        wal_descriptor, wal_identity = _open_existing_sidecar(
            directory_descriptor, f"{database_name}-wal",
        )
        database = _read_stable_descriptor(
            database_descriptor, f"journal {database_name} legacy F89",
        )
        database_fingerprint = _descriptor_fingerprint(os.fstat(database_descriptor))
        wal = (
            b"" if wal_descriptor is None
            else _read_stable_descriptor(
                wal_descriptor, f"WAL {database_name} legacy F89",
            )
        )
        wal_fingerprint = (
            None if wal_descriptor is None
            else _descriptor_fingerprint(os.fstat(wal_descriptor))
        )
        snapshot = _ReadOnlySQLiteSnapshot(database, wal)
        if (_descriptor_fingerprint(os.fstat(database_descriptor))
                != database_fingerprint
                or (wal_descriptor is not None
                    and _descriptor_fingerprint(os.fstat(wal_descriptor))
                    != wal_fingerprint)
                or _sidecar_state(
                    directory_descriptor, f"{database_name}-wal",
                ) != wal_identity):
            raise LegacyCampaignRequalificationError(
                "identite journal auxiliaire legacy F89 modifiee"
            )
        return snapshot
    finally:
        if wal_descriptor is not None:
            os.close(wal_descriptor)


@dataclass(frozen=True)
class _LegacyF89LocalEvidence:
    """All local facts required by the one exact F89 compatibility branch."""

    spec: CampaignSpec
    campaign_coordinates: tuple[object, ...]
    merges: tuple[tuple[str, int, str, str, str], ...]
    existing_requalification: LegacyParentRequalification | None


def _existing_legacy_requalification_from_snapshot(
    snapshot: _ReadOnlySQLiteSnapshot,
) -> LegacyParentRequalification | None:
    rows = _snapshot_table_rows(
        snapshot, "campaign_legacy_parent_requalifications", required=False,
    )
    if rows is None:
        return None
    matches = tuple(
        row for row in rows
        if len(row) >= 1 and row[0] == LEGACY_F89_CAMPAIGN_ID
    )
    if not matches:
        return None
    if len(matches) != 1 or len(matches[0]) != 4:
        raise LegacyCampaignRequalificationError(
            "requalification legacy F89 corrompue"
        )
    row = matches[0]
    try:
        raw = json.loads(row[2])
        raw["criteria"] = tuple(tuple(item) for item in raw["criteria"])
        record = LegacyParentRequalification(**raw)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise LegacyCampaignRequalificationError(
            "requalification legacy F89 corrompue"
        ) from None
    if (record.audit_digest != row[1] or record.recorded_at != row[3]):
        raise LegacyCampaignRequalificationError(
            "digest requalification legacy F89 modifie"
        )
    return record


def _legacy_f89_local_evidence_from_snapshots(
    campaign_snapshot: _ReadOnlySQLiteSnapshot,
    receipt_snapshot: _ReadOnlySQLiteSnapshot,
) -> _LegacyF89LocalEvidence:
    """Validate budget, graph state, and both copies of all three merges."""
    try:
        try:
            spec = _legacy_f89_spec_from_snapshot(campaign_snapshot)
        except (TypeError, UnicodeError, ValueError, struct.error):
            raise LegacyCampaignRequalificationError(
                "binding campagne legacy F89 invalide"
            ) from None
        campaign_rows = _snapshot_table_rows(campaign_snapshot, "campaigns")
        assert campaign_rows is not None
        campaigns = tuple(
            row for row in campaign_rows
            if len(row) >= 1 and row[0] == LEGACY_F89_CAMPAIGN_ID
        )
        if len(campaigns) != 1 or len(campaigns[0]) < 9:
            raise LegacyCampaignRequalificationError(
                "campagne legacy F89 absente"
            )
        campaign = campaigns[0]
        if (type(campaign[8]) is not int
                or campaign[8] != LEGACY_F89_BUDGET_SPENT):
            raise LegacyCampaignRequalificationError("budget legacy F89 modifie")

        existing = _existing_legacy_requalification_from_snapshot(campaign_snapshot)
        coordinates = (campaign[4], campaign[5], campaign[7], campaign[8])
        allowed = {
            ("suspended", "host_or_effect_failure", 2, LEGACY_F89_BUDGET_SPENT),
        }
        if existing is not None:
            allowed.update({
                ("running", "campaign_revalidated", 2, LEGACY_F89_BUDGET_SPENT),
                ("running", None, 2, LEGACY_F89_BUDGET_SPENT),
                ("completed", None, 2, LEGACY_F89_BUDGET_SPENT),
            })
        if coordinates not in allowed:
            raise LegacyCampaignRequalificationError(
                "campagne legacy F89 non eligible"
            )

        issue_rows = _snapshot_table_rows(campaign_snapshot, "campaign_issues")
        assert issue_rows is not None
        issues = tuple(
            row for row in issue_rows
            if len(row) >= 1 and row[0] == LEGACY_F89_CAMPAIGN_ID
        )
        expected_waves = {
            issue_id: wave_index
            for wave_index, wave in enumerate(spec.waves)
            for issue_id in wave
        }
        if (len(issues) != len(LEGACY_F89_CHILDREN)
                or tuple(sorted(row[1] for row in issues if len(row) >= 2))
                != LEGACY_F89_CHILDREN):
            raise LegacyCampaignRequalificationError(
                "enfants legacy F89 non termines"
            )
        attempts: dict[str, int] = {}
        for row in issues:
            if (len(row) < 6 or row[2] != expected_waves.get(row[1])
                    or type(row[3]) is not int
                    or not 1 <= row[3] <= spec.max_attempts_per_issue
                    or row[4] != "done" or row[5] != "merged"):
                raise LegacyCampaignRequalificationError(
                    "enfants legacy F89 non termines"
                )
            attempts[row[1]] = row[3]

        effect_rows = _snapshot_table_rows(campaign_snapshot, "campaign_effects")
        assert effect_rows is not None
        merge_rows = tuple(
            row for row in effect_rows
            if len(row) >= 4 and row[0] == LEGACY_F89_CAMPAIGN_ID
            and row[3] == "merge"
        )
        if len(merge_rows) != len(LEGACY_F89_CHILDREN):
            raise LegacyCampaignRequalificationError(
                "recu merge legacy F89 invalide"
            )
        merges = []
        for issue_id in LEGACY_F89_CHILDREN:
            matches = tuple(row for row in merge_rows if row[1] == issue_id)
            attempt = attempts[issue_id]
            effect_id, work_id = CampaignCoordinator._identity(
                spec, issue_id, attempt, "merge",
            )
            if len(matches) != 1 or len(matches[0]) < 12:
                raise LegacyCampaignRequalificationError(
                    "recu merge legacy F89 invalide"
                )
            row = matches[0]
            if (row[2] != attempt or row[4:7] != (
                    effect_id, work_id, "completed",
                ) or row[8:12] != (0, None, None, None)
                    or (len(row) >= 13
                        and row[12] not in {"not-applicable", "unknown"})):
                raise LegacyCampaignRequalificationError(
                    "recu merge legacy F89 invalide"
                )
            _safe_digest(row[7], "recu merge legacy F89")
            merges.append((issue_id, attempt, effect_id, work_id, row[7]))

        primitive_rows = _snapshot_table_rows(
            receipt_snapshot, "primitive_receipts",
        )
        assert primitive_rows is not None
        primitive_merges = tuple(
            row for row in primitive_rows
            if len(row) >= 6 and row[2] == LEGACY_F89_CAMPAIGN_ID
            and row[5] == "merge"
        )
        if len(primitive_merges) != len(merges):
            raise LegacyCampaignRequalificationError(
                "recu merge legacy F89 invalide"
            )
        for issue_id, attempt, effect_id, _work_id, proof_digest in merges:
            matches = tuple(
                row for row in primitive_merges if len(row) >= 4 and row[3] == issue_id
            )
            if (len(matches) != 1 or len(matches[0]) < 8
                    or matches[0][:8] != (
                        effect_id, LEGACY_F89_BINDING_DIGEST,
                        LEGACY_F89_CAMPAIGN_ID, issue_id, attempt, "merge",
                        "completed", proof_digest,
                    )):
                raise LegacyCampaignRequalificationError(
                    "recu merge legacy F89 invalide"
                )
        return _LegacyF89LocalEvidence(
            spec, coordinates, tuple(merges), existing,
        )
    except LegacyCampaignRequalificationError:
        raise
    except (KeyError, OSError, TypeError, UnicodeError, ValueError, struct.error):
        raise LegacyCampaignRequalificationError(
            "preuves locales legacy F89 invalides"
        ) from None


def _stored_legacy_f89_local_evidence(
    directory_descriptor: int, descriptors: dict[str, int],
) -> _LegacyF89LocalEvidence:
    """Stage every local F89 proof using retained descriptors, never SQLite."""
    try:
        campaign_snapshot = _stable_database_snapshot(
            directory_descriptor, descriptors["campaign.sqlite3"],
            "campaign.sqlite3",
        )
        receipt_snapshot = _stable_database_snapshot(
            directory_descriptor, descriptors["primitive-receipts.sqlite3"],
            "primitive-receipts.sqlite3",
        )
        return _legacy_f89_local_evidence_from_snapshots(
            campaign_snapshot, receipt_snapshot,
        )
    except LegacyCampaignRequalificationError:
        raise
    except (KeyError, OSError, TypeError, UnicodeError, ValueError, struct.error):
        raise LegacyCampaignRequalificationError(
            "preuves locales legacy F89 invalides"
        ) from None


def _preflight_legacy_f89_evidence(
    path: str | Path,
) -> tuple[
    Path, _LegacyF89LocalEvidence, dict[str, tuple[int, int, int]],
]:
    """Stage every committed local proof before locks, sidecars, or SQLite."""
    raw = _raw_path_without_links(path, "repertoire campagne F89")
    directory_descriptor = _open_no_follow(
        raw, directory=True, label="repertoire campagne F89",
    )
    descriptors: dict[str, int] = {}
    try:
        directory_identity = _identity(os.fstat(directory_descriptor))
        database_paths = {}
        database_identities = {}
        for name in _LEGACY_DATABASES:
            database = _raw_path_without_links(
                raw / name, "journal legacy F89",
            )
            database_paths[name] = database
            descriptors[name] = _open_no_follow(
                database, directory=False, label="journal legacy F89",
            )
            database_identities[name] = _identity(os.fstat(descriptors[name]))
        for descriptor in descriptors.values():
            _validate_wal_database_header(descriptor)
        _require_absent_rollback_journals(directory_descriptor)
        evidence = _stored_legacy_f89_local_evidence(
            directory_descriptor, descriptors,
        )
        _raw_path_without_links(raw, "repertoire campagne F89")
        if (_identity(os.lstat(raw)) != directory_identity
                or any(
                    _identity(os.lstat(database_paths[name])) != identity
                    or _identity(os.fstat(descriptors[name])) != identity
                    for name, identity in database_identities.items()
                )):
            raise LegacyCampaignRequalificationError(
                "identite journaux legacy F89 invalide"
            )
        _require_absent_rollback_journals(directory_descriptor)
        return raw, evidence, database_identities
    finally:
        for descriptor in descriptors.values():
            os.close(descriptor)
        os.close(directory_descriptor)


def _open_or_create_sidecar(
    directory_descriptor: int, name: str,
    expected: tuple[int, int, int] | None,
) -> tuple[int, bool]:
    """Retain an existing sidecar or atomically reserve an absent clean one."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise LegacyCampaignRequalificationError(
            "journal auxiliaire legacy F89 non securisable"
        )
    flags = os.O_RDWR | no_follow | getattr(os, "O_CLOEXEC", 0)
    created = expected is None
    if created:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(
            name, flags, 0o600, dir_fd=directory_descriptor,
        )
    except OSError as exc:
        raise LegacyCampaignRequalificationError(
            "journal auxiliaire legacy F89 modifie"
        ) from exc
    observed = _identity(os.fstat(descriptor))
    if (expected is not None and observed != expected) or observed[2] != stat.S_IFREG:
        os.close(descriptor)
        raise LegacyCampaignRequalificationError(
            "identite journal auxiliaire legacy F89 modifiee"
        )
    return descriptor, created


@contextmanager
def _validated_campaign_directory(
    path: str | Path, *, proven_evidence: _LegacyF89LocalEvidence,
    proven_database_identities: dict[str, tuple[int, int, int]],
    pre_effect_guard,
):
    """Serialize already-staged F89 evidence against SQLite finalizers."""
    raw = _raw_path_without_links(path, "repertoire campagne F89")
    try:
        with _sqlite_identity_window(True):
            with _validated_campaign_directory_locked(
                raw, proven_spec=proven_evidence.spec,
                proven_database_identities=proven_database_identities,
                proven_evidence=proven_evidence,
                pre_effect_guard=pre_effect_guard,
            ) as validated:
                yield validated
    except CampaignError as exc:
        raise LegacyCampaignRequalificationError(str(exc)) from None


@contextmanager
def _validated_campaign_directory_locked(
    path: str | Path, *, proven_spec: CampaignSpec,
    proven_database_identities: dict[str, tuple[int, int, int]],
    proven_evidence: _LegacyF89LocalEvidence,
    pre_effect_guard,
):
    """Retain exact database and WAL/SHM identities through every SQLite open."""
    raw = _raw_path_without_links(path, "repertoire campagne F89")
    directory_descriptor = _open_no_follow(
        raw, directory=True, label="repertoire campagne F89",
    )
    descriptors: dict[str, int] = {}
    anchors: list[sqlite3.Connection] = []
    created_sidecars: dict[str, tuple[int, int, int]] = {}
    sidecars_ready = False
    try:
        directory_identity = _identity(os.fstat(directory_descriptor))
        for name in _LEGACY_DATABASES:
            candidate = _raw_path_without_links(raw / name, "journal legacy F89")
            descriptors[name] = _open_no_follow(
                candidate, directory=False, label="journal legacy F89",
            )
        for descriptor in descriptors.values():
            _validate_wal_database_header(descriptor)
        _require_absent_rollback_journals(directory_descriptor)
        if (any(
                _identity(os.fstat(descriptors[name])) != identity
                for name, identity in proven_database_identities.items()
            )
                or _stored_legacy_f89_local_evidence(
                    directory_descriptor, descriptors,
                ) != proven_evidence):
            raise LegacyCampaignRequalificationError(
                "preuves locales legacy F89 modifiees"
            )
        # The tracker snapshot and its atomic-close audit are also staged before
        # the first sidecar allocation.  Re-observe them at the last effect-free
        # boundary so a stale preflight cannot authorize local mutation.
        pre_effect_guard()
        sidecar_states = {
            f"{database}{suffix}": _sidecar_state(
                directory_descriptor, f"{database}{suffix}",
            )
            for database in _LEGACY_DATABASES
            for suffix in _SQLITE_SIDECAR_SUFFIXES
        }
        _require_absent_rollback_journals(directory_descriptor)
        for name, expected in sidecar_states.items():
            descriptor, created = _open_or_create_sidecar(
                directory_descriptor, name, expected,
            )
            descriptors[name] = descriptor
            if created:
                created_sidecars[name] = _identity(os.fstat(descriptor))
        sidecars_ready = True
        protected_identities = {
            name: _identity(os.fstat(descriptor))
            for name, descriptor in descriptors.items()
        }
        database_identities = {
            name: protected_identities[name]
            for name in _LEGACY_DATABASES
        }
        sidecar_identities = {
            database: tuple(
                protected_identities[f"{database}{suffix}"]
                for suffix in _SQLITE_SIDECAR_SUFFIXES
            )
            for database in _LEGACY_DATABASES
        }

        def revalidate() -> None:
            _raw_path_without_links(raw, "repertoire campagne F89")
            try:
                if (_identity(os.fstat(directory_descriptor)) != directory_identity
                        or _identity(os.lstat(raw)) != directory_identity):
                    raise LegacyCampaignRequalificationError(
                        "identite repertoire campagne F89 modifiee"
                    )
                for name, descriptor in descriptors.items():
                    auxiliary = any(
                        name.endswith(suffix)
                        for suffix in _SQLITE_SIDECAR_SUFFIXES
                    )
                    label = (
                        "journal auxiliaire legacy F89" if auxiliary
                        else "journal legacy F89"
                    )
                    candidate = _raw_path_without_links(
                        raw / name, label,
                    )
                    identity = protected_identities[name]
                    if (_identity(os.fstat(descriptor)) != identity
                            or _identity(os.lstat(candidate)) != identity
                            or _identity(os.stat(
                                name, dir_fd=directory_descriptor,
                                follow_symlinks=False,
                            )) != identity):
                        raise LegacyCampaignRequalificationError(
                            f"identite {label} modifiee"
                        )
            except OSError as exc:
                raise LegacyCampaignRequalificationError(
                    "identite journaux legacy F89 invalide"
                ) from exc

        def binding_guard() -> None:
            revalidate()
            for database in _LEGACY_DATABASES:
                _validate_wal_database_header(descriptors[database])
            _require_absent_rollback_journals(directory_descriptor)
            if (_stored_legacy_f89_local_evidence(
                directory_descriptor, descriptors,
            ) != proven_evidence):
                raise LegacyCampaignRequalificationError(
                    "preuves locales legacy F89 modifiees"
                )

        binding_guard()
        for database in _LEGACY_DATABASES:
            identities = (
                database_identities[database], *sidecar_identities[database],
            )
            with _sqlite_identity_window(True) as open_descriptors:
                binding_guard()
                connection = sqlite3.connect(raw / database, timeout=10.0)
                try:
                    if connection.execute("PRAGMA journal_mode").fetchone() != ("wal",):
                        raise LegacyCampaignRequalificationError(
                            "mode WAL journal legacy F89 invalide"
                        )
                    connection.execute("PRAGMA schema_version").fetchone()
                    revalidate()
                    _verify_guarded_sqlite_descriptors(
                        open_descriptors, identities,
                    )
                except BaseException:
                    connection.close()
                    raise
            anchors.append(connection)
        yield raw, binding_guard, database_identities, sidecar_identities, proven_spec
        revalidate()
    except (CampaignError, sqlite3.DatabaseError) as exc:
        raise LegacyCampaignRequalificationError(str(exc)) from None
    finally:
        for connection in reversed(anchors):
            connection.close()
        for descriptor in descriptors.values():
            os.close(descriptor)
        if not sidecars_ready:
            for name, identity in created_sidecars.items():
                try:
                    if _identity(os.stat(
                        name, dir_fd=directory_descriptor, follow_symlinks=False,
                    )) == identity:
                        os.unlink(name, dir_fd=directory_descriptor)
                except OSError:
                    pass
        os.close(directory_descriptor)


def _read_mapping(path: str | Path) -> object:
    raw = _raw_path_without_links(path, "mapping legacy F89")
    descriptor = _open_no_follow(raw, directory=False, label="mapping legacy F89")
    try:
        size = os.fstat(descriptor).st_size
        if size > 64 * 1024:
            raise LegacyCampaignRequalificationError("mapping legacy F89 invalide")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(64 * 1024 + 1)
        if len(payload) > 64 * 1024:
            raise LegacyCampaignRequalificationError("mapping legacy F89 invalide")
        return json.loads(payload.decode("utf-8"))
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _closure_digest(outcome: object) -> str:
    return _digest({
        "contract": "foundry-provider-epic-closure-audit.v1",
        "receipt": asdict(outcome.receipt),
        "closed_parent_version": outcome.closed_parent_version,
        "audit_id": outcome.audit_id,
    })


def _validated_requalification_request(
    campaign_directory: str | Path,
    mapping: object,
    actor: object,
) -> tuple[Path, tuple[tuple[str, str, str], ...]]:
    """Prove the one-off request coordinates before any SQLite boundary."""
    candidate = Path(campaign_directory).expanduser()
    if candidate.name != LEGACY_F89_CAMPAIGN_ID:
        raise LegacyCampaignRequalificationError(
            "repertoire campagne F89 inattendu"
        )
    try:
        _safe_identifier(actor, "acteur requalification legacy F89")
        if (not isinstance(mapping, tuple) or len(mapping) != len(LEGACY_F89_CHILDREN)
                or any(not isinstance(item, tuple) or len(item) != 3 for item in mapping)):
            raise ValueError("mapping legacy F89 invalide")
        criterion_ids = []
        for criterion_id, criterion_digest, issue_id in mapping:
            _safe_identifier(criterion_id, "critere legacy F89")
            _safe_digest(criterion_digest, "digest critere legacy F89")
            if issue_id not in LEGACY_F89_CHILDREN:
                raise ValueError("enfant legacy F89 invalide")
            criterion_ids.append(criterion_id)
        normalized = tuple(sorted(mapping, key=lambda item: item[0]))
        if (len(criterion_ids) != len(set(criterion_ids))
                or tuple(sorted(item[2] for item in normalized)) != LEGACY_F89_CHILDREN):
            raise ValueError("mapping legacy F89 invalide")
    except ValueError as exc:
        raise LegacyCampaignRequalificationError(str(exc)) from None
    return candidate, normalized


def _legacy_f89_record(
    spec: CampaignSpec,
    qualified: tuple[tuple[str, str, str, str, str], ...], *,
    actor: str, tracker, recorded_at: int,
) -> LegacyParentRequalification:
    """Bind immutable local merges to the exact provider parent and close audit."""
    if (spec.binding_digest != LEGACY_F89_BINDING_DIGEST
            or spec.epic_id != LEGACY_F89_PARENT_ID
            or tuple(sorted(issue for wave in spec.waves for issue in wave))
            != LEGACY_F89_CHILDREN):
        raise LegacyCampaignRequalificationError(
            "binding campagne legacy F89 invalide"
        )
    parent = tracker.get_issue(LEGACY_F89_PARENT_ID)
    body = getattr(parent, "body", None)
    parent_type = getattr(parent, "type", None)
    mapping = tuple(item[:3] for item in qualified)
    try:
        current_criteria = acceptance_criteria(body) if isinstance(body, str) else []
        criterion_coordinates = {
            (criterion["id"], criterion["digest"])
            for criterion in current_criteria
        }
        current_children = tuple(sorted(
            relation.target for relation in getattr(parent, "links", ())
            if getattr(relation, "type", None) == "parent-of"
        ))
    except (AttributeError, KeyError, TypeError, ValueError):
        raise LegacyCampaignRequalificationError(
            "snapshot ou mapping legacy F89 invalide"
        ) from None
    if (getattr(parent, "id", None) != LEGACY_F89_PARENT_ID
            or not isinstance(parent_type, str) or parent_type.casefold() != "epic"
            or getattr(parent, "state", None) != "done"
            or getattr(parent, "version", None) != 6
            or current_children != LEGACY_F89_CHILDREN
            or len(mapping) != len(current_criteria)
            or tuple(sorted(item[2] for item in mapping)) != LEGACY_F89_CHILDREN
            or {(item[0], item[1]) for item in mapping}
            != criterion_coordinates):
        raise LegacyCampaignRequalificationError(
            "snapshot ou mapping legacy F89 invalide"
        )

    try:
        project = write._epic_closure_project(tracker)
        outcome = write._validate_epic_outcome(
            tracker.get_epic_closure(project, LEGACY_F89_PARENT_ID),
            project=project, parent=parent, expected=None,
        )
    except (AttributeError, SystemExit, ValueError):
        raise LegacyCampaignRequalificationError(
            "recu atomique de cloture legacy F89 invalide"
        ) from None
    if (outcome.receipt.parent_version != 5
            or outcome.closed_parent_version != 6
            or tuple(item.id for item in outcome.receipt.children)
            != LEGACY_F89_CHILDREN):
        raise LegacyCampaignRequalificationError(
            "transition de cloture legacy F89 invalide"
        )
    try:
        return LegacyParentRequalification(
            LEGACY_F89_CAMPAIGN_ID, LEGACY_F89_BINDING_DIGEST,
            spec.snapshot_digest, LEGACY_F89_PARENT_ID, 4, 5, 6,
            LEGACY_F89_BUDGET_SPENT, qualified, _digest(body),
            acceptance_digest(current_criteria), _closure_digest(outcome),
            actor, recorded_at,
        )
    except (TypeError, ValueError):
        raise LegacyCampaignRequalificationError(
            "preuve requalification legacy F89 invalide"
        ) from None


def requalify_legacy_f89(
    campaign_directory: str | Path,
    mapping: tuple[tuple[str, str, str], ...],
    *, actor: str, tracker=None, now_ms=None,
) -> LegacyParentRequalification:
    """Qualify only F89 v4->v6 from three merges and the atomic close audit."""
    candidate, normalized_mapping = _validated_requalification_request(
        campaign_directory, mapping, actor,
    )
    raw, proven_evidence, proven_database_identities = (
        _preflight_legacy_f89_evidence(candidate)
    )
    active_tracker = tracker or foundry.tracker()
    recorded_at = int(time.time() * 1000) if now_ms is None else now_ms()
    merge_by_issue = {
        issue_id: (effect_id, proof_digest)
        for issue_id, _attempt, effect_id, _work_id, proof_digest
        in proven_evidence.merges
    }
    proven_criteria = tuple(
        (
            criterion_id, criterion_digest, issue_id,
            merge_by_issue[issue_id][0], merge_by_issue[issue_id][1],
        )
        for criterion_id, criterion_digest, issue_id in normalized_mapping
    )
    proven_record = _legacy_f89_record(
        proven_evidence.spec, proven_criteria, actor=actor,
        tracker=active_tracker, recorded_at=recorded_at,
    )
    existing = proven_evidence.existing_requalification
    if (existing is not None
            and existing.evidence_digest != proven_record.evidence_digest):
        raise LegacyCampaignRequalificationError(
            "requalification legacy F89 modifiee"
        )

    def pre_effect_guard() -> None:
        observed = _legacy_f89_record(
            proven_evidence.spec, proven_criteria, actor=actor,
            tracker=active_tracker, recorded_at=recorded_at,
        )
        if observed != proven_record:
            raise LegacyCampaignRequalificationError(
                "preuve provider legacy F89 modifiee"
            )

    with _validated_campaign_directory(
        raw, proven_evidence=proven_evidence,
        proven_database_identities=proven_database_identities,
        pre_effect_guard=pre_effect_guard,
    ) as (
        directory, guard, database_identities, sidecar_identities, proven_spec,
    ):
        return _requalify_validated_legacy_f89(
            directory, normalized_mapping, actor=actor, tracker=active_tracker,
            recorded_at=recorded_at, connection_guard=guard,
            database_identities=database_identities,
            sidecar_identities=sidecar_identities, proven_spec=proven_spec,
            proven_record=proven_record,
        )


def _requalify_validated_legacy_f89(
    directory: Path, mapping: tuple[tuple[str, str, str], ...], *,
    actor: str, tracker, recorded_at, connection_guard, database_identities,
    sidecar_identities, proven_spec, proven_record,
) -> LegacyParentRequalification:
    if directory.name != LEGACY_F89_CAMPAIGN_ID:
        raise LegacyCampaignRequalificationError("repertoire campagne F89 inattendu")
    ledger_path = directory / "campaign.sqlite3"
    receipt_path = directory / "primitive-receipts.sqlite3"
    try:
        store = CampaignStore(
            ledger_path, connection_guard=connection_guard,
            connection_identity=database_identities["campaign.sqlite3"],
            connection_sidecar_identities=sidecar_identities["campaign.sqlite3"],
        )
        effects = CampaignEffectStore(
            receipt_path, connection_guard=connection_guard,
            connection_identity=database_identities["primitive-receipts.sqlite3"],
            connection_sidecar_identities=(
                sidecar_identities["primitive-receipts.sqlite3"]
            ),
        )
        spec = store.campaign_spec(LEGACY_F89_CAMPAIGN_ID)
    except CampaignError as exc:
        raise LegacyCampaignRequalificationError(str(exc)) from None
    if (spec != proven_spec
            or spec.binding_digest != LEGACY_F89_BINDING_DIGEST
            or spec.epic_id != LEGACY_F89_PARENT_ID
            or tuple(sorted(issue for wave in spec.waves for issue in wave))
            != LEGACY_F89_CHILDREN):
        raise LegacyCampaignRequalificationError("binding campagne legacy F89 invalide")
    qualified = []
    for criterion_id, criterion_digest, issue_id in mapping:
        issue = store.issue(LEGACY_F89_CAMPAIGN_ID, issue_id)
        if issue is None or issue["state"] != "merged":
            raise LegacyCampaignRequalificationError("enfant legacy F89 non merge")
        merge = store.effect(
            LEGACY_F89_CAMPAIGN_ID, issue_id, issue["attempt"], "merge",
        )
        effect_id, work_id = CampaignCoordinator._identity(
            spec, issue_id, issue["attempt"], "merge",
        )
        envelope = EffectEnvelope(
            spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
            issue_id, issue["attempt"], "merge", effect_id, work_id,
            "legacy-f89-requalification", 1, spec.binding_digest,
            spec.snapshot_digest, spec.policy_digest, None, 0,
        )
        try:
            primitive = effects.get(envelope)
        except CampaignError:
            raise LegacyCampaignRequalificationError(
                "recu merge legacy F89 invalide"
            ) from None
        if (merge is None or merge[:2] != (effect_id, work_id)
                or merge[2] != "completed" or primitive is None
                or primitive.status != "completed"
                or primitive.effect_id != merge[0]
                or primitive.proof_digest != merge[3]):
            raise LegacyCampaignRequalificationError("recu merge legacy F89 invalide")
        qualified.append((
            criterion_id, criterion_digest, issue_id, merge[0], merge[3],
        ))

    campaign = store.campaign(LEGACY_F89_CAMPAIGN_ID)
    if campaign.budget_spent != LEGACY_F89_BUDGET_SPENT:
        raise LegacyCampaignRequalificationError("budget legacy F89 modifie")
    if store.campaign_snapshot_digest(LEGACY_F89_CAMPAIGN_ID) != spec.snapshot_digest:
        raise LegacyCampaignRequalificationError(
            "snapshot durable de campagne invalide"
        )
    record = _legacy_f89_record(
        spec, tuple(qualified), actor=actor, tracker=tracker,
        recorded_at=recorded_at,
    )
    if record != proven_record:
        raise LegacyCampaignRequalificationError(
            "preuve requalification legacy F89 modifiee"
        )
    try:
        record = store.record_legacy_parent_requalification(record)
    except (CampaignError, ValueError) as exc:
        raise LegacyCampaignRequalificationError(str(exc)) from None
    return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Requalifie explicitement la campagne legacy F89 v4 vers v6",
    )
    parser.add_argument("--campaign-dir", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args(argv)
    try:
        raw = _read_mapping(args.mapping)
        if not isinstance(raw, list):
            raise ValueError
        mapping = tuple(
            (item["criterion_id"], item["criterion_digest"], item["issue_id"])
            for item in raw
            if isinstance(item, dict) and set(item) == {
                "criterion_id", "criterion_digest", "issue_id",
            }
        )
        if len(mapping) != len(raw):
            raise ValueError
        record = requalify_legacy_f89(
            args.campaign_dir, mapping, actor=args.actor,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError,
            LegacyCampaignRequalificationError) as exc:
        raise SystemExit(f"requalification legacy F89 refusee: {exc}") from None
    print(json.dumps({
        "campaign_id": record.campaign_id,
        "audit_digest": record.audit_digest,
        "budget_spent_cents": record.budget_spent_cents,
        "from_version": record.from_version,
        "closed_version": record.closed_version,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
