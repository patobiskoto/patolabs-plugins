"""Thin host detectors feeding the shared routing decision.

These adapters deliberately return signal *names*, never values.  Invocation mechanics
belong to FOUNDRY-7/8; the decision that an active signal neutralizes policy remains in
``routing.host_override_warnings``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, replace
from typing import Callable, Mapping

from foundry.routing import (
    ReviewDeduplicator,
    RoutingConfigError,
    RoutingPolicy,
    UserRouteRequest,
    repository_identity,
    review_diff_hash,
    review_diff_coordinates,
)
from foundry.escalation import EscalationStore, MAX_PARALLEL_AGENTS
from foundry.telemetry import TelemetryObserver, TelemetryRun, unknown_metric


CLAUDE_MODEL_OVERRIDE = "CLAUDE_CODE_SUBAGENT_MODEL"
CLAUDE_EFFORT_OVERRIDE = "CLAUDE_CODE_EFFORT_LEVEL"
CLAUDE_ALIAS_MODEL_OVERRIDES = (
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
)
CODEX_MODEL_OVERRIDE = "profile.model"
CODEX_EFFORT_OVERRIDE = "profile.model_reasoning_effort"
CLAUDE_AVAILABLE_MODELS = "FOUNDRY_CLAUDE_AVAILABLE_MODELS"
CODEX_AVAILABLE_MODELS = "FOUNDRY_CODEX_AVAILABLE_MODELS"
CLAUDE_USER_REQUEST_PREFIX = "FOUNDRY_ROUTE_REQUEST="
# Claude routes name installed AgentDefinition profiles. Codex effort scopes
# stay open, but a Claude policy must be executable by the hook.
CLAUDE_EXECUTABLE_EFFORTS = frozenset(("low", "medium", "high", "xhigh", "max"))

# Semantic roles are stable policy, escalation, and permission keys.  These are the
# user-facing/logical identities used by the two host façades instead.
AGENT_IDENTITIES = {
    "architect": "Vauban",
    "implementer": "Eiffel",
    "reviewer": "Maigret",
    "scout": "Lupin",
}


def agent_identity(role: str) -> str:
    """Return the display identity for a stable semantic Foundry role."""
    try:
        return AGENT_IDENTITIES[role]
    except KeyError as exc:
        raise RoutingConfigError(f"rôle Foundry inconnu : {role}.") from exc

# Host translation is a declaration, not a chain of model-specific code paths.
_CLAUDE_MODEL_DECLARATION = (
    ("haiku-4.5", "haiku", ("claude-haiku-4-5",)),
    ("sonnet-5", "sonnet", ("claude-sonnet-5",)),
    ("opus-5", "opus", ("claude-opus-5",)),
    ("fable-5", "fable", ("claude-fable-5",)),
)
_CLAUDE_MODEL_IDS = {canonical: alias for canonical, alias, _ in _CLAUDE_MODEL_DECLARATION}
_CLAUDE_POLICY_MODELS = {
    **{alias: canonical for canonical, alias, _ in _CLAUDE_MODEL_DECLARATION},
    **{spelling: canonical for canonical, _, spellings in _CLAUDE_MODEL_DECLARATION for spelling in spellings},
}


@dataclass(frozen=True)
class CodexRole:
    """Codex invocation mechanics; model and effort always come from the policy."""

    agent_type: str
    max_prompt_chars: int
    contract: str


CODEX_ROLES = {
    "scout": CodexRole(
        "explorer",
        4_000,
        "Explore the requested codebase question read-only. Return concise findings "
        "with file and line evidence. Do not edit files or delegate.",
    ),
    "implementer": CodexRole(
        "worker",
        12_000,
        "Own the bounded implementation scope in the packet. You are not alone in the "
        "codebase: preserve unrelated work, accommodate concurrent changes, and do not "
        "revert others' edits. Do not delegate.",
    ),
    "reviewer": CodexRole(
        "reviewer",
        16_000,
        "Run the Foundry two-stage review strictly read-only from this blank context. "
        "Read the diff only through the claimed-hash verifier. Do not edit or delegate.",
    ),
    "architect": CodexRole(
        "default",
        10_000,
        "Resolve only the durable architecture decision in the packet. Work read-only, "
        "honor accepted ADRs, compare trade-offs, and do not edit or delegate.",
    ),
}

_PACKET_HEADINGS = ("Goal:", "Inputs:", "Constraints:", "Done when:")
_CODEX_ROUTED_MARKER = "FOUNDRY_CODEX_ROUTED_AGENT_V1"
_TASK_NAME = re.compile(r"[a-z0-9_]+\Z")
_EXECUTION_ID = re.compile(r"[0-9a-f]{16}\Z")
_MAX_CODEX_TASK_NAME_LENGTH = 64


def _codex_execution_name(role: str, requested_name: str | None) -> tuple[str, str]:
    """Return a bounded host instance name without changing the logical role.

    Codex task paths are process-global for a long-running task. A stable name such
    as ``foundry_eiffel`` can therefore collide with a completed or concurrent
    delegation. The random execution suffix is intentionally opaque: it is an
    invocation identifier, not user/task content, and keeps the display identity in
    the stable prefix.
    """
    prefix = requested_name or f"foundry_{agent_identity(role).lower()}"
    if not isinstance(prefix, str) or not _TASK_NAME.fullmatch(prefix):
        raise RoutingConfigError(
            "task_name Codex invalide : lettres minuscules, chiffres et underscores requis."
        )
    execution_id = secrets.token_hex(8)
    task_name = f"{prefix}_{execution_id}"
    if len(task_name) > _MAX_CODEX_TASK_NAME_LENGTH:
        raise RoutingConfigError(
            "task_name Codex trop long : le préfixe et son identifiant d'exécution "
            f"doivent tenir en {_MAX_CODEX_TASK_NAME_LENGTH} caractères."
        )
    # Keep this explicit even though token_hex currently guarantees it: this is the
    # host-facing boundary and protects a future generator change.
    if not _EXECUTION_ID.fullmatch(execution_id):
        raise RoutingConfigError("identifiant d'exécution Codex invalide.")
    return task_name, execution_id


def _telemetry_resolution_source(sources: Mapping[str, str]) -> str:
    """Reduce routing-internal source detail to the telemetry vocabulary."""
    values = set(sources.values())
    if any(value.startswith("escalation:") for value in values):
        return "escalation"
    allowed = {"default", "project", "user", "fallback"}
    values &= allowed
    return next(iter(values)) if len(values) == 1 else "mixed"


def observe_invocation_completion(
    observer: TelemetryObserver,
    route,
    *,
    status: str,
    failure_class: str,
    kind: str = "delegated",
    scope: str = "bounded_packet",
    context_policy: str = "fresh",
) -> TelemetryRun | None:
    """Observe an already-completed routed invocation without touching routing.

    This is the sole shared completion boundary used by both host adapters.  The
    route was resolved before invocation; this function only copies its structured,
    already-known fields and writes no state except optional telemetry.
    """
    if not isinstance(observer, TelemetryObserver):
        return None
    return observer.invocation_completed(**_completion_fields(
        route,
        kind=kind,
        scope=scope,
        context_policy=context_policy,
        status=status,
        failure_class=failure_class,
    ))


def run_claude_measurement(harness, request, **kwargs):
    """Explicit F43 Claude adapter; normal routing and telemetry never call it."""
    from foundry.measurement_harness import run_claude

    return run_claude(harness, request, **kwargs)


def run_codex_measurement(harness, request, **kwargs):
    """Explicit F43 Codex adapter; normal routing and telemetry never call it."""
    from foundry.measurement_harness import run_codex

    return run_codex(harness, request, **kwargs)


def _completion_fields(
    route,
    *,
    kind: str = "delegated",
    scope: str = "bounded_packet",
    context_policy: str = "fresh",
    status: str = "unknown",
    failure_class: str = "unknown",
) -> dict:
    """Copy only the closed telemetry projection of an already-resolved route."""
    return {
        "host": route.host,
        "kind": kind,
        "role": route.role,
        "tier": route.selected_tier,
        "model": route.model,
        "effort": route.effort,
        "scope": scope,
        "context_policy": context_policy,
        "resolution_source": _telemetry_resolution_source(route.sources),
        "signals": {
            "availability_probed": route.availability_probed,
            "fallback_steps": {
                "value": max(0, len(route.fallback_path) - 1), "provenance": "observed",
            },
            "host_override_count": {
                "value": sum(
                    warning.code == "HOST_OVERRIDE_NEUTRALIZES_POLICY"
                    for warning in route.warnings
                ),
                "provenance": "observed",
            },
            "escalation_floor_active": route.minimum_tier is not None,
        },
        "usage": {"input_tokens": unknown_metric(), "output_tokens": unknown_metric(),
                  "total_tokens": unknown_metric()},
        "duration_ms": unknown_metric(),
        "packet_bytes": unknown_metric(),
        "file_count": unknown_metric(),
        "estimated_cost": None,
        "provider_reported_cost": None,
        "status": status,
        "failure_class": failure_class,
    }


def prepare_claude_invocation(
    observer: TelemetryObserver, route, correlation: str,
) -> bool:
    """Register private route state for Claude's real post-tool callback."""
    if not isinstance(observer, TelemetryObserver) or getattr(route, "host", None) != "claude":
        return False
    try:
        return observer.prepare_correlated_invocation(
            correlation, **_completion_fields(route),
        )
    except Exception:
        return False


def _prepare_codex_invocation(
    route, *, mode: str, effort_scopes, project_models,
) -> dict | None:
    # A current-context fallback has no distinct host-return boundary. Recording it
    # from the planning command would call an in-flight main loop "completed".
    if mode != "subagent":
        return None
    try:
        observer = TelemetryObserver.from_environ(
            effort_scopes=effort_scopes, project_models=project_models,
        )
        capability = observer.prepare_invocation(**_completion_fields(
            route,
            kind="delegated",
            scope="bounded_packet",
            context_policy="fresh",
        ))
    except Exception:
        return None
    if capability is None:
        return None
    return {"completion_capability": capability}


def claude_invocation_completed(
    observer: TelemetryObserver, route=None, *, correlation: str | None = None,
    **completion: object,
) -> TelemetryRun | bool | None:
    """Claude completion adapter used by the real post-tool callback."""
    if "status" not in completion or "failure_class" not in completion:
        return False if correlation is not None else None
    if correlation is not None:
        return observer.complete_correlated_invocation(correlation, **completion)
    if getattr(route, "host", None) != "claude":
        return None
    return observe_invocation_completion(observer, route, **completion)


def codex_invocation_completed(
    observer: TelemetryObserver, route_or_capability, **completion: object,
) -> TelemetryRun | None:
    """Codex completion adapter used by the required post-spawn CLI command."""
    if "status" not in completion or "failure_class" not in completion:
        return None
    if isinstance(route_or_capability, str):
        return observer.complete_invocation(
            route_or_capability, expected_host="codex", **completion,
        )
    route = route_or_capability
    if getattr(route, "host", None) != "codex":
        return None
    return observe_invocation_completion(observer, route, **completion)


def detect_claude_overrides(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Detect Claude Code overrides that outrank invocation/frontmatter policy."""
    environ = os.environ if environ is None else environ
    signals = (
        CLAUDE_MODEL_OVERRIDE,
        CLAUDE_EFFORT_OVERRIDE,
        *CLAUDE_ALIAS_MODEL_OVERRIDES,
    )
    return tuple(signal for signal in signals if environ.get(signal))


def detect_codex_overrides(profile: Mapping[str, object] | None) -> tuple[str, ...]:
    """Detect effective Codex profile keys supplied by the Codex invocation façade."""
    if profile is None:
        return ()
    if not isinstance(profile, Mapping):
        raise RoutingConfigError("profil Codex résolu : objet attendu.")
    active = []
    if profile.get("model") is not None:
        active.append(CODEX_MODEL_OVERRIDE)
    if profile.get("model_reasoning_effort") is not None:
        active.append(CODEX_EFFORT_OVERRIDE)
    return tuple(active)


def codex_available_models(environ: Mapping[str, str] | None = None) -> set[str] | None:
    """Optional explicit Codex availability observation for deterministic fallback."""
    environ = os.environ if environ is None else environ
    raw = environ.get(CODEX_AVAILABLE_MODELS)
    if raw is None:
        return None
    values = {value.strip() for value in raw.split(",") if value.strip()}
    if not values:
        raise RoutingConfigError(
            f"{CODEX_AVAILABLE_MODELS}: liste CSV non vide de noms de modèles attendue."
        )
    return values


def _validate_task_packet(packet: str, role: str, host: str) -> str:
    if not isinstance(packet, str) or not packet.strip():
        raise RoutingConfigError(f"task packet {host} non vide attendu.")
    profile = CODEX_ROLES[role]
    if len(packet) > profile.max_prompt_chars:
        raise RoutingConfigError(
            f"task packet {host} de {role} trop long : {len(packet)} caractères ; "
            f"maximum {profile.max_prompt_chars}. Fournissez des chemins/références "
            "plutôt que l'historique complet."
        )
    missing = [heading for heading in _PACKET_HEADINGS if heading not in packet]
    if missing:
        raise RoutingConfigError(
            f"task packet {host} de {role} incomplet ; sections requises : "
            f"{', '.join(_PACKET_HEADINGS)} (manquantes : {', '.join(missing)})."
        )
    return packet.strip()


def _validate_codex_task_packet(packet: str, role: str) -> str:
    return _validate_task_packet(packet, role, "Codex")


def _codex_message(
    role: str,
    packet: str,
    diff_hash: str | None,
    claim_id: str | None,
    verifier: Mapping[str, str] | None,
) -> str:
    claim = "\n"
    if diff_hash:
        claim = (
            f"\nClaimed review diff hash: {diff_hash}\n"
            f"Active review claim id: {claim_id}\n"
            "Review verifier coordinates (pass root as --root and base as --base to "
            "read-review): "
            f"{json.dumps(dict(verifier or {}), ensure_ascii=False)}\n"
        )
    return (
        f"{_CODEX_ROUTED_MARKER}\n"
        f"Agent identity: {agent_identity(role)}\n"
        f"Resolved role: {role}\n"
        f"Role contract: {CODEX_ROLES[role].contract}"
        f"{claim}"
        "The bounded task packet follows; do not infer missing work from parent history.\n\n"
        f"{packet}"
    )


def _technical_remediation_local_instructions(
    role: str,
    packet: str,
) -> str:
    """Describe the only work allowed by a released local diagnostic receipt."""
    return (
        f"{_CODEX_ROUTED_MARKER}\n"
        f"Resolved role: {role}\n"
        "Technical-remediation contract: execute only local diagnosis, source edits, and "
        "local validation for this exact issue. Do not invoke a provider, a campaign, a "
        "tracker, a code host, a PR, a merge, or any external resource. This receipt "
        "does not grant capacity or authority. Any review requires its own fresh Foundry "
        "review claim and, for a campaign, fresh capacity plus campaign revalidation.\n\n"
        f"{packet}"
    )


def _review_claim(value: Mapping[str, object] | None) -> tuple[
    str, bool, str, str | None, dict[str, str],
]:
    if not isinstance(value, Mapping):
        raise RoutingConfigError(
            "review Codex : verdict commun requis avant délégation "
            "({diff_hash, should_run})."
        )
    diff_hash = value.get("diff_hash")
    should_run = value.get("should_run")
    if (not isinstance(diff_hash, str) or len(diff_hash) != 64 or
            any(character not in "0123456789abcdef" for character in diff_hash)):
        raise RoutingConfigError("review Codex : diff_hash SHA-256 invalide.")
    if type(should_run) is not bool:
        raise RoutingConfigError("review Codex : should_run booléen requis.")
    state = value.get("state")
    if state not in {"in_progress", "completed"}:
        raise RoutingConfigError("review Codex : state in_progress/completed requis.")
    generation = value.get("generation")
    if type(generation) is not int or generation < 0:
        raise RoutingConfigError("review Codex : génération entière positive requise.")
    claim_id = value.get("claim_id")
    if should_run:
        if (not isinstance(claim_id, str) or len(claim_id) != 64 or
                any(character not in "0123456789abcdef" for character in claim_id)):
            raise RoutingConfigError("review Codex : claim_id actif invalide.")
    elif claim_id is not None:
        raise RoutingConfigError("review Codex : une déduplication ne divulgue pas claim_id.")
    root = value.get("root")
    base = value.get("base")
    if not isinstance(root, str) or not root:
        raise RoutingConfigError("review Codex : root du vérificateur requis.")
    if not isinstance(base, str) or not base or base.startswith("-"):
        raise RoutingConfigError("review Codex : base du vérificateur invalide.")
    return diff_hash, should_run, state, claim_id, {"root": root, "base": base}


def codex_spawn_plan(
    role: str,
    task_packet: str,
    *,
    root: str | os.PathLike | None = None,
    user: UserRouteRequest | None = None,
    available_models: set[str] | None = None,
    effective_profile: Mapping[str, object] | None = None,
    task_name: str | None = None,
    subagent_available: bool = True,
    review_claim: Mapping[str, object] | None = None,
    issue_id: str | None = None,
    escalation_state_dir: str | os.PathLike | None = None,
    technical_remediation: bool = False,
    technical_remediation_id: str | None = None,
    _observe: bool = True,
) -> dict:
    """Build the exact, explicit Codex subagent invocation without spawning it.

    The skill remains the host façade that executes ``spawn``. Keeping this function
    pure makes precedence, context isolation, review deduplication, and redaction
    deterministic and testable without a live model call.
    """
    if role not in CODEX_ROLES:
        raise RoutingConfigError(
            f"rôle Codex '{role}' inconnu ; attendu : {', '.join(CODEX_ROLES)}."
        )
    if type(technical_remediation) is not bool:
        raise RoutingConfigError("portée de remédiation technique Codex invalide.")
    if technical_remediation != (technical_remediation_id is not None):
        raise RoutingConfigError(
            "portée et identité de remédiation technique Codex requises ensemble."
        )
    if technical_remediation and issue_id is None:
        raise RoutingConfigError(
            "remédiation technique Codex : issue auditée requise."
        )
    diff_hash = None
    claim_id = None
    verifier = None
    claim_payload = None
    plan_remediation = {
        "state": "none", "maximum_credits": 0, "remaining_credits": 0,
        "role": None, "halt_generation": None,
    }
    plan_rearm_audit = []
    technical_remediation_open = False
    if issue_id is not None:
        escalation_status = EscalationStore.for_root(
            root, state_dir=escalation_state_dir,
        ).status(issue_id)
        plan_remediation = escalation_status["remediation_authorization"]
        plan_rearm_audit = escalation_status["remediation_rearm_audit"]
        technical_remediation_open = escalation_status["technical_remediation_open"]
    if role == "reviewer" and not technical_remediation:
        diff_hash, should_run, claim_state, claim_id, verifier = _review_claim(review_claim)
        claim_payload = {
            "diff_hash": diff_hash,
            "should_run": should_run,
            "state": claim_state,
            "generation": review_claim["generation"],
            **verifier,
        }
        if claim_id is not None:
            claim_payload["claim_id"] = claim_id
        if not should_run:
            return {
                "schema_version": 1,
                "host": "codex",
                "role": role,
                "mode": "deduplicated",
                "independent_context": False,
                "spawn": None,
                "review_claim": claim_payload,
                "escalation": {
                    "issue_id": issue_id,
                    "minimum_tier": None,
                    "max_parallel_agents": MAX_PARALLEL_AGENTS,
                    "remediation_authorization": plan_remediation,
                    "remediation_rearm_audit": plan_rearm_audit,
                },
                "disclosure": (
                    "Review non relancée : "
                    + (
                        "ce hash de diff possède déjà un reviewer. Réutilisez son verdict."
                        if claim_state == "in_progress" else
                        "ce hash de diff possède une review terminée et reste dédupliqué."
                    )
                ),
            }
    elif review_claim is not None:
        raise RoutingConfigError("review_claim n'est accepté que pour le rôle reviewer.")

    packet = _validate_codex_task_packet(task_packet, role)
    escalation_floor = None
    remediation = plan_remediation
    if issue_id is not None:
        escalation_store = EscalationStore.for_root(root, state_dir=escalation_state_dir)
        escalation_floor = escalation_store.active_floor(
            issue_id,
            role,
            technical_remediation=technical_remediation,
            technical_remediation_id=technical_remediation_id,
        )
    policy = RoutingPolicy.load(root)
    route = policy.resolve(
        role,
        "codex",
        user=user,
        available_models=available_models,
        active_host_overrides=detect_codex_overrides(effective_profile),
        minimum_tier=escalation_floor,
        minimum_source=f"escalation:{issue_id}" if escalation_floor else None,
    )
    if technical_remediation:
        return {
            "schema_version": 1,
            "host": "codex",
            "role": role,
            "mode": "local_diagnostic",
            "independent_context": False,
            "spawn": None,
            "local_instructions": _technical_remediation_local_instructions(
                role, packet,
            ),
            "route": route.to_dict(),
            "review_claim": None,
            "escalation": {
                "issue_id": issue_id,
                "minimum_tier": escalation_floor,
                "max_parallel_agents": MAX_PARALLEL_AGENTS,
                "remediation_authorization": remediation,
                "remediation_rearm_audit": plan_rearm_audit,
                "technical_remediation_open": technical_remediation_open,
                "technical_remediation_requested": True,
                "technical_remediation_claimed": True,
                "provider_effect_allowed": False,
                "campaign_restart_allowed": False,
                "fresh_review_required": True,
            },
            "disclosure": (
                "Route locale seulement : aucun sous-agent/provider n'est lancé. "
                "Une review utilisera un claim et une capacité fraîche séparés."
            ),
        }
    task_name, execution_id = _codex_execution_name(role, task_name)
    message = _codex_message(role, packet, diff_hash, claim_id, verifier)
    spawn = {
        "task_name": task_name,
        "agent_type": CODEX_ROLES[role].agent_type,
        "fork_turns": "none",
        "message": message,
        "model": route.model,
        "reasoning_effort": route.effort,
    }
    base = {
        "schema_version": 1,
        "host": "codex",
        "role": role,
        "agent_identity": agent_identity(role),
        "execution_id": execution_id,
        "route": route.to_dict(),
        "review_claim": claim_payload,
        "escalation": {
            "issue_id": issue_id,
            "minimum_tier": escalation_floor,
            "max_parallel_agents": MAX_PARALLEL_AGENTS,
            "remediation_authorization": remediation,
            "remediation_rearm_audit": plan_rearm_audit,
            "technical_remediation_open": technical_remediation_open,
            "technical_remediation_requested": technical_remediation,
            **(
                {"technical_remediation_claimed": True}
                if technical_remediation else {}
            ),
        },
    }
    if subagent_available:
        result = {
            **base,
            "mode": "subagent",
            "independent_context": True,
            "spawn": spawn,
            "disclosure": None,
        }
    else:
        result = {
            **base,
            "mode": "current_context",
            "independent_context": False,
            "spawn": None,
            "current_context_instructions": message,
            "disclosure": (
                f"Sous-agent Codex indisponible : {role} s'exécute dans le contexte courant ; "
                "la politique modèle/effort ne peut pas y être appliquée et l'indépendance "
                "du contexte est perdue."
            ),
        }
    correction_plan_claimed = False
    if issue_id is not None and role != "reviewer":
        # A credited correction is a one-shot capability, not a read-only floor.
        # Claim it only after every deterministic plan field has been validated,
        # immediately before the plan can become a launchable response.
        correction_plan_claimed = escalation_store.claim_credited_correction_plan(
            issue_id, role, root=root,
        )
    if correction_plan_claimed:
        result["escalation"]["credited_correction_plan_claimed"] = True
    telemetry = _prepare_codex_invocation(
        route, mode=result["mode"], effort_scopes=policy.effort_scopes,
        project_models=(*policy.project_models, route.model),
    ) if _observe else None
    if telemetry is not None:
        result["telemetry"] = telemetry
    return result


def codex_review_claim(
    repository: str,
    *,
    root: str | os.PathLike,
    base: str,
    state_dir: str | os.PathLike | None = None,
    claim_attempt_token: str | None = None,
    replay_interrupted: bool = False,
) -> dict[str, object]:
    """Claim only the exact Git diff at explicit immutable coordinates."""
    if re.fullmatch(r"[0-9a-f]{40}", base) is None:
        raise RoutingConfigError(
            "review Codex : base SHA Git figé de 40 hexadécimaux requis avant toute claim."
        )
    review_root, review_base = review_diff_coordinates(root, base)
    verifier = {"root": str(review_root), "base": review_base}
    from foundry.routing import git_diff

    expected_hash = review_diff_hash(git_diff(review_root, review_base))
    return ReviewDeduplicator(repository, state_dir).claim_git(
        expected_hash,
        coordinates=verifier,
        claim_attempt_token=claim_attempt_token,
        replay_interrupted=replay_interrupted,
    ).to_dict()


def codex_review_plan(
    task_packet: str,
    *,
    root: str | os.PathLike | None = None,
    base: str | None = None,
    user: UserRouteRequest | None = None,
    available_models: set[str] | None = None,
    effective_profile: Mapping[str, object] | None = None,
    task_name: str | None = None,
    subagent_available: bool = True,
    state_dir: str | os.PathLike | None = None,
    issue_id: str | None = None,
    recovered_diff_hash: str | None = None,
    recovered_claim_id: str | None = None,
    claim_attempt_token: str | None = None,
    replay_interrupted: bool = False,
) -> dict:
    """Require and preflight the issue, then atomically claim its review diff."""
    if root is None:
        raise RoutingConfigError(
            "review Codex : root Git exact requis avant toute claim."
        )
    if not isinstance(base, str) or re.fullmatch(r"[0-9a-f]{40}", base) is None:
        raise RoutingConfigError(
            "review Codex : base SHA Git figé de 40 hexadécimaux requis avant toute claim."
        )
    review_root, review_base = review_diff_coordinates(root, base)
    EscalationStore.for_root(
        review_root, state_dir=state_dir,
    ).active_floor(issue_id, "reviewer")
    repository = repository_identity(review_root)
    verifier = {"root": str(review_root), "base": review_base}
    deduplicator = ReviewDeduplicator(repository, state_dir)
    from foundry.routing import git_diff

    expected_hash = review_diff_hash(git_diff(review_root, review_base))
    if (recovered_diff_hash is None) != (recovered_claim_id is None):
        raise RoutingConfigError(
            "review Codex récupérée : diff_hash et claim_id sont requis ensemble."
        )
    if recovered_diff_hash is not None and replay_interrupted:
        raise RoutingConfigError(
            "review Codex : récupération par capability et rejeu de publication "
            "interrompue sont mutuellement exclusifs."
        )
    if recovered_diff_hash is not None:
        if expected_hash != recovered_diff_hash:
            raise RoutingConfigError(
                f"le diff a changé depuis le claim : attendu {recovered_diff_hash}, "
                f"actuel {expected_hash}. Réservez le nouveau diff avant de relancer "
                "un reviewer."
            )
    preflight_claim = {
        "diff_hash": expected_hash, "should_run": True, "state": "in_progress",
        "generation": 1,
        "claim_id": recovered_claim_id or "0" * 64,
        **verifier,
    }
    if (
        recovered_diff_hash is not None
        or replay_interrupted
        or deduplicator.git_claim_requires_preflight(expected_hash)
    ):
        codex_spawn_plan(
            "reviewer",
            task_packet,
            root=review_root,
            user=user,
            available_models=available_models,
            effective_profile=effective_profile,
            task_name=task_name,
            subagent_available=subagent_available,
            review_claim=preflight_claim,
            issue_id=issue_id,
            escalation_state_dir=state_dir,
            _observe=False,
        )
    def validate_claim():
        if recovered_diff_hash is not None:
            return deduplicator.active_git_claim(
                recovered_diff_hash, recovered_claim_id, coordinates=verifier,
            )
        return deduplicator.claim_git(
            expected_hash,
            coordinates=verifier,
            claim_attempt_token=claim_attempt_token,
            replay_interrupted=replay_interrupted,
        )

    def validate_rearm(previous_diff_hash: str):
        binding = deduplicator.validated_terminal_proof_binding(
            issue_id, previous_diff_hash, coordinates=verifier,
        )
        if binding is None:
            return None
        return {
            "proof_id": binding["proof_id"],
            "completed_at": binding["completed_at"],
            "quality": binding["quality"],
            "all_pass": binding["all_pass"],
        }

    result = EscalationStore.for_root(
        review_root, state_dir=state_dir,
    ).claim_fresh_reviewer_authorization(
        issue_id,
        expected_hash,
        validated_claim=validate_claim,
        validated_rearm=validate_rearm,
    )
    claim = {**result.to_dict(), **verifier}
    return codex_spawn_plan(
        "reviewer",
        task_packet,
        root=review_root,
        user=user,
        available_models=available_models,
        effective_profile=effective_profile,
        task_name=task_name,
        subagent_available=subagent_available,
        review_claim=claim,
        issue_id=issue_id,
        escalation_state_dir=state_dir,
    )


def _project_claude_models(root: str | os.PathLike | None) -> dict[str, str]:
    return dict(RoutingPolicy.load(root).claude_models)


def claude_invocation_model(
    policy_model: str,
    *,
    root: str | os.PathLike | None = None,
    project_models: Mapping[str, str] | None = None,
) -> str:
    """Translate accepted Claude policy spellings to current Agent tool aliases."""
    if not isinstance(policy_model, str) or not policy_model.strip():
        raise RoutingConfigError("modèle Claude résolu : chaîne non vide attendue.")
    canonical = claude_policy_model(policy_model)
    if canonical in _CLAUDE_MODEL_IDS:
        return _CLAUDE_MODEL_IDS[canonical]
    project_models = (
        _project_claude_models(root) if project_models is None else project_models
    )
    if canonical in project_models:
        return project_models[canonical]
    raise RoutingConfigError(
        f"modèle Claude résolu '{canonical}' : traduction hôte inconnue (alias Agent attendu)."
    )


def claude_policy_model(invocation_model: str) -> str:
    """Normalize Claude aliases/full current IDs for common availability matching."""
    if not isinstance(invocation_model, str) or not invocation_model.strip():
        raise RoutingConfigError("modèle Claude demandé : chaîne non vide attendue.")
    value = invocation_model.strip()
    return _CLAUDE_POLICY_MODELS.get(value, value)


def _load_claude_policy(
    root: str | os.PathLike, *, policy_loader=None,
) -> RoutingPolicy:
    """Canonicalize Claude project targets before the shared resolver sees them."""
    policy = RoutingPolicy.load(root) if policy_loader is None else policy_loader(root)
    # Lightweight test/host policy doubles may only implement resolution. They
    # carry no project mapping, so there is no Claude declaration to validate.
    configured = getattr(policy, "mapping_overrides", {}).get("claude")
    declared_models = getattr(policy, "claude_models", None)
    if declared_models is None:
        return policy

    # The shipped canonical spellings have fixed Agent aliases.  Project data
    # may add future models, but cannot silently turn a reviewer/architect
    # route into a weaker built-in Agent model.
    for configured_model, alias in declared_models.items():
        canonical = claude_policy_model(configured_model)
        if canonical in _CLAUDE_MODEL_IDS and alias != _CLAUDE_MODEL_IDS[canonical]:
            raise RoutingConfigError(
                f"{policy.config_path}.claude_models.{configured_model}: alias Agent "
                f"intégré incompatible ; '{_CLAUDE_MODEL_IDS[canonical]}' obligatoire."
            )

    for family, scope in policy.effort_scopes.get("claude", {}).items():
        unavailable = [level for level in scope.levels if level not in CLAUDE_EXECUTABLE_EFFORTS]
        if unavailable:
            raise RoutingConfigError(
                f"{policy.config_path}.effort_scopes.claude.{family}: profils Agent "
                f"Claude absents pour {', '.join(unavailable)}."
            )

    if not configured:
        return policy

    normalized = {}
    for tier, target in configured.items():
        clean_target = dict(target)
        if "model" in clean_target:
            model = claude_policy_model(clean_target["model"])
            project_models = policy.claude_models
            if model not in _CLAUDE_MODEL_IDS and model not in project_models:
                where = f"{policy.config_path}.mappings.claude.{tier}.model"
                raise RoutingConfigError(
                    f"{where}: modèle '{model}' sans traduction hôte ; déclarez "
                    f"claude_models.{model} avec son alias Agent."
                )
            clean_target["model"] = model
        normalized[tier] = clean_target

    mappings = dict(policy.mapping_overrides)
    mappings["claude"] = normalized
    return replace(policy, mapping_overrides=mappings)


def claude_available_models(environ: Mapping[str, str] | None = None) -> set[str] | None:
    """Optional explicit availability observation supplied to the common fallback."""
    environ = os.environ if environ is None else environ
    raw = (
        environ.get(CLAUDE_AVAILABLE_MODELS)
        or environ.get(f"CLAUDE_PLUGIN_OPTION_{CLAUDE_AVAILABLE_MODELS}")
    )
    if raw is None:
        return None
    values = {claude_policy_model(value) for value in raw.split(",") if value.strip()}
    if not values:
        raise RoutingConfigError(
            f"{CLAUDE_AVAILABLE_MODELS}: liste CSV non vide de modèles Claude attendue."
        )
    return values


def claude_user_request(prompt: str, invocation_model: str | None = None) -> tuple[
    UserRouteRequest, str, str | None,
]:
    """Extract the façade's strict first-line request envelope from a task packet."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise RoutingConfigError("prompt d'agent Claude : task packet non vide attendu.")
    request = {}
    task_prompt = prompt
    first, separator, rest = prompt.partition("\n")
    if first.startswith(CLAUDE_USER_REQUEST_PREFIX):
        try:
            request = json.loads(first.removeprefix(CLAUDE_USER_REQUEST_PREFIX))
        except json.JSONDecodeError as exc:
            raise RoutingConfigError(
                f"{CLAUDE_USER_REQUEST_PREFIX[:-1]} invalide : {exc.msg}."
            ) from exc
        if not isinstance(request, dict):
            raise RoutingConfigError(
                f"{CLAUDE_USER_REQUEST_PREFIX[:-1]} : objet JSON attendu."
            )
        unknown = sorted(set(request) - {
            "tier", "model", "effort", "issue", "technical_remediation",
            "technical_remediation_id",
        })
        if unknown:
            raise RoutingConfigError(
                f"{CLAUDE_USER_REQUEST_PREFIX[:-1]} : clés inconnues : {', '.join(unknown)}."
            )
        task_prompt = rest if separator else ""
    requested_model = request.get("model")
    model = (
        claude_policy_model(requested_model)
        if requested_model is not None
        else None
    )
    if invocation_model and invocation_model != "inherit":
        invocation_model = claude_policy_model(invocation_model)
        if model is not None and model != invocation_model:
            raise RoutingConfigError(
                "demande Claude ambiguë : model diffère entre l'enveloppe et Agent.model."
            )
        model = invocation_model
    issue_id = request.get("issue")
    if issue_id is not None and (not isinstance(issue_id, str) or not issue_id.strip()):
        raise RoutingConfigError(
            f"{CLAUDE_USER_REQUEST_PREFIX[:-1]}.issue : chaîne non vide attendue."
        )
    technical_remediation = request.get("technical_remediation", False)
    if type(technical_remediation) is not bool:
        raise RoutingConfigError(
            f"{CLAUDE_USER_REQUEST_PREFIX[:-1]}.technical_remediation : booléen attendu."
        )
    technical_remediation_id = request.get("technical_remediation_id")
    if technical_remediation != (technical_remediation_id is not None):
        raise RoutingConfigError(
            f"{CLAUDE_USER_REQUEST_PREFIX[:-1]} : technical_remediation et "
            "technical_remediation_id sont requis ensemble."
        )
    if technical_remediation_id is not None and not isinstance(
        technical_remediation_id, str,
    ):
        raise RoutingConfigError(
            f"{CLAUDE_USER_REQUEST_PREFIX[:-1]}.technical_remediation_id : chaîne attendue."
        )
    return (
        UserRouteRequest(
            tier=request.get("tier"), model=model, effort=request.get("effort"),
            technical_remediation=technical_remediation,
            technical_remediation_id=technical_remediation_id,
        ),
        task_prompt,
        issue_id.strip() if issue_id is not None else None,
    )


def claude_route_plan(
    role: str,
    prompt: str,
    *,
    root: str | os.PathLike,
    environ: Mapping[str, str] | None = None,
    invocation_model: str | None = None,
    _preclaim_validate: Callable[[Mapping[str, object]], None] | None = None,
) -> dict[str, object]:
    """Resolve the exact shared Claude route used by the Agent pre-tool facade."""
    if _preclaim_validate is not None and not callable(_preclaim_validate):
        raise RoutingConfigError("préflight Claude avant claim invalide.")
    environ = os.environ if environ is None else environ
    request, task_prompt, issue_id = claude_user_request(prompt, invocation_model)
    escalation_floor = None
    remediation_authorization = None
    remediation_rearm_audit = []
    technical_remediation_open = False
    if issue_id is not None:
        escalation_store = EscalationStore.for_root(root)
        escalation_status = escalation_store.status(issue_id)
        remediation_authorization = escalation_status["remediation_authorization"]
        remediation_rearm_audit = escalation_status["remediation_rearm_audit"]
        technical_remediation_open = escalation_status["technical_remediation_open"]
        escalation_floor = escalation_store.active_floor(
            issue_id,
            role,
            technical_remediation=request.technical_remediation,
            technical_remediation_id=request.technical_remediation_id,
        )
    policy = _load_claude_policy(root)
    # Validate a direct request before availability fallback can substitute a
    # different configured model and hide the missing host translation.
    if request.model is not None:
        claude_invocation_model(request.model, project_models=policy.claude_models)
    route = policy.resolve(
        role,
        "claude",
        user=request,
        available_models=claude_available_models(environ),
        active_host_overrides=detect_claude_overrides(environ),
        minimum_tier=escalation_floor,
        minimum_source=f"escalation:{issue_id}" if escalation_floor else None,
    )
    # A direct model override must fail here, before the route can be used to
    # create telemetry state.  The host translation is intentionally checked
    # again by the launcher so this remains a pure validation boundary.
    claude_invocation_model(route.model, project_models=policy.claude_models)
    task_prompt = _validate_task_packet(task_prompt, role, "Claude")
    result = {
        "route": route,
        "effort_scopes": policy.effort_scopes,
        "project_models": policy.project_models,
        "claude_models": policy.claude_models,
        "task_prompt": task_prompt,
        "issue_id": issue_id,
        "minimum_tier": escalation_floor,
        "remediation_authorization": remediation_authorization,
        "remediation_rearm_audit": remediation_rearm_audit,
        "technical_remediation_open": technical_remediation_open,
        "technical_remediation_requested": request.technical_remediation,
        "technical_remediation_local_only": request.technical_remediation,
        **(
            {"technical_remediation_claimed": True}
            if request.technical_remediation else {}
        ),
    }
    if _preclaim_validate is not None:
        _preclaim_validate(result)
    correction_plan_claimed = False
    if (
        issue_id is not None
        and role != "reviewer"
        and not request.technical_remediation
    ):
        # Claude and Codex share this same durable CAS immediately before a
        # launchable plan is returned. Neither host gains a second provider use.
        correction_plan_claimed = escalation_store.claim_credited_correction_plan(
            issue_id, role, root=root,
        )
    if correction_plan_claimed:
        result["credited_correction_plan_claimed"] = True
    return result
