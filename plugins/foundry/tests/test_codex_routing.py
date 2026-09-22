import json
import multiprocessing
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from foundry.routing import (
    AcceptanceProofStore,
    ReviewDeduplicator,
    RoutingConfigError,
    RoutingUnavailableError,
    UserRouteRequest,
    acceptance_criteria,
    claimed_review_diff,
    main,
    review_diff_hash,
)
from foundry.escalation import EscalationStore, EscalationTechnicalBlockedError
from foundry.routing_facades import (
    AGENT_IDENTITIES,
    CODEX_AVAILABLE_MODELS,
    codex_available_models,
    codex_review_plan,
    codex_spawn_plan,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "1" * 40
CLAIM_ATTEMPT_TOKEN = "a" * 64
OTHER_CLAIM_ATTEMPT_TOKEN = "b" * 64


def _packet(body="Inspect the requested scope."):
    return (
        f"Goal:\n{body}\n"
        "Inputs:\nIssue, accepted ADRs, and relevant paths.\n"
        "Constraints:\nPreserve unrelated work; do not infer parent history.\n"
        "Done when:\nReturn evidence and validation."
    )


def _review_claim(tmp_path, diff=b"review me"):
    return {
        **ReviewDeduplicator("owner/repo", tmp_path / "state").claim(
            diff, coordinates={"root": str(tmp_path), "base": BASE_SHA},
        ).to_dict(),
        "root": str(tmp_path),
        "base": BASE_SHA,
    }


def _concurrent_f108_review_plan(args):
    root, state_dir, issue_id, diff = args
    import foundry.routing as routing

    routing.git_diff = lambda *_args, **_kwargs: diff
    plan = codex_review_plan(
        _packet(), root=root, base=BASE_SHA,
        state_dir=state_dir, issue_id=issue_id,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    return plan["mode"], plan["independent_context"], plan["review_claim"]


def _consumed_f108_route(tmp_path, route_role="reviewer"):
    state_dir = tmp_path / "state"
    store = EscalationStore.for_root(tmp_path, state_dir=state_dir)
    issue = "FOUNDRY-108"
    for tier in (
        "economy", "economy", "balanced", "balanced", "frontier", "frontier",
    ):
        store.record_failure(issue, route_role, "test_red", tier)
    generation = store.status(issue)["halt_generation"]
    store.resume_technical_remediation(issue, generation, "d" * 64)
    return store, state_dir, issue, generation


def _consumed_route_for_issue(tmp_path, issue, route_role="implementer"):
    state_dir = tmp_path / "state"
    store = EscalationStore.for_root(tmp_path, state_dir=state_dir)
    for tier in (
        "economy", "economy", "balanced", "balanced", "frontier", "frontier",
    ):
        store.record_failure(issue, route_role, "test_red", tier)
    generation = store.status(issue)["halt_generation"]
    store.resume_technical_remediation(issue, generation, "d" * 64)
    store.claim_technical_remediation_route(
        issue, route_role, generation, f"f165-{route_role}-route-0001",
    )
    return store, state_dir


def _blocked_review_proof(monkeypatch, tmp_path, state_dir, issue, diff):
    ledger = ReviewDeduplicator(str(tmp_path), state_dir)
    coordinates = {"root": str(tmp_path), "base": BASE_SHA}
    diff_hash = review_diff_hash(diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr("foundry.routing.git_head", lambda *_args, **_kwargs: "9" * 40)
    claim = ledger.claim_git(
        diff_hash,
        coordinates=coordinates,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    body = "- [ ] corrected diff must receive an independent fresh review\n"
    outcomes = [
        {**criterion, "verdict": "fail"}
        for criterion in acceptance_criteria(body)
    ]
    proof = AcceptanceProofStore(str(tmp_path), state_dir).create(
        issue_id=issue,
        issue_body=body,
        reviewer_role="reviewer",
        outcomes=outcomes,
        quality="blocked",
        diff_hash=diff_hash,
        claim_id=claim.claim_id,
        root=tmp_path,
        base=BASE_SHA,
        state_dir=state_dir,
    )
    return ledger, diff_hash, proof


def _pollute_technical_review_binding(store, issue, diff_hash, claimed_at):
    path = store._path(issue)
    state = json.loads(path.read_text(encoding="utf-8"))
    event = state["technical_remediation_audit"][0]
    event["review_diff_hash"] = diff_hash
    event["review_claimed_at"] = claimed_at
    path.write_text(json.dumps(state), encoding="utf-8")
    store.status(issue)


@pytest.mark.parametrize(
    ("role", "agent_type", "model", "effort"),
    [
        ("scout", "explorer", "gpt-5.6-luna", "low"),
        ("implementer", "worker", "gpt-5.6-terra", "medium"),
        ("reviewer", "reviewer", "gpt-5.6-sol", "high"),
        ("architect", "default", "gpt-5.6-sol", "max"),
    ],
)
def test_codex_roles_receive_the_resolved_explicit_spawn(
    tmp_path, role, agent_type, model, effort,
):
    claim = _review_claim(tmp_path) if role == "reviewer" else None

    plan = codex_spawn_plan(role, _packet(), root=tmp_path, review_claim=claim)

    assert plan["mode"] == "subagent"
    assert plan["independent_context"] is True
    assert plan["spawn"]["agent_type"] == agent_type
    assert plan["spawn"]["model"] == model
    assert plan["spawn"]["reasoning_effort"] == effort
    assert plan["spawn"]["fork_turns"] == "none"
    assert plan["agent_identity"] == AGENT_IDENTITIES[role]
    assert re.fullmatch(r"[0-9a-f]{16}", plan["execution_id"])
    assert plan["spawn"]["task_name"] == (
        f"foundry_{AGENT_IDENTITIES[role].lower()}_{plan['execution_id']}"
    )
    assert f"Agent identity: {AGENT_IDENTITIES[role]}" in plan["spawn"]["message"]
    assert "FOUNDRY_CODEX_ROUTED_AGENT_V1" in plan["spawn"]["message"]
    assert all(heading in plan["spawn"]["message"] for heading in (
        "Goal:", "Inputs:", "Constraints:", "Done when:",
    ))


def test_codex_user_request_wins_over_project_policy(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "roles": {"implementer": "frontier"},
        "mappings": {"codex": {"economy": {
            "model": "project-luna", "effort": "medium",
        }}},
    }), encoding="utf-8")

    plan = codex_spawn_plan(
        "implementer",
        _packet(),
        root=tmp_path,
        user=UserRouteRequest(tier="economy", model="user-luna", effort="low"),
    )

    assert plan["spawn"]["model"] == "user-luna"
    assert plan["spawn"]["reasoning_effort"] == "low"
    assert plan["route"]["sources"] == {
        "tier": "user", "model": "user", "effort": "user",
    }


def test_codex_availability_fallback_is_visible_and_gate_safe(tmp_path):
    ordinary = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path,
        available_models={"gpt-5.6-luna"},
    )
    assert ordinary["spawn"]["model"] == "gpt-5.6-luna"
    assert ordinary["route"]["selected_tier"] == "economy"
    assert ordinary["route"]["warnings"][0]["code"] == "MODEL_FALLBACK_DOWN"

    gate_root = tmp_path / "gate"
    gate_config = gate_root / ".foundry" / "model-routing.json"
    gate_config.parent.mkdir(parents=True)
    gate_config.write_text(json.dumps({
        "mappings": {"codex": {"apex": {"model": "apex-only"}}},
    }), encoding="utf-8")
    reviewer = codex_spawn_plan(
        "reviewer", _packet(), root=gate_root,
        available_models={"apex-only"}, review_claim=_review_claim(tmp_path, b"gate"),
    )
    assert reviewer["route"]["selected_tier"] == "apex"
    assert reviewer["spawn"]["model"] == "apex-only"
    assert reviewer["spawn"]["reasoning_effort"] == "max"
    assert reviewer["route"]["warnings"][0]["code"] == "GATE_MODEL_FALLBACK_UP"

    with pytest.raises(RoutingUnavailableError, match="gate 'reviewer'"):
        codex_spawn_plan(
            "reviewer", _packet(), root=tmp_path,
            available_models={"gpt-5.6-luna", "gpt-5.6-terra"},
            review_claim=_review_claim(tmp_path, b"unavailable gate"),
        )


def test_codex_profile_override_warning_is_value_free(tmp_path):
    private_model = "do-not-print-private-model"
    private_effort = "do-not-print-private-effort"

    plan = codex_spawn_plan(
        "scout",
        _packet(),
        root=tmp_path,
        effective_profile={
            "model": private_model,
            "model_reasoning_effort": private_effort,
        },
    )
    encoded = json.dumps(plan)

    assert "profile.model" in encoded
    assert "profile.model_reasoning_effort" in encoded
    assert "HOST_OVERRIDE_NEUTRALIZES_POLICY" in encoded
    assert private_model not in encoded
    assert private_effort not in encoded


def test_codex_review_claim_is_transmitted_and_duplicate_never_spawns(tmp_path):
    first = _review_claim(tmp_path, b"same exact diff")
    first_plan = codex_spawn_plan(
        "reviewer", _packet(), root=tmp_path, review_claim=first,
    )
    assert first == {
        "diff_hash": first["diff_hash"], "should_run": True,
        "state": "in_progress", "generation": 1, "claim_id": first["claim_id"],
        "root": str(tmp_path), "base": BASE_SHA,
    }
    assert first["diff_hash"] in first_plan["spawn"]["message"]
    assert first["claim_id"] in first_plan["spawn"]["message"]
    assert first_plan["review_claim"] == first

    second = _review_claim(tmp_path, b"same exact diff")
    second_plan = codex_spawn_plan(
        "reviewer", _packet(), root=tmp_path, review_claim=second,
    )
    assert second == {
        "diff_hash": first["diff_hash"], "should_run": False,
        "state": "in_progress", "generation": 1,
        "root": str(tmp_path), "base": BASE_SHA,
    }
    assert second_plan["mode"] == "deduplicated"
    assert second_plan["spawn"] is None
    assert "Review non relancée" in second_plan["disclosure"]


def test_codex_review_preflight_failure_does_not_consume_the_hash(monkeypatch, tmp_path):
    state = tmp_path / "state"
    diff = b"claim only after a valid plan"
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    with pytest.raises(RoutingConfigError, match="sections requises"):
        codex_review_plan(
            "Goal:\nIncomplete",
            root=tmp_path,
            base=BASE_SHA,
            state_dir=state,
            issue_id="FOUNDRY-42",
        )

    valid = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA, state_dir=state,
        issue_id="FOUNDRY-42", claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    assert valid["review_claim"]["should_run"] is True
    duplicate = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA, state_dir=state,
        issue_id="FOUNDRY-42",
    )
    assert duplicate["mode"] == "deduplicated"
    assert duplicate["spawn"] is None


def test_f111_legacy_review_waits_for_packet_preflight_before_migration(
    monkeypatch, tmp_path,
):
    state = tmp_path / "state"
    diff = b"legacy review awaiting a coordinated owner"
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    ledger = ReviewDeduplicator(str(tmp_path), state)
    legacy = ledger.claim(diff)
    marker = ledger.directory / legacy.diff_hash
    before = marker.read_bytes()

    with pytest.raises(RoutingConfigError, match="sections requises"):
        codex_review_plan(
            "Goal:\nIncomplete",
            root=tmp_path,
            base=BASE_SHA,
            state_dir=state,
            issue_id="FOUNDRY-111",
        )

    assert marker.read_bytes() == before
    assert not (ledger.directory / "legacy-quarantine").exists()

    owner = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA, state_dir=state,
        issue_id="FOUNDRY-111", claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    duplicate = codex_review_plan(
        "intentionally incomplete coordinated duplicate", root=tmp_path,
        base=BASE_SHA, state_dir=state, issue_id="FOUNDRY-111",
    )

    assert owner["review_claim"]["should_run"] is True
    assert owner["review_claim"]["claim_id"] != legacy.claim_id
    assert duplicate["mode"] == "deduplicated"
    assert duplicate["review_claim"]["should_run"] is False
    assert len(tuple((ledger.directory / "legacy-quarantine").iterdir())) == 1
    active = json.loads(marker.read_text(encoding="utf-8"))
    assert active["claim"]["id"] == owner["review_claim"]["claim_id"]
    assert active["coordinates"] == {"root": str(tmp_path), "base": BASE_SHA}


def test_f111_legacy_review_waits_for_resolved_route_before_migration(
    monkeypatch, tmp_path,
):
    state = tmp_path / "state"
    diff = b"legacy review awaiting a reviewer route"
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    ledger = ReviewDeduplicator(str(tmp_path), state)
    legacy = ledger.claim(diff)
    marker = ledger.directory / legacy.diff_hash
    before = marker.read_bytes()

    with pytest.raises(RoutingUnavailableError, match="gate 'reviewer'"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            available_models={"gpt-5.6-luna", "gpt-5.6-terra"},
            state_dir=state, issue_id="FOUNDRY-111",
        )

    assert marker.read_bytes() == before
    assert not (ledger.directory / "legacy-quarantine").exists()


def test_f111_interrupted_publication_replay_waits_for_full_codex_preflight(
    monkeypatch, tmp_path,
):
    state = tmp_path / "state"
    diff = b"interrupted Codex reviewer publication"
    diff_hash = review_diff_hash(diff)
    attempt_token = CLAIM_ATTEMPT_TOKEN
    coordinates = {"root": str(tmp_path), "base": BASE_SHA}
    ledger = ReviewDeduplicator(str(tmp_path), state)
    original_atomic_write = ReviewDeduplicator._atomic_write
    interrupted = False

    def interrupt_after_durable_publication(self, marker, record):
        nonlocal interrupted
        original_atomic_write(self, marker, record)
        if marker.name == diff_hash and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()

    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    monkeypatch.setattr(
        ReviewDeduplicator, "_atomic_write", interrupt_after_durable_publication,
    )
    with pytest.raises(KeyboardInterrupt):
        ledger.claim_git(
            diff_hash, coordinates=coordinates,
            claim_attempt_token=attempt_token,
        )
    monkeypatch.setattr(ReviewDeduplicator, "_atomic_write", original_atomic_write)

    marker = ledger.directory / diff_hash
    before = marker.read_bytes()
    published_claim_id = json.loads(before)["claim"]["id"]
    with pytest.raises(RoutingConfigError, match="sections requises"):
        codex_review_plan(
            "Goal:\nIncomplete", root=tmp_path, base=BASE_SHA,
            state_dir=state, issue_id="FOUNDRY-111",
            claim_attempt_token=attempt_token, replay_interrupted=True,
        )
    assert marker.read_bytes() == before

    plan = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA,
        state_dir=state, issue_id="FOUNDRY-111",
        claim_attempt_token=attempt_token, replay_interrupted=True,
    )
    assert plan["review_claim"]["should_run"] is True
    assert plan["review_claim"]["generation"] == 1
    assert plan["review_claim"]["claim_id"] == published_claim_id
    assert attempt_token not in json.dumps(plan)
    assert marker.read_bytes() == before

    repeated = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA,
        state_dir=state, issue_id="FOUNDRY-111",
        claim_attempt_token=attempt_token, replay_interrupted=True,
    )
    assert repeated["review_claim"] == plan["review_claim"]
    assert marker.read_bytes() == before


def test_f108_reviewer_claim_waits_for_a_consumed_local_route(monkeypatch, tmp_path):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path)
    diff = b"FOUNDRY-106 exact remediated diff"
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)
    diff_hash = review_diff_hash(diff)
    current_diff = {"value": diff}
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: current_diff["value"],
    )

    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
        )
    assert deduplicator.is_claimed(diff_hash) is False

    route_id = "f108-local-route-0001"
    claimed_route = store.claim_technical_remediation_route(
        issue, "reviewer", generation, route_id,
    )
    plan = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA,
        state_dir=state_dir, issue_id=issue,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )

    assert plan["mode"] == "subagent"
    assert plan["independent_context"] is True
    assert plan["role"] == "reviewer"
    assert plan["spawn"]["agent_type"] == "reviewer"
    assert plan["review_claim"]["diff_hash"] == diff_hash
    assert plan["review_claim"]["should_run"] is True
    assert plan["review_claim"]["claim_id"] in plan["spawn"]["message"]
    assert plan["escalation"]["minimum_tier"] == "frontier"
    assert claimed_route["provider_effect_allowed"] is False
    assert claimed_route["campaign_restart_allowed"] is False
    assert claimed_route["human_authorization_required"] is False
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] == diff_hash
    assert event["review_claimed_at"] is not None

    duplicate = codex_review_plan(
        "intentionally incomplete duplicate packet", root=tmp_path,
        base=BASE_SHA, state_dir=state_dir, issue_id=issue,
    )
    assert duplicate["mode"] == "deduplicated"
    current_diff["value"] = b"FOUNDRY-106 a distinct remediated diff"
    with pytest.raises(EscalationTechnicalBlockedError, match="déjà autorisé"):
        codex_review_plan(
            _packet(), root=tmp_path,
            base=BASE_SHA, state_dir=state_dir, issue_id=issue,
        )

    for role in ("implementer", "scout", "architect"):
        with pytest.raises(EscalationTechnicalBlockedError):
            codex_spawn_plan(
                role, _packet(), root=tmp_path, issue_id=issue,
                escalation_state_dir=state_dir,
            )


def test_f165_f70_sequence_keeps_deduplicated_blocked_review_out_of_fresh_slot(
    monkeypatch, tmp_path,
):
    state_dir = tmp_path / "state"
    issue = "FOUNDRY-165"
    old_diff = b"FOUNDRY-70 blocked review before local technical remediation"
    ledger, old_hash, old_proof = _blocked_review_proof(
        monkeypatch, tmp_path, state_dir, issue, old_diff,
    )
    store, _ = _consumed_route_for_issue(tmp_path, issue)
    before_authorization = store.status(issue)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: old_diff)

    duplicate = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA, state_dir=state_dir, issue_id=issue,
    )
    assert duplicate["mode"] == "deduplicated"
    assert duplicate["spawn"] is None
    assert duplicate["review_claim"] == {
        "diff_hash": old_hash,
        "should_run": False,
        "state": "completed",
        "generation": 1,
        "root": str(tmp_path),
        "base": BASE_SHA,
    }
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] is None
    assert event["review_claimed_at"] is None

    corrected_diff = b"FOUNDRY-70 corrected commit after local diagnostic"
    corrected_hash = review_diff_hash(corrected_diff)
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: corrected_diff,
    )
    fresh = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA, state_dir=state_dir, issue_id=issue,
        claim_attempt_token=OTHER_CLAIM_ATTEMPT_TOKEN,
    )
    assert fresh["mode"] == "subagent"
    assert fresh["review_claim"]["should_run"] is True
    assert fresh["review_claim"]["diff_hash"] == corrected_hash
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] == corrected_hash
    assert event["review_rearm_audit"] == []
    after_authorization = store.status(issue)
    for key in (
        "total_escalations", "halt_generation", "roles", "resume_count",
        "consumption_audit", "remediation_rearm_audit", "remediation_authorization",
        "terminal_outcome", "human_required", "technical_blocked",
    ):
        assert after_authorization[key] == before_authorization[key]
    terminal = ledger.validated_terminal_proof_binding(
        issue, old_hash, coordinates={"root": str(tmp_path), "base": BASE_SHA},
    )
    assert terminal["proof_id"] == old_proof["proof_id"]
    assert terminal["quality"] == "blocked"
    assert terminal["all_pass"] is False


def test_f165_polluted_blocked_review_is_reconciled_once_from_prior_terminal_evidence(
    monkeypatch, tmp_path,
):
    state_dir = tmp_path / "state"
    issue = "FOUNDRY-165"
    old_diff = b"historical blocked diff terminal before phantom technical binding"
    ledger, old_hash, old_proof = _blocked_review_proof(
        monkeypatch, tmp_path, state_dir, issue, old_diff,
    )
    terminal = ledger.validated_terminal_proof_binding(issue, old_hash)
    store, _ = _consumed_route_for_issue(tmp_path, issue)
    route_consumed = datetime.fromisoformat(
        store.status(issue)["technical_remediation_audit"][0]["route_consumed_at"]
        .replace("Z", "+00:00")
    )
    completed = datetime.fromisoformat(terminal["completed_at"].replace("Z", "+00:00"))
    polluted_at = max(route_consumed, completed) + timedelta(microseconds=1)
    _pollute_technical_review_binding(
        store, issue, old_hash, polluted_at.isoformat().replace("+00:00", "Z"),
    )

    corrected_diff = b"corrected diff requiring the only actual fresh technical review"
    corrected_hash = review_diff_hash(corrected_diff)
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: corrected_diff,
    )
    plan = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA, state_dir=state_dir, issue_id=issue,
        claim_attempt_token=OTHER_CLAIM_ATTEMPT_TOKEN,
    )
    assert plan["mode"] == "subagent"
    assert plan["review_claim"]["diff_hash"] == corrected_hash
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] == corrected_hash
    assert event["review_rearm_audit"] == [{
        "code": "technical_review_pollution_reconciled",
        "previous_diff_hash": old_hash,
        "previous_claimed_at": polluted_at.isoformat().replace("+00:00", "Z"),
        "terminal_proof_id": old_proof["proof_id"],
        "terminal_completed_at": terminal["completed_at"],
        "terminal_quality": "blocked",
        "repair_kind": "terminal_before_binding_reconciliation",
        "new_diff_hash": corrected_hash,
        "rearmed_at": event["review_claimed_at"],
    }]
    assert ledger.validated_terminal_proof_binding(issue, old_hash)["quality"] == "blocked"

    third_diff = b"a second replacement is never authorized"
    third_hash = review_diff_hash(third_diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: third_diff)
    with pytest.raises(EscalationTechnicalBlockedError, match="unique réarm"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            claim_attempt_token="c" * 64,
        )
    assert ledger.is_claimed(third_hash) is False


def test_f165_polluted_active_claim_is_refused_without_partial_new_claim(
    monkeypatch, tmp_path,
):
    issue = "FOUNDRY-165"
    store, state_dir = _consumed_route_for_issue(tmp_path, issue)
    ledger = ReviewDeduplicator(str(tmp_path), state_dir)
    old_diff = b"active review cannot prove historical terminal pollution"
    old_hash = review_diff_hash(old_diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: old_diff)
    ledger.claim_git(
        old_hash,
        coordinates={"root": str(tmp_path), "base": BASE_SHA},
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    route_consumed = datetime.fromisoformat(
        store.status(issue)["technical_remediation_audit"][0]["route_consumed_at"]
        .replace("Z", "+00:00")
    )
    _pollute_technical_review_binding(
        store,
        issue,
        old_hash,
        (route_consumed + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z"),
    )
    before = store._path(issue).read_bytes()
    new_diff = b"must not be claimed without terminal evidence"
    new_hash = review_diff_hash(new_diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: new_diff)

    with pytest.raises(EscalationTechnicalBlockedError, match="preuve AC terminale"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            claim_attempt_token=OTHER_CLAIM_ATTEMPT_TOKEN,
        )
    assert store._path(issue).read_bytes() == before
    assert ledger.is_claimed(new_hash) is False


def test_f165_terminal_hash_without_structured_proof_cannot_reconcile(
    monkeypatch, tmp_path,
):
    issue = "FOUNDRY-165"
    store, state_dir = _consumed_route_for_issue(tmp_path, issue)
    ledger = ReviewDeduplicator(str(tmp_path), state_dir)
    old_hash = review_diff_hash(b"legacy terminal marker without structured proof")
    ledger.directory.mkdir(parents=True, exist_ok=True)
    (ledger.directory / old_hash).write_text(
        json.dumps({"diff_hash": old_hash}), encoding="utf-8",
    )
    route_consumed = datetime.fromisoformat(
        store.status(issue)["technical_remediation_audit"][0]["route_consumed_at"]
        .replace("Z", "+00:00")
    )
    _pollute_technical_review_binding(
        store,
        issue,
        old_hash,
        (route_consumed + timedelta(microseconds=1)).isoformat().replace("+00:00", "Z"),
    )
    before = store._path(issue).read_bytes()
    new_diff = b"new diff cannot rely on proof-free legacy terminal marker"
    new_hash = review_diff_hash(new_diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: new_diff)

    with pytest.raises(EscalationTechnicalBlockedError, match="preuve AC terminale"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            claim_attempt_token=OTHER_CLAIM_ATTEMPT_TOKEN,
        )
    assert store._path(issue).read_bytes() == before
    assert ledger.is_claimed(new_hash) is False


@pytest.mark.parametrize("chronology", ["equal", "terminal_after_binding"])
def test_f165_ambiguous_or_late_blocked_terminal_proof_cannot_reconcile(
    monkeypatch, tmp_path, chronology,
):
    issue = "FOUNDRY-165"
    store, state_dir = _consumed_route_for_issue(tmp_path, issue)
    old_diff = f"blocked proof with {chronology}".encode()
    ledger, old_hash, _ = _blocked_review_proof(
        monkeypatch, tmp_path, state_dir, issue, old_diff,
    )
    terminal = ledger.validated_terminal_proof_binding(issue, old_hash)
    completed = datetime.fromisoformat(terminal["completed_at"].replace("Z", "+00:00"))
    claimed = completed if chronology == "equal" else completed - timedelta(microseconds=1)
    _pollute_technical_review_binding(
        store, issue, old_hash, claimed.isoformat().replace("+00:00", "Z"),
    )
    before = store._path(issue).read_bytes()
    new_diff = f"new diff after {chronology}".encode()
    new_hash = review_diff_hash(new_diff)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: new_diff)

    with pytest.raises(EscalationTechnicalBlockedError, match="preuve AC terminale"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            claim_attempt_token=OTHER_CLAIM_ATTEMPT_TOKEN,
        )
    assert store._path(issue).read_bytes() == before
    assert ledger.is_claimed(new_hash) is False


def test_f165_terminal_evidence_from_different_coordinates_is_refused(
    monkeypatch, tmp_path,
):
    state_dir = tmp_path / "state"
    issue = "FOUNDRY-165"
    old_diff = b"terminal evidence is tied to its original immutable coordinates"
    ledger, old_hash, _ = _blocked_review_proof(
        monkeypatch, tmp_path, state_dir, issue, old_diff,
    )

    with pytest.raises(RoutingConfigError, match="coordonnées de review différentes"):
        ledger.validated_terminal_proof_binding(
            issue,
            old_hash,
            coordinates={"root": str(tmp_path / "other"), "base": BASE_SHA},
        )


def test_f152_reviewer_diagnostic_is_claimless_and_local(tmp_path):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path)
    route_id = "f152-reviewer-diagnostic-0001"
    store.claim_technical_remediation_route(issue, "reviewer", generation, route_id)

    plan = codex_spawn_plan(
        "reviewer", _packet(), root=tmp_path, issue_id=issue,
        escalation_state_dir=state_dir, technical_remediation=True,
        technical_remediation_id=route_id,
    )

    assert plan["mode"] == "local_diagnostic"
    assert plan["spawn"] is None
    assert plan["review_claim"] is None
    assert plan["escalation"]["provider_effect_allowed"] is False
    assert plan["escalation"]["campaign_restart_allowed"] is False


@pytest.mark.parametrize("route_role", ["scout", "coordinator", "architect"])
def test_f108_codex_review_rejects_consumed_non_reviewer_route_before_claim(
    monkeypatch, tmp_path, route_role,
):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path, route_role)
    store.claim_technical_remediation_route(
        issue, route_role, generation, f"f108-{route_role}-route-0001",
    )
    diff = f"invalid {route_role} remediation route".encode()
    diff_hash = review_diff_hash(diff)
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)

    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
        )
    assert deduplicator.is_claimed(diff_hash) is False


def test_f108_reviewer_recovery_requires_the_exact_active_hash_and_claim(monkeypatch, tmp_path):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path)
    store.claim_technical_remediation_route(
        issue, "reviewer", generation, "f108-local-route-0002",
    )
    diff = b"FOUNDRY-106 recovery-bound diff"
    current_diff = {"value": diff}
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: current_diff["value"],
    )
    first = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA,
        state_dir=state_dir, issue_id=issue,
        claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)
    recovered = deduplicator.recover(
        first["review_claim"]["diff_hash"],
        first["review_claim"]["generation"],
        first["review_claim"]["claim_id"],
        "reviewer interrupted",
        coordinates={"root": str(tmp_path), "base": BASE_SHA},
    )

    with pytest.raises(RoutingConfigError, match="requis ensemble"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            recovered_diff_hash=recovered.diff_hash,
        )
    current_diff["value"] = b"derived diff"
    with pytest.raises(RoutingConfigError, match="diff a changé"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            recovered_diff_hash=recovered.diff_hash,
            recovered_claim_id=recovered.claim_id,
        )
    current_diff["value"] = diff
    with pytest.raises(RoutingConfigError, match="plus active"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            recovered_diff_hash=recovered.diff_hash,
            recovered_claim_id="f" * 64,
        )

    recovered_plan = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA,
        state_dir=state_dir, issue_id=issue,
        recovered_diff_hash=recovered.diff_hash,
        recovered_claim_id=recovered.claim_id,
    )
    assert recovered_plan["mode"] == "subagent"
    assert recovered_plan["independent_context"] is True
    assert recovered_plan["review_claim"] == {
        **recovered.to_dict(), "root": str(tmp_path), "base": BASE_SHA,
    }

    deduplicator.recover(
        recovered.diff_hash, recovered.generation, recovered.claim_id,
        "second reviewer interrupted", coordinates={"root": str(tmp_path), "base": BASE_SHA},
    )
    with pytest.raises(RoutingConfigError, match="plus active"):
        codex_review_plan(
            _packet(), root=tmp_path, base=BASE_SHA,
            state_dir=state_dir, issue_id=issue,
            recovered_diff_hash=recovered.diff_hash,
            recovered_claim_id=recovered.claim_id,
        )
    assert (deduplicator.directory / recovered.diff_hash).is_file()


def test_f108_concurrent_fresh_review_preflights_create_one_claim(tmp_path):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path)
    store.claim_technical_remediation_route(
        issue, "reviewer", generation, "f108-local-route-0003",
    )
    args = (str(tmp_path), str(state_dir), issue, b"concurrent FOUNDRY-106 diff")

    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_f108_review_plan, (args, args))

    assert sorted(mode for mode, _, _ in results) == ["deduplicated", "subagent"]
    owner = next(result for result in results if result[0] == "subagent")
    duplicate = next(result for result in results if result[0] == "deduplicated")
    assert owner[1] is True
    assert owner[2]["should_run"] is True
    assert duplicate[1] is False
    assert duplicate[2]["should_run"] is False
    assert owner[2]["diff_hash"] == duplicate[2]["diff_hash"]
    assert "claim_id" not in duplicate[2]
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] == owner[2]["diff_hash"]
    assert event["review_claimed_at"] is not None


def test_codex_duplicate_short_circuits_routing_and_packet_preflight(monkeypatch, tmp_path):
    state = tmp_path / "state"
    diff = b"already reviewed exact diff"
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: diff)
    first = codex_review_plan(
        _packet(), root=tmp_path, base=BASE_SHA, state_dir=state,
        issue_id="FOUNDRY-42", claim_attempt_token=CLAIM_ATTEMPT_TOKEN,
    )
    assert first["mode"] == "subagent"

    duplicate = codex_review_plan(
        "this packet is intentionally incomplete",
        root=tmp_path,
        base=BASE_SHA,
        available_models={"gpt-5.6-luna", "gpt-5.6-terra"},
        state_dir=state,
        issue_id="FOUNDRY-42",
    )
    assert duplicate["mode"] == "deduplicated"
    assert duplicate["spawn"] is None
    assert duplicate["review_claim"]["should_run"] is False


def test_codex_current_context_fallback_discloses_lost_independence(tmp_path):
    plan = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, subagent_available=False,
    )

    assert plan["mode"] == "current_context"
    assert plan["independent_context"] is False
    assert plan["spawn"] is None
    assert "contexte courant" in plan["disclosure"]
    assert "indépendance" in plan["disclosure"]
    assert "FOUNDRY_CODEX_ROUTED_AGENT_V1" in plan["current_context_instructions"]


def test_codex_packet_and_task_name_are_bounded(tmp_path):
    with pytest.raises(RoutingConfigError, match="sections requises"):
        codex_spawn_plan("scout", "Goal:\nOnly one section", root=tmp_path)
    with pytest.raises(RoutingConfigError, match="trop long"):
        codex_spawn_plan("scout", _packet("x" * 4_100), root=tmp_path)
    with pytest.raises(RoutingConfigError, match="task_name Codex invalide"):
        codex_spawn_plan("scout", _packet(), root=tmp_path, task_name="Bad-name")
    with pytest.raises(RoutingConfigError, match="task_name Codex trop long"):
        codex_spawn_plan("scout", _packet(), root=tmp_path, task_name="x" * 48)


def test_codex_successive_same_role_delegations_have_distinct_bounded_instances(tmp_path):
    first = codex_spawn_plan("implementer", _packet(), root=tmp_path)
    second = codex_spawn_plan("implementer", _packet(), root=tmp_path)

    assert first["agent_identity"] == second["agent_identity"] == "Eiffel"
    assert first["execution_id"] != second["execution_id"]
    assert first["spawn"]["task_name"] != second["spawn"]["task_name"]
    assert all(
        re.fullmatch(r"foundry_eiffel_[0-9a-f]{16}", plan["spawn"]["task_name"])
        and len(plan["spawn"]["task_name"]) <= 64
        for plan in (first, second)
    )


def test_codex_availability_environment_is_strict():
    assert codex_available_models({CODEX_AVAILABLE_MODELS: "gpt-5.6-luna, gpt-5.6-sol"}) == {
        "gpt-5.6-luna", "gpt-5.6-sol",
    }
    assert codex_available_models({}) is None
    with pytest.raises(RoutingConfigError, match="liste CSV non vide"):
        codex_available_models({CODEX_AVAILABLE_MODELS: " , "})


def test_codex_plan_cli_emits_executable_spawn_descriptor(tmp_path, capsys, monkeypatch):
    packet = tmp_path / "packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    monkeypatch.delenv(CODEX_AVAILABLE_MODELS, raising=False)

    main([
        "codex-plan", "implementer",
        "--packet-file", str(packet),
        "--root", str(tmp_path),
        "--profile-model-active",
    ])
    plan = json.loads(capsys.readouterr().out)

    assert plan["spawn"] == {
        "task_name": plan["spawn"]["task_name"],
        "agent_type": "worker",
        "fork_turns": "none",
        "message": plan["spawn"]["message"],
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
    }
    assert re.fullmatch(r"foundry_eiffel_[0-9a-f]{16}", plan["spawn"]["task_name"])
    assert plan["agent_identity"] == "Eiffel"
    assert "profile.model" in json.dumps(plan)

    with pytest.raises(SystemExit):
        main([
            "codex-plan", "coordinator",
            "--packet-file", str(packet),
            "--root", str(tmp_path),
        ])


@pytest.mark.parametrize("issue_args", [[], ["--issue", "invalid"]])
def test_codex_reviewer_cli_requires_a_valid_issue_before_reading_or_claiming(
    tmp_path, monkeypatch, issue_args,
):
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    exact_diff = b"must stay unclaimed"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff",
        lambda *_args, **_kwargs: reads.append(True) or exact_diff,
    )
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)

    with pytest.raises(RoutingConfigError, match="issue"):
        main(
            [
                "codex-plan", "reviewer", "--packet-file", str(packet),
                "--git-diff", "--root", str(tmp_path), "--base", BASE_SHA, *issue_args,
            ],
            handle_config_errors=False,
        )

    assert reads == []
    assert deduplicator.is_claimed(review_diff_hash(exact_diff)) is False


def test_codex_reviewer_cli_preflights_before_claim_then_allows_consumed_reviewer(
    tmp_path, capsys, monkeypatch,
):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path)
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: reads.append(True),
    )
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)
    exact_diff = b"review after consumed implementer route"
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff",
        lambda *_args, **_kwargs: reads.append(True) or exact_diff,
    )
    argv = [
        "codex-plan", "reviewer", "--issue", issue,
        "--packet-file", str(packet), "--git-diff",
        "--root", str(tmp_path), "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ]
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)

    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        main(argv, handle_config_errors=False)
    assert reads == []
    assert deduplicator.is_claimed(review_diff_hash(exact_diff)) is False

    store.claim_technical_remediation_route(
        issue, "reviewer", generation, "f108-cli-reviewer-route-0001",
    )
    main(argv)
    plan = json.loads(capsys.readouterr().out)

    assert reads == [True, True]
    assert plan["mode"] == "subagent"
    assert plan["review_claim"]["should_run"] is True
    assert plan["review_claim"]["diff_hash"] == review_diff_hash(exact_diff)
    assert deduplicator.is_claimed(review_diff_hash(exact_diff)) is True


def test_f152_reviewer_technical_remediation_cli_dispatches_local_only(
    tmp_path, capsys, monkeypatch,
):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path)
    route_id = "f152-cli-reviewer-diagnostic-0001"
    store.claim_technical_remediation_route(issue, "reviewer", generation, route_id)
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: reads.append(True),
    )
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)

    main([
        "codex-plan", "reviewer", "--issue", issue,
        "--packet-file", str(packet), "--root", str(tmp_path),
        "--technical-remediation", "--technical-remediation-id", route_id,
    ])
    plan = json.loads(capsys.readouterr().out)

    assert plan["mode"] == "local_diagnostic"
    assert plan["spawn"] is None
    assert plan["review_claim"] is None
    assert reads == []
    assert deduplicator.is_claimed(review_diff_hash(b"F152 local diagnostic")) is False
    assert plan["escalation"]["technical_remediation_claimed"] is True
    assert plan["escalation"]["provider_effect_allowed"] is False
    assert plan["escalation"]["campaign_restart_allowed"] is False

    with pytest.raises(RoutingConfigError, match="--issue est requis"):
        main([
            "codex-plan", "reviewer", "--packet-file", str(packet),
            "--root", str(tmp_path), "--technical-remediation",
            "--technical-remediation-id", route_id,
        ], handle_config_errors=False)


def test_f152_technical_remediation_facade_requires_an_audited_issue(tmp_path):
    with pytest.raises(RoutingConfigError, match="issue auditée requise"):
        codex_spawn_plan(
            "reviewer", _packet(), root=tmp_path,
            technical_remediation=True,
            technical_remediation_id="f152-direct-facade-route-0001",
        )


@pytest.mark.parametrize(
    "forbidden_args",
    [
        ["--git-diff"],
        ["--diff-hash", "a" * 64],
        ["--claim-id", "f152-claim-id"],
        ["--replay-interrupted"],
    ],
)
def test_f152_reviewer_technical_remediation_cli_refuses_review_claim_inputs(
    tmp_path, monkeypatch, forbidden_args,
):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path)
    route_id = "f152-cli-reviewer-refusal-0001"
    store.claim_technical_remediation_route(issue, "reviewer", generation, route_id)
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: reads.append(True),
    )

    with pytest.raises(RoutingConfigError, match="aucune claim ou lecture Git"):
        main(
            [
                "codex-plan", "reviewer", "--issue", issue,
                "--packet-file", str(packet), "--root", str(tmp_path),
                "--technical-remediation", "--technical-remediation-id", route_id,
                *forbidden_args,
            ],
            handle_config_errors=False,
        )

    assert reads == []


@pytest.mark.parametrize("route_role", ["scout", "coordinator", "architect"])
def test_codex_reviewer_cli_rejects_consumed_non_reviewer_before_diff_or_claim(
    tmp_path, monkeypatch, route_role,
):
    store, state_dir, issue, generation = _consumed_f108_route(tmp_path, route_role)
    store.claim_technical_remediation_route(
        issue, route_role, generation, f"f108-cli-{route_role}-route-0001",
    )
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    exact_diff = f"invalid {route_role} CLI review".encode()
    reads = []
    monkeypatch.setattr(
        "foundry.routing.git_diff",
        lambda *_args, **_kwargs: reads.append(True) or exact_diff,
    )
    deduplicator = ReviewDeduplicator(str(tmp_path), state_dir)

    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        main(
            [
                "codex-plan", "reviewer", "--issue", issue,
                "--packet-file", str(packet), "--git-diff",
                "--root", str(tmp_path), "--base", BASE_SHA,
            ],
            handle_config_errors=False,
        )

    assert reads == []
    assert deduplicator.is_claimed(review_diff_hash(exact_diff)) is False


def test_codex_reviewer_cli_claims_before_emitting_one_spawn(tmp_path, capsys, monkeypatch):
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    exact_diff = b"exact cli review diff"
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: exact_diff)

    argv = [
        "codex-plan", "reviewer", "--issue", "FOUNDRY-42",
        "--packet-file", str(packet),
        "--git-diff",
        "--root", str(tmp_path),
        "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ]
    main(argv)
    first = json.loads(capsys.readouterr().out)
    assert first["mode"] == "subagent"
    assert first["review_claim"]["should_run"] is True
    assert first["review_claim"]["diff_hash"] in first["spawn"]["message"]
    assert first["review_claim"]["claim_id"] in first["spawn"]["message"]
    assert str(tmp_path) in first["spawn"]["message"]
    assert BASE_SHA in first["spawn"]["message"]
    assert claimed_review_diff(
        first["review_claim"]["diff_hash"],
        str(tmp_path),
        claim_id=first["review_claim"]["claim_id"],
        root=tmp_path,
        base=BASE_SHA,
        consume=bytes,
    ) == exact_diff

    main(argv)
    second = json.loads(capsys.readouterr().out)
    assert second["mode"] == "deduplicated"
    assert second["spawn"] is None


def test_codex_reviewer_cli_spawns_the_explicit_recovered_owner(
    tmp_path, capsys, monkeypatch,
):
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    current_diff = {"value": b"exact recovered review diff"}
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: current_diff["value"],
    )
    base_argv = [
        "codex-plan", "reviewer", "--issue", "FOUNDRY-42",
        "--packet-file", str(packet),
        "--git-diff",
        "--root", str(tmp_path),
        "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ]

    main(base_argv)
    interrupted = json.loads(capsys.readouterr().out)
    main([
        "recover-review",
        "--diff-hash", interrupted["review_claim"]["diff_hash"],
        "--generation", str(interrupted["review_claim"]["generation"]),
        "--claim-id", interrupted["review_claim"]["claim_id"],
        "--reason", "reviewer interrupted",
        "--root", str(tmp_path), "--base", BASE_SHA,
    ])
    recovered = json.loads(capsys.readouterr().out)

    main(base_argv)
    unowned = json.loads(capsys.readouterr().out)
    assert unowned["mode"] == "deduplicated"
    assert unowned["spawn"] is None
    assert recovered["claim_id"] not in json.dumps(unowned)

    recovered_argv = [
        *base_argv,
        "--diff-hash", recovered["diff_hash"],
        "--claim-id", recovered["claim_id"],
    ]
    main(recovered_argv)
    plan = json.loads(capsys.readouterr().out)

    assert plan["mode"] == "subagent"
    assert plan["review_claim"] == {
        **recovered, "root": str(tmp_path), "base": BASE_SHA,
    }
    assert plan["spawn"]["agent_type"] == "reviewer"
    assert plan["spawn"]["model"] == "gpt-5.6-sol"
    assert plan["spawn"]["reasoning_effort"] == "high"
    assert recovered["claim_id"] in plan["spawn"]["message"]
    assert claimed_review_diff(
        recovered["diff_hash"], str(tmp_path),
        claim_id=recovered["claim_id"], root=tmp_path, base=BASE_SHA, consume=bytes,
    ) == current_diff["value"]

    stale_owner = recovered["claim_id"]
    ledger = ReviewDeduplicator(str(tmp_path), tmp_path / "state")
    current = ledger.recover(
        recovered["diff_hash"], recovered["generation"], recovered["claim_id"],
        "second interruption",
        coordinates={"root": str(tmp_path), "base": BASE_SHA},
    )
    with pytest.raises(SystemExit, match="plus active"):
        main(recovered_argv)

    stale_argv = [
        *base_argv,
        "--diff-hash", current.diff_hash,
        "--claim-id", current.claim_id,
    ]
    ledger.recover(
        current.diff_hash, current.generation, current.claim_id,
        "third reviewer interrupted", coordinates={"root": str(tmp_path), "base": BASE_SHA},
    )
    with pytest.raises(SystemExit, match="plus active"):
        main(stale_argv)

    assert stale_owner not in capsys.readouterr().out


def test_codex_recovered_reviewer_refuses_a_changed_git_diff(
    tmp_path, capsys, monkeypatch,
):
    packet = tmp_path / "review-packet.txt"
    packet.write_text(_packet(), encoding="utf-8")
    current_diff = {"value": b"claimed review diff"}
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setattr(
        "foundry.routing.git_diff", lambda *_args, **_kwargs: current_diff["value"],
    )
    argv = [
        "codex-plan", "reviewer", "--issue", "FOUNDRY-42",
        "--packet-file", str(packet), "--git-diff",
        "--root", str(tmp_path), "--base", BASE_SHA,
        "--claim-attempt-token", CLAIM_ATTEMPT_TOKEN,
    ]
    main(argv)
    interrupted = json.loads(capsys.readouterr().out)
    main([
        "recover-review", "--diff-hash", interrupted["review_claim"]["diff_hash"],
        "--generation", "1", "--claim-id", interrupted["review_claim"]["claim_id"],
        "--reason", "reviewer interrupted", "--root", str(tmp_path),
        "--base", BASE_SHA,
    ])
    recovered = json.loads(capsys.readouterr().out)

    current_diff["value"] = b"changed after recovery"
    with pytest.raises(SystemExit, match="diff a changé"):
        main([
            *argv, "--diff-hash", recovered["diff_hash"],
            "--claim-id", recovered["claim_id"],
        ])


def test_foundry_skills_execute_codex_plans_and_disclose_fallback():
    skills = PLUGIN_ROOT / "skills"
    texts = {
        name: (skills / name / "SKILL.md").read_text(encoding="utf-8")
        for name in ("start-issue", "resume-issue", "frame", "merge-pr", "review-pr")
    }

    assert "routing codex-plan implementer" in texts["start-issue"]
    assert "routing codex-plan implementer" in texts["resume-issue"]
    assert "routing codex-plan scout" in texts["frame"]
    assert "codex-plan architect" in texts["frame"]
    assert "routing codex-plan reviewer" in texts["merge-pr"]
    assert "routing codex-plan reviewer" in texts["review-pr"]
    assert "--git-diff" in texts["merge-pr"] and "mode=deduplicated" in texts["merge-pr"]
    assert texts["merge-pr"].count(
        "routing codex-plan reviewer --issue <ISSUE-ID>"
    ) == 2
    assert texts["review-pr"].count(
        "routing codex-plan reviewer --issue <ISSUE-ID>"
    ) == 2
    routing_doc = (PLUGIN_ROOT / "docs" / "model-routing.md").read_text(
        encoding="utf-8",
    )
    assert routing_doc.count(
        "routing codex-plan reviewer --issue FOUNDRY-42"
    ) == 2
    assert "recover-review --diff-hash <hash> --generation <n> --claim-id <active-claim-id>" in routing_doc
    for name in ("merge-pr", "review-pr"):
        assert "routing recover-review" in texts[name]
        assert "--claim-id <ACTIVE-CLAIM-ID>" in texts[name]
        assert "--root <ROOT> --base <BASE-SHA>" in texts[name]
        assert "--claim-attempt-token <CLAIM-ATTEMPT-TOKEN>" in texts[name]
        assert "--replay-interrupted" in texts[name]
        assert "secrets.token_hex(32)" in texts[name]
        assert "routing record-review-proof" in texts[name]
        assert "routing complete-review" not in texts[name]
        assert "--claim-id" in texts[name]
    assert "--claim-attempt-token <claim-attempt-token>" in routing_doc
    assert "--replay-interrupted" in routing_doc
    for text in texts.values():
        assert "--no-subagent" in text
        assert "indépend" in text or "independ" in text
        assert "--profile-model-active" in text
        assert "--profile-effort-active" in text
    for name in ("start-issue", "resume-issue", "merge-pr"):
        assert "model" in texts[name]
        assert "reasoning_effort" in texts[name]
        assert "fork_turns=none" in texts[name]
