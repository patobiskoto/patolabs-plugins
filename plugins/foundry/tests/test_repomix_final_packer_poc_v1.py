"""Adversarial tests for the isolated FOUNDRY-54 Repomix POC."""

from __future__ import annotations

import copy
import functools
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-54"
spec = importlib.util.spec_from_file_location(
    "foundry54_repomix", ROOT / "repomix-poc-v1.py"
)
assert spec is not None and spec.loader is not None
POC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(POC)


@functools.cache
def artifacts():
    frozen = POC.validate_f51_freeze()
    return frozen, *(
        json.loads((ROOT / name).read_text())
        for name in (
            "protocol-v1.json",
            "fixtures-v1.json",
            "evidence-v1.json",
            "report-v1.json",
        )
    )


def test_exact_f51_binding_and_63_paired_slots():
    assert POC.validate_artifacts()["plan_count"] == 63
    slots = POC.plan(POC.validate_f51_freeze())
    paired = [
        {
            (x["case_id"], x["revision"], x["repetition"])
            for x in slots
            if x["candidate"] == candidate
        }
        for candidate in POC.CANDIDATES
    ]
    assert paired[0] == paired[1] == paired[2]


def test_forged_binding_plan_or_persisted_slot_fails_closed():
    frozen, _protocol, fixture, evidence, _report = artifacts()
    bad = copy.deepcopy(fixture)
    bad["f51_binding"]["case_count"] = 8
    with pytest.raises(POC.RepomixPocError, match="fixture plan"):
        POC.fixture(bad, frozen)
    bad = copy.deepcopy(evidence)
    bad["attempts"][0]["revision"] = "0" * 40
    with pytest.raises(POC.RepomixPocError, match="evidence"):
        POC.evidence(bad, frozen)


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt", "private source"),
        ("output_bytes", 0),
        ("source_digest_sha256", "0" * 64),
        ("truncation_state", "not_truncated"),
        ("overflow_state", "no_overflow"),
    ],
)
def test_privacy_and_fabricated_measurement_or_packing_state_fail_closed(field, value):
    frozen, _protocol, _fixture, evidence, _report = artifacts()
    bad = copy.deepcopy(evidence)
    bad["attempts"][0][field] = value
    with pytest.raises(POC.RepomixPocError):
        POC.evidence(bad, frozen)


def test_mode_availability_explicit_scoped_conclusion_and_non_promotion_are_derived():
    frozen, _protocol, _fixture, evidence, report = artifacts()
    assert evidence["attempts"][0]["mode_availability"] == {
        mode: "unavailable" for mode in POC.MODES
    }
    conclusion = report["comparison"]["conclusion"]
    assert conclusion == {
        "decision": "reject",
        "scope": POC.CONCLUSION_SCOPE,
        "decision_target": POC.CONCLUSION_TARGET,
        "basis": {
            "campaign_state": "not_executed",
            "controlled_execution_contract": "unavailable_fail_closed",
            "f51_slot_count": 63,
            "observed_attempt_count": 0,
            "unavailable_attempt_count": 63,
        },
        "boundary": POC.CONCLUSION_BOUNDARY,
    }
    assert conclusion["decision"] in POC.CONCLUSION_DECISIONS
    assert report["comparison"]["production_promotion"] == "prohibited"
    bad = copy.deepcopy(report)
    bad["comparison"]["production_promotion"] = "approved"
    with pytest.raises(POC.RepomixPocError, match="report"):
        POC.validate_report(bad, frozen, evidence)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (
            lambda report: report["comparison"].pop("conclusion"),
            "conclusion is missing",
        ),
        (
            lambda report: report["comparison"]["conclusion"].update(
                {"decision": "inconclusive_no_promotion"}
            ),
            "required vocabulary",
        ),
        (
            lambda report: report["comparison"]["conclusion"].update(
                {"decision": "reuse"}
            ),
            "incoherent",
        ),
        (
            lambda report: report["comparison"]["conclusion"]["basis"].update(
                {"observed_attempt_count": 1}
            ),
            "invented|not derived",
        ),
        (
            lambda report: report["comparison"]["conclusion"].update(
                {"scope": "global_repomix_suitability"}
            ),
            "not scoped",
        ),
    ],
)
def test_report_rejects_missing_invalid_incoherent_or_unbound_conclusion(
    mutate, match
):
    frozen, _protocol, _fixture, evidence, report = artifacts()
    bad = copy.deepcopy(report)
    mutate(bad)
    with pytest.raises(POC.RepomixPocError, match=match):
        POC.validate_report(bad, frozen, evidence)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda protocol: protocol["scope"].update({"execution": "networked"}),
        lambda protocol: protocol["scope"]["exclusions"].append("arbitrary_scope"),
        lambda protocol: protocol["primary_sources"][1].update(
            {"url": "https://raw.githubusercontent.com/yamadashy/repomix/main/LICENSE"}
        ),
        lambda protocol: protocol["primary_sources"][2].update(
            {"commit_sha1": "0" * 40}
        ),
        lambda protocol: protocol["primary_sources"][2]["content_sha256"].update(
            {"value": "0" * 64}
        ),
        lambda protocol: protocol["primary_sources"][0]["content_sha256"].update(
            {"state": "observed", "value": None}
        ),
        lambda protocol: protocol["primary_sources"][1].update({"code": "private"}),
        lambda protocol: protocol["primary_sources"][1].update({"path": "/private/source"}),
        lambda protocol: protocol["primary_sources"][1].update({"secret": "private"}),
        lambda protocol: protocol["primary_sources"].__setitem__(1, None),
    ],
)
def test_protocol_rejects_unbound_sources_unsafe_scope_and_source_privacy(mutate):
    frozen, protocol, _fixture, _evidence, _report = artifacts()
    bad = copy.deepcopy(protocol)
    mutate(bad)
    with pytest.raises(POC.RepomixPocError):
        POC.protocol(bad, frozen)


def test_every_declared_metric_uses_nullable_envelope_without_baseline_claims():
    frozen, protocol, _fixture, evidence, report = artifacts()
    assert protocol["metrics"] == [
        *POC.METRICS,
        "source_digest",
        "output_digest",
        "output_size",
        "truncation",
        "overflow",
        "mode_availability",
        "operational_complexity",
        "security_risks",
        "failure_classes",
    ]
    for candidate in report["candidate_reports"]:
        assert set(candidate["metrics"]) == set(protocol["metrics"])
        assert all(
            metric == {"state": "unavailable", "value": None}
            for metric in candidate["metrics"].values()
        )
        assert "deterministic" not in json.dumps(candidate, sort_keys=True)
        assert "stable" not in json.dumps(candidate, sort_keys=True)
    regions = dict(POC.report_metric_envelopes(report))
    assert set(report["comparison"]["marginal_deltas"]) == set(POC.MARGINAL_DELTA_METRICS)
    assert len(regions) == len(POC.CANDIDATES) * len(POC.REPORT_METRICS) + len(POC.MARGINAL_DELTA_METRICS)
    assert all(metric == {"state": "unavailable", "value": None} for metric in regions.values())
    assert any(path.startswith("candidate_reports.") for path in regions)
    assert any(path.startswith("comparison.marginal_deltas.") for path in regions)
    bad = copy.deepcopy(report)
    bad["candidate_reports"][0]["metrics"]["determinism"] = {
        "state": "observed",
        "value": True,
    }
    with pytest.raises(POC.RepomixPocError, match="report"):
        POC.validate_report(bad, frozen, evidence)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["comparison"]["marginal_deltas"].__setitem__("recall", None),
        lambda report: report["comparison"]["marginal_deltas"]["bytes"].update({"state": "unknown"}),
        lambda report: report["comparison"]["marginal_deltas"]["latency_ms"].update(
            {"state": "observed", "value": None}
        ),
        lambda report: report["comparison"]["marginal_deltas"]["cost_usd"].__setitem__(
            "unexpected", None
        ),
    ],
)
def test_bare_unknown_or_malformed_comparison_delta_fails_closed(mutate):
    frozen, _protocol, _fixture, evidence, report = artifacts()
    bad = copy.deepcopy(report)
    mutate(bad)
    with pytest.raises(POC.RepomixPocError, match="metric|marginal delta"):
        POC.validate_report(bad, frozen, evidence)
