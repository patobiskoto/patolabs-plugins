"""Deterministic, candidate-agnostic contract for local-scout benchmarks.

This module validates evidence produced by an external benchmark runner.  It does
not install, download, start, stop, supervise, or invoke a model or daemon.  Runtime
state remains operator/runtime-owned and is represented only by opaque preparation
identities and attestations in the result artifact.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn


CONTRACT_ARTIFACT = "foundry.local_scout.benchmark_contract"
RESULT_ARTIFACT = "foundry.local_scout.benchmark_result"
CONTRACT_VERSION = 1
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
# Fixed v1 allowance for monotonic-clock observation/scheduling lag after a deadline.
_V1_TIMEOUT_OBSERVATION_OVERHEAD_MAX_SECONDS = 0.25

_STATE_ORDER = ("cold_start", "warm_run", "already_loaded")
_METRICS = ("timeout_rate", "median_seconds", "p95_seconds")
_REQUIRED_METADATA = (
    "candidate.identifier",
    "candidate.checksum_sha256",
    "candidate.quantization",
    "runtime.name",
    "runtime.version",
    "runtime.instance_id",
    "prompt_template_sha256",
    "hardware.host_id",
    "hardware.architecture",
    "hardware.cpu_model",
    "hardware.logical_cpu_count",
    "hardware.memory_bytes",
    "hardware.accelerator_model",
    "hardware.accelerator_count",
    "hardware.accelerator_memory_bytes",
    "effective_limits.connection_timeout_seconds",
    "effective_limits.total_timeout_seconds",
    "request_parameters.max_output_tokens",
    "request_parameters.temperature",
    "request_parameters.top_p",
    "request_parameters.seed",
    "request_parameters.stream",
)
_STATE_CONTRACTS = (
    {
        "state": "cold_start",
        "loaded_before_timing": False,
        "preparation_kind": "external_unloaded_reset",
        "preparation_relation": "one_excluded_preparation_per_repetition",
        "predecessor_relation": "none",
        "load_cost_policy": "may_be_in_timed_request",
    },
    {
        "state": "warm_run",
        "loaded_before_timing": True,
        "preparation_kind": None,
        "preparation_relation": "none",
        "predecessor_relation": "immediately_prior_measured_run_attesting_loaded_after",
        "load_cost_policy": "model_attested_loaded_before_timing",
    },
    {
        "state": "already_loaded",
        "loaded_before_timing": True,
        "preparation_kind": "external_preload",
        "preparation_relation": "one_excluded_preparation_before_series",
        "predecessor_relation": "excluded_preparation_not_measured_run",
        "load_cost_policy": "preparation_excluded_from_timing",
    },
)
_CLASSIFICATIONS = {
    "completed": "completed",
    "product_timeout": "configured_product_limit_reached",
    "model_failure": "model_failure",
}
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BenchmarkValidationError(ValueError):
    """A deterministic contract or result validation failure."""


@dataclass(frozen=True)
class BenchmarkContract:
    """Validated controls needed to check one v1 benchmark result."""

    version: int
    repetitions_per_state: int
    warm_predecessor_max_gap_seconds: float
    connection_timeout_max_seconds: float
    total_timeout_max_seconds: float
    timeout_observation_overhead_max_seconds: float
    aggregate_decimal_places: int


def _fail(path: str, message: str) -> NoReturn:
    raise BenchmarkValidationError(f"{path}: {message}")


def _object(value: object, path: str, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict:
        _fail(path, "must be an object")
    result = value
    if any(type(key) is not str for key in result):
        _fail(path, "field names must be strings")
    missing = fields - set(result)
    unknown = set(result) - fields
    if missing:
        _fail(path, f"missing field {sorted(missing)[0]}")
    if unknown:
        _fail(path, f"unknown field {sorted(unknown)[0]}")
    return result


def _list(value: object, path: str) -> list[object]:
    if type(value) is not list:
        _fail(path, "must be an array")
    return value


def _string(value: object, path: str, *, maximum: int = 256) -> str:
    if type(value) is not str or not value or value != value.strip() or len(value) > maximum:
        _fail(path, "must be a non-empty bounded string without peripheral whitespace")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _fail(path, "must be strict UTF-8")
    if "://" in value:
        _fail(path, "must not contain an endpoint URL")
    return value


def _opaque_id(value: object, path: str) -> str:
    if type(value) is not str or _OPAQUE_ID.fullmatch(value) is None:
        _fail(path, "must be an opaque non-secret identifier")
    return value


def _sha256(value: object, path: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(path, "must be a lowercase SHA-256 digest")
    return value


def _integer(
    value: object,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        _fail(path, "must be an integer")
    if minimum is not None and value < minimum:
        _fail(path, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        _fail(path, f"must be at most {maximum}")
    return value


def _number(
    value: object,
    path: str,
    *,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if type(value) not in (int, float):
        _fail(path, "must be a number")
    try:
        result = float(value)
    except OverflowError:
        _fail(path, "must be finite")
    if not math.isfinite(result):
        _fail(path, "must be finite")
    if minimum is not None and result < minimum:
        _fail(path, f"must be at least {minimum}")
    if exclusive_minimum is not None and result <= exclusive_minimum:
        _fail(path, f"must be greater than {exclusive_minimum}")
    if maximum is not None and result > maximum:
        _fail(path, f"must be at most {maximum}")
    return result


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        _fail(path, "must be a boolean")
    return value


def _constant(value: object, expected: object, path: str) -> None:
    if type(value) is not type(expected) or value != expected:
        _fail(path, f"must equal {expected!r}")


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkValidationError(f"JSON: duplicate field {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise BenchmarkValidationError(f"JSON: non-standard numeric constant {value}")


def _json_strings_are_strict_utf8(value: object) -> bool:
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                return False
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
    return True


def _load_json(path: str | Path) -> object:
    source = Path(path)
    try:
        with source.open("rb") as handle:
            encoded = handle.read(MAX_ARTIFACT_BYTES + 1)
    except OSError as exc:
        raise BenchmarkValidationError("artifact could not be read") from exc
    if len(encoded) > MAX_ARTIFACT_BYTES:
        raise BenchmarkValidationError("artifact exceeds the size limit")
    try:
        text = encoded.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_constant=_reject_json_constant,
        )
        if not _json_strings_are_strict_utf8(value):
            raise BenchmarkValidationError("artifact contains non-UTF-8 JSON text")
        return value
    except BenchmarkValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BenchmarkValidationError("artifact is not strict JSON") from exc


def parse_benchmark_contract(value: object) -> BenchmarkContract:
    """Validate and parse the candidate-agnostic v1 protocol fixture."""
    contract = _object(value, "$", {
        "artifact",
        "contract_version",
        "scope",
        "repetitions_per_state",
        "state_order",
        "states",
        "warm_predecessor_max_gap_seconds",
        "effective_limit_maxima_seconds",
        "timeout_observation_overhead_max_seconds",
        "required_metadata",
        "reporting",
        "classification",
    })
    _constant(contract["artifact"], CONTRACT_ARTIFACT, "$.artifact")
    _constant(contract["contract_version"], CONTRACT_VERSION, "$.contract_version")
    _constant(
        contract["scope"],
        "candidate-agnostic protocol; contains no benchmark result",
        "$.scope",
    )
    repetitions = _integer(
        contract["repetitions_per_state"],
        "$.repetitions_per_state",
        minimum=3,
        maximum=100,
    )

    state_order = _list(contract["state_order"], "$.state_order")
    if tuple(state_order) != _STATE_ORDER or any(type(item) is not str for item in state_order):
        _fail("$.state_order", f"must equal {list(_STATE_ORDER)!r}")

    states = _list(contract["states"], "$.states")
    if len(states) != len(_STATE_CONTRACTS):
        _fail("$.states", "must define each required state exactly once")
    state_fields = set(_STATE_CONTRACTS[0])
    for index, expected in enumerate(_STATE_CONTRACTS):
        state = _object(states[index], f"$.states[{index}]", state_fields)
        for field, expected_value in expected.items():
            if type(state[field]) is not type(expected_value) or state[field] != expected_value:
                _fail(f"$.states[{index}]", "does not match the v1 state semantics")

    gap = _number(
        contract["warm_predecessor_max_gap_seconds"],
        "$.warm_predecessor_max_gap_seconds",
        exclusive_minimum=0.0,
        maximum=120.0,
    )
    _constant(gap, 120.0, "$.warm_predecessor_max_gap_seconds")

    maxima = _object(
        contract["effective_limit_maxima_seconds"],
        "$.effective_limit_maxima_seconds",
        {"connection_timeout_seconds", "total_timeout_seconds"},
    )
    connection_max = _number(
        maxima["connection_timeout_seconds"],
        "$.effective_limit_maxima_seconds.connection_timeout_seconds",
        exclusive_minimum=0.0,
    )
    total_max = _number(
        maxima["total_timeout_seconds"],
        "$.effective_limit_maxima_seconds.total_timeout_seconds",
        exclusive_minimum=0.0,
    )
    _constant(connection_max, 10.0, "$.effective_limit_maxima_seconds.connection_timeout_seconds")
    _constant(total_max, 120.0, "$.effective_limit_maxima_seconds.total_timeout_seconds")

    timeout_overhead_max = _number(
        contract["timeout_observation_overhead_max_seconds"],
        "$.timeout_observation_overhead_max_seconds",
        exclusive_minimum=0.0,
        maximum=1.0,
    )
    _constant(
        timeout_overhead_max,
        _V1_TIMEOUT_OBSERVATION_OVERHEAD_MAX_SECONDS,
        "$.timeout_observation_overhead_max_seconds",
    )

    required_metadata = _list(contract["required_metadata"], "$.required_metadata")
    if tuple(required_metadata) != _REQUIRED_METADATA or any(
        type(item) is not str for item in required_metadata
    ):
        _fail("$.required_metadata", "must list every v1 pinned metadata field exactly once")

    reporting = _object(contract["reporting"], "$.reporting", {
        "group_by", "metrics", "p95_method", "aggregate_decimal_places",
    })
    _constant(reporting["group_by"], ["state"], "$.reporting.group_by")
    _constant(reporting["metrics"], list(_METRICS), "$.reporting.metrics")
    _constant(reporting["p95_method"], "nearest_rank", "$.reporting.p95_method")
    decimal_places = _integer(
        reporting["aggregate_decimal_places"],
        "$.reporting.aggregate_decimal_places",
        minimum=0,
        maximum=12,
    )
    _constant(decimal_places, 6, "$.reporting.aggregate_decimal_places")

    classification = _object(
        contract["classification"], "$.classification", set(_CLASSIFICATIONS),
    )
    for field, expected_value in _CLASSIFICATIONS.items():
        _constant(
            classification[field], expected_value, f"$.classification.{field}",
        )

    return BenchmarkContract(
        version=CONTRACT_VERSION,
        repetitions_per_state=repetitions,
        warm_predecessor_max_gap_seconds=gap,
        connection_timeout_max_seconds=connection_max,
        total_timeout_max_seconds=total_max,
        timeout_observation_overhead_max_seconds=timeout_overhead_max,
        aggregate_decimal_places=decimal_places,
    )


def load_benchmark_contract(path: str | Path) -> BenchmarkContract:
    """Load a strict JSON protocol fixture without accepting duplicate keys or NaN."""
    return parse_benchmark_contract(_load_json(path))


def _validated_contract(value: object) -> BenchmarkContract:
    if type(value) is not BenchmarkContract:
        _fail("contract", "must be produced by parse_benchmark_contract")
    if (
        value.version != CONTRACT_VERSION
        or not 3 <= value.repetitions_per_state <= 100
        or value.warm_predecessor_max_gap_seconds != 120.0
        or value.connection_timeout_max_seconds != 10.0
        or value.total_timeout_max_seconds != 120.0
        or value.timeout_observation_overhead_max_seconds
        != _V1_TIMEOUT_OBSERVATION_OVERHEAD_MAX_SECONDS
        or value.aggregate_decimal_places != 6
    ):
        _fail("contract", "is not the supported v1 protocol")
    return value


def _validate_metadata(
    value: object, contract: BenchmarkContract,
) -> tuple[dict[str, object], float, float]:
    metadata = _object(value, "$.metadata", {
        "candidate",
        "runtime",
        "prompt_template_sha256",
        "hardware",
        "effective_limits",
        "request_parameters",
    })

    candidate = _object(metadata["candidate"], "$.metadata.candidate", {
        "identifier", "checksum_sha256", "quantization",
    })
    _string(candidate["identifier"], "$.metadata.candidate.identifier")
    _sha256(candidate["checksum_sha256"], "$.metadata.candidate.checksum_sha256")
    _string(candidate["quantization"], "$.metadata.candidate.quantization", maximum=64)

    runtime = _object(metadata["runtime"], "$.metadata.runtime", {
        "name", "version", "instance_id",
    })
    _string(runtime["name"], "$.metadata.runtime.name", maximum=128)
    _string(runtime["version"], "$.metadata.runtime.version", maximum=128)
    _opaque_id(runtime["instance_id"], "$.metadata.runtime.instance_id")

    _sha256(metadata["prompt_template_sha256"], "$.metadata.prompt_template_sha256")

    hardware = _object(metadata["hardware"], "$.metadata.hardware", {
        "host_id",
        "architecture",
        "cpu_model",
        "logical_cpu_count",
        "memory_bytes",
        "accelerator_model",
        "accelerator_count",
        "accelerator_memory_bytes",
    })
    _opaque_id(hardware["host_id"], "$.metadata.hardware.host_id")
    _string(hardware["architecture"], "$.metadata.hardware.architecture", maximum=64)
    _string(hardware["cpu_model"], "$.metadata.hardware.cpu_model")
    _integer(
        hardware["logical_cpu_count"],
        "$.metadata.hardware.logical_cpu_count",
        minimum=1,
    )
    _integer(hardware["memory_bytes"], "$.metadata.hardware.memory_bytes", minimum=1)
    accelerator_model = _string(
        hardware["accelerator_model"], "$.metadata.hardware.accelerator_model",
    )
    accelerator_count = _integer(
        hardware["accelerator_count"],
        "$.metadata.hardware.accelerator_count",
        minimum=0,
    )
    accelerator_memory = _integer(
        hardware["accelerator_memory_bytes"],
        "$.metadata.hardware.accelerator_memory_bytes",
        minimum=0,
    )
    if accelerator_count == 0 and (
        accelerator_model != "none" or accelerator_memory != 0
    ):
        _fail("$.metadata.hardware", "zero accelerators require model 'none' and zero capacity")
    if accelerator_count > 0 and (
        accelerator_model == "none" or accelerator_memory == 0
    ):
        _fail("$.metadata.hardware", "accelerator identity and capacity are inconsistent")

    limits = _object(metadata["effective_limits"], "$.metadata.effective_limits", {
        "connection_timeout_seconds", "total_timeout_seconds",
    })
    connection_limit = _number(
        limits["connection_timeout_seconds"],
        "$.metadata.effective_limits.connection_timeout_seconds",
        exclusive_minimum=0.0,
        maximum=contract.connection_timeout_max_seconds,
    )
    total_limit = _number(
        limits["total_timeout_seconds"],
        "$.metadata.effective_limits.total_timeout_seconds",
        exclusive_minimum=0.0,
        maximum=contract.total_timeout_max_seconds,
    )
    if connection_limit > total_limit:
        _fail("$.metadata.effective_limits", "connection limit exceeds total limit")

    parameters = _object(metadata["request_parameters"], "$.metadata.request_parameters", {
        "max_output_tokens", "temperature", "top_p", "seed", "stream",
    })
    _integer(
        parameters["max_output_tokens"],
        "$.metadata.request_parameters.max_output_tokens",
        minimum=1,
        maximum=1_000_000,
    )
    _number(
        parameters["temperature"],
        "$.metadata.request_parameters.temperature",
        minimum=0.0,
    )
    _number(
        parameters["top_p"],
        "$.metadata.request_parameters.top_p",
        exclusive_minimum=0.0,
        maximum=1.0,
    )
    _integer(
        parameters["seed"],
        "$.metadata.request_parameters.seed",
        minimum=-(2 ** 63),
        maximum=2 ** 63 - 1,
    )
    _constant(parameters["stream"], False, "$.metadata.request_parameters.stream")
    return metadata, connection_limit, total_limit


def benchmark_context_sha256(
    *,
    contract_version: int,
    repetitions_per_state: int,
    metadata: Mapping[str, object],
) -> str:
    """Return the canonical digest every run/preparation must pin.

    Callers should validate the complete result afterward; this helper only makes
    it possible for an external runner to construct the repeated context binding.
    """
    value = {
        "contract_version": contract_version,
        "repetitions_per_state": repetitions_per_state,
        "metadata": metadata,
    }
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise BenchmarkValidationError("benchmark context is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _nullable_opaque_id(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _opaque_id(value, path)


def _nullable_nonnegative_number(value: object, path: str) -> float | None:
    if value is None:
        return None
    return _number(value, path, minimum=0.0)


def _validate_preparations(
    value: object, *, context_sha256: str,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    entries = _list(value, "$.preparations")
    preparations: list[dict[str, object]] = []
    by_id: dict[str, dict[str, object]] = {}
    attestation_ids: set[str] = set()
    previous_sequence = 0
    fields = {
        "preparation_id",
        "kind",
        "sequence",
        "context_sha256",
        "authority",
        "excluded_from_metrics",
        "attestation_id",
        "target_model_loaded",
    }
    for index, raw in enumerate(entries):
        path = f"$.preparations[{index}]"
        item = _object(raw, path, fields)
        preparation_id = _opaque_id(item["preparation_id"], f"{path}.preparation_id")
        if preparation_id in by_id:
            _fail(f"{path}.preparation_id", "duplicates another preparation")
        kind = item["kind"]
        if type(kind) is not str or kind not in {
            "external_unloaded_reset", "external_preload",
        }:
            _fail(f"{path}.kind", "is not a supported external preparation")
        sequence = _integer(item["sequence"], f"{path}.sequence", minimum=1)
        if sequence <= previous_sequence:
            _fail(f"{path}.sequence", "preparations must be listed in sequence order")
        previous_sequence = sequence
        _constant(item["context_sha256"], context_sha256, f"{path}.context_sha256")
        _constant(item["authority"], "external_operator_or_runtime", f"{path}.authority")
        _constant(item["excluded_from_metrics"], True, f"{path}.excluded_from_metrics")
        attestation_id = _opaque_id(item["attestation_id"], f"{path}.attestation_id")
        if attestation_id in attestation_ids:
            _fail(f"{path}.attestation_id", "duplicates another attestation")
        attestation_ids.add(attestation_id)
        loaded = _boolean(item["target_model_loaded"], f"{path}.target_model_loaded")
        expected_loaded = kind == "external_preload"
        if loaded is not expected_loaded:
            _fail(f"{path}.target_model_loaded", "contradicts the preparation kind")
        preparations.append(item)
        by_id[preparation_id] = item
    return preparations, by_id


def _validate_runs(
    value: object,
    *,
    contract: BenchmarkContract,
    context_sha256: str,
    connection_limit: float,
    total_limit: float,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    entries = _list(value, "$.runs")
    expected_count = len(_STATE_ORDER) * contract.repetitions_per_state
    if len(entries) != expected_count:
        _fail("$.runs", f"must contain exactly {expected_count} timed runs")
    runs: list[dict[str, object]] = []
    by_id: dict[str, dict[str, object]] = {}
    repetitions: set[tuple[str, int]] = set()
    previous_sequence = 0
    fields = {
        "run_id",
        "state",
        "repetition",
        "sequence",
        "context_sha256",
        "loaded_before_timing",
        "loaded_after_timing",
        "preparation_id",
        "predecessor_run_id",
        "predecessor_gap_seconds",
        "duration_seconds",
        "timed_out",
        "timeout_kind",
        "classification",
    }
    classifications = set(_CLASSIFICATIONS.values())
    for index, raw in enumerate(entries):
        path = f"$.runs[{index}]"
        item = _object(raw, path, fields)
        run_id = _opaque_id(item["run_id"], f"{path}.run_id")
        if run_id in by_id:
            _fail(f"{path}.run_id", "duplicates another timed run")
        state = item["state"]
        if type(state) is not str or state not in _STATE_ORDER:
            _fail(f"{path}.state", "is not a required benchmark state")
        repetition = _integer(
            item["repetition"],
            f"{path}.repetition",
            minimum=1,
            maximum=contract.repetitions_per_state,
        )
        repetition_key = (state, repetition)
        if repetition_key in repetitions:
            _fail(f"{path}.repetition", "duplicates a state/repetition identity")
        repetitions.add(repetition_key)
        sequence = _integer(item["sequence"], f"{path}.sequence", minimum=1)
        if sequence <= previous_sequence:
            _fail(f"{path}.sequence", "timed runs must be listed in sequence order")
        previous_sequence = sequence
        _constant(item["context_sha256"], context_sha256, f"{path}.context_sha256")
        _boolean(item["loaded_before_timing"], f"{path}.loaded_before_timing")
        loaded_after = _boolean(item["loaded_after_timing"], f"{path}.loaded_after_timing")
        _nullable_opaque_id(item["preparation_id"], f"{path}.preparation_id")
        _nullable_opaque_id(item["predecessor_run_id"], f"{path}.predecessor_run_id")
        _nullable_nonnegative_number(
            item["predecessor_gap_seconds"], f"{path}.predecessor_gap_seconds",
        )
        duration = _number(
            item["duration_seconds"],
            f"{path}.duration_seconds",
            minimum=0.0,
        )
        timed_out = _boolean(item["timed_out"], f"{path}.timed_out")
        timeout_kind = item["timeout_kind"]
        if timeout_kind is not None and (
            type(timeout_kind) is not str
            or timeout_kind not in {"connection", "total"}
        ):
            _fail(
                f"{path}.timeout_kind",
                "must be one of 'connection', 'total', or null",
            )
        classification = item["classification"]
        if type(classification) is not str or classification not in classifications:
            _fail(f"{path}.classification", "is not a supported outcome classification")
        if timed_out and classification != _CLASSIFICATIONS["product_timeout"]:
            _fail(
                f"{path}.classification",
                "a product timeout must be configured_product_limit_reached",
            )
        if not timed_out and classification == _CLASSIFICATIONS["product_timeout"]:
            _fail(f"{path}.classification", "claims a product timeout that did not occur")
        if timed_out and timeout_kind is None:
            _fail(f"{path}.timeout_kind", "a product timeout must identify its deadline")
        if not timed_out and timeout_kind is not None:
            _fail(f"{path}.timeout_kind", "must be null when timed_out is false")
        if timed_out:
            timeout_budget = {
                "connection": connection_limit,
                "total": total_limit,
            }[timeout_kind]
            if duration < timeout_budget:
                _fail(
                    f"{path}.duration_seconds",
                    f"must reach the configured {timeout_kind} timeout budget",
                )
            if duration > (
                timeout_budget
                + contract.timeout_observation_overhead_max_seconds
            ):
                _fail(
                    f"{path}.duration_seconds",
                    f"exceeds the bounded {timeout_kind} timeout observation window",
                )
        elif duration > total_limit:
            _fail(
                f"{path}.duration_seconds",
                "must not exceed the configured total timeout budget",
            )
        if classification == _CLASSIFICATIONS["model_failure"] and timed_out:
            _fail(f"{path}.classification", "a product timeout is not a model failure")
        if classification == _CLASSIFICATIONS["completed"] and not loaded_after:
            _fail(f"{path}.loaded_after_timing", "a completed run must attest the model loaded")
        runs.append(item)
        by_id[run_id] = item

    expected_repetitions = {
        (state, repetition)
        for state in _STATE_ORDER
        for repetition in range(1, contract.repetitions_per_state + 1)
    }
    if repetitions != expected_repetitions:
        _fail("$.runs", "does not provide exact state/repetition coverage")
    return runs, by_id


def _validate_sequence(
    preparations: list[dict[str, object]],
    preparation_by_id: dict[str, dict[str, object]],
    runs: list[dict[str, object]],
    run_by_id: dict[str, dict[str, object]],
    contract: BenchmarkContract,
) -> None:
    events: dict[int, tuple[str, dict[str, object]]] = {}
    for kind, items in (("preparation", preparations), ("run", runs)):
        for item in items:
            sequence = item["sequence"]
            if sequence in events:
                _fail(f"$.{kind}s", f"sequence {sequence} duplicates another event")
            events[sequence] = (kind, item)
    expected_event_count = 4 * contract.repetitions_per_state + 1
    if set(events) != set(range(1, expected_event_count + 1)):
        _fail("$", "event sequence must be contiguous and complete")

    cursor = 1
    previous_measured_run: dict[str, object] | None = None
    used_preparations: set[str] = set()
    for repetition in range(1, contract.repetitions_per_state + 1):
        prep_kind, preparation = events[cursor]
        run_kind, run = events[cursor + 1]
        if prep_kind != "preparation" or preparation["kind"] != "external_unloaded_reset":
            _fail("$", f"cold_start repetition {repetition} lacks its external unloaded reset")
        if run_kind != "run" or (run["state"], run["repetition"]) != (
            "cold_start", repetition,
        ):
            _fail("$", f"cold_start repetition {repetition} is out of sequence")
        preparation_id = preparation["preparation_id"]
        if run["preparation_id"] != preparation_id:
            _fail("$", f"cold_start repetition {repetition} is not linked to its reset")
        if run["loaded_before_timing"] is not False:
            _fail("$", f"cold_start repetition {repetition} must attest unloaded-before")
        if run["predecessor_run_id"] is not None or run["predecessor_gap_seconds"] is not None:
            _fail("$", "cold_start cannot claim a measured predecessor")
        used_preparations.add(preparation_id)
        previous_measured_run = run
        cursor += 2

    for repetition in range(1, contract.repetitions_per_state + 1):
        kind, run = events[cursor]
        if kind != "run" or (run["state"], run["repetition"]) != ("warm_run", repetition):
            _fail("$", f"warm_run repetition {repetition} is out of sequence")
        if run["loaded_before_timing"] is not True:
            _fail("$", f"warm_run repetition {repetition} must attest loaded-before")
        if run["preparation_id"] is not None:
            _fail("$", "warm_run must not use an excluded preparation as its predecessor")
        if previous_measured_run is None or (
            run["predecessor_run_id"] != previous_measured_run["run_id"]
        ):
            _fail("$", "warm_run must link the immediately prior measured run")
        predecessor = run_by_id.get(run["predecessor_run_id"])
        if predecessor is None:
            _fail("$", "warm_run predecessor does not exist")
        if predecessor["loaded_after_timing"] is not True:
            _fail("$", "warm_run predecessor must attest loaded-after")
        gap = run["predecessor_gap_seconds"]
        if type(gap) not in (int, float) or not math.isfinite(float(gap)) or (
            float(gap) < 0.0
            or float(gap) > contract.warm_predecessor_max_gap_seconds
        ):
            _fail("$", "warm_run predecessor gap is missing or exceeds the bound")
        previous_measured_run = run
        cursor += 1

    prep_kind, preload = events[cursor]
    if prep_kind != "preparation" or preload["kind"] != "external_preload":
        _fail("$", "already_loaded series lacks its excluded external preload")
    preload_id = preload["preparation_id"]
    used_preparations.add(preload_id)
    cursor += 1
    previous_loaded_run: dict[str, object] | None = None
    for repetition in range(1, contract.repetitions_per_state + 1):
        kind, run = events[cursor]
        if kind != "run" or (run["state"], run["repetition"]) != (
            "already_loaded", repetition,
        ):
            _fail("$", f"already_loaded repetition {repetition} is out of sequence")
        if run["loaded_before_timing"] is not True:
            _fail("$", f"already_loaded repetition {repetition} must attest loaded-before")
        if run["preparation_id"] != preload_id:
            _fail("$", "every already_loaded run must identify the series preload")
        if run["predecessor_run_id"] is not None or run["predecessor_gap_seconds"] is not None:
            _fail("$", "already_loaded predecessor must be the excluded preparation")
        if (
            previous_loaded_run is not None
            and previous_loaded_run["loaded_after_timing"] is not True
        ):
            _fail(
                "$",
                "already_loaded series cannot regain loaded state without a new preparation",
            )
        previous_loaded_run = run
        cursor += 1

    if used_preparations != set(preparation_by_id):
        _fail("$.preparations", "contains an unreferenced or wrong-kind preparation")


def _computed_metrics(
    runs: list[dict[str, object]], contract: BenchmarkContract,
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    places = contract.aggregate_decimal_places
    for state in _STATE_ORDER:
        state_runs = [run for run in runs if run["state"] == state]
        durations = sorted(float(run["duration_seconds"]) for run in state_runs)
        nearest_rank = math.ceil(0.95 * len(durations)) - 1
        result[state] = {
            "timeout_rate": round(
                sum(run["timed_out"] is True for run in state_runs) / len(state_runs),
                places,
            ),
            "median_seconds": round(statistics.median(durations), places),
            "p95_seconds": round(durations[nearest_rank], places),
        }
    return result


def _validate_reported_metrics(
    value: object, computed: dict[str, dict[str, float]],
) -> None:
    reported = _object(value, "$.reported_metrics", set(_STATE_ORDER))
    for state in _STATE_ORDER:
        metrics = _object(reported[state], f"$.reported_metrics.{state}", set(_METRICS))
        for metric in _METRICS:
            maximum = 1.0 if metric == "timeout_rate" else None
            numeric = _number(
                metrics[metric],
                f"$.reported_metrics.{state}.{metric}",
                minimum=0.0,
                maximum=maximum,
            )
            if numeric != computed[state][metric]:
                _fail(
                    f"$.reported_metrics.{state}.{metric}",
                    "does not equal the aggregate computed from raw runs",
                )


def validate_benchmark_record(
    value: object, contract: BenchmarkContract,
) -> dict[str, dict[str, float]]:
    """Validate a complete future FOUNDRY-35 result and return computed metrics.

    The function is pure: it reads no environment, endpoint, daemon, or model.  A
    result is accepted only when its raw bounded runs, state attestations, external
    preparations, measured predecessor chain, pinned context, and reported metrics
    all satisfy the supplied versioned contract.
    """
    contract = _validated_contract(contract)
    record = _object(value, "$", {
        "artifact",
        "contract_version",
        "repetitions_per_state",
        "metadata",
        "preparations",
        "runs",
        "reported_metrics",
    })
    _constant(record["artifact"], RESULT_ARTIFACT, "$.artifact")
    _constant(record["contract_version"], contract.version, "$.contract_version")
    _constant(
        record["repetitions_per_state"],
        contract.repetitions_per_state,
        "$.repetitions_per_state",
    )
    metadata, connection_limit, total_limit = _validate_metadata(
        record["metadata"], contract,
    )
    context_sha256 = benchmark_context_sha256(
        contract_version=contract.version,
        repetitions_per_state=contract.repetitions_per_state,
        metadata=metadata,
    )
    preparations, preparation_by_id = _validate_preparations(
        record["preparations"], context_sha256=context_sha256,
    )
    runs, run_by_id = _validate_runs(
        record["runs"],
        contract=contract,
        context_sha256=context_sha256,
        connection_limit=connection_limit,
        total_limit=total_limit,
    )
    _validate_sequence(
        preparations,
        preparation_by_id,
        runs,
        run_by_id,
        contract,
    )
    computed = _computed_metrics(runs, contract)
    _validate_reported_metrics(record["reported_metrics"], computed)
    return computed


def load_benchmark_record(
    path: str | Path, contract: BenchmarkContract,
) -> dict[str, dict[str, float]]:
    """Load and validate one strict JSON benchmark result artifact."""
    return validate_benchmark_record(_load_json(path), contract)
