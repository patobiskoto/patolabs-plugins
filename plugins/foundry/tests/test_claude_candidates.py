"""PAT-16 deterministic boundaries; these tests never qualify a native host."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from foundry.cost_attribution import PRICE_GRID_PATH, cost_record, load_price_grid, price_for, read_host_log
from foundry.routing import DEFAULT_MAPPINGS, RoutingConfigError, RoutingPolicy, RoutingUnavailableError, UserRouteRequest
from foundry.routing_facades import _completion_fields, claude_invocation_model, claude_policy_model, detect_claude_overrides
from foundry.telemetry import TelemetryObserver

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pat16_route_agent", ROOT / "hooks/route_agent.py")
hook = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hook
spec.loader.exec_module(hook)


def packet():
    return "Goal:\nInspect the fixture.\nInputs:\nFixture.\nConstraints:\nRead only; no delegation.\nDone when:\nReturn evidence."


def install_candidates(root):
    path = root / ".foundry/model-routing.json"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes((ROOT / "docs/examples/claude-candidates-pat16.json").read_bytes())
    return path


@pytest.mark.parametrize("role,model,effort,profile,turns", [
    ("scout", "claude-haiku-4-5", None, "readonly-none", 10),
    ("implementer", "claude-sonnet-5-5", "medium", "worker-medium", 50),
    ("reviewer", "claude-opus-5-5", "high", "readonly-high", 24),
    ("architect", "claude-opus-5-5", "high", "readonly-high", 30),
])
@pytest.mark.parametrize("configured", [False, True])
def test_promoted_defaults_and_explicit_candidates_preserve_wire_permissions_and_unknown_observation(tmp_path, role, model, effort, profile, turns, configured):
    if configured:
        install_candidates(tmp_path)
    route = RoutingPolicy.load(tmp_path).resolve(role, "claude")
    assert (route.model, route.effort) == (claude_policy_model(model), effort)
    updated, context = hook.route_tool_input({"subagent_type": f"foundry:{role}", "prompt": packet(), "max_turns": 999, "effort": "max"}, cwd=tmp_path, environ={})
    visible = json.loads(context.removeprefix("Foundry Claude route: "))
    assert "model" not in updated
    assert updated["max_turns"] == turns
    assert updated["subagent_type"] == f"foundry:routed-{profile}-{claude_policy_model(model)}"
    assert "effort" not in updated and "maxTurns" not in updated
    assert visible["effort_parameters"]["transmitted"] == effort
    assert visible["execution_observation"] == {"model": None, "effort": None, "status": "unknown"}
    frontmatter = (ROOT / f"agents/{updated['subagent_type'].removeprefix('foundry:')}.md").read_text().split("---", 2)[1]
    assert f"model: {model}" in frontmatter
    assert "Agent" not in frontmatter and "Task" not in frontmatter
    assert ("effort:" not in frontmatter) if effort is None else f"effort: {effort}" in frontmatter
    if role != "implementer":
        assert "Edit" not in frontmatter and "Write" not in frontmatter


@pytest.mark.parametrize("pin", ["claude-sonnet-5", "claude-opus-5", "claude-fable-5", "claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"])
def test_explicit_pins_never_become_latest_aliases(pin):
    assert claude_invocation_model(pin) == pin


@pytest.mark.parametrize("alias", ["haiku", "sonnet", "opus", "fable"])
def test_alias_intent_is_preserved_and_diagnosed(tmp_path, alias):
    request = {"model": alias}
    if alias != "haiku":
        request["effort"] = "low"
    prompt = 'FOUNDRY_ROUTE_REQUEST=' + json.dumps(request) + '\n' + packet()
    updated, context = hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": prompt}, cwd=tmp_path, environ={})
    assert updated["model"] == alias
    assert "CLAUDE_ALIAS_VERSION_UNOBSERVED" in context
    assert '"status": "unknown"' in context


@pytest.mark.parametrize("model", ["sonnet", "opus", "fable", "claude-sonnet-5-5"])
@pytest.mark.parametrize("source", ["user", "project"])
def test_non_haiku_model_override_requires_explicit_effort(tmp_path, model, source):
    prompt = packet()
    if source == "user":
        prompt = 'FOUNDRY_ROUTE_REQUEST=' + json.dumps({"model": model}) + '\n' + prompt
    else:
        path = tmp_path / ".foundry/model-routing.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"mappings": {"claude": {"economy": {"model": model}}}}))
    with pytest.raises(RoutingConfigError, match="effort null réservé à Haiku 4.5.*effort explicite"):
        hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": prompt},
                             cwd=tmp_path, environ={})


def test_alias_availability_cannot_certify_a_version_pin(tmp_path):
    install_candidates(tmp_path)
    with pytest.raises(RoutingUnavailableError):
        hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": packet()}, cwd=tmp_path, environ={"FOUNDRY_CLAUDE_AVAILABLE_MODELS": "haiku"})


@pytest.mark.parametrize("effort", ["low", "medium", "high", "max", "ultracode"])
def test_haiku_explicit_effort_rejected(tmp_path, effort):
    with pytest.raises(RoutingConfigError, match="non applicable"):
        RoutingPolicy.load(tmp_path).resolve("scout", "claude", user=UserRouteRequest(effort=effort))


def test_null_is_not_a_ranked_effort_for_other_models_or_gates(tmp_path):
    path = install_candidates(tmp_path)
    data = json.loads(path.read_text())
    data["mappings"]["claude"]["frontier"]["effort"] = None
    path.write_text(json.dumps(data))
    with pytest.raises(RoutingConfigError):
        RoutingPolicy.load(tmp_path).resolve("reviewer", "claude")
    data["mappings"]["claude"]["frontier"] = {"model": "haiku-4.5", "effort": None}
    path.write_text(json.dumps(data))
    with pytest.raises(RoutingConfigError, match="plancher"):
        RoutingPolicy.load(tmp_path).resolve("reviewer", "claude")


@pytest.mark.parametrize("name", ["CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_CODE_EFFORT_LEVEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL"])
def test_documented_override_names_are_value_free_and_cannot_observe_execution(tmp_path, name):
    install_candidates(tmp_path)
    assert detect_claude_overrides({name: "private-value"}) == (name,)
    _, context = hook.route_tool_input({"subagent_type": "foundry:reviewer", "prompt": packet()}, cwd=tmp_path, environ={name: "private-value"})
    assert name in context and "private-value" not in context
    assert "HOST_OVERRIDE_NEUTRALIZES_POLICY" in context
    assert '"status": "unknown"' in context


@pytest.mark.parametrize("setting", ["availableModels", "enforceAvailableModels", "maxEffortLevel", "modelSettings", "fallbackModel", "ultracode"])
def test_explicit_effective_settings_are_value_free_diagnostics(setting):
    assert detect_claude_overrides({}, settings={setting: "private-value"}) == (setting,)
    # No guessed environment spelling for a managed setting.
    assert detect_claude_overrides({setting: "private-value"}) == ()


def test_legacy_requested_low_is_preserved_but_never_transmitted(tmp_path):
    path = tmp_path / ".foundry/model-routing.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"mappings": {"claude": {
        "economy": {"model": "haiku-4.5", "effort": "low"},
    }}}))
    _, context = hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": packet()}, cwd=tmp_path, environ={})
    visible = json.loads(context.removeprefix("Foundry Claude route: "))
    assert visible["effort"] == "low"
    assert visible["effort_parameters"] == {"requested": "low", "transmitted": None, "status": "not_applicable", "observed": None}


def test_rollback_restores_only_original_project_policy_bytes(tmp_path):
    path = tmp_path / ".foundry/model-routing.json"
    path.parent.mkdir()
    original = b'{"mappings":{"claude":{"balanced":{"model":"claude-sonnet-5","effort":"medium"}}}}\n'
    path.write_bytes(original)
    before = [RoutingPolicy.load(tmp_path).resolve(role, "claude").to_dict() for role in ("scout", "implementer", "reviewer", "architect")]
    install_candidates(tmp_path)
    assert RoutingPolicy.load(tmp_path).resolve("implementer", "claude").model == "sonnet-5.5"
    path.write_bytes(original)
    assert path.read_bytes() == original
    assert before == [RoutingPolicy.load(tmp_path).resolve(role, "claude").to_dict() for role in ("scout", "implementer", "reviewer", "architect")]
    assert RoutingPolicy.load(tmp_path).resolve("implementer", "claude").model == "claude-sonnet-5"
    assert DEFAULT_MAPPINGS["claude"]["balanced"].model == "sonnet-5.5"
    assert DEFAULT_MAPPINGS["codex"]["apex"].effort == "max"


@pytest.mark.parametrize("model,effort", [("sonnet-5", "medium"), ("opus-5", "high"), ("fable-5", "high"), ("sonnet-5.5", "medium"), ("opus-5.5", "high"), ("fable-5.1", "high"), ("haiku-4.5", "low"), ("haiku-4.5", None)])
def test_historical_and_candidate_telemetry_identity_retained(tmp_path, model, effort):
    observer = TelemetryObserver(tmp_path)
    route = replace(RoutingPolicy.load(tmp_path).resolve("scout", "claude"), model=model, effort=effort)
    run = observer.invocation_completed(**_completion_fields(route))
    assert run is not None
    event = json.loads((tmp_path / "telemetry/journal.ndjson").read_text())
    assert event["model"] == model and event["effort"] == effort
    assert event["usage"]["input_tokens"]["value"] is None



@pytest.mark.parametrize("wire", ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"])
def test_new_native_id_does_not_inherit_historical_wildcard_prices(wire):
    assert price_for(load_price_grid(PRICE_GRID_PATH), "claude", wire, "2026-10-03") is None
    assert claude_policy_model(wire) != "unknown"


@pytest.mark.parametrize("wire", ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"])
def test_offline_native_reader_preserves_new_identity_usage_and_unpriced_cost(tmp_path, wire):
    path = tmp_path / "native-fixture.jsonl"
    path.write_text(json.dumps({"type": "assistant", "sessionId": "fixture-child",
        "timestamp": "2026-10-03T10:00:00Z", "effort": "high",
        "message": {"model": wire, "text": "PRIVATE_CONTENT", "usage": {
            "input_tokens": 2, "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 4, "output_tokens": 5}}}) + "\n")
    rows = read_host_log("claude", path)
    assert len(rows) == 1 and rows[0]["model"] == wire
    assert rows[0]["tokens"]["input_tokens"] == 2
    assert "PRIVATE_CONTENT" not in json.dumps(rows)
    row = cost_record(rows[0], load_price_grid(PRICE_GRID_PATH))
    assert row["model"] == wire
    assert row["cost_micros"] is None and row["cost_provenance"] == "unavailable"


@pytest.mark.parametrize("wire", ["haiku", "claude-haiku-4-5", "claude-haiku-4-5-20251001"])
@pytest.mark.parametrize("requested", [None, "low"])
def test_project_haiku_translation_uses_model_specific_effort_rules(tmp_path, wire, requested):
    path = tmp_path / ".foundry/model-routing.json"
    path.parent.mkdir()
    data = {"claude_models": {"project-haiku": wire}, "mappings": {"claude": {
        "economy": {"model": "project-haiku", "effort": requested},
        "frontier": {"model": "project-haiku", "effort": None},
    }}}
    path.write_text(json.dumps(data))
    updated, context = hook.route_tool_input(
        {"subagent_type": "foundry:scout", "prompt": packet()}, cwd=tmp_path, environ={},
    )
    visible = json.loads(context.removeprefix("Foundry Claude route: "))
    assert visible["model"] == "project-haiku"
    assert visible["effort"] == requested
    assert visible["effort_parameters"] == {
        "requested": requested, "transmitted": None, "status": "not_applicable", "observed": None,
    }
    assert updated["max_turns"] == 10
    assert "effort" not in updated
    assert "none" in updated["subagent_type"]
    if wire == "haiku":
        assert updated["model"] == wire
    else:
        assert "model" not in updated
    for effort in ("low", "high"):
        explicit = 'FOUNDRY_ROUTE_REQUEST=' + json.dumps({"effort": effort}) + '\n' + packet()
        with pytest.raises(RoutingConfigError, match="non applicable"):
            hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": explicit},
                                 cwd=tmp_path, environ={})
    with pytest.raises(RoutingConfigError, match="plancher"):
        hook.route_tool_input({"subagent_type": "foundry:reviewer", "prompt": packet()},
                             cwd=tmp_path, environ={})
