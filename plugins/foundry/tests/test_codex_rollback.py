"""Offline rollback verification; no host invocation or installation is performed."""
import json
from pathlib import Path

import pytest

from foundry.escalation import EscalationStore
from foundry.routing import (
    ReviewDeduplicator, RoutingConfigError, RoutingPolicy,
    RoutingUnavailableError, UserRouteRequest,
)
from foundry.routing_facades import codex_spawn_plan

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
OLD = {
    "scout": ("gpt-5.6-luna", "low"),
    "implementer": ("gpt-5.6-terra", "medium"),
    "coordinator": ("gpt-5.6-terra", "medium"),
    "reviewer": ("gpt-5.6-sol", "high"),
    "architect": ("gpt-5.6-sol", "max"),
}
PACKET = "Goal:\nVerify rollback.\nInputs:\nExplicit old policy.\nConstraints:\nDo not delegate.\nDone when:\nReport resolution."


@pytest.fixture
def rollback_root(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_bytes((PLUGIN_ROOT / "examples/codex-gpt-5.6-rollback.json").read_bytes())
    return tmp_path


@pytest.mark.parametrize("role", OLD)
def test_explicit_old_mapping_wins_in_current_runtime(rollback_root, role):
    policy = RoutingPolicy.load(rollback_root)
    route = policy.resolve(role, "codex")
    assert (route.model, route.effort) == OLD[role]
    assert route.sources == {"tier": "default", "model": "project", "effort": "project"}
    assert policy.resolve(role, "claude").sources["model"] == "default"


def test_rollback_preserves_gate_floors_and_declared_fallback(rollback_root):
    policy = RoutingPolicy.load(rollback_root)
    route = policy.resolve("implementer", "codex", available_models={"gpt-5.6-luna"})
    assert (route.selected_tier, route.model) == ("economy", "gpt-5.6-luna")
    with pytest.raises(RoutingUnavailableError):
        policy.resolve("reviewer", "codex", available_models={"gpt-5.6-luna", "gpt-5.6-terra"})
    for role in ("reviewer", "architect"):
        with pytest.raises(RoutingConfigError):
            policy.resolve(role, "codex", user=UserRouteRequest(effort="low"))
    with pytest.raises(RoutingUnavailableError):
        policy.resolve("implementer", "codex", available_models={"gpt-6.1-sol"})


def test_rollback_does_not_reset_escalation_or_review_proof(rollback_root):
    state = rollback_root / "state"
    store = EscalationStore(str(rollback_root), state_dir=state)
    for _ in range(2):
        store.record_failure("PAT-15", "implementer", "test_red", "balanced")
    ledger = ReviewDeduplicator(str(rollback_root), state)
    claim = ledger.claim(b"historical frozen diff")
    proof_dir = state / "acceptance-proofs"
    proof_dir.mkdir(exist_ok=True)
    proof = proof_dir / "historical.json"
    proof.write_text(json.dumps({"source_sha": "7adc33793cc04dfd5102f1e11a5d47cb6ecc9cd0", "quality": "blocked"}))
    before = {p: p.read_bytes() for p in state.rglob("*.json")}
    plan = codex_spawn_plan(
        "implementer", PACKET, root=rollback_root, issue_id="PAT-15",
        escalation_state_dir=state,
    )
    assert (plan["route"]["selected_tier"], plan["spawn"]["model"], plan["spawn"]["reasoning_effort"]) == (
        "frontier", "gpt-5.6-sol", "high",
    )
    assert {p: p.read_bytes() for p in state.rglob("*.json")} == before
    assert ledger.claim(b"historical frozen diff").should_run is False
    assert claim.should_run is True


def test_explicit_old_user_pin_is_never_replaced(tmp_path):
    route = RoutingPolicy.load(tmp_path).resolve(
        "implementer", "codex", user=UserRouteRequest(model="gpt-5.6-terra", effort="medium"),
    )
    assert (route.model, route.effort) == OLD["implementer"]
    assert route.sources["model"] == "user"


@pytest.mark.parametrize("role", OLD)
def test_new_defaults_do_not_invent_fallback_to_old_account_models(tmp_path, role):
    with pytest.raises(RoutingUnavailableError):
        RoutingPolicy.load(tmp_path).resolve(
            role, "codex",
            available_models={model for model, _effort in OLD.values()},
        )
