"""Keep the PAT-61 provider-live recipe bounded and honest."""

import hashlib
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
    assert all(cell["status"] == "passed" for cell in recipe["cells"])
    assert all(cell["partial_evidence"] for cell in recipe["cells"])
    assert recipe["final_assessment"]["required_stage_count"] == 36
    assert recipe["final_assessment"]["core_behavior_gaps"] == []
    for cell in recipe["cells"]:
        assert cell["historical_status"] in {"in_progress", "passed"}
        assert set(cell["journey_evidence"]) == set(recipe["required_journey"])
        assert cell["journey_classification"] == {
            step: "passed" for step in recipe["required_journey"]
        }
        for step in recipe["required_journey"]:
            assert set(cell["journey_evidence"][step]) == {
                "source", "coordinates", "existing_proofs"
            }
            assert all(cell["journey_evidence"][step].values())
    # The material composite attribution must remain visible, not be silently
    # recast as a native Claude mutation or an execution under the new defaults.
    linear_claude = next(
        cell for cell in recipe["cells"] if cell["id"] == "linear-claude"
    )
    assert "not attributed to Claude" in linear_claude["journey_evidence"][
        "adr_create_and_evolve"
    ]["existing_proofs"]
    assert "subject to independent" in recipe["final_assessment"]["proposal"]


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


def test_pat61_native_observation_preserves_identity_unknowns_and_hashes():
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    ref = recipe["post_integration_observation"]
    observation_path = RECIPE.parent / ref["file"]
    assert hashlib.sha256(observation_path.read_bytes()).hexdigest() == ref["sha256"]
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    assert observation["observation_not_receipt"] is True
    assert observation["head"] == recipe["current_checkpoint"]["head"]
    assert observation["plugin_version"] == "0.9.0"
    assert observation["runtime_observed"]["effort"] is None
    assert observation["runtime_observed"]["effort_status"] == "not_exposed"
    assert observation["runtime_observed"]["quota_remaining"] is None
    assert observation["runtime_observed"]["quota_status"] == "not_observed"
    assert observation["phase_limits"]["parents"] == 1
    assert observation["phase_limits"]["children"] == 0
    assert observation["phase_limits"]["retries"] == 0
    assert len(observation["tool_calls"]) == 3
    for tracker in ("youtrack", "linear", "ghprojects"):
        seen = observation["trackers"][tracker]
        assert seen["read_only"] is True
        assert seen["groom"]["truncated"] is False
        assert seen["groom"]["next_page"] is None
        assert seen["changelog"]["items"] == seen["bridge"]["items"]
        assert set(seen["bridge"]["items"].values()) == {"accepted"}
    for tracker in ("youtrack", "linear"):
        assert observation["trackers"][tracker]["bridge"]["closure"][
            "native_capability"
        ] == "unavailable"
