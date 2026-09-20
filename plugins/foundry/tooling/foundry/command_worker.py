"""Bounded pull/lease worker for DevHub execution intents.

DevHub is the durable intention store; this module remains a Foundry orchestrator.
It revalidates every binding before an effect, journals effect identity locally, and
requires providers to resolve an ambiguous command ID before any further launch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from foundry.devhub_commands import (
    DevHubCommand,
    DevHubCommandClient,
    DevHubCommandError,
    DevHubCommandTransportError,
)
from foundry.devhub_events import PassiveEventPublisher
from foundry.execution_receipts import ExecutionReceiptStore, ReceiptStoreError
from foundry.routing import LEVELS


RECEIPT_CONTRACT = "foundry-command-receipt.v1"
RESERVATION_CONTRACT = "foundry-host-reservation.v1"
REQUIRED_GATES = frozenset({"tests", "independent-review", "human-test"})
MAX_PULL_PAGES = 10
MAX_COMMANDS_PER_RUN = 16
MAX_RECEIPT_BYTES = 16_384
RESERVATION_OWNER_TTL_MS = 30_000
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}$")
_RESUMABLE_EFFECT_STATUSES = {"running", "paused"}
_EFFECT_STATUSES = _RESUMABLE_EFFECT_STATUSES | {
    "cancelled", "completed", "failed",
}
_TERMINAL_EFFECT_STATUSES = {"cancelled", "completed", "failed"}
_CONTROL_REQUEST_STATES = {"pause-requested", "cancel-requested"}
_PRE_EFFECT_PREAMBLE_PHASE = "pre-effect-preamble"
_PRE_EFFECT_PAUSE_PHASE = "pre-effect-paused"
_HUMAN_PAUSE_PHASE = "human-paused"
_HUMAN_CANCEL_PHASE = "human-cancelled"
_HUMAN_CONTROL_PHASES = {
    _HUMAN_PAUSE_PHASE: "paused",
    _HUMAN_CANCEL_PHASE: "cancelled",
}
_TERMINAL_RECEIPT_PHASES = {"terminal", _HUMAN_CANCEL_PHASE}


class CommandWorkerError(RuntimeError):
    """Fail-closed worker error with no provider payload or credential material."""


class CommandRevalidationError(CommandWorkerError):
    pass


class PreEffectCapacityError(CommandWorkerError):
    """A deterministic host-capacity refusal before any provider effect."""


class AmbiguousEffectError(CommandWorkerError):
    """An effect may exist; only identity resolution can make progress safe."""


@dataclass(frozen=True)
class SnapshotAdvancementObservation:
    """One campaign-owned monotone parent mutation recognized by the outer worker."""

    command_id: str
    effect_id: str
    binding_digest: str
    snapshot_digest: str
    from_version: int
    to_version: int
    advancement_digest: str
    primitive_receipt_digest: str

    def __post_init__(self) -> None:
        if (_IDENTIFIER.fullmatch(self.command_id) is None
                or _IDENTIFIER.fullmatch(self.effect_id) is None
                or type(self.from_version) is not int or self.from_version < 1
                or type(self.to_version) is not int
                or self.to_version != self.from_version + 1
                or any(_DIGEST.fullmatch(value) is None for value in (
                    self.binding_digest, self.snapshot_digest,
                    self.advancement_digest, self.primitive_receipt_digest,
                ))):
            raise ValueError("observation avancement snapshot invalide")


@dataclass(frozen=True)
class RevalidationObservation:
    """Fresh Foundry-owned observation of the approved execution binding."""

    command_id: str
    project: str
    preview_id: str
    approval_id: str
    approval_state: str
    epic_id: str
    epic_version: int
    planning_version_id: str
    preview_digest: str
    snapshot_digest: str
    policy_digest: str
    preview_expires_at: int
    approval_expires_at: int
    approved_minimum_tier: str | None
    current_minimum_tier: str | None
    local_max_cost_cents: int
    local_max_concurrency: int
    budget_remaining_cents: int
    active_concurrency: int
    observed_at: int
    valid_until: int
    host_available: bool
    required_gates: frozenset[str]
    provider_invocation_ceiling_cents: int | None = None
    snapshot_advancement: SnapshotAdvancementObservation | None = None


@dataclass(frozen=True)
class EffectiveLimits:
    max_cost_cents: int
    max_concurrency: int
    provider_invocation_ceiling_cents: int
    minimum_tier: str | None
    observed_budget_remaining_cents: int
    observed_active_concurrency: int
    observed_at: int


@dataclass(frozen=True)
class ExecutionProfile:
    """Provider-declared worst-case resources, checked before any effect."""

    selected_tier: str
    cost_ceiling_cents: int
    concurrency_units: int = 1

    def __post_init__(self) -> None:
        if self.selected_tier not in LEVELS:
            raise ValueError("tier d'exécution Foundry invalide")
        if (type(self.cost_ceiling_cents) is not int
                or not 1 <= self.cost_ceiling_cents <= 1_000_000):
            raise ValueError("plafond d'exécution Foundry invalide")
        if (type(self.concurrency_units) is not int
                or not 1 <= self.concurrency_units <= 16):
            raise ValueError("concurrence d'exécution Foundry invalide")


@dataclass(frozen=True)
class ExecutionAuthorization:
    """One invocation's immutable, pre-effect authorization envelope."""

    command_id: str
    binding_digest: str
    selected_tier: str
    cost_ceiling_cents: int
    concurrency_units: int
    approved_max_cost_cents: int
    approved_max_concurrency: int
    minimum_tier: str | None
    provider_invocation_ceiling_cents: int

    def __post_init__(self) -> None:
        if (_IDENTIFIER.fullmatch(self.command_id) is None
                or _DIGEST.fullmatch(self.binding_digest) is None
                or self.selected_tier not in LEVELS):
            raise ValueError("autorisation d'exécution Foundry invalide")
        for value, maximum in (
            (self.cost_ceiling_cents, 1_000_000),
            (self.provider_invocation_ceiling_cents, 1_000_000),
            (self.approved_max_cost_cents, 1_000_000),
            (self.concurrency_units, 16),
            (self.approved_max_concurrency, 16),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("autorisation d'exécution Foundry hors borne")
        if (self.cost_ceiling_cents > self.approved_max_cost_cents
                or self.provider_invocation_ceiling_cents
                > self.cost_ceiling_cents
                or self.concurrency_units > self.approved_max_concurrency):
            raise ValueError("autorisation d'exécution Foundry élargie")
        if self.minimum_tier is not None and (
            self.minimum_tier not in LEVELS
            or LEVELS.index(self.selected_tier) < LEVELS.index(self.minimum_tier)
        ):
            raise ValueError("autorisation d'exécution sous le floor")


@dataclass(frozen=True)
class EffectReceipt:
    """Bounded proof returned by the existing Foundry execution provider."""

    effect_id: str
    status: str
    proof_digest: str
    cost_cents: int | None = None
    duration_ms: int | None = None
    attempt_id: str | None = None
    attempt_started_at: int | None = None

    def __post_init__(self) -> None:
        if (_IDENTIFIER.fullmatch(self.effect_id) is None
                or self.status not in _EFFECT_STATUSES
                or _DIGEST.fullmatch(self.proof_digest) is None):
            raise ValueError("reçu d'effet Foundry invalide")
        if (self.cost_cents is not None
                and (type(self.cost_cents) is not int or not 0 <= self.cost_cents <= 1_000_000)):
            raise ValueError("coût reçu Foundry invalide")
        if (self.duration_ms is not None
                and (type(self.duration_ms) is not int or not 0 <= self.duration_ms <= 604_800_000)):
            raise ValueError("durée reçue Foundry invalide")
        if self.attempt_id is not None and _IDENTIFIER.fullmatch(self.attempt_id) is None:
            raise ValueError("identité de tentative Foundry invalide")
        if (self.attempt_started_at is not None
                and (type(self.attempt_started_at) is not int or self.attempt_started_at < 0)):
            raise ValueError("début de tentative Foundry invalide")
        if (self.attempt_id is None) != (self.attempt_started_at is None):
            raise ValueError("preuve de tentative Foundry incomplète")


class CommandEffectProvider(Protocol):
    """Seam to Foundry's existing provider-backed execution loop.

    ``launch`` receives the DevHub command ID as its idempotent effect identity.
    ``resolve`` must inspect that identity without launching. ``resume`` continues a
    proven existing effect and therefore cannot create another one.
    """

    def observe(self, command: DevHubCommand) -> RevalidationObservation: ...

    def resolve(self, command_id: str, binding_digest: str) -> EffectReceipt | None: ...

    def execution_profile(
        self, command: DevHubCommand, receipt: EffectReceipt | None,
    ) -> ExecutionProfile: ...

    def launch(
        self, command: DevHubCommand, authorization: ExecutionAuthorization, *,
        effect_id: str, heartbeat: Callable[[], None],
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt: ...

    def resume(
        self, command: DevHubCommand, receipt: EffectReceipt,
        authorization: ExecutionAuthorization, *,
        heartbeat: Callable[[], None],
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt: ...

    def reconcile_existing(
        self, command: DevHubCommand, receipt: EffectReceipt, *,
        heartbeat: Callable[[], object],
    ) -> EffectReceipt: ...


@dataclass(frozen=True)
class DurableReceipt:
    command_id: str
    project: str
    binding_digest: str
    phase: str
    lease_id: str
    effect: EffectReceipt | None
    updated_at: int


@dataclass(frozen=True)
class WorkerOutcome:
    command_id: str
    status: str
    detail: str


class CommandJournalEventTransport:
    """Passive publisher adapter over the already-authoritative command journal."""

    def __init__(self, client: DevHubCommandClient):
        self.client = client

    def append_event(
        self, command_id: str, expected_version: int, event: dict,
    ) -> dict:
        return self.client.append_passive_observation(command_id, expected_version, event)

    def reconcile(self, command_id: str) -> dict:
        return self.client.reconcile_passive_observations(command_id)


@dataclass(frozen=True)
class HostReservation:
    """One durable host allocation keyed by the provider effect identity."""

    command_id: str
    binding_digest: str
    state: str
    owner_id: str | None
    cost_ceiling_cents: int
    concurrency_units: int
    settled_cost_cents: int | None
    reserved_at: int
    owner_expires_at: int | None
    settled_at: int | None
    reconciliation_proof_digest: str | None = None
    reconciliation_reason: str | None = None
    reconciled_at: int | None = None


@dataclass(frozen=True)
class UnlaunchedReservationProof:
    """Content-free proof that a stopped campaign never crossed implementation."""

    command_id: str
    binding_digest: str
    proof_digest: str
    observed_at: int

    def __post_init__(self) -> None:
        if (_IDENTIFIER.fullmatch(self.command_id) is None
                or _DIGEST.fullmatch(self.binding_digest) is None
                or _DIGEST.fullmatch(self.proof_digest) is None
                or type(self.observed_at) is not int
                or self.observed_at < 0):
            raise ValueError("preuve de réservation non lancée invalide")


@dataclass(frozen=True)
class HostUnavailableReservationProof:
    """Proof binding a zero-cost campaign ledger to the released host capacity."""

    command_id: str
    binding_digest: str
    ledger_proof_digest: str
    reserved_at: int
    cost_ceiling_cents: int
    concurrency_units: int
    observed_at: int

    def __post_init__(self) -> None:
        if (
            _IDENTIFIER.fullmatch(self.command_id) is None
            or _DIGEST.fullmatch(self.binding_digest) is None
            or _DIGEST.fullmatch(self.ledger_proof_digest) is None
            or type(self.reserved_at) is not int or self.reserved_at < 0
            or type(self.cost_ceiling_cents) is not int
            or not 1 <= self.cost_ceiling_cents <= 1_000_000
            or type(self.concurrency_units) is not int
            or not 1 <= self.concurrency_units <= 16
            or type(self.observed_at) is not int
            or self.observed_at < self.reserved_at
        ):
            raise ValueError("preuve de réservation host-unavailable invalide")

    @property
    def proof_digest(self) -> str:
        return _digest({
            "contract": "foundry-host-unavailable-reservation-proof.v1",
            "command_id": self.command_id,
            "binding_digest": self.binding_digest,
            "ledger_proof_digest": self.ledger_proof_digest,
            "reserved_at": self.reserved_at,
            "cost_ceiling_cents": self.cost_ceiling_cents,
            "concurrency_units": self.concurrency_units,
            "reason": "host_unavailable_campaign_ledger",
        })


@dataclass(frozen=True)
class ReviewWaitingReservationProof:
    """Proof binding settled implementation cost to released host capacity."""

    command_id: str
    binding_digest: str
    ledger_proof_digest: str
    reserved_at: int
    cost_ceiling_cents: int
    concurrency_units: int
    spent_cents: int
    observed_at: int

    def __post_init__(self) -> None:
        if (
            _IDENTIFIER.fullmatch(self.command_id) is None
            or _DIGEST.fullmatch(self.binding_digest) is None
            or _DIGEST.fullmatch(self.ledger_proof_digest) is None
            or type(self.reserved_at) is not int or self.reserved_at < 0
            or type(self.cost_ceiling_cents) is not int
            or not 1 <= self.cost_ceiling_cents <= 1_000_000
            or type(self.concurrency_units) is not int
            or not 1 <= self.concurrency_units <= 16
            or type(self.spent_cents) is not int
            or not 0 <= self.spent_cents <= self.cost_ceiling_cents
            or type(self.observed_at) is not int
            or self.observed_at < self.reserved_at
        ):
            raise ValueError("preuve de réservation review-waiting invalide")

    @property
    def proof_digest(self) -> str:
        return _digest({
            "contract": "foundry-review-waiting-reservation-proof.v1",
            "command_id": self.command_id,
            "binding_digest": self.binding_digest,
            "ledger_proof_digest": self.ledger_proof_digest,
            "reserved_at": self.reserved_at,
            "cost_ceiling_cents": self.cost_ceiling_cents,
            "concurrency_units": self.concurrency_units,
            "spent_cents": self.spent_cents,
            "reason": "review_waiting_campaign_ledger",
        })


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _approved_inputs(command: DevHubCommand) -> dict[str, object]:
    """Return every immutable human-approved command input.

    Claim/heartbeat/event responses may advance projection, version and lease facts,
    but they are never authority to rewrite this material.
    """
    return {
        "contract": "devhub-foundry-command.v1",
        "command_id": command.id,
        "project": command.project,
        "preview_id": command.preview_id,
        "approval_id": command.approval_id,
        "preview_digest": command.preview_digest,
        "snapshot_digest": command.snapshot_digest,
        "policy_digest": command.policy_digest,
        "epic_id": command.epic_id,
        "planning_version_id": command.planning_version_id,
        "limits": {
            "max_cost_cents": command.max_cost_cents,
            "max_concurrency": command.max_concurrency,
        },
        "scheduled_for": command.scheduled_for,
        "created_at": command.created_at,
    }


def _binding_digest(command: DevHubCommand) -> str:
    return _digest(_approved_inputs(command))


def _require_approved_inputs_unchanged(
    before: DevHubCommand, after: DevHubCommand, *, operation: str,
) -> None:
    if _approved_inputs(after) != _approved_inputs(before):
        raise CommandWorkerError(f"{operation} DevHub a modifié les inputs approuvés")


def _idempotency(operation: str, command_id: str, *parts: object) -> str:
    material = _digest([operation, command_id, *parts])
    return f"foundry-{operation}-{material[:32]}"


def revalidate_command(
    command: DevHubCommand, observation: RevalidationObservation, *, now_ms: int,
    allow_zero_budget_reconciliation: bool = False,
) -> EffectiveLimits:
    """Revalidate all authority-bearing facts and only tighten approved bounds."""
    if (type(now_ms) is not int or now_ms < 0
            or type(allow_zero_budget_reconciliation) is not bool):
        raise CommandRevalidationError("horloge Foundry invalide")
    exact = (
        ("command_id", command.id),
        ("project", command.project),
        ("preview_id", command.preview_id),
        ("approval_id", command.approval_id),
        ("epic_id", command.epic_id),
        ("planning_version_id", command.planning_version_id),
        ("preview_digest", command.preview_digest),
        ("snapshot_digest", command.snapshot_digest),
        ("policy_digest", command.policy_digest),
    )
    for field, expected in exact:
        if getattr(observation, field) != expected:
            raise CommandRevalidationError(f"binding {field} modifié")
    if type(observation.epic_version) is not int or observation.epic_version < 1:
        raise CommandRevalidationError("version Epic observée invalide")
    advancement = observation.snapshot_advancement
    if advancement is not None and (
        advancement.command_id != command.id
        or advancement.binding_digest != _binding_digest(command)
        or advancement.snapshot_digest != command.snapshot_digest
        or observation.epic_version != advancement.from_version
    ):
        raise CommandRevalidationError("avancement snapshot parent contradictoire")
    if observation.approval_state != "approved":
        raise CommandRevalidationError("approbation humaine inactive")
    if (type(observation.preview_expires_at) is not int
            or type(observation.approval_expires_at) is not int
            or observation.preview_expires_at <= now_ms
            or observation.approval_expires_at <= now_ms
            or observation.approval_expires_at > observation.preview_expires_at):
        raise CommandRevalidationError("preview ou approbation expiré")
    if (command.scheduled_for is not None and command.scheduled_for > now_ms
            and command.state not in _CONTROL_REQUEST_STATES):
        raise CommandRevalidationError("commande planifiée non échue")
    if (not isinstance(observation.required_gates, frozenset)
            or not REQUIRED_GATES.issubset(observation.required_gates)):
        raise CommandRevalidationError("gates Foundry incomplets")
    approved = observation.approved_minimum_tier
    current = observation.current_minimum_tier
    if approved is not None and approved not in LEVELS:
        raise CommandRevalidationError("floor approuvé inconnu")
    if current is not None and current not in LEVELS:
        raise CommandRevalidationError("floor courant inconnu")
    if approved is not None and (current is None or LEVELS.index(current) < LEVELS.index(approved)):
        raise CommandRevalidationError("floor Foundry abaissé")
    for value, name, maximum in (
        (observation.local_max_cost_cents, "plafond coût", 1_000_000),
        (observation.local_max_concurrency, "plafond concurrence", 16),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise CommandRevalidationError(f"{name} invalide")
    if (type(observation.budget_remaining_cents) is not int
            or not 0 <= observation.budget_remaining_cents <= 1_000_000):
        raise CommandRevalidationError("budget restant invalide")
    if (observation.budget_remaining_cents == 0
            and not allow_zero_budget_reconciliation):
        raise PreEffectCapacityError("budget hôte Foundry épuisé avant effet")
    invocation_ceiling = observation.provider_invocation_ceiling_cents
    if (type(invocation_ceiling) is not int
            or not 1 <= invocation_ceiling <= 1_000_000):
        raise PreEffectCapacityError(
            "plafond par invocation provider absent ou invalide",
        )
    if (type(observation.active_concurrency) is not int
            or observation.active_concurrency < 0):
        raise CommandRevalidationError("concurrence active invalide")
    if (type(observation.observed_at) is not int
            or type(observation.valid_until) is not int
            or not 0 <= observation.observed_at <= now_ms < observation.valid_until):
        raise CommandRevalidationError("horodatage d'observation invalide")
    if type(observation.host_available) is not bool or not observation.host_available:
        raise CommandRevalidationError("hôte Foundry indisponible")
    max_cost = min(
        command.max_cost_cents,
        observation.local_max_cost_cents,
        observation.budget_remaining_cents,
    )
    max_concurrency = min(command.max_concurrency, observation.local_max_concurrency)
    if observation.active_concurrency >= max_concurrency:
        raise CommandRevalidationError("plafond de concurrence atteint")
    return EffectiveLimits(
        max_cost, max_concurrency, min(invocation_ceiling, max_cost),
        current or approved,
        observation.budget_remaining_cents,
        observation.active_concurrency,
        observation.observed_at,
    )


def _tighten_limits(first: EffectiveLimits, latest: EffectiveLimits) -> EffectiveLimits:
    floors = [item for item in (first.minimum_tier, latest.minimum_tier) if item is not None]
    floor = max(floors, key=LEVELS.index) if floors else None
    return EffectiveLimits(
        min(first.max_cost_cents, latest.max_cost_cents),
        min(first.max_concurrency, latest.max_concurrency),
        min(
            first.provider_invocation_ceiling_cents,
            latest.provider_invocation_ceiling_cents,
        ),
        floor,
        min(
            first.observed_budget_remaining_cents,
            latest.observed_budget_remaining_cents,
        ),
        max(first.observed_active_concurrency, latest.observed_active_concurrency),
        min(first.observed_at, latest.observed_at),
    )


def _require_actionable_projection(
    command: DevHubCommand, receipt: EffectReceipt | None,
) -> None:
    if command.projection_stale:
        raise AmbiguousEffectError("projection DevHub obsolète")
    if receipt is None:
        if command.state not in {"requested", *_CONTROL_REQUEST_STATES}:
            raise AmbiguousEffectError("projection DevHub non lançable")
        return
    if (receipt.status not in _RESUMABLE_EFFECT_STATUSES
            or command.state not in {
                "accepted", "running", "unknown", *_CONTROL_REQUEST_STATES,
                "requested", "paused",
            }):
        raise CommandWorkerError("effet Foundry non reprenable dans cet état")


def _require_unlaunched_preamble_projection(command: DevHubCommand) -> None:
    """Accept only lifecycle states explained by a durable preamble marker."""
    if command.projection_stale:
        if command.state == "unknown":
            return
        raise AmbiguousEffectError("projection préambule DevHub contradictoire")
    if command.state not in {
        "requested", "accepted", "running", *_CONTROL_REQUEST_STATES,
    }:
        raise AmbiguousEffectError("projection préambule DevHub non lançable")


class ReceiptStore:
    """Crash-durable, bounded local journal keyed by DevHub command identity."""

    _PHASES = {
        "claimed", "launch-intent", _PRE_EFFECT_PREAMBLE_PHASE,
        _PRE_EFFECT_PAUSE_PHASE,
        *_HUMAN_CONTROL_PHASES, "effect-proven", "unresolved", "terminal",
    }
    _TRANSITIONS = {
        "claimed": {
            "claimed", "launch-intent", _PRE_EFFECT_PREAMBLE_PHASE,
            _PRE_EFFECT_PAUSE_PHASE,
            *_HUMAN_CONTROL_PHASES, "effect-proven", "unresolved", "terminal",
        },
        _PRE_EFFECT_PREAMBLE_PHASE: {
            _PRE_EFFECT_PREAMBLE_PHASE, "launch-intent", _PRE_EFFECT_PAUSE_PHASE,
            *_HUMAN_CONTROL_PHASES, "effect-proven", "unresolved", "terminal",
        },
        "launch-intent": {
            "launch-intent", _PRE_EFFECT_PAUSE_PHASE,
            *_HUMAN_CONTROL_PHASES, "effect-proven", "unresolved", "terminal",
        },
        _PRE_EFFECT_PAUSE_PHASE: {
            _PRE_EFFECT_PAUSE_PHASE, "launch-intent",
            *_HUMAN_CONTROL_PHASES, "effect-proven", "unresolved", "terminal",
        },
        _HUMAN_PAUSE_PHASE: {_HUMAN_PAUSE_PHASE, _HUMAN_CANCEL_PHASE},
        _HUMAN_CANCEL_PHASE: {_HUMAN_CANCEL_PHASE},
        "unresolved": {"unresolved", "effect-proven", "terminal"},
        "effect-proven": {"effect-proven", "terminal"},
        "terminal": {"terminal"},
    }

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS command_receipts (
                     command_id TEXT PRIMARY KEY,
                     project TEXT NOT NULL,
                     binding_digest TEXT NOT NULL,
                     phase TEXT NOT NULL,
                     lease_id TEXT NOT NULL,
                     effect_json TEXT,
                     updated_at INTEGER NOT NULL
                   )""",
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS pull_cursors (
                     project TEXT PRIMARY KEY,
                     cursor TEXT NOT NULL
                   )""",
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _effect_json(effect: EffectReceipt | None) -> str | None:
        if effect is None:
            return None
        value = json.dumps({
            "contract": RECEIPT_CONTRACT,
            "effect_id": effect.effect_id,
            "status": effect.status,
            "proof_digest": effect.proof_digest,
            "cost_cents": effect.cost_cents,
            "duration_ms": effect.duration_ms,
            "attempt_id": effect.attempt_id,
            "attempt_started_at": effect.attempt_started_at,
        }, sort_keys=True, separators=(",", ":"))
        if len(value.encode("utf-8")) > MAX_RECEIPT_BYTES:
            raise ValueError("reçu Foundry hors borne")
        return value

    @staticmethod
    def _parse_effect(value: str | None) -> EffectReceipt | None:
        if value is None:
            return None
        try:
            raw = json.loads(value)
            if not isinstance(raw, dict) or set(raw) not in ({
                "contract", "effect_id", "status", "proof_digest",
                "cost_cents", "duration_ms",
            }, {
                "contract", "effect_id", "status", "proof_digest",
                "cost_cents", "duration_ms", "attempt_id", "attempt_started_at",
            }) or raw["contract"] != RECEIPT_CONTRACT:
                raise ValueError
            return EffectReceipt(
                raw["effect_id"], raw["status"], raw["proof_digest"],
                raw["cost_cents"], raw["duration_ms"],
                raw.get("attempt_id"), raw.get("attempt_started_at"),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            raise CommandWorkerError("journal de reçus Foundry corrompu") from None

    @staticmethod
    def _merge_terminal_effect(
        previous: EffectReceipt, current: EffectReceipt,
    ) -> EffectReceipt:
        if (previous.effect_id != current.effect_id
                or previous.status != current.status
                or previous.proof_digest != current.proof_digest):
            raise CommandWorkerError("reçu terminal durable modifié")
        telemetry = []
        for old, new in (
            (previous.cost_cents, current.cost_cents),
            (previous.duration_ms, current.duration_ms),
        ):
            if old is not None and new is not None and old != new:
                raise CommandWorkerError("reçu terminal durable modifié")
            telemetry.append(old if old is not None else new)
        if (previous.attempt_id is not None and current.attempt_id is not None
                and (previous.attempt_id != current.attempt_id
                     or previous.attempt_started_at != current.attempt_started_at)):
            raise CommandWorkerError("reçu terminal durable modifié")
        attempt_id = previous.attempt_id if previous.attempt_id is not None else current.attempt_id
        attempt_started_at = (
            previous.attempt_started_at
            if previous.attempt_started_at is not None else current.attempt_started_at
        )
        return EffectReceipt(
            previous.effect_id, previous.status, previous.proof_digest,
            telemetry[0], telemetry[1],
            attempt_id, attempt_started_at,
        )

    def get(self, command_id: str) -> DurableReceipt | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT command_id, project, binding_digest, phase, lease_id, effect_json, updated_at "
                "FROM command_receipts WHERE command_id = ?", (command_id,),
            ).fetchone()
        if row is None:
            return None
        effect = self._parse_effect(row[5])
        if row[3] not in self._PHASES:
            raise CommandWorkerError("phase de commande durable inconnue")
        if row[3] == _PRE_EFFECT_PREAMBLE_PHASE and effect is not None:
            raise CommandWorkerError("préambule pré-effet durable contradictoire")
        if row[3] == _PRE_EFFECT_PAUSE_PHASE and (
            effect is None or effect.status != "paused"
            or effect.cost_cents != 0 or effect.duration_ms != 0
            or effect.attempt_id is not None or effect.attempt_started_at is not None
        ):
            raise CommandWorkerError("pause pré-effet durable contradictoire")
        human_status = _HUMAN_CONTROL_PHASES.get(row[3])
        if human_status is not None and (
            effect is None or effect.status != human_status
            or effect.cost_cents != 0 or effect.duration_ms != 0
            or effect.attempt_id is not None or effect.attempt_started_at is not None
        ):
            raise CommandWorkerError("contrôle humain pré-effet durable contradictoire")
        return DurableReceipt(
            command_id=row[0], project=row[1], binding_digest=row[2], phase=row[3],
            lease_id=row[4], effect=effect, updated_at=row[6],
        )

    def record(
        self, command: DevHubCommand, binding_digest: str, phase: str, *,
        lease_id: str, effect: EffectReceipt | None, now_ms: int,
    ) -> DurableReceipt:
        if (phase not in self._PHASES or _DIGEST.fullmatch(binding_digest) is None
                or _IDENTIFIER.fullmatch(lease_id) is None
                or type(now_ms) is not int or now_ms < 0):
            raise ValueError("état reçu Foundry invalide")
        if ((phase in _TERMINAL_RECEIPT_PHASES) != (
                effect is not None and effect.status in _TERMINAL_EFFECT_STATUSES
        )):
            raise ValueError("phase et reçu terminal Foundry contradictoires")
        if phase == _PRE_EFFECT_PREAMBLE_PHASE and effect is not None:
            raise ValueError("préambule pré-effet Foundry invalide")
        if phase == _PRE_EFFECT_PAUSE_PHASE and (
            effect is None or effect.status != "paused"
            or effect.cost_cents != 0 or effect.duration_ms != 0
            or effect.attempt_id is not None or effect.attempt_started_at is not None
        ):
            raise ValueError("pause pré-effet Foundry invalide")
        human_status = _HUMAN_CONTROL_PHASES.get(phase)
        if human_status is not None and (
            effect is None or effect.status != human_status
            or effect.cost_cents != 0 or effect.duration_ms != 0
            or effect.attempt_id is not None or effect.attempt_started_at is not None
        ):
            raise ValueError("contrôle humain pré-effet Foundry invalide")
        if phase == "effect-proven" and effect is None:
            raise ValueError("preuve d'effet Foundry absente")
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT project, binding_digest, phase, effect_json "
                "FROM command_receipts WHERE command_id = ?",
                (command.id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != command.project or existing[1] != binding_digest:
                    raise CommandWorkerError("identité de commande durable contradictoire")
                previous_phase = existing[2]
                previous = self._parse_effect(existing[3])
                if (previous_phase in _TERMINAL_RECEIPT_PHASES
                        or (previous is not None
                            and previous.status in _TERMINAL_EFFECT_STATUSES)):
                    if (phase not in _TERMINAL_RECEIPT_PHASES
                            or previous is None or effect is None):
                        raise CommandWorkerError("reçu terminal durable modifié")
                    effect = self._merge_terminal_effect(previous, effect)
                if (previous_phase not in self._PHASES
                        or phase not in self._TRANSITIONS[previous_phase]):
                    raise CommandWorkerError("phase de commande durable rétrogradée")
                if (previous is not None and effect is not None
                        and previous.effect_id != effect.effect_id):
                    raise CommandWorkerError("identité d'effet durable contradictoire")
                if previous is not None and effect is None:
                    raise CommandWorkerError("preuve d'effet durable effacée")
            effect_json = self._effect_json(effect)
            connection.execute(
                """INSERT INTO command_receipts
                     (command_id, project, binding_digest, phase, lease_id, effect_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(command_id) DO UPDATE SET
                     phase=excluded.phase, lease_id=excluded.lease_id,
                     effect_json=COALESCE(excluded.effect_json, command_receipts.effect_json),
                     updated_at=excluded.updated_at""",
                (command.id, command.project, binding_digest, phase, lease_id, effect_json, now_ms),
            )
        receipt = self.get(command.id)
        assert receipt is not None
        return receipt

    def cursor(self, project: str) -> str:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM pull_cursors WHERE project = ?", (project,),
            ).fetchone()
        return row[0] if row is not None else "0"

    def set_cursor(self, project: str, cursor: str) -> None:
        if not re.fullmatch(r"^(?:0|[1-9][0-9]{0,18})$", cursor):
            raise ValueError("curseur durable invalide")
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO pull_cursors(project, cursor) VALUES (?, ?) "
                "ON CONFLICT(project) DO UPDATE SET cursor=excluded.cursor",
                (project, cursor),
            )


class HostReservationStore:
    """Atomic host-wide budget/concurrency ledger shared by worker processes.

    Active allocations are never aged out merely because their owner disappeared:
    absence after a crash cannot prove that an external effect stopped.  A new owner
    may take over only after the prior owner TTL elapsed *and* a provider receipt
    proves the same non-terminal effect.  Terminal proof settles actual cost (or the
    full reserved ceiling when telemetry is absent) and releases concurrency.
    """

    _STATES = {"active", "settled"}

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        for attempt in range(100):
            try:
                with self._connect() as connection:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute(
                        """CREATE TABLE IF NOT EXISTS host_reservations (
                             command_id TEXT PRIMARY KEY,
                             contract TEXT NOT NULL,
                             binding_digest TEXT NOT NULL,
                             state TEXT NOT NULL,
                             owner_id TEXT,
                             cost_ceiling_cents INTEGER NOT NULL,
                             concurrency_units INTEGER NOT NULL,
                             settled_cost_cents INTEGER,
                             reserved_at INTEGER NOT NULL,
                             owner_expires_at INTEGER,
                             settled_at INTEGER,
                             reconciliation_proof_digest TEXT,
                             reconciliation_reason TEXT,
                             reconciled_at INTEGER
                           )""",
                    )
                    columns = {
                        row[1] for row in connection.execute(
                            "PRAGMA table_info(host_reservations)",
                        )
                    }
                    if "reconciliation_proof_digest" not in columns:
                        connection.execute(
                            "ALTER TABLE host_reservations ADD COLUMN "
                            "reconciliation_proof_digest TEXT",
                        )
                    if "reconciled_at" not in columns:
                        connection.execute(
                            "ALTER TABLE host_reservations ADD COLUMN reconciled_at INTEGER",
                        )
                    if "reconciliation_reason" not in columns:
                        connection.execute(
                            "ALTER TABLE host_reservations ADD COLUMN reconciliation_reason TEXT",
                        )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS host_reservations_state "
                        "ON host_reservations(state)",
                    )
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 99:
                    raise CommandWorkerError(
                        "ledger de réservation Foundry indisponible",
                    ) from None
                time.sleep(0.05)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _from_row(row: tuple[object, ...] | None) -> HostReservation | None:
        if row is None:
            return None
        try:
            reservation = HostReservation(
                command_id=row[0], binding_digest=row[2], state=row[3], owner_id=row[4],
                cost_ceiling_cents=row[5], concurrency_units=row[6],
                settled_cost_cents=row[7], reserved_at=row[8],
                owner_expires_at=row[9], settled_at=row[10],
                reconciliation_proof_digest=row[11], reconciliation_reason=row[12],
                reconciled_at=row[13],
            )
            if (row[1] != RESERVATION_CONTRACT
                    or _IDENTIFIER.fullmatch(reservation.command_id) is None
                    or _DIGEST.fullmatch(reservation.binding_digest) is None
                    or reservation.state not in HostReservationStore._STATES
                    or (reservation.owner_id is not None
                        and _IDENTIFIER.fullmatch(reservation.owner_id) is None)
                    or type(reservation.cost_ceiling_cents) is not int
                    or not 1 <= reservation.cost_ceiling_cents <= 1_000_000
                    or type(reservation.concurrency_units) is not int
                    or not 1 <= reservation.concurrency_units <= 16
                    or type(reservation.reserved_at) is not int
                    or reservation.reserved_at < 0):
                raise ValueError
            nullable_integers = (
                reservation.settled_cost_cents,
                reservation.owner_expires_at,
                reservation.settled_at,
                reservation.reconciled_at,
            )
            if any(value is not None and (type(value) is not int or value < 0)
                   for value in nullable_integers):
                raise ValueError
            if (reservation.owner_expires_at is not None
                    and reservation.owner_expires_at <= reservation.reserved_at):
                raise ValueError
            if (reservation.settled_at is not None
                    and reservation.settled_at < reservation.reserved_at):
                raise ValueError
            if (reservation.reconciliation_proof_digest is not None
                    and _DIGEST.fullmatch(reservation.reconciliation_proof_digest) is None):
                raise ValueError
            if (reservation.reconciliation_reason is not None
                    and reservation.reconciliation_reason not in {
                        "unlaunched_campaign_ledger",
                        "host_unavailable_campaign_ledger",
                        "review_waiting_campaign_ledger",
                    }):
                raise ValueError
            if (reservation.reconciled_at is not None
                    and reservation.reconciled_at < reservation.reserved_at):
                raise ValueError
            if reservation.state == "active":
                if (reservation.settled_cost_cents is not None
                        or reservation.settled_at is not None
                        or reservation.reconciliation_proof_digest is not None
                        or reservation.reconciliation_reason is not None
                        or reservation.reconciled_at is not None):
                    raise ValueError
                if ((reservation.owner_id is None) !=
                        (reservation.owner_expires_at is None)):
                    raise ValueError
            elif (reservation.owner_id is not None
                  or reservation.owner_expires_at is not None
                  or reservation.settled_cost_cents is None
                  or reservation.settled_at is None
                  or reservation.settled_cost_cents > reservation.cost_ceiling_cents):
                raise ValueError
            if ((reservation.reconciliation_proof_digest is None)
                    != (reservation.reconciliation_reason is None)
                    or (reservation.reconciliation_proof_digest is None)
                    != (reservation.reconciled_at is None)):
                raise ValueError
            return reservation
        except (IndexError, TypeError, ValueError):
            raise CommandWorkerError("ledger de réservation Foundry corrompu") from None

    @staticmethod
    def _validate_identity(command_id: str, binding_digest: str) -> None:
        if (_IDENTIFIER.fullmatch(command_id) is None
                or _DIGEST.fullmatch(binding_digest) is None):
            raise ValueError("identité de réservation Foundry invalide")

    @staticmethod
    def _validate_clock(now_ms: int) -> None:
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("horloge de réservation Foundry invalide")

    @staticmethod
    def _row(connection: sqlite3.Connection, command_id: str) -> tuple[object, ...] | None:
        return connection.execute(
            "SELECT command_id, contract, binding_digest, state, owner_id, "
            "cost_ceiling_cents, concurrency_units, settled_cost_cents, reserved_at, "
            "owner_expires_at, settled_at, reconciliation_proof_digest, "
            "reconciliation_reason, reconciled_at "
            "FROM host_reservations WHERE command_id = ?",
            (command_id,),
        ).fetchone()

    @classmethod
    def _validated_rows(
        cls, connection: sqlite3.Connection,
    ) -> tuple[HostReservation, ...]:
        """Read and validate the complete ledger before deriving any capacity."""
        try:
            rows = connection.execute(
                "SELECT command_id, contract, binding_digest, state, owner_id, "
                "cost_ceiling_cents, concurrency_units, settled_cost_cents, reserved_at, "
                "owner_expires_at, settled_at, reconciliation_proof_digest, "
                "reconciliation_reason, reconciled_at "
                "FROM host_reservations",
            ).fetchall()
        except sqlite3.DatabaseError:
            raise CommandWorkerError("ledger de réservation Foundry corrompu") from None
        reservations: list[HostReservation] = []
        for row in rows:
            reservation = cls._from_row(row)
            if reservation is None:  # defensive: fetched rows are never None
                raise CommandWorkerError("ledger de réservation Foundry corrompu")
            reservations.append(reservation)
        return tuple(reservations)

    @classmethod
    def _require_capacity(
        cls, connection: sqlite3.Connection, command_id: str,
        profile: ExecutionProfile, limits: EffectiveLimits, *, now_ms: int,
    ) -> None:
        if (not isinstance(profile, ExecutionProfile)
                or not isinstance(limits, EffectiveLimits)):
            raise ValueError("demande de réservation Foundry invalide")
        if (type(limits.max_cost_cents) is not int
                or not 1 <= limits.max_cost_cents <= 1_000_000
                or type(limits.max_concurrency) is not int
                or not 1 <= limits.max_concurrency <= 16
                or profile.cost_ceiling_cents > limits.max_cost_cents
                or profile.concurrency_units > limits.max_concurrency):
            raise CommandWorkerError("ressources Foundry refusées avant réservation")
        if (type(limits.observed_budget_remaining_cents) is not int
                or not 1 <= limits.observed_budget_remaining_cents <= 1_000_000
                or type(limits.observed_active_concurrency) is not int
                or limits.observed_active_concurrency < 0
                or type(limits.observed_at) is not int
                or not 0 <= limits.observed_at <= now_ms):
            raise CommandWorkerError("signaux de capacité Foundry invalides")

        active_cost = 0
        active_units = 0
        settled_cost = 0
        for reservation in cls._validated_rows(connection):
            if reservation.command_id == command_id:
                continue
            if reservation.state == "active":
                active_cost += reservation.cost_ceiling_cents
                active_units += reservation.concurrency_units
            elif reservation.settled_at is not None and reservation.settled_at >= limits.observed_at:
                # A settled row is validated to have a non-null integer cost.
                assert reservation.settled_cost_cents is not None
                settled_cost += reservation.settled_cost_cents

        if (limits.observed_active_concurrency + active_units
                + profile.concurrency_units > limits.max_concurrency):
            raise PreEffectCapacityError("capacité hôte Foundry refusée avant effet")
        if (active_cost + settled_cost + profile.cost_ceiling_cents
                > limits.observed_budget_remaining_cents):
            raise PreEffectCapacityError("budget hôte Foundry refusé avant effet")

    def get(self, command_id: str) -> HostReservation | None:
        if _IDENTIFIER.fullmatch(command_id) is None:
            raise ValueError("identité de réservation Foundry invalide")
        with self._lock, self._connect() as connection:
            return self._from_row(self._row(connection, command_id))

    def reserve(
        self, command_id: str, binding_digest: str, profile: ExecutionProfile,
        limits: EffectiveLimits, *, owner_id: str, now_ms: int,
        prior_effect: EffectReceipt | None = None,
    ) -> HostReservation:
        """Compare all host signals and commit one allocation before provider effect."""
        self._validate_identity(command_id, binding_digest)
        self._validate_clock(now_ms)
        if (_IDENTIFIER.fullmatch(owner_id) is None
                or not isinstance(profile, ExecutionProfile)
                or not isinstance(limits, EffectiveLimits)):
            raise ValueError("demande de réservation Foundry invalide")
        if prior_effect is not None and (
            not isinstance(prior_effect, EffectReceipt)
            or prior_effect.effect_id != command_id
            or prior_effect.status in _TERMINAL_EFFECT_STATUSES
        ):
            raise CommandWorkerError("preuve de reprise Foundry invalide")

        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._from_row(self._row(connection, command_id))
                if existing is not None:
                    if (existing.binding_digest != binding_digest
                            or existing.cost_ceiling_cents != profile.cost_ceiling_cents
                            or existing.concurrency_units < profile.concurrency_units):
                        raise CommandWorkerError("réservation d'effet Foundry contradictoire")
                    if (existing.concurrency_units > profile.concurrency_units
                            and prior_effect is None):
                        raise CommandWorkerError(
                            "resserrement de réservation sans preuve de reprise"
                        )
                    if existing.state == "settled":
                        raise CommandWorkerError("réservation terminale Foundry non relançable")
                    if existing.owner_id not in {None, owner_id}:
                        if (prior_effect is None or existing.owner_expires_at is None
                                or existing.owner_expires_at > now_ms):
                            raise CommandWorkerError("réservation d'effet Foundry déjà active")
                    elif existing.owner_id is None and prior_effect is None:
                        raise CommandWorkerError("réservation d'effet Foundry non résolue")

                self._require_capacity(
                    connection, command_id, profile, limits, now_ms=now_ms,
                )

                owner_expires_at = now_ms + RESERVATION_OWNER_TTL_MS
                if existing is None:
                    connection.execute(
                        """INSERT INTO host_reservations
                             (command_id, contract, binding_digest, state, owner_id,
                              cost_ceiling_cents, concurrency_units, settled_cost_cents,
                              reserved_at, owner_expires_at, settled_at)
                           VALUES (?, ?, ?, 'active', ?, ?, ?, NULL, ?, ?, NULL)""",
                        (
                            command_id, RESERVATION_CONTRACT, binding_digest, owner_id,
                            profile.cost_ceiling_cents, profile.concurrency_units,
                            now_ms, owner_expires_at,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE host_reservations SET owner_id = ?, owner_expires_at = ?, "
                        "concurrency_units = ? "
                        "WHERE command_id = ?",
                        (owner_id, owner_expires_at, profile.concurrency_units, command_id),
                    )
                reserved = self._from_row(self._row(connection, command_id))
                assert reserved is not None
                connection.commit()
                return reserved
            except Exception:
                connection.rollback()
                raise

    def reconcile_capacity(
        self, command_id: str, binding_digest: str, profile: ExecutionProfile,
        limits: EffectiveLimits, *, owner_id: str, now_ms: int,
    ) -> None:
        """Recheck the latest external baseline against the complete local ledger."""
        self._validate_identity(command_id, binding_digest)
        self._validate_clock(now_ms)
        if (_IDENTIFIER.fullmatch(owner_id) is None
                or not isinstance(profile, ExecutionProfile)
                or not isinstance(limits, EffectiveLimits)):
            raise ValueError("réconciliation de réservation Foundry invalide")
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._from_row(self._row(connection, command_id))
                if (current is None or current.binding_digest != binding_digest
                        or current.state != "active" or current.owner_id != owner_id
                        or current.cost_ceiling_cents != profile.cost_ceiling_cents
                        or current.concurrency_units != profile.concurrency_units):
                    raise CommandWorkerError("réservation Foundry modifiée avant effet")
                self._require_capacity(
                    connection, command_id, profile, limits, now_ms=now_ms,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def refresh_owner(
        self, command_id: str, binding_digest: str, owner_id: str, *, now_ms: int,
    ) -> bool:
        self._validate_identity(command_id, binding_digest)
        self._validate_clock(now_ms)
        if _IDENTIFIER.fullmatch(owner_id) is None:
            raise ValueError("propriétaire de réservation Foundry invalide")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE host_reservations SET owner_expires_at = ? "
                "WHERE command_id = ? AND binding_digest = ? AND state = 'active' "
                "AND owner_id = ?",
                (
                    now_ms + RESERVATION_OWNER_TTL_MS,
                    command_id, binding_digest, owner_id,
                ),
            ).rowcount
            connection.commit()
            return updated == 1

    def yield_owner(
        self, command_id: str, binding_digest: str, owner_id: str,
    ) -> None:
        """End one local invocation while retaining its unresolved allocation."""
        self._validate_identity(command_id, binding_digest)
        if _IDENTIFIER.fullmatch(owner_id) is None:
            raise ValueError("propriétaire de réservation Foundry invalide")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._from_row(self._row(connection, command_id))
            if current is None or current.binding_digest != binding_digest:
                connection.rollback()
                raise CommandWorkerError("réservation Foundry absente")
            if current.state == "settled" or current.owner_id is None:
                connection.commit()
                return
            if current.owner_id != owner_id:
                connection.rollback()
                raise CommandWorkerError("propriétaire de réservation Foundry modifié")
            connection.execute(
                "UPDATE host_reservations SET owner_id = NULL, owner_expires_at = NULL "
                "WHERE command_id = ?",
                (command_id,),
            )
            connection.commit()

    def settle(
        self, command_id: str, binding_digest: str, effect: EffectReceipt, *,
        now_ms: int, owner_id: str | None = None,
    ) -> HostReservation | None:
        """Release concurrency and charge terminal cost without losing ambiguity."""
        self._validate_identity(command_id, binding_digest)
        self._validate_clock(now_ms)
        if (not isinstance(effect, EffectReceipt) or effect.effect_id != command_id
                or effect.status not in _TERMINAL_EFFECT_STATUSES
                or (owner_id is not None and _IDENTIFIER.fullmatch(owner_id) is None)):
            raise CommandWorkerError("settlement de réservation Foundry invalide")
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._from_row(self._row(connection, command_id))
                if current is None:
                    connection.commit()
                    return None
                if current.binding_digest != binding_digest:
                    raise CommandWorkerError("binding de réservation Foundry modifié")
                charge = (
                    current.settled_cost_cents
                    if current.state == "settled" and effect.cost_cents is None
                    else current.cost_ceiling_cents
                    if effect.cost_cents is None else effect.cost_cents
                )
                assert charge is not None
                if charge > current.cost_ceiling_cents:
                    raise CommandWorkerError("coût de settlement Foundry hors réservation")
                if current.state == "settled":
                    charge = max(charge, current.settled_cost_cents or 0)
                    settled_at = current.settled_at
                else:
                    if owner_id is not None and current.owner_id != owner_id:
                        raise CommandWorkerError("propriétaire de réservation Foundry modifié")
                    settled_at = now_ms
                connection.execute(
                    "UPDATE host_reservations SET state = 'settled', owner_id = NULL, "
                    "owner_expires_at = NULL, settled_cost_cents = ?, settled_at = ?, "
                    "reconciliation_proof_digest = NULL, reconciliation_reason = NULL, "
                    "reconciled_at = NULL "
                    "WHERE command_id = ?",
                    (charge, settled_at, command_id),
                )
                settled = self._from_row(self._row(connection, command_id))
                assert settled is not None
                connection.commit()
                return settled
            except Exception:
                connection.rollback()
                raise

    def reconcile_unlaunched(
        self, proof: UnlaunchedReservationProof, *, now_ms: int,
    ) -> HostReservation:
        """Release one orphan only after a ledger proves no implementation began.

        This is deliberately not a timeout cleanup. A live owner, a different binding,
        or any normal terminal settlement remains non-reconcilable. Replaying the exact
        same proof is safe and returns the existing zero-cost settlement.
        """
        if not isinstance(proof, UnlaunchedReservationProof):
            raise ValueError("preuve de réconciliation Foundry invalide")
        self._validate_clock(now_ms)
        if proof.observed_at > now_ms:
            raise CommandWorkerError("preuve de réconciliation Foundry future")
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._from_row(self._row(connection, proof.command_id))
                if current is None or current.binding_digest != proof.binding_digest:
                    raise CommandWorkerError("réservation Foundry absente ou contradictoire")
                if current.state == "settled":
                    if (current.settled_cost_cents == 0
                            and current.reconciliation_proof_digest == proof.proof_digest
                            and current.reconciled_at is not None):
                        connection.commit()
                        return current
                    raise CommandWorkerError("réservation terminale Foundry non réconciliable")
                if current.owner_id is not None or current.owner_expires_at is not None:
                    raise CommandWorkerError("réservation Foundry encore possédée")
                connection.execute(
                    "UPDATE host_reservations SET state = 'settled', owner_id = NULL, "
                    "owner_expires_at = NULL, settled_cost_cents = 0, settled_at = ?, "
                    "reconciliation_proof_digest = ?, "
                    "reconciliation_reason = 'unlaunched_campaign_ledger', reconciled_at = ? "
                    "WHERE command_id = ?",
                    (now_ms, proof.proof_digest, now_ms, proof.command_id),
                )
                reconciled = self._from_row(self._row(connection, proof.command_id))
                assert reconciled is not None
                connection.commit()
                return reconciled
            except Exception:
                connection.rollback()
                raise

    def reconcile_host_unavailable(
        self, proof: HostUnavailableReservationProof, *, now_ms: int,
    ) -> HostReservation:
        """Release one exact zero-cost host-unavailable campaign reservation.

        Unlike unlaunched reconciliation, this path accepts implementation intents.
        Their complete cross-ledger proof is bound to the original reservation time,
        cost ceiling and concurrency units before the row is settled atomically.
        """
        if not isinstance(proof, HostUnavailableReservationProof):
            raise ValueError("preuve host-unavailable Foundry invalide")
        self._validate_clock(now_ms)
        if proof.observed_at > now_ms:
            raise CommandWorkerError("preuve host-unavailable Foundry future")
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._from_row(self._row(connection, proof.command_id))
                if current is None or current.binding_digest != proof.binding_digest:
                    raise CommandWorkerError("réservation host-unavailable absente ou contradictoire")
                if current.state == "settled":
                    if (
                        current.settled_cost_cents == 0
                        and current.reconciliation_reason
                        == "host_unavailable_campaign_ledger"
                        and current.reconciliation_proof_digest == proof.proof_digest
                        and current.reconciled_at is not None
                    ):
                        connection.commit()
                        return current
                    raise CommandWorkerError(
                        "réservation terminale host-unavailable non réconciliable"
                    )
                if current.owner_id is not None or current.owner_expires_at is not None:
                    raise CommandWorkerError("réservation host-unavailable encore possédée")
                if (
                    current.reserved_at != proof.reserved_at
                    or current.cost_ceiling_cents != proof.cost_ceiling_cents
                    or current.concurrency_units != proof.concurrency_units
                ):
                    raise CommandWorkerError("capacité host-unavailable contradictoire")
                connection.execute(
                    "UPDATE host_reservations SET state = 'settled', owner_id = NULL, "
                    "owner_expires_at = NULL, settled_cost_cents = 0, settled_at = ?, "
                    "reconciliation_proof_digest = ?, "
                    "reconciliation_reason = 'host_unavailable_campaign_ledger', "
                    "reconciled_at = ? WHERE command_id = ?",
                    (now_ms, proof.proof_digest, now_ms, proof.command_id),
                )
                reconciled = self._from_row(self._row(connection, proof.command_id))
                assert reconciled is not None
                connection.commit()
                return reconciled
            except Exception:
                connection.rollback()
                raise


    def reconcile_review_waiting(
        self, proof: ReviewWaitingReservationProof, *, now_ms: int,
    ) -> HostReservation:
        """Settle one suspended review-waiting campaign without authorizing an effect.

        The only mutation is the host row settlement.  A later provider invocation
        must therefore acquire a new reservation through ``reserve`` and a fresh
        capacity observation; this primitive carries no owner or launch authority.
        """
        if not isinstance(proof, ReviewWaitingReservationProof):
            raise ValueError("preuve review-waiting Foundry invalide")
        self._validate_clock(now_ms)
        if proof.observed_at > now_ms:
            raise CommandWorkerError("preuve review-waiting Foundry future")
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._from_row(self._row(connection, proof.command_id))
                if current is None or current.binding_digest != proof.binding_digest:
                    raise CommandWorkerError("réservation review-waiting absente ou contradictoire")
                if current.state == "settled":
                    if (
                        current.settled_cost_cents == proof.spent_cents
                        and current.reconciliation_reason == "review_waiting_campaign_ledger"
                        and current.reconciliation_proof_digest == proof.proof_digest
                        and current.reconciled_at is not None
                    ):
                        connection.commit()
                        return current
                    raise CommandWorkerError(
                        "réservation terminale review-waiting non réconciliable"
                    )
                if current.owner_id is not None or current.owner_expires_at is not None:
                    raise CommandWorkerError("réservation review-waiting encore possédée")
                if (
                    current.reserved_at != proof.reserved_at
                    or current.cost_ceiling_cents != proof.cost_ceiling_cents
                    or current.concurrency_units != proof.concurrency_units
                ):
                    raise CommandWorkerError("capacité review-waiting contradictoire")
                connection.execute(
                    "UPDATE host_reservations SET state = 'settled', owner_id = NULL, "
                    "owner_expires_at = NULL, settled_cost_cents = ?, settled_at = ?, "
                    "reconciliation_proof_digest = ?, "
                    "reconciliation_reason = 'review_waiting_campaign_ledger', "
                    "reconciled_at = ? WHERE command_id = ?",
                    (proof.spent_cents, now_ms, proof.proof_digest,
                     now_ms, proof.command_id),
                )
                reconciled = self._from_row(self._row(connection, proof.command_id))
                assert reconciled is not None
                connection.commit()
                return reconciled
            except Exception:
                connection.rollback()
                raise


class _LeaseSession:
    def __init__(
        self, client: DevHubCommandClient, command: DevHubCommand, *,
        lease_seconds: int, now_ms: Callable[[], int],
    ):
        if command.lease is None:
            raise CommandWorkerError("lease DevHub absent après claim")
        self.client = client
        self.command = command
        self.lease_id = command.lease.id
        self.lease_seconds = lease_seconds
        self.now_ms = now_ms
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: Exception | None = None
        self._heartbeat_count = 0

    def _accept_heartbeat(
        self, previous: DevHubCommand, updated: DevHubCommand,
    ) -> DevHubCommand:
        _require_approved_inputs_unchanged(previous, updated, operation="heartbeat")
        before = previous.lease
        after = updated.lease
        now = self.now_ms()
        if (before is None or after is None or after.id != self.lease_id
                or after.worker_id != before.worker_id
                or updated.version <= previous.version
                or after.heartbeat_at <= before.heartbeat_at
                or after.expires_at <= now
                or after.expires_at <= before.expires_at
                or after.heartbeat_at > after.expires_at):
            raise AmbiguousEffectError("heartbeat DevHub inchangé ou expiré")
        return updated

    def heartbeat(self) -> DevHubCommand:
        with self._lock:
            if self.error is not None:
                raise CommandWorkerError("heartbeat DevHub perdu") from self.error
            current = self.command
            self._heartbeat_count += 1
            key = _idempotency(
                "heartbeat", current.id, self.lease_id, current.version,
                self._heartbeat_count,
            )
            try:
                updated = self.client.heartbeat(
                    current, lease_id=self.lease_id,
                    extend_seconds=self.lease_seconds, idempotency_key=key,
                )
            except DevHubCommandTransportError:
                try:
                    updated = self.client.get_command(current.id)
                except DevHubCommandError:
                    raise AmbiguousEffectError("heartbeat DevHub non résolu") from None
            self.command = self._accept_heartbeat(current, updated)
            return self.command

    def _loop(self) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                self.heartbeat()
            except Exception as exc:  # recorded and surfaced before any terminal fact
                self.error = exc
                self._stop.set()

    def start(self) -> None:
        self.heartbeat()
        self._thread = threading.Thread(
            target=self._loop, name=f"foundry-lease-{self.command.id}", daemon=True,
        )
        self._thread.start()

    def stop(self) -> DevHubCommand:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self.error is not None:
            raise CommandWorkerError("heartbeat DevHub perdu") from self.error
        return self.command


class BoundedExecutionPath:
    """The only worker-owned path allowed to invoke a provider effect.

    The provider declares its worst-case cost, tier and concurrency before this
    class calls ``launch``/``resume``.  The guard validates and reserves those
    resources first, then hands the provider an immutable authorization rather
    than raw, advisory limits.
    """

    def __init__(
        self, provider: CommandEffectProvider, reservations: HostReservationStore, *,
        now_ms: Callable[[], int],
    ):
        self.provider = provider
        self.reservations = reservations
        self.now_ms = now_ms

    def _authorize(
        self, command: DevHubCommand, binding_digest: str,
        receipt: EffectReceipt | None, limits: EffectiveLimits, *, owner_id: str,
        prior_reservation_effect: EffectReceipt | None,
    ) -> ExecutionAuthorization:
        try:
            profile = self.provider.execution_profile(command, receipt)
        except (TypeError, ValueError) as exc:
            raise CommandWorkerError("profil d'exécution provider invalide") from exc
        if not isinstance(profile, ExecutionProfile):
            raise CommandWorkerError("profil d'exécution provider invalide")
        if profile.cost_ceiling_cents > limits.max_cost_cents:
            raise CommandWorkerError("plafond de coût refusé avant effet")
        if profile.concurrency_units > limits.max_concurrency:
            raise CommandWorkerError("concurrence refusée avant effet")
        declared_provider_invocation_ceiling = getattr(
            profile, "provider_invocation_ceiling_cents",
            limits.provider_invocation_ceiling_cents,
        )
        if (type(declared_provider_invocation_ceiling) is not int
                or not 1 <= declared_provider_invocation_ceiling
                <= limits.provider_invocation_ceiling_cents):
            raise PreEffectCapacityError(
                "plafond par invocation provider refusé avant effet",
            )
        provider_invocation_ceiling = min(
            declared_provider_invocation_ceiling,
            profile.cost_ceiling_cents,
        )
        if (limits.minimum_tier is not None
                and LEVELS.index(profile.selected_tier) < LEVELS.index(limits.minimum_tier)):
            raise CommandWorkerError("floor Foundry refusé avant effet")
        authorization = ExecutionAuthorization(
            command_id=command.id,
            binding_digest=binding_digest,
            selected_tier=profile.selected_tier,
            cost_ceiling_cents=profile.cost_ceiling_cents,
            concurrency_units=profile.concurrency_units,
            approved_max_cost_cents=limits.max_cost_cents,
            approved_max_concurrency=limits.max_concurrency,
            minimum_tier=limits.minimum_tier,
            provider_invocation_ceiling_cents=provider_invocation_ceiling,
        )
        self.reservations.reserve(
            command.id, binding_digest, profile, limits,
            owner_id=owner_id, now_ms=self.now_ms(),
            prior_effect=prior_reservation_effect,
        )
        return authorization

    def _owner_heartbeat(
        self, command_id: str, binding_digest: str, owner_id: str,
        stop: threading.Event,
    ) -> None:
        interval = max(1.0, RESERVATION_OWNER_TTL_MS / 3000)
        while not stop.wait(interval):
            try:
                if not self.reservations.refresh_owner(
                    command_id, binding_digest, owner_id, now_ms=self.now_ms(),
                ):
                    return
            except Exception:
                # The allocation itself remains durable and therefore fail-closed.
                # The foreground path will surface its own result/ambiguity.
                return

    def _reconcile_capacity(
        self, command: DevHubCommand, binding_digest: str,
        authorization: ExecutionAuthorization, limits: EffectiveLimits,
        observation: RevalidationObservation, *, owner_id: str,
    ) -> None:
        now_ms = self.now_ms()
        latest = revalidate_command(command, observation, now_ms=now_ms)
        reconciled = _tighten_limits(limits, latest)
        profile = ExecutionProfile(
            authorization.selected_tier,
            authorization.cost_ceiling_cents,
            authorization.concurrency_units,
        )
        if (reconciled.minimum_tier is not None
                and LEVELS.index(profile.selected_tier)
                < LEVELS.index(reconciled.minimum_tier)):
            raise CommandWorkerError("floor Foundry refusé avant effet")
        if (authorization.provider_invocation_ceiling_cents
                > reconciled.provider_invocation_ceiling_cents):
            raise PreEffectCapacityError(
                "plafond par invocation provider resserré avant effet",
            )
        self.reservations.reconcile_capacity(
            command.id, binding_digest, profile, reconciled,
            owner_id=owner_id, now_ms=now_ms,
        )

    def reconcile(
        self, command: DevHubCommand, binding_digest: str, effect: EffectReceipt,
    ) -> None:
        if effect.status in _TERMINAL_EFFECT_STATUSES:
            self.reservations.settle(
                command.id, binding_digest, effect, now_ms=self.now_ms(),
            )

    def reconcile_existing(
        self, command: DevHubCommand, binding_digest: str,
        receipt: EffectReceipt, limits: EffectiveLimits, *,
        heartbeat: Callable[[], object],
    ) -> EffectReceipt:
        """Observe one proven effect without reserving or entering launch/resume."""
        if (receipt.effect_id != command.id
                or receipt.status not in _RESUMABLE_EFFECT_STATUSES
                or limits.max_cost_cents != 0
                or limits.provider_invocation_ceiling_cents != 0
                or limits.observed_budget_remaining_cents != 0):
            raise CommandWorkerError("réconciliation sans budget Foundry invalide")
        reconciler = getattr(self.provider, "reconcile_existing", None)
        if not callable(reconciler):
            raise PreEffectCapacityError(
                "budget hôte Foundry épuisé avant effet",
            )
        result = reconciler(command, receipt, heartbeat=heartbeat)
        if (not isinstance(result, EffectReceipt)
                or result.effect_id != command.id
                or (result.cost_cents is not None
                    and result.cost_cents > command.max_cost_cents)):
            raise CommandWorkerError("réconciliation d'effet Foundry contradictoire")
        return result

    def invoke(
        self, command: DevHubCommand, binding_digest: str,
        receipt: EffectReceipt | None, limits: EffectiveLimits, *,
        heartbeat: Callable[[], None],
        unlaunched_pause: EffectReceipt | None = None,
        engage: Callable[[], DevHubCommand] | None = None,
    ) -> EffectReceipt:
        if receipt is not None and receipt.status in _TERMINAL_EFFECT_STATUSES:
            raise CommandWorkerError("reçu terminal non reprenable")
        if unlaunched_pause is not None and (
            receipt is not None or unlaunched_pause.status != "paused"
            or unlaunched_pause.cost_cents != 0
            or unlaunched_pause.duration_ms != 0
            or unlaunched_pause.attempt_id is not None
            or unlaunched_pause.attempt_started_at is not None
        ):
            raise CommandWorkerError("pause pré-effet Foundry invalide")
        owner_id = f"reservation-{uuid.uuid4().hex}"
        authorization = self._authorize(
            command, binding_digest, receipt, limits, owner_id=owner_id,
            prior_reservation_effect=(
                unlaunched_pause if unlaunched_pause is not None else receipt
            ),
        )
        capacity_reconciled = False

        def reconcile_capacity(observation: RevalidationObservation) -> None:
            nonlocal capacity_reconciled
            if capacity_reconciled:
                raise CommandWorkerError("capacité Foundry réconciliée plusieurs fois")
            self._reconcile_capacity(
                command, binding_digest, authorization, limits, observation,
                owner_id=owner_id,
            )
            capacity_reconciled = True

        stop = threading.Event()
        thread = threading.Thread(
            target=self._owner_heartbeat,
            args=(command.id, binding_digest, owner_id, stop),
            name=f"foundry-reservation-{command.id}", daemon=True,
        )
        thread.start()
        try:
            try:
                if receipt is None:
                    if engage is not None:
                        engaged_command = engage()
                        if not isinstance(engaged_command, DevHubCommand):
                            raise CommandWorkerError(
                                "frontière de lancement Foundry invalide",
                            )
                        _require_approved_inputs_unchanged(
                            command, engaged_command, operation="lancement",
                        )
                        command = engaged_command
                    result = self.provider.launch(
                        command, authorization, effect_id=command.id, heartbeat=heartbeat,
                        reconcile_capacity=reconcile_capacity,
                    )
                else:
                    result = self.provider.resume(
                        command, receipt, authorization, heartbeat=heartbeat,
                        reconcile_capacity=reconcile_capacity,
                    )
                if not capacity_reconciled:
                    raise CommandWorkerError("capacité provider non réconciliée avant effet")
            except BaseException:
                self.reservations.yield_owner(command.id, binding_digest, owner_id)
                raise
        finally:
            stop.set()
            thread.join(timeout=5.0)
        try:
            if not isinstance(result, EffectReceipt) or result.effect_id != command.id:
                raise CommandWorkerError("identité d'effet provider contradictoire")
            if (result.cost_cents is not None
                    and result.cost_cents > authorization.cost_ceiling_cents):
                raise CommandWorkerError("plafond de coût provider dépassé")
            if result.status in _TERMINAL_EFFECT_STATUSES:
                self.reservations.settle(
                    command.id, binding_digest, result, now_ms=self.now_ms(),
                    owner_id=owner_id,
                )
            else:
                self.reservations.yield_owner(command.id, binding_digest, owner_id)
        except BaseException:
            self.reservations.yield_owner(command.id, binding_digest, owner_id)
            raise
        return result


class CommandWorker:
    def __init__(
        self, client: DevHubCommandClient, store: ReceiptStore,
        provider: CommandEffectProvider, *, worker_id: str,
        lease_seconds: int = 60, page_size: int = 50,
        max_pages: int = MAX_PULL_PAGES,
        max_commands: int = MAX_COMMANDS_PER_RUN,
        now_ms: Callable[[], int] | None = None,
        event_publisher: PassiveEventPublisher | None = None,
        execution_receipts: ExecutionReceiptStore | None = None,
        reservation_store: HostReservationStore | None = None,
    ):
        if _IDENTIFIER.fullmatch(worker_id) is None:
            raise ValueError("worker_id Foundry invalide")
        if not 15 <= lease_seconds <= 300:
            raise ValueError("lease Foundry hors borne")
        if not 1 <= page_size <= 100 or not 1 <= max_pages <= MAX_PULL_PAGES:
            raise ValueError("pagination worker Foundry hors borne")
        if not 1 <= max_commands <= MAX_COMMANDS_PER_RUN:
            raise ValueError("lot worker Foundry hors borne")
        self.client = client
        self.store = store
        self.provider = provider
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.page_size = page_size
        self.max_pages = max_pages
        self.max_commands = max_commands
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.event_publisher = event_publisher
        self.execution_receipts = execution_receipts
        self.reservations = reservation_store or HostReservationStore(
            store.path.parent / "host-reservations.sqlite3",
        )
        self.execution = BoundedExecutionPath(
            provider, self.reservations, now_ms=self.now_ms,
        )

    def _claim(self, command: DevHubCommand) -> DevHubCommand:
        if (command.lease is not None
                and command.lease.expires_at > self.now_ms()):
            if command.lease.worker_id != self.worker_id:
                raise CommandWorkerError("lease DevHub détenue par un autre worker")
            key = _idempotency(
                "claim-heartbeat", command.id, command.version, self.worker_id,
            )
            ambiguous = False
            try:
                claimed = self.client.heartbeat(
                    command, lease_id=command.lease.id,
                    extend_seconds=self.lease_seconds, idempotency_key=key,
                )
            except DevHubCommandTransportError:
                ambiguous = True
                try:
                    claimed = self.client.get_command(command.id)
                except DevHubCommandError:
                    raise AmbiguousEffectError("lease DevHub non résolue") from None
            _require_approved_inputs_unchanged(
                command, claimed, operation="claim-heartbeat",
            )
            if (claimed.lease is None
                    or claimed.lease.id != command.lease.id
                    or claimed.lease.worker_id != self.worker_id
                    or claimed.version <= command.version
                    or claimed.lease.heartbeat_at <= command.lease.heartbeat_at
                    or claimed.lease.expires_at <= self.now_ms()
                    or claimed.lease.heartbeat_at > claimed.lease.expires_at):
                if ambiguous:
                    raise AmbiguousEffectError("lease DevHub non résolue") from None
                raise CommandWorkerError("lease DevHub contradictoire")
            return claimed
        key = _idempotency("claim", command.id, command.version, self.worker_id)
        ambiguous = False
        try:
            claimed = self.client.claim(
                command, worker_id=self.worker_id, lease_seconds=self.lease_seconds,
                idempotency_key=key,
            )
        except DevHubCommandTransportError:
            ambiguous = True
            try:
                claimed = self.client.get_command(command.id)
            except DevHubCommandError:
                raise AmbiguousEffectError("claim DevHub non résolu") from None
        _require_approved_inputs_unchanged(command, claimed, operation="claim")
        if (claimed.lease is None or claimed.lease.worker_id != self.worker_id
                or claimed.version <= command.version
                or claimed.lease.expires_at <= self.now_ms()
                or claimed.lease.heartbeat_at > claimed.lease.expires_at):
            if ambiguous:
                raise AmbiguousEffectError("claim DevHub non résolu") from None
            raise CommandWorkerError("claim DevHub contradictoire")
        return claimed

    def _resolve_event(self, command_id: str, event_id: str) -> DevHubCommand | None:
        cursor = "0"
        for _ in range(MAX_PULL_PAGES):
            page = self.client.list_events(command_id, cursor=cursor, limit=100)
            if any(item.id == event_id for item in page.items):
                return self.client.get_command(command_id)
            if page.next_cursor is None:
                return None
            cursor = page.next_cursor
        raise AmbiguousEffectError("journal événements DevHub hors borne")

    def _event(
        self, session: _LeaseSession, event_type: str, *,
        receipt: EffectReceipt | None = None,
        terminal_evidence: Mapping[str, object] | None = None,
        terminal: bool = True,
    ) -> DevHubCommand:
        def append(command: DevHubCommand) -> DevHubCommand:
            sequence = command.next_event_sequence
            event_id = f"event-{_digest([command.id, sequence, event_type])[:32]}"
            key = _idempotency("event", command.id, event_id)
            evidence = dict(terminal_evidence) if terminal_evidence is not None else (
                self._terminal_command_evidence(
                    command, event_type=event_type, effect=receipt,
                )
            )
            try:
                updated = self.client.append_event(
                    command, event_id=event_id, sequence=sequence,
                    event_type=event_type, occurred_at=self.now_ms(),
                    lease_id=session.lease_id,
                    cost_cents=receipt.cost_cents if receipt else None,
                    duration_ms=receipt.duration_ms if receipt else None,
                    idempotency_key=key,
                    **evidence,
                )
            except DevHubCommandTransportError:
                updated = self._resolve_event(command.id, event_id)
                if updated is None:
                    raise AmbiguousEffectError("événement DevHub non résolu") from None
            _require_approved_inputs_unchanged(command, updated, operation="événement")
            return updated

        if terminal:
            return append(session.stop())
        # A lifecycle preamble must advance the same live lease. Hold the session
        # lock while the optimistic command version advances so its heartbeat cannot
        # race the exact sequence or overwrite the returned version.
        with session._lock:
            if session.error is not None:
                raise CommandWorkerError("heartbeat DevHub perdu") from session.error
            session.command = append(session.command)
            return session.command

    def _published_receipts(
        self, attempt_id: str,
    ) -> tuple[tuple[str, Mapping[str, object]], ...]:
        """Read only exact F91 receipts; unreadable evidence stays unavailable.

        The F91 store is append-only evidence. A read failure therefore cannot be
        translated into a retry, a command-state change, or fabricated receipts.
        """
        receipt_store = self.execution_receipts
        if receipt_store is None:
            return ()
        try:
            rows = receipt_store.for_attempt(attempt_id)
        except (OSError, ReceiptStoreError, ValueError):
            return ()
        published: list[tuple[str, Mapping[str, object]]] = []
        for row in rows:
            issue_id = row.get("issue_id")
            receipts = row.get("receipts")
            if (not isinstance(issue_id, str) or not isinstance(receipts, Mapping)
                    or not any(value is not None for value in receipts.values())):
                continue
            published.append((issue_id, receipts))
        return tuple(published)

    def _terminal_command_evidence(
        self, command: DevHubCommand, *, event_type: str,
        effect: EffectReceipt | None,
    ) -> dict[str, object]:
        """Bind a completed native lifecycle event to its exact F91 closure.

        Passive observations remain a separate journal. This narrow projection
        only enriches the already-authoritative completed command event when the
        original attempt produced a complete, representable closure coordinate.
        """
        if (event_type != "completed" or effect is None
                or effect.attempt_id is None):
            return {}
        command_receipts = next((
            receipts for issue_id, receipts in self._published_receipts(
                effect.attempt_id,
            ) if issue_id == command.epic_id
        ), None)
        return self._validated_terminal_command_evidence(
            command, effect, command_receipts,
        )

    @staticmethod
    def _validated_terminal_command_evidence(
        command: DevHubCommand, effect: EffectReceipt,
        command_receipts: object,
    ) -> dict[str, object]:
        """Validate either passive F91 data or a freshly re-read original receipt."""
        if not isinstance(command_receipts, Mapping):
            return {}
        closure = command_receipts.get("epic_closure")
        if (not isinstance(closure, Mapping)
                or set(closure) != {
                    "epic_id", "state", "receipt_id", "parent_version",
                    "children", "closure_digest",
                }
                or closure.get("epic_id") != command.epic_id
                or not isinstance(closure.get("children"), list)
                or not 1 <= len(closure["children"]) <= 100):
            return {}
        return {
            "attempt_id": effect.attempt_id,
            "issue_id": command.epic_id,
            "receipts": command_receipts,
        }

    def _terminal_evidence_for_publication(
        self, command: DevHubCommand, effect: EffectReceipt,
    ) -> tuple[dict[str, object], bool]:
        """Obtain a verified native campaign closure for ``completed``.

        Providers without the narrow campaign-evidence hook retain their existing event
        contract.  The campaign provider uses it only for receipts carrying the
        native attempt coordinate; its true legacy receipts explicitly opt out.
        """
        evidence_hook = getattr(
            self.provider, "verified_campaign_terminal_evidence", None,
        )
        if not callable(evidence_hook):
            return self._terminal_command_evidence(
                command, event_type="completed", effect=effect,
            ), False
        recovered = evidence_hook(command, effect)
        if (not isinstance(recovered, tuple) or len(recovered) != 2
                or type(recovered[0]) is not bool
                or (recovered[1] is not None
                    and not isinstance(recovered[1], Mapping))):
            raise CommandWorkerError("contrat de preuve terminale provider invalide")
        required, receipts = recovered
        evidence: dict[str, object] = {}
        if receipts is not None:
            evidence = self._validated_terminal_command_evidence(
                command, effect, receipts,
            )
            if not evidence:
                raise CommandWorkerError("preuve terminale provider invalide")
        return evidence, required and not evidence

    def _publish_observed_effect(
        self, command: DevHubCommand, effect: EffectReceipt,
    ) -> None:
        """Best-effort passive publication after the effect is durably proven.

        The event is derived solely from the claimed command and provider receipt.
        A failed, stale or backpressured publication is deliberately invisible to
        the execution path: it neither rewrites the effect nor changes its outcome.
        """
        publisher = self.event_publisher
        if publisher is None:
            return
        if (command.lease is None or effect.attempt_id is None
                or effect.attempt_started_at is None):
            # Older/generic providers do not expose an independent attempt fact.
            # Absence remains null: command/effect identity is not relabelled as an
            # attempt merely to make the passive event look complete.
            return
        observed = {
            "id": command.id,
            "version": command.version,
            "lease": {"id": command.lease.id},
            "next_event_sequence": command.next_event_sequence,
        }
        try:
            receipt_rows = self._published_receipts(effect.attempt_id)
            command_receipts = next((
                receipts for issue_id, receipts in receipt_rows
                if issue_id == command.epic_id
            ), None)
            if effect.status != "running":
                publisher.publish(
                    command=observed, attempt_id=effect.attempt_id,
                    issue_id=command.epic_id, event_type="running",
                    occurred_at=effect.attempt_started_at,
                )
            publisher.publish(
                command=observed, attempt_id=effect.attempt_id,
                issue_id=command.epic_id, event_type=effect.status,
                occurred_at=self.now_ms(),
                metrics={
                    "cost_cents": effect.cost_cents,
                    "duration_ms": effect.duration_ms,
                },
                receipts=command_receipts,
            )
            # F91 associates a receipt with the exact attempt and issue that
            # observed it. Emit each available evidence row separately so an
            # epic-level runtime cost is never attributed to a child issue and a
            # receipt cannot be relabelled as the command's own issue state.
            for issue_id, receipts in receipt_rows:
                if issue_id == command.epic_id:
                    # The command event already carries its exact receipts. A
                    # second event with the same identity would be a conflicting
                    # duplicate rather than an enrichment.
                    continue
                publisher.publish(
                    command=observed, attempt_id=effect.attempt_id,
                    # A F91 receipt proves only the receipt's exact source.
                    # It does not prove a child lifecycle transition, even when
                    # the enclosing epic command has completed.
                    issue_id=issue_id, event_type="unknown",
                    occurred_at=self.now_ms(), receipts=receipts,
                )
        except Exception:
            # Publication is telemetry/provenance only.  It is never an execution
            # gate and cannot turn a proven effect into a retry or refusal.
            return

    def _flush_passive_observations(self, command: DevHubCommand) -> None:
        """Replay any durable passive facts even after native terminalization.

        The command list supplies the current optimistic version, so a retry is
        not tied to the old lifecycle version stored with the pending fact.
        Publication remains strictly observational: no outcome, gate, routing or
        command field is inspected or altered by a failed flush.
        """
        publisher = self.event_publisher
        if publisher is None:
            return
        try:
            publisher.flush(command.id, command_version=command.version)
        except Exception:
            return

    @staticmethod
    def _pre_effect_pause(
        command: DevHubCommand, binding_digest: str, error: PreEffectCapacityError,
    ) -> EffectReceipt:
        """Materialize a zero-cost, resume-safe pause before provider launch.

        The subclass is emitted only by trusted authority/capacity guards.  It
        therefore proves that no provider effect was reachable, unlike transport
        or generic worker failures which must remain unresolved and resolve-first.
        """
        return EffectReceipt(
            command.id,
            "paused",
            _digest({
                "contract": "foundry-pre-effect-pause.v1",
                "command_id": command.id,
                "binding_digest": binding_digest,
                "reason": str(error),
            }),
            cost_cents=0,
            duration_ms=0,
        )

    @staticmethod
    def _preamble_reservation_proof(
        command: DevHubCommand, binding_digest: str,
    ) -> EffectReceipt:
        """Reclaim only the reservation guarded by a durable preamble phase.

        This receipt is never stored or projected as an effect.  It is the typed
        zero-cost proof expected by the reservation ledger when ownership was
        yielded before ``launch-intent`` could be written.
        """
        return EffectReceipt(
            command.id,
            "paused",
            _digest({
                "contract": "foundry-pre-effect-preamble.v1",
                "command_id": command.id,
                "binding_digest": binding_digest,
            }),
            cost_cents=0,
            duration_ms=0,
        )

    @staticmethod
    def _pre_effect_human_control(
        command: DevHubCommand, binding_digest: str, control: str,
    ) -> tuple[EffectReceipt, str]:
        """Confirm one human control without inventing a provider effect."""
        outcome = {
            "pause-requested": ("paused", _HUMAN_PAUSE_PHASE),
            "cancel-requested": ("cancelled", _HUMAN_CANCEL_PHASE),
        }.get(control)
        if outcome is None:
            raise CommandWorkerError("contrôle humain pré-effet invalide")
        status, phase = outcome
        return EffectReceipt(
            command.id,
            status,
            _digest({
                "contract": "foundry-pre-effect-human-control.v1",
                "command_id": command.id,
                "binding_digest": binding_digest,
                "control": control,
            }),
            cost_cents=0,
            duration_ms=0,
        ), phase

    def _execute(self, command: DevHubCommand) -> WorkerOutcome:
        late_hook = getattr(self.provider, "late_terminal_evidence", None)
        if (command.lease is not None and command.lease.expires_at <= self.now_ms()
                and callable(late_hook)):
            required, evidence = late_hook(command)
            if required:
                if evidence is None:
                    return WorkerOutcome(command.id, "deferred", "terminal_evidence_unavailable")
                original = evidence["original_closure"]
                coordinates = _digest([
                    "foundry-late-terminal-publication.v1", _binding_digest(command),
                    command.lease.id, command.next_event_sequence, original,
                ])
                self.client.publish_late_terminal(
                    command, publication_id=f"late-{coordinates[:32]}",
                    event_id=f"event-{coordinates[32:]}",
                    sequence=command.next_event_sequence,
                    occurred_at=original["outcome"]["receipt"]["issued_at"],
                    lease_id=command.lease.id, **evidence,
                )
                return WorkerOutcome(command.id, "completed", "late_terminal_fact_published")
        stale_recovery = command.state == "unknown" and command.projection_stale
        claimed = self._claim(command)
        binding = _binding_digest(claimed)
        assert claimed.lease is not None
        durable = self.store.get(claimed.id)
        if durable is not None and durable.binding_digest != binding:
            raise CommandWorkerError("binding durable modifié")
        if durable is None:
            durable = self.store.record(
                claimed, binding, "claimed", lease_id=claimed.lease.id,
                effect=None, now_ms=self.now_ms(),
            )

        # Resolve first on every restart and for every uncertain DevHub projection.
        resolved = self.provider.resolve(claimed.id, binding)
        if resolved is not None:
            if resolved.effect_id != claimed.id:
                raise CommandWorkerError("identité d'effet provider contradictoire")
            durable = self.store.record(
                claimed, binding,
                "terminal" if resolved.status in _TERMINAL_EFFECT_STATUSES else "effect-proven",
                lease_id=claimed.lease.id, effect=resolved, now_ms=self.now_ms(),
            )
        unlaunched_preamble = durable.phase == _PRE_EFFECT_PREAMBLE_PHASE
        unlaunched_pre_effect = durable.phase == _PRE_EFFECT_PAUSE_PHASE
        human_controlled_pre_effect = durable.phase in _HUMAN_CONTROL_PHASES
        provider_unreachable = unlaunched_preamble or unlaunched_pre_effect
        provider_effect_exists = (
            durable.effect is not None
            and not provider_unreachable
            and not human_controlled_pre_effect
        )
        can_reconcile_without_budget = (
            provider_effect_exists
            and callable(getattr(self.provider, "reconcile_existing", None))
        )

        # Expired authority must still fail closed before any new or resumed
        # effect, but it cannot erase a terminal fact already proven durably.
        initial_limits: EffectiveLimits | None = None
        initial_capacity_error: PreEffectCapacityError | None = None
        if (not human_controlled_pre_effect
                and (durable.effect is None
                     or durable.effect.status not in _TERMINAL_EFFECT_STATUSES)):
            try:
                observation = self.provider.observe(claimed)
                initial_limits = revalidate_command(
                    claimed, observation, now_ms=self.now_ms(),
                    allow_zero_budget_reconciliation=(
                        can_reconcile_without_budget
                    ),
                )
            except PreEffectCapacityError as exc:
                initial_capacity_error = exc

        if durable.phase in {"launch-intent", "unresolved"}:
            self.store.record(
                claimed, binding, "unresolved", lease_id=claimed.lease.id,
                effect=durable.effect, now_ms=self.now_ms(),
            )
            return WorkerOutcome(claimed.id, "unknown", "effect_identity_unresolved")
        if (not provider_effect_exists and not provider_unreachable
                and not human_controlled_pre_effect
                and (claimed.state not in {"requested", *_CONTROL_REQUEST_STATES}
                     or claimed.projection_stale)):
            # A prior worker may have produced an effect without leaving a local
            # receipt on this host.  Absence of proof is not proof of absence.
            self.store.record(
                claimed, binding, "unresolved", lease_id=claimed.lease.id,
                effect=durable.effect, now_ms=self.now_ms(),
            )
            return WorkerOutcome(claimed.id, "unknown", "prior_effect_unresolved")

        session = _LeaseSession(
            self.client, claimed, lease_seconds=self.lease_seconds, now_ms=self.now_ms,
        )
        session.start()
        effect_limits: EffectiveLimits | None = None
        pre_effect_pause = False
        human_control_phase: str | None = None
        try:
            if human_controlled_pre_effect:
                assert durable.effect is not None
                if (durable.phase == _HUMAN_PAUSE_PHASE
                        and session.command.state == "cancel-requested"):
                    effect, human_control_phase = self._pre_effect_human_control(
                        claimed, binding, session.command.state,
                    )
                elif session.command.state in {
                    _HUMAN_CONTROL_PHASES[durable.phase],
                    "pause-requested"
                    if durable.phase == _HUMAN_PAUSE_PHASE
                    else "cancel-requested",
                }:
                    effect = durable.effect
                    human_control_phase = durable.phase
                else:
                    # DevHub v1 exposes no explicit human-resume proof.  A changed
                    # projection alone can therefore never revive this command.
                    raise AmbiguousEffectError(
                        "contrôle humain pré-effet non résolu",
                    )
            elif provider_effect_exists:
                assert durable.effect is not None
                if durable.effect.status in _TERMINAL_EFFECT_STATUSES:
                    effect = durable.effect
                else:
                    if initial_limits is None and initial_capacity_error is None:
                        raise CommandWorkerError("limites pré-effet absentes")
                    session.heartbeat()
                    if stale_recovery:
                        if (durable.effect.status not in _RESUMABLE_EFFECT_STATUSES
                                or session.command.state != "unknown"
                                or not session.command.projection_stale):
                            raise AmbiguousEffectError(
                                "projection DevHub obsolète non réconciliable",
                            )
                        try:
                            if initial_capacity_error is not None:
                                raise initial_capacity_error
                            if initial_limits is None:
                                raise CommandWorkerError("limites pré-effet absentes")
                            latest = revalidate_command(
                                session.command, self.provider.observe(session.command),
                                now_ms=self.now_ms(),
                                allow_zero_budget_reconciliation=(
                                    can_reconcile_without_budget
                                ),
                            )
                            _tighten_limits(initial_limits, latest)
                        except PreEffectCapacityError as exc:
                            effect = self._pre_effect_pause(claimed, binding, exc)
                            pre_effect_pause = True
                        else:
                            restored = self._event(session, "running", terminal=False)
                            if (restored.state != "running"
                                    or restored.projection_stale
                                    or restored.lease is None
                                    or restored.lease.id != session.lease_id
                                    or restored.lease.worker_id != self.worker_id):
                                raise AmbiguousEffectError(
                                    "projection DevHub de reprise non résolue",
                                )
                            self.store.record(
                                restored, binding, "effect-proven",
                                lease_id=restored.lease.id, effect=durable.effect,
                                now_ms=self.now_ms(),
                            )
                            return WorkerOutcome(
                                claimed.id, "running", "resume_projection_restored",
                            )
                    else:
                        try:
                            if initial_capacity_error is not None:
                                raise initial_capacity_error
                            assert initial_limits is not None
                            _require_actionable_projection(session.command, durable.effect)
                            latest = revalidate_command(
                                session.command, self.provider.observe(session.command),
                                now_ms=self.now_ms(),
                                allow_zero_budget_reconciliation=(
                                    can_reconcile_without_budget
                                ),
                            )
                            limits = _tighten_limits(initial_limits, latest)
                            if limits.observed_budget_remaining_cents == 0:
                                effect = self.execution.reconcile_existing(
                                    session.command, binding, durable.effect, limits,
                                    heartbeat=session.heartbeat,
                                )
                            else:
                                effect_limits = limits
                                effect = self.execution.invoke(
                                    session.command, binding, durable.effect, limits,
                                    heartbeat=session.heartbeat,
                                )
                        except PreEffectCapacityError as exc:
                            effect = self._pre_effect_pause(claimed, binding, exc)
                            pre_effect_pause = True
                        except (TimeoutError, OSError, DevHubCommandTransportError):
                            effect = self.provider.resolve(claimed.id, binding)
                            if effect is None:
                                raise AmbiguousEffectError(
                                    "reprise Foundry non résolue",
                                ) from None
            else:
                if initial_limits is None and initial_capacity_error is None:
                    raise CommandWorkerError("limites pré-effet absentes")
                if ((provider_unreachable or initial_capacity_error is not None)
                        and session.command.state in _CONTROL_REQUEST_STATES):
                    effect, human_control_phase = self._pre_effect_human_control(
                        claimed, binding, session.command.state,
                    )
                elif provider_unreachable and initial_capacity_error is not None:
                    # Durable proof already keeps the provider unreachable. Persist
                    # the exact current refusal until two fresh capacity observations
                    # authorize a first provider launch.
                    effect = self._pre_effect_pause(
                        claimed, binding, initial_capacity_error,
                    )
                    pre_effect_pause = True
                else:
                    session.heartbeat()
                    if ((provider_unreachable or initial_capacity_error is not None)
                            and session.command.state in _CONTROL_REQUEST_STATES):
                        effect, human_control_phase = self._pre_effect_human_control(
                            claimed, binding, session.command.state,
                        )
                    else:
                        if unlaunched_preamble:
                            _require_unlaunched_preamble_projection(session.command)
                        else:
                            _require_actionable_projection(
                                session.command,
                                durable.effect if unlaunched_pre_effect else None,
                            )
                        if unlaunched_pre_effect and session.command.state == "unknown":
                            raise AmbiguousEffectError(
                                "projection de reprise pré-effet non résolue",
                            )
                        try:
                            if initial_capacity_error is not None:
                                raise initial_capacity_error
                            assert initial_limits is not None
                            latest = revalidate_command(
                                session.command, self.provider.observe(session.command),
                                now_ms=self.now_ms(),
                            )
                            limits = _tighten_limits(initial_limits, latest)
                            effect_limits = limits
                            unlaunched_proof = (
                                durable.effect
                                if unlaunched_pre_effect
                                else self._preamble_reservation_proof(claimed, binding)
                                if unlaunched_preamble
                                else None
                            )
                            # This phase must precede the reservation commit itself:
                            # neither a process death after ``reserve`` nor a failure
                            # before ``engage`` may strand an allocation without exact
                            # proof that the provider boundary was still unreachable.
                            if not unlaunched_pre_effect:
                                self.store.record(
                                    session.command, binding,
                                    _PRE_EFFECT_PREAMBLE_PHASE,
                                    lease_id=session.lease_id, effect=None,
                                    now_ms=self.now_ms(),
                                )

                            def engage() -> DevHubCommand:
                                # Capacity, reservation and the lifecycle preamble
                                # must all succeed before the crash-durable marker
                                # says a provider effect may exist.
                                if (unlaunched_preamble
                                        and session.command.state == "unknown"
                                        and session.command.projection_stale):
                                    self._event(
                                        session, "running", terminal=False,
                                    )
                                if session.command.state == "requested":
                                    self._event(
                                        session, "accepted", terminal=False,
                                    )
                                if session.command.state in {"accepted", "paused"}:
                                    self._event(
                                        session, "running", terminal=False,
                                    )
                                if (session.command.state not in {
                                        "running", *_CONTROL_REQUEST_STATES,
                                }
                                        or session.command.projection_stale
                                        or session.command.lease is None
                                        or session.command.lease.id != session.lease_id
                                        or session.command.lease.worker_id != self.worker_id):
                                    raise AmbiguousEffectError(
                                        "préambule lifecycle DevHub non résolu",
                                    )
                                self.store.record(
                                    claimed, binding, "launch-intent",
                                    lease_id=claimed.lease.id,
                                    effect=(
                                        durable.effect
                                        if unlaunched_pre_effect else None
                                    ),
                                    now_ms=self.now_ms(),
                                )
                                return session.command

                            effect = self.execution.invoke(
                                session.command, binding, None, limits,
                                heartbeat=session.heartbeat,
                                unlaunched_pause=unlaunched_proof,
                                engage=engage,
                            )
                        except PreEffectCapacityError as exc:
                            effect = self._pre_effect_pause(claimed, binding, exc)
                            pre_effect_pause = True
                        except (TimeoutError, OSError, DevHubCommandTransportError):
                            current = self.store.get(claimed.id)
                            if (current is not None and current.phase in {
                                    _PRE_EFFECT_PREAMBLE_PHASE,
                                    _PRE_EFFECT_PAUSE_PHASE,
                            }):
                                # ``engage`` writes launch-intent only after every
                                # lifecycle prerequisite.  While either of these
                                # phases remains durable, the provider call is
                                # therefore unreachable and resolution must not
                                # erase that exact retry proof.
                                raise
                            effect = self.provider.resolve(claimed.id, binding)
                            if effect is None:
                                self.store.record(
                                    claimed, binding, "unresolved",
                                    lease_id=claimed.lease.id,
                                    effect=(
                                        durable.effect if unlaunched_pre_effect else None
                                    ),
                                    now_ms=self.now_ms(),
                                )
                                raise AmbiguousEffectError(
                                    "effet Foundry non résolu",
                                ) from None
            if (pre_effect_pause and not provider_effect_exists
                    and session.command.state in _CONTROL_REQUEST_STATES):
                # The capacity guard proved that the provider boundary was not
                # crossed.  A human control observed meanwhile is therefore the
                # durable fact, not another technical pause.
                effect, human_control_phase = self._pre_effect_human_control(
                    claimed, binding, session.command.state,
                )
                pre_effect_pause = False
            if effect.effect_id != claimed.id:
                raise CommandWorkerError("identité d'effet provider contradictoire")
            expected_control_status = {
                "pause-requested": "paused", "cancel-requested": "cancelled",
            }.get(session.command.state)
            if (expected_control_status is not None
                    and effect.status != expected_control_status):
                raise CommandWorkerError("contrôle humain non confirmé par le provider")
            self.execution.reconcile(session.command, binding, effect)
            ceiling = (
                effect_limits.max_cost_cents
                if effect_limits is not None else claimed.max_cost_cents
            )
            if effect.cost_cents is not None and effect.cost_cents > ceiling:
                raise CommandWorkerError("plafond de coût dépassé")
            phase = (
                human_control_phase
                if human_control_phase is not None
                else "terminal"
                if effect.status in _TERMINAL_EFFECT_STATUSES
                else _PRE_EFFECT_PAUSE_PHASE
                if pre_effect_pause and not provider_effect_exists
                else "effect-proven"
            )
            self.store.record(
                claimed, binding, phase, lease_id=claimed.lease.id,
                effect=effect, now_ms=self.now_ms(),
            )
            if pre_effect_pause and not provider_effect_exists:
                # The pause is durable before these lifecycle prerequisites. If
                # either event fails, a later scan still has exact proof that no
                # provider effect was reachable and no launch intent was written.
                if session.command.state == "requested":
                    self._event(session, "accepted", terminal=False)
                if session.command.state == "accepted":
                    self._event(session, "running", terminal=False)
            event_type = effect.status
            # The native command event is the authoritative terminal transition.
            # Passive F88 publication is strictly additive and runs only after
            # that transition (including its ambiguity resolution) succeeds.
            if session.command.state == "paused" and event_type == "paused":
                # DevHub v1 does not accept paused->paused. The provider was still
                # reached to reconcile the exact local reason, but absence of a
                # proven resume leaves the already-projected fact untouched.
                session.stop()
                detail = (
                    "human_pause_retained"
                    if human_control_phase == _HUMAN_PAUSE_PHASE
                    else "pre_effect_pause_retained"
                    if pre_effect_pause
                    else "effect_already_projected"
                )
            else:
                terminal_evidence = None
                if event_type == "completed":
                    terminal_evidence, evidence_pending = (
                        self._terminal_evidence_for_publication(
                            session.command, effect,
                        )
                    )
                    if evidence_pending:
                        session.stop()
                        return WorkerOutcome(
                            claimed.id, "deferred", "terminal_evidence_unavailable",
                        )
                terminal_command = self._event(
                    session, event_type, receipt=effect,
                    terminal_evidence=terminal_evidence,
                )
                self._publish_observed_effect(terminal_command, effect)
                detail = (
                    "human_control_confirmed"
                    if human_control_phase is not None
                    else "pre_effect_paused"
                    if pre_effect_pause
                    else "effect_proven"
                )
            return WorkerOutcome(claimed.id, event_type, detail)
        finally:
            if not session._stop.is_set():
                session.stop()

    def run_once(self, project: str) -> tuple[WorkerOutcome, ...]:
        """Scan a bounded cursor window and process at most ``max_commands``."""
        if not isinstance(project, str) or re.fullmatch(r"^[A-Z][A-Z0-9]{1,15}$", project) is None:
            raise ValueError("projet worker Foundry invalide")
        cursor = self.store.cursor(project)
        outcomes: list[WorkerOutcome] = []
        pages = 0
        while pages < self.max_pages and len(outcomes) < self.max_commands:
            page = self.client.list_commands(project, cursor=cursor, limit=self.page_size)
            pages += 1
            for command in page.items:
                if len(outcomes) >= self.max_commands:
                    break
                if command.project != project:
                    continue
                # Terminal commands still own passive spool records. Replaying
                # them is independent of lifecycle execution and lets an outage
                # recover without reopening or reprocessing the command.
                self._flush_passive_observations(command)
                if command.terminal:
                    continue
                if (command.scheduled_for is not None
                        and command.scheduled_for > self.now_ms()
                        and command.state not in _CONTROL_REQUEST_STATES):
                    continue
                if (command.lease is not None and command.lease.expires_at > self.now_ms()
                        and command.lease.worker_id != self.worker_id):
                    continue
                try:
                    outcomes.append(self._execute(command))
                except DevHubCommandError as exc:
                    outcomes.append(WorkerOutcome(command.id, "deferred", exc.code))
                except CommandRevalidationError:
                    outcomes.append(WorkerOutcome(command.id, "refused", "revalidation_failed"))
                except AmbiguousEffectError:
                    outcomes.append(WorkerOutcome(command.id, "unknown", "identity_unresolved"))
            if page.next_cursor is None:
                cursor = "0"
                break
            cursor = page.next_cursor
        self.store.set_cursor(project, cursor)
        return tuple(outcomes)
