from __future__ import annotations

import multiprocessing
import sqlite3
import time

import pytest

from foundry.command_worker import (
    BoundedExecutionPath,
    CommandWorkerError,
    EffectReceipt,
    EffectiveLimits,
    ExecutionProfile,
    HostUnavailableReservationProof,
    HostReservationStore,
    RESERVATION_OWNER_TTL_MS,
    RevalidationObservation,
    UnlaunchedReservationProof,
)
from foundry.devhub_commands import DevHubCommand


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def command(command_id: str = "command-1") -> DevHubCommand:
    return DevHubCommand(
        id=command_id, project="DEVHUB", preview_id="preview-1",
        approval_id="approval-1", preview_digest=DIGEST_A,
        snapshot_digest=DIGEST_B, policy_digest=DIGEST_C,
        epic_id="DEVHUB-20", planning_version_id="DEVHUB-VERSION-1",
        max_cost_cents=500, max_concurrency=4, state="requested",
        projection_stale=False, next_event_sequence=1, scheduled_for=None,
        lease=None, version=3, created_at=100, updated_at=100,
    )


def limits(
    *, budget: int = 300, maximum: int = 3, active: int = 0,
    observed_at: int = 9_000, max_cost: int | None = None,
) -> EffectiveLimits:
    return EffectiveLimits(
        max_cost or budget, maximum, min(max_cost or budget, budget),
        "frontier", budget, active, observed_at,
    )


def observation(
    item: DevHubCommand, *, budget: int = 300, maximum: int = 3,
    active: int = 0, observed_at: int = 9_000,
) -> RevalidationObservation:
    return RevalidationObservation(
        command_id=item.id, project=item.project,
        preview_id=item.preview_id, approval_id=item.approval_id,
        approval_state="approved",
        epic_id=item.epic_id, epic_version=4,
        planning_version_id=item.planning_version_id,
        preview_digest=item.preview_digest, snapshot_digest=item.snapshot_digest,
        policy_digest=item.policy_digest, preview_expires_at=20_000,
        approval_expires_at=19_000, approved_minimum_tier="frontier",
        current_minimum_tier="frontier", local_max_cost_cents=500,
        local_max_concurrency=maximum, budget_remaining_cents=budget,
        active_concurrency=active, observed_at=observed_at,
        valid_until=15_000, host_available=True,
        required_gates=frozenset({"tests", "independent-review", "human-test"}),
        provider_invocation_ceiling_cents=min(300, budget),
    )


def _reserve_in_process(args):
    (
        path, command_id, owner_id, cost, units, budget, maximum, active,
        observed_at, now_ms, start_at,
    ) = args
    store = HostReservationStore(path)
    while time.time() < start_at:
        time.sleep(0.001)
    try:
        store.reserve(
            command_id, DIGEST_A, ExecutionProfile("frontier", cost, units),
            limits(
                budget=budget, maximum=maximum, active=active,
                observed_at=observed_at, max_cost=cost,
            ),
            owner_id=owner_id, now_ms=now_ms,
        )
        return "reserved"
    except CommandWorkerError as exc:
        return str(exc)


def _run_reservation_race(path, *, cost, budget, maximum, active):
    start_at = time.time() + 0.5
    common = (
        str(path), None, None, cost, 1, budget, maximum, active,
        9_000, 10_000, start_at,
    )
    args = [
        (common[0], f"command-{index}", f"owner-{index}", *common[3:])
        for index in (1, 2)
    ]
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        return pool.map(_reserve_in_process, args)


def test_concurrent_processes_cannot_oversubscribe_external_active_capacity(tmp_path):
    results = _run_reservation_race(
        tmp_path / "capacity.sqlite3",
        cost=100, budget=1_000, maximum=2, active=1,
    )

    assert results.count("reserved") == 1
    assert sum("capacité hôte" in result for result in results) == 1


def test_concurrent_processes_cannot_oversubscribe_observed_budget(tmp_path):
    results = _run_reservation_race(
        tmp_path / "budget.sqlite3",
        cost=200, budget=300, maximum=4, active=0,
    )

    assert results.count("reserved") == 1
    assert sum("budget hôte" in result for result in results) == 1


def test_effect_identity_reservation_is_idempotent_but_not_double_owned(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    profile = ExecutionProfile("frontier", 100)
    bounded = limits(budget=200, maximum=2, max_cost=100)

    first = store.reserve(
        "command-1", DIGEST_A, profile, bounded,
        owner_id="owner-1", now_ms=10_000,
    )
    repeated = store.reserve(
        "command-1", DIGEST_A, profile, bounded,
        owner_id="owner-1", now_ms=10_001,
    )
    second = store.reserve(
        "command-2", DIGEST_A, profile, bounded,
        owner_id="owner-2", now_ms=10_002,
    )

    assert repeated.command_id == first.command_id
    assert second.state == "active"
    with pytest.raises(CommandWorkerError, match="déjà active"):
        store.reserve(
            "command-1", DIGEST_A, profile, bounded,
            owner_id="owner-3", now_ms=10_003,
        )


def test_terminal_settlement_releases_capacity_and_charges_only_actual_cost(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    bounded = limits(budget=300, maximum=1, max_cost=200)
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 200), bounded,
        owner_id="owner-1", now_ms=10_000,
    )

    settled = store.settle(
        "command-1", DIGEST_A,
        EffectReceipt("command-1", "completed", DIGEST_B, 120, 20),
        owner_id="owner-1", now_ms=10_001,
    )

    assert settled is not None
    assert settled.state == "settled"
    assert settled.owner_id is None
    assert settled.settled_cost_cents == 120
    with pytest.raises(CommandWorkerError, match="budget hôte"):
        store.reserve(
            "command-2", DIGEST_A, ExecutionProfile("frontier", 181),
            limits(budget=300, maximum=1, max_cost=181),
            owner_id="owner-2", now_ms=10_002,
        )
    assert store.reserve(
        "command-2", DIGEST_A, ExecutionProfile("frontier", 180),
        limits(budget=300, maximum=1, max_cost=180),
        owner_id="owner-2", now_ms=10_003,
    ).state == "active"


def test_unlaunched_reconciliation_releases_only_the_exact_orphan_once(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    profile = ExecutionProfile("frontier", 200)
    bounded = limits(budget=200, maximum=1, max_cost=200)
    store.reserve(
        "command-1", DIGEST_A, profile, bounded,
        owner_id="owner-1", now_ms=10_000,
    )
    store.yield_owner("command-1", DIGEST_A, "owner-1")
    proof = UnlaunchedReservationProof("command-1", DIGEST_A, DIGEST_B, 10_001)

    settled = store.reconcile_unlaunched(proof, now_ms=10_002)

    assert settled.state == "settled"
    assert settled.settled_cost_cents == 0
    assert settled.reconciliation_reason == "unlaunched_campaign_ledger"
    assert settled.reconciliation_proof_digest == DIGEST_B
    assert store.reconcile_unlaunched(proof, now_ms=10_003) == settled
    with pytest.raises(CommandWorkerError, match="terminale"):
        store.reconcile_unlaunched(
            UnlaunchedReservationProof("command-1", DIGEST_A, DIGEST_C, 10_003),
            now_ms=10_004,
        )
    assert store.reserve(
        "command-2", DIGEST_A, profile,
        limits(budget=200, maximum=1, observed_at=10_004, max_cost=200),
        owner_id="owner-2", now_ms=10_005,
    ).state == "active"


def test_unlaunched_reconciliation_refuses_an_owned_reservation(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 100),
        limits(budget=100, maximum=1, max_cost=100),
        owner_id="owner-1", now_ms=10_000,
    )

    with pytest.raises(CommandWorkerError, match="encore possédée"):
        store.reconcile_unlaunched(
            UnlaunchedReservationProof("command-1", DIGEST_A, DIGEST_B, 10_001),
            now_ms=10_002,
        )


def test_host_unavailable_reconciliation_binds_capacity_and_is_idempotent(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 200, 2),
        limits(budget=200, maximum=2, max_cost=200),
        owner_id="owner-1", now_ms=10_000,
    )
    store.yield_owner("command-1", DIGEST_A, "owner-1")
    proof = HostUnavailableReservationProof(
        "command-1", DIGEST_A, DIGEST_B, 10_000, 200, 2, 10_001,
    )

    settled = store.reconcile_host_unavailable(proof, now_ms=10_002)

    assert settled.state == "settled"
    assert settled.settled_cost_cents == 0
    assert settled.reconciliation_reason == "host_unavailable_campaign_ledger"
    assert settled.reconciliation_proof_digest == proof.proof_digest
    assert store.reconcile_host_unavailable(proof, now_ms=10_003) == settled


def test_host_unavailable_reconciliation_refuses_changed_capacity(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 200, 2),
        limits(budget=200, maximum=2, max_cost=200),
        owner_id="owner-1", now_ms=10_000,
    )
    store.yield_owner("command-1", DIGEST_A, "owner-1")

    with pytest.raises(CommandWorkerError, match="capacité host-unavailable contradictoire"):
        store.reconcile_host_unavailable(
            HostUnavailableReservationProof(
                "command-1", DIGEST_A, DIGEST_B, 10_000, 199, 2, 10_001,
            ),
            now_ms=10_002,
        )
    assert store.get("command-1").state == "active"


def test_newer_external_budget_observation_reconciles_prior_settlement(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 200),
        limits(budget=300, maximum=1, max_cost=200),
        owner_id="owner-1", now_ms=10_000,
    )
    store.settle(
        "command-1", DIGEST_A,
        EffectReceipt("command-1", "completed", DIGEST_B, 120, 20),
        owner_id="owner-1", now_ms=10_001,
    )

    reservation = store.reserve(
        "command-2", DIGEST_A, ExecutionProfile("frontier", 180),
        limits(
            budget=180, maximum=1, observed_at=10_002, max_cost=180,
        ),
        owner_id="owner-2", now_ms=10_003,
    )

    assert reservation.state == "active"


def test_crash_recovery_requires_expired_owner_and_proven_effect(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    profile = ExecutionProfile("frontier", 100)
    bounded = limits(budget=200, maximum=1, max_cost=100)
    store.reserve(
        "command-1", DIGEST_A, profile, bounded,
        owner_id="owner-old", now_ms=10_000,
    )
    running = EffectReceipt("command-1", "running", DIGEST_B, 10, 20)

    with pytest.raises(CommandWorkerError, match="déjà active"):
        store.reserve(
            "command-1", DIGEST_A, profile, bounded,
            owner_id="owner-new", now_ms=10_000 + RESERVATION_OWNER_TTL_MS - 1,
            prior_effect=running,
        )
    with pytest.raises(CommandWorkerError, match="déjà active"):
        store.reserve(
            "command-1", DIGEST_A, profile, bounded,
            owner_id="owner-new", now_ms=10_000 + RESERVATION_OWNER_TTL_MS,
        )

    recovered = store.reserve(
        "command-1", DIGEST_A, profile, bounded,
        owner_id="owner-new", now_ms=10_000 + RESERVATION_OWNER_TTL_MS,
        prior_effect=running,
    )
    assert recovered.owner_id == "owner-new"
    store.settle(
        "command-1", DIGEST_A,
        EffectReceipt("command-1", "completed", DIGEST_C, 50, 30),
        owner_id="owner-new", now_ms=10_001 + RESERVATION_OWNER_TTL_MS,
    )
    assert store.reserve(
        "command-2", DIGEST_A, profile,
        limits(
            budget=150, maximum=1,
            observed_at=10_002 + RESERVATION_OWNER_TTL_MS, max_cost=100,
        ),
        owner_id="owner-2", now_ms=10_003 + RESERVATION_OWNER_TTL_MS,
    ).state == "active"


def test_proven_running_effect_can_only_tighten_its_durable_concurrency(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    initial = ExecutionProfile("frontier", 200, concurrency_units=2)
    store.reserve(
        "command-1", DIGEST_A, initial,
        limits(budget=300, maximum=3, max_cost=200),
        owner_id="owner-old", now_ms=10_000,
    )
    store.yield_owner("command-1", DIGEST_A, "owner-old")
    running = EffectReceipt("command-1", "running", DIGEST_B, 10, 20)

    tightened = store.reserve(
        "command-1", DIGEST_A,
        ExecutionProfile("frontier", 200, concurrency_units=1),
        limits(budget=300, maximum=1, max_cost=200),
        owner_id="owner-new", now_ms=10_001, prior_effect=running,
    )

    assert tightened.concurrency_units == 1
    store.yield_owner("command-1", DIGEST_A, "owner-new")
    with pytest.raises(CommandWorkerError, match="contradictoire"):
        store.reserve(
            "command-1", DIGEST_A, initial,
            limits(budget=300, maximum=3, max_cost=200),
            owner_id="owner-third", now_ms=10_002, prior_effect=running,
        )


def test_budget_overrun_never_releases_the_original_allocation(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    bounded = limits(budget=100, maximum=4, max_cost=100)
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 100), bounded,
        owner_id="owner-1", now_ms=10_000,
    )

    with pytest.raises(CommandWorkerError, match="hors réservation"):
        store.settle(
            "command-1", DIGEST_A,
            EffectReceipt("command-1", "failed", DIGEST_B, 101, 20),
            owner_id="owner-1", now_ms=10_001,
        )

    assert store.get("command-1").state == "active"
    with pytest.raises(CommandWorkerError, match="budget hôte"):
        store.reserve(
            "command-2", DIGEST_A, ExecutionProfile("frontier", 1),
            limits(budget=100, maximum=4, max_cost=1),
            owner_id="owner-2", now_ms=10_002,
        )


@pytest.mark.parametrize(("latest_budget", "latest_active", "message"), [
    (150, 1, "budget hôte"),
    (300, 2, "capacité hôte"),
])
def test_later_external_limit_is_reconciled_with_local_rows_before_launch(
    tmp_path, latest_budget, latest_active, message,
):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 100),
        limits(budget=300, maximum=3, active=1, max_cost=100),
        owner_id="owner-1", now_ms=10_000,
    )

    class Provider:
        launches = 0

        def execution_profile(self, _command, _receipt):
            return ExecutionProfile("frontier", 100)

        def launch(
            self, item, _authorization, *, effect_id, heartbeat,
            reconcile_capacity,
        ):
            current = HostReservationStore(store.path).get(effect_id)
            assert current is not None
            assert current.state == "active"
            reconcile_capacity(observation(
                item, budget=latest_budget, maximum=3,
                active=latest_active, observed_at=9_500,
            ))
            self.launches += 1
            return EffectReceipt(item.id, "completed", DIGEST_B, 50, 20)

        def resume(self, *_args, **_kwargs):
            raise AssertionError("launch attendu")

    provider = Provider()
    with pytest.raises(CommandWorkerError, match=message):
        BoundedExecutionPath(
            provider, store, now_ms=lambda: 10_000,
        ).invoke(
            command("command-2"), DIGEST_A, None,
            limits(budget=300, maximum=3, active=1, max_cost=100),
            heartbeat=lambda: None,
        )

    assert provider.launches == 0


@pytest.mark.parametrize("corruption", [
    "unknown-state",
    "unreadable-cost",
    "negative-concurrency",
    "overflowed-cost",
    "owner-before-reservation",
])
def test_corrupt_non_current_ledger_row_fails_closed_before_effect(
    tmp_path, corruption,
):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    store.reserve(
        "command-1", DIGEST_A, ExecutionProfile("frontier", 100),
        limits(budget=300, maximum=3, max_cost=100),
        owner_id="owner-1", now_ms=10_000,
    )
    mutations = {
        "unknown-state": ("state", "unknown"),
        "unreadable-cost": ("cost_ceiling_cents", "unreadable"),
        "negative-concurrency": ("concurrency_units", -1),
        "overflowed-cost": ("cost_ceiling_cents", 9_223_372_036_854_775_807),
        "owner-before-reservation": ("owner_expires_at", 9_999),
    }
    column, value = mutations[corruption]
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            f"UPDATE host_reservations SET {column} = ? WHERE command_id = ?",
            (value, "command-1"),
        )

    class Provider:
        launches = 0

        def execution_profile(self, _command, _receipt):
            return ExecutionProfile("frontier", 1)

        def launch(self, *_args, **_kwargs):
            self.launches += 1
            raise AssertionError("effet interdit")

        def resume(self, *_args, **_kwargs):
            raise AssertionError("reprise interdite")

    provider = Provider()
    with pytest.raises(CommandWorkerError, match="ledger de réservation Foundry corrompu"):
        BoundedExecutionPath(
            provider, store, now_ms=lambda: 10_001,
        ).invoke(
            command("command-2"), DIGEST_A, None,
            limits(budget=300, maximum=3, max_cost=1), heartbeat=lambda: None,
        )

    assert provider.launches == 0


def test_settled_row_before_reservation_and_observation_blocks_new_effect(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")
    profile = ExecutionProfile("frontier", 100)
    bounded = limits(budget=100, maximum=1, observed_at=9_000, max_cost=100)
    store.reserve(
        "command-1", DIGEST_A, profile, bounded,
        owner_id="owner-1", now_ms=10_000,
    )
    store.settle(
        "command-1", DIGEST_A,
        EffectReceipt("command-1", "completed", DIGEST_B, 100, 20),
        owner_id="owner-1", now_ms=10_001,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE host_reservations SET settled_at = ? WHERE command_id = ?",
            (8_999, "command-1"),
        )

    class Provider:
        launches = 0

        def execution_profile(self, _command, _receipt):
            return profile

        def launch(self, *_args, **_kwargs):
            self.launches += 1
            raise AssertionError("effet interdit")

        def resume(self, *_args, **_kwargs):
            raise AssertionError("reprise interdite")

    provider = Provider()
    with pytest.raises(CommandWorkerError, match="ledger de réservation Foundry corrompu"):
        BoundedExecutionPath(
            provider, store, now_ms=lambda: 10_002,
        ).invoke(
            command("command-2"), DIGEST_A, None, bounded,
            heartbeat=lambda: None,
        )

    assert provider.launches == 0
    assert store.get("command-2") is None


def test_reservation_is_durable_before_provider_launch_and_settled_afterward(tmp_path):
    store = HostReservationStore(tmp_path / "host.sqlite3")

    class Provider:
        def execution_profile(self, _command, _receipt):
            return ExecutionProfile("frontier", 100)

        def launch(
            self, item, _authorization, *, effect_id, heartbeat,
            reconcile_capacity,
        ):
            reservation = HostReservationStore(store.path).get(effect_id)
            assert reservation is not None
            assert reservation.state == "active"
            assert reservation.owner_id is not None
            reconcile_capacity(observation(item, budget=100, maximum=1))
            return EffectReceipt(item.id, "completed", DIGEST_B, 75, 20)

        def resume(self, *_args, **_kwargs):
            raise AssertionError("launch attendu")

    item = command()
    effect = BoundedExecutionPath(
        Provider(), store, now_ms=lambda: 10_000,
    ).invoke(
        item, DIGEST_A, None,
        limits(budget=100, maximum=1, max_cost=100), heartbeat=lambda: None,
    )

    assert effect.status == "completed"
    settled = store.get(item.id)
    assert settled.state == "settled"
    assert settled.settled_cost_cents == 75
