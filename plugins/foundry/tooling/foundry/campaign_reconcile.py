"""Explicit, fail-closed reconciliation for a campaign that never launched an agent."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

from foundry.campaign_coordinator import CampaignError, CampaignStore
from foundry.command_worker import (
    CommandWorkerError,
    HostUnavailableReservationProof,
    HostReservationStore,
    ReviewWaitingReservationProof,
    UnlaunchedReservationProof,
    _binding_digest,
)
from foundry.devhub_commands import DevHubCommand, DevHubCommandClient


class CampaignReconcileError(RuntimeError):
    """The historical command cannot be safely released."""


def _campaign_command_coordinates(command: DevHubCommand) -> dict[str, object]:
    """Return the immutable command fields also persisted by CampaignSpec."""
    return {
        "command_id": command.id,
        "project": command.project,
        "epic_id": command.epic_id,
        "preview_id": command.preview_id,
        "approval_id": command.approval_id,
        "planning_version_id": command.planning_version_id,
        "preview_digest": command.preview_digest,
        "snapshot_digest": command.snapshot_digest,
        "policy_digest": command.policy_digest,
        "max_concurrency": command.max_concurrency,
        "scheduled_for": command.scheduled_for,
    }


def _reconciliation_context(
    client: DevHubCommandClient, command_id: str, *, state_dir: str | Path,
    now_ms: Callable[[], int] | None,
) -> tuple[DevHubCommand, int, Path, Path]:
    """Resolve one non-resumable command and its existing local ledgers."""
    clock = now_ms or (lambda: int(time.time() * 1000))
    now = clock()
    if type(now) is not int or now < 0:
        raise CampaignReconcileError("horloge de réconciliation invalide")
    try:
        command = client.get_command(command_id)
    except Exception as exc:
        raise CampaignReconcileError("commande DevHub indisponible") from exc
    if not isinstance(command, DevHubCommand):
        raise CampaignReconcileError("commande DevHub invalide")
    if command.state != "unknown" or not command.projection_stale:
        raise CampaignReconcileError("commande DevHub encore récupérable")
    if command.lease is not None and command.lease.expires_at >= now:
        raise CampaignReconcileError("lease DevHub encore saine")
    root = Path(state_dir).expanduser().resolve() / "command-worker"
    campaign_path = root / command.project / "campaign-runtime" / "campaigns" / command.id
    ledger_path = campaign_path / "campaign.sqlite3"
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise CampaignReconcileError("ledger de campagne absent")
    return command, now, root, campaign_path


def _review_waiting_reconciliation_context(
    client: DevHubCommandClient, command_id: str, *, state_dir: str | Path,
    now_ms: Callable[[], int] | None,
) -> tuple[DevHubCommand, int, Path, Path]:
    """Resolve a stable, paused review gate and its existing local ledgers.

    Unlike unknown/stale recovery, this seam accepts only a current DevHub pause.
    A stale projection would leave the gate and any possible child work ambiguous.
    """
    clock = now_ms or (lambda: int(time.time() * 1000))
    now = clock()
    if type(now) is not int or now < 0:
        raise CampaignReconcileError("horloge de réconciliation invalide")
    try:
        command = client.get_command(command_id)
    except Exception as exc:
        raise CampaignReconcileError("commande DevHub indisponible") from exc
    if not isinstance(command, DevHubCommand):
        raise CampaignReconcileError("commande DevHub invalide")
    if command.state != "paused" or command.projection_stale:
        raise CampaignReconcileError("commande DevHub review-waiting non stable")
    if command.lease is not None and command.lease.expires_at >= now:
        raise CampaignReconcileError("lease DevHub encore saine")
    root = Path(state_dir).expanduser().resolve() / "command-worker"
    campaign_path = root / command.project / "campaign-runtime" / "campaigns" / command.id
    ledger_path = campaign_path / "campaign.sqlite3"
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise CampaignReconcileError("ledger de campagne absent")
    return command, now, root, campaign_path


def reconcile_unlaunched_command(
    client: DevHubCommandClient, command_id: str, *, state_dir: str | Path,
    now_ms: Callable[[], int] | None = None,
) -> dict[str, object]:
    """Settle one stale, expired, pre-implementation campaign at zero cost.

    A normal campaign must continue through its command journal. This recovery seam is
    intentionally available only when the remote command is already stale/unknown and
    its local campaign ledger proves that it stopped before implementation intent.
    """
    command, now, root, campaign_path = _reconciliation_context(
        client, command_id, state_dir=state_dir, now_ms=now_ms,
    )
    ledger = CampaignStore(campaign_path / "campaign.sqlite3")
    try:
        proof = ledger.unlaunched_reservation_proof(
            command.id, _binding_digest(command), observed_at=now,
        )
        reservation = HostReservationStore(root / "host-reservations.sqlite3")
        settled = reservation.reconcile_unlaunched(
            UnlaunchedReservationProof(
                proof.campaign_id, proof.binding_digest, proof.proof_digest,
                proof.observed_at,
            ),
            now_ms=now,
        )
    except (CampaignError, CommandWorkerError, ValueError, OSError) as exc:
        raise CampaignReconcileError(str(exc)) from None
    return {
        "command_id": settled.command_id,
        "state": settled.state,
        "settled_cost_cents": settled.settled_cost_cents,
        "reconciliation_reason": settled.reconciliation_reason,
        "reconciliation_proof_digest": settled.reconciliation_proof_digest,
        "reconciled_at": settled.reconciled_at,
    }


def reconcile_host_unavailable_command(
    client: DevHubCommandClient, command_id: str, *, state_dir: str | Path,
    now_ms: Callable[[], int] | None = None,
) -> dict[str, object]:
    """Settle one stale campaign whose complete ledgers prove zero-cost host failure."""
    command, now, root, campaign_path = _reconciliation_context(
        client, command_id, state_dir=state_dir, now_ms=now_ms,
    )
    ledger = CampaignStore(campaign_path / "campaign.sqlite3")
    reservation_path = root / "host-reservations.sqlite3"
    if reservation_path.is_symlink() or not reservation_path.is_file():
        raise CampaignReconcileError("ledger de réservation host-unavailable absent")
    reservations = HostReservationStore(reservation_path)
    try:
        campaign_proof = ledger.host_unavailable_reservation_proof(
            command.id, _binding_digest(command),
            receipts_path=campaign_path / "primitive-receipts.sqlite3",
            expected_command=_campaign_command_coordinates(command),
            observed_at=now,
        )
        current = reservations.get(command.id)
        if current is None:
            raise CommandWorkerError("réservation host-unavailable absente")
        if (
            current.cost_ceiling_cents != campaign_proof.budget_cents
            or current.concurrency_units != campaign_proof.max_concurrency
        ):
            raise CommandWorkerError("capacité de campagne host-unavailable contradictoire")
        proof = HostUnavailableReservationProof(
            campaign_proof.campaign_id, campaign_proof.binding_digest,
            campaign_proof.proof_digest, current.reserved_at,
            current.cost_ceiling_cents, current.concurrency_units,
            campaign_proof.observed_at,
        )
        settled = reservations.reconcile_host_unavailable(proof, now_ms=now)
    except (CampaignError, CommandWorkerError, ValueError, OSError) as exc:
        raise CampaignReconcileError(str(exc)) from None
    return {
        "command_id": settled.command_id,
        "state": settled.state,
        "settled_cost_cents": settled.settled_cost_cents,
        "reconciliation_reason": settled.reconciliation_reason,
        "reconciliation_proof_digest": settled.reconciliation_proof_digest,
        "reconciled_at": settled.reconciled_at,
        "released_cost_ceiling_cents": settled.cost_ceiling_cents,
        "released_concurrency_units": settled.concurrency_units,
    }


def reconcile_review_waiting_command(
    client: DevHubCommandClient, command_id: str, *, state_dir: str | Path,
    now_ms: Callable[[], int] | None = None,
) -> dict[str, object]:
    """Settle a stable paused review gate after complete local proof only.

    The campaign ledger stays immutable. Its write lock spans proof validation and the
    single host-row settlement, so a lease, remediation, or downstream campaign effect
    cannot appear between those two operations.
    """
    command, now, root, campaign_path = _review_waiting_reconciliation_context(
        client, command_id, state_dir=state_dir, now_ms=now_ms,
    )
    ledger = CampaignStore(campaign_path / "campaign.sqlite3")
    reservation_path = root / "host-reservations.sqlite3"
    if reservation_path.is_symlink() or not reservation_path.is_file():
        raise CampaignReconcileError("ledger de réservation review-waiting absent")
    reservations = HostReservationStore(reservation_path)

    def settle(campaign_proof):
        current = reservations.get(command.id)
        if current is None:
            raise CommandWorkerError("réservation review-waiting absente")
        if (
            current.cost_ceiling_cents != campaign_proof.budget_cents
            or current.concurrency_units != campaign_proof.max_concurrency
            or campaign_proof.spent_cents > current.cost_ceiling_cents
        ):
            raise CommandWorkerError("capacité de campagne review-waiting contradictoire")
        proof = ReviewWaitingReservationProof(
            campaign_proof.campaign_id, campaign_proof.binding_digest,
            campaign_proof.proof_digest, current.reserved_at,
            current.cost_ceiling_cents, current.concurrency_units,
            campaign_proof.spent_cents, campaign_proof.observed_at,
        )
        return reservations.reconcile_review_waiting(proof, now_ms=now)

    try:
        settled = ledger.settle_review_waiting_reservation(
            command.id, _binding_digest(command),
            receipts_path=campaign_path / "primitive-receipts.sqlite3",
            expected_command=_campaign_command_coordinates(command), observed_at=now,
            settle=settle,
        )
    except (CampaignError, CommandWorkerError, ValueError, OSError) as exc:
        raise CampaignReconcileError(str(exc)) from None
    return {
        "command_id": settled.command_id,
        "state": settled.state,
        "settled_cost_cents": settled.settled_cost_cents,
        "reconciliation_reason": settled.reconciliation_reason,
        "reconciliation_proof_digest": settled.reconciliation_proof_digest,
        "reconciled_at": settled.reconciled_at,
        "released_cost_ceiling_cents": settled.cost_ceiling_cents,
        "released_concurrency_units": settled.concurrency_units,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Réconcilie une campagne stale prouvée non lancée",
    )
    parser.add_argument("command_id")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument(
        "--mode", choices=("unlaunched", "host-unavailable", "review-waiting"), default="unlaunched",
    )
    args = parser.parse_args(argv)
    try:
        reconcile = {
            "unlaunched": reconcile_unlaunched_command,
            "host-unavailable": reconcile_host_unavailable_command,
            "review-waiting": reconcile_review_waiting_command,
        }[args.mode]
        result = reconcile(DevHubCommandClient(), args.command_id, state_dir=args.state_dir)
    except (CampaignReconcileError, ValueError, OSError) as exc:
        raise SystemExit(f"⛔ Réconciliation refusée : {exc}") from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
