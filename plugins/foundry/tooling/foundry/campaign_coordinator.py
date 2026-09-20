"""Trusted, durable coordinator for one approved Epic campaign.

The coordinator is deliberately provider neutral.  It owns ordering, leases,
budgets, idempotent effect identities and the normal Foundry gate sequence; an
isolated agent is only allowed to return a bounded implementation proposal.

No prompt, source code, command line or credential is accepted by this module or
stored in its ledger.  Concrete hosts plug in through ``CampaignPipeline`` and
``ImplementationExecutor``.  This keeps DevHub an intention/event store and keeps
all tracker/code-host authority in the Foundry process (FOUNDRY-ADR-0013).
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Iterator, Protocol

from foundry.routing import LEVELS


CAMPAIGN_CONTRACT = "foundry-epic-campaign.v1"
LEDGER_CONTRACT = "foundry-epic-campaign-ledger.v1"
REQUIRED_STEPS = (
    "start", "implementation", "open-pr", "review", "ci", "human-gate", "merge",
)
PARENT_ACCEPTANCE_STEP = "sync-parent-acceptance"
LEGACY_F89_CAMPAIGN_ID = "command_559b71e5-b2b9-4086-ae96-f9ccf650d2e8"
LEGACY_F89_BINDING_DIGEST = "102f5506082bf7bc9dca2dbe5e352404dab9067e9cfefd12883623441a02e747"
LEGACY_F89_PARENT_ID = "F89E2E-9"
LEGACY_F89_CHILDREN = ("F89E2E-10", "F89E2E-11", "F89E2E-12")
LEGACY_F89_BUDGET_SPENT = 39
PIPELINE_STEP_METHODS = {
    "start": "start_issue",
    "open-pr": "open_pr",
    "review": "review",
    "ci": "ci_gate",
    "human-gate": "human_gate",
    "merge": "merge_issue",
    "close-epic": "close_epic",
}
CONTROL_STATES = frozenset({"active", "pause-requested", "cancel-requested"})
CAMPAIGN_STATES = frozenset({
    "scheduled", "running", "paused", "cancelled", "suspended", "completed",
})
RECEIPT_STATUSES = frozenset({
    "completed", "waiting", "blocked", "unavailable", "unknown",
})
APPROVAL_STATES = frozenset({"approved", "revoked", "expired"})
PROPOSAL_OUTCOMES = frozenset({
    "completed", "blocked", "failed", "host-unavailable", "unknown",
})
RETRY_SIGNALS = frozenset({"test_red", "review_blocking", "review_blocking_after_fix"})
RECONCILIATION_STATES = frozenset({"not-engaged", "terminated", "unknown"})
RESUME_REASONS = frozenset({
    "proof_available", "host_recovered", "budget_approved", "manual_retry_approved",
    "campaign_revalidated",
})
_REVALIDATABLE_GATE_REASONS = {
    "review_waiting": "review",
    "review_blocking": "review",
    "ci_waiting": "ci",
    "ci_blocked": "ci",
    "human_gate": "human-gate",
}
BLOCKER_STATES = frozenset({
    "backlog", "ready", "in-progress", "review", "blocked", "done", "dropped",
    "unknown",
})
RESOLVED_BLOCKER_STATES = frozenset({"done", "dropped"})
MAX_CAMPAIGN_ISSUES = 100
MAX_CAMPAIGN_WAVES = 50
MAX_CAMPAIGN_BLOCKERS = 200
MAX_ATTEMPTS = 10
MAX_LEASE_MS = 5 * 60 * 1000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9]{1,15}-[1-9][0-9]*\Z")
_PROJECT = re.compile(r"[A-Z][A-Z0-9]{1,15}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_COMMAND_CAMPAIGN_FIELDS = (
    "command_id", "project", "epic_id", "preview_id", "approval_id",
    "planning_version_id", "preview_digest", "snapshot_digest", "policy_digest",
    "max_concurrency", "scheduled_for",
)


class CampaignError(RuntimeError):
    """A fail-closed campaign contract or ledger error."""


class CampaignLeaseError(CampaignError):
    """Another coordinator owns the non-expired campaign lease."""


class CampaignRevalidationError(CampaignError):
    """The approved binding cannot authorize the next effect."""


_SQLITE_IDENTITY_LOCK = threading.RLock()


def _descriptor_numbers() -> dict[int, tuple[int, int, int]]:
    """Snapshot live descriptor identities for an exact SQLite-open check."""
    try:
        descriptors = {}
        for name in os.listdir("/dev/fd"):
            if not name.isdecimal():
                continue
            descriptor = int(name)
            try:
                observed = os.fstat(descriptor)
            except OSError:
                # ``listdir`` exposes its own already-closed directory handle.
                continue
            descriptors[descriptor] = (
                observed.st_dev, observed.st_ino, observed.st_mode & 0o170000,
            )
        return descriptors
    except OSError as exc:
        raise CampaignError("descripteurs SQLite non verifiables") from exc


def _verify_new_sqlite_descriptor(
    before: dict[int, tuple[int, int, int]], expected: tuple[int, int, int],
) -> None:
    """Prove sqlite retained a newly opened descriptor for the expected inode."""
    for descriptor, identity in _descriptor_numbers().items():
        if identity == expected and before.get(descriptor) != identity:
            return
    raise CampaignError("identite connexion SQLite modifiee")


def _verify_guarded_sqlite_descriptors(
    before: dict[int, tuple[int, int, int]],
    expected: tuple[tuple[int, int, int], ...],
) -> None:
    """Reject any retained regular descriptor outside an anchored SQLite set."""
    allowed = set(expected)
    for descriptor, identity in _descriptor_numbers().items():
        if (before.get(descriptor) != identity and identity[2] == 0o100000
                and identity not in allowed):
            raise CampaignError("identite connexion SQLite modifiee")


@contextmanager
def _sqlite_identity_window(
    required: bool,
) -> Iterator[dict[int, tuple[int, int, int]]]:
    """Keep the descriptor snapshot stable while SQLite opens a guarded path."""
    if not required:
        yield {}
        return
    with _SQLITE_IDENTITY_LOCK:
        # A cyclic sqlite connection can otherwise close after the snapshot and
        # let the guarded open reuse the same descriptor number and inode.
        gc.collect()
        gc_was_enabled = gc.isenabled()
        if gc_was_enabled:
            gc.disable()
        try:
            yield _descriptor_numbers()
        finally:
            if gc_was_enabled:
                gc.enable()


class _CampaignCapacityDeferred(CampaignError):
    """A sibling owns the newly tightened slot; retry in the next bounded batch."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _safe_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} Foundry invalide")
    return value


def _safe_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field} Foundry invalide")
    return value


def _ordered_parent_acceptance_mapping(
    mapping: tuple[tuple[str, str, str], ...],
) -> tuple[tuple[str, str, str], ...]:
    """Project an attested mapping into the parent body's canonical AC order."""
    indexed = []
    for item in mapping:
        match = re.fullmatch(r"ac-([1-9][0-9]*)", item[0])
        if match is None:
            raise ValueError("mapping AC parent non ordonnable")
        indexed.append((int(match.group(1)), item))
    if {index for index, _item in indexed} != set(range(1, len(mapping) + 1)):
        raise ValueError("mapping AC parent non ordonnable")
    return tuple(item for _index, item in sorted(indexed))


@dataclass(frozen=True)
class CampaignSpec:
    """The complete immutable approval envelope used by the coordinator."""

    campaign_id: str
    command_id: str
    project: str
    epic_id: str
    preview_id: str
    approval_id: str
    planning_version_id: str
    preview_digest: str
    snapshot_digest: str
    policy_digest: str
    waves: tuple[tuple[str, ...], ...]
    dependencies: tuple[tuple[str, str], ...]
    max_concurrency: int
    budget_cents: int
    expires_at: int
    scheduled_for: int | None = None
    minimum_tier: str | None = None
    max_attempts_per_issue: int = 3
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _safe_identifier(self.campaign_id, "campagne")
        _safe_identifier(self.command_id, "commande")
        _safe_identifier(self.preview_id, "preview")
        _safe_identifier(self.approval_id, "approbation")
        if not isinstance(self.project, str) or _PROJECT.fullmatch(self.project) is None:
            raise ValueError("projet de campagne invalide")
        if (not isinstance(self.epic_id, str) or _ISSUE_ID.fullmatch(self.epic_id) is None
                or not self.epic_id.startswith(f"{self.project}-")):
            raise ValueError("Epic de campagne invalide")
        _safe_identifier(self.planning_version_id, "version de plan")
        for field in ("preview_digest", "snapshot_digest", "policy_digest"):
            _safe_digest(getattr(self, field), field)
        if (type(self.max_concurrency) is not int
                or not 1 <= self.max_concurrency <= 16):
            raise ValueError("concurrence de campagne hors borne")
        if type(self.budget_cents) is not int or not 1 <= self.budget_cents <= 1_000_000:
            raise ValueError("budget de campagne hors borne")
        if type(self.expires_at) is not int or self.expires_at <= 0:
            raise ValueError("expiration de campagne invalide")
        if (self.scheduled_for is not None
                and (type(self.scheduled_for) is not int or self.scheduled_for < 0
                     or self.scheduled_for >= self.expires_at)):
            raise ValueError("programmation de campagne invalide")
        if self.minimum_tier is not None and self.minimum_tier not in LEVELS:
            raise ValueError("floor de campagne invalide")
        if (type(self.max_attempts_per_issue) is not int
                or not 1 <= self.max_attempts_per_issue <= MAX_ATTEMPTS):
            raise ValueError("tentatives de campagne hors borne")
        if (not isinstance(self.blockers, tuple)
                or len(self.blockers) > MAX_CAMPAIGN_BLOCKERS
                or len(self.blockers) != len(set(self.blockers))
                or any(
                    not isinstance(issue_id, str)
                    or _ISSUE_ID.fullmatch(issue_id) is None
                    or not issue_id.startswith(f"{self.project}-")
                    for issue_id in self.blockers
                )):
            raise ValueError("blockers approuvés de campagne invalides")
        if (not isinstance(self.waves, tuple)
                or not 1 <= len(self.waves) <= MAX_CAMPAIGN_WAVES):
            raise ValueError("vagues de campagne invalides")
        seen: set[str] = set()
        wave_by_issue: dict[str, int] = {}
        for index, wave in enumerate(self.waves):
            if not isinstance(wave, tuple) or not wave or len(wave) > MAX_CAMPAIGN_ISSUES:
                raise ValueError("vague de campagne vide ou hors borne")
            for issue_id in wave:
                if (not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None
                        or not issue_id.startswith(f"{self.project}-")
                        or issue_id == self.epic_id or issue_id in seen):
                    raise ValueError("identité de vague invalide ou dupliquée")
                seen.add(issue_id)
                wave_by_issue[issue_id] = index
        if len(seen) > MAX_CAMPAIGN_ISSUES:
            raise ValueError("campagne au-dessus du plafond d'issues")
        if not isinstance(self.dependencies, tuple):
            raise ValueError("DAG de campagne invalide")
        dependency_keys: set[tuple[str, str]] = set()
        for relation in self.dependencies:
            if (not isinstance(relation, tuple) or len(relation) != 2
                    or relation in dependency_keys):
                raise ValueError("DAG de campagne invalide")
            issue_id, prerequisite = relation
            if issue_id not in seen or prerequisite not in seen or issue_id == prerequisite:
                raise ValueError("DAG de campagne hors scope")
            if wave_by_issue[prerequisite] >= wave_by_issue[issue_id]:
                raise ValueError("ordre de vagues contraire au DAG")
            dependency_keys.add(relation)

    @property
    def binding_digest(self) -> str:
        return _digest({"contract": CAMPAIGN_CONTRACT, **asdict(self)})


@dataclass(frozen=True)
class CampaignObservation:
    """Fresh Foundry-owned authority and capacity facts."""

    campaign_id: str
    command_id: str
    project: str
    epic_id: str
    preview_id: str
    approval_id: str
    approval_state: str
    planning_version_id: str
    preview_digest: str
    snapshot_digest: str
    policy_digest: str
    expires_at: int
    observed_at: int
    valid_until: int
    max_concurrency: int
    budget_remaining_cents: int
    minimum_tier: str | None
    provider_invocation_ceiling_cents: int
    host_available: bool = True
    epic_version: int = 1
    acceptance_mapping: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class BlockerObservation:
    """One fresh, provider-normalized blocker coordinate observed by Foundry."""

    issue_id: str
    state: str
    updated_at: int | None
    version: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.issue_id, str) or _ISSUE_ID.fullmatch(self.issue_id) is None:
            raise ValueError("identité de blocker invalide")
        if self.state not in BLOCKER_STATES:
            raise ValueError("état de blocker invalide")
        for value in (self.updated_at, self.version):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("coordonnée de blocker invalide")

    @property
    def resolved(self) -> bool:
        # A terminal label without any provider freshness coordinate is not proof.
        return (
            self.state in RESOLVED_BLOCKER_STATES
            and (self.updated_at is not None or self.version is not None)
        )

    @property
    def proof_digest(self) -> str:
        return _digest({
            "contract": "foundry-campaign-blocker-observation.v1",
            "issue_id": self.issue_id,
            "state": self.state,
            "updated_at": self.updated_at,
            "version": self.version,
        })


@dataclass(frozen=True)
class AttemptProfile:
    selected_tier: str
    cost_ceiling_cents: int

    def __post_init__(self) -> None:
        if self.selected_tier not in LEVELS:
            raise ValueError("tier de tentative invalide")
        if (type(self.cost_ceiling_cents) is not int
                or not 1 <= self.cost_ceiling_cents <= 1_000_000):
            raise ValueError("plafond de tentative invalide")


@dataclass(frozen=True)
class EffectEnvelope:
    campaign_id: str
    command_id: str
    project: str
    epic_id: str
    issue_id: str
    attempt: int
    step: str
    effect_id: str
    work_id: str
    lease_id: str
    lease_expires_at: int
    binding_digest: str
    snapshot_digest: str
    policy_digest: str
    selected_tier: str | None
    cost_ceiling_cents: int


@dataclass(frozen=True)
class StepReceipt:
    effect_id: str
    status: str
    proof_digest: str

    def __post_init__(self) -> None:
        _safe_identifier(self.effect_id, "effet")
        if self.status not in RECEIPT_STATUSES:
            raise ValueError("statut de reçu invalide")
        _safe_digest(self.proof_digest, "preuve")


@dataclass(frozen=True)
class ParentAcceptanceProof:
    """Campaign-owned identity for the only parent AC synchronization we permit.

    It deliberately contains terminal Foundry merge receipt identities, not a
    DevHub claim.  The tracker may atomically apply the resulting markers but
    never decides that an AC, a review, or a human gate passed.
    """

    campaign_id: str
    parent_id: str
    binding_digest: str
    snapshot_digest: str
    epic_version: int
    children: tuple[tuple[str, str, str], ...]
    mapping: tuple[tuple[str, str, str], ...]

    def __post_init__(self) -> None:
        _safe_identifier(self.campaign_id, "campagne AC parent")
        if not isinstance(self.parent_id, str) or _ISSUE_ID.fullmatch(self.parent_id) is None:
            raise ValueError("parent AC de campagne invalide")
        _safe_digest(self.binding_digest, "binding AC parent")
        _safe_digest(self.snapshot_digest, "snapshot AC parent")
        if type(self.epic_version) is not int or self.epic_version < 1:
            raise ValueError("version AC parent invalide")
        if (not self.children or tuple(item[0] for item in self.children) != tuple(
                sorted(item[0] for item in self.children))):
            raise ValueError("preuves enfants AC parent non canoniques")
        for issue_id, effect_id, proof_digest in self.children:
            if (_ISSUE_ID.fullmatch(issue_id) is None or not isinstance(effect_id, str)
                    or _IDENTIFIER.fullmatch(effect_id) is None):
                raise ValueError("identité de preuve enfant invalide")
            _safe_digest(proof_digest, "preuve enfant AC parent")
        if not self.mapping:
            raise ValueError("mapping AC parent approuve absent")
        criterion_ids: list[str] = []
        mapped_children: list[str] = []
        for criterion_id, criterion_digest, issue_id in self.mapping:
            _safe_identifier(criterion_id, "critere AC parent approuve")
            _safe_digest(criterion_digest, "digest critere AC parent approuve")
            if not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None:
                raise ValueError("enfant AC parent approuve invalide")
            criterion_ids.append(criterion_id)
            mapped_children.append(issue_id)
        if (len(criterion_ids) != len(set(criterion_ids))
                or len(mapped_children) != len(set(mapped_children))
                or set(mapped_children) != {item[0] for item in self.children}):
            raise ValueError("mapping AC parent approuve contradictoire")

    @property
    def proof_id(self) -> str:
        return _digest({
            "contract": "foundry-campaign-parent-acceptance.v1",
            "campaign_id": self.campaign_id, "parent_id": self.parent_id,
            "binding_digest": self.binding_digest, "snapshot_digest": self.snapshot_digest,
            "epic_version": self.epic_version,
            "children": self.children,
            # Preview order is part of the approved mapping contract.  Keeping it
            # here prevents a replay ledger from normalizing away ordered drift.
            "mapping": self.mapping,
        })


@dataclass(frozen=True)
class ParentSnapshotAdvancement:
    """Exact, replayable parent mutation authorized by campaign merge receipts."""

    campaign_id: str
    parent_id: str
    effect_id: str
    binding_digest: str
    snapshot_digest: str
    proof_id: str
    from_version: int
    to_version: int
    from_body_digest: str
    to_body_digest: str
    from_ac_digest: str
    to_ac_digest: str
    criteria: tuple[tuple[str, str, str, str, str], ...]

    def __post_init__(self) -> None:
        _safe_identifier(self.campaign_id, "campagne avancement parent")
        if not isinstance(self.parent_id, str) or _ISSUE_ID.fullmatch(self.parent_id) is None:
            raise ValueError("parent avancement de campagne invalide")
        _safe_identifier(self.effect_id, "effet avancement parent")
        for value, field in (
            (self.binding_digest, "binding avancement parent"),
            (self.snapshot_digest, "snapshot avancement parent"),
            (self.proof_id, "preuve avancement parent"),
            (self.from_body_digest, "corps source avancement parent"),
            (self.to_body_digest, "corps cible avancement parent"),
            (self.from_ac_digest, "AC source avancement parent"),
            (self.to_ac_digest, "AC cible avancement parent"),
        ):
            _safe_digest(value, field)
        if (type(self.from_version) is not int or self.from_version < 1
                or type(self.to_version) is not int
                or self.to_version != self.from_version + 1):
            raise ValueError("versions avancement parent non monotones")
        if not self.criteria:
            raise ValueError("mapping AC parent absent")
        criterion_ids: list[str] = []
        children: list[str] = []
        for criterion_id, criterion_digest, issue_id, effect_id, proof_digest in self.criteria:
            _safe_identifier(criterion_id, "critère avancement parent")
            _safe_digest(criterion_digest, "digest critère avancement parent")
            if not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None:
                raise ValueError("enfant avancement parent invalide")
            _safe_identifier(effect_id, "effet merge avancement parent")
            _safe_digest(proof_digest, "preuve merge avancement parent")
            criterion_ids.append(criterion_id)
            children.append(issue_id)
        if (len(criterion_ids) != len(set(criterion_ids))
                or len(children) != len(set(children))):
            raise ValueError("mapping AC parent dupliqué")

    @property
    def advancement_digest(self) -> str:
        return _digest({
            "contract": "foundry-campaign-parent-snapshot-advancement.v1",
            **asdict(self),
        })


@dataclass(frozen=True)
class LegacyParentRequalification:
    """Audited one-off qualification of the historical F89 v4 -> v6 state.

    This does not manufacture either primitive receipt.  It records a new,
    explicitly named qualification whose evidence is the three durable merge
    receipts plus the provider's existing atomic Epic-closure audit.
    """

    campaign_id: str
    binding_digest: str
    snapshot_digest: str
    parent_id: str
    from_version: int
    acceptance_version: int
    closed_version: int
    budget_spent_cents: int
    criteria: tuple[tuple[str, str, str, str, str], ...]
    parent_body_digest: str
    parent_ac_digest: str
    closure_receipt_digest: str
    actor: str
    recorded_at: int

    def __post_init__(self) -> None:
        if (self.campaign_id != LEGACY_F89_CAMPAIGN_ID
                or self.binding_digest != LEGACY_F89_BINDING_DIGEST
                or self.parent_id != LEGACY_F89_PARENT_ID
                or (self.from_version, self.acceptance_version, self.closed_version) != (4, 5, 6)
                or self.budget_spent_cents != LEGACY_F89_BUDGET_SPENT
                or tuple(sorted(item[2] for item in self.criteria)) != LEGACY_F89_CHILDREN):
            raise ValueError("requalification legacy F89 hors coordonnees")
        for value, field in (
            (self.snapshot_digest, "snapshot legacy F89"),
            (self.parent_body_digest, "corps legacy F89"),
            (self.parent_ac_digest, "AC legacy F89"),
            (self.closure_receipt_digest, "cloture legacy F89"),
        ):
            _safe_digest(value, field)
        if (not isinstance(self.actor, str) or _IDENTIFIER.fullmatch(self.actor) is None
                or type(self.recorded_at) is not int or self.recorded_at < 0):
            raise ValueError("audit legacy F89 invalide")
        criterion_ids: list[str] = []
        for criterion_id, criterion_digest, issue_id, effect_id, proof_digest in self.criteria:
            _safe_identifier(criterion_id, "critere legacy F89")
            _safe_digest(criterion_digest, "digest critere legacy F89")
            if issue_id not in LEGACY_F89_CHILDREN:
                raise ValueError("enfant legacy F89 invalide")
            _safe_identifier(effect_id, "effet merge legacy F89")
            _safe_digest(proof_digest, "recu merge legacy F89")
            criterion_ids.append(criterion_id)
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("mapping legacy F89 duplique")

    @property
    def audit_digest(self) -> str:
        return _digest({
            "contract": "foundry-campaign-legacy-parent-requalification.v1",
            **asdict(self),
        })

    @property
    def evidence_digest(self) -> str:
        """Stable identity of the qualified proof, excluding journal time/order."""
        evidence = asdict(self)
        evidence.pop("recorded_at")
        evidence["criteria"] = tuple(sorted(
            self.criteria, key=lambda item: item[0],
        ))
        return _digest({
            "contract": "foundry-campaign-legacy-parent-requalification-evidence.v1",
            **evidence,
        })


@dataclass(frozen=True)
class ImplementationProposal:
    """Content-free result an isolated agent may return to the coordinator."""

    work_id: str
    issue_id: str
    attempt: int
    outcome: str
    proof_digest: str
    cost_cents: int
    selected_tier: str
    failure_signal: str | None = None

    def __post_init__(self) -> None:
        _safe_identifier(self.work_id, "travail")
        if not isinstance(self.issue_id, str) or _ISSUE_ID.fullmatch(self.issue_id) is None:
            raise ValueError("issue de proposition invalide")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("tentative de proposition invalide")
        if self.outcome not in PROPOSAL_OUTCOMES:
            raise ValueError("résultat de proposition invalide")
        _safe_digest(self.proof_digest, "preuve de proposition")
        if type(self.cost_cents) is not int or not 0 <= self.cost_cents <= 1_000_000:
            raise ValueError("coût de proposition invalide")
        if self.selected_tier not in LEVELS:
            raise ValueError("tier de proposition invalide")
        if self.failure_signal is not None and self.failure_signal not in RETRY_SIGNALS:
            raise ValueError("signal de retry invalide")
        if self.outcome == "failed" and self.failure_signal is None:
            raise ValueError("échec sans signal déterministe")
        if self.outcome != "failed" and self.failure_signal is not None:
            raise ValueError("signal de retry sans échec")


@dataclass(frozen=True)
class ImplementationReconciliation:
    """Fresh, content-free proof about an implementation intent without a receipt."""

    work_id: str
    state: str
    proof_digest: str
    cost_cents: int

    def __post_init__(self) -> None:
        _safe_identifier(self.work_id, "travail réconcilié")
        if self.state not in RECONCILIATION_STATES:
            raise ValueError("état de réconciliation invalide")
        _safe_digest(self.proof_digest, "preuve de réconciliation")
        if (type(self.cost_cents) is not int
                or not 0 <= self.cost_cents <= 1_000_000
                or (self.state == "unknown" and self.cost_cents != 0)):
            raise ValueError("coût de réconciliation invalide")


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    selected_tier: str
    audit_digest: str
    reason: str
    authorization_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool or self.selected_tier not in LEVELS:
            raise ValueError("décision de retry invalide")
        _safe_digest(self.audit_digest, "audit de retry")
        _safe_identifier(self.reason, "raison de retry")
        if self.authorization_digest is not None:
            _safe_digest(self.authorization_digest, "autorisation de retry")
        if self.allowed and self.authorization_digest is None:
            raise ValueError("retry autorisé sans preuve d'autorisation")


@dataclass(frozen=True)
class ReviewRemediationTarget:
    """Exact blocked review coordinates that fresh authority must corroborate."""

    campaign_id: str
    issue_id: str
    attempt: int
    effect_id: str
    proof_digest: str
    binding_digest: str
    role: str
    current_tier: str

    def __post_init__(self) -> None:
        _safe_identifier(self.campaign_id, "campagne review a revalider")
        if not isinstance(self.issue_id, str) or _ISSUE_ID.fullmatch(self.issue_id) is None:
            raise ValueError("issue de review a revalider invalide")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("tentative de review a revalider invalide")
        _safe_identifier(self.effect_id, "effet review a revalider")
        _safe_digest(self.proof_digest, "preuve review a revalider")
        _safe_digest(self.binding_digest, "binding review a revalider")
        if self.role != "implementer" or self.current_tier not in LEVELS:
            raise ValueError("role ou tier de review a revalider invalide")

    @property
    def step(self) -> str:
        return "review"


@dataclass(frozen=True)
class ReviewRemediationAuthorization:
    """Exact human grant for one first blocked campaign review."""

    authorization_digest: str
    campaign_id: str
    issue_id: str
    attempt: int
    effect_id: str
    proof_digest: str
    role: str
    current_tier: str
    actor: str
    expires_at: int

    def __post_init__(self) -> None:
        _safe_digest(self.authorization_digest, "autorisation review campagne")
        _safe_identifier(self.campaign_id, "campagne review autorisée")
        if not isinstance(self.issue_id, str) or _ISSUE_ID.fullmatch(self.issue_id) is None:
            raise ValueError("issue de review autorisée invalide")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("tentative de review autorisée invalide")
        _safe_identifier(self.effect_id, "effet review autorisé")
        _safe_digest(self.proof_digest, "preuve review autorisée")
        if self.role != "implementer" or self.current_tier not in LEVELS:
            raise ValueError("rôle ou tier de review autorisée invalide")
        _safe_identifier(self.actor, "acteur review autorisée")
        if type(self.expires_at) is not int or self.expires_at <= 0:
            raise ValueError("expiration de review autorisée invalide")


class EscalationRetryAuthorizer:
    """Adapter to the existing issue-scoped escalation/remediation ledger."""

    def __init__(
        self, escalation_store, *,
        review_authorization: Callable[
            [EffectEnvelope], ReviewRemediationAuthorization | None
        ] | None = None,
        now_ms: Callable[[], int] | None = None,
    ):
        self.store = escalation_store
        self.review_authorization = review_authorization
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))

    def authorize(
        self, envelope: EffectEnvelope, signal: str, current_tier: str,
    ) -> RetryDecision:
        if signal not in RETRY_SIGNALS or current_tier not in LEVELS:
            raise CampaignError("signal retry hors politique existante")
        authorization = (
            self.review_authorization(envelope)
            if signal == "review_blocking" and self.review_authorization is not None
            else None
        )
        if authorization is not None:
            if (
                not isinstance(authorization, ReviewRemediationAuthorization)
                or authorization.campaign_id != envelope.campaign_id
                or authorization.issue_id != envelope.issue_id
                or authorization.attempt != envelope.attempt
                or authorization.effect_id != envelope.effect_id
                or authorization.current_tier != current_tier
            ):
                raise CampaignError("autorisation exacte de première review invalide")
            result = self.store.consume_campaign_review_remediation(
                envelope.issue_id, current_tier, effect_id=envelope.effect_id,
                expires_at=authorization.expires_at,
                authorization_digest=authorization.authorization_digest,
                now_ms=self.now_ms(),
            )
            if (
                not isinstance(result, dict)
                or type(result.get("allowed")) is not bool
                or result.get("selected_tier") not in LEVELS
                or not isinstance(result.get("audit_digest"), str)
                or _DIGEST.fullmatch(result["audit_digest"]) is None
                or LEVELS.index(result["selected_tier"]) < LEVELS.index(current_tier)
                or (
                    result["allowed"]
                    and (
                        result.get("action") != "remediation_continued"
                        or result.get("authorization_digest")
                        != authorization.authorization_digest
                    )
                )
                or (not result["allowed"]
                    and result.get("action") != "authorization_expired")
            ):
                raise CampaignError("décision de première remédiation invalide")
            return RetryDecision(
                result["allowed"], result["selected_tier"],
                result["audit_digest"], result["action"],
                result.get("authorization_digest") if result["allowed"] else None,
            )
        decision = self.store.record_failure(
            envelope.issue_id, "implementer", signal, current_tier,
            idempotency_key=envelope.effect_id, authorization_aware=True,
        )
        if (not hasattr(decision, "to_dict")
                or decision.final_tier not in LEVELS
                or not isinstance(decision.human_required, bool)
                or LEVELS.index(decision.final_tier) < LEVELS.index(current_tier)
                or LEVELS.index(decision.final_tier) < LEVELS.index(decision.initial_tier)):
            raise CampaignError("décision d'escalade existante invalide")
        allowed = (
            decision.action == "remediation_continued"
            and not decision.human_required
            and isinstance(decision.remediation_authorization, dict)
            and decision.remediation_authorization.get("state") in {"active", "exhausted"}
        )
        return RetryDecision(
            allowed,
            decision.final_tier,
            _digest(decision.to_dict()),
            decision.action,
            _digest(decision.remediation_authorization) if allowed else None,
        )


@dataclass(frozen=True)
class CampaignOutcome:
    campaign_id: str
    state: str
    reason: str | None
    wave_index: int
    completed_issues: tuple[str, ...]
    budget_spent_cents: int
    peak_concurrency: int


class CampaignPipeline(Protocol):
    """Trusted Foundry seam to the existing mechanical pipeline primitives."""

    def observe(self, spec: CampaignSpec) -> CampaignObservation: ...

    def observe_blockers(
        self, spec: CampaignSpec,
    ) -> tuple[BlockerObservation, ...]: ...

    def resolve_effect(
        self, envelope: EffectEnvelope, *,
        engage_merge: Callable[[], None] | None = None,
    ) -> StepReceipt | None: ...

    def start_issue(self, envelope: EffectEnvelope) -> StepReceipt: ...

    def open_pr(self, envelope: EffectEnvelope) -> StepReceipt: ...

    def review(self, envelope: EffectEnvelope) -> StepReceipt: ...

    def ci_gate(self, envelope: EffectEnvelope) -> StepReceipt: ...

    def human_gate(self, envelope: EffectEnvelope) -> StepReceipt: ...

    def merge_issue(
        self, envelope: EffectEnvelope, *, engage: Callable[[], None],
    ) -> StepReceipt: ...

    def close_epic(self, envelope: EffectEnvelope) -> StepReceipt: ...

    def sync_parent_acceptance(
        self, envelope: EffectEnvelope, proof: ParentAcceptanceProof,
        advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt: ...

    def prepare_parent_acceptance(
        self, envelope: EffectEnvelope, proof: ParentAcceptanceProof,
    ) -> ParentSnapshotAdvancement: ...

    def reconcile_parent_acceptance(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt | None: ...

    def verify_parent_acceptance_proof(
        self, proof: ParentAcceptanceProof,
    ) -> None: ...

    def reconcile_close_epic(
        self, envelope: EffectEnvelope, advancement: ParentSnapshotAdvancement,
    ) -> StepReceipt | None: ...

    def verify_legacy_parent_requalification(
        self, record: LegacyParentRequalification,
    ) -> None: ...

    def authorize_retry(
        self, envelope: EffectEnvelope, signal: str, current_tier: str,
    ) -> RetryDecision: ...


class ImplementationExecutor(Protocol):
    """Unprivileged host seam; it never receives service credentials or a pipeline API."""

    def execution_profile(self, issue_id: str, attempt: int) -> AttemptProfile: ...

    def resolve(self, work_id: str) -> ImplementationProposal | None: ...

    def reconcile(self, envelope: EffectEnvelope) -> ImplementationReconciliation: ...

    def execute(self, envelope: EffectEnvelope) -> ImplementationProposal: ...


@dataclass(frozen=True)
class _CampaignRow:
    state: str
    reason: str | None
    control: str
    wave_index: int
    budget_spent: int
    budget_reserved: int
    peak_concurrency: int
    active_concurrency: int
    lease_id: str | None
    lease_owner: str | None
    lease_expires_at: int | None


@dataclass(frozen=True)
class UnlaunchedCampaignProof:
    """Minimal durable fact set for an explicit zero-cost campaign abandonment."""

    campaign_id: str
    binding_digest: str
    proof_digest: str
    observed_at: int

    def __post_init__(self) -> None:
        _safe_identifier(self.campaign_id, "campagne de réconciliation")
        _safe_digest(self.binding_digest, "binding de commande de réconciliation")
        _safe_digest(self.proof_digest, "preuve de réconciliation")
        if type(self.observed_at) is not int or self.observed_at < 0:
            raise ValueError("horodatage de réconciliation invalide")


@dataclass(frozen=True)
class HostUnavailableCampaignProof:
    """Content-free digest of a complete zero-cost host-unavailable ledger."""

    campaign_id: str
    binding_digest: str
    proof_digest: str
    observed_at: int
    budget_cents: int
    max_concurrency: int

    def __post_init__(self) -> None:
        _safe_identifier(self.campaign_id, "campagne host-unavailable")
        _safe_digest(self.binding_digest, "binding de commande host-unavailable")
        _safe_digest(self.proof_digest, "preuve host-unavailable")
        if (
            type(self.observed_at) is not int or self.observed_at < 0
            or type(self.budget_cents) is not int or self.budget_cents < 1
            or type(self.max_concurrency) is not int
            or not 1 <= self.max_concurrency <= 16
        ):
            raise ValueError("horodatage host-unavailable invalide")


@dataclass(frozen=True)
class ReviewWaitingCampaignProof:
    """Content-free proof of a review-blocked campaign with settled implementations."""

    campaign_id: str
    binding_digest: str
    proof_digest: str
    observed_at: int
    spent_cents: int
    budget_cents: int
    max_concurrency: int

    def __post_init__(self) -> None:
        _safe_identifier(self.campaign_id, "campagne review-waiting")
        _safe_digest(self.binding_digest, "binding de commande review-waiting")
        _safe_digest(self.proof_digest, "preuve review-waiting")
        if (
            type(self.observed_at) is not int or self.observed_at < 0
            or type(self.spent_cents) is not int or self.spent_cents < 0
            or type(self.budget_cents) is not int or self.budget_cents < 1
            or self.spent_cents > self.budget_cents
            or type(self.max_concurrency) is not int
            or not 1 <= self.max_concurrency <= 16
        ):
            raise ValueError("preuve review-waiting invalide")


class CampaignStore:
    """Crash-durable ledger containing coordinates and digests, never content."""

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
        self._connection_guard = connection_guard
        self._connection_identity = connection_identity
        self._connection_sidecar_identities = connection_sidecar_identities
        self._lock = threading.RLock()
        self._initialize()

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
                    # campaign query can precede sidecar provenance verification.
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
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                  campaign_id TEXT PRIMARY KEY, contract TEXT NOT NULL,
                  binding_digest TEXT NOT NULL, spec_json TEXT NOT NULL,
                  state TEXT NOT NULL, reason TEXT, control TEXT NOT NULL,
                  wave_index INTEGER NOT NULL, budget_spent INTEGER NOT NULL,
                  budget_reserved INTEGER NOT NULL, peak_concurrency INTEGER NOT NULL,
                  active_concurrency INTEGER NOT NULL, lease_id TEXT, lease_owner TEXT,
                  lease_expires_at INTEGER, updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaign_issues (
                  campaign_id TEXT NOT NULL, issue_id TEXT NOT NULL,
                  wave_index INTEGER NOT NULL, attempt INTEGER NOT NULL,
                  next_step TEXT NOT NULL, state TEXT NOT NULL,
                  selected_tier TEXT, engaged_merge INTEGER NOT NULL DEFAULT 0,
                  merge_dispatch_started INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY (campaign_id, issue_id),
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_effects (
                  campaign_id TEXT NOT NULL, issue_id TEXT NOT NULL,
                  attempt INTEGER NOT NULL, step TEXT NOT NULL, effect_id TEXT NOT NULL,
                  work_id TEXT NOT NULL, status TEXT NOT NULL, proof_digest TEXT,
                  cost_ceiling INTEGER NOT NULL, cost_cents INTEGER,
                  selected_tier TEXT, retry_audit_digest TEXT,
                  engagement_state TEXT NOT NULL DEFAULT 'not-applicable',
                  PRIMARY KEY (campaign_id, issue_id, attempt, step),
                  UNIQUE (effect_id), UNIQUE (work_id),
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_retry_decisions (
                  audit_digest TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
                  issue_id TEXT NOT NULL, attempt INTEGER NOT NULL, step TEXT NOT NULL,
                  allowed INTEGER NOT NULL, selected_tier TEXT NOT NULL,
                  reason TEXT NOT NULL, authorization_digest TEXT,
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_resume_audits (
                  campaign_id TEXT NOT NULL, issue_id TEXT NOT NULL,
                  from_attempt INTEGER NOT NULL, to_attempt INTEGER NOT NULL,
                  reason TEXT NOT NULL, effect_id TEXT NOT NULL,
                  proof_digest TEXT NOT NULL, authorization_digest TEXT,
                  created_at INTEGER NOT NULL,
                  PRIMARY KEY (campaign_id, issue_id, from_attempt, reason),
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_manual_retry_authorizations (
                  authorization_digest TEXT PRIMARY KEY,
                  campaign_id TEXT NOT NULL, issue_id TEXT NOT NULL,
                  from_attempt INTEGER NOT NULL, effect_id TEXT NOT NULL,
                  proof_digest TEXT NOT NULL, actor TEXT NOT NULL,
                  created_at INTEGER NOT NULL,
                  UNIQUE (campaign_id, issue_id, from_attempt),
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_review_remediation_authorizations (
                  authorization_digest TEXT PRIMARY KEY,
                  campaign_id TEXT NOT NULL, issue_id TEXT NOT NULL,
                  from_attempt INTEGER NOT NULL, effect_id TEXT NOT NULL,
                  proof_digest TEXT NOT NULL, role TEXT NOT NULL,
                  current_tier TEXT NOT NULL, actor TEXT NOT NULL,
                  expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
                  UNIQUE (campaign_id, issue_id, from_attempt),
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_blocker_observations (
                  campaign_id TEXT NOT NULL, blocker_id TEXT NOT NULL,
                  issue_state TEXT NOT NULL, issue_updated_at INTEGER,
                  issue_version INTEGER, resolved INTEGER NOT NULL,
                  proof_digest TEXT NOT NULL, observed_at INTEGER NOT NULL,
                  PRIMARY KEY (campaign_id, blocker_id, proof_digest),
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_parent_snapshot_advancements (
                  campaign_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL,
                  effect_id TEXT NOT NULL UNIQUE, advancement_digest TEXT NOT NULL,
                  advancement_json TEXT NOT NULL, status TEXT NOT NULL,
                  receipt_digest TEXT, created_at INTEGER NOT NULL,
                  completed_at INTEGER,
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                CREATE TABLE IF NOT EXISTS campaign_legacy_parent_requalifications (
                  campaign_id TEXT PRIMARY KEY, audit_digest TEXT NOT NULL UNIQUE,
                  audit_json TEXT NOT NULL, recorded_at INTEGER NOT NULL,
                  FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id)
                );
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(campaign_effects)")
            }
            if "engagement_state" not in columns:
                connection.execute(
                    "ALTER TABLE campaign_effects ADD COLUMN engagement_state TEXT "
                    "NOT NULL DEFAULT 'unknown'"
                )
            issue_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(campaign_issues)")
            }
            if "merge_dispatch_started" not in issue_columns:
                connection.execute(
                    "ALTER TABLE campaign_issues ADD COLUMN merge_dispatch_started "
                    "INTEGER NOT NULL DEFAULT 0"
                )
                # Legacy ``engaged_merge`` meant the pre-dispatch recovery
                # boundary.  Preserve that meaning for an in-flight campaign
                # created before this finer-grained marker existed.
                connection.execute(
                    "UPDATE campaign_issues SET merge_dispatch_started = 1 "
                    "WHERE engaged_merge = 1 AND state != 'merged'"
                )
            resume_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(campaign_resume_audits)")
            }
            if "authorization_digest" not in resume_columns:
                connection.execute(
                    "ALTER TABLE campaign_resume_audits ADD COLUMN authorization_digest TEXT"
                )
        if self._connection_guard is None:
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    @staticmethod
    def _spec_payload(spec: CampaignSpec) -> dict:
        payload = asdict(spec)
        payload["waves"] = [list(wave) for wave in spec.waves]
        payload["dependencies"] = [list(edge) for edge in spec.dependencies]
        return {"contract": CAMPAIGN_CONTRACT, **payload}

    def initialize(self, spec: CampaignSpec, *, now_ms: int) -> None:
        payload = self._spec_payload(spec)
        encoded = _canonical(payload).decode("utf-8")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT contract, binding_digest, spec_json FROM campaigns WHERE campaign_id = ?",
                (spec.campaign_id,),
            ).fetchone()
            if existing is None:
                initial_state = (
                    "scheduled" if spec.scheduled_for is not None and spec.scheduled_for > now_ms
                    else "running"
                )
                connection.execute(
                    "INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, NULL, 'active', 0, 0, 0, "
                    "0, 0, NULL, NULL, NULL, ?)",
                    (spec.campaign_id, LEDGER_CONTRACT, spec.binding_digest, encoded,
                     initial_state, now_ms),
                )
                for index, wave in enumerate(spec.waves):
                    for issue_id in wave:
                        connection.execute(
                            "INSERT INTO campaign_issues "
                            "(campaign_id, issue_id, wave_index, attempt, next_step, state, "
                            "selected_tier, engaged_merge) VALUES (?, ?, ?, 1, 'start', "
                            "'pending', NULL, 0)",
                            (spec.campaign_id, issue_id, index),
                        )
            elif existing != (LEDGER_CONTRACT, spec.binding_digest, encoded):
                connection.rollback()
                raise CampaignError("binding durable de campagne modifié")
            connection.commit()

    @staticmethod
    def _campaign_row(row) -> _CampaignRow:
        if row is None:
            raise CampaignError("campagne durable absente")
        result = _CampaignRow(*row)
        if (result.state not in CAMPAIGN_STATES or result.control not in CONTROL_STATES
                or min(result.wave_index, result.budget_spent, result.budget_reserved,
                       result.peak_concurrency, result.active_concurrency) < 0):
            raise CampaignError("ledger de campagne corrompu")
        return result

    def campaign(self, campaign_id: str) -> _CampaignRow:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state, reason, control, wave_index, budget_spent, budget_reserved, "
                "peak_concurrency, active_concurrency, lease_id, lease_owner, "
                "lease_expires_at FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        return self._campaign_row(row)

    def campaign_snapshot_digest(self, campaign_id: str) -> str:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT spec_json FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        try:
            value = json.loads(row[0])["snapshot_digest"] if row is not None else None
            return _safe_digest(value, "snapshot campagne")
        except (TypeError, KeyError, json.JSONDecodeError, ValueError):
            raise CampaignError("snapshot durable de campagne invalide") from None

    def campaign_spec(self, campaign_id: str) -> CampaignSpec:
        """Return the exact immutable campaign envelope stored in the ledger."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT contract, binding_digest, spec_json FROM campaigns "
                "WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        try:
            contract, binding_digest, encoded = row
            raw = json.loads(encoded)
            if set(raw) != {"contract", *CampaignSpec.__dataclass_fields__}:
                raise ValueError
            raw_contract = raw.pop("contract")
            raw["blockers"] = tuple(raw["blockers"])
            raw["waves"] = tuple(tuple(wave) for wave in raw["waves"])
            raw["dependencies"] = tuple(tuple(edge) for edge in raw["dependencies"])
            spec = CampaignSpec(**raw)
        except (TypeError, KeyError, json.JSONDecodeError, ValueError):
            raise CampaignError("binding durable de campagne invalide") from None
        if (contract != LEDGER_CONTRACT or raw_contract != CAMPAIGN_CONTRACT
                or spec.campaign_id != campaign_id
                or binding_digest != spec.binding_digest
                or binding_digest != _digest(json.loads(encoded))):
            raise CampaignError("binding durable de campagne invalide")
        return spec

    def unlaunched_reservation_proof(
        self, campaign_id: str, binding_digest: str, *, observed_at: int,
    ) -> UnlaunchedCampaignProof:
        """Prove a suspended campaign never created an implementation intent.

        The proof is deliberately narrower than a general cancellation: it accepts only
        the pre-provider failure state with no spent/reserved budget, no selected tier,
        and no primitive beyond ``start``. Any uncertain or partially executed campaign
        remains reserved for ordinary resolve-first recovery.
        """
        _safe_identifier(campaign_id, "campagne de réconciliation")
        _safe_digest(binding_digest, "binding de commande de réconciliation")
        if type(observed_at) is not int or observed_at < 0:
            raise ValueError("horodatage de réconciliation invalide")
        with self._lock, self._connect() as connection:
            campaign = connection.execute(
                "SELECT contract, binding_digest, spec_json, state, reason, budget_spent, "
                "budget_reserved, peak_concurrency, active_concurrency, lease_expires_at "
                "FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            issue_count, selected_count = connection.execute(
                "SELECT count(*), count(selected_tier) FROM campaign_issues "
                "WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            non_start_effects = connection.execute(
                "SELECT count(*) FROM campaign_effects WHERE campaign_id = ? "
                "AND step <> 'start'",
                (campaign_id,),
            ).fetchone()[0]
        if campaign is None:
            raise CampaignError("campagne de réconciliation absente")
        (
            contract, durable_binding, raw_spec, state, reason, budget_spent,
            budget_reserved, peak_concurrency, active_concurrency, lease_expires_at,
        ) = campaign
        stored_spec = None
        try:
            stored_spec = json.loads(raw_spec)
            stored_command_id = stored_spec["command_id"]
            stored_contract = stored_spec["contract"]
            stored_binding = _digest(stored_spec)
        except (TypeError, ValueError, KeyError):
            stored_command_id = None
            stored_contract = None
            stored_binding = None
        if (
            contract != LEDGER_CONTRACT
            or not isinstance(durable_binding, str)
            or _DIGEST.fullmatch(durable_binding) is None
            or stored_contract != CAMPAIGN_CONTRACT
            or stored_binding != durable_binding
            or stored_command_id != campaign_id
            or state != "suspended"
            or reason != "host_or_effect_failure"
            or issue_count < 1
            or selected_count != 0
            or non_start_effects != 0
            or any(value != 0 for value in (
                budget_spent, budget_reserved, peak_concurrency, active_concurrency,
            ))
            or type(lease_expires_at) is not int
            or lease_expires_at >= observed_at
        ):
            raise CampaignError("preuve de campagne non lancée absente")
        material = {
            "contract": "foundry-campaign-unlaunched-proof.v1",
            "campaign_id": campaign_id,
            "command_binding_digest": binding_digest,
            "campaign_binding_digest": durable_binding,
            "state": state,
            "reason": reason,
            "issue_count": issue_count,
            "selected_tier_count": selected_count,
            "non_start_effect_count": non_start_effects,
            "budget_spent_cents": budget_spent,
            "budget_reserved_cents": budget_reserved,
            "peak_concurrency": peak_concurrency,
            "active_concurrency": active_concurrency,
            "lease_expires_at": lease_expires_at,
        }
        return UnlaunchedCampaignProof(
            campaign_id, binding_digest, _digest(material), observed_at,
        )

    def host_unavailable_reservation_proof(
        self, campaign_id: str, binding_digest: str, *, receipts_path: str | os.PathLike,
        expected_command: dict[str, object], observed_at: int,
    ) -> HostUnavailableCampaignProof:
        """Prove every launched effect stopped at zero-cost host unavailability.

        This contract is intentionally distinct from ``unlaunched_reservation_proof``:
        start and implementation intents exist, so both the coordinator ledger and the
        trusted primitive receipt ledger must form one complete, matching snapshot.
        Any retry lineage, downstream primitive, incomplete row or contradictory digest
        keeps the host reservation active.
        """
        _safe_identifier(campaign_id, "campagne host-unavailable")
        _safe_digest(binding_digest, "binding de commande host-unavailable")
        if (
            type(expected_command) is not dict
            or set(expected_command) != set(_COMMAND_CAMPAIGN_FIELDS)
        ):
            raise ValueError("coordonnées de commande host-unavailable invalides")
        if type(observed_at) is not int or observed_at < 0:
            raise ValueError("horodatage host-unavailable invalide")
        receipt_candidate = Path(receipts_path).expanduser()
        if receipt_candidate.is_symlink():
            raise CampaignError("ledger de reçus host-unavailable absent")
        receipt_file = receipt_candidate.resolve()
        if not receipt_file.is_file():
            raise CampaignError("ledger de reçus host-unavailable absent")

        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    "ATTACH DATABASE ? AS primitive",
                    (str(receipt_file),),
                )
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
                campaign = connection.execute(
                    "SELECT contract, binding_digest, spec_json, state, reason, control, "
                    "wave_index, budget_spent, budget_reserved, peak_concurrency, "
                    "active_concurrency, lease_expires_at, updated_at FROM campaigns "
                    "WHERE campaign_id = ?",
                    (campaign_id,),
                ).fetchone()
                issues = connection.execute(
                    "SELECT issue_id, wave_index, attempt, next_step, state, selected_tier, "
                    "engaged_merge, merge_dispatch_started FROM campaign_issues "
                    "WHERE campaign_id = ? ORDER BY issue_id",
                    (campaign_id,),
                ).fetchall()
                effects = connection.execute(
                    "SELECT issue_id, attempt, step, effect_id, work_id, status, "
                    "proof_digest, cost_ceiling, cost_cents, selected_tier, "
                    "retry_audit_digest, engagement_state FROM campaign_effects "
                    "WHERE campaign_id = ? ORDER BY issue_id, attempt, step",
                    (campaign_id,),
                ).fetchall()
                auxiliary_counts = tuple(
                    connection.execute(
                        f"SELECT count(*) FROM {table} WHERE campaign_id = ?",
                        (campaign_id,),
                    ).fetchone()[0]
                    for table in (
                        "campaign_retry_decisions", "campaign_resume_audits",
                        "campaign_manual_retry_authorizations",
                        "campaign_review_remediation_authorizations",
                        "campaign_blocker_observations",
                    )
                )
                primitive_receipts = connection.execute(
                    "SELECT effect_id, binding_digest, campaign_id, issue_id, attempt, "
                    "step, status, proof_digest, pr_number, head_sha, base_sha, updated_at "
                    "FROM primitive.primitive_receipts WHERE campaign_id = ? "
                    "ORDER BY issue_id, attempt, step",
                    (campaign_id,),
                ).fetchall()
                implementation_intents = connection.execute(
                    "SELECT campaign_id, issue_id, attempt, effect_id, work_id, "
                    "binding_digest, outcome, head_sha, tree_sha, proof_digest, created_at "
                    "FROM primitive.implementation_intents WHERE campaign_id = ? "
                    "ORDER BY issue_id, attempt",
                    (campaign_id,),
                ).fetchall()
                approval_count = sum(
                    connection.execute(
                        f"SELECT count(*) FROM primitive.{table}",
                    ).fetchone()[0]
                    for table in ("human_approvals", "human_approval_events")
                )
                connection.commit()
        except (OSError, sqlite3.DatabaseError):
            raise CampaignError("ledger host-unavailable illisible ou tronqué") from None

        if campaign is None:
            raise CampaignError("campagne host-unavailable absente")
        (
            contract, durable_binding, raw_spec, state, reason, control, wave_index,
            budget_spent, budget_reserved, peak_concurrency, active_concurrency,
            lease_expires_at, updated_at,
        ) = campaign
        stored_spec = None
        try:
            stored_spec = json.loads(raw_spec)
            spec_values = {key: value for key, value in stored_spec.items() if key != "contract"}
            spec_values["waves"] = tuple(tuple(wave) for wave in spec_values["waves"])
            spec_values["dependencies"] = tuple(
                tuple(edge) for edge in spec_values["dependencies"]
            )
            spec_values["blockers"] = tuple(spec_values.get("blockers", ()))
            stored_campaign = CampaignSpec(**spec_values)
        except (TypeError, ValueError, KeyError):
            stored_campaign = None
        if (
            contract != LEDGER_CONTRACT
            or not isinstance(stored_spec, dict)
            or stored_spec.get("contract") != CAMPAIGN_CONTRACT
        ):
            raise CampaignError("contrat host-unavailable durable invalide")
        if (
            stored_campaign is None
            or durable_binding != stored_campaign.binding_digest
            or durable_binding != _digest(stored_spec)
            or stored_campaign.command_id != campaign_id
            or stored_campaign.campaign_id != campaign_id
            or not isinstance(durable_binding, str)
            or _DIGEST.fullmatch(durable_binding) is None
        ):
            raise CampaignError("binding host-unavailable durable invalide")
        stored_command = {
            field: getattr(stored_campaign, field)
            for field in _COMMAND_CAMPAIGN_FIELDS
        }
        if stored_command != expected_command:
            raise CampaignError("binding commande/campagne host-unavailable contradictoire")
        if (
            state != "suspended" or reason != "host_unavailable" or control != "active"
            or type(wave_index) is not int
            or not 0 <= wave_index < len(stored_campaign.waves)
            or budget_spent != 0 or budget_reserved != 0 or active_concurrency != 0
            or type(peak_concurrency) is not int
            or not 1 <= peak_concurrency <= stored_campaign.max_concurrency
            or type(lease_expires_at) is not int or lease_expires_at >= observed_at
            or type(updated_at) is not int or not 0 <= updated_at <= observed_at
        ):
            raise CampaignError("état host-unavailable non réconciliable")
        if any(auxiliary_counts) or approval_count:
            raise CampaignError("effet aval ou lignée de reprise host-unavailable présent")

        expected_wave = {
            issue_id: index
            for index, wave in enumerate(stored_campaign.waves)
            for issue_id in wave
        }
        if len(issues) != len(expected_wave) or {row[0] for row in issues} != set(expected_wave):
            raise CampaignError("ledger d issues host-unavailable incomplet")
        started: dict[str, str] = {}
        for row in issues:
            issue_id, issue_wave, attempt, next_step, issue_state, tier, merge, dispatch = row
            pending = (
                attempt == 1 and next_step == "start" and issue_state == "pending"
                and tier is None
            )
            unavailable = (
                attempt == 1 and next_step == "implementation"
                and issue_state == "start-completed" and tier in LEVELS
            )
            if (
                issue_wave != expected_wave[issue_id]
                or not (pending or unavailable)
                or merge != 0 or dispatch != 0
            ):
                raise CampaignError("état d issue host-unavailable ambigu")
            if unavailable:
                started[issue_id] = tier
        if not started:
            raise CampaignError("aucun effet host-unavailable lancé")

        effects_by_issue: dict[str, dict[str, tuple]] = {issue_id: {} for issue_id in started}
        for row in effects:
            issue_id, attempt, step = row[:3]
            if issue_id not in started or attempt != 1 or step not in {"start", "implementation"}:
                raise CampaignError("effet aval host-unavailable présent")
            if step in effects_by_issue[issue_id]:
                raise CampaignError("effet host-unavailable dupliqué")
            effects_by_issue[issue_id][step] = row
        if any(set(rows) != {"start", "implementation"} for rows in effects_by_issue.values()):
            raise CampaignError("ledger d effets host-unavailable incomplet")

        start_receipts = {row[0]: row for row in primitive_receipts}
        intents = {(row[1], row[2]): row for row in implementation_intents}
        if (
            len(start_receipts) != len(started)
            or len(intents) != len(started)
            or len(start_receipts) != len(primitive_receipts)
            or len(intents) != len(implementation_intents)
        ):
            raise CampaignError("preuve host-unavailable tronquée")
        for issue_id, tier in started.items():
            start = effects_by_issue[issue_id]["start"]
            implementation = effects_by_issue[issue_id]["implementation"]
            if (
                not isinstance(start[3], str) or _IDENTIFIER.fullmatch(start[3]) is None
                or not isinstance(start[4], str) or _IDENTIFIER.fullmatch(start[4]) is None
                or start[5] != "completed" or not isinstance(start[6], str)
                or _DIGEST.fullmatch(start[6]) is None or start[7:] != (0, None, None, None, "not-applicable")
                or not isinstance(implementation[3], str)
                or _IDENTIFIER.fullmatch(implementation[3]) is None
                or not isinstance(implementation[4], str)
                or _IDENTIFIER.fullmatch(implementation[4]) is None
                or implementation[5] != "host-unavailable"
                or not isinstance(implementation[6], str)
                or _DIGEST.fullmatch(implementation[6]) is None
                or type(implementation[7]) is not int or implementation[7] < 1
                or implementation[8:] != (0, tier, None, "settled")
            ):
                raise CampaignError("effet host-unavailable contradictoire")
            primitive = start_receipts.get(start[3])
            if primitive is None:
                raise CampaignError("reçu start host-unavailable contradictoire")
            expected_primitive = (
                start[3], durable_binding, campaign_id, issue_id, 1, "start",
                "completed", start[6], None, None, None,
            )
            if (
                primitive[:11] != expected_primitive
                or type(primitive[11]) is not int
                or not 0 <= primitive[11] <= observed_at
            ):
                raise CampaignError("reçu start host-unavailable contradictoire")
            intent = intents.get((issue_id, 1))
            if (
                intent is None
                or intent[:7] != (
                    campaign_id, issue_id, 1, implementation[3], implementation[4],
                    durable_binding, "host-unavailable",
                )
                or not isinstance(intent[7], str) or _SHA.fullmatch(intent[7]) is None
                or not isinstance(intent[8], str) or _SHA.fullmatch(intent[8]) is None
                or intent[9] != implementation[6]
                or type(intent[10]) is not int or not 0 <= intent[10] <= observed_at
            ):
                raise CampaignError("intent host-unavailable contradictoire")

        material = {
            "contract": "foundry-campaign-host-unavailable-proof.v1",
            "campaign_id": campaign_id,
            "command_binding_digest": binding_digest,
            "campaign_binding_digest": durable_binding,
            "state": state,
            "reason": reason,
            "wave_index": wave_index,
            "budget_spent_cents": budget_spent,
            "budget_reserved_cents": budget_reserved,
            "peak_concurrency": peak_concurrency,
            "active_concurrency": active_concurrency,
            "lease_expires_at": lease_expires_at,
            "campaign_updated_at": updated_at,
            "issues": issues,
            "effects": effects,
            "primitive_receipts": primitive_receipts,
            "implementation_intents": implementation_intents,
            "auxiliary_counts": auxiliary_counts,
        }
        return HostUnavailableCampaignProof(
            campaign_id, binding_digest, _digest(material), observed_at,
            stored_campaign.budget_cents, stored_campaign.max_concurrency,
        )

    @staticmethod
    def _review_waiting_receipt_file(receipts_path: str | os.PathLike) -> Path:
        receipt_candidate = Path(receipts_path).expanduser()
        if receipt_candidate.is_symlink():
            raise CampaignError("ledger de reçus review-waiting absent")
        receipt_file = receipt_candidate.resolve()
        if not receipt_file.is_file():
            raise CampaignError("ledger de reçus review-waiting absent")
        return receipt_file

    @staticmethod
    def _review_waiting_reservation_snapshot(
        connection: sqlite3.Connection, campaign_id: str,
    ) -> tuple[object, object, object, object, object, object, object, object]:
        """Read every immutable fact used by the review-waiting proof."""
        campaign = connection.execute(
            "SELECT contract, binding_digest, spec_json, state, reason, control, "
            "wave_index, budget_spent, budget_reserved, peak_concurrency, "
            "active_concurrency, lease_expires_at, updated_at FROM campaigns "
            "WHERE campaign_id = ?", (campaign_id,),
        ).fetchone()
        issues = connection.execute(
            "SELECT issue_id, wave_index, attempt, next_step, state, selected_tier, "
            "engaged_merge, merge_dispatch_started FROM campaign_issues "
            "WHERE campaign_id = ? ORDER BY issue_id", (campaign_id,),
        ).fetchall()
        effects = connection.execute(
            "SELECT issue_id, attempt, step, effect_id, work_id, status, "
            "proof_digest, cost_ceiling, cost_cents, selected_tier, "
            "retry_audit_digest, engagement_state FROM campaign_effects "
            "WHERE campaign_id = ? ORDER BY issue_id, attempt, step", (campaign_id,),
        ).fetchall()
        primitive_receipts = connection.execute(
            "SELECT effect_id, binding_digest, campaign_id, issue_id, attempt, "
            "step, status, proof_digest, pr_number, head_sha, base_sha, updated_at "
            "FROM primitive.primitive_receipts WHERE campaign_id = ? "
            "ORDER BY issue_id, attempt, step", (campaign_id,),
        ).fetchall()
        implementation_intents = connection.execute(
            "SELECT campaign_id, issue_id, attempt, effect_id, work_id, "
            "binding_digest, outcome, head_sha, tree_sha, proof_digest, created_at "
            "FROM primitive.implementation_intents WHERE campaign_id = ? "
            "ORDER BY issue_id, attempt", (campaign_id,),
        ).fetchall()
        retry_decisions = connection.execute(
            "SELECT audit_digest, campaign_id, issue_id, attempt, step, "
            "allowed, selected_tier, reason, authorization_digest "
            "FROM campaign_retry_decisions WHERE campaign_id = ? "
            "ORDER BY issue_id, attempt, step, audit_digest", (campaign_id,),
        ).fetchall()
        auxiliary_counts = tuple(
            connection.execute(
                f"SELECT count(*) FROM {table} WHERE campaign_id = ?", (campaign_id,),
            ).fetchone()[0]
            for table in (
                "campaign_resume_audits",
                "campaign_manual_retry_authorizations",
                "campaign_review_remediation_authorizations",
                "campaign_blocker_observations",
                "campaign_parent_snapshot_advancements",
                "campaign_legacy_parent_requalifications",
            )
        )
        approval_count = sum(
            connection.execute(f"SELECT count(*) FROM primitive.{table}").fetchone()[0]
            for table in ("human_approvals", "human_approval_events")
        )
        return (
            campaign, issues, effects, primitive_receipts, implementation_intents,
            retry_decisions, auxiliary_counts, approval_count,
        )

    def _review_waiting_reservation_proof_from_snapshot(
        self, campaign_id: str, binding_digest: str, *,
        expected_command: dict[str, object], observed_at: int,
        snapshot: tuple[object, object, object, object, object, object, object, object],
    ) -> ReviewWaitingCampaignProof:
        """Validate one closed-world campaign snapshot without mutating it."""
        (
            campaign, issues, effects, primitive_receipts, implementation_intents,
            retry_decisions, auxiliary_counts, approval_count,
        ) = snapshot
        if campaign is None:
            raise CampaignError("campagne review-waiting absente")
        (
            contract, durable_binding, raw_spec, state, reason, control, wave_index,
            budget_spent, budget_reserved, peak_concurrency, active_concurrency,
            lease_expires_at, updated_at,
        ) = campaign
        try:
            stored_spec = json.loads(raw_spec)
            spec_values = {key: value for key, value in stored_spec.items() if key != "contract"}
            spec_values["waves"] = tuple(tuple(wave) for wave in spec_values["waves"])
            spec_values["dependencies"] = tuple(tuple(edge) for edge in spec_values["dependencies"])
            spec_values["blockers"] = tuple(spec_values.get("blockers", ()))
            stored_campaign = CampaignSpec(**spec_values)
        except (TypeError, ValueError, KeyError):
            stored_spec, stored_campaign = None, None
        if (
            contract != LEDGER_CONTRACT or not isinstance(stored_spec, dict)
            or stored_spec.get("contract") != CAMPAIGN_CONTRACT
            or stored_campaign is None or durable_binding != stored_campaign.binding_digest
            or durable_binding != _digest(stored_spec)
            or stored_campaign.command_id != campaign_id
            or stored_campaign.campaign_id != campaign_id
            or not isinstance(durable_binding, str) or _DIGEST.fullmatch(durable_binding) is None
        ):
            raise CampaignError("binding review-waiting durable invalide")
        stored_command = {field: getattr(stored_campaign, field) for field in _COMMAND_CAMPAIGN_FIELDS}
        if stored_command != expected_command:
            raise CampaignError("binding commande/campagne review-waiting contradictoire")
        if (
            state != "suspended" or reason != "review_waiting" or control != "active"
            or type(wave_index) is not int or not 0 <= wave_index < len(stored_campaign.waves)
            or type(budget_spent) is not int or not 0 <= budget_spent <= stored_campaign.budget_cents
            or budget_reserved != 0 or active_concurrency != 0
            or type(peak_concurrency) is not int
            or not 1 <= peak_concurrency <= stored_campaign.max_concurrency
            or type(lease_expires_at) is not int or lease_expires_at >= observed_at
            or type(updated_at) is not int or not 0 <= updated_at <= observed_at
            or any(auxiliary_counts)
            or approval_count
        ):
            raise CampaignError("état review-waiting non réconciliable")
        expected_wave = {
            issue_id: index for index, wave in enumerate(stored_campaign.waves) for issue_id in wave
        }
        if len(issues) != len(expected_wave) or {row[0] for row in issues} != set(expected_wave):
            raise CampaignError("ledger d issues review-waiting incomplet")
        waiting: dict[str, str] = {}
        for issue_id, issue_wave, attempt, next_step, issue_state, tier, merge, dispatch in issues:
            pending = attempt == 1 and next_step == "start" and issue_state == "pending" and tier is None
            review_waiting = (
                attempt == 1 and next_step == "review" and issue_state == "open-pr-completed"
                and tier in LEVELS
            )
            if (
                issue_wave != expected_wave[issue_id]
                or not (pending or review_waiting)
                or issue_wave < wave_index
                or (pending and issue_wave <= wave_index)
                or (review_waiting and issue_wave != wave_index)
                or merge != 0 or dispatch != 0
            ):
                raise CampaignError("état d issue review-waiting ambigu")
            if review_waiting:
                waiting[issue_id] = tier
        if not waiting:
            raise CampaignError("aucune implémentation review-waiting soldée")
        effects_by_issue: dict[str, dict[str, tuple]] = {issue_id: {} for issue_id in waiting}
        for row in effects:
            issue_id, attempt, step = row[:3]
            if issue_id not in waiting or attempt != 1 or step not in {"start", "implementation", "open-pr", "review"}:
                raise CampaignError("effet aval review-waiting présent")
            if step in effects_by_issue[issue_id]:
                raise CampaignError("effet review-waiting dupliqué")
            effects_by_issue[issue_id][step] = row
        if any(
            set(rows) != {"start", "implementation", "open-pr", "review"}
            for rows in effects_by_issue.values()
        ):
            raise CampaignError("ledger d effets review-waiting incomplet")
        primitives = {row[0]: row for row in primitive_receipts}
        intents = {(row[1], row[2]): row for row in implementation_intents}
        if (
            len(primitives) != len(waiting) * 3
            or len(primitives) != len(primitive_receipts)
            or len(intents) != len(waiting) or len(intents) != len(implementation_intents)
        ):
            raise CampaignError("preuve review-waiting tronquée")
        if retry_decisions:
            raise CampaignError("décision review-waiting contradictoire")
        spent = 0
        for issue_id, tier in waiting.items():
            start = effects_by_issue[issue_id]["start"]
            implementation = effects_by_issue[issue_id]["implementation"]
            open_pr = effects_by_issue[issue_id]["open-pr"]
            review = effects_by_issue[issue_id]["review"]
            if (
                start[5] != "completed" or start[7:] != (0, None, None, None, "not-applicable")
                or implementation[5] != "completed" or not isinstance(implementation[7], int)
                or implementation[7] < 1 or not isinstance(implementation[8], int)
                or not 0 <= implementation[8] <= implementation[7]
                or implementation[9:] != (tier, None, "settled")
                or open_pr[5] != "completed" or open_pr[7:] != (0, None, None, None, "not-applicable")
            ):
                raise CampaignError("effet review-waiting contradictoire")
            if (
                review[5] != "waiting"
                or review[10] is not None
                or review[7:10] != (0, None, None)
                or review[11] != "not-applicable"
            ):
                raise CampaignError("effet review-waiting contradictoire")
            for effect in (start, implementation, open_pr, review):
                if (not isinstance(effect[3], str) or _IDENTIFIER.fullmatch(effect[3]) is None
                        or not isinstance(effect[4], str) or _IDENTIFIER.fullmatch(effect[4]) is None
                        or not isinstance(effect[6], str) or _DIGEST.fullmatch(effect[6]) is None):
                    raise CampaignError("effet review-waiting contradictoire")
            spent += implementation[8]
            intent = intents.get((issue_id, 1))
            if (
                intent is None or intent[:7] != (
                    campaign_id, issue_id, 1, implementation[3], implementation[4], durable_binding, "completed",
                ) or intent[9] != implementation[6]
                or not isinstance(intent[7], str) or _SHA.fullmatch(intent[7]) is None
                or not isinstance(intent[8], str) or _SHA.fullmatch(intent[8]) is None
                or type(intent[10]) is not int or not 0 <= intent[10] <= observed_at
            ):
                raise CampaignError("intent review-waiting contradictoire")
            start_receipt = primitives.get(start[3])
            pr_receipt = primitives.get(open_pr[3])
            review_receipt = primitives.get(review[3])
            if (
                start_receipt is None or pr_receipt is None
                or start_receipt[:8] != (start[3], durable_binding, campaign_id, issue_id, 1, "start", "completed", start[6])
                or start_receipt[8:11] != (None, None, None)
                or pr_receipt[:8] != (open_pr[3], durable_binding, campaign_id, issue_id, 1, "open-pr", "completed", open_pr[6])
                or type(pr_receipt[8]) is not int or pr_receipt[8] < 1
                or not isinstance(pr_receipt[9], str) or _SHA.fullmatch(pr_receipt[9]) is None
                or not isinstance(pr_receipt[10], str) or _SHA.fullmatch(pr_receipt[10]) is None
                or any(
                    type(item[11]) is not int or not 0 <= item[11] <= observed_at
                    for item in (start_receipt, pr_receipt)
                )
            ):
                raise CampaignError("reçu review-waiting contradictoire")
            if (
                review_receipt is None
                or review_receipt[:8] != (
                    review[3], durable_binding, campaign_id, issue_id, 1,
                    "review", "waiting", review[6],
                )
                or review_receipt[8:11] != pr_receipt[8:11]
                or type(review_receipt[11]) is not int
                or not 0 <= review_receipt[11] <= observed_at
            ):
                raise CampaignError("reçu review-waiting contradictoire")
        if spent != budget_spent:
            raise CampaignError("coût review-waiting contradictoire")
        material = {
            "contract": "foundry-campaign-review-waiting-proof.v1",
            "campaign_id": campaign_id, "command_binding_digest": binding_digest,
            "campaign_binding_digest": durable_binding, "state": state, "reason": reason,
            "wave_index": wave_index, "budget_spent_cents": budget_spent,
            "budget_reserved_cents": budget_reserved, "peak_concurrency": peak_concurrency,
            "active_concurrency": active_concurrency, "lease_expires_at": lease_expires_at,
            "campaign_updated_at": updated_at, "issues": issues, "effects": effects,
            "primitive_receipts": primitive_receipts, "implementation_intents": implementation_intents,
            "retry_decisions": retry_decisions, "auxiliary_counts": auxiliary_counts,
        }
        return ReviewWaitingCampaignProof(
            campaign_id, binding_digest, _digest(material), observed_at, budget_spent,
            stored_campaign.budget_cents, stored_campaign.max_concurrency,
        )


    def review_waiting_reservation_proof(
        self, campaign_id: str, binding_digest: str, *, receipts_path: str | os.PathLike,
        expected_command: dict[str, object], observed_at: int,
    ) -> ReviewWaitingCampaignProof:
        """Prove a suspended review gate has no work beyond settled implementations.

        PR and waiting-review receipts are preserved as evidence. They are never replayed:
        this proof can only settle the historical host reservation.
        """
        _safe_identifier(campaign_id, "campagne review-waiting")
        _safe_digest(binding_digest, "binding de commande review-waiting")
        if (
            type(expected_command) is not dict
            or set(expected_command) != set(_COMMAND_CAMPAIGN_FIELDS)
        ):
            raise ValueError("coordonnées de commande review-waiting invalides")
        if type(observed_at) is not int or observed_at < 0:
            raise ValueError("horodatage review-waiting invalide")
        receipt_file = self._review_waiting_receipt_file(receipts_path)
        try:
            with self._lock, self._connect() as connection:
                connection.execute("ATTACH DATABASE ? AS primitive", (str(receipt_file),))
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN")
                snapshot = self._review_waiting_reservation_snapshot(connection, campaign_id)
                connection.commit()
        except (OSError, sqlite3.DatabaseError):
            raise CampaignError("ledger review-waiting illisible ou tronqué") from None
        return self._review_waiting_reservation_proof_from_snapshot(
            campaign_id, binding_digest, expected_command=expected_command,
            observed_at=observed_at, snapshot=snapshot,
        )

    def settle_review_waiting_reservation(
        self, campaign_id: str, binding_digest: str, *, receipts_path: str | os.PathLike,
        expected_command: dict[str, object], observed_at: int,
        settle: Callable[[ReviewWaitingCampaignProof], object],
    ) -> object:
        """Hold the campaign write lock through one host settlement callback.

        The callback must settle only the already reserved host capacity. Keeping this
        SQLite write transaction open prevents a lease, remediation, or downstream
        campaign effect from appearing after the proof and before that settlement.
        """
        _safe_identifier(campaign_id, "campagne review-waiting")
        _safe_digest(binding_digest, "binding de commande review-waiting")
        if (
            type(expected_command) is not dict
            or set(expected_command) != set(_COMMAND_CAMPAIGN_FIELDS)
        ):
            raise ValueError("coordonnées de commande review-waiting invalides")
        if type(observed_at) is not int or observed_at < 0 or not callable(settle):
            raise ValueError("settlement review-waiting invalide")
        receipt_file = self._review_waiting_receipt_file(receipts_path)
        try:
            with self._lock, self._connect() as connection:
                connection.execute("ATTACH DATABASE ? AS primitive", (str(receipt_file),))
                connection.execute("BEGIN IMMEDIATE")
                snapshot = self._review_waiting_reservation_snapshot(connection, campaign_id)
                proof = self._review_waiting_reservation_proof_from_snapshot(
                    campaign_id, binding_digest, expected_command=expected_command,
                    observed_at=observed_at, snapshot=snapshot,
                )
                settled = settle(proof)
                connection.commit()
                return settled
        except sqlite3.DatabaseError:
            raise CampaignError("ledger review-waiting illisible ou tronqué") from None

    @staticmethod
    def _blocker_observation_is_stale(
        observation: BlockerObservation,
        previous: tuple[str, int | None, int | None, str],
    ) -> bool:
        """Reject a resolved blocker fact that regresses a durable coordinate.

        Provider timestamps and versions are not interchangeable.  A missing or
        changed coordinate system is therefore ambiguous once a prior fact exists;
        accepting it could make an old ``done`` observation reopen a suspended
        campaign after a newer ``blocked`` observation.
        """
        previous_state, previous_updated_at, previous_version, previous_digest = previous
        if (observation.version is None) != (previous_version is None):
            return True
        if observation.version is not None and observation.version < previous_version:
            return True
        if (observation.updated_at is None) != (previous_updated_at is None):
            return True
        if (
            observation.updated_at is not None
            and observation.updated_at < previous_updated_at
        ):
            return True
        # An identical provider coordinate is a replay only when its exact
        # normalized observation is identical.  In particular, an old ``done``
        # with the same timestamp/version may not overwrite a newer ``blocked``
        # fact merely because the provider supplied a stale replica.
        return (
            observation.updated_at == previous_updated_at
            and observation.version == previous_version
            and (observation.state != previous_state
                 or observation.proof_digest != previous_digest)
        )

    def record_blocker_observations(
        self, spec: CampaignSpec, observations: tuple[BlockerObservation, ...], *,
        now_ms: int,
    ) -> None:
        """Journal fresh blocker facts without rewriting the immutable preview."""
        if (type(now_ms) is not int or now_ms < 0
                or tuple(item.issue_id for item in observations) != spec.blockers):
            raise CampaignError("observations de blockers contradictoires")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = connection.execute(
                "SELECT binding_digest FROM campaigns WHERE campaign_id = ?",
                (spec.campaign_id,),
            ).fetchone()
            if binding != (spec.binding_digest,):
                connection.rollback()
                raise CampaignError("binding de blockers modifié")
            for observation in observations:
                previous = connection.execute(
                    "SELECT issue_state, issue_updated_at, issue_version, proof_digest "
                    "FROM campaign_blocker_observations "
                    "WHERE campaign_id = ? AND blocker_id = ?",
                    (spec.campaign_id, observation.issue_id),
                ).fetchall()
                if any(
                    self._blocker_observation_is_stale(observation, item)
                    for item in previous
                ):
                    connection.rollback()
                    raise CampaignRevalidationError("blocker_observation_stale")
                connection.execute(
                    "INSERT INTO campaign_blocker_observations "
                    "(campaign_id, blocker_id, issue_state, issue_updated_at, "
                    "issue_version, resolved, proof_digest, observed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(campaign_id, blocker_id, proof_digest) DO UPDATE SET "
                    "observed_at = excluded.observed_at",
                    (
                        spec.campaign_id, observation.issue_id, observation.state,
                        observation.updated_at, observation.version,
                        int(observation.resolved), observation.proof_digest, now_ms,
                    ),
                )
            connection.commit()

    def acquire_lease(
        self, campaign_id: str, owner_id: str, *, now_ms: int, lease_ms: int,
    ) -> _CampaignRow:
        _safe_identifier(owner_id, "owner")
        if type(lease_ms) is not int or not 1_000 <= lease_ms <= MAX_LEASE_MS:
            raise ValueError("durée de lease campagne hors borne")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._campaign_row(connection.execute(
                "SELECT state, reason, control, wave_index, budget_spent, budget_reserved, "
                "peak_concurrency, active_concurrency, lease_id, lease_owner, "
                "lease_expires_at FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone())
            if (current.lease_owner not in {None, owner_id}
                    and current.lease_expires_at is not None
                    and current.lease_expires_at > now_ms):
                connection.rollback()
                raise CampaignLeaseError("campagne déjà lease par un autre coordinateur")
            lease_id = (
                current.lease_id if current.lease_owner == owner_id and current.lease_id
                else f"lease-{uuid.uuid4().hex}"
            )
            connection.execute(
                "UPDATE campaigns SET lease_id = ?, lease_owner = ?, lease_expires_at = ?, "
                "updated_at = ? WHERE campaign_id = ?",
                (lease_id, owner_id, now_ms + lease_ms, now_ms, campaign_id),
            )
            connection.commit()
        return self.campaign(campaign_id)

    def refresh_lease(
        self, campaign_id: str, owner_id: str, lease_id: str, *, now_ms: int,
        lease_ms: int,
    ) -> _CampaignRow:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE campaigns SET lease_expires_at = ?, updated_at = ? "
                "WHERE campaign_id = ? AND lease_owner = ? AND lease_id = ? "
                "AND lease_expires_at > ?",
                (now_ms + lease_ms, now_ms, campaign_id, owner_id, lease_id, now_ms),
            ).rowcount
            connection.commit()
        if updated != 1:
            raise CampaignLeaseError("lease campagne perdue")
        return self.campaign(campaign_id)

    def request_control(self, campaign_id: str, control: str, *, now_ms: int) -> None:
        if control not in {"pause-requested", "cancel-requested"}:
            raise ValueError("contrôle campagne invalide")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT control FROM campaigns WHERE campaign_id = ?", (campaign_id,),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise CampaignError("campagne durable absente")
            # cancel is stronger and cannot be overwritten by a late pause.
            effective = "cancel-requested" if "cancel-requested" in {current[0], control} else control
            connection.execute(
                "UPDATE campaigns SET control = ?, updated_at = ? WHERE campaign_id = ?",
                (effective, now_ms, campaign_id),
            )
            connection.commit()

    def resume(self, campaign_id: str, reason: str, *, now_ms: int) -> None:
        if reason not in RESUME_REASONS:
            raise ValueError("raison de reprise campagne invalide")
        if reason == "manual_retry_approved":
            raise CampaignError("reprise manuelle sans autorisation exacte")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state FROM campaigns WHERE campaign_id = ?", (campaign_id,),
            ).fetchone()
            if row is None or row[0] not in {"paused", "suspended"}:
                connection.rollback()
                raise CampaignError("campagne non reprenable dans cet état")
            connection.execute(
                "UPDATE campaigns SET state = 'running', reason = ?, control = 'active', "
                "updated_at = ? WHERE campaign_id = ?", (reason, now_ms, campaign_id),
            )
            connection.commit()

    def authorize_manual_retry(
        self, campaign_id: str, issue_id: str, attempt: int, *, actor: str,
        now_ms: int,
    ) -> str:
        """Bind one explicit human signal to one settled blocked implementation."""
        _safe_identifier(campaign_id, "campagne de reprise")
        if not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None:
            raise ValueError("issue de reprise manuelle invalide")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("tentative de reprise manuelle invalide")
        _safe_identifier(actor, "acteur de reprise manuelle")
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("horodatage de reprise manuelle invalide")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            campaign = connection.execute(
                "SELECT state, reason, control FROM campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            issue = connection.execute(
                "SELECT attempt, next_step FROM campaign_issues "
                "WHERE campaign_id = ? AND issue_id = ?",
                (campaign_id, issue_id),
            ).fetchone()
            effect = connection.execute(
                "SELECT effect_id, status, proof_digest, engagement_state "
                "FROM campaign_effects WHERE campaign_id = ? AND issue_id = ? "
                "AND attempt = ? AND step = 'implementation'",
                (campaign_id, issue_id, attempt),
            ).fetchone()
            if (issue != (attempt, "implementation")
                    or effect is None or effect[1] != "blocked"
                    or not isinstance(effect[2], str)
                    or _DIGEST.fullmatch(effect[2]) is None
                    or effect[3] != "settled"):
                connection.rollback()
                raise CampaignError("blocker d'implémentation exact absent")
            material = {
                "contract": "foundry-campaign-manual-retry.v1",
                "campaign_id": campaign_id,
                "issue_id": issue_id,
                "from_attempt": attempt,
                "effect_id": effect[0],
                "proof_digest": effect[2],
                "reason": "manual_retry_approved",
                "actor": actor,
            }
            authorization_digest = _digest(material)
            current = connection.execute(
                "SELECT authorization_digest, effect_id, proof_digest, actor "
                "FROM campaign_manual_retry_authorizations "
                "WHERE campaign_id = ? AND issue_id = ? AND from_attempt = ?",
                (campaign_id, issue_id, attempt),
            ).fetchone()
            expected = (authorization_digest, effect[0], effect[2], actor)
            if current is None:
                if campaign != ("suspended", "blocker", "active"):
                    connection.rollback()
                    raise CampaignError("blocker d'implémentation exact absent")
                connection.execute(
                    "INSERT INTO campaign_manual_retry_authorizations "
                    "(authorization_digest, campaign_id, issue_id, from_attempt, "
                    "effect_id, proof_digest, actor, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (authorization_digest, campaign_id, issue_id, attempt,
                     effect[0], effect[2], actor, now_ms),
                )
            elif current != expected:
                connection.rollback()
                raise CampaignError("autorisation de reprise manuelle modifiée")
            elif campaign == ("running", "manual_retry_approved", "active"):
                connection.commit()
                return authorization_digest
            elif campaign != ("suspended", "blocker", "active"):
                connection.rollback()
                raise CampaignError("blocker d'implémentation exact absent")
            updated = connection.execute(
                "UPDATE campaigns SET state = 'running', reason = 'manual_retry_approved', "
                "updated_at = ? WHERE campaign_id = ? AND state = 'suspended' "
                "AND reason = 'blocker' AND control = 'active'",
                (now_ms, campaign_id),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise CampaignError("transition de reprise manuelle perdue")
            connection.commit()
        return authorization_digest

    @staticmethod
    def _review_remediation_row(
        row, *, binding_digest: str, failure_audit_digest: str,
    ) -> ReviewRemediationAuthorization:
        if row is None:
            raise CampaignError("autorisation de remédiation review absente")
        try:
            authorization = ReviewRemediationAuthorization(*row)
            _safe_digest(binding_digest, "binding de remédiation review")
            _safe_digest(failure_audit_digest, "échec de remédiation review")
        except (TypeError, ValueError):
            raise CampaignError("autorisation de remédiation review invalide") from None
        material = {
            "contract": "foundry-campaign-first-review-remediation.v1",
            "binding_digest": binding_digest,
            "campaign_id": authorization.campaign_id,
            "issue_id": authorization.issue_id,
            "attempt": authorization.attempt,
            "effect_id": authorization.effect_id,
            "proof_digest": authorization.proof_digest,
            "failure_audit_digest": failure_audit_digest,
            "role": authorization.role,
            "current_tier": authorization.current_tier,
            "actor": authorization.actor,
            "expires_at": authorization.expires_at,
        }
        if authorization.authorization_digest != _digest(material):
            raise CampaignError("digest de remédiation review modifié")
        return authorization

    def authorize_review_remediation(
        self, campaign_id: str, issue_id: str, attempt: int, *, actor: str,
        valid_seconds: int, now_ms: int,
        revalidate: Callable[[ReviewRemediationTarget], ReviewRemediationTarget],
    ) -> ReviewRemediationAuthorization:
        """Revalidate, grant one exact first-review correction and resume atomically."""
        _safe_identifier(campaign_id, "campagne de remédiation review")
        if not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None:
            raise ValueError("issue de remédiation review invalide")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("tentative de remédiation review invalide")
        _safe_identifier(actor, "acteur de remédiation review")
        if (type(valid_seconds) is not int or not 60 <= valid_seconds <= 86_400
                or type(now_ms) is not int or now_ms < 0):
            raise CampaignError("durée d'autorisation review hors borne")

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            campaign = connection.execute(
                "SELECT state, reason, control, binding_digest, spec_json "
                "FROM campaigns WHERE campaign_id = ?", (campaign_id,),
            ).fetchone()
            failures = connection.execute(
                "SELECT audit_digest, selected_tier, reason, authorization_digest "
                "FROM campaign_retry_decisions WHERE campaign_id = ? AND issue_id = ? "
                "AND attempt = ? AND step = 'review' AND allowed = 0",
                (campaign_id, issue_id, attempt),
            ).fetchall()
            existing = connection.execute(
                "SELECT authorization_digest, campaign_id, issue_id, from_attempt, "
                "effect_id, proof_digest, role, current_tier, actor, expires_at, created_at "
                "FROM campaign_review_remediation_authorizations "
                "WHERE campaign_id = ? AND issue_id = ? AND from_attempt = ?",
                (campaign_id, issue_id, attempt),
            ).fetchone()
            effects = connection.execute(
                "SELECT step, effect_id, status, proof_digest FROM campaign_effects "
                "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? "
                "AND step IN ('implementation', 'open-pr', 'review')",
                (campaign_id, issue_id, attempt),
            ).fetchall()
            current = {step: values for step, *values in effects}
            issue = connection.execute(
                "SELECT attempt, next_step, state, selected_tier "
                "FROM campaign_issues WHERE campaign_id = ? AND issue_id = ?",
                (campaign_id, issue_id),
            ).fetchone()
            current_tier = issue[3] if issue is not None else None
            review = current.get("review")
            prior_review_remediations = connection.execute(
                "SELECT COUNT(*) FROM campaign_retry_decisions WHERE campaign_id = ? "
                "AND issue_id = ? AND step = 'review' AND allowed = 1",
                (campaign_id, issue_id),
            ).fetchone()

            def revalidate_target(target: ReviewRemediationTarget) -> None:
                try:
                    observed = revalidate(target)
                except Exception:
                    connection.rollback()
                    raise CampaignError(
                        "revalidation d'autorité de review refusée"
                    ) from None
                if not isinstance(observed, ReviewRemediationTarget) or observed != target:
                    connection.rollback()
                    raise CampaignError(
                        "revalidation d'autorité de review contradictoire"
                    )

            if existing is not None:
                if (
                    campaign is None
                    or campaign[:3] != (
                        "running", "manual_retry_approved", "active",
                    )
                    or len(failures) != 1
                ):
                    connection.rollback()
                    raise CampaignError("première review bloquée exacte absente")
                authorization = self._review_remediation_row(
                    existing[:-1], binding_digest=campaign[3],
                    failure_audit_digest=failures[0][0],
                )
                if (
                    issue != (
                        authorization.attempt, "review", "open-pr-completed",
                        authorization.current_tier,
                    )
                    or prior_review_remediations != (0,)
                ):
                    connection.rollback()
                    raise CampaignError("première review bloquée exacte absente")
                if (failures[0][1:] != (
                        authorization.current_tier, "failure_recorded", None,
                    ) or review != [
                        authorization.effect_id, "blocked",
                        authorization.proof_digest,
                    ] or authorization.actor != actor
                        or type(existing[-1]) is not int
                        or authorization.expires_at - existing[-1]
                        != valid_seconds * 1_000):
                    connection.rollback()
                    raise CampaignError(
                        "autorisation de remédiation review liée à d'autres coordonnées"
                    )
                if now_ms >= authorization.expires_at:
                    connection.rollback()
                    raise CampaignError("autorisation de remédiation review expirée")
                revalidate_target(ReviewRemediationTarget(
                    campaign_id, issue_id, attempt, authorization.effect_id,
                    authorization.proof_digest, campaign[3],
                    authorization.role, authorization.current_tier,
                ))
                connection.commit()
                return authorization

            expires_at = now_ms + valid_seconds * 1_000
            try:
                spec = json.loads(campaign[4]) if campaign is not None else None
                campaign_expires_at = spec["expires_at"]
            except (TypeError, KeyError, json.JSONDecodeError):
                campaign_expires_at = None
            if (
                campaign is None
                or campaign[:3] != ("suspended", "review_blocking", "active")
                or type(campaign_expires_at) is not int
                or not now_ms < expires_at <= campaign_expires_at
                or current_tier not in LEVELS
                or issue != (attempt, "review", "open-pr-completed", current_tier)
                or set(current) != {"implementation", "open-pr", "review"}
                or current["implementation"][1] != "completed"
                or current["open-pr"][1] != "completed"
                or review is None or review[1] != "blocked"
                or any(
                    not isinstance(current[step][2], str)
                    or _DIGEST.fullmatch(current[step][2]) is None
                    for step in current
                )
                or len(failures) != 1
                or failures[0][1:] != (current_tier, "failure_recorded", None)
                or prior_review_remediations != (0,)
            ):
                connection.rollback()
                raise CampaignError("première review bloquée exacte absente")
            failure_audit_digest = failures[0][0]
            effect_id, _status, proof_digest = review
            role = "implementer"
            _safe_digest(failure_audit_digest, "audit de première review")
            target = ReviewRemediationTarget(
                campaign_id, issue_id, attempt, effect_id, proof_digest,
                campaign[3], role, current_tier,
            )
            # Keep the local campaign ledger locked while the trusted primitive
            # re-reads the current PR and blocked proof.  The callback cannot
            # authorize anything: it must return this exact content-free target.
            revalidate_target(target)
            material = {
                "contract": "foundry-campaign-first-review-remediation.v1",
                "campaign_id": campaign_id,
                "binding_digest": campaign[3],
                "issue_id": issue_id,
                "attempt": attempt,
                "effect_id": effect_id,
                "proof_digest": proof_digest,
                "failure_audit_digest": failure_audit_digest,
                "role": role,
                "current_tier": current_tier,
                "actor": actor,
                "expires_at": expires_at,
            }
            authorization_digest = _digest(material)
            connection.execute(
                "INSERT INTO campaign_review_remediation_authorizations "
                "(authorization_digest, campaign_id, issue_id, from_attempt, "
                "effect_id, proof_digest, role, current_tier, actor, expires_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (authorization_digest, campaign_id, issue_id, attempt, effect_id,
                 proof_digest, role, current_tier, actor, expires_at, now_ms),
            )
            resumed = connection.execute(
                "UPDATE campaigns SET state = 'running', "
                "reason = 'manual_retry_approved', updated_at = ? "
                "WHERE campaign_id = ? AND state = 'suspended' "
                "AND reason = 'review_blocking' AND control = 'active'",
                (now_ms, campaign_id),
            ).rowcount
            if resumed != 1:
                connection.rollback()
                raise CampaignError("transition de remédiation review perdue")
            connection.commit()
        return ReviewRemediationAuthorization(
            authorization_digest, campaign_id, issue_id, attempt, effect_id,
            proof_digest, role, current_tier, actor, expires_at,
        )

    def review_remediation_authorization(
        self, envelope: EffectEnvelope,
    ) -> ReviewRemediationAuthorization | None:
        """Resolve only a durable grant still bound to this blocked effect."""
        if envelope.step != "review":
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT authorization_digest, campaign_id, issue_id, from_attempt, "
                "effect_id, proof_digest, role, current_tier, actor, expires_at "
                "FROM campaign_review_remediation_authorizations "
                "WHERE campaign_id = ? AND issue_id = ? AND from_attempt = ? "
                "AND effect_id = ?",
                (envelope.campaign_id, envelope.issue_id, envelope.attempt,
                 envelope.effect_id),
            ).fetchone()
            if row is None:
                return None
            campaign = connection.execute(
                "SELECT state, control, binding_digest FROM campaigns "
                "WHERE campaign_id = ?", (envelope.campaign_id,),
            ).fetchone()
            failures = connection.execute(
                "SELECT audit_digest, selected_tier, reason, authorization_digest "
                "FROM campaign_retry_decisions WHERE campaign_id = ? AND issue_id = ? "
                "AND attempt = ? AND step = 'review' AND allowed = 0",
                (envelope.campaign_id, envelope.issue_id, envelope.attempt),
            ).fetchall()
            if (campaign is None or len(failures) != 1
                    or failures[0][1:] != (
                        envelope.selected_tier, "failure_recorded", None,
                    )):
                raise CampaignError("autorisation de remédiation review incohérente")
            authorization = self._review_remediation_row(
                row, binding_digest=campaign[2],
                failure_audit_digest=failures[0][0],
            )
            effect = connection.execute(
                "SELECT status, proof_digest FROM campaign_effects WHERE campaign_id = ? "
                "AND issue_id = ? AND attempt = ? AND step = 'review'",
                (envelope.campaign_id, envelope.issue_id, envelope.attempt),
            ).fetchone()
            issue = connection.execute(
                "SELECT attempt, next_step, selected_tier FROM campaign_issues "
                "WHERE campaign_id = ? AND issue_id = ?",
                (envelope.campaign_id, envelope.issue_id),
            ).fetchone()
        if (
            campaign[:2] != ("running", "active")
            or authorization.current_tier != envelope.selected_tier
            or effect != ("blocked", authorization.proof_digest)
            or issue != (
                envelope.attempt, "review", authorization.current_tier,
            )
        ):
            raise CampaignError("autorisation de remédiation review incohérente")
        return authorization

    def set_state(
        self, campaign_id: str, state: str, reason: str | None, *, now_ms: int,
        wave_index: int | None = None,
    ) -> None:
        if state not in CAMPAIGN_STATES:
            raise ValueError("état campagne invalide")
        if reason is not None:
            _safe_identifier(reason, "raison campagne")
        fields = "state = ?, reason = ?, updated_at = ?"
        params: list[object] = [state, reason, now_ms]
        if wave_index is not None:
            fields += ", wave_index = ?"
            params.append(wave_index)
        params.append(campaign_id)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE campaigns SET {fields} WHERE campaign_id = ?", params,
            )

    def issue(self, campaign_id: str, issue_id: str) -> dict:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT wave_index, attempt, next_step, state, selected_tier, engaged_merge, "
                "merge_dispatch_started "
                "FROM campaign_issues WHERE campaign_id = ? AND issue_id = ?",
                (campaign_id, issue_id),
            ).fetchone()
        if row is None:
            raise CampaignError("issue de campagne absente")
        return {
            "wave_index": row[0], "attempt": row[1], "next_step": row[2],
            "state": row[3], "selected_tier": row[4],
            "engaged_merge": bool(row[5]),
            "merge_dispatch_started": bool(row[6]),
        }

    def advance_issue(
        self, campaign_id: str, issue_id: str, *, next_step: str, state: str,
        selected_tier: str | None = None, engaged_merge: bool | None = None,
        merge_dispatch_started: bool | None = None,
        increment_attempt: bool = False,
    ) -> None:
        if next_step not in REQUIRED_STEPS and next_step != "done":
            raise ValueError("étape campagne invalide")
        fields = ["next_step = ?", "state = ?"]
        params: list[object] = [next_step, state]
        if selected_tier is not None:
            if selected_tier not in LEVELS:
                raise ValueError("tier campagne invalide")
            fields.append("selected_tier = ?")
            params.append(selected_tier)
        if engaged_merge is not None:
            fields.append("engaged_merge = ?")
            params.append(int(engaged_merge))
        if merge_dispatch_started is not None:
            fields.append("merge_dispatch_started = ?")
            params.append(int(merge_dispatch_started))
        if increment_attempt:
            fields.append("attempt = attempt + 1")
        params.extend([campaign_id, issue_id])
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE campaign_issues SET {', '.join(fields)} "
                "WHERE campaign_id = ? AND issue_id = ?", params,
            )

    def completed_issues(self, campaign_id: str) -> tuple[str, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT issue_id FROM campaign_issues WHERE campaign_id = ? "
                "AND state = 'merged' ORDER BY issue_id", (campaign_id,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def engaged_merge_issues(self, campaign_id: str) -> tuple[str, ...]:
        """Return issues whose merge crossed the durable dispatch boundary.

        The historical method name remains for ledger compatibility.  The field
        it queries is deliberately distinct from ``engaged_merge``: the latter
        is only set after the merge primitive has returned a receipt, whereas a
        dispatch marker must survive a crash between invoking that primitive and
        receiving its result.
        """
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT issue_id FROM campaign_issues WHERE campaign_id = ? "
                "AND merge_dispatch_started = 1 AND state != 'merged' ORDER BY issue_id",
                (campaign_id,),
            ).fetchall()
        return tuple(row[0] for row in rows)

    def mark_merge_dispatch_started(
        self, campaign_id: str, issue_id: str, attempt: int,
    ) -> None:
        """Persist the crash-recovery boundary immediately before a merge call.

        This is intentionally not ``engaged_merge``: no successful merge receipt
        exists yet.  The marker only authorizes reconciliation of the same exact
        effect identity; it never authorizes a second merge dispatch.
        """
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE campaign_issues SET state = 'merge-dispatch-started', "
                "merge_dispatch_started = 1 WHERE campaign_id = ? AND issue_id = ? "
                "AND attempt = ? AND next_step = 'merge' AND state != 'merged' "
                "AND merge_dispatch_started = 0",
                (campaign_id, issue_id, attempt),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise CampaignError("frontière de dispatch merge perdue")
            connection.commit()

    def reserve_budget(
        self, spec: CampaignSpec, issue_id: str, attempt: int, ceiling: int, *,
        observed_remaining: int, now_ms: int,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            spent, reserved = connection.execute(
                "SELECT budget_spent, budget_reserved FROM campaigns WHERE campaign_id = ?",
                (spec.campaign_id,),
            ).fetchone()
            if (ceiling > observed_remaining
                    or spent + reserved + ceiling > spec.budget_cents
                    or reserved + ceiling > observed_remaining):
                connection.rollback()
                raise CampaignRevalidationError("budget_exhausted")
            connection.execute(
                "UPDATE campaigns SET budget_reserved = budget_reserved + ?, "
                "active_concurrency = active_concurrency + 1, "
                "peak_concurrency = MAX(peak_concurrency, active_concurrency + 1), "
                "updated_at = ? WHERE campaign_id = ?",
                (ceiling, now_ms, spec.campaign_id),
            )
            connection.commit()

    def begin_implementation(
        self, spec: CampaignSpec, envelope: EffectEnvelope, *,
        observed_remaining: int, observed_max_concurrency: int, now_ms: int,
    ) -> tuple[tuple, bool]:
        """Atomically reserve capacity and persist the non-replayable work intent."""
        ceiling = envelope.cost_ceiling_cents
        selected_tier = envelope.selected_tier
        if selected_tier not in LEVELS:
            raise CampaignError("tier d'implémentation réservé invalide")
        if (type(observed_max_concurrency) is not int
                or not 1 <= observed_max_concurrency <= spec.max_concurrency):
            raise CampaignRevalidationError("concurrency_drift")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT effect_id, work_id, status, proof_digest, cost_ceiling, cost_cents, "
                "selected_tier, retry_audit_digest FROM campaign_effects "
                "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? "
                "AND step = 'implementation'",
                (spec.campaign_id, envelope.issue_id, envelope.attempt),
            ).fetchone()
            if row is not None:
                if (row[0] != envelope.effect_id or row[1] != envelope.work_id
                        or row[4] != ceiling or row[6] != selected_tier):
                    connection.rollback()
                    raise CampaignError("identité d'implémentation durable modifiée")
                connection.commit()
                return row, False
            issue = connection.execute(
                "SELECT attempt, next_step, selected_tier FROM campaign_issues "
                "WHERE campaign_id = ? AND issue_id = ?",
                (spec.campaign_id, envelope.issue_id),
            ).fetchone()
            if (issue is None or issue[0] != envelope.attempt
                    or issue[1] != "implementation"
                    or (issue[2] is not None and (
                        issue[2] not in LEVELS
                        or LEVELS.index(selected_tier) < LEVELS.index(issue[2])
                    ))):
                connection.rollback()
                raise CampaignError("état de tier d'implémentation invalide")
            spent, reserved, active = connection.execute(
                "SELECT budget_spent, budget_reserved, active_concurrency "
                "FROM campaigns WHERE campaign_id = ?",
                (spec.campaign_id,),
            ).fetchone()
            if active >= observed_max_concurrency:
                connection.rollback()
                raise _CampaignCapacityDeferred("concurrency_tightened")
            if (ceiling > observed_remaining
                    or spent + reserved + ceiling > spec.budget_cents
                    or reserved + ceiling > observed_remaining):
                connection.rollback()
                raise CampaignRevalidationError("budget_exhausted")
            connection.execute(
                "INSERT INTO campaign_effects "
                "(campaign_id, issue_id, attempt, step, effect_id, work_id, status, "
                "proof_digest, cost_ceiling, cost_cents, selected_tier, "
                "retry_audit_digest, engagement_state) VALUES "
                "(?, ?, ?, 'implementation', ?, ?, 'intent', NULL, ?, NULL, ?, NULL, "
                "'reserved')",
                (spec.campaign_id, envelope.issue_id, envelope.attempt,
                 envelope.effect_id, envelope.work_id, ceiling, selected_tier),
            )
            updated_issue = connection.execute(
                "UPDATE campaign_issues SET selected_tier = ? WHERE campaign_id = ? "
                "AND issue_id = ? AND attempt = ? AND next_step = 'implementation'",
                (selected_tier, spec.campaign_id, envelope.issue_id, envelope.attempt),
            ).rowcount
            if updated_issue != 1:
                connection.rollback()
                raise CampaignError("tier de tentative d'implémentation perdu")
            connection.execute(
                "UPDATE campaigns SET budget_reserved = budget_reserved + ?, "
                "active_concurrency = active_concurrency + 1, "
                "peak_concurrency = MAX(peak_concurrency, active_concurrency + 1), "
                "updated_at = ? WHERE campaign_id = ?",
                (ceiling, now_ms, spec.campaign_id),
            )
            connection.commit()
        return (
            envelope.effect_id, envelope.work_id, "intent", None,
            ceiling, None, selected_tier, None,
        ), True

    def finish_implementation(
        self, spec: CampaignSpec, envelope: EffectEnvelope,
        proposal: ImplementationProposal, *, now_ms: int,
    ) -> None:
        """Atomically charge the attempt and persist its bounded proposal."""
        ceiling = envelope.cost_ceiling_cents
        if proposal.cost_cents > ceiling:
            raise CampaignError("coût de tentative au-dessus de la réservation")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status, proof_digest, cost_cents, cost_ceiling FROM campaign_effects "
                "WHERE effect_id = ?", (envelope.effect_id,),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise CampaignError("intent d'implémentation absent")
            expected = (proposal.outcome, proposal.proof_digest, proposal.cost_cents)
            if current[0] != "intent":
                if current[:3] != expected:
                    connection.rollback()
                    raise CampaignError("proposition durable modifiée")
                connection.commit()
                return
            updated = connection.execute(
                "UPDATE campaigns SET budget_reserved = budget_reserved - ?, "
                "budget_spent = budget_spent + ?, active_concurrency = active_concurrency - 1, "
                "updated_at = ? WHERE campaign_id = ? AND budget_reserved >= ? "
                "AND active_concurrency > 0",
                (ceiling, proposal.cost_cents, now_ms, spec.campaign_id, ceiling),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise CampaignError("réservation d'implémentation perdue")
            connection.execute(
                "UPDATE campaign_effects SET status = ?, proof_digest = ?, cost_cents = ?, "
                "engagement_state = 'settled' "
                "WHERE effect_id = ?",
                (*expected, envelope.effect_id),
            )
            connection.commit()

    def implementation_engagement(self, effect_id: str) -> str:
        _safe_identifier(effect_id, "effet d'implémentation")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT step, engagement_state FROM campaign_effects WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
        if row is None or row[0] != "implementation" or row[1] not in {
            "reserved", "engaged", "settled", "unknown",
        }:
            raise CampaignError("engagement d'implémentation durable invalide")
        return row[1]

    def mark_implementation_engaged(self, envelope: EffectEnvelope) -> None:
        """Persist the last safe pre-launch boundary before entering the provider."""
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, engagement_state FROM campaign_effects "
                "WHERE effect_id = ? AND step = 'implementation'",
                (envelope.effect_id,),
            ).fetchone()
            if row != ("intent", "reserved"):
                connection.rollback()
                raise CampaignError("intent d'implémentation non engageable")
            updated = connection.execute(
                "UPDATE campaign_effects SET engagement_state = 'engaged' "
                "WHERE effect_id = ? AND status = 'intent' "
                "AND engagement_state = 'reserved'",
                (envelope.effect_id,),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise CampaignError("engagement d'implémentation perdu")
            connection.commit()

    def tighten_reserved_implementation_ceiling(
        self, envelope: EffectEnvelope, ceiling: int,
    ) -> None:
        """Atomically lower a not-yet-engaged child reservation; never widen it."""
        current = envelope.cost_ceiling_cents
        if (type(current) is not int or type(ceiling) is not int
                or not 1 <= ceiling <= current):
            raise CampaignError("plafond d'implémentation non resserrable")
        if ceiling == current:
            return
        released = current - ceiling
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            effect = connection.execute(
                "SELECT campaign_id, issue_id, attempt, status, cost_ceiling, "
                "engagement_state FROM campaign_effects WHERE effect_id = ? "
                "AND step = 'implementation'",
                (envelope.effect_id,),
            ).fetchone()
            if effect != (
                envelope.campaign_id, envelope.issue_id, envelope.attempt,
                "intent", current, "reserved",
            ):
                connection.rollback()
                raise CampaignError("intent d'implémentation non resserrable")
            updated_campaign = connection.execute(
                "UPDATE campaigns SET budget_reserved = budget_reserved - ? "
                "WHERE campaign_id = ? AND budget_reserved >= ?",
                (released, envelope.campaign_id, current),
            ).rowcount
            updated_effect = connection.execute(
                "UPDATE campaign_effects SET cost_ceiling = ? WHERE effect_id = ? "
                "AND status = 'intent' AND cost_ceiling = ? "
                "AND engagement_state = 'reserved'",
                (ceiling, envelope.effect_id, current),
            ).rowcount
            if updated_campaign != 1 or updated_effect != 1:
                connection.rollback()
                raise CampaignError("resserrement de capacité d'implémentation perdu")
            connection.commit()

    def raise_reserved_implementation_tier(
        self, envelope: EffectEnvelope, selected_tier: str,
    ) -> None:
        """Monotonically tighten an implementation intent before provider engagement."""
        if envelope.selected_tier not in LEVELS or selected_tier not in LEVELS:
            raise CampaignError("tier d'implémentation réservé invalide")
        if LEVELS.index(selected_tier) < LEVELS.index(envelope.selected_tier):
            raise CampaignError("tier d'implémentation réservé abaissé")
        if selected_tier == envelope.selected_tier:
            return
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            effect = connection.execute(
                "SELECT campaign_id, issue_id, attempt, status, selected_tier, "
                "engagement_state FROM campaign_effects WHERE effect_id = ? "
                "AND step = 'implementation'",
                (envelope.effect_id,),
            ).fetchone()
            issue = connection.execute(
                "SELECT attempt, next_step, selected_tier FROM campaign_issues "
                "WHERE campaign_id = ? AND issue_id = ?",
                (envelope.campaign_id, envelope.issue_id),
            ).fetchone()
            if effect != (
                envelope.campaign_id, envelope.issue_id, envelope.attempt,
                "intent", envelope.selected_tier, "reserved",
            ) or issue not in {
                (envelope.attempt, "implementation", None),
                (envelope.attempt, "implementation", envelope.selected_tier),
            }:
                connection.rollback()
                raise CampaignError("intent d'implémentation non resserrable")
            updated_effect = connection.execute(
                "UPDATE campaign_effects SET selected_tier = ? WHERE effect_id = ? "
                "AND status = 'intent' AND selected_tier = ? "
                "AND engagement_state = 'reserved'",
                (selected_tier, envelope.effect_id, envelope.selected_tier),
            ).rowcount
            updated_issue = connection.execute(
                "UPDATE campaign_issues SET selected_tier = ? WHERE campaign_id = ? "
                "AND issue_id = ? AND attempt = ? AND next_step = 'implementation' "
                "AND (selected_tier IS NULL OR selected_tier = ?)",
                (selected_tier, envelope.campaign_id, envelope.issue_id,
                 envelope.attempt, envelope.selected_tier),
            ).rowcount
            if updated_effect != 1 or updated_issue != 1:
                connection.rollback()
                raise CampaignError("resserrement du tier d'implémentation perdu")
            connection.commit()

    def prepare_resumed_implementation(
        self, spec: CampaignSpec, envelope: EffectEnvelope,
        proposal: ImplementationProposal, *, reason: str, now_ms: int,
    ) -> bool:
        """Atomically bind an explicit resume to a distinct next attempt identity."""
        expected_reason = {
            "blocked": "manual_retry_approved",
            "host-unavailable": "host_recovered",
        }.get(proposal.outcome)
        if (reason != expected_reason or envelope.step != "implementation"
                or proposal.work_id != envelope.work_id
                or proposal.issue_id != envelope.issue_id
                or proposal.attempt != envelope.attempt):
            raise CampaignError("reprise d'implémentation non autorisée")
        if envelope.attempt >= spec.max_attempts_per_issue:
            return False
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            effect = connection.execute(
                "SELECT status, proof_digest, selected_tier FROM campaign_effects "
                "WHERE effect_id = ? AND campaign_id = ? AND issue_id = ? "
                "AND attempt = ? AND step = 'implementation'",
                (envelope.effect_id, spec.campaign_id, envelope.issue_id, envelope.attempt),
            ).fetchone()
            issue = connection.execute(
                "SELECT attempt, next_step, selected_tier FROM campaign_issues "
                "WHERE campaign_id = ? AND issue_id = ?",
                (spec.campaign_id, envelope.issue_id),
            ).fetchone()
            expected_effect = (
                proposal.outcome, proposal.proof_digest, proposal.selected_tier,
            )
            if effect != expected_effect or issue is None:
                connection.rollback()
                raise CampaignError("reçu d'implémentation à reprendre modifié")
            authorization = None
            if reason == "manual_retry_approved":
                authorization = connection.execute(
                    "SELECT authorization_digest FROM "
                    "campaign_manual_retry_authorizations WHERE campaign_id = ? "
                    "AND issue_id = ? AND from_attempt = ? AND effect_id = ? "
                    "AND proof_digest = ?",
                    (spec.campaign_id, envelope.issue_id, envelope.attempt,
                     envelope.effect_id, proposal.proof_digest),
                ).fetchone()
                if authorization is None:
                    connection.rollback()
                    raise CampaignError("autorisation de reprise manuelle absente")
            authorization_digest = authorization[0] if authorization is not None else None
            if issue[0] == envelope.attempt + 1:
                audit = connection.execute(
                    "SELECT effect_id, proof_digest, authorization_digest "
                    "FROM campaign_resume_audits "
                    "WHERE campaign_id = ? AND issue_id = ? AND from_attempt = ? "
                    "AND reason = ?",
                    (spec.campaign_id, envelope.issue_id, envelope.attempt, reason),
                ).fetchone()
                if (issue[1] != "implementation" or issue[2] != proposal.selected_tier
                        or audit != (
                            envelope.effect_id, proposal.proof_digest,
                            authorization_digest,
                        )):
                    connection.rollback()
                    raise CampaignError("rejeu de reprise d'implémentation contradictoire")
                connection.commit()
                return True
            if (issue[0] != envelope.attempt or issue[1] != "implementation"
                    or issue[2] not in {None, proposal.selected_tier}):
                connection.rollback()
                raise CampaignError("état d'implémentation à reprendre modifié")
            connection.execute(
                "INSERT INTO campaign_resume_audits "
                "(campaign_id, issue_id, from_attempt, to_attempt, reason, effect_id, "
                "proof_digest, authorization_digest, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (spec.campaign_id, envelope.issue_id, envelope.attempt,
                 envelope.attempt + 1, reason, envelope.effect_id,
                 proposal.proof_digest, authorization_digest, now_ms),
            )
            updated = connection.execute(
                "UPDATE campaign_issues SET attempt = attempt + 1, "
                "state = 'resume-approved', selected_tier = ? "
                "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? "
                "AND next_step = 'implementation'",
                (proposal.selected_tier, spec.campaign_id, envelope.issue_id,
                 envelope.attempt),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise CampaignError("transition de reprise d'implémentation perdue")
            connection.commit()
        return True

    def settle_budget(
        self, spec: CampaignSpec, ceiling: int, cost: int, *, now_ms: int,
    ) -> None:
        if cost > ceiling:
            raise CampaignError("coût de tentative au-dessus de la réservation")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE campaigns SET budget_reserved = budget_reserved - ?, "
                "budget_spent = budget_spent + ?, active_concurrency = active_concurrency - 1, "
                "updated_at = ? WHERE campaign_id = ? AND budget_reserved >= ? "
                "AND active_concurrency > 0",
                (ceiling, cost, now_ms, spec.campaign_id, ceiling),
            ).rowcount
            connection.commit()
        if updated != 1:
            raise CampaignError("réservation de budget campagne perdue")

    def effect(self, campaign_id: str, issue_id: str, attempt: int, step: str):
        with self._lock, self._connect() as connection:
            return connection.execute(
                "SELECT effect_id, work_id, status, proof_digest, cost_ceiling, cost_cents, "
                "selected_tier, retry_audit_digest FROM campaign_effects "
                "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? AND step = ?",
                (campaign_id, issue_id, attempt, step),
            ).fetchone()

    def _parent_acceptance_children(
        self, spec: CampaignSpec, advancement: ParentSnapshotAdvancement,
    ) -> tuple[tuple[str, str, str], ...]:
        """Validate ledger structure without treating its AC mapping as authority."""
        expected_children = tuple(sorted(
            issue_id for wave in spec.waves for issue_id in wave
        ))
        expected_waves = {
            issue_id: wave_index
            for wave_index, wave in enumerate(spec.waves)
            for issue_id in wave
        }
        parent_material = _digest([
            spec.binding_digest, spec.epic_id, 1, PARENT_ACCEPTANCE_STEP,
        ])
        expected_parent_effect = (
            f"effect-{parent_material[:32]}", f"work-{parent_material[32:]}",
        )
        if (advancement.campaign_id != spec.campaign_id
                or advancement.parent_id != spec.epic_id
                or advancement.effect_id != expected_parent_effect[0]
                or advancement.binding_digest != spec.binding_digest
                or advancement.snapshot_digest != spec.snapshot_digest):
            raise CampaignError("binding preuve avancement parent contradictoire")

        with self._lock, self._connect() as connection:
            issues = connection.execute(
                "SELECT issue_id, wave_index, attempt, next_step, state, "
                "engaged_merge, merge_dispatch_started FROM campaign_issues "
                "WHERE campaign_id = ? ORDER BY issue_id",
                (spec.campaign_id,),
            ).fetchall()
            merges = connection.execute(
                "SELECT issue_id, attempt, effect_id, work_id, status, proof_digest, "
                "cost_ceiling, cost_cents, selected_tier, retry_audit_digest, "
                "engagement_state FROM campaign_effects WHERE campaign_id = ? "
                "AND step = 'merge' ORDER BY issue_id, attempt",
                (spec.campaign_id,),
            ).fetchall()
            parent_effects = connection.execute(
                "SELECT issue_id, attempt, effect_id, work_id, status, proof_digest, "
                "cost_ceiling, cost_cents, selected_tier, retry_audit_digest, "
                "engagement_state FROM campaign_effects WHERE campaign_id = ? "
                "AND step = ? ORDER BY issue_id, attempt",
                (spec.campaign_id, PARENT_ACCEPTANCE_STEP),
            ).fetchall()

        if (tuple(row[0] for row in issues) != expected_children
                or len(merges) != len(expected_children)
                or tuple(row[0] for row in merges) != expected_children
                or len(parent_effects) != 1):
            raise CampaignError("preuve enfants avancement parent incomplete")
        if parent_effects[0][:4] != (
            spec.epic_id, 1, *expected_parent_effect,
        ) or parent_effects[0][4] not in {"intent", "completed"} or (
            parent_effects[0][4] == "intent" and parent_effects[0][5] is not None
        ) or parent_effects[0][6:] != (0, None, None, None, "not-applicable"):
            raise CampaignError("effet avancement parent contradictoire")

        children = []
        for issue, merge in zip(issues, merges, strict=True):
            issue_id, wave_index, attempt, next_step, state, engaged, dispatch = issue
            if (wave_index != expected_waves.get(issue_id)
                    or type(attempt) is not int
                    or not 1 <= attempt <= spec.max_attempts_per_issue
                    or next_step != "done" or state != "merged"
                    or engaged != 0 or dispatch != 0
                    or merge[:2] != (issue_id, attempt)):
                raise CampaignError("preuve enfants avancement parent contradictoire")
            material = _digest([spec.binding_digest, issue_id, attempt, "merge"])
            expected_merge = (
                issue_id, attempt, f"effect-{material[:32]}",
                f"work-{material[32:]}", "completed",
            )
            proof_digest = merge[5]
            if (merge[:5] != expected_merge
                    or not isinstance(proof_digest, str)
                    or _DIGEST.fullmatch(proof_digest) is None
                    or merge[6:] != (0, None, None, None, "not-applicable")):
                raise CampaignError("effet merge avancement parent contradictoire")
            children.append((issue_id, merge[2], proof_digest))

        receipts = {
            issue_id: (effect_id, proof_digest)
            for issue_id, effect_id, proof_digest in children
        }
        if (len(advancement.criteria) != len(expected_children)
                or {item[2] for item in advancement.criteria} != set(expected_children)
                or any(
                    item[3:] != receipts.get(item[2])
                    for item in advancement.criteria
                )):
            raise CampaignError("mapping preuve avancement parent contradictoire")
        return tuple(children)

    def parent_acceptance_proof(
        self, spec: CampaignSpec, advancement: ParentSnapshotAdvancement, *,
        epic_version: int,
        acceptance_mapping: tuple[tuple[str, str, str], ...],
    ) -> ParentAcceptanceProof:
        """Rebuild the parent proof from preview authority and exact merge effects.

        ``advancement_json`` is recovery input, not authority.  Even a caller that
        rewrites its mapping, proof id and advancement digest consistently must
        match the ordered mapping attested by the frozen preview.
        """
        children = self._parent_acceptance_children(spec, advancement)

        try:
            proof = ParentAcceptanceProof(
                spec.campaign_id, spec.epic_id, spec.binding_digest,
                spec.snapshot_digest, epic_version,
                children, acceptance_mapping,
            )
            ordered_mapping = _ordered_parent_acceptance_mapping(proof.mapping)
        except ValueError:
            raise CampaignError("mapping preuve avancement parent contradictoire") from None
        receipts = {
            issue_id: (effect_id, proof_digest)
            for issue_id, effect_id, proof_digest in proof.children
        }
        expected_criteria = tuple(
            (criterion_id, criterion_digest, issue_id, *receipts[issue_id])
            for criterion_id, criterion_digest, issue_id in ordered_mapping
        )
        if (advancement.criteria != expected_criteria
                or advancement.proof_id != proof.proof_id):
            raise CampaignError("preuve avancement parent durable modifiee")
        return proof

    def validate_parent_acceptance_evidence(
        self, spec: CampaignSpec, advancement: ParentSnapshotAdvancement,
    ) -> None:
        """Reject structurally forged ledger evidence before authority I/O.

        This check deliberately grants no replay authority: a caller must still
        rebuild the proof with the preview-attested mapping before any effect.
        """
        self._parent_acceptance_children(spec, advancement)

    def begin_parent_snapshot_advancement(
        self, advancement: ParentSnapshotAdvancement, *, now_ms: int,
    ) -> None:
        """Persist the coordinator-side intent before the tracker can mutate."""
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("horodatage avancement parent invalide")
        encoded = _canonical(asdict(advancement)).decode("utf-8")
        exact = (
            advancement.parent_id, advancement.effect_id,
            advancement.advancement_digest, encoded,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT parent_id, effect_id, advancement_digest, advancement_json, "
                "status, receipt_digest FROM campaign_parent_snapshot_advancements "
                "WHERE campaign_id = ?",
                (advancement.campaign_id,),
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO campaign_parent_snapshot_advancements VALUES "
                    "(?, ?, ?, ?, ?, 'intent', NULL, ?, NULL)",
                    (advancement.campaign_id, *exact, now_ms),
                )
            elif (current[:4] != exact
                    or current[4] not in {"intent", "completed"}
                    or (current[4] == "intent" and current[5] is not None)):
                connection.rollback()
                raise CampaignError("avancement parent durable modifié")
            connection.commit()

    def finish_parent_snapshot_advancement(
        self, advancement: ParentSnapshotAdvancement, receipt: StepReceipt, *, now_ms: int,
    ) -> None:
        if receipt.effect_id != advancement.effect_id or receipt.status != "completed":
            raise CampaignError("reçu avancement parent non terminal")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT advancement_digest, status, receipt_digest "
                "FROM campaign_parent_snapshot_advancements WHERE campaign_id = ?",
                (advancement.campaign_id,),
            ).fetchone()
            if current is None or current[0] != advancement.advancement_digest:
                connection.rollback()
                raise CampaignError("intent avancement parent absent")
            if current[1] == "intent" and current[2] is not None:
                connection.rollback()
                raise CampaignError("reçu avancement parent contradictoire")
            if current[1] == "completed" and current[2] != receipt.proof_digest:
                connection.rollback()
                raise CampaignError("reçu avancement parent modifié")
            connection.execute(
                "UPDATE campaign_parent_snapshot_advancements SET status = 'completed', "
                "receipt_digest = ?, completed_at = ? WHERE campaign_id = ?",
                (receipt.proof_digest, now_ms, advancement.campaign_id),
            )
            connection.commit()

    def parent_snapshot_advancement(
        self, campaign_id: str,
    ) -> tuple[ParentSnapshotAdvancement, str, str | None] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT parent_id, effect_id, advancement_digest, advancement_json, "
                "status, receipt_digest "
                "FROM campaign_parent_snapshot_advancements WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(row[3])
            raw["criteria"] = tuple(tuple(item) for item in raw["criteria"])
            advancement = ParentSnapshotAdvancement(**raw)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            raise CampaignError("avancement parent durable corrompu") from None
        if (advancement.campaign_id != campaign_id
                or advancement.parent_id != row[0]
                or advancement.effect_id != row[1]
                or advancement.advancement_digest != row[2]):
            raise CampaignError("digest avancement parent durable modifie")
        if row[4] not in {"intent", "completed"}:
            raise CampaignError("état avancement parent durable invalide")
        if row[4] == "intent" and row[5] is not None:
            raise CampaignError("reçu avancement parent durable contradictoire")
        if row[5] is not None:
            _safe_digest(row[5], "reçu avancement parent durable")
        return advancement, row[4], row[5]

    def record_legacy_parent_requalification(
        self, record: LegacyParentRequalification,
    ) -> LegacyParentRequalification:
        encoded = _canonical(asdict(record)).decode("utf-8")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            campaign = connection.execute(
                "SELECT binding_digest, state, reason, wave_index, budget_spent, spec_json "
                "FROM campaigns WHERE campaign_id = ?", (record.campaign_id,),
            ).fetchone()
            if campaign is None:
                connection.rollback()
                raise CampaignError("campagne legacy F89 absente")
            try:
                spec = json.loads(campaign[5])
            except (TypeError, json.JSONDecodeError):
                connection.rollback()
                raise CampaignError("spec legacy F89 corrompue") from None
            if (campaign[0] != LEGACY_F89_BINDING_DIGEST
                    or campaign[4] != LEGACY_F89_BUDGET_SPENT
                    or spec.get("project") != "F89E2E"
                    or spec.get("epic_id") != LEGACY_F89_PARENT_ID
                    or spec.get("snapshot_digest") != record.snapshot_digest
                    or tuple(sorted(
                        issue_id for wave in spec.get("waves", ()) for issue_id in wave
                    )) != LEGACY_F89_CHILDREN):
                connection.rollback()
                raise CampaignError("campagne legacy F89 non eligible")
            issues = connection.execute(
                "SELECT issue_id, state FROM campaign_issues WHERE campaign_id = ? "
                "ORDER BY issue_id", (record.campaign_id,),
            ).fetchall()
            if tuple(issues) != tuple((issue_id, "merged") for issue_id in LEGACY_F89_CHILDREN):
                connection.rollback()
                raise CampaignError("enfants legacy F89 non termines")
            current = connection.execute(
                "SELECT audit_digest, audit_json FROM campaign_legacy_parent_requalifications "
                "WHERE campaign_id = ?", (record.campaign_id,),
            ).fetchone()
            if current is not None:
                try:
                    raw = json.loads(current[1])
                    raw["criteria"] = tuple(tuple(item) for item in raw["criteria"])
                    existing = LegacyParentRequalification(**raw)
                except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                    connection.rollback()
                    raise CampaignError(
                        "requalification legacy F89 corrompue"
                    ) from None
                if existing.audit_digest != current[0]:
                    connection.rollback()
                    raise CampaignError(
                        "digest requalification legacy F89 modifie"
                    )
                if existing.evidence_digest != record.evidence_digest:
                    connection.rollback()
                    raise CampaignError("requalification legacy F89 modifiee")
                if (campaign[1], campaign[2], campaign[3]) not in {
                    ("suspended", "host_or_effect_failure", 2),
                    ("running", "campaign_revalidated", 2),
                    ("running", None, 2),
                    ("completed", None, 2),
                }:
                    connection.rollback()
                    raise CampaignError("etat requalification legacy F89 invalide")
                connection.rollback()
                return existing
            if (campaign[1], campaign[2], campaign[3]) != (
                "suspended", "host_or_effect_failure", 2,
            ):
                connection.rollback()
                raise CampaignError("campagne legacy F89 non eligible")
            connection.execute(
                "INSERT INTO campaign_legacy_parent_requalifications VALUES (?, ?, ?, ?) "
                "ON CONFLICT(campaign_id) DO NOTHING",
                (record.campaign_id, record.audit_digest, encoded, record.recorded_at),
            )
            connection.commit()
        return record

    def legacy_parent_requalification(
        self, campaign_id: str,
    ) -> LegacyParentRequalification | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT audit_json, audit_digest FROM "
                "campaign_legacy_parent_requalifications WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(row[0])
            raw["criteria"] = tuple(tuple(item) for item in raw["criteria"])
            record = LegacyParentRequalification(**raw)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            raise CampaignError("requalification legacy F89 corrompue") from None
        if record.audit_digest != row[1]:
            raise CampaignError("digest requalification legacy F89 modifie")
        return record

    def review_failure_signal(
        self, campaign_id: str, issue_id: str, attempt: int,
    ) -> str:
        """Classify a blocking review from its exact durable remediation lineage."""
        if (not isinstance(campaign_id, str) or not campaign_id
                or not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None
                or type(attempt) is not int or attempt < 1):
            raise ValueError("coordonnées de review bloquante invalides")
        with self._lock, self._connect() as connection:
            issue = connection.execute(
                "SELECT attempt, next_step, state FROM campaign_issues "
                "WHERE campaign_id = ? AND issue_id = ?",
                (campaign_id, issue_id),
            ).fetchone()
            effects = connection.execute(
                "SELECT step, status, proof_digest FROM campaign_effects "
                "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? "
                "AND step IN ('implementation', 'open-pr', 'review')",
                (campaign_id, issue_id, attempt),
            ).fetchall()
            lineage = connection.execute(
                "SELECT effect.attempt, effect.status, effect.retry_audit_digest, "
                "decision.campaign_id, decision.issue_id, decision.attempt, "
                "decision.step, decision.allowed, decision.reason, "
                "decision.authorization_digest "
                "FROM campaign_effects AS effect LEFT JOIN campaign_retry_decisions "
                "AS decision ON decision.audit_digest = effect.retry_audit_digest "
                "WHERE effect.campaign_id = ? AND effect.issue_id = ? "
                "AND effect.step = 'review' AND effect.attempt < ? "
                "AND effect.retry_audit_digest IS NOT NULL ORDER BY effect.attempt",
                (campaign_id, issue_id, attempt),
            ).fetchall()

        current = {step: (status, proof) for step, status, proof in effects}
        if (issue != (attempt, "review", "open-pr-completed")
                or set(current) != {"implementation", "open-pr", "review"}
                or any(
                    current[step][0] != expected
                    or not isinstance(current[step][1], str)
                    or _DIGEST.fullmatch(current[step][1]) is None
                    for step, expected in {
                        "implementation": "completed",
                        "open-pr": "completed",
                        "review": "blocked",
                    }.items()
                )):
            raise CampaignError("preuve durable de review bloquante incohérente")

        correction_authorized = False
        for row in lineage:
            (
                prior_attempt, status, audit_digest, decision_campaign,
                decision_issue, decision_attempt, decision_step, allowed,
                reason, authorization_digest,
            ) = row
            if (status != "blocked" or not isinstance(audit_digest, str)
                    or _DIGEST.fullmatch(audit_digest) is None
                    or (decision_campaign, decision_issue, decision_attempt,
                        decision_step) != (
                            campaign_id, issue_id, prior_attempt, "review",
                        )
                    or allowed not in {0, 1}):
                raise CampaignError("lignée durable de remédiation invalide")
            if allowed == 0:
                if authorization_digest is not None:
                    raise CampaignError("lignée durable de remédiation invalide")
                continue
            if (reason != "remediation_continued"
                    or not isinstance(authorization_digest, str)
                    or _DIGEST.fullmatch(authorization_digest) is None):
                raise CampaignError("lignée durable de remédiation invalide")
            correction_authorized = True
        return (
            "review_blocking_after_fix" if correction_authorized
            else "review_blocking"
        )

    def begin_effect(
        self, envelope: EffectEnvelope, *, selected_tier: str | None,
        cost_ceiling: int,
    ) -> tuple[tuple, bool]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT effect_id, work_id, status, proof_digest, cost_ceiling, cost_cents, "
                "selected_tier, retry_audit_digest FROM campaign_effects "
                "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? AND step = ?",
                (envelope.campaign_id, envelope.issue_id, envelope.attempt, envelope.step),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO campaign_effects "
                    "(campaign_id, issue_id, attempt, step, effect_id, work_id, status, "
                    "proof_digest, cost_ceiling, cost_cents, selected_tier, "
                    "retry_audit_digest, engagement_state) VALUES "
                    "(?, ?, ?, ?, ?, ?, 'intent', NULL, ?, NULL, ?, NULL, "
                    "'not-applicable')",
                    (envelope.campaign_id, envelope.issue_id, envelope.attempt,
                     envelope.step, envelope.effect_id, envelope.work_id,
                     cost_ceiling, selected_tier),
                )
                row = (envelope.effect_id, envelope.work_id, "intent", None,
                       cost_ceiling, None, selected_tier, None)
                created = True
            elif (row[0] != envelope.effect_id or row[1] != envelope.work_id
                  or row[4] != cost_ceiling or row[6] != selected_tier):
                connection.rollback()
                raise CampaignError("identité d'effet durable modifiée")
            else:
                created = False
            connection.commit()
        return row, created

    def finish_effect(
        self, envelope: EffectEnvelope, *, status: str, proof_digest: str,
        cost_cents: int | None = None, retry_audit_digest: str | None = None,
    ) -> None:
        if status not in RECEIPT_STATUSES | PROPOSAL_OUTCOMES:
            raise ValueError("statut d'effet durable invalide")
        _safe_digest(proof_digest, "preuve durable")
        if retry_audit_digest is not None:
            _safe_digest(retry_audit_digest, "audit retry durable")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT status, proof_digest, cost_cents, step FROM campaign_effects "
                "WHERE effect_id = ?", (envelope.effect_id,),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise CampaignError("intent d'effet absent")
            gate_reobservation = (
                current[3] in {"review", "ci", "human-gate"}
                and current[0] in RECEIPT_STATUSES and status in RECEIPT_STATUSES
            )
            if (current[0] != "intent" and not gate_reobservation
                    and current[:3] != (status, proof_digest, cost_cents)):
                connection.rollback()
                raise CampaignError("reçu durable modifié")
            connection.execute(
                "UPDATE campaign_effects SET status = ?, proof_digest = ?, cost_cents = ?, "
                "retry_audit_digest = COALESCE(?, retry_audit_digest) WHERE effect_id = ?",
                (status, proof_digest, cost_cents, retry_audit_digest, envelope.effect_id),
            )
            connection.commit()

    def apply_retry_decision(
        self, envelope: EffectEnvelope, decision: RetryDecision, *, current_tier: str,
    ) -> bool:
        """Atomically audit one idempotent retry decision and advance when authorized."""
        if not isinstance(decision, RetryDecision) or current_tier not in LEVELS:
            raise ValueError("décision retry durable invalide")
        if LEVELS.index(decision.selected_tier) < LEVELS.index(current_tier):
            raise CampaignError("tier retry durable abaissé")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            effect = connection.execute(
                "SELECT retry_audit_digest FROM campaign_effects WHERE campaign_id = ? "
                "AND issue_id = ? AND attempt = ? AND step = ?",
                (envelope.campaign_id, envelope.issue_id, envelope.attempt, envelope.step),
            ).fetchone()
            issue = connection.execute(
                "SELECT attempt, next_step, selected_tier FROM campaign_issues "
                "WHERE campaign_id = ? AND issue_id = ?",
                (envelope.campaign_id, envelope.issue_id),
            ).fetchone()
            if effect is None or issue is None:
                connection.rollback()
                raise CampaignError("coordonnées retry durables absentes")
            if effect[0] is not None:
                prior_audit = connection.execute(
                    "SELECT allowed FROM campaign_retry_decisions "
                    "WHERE audit_digest = ?",
                    (effect[0],),
                ).fetchone()
                if prior_audit is None:
                    # Ledgers written by the reviewed HEAD stored a denied digest
                    # directly on the effect.  Because the issue did not advance,
                    # that digest cannot represent an applied authorization.  Keep
                    # it append-only before accepting a later human window.
                    connection.execute(
                        "INSERT INTO campaign_retry_decisions "
                        "(audit_digest, campaign_id, issue_id, attempt, step, allowed, "
                        "selected_tier, reason, authorization_digest) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?, 'legacy-denied', NULL)",
                        (effect[0], envelope.campaign_id, envelope.issue_id,
                         envelope.attempt, envelope.step, current_tier),
                    )
                    prior_audit = (0,)
            audit = connection.execute(
                "SELECT campaign_id, issue_id, attempt, step, allowed, selected_tier, "
                "reason, authorization_digest FROM campaign_retry_decisions "
                "WHERE audit_digest = ?",
                (decision.audit_digest,),
            ).fetchone()
            expected_audit = (
                envelope.campaign_id, envelope.issue_id, envelope.attempt,
                envelope.step, int(decision.allowed), decision.selected_tier,
                decision.reason, decision.authorization_digest,
            )
            if audit is None:
                connection.execute(
                    "INSERT INTO campaign_retry_decisions "
                    "(audit_digest, campaign_id, issue_id, attempt, step, allowed, "
                    "selected_tier, reason, authorization_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (decision.audit_digest, *expected_audit),
                )
            elif audit != expected_audit:
                connection.rollback()
                raise CampaignError("audit retry append-only modifié")
            if issue[0] == envelope.attempt + 1:
                if (not decision.allowed or effect[0] != decision.audit_digest
                        or issue[1] != "implementation"
                        or issue[2] != decision.selected_tier):
                    connection.rollback()
                    raise CampaignError("rejeu retry durable contradictoire")
                connection.commit()
                return True
            if issue[0] != envelope.attempt or issue[1] != envelope.step:
                connection.rollback()
                raise CampaignError("état retry durable modifié")
            if decision.allowed:
                if effect[0] not in {None, decision.audit_digest}:
                    prior = connection.execute(
                        "SELECT allowed FROM campaign_retry_decisions "
                        "WHERE audit_digest = ?",
                        (effect[0],),
                    ).fetchone()
                    if prior != (0,):
                        connection.rollback()
                        raise CampaignError("autorisation retry durable modifiée")
                connection.execute(
                    "UPDATE campaign_effects SET retry_audit_digest = ? "
                    "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? AND step = ?",
                    (decision.audit_digest, envelope.campaign_id, envelope.issue_id,
                     envelope.attempt, envelope.step),
                )
                updated = connection.execute(
                    "UPDATE campaign_issues SET next_step = 'implementation', "
                    "state = 'retry-approved', selected_tier = ?, attempt = attempt + 1 "
                    "WHERE campaign_id = ? AND issue_id = ? AND attempt = ? "
                    "AND next_step = ?",
                    (decision.selected_tier, envelope.campaign_id, envelope.issue_id,
                     envelope.attempt, envelope.step),
                ).rowcount
                if updated != 1:
                    connection.rollback()
                    raise CampaignError("transition retry durable perdue")
            connection.commit()
        return decision.allowed


class CampaignCoordinator:
    """Run and resume an approved campaign through the standard gated sequence."""

    def __init__(
        self, store: CampaignStore, pipeline: CampaignPipeline,
        executor: ImplementationExecutor, *, owner_id: str,
        now_ms: Callable[[], int] | None = None, lease_ms: int = 60_000,
        control_observer: Callable[[CampaignSpec], str | None] | None = None,
    ):
        self.store = store
        self.pipeline = pipeline
        self.executor = executor
        self.owner_id = _safe_identifier(owner_id, "owner coordinateur")
        self.now_ms = now_ms or (lambda: int(time.time() * 1000))
        self.control_observer = control_observer
        if type(lease_ms) is not int or not 1_000 <= lease_ms <= MAX_LEASE_MS:
            raise ValueError("lease coordinateur hors borne")
        self.lease_ms = lease_ms
        self._lease_id: str | None = None
        self._lease_expires_at = 0

    def request_pause(self, campaign_id: str) -> None:
        self.store.request_control(campaign_id, "pause-requested", now_ms=self.now_ms())

    def request_cancel(self, campaign_id: str) -> None:
        self.store.request_control(campaign_id, "cancel-requested", now_ms=self.now_ms())

    def resume(self, campaign_id: str, reason: str) -> None:
        self.store.resume(campaign_id, reason, now_ms=self.now_ms())

    def _revalidate_paused_gate(
        self, spec: CampaignSpec, *, step: str, reason: str,
    ) -> bool:
        """Re-observe all already-created non-mutating gates by exact identity.

        The DevHub command is still projected as ``paused`` at this point.  Review,
        CI and the human gate are read/reconcile operations on a durable effect, so
        this method may update their receipt without starting a child, dispatching a
        merge or inferring a human approval.  The normal coordinator run advances
        only after this exact receipt is completed and Foundry has published its
        explicit ``paused -> running`` projection.
        """
        candidates = []
        for wave in spec.waves:
            for issue_id in wave:
                issue = self.store.issue(spec.campaign_id, issue_id)
                if issue["state"] != "merged" and issue["next_step"] == step:
                    candidates.append((issue_id, issue))
        if not candidates:
            raise CampaignRevalidationError("paused_gate_receipt_missing")
        unresolved_reason = None
        for issue_id, issue in sorted(candidates):
            envelope = self._envelope(spec, issue_id, issue["attempt"], step)
            durable = self.store.effect(
                spec.campaign_id, issue_id, issue["attempt"], step,
            )
            if durable is None or durable[0] != envelope.effect_id:
                raise CampaignRevalidationError("paused_gate_receipt_missing")
            receipt = self.pipeline.resolve_effect(envelope)
            if receipt is None:
                unresolved_reason = unresolved_reason or reason
                continue
            if (not isinstance(receipt, StepReceipt)
                    or receipt.effect_id != envelope.effect_id):
                raise CampaignError("reçu de gate revalidé contradictoire")
            self.store.finish_effect(
                envelope, status=receipt.status, proof_digest=receipt.proof_digest,
            )
            if receipt.status != "completed":
                unresolved_reason = unresolved_reason or {
                    ("review", "waiting"): "review_waiting",
                    ("review", "blocked"): "review_blocking",
                    ("ci", "waiting"): "ci_waiting",
                    ("ci", "blocked"): "ci_blocked",
                    ("human-gate", "waiting"): "human_gate",
                }.get((step, receipt.status), reason)
        if unresolved_reason is not None:
            self._suspend(spec, unresolved_reason)
            return False
        return True

    def revalidate_paused_projection(self, spec: CampaignSpec) -> bool:
        """Prepare only a proven local resume; never cross an effect boundary.

        DevHub v1 has no human resume command.  A ``paused`` projection can therefore
        become ``running`` only when Foundry already holds an exact local
        authorization, when a fresh authoritative observation proves that the
        original preview blockers (or a known host outage) are resolved, or after
        the same durable read-only gate receipt becomes completed.  The next worker
        scan performs the actual same-effect resume after DevHub has recorded that
        running fact and restored its human control surface.
        """
        row = self.store.campaign(spec.campaign_id)
        if (row.state in {"paused", "suspended"}
                and self.store.engaged_merge_issues(spec.campaign_id)):
            # Receipt reconciliation is the only permitted operation here.  It
            # deliberately re-enters ``run`` rather than reviving the projection:
            # the normal loop observes an external pause/cancel and confirms it
            # only after the exact irreversible merge identity has settled.
            try:
                self.run(spec)
            except CampaignRevalidationError as exc:
                if self.store.campaign(spec.campaign_id).state == "running":
                    self._suspend(spec, str(exc))
            except Exception:
                if self.store.campaign(spec.campaign_id).state == "running":
                    self._suspend(spec, "host_or_effect_failure")
            return False
        if row.state == "running" and row.reason == "manual_retry_approved":
            resume_reason = None
        elif row.state == "suspended" and row.reason == "approved_blockers":
            resume_reason = "proof_available"
        elif row.state == "suspended" and row.reason == "host_unavailable":
            resume_reason = "host_recovered"
        elif row.state == "suspended" and row.reason in _REVALIDATABLE_GATE_REASONS:
            resume_reason = "proof_available"
        elif row.state == "suspended" and row.reason == "parent_acceptance_failure":
            advancement_record = self.store.parent_snapshot_advancement(
                spec.campaign_id,
            )
            if advancement_record is None:
                # Absence of the advancement journal proves the provider mutation
                # boundary was not crossed.  A generic intent may already exist if
                # preparation or a preceding authority read failed; accept only its
                # exact, still-unsettled identity.  Revalidation below remains
                # read-only and the next scan will prepare a fresh provider proof.
                parent_intent = self.store.effect(
                    spec.campaign_id, spec.epic_id, 1, PARENT_ACCEPTANCE_STEP,
                )
                effect_id, work_id = self._identity(
                    spec, spec.epic_id, 1, PARENT_ACCEPTANCE_STEP,
                )
                if (parent_intent is not None
                        and parent_intent != (
                            effect_id, work_id, "intent", None, 0, None, None, None,
                        )):
                    return False
            # Either the exact advancement can be reconciled, or its durable
            # absence proves no parent effect began.  Fresh authority can
            # reactivate only that same bounded path; this projection mutates no
            # tracker state and invents no advancement proof.
            resume_reason = "campaign_revalidated"
        elif row.state == "suspended" and row.reason == "host_or_effect_failure":
            legacy = self.store.legacy_parent_requalification(spec.campaign_id)
            if legacy is not None:
                try:
                    self.pipeline.verify_legacy_parent_requalification(legacy)
                except Exception:
                    self._suspend(spec, "legacy_parent_requalification_invalid")
                    return False
            else:
                advancement_record = self.store.parent_snapshot_advancement(
                    spec.campaign_id,
                )
                close = self.store.effect(
                    spec.campaign_id, spec.epic_id, 1, "close-epic",
                )
                effect_id, work_id = self._identity(
                    spec, spec.epic_id, 1, "close-epic",
                )
                if (advancement_record is None or advancement_record[1] != "completed"
                        or advancement_record[0].binding_digest != spec.binding_digest
                        or advancement_record[0].parent_id != spec.epic_id
                        or close is None
                        or close[:3] != (effect_id, work_id, "completed")
                        or not isinstance(close[3], str)
                        or _DIGEST.fullmatch(close[3]) is None
                        or close[4:] != (0, None, None, None)):
                    return False
            # The provider has recovered either the exact ordinary close receipts
            # or the audited F89 qualification.  Fresh authority is still mandatory
            # below; the next scan only consumes that durable proof.
            resume_reason = "campaign_revalidated"
        else:
            return False
        try:
            self._lease(spec)
            self._observe(spec)
        except CampaignRevalidationError as exc:
            # Preserve an existing suspension reason so a transient lease or
            # observation failure cannot erase the only proof class that may be
            # revalidated on a later scan.  A manually-authorized running row,
            # however, must be suspended when its fresh validation fails.
            if row.state == "running":
                self._suspend(spec, str(exc))
            return False
        except Exception:
            if row.state == "running":
                self._suspend(spec, "host_or_effect_failure")
            return False
        if row.reason in _REVALIDATABLE_GATE_REASONS:
            try:
                if not self._revalidate_paused_gate(
                    spec,
                    step=_REVALIDATABLE_GATE_REASONS[row.reason],
                    reason=row.reason,
                ):
                    return False
            except CampaignRevalidationError as exc:
                self._suspend(spec, str(exc))
                return False
            except Exception:
                self._suspend(spec, "host_or_effect_failure")
                return False
        if resume_reason is not None:
            self.store.resume(
                spec.campaign_id, resume_reason, now_ms=self.now_ms(),
            )
        return True

    def current_outcome(self, spec: CampaignSpec) -> CampaignOutcome:
        return self._outcome(spec)

    def reconcile_engaged_implementations_at_zero_budget(
        self, spec: CampaignSpec,
    ) -> CampaignOutcome:
        """Resolve only already-engaged work when fresh launch budget is empty."""
        row = self.store.campaign(spec.campaign_id)
        if row.state != "suspended" or row.reason != "host_or_effect_failure":
            raise CampaignRevalidationError(
                "zero_budget_reconciliation_not_applicable",
            )
        candidates = []
        for wave in spec.waves:
            for issue_id in wave:
                issue = self.store.issue(spec.campaign_id, issue_id)
                if issue["next_step"] != "implementation":
                    continue
                effect = self.store.effect(
                    spec.campaign_id, issue_id, issue["attempt"], "implementation",
                )
                if (effect is not None and effect[2] == "intent"
                        and self.store.implementation_engagement(effect[0]) == "engaged"):
                    candidates.append((issue_id, issue))
        if not candidates:
            raise CampaignRevalidationError(
                "zero_budget_engaged_effect_missing",
            )

        self.store.resume(
            spec.campaign_id, "host_recovered", now_ms=self.now_ms(),
        )
        try:
            self._lease(spec)
            observation = self._observe(spec)
            if observation.budget_remaining_cents != 0:
                raise CampaignRevalidationError(
                    "zero_budget_reconciliation_not_applicable",
                )
            unresolved = False
            for issue_id, issue in candidates:
                proposal = self._implementation(
                    spec, issue_id, issue, reconcile_intent=True,
                )
                unresolved = unresolved or proposal.outcome == "unknown"
        except BaseException:
            if self.store.campaign(spec.campaign_id).state == "running":
                self._suspend(spec, "host_or_effect_failure")
            raise
        self._suspend(
            spec,
            "host_or_effect_failure" if unresolved else "budget_exhausted",
        )
        return self._outcome(spec)

    def authorize_manual_retry(
        self, campaign_id: str, issue_id: str, attempt: int, *, actor: str,
    ) -> str:
        return self.store.authorize_manual_retry(
            campaign_id, issue_id, attempt, actor=actor, now_ms=self.now_ms(),
        )

    def _lease(self, spec: CampaignSpec) -> _CampaignRow:
        now = self.now_ms()
        if self._lease_id is None:
            row = self.store.acquire_lease(
                spec.campaign_id, self.owner_id, now_ms=now, lease_ms=self.lease_ms,
            )
            assert row.lease_id is not None and row.lease_expires_at is not None
            self._lease_id, self._lease_expires_at = row.lease_id, row.lease_expires_at
            return row
        row = self.store.refresh_lease(
            spec.campaign_id, self.owner_id, self._lease_id,
            now_ms=now, lease_ms=self.lease_ms,
        )
        assert row.lease_expires_at is not None
        self._lease_expires_at = row.lease_expires_at
        return row

    def _observe(self, spec: CampaignSpec) -> CampaignObservation:
        observation = self.pipeline.observe(spec)
        if not isinstance(observation, CampaignObservation):
            raise CampaignRevalidationError("observation_invalid")
        for field in (
            "campaign_id", "command_id", "project", "epic_id", "preview_id",
            "approval_id", "planning_version_id", "preview_digest", "snapshot_digest",
            "policy_digest", "expires_at",
        ):
            if getattr(observation, field) != getattr(spec, field):
                raise CampaignRevalidationError(f"drift_{field}")
        if (observation.approval_state not in APPROVAL_STATES
                or observation.approval_state != "approved"):
            raise CampaignRevalidationError("approval_not_active")
        now = self.now_ms()
        if (type(observation.observed_at) is not int
                or type(observation.valid_until) is not int
                or not observation.observed_at <= now < observation.valid_until
                or now >= spec.expires_at):
            raise CampaignRevalidationError("authority_expired")
        if (type(observation.max_concurrency) is not int
                or not 1 <= observation.max_concurrency <= spec.max_concurrency):
            raise CampaignRevalidationError("concurrency_drift")
        if (type(observation.budget_remaining_cents) is not int
                or not 0 <= observation.budget_remaining_cents <= spec.budget_cents):
            raise CampaignRevalidationError("budget_observation_invalid")
        if (type(observation.provider_invocation_ceiling_cents) is not int
                or not 1 <= observation.provider_invocation_ceiling_cents
                <= spec.budget_cents):
            raise CampaignRevalidationError(
                "provider_invocation_capacity_invalid",
            )
        if observation.minimum_tier not in LEVELS:
            raise CampaignRevalidationError("floor_unknown")
        if (spec.minimum_tier is not None
                and LEVELS.index(observation.minimum_tier) < LEVELS.index(spec.minimum_tier)):
            raise CampaignRevalidationError("floor_lowered")
        if type(observation.host_available) is not bool or not observation.host_available:
            raise CampaignRevalidationError("host_unavailable")
        if type(observation.epic_version) is not int or observation.epic_version < 1:
            raise CampaignRevalidationError("epic_version_invalid")
        if spec.blockers:
            try:
                blockers = self.pipeline.observe_blockers(spec)
            except CampaignRevalidationError:
                raise
            except Exception:
                raise CampaignRevalidationError("blocker_resolution_unknown") from None
            if (not isinstance(blockers, tuple)
                    or any(not isinstance(item, BlockerObservation) for item in blockers)
                    or tuple(item.issue_id for item in blockers) != spec.blockers):
                raise CampaignRevalidationError("blocker_resolution_invalid")
            self.store.record_blocker_observations(
                spec, blockers, now_ms=now,
            )
            if not all(item.resolved for item in blockers):
                raise CampaignRevalidationError("approved_blockers")
        return observation

    @staticmethod
    def _provider_invocation_ceiling(
        observation: CampaignObservation, profile: AttemptProfile,
    ) -> int:
        """Tighten one child call from fresh, independently bounded capacity."""
        ceiling = min(
            profile.cost_ceiling_cents,
            observation.provider_invocation_ceiling_cents,
            observation.budget_remaining_cents // observation.max_concurrency,
        )
        if ceiling < 1:
            raise CampaignRevalidationError("budget_exhausted")
        return ceiling

    @staticmethod
    def _identity(spec: CampaignSpec, issue_id: str, attempt: int, step: str) -> tuple[str, str]:
        material = [spec.binding_digest, issue_id, attempt, step]
        digest = _digest(material)
        return f"effect-{digest[:32]}", f"work-{digest[32:]}"

    def _envelope(
        self, spec: CampaignSpec, issue_id: str, attempt: int, step: str, *,
        selected_tier: str | None = None, cost_ceiling: int = 0,
    ) -> EffectEnvelope:
        if self._lease_id is None or self._lease_expires_at <= self.now_ms():
            raise CampaignLeaseError("lease campagne absent avant effet")
        effect_id, work_id = self._identity(spec, issue_id, attempt, step)
        return EffectEnvelope(
            spec.campaign_id, spec.command_id, spec.project, spec.epic_id,
            issue_id, attempt, step, effect_id, work_id, self._lease_id,
            self._lease_expires_at, spec.binding_digest, spec.snapshot_digest,
            spec.policy_digest, selected_tier, cost_ceiling,
        )

    def _refresh_external_control(self, spec: CampaignSpec) -> None:
        if self.control_observer is not None:
            observed = self.control_observer(spec)
            if observed not in {None, "active", "pause-requested", "cancel-requested"}:
                raise CampaignRevalidationError("control_observation_invalid")
            if observed in {"pause-requested", "cancel-requested"}:
                self.store.request_control(
                    spec.campaign_id, observed, now_ms=self.now_ms(),
                )

    def _control_before_effect(
        self, spec: CampaignSpec, *, allow_merge_observation: bool = False,
    ) -> None:
        self._refresh_external_control(spec)
        row = self.store.campaign(spec.campaign_id)
        if row.state != "running":
            if allow_merge_observation and row.state in {"paused", "cancelled", "suspended"}:
                return
            raise CampaignRevalidationError(row.reason or row.state)
        if row.control == "active":
            return
        if allow_merge_observation:
            return
        state = "paused" if row.control == "pause-requested" else "cancelled"
        reason = row.control.replace("-requested", "")
        self.store.set_state(spec.campaign_id, state, reason, now_ms=self.now_ms())
        raise CampaignRevalidationError(reason)

    def _perform_step(
        self, spec: CampaignSpec, issue_id: str, attempt: int, step: str,
    ) -> StepReceipt:
        if step == "close-epic":
            advancement_record = self.store.parent_snapshot_advancement(spec.campaign_id)
            existing = self.store.effect(spec.campaign_id, issue_id, attempt, step)
            if advancement_record is None or advancement_record[1] != "completed":
                raise CampaignError("avancement parent absent avant cloture")
            if existing is not None:
                self._lease(spec)
                envelope = self._envelope(spec, issue_id, attempt, step)
                if existing[0] != envelope.effect_id:
                    raise CampaignError("identite cloture Epic modifiee")
                reconciled = self.pipeline.reconcile_close_epic(
                    envelope, advancement_record[0],
                )
                if reconciled is not None:
                    if (not isinstance(reconciled, StepReceipt)
                            or reconciled.effect_id != envelope.effect_id
                            or reconciled.status != "completed"):
                        raise CampaignError("recu cloture Epic reconcilie contradictoire")
                    if existing[2] == "completed" and existing[3] != reconciled.proof_digest:
                        raise CampaignError("recu cloture Epic modifie")
                    self.store.finish_effect(
                        envelope, status=reconciled.status,
                        proof_digest=reconciled.proof_digest,
                    )
                    return reconciled
                if existing[2] == "completed":
                    # A local terminal row is only a journaled claim.  The provider
                    # parent and its exact atomic-close audit remain authoritative.
                    # If fresh reconciliation still observes the parent open (or
                    # otherwise cannot prove the close), never fall through to the
                    # generic completed-receipt fast path and never redispatch it.
                    raise CampaignError("recu cloture Epic local non confirme")
        issue_row = self.store.issue(spec.campaign_id, issue_id) if step == "merge" else None
        engaged = (
            step == "merge" and issue_row is not None
            and issue_row["merge_dispatch_started"]
        )
        recovering_engaged_merge = engaged
        # Receipt recovery is a read-only operation on one previously-dispatched
        # merge.  It must happen before fresh approval, host or control reads:
        # those checks authorize new work but cannot make an already-issued merge
        # disappear or justify issuing it again.
        if recovering_engaged_merge:
            envelope = self._envelope(spec, issue_id, attempt, step)
            row, created = self.store.begin_effect(
                envelope, selected_tier=None, cost_ceiling=0,
            )
            if created or row[0] != envelope.effect_id:
                raise CampaignError("reçu merge engagé absent")
            receipt = self.pipeline.resolve_effect(envelope, engage_merge=None)
            if receipt is None:
                return StepReceipt(
                    envelope.effect_id, "unknown",
                    _digest([envelope.effect_id, "merge-receipt-unknown"]),
                )
            if (not isinstance(receipt, StepReceipt)
                    or receipt.effect_id != envelope.effect_id):
                raise CampaignError("reçu pipeline contradictoire")
            self.store.finish_effect(
                envelope, status=receipt.status, proof_digest=receipt.proof_digest,
            )
            return receipt
        self._control_before_effect(spec, allow_merge_observation=engaged)
        self._lease(spec)
        # A previously engaged merge is never dispatched again.  Its exact receipt
        # must remain reconcilable even after the campaign approval expires or the
        # host temporarily disappears; those facts can forbid *new* effects but
        # cannot justify silently abandoning an irreversible one already sent.
        if not recovering_engaged_merge:
            self._observe(spec)
        envelope = self._envelope(spec, issue_id, attempt, step)
        row, created = self.store.begin_effect(
            envelope, selected_tier=None, cost_ceiling=0,
        )

        def prepare_effect(*, observe_engaged_merge: bool = False) -> None:
            # The durable intent is not authority to dispatch.  Re-read human
            # control and all Foundry-owned authority after it exists and directly
            # before either a first call or a recovery call that may mutate.
            self._control_before_effect(
                spec, allow_merge_observation=observe_engaged_merge,
            )
            self._lease(spec)
            if not (recovering_engaged_merge and observe_engaged_merge):
                self._observe(spec)

        def engage_merge() -> None:
            nonlocal engaged
            if step != "merge" or engaged:
                raise CampaignError("engagement merge contradictoire")
            # Called by the trusted pipeline at the irreversible merge dispatch
            # boundary, after its gate/PR preflight.  A control observed here wins
            # and leaves the intent explicitly not engaged.
            prepare_effect()
            self.store.mark_merge_dispatch_started(
                spec.campaign_id, issue_id, attempt,
            )
            engaged = True

        def resolve_effect() -> StepReceipt | None:
            prepare_effect(observe_engaged_merge=engaged)
            if step == "merge":
                return self.pipeline.resolve_effect(
                    envelope, engage_merge=None if engaged else engage_merge,
                )
            return self.pipeline.resolve_effect(envelope)

        def perform_effect() -> StepReceipt:
            prepare_effect(observe_engaged_merge=engaged)
            method_name = PIPELINE_STEP_METHODS.get(step)
            if method_name is None:
                raise CampaignError("primitive pipeline inconnue")
            if step == "merge":
                receipt = self.pipeline.merge_issue(envelope, engage=engage_merge)
                if not engaged:
                    raise CampaignError("merge sans engagement durable")
                return receipt
            return getattr(self.pipeline, method_name)(envelope)

        if row[2] == "intent":
            # A fresh intent proves no provider call has started, so resolution is
            # both unnecessary and unsafe: production resolution may re-enter a
            # mutating Foundry primitive.  Recovered intents must instead resolve
            # the same identity and may never be blindly launched.
            if created:
                receipt = perform_effect()
            else:
                resolved = resolve_effect()
                if resolved is None:
                    return StepReceipt(
                        envelope.effect_id, "unknown",
                        _digest([envelope.effect_id, "unknown"]),
                    )
                receipt = resolved
        else:
            prior = StepReceipt(row[0], row[2], row[3])
            if prior.status == "completed":
                return prior
            receipt = resolve_effect()
            if receipt is None:
                return prior
        if not isinstance(receipt, StepReceipt) or receipt.effect_id != envelope.effect_id:
            raise CampaignError("reçu pipeline contradictoire")
        self.store.finish_effect(
            envelope, status=receipt.status, proof_digest=receipt.proof_digest,
        )
        return receipt

    @staticmethod
    def _validated_parent_acceptance_mapping(
        spec: CampaignSpec, observation: CampaignObservation,
    ) -> tuple[tuple[str, str, str], ...]:
        approved_children = {issue_id for wave in spec.waves for issue_id in wave}
        mapping = observation.acceptance_mapping
        if (not isinstance(mapping, tuple) or not mapping
                or any(not isinstance(item, tuple) or len(item) != 3 for item in mapping)
                or len({item[0] for item in mapping}) != len(mapping)
                or {item[2] for item in mapping} != approved_children):
            raise CampaignRevalidationError("parent_acceptance_mapping_invalid")
        for criterion_id, criterion_digest, issue_id in mapping:
            try:
                _safe_identifier(criterion_id, "critere AC approuve")
                _safe_digest(criterion_digest, "digest critere AC approuve")
            except ValueError:
                raise CampaignRevalidationError(
                    "parent_acceptance_mapping_invalid"
                ) from None
            if not isinstance(issue_id, str) or _ISSUE_ID.fullmatch(issue_id) is None:
                raise CampaignRevalidationError("parent_acceptance_mapping_invalid")
        return mapping

    def _sync_parent_acceptance(self, spec: CampaignSpec) -> StepReceipt:
        """Synchronize only ACs whose exact child merges are durable.

        This is a separate, replayable effect.  Crucially the subsequent F83
        envelope is still computed from the immutable campaign binding, so an
        interrupted close resumes its existing effect identity.
        """
        durable = self.store.parent_snapshot_advancement(spec.campaign_id)
        if durable is not None:
            self.store.validate_parent_acceptance_evidence(spec, durable[0])
        observation = self._observe(spec)
        mapping = self._validated_parent_acceptance_mapping(spec, observation)
        if durable is not None:
            advancement = durable[0]
            proof = self.store.parent_acceptance_proof(
                spec, advancement, epic_version=observation.epic_version,
                acceptance_mapping=mapping,
            )
            self.pipeline.verify_parent_acceptance_proof(proof)
            return self._perform_parent_acceptance(spec, proof)
        children = []
        for issue_id in sorted(issue for wave in spec.waves for issue in wave):
            row = self.store.issue(spec.campaign_id, issue_id)
            effect = self.store.effect(spec.campaign_id, issue_id, row["attempt"], "merge")
            if row["state"] != "merged" or effect is None or effect[2] != "completed":
                raise CampaignRevalidationError("parent_acceptance_child_unproven")
            children.append((issue_id, effect[0], effect[3]))
        proof = ParentAcceptanceProof(
            spec.campaign_id, spec.epic_id, spec.binding_digest, spec.snapshot_digest,
            observation.epic_version, tuple(children), mapping,
        )
        self.pipeline.verify_parent_acceptance_proof(proof)
        return self._perform_parent_acceptance(spec, proof)

    def _perform_parent_acceptance(
        self, spec: CampaignSpec, proof: ParentAcceptanceProof,
    ) -> StepReceipt:
        # Reconcile the exact source/target transition before fresh external
        # authority.  A crash after the provider mutation must remain recoverable,
        # while a source snapshot still requires normal revalidation before write.
        self._lease(spec)
        envelope = self._envelope(spec, spec.epic_id, 1, PARENT_ACCEPTANCE_STEP)
        row, _created = self.store.begin_effect(
            envelope, selected_tier=None, cost_ceiling=0,
        )
        durable = self.store.parent_snapshot_advancement(spec.campaign_id)
        if row[2] == "completed":
            if (durable is None or durable[1] != "completed"
                    or durable[2] != row[3]):
                raise CampaignError("avancement parent terminal absent")
            return StepReceipt(row[0], row[2], row[3])
        if durable is not None:
            advancement = durable[0]
            if (advancement.effect_id != envelope.effect_id
                    or advancement.proof_id != proof.proof_id):
                raise CampaignError("preuve avancement parent modifiee")
            reconciled = self.pipeline.reconcile_parent_acceptance(envelope, advancement)
            if reconciled is not None:
                if (not isinstance(reconciled, StepReceipt)
                        or reconciled.effect_id != envelope.effect_id
                        or reconciled.status != "completed"):
                    raise CampaignError(
                        "recu avancement parent reconcilie contradictoire"
                    )
                self.store.finish_parent_snapshot_advancement(
                    advancement, reconciled, now_ms=self.now_ms(),
                )
                self.store.finish_effect(
                    envelope, status=reconciled.status,
                    proof_digest=reconciled.proof_digest,
                )
                return reconciled
            if durable[1:] != ("intent", None):
                # A locally completed advancement must still be corroborated by
                # the provider target.  Never turn a contradictory source
                # observation into permission to issue the mutation again.
                raise CampaignError("avancement parent local non confirme")
        self._control_before_effect(spec)
        self._lease(spec)
        observation = self._observe(spec)
        mapping = self._validated_parent_acceptance_mapping(spec, observation)
        envelope = self._envelope(spec, spec.epic_id, 1, PARENT_ACCEPTANCE_STEP)
        if durable is None:
            if row[2] != "intent" or row[3] is not None:
                raise CampaignError("intent avancement parent contradictoire")
            advancement = self.pipeline.prepare_parent_acceptance(envelope, proof)
            self.store.begin_parent_snapshot_advancement(
                advancement, now_ms=self.now_ms(),
            )
        else:
            advancement = durable[0]
            if (advancement.effect_id != envelope.effect_id
                    or advancement.proof_id != proof.proof_id):
                raise CampaignError("preuve avancement parent modifiée")
            # ``intent`` says only that the local journal was written.  Rebuild
            # the transition from a fresh trusted parent observation before the
            # provider-facing call.  Exact equality proves the parent is still at
            # the frozen source and that replaying this idempotent transition is
            # the only permitted recovery.
            reproven = self.pipeline.prepare_parent_acceptance(envelope, proof)
            if (not isinstance(reproven, ParentSnapshotAdvancement)
                    or reproven != advancement):
                raise CampaignError("preuve source avancement parent modifiee")
        exact_proof = self.store.parent_acceptance_proof(
            spec, advancement, epic_version=observation.epic_version,
            acceptance_mapping=mapping,
        )
        self.pipeline.verify_parent_acceptance_proof(exact_proof)
        if exact_proof != proof:
            raise CampaignError("preuve avancement parent modifiee")
        receipt = self.pipeline.sync_parent_acceptance(envelope, proof, advancement)
        if not isinstance(receipt, StepReceipt) or receipt.effect_id != envelope.effect_id:
            raise CampaignError("reçu synchronisation parent contradictoire")
        if receipt.status == "completed":
            self.store.finish_parent_snapshot_advancement(
                advancement, receipt, now_ms=self.now_ms(),
            )
        self.store.finish_effect(envelope, status=receipt.status, proof_digest=receipt.proof_digest)
        return receipt

    @staticmethod
    def _unknown_implementation(
        envelope: EffectEnvelope, selected_tier: str,
    ) -> ImplementationProposal:
        return ImplementationProposal(
            envelope.work_id, envelope.issue_id, envelope.attempt, "unknown",
            _digest([envelope.work_id, "unknown"]), 0, selected_tier,
        )

    @staticmethod
    def _validate_implementation_proposal(
        proposal: object, envelope: EffectEnvelope, ceiling: int,
    ) -> ImplementationProposal:
        if (not isinstance(proposal, ImplementationProposal)
                or proposal.work_id != envelope.work_id
                or proposal.issue_id != envelope.issue_id
                or proposal.attempt != envelope.attempt
                or proposal.selected_tier != envelope.selected_tier
                or proposal.cost_cents > ceiling):
            raise CampaignError("proposition agent contradictoire")
        return proposal

    def _safe_implementation_reconciliation(
        self, envelope: EffectEnvelope,
    ) -> ImplementationReconciliation:
        engagement = self.store.implementation_engagement(envelope.effect_id)
        if engagement == "reserved":
            # The coordinator durably proves it never crossed its own provider
            # boundary.  ``resolve`` has already freshly observed no receipt.
            return ImplementationReconciliation(
                envelope.work_id, "not-engaged",
                _digest([envelope.effect_id, envelope.work_id, "reserved-not-engaged"]),
                0,
            )
        reconcile = getattr(self.executor, "reconcile", None)
        if reconcile is None:
            return ImplementationReconciliation(
                envelope.work_id, "unknown",
                _digest([envelope.effect_id, envelope.work_id, "reconciliation-unknown"]),
                0,
            )
        result = reconcile(envelope)
        if (not isinstance(result, ImplementationReconciliation)
                or result.work_id != envelope.work_id
                or result.cost_cents > envelope.cost_ceiling_cents):
            raise CampaignError("réconciliation agent contradictoire")
        return result

    def _implementation(
        self, spec: CampaignSpec, issue_id: str, issue_row: dict, *,
        reconcile_intent: bool = False,
    ) -> ImplementationProposal:
        attempt = issue_row["attempt"]
        self._control_before_effect(spec)
        self._lease(spec)
        observation = self._observe(spec)
        profile = self.executor.execution_profile(issue_id, attempt)
        if not isinstance(profile, AttemptProfile):
            raise CampaignError("profil agent invalide")
        floor = observation.minimum_tier
        durable = self.store.effect(
            spec.campaign_id, issue_id, attempt, "implementation",
        )
        if durable is None:
            # Fresh capacity authorizes only a distinct new provider invocation.
            # Reading an already-identified work effect below needs no new budget.
            invocation_ceiling = self._provider_invocation_ceiling(observation, profile)
            selected = issue_row["selected_tier"] or profile.selected_tier
            selected = max((selected, floor), key=LEVELS.index)
            cost_ceiling = invocation_ceiling
        else:
            # Reconciliation never retroactively changes the tier of an already
            # identified work effect.  A raised floor applies to the distinct next
            # attempt only.
            selected = durable[6]
            if selected not in LEVELS:
                raise CampaignError("tier d'implémentation durable invalide")
            if (type(durable[4]) is not int
                    or not 1 <= durable[4] <= profile.cost_ceiling_cents):
                raise CampaignError("plafond d'implémentation durable invalide")
            cost_ceiling = durable[4]
        # The envelope carries the tightened tier explicitly; the executor must
        # honor that exact value and echo it in its proposal.
        envelope = self._envelope(
            spec, issue_id, attempt, "implementation", selected_tier=selected,
            cost_ceiling=cost_ceiling,
        )
        existing, created = self.store.begin_implementation(
            spec, envelope, observed_remaining=observation.budget_remaining_cents,
            observed_max_concurrency=observation.max_concurrency,
            now_ms=self.now_ms(),
        )
        # Always observe the work identity afresh.  A durable blocked/unavailable
        # row is audit history, not evidence that the external condition is current.
        proposal = self.executor.resolve(envelope.work_id)
        if proposal is not None:
            proposal = self._validate_implementation_proposal(
                proposal, envelope, envelope.cost_ceiling_cents,
            )
            if existing[2] == "intent":
                self.store.finish_implementation(
                    spec, envelope, proposal, now_ms=self.now_ms(),
                )
            elif existing[2:7] != (
                proposal.outcome, proposal.proof_digest, existing[4],
                proposal.cost_cents, proposal.selected_tier,
            ):
                raise CampaignError("proposition durable modifiée")
            return proposal
        if not created and existing[2] != "intent":
            reconciliation = self._safe_implementation_reconciliation(envelope)
            if (existing[2] == "host-unavailable"
                    and reconciliation.state in {"not-engaged", "terminated"}
                    and existing[3] == reconciliation.proof_digest
                    and existing[5] == reconciliation.cost_cents):
                return ImplementationProposal(
                    envelope.work_id, issue_id, attempt, "host-unavailable",
                    reconciliation.proof_digest, reconciliation.cost_cents, selected,
                )
            return self._unknown_implementation(envelope, selected)
        if not created and reconcile_intent:
            reconciliation = self._safe_implementation_reconciliation(envelope)
            if reconciliation.state in {"not-engaged", "terminated"}:
                proposal = ImplementationProposal(
                    envelope.work_id, issue_id, attempt, "host-unavailable",
                    reconciliation.proof_digest, reconciliation.cost_cents, selected,
                )
                self.store.finish_implementation(
                    spec, envelope, proposal, now_ms=self.now_ms(),
                )
                return proposal
            return self._unknown_implementation(envelope, selected)
        if proposal is None and created:
            try:
                self._control_before_effect(spec)
                self._lease(spec)
                latest = self._observe(spec)
                fresh_ceiling = self._provider_invocation_ceiling(latest, profile)
                if fresh_ceiling < envelope.cost_ceiling_cents:
                    self.store.tighten_reserved_implementation_ceiling(
                        envelope, fresh_ceiling,
                    )
                    envelope = replace(
                        envelope, cost_ceiling_cents=fresh_ceiling,
                    )
                execution_tier = max(
                    (selected, latest.minimum_tier), key=LEVELS.index,
                )
                if execution_tier != selected:
                    self.store.raise_reserved_implementation_tier(
                        envelope, execution_tier,
                    )
                    selected = execution_tier
                # Refresh both the monotone tier and the just-renewed lease in the
                # exact envelope that crosses the provider boundary.
                assert self._lease_id is not None
                envelope = replace(
                    envelope, selected_tier=selected,
                    lease_id=self._lease_id,
                    lease_expires_at=self._lease_expires_at,
                )
                self.store.mark_implementation_engaged(envelope)
                proposal = self.executor.execute(envelope)
            except BaseException:
                # The reservation intentionally remains durable after ambiguity.
                # Resume must resolve the work identity rather than launch again.
                raise
        elif proposal is None:
            return self._unknown_implementation(envelope, selected)
        proposal = self._validate_implementation_proposal(
            proposal, envelope, envelope.cost_ceiling_cents,
        )
        self.store.finish_implementation(
            spec, envelope, proposal, now_ms=self.now_ms(),
        )
        return proposal

    def _reconcile_resumed_implementations(
        self, spec: CampaignSpec, reason: str | None,
    ) -> None:
        if reason not in {"host_recovered", "manual_retry_approved"}:
            return
        for wave in spec.waves:
            for issue_id in wave:
                issue = self.store.issue(spec.campaign_id, issue_id)
                if issue["next_step"] != "implementation":
                    continue
                effect = self.store.effect(
                    spec.campaign_id, issue_id, issue["attempt"], "implementation",
                )
                if effect is None:
                    continue
                proposal = self._implementation(
                    spec, issue_id, issue, reconcile_intent=True,
                )
                expected = {
                    "host_recovered": "host-unavailable",
                    "manual_retry_approved": "blocked",
                }[reason]
                if proposal.outcome != expected:
                    continue
                envelope = self._envelope(
                    spec, issue_id, issue["attempt"], "implementation",
                    selected_tier=proposal.selected_tier,
                    cost_ceiling=effect[4],
                )
                self.store.prepare_resumed_implementation(
                    spec, envelope, proposal, reason=reason, now_ms=self.now_ms(),
                )

    def _retry(
        self, spec: CampaignSpec, issue_id: str, issue_row: dict, signal: str,
    ) -> bool:
        if issue_row["attempt"] >= spec.max_attempts_per_issue:
            return False
        self._control_before_effect(spec)
        self._lease(spec)
        observation = self._observe(spec)
        current_tier = issue_row["selected_tier"] or observation.minimum_tier
        assert current_tier is not None
        envelope = self._envelope(
            spec, issue_id, issue_row["attempt"], issue_row["next_step"],
            selected_tier=current_tier,
        )
        decision = self.pipeline.authorize_retry(envelope, signal, current_tier)
        if not isinstance(decision, RetryDecision):
            raise CampaignError("décision retry provider invalide")
        return self.store.apply_retry_decision(
            envelope, decision, current_tier=current_tier,
        )

    def _suspend(self, spec: CampaignSpec, reason: str) -> None:
        self.store.set_state(
            spec.campaign_id, "suspended", reason, now_ms=self.now_ms(),
        )

    def _advance_issue(self, spec: CampaignSpec, issue_id: str) -> None:
        while True:
            row = self.store.issue(spec.campaign_id, issue_id)
            if row["state"] == "merged":
                return
            step, attempt = row["next_step"], row["attempt"]
            if step == "implementation":
                proposal = self._implementation(spec, issue_id, row)
                if proposal.outcome == "completed":
                    self.store.advance_issue(
                        spec.campaign_id, issue_id, next_step="open-pr",
                        state="implementation-proposed",
                        selected_tier=proposal.selected_tier,
                    )
                    continue
                if proposal.outcome == "failed" and self._retry(
                    spec, issue_id, self.store.issue(spec.campaign_id, issue_id),
                    proposal.failure_signal or "test_red",
                ):
                    continue
                reason = {
                    "blocked": "blocker", "host-unavailable": "host_unavailable",
                    "unknown": "implementation_unknown", "failed": "retry_exhausted",
                }[proposal.outcome]
                self._suspend(spec, reason)
                return

            receipt = self._perform_step(spec, issue_id, attempt, step)
            if receipt.status != "completed":
                if step == "review" and receipt.status == "blocked" and self._retry(
                    spec, issue_id, self.store.issue(spec.campaign_id, issue_id),
                    self.store.review_failure_signal(
                        spec.campaign_id, issue_id, attempt,
                    ),
                ):
                    continue
                reason = {
                    ("human-gate", "waiting"): "human_gate",
                    ("review", "blocked"): "review_blocking",
                    ("merge", "unknown"): "merge_ambiguous",
                }.get((step, receipt.status))
                if reason is None:
                    reason = (
                        "host_unavailable" if receipt.status == "unavailable"
                        else f"{step}_{receipt.status}"
                    )
                self._suspend(spec, reason)
                return
            if step == "merge":
                self.store.advance_issue(
                    spec.campaign_id, issue_id, next_step="done", state="merged",
                    engaged_merge=False, merge_dispatch_started=False,
                )
                return
            next_step = REQUIRED_STEPS[REQUIRED_STEPS.index(step) + 1]
            self.store.advance_issue(
                spec.campaign_id, issue_id, next_step=next_step,
                state=f"{step}-completed",
            )

    def _outcome(self, spec: CampaignSpec) -> CampaignOutcome:
        row = self.store.campaign(spec.campaign_id)
        return CampaignOutcome(
            spec.campaign_id, row.state, row.reason, row.wave_index,
            self.store.completed_issues(spec.campaign_id), row.budget_spent,
            row.peak_concurrency,
        )

    def run(self, spec: CampaignSpec) -> CampaignOutcome:
        now = self.now_ms()
        self.store.initialize(spec, now_ms=now)
        row = self._lease(spec)
        # Recover an exact merge dispatch before consulting dynamic authority or
        # host/control state.  A stale/expired approval can prohibit new effects,
        # but it cannot erase a previously crossed irreversible boundary.
        dispatched_merges = self.store.engaged_merge_issues(spec.campaign_id)
        if dispatched_merges:
            for issue_id in dispatched_merges:
                issue = self.store.issue(spec.campaign_id, issue_id)
                try:
                    receipt = self._perform_step(
                        spec, issue_id, issue["attempt"], "merge",
                    )
                    if receipt.status == "completed":
                        self.store.advance_issue(
                            spec.campaign_id, issue_id, next_step="done", state="merged",
                            engaged_merge=False, merge_dispatch_started=False,
                        )
                    else:
                        if self.store.campaign(spec.campaign_id).state == "running":
                            self._suspend(spec, "merge_ambiguous")
                except Exception:
                    # Preserve an existing pause/suspension reason: it is still
                    # the operator-visible reason for why no new work may start.
                    if self.store.campaign(spec.campaign_id).state == "running":
                        self._suspend(spec, "host_or_effect_failure")
            if self.store.engaged_merge_issues(spec.campaign_id):
                return self._outcome(spec)
        try:
            self._refresh_external_control(spec)
        except Exception:
            self._suspend(spec, "host_or_effect_failure")
            return self._outcome(spec)
        row = self.store.campaign(spec.campaign_id)
        if row.state in {"completed", "cancelled"}:
            return self._outcome(spec)
        engaged_merges = self.store.engaged_merge_issues(spec.campaign_id)
        if engaged_merges and (
            row.control != "active" or row.state in {"paused", "suspended"}
        ):
            # A control stops every new effect, but an already-engaged merge must
            # first be reconciled through its exact effect identity.  This is
            # observation/recovery only: no other child or parent step can start.
            for issue_id in self.store.engaged_merge_issues(spec.campaign_id):
                try:
                    self._advance_issue(spec, issue_id)
                except CampaignRevalidationError as exc:
                    if self.store.campaign(spec.campaign_id).state == "running":
                        self._suspend(spec, str(exc))
                except Exception:
                    if self.store.campaign(spec.campaign_id).state == "running":
                        self._suspend(spec, "host_or_effect_failure")
                current = self.store.campaign(spec.campaign_id)
                if current.state in {"completed", "cancelled"}:
                    return self._outcome(spec)
            row = self.store.campaign(spec.campaign_id)
        if row.control != "active":
            # A prior suspension is not permission to re-enter the pipeline, but it
            # must not make a later human pause/cancel request unconfirmable.  Keep
            # reconciling an engaged merge until its receipt settles; otherwise
            # confirming the control would silently abandon an already-started merge.
            if self.store.engaged_merge_issues(spec.campaign_id):
                return self._outcome(spec)
            current = self.store.campaign(spec.campaign_id)
            if current.control not in {"pause-requested", "cancel-requested"}:
                return self._outcome(spec)
            state = (
                "paused" if current.control == "pause-requested" else "cancelled"
            )
            self.store.set_state(
                spec.campaign_id, state,
                current.control.replace("-requested", ""),
                now_ms=now,
            )
            return self._outcome(spec)
        if row.state in {"paused", "suspended"}:
            return self._outcome(spec)
        if spec.scheduled_for is not None and now < spec.scheduled_for:
            self.store.set_state(spec.campaign_id, "scheduled", "not_due", now_ms=now)
            return self._outcome(spec)
        try:
            observation = self._observe(spec)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, CampaignRevalidationError) else "host_or_effect_failure"
            self._suspend(spec, reason)
            return self._outcome(spec)
        del observation
        resume_reason = row.reason if row.reason in RESUME_REASONS else None
        try:
            self._reconcile_resumed_implementations(spec, resume_reason)
        except CampaignRevalidationError as exc:
            self._suspend(spec, str(exc))
            return self._outcome(spec)
        except Exception:
            self._suspend(spec, "host_or_effect_failure")
            return self._outcome(spec)
        self.store.set_state(spec.campaign_id, "running", None, now_ms=self.now_ms())

        for wave_index, wave in enumerate(spec.waves):
            while True:
                unfinished = [
                    issue_id for issue_id in wave
                    if self.store.issue(spec.campaign_id, issue_id)["state"] != "merged"
                ]
                if not unfinished:
                    break
                try:
                    self._control_before_effect(spec)
                    latest = self._observe(spec)
                    concurrency = min(spec.max_concurrency, latest.max_concurrency)
                except CampaignRevalidationError as exc:
                    if self.store.campaign(spec.campaign_id).state == "running":
                        self._suspend(spec, str(exc))
                    return self._outcome(spec)
                except Exception:
                    if self.store.campaign(spec.campaign_id).state == "running":
                        self._suspend(spec, "host_or_effect_failure")
                    return self._outcome(spec)
                batch = unfinished[:concurrency]
                with ThreadPoolExecutor(
                    max_workers=len(batch),
                    thread_name_prefix=f"foundry-{spec.campaign_id}",
                ) as pool:
                    futures = {
                        pool.submit(self._advance_issue, spec, issue_id): issue_id
                        for issue_id in batch
                    }
                    for future in as_completed(futures):
                        try:
                            future.result()
                        except _CampaignCapacityDeferred:
                            # A sibling already owns the freshly tightened slot.
                            # Recompute capacity and schedule this child in the next
                            # bounded batch instead of abandoning the wave.
                            pass
                        except CampaignRevalidationError as exc:
                            if self.store.campaign(spec.campaign_id).state == "running":
                                self._suspend(spec, str(exc))
                        except Exception:
                            if self.store.campaign(spec.campaign_id).state == "running":
                                self._suspend(spec, "host_or_effect_failure")
                current = self.store.campaign(spec.campaign_id)
                if current.state != "running":
                    return self._outcome(spec)
            self.store.set_state(
                spec.campaign_id, "running", None, now_ms=self.now_ms(),
                wave_index=wave_index + 1,
            )

        # Parent ACs may be projected only from the campaign's own terminal merge
        # receipts.  This is not a DevHub gate and does not replace F83.
        legacy = self.store.legacy_parent_requalification(spec.campaign_id)
        if legacy is not None:
            try:
                self.pipeline.verify_legacy_parent_requalification(legacy)
            except Exception:
                if self.store.campaign(spec.campaign_id).state == "running":
                    self._suspend(spec, "legacy_parent_requalification_invalid")
                return self._outcome(spec)
            self.store.set_state(
                spec.campaign_id, "completed", None, now_ms=self.now_ms(),
                wave_index=len(spec.waves),
            )
            return self._outcome(spec)
        try:
            synced = self._sync_parent_acceptance(spec)
        except CampaignRevalidationError as exc:
            if self.store.campaign(spec.campaign_id).state == "running":
                self._suspend(spec, str(exc))
            return self._outcome(spec)
        except Exception:
            if self.store.campaign(spec.campaign_id).state == "running":
                self._suspend(spec, "parent_acceptance_failure")
            return self._outcome(spec)
        if synced.status != "completed":
            self._suspend(spec, f"parent_acceptance_{synced.status}")
            return self._outcome(spec)

        # Parent closure is a separate, provider-audited FOUNDRY-83 effect.  It is
        # never substituted for any child merge gate.
        try:
            receipt = self._perform_step(spec, spec.epic_id, 1, "close-epic")
        except CampaignRevalidationError as exc:
            if self.store.campaign(spec.campaign_id).state == "running":
                self._suspend(spec, str(exc))
            return self._outcome(spec)
        except Exception:
            if self.store.campaign(spec.campaign_id).state == "running":
                self._suspend(spec, "host_or_effect_failure")
            return self._outcome(spec)
        if receipt.status != "completed":
            self._suspend(spec, f"parent_close_{receipt.status}")
            return self._outcome(spec)
        self.store.set_state(
            spec.campaign_id, "completed", None, now_ms=self.now_ms(),
            wave_index=len(spec.waves),
        )
        return self._outcome(spec)
