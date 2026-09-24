"""Deterministic, issue-scoped escalation state shared by Claude Code and Codex."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from foundry import registry
from foundry.routing import (
    LEVELS,
    ROLE_DEFAULTS,
    RoutingConfigError,
    RoutingUnavailableError,
    repository_identity,
)


FAILURE_KINDS = ("test_red", "review_blocking", "review_blocking_after_fix")
RISK_FLOORS = {
    "security": "frontier",
    "data_migration": "frontier",
    "concurrency": "frontier",
    "public_api": "frontier",
    "release": "frontier",
    "adr_creation": "apex",
    "adr_reconsideration": "apex",
}
MAX_ESCALATIONS_PER_ISSUE = 2
MAX_PARALLEL_AGENTS = 4
MAX_REMEDIATION_CREDITS = 3
MAX_DIAGNOSTIC_ATTEMPTS = 3
MAX_TECHNICAL_REMEDIATIONS_PER_GENERATION = 1
RESUME_REASON_CODES = (
    "remediation_reviewed",
    "risk_accepted",
    "manual_retry_approved",
)
REQUEST_CATEGORIES = (
    "model_tier",
    "strategy_decision",
    "product_decision",
    "durable_ambiguity",
)
_ISSUE_ID = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*\Z")
_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)
_REMEDIATION_CONSUMPTION_CODE = "review_blocking_after_fix_consumed"
_REMEDIATION_REARM_CODE = "remediation_window_rearmed"
_SAFE_OPEN_FLAGS = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_ROLE_STATE_KEYS = {
    "minimum_tier", "escalations", "deterministic_failures",
    "failures_since_escalation",
}
_CONSUMPTION_EVENT_KEYS = {"code", "at", "role", "halt_generation"}
_REARM_EVENT_KEYS = {
    "code", "at", "reason", "role", "halt_generation", "granted_credits",
    "exhausted_window_armed_at", "exhausted_window_maximum_credits",
}
_TECHNICAL_GENERATION_REARM_EVENT_KEYS = _REARM_EVENT_KEYS | {
    "exhausted_halt_generation",
}
_REMEDIATION_KEYS = {
    "state", "role", "halt_generation", "maximum_credits", "remaining_credits",
    "armed_at", "consumption_audit", "consumed_credits", "forfeited_credits",
}
_LEDGER_REQUIRED_KEYS = {
    "version", "issue_id", "total_escalations", "halted", "halted_reason", "roles",
}
_LEDGER_OPTIONAL_KEYS = {
    "halt_generation", "halted_role", "resume_count", "last_resumed_at",
    "last_resume_reason", "last_resumed_halt_generation",
    "remediation_authorization", "consumption_audit", "remediation_rearm_audit",
    "failure_receipts", "campaign_review_retry_receipts", "terminal_outcome",
    "terminal_reclassification", "technical_remediation_audit",
}
_FAILURE_RECEIPT_KEYS = {"role", "kind", "current_tier", "decision"}
_DECISION_REQUIRED_KEYS = {
    "issue_id", "role", "action", "initial_tier", "final_tier",
    "issue_escalations", "role_escalations", "deterministic_failures",
    "failures_since_escalation", "signal", "reason", "human_required",
    "max_escalations_per_issue", "max_parallel_agents",
}
_DECISION_OPTIONAL_KEYS = {
    "remediation_authorization", "authority_category", "technical_remediation",
}
_FAILURE_IDEMPOTENCY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,159}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CAMPAIGN_REVIEW_RECEIPT_KEYS = {
    "authorization_digest", "effect_id", "state", "decision",
}
_CAMPAIGN_REVIEW_DECISION_KEYS = {
    "allowed", "selected_tier", "audit_digest", "action",
    "authorization_digest",
}
MAX_FAILURE_RECEIPTS = 128
MAX_CAMPAIGN_REVIEW_RECEIPTS = 32
_HUMAN_AUTHORITY_CATEGORIES = frozenset({
    "strategy_decision", "product_decision", "durable_ambiguity",
})
_TECHNICAL_AUTHORITY_CATEGORIES = frozenset({
    "deterministic_failure", "technical_policy",
})
_TERMINAL_SIGNALS = frozenset({
    *FAILURE_KINDS,
    *RISK_FLOORS,
    "explicit_user_request",
    "cancel_remediation",
    *_HUMAN_AUTHORITY_CATEGORIES,
})
_DIAGNOSTIC_KEYS = {
    "outcome", "attempts", "maximum_attempts", "evidence_digest",
}
_TERMINAL_OUTCOME_KEYS = {
    "action", "category", "reason_code", "signal", "role", "halt_generation",
    "diagnostic", "source_receipt",
}
_TERMINAL_RECLASSIFICATION_KEYS = {
    "code", "at", "source_receipt", "halt_generation", "signal", "role",
    "previous_halted_reason", "evidence_kind", "causal_digest", "causal_facts",
}
_LEGACY_CAUSAL_FACT_KEYS = {
    "contract", "issue_id", "total_escalations", "halt_generation", "halted_role",
    "previous_halted_reason", "signal", "roles", "resume_count",
    "last_resumed_at", "last_resumed_halt_generation", "remediation_state",
    "failure_receipt_keys",
}
_LEGACY_CAUSAL_REMEDIATION_STATES = frozenset({
    "none", "active", "exhausted", "cancelled", "invalidated",
})
_TECHNICAL_REMEDIATION_KEYS = {
    "path", "reason_code", "provider_effect_allowed", "campaign_restart_allowed",
    "human_authorization_required",
}
_TECHNICAL_REMEDIATION_AUDIT_KEYS = {
    "code", "at", "halt_generation", "evidence_digest", "role", "route_id",
    "route_consumed_at", "review_diff_hash", "review_claimed_at",
    "review_rearm_audit",
}
_LEGACY_TECHNICAL_REMEDIATION_AUDIT_KEYS = (
    _TECHNICAL_REMEDIATION_AUDIT_KEYS - {
        "review_diff_hash", "review_claimed_at", "review_rearm_audit",
    }
)
_PRE_REARM_TECHNICAL_REMEDIATION_AUDIT_KEYS = (
    _TECHNICAL_REMEDIATION_AUDIT_KEYS - {"review_rearm_audit"}
)
_LEGACY_TECHNICAL_REVIEW_REARM_AUDIT_KEYS = {
    "code", "previous_diff_hash", "previous_claimed_at", "terminal_proof_id",
    "new_diff_hash", "rearmed_at",
}
_TECHNICAL_REVIEW_REARM_AUDIT_KEYS = (
    _LEGACY_TECHNICAL_REVIEW_REARM_AUDIT_KEYS
    | {"terminal_completed_at", "terminal_quality", "repair_kind"}
)
_TECHNICAL_REVIEW_REARM_BINDING_KEYS = {
    "proof_id", "completed_at", "quality", "all_pass",
}
_RECLASSIFICATION_CODE = "legacy_human_required_reclassified_technical"
_TECHNICAL_REMEDIATION_RESUME_CODE = "technical_remediation_resumed"
_TECHNICAL_REMEDIATION_ROUTE_CODE = "technical_remediation_route_claimed"
_LEGACY_LEDGER_EVIDENCE_KIND = "complete_legacy_ledger"
_LEGACY_RECEIPT_EVIDENCE_KIND = "failure_receipt"
_LEGACY_LEDGER_EVIDENCE_PREFIX = "legacy-ledger-halt-generation-"
_TECHNICAL_REASON_MESSAGES = {
    "apex_deterministic_failure": (
        "Échec déterministe toujours présent au niveau apex ; diagnostic technique "
        "local borné requis."
    ),
    "escalation_ceiling_reached": (
        f"Plafond technique de {MAX_ESCALATIONS_PER_ISSUE} escalades atteint ; "
        "diagnostic technique local borné requis."
    ),
    "routing_apex_reached": (
        "Niveau apex déjà atteint pour une demande de routage ; diagnostic technique "
        "local borné requis."
    ),
    "remediation_window_exhausted": (
        "Crédits de remédiation épuisés ; diagnostic technique local borné requis."
    ),
    "remediation_window_cancelled": (
        "Fenêtre de remédiation annulée ; diagnostic technique local borné requis."
    ),
    "remediation_authorization_invalid": (
        "Autorisation de remédiation invalide ; diagnostic technique local borné requis."
    ),
    "signal_outside_remediation_window": (
        "Signal hors fenêtre de remédiation ; diagnostic technique local borné requis."
    ),
    "remediation_window_inactive": (
        "Fenêtre de remédiation inactive ; diagnostic technique local borné requis."
    ),
    "legacy_deterministic_failure_reclassified": (
        "Arrêt historique reclassé comme échec déterministe ; diagnostic technique "
        "local borné requis."
    ),
}
_HUMAN_REASON_MESSAGES = {
    "strategy_decision_requested": "Décision de stratégie explicitement demandée.",
    "product_decision_requested": "Décision produit explicitement demandée.",
    "durable_ambiguity_attested": (
        "Ambiguïté durable attestée par un diagnostic borné."
    ),
}
_TERMINAL_REMEDIATION_REASONS = {
    "autorisation de remédiation annulée ; intervention humaine requise.",
    "autorisation de remédiation invalide ; intervention humaine requise.",
    "signal hors autorisation de remédiation ; intervention humaine requise.",
    "autorisation de remédiation inactive ; intervention humaine requise.",
    "crédits de remédiation épuisés ; intervention humaine requise.",
}
_CONTROLLED_HALT_REASONS = frozenset(
    _TERMINAL_REMEDIATION_REASONS
    | set(_TECHNICAL_REASON_MESSAGES.values())
    | set(_HUMAN_REASON_MESSAGES.values())
    | {
        f"plafond de {MAX_ESCALATIONS_PER_ISSUE} escalades atteint ; "
        f"signal supplémentaire {signal} refusé."
        for signal in (*FAILURE_KINDS, *RISK_FLOORS, "explicit_user_request")
    }
    | {
        f"{role} est déjà au niveau apex après le signal {kind}."
        for role in ROLE_DEFAULTS
        for kind in FAILURE_KINDS
    }
    | {
        f"{role} est déjà au niveau apex après la demande explicite."
        for role in ROLE_DEFAULTS
    }
)


class EscalationHumanRequiredError(RoutingUnavailableError):
    """A valid strategy, product, or attested ambiguity verdict is required."""


class EscalationTechnicalBlockedError(RoutingUnavailableError):
    """A deterministic technical block prevents delegation or campaign effects."""


class EscalationAuthorityAmbiguousError(RoutingUnavailableError):
    """A legacy or untrusted terminal cause is fail-closed and unclassified."""


@dataclass(frozen=True)
class BoundedDiagnostic:
    """Typed proof that a bounded diagnostic ended in durable ambiguity."""

    outcome: str
    attempts: int
    maximum_attempts: int
    evidence_digest: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EscalationDecision:
    issue_id: str
    role: str
    action: str
    initial_tier: str
    final_tier: str
    issue_escalations: int
    role_escalations: int
    deterministic_failures: int
    failures_since_escalation: int
    signal: str
    reason: str
    human_required: bool
    max_escalations_per_issue: int = MAX_ESCALATIONS_PER_ISSUE
    max_parallel_agents: int = MAX_PARALLEL_AGENTS
    remediation_authorization: dict | None = None
    authority_category: str | None = None
    technical_remediation: dict | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        for optional in (
            "remediation_authorization", "authority_category", "technical_remediation",
        ):
            if payload[optional] is None:
                payload.pop(optional)
        return payload


def _validate_issue_id(issue_id: str) -> str:
    if not isinstance(issue_id, str) or not _ISSUE_ID.fullmatch(issue_id):
        raise RoutingConfigError(
            "issue d'escalade invalide : identifiant de type FOUNDRY-42 attendu."
        )
    return issue_id


def _validate_role(role: str) -> str:
    if not isinstance(role, str) or role not in ROLE_DEFAULTS:
        raise RoutingConfigError(
            f"rôle d'escalade '{role}' inconnu ; attendu : {', '.join(ROLE_DEFAULTS)}."
        )
    return role


def _validate_tier(tier: str) -> str:
    if not isinstance(tier, str) or tier not in LEVELS:
        raise RoutingConfigError(
            f"tier d'escalade '{tier}' inconnu ; attendu : {', '.join(LEVELS)}."
        )
    return tier


def _validate_resume_reason(reason: str) -> str:
    """Accept only a public, non-sensitive human audit code."""
    if not isinstance(reason, str) or reason not in RESUME_REASON_CODES:
        raise RoutingConfigError(
            "code de reprise invalide ; code contrôlé attendu."
        )
    return reason


def _validate_halt_generation(halt_generation: int) -> int:
    if type(halt_generation) is not int or halt_generation <= 0:
        raise RoutingConfigError("génération d'arrêt positive attendue.")
    return halt_generation


def _validate_remediation_credits(credits: int) -> int:
    if type(credits) is not int or not 1 <= credits <= MAX_REMEDIATION_CREDITS:
        raise RoutingConfigError(
            f"crédits de remédiation : entier de 1 à {MAX_REMEDIATION_CREDITS} attendu."
        )
    return credits


def _validate_request_category(category: str) -> str:
    if not isinstance(category, str) or category not in REQUEST_CATEGORIES:
        raise RoutingConfigError("catégorie de demande d'escalade invalide.")
    return category


def _normalize_bounded_diagnostic(
    value: object,
    *,
    issue_id: str | None = None,
) -> dict:
    """Validate a compact diagnostic attestation without echoing hostile input."""
    raw = value.to_dict() if isinstance(value, BoundedDiagnostic) else value
    if not isinstance(raw, dict) or set(raw) != _DIAGNOSTIC_KEYS:
        if issue_id is not None:
            raise _invalid_ledger(issue_id)
        raise RoutingConfigError("attestation de diagnostic borné invalide.")
    attempts = raw.get("attempts")
    maximum = raw.get("maximum_attempts")
    digest = raw.get("evidence_digest")
    if (
        raw.get("outcome") != "durable_ambiguity"
        or type(attempts) is not int
        or type(maximum) is not int
        or not 1 <= attempts == maximum <= MAX_DIAGNOSTIC_ATTEMPTS
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
    ):
        if issue_id is not None:
            raise _invalid_ledger(issue_id)
        raise RoutingConfigError("attestation de diagnostic borné invalide.")
    return {
        "outcome": "durable_ambiguity",
        "attempts": attempts,
        "maximum_attempts": maximum,
        "evidence_digest": digest,
    }


def _technical_remediation(reason_code: str) -> dict:
    if reason_code not in _TECHNICAL_REASON_MESSAGES:
        raise RoutingConfigError("raison de blocage technique interne invalide.")
    return {
        "path": "bounded_local_technical_diagnostic",
        "reason_code": reason_code,
        "provider_effect_allowed": False,
        "campaign_restart_allowed": False,
        "human_authorization_required": False,
    }


def _valid_technical_remediation(value: object, reason_code: str) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _TECHNICAL_REMEDIATION_KEYS
        and value == _technical_remediation(reason_code)
    )


def _normalize_terminal_outcome(
    value: object,
    issue_id: str,
    *,
    halted: bool,
    halted_reason: str | None,
    halted_role: object,
    halt_generation: int,
) -> dict | None:
    if value is None:
        return None
    if (
        not halted
        or not isinstance(value, dict)
        or set(value) != _TERMINAL_OUTCOME_KEYS
    ):
        raise _invalid_ledger(issue_id)
    action = value.get("action")
    category = value.get("category")
    reason_code = value.get("reason_code")
    signal = value.get("signal")
    role = value.get("role")
    generation = value.get("halt_generation")
    diagnostic = value.get("diagnostic")
    source_receipt = value.get("source_receipt")
    if (
        role not in ROLE_DEFAULTS
        or role != halted_role
        or type(generation) is not int
        or generation != halt_generation
        or not isinstance(signal, str)
        or signal not in _TERMINAL_SIGNALS
    ):
        raise _invalid_ledger(issue_id)

    if action == "technical_blocked":
        if (
            category not in _TECHNICAL_AUTHORITY_CATEGORIES
            or reason_code not in _TECHNICAL_REASON_MESSAGES
            or diagnostic is not None
            or halted_reason != _TECHNICAL_REASON_MESSAGES[reason_code]
            or (
                reason_code in {
                    "apex_deterministic_failure",
                    "legacy_deterministic_failure_reclassified",
                }
                and (category != "deterministic_failure" or signal not in FAILURE_KINDS)
            )
            or (
                reason_code == "remediation_window_exhausted"
                and (
                    category != "deterministic_failure"
                    or signal != "review_blocking_after_fix"
                )
            )
            or (
                reason_code == "escalation_ceiling_reached"
                and (
                    signal not in (*FAILURE_KINDS, *RISK_FLOORS, "explicit_user_request")
                    or (
                        signal in FAILURE_KINDS
                        and category != "deterministic_failure"
                    )
                    or (
                        signal not in FAILURE_KINDS
                        and category != "technical_policy"
                    )
                )
            )
            or (
                reason_code in {
                    "remediation_window_cancelled",
                    "remediation_authorization_invalid",
                    "signal_outside_remediation_window",
                    "remediation_window_inactive",
                    "routing_apex_reached",
                }
                and category != "technical_policy"
            )
            or (
                reason_code == "remediation_window_cancelled"
                and signal != "cancel_remediation"
            )
            or (
                reason_code == "routing_apex_reached"
                and signal != "explicit_user_request"
            )
            or (
                reason_code != "legacy_deterministic_failure_reclassified"
                and source_receipt is not None
            )
            or (
                reason_code == "legacy_deterministic_failure_reclassified"
                and (
                    not isinstance(source_receipt, str)
                    or _FAILURE_IDEMPOTENCY.fullmatch(source_receipt) is None
                )
            )
        ):
            raise _invalid_ledger(issue_id)
    elif action == "human_required":
        expected_reason = {
            "strategy_decision": "strategy_decision_requested",
            "product_decision": "product_decision_requested",
            "durable_ambiguity": "durable_ambiguity_attested",
        }
        if (
            category not in _HUMAN_AUTHORITY_CATEGORIES
            or reason_code != expected_reason[category]
            or signal != category
            or source_receipt is not None
            or halted_reason != _HUMAN_REASON_MESSAGES[reason_code]
            or (
                category == "durable_ambiguity"
                and _normalize_bounded_diagnostic(
                    diagnostic, issue_id=issue_id,
                ) != diagnostic
            )
            or (category != "durable_ambiguity" and diagnostic is not None)
        ):
            raise _invalid_ledger(issue_id)
    else:
        raise _invalid_ledger(issue_id)
    return {
        "action": action,
        "category": category,
        "reason_code": reason_code,
        "signal": signal,
        "role": role,
        "halt_generation": generation,
        "diagnostic": dict(diagnostic) if diagnostic is not None else None,
        "source_receipt": source_receipt,
    }


def _legacy_receipt_proves_deterministic_failure(
    receipt: object,
    state: dict,
    *,
    require_current_reason: bool,
) -> bool:
    """Accept only an exact, terminal, deterministic v1 receipt."""
    if not isinstance(receipt, dict):
        return False
    role = receipt.get("role")
    kind = receipt.get("kind")
    decision = receipt.get("decision")
    role_state = state.get("roles", {}).get(role)
    if (
        role not in ROLE_DEFAULTS
        or kind not in FAILURE_KINDS
        or not isinstance(decision, dict)
        or not isinstance(role_state, dict)
        or decision.get("action") != "human_required"
        or decision.get("human_required") is not True
        or decision.get("authority_category") is not None
        or decision.get("signal") != kind
        or decision.get("role") != role
        or decision.get("issue_id") != state.get("issue_id")
        or decision.get("issue_escalations") != state.get("total_escalations")
        or decision.get("role_escalations") != role_state.get("escalations")
        or decision.get("deterministic_failures")
        != role_state.get("deterministic_failures")
        or decision.get("failures_since_escalation")
        != role_state.get("failures_since_escalation")
        or decision.get("initial_tier") != decision.get("final_tier")
        or state.get("halted_role") != role
    ):
        return False
    apex_reason = f"{role} est déjà au niveau apex après le signal {kind}."
    ceiling_reason = (
        f"plafond de {MAX_ESCALATIONS_PER_ISSUE} escalades atteint ; "
        f"signal supplémentaire {kind} refusé."
    )
    allowed_reasons = {apex_reason, ceiling_reason}
    if kind == "review_blocking_after_fix":
        allowed_reasons.add(
            "crédits de remédiation épuisés ; intervention humaine requise."
        )
    reason = decision.get("reason")
    if reason not in allowed_reasons:
        return False
    if reason == apex_reason and decision.get("final_tier") != "apex":
        return False
    if (
        reason == ceiling_reason
        and decision.get("issue_escalations") != MAX_ESCALATIONS_PER_ISSUE
    ):
        return False
    return not require_current_reason or state.get("halted_reason") == reason


def _legacy_terminal_signal(state: dict) -> str | None:
    """Derive one deterministic signal only from a complete released-v1 halt."""
    if not state.get("halted") or state.get("terminal_outcome") is not None:
        return None
    role = state.get("halted_role")
    role_state = state.get("roles", {}).get(role)
    reason = state.get("halted_reason")
    if (
        role not in ROLE_DEFAULTS
        or not isinstance(role_state, dict)
        or role_state.get("deterministic_failures", 0) < 1
        or role_state.get("failures_since_escalation", 0) < 1
    ):
        return None
    for kind in FAILURE_KINDS:
        if (
            reason == f"{role} est déjà au niveau apex après le signal {kind}."
            and role_state.get("minimum_tier") == "apex"
        ):
            return kind
        if (
            reason == (
                f"plafond de {MAX_ESCALATIONS_PER_ISSUE} escalades atteint ; "
                f"signal supplémentaire {kind} refusé."
            )
            and state.get("total_escalations") == MAX_ESCALATIONS_PER_ISSUE
        ):
            return kind
    if (
        reason == "crédits de remédiation épuisés ; intervention humaine requise."
        and state.get("remediation_authorization", {}).get("state") == "exhausted"
    ):
        return "review_blocking_after_fix"
    return None


def _legacy_causal_material(
    state: dict,
    *,
    signal: str,
    previous_halted_reason: str,
    remediation_state: str | None = None,
) -> dict:
    """Freeze the complete legacy facts that justified one local reclassification."""
    if remediation_state is None:
        authorization = state.get("remediation_authorization")
        remediation_state = "none" if authorization is None else authorization["state"]
    return {
        "contract": "foundry-escalation-legacy-causal-ledger.v1",
        "issue_id": state["issue_id"],
        "total_escalations": state["total_escalations"],
        "halt_generation": state["halt_generation"],
        "halted_role": state["halted_role"],
        "previous_halted_reason": previous_halted_reason,
        "signal": signal,
        "roles": {role: dict(record) for role, record in state["roles"].items()},
        "resume_count": state["resume_count"],
        "last_resumed_at": state["last_resumed_at"],
        "last_resumed_halt_generation": state["last_resumed_halt_generation"],
        "remediation_state": remediation_state,
        "failure_receipt_keys": sorted(state["failure_receipts"]),
    }


def _normalize_legacy_causal_facts(value: object, issue_id: str) -> dict:
    """Retain a complete, independently verifiable legacy halt snapshot."""
    if not isinstance(value, dict) or set(value) != _LEGACY_CAUSAL_FACT_KEYS:
        raise _invalid_ledger(issue_id)
    total = value.get("total_escalations")
    generation = value.get("halt_generation")
    role = value.get("halted_role")
    signal = value.get("signal")
    previous_reason = value.get("previous_halted_reason")
    resume_count = value.get("resume_count")
    last_resumed_at = value.get("last_resumed_at")
    last_resumed_generation = value.get("last_resumed_halt_generation")
    remediation_state = value.get("remediation_state")
    receipt_keys = value.get("failure_receipt_keys")
    if (
        value.get("contract") != "foundry-escalation-legacy-causal-ledger.v1"
        or value.get("issue_id") != issue_id
        or type(total) is not int
        or not 0 <= total <= MAX_ESCALATIONS_PER_ISSUE
        or type(generation) is not int
        or generation <= 0
        or role not in ROLE_DEFAULTS
        or signal not in FAILURE_KINDS
        or previous_reason not in _CONTROLLED_HALT_REASONS
        or type(resume_count) is not int
        or not 0 <= resume_count <= generation - 1
        or remediation_state not in _LEGACY_CAUSAL_REMEDIATION_STATES
        or not isinstance(receipt_keys, list)
        or receipt_keys != sorted(set(receipt_keys))
        or len(receipt_keys) > MAX_FAILURE_RECEIPTS
        or any(
            not isinstance(key, str)
            or _FAILURE_IDEMPOTENCY.fullmatch(key) is None
            for key in receipt_keys
        )
    ):
        raise _invalid_ledger(issue_id)
    if resume_count == 0:
        if last_resumed_at is not None or last_resumed_generation is not None:
            raise _invalid_ledger(issue_id)
    elif (
        _utc_timestamp(last_resumed_at) is None
        or type(last_resumed_generation) is not int
        or not 1 <= last_resumed_generation < generation
    ):
        raise _invalid_ledger(issue_id)

    roles_raw = value.get("roles")
    if not isinstance(roles_raw, dict):
        raise _invalid_ledger(issue_id)
    roles = {}
    role_escalation_total = 0
    for candidate, record in roles_raw.items():
        if (
            candidate not in ROLE_DEFAULTS
            or not isinstance(record, dict)
            or set(record) != _ROLE_STATE_KEYS
        ):
            raise _invalid_ledger(issue_id)
        minimum = record.get("minimum_tier")
        escalations = record.get("escalations")
        deterministic = record.get("deterministic_failures")
        since_escalation = record.get("failures_since_escalation")
        if (
            (minimum is not None and minimum not in LEVELS)
            or type(escalations) is not int
            or not 0 <= escalations <= MAX_ESCALATIONS_PER_ISSUE
            or type(deterministic) is not int
            or deterministic < 0
            or type(since_escalation) is not int
            or not 0 <= since_escalation <= deterministic
            or (escalations > 0 and minimum is None)
            or (escalations > 0 and LEVELS.index(minimum) < escalations)
        ):
            raise _invalid_ledger(issue_id)
        roles[candidate] = {
            "minimum_tier": minimum,
            "escalations": escalations,
            "deterministic_failures": deterministic,
            "failures_since_escalation": since_escalation,
        }
        role_escalation_total += escalations
    if role_escalation_total != total or role not in roles:
        raise _invalid_ledger(issue_id)
    return {
        "contract": value["contract"],
        "issue_id": issue_id,
        "total_escalations": total,
        "halt_generation": generation,
        "halted_role": role,
        "previous_halted_reason": previous_reason,
        "signal": signal,
        "roles": roles,
        "resume_count": resume_count,
        "last_resumed_at": last_resumed_at,
        "last_resumed_halt_generation": last_resumed_generation,
        "remediation_state": remediation_state,
        "failure_receipt_keys": list(receipt_keys),
    }


def _legacy_reclassification_evidence(
    state: dict,
    *,
    signal: str,
) -> tuple[str, str] | None:
    """Prefer an exact legacy receipt; otherwise require a receipt-free v1 ledger."""
    candidates = [
        key for key, receipt in state["failure_receipts"].items()
        if _legacy_receipt_proves_deterministic_failure(
            receipt, state, require_current_reason=True,
        )
    ]
    if candidates:
        return (candidates[0], _LEGACY_RECEIPT_EVIDENCE_KIND) if len(candidates) == 1 else None
    if state["failure_receipts"]:
        return None
    return (
        f"{_LEGACY_LEDGER_EVIDENCE_PREFIX}{state['halt_generation']}",
        _LEGACY_LEDGER_EVIDENCE_KIND,
    )


def _utc_timestamp(value: object) -> datetime | None:
    """Parse one controlled UTC timestamp without surfacing hostile content."""
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_after(*values: object) -> str:
    """Return a UTC audit timestamp strictly after every trusted timestamp."""
    current = _utc_timestamp(_now_utc())
    if current is None:  # pragma: no cover - _now_utc is an internal trusted source
        raise RoutingConfigError("horodatage d'audit interne invalide.")
    previous = [parsed for parsed in (_utc_timestamp(value) for value in values) if parsed]
    if previous and current <= max(previous):
        current = max(previous) + timedelta(microseconds=1)
    return current.isoformat().replace("+00:00", "Z")


def _invalid_ledger(issue_id: str) -> RoutingConfigError:
    """Build the sole redacted error for an untrusted version-1 ledger."""
    return RoutingConfigError(f"état d'escalade invalide pour {issue_id}.")


def _fdopen_text(descriptor: int, mode: str):
    """Transfer one descriptor to a text handle without a failure-path leak."""
    try:
        return os.fdopen(descriptor, mode, encoding="utf-8")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _strict_json_loads(raw: str) -> object:
    """Reject duplicate keys and non-standard constants before validation."""
    def object_from_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate ledger key")
            value[key] = item
        return value

    def reject_constant(_value):
        raise ValueError("non-standard JSON constant")

    return json.loads(
        raw,
        object_pairs_hook=object_from_pairs,
        parse_constant=reject_constant,
    )


def _normalize_consumption_event(
    value: object,
    issue_id: str,
) -> tuple[dict, datetime]:
    if not isinstance(value, dict) or set(value) != _CONSUMPTION_EVENT_KEYS:
        raise _invalid_ledger(issue_id)
    code = value.get("code")
    at_text = value.get("at")
    role = value.get("role")
    generation = value.get("halt_generation")
    at = _utc_timestamp(at_text)
    if (
        code != _REMEDIATION_CONSUMPTION_CODE
        or at is None
        or not isinstance(role, str)
        or role not in ROLE_DEFAULTS
        or type(generation) is not int
        or generation <= 0
    ):
        raise _invalid_ledger(issue_id)
    return {
        "code": code,
        "at": at_text,
        "role": role,
        "halt_generation": generation,
    }, at


def _normalize_rearm_event(
    value: object,
    issue_id: str,
) -> tuple[dict, datetime, datetime]:
    """Validate one bounded public rearm event without echoing hostile input."""
    if (
        not isinstance(value, dict)
        or set(value) not in (
            _REARM_EVENT_KEYS, _TECHNICAL_GENERATION_REARM_EVENT_KEYS,
        )
    ):
        raise _invalid_ledger(issue_id)
    at_text = value.get("at")
    exhausted_at_text = value.get("exhausted_window_armed_at")
    at = _utc_timestamp(at_text)
    exhausted_at = _utc_timestamp(exhausted_at_text)
    reason = value.get("reason")
    role = value.get("role")
    generation = value.get("halt_generation")
    exhausted_generation = value.get("exhausted_halt_generation", generation)
    granted = value.get("granted_credits")
    exhausted_maximum = value.get("exhausted_window_maximum_credits")
    if (
        value.get("code") != _REMEDIATION_REARM_CODE
        or at is None
        or exhausted_at is None
        or exhausted_at >= at
        or reason not in RESUME_REASON_CODES
        or not isinstance(role, str)
        or role not in ROLE_DEFAULTS
        or type(generation) is not int
        or generation <= 0
        or type(exhausted_generation) is not int
        or exhausted_generation <= 0
        or exhausted_generation > generation
        or (
            "exhausted_halt_generation" in value
            and exhausted_generation == generation
        )
        or type(granted) is not int
        or not 1 <= granted <= MAX_REMEDIATION_CREDITS
        or type(exhausted_maximum) is not int
        or not 1 <= exhausted_maximum <= MAX_REMEDIATION_CREDITS
    ):
        raise _invalid_ledger(issue_id)
    event = {
        "code": _REMEDIATION_REARM_CODE,
        "at": at_text,
        "reason": reason,
        "role": role,
        "halt_generation": generation,
        "granted_credits": granted,
        "exhausted_window_armed_at": exhausted_at_text,
        "exhausted_window_maximum_credits": exhausted_maximum,
    }
    if "exhausted_halt_generation" in value:
        event["exhausted_halt_generation"] = exhausted_generation
    return event, at, exhausted_at


def _higher(left: str, right: str) -> str:
    return left if LEVELS.index(left) >= LEVELS.index(right) else right


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _campaign_review_decision_material(
    issue_id: str, effect_id: str, authorization_digest: str,
    selected_tier: str,
) -> dict:
    return {
        "contract": "foundry-campaign-first-review-remediation-decision.v1",
        "authorization_digest": authorization_digest,
        "issue_id": issue_id,
        "effect_id": effect_id,
        "selected_tier": selected_tier,
        "action": "remediation_continued",
    }


class EscalationStore:
    """A small locked JSON state machine, namespaced by hashed repository identity."""

    def __init__(
        self,
        repository: str,
        *,
        state_dir: str | os.PathLike | None = None,
    ):
        if not isinstance(repository, str) or not repository.strip():
            raise RoutingConfigError("dépôt d'escalade : identité non vide attendue.")
        namespace = hashlib.sha256(repository.strip().encode("utf-8")).hexdigest()
        root = Path(state_dir) if state_dir is not None else Path(registry.data_dir())
        self.directory = root / "model-routing" / "escalations" / namespace

    @classmethod
    def for_root(
        cls,
        root: str | os.PathLike | None = None,
        *,
        state_dir: str | os.PathLike | None = None,
    ) -> "EscalationStore":
        return cls(repository_identity(root), state_dir=state_dir)

    def _path(self, issue_id: str) -> Path:
        return self.directory / f"{_validate_issue_id(issue_id)}.json"

    def _lock_path(self, issue_id: str) -> Path:
        return self.directory / f"{_validate_issue_id(issue_id)}.lock"

    @contextmanager
    def _issue_lock(self, issue_id: str, operation: int):
        """Lock the stable per-issue inode before touching its JSON ledger."""
        path = self._lock_path(issue_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                path,
                os.O_RDWR | os.O_CREAT | _SAFE_OPEN_FLAGS,
                0o600,
            )
        except OSError as exc:
            raise RoutingConfigError(
                f"verrou d'escalade illisible pour {issue_id}."
            ) from exc
        try:
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RoutingConfigError(
                        f"verrou d'escalade invalide pour {issue_id}."
                    )
                if stat.S_IMODE(metadata.st_mode) != 0o600:
                    os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, operation)
            except OSError as exc:
                raise RoutingConfigError(
                    f"verrou d'escalade illisible pour {issue_id}."
                ) from exc
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _new_state(issue_id: str) -> dict:
        return {
            "version": 1,
            "issue_id": issue_id,
            "total_escalations": 0,
            "halted": False,
            "halted_reason": None,
            "halt_generation": 0,
            "roles": {},
            "consumption_audit": [],
            "remediation_rearm_audit": [],
            "technical_remediation_audit": [],
            "failure_receipts": {},
            "campaign_review_retry_receipts": {},
        }

    @staticmethod
    def _role_state(state: dict, role: str) -> dict:
        return state["roles"].setdefault(role, {
            "minimum_tier": None,
            "escalations": 0,
            "deterministic_failures": 0,
            "failures_since_escalation": 0,
        })

    @staticmethod
    def _role_state_view(state: dict, role: str) -> dict:
        """Read role counters without adding a role for a rejected signal."""
        return state["roles"].get(role, {
            "minimum_tier": None,
            "escalations": 0,
            "deterministic_failures": 0,
            "failures_since_escalation": 0,
        })

    @staticmethod
    def _remediation(state: dict) -> dict:
        """Build the bounded public authorization view from a validated ledger."""
        authorization = state.get("remediation_authorization")
        if authorization is None:
            return {"state": "none", "role": None, "halt_generation": None,
                    "maximum_credits": 0, "remaining_credits": 0}
        return {
            "state": authorization["state"],
            "role": authorization["role"],
            "halt_generation": authorization["halt_generation"],
            "maximum_credits": authorization["maximum_credits"],
            "remaining_credits": authorization["remaining_credits"],
        }

    @staticmethod
    def _terminal_outcome(state: dict) -> dict:
        """Return a redacted terminal classification without inventing authority."""
        if not state["halted"]:
            return {
                "action": "none",
                "category": None,
                "reason_code": None,
                "signal": None,
                "role": None,
                "halt_generation": None,
                "human_required": False,
                "technical_blocked": False,
            }
        outcome = state.get("terminal_outcome")
        if outcome is None:
            return {
                "action": "authority_ambiguous",
                "category": "legacy_unclassified",
                "reason_code": "legacy_terminal_unclassified",
                "signal": None,
                "role": state.get("halted_role"),
                "halt_generation": state["halt_generation"],
                "human_required": False,
                "technical_blocked": False,
            }
        public = {
            key: outcome[key]
            for key in (
                "action", "category", "reason_code", "signal", "role",
                "halt_generation",
            )
        }
        public["human_required"] = outcome["action"] == "human_required"
        public["technical_blocked"] = outcome["action"] == "technical_blocked"
        if outcome["diagnostic"] is not None:
            public["diagnostic"] = dict(outcome["diagnostic"])
        return public

    @classmethod
    def _technical_remediation_view(cls, state: dict) -> dict | None:
        outcome = cls._terminal_outcome(state)
        if not outcome["technical_blocked"]:
            return None
        return _technical_remediation(outcome["reason_code"])

    @staticmethod
    def _technical_remediation_event(state: dict) -> dict | None:
        """Return the active local diagnostic event for the current halt generation."""
        audit = state["technical_remediation_audit"]
        if (
            not state["halted"]
            and audit
            and audit[-1]["halt_generation"] == state["halt_generation"]
        ):
            return audit[-1]
        return None

    @classmethod
    def _technical_remediation_open(cls, state: dict) -> bool:
        """Return whether its single local route has not yet been claimed."""
        event = cls._technical_remediation_event(state)
        return event is not None and event["route_id"] is None

    @staticmethod
    def _next_audit_timestamp(state: dict) -> str:
        """Order grant and consumption audits even if the wall clock moves back."""
        return _timestamp_after(
            state.get("last_resumed_at"),
            *(event["at"] for event in state.get("consumption_audit", [])),
            *(event["at"] for event in state.get("remediation_rearm_audit", [])),
            *(event["at"] for event in state.get("technical_remediation_audit", [])),
            *(
                event["route_consumed_at"]
                for event in state.get("technical_remediation_audit", [])
                if event["route_consumed_at"] is not None
            ),
            *(
                event["review_claimed_at"]
                for event in state.get("technical_remediation_audit", [])
                if event["review_claimed_at"] is not None
            ),
            *(
                rearm["rearmed_at"]
                for event in state.get("technical_remediation_audit", [])
                for rearm in event.get("review_rearm_audit", [])
            ),
            state.get("remediation_authorization", {}).get("armed_at"),
            state.get("terminal_reclassification", {}).get("at"),
        )

    @staticmethod
    def _normalize_state(raw: object, issue_id: str) -> dict:
        """Validate the complete v1 envelope and return only trusted fields."""
        if not isinstance(raw, dict):
            raise _invalid_ledger(issue_id)
        keys = set(raw)
        if (
            not _LEDGER_REQUIRED_KEYS <= keys
            or not keys <= _LEDGER_REQUIRED_KEYS | _LEDGER_OPTIONAL_KEYS
            or type(raw.get("version")) is not int
            or raw.get("version") != 1
            or raw.get("issue_id") != issue_id
        ):
            raise _invalid_ledger(issue_id)

        total = raw.get("total_escalations")
        halted = raw.get("halted")
        halted_reason = raw.get("halted_reason")
        if (
            type(total) is not int
            or not 0 <= total <= MAX_ESCALATIONS_PER_ISSUE
            or type(halted) is not bool
            or (halted and (
                not isinstance(halted_reason, str)
                or halted_reason not in _CONTROLLED_HALT_REASONS
            ))
            or (not halted and halted_reason is not None)
        ):
            raise _invalid_ledger(issue_id)

        generation = raw.get("halt_generation", 1 if halted else 0)
        if (
            type(generation) is not int
            or generation < 0
            or (halted and generation == 0)
        ):
            raise _invalid_ledger(issue_id)

        halted_role = raw.get("halted_role")
        if "halted_role" in raw and (
            not isinstance(halted_role, str) or halted_role not in ROLE_DEFAULTS
        ):
            raise _invalid_ledger(issue_id)

        resume_count = raw.get("resume_count", 0)
        last_resumed_at = raw.get("last_resumed_at")
        last_resume_reason = raw.get("last_resume_reason")
        last_resumed_generation = raw.get("last_resumed_halt_generation")
        maximum_resume_count = generation - 1 if halted else generation
        if (
            type(resume_count) is not int
            or resume_count < 0
            or resume_count > maximum_resume_count
        ):
            raise _invalid_ledger(issue_id)
        if resume_count == 0:
            if any(value is not None for value in (
                last_resumed_at, last_resume_reason, last_resumed_generation,
            )):
                raise _invalid_ledger(issue_id)
            last_resumed_time = None
        else:
            last_resumed_time = _utc_timestamp(last_resumed_at)
            if (
                last_resumed_time is None
                or last_resume_reason not in RESUME_REASON_CODES
                or type(last_resumed_generation) is not int
                or not 1 <= last_resumed_generation <= generation
            ):
                raise _invalid_ledger(issue_id)

        roles_raw = raw.get("roles")
        if not isinstance(roles_raw, dict):
            raise _invalid_ledger(issue_id)
        roles = {}
        role_escalation_total = 0
        for role, record in roles_raw.items():
            if (
                not isinstance(role, str)
                or role not in ROLE_DEFAULTS
                or not isinstance(record, dict)
                or set(record) != _ROLE_STATE_KEYS
            ):
                raise _invalid_ledger(issue_id)
            minimum = record.get("minimum_tier")
            escalations = record.get("escalations")
            deterministic = record.get("deterministic_failures")
            since_escalation = record.get("failures_since_escalation")
            if (
                (minimum is not None and (
                    not isinstance(minimum, str) or minimum not in LEVELS
                ))
                or type(escalations) is not int
                or not 0 <= escalations <= MAX_ESCALATIONS_PER_ISSUE
                or type(deterministic) is not int
                or deterministic < 0
                or type(since_escalation) is not int
                or not 0 <= since_escalation <= deterministic
                or (escalations > 0 and minimum is None)
                or (
                    escalations > 0
                    and LEVELS.index(minimum) < escalations
                )
            ):
                raise _invalid_ledger(issue_id)
            roles[role] = {
                "minimum_tier": minimum,
                "escalations": escalations,
                "deterministic_failures": deterministic,
                "failures_since_escalation": since_escalation,
            }
            role_escalation_total += escalations
        if role_escalation_total != total:
            raise _invalid_ledger(issue_id)

        failure_receipts_raw = raw.get("failure_receipts", {})
        if (not isinstance(failure_receipts_raw, dict)
                or len(failure_receipts_raw) > MAX_FAILURE_RECEIPTS):
            raise _invalid_ledger(issue_id)
        failure_receipts = {}
        for idempotency_key, receipt in failure_receipts_raw.items():
            if (not isinstance(idempotency_key, str)
                    or _FAILURE_IDEMPOTENCY.fullmatch(idempotency_key) is None
                    or not isinstance(receipt, dict)
                    or set(receipt) != _FAILURE_RECEIPT_KEYS):
                raise _invalid_ledger(issue_id)
            role = receipt.get("role")
            kind = receipt.get("kind")
            current_tier = receipt.get("current_tier")
            decision_raw = receipt.get("decision")
            if (role not in ROLE_DEFAULTS or kind not in FAILURE_KINDS
                    or current_tier not in LEVELS or not isinstance(decision_raw, dict)
                    or not _DECISION_REQUIRED_KEYS <= set(decision_raw)
                    or not set(decision_raw) <= _DECISION_REQUIRED_KEYS | _DECISION_OPTIONAL_KEYS):
                raise _invalid_ledger(issue_id)
            try:
                decision = EscalationDecision(**decision_raw)
            except (TypeError, ValueError):
                raise _invalid_ledger(issue_id) from None
            role_state = roles.get(role)
            authorization = decision.remediation_authorization
            authority_category = decision.authority_category
            technical_remediation = decision.technical_remediation
            terminal_decision_valid = False
            if decision.action == "technical_blocked":
                reason_code = (
                    technical_remediation.get("reason_code")
                    if isinstance(technical_remediation, dict) else ""
                )
                terminal_decision_valid = (
                    decision.human_required is False
                    and authority_category in _TECHNICAL_AUTHORITY_CATEGORIES
                    and reason_code in _TECHNICAL_REASON_MESSAGES
                    and _valid_technical_remediation(
                        technical_remediation, reason_code,
                    )
                    and decision.reason == _TECHNICAL_REASON_MESSAGES[reason_code]
                )
            elif decision.action == "human_required":
                # Category-less receipts were emitted by released v1 code and remain
                # readable. New human verdicts are explicitly typed.
                terminal_decision_valid = (
                    decision.human_required is True
                    and (
                        authority_category is None
                        or authority_category in _HUMAN_AUTHORITY_CATEGORIES
                    )
                    and technical_remediation is None
                )
            elif decision.action == "authority_ambiguous":
                terminal_decision_valid = (
                    decision.human_required is False
                    and authority_category == "legacy_unclassified"
                    and technical_remediation is None
                )
            else:
                terminal_decision_valid = (
                    decision.human_required is False
                    and authority_category is None
                    and technical_remediation is None
                )
            if (
                decision.issue_id != issue_id
                or decision.role != role
                or decision.signal != kind
                or decision.initial_tier not in LEVELS
                or decision.final_tier not in LEVELS
                or LEVELS.index(decision.initial_tier) < LEVELS.index(current_tier)
                or LEVELS.index(decision.final_tier) < LEVELS.index(decision.initial_tier)
                or decision.action not in {
                    "failure_recorded", "escalated", "satisfied",
                    "human_required", "remediation_continued",
                    "technical_blocked", "authority_ambiguous",
                }
                or type(decision.human_required) is not bool
                or not terminal_decision_valid
                or not isinstance(decision.reason, str)
                or not 1 <= len(decision.reason) <= 500
                or not decision.reason.isprintable()
                or decision.max_escalations_per_issue != MAX_ESCALATIONS_PER_ISSUE
                or decision.max_parallel_agents != MAX_PARALLEL_AGENTS
                or role_state is None
                or type(decision.issue_escalations) is not int
                or not 0 <= decision.issue_escalations <= total
                or type(decision.role_escalations) is not int
                or not 0 <= decision.role_escalations <= role_state["escalations"]
                or type(decision.deterministic_failures) is not int
                or not 0 <= decision.deterministic_failures <= role_state["deterministic_failures"]
                or type(decision.failures_since_escalation) is not int
                or not 0 <= decision.failures_since_escalation <= decision.deterministic_failures
                or (
                    authorization is not None
                    and (
                        not isinstance(authorization, dict)
                        or set(authorization) != {
                            "state", "role", "halt_generation",
                            "maximum_credits", "remaining_credits",
                        }
                        or authorization.get("state") not in {"active", "exhausted"}
                        or authorization.get("role") != role
                        or type(authorization.get("halt_generation")) is not int
                        or authorization["halt_generation"] <= 0
                        or type(authorization.get("maximum_credits")) is not int
                        or not 1 <= authorization["maximum_credits"] <= MAX_REMEDIATION_CREDITS
                        or type(authorization.get("remaining_credits")) is not int
                        or not 0 <= authorization["remaining_credits"] <= authorization["maximum_credits"]
                    )
                )
                or (
                    decision.action == "remediation_continued"
                    and authorization is None
                )
            ):
                raise _invalid_ledger(issue_id)
            failure_receipts[idempotency_key] = {
                "role": role,
                "kind": kind,
                "current_tier": current_tier,
                "decision": decision.to_dict(),
            }

        campaign_receipts_raw = raw.get("campaign_review_retry_receipts", {})
        if (not isinstance(campaign_receipts_raw, dict)
                or len(campaign_receipts_raw) > MAX_CAMPAIGN_REVIEW_RECEIPTS):
            raise _invalid_ledger(issue_id)
        campaign_receipts = {}
        seen_review_effects = set()
        for authorization_digest, receipt in campaign_receipts_raw.items():
            if (not isinstance(authorization_digest, str)
                    or _DIGEST.fullmatch(authorization_digest) is None
                    or not isinstance(receipt, dict)
                    or set(receipt) != _CAMPAIGN_REVIEW_RECEIPT_KEYS
                    or receipt.get("authorization_digest") != authorization_digest
                    or receipt.get("state") != "consumed"):
                raise _invalid_ledger(issue_id)
            effect_id = receipt.get("effect_id")
            decision = receipt.get("decision")
            failure = failure_receipts.get(effect_id)
            if (not isinstance(effect_id, str)
                    or _FAILURE_IDEMPOTENCY.fullmatch(effect_id) is None
                    or effect_id in seen_review_effects
                    or not isinstance(decision, dict)
                    or set(decision) != _CAMPAIGN_REVIEW_DECISION_KEYS
                    or decision.get("allowed") is not True
                    or decision.get("action") != "remediation_continued"
                    or decision.get("authorization_digest") != authorization_digest
                    or decision.get("selected_tier") not in LEVELS
                    or not isinstance(decision.get("audit_digest"), str)
                    or _DIGEST.fullmatch(decision["audit_digest"]) is None
                    or not isinstance(failure, dict)
                    or failure.get("role") != "implementer"
                    or failure.get("kind") != "review_blocking"
                    or failure.get("current_tier") not in LEVELS
                    or failure.get("decision", {}).get("action") != "failure_recorded"
                    or failure.get("decision", {}).get("human_required") is not False
                    or LEVELS.index(decision["selected_tier"])
                    < LEVELS.index(failure["current_tier"])
                    or decision["audit_digest"] != _canonical_digest(
                        _campaign_review_decision_material(
                            issue_id, effect_id, authorization_digest,
                            decision["selected_tier"],
                        )
                    )):
                raise _invalid_ledger(issue_id)
            seen_review_effects.add(effect_id)
            campaign_receipts[authorization_digest] = dict(receipt)

        durable_present = "consumption_audit" in raw
        durable_raw = raw.get("consumption_audit", [])
        if not isinstance(durable_raw, list):
            raise _invalid_ledger(issue_id)
        durable_audit = []
        durable_times = []
        generation_roles = {}
        generation_counts = {}
        role_consumptions = {role: 0 for role in ROLE_DEFAULTS}
        previous_time = None
        previous_generation = 0
        for value in durable_raw:
            event, event_time = _normalize_consumption_event(value, issue_id)
            event_generation = event["halt_generation"]
            event_role = event["role"]
            # resume_count is a cardinality, not a generation ceiling: bounded
            # technical resumes advance halt_generation without incrementing it.
            if (
                last_resumed_generation is None
                or event_generation < previous_generation
                or (previous_time is not None and event_time < previous_time)
                or generation_roles.setdefault(event_generation, event_role) != event_role
            ):
                raise _invalid_ledger(issue_id)
            generation_counts[event_generation] = (
                generation_counts.get(event_generation, 0) + 1
            )
            role_consumptions[event_role] += 1
            durable_audit.append(event)
            durable_times.append(event_time)
            previous_time = event_time
            previous_generation = event_generation
        for role, count in role_consumptions.items():
            if count and (
                role not in roles
                or roles[role]["deterministic_failures"] < count
            ):
                raise _invalid_ledger(issue_id)
        if last_resumed_time is not None:
            for event, event_time in zip(durable_audit, durable_times, strict=True):
                event_generation = event["halt_generation"]
                if (
                    (event_generation < last_resumed_generation
                     and event_time > last_resumed_time)
                    or (event_generation == last_resumed_generation
                        and event_time < last_resumed_time)
                ):
                    raise _invalid_ledger(issue_id)

        rearm_raw = raw.get("remediation_rearm_audit", [])
        if not isinstance(rearm_raw, list):
            raise _invalid_ledger(issue_id)
        rearm_audit = []
        rearm_by_generation = {}
        rearm_by_exhausted_generation = {}
        bridge_rearm_times = {}
        rearm_times = []
        previous_rearm_time = None
        previous_rearm_generation = 0
        for value in rearm_raw:
            event, event_time, exhausted_at = _normalize_rearm_event(value, issue_id)
            event_generation = event["halt_generation"]
            exhausted_generation = event.get(
                "exhausted_halt_generation", event_generation,
            )
            event_role = event["role"]
            if (
                last_resumed_generation is None
                or event_generation < previous_rearm_generation
                or (previous_rearm_time is not None and event_time <= previous_rearm_time)
                or generation_roles.get(exhausted_generation) != event_role
                or any(
                    durable_generation == event_generation and durable_time == event_time
                    for durable_generation, durable_time in zip(
                        (item["halt_generation"] for item in durable_audit),
                        durable_times,
                        strict=True,
                    )
                )
            ):
                raise _invalid_ledger(issue_id)
            if last_resumed_time is not None and (
                (event_generation < last_resumed_generation
                 and event_time > last_resumed_time)
                or (event_generation == last_resumed_generation
                    and event_time < last_resumed_time)
            ):
                raise _invalid_ledger(issue_id)

            generation_rearms = rearm_by_exhausted_generation.setdefault(
                exhausted_generation, [],
            )
            if generation_rearms:
                previous_event, previous_event_time = generation_rearms[-1]
                if (
                    event["exhausted_window_armed_at"] != previous_event["at"]
                    or event["exhausted_window_maximum_credits"]
                    != previous_event["granted_credits"]
                ):
                    raise _invalid_ledger(issue_id)
                window_start = previous_event_time
            else:
                window_start = exhausted_at
            window_events = [
                durable_time
                for durable_event, durable_time in zip(
                    durable_audit, durable_times, strict=True,
                )
                if (
                    durable_event["halt_generation"] == exhausted_generation
                    and window_start <= durable_time < event_time
                )
            ]
            if (
                len(window_events) != event["exhausted_window_maximum_credits"]
                or any(
                    durable_event["halt_generation"] == exhausted_generation
                    and durable_time < window_start
                    for durable_event, durable_time in zip(
                        durable_audit, durable_times, strict=True,
                    )
                ) and not generation_rearms
            ):
                raise _invalid_ledger(issue_id)
            generation_rearms.append((event, event_time))
            rearm_by_generation.setdefault(event_generation, []).append(
                (event, event_time),
            )
            if exhausted_generation != event_generation:
                bridge_rearm_times.setdefault(
                    (event_generation, event_role), event_time,
                )
            rearm_audit.append(event)
            rearm_times.append(event_time)
            previous_rearm_time = event_time
            previous_rearm_generation = event_generation

        for event_generation, count in generation_counts.items():
            generation_rearms = rearm_by_generation.get(event_generation, [])
            if not generation_rearms:
                if count > MAX_REMEDIATION_CREDITS:
                    raise _invalid_ledger(issue_id)
                continue
            latest_event, latest_event_time = generation_rearms[-1]
            trailing = sum(
                durable_event["halt_generation"] == event_generation
                and durable_time > latest_event_time
                for durable_event, durable_time in zip(
                    durable_audit, durable_times, strict=True,
                )
            )
            if trailing > latest_event["granted_credits"]:
                raise _invalid_ledger(issue_id)

        technical_raw = raw.get("technical_remediation_audit", [])
        if not isinstance(technical_raw, list):
            raise _invalid_ledger(issue_id)
        technical_audit = []
        technical_times = []
        technical_counts_by_generation = {}
        prior_generation = 0
        prior_time = None
        for event in technical_raw:
            if (
                not isinstance(event, dict)
                or set(event) not in (
                    _TECHNICAL_REMEDIATION_AUDIT_KEYS,
                    _PRE_REARM_TECHNICAL_REMEDIATION_AUDIT_KEYS,
                    _LEGACY_TECHNICAL_REMEDIATION_AUDIT_KEYS,
                )
                or event.get("code") != _TECHNICAL_REMEDIATION_RESUME_CODE
                or type(event.get("halt_generation")) is not int
                or not 1 <= event["halt_generation"] <= generation
                or not isinstance(event.get("evidence_digest"), str)
                or _DIGEST.fullmatch(event["evidence_digest"]) is None
                or event.get("role") not in ROLE_DEFAULTS
                or (
                    event.get("route_id") is None
                    and event.get("route_consumed_at") is not None
                )
                or (
                    event.get("route_id") is not None
                    and (
                        not isinstance(event.get("route_id"), str)
                        or _FAILURE_IDEMPOTENCY.fullmatch(event["route_id"]) is None
                        or _utc_timestamp(event.get("route_consumed_at")) is None
                    )
                )
                or (
                    event.get("review_diff_hash") is None
                    and event.get("review_claimed_at") is not None
                )
                or (
                    event.get("review_diff_hash") is not None
                    and (
                        not isinstance(event.get("review_diff_hash"), str)
                        or _DIGEST.fullmatch(event["review_diff_hash"]) is None
                        or _utc_timestamp(event.get("review_claimed_at")) is None
                    )
                )
            ):
                raise _invalid_ledger(issue_id)
            event_time = _utc_timestamp(event.get("at"))
            route_consumed_time = _utc_timestamp(event.get("route_consumed_at"))
            review_claimed_time = _utc_timestamp(event.get("review_claimed_at"))
            review_rearm_audit = event.get("review_rearm_audit", [])
            if not isinstance(review_rearm_audit, list) or len(review_rearm_audit) > 1:
                raise _invalid_ledger(issue_id)
            if review_rearm_audit:
                rearm = review_rearm_audit[0]
                legacy_rearm = (
                    isinstance(rearm, dict)
                    and set(rearm) == _LEGACY_TECHNICAL_REVIEW_REARM_AUDIT_KEYS
                )
                current_rearm = (
                    isinstance(rearm, dict)
                    and set(rearm) == _TECHNICAL_REVIEW_REARM_AUDIT_KEYS
                )
                terminal_completed_at = (
                    _utc_timestamp(rearm.get("terminal_completed_at"))
                    if isinstance(rearm, dict) else None
                )
                if (
                    not isinstance(rearm, dict)
                    or not (legacy_rearm or current_rearm)
                    or rearm.get("code") not in {
                        "technical_review_rearmed",
                        "technical_review_pollution_reconciled",
                    }
                    or (
                        legacy_rearm
                        and rearm.get("code") != "technical_review_rearmed"
                    )
                    or not isinstance(rearm.get("previous_diff_hash"), str)
                    or _DIGEST.fullmatch(rearm["previous_diff_hash"]) is None
                    or not isinstance(rearm.get("terminal_proof_id"), str)
                    or _DIGEST.fullmatch(rearm["terminal_proof_id"]) is None
                    or rearm.get("new_diff_hash") != event.get("review_diff_hash")
                    or rearm.get("previous_diff_hash") == event.get("review_diff_hash")
                    or _utc_timestamp(rearm.get("previous_claimed_at")) is None
                    or _utc_timestamp(rearm.get("rearmed_at")) is None
                    or rearm["rearmed_at"] != event.get("review_claimed_at")
                    or rearm["previous_claimed_at"] >= rearm["rearmed_at"]
                    or (
                        current_rearm
                        and (
                            terminal_completed_at is None
                            or rearm.get("terminal_quality") not in {
                                "mergeable", "blocked",
                            }
                            or rearm.get("repair_kind") not in {
                                "mergeable_terminal_rearm",
                                "terminal_before_binding_reconciliation",
                            }
                            or (
                                rearm.get("code")
                                == "technical_review_pollution_reconciled"
                            ) != (
                                rearm.get("repair_kind")
                                == "terminal_before_binding_reconciliation"
                            )
                            or (
                                rearm.get("repair_kind")
                                == "terminal_before_binding_reconciliation"
                                and terminal_completed_at
                                >= _utc_timestamp(rearm["previous_claimed_at"])
                            )
                        )
                    )
                ):
                    raise _invalid_ledger(issue_id)
            if (
                event_time is None
                or (
                    route_consumed_time is not None
                    and route_consumed_time <= event_time
                )
                or (
                    review_claimed_time is not None
                    and (
                        route_consumed_time is None
                        or review_claimed_time <= route_consumed_time
                    )
                )
                or event["halt_generation"] <= prior_generation
                or (prior_time is not None and event_time <= prior_time)
            ):
                raise _invalid_ledger(issue_id)
            event_generation = event["halt_generation"]
            technical_counts_by_generation[event_generation] = (
                technical_counts_by_generation.get(event_generation, 0) + 1
            )
            if (
                technical_counts_by_generation[event_generation]
                > MAX_TECHNICAL_REMEDIATIONS_PER_GENERATION
            ):
                raise _invalid_ledger(issue_id)
            normalized_event = dict(event)
            normalized_event.setdefault("review_diff_hash", None)
            normalized_event.setdefault("review_claimed_at", None)
            normalized_event.setdefault("review_rearm_audit", [])
            technical_audit.append(normalized_event)
            technical_times.append(event_time)
            prior_generation = event["halt_generation"]
            prior_time = event_time
        expected_clear_count = generation - (1 if halted else 0)
        if (
            resume_count + len(technical_audit) != expected_clear_count
            or (
                technical_audit
                and technical_audit[-1]["halt_generation"] > expected_clear_count
            )
        ):
            raise _invalid_ledger(issue_id)
        if (
            (
                resume_count > 0
                and (
                    last_resumed_generation > expected_clear_count
                    or last_resumed_generation in technical_counts_by_generation
                    or sum(
                        event["halt_generation"] > last_resumed_generation
                        for event in technical_audit
                    ) != expected_clear_count - last_resumed_generation
                )
            )
            or any(
                event["halt_generation"] in technical_counts_by_generation
                and (
                    (event["halt_generation"], event["role"])
                    not in bridge_rearm_times
                    or event_time <= bridge_rearm_times[
                        (event["halt_generation"], event["role"])
                    ]
                )
                for event, event_time in zip(
                    durable_audit, durable_times, strict=True,
                )
            )
        ):
            raise _invalid_ledger(issue_id)
        prior_bridges = set()
        for event, event_time in zip(rearm_audit, rearm_times, strict=True):
            exhausted_generation = event.get(
                "exhausted_halt_generation", event["halt_generation"],
            )
            event_generation = event["halt_generation"]
            if event_generation > last_resumed_generation and (
                (
                    exhausted_generation == event_generation
                    and (event_generation, event["role"]) not in prior_bridges
                )
                or event_generation not in technical_counts_by_generation
                or not any(
                    technical["halt_generation"] == event_generation
                    and technical["role"] == event["role"]
                    and technical["route_id"] is not None
                    and technical["route_consumed_at"] is not None
                    for technical in technical_audit
                )
                or event_time is None
                or event_time <= technical_times[
                    next(
                        index for index, technical in enumerate(technical_audit)
                        if technical["halt_generation"] == event_generation
                    )
                ]
            ):
                raise _invalid_ledger(issue_id)
            if exhausted_generation != event_generation:
                prior_bridges.add((event_generation, event["role"]))

        state = {
            "version": 1,
            "issue_id": issue_id,
            "total_escalations": total,
            "halted": halted,
            "halted_reason": halted_reason,
            "halt_generation": generation,
            "roles": roles,
            "resume_count": resume_count,
            "last_resumed_at": last_resumed_at,
            "last_resume_reason": last_resume_reason,
            "last_resumed_halt_generation": last_resumed_generation,
            "consumption_audit": durable_audit,
            "remediation_rearm_audit": rearm_audit,
            "technical_remediation_audit": technical_audit,
            "failure_receipts": failure_receipts,
            "campaign_review_retry_receipts": campaign_receipts,
        }
        if "halted_role" in raw:
            state["halted_role"] = halted_role

        terminal_outcome = _normalize_terminal_outcome(
            raw.get("terminal_outcome"),
            issue_id,
            halted=halted,
            halted_reason=halted_reason,
            halted_role=halted_role,
            halt_generation=generation,
        )
        if terminal_outcome is not None:
            state["terminal_outcome"] = terminal_outcome

        reclassification_raw = raw.get("terminal_reclassification")
        if reclassification_raw is not None:
            reclassification_generation = (
                reclassification_raw.get("halt_generation")
                if isinstance(reclassification_raw, dict) else None
            )
            evidence_kind = (
                reclassification_raw.get("evidence_kind")
                if isinstance(reclassification_raw, dict) else None
            )
            reclassified_terminal = (
                terminal_outcome is not None
                and terminal_outcome["action"] == "technical_blocked"
                and terminal_outcome["reason_code"]
                == "legacy_deterministic_failure_reclassified"
                and reclassification_generation == generation
            )
            reclassification_resume = next(
                (
                    event for event in technical_audit
                    if event["halt_generation"] == reclassification_generation
                ),
                None,
            )
            if (
                not isinstance(reclassification_raw, dict)
                or set(reclassification_raw) != _TERMINAL_RECLASSIFICATION_KEYS
            ):
                raise _invalid_ledger(issue_id)
            causal_facts = _normalize_legacy_causal_facts(
                reclassification_raw.get("causal_facts"), issue_id,
            )
            causal_digest = reclassification_raw.get("causal_digest")
            if (
                reclassification_raw.get("code") != _RECLASSIFICATION_CODE
                or _utc_timestamp(reclassification_raw.get("at")) is None
                or type(reclassification_generation) is not int
                or not 1 <= reclassification_generation <= generation
                or reclassification_raw.get("signal") not in FAILURE_KINDS
                or reclassification_raw.get("role") not in ROLE_DEFAULTS
                or evidence_kind not in {
                    _LEGACY_LEDGER_EVIDENCE_KIND, _LEGACY_RECEIPT_EVIDENCE_KIND,
                }
                or not isinstance(causal_digest, str)
                or _DIGEST.fullmatch(causal_digest) is None
                or causal_digest != _canonical_digest(causal_facts)
                or (
                    evidence_kind == _LEGACY_LEDGER_EVIDENCE_KIND
                    and reclassification_raw.get("source_receipt")
                    != f"{_LEGACY_LEDGER_EVIDENCE_PREFIX}{reclassification_generation}"
                )
                or (
                    evidence_kind == _LEGACY_RECEIPT_EVIDENCE_KIND
                    and reclassification_raw.get("source_receipt") not in failure_receipts
                )
                or not (
                    reclassified_terminal
                    or reclassification_resume is not None
                    or (
                        terminal_outcome is not None
                        and terminal_outcome["action"] == "human_required"
                    )
                )
                or (
                    reclassified_terminal
                    and reclassification_raw.get("source_receipt")
                    != terminal_outcome["source_receipt"]
                )
                or (
                    reclassified_terminal
                    and reclassification_raw.get("signal") != terminal_outcome["signal"]
                )
                or (
                    reclassified_terminal
                    and reclassification_raw.get("role") != terminal_outcome["role"]
                )
                or not isinstance(reclassification_raw.get("previous_halted_reason"), str)
                or reclassification_raw["previous_halted_reason"] not in _CONTROLLED_HALT_REASONS
            ):
                raise _invalid_ledger(issue_id)
            source_receipt = reclassification_raw["source_receipt"]
            reclassified_at = _utc_timestamp(reclassification_raw["at"])
            if reclassified_terminal:
                authorization_raw = raw.get("remediation_authorization")
                remediation_state = (
                    "none" if authorization_raw is None
                    else authorization_raw.get("state")
                    if isinstance(authorization_raw, dict) else None
                )
                expected_causal_facts = _legacy_causal_material(
                    state,
                    signal=reclassification_raw["signal"],
                    previous_halted_reason=reclassification_raw[
                        "previous_halted_reason"
                    ],
                    remediation_state=remediation_state,
                )
                if causal_facts != expected_causal_facts:
                    raise _invalid_ledger(issue_id)
                if evidence_kind == _LEGACY_LEDGER_EVIDENCE_KIND:
                    if failure_receipts:
                        raise _invalid_ledger(issue_id)
                elif not _legacy_receipt_proves_deterministic_failure(
                    failure_receipts[source_receipt], state,
                    require_current_reason=False,
                ):
                    raise _invalid_ledger(issue_id)
            if reclassification_resume is not None:
                if reclassified_at >= _utc_timestamp(reclassification_resume["at"]):
                    raise _invalid_ledger(issue_id)
            else:
                trusted_times = [
                    parsed for parsed in (
                        last_resumed_time,
                        *(durable_times or []),
                        *(
                            item[1]
                            for items in rearm_by_generation.values()
                            for item in items
                        ),
                    ) if parsed is not None
                ]
                if trusted_times and reclassified_at <= max(trusted_times):
                    raise _invalid_ledger(issue_id)
            if reclassification_resume is not None and not reclassified_terminal:
                # A historical reclassification remains evidence after later technical
                # stops.  It may not be repurposed to classify their new terminal state.
                if reclassification_resume["halt_generation"] != reclassification_generation:
                    raise _invalid_ledger(issue_id)
            if reclassified_terminal and reclassification_resume is not None:
                raise _invalid_ledger(issue_id)
            state["terminal_reclassification"] = {
                "code": _RECLASSIFICATION_CODE,
                "at": reclassification_raw["at"],
                "source_receipt": source_receipt,
                "halt_generation": reclassification_generation,
                "signal": reclassification_raw["signal"],
                "role": reclassification_raw["role"],
                "previous_halted_reason": reclassification_raw[
                    "previous_halted_reason"
                ],
                "evidence_kind": evidence_kind,
                "causal_digest": causal_digest,
                "causal_facts": causal_facts,
            }
        elif (
            terminal_outcome is not None
            and terminal_outcome["source_receipt"] is not None
        ):
            raise _invalid_ledger(issue_id)

        authorization = raw.get("remediation_authorization")
        if authorization is None:
            return state
        if (
            not durable_present
            or not isinstance(authorization, dict)
            or set(authorization) != _REMEDIATION_KEYS
        ):
            raise _invalid_ledger(issue_id)

        authorization_state = authorization.get("state")
        authorization_role = authorization.get("role")
        authorization_generation = authorization.get("halt_generation")
        maximum = authorization.get("maximum_credits")
        remaining = authorization.get("remaining_credits")
        consumed = authorization.get("consumed_credits")
        forfeited = authorization.get("forfeited_credits")
        armed_at_text = authorization.get("armed_at")
        armed_at = _utc_timestamp(armed_at_text)
        window_raw = authorization.get("consumption_audit")
        authorization_rearms = (
            rearm_by_generation.get(authorization_generation, [])
            if type(authorization_generation) is int else []
        )
        latest_rearm = authorization_rearms[-1][0] if authorization_rearms else None
        rearmed = latest_rearm is not None
        if (
            not isinstance(authorization_state, str)
            or authorization_state not in {
                "active", "exhausted", "cancelled", "invalidated",
            }
            or not isinstance(authorization_role, str)
            or authorization_role not in ROLE_DEFAULTS
            or type(authorization_generation) is not int
            or authorization_generation <= 0
            or type(maximum) is not int
            or not 1 <= maximum <= MAX_REMEDIATION_CREDITS
            or type(remaining) is not int
            or not 0 <= remaining <= maximum
            or type(consumed) is not int
            or consumed < 0
            or type(forfeited) is not int
            or forfeited < 0
            or maximum != remaining + consumed + forfeited
            or armed_at is None
            or not isinstance(window_raw, list)
            or len(window_raw) > MAX_REMEDIATION_CREDITS
            or (
                authorization_generation != last_resumed_generation
                and not (
                    latest_rearm is not None
                    and latest_rearm.get("exhausted_halt_generation")
                    != authorization_generation
                    and authorization_generation in technical_counts_by_generation
                )
            )
            or last_resumed_time is None
            or (
                rearmed
                and (
                    armed_at_text != latest_rearm["at"]
                    or authorization_role != latest_rearm["role"]
                    or maximum != latest_rearm["granted_credits"]
                )
            )
            or (not rearmed and armed_at > last_resumed_time)
        ):
            raise _invalid_ledger(issue_id)

        window_audit = []
        window_times = []
        for value in window_raw:
            event, event_time = _normalize_consumption_event(value, issue_id)
            if (
                event["role"] != authorization_role
                or event["halt_generation"] != authorization_generation
                or event_time < armed_at
            ):
                raise _invalid_ledger(issue_id)
            window_audit.append(event)
            window_times.append(event_time)
        prior_times = [
            event_time
            for event, event_time in zip(durable_audit, durable_times, strict=True)
            if (
                event["halt_generation"] < authorization_generation
                or (
                    event["halt_generation"] == authorization_generation
                    and event_time < armed_at
                )
            )
        ]
        durable_window = [
            event
            for event, event_time in zip(durable_audit, durable_times, strict=True)
            if (
                event["halt_generation"] == authorization_generation
                and event_time >= armed_at
            )
        ]
        if (
            len(window_audit) != consumed
            or window_audit != durable_window
            or window_times != sorted(window_times)
            or any(event_time < armed_at for event_time in window_times)
            or (prior_times and armed_at < prior_times[-1])
            or (
                consumed > 0
                and (
                    authorization_role not in roles
                    or roles[authorization_role]["failures_since_escalation"] < consumed
                )
            )
            or any(
                event["halt_generation"] > authorization_generation
                for event in durable_audit
            )
        ):
            raise _invalid_ledger(issue_id)

        coherent = False
        if authorization_state == "active":
            coherent = (
                not halted
                and generation == authorization_generation
                and remaining > 0
                and forfeited == 0
                and halted_role == authorization_role
            )
        elif authorization_state == "exhausted":
            # Exhaustion closes the human retry budget, not the separately
            # audited local technical-diagnostic lane. Every later cleared
            # generation must have exactly one contiguous technical receipt.
            first_technical_generation = authorization_generation + 1
            last_cleared_generation = (
                generation - 1 if halted else generation
            )
            technical_generation_count = sum(
                event["halt_generation"] >= first_technical_generation
                for event in technical_audit
            )
            coherent = (
                remaining == 0
                and forfeited == 0
                and consumed == maximum
                and (
                    (not halted and generation == authorization_generation)
                    or (
                        generation >= first_technical_generation
                        and technical_generation_count == (
                            last_cleared_generation - first_technical_generation + 1
                        )
                    )
                )
            )
        elif authorization_state == "invalidated":
            # An invalidated human window remains terminal for campaign effects.
            # Each following deterministic stop can be cleared exactly once for a
            # separately audited local diagnostic, so require a contiguous chain
            # rather than accepting a gap or reviving the authorization itself.
            first_technical_generation = authorization_generation + 1
            last_cleared_generation = (
                generation - 1 if halted else generation
            )
            technical_generation_count = sum(
                event["halt_generation"] >= first_technical_generation
                for event in technical_audit
            )
            coherent = (
                remaining == 0
                and forfeited > 0
                and generation >= first_technical_generation
                # Audit generations are already unique, ordered and bounded by
                # last_cleared_generation. Equal cardinality therefore proves
                # the complete contiguous suffix without materializing it.
                and technical_generation_count == (
                    last_cleared_generation - first_technical_generation + 1
                )
            )
        else:
            coherent = (
                halted
                and generation == authorization_generation + 1
                and remaining == 0
                and forfeited > 0
            )
        if not coherent:
            raise _invalid_ledger(issue_id)

        state["remediation_authorization"] = {
            "state": authorization_state,
            "role": authorization_role,
            "halt_generation": authorization_generation,
            "maximum_credits": maximum,
            "remaining_credits": remaining,
            "armed_at": armed_at_text,
            "consumption_audit": window_audit,
            "consumed_credits": consumed,
            "forfeited_credits": forfeited,
        }
        return state

    def _locked(self, issue_id: str, mutate) -> tuple[dict, object]:
        path = self._path(issue_id)
        with self._issue_lock(issue_id, fcntl.LOCK_EX):
            while True:
                try:
                    descriptor = os.open(
                        path,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | _SAFE_OPEN_FLAGS,
                        0o600,
                    )
                    created = True
                    break
                except FileExistsError:
                    try:
                        descriptor = os.open(
                            path, os.O_RDWR | _SAFE_OPEN_FLAGS,
                        )
                        created = False
                        break
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise RoutingConfigError(
                            f"état d'escalade illisible pour {issue_id}."
                        ) from exc
                except OSError as exc:
                    raise RoutingConfigError(
                        f"état d'escalade illisible pour {issue_id}."
                    ) from exc
            with _fdopen_text(descriptor, "r+") as handle:
                try:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise RoutingConfigError(
                            f"état d'escalade illisible pour {issue_id}."
                        )
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except OSError as exc:
                    raise RoutingConfigError(
                        f"état d'escalade illisible pour {issue_id}."
                    ) from exc
                try:
                    raw = handle.read()
                    if not raw and not created:
                        raise RoutingConfigError(
                            f"état d'escalade corrompu pour {issue_id}."
                        )
                    untrusted = (
                        _strict_json_loads(raw) if raw else self._new_state(issue_id)
                    )
                except (ValueError, RecursionError, UnicodeError) as exc:
                    raise RoutingConfigError(
                        f"état d'escalade corrompu pour {issue_id}."
                    ) from exc
                state = self._normalize_state(untrusted, issue_id)
                try:
                    result, changed = mutate(state)
                except Exception:
                    if created:
                        state = self._normalize_state(
                            self._new_state(issue_id), issue_id,
                        )
                        handle.seek(0)
                        json.dump(
                            state, handle, ensure_ascii=False, indent=2,
                            sort_keys=True,
                        )
                        handle.write("\n")
                        handle.truncate()
                        handle.flush()
                        os.fsync(handle.fileno())
                    raise
                if changed or created:
                    state = self._normalize_state(state, issue_id)
                    handle.seek(0)
                    json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                return state, result

    def status(self, issue_id: str) -> dict:
        """Read issue state under the stable creation lock and state-inode lock."""
        issue_id = _validate_issue_id(issue_id)
        path = self._path(issue_id)
        try:
            os.lstat(path)
        except FileNotFoundError:
            state = self._normalize_state(self._new_state(issue_id), issue_id)
        except OSError as exc:
            raise RoutingConfigError(
                f"état d'escalade illisible pour {issue_id}."
            ) from exc
        else:
            with self._issue_lock(issue_id, fcntl.LOCK_SH):
                try:
                    descriptor = os.open(path, os.O_RDONLY | _SAFE_OPEN_FLAGS)
                except OSError as exc:
                    raise RoutingConfigError(
                        f"état d'escalade illisible pour {issue_id}."
                    ) from exc
                with _fdopen_text(descriptor, "r") as handle:
                    try:
                        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                            raise RoutingConfigError(
                                f"état d'escalade illisible pour {issue_id}."
                            )
                        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                        raw = handle.read()
                    except OSError as exc:
                        raise RoutingConfigError(
                            f"état d'escalade illisible pour {issue_id}."
                        ) from exc
                    except UnicodeError as exc:
                        raise RoutingConfigError(
                            f"état d'escalade illisible pour {issue_id}."
                        ) from exc
            try:
                if not raw:
                    raise RoutingConfigError(
                        f"état d'escalade corrompu pour {issue_id}."
                    )
                untrusted = _strict_json_loads(raw)
            except (ValueError, RecursionError) as exc:
                raise RoutingConfigError(
                    f"état d'escalade corrompu pour {issue_id}."
                ) from exc
            state = self._normalize_state(untrusted, issue_id)
        terminal_outcome = self._terminal_outcome(state)
        payload = {
            "version": state["version"],
            "issue_id": state["issue_id"],
            "total_escalations": state["total_escalations"],
            "halted": state["halted"],
            "halted_reason": state["halted_reason"],
            "halt_generation": state["halt_generation"],
            "roles": {
                role: dict(record) for role, record in state["roles"].items()
            },
            "resume_count": state["resume_count"],
            "last_resumed_at": state["last_resumed_at"],
            "last_resume_reason": state["last_resume_reason"],
            "last_resumed_halt_generation": state["last_resumed_halt_generation"],
            "consumption_audit": [
                dict(event) for event in state["consumption_audit"]
            ],
            "remediation_rearm_audit": [
                dict(event) for event in state["remediation_rearm_audit"]
            ],
            "technical_remediation_audit": [
                dict(event) for event in state["technical_remediation_audit"]
            ],
            "max_escalations_per_issue": MAX_ESCALATIONS_PER_ISSUE,
            "max_parallel_agents": MAX_PARALLEL_AGENTS,
            "remediation_authorization": self._remediation(state),
            "terminal_outcome": terminal_outcome,
            "human_required": terminal_outcome["human_required"],
            "technical_blocked": terminal_outcome["technical_blocked"],
            "technical_remediation": self._technical_remediation_view(state),
            "technical_remediation_open": self._technical_remediation_open(state),
        }
        reclassification = state.get("terminal_reclassification")
        if reclassification is not None:
            payload["terminal_reclassification"] = {
                key: reclassification[key]
                for key in (
                    "code", "at", "halt_generation", "signal", "role",
                    "previous_halted_reason", "evidence_kind", "causal_digest",
                )
            }
        if "halted_role" in state:
            payload["halted_role"] = state["halted_role"]
        return payload

    def consume_campaign_review_remediation(
        self, issue_id: str, current_tier: str, *, effect_id: str,
        authorization_digest: str, expires_at: int, now_ms: int,
    ) -> dict:
        """Consume or replay one compact receipt for an exact campaign grant."""
        _validate_issue_id(issue_id)
        _validate_tier(current_tier)
        if (not isinstance(effect_id, str)
                or _FAILURE_IDEMPOTENCY.fullmatch(effect_id) is None
                or not isinstance(authorization_digest, str)
                or _DIGEST.fullmatch(authorization_digest) is None
                or type(expires_at) is not int or expires_at <= 0
                or type(now_ms) is not int or now_ms < 0):
            raise RoutingConfigError(
                "coordonnées d'autorisation de première review invalides."
            )

        def mutate(state):
            receipts = state["campaign_review_retry_receipts"]
            existing = receipts.get(authorization_digest)
            if existing is not None:
                if existing["effect_id"] != effect_id:
                    raise RoutingConfigError(
                        "coordonnées d'autorisation de première review incohérentes."
                    )
                return dict(existing["decision"]), False
            if any(receipt["effect_id"] == effect_id for receipt in receipts.values()):
                raise RoutingConfigError(
                    "coordonnées d'autorisation de première review incohérentes."
                )
            if now_ms >= expires_at:
                denial = {
                    "contract": "foundry-campaign-review-authorization-expired.v1",
                    "issue_id": issue_id,
                    "effect_id": effect_id,
                    "authorization_digest": authorization_digest,
                }
                return {
                    "allowed": False, "selected_tier": current_tier,
                    "audit_digest": _canonical_digest(denial),
                    "action": "authorization_expired",
                }, False
            failure = state["failure_receipts"].get(effect_id)
            if (not isinstance(failure, dict)
                    or failure.get("role") != "implementer"
                    or failure.get("kind") != "review_blocking"
                    or failure.get("current_tier") != current_tier
                    or failure.get("decision", {}).get("action") != "failure_recorded"
                    or failure.get("decision", {}).get("human_required") is not False):
                raise RoutingConfigError(
                    "reçu exact de première review bloquée absent ; remédiation refusée."
                )
            if state["halted"] or self._remediation(state)["state"] != "none":
                raise RoutingConfigError(
                    "politique de remédiation déjà active ; première review refusée."
                )
            if len(receipts) >= MAX_CAMPAIGN_REVIEW_RECEIPTS:
                raise RoutingConfigError("ledger de reçus review hors borne.")
            selected_tier = _higher(
                current_tier,
                state["roles"].get("implementer", {}).get("minimum_tier")
                or current_tier,
            )
            material = _campaign_review_decision_material(
                issue_id, effect_id, authorization_digest, selected_tier,
            )
            decision = {
                "allowed": True,
                "selected_tier": selected_tier,
                "audit_digest": _canonical_digest(material),
                "action": "remediation_continued",
                "authorization_digest": authorization_digest,
            }
            receipts[authorization_digest] = {
                "authorization_digest": authorization_digest,
                "effect_id": effect_id,
                "state": "consumed",
                "decision": decision,
            }
            return dict(decision), True

        _, result = self._locked(issue_id, mutate)
        return result

    def reclassify_legacy_terminal(
        self, issue_id: str, halt_generation: int,
    ) -> dict:
        """Classify one released-v1 stop without releasing it or changing its budget."""
        issue_id = _validate_issue_id(issue_id)
        halt_generation = _validate_halt_generation(halt_generation)

        def mutate(state):
            if not state["halted"]:
                raise RoutingConfigError(
                    f"{issue_id} n'est pas arrêtée ; reclassification refusée."
                )
            if state["halt_generation"] != halt_generation:
                raise RoutingConfigError(
                    "génération d'arrêt obsolète ; reclassification refusée."
                )
            terminal = self._terminal_outcome(state)
            if terminal["action"] == "technical_blocked":
                return {
                    "action": "technical_blocked",
                    "issue_id": issue_id,
                    "halt_generation": halt_generation,
                    "human_required": False,
                    "technical_remediation": self._technical_remediation_view(state),
                    "reclassified": False,
                }, False
            if terminal["action"] != "authority_ambiguous":
                raise RoutingConfigError(
                    "arrêt déjà classé ; reclassification technique refusée."
                )
            signal = _legacy_terminal_signal(state)
            if signal is None:
                return {
                    "action": "authority_ambiguous",
                    "issue_id": issue_id,
                    "halt_generation": halt_generation,
                    "human_required": False,
                    "technical_remediation": None,
                    "reclassified": False,
                }, False
            previous_reason = state["halted_reason"]
            evidence = _legacy_reclassification_evidence(state, signal=signal)
            if evidence is None:
                return {
                    "action": "authority_ambiguous",
                    "issue_id": issue_id,
                    "halt_generation": halt_generation,
                    "human_required": False,
                    "technical_remediation": None,
                    "reclassified": False,
                }, False
            source_receipt, evidence_kind = evidence
            causal_facts = _legacy_causal_material(
                state, signal=signal, previous_halted_reason=previous_reason,
            )
            causal_digest = _canonical_digest(causal_facts)
            reason_code = "legacy_deterministic_failure_reclassified"
            state["halted_reason"] = _TECHNICAL_REASON_MESSAGES[reason_code]
            state["terminal_outcome"] = {
                "action": "technical_blocked",
                "category": "deterministic_failure",
                "reason_code": reason_code,
                "signal": signal,
                "role": state["halted_role"],
                "halt_generation": halt_generation,
                "diagnostic": None,
                "source_receipt": source_receipt,
            }
            state["terminal_reclassification"] = {
                "code": _RECLASSIFICATION_CODE,
                "at": self._next_audit_timestamp(state),
                "source_receipt": source_receipt,
                "halt_generation": halt_generation,
                "signal": signal,
                "role": state["halted_role"],
                "previous_halted_reason": previous_reason,
                "evidence_kind": evidence_kind,
                "causal_digest": causal_digest,
                "causal_facts": causal_facts,
            }
            _normalize_terminal_outcome(
                state["terminal_outcome"], issue_id, halted=True,
                halted_reason=state["halted_reason"],
                halted_role=state["halted_role"], halt_generation=halt_generation,
            )
            return {
                "action": "technical_blocked",
                "issue_id": issue_id,
                "halt_generation": halt_generation,
                "human_required": False,
                "technical_remediation": self._technical_remediation_view(state),
                "reclassified": True,
            }, True

        _, result = self._locked(issue_id, mutate)
        return result

    def resume_technical_remediation(
        self, issue_id: str, halt_generation: int, evidence_digest: str,
    ) -> dict:
        """Open one local diagnostic turn without creating a human or campaign grant."""
        issue_id = _validate_issue_id(issue_id)
        halt_generation = _validate_halt_generation(halt_generation)
        if not isinstance(evidence_digest, str) or _DIGEST.fullmatch(evidence_digest) is None:
            raise RoutingConfigError("digest de diagnostic technique invalide.")

        def result_for(state, *, replayed: bool) -> dict:
            return {
                "action": "technical_remediation_resumed",
                "issue_id": issue_id,
                "halt_generation": halt_generation,
                "human_required": False,
                "provider_effect_allowed": False,
                "campaign_restart_allowed": False,
                "replayed": replayed,
                "technical_remediation_audit": [
                    dict(event) for event in state["technical_remediation_audit"]
                ],
            }

        def mutate(state):
            audit = state["technical_remediation_audit"]
            if not state["halted"]:
                if (
                    audit
                    and audit[-1]["halt_generation"] == halt_generation
                    and audit[-1]["evidence_digest"] == evidence_digest
                ):
                    return result_for(state, replayed=True), False
                raise RoutingConfigError(
                    f"{issue_id} n'est pas arrêtée ; remédiation technique refusée."
                )
            if state["halt_generation"] != halt_generation:
                raise RoutingConfigError(
                    "génération d'arrêt obsolète ; remédiation technique refusée."
                )
            if self._terminal_outcome(state)["action"] != "technical_blocked":
                raise RoutingConfigError(
                    "arrêt sans blocage technique attesté ; remédiation refusée."
                )
            if sum(
                event["halt_generation"] == halt_generation
                for event in audit
            ) >= MAX_TECHNICAL_REMEDIATIONS_PER_GENERATION:
                raise RoutingConfigError(
                    "diagnostic technique déjà consommé pour cette génération."
                )
            event = {
                "code": _TECHNICAL_REMEDIATION_RESUME_CODE,
                "at": self._next_audit_timestamp(state),
                "halt_generation": halt_generation,
                "evidence_digest": evidence_digest,
                "role": state["halted_role"],
                "route_id": None,
                "route_consumed_at": None,
                "review_diff_hash": None,
                "review_claimed_at": None,
                "review_rearm_audit": [],
            }
            audit.append(event)
            state["halted"] = False
            state["halted_reason"] = None
            state.pop("terminal_outcome", None)
            return result_for(state, replayed=False), True

        _, result = self._locked(issue_id, mutate)
        return result

    def resume(
        self, issue_id: str, reason: str, halt_generation: int,
        remediation_credits: int | None = None,
    ) -> dict:
        """Clear one human stop while preserving every escalation budget and floor."""
        issue_id = _validate_issue_id(issue_id)
        reason = _validate_resume_reason(reason)
        halt_generation = _validate_halt_generation(halt_generation)
        if remediation_credits is not None:
            remediation_credits = _validate_remediation_credits(remediation_credits)

        def mutate(state):
            if not state["halted"]:
                raise RoutingConfigError(f"{issue_id} n'est pas arrêtée ; reprise refusée.")
            if self._terminal_outcome(state)["action"] != "human_required":
                raise RoutingConfigError(
                    "arrêt sans verdict humain valide ; reprise humaine refusée."
                )
            current_generation = state.get("halt_generation", 1)
            if type(current_generation) is not int or current_generation <= 0:
                raise RoutingConfigError(f"état d'escalade invalide pour {issue_id}.")
            if halt_generation != current_generation:
                raise RoutingConfigError("génération d'arrêt obsolète ; reprise refusée.")
            resume_anchor = self._next_audit_timestamp(state)
            if remediation_credits is not None:
                role = state.get("halted_role")
                if not isinstance(role, str) or role not in ROLE_DEFAULTS:
                    raise RoutingConfigError(
                        "rôle de correction arrêté ambigu ou absent ; autorisation refusée."
                    )
                state["remediation_authorization"] = {
                    "state": "active", "role": role,
                    "halt_generation": current_generation,
                    "maximum_credits": remediation_credits,
                    "remaining_credits": remediation_credits,
                    "armed_at": resume_anchor,
                    "consumption_audit": [],
                    "consumed_credits": 0,
                    "forfeited_credits": 0,
                }
            elif remediation_credits is None:
                # A normal human resume explicitly has no lingering automatic window.
                state.pop("remediation_authorization", None)
            resume_count = state.get("resume_count", 0)
            if type(resume_count) is not int or resume_count < 0:
                raise RoutingConfigError(f"état d'escalade invalide pour {issue_id}.")
            resumed_at = (
                self._next_audit_timestamp(state)
                if remediation_credits is not None else resume_anchor
            )
            # Deliberately mutate only the stop and its v1-compatible human audit.
            state["halted"] = False
            state["halted_reason"] = None
            state.pop("terminal_outcome", None)
            state["halt_generation"] = current_generation
            state["resume_count"] = resume_count + 1
            state["last_resumed_at"] = resumed_at
            state["last_resume_reason"] = reason
            state["last_resumed_halt_generation"] = current_generation
            return {
                "action": "resumed",
                "issue_id": issue_id,
                "resume_count": state["resume_count"],
                "last_resumed_at": resumed_at,
                "last_resume_reason": reason,
                "last_resumed_halt_generation": current_generation,
                "halt_generation": current_generation,
                "total_escalations": state["total_escalations"],
                "max_escalations_per_issue": MAX_ESCALATIONS_PER_ISSUE,
                "max_parallel_agents": MAX_PARALLEL_AGENTS,
                "remediation_authorization": self._remediation(state),
            }, True

        _, result = self._locked(issue_id, mutate)
        return result

    def rearm_remediation(
        self,
        issue_id: str,
        role: str,
        reason: str,
        halt_generation: int,
        remediation_credits: int,
        current_halt_generation: int | None = None,
    ) -> dict:
        """CAS-rearm an exhausted authorization, optionally after one diagnostic."""
        issue_id = _validate_issue_id(issue_id)
        role = _validate_role(role)
        reason = _validate_resume_reason(reason)
        halt_generation = _validate_halt_generation(halt_generation)
        remediation_credits = _validate_remediation_credits(remediation_credits)
        if current_halt_generation is not None:
            current_halt_generation = _validate_halt_generation(
                current_halt_generation,
            )

        def mutate(state):
            authorization = self._remediation(state)
            if authorization["state"] != "exhausted":
                raise RoutingConfigError(
                    "fenêtre de remédiation épuisée attendue ; réarmement refusé."
                )
            observed_generation = (
                halt_generation
                if current_halt_generation is None else current_halt_generation
            )
            if authorization["halt_generation"] != halt_generation:
                raise RoutingConfigError(
                    "génération d'arrêt obsolète ; réarmement refusé."
                )
            if authorization["role"] != role:
                raise RoutingConfigError(
                    "rôle de remédiation différent ; réarmement refusé."
                )
            if state["halted"] or state["halt_generation"] != observed_generation:
                raise RoutingConfigError(
                    "génération d'arrêt obsolète ; génération technique observée "
                    "obsolète ; réarmement refusé."
                )
            bridging_technical_generation = observed_generation != halt_generation
            if bridging_technical_generation:
                technical_audit = state["technical_remediation_audit"]
                if (
                    observed_generation < halt_generation
                    or not technical_audit
                    or technical_audit[-1]["halt_generation"] != observed_generation
                    or technical_audit[-1]["role"] != role
                    or technical_audit[-1]["route_id"] is None
                    or technical_audit[-1]["route_consumed_at"] is None
                ):
                    raise RoutingConfigError(
                        "diagnostic technique observé absent ou incompatible ; "
                        "réarmement refusé."
                    )

            exhausted = state["remediation_authorization"]
            rearmed_at = self._next_audit_timestamp(state)
            audit = {
                "code": _REMEDIATION_REARM_CODE,
                "at": rearmed_at,
                "reason": reason,
                "role": role,
                "halt_generation": observed_generation,
                "granted_credits": remediation_credits,
                "exhausted_window_armed_at": exhausted["armed_at"],
                "exhausted_window_maximum_credits": exhausted["maximum_credits"],
            }
            if bridging_technical_generation:
                audit["exhausted_halt_generation"] = halt_generation
            state["remediation_rearm_audit"].append(audit)
            state["remediation_authorization"] = {
                "state": "active",
                "role": role,
                "halt_generation": observed_generation,
                "maximum_credits": remediation_credits,
                "remaining_credits": remediation_credits,
                "armed_at": rearmed_at,
                "consumption_audit": [],
                "consumed_credits": 0,
                "forfeited_credits": 0,
            }
            return {
                "action": "remediation_rearmed",
                "issue_id": issue_id,
                "role": role,
                "halt_generation": observed_generation,
                "exhausted_halt_generation": halt_generation,
                "reason": reason,
                "granted_credits": remediation_credits,
                "rearmed_at": rearmed_at,
                "exhausted_window": {
                    "armed_at": audit["exhausted_window_armed_at"],
                    "maximum_credits": audit[
                        "exhausted_window_maximum_credits"
                    ],
                },
                "total_escalations": state["total_escalations"],
                "max_escalations_per_issue": MAX_ESCALATIONS_PER_ISSUE,
                "max_parallel_agents": MAX_PARALLEL_AGENTS,
                "remediation_authorization": self._remediation(state),
                "remediation_rearm_audit": [
                    dict(event) for event in state["remediation_rearm_audit"]
                ],
            }, True

        _, result = self._locked(issue_id, mutate)
        return result

    def cancel_remediation(self, issue_id: str, halt_generation: int) -> dict:
        """CAS-cancel a live remediation window and stop the issue again."""
        issue_id = _validate_issue_id(issue_id)
        halt_generation = _validate_halt_generation(halt_generation)

        def mutate(state):
            authorization = self._remediation(state)
            if authorization["state"] != "active" or authorization["halt_generation"] != halt_generation:
                raise RoutingConfigError("autorisation de remédiation absente, inactive ou obsolète.")
            raw = state["remediation_authorization"]
            raw["state"] = "cancelled"
            raw["forfeited_credits"] += raw["remaining_credits"]
            raw["remaining_credits"] = 0
            self._halt(
                state,
                action="technical_blocked",
                category="technical_policy",
                reason_code="remediation_window_cancelled",
                signal="cancel_remediation",
                role=authorization["role"],
            )
            return {
                "action": "technical_blocked", "issue_id": issue_id,
                "halt_generation": state["halt_generation"],
                "human_required": False,
                "technical_remediation": self._technical_remediation_view(state),
                "remediation_authorization": self._remediation(state),
            }, True

        _, result = self._locked(issue_id, mutate)
        return result

    def claim_technical_remediation_route(
        self,
        issue_id: str,
        role: str,
        halt_generation: int,
        route_id: str,
    ) -> dict:
        """Atomically bind the one local diagnostic route to one stable identity."""
        issue_id = _validate_issue_id(issue_id)
        role = _validate_role(role)
        halt_generation = _validate_halt_generation(halt_generation)
        if (
            not isinstance(route_id, str)
            or _FAILURE_IDEMPOTENCY.fullmatch(route_id) is None
        ):
            raise RoutingConfigError("identité de route de remédiation invalide.")
        try:
            os.lstat(self._path(issue_id))
        except FileNotFoundError as exc:
            raise RoutingConfigError(
                f"{issue_id} ne possède aucune remédiation technique ouverte."
            ) from exc
        except OSError as exc:
            raise RoutingConfigError(
                f"état d'escalade illisible pour {issue_id}."
            ) from exc

        def result_for(event: dict, *, replayed: bool) -> dict:
            return {
                "action": _TECHNICAL_REMEDIATION_ROUTE_CODE,
                "issue_id": issue_id,
                "halt_generation": halt_generation,
                "role": role,
                "replayed": replayed,
                "provider_effect_allowed": False,
                "campaign_restart_allowed": False,
                "human_authorization_required": False,
                "route_state": "claimed",
                "route_consumed_at": event["route_consumed_at"],
            }

        def mutate(state):
            if state["halted"]:
                return self._terminal_outcome(state)["action"], False
            event = self._technical_remediation_event(state)
            if event is None:
                return "technical_remediation_unavailable", False
            if event["halt_generation"] != halt_generation:
                return "technical_remediation_stale", False
            if event["role"] != role:
                return "technical_remediation_role_mismatch", False
            if event["route_id"] is None:
                event["route_id"] = route_id
                event["route_consumed_at"] = self._next_audit_timestamp(state)
                return result_for(event, replayed=False), True
            if event["route_id"] == route_id:
                return result_for(event, replayed=True), False
            return "technical_remediation_consumed", False

        _, result = self._locked(issue_id, mutate)
        if isinstance(result, dict):
            return result
        errors = {
            "technical_blocked": "est bloquée techniquement ; route locale refusée.",
            "authority_ambiguous": "possède une autorité ambiguë ; route locale refusée.",
            "human_required": "attend une décision humaine valide ; route locale refusée.",
            "technical_remediation_unavailable": (
                "ne possède aucune remédiation technique locale ouverte."
            ),
            "technical_remediation_stale": "a changé de génération ; route locale refusée.",
            "technical_remediation_role_mismatch": (
                "réserve cette remédiation au rôle arrêté ; route locale refusée."
            ),
            "technical_remediation_consumed": (
                "possède déjà une route locale consommée ; nouvel identifiant refusé."
            ),
        }
        raise EscalationTechnicalBlockedError(f"{issue_id} {errors[result]}")

    def technical_remediation_floor(
        self, issue_id: str, role: str, route_id: str,
    ) -> str | None:
        """Read a previously claimed local route; this never mutates the ledger."""
        issue_id = _validate_issue_id(issue_id)
        role = _validate_role(role)
        if (
            not isinstance(route_id, str)
            or _FAILURE_IDEMPOTENCY.fullmatch(route_id) is None
        ):
            raise RoutingConfigError("identité de route de remédiation invalide.")
        try:
            os.lstat(self._path(issue_id))
        except FileNotFoundError as exc:
            raise EscalationTechnicalBlockedError(
                f"{issue_id} ne possède aucune remédiation technique locale."
            ) from exc
        except OSError as exc:
            raise RoutingConfigError(
                f"état d'escalade illisible pour {issue_id}."
            ) from exc

        def mutate(state):
            if state["halted"]:
                return self._terminal_outcome(state)["action"], False
            event = self._technical_remediation_event(state)
            if event is None:
                return "technical_remediation_unavailable", False
            if event["role"] != role:
                return "technical_remediation_role_mismatch", False
            if event["route_id"] is None:
                return "technical_remediation_pending", False
            if event["route_id"] != route_id:
                return "technical_remediation_consumed", False
            return state["roles"].get(role, {}).get("minimum_tier"), False

        state, result = self._locked(issue_id, mutate)
        if result == "technical_blocked":
            reason_code = self._terminal_outcome(state)["reason_code"]
            raise EscalationTechnicalBlockedError(
                f"{issue_id} est bloquée techniquement ({reason_code}) ; "
                "diagnostic local borné requis."
            )
        if result == "authority_ambiguous":
            raise EscalationAuthorityAmbiguousError(
                f"{issue_id} possède un arrêt dont l'autorité est ambiguë ; "
                "nouvelle délégation refusée."
            )
        if result == "human_required":
            raise EscalationHumanRequiredError(
                f"{issue_id} attend une décision humaine valide. "
                "Intervention humaine requise."
            )
        messages = {
            "technical_remediation_unavailable": "ne possède aucune route locale active.",
            "technical_remediation_role_mismatch": (
                "réserve la route locale au rôle arrêté."
            ),
            "technical_remediation_pending": (
                "attend la réclamation atomique de sa route locale."
            ),
            "technical_remediation_consumed": (
                "refuse un identifiant différent après réclamation de la route locale."
            ),
        }
        if isinstance(result, str) and result in messages:
            raise EscalationTechnicalBlockedError(f"{issue_id} {messages[result]}")
        return result

    def claim_fresh_reviewer_authorization(
        self,
        issue_id: str,
        diff_hash: str,
        *,
        validated_claim: Callable[[], object],
        validated_rearm: Callable[[str], object | None] | None = None,
    ) -> object:
        """Validate a Git claim, then bind one bounded reviewer authorization.

        The callback must perform the claim/coordinates/current-diff checks in the
        review ledger.  It runs while the issue lock is held, after the escalation
        state has proved that this hash may use the reviewer authorization and before
        that authorization is persisted.  A rejected callback therefore cannot bind
        ``review_diff_hash``; a competing hash cannot create a review claim.  A
        distinct second diff is possible exactly once, only through
        ``validated_rearm`` after it has verified the terminal structured proof of
        the first claim.  A completed claim whose public verdict has
        ``should_run=false`` is returned idempotently and never binds or replaces
        the technical review slot.  An in-progress duplicate still reserves the
        slot: it represents an already-active reviewer even though this caller does
        not receive another launch capability.
        """
        issue_id = _validate_issue_id(issue_id)
        if not isinstance(diff_hash, str) or _DIGEST.fullmatch(diff_hash) is None:
            raise RoutingConfigError("hash de diff reviewer invalide.")
        if not callable(validated_claim):
            raise RoutingConfigError(
                "claim reviewer : validation Git atomique requise avant liaison."
            )
        if validated_rearm is not None and not callable(validated_rearm):
            raise RoutingConfigError(
                "réarm de review : validation terminale atomique requise."
            )
        try:
            os.lstat(self._path(issue_id))
        except FileNotFoundError:
            return validated_claim()
        except OSError as exc:
            raise RoutingConfigError(
                f"état d'escalade illisible pour {issue_id}."
            ) from exc

        def claim_reserves_slot(claim: object) -> bool:
            should_run = getattr(claim, "should_run", None)
            claim_state = getattr(claim, "state", None)
            if (
                type(should_run) is not bool
                or claim_state not in {"in_progress", "completed"}
                or (should_run and claim_state != "in_progress")
            ):
                raise RoutingConfigError(
                    "claim reviewer : verdict exécutable et état cohérents requis."
                )
            return claim_state == "in_progress"

        def mutate(state):
            remediation = self._remediation(state)
            if remediation["state"] == "invalid":
                self._halt(
                    state,
                    action="technical_blocked",
                    category="technical_policy",
                    reason_code="remediation_authorization_invalid",
                    signal="explicit_user_request",
                    role="reviewer",
                )
                return "technical_blocked", True
            if state["halted"]:
                return self._terminal_outcome(state)["action"], False
            technical_event = self._technical_remediation_event(state)
            if technical_event is None:
                return ("validated", validated_claim()), False
            if (
                technical_event["role"] not in {"implementer", "reviewer"}
                or technical_event["route_id"] is None
                or technical_event["route_consumed_at"] is None
            ):
                return "technical_remediation_pending", False
            claimed_diff = technical_event["review_diff_hash"]
            if claimed_diff is None:
                claim = validated_claim()
                if not claim_reserves_slot(claim):
                    return ("validated", claim), False
                technical_event["review_diff_hash"] = diff_hash
                technical_event["review_claimed_at"] = self._next_audit_timestamp(state)
                return ("validated", claim), True
            if claimed_diff == diff_hash:
                return ("validated", validated_claim()), False
            rearm_audit = technical_event["review_rearm_audit"]
            if rearm_audit:
                return "technical_reviewer_rearm_consumed", False
            if validated_rearm is None:
                return "technical_reviewer_terminal_proof_required", False
            previous_claimed_at = technical_event["review_claimed_at"]
            binding = validated_rearm(claimed_diff)
            if binding is None:
                return "technical_reviewer_terminal_proof_required", False
            if (
                not isinstance(binding, dict)
                or set(binding) != _TECHNICAL_REVIEW_REARM_BINDING_KEYS
                or not isinstance(binding.get("proof_id"), str)
                or _DIGEST.fullmatch(binding["proof_id"]) is None
                or _utc_timestamp(binding.get("completed_at")) is None
                or binding.get("quality") not in {"mergeable", "blocked"}
                or type(binding.get("all_pass")) is not bool
            ):
                raise RoutingConfigError(
                    "réarm de review : preuve terminale valide requise."
                )
            completed_at = _utc_timestamp(binding["completed_at"])
            claimed_at = _utc_timestamp(previous_claimed_at)
            polluted = completed_at < claimed_at
            mergeable = binding["quality"] == "mergeable" and binding["all_pass"]
            if not polluted and not mergeable:
                return "technical_reviewer_terminal_proof_required", False
            claim = validated_claim()
            if not claim_reserves_slot(claim):
                return ("validated", claim), False
            rearmed_at = self._next_audit_timestamp(state)
            technical_event["review_diff_hash"] = diff_hash
            technical_event["review_claimed_at"] = rearmed_at
            rearm_audit.append({
                "code": (
                    "technical_review_pollution_reconciled"
                    if polluted else "technical_review_rearmed"
                ),
                "previous_diff_hash": claimed_diff,
                "previous_claimed_at": previous_claimed_at,
                "terminal_proof_id": binding["proof_id"],
                "terminal_completed_at": binding["completed_at"],
                "terminal_quality": binding["quality"],
                "repair_kind": (
                    "terminal_before_binding_reconciliation"
                    if polluted else "mergeable_terminal_rearm"
                ),
                "new_diff_hash": diff_hash,
                "rearmed_at": rearmed_at,
            })
            return ("validated", claim), True

        state, result = self._locked(issue_id, mutate)
        if result == "technical_blocked":
            reason_code = self._terminal_outcome(state)["reason_code"]
            raise EscalationTechnicalBlockedError(
                f"{issue_id} est bloquée techniquement ({reason_code}) ; "
                "diagnostic local borné requis."
            )
        if result == "authority_ambiguous":
            raise EscalationAuthorityAmbiguousError(
                f"{issue_id} possède un arrêt dont l'autorité est ambiguë ; "
                "nouvelle délégation refusée."
            )
        if result == "human_required":
            raise EscalationHumanRequiredError(
                f"{issue_id} attend une décision humaine valide. "
                "Intervention humaine requise."
            )
        messages = {
            "technical_remediation_pending": (
                "attend une route de remédiation technique consommée par son "
                "implémenteur ou reviewer ; nouvelle review refusée."
            ),
            "technical_reviewer_consumed": (
                "a déjà autorisé une review fraîche pour un autre diff ; "
                "nouvelle review refusée."
            ),
            "technical_reviewer_terminal_proof_required": (
                "a déjà autorisé une review et ne peut la réarmer qu'après une "
                "preuve AC terminale valide."
            ),
            "technical_reviewer_rearm_consumed": (
                "a déjà consommé son unique réarm de review technique."
            ),
        }
        if isinstance(result, str) and result in messages:
            raise EscalationTechnicalBlockedError(f"{issue_id} {messages[result]}")
        if not isinstance(result, tuple) or len(result) != 2 or result[0] != "validated":
            raise RoutingConfigError(
                "claim reviewer : résultat de validation Git incohérent."
            )
        return result[1]

    def active_floor(
        self,
        issue_id: str,
        role: str,
        *,
        technical_remediation: bool = False,
        technical_remediation_id: str | None = None,
    ) -> str | None:
        issue_id = _validate_issue_id(issue_id)
        role = _validate_role(role)
        if type(technical_remediation) is not bool:
            raise RoutingConfigError("portée de remédiation technique invalide.")
        if technical_remediation:
            if technical_remediation_id is None:
                raise RoutingConfigError(
                    "identité de route requise pour la remédiation technique."
                )
            return self.technical_remediation_floor(
                issue_id, role, technical_remediation_id,
            )
        if technical_remediation_id is not None:
            raise RoutingConfigError(
                "identité de route sans portée de remédiation technique."
            )
        try:
            os.lstat(self._path(issue_id))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RoutingConfigError(
                f"état d'escalade illisible pour {issue_id}."
            ) from exc

        def mutate(state):
            remediation = self._remediation(state)
            if remediation["state"] == "invalid":
                self._halt(
                    state,
                    action="technical_blocked",
                    category="technical_policy",
                    reason_code="remediation_authorization_invalid",
                    signal="explicit_user_request",
                    role=role,
                )
                return "technical_blocked", True
            if state["halted"]:
                return self._terminal_outcome(state)["action"], False
            technical_event = self._technical_remediation_event(state)
            if technical_event is not None:
                # Either bounded local route may preflight one separately claimed,
                # independent quality review; neither grants a general delegation.
                if (
                    role == "reviewer"
                    and technical_event["role"] in {"implementer", "reviewer"}
                    and technical_event["route_id"] is not None
                    and technical_event["route_consumed_at"] is not None
                ):
                    return state["roles"].get(role, {}).get("minimum_tier"), False
                return "technical_remediation_pending", False
            role_state = state["roles"].get(role, {})
            return role_state.get("minimum_tier"), False

        state, result = self._locked(issue_id, mutate)
        if result == "technical_blocked":
            reason_code = self._terminal_outcome(state)["reason_code"]
            raise EscalationTechnicalBlockedError(
                f"{issue_id} est bloquée techniquement ({reason_code}) ; "
                "diagnostic local borné requis."
            )
        if result == "technical_remediation_pending":
            raise EscalationTechnicalBlockedError(
                f"{issue_id} attend une route de remédiation technique explicitement "
                "demandée ; nouvelle délégation ordinaire refusée."
            )
        if result == "authority_ambiguous":
            raise EscalationAuthorityAmbiguousError(
                f"{issue_id} possède un arrêt dont l'autorité est ambiguë ; "
                "nouvelle délégation refusée."
            )
        if result == "human_required":
            raise EscalationHumanRequiredError(
                f"{issue_id} attend une décision humaine valide. "
                "Intervention humaine requise."
            )
        return result

    def _guard_remediation_signal(
        self,
        state: dict,
        role: str,
        role_state: dict,
        current: str,
        signal: str,
        *,
        eligible: bool = False,
    ) -> tuple[EscalationDecision, bool] | None:
        """Stop every signal not authorized by the live remediation window."""
        remediation = self._remediation(state)
        if remediation["state"] == "invalid":
            self._halt(
                state,
                action="technical_blocked",
                category="technical_policy",
                reason_code="remediation_authorization_invalid",
                signal=signal,
                role=role,
            )
            changed = True
        elif state["halted"]:
            changed = False
        elif remediation["state"] == "active" and not eligible:
            self._halt(
                state,
                action="technical_blocked",
                category="technical_policy",
                reason_code="signal_outside_remediation_window",
                signal=signal,
                role=role,
            )
            changed = True
        elif remediation["state"] in {"cancelled", "exhausted", "invalidated"}:
            reason_code = (
                "remediation_window_exhausted"
                if remediation["state"] == "exhausted"
                and signal == "review_blocking_after_fix"
                else "remediation_window_inactive"
            )
            self._halt(
                state,
                action="technical_blocked",
                category=(
                    "deterministic_failure"
                    if reason_code == "remediation_window_exhausted"
                    else "technical_policy"
                ),
                reason_code=reason_code,
                signal=signal,
                role=role,
            )
            changed = True
        else:
            return None
        terminal = self._terminal_outcome(state)
        return self._decision(
            state, role, role_state, action=terminal["action"], initial_tier=current,
            final_tier=current, signal=signal, reason=state["halted_reason"],
        ), changed

    @classmethod
    def _decision(
        cls,
        state: dict,
        role: str,
        role_state: dict,
        *,
        action: str,
        initial_tier: str,
        final_tier: str,
        signal: str,
        reason: str,
    ) -> EscalationDecision:
        terminal = cls._terminal_outcome(state)
        terminal_action = action in {
            "human_required", "technical_blocked", "authority_ambiguous",
        }
        if terminal_action and action != terminal["action"]:
            raise RoutingConfigError("décision terminale interne incohérente.")
        return EscalationDecision(
            issue_id=state["issue_id"],
            role=role,
            action=action,
            initial_tier=initial_tier,
            final_tier=final_tier,
            issue_escalations=state["total_escalations"],
            role_escalations=role_state["escalations"],
            deterministic_failures=role_state["deterministic_failures"],
            failures_since_escalation=role_state["failures_since_escalation"],
            signal=signal,
            reason=reason,
            human_required=terminal_action and terminal["human_required"],
            authority_category=(terminal["category"] if terminal_action else None),
            technical_remediation=(
                cls._technical_remediation_view(state)
                if terminal_action and terminal["technical_blocked"] else None
            ),
        )

    def _halt(
        self,
        state: dict,
        *,
        action: str,
        category: str,
        reason_code: str,
        signal: str,
        role: str,
        diagnostic: dict | None = None,
        source_receipt: str | None = None,
    ) -> bool:
        if state["halted"]:
            return False
        authorization = state.get("remediation_authorization")
        # Never manipulate raw ledger values before strict validation: hostile or
        # malformed counters must be redacted/fail-closed, not raise here.
        if self._remediation(state)["state"] == "active":
            authorization["state"] = "invalidated"
            authorization["forfeited_credits"] = (
                authorization.get("forfeited_credits", 0) + authorization.get("remaining_credits", 0)
            )
            authorization["remaining_credits"] = 0
        previous = state.get("halt_generation", 0)
        if type(previous) is not int or previous < 0:
            raise RoutingConfigError(f"état d'escalade invalide pour {state['issue_id']}.")
        state["halt_generation"] = previous + 1
        reason = (
            _TECHNICAL_REASON_MESSAGES.get(reason_code)
            if action == "technical_blocked"
            else _HUMAN_REASON_MESSAGES.get(reason_code)
        )
        if reason is None:
            raise RoutingConfigError("raison terminale interne invalide.")
        state["halted"] = True
        state["halted_reason"] = reason
        state["halted_role"] = role
        state["terminal_outcome"] = {
            "action": action,
            "category": category,
            "reason_code": reason_code,
            "signal": signal,
            "role": role,
            "halt_generation": state["halt_generation"],
            "diagnostic": dict(diagnostic) if diagnostic is not None else None,
            "source_receipt": source_receipt,
        }
        _normalize_terminal_outcome(
            state["terminal_outcome"],
            state["issue_id"],
            halted=True,
            halted_reason=reason,
            halted_role=role,
            halt_generation=state["halt_generation"],
        )
        return True

    def _escalate(
        self,
        state: dict,
        role: str,
        role_state: dict,
        current_tier: str,
        target_tier: str,
        signal: str,
        reason: str,
    ) -> EscalationDecision:
        current = _higher(current_tier, role_state["minimum_tier"] or current_tier)
        target = _higher(current, target_tier)
        if target == current:
            return self._decision(
                state, role, role_state,
                action="satisfied", initial_tier=current, final_tier=current,
                signal=signal, reason=f"{reason} Le plancher {current} est déjà satisfait.",
            )
        if state["total_escalations"] >= MAX_ESCALATIONS_PER_ISSUE:
            self._halt(
                state,
                action="technical_blocked",
                category=(
                    "deterministic_failure"
                    if signal in FAILURE_KINDS else "technical_policy"
                ),
                reason_code="escalation_ceiling_reached",
                signal=signal,
                role=role,
            )
            return self._decision(
                state, role, role_state,
                action="technical_blocked", initial_tier=current, final_tier=current,
                signal=signal, reason=state["halted_reason"],
            )
        role_state["minimum_tier"] = target
        role_state["escalations"] += 1
        role_state["failures_since_escalation"] = 0
        state["total_escalations"] += 1
        return self._decision(
            state, role, role_state,
            action="escalated", initial_tier=current, final_tier=target,
            signal=signal, reason=reason,
        )

    def record_failure(
        self,
        issue_id: str,
        role: str,
        kind: str,
        current_tier: str,
        *,
        idempotency_key: str | None = None,
        authorization_aware: bool = False,
    ) -> EscalationDecision:
        issue_id = _validate_issue_id(issue_id)
        role = _validate_role(role)
        current_tier = _validate_tier(current_tier)
        if kind not in FAILURE_KINDS:
            raise RoutingConfigError(
                f"échec non déterministe '{kind}' refusé ; attendu : "
                f"{', '.join(FAILURE_KINDS)}."
            )
        if (idempotency_key is not None
                and (not isinstance(idempotency_key, str)
                     or _FAILURE_IDEMPOTENCY.fullmatch(idempotency_key) is None)):
            raise RoutingConfigError("identité idempotente d'échec invalide.")
        if type(authorization_aware) is not bool:
            raise RoutingConfigError("portée idempotente d'échec invalide.")

        def mutate(state):
            receipt_key = idempotency_key
            if authorization_aware and receipt_key is not None:
                remediation = self._remediation(state)
                raw = state.get("remediation_authorization")
                if isinstance(raw, dict):
                    window = {
                        "role": remediation.get("role"),
                        "halt_generation": remediation.get("halt_generation"),
                        "armed_at": remediation.get("armed_at"),
                        "maximum_credits": remediation.get("maximum_credits"),
                    }
                    suffix = hashlib.sha256(
                        json.dumps(
                            window, sort_keys=True, separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()[:16]
                    state_scope = (
                        "window" if remediation["state"] in {"active", "exhausted"}
                        else f"window-{remediation['state']}"
                    )
                    receipt_key = f"{receipt_key}:{state_scope}:{suffix}"
            if receipt_key is not None:
                prior = state["failure_receipts"].get(receipt_key)
                if prior is not None:
                    if (prior["role"] != role or prior["kind"] != kind
                            or prior["current_tier"] != current_tier):
                        raise RoutingConfigError(
                            "identité idempotente d'échec réutilisée avec un autre signal."
                        )
                    return EscalationDecision(**prior["decision"]), False

            def remembered(decision: EscalationDecision, changed: bool):
                if receipt_key is None:
                    return decision, changed
                if len(state["failure_receipts"]) >= MAX_FAILURE_RECEIPTS:
                    raise RoutingConfigError("ledger idempotent d'échecs hors borne.")
                state["failure_receipts"][receipt_key] = {
                    "role": role,
                    "kind": kind,
                    "current_tier": current_tier,
                    "decision": decision.to_dict(),
                }
                return decision, True

            role_state = self._role_state_view(state, role)
            current = _higher(current_tier, role_state["minimum_tier"] or current_tier)
            remediation = self._remediation(state)
            eligible = (
                remediation["state"] == "active" and
                role == remediation["role"] and
                kind == "review_blocking_after_fix"
            )
            blocked = self._guard_remediation_signal(
                state, role, role_state, current, kind, eligible=eligible,
            )
            if blocked is not None:
                return remembered(*blocked)
            role_state = self._role_state(state, role)
            if eligible:
                raw = state["remediation_authorization"]
                if raw["remaining_credits"] <= 0:
                    raw["state"] = "exhausted"
                    self._halt(
                        state,
                        action="technical_blocked",
                        category="deterministic_failure",
                        reason_code="remediation_window_exhausted",
                        signal=kind,
                        role=role,
                    )
                    return remembered(self._decision(
                        state, role, role_state, action="technical_blocked",
                        initial_tier=current,
                        final_tier=current, signal=kind, reason=state["halted_reason"],
                    ), True)
                raw["remaining_credits"] -= 1
                raw["consumed_credits"] += 1
                event = {
                    "code": _REMEDIATION_CONSUMPTION_CODE,
                    "at": self._next_audit_timestamp(state),
                    "role": role,
                    "halt_generation": raw["halt_generation"],
                }
                raw["consumption_audit"].append(dict(event))
                state["consumption_audit"].append(dict(event))
                if raw["remaining_credits"] == 0:
                    raw["state"] = "exhausted"
                role_state["deterministic_failures"] += 1
                role_state["failures_since_escalation"] += 1
                decision = self._decision(
                    state, role, role_state, action="remediation_continued",
                    initial_tier=current, final_tier=current, signal=kind,
                    reason="Crédit de remédiation consommé après review bloquante.",
                )
                # The public authorization is made from this exact locked mutation,
                # so a skill never needs a racy follow-up status read.
                return remembered(replace(
                    decision, remediation_authorization=self._remediation(state),
                ), True)
            role_state["deterministic_failures"] += 1
            role_state["failures_since_escalation"] += 1
            immediate = kind == "review_blocking_after_fix"
            if role_state["failures_since_escalation"] < 2 and not immediate:
                decision = self._decision(
                    state, role, role_state,
                    action="failure_recorded", initial_tier=current, final_tier=current,
                    signal=kind,
                    reason="Échec déterministe enregistré ; seuil de deux non atteint.",
                )
                return remembered(decision, True)
            index = LEVELS.index(current)
            if index == len(LEVELS) - 1:
                self._halt(
                    state,
                    action="technical_blocked",
                    category="deterministic_failure",
                    reason_code="apex_deterministic_failure",
                    signal=kind,
                    role=role,
                )
                decision = self._decision(
                    state, role, role_state,
                    action="technical_blocked", initial_tier=current, final_tier=current,
                    signal=kind, reason=state["halted_reason"],
                )
                return remembered(decision, True)
            reason = (
                "Correction de review à nouveau bloquée."
                if immediate else "Deux échecs déterministes atteints pour ce rôle."
            )
            return remembered(self._escalate(
                state, role, role_state, current, LEVELS[index + 1], kind, reason,
            ), True)

        _, decision = self._locked(issue_id, mutate)
        return decision

    def record_risk(
        self,
        issue_id: str,
        role: str,
        kind: str,
        current_tier: str,
    ) -> EscalationDecision:
        issue_id = _validate_issue_id(issue_id)
        role = _validate_role(role)
        current_tier = _validate_tier(current_tier)
        if kind not in RISK_FLOORS:
            raise RoutingConfigError(
                f"risque d'escalade '{kind}' inconnu ; attendu : {', '.join(RISK_FLOORS)}."
            )

        def mutate(state):
            role_state = self._role_state_view(state, role)
            current = _higher(current_tier, role_state["minimum_tier"] or current_tier)
            blocked = self._guard_remediation_signal(
                state, role, role_state, current, kind,
            )
            if blocked is not None:
                return blocked
            role_state = self._role_state(state, role)
            risk_floor = RISK_FLOORS[kind]
            if LEVELS.index(current) >= LEVELS.index(risk_floor):
                previous_floor = role_state["minimum_tier"]
                if (previous_floor is None or
                        LEVELS.index(previous_floor) < LEVELS.index(risk_floor)):
                    role_state["minimum_tier"] = risk_floor
                return self._decision(
                    state, role, role_state,
                    action="satisfied", initial_tier=current, final_tier=current,
                    signal=kind,
                    reason=(f"Le risque explicite {kind} impose au moins {risk_floor}. "
                            f"Le plancher {current} est déjà satisfait."),
                ), role_state["minimum_tier"] != previous_floor
            decision = self._escalate(
                state, role, role_state, current, risk_floor, kind,
                f"Le risque explicite {kind} impose au moins {risk_floor}.",
            )
            return decision, decision.action in {
                "escalated", "technical_blocked", "human_required",
            }

        _, decision = self._locked(issue_id, mutate)
        return decision

    def record_explicit_request(
        self,
        issue_id: str,
        role: str,
        current_tier: str,
        *,
        category: str = "model_tier",
        diagnostic: BoundedDiagnostic | dict | None = None,
    ) -> EscalationDecision:
        """Persist a typed model, strategy, product, or diagnostic request."""
        issue_id = _validate_issue_id(issue_id)
        role = _validate_role(role)
        current_tier = _validate_tier(current_tier)
        category = _validate_request_category(category)
        if category == "durable_ambiguity":
            diagnostic_payload = _normalize_bounded_diagnostic(diagnostic)
        elif diagnostic is not None:
            raise RoutingConfigError(
                "diagnostic accepté uniquement pour une ambiguïté durable."
            )
        else:
            diagnostic_payload = None

        def mutate(state):
            role_state = self._role_state_view(state, role)
            current = _higher(current_tier, role_state["minimum_tier"] or current_tier)
            if category in _HUMAN_AUTHORITY_CATEGORIES:
                if state["halted"]:
                    terminal = self._terminal_outcome(state)
                    if terminal["action"] in {
                        "technical_blocked", "authority_ambiguous",
                    }:
                        reason_code = {
                            "strategy_decision": "strategy_decision_requested",
                            "product_decision": "product_decision_requested",
                            "durable_ambiguity": "durable_ambiguity_attested",
                        }[category]
                        state["halted_reason"] = _HUMAN_REASON_MESSAGES[reason_code]
                        state["halted_role"] = role
                        state["terminal_outcome"] = {
                            "action": "human_required",
                            "category": category,
                            "reason_code": reason_code,
                            "signal": category,
                            "role": role,
                            "halt_generation": state["halt_generation"],
                            "diagnostic": diagnostic_payload,
                            "source_receipt": None,
                        }
                        _normalize_terminal_outcome(
                            state["terminal_outcome"], issue_id, halted=True,
                            halted_reason=state["halted_reason"],
                            halted_role=role,
                            halt_generation=state["halt_generation"],
                        )
                        return self._decision(
                            state, role, role_state,
                            action="human_required", initial_tier=current,
                            final_tier=current, signal=category,
                            reason=state["halted_reason"],
                        ), True
                    return self._decision(
                        state, role, role_state,
                        action=terminal["action"], initial_tier=current,
                        final_tier=current, signal=category,
                        reason=state["halted_reason"],
                    ), False
                reason_code = {
                    "strategy_decision": "strategy_decision_requested",
                    "product_decision": "product_decision_requested",
                    "durable_ambiguity": "durable_ambiguity_attested",
                }[category]
                self._halt(
                    state,
                    action="human_required",
                    category=category,
                    reason_code=reason_code,
                    signal=category,
                    role=role,
                    diagnostic=diagnostic_payload,
                )
                return self._decision(
                    state, role, role_state,
                    action="human_required", initial_tier=current,
                    final_tier=current, signal=category,
                    reason=state["halted_reason"],
                ), True
            blocked = self._guard_remediation_signal(
                state, role, role_state, current, "explicit_user_request",
            )
            if blocked is not None:
                return blocked
            role_state = self._role_state(state, role)
            index = LEVELS.index(current)
            if index == len(LEVELS) - 1:
                self._halt(
                    state,
                    action="technical_blocked",
                    category="technical_policy",
                    reason_code="routing_apex_reached",
                    signal="explicit_user_request",
                    role=role,
                )
                return self._decision(
                    state, role, role_state,
                    action="technical_blocked", initial_tier=current,
                    final_tier=current,
                    signal="explicit_user_request", reason=state["halted_reason"],
                ), True
            decision = self._escalate(
                state, role, role_state, current, LEVELS[index + 1],
                "explicit_user_request", "L'utilisateur a demandé une escalade explicite.",
            )
            return decision, decision.action in {"escalated", "technical_blocked"}

        _, decision = self._locked(issue_id, mutate)
        return decision

    def record_human_verdict(
        self,
        issue_id: str,
        role: str,
        current_tier: str,
        *,
        category: str,
        diagnostic: BoundedDiagnostic | dict | None = None,
    ) -> EscalationDecision:
        """Record only an explicitly valid human-authority category."""
        if category not in _HUMAN_AUTHORITY_CATEGORIES:
            raise RoutingConfigError("catégorie de verdict humain invalide.")
        return self.record_explicit_request(
            issue_id,
            role,
            current_tier,
            category=category,
            diagnostic=diagnostic,
        )
