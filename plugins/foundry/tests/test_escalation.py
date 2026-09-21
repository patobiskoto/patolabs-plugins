import fcntl
import json
import multiprocessing
import os
import queue
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from foundry.escalation import (
    BoundedDiagnostic,
    MAX_ESCALATIONS_PER_ISSUE,
    MAX_PARALLEL_AGENTS,
    RESUME_REASON_CODES,
    EscalationAuthorityAmbiguousError,
    EscalationTechnicalBlockedError,
    EscalationStore,
)
from foundry.routing import (
    RoutingConfigError,
    RoutingPolicy,
    RoutingUnavailableError,
    UserRouteRequest,
    main,
)
from foundry.routing_facades import codex_spawn_plan


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _packet():
    return (
        "Goal:\nImplement the bounded issue scope.\n"
        "Inputs:\nIssue, accepted ADRs, and relevant paths.\n"
        "Constraints:\nPreserve unrelated work; no external writes.\n"
        "Done when:\nTests are green and evidence is returned."
    )


def _concurrent_failure(args):
    repository, state_dir = args
    return EscalationStore(repository, state_dir=state_dir).record_failure(
        "FOUNDRY-42", "implementer", "test_red", "balanced",
    ).to_dict()


def _concurrent_technical_resume(args):
    root, state_dir, issue_id, halt_generation, evidence_digest = args
    return EscalationStore.for_root(root, state_dir=state_dir).resume_technical_remediation(
        issue_id, halt_generation, evidence_digest,
    )


def _concurrent_technical_claim(args):
    root, state_dir, issue_id, halt_generation, route_id = args
    return EscalationStore.for_root(
        root, state_dir=state_dir,
    ).claim_technical_remediation_route(
        issue_id, "implementer", halt_generation, route_id,
    )


def _concurrent_reviewer_authorization(args):
    root, state_dir, issue_id, diff_hash, rearm_proof_id = args
    try:
        validated_rearm = None
        if rearm_proof_id is not None:
            def validated_rearm(_previous):
                return diff_hash, rearm_proof_id
        result = EscalationStore.for_root(
            root, state_dir=state_dir,
        ).claim_fresh_reviewer_authorization(
            issue_id,
            diff_hash,
            validated_claim=lambda: diff_hash,
            validated_rearm=validated_rearm,
        )
        return "authorized", result
    except EscalationTechnicalBlockedError as exc:
        return "blocked", str(exc)


def _controlled_first_creator(
    repository, state_dir, state_created, sidecar_contended, results,
):
    """Pause after exposing the former unsafe empty-state interleaving."""
    store = EscalationStore(repository, state_dir=state_dir)
    state_path = os.fspath(store._path("FOUNDRY-42"))
    original_open = os.open

    def controlled_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            descriptor = original_open(path, flags, mode)
        else:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if (
            os.fspath(path) == state_path
            and flags & os.O_CREAT
            and flags & os.O_EXCL
        ):
            state_created.set()
            if not sidecar_contended.wait(10):
                os.close(descriptor)
                raise RuntimeError("contender did not reach the sidecar lock")
        return descriptor

    os.open = controlled_open
    try:
        decision = store.record_failure(
            "FOUNDRY-42", "implementer", "test_red", "balanced",
        )
        results.put(("creator", decision.to_dict()))
    except BaseException as exc:
        results.put(("creator", {"error": f"{type(exc).__name__}: {exc}"}))
    finally:
        os.open = original_open


def _controlled_first_contender(
    repository, state_dir, state_created, sidecar_contended, results,
):
    """Prove the second mutation blocks on the stable inode, then let it wait."""
    if not state_created.wait(10):
        results.put(("contender", {"error": "creator did not expose state"}))
        return
    store = EscalationStore(repository, state_dir=state_dir)
    lock_path = os.fspath(store._lock_path("FOUNDRY-42"))
    lock_descriptors = set()
    original_open = os.open
    original_flock = fcntl.flock

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            descriptor = original_open(path, flags, mode)
        else:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if os.fspath(path) == lock_path:
            lock_descriptors.add(descriptor)
        return descriptor

    def controlled_flock(descriptor, operation):
        if descriptor in lock_descriptors and operation == fcntl.LOCK_EX:
            try:
                original_flock(descriptor, operation | fcntl.LOCK_NB)
            except BlockingIOError:
                sidecar_contended.set()
                return original_flock(descriptor, operation)
            original_flock(descriptor, fcntl.LOCK_UN)
            raise RuntimeError("creator did not retain the sidecar lock")
        return original_flock(descriptor, operation)

    os.open = tracked_open
    fcntl.flock = controlled_flock
    try:
        decision = store.record_failure(
            "FOUNDRY-42", "implementer", "test_red", "balanced",
        )
        results.put(("contender", decision.to_dict()))
    except BaseException as exc:
        results.put(("contender", {"error": f"{type(exc).__name__}: {exc}"}))
    finally:
        fcntl.flock = original_flock
        os.open = original_open


def _concurrent_resume_or_failure(args):
    repository, state_dir, action = args
    store = EscalationStore(repository, state_dir=state_dir)
    if action == "resume":
        return store.resume("FOUNDRY-17", "manual_retry_approved", 1)
    return store.record_failure(
        "FOUNDRY-17", "scout", "test_red", "frontier",
    ).to_dict()


def _concurrent_remediation_failure(args):
    repository, state_dir = args
    return EscalationStore(repository, state_dir=state_dir).record_failure(
        "FOUNDRY-58", "implementer", "review_blocking_after_fix", "frontier",
    ).to_dict()


def _concurrent_remediation_rearm(args):
    repository, state_dir = args
    try:
        return EscalationStore(repository, state_dir=state_dir).rearm_remediation(
            "FOUNDRY-90", "implementer", "manual_retry_approved", 1, 2,
        )
    except RoutingConfigError:
        return {"action": "rejected"}


def _human_stop(store, issue, role):
    """Build a valid typed human stop without relying on a technical ceiling."""
    store.record_failure(issue, role, "test_red", "economy")
    decision = store.record_human_verdict(
        issue, role, "economy", category="strategy_decision",
    )
    assert decision.action == "human_required"
    return decision


def _exhaust_remediation_window(store, issue, credits=1):
    store.record_risk(issue, "implementer", "adr_creation", "apex")
    halted = _human_stop(store, issue, "implementer")
    assert halted.action == "human_required"
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, credits)
    for _ in range(credits):
        continued = store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "apex",
        )
        assert continued.action == "remediation_continued"
    return generation


def _generation_two_remediation_window(store, issue, credits=1):
    """Open a human remediation window after one bounded technical resume."""
    stopped = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
    )
    assert stopped.action == "technical_blocked"
    assert store.status(issue)["halt_generation"] == 1
    store.resume_technical_remediation(issue, 1, "a" * 64)
    verdict = store.record_human_verdict(
        issue, "implementer", "apex", category="strategy_decision",
    )
    assert verdict.action == "human_required"
    generation = store.status(issue)["halt_generation"]
    assert generation == 2
    store.resume(issue, "remediation_reviewed", generation, credits)
    return generation


def test_only_deterministic_failures_count_and_two_escalate(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)

    with pytest.raises(RoutingConfigError, match="échec non déterministe"):
        store.record_failure("FOUNDRY-42", "implementer", "agent_felt_stuck", "balanced")
    assert store.status("FOUNDRY-42")["roles"] == {}

    first = store.record_failure("FOUNDRY-42", "implementer", "test_red", "balanced")
    second = store.record_failure(
        "FOUNDRY-42", "implementer", "review_blocking", "balanced",
    )

    assert first.action == "failure_recorded"
    assert first.deterministic_failures == 1
    assert first.failures_since_escalation == 1
    assert (second.action, second.initial_tier, second.final_tier) == (
        "escalated", "balanced", "frontier",
    )
    assert second.issue_escalations == 1
    assert second.role_escalations == 1
    assert store.active_floor("FOUNDRY-42", "implementer") == "frontier"


def test_explicit_risks_apply_frontier_or_apex_and_review_reblock_is_immediate(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)

    security = store.record_risk(
        "FOUNDRY-7", "implementer", "security", "balanced",
    )
    assert (security.initial_tier, security.final_tier, security.signal) == (
        "balanced", "frontier", "security",
    )

    adr = store.record_risk("FOUNDRY-8", "architect", "adr_creation", "apex")
    assert adr.action == "satisfied"
    assert adr.final_tier == "apex"
    assert adr.issue_escalations == 0
    assert store.active_floor("FOUNDRY-8", "architect") == "apex"

    already_frontier = store.record_risk(
        "FOUNDRY-13", "implementer", "security", "frontier",
    )
    assert already_frontier.action == "satisfied"
    assert already_frontier.issue_escalations == 0
    assert store.active_floor("FOUNDRY-13", "implementer") == "frontier"

    reblocked = store.record_failure(
        "FOUNDRY-9", "implementer", "review_blocking_after_fix", "balanced",
    )
    assert reblocked.action == "escalated"
    assert reblocked.final_tier == "frontier"
    assert "à nouveau bloquée" in reblocked.reason

    requested = store.record_explicit_request(
        "FOUNDRY-12", "scout", "economy",
    )
    assert requested.signal == "explicit_user_request"
    assert requested.final_tier == "balanced"


def test_two_escalation_ceiling_records_a_technical_block(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)
    issue = "FOUNDRY-99"

    for tier in ("economy", "economy", "balanced", "balanced", "frontier"):
        decision = store.record_failure(issue, "scout", "test_red", tier)
        assert decision.human_required is False
    stopped = store.record_failure(issue, "scout", "test_red", "frontier")

    assert stopped.action == "technical_blocked"
    assert stopped.human_required is False
    assert stopped.issue_escalations == MAX_ESCALATIONS_PER_ISSUE == 2
    assert stopped.initial_tier == stopped.final_tier == "frontier"
    assert "Plafond technique de 2" in stopped.reason
    with pytest.raises(EscalationTechnicalBlockedError, match="diagnostic local borné"):
        store.active_floor(issue, "scout")


def test_escalation_is_role_persistent_but_issue_scoped(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)
    store.record_failure("FOUNDRY-10", "implementer", "test_red", "balanced")
    store.record_failure("FOUNDRY-10", "implementer", "test_red", "balanced")

    assert store.active_floor("FOUNDRY-10", "implementer") == "frontier"
    assert store.active_floor("FOUNDRY-10", "scout") is None
    assert store.active_floor("FOUNDRY-11", "implementer") is None
    assert store.status("FOUNDRY-11")["total_escalations"] == 0


def test_concurrent_failure_updates_are_serialized(tmp_path):
    args = [("owner/concurrent", str(tmp_path))] * 2
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        decisions = pool.map(_concurrent_failure, args)

    assert {decision["action"] for decision in decisions} == {
        "failure_recorded", "escalated",
    }
    status = EscalationStore("owner/concurrent", state_dir=tmp_path).status("FOUNDRY-42")
    assert status["total_escalations"] == 1
    assert status["roles"]["implementer"]["deterministic_failures"] == 2


def test_first_creation_serializes_before_empty_state_is_observable(tmp_path):
    context = multiprocessing.get_context("spawn")
    state_created = context.Event()
    sidecar_contended = context.Event()
    results = context.Queue()
    repository = "owner/deterministic-first-create"
    args = (
        repository, str(tmp_path), state_created, sidecar_contended, results,
    )
    creator = context.Process(target=_controlled_first_creator, args=args)
    contender = context.Process(target=_controlled_first_contender, args=args)
    creator.start()
    contender.start()
    try:
        creator.join(15)
        contender.join(15)
    finally:
        for process in (creator, contender):
            if process.is_alive():
                process.terminate()
                process.join(5)

    assert state_created.is_set()
    assert sidecar_contended.is_set()
    assert creator.exitcode == contender.exitcode == 0
    try:
        outcomes = dict(results.get(timeout=5) for _ in range(2))
    except queue.Empty:
        pytest.fail("first-creation workers did not return both outcomes")
    assert "error" not in outcomes["creator"]
    assert "error" not in outcomes["contender"]
    assert outcomes["creator"]["action"] == "failure_recorded"
    assert outcomes["contender"]["action"] == "escalated"

    store = EscalationStore(repository, state_dir=tmp_path)
    status_payload = store.status("FOUNDRY-42")
    assert status_payload["total_escalations"] == 1
    assert status_payload["roles"]["implementer"]["deterministic_failures"] == 2
    assert stat.S_IMODE(store._path("FOUNDRY-42").stat().st_mode) == 0o600
    lock_path = store._lock_path("FOUNDRY-42")
    assert lock_path.is_file()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_preexisting_empty_state_remains_corrupt_under_creation_lock(tmp_path):
    store = EscalationStore("owner/stale-empty", state_dir=tmp_path)
    issue = "FOUNDRY-42"
    state_path = store._path(issue)
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(b"")

    with pytest.raises(RoutingConfigError) as rejected:
        store.record_failure(issue, "implementer", "test_red", "balanced")

    assert str(rejected.value) == f"état d'escalade corrompu pour {issue}."
    assert str(tmp_path) not in str(rejected.value)
    assert state_path.read_bytes() == b""
    lock_path = store._lock_path(issue)
    assert lock_path.is_file()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW unavailable")
def test_sidecar_symlink_is_rejected_without_touching_target(tmp_path):
    store = EscalationStore("owner/sidecar-symlink", state_dir=tmp_path)
    issue = "FOUNDRY-42"
    target = tmp_path / "sidecar-target"
    sentinel = b"SIDE-CAR-SENTINEL"
    target.write_bytes(sentinel)
    lock_path = store._lock_path(issue)
    lock_path.parent.mkdir(parents=True)
    lock_path.symlink_to(target)

    with pytest.raises(RoutingConfigError) as rejected:
        store.record_failure(issue, "implementer", "test_red", "balanced")

    assert str(tmp_path) not in str(rejected.value)
    assert sentinel.decode() not in str(rejected.value)
    assert target.read_bytes() == sentinel
    assert lock_path.is_symlink()
    assert not store._path(issue).exists()


def test_active_floor_cannot_be_demoted_or_bypassed_by_availability(tmp_path):
    policy = RoutingPolicy.load(tmp_path)
    with pytest.raises(RoutingConfigError, match="plancher actif 'frontier'"):
        policy.resolve(
            "implementer", "codex",
            user=UserRouteRequest(tier="economy"),
            minimum_tier="frontier", minimum_source="escalation:FOUNDRY-42",
        )
    with pytest.raises(RoutingConfigError, match="modèle direct"):
        policy.resolve(
            "implementer", "codex",
            user=UserRouteRequest(model="gpt-5.6-luna"),
            minimum_tier="frontier", minimum_source="escalation:FOUNDRY-42",
        )
    with pytest.raises(RoutingUnavailableError, match="plancher actif"):
        policy.resolve(
            "implementer", "codex",
            available_models={"gpt-5.6-luna", "gpt-5.6-terra"},
            minimum_tier="frontier", minimum_source="escalation:FOUNDRY-42",
        )

    route = policy.resolve(
        "implementer", "codex", available_models={"gpt-5.6-sol"},
        minimum_tier="frontier", minimum_source="escalation:FOUNDRY-42",
    )
    assert route.selected_tier == "frontier"
    assert route.minimum_tier == "frontier"
    assert route.sources["tier"] == "escalation:FOUNDRY-42"


def test_codex_plan_applies_persistent_floor_without_changing_capability(tmp_path):
    state_dir = tmp_path / "state"
    store = EscalationStore(str(tmp_path), state_dir=state_dir)
    store.record_failure("FOUNDRY-42", "implementer", "test_red", "balanced")
    store.record_failure("FOUNDRY-42", "implementer", "test_red", "balanced")

    plan = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id="FOUNDRY-42",
        escalation_state_dir=state_dir,
    )

    assert plan["spawn"]["agent_type"] == "worker"
    assert plan["spawn"]["model"] == "gpt-5.6-sol"
    assert plan["spawn"]["reasoning_effort"] == "high"
    assert plan["route"]["selected_tier"] == "frontier"
    assert plan["escalation"] == {
        "issue_id": "FOUNDRY-42",
        "minimum_tier": "frontier",
        "max_parallel_agents": MAX_PARALLEL_AGENTS,
        "remediation_authorization": {
            "state": "none", "role": None, "halt_generation": None,
            "maximum_credits": 0, "remaining_credits": 0,
        },
        "remediation_rearm_audit": [],
        "technical_remediation_open": False,
        "technical_remediation_requested": False,
    }
    assert MAX_PARALLEL_AGENTS == 4


def test_technical_halt_blocks_new_codex_delegation_without_human_verdict(tmp_path):
    state_dir = tmp_path / "state"
    store = EscalationStore(str(tmp_path), state_dir=state_dir)
    issue = "FOUNDRY-77"
    for tier in ("economy", "economy", "balanced", "balanced", "frontier", "frontier"):
        store.record_failure(issue, "scout", "test_red", tier)

    with pytest.raises(EscalationTechnicalBlockedError, match="diagnostic local borné"):
        codex_spawn_plan(
            "scout", _packet(), root=tmp_path, issue_id=issue,
            escalation_state_dir=state_dir,
        )


def test_escalation_cli_reports_a_technical_stop_without_human_exit(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    common = ["FOUNDRY-55", "scout", "--kind", "test_red", "--root", str(tmp_path)]
    for index, tier in enumerate(
        ("economy", "economy", "balanced", "balanced", "frontier"),
    ):
        main([
            "escalation", "failure", *common, "--current-tier", tier,
            "--idempotency-key", f"f107-cli-stop-{index}",
        ])
        payload = json.loads(capsys.readouterr().out)
        assert payload["human_required"] is False

    main([
        "escalation", "failure", *common, "--current-tier", "frontier",
        "--idempotency-key", "f107-cli-stop-5",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "technical_blocked"
    assert payload["human_required"] is False
    assert payload["issue_escalations"] == 2


def test_failure_cli_replays_one_exact_idempotency_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    command = [
        "escalation", "failure", "FOUNDRY-56", "implementer",
        "--kind", "test_red", "--current-tier", "economy",
        "--idempotency-key", "f107-cli-replay-01", "--root", str(tmp_path),
    ]

    main(command)
    first = json.loads(capsys.readouterr().out)
    main(command)
    replay = json.loads(capsys.readouterr().out)

    assert replay == first
    store = EscalationStore.for_root(tmp_path)
    status = store.status("FOUNDRY-56")
    assert status["roles"]["implementer"]["deterministic_failures"] == 1
    raw = json.loads(store._path("FOUNDRY-56").read_text(encoding="utf-8"))
    assert len(raw["failure_receipts"]) == 1


def _halt_after_budget(store, issue="FOUNDRY-17"):
    _human_stop(store, issue, "scout")


@pytest.mark.parametrize("reason", [None, "", "YOUTRACK_TOKEN=perm:secret", "Bearer secret", "an arbitrary sentence"])
def test_resume_rejects_missing_or_uncontrolled_audit_reason_without_persisting_it(tmp_path, reason):
    store = EscalationStore("owner/repo", state_dir=tmp_path)
    _halt_after_budget(store)
    before = store.status("FOUNDRY-17")

    with pytest.raises(RoutingConfigError, match="code de reprise") as exc:
        store.resume("FOUNDRY-17", reason, 1)
    assert "secret" not in str(exc.value)
    assert store.status("FOUNDRY-17") == before


def test_resume_requires_current_positive_halt_generation_and_a_halted_issue(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)
    with pytest.raises(RoutingConfigError, match="génération d'arrêt"):
        store.resume("FOUNDRY-17", "manual_retry_approved", 0)
    with pytest.raises(RoutingConfigError, match="n'est pas arrêtée"):
        store.resume("FOUNDRY-17", "manual_retry_approved", 1)


def test_resume_audits_without_changing_budget_or_role_state_and_persists(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)
    _halt_after_budget(store)
    before = store.status("FOUNDRY-17")
    role_snapshot = json.loads(json.dumps(before["roles"], sort_keys=True))

    resumed = store.resume("FOUNDRY-17", "remediation_reviewed", before["halt_generation"])

    assert resumed["action"] == "resumed"
    assert resumed["issue_id"] == "FOUNDRY-17"
    assert resumed["resume_count"] == 1
    assert resumed["last_resume_reason"] == "remediation_reviewed"
    assert resumed["last_resumed_halt_generation"] == 1
    assert resumed["last_resumed_at"].endswith("Z")
    after = store.status("FOUNDRY-17")
    assert after["halted"] is False
    assert after["halted_reason"] is None
    assert after["total_escalations"] == before["total_escalations"] == 0
    assert after["roles"] == role_snapshot

    reopened = EscalationStore("owner/repo", state_dir=tmp_path).status("FOUNDRY-17")
    assert reopened["resume_count"] == 1
    assert reopened["last_resumed_at"] == resumed["last_resumed_at"]


def test_old_v1_state_has_resume_defaults_and_resume_keeps_claude_codex_floor(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    store = EscalationStore(str(tmp_path), state_dir=state_dir)
    path = store._path("FOUNDRY-17")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Genuine released v1 envelope: no public/computed fields and none of the
    # remediation-window fields introduced by FOUNDRY-37.
    path.write_text(json.dumps({
        "version": 1,
        "issue_id": "FOUNDRY-17",
        "total_escalations": 1,
        "halted": False,
        "halted_reason": None,
        "roles": {
            "implementer": {
                "minimum_tier": "frontier",
                "escalations": 1,
                "deterministic_failures": 2,
                "failures_since_escalation": 0,
            },
        },
    }), encoding="utf-8")
    assert store.status("FOUNDRY-17")["resume_count"] == 0

    # Create an actual halt without changing the persistent implementer floor.
    _human_stop(store, "FOUNDRY-17", "scout")
    store.resume("FOUNDRY-17", "remediation_reviewed", 1)
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    plan = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id="FOUNDRY-17",
        escalation_state_dir=state_dir,
    )
    assert plan["escalation"]["minimum_tier"] == "frontier"


def test_resume_does_not_replenish_exhausted_budget_and_next_signal_rehalts(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)
    _halt_after_budget(store)
    store.resume("FOUNDRY-17", "manual_retry_approved", 1)

    first = store.record_failure("FOUNDRY-17", "scout", "test_red", "frontier")
    second = store.record_failure("FOUNDRY-17", "scout", "test_red", "frontier")
    stopped = store.record_failure("FOUNDRY-17", "scout", "test_red", "apex")

    assert first.action == "escalated"
    assert second.action == "failure_recorded"
    assert stopped.action == "technical_blocked"
    assert stopped.human_required is False
    assert stopped.issue_escalations == 1
    assert store.status("FOUNDRY-17")["resume_count"] == 1


def test_stale_resume_cannot_clear_a_newer_halt_generation(tmp_path):
    store = EscalationStore("owner/repo", state_dir=tmp_path)
    _halt_after_budget(store)
    first = store.status("FOUNDRY-17")["halt_generation"]
    store.resume("FOUNDRY-17", "manual_retry_approved", first)
    store.record_failure("FOUNDRY-17", "scout", "test_red", "frontier")
    store.record_failure("FOUNDRY-17", "scout", "test_red", "frontier")
    store.record_failure("FOUNDRY-17", "scout", "test_red", "apex")
    store.record_failure("FOUNDRY-17", "scout", "test_red", "apex")
    newer = store.status("FOUNDRY-17")

    assert newer["halted"] is True
    assert newer["halt_generation"] == first + 1
    with pytest.raises(RoutingConfigError, match="sans verdict humain valide"):
        store.resume("FOUNDRY-17", "risk_accepted", first)
    final = store.status("FOUNDRY-17")
    assert final["halted"] is True
    assert final["halt_generation"] == newer["halt_generation"]


def test_concurrent_resume_and_signal_are_serialized_without_losing_the_signal(tmp_path):
    store = EscalationStore("owner/resume-race", state_dir=tmp_path)
    _halt_after_budget(store)
    args = [
        ("owner/resume-race", str(tmp_path), "resume"),
        ("owner/resume-race", str(tmp_path), "failure"),
    ]
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_resume_or_failure, args)

    assert {result["action"] for result in results} in (
        {"resumed", "escalated"}, {"resumed", "human_required"},
    )
    status = EscalationStore("owner/resume-race", state_dir=tmp_path).status("FOUNDRY-17")
    assert status["resume_count"] == 1
    assert status["total_escalations"] in {0, 1}
    # If the signal acquired the lock after resume it re-halts; otherwise it was
    # observed while halted. Both are valid serial orders and retain the signal.
    assert status["roles"].get("scout", {}).get("deterministic_failures", 0) in {1, 2}


def test_escalation_cli_show_and_resume_are_deterministic(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    _halt_after_budget(store)

    generation = store.status("FOUNDRY-17")["halt_generation"]
    main(["escalation", "resume", "FOUNDRY-17", "--reason", "manual_retry_approved", "--halt-generation", str(generation), "--root", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "resumed"
    assert payload["resume_count"] == 1
    assert payload["total_escalations"] == 0

    main(["escalation", "show", "FOUNDRY-17", "--root", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["last_resume_reason"] == "manual_retry_approved"


def test_escalation_cli_arms_and_shows_bounded_remediation_credits(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    _halt_after_budget(store)
    generation = store.status("FOUNDRY-17")["halt_generation"]
    main([
        "escalation", "resume", "FOUNDRY-17", "--reason", "remediation_reviewed",
        "--halt-generation", str(generation), "--remediation-credits", "1", "--root", str(tmp_path),
    ])
    assert json.loads(capsys.readouterr().out)["remediation_authorization"]["remaining_credits"] == 1
    main(["escalation", "show", "FOUNDRY-17", "--root", str(tmp_path)])
    shown = json.loads(capsys.readouterr().out)["remediation_authorization"]
    assert shown == {
        "state": "active", "role": "scout", "halt_generation": generation,
        "maximum_credits": 1, "remaining_credits": 1,
    }


def test_remediation_arm_rejects_nonhashable_halted_role_redacted_in_api_and_cli(
    tmp_path, monkeypatch,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    _human_stop(store, "FOUNDRY-75", "implementer")
    generation = store.status("FOUNDRY-75")["halt_generation"]
    path = store._path("FOUNDRY-75")
    payload = json.loads(path.read_text(encoding="utf-8"))
    sentinel = "HOSTILE_HALTED_ROLE_SECRET_SENTINEL"
    payload["halted_role"] = [sentinel]
    path.write_text(json.dumps(payload), encoding="utf-8")

    before = path.read_bytes()
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide") as rejected:
        store.resume("FOUNDRY-75", "remediation_reviewed", generation, 1)
    assert sentinel not in str(rejected.value)
    assert path.read_bytes() == before

    cli = subprocess.run(
        [
            sys.executable, str(PLUGIN_ROOT / "tooling" / "foundry_cli.py"),
            "routing", "escalation", "resume", "FOUNDRY-75",
            "--reason", "remediation_reviewed",
            "--halt-generation", str(generation),
            "--remediation-credits", "1", "--root", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "FOUNDRY_DATA": str(state_dir)},
    )
    assert cli.returncode == 2
    assert cli.stdout == ""
    assert cli.stderr == "Configuration de routage invalide.\n"
    assert "Traceback" not in cli.stderr
    assert sentinel not in cli.stdout + cli.stderr


def test_escalation_cli_rejects_sensitive_resume_reason_without_disclosing_it(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    monkeypatch.setenv(
        "PYTHONPATH", str(Path(__file__).resolve().parents[1] / "tooling"),
    )
    sensitive_reason = "YOUTRACK_TOKEN=perm:secret"

    rejected = subprocess.run(
        [
            sys.executable, "-m", "foundry.routing",
            "escalation", "resume", "FOUNDRY-17",
            "--reason", sensitive_reason,
            "--halt-generation", "1",
            "--root", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert rejected.returncode != 0
    assert sensitive_reason not in rejected.stdout
    assert sensitive_reason not in rejected.stderr
    assert "code de reprise invalide" in rejected.stderr


def test_foundry_cli_routing_config_error_is_concise_and_redacts_sensitive_value(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    sensitive_reason = "FOUNDRY_23_SECRET_SENTINEL=perm:secret"

    rejected = subprocess.run(
        [
            sys.executable, str(PLUGIN_ROOT / "tooling" / "foundry_cli.py"),
            "routing", "escalation", "resume", "FOUNDRY-17",
            "--reason", sensitive_reason,
            "--halt-generation", "1", "--root", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert rejected.stderr == "Configuration de routage invalide.\n"
    assert "Traceback" not in rejected.stderr
    assert sensitive_reason not in rejected.stderr
    assert sensitive_reason not in rejected.stdout


def test_foundry_cli_routing_unavailable_error_is_concise():
    launcher = PLUGIN_ROOT / "tooling" / "foundry_cli.py"
    script = f'''\
import runpy
import sys
sys.path.insert(0, {str(PLUGIN_ROOT / "tooling")!r})
import foundry.routing
def fail(*_args, **_kwargs):
    raise foundry.routing.RoutingUnavailableError("FOUNDRY_23_UNAVAILABLE_SENTINEL")
foundry.routing.main = fail
sys.argv = [{str(launcher)!r}, "routing", "show"]
runpy.run_path({str(launcher)!r}, run_name="__main__")
'''

    unavailable = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False,
    )

    assert unavailable.returncode == 2
    assert unavailable.stdout == ""
    assert unavailable.stderr == "Configuration de routage invalide.\n"
    assert "Traceback" not in unavailable.stderr
    assert "FOUNDRY_23_UNAVAILABLE_SENTINEL" not in unavailable.stderr


def test_foundry_cli_routing_keeps_unexpected_exception_traceback():
    launcher = PLUGIN_ROOT / "tooling" / "foundry_cli.py"
    injected_failure = "FOUNDRY_23_UNEXPECTED_SENTINEL"
    script = f'''\
import runpy
import sys
sys.path.insert(0, {str(PLUGIN_ROOT / "tooling")!r})
import foundry.routing
def fail(*_args, **_kwargs):
    raise RuntimeError({injected_failure!r})
foundry.routing.main = fail
sys.argv = [{str(launcher)!r}, "routing", "show"]
runpy.run_path({str(launcher)!r}, run_name="__main__")
'''

    failed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False,
    )

    assert failed.returncode != 0
    assert "Traceback" in failed.stderr
    assert injected_failure in failed.stderr


def test_escalation_resume_help_lists_only_public_reason_codes(capsys):
    with pytest.raises(SystemExit) as shown:
        main(["escalation", "resume", "--help"])

    captured = capsys.readouterr()
    assert shown.value.code == 0
    assert captured.err == ""
    assert all(code in captured.out for code in RESUME_REASON_CODES)


def test_resume_reason_codes_are_public_and_small():
    assert RESUME_REASON_CODES == (
        "remediation_reviewed", "risk_accepted", "manual_retry_approved",
    )


def test_execution_skills_propagate_issue_and_deterministic_signals():
    skills = Path(__file__).resolve().parents[1] / "skills"
    start = (skills / "start-issue" / "SKILL.md").read_text(encoding="utf-8")
    resume = (skills / "resume-issue" / "SKILL.md").read_text(encoding="utf-8")
    merge = (skills / "merge-pr" / "SKILL.md").read_text(encoding="utf-8")

    for text in (start, resume, merge):
        assert 'FOUNDRY_ROUTE_REQUEST={"issue":"<ISSUE-ID>"}' in text
        assert "--issue <ISSUE-ID>" in text
        assert "human_required" in text
    assert "routing escalation failure" in start
    assert "test_red" in start
    assert "agent confidence" in start
    assert "review_blocking_after_fix" in merge
    assert "At most four" in start
    assert "technical_blocked" in start
    assert "technical_blocked" in merge
    assert "resume-technical" in resume
    assert "three valid human categories" in resume


def test_remediation_window_is_role_bound_consumed_and_fails_closed(tmp_path):
    store = EscalationStore("owner/remediation", state_dir=tmp_path)
    # Construct a role-attributable, explicitly typed human stop.
    _human_stop(store, "FOUNDRY-55", "implementer")
    halted = store.status("FOUNDRY-55")
    assert halted["halted"] is True
    assert halted["halted_role"] == "implementer"

    resumed = store.resume(
        "FOUNDRY-55", "remediation_reviewed", halted["halt_generation"], 2,
    )
    authorization = resumed["remediation_authorization"]
    assert authorization == {
        "state": "active", "role": "implementer",
        "halt_generation": halted["halt_generation"],
        "maximum_credits": 2, "remaining_credits": 2,
    }
    before_budget = store.status("FOUNDRY-55")["total_escalations"]
    first = store.record_failure(
        "FOUNDRY-55", "implementer", "review_blocking_after_fix", "frontier",
    )
    assert first.action == "remediation_continued"
    assert store.status("FOUNDRY-55")["remediation_authorization"]["remaining_credits"] == 1
    assert store.status("FOUNDRY-55")["total_escalations"] == before_budget

    blocked = store.record_failure("FOUNDRY-55", "reviewer", "review_blocking_after_fix", "frontier")
    assert blocked.action == "technical_blocked"
    assert store.status("FOUNDRY-55")["halted"] is True


def test_remediation_window_old_state_cancel_and_malformed_fail_closed(tmp_path):
    store = EscalationStore("owner/remediation-cas", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-56", "implementer")
    generation = store.status("FOUNDRY-56")["halt_generation"]
    store.resume("FOUNDRY-56", "manual_retry_approved", generation, 1)
    cancelled = store.cancel_remediation("FOUNDRY-56", generation)
    assert cancelled["action"] == "technical_blocked"
    with pytest.raises(RoutingConfigError, match="obsolète"):
        store.cancel_remediation("FOUNDRY-56", generation)

    old = EscalationStore("owner/old", state_dir=tmp_path)
    assert old.status("FOUNDRY-57")["remediation_authorization"]["state"] == "none"


def test_remediation_credit_consumption_is_atomic_under_the_shared_lock(tmp_path):
    store = EscalationStore("owner/remediation-race", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-58", "implementer")
    generation = store.status("FOUNDRY-58")["halt_generation"]
    store.resume("FOUNDRY-58", "remediation_reviewed", generation, 1)
    with multiprocessing.Pool(2) as pool:
        results = pool.map(_concurrent_remediation_failure, [
            ("owner/remediation-race", str(tmp_path)),
            ("owner/remediation-race", str(tmp_path)),
        ])
    assert {result["action"] for result in results} == {
        "remediation_continued", "technical_blocked",
    }
    authorization = store.status("FOUNDRY-58")["remediation_authorization"]
    assert authorization["remaining_credits"] == 0
    assert authorization["state"] == "exhausted"


def test_remediation_arm_bounds_stale_cas_and_simple_resume_clear_old_window(tmp_path):
    store = EscalationStore("owner/remediation-sequence", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-59", "implementer")
    generation = store.status("FOUNDRY-59")["halt_generation"]
    for credits in (0, 4):
        with pytest.raises(RoutingConfigError, match="1 à 3"):
            store.resume("FOUNDRY-59", "remediation_reviewed", generation, credits)
    with pytest.raises(RoutingConfigError, match="obsolète"):
        store.resume("FOUNDRY-59", "remediation_reviewed", generation + 1, 1)
    store.resume("FOUNDRY-59", "remediation_reviewed", generation, 3)
    # A different role is a terminal, incompatible stop; the next ordinary resume
    # must not let the prior generation's credits reappear.
    assert store.record_failure("FOUNDRY-59", "reviewer", "review_blocking_after_fix", "frontier").action == "technical_blocked"
    newer_generation = store.status("FOUNDRY-59")["halt_generation"]
    assert newer_generation == generation + 1
    with pytest.raises(RoutingConfigError, match="sans verdict humain valide"):
        store.resume("FOUNDRY-59", "manual_retry_approved", newer_generation)
    assert store.status("FOUNDRY-59")["remediation_authorization"]["state"] == "invalidated"


def test_remediation_malformed_audit_is_redacted_and_fail_closed(tmp_path):
    store = EscalationStore("owner/remediation-redaction", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-60", "implementer")
    generation = store.status("FOUNDRY-60")["halt_generation"]
    store.resume("FOUNDRY-60", "remediation_reviewed", generation, 1)
    path = store._path("FOUNDRY-60")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["remediation_authorization"]["free_form_secret"] = "REMEDIATION_SECRET_SENTINEL"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(RoutingConfigError) as shown:
        store.status("FOUNDRY-60")
    assert "REMEDIATION_SECRET_SENTINEL" not in str(shown.value)
    with pytest.raises(RoutingConfigError):
        store.active_floor("FOUNDRY-60", "implementer")
    assert path.read_bytes() == before


def test_hostile_active_remediation_values_halt_redacted_in_api_and_cli(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    _human_stop(store, "FOUNDRY-62", "implementer")
    generation = store.status("FOUNDRY-62")["halt_generation"]
    store.resume("FOUNDRY-62", "remediation_reviewed", generation, 1)
    path = store._path("FOUNDRY-62")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["remediation_authorization"]["remaining_credits"] = "HOSTILE_SECRET_SENTINEL"
    payload["remediation_authorization"]["forfeited_credits"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")

    before = path.read_bytes()
    with pytest.raises(RoutingConfigError) as rejected_api:
        store.record_failure(
            "FOUNDRY-62", "implementer", "review_blocking_after_fix", "frontier",
        )
    assert "HOSTILE_SECRET_SENTINEL" not in str(rejected_api.value)
    assert path.read_bytes() == before

    rejected = subprocess.run(
        [
            sys.executable, str(PLUGIN_ROOT / "tooling" / "foundry_cli.py"),
            "routing", "escalation", "failure", "FOUNDRY-62", "implementer",
            "--kind", "review_blocking_after_fix", "--current-tier", "frontier",
            "--idempotency-key", "f107-hostile-failure-62",
            "--root", str(tmp_path),
        ], capture_output=True, text=True, check=False,
        env={**os.environ, "FOUNDRY_DATA": str(state_dir)},
    )
    assert rejected.returncode == 2
    assert "Traceback" not in rejected.stderr
    assert "HOSTILE_SECRET_SENTINEL" not in rejected.stdout + rejected.stderr


def test_same_role_noneligible_signal_invalidates_without_consuming_credit(tmp_path):
    store = EscalationStore("owner/remediation-same-role", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-63", "implementer")
    generation = store.status("FOUNDRY-63")["halt_generation"]
    store.resume("FOUNDRY-63", "remediation_reviewed", generation, 2)

    stopped = store.record_failure("FOUNDRY-63", "implementer", "test_red", "frontier")

    authorization = store.status("FOUNDRY-63")["remediation_authorization"]
    assert stopped.action == "technical_blocked"
    assert stopped.human_required is False
    assert authorization["state"] == "invalidated"
    raw = json.loads(store._path("FOUNDRY-63").read_text(encoding="utf-8"))
    assert raw["remediation_authorization"]["consumed_credits"] == 0
    assert raw["remediation_authorization"]["forfeited_credits"] == 2
    assert raw["remediation_authorization"]["consumption_audit"] == []


@pytest.mark.parametrize("credits", [2, 3])
def test_every_remediation_credit_has_a_bounded_immutable_audit_entry(tmp_path, credits):
    store = EscalationStore(f"owner/remediation-history-{credits}", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-64", "implementer")
    generation = store.status("FOUNDRY-64")["halt_generation"]
    store.resume("FOUNDRY-64", "remediation_reviewed", generation, credits)

    for remaining in range(credits - 1, -1, -1):
        decision = store.record_failure(
            "FOUNDRY-64", "implementer", "review_blocking_after_fix", "frontier",
        )
        assert decision.action == "remediation_continued"
        assert decision.remediation_authorization == {
            "state": "active" if remaining else "exhausted",
            "role": "implementer", "halt_generation": generation,
            "maximum_credits": credits, "remaining_credits": remaining,
        }

    raw = json.loads(store._path("FOUNDRY-64").read_text(encoding="utf-8"))
    audit = raw["remediation_authorization"]["consumption_audit"]
    assert len(audit) == credits <= 3
    assert all(
        set(entry) == {"code", "at", "role", "halt_generation"}
        for entry in audit
    )
    assert all(entry["code"] == "review_blocking_after_fix_consumed" for entry in audit)
    assert all(entry["at"].endswith("Z") for entry in audit)
    assert all(entry["role"] == "implementer" for entry in audit)
    assert all(entry["halt_generation"] == generation for entry in audit)
    assert raw["consumption_audit"] == audit
    assert raw["remediation_authorization"]["consumed_credits"] == credits


def test_issue_consumption_audit_is_append_only_across_two_windows_and_resume(tmp_path):
    store = EscalationStore("owner/remediation-durable-history", state_dir=tmp_path)
    issue = "FOUNDRY-78"
    _human_stop(store, issue, "implementer")
    first_generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", first_generation, 2)
    for _ in range(2):
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "frontier",
        )
    first_window = store.status(issue)["consumption_audit"]
    assert len(first_window) == 2

    # A controlled rearm of the exact exhausted window replaces the window object
    # without turning an exhausted technical condition into a human verdict.
    store.rearm_remediation(
        issue, "implementer", "manual_retry_approved", first_generation, 1,
    )
    second_generation = first_generation
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "frontier",
    )
    after_second_window = store.status(issue)["consumption_audit"]
    assert after_second_window[:2] == first_window
    assert [event["halt_generation"] for event in after_second_window] == [
        first_generation, first_generation, second_generation,
    ]

    final_decision = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "frontier",
    )
    final = store.status(issue)
    assert final_decision.action == "technical_blocked"
    assert final["remediation_authorization"]["state"] == "exhausted"
    assert final["consumption_audit"] == after_second_window
    assert all(
        set(event) == {"code", "at", "role", "halt_generation"}
        for event in final["consumption_audit"]
    )


@pytest.mark.parametrize("terminal", ["cancelled", "invalidated"])
def test_durable_consumption_survives_window_cancellation_or_invalidation(
    tmp_path, terminal,
):
    store = EscalationStore(f"owner/remediation-durable-{terminal}", state_dir=tmp_path)
    issue = "FOUNDRY-79"
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 2)
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "frontier",
    )
    event = store.status(issue)["consumption_audit"][0]

    if terminal == "cancelled":
        store.cancel_remediation(issue, generation)
    else:
        store.record_failure(issue, "implementer", "test_red", "frontier")
    halted = store.status(issue)
    assert halted["remediation_authorization"]["state"] == terminal
    assert halted["consumption_audit"] == [event]

    assert halted["technical_blocked"] is True
    assert store.status(issue)["consumption_audit"] == [event]


def test_invalidated_rearmed_window_allows_one_bounded_technical_resume(
    tmp_path,
):
    """A local diagnostic may follow invalidation without reviving its window."""
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-148"
    _human_stop(store, issue, "implementer")
    window_generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", window_generation, 1)
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
    )
    store.rearm_remediation(
        issue, "implementer", "manual_retry_approved", window_generation, 1,
    )
    stopped = store.record_failure(issue, "implementer", "test_red", "apex")
    assert stopped.action == "technical_blocked"
    halted = store.status(issue)
    authorization_before = json.loads(
        store._path(issue).read_text(encoding="utf-8")
    )["remediation_authorization"]

    resumed = store.resume_technical_remediation(
        issue, halted["halt_generation"], "a" * 64,
    )

    assert resumed["human_required"] is False
    assert resumed["provider_effect_allowed"] is False
    assert resumed["campaign_restart_allowed"] is False
    current = store.status(issue)
    assert current["halted"] is False
    assert current["human_required"] is False
    assert current["technical_blocked"] is False
    assert current["remediation_authorization"] == {
        "state": "invalidated", "role": "implementer",
        "halt_generation": window_generation,
        "maximum_credits": 1, "remaining_credits": 0,
    }
    raw = json.loads(store._path(issue).read_text(encoding="utf-8"))
    assert raw["remediation_authorization"] == authorization_before
    assert len(raw["technical_remediation_audit"]) == 1
    assert raw["technical_remediation_audit"][0]["halt_generation"] == (
        halted["halt_generation"]
    )
    route_id = "f148-local-route-0001"
    claimed = store.claim_technical_remediation_route(
        issue, "implementer", halted["halt_generation"], route_id,
    )
    assert claimed["provider_effect_allowed"] is False
    assert claimed["campaign_restart_allowed"] is False
    plan = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id=issue,
        escalation_state_dir=tmp_path, technical_remediation=True,
        technical_remediation_id=route_id,
    )
    assert plan["mode"] == "local_diagnostic"
    assert plan["spawn"] is None

    second_stopped = store.record_failure(
        issue, "implementer", "test_red", "apex",
    )
    assert second_stopped.action == "technical_blocked"
    second_generation = store.status(issue)["halt_generation"]
    assert second_generation == halted["halt_generation"] + 1
    assert json.loads(store._path(issue).read_text(encoding="utf-8"))[
        "remediation_authorization"
    ] == authorization_before

    with pytest.raises(RoutingConfigError, match="génération d'arrêt obsolète"):
        store.resume_technical_remediation(
            issue, halted["halt_generation"], "b" * 64,
        )
    second_resumed = store.resume_technical_remediation(
        issue, second_generation, "b" * 64,
    )
    assert second_resumed["replayed"] is False
    assert store.resume_technical_remediation(
        issue, second_generation, "b" * 64,
    ) == {**second_resumed, "replayed": True}

    before_stale_claim = store._path(issue).read_bytes()
    with pytest.raises(EscalationTechnicalBlockedError, match="changé de génération"):
        store.claim_technical_remediation_route(
            issue, "implementer", halted["halt_generation"], route_id,
        )
    assert store._path(issue).read_bytes() == before_stale_claim
    second_route_id = "f148-local-route-0002"
    second_claimed = store.claim_technical_remediation_route(
        issue, "implementer", second_generation, second_route_id,
    )
    assert second_claimed["provider_effect_allowed"] is False
    assert store.claim_technical_remediation_route(
        issue, "implementer", second_generation, second_route_id,
    ) == {**second_claimed, "replayed": True}

    before = store._path(issue).read_bytes()
    with pytest.raises(RoutingConfigError, match="n'est pas arrêtée"):
        store.resume_technical_remediation(
            issue, second_generation, "c" * 64,
        )
    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        store.active_floor(issue, "implementer")
    assert store._path(issue).read_bytes() == before


@pytest.mark.parametrize(
    "tampering", ["durable_deleted", "window_deleted", "role_counter"],
)
def test_current_window_and_counters_must_match_the_durable_audit(
    tmp_path, tampering,
):
    store = EscalationStore(f"owner/remediation-consistency-{tampering}", state_dir=tmp_path)
    issue = "FOUNDRY-82"
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 2)
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "frontier",
    )
    path = store._path(issue)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if tampering == "durable_deleted":
        payload["consumption_audit"] = []
    elif tampering == "window_deleted":
        payload["remediation_authorization"]["consumption_audit"] = []
    else:
        payload["roles"]["implementer"]["failures_since_escalation"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.status(issue)
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "frontier",
        )
    assert path.read_bytes() == before


def test_failure_cli_serializes_atomic_redacted_remediation_authorization(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    _human_stop(store, "FOUNDRY-65", "implementer")
    generation = store.status("FOUNDRY-65")["halt_generation"]
    store.resume("FOUNDRY-65", "remediation_reviewed", generation, 2)

    main([
        "escalation", "failure", "FOUNDRY-65", "implementer", "--kind",
        "review_blocking_after_fix", "--current-tier", "frontier",
        "--idempotency-key", "f107-remediation-65", "--root", str(tmp_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "remediation_continued"
    assert payload["human_required"] is False
    assert payload["remediation_authorization"] == {
        "state": "active", "role": "implementer", "halt_generation": generation,
        "maximum_credits": 2, "remaining_credits": 1,
    }
    assert payload["remediation_authorization"] == store.status("FOUNDRY-65")["remediation_authorization"]


def test_failure_cli_returns_the_exact_just_exhausted_credit_snapshot(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    store = EscalationStore.for_root(tmp_path)
    _human_stop(store, "FOUNDRY-68", "implementer")
    generation = store.status("FOUNDRY-68")["halt_generation"]
    store.resume("FOUNDRY-68", "remediation_reviewed", generation, 1)

    main([
        "escalation", "failure", "FOUNDRY-68", "implementer", "--kind",
        "review_blocking_after_fix", "--current-tier", "frontier",
        "--idempotency-key", "f107-remediation-68", "--root", str(tmp_path),
    ])

    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "remediation_continued"
    assert payload["human_required"] is False
    assert payload["remediation_authorization"] == {
        "state": "exhausted", "role": "implementer", "halt_generation": generation,
        "maximum_credits": 1, "remaining_credits": 0,
    }


def test_serialized_released_v1_ledger_without_remediation_fields_remains_readable(tmp_path):
    store = EscalationStore("owner/released-v1", state_dir=tmp_path)
    path = store._path("FOUNDRY-66")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": 1, "issue_id": "FOUNDRY-66", "total_escalations": 0,
        "halted": False, "halted_reason": None, "halt_generation": 0, "roles": {},
    }), encoding="utf-8")

    status = store.status("FOUNDRY-66")
    assert status["remediation_authorization"]["state"] == "none"
    assert store.record_failure("FOUNDRY-66", "implementer", "test_red", "balanced").action == "failure_recorded"


@pytest.mark.parametrize("halted", [False, True])
def test_genuine_released_v1_ledgers_normalize_without_remediation_history(
    tmp_path, halted,
):
    store = EscalationStore(f"owner/released-v1-{halted}", state_dir=tmp_path)
    issue = "FOUNDRY-76"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    if halted:
        ledger = {
            "version": 1,
            "issue_id": issue,
            "total_escalations": 2,
            "halted": True,
            "halted_reason": (
                "plafond de 2 escalades atteint ; "
                "signal supplémentaire test_red refusé."
            ),
            "roles": {
                "scout": {
                    "minimum_tier": "frontier",
                    "escalations": 2,
                    "deterministic_failures": 6,
                    "failures_since_escalation": 2,
                },
            },
        }
    else:
        ledger = {
            "version": 1,
            "issue_id": issue,
            "total_escalations": 1,
            "halted": False,
            "halted_reason": None,
            "roles": {
                "implementer": {
                    "minimum_tier": "frontier",
                    "escalations": 1,
                    "deterministic_failures": 2,
                    "failures_since_escalation": 0,
                },
            },
        }
    path.write_text(json.dumps(ledger), encoding="utf-8")

    status = store.status(issue)
    assert status["halt_generation"] == (1 if halted else 0)
    assert status["resume_count"] == 0
    assert status["consumption_audit"] == []
    assert status["remediation_authorization"]["state"] == "none"
    if halted:
        with pytest.raises(EscalationAuthorityAmbiguousError):
            store.active_floor(issue, "scout")
        reclassified = store.reclassify_legacy_terminal(issue, 1)
        assert reclassified["action"] == "authority_ambiguous"
        assert reclassified["reclassified"] is False
        assert store.status(issue)["human_required"] is False
    else:
        assert store.active_floor(issue, "implementer") == "frontier"


@pytest.mark.parametrize(
    "corruption",
    [
        "role_record_list",
        "halted_missing",
        "halted_non_bool",
        "counter",
        "tier",
        "timestamp",
        "halted_reason",
        "unknown_key",
        "empty_file",
    ],
)
def test_hostile_outer_ledger_is_total_redacted_and_denies_api_and_cli_routing(
    tmp_path, corruption,
):
    state_dir = tmp_path / "state"
    store = EscalationStore.for_root(tmp_path, state_dir=state_dir)
    issue = "FOUNDRY-81"
    _halt_after_budget(store, issue)
    store.resume(issue, "manual_retry_approved", 1)
    path = store._path(issue)
    payload = json.loads(path.read_text(encoding="utf-8"))
    sentinel = "HOSTILE_OUTER_LEDGER_SECRET_SENTINEL"
    if corruption == "role_record_list":
        payload["roles"] = {"implementer": []}
    elif corruption == "halted_missing":
        payload.pop("halted")
    elif corruption == "halted_non_bool":
        payload["halted"] = sentinel
    elif corruption == "counter":
        payload["roles"]["scout"]["deterministic_failures"] = [sentinel]
    elif corruption == "tier":
        payload["roles"]["scout"]["minimum_tier"] = "invalid-tier"
    elif corruption == "timestamp":
        payload["last_resumed_at"] = sentinel
    elif corruption == "halted_reason":
        payload["halted"] = True
        payload["halt_generation"] = 2
        payload["halted_reason"] = sentinel
    elif corruption == "unknown_key":
        payload["unknown_secret_field"] = sentinel
    if corruption == "empty_file":
        path.write_bytes(b"")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    for operation in (
        lambda: store.status(issue),
        lambda: store.active_floor(issue, "scout"),
        lambda: store.record_failure(issue, "scout", "test_red", "frontier"),
    ):
        with pytest.raises(RoutingConfigError) as rejected:
            operation()
        assert sentinel not in str(rejected.value)
    assert path.read_bytes() == before

    cli = subprocess.run(
        [
            sys.executable, str(PLUGIN_ROOT / "tooling" / "foundry_cli.py"),
            "routing", "escalation", "failure", issue, "scout",
            "--kind", "test_red", "--current-tier", "frontier",
            "--idempotency-key", "f107-hostile-failure-69",
            "--root", str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "FOUNDRY_DATA": str(state_dir)},
    )
    assert cli.returncode == 2
    assert cli.stdout == ""
    assert cli.stderr == "Configuration de routage invalide.\n"
    assert "Traceback" not in cli.stderr
    assert "TypeError" not in cli.stderr
    assert "KeyError" not in cli.stderr
    assert sentinel not in cli.stdout + cli.stderr
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("role", []), ("state", []), ("halt_generation", {}), ("maximum_credits", []),
    ("remaining_credits", {}), ("consumed_credits", []), ("forfeited_credits", {}),
    ("armed_at", []), ("consumption_audit", [{"code": [], "at": {}}]),
])
def test_hostile_remediation_json_is_total_and_fails_closed(tmp_path, field, value):
    store = EscalationStore("owner/remediation-hostile", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-67", "implementer")
    generation = store.status("FOUNDRY-67")["halt_generation"]
    store.resume("FOUNDRY-67", "remediation_reviewed", generation, 1)
    path = store._path("FOUNDRY-67")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["remediation_authorization"][field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    before = path.read_bytes()
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.record_failure(
            "FOUNDRY-67", "implementer", "review_blocking_after_fix", "frontier",
        )
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.status("FOUNDRY-67")
    assert path.read_bytes() == before


@pytest.mark.parametrize("signal", ["risk", "explicit_request"])
@pytest.mark.parametrize("role", ["implementer", "architect"])
def test_active_remediation_rejects_risk_and_explicit_request_without_escalating(
    tmp_path, signal, role,
):
    store = EscalationStore(f"owner/remediation-{signal}-{role}", state_dir=tmp_path)
    issue = "FOUNDRY-69"
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 2)
    before = store.status(issue)

    if signal == "risk":
        decision = store.record_risk(issue, role, "adr_creation", "frontier")
    else:
        decision = store.record_explicit_request(issue, role, "frontier")

    after = store.status(issue)
    raw = json.loads(store._path(issue).read_text(encoding="utf-8"))
    assert decision.action == "technical_blocked"
    assert decision.human_required is False
    assert after["total_escalations"] == before["total_escalations"]
    assert after["roles"] == before["roles"]
    assert after["remediation_authorization"]["state"] == "invalidated"
    assert raw["remediation_authorization"]["consumed_credits"] == 0
    assert raw["remediation_authorization"]["forfeited_credits"] == 2
    assert raw["remediation_authorization"]["consumption_audit"] == []


@pytest.mark.parametrize("signal", ["risk", "explicit_request"])
@pytest.mark.parametrize(
    "window_state", ["malformed", "exhausted", "cancelled", "invalidated"],
)
def test_nonactive_remediation_rejects_risk_and_explicit_request_fail_closed(
    tmp_path, signal, window_state,
):
    store = EscalationStore(
        f"owner/remediation-{signal}-{window_state}", state_dir=tmp_path,
    )
    issue = "FOUNDRY-70"
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 1)
    if window_state == "exhausted":
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "frontier",
        )
    elif window_state == "cancelled":
        store.cancel_remediation(issue, generation)
    elif window_state == "invalidated":
        store.record_failure(issue, "implementer", "test_red", "frontier")
    else:
        path = store._path(issue)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["remediation_authorization"]["consumption_audit"] = [{
            "code": "risk_accepted", "at": "2026-01-01T00:00:00Z",
        }]
        path.write_text(json.dumps(payload), encoding="utf-8")
    if window_state == "malformed":
        before = store._path(issue).read_bytes()
        with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
            if signal == "risk":
                store.record_risk(issue, "architect", "adr_creation", "frontier")
            else:
                store.record_explicit_request(issue, "architect", "frontier")
        assert store._path(issue).read_bytes() == before
        return

    before = store.status(issue)

    if signal == "risk":
        decision = store.record_risk(issue, "architect", "adr_creation", "frontier")
    else:
        decision = store.record_explicit_request(issue, "architect", "frontier")

    after = store.status(issue)
    assert decision.action == "technical_blocked"
    assert decision.human_required is False
    assert after["halted"] is True
    assert after["total_escalations"] == before["total_escalations"]
    assert after["roles"] == before["roles"]
    assert after["remediation_authorization"]["state"] == window_state


@pytest.mark.parametrize("audit_code", RESUME_REASON_CODES)
def test_resume_reason_codes_are_never_valid_consumption_events(tmp_path, audit_code):
    store = EscalationStore(f"owner/remediation-audit-{audit_code}", state_dir=tmp_path)
    issue = "FOUNDRY-71"
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 1)
    path = store._path(issue)
    payload = json.loads(path.read_text(encoding="utf-8"))
    armed_at = payload["remediation_authorization"]["armed_at"]
    event = {
        "code": audit_code,
        "at": armed_at,
        "role": "implementer",
        "halt_generation": generation,
    }
    payload["remediation_authorization"].update({
        "state": "exhausted", "remaining_credits": 0, "consumed_credits": 1,
        "consumption_audit": [event],
    })
    payload["consumption_audit"] = [event]
    payload["roles"]["implementer"]["deterministic_failures"] += 1
    payload["roles"]["implementer"]["failures_since_escalation"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.status(issue)


def test_consumption_audit_rejects_events_before_authorization(tmp_path):
    store = EscalationStore("owner/remediation-audit-order", state_dir=tmp_path)
    issue = "FOUNDRY-72"
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 1)
    path = store._path(issue)
    payload = json.loads(path.read_text(encoding="utf-8"))
    event = {
        "code": "review_blocking_after_fix_consumed",
        "at": "2000-01-01T00:00:00Z",
        "role": "implementer",
        "halt_generation": generation,
    }
    payload["remediation_authorization"].update({
        "state": "exhausted", "remaining_credits": 0, "consumed_credits": 1,
        "consumption_audit": [event],
    })
    payload["consumption_audit"] = [event]
    payload["roles"]["implementer"]["deterministic_failures"] += 1
    payload["roles"]["implementer"]["failures_since_escalation"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.status(issue)


def test_active_floor_allows_routing_around_the_last_credit_until_next_signal(tmp_path):
    store = EscalationStore("owner/remediation-floor-routing", state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-73", "implementer")
    generation = store.status("FOUNDRY-73")["halt_generation"]
    store.resume("FOUNDRY-73", "remediation_reviewed", generation, 1)
    active = store.status("FOUNDRY-73")

    assert store.active_floor("FOUNDRY-73", "reviewer") is None
    assert store.status("FOUNDRY-73") == active

    consumed = store.record_failure(
        "FOUNDRY-73", "implementer", "review_blocking_after_fix", "frontier",
    )
    assert consumed.action == "remediation_continued"
    exhausted = store.status("FOUNDRY-73")
    assert exhausted["halted"] is False
    assert exhausted["remediation_authorization"]["state"] == "exhausted"

    assert store.active_floor("FOUNDRY-73", "implementer") is None
    assert store.active_floor("FOUNDRY-73", "reviewer") is None
    assert store.status("FOUNDRY-73") == exhausted

    stopped = store.record_failure(
        "FOUNDRY-73", "implementer", "review_blocking_after_fix", "frontier",
    )
    assert stopped.action == "technical_blocked"
    assert stopped.human_required is False
    assert store.status("FOUNDRY-73")["halted"] is True


def test_failure_identity_replays_the_exact_decision_without_consuming_twice(tmp_path):
    store = EscalationStore("owner/idempotent-failure", state_dir=tmp_path)
    key = "effect-1234567890abcdef"

    first = store.record_failure(
        "FOUNDRY-74", "implementer", "test_red", "balanced",
        idempotency_key=key,
    )
    before = store._path("FOUNDRY-74").read_bytes()
    second = store.record_failure(
        "FOUNDRY-74", "implementer", "test_red", "balanced",
        idempotency_key=key,
    )

    assert second == first
    assert store._path("FOUNDRY-74").read_bytes() == before
    state = json.loads(before)
    assert state["roles"]["implementer"]["deterministic_failures"] == 1
    assert list(state["failure_receipts"]) == [key]
    with pytest.raises(RoutingConfigError, match="autre signal"):
        store.record_failure(
            "FOUNDRY-74", "implementer", "review_blocking", "balanced",
            idempotency_key=key,
        )


def test_first_campaign_review_authorization_is_exact_expiring_and_one_shot(tmp_path):
    store = EscalationStore("owner/first-campaign-review", state_dir=tmp_path)
    issue = "FOUNDRY-104"
    effect_id = "effect-1234567890abcdef1234567890abcdef"
    authorization_digest = "a" * 64
    store.record_failure(
        issue, "implementer", "review_blocking", "apex",
        idempotency_key=effect_id, authorization_aware=True,
    )

    decision = store.consume_campaign_review_remediation(
        issue, "apex", effect_id=effect_id, expires_at=2_000,
        authorization_digest=authorization_digest, now_ms=1_500,
    )
    consumed = store._path(issue).read_bytes()
    replayed = store.consume_campaign_review_remediation(
        issue, "apex", effect_id=effect_id, expires_at=2_000,
        authorization_digest=authorization_digest, now_ms=9_000,
    )

    assert decision == replayed
    assert decision["allowed"] is True
    assert decision["action"] == "remediation_continued"
    assert store._path(issue).read_bytes() == consumed

    with pytest.raises(RoutingConfigError, match="coordonnées"):
        store.consume_campaign_review_remediation(
            issue, "apex", effect_id="effect-fedcba0987654321fedcba0987654321",
            expires_at=2_000, authorization_digest=authorization_digest,
            now_ms=1_500,
        )
    with pytest.raises(RoutingConfigError, match="coordonnées"):
        store.consume_campaign_review_remediation(
            issue, "apex", effect_id=effect_id, expires_at=2_000,
            authorization_digest="b" * 64, now_ms=1_500,
        )
    assert store._path(issue).read_bytes() == consumed


def test_first_campaign_review_authorization_refuses_expiry_and_wrong_lineage(tmp_path):
    store = EscalationStore("owner/first-campaign-review-refusal", state_dir=tmp_path)
    issue = "FOUNDRY-104"
    effect_id = "effect-1234567890abcdef1234567890abcdef"
    store.record_failure(
        issue, "implementer", "review_blocking", "frontier",
        idempotency_key=effect_id, authorization_aware=True,
    )
    before = store._path(issue).read_bytes()

    expired = store.consume_campaign_review_remediation(
        issue, "frontier", effect_id=effect_id, expires_at=1_000,
        authorization_digest="a" * 64, now_ms=1_000,
    )
    assert expired["allowed"] is False
    assert expired["action"] == "authorization_expired"
    with pytest.raises(RoutingConfigError, match="première review"):
        store.consume_campaign_review_remediation(
            issue, "balanced", effect_id=effect_id, expires_at=2_000,
            authorization_digest="a" * 64, now_ms=1_000,
        )

    assert store._path(issue).read_bytes() == before


def test_remediation_credit_and_decision_are_one_atomic_idempotent_mutation(tmp_path):
    store = EscalationStore("owner/idempotent-remediation", state_dir=tmp_path)
    issue = "FOUNDRY-75"
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 2)
    key = "effect-fedcba0987654321"

    first = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "frontier",
        idempotency_key=key,
    )
    before = store._path(issue).read_bytes()
    replay = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "frontier",
        idempotency_key=key,
    )

    assert first.action == replay.action == "remediation_continued"
    assert replay == first
    assert store._path(issue).read_bytes() == before
    state = json.loads(before)
    authorization = state["remediation_authorization"]
    assert authorization["remaining_credits"] == 1
    assert authorization["consumed_credits"] == 1
    assert len(authorization["consumption_audit"]) == 1


def test_generation_two_human_window_consumption_is_atomic_and_idempotent(tmp_path):
    store = EscalationStore("owner/f160-generation-two", state_dir=tmp_path)
    issue = "FOUNDRY-160"
    generation = _generation_two_remediation_window(store, issue)
    key = "f160-generation-two-review"
    before = store.status(issue)

    first = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
        idempotency_key=key,
    )
    persisted = store._path(issue).read_bytes()
    replay = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
        idempotency_key=key,
    )

    assert first == replay
    assert first.action == "remediation_continued"
    assert first.human_required is False
    assert first.authority_category is None
    assert first.technical_remediation is None
    assert first.remediation_authorization == {
        "state": "exhausted", "role": "implementer",
        "halt_generation": generation,
        "maximum_credits": 1, "remaining_credits": 0,
    }
    assert store._path(issue).read_bytes() == persisted

    after = store.status(issue)
    assert after["halted"] is False
    assert after["halt_generation"] == generation == 2
    assert after["resume_count"] == 1
    assert after["last_resumed_halt_generation"] == generation
    assert after["total_escalations"] == before["total_escalations"]
    assert after["technical_remediation_audit"] == before[
        "technical_remediation_audit"
    ]
    assert after["roles"]["implementer"]["deterministic_failures"] == (
        before["roles"]["implementer"]["deterministic_failures"] + 1
    )
    raw = json.loads(persisted)
    window_audit = raw["remediation_authorization"]["consumption_audit"]
    assert len(window_audit) == 1
    assert raw["consumption_audit"] == window_audit
    assert window_audit[0]["halt_generation"] == generation
    original_receipt = raw["failure_receipts"][key]

    with pytest.raises(RoutingConfigError, match="identité idempotente d'échec invalide"):
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "apex",
            idempotency_key="short",
        )
    assert store._path(issue).read_bytes() == persisted

    for role, kind, tier in (
        ("reviewer", "review_blocking_after_fix", "apex"),
        ("implementer", "test_red", "apex"),
        ("implementer", "review_blocking_after_fix", "frontier"),
    ):
        with pytest.raises(
            RoutingConfigError,
            match="identité idempotente d'échec réutilisée avec un autre signal",
        ):
            store.record_failure(
                issue, role, kind, tier, idempotency_key=key,
            )
        assert json.loads(store._path(issue).read_bytes())["failure_receipts"][key] == (
            original_receipt
        )


@pytest.mark.parametrize(
    ("window_state", "credits", "remaining"),
    [
        ("active", 2, 1),
        ("cancelled", 2, 0),
        ("invalidated", 2, 0),
    ],
)
def test_generation_two_window_states_remain_fail_closed(
    tmp_path, window_state, credits, remaining,
):
    store = EscalationStore(
        f"owner/f160-generation-two-{window_state}", state_dir=tmp_path,
    )
    issue = "FOUNDRY-161"
    generation = _generation_two_remediation_window(store, issue, credits)
    continued = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
    )
    assert continued.action == "remediation_continued"

    if window_state == "cancelled":
        store.cancel_remediation(issue, generation)
    elif window_state == "invalidated":
        store.record_failure(issue, "implementer", "test_red", "apex")

    before_rearm = store._path(issue).read_bytes()
    with pytest.raises(RoutingConfigError, match="fenêtre de remédiation épuisée"):
        store.rearm_remediation(
            issue, "implementer", "manual_retry_approved", generation, 1,
        )
    assert store._path(issue).read_bytes() == before_rearm
    state = store.status(issue)
    assert state["remediation_authorization"] == {
        "state": window_state, "role": "implementer",
        "halt_generation": generation,
        "maximum_credits": credits, "remaining_credits": remaining,
    }
    assert state["consumption_audit"][0]["halt_generation"] == generation


def test_generation_two_consumption_rejects_a_technical_only_generation(tmp_path):
    store = EscalationStore("owner/f160-generation-forgery", state_dir=tmp_path)
    issue = "FOUNDRY-162"
    _generation_two_remediation_window(store, issue)
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
        idempotency_key="f160-valid-generation-two",
    )
    path = store._path(issue)
    payload = json.loads(path.read_text(encoding="utf-8"))
    forged = {
        "code": "review_blocking_after_fix_consumed",
        "at": payload["technical_remediation_audit"][0]["at"],
        "role": "implementer",
        "halt_generation": 1,
    }
    payload["consumption_audit"].insert(0, forged)
    payload["roles"]["implementer"]["deterministic_failures"] += 1
    payload["roles"]["implementer"]["failures_since_escalation"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    malformed = path.read_bytes()

    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.status(issue)
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "apex",
            idempotency_key="f160-refused-generation-one",
        )
    assert path.read_bytes() == malformed


def test_generation_two_consumption_succeeds_through_the_cli(
    tmp_path, monkeypatch, capsys,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path, state_dir=state_dir)
    issue = "FOUNDRY-163"
    generation = _generation_two_remediation_window(store, issue)
    argv = [
        "escalation", "failure", issue, "implementer",
        "--kind", "review_blocking_after_fix", "--current-tier", "apex",
        "--idempotency-key", "f160-cli-generation-two",
        "--root", str(tmp_path),
    ]

    main(argv)
    first = json.loads(capsys.readouterr().out)
    persisted = store._path(issue).read_bytes()
    main(argv)
    replay = json.loads(capsys.readouterr().out)

    assert replay == first
    assert first["action"] == "remediation_continued"
    assert first["human_required"] is False
    assert first["remediation_authorization"] == {
        "state": "exhausted", "role": "implementer",
        "halt_generation": generation,
        "maximum_credits": 1, "remaining_credits": 0,
    }
    assert store._path(issue).read_bytes() == persisted


@pytest.mark.parametrize("after_halt", [False, True])
def test_exhausted_authorization_rejects_every_incompatible_generation(
    tmp_path, after_halt,
):
    store = EscalationStore(
        f"owner/remediation-exhausted-generation-{after_halt}", state_dir=tmp_path,
    )
    issue = "FOUNDRY-80"
    _human_stop(store, issue, "implementer")
    authorization_generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", authorization_generation, 1)
    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "frontier",
    )
    assert store.active_floor(issue, "implementer") is None
    assert store.active_floor(issue, "reviewer") is None
    if after_halt:
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "frontier",
        )

    path = store._path(issue)
    payload = json.loads(path.read_text(encoding="utf-8"))
    incompatible_generation = authorization_generation + (2 if after_halt else 1)
    payload["halt_generation"] = incompatible_generation
    payload["resume_count"] = incompatible_generation - (1 if after_halt else 0)
    payload["last_resumed_halt_generation"] = payload["resume_count"]
    payload["last_resumed_at"] = payload["consumption_audit"][-1]["at"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.active_floor(issue, "implementer")
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "frontier",
        )
    assert path.read_bytes() == before


def test_exhausted_window_rearm_preserves_floor_counters_and_all_past_audits(
    tmp_path,
):
    store = EscalationStore("owner/remediation-rearm", state_dir=tmp_path)
    issue = "FOUNDRY-90"
    generation = _exhaust_remediation_window(store, issue, credits=2)
    before = store.status(issue)
    raw_before = json.loads(store._path(issue).read_text(encoding="utf-8"))
    exhausted_armed_at = raw_before["remediation_authorization"]["armed_at"]
    with pytest.raises(RoutingConfigError, match="n'est pas arrêtée"):
        store.resume(
            issue, "manual_retry_approved", generation, remediation_credits=3,
        )
    assert store.status(issue) == before

    rearmed = store.rearm_remediation(
        issue, "implementer", "manual_retry_approved", generation, 3,
    )

    assert rearmed["action"] == "remediation_rearmed"
    assert rearmed["action"] != "resumed"
    assert rearmed["exhausted_window"] == {
        "armed_at": exhausted_armed_at,
        "maximum_credits": 2,
    }
    after = store.status(issue)
    assert after["halted"] is False
    assert after["halt_generation"] == generation
    assert after["resume_count"] == before["resume_count"] == 1
    assert after["last_resumed_at"] == before["last_resumed_at"]
    assert after["last_resume_reason"] == before["last_resume_reason"]
    assert after["total_escalations"] == before["total_escalations"]
    assert after["roles"] == before["roles"]
    assert after["consumption_audit"] == before["consumption_audit"]
    assert store.active_floor(issue, "implementer") == "apex"
    assert after["remediation_authorization"] == {
        "state": "active", "role": "implementer",
        "halt_generation": generation,
        "maximum_credits": 3, "remaining_credits": 3,
    }
    audit = after["remediation_rearm_audit"]
    assert len(audit) == 1
    assert set(audit[0]) == {
        "code", "at", "reason", "role", "halt_generation", "granted_credits",
        "exhausted_window_armed_at", "exhausted_window_maximum_credits",
    }
    assert audit[0] == {
        "code": "remediation_window_rearmed",
        "at": rearmed["rearmed_at"],
        "reason": "manual_retry_approved",
        "role": "implementer",
        "halt_generation": generation,
        "granted_credits": 3,
        "exhausted_window_armed_at": exhausted_armed_at,
        "exhausted_window_maximum_credits": 2,
    }

    for _ in range(3):
        store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "apex",
        )
    final = store.status(issue)
    assert final["remediation_authorization"]["state"] == "exhausted"
    assert final["consumption_audit"][:2] == before["consumption_audit"]
    assert len(final["consumption_audit"]) == 5
    assert final["remediation_rearm_audit"] == audit


@pytest.mark.parametrize("window_state", ["active", "cancelled", "invalidated"])
def test_rearm_atomically_rejects_every_nonexhausted_authorization(
    tmp_path, window_state,
):
    store = EscalationStore(
        f"owner/remediation-rearm-{window_state}", state_dir=tmp_path,
    )
    issue = "FOUNDRY-91"
    store.record_risk(issue, "implementer", "adr_creation", "apex")
    _human_stop(store, issue, "implementer")
    generation = store.status(issue)["halt_generation"]
    store.resume(issue, "remediation_reviewed", generation, 2)
    if window_state == "cancelled":
        store.cancel_remediation(issue, generation)
    elif window_state == "invalidated":
        store.record_failure(issue, "implementer", "test_red", "apex")
    path = store._path(issue)
    before = path.read_bytes()

    with pytest.raises(RoutingConfigError, match="épuisée"):
        store.rearm_remediation(
            issue, "implementer", "manual_retry_approved", generation, 1,
        )

    assert path.read_bytes() == before
    assert store.status(issue)["remediation_authorization"]["state"] == window_state


def test_rearm_atomically_rejects_stale_generation_different_role_and_halted_exhaustion(
    tmp_path,
):
    store = EscalationStore("owner/remediation-rearm-cas", state_dir=tmp_path)
    issue = "FOUNDRY-92"
    generation = _exhaust_remediation_window(store, issue)
    path = store._path(issue)
    exhausted = path.read_bytes()

    with pytest.raises(RoutingConfigError, match="rôle de remédiation différent"):
        store.rearm_remediation(
            issue, "reviewer", "manual_retry_approved", generation, 1,
        )
    with pytest.raises(RoutingConfigError, match="génération d'arrêt obsolète"):
        store.rearm_remediation(
            issue, "implementer", "manual_retry_approved", generation + 1, 1,
        )
    assert path.read_bytes() == exhausted

    store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
    )
    halted = path.read_bytes()
    with pytest.raises(RoutingConfigError, match="génération d'arrêt obsolète"):
        store.rearm_remediation(
            issue, "implementer", "manual_retry_approved", generation, 1,
        )
    assert path.read_bytes() == halted


@pytest.mark.parametrize("credits", [0, 4, True])
def test_rearm_rejects_unbounded_credits_without_mutation(tmp_path, credits):
    store = EscalationStore("owner/remediation-rearm-credits", state_dir=tmp_path)
    generation = _exhaust_remediation_window(store, "FOUNDRY-93")
    path = store._path("FOUNDRY-93")
    before = path.read_bytes()
    with pytest.raises(RoutingConfigError, match="entier de 1 à 3"):
        store.rearm_remediation(
            "FOUNDRY-93", "implementer", "manual_retry_approved",
            generation, credits,
        )
    assert path.read_bytes() == before


def test_rearm_cli_is_distinct_audited_and_rejects_sensitive_reason(
    tmp_path, monkeypatch, capsys,
):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    store = EscalationStore.for_root(tmp_path)
    issue = "FOUNDRY-94"
    generation = _exhaust_remediation_window(store, issue)

    main([
        "escalation", "rearm-remediation", issue, "implementer",
        "--reason", "manual_retry_approved",
        "--halt-generation", str(generation),
        "--remediation-credits", "2", "--root", str(tmp_path),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "remediation_rearmed"
    assert payload["remediation_authorization"]["state"] == "active"
    assert payload["remediation_rearm_audit"][-1]["granted_credits"] == 2

    sensitive = "PROMPT_SECRET_SENTINEL"
    before = store._path(issue).read_bytes()
    with pytest.raises(RoutingConfigError, match="code de reprise invalide") as rejected:
        store.rearm_remediation(
            issue, "implementer", sensitive, generation, 1,
        )
    assert sensitive not in str(rejected.value)
    assert store._path(issue).read_bytes() == before


def test_concurrent_rearm_is_serialized_and_grants_exactly_one_window(tmp_path):
    repository = "owner/remediation-rearm-race"
    store = EscalationStore(repository, state_dir=tmp_path)
    _exhaust_remediation_window(store, "FOUNDRY-90")
    before = store.status("FOUNDRY-90")
    args = [(repository, str(tmp_path))] * 2

    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_remediation_rearm, args)

    assert sorted(result["action"] for result in results) == [
        "rejected", "remediation_rearmed",
    ]
    final = EscalationStore(repository, state_dir=tmp_path).status("FOUNDRY-90")
    assert final["remediation_authorization"] == {
        "state": "active", "role": "implementer", "halt_generation": 1,
        "maximum_credits": 2, "remaining_credits": 2,
    }
    assert len(final["remediation_rearm_audit"]) == 1
    assert final["roles"] == before["roles"]
    assert final["consumption_audit"] == before["consumption_audit"]


def test_malformed_rearm_audit_is_redacted_and_never_rewritten(tmp_path):
    store = EscalationStore("owner/remediation-rearm-redacted", state_dir=tmp_path)
    issue = "FOUNDRY-95"
    generation = _exhaust_remediation_window(store, issue)
    store.rearm_remediation(
        issue, "implementer", "manual_retry_approved", generation, 1,
    )
    path = store._path(issue)
    payload = json.loads(path.read_text(encoding="utf-8"))
    sentinel = "REARM_PROMPT_SECRET_SENTINEL"
    payload["remediation_rearm_audit"][0]["reason"] = sentinel
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(RoutingConfigError, match="état d'escalade invalide") as rejected:
        store.status(issue)
    assert sentinel not in str(rejected.value)
    assert path.read_bytes() == before


def _f106_legacy_ledger(issue, role="implementer"):
    return {
        "version": 1,
        "issue_id": issue,
        "total_escalations": 1,
        "halted": True,
        "halted_reason": (
            f"{role} est déjà au niveau apex après le signal "
            "review_blocking_after_fix."
        ),
        "halt_generation": 1,
        "halted_role": role,
        "roles": {
            role: {
                "minimum_tier": "apex",
                "escalations": 1,
                "deterministic_failures": 3,
                "failures_since_escalation": 1,
            },
        },
    }


def test_f106_shaped_legacy_halt_reclassifies_once_then_needs_local_evidence(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-106"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue)), encoding="utf-8")

    first = store.reclassify_legacy_terminal(issue, 1)
    after_first = path.read_bytes()
    replay = store.reclassify_legacy_terminal(issue, 1)

    assert first == {
        "action": "technical_blocked",
        "issue_id": issue,
        "halt_generation": 1,
        "human_required": False,
        "technical_remediation": {
            "path": "bounded_local_technical_diagnostic",
            "reason_code": "legacy_deterministic_failure_reclassified",
            "provider_effect_allowed": False,
            "campaign_restart_allowed": False,
            "human_authorization_required": False,
        },
        "reclassified": True,
    }
    assert replay == {**first, "reclassified": False}
    assert path.read_bytes() == after_first
    status = store.status(issue)
    assert status["halted"] is True
    assert status["human_required"] is False
    assert status["technical_blocked"] is True
    assert status["terminal_reclassification"]["previous_halted_reason"] == (
        "implementer est déjà au niveau apex après le signal "
        "review_blocking_after_fix."
    )
    assert status["terminal_reclassification"]["evidence_kind"] == (
        "complete_legacy_ledger"
    )
    assert len(status["terminal_reclassification"]["causal_digest"]) == 64
    persisted = json.loads(path.read_text(encoding="utf-8"))[
        "terminal_reclassification"
    ]
    assert persisted["causal_facts"]["issue_id"] == issue
    assert persisted["causal_facts"]["failure_receipt_keys"] == []
    with pytest.raises(EscalationTechnicalBlockedError):
        store.active_floor(issue, "implementer")

    resumed = store.resume_technical_remediation(issue, 1, "b" * 64)
    after_resume = path.read_bytes()
    replayed = store.resume_technical_remediation(issue, 1, "b" * 64)

    assert resumed["action"] == "technical_remediation_resumed"
    assert resumed["human_required"] is False
    assert resumed["provider_effect_allowed"] is False
    assert resumed["campaign_restart_allowed"] is False
    assert replayed == {**resumed, "replayed": True}
    assert path.read_bytes() == after_resume
    assert store.status(issue)["halted"] is False
    assert store.status(issue)["technical_remediation_open"] is True
    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        store.active_floor(issue, "implementer")
    route_id = "f106-local-route-0001"
    with pytest.raises(EscalationTechnicalBlockedError, match="réclamation atomique"):
        store.active_floor(
            issue, "implementer", technical_remediation=True,
            technical_remediation_id=route_id,
        )
    claimed = store.claim_technical_remediation_route(
        issue, "implementer", 1, route_id,
    )
    assert claimed["provider_effect_allowed"] is False
    assert claimed["campaign_restart_allowed"] is False
    assert store.claim_technical_remediation_route(
        issue, "implementer", 1, route_id,
    ) == {**claimed, "replayed": True}
    assert store.status(issue)["technical_remediation_open"] is False
    assert store.active_floor(
        issue, "implementer", technical_remediation=True,
        technical_remediation_id=route_id,
    ) == "apex"
    with pytest.raises(EscalationTechnicalBlockedError, match="nouvel identifiant"):
        store.claim_technical_remediation_route(
            issue, "implementer", 1, "f106-local-route-0002",
        )
    with pytest.raises(EscalationTechnicalBlockedError, match="rôle arrêté"):
        store.claim_technical_remediation_route(
            issue, "scout", 1, route_id,
        )
    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        codex_spawn_plan(
            "implementer", _packet(), root=tmp_path, issue_id=issue,
            escalation_state_dir=tmp_path,
        )
    plan = codex_spawn_plan(
        "implementer", _packet(), root=tmp_path, issue_id=issue,
        escalation_state_dir=tmp_path, technical_remediation=True,
        technical_remediation_id=route_id,
    )
    assert plan["mode"] == "local_diagnostic"
    assert plan["spawn"] is None
    assert "Do not invoke a provider" in plan["local_instructions"]
    assert plan["route"]["selected_tier"] == "apex"
    assert plan["escalation"]["technical_remediation_open"] is False
    assert plan["escalation"]["technical_remediation_requested"] is True
    assert plan["escalation"]["technical_remediation_claimed"] is True
    assert plan["escalation"]["provider_effect_allowed"] is False
    assert plan["escalation"]["campaign_restart_allowed"] is False


def test_legacy_reclassification_rejects_a_tampered_causal_snapshot(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-106"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue)), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "b" * 64)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["terminal_reclassification"]["causal_facts"]["roles"][
        "implementer"
    ]["deterministic_failures"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.status(issue)


def test_f106_technical_remediation_leaves_f89_human_gate_byte_identical(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    _human_stop(store, "FOUNDRY-89", "implementer")
    f89_path = store._path("FOUNDRY-89")
    f89_before = f89_path.read_bytes()

    issue = "FOUNDRY-106"
    f106_path = store._path(issue)
    f106_path.parent.mkdir(parents=True, exist_ok=True)
    f106_path.write_text(json.dumps(_f106_legacy_ledger(issue)), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    resumed = store.resume_technical_remediation(issue, 1, "c" * 64)

    assert resumed["provider_effect_allowed"] is False
    assert resumed["campaign_restart_allowed"] is False
    assert f89_path.read_bytes() == f89_before
    f89 = store.status("FOUNDRY-89")
    assert f89["halted"] is True
    assert f89["human_required"] is True
    assert f89["technical_blocked"] is False


def test_technical_resume_is_atomic_and_idempotent_under_concurrency(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-106"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue)), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)

    args = [(str(tmp_path), str(tmp_path), issue, 1, "c" * 64)] * 2
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_technical_resume, args)

    assert {result["action"] for result in results} == {
        "technical_remediation_resumed",
    }
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert store.status(issue)["technical_remediation_audit"] == [{
        "code": "technical_remediation_resumed",
        "at": store.status(issue)["technical_remediation_audit"][0]["at"],
        "halt_generation": 1,
        "evidence_digest": "c" * 64,
        "role": "implementer",
        "route_id": None,
        "route_consumed_at": None,
            "review_diff_hash": None,
            "review_claimed_at": None,
            "review_rearm_audit": [],
        }]


def test_technical_route_claim_is_atomic_and_one_use_under_concurrency(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-106"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue)), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "c" * 64)

    args = [(str(tmp_path), str(tmp_path), issue, 1, "f106-route-0001")] * 2
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_technical_claim, args)

    assert {result["action"] for result in results} == {
        "technical_remediation_route_claimed",
    }
    assert sorted(result["replayed"] for result in results) == [False, True]
    audit = store.status(issue)["technical_remediation_audit"]
    assert audit[0]["route_id"] == "f106-route-0001"
    assert audit[0]["route_consumed_at"] is not None


def test_consumed_technical_route_opens_only_the_fresh_reviewer_floor(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-108"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue, "reviewer")), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "d" * 64)

    with pytest.raises(EscalationTechnicalBlockedError, match="explicitement demandée"):
        store.active_floor(issue, "reviewer")

    route_id = "f108-local-route-0001"
    store.claim_technical_remediation_route(
        issue, "reviewer", 1, route_id,
    )
    before_review_preflight = path.read_bytes()

    assert store.active_floor(issue, "reviewer") == "apex"
    assert path.read_bytes() == before_review_preflight
    for role in ("implementer", "scout", "coordinator", "architect"):
        with pytest.raises(
            EscalationTechnicalBlockedError, match="explicitement demandée",
        ):
            store.active_floor(issue, role)

    with pytest.raises(EscalationTechnicalBlockedError, match="nouvel identifiant"):
        store.claim_technical_remediation_route(
            issue, "reviewer", 1, "f108-local-route-0002",
        )
    with pytest.raises(EscalationTechnicalBlockedError, match="rôle arrêté"):
        store.claim_technical_remediation_route(
            issue, "implementer", 1, route_id,
        )

    status = store.status(issue)
    event = status["technical_remediation_audit"][0]
    assert event["halt_generation"] == 1
    assert event["route_id"] == route_id
    assert event["route_consumed_at"] is not None
    assert status["human_required"] is False
    assert status["technical_remediation_open"] is False


def test_consumed_reviewer_route_binds_exactly_one_reviewer_diff(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-108"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue, "reviewer")), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "d" * 64)
    store.claim_technical_remediation_route(
        issue, "reviewer", 1, "f108-local-route-0001",
    )

    first = "a" * 64
    assert store.claim_fresh_reviewer_authorization(
        issue, first, validated_claim=lambda: first,
    ) == first
    bound = store.status(issue)["technical_remediation_audit"][0]
    assert bound["review_diff_hash"] == first
    assert bound["review_claimed_at"] is not None
    state_after_first_claim = path.read_bytes()

    assert store.claim_fresh_reviewer_authorization(
        issue, first, validated_claim=lambda: first,
    ) == first
    assert path.read_bytes() == state_after_first_claim
    with pytest.raises(EscalationTechnicalBlockedError, match="déjà autorisé"):
        store.claim_fresh_reviewer_authorization(
            issue, "b" * 64, validated_claim=lambda: "b" * 64,
        )


def test_terminal_review_rearm_is_single_use_and_audited(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-130"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue, "reviewer")), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "d" * 64)
    store.claim_technical_remediation_route(
        issue, "reviewer", 1, "f130-local-route-0001",
    )
    first, second, proof_id = "a" * 64, "b" * 64, "c" * 64
    store.claim_fresh_reviewer_authorization(
        issue, first, validated_claim=lambda: first,
    )
    before = store.status(issue)["technical_remediation_audit"][0]

    assert store.claim_fresh_reviewer_authorization(
        issue,
        second,
        validated_claim=lambda: second,
        validated_rearm=lambda previous: (second, proof_id) if previous == first else None,
    ) == second
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] == second
    assert event["review_rearm_audit"] == [{
        "code": "technical_review_rearmed",
        "previous_diff_hash": first,
        "previous_claimed_at": before["review_claimed_at"],
        "terminal_proof_id": proof_id,
        "new_diff_hash": second,
        "rearmed_at": event["review_claimed_at"],
    }]
    with pytest.raises(EscalationTechnicalBlockedError, match="unique réarm"):
        store.claim_fresh_reviewer_authorization(
            issue, "d" * 64, validated_claim=lambda: "d" * 64,
            validated_rearm=lambda _previous: ("d" * 64, proof_id),
        )


def test_terminal_review_rearm_refuses_an_invalid_proof_without_mutation(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-130"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue, "reviewer")), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "d" * 64)
    store.claim_technical_remediation_route(
        issue, "reviewer", 1, "f130-local-route-0001",
    )
    store.claim_fresh_reviewer_authorization(
        issue, "a" * 64, validated_claim=lambda: "a" * 64,
    )
    before = path.read_bytes()
    with pytest.raises(RoutingConfigError, match="preuve terminale valide"):
        store.claim_fresh_reviewer_authorization(
            issue,
            "b" * 64,
            validated_claim=lambda: "b" * 64,
            validated_rearm=lambda _previous: ("b" * 64, "invalid"),
        )
    assert path.read_bytes() == before


def test_concurrent_reviewer_authorizations_bind_only_one_distinct_diff(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-108"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue, "reviewer")), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "d" * 64)
    store.claim_technical_remediation_route(
        issue, "reviewer", 1, "f108-local-route-0001",
    )

    args = (
            (str(tmp_path), str(tmp_path), issue, "a" * 64, None),
            (str(tmp_path), str(tmp_path), issue, "b" * 64, None),
    )
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_reviewer_authorization, args)

    assert sorted(kind for kind, _ in results) == ["authorized", "blocked"]
    assert "déjà autorisé" in next(message for kind, message in results if kind == "blocked")
    bound = store.status(issue)["technical_remediation_audit"][0]
    assert bound["review_diff_hash"] in {"a" * 64, "b" * 64}


def test_concurrent_terminal_review_rearms_bind_only_one_new_diff(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-130"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue, "reviewer")), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    store.resume_technical_remediation(issue, 1, "d" * 64)
    store.claim_technical_remediation_route(
        issue, "reviewer", 1, "f130-local-route-0001",
    )
    store.claim_fresh_reviewer_authorization(
        issue, "a" * 64, validated_claim=lambda: "a" * 64,
    )

    args = (
        (str(tmp_path), str(tmp_path), issue, "b" * 64, "c" * 64),
        (str(tmp_path), str(tmp_path), issue, "d" * 64, "c" * 64),
    )
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        results = pool.map(_concurrent_reviewer_authorization, args)

    assert sorted(kind for kind, _ in results) == ["authorized", "blocked"]
    assert "unique réarm" in next(message for kind, message in results if kind == "blocked")
    event = store.status(issue)["technical_remediation_audit"][0]
    assert event["review_diff_hash"] in {"b" * 64, "d" * 64}
    assert len(event["review_rearm_audit"]) == 1


@pytest.mark.parametrize("route_role", ["scout", "coordinator", "architect"])
def test_consumed_non_reviewer_route_keeps_reviewer_floor_closed(
    tmp_path, route_role,
):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-108"
    for tier in (
        "economy", "economy", "balanced", "balanced", "frontier", "frontier",
    ):
        store.record_failure(issue, route_role, "test_red", tier)
    generation = store.status(issue)["halt_generation"]
    store.resume_technical_remediation(issue, generation, "e" * 64)
    store.claim_technical_remediation_route(
        issue, route_role, generation, f"f108-{route_role}-route-0001",
    )

    with pytest.raises(
        EscalationTechnicalBlockedError, match="explicitement demandée",
    ):
        store.active_floor(issue, "reviewer")


def test_technical_resumes_are_bounded_per_fresh_deterministic_stop(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-106"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue)), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)

    for generation, digest in enumerate(("c" * 64, "d" * 64, "e" * 64), start=1):
        resumed = store.resume_technical_remediation(issue, generation, digest)
        claimed = store.claim_technical_remediation_route(
            issue, "implementer", generation, f"f112-route-{generation:04d}",
        )
        decision = store.record_failure(
            issue, "implementer", "review_blocking_after_fix", "apex",
        )
        assert resumed["human_required"] is False
        assert claimed["provider_effect_allowed"] is False
        assert claimed["campaign_restart_allowed"] is False
        assert decision.action == "technical_blocked"
        assert decision.human_required is False

    resumed = store.resume_technical_remediation(issue, 4, "f" * 64)
    claimed = store.claim_technical_remediation_route(
        issue, "implementer", 4, "f112-route-0004",
    )

    assert resumed["human_required"] is False
    assert resumed["provider_effect_allowed"] is False
    assert resumed["campaign_restart_allowed"] is False
    assert claimed["provider_effect_allowed"] is False
    assert claimed["campaign_restart_allowed"] is False
    status = store.status(issue)
    assert status["halt_generation"] == 4
    assert status["human_required"] is False
    assert status["technical_blocked"] is False
    assert [
        event["halt_generation"] for event in status["technical_remediation_audit"]
    ] == [1, 2, 3, 4]
    assert status["terminal_reclassification"]["halt_generation"] == 1

    before = path.read_bytes()
    with pytest.raises(EscalationTechnicalBlockedError, match="nouvel identifiant"):
        store.claim_technical_remediation_route(
            issue, "implementer", 4, "f112-route-duplicate",
        )
    with pytest.raises(EscalationTechnicalBlockedError, match="rôle arrêté"):
        store.claim_technical_remediation_route(
            issue, "reviewer", 4, "f112-route-0004",
        )
    assert path.read_bytes() == before

    decision = store.record_failure(
        issue, "implementer", "review_blocking_after_fix", "apex",
    )
    assert decision.action == "technical_blocked"
    assert store.status(issue)["halt_generation"] == 5
    with pytest.raises(RoutingConfigError, match="génération d'arrêt obsolète"):
        store.resume_technical_remediation(issue, 4, "a" * 64)

    payload = json.loads(path.read_text(encoding="utf-8"))
    duplicate = dict(payload["technical_remediation_audit"][-1])
    duplicate["at"] = "9999-12-31T23:59:59.999999Z"
    payload["technical_remediation_audit"].append(duplicate)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RoutingConfigError, match="état d'escalade invalide"):
        store.status(issue)


def test_legacy_stop_without_complete_causal_facts_stays_ambiguous(tmp_path):
    store = EscalationStore("owner/legacy-ambiguous", state_dir=tmp_path)
    issue = "FOUNDRY-108"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = _f106_legacy_ledger(issue)
    ledger.pop("halted_role")
    path.write_text(json.dumps(ledger), encoding="utf-8")
    before = path.read_bytes()

    result = store.reclassify_legacy_terminal(issue, 1)

    assert result["action"] == "authority_ambiguous"
    assert result["reclassified"] is False
    assert path.read_bytes() == before
    with pytest.raises(EscalationAuthorityAmbiguousError):
        store.active_floor(issue, "implementer")


def test_attested_durable_ambiguity_reclassifies_an_ambiguous_legacy_stop(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-108"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = _f106_legacy_ledger(issue)
    ledger.pop("halted_role")
    path.write_text(json.dumps(ledger), encoding="utf-8")

    verdict = store.record_human_verdict(
        issue, "architect", "apex", category="durable_ambiguity",
        diagnostic=BoundedDiagnostic(
            outcome="durable_ambiguity", attempts=3, maximum_attempts=3,
            evidence_digest="f" * 64,
        ),
    )

    assert verdict.action == "human_required"
    status = store.status(issue)
    assert status["halted"] is True
    assert status["human_required"] is True
    assert status["technical_blocked"] is False
    assert status["terminal_outcome"]["category"] == "durable_ambiguity"
    assert status["halt_generation"] == 1


def test_human_verdict_needs_a_typed_decision_or_bounded_ambiguity(tmp_path):
    store = EscalationStore("owner/human-verdict", state_dir=tmp_path)
    with pytest.raises(RoutingConfigError, match="catégorie de verdict humain"):
        store.record_human_verdict(
            "FOUNDRY-109", "implementer", "economy", category="model_tier",
        )
    with pytest.raises(RoutingConfigError, match="attestation de diagnostic borné"):
        store.record_human_verdict(
            "FOUNDRY-109", "implementer", "economy",
            category="durable_ambiguity",
            diagnostic=BoundedDiagnostic(
                outcome="durable_ambiguity", attempts=2, maximum_attempts=3,
                evidence_digest="a" * 64,
            ),
        )

    decision = store.record_human_verdict(
        "FOUNDRY-109", "implementer", "economy",
        category="durable_ambiguity",
        diagnostic=BoundedDiagnostic(
            outcome="durable_ambiguity", attempts=3, maximum_attempts=3,
            evidence_digest="a" * 64,
        ),
    )

    assert decision.action == "human_required"
    assert decision.authority_category == "durable_ambiguity"
    assert decision.human_required is True


def test_attested_durable_ambiguity_supersedes_a_technical_stop(tmp_path):
    store = EscalationStore("owner/technical-to-human", state_dir=tmp_path)
    issue = "FOUNDRY-111"
    for tier in (
        "economy", "economy", "balanced", "balanced", "frontier", "frontier",
    ):
        store.record_failure(issue, "implementer", "test_red", tier)
    technical = store.status(issue)
    assert technical["technical_blocked"] is True
    generation = technical["halt_generation"]

    verdict = store.record_human_verdict(
        issue, "architect", "apex", category="durable_ambiguity",
        diagnostic=BoundedDiagnostic(
            outcome="durable_ambiguity", attempts=3, maximum_attempts=3,
            evidence_digest="d" * 64,
        ),
    )

    assert verdict.action == "human_required"
    assert verdict.human_required is True
    status = store.status(issue)
    assert status["halt_generation"] == generation
    assert status["human_required"] is True
    assert status["technical_blocked"] is False
    assert status["terminal_outcome"]["category"] == "durable_ambiguity"


def test_attested_ambiguity_preserves_a_reclassified_legacy_snapshot(tmp_path):
    store = EscalationStore.for_root(tmp_path, state_dir=tmp_path)
    issue = "FOUNDRY-106"
    path = store._path(issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger(issue)), encoding="utf-8")
    store.reclassify_legacy_terminal(issue, 1)
    before = json.loads(path.read_text(encoding="utf-8"))["terminal_reclassification"]

    store.record_human_verdict(
        issue, "architect", "apex", category="durable_ambiguity",
        diagnostic=BoundedDiagnostic(
            outcome="durable_ambiguity", attempts=3, maximum_attempts=3,
            evidence_digest="e" * 64,
        ),
    )

    status = store.status(issue)
    assert status["human_required"] is True
    assert status["terminal_reclassification"]["causal_digest"] == (
        before["causal_digest"]
    )


def test_cli_separates_human_verdict_from_legacy_technical_reclassification(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setenv("FOUNDRY_DATA", str(tmp_path / "state"))
    with pytest.raises(SystemExit) as stopped:
        main([
            "escalation", "verdict", "FOUNDRY-110", "implementer",
            "--current-tier", "economy", "--category", "strategy_decision",
            "--root", str(tmp_path),
        ])
    assert stopped.value.code == 2
    assert json.loads(capsys.readouterr().out)["human_required"] is True

    store = EscalationStore.for_root(tmp_path)
    path = store._path("FOUNDRY-106")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_f106_legacy_ledger("FOUNDRY-106")), encoding="utf-8")
    main([
        "escalation", "reclassify-legacy-terminal", "FOUNDRY-106",
        "--halt-generation", "1", "--root", str(tmp_path),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "technical_blocked"
    assert payload["human_required"] is False
    main([
        "escalation", "resume-technical", "FOUNDRY-106",
        "--halt-generation", "1", "--diagnostic-digest", "b" * 64,
        "--root", str(tmp_path),
    ])
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "technical_remediation_resumed"
    assert payload["human_required"] is False
    assert payload["provider_effect_allowed"] is False
    assert payload["campaign_restart_allowed"] is False
