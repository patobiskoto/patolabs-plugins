import copy
from pathlib import Path

import pytest

from foundry.benchmark_evidence import (
    CampaignEvidenceError,
    recompute_aggregate,
    validate_campaign,
)


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-35"


def test_foundry_35_evidence_is_executable_measured_and_strictly_recomputed():
    evidence = validate_campaign(ROOT)
    assert len(evidence["raw"]["candidate_rows"]) == 36
    assert len(evidence["raw"]["adversarial_rows"]) == 15
    assert evidence["aggregate"]["promotion_eligible_candidates"] == []
    assert evidence["aggregate"]["candidate_results"][0]["schema_valid_rate"] == 1.0
    assert evidence["aggregate"]["candidate_results"][1][
        "schema_valid_rate"
    ] == pytest.approx(1 / 6)
    assert evidence["aggregate"]["kpis"]["estimated_cloud_cost_reduction"] is None


def test_recompute_rejects_missing_repetition_and_forged_citation():
    evidence = validate_campaign(ROOT)
    raw = copy.deepcopy(evidence["raw"])
    raw["candidate_rows"].pop()
    with pytest.raises(CampaignEvidenceError, match="exactly three rows"):
        recompute_aggregate(raw, evidence["protocol"], evidence["corpus"])

    raw = copy.deepcopy(evidence["raw"])
    raw["candidate_rows"][0]["accepted_evidence_ids"].append("FORGED-999")
    with pytest.raises(CampaignEvidenceError, match="unbound citation"):
        recompute_aggregate(raw, evidence["protocol"], evidence["corpus"])


def test_recompute_does_not_turn_missing_cost_or_usage_into_zero():
    evidence = validate_campaign(ROOT)
    aggregate = recompute_aggregate(
        evidence["raw"], evidence["protocol"], evidence["corpus"]
    )
    assert aggregate["kpis"]["cloud_tokens_avoided"] is None
    assert aggregate["kpis"]["median_cloud_input_reduction"] is None
    assert aggregate["kpis"]["estimated_cloud_cost_reduction"] is None
