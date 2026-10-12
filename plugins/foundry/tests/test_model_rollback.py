"""Configuration exercise on disposable copies; no native host is called."""
import importlib.util
from pathlib import Path

import pytest

from foundry.routing import RoutingConfigError, RoutingPolicy, RoutingUnavailableError, UserRouteRequest

PLUGIN = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("model_rollback", PLUGIN / "scripts/check_model_rollback.py")
rollback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rollback)


@pytest.mark.parametrize("host", rollback.EXAMPLES)
@pytest.mark.parametrize("role", rollback.ROLES)
def test_each_host_role_rollback_constructs_invocation_and_restores_policy(host, role):
    result = rollback.exercise(host, role)
    assert result["cli_resolve"] == "passed"
    assert result["existing_policy_restored_byte_exact"]
    assert result["absent_policy_restored_to_absence"]
    assert result["unrelated_configuration_preserved"]
    assert result["native_host_called"] is False
    assert result["executed_identity"] is None
    assert result["backend_availability"] == "unknown"
    invocation = result["constructed_invocation"]
    if host == "codex":
        assert invocation["model"] == result["rollback"]["model"]
        assert invocation["reasoning_effort"] == result["rollback"]["effort"]
        assert invocation["fork_turns"] == "none"
    else:
        assert invocation["effort_parameters"]["transmitted"] == result["rollback"]["effort"]


@pytest.mark.parametrize("host", rollback.EXAMPLES)
def test_rollback_keeps_gate_floors_and_unavailable_incumbent_fails_closed(tmp_path, host):
    policy_path = tmp_path / ".foundry" / "model-routing.json"
    policy_path.parent.mkdir()
    policy_path.write_bytes((PLUGIN / "examples" / rollback.EXAMPLES[host]).read_bytes())
    policy = RoutingPolicy.load(tmp_path)
    economy = policy.resolve("scout", host).model
    for role in ("reviewer", "architect"):
        with pytest.raises(RoutingConfigError):
            policy.resolve(role, host, user=UserRouteRequest(tier="economy"))
        with pytest.raises(RoutingUnavailableError):
            policy.resolve(role, host, available_models={economy})
