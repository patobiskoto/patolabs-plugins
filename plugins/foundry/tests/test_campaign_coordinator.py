from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import replace

import pytest

from foundry.campaign_coordinator import (
    AttemptProfile,
    BlockerObservation,
    CampaignCoordinator,
    CampaignError,
    CampaignObservation,
    CampaignRevalidationError,
    CampaignSpec,
    CampaignStore,
    EscalationRetryAuthorizer,
    ImplementationReconciliation,
    ParentSnapshotAdvancement,
    ImplementationProposal,
    ReviewRemediationTarget,
    RetryDecision,
    StepReceipt,
    _digest,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def campaign(**changes) -> CampaignSpec:
    values = {
        "campaign_id": "campaign-1",
        "command_id": "command-1",
        "project": "APP",
        "epic_id": "APP-1",
        "preview_id": "preview-1",
        "approval_id": "approval-1",
        "planning_version_id": "plan-1",
        "preview_digest": DIGEST_A,
        "snapshot_digest": DIGEST_B,
        "policy_digest": DIGEST_C,
        "waves": (("APP-2", "APP-3"), ("APP-4",)),
        "dependencies": (("APP-4", "APP-2"),),
        "max_concurrency": 2,
        "budget_cents": 300,
        "expires_at": 20_000,
        "scheduled_for": None,
        "minimum_tier": "balanced",
        "max_attempts_per_issue": 3,
    }
    values.update(changes)
    return CampaignSpec(**values)


def test_unlaunched_reservation_proof_requires_the_exact_pre_implementation_ledger(tmp_path):
    spec = campaign(campaign_id="command-1", command_id="command-1")
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    store.initialize(spec, now_ms=1_000)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE campaigns SET state = 'suspended', reason = 'host_or_effect_failure', "
            "lease_expires_at = ? WHERE campaign_id = ?",
            (1_500, spec.campaign_id),
        )

    proof = store.unlaunched_reservation_proof(
        spec.campaign_id, spec.binding_digest, observed_at=2_000,
    )

    assert proof.campaign_id == spec.campaign_id
    assert proof.binding_digest == spec.binding_digest
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE campaign_issues SET selected_tier = 'frontier' "
            "WHERE campaign_id = ? AND issue_id = ?",
            (spec.campaign_id, "APP-2"),
        )
    with pytest.raises(CampaignError, match="non lancée"):
        store.unlaunched_reservation_proof(
            spec.campaign_id, spec.binding_digest, observed_at=2_001,
        )


def test_unlaunched_reservation_proof_refuses_a_corrupt_campaign_binding(tmp_path):
    spec = campaign(campaign_id="command-1", command_id="command-1")
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    store.initialize(spec, now_ms=1_000)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE campaigns SET state = 'suspended', reason = 'host_or_effect_failure', "
            "lease_expires_at = ?, binding_digest = ? WHERE campaign_id = ?",
            (1_500, DIGEST_A, spec.campaign_id),
        )
    assert spec.binding_digest != DIGEST_A

    with pytest.raises(CampaignError, match="non lancée"):
        store.unlaunched_reservation_proof(
            spec.campaign_id, DIGEST_B, observed_at=2_000,
        )


class FakePipeline:
    def __init__(self, spec: CampaignSpec, *, now=10_000):
        self.spec = spec
        self.now = now
        self.calls = []
        self.receipts = {}
        self.results = {}
        self.observation_changes = {}
        self.observe_count = 0
        self.blocker_observation_versions = {}
        self.blocker_observation_states = {}
        self.retry_allowed = True
        self.blocker_states = {issue_id: "blocked" for issue_id in spec.blockers}

    def observe(self, spec):
        self.observe_count += 1
        values = {
            "campaign_id": spec.campaign_id,
            "command_id": spec.command_id,
            "project": spec.project,
            "epic_id": spec.epic_id,
            "preview_id": spec.preview_id,
            "approval_id": spec.approval_id,
            "approval_state": "approved",
            "planning_version_id": spec.planning_version_id,
            "preview_digest": spec.preview_digest,
            "snapshot_digest": spec.snapshot_digest,
            "policy_digest": spec.policy_digest,
            "expires_at": spec.expires_at,
            "observed_at": self.now - 1,
            "valid_until": self.now + 1_000,
            "max_concurrency": spec.max_concurrency,
            "budget_remaining_cents": spec.budget_cents,
            "minimum_tier": "frontier",
            "provider_invocation_ceiling_cents": min(80, spec.budget_cents),
            "host_available": True,
            "acceptance_mapping": tuple(
                (f"ac-{index}", _digest(["criterion", index]), issue_id)
                for index, issue_id in enumerate(
                    sorted(issue for wave in spec.waves for issue in wave), start=1,
                )
            ),
        }
        values.update(self.observation_changes.get(self.observe_count, {}))
        return CampaignObservation(**values)

    def observe_blockers(self, spec):
        observations = []
        for issue_id in spec.blockers:
            state = self.blocker_states.get(issue_id, "unknown")
            if self.blocker_observation_states.get(issue_id) != state:
                self.blocker_observation_states[issue_id] = state
                self.blocker_observation_versions[issue_id] = (
                    self.blocker_observation_versions.get(issue_id, 0) + 1
                )
            version = self.blocker_observation_versions[issue_id]
            observations.append(BlockerObservation(
                issue_id, state, self.now + version, version,
            ))
        return tuple(observations)

    def resolve_effect(self, envelope, *, engage_merge=None):
        return self.receipts.get(envelope.effect_id)

    def _perform(self, envelope):
        self.calls.append((envelope.issue_id, envelope.attempt, envelope.step))
        status = self.results.get((envelope.issue_id, envelope.attempt, envelope.step), "completed")
        receipt = StepReceipt(
            envelope.effect_id, status,
            _digest([envelope.effect_id, status, len(self.calls)]),
        )
        self.receipts[envelope.effect_id] = receipt
        return receipt

    def start_issue(self, envelope):
        return self._perform(envelope)

    def open_pr(self, envelope):
        return self._perform(envelope)

    def review(self, envelope):
        return self._perform(envelope)

    def ci_gate(self, envelope):
        return self._perform(envelope)

    def human_gate(self, envelope):
        return self._perform(envelope)

    def merge_issue(self, envelope, *, engage):
        engage()
        return self._perform(envelope)

    def close_epic(self, envelope):
        return self._perform(envelope)

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
        return self._perform(envelope)

    def reconcile_parent_acceptance(self, envelope, advancement):
        return self.receipts.get(envelope.effect_id)

    def verify_parent_acceptance_proof(self, proof):
        for _issue_id, effect_id, proof_digest in proof.children:
            assert self.receipts.get(effect_id) == StepReceipt(
                effect_id, "completed", proof_digest,
            )

    def reconcile_close_epic(self, envelope, advancement):
        return self.receipts.get(envelope.effect_id)

    def authorize_retry(self, envelope, signal, current_tier):
        self.calls.append((envelope.issue_id, envelope.attempt, f"retry:{signal}"))
        return RetryDecision(
            self.retry_allowed, "apex" if self.retry_allowed else current_tier,
            _digest([envelope.effect_id, signal]), "existing-escalation-credit",
            _digest([envelope.effect_id, signal, "authorization"])
            if self.retry_allowed else None,
        )


class FakeExecutor:
    def __init__(self, *, cost_ceiling=80, costs=None, outcomes=None, delay=0.0):
        self.cost_ceiling = cost_ceiling
        self.costs = costs or {}
        self.outcomes = outcomes or {}
        self.delay = delay
        self.calls = []
        self.resolve_calls = []
        self.proposals = {}
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def execution_profile(self, issue_id, attempt):
        return AttemptProfile("frontier", self.cost_ceiling)

    def resolve(self, work_id):
        self.resolve_calls.append(work_id)
        return self.proposals.get(work_id)

    def reconcile(self, envelope):
        return ImplementationReconciliation(
            envelope.work_id, "unknown",
            _digest([envelope.effect_id, "unknown-reconciliation"]), 0,
        )

    def execute(self, envelope):
        # These are the only coordinates crossing the unprivileged seam.  There is
        # no tracker client, credential, prompt, command or code-content field.
        assert set(envelope.__dict__) == {
            "campaign_id", "command_id", "project", "epic_id", "issue_id",
            "attempt", "step", "effect_id", "work_id", "lease_id",
            "lease_expires_at", "binding_digest", "snapshot_digest",
            "policy_digest", "selected_tier", "cost_ceiling_cents",
        }
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            self.calls.append((envelope.issue_id, envelope.attempt))
            outcome = self.outcomes.get((envelope.issue_id, envelope.attempt), "completed")
            proposal = ImplementationProposal(
                envelope.work_id, envelope.issue_id, envelope.attempt, outcome,
                _digest([envelope.work_id, outcome]),
                self.costs.get((envelope.issue_id, envelope.attempt), 30),
                envelope.selected_tier,
                "test_red" if outcome == "failed" else None,
            )
            self.proposals[envelope.work_id] = proposal
            return proposal
        finally:
            with self.lock:
                self.active -= 1


def coordinator(tmp_path, spec, pipeline=None, executor=None):
    pipeline = pipeline or FakePipeline(spec)
    executor = executor or FakeExecutor()
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    return (
        CampaignCoordinator(
            store, pipeline, executor, owner_id="coordinator-1",
            now_ms=lambda: 10_000, lease_ms=30_000,
        ),
        store,
        pipeline,
        executor,
    )


def test_dag_waves_run_with_real_bounded_concurrency_and_normal_gates(tmp_path):
    spec = campaign()
    engine, store, pipeline, executor = coordinator(
        tmp_path, spec, executor=FakeExecutor(delay=0.04),
    )

    outcome = engine.run(spec)

    assert outcome.state == "completed"
    assert outcome.completed_issues == ("APP-2", "APP-3", "APP-4")
    assert executor.peak == outcome.peak_concurrency == 2
    assert outcome.budget_spent_cents == 90
    for issue_id in ("APP-2", "APP-3", "APP-4"):
        issue_steps = [step for issue, _attempt, step in pipeline.calls if issue == issue_id]
        assert issue_steps == ["start", "open-pr", "review", "ci", "human-gate", "merge"]
    first_wave_merge_positions = [
        pipeline.calls.index((issue_id, 1, "merge")) for issue_id in ("APP-2", "APP-3")
    ]
    assert pipeline.calls.index(("APP-4", 1, "start")) > max(first_wave_merge_positions)
    assert pipeline.calls[-1] == ("APP-1", 1, "close-epic")
    assert store.issue(spec.campaign_id, "APP-4")["state"] == "merged"


def test_parent_ac_sync_reuses_the_existing_close_effect_after_an_interruption(tmp_path):
    spec = campaign(waves=(("APP-2", "APP-3", "APP-4"),), dependencies=())

    class ParentProofPipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.synced = []
            self.sync_interrupted = True
            self.close_attempts = 0

        def sync_parent_acceptance(self, envelope, proof, advancement):
            self.synced.append(proof)
            receipt = self._perform(envelope)
            if self.sync_interrupted:
                self.sync_interrupted = False
                raise RuntimeError("lost parent sync receipt")
            return receipt

        def resolve_effect(self, envelope, *, engage_merge=None):
            if envelope.step == "close-epic" and envelope.effect_id not in self.receipts:
                return self.close_epic(envelope)
            return super().resolve_effect(envelope, engage_merge=engage_merge)

        def close_epic(self, envelope):
            self.close_attempts += 1
            self.calls.append((envelope.issue_id, envelope.attempt, envelope.step))
            if self.close_attempts == 1:
                # Intent exists but the provider refused incomplete parent AC.
                raise RuntimeError("parent acceptance incomplete")
            return self._perform(envelope)

    pipeline = ParentProofPipeline(spec)
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)
    first = engine.run(spec)
    assert first.reason == "parent_acceptance_failure"
    child_calls = [call for call in pipeline.calls if call[0] != spec.epic_id]

    engine.resume(spec.campaign_id, "proof_available")
    second = engine.run(spec)
    assert second.reason == "host_or_effect_failure"
    close = store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic")
    assert close is not None

    engine.resume(spec.campaign_id, "proof_available")
    third = engine.run(spec)

    assert third.state == "completed"
    assert pipeline.close_attempts == 2
    assert store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic")[0] == close[0]
    assert len(pipeline.synced) == 1
    assert [call for call in pipeline.calls if call[0] != spec.epic_id] == child_calls
    proof = pipeline.synced[0]
    assert [child[0] for child in proof.children] == ["APP-2", "APP-3", "APP-4"]
    assert all(child[1].startswith("effect-") for child in proof.children)


def test_pre_advancement_parent_failure_recovers_exact_intent_without_replaying_effects(
    tmp_path,
):
    spec = campaign(waves=(("APP-2",),), dependencies=(), max_concurrency=1)

    class InterruptedPreparationPipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.prepare_proofs = []

        def prepare_parent_acceptance(self, envelope, proof):
            self.prepare_proofs.append(proof.proof_id)
            if len(self.prepare_proofs) == 1:
                raise OSError("transient failure before advancement journal")
            return super().prepare_parent_acceptance(envelope, proof)

    pipeline = InterruptedPreparationPipeline(spec)
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )

    first = engine.run(spec)

    assert (first.state, first.reason) == (
        "suspended", "parent_acceptance_failure",
    )
    assert store.parent_snapshot_advancement(spec.campaign_id) is None
    intent = store.effect(
        spec.campaign_id, spec.epic_id, 1, "sync-parent-acceptance",
    )
    assert intent is not None and intent[2:] == (
        "intent", None, 0, None, None, None,
    )
    child_calls = [call for call in pipeline.calls if call[0] != spec.epic_id]

    assert engine.revalidate_paused_projection(spec) is True
    assert (store.campaign(spec.campaign_id).state,
            store.campaign(spec.campaign_id).reason) == (
                "running", "campaign_revalidated",
            )
    recovered = engine.run(spec)

    assert recovered.state == "completed"
    assert pipeline.prepare_proofs == [
        pipeline.prepare_proofs[0], pipeline.prepare_proofs[0],
    ]
    assert pipeline.calls.count((spec.epic_id, 1, "sync-parent-acceptance")) == 1
    assert pipeline.calls.count((spec.epic_id, 1, "close-epic")) == 1
    assert [call for call in pipeline.calls if call[0] != spec.epic_id] == child_calls
    assert store.effect(
        spec.campaign_id, spec.epic_id, 1, "sync-parent-acceptance",
    )[0] == intent[0]


def test_post_advancement_intent_crash_reproves_source_and_closes_exactly_once(
    tmp_path,
):
    spec = campaign(waves=(("APP-2",),), dependencies=(), max_concurrency=1)

    class PostIntentCrashPipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.parent_at_source = True
            self.reconciliations = 0
            self.parent_mutations = 0
            self.close_dispatches = 0

        def reconcile_parent_acceptance(self, envelope, advancement):
            self.reconciliations += 1
            if self.parent_at_source:
                return None
            return self.receipts.get(envelope.effect_id)

        def sync_parent_acceptance(self, envelope, proof, advancement):
            assert self.parent_at_source
            assert advancement.proof_id == proof.proof_id
            self.parent_mutations += 1
            self.parent_at_source = False
            return self._perform(envelope)

        def close_epic(self, envelope):
            assert not self.parent_at_source
            self.close_dispatches += 1
            return self._perform(envelope)

    pipeline = PostIntentCrashPipeline(spec)
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )
    original_begin = store.begin_parent_snapshot_advancement
    crashed = False

    def crash_after_intent(advancement, *, now_ms):
        nonlocal crashed
        original_begin(advancement, now_ms=now_ms)
        if not crashed:
            crashed = True
            raise OSError("crash after coordinator advancement intent")

    store.begin_parent_snapshot_advancement = crash_after_intent
    first = engine.run(spec)
    store.begin_parent_snapshot_advancement = original_begin

    advancement = store.parent_snapshot_advancement(spec.campaign_id)
    parent_effect = store.effect(
        spec.campaign_id, spec.epic_id, 1, "sync-parent-acceptance",
    )
    assert (first.state, first.reason) == (
        "suspended", "parent_acceptance_failure",
    )
    assert advancement is not None and advancement[1:] == ("intent", None)
    assert parent_effect is not None and parent_effect[2] == "intent"
    assert pipeline.parent_at_source is True
    assert pipeline.parent_mutations == pipeline.close_dispatches == 0

    assert engine.revalidate_paused_projection(spec) is True
    recovered = engine.run(spec)

    assert recovered.state == "completed"
    assert pipeline.reconciliations == 1
    assert pipeline.parent_mutations == 1
    assert pipeline.close_dispatches == 1
    assert pipeline.calls.count((spec.epic_id, 1, "sync-parent-acceptance")) == 1
    assert pipeline.calls.count((spec.epic_id, 1, "close-epic")) == 1
    assert store.parent_snapshot_advancement(spec.campaign_id)[1] == "completed"


def test_post_advancement_intent_recovery_refuses_an_unproved_source_snapshot(
    tmp_path,
):
    spec = campaign(waves=(("APP-2",),), dependencies=(), max_concurrency=1)

    class DriftedSourcePipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.preparations = 0
            self.parent_mutations = 0

        def prepare_parent_acceptance(self, envelope, proof):
            self.preparations += 1
            advancement = super().prepare_parent_acceptance(envelope, proof)
            if self.preparations > 1:
                return replace(advancement, to_body_digest=DIGEST_C)
            return advancement

        def sync_parent_acceptance(self, envelope, proof, advancement):
            self.parent_mutations += 1
            return super().sync_parent_acceptance(envelope, proof, advancement)

    pipeline = DriftedSourcePipeline(spec)
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )
    original_begin = store.begin_parent_snapshot_advancement

    def crash_after_intent(advancement, *, now_ms):
        original_begin(advancement, now_ms=now_ms)
        raise OSError("crash after coordinator advancement intent")

    store.begin_parent_snapshot_advancement = crash_after_intent
    first = engine.run(spec)
    store.begin_parent_snapshot_advancement = original_begin
    assert (first.state, first.reason) == (
        "suspended", "parent_acceptance_failure",
    )

    assert engine.revalidate_paused_projection(spec) is True
    refused = engine.run(spec)

    assert (refused.state, refused.reason) == (
        "suspended", "parent_acceptance_failure",
    )
    assert pipeline.preparations == 2
    assert pipeline.parent_mutations == 0
    assert pipeline.calls.count((spec.epic_id, 1, "close-epic")) == 0
    assert store.parent_snapshot_advancement(spec.campaign_id)[1:] == (
        "intent", None,
    )
    assert store.effect(
        spec.campaign_id, spec.epic_id, 1, "sync-parent-acceptance",
    )[2] == "intent"


@pytest.mark.parametrize(
    "invalid_receipt",
    ("not-receipt", "wrong-effect", "wrong-status"),
)
def test_close_reconciliation_rejects_receipts_not_bound_to_the_exact_terminal_effect(
    tmp_path, invalid_receipt,
):
    spec = campaign(waves=(("APP-2",),), dependencies=())

    class InterruptedClosePipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.close_envelope = None

        def close_epic(self, envelope):
            self.close_envelope = envelope
            raise OSError("crash after close dispatch")

        def reconcile_close_epic(self, envelope, _advancement):
            assert envelope == self.close_envelope
            if invalid_receipt == "not-receipt":
                return object()
            if invalid_receipt == "wrong-effect":
                return StepReceipt("effect-stale-12345678", "completed", DIGEST_A)
            return StepReceipt(envelope.effect_id, "waiting", DIGEST_A)

    pipeline = InterruptedClosePipeline(spec)
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )
    first = engine.run(spec)
    close = store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic")
    assert (first.state, first.reason, close[2]) == (
        "suspended", "host_or_effect_failure", "intent",
    )

    engine.resume(spec.campaign_id, "proof_available")
    second = engine.run(spec)

    assert (second.state, second.reason) == ("suspended", "host_or_effect_failure")
    assert store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic")[2] == "intent"


def test_close_reconciliation_rejects_a_stale_proof_for_an_already_completed_effect(
    tmp_path,
):
    spec = campaign(waves=(("APP-2",),), dependencies=())

    class StaleClosePipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.close_envelope = None

        def close_epic(self, envelope):
            self.close_envelope = envelope
            raise OSError("crash after close dispatch")

        def reconcile_close_epic(self, envelope, _advancement):
            return StepReceipt(envelope.effect_id, "completed", DIGEST_C)

    pipeline = StaleClosePipeline(spec)
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )
    engine.run(spec)
    envelope = pipeline.close_envelope
    assert envelope is not None
    store.finish_effect(envelope, status="completed", proof_digest=DIGEST_A)

    with pytest.raises(CampaignError, match="recu cloture Epic modifie"):
        engine._perform_step(spec, spec.epic_id, 1, "close-epic")

    assert store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic")[3] == DIGEST_A


def test_completed_local_close_receipt_cannot_complete_with_provider_parent_open(
    tmp_path,
):
    spec = campaign(waves=(("APP-2",),), dependencies=())

    class OpenParentPipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.close_dispatches = 0
            self.close_reconciliations = 0
            self.provider_parent_closed = False

        def close_epic(self, envelope):
            self.close_dispatches += 1
            return super().close_epic(envelope)

        def reconcile_close_epic(self, _envelope, _advancement):
            self.close_reconciliations += 1
            # Fresh provider truth still has the parent at the exact open
            # post-acceptance snapshot, so no terminal close can be proved.
            if not self.provider_parent_closed:
                return None
            return super().reconcile_close_epic(_envelope, _advancement)

    pipeline = OpenParentPipeline(spec)
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )
    original_set_state = store.set_state
    interrupted = False

    def interrupt_after_local_receipt(
        campaign_id, state, reason, *, now_ms, wave_index=None,
    ):
        nonlocal interrupted
        if state == "completed" and not interrupted:
            interrupted = True
            raise OSError("crash after local close receipt")
        return original_set_state(
            campaign_id, state, reason, now_ms=now_ms, wave_index=wave_index,
        )

    store.set_state = interrupt_after_local_receipt
    with pytest.raises(OSError, match="local close receipt"):
        engine.run(spec)
    store.set_state = original_set_state
    close = store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic")
    assert close is not None and close[2] == "completed"

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == (
        "suspended", "host_or_effect_failure",
    )
    assert pipeline.close_dispatches == 1
    assert pipeline.close_reconciliations == 1
    assert store.effect(spec.campaign_id, spec.epic_id, 1, "close-epic") == close

    pipeline.provider_parent_closed = True
    assert engine.revalidate_paused_projection(spec) is True
    recovered = engine.run(spec)

    assert recovered.state == "completed"
    assert pipeline.close_dispatches == 1
    assert pipeline.close_reconciliations == 2


def test_one_time_schedule_and_drift_suspend_before_any_effect(tmp_path):
    scheduled = campaign(scheduled_for=11_000)
    engine, _store, pipeline, executor = coordinator(tmp_path, scheduled)
    outcome = engine.run(scheduled)
    assert (outcome.state, outcome.reason) == ("scheduled", "not_due")
    assert pipeline.calls == executor.calls == []

    drifted = campaign(campaign_id="campaign-drift")
    engine2, _store2, pipeline2, executor2 = coordinator(tmp_path / "drift", drifted)
    pipeline2.observation_changes[1] = {"snapshot_digest": "d" * 64}
    outcome2 = engine2.run(drifted)
    assert (outcome2.state, outcome2.reason) == ("suspended", "drift_snapshot_digest")
    assert pipeline2.calls == executor2.calls == []


def test_approved_preview_blockers_suspend_before_any_campaign_attempt(tmp_path):
    spec = campaign(
        campaign_id="campaign-approved-blockers",
        waves=(("APP-2",),), dependencies=(), blockers=("APP-99",),
    )
    engine, store, pipeline, executor = coordinator(tmp_path, spec)

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == ("suspended", "approved_blockers")
    assert pipeline.calls == executor.calls == []
    assert store.issue(spec.campaign_id, "APP-2")["state"] == "pending"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM campaign_effects").fetchone() == (0,)


def test_approved_blocker_requires_fresh_terminal_proof_and_can_then_resume(tmp_path):
    spec = campaign(
        campaign_id="campaign-resolved-blocker",
        waves=(("APP-2",),), dependencies=(), blockers=("APP-99",),
    )
    pipeline = FakePipeline(spec)
    engine, store, _pipeline, executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )

    assert engine.run(spec).reason == "approved_blockers"
    engine.resume(spec.campaign_id, "proof_available")
    assert engine.run(spec).reason == "approved_blockers"
    assert executor.calls == []

    pipeline.blocker_states["APP-99"] = "done"
    engine.resume(spec.campaign_id, "proof_available")
    assert engine.run(spec).state == "completed"
    assert executor.calls == [("APP-2", 1)]
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute(
            "SELECT issue_state, resolved FROM campaign_blocker_observations "
            "WHERE campaign_id = ? AND blocker_id = ? ORDER BY issue_state",
            (spec.campaign_id, "APP-99"),
        ).fetchall()
    assert rows == [("blocked", 0), ("done", 1)]


def test_resolved_blocker_reopened_before_effect_suspends_again(tmp_path):
    spec = campaign(
        campaign_id="campaign-reopened-blocker",
        waves=(("APP-2",),), dependencies=(), blockers=("APP-99",),
    )

    class ReopeningPipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.blocker_reads = 0

        def observe_blockers(self, campaign_spec):
            self.blocker_reads += 1
            state = "done" if self.blocker_reads == 1 else "blocked"
            return (
                BlockerObservation(
                    campaign_spec.blockers[0], state,
                    self.now + self.blocker_reads, self.blocker_reads,
                ),
            )

    pipeline = ReopeningPipeline(spec)
    engine, store, _pipeline, executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == ("suspended", "approved_blockers")
    assert pipeline.blocker_reads == 2
    assert pipeline.calls == executor.calls == []
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT issue_state, resolved FROM campaign_blocker_observations "
            "WHERE campaign_id = ? ORDER BY issue_state",
            (spec.campaign_id,),
        ).fetchall() == [("blocked", 0), ("done", 1)]


def test_human_gate_suspends_and_resume_only_observes_same_effect_identity(tmp_path):
    spec = campaign(
        campaign_id="campaign-human", waves=(("APP-2",),), dependencies=(),
        max_concurrency=1,
    )
    pipeline = FakePipeline(spec)
    pipeline.results[("APP-2", 1, "human-gate")] = "waiting"
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "human_gate")
    assert ("APP-2", 1, "merge") not in pipeline.calls
    gate_effect = store.effect(spec.campaign_id, "APP-2", 1, "human-gate")
    assert gate_effect is not None

    pipeline.receipts[gate_effect[0]] = StepReceipt(
        gate_effect[0], "completed", _digest([gate_effect[0], "human-approved"]),
    )
    engine.resume(spec.campaign_id, "proof_available")
    second = engine.run(spec)

    assert second.state == "completed"
    assert pipeline.calls.count(("APP-2", 1, "human-gate")) == 1
    assert pipeline.calls[-3:] == [
        ("APP-2", 1, "merge"),
        ("APP-1", 1, "sync-parent-acceptance"),
        ("APP-1", 1, "close-epic"),
    ]


def test_gate_reobservation_can_turn_waiting_into_a_durable_block(tmp_path):
    spec = campaign(
        campaign_id="campaign-ci-reobserve", waves=(("APP-2",),), dependencies=(),
        max_concurrency=1,
    )
    pipeline = FakePipeline(spec)
    pipeline.results[("APP-2", 1, "ci")] = "waiting"
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)
    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "ci_waiting")
    effect = store.effect(spec.campaign_id, "APP-2", 1, "ci")
    assert effect is not None
    pipeline.receipts[effect[0]] = StepReceipt(
        effect[0], "blocked", _digest([effect[0], "red"]),
    )

    engine.resume(spec.campaign_id, "proof_available")
    second = engine.run(spec)

    assert (second.state, second.reason) == ("suspended", "ci_blocked")
    assert store.effect(spec.campaign_id, "APP-2", 1, "ci")[2] == "blocked"


@pytest.mark.parametrize(
    ("step", "initial_status", "suspension_reason"),
    (
        ("review", "blocked", "review_blocking"),
        ("ci", "waiting", "ci_waiting"),
        ("human-gate", "waiting", "human_gate"),
    ),
)
def test_paused_projection_revalidates_an_exact_gate_before_resuming(
    tmp_path, step, initial_status, suspension_reason,
):
    spec = campaign(
        campaign_id=f"campaign-revalidate-{step}", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )
    pipeline = FakePipeline(spec)
    pipeline.retry_allowed = False
    pipeline.results[("APP-2", 1, step)] = initial_status
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", suspension_reason)
    effect = store.effect(spec.campaign_id, "APP-2", 1, step)
    assert effect is not None
    pipeline.receipts[effect[0]] = StepReceipt(
        effect[0], "completed", _digest([effect[0], "fresh-gate-proof"]),
    )

    assert engine.revalidate_paused_projection(spec) is True
    resumed = store.campaign(spec.campaign_id)
    assert (resumed.state, resumed.reason) == ("running", "proof_available")
    assert engine.run(spec).state == "completed"


def test_stale_resolved_blocker_cannot_reopen_a_newer_blocked_campaign(tmp_path):
    spec = campaign(
        campaign_id="campaign-stale-blocker", waves=(("APP-2",),),
        dependencies=(), blockers=("APP-99",), max_concurrency=1,
    )

    class StaleBlockerPipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.reads = 0

        def observe_blockers(self, campaign_spec):
            self.reads += 1
            if self.reads == 1:
                return (BlockerObservation("APP-99", "blocked", self.now, 2),)
            return (BlockerObservation("APP-99", "done", self.now + 1, 1),)

    pipeline = StaleBlockerPipeline(spec)
    engine, store, _pipeline, executor = coordinator(tmp_path, spec, pipeline=pipeline)

    assert engine.run(spec).reason == "approved_blockers"
    assert engine.revalidate_paused_projection(spec) is False
    assert (store.campaign(spec.campaign_id).state, store.campaign(spec.campaign_id).reason) == (
        "suspended", "approved_blockers",
    )
    assert executor.calls == []


def test_same_coordinate_done_cannot_override_newer_blocked_blocker(tmp_path):
    spec = campaign(
        campaign_id="campaign-same-coordinate-blocker", waves=(("APP-2",),),
        dependencies=(), blockers=("APP-99",), max_concurrency=1,
    )

    class SameCoordinatePipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.reads = 0

        def observe_blockers(self, campaign_spec):
            self.reads += 1
            state = "blocked" if self.reads == 1 else "done"
            return (BlockerObservation("APP-99", state, 10_000, 2),)

    pipeline = SameCoordinatePipeline(spec)
    engine, store, _pipeline, executor = coordinator(tmp_path, spec, pipeline=pipeline)

    assert engine.run(spec).reason == "approved_blockers"
    engine.resume(spec.campaign_id, "proof_available")
    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == (
        "suspended", "blocker_observation_stale",
    )
    assert executor.calls == []
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT issue_state, issue_updated_at, issue_version FROM "
            "campaign_blocker_observations WHERE campaign_id = ?",
            (spec.campaign_id,),
        ).fetchall() == [("blocked", 10_000, 2)]


def test_retry_consumes_existing_authorization_and_budget_limit_suspends(tmp_path):
    spec = campaign(
        campaign_id="campaign-retry", waves=(("APP-2",),), dependencies=(),
        max_concurrency=1, budget_cents=150,
    )
    executor = FakeExecutor(
        cost_ceiling=80,
        costs={("APP-2", 1): 40, ("APP-2", 2): 50},
        outcomes={("APP-2", 1): "failed"},
    )
    engine, store, pipeline, _executor = coordinator(tmp_path, spec, executor=executor)

    outcome = engine.run(spec)

    assert outcome.state == "completed"
    assert outcome.budget_spent_cents == 90
    assert executor.calls == [("APP-2", 1), ("APP-2", 2)]
    assert ("APP-2", 1, "retry:test_red") in pipeline.calls
    assert store.issue(spec.campaign_id, "APP-2")["attempt"] == 2

    tight = replace(spec, campaign_id="campaign-budget", budget_cents=1)
    engine2, _store2, pipeline2, executor2 = coordinator(
        tmp_path / "tight", tight,
        executor=FakeExecutor(cost_ceiling=80, delay=0.02),
    )
    # Integer division never rounds a one-cent balance up across two allowed calls.
    tight = replace(
        tight, waves=(("APP-2", "APP-3"),), dependencies=(), max_concurrency=2,
    )
    outcome2 = engine2.run(tight)
    assert (outcome2.state, outcome2.reason) == ("suspended", "budget_exhausted")
    assert len(executor2.calls) <= 1
    assert ("APP-1", 1, "close-epic") not in pipeline2.calls


def test_cancel_is_cooperative_and_does_not_start_next_pipeline_effect(tmp_path):
    spec = campaign(
        campaign_id="campaign-cancel", waves=(("APP-2",),), dependencies=(),
        max_concurrency=1,
    )
    engine_ref = {}

    class CancellingExecutor(FakeExecutor):
        def execute(self, envelope):
            proposal = super().execute(envelope)
            engine_ref["engine"].request_cancel(envelope.campaign_id)
            return proposal

    engine, _store, pipeline, executor = coordinator(
        tmp_path, spec, executor=CancellingExecutor(),
    )
    engine_ref["engine"] = engine

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == ("cancelled", "cancel")
    assert executor.calls == [("APP-2", 1)]
    assert pipeline.calls == [("APP-2", 1, "start")]
    assert ("APP-1", 1, "close-epic") not in pipeline.calls


@pytest.mark.parametrize(
    ("control", "state", "reason"),
    (("pause-requested", "paused", "pause"),
     ("cancel-requested", "cancelled", "cancel")),
)
def test_control_after_fresh_intent_prevents_resolution_and_provider_effect(
    tmp_path, control, state, reason,
):
    spec = campaign(
        campaign_id=f"campaign-intent-{state}", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )

    class ControlAfterIntentStore(CampaignStore):
        def begin_effect(self, envelope, **kwargs):
            row, created = super().begin_effect(envelope, **kwargs)
            if created and envelope.step == "start":
                self.request_control(envelope.campaign_id, control, now_ms=10_000)
            return row, created

    class MutatingResolverPipeline(FakePipeline):
        def resolve_effect(self, envelope, *, engage_merge=None):
            raise AssertionError("a fresh intent must not enter mutating resolution")

    store = ControlAfterIntentStore(tmp_path / "campaign.sqlite3")
    pipeline = MutatingResolverPipeline(spec)
    executor = FakeExecutor()
    engine = CampaignCoordinator(
        store, pipeline, executor, owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000,
    )

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == (state, reason)
    assert pipeline.calls == executor.calls == []
    assert store.effect(spec.campaign_id, "APP-2", 1, "start")[2] == "intent"


@pytest.mark.parametrize(
    ("control", "state", "reason"),
    (("pause-requested", "paused", "pause"),
     ("cancel-requested", "cancelled", "cancel")),
)
def test_control_before_merge_dispatch_leaves_merge_unengaged_and_unattempted(
    tmp_path, control, state, reason,
):
    spec = campaign(
        campaign_id=f"campaign-pre-merge-{state}", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )

    class ControlAtMergeIntentStore(CampaignStore):
        def begin_effect(self, envelope, **kwargs):
            row, created = super().begin_effect(envelope, **kwargs)
            if created and envelope.step == "merge":
                self.request_control(envelope.campaign_id, control, now_ms=10_000)
            return row, created

    store = ControlAtMergeIntentStore(tmp_path / "campaign.sqlite3")
    pipeline = FakePipeline(spec)
    engine = CampaignCoordinator(
        store, pipeline, FakeExecutor(), owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000,
    )

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == (state, reason)
    assert ("APP-2", 1, "merge") not in pipeline.calls
    assert ("APP-1", 1, "close-epic") not in pipeline.calls
    assert store.issue(spec.campaign_id, "APP-2")["engaged_merge"] is False
    assert store.effect(spec.campaign_id, "APP-2", 1, "merge")[2] == "intent"


def test_pause_during_engaged_merge_observes_receipt_but_does_not_close_parent(tmp_path):
    spec = campaign(
        campaign_id="campaign-pause-merge", waves=(("APP-2",),), dependencies=(),
        max_concurrency=1,
    )
    engine_ref = {}

    class PausingPipeline(FakePipeline):
        def merge_issue(self, envelope, *, engage):
            engage()
            engine_ref["engine"].request_pause(envelope.campaign_id)
            return self._perform(envelope)

    pipeline = PausingPipeline(spec)
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)
    engine_ref["engine"] = engine

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == ("paused", "pause")
    assert store.issue(spec.campaign_id, "APP-2")["state"] == "merged"
    assert ("APP-2", 1, "merge") in pipeline.calls
    assert ("APP-1", 1, "close-epic") not in pipeline.calls


def test_cancel_reconciles_ambiguous_engaged_merge_before_confirming_control(tmp_path):
    spec = campaign(
        campaign_id="campaign-cancel-ambiguous-merge",
        waves=(("APP-2",),), dependencies=(), max_concurrency=1,
    )
    control = {"state": None}

    class AmbiguousMergePipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.raise_once = True

        def merge_issue(self, envelope, *, engage):
            receipt = super().merge_issue(envelope, engage=engage)
            if self.raise_once:
                self.raise_once = False
                raise TimeoutError("merge receipt transport lost")
            return receipt

    pipeline = AmbiguousMergePipeline(spec)
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    engine = CampaignCoordinator(
        store, pipeline, FakeExecutor(), owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000,
        control_observer=lambda _spec: control["state"],
    )
    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "host_or_effect_failure")
    assert store.issue(spec.campaign_id, "APP-2")["merge_dispatch_started"] is True
    assert store.issue(spec.campaign_id, "APP-2")["engaged_merge"] is False

    control["state"] = "cancel-requested"
    engine.resume(spec.campaign_id, "host_recovered")
    second = engine.run(spec)

    assert (second.state, second.reason) == ("cancelled", "cancel")
    assert store.issue(spec.campaign_id, "APP-2")["state"] == "merged"
    assert [call for call in pipeline.calls if call[2] == "merge"] == [
        ("APP-2", 1, "merge"),
    ]
    assert ("APP-1", 1, "close-epic") not in pipeline.calls


def test_paused_projection_reconciles_engaged_merge_without_revalidating_new_effects(tmp_path):
    spec = campaign(
        campaign_id="campaign-projected-ambiguous-merge", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )

    class AmbiguousMergePipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.raise_once = True

        def merge_issue(self, envelope, *, engage):
            receipt = super().merge_issue(envelope, engage=engage)
            if self.raise_once:
                self.raise_once = False
                raise TimeoutError("merge receipt transport lost")
            return receipt

    pipeline = AmbiguousMergePipeline(spec)
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "host_or_effect_failure")
    assert store.issue(spec.campaign_id, "APP-2")["merge_dispatch_started"] is True
    assert store.issue(spec.campaign_id, "APP-2")["engaged_merge"] is False
    observations_before = pipeline.observe_count

    assert engine.revalidate_paused_projection(spec) is False
    assert store.issue(spec.campaign_id, "APP-2")["state"] == "merged"
    assert pipeline.observe_count == observations_before
    assert [call for call in pipeline.calls if call[2] == "merge"] == [
        ("APP-2", 1, "merge"),
    ]


def test_engaged_merge_receipt_recovers_before_unavailable_control_observation(tmp_path):
    spec = campaign(
        campaign_id="campaign-merge-recovery-before-host", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )

    class AmbiguousMergePipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.raise_once = True

        def merge_issue(self, envelope, *, engage):
            receipt = super().merge_issue(envelope, engage=engage)
            if self.raise_once:
                self.raise_once = False
                raise TimeoutError("merge receipt transport lost")
            return receipt

    pipeline = AmbiguousMergePipeline(spec)
    control_reads = []
    control_unavailable = {"value": False}

    def control(_spec):
        control_reads.append("read")
        if control_unavailable["value"]:
            raise OSError("host unavailable")
        return None

    store = CampaignStore(tmp_path / "campaign.sqlite3")
    engine = CampaignCoordinator(
        store, pipeline, FakeExecutor(), owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000, control_observer=control,
    )

    assert engine.run(spec).reason == "host_or_effect_failure"
    control_unavailable["value"] = True
    outcome = engine.run(spec)

    assert store.issue(spec.campaign_id, "APP-2")["state"] == "merged"
    assert store.issue(spec.campaign_id, "APP-2")["merge_dispatch_started"] is False
    assert outcome.state == "suspended"
    assert pipeline.calls.count(("APP-2", 1, "merge")) == 1


def test_parallel_paused_gates_revalidate_each_exact_receipt(tmp_path):
    spec = campaign(
        campaign_id="campaign-parallel-paused-gates", waves=(("APP-2", "APP-3"),),
        dependencies=(), max_concurrency=2,
    )
    class ConcurrentReviewPipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.review_barrier = threading.Barrier(2)

        def review(self, envelope):
            # Both siblings must have created their exact review effect before
            # either waiting receipt suspends the wave.  Without this barrier the
            # test races the scheduler rather than exercising all paused gates.
            self.review_barrier.wait(timeout=5)
            return self._perform(envelope)

    pipeline = ConcurrentReviewPipeline(spec)
    pipeline.results[("APP-2", 1, "review")] = "waiting"
    pipeline.results[("APP-3", 1, "review")] = "waiting"
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)

    assert engine.run(spec).reason == "review_waiting"
    for issue_id in ("APP-2", "APP-3"):
        effect = store.effect(spec.campaign_id, issue_id, 1, "review")
        assert effect is not None
        pipeline.receipts[effect[0]] = StepReceipt(
            effect[0], "completed", _digest([effect[0], "review-green"]),
        )

    assert engine.revalidate_paused_projection(spec) is True
    assert engine.run(spec).state == "completed"


@pytest.mark.parametrize(
    ("control", "state", "reason"),
    (("pause-requested", "paused", "pause"),
     ("cancel-requested", "cancelled", "cancel")),
)
def test_external_control_confirms_suspended_blocker_without_manual_resume(
    tmp_path, control, state, reason,
):
    spec = campaign(
        campaign_id=f"campaign-blocker-{state}", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )
    observed_control = {"state": None}
    pipeline = FakePipeline(spec)
    executor = FakeExecutor(outcomes={("APP-2", 1): "blocked"})
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    engine = CampaignCoordinator(
        store, pipeline, executor, owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000,
        control_observer=lambda _spec: observed_control["state"],
    )

    blocked = engine.run(spec)
    assert (blocked.state, blocked.reason) == ("suspended", "blocker")
    calls_before_control = (list(pipeline.calls), list(executor.calls))

    observed_control["state"] = control
    confirmed = engine.run(spec)

    assert (confirmed.state, confirmed.reason) == (state, reason)
    assert (pipeline.calls, executor.calls) == calls_before_control
    assert store.issue(spec.campaign_id, "APP-2")["attempt"] == 1


def test_ambiguous_agent_return_resumes_by_work_identity_without_second_launch(tmp_path):
    spec = campaign(
        campaign_id="campaign-agent-resume", waves=(("APP-2",),), dependencies=(),
        max_concurrency=1,
    )

    class AmbiguousOnceExecutor(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.interrupt = True

        def execute(self, envelope):
            proposal = super().execute(envelope)
            if self.interrupt:
                self.interrupt = False
                raise TimeoutError("lost terminal transport")
            return proposal

    executor = AmbiguousOnceExecutor()
    engine, _store, _pipeline, _executor = coordinator(
        tmp_path, spec, executor=executor,
    )

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "host_or_effect_failure")
    assert executor.calls == [("APP-2", 1)]

    engine.resume(spec.campaign_id, "host_recovered")
    second = engine.run(spec)
    assert second.state == "completed"
    assert executor.calls == [("APP-2", 1)]
    assert second.budget_spent_cents == 30


def test_engaged_proposal_reconciles_with_zero_fresh_budget_but_new_work_is_refused(
    tmp_path,
):
    spec = campaign(
        campaign_id="campaign-zero-budget-reconcile",
        waves=(("APP-2", "APP-3"),), dependencies=(), max_concurrency=1,
    )

    class ExhaustiblePipeline(FakePipeline):
        def __init__(self, value):
            super().__init__(value)
            self.remaining = value.budget_cents

        def observe(self, candidate):
            return replace(
                super().observe(candidate),
                budget_remaining_cents=self.remaining,
            )

    class AmbiguousAfterProposalExecutor(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.interrupt = True

        def execute(self, envelope):
            proposal = super().execute(envelope)
            if self.interrupt:
                self.interrupt = False
                raise TimeoutError("lost terminal transport")
            return proposal

    pipeline = ExhaustiblePipeline(spec)
    executor = AmbiguousAfterProposalExecutor()
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline, executor=executor,
    )

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "host_or_effect_failure")
    engaged = store.effect(spec.campaign_id, "APP-2", 1, "implementation")
    assert engaged is not None and engaged[2] == "intent"
    assert executor.calls == [("APP-2", 1)]

    pipeline.remaining = 0
    engine.resume(spec.campaign_id, "host_recovered")
    proposal = engine._implementation(
        spec, "APP-2", store.issue(spec.campaign_id, "APP-2"),
        reconcile_intent=True,
    )

    assert proposal.outcome == "completed"
    assert executor.calls == [("APP-2", 1)]
    reconciled = store.effect(spec.campaign_id, "APP-2", 1, "implementation")
    assert reconciled is not None
    assert reconciled[2:4] == ("completed", proposal.proof_digest)
    assert (store.campaign(spec.campaign_id).budget_reserved,
            store.campaign(spec.campaign_id).active_concurrency) == (0, 0)

    with pytest.raises(CampaignRevalidationError, match="budget_exhausted"):
        engine._implementation(
            spec, "APP-3", store.issue(spec.campaign_id, "APP-3"),
        )
    assert store.effect(spec.campaign_id, "APP-3", 1, "implementation") is None
    assert executor.calls == [("APP-2", 1)]


def test_host_unavailable_receipt_requires_fresh_resolution_and_new_attempt(tmp_path):
    spec = campaign(
        campaign_id="campaign-host-recovered", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )
    executor = FakeExecutor(outcomes={("APP-2", 1): "host-unavailable"})
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, executor=executor,
    )

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "host_unavailable")
    first_effect = store.effect(spec.campaign_id, "APP-2", 1, "implementation")
    assert first_effect is not None
    resolves_before_resume = executor.resolve_calls.count(first_effect[1])

    engine.resume(spec.campaign_id, "host_recovered")
    second = engine.run(spec)

    assert second.state == "completed"
    assert executor.calls == [("APP-2", 1), ("APP-2", 2)]
    assert executor.resolve_calls.count(first_effect[1]) > resolves_before_resume
    assert store.issue(spec.campaign_id, "APP-2")["attempt"] == 2


def test_blocked_receipt_reobserves_but_only_manual_retry_creates_new_attempt(tmp_path):
    spec = campaign(
        campaign_id="campaign-manual-blocker", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )
    executor = FakeExecutor(outcomes={("APP-2", 1): "blocked"})
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, executor=executor,
    )

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "blocker")
    effect = store.effect(spec.campaign_id, "APP-2", 1, "implementation")
    assert effect is not None
    resolves = executor.resolve_calls.count(effect[1])

    engine.resume(spec.campaign_id, "proof_available")
    still_blocked = engine.run(spec)
    assert (still_blocked.state, still_blocked.reason) == ("suspended", "blocker")
    assert executor.calls == [("APP-2", 1)]
    assert executor.resolve_calls.count(effect[1]) > resolves

    with pytest.raises(CampaignError, match="autorisation exacte"):
        engine.resume(spec.campaign_id, "manual_retry_approved")
    authorization = engine.authorize_manual_retry(
        spec.campaign_id, "APP-2", 1, actor="operator-1",
    )
    completed = engine.run(spec)
    assert completed.state == "completed"
    assert executor.calls == [("APP-2", 1), ("APP-2", 2)]
    assert store.issue(spec.campaign_id, "APP-2")["attempt"] == 2
    with sqlite3.connect(store.path) as connection:
        audit = connection.execute(
            "SELECT effect_id, proof_digest, authorization_digest "
            "FROM campaign_resume_audits WHERE campaign_id = ? AND issue_id = ?",
            (spec.campaign_id, "APP-2"),
        ).fetchone()
    assert audit == (effect[0], effect[3], authorization)


def test_pre_receipt_exception_releases_only_after_safe_reconciliation(tmp_path):
    spec = campaign(
        campaign_id="campaign-safe-release", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )

    class PreReceiptExceptionExecutor(FakeExecutor):
        def execute(self, envelope):
            if envelope.attempt == 1:
                self.calls.append((envelope.issue_id, envelope.attempt))
                raise OSError("provider did not engage")
            return super().execute(envelope)

        def reconcile(self, envelope):
            return ImplementationReconciliation(
                envelope.work_id, "not-engaged",
                _digest([envelope.effect_id, "provider-not-engaged"]), 0,
            )

    executor = PreReceiptExceptionExecutor()
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, executor=executor,
    )

    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "host_or_effect_failure")
    reserved = store.campaign(spec.campaign_id)
    assert (reserved.budget_reserved, reserved.active_concurrency) == (80, 1)

    engine.resume(spec.campaign_id, "host_recovered")
    completed = engine.run(spec)

    assert completed.state == "completed"
    assert executor.calls == [("APP-2", 1), ("APP-2", 2)]
    settled = store.campaign(spec.campaign_id)
    assert (settled.budget_reserved, settled.active_concurrency) == (0, 0)
    assert completed.budget_spent_cents == 30


def test_ambiguous_pre_receipt_exception_keeps_reservation_and_never_duplicates(tmp_path):
    spec = campaign(
        campaign_id="campaign-ambiguous-release", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )

    class AmbiguousExecutor(FakeExecutor):
        def execute(self, envelope):
            self.calls.append((envelope.issue_id, envelope.attempt))
            raise TimeoutError("provider state unknown")

    executor = AmbiguousExecutor()
    engine, store, _pipeline, _executor = coordinator(
        tmp_path, spec, executor=executor,
    )
    first = engine.run(spec)
    assert (first.state, first.reason) == ("suspended", "host_or_effect_failure")

    engine.resume(spec.campaign_id, "host_recovered")
    second = engine.run(spec)

    assert (second.state, second.reason) == ("suspended", "implementation_unknown")
    assert executor.calls == [("APP-2", 1)]
    row = store.campaign(spec.campaign_id)
    assert (row.budget_reserved, row.active_concurrency) == (80, 1)


def test_retry_adapter_reuses_existing_escalation_decision():
    class Decision:
        initial_tier = "balanced"
        final_tier = "frontier"
        human_required = False
        action = "remediation_continued"
        remediation_authorization = {"state": "active"}

        def to_dict(self):
            return {
                "action": self.action, "initial_tier": self.initial_tier,
                "final_tier": self.final_tier,
                "remediation_authorization": self.remediation_authorization,
            }

    class ExistingStore:
        def __init__(self):
            self.calls = []

        def record_failure(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return Decision()

    spec = campaign(waves=(("APP-2",),), dependencies=(), max_concurrency=1)
    effect_id, work_id = CampaignCoordinator._identity(spec, "APP-2", 1, "implementation")
    from foundry.campaign_coordinator import EffectEnvelope
    envelope = EffectEnvelope(
        spec.campaign_id, spec.command_id, spec.project, spec.epic_id, "APP-2", 1,
        "implementation", effect_id, work_id, "lease-1", 20_000,
        spec.binding_digest, spec.snapshot_digest, spec.policy_digest, "balanced", 80,
    )
    existing = ExistingStore()

    decision = EscalationRetryAuthorizer(existing).authorize(
        envelope, "test_red", "balanced",
    )

    assert decision.allowed is True
    assert decision.selected_tier == "frontier"
    assert existing.calls == [(
        ("APP-2", "implementer", "test_red", "balanced"),
        {"idempotency_key": envelope.effect_id, "authorization_aware": True},
    )]


def test_first_campaign_review_records_only_first_signal_idempotently(tmp_path):
    from foundry.escalation import EscalationStore

    spec = campaign(
        campaign_id="campaign-first-review", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1, minimum_tier="apex",
    )
    escalation = EscalationStore("campaign-test", state_dir=tmp_path / "routing")

    class ProductionRetryPipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.authorizer = EscalationRetryAuthorizer(escalation)

        def authorize_retry(self, envelope, signal, current_tier):
            self.calls.append((envelope.issue_id, envelope.attempt, f"retry:{signal}"))
            return self.authorizer.authorize(envelope, signal, current_tier)

    pipeline = ProductionRetryPipeline(spec)
    pipeline.results[("APP-2", 1, "review")] = "blocked"
    pipeline.observation_changes = {
        index: {"minimum_tier": "apex"} for index in range(1, 100)
    }
    engine, store, _pipeline, executor = coordinator(
        tmp_path / "campaign", spec, pipeline=pipeline,
    )

    blocked = engine.run(spec)
    assert (blocked.state, blocked.reason) == ("suspended", "review_blocking")
    assert pipeline.calls.count((
        "APP-2", 1, "retry:review_blocking",
    )) == 1
    status = escalation.status("APP-2")
    assert status["halted"] is False
    assert status["roles"]["implementer"]["deterministic_failures"] == 1
    assert status.get("consumption_audit", []) == []
    assert status["remediation_authorization"]["state"] == "none"

    engine.resume(spec.campaign_id, "proof_available")
    replay = engine.run(spec)

    assert (replay.state, replay.reason) == ("suspended", "review_blocking")
    assert pipeline.calls.count((
        "APP-2", 1, "retry:review_blocking",
    )) == 2
    assert not [call for call in pipeline.calls if call[2].endswith("after_fix")]
    assert escalation.status("APP-2") == status
    assert executor.calls == [("APP-2", 1)]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*), SUM(allowed) FROM campaign_retry_decisions"
        ).fetchone() == (1, 0)


def test_exact_first_review_authorization_resumes_once_through_normal_pipeline(tmp_path):
    from foundry.escalation import EscalationStore

    spec = campaign(
        campaign_id="campaign-authorized-first-review", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1, minimum_tier="apex",
        expires_at=100_000,
    )
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    escalation = EscalationStore("campaign-authorized", state_dir=tmp_path / "routing")

    class ProductionRetryPipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.authorizer = EscalationRetryAuthorizer(
                escalation,
                review_authorization=store.review_remediation_authorization,
                now_ms=lambda: 10_000,
            )

        def authorize_retry(self, envelope, signal, current_tier):
            self.calls.append((envelope.issue_id, envelope.attempt, f"retry:{signal}"))
            return self.authorizer.authorize(envelope, signal, current_tier)

    pipeline = ProductionRetryPipeline(spec)
    pipeline.results[("APP-2", 1, "review")] = "blocked"
    pipeline.observation_changes = {
        index: {"minimum_tier": "apex"} for index in range(1, 100)
    }
    executor = FakeExecutor()
    engine = CampaignCoordinator(
        store, pipeline, executor, owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000,
    )

    blocked = engine.run(spec)
    assert (blocked.state, blocked.reason) == ("suspended", "review_blocking")
    revalidated = []

    def revalidate(target):
        revalidated.append(target)
        return target

    authorization = store.authorize_review_remediation(
        spec.campaign_id, "APP-2", 1, actor="operator-1",
        valid_seconds=60, now_ms=10_000, revalidate=revalidate,
    )
    replayed = store.authorize_review_remediation(
        spec.campaign_id, "APP-2", 1, actor="operator-1",
        valid_seconds=60, now_ms=10_001, revalidate=revalidate,
    )

    completed = engine.run(spec)

    assert completed.state == "completed"
    assert authorization == replayed
    assert len(revalidated) == 2
    assert revalidated[0] == revalidated[1] == ReviewRemediationTarget(
        spec.campaign_id, "APP-2", 1, authorization.effect_id,
        authorization.proof_digest, spec.binding_digest, "implementer", "apex",
    )
    assert executor.calls == [("APP-2", 1), ("APP-2", 2)]
    assert store.issue(spec.campaign_id, "APP-2")["attempt"] == 2
    assert [call for call in pipeline.calls if call[2].startswith("retry:")] == [
        ("APP-2", 1, "retry:review_blocking"),
        ("APP-2", 1, "retry:review_blocking"),
    ]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT attempt, step, allowed, authorization_digest "
            "FROM campaign_retry_decisions"
        ).fetchall() == [(1, "review", 0, None), (1, "review", 1, authorization.authorization_digest)]


@pytest.mark.parametrize("failure", ("unavailable", "effect", "role"))
def test_first_review_revalidation_failure_leaves_grant_and_campaign_untouched(
    tmp_path, failure,
):
    from foundry.escalation import EscalationStore

    spec = campaign(
        campaign_id="campaign-review-revalidation", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1, minimum_tier="apex",
        expires_at=100_000,
    )
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    escalation = EscalationStore("campaign-revalidation", state_dir=tmp_path / "routing")

    class ProductionRetryPipeline(FakePipeline):
        def __init__(self, campaign_spec):
            super().__init__(campaign_spec)
            self.authorizer = EscalationRetryAuthorizer(escalation)

        def authorize_retry(self, envelope, signal, current_tier):
            self.calls.append((envelope.issue_id, envelope.attempt, f"retry:{signal}"))
            return self.authorizer.authorize(envelope, signal, current_tier)

    pipeline = ProductionRetryPipeline(spec)
    pipeline.results[("APP-2", 1, "review")] = "blocked"
    pipeline.observation_changes = {
        index: {"minimum_tier": "apex"} for index in range(1, 100)
    }
    engine = CampaignCoordinator(
        store, pipeline, FakeExecutor(), owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000,
    )
    blocked = engine.run(spec)
    assert (blocked.state, blocked.reason) == ("suspended", "review_blocking")
    before_campaign = store.campaign(spec.campaign_id)
    before_issue = store.issue(spec.campaign_id, "APP-2")

    def revalidate(target):
        if failure == "unavailable":
            raise OSError("authoritative read unavailable")
        if failure == "effect":
            return replace(target, effect_id="effect-drift")
        return replace(target, role="reviewer")

    with pytest.raises(CampaignError, match="revalidation d'autorité"):
        store.authorize_review_remediation(
            spec.campaign_id, "APP-2", 1, actor="operator-1",
            valid_seconds=60, now_ms=10_000, revalidate=revalidate,
        )

    assert store.campaign(spec.campaign_id) == before_campaign
    assert store.issue(spec.campaign_id, "APP-2") == before_issue
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM campaign_review_remediation_authorizations"
        ).fetchone() == (0,)


def test_authorized_review_remediation_must_progress_before_after_fix_signal(tmp_path):
    spec = campaign(
        campaign_id="campaign-review-remediation", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1, minimum_tier="apex",
    )

    class ExplicitRemediationPipeline(FakePipeline):
        def authorize_retry(self, envelope, signal, current_tier):
            self.calls.append((envelope.issue_id, envelope.attempt, f"retry:{signal}"))
            allowed = signal == "review_blocking"
            return RetryDecision(
                allowed, current_tier,
                _digest([envelope.effect_id, signal, "decision"]),
                "remediation_continued" if allowed else "failure_recorded",
                _digest([envelope.effect_id, signal, "authorization"])
                if allowed else None,
            )

    pipeline = ExplicitRemediationPipeline(spec)
    pipeline.results.update({
        ("APP-2", 1, "review"): "blocked",
        ("APP-2", 2, "review"): "blocked",
    })
    pipeline.observation_changes = {
        index: {"minimum_tier": "apex"} for index in range(1, 100)
    }
    engine, store, _pipeline, executor = coordinator(
        tmp_path, spec, pipeline=pipeline,
    )

    blocked_after_fix = engine.run(spec)
    assert (blocked_after_fix.state, blocked_after_fix.reason) == (
        "suspended", "review_blocking",
    )
    assert executor.calls == [("APP-2", 1), ("APP-2", 2)]
    assert [call for call in pipeline.calls if call[2].startswith("retry:")] == [
        ("APP-2", 1, "retry:review_blocking"),
        ("APP-2", 2, "retry:review_blocking_after_fix"),
    ]

    engine.resume(spec.campaign_id, "proof_available")
    replay = engine.run(spec)

    assert (replay.state, replay.reason) == ("suspended", "review_blocking")
    assert executor.calls == [("APP-2", 1), ("APP-2", 2)]
    assert [call for call in pipeline.calls if call[2].startswith("retry:")] == [
        ("APP-2", 1, "retry:review_blocking"),
        ("APP-2", 2, "retry:review_blocking_after_fix"),
        ("APP-2", 2, "retry:review_blocking_after_fix"),
    ]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT attempt, step, allowed FROM campaign_retry_decisions "
            "ORDER BY attempt"
        ).fetchall() == [(1, "review", 1), (2, "review", 0)]


def test_later_first_review_after_test_retry_keeps_first_review_signal(tmp_path):
    spec = campaign(
        campaign_id="campaign-test-retry-before-review", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )
    pipeline = FakePipeline(spec)
    pipeline.results[("APP-2", 2, "review")] = "blocked"
    executor = FakeExecutor(outcomes={("APP-2", 1): "failed"})
    engine, _store, _pipeline, _executor = coordinator(
        tmp_path, spec, pipeline=pipeline, executor=executor,
    )

    completed = engine.run(spec)

    assert completed.state == "completed"
    assert executor.calls == [("APP-2", 1), ("APP-2", 2), ("APP-2", 3)]
    assert [call for call in pipeline.calls if call[2].startswith("retry:")] == [
        ("APP-2", 1, "retry:test_red"),
        ("APP-2", 2, "retry:review_blocking"),
    ]


def test_spec_allows_large_wave_to_be_batched_but_rejects_invalid_dag_order():
    bounded = campaign(
        waves=(("APP-2", "APP-3", "APP-4"),), dependencies=(), max_concurrency=2,
    )
    assert len(bounded.waves[0]) == 3
    with pytest.raises(ValueError, match="contraire au DAG"):
        campaign(
            waves=(("APP-2", "APP-3"),), dependencies=(("APP-3", "APP-2"),),
        )


def test_tightened_concurrency_processes_every_wave_child_in_bounded_batches(tmp_path):
    spec = campaign(
        campaign_id="campaign-batches",
        waves=(("APP-2", "APP-3", "APP-4"),), dependencies=(), max_concurrency=3,
    )
    pipeline = FakePipeline(spec)
    pipeline.observation_changes = {
        index: {"max_concurrency": 1} for index in range(1, 100)
    }
    engine, _store, pipeline, executor = coordinator(
        tmp_path, spec, pipeline=pipeline, executor=FakeExecutor(delay=0.01),
    )

    outcome = engine.run(spec)

    assert outcome.state == "completed"
    assert outcome.completed_issues == ("APP-2", "APP-3", "APP-4")
    assert executor.peak == outcome.peak_concurrency == 1
    assert [item for item in pipeline.calls if item[2] == "start"] == [
        ("APP-2", 1, "start"), ("APP-3", 1, "start"), ("APP-4", 1, "start"),
    ]


def test_concurrency_tightening_between_batch_and_attempt_defers_extra_child(tmp_path):
    spec = campaign(
        campaign_id="campaign-tightens-before-attempt",
        waves=(("APP-2", "APP-3"),), dependencies=(), max_concurrency=2,
    )
    pipeline = FakePipeline(spec)
    pipeline.observation_changes = {
        index: {"max_concurrency": 1} for index in range(3, 100)
    }
    engine, _store, _pipeline, executor = coordinator(
        tmp_path, spec, pipeline=pipeline, executor=FakeExecutor(delay=0.03),
    )

    outcome = engine.run(spec)

    assert outcome.state == "completed"
    assert outcome.completed_issues == ("APP-2", "APP-3")
    assert executor.peak == outcome.peak_concurrency == 1


@pytest.mark.parametrize(
    ("control", "state", "reason"),
    (("pause-requested", "paused", "pause"),
     ("cancel-requested", "cancelled", "cancel")),
)
def test_external_control_is_confirmed_before_not_due_schedule(
    tmp_path, control, state, reason,
):
    spec = campaign(
        campaign_id=f"campaign-scheduled-{state}", scheduled_for=11_000,
    )
    pipeline = FakePipeline(spec)
    store = CampaignStore(tmp_path / "campaign.sqlite3")
    engine = CampaignCoordinator(
        store, pipeline, FakeExecutor(), owner_id="coordinator-1",
        now_ms=lambda: 10_000, lease_ms=30_000,
        control_observer=lambda _spec: control,
    )

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == (state, reason)
    assert pipeline.calls == []


def test_unexpected_pipeline_exception_always_suspends_durably(tmp_path):
    spec = campaign(
        campaign_id="campaign-provider-error", waves=(("APP-2",),),
        dependencies=(), max_concurrency=1,
    )

    class ExplodingPipeline(FakePipeline):
        def open_pr(self, _envelope):
            raise LookupError("provider exploded")

    pipeline = ExplodingPipeline(spec)
    engine, store, _pipeline, _executor = coordinator(tmp_path, spec, pipeline=pipeline)

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == (
        "suspended", "host_or_effect_failure",
    )
    assert store.campaign(spec.campaign_id).state == "suspended"


def test_approval_identity_and_active_state_are_revalidated(tmp_path):
    spec = campaign(
        campaign_id="campaign-revoked", waves=(("APP-2",),), dependencies=(),
        max_concurrency=1,
    )
    pipeline = FakePipeline(spec)
    pipeline.observation_changes[1] = {"approval_state": "revoked"}
    engine, _store, _pipeline, executor = coordinator(tmp_path, spec, pipeline=pipeline)

    outcome = engine.run(spec)

    assert (outcome.state, outcome.reason) == (
        "suspended", "approval_not_active",
    )
    assert executor.calls == []
