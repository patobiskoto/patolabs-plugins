"""Offline adversarial tests for the FOUNDRY-65 lifecycle diagnostic."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
DIAGNOSTIC_PATH = (
    ROOT / "plugins/foundry/benchmarks/foundry-65/diagnose-codex-lifecycle-v1.py"
)


def _diagnostic():
    spec = importlib.util.spec_from_file_location("foundry_65_diagnostic_test", DIAGNOSTIC_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_completed_fake_stream_exits_zero_but_invalid_terminal_envelope_is_rejected():
    diagnostic = _diagnostic()
    checks = diagnostic.exercise_runner_lifecycle()
    assert checks["completed_without_terminal_envelope"] == {
        "fake_processes": 1,
        "shell_false": True,
        "stdin_eof_before_wait": True,
        "stdout_drained": True,
        "process_wait_observed": True,
        "zero_exit_observed": True,
        "terminal_envelope_valid": False,
        "failure_class": "guard_refusal",
    }


def test_fake_timeout_is_a_separate_native_timeout_with_cleanup():
    diagnostic = _diagnostic()
    checks = diagnostic.exercise_runner_lifecycle()
    assert checks["bounded_timeout"] == {
        "fake_processes": 1,
        "shell_false": True,
        "timeout_category_observed": True,
        "timeout_cleanup_observed": True,
        "failure_class": "native_timeout",
    }
    assert checks == {
        "fake_processes": 2,
        "completed_without_terminal_envelope": checks["completed_without_terminal_envelope"],
        "bounded_timeout": checks["bounded_timeout"],
        "raw_native_output_retained": False,
        "native_calls": 0,
        "provider_calls": 0,
        "campaign_calls": 0,
    }


def test_frozen_check_is_closed_offline_and_does_not_overclaim():
    diagnostic = _diagnostic()
    result = diagnostic.check()
    assert result == {
        "status": "pass",
        "verdict": "inconclusive_no_runner_lifecycle_defect_reproduced",
        "fake_processes": 2,
        "native_calls": 0,
        "provider_calls": 0,
        "campaign_calls": 0,
    }
    evidence = diagnostic.load_json(diagnostic.EVIDENCE_PATH)
    assert evidence["diagnosis"] == {
        "current_runner_lifecycle_defect": "not_reproduced",
        "causal_attribution": "unavailable",
        "observed_codex_terminal_state": "unknown",
        "confidence_scope": "offline_fake_boundary_only",
    }


def test_each_observed_run_is_content_free_provenanced_and_inconclusive():
    diagnostic = _diagnostic()
    evidence = diagnostic.build_evidence()
    assert [row["status"] for row in evidence["observed_inputs"]] == [
        "inconclusive", "inconclusive",
    ]
    for row in evidence["observed_inputs"]:
        assert row["provenance"] == {
            "record_kind": "content_free_run_summary",
            "run_reference": row["run_id"],
            "provenance_scope": "f65_initial_frozen_evidence_only",
            "source_artifact_sha256": diagnostic.INITIAL_F65_EVIDENCE_SHA256,
            "source_artifact_sha256_provenance": "initial_f65_evidence_file",
            "raw_native_output_available": False,
            "raw_native_output_retained": False,
        }
    report = diagnostic.build_report(evidence)
    assert report["observed_run_statuses"] == [
        {"run_id": "run03", "status": "inconclusive"},
        {"run_id": "run04", "status": "inconclusive"},
    ]


def test_unavailable_provider_tokens_and_costs_are_null_with_provenance():
    diagnostic = _diagnostic()
    for row in diagnostic.build_evidence()["observed_inputs"]:
        assert row["provider"] is None
        assert row["provider_provenance"] == "unavailable"
        assert set(row["metrics"]) == set(diagnostic.UNAVAILABLE_METRIC_FIELDS)
        assert all(value is None for value in row["metrics"].values())
        assert row["metric_provenance"] == {
            name: "unavailable" for name in diagnostic.UNAVAILABLE_METRIC_FIELDS
        }
        assert row["effective_cost_usd"] is None
        assert row["effective_cost_provenance"] == "unavailable"
        assert row["economic_verdict"] == "unavailable"


@pytest.mark.parametrize(
    ("artifact_name", "mutation"),
    [
        ("protocol", lambda value: value["scope"].update({"benign_marker": False})),
        ("evidence", lambda value: value["observed_inputs"][0]["metrics"].update(
            {"benign_marker": None}
        )),
        ("report", lambda value: value["recommendation"].update({"benign_marker": False})),
        ("manifest", lambda value: value["files"].update({"benign_marker": "0" * 64})),
    ],
)
def test_recursive_allowlists_reject_unknown_nested_keys(artifact_name, mutation):
    diagnostic = _diagnostic()
    evidence = diagnostic.build_evidence()
    artifacts = {
        "protocol": (diagnostic.build_protocol(), diagnostic.validate_protocol),
        "evidence": (evidence, diagnostic.validate_evidence),
        "report": (diagnostic.build_report(evidence), diagnostic.validate_report),
        "manifest": (diagnostic.build_manifest(), diagnostic.validate_manifest),
    }
    expected, validator = artifacts[artifact_name]
    changed = diagnostic.load_json({
        "protocol": diagnostic.PROTOCOL_PATH,
        "evidence": diagnostic.EVIDENCE_PATH,
        "report": diagnostic.REPORT_PATH,
        "manifest": diagnostic.MANIFEST_PATH,
    }[artifact_name])
    mutation(changed)
    with pytest.raises(diagnostic.DiagnosticError, match="recursively allowlisted"):
        validator(changed, expected)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["privacy"].update({"raw_native_output_retained": True}),
        lambda value: value["observed_inputs"][0].update({"terminal_category": "other"}),
    ],
)
def test_allowlisted_evidence_still_rejects_changed_values(mutation):
    diagnostic = _diagnostic()
    expected = diagnostic.build_evidence()
    changed = diagnostic.load_json(diagnostic.EVIDENCE_PATH)
    mutation(changed)
    with pytest.raises(diagnostic.DiagnosticError, match="evidence differs"):
        diagnostic.validate_evidence(changed, expected)


def test_recommendation_is_single_use_bounded_and_needs_fresh_human_authorization():
    diagnostic = _diagnostic()
    report = diagnostic.build_report(diagnostic.build_evidence())
    recommendation = report["recommendation"]
    assert recommendation["action"] == "one_slot_codex_lifecycle_probe"
    assert recommendation["scope"] == "separate_diagnostic_only"
    assert recommendation["fresh_human_authorization_required"] is True
    assert recommendation["operator_opt_in_required"] is True
    assert recommendation["maximum_native_invocations"] == 1
    assert recommendation["campaign_evidence_eligible"] is False
    assert recommendation["raw_native_output_retained"] is False
    assert recommendation["automatic_retries"] == 0
    assert recommendation["automatic_corrections"] == 0
    assert recommendation["automatic_escalations"] == 0


def test_time_candidates_are_auditable_but_do_not_prove_campaign_completion():
    diagnostic = _diagnostic()
    candidates = diagnostic.candidate_time_envelopes()
    assert [candidate["native_call_ceiling_seconds"] for candidate in candidates] == [
        24_570, 43_470, 62_370,
    ]
    assert all(candidate["native_call_ceiling_fits_authority"] for candidate in candidates)
    assert all(candidate["full_campaign_completion_proven"] is False for candidate in candidates)
    with pytest.raises(diagnostic.DiagnosticError, match="positive"):
        diagnostic.native_time_envelope(0)


def test_cli_has_no_execute_mode():
    diagnostic = _diagnostic()
    with pytest.raises(SystemExit):
        diagnostic.main([])
