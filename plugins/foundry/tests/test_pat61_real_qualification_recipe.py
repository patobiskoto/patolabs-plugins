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
    # Historical V1-to-V2 attribution stays visible alongside the distinct
    # authorized native V2-to-V3 evolution.
    linear_claude = next(
        cell for cell in recipe["cells"] if cell["id"] == "linear-claude"
    )
    assert "not attributed to Claude" in linear_claude["journey_evidence"][
        "adr_create_and_evolve"
    ]["existing_proofs"]
    assert "native Claude" in linear_claude["journey_evidence"][
        "adr_create_and_evolve"
    ]["source"]
    assert "V2→V3" in linear_claude["journey_evidence"][
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


def test_pat61_native_adr_evolution_is_distinct_and_preserves_block_history():
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    ref = recipe["native_adr_evolution_observation"]
    observation_bytes = (RECIPE.parent / ref["file"]).read_bytes()
    assert hashlib.sha256(observation_bytes).hexdigest() == ref["sha256"]
    observation = json.loads(observation_bytes)
    assert observation["schema"] == (
        "foundry.pat-61-native-adr-evolution-host-observation.v1"
    )
    assert observation["observation_not_receipt"] is True
    assert observation["source_head"] == ref["source_head"] == (
        "4f142e5ce4baacbdbff7d1486c4870126b1afc91"
    )
    assert observation["plugin_version"] == "0.9.0"
    assert observation["claude_version"] == "2.1.285"
    assert observation["budget"] == {
        "parents": 1, "max_turns": 12, "max_cost_catalogue_usd": 3,
        "children": 0, "retries": 0, "additional_api_credits": 0,
        "status": "completed_exhausted",
    }
    assert observation["requested"] == {"model": "sonnet-5.5", "effort": "medium"}
    assert observation["transmitted"] == {
        "model": "claude-sonnet-5-5", "effort": "medium"
    }
    runtime = observation["runtime_observed"]
    assert runtime["assistant_models"] == ["claude-sonnet-5-5"]
    assert runtime["model_usage_keys"] == ["claude-sonnet-5-5"]
    assert runtime["effort"] is None
    assert runtime["effort_status"] == "not_exposed"
    assert runtime["quota_remaining"] is None
    assert runtime["quota_status"] == "not_observed"
    assert observation["tool_calls"] == [{
        "name": "Bash", "command": "python3 /tmp/pat61-v3-native-helper.py"
    }]
    result = observation["host_return"]
    assert (result["subtype"], result["is_error"], result["exit_code"]) == (
        "success", False, 0
    )
    assert result["num_turns"] == 2
    assert result["total_cost_usd"] == 0.0277176
    assert result["cost_basis"] == "catalogue_not_invoice"
    effect = observation["effect"]
    assert effect["observation_not_receipt"] is True
    assert effect["source_head"] == observation["source_head"]
    assert effect["project_id"] == "d3d412b6-1327-4bba-905b-01e4bb29797f"
    assert effect["tracker"] == "linear"
    assert effect["operation"] == "Foundry CLI adr edit PAT-ADR-0002"
    assert effect["before"]["ref"] == "d590fe2b-eb78-4297-9391-5be4e68d34f7"
    assert effect["after"]["ref"] == "b6a6f4aa-47a2-40db-a3b0-1cbb9f419128"
    assert (effect["before"]["sequence"], effect["after"]["sequence"]) == (1, 2)
    assert effect["before"]["status"] == effect["after"]["status"] == "proposed"
    for field in ("expected_body_exact", "only_marker_changed",
                  "unrelated_properties_preserved", "existing_documents_preserved"):
        assert effect[field] is True
    assert all(record["returncode"] == 0 for record in effect["records"])
    assert observation["file_digests"]["/tmp/pat61-v3-native-stream.jsonl"] == (
        "a71cdf030d37c1b8d3adeadbe451c0ea4da846d15627f58ab7b81b9c96c49678"
    )
    historical = recipe["historical_blocked_review"]
    assert historical["proof_id"] == (
        "4349771b741ceabb46506a2b59c84470b6fa562221a58547a3094d49af6fb5ca"
    )
    assert historical["quality"] == "blocked"
    assert historical["generation"] == 1
    assert len(historical["outcomes"]) == 9
    assert {item["id"]: item["verdict"] for item in historical["outcomes"]} == {
        "ac-1": "not_covered", "ac-6": "contradicted",
        **{f"ac-{n}": "pass" for n in (2, 3, 4, 5, 7, 8, 9)},
    }
    assert historical["acceptance_snapshot_ac_digest"] == (
        "f9a2ffabc545ab54942de27ebebde7395ef06659e161e8010432882e1edcbc30"
    )
