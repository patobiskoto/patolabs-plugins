from __future__ import annotations

import sqlite3
import threading

import pytest

from foundry.campaign_coordinator import (
    CampaignSpec,
    CampaignStore,
    EffectEnvelope,
    ImplementationProposal,
    StepReceipt,
)
from foundry.campaign_reconcile import (
    CampaignReconcileError,
    reconcile_host_unavailable_command,
    reconcile_review_waiting_command,
    reconcile_unlaunched_command,
)
from foundry.campaign_runtime import CampaignEffectStore
from foundry.command_worker import (
    BoundedExecutionPath,
    CommandWorkerError,
    EffectReceipt,
    EffectiveLimits,
    ExecutionProfile,
    HostReservationStore,
    RevalidationObservation,
    _binding_digest,
)
from foundry.devhub_commands import CommandLease, DevHubCommand


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def command(**changes) -> DevHubCommand:
    values = {
        "id": "command-1", "project": "APP", "preview_id": "preview-1",
        "approval_id": "approval-1", "preview_digest": DIGEST_A,
        "snapshot_digest": DIGEST_B, "policy_digest": DIGEST_C,
        "epic_id": "APP-1", "planning_version_id": "APP-VERSION-1",
        "max_cost_cents": 100, "max_concurrency": 1, "state": "unknown",
        "projection_stale": True, "next_event_sequence": 2, "scheduled_for": None,
        "lease": None, "version": 3, "created_at": 100, "updated_at": 100,
    }
    values.update(changes)
    return DevHubCommand(**values)


def review_waiting_command(**changes) -> DevHubCommand:
    """A current DevHub pause is required for review-waiting reconciliation."""
    changes.setdefault("state", "paused")
    changes.setdefault("projection_stale", False)
    return command(**changes)


def spec(item: DevHubCommand) -> CampaignSpec:
    return CampaignSpec(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, preview_id=item.preview_id, approval_id=item.approval_id,
        planning_version_id=item.planning_version_id, preview_digest=item.preview_digest,
        snapshot_digest=item.snapshot_digest, policy_digest=item.policy_digest,
        waves=(("APP-2",),), dependencies=(), max_concurrency=1, budget_cents=100,
        expires_at=10_000, minimum_tier="frontier",
    )


class Client:
    def __init__(self, item: DevHubCommand):
        self.item = item

    def get_command(self, command_id: str) -> DevHubCommand:
        assert command_id == self.item.id
        return self.item


def prepared_state(tmp_path, item: DevHubCommand) -> None:
    root = tmp_path / "command-worker"
    campaign_path = root / item.project / "campaign-runtime" / "campaigns" / item.id
    ledger = CampaignStore(campaign_path / "campaign.sqlite3")
    campaign = spec(item)
    ledger.initialize(campaign, now_ms=100)
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "UPDATE campaigns SET state = 'suspended', reason = 'host_or_effect_failure', "
            "lease_expires_at = ? WHERE campaign_id = ?",
            (200, item.id),
        )
    reservations = HostReservationStore(root / "host-reservations.sqlite3")
    reservations.reserve(
        item.id, _binding_digest(item), ExecutionProfile("frontier", 100),
        EffectiveLimits(100, 1, 100, "frontier", 100, 0, 100),
        owner_id="owner-1", now_ms=100,
    )
    reservations.yield_owner(item.id, _binding_digest(item), "owner-1")


def _envelope(
    item: DevHubCommand, campaign: CampaignSpec, lease, issue_id: str, step: str,
    *, cost: int = 0, tier: str | None = None,
) -> EffectEnvelope:
    suffix = f"{issue_id.lower()}-{step}"
    return EffectEnvelope(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, issue_id=issue_id, attempt=1, step=step,
        effect_id=f"effect-{suffix}", work_id=f"work-{suffix}",
        lease_id=lease.lease_id, lease_expires_at=lease.lease_expires_at,
        binding_digest=campaign.binding_digest,
        snapshot_digest=campaign.snapshot_digest, policy_digest=campaign.policy_digest,
        selected_tier=tier, cost_ceiling_cents=cost,
    )


def prepared_host_unavailable_state(
    tmp_path, item: DevHubCommand, *, campaign_snapshot_digest: str | None = None,
) -> None:
    root = tmp_path / "command-worker"
    campaign_path = root / item.project / "campaign-runtime" / "campaigns" / item.id
    ledger = CampaignStore(campaign_path / "campaign.sqlite3")
    campaign = CampaignSpec(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, preview_id=item.preview_id, approval_id=item.approval_id,
        planning_version_id=item.planning_version_id, preview_digest=item.preview_digest,
        snapshot_digest=campaign_snapshot_digest or item.snapshot_digest,
        policy_digest=item.policy_digest,
        waves=(("APP-2", "APP-3"), ("APP-4",)), dependencies=(),
        max_concurrency=2, budget_cents=100, expires_at=10_000,
        minimum_tier="frontier",
    )
    ledger.initialize(campaign, now_ms=100)
    lease = ledger.acquire_lease(item.id, "coordinator-1", now_ms=100, lease_ms=1_000)
    receipts = CampaignEffectStore(campaign_path / "primitive-receipts.sqlite3")
    implementations = []
    for index, issue_id in enumerate(("APP-2", "APP-3"), start=1):
        start = _envelope(item, campaign, lease, issue_id, "start")
        ledger.begin_effect(start, selected_tier=None, cost_ceiling=0)
        start_proof = ("a" if index == 1 else "b") * 64
        ledger.finish_effect(start, status="completed", proof_digest=start_proof)
        receipts.record(
            start, StepReceipt(start.effect_id, "completed", start_proof),
            now_ms=200 + index,
        )
        ledger.advance_issue(
            item.id, issue_id, next_step="implementation",
            state="start-completed", selected_tier="frontier",
        )
        implementation = _envelope(
            item, campaign, lease, issue_id, "implementation", cost=50,
            tier="frontier",
        )
        ledger.begin_implementation(
            campaign, implementation, observed_remaining=100,
            observed_max_concurrency=2, now_ms=300 + index,
        )
        implementations.append(implementation)
    for index, implementation in enumerate(implementations, start=1):
        proof = receipts.record_implementation_intent(
            implementation, outcome="host-unavailable",
            head_sha="c" * 40, tree_sha="d" * 40, now_ms=400 + index,
        )
        ledger.finish_implementation(
            campaign, implementation,
            ImplementationProposal(
                implementation.work_id, implementation.issue_id, 1,
                "host-unavailable", proof, 0, "frontier",
            ),
            now_ms=500 + index,
        )
    ledger.set_state(item.id, "suspended", "host_unavailable", now_ms=600)
    reservations = HostReservationStore(root / "host-reservations.sqlite3")
    reservations.reserve(
        item.id, _binding_digest(item), ExecutionProfile("frontier", 100, 2),
        EffectiveLimits(100, 2, 100, "frontier", 100, 0, 100),
        owner_id="owner-1", now_ms=100,
    )
    reservations.yield_owner(item.id, _binding_digest(item), "owner-1")


def prepared_review_waiting_state(
    tmp_path, item: DevHubCommand, *,
    waves: tuple[tuple[str, ...], ...] = (("APP-2", "APP-3"), ("APP-4",)),
) -> None:
    """Review-waiting shape: two settled PRs and two non-engaged review gates."""
    root = tmp_path / "command-worker"
    campaign_path = root / item.project / "campaign-runtime" / "campaigns" / item.id
    ledger = CampaignStore(campaign_path / "campaign.sqlite3")
    campaign = CampaignSpec(
        campaign_id=item.id, command_id=item.id, project=item.project,
        epic_id=item.epic_id, preview_id=item.preview_id, approval_id=item.approval_id,
        planning_version_id=item.planning_version_id, preview_digest=item.preview_digest,
        snapshot_digest=item.snapshot_digest, policy_digest=item.policy_digest,
        waves=waves, dependencies=(),
        max_concurrency=2, budget_cents=2_000, expires_at=10_000,
        minimum_tier="frontier",
    )
    ledger.initialize(campaign, now_ms=100)
    lease = ledger.acquire_lease(item.id, "coordinator-1", now_ms=100, lease_ms=1_000)
    receipts = CampaignEffectStore(campaign_path / "primitive-receipts.sqlite3")
    for index, (issue_id, spent) in enumerate((("APP-2", 15), ("APP-3", 12)), start=1):
        start = _envelope(item, campaign, lease, issue_id, "start")
        ledger.begin_effect(start, selected_tier=None, cost_ceiling=0)
        start_proof = ("a" if index == 1 else "b") * 64
        ledger.finish_effect(start, status="completed", proof_digest=start_proof)
        receipts.record(start, StepReceipt(start.effect_id, "completed", start_proof), now_ms=200 + index)
        ledger.advance_issue(item.id, issue_id, next_step="implementation", state="start-completed", selected_tier="frontier")
        implementation = _envelope(item, campaign, lease, issue_id, "implementation", cost=1_000, tier="frontier")
        ledger.begin_implementation(campaign, implementation, observed_remaining=2_000, observed_max_concurrency=2, now_ms=300 + index)
        proof = receipts.record_implementation_intent(
            implementation, outcome="completed", head_sha="c" * 40, tree_sha="d" * 40,
            now_ms=400 + index,
        )
        ledger.finish_implementation(
            campaign, implementation,
            ImplementationProposal(implementation.work_id, issue_id, 1, "completed", proof, spent, "frontier"),
            now_ms=500 + index,
        )
        ledger.advance_issue(item.id, issue_id, next_step="open-pr", state="implementation-completed")
        open_pr = _envelope(item, campaign, lease, issue_id, "open-pr")
        ledger.begin_effect(open_pr, selected_tier=None, cost_ceiling=0)
        pr_proof = ("e" if index == 1 else "f") * 64
        ledger.finish_effect(open_pr, status="completed", proof_digest=pr_proof)
        receipts.record(
            open_pr, StepReceipt(open_pr.effect_id, "completed", pr_proof),
            pr_number=index, head_sha=("1" if index == 1 else "2") * 40,
            base_sha="0" * 40, now_ms=600 + index,
        )
        ledger.advance_issue(item.id, issue_id, next_step="review", state="open-pr-completed")
        review = _envelope(item, campaign, lease, issue_id, "review")
        ledger.begin_effect(review, selected_tier=None, cost_ceiling=0)
        review_proof = ("7" if index == 1 else "8") * 64
        ledger.finish_effect(review, status="waiting", proof_digest=review_proof)
        receipts.record(
            review, StepReceipt(review.effect_id, "waiting", review_proof),
            pr_number=index, head_sha=("1" if index == 1 else "2") * 40,
            base_sha="0" * 40, now_ms=700 + index,
        )
    ledger.set_state(item.id, "suspended", "review_waiting", now_ms=800)
    reservations = HostReservationStore(root / "host-reservations.sqlite3")
    reservations.reserve(
        item.id, _binding_digest(item), ExecutionProfile("frontier", 2_000, 2),
        EffectiveLimits(2_000, 2, 1_000, "frontier", 2_000, 0, 100),
        owner_id="owner-1", now_ms=100,
    )
    reservations.yield_owner(item.id, _binding_digest(item), "owner-1")


def test_reconcile_unlaunched_command_requires_stale_expired_remote_and_local_proof(tmp_path):
    item = command()
    prepared_state(tmp_path, item)

    result = reconcile_unlaunched_command(Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 300)

    assert result["state"] == "settled"
    assert result["settled_cost_cents"] == 0
    assert result["reconciliation_reason"] == "unlaunched_campaign_ledger"
    assert reconcile_unlaunched_command(Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 301) == result


def test_reconcile_unlaunched_command_refuses_a_healthy_remote_lease(tmp_path):
    item = command(lease=CommandLease("lease-1", "worker-1", 400, 200))
    prepared_state(tmp_path, item)

    with pytest.raises(CampaignReconcileError, match="lease DevHub encore saine"):
        reconcile_unlaunched_command(Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 300)


def test_reconcile_unlaunched_command_refuses_a_missing_ledger_without_creating_one(tmp_path):
    item = command()

    with pytest.raises(CampaignReconcileError, match="ledger de campagne absent"):
        reconcile_unlaunched_command(Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 300)
    assert not (tmp_path / "command-worker").exists()


def test_reconcile_observed_host_unavailable_ledger_releases_exact_capacity_once(tmp_path):
    item = command(max_concurrency=2)
    prepared_host_unavailable_state(tmp_path, item)

    result = reconcile_host_unavailable_command(
        Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
    )

    assert result["state"] == "settled"
    assert result["settled_cost_cents"] == 0
    assert result["reconciliation_reason"] == "host_unavailable_campaign_ledger"
    assert result["released_cost_ceiling_cents"] == 100
    assert result["released_concurrency_units"] == 2
    assert reconcile_host_unavailable_command(
        Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_201,
    ) == result
    reservations = HostReservationStore(tmp_path / "command-worker" / "host-reservations.sqlite3")
    assert reservations.reserve(
        "command-next", DIGEST_A, ExecutionProfile("frontier", 100, 2),
        EffectiveLimits(100, 2, 100, "frontier", 100, 0, 1_201),
        owner_id="owner-next", now_ms=1_202,
    ).state == "active"


def test_host_unavailable_reconciliation_refuses_a_different_campaign_binding(tmp_path):
    item = command(max_concurrency=2)
    prepared_host_unavailable_state(
        tmp_path, item, campaign_snapshot_digest="e" * 64,
    )

    with pytest.raises(
        CampaignReconcileError,
        match="binding commande/campagne host-unavailable contradictoire",
    ):
        reconcile_host_unavailable_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )

    reservation = HostReservationStore(
        tmp_path / "command-worker" / "host-reservations.sqlite3",
    ).get(item.id)
    assert reservation is not None and reservation.state == "active"


def test_review_waiting_reconciliation_blocks_campaign_writer_through_settlement(
    tmp_path, monkeypatch,
):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2)
    prepared_review_waiting_state(tmp_path, item)
    campaign_path = (
        tmp_path / "command-worker" / item.project / "campaign-runtime"
        / "campaigns" / item.id
    )
    original = HostReservationStore.reconcile_review_waiting
    settlement_entered = threading.Event()
    release_settlement = threading.Event()
    reconciliation_done = threading.Event()
    lease_done = threading.Event()
    failures: list[Exception] = []

    def hold_host_settlement(self, proof, *, now_ms):
        settlement_entered.set()
        assert release_settlement.wait(timeout=5)
        return original(self, proof, now_ms=now_ms)

    def reconcile() -> None:
        try:
            reconcile_review_waiting_command(
                Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
            )
        except Exception as exc:  # surfaced below after both bounded threads finish
            failures.append(exc)
        finally:
            reconciliation_done.set()

    def acquire_competing_lease() -> None:
        try:
            CampaignStore(campaign_path / "campaign.sqlite3").acquire_lease(
                item.id, "racing-coordinator", now_ms=1_200, lease_ms=1_000,
            )
        except Exception as exc:  # an unexpected writer failure is test evidence
            failures.append(exc)
        finally:
            lease_done.set()

    monkeypatch.setattr(HostReservationStore, "reconcile_review_waiting", hold_host_settlement)
    reconciliation = threading.Thread(target=reconcile)
    reconciliation.start()
    assert settlement_entered.wait(timeout=5)
    competing_writer = threading.Thread(target=acquire_competing_lease)
    competing_writer.start()
    assert not lease_done.wait(timeout=0.15)

    release_settlement.set()
    reconciliation.join(timeout=5)
    competing_writer.join(timeout=5)
    assert reconciliation_done.is_set() and lease_done.is_set()
    assert not failures
    reservation = HostReservationStore(
        tmp_path / "command-worker" / "host-reservations.sqlite3",
    ).get(item.id)
    assert reservation is not None and reservation.state == "settled"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("nonzero", "effet host-unavailable contradictoire"),
        ("nonterminal", "effet host-unavailable contradictoire"),
        ("open-pr", "effet aval host-unavailable présent"),
        ("ci", "effet aval host-unavailable présent"),
        ("merge", "effet aval host-unavailable présent"),
        ("truncated", "preuve host-unavailable tronquée"),
        ("capacity", "capacité de campagne host-unavailable contradictoire"),
    ),
)
def test_host_unavailable_reconciliation_refuses_ambiguous_or_incomplete_evidence(
    tmp_path, mutation, message,
):
    item = command(max_concurrency=2)
    prepared_host_unavailable_state(tmp_path, item)
    campaign_path = (
        tmp_path / "command-worker" / item.project / "campaign-runtime"
        / "campaigns" / item.id
    )
    if mutation == "nonzero":
        with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
            connection.execute(
                "UPDATE campaign_effects SET cost_cents = 1 "
                "WHERE step = 'implementation' AND issue_id = 'APP-2'",
            )
    elif mutation == "nonterminal":
        with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
            connection.execute(
                "UPDATE campaign_effects SET status = 'unknown' "
                "WHERE step = 'implementation' AND issue_id = 'APP-2'",
            )
    elif mutation in {"open-pr", "ci", "merge"}:
        with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
            connection.execute(
                "INSERT INTO campaign_effects "
                "(campaign_id, issue_id, attempt, step, effect_id, work_id, status, "
                "proof_digest, cost_ceiling, cost_cents, selected_tier, "
                "retry_audit_digest, engagement_state) VALUES "
                "(?, 'APP-2', 1, ?, ?, ?, "
                "'completed', ?, 0, NULL, NULL, NULL, 'not-applicable')",
                (
                    item.id, mutation, f"effect-{mutation}", f"work-{mutation}",
                    DIGEST_A,
                ),
            )
    elif mutation == "truncated":
        with sqlite3.connect(campaign_path / "primitive-receipts.sqlite3") as connection:
            connection.execute(
                "DELETE FROM implementation_intents WHERE issue_id = 'APP-2'",
            )
    else:
        with sqlite3.connect(
            tmp_path / "command-worker" / "host-reservations.sqlite3",
        ) as connection:
            connection.execute(
                "UPDATE host_reservations SET cost_ceiling_cents = 99 "
                "WHERE command_id = ?",
                (item.id,),
            )

    with pytest.raises(CampaignReconcileError, match=message):
        reconcile_host_unavailable_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )
    reservation = HostReservationStore(
        tmp_path / "command-worker" / "host-reservations.sqlite3",
    ).get(item.id)
    assert reservation is not None and reservation.state == "active"


def test_review_waiting_reconciliation_settles_exact_cost_once_without_effect(tmp_path):
    item = review_waiting_command(
        id="command_af2390d4-e5c6-414a-9c3e-e6eafd808d07",
        max_cost_cents=2_000,
        max_concurrency=2,
    )
    prepared_review_waiting_state(tmp_path, item)

    result = reconcile_review_waiting_command(
        Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
    )

    assert result["state"] == "settled"
    assert result["settled_cost_cents"] == 27
    assert result["reconciliation_reason"] == "review_waiting_campaign_ledger"
    assert result["released_cost_ceiling_cents"] == 2_000
    assert result["released_concurrency_units"] == 2
    assert reconcile_review_waiting_command(
        Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_201,
    ) == result
    campaign_path = (
        tmp_path / "command-worker" / item.project / "campaign-runtime"
        / "campaigns" / item.id
    )
    with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
        assert connection.execute(
            "SELECT state, reason, budget_spent, budget_reserved, active_concurrency "
            "FROM campaigns WHERE campaign_id = ?", (item.id,),
        ).fetchone() == ("suspended", "review_waiting", 27, 0, 0)
        assert connection.execute(
            "SELECT count(*) FROM campaign_effects WHERE step = 'review' AND status = 'waiting'",
        ).fetchone() == (2,)
    assert item.state == "paused" and not item.projection_stale



@pytest.mark.parametrize(
    "changes",
    (
        {"state": "unknown", "projection_stale": True},
        {"state": "paused", "projection_stale": True},
        {"state": "running", "projection_stale": False},
    ),
)
def test_review_waiting_reconciliation_refuses_ambiguous_command_state(tmp_path, changes):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2, **changes)
    prepared_review_waiting_state(tmp_path, item)

    with pytest.raises(CampaignReconcileError, match="review-waiting non stable"):
        reconcile_review_waiting_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )

    reservation = HostReservationStore(
        tmp_path / "command-worker" / "host-reservations.sqlite3",
    ).get(item.id)
    assert reservation is not None and reservation.state == "active"


def test_review_waiting_reconciliation_refuses_review_in_a_later_wave(tmp_path):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2)
    prepared_review_waiting_state(
        tmp_path, item, waves=(("APP-4",), ("APP-2", "APP-3")),
    )

    with pytest.raises(
        CampaignReconcileError,
        match="état d issue review-waiting ambigu",
    ):
        reconcile_review_waiting_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )

    reservation = HostReservationStore(
        tmp_path / "command-worker" / "host-reservations.sqlite3",
    ).get(item.id)
    assert reservation is not None and reservation.state == "active"


def test_review_waiting_reconciliation_refuses_pending_issue_in_active_wave(tmp_path):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2)
    prepared_review_waiting_state(
        tmp_path, item, waves=(("APP-2", "APP-3", "APP-4"),),
    )

    with pytest.raises(
        CampaignReconcileError,
        match="état d issue review-waiting ambigu",
    ):
        reconcile_review_waiting_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )

    reservation = HostReservationStore(
        tmp_path / "command-worker" / "host-reservations.sqlite3",
    ).get(item.id)
    assert reservation is not None and reservation.state == "active"


def test_review_waiting_reconciliation_replays_after_post_settlement_crash(
    tmp_path, monkeypatch,
):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2)
    prepared_review_waiting_state(tmp_path, item)
    original = HostReservationStore.reconcile_review_waiting
    interrupted = False

    def crash_after_commit(self, proof, *, now_ms):
        nonlocal interrupted
        settled = original(self, proof, now_ms=now_ms)
        if not interrupted:
            interrupted = True
            raise OSError("interruption après settlement durable")
        return settled

    monkeypatch.setattr(
        HostReservationStore, "reconcile_review_waiting", crash_after_commit,
    )
    with pytest.raises(CampaignReconcileError, match="interruption après settlement"):
        reconcile_review_waiting_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )

    replay = reconcile_review_waiting_command(
        Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_201,
    )
    assert replay["state"] == "settled"
    assert replay["settled_cost_cents"] == 27
    assert replay["reconciled_at"] == 1_200


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing-intent", "preuve review-waiting tronquée"),
        ("live-implementation", "effet review-waiting contradictoire"),
        ("binding", "binding review-waiting durable invalide"),
        ("owner", "réservation review-waiting encore possédée"),
        ("decision", "décision review-waiting contradictoire"),
        ("ci", "effet aval review-waiting présent"),
    ),
)
def test_review_waiting_reconciliation_refuses_ambiguous_evidence(tmp_path, mutation, message):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2)
    prepared_review_waiting_state(tmp_path, item)
    root = tmp_path / "command-worker"
    campaign_path = root / item.project / "campaign-runtime" / "campaigns" / item.id
    if mutation == "missing-intent":
        with sqlite3.connect(campaign_path / "primitive-receipts.sqlite3") as connection:
            connection.execute("DELETE FROM implementation_intents WHERE issue_id = 'APP-2'")
    elif mutation == "live-implementation":
        with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
            connection.execute(
                "UPDATE campaign_effects SET engagement_state = 'engaged' "
                "WHERE issue_id = 'APP-2' AND step = 'implementation'",
            )
    elif mutation == "binding":
        with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
            connection.execute(
                "UPDATE campaigns SET spec_json = replace(spec_json, ?, ?)",
                (item.snapshot_digest, "e" * 64),
            )
    elif mutation == "owner":
        with sqlite3.connect(root / "host-reservations.sqlite3") as connection:
            connection.execute(
                "UPDATE host_reservations SET owner_id = 'owner-2', owner_expires_at = 1500 "
                "WHERE command_id = ?", (item.id,),
            )
    elif mutation == "decision":
        with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
            connection.execute(
                "INSERT INTO campaign_retry_decisions "
                "(audit_digest, campaign_id, issue_id, attempt, step, allowed, "
                "selected_tier, reason, authorization_digest) "
                "VALUES (?, ?, 'APP-2', 1, 'review', 0, 'frontier', 'human-required', NULL)",
                ("9" * 64, item.id),
            )
    else:
        with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
            connection.execute(
                "INSERT INTO campaign_effects "
                "(campaign_id, issue_id, attempt, step, effect_id, work_id, status, "
                "proof_digest, cost_ceiling, cost_cents, selected_tier, "
                "retry_audit_digest, engagement_state) VALUES "
                "(?, 'APP-2', 1, 'ci', 'effect-ci', 'work-ci', 'completed', ?, 0, NULL, "
                "NULL, NULL, 'not-applicable')",
                (item.id, DIGEST_A),
            )
    with pytest.raises(CampaignReconcileError, match=message):
        reconcile_review_waiting_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )
    reservation = HostReservationStore(root / "host-reservations.sqlite3").get(item.id)
    assert reservation is not None and reservation.state == "active"


@pytest.mark.parametrize(
    ("table", "insert"),
    (
        (
            "campaign_resume_audits",
            "INSERT INTO campaign_resume_audits "
            "(campaign_id, issue_id, from_attempt, to_attempt, reason, effect_id, "
            "proof_digest, authorization_digest, created_at) "
            "VALUES (?, 'APP-2', 1, 2, 'manual_retry_approved', 'effect-resume', ?, ?, 900)",
        ),
        (
            "campaign_parent_snapshot_advancements",
            "INSERT INTO campaign_parent_snapshot_advancements "
            "(campaign_id, parent_id, effect_id, advancement_digest, advancement_json, "
            "status, receipt_digest, created_at, completed_at) "
            "VALUES (?, 'APP-1', 'effect-parent', ?, '{}', 'completed', ?, 900, 901)",
        ),
        (
            "campaign_legacy_parent_requalifications",
            "INSERT INTO campaign_legacy_parent_requalifications "
            "(campaign_id, audit_digest, audit_json, recorded_at) "
            "VALUES (?, ?, ?, 900)",
        ),
    ),
)
def test_review_waiting_reconciliation_refuses_auxiliary_or_parent_effects(
    tmp_path, table, insert,
):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2)
    prepared_review_waiting_state(tmp_path, item)
    campaign_path = (
        tmp_path / "command-worker" / item.project / "campaign-runtime"
        / "campaigns" / item.id
    )
    with sqlite3.connect(campaign_path / "campaign.sqlite3") as connection:
        connection.execute(insert, (item.id, DIGEST_A, DIGEST_B))

    with pytest.raises(
        CampaignReconcileError,
        match="état review-waiting non réconciliable",
    ):
        reconcile_review_waiting_command(
            Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200,
        )

    reservation = HostReservationStore(
        tmp_path / "command-worker" / "host-reservations.sqlite3",
    ).get(item.id)
    assert reservation is not None and reservation.state == "active"


def test_review_invocation_requires_fresh_capacity_after_historical_release(tmp_path):
    item = review_waiting_command(max_cost_cents=2_000, max_concurrency=2)
    prepared_review_waiting_state(tmp_path, item)
    reconcile_review_waiting_command(Client(item), item.id, state_dir=tmp_path, now_ms=lambda: 1_200)
    reservations = HostReservationStore(tmp_path / "command-worker" / "host-reservations.sqlite3")

    review_command = command(
        id="command-review-fresh", max_cost_cents=2_000,
        max_concurrency=1, projection_stale=False, state="running",
    )

    class ReviewProvider:
        reviews = 0

        def execution_profile(self, _command, _receipt):
            return ExecutionProfile("frontier", 1_000)

        def launch(
            self, current, _authorization, *, effect_id, heartbeat,
            reconcile_capacity,
        ):
            reservation = reservations.get(effect_id)
            assert reservation is not None and reservation.state == "active"
            assert reservation.reserved_at > 1_200
            reconcile_capacity(RevalidationObservation(
                command_id=current.id, project=current.project,
                preview_id=current.preview_id, approval_id=current.approval_id,
                approval_state="approved", epic_id=current.epic_id,
                epic_version=4,
                planning_version_id=current.planning_version_id,
                preview_digest=current.preview_digest,
                snapshot_digest=current.snapshot_digest,
                policy_digest=current.policy_digest,
                preview_expires_at=10_000, approval_expires_at=9_000,
                approved_minimum_tier="frontier",
                current_minimum_tier="frontier",
                local_max_cost_cents=2_000, local_max_concurrency=1,
                budget_remaining_cents=1_000, active_concurrency=0,
                observed_at=1_202, valid_until=2_000, host_available=True,
                required_gates=frozenset({
                    "tests", "independent-review", "human-test",
                }),
                provider_invocation_ceiling_cents=1_000,
            ))
            return self.review(current)

        def review(self, current):
            self.reviews += 1
            return EffectReceipt(current.id, "completed", DIGEST_B, 25, 1)

        def resume(self, *_args, **_kwargs):
            raise AssertionError("nouvelle invocation de review attendue")

    provider = ReviewProvider()
    invocation = BoundedExecutionPath(
        provider, reservations, now_ms=lambda: 1_203,
    )
    with pytest.raises(CommandWorkerError, match="budget hôte Foundry refusé"):
        invocation.invoke(
            review_command, _binding_digest(review_command), None,
            EffectiveLimits(2_000, 1, 1_000, "frontier", 999, 0, 1_201),
            heartbeat=lambda: None,
        )
    assert provider.reviews == 0
    assert reservations.get(review_command.id) is None

    receipt = invocation.invoke(
        review_command, _binding_digest(review_command), None,
        EffectiveLimits(2_000, 1, 1_000, "frontier", 1_000, 0, 1_202),
        heartbeat=lambda: None,
    )
    assert receipt.status == "completed"
    assert provider.reviews == 1
    historical = reservations.get(item.id)
    assert historical is not None
    assert historical.state == "settled"
    assert historical.reconciliation_reason == "review_waiting_campaign_ledger"
    assert historical.settled_cost_cents == 27
