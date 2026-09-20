"""Adversarial tests for the isolated FOUNDRY-53 RepoMap ranking POC."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-53"


def _module():
    spec = importlib.util.spec_from_file_location("foundry53_repomap", ROOT / "repomap-poc-v1.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POC = _module()


def _artifacts():
    frozen = POC.validate_f51_freeze()
    return frozen, json.loads((ROOT / "protocol-v1.json").read_text()), json.loads((ROOT / "fixtures-v1.json").read_text()), json.loads((ROOT / "evidence-v1.json").read_text())


def test_offline_artifacts_revalidate_exact_f51_paired_plan():
    result = POC.validate_artifacts()
    assert result["plan_count"] == 63
    slots = POC.plan(POC.validate_f51_freeze())
    assert {(slot["case_id"], slot["revision"], slot["repetition"]) for slot in slots if slot["candidate"] == "lexical"} == {(slot["case_id"], slot["revision"], slot["repetition"]) for slot in slots if slot["candidate"] == "aider_repomap"}


def test_f51_binding_and_paired_slot_mismatch_fail_closed():
    frozen, _protocol, fixture, evidence = _artifacts()
    fixture["f51_binding"]["case_count"] = 8
    with pytest.raises(POC.RepoMapPocError, match="F51 binding"):
        POC._fixture(fixture, frozen)

    attempts = POC.unavailable_attempts(frozen)
    attempts[0]["revision"] = "0" * 40
    assert attempts[0] != POC.unavailable_attempts(frozen)[0]
    # The persisted template cannot substitute a forged plan: the derived plan hash binds it.
    evidence["plan_sha256"] = "0" * 64
    with pytest.raises(POC.RepoMapPocError, match="evidence plan binding"):
        POC._evidence(evidence, frozen)


def test_missing_or_invalid_provenance_and_metric_nullability_fail_closed():
    frozen, _protocol, _fixture, evidence = _artifacts()
    bad = copy.deepcopy(evidence)
    bad["slot_template"]["provenance_sha256"] = "not-a-digest"
    with pytest.raises(POC.RepoMapPocError, match="evidence plan binding"):
        POC._evidence(bad, frozen)

    bad = copy.deepcopy(evidence)
    bad["slot_template"]["cost_usd"] = 0
    with pytest.raises(POC.RepoMapPocError, match="evidence plan binding"):
        POC._evidence(bad, frozen)


def test_privacy_leakage_and_fabricated_report_fail_closed():
    frozen, _protocol, _fixture, evidence = _artifacts()
    bad = copy.deepcopy(evidence)
    bad["slot_template"]["prompt"] = "private source"
    with pytest.raises(POC.RepoMapPocError, match="evidence slot template"):
        POC._evidence(bad, frozen)

    report = POC.build_report(frozen, evidence)
    report["comparison"]["token_or_cost_gain"] = 0.5
    with pytest.raises(POC.RepoMapPocError, match="report is not derived"):
        POC.validate_report(report, frozen, evidence)
