"""Static preloaded pins for the observed Agent.model alias-only wire schema."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys

import pytest

from foundry.routing import RoutingConfigError, RoutingPolicy
from foundry.routing_facades import (
    CLAUDE_AGENT_MODEL_ALIASES,
    claude_invocation_binding, claude_pin_profile_documents,
)

ROOT = Path(__file__).resolve().parents[1]


def load_hook(name):
    spec = importlib.util.spec_from_file_location(f"pat16_profile_{name}", ROOT / f"hooks/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = load_hook("route_agent")
guard = load_hook("guard_routed_readonly")
observe = load_hook("observe_agent")


def packet():
    return "Goal:\nInspect fixture.\nInputs:\nFixture.\nConstraints:\nBounded; no delegation.\nDone when:\nReturn evidence."


def config(root, model, effort, *, declaration=None, role="scout"):
    tier = "economy" if role == "scout" else "balanced"
    data = {"mappings": {"claude": {tier: {"model": model, "effort": effort}}}}
    if declaration:
        data["claude_models"] = declaration
    path = root / ".foundry/model-routing.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data))


GENERATED = claude_pin_profile_documents(ROOT)


def test_generated_inventory_has_exact_source_drift_check():
    assert len(GENERATED) == 74
    pinned = {path.stem for path in (ROOT / "agents").glob("routed-*.md")
              if re.search(r"(?m)^model:", path.read_text())}
    assert pinned == set(GENERATED)
    for name, expected in GENERATED.items():
        assert (ROOT / "agents" / f"{name}.md").read_text() == expected


@pytest.mark.parametrize("name,text", sorted(GENERATED.items()))
def test_all_pins_use_profile_frontmatter_and_preserve_schema_and_caps(tmp_path, name, text):
    fields = dict(re.findall(r"(?m)^(name|model|effort|tools): (.+)$", text))
    role = "scout" if name.startswith("routed-readonly-") else "implementer"
    effort = fields.get("effort")
    config(tmp_path, fields["model"], effort, role=role)
    updated, context = hook.route_tool_input({"subagent_type": f"foundry:{role}",
        "model": fields["model"], "prompt": packet(), "max_turns": 999}, cwd=tmp_path, environ={})
    assert updated["subagent_type"] == f"foundry:{name}"
    assert "model" not in updated
    assert updated["max_turns"] == (10 if role == "scout" else 50)
    assert "Agent" not in fields["tools"] and "Task" not in fields["tools"]
    if role == "scout":
        assert "Write" not in fields["tools"] and "Edit" not in fields["tools"]
        assert guard.decision(updated["subagent_type"], "python3 -c 'print(1)'")
        command = f"python3 {ROOT}/tooling/foundry_cli.py routing read-review --diff-hash {'a' * 64} --claim-id {'b' * 64} --root /tmp/fixture --base {'c' * 40}"
        assert guard.decision(updated["subagent_type"], command) is None
    visible = json.loads(context.removeprefix("Foundry Claude route: "))
    assert visible["transmitted_launch"]["transmitted_model"] == fields["model"]
    assert visible["transmitted_launch"]["model_source"] == "profile_frontmatter"
    assert visible["execution_observation"]["status"] == "unknown"


@pytest.mark.parametrize("alias", CLAUDE_AGENT_MODEL_ALIASES)
def test_project_alias_keeps_accepted_enum_wire(tmp_path, alias):
    config(tmp_path, "project-model", "low", declaration={"project-model": alias})
    updated, _ = hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": packet()}, cwd=tmp_path, environ={})
    assert updated["model"] == alias
    assert updated["subagent_type"] == f"foundry:routed-readonly-{'none' if alias == 'haiku' else 'low'}"


def test_project_translation_to_builtin_full_id_selects_that_pin(tmp_path):
    config(tmp_path, "project-sonnet", "medium", declaration={"project-sonnet": "claude-sonnet-5-5"})
    updated, _ = hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": packet()}, cwd=tmp_path, environ={})
    assert "model" not in updated
    assert updated["subagent_type"] == "foundry:routed-readonly-medium-sonnet-5.5"


def test_custom_full_id_without_shipped_declaration_is_actionable_and_no_pending_record(tmp_path):
    config(tmp_path, "project-custom", "medium", declaration={"project-custom": "claude-custom-999"})
    with pytest.raises(RoutingConfigError, match="aucun profil épinglé préchargé"):
        hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": packet()}, cwd=tmp_path,
                             environ={"FOUNDRY_DATA": str(tmp_path / "state")}, correlation="not-launched")
    assert not (tmp_path / "state/telemetry/pending").exists()


@pytest.mark.parametrize("corrupt", [False, True])
def test_absent_or_divergent_preloaded_pin_refused(tmp_path, corrupt):
    agents = tmp_path / "agents"
    agents.mkdir()
    shutil.copyfile(ROOT / "agents/routed-readonly-none.md", agents / "routed-readonly-none.md")
    if corrupt:
        (agents / "routed-readonly-none-haiku-4.5.md").write_text(
            GENERATED["routed-readonly-none-haiku-4.5"].replace("model: claude-haiku-4-5", "model: haiku"))
    route = RoutingPolicy.load(tmp_path).resolve("scout", "claude")
    with pytest.raises(RoutingConfigError, match="absent|divergent"):
        claude_invocation_binding(route, "readonly", plugin_root=tmp_path)


# --- PAT-134: the subagent prompt-cache lifetime, a nested ``experimental`` map on the Sonnet 5.5 pins only

def _frontmatter(text):
    return text.split("\n---\n", 1)[0]


def test_exactly_the_ten_sonnet_55_pins_carry_experimental_cache_ttl_1h_as_a_nested_map():
    carrying = {name for name in GENERATED if "experimental" in GENERATED[name]}
    assert carrying == {f"routed-{cap}-{effort}-sonnet-5.5" for cap in ("readonly", "worker")
                        for effort in ("low", "medium", "high", "xhigh", "max")}
    assert len(carrying) == 10
    for name in carrying:
        front = _frontmatter((ROOT / "agents" / f"{name}.md").read_text())
        assert front.endswith("\nexperimental:\n  cacheTtl: 1h") and front.count("experimental") == 1
        assert not re.search(r"(?m)^cacheTtl:", front)  # never a top-level key
    for path in (ROOT / "agents").glob("*.md"):
        if path.stem not in carrying:
            assert "experimental" not in path.read_text() and "cacheTtl" not in path.read_text(), path.name


@pytest.mark.parametrize("mutate", [
    lambda text: text.replace("experimental:\n  cacheTtl: 1h\n", ""),                          # field missing
    lambda text: text.replace("cacheTtl: 1h", "cacheTtl: 5m"),                                  # another value
    lambda text: text.replace("  cacheTtl: 1h\n", "  cacheTtl: 1h\n  other: x\n"),             # another key
    lambda text: text.replace("experimental:\n  cacheTtl: 1h\n", "cacheTtl: 1h\n"),            # top level
])
def test_a_divergent_sonnet_55_profile_is_refused_before_launch(tmp_path, mutate):
    shutil.copytree(ROOT / "agents", tmp_path / "agents")
    path = tmp_path / "agents" / "routed-readonly-low-sonnet-5.5.md"
    path.write_text(mutate(path.read_text()))
    config(tmp_path, "sonnet-5.5", "low")
    route = RoutingPolicy.load(tmp_path).resolve("scout", "claude")
    with pytest.raises(RoutingConfigError, match="divergent"):
        claude_invocation_binding(route, "readonly", plugin_root=tmp_path)


@pytest.mark.parametrize("model,effort", [("opus-5.5", "high"), ("haiku-5.5", "medium"), ("sonnet-5", "low"),
                                          ("fable-5.1", "low"), ("opus-5", "medium")])
def test_the_field_on_any_other_pin_is_refused_before_launch(tmp_path, model, effort):
    shutil.copytree(ROOT / "agents", tmp_path / "agents")
    config(tmp_path, model, effort)
    route = RoutingPolicy.load(tmp_path).resolve("scout", "claude")
    binding = claude_invocation_binding(route, "readonly", plugin_root=tmp_path)  # unmodified: accepted
    path = tmp_path / "agents" / f"{binding['profile']}.md"
    assert "experimental" not in path.read_text()
    path.write_text(path.read_text().replace("\n---\n", "\nexperimental:\n  cacheTtl: 1h\n---\n", 1))
    with pytest.raises(RoutingConfigError, match="divergent"):
        claude_invocation_binding(route, "readonly", plugin_root=tmp_path)


def test_a_generic_template_carrying_the_field_is_an_invalid_template(tmp_path):
    shutil.copytree(ROOT / "agents", tmp_path / "agents")
    template = tmp_path / "agents" / "routed-readonly-low.md"
    template.write_text(template.read_text().replace("\n---\n", "\nexperimental:\n  cacheTtl: 1h\n---\n", 1))
    config(tmp_path, "sonnet-5.5", "low")
    route = RoutingPolicy.load(tmp_path).resolve("scout", "claude")
    with pytest.raises(RoutingConfigError, match="template de profil Claude invalide"):
        claude_invocation_binding(route, "readonly", plugin_root=tmp_path)


def test_the_shipped_sonnet_55_profile_binds_and_the_wire_model_is_unchanged(tmp_path):
    config(tmp_path, "sonnet-5.5", "low")
    route = RoutingPolicy.load(tmp_path).resolve("scout", "claude")
    binding = claude_invocation_binding(route, "readonly", plugin_root=ROOT)
    assert binding["profile"] == "routed-readonly-low-sonnet-5.5" and binding["agent_model"] is None
    assert binding["transmitted_model"] == "claude-sonnet-5-5" and binding["effort_parameters"]["transmitted"] == "low"


def test_posttool_completion_remains_correlated_for_suffixed_profiles(tmp_path):
    config(tmp_path, "opus-5.5", "high")
    (tmp_path / "state").mkdir()
    environ = {"FOUNDRY_DATA": str(tmp_path / "state")}
    updated, _ = hook.route_tool_input({"subagent_type": "foundry:scout", "prompt": packet()},
                                     cwd=tmp_path, environ=environ, correlation="pin-child")
    assert updated["subagent_type"].endswith("-opus-5.5")
    payload = {"hook_event_name": "PostToolUse", "tool_use_id": "pin-child", "tool_input": updated}
    assert observe.observe(payload, environ=environ)
    assert not observe.observe(payload, environ=environ)
    event = json.loads((tmp_path / "state/telemetry/journal.ndjson").read_text())
    assert event["model"] == "opus-5.5"
    assert event["usage"]["input_tokens"]["value"] is None


def test_historical_measurement_alias_contract_refuses_default_pin_before_launch(tmp_path, monkeypatch):
    from foundry import measurement_harness as harness

    # The private historical F41 corpus is absent from the public repository.
    # Isolate the route boundary; this does not qualify/recreate that corpus.
    monkeypatch.setattr(harness, "_validate_f41_case", lambda *_args: None)
    writer = harness.MeasurementHarness(tmp_path / "trace", "claude", enabled=True)
    calls = []
    request = harness.MeasurementRequest("claude-sonnet-medium", "FOUNDRY-31", "a" * 40, 1)
    with pytest.raises(harness.MeasurementValidationError, match="explicit project alias mapping"):
        harness.run_claude(
            writer, request, task_packet=packet(), root=tmp_path,
            command=("claude", "--model", "{model}", "--effort", "{effort}"),
            timeout_seconds=1, environ={},
            process_factory=lambda *_args, **_kwargs: calls.append(True),
        )
    assert calls == []
    assert not writer.path.exists()


def test_alias_only_availability_diagnoses_pin_identity(tmp_path):
    from foundry.routing import RoutingUnavailableError
    from foundry.routing_facades import claude_route_plan

    with pytest.raises(RoutingUnavailableError, match="alias Claude courts"):
        claude_route_plan("implementer", packet(), root=tmp_path,
                          environ={"FOUNDRY_CLAUDE_AVAILABLE_MODELS": "haiku,sonnet,opus"})
