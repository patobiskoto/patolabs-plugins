#!/usr/bin/env python3
"""Route Foundry's logical Claude agents immediately before Agent tool execution.

Claude Code caches plugin agent frontmatter, so project policy cannot safely be
materialized there.  This PreToolUse hook is the dynamic façade: it resolves the common
policy, injects the invocation model and turn cap, then swaps the logical role for an
internal agent whose frontmatter carries only the resolved effort/capability profile.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "tooling"))

from foundry.routing import (  # noqa: E402
    RoutingConfigError,
    RoutingUnavailableError,
)
from foundry.routing_facades import (  # noqa: E402
    AGENT_IDENTITIES,
    agent_identity,
    claude_invocation_model,
    claude_route_plan,
    prepare_claude_invocation,
)
from foundry.escalation import MAX_PARALLEL_AGENTS  # noqa: E402
from foundry.telemetry import TelemetryObserver  # noqa: E402


@dataclass(frozen=True)
class ClaudeRole:
    capability: str
    max_turns: int
    max_prompt_chars: int


CLAUDE_ROLES = {
    "scout": ClaudeRole("readonly", 10, 4_000),
    "implementer": ClaudeRole("worker", 50, 12_000),
    "reviewer": ClaudeRole("readonly", 24, 16_000),
    "architect": ClaudeRole("readonly", 30, 10_000),
}
_PACKET_HEADINGS = ("Goal:", "Inputs:", "Constraints:", "Done when:")
_ROUTED_MARKER = "FOUNDRY_ROUTED_AGENT_V1"


def _logical_role(subagent_type: object) -> str | None:
    if not isinstance(subagent_type, str):
        return None
    # This hook is registered for every host Agent/Task call.  Only Foundry's
    # explicit namespace may opt into its routing policy; a host agent that happens
    # to share one of our display identities must remain untouched.
    if not subagent_type.startswith("foundry:"):
        return None
    candidate = subagent_type.removeprefix("foundry:").lower()
    identities = {identity.lower(): role for role, identity in AGENT_IDENTITIES.items()}
    # The pre-0.8 semantic identifiers remain a deterministic compatibility alias.
    return identities.get(candidate) or (candidate if candidate in CLAUDE_ROLES else None)


def _bounded_turns(value: object, ceiling: int) -> int:
    if value is None:
        return ceiling
    if type(value) is not int or value < 1:
        raise RoutingConfigError("Agent.max_turns doit être un entier strictement positif.")
    return min(value, ceiling)


def _validate_task_packet(prompt: str, role: str) -> None:
    profile = CLAUDE_ROLES[role]
    if len(prompt) > profile.max_prompt_chars:
        raise RoutingConfigError(
            f"task packet de {role} trop long : {len(prompt)} caractères ; "
            f"maximum {profile.max_prompt_chars}. Fournissez des chemins/références "
            "plutôt que l'historique complet."
        )
    missing = [heading for heading in _PACKET_HEADINGS if heading not in prompt]
    if missing:
        raise RoutingConfigError(
            f"task packet de {role} incomplet ; sections requises : "
            f"{', '.join(_PACKET_HEADINGS)} (manquantes : {', '.join(missing)})."
        )


def _warning_context(
    route,
    issue_id: str | None,
    remediation_authorization: dict | None = None,
    remediation_rearm_audit: list[dict] | None = None,
    technical_remediation_open: bool = False,
    technical_remediation_requested: bool = False,
    technical_remediation_claimed: bool = False,
    credited_correction_plan_claimed: bool = False,
) -> str:
    visible = {
        "role": route.role,
        "requested_tier": route.requested_tier,
        "selected_tier": route.selected_tier,
        "model": route.model,
        "effort": route.effort,
        "sources": dict(route.sources),
        "fallback_path": list(route.fallback_path),
        "gate_floor": route.gate_floor,
        "gate_effort_floor": route.gate_effort_floor,
        "availability_probed": route.availability_probed,
        "escalation": {
            "issue_id": issue_id,
            "minimum_tier": route.minimum_tier,
            "max_parallel_agents": MAX_PARALLEL_AGENTS,
            "remediation_authorization": remediation_authorization or {
                "state": "none", "role": None, "halt_generation": None,
                "maximum_credits": 0, "remaining_credits": 0,
            },
            "remediation_rearm_audit": remediation_rearm_audit or [],
            "technical_remediation_open": technical_remediation_open,
            "technical_remediation_requested": technical_remediation_requested,
            **(
                {"technical_remediation_claimed": True}
                if technical_remediation_claimed else {}
            ),
            **(
                {"credited_correction_plan_claimed": True}
                if credited_correction_plan_claimed else {}
            ),
        },
        "warnings": [
            {"code": warning.code, "message": warning.message}
            for warning in route.warnings
        ],
    }
    return "Foundry Claude route: " + json.dumps(visible, ensure_ascii=False)


def _role_contract(role: str) -> str:
    role_file = PLUGIN_ROOT / "agents" / f"{agent_identity(role).lower()}.md"
    try:
        text = role_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingConfigError(
            f"contrat du rôle Claude illisible ({role_file}): {exc.strerror or exc}."
        ) from exc
    parts = text.split("---\n", 2)
    if len(parts) != 3 or parts[0]:
        raise RoutingConfigError(f"frontmatter invalide pour le rôle Claude {role}.")
    # The reviewer guard compares the concrete launcher path.  Expand this host-only
    # placeholder before handing the contract to the Agent so the guarded command is
    # executable even when Claude does not export CLAUDE_PLUGIN_ROOT to subagents.
    return parts[2].strip().replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_ROOT))


def route_tool_input(
    tool_input: Mapping[str, object],
    *,
    cwd: str | os.PathLike,
    environ: Mapping[str, str] | None = None,
    correlation: str | None = None,
) -> tuple[dict[str, object], str] | None:
    """Return the rewritten Agent input + visible context, or None if out of scope."""
    role = _logical_role(tool_input.get("subagent_type"))
    if role is None:
        return None
    environ = os.environ if environ is None else environ
    prompt = tool_input.get("prompt")
    prepared = {}

    def preclaim_validate(resolution):
        if resolution.get("technical_remediation_local_only", False):
            return
        route = resolution["route"]
        task_prompt = resolution["task_prompt"]
        issue_id = resolution["issue_id"]
        remediation_authorization = resolution["remediation_authorization"]
        remediation_rearm_audit = resolution["remediation_rearm_audit"]
        technical_remediation_open = resolution["technical_remediation_open"]
        technical_remediation_requested = resolution["technical_remediation_requested"]
        technical_remediation_claimed = resolution.get(
            "technical_remediation_claimed", False,
        )
        _validate_task_packet(task_prompt, role)
        profile = CLAUDE_ROLES[role]
        role_contract = _role_contract(role)
        bounded_turns = _bounded_turns(
            tool_input.get("max_turns"), profile.max_turns,
        )
        invocation_model = claude_invocation_model(
            route.model, project_models=resolution["claude_models"],
        )
        routed_prompt = (
            f"{_ROUTED_MARKER}\n"
            f"Resolved role: {role}\n"
            "Follow this routed role contract:\n"
            "--- BEGIN ROLE CONTRACT ---\n"
            f"{role_contract}\n"
            "--- END ROLE CONTRACT ---\n"
            "Do not delegate to another agent. The task packet follows.\n\n"
            f"{task_prompt}"
        )
        updated = dict(tool_input)
        # maxTurns belongs to AgentDefinition/frontmatter, never to the Agent tool wire.
        updated.pop("maxTurns", None)
        updated.update({
            "subagent_type": f"foundry:routed-{profile.capability}-{route.effort}",
            "prompt": routed_prompt,
            "model": invocation_model,
            "max_turns": bounded_turns,
        })
        prepared["updated"] = updated
        prepared["unclaimed_context"] = _warning_context(
            route, issue_id, remediation_authorization, remediation_rearm_audit,
            technical_remediation_open, technical_remediation_requested,
            technical_remediation_claimed, False,
        )
        prepared["claimed_context"] = _warning_context(
            route, issue_id, remediation_authorization, remediation_rearm_audit,
            technical_remediation_open, technical_remediation_requested,
            technical_remediation_claimed, True,
        )
        if correlation:
            prepared["telemetry"] = (
                TelemetryObserver.from_environ(
                    environ, effort_scopes=resolution["effort_scopes"],
                    project_models=resolution["project_models"],
                ), route, correlation,
            )

    resolution = claude_route_plan(
        role,
        prompt if isinstance(prompt, str) else "",
        root=cwd,
        environ=environ,
        invocation_model=(
            tool_input.get("model")
            if isinstance(tool_input.get("model"), str) else None
        ),
        _preclaim_validate=preclaim_validate,
    )
    if resolution.get("technical_remediation_local_only", False):
        raise RoutingConfigError(
            "remédiation technique locale : l'invocation Agent/provider est refusée ; "
            "utilisez le plan local Foundry et conservez une review sous claim frais."
        )
    telemetry = prepared.get("telemetry")
    if telemetry is not None:
        prepare_claude_invocation(*telemetry)
    return prepared["updated"], (
        prepared["claimed_context"]
        if resolution.get("credited_correction_plan_claimed", False)
        else prepared["unclaimed_context"]
    )


def _allow(updated: dict[str, object], context: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "Agent Foundry routé par la politique résolue.",
        "updatedInput": updated,
        "additionalContext": context,
    }}


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"Routage Foundry refusé : {reason}",
    }}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or _logical_role(tool_input.get("subagent_type")) is None:
        return
    try:
        routed = route_tool_input(
            tool_input,
            cwd=payload.get("cwd") or os.getcwd(),
            environ=os.environ,
            correlation=payload.get("tool_use_id"),
        )
        assert routed is not None
        output = _allow(*routed)
    except (RoutingConfigError, RoutingUnavailableError) as exc:
        output = _deny(str(exc))
    except Exception:
        # Shared hooks remain fail-open per ADR-0005. Logical agents have a minimal
        # Read-only entrypoint and self-stop when the routed marker is absent.
        return
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
