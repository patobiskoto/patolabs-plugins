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
    assert len(GENERATED) == 64
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
