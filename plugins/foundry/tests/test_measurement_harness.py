"""Offline contracts for FOUNDRY-43's explicit, bounded host runners."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import signal
import subprocess
import threading
import time

import pytest

from foundry import measurement_harness as harness
from foundry import routing_facades
from foundry.routing_facades import (
    codex_spawn_plan,
    run_claude_measurement,
    run_codex_measurement,
)
from foundry.telemetry import TelemetryObserver


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-43"
CLAUDE_PACKET = "confidential prompt: measure only"
CODEX_PACKET = "Goal:\nMeasure.\nInputs:\nFrozen case.\nConstraints:\nOffline.\nDone when:\nTrace."
CLAUDE_COMMAND = ("claude", "--model", "{model}", "--effort", "{effort}")
CODEX_COMMAND = ("codex", "exec", "--json")


def _request(host: str, repetition: int = 1):
    return harness.MeasurementRequest(
        "claude-sonnet-medium" if host == "claude" else "codex-terra-medium",
        "FOUNDRY-31",
        "3fec3f1677ae6bd3ab5ef069afd6ca3073901b46",
        repetition,
    )


def _usage(input_tokens=12):
    return {
        "input_tokens": input_tokens,
        "output_tokens": None,
        "cached_input_tokens": None,
    }


def _codex_usage():
    return {
        "input_tokens": 12,
        "cached_input_tokens": 5,
        "cache_write_input_tokens": 3,
        "output_tokens": 8,
        "reasoning_output_tokens": 2,
    }


def _claude_output(*, override=False):
    return {
        "model": "sonnet",
        "effort": "medium",
        "host_version": "2.1.224",
        "host_override_active": override,
        "usage": _usage(),
        "cache_state": None,
        "review_outcome": None,
    }


class _Pipe:
    """Bounded fake binary pipe with visible read requests for cap assertions."""

    def __init__(self, value: bytes):
        self._value = bytearray(value)
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if self.closed or not self._value:
            return b""
        chunk = bytes(self._value[:size])
        del self._value[:size]
        return chunk

    def close(self) -> None:
        self.closed = True


class _Input:
    def __init__(self):
        self.value = bytearray()
        self.closed = False

    def write(self, value: bytes) -> int:
        self.value.extend(value)
        return len(value)

    def close(self) -> None:
        self.closed = True


class _BlockingInput(_Input):
    """A stdin fake whose write returns only after the harness closes it."""

    def __init__(self, order: list[str] | None = None):
        super().__init__()
        self.write_started = threading.Event()
        self._released = threading.Event()
        self._order = order

    def write(self, value: bytes) -> int:
        if self._order is not None:
            self._order.append("stdin.write")
        self.write_started.set()
        self._released.wait(1)
        if self.closed:
            raise OSError("fake stdin closed")
        self.value.extend(value)
        return len(value)

    def close(self) -> None:
        super().close()
        self._released.set()


class _BrokenPipeInput(_Input):
    """A child-side stdin closure observed by the writer after Popen."""

    def write(self, value: bytes) -> int:
        raise BrokenPipeError("fake child closed stdin")


class _GatePipe(_Pipe):
    """Keep a launched attempt inside collection until a concurrent check is ready."""

    def __init__(self, value: bytes, gate: threading.Event):
        super().__init__(value)
        self._gate = gate

    def read(self, size: int) -> bytes:
        self._gate.wait(1)
        return super().read(size)


class _Process:
    """Offline Popen stub: returncode is the sole process terminal authority."""

    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"", *, waits_forever: bool = False):
        self.stdin = _Input()
        self.stdout = _Pipe(stdout)
        self.stderr = _Pipe(stderr)
        self.returncode = returncode
        self.waits_forever = waits_forever
        self.pid = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.waits_forever else self.returncode

    def wait(self, timeout=None):
        if self.waits_forever:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.waits_forever = False
        self.returncode = -signal.SIGTERM

    def kill(self):
        self.killed = True
        self.waits_forever = False
        self.returncode = -signal.SIGKILL


def _factory(processes, calls):
    """Return Popen-compatible offline factory from a supplied process sequence."""

    values = iter(processes)

    def process_factory(argv, **kwargs):
        process = next(values)
        calls.append((argv, kwargs, process))
        return process

    return process_factory


def _completed(output, calls):
    return _factory([_Process(stdout=json.dumps(output).encode("utf-8"))], calls)


def _codex_events(terminal_event="turn.completed", usage=None, error_notifications=0) -> bytes:
    if terminal_event == "turn.completed":
        terminal = {"type": "turn.completed", "usage": _codex_usage() if usage is None else usage}
    elif terminal_event == "turn.failed":
        terminal = {"type": "turn.failed", "error": {"message": "untrusted turn failure"}}
    else:
        raise AssertionError("native error is a notification, not a terminal")
    events = [
        {"type": "thread.started", "thread_id": "thread-offline-0147"},
        {"type": "turn.started"},
        {"type": "item.started", "item": {"id": "item-1", "type": "agent_message", "text": "untrusted start"}},
        # Codex 0.147.0 lifecycle updates may contain hostile content. It is parsed
        # only for event type and never retained.
        {"type": "item.updated", "item": {"id": "item-1", "type": "agent_message", "text": "untrusted update"}},
        # The parser deliberately does not retain this host text.
        {"type": "item.completed", "item": {"id": "item-1", "type": "agent_message", "text": "untrusted response"}},
    ]
    events.extend({"type": "error", "message": "untrusted native error"} for _ in range(error_notifications))
    events.append(terminal)
    return b"".join(json.dumps(event).encode("utf-8") + b"\n" for event in events)


def _jsonl(events) -> bytes:
    return b"".join(json.dumps(event).encode("utf-8") + b"\n" for event in events)


def _native_prefix():
    return [
        {"type": "thread.started", "thread_id": "thread-0147"},
        {"type": "turn.started"},
    ]


def _native_item(**changes):
    item = {"id": "item-1", "type": "agent_message", "text": "untrusted item"}
    item.update(changes)
    return {"type": "item.updated", "item": item}


def _native_completed(usage=None):
    return {"type": "turn.completed", "usage": _codex_usage() if usage is None else usage}


def test_protocol_is_frozen_jsonl_contract_with_explicit_opt_in():
    value = json.loads((ROOT / "protocol-v1.json").read_text(encoding="utf-8"))
    assert harness.validate_protocol(value)["storage"]["format"] == "append_only_jsonl"
    value["storage"]["format"] = "json"
    with pytest.raises(harness.MeasurementValidationError, match="storage differs"):
        harness.validate_protocol(value)
    value = json.loads((ROOT / "protocol-v1.json").read_text(encoding="utf-8"))
    value["host_result_contract"]["transport"]["stderr"] = "stored"
    with pytest.raises(harness.MeasurementValidationError, match="host result contract differs"):
        harness.validate_protocol(value)
    value = json.loads((ROOT / "protocol-v1.json").read_text(encoding="utf-8"))
    value["host_result_contract"]["post_launch_failure"]["record"] = "none"
    with pytest.raises(harness.MeasurementValidationError, match="host result contract differs"):
        harness.validate_protocol(value)


def test_harness_is_disabled_by_default_and_does_not_enable_telemetry(tmp_path):
    with pytest.raises(harness.MeasurementValidationError, match="explicit opt-in"):
        harness.MeasurementHarness(tmp_path, "claude")
    with pytest.raises(harness.MeasurementValidationError, match="explicit opt-in"):
        harness.MeasurementHarness.from_environ(tmp_path, "claude", {})
    assert TelemetryObserver.from_environ({}).data_dir is None
    assert not list(tmp_path.iterdir())


def test_claude_runner_launches_bounded_command_and_persists_only_host_observation(tmp_path):
    calls = []
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    receipt = run_claude_measurement(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_completed(_claude_output(), calls),
    )
    assert receipt.terminal == {"status": "completed", "failure_class": "none"}
    assert calls[0][0] == ("claude", "--model", "sonnet", "--effort", "medium")
    assert calls[0][1]["start_new_session"] is True
    assert calls[0][1]["cwd"] == str(tmp_path.resolve())
    assert calls[0][2].stdin.value == CLAUDE_PACKET.encode()
    raw = writer.path.read_bytes()
    assert writer.path.name == "claude.trace.jsonl"
    assert raw.count(b"\n") == 1
    assert b"confidential prompt" not in raw
    rows, digest = harness.reproduce_jsonl(raw, host="claude")
    assert receipt.digest == digest
    assert rows[0]["metrics"]["input_tokens"] == {"value": 12, "provenance": "host_reported"}
    assert rows[0]["metrics"]["duration_seconds"]["provenance"] == "client_observed"


def test_claude_rejects_invalid_prelaunch_command_without_a_row(tmp_path):
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    with pytest.raises(harness.MeasurementValidationError, match="does not bind the resolved profile"):
        harness.run_claude(
            writer,
            _request("claude"),
            task_packet=CLAUDE_PACKET,
            root=tmp_path,
            command=("claude",),
            timeout_seconds=1,
            process_factory=_completed(_claude_output(), []),
        )
    assert not writer.path.exists()


@pytest.mark.parametrize(("label", "stdout", "environ"), [
    ("empty", b"", None),
    ("invalid", b'{"response":"untrusted callback"}', None),
    ("mismatch", json.dumps(_claude_output(override=False)).encode("utf-8"), {"CLAUDE_CODE_SUBAGENT_MODEL": "forced"}),
])
def test_claude_invalid_callback_after_launch_persists_one_sanitized_unknown_row(tmp_path, label, stdout, environ):
    writer = harness.MeasurementHarness(tmp_path / label, "claude", enabled=True)
    receipt = harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        environ=environ,
        process_factory=_factory([_Process(stdout=stdout)], []),
    )
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    raw = writer.path.read_bytes()
    assert raw.count(b"\n") == 1
    assert b"untrusted callback" not in raw
    row = writer.read()[0]
    assert row["terminal"] == {"status": "unknown", "failure_class": "unknown"}
    assert row["metrics"]["input_tokens"] == {"value": None, "provenance": "unavailable"}


def test_claude_rejects_a_project_route_that_diverges_from_its_frozen_profile(tmp_path):
    root = tmp_path / "route-mismatch"
    (root / ".foundry").mkdir(parents=True)
    (root / ".foundry" / "model-routing.json").write_text(json.dumps({
        "mappings": {"claude": {"balanced": {"model": "opus"}}},
    }), encoding="utf-8")
    calls = []
    with pytest.raises(harness.MeasurementValidationError, match="resolved route differs"):
        harness.run_claude(
            harness.MeasurementHarness(tmp_path / "trace", "claude", enabled=True),
            _request("claude"),
            task_packet=CLAUDE_PACKET,
            root=root,
            command=CLAUDE_COMMAND,
            timeout_seconds=1,
            process_factory=_completed(_claude_output(), calls),
        )
    assert not calls


def test_process_terminal_classifications_are_derived_from_real_process_results(tmp_path):
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    host_process = _Process(returncode=23, stdout=b"untrusted", stderr=b"untrusted")
    host_failure = harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([host_process], []),
    )
    assert host_failure.terminal == {"status": "failed", "failure_class": "host"}
    timeout_process = _Process(stdout=b"", stderr=b"", waits_forever=True)
    timeout = harness.run_claude(
        harness.MeasurementHarness(tmp_path / "timeout", "claude", enabled=True),
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=0.01,
        process_factory=_factory([timeout_process], []),
    )
    assert timeout.terminal == {"status": "failed", "failure_class": "timeout"}
    assert timeout_process.terminated and timeout_process.stdout.closed and timeout_process.stderr.closed

    cancelled = harness.run_claude(
        harness.MeasurementHarness(tmp_path / "cancelled", "claude", enabled=True),
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(returncode=-signal.SIGTERM)], []),
    )
    assert cancelled.terminal == {"status": "cancelled", "failure_class": "cancelled"}


@pytest.mark.parametrize(("returncode", "terminal"), [
    (23, {"status": "failed", "failure_class": "host"}),
    (-signal.SIGTERM, {"status": "cancelled", "failure_class": "cancelled"}),
])
def test_broken_stdin_preserves_observable_process_terminal(tmp_path, returncode, terminal):
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    process = _Process(returncode=returncode, stdout=b"untrusted process output")
    process.stdin = _BrokenPipeInput()
    receipt = harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([process], []),
    )
    assert receipt.terminal == terminal
    raw = writer.path.read_bytes()
    assert raw.count(b"\n") == 1 and b"untrusted process output" not in raw
    assert writer.read()[0]["terminal"] == terminal


@pytest.mark.parametrize(("second_repetition", "maximum_records", "error"), [
    (1, harness.MAX_RECORDS, "duplicate rows"),
    (2, 1, "trace cap"),
])
def test_reservation_contention_denies_second_attempt_before_popen(
    tmp_path,
    monkeypatch,
    second_repetition,
    maximum_records,
    error,
):
    monkeypatch.setattr(harness, "MAX_RECORDS", maximum_records)
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    gate = threading.Event()
    first_spawned = threading.Event()
    first_process = _Process(stdout=json.dumps(_claude_output()).encode("utf-8"))
    first_process.stdout = _GatePipe(first_process.stdout._value, gate)
    first_result: dict[str, object] = {}
    second_result: dict[str, object] = {}
    second_calls = []

    def first_factory(argv, **kwargs):
        first_spawned.set()
        return first_process

    def second_factory(argv, **kwargs):
        second_calls.append((argv, kwargs))
        return _Process(stdout=json.dumps(_claude_output()).encode("utf-8"))

    def first_attempt():
        try:
            first_result["receipt"] = harness.run_claude(
                writer,
                _request("claude", 1),
                task_packet=CLAUDE_PACKET,
                root=tmp_path,
                command=CLAUDE_COMMAND,
                timeout_seconds=1,
                process_factory=first_factory,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            first_result["error"] = exc

    def second_attempt():
        try:
            harness.run_claude(
                writer,
                _request("claude", second_repetition),
                task_packet=CLAUDE_PACKET,
                root=tmp_path,
                command=CLAUDE_COMMAND,
                timeout_seconds=1,
                process_factory=second_factory,
            )
        except Exception as exc:
            second_result["error"] = exc

    first_thread = threading.Thread(target=first_attempt)
    first_thread.start()
    assert first_spawned.wait(1)
    second_thread = threading.Thread(target=second_attempt)
    second_thread.start()
    time.sleep(0.02)
    assert second_thread.is_alive() and not second_calls
    gate.set()
    first_thread.join(1)
    second_thread.join(1)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert "error" not in first_result
    assert first_result["receipt"].terminal == {"status": "completed", "failure_class": "none"}
    assert isinstance(second_result.get("error"), harness.MeasurementValidationError)
    assert error in str(second_result["error"])
    assert not second_calls and writer.path.read_bytes().count(b"\n") == 1


def test_pipe_collection_overflow_after_launch_persists_one_sanitized_unknown_row(tmp_path):
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    stdout_marker = b"private-overflow-stdout"
    stderr_marker = b"private-overflow-stderr"
    process = _Process(
        stdout=stdout_marker * (harness.MAX_RESULT_BYTES // len(stdout_marker) + 1),
        stderr=stderr_marker * (harness.MAX_STDERR_BYTES // len(stderr_marker) + 1),
        waits_forever=True,
    )
    receipt = harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([process], []),
    )
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    assert max(process.stdout.read_sizes) <= harness.MAX_RESULT_BYTES + 1
    assert max(process.stderr.read_sizes) <= harness.MAX_STDERR_BYTES + 1
    assert process.terminated and process.stdin.closed and process.stdout.closed and process.stderr.closed
    raw = writer.path.read_bytes()
    assert raw.count(b"\n") == 1
    assert stdout_marker not in raw and stderr_marker not in raw
    row = writer.read()[0]
    assert row["terminal"] == {"status": "unknown", "failure_class": "unknown"}
    assert row["metrics"]["input_tokens"] == {"value": None, "provenance": "unavailable"}


def test_stdin_is_isolated_after_reader_start_and_cannot_bypass_the_deadline(tmp_path, monkeypatch):
    """A child that never reads stdin cannot prevent bounded collection or timeout."""
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    order: list[str] = []
    process = _Process(stdout=b"", stderr=b"", waits_forever=True)
    process.stdin = _BlockingInput(order)
    original_start = harness._BoundedPipeReader.start

    def ordered_start(reader):
        order.append("reader.start")
        return original_start(reader)

    monkeypatch.setattr(harness._BoundedPipeReader, "start", ordered_start)
    started = time.monotonic()
    receipt = harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=0.02,
        process_factory=_factory([process], []),
    )
    assert time.monotonic() - started < 0.5
    assert receipt.terminal == {"status": "failed", "failure_class": "timeout"}
    assert order[:3] == ["reader.start", "reader.start", "stdin.write"]
    assert process.stdin.write_started.is_set()
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed


def test_claude_measurement_rejects_route_envelopes_without_accessing_escalation_store(tmp_path, monkeypatch):
    """Measurement packets never invoke the facade's issue/escalation parser."""
    accesses = []

    def forbidden_store(*args, **kwargs):
        accesses.append((args, kwargs))
        raise AssertionError("measurement must not access EscalationStore")

    monkeypatch.setattr(routing_facades.EscalationStore, "for_root", forbidden_store)
    with pytest.raises(harness.MeasurementValidationError, match="route request envelope"):
        harness.run_claude(
            harness.MeasurementHarness(tmp_path, "claude", enabled=True),
            _request("claude"),
            task_packet='FOUNDRY_ROUTE_REQUEST={"issue":"FOUNDRY-31"}\n' + CLAUDE_PACKET,
            root=tmp_path,
            command=CLAUDE_COMMAND,
            timeout_seconds=1,
            process_factory=_completed(_claude_output(), []),
        )
    assert not accesses


@pytest.mark.parametrize(("event", "terminal"), [
    ("turn.completed", ("completed", "none")),
    ("turn.failed", ("failed", "host")),
])
def test_codex_runner_binds_native_jsonl_terminal_events_to_its_spawn_descriptor(tmp_path, event, terminal):
    calls = []
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    resolved_root = tmp_path / "resolved-root"
    resolved_root.mkdir()
    alias = tmp_path / "root-alias"
    alias.symlink_to(resolved_root, target_is_directory=True)
    receipt = run_codex_measurement(
        writer,
        _request("codex"),
        task_packet=CODEX_PACKET,
        root=alias,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        environ={"FOUNDRY_MEASUREMENT_DESCRIPTOR_SHA256": "forged", "SAFE": "1"},
        process_factory=_factory([_Process(stdout=_codex_events(event, error_notifications=1 if event == "turn.failed" else 0))], calls),
    )
    assert receipt.terminal == {"status": terminal[0], "failure_class": terminal[1]}
    row = writer.read()[0]
    assert calls[0][0] == (
        "codex", "exec", "--ephemeral", "--json", "--model", "gpt-5.6-terra",
        "-c", 'model_reasoning_effort="medium"', "-",
    )
    assert calls[0][1]["cwd"] == str(resolved_root.resolve())
    assert "--effort" not in calls[0][0] and "--descriptor" not in calls[0][0] and "-C" not in calls[0][0]
    assert "FOUNDRY_MEASUREMENT_DESCRIPTOR_SHA256" not in calls[0][1]["env"]
    assert calls[0][1]["env"]["SAFE"] == "1"
    assert harness.SHA256.fullmatch(row["binding"]["descriptor_sha256"])
    assert row["binding"]["event_summary"]["terminal_event"] == event
    if event == "turn.completed":
        assert row["metrics"]["input_tokens"] == {"value": 12, "provenance": "host_reported"}
        assert row["metrics"]["cached_input_tokens"] == {"value": 5, "provenance": "host_reported"}
        assert row["metrics"]["cache_write_input_tokens"] == {"value": 3, "provenance": "host_reported"}
        assert row["metrics"]["output_tokens"] == {"value": 8, "provenance": "host_reported"}
        assert row["metrics"]["reasoning_output_tokens"] == {"value": 2, "provenance": "host_reported"}
    else:
        assert row["metrics"]["input_tokens"] == {"value": None, "provenance": "unavailable"}
    assert b"untrusted update" not in writer.path.read_bytes()
    assert b"untrusted start" not in writer.path.read_bytes()
    assert b"untrusted response" not in writer.path.read_bytes()
    assert b"untrusted native error" not in writer.path.read_bytes()
    assert b"untrusted turn failure" not in writer.path.read_bytes()


def test_codex_optional_native_usage_is_unavailable_without_an_invented_zero(tmp_path):
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    usage_without_cache_write = {
        "input_tokens": 12,
        "cached_input_tokens": 5,
        "output_tokens": 8,
        "reasoning_output_tokens": 2,
    }
    receipt = harness.run_codex(
        writer,
        _request("codex"),
        task_packet=CODEX_PACKET,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(stdout=_codex_events(usage=usage_without_cache_write))], []),
    )
    assert receipt.terminal == {"status": "completed", "failure_class": "none"}
    assert writer.read()[0]["metrics"]["cache_write_input_tokens"] == {
        "value": None, "provenance": "unavailable",
    }


def test_codex_0147_rejects_the_fictitious_turn_cancelled_event():
    with pytest.raises(harness.MeasurementValidationError, match="event stream is malformed"):
        harness._codex_jsonl_result(b'{"type":"turn.cancelled"}\n')


def test_codex_0147_fails_closed_on_required_native_structure_and_order():
    complete = _native_completed()
    cases = {
        "missing_thread_id": [{"type": "thread.started"}, {"type": "turn.started"}, complete],
        "thread_not_first": [{"type": "turn.started"}, {"type": "thread.started", "thread_id": "thread-0147"}, complete],
        "turn_not_second": [{"type": "thread.started", "thread_id": "thread-0147"}, _native_item(), complete],
        "item_missing": _native_prefix() + [{"type": "item.updated"}, complete],
        "item_id_missing": _native_prefix() + [_native_item(id=None), complete],
        "item_type_missing": _native_prefix() + [_native_item(type=None), complete],
        "item_text_missing": _native_prefix() + [_native_item(text=None), complete],
        "completed_usage_missing": _native_prefix() + [{"type": "turn.completed"}],
        "usage_before_terminal": [
            {"type": "thread.started", "thread_id": "thread-0147"},
            {"type": "turn.started", "usage": _codex_usage()},
            complete,
        ],
        "failed_error_missing": _native_prefix() + [{"type": "turn.failed"}],
        "failed_message_missing": _native_prefix() + [{"type": "turn.failed", "error": {}}],
        "notification_message_missing": _native_prefix() + [{"type": "error"}, {"type": "turn.failed", "error": {"message": "x"}}],
        "notification_without_terminal": _native_prefix() + [{"type": "error", "message": "x"}],
        "notification_then_success": _native_prefix() + [{"type": "error", "message": "x"}, complete],
        "multiple_terminals": _native_prefix() + [complete, {"type": "turn.failed", "error": {"message": "x"}}],
    }
    for label, events in cases.items():
        with pytest.raises(harness.MeasurementValidationError, match="Codex JSONL") as exc_info:
            harness._codex_jsonl_result(_jsonl(events))
        assert label
        assert "completed" not in str(exc_info.value)


def test_codex_error_notifications_before_turn_failed_are_one_host_failure_without_text(tmp_path):
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    receipt = harness.run_codex(
        writer,
        _request("codex"),
        task_packet=CODEX_PACKET,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(stdout=_codex_events("turn.failed", error_notifications=2))], []),
    )
    assert receipt.terminal == {"status": "failed", "failure_class": "host"}
    row = writer.read()[0]
    assert row["binding"]["event_summary"]["terminal_event"] == "turn.failed"
    assert b"untrusted native error" not in writer.path.read_bytes()
    assert b"untrusted turn failure" not in writer.path.read_bytes()


def test_codex_error_notification_without_terminal_persists_unknown_not_success(tmp_path):
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    stream = _jsonl(_native_prefix() + [{"type": "error", "message": "untrusted native error"}])
    receipt = harness.run_codex(
        writer,
        _request("codex"),
        task_packet=CODEX_PACKET,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(stdout=stream)], []),
    )
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    row = writer.read()[0]
    assert row["metrics"]["input_tokens"] == {"value": None, "provenance": "unavailable"}
    assert b"untrusted native error" not in writer.path.read_bytes()


def test_codex_keeps_a_forged_envelope_unknown_and_rejects_a_forged_plan(tmp_path, monkeypatch):
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    # A legacy generic envelope is not a native Codex JSONL event. It cannot forge a
    # descriptor binding or a success, but its launched attempt remains observable.
    legacy_envelope = {"descriptor_sha256": "0" * 64, "status": "completed"}
    receipt = harness.run_codex(
        writer,
        _request("codex"),
        task_packet=CODEX_PACKET,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(stdout=json.dumps(legacy_envelope).encode("utf-8"))], []),
    )
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    assert writer.path.read_bytes().count(b"\n") == 1
    genuine = codex_spawn_plan("implementer", CODEX_PACKET, root=tmp_path, _observe=False)
    forged_plan = copy.deepcopy(genuine)
    forged_plan["spawn"]["model"] = "gpt-5.6-sol"
    monkeypatch.setattr(harness, "codex_spawn_plan", lambda *args, **kwargs: forged_plan)
    with pytest.raises(harness.MeasurementValidationError, match="spawn plan differs"):
        harness.run_codex(
            writer,
            _request("codex"),
            task_packet=CODEX_PACKET,
            root=tmp_path,
            command=CODEX_COMMAND,
            timeout_seconds=1,
            process_factory=_factory([_Process(stdout=_codex_events())], []),
        )
    assert writer.read()[0]["terminal"] == {"status": "unknown", "failure_class": "unknown"}


@pytest.mark.parametrize("command", [
    ("codex", "exec", "{model}", "{effort}", "{descriptor_sha256}"),
    ("codex", "exec", "--model", "{model}", "--json", "{effort}", "{descriptor_sha256}"),
    ("codex", "exec", "--json", "--model", "{model}", "--effort", "{effort}"),
])
def test_codex_requires_the_native_jsonl_invocation_before_spawning(tmp_path, command):
    calls = []
    with pytest.raises(harness.MeasurementValidationError, match="native JSONL"):
        harness.run_codex(
            harness.MeasurementHarness(tmp_path, "codex", enabled=True),
            _request("codex"),
            task_packet=CODEX_PACKET,
            root=tmp_path,
            command=command,
            timeout_seconds=1,
            process_factory=_factory([_Process(stdout=_codex_events())], calls),
        )
    assert not calls


@pytest.mark.parametrize("stream", [
    b"",
    b'{"type":"turn.completed"}\n{"type":"turn.failed"}\n',
    b'{"type":"turn.started"}\n',
    b'{"type":"unknown.extension"}\n',
    b'{"type":"turn.completed"}',
])
def test_codex_keeps_absent_malformed_or_ambiguous_streams_unknown(tmp_path, stream):
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    receipt = harness.run_codex(
        writer,
        _request("codex"),
        task_packet=CODEX_PACKET,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(stdout=stream)], []),
    )
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    raw = writer.path.read_bytes()
    assert raw.count(b"\n") == 1
    assert b"turn." not in raw and b"unknown.extension" not in raw
    row = writer.read()[0]
    assert row["binding"]["event_summary"] == {
        "terminal_event": "stream.unknown", "event_count": 0, "usage_event_count": 0,
    }
    assert all(
        row["metrics"][name] == {"value": None, "provenance": "unavailable"}
        for name in harness.METRICS
        if name != "duration_seconds"
    )
    assert row["metrics"]["duration_seconds"]["provenance"] == "client_observed"


@pytest.mark.parametrize(("process", "terminal", "summary"), [
    (_Process(returncode=23), {"status": "failed", "failure_class": "host"}, "process.exit"),
    (_Process(waits_forever=True), {"status": "failed", "failure_class": "timeout"}, "process.timeout"),
    (_Process(returncode=-signal.SIGTERM), {"status": "cancelled", "failure_class": "cancelled"}, "process.exit"),
])
def test_codex_process_lifecycle_is_derived_without_a_stream(tmp_path, process, terminal, summary):
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    receipt = harness.run_codex(
        writer,
        _request("codex"),
        task_packet=CODEX_PACKET,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=0.01 if process.waits_forever else 1,
        process_factory=_factory([process], []),
    )
    assert receipt.terminal == terminal
    row = writer.read()[0]
    assert row["binding"]["event_summary"]["terminal_event"] == summary
    assert row["metrics"]["input_tokens"] == {"value": None, "provenance": "unavailable"}
    if summary == "process.timeout":
        assert process.terminated and process.stdout.closed and process.stderr.closed


def test_reader_cap_is_revalidated_after_join_before_success(tmp_path, monkeypatch):
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    process = _Process(stdout=json.dumps(_claude_output()).encode("utf-8"))
    original_join = harness._BoundedPipeReader.join
    flipped = False

    def racing_join(reader, timeout):
        nonlocal flipped
        completed = original_join(reader, timeout)
        if completed and reader._maximum == harness.MAX_RESULT_BYTES and not flipped:
            reader.overflowed = True
            flipped = True
        return completed

    monkeypatch.setattr(harness._BoundedPipeReader, "join", racing_join)
    receipt = harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([process], []),
    )
    assert flipped and process.stdout.closed and process.stderr.closed
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    assert writer.path.read_bytes().count(b"\n") == 1


def test_codex_descriptor_is_verifiable_without_persisting_the_packet(tmp_path):
    plan = codex_spawn_plan("implementer", CODEX_PACKET, root=tmp_path, _observe=False)
    identity = harness.codex_descriptor_identity(plan, profile_id="codex-terra-medium")
    assert harness.verify_codex_descriptor(plan, identity, profile_id="codex-terra-medium")
    forged = copy.deepcopy(plan)
    forged["spawn"]["message"] = "Goal:\nDifferent.\nInputs:\nx\nConstraints:\ny\nDone when:\nz"
    assert not harness.verify_codex_descriptor(forged, identity, profile_id="codex-terra-medium")


def test_codex_descriptor_digest_binds_the_exact_stdin_bytes(tmp_path):
    """Whitespace normalized by codex_spawn_plan must still change the trace binding."""
    writer = harness.MeasurementHarness(tmp_path, "codex", enabled=True)
    calls = []
    padded_packet = f"  {CODEX_PACKET}  "
    harness.run_codex(
        writer,
        _request("codex", 1),
        task_packet=CODEX_PACKET,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(stdout=_codex_events())], calls),
    )
    harness.run_codex(
        writer,
        _request("codex", 2),
        task_packet=padded_packet,
        root=tmp_path,
        command=CODEX_COMMAND,
        timeout_seconds=1,
        process_factory=_factory([_Process(stdout=_codex_events())], calls),
    )
    rows = writer.read()
    assert calls[1][2].stdin.value == padded_packet.encode("utf-8")
    assert rows[0]["binding"]["descriptor_sha256"] != rows[1]["binding"]["descriptor_sha256"]
    assert padded_packet.encode("utf-8") not in writer.path.read_bytes()
    plan = codex_spawn_plan("implementer", padded_packet, root=tmp_path, _observe=False)
    identity = harness.codex_descriptor_identity(
        plan,
        profile_id="codex-terra-medium",
        stdin_packet=padded_packet,
    )
    assert harness.verify_codex_descriptor(
        plan,
        identity,
        profile_id="codex-terra-medium",
        stdin_packet=padded_packet,
    )
    assert not harness.verify_codex_descriptor(
        plan,
        identity,
        profile_id="codex-terra-medium",
        stdin_packet=CODEX_PACKET,
    )


def test_jsonl_is_append_only_canonical_and_capped(tmp_path, monkeypatch):
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    calls = []
    common = {
        "task_packet": CLAUDE_PACKET,
        "root": tmp_path,
        "command": CLAUDE_COMMAND,
        "timeout_seconds": 1,
        "process_factory": _factory(
            [_Process(stdout=json.dumps(_claude_output()).encode("utf-8")) for _ in range(5)],
            calls,
        ),
    }
    harness.run_claude(writer, _request("claude", 1), **common)
    first = writer.path.read_bytes()
    harness.run_claude(writer, _request("claude", 2), **common)
    second = writer.path.read_bytes()
    assert second.startswith(first) and second.count(b"\n") == 2
    before_duplicate = len(calls)
    with pytest.raises(harness.MeasurementValidationError, match="duplicate rows"):
        harness.run_claude(writer, _request("claude", 2), **common)
    assert len(calls) == before_duplicate and writer.path.read_bytes() == second

    capped = harness.MeasurementHarness(tmp_path / "cap", "claude", enabled=True)
    monkeypatch.setattr(harness, "MAX_RECORDS", 1)
    harness.run_claude(capped, _request("claude", 1), **common)
    before_record_cap = len(calls)
    with pytest.raises(harness.MeasurementValidationError, match="trace cap"):
        harness.run_claude(capped, _request("claude", 2), **common)
    assert len(calls) == before_record_cap and len(capped.read()) == 1

    byte_capped = harness.MeasurementHarness(tmp_path / "byte-cap", "claude", enabled=True)
    monkeypatch.setattr(harness, "MAX_RECORDS", 100)
    harness.run_claude(byte_capped, _request("claude", 1), **common)
    monkeypatch.setattr(harness, "MAX_TRACE_BYTES", len(byte_capped.path.read_bytes()))
    before_byte_cap = len(calls)
    with pytest.raises(harness.MeasurementValidationError, match="trace cap"):
        harness.run_claude(byte_capped, _request("claude", 2), **common)
    assert len(calls) == before_byte_cap and len(byte_capped.read()) == 1


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_host_json_value_error_after_launch_persists_one_sanitized_unknown_row(tmp_path, host):
    hostile_integer = b"9" * 5_000
    writer = harness.MeasurementHarness(tmp_path / host, host, enabled=True)
    if host == "claude":
        receipt = harness.run_claude(
            writer,
            _request(host),
            task_packet=CLAUDE_PACKET,
            root=tmp_path,
            command=CLAUDE_COMMAND,
            timeout_seconds=1,
            process_factory=_factory([_Process(stdout=b'{"model":' + hostile_integer + b"}")], []),
        )
    else:
        receipt = harness.run_codex(
            writer,
            _request(host),
            task_packet=CODEX_PACKET,
            root=tmp_path,
            command=CODEX_COMMAND,
            timeout_seconds=1,
            process_factory=_factory([_Process(stdout=b'{"type":' + hostile_integer + b"}\n")], []),
        )
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    raw = writer.path.read_bytes()
    assert raw.count(b"\n") == 1 and hostile_integer[:100] not in raw
    row = writer.read()[0]
    assert row["terminal"] == {"status": "unknown", "failure_class": "unknown"}
    assert row["metrics"]["input_tokens"] == {"value": None, "provenance": "unavailable"}


def test_freeze_fails_before_launch_and_unsafe_callback_is_sanitized_after_launch(tmp_path):
    calls = []
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    invalid = harness.MeasurementRequest("claude-sonnet-medium", "FOUNDRY-31", "0" * 40, 1)
    with pytest.raises(harness.MeasurementValidationError, match="frozen F41 corpus"):
        harness.run_claude(
            writer,
            invalid,
            task_packet=CLAUDE_PACKET,
            root=tmp_path,
            command=CLAUDE_COMMAND,
            timeout_seconds=1,
            process_factory=_completed(_claude_output(), calls),
        )
    assert not calls and not writer.path.exists()
    unsafe = _claude_output()
    unsafe["prompt"] = "secret"
    receipt = harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_completed(unsafe, calls),
    )
    assert len(calls) == 1
    assert receipt.terminal == {"status": "unknown", "failure_class": "unknown"}
    assert b"secret" not in writer.path.read_bytes()


def test_offline_reproduction_rejects_noncanonical_or_sensitive_rows(tmp_path):
    writer = harness.MeasurementHarness(tmp_path, "claude", enabled=True)
    harness.run_claude(
        writer,
        _request("claude"),
        task_packet=CLAUDE_PACKET,
        root=tmp_path,
        command=CLAUDE_COMMAND,
        timeout_seconds=1,
        process_factory=_completed(_claude_output(), []),
    )
    raw = writer.path.read_bytes()
    assert harness.reproduce_jsonl(raw) == harness.reproduce_jsonl(raw)
    row = json.loads(raw)
    row["prompt"] = "secret"
    forged = json.dumps(row).encode() + b"\n"
    with pytest.raises(harness.MeasurementValidationError, match="unknown or missing fields"):
        harness.reproduce_jsonl(forged)


# FOUNDRY-55 consumes only the privacy-safe, already validated F43 projection. These
# fixtures deliberately do not start a host: baseline reporting is offline-only.
def _f43_measurement_for_baseline(
    host="claude",
    *,
    case_id="FOUNDRY-31",
    revision="3fec3f1677ae6bd3ab5ef069afd6ca3073901b46",
    input_tokens=12,
    output_tokens=None,
    cached_input_tokens=None,
    cache_write_input_tokens=None,
):
    request = harness.MeasurementRequest(
        "claude-sonnet-medium" if host == "claude" else "codex-terra-medium",
        case_id,
        revision,
        1,
    )
    model, effort, version, _role = harness._profile(host, request.profile_id)
    binding = {
        "source": f"{host}_resolved",
        "model": model,
        "effort": effort,
        "host_version": version,
        "host_override_active": False,
        "result_sha256": "a" * 64,
    }
    if host == "claude":
        binding["route_sha256"] = "b" * 64
    else:
        binding["descriptor_sha256"] = "b" * 64
        binding["event_summary"] = {
            "terminal_event": "turn.completed",
            "event_count": 3,
            "usage_event_count": 1,
        }
    observed = {"review_outcome": "approved"}
    for name, tokens in {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
    }.items():
        if tokens is not None:
            observed[name] = tokens
    return harness.validate_record(harness._base_record(
        host=host,
        request=request,
        binding=binding,
        terminal={"status": "completed", "failure_class": "none"},
        host_reported=observed,
        duration_seconds=1.5,
    ))


@pytest.mark.parametrize(("host", "provider", "model"), [
    ("claude", "anthropic", "sonnet"),
    ("codex", "openai", "gpt-5.6-terra"),
])
def test_context_baseline_derives_only_available_f43_observations(host, provider, model):
    measurement = _f43_measurement_for_baseline(host)
    baseline = harness.context_baseline_from_measurement(measurement)

    execution = baseline["execution"]
    assert execution["host"] == host
    assert execution["provider"] == provider
    assert execution["role"] == "implementer"
    assert execution["model"] == model
    assert baseline["f43_measurement"] == measurement
    assert baseline["metrics"]["input_tokens"] == {
        "state": "observed",
        "value": 12,
        "source": "host_reported",
        "evidence_sha256": baseline["metrics"]["input_tokens"]["evidence_sha256"],
    }
    assert baseline["metrics"]["duration_seconds"]["state"] == "observed"
    assert baseline["metrics"]["models"]["value"] == [model]
    assert baseline["metrics"]["tool_result_tokens"] == {
        "state": "unavailable", "value": None,
    }
    assert baseline["metrics"]["provider_cost_usd"] == {
        "state": "unavailable", "value": None,
    }
    assert harness.verify_context_baseline_binding(baseline, measurement)


def test_context_baseline_rejects_synthetic_or_unbound_observed_values():
    measurement = _f43_measurement_for_baseline()
    baseline = harness.context_baseline_from_measurement(measurement)
    forged = copy.deepcopy(baseline)
    forged["metrics"]["tool_calls"] = {
        "state": "observed",
        "value": 1,
        "source": "synthetic",
        "evidence_sha256": "0" * 64,
    }
    with pytest.raises(harness.MeasurementValidationError, match="source differs"):
        harness.validate_context_baseline_record(forged)

    # An allowlisted source and a self-consistent digest are still not evidence: v1
    # derives every public F55 metric from the embedded, validated F43 row.
    forged = copy.deepcopy(baseline)
    forged["metrics"]["provider_cost_usd"] = _baseline_observed(4.0, "provider_reported")
    forged["metrics"]["provider_cost_usd"]["evidence_sha256"] = harness._context_baseline_evidence(
        measurement,
        source="provider_reported",
        name="provider_cost_usd",
        value=4.0,
    )
    forged["metrics"]["outcome"] = _baseline_observed("success", "client_observed")
    forged["metrics"]["outcome"]["evidence_sha256"] = harness._context_baseline_evidence(
        measurement,
        source="client_observed",
        name="outcome",
        value="success",
    )
    with pytest.raises(harness.MeasurementValidationError, match="derivation differs"):
        harness.context_baseline_report([forged])

    forged = copy.deepcopy(baseline)
    forged["execution"]["model"] = "opus"
    with pytest.raises(harness.MeasurementValidationError, match="execution differs"):
        harness.validate_context_baseline_record(forged)

    forged = copy.deepcopy(baseline)
    forged["metrics"]["input_tokens"]["evidence_sha256"] = "0" * 64
    with pytest.raises(harness.MeasurementValidationError, match="derivation differs"):
        harness.validate_context_baseline_record(forged)

    forged = copy.deepcopy(baseline)
    forged["prompt"] = "prohibited"
    with pytest.raises(harness.MeasurementValidationError, match="schema differs"):
        harness.validate_context_baseline_record(forged)

    missing = harness.context_baseline_from_measurement(
        _f43_measurement_for_baseline(input_tokens=None),
    )
    assert missing["metrics"]["input_tokens"] == {
        "state": "unavailable", "value": None,
    }


def test_context_baseline_retains_each_f43_token_observation_separately():
    measurement = _f43_measurement_for_baseline(
        "codex",
        input_tokens=12,
        output_tokens=8,
        cached_input_tokens=5,
        cache_write_input_tokens=3,
    )
    baseline = harness.context_baseline_from_measurement(measurement)
    for name, value in {
        "input_tokens": 12,
        "output_tokens": 8,
        "cached_input_tokens": 5,
        "cache_write_input_tokens": 3,
    }.items():
        assert baseline["metrics"][name]["state"] == "observed"
        assert baseline["metrics"][name]["value"] == value

    missing = harness.context_baseline_from_measurement(_f43_measurement_for_baseline())
    for name in ("output_tokens", "cached_input_tokens", "cache_write_input_tokens"):
        assert missing["metrics"][name] == {"state": "unavailable", "value": None}

    forged = copy.deepcopy(missing)
    forged["metrics"]["output_tokens"] = _baseline_observed(8, "host_reported")
    forged["metrics"]["output_tokens"]["evidence_sha256"] = harness._context_baseline_evidence(
        missing["f43_measurement"],
        source="host_reported",
        name="output_tokens",
        value=8,
    )
    with pytest.raises(harness.MeasurementValidationError, match="derivation differs"):
        harness.validate_context_baseline_record(forged)


def test_context_baseline_report_rejects_two_results_for_one_f41_execution_slot():
    first = harness.context_baseline_from_measurement(
        _f43_measurement_for_baseline(input_tokens=12),
    )
    second = harness.context_baseline_from_measurement(
        _f43_measurement_for_baseline(input_tokens=13),
    )
    assert first["execution"]["execution_id"] != second["execution"]["execution_id"]
    with pytest.raises(harness.MeasurementValidationError, match="duplicate F41 slots"):
        harness.context_baseline_report([first, second])


def _baseline_observed(value, source):
    return {
        "state": "observed",
        "value": value,
        "source": source,
        "evidence_sha256": "c" * 64,
    }


def test_context_baseline_preserves_null_and_unavailable_and_costs_need_available_inputs():
    first = harness.context_baseline_from_measurement(_f43_measurement_for_baseline())
    second = harness.context_baseline_from_measurement(_f43_measurement_for_baseline(
        case_id="FOUNDRY-33",
        revision="c60a67219ea328badd6452ed881ad6a80bf23d98",
    ))
    report = harness.context_baseline_report([first, second])
    group = report["groups"][0]
    assert group["cost_per_success_usd"] == {"state": "unavailable", "value": None}
    assert group["cost_per_merge_usd"] == {"state": "unavailable", "value": None}
    assert group["metrics"]["tool_calls"] == {
        "observed": 0, "null": 0, "unavailable": 2,
    }
    assert report["context_broker_gain"] == {
        "state": "prohibited_before_baseline", "value": None,
    }

    assert harness._validate_context_baseline_metric("input_tokens", {
        "state": "null",
        "value": None,
        "source": "host_reported",
        "evidence_sha256": "d" * 64,
    }, model="sonnet") == {
        "state": "null",
        "value": None,
        "source": "host_reported",
        "evidence_sha256": "d" * 64,
    }
    controlled = [
        {"metrics": {
            "provider_cost_usd": _baseline_observed(4.0, "provider_reported"),
            "outcome": _baseline_observed("success", "client_observed"),
            "merge_outcome": _baseline_observed("merged", "client_observed"),
        }},
        {"metrics": {
            "provider_cost_usd": _baseline_observed(2.0, "provider_reported"),
            "outcome": _baseline_observed("success", "client_observed"),
            "merge_outcome": _baseline_observed("merged", "client_observed"),
        }},
    ]
    assert harness._context_baseline_cost(
        controlled,
        outcome_name="outcome",
        selected_value="success",
    ) == {"state": "observed", "value": 3.0}
    assert harness._context_baseline_cost(
        controlled,
        outcome_name="merge_outcome",
        selected_value="merged",
    ) == {"state": "observed", "value": 3.0}
    success_and_failure = [
        {"metrics": {
            "provider_cost_usd": _baseline_observed(4.0, "provider_reported"),
            "outcome": _baseline_observed("success", "client_observed"),
            "merge_outcome": _baseline_observed("merged", "client_observed"),
        }},
        {"metrics": {
            "provider_cost_usd": _baseline_observed(2.0, "provider_reported"),
            "outcome": _baseline_observed("failure", "client_observed"),
            "merge_outcome": _baseline_observed("not_merged", "client_observed"),
        }},
    ]
    assert harness._context_baseline_cost(
        success_and_failure,
        outcome_name="outcome",
        selected_value="success",
    ) == {"state": "observed", "value": 6.0}
    unknown_outcome = copy.deepcopy(success_and_failure)
    unknown_outcome[1]["metrics"]["outcome"] = _baseline_observed(
        "unknown", "client_observed",
    )
    assert harness._context_baseline_cost(
        unknown_outcome,
        outcome_name="outcome",
        selected_value="success",
    ) == {"state": "unavailable", "value": None}


def test_context_baseline_protocol_and_report_fail_closed_before_broker_comparison():
    root = Path(__file__).parents[1] / "benchmarks" / "foundry-55"
    protocol = json.loads((root / "protocol-v1.json").read_text(encoding="utf-8"))
    assert harness.validate_context_baseline_protocol(protocol)["status"] == "frozen_pre_run"
    protocol["context_broker_gain"]["value"] = 0
    with pytest.raises(harness.MeasurementValidationError, match="broker gain differs"):
        harness.validate_context_baseline_protocol(protocol)

    report = harness.context_baseline_report([
        harness.context_baseline_from_measurement(_f43_measurement_for_baseline()),
    ])
    report["groups"][0]["metrics"]["input_tokens"]["observed"] = 99
    with pytest.raises(harness.MeasurementValidationError, match="metric summary differs"):
        harness.validate_context_baseline_report(report)
