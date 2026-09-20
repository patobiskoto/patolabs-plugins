from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from collections import namedtuple
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-35"


def _module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EVIDENCE = _module("foundry35_benchmark_evidence_v4_test", "benchmark-evidence-v4.py")
HARNESS = _module("foundry35_run_phase_b_v4_test", "run-phase-b-v4.py")


def test_v4_freeze_retains_exact_26b_and_preserves_v3_failure():
    frozen = EVIDENCE.validate_freeze_v4(ROOT)
    assert [row["identifier"] for row in frozen["models"]["candidates"]] == EVIDENCE.CANDIDATES
    gemma = frozen["models"]["candidates"][-1]
    assert gemma["repository"] == "mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit"
    assert gemma["revision"] == "e0061bda54f72709cf6fa51229530c3b14cd9d7d"
    assert gemma["artifact_bytes"] == 18807827816
    assert gemma["lfs_bytes"] == 18807520609
    assert len(gemma["files"]) == 6
    assert gemma == frozen["base_v3"]["models"]["candidates"][-1]
    failed = frozen["supersession"]["superseded_campaign"]
    assert failed == {
        **failed,
        "freeze_commit": "9d8547df3301dee58bb25f495a18b926bf421dab",
        "failure_code": "EXTERNAL_VOLUME_WRITE_UNRESPONSIVE",
        "checkpoint_created": False,
        "model_download_started": False,
    }
    assert frozen["rationale"]["researched_fallback"]["status"] == "researched_not_executed"
    assert frozen["rationale"]["tested_results"] == []


def test_v4_historical_validation_never_reads_substituted_worktree_inputs(monkeypatch):
    def substituted_worktree(*_args, **_kwargs):
        raise AssertionError("historical validation must not read a worktree JSON input")

    monkeypatch.setattr(EVIDENCE, "_load", substituted_worktree)
    frozen = EVIDENCE.validate_freeze_v4(ROOT)
    assert frozen["protocol"]["version"] == 4


def test_v4_historical_overlay_substitution_fails_its_frozen_digest(monkeypatch):
    original_git = EVIDENCE._git
    overlay_ref = (
        f"{EVIDENCE.V4_FREEZE_COMMIT}:"
        "plugins/foundry/benchmarks/foundry-35/protocol-v4.json"
    )

    def substituted_git(repository, *arguments):
        result = original_git(repository, *arguments)
        if arguments == ("show", overlay_ref):
            value = json.loads(result)
            value["coordinated_worktree_substitution"] = True
            return json.dumps(value, separators=(",", ":")).encode()
        return result

    monkeypatch.setattr(EVIDENCE, "_git", substituted_git)
    with pytest.raises(EVIDENCE.CampaignEvidenceV4Error, match="historical freeze digest differs"):
        EVIDENCE.validate_freeze_v4(ROOT)


def test_v4_missing_historical_blob_fails_closed(monkeypatch):
    original_git = EVIDENCE._git
    missing_ref = (
        f"{EVIDENCE.V4_FREEZE_COMMIT}:"
        "plugins/foundry/benchmarks/foundry-35/goldens-v3.json"
    )

    def missing_git(repository, *arguments):
        if arguments == ("show", missing_ref):
            raise EVIDENCE.CampaignEvidenceV4Error("git verification failed: frozen blob absent")
        return original_git(repository, *arguments)

    monkeypatch.setattr(EVIDENCE, "_git", missing_git)
    with pytest.raises(EVIDENCE.CampaignEvidenceV4Error, match="frozen blob absent"):
        EVIDENCE.validate_freeze_v4(ROOT)


def test_v4_reuses_exact_product_and_cloud_matrix():
    frozen = EVIDENCE.validate_freeze_v4(ROOT)
    plan = HARNESS.BASE.build_execution_plan(frozen["protocol"])
    assert len(plan["intrinsic"]) == 108
    assert len(plan["product"]) == 48
    assert len(plan["baseline"]) == 60
    assert len(plan["pipeline"]) == 48
    assert len({row["control_row_id"] for row in plan["pipeline"]}) == 12
    functions = frozen["protocol"]["product_promotion_matrix"]["product_functions"]
    assert functions == {
        "code-map": "foundry.local_code.run_code_map",
        "diff": "foundry.local_scout.preprocess_diff",
        "logs": "foundry.local_scout.preprocess_logs",
        "tests": "foundry.local_scout.preprocess_tests",
    }
    baseline = frozen["protocol"]["cloud_matrix"]["common_baseline"]["profiles"][0]
    assert (baseline["model"], baseline["effort"]) == ("gpt-5.6-sol", "high")


def test_v4_internal_cache_is_explicit_resolved_and_confined(tmp_path):
    home = tmp_path / "home"
    allowed = home / "Library/Caches/FoundryModels/foundry-35"
    assert HARNESS.require_internal_cache_path(allowed, home=home) == allowed
    assert HARNESS.require_internal_cache_path(allowed / "operator", home=home) == allowed / "operator"
    with pytest.raises(HARNESS.PhaseBGuardV4Error, match="must be confined"):
        HARNESS.require_internal_cache_path(tmp_path / "outside", home=home)


def test_v4_disk_guard_is_read_only_and_precedes_any_action(monkeypatch, tmp_path):
    usage = namedtuple("usage", "total used free")
    cache = tmp_path / "not-created" / "cache"
    available = HARNESS.ensure_disk_capacity(
        cache,
        EVIDENCE.DISK_GUARD_BYTES,
        disk_usage=lambda path: usage(40_000_000_000, 1, 35_000_000_000),
        apfs_probe=lambda: True,
    )
    assert available == 35_000_000_000
    assert not cache.exists()
    with pytest.raises(HARNESS.PhaseBGuardV4Error, match="below required"):
        HARNESS.ensure_disk_capacity(
            cache,
            EVIDENCE.DISK_GUARD_BYTES,
            disk_usage=lambda path: usage(20_000_000_000, 1, 10),
            apfs_probe=lambda: True,
        )
    called = []

    def reject(*args, **kwargs):
        called.append("preflight")
        raise HARNESS.PhaseBGuardV4Error("disk unavailable")

    monkeypatch.setattr(HARNESS, "validate_phase_b_preflight_v4", reject)
    monkeypatch.setattr(
        HARNESS,
        "qualify_candidate_snapshots_v4",
        lambda *args, **kwargs: called.append("qualification"),
    )
    output = tmp_path / "raw.json"
    with pytest.raises(HARNESS.PhaseBGuardV4Error, match="disk unavailable"):
        HARNESS.execute_campaign_v4(
            authorization={},
            internal_cache=cache,
            enable_cloud=True,
            confirm_cloud_108=True,
            output=output,
            aggregate_output=tmp_path / "aggregate.json",
            attestation_output=tmp_path / "attestation.json",
        )
    assert called == ["preflight"]
    assert not output.exists()
    assert not cache.exists()


def test_v4_downloads_only_exact_26b_via_local_dir(monkeypatch, tmp_path):
    frozen = EVIDENCE.validate_freeze_v4(ROOT)
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return kwargs["local_dir"]

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_download)
    monkeypatch.setattr(HARNESS.BASE, "verify_snapshot", lambda *args, **kwargs: None)
    ledger = HARNESS.AttemptLedgerV4()
    snapshots = HARNESS.qualify_candidate_snapshots_v4(
        frozen["models"]["candidates"], tmp_path, ledger
    )
    assert calls == [
        {
            "repo_id": "mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit",
            "revision": "e0061bda54f72709cf6fa51229530c3b14cd9d7d",
            "local_dir": str(tmp_path / HARNESS.ACTIVE_LOCAL_DIR_NAME),
        }
    ]
    assert "cache_dir" not in calls[0]
    assert snapshots[EVIDENCE.V4_GEMMA] == tmp_path / HARNESS.ACTIVE_LOCAL_DIR_NAME
    assert len(ledger.rows) == 4
    assert all(row["campaign_version"] == 4 for row in ledger.rows)


def test_v4_preflight_requires_double_cloud_opt_in(monkeypatch, tmp_path):
    benchmark = tmp_path / "plugins/foundry/benchmarks/foundry-35"
    benchmark.mkdir(parents=True)
    manifest = benchmark / "freeze-manifest-v4.json"
    manifest.write_text("{}\n", encoding="utf-8")
    freeze_commit = "a" * 40
    head = "b" * 40
    frozen = {"manifest": {"repository_files": {}}}
    monkeypatch.setattr(HARNESS.EVIDENCE, "validate_freeze_v4", lambda value: frozen)

    def fake_git(repository, *arguments):
        if arguments == ("rev-parse", "HEAD^{commit}"):
            return f"{head}\n".encode()
        if arguments[:2] == (
            "show",
            f"{freeze_commit}:plugins/foundry/benchmarks/foundry-35/freeze-manifest-v4.json",
        ):
            return manifest.read_bytes()
        if arguments == ("show", "-s", "--format=%cI", freeze_commit):
            return b"2026-08-22T00:00:00+02:00\n"
        if arguments[:2] == ("merge-base", "--is-ancestor"):
            return b""
        raise AssertionError(arguments)

    monkeypatch.setattr(HARNESS, "_git", fake_git)
    selected = Path.home() / "Library/Caches/FoundryModels/foundry-35"
    authorization = {
        "artifact": "foundry.local_scout.phase_b_authorization",
        "version": 4,
        "freeze_commit": freeze_commit,
        "freeze_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "superseded_v3_commit": EVIDENCE.V3_FREEZE_COMMIT,
        "internal_cache": str(selected),
        "cloud_opt_in": True,
        "cloud_108_confirmation": True,
        "authorized_at": "2026-08-22T01:00:00+02:00",
    }
    result = HARNESS.validate_phase_b_preflight_v4(
        authorization,
        internal_cache=selected,
        enable_cloud=True,
        confirm_cloud_108=True,
        repository=tmp_path,
        benchmark=benchmark,
        disk_guard=lambda path, required: 35_000_000_000,
    )
    assert result["available_disk_bytes"] == 35_000_000_000
    with pytest.raises(HARNESS.PhaseBGuardV4Error, match="double opt-in"):
        HARNESS.validate_phase_b_preflight_v4(
            authorization,
            internal_cache=selected,
            enable_cloud=True,
            confirm_cloud_108=False,
            repository=tmp_path,
            benchmark=benchmark,
            disk_guard=lambda path, required: 35_000_000_000,
        )


def test_v4_harness_has_no_delete_move_cache_or_default_mutation_calls():
    source = (ROOT / "run-phase-b-v4.py").read_text(encoding="utf-8")
    for forbidden in (
        "shutil.rmtree",
        ".unlink(",
        "os.remove",
        "os.rename",
        "shutil.move",
        "cache_dir=",
        "git reset",
        "git checkout",
        "routing edit",
        "foundry_cli.py edit",
    ):
        assert forbidden not in source
    assert source.index("validate_phase_b_preflight_v4(") < source.index(
        "selected.mkdir("
    )


def test_v4_harness_digest_mutation_invalidates_freeze(tmp_path):
    frozen = EVIDENCE.validate_freeze_v4(ROOT)
    repository = tmp_path / "repository"
    for relative in frozen["manifest"]["repository_files"]:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            subprocess.run(
                ["git", "show", f"{EVIDENCE.V4_FREEZE_COMMIT}:{relative}"],
                cwd=ROOT.parents[3], check=True, capture_output=True,
            ).stdout
        )
    EVIDENCE.validate_repository_files_v4(frozen["manifest"], repository)
    harness = repository / "plugins/foundry/benchmarks/foundry-35/run-phase-b-v4.py"
    harness.write_text(harness.read_text(encoding="utf-8") + "# mutation\n")
    with pytest.raises(EVIDENCE.CampaignEvidenceV4Error, match="freeze digest differs"):
        EVIDENCE.validate_repository_files_v4(frozen["manifest"], repository)


def test_v4_archive_validation_uses_git_snapshot_but_current_worktree_fails():
    frozen = EVIDENCE.validate_freeze_v4(ROOT)
    with pytest.raises(EVIDENCE.CampaignEvidenceV4Error, match="freeze digest differs"):
        EVIDENCE.validate_repository_files_v4(frozen["manifest"], ROOT.parents[3])


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_v4_attestation_requires_freeze_ancestor_and_chronology(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    benchmark = repository / "plugins/foundry/benchmarks/foundry-35"
    benchmark.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "foundry@example.invalid")
    _git(repository, "config", "user.name", "Foundry Test")
    (repository / "v3.txt").write_text("v3\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "v3")
    v3_commit = _git(repository, "rev-parse", "HEAD")
    monkeypatch.setattr(EVIDENCE, "V3_FREEZE_COMMIT", v3_commit)
    frozen_file = repository / "plugins/foundry/frozen-v4.txt"
    frozen_file.parent.mkdir(parents=True, exist_ok=True)
    frozen_file.write_text("frozen-v4\n", encoding="utf-8")
    digest = hashlib.sha256(frozen_file.read_bytes()).hexdigest()
    manifest = {
        "artifact": "foundry.local_scout.freeze_manifest",
        "version": 4,
        "repository_files": {"plugins/foundry/frozen-v4.txt": digest},
    }
    manifest_path = benchmark / "freeze-manifest-v4.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "freeze v4")
    freeze_commit = _git(repository, "rev-parse", "HEAD")
    (repository / "evidence.json").write_text("{}\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "evidence")
    attestation = {
        "artifact": "foundry.local_scout.run_attestation",
        "version": 4,
        "freeze_commit": freeze_commit,
        "freeze_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "superseded_v3_commit": v3_commit,
        "first_v4_operation_at": "2099-01-01T00:00:00Z",
        "last_v4_operation_at": "2099-01-01T00:01:00Z",
    }
    EVIDENCE.validate_run_attestation_v4(
        benchmark, attestation, repository_root=repository, evidence_commit="HEAD"
    )
    attestation["first_v4_operation_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(EVIDENCE.CampaignEvidenceV4Error, match="chronology"):
        EVIDENCE.validate_run_attestation_v4(
            benchmark, attestation, repository_root=repository, evidence_commit="HEAD"
        )
