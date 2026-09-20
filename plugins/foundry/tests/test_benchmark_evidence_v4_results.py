from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-35"


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVIDENCE = _module(
    "foundry35_benchmark_evidence_v4_results_test", "benchmark-evidence-v4.py"
)


def _load(name: str) -> dict[str, object]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _sha(name: str) -> str:
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def test_tracked_v4_results_recompute_and_fail_closed():
    raw = _load("raw-evidence-v4.json")
    aggregate = _load("aggregate-v4.json")
    frozen = EVIDENCE.validate_freeze_v4(ROOT)
    assert _sha("raw-evidence-v4.json") == (
        "6a202d4e98315df1e77d28b4aed7510ec40b34f5c62229b716ddf6e1d9a72249"
    )
    assert _sha("aggregate-v4.json") == (
        "798881ec34719ac4103c3a9fd6ec938decdd6a83bd65053ddb70a310d86dca46"
    )
    assert EVIDENCE.validate_raw_evidence_v4(
        raw, frozen["protocol"], frozen["corpus"]
    ) == aggregate
    assert len(raw["intrinsic_rows"]) == 108
    assert len(raw["product_rows"]) == 48
    assert len(raw["wrapper_rows"]) == 7
    assert len(raw["baseline_rows"]) == 60
    assert len(raw["candidate_pipeline_rows"]) == 48
    assert len(raw["attempts"]) == 346
    assert Counter(row["category"] for row in raw["attempts"]) == {
        "local_model": 156,
        "downstream_cloud": 108,
        "facade_resolution": 12,
        "technical_setup": 70,
    }
    assert sum(
        row["campaign_version"] == 1
        and row["http_status"] == 404
        and row["category"] == "technical_setup"
        for row in raw["attempts"]
    ) == 51
    assert aggregate["promotion_eligible_candidates"] == []
    assert all(not row["promotion_eligible"] for row in aggregate["candidate_results"])
    assert all(row["estimated_cloud_cost_reduction"] is None for row in aggregate["candidate_results"])
    assert all(row["net_cost_reduction"] is None for row in aggregate["candidate_results"])


def test_tracked_v4_publication_is_sanitized_and_recommended_none():
    raw_text = (ROOT / "raw-evidence-v4.json").read_text(encoding="utf-8")
    attestation_text = (ROOT / "run-attestation-v4.json").read_text(encoding="utf-8")
    report = (ROOT / "FINAL-RECOMMENDATION-v4.md").read_text(encoding="utf-8")
    assert "/Users/" not in raw_text + attestation_text
    assert "phase-b-authorization" not in raw_text + attestation_text
    assert "$HOME/Library/Caches/FoundryModels/foundry-35" in raw_text
    assert "$HOME/Library/Caches/FoundryModels/foundry-35" in attestation_text
    assert _sha("run-attestation-v4.json") == (
        "9ed6fe479fe006cae80258458fab46d1b5bb3b825719f47553c94675abef1dc3"
    )
    attestation = json.loads(attestation_text)
    assert attestation["freeze_commit"] == (
        "4c7aed539583b597b12fbc71f4b99cffb577c916"
    )
    assert attestation["freeze_manifest_sha256"] == (
        "52b537213425d4ad33daea001030125807f537a8d7e8a78531353fe63742f975"
    )
    assert "promote no candidate" in report
    assert "all defaults remain unchanged" in report
