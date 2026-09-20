"""Offline fail-closed checks for the additive FOUNDRY-46 pilot evidence."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from foundry import measurement_harness as f43


ROOT = Path(__file__).parents[1]
PILOT_ROOT = ROOT / "benchmarks" / "foundry-46"


def _load_compiler():
    path = PILOT_ROOT / "compile-pilot-v1.py"
    spec = importlib.util.spec_from_file_location("foundry46_pilot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot = _load_compiler()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trace_row(
    host: str,
    profile_id: str,
    case_id: str,
    revision: str,
    repetition: int,
) -> dict[str, object]:
    model, effort, version, _role = f43.PROFILE_BINDINGS[host][profile_id]
    metrics = {
        "input_tokens": {"value": 100, "provenance": "host_reported"},
        "output_tokens": {"value": 10, "provenance": "host_reported"},
        "cached_input_tokens": {"value": 20, "provenance": "host_reported"},
        "cache_write_input_tokens": {"value": None, "provenance": "unavailable"},
        "reasoning_output_tokens": (
            {"value": 3, "provenance": "host_reported"}
            if host == "codex"
            else {"value": None, "provenance": "unavailable"}
        ),
        "estimated_cost_usd": {"value": None, "provenance": "unavailable"},
        "duration_seconds": {"value": 0.5, "provenance": "client_observed"},
        "test_outcome": {"value": None, "provenance": "unavailable"},
        "review_outcome": (
            {"value": "approved", "provenance": "host_reported"}
            if host == "claude"
            else {"value": None, "provenance": "unavailable"}
        ),
    }
    binding = {
        "source": f"{host}_resolved",
        "model": model,
        "effort": effort,
        "host_version": version,
        "host_override_active": False,
        "result_sha256": "b" * 64,
        "route_sha256" if host == "claude" else "descriptor_sha256": "a" * 64,
    }
    if host == "codex":
        binding["event_summary"] = {
            "terminal_event": "turn.completed",
            "event_count": 3,
            "usage_event_count": 1,
        }
    return {
        "artifact": f43.ARTIFACT,
        "version": f43.SCHEMA_VERSION,
        "f41_manifest_sha256": f43.F41_MANIFEST_SHA256,
        "host": host,
        "profile_id": profile_id,
        "case_id": case_id,
        "revision": revision,
        "repetition": repetition,
        "binding": binding,
        "terminal": {"status": "completed", "failure_class": "none"},
        "metrics": metrics,
        "cache_state": {"value": "hit", "provenance": "host_reported"},
    }


def _write_fixture(root: Path) -> dict[str, object]:
    root.mkdir(exist_ok=True)
    (root / "evidence-v1").mkdir()
    protocol = json.loads((PILOT_ROOT / pilot.PROTOCOL_FILE).read_text())
    (root / pilot.PROTOCOL_FILE).write_text(
        json.dumps(protocol, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    trace_meta = {}
    for host, relative in pilot.TRACE_FILES.items():
        rows = [
            _trace_row(host, profile, case_id, revision, repetition)
            for profile in pilot.PROFILES[host]
            for case_id, revision, repetition in sorted(pilot._corpus_keys())
        ]
        raw = b"".join(_canonical(row) + b"\n" for row in rows)
        path = root / relative
        path.write_bytes(raw)
        assert f43.reproduce_jsonl(raw, host=host)[0] == rows
        trace_meta[host] = {
            "file": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "rows": len(rows),
        }
    context_slots = []
    for case_id, revision, repetition in sorted(pilot._corpus_keys()):
        digest = hashlib.sha256(f"{case_id}:{revision}".encode()).hexdigest()
        context_slots.append({
            "case_id": case_id,
            "revision": revision,
            "repetition": repetition,
            "policy": "baseline-v1",
            "evidence_count": 2,
            "source_counts": {
                source: 2 if source == "diff" else 0 for source in pilot.SOURCES
            },
            "metrics": {
                "collected_bytes": {"value": 1000, "provenance": "client_observed"},
                "sent_bytes": {"value": 800, "provenance": "client_observed"},
                "files_read": {"value": 0, "provenance": "client_observed"},
                "compression_basis_points": {
                    "value": 8000, "provenance": "client_observed",
                },
            },
            "truncated": False,
            "identical_host_packets": True,
            "benchmark_diff_sha256": digest,
            "bounded_diff_sha256": digest,
            "review_claim_diff_sha256": digest,
        })
    attestation = {
        "artifact": pilot.ATTESTATION_ARTIFACT,
        "version": 1,
        "protocol_sha256": _sha(root / pilot.PROTOCOL_FILE),
        "started_at": "2026-08-25T20:00:00+02:00",
        "ended_at": "2026-08-25T20:10:00+02:00",
        "cli_versions": {"claude": "2.1.224", "codex": "0.147.0"},
        "traces": trace_meta,
        "actual_calls": dict(pilot.EXPECTED_CALLS),
        "calls_by_profile": {
            profile: 12
            for profiles in pilot.PROFILES.values()
            for profile in profiles
        },
        "context_slots": context_slots,
        "execution": {
            "automatic_retries": 0,
            "corrections": 0,
            "escalations": 0,
            "escalation_ceiling": 2,
            "production_state_mutations": 0,
        },
        "operator_claims": {
            "one_packet_object_per_pair": True,
            "same_packet_bytes_for_all_profiles_in_pair": True,
            "registered_context_policy_used": "baseline-v1",
            "raw_packet_retained": False,
            "raw_host_output_retained": False,
        },
        "limitations": list(pilot.EXPECTED_LIMITATIONS),
        "runner_adapter": copy.deepcopy(pilot.EXPECTED_RUNNER_ADAPTER),
    }
    (root / pilot.ATTESTATION_FILE).write_text(
        json.dumps(attestation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return attestation


def test_complete_valid_profile_fixture_compiles_and_reproduces(tmp_path):
    _write_fixture(tmp_path)
    results, report = pilot.write_outputs(tmp_path)
    checked, checked_report = pilot.check_outputs(tmp_path)
    assert checked == results
    assert checked_report == report
    assert len(results["attempts"]) == 60
    assert results["matrix"]["complete_frozen_matrix"] is True
    assert results["matrix"]["complete_f46_requested_matrix"] is False
    assert results["acceptance"]["AC-46-2"] == "not_satisfied_unsupported_frozen_matrix"
    assert results["aggregate"]["value"] is None
    assert all(
        attempt["metrics"]["cumulative_cost_usd"]["value"] is None
        and attempt["loop"]["escalation_count"] == 0
        and attempt["gate"]["preserved"] is True
        for attempt in results["attempts"]
    )


def test_protocol_rejects_post_freeze_low_high_arm():
    protocol = json.loads((PILOT_ROOT / pilot.PROTOCOL_FILE).read_text())
    protocol["matrix"]["codex"].append("codex-terra-low")
    with pytest.raises(pilot.PilotEvidenceError, match="frozen profiles"):
        pilot.validate_protocol(protocol)


def test_attestation_rejects_claim_not_bound_to_exact_diff(tmp_path):
    attestation = _write_fixture(tmp_path)
    attestation["context_slots"][0]["review_claim_diff_sha256"] = "f" * 64
    with pytest.raises(pilot.PilotEvidenceError, match="exact benchmark diff"):
        pilot.validate_attestation(attestation, root=tmp_path)


def test_attestation_rejects_unrecorded_private_adapter_change(tmp_path):
    attestation = _write_fixture(tmp_path)
    attestation["runner_adapter"]["production_routing_changed"] = True
    with pytest.raises(pilot.PilotEvidenceError, match="adapter attestation"):
        pilot.validate_attestation(attestation, root=tmp_path)


def test_trace_matrix_fails_closed_when_one_attempt_is_missing(tmp_path):
    _write_fixture(tmp_path)
    path = tmp_path / pilot.TRACE_FILES["codex"]
    path.write_bytes(b"\n".join(path.read_bytes().splitlines()[:-1]) + b"\n")
    with pytest.raises(pilot.PilotEvidenceError, match="trace is incomplete"):
        pilot._load_traces(tmp_path)


def test_derived_results_reject_invented_cost(tmp_path):
    _write_fixture(tmp_path)
    results, _report = pilot.write_outputs(tmp_path)
    results["host_reports"]["claude"]["cumulative_cost_usd"] = {
        "value": 0,
        "provenance": "host_reported",
    }
    (tmp_path / pilot.RESULTS_FILE).write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(pilot.PilotEvidenceError, match="deterministically derived"):
        pilot.check_outputs(tmp_path)


def test_durable_pilot_evidence_contains_no_forbidden_content_fields():
    forbidden = {
        b"prompt", b"response", b"excerpt", b"command_output", b"error_text",
        b"secret", b"credential", b"user_identifier",
    }
    paths = [
        PILOT_ROOT / pilot.ATTESTATION_FILE,
        PILOT_ROOT / pilot.RESULTS_FILE,
        PILOT_ROOT / pilot.REPORT_FILE,
        *(PILOT_ROOT / relative for relative in pilot.TRACE_FILES.values()),
    ]
    for path in paths:
        raw = path.read_bytes().lower()
        assert not forbidden.intersection(raw), path.name
