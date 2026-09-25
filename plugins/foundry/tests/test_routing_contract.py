import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from foundry.routing import ReviewDeduplicator, RoutingPolicy
from foundry.routing_facades import AGENT_IDENTITIES, codex_spawn_plan


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_hook(filename, module_name):
    path = PLUGIN_ROOT / "hooks" / f"{filename}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


route_agent = _load_hook("route_agent", "route_agent_contract")


def _packet():
    return (
        "Goal:\nValidate the resolved invocation contract.\n"
        "Inputs:\nIssue, accepted ADRs, and bounded repository paths.\n"
        "Constraints:\nPreserve unrelated work and do not delegate.\n"
        "Done when:\nReturn evidence without widening scope."
    )


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert match, path
    return match.group(1)


@pytest.mark.parametrize(
    ("role", "profile", "model", "effort", "turns", "capability"),
    [
        ("scout", "routed-readonly-low", "haiku", "low", 10, "readonly"),
        ("implementer", "routed-worker-medium", "sonnet", "medium", 50,
         "worker"),
        ("reviewer", "routed-readonly-high", "opus", "high", 24,
         "readonly"),
        ("architect", "routed-readonly-high", "fable", "high", 30,
         "readonly"),
    ],
)
def test_ci_validates_resolved_claude_invocation_without_model_frontmatter(
    tmp_path, role, profile, model, effort, turns, capability,
):
    updated, _ = route_agent.route_tool_input(
        {"subagent_type": f"foundry:{role}", "prompt": _packet()},
        cwd=tmp_path,
        environ={},
    )

    assert updated["subagent_type"] == f"foundry:{profile}"
    assert updated["model"] == model
    assert updated["max_turns"] == turns

    identity = AGENT_IDENTITIES[role].lower()
    logical = _frontmatter(PLUGIN_ROOT / "agents" / f"{identity}.md")
    assert f"name: {identity}" in logical
    execution = _frontmatter(PLUGIN_ROOT / "agents" / f"{profile}.md")
    assert not re.search(r"(?m)^(model|effort|maxTurns):", logical)
    assert re.search(r"(?m)^tools: Read$", logical)
    assert not re.search(r"(?m)^(model|maxTurns):", execution)
    assert re.search(rf"(?m)^effort: {effort}$", execution)

    tools = re.search(r"(?m)^tools: (.+)$", execution).group(1).split(", ")
    assert "Agent" not in tools and "Task" not in tools
    if capability == "readonly":
        assert "Write" not in tools and "Edit" not in tools
    else:
        assert "Write" in tools and "Edit" in tools


def test_claude_xhigh_route_uses_a_shipped_profile_through_the_real_hook(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"claude": {"balanced": {"effort": "xhigh"}}},
    }), encoding="utf-8")

    updated, _ = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": _packet()},
        cwd=tmp_path,
        environ={},
    )

    assert updated["subagent_type"] == "foundry:routed-worker-xhigh"
    profile = PLUGIN_ROOT / "agents" / "routed-worker-xhigh.md"
    assert profile.is_file()
    assert "effort: xhigh" in _frontmatter(profile)


@pytest.mark.parametrize(
    ("role", "agent_type", "model", "effort"),
    [
        ("scout", "explorer", "gpt-5.6-luna", "low"),
        ("implementer", "worker", "gpt-5.6-terra", "medium"),
        ("reviewer", "reviewer", "gpt-5.6-sol", "high"),
        ("architect", "default", "gpt-5.6-sol", "max"),
    ],
)
def test_ci_validates_resolved_codex_invocation(role, agent_type, model, effort, tmp_path):
    claim = None
    if role == "reviewer":
        claim = {
            **ReviewDeduplicator("owner/repo", tmp_path / "state").claim(
                b"routing contract",
            ).to_dict(),
            "root": str(tmp_path),
            "base": "1" * 40,
        }

    plan = codex_spawn_plan(role, _packet(), root=tmp_path, review_claim=claim)

    assert plan["spawn"]["agent_type"] == agent_type
    assert plan["spawn"]["model"] == model
    assert plan["spawn"]["reasoning_effort"] == effort
    assert plan["spawn"]["fork_turns"] == "none"
    assert "max_turns" not in plan["spawn"]
    message = plan["spawn"]["message"].lower()
    assert "do not delegate" in message
    if role in {"scout", "reviewer", "architect"}:
        assert "read-only" in message
        assert "do not edit" in message
    else:
        assert "bounded implementation scope" in message


def test_minimal_project_override_example_is_executable(tmp_path):
    example = PLUGIN_ROOT / "examples" / "model-routing.json"
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    assert json.loads(example.read_text(encoding="utf-8")) == {
        "version": 1,
        "roles": {"implementer": "frontier"},
    }
    route = RoutingPolicy.load(tmp_path).resolve("implementer", "codex")
    assert route.selected_tier == "frontier"
    assert route.model == "gpt-5.6-sol"
    assert route.effort == "high"


def test_pilot_v1_protocol_and_manual_sheet_are_frozen_before_p01():
    protocol = (PLUGIN_ROOT / "docs" / "model-routing-pilot-v1.md").read_text(
        encoding="utf-8",
    )
    results = (PLUGIN_ROOT / "docs" / "model-routing-pilot-results-v1.md").read_text(
        encoding="utf-8",
    )

    assert "`FOUNDRY-MR-PILOT-v1`" in protocol
    assert "| Status | `FROZEN` |" in protocol
    assert "before pilot issue P-01" in protocol
    assert "actual_cost(i) = Σ(j,k)" in protocol
    assert "counterfactual_cost(i) = Σ(j,k)" in protocol
    assert "main_loop_cost(i)" in protocol and "subagent_cost(i)" in protocol
    assert "Tokens are not equivalent across models" in protocol
    assert "invalidates the whole series" in protocol
    assert "adds no usage hook" in protocol
    assert all(category in protocol for category in (
        "uncached_input", "cache_read", "cache_write_standard",
        "cache_write_extended", "output",
    ))
    assert "cache_creation_input_tokens" in protocol
    assert "cache_write_tokens" in protocol
    assert "subtract both once to derive" in protocol
    assert "Never also price the aggregate input count" in protocol

    price_header = (
        "| host | model | uncached_input_usd_per_mtok | cache_read_usd_per_mtok | "
        "cache_write_standard_usd_per_mtok | cache_write_extended_usd_per_mtok | "
        "output_usd_per_mtok | source | captured_at_utc |"
    )
    invocation_header = (
        "| slot | issue_id | issue_type | estimate | host | component | role | model | "
        "effort | escalation_count | failure_signal | uncached_input_tokens | "
        "cache_read_tokens | cache_write_standard_tokens | cache_write_extended_tokens "
        "| output_tokens | retries | actual_cost_usd | review_verdict | evidence_note |"
    )
    summary_header = (
        "| slot | issue_id | issue_type | estimate | host | primary_model | "
        "primary_effort | main_loop_cost_usd | subagent_cost_usd | actual_cost_usd | "
        "counterfactual_cost_usd | savings_usd | savings_pct | escalation_count | retries "
        "| review_verdict | valid |"
    )
    assert price_header in results
    assert invocation_header in results
    assert summary_header in results
    summary_section = results.split("## Per-issue summary", 1)[1].split(
        "## Pilot totals and decision", 1,
    )[0]
    assert summary_section.count("| P-") == 10
    assert "Status: `COMPLETE`" in results
    assert "AC6: `INCONCLUSIVE`" in results
    assert "V2 must freeze that baseline" in results
    ledger = results.split("## Invocation ledger", 1)[1].split("## Per-issue summary", 1)[0]
    for slot, expected in {"P-03": 2, "P-05": 3, "P-06": 2, "P-07": 2, "P-09": 2, "P-10": 2}.items():
        rows = [line for line in ledger.splitlines() if line.startswith(f"| {slot} |")]
        implementation_rows = [line for line in rows if "| implementer |" in line]
        assert len(implementation_rows) == expected
        detailed = [line for line in implementation_rows if "local Codex usage delta;" in line]
        assert all("model calls aggregated" in line for line in detailed)
        assert not any("initial run and retry aggregated" in line for line in detailed)
    assert "No prompt, response, diff" in results
