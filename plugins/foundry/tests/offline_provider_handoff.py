"""Reusable offline double for crash-safe provider handoff tests.

This module is deliberately test-only and standard-library-only.  Its fixture
capabilities model capacity checks but never represent authenticated provider,
tracker, campaign, or human authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class HandoffRejected(PermissionError):
    """Raised when an offline handoff cannot prove its exact bounded context."""


class InjectedHandoffCrash(OSError):
    """Raised at an explicit test-only crash boundary."""


class CrashPoint(str, Enum):
    BEFORE_EFFECT = "before_effect"
    AFTER_EFFECT_BEFORE_RECEIPT = "after_effect_before_receipt"


@dataclass(frozen=True)
class HandoffCoordinates:
    """Every coordinate that binds one simulated provider effect."""

    issue_id: str
    operation_id: str
    diff_sha256: str
    ac_sha256: str
    generation: int
    authority_id: str

    def __post_init__(self) -> None:
        for name in ("issue_id", "operation_id", "authority_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")
        for name in ("diff_sha256", "ac_sha256"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not isinstance(self.generation, int) or self.generation < 1:
            raise ValueError("generation must be a positive integer")

    def frozen(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def identity_sha256(self) -> str:
        encoded = json.dumps(
            self.frozen(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FixtureCapability:
    """Opaque capacity token whose authority is explicitly always false."""

    capability_id: str
    authoritative: bool = False

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id must be non-empty")
        if self.authoritative is not False:
            raise ValueError("fixture capabilities cannot become authoritative")


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise HandoffRejected("durable fixture state must be an object")
    return value


class DurableOfflineProvider:
    """Provider-side idempotency and capacity state, isolated from local receipts."""

    _SCHEMA = "foundry-offline-provider.v1"

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)
        if self.state_path.exists():
            self._load()
        else:
            _atomic_write(
                self.state_path,
                {
                    "schema": self._SCHEMA,
                    "fixture_authority": False,
                    "next_capability": 1,
                    "capabilities": {},
                    "effects": {},
                    "effect_count": 0,
                },
            )

    def _load(self) -> dict[str, Any]:
        state = _read(self.state_path)
        if (
            state.get("schema") != self._SCHEMA
            or state.get("fixture_authority") is not False
            or not isinstance(state.get("capabilities"), dict)
            or not isinstance(state.get("effects"), dict)
            or not isinstance(state.get("effect_count"), int)
        ):
            raise HandoffRejected("invalid offline provider state")
        return state

    def issue_fixture_capability(
        self, coordinates: HandoffCoordinates, *, now: int, ttl: int
    ) -> FixtureCapability:
        """Mint offline capacity data; this deliberately grants no real authority."""
        if not isinstance(now, int):
            raise ValueError("now must be an integer")
        if not isinstance(ttl, int) or ttl <= 0:
            raise ValueError("ttl must be a positive integer")
        state = self._load()
        capability_id = f"offline-fixture-capability-{state['next_capability']:04d}"
        state["next_capability"] += 1
        state["capabilities"][capability_id] = {
            "authoritative": False,
            "coordinates": coordinates.frozen(),
            "identity_sha256": coordinates.identity_sha256,
            "issued_at": now,
            "expires_at": now + ttl,
            "status": "issued",
        }
        _atomic_write(self.state_path, state)
        return FixtureCapability(capability_id)

    def consume_fixture_capability_without_effect(
        self, capability: FixtureCapability
    ) -> None:
        """Arrange an already-spent capacity grant for a fail-closed test case."""
        state = self._load()
        grant = state["capabilities"].get(capability.capability_id)
        if grant is None or grant.get("status") != "issued":
            raise HandoffRejected("issued fixture capability required")
        grant["status"] = "consumed"
        grant["consumed_by"] = "offline-test-arrangement-without-effect"
        _atomic_write(self.state_path, state)

    def apply(
        self,
        coordinates: HandoffCoordinates,
        *,
        capability: FixtureCapability | None,
        now: int,
        crash_before_effect: bool,
    ) -> tuple[dict[str, Any], bool]:
        """Apply or replay one exact simulated effect at the provider boundary."""
        if not isinstance(now, int):
            raise ValueError("now must be an integer")
        state = self._load()
        existing = state["effects"].get(coordinates.operation_id)
        if existing is not None:
            self._require_exact_effect(existing, coordinates, capability)
            return dict(existing["result"]), True

        grant = self._require_first_effect_capacity(state, coordinates, capability, now)
        if crash_before_effect:
            raise InjectedHandoffCrash("injected crash before provider effect")

        effect_id = hashlib.sha256(
            f"offline-provider-effect:{coordinates.identity_sha256}".encode("utf-8")
        ).hexdigest()
        result = {
            "effect_id": effect_id,
            "identity_sha256": coordinates.identity_sha256,
            "operation_id": coordinates.operation_id,
        }
        state["effects"][coordinates.operation_id] = {
            "capability_id": capability.capability_id,
            "coordinates": coordinates.frozen(),
            "identity_sha256": coordinates.identity_sha256,
            "result": result,
        }
        grant["status"] = "consumed"
        grant["consumed_by"] = coordinates.operation_id
        state["effect_count"] += 1
        _atomic_write(self.state_path, state)
        return result, False

    @staticmethod
    def _require_exact_effect(
        existing: dict[str, Any],
        coordinates: HandoffCoordinates,
        capability: FixtureCapability | None,
    ) -> None:
        if existing.get("coordinates") != coordinates.frozen():
            raise HandoffRejected("provider idempotency identity conflict")
        if (
            not isinstance(capability, FixtureCapability)
            or existing.get("capability_id") != capability.capability_id
        ):
            raise HandoffRejected("provider capability identity conflict")

    @staticmethod
    def _require_first_effect_capacity(
        state: dict[str, Any],
        coordinates: HandoffCoordinates,
        capability: FixtureCapability | None,
        now: int,
    ) -> dict[str, Any]:
        if not isinstance(capability, FixtureCapability):
            raise HandoffRejected("offline fixture capability required")
        grant = state["capabilities"].get(capability.capability_id)
        if grant is None or grant.get("authoritative") is not False:
            raise HandoffRejected("known non-authoritative fixture capability required")
        if grant.get("coordinates") != coordinates.frozen():
            raise HandoffRejected("fixture capability context drift")
        if grant.get("status") != "issued":
            raise HandoffRejected("fixture capability already consumed")
        if now >= grant.get("expires_at", 0):
            raise HandoffRejected("fixture capability expired")
        return grant

    def snapshot(self) -> dict[str, Any]:
        return self._load()


class DurableLocalLedger:
    """Local intent and receipt state, never co-persisted with provider effects."""

    _SCHEMA = "foundry-offline-handoff-ledger.v1"

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)
        if self.state_path.exists():
            self._load()
        else:
            _atomic_write(
                self.state_path,
                {
                    "schema": self._SCHEMA,
                    "operations": {},
                    "receipt_count": 0,
                },
            )

    def _load(self) -> dict[str, Any]:
        state = _read(self.state_path)
        if (
            state.get("schema") != self._SCHEMA
            or not isinstance(state.get("operations"), dict)
            or not isinstance(state.get("receipt_count"), int)
        ):
            raise HandoffRejected("invalid local handoff ledger")
        return state

    def begin(
        self,
        coordinates: HandoffCoordinates,
        capability: FixtureCapability | None,
    ) -> dict[str, Any] | None:
        state = self._load()
        capability_id = (
            capability.capability_id
            if isinstance(capability, FixtureCapability)
            else None
        )
        existing = state["operations"].get(coordinates.operation_id)
        if existing is not None:
            if (
                existing.get("coordinates") != coordinates.frozen()
                or existing.get("capability_id") != capability_id
            ):
                raise HandoffRejected("local operation identity conflict")
            if existing.get("status") == "completed":
                return dict(existing["receipt"])
            if existing.get("status") != "intent" or existing.get("receipt") is not None:
                raise HandoffRejected("invalid local operation state")
            return None

        state["operations"][coordinates.operation_id] = {
            "capability_id": capability_id,
            "coordinates": coordinates.frozen(),
            "identity_sha256": coordinates.identity_sha256,
            "status": "intent",
            "receipt": None,
        }
        _atomic_write(self.state_path, state)
        return None

    def complete(
        self,
        coordinates: HandoffCoordinates,
        capability: FixtureCapability,
        provider_result: dict[str, Any],
        *,
        provider_replayed: bool,
    ) -> dict[str, Any]:
        state = self._load()
        operation = state["operations"].get(coordinates.operation_id)
        if operation is None:
            raise HandoffRejected("durable local intent required")
        if (
            operation.get("coordinates") != coordinates.frozen()
            or operation.get("capability_id") != capability.capability_id
        ):
            raise HandoffRejected("local operation identity conflict")
        if operation.get("status") == "completed":
            return dict(operation["receipt"])
        if operation.get("status") != "intent" or operation.get("receipt") is not None:
            raise HandoffRejected("invalid local operation state")
        if (
            provider_result.get("identity_sha256") != coordinates.identity_sha256
            or provider_result.get("operation_id") != coordinates.operation_id
        ):
            raise HandoffRejected("provider result identity conflict")

        receipt = {
            "schema": "foundry-offline-handoff-receipt.v1",
            "coordinates": coordinates.frozen(),
            "identity_sha256": coordinates.identity_sha256,
            "provider_effect_id": provider_result["effect_id"],
            "provider_effect_replayed": provider_replayed,
        }
        operation["status"] = "completed"
        operation["receipt"] = receipt
        state["receipt_count"] += 1
        _atomic_write(self.state_path, state)
        return receipt

    def snapshot(self) -> dict[str, Any]:
        return self._load()


class OfflineProviderHandoff:
    """Crash-injectable coordinator over separate provider and local stores."""

    def __init__(self, *, provider_state_path: Path, local_ledger_path: Path) -> None:
        provider_path = Path(provider_state_path)
        ledger_path = Path(local_ledger_path)
        if provider_path.resolve() == ledger_path.resolve():
            raise ValueError("provider state and local ledger must use separate files")
        self.provider = DurableOfflineProvider(provider_path)
        self.ledger = DurableLocalLedger(ledger_path)

    def issue_fixture_capability(
        self, coordinates: HandoffCoordinates, *, now: int, ttl: int
    ) -> FixtureCapability:
        return self.provider.issue_fixture_capability(coordinates, now=now, ttl=ttl)

    def consume_fixture_capability_without_effect(
        self, capability: FixtureCapability
    ) -> None:
        self.provider.consume_fixture_capability_without_effect(capability)

    def execute(
        self,
        coordinates: HandoffCoordinates,
        *,
        capability: FixtureCapability | None,
        now: int,
        crash_at: CrashPoint | None = None,
    ) -> dict[str, Any]:
        completed = self.ledger.begin(coordinates, capability)
        if completed is not None:
            return completed
        result, provider_replayed = self.provider.apply(
            coordinates,
            capability=capability,
            now=now,
            crash_before_effect=crash_at is CrashPoint.BEFORE_EFFECT,
        )
        if crash_at is CrashPoint.AFTER_EFFECT_BEFORE_RECEIPT:
            raise InjectedHandoffCrash(
                "injected crash after provider effect and before local receipt"
            )
        if not isinstance(capability, FixtureCapability):
            raise HandoffRejected("offline fixture capability required")
        return self.ledger.complete(
            coordinates,
            capability,
            result,
            provider_replayed=provider_replayed,
        )
