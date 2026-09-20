"""Additive FOUNDRY-61 Codex trace contract and offline command binding.

This module does not invoke a host and is not imported by production routing. Raw Codex
events are accepted only through an explicitly versioned v2 binding, validated in
memory, projected to an exact content-free schema, and never persisted here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping


TRACE_CONTRACT_ARTIFACT = "foundry.codex_exec.trace_contract"
TRACE_OBSERVATION_ARTIFACT = "foundry.codex_exec.trace_observation"
TRACE_VERSION = 2
HOST_VERSION = "0.147.0"
COMMAND_CONTRACT_ID = "codex-exec-json-v2"
MAX_TRACE_BYTES = 16 * 1024
MAX_EVENTS = 64
MAX_IDENTIFIER_BYTES = 256
MAX_TRANSIENT_TEXT_BYTES = 4 * 1024
MAX_PACKET_BYTES = 16 * 1024
PACKET_ID = re.compile(r"f61-packet-(?:00[1-9]|01[0-2])\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")

CODEX_PROFILES = {
    "codex-terra-low": ("gpt-5.6-terra", "low", "implementer", "balanced", True),
    "codex-terra-medium": (
        "gpt-5.6-terra", "medium", "implementer", "balanced", False,
    ),
    "codex-terra-high": ("gpt-5.6-terra", "high", "implementer", "balanced", True),
    "codex-sol-high": ("gpt-5.6-sol", "high", "reviewer", "frontier", False),
}
REQUIRED_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
OPTIONAL_USAGE_FIELDS = ("cache_write_input_tokens",)
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
OBSERVATION_FIELDS = {
    "artifact", "version", "profile_id", "packet_id", "host_version", "model",
    "reasoning_effort", "terminal", "event_summary", "metrics", "raw_retained",
    "diagnostic_code",
}
FORBIDDEN_PERSISTED_KEYS = {
    "prompt", "response", "excerpt", "text", "message", "command", "path", "code",
    "thread_id", "packet_sha256", "revision", "context_policy", "secret", "credential",
    "user_identifier", "raw_jsonl",
}


class CodexTraceV2Error(ValueError):
    """A native event stream is outside the explicit v2 benchmark contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CodexTraceV2Error(message)


def _utf8_size(value: object) -> int | None:
    if type(value) is not str:
        return None
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _bounded_text(value: object, label: str) -> str:
    size = _utf8_size(value)
    _require(
        size is not None and 0 < size <= MAX_TRANSIENT_TEXT_BYTES,
        f"{label} differs",
    )
    return value


def _identifier(value: object, label: str) -> str:
    size = _utf8_size(value)
    _require(
        size is not None and 0 < size <= MAX_IDENTIFIER_BYTES,
        f"{label} differs",
    )
    return value


@dataclass(frozen=True)
class CodexTraceBindingV2:
    """Content-free binding established before a future benchmark invocation."""

    artifact: str
    version: int
    command_contract_id: str
    host_version: str
    profile_id: str
    packet_id: str
    packet_sha256: str
    revision: str
    context_policy: str


@dataclass(frozen=True)
class CodexTraceInputV2:
    """Transient native bytes paired with their preflighted v2 binding."""

    binding: CodexTraceBindingV2
    raw_jsonl: bytes


def validate_binding(value: object) -> CodexTraceBindingV2:
    _require(type(value) is CodexTraceBindingV2, "Codex v2 binding differs")
    _require(
        value.artifact == TRACE_CONTRACT_ARTIFACT
        and value.version == TRACE_VERSION
        and value.command_contract_id == COMMAND_CONTRACT_ID
        and value.host_version == HOST_VERSION,
        "Codex v2 binding identity differs",
    )
    _require(value.profile_id in CODEX_PROFILES, "Codex v2 profile differs")
    _require(
        type(value.packet_id) is str and PACKET_ID.fullmatch(value.packet_id) is not None,
        "Codex v2 packet binding differs",
    )
    _require(
        type(value.packet_sha256) is str
        and SHA256.fullmatch(value.packet_sha256) is not None,
        "Codex v2 packet fingerprint differs",
    )
    _require(
        type(value.revision) is str and REVISION.fullmatch(value.revision) is not None,
        "Codex v2 snapshot binding differs",
    )
    _require(value.context_policy == "baseline-v1", "Codex v2 context policy differs")
    return value


def codex_command_v2(
    binding: CodexTraceBindingV2,
    *,
    packet_id: str,
    task_packet: str,
    revision: str,
    context_policy: str,
) -> tuple[str, ...]:
    """Materialize the frozen future command without launching it."""
    binding = validate_binding(binding)
    packet_size = _utf8_size(task_packet)
    _require(
        packet_size is not None
        and 0 < packet_size <= MAX_PACKET_BYTES
        and binding.packet_sha256
        == hashlib.sha256(task_packet.encode("utf-8")).hexdigest(),
        "Codex v2 packet fingerprint differs",
    )
    _require(binding.packet_id == packet_id, "Codex v2 packet binding differs")
    _require(binding.revision == revision, "Codex v2 snapshot binding differs")
    _require(
        context_policy == "baseline-v1" and binding.context_policy == context_policy,
        "Codex v2 context policy differs",
    )
    model, effort, _role, _tier, _experimental = CODEX_PROFILES[binding.profile_id]
    return (
        "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--sandbox", "read-only", "--json", "--model", model,
        "-c", f'model_reasoning_effort="{effort}"', "-",
    )


def _decode_events(raw: object) -> list[dict[str, object]]:
    _require(type(raw) is bytes, "Codex v2 trace must be bytes")
    _require(0 < len(raw) <= MAX_TRACE_BYTES, "Codex v2 trace byte cap differs")
    _require(raw.endswith(b"\n"), "Codex v2 trace must end with LF")

    def reject_constant(_value: str) -> None:
        raise ValueError("non-standard JSON constant")

    def exact_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, nested in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = nested
        return value

    try:
        lines = raw.splitlines()
        events = [
            json.loads(
                line,
                parse_constant=reject_constant,
                object_pairs_hook=exact_object,
            )
            for line in lines
        ]
    except (UnicodeDecodeError, ValueError) as exc:
        raise CodexTraceV2Error("Codex v2 trace is invalid JSONL") from exc
    _require(
        3 <= len(events) <= MAX_EVENTS
        and all(line for line in lines)
        and all(type(event) is dict for event in events),
        "Codex v2 trace event count differs",
    )
    return events


def _usage(value: object) -> dict[str, int]:
    _require(
        type(value) is dict
        and set(REQUIRED_USAGE_FIELDS) <= set(value) <= set(USAGE_FIELDS),
        "Codex v2 usage schema differs",
    )
    clean = {}
    for name in value:
        metric = value[name]
        if name in OPTIONAL_USAGE_FIELDS and metric is None:
            continue
        _require(type(metric) is int and metric >= 0, f"Codex v2 usage differs: {name}")
        clean[name] = metric
    return clean


def _metric(value: int | None, provenance: str) -> dict[str, object]:
    return {"value": value, "provenance": provenance}


def _observation(
    binding: CodexTraceBindingV2,
    *,
    terminal: Mapping[str, str],
    terminal_event: str,
    event_count: int,
    agent_messages: int,
    reasoning_items: int,
    top_level_errors: int,
    usage: Mapping[str, int] | None,
    diagnostic_code: str | None,
) -> dict[str, object]:
    model, effort, _role, _tier, _experimental = CODEX_PROFILES[binding.profile_id]
    metrics = {
        name: _metric(
            usage[name] if usage is not None and name in usage else None,
            "host_reported" if usage is not None and name in usage else "unavailable",
        )
        for name in USAGE_FIELDS
    }
    return {
        "artifact": TRACE_OBSERVATION_ARTIFACT,
        "version": TRACE_VERSION,
        "profile_id": binding.profile_id,
        "packet_id": binding.packet_id,
        "host_version": binding.host_version,
        "model": model,
        "reasoning_effort": effort,
        "terminal": dict(terminal),
        "event_summary": {
            "terminal_event": terminal_event,
            "event_count": event_count,
            "agent_message_count": agent_messages,
            "reasoning_item_count": reasoning_items,
            "top_level_error_count": top_level_errors,
        },
        "metrics": metrics,
        "raw_retained": False,
        "diagnostic_code": diagnostic_code,
    }


def validate_observation(value: object) -> dict[str, object]:
    """Validate that a persisted projection cannot contain transient host content."""
    _require(type(value) is dict and set(value) == OBSERVATION_FIELDS,
             "Codex v2 observation schema differs")
    _require(
        value.get("artifact") == TRACE_OBSERVATION_ARTIFACT
        and value.get("version") == TRACE_VERSION
        and value.get("profile_id") in CODEX_PROFILES
        and value.get("host_version") == HOST_VERSION
        and value.get("raw_retained") is False,
        "Codex v2 observation identity differs",
    )
    _require(
        type(value.get("packet_id")) is str
        and PACKET_ID.fullmatch(value["packet_id"]) is not None,
        "Codex v2 observation packet differs",
    )
    model, effort, _role, _tier, _experimental = CODEX_PROFILES[value["profile_id"]]
    _require(
        value.get("model") == model and value.get("reasoning_effort") == effort,
        "Codex v2 observation profile binding differs",
    )
    terminal = value.get("terminal")
    _require(
        terminal in (
            {"status": "completed", "failure_class": "none"},
            {"status": "failed", "failure_class": "host"},
            {"status": "unknown", "failure_class": "unknown"},
        ),
        "Codex v2 terminal differs",
    )
    summary = value.get("event_summary")
    _require(
        type(summary) is dict
        and set(summary) == {
            "terminal_event", "event_count", "agent_message_count",
            "reasoning_item_count", "top_level_error_count",
        }
        and summary.get("terminal_event") in {
            "turn.completed", "turn.failed", "stream.invalid",
        }
        and all(
            type(summary.get(name)) is int and summary[name] >= 0
            for name in (
                "event_count", "agent_message_count", "reasoning_item_count",
                "top_level_error_count",
            )
        ),
        "Codex v2 event summary differs",
    )
    metrics = value.get("metrics")
    _require(type(metrics) is dict and set(metrics) == set(USAGE_FIELDS),
             "Codex v2 metric set differs")
    for name, metric in metrics.items():
        _require(
            type(metric) is dict
            and set(metric) == {"value", "provenance"}
            and metric["provenance"] in {"host_reported", "unavailable"}
            and (metric["value"] is None) == (metric["provenance"] == "unavailable")
            and (
                metric["value"] is None
                or (type(metric["value"]) is int and metric["value"] >= 0)
            ),
            f"Codex v2 metric differs: {name}",
        )
    diagnostic = value.get("diagnostic_code")
    _require(
        diagnostic in {None, "CODEX_TRACE_V2_INVALID"}
        and (diagnostic is None) == (terminal["status"] != "unknown"),
        "Codex v2 diagnostic differs",
    )
    status = terminal["status"]
    metric_provenance = {metric["provenance"] for metric in metrics.values()}
    if status == "completed":
        _require(
            summary["terminal_event"] == "turn.completed"
            and summary["agent_message_count"] > 0
            and summary["top_level_error_count"] == 0
            and all(
                metrics[name]["provenance"] == "host_reported"
                for name in REQUIRED_USAGE_FIELDS
            )
            and all(
                metrics[name]["provenance"] in {"host_reported", "unavailable"}
                for name in OPTIONAL_USAGE_FIELDS
            ),
            "Codex v2 completed observation differs",
        )
    elif status == "failed":
        _require(
            summary["terminal_event"] == "turn.failed"
            and metric_provenance == {"unavailable"},
            "Codex v2 failed observation differs",
        )
    else:
        _require(
            summary == {
                "terminal_event": "stream.invalid",
                "event_count": 0,
                "agent_message_count": 0,
                "reasoning_item_count": 0,
                "top_level_error_count": 0,
            }
            and metric_provenance == {"unavailable"},
            "Codex v2 unknown observation differs",
        )
    _require(
        not (FORBIDDEN_PERSISTED_KEYS & set(_walk_keys(value))),
        "Codex v2 observation contains transient content",
    )
    return dict(value)


def _walk_keys(value: object):
    if type(value) is dict:
        for key, nested in value.items():
            if type(key) is str:
                yield key.casefold()
            yield from _walk_keys(nested)
    elif type(value) is list:
        for nested in value:
            yield from _walk_keys(nested)


def parse_codex_trace_v2(value: object) -> dict[str, object]:
    """Strictly parse one native 0.147.0 stream into a content-free observation."""
    _require(type(value) is CodexTraceInputV2, "Codex v2 trace envelope differs")
    binding = validate_binding(value.binding)
    events = _decode_events(value.raw_jsonl)
    first, second = events[0], events[1]
    _require(
        set(first) == {"type", "thread_id"}
        and first.get("type") == "thread.started",
        "Codex v2 thread start differs",
    )
    _identifier(first.get("thread_id"), "Codex v2 thread id")
    _require(second == {"type": "turn.started"}, "Codex v2 turn start differs")

    active_items: dict[str, str] = {}
    completed_items: set[str] = set()
    agent_messages = 0
    reasoning_items = 0
    top_level_errors = 0
    for index, event in enumerate(events[2:], start=2):
        event_type = event.get("type")
        final = index == len(events) - 1
        if event_type in {"item.started", "item.updated", "item.completed"}:
            _require(not final and set(event) == {"type", "item"},
                     "Codex v2 item event order differs")
            item = event.get("item")
            _require(type(item) is dict, "Codex v2 item differs")
            _require(set(item) == {"id", "type", "text"},
                     "Codex v2 benchmark item schema differs")
            item_id = _identifier(item.get("id"), "Codex v2 item id")
            _bounded_text(item.get("text"), "Codex v2 transient item text")
            _require(item.get("type") in {"agent_message", "reasoning"},
                     "Codex v2 benchmark item type differs")
            item_type = item["type"]
            if event_type == "item.started":
                _require(
                    item_id not in active_items and item_id not in completed_items,
                    "Codex v2 item lifecycle differs",
                )
                active_items[item_id] = item_type
            elif event_type == "item.updated":
                _require(
                    active_items.get(item_id) == item_type,
                    "Codex v2 item lifecycle differs",
                )
            else:
                _require(item_id not in completed_items, "Codex v2 item id is duplicated")
                if item_id in active_items:
                    _require(
                        active_items.pop(item_id) == item_type,
                        "Codex v2 item lifecycle differs",
                    )
                completed_items.add(item_id)
                if item_type == "agent_message":
                    agent_messages += 1
                else:
                    reasoning_items += 1
            continue
        if event_type == "error":
            _require(not final and set(event) == {"type", "message"},
                     "Codex v2 top-level error differs")
            _bounded_text(event.get("message"), "Codex v2 transient error")
            top_level_errors += 1
            continue
        if event_type == "turn.completed":
            _require(
                final
                and set(event) == {"type", "usage"}
                and top_level_errors == 0
                and agent_messages > 0
                and not active_items,
                "Codex v2 success terminal differs",
            )
            usage = _usage(event.get("usage"))
            return validate_observation(_observation(
                binding,
                terminal={"status": "completed", "failure_class": "none"},
                terminal_event="turn.completed",
                event_count=len(events),
                agent_messages=agent_messages,
                reasoning_items=reasoning_items,
                top_level_errors=top_level_errors,
                usage=usage,
                diagnostic_code=None,
            ))
        if event_type == "turn.failed":
            _require(final and set(event) == {"type", "error"},
                     "Codex v2 failure terminal differs")
            error = event.get("error")
            _require(type(error) is dict and set(error) == {"message"},
                     "Codex v2 failure error differs")
            _bounded_text(error.get("message"), "Codex v2 transient failure")
            return validate_observation(_observation(
                binding,
                terminal={"status": "failed", "failure_class": "host"},
                terminal_event="turn.failed",
                event_count=len(events),
                agent_messages=agent_messages,
                reasoning_items=reasoning_items,
                top_level_errors=top_level_errors,
                usage=None,
                diagnostic_code=None,
            ))
        raise CodexTraceV2Error("Codex v2 event type differs")
    raise CodexTraceV2Error("Codex v2 terminal event is missing")


def observe_codex_trace_v2(
    binding: CodexTraceBindingV2,
    raw_jsonl: object,
) -> dict[str, object]:
    """Fail closed to one sanitized unknown row for an invalid post-launch stream."""
    binding = validate_binding(binding)
    try:
        return parse_codex_trace_v2(CodexTraceInputV2(binding, raw_jsonl))
    except (CodexTraceV2Error, UnicodeError):
        return validate_observation(_observation(
            binding,
            terminal={"status": "unknown", "failure_class": "unknown"},
            terminal_event="stream.invalid",
            event_count=0,
            agent_messages=0,
            reasoning_items=0,
            top_level_errors=0,
            usage=None,
            diagnostic_code="CODEX_TRACE_V2_INVALID",
        ))
