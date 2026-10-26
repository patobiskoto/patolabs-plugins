"""Offline lifecycle witness for authenticated technical-diagnostic completion."""
import hashlib
import json
import runpy
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from foundry.escalation import (
    EscalationHumanRequiredError,
    EscalationStore,
    EscalationTechnicalBlockedError,
)
from foundry.routing import (
    AcceptanceProofStore,
    ReviewDeduplicator,
    RoutingConfigError,
    acceptance_criteria,
    git_diff,
    git_head,
    repository_identity,
    review_diff_hash,
)
from foundry.routing_facades import codex_review_plan, codex_spawn_plan


ISSUE = "TEST-312"
BODY = "- [ ] Correction is reviewed.\n- [ ] Remaining qualification is observed.\n"
PACKET = (
    "Goal:\nPrepare the remaining qualification.\n"
    "Inputs:\nCurrent issue and reviewed candidate.\n"
    "Constraints:\nPreparation only; external effects require their own authority.\n"
    "Done when:\nReturn the bounded qualification procedure."
)
ROUTE_ID = "technical-diagnostic-0001"


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _commit(root, text):
    (root / "candidate.txt").write_text(text)
    _git(root, "add", "candidate.txt")
    _git(root, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--no-gpg-sign", "-m", "synthetic candidate")


@pytest.fixture
def case(tmp_path, monkeypatch, request):
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "remote", "add", "origin", "https://example.invalid/fixture/repository.git")
    _commit(root, "base\n")
    base = git_head(root)
    _commit(root, "corrected\n")
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FOUNDRY_DATA", str(state_dir))
    current = SimpleNamespace(id=ISSUE, body=BODY)
    reads = []

    class Tracker:
        def resolve_checkout_project(self, cwd):
            assert str(cwd) == str(root)
            reads.append("project")

        def observe_issue(self, issue):
            assert issue == ISSUE
            reads.append("issue")
            return current

    monkeypatch.setattr("foundry.tracker", lambda **_kwargs: Tracker())
    store = EscalationStore.for_root(root, state_dir=state_dir)
    role = getattr(request, "param", "implementer")
    for tier in ("economy", "economy", "balanced", "balanced", "frontier", "frontier"):
        store.record_failure(ISSUE, role, "test_red", tier)
    generation = store.status(ISSUE)["halt_generation"]
    store.resume_technical_remediation(ISSUE, generation, "d" * 64)
    store.claim_technical_remediation_route(ISSUE, role, generation, ROUTE_ID)
    plan = codex_review_plan(
        PACKET, root=root, base=base, state_dir=state_dir, issue_id=ISSUE,
        claim_attempt_token="a" * 64,
    )
    claim = plan["review_claim"]
    proofs = AcceptanceProofStore(repository_identity(root), state_dir)
    result = proofs.create(
        issue_id=ISSUE, issue_body=BODY, reviewer_role="reviewer",
        outcomes=[{**criterion, "verdict": verdict} for criterion, verdict in zip(
            acceptance_criteria(BODY), ("pass", "not_covered"), strict=True,
        )],
        quality="mergeable", diff_hash=claim["diff_hash"], claim_id=claim["claim_id"],
        root=root, base=base, state_dir=state_dir,
    )
    ledger = ReviewDeduplicator(repository_identity(root), state_dir)
    return SimpleNamespace(
        root=root, base=base, state_dir=state_dir, store=store, generation=generation,
        current=current, reads=reads, proofs=proofs, proof=result, ledger=ledger,
        claim=claim, role=role,
    )


def _complete(case):
    return case.store.complete_technical_remediation(
        ISSUE, case.role, case.generation, root=case.root, base=case.base,
    )


def _prepare(case):
    return codex_spawn_plan(
        "implementer", PACKET, root=case.root, issue_id=ISSUE,
        escalation_state_dir=case.state_dir,
    )


def _cli(monkeypatch, *args):
    launcher = Path(__file__).resolve().parents[1] / "tooling" / "foundry_cli.py"
    monkeypatch.setattr(sys, "argv", [str(launcher), "routing", *args])
    runpy.run_path(str(launcher), run_name="__main__")


def test_completion_reopens_preparation_without_accepting_uncovered_criteria(
    case, capsys, monkeypatch,
):
    before = case.store.status(ISSUE)
    proof_path = case.proofs.directory / case.proof["proof_id"]
    proof_bytes = proof_path.read_bytes()
    claim_bytes = (case.ledger.directory / case.claim["diff_hash"]).read_bytes()
    with pytest.raises(EscalationTechnicalBlockedError, match="ordinaire refusée"):
        _prepare(case)

    _cli(monkeypatch, "escalation", "show", ISSUE, "--root", str(case.root))
    shown = json.loads(capsys.readouterr().out)
    assert shown["halt_generation"] == case.generation
    _cli(monkeypatch,
        "escalation", "complete-technical", ISSUE, "implementer",
        "--halt-generation", str(shown["halt_generation"]), "--root", str(case.root),
        "--base", case.base,
    )
    completed = json.loads(capsys.readouterr().out)
    assert completed["action"] == "technical_remediation_completed"
    assert completed["replayed"] is False
    assert completed["provider_effect_allowed"] is False
    assert completed["campaign_restart_allowed"] is False
    assert completed["product_accepted"] is False
    assert completed["not_covered"] == ["ac-2"]
    assert case.reads == ["project", "issue"]
    after = case.store.status(ISSUE)
    assert {key: value for key, value in after.items()
            if key != "technical_completion_audit"} == {
        key: value for key, value in before.items() if key != "technical_completion_audit"
    }
    assert len(after["technical_completion_audit"]) == 1
    assert proof_path.read_bytes() == proof_bytes
    assert (case.ledger.directory / case.claim["diff_hash"]).read_bytes() == claim_bytes
    with pytest.raises(RoutingConfigError, match="incomplète"):
        case.proofs.valid_for_merge(
            issue_id=ISSUE, issue_body=BODY, head=git_head(case.root),
            diff=git_diff(case.root, case.base), base=case.base,
        )
    packet = case.root.parent / "qualification-packet.txt"
    packet.write_text(PACKET)
    _cli(monkeypatch, "codex-plan", "implementer", "--issue", ISSUE,
         "--root", str(case.root), "--packet-file", str(packet))
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["route"]["selected_tier"] == before["roles"]["implementer"]["minimum_tier"]
    assert _complete(case)["replayed"] is True

    duplicate = codex_review_plan(
        PACKET, root=case.root, base=case.base, state_dir=case.state_dir, issue_id=ISSUE,
        claim_attempt_token="b" * 64,
    )
    assert duplicate["mode"] == "deduplicated"
    _commit(case.root, "qualified candidate\n")
    fresh = codex_review_plan(
        PACKET, root=case.root, base=case.base, state_dir=case.state_dir, issue_id=ISSUE,
        claim_attempt_token="c" * 64,
    )
    assert fresh["review_claim"]["should_run"] is True
    assert fresh["review_claim"]["diff_hash"] != case.claim["diff_hash"]
    assert fresh["review_claim"]["claim_id"] != case.claim["claim_id"]
    assert fresh["review_claim"]["diff_hash"] == review_diff_hash(git_diff(case.root, case.base))


def _state_bytes(case):
    return {str(path.relative_to(case.state_dir)): path.read_bytes()
            for path in case.state_dir.rglob("*") if path.is_file()}


def _rewrite_proof(case, mutate):
    """Model another canonical terminal verdict only inside the synthetic store."""
    path = case.proofs.directory / case.proof["proof_id"]
    proof = json.loads(path.read_text())
    mutate(proof)
    proof.pop("proof_id")
    proof["proof_id"] = hashlib.sha256(json.dumps(
        proof, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    path.unlink()
    (case.proofs.directory / proof["proof_id"]).write_text(json.dumps(proof))
    marker = case.ledger.directory / case.claim["diff_hash"]
    record = json.loads(marker.read_text())
    record["acceptance_proof_id"] = proof["proof_id"]
    marker.write_text(json.dumps(record))


@pytest.mark.parametrize("damage", [
    "missing_proof", "altered_proof", "missing_claim", "in_progress", "missing_head",
    "review_generation", "claim_digest", "claim_root", "claim_base", "claim_head",
    "proof_issue", "proof_role", "proof_ac_digest", "proof_ac_item", "blocked",
    "fail", "contradicted", "before_binding", "malformed_claim", "malformed_escalation",
])
def test_completion_refuses_untrusted_terminal_evidence_without_effect(case, damage):
    marker = case.ledger.directory / case.claim["diff_hash"]
    proof_path = case.proofs.directory / case.proof["proof_id"]
    if damage == "missing_proof":
        proof_path.unlink()
    elif damage == "altered_proof":
        proof_path.write_text(proof_path.read_text().replace('"mergeable"', '"blocked"'))
    elif damage == "missing_claim":
        marker.unlink()
    elif damage == "malformed_claim":
        marker.write_text("[]")
    elif damage == "malformed_escalation":
        case.store._path(ISSUE).write_text('{"version":1,"version":1}')
    elif damage in {"blocked", "fail", "contradicted", "proof_issue", "proof_role",
                    "proof_ac_digest", "proof_ac_item"}:
        def change(proof):
            if damage == "blocked":
                proof["quality"] = "blocked"
            elif damage in {"fail", "contradicted"}:
                proof["issue"]["criteria"][1]["verdict"] = damage
            elif damage == "proof_issue":
                proof["issue"]["id"] = "TEST-313"
            elif damage == "proof_role":
                proof["review"]["role"] = "implementer"
            elif damage == "proof_ac_digest":
                proof["issue"]["ac_digest"] = "e" * 64
            else:
                proof["issue"]["criteria"][1]["digest"] = "e" * 64
        _rewrite_proof(case, change)
    else:
        record = json.loads(marker.read_text())
        if damage == "in_progress":
            record["state"] = "in_progress"
        elif damage == "missing_head":
            record.pop("head")
        elif damage == "review_generation":
            record["generation"] += 1
        elif damage == "claim_digest":
            record["claim"]["id"] = "e" * 64
        elif damage == "claim_root":
            record["coordinates"]["root"] += "-other"
        elif damage == "claim_base":
            record["coordinates"]["base"] = "e" * 40
        elif damage == "claim_head":
            record["head"] = "e" * 40
        else:
            record["completed_at"] = "2000-01-01T00:00:00Z"
        marker.write_text(json.dumps(record))
    before = _state_bytes(case)
    with pytest.raises(RoutingConfigError):
        _complete(case)
    assert _state_bytes(case) == before


@pytest.mark.parametrize("drift", [
    "issue", "role", "generation", "root", "base", "repository", "head", "diff",
    "criteria", "criteria_order", "native_issue", "native_error", "human_pause",
])
def test_completion_refuses_stale_or_foreign_context_without_effect(case, monkeypatch, drift):
    kwargs = {"root": case.root, "base": case.base}
    issue, role, generation = ISSUE, "implementer", case.generation
    if drift == "issue":
        issue = "TEST-313"
    elif drift == "role":
        role = "reviewer"
    elif drift == "generation":
        generation += 1
    elif drift == "root":
        sibling = case.root.parent / "sibling"
        _git(case.root, "worktree", "add", "--detach", str(sibling), "HEAD")
        kwargs["root"] = sibling
    elif drift == "base":
        kwargs["base"] = git_head(case.root)
    elif drift == "repository":
        _git(case.root, "remote", "set-url", "origin", "https://example.invalid/fixture/other.git")
    elif drift == "head":
        _git(case.root, "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "--no-gpg-sign", "--allow-empty", "-m", "same diff, another head")
    elif drift == "diff":
        monkeypatch.setattr("foundry.routing.git_diff", lambda *_args, **_kwargs: b"other diff")
    elif drift == "criteria":
        case.current.body += "- [ ] Additional qualification.\n"
    elif drift == "criteria_order":
        case.current.body = "\n".join(reversed(BODY.splitlines()))
    elif drift == "native_issue":
        case.current.id = "TEST-313"
    elif drift == "native_error":
        def unavailable(**_kwargs):
            raise RoutingConfigError("native issue unavailable")
        monkeypatch.setattr("foundry.tracker", unavailable)
    else:
        case.store.record_human_verdict(
            ISSUE, "implementer", "apex", category="strategy_decision",
        )
    before = _state_bytes(case)
    with pytest.raises((RoutingConfigError, EscalationTechnicalBlockedError)):
        case.store.complete_technical_remediation(issue, role, generation, **kwargs)
    assert _state_bytes(case) == before


def test_completion_rechecks_git_after_native_read_inside_issue_lock(case, monkeypatch):
    def drift():
        _commit(case.root, "changed during observation\n")
        return case.current
    monkeypatch.setattr("foundry.tracker", lambda **_kwargs: SimpleNamespace(
        resolve_checkout_project=lambda _root: None,
        observe_issue=lambda _issue: drift(),
    ))
    before = _state_bytes(case)
    with pytest.raises(RoutingConfigError, match="périmé"):
        _complete(case)
    assert _state_bytes(case) == before


@pytest.mark.parametrize("damage", ["proof", "criteria", "head", "generation"])
def test_completion_replay_reauthenticates_current_evidence(case, damage):
    _complete(case)
    if damage == "proof":
        (case.proofs.directory / case.proof["proof_id"]).unlink()
    elif damage == "criteria":
        case.current.body = "- [ ] Changed criterion.\n"
    elif damage == "head":
        _commit(case.root, "changed after completion\n")
    else:
        case.store.record_failure(ISSUE, "implementer", "test_red", "apex")
    before = _state_bytes(case)
    with pytest.raises((RoutingConfigError, EscalationTechnicalBlockedError)):
        _complete(case)
    assert _state_bytes(case) == before


def test_completion_is_one_cas_transition_under_concurrent_replay(case):
    barrier = threading.Barrier(4)
    def complete(_index):
        barrier.wait(timeout=10)
        return _complete(case)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(complete, range(4)))
    assert sum(not result["replayed"] for result in results) == 1
    assert len(case.store.status(ISSUE)["technical_completion_audit"]) == 1


def test_completion_cannot_release_a_concurrent_human_pause(case, monkeypatch):
    reading = threading.Event()
    release = threading.Event()
    original = ReviewDeduplicator.validated_technical_completion
    def paused_read(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        reading.set()
        assert release.wait(timeout=10)
        return result
    monkeypatch.setattr(ReviewDeduplicator, "validated_technical_completion", paused_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        completion = pool.submit(_complete, case)
        assert reading.wait(timeout=10)
        pause = pool.submit(
            case.store.record_human_verdict, ISSUE, "implementer", "apex",
            category="strategy_decision",
        )
        release.set()
        assert completion.result(timeout=10)["replayed"] is False
        assert pause.result(timeout=10).human_required is True
    assert case.store.status(ISSUE)["human_required"] is True
    with pytest.raises(EscalationHumanRequiredError, match="décision humaine"):
        _prepare(case)


@pytest.mark.parametrize("case", ["reviewer"], indirect=True)
def test_completed_reviewer_diagnostic_releases_preparation_with_its_floor_intact(case):
    before = case.store.status(ISSUE)
    with pytest.raises(EscalationTechnicalBlockedError):
        _prepare(case)
    assert _complete(case)["role"] == "reviewer"
    assert _prepare(case)["mode"] == "subagent"
    assert case.store.status(ISSUE)["roles"] == before["roles"]


def test_completed_route_cannot_reopen_or_follow_the_next_generation(case):
    _complete(case)
    before = case.store._path(ISSUE).read_bytes()
    assert case.store.resume_technical_remediation(
        ISSUE, case.generation, "d" * 64,
    )["replayed"] is True
    for operation in (
        lambda: case.store.claim_technical_remediation_route(
            ISSUE, "implementer", case.generation, ROUTE_ID,
        ),
        lambda: case.store.active_floor(
            ISSUE, "implementer", technical_remediation=True, technical_remediation_id=ROUTE_ID,
        ),
    ):
        with pytest.raises(EscalationTechnicalBlockedError):
            operation()
    assert case.store._path(ISSUE).read_bytes() == before
    case.store.record_failure(ISSUE, "implementer", "test_red", "apex")
    generation = case.store.status(ISSUE)["halt_generation"]
    assert generation == case.generation + 1
    case.store.resume_technical_remediation(ISSUE, generation, "e" * 64)
    with pytest.raises(EscalationTechnicalBlockedError):
        _prepare(case)
    with pytest.raises(EscalationTechnicalBlockedError):
        case.store.claim_technical_remediation_route(
            ISSUE, "implementer", case.generation, ROUTE_ID,
        )
    case.store.claim_technical_remediation_route(
        ISSUE, "implementer", generation, "next-diagnostic-0001",
    )
    with pytest.raises(RoutingConfigError):
        case.store.complete_technical_remediation(
            ISSUE, "implementer", generation, root=case.root, base=case.base,
        )
    assert len(case.store.status(ISSUE)["technical_completion_audit"]) == 1


@pytest.mark.parametrize("damage", [
    "duplicate", "role", "generation", "diff", "repository", "time", "unknown_key",
])
def test_malformed_completion_ledger_fails_closed_before_preparation(case, damage):
    _complete(case)
    path = case.store._path(ISSUE)
    state = json.loads(path.read_text())
    completion = state["technical_completion_audit"][0]
    if damage == "duplicate":
        state["technical_completion_audit"].append(completion)
    elif damage == "role":
        completion["role"] = "reviewer"
    elif damage == "generation":
        completion["halt_generation"] += 1
    elif damage == "diff":
        completion["proof"]["diff_hash"] = "f" * 64
    elif damage == "repository":
        completion["proof"]["repository_digest"] = "f" * 64
    elif damage == "time":
        completion["at"] = completion["proof"]["completed_at"]
    else:
        completion["provider_effect_allowed"] = True
    path.write_text(json.dumps(state))
    before = _state_bytes(case)
    with pytest.raises(RoutingConfigError):
        _prepare(case)
    with pytest.raises(RoutingConfigError):
        _complete(case)
    assert _state_bytes(case) == before


def test_completion_receipt_never_supplies_campaign_authority(case):
    from foundry import campaign_approval

    result = _complete(case)
    before = _state_bytes(case)
    with pytest.raises(campaign_approval.ApprovalAbsent):
        campaign_approval.check(ISSUE, result["proof_id"], "a" * 64)
    assert _state_bytes(case) == before
    assert not campaign_approval.journal_path().exists()
    with pytest.raises(TypeError):
        case.store.complete_technical_remediation(
            ISSUE, "implementer", case.generation, root=case.root, base=case.base,
            proof_id=result["proof_id"],
        )
