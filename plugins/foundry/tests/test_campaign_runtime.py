from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from foundry.campaign_gate import main as campaign_gate_main
from foundry.campaign_coordinator import (
    AttemptProfile,
    BlockerObservation,
    CampaignCoordinator,
    CampaignError,
    CampaignObservation,
    CampaignOutcome,
    CampaignRevalidationError,
    CampaignSpec,
    CampaignStore,
    EffectEnvelope,
    ImplementationProposal,
    ImplementationReconciliation,
    ParentAcceptanceProof,
    ParentSnapshotAdvancement,
    ReviewRemediationTarget,
    StepReceipt,
    _digest,
)
from foundry.campaign_retry import main as campaign_retry_main
from foundry.campaign_review_remediation import (
    main as campaign_review_remediation_main,
)
from foundry.campaign_runtime import (
    CampaignCommandEffectProvider,
    CampaignEffectStore,
    FoundryPrimitiveRunner,
    IsolatedClaudeIssueExecutor,
    _child_environment,
)
from foundry.command_runtime import AUTHORITY_CONTRACT, FileAuthoritySource
from foundry.command_worker import (
    RECEIPT_CONTRACT,
    CommandWorker,
    CommandWorkerError,
    EffectReceipt,
    ExecutionAuthorization,
    PreEffectCapacityError,
    RevalidationObservation,
    ReceiptStore,
    _binding_digest,
)
from foundry.devhub_commands import (
    CommandLease,
    CommandPage,
    DevHubCommand,
    DevHubCommandError,
)
from foundry.escalation import EscalationStore
from foundry.execution_receipts import (
    ATTEMPT_ID_ENV,
    RECEIPT_DIRECTORY_ENV,
    ExecutionReceiptStore,
    epic_closure_receipt,
)
from foundry.models import (
    EpicClosureChild,
    EpicClosureOutcome,
    EpicClosureReceipt,
    Project,
    PullRequest,
)
from foundry.routing import RoutingConfigError, acceptance_criteria
from foundry.trackers.devhub import DevHubTrackerError


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
HEAD = "1" * 40
BASE = "2" * 40


def test_native_attempt_matches_devhub_late_publication_conformance_vector():
    item = replace(
        command(), id="command_1", preview_id="preview_1", approval_id="approval_1",
        epic_id="DEVHUB-33", max_cost_cents=5000, max_concurrency=2,
        created_at=1788540000000,
    )
    binding = _binding_digest(item)
    assert binding == "028480159a7dfcb38786ce9e90bc8948a832433b52d281facddce9b9ae376aec"
    assert CampaignCommandEffectProvider._attempt_id(item, binding) == "attempt-9365a6fd52ed0e800fa8d8ac968c4323"


def command() -> DevHubCommand:
    return DevHubCommand(
        id="command-1", project="DEVHUB", preview_id="preview-1",
        approval_id="approval-1", preview_digest=DIGEST_A,
        snapshot_digest=DIGEST_B, policy_digest=DIGEST_C,
        epic_id="DEVHUB-20", planning_version_id="DEVHUB-VERSION-1",
        max_cost_cents=500, max_concurrency=4, state="requested",
        projection_stale=False, next_event_sequence=1, scheduled_for=None,
        lease=None, version=3, created_at=100, updated_at=100,
    )


def observation() -> RevalidationObservation:
    return RevalidationObservation(
        command_id="command-1", project="DEVHUB",
        preview_id="preview-1", approval_id="approval-1",
        approval_state="approved", epic_id="DEVHUB-20",
        epic_version=4,
        planning_version_id="DEVHUB-VERSION-1",
        preview_digest=DIGEST_A, snapshot_digest=DIGEST_B,
        policy_digest=DIGEST_C, preview_expires_at=20_000,
        approval_expires_at=19_000, approved_minimum_tier="balanced",
        current_minimum_tier="frontier", local_max_cost_cents=400,
        local_max_concurrency=3, budget_remaining_cents=300,
        active_concurrency=1, observed_at=9_000, valid_until=15_000,
        host_available=True,
        required_gates=frozenset({"tests", "independent-review", "human-test"}),
        provider_invocation_ceiling_cents=150,
    )


class FakeSource:
    def __init__(self, *, blockers=()):
        self.now_ms = lambda: 10_000
        self.blockers = blockers

    def load(self, _command):
        issue_id = "DEVHUB-21"
        return SimpleNamespace(
            observation=observation(), waves=((issue_id,),),
            blockers=self.blockers,
            acceptance_mapping=(("ac-1", _digest(["criterion", 1]), issue_id),),
        )


class BudgetCapacitySource(FakeSource):
    def __init__(self, remaining=2_000):
        super().__init__()
        self.remaining = remaining

    def load(self, _command):
        return SimpleNamespace(
            observation=replace(
                observation(), local_max_cost_cents=2_000,
                budget_remaining_cents=self.remaining, local_max_concurrency=2,
                active_concurrency=0,
                provider_invocation_ceiling_cents=1_000,
            ),
            waves=(("DEVHUB-21",),), blockers=(),
            acceptance_mapping=(("ac-1", _digest(["criterion", 1]), "DEVHUB-21"),),
        )


def file_authority(tmp_path, *, command_id="command-1", budget_remaining=500):
    policy = {
        "implementer_minimum_tier": "balanced", "source": "approved-policy",
    }
    policy_digest = _digest(policy)
    plan = {
        "waves": [{"issue_ids": ["DEVHUB-21", "DEVHUB-22"]}],
        "blockers": [],
        "required_gates": ["tests", "independent-review", "human-test"],
        "acceptance_mapping": [
            {
                "criterion_id": "ac-1", "criterion_digest": "1" * 64,
                "issue_id": "DEVHUB-21",
            },
            {
                "criterion_id": "ac-2", "criterion_digest": "2" * 64,
                "issue_id": "DEVHUB-22",
            },
        ],
    }
    preview = {
        "actor": "foundry-service", "epic_version": 7,
        "expires_at": 20_000,
        "limits": {"max_cost_cents": 500, "max_concurrency": 1},
        "plan": plan,
    }
    preview_digest = _digest({
        "contract": "devhub-foundry-command.v1", "project": "DEVHUB",
        "actor": preview["actor"], "intent": {"type": "execute-epic"},
        "epic_id": "DEVHUB-20", "epic_version": preview["epic_version"],
        "planning_version_id": "DEVHUB-VERSION-1",
        "snapshot_digest": DIGEST_B, "policy_digest": policy_digest,
        "expires_at": preview["expires_at"], "limits": preview["limits"],
        "plan": plan,
    })
    item = replace(
        command(), id=command_id, preview_digest=preview_digest,
        policy_digest=policy_digest, max_cost_cents=500, max_concurrency=1,
    )
    document = {
        "contract": AUTHORITY_CONTRACT,
        "command": {
            "id": item.id, "project": item.project,
            "preview_id": item.preview_id, "approval_id": item.approval_id,
            "epic_id": item.epic_id,
            "planning_version_id": item.planning_version_id,
            "snapshot_digest": item.snapshot_digest,
            "policy_digest": item.policy_digest,
            "scheduled_for": item.scheduled_for, "created_at": item.created_at,
        },
        "preview": preview,
        "approval": {
            "id": item.approval_id, "preview_id": item.preview_id,
            "state": "approved", "expires_at": 19_000,
        },
        "policy": policy,
        "local": {
            "observed_at": 9_000, "valid_until": 15_000,
            "minimum_tier": "frontier", "max_cost_cents": 500,
            "max_concurrency": 1,
            "budget_remaining_cents": budget_remaining,
            "active_concurrency": 0, "host_available": True,
            "provider_invocation_ceiling_cents": 300,
        },
    }
    directory = tmp_path / "authority"
    directory.mkdir(exist_ok=True)
    path = directory / f"{item.id}.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return item, document, path


class FakeEscalationStore:
    def record_failure(self, *_args, **_kwargs):
        raise AssertionError("no retry expected")


class FakePrimitives:
    instances = []
    blocker_states = {}
    blocker_observation_versions = {}
    blocker_observation_states = {}

    def __init__(self, _root, *, receipts, **_kwargs):
        self.receipts = receipts
        self.calls = []
        self.instances.append(self)

    def resolve_effect(self, envelope, *, engage_merge=None):
        return self.receipts.get(envelope)

    def observe_blockers(self, spec):
        observations = []
        for issue_id in spec.blockers:
            state = self.blocker_states.get(issue_id, "blocked")
            if self.blocker_observation_states.get(issue_id) != state:
                self.blocker_observation_states[issue_id] = state
                self.blocker_observation_versions[issue_id] = (
                    self.blocker_observation_versions.get(issue_id, 0) + 1
                )
            version = self.blocker_observation_versions[issue_id]
            observations.append(BlockerObservation(
                issue_id, state, 10_000 + version, version,
            ))
        return tuple(observations)

    def _complete(self, envelope):
        self.calls.append((envelope.issue_id, envelope.step))
        receipt = StepReceipt(
            envelope.effect_id, "completed", _digest([envelope.effect_id, envelope.step]),
        )
        return self.receipts.record(envelope, receipt, now_ms=10_000)

    start_issue = _complete
    open_pr = _complete
    review = _complete
    ci_gate = _complete
    human_gate = _complete
    close_epic = _complete

    def merge_issue(self, envelope, *, engage=None):
        if engage is not None:
            engage()
        self.calls.append((envelope.issue_id, envelope.step))
        receipt = StepReceipt(
            envelope.effect_id, "completed",
            _digest([envelope.effect_id, 1, HEAD]),
        )
        return self.receipts.record(
            envelope, receipt, pr_number=1, head_sha=HEAD, base_sha=BASE,
            now_ms=10_000,
        )

    def prepare_parent_acceptance(self, envelope, proof):
        receipts = {item[0]: item[1:] for item in proof.children}
        criteria = tuple(
            (criterion_id, criterion_digest, issue_id, *receipts[issue_id])
            for criterion_id, criterion_digest, issue_id in proof.mapping
        )
        return ParentSnapshotAdvancement(
            proof.campaign_id, proof.parent_id, envelope.effect_id,
            proof.binding_digest, proof.snapshot_digest, proof.proof_id,
            proof.epic_version, proof.epic_version + 1,
            _digest(["body", "before"]), _digest(["body", "after"]),
            _digest(["ac", "before"]), _digest(["ac", "after"]), criteria,
        )

    def sync_parent_acceptance(self, envelope, proof, advancement):
        assert advancement.proof_id == proof.proof_id
        return self._complete(envelope)

    def reconcile_parent_acceptance(self, envelope, advancement):
        return self.receipts.get(envelope)

    def reconcile_close_epic(self, envelope, advancement):
        return self.receipts.get(envelope)

    def verify_parent_snapshot_advancement(self, _campaign, **_kwargs):
        return None


class FakeExecutor:
    def __init__(self, _root, _primitives, *, selected_tier, cost_ceiling_cents, **_kwargs):
        self.selected_tier = selected_tier
        self.cost_ceiling_cents = cost_ceiling_cents
        self.proposals = {}

    def execution_profile(self, _issue_id, _attempt):
        from foundry.campaign_coordinator import AttemptProfile
        return AttemptProfile(self.selected_tier, self.cost_ceiling_cents)

    def resolve(self, work_id):
        return self.proposals.get(work_id)

    def execute(self, envelope):
        proposal = ImplementationProposal(
            envelope.work_id, envelope.issue_id, envelope.attempt, "completed",
            _digest([envelope.work_id, "proposal"]), 30, envelope.selected_tier,
        )
        self.proposals[envelope.work_id] = proposal
        return proposal


class FakeRouting:
    def resolve(self, *_args, **_kwargs):
        return SimpleNamespace(
            selected_tier="frontier", model="opus-5", effort="high",
        )


def seed_completed_merge_evidence(spec, coordinator_store, effects):
    children = []
    for pr_number, issue_id in enumerate(
        sorted(issue for wave in spec.waves for issue in wave), start=1,
    ):
        effect_id, work_id = CampaignCoordinator._identity(
            spec, issue_id, 1, "merge",
        )
        envelope = EffectEnvelope(
            spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
            issue_id, 1, "merge", effect_id, work_id, "lease-merge-evidence",
            20_000, spec.binding_digest, spec.snapshot_digest,
            spec.policy_digest, None, 0,
        )
        receipt = StepReceipt(
            effect_id, "completed", _digest([effect_id, pr_number, HEAD]),
        )
        coordinator_store.begin_effect(
            envelope, selected_tier=None, cost_ceiling=0,
        )
        coordinator_store.finish_effect(
            envelope, status="completed", proof_digest=receipt.proof_digest,
        )
        coordinator_store.advance_issue(
            spec.campaign_id, issue_id, next_step="done", state="merged",
            engaged_merge=False, merge_dispatch_started=False,
        )
        effects.record(
            envelope, receipt, pr_number=pr_number, head_sha=HEAD,
            base_sha=BASE, now_ms=10_000,
        )
        children.append((issue_id, effect_id, receipt.proof_digest))
    return tuple(children)


def test_start_issue_serializes_only_the_start_primitive(tmp_path):
    """Sibling starts do not race, while later wave work remains unconstrained."""

    class Receipts:
        def record(self, _envelope, receipt, **_kwargs):
            return receipt

    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=Receipts(),
    )
    runner.worktree = lambda _campaign_id, issue_id: tmp_path / issue_id
    runner._git = lambda *_args, **_kwargs: "fix/campaign-child"
    active = 0
    peak = 0
    lock = threading.Lock()
    first_entered = threading.Event()
    release_first = threading.Event()

    def issue_command(envelope, action, *_extra, **_kwargs):
        nonlocal active, peak
        assert action == "start"
        with lock:
            active += 1
            peak = max(peak, active)
            first_entered.set()
        release_first.wait(timeout=2)
        with lock:
            active -= 1

    runner._issue_command = issue_command

    def envelope(issue_id):
        return EffectEnvelope(
            "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", issue_id, 1,
            "start", f"effect-{issue_id.lower()}", f"work-{issue_id.lower()}",
            "lease-1", 20_000, DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
        )

    first = threading.Thread(target=runner.start_issue, args=(envelope("DEVHUB-21"),))
    second = threading.Thread(target=runner.start_issue, args=(envelope("DEVHUB-22"),))
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    time.sleep(0.05)
    assert peak == 1
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert peak == 1


def test_campaign_primitive_binds_f91_to_its_explicit_attempt(tmp_path, monkeypatch):
    calls = []

    def invoke(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    execution_receipts = ExecutionReceiptStore(tmp_path / "execution-receipts")
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees",
        receipts=CampaignEffectStore(tmp_path / "effects.sqlite3"),
        runner=invoke, execution_receipts=execution_receipts,
        attempt_id="attempt-command-1",
    )
    monkeypatch.setenv(ATTEMPT_ID_ENV, "ambient-attempt")
    monkeypatch.setenv(RECEIPT_DIRECTORY_ENV, str(tmp_path / "ambient-receipts"))
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-20", 1,
        "close-epic", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )

    runner._issue_command(envelope, "close-epic")

    environment = calls[0][1]["env"]
    assert environment[ATTEMPT_ID_ENV] == "attempt-command-1"
    assert environment[RECEIPT_DIRECTORY_ENV] == str(execution_receipts.directory)


def test_command_provider_runs_the_real_named_campaign_composition(tmp_path):
    FakePrimitives.instances.clear()
    item = command()
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(), root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=FakeExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        reconciled = []
        effect = provider.launch(
            item, authorization, effect_id=item.id,
            heartbeat=lambda: item,
            reconcile_capacity=lambda observed: reconciled.append(observed),
        )

    assert (profile.cost_ceiling_cents, profile.concurrency_units) == (300, 2)
    assert effect.status == "completed"
    assert effect.cost_cents == 30
    assert effect.attempt_id == provider._attempt_id(item, _binding_digest(item))
    assert effect.attempt_started_at == 10_000
    assert len(reconciled) == 1
    assert FakePrimitives.instances[0].calls == [
        ("DEVHUB-21", "start"), ("DEVHUB-21", "open-pr"),
            ("DEVHUB-21", "review"), ("DEVHUB-21", "ci"),
            ("DEVHUB-21", "human-gate"), ("DEVHUB-21", "merge"),
            ("DEVHUB-20", "sync-parent-acceptance"),
            ("DEVHUB-20", "close-epic"),
    ]
    assert provider.resolve(item.id, _binding_digest(item)) == effect


def test_campaign_provider_reads_legacy_receipt_without_inventing_attempt(tmp_path):
    item = command()
    state = tmp_path / "state"
    binding_digest = _binding_digest(item)
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(), root=tmp_path, state_directory=state,
            owner_id="worker-1",
        )
    path = state / "effects" / f"{item.id}.json"
    path.write_text(json.dumps({
        "contract": RECEIPT_CONTRACT,
        "binding_digest": binding_digest,
        "effect": {
            "effect_id": item.id, "status": "completed",
            "proof_digest": DIGEST_A, "cost_cents": 0, "duration_ms": 0,
        },
    }), encoding="utf-8")

    effect = provider.resolve(item.id, binding_digest)

    assert effect is not None
    assert effect.attempt_id is None and effect.attempt_started_at is None
    assert provider._attempt_coordinates(item, binding_digest, effect) == (None, None)
    assert provider.verified_campaign_terminal_evidence(item, effect) == (False, None)


@pytest.mark.parametrize(
    ("crash_window", "terminal_proof"),
    (
        (None, "available"),
        ("before-primitive-intent", "available"),
        ("after-primitive-intent", "available"),
        ("after-provider-mutation", "available"),
        ("after-close-provider-mutation", "available"),
        ("after-close-provider-mutation-expired", "f91-unavailable"),
        (None, "f91-unavailable"),
        (None, "original-restored-live"),
        (None, "original-restored-expired"),
        (None, "original-unavailable"),
        (None, "original-missing"),
        (None, "original-contradictory"),
        (None, "original-unavailable-expired"),
        (None, "original-missing-expired"),
        (None, "original-contradictory-expired"),
    ),
)
def test_outer_worker_recovers_three_child_parent_sync_and_close_exactly_once(
    tmp_path, monkeypatch, crash_window, terminal_proof,
):
    expired_crash = crash_window == "after-close-provider-mutation-expired"
    if expired_crash:
        crash_window = "after-close-provider-mutation"
    expire_missing = terminal_proof in {
        "original-unavailable-expired", "original-missing-expired",
        "original-contradictory-expired",
    }
    if expire_missing:
        terminal_proof = terminal_proof.removesuffix("-expired")
    body = (
        "## Critères d acceptation\n"
        "- [ ] enfant dix livre\n"
        "- [ ] enfant onze livre\n"
        "- [ ] enfant douze livre\n"
    )
    child_ids = ("DEVHUB-21", "DEVHUB-22", "DEVHUB-23")
    criteria = acceptance_criteria(body)
    approved_mapping = (
        (criteria[0]["id"], criteria[0]["digest"], "DEVHUB-21"),
        (criteria[1]["id"], criteria[1]["digest"], "DEVHUB-22"),
        (criteria[2]["id"], criteria[2]["digest"], "DEVHUB-23"),
    )

    class Tracker:
        name = "devhub"
        acceptance_sync_supported = True
        epic_closure_supported = True
        requires_mutation_binding = False

        def __init__(self):
            self.project = Project("DEVHUB", "project-1")
            self.parent = SimpleNamespace(
                id="DEVHUB-20", title="Epic", type="Epic", state="in-progress",
                version=4, body=body, ac_done=0, ac_total=3, pr_url=None,
                links=[
                    SimpleNamespace(type="parent-of", target=issue_id)
                    for issue_id in child_ids
                ],
            )
            self.patch_calls = 0
            self.close_calls = 0
            self.closure = None
            self.closure_reads = 0

        def get_issue(self, issue_id):
            if issue_id == self.parent.id:
                return self.parent
            return SimpleNamespace(id=issue_id, state="done", version=2)

        def sync_acceptance_body(
            self, issue_id, expected_body, updated_body, _proof_id, **_kwargs,
        ):
            assert issue_id == self.parent.id and expected_body == self.parent.body
            self.parent.body = updated_body
            self.parent.version = 5
            self.parent.ac_done = 3
            self.patch_calls += 1
            return True

        def resolve_project(self, _repo):
            return self.project

        def get_epic_closure(self, project, parent_id):
            assert project == self.project and parent_id == self.parent.id
            self.closure_reads += 1
            if self.closure is None:
                return None
            if (self.closure_reads > 1
                    and terminal_proof == "original-unavailable"):
                raise DevHubTrackerError(
                    "GET", "/projects/DEVHUB/epics/DEVHUB-20/closure",
                    None, "tracker_unavailable",
                )
            if (self.closure_reads == 2 and terminal_proof in {
                "original-restored-live", "original-restored-expired",
            }):
                return None
            if self.closure_reads > 1 and terminal_proof == "original-missing":
                return None
            if (self.closure_reads > 1
                    and terminal_proof == "original-contradictory"):
                return replace(
                    self.closure, audit_id="audit:outer:changed", replayed=True,
                )
            return replace(self.closure, replayed=True)

        def close(self):
            self.close_calls += 1
            receipt = EpicClosureReceipt(
                self.project.key, self.project.id, self.parent.id, 5, "Epic", 3, 3,
                tuple(EpicClosureChild(issue_id, 2, "done") for issue_id in child_ids),
                10_000, "nonce_1234567890abcdef",
            )
            self.parent.state = "done"
            self.parent.version = 6
            self.closure = EpicClosureOutcome(receipt, 6, "audit:outer:close")

    tracker = Tracker()
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: tracker)
    crash = {"fired": False}
    merge_calls = {issue_id: 0 for issue_id in child_ids}

    class CrashRunner(FoundryPrimitiveRunner):
        def start_issue(self, envelope):
            return self._record_complete(envelope)

        open_pr = start_issue
        review = start_issue
        ci_gate = start_issue
        human_gate = start_issue

        def _record_complete(self, envelope):
            receipt = self._receipt(envelope, "completed", [envelope.effect_id])
            return self.receipts.record(envelope, receipt, now_ms=self.now_ms())

        def merge_issue(self, envelope, *, engage=None):
            if engage is not None:
                engage()
            merge_calls[envelope.issue_id] += 1
            receipt = self._receipt(
                envelope, "completed", [envelope.effect_id, 1, HEAD],
            )
            return self.receipts.record(
                envelope, receipt, pr_number=1, head_sha=HEAD,
                base_sha=BASE, now_ms=self.now_ms(),
            )

        def sync_parent_acceptance(self, envelope, proof, advancement):
            if crash_window == "before-primitive-intent" and not crash["fired"]:
                crash["fired"] = True
                raise OSError("crash before primitive intent")
            if crash_window == "after-primitive-intent" and not crash["fired"]:
                original = self.receipts.begin_parent_snapshot_advancement

                def interrupted(candidate, frozen, **kwargs):
                    original(candidate, frozen, **kwargs)
                    if not crash["fired"]:
                        crash["fired"] = True
                        raise OSError("crash after primitive intent")

                self.receipts.begin_parent_snapshot_advancement = interrupted
            if crash_window == "after-provider-mutation" and not crash["fired"]:
                original = self.receipts.record

                def interrupted(candidate, receipt, **kwargs):
                    if candidate.step == "sync-parent-acceptance" and not crash["fired"]:
                        crash["fired"] = True
                        raise OSError("crash after provider mutation")
                    return original(candidate, receipt, **kwargs)

                self.receipts.record = interrupted
            return super().sync_parent_acceptance(envelope, proof, advancement)

        def _issue_command(self, envelope, action, *extra, **kwargs):
            assert action == "close-epic" and not extra
            tracker.close()
            if crash_window == "after-close-provider-mutation" and not crash["fired"]:
                crash["fired"] = True
                raise OSError("crash after close provider mutation")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    class ThreeChildSource(FakeSource):
        def load(self, _command):
            return SimpleNamespace(
                observation=observation(), waves=(child_ids,), blockers=(),
                acceptance_mapping=approved_mapping,
            )

    implementation_calls = []

    class CountingExecutor(FakeExecutor):
        def execute(self, envelope):
            implementation_calls.append((envelope.issue_id, envelope.attempt))
            return super().execute(envelope)

    class Client:
        def __init__(self, item):
            self.item = item
            self.event_evidence = []
            self.claim_calls = 0
            self.heartbeat_calls = 0
            self.late_calls = 0

        def publish_late_terminal(self, item, **payload):
            self.late_calls += 1
            assert item.lease.expires_at <= 10_000
            assert payload["lease_id"] == item.lease.id
            assert payload["original_closure"]["outcome"]["audit_id"] == tracker.closure.audit_id
            # The server derives this projection from its own original journal.
            projected = epic_closure_receipt(
                "devhub", item.epic_id, tracker.closure, observed_at=10_000,
            ).receipt
            self.append_event(
                item, event_type="completed", sequence=payload["sequence"],
                attempt_id=payload["attempt_id"], issue_id=item.epic_id,
                receipts={"pr": None, "review": None, "ci": None, "merge": None,
                          "epic_closure": projected},
            )

        def list_commands(self, _project, *, cursor, limit):
            return CommandPage((self.item,), None, False)

        def claim(self, item, *, worker_id, lease_seconds, idempotency_key):
            self.claim_calls += 1
            if (terminal_proof == "original-restored-expired"
                    and item.lease is not None
                    and item.lease.expires_at <= 10_000):
                # Mirrors DevHub claimCommand: after the close advanced the parent,
                # revalidatePreviewBinding rejects a fresh lease before provider work.
                raise DevHubCommandError(
                    "POST", f"/commands/{item.id}/claim", 409,
                    "digest_conflict",
                )
            self.item = replace(
                item, version=item.version + 1,
                lease=CommandLease("lease-outer", worker_id, 30_000, 10_000),
            )
            return self.item

        def get_command(self, _command_id):
            return self.item

        def heartbeat(self, item, **_kwargs):
            self.heartbeat_calls += 1
            lease = item.lease
            self.item = replace(
                item, version=item.version + 1,
                lease=replace(
                    lease, expires_at=lease.expires_at + 1_000,
                    heartbeat_at=lease.heartbeat_at + 1,
                ),
            )
            return self.item

        def append_event(
            self, item, *, event_type, sequence, attempt_id=None,
            issue_id=None, receipts=None, **_kwargs,
        ):
            self.event_evidence.append(
                (event_type, attempt_id, issue_id, receipts)
            )
            self.item = replace(
                item, state=event_type, version=item.version + 1,
                next_event_sequence=sequence + 1, projection_stale=False,
            )
            return self.item

        def list_events(self, *_args, **_kwargs):
            raise AssertionError("no ambiguous outer event expected")

    item = command()
    client = Client(item)
    state = tmp_path / "state"
    execution_receipts = ExecutionReceiptStore(tmp_path / "execution-receipts")
    f91_available = {"value": terminal_proof == "available"}
    original_f91_save = execution_receipts._save

    def controlled_f91_save(value):
        if not f91_available["value"]:
            raise OSError("F91 unavailable")
        return original_f91_save(value)

    monkeypatch.setattr(execution_receipts, "_save", controlled_f91_save)
    with patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()):
        provider = CampaignCommandEffectProvider(
            ThreeChildSource(), root=tmp_path, state_directory=state,
            owner_id="worker-1", primitive_runner=CrashRunner,
            executor_factory=CountingExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
            execution_receipts=execution_receipts,
        )
        outer = CommandWorker(
            client, ReceiptStore(tmp_path / "outer-receipts.sqlite3"), provider,
            worker_id="worker-1", lease_seconds=60, now_ms=lambda: 10_000,
            execution_receipts=execution_receipts,
        )
        statuses = []
        details = []
        effect_before_evidence_retry = None
        work_before_evidence_retry = None
        for _ in range(5):
            outcome = outer.run_once("DEVHUB")[0]
            statuses.append(outcome.status)
            details.append(outcome.detail)
            if expired_crash and tracker.closure is not None and outcome.status != "completed":
                client.item = replace(
                    client.item, lease=replace(client.item.lease, expires_at=9_000),
                )
            if outcome.status == "completed":
                break
            if outcome.status != "deferred":
                continue
            if effect_before_evidence_retry is None:
                assert outcome.detail == "terminal_evidence_unavailable"
                assert all(event[0] != "completed" for event in client.event_evidence)
                effect_before_evidence_retry = provider.resolve(
                    item.id, _binding_digest(item),
                )
                work_before_evidence_retry = (
                    tuple(implementation_calls), dict(merge_calls), tracker.close_calls,
                    tracker.patch_calls,
                )
                if terminal_proof == "original-restored-expired" or expire_missing:
                    assert client.item.lease is not None
                    client.item = replace(
                        client.item,
                        lease=replace(client.item.lease, expires_at=9_000),
                    )
                continue
            # A passive F91 store cannot replace a missing or changed original
            # DevHub closure. One retry proves the fail-closed result.
            assert terminal_proof in {
                "original-restored-expired", "original-unavailable",
                "original-missing",
                "original-contradictory",
            }
            break

    campaign_row = CampaignStore(
        state / "campaigns" / item.id / "campaign.sqlite3",
    ).campaign(item.id)
    terminal_expected = terminal_proof in {
        "available", "f91-unavailable", "original-restored-live",
        "original-restored-expired",
    }
    expected_status = "completed" if terminal_expected else "deferred"
    assert statuses[-1] == expected_status, (
        statuses, campaign_row.state, campaign_row.reason,
    )
    assert crash["fired"] is (crash_window is not None)
    assert tracker.patch_calls == 1
    assert tracker.close_calls == 1
    assert merge_calls == {issue_id: 1 for issue_id in child_ids}
    assert sorted(implementation_calls) == [
        (issue_id, 1) for issue_id in child_ids
    ]
    assert tracker.parent.version == 6 and tracker.parent.state == "done"
    assert (tracker.parent.ac_done, tracker.parent.ac_total) == (3, 3)
    effect = provider.resolve(item.id, _binding_digest(item))
    assert effect is not None
    # Late publication settles the remote fact; it does not invent a completed
    # local campaign outcome when interruption prevented recording that outcome.
    assert effect.status == ("paused" if expired_crash else "completed")
    if expired_crash:
        assert client.late_calls == 1
        assert details[-1] == "late_terminal_fact_published"
    assert effect.attempt_id == provider._attempt_id(item, _binding_digest(item))
    assert effect.attempt_started_at == 10_000
    assert outer.store.get(item.id).binding_digest == _binding_digest(item)
    if terminal_proof in {
        "original-restored-live", "original-restored-expired",
        "original-unavailable", "original-missing", "original-contradictory",
    }:
        assert statuses[:2] == ["deferred", expected_status]
        assert effect_before_evidence_retry == effect
        assert work_before_evidence_retry == (
            tuple(implementation_calls), dict(merge_calls), tracker.close_calls,
            tracker.patch_calls,
        )
    if terminal_proof == "original-restored-expired":
        assert details[:2] == [
            "terminal_evidence_unavailable", "late_terminal_fact_published",
        ]
        assert client.claim_calls == 1
        assert client.late_calls == 1
        assert tracker.closure_reads == 3
    else:
        assert client.claim_calls == 1
    if terminal_proof == "original-restored-live":
        assert client.heartbeat_calls > 0
        assert tracker.closure_reads == 3
    elif terminal_proof in {
        "original-unavailable", "original-missing", "original-contradictory",
    }:
        assert tracker.closure_reads == 3
    elif terminal_proof != "original-restored-expired":
        # One read verifies the provider close; the second independently sources
        # the command event even when the exact closure is already present in F91.
        assert tracker.closure_reads >= 2
    exact_receipts = execution_receipts.receipts_for(
        effect.attempt_id, item.epic_id,
    )
    expected_closure = {
        "epic_id": item.epic_id,
        "state": "closed",
        "receipt_id": "audit:outer:close",
        "parent_version": 6,
        "children": [
            {"issue_id": issue_id, "version": 2, "state": "done"}
            for issue_id in child_ids
        ],
    }
    if terminal_expected:
        event_receipts = client.event_evidence[-1][3]
        assert event_receipts["epic_closure"] == {
            **expected_closure,
            "closure_digest": event_receipts["epic_closure"]["closure_digest"],
        }
        assert client.event_evidence[-1] == (
            "completed", effect.attempt_id, item.epic_id, event_receipts,
        )
        if terminal_proof == "available":
            assert exact_receipts == event_receipts
        else:
            # F91 remains passive: its outage does not downgrade or block the
            # validated original closure carried by the native command event.
            assert exact_receipts["epic_closure"] is None
        assert provider.observe(item).snapshot_advancement is not None
    else:
        assert exact_receipts["epic_closure"] is None
        assert all(event[0] != "completed" for event in client.event_evidence)
    assert tracker.close_calls == 1
    tracker.parent.version = 7
    with pytest.raises(
        CampaignRevalidationError, match="parent_snapshot_advancement_conflict",
    ):
        provider.observe(item)


def test_command_provider_journals_approved_blockers_and_projects_only_paused(tmp_path):
    FakePrimitives.instances.clear()
    FakePrimitives.blocker_states = {"DEVHUB-99": "blocked"}
    FakePrimitives.blocker_observation_versions = {}
    FakePrimitives.blocker_observation_states = {}
    item = command()

    class NoExecutionExecutor(FakeExecutor):
        def execute(self, envelope):
            raise AssertionError(f"unexpected implementation attempt for {envelope.issue_id}")

    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(blockers=("DEVHUB-99",)), root=tmp_path,
            state_directory=tmp_path / "state", owner_id="worker-1",
            primitive_runner=FakePrimitives, executor_factory=NoExecutionExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        effect = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

    ledger_store = CampaignStore(
        tmp_path / "state" / "campaigns" / item.id / "campaign.sqlite3",
    )
    ledger = ledger_store.campaign(item.id)
    assert effect.status == "paused"
    assert (ledger.state, ledger.reason) == ("suspended", "approved_blockers")
    assert FakePrimitives.instances[0].calls == []
    with sqlite3.connect(ledger_store.path) as connection:
        spec = json.loads(connection.execute(
            "SELECT spec_json FROM campaigns WHERE campaign_id = ?", (item.id,),
        ).fetchone()[0])
    assert spec["blockers"] == ["DEVHUB-99"]


def test_paused_blocker_projection_reactivates_only_after_fresh_resolution_proof(
    tmp_path,
):
    FakePrimitives.instances.clear()
    FakePrimitives.blocker_states = {"DEVHUB-99": "blocked"}
    item = command()
    paused_command = replace(item, state="paused")
    running_command = replace(item, state="running")
    state = tmp_path / "state"
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(blockers=("DEVHUB-99",)), root=tmp_path,
            state_directory=state, owner_id="worker-1",
            primitive_runner=FakePrimitives, executor_factory=FakeExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        suspended = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )
        still_suspended = provider.resume(
            paused_command, suspended, authorization,
            heartbeat=lambda: paused_command,
            reconcile_capacity=lambda _observed: None,
        )
        assert still_suspended.status == "paused"
        assert FakePrimitives.instances[-1].calls == []

        FakePrimitives.blocker_states["DEVHUB-99"] = "done"
        reactivated = provider.resume(
            paused_command, still_suspended, authorization,
            heartbeat=lambda: paused_command,
            reconcile_capacity=lambda _observed: None,
        )
        assert reactivated.status == "running"
        assert FakePrimitives.instances[-1].calls == []

        completed = provider.resume(
            running_command, reactivated, authorization,
            heartbeat=lambda: running_command,
            reconcile_capacity=lambda _observed: None,
        )

    assert completed.status == "completed"
    assert FakePrimitives.instances[-1].calls[-1] == ("DEVHUB-20", "close-epic")


def test_command_provider_does_not_infer_undocumented_human_pause_reactivation(tmp_path):
    FakePrimitives.instances.clear()
    pause_request = replace(command(), state="pause-requested")
    paused_projection = replace(command(), state="paused")
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(), root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=FakeExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(pause_request, None)
        authorization = ExecutionAuthorization(
            pause_request.id, _binding_digest(pause_request), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        paused = provider.launch(
            pause_request, authorization, effect_id=pause_request.id,
            heartbeat=lambda: pause_request,
            reconcile_capacity=lambda _observed: None,
        )
        observed = provider.resume(
            paused_projection, paused, authorization,
            heartbeat=lambda: paused_projection,
            reconcile_capacity=lambda _observed: None,
        )

    assert paused.status == "paused"
    assert observed.status == "paused"
    assert FakePrimitives.instances[-1].calls == []


def test_production_blocker_observation_requires_exact_fresh_terminal_fact(tmp_path):
    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=store,
    )
    spec = SimpleNamespace(
        blockers=("DEVHUB-91", "DEVHUB-92", "DEVHUB-93", "DEVHUB-94"),
    )
    issues = {
        "DEVHUB-91": SimpleNamespace(
            id="DEVHUB-91", state="done", updated=10_000, version=None,
        ),
        "DEVHUB-92": SimpleNamespace(
            id="DEVHUB-92", state="dropped", updated=None, version=7,
        ),
        "DEVHUB-93": SimpleNamespace(
            id="DEVHUB-93", state="done", updated=None, version=None,
        ),
        "DEVHUB-94": SimpleNamespace(
            id="DEVHUB-FOREIGN", state="done", updated=10_000, version=8,
        ),
    }
    with patch("foundry.campaign_runtime.foundry.tracker") as tracker:
        tracker.return_value.get_issue.side_effect = issues.__getitem__
        observed = runner.observe_blockers(spec)

    assert [item.state for item in observed] == [
        "done", "dropped", "done", "unknown",
    ]
    assert [item.resolved for item in observed] == [True, True, False, False]


def test_production_campaign_raises_a_fresh_floor_before_implementation_effect(tmp_path):
    class TighteningSource(FakeSource):
        def __init__(self):
            super().__init__()
            self.floor = "frontier"

        def load(self, _command):
            return SimpleNamespace(
                observation=replace(
                    observation(), current_minimum_tier=self.floor,
                ),
                waves=(("DEVHUB-21",),), blockers=(),
                acceptance_mapping=((
                    "ac-1", _digest(["criterion", 1]), "DEVHUB-21",
                ),),
            )

    source = TighteningSource()

    class TighteningExecutor(FakeExecutor):
        envelopes = []

        def execution_profile(self, _issue_id, _attempt):
            # This happens after the coordinator's first observation and before
            # its final pre-provider observation.
            source.floor = "apex"
            return AttemptProfile(self.selected_tier, self.cost_ceiling_cents)

        def execute(self, envelope):
            self.envelopes.append(envelope)
            return super().execute(envelope)

    item = command()
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=TighteningExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        effect = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

    assert effect.status == "completed"
    assert [envelope.selected_tier for envelope in TighteningExecutor.envelopes] == [
        "apex",
    ]
    store = CampaignStore(
        tmp_path / "state" / "campaigns" / item.id / "campaign.sqlite3",
    )
    assert store.effect(item.id, "DEVHUB-21", 1, "implementation")[6] == "apex"
    assert store.issue(item.id, "DEVHUB-21")["selected_tier"] == "apex"


def test_production_blocker_requires_exact_manual_retry_authorization(tmp_path, capsys):
    class PersistentBlockedExecutor(FakeExecutor):
        shared_proposals = {}
        envelopes = []

        def resolve(self, work_id):
            return self.shared_proposals.get(work_id)

        def reconcile(self, envelope):
            return ImplementationReconciliation(
                envelope.work_id, "unknown",
                _digest([envelope.effect_id, "unknown"]), 0,
            )

        def execute(self, envelope):
            self.envelopes.append(envelope)
            outcome = "blocked" if envelope.attempt == 1 else "completed"
            proposal = ImplementationProposal(
                envelope.work_id, envelope.issue_id, envelope.attempt, outcome,
                _digest([envelope.work_id, outcome]), 30, envelope.selected_tier,
            )
            self.shared_proposals[envelope.work_id] = proposal
            return proposal

    PersistentBlockedExecutor.shared_proposals.clear()
    PersistentBlockedExecutor.envelopes.clear()
    item = command()
    state = tmp_path / "state"
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(), root=tmp_path, state_directory=state,
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=PersistentBlockedExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        blocked = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )
        observed_again = provider.resume(
            item, blocked, authorization, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

        assert blocked.status == observed_again.status == "paused"
        assert [item.attempt for item in PersistentBlockedExecutor.envelopes] == [1]

        with patch("foundry.campaign_retry.time.time", return_value=10):
            campaign_retry_main([
                item.id, "DEVHUB-21", "--attempt", "1", "--actor", "operator-1",
                "--state-dir", str(state),
            ])
        authorization_record = json.loads(capsys.readouterr().out)
        assert CampaignStore(
            state / "campaigns" / item.id / "campaign.sqlite3",
        ).authorize_manual_retry(
            item.id, "DEVHUB-21", 1, actor="operator-1", now_ms=10_001,
        ) == authorization_record["authorization_digest"]
        completed = provider.resume(
            item, observed_again, authorization, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

    assert authorization_record["reason"] == "manual_retry_approved"
    assert completed.status == "completed"
    assert [item.attempt for item in PersistentBlockedExecutor.envelopes] == [1, 2]
    assert len({item.effect_id for item in PersistentBlockedExecutor.envelopes}) == 2
    assert len({item.work_id for item in PersistentBlockedExecutor.envelopes}) == 2
    database = state / "campaigns" / item.id / "campaign.sqlite3"
    with sqlite3.connect(database) as connection:
        audit = connection.execute(
            "SELECT authorization_digest, effect_id, proof_digest "
            "FROM campaign_resume_audits WHERE campaign_id = ? AND issue_id = ?",
            (item.id, "DEVHUB-21"),
        ).fetchone()
    first = PersistentBlockedExecutor.envelopes[0]
    assert audit == (
        authorization_record["authorization_digest"], first.effect_id,
        PersistentBlockedExecutor.shared_proposals[first.work_id].proof_digest,
    )


@pytest.mark.parametrize(
    "drift",
    (
        "head", "diff", "base", "proof", "proof-missing", "pr-number",
        "pr-state", "pr-merged", "effect", "primitive-proof", "ambiguous",
        "changed-during-read",
    ),
)
def test_review_remediation_revalidation_rejects_current_authority_drift(
    tmp_path, drift,
):
    effect_id = "effect-review-1"
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "review", effect_id, "work-review-1", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, "frontier", 0,
    )
    proof_id = "4" * 64
    changed_proof_id = "5" * 64
    effects = CampaignEffectStore(tmp_path / "primitive-receipts.sqlite3")
    blocked = StepReceipt(
        effect_id, "blocked",
        _digest([effect_id, proof_id, "ac-proof-nonpass"]),
    )
    effects.record(
        envelope, blocked, pr_number=7, head_sha=HEAD, base_sha=BASE,
        now_ms=10_000,
    )
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=effects,
    )
    target = ReviewRemediationTarget(
        envelope.campaign_id, envelope.issue_id, envelope.attempt,
        envelope.effect_id, blocked.proof_digest, envelope.binding_digest,
        "implementer", "frontier",
    )
    pr = PullRequest(
        7, "https://example.invalid/pull/7", "fix/devhub-21", "main",
        base_sha=BASE, sha=HEAD,
    )
    if drift == "head":
        pr.sha = "7" * 40
    elif drift == "base":
        pr.base_sha = "6" * 40
    elif drift == "pr-number":
        pr.number = 8
    elif drift == "pr-state":
        pr.state = "closed"
    elif drift == "pr-merged":
        pr.merged = True
    elif drift == "effect":
        target = replace(target, effect_id="effect-review-drift")
    elif drift == "primitive-proof":
        target = replace(target, proof_digest=DIGEST_C)

    class ProofStore:
        def valid_for_merge(self, **_coordinates):
            raise RoutingConfigError("not mergeable")

        def current_for_sync(self, **coordinates):
            if drift == "proof-missing":
                raise RoutingConfigError("proof missing")
            assert coordinates["diff"] == (
                b"changed diff" if drift == "diff" else b"stable diff"
            )
            return {
                "proof_id": changed_proof_id
                if (drift in {"head", "diff", "proof"}
                    or coordinates["base"] != BASE) else proof_id,
            }

    if drift == "ambiguous":
        def codehost_coordinates(_target):
            raise CampaignError("PR d'issue absente ou ambigue")
        runner._codehost_coordinates = codehost_coordinates
    elif drift == "changed-during-read":
        reads = iter((
            pr,
            replace(pr, base_sha="6" * 40),
        ))
        runner._codehost_coordinates = lambda _target: (
            tmp_path, object(), "owner/repository", next(reads),
        )
    else:
        runner._codehost_coordinates = lambda _target: (
            tmp_path, object(), "owner/repository", pr,
        )
    issue = SimpleNamespace(id="DEVHUB-21", body="- [ ] criterion")
    current_head = pr.sha
    current_diff = b"changed diff" if drift == "diff" else b"stable diff"

    with (
        patch("foundry.campaign_runtime.git_head", return_value=current_head),
        patch("foundry.campaign_runtime.git_diff", return_value=current_diff),
        patch("foundry.campaign_runtime.repository_identity", return_value="owner/repository"),
        patch("foundry.campaign_runtime.AcceptanceProofStore", return_value=ProofStore()),
        patch(
            "foundry.campaign_runtime.foundry.tracker",
            return_value=SimpleNamespace(get_issue=lambda _issue_id: issue),
        ),
    ):
        with pytest.raises(CampaignRevalidationError, match="review_remediation"):
            runner.revalidate_review_remediation(target)

    assert effects.coordinates("campaign-1", "DEVHUB-21", 1, "review") == (
        blocked, 7, HEAD, BASE,
    )


def test_review_remediation_revalidation_accepts_only_exact_blocked_proof(tmp_path):
    effect_id = "effect-review-1"
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "review", effect_id, "work-review-1", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, "frontier", 0,
    )
    proof_id = "4" * 64
    blocked = StepReceipt(
        effect_id, "blocked",
        _digest([effect_id, proof_id, "ac-proof-nonpass"]),
    )
    effects = CampaignEffectStore(tmp_path / "primitive-receipts.sqlite3")
    effects.record(
        envelope, blocked, pr_number=7, head_sha=HEAD, base_sha=BASE,
        now_ms=10_000,
    )
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=effects,
    )
    target = ReviewRemediationTarget(
        envelope.campaign_id, envelope.issue_id, envelope.attempt,
        envelope.effect_id, blocked.proof_digest, envelope.binding_digest,
        "implementer", "frontier",
    )
    pr = PullRequest(
        7, "https://example.invalid/pull/7", "fix/devhub-21", "main",
        base_sha=BASE, sha=HEAD,
    )
    runner._codehost_coordinates = lambda _target: (
        tmp_path, object(), "owner/repository", pr,
    )

    class ProofStore:
        def valid_for_merge(self, **_coordinates):
            raise RoutingConfigError("not mergeable")

        def current_for_sync(self, **_coordinates):
            return {"proof_id": proof_id}

    with (
        patch("foundry.campaign_runtime.git_head", return_value=HEAD),
        patch("foundry.campaign_runtime.git_diff", return_value=b"stable diff"),
        patch("foundry.campaign_runtime.repository_identity", return_value="owner/repository"),
        patch("foundry.campaign_runtime.AcceptanceProofStore", return_value=ProofStore()),
        patch(
            "foundry.campaign_runtime.foundry.tracker",
            return_value=SimpleNamespace(
                get_issue=lambda _issue_id: SimpleNamespace(
                    id="DEVHUB-21", body="- [ ] criterion",
                ),
            ),
        ),
    ):
        assert runner.revalidate_review_remediation(target) == target

    with pytest.raises(ValueError, match="role"):
        replace(target, role="reviewer")


@pytest.mark.parametrize("drift", ("head", "diff", "proof"))
def test_public_review_remediation_drift_leaves_grant_and_campaign_untouched(
    tmp_path, drift,
):
    stored_proof_id = "4" * 64
    current_proof_id = "5" * 64

    class ReviewSource(FakeSource):
        def load(self, command):
            authority = super().load(command)
            authority.observation = replace(
                authority.observation, preview_expires_at=100_000,
                approval_expires_at=90_000,
            )
            return authority

    class BlockedReviewPrimitives(FakePrimitives):
        def review(self, envelope):
            receipt = StepReceipt(
                envelope.effect_id, "blocked",
                _digest([
                    envelope.effect_id, stored_proof_id, "ac-proof-nonpass",
                ]),
            )
            return self.receipts.record(
                envelope, receipt, pr_number=7, head_sha=HEAD, base_sha=BASE,
                now_ms=10_000,
            )

    item = command()
    state = tmp_path / "state"
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            ReviewSource(), root=tmp_path, state_directory=state,
            owner_id="worker-1", primitive_runner=BlockedReviewPrimitives,
            executor_factory=FakeExecutor,
            escalation_store_factory=lambda _root: EscalationStore(
                f"runtime-review-drift-{drift}", state_dir=tmp_path / "escalation",
            ),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        blocked = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

    store = CampaignStore(state / "campaigns" / item.id / "campaign.sqlite3")
    before_campaign = store.campaign(item.id)
    before_issue = store.issue(item.id, "DEVHUB-21")
    assert (blocked.status, before_campaign.reason) == ("paused", "review_blocking")
    live_head = "7" * 40 if drift == "head" else HEAD
    live_diff = b"changed diff" if drift == "diff" else b"stable diff"
    pr = PullRequest(
        7, "https://example.invalid/pull/7", "fix/devhub-21", "main",
        base_sha=BASE, sha=live_head,
    )

    class CurrentProofStore:
        def valid_for_merge(self, **_coordinates):
            raise RoutingConfigError("not mergeable")

        def current_for_sync(self, **_coordinates):
            return {
                "proof_id": current_proof_id
                if drift in {"diff", "proof"} else stored_proof_id,
            }

    with (
        patch.object(
            FoundryPrimitiveRunner, "_codehost_coordinates",
            return_value=(tmp_path, object(), "owner/repository", pr),
        ),
        patch("foundry.campaign_runtime.git_head", return_value=live_head),
        patch("foundry.campaign_runtime.git_diff", return_value=live_diff),
        patch("foundry.campaign_runtime.repository_identity", return_value="owner/repository"),
        patch(
            "foundry.campaign_runtime.AcceptanceProofStore",
            return_value=CurrentProofStore(),
        ),
        patch(
            "foundry.campaign_runtime.foundry.tracker",
            return_value=SimpleNamespace(
                get_issue=lambda _issue_id: SimpleNamespace(
                    id="DEVHUB-21", body="- [ ] criterion",
                ),
            ),
        ),
        patch("foundry.campaign_review_remediation.time.time", return_value=10),
    ):
        with pytest.raises(CampaignError, match="revalidation d'autorité"):
            campaign_review_remediation_main([
                item.id, "DEVHUB-21", "--attempt", "1",
                "--actor", "operator-1", "--valid-seconds", "60",
                "--state-dir", str(state), "--root", str(tmp_path),
            ])

    assert store.campaign(item.id) == before_campaign
    assert store.issue(item.id, "DEVHUB-21") == before_issue
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM campaign_review_remediation_authorizations"
        ).fetchone() == (0,)


@pytest.mark.parametrize(
    ("control", "campaign_state", "reason"),
    (
        ("pause-requested", "paused", "pause"),
        ("cancel-requested", "cancelled", "cancel"),
    ),
)
def test_public_review_remediation_replay_rejects_changed_campaign_control(
    tmp_path, capsys, control, campaign_state, reason,
):
    stable_proof_id = "4" * 64

    class ReviewSource(FakeSource):
        def load(self, command):
            authority = super().load(command)
            authority.observation = replace(
                authority.observation, preview_expires_at=100_000,
                approval_expires_at=90_000,
            )
            return authority

    class BlockedReviewPrimitives(FakePrimitives):
        def review(self, envelope):
            receipt = StepReceipt(
                envelope.effect_id, "blocked",
                _digest([
                    envelope.effect_id, stable_proof_id, "ac-proof-nonpass",
                ]),
            )
            return self.receipts.record(
                envelope, receipt, pr_number=7, head_sha=HEAD, base_sha=BASE,
                now_ms=10_000,
            )

    item = command()
    state = tmp_path / "state"
    escalation_dir = tmp_path / "escalation"
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            ReviewSource(), root=tmp_path, state_directory=state,
            owner_id="worker-1", primitive_runner=BlockedReviewPrimitives,
            executor_factory=FakeExecutor,
            escalation_store_factory=lambda _root: EscalationStore(
                f"runtime-review-control-{control}", state_dir=escalation_dir,
            ),
        )
        profile = provider.execution_profile(item, None)
        execution_authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        blocked = provider.launch(
            item, execution_authorization, effect_id=item.id,
            heartbeat=lambda: item, reconcile_capacity=lambda _observed: None,
        )

    campaign_store = CampaignStore(
        state / "campaigns" / item.id / "campaign.sqlite3",
    )
    assert blocked.status == "paused"
    authorization = campaign_store.authorize_review_remediation(
        item.id, "DEVHUB-21", 1, actor="operator-1", valid_seconds=60,
        now_ms=10_000, revalidate=lambda target: target,
    )
    authorized_campaign = campaign_store.campaign(item.id)
    assert (
        authorized_campaign.state,
        authorized_campaign.reason,
        authorized_campaign.control,
    ) == (
        "running", "manual_retry_approved", "active",
    )

    campaign_store.request_control(item.id, control, now_ms=11_000)
    campaign_store.set_state(item.id, campaign_state, reason, now_ms=11_001)
    controlled = campaign_store.campaign(item.id)
    assert (controlled.state, controlled.reason, controlled.control) == (
        campaign_state, reason, control,
    )
    with sqlite3.connect(campaign_store.path) as connection:
        campaign_before_replay = tuple(connection.iterdump())
    escalation_before_replay = {
        path.relative_to(escalation_dir): path.read_bytes()
        for path in escalation_dir.rglob("*") if path.is_file()
    }
    capsys.readouterr()

    with patch.object(
        FoundryPrimitiveRunner, "revalidate_review_remediation",
        autospec=True, side_effect=lambda _runner, target: target,
    ) as revalidate:
        with patch("foundry.campaign_review_remediation.time.time", return_value=11):
            with pytest.raises(CampaignError, match="première review"):
                campaign_review_remediation_main([
                    item.id, "DEVHUB-21", "--attempt", "1",
                    "--actor", "operator-1", "--valid-seconds", "60",
                    "--state-dir", str(state), "--root", str(tmp_path),
                ])

    assert revalidate.call_count == 0
    assert capsys.readouterr().out == ""
    with sqlite3.connect(campaign_store.path) as connection:
        assert tuple(connection.iterdump()) == campaign_before_replay
        assert connection.execute(
            "SELECT authorization_digest "
            "FROM campaign_review_remediation_authorizations"
        ).fetchall() == [(authorization.authorization_digest,)]
    assert {
        path.relative_to(escalation_dir): path.read_bytes()
        for path in escalation_dir.rglob("*") if path.is_file()
    } == escalation_before_replay


@pytest.mark.parametrize("second_review_status", ("completed", "blocked"))
def test_production_first_review_remediation_is_public_exact_and_idempotent(
    tmp_path, capsys, second_review_status,
):
    stable_proof_id = "4" * 64

    class ReviewSource(FakeSource):
        def load(self, command):
            authority = super().load(command)
            authority.observation = replace(
                authority.observation, preview_expires_at=100_000,
                approval_expires_at=90_000,
            )
            return authority

    class FirstReviewBlockedPrimitives(FakePrimitives):
        review_attempts = []

        def review(self, envelope):
            self.review_attempts.append(envelope.attempt)
            self.calls.append((envelope.issue_id, envelope.step))
            status = "blocked" if envelope.attempt == 1 else second_review_status
            proof_digest = (
                _digest([
                    envelope.effect_id, stable_proof_id, "ac-proof-nonpass",
                ])
                if envelope.attempt == 1
                else _digest([envelope.effect_id, status])
            )
            return self.receipts.record(
                envelope,
                StepReceipt(envelope.effect_id, status, proof_digest),
                pr_number=7, head_sha=HEAD, base_sha=BASE, now_ms=10_000,
            )

    class CountingExecutor(FakeExecutor):
        attempts = []

        def execute(self, envelope):
            self.attempts.append(envelope.attempt)
            return super().execute(envelope)

    FirstReviewBlockedPrimitives.review_attempts.clear()
    CountingExecutor.attempts.clear()
    item = command()
    state = tmp_path / "state"
    escalation_dir = tmp_path / "escalation"
    source = ReviewSource()

    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=state, owner_id="worker-1",
            primitive_runner=FirstReviewBlockedPrimitives,
            executor_factory=CountingExecutor,
            escalation_store_factory=lambda _root: EscalationStore(
                "runtime-review-remediation", state_dir=escalation_dir,
            ),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        blocked = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

        campaign_store = CampaignStore(
            state / "campaigns" / item.id / "campaign.sqlite3",
        )
        assert blocked.status == "paused"
        assert campaign_store.campaign(item.id).reason == "review_blocking"
        assert CountingExecutor.attempts == [1]
        with pytest.raises(SystemExit, match="compris entre 60 et 86400"):
            campaign_review_remediation_main([
                item.id, "DEVHUB-21", "--attempt", "1", "--actor", "operator-1",
                "--valid-seconds", "59", "--state-dir", str(state),
            ])
        with pytest.raises(CampaignError, match="première review"):
            campaign_review_remediation_main([
                item.id, "DEVHUB-21", "--attempt", "2", "--actor", "operator-1",
                "--valid-seconds", "60", "--state-dir", str(state),
            ])

        pr = PullRequest(
            7, "https://example.invalid/pull/7", "fix/devhub-21", "main",
            base_sha=BASE, sha=HEAD,
        )

        class BlockedProofStore:
            def valid_for_merge(self, **_coordinates):
                raise RoutingConfigError("not mergeable")

            def current_for_sync(self, **_coordinates):
                return {"proof_id": stable_proof_id}

        with (
            patch.object(
                FoundryPrimitiveRunner, "_codehost_coordinates",
                return_value=(tmp_path, object(), "owner/repository", pr),
            ) as read_pr,
            patch("foundry.campaign_runtime.git_head", return_value=HEAD),
            patch("foundry.campaign_runtime.git_diff", return_value=b"stable diff"),
            patch(
                "foundry.campaign_runtime.repository_identity",
                return_value="owner/repository",
            ),
            patch(
                "foundry.campaign_runtime.AcceptanceProofStore",
                return_value=BlockedProofStore(),
            ),
            patch(
                "foundry.campaign_runtime.foundry.tracker",
                return_value=SimpleNamespace(
                    get_issue=lambda _issue_id: SimpleNamespace(
                        id="DEVHUB-21", body="- [ ] criterion",
                    ),
                ),
            ),
        ):
            with patch("foundry.campaign_review_remediation.time.time", return_value=10):
                campaign_review_remediation_main([
                    item.id, "DEVHUB-21", "--attempt", "1", "--actor", "operator-1",
                    "--valid-seconds", "60", "--state-dir", str(state),
                    "--root", str(tmp_path),
                ])
            granted = json.loads(capsys.readouterr().out)
            with patch("foundry.campaign_review_remediation.time.time", return_value=11):
                campaign_review_remediation_main([
                    item.id, "DEVHUB-21", "--attempt", "1", "--actor", "operator-1",
                    "--valid-seconds", "60", "--state-dir", str(state),
                    "--root", str(tmp_path),
                ])
        assert read_pr.call_count == 4
        assert json.loads(capsys.readouterr().out) == granted
        assert CountingExecutor.attempts == [1]

        resumed = provider.resume(
            item, blocked, authorization, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

        with sqlite3.connect(campaign_store.path) as connection:
            campaign_before_stale_replay = tuple(connection.iterdump())
        escalation_path = EscalationStore(
            "runtime-review-remediation", state_dir=escalation_dir,
        )._path("DEVHUB-21")
        escalation_before_stale_replay = escalation_path.read_bytes()
        with patch.object(
            FoundryPrimitiveRunner, "revalidate_review_remediation",
            autospec=True, side_effect=lambda _runner, target: target,
        ) as stale_revalidate:
            with patch("foundry.campaign_review_remediation.time.time", return_value=12):
                with pytest.raises(CampaignError, match="première review"):
                    campaign_review_remediation_main([
                        item.id, "DEVHUB-21", "--attempt", "1",
                        "--actor", "operator-1", "--valid-seconds", "60",
                        "--state-dir", str(state), "--root", str(tmp_path),
                    ])
        assert stale_revalidate.call_count == 0
        assert capsys.readouterr().out == ""
        with sqlite3.connect(campaign_store.path) as connection:
            assert tuple(connection.iterdump()) == campaign_before_stale_replay
        assert escalation_path.read_bytes() == escalation_before_stale_replay

    assert granted["contract"] == "foundry-campaign-first-review-remediation.v1"
    assert granted["role"] == "implementer"
    assert granted["current_tier"] == "frontier"
    assert resumed.status == second_review_status.replace("blocked", "paused")
    assert CountingExecutor.attempts == [1, 2]
    assert FirstReviewBlockedPrimitives.review_attempts == [1, 2]
    assert campaign_store.issue(item.id, "DEVHUB-21")["attempt"] == 2
    with sqlite3.connect(campaign_store.path) as connection:
        retries = connection.execute(
            "SELECT COUNT(*), SUM(allowed) FROM campaign_retry_decisions "
            "WHERE issue_id = 'DEVHUB-21' AND step = 'review'",
        ).fetchone()
        grants = connection.execute(
            "SELECT from_attempt FROM campaign_review_remediation_authorizations",
        ).fetchall()
    assert retries == ((2, 1) if second_review_status == "completed" else (3, 1))
    assert grants == [(1,)]
    if second_review_status == "blocked":
        second_review = campaign_store.effect(item.id, "DEVHUB-21", 2, "review")
        escalation = EscalationStore(
            "runtime-review-remediation", state_dir=escalation_dir,
        )
        ledger = json.loads(escalation._path("DEVHUB-21").read_text())
        failure = ledger["failure_receipts"][second_review[0]]
        assert failure["kind"] == "review_blocking_after_fix"
        assert failure["decision"]["signal"] == "review_blocking_after_fix"
        assert failure["decision"]["action"] == "escalated"
        assert len(ledger["campaign_review_retry_receipts"]) == 1
        with patch("foundry.campaign_review_remediation.time.time", return_value=12):
            with pytest.raises(CampaignError, match="première review"):
                campaign_review_remediation_main([
                    item.id, "DEVHUB-21", "--attempt", "2",
                    "--actor", "operator-1", "--valid-seconds", "60",
                    "--state-dir", str(state),
                ])
        assert CountingExecutor.attempts == [1, 2]
        assert campaign_store.issue(item.id, "DEVHUB-21")["attempt"] == 2


@pytest.mark.parametrize(
    ("control", "status"),
    (("pause-requested", "paused"), ("cancel-requested", "cancelled")),
)
def test_production_control_confirms_suspended_blocker_without_manual_retry(
    tmp_path, control, status,
):
    class PersistentBlockedExecutor(FakeExecutor):
        proposals = {}
        envelopes = []

        def resolve(self, work_id):
            return self.proposals.get(work_id)

        def execute(self, envelope):
            self.envelopes.append(envelope)
            proposal = ImplementationProposal(
                envelope.work_id, envelope.issue_id, envelope.attempt, "blocked",
                _digest([envelope.work_id, "blocked"]), 30, envelope.selected_tier,
            )
            self.proposals[envelope.work_id] = proposal
            return proposal

    PersistentBlockedExecutor.proposals.clear()
    PersistentBlockedExecutor.envelopes.clear()
    item = command()
    controlled = replace(item, state=control)
    state_directory = tmp_path / "state"
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(), root=tmp_path, state_directory=state_directory,
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=PersistentBlockedExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        blocked = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )
        confirmed = provider.resume(
            item, blocked, authorization, heartbeat=lambda: controlled,
            reconcile_capacity=lambda _observed: None,
        )

    assert blocked.status == "paused"
    assert confirmed.status == status
    assert [envelope.attempt for envelope in PersistentBlockedExecutor.envelopes] == [1]


def test_production_ambiguous_implementation_stays_suspended_without_duplicate(tmp_path):
    class AmbiguousExecutor(FakeExecutor):
        envelopes = []

        def resolve(self, _work_id):
            return None

        def reconcile(self, envelope):
            return ImplementationReconciliation(
                envelope.work_id, "unknown",
                _digest([envelope.effect_id, "unknown"]), 0,
            )

        def execute(self, envelope):
            self.envelopes.append(envelope)
            raise TimeoutError("provider receipt unknown")

    AmbiguousExecutor.envelopes.clear()
    item = command()
    state = tmp_path / "state"
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            FakeSource(), root=tmp_path, state_directory=state,
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=AmbiguousExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        first = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )
        store = CampaignStore(state / "campaigns" / item.id / "campaign.sqlite3")
        with pytest.raises(CampaignError, match="blocker d'implémentation exact absent"):
            store.authorize_manual_retry(
                item.id, "DEVHUB-21", 1, actor="operator-1", now_ms=10_000,
            )
        second = provider.resume(
            item, first, authorization, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )
        third = provider.resume(
            item, second, authorization, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

    assert first.status == "paused"
    assert second.status == third.status == "paused"
    assert len(AmbiguousExecutor.envelopes) == 1
    assert store.campaign(item.id).reason == "implementation_unknown"
    assert store.issue(item.id, "DEVHUB-21")["attempt"] == 1


def test_isolated_executor_has_no_shell_plugin_mcp_or_foundry_secrets(tmp_path, monkeypatch):
    calls = []
    routing_config = tmp_path / ".foundry" / "model-routing.json"
    routing_config.parent.mkdir()
    routing_config.write_text(json.dumps({
        "claude_models": {"future-campaign-model": "campaign-wire-alias"},
    }), encoding="utf-8")

    class Worktrees:
        def worktree(self, _campaign_id, _issue_id):
            return tmp_path

        def capture_implementation_intent(self, envelope, *, outcome):
            return _digest([
                envelope.effect_id, envelope.work_id, envelope.binding_digest, outcome,
            ])

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "is_error": False, "total_cost_usd": 0.25, "duration_ms": 100,
                "structured_output": {"outcome": "completed"},
            }),
        )

    ambient_credentials = {
        "YOUTRACK_TOKEN", "DEVHUB_TRACKER_TOKEN", "DEVHUB_COMMAND_TOKEN",
        "DEVHUB_TRACKER_PROOF_SECRET", "FOUNDRY_CONFIG",
        "GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN", "UNREVIEWED_FUTURE_PROVIDER_CREDENTIAL",
    }
    for name in ambient_credentials:
        monkeypatch.setenv(name, "secret")
    monkeypatch.setenv("USER", "foundry-child")
    monkeypatch.setenv("LOGNAME", "foundry-child")
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "implementation", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, "frontier", 100,
    )
    with (
        patch("foundry.campaign_runtime.foundry.tracker") as tracker,
        patch("foundry.campaign_runtime.git_head", return_value=HEAD),
        patch("foundry.campaign_runtime.git_diff", return_value=b"diff"),
    ):
        tracker.return_value.get_issue.return_value = SimpleNamespace(
            id="DEVHUB-21", title="Focused issue", body="Implement the bounded change.",
        )
        executor = IsolatedClaudeIssueExecutor(
            tmp_path, Worktrees(), proposal_directory=tmp_path / "proposals",
            selected_tier="frontier", cost_ceiling_cents=100, runner=runner,
        )
        proposal = executor.execute(envelope)

    assert proposal.outcome == "completed"
    argv, kwargs = calls[0]
    assert argv[argv.index("--model") + 1] == "opus"
    assert "opus-5" not in argv
    assert argv[argv.index("--tools") + 1] == "Read,Edit,Write,Grep,Glob"
    assert "--safe-mode" in argv and "--disable-slash-commands" in argv
    assert "--no-session-persistence" in argv
    assert "--plugin-dir" not in argv and "Bash" not in argv
    assert kwargs["env"]["FOUNDRY_RUNTIME_CONFIG_ISOLATED"] == "1"
    assert kwargs["env"]["USER"] == "foundry-child"
    assert kwargs["env"]["LOGNAME"] == "foundry-child"
    assert ambient_credentials.isdisjoint(kwargs["env"])
    assert set(kwargs["env"]) <= {
        "HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
        "FOUNDRY_RUNTIME_CONFIG_ISOLATED",
    }


def test_campaign_child_environment_has_an_exact_non_secret_allowlist(monkeypatch):
    names = ("HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER")
    expected = {name: f"value-{name}" for name in names}
    for name, value in expected.items():
        monkeypatch.setenv(name, value)

    environment = _child_environment()

    assert environment == {
        **expected,
        "FOUNDRY_RUNTIME_CONFIG_ISOLATED": "1",
    }


@pytest.mark.parametrize("missing", ["USER", "LOGNAME"])
def test_campaign_child_environment_omits_each_absent_local_identity(
    monkeypatch, missing,
):
    monkeypatch.setenv("USER", "foundry-user")
    monkeypatch.setenv("LOGNAME", "foundry-logname")
    monkeypatch.delenv(missing)

    environment = _child_environment()

    assert missing not in environment
    present = "LOGNAME" if missing == "USER" else "USER"
    assert environment[present] == f"foundry-{present.lower()}"


def test_isolated_executor_reconciles_a_proven_prelaunch_provider_exception(tmp_path):
    class Worktrees:
        def worktree(self, _campaign_id, _issue_id):
            return tmp_path

    def unavailable_runner(_argv, **_kwargs):
        raise FileNotFoundError("provider executable absent")

    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "implementation", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, "frontier", 100,
    )
    with (
        patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()),
        patch("foundry.campaign_runtime.foundry.tracker") as tracker,
    ):
        tracker.return_value.get_issue.return_value = SimpleNamespace(
            id="DEVHUB-21", title="Focused issue", body="Bounded change.",
        )
        executor = IsolatedClaudeIssueExecutor(
            tmp_path, Worktrees(), proposal_directory=tmp_path / "proposals",
            selected_tier="frontier", cost_ceiling_cents=100,
            runner=unavailable_runner,
        )
        with pytest.raises(FileNotFoundError):
            executor.execute(envelope)

    assert executor.resolve(envelope.work_id) is None
    reconciliation = executor.reconcile(envelope)
    assert reconciliation.state == "not-engaged"
    assert reconciliation.cost_cents == 0
    assert reconciliation.work_id == envelope.work_id


def test_persistent_campaign_profile_tightens_into_coordinator_batching(tmp_path):
    class TighteningSource(FakeSource):
        def __init__(self):
            super().__init__()
            self.active = 1

        def load(self, _command):
            return SimpleNamespace(
                observation=replace(observation(), active_concurrency=self.active),
                waves=(("DEVHUB-21", "DEVHUB-22", "DEVHUB-23"),), blockers=(),
                acceptance_mapping=tuple(
                    (f"ac-{index}", _digest(["criterion", index]), issue_id)
                    for index, issue_id in enumerate(
                        ("DEVHUB-21", "DEVHUB-22", "DEVHUB-23"), start=1,
                    )
                ),
            )

    source = TighteningSource()
    item = command()
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=FakeExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        initial = provider.execution_profile(item, None)
        source.active = 2
        tightened = provider.execution_profile(item, EffectReceipt(
            item.id, "paused", DIGEST_A, 0, 0,
        ))
        source.active = 1
        not_widened = provider.execution_profile(item, EffectReceipt(
            item.id, "paused", DIGEST_A, 0, 0,
        ))
        source.active = 2
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), tightened.selected_tier,
            tightened.cost_ceiling_cents, tightened.concurrency_units,
            tightened.cost_ceiling_cents, 3, "frontier",
            tightened.provider_invocation_ceiling_cents,
        )
        composition = provider._composition(
            item, authorization, heartbeat=lambda: item,
        )

    assert initial.concurrency_units == 2
    assert tightened.concurrency_units == 1
    assert not_widened.concurrency_units == 1
    assert initial.provider_invocation_ceiling_cents == 150
    assert tightened.provider_invocation_ceiling_cents == 150
    assert not_widened.provider_invocation_ceiling_cents == 150
    assert composition.spec.max_concurrency == item.max_concurrency == 4
    assert composition.coordinator.pipeline.observe(composition.spec).max_concurrency == 1
    assert composition.coordinator.executor.cost_ceiling_cents == 150


def test_campaign_launch_separates_aggregate_budget_from_provider_call_ceiling(tmp_path):
    executors = []

    def executor_factory(*args, **kwargs):
        executor = FakeExecutor(*args, **kwargs)
        executors.append(executor)
        return executor

    FakePrimitives.instances.clear()
    item = replace(command(), max_cost_cents=2_000, max_concurrency=2)
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            BudgetCapacitySource(), root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=executor_factory,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        effect = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

    assert profile.cost_ceiling_cents == 2_000
    assert profile.concurrency_units == 2
    assert profile.provider_invocation_ceiling_cents == 1_000
    persisted = json.loads(
        (tmp_path / "state" / "profiles" / f"{item.id}.json").read_text(
            encoding="utf-8",
        ),
    )
    assert persisted["cost_ceiling_cents"] == 2_000
    assert persisted["provider_invocation_ceiling_cents"] == 1_000
    assert executors[0].cost_ceiling_cents == 1_000
    assert effect.status == "completed"


def test_later_campaign_invocation_uses_freshly_tightened_call_ceiling(tmp_path):
    class TwoWaveCapacitySource(BudgetCapacitySource):
        def load(self, _command):
            return SimpleNamespace(
                observation=replace(
                    observation(), local_max_cost_cents=2_000,
                    budget_remaining_cents=self.remaining,
                    local_max_concurrency=2, active_concurrency=0,
                    provider_invocation_ceiling_cents=1_000,
                ),
                waves=(("DEVHUB-21",), ("DEVHUB-22",)), blockers=(),
                acceptance_mapping=(
                    ("ac-1", _digest(["criterion", 1]), "DEVHUB-21"),
                    ("ac-2", _digest(["criterion", 2]), "DEVHUB-22"),
                ),
            )

    source = TwoWaveCapacitySource()
    invocation_ceilings = []

    class SpendingExecutor(FakeExecutor):
        def execute(self, envelope):
            invocation_ceilings.append(envelope.cost_ceiling_cents)
            proposal = ImplementationProposal(
                envelope.work_id, envelope.issue_id, envelope.attempt, "completed",
                _digest([envelope.work_id, "proposal"]), 27,
                envelope.selected_tier,
            )
            self.proposals[envelope.work_id] = proposal
            if len(invocation_ceilings) == 1:
                source.remaining = 1_973
            return proposal

    item = replace(command(), max_cost_cents=2_000, max_concurrency=2)
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=SpendingExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        effect = provider.launch(
            item, authorization, effect_id=item.id, heartbeat=lambda: item,
            reconcile_capacity=lambda _observed: None,
        )

    assert effect.status == "completed"
    assert invocation_ceilings == [1_000, 986]
    assert all(ceiling <= 1_000 for ceiling in invocation_ceilings)
    assert invocation_ceilings[1] <= source.remaining


def test_campaign_profile_keeps_explicit_call_ceiling_separate_from_budget(tmp_path):
    item = replace(command(), max_cost_cents=2_000, max_concurrency=2)
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            BudgetCapacitySource(remaining=1_973), root=tmp_path,
            state_directory=tmp_path / "state", owner_id="worker-1",
        )
        profile = provider.execution_profile(item, None)

    assert profile.cost_ceiling_cents == 1_973
    assert profile.concurrency_units == 2
    assert profile.provider_invocation_ceiling_cents == 1_000


def test_campaign_resume_rejects_legacy_profile_without_call_ceiling_before_effect(
    tmp_path,
):
    effects = []

    def forbidden_primitive(*_args, **_kwargs):
        effects.append("primitive")
        raise AssertionError("no primitive may be constructed")

    def forbidden_executor(*_args, **_kwargs):
        effects.append("executor")
        raise AssertionError("no executor may be constructed")

    def forbidden_subprocess(*_args, **_kwargs):
        effects.append("subprocess")
        raise AssertionError("no subprocess may run")

    source = BudgetCapacitySource()
    item = replace(command(), max_cost_cents=2_000, max_concurrency=2)
    state = tmp_path / "state"
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=state, owner_id="worker-1",
            primitive_runner=forbidden_primitive,
            executor_factory=forbidden_executor,
            implementation_runner=forbidden_subprocess,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        profile = provider.execution_profile(item, None)
        profile_path = state / "profiles" / f"{item.id}.json"
        legacy_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        del legacy_profile["provider_invocation_ceiling_cents"]
        profile_path.write_text(
            json.dumps(legacy_profile, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        durable_before = profile_path.read_bytes()
        source.remaining = 1_973
        authorization = ExecutionAuthorization(
            item.id, _binding_digest(item), profile.selected_tier,
            profile.cost_ceiling_cents, profile.concurrency_units,
            profile.cost_ceiling_cents, profile.concurrency_units, "frontier",
            profile.provider_invocation_ceiling_cents,
        )
        with pytest.raises(PreEffectCapacityError, match="sans plafond provider"):
            provider.resume(
                item, EffectReceipt(item.id, "running", DIGEST_A, 27, 1),
                authorization, heartbeat=lambda: effects.append("heartbeat"),
                reconcile_capacity=lambda _observed: None,
            )

    assert profile.provider_invocation_ceiling_cents == 1_000
    assert 1_973 // 2 == 986
    assert effects == []
    assert profile_path.read_bytes() == durable_before
    assert provider.resolve(item.id, _binding_digest(item)) is None


def test_worker_pauses_too_wide_durable_call_ceiling_at_zero_cost_before_effect(tmp_path):
    class Client:
        def __init__(self, item):
            self.item = item
            self.events = []

        def list_commands(self, _project, *, cursor, limit):
            return CommandPage((self.item,), None, False)

        def claim(self, item, *, worker_id, **_kwargs):
            self.item = replace(
                item, version=item.version + 1,
                lease=CommandLease("lease-1", worker_id, 30_000, 10_000),
            )
            return self.item

        def heartbeat(self, item, **_kwargs):
            self.item = replace(
                item, version=item.version + 1,
                lease=replace(
                    item.lease, expires_at=item.lease.expires_at + 1_000,
                    heartbeat_at=item.lease.heartbeat_at + 1,
                ),
            )
            return self.item

        def append_event(self, item, *, event_type, sequence, **_kwargs):
            self.events.append(event_type)
            self.item = replace(
                item, state=event_type, version=item.version + 1,
                next_event_sequence=sequence + 1,
            )
            return self.item

        def list_events(self, *_args, **_kwargs):
            raise AssertionError("no ambiguous event expected")

    effects = []

    def forbidden(kind):
        def fail(*_args, **_kwargs):
            effects.append(kind)
            raise AssertionError(f"no {kind} may run")
        return fail

    source = BudgetCapacitySource()
    item = replace(command(), max_cost_cents=2_000, max_concurrency=2)
    state = tmp_path / "state"
    client = Client(item)
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=state, owner_id="worker-1",
            primitive_runner=forbidden("primitive"),
            executor_factory=forbidden("executor"),
            implementation_runner=forbidden("subprocess"),
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        provider.execution_profile(item, None)
        profile_path = state / "profiles" / f"{item.id}.json"
        durable_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        durable_profile["provider_invocation_ceiling_cents"] = 1_500
        profile_path.write_text(
            json.dumps(durable_profile, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        profile_before = profile_path.read_bytes()
        outer_store = ReceiptStore(tmp_path / "outer-receipts.sqlite3")
        outcome = CommandWorker(
            client, outer_store, provider, worker_id="worker-1", lease_seconds=60,
            now_ms=lambda: 10_000,
        ).run_once("DEVHUB")[0]

    assert (outcome.status, outcome.detail) == ("paused", "pre_effect_paused")
    assert client.events == ["accepted", "running", "paused"]
    durable = outer_store.get(item.id)
    assert durable.effect is not None
    assert (durable.effect.status, durable.effect.cost_cents) == ("paused", 0)
    assert effects == []
    assert provider.resolve(item.id, _binding_digest(item)) is None
    assert profile_path.read_bytes() == profile_before


def test_file_authority_zero_budget_reconciles_only_the_engaged_campaign_effect(
    tmp_path,
):
    class Client:
        def __init__(self, item):
            self.item = item
            self.events = []

        def list_commands(self, _project, *, cursor, limit):
            return CommandPage((self.item,), None, False)

        def claim(self, item, *, worker_id, **_kwargs):
            self.item = replace(
                item, version=item.version + 1,
                lease=CommandLease("lease-1", worker_id, 30_000, 10_000),
            )
            return self.item

        def heartbeat(self, item, **_kwargs):
            self.item = replace(
                item, version=item.version + 1,
                lease=replace(
                    item.lease, expires_at=item.lease.expires_at + 1_000,
                    heartbeat_at=item.lease.heartbeat_at + 1,
                ),
            )
            return self.item

        def append_event(self, item, *, event_type, sequence, **_kwargs):
            self.events.append(event_type)
            self.item = replace(
                item, state=event_type, version=item.version + 1,
                next_event_sequence=sequence + 1,
            )
            return self.item

        def list_events(self, *_args, **_kwargs):
            raise AssertionError("no ambiguous event expected")

    class AmbiguousAfterProposalExecutor(FakeExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.execute_calls = 0

        def execute(self, envelope):
            self.execute_calls += 1
            super().execute(envelope)
            raise TimeoutError("proposal receipt lost after provider engagement")

    item, document, authority_path = file_authority(tmp_path)
    source = FileAuthoritySource(authority_path.parent, now_ms=lambda: 10_000)
    client = Client(item)
    outer_store = ReceiptStore(tmp_path / "outer-receipts.sqlite3")
    FakePrimitives.instances.clear()
    executor = AmbiguousAfterProposalExecutor(
        tmp_path, None, selected_tier="frontier", cost_ceiling_cents=300,
    )
    calls = {"launch": 0, "resume": 0, "reconcile": 0}
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=lambda *_args, **_kwargs: executor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        launch = provider.launch
        resume = provider.resume
        reconcile = provider.reconcile_existing

        def counted_launch(*args, **kwargs):
            calls["launch"] += 1
            return launch(*args, **kwargs)

        def counted_resume(*args, **kwargs):
            calls["resume"] += 1
            return resume(*args, **kwargs)

        def counted_reconcile(*args, **kwargs):
            calls["reconcile"] += 1
            return reconcile(*args, **kwargs)

        provider.launch = counted_launch
        provider.resume = counted_resume
        provider.reconcile_existing = counted_reconcile
        subject = CommandWorker(
            client, outer_store, provider, worker_id="worker-1",
            lease_seconds=60, now_ms=lambda: 10_000,
        )

        first = subject.run_once("DEVHUB")[0]
        assert (first.status, first.detail) == ("paused", "effect_proven")
        assert calls == {"launch": 1, "resume": 0, "reconcile": 0}
        assert executor.execute_calls == 1
        assert client.events == ["accepted", "running", "paused"]
        campaign_store = CampaignStore(
            tmp_path / "state" / "campaigns" / item.id / "campaign.sqlite3",
        )
        engaged = campaign_store.effect(
            item.id, "DEVHUB-21", 1, "implementation",
        )
        assert engaged is not None and engaged[2] == "intent"
        assert campaign_store.implementation_engagement(engaged[0]) == "engaged"
        primitive_calls_before_reconciliation = sum(
            len(instance.calls) for instance in FakePrimitives.instances
        )

        document["local"]["budget_remaining_cents"] = 0
        authority_path.write_text(json.dumps(document), encoding="utf-8")
        assert source.load(client.item).observation.budget_remaining_cents == 0

        reconciled = subject.run_once("DEVHUB")[0]

        assert (reconciled.status, reconciled.detail) == (
            "paused", "effect_already_projected",
        )
        assert calls == {"launch": 1, "resume": 0, "reconcile": 1}
        assert executor.execute_calls == 1
        finished = campaign_store.effect(
            item.id, "DEVHUB-21", 1, "implementation",
        )
        assert finished is not None and finished[2] == "completed"
        campaign = campaign_store.campaign(item.id)
        assert (campaign.state, campaign.reason) == (
            "suspended", "budget_exhausted",
        )
        assert (campaign.budget_reserved, campaign.active_concurrency) == (0, 0)
        assert campaign_store.effect(
            item.id, "DEVHUB-22", 1, "implementation",
        ) is None
        assert sum(len(instance.calls) for instance in FakePrimitives.instances) == (
            primitive_calls_before_reconciliation
        )

        fresh, _fresh_document, _fresh_path = file_authority(
            tmp_path, command_id="command-2", budget_remaining=0,
        )
        fresh_client = Client(fresh)
        fresh_store = ReceiptStore(tmp_path / "fresh-outer-receipts.sqlite3")
        refused = CommandWorker(
            fresh_client, fresh_store, provider, worker_id="worker-1",
            lease_seconds=60, now_ms=lambda: 10_000,
        ).run_once("DEVHUB")[0]

    assert (refused.status, refused.detail) == ("paused", "pre_effect_paused")
    assert calls == {"launch": 1, "resume": 0, "reconcile": 1}
    assert executor.execute_calls == 1
    fresh_receipt = fresh_store.get(fresh.id)
    assert fresh_receipt is not None
    assert fresh_receipt.phase == "pre-effect-paused"
    assert not (tmp_path / "state" / "campaigns" / fresh.id).exists()
    assert sum(len(instance.calls) for instance in FakePrimitives.instances) == (
        primitive_calls_before_reconciliation
    )


def test_worker_launches_one_campaign_after_pre_effect_capacity_recovers(tmp_path):
    class RecoveringCapacitySource(FakeSource):
        def __init__(self):
            super().__init__()
            self.provider_ceiling = None

        def load(self, _command):
            return SimpleNamespace(
                observation=replace(
                    observation(),
                    provider_invocation_ceiling_cents=self.provider_ceiling,
                ),
                waves=(("DEVHUB-21",),), blockers=(),
                acceptance_mapping=(
                    ("ac-1", _digest(["criterion", 1]), "DEVHUB-21"),
                ),
            )

    class Client:
        def __init__(self, item):
            self.item = item
            self.events = []

        def list_commands(self, _project, *, cursor, limit):
            return CommandPage((self.item,), None, False)

        def claim(self, item, *, worker_id, **_kwargs):
            self.item = replace(
                item, version=item.version + 1,
                lease=CommandLease("lease-1", worker_id, 30_000, 10_000),
            )
            return self.item

        def heartbeat(self, item, **_kwargs):
            self.item = replace(
                item, version=item.version + 1,
                lease=replace(
                    item.lease, expires_at=item.lease.expires_at + 1_000,
                    heartbeat_at=item.lease.heartbeat_at + 1,
                ),
            )
            return self.item

        def append_event(self, item, *, event_type, sequence, **_kwargs):
            self.events.append(event_type)
            self.item = replace(
                item, state=event_type, version=item.version + 1,
                next_event_sequence=sequence + 1,
            )
            return self.item

        def list_events(self, *_args, **_kwargs):
            raise AssertionError("no ambiguous event expected")

    FakePrimitives.instances.clear()
    source = RecoveringCapacitySource()
    item = command()
    client = Client(item)
    store = ReceiptStore(tmp_path / "outer-receipts.sqlite3")
    calls = {"launch": 0, "resume": 0}
    with patch(
        "foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting(),
    ):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=tmp_path / "state",
            owner_id="worker-1", primitive_runner=FakePrimitives,
            executor_factory=FakeExecutor,
            escalation_store_factory=lambda _root: FakeEscalationStore(),
        )
        launch = provider.launch

        def counted_launch(*args, **kwargs):
            calls["launch"] += 1
            return launch(*args, **kwargs)

        def forbidden_resume(*_args, **_kwargs):
            calls["resume"] += 1
            raise AssertionError("an uncreated campaign cannot be resumed")

        provider.launch = counted_launch
        provider.resume = forbidden_resume
        provider.verified_campaign_terminal_evidence = lambda *_args: (False, None)
        subject = CommandWorker(
            client, store, provider, worker_id="worker-1", lease_seconds=60,
            now_ms=lambda: 10_000,
        )

        paused = subject.run_once("DEVHUB")[0]

        assert (paused.status, paused.detail) == ("paused", "pre_effect_paused")
        assert calls == {"launch": 0, "resume": 0}
        assert client.events == ["accepted", "running", "paused"]
        durable_pause = store.get(item.id)
        assert durable_pause is not None and durable_pause.effect is not None
        assert durable_pause.phase == "pre-effect-paused"
        assert (durable_pause.effect.cost_cents, durable_pause.effect.duration_ms) == (0, 0)
        assert provider.resolve(item.id, _binding_digest(item)) is None
        assert not (tmp_path / "state" / "campaigns" / item.id).exists()

        still_paused = subject.run_once("DEVHUB")[0]

        assert (still_paused.status, still_paused.detail) == (
            "paused", "pre_effect_pause_retained",
        )
        assert calls == {"launch": 0, "resume": 0}
        assert client.events == ["accepted", "running", "paused"]
        assert not (tmp_path / "state" / "campaigns" / item.id).exists()

        source.provider_ceiling = 150
        completed = subject.run_once("DEVHUB")[0]

        assert (completed.status, completed.detail) == ("completed", "effect_proven")
        assert calls == {"launch": 1, "resume": 0}
        assert client.events == [
            "accepted", "running", "paused", "running", "completed",
        ]
        campaign = CampaignStore(
            tmp_path / "state" / "campaigns" / item.id / "campaign.sqlite3",
        ).campaign(item.id)
        assert campaign.state == "completed"
        assert FakePrimitives.instances[-1].calls == [
            ("DEVHUB-21", "start"), ("DEVHUB-21", "open-pr"),
            ("DEVHUB-21", "review"), ("DEVHUB-21", "ci"),
            ("DEVHUB-21", "human-gate"), ("DEVHUB-21", "merge"),
            ("DEVHUB-20", "sync-parent-acceptance"),
            ("DEVHUB-20", "close-epic"),
        ]

        assert subject.run_once("DEVHUB") == ()
        assert calls == {"launch": 1, "resume": 0}


def test_human_gate_requires_an_explicit_exact_unexpired_receipt(tmp_path):
    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "human-gate", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )
    assert store.human_approval(
        envelope, head_sha=HEAD, review_digest=DIGEST_B,
        ci_digest=DIGEST_C, now_ms=10_000,
    ) is None

    approval = store.approve_human_gate(
        envelope, head_sha=HEAD, review_digest=DIGEST_B, ci_digest=DIGEST_C,
        actor="human-1", expires_at=11_000, now_ms=10_000,
    )
    assert store.approve_human_gate(
        envelope, head_sha=HEAD, review_digest=DIGEST_B, ci_digest=DIGEST_C,
        actor="human-1", expires_at=11_000, now_ms=10_100,
    ) == approval

    assert store.human_approval(
        envelope, head_sha=HEAD, review_digest=DIGEST_B,
        ci_digest=DIGEST_C, now_ms=10_500,
    ) == approval
    assert store.human_approval(
        envelope, head_sha=HEAD, review_digest=DIGEST_B,
        ci_digest=DIGEST_C, now_ms=11_000,
    ) is None


def test_parent_snapshot_advancement_recovers_after_tracker_mutation_before_receipt(
    tmp_path, monkeypatch,
):
    body_before = (
        "## Critères d acceptation\n"
        "- [ ] enfant un livré\n"
        "- [ ] enfant deux livré\n"
        "- [ ] enfant trois livré\n"
    )

    class Tracker:
        name = "devhub"
        acceptance_sync_supported = True
        requires_mutation_binding = False

        def __init__(self):
            self.parent = SimpleNamespace(
                id="DEVHUB-20", type="Epic", body=body_before, version=4,
                state="in-progress",
                links=[
                    SimpleNamespace(type="parent-of", target=issue_id)
                    for issue_id in ("DEVHUB-21", "DEVHUB-22", "DEVHUB-23")
                ],
            )
            self.mutations = 0

        def get_issue(self, _issue_id):
            return self.parent

        def sync_acceptance_body(
            self, issue_id, expected_body, updated_body, _proof_id, **_kwargs,
        ):
            assert issue_id == self.parent.id
            assert expected_body == self.parent.body
            self.parent.body = updated_body
            self.parent.version += 1
            self.mutations += 1
            return True

    tracker = Tracker()
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: tracker)
    state = tmp_path / "state"
    campaign_dir = state / "campaigns" / "command-1"
    effects = CampaignEffectStore(campaign_dir / "primitive-receipts.sqlite3")
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=state / "worktrees", receipts=effects,
        now_ms=lambda: 10_000,
    )
    spec = CampaignSpec(
        campaign_id="command-1", command_id="command-1", project="DEVHUB",
        epic_id="DEVHUB-20", preview_id="preview-1", approval_id="approval-1",
        planning_version_id="DEVHUB-VERSION-1", preview_digest=DIGEST_A,
        snapshot_digest=DIGEST_B, policy_digest=DIGEST_C,
        waves=(("DEVHUB-21", "DEVHUB-22", "DEVHUB-23"),), dependencies=(),
        max_concurrency=4, budget_cents=500, expires_at=20_000,
        minimum_tier="frontier",
    )
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id, spec.epic_id, 1,
        "sync-parent-acceptance", effect_id, work_id,
        "lease-parent-sync", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    coordinator_store = CampaignStore(campaign_dir / "campaign.sqlite3")
    coordinator_store.initialize(spec, now_ms=10_000)
    children = seed_completed_merge_evidence(spec, coordinator_store, effects)
    criteria = acceptance_criteria(body_before)
    mapping = tuple(reversed((
        (criteria[0]["id"], criteria[0]["digest"], "DEVHUB-21"),
        (criteria[1]["id"], criteria[1]["digest"], "DEVHUB-22"),
        (criteria[2]["id"], criteria[2]["digest"], "DEVHUB-23"),
    )))
    proof = ParentAcceptanceProof(
        spec.campaign_id, spec.epic_id, spec.binding_digest, spec.snapshot_digest,
        4, children, mapping,
    )
    unknown = replace(
        proof,
        mapping=(
            ("ac-unknown", DIGEST_A, "DEVHUB-21"),
            proof.mapping[1],
            proof.mapping[0],
        ),
    )
    with pytest.raises(CampaignError, match="mapping AC parent"):
        runner.prepare_parent_acceptance(envelope, unknown)
    with pytest.raises(ValueError, match="mapping AC parent approuve contradictoire"):
        ParentAcceptanceProof(
            spec.campaign_id, spec.epic_id, spec.binding_digest,
            spec.snapshot_digest, 4, children,
            (
                (criteria[0]["id"], criteria[0]["digest"], "DEVHUB-21"),
                (criteria[0]["id"], criteria[0]["digest"], "DEVHUB-22"),
                (criteria[2]["id"], criteria[2]["digest"], "DEVHUB-23"),
            ),
        )
    advancement = runner.prepare_parent_acceptance(envelope, proof)
    assert tuple(item[:3] for item in advancement.criteria) == (
        (criteria[0]["id"], criteria[0]["digest"], "DEVHUB-21"),
        (criteria[1]["id"], criteria[1]["digest"], "DEVHUB-22"),
        (criteria[2]["id"], criteria[2]["digest"], "DEVHUB-23"),
    )
    coordinator_store.begin_effect(envelope, selected_tier=None, cost_ceiling=0)
    coordinator_store.begin_parent_snapshot_advancement(advancement, now_ms=10_000)

    original_record = effects.record
    crashed = False

    def crash_before_receipt(candidate, receipt, **kwargs):
        nonlocal crashed
        if candidate.step == "sync-parent-acceptance" and not crashed:
            crashed = True
            raise OSError("crash after provider mutation")
        return original_record(candidate, receipt, **kwargs)

    monkeypatch.setattr(effects, "record", crash_before_receipt)
    with pytest.raises(OSError, match="after provider mutation"):
        runner.sync_parent_acceptance(envelope, proof, advancement)
    assert tracker.parent.version == 5
    assert tracker.mutations == 1
    assert effects.parent_snapshot_advancement(envelope)[1] == "intent"

    monkeypatch.setattr(effects, "record", original_record)
    receipt = runner.resolve_effect(envelope)
    assert receipt is not None and receipt.status == "completed"
    assert tracker.mutations == 1
    assert effects.parent_snapshot_advancement(envelope)[1] == "completed"
    coordinator_store.finish_parent_snapshot_advancement(
        advancement, receipt, now_ms=10_001,
    )

    class BoundParentSource(FakeSource):
        def load(self, _command):
            return SimpleNamespace(
                observation=observation(), waves=spec.waves, blockers=(),
                acceptance_mapping=proof.mapping,
            )

    with patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()):
        provider = CampaignCommandEffectProvider(
            BoundParentSource(), root=tmp_path, state_directory=state,
            owner_id="worker-1",
        )
    observed = provider.observe(command())
    assert observed.epic_version == 4
    assert observed.snapshot_advancement is not None
    assert observed.snapshot_advancement.to_version == 5
    assert observed.snapshot_advancement.primitive_receipt_digest == receipt.proof_digest
    runner.verify_parent_snapshot_advancement(spec.campaign_id)

    forged = replace(
        proof,
        children=(*proof.children[:-1], (
            proof.children[-1][0], proof.children[-1][1], DIGEST_A,
        )),
    )
    with pytest.raises(CampaignError, match="mapping preuve"):
        runner.sync_parent_acceptance(envelope, forged, advancement)

    remapped = replace(
        proof,
        mapping=(
            (*proof.mapping[0][:2], proof.mapping[1][2]),
            (*proof.mapping[1][:2], proof.mapping[0][2]),
            proof.mapping[2],
        ),
    )
    with pytest.raises(CampaignError, match="mapping preuve"):
        runner.sync_parent_acceptance(envelope, remapped, advancement)

    removed_link = tracker.parent.links.pop()
    with pytest.raises(
        CampaignRevalidationError, match="parent_snapshot_advancement_conflict",
    ):
        runner.verify_parent_snapshot_advancement(spec.campaign_id)
    tracker.parent.links.append(removed_link)

    tracker.parent.body += "\nmutation externe\n"
    tracker.parent.version += 1
    with pytest.raises(
        CampaignRevalidationError, match="parent_snapshot_advancement_conflict",
    ):
        runner.verify_parent_snapshot_advancement(spec.campaign_id)


def test_parent_acceptance_binds_cross_order_mapping_by_criterion_key(
    tmp_path, monkeypatch,
):
    body = (
        "## Critères d acceptation\n"
        "- [ ] premier critère livré\n"
        "- [ ] second critère livré\n"
    )

    class Tracker:
        name = "devhub"
        acceptance_sync_supported = True
        requires_mutation_binding = False

        def __init__(self):
            self.parent = SimpleNamespace(
                id="APP-1", type="Epic", body=body, version=4,
                state="in-progress",
                links=[
                    SimpleNamespace(type="parent-of", target=issue_id)
                    for issue_id in ("APP-2", "APP-3")
                ],
            )
            self.mutations = 0

        def get_issue(self, _issue_id):
            return self.parent

        def sync_acceptance_body(
            self, issue_id, expected_body, updated_body, _proof_id, **_kwargs,
        ):
            assert issue_id == self.parent.id
            assert expected_body == self.parent.body
            self.parent.body = updated_body
            self.parent.version += 1
            self.mutations += 1
            return True

    tracker = Tracker()
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: tracker)
    spec = CampaignSpec(
        campaign_id="campaign-cross-map", command_id="command-cross-map",
        project="APP", epic_id="APP-1", preview_id="preview-cross-map",
        approval_id="approval-cross-map", planning_version_id="APP-VERSION-1",
        preview_digest=DIGEST_A, snapshot_digest=DIGEST_B,
        policy_digest=DIGEST_C, waves=(("APP-2", "APP-3"),),
        dependencies=(), max_concurrency=2, budget_cents=100,
        expires_at=20_000, minimum_tier="frontier",
    )
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
        spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
        "lease-cross-map", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    criteria = acceptance_criteria(body)
    assert [item["id"] for item in criteria] == ["ac-1", "ac-2"]
    coordinator_store = CampaignStore(tmp_path / "campaign.sqlite3")
    coordinator_store.initialize(spec, now_ms=9_000)
    effects = CampaignEffectStore(tmp_path / "primitive-receipts.sqlite3")
    children = seed_completed_merge_evidence(spec, coordinator_store, effects)
    proof = ParentAcceptanceProof(
        spec.campaign_id, spec.epic_id, spec.binding_digest,
        spec.snapshot_digest, 4, children,
        (
            (criteria[0]["id"], criteria[0]["digest"], "APP-3"),
            (criteria[1]["id"], criteria[1]["digest"], "APP-2"),
        ),
    )
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=effects,
        now_ms=lambda: 10_000,
    )
    advancement = runner.prepare_parent_acceptance(envelope, proof)
    assert tuple((item[0], item[2]) for item in advancement.criteria) == (
        ("ac-1", "APP-3"), ("ac-2", "APP-2"),
    )

    coordinator_store.begin_effect(envelope, selected_tier=None, cost_ceiling=0)
    coordinator_store.begin_parent_snapshot_advancement(advancement, now_ms=9_001)
    receipt = runner.sync_parent_acceptance(envelope, proof, advancement)
    assert receipt.status == "completed"

    class ReconciliationPipeline:
        def observe(self, candidate):
            return CampaignObservation(
                campaign_id=candidate.campaign_id,
                command_id=candidate.command_id,
                project=candidate.project,
                epic_id=candidate.epic_id,
                preview_id=candidate.preview_id,
                approval_id=candidate.approval_id,
                approval_state="approved",
                planning_version_id=candidate.planning_version_id,
                preview_digest=candidate.preview_digest,
                snapshot_digest=candidate.snapshot_digest,
                policy_digest=candidate.policy_digest,
                expires_at=candidate.expires_at,
                observed_at=10_000,
                valid_until=11_000,
                max_concurrency=candidate.max_concurrency,
                budget_remaining_cents=candidate.budget_cents,
                minimum_tier="frontier",
                provider_invocation_ceiling_cents=100,
                epic_version=4,
                acceptance_mapping=proof.mapping,
            )

        def reconcile_parent_acceptance(self, candidate, durable):
            return runner.reconcile_parent_acceptance(candidate, durable)

        def verify_parent_acceptance_proof(self, candidate):
            effects.verify_parent_acceptance_proof(candidate)

    coordinator = CampaignCoordinator(
        coordinator_store, ReconciliationPipeline(), object(),
        owner_id="worker-cross-map", now_ms=lambda: 10_001,
    )
    recovered = coordinator._sync_parent_acceptance(spec)

    assert recovered == receipt
    assert tracker.mutations == 1
    assert coordinator_store.parent_snapshot_advancement(spec.campaign_id)[1] == "completed"


def test_parent_snapshot_advancement_reads_verify_stored_digest_and_snapshot_binding(
    tmp_path,
):
    spec = CampaignSpec(
        campaign_id="command-1", command_id="command-1", project="DEVHUB",
        epic_id="DEVHUB-20", preview_id="preview-1", approval_id="approval-1",
        planning_version_id="DEVHUB-VERSION-1", preview_digest=DIGEST_A,
        snapshot_digest=DIGEST_B, policy_digest=DIGEST_C,
        waves=(("DEVHUB-21",),), dependencies=(), max_concurrency=1,
        budget_cents=100, expires_at=20_000, minimum_tier="frontier",
    )
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
        spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
        "lease-parent", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    advancement = ParentSnapshotAdvancement(
        spec.campaign_id, spec.epic_id, effect_id, spec.binding_digest,
        spec.snapshot_digest, DIGEST_A, 4, 5, DIGEST_A, DIGEST_B,
        DIGEST_A, DIGEST_B,
        (("ac-1", DIGEST_A, "DEVHUB-21", "effect-merge-12345678", DIGEST_C),),
    )
    effects = CampaignEffectStore(tmp_path / "primitive.sqlite3")
    with pytest.raises(CampaignError, match="binding intent avancement parent"):
        effects.begin_parent_snapshot_advancement(
            replace(envelope, snapshot_digest=DIGEST_C), advancement, now_ms=10_000,
        )
    effects.begin_parent_snapshot_advancement(envelope, advancement, now_ms=10_000)

    with sqlite3.connect(effects.path) as connection:
        connection.execute(
            "UPDATE parent_snapshot_advancements SET advancement_digest = ?",
            (DIGEST_C,),
        )
    with pytest.raises(CampaignError, match="avancement parent contradictoire"):
        effects.parent_snapshot_advancement(envelope)
    with pytest.raises(CampaignError, match="avancement parent contradictoire"):
        effects.command_parent_snapshot_advancement(spec.campaign_id)

    with sqlite3.connect(effects.path) as connection:
        connection.execute(
            "UPDATE parent_snapshot_advancements SET advancement_digest = ?, "
            "binding_digest = ?",
            (advancement.advancement_digest, DIGEST_C),
        )
    with pytest.raises(CampaignError, match="avancement parent contradictoire"):
        effects.parent_snapshot_advancement(envelope)
    with pytest.raises(CampaignError, match="avancement parent contradictoire"):
        effects.command_parent_snapshot_advancement(spec.campaign_id)

    with sqlite3.connect(effects.path) as connection:
        raw = json.loads(connection.execute(
            "SELECT advancement_json FROM parent_snapshot_advancements",
        ).fetchone()[0])
        raw["from_body_digest"] = DIGEST_C
        connection.execute(
            "UPDATE parent_snapshot_advancements SET binding_digest = ?, "
            "advancement_digest = ?, "
            "advancement_json = ?",
            (
                advancement.binding_digest, advancement.advancement_digest,
                json.dumps(raw, sort_keys=True),
            ),
        )
    with pytest.raises(CampaignError, match="avancement parent contradictoire"):
        effects.parent_snapshot_advancement(envelope)

    coordinator = CampaignStore(tmp_path / "campaign.sqlite3")
    coordinator.initialize(spec, now_ms=10_000)
    coordinator.begin_parent_snapshot_advancement(advancement, now_ms=10_000)
    with sqlite3.connect(coordinator.path) as connection:
        connection.execute(
            "UPDATE campaign_parent_snapshot_advancements "
            "SET advancement_digest = ?",
            (DIGEST_C,),
        )
    with pytest.raises(CampaignError, match="digest avancement parent durable"):
        coordinator.parent_snapshot_advancement(spec.campaign_id)


def test_primitive_parent_intent_with_receipt_digest_fails_closed_before_replay(
    tmp_path, monkeypatch,
):
    spec = CampaignSpec(
        campaign_id="command-1", command_id="command-1", project="DEVHUB",
        epic_id="DEVHUB-20", preview_id="preview-1", approval_id="approval-1",
        planning_version_id="DEVHUB-VERSION-1", preview_digest=DIGEST_A,
        snapshot_digest=DIGEST_B, policy_digest=DIGEST_C,
        waves=(("DEVHUB-21",),), dependencies=(), max_concurrency=1,
        budget_cents=100, expires_at=20_000, minimum_tier="frontier",
    )
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
        spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
        "lease-parent", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    advancement = ParentSnapshotAdvancement(
        spec.campaign_id, spec.epic_id, effect_id, spec.binding_digest,
        spec.snapshot_digest, DIGEST_A, 4, 5, DIGEST_A, DIGEST_B,
        DIGEST_A, DIGEST_B,
        (("ac-1", DIGEST_A, "DEVHUB-21", "effect-merge-12345678", DIGEST_C),),
    )
    effects = CampaignEffectStore(tmp_path / "primitive.sqlite3")
    effects.begin_parent_snapshot_advancement(envelope, advancement, now_ms=10_000)
    with sqlite3.connect(effects.path) as connection:
        connection.execute(
            "UPDATE parent_snapshot_advancements SET receipt_digest = ? "
            "WHERE effect_id = ?",
            (DIGEST_C, effect_id),
        )
        before = connection.execute(
            "SELECT * FROM parent_snapshot_advancements WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()

    class NoTrackerRead:
        calls = 0

        def get_issue(self, _issue_id):
            self.calls += 1
            raise AssertionError("tracker must not be read for a contradictory intent")

    tracker = NoTrackerRead()
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: tracker)
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=effects,
        now_ms=lambda: 10_001,
    )
    receipt = StepReceipt(effect_id, "completed", DIGEST_B)
    close_effect_id, close_work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "close-epic",
    )
    close_envelope = replace(
        envelope, step="close-epic", effect_id=close_effect_id,
        work_id=close_work_id,
    )

    operations = (
        lambda: effects.parent_snapshot_advancement(envelope),
        lambda: effects.command_parent_snapshot_advancement(spec.campaign_id),
        lambda: effects.begin_parent_snapshot_advancement(
            envelope, advancement, now_ms=10_001,
        ),
        lambda: effects.finish_parent_snapshot_advancement(
            envelope, advancement, receipt, now_ms=10_001,
        ),
        lambda: runner.resolve_effect(envelope),
        lambda: runner.reconcile_parent_acceptance(envelope, advancement),
        lambda: runner.close_epic(close_envelope),
        lambda: runner.verify_parent_snapshot_advancement(spec.campaign_id),
    )
    for operation in operations:
        with pytest.raises(
            CampaignError, match="reçu primitive avancement parent contradictoire",
        ):
            operation()

    with sqlite3.connect(effects.path) as connection:
        after = connection.execute(
            "SELECT * FROM parent_snapshot_advancements WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()
        receipt_count = connection.execute(
            "SELECT COUNT(*) FROM primitive_receipts WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()[0]
    assert after == before
    assert receipt_count == 0
    assert tracker.calls == 0

    for status, receipt_digest, error in (
        ("completed", None, "reçu primitive avancement parent contradictoire"),
        ("unexpected", None, "état primitive avancement parent invalide"),
    ):
        with sqlite3.connect(effects.path) as connection:
            connection.execute(
                "UPDATE parent_snapshot_advancements "
                "SET status = ?, receipt_digest = ? WHERE effect_id = ?",
                (status, receipt_digest, effect_id),
            )
            invalid_before = connection.execute(
                "SELECT * FROM parent_snapshot_advancements WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
        for operation in operations:
            with pytest.raises(CampaignError, match=error):
                operation()
        with sqlite3.connect(effects.path) as connection:
            invalid_after = connection.execute(
                "SELECT * FROM parent_snapshot_advancements WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
        assert invalid_after == invalid_before
    assert tracker.calls == 0


def test_outer_reconciliation_rejects_self_consistent_forged_binding_before_primitive_intent(
    tmp_path,
):
    item = command()
    state = tmp_path / "state"
    campaign_dir = state / "campaigns" / item.id
    spec = CampaignSpec(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, preview_id=item.preview_id,
        approval_id=item.approval_id, planning_version_id=item.planning_version_id,
        preview_digest=item.preview_digest, snapshot_digest=item.snapshot_digest,
        policy_digest=item.policy_digest, waves=(("DEVHUB-21",),), dependencies=(),
        max_concurrency=item.max_concurrency, budget_cents=300, expires_at=19_000,
        scheduled_for=item.scheduled_for, minimum_tier="frontier",
    )
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
        spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
        "lease-parent", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    advancement = ParentSnapshotAdvancement(
        spec.campaign_id, spec.epic_id, effect_id, spec.binding_digest,
        spec.snapshot_digest, DIGEST_A, 4, 5, DIGEST_A, DIGEST_B,
        DIGEST_A, DIGEST_B,
        (("ac-1", DIGEST_A, "DEVHUB-21", "effect-merge-12345678", DIGEST_C),),
    )
    coordinator = CampaignStore(campaign_dir / "campaign.sqlite3")
    coordinator.initialize(spec, now_ms=10_000)
    coordinator.begin_effect(envelope, selected_tier=None, cost_ceiling=0)
    coordinator.begin_parent_snapshot_advancement(advancement, now_ms=10_000)
    CampaignEffectStore(campaign_dir / "primitive-receipts.sqlite3")

    forged = replace(advancement, binding_digest=DIGEST_A)
    assert forged.binding_digest != spec.binding_digest
    with sqlite3.connect(coordinator.path) as connection:
        connection.execute(
            "UPDATE campaign_parent_snapshot_advancements "
            "SET advancement_digest = ?, advancement_json = ?",
            (
                forged.advancement_digest,
                json.dumps(asdict(forged), sort_keys=True, separators=(",", ":")),
            ),
        )

    class NoExternalObservation:
        now_ms = staticmethod(lambda: 10_000)
        calls = 0

        def load(self, _command):
            self.calls += 1
            raise AssertionError("external observation must not run")

    source = NoExternalObservation()
    with patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=state, owner_id="worker-1",
        )

    with pytest.raises(CommandWorkerError, match="binding durable avancement snapshot"):
        provider.observe(item)
    assert source.calls == 0


def test_outer_reconciliation_rejects_self_consistent_forged_parent_evidence(
    tmp_path,
):
    item = command()
    state = tmp_path / "state"
    campaign_dir = state / "campaigns" / item.id
    spec = CampaignSpec(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, preview_id=item.preview_id,
        approval_id=item.approval_id, planning_version_id=item.planning_version_id,
        preview_digest=item.preview_digest, snapshot_digest=item.snapshot_digest,
        policy_digest=item.policy_digest, waves=(("DEVHUB-21",),), dependencies=(),
        max_concurrency=item.max_concurrency, budget_cents=300, expires_at=19_000,
        scheduled_for=item.scheduled_for, minimum_tier="frontier",
    )
    coordinator = CampaignStore(campaign_dir / "campaign.sqlite3")
    coordinator.initialize(spec, now_ms=10_000)
    effects = CampaignEffectStore(campaign_dir / "primitive-receipts.sqlite3")
    seed_completed_merge_evidence(spec, coordinator, effects)
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
        spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
        "lease-parent", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    coordinator.begin_effect(envelope, selected_tier=None, cost_ceiling=0)
    forged_child = "DEVHUB-99"
    forged_merge = "effect-forged-merge"
    forged_receipt = _digest([forged_merge, "completed"])
    forged_proof = ParentAcceptanceProof(
        spec.campaign_id, spec.epic_id, spec.binding_digest,
        spec.snapshot_digest, 4,
        ((forged_child, forged_merge, forged_receipt),),
        (("ac-1", DIGEST_A, forged_child),),
    )
    with pytest.raises(CampaignError, match="reçus primitive merge parent"):
        effects.verify_parent_acceptance_proof(forged_proof)
    forged = ParentSnapshotAdvancement(
        spec.campaign_id, spec.epic_id, effect_id, spec.binding_digest,
        spec.snapshot_digest, forged_proof.proof_id, 4, 5,
        DIGEST_A, DIGEST_B, DIGEST_A, DIGEST_B,
        (("ac-1", DIGEST_A, forged_child, forged_merge, forged_receipt),),
    )
    coordinator.begin_parent_snapshot_advancement(forged, now_ms=10_000)

    class NoExternalObservation:
        now_ms = staticmethod(lambda: 10_000)
        calls = 0

        def load(self, _command):
            self.calls += 1
            raise AssertionError("external observation must not run")

    class NoParentMutationRunner:
        constructions = 0

        def __init__(self, *_args, **_kwargs):
            type(self).constructions += 1
            raise AssertionError("parent primitive must not be constructed")

    source = NoExternalObservation()
    with patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=state, owner_id="worker-1",
            primitive_runner=NoParentMutationRunner,
        )

    with pytest.raises(CampaignError, match="mapping preuve avancement parent"):
        provider.observe(item)
    assert source.calls == 0
    assert NoParentMutationRunner.constructions == 0
    assert coordinator.parent_snapshot_advancement(item.id) == (
        forged, "intent", None,
    )


def test_outer_replay_rejects_self_consistent_permuted_preview_mapping(
    tmp_path,
):
    item = command()
    state = tmp_path / "state"
    campaign_dir = state / "campaigns" / item.id
    spec = CampaignSpec(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, preview_id=item.preview_id,
        approval_id=item.approval_id, planning_version_id=item.planning_version_id,
        preview_digest=item.preview_digest, snapshot_digest=item.snapshot_digest,
        policy_digest=item.policy_digest,
        waves=(("DEVHUB-21", "DEVHUB-22"),), dependencies=(),
        max_concurrency=item.max_concurrency, budget_cents=300, expires_at=19_000,
        scheduled_for=item.scheduled_for, minimum_tier="frontier",
    )
    coordinator = CampaignStore(campaign_dir / "campaign.sqlite3")
    coordinator.initialize(spec, now_ms=10_000)
    effects = CampaignEffectStore(campaign_dir / "primitive-receipts.sqlite3")
    children = seed_completed_merge_evidence(spec, coordinator, effects)
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
        spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
        "lease-parent", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    approved_mapping = (
        ("ac-1", DIGEST_A, "DEVHUB-21"),
        ("ac-2", DIGEST_B, "DEVHUB-22"),
    )
    proof = ParentAcceptanceProof(
        spec.campaign_id, spec.epic_id, spec.binding_digest,
        spec.snapshot_digest, 4, children, approved_mapping,
    )
    receipts = {child[0]: child[1:] for child in children}
    advancement = ParentSnapshotAdvancement(
        spec.campaign_id, spec.epic_id, effect_id, spec.binding_digest,
        spec.snapshot_digest, proof.proof_id, 4, 5,
        DIGEST_A, DIGEST_B, DIGEST_A, DIGEST_B,
        tuple(
            (criterion_id, criterion_digest, issue_id, *receipts[issue_id])
            for criterion_id, criterion_digest, issue_id in approved_mapping
        ),
    )
    coordinator.begin_effect(envelope, selected_tier=None, cost_ceiling=0)
    coordinator.begin_parent_snapshot_advancement(advancement, now_ms=10_000)

    permuted_mapping = (
        ("ac-1", DIGEST_A, "DEVHUB-22"),
        ("ac-2", DIGEST_B, "DEVHUB-21"),
    )
    permuted_proof = replace(proof, mapping=permuted_mapping)
    forged = replace(
        advancement,
        proof_id=permuted_proof.proof_id,
        criteria=tuple(
            (criterion_id, criterion_digest, issue_id, *receipts[issue_id])
            for criterion_id, criterion_digest, issue_id in permuted_mapping
        ),
    )
    with sqlite3.connect(coordinator.path) as connection:
        connection.execute(
            "UPDATE campaign_parent_snapshot_advancements "
            "SET advancement_digest = ?, advancement_json = ? WHERE campaign_id = ?",
            (
                forged.advancement_digest,
                json.dumps(asdict(forged), sort_keys=True, separators=(",", ":")),
                spec.campaign_id,
            ),
        )
        before = connection.execute(
            "SELECT * FROM campaign_parent_snapshot_advancements WHERE campaign_id = ?",
            (spec.campaign_id,),
        ).fetchone()

    class PreviewBoundSource:
        now_ms = staticmethod(lambda: 10_000)

        def __init__(self):
            self.calls = 0

        def load(self, _command):
            self.calls += 1
            return SimpleNamespace(
                observation=observation(), waves=spec.waves, blockers=(),
                acceptance_mapping=approved_mapping,
            )

    class NoParentMutationRunner:
        constructions = 0

        def __init__(self, *_args, **_kwargs):
            type(self).constructions += 1
            raise AssertionError("parent primitive must not be constructed")

    source = PreviewBoundSource()
    with patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=state, owner_id="worker-1",
            primitive_runner=NoParentMutationRunner,
        )

    with pytest.raises(CampaignError, match="preuve avancement parent durable modifiee"):
        provider.observe(item)

    with sqlite3.connect(coordinator.path) as connection:
        after = connection.execute(
            "SELECT * FROM campaign_parent_snapshot_advancements WHERE campaign_id = ?",
            (spec.campaign_id,),
        ).fetchone()
        parent_effect = connection.execute(
            "SELECT status, proof_digest FROM campaign_effects "
            "WHERE campaign_id = ? AND issue_id = ? AND step = ?",
            (spec.campaign_id, spec.epic_id, "sync-parent-acceptance"),
        ).fetchone()
    assert after == before
    assert parent_effect == ("intent", None)
    assert effects.command_parent_snapshot_advancement(spec.campaign_id) is None
    assert source.calls == 1
    assert NoParentMutationRunner.constructions == 0


def test_outer_reconciliation_rejects_coordinator_intent_with_receipt_digest(
    tmp_path,
):
    item = command()
    state = tmp_path / "state"
    campaign_dir = state / "campaigns" / item.id
    spec = CampaignSpec(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, preview_id=item.preview_id,
        approval_id=item.approval_id, planning_version_id=item.planning_version_id,
        preview_digest=item.preview_digest, snapshot_digest=item.snapshot_digest,
        policy_digest=item.policy_digest, waves=(("DEVHUB-21",),), dependencies=(),
        max_concurrency=item.max_concurrency, budget_cents=300, expires_at=19_000,
        scheduled_for=item.scheduled_for, minimum_tier="frontier",
    )
    coordinator = CampaignStore(campaign_dir / "campaign.sqlite3")
    coordinator.initialize(spec, now_ms=10_000)
    effects = CampaignEffectStore(campaign_dir / "primitive-receipts.sqlite3")
    children = seed_completed_merge_evidence(spec, coordinator, effects)
    effect_id, work_id = CampaignCoordinator._identity(
        spec, spec.epic_id, 1, "sync-parent-acceptance",
    )
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
        spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
        "lease-parent", 20_000, spec.binding_digest, spec.snapshot_digest,
        spec.policy_digest, None, 0,
    )
    proof = ParentAcceptanceProof(
        spec.campaign_id, spec.epic_id, spec.binding_digest,
        spec.snapshot_digest, 4, children,
        (("ac-1", DIGEST_A, "DEVHUB-21"),),
    )
    advancement = ParentSnapshotAdvancement(
        spec.campaign_id, spec.epic_id, effect_id, spec.binding_digest,
        spec.snapshot_digest, proof.proof_id, 4, 5,
        DIGEST_A, DIGEST_B, DIGEST_A, DIGEST_B,
        (("ac-1", DIGEST_A, *children[0]),),
    )
    coordinator.begin_effect(envelope, selected_tier=None, cost_ceiling=0)
    coordinator.begin_parent_snapshot_advancement(advancement, now_ms=10_000)
    primitive_receipt = StepReceipt(effect_id, "completed", DIGEST_C)
    effects.begin_parent_snapshot_advancement(envelope, advancement, now_ms=10_000)
    effects.record(envelope, primitive_receipt, now_ms=10_000)
    effects.finish_parent_snapshot_advancement(
        envelope, advancement, primitive_receipt, now_ms=10_001,
    )
    with sqlite3.connect(coordinator.path) as connection:
        connection.execute(
            "UPDATE campaign_parent_snapshot_advancements "
            "SET receipt_digest = ? WHERE campaign_id = ?",
            (DIGEST_A, item.id),
        )

    class NoExternalObservation:
        now_ms = staticmethod(lambda: 10_000)
        calls = 0

        def load(self, _command):
            self.calls += 1
            raise AssertionError("external observation must not run")

    class NoParentMutationRunner:
        constructions = 0

        def __init__(self, *_args, **_kwargs):
            type(self).constructions += 1
            raise AssertionError("parent primitive must not be constructed")

    source = NoExternalObservation()
    with patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()):
        provider = CampaignCommandEffectProvider(
            source, root=tmp_path, state_directory=state, owner_id="worker-1",
            primitive_runner=NoParentMutationRunner,
        )

    with pytest.raises(CampaignError, match="reçu avancement parent durable contradictoire"):
        provider.observe(item)
    assert source.calls == 0
    assert NoParentMutationRunner.constructions == 0
    assert effects.command_parent_snapshot_advancement(item.id) == (
        advancement, "completed", primitive_receipt.proof_digest,
    )
    with sqlite3.connect(coordinator.path) as connection:
        assert connection.execute(
            "SELECT status, receipt_digest FROM "
            "campaign_parent_snapshot_advancements WHERE campaign_id = ?",
            (item.id,),
        ).fetchone() == ("intent", DIGEST_A)


def test_human_gate_can_be_explicitly_renewed_on_the_same_exact_binding(tmp_path):
    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "human-gate", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )
    first = store.approve_human_gate(
        envelope, head_sha=HEAD, review_digest=DIGEST_B, ci_digest=DIGEST_C,
        actor="human-1", expires_at=11_000, now_ms=10_000,
    )
    renewed = store.approve_human_gate(
        envelope, head_sha=HEAD, review_digest=DIGEST_B, ci_digest=DIGEST_C,
        actor="human-1", expires_at=12_000, now_ms=11_001,
    )

    assert renewed != first
    assert store.human_approval(
        envelope, head_sha=HEAD, review_digest=DIGEST_B,
        ci_digest=DIGEST_C, now_ms=11_001,
    ) == renewed
    with sqlite3.connect(store.path) as connection:
        events = connection.execute(
            "SELECT approval_digest FROM human_approval_events "
            "WHERE effect_id = ? ORDER BY expires_at",
            (envelope.effect_id,),
        ).fetchall()
    assert events == [(first,), (renewed,)]


def test_operator_gate_binds_only_the_waiting_exact_review_ci_and_head(
    tmp_path, capsys,
):
    state = tmp_path / "runtime"
    database = (
        state / "campaigns" / "campaign-1" / "primitive-receipts.sqlite3"
    )
    store = CampaignEffectStore(database)

    def envelope(step, effect_id):
        return EffectEnvelope(
            "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
            step, effect_id, "work-12345678", "lease-1", 20_000,
            DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
        )

    review = envelope("review", "review-effect-12345678")
    ci = envelope("ci", "ci-effect-12345678")
    human = envelope("human-gate", "human-effect-12345678")
    store.record(
        review, StepReceipt(review.effect_id, "completed", DIGEST_B),
        head_sha=HEAD, now_ms=10_000,
    )
    store.record(
        ci, StepReceipt(ci.effect_id, "completed", DIGEST_C),
        head_sha=HEAD, now_ms=10_000,
    )
    store.record(
        human, StepReceipt(human.effect_id, "waiting", DIGEST_A),
        head_sha=HEAD, now_ms=10_000,
    )

    with patch("foundry.campaign_gate.time.time", return_value=10):
        campaign_gate_main([
            "campaign-1", "DEVHUB-21", "--attempt", "1",
            "--actor", "operator-1", "--valid-seconds", "60",
            "--state-dir", str(state),
        ])
    output = json.loads(capsys.readouterr().out)
    assert output["contract"] == "foundry-campaign-human-gate.v1"
    assert store.human_approval(
        human, head_sha=HEAD, review_digest=DIGEST_B,
        ci_digest=DIGEST_C, now_ms=69_999,
    ) == output["approval_digest"]
    with patch("foundry.campaign_gate.time.time", return_value=10):
        campaign_gate_main([
            "campaign-1", "DEVHUB-21", "--attempt", "1",
            "--actor", "operator-1", "--valid-seconds", "60",
            "--state-dir", str(state),
        ])
    assert json.loads(capsys.readouterr().out) == output


def test_operator_gate_fails_closed_when_gate_heads_do_not_match(tmp_path):
    store = CampaignEffectStore(tmp_path / "effects.sqlite3")

    def envelope(step, effect_id):
        return EffectEnvelope(
            "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
            step, effect_id, "work-12345678", "lease-1", 20_000,
            DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
        )

    for step, effect_id, proof, head, status in (
        ("review", "review-effect-12345678", DIGEST_B, HEAD, "completed"),
        ("ci", "ci-effect-12345678", DIGEST_C, BASE, "completed"),
        ("human-gate", "human-effect-12345678", DIGEST_A, HEAD, "waiting"),
    ):
        item = envelope(step, effect_id)
        store.record(
            item, StepReceipt(item.effect_id, status, proof),
            head_sha=head, now_ms=10_000,
        )

    with pytest.raises(CampaignError, match="preuves exactes"):
        store.approve_pending_human_gate(
            "campaign-1", "DEVHUB-21", 1, actor="operator-1",
            expires_at=20_000, now_ms=10_000,
        )


def test_trusted_primitive_commits_edit_only_proposal_before_open_pr(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repository, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "foundry@example.invalid")
    git("config", "user.name", "Foundry Test")
    tracked = repository / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "base")
    git("switch", "-c", "feat/devhub-21-campaign")
    untracked = repository / "untracked.txt"

    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    runner = FoundryPrimitiveRunner(
        repository, worktree_directory=tmp_path / "worktrees", receipts=store,
    )
    runner.worktree = lambda _campaign_id, _issue_id: repository

    def implementation_runner(_argv, **_kwargs):
        tracked.write_text("proposal\n", encoding="utf-8")
        untracked.write_text("also proposed\n", encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "is_error": False, "total_cost_usd": 0.25, "duration_ms": 100,
                "structured_output": {"outcome": "completed"},
            }),
        )

    implementation = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "implementation", "implementation-effect-12345678",
        "implementation-work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, "frontier", 100,
    )
    with (
        patch("foundry.routing_facades.RoutingPolicy.load", return_value=FakeRouting()),
        patch("foundry.campaign_runtime.foundry.tracker") as tracker,
    ):
        tracker.return_value.get_issue.return_value = SimpleNamespace(
            id="DEVHUB-21", title="Focused issue", body="Bounded implementation.",
        )
        executor = IsolatedClaudeIssueExecutor(
            repository, runner, proposal_directory=tmp_path / "proposals",
            selected_tier="frontier", cost_ceiling_cents=100,
            runner=implementation_runner,
        )
        proposal = executor.execute(implementation)
    proof_digest = proposal.proof_digest
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "open-pr", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )

    runner._commit_implementation(envelope)

    assert git("status", "--porcelain") == ""
    assert git("rev-list", "--count", "main..HEAD") == "1"
    assert git("log", "-1", "--format=%s") == (
        "[DEVHUB-21] implementation de campagne bornee"
    )
    assert git("show", "HEAD:tracked.txt") == "proposal"
    assert git("show", "HEAD:untracked.txt") == "also proposed"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT proof_digest FROM implementation_intents",
        ).fetchone() == (proof_digest,)


def test_trusted_primitive_refuses_dirty_or_untracked_drift_before_commit(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repository, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "foundry@example.invalid")
    git("config", "user.name", "Foundry Test")
    tracked = repository / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "base")
    git("switch", "-c", "feat/devhub-21-campaign")
    tracked.write_text("proposal\n", encoding="utf-8")
    untracked = repository / "untracked.txt"
    untracked.write_text("intended\n", encoding="utf-8")

    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    runner = FoundryPrimitiveRunner(
        repository, worktree_directory=tmp_path / "worktrees", receipts=store,
    )
    runner.worktree = lambda _campaign_id, _issue_id: repository
    implementation = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "implementation", "implementation-effect-12345678",
        "implementation-work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, "frontier", 100,
    )
    runner.capture_implementation_intent(implementation, outcome="completed")
    original_head = git("rev-parse", "HEAD")
    untracked.write_text("changed after provider return\n", encoding="utf-8")
    open_pr = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "open-pr", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )

    with pytest.raises(CampaignError, match="modifié avant commit"):
        runner._commit_implementation(open_pr)

    assert git("rev-parse", "HEAD") == original_head
    assert git("rev-list", "--count", "main..HEAD") == "0"


def test_production_review_blocks_mergeable_proof_with_a_nonpass_ac(tmp_path):
    proof = {
        "proof_id": DIGEST_A,
        "quality": "mergeable",
        "issue": {"criteria": [{"id": "ac-1", "verdict": "fail"}]},
    }

    class NonPassStore:
        def valid_for_merge(self, **_coordinates):
            raise RoutingConfigError("preuve AC non mergeable ou incomplète")

        def current_for_sync(self, **_coordinates):
            return proof

    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=store,
        now_ms=lambda: 10_000,
    )
    pr = SimpleNamespace(number=12, sha=HEAD, base_sha=BASE)
    runner._codehost_coordinates = lambda _envelope: (tmp_path, None, None, pr)
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "review", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )
    with (
        patch("foundry.campaign_runtime.git_head", return_value=HEAD),
        patch("foundry.campaign_runtime.git_diff", return_value=b"reviewed diff"),
        patch("foundry.campaign_runtime.repository_identity", return_value="owner/repo"),
        patch("foundry.campaign_runtime.AcceptanceProofStore", return_value=NonPassStore()),
        patch("foundry.campaign_runtime.foundry.tracker") as tracker,
    ):
        tracker.return_value.get_issue.return_value = SimpleNamespace(
            id="DEVHUB-21", body="- [ ] exact AC\n",
        )
        receipt = runner.review(envelope)

    assert receipt.status == "blocked"
    assert store.get(envelope) == receipt


def test_campaign_projection_uses_devhub_paused_and_keeps_reason_bound_proofs_distinct():
    receipts = [
        CampaignCommandEffectProvider._effect(
            command(), CampaignOutcome(
                "command-1", "suspended", reason, 0, (), 30, 1,
            ), 25, attempt_id="attempt-1", attempt_started_at=10_000,
        )
        for reason in (
            "blocker", "review_blocking", "human_gate", "host_unavailable",
            "implementation_unknown", "approved_blockers",
        )
    ]

    assert {receipt.status for receipt in receipts} == {"paused"}
    assert len({receipt.proof_digest for receipt in receipts}) == len(receipts)
    assert all(set(receipt.__dict__) == {
        "effect_id", "status", "proof_digest", "cost_cents", "duration_ms",
        "attempt_id", "attempt_started_at",
    } for receipt in receipts)


def test_merge_engagement_callback_runs_at_the_irreversible_dispatch_boundary(tmp_path):
    events = []

    class MergeReceipts:
        def coordinates(self, _campaign_id, _issue_id, _attempt, step):
            events.append(step)
            return (
                StepReceipt(f"effect-{step}-12345678", "completed", DIGEST_A),
                7, HEAD, BASE,
            )

        def human_approval(self, *_args, **_kwargs):
            events.append("approval")
            return DIGEST_B

        def record(self, _envelope, receipt, **_kwargs):
            events.append("record")
            return receipt

    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=MergeReceipts(),
        now_ms=lambda: 10_000,
    )
    runner.worktree = lambda _campaign_id, _issue_id: tmp_path

    def codehost_coordinates(_envelope):
        events.append("codehost")
        return tmp_path, object(), "owner/repo", SimpleNamespace(
            number=7, sha=HEAD, base_sha=BASE,
        )

    runner._codehost_coordinates = codehost_coordinates
    runner._run = lambda *_args, **_kwargs: events.append("provider-call")
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "merge", "effect-merge-12345678", "work-merge-12345678", "lease-1",
        20_000, DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )

    receipt = runner.merge_issue(
        envelope, engage=lambda: events.append("engaged"),
    )

    assert receipt.status == "completed"
    assert events[-3:] == ["engaged", "provider-call", "record"]


def test_engaged_merge_reconciles_an_already_merged_pr_before_gate_revalidation(tmp_path):
    events = []

    class Receipts:
        def coordinates(self, *_args, **_kwargs):
            raise AssertionError("an already-merged PR must reconcile before gate lookup")

        def human_approval(self, *_args, **_kwargs):
            raise AssertionError("expired approval must not block merge reconciliation")

        def record(self, _envelope, receipt, **_kwargs):
            events.append("record")
            return receipt

    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=Receipts(),
        now_ms=lambda: 20_000,
    )
    runner._codehost_coordinates = lambda _envelope: (
        tmp_path, object(), "owner/repo", SimpleNamespace(
            number=7, sha=HEAD, base_sha=BASE, merged=True,
        ),
    )
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        "merge", "effect-merge-12345678", "work-merge-12345678", "lease-1",
        20_000, DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )

    receipt = runner.merge_issue(envelope, engage=None)

    assert receipt.status == "completed"
    assert events == ["record"]


@pytest.mark.parametrize(
    ("step", "method"),
    (("start", "start_issue"), ("open-pr", "open_pr"),
     ("merge", "merge_issue"), ("close-epic", "close_epic")),
)
def test_recovered_mutation_intent_reenters_only_its_existing_idempotent_primitive(
    tmp_path, step, method,
):
    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=store,
    )
    calls = []
    expected = StepReceipt("effect-12345678", "completed", DIGEST_A)
    setattr(
        runner, method,
        lambda envelope, **_kwargs: calls.append(envelope.step) or expected,
    )
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-21", 1,
        step, "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )

    assert runner.resolve_effect(envelope) == expected
    assert calls == [step]


def test_f83_close_epic_binding_calls_only_the_existing_atomic_primitive(
    tmp_path, monkeypatch,
):
    store = CampaignEffectStore(tmp_path / "effects.sqlite3")
    runner = FoundryPrimitiveRunner(
        tmp_path, worktree_directory=tmp_path / "worktrees", receipts=store,
        now_ms=lambda: 10_000,
    )
    calls = []
    runner._issue_command = lambda envelope, action, *extra: calls.append(
        (envelope.issue_id, action, extra)
    )
    envelope = EffectEnvelope(
        "campaign-1", "command-1", "DEVHUB", "DEVHUB-20", "DEVHUB-20", 1,
        "close-epic", "effect-12345678", "work-12345678", "lease-1", 20_000,
        DIGEST_A, DIGEST_B, DIGEST_C, None, 0,
    )
    advancement = ParentSnapshotAdvancement(
        "campaign-1", "DEVHUB-20", "effect-parent-sync", DIGEST_A, DIGEST_B,
        DIGEST_C, 4, 5, DIGEST_A, DIGEST_B, DIGEST_A, DIGEST_B,
        (("ac-1", DIGEST_A, "DEVHUB-21", "effect-merge-1", DIGEST_C),),
    )
    store.command_parent_snapshot_advancement = lambda _campaign_id: (
        advancement, "completed", DIGEST_A,
    )
    parent = SimpleNamespace(
        id="DEVHUB-20", type="Epic", state="in-progress", version=5,
        links=[SimpleNamespace(type="parent-of", target="DEVHUB-21")],
    )
    monkeypatch.setattr("foundry.campaign_runtime.foundry.tracker", lambda: SimpleNamespace(
        get_issue=lambda _issue_id: parent,
    ))
    runner._verified_close_receipt = lambda candidate, _advancement: StepReceipt(
        candidate.effect_id, "completed", DIGEST_A,
    )

    receipt = runner.close_epic(envelope)

    assert receipt.status == "completed"
    assert calls == [("DEVHUB-20", "close-epic", ())]


def test_campaign_runtime_rejects_untranslated_claude_model_before_state(tmp_path):
    config = tmp_path / ".foundry" / "model-routing.json"
    config.parent.mkdir()
    config.write_text(json.dumps({
        "mappings": {"claude": {"balanced": {"model": "untranslated-model"}}},
    }), encoding="utf-8")
    state = tmp_path / "state"

    with pytest.raises(RoutingConfigError, match="sans traduction hôte"):
        CampaignCommandEffectProvider(
            object(), root=tmp_path, state_directory=state, owner_id="owner-1",
        )
    assert not state.exists()
