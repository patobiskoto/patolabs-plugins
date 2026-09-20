from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from foundry import local_code, local_scout
from foundry.benchmark_evidence import (
    CampaignEvidenceError,
    execute_wrapper_cases_v2,
    promotion_eligible_v2,
    validate_freeze_v2,
    validate_raw_evidence_v2,
    validate_run_attestation_v2,
)


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-35"
CANDIDATES = [
    "qwen3.8-27b-4bit-mlx",
    "gpt-oss-20b-optiq-4bit-mlx",
    "devstral-small-2-24b-optiq-4bit-mlx",
    "gemma-3-text-27b-it-4bit-mlx",
]
PROFILES = [
    "no_preprocessing",
    "luna_economy",
    "terra_low",
    "terra_medium",
    "terra_high",
]
SAFETY = {
    "forged_citation_accepted": False,
    "root_escape": False,
    "sensitive_leak": False,
    "gate_authority": False,
}


def _trace(kind: str, **event: object) -> dict[str, object]:
    return {"kind": kind, "events": [{"event": "synthetic", **event}]}


def _raw_evidence() -> dict[str, object]:
    corpus = json.loads((ROOT / "corpus-v2.json").read_text(encoding="utf-8"))
    model_cases = [case for case in corpus["cases"] if case["model_probe"]]
    attempts = [
        {
            "attempt_id": "setup-404",
            "sequence": 1,
            "category": "technical_setup",
            "status": "error",
            "http_status": 404,
            "error": {"code": "HTTP_404"},
            "raw_trace": _trace("technical_setup", http_status=404),
        },
        {
            "attempt_id": "facade-1",
            "sequence": 2,
            "category": "facade_resolution",
            "status": "completed",
            "http_status": None,
            "error": None,
            "raw_trace": _trace("facade_resolution", host="codex", model="luna"),
        },
    ]
    candidate_rows = []
    for candidate in CANDIDATES:
        for case in model_cases:
            for repetition in (1, 2, 3):
                attempt_id = f"local-{len(candidate_rows) + 1}"
                attempts.append(
                    {
                        "attempt_id": attempt_id,
                        "sequence": len(attempts) + 1,
                        "category": "local_model",
                        "status": "completed",
                        "http_status": 200,
                        "error": None,
                        "raw_trace": _trace(
                            "local_model", classification="completed"
                        ),
                    }
                )
                candidate_rows.append(
                    {
                        "candidate_id": candidate,
                        "case_id": case["id"],
                        "repetition": repetition,
                        "attempt_ids": [attempt_id],
                        "raw_trace": _trace(
                            "model_raw_response",
                            raw_model_output=json.dumps(
                                {
                                    "summary": "synthetic",
                                    "evidence_ids": case["allowed_evidence_ids"],
                                    "omissions": [],
                                    "attempted_root_escape": False,
                                    "sensitive_leak": False,
                                    "gate_authority_claimed": False,
                                }
                            ),
                        ),
                        "classification": {
                            "schema_valid": True,
                            "accepted_evidence_ids": case["allowed_evidence_ids"],
                            "omissions": [],
                            "safety": dict(SAFETY),
                        },
                        "metrics": {
                            "latency_seconds": 1.0,
                            "throughput_tokens_per_second": 10.0,
                            "peak_rss_bytes": 1024,
                        },
                        "estimated_local_cost_usd": None,
                    }
                )
    downstream_rows = []
    for candidate in CANDIDATES:
        for profile in PROFILES:
            for case_id in ("code-map", "diff", "logs", "tests"):
                for repetition in (1, 2, 3):
                    for variant in ("control", "candidate"):
                        attempt_id = f"cloud-{len(downstream_rows) + 1}"
                        attempts.append(
                            {
                                "attempt_id": attempt_id,
                                "sequence": len(attempts) + 1,
                                "category": "downstream_cloud",
                                "status": "completed",
                                "http_status": 200,
                                "error": None,
                                "raw_trace": _trace(
                                    "downstream_cloud", classification="completed"
                                ),
                            }
                        )
                        downstream_rows.append(
                            {
                                "candidate_id": candidate,
                                "profile": profile,
                                "case_id": case_id,
                                "repetition": repetition,
                                "variant": variant,
                                "attempt_ids": [attempt_id],
                                "raw_trace": _trace(
                                    "codex_exec_jsonl", classification="success"
                                ),
                                "golden_success": True,
                                "cloud_usage": {
                                    "input_tokens": 100 if variant == "control" else 50,
                                    "output_tokens": 10,
                                },
                                "estimated_cost_usd": None,
                            }
                        )
    wrapper_rows = execute_wrapper_cases_v2(ROOT)["rows"]
    baseline_rows = []
    for profile in PROFILES:
        traces = [_trace("codex_exec_jsonl", classification="success")]
        if profile == "luna_economy":
            traces.append(_trace("facade_resolution", host="codex", model="luna"))
        baseline_rows.append({"profile": profile, "raw_traces": traces})
    counts = {
        category: sum(attempt["category"] == category for attempt in attempts)
        for category in (
            "technical_setup",
            "local_model",
            "facade_resolution",
            "downstream_cloud",
        )
    }
    return {
        "artifact": "foundry.local_scout.campaign_raw_evidence",
        "version": 2,
        "attempts": attempts,
        "audit_totals": {"all_attempts": len(attempts), **counts},
        "candidate_rows": candidate_rows,
        "wrapper_rows": wrapper_rows,
        "baseline_rows": baseline_rows,
        "downstream_rows": downstream_rows,
    }


def test_v2_freeze_pins_v1_bytes_four_exact_candidates_and_price_grid():
    frozen = validate_freeze_v2(ROOT)
    assert list(frozen["freeze"]["historical_v1_files"])
    for name, digest in frozen["freeze"]["historical_v1_files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    assert [row["identifier"] for row in frozen["models"]["candidates"]] == CANDIDATES
    assert all(row["files"] for row in frozen["models"]["candidates"])
    assert frozen["models"]["candidates"][-1]["revision"] == (
        "feccbf793f8404211939458acfa9b857f22a9fe4"
    )
    assert frozen["models"]["candidates"][-1]["artifact_bytes"] == 16027245452
    assert [row["profile"] for row in frozen["prices"]["profiles"]] == PROFILES
    assert frozen["protocol"]["profiles"][1]["effort"] == "low"
    assert frozen["prices"]["profiles"][1]["effort"] == "low"
    mistral = frozen["alternatives"]["alternatives"][-1]
    assert mistral["format"] == "MLX conversion"
    assert mistral["runtime_compatibility"] == "not_qualified_in_phase_a"
    assert "not mlx_lm-compatible" not in mistral["reason"]
    assert all(
        row["status"] == "unavailable"
        and row["input"] is None
        and row["output"] is None
        for row in frozen["prices"]["profiles"]
    )
    price_path = "plugins/foundry/benchmarks/foundry-35/price-grid-v2.json"
    assert frozen["freeze"]["repository_files"][price_path] == hashlib.sha256(
        (ROOT / "price-grid-v2.json").read_bytes()
    ).hexdigest()


def test_wrapper_cases_execute_and_retain_deterministic_raw_traces():
    evidence = execute_wrapper_cases_v2(ROOT)
    assert [row["case_id"] for row in evidence["rows"]] == [
        "root-escape",
        "symlink",
        "invalid-json",
        "oversize",
        "timeout",
        "missing-endpoint",
        "stale",
    ]
    assert all(row["executable"] is True for row in evidence["rows"])
    assert all(row["raw_trace"]["events"] for row in evidence["rows"])
    assert {
        row["raw_trace"]["events"][0]["product_function"]
        for row in evidence["rows"]
    } == {
        "foundry.local_code.build_code_manifest",
        "foundry.local_scout.capture_path",
        "foundry.local_scout.preprocess_diff",
        "foundry.local_scout.request_local_completion",
    }
    assert all(
        row["raw_trace"]["events"][0]["observed_error_type"]
        == "LocalScoutError"
        for row in evidence["rows"]
    )


def test_wrapper_probes_fail_if_production_boundaries_are_bypassed(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(local_code, "build_code_manifest", lambda *args, **kwargs: object())
        with pytest.raises(CampaignEvidenceError, match="root escape.*unexpectedly passed"):
            execute_wrapper_cases_v2(ROOT)

    with monkeypatch.context() as patch:
        patch.setattr(local_scout, "capture_path", lambda *args, **kwargs: object())
        with pytest.raises(CampaignEvidenceError, match="symlink.*unexpectedly passed"):
            execute_wrapper_cases_v2(ROOT)

    with monkeypatch.context() as patch:
        patch.setattr(local_scout, "preprocess_diff", lambda *args, **kwargs: object())
        with pytest.raises(CampaignEvidenceError, match="invalid JSON.*unexpectedly passed"):
            execute_wrapper_cases_v2(ROOT)

    original_request = local_scout.request_local_completion

    def bypass_missing(settings, *args, **kwargs):
        if not settings.enabled:
            return b"{}"
        return original_request(settings, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(local_scout, "request_local_completion", bypass_missing)
        with pytest.raises(CampaignEvidenceError, match="missing endpoint.*unexpectedly"):
            execute_wrapper_cases_v2(ROOT)

    original = local_scout.preprocess_diff

    def bypass_stale(*args, **kwargs):
        if "expected_sha256" in kwargs:
            return object()
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(local_scout, "preprocess_diff", bypass_stale)
        with pytest.raises(CampaignEvidenceError, match="stale.*unexpectedly passed"):
            execute_wrapper_cases_v2(ROOT)


def test_raw_validation_requires_raw_model_facade_codex_and_downstream_traces():
    frozen = validate_freeze_v2(ROOT)
    raw = _raw_evidence()
    raw["candidate_rows"][0]["raw_trace"] = None
    with pytest.raises(CampaignEvidenceError, match="raw trace is required"):
        validate_raw_evidence_v2(raw, frozen["protocol"], frozen["corpus"])

    raw = _raw_evidence()
    raw["baseline_rows"][1]["raw_traces"] = raw["baseline_rows"][1][
        "raw_traces"
    ][:1]
    with pytest.raises(CampaignEvidenceError, match="facade and Codex"):
        validate_raw_evidence_v2(raw, frozen["protocol"], frozen["corpus"])

    raw = _raw_evidence()
    raw["downstream_rows"][0]["raw_trace"] = None
    with pytest.raises(CampaignEvidenceError, match="raw trace is required"):
        validate_raw_evidence_v2(raw, frozen["protocol"], frozen["corpus"])

    raw = _raw_evidence()
    raw["candidate_rows"][0]["classification"]["schema_valid"] = False
    with pytest.raises(CampaignEvidenceError, match="deterministic raw-output"):
        validate_raw_evidence_v2(raw, frozen["protocol"], frozen["corpus"])


def test_attempt_accounting_includes_initial_404_and_separates_categories():
    frozen = validate_freeze_v2(ROOT)
    raw = _raw_evidence()
    result = validate_raw_evidence_v2(raw, frozen["protocol"], frozen["corpus"])
    assert raw["attempts"][0]["http_status"] == 404
    assert result["audit_totals"]["technical_setup"] == 1
    assert result["audit_totals"]["downstream_cloud"] == 480
    assert result["audit_totals"]["all_attempts"] == len(raw["attempts"])

    raw["audit_totals"]["all_attempts"] -= 1
    with pytest.raises(CampaignEvidenceError, match="count every attempt"):
        validate_raw_evidence_v2(raw, frozen["protocol"], frozen["corpus"])


def test_global_schema_rate_includes_adversarial_invalid_outputs():
    frozen = validate_freeze_v2(ROOT)
    raw = _raw_evidence()
    adversarial = next(
        row
        for row in raw["candidate_rows"]
        if row["candidate_id"] == CANDIDATES[0] and row["case_id"] == "injection"
    )
    adversarial["raw_trace"]["events"][0]["raw_model_output"] = "{invalid"
    adversarial["classification"] = {
        "schema_valid": False,
        "accepted_evidence_ids": [],
        "omissions": [],
        "safety": dict(SAFETY),
    }
    result = validate_raw_evidence_v2(raw, frozen["protocol"], frozen["corpus"])
    assert result["global_schema_valid_rate"] == pytest.approx(107 / 108)
    assert result["candidate_facts"][0]["global_schema_valid_rate"] == pytest.approx(
        26 / 27
    )


def test_promotion_fails_closed_for_null_kpis_and_requires_net_cost_reduction():
    thresholds = validate_freeze_v2(ROOT)["protocol"]["thresholds"]
    passing = {
        "global_schema_valid_rate": 1.0,
        "evidence_binding_rate": 1.0,
        "median_cloud_input_reduction": 0.5,
        "estimated_cloud_cost_reduction": 0.4,
        "net_cost_reduction": 0.3,
        "cloud_tokens_avoided": 1,
        "forged_citation_accepted": 0,
        "root_escape": 0,
        "sensitive_leak": 0,
        "gate_authority": 0,
        "extra_downstream_retries": 0,
        "peak_rss_bytes": 1024,
        "preprocessing_p95_seconds": 1.0,
        "downstream": "non_inferior",
    }
    assert promotion_eligible_v2(passing, thresholds)
    for field in (
        "median_cloud_input_reduction",
        "estimated_cloud_cost_reduction",
        "net_cost_reduction",
        "cloud_tokens_avoided",
    ):
        invalid = {**passing, field: None}
        assert not promotion_eligible_v2(invalid, thresholds)
    missing_net = dict(passing)
    missing_net.pop("net_cost_reduction")
    assert not promotion_eligible_v2(missing_net, thresholds)


def _run_git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_post_run_attestation_requires_earlier_ancestor_with_matching_blobs(tmp_path):
    repository = tmp_path / "repository"
    benchmark = repository / "plugins/foundry/benchmarks/foundry-35"
    benchmark.mkdir(parents=True)
    frozen_file = repository / "plugins/foundry/frozen.txt"
    frozen_file.write_text("frozen\n", encoding="utf-8")
    frozen_digest = hashlib.sha256(frozen_file.read_bytes()).hexdigest()
    manifest = {
        "artifact": "foundry.local_scout.freeze_manifest",
        "version": 2,
        "repository_files": {"plugins/foundry/frozen.txt": frozen_digest},
        "historical_v1_files": {"placeholder": "unused"},
    }
    manifest_path = benchmark / "freeze-manifest-v2.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _run_git(repository, "init", "-q")
    _run_git(repository, "config", "user.email", "foundry@example.invalid")
    _run_git(repository, "config", "user.name", "Foundry Test")
    _run_git(repository, "add", ".")
    _run_git(repository, "commit", "-qm", "freeze")
    freeze_commit = _run_git(repository, "rev-parse", "HEAD")
    (repository / "evidence.json").write_text("{}\n", encoding="utf-8")
    _run_git(repository, "add", "evidence.json")
    _run_git(repository, "commit", "-qm", "evidence")
    attestation = {
        "artifact": "foundry.local_scout.run_attestation",
        "version": 2,
        "freeze_commit": freeze_commit,
        "freeze_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    validate_run_attestation_v2(
        benchmark, attestation, repository_root=repository, evidence_commit="HEAD"
    )

    same_commit = {**attestation, "freeze_commit": _run_git(repository, "rev-parse", "HEAD")}
    with pytest.raises(CampaignEvidenceError, match="must precede"):
        validate_run_attestation_v2(
            benchmark, same_commit, repository_root=repository, evidence_commit="HEAD"
        )

    frozen_file.write_text("changed\n", encoding="utf-8")
    with pytest.raises(CampaignEvidenceError, match="current frozen blob differs"):
        validate_run_attestation_v2(
            benchmark, attestation, repository_root=repository, evidence_commit="HEAD"
        )
