import json
import subprocess

import pytest

from foundry.effort_policy import DEFAULT_EFFORT_SCOPES
from foundry.routing import EFFORTS, RoutingConfigError, RoutingPolicy, UserRouteRequest
from foundry.routing_facades import claude_invocation_model
import foundry.telemetry as telemetry


def test_six_effort_scope_accepts_xhigh_and_refuses_ultra_with_reason(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "effort_scopes": {"codex": {"gpt-6-astra": {
            "version": 1,
            "levels": ["low", "medium", "high", "xhigh", "max", "ultra"],
            "inadmissible": {},
        }}},
        "mappings": {"codex": {"balanced": {
            "model": "gpt-6-astra", "effort": "xhigh",
        }}},
    }), encoding="utf-8")
    route = RoutingPolicy.load(tmp_path).resolve("implementer", "codex")
    assert (route.model, route.effort) == ("gpt-6-astra", "xhigh")
    with pytest.raises(RoutingConfigError, match="inadmissible.*automatic task delegation"):
        RoutingPolicy.load(tmp_path).resolve(
            "implementer", "codex", user=UserRouteRequest(effort="ultra"),
        )
    with pytest.raises(RoutingConfigError, match="inconnu.*low, medium, high, xhigh, max, ultra"):
        RoutingPolicy.load(tmp_path).resolve(
            "implementer", "codex", user=UserRouteRequest(effort="unknown"),
        )
    with pytest.raises(RoutingConfigError, match="implementer.effort : chaîne attendue"):
        RoutingPolicy.load(tmp_path).resolve(
            "implementer", "codex", user=UserRouteRequest(effort=3),
        )


def test_immutable_v1_pilots_consume_the_data_derived_effort_projection():
    assert EFFORTS == DEFAULT_EFFORT_SCOPES["claude"]["default"].levels
    assert EFFORTS.index("xhigh") == EFFORTS.index("high") + 1


def test_claude_translation_is_project_data_and_unknown_model_is_explicit(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"claude_models": {"new-anthropic-model": "new-wire-alias"}}), encoding="utf-8")
    assert claude_invocation_model("new-anthropic-model", root=tmp_path) == "new-wire-alias"
    with pytest.raises(RoutingConfigError, match="missing-model.*traduction hôte inconnue"):
        claude_invocation_model("missing-model", root=tmp_path)


def test_claude_translation_is_found_from_a_nested_repository_directory(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "claude_models": {"nested-model": "nested-wire-alias"},
    }), encoding="utf-8")
    nested = tmp_path / "one" / "two"
    nested.mkdir(parents=True)
    assert claude_invocation_model("nested-model", root=nested) == "nested-wire-alias"


def test_telemetry_uses_the_project_effort_scope_and_names_rejected_levels(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "effort_scopes": {"codex": {"project-model": {
            "version": 3, "levels": ["careful", "deep"], "inadmissible": {},
        }}},
    }), encoding="utf-8")
    scopes = RoutingPolicy.load(tmp_path).effort_scopes
    assert telemetry._telemetry_effort(
        "codex", "project-model-v1", "deep", scopes,
    ) == "deep"
    with pytest.raises(
        telemetry.TelemetryValidationError,
        match="rejected.*project-model.*v3.*accepted levels: careful, deep",
    ):
        telemetry._telemetry_effort(
            "codex", "project-model-v1", "rejected", scopes,
        )


def test_gate_floor_missing_from_custom_scope_is_an_explicit_configuration_error(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "effort_scopes": {"claude": {"new-model": {
            "version": 1, "levels": ["low", "medium", "xhigh"], "inadmissible": {},
        }}},
        "mappings": {"claude": {"frontier": {
            "model": "new-model", "effort": "xhigh",
        }}},
        "claude_models": {"new-model": "new-wire-alias"},
    }), encoding="utf-8")
    with pytest.raises(
        RoutingConfigError,
        match="configuration invalide.*plancher d'effort 'high'.*new-model.*niveaux acceptés",
    ):
        RoutingPolicy.load(tmp_path).resolve("reviewer", "claude")


def test_default_routes_and_gate_floors_are_unchanged(tmp_path):
    policy = RoutingPolicy.load(tmp_path)
    assert (policy.resolve("implementer", "codex").model,
            policy.resolve("implementer", "codex").effort) == ("gpt-5.6-terra", "medium")
    reviewer = policy.resolve("reviewer", "codex")
    assert (reviewer.selected_tier, reviewer.gate_floor, reviewer.gate_effort_floor) == (
        "frontier", "frontier", "high",
    )
    with pytest.raises(RoutingConfigError, match="gate 'reviewer'.*reçu : 'low'"):
        policy.resolve("reviewer", "codex", user=UserRouteRequest(effort="low"))
