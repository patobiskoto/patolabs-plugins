"""Adversarial tests for the bounded offline FOUNDRY-58 experiment."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-58"


def _module():
    spec = importlib.util.spec_from_file_location(
        "foundry58_hybrid_retrieval", ROOT / "hybrid-retrieval-v1.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EXPERIMENT = _module()


def _artifacts():
    frozen = EXPERIMENT.validate_f51_freeze()
    fixture = json.loads((ROOT / "fixtures-v1.json").read_text(encoding="utf-8"))
    evidence = json.loads((ROOT / "evidence-v1.json").read_text(encoding="utf-8"))
    report = json.loads((ROOT / "report-v1.json").read_text(encoding="utf-8"))
    return frozen, fixture, evidence, report


def test_artifacts_revalidate_frozen_f51_and_exact_f56_paired_matrix():
    result = EXPERIMENT.validate_artifacts()
    plan = EXPERIMENT.plan(EXPERIMENT.validate_f51_freeze())
    assert result["slot_count"] == len(plan) == 126
    assert result["fixture"]["plan_sha256"] == EXPERIMENT.canonical_sha256(plan)
    dimensions = {
        (row["case_id"], row["revision"], row["budget_id"], row["repetition"])
        for row in plan if row["strategy"] == "lexical"
    }
    for strategy in EXPERIMENT.STRATEGIES:
        assert dimensions == {
            (row["case_id"], row["revision"], row["budget_id"], row["repetition"])
            for row in plan if row["strategy"] == strategy
        }


def test_budget_profiles_are_materialized_by_f56_and_budget_or_plan_drift_fails_closed():
    frozen, fixture, _evidence, _report = _artifacts()
    profiles = EXPERIMENT.budget_profiles()
    assert [row["partitions"]["injectable_capacity_tokens"] for row in profiles] == [1024, 10240]

    bad = copy.deepcopy(fixture)
    bad["budget_profiles"][0]["partitions"]["margin_tokens"] += 1
    with pytest.raises(EXPERIMENT.HybridExperimentError, match="F56 budgets differ"):
        EXPERIMENT.validate_fixture(bad, frozen)

    bad = copy.deepcopy(fixture)
    bad["plan_sha256"] = "0" * 64
    with pytest.raises(EXPERIMENT.HybridExperimentError, match="paired plan differs"):
        EXPERIMENT.validate_fixture(bad, frozen)


def test_pure_composition_is_stable_deduplicated_and_budget_bounded_without_becoming_evidence():
    first = EXPERIMENT.compose_lexical_first(
        ["C1", "C2", "C1"], ["C2", "C3", "C4"],
        {"C1": 3, "C2": 4, "C3": 5, "C4": 2}, 10,
    )
    second = EXPERIMENT.compose_lexical_first(
        ["C1", "C2", "C1"], ["C2", "C3", "C4"],
        {"C1": 3, "C2": 4, "C3": 5, "C4": 2}, 10,
    )
    assert first == second == ("C1", "C2", "C4")
    report = EXPERIMENT.validate_artifacts()["report"]
    union = next(row for row in report["candidate_reports"] if row["strategy"].endswith("union"))
    assert union["observed_attempts"] == 0
    assert set(union["metrics"].values()) == {None}

    with pytest.raises(EXPERIMENT.HybridExperimentError, match="token size is unavailable"):
        EXPERIMENT.compose_lexical_first(["C1"], [], {}, 10)


def test_verified_dependency_artifact_does_not_imply_observed_retrieval():
    result = EXPERIMENT.validate_artifacts()
    assert all(row["artifact_state"] == "verified" for row in result["evidence"]["dependency_artifacts"])
    assert result["evidence"]["persisted_selection"] is None
    for candidate in result["report"]["candidate_reports"]:
        assert candidate["observed_attempts"] == 0
        assert set(candidate["metric_states"].values()) == {"unavailable"}
        assert set(candidate["metrics"].values()) == {None}


def test_missing_or_altered_optional_source_degrades_to_unavailable_without_conclusion(tmp_path):
    missing = EXPERIMENT.dependency_observation(
        "f52_evidence", tmp_path / "missing.json",
        EXPERIMENT.SOURCE_DIGESTS["f52_evidence"],
    )
    assert missing == {
        "source": "f52_evidence",
        "artifact_state": "unavailable",
        "artifact_sha256": None,
        "expected_sha256": EXPERIMENT.SOURCE_DIGESTS["f52_evidence"],
        "reason": "source_artifact_missing",
    }

    altered = tmp_path / "altered.json"
    altered.write_text("{}", encoding="utf-8")
    observation = EXPERIMENT.dependency_observation(
        "f52_evidence", altered, EXPERIMENT.SOURCE_DIGESTS["f52_evidence"],
    )
    assert observation["artifact_state"] == "unavailable"
    assert observation["reason"] == "source_digest_mismatch"

    for source, replacement in {
        "f52_evidence": tmp_path / "missing.json",
        "f53_report": altered,
        "f57_report": tmp_path / "missing-f57.json",
    }.items():
        result = EXPERIMENT.validate_artifacts(
            dependency_overrides={source: replacement},
        )
        unavailable = next(
            row for row in result["evidence"]["dependency_artifacts"]
            if row["source"] == source
        )
        assert unavailable["artifact_state"] == "unavailable"
        family = source.split("_", 1)[0]
        observation = result["evidence"]["source_observations"][family]
        assert observation["campaign_state"] == "dependency_unavailable"
        if family == "f57":
            assert observation["f51_retrieval_rows"] is None
        else:
            assert observation["observed_attempts"] is None
        assert result["evidence"]["persisted_selection"] is None
        assert result["report"]["recommendation"] == {
            "strategy": None,
            "decision": "inconclusive_no_promotion",
            "production_promotion": "prohibited",
            "promotion_level_conclusion": False,
            "limitation": "One or more optional retrieval sources are unavailable; no comparable ranked selections are available under the F56 budgets.",
        }
        for candidate in result["report"]["candidate_reports"]:
            assert candidate["observed_attempts"] == 0
            assert set(candidate["metrics"].values()) == {None}

    _frozen, _fixture, _evidence, report = _artifacts()
    assert report["recommendation"] == {
        "strategy": None,
        "decision": "inconclusive_no_promotion",
        "production_promotion": "prohibited",
        "promotion_level_conclusion": False,
        "limitation": "F52 observations are withdrawn and F53 has no execution; no comparable ranked selections are available under the F56 budgets.",
    }


def test_unavailable_selection_nullability_and_fabricated_metrics_fail_closed():
    frozen, fixture, evidence, report = _artifacts()
    bad = copy.deepcopy(evidence)
    bad["persisted_selection"] = []
    with pytest.raises(EXPERIMENT.HybridExperimentError, match="selection must remain null"):
        EXPERIMENT.validate_evidence(bad, frozen)

    forged = copy.deepcopy(report)
    forged["candidate_reports"][0]["metrics"]["recall"] = 0
    with pytest.raises(EXPERIMENT.HybridExperimentError, match="evidence-derived"):
        EXPERIMENT.validate_report(forged, frozen, fixture, evidence, strict=True)

    forged = copy.deepcopy(report)
    forged["recommendation"]["strategy"] = "lexical"
    with pytest.raises(EXPERIMENT.HybridExperimentError, match="evidence-derived"):
        EXPERIMENT.validate_report(forged, frozen, fixture, evidence, strict=True)


def test_all_requested_metrics_and_ablations_are_published_as_nullable():
    report = EXPERIMENT.validate_artifacts()["report"]
    assert set(report["candidate_reports"][0]["metrics"]) == set(EXPERIMENT.METRICS)
    assert {row["id"] for row in report["ablations"]} == {
        "without_structural", "without_lexical", "budget_sensitivity",
        "structural_source_swap", "tool_hygiene",
    }
    assert all(set(row["deltas"].values()) == {None} for row in report["ablations"])
    assert set(report["comparison"]["marginal_deltas"].values()) == {None}
    assert report["comparison"]["equivalent_quality"] is None
    assert report["comparison"]["simplest_strategy_at_equivalent_quality"] is None


def test_rrf_is_not_implemented_without_comparable_ranked_inputs():
    report = EXPERIMENT.validate_artifacts()["report"]
    assert report["rrf"]["state"] == "not_retained"
    assert report["rrf"]["implemented"] is False
    assert "comparable observed ranked lists" in report["rrf"]["justification"]
    assert not hasattr(EXPERIMENT, "rrf")


@pytest.mark.parametrize(
    "value",
    [
        {"raw_code_value": "synthetic"},
        {"artifact_path_hint": "synthetic"},
        {"Prompt": "synthetic"},
        {"safe": "/private/local"},
        {"safe": "Bearer SYNTHETIC_REJECTION_MARKER"},
    ],
)
def test_privacy_forbidden_material_fails_closed(value):
    with pytest.raises(EXPERIMENT.HybridExperimentError, match="privacy-forbidden"):
        EXPERIMENT.validate_privacy(value)


def test_experiment_has_no_production_import_or_network_execution_surface():
    source = (ROOT / "hybrid-retrieval-v1.py").read_text(encoding="utf-8")
    assert "requests" not in source
    assert "urllib" not in source
    assert "socket" not in source
    assert "foundry.routing" not in source
    assert "subprocess.Popen" not in source
    assert "subprocess.run([\"git\"" not in source
