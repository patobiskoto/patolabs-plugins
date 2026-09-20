"""Explicit, bounded FOUNDRY-43 measurement runners.

This module is not connected to production routing, telemetry, hooks, providers, or
gates. It launches a host command only after dedicated operator opt-in. Persistent
evidence is an append-only JSONL projection: task packets, commands and stdout never
reach disk.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import Callable, Mapping, Sequence

from foundry.routing import ResolvedRoute
from foundry.routing_facades import (
    CLAUDE_USER_REQUEST_PREFIX,
    claude_invocation_model,
    claude_route_plan,
    codex_spawn_plan,
)


SCHEMA_VERSION = 3
ARTIFACT = "foundry.dual_host.measurement_trace"
F41_MANIFEST_SHA256 = "6dc995f3879165d4b2a9b72fbaaf6a729158ec4f9df43b67fe6a9b2eb8d50d5f"
OPT_IN_ENV = "FOUNDRY_MEASUREMENT_HARNESS"
HOST_PROFILES = {
    "claude": {"claude-sonnet-medium", "claude-opus-high", "claude-fable-high"},
    "codex": {"codex-terra-medium", "codex-sol-high"},
}
# Invocation model, effort, frozen CLI version, semantic role.
PROFILE_BINDINGS = {
    "claude": {
        "claude-sonnet-medium": ("sonnet", "medium", "2.1.224", "implementer"),
        "claude-opus-high": ("opus", "high", "2.1.224", "reviewer"),
        "claude-fable-high": ("fable", "high", "2.1.224", "architect"),
    },
    "codex": {
        "codex-terra-medium": ("gpt-5.6-terra", "medium", "0.147.0", "implementer"),
        "codex-sol-high": ("gpt-5.6-sol", "high", "0.147.0", "reviewer"),
    },
}
FROZEN_HOST_BINDINGS = {
    "claude": {
        "source": "claude_resolved", "host_version": "2.1.224",
        "profiles": {
            "claude-sonnet-medium": {"model": "sonnet", "effort": "medium", "role": "implementer"},
            "claude-opus-high": {"model": "opus", "effort": "high", "role": "reviewer"},
            "claude-fable-high": {"model": "fable", "effort": "high", "role": "architect"},
        },
    },
    "codex": {
        "source": "codex_resolved", "host_version": "0.147.0",
        "profiles": {
            "codex-terra-medium": {"model": "gpt-5.6-terra", "effort": "medium", "role": "implementer"},
            "codex-sol-high": {"model": "gpt-5.6-sol", "effort": "high", "role": "reviewer"},
        },
    },
}
TERMINAL_CLASSIFICATIONS = {
    ("completed", "none"), ("failed", "host"), ("failed", "timeout"),
    ("cancelled", "cancelled"), ("unknown", "unknown"),
}
METRIC_PROVENANCE = {
    "input_tokens": {"host_reported", "unavailable"},
    "output_tokens": {"host_reported", "unavailable"},
    "cached_input_tokens": {"host_reported", "unavailable"},
    "cache_write_input_tokens": {"host_reported", "unavailable"},
    "reasoning_output_tokens": {"host_reported", "unavailable"},
    "estimated_cost_usd": {"unavailable"},
    "duration_seconds": {"client_observed", "unavailable"},
    "test_outcome": {"unavailable"},
    "review_outcome": {"host_reported", "unavailable"},
}
METRICS = tuple(METRIC_PROVENANCE)
HOST_REPORTED_INPUTS = {
    "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "reasoning_output_tokens", "cache_state", "review_outcome",
}
MAX_RECORDS = 100
MAX_TRACE_BYTES = 64 * 1024
MAX_DURATION_SECONDS = 900
MAX_PACKET_BYTES = 16 * 1024
MAX_COMMAND_BYTES = 16 * 1024
MAX_RESULT_BYTES = 16 * 1024
MAX_STDERR_BYTES = 4 * 1024
RESERVATION_SLACK_BYTES = 64
CASE_ID = re.compile(r"FOUNDRY-[1-9][0-9]*\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
ROW_FIELDS = {
    "artifact", "version", "f41_manifest_sha256", "host", "profile_id", "case_id",
    "revision", "repetition", "binding", "terminal", "metrics", "cache_state",
}
PROTOCOL_FIELDS = {
    "artifact", "version", "status", "f41_manifest_sha256", "activation", "hosts",
    "limits", "privacy", "unavailable", "host_bindings", "terminal_classifications",
    "metric_inputs", "storage", "host_result_contract", "production_boundary",
}

# FOUNDRY-55 is an offline-only baseline projection over already validated F43 rows.
# It intentionally does not add an observer, provider call, or Context Broker.
CONTEXT_BASELINE_ARTIFACT = "foundry.context_usage.baseline"
CONTEXT_BASELINE_REPORT_ARTIFACT = "foundry.context_usage.baseline_report"
CONTEXT_BASELINE_PROTOCOL_ARTIFACT = "foundry.context_usage.baseline_protocol"
CONTEXT_BASELINE_VERSION = 1
CONTEXT_BASELINE_MAX_RECORDS = 100
CONTEXT_BASELINE_MAX_BYTES = 256 * 1024
CONTEXT_BASELINE_MAX_COST_USD = 1_000_000
F43_PROVIDERS = {"claude": "anthropic", "codex": "openai"}
CONTEXT_BASELINE_METRICS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "tool_result_tokens",
    "tool_calls",
    "tool_retries",
    "escalations",
    "models",
    "duration_seconds",
    "test_outcome",
    "review_outcome",
    "outcome",
    "merge_outcome",
    "provider_cost_usd",
)
CONTEXT_BASELINE_SOURCES = {
    "input_tokens": {"host_reported", "provider_reported"},
    "output_tokens": {"host_reported", "provider_reported"},
    "cached_input_tokens": {"host_reported", "provider_reported"},
    "cache_write_input_tokens": {"host_reported", "provider_reported"},
    "tool_result_tokens": {"provider_reported"},
    "tool_calls": {"host_reported", "provider_reported"},
    "tool_retries": {"host_reported", "provider_reported"},
    "escalations": {"host_reported"},
    "models": {"host_resolved"},
    "duration_seconds": {"client_observed"},
    "test_outcome": {"client_observed"},
    "review_outcome": {"host_reported"},
    "outcome": {"client_observed"},
    "merge_outcome": {"client_observed"},
    "provider_cost_usd": {"provider_reported"},
}
CONTEXT_BASELINE_RECORD_FIELDS = {
    "artifact", "version", "f43_measurement", "execution", "metrics",
}
CONTEXT_BASELINE_EXECUTION_FIELDS = {
    "execution_id", "measurement_sha256", "f41_manifest_sha256", "host", "provider",
    "profile_id", "task_id", "revision", "role", "model", "repetition",
}
CONTEXT_BASELINE_REPORT_FIELDS = {
    "artifact", "version", "measurements", "groups", "context_broker_gain",
}
CONTEXT_BASELINE_PROTOCOL = {
    "artifact": CONTEXT_BASELINE_PROTOCOL_ARTIFACT,
    "version": CONTEXT_BASELINE_VERSION,
    "status": "frozen_pre_run",
    "scope": (
        "FOUNDRY-55 available-only context and tool-result baseline; no Context Broker "
        "gain conclusion or benchmark execution."
    ),
    "upstream": {
        "f41_manifest_sha256": F41_MANIFEST_SHA256,
        "f43_trace": {"artifact": ARTIFACT, "version": SCHEMA_VERSION},
    },
    "execution_binding": [
        "execution_id", "measurement_sha256", "task_id", "revision", "role", "model",
        "repetition", "host", "provider",
    ],
    "f43_source": "embedded validated F43 row; every F55 metric is deterministically rederived",
    "metric_states": ["observed", "null", "unavailable"],
    "metrics": list(CONTEXT_BASELINE_METRICS),
    "metric_sources": {
        name: sorted(sources) for name, sources in CONTEXT_BASELINE_SOURCES.items()
    },
    "privacy": {
        "forbidden": [
            "prompt", "response", "excerpt", "path", "secret", "user_identifier",
            "url", "code",
        ],
        "schema": "exact allowlist; opaque SHA-256 evidence references only",
    },
    "cost": {
        "currency": "USD",
        "rule": (
            "numerator includes every comparable attempt cost; denominator includes only "
            "successful or merged outcomes; cost is unavailable unless every cost and "
            "outcome input is observed and known"
        ),
    },
    "context_broker_gain": {
        "state": "prohibited_before_baseline",
        "value": None,
    },
    "production_boundary": (
        "No automatic observation, Context Broker, provider call, model call, routing, "
        "escalation, gate, telemetry, test execution, or runtime mutation."
    ),
}


class MeasurementValidationError(ValueError):
    """A request, host result or trace violates the explicit measurement contract."""


@dataclass(frozen=True)
class MeasurementRequest:
    """Frozen F41 coordinates for one explicitly requested host invocation."""

    profile_id: str
    case_id: str
    revision: str
    repetition: int


@dataclass(frozen=True)
class MeasurementReceipt:
    """Safe result of a persisted invocation, with no command or host output."""

    digest: str
    terminal: Mapping[str, str]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MeasurementValidationError(message)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MeasurementValidationError("measurement value must be canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _bounded_text(value: object, *, label: str, maximum: int) -> str:
    _require(type(value) is str, f"{label} differs")
    _require(0 < len(value.encode("utf-8")) <= maximum, f"{label} exceeds its cap")
    return value


def _bounded_timeout(value: object) -> float:
    _require(type(value) in {int, float} and math.isfinite(value), "measurement timeout differs")
    result = float(value)
    _require(0 < result <= MAX_DURATION_SECONDS, "measurement timeout exceeds its cap")
    return result


def _metric(name: str, value: object) -> dict[str, object]:
    _require(type(value) is dict and set(value) == {"value", "provenance"}, f"{name} metric schema differs")
    metric, provenance = value["value"], value["provenance"]
    _require(type(provenance) is str and provenance in METRIC_PROVENANCE[name], f"{name} metric provenance differs")
    _require((metric is None) == (provenance == "unavailable"), f"{name} unavailable metrics must be null")
    if metric is None:
        return {"value": None, "provenance": provenance}
    if name in {
        "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
        "reasoning_output_tokens",
    }:
        _require(type(metric) is int and metric >= 0, f"{name} metric value differs")
    elif name == "duration_seconds":
        _require(type(metric) in {int, float} and math.isfinite(metric) and 0 < metric <= MAX_DURATION_SECONDS, "duration_seconds must be bounded")
    elif name == "review_outcome":
        _require(metric in {"approved", "changes_requested", "unknown"}, "review_outcome differs")
    return {"value": metric, "provenance": provenance}


def _observed_measurements(host_reported: Mapping[str, object], *, duration_seconds: float) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    _require(isinstance(host_reported, Mapping), "host-reported observation differs")
    observed = dict(host_reported)
    _require(set(observed) <= HOST_REPORTED_INPUTS, "host-reported observation differs")
    metrics: dict[str, dict[str, object]] = {}
    for name in METRICS:
        if name in observed:
            metrics[name] = {"value": observed[name], "provenance": "host_reported"}
        elif name == "duration_seconds":
            metrics[name] = {"value": duration_seconds, "provenance": "client_observed"}
        else:
            metrics[name] = {"value": None, "provenance": "unavailable"}
        metrics[name] = _metric(name, metrics[name])
    cache = {
        "value": observed.get("cache_state"),
        "provenance": "host_reported" if "cache_state" in observed else "unavailable",
    }
    _require(
        (cache["value"] is None) == (cache["provenance"] == "unavailable")
        and cache["value"] in {None, "hit", "miss"},
        "cache observed state differs",
    )
    return metrics, cache


def _validate_request(request: object, *, host: str | None = None) -> MeasurementRequest:
    _require(isinstance(request, MeasurementRequest), "measurement request differs")
    profile_host = next((candidate for candidate, values in HOST_PROFILES.items() if request.profile_id in values), None)
    _require(profile_host is not None and (host is None or profile_host == host), "measurement profile differs")
    _require(type(request.case_id) is str and CASE_ID.fullmatch(request.case_id) is not None, "measurement case identifier differs")
    _require(type(request.revision) is str and REVISION.fullmatch(request.revision) is not None, "measurement revision differs")
    _require(type(request.repetition) is int and request.repetition in {1, 2, 3}, "measurement repetition differs")
    _validate_f41_case(request.case_id, request.revision, request.repetition)
    return request


def _validate_f41_case(case_id: str, revision: str, repetition: int) -> None:
    """Bind every invocation to the immutable F41 corpus and manifest."""
    root = Path(__file__).resolve().parents[2] / "benchmarks" / "foundry-41"
    try:
        manifest_bytes = (root / "freeze-manifest-v1.json").read_bytes()
        _require(hashlib.sha256(manifest_bytes).hexdigest() == F41_MANIFEST_SHA256, "measurement F41 freeze differs")
        manifest = json.loads(manifest_bytes)
        corpus_bytes = (root / "corpus-v1.json").read_bytes()
        _require(type(manifest) is dict and type(manifest.get("files")) is dict and manifest["files"].get("corpus-v1.json") == hashlib.sha256(corpus_bytes).hexdigest(), "measurement F41 corpus digest differs")
        corpus = json.loads(corpus_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MeasurementValidationError("measurement F41 corpus is unreadable") from exc
    _require(type(corpus) is dict and type(corpus.get("cases")) is list, "measurement F41 corpus differs")
    _require(any(type(case) is dict and case.get("case_id") == case_id and case.get("revision") == revision and repetition in case.get("repetitions", []) for case in corpus["cases"]), "measurement case is not in the frozen F41 corpus")


def _profile(host: str, profile_id: str) -> tuple[str, str, str, str]:
    _require(host in PROFILE_BINDINGS and profile_id in PROFILE_BINDINGS[host], "measurement profile differs")
    return PROFILE_BINDINGS[host][profile_id]


def _route_override(route: object) -> bool:
    return any(getattr(warning, "code", None) == "HOST_OVERRIDE_NEUTRALIZES_POLICY" for warning in getattr(route, "warnings", ()))


def _route_projection(route: ResolvedRoute) -> dict[str, object]:
    return {"host": route.host, "role": route.role, "tier": route.selected_tier, "model": route.model, "effort": route.effort, "host_override_active": _route_override(route)}


def _materialize_command(
    command: object,
    *,
    host: str,
    model: str,
    effort: str,
) -> tuple[str, ...]:
    """Bind an operator command template to the profile selected by the runner."""
    _require(isinstance(command, Sequence) and not isinstance(command, (str, bytes)), "measurement command differs")
    argv = tuple(command)
    _require(argv and all(type(item) is str and item for item in argv), "measurement command differs")
    _require(argv[0] == host, "measurement command host differs")
    _require(sum(len(item.encode("utf-8")) for item in argv) <= MAX_COMMAND_BYTES, "measurement command exceeds its cap")
    if host == "codex":
        _require(argv == ("codex", "exec", "--json"), "Codex measurement command must use native JSONL")
        return (
            "codex", "exec", "--ephemeral", "--json", "--model", model,
            "-c", f'model_reasoning_effort="{effort}"', "-",
        )
    _require({"{model}", "{effort}"} <= set(argv), "measurement command does not bind the resolved profile")
    return tuple({"{model}": model, "{effort}": effort}.get(item, item) for item in argv)


def _resolved_root(root: object) -> Path:
    """Resolve the operator root once, then use that exact directory as subprocess cwd."""
    try:
        result = Path(root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise MeasurementValidationError("measurement root is invalid") from exc
    _require(result.is_dir(), "measurement root is invalid")
    return result


class _BoundedPipeReader:
    """Drain one pipe without retaining more than its explicit cap."""

    def __init__(self, stream, maximum: int):
        self._stream = stream
        self._maximum = maximum
        self.data = bytearray()
        self.overflowed = False
        self.invalid = False
        self._started = threading.Event()
        self._thread = threading.Thread(target=self._read, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def wait_started(self, timeout: float) -> bool:
        return self._started.wait(timeout)

    def close(self) -> None:
        try:
            self._stream.close()
        except OSError:
            pass

    def _read(self) -> None:
        self._started.set()
        while True:
            # At most one byte beyond the cap is ever requested from this pipe.
            try:
                chunk = self._stream.read(min(4096, self._maximum - len(self.data) + 1))
            except (OSError, ValueError):
                self.invalid = True
                return
            if not chunk:
                return
            if type(chunk) is not bytes:
                self.invalid = True
                return
            if len(self.data) + len(chunk) > self._maximum:
                self.overflowed = True
                return
            self.data.extend(chunk)


class _BoundedStdinWriter:
    """Feed the bounded packet off-thread so an unread pipe cannot block the caller."""

    def __init__(self, stream, value: bytes):
        self._stream = stream
        self._value = value
        self.failed = False
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._write, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float) -> bool:
        self._thread.join(timeout)
        return self._done.is_set()

    def close(self) -> None:
        try:
            self._stream.close()
        except OSError:
            pass

    def _write(self) -> None:
        try:
            offset = 0
            while offset < len(self._value):
                written = self._stream.write(self._value[offset:])
                if type(written) is not int or not 0 < written <= len(self._value) - offset:
                    raise OSError("measurement stdin write differs")
                offset += written
        except (OSError, TypeError, ValueError):
            self.failed = True
        finally:
            self.close()
            self._done.set()


def _close_process_streams(process: object) -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, name, None)
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass


def _terminate_process(process: object) -> None:
    """Terminate the dedicated process group, then force-kill if necessary."""
    if getattr(process, "poll", lambda: None)() is not None:
        _close_process_streams(process)
        return
    pid = getattr(process, "pid", None)
    try:
        if type(pid) is int and os.name == "posix":
            os.killpg(pid, signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError, AttributeError):
        pass
    try:
        process.wait(timeout=0.2)
    except (subprocess.TimeoutExpired, AttributeError):
        try:
            if type(pid) is int and os.name == "posix":
                os.killpg(pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError, AttributeError):
            pass
        try:
            process.wait(timeout=0.2)
        except (subprocess.TimeoutExpired, AttributeError):
            pass
    _close_process_streams(process)


def _terminal_from_returncode(returncode: int) -> dict[str, str]:
    if returncode in {-signal.SIGINT, -signal.SIGTERM, 130, 143}:
        return {"status": "cancelled", "failure_class": "cancelled"}
    return {"status": "failed", "failure_class": "host"}


def _run_command(command: tuple[str, ...], packet: str, *, timeout_seconds: float, cwd: Path, environ: Mapping[str, str] | None, process_factory: Callable[..., object]) -> tuple[dict[str, str], bytes | None, float]:
    """Launch a process group while capping stdout/stderr before buffering it."""
    started = time.monotonic()
    try:
        process = process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            cwd=str(cwd),
            env=None if environ is None else dict(environ),
        )
    except OSError:
        return {"status": "failed", "failure_class": "host"}, None, min(max(time.monotonic() - started, 1e-9), timeout_seconds)
    raw_stdout = getattr(process, "stdout", None)
    raw_stderr = getattr(process, "stderr", None)
    if raw_stdout is None or raw_stderr is None:
        _terminate_process(process)
        return {"status": "unknown", "failure_class": "unknown"}, None, min(
            max(time.monotonic() - started, 1e-9),
            timeout_seconds,
        )
    deadline = started + timeout_seconds
    stdout = _BoundedPipeReader(raw_stdout, MAX_RESULT_BYTES)
    stderr = _BoundedPipeReader(raw_stderr, MAX_STDERR_BYTES)
    stdin_writer: _BoundedStdinWriter | None = None
    try:
        stdin = getattr(process, "stdin", None)
        _require(stdin is not None, "measurement command result differs")
        stdin_writer = _BoundedStdinWriter(stdin, packet.encode("utf-8"))
        # The drainers and deadline are active before any packet byte is fed.
        # Stdin runs in a daemon so an unread pipe cannot retain the caller.
        stdout.start()
        stderr.start()
        for reader in (stdout, stderr):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not reader.wait_started(remaining):
                _terminate_process(process)
                return {"status": "failed", "failure_class": "timeout"}, None, timeout_seconds
        stdin_writer.start()
        while True:
            if stdout.overflowed or stderr.overflowed:
                _terminate_process(process)
                raise MeasurementValidationError("measurement host result exceeds its cap")
            if stdout.invalid or stderr.invalid:
                _terminate_process(process)
                raise MeasurementValidationError("measurement command result differs")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                return {"status": "failed", "failure_class": "timeout"}, None, timeout_seconds
            output_done = stdout.join(min(remaining, 0.02))
            error_done = stderr.join(0)
            input_done = stdin_writer.join(min(max(deadline - time.monotonic(), 0), 0.02))
            if output_done and error_done and input_done:
                break
        # A reader can set a failure flag just as its join reports completion.
        # Re-check every bound before an otherwise successful process is observed.
        if stdout.overflowed or stderr.overflowed:
            _terminate_process(process)
            raise MeasurementValidationError("measurement host result exceeds its cap")
        if stdout.invalid or stderr.invalid:
            _terminate_process(process)
            raise MeasurementValidationError("measurement command result differs")
        if time.monotonic() >= deadline:
            _terminate_process(process)
            return {"status": "failed", "failure_class": "timeout"}, None, timeout_seconds
        try:
            process.wait(timeout=max(deadline - time.monotonic(), 0))
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            return {"status": "failed", "failure_class": "timeout"}, None, timeout_seconds
        elapsed = time.monotonic() - started
        if elapsed >= timeout_seconds:
            _terminate_process(process)
            return {"status": "failed", "failure_class": "timeout"}, None, timeout_seconds
        if stdout.overflowed or stderr.overflowed:
            _terminate_process(process)
            raise MeasurementValidationError("measurement host result exceeds its cap")
        if stdout.invalid or stderr.invalid:
            _terminate_process(process)
            raise MeasurementValidationError("measurement command result differs")
        if time.monotonic() >= deadline:
            _terminate_process(process)
            return {"status": "failed", "failure_class": "timeout"}, None, timeout_seconds
        returncode = getattr(process, "returncode", None)
        _require(type(returncode) is int, "measurement command result differs")
        if returncode != 0:
            return _terminal_from_returncode(returncode), None, max(elapsed, 1e-9)
        if stdin_writer.failed:
            # A closed stdin does not overrule an observable process terminal.  If
            # the process nevertheless exits successfully, its actual work is unknown.
            return {"status": "unknown", "failure_class": "unknown"}, None, max(elapsed, 1e-9)
        return {"status": "completed", "failure_class": "none"}, bytes(stdout.data), max(elapsed, 1e-9)
    except MeasurementValidationError:
        _terminate_process(process)
        return {"status": "unknown", "failure_class": "unknown"}, None, min(
            max(time.monotonic() - started, 1e-9),
            timeout_seconds,
        )
    except OSError:
        _terminate_process(process)
        return {"status": "failed", "failure_class": "host"}, None, min(max(time.monotonic() - started, 1e-9), timeout_seconds)
    finally:
        if stdin_writer is not None:
            stdin_writer.close()
        stdout.close()
        stderr.close()
        if stdin_writer is not None:
            stdin_writer.join(0)
        stdout.join(0)
        stderr.join(0)


def _decode_host_result(value: bytes, *, fields: set[str], label: str) -> dict[str, object]:
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, ValueError) as exc:
        raise MeasurementValidationError(f"{label} host result is invalid") from exc
    _require(type(decoded) is dict and set(decoded) == fields, f"{label} host result schema differs")
    return decoded


def _host_observations(value: Mapping[str, object]) -> dict[str, object]:
    usage = value.get("usage")
    _require(isinstance(usage, Mapping) and set(usage) == {"input_tokens", "output_tokens", "cached_input_tokens"}, "measurement usage schema differs")
    observed = {name: usage[name] for name in usage if usage[name] is not None}
    for name in ("cache_state", "review_outcome"):
        if value.get(name) is not None:
            observed[name] = value[name]
    _observed_measurements(observed, duration_seconds=1e-9)
    return observed


def _claude_result(value: bytes, *, route: ResolvedRoute, profile_id: str) -> tuple[dict[str, object], dict[str, object]]:
    model, effort, version, _role = _profile("claude", profile_id)
    callback = _decode_host_result(value, fields={"model", "effort", "host_version", "host_override_active", "usage", "cache_state", "review_outcome"}, label="Claude")
    expected = {"model": model, "effort": effort, "host_version": version, "host_override_active": _route_override(route)}
    _require({name: callback[name] for name in expected} == expected, "Claude host callback differs from resolved route")
    return callback, _host_observations(callback)


def _codex_descriptor_projection(
    plan: object,
    *,
    profile_id: str,
    stdin_packet: str | None = None,
) -> dict[str, object]:
    _require(isinstance(plan, Mapping), "Codex spawn plan differs")
    spawn, route = plan.get("spawn"), plan.get("route")
    model, effort, _version, role = _profile("codex", profile_id)
    _require(plan.get("host") == "codex" and plan.get("mode") == "subagent" and plan.get("role") == role and isinstance(spawn, Mapping) and isinstance(route, Mapping), "Codex spawn plan differs")
    _require(spawn.get("model") == model and spawn.get("reasoning_effort") == effort and route.get("model") == model and route.get("effort") == effort, "Codex spawn plan differs from frozen profile")
    message = _bounded_text(spawn.get("message"), label="Codex task packet", maximum=MAX_PACKET_BYTES)
    stdin_value = message if stdin_packet is None else _bounded_text(
        stdin_packet,
        label="Codex stdin packet",
        maximum=MAX_PACKET_BYTES,
    )
    warnings = route.get("warnings")
    _require(isinstance(warnings, list) and all(isinstance(item, Mapping) and type(item.get("code")) is str for item in warnings), "Codex spawn plan differs")
    return {
        "host": "codex", "role": role, "mode": "subagent",
        "route": {"model": model, "effort": effort, "host_override_active": any(item["code"] == "HOST_OVERRIDE_NEUTRALIZES_POLICY" for item in warnings)},
        "spawn": {"task_name": spawn.get("task_name"), "agent_type": spawn.get("agent_type"), "fork_turns": spawn.get("fork_turns"), "model": model, "reasoning_effort": effort, "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest()},
        "stdin_sha256": hashlib.sha256(stdin_value.encode("utf-8")).hexdigest(),
    }


def codex_descriptor_identity(
    plan: object,
    *,
    profile_id: str,
    stdin_packet: str | None = None,
) -> str:
    """Return a digest of the resolved descriptor and exact packet bytes sent to stdin."""
    return _sha256(_codex_descriptor_projection(
        plan,
        profile_id=profile_id,
        stdin_packet=stdin_packet,
    ))


def verify_codex_descriptor(
    plan: object,
    identity: object,
    *,
    profile_id: str,
    stdin_packet: str | None = None,
) -> bool:
    """Verify a descriptor ID without persisting its packet bytes."""
    return (
        type(identity) is str
        and SHA256.fullmatch(identity) is not None
        and identity == codex_descriptor_identity(
            plan,
            profile_id=profile_id,
            stdin_packet=stdin_packet,
        )
    )


_CODEX_EVENT_TYPES = {
    "thread.started", "turn.started", "turn.completed", "turn.failed", "item.started",
    "item.updated", "item.completed", "error",
}
_CODEX_TERMINALS = {
    "turn.completed": {"status": "completed", "failure_class": "none"},
    "turn.failed": {"status": "failed", "failure_class": "host"},
}
_CODEX_SUMMARY_TERMINALS = set(_CODEX_TERMINALS) | {"process.exit", "process.timeout", "stream.unknown"}
_CODEX_USAGE_REQUIRED = {
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
}
_CODEX_USAGE_OPTIONAL = {"cache_write_input_tokens"}


def _codex_item(event: Mapping[str, object]) -> None:
    item = event.get("item")
    _require(isinstance(item, Mapping), "Codex JSONL item differs")
    _require(
        all(type(item.get(name)) is str and item[name] for name in ("id", "type", "text")),
        "Codex JSONL item differs",
    )


def _codex_error(value: object, *, label: str) -> None:
    _require(isinstance(value, Mapping) and type(value.get("message")) is str and value["message"], f"Codex JSONL {label} differs")


def _codex_usage(value: object) -> dict[str, object]:
    _require(isinstance(value, Mapping), "Codex JSONL usage differs")
    _require(
        _CODEX_USAGE_REQUIRED <= set(value) <= _CODEX_USAGE_REQUIRED | _CODEX_USAGE_OPTIONAL,
        "Codex JSONL usage differs",
    )
    observed: dict[str, object] = {}
    for name in _CODEX_USAGE_REQUIRED | _CODEX_USAGE_OPTIONAL:
        if name not in value:
            continue
        if value[name] is None:
            _require(name in _CODEX_USAGE_OPTIONAL, "Codex JSONL usage differs")
            continue
        _require(type(value[name]) is int and value[name] >= 0, "Codex JSONL usage differs")
        observed[name] = value[name]
    return observed


def _codex_jsonl_result(value: bytes) -> tuple[dict[str, str], dict[str, object], dict[str, object]]:
    """Parse the native ``codex exec --json`` stream without retaining event text."""
    try:
        text = value.decode("utf-8")
        _require(text.endswith("\n"), "Codex JSONL event stream is invalid")
        lines = text.splitlines()
        events = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, ValueError) as exc:
        raise MeasurementValidationError("Codex JSONL event stream is invalid") from exc
    _require(lines and all(type(event) is dict for event in events), "Codex JSONL event stream is invalid")
    _require(all(type(event.get("type")) is str and event["type"] in _CODEX_EVENT_TYPES for event in events), "Codex JSONL event stream is malformed")
    _require(events[0].get("type") == "thread.started", "Codex JSONL thread start differs")
    _require(type(events[0].get("thread_id")) is str and events[0]["thread_id"], "Codex JSONL thread start differs")
    _require(len(events) >= 3 and events[1].get("type") == "turn.started", "Codex JSONL turn start differs")
    _require("usage" not in events[0] and "usage" not in events[1], "Codex JSONL usage differs")

    saw_error = False
    for index, event in enumerate(events[2:], start=2):
        event_type = event["type"]
        terminal = index == len(events) - 1
        _require("usage" not in event or (terminal and event_type == "turn.completed"), "Codex JSONL usage differs")
        if event_type in {"item.started", "item.updated", "item.completed"}:
            _require(not terminal, "Codex JSONL event order differs")
            _codex_item(event)
            continue
        if event_type == "error":
            _require(not terminal, "Codex JSONL terminal event is missing")
            _codex_error(event, label="error")
            saw_error = True
            continue
        if event_type == "turn.completed":
            _require(terminal and not saw_error, "Codex JSONL terminal event is malformed")
            observed = _codex_usage(event.get("usage"))
            terminal_event = event_type
            break
        if event_type == "turn.failed":
            _require(terminal, "Codex JSONL terminal event is malformed")
            _codex_error(event.get("error"), label="turn failure")
            terminal_event = event_type
            observed = {}
            break
        _require(False, "Codex JSONL event order differs")
    else:
        raise MeasurementValidationError("Codex JSONL terminal event is missing")

    _observed_measurements(observed, duration_seconds=1e-9)
    summary = {
        "terminal_event": terminal_event,
        "event_count": len(events),
        "usage_event_count": 1 if terminal_event == "turn.completed" else 0,
    }
    return _CODEX_TERMINALS[terminal_event], summary, observed


def _base_record(*, host: str, request: MeasurementRequest, binding: Mapping[str, object], terminal: Mapping[str, str], host_reported: Mapping[str, object], duration_seconds: float) -> dict[str, object]:
    metrics, cache = _observed_measurements(host_reported, duration_seconds=duration_seconds)
    return {"artifact": ARTIFACT, "version": SCHEMA_VERSION, "f41_manifest_sha256": F41_MANIFEST_SHA256, "host": host, "profile_id": request.profile_id, "case_id": request.case_id, "revision": request.revision, "repetition": request.repetition, "binding": dict(binding), "terminal": dict(terminal), "metrics": metrics, "cache_state": cache}


def _reject_claude_route_envelope(packet: str) -> None:
    """Keep measurement packets out of the stateful facade request envelope."""
    first_line, _separator, _remaining = packet.partition("\n")
    _require(
        not first_line.startswith(CLAUDE_USER_REQUEST_PREFIX),
        "Claude measurement route request envelope is forbidden",
    )


def validate_record(value: object, *, host: str | None = None) -> dict[str, object]:
    _require(isinstance(value, Mapping) and set(value) == ROW_FIELDS, "measurement row has unknown or missing fields")
    _require(value.get("artifact") == ARTIFACT and value.get("version") == SCHEMA_VERSION, "measurement row identity differs")
    _require(value.get("f41_manifest_sha256") == F41_MANIFEST_SHA256, "measurement row freeze differs")
    row_host = value.get("host")
    _require(type(row_host) is str and row_host in HOST_PROFILES and (host is None or host == row_host), "measurement host differs")
    request = _validate_request(MeasurementRequest(value.get("profile_id"), value.get("case_id"), value.get("revision"), value.get("repetition")), host=row_host)
    model, effort, version, _role = _profile(row_host, request.profile_id)
    binding = value.get("binding")
    common = {"source", "model", "effort", "host_version", "host_override_active", "result_sha256"}
    identity = "route_sha256" if row_host == "claude" else "descriptor_sha256"
    required_binding = common | {identity}
    if row_host == "codex":
        required_binding.add("event_summary")
    _require(isinstance(binding, Mapping) and set(binding) == required_binding, "measurement binding schema differs")
    _require(binding.get("source") == f"{row_host}_resolved" and binding.get("model") == model and binding.get("effort") == effort and binding.get("host_version") == version and type(binding.get("host_override_active")) is bool and type(binding.get("result_sha256")) is str and SHA256.fullmatch(binding["result_sha256"]) is not None and type(binding.get(identity)) is str and SHA256.fullmatch(binding[identity]) is not None, "measurement binding differs from frozen host profile")
    if row_host == "codex":
        summary = binding.get("event_summary")
        _require(isinstance(summary, Mapping) and set(summary) == {"terminal_event", "event_count", "usage_event_count"} and summary.get("terminal_event") in _CODEX_SUMMARY_TERMINALS and type(summary.get("event_count")) is int and summary["event_count"] >= 0 and type(summary.get("usage_event_count")) is int and 0 <= summary["usage_event_count"] <= summary["event_count"] and (summary["event_count"] > 0 or summary["terminal_event"] in {"process.exit", "process.timeout", "stream.unknown"}), "measurement Codex event summary differs")
    terminal = value.get("terminal")
    _require(isinstance(terminal, Mapping) and set(terminal) == {"status", "failure_class"} and (terminal.get("status"), terminal.get("failure_class")) in TERMINAL_CLASSIFICATIONS, "measurement terminal classification differs")
    if row_host == "codex":
        terminal_event = binding["event_summary"]["terminal_event"]
        if terminal_event in _CODEX_TERMINALS:
            _require(dict(terminal) == _CODEX_TERMINALS[terminal_event], "measurement Codex terminal differs from event stream")
        elif terminal_event == "process.timeout":
            _require(dict(terminal) == {"status": "failed", "failure_class": "timeout"}, "measurement Codex terminal differs from process result")
        elif terminal_event == "stream.unknown":
            _require(dict(terminal) == {"status": "unknown", "failure_class": "unknown"}, "measurement Codex terminal differs from stream")
        else:
            _require(dict(terminal) != {"status": "completed", "failure_class": "none"}, "measurement Codex terminal differs from process result")
    metrics = value.get("metrics")
    _require(isinstance(metrics, Mapping) and set(metrics) == set(METRICS), "measurement metric set differs")
    cache = value.get("cache_state")
    _require(isinstance(cache, Mapping) and set(cache) == {"value", "provenance"} and (cache["value"] is None) == (cache["provenance"] == "unavailable") and cache["value"] in {None, "hit", "miss"} and cache["provenance"] in {"host_reported", "unavailable"}, "cache state differs")
    return {"artifact": ARTIFACT, "version": SCHEMA_VERSION, "f41_manifest_sha256": F41_MANIFEST_SHA256, "host": row_host, "profile_id": request.profile_id, "case_id": request.case_id, "revision": request.revision, "repetition": request.repetition, "binding": dict(binding), "terminal": dict(terminal), "metrics": {name: _metric(name, metrics[name]) for name in METRICS}, "cache_state": {"value": cache["value"], "provenance": cache["provenance"]}}


def reproduce_jsonl(value: bytes | str, *, host: str | None = None) -> tuple[list[dict[str, object]], str]:
    """Offline validate canonical append-only JSONL and calculate its digest."""
    raw = value.encode("utf-8") if type(value) is str else value
    _require(type(raw) is bytes and len(raw) <= MAX_TRACE_BYTES, "measurement trace byte cap differs")
    _require(not raw or raw.endswith(b"\n"), "measurement JSONL must end in a newline")
    try:
        lines = raw.splitlines()
        rows = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, ValueError) as exc:
        raise MeasurementValidationError("measurement JSONL is invalid") from exc
    _require(len(rows) <= MAX_RECORDS and all(line for line in lines), "measurement trace record cap differs")
    cleaned = [validate_record(row, host=host) for row in rows]
    keys = {(row["profile_id"], row["case_id"], row["revision"], row["repetition"]) for row in cleaned}
    _require(len(keys) == len(cleaned), "measurement JSONL contains duplicate rows")
    canonical = b"".join(_canonical(row) + b"\n" for row in cleaned)
    _require(canonical == raw, "measurement JSONL is not canonical")
    return cleaned, hashlib.sha256(canonical).hexdigest()


@dataclass
class _TraceReservation:
    """One host trace slot locked from preflight until its launched attempt is stored."""

    descriptor: int
    key: tuple[str, str, str, int]
    existing: bytes
    rows: int
    reserved_bytes: int
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.descriptor)


class MeasurementHarness:
    """Explicit append-only JSONL writer; production code never instantiates it."""

    def __init__(self, directory: str | Path, host: str, *, enabled: bool = False):
        _require(enabled is True, "measurement harness is disabled without explicit opt-in")
        _require(host in HOST_PROFILES, "measurement host differs")
        self._directory = Path(directory)
        self._host = host

    @classmethod
    def from_environ(cls, directory: str | Path, host: str, environ: Mapping[str, str] | None = None):
        values = os.environ if environ is None else environ
        return cls(directory, host, enabled=values.get(OPT_IN_ENV) == "1")

    @property
    def host(self) -> str:
        return self._host

    @property
    def path(self) -> Path:
        return self._directory / f"{self._host}.trace.jsonl"

    def read(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        try:
            return reproduce_jsonl(self.path.read_bytes(), host=self._host)[0]
        except OSError as exc:
            raise MeasurementValidationError("measurement JSONL is unreadable") from exc

    def _reserve(self, fallback: Mapping[str, object]) -> _TraceReservation:
        """Atomically reserve one privacy-safe trace slot before any host starts."""
        clean = validate_record(fallback, host=self._host)
        key = (clean["profile_id"], clean["case_id"], clean["revision"], clean["repetition"])
        reserved_bytes = len(_canonical(clean) + b"\n") + RESERVATION_SLACK_BYTES
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self._directory / f".{self._host}.trace.lock"
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            existing = self.path.read_bytes() if self.path.exists() else b""
            rows, _digest = reproduce_jsonl(existing, host=self._host)
            _require(all((row["profile_id"], row["case_id"], row["revision"], row["repetition"]) != key for row in rows), "measurement JSONL contains duplicate rows")
            _require(len(rows) + 1 <= MAX_RECORDS and len(existing) + reserved_bytes <= MAX_TRACE_BYTES, "measurement trace cap differs")
            return _TraceReservation(descriptor, key, existing, len(rows), reserved_bytes)
        except Exception:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise

    def _append(
        self,
        record: Mapping[str, object],
        *,
        fallback: Mapping[str, object],
        reservation: _TraceReservation,
    ) -> MeasurementReceipt:
        """Append the collected row or its reserved sanitized fallback exactly once."""
        try:
            fallback_clean = validate_record(fallback, host=self._host)
            _require(
                (fallback_clean["profile_id"], fallback_clean["case_id"], fallback_clean["revision"], fallback_clean["repetition"]) == reservation.key,
                "measurement reservation differs",
            )
            try:
                clean = validate_record(record, host=self._host)
            except MeasurementValidationError:
                clean = fallback_clean
            line = _canonical(clean) + b"\n"
            if (
                len(line) > reservation.reserved_bytes
                or len(reservation.existing) + len(line) > MAX_TRACE_BYTES
            ):
                clean = fallback_clean
                line = _canonical(clean) + b"\n"
            _require(len(line) <= reservation.reserved_bytes, "measurement trace cap differs")
            _require(reservation.rows + 1 <= MAX_RECORDS and len(reservation.existing) + len(line) <= MAX_TRACE_BYTES, "measurement trace cap differs")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                offset = 0
                while offset < len(line):
                    offset += os.write(descriptor, line[offset:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return MeasurementReceipt(
                hashlib.sha256(reservation.existing + line).hexdigest(),
                {"status": clean["terminal"]["status"], "failure_class": clean["terminal"]["failure_class"]},
            )
        finally:
            reservation.release()


def run_claude(harness: MeasurementHarness, request: MeasurementRequest, *, task_packet: str, root: str | os.PathLike, command: Sequence[str], timeout_seconds: float, environ: Mapping[str, str] | None = None, process_factory: Callable[..., object] = subprocess.Popen) -> MeasurementReceipt:
    """Resolve, launch, bound and observe one Claude invocation through stdout JSON."""
    _require(isinstance(harness, MeasurementHarness) and harness.host == "claude", "measurement harness host differs")
    request = _validate_request(request, host="claude")
    packet = _bounded_text(task_packet, label="Claude task packet", maximum=MAX_PACKET_BYTES)
    _reject_claude_route_envelope(packet)
    timeout = _bounded_timeout(timeout_seconds)
    resolved_root = _resolved_root(root)
    model, effort, version, role = _profile("claude", request.profile_id)
    argv = _materialize_command(command, host="claude", model=model, effort=effort)
    resolved = claude_route_plan(role, packet, root=resolved_root, environ=environ)
    route = resolved.get("route") if isinstance(resolved, Mapping) else None
    _require(isinstance(route, ResolvedRoute) and route.host == "claude", "Claude resolved route differs")
    _require(claude_invocation_model(route.model) == model and route.effort == effort, "Claude resolved route differs from frozen profile")
    unknown = {"status": "unknown", "failure_class": "unknown"}
    binding = {"source": "claude_resolved", "model": model, "effort": effort, "host_version": version, "host_override_active": _route_override(route), "route_sha256": _sha256(_route_projection(route)), "result_sha256": _sha256({"terminal": unknown})}
    reservation = harness._reserve(_base_record(
        host="claude",
        request=request,
        binding=binding,
        terminal=unknown,
        host_reported={},
        duration_seconds=MAX_DURATION_SECONDS,
    ))
    try:
        terminal, stdout, duration = _run_command(
            argv,
            packet,
            timeout_seconds=timeout,
            cwd=resolved_root,
            environ=environ,
            process_factory=process_factory,
        )
        binding["result_sha256"] = _sha256({"terminal": terminal})
        observed: Mapping[str, object] = {}
        if terminal == {"status": "completed", "failure_class": "none"}:
            try:
                _require(stdout not in {None, b""}, "Claude host callback is missing")
                callback, observed = _claude_result(stdout, route=route, profile_id=request.profile_id)
            except MeasurementValidationError:
                terminal = unknown
                observed = {}
                binding["result_sha256"] = _sha256({"terminal": terminal})
            else:
                binding["result_sha256"] = _sha256(callback)
        record = _base_record(
            host="claude",
            request=request,
            binding=binding,
            terminal=terminal,
            host_reported=observed,
            duration_seconds=duration,
        )
        fallback_terminal = terminal if terminal != {"status": "completed", "failure_class": "none"} else unknown
        fallback_binding = dict(binding)
        fallback_binding["result_sha256"] = _sha256({"terminal": fallback_terminal})
        fallback = _base_record(
            host="claude",
            request=request,
            binding=fallback_binding,
            terminal=fallback_terminal,
            host_reported={},
            duration_seconds=duration,
        )
        return harness._append(record, fallback=fallback, reservation=reservation)
    finally:
        reservation.release()


def run_codex(harness: MeasurementHarness, request: MeasurementRequest, *, task_packet: str, root: str | os.PathLike, command: Sequence[str], timeout_seconds: float, effective_profile: Mapping[str, object] | None = None, environ: Mapping[str, str] | None = None, process_factory: Callable[..., object] = subprocess.Popen) -> MeasurementReceipt:
    """Resolve a Codex spawn plan, bind it, launch a bounded command and observe it."""
    _require(isinstance(harness, MeasurementHarness) and harness.host == "codex", "measurement harness host differs")
    request = _validate_request(request, host="codex")
    packet = _bounded_text(task_packet, label="Codex task packet", maximum=MAX_PACKET_BYTES)
    timeout = _bounded_timeout(timeout_seconds)
    resolved_root = _resolved_root(root)
    _model, _effort, version, role = _profile("codex", request.profile_id)
    plan = codex_spawn_plan(role, packet, root=resolved_root, effective_profile=effective_profile, _observe=False)
    descriptor = codex_descriptor_identity(
        plan,
        profile_id=request.profile_id,
        stdin_packet=packet,
    )
    argv = _materialize_command(
        command,
        host="codex",
        model=plan["spawn"]["model"],
        effort=plan["spawn"]["reasoning_effort"],
    )
    execution_environ = dict(os.environ if environ is None else environ)
    execution_environ.pop("FOUNDRY_MEASUREMENT_DESCRIPTOR_SHA256", None)
    route = plan["route"]
    warnings = route.get("warnings")
    override = isinstance(warnings, list) and any(isinstance(item, Mapping) and item.get("code") == "HOST_OVERRIDE_NEUTRALIZES_POLICY" for item in warnings)
    unknown = {"status": "unknown", "failure_class": "unknown"}
    unknown_summary: dict[str, object] = {
        "terminal_event": "stream.unknown",
        "event_count": 0,
        "usage_event_count": 0,
    }
    binding = {"source": "codex_resolved", "model": plan["spawn"]["model"], "effort": plan["spawn"]["reasoning_effort"], "host_version": version, "host_override_active": override, "descriptor_sha256": descriptor, "event_summary": unknown_summary, "result_sha256": _sha256({"terminal": unknown, "event_summary": unknown_summary})}
    reservation = harness._reserve(_base_record(
        host="codex",
        request=request,
        binding=binding,
        terminal=unknown,
        host_reported={},
        duration_seconds=MAX_DURATION_SECONDS,
    ))
    try:
        terminal, stdout, duration = _run_command(
            argv,
            packet,
            timeout_seconds=timeout,
            cwd=resolved_root,
            environ=execution_environ,
            process_factory=process_factory,
        )
        event_summary: dict[str, object] = {
            "terminal_event": "process.timeout" if terminal["failure_class"] == "timeout" else "process.exit",
            "event_count": 0,
            "usage_event_count": 0,
        }
        observed: Mapping[str, object] = {}
        if terminal == {"status": "completed", "failure_class": "none"}:
            try:
                _require(stdout is not None, "Codex JSONL event stream is missing")
                terminal, event_summary, observed = _codex_jsonl_result(stdout)
            except MeasurementValidationError:
                terminal = unknown
                event_summary = unknown_summary
                observed = {}
        binding["event_summary"] = event_summary
        binding["result_sha256"] = _sha256({
            "terminal": terminal,
            "event_summary": event_summary,
            "observed": observed,
        })
        record = _base_record(
            host="codex",
            request=request,
            binding=binding,
            terminal=terminal,
            host_reported=observed,
            duration_seconds=duration,
        )
        fallback_terminal = terminal if terminal != {"status": "completed", "failure_class": "none"} else unknown
        fallback_summary = event_summary if fallback_terminal == terminal else unknown_summary
        fallback_binding = dict(binding)
        fallback_binding["event_summary"] = fallback_summary
        fallback_binding["result_sha256"] = _sha256({
            "terminal": fallback_terminal,
            "event_summary": fallback_summary,
        })
        fallback = _base_record(
            host="codex",
            request=request,
            binding=fallback_binding,
            terminal=fallback_terminal,
            host_reported={},
            duration_seconds=duration,
        )
        return harness._append(record, fallback=fallback, reservation=reservation)
    finally:
        reservation.release()


def validate_protocol(value: object) -> dict[str, object]:
    """Validate the frozen F43 operator contract without invoking a host."""
    _require(isinstance(value, Mapping) and set(value) == PROTOCOL_FIELDS, "measurement protocol has unknown or missing fields")
    _require(value["artifact"] == "foundry.dual_host.measurement_protocol" and value["version"] == 1 and value["status"] == "frozen_pre_run", "measurement protocol identity differs")
    _require(value["f41_manifest_sha256"] == F41_MANIFEST_SHA256, "measurement protocol freeze differs")
    _require(value["activation"] == {"environment": OPT_IN_ENV, "value": "1", "default": "disabled"}, "measurement protocol activation differs")
    _require(value["hosts"] == sorted(HOST_PROFILES), "measurement protocol hosts differ")
    _require(value["limits"] == {"records_per_host": MAX_RECORDS, "trace_bytes_per_host": MAX_TRACE_BYTES, "duration_seconds_max": MAX_DURATION_SECONDS, "packet_bytes_max": MAX_PACKET_BYTES, "command_bytes_max": MAX_COMMAND_BYTES, "result_bytes_max": MAX_RESULT_BYTES, "stderr_bytes_max": MAX_STDERR_BYTES}, "measurement protocol limits differ")
    _require(value["privacy"] == {"forbidden": ["prompt", "response", "excerpt", "path", "secret", "user_identifier", "url", "code"], "schema": "exact allowlist"}, "measurement protocol privacy differs")
    _require(value["unavailable"] == {"value": None, "provenance": "unavailable"}, "measurement protocol unavailable rule differs")
    _require(value["host_bindings"] == FROZEN_HOST_BINDINGS, "measurement protocol host bindings differ")
    _require(value["terminal_classifications"] == [["completed", "none"], ["failed", "host"], ["failed", "timeout"], ["cancelled", "cancelled"], ["unknown", "unknown"]], "measurement protocol terminal classifications differ")
    _require(value["metric_inputs"] == {"host_reported": ["input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens", "cache_state", "review_outcome"], "client_observed": ["duration_seconds"], "always_unavailable": ["estimated_cost_usd", "test_outcome"]}, "measurement protocol metric inputs differ")
    _require(value["storage"] == {"format": "append_only_jsonl", "trace_schema_version": SCHEMA_VERSION, "line": "canonical JSON object plus LF", "prelaunch_reservation": "atomic host-trace lock validates key and capacity before Popen"}, "measurement protocol storage differs")
    _require(value["host_result_contract"] == {"transport": {"packet": "stdin only; Claude FOUNDRY_ROUTE_REQUEST envelope rejected", "claude_stdout": "one JSON object", "codex_stdout": "native codex exec --json JSONL", "stderr": "discarded"}, "working_directory": "resolved validated --root", "command_placeholders": {"claude": ["{model}", "{effort}"], "codex": []}, "codex_native_argv": ["codex", "exec", "--ephemeral", "--json", "--model", "<resolved model>", "-c", "model_reasoning_effort=\"<resolved effort>\"", "-"], "claude_success_fields": ["model", "effort", "host_version", "host_override_active", "usage", "cache_state", "review_outcome"], "codex_jsonl": {"event_types": ["thread.started", "turn.started", "turn.completed", "turn.failed", "item.started", "item.updated", "item.completed", "error"], "terminal_events": ["turn.completed", "turn.failed"], "usage_keys": ["input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"], "optional_usage_keys": ["cache_write_input_tokens"], "sequence": ["thread.started(thread_id)", "turn.started", "item.*|error*", "turn.completed|turn.failed"], "item_required_fields": ["id", "type", "text"], "turn_failed_error": "error.message", "error_notification": {"message": "required", "position": "active turn before turn.failed", "terminal": False}, "usage_event": "turn.completed only", "terminal_rule": "exactly one terminal event and it is final", "stream_failure": {"terminal": ["unknown", "unknown"], "summary_terminal": "stream.unknown", "host_metrics": "unavailable"}}, "codex_descriptor_binding": "runner-owned trace digest binds resolved descriptor and exact stdin UTF-8 bytes", "post_launch_failure": {"record": "exactly one sanitized JSONL row", "terminal": ["unknown", "unknown"], "host_metrics": "unavailable", "decode_value_error": "sanitized unknown row", "preserve_process_terminal": ["failed:timeout", "cancelled:cancelled", "failed:host"]}}, "measurement protocol host result contract differs")
    _require(value["production_boundary"] == "No automatic observation, model call, network call, test execution, routing, escalation, gate, or telemetry mutation.", "measurement protocol production boundary differs")
    return dict(value)


def validate_context_baseline_protocol(value: object) -> dict[str, object]:
    """Validate the frozen F55 baseline contract without starting any runtime."""
    _require(isinstance(value, Mapping), "context baseline protocol differs")
    _require(
        value.get("context_broker_gain") == {
            "state": "prohibited_before_baseline",
            "value": None,
        },
        "context baseline broker gain differs",
    )
    _require(
        _canonical(value) == _canonical(CONTEXT_BASELINE_PROTOCOL),
        "context baseline protocol differs",
    )
    return dict(CONTEXT_BASELINE_PROTOCOL)


def _baseline_execution_id(
    *,
    measurement_sha256: str,
    host: str,
    provider: str,
    profile_id: str,
    task_id: str,
    revision: str,
    role: str,
    model: str,
    repetition: int,
) -> str:
    return _sha256({
        "measurement_sha256": measurement_sha256,
        "host": host,
        "provider": provider,
        "profile_id": profile_id,
        "task_id": task_id,
        "revision": revision,
        "role": role,
        "model": model,
        "repetition": repetition,
    })


def _context_baseline_execution(measurement: Mapping[str, object]) -> dict[str, object]:
    """Project a validated F43 row into the F55 execution binding."""
    clean = validate_record(measurement)
    host = clean["host"]
    model, _effort, _version, role = _profile(host, clean["profile_id"])
    measurement_sha256 = _sha256(clean)
    execution = {
        "measurement_sha256": measurement_sha256,
        "f41_manifest_sha256": F41_MANIFEST_SHA256,
        "host": host,
        "provider": F43_PROVIDERS[host],
        "profile_id": clean["profile_id"],
        "task_id": clean["case_id"],
        "revision": clean["revision"],
        "role": role,
        "model": model,
        "repetition": clean["repetition"],
    }
    return {
        "execution_id": _baseline_execution_id(
            measurement_sha256=measurement_sha256,
            host=host,
            provider=F43_PROVIDERS[host],
            profile_id=clean["profile_id"],
            task_id=clean["case_id"],
            revision=clean["revision"],
            role=role,
            model=model,
            repetition=clean["repetition"],
        ),
        **execution,
    }


def _validate_context_baseline_execution(value: object) -> dict[str, object]:
    _require(
        isinstance(value, Mapping) and set(value) == CONTEXT_BASELINE_EXECUTION_FIELDS,
        "context baseline execution schema differs",
    )
    host = value.get("host")
    provider = value.get("provider")
    _require(
        type(host) is str and host in F43_PROVIDERS and provider == F43_PROVIDERS[host],
        "context baseline execution differs",
    )
    request = _validate_request(MeasurementRequest(
        value.get("profile_id"),
        value.get("task_id"),
        value.get("revision"),
        value.get("repetition"),
    ), host=host)
    model, _effort, _version, role = _profile(host, request.profile_id)
    _require(
        value.get("f41_manifest_sha256") == F41_MANIFEST_SHA256
        and value.get("role") == role
        and value.get("model") == model,
        "context baseline execution differs",
    )
    measurement_sha256 = value.get("measurement_sha256")
    _require(
        type(measurement_sha256) is str and SHA256.fullmatch(measurement_sha256) is not None,
        "context baseline execution differs",
    )
    expected_id = _baseline_execution_id(
        measurement_sha256=measurement_sha256,
        host=host,
        provider=provider,
        profile_id=request.profile_id,
        task_id=request.case_id,
        revision=request.revision,
        role=role,
        model=model,
        repetition=request.repetition,
    )
    _require(value.get("execution_id") == expected_id, "context baseline execution differs")
    return dict(value)


def _context_baseline_value(name: str, value: object, *, model: str) -> object:
    if name in {
        "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
        "tool_result_tokens", "tool_calls", "tool_retries", "escalations",
    }:
        _require(type(value) is int and 0 <= value <= 1_000_000, "context baseline metric value differs")
        return value
    if name == "models":
        _require(type(value) is list and value == [model], "context baseline metric value differs")
        return list(value)
    if name == "duration_seconds":
        _require(
            type(value) in {int, float} and math.isfinite(value)
            and 0 < value <= MAX_DURATION_SECONDS,
            "context baseline metric value differs",
        )
        return float(value)
    if name == "test_outcome":
        _require(
            type(value) is str and value in {"pass", "fail", "unknown"},
            "context baseline metric value differs",
        )
        return value
    if name == "review_outcome":
        _require(
            type(value) is str and value in {"approved", "changes_requested", "unknown"},
            "context baseline metric value differs",
        )
        return value
    if name == "outcome":
        _require(
            type(value) is str and value in {"success", "failure", "unknown"},
            "context baseline metric value differs",
        )
        return value
    if name == "merge_outcome":
        _require(
            type(value) is str and value in {"merged", "not_merged", "unknown"},
            "context baseline metric value differs",
        )
        return value
    _require(
        type(value) in {int, float} and math.isfinite(value)
        and 0 <= value <= CONTEXT_BASELINE_MAX_COST_USD,
        "context baseline metric value differs",
    )
    return float(value)


def _validate_context_baseline_metric(
    name: str,
    value: object,
    *,
    model: str,
) -> dict[str, object]:
    _require(name in CONTEXT_BASELINE_METRICS and isinstance(value, Mapping), "context baseline metric schema differs")
    state = value.get("state")
    if state == "unavailable":
        _require(set(value) == {"state", "value"} and value.get("value") is None, "context baseline unavailable metric differs")
        return {"state": "unavailable", "value": None}
    _require(
        type(state) is str and state in {"observed", "null"},
        "context baseline metric state differs",
    )
    _require(
        set(value) == {"state", "value", "source", "evidence_sha256"},
        "context baseline metric schema differs",
    )
    source = value.get("source")
    evidence = value.get("evidence_sha256")
    _require(
        type(source) is str and source in CONTEXT_BASELINE_SOURCES[name],
        "context baseline metric source differs",
    )
    _require(
        type(evidence) is str and SHA256.fullmatch(evidence) is not None,
        "context baseline metric evidence differs",
    )
    if state == "null":
        _require(value.get("value") is None, "context baseline null metric differs")
        return {
            "state": "null", "value": None, "source": source,
            "evidence_sha256": evidence,
        }
    return {
        "state": "observed",
        "value": _context_baseline_value(name, value.get("value"), model=model),
        "source": source,
        "evidence_sha256": evidence,
    }


def validate_context_baseline_record(value: object) -> dict[str, object]:
    """Fail closed unless every public F55 metric rederives from its F43 row."""
    _require(
        isinstance(value, Mapping) and set(value) == CONTEXT_BASELINE_RECORD_FIELDS,
        "context baseline schema differs",
    )
    _require(
        value.get("artifact") == CONTEXT_BASELINE_ARTIFACT
        and value.get("version") == CONTEXT_BASELINE_VERSION,
        "context baseline identity differs",
    )
    measurement = validate_record(value.get("f43_measurement"))
    expected_execution = _context_baseline_execution(measurement)
    execution = _validate_context_baseline_execution(value.get("execution"))
    _require(execution == expected_execution, "context baseline execution differs")
    metrics = value.get("metrics")
    _require(
        isinstance(metrics, Mapping) and set(metrics) == set(CONTEXT_BASELINE_METRICS),
        "context baseline metric set differs",
    )
    clean_metrics = {
        name: _validate_context_baseline_metric(
            name,
            metrics[name],
            model=execution["model"],
        )
        for name in CONTEXT_BASELINE_METRICS
    }
    _require(
        clean_metrics == _context_baseline_metrics(measurement, execution=execution),
        "context baseline derivation differs",
    )
    return {
        "artifact": CONTEXT_BASELINE_ARTIFACT,
        "version": CONTEXT_BASELINE_VERSION,
        "f43_measurement": measurement,
        "execution": execution,
        "metrics": clean_metrics,
    }


def _context_baseline_evidence(
    measurement: Mapping[str, object],
    *,
    source: str,
    name: str,
    value: object,
) -> str:
    return _sha256({
        "measurement_sha256": _sha256(measurement),
        "source": source,
        "name": name,
        "value": value,
    })


def _observed_context_baseline_metric(
    measurement: Mapping[str, object],
    *,
    name: str,
    value: object,
    source: str,
) -> dict[str, object]:
    return {
        "state": "observed",
        "value": value,
        "source": source,
        "evidence_sha256": _context_baseline_evidence(
            measurement,
            source=source,
            name=name,
            value=value,
        ),
    }


def _context_baseline_metrics(
    measurement: Mapping[str, object],
    *,
    execution: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    """Return the only v1 metric projection permitted from a validated F43 row."""
    metrics: dict[str, dict[str, object]] = {
        name: {"state": "unavailable", "value": None}
        for name in CONTEXT_BASELINE_METRICS
    }
    for name in (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
    ):
        metric = measurement["metrics"][name]
        if metric["provenance"] == "host_reported":
            metrics[name] = _observed_context_baseline_metric(
                measurement,
                name=name,
                value=metric["value"],
                source="host_reported",
            )
    duration = measurement["metrics"]["duration_seconds"]
    if duration["provenance"] == "client_observed":
        metrics["duration_seconds"] = _observed_context_baseline_metric(
            measurement,
            name="duration_seconds",
            value=duration["value"],
            source="client_observed",
        )
    review = measurement["metrics"]["review_outcome"]
    if review["provenance"] == "host_reported":
        metrics["review_outcome"] = _observed_context_baseline_metric(
            measurement,
            name="review_outcome",
            value=review["value"],
            source="host_reported",
        )
    metrics["models"] = _observed_context_baseline_metric(
        measurement,
        name="models",
        value=[execution["model"]],
        source="host_resolved",
    )
    return metrics


def context_baseline_from_measurement(measurement: object) -> dict[str, object]:
    """Create an available-only F55 baseline row from one validated F43 trace row."""
    clean = validate_record(measurement)
    execution = _context_baseline_execution(clean)
    return validate_context_baseline_record({
        "artifact": CONTEXT_BASELINE_ARTIFACT,
        "version": CONTEXT_BASELINE_VERSION,
        "f43_measurement": clean,
        "execution": execution,
        "metrics": _context_baseline_metrics(clean, execution=execution),
    })


def verify_context_baseline_binding(value: object, measurement: object) -> bool:
    """Prove that a baseline carries the exact validated F43 source passed by caller."""
    baseline = validate_context_baseline_record(value)
    clean = validate_record(measurement)
    _require(
        baseline["f43_measurement"] == clean
        and baseline["execution"] == _context_baseline_execution(clean),
        "context baseline binding differs",
    )
    return True


def _context_baseline_cost(
    records: Sequence[Mapping[str, object]],
    *,
    outcome_name: str,
    selected_value: str,
) -> dict[str, object]:
    pairs = [
        (record["metrics"]["provider_cost_usd"], record["metrics"][outcome_name])
        for record in records
    ]
    if any(cost["state"] != "observed" or outcome["state"] != "observed" for cost, outcome in pairs):
        return {"state": "unavailable", "value": None}
    negative_value = {"success": "failure", "merged": "not_merged"}.get(selected_value)
    _require(negative_value is not None, "context baseline cost outcome differs")
    if any(outcome["value"] not in {selected_value, negative_value} for _cost, outcome in pairs):
        return {"state": "unavailable", "value": None}
    denominator = sum(outcome["value"] == selected_value for _cost, outcome in pairs)
    if denominator == 0:
        return {"state": "unavailable", "value": None}
    return {
        "state": "observed",
        "value": sum(cost["value"] for cost, _outcome in pairs) / denominator,
    }


def _context_baseline_slot(record: Mapping[str, object]) -> tuple[str, str, str, str, int]:
    """Return the immutable F41 slot; result/digest variation cannot create another row."""
    execution = record["execution"]
    return (
        execution["host"],
        execution["profile_id"],
        execution["task_id"],
        execution["revision"],
        execution["repetition"],
    )


def _context_baseline_groups(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for record in records:
        execution = record["execution"]
        grouped.setdefault((execution["host"], execution["provider"]), []).append(record)
    groups: list[dict[str, object]] = []
    for (host, provider), group_records in sorted(grouped.items()):
        summary = {
            name: {
                state: sum(record["metrics"][name]["state"] == state for record in group_records)
                for state in ("observed", "null", "unavailable")
            }
            for name in CONTEXT_BASELINE_METRICS
        }
        groups.append({
            "host": host,
            "provider": provider,
            "measurement_count": len(group_records),
            "metrics": summary,
            "cost_per_success_usd": _context_baseline_cost(
                group_records,
                outcome_name="outcome",
                selected_value="success",
            ),
            "cost_per_merge_usd": _context_baseline_cost(
                group_records,
                outcome_name="merge_outcome",
                selected_value="merged",
            ),
        })
    return groups


def context_baseline_report(values: Sequence[object]) -> dict[str, object]:
    """Report F55 rows per host/provider without deriving a Broker comparison."""
    _require(
        isinstance(values, Sequence) and not isinstance(values, (str, bytes))
        and 0 < len(values) <= CONTEXT_BASELINE_MAX_RECORDS,
        "context baseline record cap differs",
    )
    records = [validate_context_baseline_record(value) for value in values]
    records.sort(key=lambda row: row["execution"]["execution_id"])
    _require(
        len({_context_baseline_slot(row) for row in records}) == len(records),
        "context baseline contains duplicate F41 slots",
    )
    report = {
        "artifact": CONTEXT_BASELINE_REPORT_ARTIFACT,
        "version": CONTEXT_BASELINE_VERSION,
        "measurements": records,
        "groups": _context_baseline_groups(records),
        "context_broker_gain": {
            "state": "prohibited_before_baseline",
            "value": None,
        },
    }
    _require(
        len(_canonical(report)) <= CONTEXT_BASELINE_MAX_BYTES,
        "context baseline byte cap differs",
    )
    return validate_context_baseline_report(report)


def validate_context_baseline_report(value: object) -> dict[str, object]:
    """Offline, strict validation of the F55 report and its no-gain boundary."""
    _require(
        isinstance(value, Mapping) and set(value) == CONTEXT_BASELINE_REPORT_FIELDS,
        "context baseline report schema differs",
    )
    _require(
        value.get("artifact") == CONTEXT_BASELINE_REPORT_ARTIFACT
        and value.get("version") == CONTEXT_BASELINE_VERSION,
        "context baseline report identity differs",
    )
    _require(
        value.get("context_broker_gain") == {
            "state": "prohibited_before_baseline",
            "value": None,
        },
        "context baseline broker gain differs",
    )
    measurements = value.get("measurements")
    _require(
        type(measurements) is list and 0 < len(measurements) <= CONTEXT_BASELINE_MAX_RECORDS,
        "context baseline report measurements differ",
    )
    records = [validate_context_baseline_record(row) for row in measurements]
    _require(
        records == sorted(records, key=lambda row: row["execution"]["execution_id"]),
        "context baseline report order differs",
    )
    _require(
        len({_context_baseline_slot(row) for row in records}) == len(records),
        "context baseline contains duplicate F41 slots",
    )
    groups = value.get("groups")
    _require(type(groups) is list and groups == _context_baseline_groups(records), "context baseline metric summary differs")
    _require(len(_canonical(value)) <= CONTEXT_BASELINE_MAX_BYTES, "context baseline byte cap differs")
    return {
        "artifact": CONTEXT_BASELINE_REPORT_ARTIFACT,
        "version": CONTEXT_BASELINE_VERSION,
        "measurements": records,
        "groups": groups,
        "context_broker_gain": {
            "state": "prohibited_before_baseline",
            "value": None,
        },
    }


def _command_file(path: str) -> tuple[str, ...]:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MeasurementValidationError("measurement command file is invalid") from exc
    _require(type(value) is list, "measurement command file differs")
    return tuple(value)


def _packet_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise MeasurementValidationError("measurement task packet is unreadable") from exc


def main(argv: list[str] | None = None) -> None:
    """Operator-only CLI; it performs no work without the dedicated opt-in flag."""
    parser = argparse.ArgumentParser(description="Run one opt-in bounded FOUNDRY-43 measurement")
    parser.add_argument("host", choices=sorted(HOST_PROFILES))
    parser.add_argument("--trace-dir", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--packet-file", required=True)
    parser.add_argument("--command-file", required=True)
    args = parser.parse_args(argv)
    try:
        instance = MeasurementHarness.from_environ(args.trace_dir, args.host)
        request = MeasurementRequest(args.profile_id, args.case_id, args.revision, args.repetition)
        common = {"task_packet": _packet_file(args.packet_file), "root": args.root, "command": _command_file(args.command_file), "timeout_seconds": args.timeout_seconds}
        receipt = run_claude(instance, request, **common) if args.host == "claude" else run_codex(instance, request, **common)
    except MeasurementValidationError as exc:
        parser.error(str(exc))
    print(json.dumps({"digest": receipt.digest, "terminal": receipt.terminal}, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
