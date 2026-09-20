"""Concrete trusted composition for bounded Epic campaigns.

This module is the production bridge between the outbound DevHub command worker and
``CampaignCoordinator``.  Mutating tracker/code-host operations stay in the trusted
Foundry process and call the existing mechanical issue primitives.  The isolated
implementation process receives one issue worktree and edit tools only: no shell,
plugin, MCP server, Foundry configuration or Foundry credential.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

import foundry
from foundry import config, registry, write
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
    EscalationRetryAuthorizer,
    ImplementationProposal,
    ImplementationReconciliation,
    LegacyParentRequalification,
    ParentAcceptanceProof,
    ParentSnapshotAdvancement,
    ReviewRemediationTarget,
    RetryDecision,
    StepReceipt,
    _digest,
    _ordered_parent_acceptance_mapping,
    _verify_guarded_sqlite_descriptors,
    _sqlite_identity_window,
    _verify_new_sqlite_descriptor,
)
from foundry.command_worker import (
    MAX_RECEIPT_BYTES,
    RECEIPT_CONTRACT,
    CommandWorkerError,
    EffectReceipt,
    ExecutionAuthorization,
    ExecutionProfile,
    PreEffectCapacityError,
    RevalidationObservation,
    SnapshotAdvancementObservation,
    _binding_digest,
    _canonical,
    revalidate_command,
)
from foundry.devhub_commands import DevHubCommand
from foundry.escalation import EscalationStore
from foundry.execution_receipts import (
    ATTEMPT_ID_ENV,
    RECEIPT_DIRECTORY_ENV,
    ExecutionReceiptStore,
    ReceiptStoreError,
    epic_closure_receipt,
)
from foundry.routing import (
    LEVELS,
    AcceptanceProofStore,
    acceptance_criteria,
    acceptance_digest,
    synchronize_acceptance_body,
    RoutingConfigError,
    RoutingPolicy,
    git_diff,
    git_head,
    repository_identity,
)
from foundry.routing_facades import _load_claude_policy, claude_invocation_model
from foundry.trackers.devhub import DevHubTrackerError


MAX_PROVIDER_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_IMPLEMENTATION_SECONDS = 6 * 60 * 60
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MUTATING_STEPS = frozenset({"start", "open-pr", "merge", "close-epic", "sync-parent-acceptance"})
_GATE_STEPS = frozenset({"review", "ci", "human-gate"})
_CHILD_ENV_ALLOWLIST = (
    "HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "TMPDIR", "USER",
)


class AuthoritySource(Protocol):
    now_ms: Callable[[], int]

    def load(self, command: DevHubCommand): ...


@dataclass(frozen=True, kw_only=True)
class CampaignExecutionProfile(ExecutionProfile):
    """Campaign-wide reservation plus the distinct ceiling of one Claude call."""

    provider_invocation_ceiling_cents: int

    def __post_init__(self) -> None:
        super().__post_init__()
        if (type(self.provider_invocation_ceiling_cents) is not int
                or not 1 <= self.provider_invocation_ceiling_cents
                <= self.cost_ceiling_cents):
            raise ValueError("plafond d'invocation provider invalide")


@dataclass(frozen=True)
class HumanGateCoordinates:
    effect_id: str
    binding_digest: str
    issue_id: str
    step: str = "human-gate"


def _child_environment() -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in _CHILD_ENV_ALLOWLIST
        if name in os.environ
    }
    environment[config.RUNTIME_CONFIG_ISOLATION_ENV] = "1"
    return environment


def _validate_completed(completed: object, *, operation: str):
    returncode = getattr(completed, "returncode", None)
    stdout = getattr(completed, "stdout", None)
    stderr = getattr(completed, "stderr", "")
    if (type(returncode) is not int or not isinstance(stdout, str)
            or not isinstance(stderr, str)
            or len(stdout.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES
            or len(stderr.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES):
        raise OSError(f"résultat {operation} Foundry ambigu")
    if returncode != 0:
        raise RuntimeError(f"primitive Foundry {operation} refusée")
    return completed


class CampaignEffectStore:
    """Private exact receipts for trusted primitives and explicit human gates."""

    def __init__(
        self, path: str | os.PathLike, *,
        connection_guard: Callable[[], None] | None = None,
        connection_identity: tuple[int, int, int] | None = None,
        connection_sidecar_identities: tuple[tuple[int, int, int], ...] = (),
    ):
        if (connection_sidecar_identities and connection_identity is None):
            raise ValueError("sidecars SQLite sans identite primaire")
        if ((connection_identity is not None or connection_sidecar_identities)
                and connection_guard is None):
            raise ValueError("identite SQLite sans controle de chemin")
        candidate = Path(path).expanduser()
        if connection_guard is not None:
            connection_guard()
        self.path = candidate.resolve()
        if connection_guard is not None:
            connection_guard()
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
        self._connection_guard = connection_guard
        self._connection_identity = connection_identity
        self._connection_sidecar_identities = connection_sidecar_identities
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS primitive_receipts (
                  effect_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL,
                  campaign_id TEXT NOT NULL, issue_id TEXT NOT NULL,
                  attempt INTEGER NOT NULL, step TEXT NOT NULL,
                  status TEXT NOT NULL, proof_digest TEXT NOT NULL,
                  pr_number INTEGER, head_sha TEXT, base_sha TEXT,
                  updated_at INTEGER NOT NULL,
                  UNIQUE (campaign_id, issue_id, attempt, step)
                );
                CREATE TABLE IF NOT EXISTS human_approvals (
                  effect_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL,
                  issue_id TEXT NOT NULL, head_sha TEXT NOT NULL,
                  review_digest TEXT NOT NULL, ci_digest TEXT NOT NULL,
                  actor TEXT NOT NULL, expires_at INTEGER NOT NULL,
                  approval_digest TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS human_approval_events (
                  effect_id TEXT NOT NULL, approval_digest TEXT NOT NULL,
                  binding_digest TEXT NOT NULL, issue_id TEXT NOT NULL,
                  head_sha TEXT NOT NULL, review_digest TEXT NOT NULL,
                  ci_digest TEXT NOT NULL, actor TEXT NOT NULL,
                  expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
                  PRIMARY KEY (effect_id, approval_digest)
                );
                CREATE TABLE IF NOT EXISTS implementation_intents (
                  campaign_id TEXT NOT NULL, issue_id TEXT NOT NULL,
                  attempt INTEGER NOT NULL, effect_id TEXT NOT NULL,
                  work_id TEXT NOT NULL, binding_digest TEXT NOT NULL,
                  outcome TEXT NOT NULL, head_sha TEXT NOT NULL,
                  tree_sha TEXT NOT NULL, proof_digest TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY (campaign_id, issue_id, attempt),
                  UNIQUE (effect_id), UNIQUE (work_id)
                );
                CREATE TABLE IF NOT EXISTS parent_snapshot_advancements (
                  effect_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL UNIQUE,
                  binding_digest TEXT NOT NULL, advancement_digest TEXT NOT NULL,
                  advancement_json TEXT NOT NULL, status TEXT NOT NULL,
                  receipt_digest TEXT, created_at INTEGER NOT NULL,
                  completed_at INTEGER
                );
                """
            )
        if connection_guard is None:
            self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        with _sqlite_identity_window(
            self._connection_identity is not None
            or bool(self._connection_sidecar_identities),
        ) as descriptors:
            if self._connection_guard is not None:
                self._connection_guard()
            connection = sqlite3.connect(self.path, timeout=10.0)
            try:
                if self._connection_sidecar_identities:
                    # SQLite opens WAL/SHM lazily on the first schema access.  Keep
                    # that probe inside the same guarded descriptor window so no
                    # receipt query can precede sidecar provenance verification.
                    connection.execute("PRAGMA schema_version").fetchone()
                if self._connection_guard is not None:
                    self._connection_guard()
                if self._connection_sidecar_identities:
                    _verify_guarded_sqlite_descriptors(
                        descriptors,
                        (self._connection_identity, *self._connection_sidecar_identities),
                    )
                elif self._connection_identity is not None:
                    _verify_new_sqlite_descriptor(
                        descriptors, self._connection_identity,
                    )
            except sqlite3.DatabaseError as exc:
                connection.close()
                raise CampaignError("journaux connexion SQLite invalides") from exc
            except BaseException:
                connection.close()
                raise
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _coordinates(envelope: EffectEnvelope) -> tuple[object, ...]:
        return (
            envelope.effect_id, envelope.binding_digest, envelope.campaign_id,
            envelope.issue_id, envelope.attempt, envelope.step,
        )

    @staticmethod
    def _validate_parent_snapshot_binding(
        envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> None:
        if (envelope.step != "sync-parent-acceptance"
                or advancement.effect_id != envelope.effect_id
                or advancement.campaign_id != envelope.campaign_id
                or advancement.parent_id != envelope.issue_id
                or advancement.binding_digest != envelope.binding_digest
                or advancement.snapshot_digest != envelope.snapshot_digest):
            raise CampaignError("binding intent avancement parent modifié")

    @staticmethod
    def _parent_snapshot_row(
        row: tuple[object, ...], *, envelope: EffectEnvelope | None = None,
        campaign_id: str | None = None,
    ) -> tuple[ParentSnapshotAdvancement, str, str | None]:
        (
            effect_id, stored_campaign_id, binding_digest, advancement_digest,
            advancement_json, status, receipt_digest,
        ) = row
        try:
            raw = json.loads(advancement_json)
            raw["criteria"] = tuple(tuple(item) for item in raw["criteria"])
            advancement = ParentSnapshotAdvancement(**raw)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            raise CampaignError("intent primitive avancement parent corrompu") from None
        if (advancement.effect_id != effect_id
                or advancement.campaign_id != stored_campaign_id
                or advancement.binding_digest != binding_digest
                or advancement.advancement_digest != advancement_digest):
            raise CampaignError("intent primitive avancement parent contradictoire")
        if status == "intent":
            if receipt_digest is not None:
                raise CampaignError("reçu primitive avancement parent contradictoire")
        elif status == "completed":
            if (not isinstance(receipt_digest, str)
                    or _DIGEST.fullmatch(receipt_digest) is None):
                raise CampaignError("reçu primitive avancement parent contradictoire")
        else:
            raise CampaignError("état primitive avancement parent invalide")
        if campaign_id is not None and stored_campaign_id != campaign_id:
            raise CampaignError("intent primitive avancement parent contradictoire")
        if envelope is not None:
            CampaignEffectStore._validate_parent_snapshot_binding(
                envelope, advancement,
            )
        return advancement, status, receipt_digest

    def get(self, envelope: EffectEnvelope) -> StepReceipt | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT binding_digest, campaign_id, issue_id, attempt, step, "
                "status, proof_digest FROM primitive_receipts WHERE effect_id = ?",
                (envelope.effect_id,),
            ).fetchone()
        if row is None:
            return None
        expected = (
            envelope.binding_digest, envelope.campaign_id, envelope.issue_id,
            envelope.attempt, envelope.step,
        )
        if row[:5] != expected:
            raise CampaignError("binding de reçu primitive modifié")
        return StepReceipt(envelope.effect_id, row[5], row[6])

    def coordinates(
        self, campaign_id: str, issue_id: str, attempt: int, step: str,
    ) -> tuple[StepReceipt, int | None, str | None, str | None] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT effect_id, status, proof_digest, pr_number, head_sha, base_sha "
                "FROM primitive_receipts WHERE campaign_id = ? AND issue_id = ? "
                "AND attempt = ? AND step = ?",
                (campaign_id, issue_id, attempt, step),
            ).fetchone()
        if row is None:
            return None
        return StepReceipt(row[0], row[1], row[2]), row[3], row[4], row[5]

    def verify_parent_acceptance_proof(self, proof: ParentAcceptanceProof) -> None:
        """Corroborate every parent-proof child with the primitive merge journal."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT effect_id, binding_digest, campaign_id, issue_id, attempt, "
                "step, status, proof_digest, pr_number, head_sha, base_sha, updated_at "
                "FROM primitive_receipts "
                "WHERE campaign_id = ? AND step = 'merge' ORDER BY issue_id, attempt",
                (proof.campaign_id,),
            ).fetchall()
        expected = {
            issue_id: (effect_id, proof_digest)
            for issue_id, effect_id, proof_digest in proof.children
        }
        if (len(rows) != len(expected)
                or tuple(row[3] for row in rows) != tuple(sorted(expected))):
            raise CampaignError("reçus primitive merge parent incomplets")
        for row in rows:
            (
                effect_id, binding_digest, campaign_id, issue_id, attempt,
                step, status, proof_digest, pr_number, head_sha, base_sha, updated_at,
            ) = row
            if (type(attempt) is not int or attempt < 1
                    or type(pr_number) is not int or pr_number < 1
                    or not isinstance(head_sha, str) or _SHA.fullmatch(head_sha) is None
                    or not isinstance(base_sha, str) or _SHA.fullmatch(base_sha) is None
                    or type(updated_at) is not int or updated_at < 0):
                raise CampaignError("reçu primitive merge parent contradictoire")
            material = _digest([
                proof.binding_digest, issue_id, attempt, "merge",
            ])
            receipt_proofs = {
                _digest([effect_id, pr_number, head_sha]),
                _digest([effect_id, pr_number, head_sha, "reconciled"]),
            }
            if ((effect_id, proof_digest) != expected.get(issue_id)
                    or effect_id != f"effect-{material[:32]}"
                    or binding_digest != proof.binding_digest
                    or campaign_id != proof.campaign_id
                    or step != "merge" or status != "completed"
                    or proof_digest not in receipt_proofs):
                raise CampaignError("reçu primitive merge parent contradictoire")

    def begin_parent_snapshot_advancement(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement, *, now_ms: int,
    ) -> None:
        self._validate_parent_snapshot_binding(envelope, advancement)
        encoded = _canonical(asdict(advancement)).decode("utf-8")
        exact = (
            envelope.effect_id, envelope.campaign_id, envelope.binding_digest,
            advancement.advancement_digest, encoded,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT effect_id, campaign_id, binding_digest, advancement_digest, "
                "advancement_json, status, receipt_digest "
                "FROM parent_snapshot_advancements "
                "WHERE effect_id = ?", (envelope.effect_id,),
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO parent_snapshot_advancements VALUES "
                    "(?, ?, ?, ?, ?, 'intent', NULL, ?, NULL)",
                    (*exact, now_ms),
                )
            else:
                stored, _status, _receipt_digest = self._parent_snapshot_row(
                    current, envelope=envelope,
                )
                if current[:5] != exact or stored != advancement:
                    connection.rollback()
                    raise CampaignError("intent primitive avancement parent modifié")
            connection.commit()

    def parent_snapshot_advancement(
        self, envelope: EffectEnvelope,
    ) -> tuple[ParentSnapshotAdvancement, str, str | None] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT effect_id, campaign_id, binding_digest, advancement_digest, "
                "advancement_json, status, receipt_digest "
                "FROM parent_snapshot_advancements WHERE effect_id = ?",
                (envelope.effect_id,),
            ).fetchone()
        if row is None:
            return None
        return self._parent_snapshot_row(row, envelope=envelope)

    def command_parent_snapshot_advancement(
        self, campaign_id: str,
    ) -> tuple[ParentSnapshotAdvancement, str, str | None] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT effect_id, campaign_id, binding_digest, advancement_digest, "
                "advancement_json, status, receipt_digest "
                "FROM parent_snapshot_advancements WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        if row is None:
            return None
        return self._parent_snapshot_row(row, campaign_id=campaign_id)

    def finish_parent_snapshot_advancement(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
        receipt: StepReceipt, *, now_ms: int,
    ) -> StepReceipt:
        self._validate_parent_snapshot_binding(envelope, advancement)
        if receipt.effect_id != envelope.effect_id or receipt.status != "completed":
            raise CampaignError("reçu primitive avancement parent non terminal")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT effect_id, campaign_id, binding_digest, advancement_digest, "
                "advancement_json, status, receipt_digest "
                "FROM parent_snapshot_advancements WHERE effect_id = ?",
                (envelope.effect_id,),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise CampaignError("intent primitive avancement parent absent")
            stored, status, receipt_digest = self._parent_snapshot_row(
                current, envelope=envelope,
            )
            if stored != advancement:
                connection.rollback()
                raise CampaignError("intent primitive avancement parent absent")
            if status == "completed":
                if receipt_digest != receipt.proof_digest:
                    connection.rollback()
                    raise CampaignError("reçu primitive avancement parent modifié")
                connection.commit()
                return receipt
            updated = connection.execute(
                "UPDATE parent_snapshot_advancements SET status = 'completed', "
                "receipt_digest = ?, completed_at = ? WHERE effect_id = ? "
                "AND status = 'intent' AND receipt_digest IS NULL",
                (receipt.proof_digest, now_ms, envelope.effect_id),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise CampaignError("transition primitive avancement parent perdue")
            connection.commit()
        return receipt

    def record(
        self, envelope: EffectEnvelope, receipt: StepReceipt, *,
        pr_number: int | None = None, head_sha: str | None = None,
        base_sha: str | None = None, now_ms: int,
    ) -> StepReceipt:
        if receipt.effect_id != envelope.effect_id:
            raise CampaignError("identité de reçu primitive contradictoire")
        if pr_number is not None and (type(pr_number) is not int or pr_number < 1):
            raise CampaignError("numéro de PR primitive invalide")
        for value in (head_sha, base_sha):
            if value is not None and _SHA.fullmatch(value) is None:
                raise CampaignError("SHA de reçu primitive invalide")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT binding_digest, campaign_id, issue_id, attempt, step, "
                "status, proof_digest, pr_number, head_sha, base_sha "
                "FROM primitive_receipts WHERE effect_id = ?",
                (envelope.effect_id,),
            ).fetchone()
            exact = (
                envelope.binding_digest, envelope.campaign_id, envelope.issue_id,
                envelope.attempt, envelope.step, receipt.status, receipt.proof_digest,
                pr_number, head_sha, base_sha,
            )
            if current is not None and current[:5] != exact[:5]:
                connection.rollback()
                raise CampaignError("binding de reçu primitive modifié")
            if current is not None and current[5] == "completed" and current != exact:
                connection.rollback()
                raise CampaignError("reçu primitive terminal modifié")
            connection.execute(
                "INSERT INTO primitive_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(effect_id) DO UPDATE SET status = excluded.status, "
                "proof_digest = excluded.proof_digest, pr_number = excluded.pr_number, "
                "head_sha = excluded.head_sha, base_sha = excluded.base_sha, "
                "updated_at = excluded.updated_at",
                (*self._coordinates(envelope), receipt.status, receipt.proof_digest,
                 pr_number, head_sha, base_sha, now_ms),
            )
            connection.commit()
        return receipt

    def record_implementation_intent(
        self, envelope: EffectEnvelope, *, outcome: str,
        head_sha: str, tree_sha: str, now_ms: int,
    ) -> str:
        """Append one exact content-free binding for the agent-produced tree."""
        if (envelope.step != "implementation" or outcome not in {
            "completed", "blocked", "failed", "host-unavailable", "unknown",
        } or _SHA.fullmatch(head_sha) is None or _SHA.fullmatch(tree_sha) is None
                or type(now_ms) is not int or now_ms < 0):
            raise CampaignError("intent d'implémentation invalide")
        material = {
            "effect_id": envelope.effect_id,
            "work_id": envelope.work_id,
            "binding_digest": envelope.binding_digest,
            "campaign_id": envelope.campaign_id,
            "issue_id": envelope.issue_id,
            "attempt": envelope.attempt,
            "outcome": outcome,
            "head_sha": head_sha,
            "tree_sha": tree_sha,
        }
        proof_digest = _digest(material)
        exact = (
            envelope.campaign_id, envelope.issue_id, envelope.attempt,
            envelope.effect_id, envelope.work_id, envelope.binding_digest,
            outcome, head_sha, tree_sha, proof_digest,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT campaign_id, issue_id, attempt, effect_id, work_id, "
                "binding_digest, outcome, head_sha, tree_sha, proof_digest "
                "FROM implementation_intents WHERE campaign_id = ? "
                "AND issue_id = ? AND attempt = ?",
                (envelope.campaign_id, envelope.issue_id, envelope.attempt),
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO implementation_intents VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*exact, now_ms),
                )
            elif current != exact:
                connection.rollback()
                raise CampaignError("intent d'implémentation durable modifié")
            connection.commit()
        return proof_digest

    def completed_implementation_intent(
        self, envelope: EffectEnvelope,
    ) -> tuple[str, str]:
        """Resolve the proposal proof that alone may be materialized as a commit."""
        if envelope.step != "open-pr":
            raise CampaignError("preuve d'implémentation absente avant commit")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT effect_id, work_id, binding_digest, outcome, head_sha, "
                "tree_sha, proof_digest FROM implementation_intents "
                "WHERE campaign_id = ? AND issue_id = ? AND attempt = ?",
                (envelope.campaign_id, envelope.issue_id, envelope.attempt),
            ).fetchone()
        if row is None:
            raise CampaignError("intent d'implémentation durable absent")
        effect_id, work_id, binding_digest, outcome, head_sha, tree_sha, stored = row
        material = {
            "effect_id": effect_id,
            "work_id": work_id,
            "binding_digest": binding_digest,
            "campaign_id": envelope.campaign_id,
            "issue_id": envelope.issue_id,
            "attempt": envelope.attempt,
            "outcome": outcome,
            "head_sha": head_sha,
            "tree_sha": tree_sha,
        }
        if (binding_digest != envelope.binding_digest or outcome != "completed"
                or _SHA.fullmatch(head_sha or "") is None
                or _SHA.fullmatch(tree_sha or "") is None
                or _digest(material) != stored):
            raise CampaignError("binding d'implémentation durable invalide")
        return head_sha, tree_sha

    def approve_human_gate(
        self, envelope: EffectEnvelope | HumanGateCoordinates, *,
        head_sha: str, review_digest: str,
        ci_digest: str, actor: str, expires_at: int, now_ms: int,
    ) -> str:
        """Record an explicit external human test; never called automatically."""
        if envelope.step != "human-gate" or _SHA.fullmatch(head_sha) is None:
            raise CampaignError("coordonnées de gate humain invalides")
        if (not isinstance(actor, str) or _IDENTIFIER.fullmatch(actor) is None
                or type(expires_at) is not int or expires_at <= now_ms):
            raise CampaignError("approbation humaine invalide")
        for value in (review_digest, ci_digest):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise CampaignError("preuve de gate humain invalide")
        material = {
            "effect_id": envelope.effect_id,
            "binding_digest": envelope.binding_digest,
            "issue_id": envelope.issue_id,
            "head_sha": head_sha,
            "review_digest": review_digest,
            "ci_digest": ci_digest,
            "actor": actor,
            "expires_at": expires_at,
        }
        approval_digest = _digest(material)
        values = (*material.values(), approval_digest, now_ms)
        event_values = (
            envelope.effect_id, approval_digest, envelope.binding_digest,
            envelope.issue_id, head_sha, review_digest, ci_digest, actor,
            expires_at, now_ms,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT effect_id, binding_digest, issue_id, head_sha, review_digest, "
                "ci_digest, actor, expires_at, approval_digest, created_at "
                "FROM human_approvals WHERE effect_id = ?", (envelope.effect_id,),
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO human_approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
            elif current[:9] == values[:9]:
                connection.commit()
                return approval_digest
            elif current[1:6] != values[1:6] or expires_at <= current[7]:
                connection.rollback()
                raise CampaignError("approbation humaine rejouée avec un autre binding")
            else:
                # A human explicitly renewed the exact same gate after its former
                # receipt expired.  Preserve the immutable prior approval event,
                # then advance only the current, longer-lived receipt.  No runner
                # may create this event: it is reachable solely through the human
                # gate command above.
                connection.execute(
                    "INSERT OR IGNORE INTO human_approval_events "
                    "(effect_id, approval_digest, binding_digest, issue_id, head_sha, "
                    "review_digest, ci_digest, actor, expires_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        current[0], current[8], current[1], current[2], current[3],
                        current[4], current[5], current[6], current[7], current[9],
                    ),
                )
                connection.execute(
                    "UPDATE human_approvals SET actor = ?, expires_at = ?, "
                    "approval_digest = ?, created_at = ? WHERE effect_id = ?",
                    (actor, expires_at, approval_digest, now_ms, envelope.effect_id),
                )
            connection.execute(
                "INSERT OR IGNORE INTO human_approval_events "
                "(effect_id, approval_digest, binding_digest, issue_id, head_sha, "
                "review_digest, ci_digest, actor, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                event_values,
            )
            connection.commit()
        return approval_digest

    def approve_pending_human_gate(
        self, campaign_id: str, issue_id: str, attempt: int, *, actor: str,
        expires_at: int, now_ms: int,
    ) -> str:
        """Bind an explicit operator approval to the currently waiting exact gates."""
        if (_IDENTIFIER.fullmatch(campaign_id) is None
                or re.fullmatch(r"[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*", issue_id) is None
                or type(attempt) is not int or attempt < 1):
            raise CampaignError("coordonnées d'approbation humaine invalides")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT step, effect_id, binding_digest, status, proof_digest, head_sha "
                "FROM primitive_receipts WHERE campaign_id = ? AND issue_id = ? "
                "AND attempt = ? AND step IN ('review', 'ci', 'human-gate')",
                (campaign_id, issue_id, attempt),
            ).fetchall()
        by_step = {row[0]: row[1:] for row in rows}
        if set(by_step) != {"review", "ci", "human-gate"}:
            raise CampaignError("gate humain ou preuves préalables absents")
        review, ci, human = (
            by_step["review"], by_step["ci"], by_step["human-gate"],
        )
        if (review[2] != "completed" or ci[2] != "completed"
                or human[2] not in {"waiting", "completed"}
                or not isinstance(human[4], str)
                or human[4] != review[4] or human[4] != ci[4]):
            raise CampaignError("preuves exactes du gate humain non validées")
        coordinates = HumanGateCoordinates(
            effect_id=human[0], binding_digest=human[1], issue_id=issue_id,
        )
        return self.approve_human_gate(
            coordinates, head_sha=human[4], review_digest=review[3],
            ci_digest=ci[3], actor=actor, expires_at=expires_at, now_ms=now_ms,
        )

    def human_approval(
        self, envelope: EffectEnvelope, *, head_sha: str, review_digest: str,
        ci_digest: str, now_ms: int,
    ) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT binding_digest, issue_id, head_sha, review_digest, ci_digest, "
                "expires_at, approval_digest FROM human_approvals WHERE effect_id = ?",
                (envelope.effect_id,),
            ).fetchone()
        if row is None:
            return None
        if row[:5] != (
            envelope.binding_digest, envelope.issue_id, head_sha, review_digest, ci_digest,
        ):
            raise CampaignError("binding de gate humain modifié")
        if row[5] <= now_ms:
            return None
        return row[6]


class FoundryPrimitiveRunner:
    """Exact adapter over issue.start/openpr/merge/close_epic and existing gates."""

    def __init__(
        self, root: str | os.PathLike, *, worktree_directory: str | os.PathLike,
        receipts: CampaignEffectStore, runner: Callable[..., object] = subprocess.run,
        now_ms: Callable[[], int] | None = None,
        execution_receipts: ExecutionReceiptStore | None = None,
        attempt_id: str | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("racine de campagne Foundry absente")
        self.worktree_directory = Path(worktree_directory).expanduser().resolve()
        self.worktree_directory.mkdir(parents=True, exist_ok=True)
        self.worktree_directory.chmod(0o700)
        self.receipts = receipts
        self.runner = runner
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        if attempt_id is not None and _IDENTIFIER.fullmatch(attempt_id) is None:
            raise ValueError("identité de tentative campagne invalide")
        self.execution_receipts = execution_receipts
        self.attempt_id = attempt_id
        self.tooling_root = Path(__file__).resolve().parents[1]
        self._worktree_lock = threading.RLock()

    def _run(self, argv: list[str], *, cwd: Path, operation: str, env=None):
        try:
            completed = self.runner(
                argv, cwd=cwd, capture_output=True, text=True,
                timeout=MAX_IMPLEMENTATION_SECONDS, check=False, env=env,
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"primitive Foundry {operation} expirée") from None
        return _validate_completed(completed, operation=operation)

    def _git(
        self, *args: str, cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        completed = self._run(
            ["git", *args], cwd=cwd or self.root, operation="git", env=env,
        )
        return completed.stdout.strip()

    def _intended_tree(self, worktree: Path) -> str:
        """Compute exactly what ``git add --all`` would commit, without staging it."""
        with tempfile.TemporaryDirectory(prefix="foundry-intended-index-") as directory:
            Path(directory).chmod(0o700)
            environment = dict(os.environ)
            environment["GIT_INDEX_FILE"] = str(Path(directory) / "index")
            self._git("read-tree", "HEAD", cwd=worktree, env=environment)
            self._git("add", "--all", cwd=worktree, env=environment)
            tree_sha = self._git("write-tree", cwd=worktree, env=environment)
        if _SHA.fullmatch(tree_sha) is None:
            raise CampaignError("arbre Git d'implémentation ambigu")
        return tree_sha

    def worktree(self, campaign_id: str, issue_id: str) -> Path:
        if (_IDENTIFIER.fullmatch(campaign_id) is None
                or re.fullmatch(r"[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*", issue_id) is None):
            raise CampaignError("identité de worktree campagne invalide")
        path = self.worktree_directory / campaign_id / issue_id
        with self._worktree_lock:
            if path.exists():
                if self._git("rev-parse", "--is-inside-work-tree", cwd=path) != "true":
                    raise CampaignError("worktree campagne existant invalide")
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            default = registry.default_branch(str(self.root)) or "main"
            remote = f"origin/{default}"
            verify = self.runner(
                ["git", "rev-parse", "--verify", remote], cwd=self.root,
                capture_output=True, text=True, timeout=60, check=False,
            )
            base = remote if getattr(verify, "returncode", 1) == 0 else default
            self._git("worktree", "add", "--detach", str(path), base)
        return path

    def _issue_command(
        self, envelope: EffectEnvelope, action: str, *extra: str,
        before_dispatch: Callable[[], None] | None = None,
    ) -> object:
        cwd = (
            self.root if action == "close-epic"
            else self.worktree(envelope.campaign_id, envelope.issue_id)
        )
        environment = dict(os.environ)
        # A campaign command owns its F91 binding.  Never inherit an unrelated
        # interactive/runtime attempt from the worker process.
        environment.pop(ATTEMPT_ID_ENV, None)
        environment.pop(RECEIPT_DIRECTORY_ENV, None)
        if self.execution_receipts is not None and self.attempt_id is not None:
            environment[ATTEMPT_ID_ENV] = self.attempt_id
            environment[RECEIPT_DIRECTORY_ENV] = str(
                self.execution_receipts.directory
            )
        current = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            str(self.tooling_root) if not current else f"{self.tooling_root}{os.pathsep}{current}"
        )
        if before_dispatch is not None:
            before_dispatch()
        return self._run(
            [sys.executable, "-m", "foundry.issue", action, envelope.issue_id, *extra],
            cwd=cwd, operation=action, env=environment,
        )

    def _codehost_coordinates(self, envelope: EffectEnvelope):
        worktree = self.worktree(envelope.campaign_id, envelope.issue_id)
        branch = self._git("branch", "--show-current", cwd=worktree)
        if not branch:
            raise CampaignError("branche d'issue absente")
        codehost = foundry.codehost(cwd=str(self.root))
        repo = codehost.resolve_repo(str(self.root))
        candidates = codehost.list_prs(repo, branch)
        usable = [item for item in candidates if item.state == "open" or item.merged]
        if len(usable) != 1:
            raise CampaignError("PR d'issue absente ou ambiguë")
        pr = codehost.get_pr(repo, usable[0].number)
        if (pr.head != branch or not isinstance(pr.sha, str)
                or _SHA.fullmatch(pr.sha) is None or not isinstance(pr.base_sha, str)
                or _SHA.fullmatch(pr.base_sha) is None):
            raise CampaignError("coordonnées de PR invalides")
        return worktree, codehost, repo, pr

    def observe_blockers(
        self, spec: CampaignSpec,
    ) -> tuple[BlockerObservation, ...]:
        """Read every immutable preview blocker afresh from the active tracker."""
        tracker = foundry.tracker()
        observations = []
        for blocker_id in spec.blockers:
            try:
                issue = tracker.get_issue(blocker_id)
            except Exception:
                observations.append(BlockerObservation(blocker_id, "unknown", None, None))
                continue
            state = getattr(issue, "state", None)
            if isinstance(state, str):
                state = state.casefold()
            if (getattr(issue, "id", None) != blocker_id
                    or state not in {
                        "backlog", "ready", "in-progress", "review", "blocked",
                        "done", "dropped",
                    }):
                observations.append(BlockerObservation(blocker_id, "unknown", None, None))
                continue
            updated_at = getattr(issue, "updated", None)
            version = getattr(issue, "version", None)
            if updated_at is not None and (type(updated_at) is not int or updated_at < 0):
                updated_at = None
            if version is not None and (type(version) is not int or version < 0):
                version = None
            observations.append(
                BlockerObservation(blocker_id, state, updated_at, version),
            )
        return tuple(observations)

    @staticmethod
    def _receipt(envelope: EffectEnvelope, status: str, material: object) -> StepReceipt:
        return StepReceipt(envelope.effect_id, status, _digest(material))

    def start_issue(self, envelope: EffectEnvelope) -> StepReceipt:
        # ``issue start`` changes both the tracker state and a local Git branch.
        # Children have distinct worktrees, but their startup primitive still shares
        # the repository-level Foundry configuration and Git refs.  A wave may run
        # implementation concurrently only after each child has this durable start
        # receipt, so serialize this narrow transition rather than the whole wave.
        with self._worktree_lock:
            self._issue_command(envelope, "start")
            worktree = self.worktree(envelope.campaign_id, envelope.issue_id)
            branch = self._git("branch", "--show-current", cwd=worktree)
        receipt = self._receipt(envelope, "completed", [envelope.effect_id, branch])
        return self.receipts.record(envelope, receipt, now_ms=self.now_ms())

    def capture_implementation_intent(
        self, envelope: EffectEnvelope, *, outcome: str,
    ) -> str:
        """Bind the provider result to the exact tracked and untracked Git tree."""
        worktree = self.worktree(envelope.campaign_id, envelope.issue_id)
        with self._worktree_lock:
            head_sha = self._git("rev-parse", "HEAD", cwd=worktree)
            if _SHA.fullmatch(head_sha) is None:
                raise CampaignError("HEAD d'implémentation ambigu")
            tree_sha = self._intended_tree(worktree)
            return self.receipts.record_implementation_intent(
                envelope, outcome=outcome, head_sha=head_sha, tree_sha=tree_sha,
                now_ms=self.now_ms(),
            )

    def _commit_implementation(self, envelope: EffectEnvelope) -> None:
        """Materialize the edit-only proposal without giving the child a shell."""
        worktree = self.worktree(envelope.campaign_id, envelope.issue_id)
        message = f"[{envelope.issue_id}] implementation de campagne bornee"
        with self._worktree_lock:
            branch = self._git("branch", "--show-current", cwd=worktree)
            if not branch or envelope.issue_id.lower() not in branch.lower():
                raise CampaignError("branche d'implémentation hors issue")
            expected_head, expected_tree = self.receipts.completed_implementation_intent(
                envelope,
            )
            current_head = self._git("rev-parse", "HEAD", cwd=worktree)
            status = self._git(
                "status", "--porcelain", "--untracked-files=all", cwd=worktree,
            )
            if current_head == expected_head:
                if self._intended_tree(worktree) != expected_tree:
                    raise CampaignError("code d'implémentation modifié avant commit")
                if status:
                    self._git("add", "--all", cwd=worktree)
                    if self._git("write-tree", cwd=worktree) != expected_tree:
                        raise CampaignError("index d'implémentation différent de l'intent")
                    if self._git("rev-parse", "HEAD", cwd=worktree) != expected_head:
                        raise CampaignError("HEAD d'implémentation modifié avant commit")
                    self._git("commit", "-m", message, cwd=worktree)
                    current_head = self._git("rev-parse", "HEAD", cwd=worktree)
            else:
                # Resolve-first recovery after a crash between the exact commit and
                # the open-PR receipt. Only that one already-materialized commit is safe.
                if (status
                        or self._git("rev-parse", "HEAD^", cwd=worktree) != expected_head
                        or self._git("rev-parse", "HEAD^{tree}", cwd=worktree) != expected_tree
                        or self._git("show", "-s", "--format=%s", "HEAD", cwd=worktree)
                        != message):
                    raise CampaignError("commit d'implémentation non lié à l'intent")
            if (self._git("rev-parse", "HEAD^", cwd=worktree) != expected_head
                    or self._git("rev-parse", "HEAD^{tree}", cwd=worktree)
                    != expected_tree
                    or self._git("show", "-s", "--format=%s", "HEAD", cwd=worktree)
                    != message):
                raise CampaignError("commit d'implémentation différent de l'intent")
            if self._git(
                "status", "--porcelain", "--untracked-files=all", cwd=worktree,
            ):
                raise CampaignError("worktree d'implémentation encore modifié après commit")
        default = registry.default_branch(str(self.root)) or "main"
        remote = f"origin/{default}"
        verify = self.runner(
            ["git", "rev-parse", "--verify", remote], cwd=worktree,
            capture_output=True, text=True, timeout=60, check=False,
        )
        base = remote if getattr(verify, "returncode", 1) == 0 else default
        ahead = self._git("rev-list", "--count", f"{base}..HEAD", cwd=worktree)
        if not ahead.isdigit() or int(ahead) < 1:
            raise CampaignError("proposition d'implémentation sans commit d'issue")

    def open_pr(self, envelope: EffectEnvelope) -> StepReceipt:
        self._commit_implementation(envelope)
        self._issue_command(envelope, "openpr")
        _worktree, _codehost, _repo, pr = self._codehost_coordinates(envelope)
        receipt = self._receipt(
            envelope, "completed",
            [envelope.effect_id, pr.number, pr.url, pr.sha, pr.base_sha],
        )
        return self.receipts.record(
            envelope, receipt, pr_number=pr.number, head_sha=pr.sha,
            base_sha=pr.base_sha, now_ms=self.now_ms(),
        )

    def _review_observation(
        self, envelope: EffectEnvelope | ReviewRemediationTarget,
    ) -> tuple[StepReceipt, object, bool]:
        """Read the exact current PR and structured review proof without writing."""
        worktree, _codehost, _repo, pr = self._codehost_coordinates(envelope)
        if git_head(worktree) != pr.sha:
            return (
                self._receipt(envelope, "unknown", [envelope.effect_id, "head-drift"]),
                pr,
                False,
            )
        issue = foundry.tracker().get_issue(envelope.issue_id)
        proof_store = AcceptanceProofStore(repository_identity(self.root))
        coordinates = {
            "issue_id": issue.id,
            "issue_body": issue.body or "",
            "head": pr.sha,
            "diff": git_diff(worktree, pr.base_sha),
            "base": pr.base_sha,
        }
        try:
            proof = proof_store.valid_for_merge(**coordinates)
        except RoutingConfigError:
            try:
                proof = proof_store.current_for_sync(**coordinates)
            except RoutingConfigError:
                receipt = self._receipt(envelope, "waiting", [
                    envelope.effect_id, pr.sha, pr.base_sha, "review-proof-missing",
                ])
            else:
                receipt = self._receipt(envelope, "blocked", [
                    envelope.effect_id, proof["proof_id"], "ac-proof-nonpass",
                ])
        else:
            receipt = self._receipt(envelope, "completed", [
                envelope.effect_id, proof["proof_id"], "all-ac-pass",
            ])
        return receipt, pr, True

    def review(self, envelope: EffectEnvelope) -> StepReceipt:
        receipt, pr, recordable = self._review_observation(envelope)
        if not recordable:
            return receipt
        return self.receipts.record(
            envelope, receipt, pr_number=pr.number, head_sha=pr.sha,
            base_sha=pr.base_sha, now_ms=self.now_ms(),
        )

    def revalidate_review_remediation(
        self, target: ReviewRemediationTarget,
    ) -> ReviewRemediationTarget:
        """Corroborate one blocked review against fresh PR and proof authority."""
        if not isinstance(target, ReviewRemediationTarget):
            raise CampaignRevalidationError("review_remediation_target_invalid")
        try:
            primitive = self.receipts.get(target)
            stored = self.receipts.coordinates(
                target.campaign_id, target.issue_id, target.attempt, "review",
            )
            if (primitive is None or stored is None
                    or primitive != StepReceipt(
                        target.effect_id, "blocked", target.proof_digest,
                    )
                    or stored[0] != primitive
                    or type(stored[1]) is not int or stored[1] < 1
                    or not isinstance(stored[2], str)
                    or _SHA.fullmatch(stored[2]) is None
                    or not isinstance(stored[3], str)
                    or _SHA.fullmatch(stored[3]) is None):
                raise CampaignRevalidationError(
                    "review_remediation_primitive_receipt_mismatch"
                )
            observations = []
            for _read in range(2):
                observed, pr, recordable = self._review_observation(target)
                current = (observed, pr.number, pr.sha, pr.base_sha)
                if (not recordable or observed.status != "blocked"
                        or getattr(pr, "state", None) != "open"
                        or bool(getattr(pr, "merged", False))):
                    raise CampaignRevalidationError(
                        "review_remediation_authority_drift"
                    )
                observations.append(current)
            if observations[0] != observations[1]:
                raise CampaignRevalidationError(
                    "review_remediation_authority_changed_during_read"
                )
            if observations[1] != stored:
                raise CampaignRevalidationError(
                    "review_remediation_authority_drift"
                )
        except CampaignRevalidationError:
            raise
        except Exception:
            raise CampaignRevalidationError(
                "review_remediation_authority_ambiguous"
            ) from None
        return target

    def ci_gate(self, envelope: EffectEnvelope) -> StepReceipt:
        _worktree, codehost, repo, pr = self._codehost_coordinates(envelope)
        verdict = write.ci_gate(codehost, repo, pr.sha, allow_no_ci=False)
        if verdict["passed"]:
            status = "completed"
        elif verdict["pending"] or verdict["total"] == 0:
            status = "waiting"
        else:
            status = "blocked"
        material = (
            {"effect_id": envelope.effect_id, "head": pr.sha, "status": "waiting"}
            if status == "waiting" else {
                "effect_id": envelope.effect_id,
                "head": pr.sha,
                "passed": verdict["passed"],
                "pending": sorted(verdict["pending"]),
                "failing": sorted(verdict["failing"]),
                "total": verdict["total"],
                "waived": verdict["waived"],
            }
        )
        receipt = self._receipt(envelope, status, material)
        return self.receipts.record(
            envelope, receipt, pr_number=pr.number, head_sha=pr.sha,
            base_sha=pr.base_sha, now_ms=self.now_ms(),
        )

    def human_gate(self, envelope: EffectEnvelope) -> StepReceipt:
        _worktree, _codehost, _repo, pr = self._codehost_coordinates(envelope)
        review = self.receipts.coordinates(
            envelope.campaign_id, envelope.issue_id, envelope.attempt, "review",
        )
        ci = self.receipts.coordinates(
            envelope.campaign_id, envelope.issue_id, envelope.attempt, "ci",
        )
        if (review is None or ci is None
                or review[0].status != "completed" or ci[0].status != "completed"):
            raise CampaignError("gate humain sans preuves review et CI")
        approval = self.receipts.human_approval(
            envelope, head_sha=pr.sha, review_digest=review[0].proof_digest,
            ci_digest=ci[0].proof_digest, now_ms=self.now_ms(),
        )
        status = "completed" if approval is not None else "waiting"
        receipt = self._receipt(
            envelope, status,
            [envelope.effect_id, pr.sha, approval or "human-approval-missing"],
        )
        return self.receipts.record(
            envelope, receipt, pr_number=pr.number, head_sha=pr.sha,
            base_sha=pr.base_sha, now_ms=self.now_ms(),
        )

    def merge_issue(
        self, envelope: EffectEnvelope, *,
        engage: Callable[[], None] | None = None,
    ) -> StepReceipt | None:
        _worktree, _codehost, _repo, pr = self._codehost_coordinates(envelope)
        if getattr(pr, "merged", False):
            # Reconciliation after a crash must observe the already-merged exact
            # PR before asking whether a now-expired approval can authorize a new
            # dispatch.  ``engage is None`` is the coordinator's durable proof
            # that this is recovery of an earlier irreversible boundary, not a
            # convenient fast-path for an independently merged PR.
            if engage is not None:
                raise CampaignError("merge observé sans engagement durable")
            receipt = self._receipt(
                envelope, "completed", [envelope.effect_id, pr.number, pr.sha, "reconciled"],
            )
            return self.receipts.record(
                envelope, receipt, pr_number=pr.number, head_sha=pr.sha,
                base_sha=pr.base_sha, now_ms=self.now_ms(),
            )
        if engage is None:
            # A recovery call has a durable dispatch marker but no receipt yet.
            # The open PR proves neither success nor safety to issue a second
            # merge.  Return an explicit absence for the coordinator to suspend
            # and await a later read of this exact identity.
            return None
        human = self.receipts.coordinates(
            envelope.campaign_id, envelope.issue_id, envelope.attempt, "human-gate",
        )
        if human is None or human[0].status != "completed":
            raise CampaignRevalidationError("human_gate_missing")
        review = self.receipts.coordinates(
            envelope.campaign_id, envelope.issue_id, envelope.attempt, "review",
        )
        ci = self.receipts.coordinates(
            envelope.campaign_id, envelope.issue_id, envelope.attempt, "ci",
        )
        if (review is None or ci is None
                or review[0].status != "completed" or ci[0].status != "completed"):
            raise CampaignRevalidationError("merge_gate_proof_missing")
        human_envelope = replace(
            envelope, step="human-gate", effect_id=human[0].effect_id,
        )
        if self.receipts.human_approval(
            human_envelope, head_sha=pr.sha,
            review_digest=review[0].proof_digest, ci_digest=ci[0].proof_digest,
            now_ms=self.now_ms(),
        ) is None:
            raise CampaignRevalidationError("human_gate_expired")
        self._issue_command(
            envelope, "merge", str(pr.number), before_dispatch=engage,
        )
        receipt = self._receipt(
            envelope, "completed", [envelope.effect_id, pr.number, pr.sha],
        )
        return self.receipts.record(
            envelope, receipt, pr_number=pr.number, head_sha=pr.sha,
            base_sha=pr.base_sha, now_ms=self.now_ms(),
        )

    def close_epic(self, envelope: EffectEnvelope) -> StepReceipt:
        # This is exactly FOUNDRY-83's provider-atomic parent+children capability.
        durable = self.receipts.command_parent_snapshot_advancement(envelope.campaign_id)
        if durable is None or durable[1] != "completed":
            raise CampaignError("avancement parent absent avant cloture Epic")
        advancement = durable[0]
        if advancement.parent_id != envelope.issue_id:
            raise CampaignError("parent de cloture Epic modifie")
        parent = foundry.tracker().get_issue(envelope.issue_id)
        current_children = tuple(sorted(
            relation.target for relation in getattr(parent, "links", ())
            if getattr(relation, "type", None) == "parent-of"
        ))
        approved_children = tuple(sorted(item[2] for item in advancement.criteria))
        if (getattr(parent, "version", None) != advancement.to_version
                or getattr(parent, "state", None) == "done"
                or current_children != approved_children):
            raise CampaignRevalidationError("parent_close_graph_or_version_conflict")
        self._issue_command(envelope, "close-epic")
        receipt = self._verified_close_receipt(envelope, advancement)
        return self.receipts.record(envelope, receipt, now_ms=self.now_ms())

    def _verified_close(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
        *, expected_proof_digest: str | None = None,
        project_evidence: bool = False,
        original_evidence: dict[str, object] | None = None,
    ) -> tuple[StepReceipt, dict[str, object] | None]:
        tracker = foundry.tracker()
        parent = tracker.get_issue(advancement.parent_id)
        try:
            project = write._epic_closure_project(tracker)
            outcome = tracker.get_epic_closure(project, advancement.parent_id)
            outcome = write._validate_epic_outcome(
                outcome, project=project, parent=parent, expected=None,
            )
        except (AttributeError, SystemExit, ValueError):
            raise CampaignRevalidationError("parent_close_receipt_invalid") from None
        approved_children = tuple(sorted(item[2] for item in advancement.criteria))
        received_children = tuple(item.id for item in outcome.receipt.children)
        if (getattr(parent, "state", None) != "done"
                or getattr(parent, "version", None) != advancement.to_version + 1
                or outcome.receipt.parent_version != advancement.to_version
                or outcome.closed_parent_version != advancement.to_version + 1
                or received_children != approved_children):
            raise CampaignRevalidationError("parent_close_receipt_invalid")
        receipt = self._receipt(envelope, "completed", [
            envelope.effect_id, advancement.advancement_digest,
            asdict(outcome.receipt), outcome.closed_parent_version, outcome.audit_id,
        ])
        if (expected_proof_digest is not None
                and receipt.proof_digest != expected_proof_digest):
            # Recovery may only re-publish the exact original close result already
            # bound by the primitive journal.  A later plausible tracker projection
            # must not contaminate F91 before that comparison is made.
            raise CampaignRevalidationError("parent_close_receipt_invalid")
        observation = None
        if (project_evidence
                or (self.execution_receipts is not None
                    and self.attempt_id is not None)):
            try:
                observation = epic_closure_receipt(
                    tracker.name, envelope.issue_id, outcome,
                    observed_at=self.now_ms(),
                )
            except (AttributeError, ReceiptStoreError, ValueError):
                if project_evidence:
                    raise CampaignRevalidationError(
                        "parent_close_receipt_invalid"
                    ) from None
        if (observation is not None and self.execution_receipts is not None
                and self.attempt_id is not None):
            try:
                self.execution_receipts.record(
                    self.attempt_id, envelope.issue_id, "epic_closure",
                    observation,
                )
            except (OSError, ReceiptStoreError, ValueError):
                # F91 remains observational.  An unreadable or contradictory
                # append-only store cannot be replaced with parent state.
                pass
        evidence = (
            dict(observation.receipt)
            if observation is not None and observation.receipt is not None else None
        )
        if original_evidence is not None:
            # Keep the immutable provider envelope, never reconstruct it from F91.
            original_evidence.update(json.loads(json.dumps({
                "schema_version": "devhub-epic-closure.v1",
                "outcome": {**asdict(outcome), "replayed": True},
            })))
        return receipt, evidence

    def _verified_close_receipt(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
        *, expected_proof_digest: str | None = None,
    ) -> StepReceipt:
        receipt, _evidence = self._verified_close(
            envelope, advancement, expected_proof_digest=expected_proof_digest,
        )
        return receipt

    @staticmethod
    def _closure_digest(outcome: object) -> str:
        return _digest({
            "contract": "foundry-provider-epic-closure-audit.v1",
            "receipt": asdict(outcome.receipt),
            "closed_parent_version": outcome.closed_parent_version,
            "audit_id": outcome.audit_id,
        })

    def verify_legacy_parent_requalification(
        self, record: LegacyParentRequalification,
    ) -> None:
        tracker = foundry.tracker()
        parent = tracker.get_issue(record.parent_id)
        body = getattr(parent, "body", None)
        current_children = tuple(sorted(
            relation.target for relation in getattr(parent, "links", ())
            if getattr(relation, "type", None) == "parent-of"
        ))
        if (getattr(parent, "state", None) != "done"
                or getattr(parent, "version", None) != record.closed_version
                or not isinstance(body, str)
                or _digest(body) != record.parent_body_digest
                or acceptance_digest(acceptance_criteria(body)) != record.parent_ac_digest
                or current_children != tuple(sorted(item[2] for item in record.criteria))
                or {(item[0], item[1]) for item in record.criteria} != {
                    (criterion["id"], criterion["digest"])
                    for criterion in acceptance_criteria(body)
                }):
            raise CampaignRevalidationError("legacy_parent_requalification_conflict")
        for _criterion_id, _criterion_digest, issue_id, effect_id, proof_digest in record.criteria:
            identities = []
            for attempt in range(1, 4):
                material = _digest([
                    record.binding_digest, issue_id, attempt, "merge",
                ])
                if f"effect-{material[:32]}" == effect_id:
                    identities.append((attempt, material))
            if len(identities) != 1:
                raise CampaignRevalidationError("legacy_merge_receipt_invalid")
            attempt, material = identities[0]
            envelope = EffectEnvelope(
                record.campaign_id, record.campaign_id,
                record.parent_id.rsplit("-", 1)[0], record.parent_id,
                issue_id, attempt, "merge", effect_id,
                f"work-{material[32:]}", "legacy-verification", self.now_ms() + 1,
                record.binding_digest, record.snapshot_digest, "0" * 64, None, 0,
            )
            try:
                receipt = self.receipts.get(envelope)
            except CampaignError:
                raise CampaignRevalidationError(
                    "legacy_merge_receipt_invalid"
                ) from None
            if (receipt is None or receipt.status != "completed"
                    or receipt.effect_id != effect_id
                    or receipt.proof_digest != proof_digest):
                raise CampaignRevalidationError("legacy_merge_receipt_invalid")
        try:
            project = write._epic_closure_project(tracker)
            outcome = write._validate_epic_outcome(
                tracker.get_epic_closure(project, record.parent_id),
                project=project, parent=parent, expected=None,
            )
        except (AttributeError, SystemExit, ValueError):
            raise CampaignRevalidationError("legacy_close_receipt_invalid") from None
        if (outcome.receipt.parent_version != record.acceptance_version
                or outcome.closed_parent_version != record.closed_version
                or tuple(item.id for item in outcome.receipt.children) != current_children
                or self._closure_digest(outcome) != record.closure_receipt_digest):
            raise CampaignRevalidationError("legacy_close_receipt_invalid")

    def reconcile_close_epic(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt | None:
        """Recover a committed atomic close by its provider audit, never by replay."""
        parent = foundry.tracker().get_issue(advancement.parent_id)
        body = getattr(parent, "body", None)
        version = getattr(parent, "version", None)
        if (version == advancement.to_version
                and getattr(parent, "state", None) != "done"
                and isinstance(body, str) and _digest(body) == advancement.to_body_digest):
            return None
        receipt = self._verified_close_receipt(envelope, advancement)
        return self.receipts.record(envelope, receipt, now_ms=self.now_ms())

    def prepare_parent_acceptance(
        self, envelope: EffectEnvelope, proof: ParentAcceptanceProof,
    ) -> ParentSnapshotAdvancement:
        """Freeze one exact checkbox patch before either ledger records its intent."""
        if (proof.campaign_id != envelope.campaign_id or proof.parent_id != envelope.issue_id
                or proof.binding_digest != envelope.binding_digest
                or proof.snapshot_digest != envelope.snapshot_digest):
            raise CampaignError("binding synchronisation parent modifié")
        tracker = foundry.tracker()
        parent = tracker.get_issue(envelope.issue_id)
        body = getattr(parent, "body", None)
        version = getattr(parent, "version", None)
        criteria = acceptance_criteria(body) if isinstance(body, str) else None
        current_children = tuple(sorted(
            relation.target for relation in getattr(parent, "links", ())
            if getattr(relation, "type", None) == "parent-of"
        ))
        approved_children = tuple(sorted(item[0] for item in proof.children))
        if (getattr(parent, "id", None) != envelope.issue_id
                or getattr(parent, "type", "").casefold() != "epic"
                or type(version) is not int or version != proof.epic_version
                or not isinstance(criteria, list)
                or current_children != approved_children):
            raise CampaignError("snapshot parent AC incompatible avec la campagne")
        current_criteria = {criterion["id"]: criterion for criterion in criteria}
        merge_receipts = {
            child_id: (merge_effect_id, merge_proof_digest)
            for child_id, merge_effect_id, merge_proof_digest in proof.children
        }
        approved_by_id = {item[0]: item for item in proof.mapping}
        if (len(current_criteria) != len(criteria)
                or len(approved_by_id) != len(proof.mapping)
                or len(proof.mapping) != len(criteria)
                or set(approved_by_id) != set(current_criteria)
                or any(
                    approved_by_id[criterion_id][1] != criterion["digest"]
                    for criterion_id, criterion in current_criteria.items()
                )):
            raise CampaignError("mapping AC parent approuve incompatible")
        mapping = tuple(
            (
                criterion_id, criterion_digest, issue_id,
                merge_receipts[issue_id][0], merge_receipts[issue_id][1],
            )
            for criterion_id, criterion_digest, issue_id in (
                approved_by_id[criterion["id"]] for criterion in criteria
            )
        )
        sync_proof = {
            "proof_id": proof.proof_id,
            "issue": {
                "id": envelope.issue_id,
                "ac_digest": acceptance_digest(criteria),
                "criteria": [
                    {"id": item[0], "digest": item[1], "verdict": "pass"}
                    for item in mapping
                ],
            },
        }
        updated_body, checked = synchronize_acceptance_body(
            envelope.issue_id, body, sync_proof,
        )
        if checked != len(mapping) or updated_body == body:
            raise CampaignError("projection AC parent non monotone")
        return ParentSnapshotAdvancement(
            campaign_id=envelope.campaign_id, parent_id=envelope.issue_id,
            effect_id=envelope.effect_id, binding_digest=envelope.binding_digest,
            snapshot_digest=envelope.snapshot_digest, proof_id=proof.proof_id,
            from_version=version, to_version=version + 1,
            from_body_digest=_digest(body), to_body_digest=_digest(updated_body),
            from_ac_digest=acceptance_digest(criteria),
            to_ac_digest=acceptance_digest(acceptance_criteria(updated_body)),
            criteria=mapping,
        )

    @staticmethod
    def _advancement_sync_proof(
        advancement: ParentSnapshotAdvancement,
    ) -> dict[str, object]:
        return {
            "proof_id": advancement.proof_id,
            "issue": {
                "id": advancement.parent_id,
                "ac_digest": advancement.from_ac_digest,
                "criteria": [
                    {"id": item[0], "digest": item[1], "verdict": "pass"}
                    for item in advancement.criteria
                ],
            },
        }

    def _apply_parent_snapshot_advancement(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt:
        self.receipts._validate_parent_snapshot_binding(envelope, advancement)
        tracker = foundry.tracker()
        parent = tracker.get_issue(envelope.issue_id)
        body = getattr(parent, "body", None)
        version = getattr(parent, "version", None)
        coordinates = (version, _digest(body) if isinstance(body, str) else None)
        source = (advancement.from_version, advancement.from_body_digest)
        target = (advancement.to_version, advancement.to_body_digest)
        if coordinates == source:
            write.sync_acceptance(
                tracker, envelope.issue_id, body,
                self._advancement_sync_proof(advancement),
            )
            parent = tracker.get_issue(envelope.issue_id)
            body = getattr(parent, "body", None)
            version = getattr(parent, "version", None)
            coordinates = (version, _digest(body) if isinstance(body, str) else None)
        if coordinates != target:
            raise CampaignRevalidationError("parent_snapshot_advancement_conflict")
        criteria = acceptance_criteria(body)
        if (acceptance_digest(criteria) != advancement.to_ac_digest
                or tuple(item[:2] for item in advancement.criteria) != tuple(
                    (criterion["id"], criterion["digest"])
                    for criterion in criteria
                )):
            raise CampaignRevalidationError("parent_acceptance_proof_mismatch")
        receipt = self._receipt(envelope, "completed", [
            envelope.effect_id, advancement.advancement_digest,
            advancement.from_version, advancement.to_version,
        ])
        self.receipts.record(envelope, receipt, now_ms=self.now_ms())
        return self.receipts.finish_parent_snapshot_advancement(
            envelope, advancement, receipt, now_ms=self.now_ms(),
        )

    def sync_parent_acceptance(
        self, envelope: EffectEnvelope, proof: ParentAcceptanceProof,
        advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt:
        """Apply or reconcile only the exact, durably planned parent projection."""
        self.receipts._validate_parent_snapshot_binding(envelope, advancement)
        merge_receipts = {
            issue_id: (effect_id, proof_digest)
            for issue_id, effect_id, proof_digest in proof.children
        }
        try:
            ordered_mapping = _ordered_parent_acceptance_mapping(proof.mapping)
        except ValueError:
            raise CampaignError("mapping preuve AC parent modifié") from None
        expected = tuple(
            (criterion_id, criterion_digest, issue_id, *merge_receipts[issue_id])
            for criterion_id, criterion_digest, issue_id in ordered_mapping
        )
        observed = advancement.criteria
        if advancement.proof_id != proof.proof_id or observed != expected:
            raise CampaignError("mapping preuve AC parent modifié")
        self.receipts.begin_parent_snapshot_advancement(
            envelope, advancement, now_ms=self.now_ms(),
        )
        return self._apply_parent_snapshot_advancement(envelope, advancement)

    def reconcile_parent_acceptance(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt | None:
        """Reconcile only a previously frozen source/target transition.

        The source coordinate proves no provider mutation needs recovery and
        therefore returns to the ordinary revalidation path.  Only the exact
        target can complete local receipts before that path.
        """
        self.receipts._validate_parent_snapshot_binding(envelope, advancement)
        durable = self.receipts.parent_snapshot_advancement(envelope)
        if durable is not None and durable[0] != advancement:
            raise CampaignError("intent primitive avancement parent modifie")
        parent = foundry.tracker().get_issue(advancement.parent_id)
        body = getattr(parent, "body", None)
        version = getattr(parent, "version", None)
        coordinates = (version, _digest(body) if isinstance(body, str) else None)
        source = (advancement.from_version, advancement.from_body_digest)
        target = (advancement.to_version, advancement.to_body_digest)
        if coordinates == source:
            return None
        if coordinates != target:
            raise CampaignRevalidationError("parent_snapshot_advancement_conflict")
        if durable is None:
            # The primitive journal is written before the provider call.  A target
            # without it is an unqualified external mutation, never success.
            raise CampaignRevalidationError("parent_snapshot_primitive_intent_missing")
        if durable[0] != advancement:
            raise CampaignError("intent primitive avancement parent modifie")
        return self._apply_parent_snapshot_advancement(envelope, advancement)

    def _verify_pending_parent_snapshot_source(
        self, spec: CampaignSpec, advancement: ParentSnapshotAdvancement,
        acceptance_mapping: tuple[tuple[str, str, str], ...],
        *, epic_version: int,
    ) -> None:
        """Requalify only the exact pre-dispatch source of a durable intent."""
        effect_id, work_id = CampaignCoordinator._identity(
            spec, spec.epic_id, 1, "sync-parent-acceptance",
        )
        if (advancement.campaign_id != spec.campaign_id
                or advancement.parent_id != spec.epic_id
                or advancement.effect_id != effect_id
                or advancement.binding_digest != spec.binding_digest
                or advancement.snapshot_digest != spec.snapshot_digest
                or advancement.from_version != epic_version):
            raise CampaignRevalidationError("parent_snapshot_advancement_conflict")
        try:
            ordered_mapping = _ordered_parent_acceptance_mapping(acceptance_mapping)
            children = tuple(sorted(
                (item[2], item[3], item[4]) for item in advancement.criteria
            ))
            proof = ParentAcceptanceProof(
                spec.campaign_id, spec.epic_id, spec.binding_digest,
                spec.snapshot_digest, advancement.from_version,
                children, acceptance_mapping,
            )
            receipts = {
                issue_id: (merge_effect_id, merge_proof_digest)
                for issue_id, merge_effect_id, merge_proof_digest in children
            }
            expected_criteria = tuple(
                (criterion_id, criterion_digest, issue_id, *receipts[issue_id])
                for criterion_id, criterion_digest, issue_id in ordered_mapping
            )
        except (KeyError, ValueError):
            raise CampaignRevalidationError(
                "parent_acceptance_mapping_invalid"
            ) from None
        if (advancement.proof_id != proof.proof_id
                or advancement.criteria != expected_criteria):
            raise CampaignRevalidationError("parent_snapshot_advancement_conflict")
        self.receipts.verify_parent_acceptance_proof(proof)
        envelope = EffectEnvelope(
            spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
            spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
            "pending-source-verification", self.now_ms() + 1,
            spec.binding_digest, spec.snapshot_digest, spec.policy_digest, None, 0,
        )
        try:
            reproven = self.prepare_parent_acceptance(envelope, proof)
        except CampaignRevalidationError:
            raise
        except CampaignError:
            raise CampaignRevalidationError(
                "parent_snapshot_advancement_conflict"
            ) from None
        if reproven != advancement:
            raise CampaignRevalidationError("parent_snapshot_advancement_conflict")

    def verify_parent_snapshot_advancement(
        self, campaign: str | CampaignSpec, *,
        acceptance_mapping: tuple[tuple[str, str, str], ...] | None = None,
        epic_version: int | None = None,
        original_evidence: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Refuse drift, except for one freshly re-proven pre-dispatch intent."""
        spec = campaign if isinstance(campaign, CampaignSpec) else None
        campaign_id = spec.campaign_id if spec is not None else campaign
        durable = self.receipts.command_parent_snapshot_advancement(campaign_id)
        if durable is None:
            return
        advancement, status, receipt_digest = durable
        if status == "intent" and receipt_digest is None:
            if (spec is None or acceptance_mapping is None
                    or type(epic_version) is not int):
                raise CampaignRevalidationError("parent_snapshot_advancement_incomplete")
            self._verify_pending_parent_snapshot_source(
                spec, advancement, acceptance_mapping, epic_version=epic_version,
            )
            return
        if status != "completed" or receipt_digest is None:
            raise CampaignRevalidationError("parent_snapshot_advancement_incomplete")
        parent = foundry.tracker().get_issue(advancement.parent_id)
        body = getattr(parent, "body", None)
        current_children = tuple(sorted(
            relation.target for relation in getattr(parent, "links", ())
            if getattr(relation, "type", None) == "parent-of"
        ))
        approved_children = tuple(sorted(item[2] for item in advancement.criteria))
        exact_target = (
            getattr(parent, "version", None) == advancement.to_version
            and getattr(parent, "state", None) != "done"
        )
        exact_closed = (
            getattr(parent, "version", None) == advancement.to_version + 1
            and getattr(parent, "state", None) == "done"
        )
        if (not isinstance(body, str)
                or _digest(body) != advancement.to_body_digest
                or acceptance_digest(acceptance_criteria(body)) != advancement.to_ac_digest
                or current_children != approved_children
                or not (exact_target or exact_closed)):
            raise CampaignRevalidationError("parent_snapshot_advancement_conflict")
        if exact_closed:
            effect_id = _digest([
                advancement.binding_digest, advancement.parent_id, 1, "close-epic",
            ])
            envelope = EffectEnvelope(
                advancement.campaign_id, advancement.campaign_id,
                advancement.parent_id.rsplit("-", 1)[0], advancement.parent_id,
                advancement.parent_id, 1, "close-epic", f"effect-{effect_id[:32]}",
                f"work-{effect_id[32:]}", "verification-lease", self.now_ms() + 1,
                advancement.binding_digest, advancement.snapshot_digest,
                "0" * 64, None, 0,
            )
            stored = self.receipts.coordinates(
                campaign_id, advancement.parent_id, 1, "close-epic",
            )
            if stored is None or stored[0].status != "completed":
                raise CampaignRevalidationError("parent_close_receipt_missing")
            # The envelope policy digest does not contribute to the deterministic
            # close receipt; its identity and campaign binding do.
            _receipt, evidence = self._verified_close(
                envelope, advancement,
                expected_proof_digest=stored[0].proof_digest,
                project_evidence=True,
                original_evidence=original_evidence,
            )
            return evidence

    def resolve_effect(
        self, envelope: EffectEnvelope, *,
        engage_merge: Callable[[], None] | None = None,
    ) -> StepReceipt | None:
        existing = self.receipts.get(envelope)
        if existing is not None and existing.status == "completed":
            if envelope.step == "sync-parent-acceptance":
                durable = self.receipts.parent_snapshot_advancement(envelope)
                if durable is None:
                    raise CampaignError("intent primitive avancement parent absent")
                advancement, status, receipt_digest = durable
                if status == "intent":
                    return self.receipts.finish_parent_snapshot_advancement(
                        envelope, advancement, existing, now_ms=self.now_ms(),
                    )
                if receipt_digest != existing.proof_digest:
                    raise CampaignError("reçu primitive avancement parent absent")
            return existing
        if envelope.step in _GATE_STEPS:
            return {
                "review": self.review,
                "ci": self.ci_gate,
                "human-gate": self.human_gate,
            }[envelope.step](envelope)
        if envelope.step not in _MUTATING_STEPS:
            raise CampaignError("étape de primitive inconnue")
        # These are the existing Foundry recovery primitives: start resumes its
        # transition path, openpr reuses the one branch PR, merge reconciles an
        # already-merged exact PR, and F83 reuses its provider-atomic receipt.
        # Re-entering them resolves the same scoped effect rather than launching a
        # generic provider mutation or inventing success from absent local state.
        if envelope.step == "merge":
            return self.merge_issue(envelope, engage=engage_merge)
        if envelope.step == "sync-parent-acceptance":
            durable = self.receipts.parent_snapshot_advancement(envelope)
            if durable is None:
                raise CampaignError("intent primitive avancement parent absent")
            advancement, status, receipt_digest = durable
            if status == "completed":
                receipt = self.receipts.get(envelope)
                if receipt is None or receipt.proof_digest != receipt_digest:
                    raise CampaignError("reçu primitive avancement parent absent")
                return receipt
            return self._apply_parent_snapshot_advancement(envelope, advancement)
        return {
            "start": self.start_issue,
            "open-pr": self.open_pr,
            "close-epic": self.close_epic,
        }[envelope.step](envelope)


class TrustedCampaignPipeline:
    """CampaignPipeline implementation bound to one exact approved command."""

    def __init__(
        self, command: DevHubCommand, source: AuthoritySource,
        primitives: FoundryPrimitiveRunner, retry_authorizer: EscalationRetryAuthorizer,
        *, reserved_cost_cents: int, reserved_concurrency: int,
        reserved_provider_invocation_ceiling_cents: int,
        acceptance_mapping: tuple[tuple[str, str, str], ...] = (),
        allow_zero_budget_reconciliation: bool = False,
    ):
        self.command = command
        self.source = source
        self.primitives = primitives
        self.retry_authorizer = retry_authorizer
        self.reserved_cost_cents = reserved_cost_cents
        self.reserved_concurrency = reserved_concurrency
        self.reserved_provider_invocation_ceiling_cents = (
            reserved_provider_invocation_ceiling_cents
        )
        self.acceptance_mapping = acceptance_mapping
        self.allow_zero_budget_reconciliation = (
            allow_zero_budget_reconciliation
        )

    def observe(self, spec: CampaignSpec) -> CampaignObservation:
        authority = self.source.load(self.command)
        acceptance_mapping = getattr(authority, "acceptance_mapping", ())
        if acceptance_mapping != self.acceptance_mapping:
            raise CampaignError("mapping AC du preview modifié")
        observed = authority.observation
        effective = revalidate_command(
            self.command, observed, now_ms=self.source.now_ms(),
            allow_zero_budget_reconciliation=(
                self.allow_zero_budget_reconciliation
            ),
        )
        self.primitives.verify_parent_snapshot_advancement(
            spec, acceptance_mapping=acceptance_mapping,
            epic_version=observed.epic_version,
        )
        floors = [item for item in (
            observed.approved_minimum_tier, observed.current_minimum_tier,
        ) if item is not None]
        floor = max(floors, key=LEVELS.index) if floors else spec.minimum_tier
        if floor is None:
            raise CampaignError("floor de campagne absent")
        return CampaignObservation(
            campaign_id=spec.campaign_id, command_id=self.command.id,
            project=self.command.project, epic_id=self.command.epic_id,
            preview_id=self.command.preview_id, approval_id=self.command.approval_id,
            approval_state=observed.approval_state,
            planning_version_id=self.command.planning_version_id,
            preview_digest=self.command.preview_digest,
            snapshot_digest=self.command.snapshot_digest,
            policy_digest=self.command.policy_digest,
            expires_at=min(observed.preview_expires_at, observed.approval_expires_at),
            observed_at=observed.observed_at, valid_until=observed.valid_until,
            max_concurrency=min(effective.max_concurrency, self.reserved_concurrency),
            budget_remaining_cents=min(
                effective.max_cost_cents, self.reserved_cost_cents,
            ),
            provider_invocation_ceiling_cents=min(
                observed.provider_invocation_ceiling_cents
                if self.allow_zero_budget_reconciliation
                and observed.budget_remaining_cents == 0
                else effective.provider_invocation_ceiling_cents,
                self.reserved_provider_invocation_ceiling_cents,
            ),
            minimum_tier=floor, host_available=observed.host_available,
            epic_version=observed.epic_version,
            acceptance_mapping=acceptance_mapping,
        )

    def observe_blockers(
        self, spec: CampaignSpec,
    ) -> tuple[BlockerObservation, ...]:
        return self.primitives.observe_blockers(spec)

    def resolve_effect(
        self, envelope: EffectEnvelope, *,
        engage_merge: Callable[[], None] | None = None,
    ) -> StepReceipt | None:
        return self.primitives.resolve_effect(
            envelope, engage_merge=engage_merge,
        )

    def sync_parent_acceptance(
        self, envelope: EffectEnvelope, proof: ParentAcceptanceProof,
        advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt:
        return self.primitives.sync_parent_acceptance(envelope, proof, advancement)

    def prepare_parent_acceptance(
        self, envelope: EffectEnvelope, proof: ParentAcceptanceProof,
    ) -> ParentSnapshotAdvancement:
        return self.primitives.prepare_parent_acceptance(envelope, proof)

    def reconcile_parent_acceptance(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt | None:
        return self.primitives.reconcile_parent_acceptance(envelope, advancement)

    def verify_parent_acceptance_proof(self, proof: ParentAcceptanceProof) -> None:
        self.primitives.receipts.verify_parent_acceptance_proof(proof)

    def reconcile_close_epic(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt | None:
        return self.primitives.reconcile_close_epic(envelope, advancement)

    def verify_legacy_parent_requalification(
        self, record: LegacyParentRequalification,
    ) -> None:
        self.primitives.verify_legacy_parent_requalification(record)

    def start_issue(self, envelope: EffectEnvelope) -> StepReceipt:
        return self.primitives.start_issue(envelope)

    def open_pr(self, envelope: EffectEnvelope) -> StepReceipt:
        return self.primitives.open_pr(envelope)

    def review(self, envelope: EffectEnvelope) -> StepReceipt:
        return self.primitives.review(envelope)

    def ci_gate(self, envelope: EffectEnvelope) -> StepReceipt:
        return self.primitives.ci_gate(envelope)

    def human_gate(self, envelope: EffectEnvelope) -> StepReceipt:
        return self.primitives.human_gate(envelope)

    def merge_issue(
        self, envelope: EffectEnvelope, *, engage: Callable[[], None],
    ) -> StepReceipt:
        return self.primitives.merge_issue(envelope, engage=engage)

    def close_epic(self, envelope: EffectEnvelope) -> StepReceipt:
        return self.primitives.close_epic(envelope)

    def authorize_retry(
        self, envelope: EffectEnvelope, signal: str, current_tier: str,
    ) -> RetryDecision:
        return self.retry_authorizer.authorize(envelope, signal, current_tier)


class IsolatedClaudeIssueExecutor:
    """One issue proposal process with edit-only tools and content-free receipts."""

    _SCHEMA = json.dumps({
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "outcome": {"type": "string", "enum": ["completed", "blocked"]},
        },
        "required": ["outcome"],
    }, separators=(",", ":"))

    def __init__(
        self, root: Path, primitives: FoundryPrimitiveRunner, *,
        proposal_directory: str | os.PathLike, selected_tier: str,
        cost_ceiling_cents: int, runner: Callable[..., object] = subprocess.run,
    ):
        self.root = root
        self.primitives = primitives
        self.directory = Path(proposal_directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self.selected_tier = selected_tier
        self.cost_ceiling_cents = cost_ceiling_cents
        self.runner = runner
        # Reject an untranslated Claude target before writing proposal state.
        self.routing = _load_claude_policy(root, policy_loader=RoutingPolicy.load)

    def execution_profile(self, _issue_id: str, _attempt: int) -> AttemptProfile:
        return AttemptProfile(self.selected_tier, self.cost_ceiling_cents)

    def _path(self, work_id: str) -> Path:
        if _IDENTIFIER.fullmatch(work_id) is None:
            raise CampaignError("identité de proposition invalide")
        return self.directory / f"{work_id}.json"

    def _execution_path(self, work_id: str) -> Path:
        if _IDENTIFIER.fullmatch(work_id) is None:
            raise CampaignError("identité d'exécution invalide")
        return self.directory / f"{work_id}.execution.json"

    @staticmethod
    def _execution_payload(
        envelope: EffectEnvelope, state: str, proof_digest: str, cost_cents: int,
    ) -> dict:
        if state not in {"engaged", "not-engaged", "terminated", "settled"}:
            raise CampaignError("état d'exécution agent invalide")
        return {
            "work_id": envelope.work_id,
            "effect_id": envelope.effect_id,
            "issue_id": envelope.issue_id,
            "attempt": envelope.attempt,
            "state": state,
            "proof_digest": proof_digest,
            "cost_cents": cost_cents,
        }

    def _store_execution(
        self, envelope: EffectEnvelope, state: str, proof_digest: str, cost_cents: int,
    ) -> None:
        payload = self._execution_payload(envelope, state, proof_digest, cost_cents)
        encoded = _canonical(payload)
        path = self._execution_path(envelope.work_id)
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise CampaignError("état d'exécution agent invalide") from None
            if (not isinstance(current, dict)
                    or set(current) != set(payload)
                    or any(current[key] != payload[key] for key in (
                        "work_id", "effect_id", "issue_id", "attempt",
                    ))):
                raise CampaignError("binding d'exécution agent modifié")
            if current == payload:
                return
            expected_engaged = self._execution_payload(
                envelope, "engaged",
                _digest([envelope.effect_id, envelope.work_id, "engaged"]), 0,
            )
            if current != expected_engaged or state == "engaged":
                raise CampaignError("état terminal d'exécution agent modifié")
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{envelope.work_id}-execution-", suffix=".tmp", dir=self.directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read_execution(self, envelope: EffectEnvelope) -> dict | None:
        path = self._execution_path(envelope.work_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            expected_keys = {
                "work_id", "effect_id", "issue_id", "attempt", "state",
                "proof_digest", "cost_cents",
            }
            if (not isinstance(raw, dict) or set(raw) != expected_keys
                    or raw["work_id"] != envelope.work_id
                    or raw["effect_id"] != envelope.effect_id
                    or raw["issue_id"] != envelope.issue_id
                    or raw["attempt"] != envelope.attempt
                    or raw["state"] not in {
                        "engaged", "not-engaged", "terminated", "settled",
                    }
                    or not isinstance(raw["proof_digest"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", raw["proof_digest"]) is None
                    or type(raw["cost_cents"]) is not int
                    or not 0 <= raw["cost_cents"] <= envelope.cost_ceiling_cents):
                raise ValueError
            return raw
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise CampaignError("état d'exécution agent invalide") from None

    def resolve(self, work_id: str) -> ImplementationProposal | None:
        path = self._path(work_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if set(raw) != {
                "work_id", "issue_id", "attempt", "outcome", "proof_digest",
                "cost_cents", "selected_tier", "failure_signal",
            }:
                raise ValueError
            return ImplementationProposal(**raw)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise CampaignError("proposition durable invalide") from None

    def reconcile(self, envelope: EffectEnvelope) -> ImplementationReconciliation:
        raw = self._read_execution(envelope)
        if raw is not None and raw["state"] in {"not-engaged", "terminated"}:
            return ImplementationReconciliation(
                envelope.work_id, raw["state"], raw["proof_digest"], raw["cost_cents"],
            )
        return ImplementationReconciliation(
            envelope.work_id, "unknown",
            _digest([envelope.effect_id, envelope.work_id, "execution-unknown"]), 0,
        )

    def _store(self, proposal: ImplementationProposal) -> None:
        path = self._path(proposal.work_id)
        payload = {
            "work_id": proposal.work_id, "issue_id": proposal.issue_id,
            "attempt": proposal.attempt, "outcome": proposal.outcome,
            "proof_digest": proposal.proof_digest, "cost_cents": proposal.cost_cents,
            "selected_tier": proposal.selected_tier,
            "failure_signal": proposal.failure_signal,
        }
        encoded = _canonical(payload)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{proposal.work_id}-", suffix=".tmp", dir=self.directory,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            if path.exists() and path.read_bytes() != encoded:
                raise CampaignError("proposition durable modifiée")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def execute(self, envelope: EffectEnvelope) -> ImplementationProposal:
        if (envelope.step != "implementation" or envelope.selected_tier is None
                or type(envelope.cost_ceiling_cents) is not int
                or not 1 <= envelope.cost_ceiling_cents <= self.cost_ceiling_cents):
            raise CampaignError("enveloppe d'implémentation contradictoire")
        engaged_proof = _digest([envelope.effect_id, envelope.work_id, "engaged"])
        self._store_execution(envelope, "engaged", engaged_proof, 0)
        provider_invoked = False
        provider_returned = False
        try:
            route = self.routing.resolve(
                "implementer", "claude", minimum_tier=envelope.selected_tier,
                minimum_source="campaign-attempt",
            )
            if route.selected_tier != envelope.selected_tier:
                raise CampaignError("route d'implémentation non exacte")
            worktree = self.primitives.worktree(envelope.campaign_id, envelope.issue_id)
            issue = foundry.tracker().get_issue(envelope.issue_id)
            prompt = (
                "FOUNDRY_ISSUE_PROPOSAL_V1\n"
                f"Implement only issue {issue.id} in the current worktree.\n"
                f"Title: {issue.title}\n"
                f"Specification:\n{issue.body or ''}\n"
                "You may inspect and edit files only. You have no shell, network, plugin, "
                "tracker, PR, review, gate or merge authority. Do not claim tests ran. "
                "Return completed when the focused edits are ready for trusted Foundry "
                "to open the PR and run external gates; return blocked otherwise."
            )
            session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"foundry:{envelope.work_id}"))
            argv = [
                "claude", "--print", "--output-format", "json",
                "--json-schema", self._SCHEMA,
                "--model", claude_invocation_model(
                    route.model,
                    project_models=getattr(self.routing, "claude_models", {}),
                ),
                "--effort", route.effort,
                "--max-budget-usd", f"{envelope.cost_ceiling_cents / 100:.2f}",
                "--permission-mode", "acceptEdits", "--safe-mode", "--no-chrome",
                "--disable-slash-commands", "--strict-mcp-config", "--mcp-config",
                '{"mcpServers":{}}', "--tools", "Read,Edit,Write,Grep,Glob",
                "--no-session-persistence",
                "--session-id", session_id, "--name", f"foundry-{envelope.issue_id}",
            ]
            provider_invoked = True
            completed = self.runner(
                argv, cwd=worktree, input=prompt, capture_output=True, text=True,
                timeout=MAX_IMPLEMENTATION_SECONDS, env=_child_environment(), check=False,
            )
            provider_returned = True
            stdout = getattr(completed, "stdout", None)
            returncode = getattr(completed, "returncode", None)
            if (not isinstance(stdout, str) or type(returncode) is not int
                    or len(stdout.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES):
                raise OSError("résultat d'implémentation ambigu")
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError:
                raise OSError("résultat d'implémentation ambigu") from None
            total_cost = result.get("total_cost_usd") if isinstance(result, dict) else None
            duration = result.get("duration_ms") if isinstance(result, dict) else None
            if (not isinstance(total_cost, (int, float)) or isinstance(total_cost, bool)
                    or type(duration) is not int or duration < 0):
                raise OSError("télémétrie d'implémentation ambiguë")
            cost_cents = math.ceil(float(total_cost) * 100)
            if not 0 <= cost_cents <= envelope.cost_ceiling_cents:
                raise CampaignError("coût d'implémentation hors plafond")
            structured = result.get("structured_output")
            if (returncode != 0 or result.get("is_error") is not False
                    or not isinstance(structured, dict)
                    or structured.get("outcome") not in {"completed", "blocked"}):
                outcome = "host-unavailable"
            else:
                outcome = structured["outcome"]
            proof_digest = self.primitives.capture_implementation_intent(
                envelope, outcome=outcome,
            )
            proposal = ImplementationProposal(
                work_id=envelope.work_id, issue_id=envelope.issue_id,
                attempt=envelope.attempt, outcome=outcome,
                proof_digest=proof_digest,
                cost_cents=cost_cents, selected_tier=envelope.selected_tier,
            )
            self._store(proposal)
            self._store_execution(
                envelope, "settled", proposal.proof_digest, proposal.cost_cents,
            )
            return proposal
        except subprocess.TimeoutExpired:
            proof = _digest([envelope.effect_id, envelope.work_id, "terminated-timeout"])
            self._store_execution(
                envelope, "terminated", proof, envelope.cost_ceiling_cents,
            )
            raise TimeoutError("implémentation de campagne expirée") from None
        except OSError:
            state = "terminated" if provider_returned else "not-engaged"
            cost = envelope.cost_ceiling_cents if provider_returned else 0
            proof = _digest([envelope.effect_id, envelope.work_id, state, "provider-oserror"])
            self._store_execution(envelope, state, proof, cost)
            raise
        except Exception:
            if not provider_invoked or provider_returned:
                state = "terminated" if provider_returned else "not-engaged"
                cost = envelope.cost_ceiling_cents if provider_returned else 0
                proof = _digest([
                    envelope.effect_id, envelope.work_id, state, "provider-exception",
                ])
                self._store_execution(envelope, state, proof, cost)
            # An exception while the provider call itself is outstanding is
            # ambiguous.  Preserve the engaged marker and its reservation.
            raise


class _CommandReceiptFiles:
    def __init__(self, directory: str | os.PathLike):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self._lock = threading.RLock()

    def _path(self, command_id: str) -> Path:
        if _IDENTIFIER.fullmatch(command_id) is None:
            raise CommandWorkerError("identité de reçu campagne invalide")
        return self.directory / f"{command_id}.json"

    def read(self, command_id: str, binding_digest: str) -> EffectReceipt | None:
        path = self._path(command_id)
        if not path.exists():
            return None
        try:
            raw_bytes = path.read_bytes()
            if len(raw_bytes) > MAX_RECEIPT_BYTES:
                raise ValueError
            raw = json.loads(raw_bytes.decode("utf-8"))
            if (set(raw) != {"contract", "binding_digest", "effect"}
                    or raw["contract"] != RECEIPT_CONTRACT
                    or raw["binding_digest"] != binding_digest):
                raise ValueError
            effect = raw["effect"]
            legacy_keys = {
                "effect_id", "status", "proof_digest", "cost_cents", "duration_ms",
            }
            extended_keys = legacy_keys | {"attempt_id", "attempt_started_at"}
            if (not isinstance(effect, dict)
                    or frozenset(effect) not in {
                        frozenset(legacy_keys), frozenset(extended_keys),
                    }):
                raise ValueError
            return EffectReceipt(**effect)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise CommandWorkerError("reçu campagne Foundry invalide") from None

    def write(self, binding_digest: str, effect: EffectReceipt) -> None:
        path = self._path(effect.effect_id)
        payload = {
            "contract": RECEIPT_CONTRACT, "binding_digest": binding_digest,
            "effect": {
                "effect_id": effect.effect_id, "status": effect.status,
                "proof_digest": effect.proof_digest, "cost_cents": effect.cost_cents,
                "duration_ms": effect.duration_ms,
                "attempt_id": effect.attempt_id,
                "attempt_started_at": effect.attempt_started_at,
            },
        }
        encoded = _canonical(payload)
        with self._lock:
            prior = self.read(effect.effect_id, binding_digest)
            if (prior is not None and prior.status in {"cancelled", "completed", "failed"}
                    and prior != effect):
                raise CommandWorkerError("reçu terminal de campagne modifié")
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{effect.effect_id}-", suffix=".tmp", dir=self.directory,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)


@dataclass(frozen=True)
class _Composition:
    spec: CampaignSpec
    coordinator: CampaignCoordinator
    store: CampaignStore


class CampaignCommandEffectProvider:
    """Outbound command provider backed by the real trusted campaign coordinator."""

    def __init__(
        self, source: AuthoritySource, *, root: str | os.PathLike,
        state_directory: str | os.PathLike, owner_id: str,
        primitive_runner: Callable[..., FoundryPrimitiveRunner] = FoundryPrimitiveRunner,
        executor_factory: Callable[..., IsolatedClaudeIssueExecutor] = (
            IsolatedClaudeIssueExecutor
        ),
        escalation_store_factory: Callable[[Path], object] = EscalationStore.for_root,
        implementation_runner: Callable[..., object] = subprocess.run,
        execution_receipts: ExecutionReceiptStore | None = None,
    ):
        self.source = source
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("racine d'exécution Foundry absente")
        # Validate the translation before state/receipt preparation or reservation.
        self.routing = _load_claude_policy(self.root, policy_loader=RoutingPolicy.load)
        self.state = Path(state_directory).expanduser().resolve()
        self.state.mkdir(parents=True, exist_ok=True)
        self.state.chmod(0o700)
        self.owner_id = owner_id
        self.primitive_runner_factory = primitive_runner
        self.executor_factory = executor_factory
        self.escalation_store_factory = escalation_store_factory
        self.implementation_runner = implementation_runner
        self.receipts = _CommandReceiptFiles(self.state / "effects")
        self.execution_receipts = (
            execution_receipts
            if execution_receipts is not None
            else ExecutionReceiptStore(self.state.parent / "execution-receipts")
        )
        self._profile_lock = threading.RLock()

    @staticmethod
    def _attempt_id(command: DevHubCommand, binding_digest: str) -> str:
        if binding_digest != _binding_digest(command):
            raise CommandWorkerError("binding de tentative campagne contradictoire")
        material = _digest([
            "foundry-campaign-command-attempt.v1", command.id, binding_digest,
        ])
        return f"attempt-{material[:32]}"

    def _persisted_attempt_id(self, command: DevHubCommand) -> str | None:
        binding_digest = _binding_digest(command)
        receipt = self.receipts.read(command.id, binding_digest)
        if receipt is None or receipt.attempt_id is None:
            return None
        if receipt.attempt_id != self._attempt_id(command, binding_digest):
            raise CommandWorkerError("identité de tentative campagne contradictoire")
        return receipt.attempt_id

    def _attempt_coordinates(
        self, command: DevHubCommand, binding_digest: str,
        prior_receipt: EffectReceipt | None,
    ) -> tuple[str | None, int | None]:
        persisted = self.receipts.read(command.id, binding_digest)
        if persisted is not None and prior_receipt is not None and (
            persisted.attempt_id != prior_receipt.attempt_id
            or persisted.attempt_started_at != prior_receipt.attempt_started_at
        ):
            raise CommandWorkerError("preuve de tentative campagne contradictoire")
        source = persisted
        if source is None and prior_receipt is not None:
            source = prior_receipt
        if source is not None and source.attempt_id is None:
            # Explicit legacy compatibility: an old campaign receipt remains
            # usable but is never relabelled as a newly observed F91 attempt.
            campaign_db = (
                self.state / "campaigns" / command.id / "campaign.sqlite3"
            )
            if persisted is not None or campaign_db.exists():
                return None, None
            source = None
        expected = self._attempt_id(command, binding_digest)
        if source is not None:
            if source.attempt_id != expected:
                raise CommandWorkerError("identité de tentative campagne contradictoire")
            return source.attempt_id, source.attempt_started_at
        return expected, self.source.now_ms()

    @contextmanager
    def _locked_profile(self, path: Path):
        lock_path = path.with_suffix(".lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        with self._profile_lock:
            try:
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                raise CommandWorkerError("verrou de profil campagne indisponible") from exc
            with os.fdopen(descriptor, "a+") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except OSError as exc:
                    raise CommandWorkerError(
                        "verrou de profil campagne indisponible"
                    ) from exc
                yield

    def observe(self, command: DevHubCommand) -> RevalidationObservation:
        authority = self._reconcile_parent_snapshot_before_revalidation(command)
        if authority is None:
            authority = self.source.load(command)
        return self._recognized_observation(command, authority.observation)

    def _reconcile_parent_snapshot_before_revalidation(
        self, command: DevHubCommand,
    ) -> object | None:
        campaign_dir = self.state / "campaigns" / command.id
        campaign_db = campaign_dir / "campaign.sqlite3"
        primitive_db = campaign_dir / "primitive-receipts.sqlite3"
        if not campaign_db.exists() and not primitive_db.exists():
            return
        if not campaign_db.exists() or not primitive_db.exists():
            raise CommandWorkerError("journaux avancement snapshot incomplets")
        coordinator = CampaignStore(campaign_db)
        durable = coordinator.parent_snapshot_advancement(command.id)
        if durable is None:
            return
        advancement = durable[0]
        spec = self._validated_parent_snapshot_spec(command, coordinator, advancement)
        coordinator.validate_parent_acceptance_evidence(spec, advancement)
        effects = CampaignEffectStore(primitive_db)
        primitive = effects.command_parent_snapshot_advancement(command.id)
        if (primitive is not None and primitive[0] != advancement):
            raise CommandWorkerError("avancement snapshot contradictoire")
        if (durable[1] == "completed"
                and (primitive is None or primitive[1] != "completed"
                     or primitive[2] is None)):
            raise CommandWorkerError("avancement snapshot contradictoire")
        authority = self.source.load(command)
        proof = coordinator.parent_acceptance_proof(
            spec, advancement,
            epic_version=authority.observation.epic_version,
            acceptance_mapping=getattr(authority, "acceptance_mapping", ()),
        )
        effects.verify_parent_acceptance_proof(proof)
        effect_id, work_id = CampaignCoordinator._identity(
            spec, spec.epic_id, 1, "sync-parent-acceptance",
        )
        envelope = EffectEnvelope(
            spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
            spec.epic_id, 1, "sync-parent-acceptance", effect_id, work_id,
            "outer-reconciliation", self.source.now_ms() + 1,
            spec.binding_digest, spec.snapshot_digest, spec.policy_digest, None, 0,
        )
        primitives = self.primitive_runner_factory(
            self.root, worktree_directory=self.state / "worktrees",
            receipts=effects, now_ms=self.source.now_ms,
            execution_receipts=self.execution_receipts,
            attempt_id=self._persisted_attempt_id(command),
        )
        if durable[1] == "completed":
            self._reconcile_close_before_revalidation(
                command, coordinator, primitives, advancement, spec=spec,
            )
            primitives.verify_parent_snapshot_advancement(command.id)
            return authority
        receipt = primitives.reconcile_parent_acceptance(envelope, advancement)
        if receipt is None:
            return authority
        coordinator.finish_parent_snapshot_advancement(
            advancement, receipt, now_ms=self.source.now_ms(),
        )
        coordinator.finish_effect(
            envelope, status=receipt.status, proof_digest=receipt.proof_digest,
        )
        return authority

    @staticmethod
    def _validated_parent_snapshot_spec(
        command: DevHubCommand, coordinator: CampaignStore,
        advancement: ParentSnapshotAdvancement,
    ) -> CampaignSpec:
        """Bind coordinator-only recovery to the immutable approved campaign."""
        spec = coordinator.campaign_spec(command.id)
        effect_id, _work_id = CampaignCoordinator._identity(
            spec, spec.epic_id, 1, "sync-parent-acceptance",
        )
        exact_command = (
            spec.command_id == command.id
            and spec.project == command.project
            and spec.epic_id == command.epic_id
            and spec.preview_id == command.preview_id
            and spec.approval_id == command.approval_id
            and spec.planning_version_id == command.planning_version_id
            and spec.preview_digest == command.preview_digest
            and spec.snapshot_digest == command.snapshot_digest
            and spec.policy_digest == command.policy_digest
            and spec.max_concurrency == command.max_concurrency
            and spec.scheduled_for == command.scheduled_for
        )
        exact_advancement = (
            advancement.campaign_id == spec.campaign_id
            and advancement.parent_id == spec.epic_id
            and advancement.effect_id == effect_id
            and advancement.binding_digest == spec.binding_digest
            and advancement.snapshot_digest == spec.snapshot_digest
        )
        if not exact_command or not exact_advancement:
            raise CommandWorkerError("binding durable avancement snapshot contradictoire")
        return spec

    def _reconcile_close_before_revalidation(
        self, command: DevHubCommand, coordinator: CampaignStore,
        primitives: FoundryPrimitiveRunner, advancement: ParentSnapshotAdvancement,
        *, spec: CampaignSpec | None = None,
    ) -> None:
        """Recover only an exact durable close intent from the provider audit."""
        close = coordinator.effect(command.id, command.epic_id, 1, "close-epic")
        if close is None or close[2] == "completed":
            return
        spec = spec or self._validated_parent_snapshot_spec(
            command, coordinator, advancement,
        )
        material = _digest([
            advancement.binding_digest, advancement.parent_id, 1, "close-epic",
        ])
        effect_id = f"effect-{material[:32]}"
        work_id = f"work-{material[32:]}"
        expected_intent = (effect_id, work_id, "intent", None, 0, None, None, None)
        if (spec.command_id != command.id or spec.project != command.project
                or spec.epic_id != command.epic_id
                or spec.snapshot_digest != command.snapshot_digest
                or spec.policy_digest != command.policy_digest
                or advancement.campaign_id != spec.campaign_id
                or advancement.parent_id != command.epic_id
                or advancement.binding_digest != spec.binding_digest
                or advancement.snapshot_digest != spec.snapshot_digest
                or close != expected_intent):
            raise CommandWorkerError("intent exact de cloture Epic contradictoire")
        envelope = EffectEnvelope(
            spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
            spec.epic_id, 1, "close-epic", effect_id, work_id,
            "outer-close-reconciliation", self.source.now_ms() + 1,
            spec.binding_digest, spec.snapshot_digest, spec.policy_digest, None, 0,
        )
        receipt = primitives.reconcile_close_epic(envelope, advancement)
        if receipt is None:
            return
        if (not isinstance(receipt, StepReceipt)
                or receipt.effect_id != effect_id or receipt.status != "completed"):
            raise CommandWorkerError("recu de cloture Epic contradictoire")
        coordinator.finish_effect(
            envelope, status=receipt.status, proof_digest=receipt.proof_digest,
        )

    def _recognized_observation(
        self, command: DevHubCommand, observed: RevalidationObservation,
    ) -> RevalidationObservation:
        campaign_dir = self.state / "campaigns" / command.id
        campaign_db = campaign_dir / "campaign.sqlite3"
        primitive_db = campaign_dir / "primitive-receipts.sqlite3"
        if not campaign_db.exists() and not primitive_db.exists():
            return observed
        if not campaign_db.exists() or not primitive_db.exists():
            raise CommandWorkerError("journaux avancement snapshot incomplets")
        campaign_record = CampaignStore(campaign_db).parent_snapshot_advancement(command.id)
        primitive_record = CampaignEffectStore(
            primitive_db,
        ).command_parent_snapshot_advancement(command.id)
        if campaign_record is None and primitive_record is None:
            return observed
        if campaign_record is not None and primitive_record is None:
            # Coordinator intent is durable before primitive intent.  The bounded
            # source-coordinate reconciliation above proved no mutation occurred;
            # ordinary authorization may now decide whether dispatch can start.
            return observed
        if campaign_record is None or primitive_record is None:
            raise CommandWorkerError("avancement snapshot non réconcilié")
        advancement, campaign_status, campaign_receipt = campaign_record
        primitive_advancement, primitive_status, primitive_receipt = primitive_record
        if (advancement == primitive_advancement
                and campaign_status == "intent" and campaign_receipt is None
                and primitive_status == "intent" and primitive_receipt is None):
            return observed
        if (advancement != primitive_advancement
                or campaign_status not in {"intent", "completed"}
                or primitive_status != "completed"
                or primitive_receipt is None
                or (campaign_status == "completed" and campaign_receipt != primitive_receipt)):
            raise CommandWorkerError("avancement snapshot contradictoire")
        transition = SnapshotAdvancementObservation(
            command.id, advancement.effect_id, _binding_digest(command),
            command.snapshot_digest, advancement.from_version,
            advancement.to_version, advancement.advancement_digest,
            primitive_receipt,
        )
        return replace(observed, snapshot_advancement=transition)

    def _route_limits(
        self, command: DevHubCommand, *,
        allow_zero_budget_reconciliation: bool = False,
    ):
        authority = self._reconcile_parent_snapshot_before_revalidation(command)
        if authority is None:
            authority = self.source.load(command)
        observed = self._recognized_observation(command, authority.observation)
        effective = revalidate_command(
            command, observed, now_ms=self.source.now_ms(),
            allow_zero_budget_reconciliation=(
                allow_zero_budget_reconciliation
            ),
        )
        floors = [item for item in (
            observed.approved_minimum_tier, observed.current_minimum_tier,
        ) if item is not None]
        floor = max(floors, key=LEVELS.index) if floors else None
        route = self.routing.resolve(
            "implementer", "claude", minimum_tier=floor,
            minimum_source="devhub-command" if floor else None,
        )
        return authority, effective, route

    @staticmethod
    def _read_persisted_profile(
        path: Path, command: DevHubCommand,
    ) -> tuple[CampaignExecutionProfile, dict]:
        keys = {
            "binding_digest", "selected_tier", "cost_ceiling_cents",
            "concurrency_units", "provider_invocation_ceiling_cents",
        }
        try:
            persisted = json.loads(path.read_text(encoding="utf-8"))
            legacy_keys = keys - {"provider_invocation_ceiling_cents"}
            if (not isinstance(persisted, dict)
                    or set(persisted) not in (keys, legacy_keys)
                    or persisted["binding_digest"] != _binding_digest(command)):
                raise ValueError
            if set(persisted) == legacy_keys:
                raise PreEffectCapacityError(
                    "profil durable de campagne sans plafond provider",
                )
            base_profile = ExecutionProfile(
                persisted["selected_tier"], persisted["cost_ceiling_cents"],
                persisted["concurrency_units"],
            )
            persisted_provider_ceiling = (
                persisted["provider_invocation_ceiling_cents"]
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise CommandWorkerError("profil durable de campagne invalide") from None
        if (type(persisted_provider_ceiling) is not int
                or not 1 <= persisted_provider_ceiling
                <= base_profile.cost_ceiling_cents):
            raise PreEffectCapacityError(
                "plafond provider durable de campagne invalide",
            )
        return CampaignExecutionProfile(
            base_profile.selected_tier, base_profile.cost_ceiling_cents,
            base_profile.concurrency_units,
            provider_invocation_ceiling_cents=persisted_provider_ceiling,
        ), persisted

    def _profile(
        self, command: DevHubCommand, authority, effective, route,
    ) -> CampaignExecutionProfile:
        path = self.state / "profiles" / f"{command.id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        available = effective.max_concurrency - authority.observation.active_concurrency
        concurrency_units = max(1, available)
        provider_ceiling = effective.provider_invocation_ceiling_cents
        candidate = CampaignExecutionProfile(
            route.selected_tier, effective.max_cost_cents, concurrency_units,
            provider_invocation_ceiling_cents=provider_ceiling,
        )
        payload = {
            "binding_digest": _binding_digest(command),
            "selected_tier": candidate.selected_tier,
            "cost_ceiling_cents": candidate.cost_ceiling_cents,
            "concurrency_units": candidate.concurrency_units,
            "provider_invocation_ceiling_cents": (
                candidate.provider_invocation_ceiling_cents
            ),
        }
        encoded = _canonical(payload)
        selected_profile = candidate
        with self._locked_profile(path):
            if path.exists():
                profile, persisted = self._read_persisted_profile(
                    path, command,
                )
                if LEVELS.index(profile.selected_tier) < LEVELS.index(route.selected_tier):
                    raise CommandWorkerError("capacité de campagne resserrée")
                tightened_concurrency = min(profile.concurrency_units, max(1, available))
                if (profile.cost_ceiling_cents > effective.max_cost_cents
                        or profile.provider_invocation_ceiling_cents
                        > effective.provider_invocation_ceiling_cents):
                    raise PreEffectCapacityError("capacité de campagne resserrée")
                selected_profile = CampaignExecutionProfile(
                    profile.selected_tier, profile.cost_ceiling_cents,
                    tightened_concurrency,
                    provider_invocation_ceiling_cents=(
                        profile.provider_invocation_ceiling_cents
                    ),
                )
                if selected_profile == profile:
                    return profile
                payload = {
                    "binding_digest": persisted["binding_digest"],
                    "selected_tier": selected_profile.selected_tier,
                    "cost_ceiling_cents": selected_profile.cost_ceiling_cents,
                    "concurrency_units": selected_profile.concurrency_units,
                    "provider_invocation_ceiling_cents": (
                        selected_profile.provider_invocation_ceiling_cents
                    ),
                }
                encoded = _canonical(payload)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{command.id}-", suffix=".tmp", dir=path.parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return selected_profile

    def _reconciliation_profile(
        self, command: DevHubCommand,
    ) -> CampaignExecutionProfile:
        """Read the immutable profile of an effect that already crossed launch."""
        path = self.state / "profiles" / f"{command.id}.json"
        if not path.is_file():
            raise CommandWorkerError(
                "profil durable de campagne absent pour réconciliation",
            )
        with self._locked_profile(path):
            profile, _persisted = self._read_persisted_profile(path, command)
        return profile

    def execution_profile(
        self, command: DevHubCommand, _receipt: EffectReceipt | None,
    ) -> ExecutionProfile:
        authority, effective, route = self._route_limits(command)
        return self._profile(command, authority, effective, route)

    def resolve(self, command_id: str, binding_digest: str) -> EffectReceipt | None:
        return self.receipts.read(command_id, binding_digest)

    def late_terminal_evidence(
        self, command: DevHubCommand,
    ) -> tuple[bool, dict[str, object] | None]:
        """Recover an original close fact, without entering the execution engine.

        A close intent may have survived before the local completed receipt. Only
        its exact provider audit can finish that local proof. No claim, route,
        reservation, pipeline run or provider mutation is reachable here.
        """
        effect = self.resolve(command.id, _binding_digest(command))
        if effect is None or effect.attempt_id is None:
            return False, None
        if effect.effect_id != command.id:
            raise CommandWorkerError("identité d'effet provider contradictoire")
        attempt_id = self._persisted_attempt_id(command)
        campaign_dir = self.state / "campaigns" / command.id
        campaign_db = campaign_dir / "campaign.sqlite3"
        primitive_db = campaign_dir / "primitive-receipts.sqlite3"
        if not campaign_db.exists() or not primitive_db.exists():
            return True, None
        coordinator = CampaignStore(campaign_db)
        close = coordinator.effect(command.id, command.epic_id, 1, "close-epic")
        if close is None:
            return False, None
        durable = coordinator.parent_snapshot_advancement(command.id)
        if durable is None or durable[1] != "completed" or durable[2] is None:
            return True, None
        advancement = durable[0]
        spec = self._validated_parent_snapshot_spec(command, coordinator, advancement)
        coordinator.validate_parent_acceptance_evidence(spec, advancement)
        effects = CampaignEffectStore(primitive_db)
        if effects.command_parent_snapshot_advancement(command.id) != durable:
            raise CommandWorkerError("preuve primitive terminale contradictoire")
        primitives = self.primitive_runner_factory(
            self.root, worktree_directory=self.state / "worktrees",
            receipts=effects, now_ms=self.source.now_ms,
            execution_receipts=self.execution_receipts, attempt_id=attempt_id,
        )
        original: dict[str, object] = {}
        try:
            self._reconcile_close_before_revalidation(
                command, coordinator, primitives, advancement, spec=spec,
            )
            primitives.verify_parent_snapshot_advancement(
                command.id, original_evidence=original,
            )
        except (CampaignRevalidationError, DevHubTrackerError):
            return True, None
        if not original:
            return True, None
        return True, {
            "attempt_id": attempt_id, "original_closure": original,
            # An interrupted execution has no complete duration/cost measurement.
            "cost_cents": effect.cost_cents if effect.status == "completed" else None,
            "duration_ms": effect.duration_ms if effect.status == "completed" else None,
        }

    def verified_campaign_terminal_evidence(
        self, command: DevHubCommand, effect: EffectReceipt,
    ) -> tuple[bool, dict[str, object] | None]:
        """Return only the original verified closure for native publication.

        The first tuple item tells the outer worker whether the native attempt
        requires extended evidence. F91 is enriched best-effort by the underlying
        verifier but is never the authority or publication gate. Legacy campaign
        receipts have no attempt coordinate and keep their old wire shape. This path
        performs only F102/provider reads; it never enters execution, close, merge,
        routing or reservation paths.
        """
        binding_digest = _binding_digest(command)
        persisted = self.receipts.read(command.id, binding_digest)
        if (effect.effect_id != command.id or effect.status != "completed"
                or persisted != effect):
            raise CommandWorkerError("reçu terminal de campagne contradictoire")
        if effect.attempt_id is None:
            return False, None
        if effect.attempt_id != self._attempt_id(command, binding_digest):
            raise CommandWorkerError("identité de tentative campagne contradictoire")
        receipts = {kind: None for kind in (
            "pr", "review", "ci", "merge", "epic_closure",
        )}
        try:
            existing = self.execution_receipts.receipts_for(
                effect.attempt_id, command.epic_id,
            )
        except (OSError, ReceiptStoreError, ValueError):
            # The append-only journal is passive. Its outage cannot erase the
            # independently revalidated original closure returned below.
            pass
        else:
            receipts.update(existing)
        # F91 may contribute other passive observations to the event, but the
        # authoritative closure coordinate below is always re-read from its
        # original tracker endpoint and re-bound to the persisted close receipt.
        receipts["epic_closure"] = None
        campaign_dir = self.state / "campaigns" / command.id
        campaign_db = campaign_dir / "campaign.sqlite3"
        primitive_db = campaign_dir / "primitive-receipts.sqlite3"
        if not campaign_db.exists() and not primitive_db.exists():
            return True, None
        if not campaign_db.exists() or not primitive_db.exists():
            raise CommandWorkerError("journaux de preuve terminale incomplets")
        coordinator = CampaignStore(campaign_db)
        durable = coordinator.parent_snapshot_advancement(command.id)
        if durable is None:
            return True, None
        advancement, status, receipt_digest = durable
        spec = self._validated_parent_snapshot_spec(command, coordinator, advancement)
        campaign = coordinator.campaign(command.id)
        if (campaign.state != "completed" or status != "completed"
                or receipt_digest is None):
            raise CommandWorkerError("preuve terminale campagne incomplète")
        coordinator.validate_parent_acceptance_evidence(spec, advancement)
        effects = CampaignEffectStore(primitive_db)
        if effects.command_parent_snapshot_advancement(command.id) != durable:
            raise CommandWorkerError("preuve primitive terminale contradictoire")
        primitives = self.primitive_runner_factory(
            self.root, worktree_directory=self.state / "worktrees",
            receipts=effects, now_ms=self.source.now_ms,
            execution_receipts=self.execution_receipts,
            attempt_id=effect.attempt_id,
        )
        try:
            closure = primitives.verify_parent_snapshot_advancement(command.id)
        except (CampaignRevalidationError, DevHubTrackerError):
            # Missing or contradictory original proof is not success.  Keep the
            # completed provider effect durable and let a later scan retry the read.
            return True, None
        if closure is None:
            return True, None
        receipts["epic_closure"] = closure
        return True, receipts

    def _composition(
        self, command: DevHubCommand,
        authorization: ExecutionAuthorization | None, *,
        heartbeat: Callable[[], object],
        attempt_id: str | None = None,
        zero_budget_reconciliation: bool = False,
    ) -> _Composition:
        if zero_budget_reconciliation:
            if authorization is not None:
                raise CommandWorkerError(
                    "autorisation fraîche interdite en réconciliation",
                )
            authority = self.source.load(command)
            observed = self._recognized_observation(
                command, authority.observation,
            )
            effective = revalidate_command(
                command, observed, now_ms=self.source.now_ms(),
                allow_zero_budget_reconciliation=True,
            )
            if effective.observed_budget_remaining_cents != 0:
                raise CommandWorkerError(
                    "réconciliation sans budget campagne invalide",
                )
            profile = self._reconciliation_profile(command)
        else:
            if authorization is None:
                raise CommandWorkerError("autorisation de campagne absente")
            authority, effective, route = self._route_limits(command)
            profile = self._profile(command, authority, effective, route)
            if (authorization.command_id != command.id
                    or authorization.binding_digest != _binding_digest(command)
                    or authorization.selected_tier != profile.selected_tier
                    or authorization.cost_ceiling_cents != profile.cost_ceiling_cents
                    or authorization.concurrency_units != profile.concurrency_units
                    or authorization.provider_invocation_ceiling_cents
                    != profile.provider_invocation_ceiling_cents):
                raise CommandWorkerError("autorisation de campagne contradictoire")
        observed = authority.observation
        spec = CampaignSpec(
            campaign_id=command.id, command_id=command.id, project=command.project,
            epic_id=command.epic_id, preview_id=command.preview_id,
            approval_id=command.approval_id,
            planning_version_id=command.planning_version_id,
            preview_digest=command.preview_digest,
            snapshot_digest=command.snapshot_digest,
            policy_digest=command.policy_digest,
            waves=authority.waves, dependencies=(),
            # The immutable campaign binding keeps the approved ceiling.  The
            # current host reservation is a monotonic runtime observation and may
            # tighten independently between resumptions.
            max_concurrency=command.max_concurrency,
            budget_cents=profile.cost_ceiling_cents,
            expires_at=min(observed.preview_expires_at, observed.approval_expires_at),
            scheduled_for=command.scheduled_for,
            minimum_tier=profile.selected_tier,
            max_attempts_per_issue=3,
            blockers=authority.blockers,
        )
        campaign_dir = self.state / "campaigns" / command.id
        effects = CampaignEffectStore(campaign_dir / "primitive-receipts.sqlite3")
        primitives = self.primitive_runner_factory(
            self.root, worktree_directory=self.state / "worktrees",
            receipts=effects, now_ms=self.source.now_ms,
            execution_receipts=self.execution_receipts,
            attempt_id=attempt_id,
        )
        store = CampaignStore(campaign_dir / "campaign.sqlite3")
        pipeline = TrustedCampaignPipeline(
            command, self.source, primitives,
            EscalationRetryAuthorizer(
                self.escalation_store_factory(self.root),
                review_authorization=store.review_remediation_authorization,
                now_ms=self.source.now_ms,
            ),
            reserved_cost_cents=profile.cost_ceiling_cents,
            reserved_concurrency=profile.concurrency_units,
            reserved_provider_invocation_ceiling_cents=(
                profile.provider_invocation_ceiling_cents
            ),
            acceptance_mapping=getattr(authority, "acceptance_mapping", ()),
            allow_zero_budget_reconciliation=zero_budget_reconciliation,
        )
        executor = self.executor_factory(
            self.root, primitives, proposal_directory=campaign_dir / "proposals",
            selected_tier=profile.selected_tier,
            cost_ceiling_cents=profile.provider_invocation_ceiling_cents,
            runner=self.implementation_runner,
        )
        def control(_spec: CampaignSpec) -> str | None:
            latest = heartbeat()
            state = getattr(latest, "state", command.state)
            if (state == "pause-requested"
                    or (state == "paused" and not zero_budget_reconciliation)):
                return "pause-requested"
            if state in {"cancel-requested", "cancelled"}:
                return "cancel-requested"
            return "active"

        coordinator = CampaignCoordinator(
            store, pipeline, executor, owner_id=self.owner_id,
            now_ms=self.source.now_ms, control_observer=control,
        )
        return _Composition(spec, coordinator, store)

    def reconcile_existing(
        self, command: DevHubCommand, receipt: EffectReceipt, *,
        heartbeat: Callable[[], object],
    ) -> EffectReceipt:
        """Resolve engaged implementation identities without launch or resume."""
        binding_digest = _binding_digest(command)
        persisted = self.receipts.read(command.id, binding_digest)
        if (receipt.effect_id != command.id
                or receipt.status not in {"running", "paused"}
                or persisted != receipt):
            raise CommandWorkerError(
                "reçu de campagne à réconcilier contradictoire",
            )
        started = time.monotonic()
        attempt_id, attempt_started_at = self._attempt_coordinates(
            command, binding_digest, receipt,
        )
        composition = self._composition(
            command, None, heartbeat=heartbeat, attempt_id=attempt_id,
            zero_budget_reconciliation=True,
        )
        outcome = (
            composition.coordinator
            .reconcile_engaged_implementations_at_zero_budget(
                composition.spec,
            )
        )
        duration = min(int((time.monotonic() - started) * 1000), 604_800_000)
        effect = self._effect(
            command, outcome, duration,
            attempt_id=attempt_id, attempt_started_at=attempt_started_at,
        )
        self.receipts.write(binding_digest, effect)
        return effect

    @staticmethod
    def _effect(
        command: DevHubCommand, outcome: CampaignOutcome, duration_ms: int, *,
        attempt_id: str | None, attempt_started_at: int | None,
    ) -> EffectReceipt:
        if outcome.state == "suspended":
            # DevHub's closed command contract has no Foundry-specific suspension
            # vocabulary.  Keep the exact resumable cause in CampaignStore and
            # publish only its existing non-terminal projection.
            status = "paused"
        else:
            status = {
                "completed": "completed", "cancelled": "cancelled",
                "paused": "paused", "scheduled": "running", "running": "running",
            }[outcome.state]
        proof = _digest({
            "campaign_id": outcome.campaign_id, "state": outcome.state,
            "reason": outcome.reason, "wave_index": outcome.wave_index,
            "completed_issues": list(outcome.completed_issues),
            "budget_spent_cents": outcome.budget_spent_cents,
            "peak_concurrency": outcome.peak_concurrency,
        })
        return EffectReceipt(
            command.id, status, proof, outcome.budget_spent_cents, duration_ms,
            attempt_id, attempt_started_at,
        )

    def _invoke(
        self, command: DevHubCommand, authorization: ExecutionAuthorization, *,
        resume: bool, prior_receipt: EffectReceipt | None,
        heartbeat: Callable[[], object],
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt:
        started = time.monotonic()
        authority, _effective, _route = self._route_limits(command)
        reconcile_capacity(authority.observation)
        attempt_id, attempt_started_at = self._attempt_coordinates(
            command, authorization.binding_digest, prior_receipt,
        )
        composition = self._composition(
            command, authorization, heartbeat=heartbeat,
            attempt_id=attempt_id,
        )
        if resume and command.state == "paused":
            # DevHub v1 has no human resume request.  Reconcile the local reason but
            # cross no campaign effect boundary while its passive projection is still
            # paused.  Only an exact Foundry proof may produce the documented
            # paused->running fact; the next scan then resumes the same command effect.
            try:
                ready = composition.coordinator.revalidate_paused_projection(
                    composition.spec,
                )
                outcome = composition.coordinator.current_outcome(composition.spec)
            except CampaignError:
                existing = self.receipts.read(command.id, authorization.binding_digest)
                if existing is not None:
                    return existing
                if prior_receipt is None:
                    raise CommandWorkerError("reçu de reprise campagne absent")
                return prior_receipt
            duration = min(int((time.monotonic() - started) * 1000), 604_800_000)
            effect = self._effect(
                command, outcome, duration,
                attempt_id=attempt_id, attempt_started_at=attempt_started_at,
            )
            if ready:
                if effect.status != "running":
                    raise CommandWorkerError("preuve de reprise campagne contradictoire")
            elif effect.status != "paused":
                # An unproved or human-confirmed pause never becomes active by
                # inference from the overloaded passive projection.
                effect = EffectReceipt(
                    command.id, "paused", effect.proof_digest,
                    effect.cost_cents, effect.duration_ms,
                    attempt_id, attempt_started_at,
                )
            self.receipts.write(authorization.binding_digest, effect)
            return effect

        # Resolve-first after an outer crash: the durable command identity exists
        # before any child effect and is therefore safe to resume, never relaunch.
        running = EffectReceipt(
            command.id, "running", _digest([
                command.id, authorization.binding_digest, "campaign-intent",
            ]), 0, 0,
            attempt_id, attempt_started_at,
        )
        self.receipts.write(authorization.binding_digest, running)
        if resume:
            try:
                row = composition.store.campaign(command.id)
            except CampaignError:
                row = None
            if row is not None and row.state == "suspended":
                # A settled implementation blocker is immutable evidence.  Merely
                # observing the outer running receipt cannot authorize another
                # provider call; the exact local manual-retry command first records
                # ``manual_retry_approved`` against that blocked effect and changes
                # the campaign back to running.  Other resumable observations retain
                # their existing resolve-first behavior.
                if row.reason != "blocker":
                    reason = (
                        "campaign_revalidated" if row.state == "paused"
                        else
                        "host_recovered"
                        if row.reason in {"host_unavailable", "host_or_effect_failure"}
                        else "budget_approved" if row.reason == "budget_exhausted"
                        else "proof_available"
                    )
                    composition.coordinator.resume(command.id, reason)
        outcome = composition.coordinator.run(composition.spec)
        duration = min(int((time.monotonic() - started) * 1000), 604_800_000)
        effect = self._effect(
            command, outcome, duration,
            attempt_id=attempt_id, attempt_started_at=attempt_started_at,
        )
        self.receipts.write(authorization.binding_digest, effect)
        return effect

    def launch(
        self, command: DevHubCommand, authorization: ExecutionAuthorization, *,
        effect_id: str, heartbeat: Callable[[], object],
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt:
        if effect_id != command.id:
            raise CommandWorkerError("identité de campagne contradictoire")
        return self._invoke(
            command, authorization, resume=False, prior_receipt=None,
            heartbeat=heartbeat,
            reconcile_capacity=reconcile_capacity,
        )

    def resume(
        self, command: DevHubCommand, receipt: EffectReceipt,
        authorization: ExecutionAuthorization, *, heartbeat: Callable[[], object],
        reconcile_capacity: Callable[[RevalidationObservation], None],
    ) -> EffectReceipt:
        if (receipt.effect_id != command.id
                or receipt.status not in {"running", "paused"}):
            raise CommandWorkerError("reçu de campagne non reprenable")
        return self._invoke(
            command, authorization, resume=True, prior_receipt=receipt,
            heartbeat=heartbeat,
            reconcile_capacity=reconcile_capacity,
        )
