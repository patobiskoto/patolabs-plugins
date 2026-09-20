from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "benchmarks" / "foundry-45"


def _module():
    spec = importlib.util.spec_from_file_location(
        "foundry45_phase_a_v2_test", ROOT / "validate-phase-a-v2.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _module()


def _copy_contract(tmp_path: Path) -> Path:
    directory = tmp_path / "foundry-45"
    directory.mkdir()
    (directory / "phase-a-v2.json").write_bytes((ROOT / "phase-a-v2.json").read_bytes())
    (directory / "freeze-manifest-v2.json").write_bytes(
        (ROOT / "freeze-manifest-v2.json").read_bytes()
    )
    return directory


def test_phase_a_contract_is_exact_and_offline():
    value = VALIDATOR.validate_phase_a(ROOT)
    assert value["execution_guard"]["runner_status"] == "not_frozen_not_authorized"
    assert value["matrix"] == {
        **value["matrix"],
        "product_rows": 48,
        "shared_cloud_control_rows": 24,
        "candidate_pipeline_rows": 96,
        "cloud_executions": 120,
    }


@pytest.mark.parametrize(
    ("path", "replacement", "error"),
    [
        (("matrix", "cloud_executions"), 108, "paired requalification matrix"),
        (("matrix", "cloud_profiles", 0, "model"), "opus", "responsible cloud controls"),
        (("local_boundary", "accepted_non_authoritative_extensions"), ["message.reasoning", "provider.extra"], "local compatibility boundary"),
        (("execution_guard", "prohibited", 0), "reuse_of_f35_authorization_but_only_once", "execution guard"),
    ],
)
def test_phase_a_rejects_matrix_or_boundary_mutation(tmp_path, path, replacement, error):
    directory = _copy_contract(tmp_path)
    document = json.loads((directory / "phase-a-v2.json").read_text(encoding="utf-8"))
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    (directory / "phase-a-v2.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(VALIDATOR.PhaseAValidationError, match=error):
        VALIDATOR.validate_phase_a(directory, repository=ROOT.parents[3])


def test_phase_a_source_digest_projection_tracks_current_compatibility_boundary():
    digests = VALIDATOR.phase_a_repository_digests(ROOT.parents[3])
    assert set(digests) == set(VALIDATOR.REPOSITORY_FILES)
    assert all(len(value) == 64 for value in digests.values())
    assert VALIDATOR._validate_manifest(ROOT, ROOT.parents[3]) == digests


def test_phase_a_rejects_mutated_historical_artifact(monkeypatch):
    actual = VALIDATOR._sha

    def forged(path):
        if path.name == "raw-evidence-v4.json":
            return "0" * 64
        return actual(path)

    monkeypatch.setattr(VALIDATOR, "_sha", forged)
    with pytest.raises(VALIDATOR.PhaseAValidationError, match="historical F35 artifact"):
        VALIDATOR.validate_phase_a(ROOT)


def test_phase_a_rejects_manifest_digest_mutation(tmp_path):
    directory = _copy_contract(tmp_path)
    manifest = json.loads((directory / "freeze-manifest-v2.json").read_text(encoding="utf-8"))
    manifest["repository_files"]["plugins/foundry/tooling/foundry/local_scout.py"] = "0" * 64
    (directory / "freeze-manifest-v2.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(VALIDATOR.PhaseAValidationError, match="repository digest"):
        VALIDATOR.validate_phase_a(directory, repository=ROOT.parents[3])
