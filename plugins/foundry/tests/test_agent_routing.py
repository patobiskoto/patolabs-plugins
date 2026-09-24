import importlib.util
import io
import json
import multiprocessing
import os
import re
import sys
from pathlib import Path

import pytest

import foundry.routing_facades as routing_facades
from foundry.routing import (
    ReviewClaim,
    RoutingConfigError,
    RoutingUnavailableError,
    UserRouteRequest,
)
from foundry.escalation import EscalationStore, EscalationTechnicalBlockedError
from foundry.routing_facades import (
    AGENT_IDENTITIES,
    CLAUDE_ALIAS_MODEL_OVERRIDES,
    CLAUDE_AVAILABLE_MODELS,
    claude_available_models,
    claude_invocation_model,
    claude_policy_model,
    claude_route_plan,
    claude_user_request,
    codex_invocation_completed,
    codex_spawn_plan,
)
from foundry import local_scout
from foundry.telemetry import TelemetryObserver


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_hook(name):
    path = PLUGIN_ROOT / "hooks" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


route_agent = _load_hook("route_agent")
readonly_guard = _load_hook("guard_routed_readonly")


def _packet(body="Inspect the requested scope."):
    return (
        f"Goal:\n{body}\n"
        "Inputs:\nIssue and relevant paths.\n"
        "Constraints:\nAccepted ADRs; preserve unrelated work.\n"
        "Done when:\nReturn evidence and validation."
    )


def _concurrent_cross_host_correction(args):
    host, root, state_dir, issue = args
    os.environ["FOUNDRY_DATA"] = state_dir
    try:
        if host == "claude":
            plan = claude_route_plan(
                "implementer",
                f'FOUNDRY_ROUTE_REQUEST={{"issue":"{issue}"}}\n' + _packet(),
                root=root,
                environ={},
            )
            return host, plan.get("credited_correction_plan_claimed") is True
        plan = codex_spawn_plan(
            "implementer", _packet(), root=root, issue_id=issue,
            escalation_state_dir=state_dir,
        )
        return host, plan["escalation"].get("credited_correction_plan_claimed") is True
    except EscalationTechnicalBlockedError:
        return host, False


def _credited_cross_host_state(root, state_dir, issue):
    store = EscalationStore.for_root(root, state_dir=state_dir)
    store.record_risk(issue, "implementer", "adr_creation", "apex")
    store.record_failure(issue, "implementer", "test_red", "economy")
    store.record_human_verdict(
        issue, "implementer", "economy", category="strategy_decision",
    )
    first_generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", first_generation, 1)
    store.record_failure(issue, "implementer", "review_blocking_after_fix", "apex")
    store.record_failure(issue, "implementer", "review_blocking_after_fix", "apex")
    generation = store.status(issue)["halt_generation"]
    store.resume_technical_remediation(issue, generation, "a" * 64)
    store.claim_technical_remediation_route(
        issue, "implementer", generation, "cross-host-local-route-0001",
    )
    store.rearm_remediation(
        issue, "implementer", "manual_retry_approved", first_generation, 1,
        current_halt_generation=generation,
    )
    diff_hash = "b" * 64
    store.claim_fresh_reviewer_authorization(
        issue, diff_hash,
        validated_claim=lambda: ReviewClaim(
            diff_hash, True, "in_progress", 1, "f" * 64,
        ),
    )
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
        validated_blocking_proof=lambda: {
            "proof_id": "c" * 64,
            "completed_at": "2030-01-01T00:00:00Z",
            "quality": "blocked", "all_pass": False,
            "diff_hash": diff_hash, "generation": 1,
            "claim_digest": "d" * 64,
            "coordinates": {"root": str(Path(root).resolve()), "base": "e" * 40},
        },
    )
    return store


@pytest.mark.parametrize(
    ("role", "identity", "agent", "model", "turns"),
    [
        ("scout", "Lupin", "foundry:routed-readonly-low", "haiku", 10),
        ("implementer", "Eiffel", "foundry:routed-worker-medium", "sonnet", 50),
        ("reviewer", "Maigret", "foundry:routed-readonly-high", "opus", 24),
        ("architect", "Vauban", "foundry:routed-readonly-high", "fable", 30),
    ],
)
def test_logical_roles_rewrite_the_actual_claude_invocation(
    tmp_path, role, identity, agent, model, turns,
):
    result = route_agent.route_tool_input(
        {"subagent_type": f"foundry:{identity.lower()}", "prompt": _packet()},
        cwd=tmp_path,
        environ={},
    )

    updated, context = result
    assert updated["subagent_type"] == agent
    assert updated["model"] == model
    assert updated["max_turns"] == turns
    assert updated["prompt"].startswith("FOUNDRY_ROUTED_AGENT_V1\n")
    assert "--- BEGIN ROLE CONTRACT ---" in updated["prompt"]
    assert "# Routed entrypoint" in updated["prompt"]
    assert '"availability_probed": false' in context


def test_legacy_claude_role_identifiers_are_deterministic_identity_aliases(tmp_path):
    for role, identity in AGENT_IDENTITIES.items():
        updated, _ = route_agent.route_tool_input(
            {"subagent_type": f"foundry:{role}", "prompt": _packet()},
            cwd=tmp_path,
            environ={},
        )
        assert route_agent._logical_role(f"foundry:{identity.lower()}") == role
        assert route_agent._logical_role(f"foundry:{role}") == role
        assert f"foundry:{identity.lower()}" in updated["prompt"]


def test_bare_identity_and_semantic_role_names_are_outside_foundry_routing(tmp_path):
    for role, identity in AGENT_IDENTITIES.items():
        for bare_name in (identity, identity.lower(), role):
            assert route_agent._logical_role(bare_name) is None
            assert route_agent.route_tool_input(
                {"subagent_type": bare_name, "prompt": _packet()},
                cwd=tmp_path,
                environ={},
            ) is None


def test_local_fallback_claude_plan_matches_the_actual_hook_route(tmp_path):
    policy = tmp_path / ".foundry" / "model-routing.json"
    policy.parent.mkdir()
    policy.write_text(
        json.dumps({"version": 1, "roles": {"scout": "balanced"}}),
        encoding="utf-8",
    )
    settings = local_scout.LocalScoutSettings(
        True, local_scout.LocalEndpoint("127.0.0.1", 11434), "m",
        cloud_fallback_enabled=True,
    )
    plan = local_scout.cloud_fallback_spawn_plan(
        local_scout._error("UNAVAILABLE", "safe"), settings,
        local_scout.capture_logs("x" * 5_000),
        root=tmp_path, host="claude", issue_id="FOUNDRY-99", environ={},
    )

    updated, context = route_agent.route_tool_input(
        plan["spawn"], cwd=tmp_path, environ={},
    )
    visible = json.loads(context.removeprefix("Foundry Claude route: "))

    assert len(plan["spawn"]["prompt"].split("\n", 1)[1]) <= 4_000
    assert '"issue":"FOUNDRY-99"' in plan["spawn"]["prompt"].split("\n", 1)[0]
    assert plan["fallback"]["route"] == plan["host_plan"]["route"]
    assert visible["selected_tier"] == plan["fallback"]["resolved_tier"]
    assert visible["model"] == plan["fallback"]["route"]["model"]
    assert visible["effort"] == plan["fallback"]["route"]["effort"]
    assert visible["escalation"]["issue_id"] == "FOUNDRY-99"
    assert updated["subagent_type"] == "foundry:routed-readonly-medium"
    assert plan["spawn"]["subagent_type"] == "foundry:scout"
    assert "foundry:lupin" in updated["prompt"]


def test_local_fallback_claude_plan_survives_an_active_scout_floor(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    store.record_failure("FOUNDRY-99", "scout", "test_red", "economy")
    store.record_failure("FOUNDRY-99", "scout", "test_red", "economy")
    settings = local_scout.LocalScoutSettings(
        True, local_scout.LocalEndpoint("127.0.0.1", 11434), "m",
        cloud_fallback_enabled=True,
    )

    plan = local_scout.cloud_fallback_spawn_plan(
        local_scout._error("UNAVAILABLE", "safe"), settings,
        local_scout.capture_logs("safe\n"),
        root=tmp_path, host="claude", issue_id="FOUNDRY-99", environ={},
    )
    updated, context = route_agent.route_tool_input(
        plan["spawn"], cwd=tmp_path, environ={},
    )
    visible = json.loads(context.removeprefix("Foundry Claude route: "))

    assert plan["host_plan"]["escalation"]["minimum_tier"] == "balanced"
    assert plan["fallback"]["route"] == plan["host_plan"]["route"]
    assert visible["selected_tier"] == plan["fallback"]["resolved_tier"] == "balanced"
    assert visible["model"] == plan["fallback"]["route"]["model"]
    assert visible["effort"] == plan["fallback"]["route"]["effort"]
    assert '"model"' not in plan["spawn"]["prompt"].split("\n", 1)[0]
    assert updated["subagent_type"] == "foundry:routed-readonly-medium"


def test_user_request_wins_over_project_policy_at_invocation(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "roles": {"implementer": "frontier"},
        "mappings": {"claude": {"economy": {"model": "sonnet-5"}}},
    }), encoding="utf-8")
    prompt = (
        'FOUNDRY_ROUTE_REQUEST={"tier":"economy","model":"haiku",'
        '"effort":"max"}\n' + _packet()
    )

    updated, context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": prompt},
        cwd=tmp_path,
        environ={},
    )

    assert updated["model"] == "haiku"
    assert updated["subagent_type"] == "foundry:routed-worker-max"
    assert "FOUNDRY_ROUTE_REQUEST=" not in updated["prompt"]
    assert '"tier": "user"' in context
    assert '"model": "user"' in context
    assert '"effort": "user"' in context


@pytest.mark.parametrize(
    ("configured", "available", "canonical_model", "wire_model"),
    [
        (configured, available, canonical, wire)
        for canonical, wire, spellings in (
            ("haiku-4.5", "haiku", ("haiku", "haiku-4.5", "claude-haiku-4-5")),
            ("sonnet-5", "sonnet", ("sonnet", "sonnet-5", "claude-sonnet-5")),
            ("opus-5", "opus", ("opus", "opus-5", "claude-opus-5")),
            ("fable-5", "fable", ("fable", "fable-5", "claude-fable-5")),
        )
        for configured in spellings
        for available in spellings
    ],
)
def test_claude_project_mapping_composes_with_all_availability_spellings(
    tmp_path, configured, available, canonical_model, wire_model,
):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"claude": {"economy": {"model": configured}}},
    }), encoding="utf-8")

    updated, context = route_agent.route_tool_input(
        {"subagent_type": "foundry:scout", "prompt": _packet()},
        cwd=tmp_path,
        environ={CLAUDE_AVAILABLE_MODELS: available},
    )
    route = json.loads(context.removeprefix("Foundry Claude route: "))

    assert updated["model"] == wire_model
    assert route["model"] == canonical_model
    assert route["selected_tier"] == "economy"
    assert route["sources"]["model"] == "project"


def test_claude_project_mapping_is_canonical_in_pending_telemetry(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"claude": {"economy": {"model": "claude-haiku-4-5"}}},
    }), encoding="utf-8")
    data_dir = tmp_path / "state"
    data_dir.mkdir()

    updated, _ = route_agent.route_tool_input(
        {"subagent_type": "foundry:scout", "prompt": _packet()},
        cwd=tmp_path,
        environ={
            CLAUDE_AVAILABLE_MODELS: "haiku",
            "FOUNDRY_DATA": str(data_dir),
        },
        correlation="tool-use-project-mapping",
    )
    pending = list((data_dir / "telemetry" / "pending").iterdir())
    event = json.loads(pending[0].read_text(encoding="utf-8"))["event"]

    assert len(pending) == 1
    assert updated["model"] == "haiku"
    assert event["model"] == "haiku-4.5"


def test_claude_project_mapping_rejects_non_host_model(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"claude": {"balanced": {"model": "project-model"}}},
    }), encoding="utf-8")

    with pytest.raises(
        RoutingConfigError,
        match=r"project-model.*sans traduction hôte.*claude_models\.project-model.*alias Agent",
    ):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:implementer", "prompt": _packet()},
            cwd=tmp_path,
            environ={CLAUDE_AVAILABLE_MODELS: "haiku"},
        )


def test_claude_project_mapping_cannot_override_a_built_in_alias(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "claude_models": {"opus-5": "haiku"},
    }), encoding="utf-8")

    with pytest.raises(RoutingConfigError, match="claude_models.opus-5.*intégré incompatible.*'opus'"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:reviewer", "prompt": _packet()},
            cwd=tmp_path, environ={},
        )


def test_claude_scope_requires_installed_effort_profile(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "effort_scopes": {"claude": {"default": {
            "version": 2, "levels": ["low", "medium", "deep"], "inadmissible": {},
        }}},
    }), encoding="utf-8")

    with pytest.raises(RoutingConfigError, match="profils Agent Claude absents pour deep"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:implementer", "prompt": _packet()},
            cwd=tmp_path, environ={},
        )


def test_agent_model_input_is_an_explicit_user_override(tmp_path):
    updated, context = route_agent.route_tool_input(
        {
            "subagent_type": "foundry:implementer",
            "prompt": _packet(),
            "model": "claude-opus-5",
        },
        cwd=tmp_path,
        environ={},
    )

    assert updated["model"] == "opus"
    assert '"model": "user"' in context

    updated, _ = route_agent.route_tool_input(
        {
            "subagent_type": "foundry:implementer",
            "prompt": _packet(),
            "model": "opus",
        },
        cwd=tmp_path,
        environ={CLAUDE_AVAILABLE_MODELS: "opus-5"},
    )
    assert updated["model"] == "opus"


def test_conflicting_user_model_channels_fail_loudly(tmp_path):
    prompt = 'FOUNDRY_ROUTE_REQUEST={"model":"one"}\n' + _packet()

    with pytest.raises(RoutingConfigError, match="demande Claude ambiguë"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:implementer", "prompt": prompt, "model": "two"},
            cwd=tmp_path,
            environ={},
        )


def test_direct_claude_model_is_translated_before_telemetry_is_prepared(tmp_path):
    data_dir = tmp_path / "state"
    with pytest.raises(RoutingConfigError, match="missing-model.*traduction hôte inconnue"):
        route_agent.route_tool_input(
            {
                "subagent_type": "foundry:implementer",
                "prompt": 'FOUNDRY_ROUTE_REQUEST={"model":"missing-model"}\n' + _packet(),
            },
            cwd=tmp_path,
            environ={"FOUNDRY_DATA": str(data_dir)},
            correlation="a" * 32,
        )
    assert not (data_dir / "telemetry" / "journal.ndjson").exists()


def test_unknown_direct_claude_model_cannot_be_hidden_by_availability_fallback(tmp_path):
    with pytest.raises(RoutingConfigError, match="missing-model.*traduction hôte inconnue"):
        route_agent.route_tool_input(
            {
                "subagent_type": "foundry:implementer",
                "prompt": 'FOUNDRY_ROUTE_REQUEST={"model":"missing-model"}\n' + _packet(),
            },
            cwd=tmp_path,
            environ={CLAUDE_AVAILABLE_MODELS: "haiku-4.5"},
        )


def test_direct_public_codex_model_keeps_a_completion_capability(monkeypatch, tmp_path):
    data_dir = tmp_path / "state"
    data_dir.mkdir()
    monkeypatch.setenv("FOUNDRY_DATA", str(data_dir))
    plan = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path,
        user=UserRouteRequest(model="gpt-6-future"),
    )

    capability = plan["telemetry"]["completion_capability"]
    assert codex_invocation_completed(
        TelemetryObserver.from_environ(), capability,
        status="completed", failure_class="none",
    ) is not None
    event = json.loads((data_dir / "telemetry" / "journal.ndjson").read_text())
    assert event["model"] == "gpt-6-future"


def test_builtin_claude_translation_does_not_load_ambient_project_policy(monkeypatch):
    monkeypatch.setattr(
        routing_facades,
        "_project_claude_models",
        lambda _root: pytest.fail("un modèle Claude intégré ne lit pas de policy projet"),
    )
    assert claude_invocation_model("opus-5", root="/missing") == "opus"


def test_ordinary_fallback_moves_down_and_is_visible(tmp_path):
    updated, context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": _packet()},
        cwd=tmp_path,
        environ={CLAUDE_AVAILABLE_MODELS: "haiku-4.5"},
    )

    assert updated["model"] == "haiku"
    assert updated["subagent_type"] == "foundry:routed-worker-low"
    assert "MODEL_FALLBACK_DOWN" in context
    assert "descend vers economy" in context
    assert '"availability_probed": true' in context


def test_reviewer_fallback_moves_up_and_never_down(tmp_path):
    updated, context = route_agent.route_tool_input(
        {"subagent_type": "foundry:reviewer", "prompt": _packet()},
        cwd=tmp_path,
        environ={CLAUDE_AVAILABLE_MODELS: "fable-5,haiku-4.5"},
    )

    assert updated["model"] == "fable"
    assert "monte vers apex" in context

    with pytest.raises(RoutingUnavailableError, match="gate 'reviewer'"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:reviewer", "prompt": _packet()},
            cwd=tmp_path,
            environ={CLAUDE_AVAILABLE_MODELS: "haiku-4.5,sonnet-5"},
        )


@pytest.mark.parametrize("role", ["reviewer", "architect"])
def test_claude_gate_cannot_be_demoted_by_user_model_or_effort(tmp_path, role):
    with pytest.raises(RoutingConfigError, match="modèle direct"):
        route_agent.route_tool_input(
            {
                "subagent_type": f"foundry:{role}",
                "prompt": 'FOUNDRY_ROUTE_REQUEST={"model":"haiku"}\n' + _packet(),
            },
            cwd=tmp_path,
            environ={},
        )
    with pytest.raises(RoutingConfigError, match="effort 'high'"):
        route_agent.route_tool_input(
            {
                "subagent_type": f"foundry:{role}",
                "prompt": 'FOUNDRY_ROUTE_REQUEST={"effort":"low"}\n' + _packet(),
            },
            cwd=tmp_path,
            environ={},
        )


@pytest.mark.parametrize(
    "signal",
    (
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_EFFORT_LEVEL",
        *CLAUDE_ALIAS_MODEL_OVERRIDES,
    ),
)
def test_claude_host_override_is_signalled_without_its_value(tmp_path, signal):
    secret_value = f"private-{signal.lower()}"
    _, context = route_agent.route_tool_input(
        {"subagent_type": "foundry:scout", "prompt": _packet()},
        cwd=tmp_path,
        environ={signal: secret_value},
    )

    assert signal in context
    assert "neutraliser" in context
    assert secret_value not in context


def test_claude_host_override_warnings_are_deterministic(tmp_path):
    signals = (*reversed(CLAUDE_ALIAS_MODEL_OVERRIDES), "CLAUDE_CODE_SUBAGENT_MODEL")
    _, context = route_agent.route_tool_input(
        {"subagent_type": "foundry:scout", "prompt": _packet()},
        cwd=tmp_path,
        environ={signal: "masked" for signal in signals},
    )
    warnings = json.loads(context.removeprefix("Foundry Claude route: "))["warnings"]

    assert [
        re.search(r"hôte '([^']+)'", warning["message"]).group(1)
        for warning in warnings
    ] == sorted(signals)


def test_claude_route_applies_issue_scoped_escalation_floor(monkeypatch, tmp_path):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    store.record_failure("FOUNDRY-42", "implementer", "test_red", "balanced")
    store.record_failure("FOUNDRY-42", "implementer", "test_red", "balanced")
    prompt = 'FOUNDRY_ROUTE_REQUEST={"issue":"FOUNDRY-42"}\n' + _packet()

    updated, context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": prompt},
        cwd=tmp_path,
        environ={},
    )

    assert updated["model"] == "opus"
    assert updated["subagent_type"] == "foundry:routed-worker-high"
    assert '"minimum_tier": "frontier"' in context
    assert '"issue_id": "FOUNDRY-42"' in context
    assert '"max_parallel_agents": 4' in context


def test_claude_and_codex_keep_the_same_floor_after_a_human_resume(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    store.record_failure("FOUNDRY-17", "implementer", "test_red", "balanced")
    store.record_failure("FOUNDRY-17", "implementer", "test_red", "balanced")
    store.record_human_verdict(
        "FOUNDRY-17", "scout", "balanced", category="strategy_decision",
    )
    assert store.status("FOUNDRY-17")["halted"] is True
    store.resume("FOUNDRY-17", "manual_retry_approved", 1)

    prompt = 'FOUNDRY_ROUTE_REQUEST={"issue":"FOUNDRY-17"}\n' + _packet()
    _, claude_context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": prompt},
        cwd=tmp_path,
        environ={},
    )
    codex = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id="FOUNDRY-17",
        escalation_state_dir=state_dir,
    )

    assert '"minimum_tier": "frontier"' in claude_context
    assert codex["escalation"]["minimum_tier"] == "frontier"


def test_claude_and_codex_expose_the_same_remediation_ledger(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    store.record_human_verdict(
        "FOUNDRY-61", "implementer", "frontier", category="strategy_decision",
    )
    generation = store.status("FOUNDRY-61")["halt_generation"]
    store.resume("FOUNDRY-61", "remediation_reviewed", generation, 3)
    prompt = 'FOUNDRY_ROUTE_REQUEST={"issue":"FOUNDRY-61"}\n' + _packet()
    _, claude_context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": prompt}, cwd=tmp_path, environ={},
    )
    codex = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id="FOUNDRY-61",
        escalation_state_dir=state_dir,
    )
    claude = json.loads(claude_context.removeprefix("Foundry Claude route: "))
    assert claude["escalation"]["remediation_authorization"] == codex["escalation"]["remediation_authorization"]
    assert claude["escalation"]["remediation_authorization"] == {
        "state": "active", "role": "implementer", "halt_generation": generation,
        "maximum_credits": 3, "remaining_credits": 3,
    }


def test_technical_remediation_requires_an_explicit_route_on_both_facades(
    monkeypatch, tmp_path,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    issue = "FOUNDRY-107"
    for tier in (
        "economy", "economy", "balanced", "balanced", "frontier", "frontier",
    ):
        store.record_failure(issue, "implementer", "test_red", tier)
    generation = store.status(issue)["halt_generation"]
    store.resume_technical_remediation(issue, generation, "a" * 64)

    def no_provider(*args, **kwargs):
        raise AssertionError("la préparation locale ne doit appeler aucun provider")

    monkeypatch.setattr(routing_facades, "run_claude_measurement", no_provider)
    monkeypatch.setattr(routing_facades, "run_codex_measurement", no_provider)

    ordinary_prompt = f'FOUNDRY_ROUTE_REQUEST={{"issue":"{issue}"}}\n' + _packet()
    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:implementer", "prompt": ordinary_prompt},
            cwd=tmp_path, environ={},
        )
    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        codex_spawn_plan(
            "implementer", _packet(), root=tmp_path, issue_id=issue,
            escalation_state_dir=state_dir,
        )

    route_id = "f107-local-route-0001"
    with pytest.raises(EscalationTechnicalBlockedError, match="réclamation atomique"):
        route_agent.route_tool_input(
            {
                "subagent_type": "foundry:implementer",
                "prompt": (
                    f'FOUNDRY_ROUTE_REQUEST={{"issue":"{issue}",'
                    f'"technical_remediation":true,'
                    f'"technical_remediation_id":"{route_id}"}}\n' + _packet()
                ),
            },
            cwd=tmp_path, environ={},
        )
    claimed = store.claim_technical_remediation_route(
        issue, "implementer", generation, route_id,
    )
    assert claimed["provider_effect_allowed"] is False
    assert claimed["campaign_restart_allowed"] is False

    technical_prompt = (
        f'FOUNDRY_ROUTE_REQUEST={{"issue":"{issue}",'
        f'"technical_remediation":true,'
        f'"technical_remediation_id":"{route_id}"}}\n' + _packet()
    )
    claude = claude_route_plan(
        "implementer", technical_prompt, root=tmp_path, environ={},
    )
    assert claude["technical_remediation_local_only"] is True
    with pytest.raises(RoutingConfigError, match="invocation Agent/provider est refusée"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:implementer", "prompt": technical_prompt},
            cwd=tmp_path, environ={},
        )
    codex = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id=issue,
        escalation_state_dir=state_dir, technical_remediation=True,
        technical_remediation_id=route_id,
    )
    assert codex["mode"] == "local_diagnostic"
    assert codex["spawn"] is None
    assert "Do not invoke a provider" in codex["local_instructions"]
    assert "fresh Foundry review claim" in codex["local_instructions"]
    assert codex["escalation"]["technical_remediation_open"] is False
    assert codex["escalation"]["technical_remediation_requested"] is True
    assert codex["escalation"]["technical_remediation_claimed"] is True
    assert codex["escalation"]["provider_effect_allowed"] is False
    assert codex["escalation"]["campaign_restart_allowed"] is False
    with pytest.raises(EscalationTechnicalBlockedError, match="rôle arrêté"):
        codex_spawn_plan(
            "scout", _packet(), root=tmp_path, issue_id=issue,
            escalation_state_dir=state_dir, technical_remediation=True,
            technical_remediation_id=route_id,
        )


def test_concurrent_claude_and_codex_contenders_share_one_correction_claim(
    monkeypatch, tmp_path,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    issue = "PAT-34"
    store = _credited_cross_host_state(tmp_path, state_dir, issue)

    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_cross_host_correction, [
            ("claude", str(tmp_path), str(state_dir), issue),
            ("codex", str(tmp_path), str(state_dir), issue),
        ])

    assert sorted(claimed for _host, claimed in results) == [False, True]
    assert {host for host, _claimed in results} == {"claude", "codex"}
    status = store.status(issue)
    assert len(status["credited_correction_claim_audit"]) == 1
    assert status["credited_correction_claim_audit"][0]["role"] == "implementer"
    with pytest.raises(EscalationTechnicalBlockedError, match="unique plan"):
        claude_route_plan(
            "implementer",
            f'FOUNDRY_ROUTE_REQUEST={{"issue":"{issue}"}}\n' + _packet(),
            root=tmp_path,
            environ={},
        )


def test_claude_and_codex_expose_exhaustion_and_the_same_rearm_audit(
    monkeypatch, tmp_path,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    issue = "FOUNDRY-90"
    store.record_risk(issue, "implementer", "adr_creation", "apex")
    store.record_human_verdict(
        issue, "implementer", "apex", category="strategy_decision",
    )
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 1)
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
    )
    prompt = f'FOUNDRY_ROUTE_REQUEST={{"issue":"{issue}"}}\n' + _packet()

    _, exhausted_context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": prompt},
        cwd=tmp_path, environ={},
    )
    exhausted_codex = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id=issue,
        escalation_state_dir=state_dir,
    )
    exhausted_claude = json.loads(
        exhausted_context.removeprefix("Foundry Claude route: ")
    )
    assert exhausted_claude["escalation"]["remediation_authorization"]["state"] == (
        "exhausted"
    )
    assert exhausted_codex["escalation"]["remediation_authorization"]["state"] == (
        "exhausted"
    )

    result = store.rearm_remediation(
        issue, "implementer", "manual_retry_approved", generation, 2,
    )
    assert result["action"] == "remediation_rearmed"
    _, rearmed_context = route_agent.route_tool_input(
        {"subagent_type": "foundry:implementer", "prompt": prompt},
        cwd=tmp_path, environ={},
    )
    rearmed_codex = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id=issue,
        escalation_state_dir=state_dir,
    )
    rearmed_claude = json.loads(
        rearmed_context.removeprefix("Foundry Claude route: ")
    )
    assert rearmed_claude["escalation"]["remediation_authorization"] == (
        rearmed_codex["escalation"]["remediation_authorization"]
    )
    assert rearmed_claude["escalation"]["remediation_rearm_audit"] == (
        rearmed_codex["escalation"]["remediation_rearm_audit"]
    )
    assert rearmed_claude["escalation"]["remediation_rearm_audit"] == (
        store.status(issue)["remediation_rearm_audit"]
    )


def test_turns_are_bounded_and_packet_shape_and_size_are_enforced(tmp_path):
    updated, _ = route_agent.route_tool_input(
        {
            "subagent_type": "foundry:scout",
            "prompt": _packet(),
            "max_turns": 999,
            "maxTurns": 777,
        },
        cwd=tmp_path,
        environ={},
    )
    assert updated["max_turns"] == 10
    assert "maxTurns" not in updated

    for invalid in (0, -1, True, "10"):
        with pytest.raises(RoutingConfigError, match="Agent.max_turns"):
            route_agent.route_tool_input(
                {"subagent_type": "foundry:scout", "prompt": _packet(), "max_turns": invalid},
                cwd=tmp_path,
                environ={},
            )

    with pytest.raises(RoutingConfigError, match="sections requises"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:scout", "prompt": "Goal:\nOnly one section"},
            cwd=tmp_path,
            environ={},
        )
    with pytest.raises(RoutingConfigError, match="trop long"):
        route_agent.route_tool_input(
            {"subagent_type": "foundry:scout", "prompt": _packet("x" * 4_100)},
            cwd=tmp_path,
            environ={},
        )


def test_unrelated_agents_are_untouched(tmp_path):
    assert route_agent.route_tool_input(
        {"subagent_type": "Explore", "prompt": "anything"},
        cwd=tmp_path,
        environ={},
    ) is None


def test_hook_main_emits_updated_input_and_fails_closed_for_a_gate(
    monkeypatch, capsys, tmp_path,
):
    payload = {
        "cwd": str(tmp_path),
        "tool_input": {"subagent_type": "foundry:scout", "prompt": _packet()},
    }
    monkeypatch.setattr(route_agent.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.delenv(CLAUDE_AVAILABLE_MODELS, raising=False)
    route_agent.main()
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"]["subagent_type"] == "foundry:routed-readonly-low"

    payload["tool_input"]["subagent_type"] = "foundry:reviewer"
    monkeypatch.setattr(route_agent.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setenv(CLAUDE_AVAILABLE_MODELS, "haiku-4.5")
    route_agent.main()
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "gate 'reviewer'" in output["permissionDecisionReason"]


def test_shared_hooks_fail_open_only_on_internal_errors(monkeypatch, capsys, tmp_path):
    payload = {
        "cwd": str(tmp_path),
        "tool_input": {"subagent_type": "foundry:scout", "prompt": _packet()},
    }
    monkeypatch.setattr(route_agent.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(
        route_agent, "route_tool_input", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("internal probe")
        ),
    )
    route_agent.main()
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(readonly_guard.sys, "stdin", io.StringIO('{"tool_input": []}'))
    readonly_guard.main()
    assert capsys.readouterr().out == ""


def test_claude_facade_parses_availability_and_strict_user_envelope():
    assert claude_available_models({
        CLAUDE_AVAILABLE_MODELS: "haiku, opus-5, claude-fable-5",
    }) == {
        "haiku-4.5", "opus-5", "fable-5",
    }
    assert claude_available_models({}) is None
    assert claude_available_models({
        f"CLAUDE_PLUGIN_OPTION_{CLAUDE_AVAILABLE_MODELS}": "sonnet-5,opus-5",
    }) == {"sonnet-5", "opus-5"}
    with pytest.raises(RoutingConfigError, match="liste CSV non vide"):
        claude_available_models({CLAUDE_AVAILABLE_MODELS: " , "})
    with pytest.raises(RoutingConfigError, match="clés inconnues"):
        claude_user_request('FOUNDRY_ROUTE_REQUEST={"unknown":1}\n' + _packet())
    assert claude_invocation_model("opus-5") == "opus"
    assert claude_invocation_model("opus") == "opus"
    assert claude_invocation_model("claude-opus-5") == "opus"
    assert claude_policy_model("opus") == "opus-5"
    assert claude_policy_model("claude-opus-5") == "opus-5"
    with pytest.raises(RoutingConfigError, match="alias Agent attendu"):
        claude_invocation_model("project-model")


def test_readonly_bash_guard_allows_only_the_claimed_diff_verifier():
    root = "/tmp/review root"
    base = "e" * 40
    valid = (
        f'python3 "{PLUGIN_ROOT}/tooling/foundry_cli.py" routing read-review '
        f'--diff-hash {"a" * 64} --claim-id {"c" * 64} '
        f'--root "{root}" --base {base}'
    )
    agent = "foundry:routed-readonly-high"

    assert readonly_guard.decision(agent, valid) is None
    literal = (
        'python3 "${CLAUDE_PLUGIN_ROOT}/tooling/foundry_cli.py" routing read-review '
        f'--diff-hash {"b" * 64} --claim-id {"d" * 64} '
        f'--root "{root}" --base {base}'
    )
    assert readonly_guard.decision(agent, literal) is None
    assert readonly_guard.decision(agent, "git diff") is not None
    assert readonly_guard.decision(agent, valid + " && git status") is not None
    assert readonly_guard.decision(agent, "python3 -c 'print(1)'") is not None
    assert readonly_guard.decision("foundry:routed-worker-high", "git status") is None


@pytest.mark.parametrize(
    "arguments",
    [
        f'--diff-hash {"a" * 64} --claim-id {"c" * 64}',
        f'--diff-hash short --claim-id {"c" * 64} --root /tmp/review --base {"e" * 40}',
        f'--diff-hash {"a" * 64} --claim-id short --root /tmp/review --base {"e" * 40}',
        f'--diff-hash {"a" * 64} --claim-id {"c" * 64} --root relative --base {"e" * 40}',
        f'--diff-hash {"a" * 64} --claim-id {"c" * 64} --root /tmp/review --base short',
        f'--diff-hash {"a" * 64} --claim-id {"c" * 64} --root /tmp/review --base {"E" * 40}',
        f'--diff-hash {"a" * 64} --claim-id {"c" * 64} --base {"e" * 40} --root /tmp/review',
        f'--root /tmp/review --base {"e" * 40} --diff-hash {"a" * 64} --claim-id {"c" * 64}',
    ],
)
def test_readonly_bash_guard_rejects_legacy_malformed_and_reordered_coordinates(arguments):
    command = (
        f'python3 "{PLUGIN_ROOT}/tooling/foundry_cli.py" routing read-review {arguments}'
    )

    assert readonly_guard.decision("foundry:routed-readonly-high", command) is not None


@pytest.mark.parametrize(
    "root",
    [
        "/tmp/$(touch /tmp/composed)",
        "/tmp/`touch /tmp/composed`",
        "/tmp/review>/tmp/composed",
        "/tmp/review*",
    ],
)
def test_readonly_bash_guard_rejects_shell_syntax_inside_root(root):
    command = (
        f'python3 "{PLUGIN_ROOT}/tooling/foundry_cli.py" routing read-review '
        f'--diff-hash {"a" * 64} --claim-id {"c" * 64} '
        f'--root "{root}" --base {"e" * 40}'
    )

    assert readonly_guard.decision("foundry:routed-readonly-high", command) is not None


def test_reviewer_contract_receives_the_resolved_guarded_cli_path(tmp_path):
    updated, _ = route_agent.route_tool_input(
        {"subagent_type": "foundry:reviewer", "prompt": _packet()}, cwd=tmp_path, environ={},
    )
    command = (
        f'python3 "{PLUGIN_ROOT}/tooling/foundry_cli.py" routing read-review '
        f'--diff-hash {"a" * 64} --claim-id {"b" * 64} '
        f'--root "{tmp_path}" --base {"c" * 40}'
    )
    assert (
        f'python3 "{PLUGIN_ROOT}/tooling/foundry_cli.py" routing read-review '
        "--diff-hash <DIFF-HASH> --claim-id <CLAIM-ID> --root <ROOT> --base <BASE-SHA>"
    ) in updated["prompt"]
    assert readonly_guard.decision(updated["subagent_type"], command) is None


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert match, path
    return match.group(1)


def test_agent_frontmatter_keeps_models_dynamic_and_effort_boundary_explicit():
    logical = {identity.lower() for identity in AGENT_IDENTITIES.values()}
    profiles = list((PLUGIN_ROOT / "agents").glob("routed-*.md"))
    assert len(profiles) == 10

    for path in (PLUGIN_ROOT / "agents").glob("*.md"):
        frontmatter = _frontmatter(path)
        assert not re.search(r"(?m)^model:", frontmatter), path
        if path.stem in logical:
            assert not re.search(r"(?m)^effort:", frontmatter), path
            assert re.search(r"(?m)^tools: Read$", frontmatter), path
        else:
            efforts = re.findall(r"(?m)^effort: (low|medium|high|xhigh|max)$", frontmatter)
            assert len(efforts) == 1, path
            tools = re.search(r"(?m)^tools: (.+)$", frontmatter).group(1).split(", ")
            assert "Agent" not in tools and "Task" not in tools, path
            if path.stem.startswith("routed-readonly-"):
                assert "Write" not in tools and "Edit" not in tools, path
                assert "Bash" in tools, path
            else:
                assert "Write" in tools and "Edit" in tools, path


def test_hooks_register_dynamic_routing_and_scoped_readonly_guard():
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    pre = hooks["hooks"]["PreToolUse"]
    by_matcher = {entry["matcher"]: entry["hooks"] for entry in pre}

    assert "route_agent.py" in by_matcher["Agent|Task"][0]["command"]
    bash_commands = [hook["command"] for hook in by_matcher["Bash"]]
    assert any("guard_routed_readonly.py" in command for command in bash_commands)
    assert any("guard_bash.py" in command for command in bash_commands)
    assert "observe_agent.py" in hooks["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "observe_agent.py" in hooks["hooks"]["PostToolUseFailure"][0]["hooks"][0]["command"]


def test_foundry_skills_invoke_each_claude_role_without_duplicate_inner_loop():
    skills = PLUGIN_ROOT / "skills"
    start = (skills / "start-issue" / "SKILL.md").read_text(encoding="utf-8")
    resume = (skills / "resume-issue" / "SKILL.md").read_text(encoding="utf-8")
    frame = (skills / "frame" / "SKILL.md").read_text(encoding="utf-8")
    merge = (skills / "merge-pr" / "SKILL.md").read_text(encoding="utf-8")
    review = (skills / "review-pr" / "SKILL.md").read_text(encoding="utf-8")

    assert "foundry:eiffel" in start and "exactly once" in start
    assert "foundry:eiffel" in resume and "do not duplicate" in resume
    assert "foundry:lupin" in frame
    assert "foundry:vauban" in frame and "apex escalation" in frame
    assert "foundry:maigret" in merge and "exactly once" in merge
    assert "full text of every cited" in merge
    for text in (start, resume, frame, merge):
        assert all(heading in text for heading in ("Goal:", "Inputs:", "Constraints:",
                                                   "Done when:"))
    for text in (start, resume, frame, merge, review):
        assert "telemetry complete" in text
        assert "telemetry outcome" in text
        assert "never enter" in text or "never part of `spawn` or the task" in text
        normalized = " ".join(text.split())
        for classification in (
            "--status completed --failure-class none",
            "--status failed --failure-class host",
            "--status failed --failure-class timeout",
            "--status cancelled --failure-class cancelled",
            "--status unknown --failure-class unknown",
        ):
            assert classification in normalized
        assert "current_context" in normalized and "no completion capability" in normalized
        assert "silent" in text
        assert "aggregate outcome" in normalized or "aggregate-outcome" in normalized
        assert "supplies the equivalent" not in text
