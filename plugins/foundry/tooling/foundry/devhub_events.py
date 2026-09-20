"""Passive, durable Foundry event publication over the DevHub command journal.

This module is deliberately outside routing, gates and issue mutation.  It owns only
an operator-provided spool and talks to the already-authoritative command/event
endpoint through a small transport port.  A caller must explicitly invoke it after
observing a fact; publishing (or failing to publish) cannot change that fact.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse


_SCHEMA = "foundry-devhub-passive-events.v5"
_PREVIOUS_SPOOL_SCHEMAS = {
    "foundry-devhub-passive-events.v2",
    "foundry-devhub-passive-events.v3",
    "foundry-devhub-passive-events.v4",
}
_LEGACY_SPOOL_SCHEMA = "foundry-devhub-passive-events.v1"
_EVENT_SCHEMA = "devhub-foundry-observation.v1"
_MAX_PENDING = 256
_MAX_RETRIES = 2
_METRICS = (
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
    "tool_tokens", "cost_cents", "duration_ms",
)
_METRIC_MAXIMA = {
    "input_tokens": 1_000_000_000, "cached_input_tokens": 1_000_000_000,
    "output_tokens": 1_000_000_000, "reasoning_tokens": 1_000_000_000,
    "tool_tokens": 1_000_000_000, "cost_cents": 1_000_000,
    "duration_ms": 604_800_000,
}
_RECEIPT_KEYS = ("pr", "review", "ci", "merge", "epic_closure")
_COMMAND_TYPES = {"accepted", "running", "paused", "cancelled", "completed", "failed", "unknown"}
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}$")
_ISSUE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-\d+$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EPIC_CLOSURE_COORDINATE_VERSION = "devhub-foundry-epic-closure.v1"
_MAX_EPIC_CLOSURE_CHILDREN = 100


class EventTransport(Protocol):
    """DevHub command-journal boundary used by the passive publisher."""

    def append_event(self, command_id: str, expected_version: int, event: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def reconcile(self, command_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class PublishResult:
    status: str  # delivered, buffered, stale, backpressured
    event_id: str | None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _nullable_metrics(metrics: Mapping[str, Any] | None) -> dict[str, int | None]:
    source = metrics or {}
    if not isinstance(source, Mapping):
        raise ValueError("metrics must be a mapping")
    result: dict[str, int | None] = {}
    for key in _METRICS:
        value = source.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= _METRIC_MAXIMA[key]):
            raise ValueError(f"metric {key} must be a non-negative integer or null")
        result[key] = value
    if set(source) - set(_METRICS):
        raise ValueError("unknown metric")
    return result


def _nullable_receipts(receipts: Mapping[str, Any] | None) -> dict[str, Any]:
    source = receipts or {}
    if not isinstance(source, Mapping) or set(source) - set(_RECEIPT_KEYS):
        raise ValueError("unknown receipt")
    # F91's internal evidence keeps each optional source field explicit as null.
    # DevHub's public receipt schema expresses the same absence by omitting the
    # optional field; keep the receipt itself nullable, never invent a value.
    result = {
        key: (
            {field: item for field, item in value.items() if item is not None}
            if isinstance(value := source.get(key), Mapping) else value
        )
        for key in _RECEIPT_KEYS
    }
    validators = {
        "pr": _validate_pr_receipt,
        "review": _validate_review_receipt,
        "ci": _validate_ci_receipt,
        "merge": _validate_merge_receipt,
        "epic_closure": _validate_epic_closure_receipt,
    }
    for key, value in result.items():
        if value is not None:
            validators[key](value)
    return result


def _required_exact(value: Any, keys: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) - keys:
        raise ValueError(f"invalid {label} receipt")


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_GIT_SHA.fullmatch(value))


def _code_host_base(value: Any, required: set[str], optional: set[str], label: str) -> None:
    _required_exact(value, required | optional, label)
    if not required.issubset(value):
        raise ValueError(f"invalid {label} receipt")
    if value["provider"] not in {"github", "forgejo"} or not isinstance(value["repository"], str) or not _REPOSITORY.fullmatch(value["repository"]):
        raise ValueError(f"invalid {label} receipt")
    if "url" in value:
        parsed = urlparse(value["url"]) if isinstance(value["url"], str) else None
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc or len(value["url"]) > 2048:
            raise ValueError(f"invalid {label} receipt")


def _positive_int(value: Any) -> bool:
    return type(value) is int and 0 < value <= 2_147_483_647


def _validate_pr_receipt(value: Any) -> None:
    required, optional = {"provider", "repository", "number", "state"}, {"url", "head_sha", "base_sha"}
    _code_host_base(value, required, optional, "pr")
    if not _positive_int(value["number"]) or value["state"] not in {"open", "closed", "merged"} or any(key in value and not _sha(value[key]) for key in {"head_sha", "base_sha"}):
        raise ValueError("invalid pr receipt")


def _validate_review_receipt(value: Any) -> None:
    required, optional = {"provider", "repository", "pr_number", "verdict"}, {"url", "review_id", "reviewer", "head_sha"}
    _code_host_base(value, required, optional, "review")
    if not _positive_int(value["pr_number"]) or value["verdict"] not in {"approved", "changes-requested", "commented", "dismissed"} or any(key in value and not _identifier(value[key]) for key in {"review_id", "reviewer"} if key in value) or ("head_sha" in value and not _sha(value["head_sha"])):
        raise ValueError("invalid review receipt")


def _validate_ci_receipt(value: Any) -> None:
    required, optional = {"provider", "repository", "status"}, {"url", "run_id", "head_sha"}
    _code_host_base(value, required, optional, "ci")
    if value["status"] not in {"queued", "in-progress", "success", "failure", "cancelled", "skipped", "neutral"} or ("run_id" in value and not _identifier(value["run_id"])) or ("head_sha" in value and not _sha(value["head_sha"])):
        raise ValueError("invalid ci receipt")


def _validate_merge_receipt(value: Any) -> None:
    required, optional = {"provider", "repository", "pr_number", "merged"}, {"url", "merge_sha"}
    _code_host_base(value, required, optional, "merge")
    if not _positive_int(value["pr_number"]) or value["merged"] is not True or ("merge_sha" in value and not _sha(value["merge_sha"])):
        raise ValueError("invalid merge receipt")


def _validate_epic_closure_receipt(value: Any) -> None:
    compact = {"epic_id", "state"}
    identified = compact | {"receipt_id"}
    complete = identified | {"parent_version", "children", "closure_digest"}
    _required_exact(value, complete, "epic_closure")
    keys = set(value)
    if (keys not in (compact, identified, complete)
            or not isinstance(value.get("epic_id"), str)
            or not _ISSUE_ID.fullmatch(value["epic_id"])
            or value.get("state") != "closed"
            or ("receipt_id" in value and not _identifier(value["receipt_id"]))):
        raise ValueError("invalid epic_closure receipt")
    if keys != complete:
        return
    children = value["children"]
    if (not _positive_int(value["parent_version"])
            or not isinstance(children, list)
            or not 1 <= len(children) <= _MAX_EPIC_CLOSURE_CHILDREN
            or not isinstance(value["closure_digest"], str)
            or _DIGEST.fullmatch(value["closure_digest"]) is None):
        raise ValueError("invalid epic_closure receipt")
    child_ids = []
    for child in children:
        if (not isinstance(child, Mapping)
                or set(child) != {"issue_id", "version", "state"}
                or not isinstance(child["issue_id"], str)
                or _ISSUE_ID.fullmatch(child["issue_id"]) is None
                or not _positive_int(child["version"])
                or child["state"] not in {"done", "dropped"}):
            raise ValueError("invalid epic_closure receipt")
        child_ids.append(child["issue_id"])
    if child_ids != sorted(child_ids) or len(child_ids) != len(set(child_ids)):
        raise ValueError("invalid epic_closure receipt")
    coordinate = {
        "contract": _EPIC_CLOSURE_COORDINATE_VERSION,
        **{key: value[key] for key in complete - {"closure_digest"}},
    }
    if hashlib.sha256(_canonical(coordinate)).hexdigest() != value["closure_digest"]:
        raise ValueError("invalid epic_closure receipt")


class PassiveEventPublisher:
    """Bounded, idempotent publisher with reconciliation-before-replay semantics."""

    def __init__(self, directory: Path, transport: EventTransport, *, max_pending: int = _MAX_PENDING, retries: int = _MAX_RETRIES):
        if max_pending < 1 or retries < 1:
            raise ValueError("invalid passive publisher bounds")
        self.directory = Path(directory)
        self.transport = transport
        self.max_pending = max_pending
        self.retries = retries

    def publish(
        self, *, command: Mapping[str, Any], attempt_id: str, issue_id: str,
        event_type: str, occurred_at: int, metrics: Mapping[str, Any] | None = None,
        receipts: Mapping[str, Any] | None = None,
    ) -> PublishResult:
        """Queue one observed fact and make a bounded best-effort flush.

        ``attempt_id`` and ``issue_id`` are part of the logical event identity.  The
        deterministic ID survives a timeout/restart, so retransmission is an exact
        DevHub idempotent replay rather than a second logical fact.
        """
        command_id = self._command_id(command)
        if not _identifier(attempt_id) or not isinstance(issue_id, str) or not _ISSUE_ID.fullmatch(issue_id):
            raise ValueError("attempt_id and issue_id are required")
        if event_type not in _COMMAND_TYPES or type(occurred_at) is not int or occurred_at < 0:
            raise ValueError("invalid passive event")
        normalized_receipts = _nullable_receipts(receipts)
        payload = {
            "schema_version": _EVENT_SCHEMA,
            "command_id": command_id,
            "attempt_id": attempt_id,
            "issue_id": issue_id,
            "type": event_type,
            "occurred_at": occurred_at,
            "metrics": _nullable_metrics(metrics),
            "receipts": normalized_receipts,
        }
        event_id = hashlib.sha256(_canonical({key: payload[key] for key in ("schema_version", "command_id", "attempt_id", "issue_id", "type")})).hexdigest()
        state = self._load()
        delivered = state["delivered"].get(event_id)
        if delivered is not None:
            if delivered != hashlib.sha256(_canonical(payload)).hexdigest():
                self._mark_stale(state, command_id)
                return PublishResult("stale", event_id)
            return PublishResult("delivered", event_id)
        item = next((row for row in state["pending"] if row["event_id"] == event_id), None)
        if item is None:
            if len(state["pending"]) >= self.max_pending:
                # The bounded replay spool cannot accept this fact.  Persist an
                # operator-visible, hash-chained alarm before returning control;
                # publication remains passive, but the observation is not silent.
                self._record_backpressure(state, payload, event_id)
                return PublishResult("backpressured", event_id)
            # A fresh local spool must not assume that it owns observation
            # sequence 1.  The remote passive journal is the authoritative
            # sequence source; synchronise it before allocating the first
            # local observation.  An unavailable read remains passive and
            # queues the fact, while an inconsistent journal is made visible
            # rather than being extended blindly.
            pending_for_command = any(
                row["command_id"] == command_id for row in state["pending"]
            )
            synchronised = "current"
            if command_id not in state["heads"] and not pending_for_command:
                synchronised = self._synchronise_empty_head(state, command_id)
            sequence = self._sequence_for(command, state)
            head = state["heads"].get(command_id, {})
            command_version = max(command["version"], head.get("version", 0))
            item = {
                **payload,
                "event_id": event_id,
                "sequence": sequence,
                "command_version": command_version,
            }
            state["pending"].append(item)
            self._save(state)
            if synchronised == "unavailable":
                return PublishResult("buffered", event_id)
            if synchronised == "stale":
                return PublishResult("stale", event_id)
        elif any(item[key] != payload[key] for key in payload):
            self._mark_stale(state, command_id)
            return PublishResult("stale", event_id)
        elif command["version"] > item["command_version"]:
            # A native lifecycle transition may advance the optimistic command
            # version while this passive observation is pending. Rebase that
            # version, never the observation identity or sequence.
            item["command_version"] = command["version"]
            self._save(state)
        return self.flush(command_id)

    def flush(
        self, command_id: str, *, command_version: int | None = None,
    ) -> PublishResult:
        """Attempt each queued event at most ``retries`` times, in sequence order."""
        if command_version is not None and (
                type(command_version) is not int or command_version < 1):
            raise ValueError("command observation version is invalid")
        state = self._load()
        if state["stale"].get(command_id):
            return PublishResult("stale", None)
        pending = sorted((row for row in state["pending"] if row["command_id"] == command_id), key=lambda row: row["sequence"])
        if not pending:
            return PublishResult("delivered", None)
        if command_version is not None:
            changed = False
            for item in pending:
                if command_version > item["command_version"]:
                    item["command_version"] = command_version
                    changed = True
            if changed:
                self._save(state)
        if command_id not in state["heads"]:
            synchronised = self._synchronise_empty_head(state, command_id)
            if synchronised == "unavailable":
                return PublishResult("buffered", pending[0]["event_id"])
            if synchronised == "stale":
                return PublishResult("stale", pending[0]["event_id"])
        for item in pending:
            result = self._flush_one(state, item)
            if result.status != "delivered":
                return result
        return PublishResult("delivered", pending[-1]["event_id"])

    def _flush_one(self, state: dict[str, Any], item: dict[str, Any]) -> PublishResult:
        command_id = item["command_id"]
        head = state["heads"].get(command_id, {})
        if (type(head.get("next_event_sequence")) is int
                and head["next_event_sequence"] != item["sequence"]):
            # A response can be lost after DevHub persisted the observation.
            # Reconciliation may acknowledge that exact identity, but a
            # different sequence is never republished under a new identity.
            result = self._reconcile(state, item)
            if result.status != "buffered":
                return result
            head = state["heads"].get(command_id, {})
            if head.get("next_event_sequence") != item["sequence"]:
                self._mark_stale(state, command_id)
                return PublishResult("stale", item["event_id"])
        if (type(head.get("version")) is int
                and head["version"] > item["command_version"]):
            item["command_version"] = head["version"]
            self._save(state)
        for _attempt in range(self.retries):
            try:
                response = self.transport.append_event(command_id, item["command_version"], self._wire(item))
            except Exception:
                # A transport outage is passive: retain the immutable record, do no
                # retry scheduling, and do not alter command/issue/routing state.
                continue
            if not isinstance(response, Mapping) or response.get("accepted") is not True:
                self._mark_stale(state, command_id)
                return PublishResult("stale", item["event_id"])
            remote = response.get("observation")
            if not self._accepted_observation(remote, item):
                self._mark_stale(state, command_id)
                return PublishResult("stale", item["event_id"])
            state["heads"][command_id] = {
                "version": item["command_version"],
                "next_event_sequence": item["sequence"] + 1,
            }
            self._remove(state, item)
            return PublishResult("delivered", item["event_id"])
        return self._reconcile(state, item)

    def _reconcile(self, state: dict[str, Any], item: dict[str, Any]) -> PublishResult:
        command_id = item["command_id"]
        try:
            view = self.transport.reconcile(command_id)
        except Exception:
            return PublishResult("buffered", item["event_id"])
        if not isinstance(view, Mapping):
            self._mark_stale(state, command_id)
            return PublishResult("stale", item["event_id"])
        command = view.get("command")
        events = view.get("events", ())
        if not isinstance(command, Mapping) or not isinstance(events, (list, tuple)):
            self._mark_stale(state, command_id)
            return PublishResult("stale", item["event_id"])
        remote = next((event for event in events if isinstance(event, Mapping) and event.get("event_id") == item["event_id"]), None)
        if remote is not None:
            if (remote.get("sequence") != item["sequence"]
                    or type(command.get("version")) is not int
                    or type(command.get("next_event_sequence")) is not int
                    or command["next_event_sequence"] < item["sequence"] + 1
                    or command.get("projection_stale") is not False
                    or command.get("observation_status") != "current"):
                self._mark_stale(state, command_id)
                return PublishResult("stale", item["event_id"])
            state["heads"][command_id] = {
                "version": command["version"],
                "next_event_sequence": command["next_event_sequence"],
            }
            self._remove(state, item)
            return PublishResult("delivered", item["event_id"])
        next_sequence = command.get("next_event_sequence")
        if (command.get("projection_stale") is True
                or command.get("observation_status") != "current"
                or type(next_sequence) is not int or next_sequence != item["sequence"]):
            self._mark_stale(state, command_id)
            return PublishResult("stale", item["event_id"])
        if type(command.get("version")) is not int or command["version"] < item["command_version"]:
            self._mark_stale(state, command_id)
            return PublishResult("stale", item["event_id"])
        item["command_version"] = command["version"]
        state["heads"][command_id] = {
            "version": command["version"],
            "next_event_sequence": next_sequence,
        }
        self._save(state)
        # The remote journal proves neither acceptance nor rejection.  Keep the
        # pending record for the next explicit passive flush; never infer success.
        return PublishResult("buffered", item["event_id"])

    def _synchronise_empty_head(self, state: dict[str, Any], command_id: str) -> str:
        """Get the remote observation head before creating a fresh local sequence.

        The result is intentionally tri-state: a temporary read failure leaves
        the spool buffered, while an incomplete or inconsistent remote view
        becomes an operator-visible stale projection.  Neither outcome gives
        the publisher permission to guess a sequence.
        """
        try:
            view = self.transport.reconcile(command_id)
        except Exception:
            return "unavailable"
        if not isinstance(view, Mapping):
            self._mark_stale(state, command_id)
            return "stale"
        command = view.get("command")
        events = view.get("events")
        if (not isinstance(command, Mapping) or not isinstance(events, (list, tuple))
                or command.get("projection_stale") is not False
                or command.get("observation_status") != "current"
                or type(command.get("version")) is not int
                or command["version"] < 1
                or type(command.get("next_event_sequence")) is not int
                or command["next_event_sequence"] < 1):
            self._mark_stale(state, command_id)
            return "stale"
        sequences = [
            event.get("sequence") if isinstance(event, Mapping) else None
            for event in events
        ]
        if (any(type(sequence) is not int for sequence in sequences)
                or sorted(sequences) != list(range(1, command["next_event_sequence"]))):
            self._mark_stale(state, command_id)
            return "stale"
        state["heads"][command_id] = {
            "version": command["version"],
            "next_event_sequence": command["next_event_sequence"],
        }
        self._save(state)
        return "current"

    @staticmethod
    def _wire(item: Mapping[str, Any]) -> dict[str, Any]:
        """The exact, closed DEVHUB-27 passive-observation wire shape."""
        return {
            "schema_version": _EVENT_SCHEMA,
            "observation_id": item["event_id"], "sequence": item["sequence"],
            "type": item["type"], "observed_at": item["occurred_at"],
            "provenance": {
                "command_id": item["command_id"], "attempt_id": item["attempt_id"],
                "issue_id": item["issue_id"], "source": "foundry",
            },
            "receipts": item["receipts"],
            "telemetry": item["metrics"],
        }

    def _sequence_for(self, command: Mapping[str, Any], state: Mapping[str, Any]) -> int:
        head = state["heads"].get(command["id"], {})
        next_sequence = head.get("next_event_sequence", 1)
        if type(next_sequence) is not int or next_sequence < 1:
            raise ValueError("observation next_sequence is invalid")
        current = [row["sequence"] for row in state["pending"] if row["command_id"] == command["id"]]
        return max(current, default=next_sequence - 1) + 1

    @staticmethod
    def _command_id(command: Mapping[str, Any]) -> str:
        if not isinstance(command, Mapping) or not _identifier(command.get("id")):
            raise ValueError("command id is required")
        if type(command.get("version")) is not int or command["version"] < 1:
            raise ValueError("active command version is required")
        return command["id"]

    def _load(self) -> dict[str, Any]:
        path = self.directory / "pending.json"
        if not path.exists():
            return {"schema": _SCHEMA, "pending": [], "stale": {}, "heads": {}, "backpressure": {}, "delivered": {}}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("passive event spool is unreadable") from error
        if not isinstance(value, dict) or value.get("schema") not in {_SCHEMA, *_PREVIOUS_SPOOL_SCHEMAS, _LEGACY_SPOOL_SCHEMA} or not isinstance(value.get("pending"), list) or not isinstance(value.get("stale"), dict):
            raise RuntimeError("passive event spool is invalid")
        if value["schema"] == _LEGACY_SPOOL_SCHEMA:
            for item in value["pending"]:
                if not isinstance(item, dict):
                    raise RuntimeError("passive event spool is invalid")
                item["schema_version"] = _EVENT_SCHEMA
                item["metrics"] = _nullable_metrics(item.get("metrics"))
                item["receipts"] = _nullable_receipts(item.get("receipts"))
        if value["schema"] in {*_PREVIOUS_SPOOL_SCHEMAS, _LEGACY_SPOOL_SCHEMA}:
            value["schema"] = _SCHEMA
            value["heads"] = {}
            value["backpressure"] = {}
            value["delivered"] = {}
        if (not isinstance(value.get("heads"), dict)
                or not isinstance(value.get("backpressure"), dict)
                or not isinstance(value.get("delivered"), dict)):
            raise RuntimeError("passive event spool is invalid")
        return value

    @staticmethod
    def _accepted_observation(remote: Any, item: Mapping[str, Any]) -> bool:
        return (
            isinstance(remote, Mapping)
            and remote.get("id") == item["event_id"]
            and remote.get("sequence") == item["sequence"]
        )

    def _record_backpressure(
        self, state: dict[str, Any], payload: Mapping[str, Any], event_id: str,
    ) -> None:
        command_id = payload["command_id"]
        previous = state["backpressure"].get(command_id, {})
        prior_digest = previous.get("chain_digest", "0" * 64)
        chain_digest = hashlib.sha256(
            prior_digest.encode("ascii") + _canonical({"event_id": event_id, **payload})
        ).hexdigest()
        state["backpressure"][command_id] = {
            "count": previous.get("count", 0) + 1,
            "chain_digest": chain_digest,
            "last_event": {"event_id": event_id, **payload},
        }
        self._save(state)

    def _save(self, state: Mapping[str, Any]) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self.directory / "pending.json"
        temporary = self.directory / ".pending.json.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, _canonical(state))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, target)

    def _remove(self, state: dict[str, Any], item: Mapping[str, Any]) -> None:
        event_id = item["event_id"]
        payload = {key: item[key] for key in (
            "schema_version", "command_id", "attempt_id", "issue_id", "type",
            "occurred_at", "metrics", "receipts",
        )}
        if len(state["delivered"]) >= self.max_pending and event_id not in state["delivered"]:
            del state["delivered"][sorted(state["delivered"])[0]]
        state["delivered"][event_id] = hashlib.sha256(_canonical(payload)).hexdigest()
        state["pending"] = [row for row in state["pending"] if row["event_id"] != event_id]
        self._save(state)

    def _mark_stale(self, state: dict[str, Any], command_id: str) -> None:
        state["stale"][command_id] = True
        self._save(state)
