"""Keep the PAT-61 provider-live recipe bounded and honest."""

import json
from pathlib import Path


RECIPE = (
    Path(__file__).resolve().parents[1]
    / "docs" / "qualification" / "pat-61-six-configuration-v1.json"
)


def test_pat61_recipe_has_exactly_the_six_real_tracker_host_cells():
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))

    assert recipe["schema"] == "foundry.pat-61-real-qualification.v1"
    assert len(recipe["adapter_baseline_sha"]) == 40
    assert {(cell["tracker"], cell["host"]) for cell in recipe["cells"]} == {
        (tracker, host)
        for tracker in ("youtrack", "linear", "ghprojects")
        for host in ("claude-code", "codex")
    }
    assert len(recipe["cells"]) == 6
    assert {cell["id"]: cell["status"] for cell in recipe["cells"]} == {
        "youtrack-claude": "not_run",
        "youtrack-codex": "in_progress",
        "linear-claude": "not_run",
        "linear-codex": "in_progress",
        "ghprojects-claude": "not_run",
        "ghprojects-codex": "blocked",
    }
    assert all(
        cell["partial_evidence"]
        for cell in recipe["cells"]
        if cell["status"] != "not_run"
    )
    assert not any(cell["status"] == "passed" for cell in recipe["cells"])


def test_pat61_recipe_is_budgeted_and_does_not_promote_deterministic_evidence():
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))

    assert recipe["budget"] == {
        "nominal_paths_per_cell": 1,
        "reasoned_retries_per_cell": 2,
        "public_app_store_effect": False,
        "quota": "record only when the host or provider exposes it",
    }
    assert recipe["evidence_boundary"]["deterministic_conformance"].endswith(
        "never provider-live evidence"
    )
    assert len(recipe["required_journey"]) == 6
    assert len(recipe["adversarial_cases"]) == 9
    assert any("other host" in rule for rule in recipe["forbidden_inference"])
    assert any("archived binding" in rule for rule in recipe["forbidden_inference"])
