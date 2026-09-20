from __future__ import annotations

import hashlib
import importlib.util
import json
import copy
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-35"
SPEC = importlib.util.spec_from_file_location(
    "foundry35_benchmark_evidence_v3", ROOT / "benchmark-evidence-v3.py"
)
assert SPEC is not None and SPEC.loader is not None
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)
HARNESS_SPEC = importlib.util.spec_from_file_location(
    "foundry35_run_phase_b_v3", ROOT / "run-phase-b-v3.py"
)
assert HARNESS_SPEC is not None and HARNESS_SPEC.loader is not None
HARNESS = importlib.util.module_from_spec(HARNESS_SPEC)
HARNESS_SPEC.loader.exec_module(HARNESS)

CANDIDATES = EVIDENCE.CANDIDATES
PROFILES = EVIDENCE.PROFILES
PROMOTION_CASES = EVIDENCE.PROMOTION_CASES
MODEL_CASES = EVIDENCE.MODEL_CASES
SAFETY = {field: False for field in EVIDENCE.SAFETY_FIELDS}


def _trace(kind: str, **event: object) -> dict[str, object]:
    return {"kind": kind, "events": [{"event": "synthetic", **event}]}


def _usage(input_count: int | None = None) -> dict[str, object]:
    return {
        "input_tokens": input_count,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": 100 if input_count is None else input_count + 10,
        "provenance": "host_reported",
    }


def _product_packet(case_id: str, markers: list[str]) -> dict[str, object]:
    evidence = [
        {
            "evidence_id": f"ev_{index:024x}",
            "locator": f"{case_id}:L{index}-L{index}",
            "sha256": f"{index:064x}",
            "raw_excerpt": f"EVIDENCE {marker}: synthetic\n",
        }
        for index, marker in enumerate(markers, start=1)
    ]
    if case_id == "code-map":
        for item in evidence:
            item["path"] = "src/retry_budget.py"
        kind = "untrusted_local_code_proposals"
        authority = "proposal_only_cloud_role_must_judge_raw_evidence"
    else:
        kind = f"untrusted_local_{case_id}_proposals"
        authority = "proposal_only_cloud_role_must_judge"
    ids = [item["evidence_id"] for item in evidence]
    return {
        "schema_version": 1,
        "kind": kind,
        "source": "local",
        "untrusted": True,
        "model": "default_model",
        "provenance": {"synthetic": True},
        "final_evidence_ids": ids,
        "hypotheses": [
            {
                "summary": "synthetic",
                "evidence_ids": ids,
                "evidence": evidence,
            }
        ],
        "authority": authority,
    }


def _raw_evidence(*, split_tokens: bool = False) -> dict[str, object]:
    corpus = json.loads((ROOT / "corpus-v3.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in corpus["cases"]}
    attempts = []

    def add_attempt(
        category: str,
        *,
        status: str = "completed",
        http_status: int | None = 200,
        campaign_version: int = 3,
    ) -> str:
        attempt_id = f"attempt-{len(attempts) + 1:04d}"
        attempts.append(
            {
                "attempt_id": attempt_id,
                "sequence": len(attempts) + 1,
                "category": category,
                "campaign_version": campaign_version,
                "status": status,
                "http_status": http_status,
                "error": {"code": "HTTP_404"} if status == "error" else None,
                "raw_trace": _trace(category, outcome=status),
            }
        )
        return attempt_id

    for _ in range(51):
        add_attempt(
            "technical_setup",
            status="error",
            http_status=404,
            campaign_version=1,
        )

    intrinsic_rows = []
    for candidate in CANDIDATES:
        for case_id in MODEL_CASES:
            case = cases[case_id]
            for repetition in (1, 2, 3):
                attempt_id = add_attempt("local_model")
                raw_output = json.dumps(
                    {
                        "summary": "synthetic",
                        "evidence_ids": case["allowed_evidence_ids"],
                        "omissions": [],
                        "attempted_root_escape": False,
                        "sensitive_leak": False,
                        "gate_authority_claimed": False,
                    },
                    separators=(",", ":"),
                )
                intrinsic_rows.append(
                    {
                        "candidate_id": candidate,
                        "case_id": case_id,
                        "repetition": repetition,
                        "attempt_ids": [attempt_id],
                        "raw_trace": _trace(
                            "model_raw_response", raw_model_output=raw_output
                        ),
                        "classification": EVIDENCE.classify_model_output_v3(
                            raw_output, case["allowed_evidence_ids"]
                        ),
                        "metrics": {
                            "latency_seconds": 1.0,
                            "throughput_tokens_per_second": 10.0,
                            "peak_rss_bytes": 1024,
                        },
                        "estimated_local_cost_usd": None,
                    }
                )

    product_rows = []
    product_functions = {
        "code-map": "foundry.local_code.run_code_map",
        "diff": "foundry.local_scout.preprocess_diff",
        "logs": "foundry.local_scout.preprocess_logs",
        "tests": "foundry.local_scout.preprocess_tests",
    }
    for candidate in CANDIDATES:
        for case_id in PROMOTION_CASES:
            markers = cases[case_id]["allowed_evidence_ids"]
            for repetition in (1, 2, 3):
                attempt_id = add_attempt("local_model")
                packet = _product_packet(case_id, markers)
                product_rows.append(
                    {
                        "candidate_id": candidate,
                        "case_id": case_id,
                        "repetition": repetition,
                        "product_function": product_functions[case_id],
                        "attempt_ids": [attempt_id],
                        "raw_http_trace": _trace(
                            "product_http_boundary",
                            raw_response_utf8="{synthetic}",
                            raw_response_provenance=(
                                "recorded_at_product_http_connection_boundary"
                            ),
                        ),
                        "cloud_packet": packet,
                        "classification": EVIDENCE.classify_product_packet(
                            packet, case_id, markers
                        ),
                        "metrics": {
                            "latency_seconds": 1.0,
                            "throughput_tokens_per_second": None,
                            "peak_rss_bytes": 1024,
                        },
                        "estimated_local_cost_usd": None,
                    }
                )

    baseline_rows = []
    for profile in PROFILES:
        for case_id in PROMOTION_CASES:
            for repetition in (1, 2, 3):
                attempt_id = add_attempt("downstream_cloud")
                traces = [_trace("codex_exec_jsonl", outcome="success")]
                facade_attempt_ids = []
                if profile == "luna_economy":
                    facade_attempt_ids = [add_attempt("facade_resolution")]
                    traces.append(
                        _trace(
                            "facade_resolution",
                            host="codex",
                            model="gpt-5.6-luna",
                            effort="low",
                        )
                    )
                baseline_rows.append(
                    {
                        "row_id": f"baseline/{profile}/{case_id}/{repetition}",
                        "profile": profile,
                        "case_id": case_id,
                        "repetition": repetition,
                        "attempt_ids": [attempt_id],
                        "facade_attempt_ids": facade_attempt_ids,
                        "raw_traces": traces,
                        "golden_success": True,
                        "cloud_usage": _usage(100 if split_tokens else None),
                        "estimated_cost_usd": None,
                    }
                )

    candidate_pipeline_rows = []
    for candidate in CANDIDATES:
        for case_id in PROMOTION_CASES:
            for repetition in (1, 2, 3):
                attempt_id = add_attempt("downstream_cloud")
                candidate_pipeline_rows.append(
                    {
                        "candidate_id": candidate,
                        "profile": "terra_medium",
                        "case_id": case_id,
                        "repetition": repetition,
                        "control_row_id": (
                            f"baseline/terra_medium/{case_id}/{repetition}"
                        ),
                        "product_row_key": f"{candidate}/{case_id}/{repetition}",
                        "attempt_ids": [attempt_id],
                        "raw_trace": _trace(
                            "codex_exec_jsonl",
                            model="gpt-5.6-terra",
                            effort="medium",
                            outcome="success",
                        ),
                        "golden_success": True,
                        "cloud_usage": _usage(50 if split_tokens else None),
                        "estimated_cost_usd": None,
                    }
                )

    wrapper_rows = [
        {
            "case_id": case_id,
            "classification": {"status": "rejected", "error_code": error},
            "raw_trace": _trace("wrapper_probe", observed_error_code=error),
            "executable": True,
        }
        for case_id, error in EVIDENCE.WRAPPER_ERRORS.items()
    ]
    counts = {
        category: sum(attempt["category"] == category for attempt in attempts)
        for category in EVIDENCE.ATTEMPT_CATEGORIES
    }
    return {
        "artifact": "foundry.local_scout.campaign_raw_evidence",
        "version": 3,
        "attempts": attempts,
        "audit_totals": {"all_attempts": len(attempts), **counts},
        "intrinsic_rows": intrinsic_rows,
        "product_rows": product_rows,
        "wrapper_rows": wrapper_rows,
        "baseline_rows": baseline_rows,
        "candidate_pipeline_rows": candidate_pipeline_rows,
    }


def test_v3_freeze_replaces_gemma_and_factors_cloud_matrix_without_v2_mutation():
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    assert [row["identifier"] for row in frozen["models"]["candidates"]] == CANDIDATES
    gemma = frozen["models"]["candidates"][-1]
    assert gemma["revision"] == "e0061bda54f72709cf6fa51229530c3b14cd9d7d"
    assert gemma["artifact_bytes"] == 18807827816
    assert gemma["lfs_bytes"] == 18807520609
    assert len(gemma["files"]) == 6
    cloud = frozen["protocol"]["cloud_matrix"]
    assert frozen["protocol"]["intrinsic_matrix"]["executions"] == 108
    assert frozen["protocol"]["intrinsic_matrix"]["promotion_authority"] is False
    assert frozen["protocol"]["product_promotion_matrix"]["executions"] == 48
    assert frozen["protocol"]["product_promotion_matrix"]["promotion_authority"] is True
    assert cloud["common_baseline"]["executions"] == 60
    assert cloud["candidate_pipeline"]["executions"] == 48
    assert cloud["shared_terra_medium_controls"] == 12
    assert cloud["total_executions"] == 108
    assert frozen["supersession"]["superseded_campaign"]["freeze_commit"] == (
        "9ab933ac244a6cd9b594e64342bd167be5e7a4e4"
    )
    assert frozen["rationale"]["external_cache_plan"]["status"] == (
        "planned_not_created_in_phase_a"
    )
    assert frozen["rationale"]["tested_results"] == []
    assert frozen["protocol"]["runner"] == "run-phase-b-v3.py"
    harness_name = "plugins/foundry/benchmarks/foundry-35/run-phase-b-v3.py"
    assert frozen["manifest"]["repository_files"][harness_name] == hashlib.sha256(
        (ROOT / "run-phase-b-v3.py").read_bytes()
    ).hexdigest()


def test_v3_historical_validation_never_reads_substituted_worktree_inputs(monkeypatch):
    def substituted_worktree(*_args, **_kwargs):
        raise AssertionError("historical validation must not read a worktree JSON input")

    monkeypatch.setattr(EVIDENCE, "_load", substituted_worktree)
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    assert frozen["protocol"]["version"] == 3


def test_v3_historical_protocol_blob_substitution_fails_its_frozen_digest(monkeypatch):
    original_git = EVIDENCE._git
    protocol_ref = (
        f"{EVIDENCE.V3_FREEZE_COMMIT}:"
        "plugins/foundry/benchmarks/foundry-35/protocol-v3.json"
    )

    def substituted_git(repository, *arguments):
        result = original_git(repository, *arguments)
        if arguments == ("show", protocol_ref):
            value = json.loads(result)
            value["coordinated_worktree_substitution"] = True
            return json.dumps(value, separators=(",", ":")).encode()
        return result

    monkeypatch.setattr(EVIDENCE, "_git", substituted_git)
    with pytest.raises(EVIDENCE.CampaignEvidenceV3Error, match="historical freeze digest differs"):
        EVIDENCE.validate_freeze_v3(ROOT)


def test_v3_missing_historical_blob_fails_closed(monkeypatch):
    original_git = EVIDENCE._git
    missing_ref = (
        f"{EVIDENCE.V3_FREEZE_COMMIT}:"
        "plugins/foundry/benchmarks/foundry-35/goldens-v3.json"
    )

    def missing_git(repository, *arguments):
        if arguments == ("show", missing_ref):
            raise EVIDENCE.CampaignEvidenceV3Error("git verification failed: frozen blob absent")
        return original_git(repository, *arguments)

    monkeypatch.setattr(EVIDENCE, "_git", missing_git)
    with pytest.raises(EVIDENCE.CampaignEvidenceV3Error, match="frozen blob absent"):
        EVIDENCE.validate_freeze_v3(ROOT)


def test_v3_harness_plan_matches_frozen_counts_and_reuses_twelve_controls():
    protocol = EVIDENCE.validate_freeze_v3(ROOT)["protocol"]
    plan = HARNESS.build_execution_plan(protocol)
    assert len(plan["intrinsic"]) == 108
    assert len(plan["product"]) == 48
    assert len(plan["baseline"]) == 60
    assert len(plan["pipeline"]) == 48
    assert len({row["control_row_id"] for row in plan["pipeline"]}) == 12
    assert all(row["profile"] == "terra_medium" for row in plan["pipeline"])
    baseline = protocol["cloud_matrix"]["common_baseline"]["profiles"][0]
    assert baseline["model"] == "gpt-5.6-sol"
    assert baseline["effort"] == "high"
    invalid = copy.deepcopy(protocol)
    invalid["cloud_matrix"]["common_baseline"]["profiles"][0]["model"] = None
    with pytest.raises(HARNESS.PhaseBGuardError, match="must be explicit"):
        HARNESS.build_execution_plan(invalid)


def test_v3_harness_cache_must_be_explicitly_confined_to_external_root():
    assert HARNESS.require_external_cache_path(
        "/Volumes/Data/FoundryModels/foundry-35"
    ) == Path("/Volumes/Data/FoundryModels/foundry-35")
    assert HARNESS.require_external_cache_path(
        "/Volumes/Data/FoundryModels/foundry-35/operator-a"
    ) == Path("/Volumes/Data/FoundryModels/foundry-35/operator-a")
    with pytest.raises(HARNESS.PhaseBGuardError, match="must be under"):
        HARNESS.require_external_cache_path("/tmp/foundry-35")


def test_v3_harness_refuses_all_action_before_preflight(monkeypatch, tmp_path):
    called = []

    def reject(*args, **kwargs):
        called.append("guard")
        raise HARNESS.PhaseBGuardError("no committed ancestor")

    monkeypatch.setattr(HARNESS, "validate_phase_b_preflight", reject)
    monkeypatch.setattr(
        HARNESS,
        "qualify_candidate_snapshots",
        lambda *args, **kwargs: called.append("qualification"),
    )
    output = tmp_path / "raw.json"
    with pytest.raises(HARNESS.PhaseBGuardError, match="committed ancestor"):
        HARNESS.execute_campaign(
            authorization={},
            external_cache="/Volumes/Data/FoundryModels/foundry-35",
            enable_cloud=True,
            output=output,
            aggregate_output=tmp_path / "aggregate.json",
        )
    assert called == ["guard"]
    assert not output.exists()


def test_v3_harness_preflight_requires_ancestor_digests_and_cloud_opt_in(
    monkeypatch, tmp_path
):
    benchmark = tmp_path / "plugins/foundry/benchmarks/foundry-35"
    benchmark.mkdir(parents=True)
    manifest_path = benchmark / "freeze-manifest-v3.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    freeze_commit = "a" * 40
    head = "b" * 40
    frozen = {"manifest": {"repository_files": {}}}
    monkeypatch.setattr(HARNESS.EVIDENCE, "validate_freeze_v3", lambda value: frozen)

    def fake_git(repository, *arguments):
        if arguments == ("rev-parse", "HEAD^{commit}"):
            return f"{head}\n".encode()
        if arguments[:2] == ("show", f"{freeze_commit}:plugins/foundry/benchmarks/foundry-35/freeze-manifest-v3.json"):
            return manifest_path.read_bytes()
        if arguments == ("show", "-s", "--format=%cI", freeze_commit):
            return b"2026-08-22T00:00:00+02:00\n"
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return b""
        raise AssertionError(arguments)

    monkeypatch.setattr(HARNESS, "_git", fake_git)
    authorization = {
        "artifact": "foundry.local_scout.phase_b_authorization",
        "version": 3,
        "freeze_commit": freeze_commit,
        "freeze_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "superseded_v2_commit": HARNESS.EVIDENCE.SUPERSEDED_V2_COMMIT,
        "external_cache": "/Volumes/Data/FoundryModels/foundry-35",
        "cloud_opt_in": True,
        "authorized_at": "2026-08-22T01:00:00+02:00",
    }
    result = HARNESS.validate_phase_b_preflight(
        authorization,
        external_cache=authorization["external_cache"],
        enable_cloud=True,
        repository=tmp_path,
        benchmark=benchmark,
    )
    assert result["freeze_commit"] == freeze_commit
    with pytest.raises(HARNESS.PhaseBGuardError, match="explicit operator opt-in"):
        HARNESS.validate_phase_b_preflight(
            authorization,
            external_cache=authorization["external_cache"],
            enable_cloud=False,
            repository=tmp_path,
            benchmark=benchmark,
        )


def test_v3_harness_downloads_only_exact_gemma_to_external_cache(
    monkeypatch, tmp_path
):
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return str(tmp_path / "gemma-snapshot")

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_download)
    monkeypatch.setattr(HARNESS, "verify_snapshot", lambda *args, **kwargs: None)
    ledger = HARNESS.AttemptLedger()
    snapshots = HARNESS.qualify_candidate_snapshots(
        frozen["models"]["candidates"], tmp_path, ledger
    )
    assert len(calls) == 1
    assert calls[0] == {
        "repo_id": "mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit",
        "revision": "e0061bda54f72709cf6fa51229530c3b14cd9d7d",
        "local_dir": str(tmp_path / HARNESS.GEMMA_LOCAL_DIR_NAME),
    }
    assert "cache_dir" not in calls[0]
    assert snapshots[CANDIDATES[-1]] == tmp_path / HARNESS.GEMMA_LOCAL_DIR_NAME
    assert len(ledger.rows) == 4
    assert all(row["category"] == "technical_setup" for row in ledger.rows)


def test_v3_external_gemma_verification_rejects_symlinked_files(tmp_path):
    source = tmp_path / "actual.bin"
    source.write_bytes(b"synthetic")
    snapshot = tmp_path / "external"
    snapshot.mkdir()
    (snapshot / "model.bin").symlink_to(source)
    candidate = {
        "identifier": CANDIDATES[-1],
        "files": [
            {
                "path": "model.bin",
                "size": len(b"synthetic"),
                "sha256": hashlib.sha256(b"synthetic").hexdigest(),
            }
        ],
    }
    with pytest.raises(RuntimeError, match="artifact mismatch"):
        HARNESS.verify_snapshot(candidate, snapshot, require_regular=True)


def test_v3_product_dispatch_uses_real_product_boundaries(monkeypatch, tmp_path):
    tooling = ROOT.parents[1] / "tooling"
    import sys

    sys.path.insert(0, str(tooling))
    from foundry import local_code, local_scout

    fixtures = json.loads(
        (ROOT / "product-fixtures-v3.json").read_text(encoding="utf-8")
    )["cases"]
    calls = []

    def fake_packet(result, **kwargs):
        calls.append(("packet", result, kwargs))
        return {"result": result}

    for case_id, name in (
        ("diff", "preprocess_diff"),
        ("logs", "preprocess_logs"),
        ("tests", "preprocess_tests"),
    ):
        calls.clear()
        monkeypatch.setattr(
            local_scout,
            name,
            lambda value, settings, **kwargs: (case_id, value),
        )
        monkeypatch.setattr(local_scout, "cloud_packet", fake_packet)
        result = HARNESS._product_api_call(
            case_id, fixtures[case_id], object(), HARNESS.ProductHttpRecorder()
        )
        assert result["result"] == (case_id, fixtures[case_id]["input"])
        assert calls[0][0] == "packet"

    monkeypatch.setattr(
        local_code,
        "run_code_map",
        lambda goal, root, signals, settings, **kwargs: (goal, signals),
    )
    monkeypatch.setattr(local_code, "code_cloud_packet", fake_packet)
    result = HARNESS._product_api_call(
        "code-map", fixtures["code-map"], object(), HARNESS.ProductHttpRecorder()
    )
    assert result["result"][0] == fixtures["code-map"]["goal"]


def test_v3_harness_owned_cleanup_targets_only_its_process_group(monkeypatch):
    signals = []

    class FakeProcess:
        pid = 4242
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout):
            self.returncode = -15

    monkeypatch.setattr(
        HARNESS.os, "killpg", lambda process_id, value: signals.append((process_id, value))
    )

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def connect_ex(self, value):
            return 1

    monkeypatch.setattr(HARNESS.socket, "socket", lambda *args, **kwargs: FakeSocket())
    result = HARNESS.terminate_owned_process(FakeProcess(), 45678)
    assert signals == [(4242, HARNESS.signal.SIGTERM)]
    assert result["owned_listener_remains"] is False


def test_v3_harness_contains_no_cache_deletion_or_default_mutation_calls():
    source = (ROOT / "run-phase-b-v3.py").read_text(encoding="utf-8")
    for forbidden in (
        "shutil.rmtree",
        ".unlink(",
        "os.remove",
        "git reset",
        "git checkout",
        "routing edit",
        "foundry_cli.py edit",
    ):
        assert forbidden not in source
    assert "_materialized_context" not in source
    assert 'product_row["cloud_packet"]' in source
    for product_function in (
        "local_scout.preprocess_diff",
        "local_scout.preprocess_logs",
        "local_scout.preprocess_tests",
        "local_code.run_code_map",
        "local_code.code_cloud_packet",
    ):
        assert product_function in source


def test_v3_harness_digest_mutation_invalidates_freeze(tmp_path):
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    repository = tmp_path / "repository"
    for relative in frozen["manifest"]["repository_files"]:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            subprocess.run(
                ["git", "show", f"{EVIDENCE.V3_FREEZE_COMMIT}:{relative}"],
                cwd=ROOT.parents[3], check=True, capture_output=True,
            ).stdout
        )
    EVIDENCE.validate_repository_files_v3(frozen["manifest"], repository)
    harness = (
        repository
        / "plugins/foundry/benchmarks/foundry-35/run-phase-b-v3.py"
    )
    harness.write_text(harness.read_text(encoding="utf-8") + "# mutation\n")
    with pytest.raises(EVIDENCE.CampaignEvidenceV3Error, match="freeze digest differs"):
        EVIDENCE.validate_repository_files_v3(frozen["manifest"], repository)


def test_v3_archive_validation_uses_git_snapshot_but_current_worktree_fails():
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    with pytest.raises(EVIDENCE.CampaignEvidenceV3Error, match="freeze digest differs"):
        EVIDENCE.validate_repository_files_v3(frozen["manifest"], ROOT.parents[3])


def test_v3_raw_recompute_uses_60_plus_48_and_fails_closed_on_null_costs():
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    result = EVIDENCE.validate_raw_evidence_v3(
        _raw_evidence(split_tokens=True), frozen["protocol"], frozen["corpus"]
    )
    assert result["cloud_matrix"] == {
        "baseline_rows": 60,
        "candidate_pipeline_rows": 48,
        "shared_terra_medium_controls": 12,
        "total_cloud_executions": 108,
    }
    assert result["audit_totals"]["technical_setup"] == 51
    assert result["audit_totals"]["downstream_cloud"] == 108
    assert all(
        row["median_cloud_input_reduction"] == 0.5
        and row["cloud_tokens_avoided"] == 600
        and row["estimated_cloud_cost_reduction"] is None
        and row["net_cost_reduction"] is None
        and row["promotion_eligible"] is False
        for row in result["candidate_results"]
    )


def test_v3_total_only_usage_keeps_token_and_cost_kpis_null():
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    result = EVIDENCE.validate_raw_evidence_v3(
        _raw_evidence(), frozen["protocol"], frozen["corpus"]
    )
    for row in result["candidate_results"]:
        assert row["median_cloud_input_reduction"] is None
        assert row["cloud_tokens_avoided"] is None
        assert row["estimated_cloud_cost_reduction"] is None
        assert row["net_cost_reduction"] is None
        assert row["promotion_eligible"] is False


def test_v3_global_schema_includes_adversarial_rows_and_is_recomputed():
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    raw = _raw_evidence()
    row = raw["wrapper_rows"][0]
    row["classification"]["error_code"] = "LOCAL_SCOUT_UNAVAILABLE"
    with pytest.raises(EVIDENCE.CampaignEvidenceV3Error, match="wrapper execution"):
        EVIDENCE.validate_raw_evidence_v3(
            raw, frozen["protocol"], frozen["corpus"]
        )
    raw = _raw_evidence()
    product = raw["product_rows"][0]
    product["cloud_packet"]["model"] = "wrong"
    product["classification"] = EVIDENCE.classify_product_packet(
        product["cloud_packet"], "code-map", ["CM-1", "CM-2", "CM-3"]
    )
    result = EVIDENCE.validate_raw_evidence_v3(
        raw, frozen["protocol"], frozen["corpus"]
    )
    assert result["global_schema_valid_rate"] == pytest.approx(75 / 76)
    assert result["candidate_results"][0]["global_schema_valid_rate"] == pytest.approx(
        18 / 19
    )


def test_v3_rejects_duplicate_control_execution_or_wrong_shared_pair():
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    raw = _raw_evidence()
    raw["candidate_pipeline_rows"][0]["control_row_id"] = (
        "baseline/terra_medium/diff/1"
    )
    with pytest.raises(
        EVIDENCE.CampaignEvidenceV3Error, match="reuse exact shared control"
    ):
        EVIDENCE.validate_raw_evidence_v3(
            raw, frozen["protocol"], frozen["corpus"]
        )


def test_v3_requires_all_51_historical_v1_404_attempts():
    frozen = EVIDENCE.validate_freeze_v3(ROOT)
    raw = _raw_evidence()
    raw["attempts"][0]["campaign_version"] = 3
    with pytest.raises(EVIDENCE.CampaignEvidenceV3Error, match="51 historical"):
        EVIDENCE.validate_raw_evidence_v3(
            raw, frozen["protocol"], frozen["corpus"]
        )


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_v3_attestation_requires_freeze_ancestor_before_first_operation(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    benchmark = repository / "plugins/foundry/benchmarks/foundry-35"
    benchmark.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "foundry@example.invalid")
    _git(repository, "config", "user.name", "Foundry Test")
    (repository / "v2.txt").write_text("historical\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "v2")
    v2_commit = _git(repository, "rev-parse", "HEAD")
    monkeypatch.setattr(EVIDENCE, "SUPERSEDED_V2_COMMIT", v2_commit)
    frozen_file = repository / "plugins/foundry/frozen-v3.txt"
    frozen_file.parent.mkdir(parents=True, exist_ok=True)
    frozen_file.write_text("frozen-v3\n", encoding="utf-8")
    digest = hashlib.sha256(frozen_file.read_bytes()).hexdigest()
    manifest = {
        "artifact": "foundry.local_scout.freeze_manifest",
        "version": 3,
        "repository_files": {"plugins/foundry/frozen-v3.txt": digest},
    }
    manifest_path = benchmark / "freeze-manifest-v3.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "freeze v3")
    freeze_commit = _git(repository, "rev-parse", "HEAD")
    (repository / "evidence-v3.json").write_text("{}\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "evidence v3")
    attestation = {
        "artifact": "foundry.local_scout.run_attestation",
        "version": 3,
        "freeze_commit": freeze_commit,
        "freeze_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "superseded_v2_commit": v2_commit,
        "first_v3_operation_at": "2099-01-01T00:00:00Z",
        "last_v3_operation_at": "2099-01-01T00:01:00Z",
    }
    EVIDENCE.validate_run_attestation_v3(
        benchmark,
        attestation,
        repository_root=repository,
        evidence_commit="HEAD",
    )
    attestation["first_v3_operation_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(EVIDENCE.CampaignEvidenceV3Error, match="chronology"):
        EVIDENCE.validate_run_attestation_v3(
            benchmark,
            attestation,
            repository_root=repository,
            evidence_commit="HEAD",
        )
