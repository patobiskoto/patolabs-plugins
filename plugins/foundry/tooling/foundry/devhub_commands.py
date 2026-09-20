"""Strict client for DevHub's bounded Foundry command control plane.

The tracker adapter intentionally keeps its broad tracker credential separate from
this client.  A command worker needs only ``foundry:command:claim`` and
``foundry:command:event``; those scopes do not grant any tracker or code-host
mutation authority.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

from foundry import config
from foundry.models import Project
from foundry.trackers.devhub import DevHubTracker, _SameOriginRedirectHandler, validate_base_url


COMMAND_CONTRACT = "devhub-foundry-command.v1"
COMMAND_EVENT_CONTRACT = "devhub-foundry-command-event.v1"
OBSERVATION_CONTRACT = "devhub-foundry-observation.v1"
EPIC_CLOSURE_COORDINATE_CONTRACT = "devhub-foundry-epic-closure.v1"
MAX_PAGE_ITEMS = 100
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MIN_LEASE_SECONDS = 15
MAX_LEASE_SECONDS = 300

_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}$")
_ISSUE_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-\d+$")
_VERSION_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}-VERSION-\d+$")
_PROJECT = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,160}$")
_CURSOR = re.compile(r"^(?:0|[1-9][0-9]{0,18})$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_CODE_HOST_PROVIDERS = frozenset({"github", "forgejo"})
_STATES = {
    "requested", "accepted", "running", "pause-requested", "paused",
    "cancel-requested", "cancelled", "completed", "failed", "unknown",
}
_EVENT_STATES = _STATES - {"requested", "pause-requested", "cancel-requested"}
_TERMINAL_STATES = {"cancelled", "completed", "failed"}


class DevHubCommandError(RuntimeError):
    """Sanitized command-protocol error."""

    def __init__(self, method: str, path: str, status: int | None, code: str):
        self.method = method
        self.path = path
        self.status = status
        self.code = code
        status_text = str(status) if status is not None else "unavailable"
        super().__init__(f"DevHubCommand {method} {path} -> {status_text} ({code})")


class DevHubCommandTransportError(DevHubCommandError):
    """The remote result is ambiguous and must be resolved by command identity."""


@dataclass(frozen=True)
class CommandLease:
    id: str
    worker_id: str
    expires_at: int
    heartbeat_at: int


@dataclass(frozen=True)
class DevHubCommand:
    id: str
    project: str
    preview_id: str
    approval_id: str
    preview_digest: str
    snapshot_digest: str
    policy_digest: str
    epic_id: str
    planning_version_id: str
    max_cost_cents: int
    max_concurrency: int
    state: str
    projection_stale: bool
    next_event_sequence: int
    scheduled_for: int | None
    lease: CommandLease | None
    version: int
    created_at: int
    updated_at: int

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


@dataclass(frozen=True)
class CommandPage:
    items: tuple[DevHubCommand, ...]
    next_cursor: str | None
    truncated: bool


@dataclass(frozen=True)
class CommandEvent:
    id: str
    command_id: str
    sequence: int
    type: str
    actor: str
    lease_id: str
    occurred_at: int
    received_at: int
    cost_cents: int | None
    duration_ms: int | None
    schema_version: str | None = None
    provenance: dict[str, str] | None = None
    receipts: dict[str, object | None] | None = None


@dataclass(frozen=True)
class CommandEventPage:
    items: tuple[CommandEvent, ...]
    next_cursor: str | None
    truncated: bool
    projection_stale: bool
    next_sequence: int


@dataclass(frozen=True)
class TerminalReconciliation:
    id: str
    command_id: str
    terminal_event_id: str
    epic_id: str
    preview_epic_version: int
    reconciled_epic_version: int
    closure_digest: str
    cost_cents: int | None
    duration_ms: int | None
    provider_effects: int
    version: int
    created_at: int


def _exact(value, keys: set[str], where: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise DevHubCommandError("PARSE", where, None, "invalid_response")
    return value


def _integer(value, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError
    return value


def _matches(value, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError
    return value


def _parse_lease(raw: object) -> CommandLease | None:
    if raw is None:
        return None
    lease = _exact(raw, {"id", "worker_id", "expires_at", "heartbeat_at"}, "lease")
    try:
        return CommandLease(
            id=_matches(lease["id"], _IDENTIFIER),
            worker_id=_matches(lease["worker_id"], _IDENTIFIER),
            expires_at=_integer(lease["expires_at"]),
            heartbeat_at=_integer(lease["heartbeat_at"]),
        )
    except ValueError:
        raise DevHubCommandError("PARSE", "lease", None, "invalid_response") from None


def parse_command(raw: object) -> DevHubCommand:
    command = _exact(raw, {
        "contract", "id", "project", "preview_id", "approval_id",
        "preview_digest", "snapshot_digest", "policy_digest", "epic_id",
        "planning_version_id", "limits", "state", "projection_stale",
        "next_event_sequence", "scheduled_for", "lease", "version",
        "created_at", "updated_at",
    }, "command")
    limits = _exact(command["limits"], {"max_cost_cents", "max_concurrency"}, "limits")
    try:
        if command["contract"] != COMMAND_CONTRACT or command["state"] not in _STATES:
            raise ValueError
        if type(command["projection_stale"]) is not bool:
            raise ValueError
        scheduled = command["scheduled_for"]
        if scheduled is not None:
            scheduled = _integer(scheduled)
        return DevHubCommand(
            id=_matches(command["id"], _IDENTIFIER),
            project=_matches(command["project"], _PROJECT),
            preview_id=_matches(command["preview_id"], _IDENTIFIER),
            approval_id=_matches(command["approval_id"], _IDENTIFIER),
            preview_digest=_matches(command["preview_digest"], _DIGEST),
            snapshot_digest=_matches(command["snapshot_digest"], _DIGEST),
            policy_digest=_matches(command["policy_digest"], _DIGEST),
            epic_id=_matches(command["epic_id"], _ISSUE_ID),
            planning_version_id=_matches(command["planning_version_id"], _VERSION_ID),
            max_cost_cents=_integer(limits["max_cost_cents"], minimum=1, maximum=1_000_000),
            max_concurrency=_integer(limits["max_concurrency"], minimum=1, maximum=16),
            state=command["state"],
            projection_stale=command["projection_stale"],
            next_event_sequence=_integer(command["next_event_sequence"], minimum=1),
            scheduled_for=scheduled,
            lease=_parse_lease(command["lease"]),
            version=_integer(command["version"], minimum=1),
            created_at=_integer(command["created_at"]),
            updated_at=_integer(command["updated_at"]),
        )
    except ValueError:
        raise DevHubCommandError("PARSE", "command", None, "invalid_response") from None


def _nullable_metric(value: object, maximum: int) -> int | None:
    if value is None:
        return None
    return _integer(value, maximum=maximum)


def _parse_event_provenance(raw: object, command_id: str) -> dict[str, str]:
    provenance = _exact(
        raw, {"command_id", "attempt_id", "issue_id", "source"}, "event-provenance",
    )
    try:
        if provenance["command_id"] != command_id or provenance["source"] != "foundry":
            raise ValueError
        return {
            "command_id": _matches(provenance["command_id"], _IDENTIFIER),
            "attempt_id": _matches(provenance["attempt_id"], _IDENTIFIER),
            "issue_id": _matches(provenance["issue_id"], _ISSUE_ID),
            "source": "foundry",
        }
    except ValueError:
        raise DevHubCommandError(
            "PARSE", "event-provenance", None, "invalid_response",
        ) from None


def _parse_epic_closure_receipt(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or not {"epic_id", "state"}.issubset(raw):
        raise DevHubCommandError("PARSE", "epic-closure", None, "invalid_response")
    if set(raw) - {
        "epic_id", "state", "receipt_id", "parent_version", "children",
        "closure_digest",
    }:
        raise DevHubCommandError("PARSE", "epic-closure", None, "invalid_response")
    try:
        if raw["state"] != "closed":
            raise ValueError
        parsed: dict[str, object] = {
            "epic_id": _matches(raw["epic_id"], _ISSUE_ID), "state": "closed",
        }
        if "receipt_id" in raw:
            parsed["receipt_id"] = _matches(raw["receipt_id"], _IDENTIFIER)
        complete_keys = {"receipt_id", "parent_version", "children", "closure_digest"}
        extended_keys = complete_keys - {"receipt_id"}
        present_extended = extended_keys & set(raw)
        if (present_extended
                and (present_extended != extended_keys or "receipt_id" not in raw)):
            raise ValueError
        if "parent_version" in raw:
            parsed["parent_version"] = _integer(raw["parent_version"], minimum=1)
        if "closure_digest" in raw:
            parsed["closure_digest"] = _matches(raw["closure_digest"], _DIGEST)
        if "children" in raw:
            children = raw["children"]
            if not isinstance(children, list) or not 1 <= len(children) <= 100:
                raise ValueError
            parsed_children = []
            identities = set()
            for item in children:
                child = _exact(item, {"issue_id", "version", "state"}, "epic-child")
                if child["state"] not in {"done", "dropped"}:
                    raise ValueError
                issue_id = _matches(child["issue_id"], _ISSUE_ID)
                if issue_id in identities:
                    raise ValueError
                identities.add(issue_id)
                parsed_children.append({
                    "issue_id": issue_id,
                    "version": _integer(child["version"], minimum=1),
                    "state": child["state"],
                })
            if [child["issue_id"] for child in parsed_children] != sorted(identities):
                raise ValueError
            parsed["children"] = parsed_children
        if present_extended:
            coordinate = {
                "contract": EPIC_CLOSURE_COORDINATE_CONTRACT,
                **{key: parsed[key] for key in (
                    "epic_id", "state", "receipt_id", "parent_version", "children",
                )},
            }
            encoded = json.dumps(
                coordinate, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8")
            if hashlib.sha256(encoded).hexdigest() != parsed["closure_digest"]:
                raise ValueError
        return parsed
    except ValueError:
        raise DevHubCommandError("PARSE", "epic-closure", None, "invalid_response") from None


def _parse_code_host_receipt(raw: object, kind: str) -> dict[str, object]:
    schemas = {
        "pr": (
            {"provider", "repository", "number", "state"},
            {"url", "head_sha", "base_sha"},
        ),
        "review": (
            {"provider", "repository", "pr_number", "verdict"},
            {"url", "review_id", "reviewer", "head_sha"},
        ),
        "ci": (
            {"provider", "repository", "status"},
            {"url", "run_id", "head_sha"},
        ),
        "merge": (
            {"provider", "repository", "pr_number", "merged"},
            {"url", "merge_sha"},
        ),
    }
    required, optional = schemas[kind]
    where = f"{kind}-receipt"
    if (not isinstance(raw, dict) or not required.issubset(raw)
            or set(raw) - required - optional):
        raise DevHubCommandError("PARSE", where, None, "invalid_response")
    try:
        if raw["provider"] not in _CODE_HOST_PROVIDERS:
            raise ValueError
        repository = _matches(raw["repository"], _REPOSITORY)
        if len(repository) > 240:
            raise ValueError
        if "url" in raw:
            url = raw["url"]
            parsed_url = urllib.parse.urlparse(url) if isinstance(url, str) else None
            if (not isinstance(url, str) or len(url) > 2_048
                    or parsed_url is None or parsed_url.scheme not in {"http", "https"}
                    or not parsed_url.netloc
                    or any(character.isspace() for character in url)):
                raise ValueError
        if kind == "pr":
            _integer(raw["number"], minimum=1, maximum=2_147_483_647)
            if raw["state"] not in {"open", "closed", "merged"}:
                raise ValueError
            sha_keys = ("head_sha", "base_sha")
        elif kind == "review":
            _integer(raw["pr_number"], minimum=1, maximum=2_147_483_647)
            if raw["verdict"] not in {
                "approved", "changes-requested", "commented", "dismissed",
            }:
                raise ValueError
            for identifier in ("review_id", "reviewer"):
                if identifier in raw:
                    _matches(raw[identifier], _IDENTIFIER)
            sha_keys = ("head_sha",)
        elif kind == "ci":
            if raw["status"] not in {
                "queued", "in-progress", "success", "failure", "cancelled",
                "skipped", "neutral",
            }:
                raise ValueError
            if "run_id" in raw:
                _matches(raw["run_id"], _IDENTIFIER)
            sha_keys = ("head_sha",)
        else:
            _integer(raw["pr_number"], minimum=1, maximum=2_147_483_647)
            if raw["merged"] is not True:
                raise ValueError
            sha_keys = ("merge_sha",)
        for sha_key in sha_keys:
            if sha_key in raw:
                _matches(raw[sha_key], _GIT_SHA)
    except (KeyError, TypeError, ValueError):
        raise DevHubCommandError(
            "PARSE", where, None, "invalid_response",
        ) from None
    return dict(raw)


def _parse_event_receipts(raw: object) -> dict[str, object | None]:
    receipts = _exact(raw, {"pr", "review", "ci", "merge", "epic_closure"}, "event-receipts")
    parsed: dict[str, object | None] = {}
    for kind in ("pr", "review", "ci", "merge"):
        parsed[kind] = None if receipts[kind] is None else _parse_code_host_receipt(receipts[kind], kind)
    parsed["epic_closure"] = (
        None if receipts["epic_closure"] is None
        else _parse_epic_closure_receipt(receipts["epic_closure"])
    )
    return parsed


def _event_receipts_wire(raw: Mapping[str, object]) -> dict[str, object | None]:
    if not isinstance(raw, Mapping):
        raise ValueError("reçus événement DevHub invalides")
    wire = {
        key: (
            {field: item for field, item in value.items() if item is not None}
            if isinstance(value := raw.get(key), Mapping) else value
        )
        for key in ("pr", "review", "ci", "merge", "epic_closure")
    }
    if set(raw) != set(wire):
        raise ValueError("reçus événement DevHub invalides")
    try:
        return _parse_event_receipts(wire)
    except DevHubCommandError:
        raise ValueError("reçus événement DevHub invalides") from None


def parse_event(raw: object) -> CommandEvent:
    if not isinstance(raw, dict):
        raise DevHubCommandError("PARSE", "event", None, "invalid_response")
    extended = "schema_version" in raw
    keys = {
        "contract", "id", "command_id", "sequence", "type", "actor",
        "lease_id", "occurred_at", "received_at", "telemetry",
    }
    if extended:
        keys |= {"schema_version", "provenance", "receipts"}
    event = _exact(raw, keys, "event")
    telemetry = event["telemetry"]
    try:
        if event["contract"] != COMMAND_CONTRACT or event["type"] not in _EVENT_STATES:
            raise ValueError
        if extended:
            if event["schema_version"] != COMMAND_EVENT_CONTRACT:
                raise ValueError
            telemetry = _exact(telemetry, {
                "input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_tokens", "tool_tokens", "cost_cents", "duration_ms",
            }, "telemetry")
            for token_key in (
                "input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_tokens", "tool_tokens",
            ):
                _nullable_metric(telemetry[token_key], 1_000_000_000)
        else:
            if telemetry is not None:
                telemetry = _exact(telemetry, {"cost_cents", "duration_ms"}, "telemetry")
            else:
                telemetry = {"cost_cents": None, "duration_ms": None}
        cost = _nullable_metric(telemetry["cost_cents"], 1_000_000)
        duration = _nullable_metric(telemetry["duration_ms"], 604_800_000)
        actor = event["actor"]
        if not isinstance(actor, str) or not 1 <= len(actor) <= 120:
            raise ValueError
        command_id = _matches(event["command_id"], _IDENTIFIER)
        return CommandEvent(
            id=_matches(event["id"], _IDENTIFIER),
            command_id=command_id,
            sequence=_integer(event["sequence"], minimum=1),
            type=event["type"], actor=actor,
            lease_id=_matches(event["lease_id"], _IDENTIFIER),
            occurred_at=_integer(event["occurred_at"]),
            received_at=_integer(event["received_at"]),
            cost_cents=cost, duration_ms=duration,
            schema_version=event.get("schema_version"),
            provenance=(
                _parse_event_provenance(event["provenance"], command_id)
                if extended else None
            ),
            receipts=_parse_event_receipts(event["receipts"]) if extended else None,
        )
    except (KeyError, ValueError):
        raise DevHubCommandError("PARSE", "event", None, "invalid_response") from None


def parse_terminal_reconciliation(raw: object) -> TerminalReconciliation:
    receipt = _exact(raw, {
        "contract", "id", "command_id", "terminal_event_id", "epic_id",
        "preview_epic_version", "reconciled_epic_version", "closure_digest",
        "terminal_state", "economics", "provider_effects", "version", "created_at",
    }, "terminal-reconciliation")
    economics = _exact(receipt["economics"], {"cost_cents", "duration_ms"}, "economics")
    try:
        if (receipt["contract"] != COMMAND_CONTRACT
                or receipt["terminal_state"] != "completed"
                or receipt["provider_effects"] != 0
                or type(receipt["provider_effects"]) is not int
                or receipt["version"] != 1 or type(receipt["version"]) is not int):
            raise ValueError
        preview_version = _integer(receipt["preview_epic_version"], minimum=1)
        reconciled_version = _integer(receipt["reconciled_epic_version"], minimum=1)
        if reconciled_version <= preview_version:
            raise ValueError
        return TerminalReconciliation(
            id=_matches(receipt["id"], _IDENTIFIER),
            command_id=_matches(receipt["command_id"], _IDENTIFIER),
            terminal_event_id=_matches(receipt["terminal_event_id"], _IDENTIFIER),
            epic_id=_matches(receipt["epic_id"], _ISSUE_ID),
            preview_epic_version=preview_version,
            reconciled_epic_version=reconciled_version,
            closure_digest=_matches(receipt["closure_digest"], _DIGEST),
            cost_cents=_nullable_metric(economics["cost_cents"], 1_000_000),
            duration_ms=_nullable_metric(economics["duration_ms"], 604_800_000),
            provider_effects=0, version=1,
            created_at=_integer(receipt["created_at"]),
        )
    except (KeyError, ValueError):
        raise DevHubCommandError(
            "PARSE", "terminal-reconciliation", None, "invalid_response",
        ) from None


class DevHubCommandClient:
    """Authenticated command-only HTTP adapter.

    Mutating requests are attempted once.  Callers resolve an ambiguous transport
    result through ``GET /commands/:id`` (or its event journal), never by blindly
    issuing a second launch or claim.
    """

    def __init__(
        self, *, url: str | None = None, token: str | None = None,
        timeout: float = 10.0, now_ms: Callable[[], int] | None = None,
    ):
        candidate = (url if url is not None else config.require_public("DEVHUB_URL")).rstrip("/")
        validate_base_url(candidate)
        credential = token if token is not None else config.require("DEVHUB_COMMAND_TOKEN")
        if not isinstance(credential, str) or len(credential) < 24:
            raise ValueError("credential commandes DevHub invalide")
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError("timeout commandes DevHub invalide")
        self.url = candidate
        self.token = credential
        self.timeout = float(timeout)
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def _request(
        self, method: str, path: str, body: object | None = None, *,
        version: int | None = None, idempotency_key: str | None = None,
        expected_contract: str = COMMAND_CONTRACT,
    ) -> object:
        validate_base_url(self.url)
        if idempotency_key is not None and _IDEMPOTENCY.fullmatch(idempotency_key) is None:
            raise ValueError("Idempotency-Key commandes DevHub invalide")
        data = None if body is None else json.dumps(
            body, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/api/tracker/v1{path}", data=data, method=method,
        )
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if version is not None:
            if type(version) is not int or version < 1:
                raise ValueError("version commandes DevHub invalide")
            request.add_header("If-Match", f'"{version}"')
        if idempotency_key is not None:
            request.add_header("Idempotency-Key", idempotency_key)
        opener = urllib.request.build_opener(_SameOriginRedirectHandler())
        try:
            with opener.open(request, timeout=self.timeout) as response:
                if response.headers.get("X-DevHub-Contract") != expected_contract:
                    raise DevHubCommandError(method, path, response.status, "contract_mismatch")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise DevHubCommandError(method, path, response.status, "response_too_large")
        except DevHubCommandError:
            raise
        except urllib.error.HTTPError as error:
            # A proxy can emit a timeout/rate-limit/5xx after the origin committed
            # a POST.  Treat that result as unknown so the worker resolves the
            # command/event identity; never classify it as a definitive rejection
            # or blindly issue the mutation again.
            if method == "POST" and (
                error.code in {408, 425, 429} or 500 <= error.code <= 599
            ):
                error.close()
                raise DevHubCommandTransportError(
                    method, path, error.code, "command_unavailable_service",
                ) from None
            code = "provider_error"
            try:
                payload = json.loads(error.read(16_384).decode("utf-8"))
                candidate = payload.get("error", {}).get("code")
                if isinstance(candidate, str) and candidate.replace("_", "").isalnum():
                    code = candidate[:80]
            except Exception:
                pass
            raise DevHubCommandError(method, path, error.code, code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DevHubCommandTransportError(method, path, None, "command_unavailable_service") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise DevHubCommandError(method, path, None, "invalid_response") from None

    def list_commands(self, project: str, *, cursor: str = "0", limit: int = 50) -> CommandPage:
        _matches(project, _PROJECT)
        if _CURSOR.fullmatch(cursor) is None or not 1 <= limit <= MAX_PAGE_ITEMS:
            raise ValueError("pagination commandes DevHub invalide")
        query = urllib.parse.urlencode({"cursor": cursor, "limit": limit})
        raw = _exact(
            self._request("GET", f"/projects/{project}/commands?{query}"),
            {"contract", "items", "page"}, "command-page",
        )
        page = _exact(raw["page"], {"returned_count", "next_cursor", "truncated"}, "page")
        if raw["contract"] != COMMAND_CONTRACT or not isinstance(raw["items"], list):
            raise DevHubCommandError("PARSE", "command-page", None, "invalid_response")
        items = tuple(parse_command(item) for item in raw["items"])
        next_cursor = page["next_cursor"]
        if next_cursor is not None and (not isinstance(next_cursor, str) or _CURSOR.fullmatch(next_cursor) is None):
            raise DevHubCommandError("PARSE", "command-page", None, "invalid_response")
        if (type(page["returned_count"]) is not int or page["returned_count"] != len(items)
                or type(page["truncated"]) is not bool
                or page["truncated"] != (next_cursor is not None)):
            raise DevHubCommandError("PARSE", "command-page", None, "invalid_response")
        return CommandPage(items, next_cursor, page["truncated"])

    def get_command(self, command_id: str) -> DevHubCommand:
        return parse_command(self._request("GET", f"/commands/{_matches(command_id, _IDENTIFIER)}"))

    def claim(
        self, command: DevHubCommand, *, worker_id: str, lease_seconds: int,
        idempotency_key: str,
    ) -> DevHubCommand:
        _matches(worker_id, _IDENTIFIER)
        if type(lease_seconds) is not int or not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS:
            raise ValueError("durée de lease commandes DevHub invalide")
        return parse_command(self._request(
            "POST", f"/projects/{command.project}/commands/pull",
            {"command_id": command.id, "worker_id": worker_id, "lease_seconds": lease_seconds},
            version=command.version, idempotency_key=idempotency_key,
        ))

    def heartbeat(
        self, command: DevHubCommand, *, lease_id: str, extend_seconds: int,
        idempotency_key: str,
    ) -> DevHubCommand:
        _matches(lease_id, _IDENTIFIER)
        if type(extend_seconds) is not int or not MIN_LEASE_SECONDS <= extend_seconds <= MAX_LEASE_SECONDS:
            raise ValueError("extension de lease commandes DevHub invalide")
        return parse_command(self._request(
            "POST", f"/commands/{command.id}/heartbeats",
            {"lease_id": lease_id, "extend_seconds": extend_seconds},
            version=command.version, idempotency_key=idempotency_key,
        ))

    def append_event(
        self, command: DevHubCommand, *, event_id: str, sequence: int,
        event_type: str, occurred_at: int, lease_id: str,
        cost_cents: int | None = None, duration_ms: int | None = None,
        attempt_id: str | None = None, issue_id: str | None = None,
        receipts: Mapping[str, object] | None = None,
        idempotency_key: str,
    ) -> DevHubCommand:
        _matches(event_id, _IDENTIFIER)
        _matches(lease_id, _IDENTIFIER)
        if event_type not in _EVENT_STATES or type(sequence) is not int or sequence < 1:
            raise ValueError("événement commandes DevHub invalide")
        if type(occurred_at) is not int or occurred_at < 0:
            raise ValueError("horodatage commandes DevHub invalide")
        telemetry = None
        if cost_cents is not None or duration_ms is not None:
            if (cost_cents is not None and (type(cost_cents) is not int or not 0 <= cost_cents <= 1_000_000)):
                raise ValueError("coût commandes DevHub invalide")
            if (duration_ms is not None and (type(duration_ms) is not int or not 0 <= duration_ms <= 604_800_000)):
                raise ValueError("durée commandes DevHub invalide")
            telemetry = {"cost_cents": cost_cents, "duration_ms": duration_ms}
        extended = any(value is not None for value in (attempt_id, issue_id, receipts))
        if extended:
            if attempt_id is None or issue_id is None or receipts is None:
                raise ValueError("provenance événement DevHub incomplète")
            try:
                normalized_attempt = _matches(attempt_id, _IDENTIFIER)
                normalized_issue = _matches(issue_id, _ISSUE_ID)
            except ValueError:
                raise ValueError("provenance événement DevHub invalide") from None
            if normalized_issue != command.epic_id:
                raise ValueError("provenance événement DevHub hors commande")
            normalized_receipts = _event_receipts_wire(receipts)
            closure = normalized_receipts["epic_closure"]
            if (closure is not None
                    and closure.get("epic_id") != normalized_issue):
                raise ValueError("reçu clôture événement DevHub hors commande")
            event_body = {
                "schema_version": COMMAND_EVENT_CONTRACT,
                "event_id": event_id,
                "sequence": sequence,
                "type": event_type,
                "occurred_at": occurred_at,
                "lease_id": lease_id,
                "provenance": {
                    "command_id": command.id,
                    "attempt_id": normalized_attempt,
                    "issue_id": normalized_issue,
                    "source": "foundry",
                },
                "receipts": normalized_receipts,
                "telemetry": {
                    "input_tokens": None,
                    "cached_input_tokens": None,
                    "output_tokens": None,
                    "reasoning_tokens": None,
                    "tool_tokens": None,
                    "cost_cents": cost_cents,
                    "duration_ms": duration_ms,
                },
            }
        else:
            event_body = {
                "event_id": event_id,
                "sequence": sequence,
                "type": event_type,
                "occurred_at": occurred_at,
                "lease_id": lease_id,
                "telemetry": telemetry,
            }
        return parse_command(self._request(
            "POST", f"/commands/{command.id}/events",
            event_body,
            version=command.version, idempotency_key=idempotency_key,
        ))

    def list_events(
        self, command_id: str, *, cursor: str = "0", limit: int = 100,
    ) -> CommandEventPage:
        _matches(command_id, _IDENTIFIER)
        if _CURSOR.fullmatch(cursor) is None or not 1 <= limit <= MAX_PAGE_ITEMS:
            raise ValueError("pagination événements DevHub invalide")
        query = urllib.parse.urlencode({"cursor": cursor, "limit": limit})
        raw = _exact(
            self._request("GET", f"/commands/{command_id}/events?{query}"),
            {"contract", "items", "page", "projection"}, "event-page",
        )
        page = _exact(raw["page"], {"returned_count", "next_cursor", "truncated"}, "page")
        projection = _exact(raw["projection"], {"stale", "next_sequence"}, "projection")
        if raw["contract"] != COMMAND_CONTRACT or not isinstance(raw["items"], list):
            raise DevHubCommandError("PARSE", "event-page", None, "invalid_response")
        items = tuple(parse_event(item) for item in raw["items"])
        next_cursor = page["next_cursor"]
        if next_cursor is not None and (not isinstance(next_cursor, str) or _CURSOR.fullmatch(next_cursor) is None):
            raise DevHubCommandError("PARSE", "event-page", None, "invalid_response")
        if (type(page["returned_count"]) is not int or page["returned_count"] != len(items)
                or type(page["truncated"]) is not bool
                or page["truncated"] != (next_cursor is not None)
                or type(projection["stale"]) is not bool
                or type(projection["next_sequence"]) is not int
                or projection["next_sequence"] < 1):
            raise DevHubCommandError("PARSE", "event-page", None, "invalid_response")
        return CommandEventPage(
            items, next_cursor, page["truncated"],
            projection["stale"], projection["next_sequence"],
        )

    def reconcile_terminal(
        self, command: DevHubCommand, *, reconciliation_id: str,
        terminal_event_id: str, epic_version: int,
    ) -> TerminalReconciliation:
        """Record one existing terminal event without acquiring execution authority."""
        _matches(reconciliation_id, _IDENTIFIER)
        _matches(terminal_event_id, _IDENTIFIER)
        if type(epic_version) is not int or epic_version < 1:
            raise ValueError("version Epic de réconciliation terminale invalide")
        receipt = parse_terminal_reconciliation(self._request(
            "POST", f"/commands/{command.id}/terminal-reconciliations",
            {
                "reconciliation_id": reconciliation_id,
                "terminal_event_id": terminal_event_id,
                "epic_version": epic_version,
            },
            version=command.version, idempotency_key=reconciliation_id,
        ))
        if (receipt.id != reconciliation_id
                or receipt.command_id != command.id
                or receipt.terminal_event_id != terminal_event_id
                or receipt.epic_id != command.epic_id
                or receipt.reconciled_epic_version != epic_version):
            raise DevHubCommandError(
                "PARSE", "terminal-reconciliation", None, "binding_mismatch",
            )
        return receipt

    def publish_late_terminal(
        self, command: DevHubCommand, *, publication_id: str, event_id: str,
        sequence: int, occurred_at: int, lease_id: str, attempt_id: str,
        original_closure: dict, cost_cents: int | None, duration_ms: int | None,
    ) -> TerminalReconciliation:
        """Publish an existing original close, with no lease/execution authority."""
        for value in (publication_id, event_id, lease_id, attempt_id):
            _matches(value, _IDENTIFIER)
        if (type(sequence) is not int or sequence < 1
                or type(occurred_at) is not int or occurred_at < 0):
            raise ValueError("coordonnées publication tardive invalides")
        try:
            project_id = original_closure["outcome"]["receipt"]["project_id"]
        except (KeyError, TypeError):
            raise ValueError("original de clôture absent") from None
        original = DevHubTracker._to_epic_closure_outcome(
            original_closure, project=Project(command.project, project_id),
            parent_id=command.epic_id,
        )
        closure = {
            "contract": EPIC_CLOSURE_COORDINATE_CONTRACT,
            "epic_id": command.epic_id, "state": "closed",
            "receipt_id": original.audit_id,
            "parent_version": original.closed_parent_version,
            "children": [{"issue_id": child.id, "version": child.version,
                          "state": child.state} for child in original.receipt.children],
        }
        expected_digest = hashlib.sha256(json.dumps(
            closure, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        if (command.lease is None or lease_id != command.lease.id
                or sequence != command.next_event_sequence
                or occurred_at < original.receipt.issued_at
                or (cost_cents is not None and (
                    type(cost_cents) is not int or not 0 <= cost_cents <= command.max_cost_cents))
                or (duration_ms is not None and (
                    type(duration_ms) is not int or not 0 <= duration_ms <= 604_800_000))):
            raise ValueError("binding publication tardive invalide")
        body = {
            "schema_version": "devhub-foundry-late-terminal-publication.v1",
            "publication_id": publication_id, "event_id": event_id,
            "sequence": sequence, "occurred_at": occurred_at,
            "lease_id": lease_id, "attempt_id": attempt_id,
            "original_closure": original_closure,
            "telemetry": {
                "input_tokens": None, "cached_input_tokens": None,
                "output_tokens": None, "reasoning_tokens": None,
                "tool_tokens": None, "cost_cents": cost_cents,
                "duration_ms": duration_ms,
            },
        }
        # One exact retry resolves a lost response; never regenerate coordinates.
        for attempt in range(2):
            try:
                raw = self._request(
                    "POST", f"/commands/{command.id}/late-terminal-publications",
                    body, version=command.version, idempotency_key=publication_id,
                )
                break
            except DevHubCommandTransportError:
                if attempt:
                    raise
        receipt = parse_terminal_reconciliation(raw)
        preview_lineage_gap = (
            original.receipt.parent_version - receipt.preview_epic_version
        )
        if (receipt.id != publication_id or receipt.command_id != command.id
                or receipt.terminal_event_id != event_id
                or receipt.epic_id != command.epic_id
                or preview_lineage_gap not in {0, 1}
                or receipt.reconciled_epic_version != original.closed_parent_version
                or receipt.closure_digest != expected_digest
                or receipt.cost_cents != cost_cents or receipt.duration_ms != duration_ms):
            raise DevHubCommandError("PARSE", "late-terminal-publication", None, "binding_mismatch")
        return receipt

    def append_passive_observation(
        self, command_id: str, expected_version: int, event: dict,
    ) -> dict:
        """Append one DEVHUB-27 passive observation without a lifecycle write."""
        _matches(command_id, _IDENTIFIER)
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("version observation passive invalide")
        if not isinstance(event, dict) or not _matches(event.get("observation_id"), _IDENTIFIER):
            raise ValueError("observation passive invalide")
        response = self._request(
            "POST", f"/commands/{command_id}/observations", event,
            version=expected_version, idempotency_key=event["observation_id"],
            expected_contract=OBSERVATION_CONTRACT,
        )
        observation = _exact(response, {
            "schema_version", "id", "command_id", "sequence", "type", "actor",
            "observed_at", "received_at", "provenance", "receipts", "telemetry",
        }, "passive-observation")
        if (observation["schema_version"] != OBSERVATION_CONTRACT
                or observation["id"] != event["observation_id"]
                or observation["command_id"] != command_id
                or observation["sequence"] != event.get("sequence")):
            raise DevHubCommandError("PARSE", "passive-observation", None, "invalid_response")
        return {
            "accepted": True,
            "observation": {"id": observation["id"], "sequence": observation["sequence"]},
        }

    def reconcile_passive_observations(self, command_id: str) -> dict:
        """Read the passive journal; neither lifecycle nor lease is mutated."""
        _matches(command_id, _IDENTIFIER)
        cursor = "0"
        observations = []
        identities: set[tuple[str, int]] = set()
        visited = {cursor}
        reconciliation = None
        for _page_number in range(10):
            query = urllib.parse.urlencode({"cursor": cursor, "limit": MAX_PAGE_ITEMS})
            raw = _exact(
                self._request(
                    "GET", f"/commands/{command_id}/observations?{query}",
                    expected_contract=OBSERVATION_CONTRACT,
                ),
                {"schema_version", "command_id", "items", "page", "reconciliation"},
                "passive-observations",
            )
            current_reconciliation = raw["reconciliation"]
            items = raw["items"]
            page = raw["page"]
            if (raw["schema_version"] != OBSERVATION_CONTRACT
                    or raw["command_id"] != command_id
                    or not isinstance(current_reconciliation, dict) or not isinstance(items, list)
                    or not isinstance(page, dict)
                    or current_reconciliation.get("status") not in {"current", "unknown"}
                    or type(current_reconciliation.get("stale")) is not bool
                    or type(current_reconciliation.get("next_sequence")) is not int
                    or type(page.get("returned_count")) is not int
                    or page["returned_count"] != len(items)
                    or type(page.get("truncated")) is not bool):
                raise DevHubCommandError("PARSE", "passive-observations", None, "invalid_response")
            coordinates = (
                current_reconciliation["status"], current_reconciliation["stale"],
                current_reconciliation["next_sequence"],
            )
            if reconciliation is not None and reconciliation != coordinates:
                raise DevHubCommandError("PARSE", "passive-observations", None, "projection_changed")
            reconciliation = coordinates
            for item in items:
                if not isinstance(item, dict):
                    raise DevHubCommandError("PARSE", "passive-observations", None, "invalid_response")
                event_id = item.get("id")
                sequence = item.get("sequence")
                if not isinstance(event_id, str) or type(sequence) is not int:
                    raise DevHubCommandError("PARSE", "passive-observations", None, "invalid_response")
                identity = (event_id, sequence)
                if identity in identities:
                    raise DevHubCommandError("PARSE", "passive-observations", None, "invalid_response")
                identities.add(identity)
                observations.append({"event_id": event_id, "sequence": sequence})
            next_cursor = page.get("next_cursor")
            if page["truncated"] != (next_cursor is not None):
                raise DevHubCommandError("PARSE", "passive-observations", None, "invalid_response")
            if next_cursor is None:
                break
            if (not isinstance(next_cursor, str) or _CURSOR.fullmatch(next_cursor) is None
                    or next_cursor in visited):
                raise DevHubCommandError("PARSE", "passive-observations", None, "invalid_response")
            cursor = next_cursor
            visited.add(cursor)
        else:
            raise DevHubCommandError("PARSE", "passive-observations", None, "pagination_exhausted")
        if reconciliation is None:
            raise DevHubCommandError("PARSE", "passive-observations", None, "invalid_response")
        command = self.get_command(command_id)
        return {
            "command": {
                "version": command.version,
                "next_event_sequence": reconciliation[2],
                "projection_stale": reconciliation[1],
                "observation_status": reconciliation[0],
            },
            "events": observations,
        }
