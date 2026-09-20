"""Offline positive and fail-closed tests for the FOUNDRY-61 Codex v2 trace."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json

import pytest

from foundry import measurement_harness_v2 as harness


TASK_PACKET = "Goal:\nFrozen.\nInputs:\nPair.\nConstraints:\nOffline.\nDone when:\nBound."
REVISION = "3fec3f1677ae6bd3ab5ef069afd6ca3073901b46"
PACKET_SHA256 = hashlib.sha256(TASK_PACKET.encode("utf-8")).hexdigest()


def _binding(profile_id: str = "codex-terra-medium", packet_id: str = "f61-packet-001"):
    return harness.CodexTraceBindingV2(
        harness.TRACE_CONTRACT_ARTIFACT,
        harness.TRACE_VERSION,
        harness.COMMAND_CONTRACT_ID,
        harness.HOST_VERSION,
        profile_id,
        packet_id,
        PACKET_SHA256,
        REVISION,
        "baseline-v1",
    )


def _jsonl(events: list[dict[str, object]]) -> bytes:
    return b"".join(
        json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
        for event in events
    )


def _usage() -> dict[str, int]:
    return {
        "input_tokens": 12,
        "cached_input_tokens": 5,
        "cache_write_input_tokens": 0,
        "output_tokens": 8,
        "reasoning_output_tokens": 2,
    }


def _success_events() -> list[dict[str, object]]:
    return [
        {"type": "thread.started", "thread_id": "offline-thread"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "reason-1", "type": "reasoning", "text": "transient"},
        },
        {
            "type": "item.completed",
            "item": {"id": "answer-1", "type": "agent_message", "text": "transient"},
        },
        {"type": "turn.completed", "usage": _usage()},
    ]


def test_conforming_native_success_is_accepted_and_projected_without_content():
    observation = harness.parse_codex_trace_v2(
        harness.CodexTraceInputV2(_binding(), _jsonl(_success_events())),
    )

    assert observation["terminal"] == {"status": "completed", "failure_class": "none"}
    assert observation["event_summary"] == {
        "terminal_event": "turn.completed",
        "event_count": 5,
        "agent_message_count": 1,
        "reasoning_item_count": 1,
        "top_level_error_count": 0,
    }
    assert observation["metrics"]["cache_write_input_tokens"] == {
        "value": 0, "provenance": "host_reported",
    }
    encoded = json.dumps(observation)
    assert "offline-thread" not in encoded
    assert "transient" not in encoded
    assert PACKET_SHA256 not in encoded
    assert REVISION not in encoded
    assert "baseline-v1" not in encoded
    assert observation["raw_retained"] is False


@pytest.mark.parametrize(
    "usage",
    [
        {key: value for key, value in _usage().items()
         if key != "cache_write_input_tokens"},
        {**_usage(), "cache_write_input_tokens": None},
    ],
)
def test_optional_cache_write_usage_absent_or_null_remains_unavailable(usage):
    events = _success_events()
    events[-1] = {"type": "turn.completed", "usage": usage}

    observation = harness.parse_codex_trace_v2(
        harness.CodexTraceInputV2(_binding(), _jsonl(events)),
    )

    assert observation["terminal"]["status"] == "completed"
    assert observation["metrics"]["cache_write_input_tokens"] == {
        "value": None, "provenance": "unavailable",
    }
    assert all(
        observation["metrics"][name]["provenance"] == "host_reported"
        for name in harness.REQUIRED_USAGE_FIELDS
    )


def test_documented_native_item_started_and_updated_lifecycle_is_accepted():
    events = [
        {"type": "thread.started", "thread_id": "offline-thread"},
        {"type": "turn.started"},
        {
            "type": "item.started",
            "item": {"id": "reason-1", "type": "reasoning", "text": "transient"},
        },
        {
            "type": "item.updated",
            "item": {"id": "reason-1", "type": "reasoning", "text": "transient"},
        },
        {
            "type": "item.completed",
            "item": {"id": "reason-1", "type": "reasoning", "text": "transient"},
        },
        {
            "type": "item.started",
            "item": {"id": "answer-1", "type": "agent_message", "text": "transient"},
        },
        {
            "type": "item.updated",
            "item": {"id": "answer-1", "type": "agent_message", "text": "transient"},
        },
        {
            "type": "item.completed",
            "item": {"id": "answer-1", "type": "agent_message", "text": "transient"},
        },
        {"type": "turn.completed", "usage": _usage()},
    ]

    observation = harness.parse_codex_trace_v2(
        harness.CodexTraceInputV2(_binding(), _jsonl(events)),
    )

    assert observation["terminal"]["status"] == "completed"
    assert observation["event_summary"] == {
        "terminal_event": "turn.completed",
        "event_count": 9,
        "agent_message_count": 1,
        "reasoning_item_count": 1,
        "top_level_error_count": 0,
    }
    assert "transient" not in json.dumps(observation)


def test_conforming_native_failure_is_accepted_with_all_metrics_null():
    events = [
        {"type": "thread.started", "thread_id": "offline-thread"},
        {"type": "turn.started"},
        {"type": "error", "message": "transient notification"},
        {"type": "turn.failed", "error": {"message": "transient failure"}},
    ]

    observation = harness.parse_codex_trace_v2(
        harness.CodexTraceInputV2(_binding("codex-sol-high"), _jsonl(events)),
    )

    assert observation["terminal"] == {"status": "failed", "failure_class": "host"}
    assert all(metric == {"value": None, "provenance": "unavailable"}
               for metric in observation["metrics"].values())
    assert "transient" not in json.dumps(observation)


def test_unbound_native_or_legacy_jsonl_is_never_implicitly_accepted():
    raw = _jsonl(_success_events())
    with pytest.raises(harness.CodexTraceV2Error, match="envelope"):
        harness.parse_codex_trace_v2(raw)
    with pytest.raises(harness.CodexTraceV2Error, match="envelope"):
        harness.parse_codex_trace_v2({"binding": _binding(), "raw_jsonl": raw})


@pytest.mark.parametrize(
    "binding",
    [
        replace(_binding(), version=1),
        replace(_binding(), command_contract_id="legacy-command"),
        replace(_binding(), host_version="0.148.0"),
        replace(_binding(), profile_id="codex-luna-low"),
        replace(_binding(), packet_id="legacy-packet"),
        replace(_binding(), packet_sha256="0" * 63),
        replace(_binding(), revision="0" * 39),
        replace(_binding(), context_policy="lean-v1"),
    ],
)
def test_binding_drift_is_rejected_before_native_bytes(binding):
    with pytest.raises(
        harness.CodexTraceV2Error,
        match="binding|profile|packet|fingerprint|snapshot|context policy",
    ):
        harness.parse_codex_trace_v2(
            harness.CodexTraceInputV2(binding, _jsonl(_success_events())),
        )


def _mutate_body(event: dict[str, object]) -> bytes:
    events = _success_events()
    events[2] = event
    return _jsonl(events)


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        (
            _mutate_body({
                "type": "item.updated",
                "item": {"id": "reason-1", "type": "reasoning", "text": "transient"},
            }),
            "lifecycle",
        ),
        (
            _mutate_body({
                "type": "item.completed",
                "item": {
                    "id": "command-1", "type": "command_execution",
                    "command": "unsafe", "output": "transient", "status": "completed",
                },
            }),
            "item schema",
        ),
        (_mutate_body({"type": "future.event"}), "event type"),
        (_jsonl(_success_events()[:-1]), "order|terminal"),
        (_jsonl(_success_events() + [{"type": "turn.started"}]), "success terminal"),
        (_jsonl([*_success_events()[:2], _success_events()[-1]]), "success terminal"),
        (_jsonl([
            *_success_events()[:2],
            {"type": "error", "message": "transient"},
            *_success_events()[3:],
        ]), "success terminal"),
        (b"{}\n{}", "end with LF"),
        (b"{not-json}\n{}\n{}\n", "invalid JSONL"),
        (b'{"type":"thread.started","thread_id":"a","thread_id":"b"}\n'
         b'{"type":"turn.started"}\n'
         b'{"type":"turn.failed","error":{"message":"x"}}\n', "invalid JSONL"),
    ],
)
def test_event_sequence_and_item_schema_drift_fail_closed(raw, match):
    with pytest.raises(harness.CodexTraceV2Error, match=match):
        harness.parse_codex_trace_v2(harness.CodexTraceInputV2(_binding(), raw))


@pytest.mark.parametrize(
    "usage",
    [
        {**_usage(), "future_tokens": 1},
        {key: value for key, value in _usage().items() if key != "output_tokens"},
        {**_usage(), "output_tokens": -1},
        {**_usage(), "output_tokens": True},
        {**_usage(), "output_tokens": None},
        {**_usage(), "cache_write_input_tokens": -1},
        {**_usage(), "cache_write_input_tokens": True},
    ],
)
def test_usage_schema_drift_fails_closed(usage):
    events = _success_events()
    events[-1] = {"type": "turn.completed", "usage": usage}
    raw = _jsonl(events)
    with pytest.raises(harness.CodexTraceV2Error, match="usage"):
        harness.parse_codex_trace_v2(
            harness.CodexTraceInputV2(_binding(), raw),
        )
    observation = harness.observe_codex_trace_v2(_binding(), raw)
    assert observation["terminal"] == {"status": "unknown", "failure_class": "unknown"}
    assert all(metric == {"value": None, "provenance": "unavailable"}
               for metric in observation["metrics"].values())


def test_duplicate_item_id_and_nonstandard_json_number_fail_closed():
    events = _success_events()
    events[3]["item"]["id"] = "reason-1"
    with pytest.raises(harness.CodexTraceV2Error, match="duplicated"):
        harness.parse_codex_trace_v2(
            harness.CodexTraceInputV2(_binding(), _jsonl(events)),
        )
    raw = _jsonl(_success_events()).replace(b'"output_tokens":8', b'"output_tokens":NaN')
    with pytest.raises(harness.CodexTraceV2Error, match="invalid JSONL"):
        harness.parse_codex_trace_v2(harness.CodexTraceInputV2(_binding(), raw))


@pytest.mark.parametrize(
    "events",
    [
        [
            {"type": "thread.started", "thread_id": "offline-thread"},
            {"type": "turn.started"},
            {"type": "item.started", "item": {
                "id": "answer-1", "type": "agent_message", "text": "transient",
            }},
            {"type": "item.started", "item": {
                "id": "answer-1", "type": "agent_message", "text": "transient",
            }},
            {"type": "turn.completed", "usage": _usage()},
        ],
        [
            {"type": "thread.started", "thread_id": "offline-thread"},
            {"type": "turn.started"},
            {"type": "item.started", "item": {
                "id": "answer-1", "type": "agent_message", "text": "transient",
            }},
            {"type": "item.updated", "item": {
                "id": "answer-1", "type": "reasoning", "text": "transient",
            }},
            {"type": "turn.completed", "usage": _usage()},
        ],
        [
            {"type": "thread.started", "thread_id": "offline-thread"},
            {"type": "turn.started"},
            {"type": "item.started", "item": {
                "id": "reason-1", "type": "reasoning", "text": "transient",
            }},
            {"type": "item.completed", "item": {
                "id": "answer-1", "type": "agent_message", "text": "transient",
            }},
            {"type": "turn.completed", "usage": _usage()},
        ],
        [
            {"type": "thread.started", "thread_id": "offline-thread"},
            {"type": "turn.started"},
            {"type": "item.started", "item": {
                "id": "command-1", "type": "command_execution", "text": "transient",
            }},
            {"type": "turn.failed", "error": {"message": "transient"}},
        ],
    ],
)
def test_invalid_lifecycle_and_tool_bearing_native_events_fail_closed(events):
    raw = _jsonl(events)
    with pytest.raises(harness.CodexTraceV2Error, match="lifecycle|item type|success terminal"):
        harness.parse_codex_trace_v2(harness.CodexTraceInputV2(_binding(), raw))
    observation = harness.observe_codex_trace_v2(_binding(), raw)
    assert observation["terminal"] == {"status": "unknown", "failure_class": "unknown"}
    assert all(metric["value"] is None for metric in observation["metrics"].values())


def test_post_launch_invalid_stream_becomes_one_sanitized_unknown_projection():
    hostile = b'{"type":"future","message":"do not retain me"}\n' * 3
    observation = harness.observe_codex_trace_v2(_binding(), hostile)

    assert observation["terminal"] == {"status": "unknown", "failure_class": "unknown"}
    assert observation["diagnostic_code"] == "CODEX_TRACE_V2_INVALID"
    assert all(metric["value"] is None for metric in observation["metrics"].values())
    assert "do not retain me" not in json.dumps(observation)


@pytest.mark.parametrize("field", ["thread_id", "item_text", "failure_message"])
def test_isolated_unicode_surrogate_is_typed_and_sanitized_fail_closed(field):
    events = _success_events()
    if field == "thread_id":
        events[0]["thread_id"] = "\ud800"
    elif field == "item_text":
        events[2]["item"]["text"] = "\ud800"
    else:
        events = [
            {"type": "thread.started", "thread_id": "offline-thread"},
            {"type": "turn.started"},
            {"type": "turn.failed", "error": {"message": "\ud800"}},
        ]
    raw = _jsonl(events)

    with pytest.raises(harness.CodexTraceV2Error, match="differs"):
        harness.parse_codex_trace_v2(harness.CodexTraceInputV2(_binding(), raw))
    observation = harness.observe_codex_trace_v2(_binding(), raw)

    assert observation["terminal"] == {"status": "unknown", "failure_class": "unknown"}
    assert observation["diagnostic_code"] == "CODEX_TRACE_V2_INVALID"
    assert all(metric == {"value": None, "provenance": "unavailable"}
               for metric in observation["metrics"].values())
    encoded = json.dumps(observation)
    assert "\\ud800" not in encoded
    assert observation["raw_retained"] is False


def test_content_free_observation_validation_rejects_cross_field_and_key_injection():
    observation = harness.parse_codex_trace_v2(
        harness.CodexTraceInputV2(_binding(), _jsonl(_success_events())),
    )
    forged = copy.deepcopy(observation)
    forged["terminal"] = {"status": "failed", "failure_class": "host"}
    with pytest.raises(harness.CodexTraceV2Error, match="failed observation"):
        harness.validate_observation(forged)
    forged = copy.deepcopy(observation)
    forged["event_summary"]["message"] = "hostile"
    with pytest.raises(harness.CodexTraceV2Error, match="summary"):
        harness.validate_observation(forged)


@pytest.mark.parametrize(
    ("profile", "model", "effort"),
    [
        ("codex-terra-low", "gpt-5.6-terra", "low"),
        ("codex-terra-medium", "gpt-5.6-terra", "medium"),
        ("codex-terra-high", "gpt-5.6-terra", "high"),
        ("codex-sol-high", "gpt-5.6-sol", "high"),
    ],
)
def test_v2_command_is_pure_bounded_and_binds_every_codex_profile(profile, model, effort):
    command = harness.codex_command_v2(
        _binding(profile),
        packet_id="f61-packet-001",
        task_packet=TASK_PACKET,
        revision=REVISION,
        context_policy="baseline-v1",
    )

    assert command == (
        "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--sandbox", "read-only", "--json", "--model", model,
        "-c", f'model_reasoning_effort="{effort}"', "-",
    )


def test_codex_command_rejects_packet_snapshot_and_policy_mismatch():
    binding = _binding("codex-terra-low")
    cases = [
        (replace(binding, packet_sha256="0" * 64), {}, "fingerprint"),
        (replace(binding, revision="0" * 40), {}, "snapshot"),
        (replace(binding, context_policy="lean-v1"), {}, "context policy"),
        (binding, {"packet_id": "f61-packet-002"}, "packet binding"),
        (binding, {"task_packet": "different"}, "fingerprint"),
        (binding, {"revision": "0" * 40}, "snapshot"),
        (binding, {"context_policy": "lean-v1"}, "context policy"),
    ]
    defaults = {
        "packet_id": "f61-packet-001",
        "task_packet": TASK_PACKET,
        "revision": REVISION,
        "context_policy": "baseline-v1",
    }
    for candidate, overrides, match in cases:
        with pytest.raises(harness.CodexTraceV2Error, match=match):
            harness.codex_command_v2(candidate, **{**defaults, **overrides})
