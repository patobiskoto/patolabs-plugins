from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-45"
EVIDENCE = ROOT / "evidence-v2" / "d9ef49a4-d856-49d8-b48b-acf3df0de6b3.jsonl"
ATTESTATION = ROOT / "run-attestation-diagnostic-v2.json"
EVIDENCE_SHA256 = "79a6d3c32872a715ecb227790af3fb24f3f0133524ef560f85730a13553b61a1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _records() -> list[dict[str, object]]:
    checkpoints = [json.loads(line) for line in EVIDENCE.read_text(encoding="utf-8").splitlines()]
    previous = None
    records: list[dict[str, object]] = []
    for sequence, checkpoint in enumerate(checkpoints):
        assert set(checkpoint) == {"sequence", "previous_record_sha256", "record_sha256", "record"}
        assert checkpoint["sequence"] == sequence
        assert checkpoint["previous_record_sha256"] == previous
        assert type(checkpoint["record"]) is dict
        digest = hashlib.sha256(_canonical(checkpoint["record"])).hexdigest()
        assert checkpoint["record_sha256"] == digest
        previous = digest
        records.append(checkpoint["record"])
    return records


def test_diagnostic_campaign_is_complete_sanitized_and_inconclusive():
    records = _records()

    assert hashlib.sha256(EVIDENCE.read_bytes()).hexdigest() == EVIDENCE_SHA256
    assert len(records) == 168
    assert {
        tuple(sorted(record))
        for record in records
    } == {
        (
            "candidate_id", "cost", "diagnostic_code", "golden_classification", "host", "kind",
            "metrics", "product_case_id", "repetition", "result", "review_outcome", "schema_version",
            "terminal", "test_outcome",
        )
    }
    assert all(record["schema_version"] == 2 and record["result"] == "inconclusive" for record in records)

    counts = Counter(
        (
            record["kind"],
            record["host"],
            record["terminal"]["status"],
            record["terminal"]["failure_class"],
            record["golden_classification"],
            record["diagnostic_code"],
        )
        for record in records
    )
    assert counts == {
        ("local_product", None, "failed", "local_unavailable", "unavailable", "LOCAL_SCOUT_UNAVAILABLE"): 48,
        ("cloud_pipeline", "claude", "skipped", "local_unavailable", "unavailable", None): 48,
        ("cloud_pipeline", "codex", "skipped", "local_unavailable", "unavailable", None): 48,
        ("cloud_control", "claude", "unknown", "unknown", "unavailable", "CLAUDE_OUTPUT_SCHEMA_INVALID"): 12,
        ("cloud_control", "codex", "completed", "none", "passed", None): 11,
        ("cloud_control", "codex", "unknown", "unknown", "unavailable", None): 1,
    }
    assert all(
        record["cost"] == {"value": None, "provenance": "unavailable"}
        and record["test_outcome"] == {"value": None, "provenance": "unavailable"}
        and record["review_outcome"] == {"value": None, "provenance": "unavailable"}
        for record in records
    )


def test_diagnostic_attestation_is_bound_to_the_sanitized_evidence():
    attestation = json.loads(ATTESTATION.read_text(encoding="utf-8"))

    assert attestation["status"] == "completed_inconclusive_no_promotion"
    assert attestation["authorization_id"] == "d9ef49a4-d856-49d8-b48b-acf3df0de6b3"
    assert attestation["evidence"] == {
        "path": "plugins/foundry/benchmarks/foundry-45/evidence-v2/d9ef49a4-d856-49d8-b48b-acf3df0de6b3.jsonl",
        "sha256": EVIDENCE_SHA256,
        "records": 168,
        "checkpoint_chain": "validated",
    }
    assert attestation["matrix"]["local_product"] == {
        "planned": 48,
        "failed_local_scout_unavailable": 48,
    }
    assert attestation["matrix"]["cloud_pipeline"] == {
        "planned": 96,
        "started": 0,
        "skipped_local_unavailable": 96,
    }
    assert attestation["metrics"] == {
        name: {"value": None, "provenance": "unavailable"}
        for name in ("cost", "local_usage", "tests", "review")
    }
    assert attestation["conclusion"]["candidate_promotion"] == "prohibited"
    assert attestation["conclusion"]["routing_or_gate_mutation"] == "prohibited"
