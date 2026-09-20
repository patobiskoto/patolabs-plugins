"""Pure contract tests for future local benchmark evidence.

All timings and identities in this file are synthetic validation inputs.  They are
not shipped benchmark results and make no performance claim about any candidate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry import local_benchmark


CONTRACT_FIXTURE = (
    Path(__file__).parent / "fixtures" / "local-scout-benchmark-contract.json"
)


def _contract():
    return local_benchmark.load_benchmark_contract(CONTRACT_FIXTURE)


def _synthetic_record():
    metadata = {
        "candidate": {
            "identifier": "synthetic-candidate",
            "checksum_sha256": "a" * 64,
            "quantization": "synthetic-q4",
        },
        "runtime": {
            "name": "synthetic-runtime",
            "version": "0.0-test",
            "instance_id": "runtime-test-001",
        },
        "prompt_template_sha256": "b" * 64,
        "hardware": {
            "host_id": "host-test-001",
            "architecture": "synthetic-arch",
            "cpu_model": "Synthetic CPU",
            "logical_cpu_count": 8,
            "memory_bytes": 16_000_000_000,
            "accelerator_model": "Synthetic Accelerator",
            "accelerator_count": 1,
            "accelerator_memory_bytes": 8_000_000_000,
        },
        "effective_limits": {
            "connection_timeout_seconds": 6.0,
            "total_timeout_seconds": 30.0,
        },
        "request_parameters": {
            "max_output_tokens": 256,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 42,
            "stream": False,
        },
    }
    context = local_benchmark.benchmark_context_sha256(
        contract_version=1,
        repetitions_per_state=3,
        metadata=metadata,
    )

    def preparation(identifier, kind, sequence, loaded):
        return {
            "preparation_id": identifier,
            "kind": kind,
            "sequence": sequence,
            "context_sha256": context,
            "authority": "external_operator_or_runtime",
            "excluded_from_metrics": True,
            "attestation_id": f"attestation-{identifier}",
            "target_model_loaded": loaded,
        }

    def run(
        identifier,
        state,
        repetition,
        sequence,
        duration,
        *,
        loaded_before,
        loaded_after,
        preparation_id=None,
        predecessor_run_id=None,
        predecessor_gap_seconds=None,
        timed_out=False,
        timeout_kind=None,
        classification="completed",
    ):
        return {
            "run_id": identifier,
            "state": state,
            "repetition": repetition,
            "sequence": sequence,
            "context_sha256": context,
            "loaded_before_timing": loaded_before,
            "loaded_after_timing": loaded_after,
            "preparation_id": preparation_id,
            "predecessor_run_id": predecessor_run_id,
            "predecessor_gap_seconds": predecessor_gap_seconds,
            "duration_seconds": duration,
            "timed_out": timed_out,
            "timeout_kind": timeout_kind,
            "classification": classification,
        }

    preparations = [
        preparation("reset-1", "external_unloaded_reset", 1, False),
        preparation("reset-2", "external_unloaded_reset", 3, False),
        preparation("reset-3", "external_unloaded_reset", 5, False),
        preparation("preload-series", "external_preload", 10, True),
    ]
    runs = [
        run(
            "cold-1", "cold_start", 1, 2, 6.125,
            loaded_before=False,
            loaded_after=False,
            preparation_id="reset-1",
            timed_out=True,
            timeout_kind="connection",
            classification="configured_product_limit_reached",
        ),
        run(
            "cold-2", "cold_start", 2, 4, 5.0,
            loaded_before=False,
            loaded_after=False,
            preparation_id="reset-2",
            classification="model_failure",
        ),
        run(
            "cold-3", "cold_start", 3, 6, 4.0,
            loaded_before=False,
            loaded_after=True,
            preparation_id="reset-3",
        ),
        run(
            "warm-1", "warm_run", 1, 7, 2.0,
            loaded_before=True,
            loaded_after=True,
            predecessor_run_id="cold-3",
            predecessor_gap_seconds=0.25,
        ),
        run(
            "warm-2", "warm_run", 2, 8, 1.8,
            loaded_before=True,
            loaded_after=True,
            predecessor_run_id="warm-1",
            predecessor_gap_seconds=0.2,
        ),
        run(
            "warm-3", "warm_run", 3, 9, 30.125,
            loaded_before=True,
            loaded_after=True,
            predecessor_run_id="warm-2",
            predecessor_gap_seconds=0.2,
            timed_out=True,
            timeout_kind="total",
            classification="configured_product_limit_reached",
        ),
        run(
            "loaded-1", "already_loaded", 1, 11, 1.0,
            loaded_before=True,
            loaded_after=True,
            preparation_id="preload-series",
        ),
        run(
            "loaded-2", "already_loaded", 2, 12, 1.2,
            loaded_before=True,
            loaded_after=True,
            preparation_id="preload-series",
        ),
        run(
            "loaded-3", "already_loaded", 3, 13, 1.1,
            loaded_before=True,
            loaded_after=False,
            preparation_id="preload-series",
            classification="model_failure",
        ),
    ]
    return {
        "artifact": "foundry.local_scout.benchmark_result",
        "contract_version": 1,
        "repetitions_per_state": 3,
        "metadata": metadata,
        "preparations": preparations,
        "runs": runs,
        "reported_metrics": {
            "cold_start": {
                "timeout_rate": 0.333333,
                "median_seconds": 5.0,
                "p95_seconds": 6.125,
            },
            "warm_run": {
                "timeout_rate": 0.333333,
                "median_seconds": 2.0,
                "p95_seconds": 30.125,
            },
            "already_loaded": {
                "timeout_rate": 0.0,
                "median_seconds": 1.1,
                "p95_seconds": 1.2,
            },
        },
    }


def _assert_rejected(record, match=None):
    with pytest.raises(local_benchmark.BenchmarkValidationError, match=match):
        local_benchmark.validate_benchmark_record(record, _contract())


def _move_warm_timeout(record, target_position):
    """Move the synthetic warm timeout without changing its raw outcome multiset."""
    source = record["runs"][5]
    target = record["runs"][3 + target_position]
    for field in (
        "duration_seconds",
        "timed_out",
        "timeout_kind",
        "classification",
    ):
        source[field], target[field] = target[field], source[field]


def test_versioned_fixture_parses_to_the_enforced_v1_protocol():
    raw = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    contract = local_benchmark.parse_benchmark_contract(raw)

    assert contract == local_benchmark.BenchmarkContract(
        version=1,
        repetitions_per_state=3,
        warm_predecessor_max_gap_seconds=120.0,
        connection_timeout_max_seconds=10.0,
        total_timeout_max_seconds=120.0,
        timeout_observation_overhead_max_seconds=0.25,
        aggregate_decimal_places=6,
    )
    assert raw["state_order"] == ["cold_start", "warm_run", "already_loaded"]
    assert raw["states"][0]["preparation_relation"] == (
        "one_excluded_preparation_per_repetition"
    )
    assert raw["states"][1]["predecessor_relation"] == (
        "immediately_prior_measured_run_attesting_loaded_after"
    )
    assert raw["states"][2]["preparation_relation"] == (
        "one_excluded_preparation_before_series"
    )


def test_conforming_synthetic_record_returns_metrics_computed_from_raw_runs():
    assert local_benchmark.validate_benchmark_record(
        _synthetic_record(), _contract(),
    ) == {
        "cold_start": {
            "timeout_rate": 0.333333,
            "median_seconds": 5.0,
            "p95_seconds": 6.125,
        },
        "warm_run": {
            "timeout_rate": 0.333333,
            "median_seconds": 2.0,
            "p95_seconds": 30.125,
        },
        "already_loaded": {
            "timeout_rate": 0.0,
            "median_seconds": 1.1,
            "p95_seconds": 1.2,
        },
    }


def test_strict_result_loader_accepts_the_synthetic_record(tmp_path):
    path = tmp_path / "synthetic-result-not-a-benchmark.json"
    path.write_text(json.dumps(_synthetic_record()), encoding="utf-8")

    assert local_benchmark.load_benchmark_record(path, _contract())["warm_run"] == {
        "timeout_rate": 0.333333,
        "median_seconds": 2.0,
        "p95_seconds": 30.125,
    }


@pytest.mark.parametrize("field", [
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
])
def test_each_pinned_metadata_field_is_mandatory(field):
    record = _synthetic_record()
    parts = field.split(".")
    target = record["metadata"]
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]

    _assert_rejected(record, "missing field")


@pytest.mark.parametrize(("section", "field"), [
    ("metadata", "endpoint_url"),
    ("metadata", "raw_prompt"),
    ("candidate", "token"),
    ("runtime", "daemon_command"),
    ("request_parameters", "provider_extension"),
])
def test_unknown_endpoint_prompt_secret_or_provider_fields_are_rejected(section, field):
    record = _synthetic_record()
    target = record["metadata"] if section == "metadata" else record["metadata"][section]
    target[field] = "forbidden-extra-field"

    _assert_rejected(record, "unknown field")


@pytest.mark.parametrize(("field", "value"), [
    ("checksum_sha256", "not-a-checksum"),
    ("identifier", "https://runtime.invalid/model"),
    ("quantization", " q4"),
])
def test_candidate_metadata_values_are_strict(field, value):
    record = _synthetic_record()
    record["metadata"]["candidate"][field] = value

    _assert_rejected(record)


def test_hardware_identity_and_capacity_must_be_consistent():
    record = _synthetic_record()
    record["metadata"]["hardware"]["accelerator_count"] = 0

    _assert_rejected(record, "zero accelerators")


@pytest.mark.parametrize(("field", "value"), [
    ("connection_timeout_seconds", 31.0),
    ("total_timeout_seconds", 121.0),
    ("total_timeout_seconds", float("nan")),
    ("total_timeout_seconds", True),
])
def test_effective_limits_are_finite_typed_and_bounded(field, value):
    record = _synthetic_record()
    record["metadata"]["effective_limits"][field] = value

    _assert_rejected(record)


@pytest.mark.parametrize(("field", "value"), [
    ("max_output_tokens", 0),
    ("temperature", float("inf")),
    ("top_p", 1.1),
    ("seed", "42"),
    ("stream", True),
])
def test_request_and_generation_parameters_are_strict_and_pinned(field, value):
    record = _synthetic_record()
    record["metadata"]["request_parameters"][field] = value

    _assert_rejected(record)


def test_top_level_missing_unknown_and_wrong_typed_fields_are_rejected():
    missing = _synthetic_record()
    del missing["reported_metrics"]
    _assert_rejected(missing, "missing field")

    unknown = _synthetic_record()
    unknown["decorative_label"] = "trust-me"
    _assert_rejected(unknown, "unknown field")

    wrong_type = _synthetic_record()
    wrong_type["runs"][0]["duration_seconds"] = "30"
    _assert_rejected(wrong_type, "must be a number")


def test_duplicate_run_and_repetition_identities_are_rejected():
    duplicate_run = _synthetic_record()
    duplicate_run["runs"][1]["run_id"] = duplicate_run["runs"][0]["run_id"]
    _assert_rejected(duplicate_run, "duplicates another timed run")

    duplicate_repetition = _synthetic_record()
    duplicate_repetition["runs"][1]["repetition"] = 1
    _assert_rejected(duplicate_repetition, "duplicates a state/repetition identity")


def test_incomplete_coverage_and_arbitrary_state_labels_are_rejected():
    incomplete = _synthetic_record()
    incomplete["runs"].pop()
    _assert_rejected(incomplete, "exactly 9 timed runs")

    arbitrary = _synthetic_record()
    arbitrary["runs"][0]["state"] = "looks_cold"
    _assert_rejected(arbitrary, "not a required benchmark state")


def test_every_cold_repetition_needs_a_new_linked_unloaded_reset():
    loaded_before = _synthetic_record()
    loaded_before["runs"][0]["loaded_before_timing"] = True
    _assert_rejected(loaded_before, "must attest unloaded-before")

    wrong_reset = _synthetic_record()
    wrong_reset["runs"][0]["preparation_id"] = "reset-2"
    _assert_rejected(wrong_reset, "not linked to its reset")

    false_attestation = _synthetic_record()
    false_attestation["preparations"][0]["target_model_loaded"] = True
    _assert_rejected(false_attestation, "contradicts the preparation kind")


def test_warm_runs_require_the_immediate_loaded_measured_predecessor():
    unlinked = _synthetic_record()
    unlinked["runs"][3]["predecessor_run_id"] = "cold-2"
    _assert_rejected(unlinked, "immediately prior measured run")

    excluded_preparation = _synthetic_record()
    excluded_preparation["runs"][3]["preparation_id"] = "preload-series"
    _assert_rejected(excluded_preparation, "must not use an excluded preparation")

    failed_predecessor = _synthetic_record()
    failed_predecessor["runs"][2]["classification"] = "model_failure"
    failed_predecessor["runs"][2]["loaded_after_timing"] = False
    _assert_rejected(failed_predecessor, "must attest loaded-after")

    missing_gap = _synthetic_record()
    missing_gap["runs"][3]["predecessor_gap_seconds"] = None
    _assert_rejected(missing_gap, "gap is missing")

    stale_predecessor = _synthetic_record()
    stale_predecessor["runs"][3]["predecessor_gap_seconds"] = 120.1
    _assert_rejected(stale_predecessor, "exceeds the bound")


@pytest.mark.parametrize(
    "timeout_position", [
        pytest.param(0, id="first"),
        pytest.param(1, id="middle"),
        pytest.param(2, id="last"),
    ],
)
def test_loaded_warm_timeout_is_position_independent(timeout_position):
    baseline = _synthetic_record()
    expected = local_benchmark.validate_benchmark_record(baseline, _contract())
    record = _synthetic_record()
    _move_warm_timeout(record, timeout_position)

    assert record["runs"][3 + timeout_position]["timeout_kind"] == "total"
    assert local_benchmark.validate_benchmark_record(record, _contract()) == expected


@pytest.mark.parametrize("timeout_position", [0, 1], ids=["first", "middle"])
def test_unloaded_warm_timeout_cannot_precede_another_warm_run(timeout_position):
    record = _synthetic_record()
    _move_warm_timeout(record, timeout_position)
    record["runs"][3 + timeout_position]["loaded_after_timing"] = False

    _assert_rejected(record, "must attest loaded-after")


def test_already_loaded_series_requires_one_excluded_preload_identity():
    not_loaded = _synthetic_record()
    not_loaded["runs"][6]["loaded_before_timing"] = False
    _assert_rejected(not_loaded, "must attest loaded-before")

    wrong_preload = _synthetic_record()
    wrong_preload["runs"][7]["preparation_id"] = "reset-3"
    _assert_rejected(wrong_preload, "identify the series preload")

    measured_predecessor = _synthetic_record()
    measured_predecessor["runs"][6]["predecessor_run_id"] = "warm-3"
    measured_predecessor["runs"][6]["predecessor_gap_seconds"] = 0.1
    _assert_rejected(measured_predecessor, "excluded preparation")

    impossible_reload = _synthetic_record()
    impossible_reload["runs"][6]["classification"] = "model_failure"
    impossible_reload["runs"][6]["loaded_after_timing"] = False
    _assert_rejected(impossible_reload, "cannot regain loaded state")


@pytest.mark.parametrize(("field", "value"), [
    ("authority", "foundry"),
    ("excluded_from_metrics", False),
])
def test_state_preparation_is_external_declarative_and_excluded(field, value):
    record = _synthetic_record()
    record["preparations"][0][field] = value

    _assert_rejected(record)


def test_event_sequence_must_be_unique_contiguous_and_protocol_ordered():
    duplicate = _synthetic_record()
    duplicate["runs"][0]["sequence"] = 1
    _assert_rejected(duplicate, "duplicates another event")

    reordered = _synthetic_record()
    reordered["runs"][-1]["sequence"] = 14
    _assert_rejected(reordered, "contiguous and complete")


def test_every_event_is_bound_to_one_pinned_metadata_context():
    changed_metadata = _synthetic_record()
    changed_metadata["metadata"]["runtime"]["version"] = "different-version"
    _assert_rejected(changed_metadata, "context_sha256")

    changed_run_context = _synthetic_record()
    changed_run_context["runs"][0]["context_sha256"] = "c" * 64
    _assert_rejected(changed_run_context, "context_sha256")

    changed_preparation_context = _synthetic_record()
    changed_preparation_context["preparations"][0]["context_sha256"] = "c" * 64
    _assert_rejected(changed_preparation_context, "context_sha256")


@pytest.mark.parametrize(
    "duration", [
        float("nan"),
        float("inf"),
        -0.001,
        6.251,
        pytest.param(10 ** 10_000, id="overflowing-int"),
    ],
)
def test_raw_durations_must_be_finite_nonnegative_and_bounded(duration):
    record = _synthetic_record()
    record["runs"][0]["duration_seconds"] = duration

    _assert_rejected(record)


def test_timeout_and_classification_cannot_contradict_each_other():
    false_timeout = _synthetic_record()
    false_timeout["runs"][0]["timed_out"] = False
    _assert_rejected(false_timeout, "claims a product timeout")

    false_classification = _synthetic_record()
    false_classification["runs"][6]["classification"] = (
        "configured_product_limit_reached"
    )
    _assert_rejected(false_classification, "did not occur")


def test_connection_and_total_timeouts_accept_bounded_observation_overhead():
    record = _synthetic_record()

    assert record["runs"][0]["duration_seconds"] == 6.125
    assert record["runs"][0]["timeout_kind"] == "connection"
    assert record["runs"][5]["duration_seconds"] == 30.125
    assert record["runs"][5]["timeout_kind"] == "total"
    local_benchmark.validate_benchmark_record(record, _contract())

    result_chosen_tolerance = _synthetic_record()
    result_chosen_tolerance["timeout_observation_overhead_max_seconds"] = 10.0
    _assert_rejected(result_chosen_tolerance, "unknown field")


@pytest.mark.parametrize(
    ("run_index", "duration", "timeout_kind", "message"), [
        (0, 0.0, "connection", "must reach the configured connection"),
        (0, 5.999, "connection", "must reach the configured connection"),
        (0, 30.0, "connection", "bounded connection timeout observation"),
        (5, 6.0, "total", "must reach the configured total"),
        (5, 30.251, "total", "bounded total timeout observation"),
    ],
)
def test_timeout_kind_and_duration_must_match_its_configured_budget(
    run_index, duration, timeout_kind, message,
):
    record = _synthetic_record()
    record["runs"][run_index]["duration_seconds"] = duration
    record["runs"][run_index]["timeout_kind"] = timeout_kind

    _assert_rejected(record, message)


def test_timeout_kind_is_strict_and_required_only_for_product_timeouts():
    missing = _synthetic_record()
    del missing["runs"][0]["timeout_kind"]
    _assert_rejected(missing, "missing field timeout_kind")

    null_timeout = _synthetic_record()
    null_timeout["runs"][0]["timeout_kind"] = None
    _assert_rejected(null_timeout, "must identify its deadline")

    wrong_kind = _synthetic_record()
    wrong_kind["runs"][0]["timeout_kind"] = "request"
    _assert_rejected(wrong_kind, "must be one of")

    kind_without_timeout = _synthetic_record()
    kind_without_timeout["runs"][1]["timeout_kind"] = "connection"
    _assert_rejected(kind_without_timeout, "must be null when timed_out is false")


def test_configured_product_timeout_can_never_be_labeled_model_failure():
    record = _synthetic_record()
    record["runs"][0]["classification"] = "model_failure"

    _assert_rejected(record, "must be configured_product_limit_reached")


@pytest.mark.parametrize(("state", "metric"), [
    ("cold_start", "timeout_rate"),
    ("warm_run", "median_seconds"),
    ("already_loaded", "p95_seconds"),
])
def test_reported_metrics_cannot_self_certify_arbitrary_values(state, metric):
    record = _synthetic_record()
    record["reported_metrics"][state][metric] += 0.01

    _assert_rejected(record, "does not equal the aggregate computed from raw runs")


def test_each_state_and_metric_must_be_reported_exactly_once():
    missing_state = _synthetic_record()
    del missing_state["reported_metrics"]["warm_run"]
    _assert_rejected(missing_state, "missing field")

    unknown_metric = _synthetic_record()
    unknown_metric["reported_metrics"]["cold_start"]["average_seconds"] = 1.0
    _assert_rejected(unknown_metric, "unknown field")


def test_contract_parser_rejects_decorative_or_weakened_state_semantics():
    raw = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    raw["states"][1]["predecessor_relation"] = "some_prior_request"
    with pytest.raises(local_benchmark.BenchmarkValidationError, match="state semantics"):
        local_benchmark.parse_benchmark_contract(raw)

    raw = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    raw["repetitions_per_state"] = 2
    with pytest.raises(local_benchmark.BenchmarkValidationError, match="at least 3"):
        local_benchmark.parse_benchmark_contract(raw)

    raw = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    raw["states"][0]["loaded_before_timing"] = 0
    with pytest.raises(local_benchmark.BenchmarkValidationError, match="state semantics"):
        local_benchmark.parse_benchmark_contract(raw)

    raw = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    raw["timeout_observation_overhead_max_seconds"] = 0.5
    with pytest.raises(local_benchmark.BenchmarkValidationError, match="must equal 0.25"):
        local_benchmark.parse_benchmark_contract(raw)


def test_contract_value_controls_exact_coverage_within_the_supported_range():
    raw = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    raw["repetitions_per_state"] = 4

    assert local_benchmark.parse_benchmark_contract(raw).repetitions_per_state == 4

    raw["repetitions_per_state"] = 101
    with pytest.raises(local_benchmark.BenchmarkValidationError, match="at most 100"):
        local_benchmark.parse_benchmark_contract(raw)


@pytest.mark.parametrize("body", [
    '{"artifact":"one","artifact":"two"}',
    '{"contract_version":NaN}',
    '{"scope":"\\ud800"}',
])
def test_strict_json_loader_rejects_duplicate_keys_and_nonstandard_numbers(body, tmp_path):
    path = tmp_path / "invalid-contract.json"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(local_benchmark.BenchmarkValidationError):
        local_benchmark.load_benchmark_contract(path)
