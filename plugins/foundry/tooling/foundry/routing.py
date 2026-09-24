"""Host-neutral model routing contract for delegated Foundry roles.

The policy talks in semantic tiers.  Claude Code and Codex façades only detect
host state and execute the resolved target; they do not duplicate precedence,
fallback, review deduplication, or override-warning decisions.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, TypeVar

from foundry import registry
from foundry.effort_policy import (
    DEFAULT_EFFORT_SCOPES,
    EffortScope,
    is_public_identifier,
    scope_for,
    validate_scope_data,
)


LEVELS = ("economy", "balanced", "frontier", "apex")
# Compatibility projection for immutable FOUNDRY-46 pilot artifacts.  Routing
# and telemetry resolve effort from scoped declarations; this legacy tuple is
# derived from that declaration so the frozen pilot also recognises ``xhigh``.
EFFORTS = DEFAULT_EFFORT_SCOPES["claude"]["default"].levels
HOSTS = ("claude", "codex")
ROLE_DEFAULTS = {
    "scout": "economy",
    "implementer": "balanced",
    "coordinator": "balanced",
    "reviewer": "frontier",
    "architect": "apex",
}
GATE_FLOORS = {"reviewer": "frontier", "architect": "apex"}
GATE_EFFORT_FLOORS = {"reviewer": "high", "architect": "high"}
_ReviewDiffConsumer = TypeVar("_ReviewDiffConsumer")
_REVIEW_CLAIM_ATTEMPT_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ModelTarget:
    model: str
    effort: str


# This is the single provider-specific source.  Skills and agents consume roles
# and semantic tiers, never their own copy of these mappings.
DEFAULT_MAPPINGS = {
    "claude": {
        "economy": ModelTarget("haiku-4.5", "low"),
        "balanced": ModelTarget("sonnet-5", "medium"),
        "frontier": ModelTarget("opus-5", "high"),
        "apex": ModelTarget("fable-5", "high"),
    },
    "codex": {
        "economy": ModelTarget("gpt-5.6-luna", "low"),
        "balanced": ModelTarget("gpt-5.6-terra", "medium"),
        "frontier": ModelTarget("gpt-5.6-sol", "high"),
        "apex": ModelTarget("gpt-5.6-sol", "max"),
    },
}


class RoutingConfigError(ValueError):
    """A project or user routing value is invalid."""


class RoutingUnavailableError(RuntimeError):
    """No available model satisfies the role's fallback contract."""


@dataclass(frozen=True)
class UserRouteRequest:
    tier: str | None = None
    model: str | None = None
    effort: str | None = None
    technical_remediation: bool = False
    technical_remediation_id: str | None = None


@dataclass(frozen=True)
class RoutingWarning:
    code: str
    message: str


@dataclass(frozen=True)
class ResolvedRoute:
    role: str
    host: str
    requested_tier: str
    selected_tier: str
    model: str
    effort: str
    sources: Mapping[str, str]
    fallback_direction: str
    fallback_candidates: tuple[str, ...]
    fallback_path: tuple[str, ...]
    gate_floor: str | None = None
    gate_effort_floor: str | None = None
    minimum_tier: str | None = None
    minimum_source: str | None = None
    availability_probed: bool = False
    warnings: tuple[RoutingWarning, ...] = ()

    def to_dict(self) -> dict:
        value = asdict(self)
        value["fallback_candidates"] = list(self.fallback_candidates)
        value["fallback_path"] = list(self.fallback_path)
        value["warnings"] = [asdict(warning) for warning in self.warnings]
        return value


def _level_index(level: str, where: str) -> int:
    try:
        return LEVELS.index(level)
    except ValueError as exc:
        raise RoutingConfigError(
            f"{where}: niveau '{level}' inconnu ; attendu : {', '.join(LEVELS)}."
        ) from exc


def _non_empty_string(value, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoutingConfigError(f"{where}: chaîne non vide attendue.")
    return value.strip()


def _public_model_identifier(value, where: str) -> str:
    model = _non_empty_string(value, where)
    if not is_public_identifier(model):
        raise RoutingConfigError(f"{where}: identifiant public de modèle attendu.")
    return model


def _validate_tier(level, where: str) -> str:
    value = _non_empty_string(level, where)
    _level_index(value, where)
    return value


def _validate_effort(effort, where: str, scope: EffortScope | None = None) -> str:
    value = _non_empty_string(effort, where)
    scope = scope or DEFAULT_EFFORT_SCOPES["codex"]["default"]
    if value not in scope.levels:
        raise RoutingConfigError(
            f"{where}: effort '{value}' inconnu pour le scope "
            f"({scope.host}, {scope.family}, v{scope.version}) ; attendu : {', '.join(scope.levels)}."
        )
    if value in scope.inadmissible:
        raise RoutingConfigError(f"{where}: effort '{value}' inadmissible : {scope.inadmissible[value]}.")
    return value


def _validate_gate_floor(role: str, tier: str, where: str) -> None:
    floor = GATE_FLOORS.get(role)
    if floor and _level_index(tier, where) < LEVELS.index(floor):
        raise RoutingConfigError(
            f"{where}: le rôle gate '{role}' ne peut pas descendre sous '{floor}' "
            f"(reçu : '{tier}')."
        )


def _validate_gate_effort(role: str, effort: str, where: str, scope: EffortScope) -> None:
    floor = GATE_EFFORT_FLOORS.get(role)
    if floor and floor not in scope.levels:
        raise RoutingConfigError(
            f"{where}: configuration invalide ; le plancher d'effort '{floor}' du rôle "
            f"gate '{role}' est absent du scope ({scope.host}, {scope.family}, "
            f"v{scope.version}) ; niveaux acceptés : {', '.join(scope.levels)}."
        )
    if floor and scope.levels.index(effort) < scope.levels.index(floor):
        raise RoutingConfigError(
            f"{where}: le rôle gate '{role}' ne peut pas descendre sous l'effort "
            f"'{floor}' (reçu : '{effort}')."
        )


def _project_root(start: str | os.PathLike | None = None) -> Path:
    cwd = Path(start or Path.cwd()).expanduser().resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=cwd,
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return cwd


def _load_project_data(root: Path) -> tuple[Path, dict]:
    path = root / ".foundry" / "model-routing.json"
    if not path.exists():
        return path, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoutingConfigError(
            f"{path}: JSON invalide à la ligne {exc.lineno}, colonne {exc.colno}: "
            f"{exc.msg}."
        ) from exc
    if not isinstance(data, dict):
        raise RoutingConfigError(f"{path}: l'objet JSON racine doit être un objet.")
    return path, data


def _validate_project_data(data: dict, path: Path) -> tuple[dict, dict, dict, dict]:
    allowed_root = {"version", "roles", "mappings", "effort_scopes", "claude_models"}
    unknown = sorted(set(data) - allowed_root)
    if unknown:
        raise RoutingConfigError(
            f"{path}: clés inconnues à la racine : {', '.join(unknown)} ; "
            "attendu : version, roles, mappings, effort_scopes, claude_models."
        )
    version = data.get("version", 1)
    if type(version) is not int or version != 1:
        raise RoutingConfigError(
            f"{path}.version: l'entier 1 est requis (booléens et nombres décimaux refusés)."
        )

    roles = data.get("roles", {})
    if not isinstance(roles, dict):
        raise RoutingConfigError(f"{path}.roles: objet attendu.")
    clean_roles = {}
    for role, tier in roles.items():
        if role not in ROLE_DEFAULTS:
            raise RoutingConfigError(
                f"{path}.roles.{role}: rôle inconnu ; attendu : "
                f"{', '.join(ROLE_DEFAULTS)}."
            )
        where = f"{path}.roles.{role}"
        clean_roles[role] = _validate_tier(tier, where)
        _validate_gate_floor(role, clean_roles[role], where)

    mappings = data.get("mappings", {})
    if not isinstance(mappings, dict):
        raise RoutingConfigError(f"{path}.mappings: objet attendu.")
    clean_mappings: dict[str, dict[str, dict[str, str]]] = {}
    for host, levels in mappings.items():
        if host not in HOSTS:
            raise RoutingConfigError(
                f"{path}.mappings.{host}: hôte inconnu ; attendu : {', '.join(HOSTS)}."
            )
        if not isinstance(levels, dict):
            raise RoutingConfigError(f"{path}.mappings.{host}: objet attendu.")
        clean_mappings[host] = {}
        for tier, target in levels.items():
            where = f"{path}.mappings.{host}.{tier}"
            tier = _validate_tier(tier, where)
            if not isinstance(target, dict):
                raise RoutingConfigError(f"{where}: objet attendu avec model et/ou effort.")
            unknown_target = sorted(set(target) - {"model", "effort"})
            if unknown_target:
                raise RoutingConfigError(
                    f"{where}: clés inconnues : {', '.join(unknown_target)} ; "
                    "attendu : model, effort."
                )
            if not target:
                raise RoutingConfigError(f"{where}: fournissez model et/ou effort.")
            clean_target = {}
            if "model" in target:
                clean_target["model"] = _public_model_identifier(target["model"], f"{where}.model")
            if "effort" in target:
                clean_target["effort"] = _non_empty_string(target["effort"], f"{where}.effort")
            clean_mappings[host][tier] = clean_target
    raw_scopes = data.get("effort_scopes", {})
    if not isinstance(raw_scopes, dict):
        raise RoutingConfigError(f"{path}.effort_scopes: objet attendu.")
    clean_scopes = {host: dict(families) for host, families in DEFAULT_EFFORT_SCOPES.items()}
    for host, families in raw_scopes.items():
        if host not in HOSTS or not isinstance(families, dict):
            raise RoutingConfigError(f"{path}.effort_scopes.{host}: objet d'hôte connu attendu.")
        clean_scopes.setdefault(host, {})
        for family, raw_scope in families.items():
            family = _non_empty_string(family, f"{path}.effort_scopes.{host}")
            try:
                clean_scopes[host][family] = validate_scope_data(
                    host, family, raw_scope, f"{path}.effort_scopes.{host}.{family}",
                )
            except ValueError as exc:
                raise RoutingConfigError(str(exc)) from exc
    claude_models = data.get("claude_models", {})
    if not isinstance(claude_models, dict) or any(
        not is_public_identifier(model) or not isinstance(alias, str) or not alias.strip()
        for model, alias in claude_models.items()
    ):
        raise RoutingConfigError(f"{path}.claude_models: objet modèle→alias non vide attendu.")
    return clean_roles, clean_mappings, clean_scopes, dict(claude_models)


def _validate_claude_host_policy(
    path: Path, mappings: Mapping[str, Mapping[str, Mapping[str, str]]],
    scopes: Mapping[str, Mapping[str, EffortScope]], claude_models: Mapping[str, str],
) -> None:
    """Make every reader reject Claude policy the hook cannot execute."""
    # The concrete aliases and profiles live with the host facade.  Importing
    # lazily avoids a module cycle while making policy loading the shared
    # boundary for previews, doctor, CLI, and invocation.
    from foundry.routing_facades import (
        CLAUDE_EXECUTABLE_EFFORTS,
        _CLAUDE_MODEL_IDS,
        claude_policy_model,
    )

    canonical_models = {
        claude_policy_model(model): alias for model, alias in claude_models.items()
    }
    for model, alias in canonical_models.items():
        if model in _CLAUDE_MODEL_IDS and alias != _CLAUDE_MODEL_IDS[model]:
            raise RoutingConfigError(
                f"{path}.claude_models.{model}: alias Agent intégré incompatible ; "
                f"'{_CLAUDE_MODEL_IDS[model]}' obligatoire."
            )
    for tier, target in mappings.get("claude", {}).items():
        model = target.get("model")
        if model is None:
            continue
        canonical = claude_policy_model(model)
        if canonical not in _CLAUDE_MODEL_IDS and canonical not in canonical_models:
            raise RoutingConfigError(
                f"{path}.mappings.claude.{tier}.model: modèle '{canonical}' sans "
                f"traduction hôte ; déclarez claude_models.{canonical} avec son alias Agent."
            )
    for family, scope in scopes.get("claude", {}).items():
        unavailable = [level for level in scope.levels if level not in CLAUDE_EXECUTABLE_EFFORTS]
        if unavailable:
            raise RoutingConfigError(
                f"{path}.effort_scopes.claude.{family}: profils Agent Claude absents pour "
                f"{', '.join(unavailable)}."
            )


def host_override_warnings(host: str, active_overrides: Iterable[str]) -> tuple[RoutingWarning, ...]:
    """Turn façade-detected overrides into the common, value-free warning contract."""
    if host not in HOSTS:
        raise RoutingConfigError(f"hôte '{host}' inconnu ; attendu : {', '.join(HOSTS)}.")
    warnings = []
    for name in sorted(set(active_overrides)):
        clean_name = _non_empty_string(name, "override hôte")
        warnings.append(RoutingWarning(
            code="HOST_OVERRIDE_NEUTRALIZES_POLICY",
            message=(f"L'override hôte '{clean_name}' est actif sur {host} et peut "
                     "neutraliser la politique Foundry résolue ; sa valeur est masquée."),
        ))
    return tuple(warnings)


@dataclass(frozen=True)
class RoutingPolicy:
    root: Path
    config_path: Path
    role_overrides: Mapping[str, str] = field(default_factory=dict)
    mapping_overrides: Mapping[str, Mapping[str, Mapping[str, str]]] = field(default_factory=dict)
    effort_scopes: Mapping[str, Mapping[str, EffortScope]] = field(default_factory=lambda: DEFAULT_EFFORT_SCOPES)
    claude_models: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | os.PathLike | None = None) -> "RoutingPolicy":
        project_root = _project_root(root)
        path, data = _load_project_data(project_root)
        roles, mappings, scopes, claude_models = _validate_project_data(data, path)
        _validate_claude_host_policy(path, mappings, scopes, claude_models)
        return cls(project_root, path, roles, mappings, scopes, claude_models)

    @property
    def has_project_config(self) -> bool:
        return self.config_path.exists()

    @property
    def project_models(self) -> frozenset[str]:
        """Return exact project-declared model identifiers for telemetry validation."""
        models = set(self.claude_models)
        for mappings in self.mapping_overrides.values():
            for target in mappings.values():
                model = target.get("model")
                if model is not None:
                    models.add(model)
        return frozenset(models)

    def _target(self, host: str, tier: str) -> tuple[ModelTarget, dict[str, str]]:
        default = DEFAULT_MAPPINGS[host][tier]
        override = self.mapping_overrides.get(host, {}).get(tier, {})
        model = override.get("model", default.model)
        effort = override.get("effort", default.effort)
        sources = {
            "model": "project" if "model" in override else "default",
            "effort": "project" if "effort" in override else "default",
        }
        return ModelTarget(model, effort), sources

    def resolve(
        self,
        role: str,
        host: str,
        *,
        user: UserRouteRequest | None = None,
        available_models: Iterable[str] | None = None,
        active_host_overrides: Iterable[str] = (),
        minimum_tier: str | None = None,
        minimum_source: str | None = None,
    ) -> ResolvedRoute:
        if role not in ROLE_DEFAULTS:
            raise RoutingConfigError(
                f"rôle '{role}' inconnu ; attendu : {', '.join(ROLE_DEFAULTS)}."
            )
        if host not in HOSTS:
            raise RoutingConfigError(f"hôte '{host}' inconnu ; attendu : {', '.join(HOSTS)}.")
        user = user or UserRouteRequest()
        requested_tier = user.tier or self.role_overrides.get(role) or ROLE_DEFAULTS[role]
        requested_tier = _validate_tier(requested_tier, f"demande utilisateur pour {role}")
        _validate_gate_floor(role, requested_tier, f"résolution de {role}")
        tier_source = ("user" if user.tier is not None else
                       "project" if role in self.role_overrides else "default")
        if minimum_tier is not None:
            minimum_tier = _validate_tier(minimum_tier, f"plancher dynamique de {role}")
            minimum_source = _non_empty_string(
                minimum_source or "dynamic", f"source du plancher dynamique de {role}",
            )
            if (user.tier is not None and
                    LEVELS.index(requested_tier) < LEVELS.index(minimum_tier)):
                raise RoutingConfigError(
                    f"demande utilisateur pour {role}.tier : '{requested_tier}' est sous "
                    f"le plancher actif '{minimum_tier}' ({minimum_source})."
                )
            if LEVELS.index(requested_tier) < LEVELS.index(minimum_tier):
                requested_tier = minimum_tier
                tier_source = minimum_source
        if user.model is not None:
            _public_model_identifier(user.model, f"demande utilisateur pour {role}.model")
            if role in GATE_FLOORS:
                raise RoutingConfigError(
                    f"demande utilisateur pour {role}.model : un modèle direct ne peut "
                    "pas être classé contre le plancher d'un gate ; demandez un tier "
                    "autorisé ou surchargez son mapping projet."
                )
            if minimum_tier is not None:
                raise RoutingConfigError(
                    f"demande utilisateur pour {role}.model : un modèle direct ne peut "
                    "pas être classé contre un plancher actif ; demandez un tier autorisé "
                    "ou surchargez son mapping projet."
                )

        start = LEVELS.index(requested_tier)
        gate_floor = GATE_FLOORS.get(role)
        if gate_floor:
            candidates = LEVELS[start:]
            fallback_direction = "up"
        elif minimum_tier is not None:
            floor_index = LEVELS.index(minimum_tier)
            if start == floor_index:
                candidates = LEVELS[start:]
                fallback_direction = "up-from-floor"
            else:
                candidates = tuple(reversed(LEVELS[floor_index:start + 1]))
                fallback_direction = "down-to-floor"
        else:
            candidates = tuple(reversed(LEVELS[:start + 1]))
            fallback_direction = "down"

        availability = None if available_models is None else set(available_models)
        attempted: list[str] = []
        selected = None
        selected_sources = None
        for index, tier in enumerate(candidates):
            target, sources = self._target(host, tier)
            # A direct model request names the first target; if unavailable, fallback
            # uses the next tier's configured model. Effort is orthogonal to model
            # availability, so an explicit user effort remains authoritative throughout
            # the fallback path.
            if index == 0:
                if user.model is not None:
                    target = ModelTarget(user.model.strip(), target.effort)
                    sources["model"] = "user"
            if user.effort is not None:
                if not isinstance(user.effort, str):
                    raise RoutingConfigError(
                        f"résolution de {role}.effort : chaîne attendue."
                    )
                target = ModelTarget(target.model, user.effort.strip())
                sources["effort"] = "user"
            scope = scope_for(host, target.model, self.effort_scopes)
            _validate_effort(target.effort, f"résolution de {role}.effort", scope)
            _validate_gate_effort(role, target.effort, f"résolution de {role}", scope)
            attempted.append(tier)
            if availability is None or target.model in availability:
                selected = (tier, target)
                selected_sources = sources
                break

        if selected is None:
            kind = f"gate '{role}'" if gate_floor else f"rôle '{role}'"
            direction = (
                "respectant son plancher actif" if minimum_tier is not None
                else "supérieurs" if gate_floor else "inférieurs"
            )
            raise RoutingUnavailableError(
                f"Aucun modèle disponible pour le {kind} sur {host}. "
                f"Niveaux {direction} essayés : {', '.join(attempted)}. "
                "Rendez un modèle de ce chemin disponible ou fournissez un override explicite."
            )

        selected_tier, target = selected
        warnings = list(host_override_warnings(host, active_host_overrides))
        if selected_tier != requested_tier:
            if gate_floor:
                warnings.insert(0, RoutingWarning(
                    code="GATE_MODEL_FALLBACK_UP",
                    message=(f"Le modèle de {requested_tier} est indisponible ; le gate "
                             f"'{role}' monte vers {selected_tier} et ne descend jamais "
                             f"sous {gate_floor}."),
                ))
            elif minimum_tier is not None and (
                LEVELS.index(selected_tier) > LEVELS.index(requested_tier)
            ):
                warnings.insert(0, RoutingWarning(
                    code="ESCALATED_MODEL_FALLBACK_UP",
                    message=(f"Le modèle de {requested_tier} est indisponible ; le rôle "
                             f"'{role}' monte vers {selected_tier} sans franchir son "
                             f"plancher actif {minimum_tier}."),
                ))
            else:
                warnings.insert(0, RoutingWarning(
                    code="MODEL_FALLBACK_DOWN",
                    message=(f"Le modèle de {requested_tier} est indisponible ; le rôle "
                             f"'{role}' descend vers {selected_tier}"
                             f"{' sans passer sous ' + minimum_tier if minimum_tier else ''}."),
                ))
        sources = {"tier": tier_source, **selected_sources}
        if selected_tier != requested_tier:
            sources["tier"] = "fallback"
        return ResolvedRoute(
            role=role,
            host=host,
            requested_tier=requested_tier,
            selected_tier=selected_tier,
            model=target.model,
            effort=target.effort,
            sources=sources,
            fallback_direction=fallback_direction,
            fallback_candidates=tuple(candidates),
            fallback_path=tuple(attempted),
            gate_floor=gate_floor,
            gate_effort_floor=GATE_EFFORT_FLOORS.get(role),
            minimum_tier=minimum_tier,
            minimum_source=minimum_source,
            availability_probed=availability is not None,
            warnings=tuple(warnings),
        )


def review_diff_hash(diff: str | bytes) -> str:
    """The one cross-host review key: SHA-256 of the exact diff bytes."""
    payload = diff.encode("utf-8") if isinstance(diff, str) else diff
    return hashlib.sha256(payload).hexdigest()


_AC_LINE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s+(.+?)\s*$")
_AC_MARKER = re.compile(r"^(?P<prefix>\s*[-*+]\s+\[)(?P<mark>[ xX])(?P<suffix>\]\s+.+)$")
_MARKDOWN_BLOCK_START = re.compile(
    r"^(?:#{1,6}(?:\s|$)|[-*+]\s+|\d{1,9}[.)]\s+|>|```|~~~)"
)


def _starts_independent_markdown_block(line: str) -> bool:
    """Return whether a line starts a block that cannot be a lazy continuation."""
    if _AC_LINE.match(line) is not None:
        return True
    return not line[:1].isspace() and _MARKDOWN_BLOCK_START.match(line) is not None


def acceptance_criteria(body: str | None) -> list[dict[str, str]]:
    """Return the frozen, provider-neutral AC projection of an issue body.

    We intentionally digest the literal criterion, not checkbox state: a tracker
    checkbox is progress metadata and must never be silently inferred from review.
    """
    if not isinstance(body, str):
        raise RoutingConfigError("preuve AC : corps d'issue textuel requis.")
    # Markdown list items can span indented or lazy paragraph continuation lines.
    # Digest the entire literal item so a material change cannot escape the review
    # contract, but stop before a distinct Markdown block or a new checkbox.
    lines = body.splitlines()
    values = []
    index = 0
    while index < len(lines):
        match = _AC_LINE.match(lines[index])
        if match is None:
            index += 1
            continue
        parts = [match.group(1).strip()]
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip() and _starts_independent_markdown_block(line):
                break
            if not line.strip():
                if (index + 1 >= len(lines) or
                        not lines[index + 1][:1].isspace() or
                        _starts_independent_markdown_block(lines[index + 1])):
                    break
                parts.append("")
            else:
                parts.append(line.strip())
            index += 1
        values.append("\n".join(parts))
    if not values:
        raise RoutingConfigError("preuve AC : aucun critère d'acceptation lisible.")
    return [
        {"id": f"ac-{index}", "digest": hashlib.sha256(value.encode("utf-8")).hexdigest()}
        for index, value in enumerate(values, start=1)
    ]


def acceptance_digest(criteria: list[dict[str, str]]) -> str:
    """Digest the ordered AC identities without retaining their text in evidence."""
    payload = json.dumps(criteria, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def synchronize_acceptance_body(
    issue_id: str, body: str, proof: dict,
) -> tuple[str, int]:
    """Check only AC authorized by a current structured proof.

    The literal body is preserved byte-for-byte except for unchecked marker bytes
    whose matching outcome is ``pass``. Proof prose is never parsed.
    """
    expected = acceptance_criteria(body)
    issue = proof.get("issue") if isinstance(proof, dict) else None
    proof_id = proof.get("proof_id") if isinstance(proof, dict) else None
    outcomes = issue.get("criteria") if isinstance(issue, dict) else None
    if (not isinstance(issue_id, str) or not isinstance(issue, dict)
            or not isinstance(proof_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", proof_id)
            or issue.get("id") != issue_id
            or issue.get("ac_digest") != acceptance_digest(expected)
            or not isinstance(outcomes, list) or len(outcomes) != len(expected)):
        raise RoutingConfigError(
            "synchronisation AC : preuve incompatible avec le corps courant."
        )
    for criterion, outcome in zip(expected, outcomes):
        if (not isinstance(outcome, dict)
                or outcome.get("id") != criterion["id"]
                or outcome.get("digest") != criterion["digest"]
                or outcome.get("verdict") not in {
                    "pass", "fail", "not_covered", "contradicted",
                }):
            raise RoutingConfigError(
                "synchronisation AC : verdict structuré invalide."
            )

    lines = body.splitlines(keepends=True)
    marker_indexes = [index for index, line in enumerate(lines) if _AC_MARKER.match(
        line.rstrip("\r\n")
    )]
    if len(marker_indexes) != len(expected):
        raise RoutingConfigError(
            "synchronisation AC : projection Markdown incohérente."
        )
    changed = 0
    for line_index, outcome in zip(marker_indexes, outcomes):
        line = lines[line_index]
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        marker = _AC_MARKER.match(content)
        if marker is None:  # guarded by marker_indexes; keep the operation total.
            raise RoutingConfigError(
                "synchronisation AC : marqueur Markdown invalide."
            )
        if outcome["verdict"] == "pass" and marker.group("mark") == " ":
            lines[line_index] = (
                content[:marker.start("mark")] + "x" + content[marker.end("mark"):] + ending
            )
            changed += 1
    updated = "".join(lines)
    if acceptance_criteria(updated) != expected:
        raise RoutingConfigError(
            "synchronisation AC : le texte littéral des critères a changé."
        )
    return updated, changed


def git_head(root: str | os.PathLike | None = None) -> str:
    project_root = _project_root(root)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root,
                            capture_output=True, text=True)
    value = result.stdout.strip()
    if result.returncode != 0 or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise RoutingConfigError("impossible de résoudre le HEAD Git courant.")
    return value


def repository_identity(root: str | os.PathLike | None = None) -> str:
    """Stable repo namespace for the shared ledger; the identity is stored only hashed."""
    project_root = _project_root(root)
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"], cwd=project_root,
        capture_output=True, text=True,
    )
    return result.stdout.strip() or str(project_root)


def git_diff(root: str | os.PathLike | None = None, base: str | None = None) -> bytes:
    """Exact local PR diff bytes used by both review input and its dedup key."""
    project_root, base = review_diff_coordinates(root, base)
    result = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", f"{base}...HEAD"],
        cwd=project_root, capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RoutingConfigError(
            f"impossible de calculer le diff {base}...HEAD : {detail or 'git diff a échoué'}."
        )
    return result.stdout


def review_diff_coordinates(
    root: str | os.PathLike | None = None,
    base: str | None = None,
) -> tuple[Path, str]:
    """Resolve once the coordinates both review claim and verifier must reuse."""
    if base is not None and (not isinstance(base, str) or not base or base.startswith("-")):
        raise RoutingConfigError(
            "base Git invalide : une révision non vide ne commençant pas par '-' est requise."
        )
    project_root = _project_root(root)
    base = base or f"origin/{registry.default_branch(str(project_root)) or 'main'}"
    return project_root, base


@dataclass(frozen=True)
class ReviewClaim:
    """Public claim verdict; only a new owner receives its generation capability."""

    diff_hash: str
    should_run: bool
    state: str
    generation: int
    claim_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "diff_hash": self.diff_hash,
            "should_run": self.should_run,
            "state": self.state,
            "generation": self.generation,
        }
        if self.claim_id is not None:
            payload["claim_id"] = self.claim_id
        return payload

    # Preserve the original two-value API for callers which only need deduplication.
    def __iter__(self):
        return iter((self.diff_hash, self.should_run))

    def __getitem__(self, index):
        return (self.diff_hash, self.should_run)[index]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ReviewDeduplicator:
    """Atomic, generation-bound review ownership in Foundry's shared state."""

    _LEGACY_QUARANTINE_REASON = "active_legacy_claim_without_git_coordinates"

    def __init__(self, repository: str, state_dir: str | os.PathLike | None = None):
        repository = _non_empty_string(repository, "namespace de dépôt")
        self.__repository = repository
        namespace = hashlib.sha256(repository.encode("utf-8")).hexdigest()
        base = Path(state_dir) if state_dir is not None else Path(registry.data_dir())
        self.__state_dir = base
        self.directory = base / "review-dedup" / namespace

    @staticmethod
    def _validate_hash(diff_hash: str) -> str:
        if (not isinstance(diff_hash, str) or len(diff_hash) != 64 or
                any(character not in "0123456789abcdef" for character in diff_hash)):
            raise RoutingConfigError("hash de review invalide : SHA-256 hexadécimal attendu.")
        return diff_hash

    @staticmethod
    def _validate_claim_id(claim_id: str) -> str:
        if (not isinstance(claim_id, str) or len(claim_id) != 64 or
                any(character not in "0123456789abcdef" for character in claim_id)):
            raise RoutingConfigError("claim_id de review invalide : identifiant hexadécimal attendu.")
        return claim_id

    @staticmethod
    def _validate_claim_attempt_token(claim_attempt_token: str) -> str:
        if (
            not isinstance(claim_attempt_token, str)
            or _REVIEW_CLAIM_ATTEMPT_TOKEN.fullmatch(claim_attempt_token) is None
        ):
            raise RoutingConfigError(
                "token secret de tentative de claim invalide : 64 caractères "
                "hexadécimaux minuscules sont requis."
            )
        return claim_attempt_token

    @staticmethod
    def _claim_id_for_attempt(
        diff_hash: str,
        coordinates: Mapping[str, str],
        claim_attempt_token: str,
    ) -> str:
        material = json.dumps(
            {
                "domain": "foundry-review-claim-v1",
                "diff_hash": diff_hash,
                "coordinates": dict(coordinates),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(
            bytes.fromhex(claim_attempt_token), material, hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _validate_coordinates(coordinates: Mapping[str, object] | None) -> dict[str, str]:
        """Validate the stable caller coordinates persisted with a review claim."""
        if (not isinstance(coordinates, Mapping) or set(coordinates) != {"root", "base"} or
                not isinstance(coordinates.get("root"), str) or
                not isinstance(coordinates.get("base"), str) or
                not coordinates["root"] or len(coordinates["base"]) != 40 or
                any(character not in "0123456789abcdef" for character in coordinates["base"])):
            raise RoutingConfigError(
                "coordonnées immuables de review invalides ; root et base SHA Git figé "
                "sont requis."
            )
        return {"root": coordinates["root"], "base": coordinates["base"]}

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / ".ledger.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _serialized_record(record: Mapping[str, object]) -> bytes:
        return (
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")

    def _atomic_write(self, marker: Path, record: Mapping[str, object]) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{marker.name}.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(self._serialized_record(record))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, marker)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _append_only_write(self, marker: Path, record: Mapping[str, object]) -> None:
        """Durably publish one immutable record without replacing an existing one."""
        marker.parent.mkdir(parents=True, exist_ok=True)
        parent_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        fd, temporary = tempfile.mkstemp(prefix=f".{marker.name}.", dir=marker.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(self._serialized_record(record))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, marker)
            except FileExistsError:
                if marker.read_bytes() != self._serialized_record(record):
                    raise RoutingConfigError(
                        "quarantaine de review divergente ; intervention humaine requise."
                    )
            directory_fd = os.open(marker.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _read_record(
        self,
        marker: Path,
        diff_hash: str,
        *,
        migrate_bare_legacy: bool = True,
    ) -> dict:
        try:
            record = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoutingConfigError(
                f"ledger de review corrompu pour {diff_hash} ; intervention humaine requise."
            ) from exc
        if record == {"diff_hash": diff_hash} and migrate_bare_legacy:
            record = {
                "schema_version": 1,
                "diff_hash": diff_hash,
                "state": "completed",
                "generation": 0,
                "claim": None,
                "recoveries": [],
                "completed_at": None,
                "legacy_migrated": True,
                "migrated_at": _utc_now(),
            }
            self._atomic_write(marker, record)
            return record
        if (not isinstance(record, dict) or record.get("schema_version") != 1 or
                record.get("diff_hash") != diff_hash or
                record.get("state") not in {"in_progress", "completed"} or
                type(record.get("generation")) is not int or
                not isinstance(record.get("recoveries"), list)):
            raise RoutingConfigError(
                f"ledger de review invalide pour {diff_hash} ; intervention humaine requise."
            )
        if "claim_publication" in record:
            self._validate_claim_publication(record)
        return record

    @staticmethod
    def _valid_utc_timestamp(value: object) -> bool:
        if not isinstance(value, str) or not value.endswith("Z"):
            return False
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            return False
        return parsed.tzinfo is not None

    def _validate_claim_publication(self, record: Mapping[str, object]) -> None:
        publication = record.get("claim_publication")
        if (
            not isinstance(publication, Mapping)
            or set(publication) != {"attempt_digest", "generation"}
            or not isinstance(publication.get("attempt_digest"), str)
            or _REVIEW_CLAIM_ATTEMPT_TOKEN.fullmatch(
                publication["attempt_digest"]
            ) is None
            or type(publication.get("generation")) is not int
            or publication["generation"] < 1
            or publication["generation"] > record.get("generation")
        ):
            raise RoutingConfigError(
                "publication de claim invalide ; intervention humaine requise."
            )

    def _assert_quarantinable_legacy_claim(
        self,
        diff_hash: str,
        record: Mapping[str, object],
    ) -> None:
        """Accept only the exact proof-free v1 active-claim shape."""
        if record.get("acceptance_proof_id") is not None:
            raise RoutingConfigError(
                "une preuve de review existe ; quarantaine legacy refusée."
            )
        expected_keys = {
            "schema_version", "diff_hash", "state", "generation", "claim", "recoveries",
        }
        claim = record.get("claim")
        recoveries = record.get("recoveries")
        generation = record.get("generation")
        if (
            set(record) != expected_keys
            or record.get("schema_version") != 1
            or record.get("diff_hash") != diff_hash
            or record.get("state") != "in_progress"
            or type(generation) is not int
            or generation < 1
            or not isinstance(claim, Mapping)
            or set(claim) != {"id", "started_at"}
            or not self._valid_utc_timestamp(claim.get("started_at"))
            or not isinstance(recoveries, list)
            or len(recoveries) != generation - 1
        ):
            raise RoutingConfigError(
                "claim legacy ambigu ou non actif ; quarantaine refusée."
            )
        self._validate_claim_id(claim.get("id"))
        abandoned_claim_ids = []
        for recovery in recoveries:
            if (
                not isinstance(recovery, Mapping)
                or set(recovery) != {"abandoned_claim_id", "reason", "recovered_at"}
                or not isinstance(recovery.get("reason"), str)
                or not recovery["reason"].strip()
                or not self._valid_utc_timestamp(recovery.get("recovered_at"))
            ):
                raise RoutingConfigError(
                    "claim legacy ambigu ou non actif ; quarantaine refusée."
                )
            abandoned_claim_ids.append(
                self._validate_claim_id(recovery.get("abandoned_claim_id"))
            )
        if (
            len(set(abandoned_claim_ids)) != len(abandoned_claim_ids)
            or claim["id"] in abandoned_claim_ids
        ):
            raise RoutingConfigError(
                "claim legacy ambigu ou non actif ; quarantaine refusée."
            )

    def _quarantine_legacy_claim(
        self,
        diff_hash: str,
        record: Mapping[str, object],
    ) -> dict[str, str]:
        """Append one deterministic archive before replacing the active hash index."""
        identity = {
            "schema_version": 1,
            "reason": self._LEGACY_QUARANTINE_REASON,
            "diff_hash": diff_hash,
            "legacy_record": record,
        }
        migration_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        marker = self.directory / "legacy-quarantine" / migration_id
        if marker.is_file():
            try:
                quarantine = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RoutingConfigError(
                    "quarantaine de review illisible ; intervention humaine requise."
                ) from exc
            if (
                not isinstance(quarantine, dict)
                or set(quarantine) != {*identity, "migration_id", "migrated_at"}
                or quarantine.get("migration_id") != migration_id
                or not self._valid_utc_timestamp(quarantine.get("migrated_at"))
                or any(quarantine.get(key) != value for key, value in identity.items())
            ):
                raise RoutingConfigError(
                    "quarantaine de review divergente ; intervention humaine requise."
                )
        else:
            quarantine = {
                **identity,
                "migration_id": migration_id,
                "migrated_at": _utc_now(),
            }
            self._append_only_write(marker, quarantine)
        return {
            "migration_id": migration_id,
            "reason": self._LEGACY_QUARANTINE_REASON,
        }

    def _record_for(self, diff_hash: str) -> tuple[Path, dict]:
        marker = self.directory / diff_hash
        if not marker.is_file():
            raise RoutingConfigError(
                f"le hash de review {diff_hash} n'est pas réservé dans le ledger partagé."
            )
        return marker, self._read_record(marker, diff_hash)

    @staticmethod
    def _assert_active_generation(record: Mapping[str, object], claim_id: str) -> None:
        claim = record.get("claim")
        if (record.get("state") != "in_progress" or not isinstance(claim, Mapping) or
                claim.get("id") != claim_id):
            raise RoutingConfigError(
                "cette génération de claim n'est plus active ; utilisez la génération "
                "courante et ne terminez pas un ancien reviewer."
            )

    def is_claimed(self, diff_hash: str) -> bool:
        self._validate_hash(diff_hash)
        return (self.directory / diff_hash).is_file()

    def git_claim_requires_preflight(self, diff_hash: str) -> bool:
        """Return whether ``claim_git`` may create or replace the hash owner.

        A coordinate-bound marker is a safe duplicate and may keep the facade's
        packet/route short circuit.  Missing and legacy uncoordinated markers can
        produce a fresh owner, so callers must validate the complete invocation
        before entering ``claim_git``.
        """
        diff_hash = self._validate_hash(diff_hash)
        marker = self.directory / diff_hash
        with self._locked():
            if not marker.is_file():
                return True
            record = self._read_record(
                marker, diff_hash, migrate_bare_legacy=False,
            )
            return "coordinates" not in record

    def verdict(self, diff_hash: str) -> ReviewClaim:
        """Return one atomic, capability-free view of an existing claim."""
        diff_hash = self._validate_hash(diff_hash)
        with self._locked():
            _, record = self._record_for(diff_hash)
            return ReviewClaim(
                diff_hash, False, record["state"], record["generation"],
            )

    def claim(
        self,
        diff: str | bytes,
        *,
        coordinates: Mapping[str, object] | None = None,
    ) -> ReviewClaim:
        """Create one in-progress generation, or return a capability-free duplicate."""
        if coordinates is not None:
            coordinates = self._validate_coordinates(coordinates)
        key = review_diff_hash(diff)
        marker = self.directory / key
        with self._locked():
            if marker.is_file():
                record = self._read_record(marker, key)
                if coordinates is not None and record.get("coordinates") != coordinates:
                    raise RoutingConfigError(
                        "les coordonnées de review ne correspondent pas au claim existant ; "
                        "réservez le diff depuis les coordonnées immuables d'origine."
                    )
                return ReviewClaim(key, False, record["state"], record["generation"])
            claim_id = secrets.token_hex(32)
            record = {
                "schema_version": 1,
                "diff_hash": key,
                "state": "in_progress",
                "generation": 1,
                "claim": {"id": claim_id, "started_at": _utc_now()},
                "recoveries": [],
            }
            if coordinates is not None:
                record["coordinates"] = coordinates
            self._atomic_write(marker, record)
            return ReviewClaim(key, True, "in_progress", 1, claim_id)

    def claim_git(
        self,
        expected_hash: str,
        *,
        coordinates: Mapping[str, object],
        claim_attempt_token: str | None = None,
        replay_interrupted: bool = False,
    ) -> ReviewClaim:
        """Atomically validate and claim the exact Git diff at fixed coordinates."""
        expected_hash = self._validate_hash(expected_hash)
        coordinates = self._validate_coordinates(coordinates)
        if claim_attempt_token is not None:
            claim_attempt_token = self._validate_claim_attempt_token(
                claim_attempt_token
            )
        if type(replay_interrupted) is not bool:
            raise RoutingConfigError("indicateur de rejeu de claim booléen attendu.")
        if replay_interrupted and claim_attempt_token is None:
            raise RoutingConfigError(
                "rejeu de claim interrompu : token secret de tentative requis."
            )
        marker = self.directory / expected_hash
        with self._locked():
            legacy_record = None
            if marker.is_file():
                record = self._read_record(
                    marker, expected_hash, migrate_bare_legacy=False,
                )
                if "coordinates" in record:
                    self._assert_coordinates(record, coordinates)
                else:
                    if replay_interrupted:
                        raise RoutingConfigError(
                            "rejeu de claim interrompu refusé : aucun claim Git coordonné."
                        )
                    self._assert_quarantinable_legacy_claim(expected_hash, record)
                    self._assert_no_orphaned_acceptance_proof(expected_hash, record)
                    legacy_record = record
            elif replay_interrupted:
                raise RoutingConfigError(
                    "rejeu de claim interrompu refusé : aucune publication d'origine."
                )
            if (
                (not marker.is_file() or legacy_record is not None)
                and claim_attempt_token is None
            ):
                raise RoutingConfigError(
                    "claim Git neuf : token secret de tentative requis avant "
                    "publication."
                )
            diff = git_diff(coordinates["root"], coordinates["base"])
            actual_hash = review_diff_hash(diff)
            if actual_hash != expected_hash:
                raise RoutingConfigError(
                    f"le diff a changé avant le claim : attendu {expected_hash}, "
                    f"actuel {actual_hash} ; aucune claim créée."
                )
            if marker.is_file() and legacy_record is None:
                if replay_interrupted:
                    return self._replay_interrupted_git_claim(
                        expected_hash,
                        record,
                        coordinates,
                        claim_attempt_token,
                    )
                return ReviewClaim(
                    expected_hash, False, record["state"], record["generation"],
                )
            legacy_quarantine = None
            if legacy_record is not None:
                legacy_quarantine = self._quarantine_legacy_claim(
                    expected_hash, legacy_record,
                )
            claim_id = self._claim_id_for_attempt(
                expected_hash, coordinates, claim_attempt_token,
            )
            record = {
                "schema_version": 1,
                "diff_hash": expected_hash,
                "state": "in_progress",
                "generation": 1,
                "claim": {"id": claim_id, "started_at": _utc_now()},
                "recoveries": [],
                "coordinates": coordinates,
            }
            record["claim_publication"] = {
                "attempt_digest": hashlib.sha256(
                    claim_attempt_token.encode("ascii")
                ).hexdigest(),
                "generation": 1,
            }
            if legacy_quarantine is not None:
                record["legacy_quarantine"] = legacy_quarantine
            self._atomic_write(marker, record)
            return ReviewClaim(expected_hash, True, "in_progress", 1, claim_id)

    def _replay_interrupted_git_claim(
        self,
        diff_hash: str,
        record: dict,
        coordinates: Mapping[str, str],
        claim_attempt_token: str,
    ) -> ReviewClaim:
        """Reconstruct an interrupted claim capability without another write."""
        publication = record.get("claim_publication")
        if (
            not isinstance(publication, Mapping)
            or set(publication) != {"attempt_digest", "generation"}
            or not hmac.compare_digest(
                str(publication.get("attempt_digest")),
                hashlib.sha256(claim_attempt_token.encode("ascii")).hexdigest(),
            )
        ):
            raise RoutingConfigError(
                "rejeu de claim interrompu refusé : token de tentative inconnu."
            )
        if (
            record.get("state") != "in_progress"
            or record.get("generation") != publication.get("generation")
            or record.get("acceptance_proof_id") is not None
        ):
            raise RoutingConfigError(
                "rejeu de claim interrompu refusé : la tentative d'origine n'est "
                "plus active."
            )
        claim = record.get("claim")
        if not isinstance(claim, Mapping):
            raise RoutingConfigError(
                "publication de claim invalide ; intervention humaine requise."
            )
        claim_id = self._claim_id_for_attempt(
            diff_hash, coordinates, claim_attempt_token,
        )
        if not hmac.compare_digest(str(claim.get("id")), claim_id):
            raise RoutingConfigError(
                "publication de claim invalide ; intervention humaine requise."
            )
        return ReviewClaim(
            diff_hash, True, "in_progress", record["generation"], claim_id,
        )

    def active_git_claim(
        self,
        expected_hash: str,
        claim_id: str,
        *,
        coordinates: Mapping[str, object],
    ) -> ReviewClaim:
        """Validate one active owner against its fixed current Git diff."""
        expected_hash = self._validate_hash(expected_hash)
        claim_id = self._validate_claim_id(claim_id)
        coordinates = self._validate_coordinates(coordinates)
        with self._locked():
            _, record = self._record_for(expected_hash)
            self._assert_active_generation(record, claim_id)
            self._assert_coordinates(record, coordinates)
            actual_hash = review_diff_hash(
                git_diff(coordinates["root"], coordinates["base"])
            )
            if actual_hash != expected_hash:
                raise RoutingConfigError(
                    f"le diff a changé depuis le claim : attendu {expected_hash}, "
                    f"actuel {actual_hash}. Réservez le nouveau diff avant de relancer "
                    "un reviewer."
                )
            return ReviewClaim(
                expected_hash, True, "in_progress", record["generation"], claim_id,
            )

    @classmethod
    def _assert_coordinates(
        cls,
        record: Mapping[str, object],
        coordinates: Mapping[str, object],
    ) -> None:
        if record.get("coordinates") != coordinates:
            raise RoutingConfigError(
                "les coordonnées immuables ne correspondent pas au claim actif ; "
                "aucune récupération n'a été créée."
            )

    def _assert_no_orphaned_acceptance_proof(
        self,
        diff_hash: str,
        record: Mapping[str, object],
    ) -> None:
        """Reject recovery when an interrupted proof already attests this owner."""
        claim = record.get("claim")
        claim_id = claim.get("id") if isinstance(claim, Mapping) else None
        self._validate_claim_id(claim_id)
        generation = record.get("generation")
        if type(generation) is not int or generation < 1:
            raise RoutingConfigError(
                f"ledger de review invalide pour {diff_hash} ; intervention humaine requise."
            )
        namespace = hashlib.sha256(self.__repository.encode("utf-8")).hexdigest()
        directory = self.__state_dir / "acceptance-proofs" / namespace
        if not directory.exists():
            return
        if not directory.is_dir():
            raise RoutingConfigError(
                "preuve AC incomplète ou illisible ; récupération de review refusée."
            )
        claim_digest = hashlib.sha256(claim_id.encode("ascii")).hexdigest()
        try:
            markers = tuple(directory.iterdir())
        except OSError as exc:
            raise RoutingConfigError(
                "preuve AC incomplète ou illisible ; récupération de review refusée."
            ) from exc
        for marker in markers:
            if not marker.is_file():
                raise RoutingConfigError(
                    "preuve AC incomplète ou illisible ; récupération de review refusée."
                )
            proof = AcceptanceProofStore._validated_proof(marker)
            if (
                proof["coordinates"]["diff_hash"] == diff_hash
                and proof["review"]["generation"] == generation
                and proof["review"]["claim_digest"] == claim_digest
                and record.get("acceptance_proof_id") != proof["proof_id"]
            ):
                raise RoutingConfigError(
                    "une preuve de review incomplète interdit toute récupération ; "
                    "intervention humaine requise."
                )

    def _assert_recoverable(
        self,
        diff_hash: str,
        record: Mapping[str, object],
        generation: int,
        claim_id: str,
        coordinates: Mapping[str, object],
    ) -> None:
        if record["state"] == "completed":
            raise RoutingConfigError(
                "une review terminée est terminale et ne peut pas être récupérée."
            )
        if record["generation"] != generation:
            raise RoutingConfigError(
                "cette génération de claim n'est plus active ; rechargez le verdict "
                "commun avant toute nouvelle récupération."
            )
        self._assert_active_generation(record, claim_id)
        if record.get("acceptance_proof_id") is not None:
            raise RoutingConfigError(
                "une preuve de review incomplète interdit toute récupération ; "
                "intervention humaine requise."
            )
        self._assert_no_orphaned_acceptance_proof(diff_hash, record)
        self._assert_coordinates(record, coordinates)

    def assert_coordinates(
        self,
        diff_hash: str,
        *,
        coordinates: Mapping[str, object],
    ) -> None:
        """Refuse use of a claim from coordinates other than its original review."""
        diff_hash = self._validate_hash(diff_hash)
        coordinates = self._validate_coordinates(coordinates)
        with self._locked():
            _, record = self._record_for(diff_hash)
            self._assert_coordinates(record, coordinates)

    def assert_recoverable(
        self,
        diff_hash: str,
        generation: int,
        claim_id: str,
        *,
        coordinates: Mapping[str, object],
    ) -> None:
        """Read-only preflight for an exact, coordinate-bound recovery CAS."""
        diff_hash = self._validate_hash(diff_hash)
        claim_id = self._validate_claim_id(claim_id)
        if type(generation) is not int or generation < 0:
            raise RoutingConfigError("récupération de review : génération positive attendue.")
        coordinates = self._validate_coordinates(coordinates)
        with self._locked():
            _, record = self._record_for(diff_hash)
            self._assert_recoverable(diff_hash, record, generation, claim_id, coordinates)

    def recover(
        self,
        diff_hash: str,
        generation: int,
        claim_id: str,
        reason: str,
        *,
        coordinates: Mapping[str, object],
    ) -> ReviewClaim:
        """Atomically verify and recover one explicitly abandoned generation.

        The current diff is read while the ledger lock is held.  Splitting the
        owner/coordinate checks, diff validation, and generation replacement
        would let recovery race a diff drift or a former owner's read.
        """
        diff_hash = self._validate_hash(diff_hash)
        claim_id = self._validate_claim_id(claim_id)
        if type(generation) is not int or generation < 0:
            raise RoutingConfigError("récupération de review : génération positive attendue.")
        if not isinstance(reason, str) or not reason.strip():
            raise RoutingConfigError("récupération de review : raison humaine non vide requise.")
        reason = reason.strip()
        coordinates = self._validate_coordinates(coordinates)
        with self._locked():
            marker, record = self._record_for(diff_hash)
            self._assert_recoverable(diff_hash, record, generation, claim_id, coordinates)
            verified = git_diff(coordinates["root"], coordinates["base"])
            if review_diff_hash(verified) != diff_hash:
                raise RoutingConfigError(
                    "le diff a changé depuis le claim ; aucune récupération n'a été créée."
                )
            new_claim_id = secrets.token_hex(32)
            recovered_at = _utc_now()
            recoveries = list(record["recoveries"])
            recoveries.append({
                "abandoned_claim_id": claim_id,
                "reason": reason,
                "recovered_at": recovered_at,
            })
            record.update({
                "generation": record["generation"] + 1,
                "claim": {"id": new_claim_id, "started_at": recovered_at},
                "recoveries": recoveries,
            })
            self._atomic_write(marker, record)
            return ReviewClaim(
                diff_hash, True, "in_progress", record["generation"], new_claim_id,
            )

    def complete(self, diff_hash: str, claim_id: str) -> dict[str, object]:
        """Refuse legacy proof-free terminalization without touching the ledger."""
        del diff_hash, claim_id
        raise RoutingConfigError(
            "terminalisation de review sans preuve AC structurée interdite ; "
            "utilisez record-review-proof."
        )

    def complete_with_proof(self, diff_hash: str, claim_id: str, proof_id: str) -> dict[str, object]:
        """Refuse an identifier-only terminalization surface.

        ``AcceptanceProofStore.create`` is the sole terminal path.  It
        validates the complete proof, active owner, immutable coordinates, and
        current diff in one ledger critical section before writing the binding.
        """
        del diff_hash, claim_id, proof_id
        raise RoutingConfigError(
            "terminalisation de review par identifiant de preuve interdite ; "
            "utilisez record-review-proof."
        )

    def completed_proof_id(self, diff_hash: str) -> str | None:
        binding = self.completed_proof_binding(diff_hash)
        return binding["proof_id"] if binding is not None else None

    def completed_proof_binding(self, diff_hash: str) -> dict[str, object] | None:
        """Return the redacted claim coordinates bound to a completed proof."""
        diff_hash = self._validate_hash(diff_hash)
        with self._locked():
            _, record = self._record_for(diff_hash)
            proof_id = record.get("acceptance_proof_id")
            if record.get("state") != "completed" or proof_id is None:
                return None
            if (not isinstance(proof_id, str) or len(proof_id) != 64 or
                    any(character not in "0123456789abcdef" for character in proof_id)):
                raise RoutingConfigError(
                    f"ledger de review invalide pour {diff_hash} ; intervention humaine requise."
                )
            generation = record.get("generation")
            claim = record.get("claim")
            claim_id = claim.get("id") if isinstance(claim, Mapping) else None
            completed_at = record.get("completed_at")
            coordinates = record.get("coordinates")
            if type(generation) is not int or generation < 1:
                raise RoutingConfigError(
                    f"ledger de review invalide pour {diff_hash} ; intervention humaine requise."
                )
            self._validate_claim_id(claim_id)
            if not self._valid_utc_timestamp(completed_at):
                raise RoutingConfigError(
                    f"ledger de review invalide pour {diff_hash} ; intervention humaine requise."
                )
            coordinates = self._validate_coordinates(coordinates)
            return {
                "proof_id": proof_id,
                "generation": generation,
                "claim_digest": hashlib.sha256(claim_id.encode("ascii")).hexdigest(),
                "completed_at": completed_at,
                "coordinates": coordinates,
            }

    def validated_terminal_proof_binding(
        self,
        issue_id: str,
        diff_hash: str,
        *,
        coordinates: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        """Return intact terminal evidence without upgrading a blocked verdict."""
        if not isinstance(issue_id, str) or not issue_id.strip():
            raise RoutingConfigError("preuve AC : identifiant d'issue requis.")
        binding = self.completed_proof_binding(diff_hash)
        if binding is None:
            return None
        if coordinates is not None:
            expected_coordinates = self._validate_coordinates(coordinates)
            if binding["coordinates"] != expected_coordinates:
                raise RoutingConfigError(
                    "preuve AC issue de coordonnées de review différentes ; refus fermé."
                )
        proof_store = AcceptanceProofStore(self.__repository, self.__state_dir)
        proof = proof_store._validated_proof(proof_store.directory / binding["proof_id"])
        if (
            proof["issue"]["id"] != issue_id.strip()
            or proof["coordinates"]["diff_hash"] != diff_hash
            or proof["coordinates"]["base"] != binding["coordinates"]["base"]
            or proof["review"]["generation"] != binding["generation"]
            or proof["review"]["claim_digest"] != binding["claim_digest"]
        ):
            raise RoutingConfigError(
                "preuve AC incohérente avec l'issue ou la review terminée ; refus fermé."
            )
        return {
            **binding,
            "quality": proof["quality"],
            "all_pass": all(
                criterion["verdict"] == "pass"
                for criterion in proof["issue"]["criteria"]
            ),
        }

    def validated_completed_proof_binding(
        self, issue_id: str, diff_hash: str,
    ) -> dict[str, object] | None:
        """Return a binding only for an intact, mergeable, all-pass terminal proof."""
        binding = self.validated_terminal_proof_binding(issue_id, diff_hash)
        if binding is None:
            return None
        if binding["quality"] != "mergeable" or not binding["all_pass"]:
            raise RoutingConfigError(
                "preuve AC non mergeable ou incomplète ; refus fermé."
            )
        return binding

    def assert_active(self, diff_hash: str, claim_id: str) -> None:
        """Reject stale/recovered/completed reviewer generations."""
        diff_hash = self._validate_hash(diff_hash)
        claim_id = self._validate_claim_id(claim_id)
        with self._locked():
            _, record = self._record_for(diff_hash)
            self._assert_active_generation(record, claim_id)

    def active_claim(self, diff_hash: str, claim_id: str) -> ReviewClaim:
        """Return the current generation only to its capability-bearing owner."""
        diff_hash = self._validate_hash(diff_hash)
        claim_id = self._validate_claim_id(claim_id)
        with self._locked():
            _, record = self._record_for(diff_hash)
            self._assert_active_generation(record, claim_id)
            return ReviewClaim(
                diff_hash, True, "in_progress", record["generation"], claim_id,
            )


class AcceptanceProofStore:
    """Append-only, private AC review evidence keyed by repository and proof digest."""

    def __init__(self, repository: str, state_dir: str | os.PathLike | None = None):
        repository = _non_empty_string(repository, "namespace de dépôt")
        self.__repository = repository
        namespace = hashlib.sha256(repository.encode("utf-8")).hexdigest()
        base = Path(state_dir) if state_dir is not None else Path(registry.data_dir())
        self.__state_dir = base
        self.directory = base / "acceptance-proofs" / namespace

    def _write(self, proof: dict) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        marker = self.directory / proof["proof_id"]
        if marker.exists():
            raise RoutingConfigError("preuve AC déjà présente ; réutilisez son identifiant.")
        fd, temporary = tempfile.mkstemp(prefix=".proof.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(proof, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, marker)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return marker
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @staticmethod
    def _load(marker: Path) -> dict:
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RoutingConfigError("preuve AC illisible ou JSON invalide ; refus fermé.") from exc
        if not isinstance(value, dict):
            raise RoutingConfigError("preuve AC invalide ; refus fermé.")
        return value

    @staticmethod
    def _is_lower_hex(value: object, length: int) -> bool:
        return (
            isinstance(value, str) and len(value) == length and
            all(character in "0123456789abcdef" for character in value)
        )

    @classmethod
    def _validated_proof(cls, marker: Path) -> dict:
        """Load one canonical proof and reject hostile data before matching it."""
        proof = cls._load(marker)
        if set(proof) != {
            "schema_version", "proof_id", "issue", "review", "coordinates", "quality",
        }:
            raise RoutingConfigError("preuve AC legacy ou incomplète ; refus fermé.")
        if (proof.get("schema_version") != 1 or
                not cls._is_lower_hex(marker.name, 64) or
                proof.get("proof_id") != marker.name):
            raise RoutingConfigError("preuve AC malformée ; refus fermé.")

        canonical = dict(proof)
        proof_id = canonical.pop("proof_id")
        calculated_id = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if calculated_id != proof_id:
            raise RoutingConfigError("intégrité de preuve AC invalide ; refus fermé.")

        issue = proof.get("issue")
        review = proof.get("review")
        coordinates = proof.get("coordinates")
        if (not isinstance(issue, dict) or
                set(issue) != {"id", "ac_digest", "criteria"} or
                not isinstance(issue.get("id"), str) or not issue["id"].strip() or
                not cls._is_lower_hex(issue.get("ac_digest"), 64) or
                not isinstance(issue.get("criteria"), list)):
            raise RoutingConfigError("preuve AC malformée ; refus fermé.")
        for index, criterion in enumerate(issue["criteria"], start=1):
            if (not isinstance(criterion, dict) or
                    set(criterion) != {"id", "digest", "verdict"} or
                    criterion.get("id") != f"ac-{index}" or
                    not cls._is_lower_hex(criterion.get("digest"), 64) or
                    criterion.get("verdict") not in {
                        "pass", "fail", "not_covered", "contradicted",
                    }):
                raise RoutingConfigError("preuve AC malformée ; refus fermé.")
        if (not isinstance(review, dict) or
                set(review) != {"role", "generation", "claim_digest"} or
                review.get("role") != "reviewer" or
                type(review.get("generation")) is not int or review["generation"] < 1 or
                not cls._is_lower_hex(review.get("claim_digest"), 64)):
            raise RoutingConfigError("preuve AC malformée ; refus fermé.")
        if (not isinstance(coordinates, dict) or
                set(coordinates) != {"head", "diff_hash", "base"} or
                not cls._is_lower_hex(coordinates.get("head"), 40) or
                not cls._is_lower_hex(coordinates.get("diff_hash"), 64) or
                not isinstance(coordinates.get("base"), str) or
                not coordinates["base"].strip() or
                proof.get("quality") not in {"mergeable", "blocked"}):
            raise RoutingConfigError("preuve AC malformée ; refus fermé.")
        return proof

    def _reuse_or_write_canonical(self, proof: dict) -> tuple[Path, bool]:
        """Return a pre-crash proof only when it is byte-for-byte canonical."""
        marker = self.directory / proof["proof_id"]
        if not marker.exists():
            return self._write(proof), True
        persisted = self._load(marker)
        persisted_id = persisted.get("proof_id") if isinstance(persisted, dict) else None
        canonical = dict(persisted)
        canonical.pop("proof_id", None)
        if (persisted_id != proof["proof_id"] or
                hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                != persisted_id or persisted != proof):
            raise RoutingConfigError(
                "preuve AC persistante non canonique ou altérée ; refus fermé."
            )
        return marker, False

    def create(self, *, issue_id: str, issue_body: str, reviewer_role: str,
               outcomes: object, quality: object, diff_hash: str, claim_id: str,
               root: str | os.PathLike | None = None, base: str | None = None,
               state_dir: str | os.PathLike | None = None) -> dict:
        """Validate structured reviewer outcomes while its exact claim is still active."""
        if not self._is_lower_hex(base, 40):
            raise RoutingConfigError(
                "preuve AC : SHA de base Git exact sur 40 hex requis ; refus fermé."
            )
        if reviewer_role != "reviewer":
            raise RoutingConfigError("preuve AC : seul le rôle reviewer peut attester.")
        if not isinstance(issue_id, str) or not issue_id.strip():
            raise RoutingConfigError("preuve AC : identifiant d'issue requis.")
        expected = acceptance_criteria(issue_body)
        if not isinstance(outcomes, list) or len(outcomes) != len(expected):
            raise RoutingConfigError("preuve AC : verdict structuré complet requis.")
        normalized = []
        for criterion, outcome in zip(expected, outcomes):
            if (not isinstance(outcome, dict) or set(outcome) != {"id", "digest", "verdict"} or
                    outcome.get("id") != criterion["id"] or outcome.get("digest") != criterion["digest"] or
                    outcome.get("verdict") not in {"pass", "fail", "not_covered", "contradicted"}):
                raise RoutingConfigError("preuve AC : verdict ou digest invalide ; refus fermé.")
            normalized.append(dict(outcome))
        if quality not in {"mergeable", "blocked"}:
            raise RoutingConfigError("preuve AC : verdict qualité invalide ; refus fermé.")
        ledger = ReviewDeduplicator(self._repository, state_dir)
        if root is None:
            raise RoutingConfigError(
                "preuve AC : root exact du claim de review requis ; refus fermé."
            )
        coordinates = ReviewDeduplicator._validate_coordinates({
            "root": str(Path(root).resolve()), "base": base,
        })
        # Keep the ledger lock across both durable writes.  On a failed ledger
        # write, remove the newly-created proof so it cannot poison a later one.
        with ledger._locked():
            marker, record = ledger._record_for(diff_hash)
            ledger._assert_active_generation(record, claim_id)
            ledger._assert_coordinates(record, coordinates)
            verified = git_diff(root, base)
            if review_diff_hash(verified) != diff_hash:
                raise RoutingConfigError(
                    "le diff a changé depuis le claim : réservez le nouveau diff avant de relancer un reviewer."
                )
            proof = {
                "schema_version": 1,
                "issue": {"id": issue_id.strip(), "ac_digest": acceptance_digest(expected),
                          "criteria": normalized},
                "review": {"role": reviewer_role, "generation": record["generation"],
                           "claim_digest": hashlib.sha256(claim_id.encode("ascii")).hexdigest()},
                "coordinates": {"head": git_head(root), "diff_hash": review_diff_hash(verified),
                                "base": coordinates["base"]},
                "quality": quality,
            }
            proof["proof_id"] = hashlib.sha256(
                json.dumps(proof, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            persisted = None
            created = False
            try:
                persisted, created = self._reuse_or_write_canonical(proof)
                record["state"] = "completed"
                record["completed_at"] = _utc_now()
                record["acceptance_proof_id"] = proof["proof_id"]
                ledger._atomic_write(marker, record)
            except Exception:
                if created and persisted is not None:
                    try:
                        persisted.unlink()
                    except FileNotFoundError:
                        pass
                raise
        return {"proof_id": proof["proof_id"], "issue": proof["issue"]["id"],
                "diff_hash": proof["coordinates"]["diff_hash"], "generation": record["generation"]}

    @property
    def _repository(self) -> str:
        # The namespace is deliberately one-way on disk; retain no public path identity.
        # The caller supplies the same repository to all verification operations.
        # Set by __init__ rather than attempting to recover it from a hashed directory.
        return self.__repository

    def current_for_sync(
        self, *, issue_id: str, issue_body: str, head: str, diff: bytes, base: str,
    ) -> dict:
        """Find the completed proof bound to the exact current issue and diff."""
        if not self._is_lower_hex(base, 40):
            raise RoutingConfigError(
                "preuve AC : SHA de base Git exact sur 40 hex requis ; refus fermé."
            )
        expected = acceptance_criteria(issue_body)
        expected_digest = acceptance_digest(expected)
        expected_diff_hash = review_diff_hash(diff)
        if not self.directory.exists():
            raise RoutingConfigError("aucune preuve AC disponible ; override humain requis.")
        binding = ReviewDeduplicator(
            self._repository, self.__state_dir,
        ).completed_proof_binding(expected_diff_hash)
        matches = []
        for marker in self.directory.iterdir():
            if not marker.is_file() or marker.name.startswith("."):
                continue
            proof = self._validated_proof(marker)
            issue = proof["issue"]
            coordinates = proof["coordinates"]
            review = proof["review"]
            if issue.get("id") != issue_id or issue.get("ac_digest") != expected_digest:
                continue
            proof_criteria = issue.get("criteria", [])
            if (len(proof_criteria) != len(expected) or any(
                outcome.get("id") != criterion["id"]
                or outcome.get("digest") != criterion["digest"]
                for criterion, outcome in zip(expected, proof_criteria)
            )):
                raise RoutingConfigError(
                    "preuve AC incohérente avec les critères courants."
                )
            if (coordinates.get("head") != head or
                    coordinates.get("diff_hash") != expected_diff_hash or
                    coordinates.get("base") != base):
                continue

            # A crash can leave a canonical proof from an abandoned generation.
            # It is safe to ignore only after full schema/integrity validation and
            # only when the completed ledger binding points at another proof.
            if binding is None or proof["proof_id"] != binding["proof_id"]:
                continue
            if (review["generation"] != binding["generation"] or
                    review["claim_digest"] != binding["claim_digest"]):
                raise RoutingConfigError("preuve AC incohérente avec la review terminée.")
            matches.append(proof)
        if len(matches) != 1:
            raise RoutingConfigError("preuve AC absente, périmée ou ambiguë ; override humain requis.")
        return matches[0]

    def valid_for_merge(
        self, *, issue_id: str, issue_body: str, head: str, diff: bytes, base: str,
    ) -> dict:
        """Find one mergeable all-pass proof on the exact current coordinates."""
        proof = self.current_for_sync(
            issue_id=issue_id, issue_body=issue_body, head=head, diff=diff, base=base,
        )
        expected = acceptance_criteria(issue_body)
        if (proof.get("quality") != "mergeable" or
                proof["issue"]["criteria"] != [
                    {**item, "verdict": "pass"} for item in expected
                ]):
            raise RoutingConfigError("preuve AC non mergeable ou incomplète ; refus fermé.")
        return proof


def claimed_review_diff(
    expected_hash: str,
    repository: str,
    *,
    claim_id: str,
    root: str | os.PathLike | None = None,
    base: str | None = None,
    state_dir: str | os.PathLike | None = None,
    consume: Callable[[bytes], _ReviewDiffConsumer] | None = None,
) -> _ReviewDiffConsumer:
    """Consume one verified review diff while its ownership remains active.

    The consumer runs before releasing the ledger lock.  It is intentionally
    mandatory: returning bytes would free the lock before a CLI or reviewer
    could emit them, allowing recovery to replace that owner in between.
    """
    if not callable(consume):
        raise RoutingConfigError(
            "lecture de review : consommateur synchrone de bytes requis ; refus fermé."
        )
    deduplicator = ReviewDeduplicator(repository, state_dir)
    if root is None:
        raise RoutingConfigError(
            "lecture de review : root exact du claim requis ; refus fermé."
        )
    coordinates = ReviewDeduplicator._validate_coordinates({
        "root": str(Path(root).resolve()), "base": base,
    })
    # Retain the lock through every read that authorizes emitted bytes.  A
    # recovery cannot replace the owner between the capability check and
    # ``git diff`` while an old reviewer is still receiving output.
    with deduplicator._locked():
        _, record = deduplicator._record_for(expected_hash)
        deduplicator._assert_active_generation(record, claim_id)
        deduplicator._assert_coordinates(record, coordinates)
        diff = git_diff(root, base)
        actual_hash = review_diff_hash(diff)
        if actual_hash != expected_hash:
            raise RoutingConfigError(
                f"le diff a changé depuis le claim : attendu {expected_hash}, actuel "
                f"{actual_hash}. Réservez le nouveau diff avant de relancer un reviewer."
            )
        return consume(diff)


def _user_request(args) -> UserRouteRequest:
    return UserRouteRequest(
        tier=args.tier,
        model=args.model,
        effort=args.effort,
        technical_remediation=getattr(args, "technical_remediation", False),
        technical_remediation_id=getattr(args, "technical_remediation_id", None),
    )


def _policy_payload(policy: RoutingPolicy, routes: Iterable[ResolvedRoute]) -> dict:
    return {
        "schema_version": 1,
        "project_root": str(policy.root),
        "project_config": str(policy.config_path) if policy.has_project_config else None,
        "levels": list(LEVELS),
        "gate_floors": dict(GATE_FLOORS),
        "gate_effort_floors": dict(GATE_EFFORT_FLOORS),
        "effort_scopes": {host: {family: {"version": scope.version, "levels": list(scope.levels), "inadmissible": dict(scope.inadmissible)} for family, scope in families.items()} for host, families in policy.effort_scopes.items()},
        "routes": [route.to_dict() for route in routes],
    }


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("entier positif attendu") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("entier positif attendu")
    return parsed


def main(
    argv: list[str] | None = None, *, handle_config_errors: bool = True,
) -> None:
    from foundry.escalation import (
        FAILURE_KINDS,
        MAX_DIAGNOSTIC_ATTEMPTS,
        RESUME_REASON_CODES,
        RISK_FLOORS,
        EscalationStore,
    )

    parser = argparse.ArgumentParser(description="Resolve Foundry's semantic model routing policy")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="print the effective read-only routing policy")
    show.add_argument("--host", choices=HOSTS)
    show.add_argument("--root")
    resolve_parser = sub.add_parser("resolve", help="resolve one delegated role")
    resolve_parser.add_argument("role", choices=tuple(ROLE_DEFAULTS))
    resolve_parser.add_argument("--host", choices=HOSTS, required=True)
    resolve_parser.add_argument("--root")
    resolve_parser.add_argument("--tier", choices=LEVELS)
    resolve_parser.add_argument("--model")
    resolve_parser.add_argument("--effort")
    resolve_parser.add_argument("--available-model", action="append", dest="available_models")
    resolve_parser.add_argument("--host-override", action="append", default=[])
    claim = sub.add_parser("claim-review", help="atomically claim one exact diff hash")
    claim.add_argument(
        "--git-diff", action="store_true", required=True,
        help="claim the exact Git base...HEAD diff",
    )
    claim.add_argument("--base", required=True, help="immutable 40-hex Git base SHA")
    claim.add_argument("--repository", help="repo namespace; defaults to origin URL")
    claim.add_argument("--root", required=True, help="exact Git worktree root")
    claim.add_argument(
        "--issue", required=True,
        help="issue id whose reviewer escalation preflight must pass before the claim",
    )
    claim.add_argument(
        "--claim-attempt-token",
        help=(
            "token secret hexadécimal de 256 bits généré avant le claim ; requis "
            "pour rejouer une publication interrompue"
        ),
    )
    claim.add_argument(
        "--replay-interrupted", action="store_true",
        help="reconstruit sans écriture le claim publié dont la capability n'a pas été reçue",
    )
    recover_review = sub.add_parser(
        "recover-review", help="explicitly replace one abandoned review generation",
    )
    recover_review.add_argument("--diff-hash", required=True)
    recover_review.add_argument("--generation", required=True, type=_positive_int)
    recover_review.add_argument(
        "--claim-id", required=True,
        help="active claim capability being explicitly abandoned",
    )
    recover_review.add_argument("--reason", required=True)
    recover_review.add_argument("--repository", help="repo namespace; defaults to origin URL")
    recover_review.add_argument("--root", required=True)
    recover_review.add_argument(
        "--base", required=True,
        help="immutable Git base used by the original review claim",
    )
    complete_review = sub.add_parser(
        "complete-review", help="deprecated: refuses proof-free review terminalization",
    )
    complete_review.add_argument("--diff-hash", required=True)
    complete_review.add_argument("--claim-id", required=True)
    complete_review.add_argument("--repository", help="repo namespace; defaults to origin URL")
    complete_review.add_argument("--root")
    record_proof = sub.add_parser(
        "record-review-proof", help="record structured AC evidence for an active review claim",
    )
    record_proof.add_argument("--issue", required=True)
    record_proof.add_argument("--diff-hash", required=True)
    record_proof.add_argument("--claim-id", required=True)
    record_proof.add_argument("--outcomes-file", required=True,
                              help="JSON object with outcomes[] and quality only")
    record_proof.add_argument("--base", required=True, help="immutable 40-hex Git base SHA")
    record_proof.add_argument("--repository", help="repo namespace; defaults to origin URL")
    record_proof.add_argument("--root", required=True, help="exact Git worktree root")
    read_review = sub.add_parser(
        "read-review", help="emit only a currently claimed and hash-matched diff",
    )
    read_review.add_argument("--diff-hash", required=True)
    read_review.add_argument("--claim-id", required=True)
    read_review.add_argument("--base", required=True, help="immutable 40-hex Git base SHA")
    read_review.add_argument("--repository", help="repo namespace; defaults to origin URL")
    read_review.add_argument("--root", required=True, help="exact Git worktree root")
    codex_plan = sub.add_parser(
        "codex-plan", help="build an explicit fresh-context Codex delegation plan",
    )
    codex_plan.add_argument(
        "role", choices=("scout", "implementer", "reviewer", "architect"),
    )
    codex_plan.add_argument(
        "--packet-file", required=True, help="bounded task packet path, or - for stdin",
    )
    codex_plan.add_argument("--root")
    codex_plan.add_argument("--tier", choices=LEVELS)
    codex_plan.add_argument("--model")
    codex_plan.add_argument("--effort")
    codex_plan.add_argument("--available-model", action="append", dest="available_models")
    codex_plan.add_argument("--profile-model-active", action="store_true")
    codex_plan.add_argument("--profile-effort-active", action="store_true")
    codex_plan.add_argument(
        "--task-name",
        help=(
            "optional lowercase execution-name prefix; Foundry appends a unique "
            "bounded suffix before passing it to the Codex host"
        ),
    )
    codex_plan.add_argument(
        "--issue",
        help="issue id for persistent escalation state; required for reviewer plans",
    )
    codex_plan.add_argument(
        "--technical-remediation", action="store_true",
        help=(
            "utilise seulement une route locale déjà réclamée pour l'issue ; "
            "ne confère aucune autorité provider ou de campagne"
        ),
    )
    codex_plan.add_argument(
        "--technical-remediation-id",
        help=(
            "identité stable de la route locale déjà réclamée ; requise avec "
            "--technical-remediation"
        ),
    )
    codex_plan.add_argument(
        "--no-subagent", action="store_true",
        help="return the explicit current-context fallback instead of spawn arguments",
    )
    codex_plan.add_argument(
        "--git-diff", action="store_true", help="claim base...HEAD before a reviewer plan",
    )
    codex_plan.add_argument("--base", help="git base ref; defaults to origin/<default>")
    codex_plan.add_argument(
        "--diff-hash",
        help="exact recovered reviewer hash; requires --claim-id and --git-diff",
    )
    codex_plan.add_argument(
        "--claim-id",
        help="active recovered reviewer capability; requires --diff-hash and --git-diff",
    )
    codex_plan.add_argument(
        "--claim-attempt-token",
        help=(
            "token secret généré avant le claim reviewer ; réutiliser "
            "avec --replay-interrupted après une publication interrompue"
        ),
    )
    codex_plan.add_argument(
        "--replay-interrupted", action="store_true",
        help="reconstruit sans écriture une publication de claim reviewer interrompue",
    )
    escalation = sub.add_parser(
        "escalation", help="record or inspect deterministic issue escalation state",
    )
    escalation_actions = escalation.add_subparsers(dest="escalation_action", required=True)
    escalation_show = escalation_actions.add_parser("show")
    escalation_show.add_argument("issue")
    escalation_show.add_argument("--root")
    escalation_resume = escalation_actions.add_parser(
        "resume", help="clear a human stop with a non-secret human audit reason",
    )
    escalation_resume.add_argument("issue")
    escalation_resume.add_argument(
        "--reason", required=True,
        metavar="{" + ",".join(RESUME_REASON_CODES) + "}",
        help=("public human audit code; never include a secret; allowed codes: "
              + ", ".join(RESUME_REASON_CODES)),
    )
    escalation_resume.add_argument("--halt-generation", required=True, type=_positive_int)
    escalation_resume.add_argument(
        "--remediation-credits", type=_positive_int, metavar="1..3",
        help="autorise 1 à 3 reprises de correction après review bloquante pour le rôle arrêté",
    )
    escalation_resume.add_argument("--root")
    escalation_rearm = escalation_actions.add_parser(
        "rearm-remediation",
        help="réarme avec CAS une fenêtre de remédiation exactement épuisée",
    )
    escalation_rearm.add_argument("issue")
    escalation_rearm.add_argument("role", choices=tuple(ROLE_DEFAULTS))
    escalation_rearm.add_argument(
        "--reason", required=True,
        metavar="{" + ",".join(RESUME_REASON_CODES) + "}",
        help=("code public d'autorisation humaine ; ne jamais inclure de secret ; "
              "codes autorisés : " + ", ".join(RESUME_REASON_CODES)),
    )
    escalation_rearm.add_argument(
        "--halt-generation", required=True, type=_positive_int,
    )
    escalation_rearm.add_argument(
        "--current-halt-generation", type=_positive_int,
        help=("génération technique observée après le diagnostic local ; requise "
              "si elle diffère de l'autorisation épuisée"),
    )
    escalation_rearm.add_argument(
        "--remediation-credits", required=True, type=_positive_int, metavar="1..3",
        help="accorde une nouvelle fenêtre bornée de 1 à 3 corrections",
    )
    escalation_rearm.add_argument("--root")
    escalation_cancel = escalation_actions.add_parser(
        "cancel-remediation", help="annule avec CAS une fenêtre de remédiation active",
    )
    escalation_cancel.add_argument("issue")
    escalation_cancel.add_argument("--halt-generation", required=True, type=_positive_int)
    escalation_cancel.add_argument("--root")
    escalation_failure = escalation_actions.add_parser("failure")
    escalation_failure.add_argument("issue")
    escalation_failure.add_argument("role", choices=tuple(ROLE_DEFAULTS))
    escalation_failure.add_argument(
        "--kind", required=True,
        choices=FAILURE_KINDS,
    )
    escalation_failure.add_argument("--current-tier", required=True, choices=LEVELS)
    escalation_failure.add_argument(
        "--idempotency-key", required=True,
        help=(
            "identifiant stable du même échec observé ; un rejeu doit réutiliser "
            "exactement cette valeur"
        ),
    )
    escalation_failure.add_argument(
        "--base",
        help=("SHA Git figé requis pour consommer un crédit après "
              "review_blocking_after_fix"),
    )
    escalation_failure.add_argument(
        "--repository",
        help="namespace du dépôt de review ; par défaut, identité du root Git",
    )
    escalation_failure.add_argument("--root")
    escalation_risk = escalation_actions.add_parser("risk")
    escalation_risk.add_argument("issue")
    escalation_risk.add_argument("role", choices=tuple(ROLE_DEFAULTS))
    escalation_risk.add_argument(
        "--kind", required=True, choices=tuple(RISK_FLOORS),
    )
    escalation_risk.add_argument("--current-tier", required=True, choices=LEVELS)
    escalation_risk.add_argument("--root")
    escalation_request = escalation_actions.add_parser("request")
    escalation_request.add_argument("issue")
    escalation_request.add_argument("role", choices=tuple(ROLE_DEFAULTS))
    escalation_request.add_argument("--current-tier", required=True, choices=LEVELS)
    escalation_request.add_argument("--root")
    escalation_verdict = escalation_actions.add_parser(
        "verdict", help="journalise une demande humaine explicitement catégorisée",
    )
    escalation_verdict.add_argument("issue")
    escalation_verdict.add_argument("role", choices=tuple(ROLE_DEFAULTS))
    escalation_verdict.add_argument("--current-tier", required=True, choices=LEVELS)
    escalation_verdict.add_argument(
        "--category", required=True,
        choices=("strategy_decision", "product_decision", "durable_ambiguity"),
    )
    escalation_verdict.add_argument("--diagnostic-attempts", type=_positive_int)
    escalation_verdict.add_argument("--diagnostic-digest")
    escalation_verdict.add_argument("--root")
    escalation_reclassify = escalation_actions.add_parser(
        "reclassify-legacy-terminal",
        help="classe un arrêt v1 sans le relâcher",
    )
    escalation_reclassify.add_argument("issue")
    escalation_reclassify.add_argument(
        "--halt-generation", required=True, type=_positive_int,
    )
    escalation_reclassify.add_argument("--root")
    escalation_technical_resume = escalation_actions.add_parser(
        "resume-technical",
        help="ouvre un diagnostic technique local borné sans verdict humain",
    )
    escalation_technical_resume.add_argument("issue")
    escalation_technical_resume.add_argument(
        "--halt-generation", required=True, type=_positive_int,
    )
    escalation_technical_resume.add_argument("--diagnostic-digest", required=True)
    escalation_technical_resume.add_argument("--root")
    escalation_technical_claim = escalation_actions.add_parser(
        "claim-technical-route",
        help="réclame une fois une route locale de diagnostic déjà ouverte",
    )
    escalation_technical_claim.add_argument("issue")
    escalation_technical_claim.add_argument(
        "role", choices=tuple(ROLE_DEFAULTS),
    )
    escalation_technical_claim.add_argument(
        "--halt-generation", required=True, type=_positive_int,
    )
    escalation_technical_claim.add_argument(
        "--route-id", required=True,
        help="identité stable du même diagnostic local ; réutiliser au rejeu",
    )
    escalation_technical_claim.add_argument("--root")
    args = parser.parse_args(argv)
    try:
        if args.command == "claim-review":
            EscalationStore.for_root(args.root).active_floor(args.issue, "reviewer")
            review_root, review_base = review_diff_coordinates(args.root, args.base)
            coordinates = {"root": str(review_root), "base": review_base}
            ReviewDeduplicator._validate_coordinates(coordinates)
            expected_hash = review_diff_hash(git_diff(review_root, review_base))
            repository = args.repository or repository_identity(review_root)
            deduplicator = ReviewDeduplicator(repository)
            def validate_claim():
                return deduplicator.claim_git(
                    expected_hash,
                    coordinates=coordinates,
                    claim_attempt_token=args.claim_attempt_token,
                    replay_interrupted=args.replay_interrupted,
                )

            def validate_rearm(previous_diff_hash: str):
                binding = deduplicator.validated_terminal_proof_binding(
                    args.issue, previous_diff_hash, coordinates=coordinates,
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
                review_root,
            ).claim_fresh_reviewer_authorization(
                args.issue,
                expected_hash,
                validated_claim=validate_claim,
                validated_rearm=validate_rearm,
            )
            print(json.dumps(result.to_dict(), indent=2))
            return
        if args.command == "recover-review":
            review_root, review_base = review_diff_coordinates(args.root, args.base)
            coordinates = {"root": str(review_root), "base": review_base}
            repository = args.repository or repository_identity(review_root)
            result = ReviewDeduplicator(repository).recover(
                args.diff_hash, args.generation, args.claim_id, args.reason,
                coordinates=coordinates,
            )
            print(json.dumps(result.to_dict(), indent=2))
            return
        if args.command == "complete-review":
            repository = args.repository or repository_identity(args.root)
            # A compatibility command remains explicit but inert: no structured,
            # coordinate-bound proof accompanies this request.
            ReviewDeduplicator(repository).complete(args.diff_hash, args.claim_id)
        if args.command == "record-review-proof":
            try:
                submitted = json.loads(Path(args.outcomes_file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RoutingConfigError("preuve AC soumise illisible ou JSON invalide.") from exc
            if not isinstance(submitted, dict) or set(submitted) != {"outcomes", "quality"}:
                raise RoutingConfigError(
                    "preuve AC soumise : seules les clés outcomes et quality sont autorisées."
                )
            from foundry import tracker
            active_tracker = tracker()
            active_tracker.resolve_checkout_project(args.root)
            current = active_tracker.get_issue(args.issue)
            repository = args.repository or repository_identity(args.root)
            result = AcceptanceProofStore(repository).create(
                issue_id=current.id, issue_body=current.body, reviewer_role="reviewer",
                outcomes=submitted["outcomes"], quality=submitted["quality"],
                diff_hash=args.diff_hash, claim_id=args.claim_id, root=args.root, base=args.base,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.command == "read-review":
            repository = args.repository or repository_identity(args.root)
            def emit(diff: bytes) -> None:
                sys.stdout.buffer.write(diff)
                sys.stdout.buffer.flush()

            claimed_review_diff(
                args.diff_hash, repository, claim_id=args.claim_id,
                root=args.root, base=args.base, consume=emit,
            )
            return
        if args.command == "escalation":
            from foundry.escalation import EscalationStore

            store = EscalationStore.for_root(args.root)
            if args.escalation_action == "show":
                payload = store.status(args.issue)
                human_required = payload["human_required"]
            elif args.escalation_action == "resume":
                payload = store.resume(
                    args.issue, args.reason, args.halt_generation, args.remediation_credits,
                )
                human_required = False
            elif args.escalation_action == "rearm-remediation":
                payload = store.rearm_remediation(
                    args.issue, args.role, args.reason, args.halt_generation,
                    args.remediation_credits, args.current_halt_generation,
                )
                human_required = False
            elif args.escalation_action == "cancel-remediation":
                payload = store.cancel_remediation(args.issue, args.halt_generation)
                human_required = False
            elif args.escalation_action == "failure":
                validated_blocking_proof = None
                if args.kind == "review_blocking_after_fix":
                    if args.root is None or args.base is None:
                        raise RoutingConfigError(
                            "review bloquante : --root et --base Git figés requis."
                        )
                    review_root, review_base = review_diff_coordinates(args.root, args.base)
                    coordinates = {"root": str(review_root), "base": review_base}
                    expected_hash = review_diff_hash(git_diff(review_root, review_base))
                    repository = args.repository or repository_identity(review_root)
                    deduplicator = ReviewDeduplicator(repository)

                    def validated_blocking_proof():
                        binding = deduplicator.validated_terminal_proof_binding(
                            args.issue, expected_hash, coordinates=coordinates,
                        )
                        if (
                            binding is None or binding["quality"] != "blocked"
                            or binding["all_pass"] is not False
                        ):
                            raise RoutingConfigError(
                                "preuve terminale bloquante authentifiée requise."
                            )
                        return {**binding, "diff_hash": expected_hash}

                decision = store.record_failure(
                    args.issue, args.role, args.kind, args.current_tier,
                    idempotency_key=args.idempotency_key,
                    authorization_aware=True,
                    validated_blocking_proof=validated_blocking_proof,
                )
                payload = decision.to_dict()
                human_required = decision.human_required
            elif args.escalation_action == "risk":
                decision = store.record_risk(
                    args.issue, args.role, args.kind, args.current_tier,
                )
                payload = decision.to_dict()
                human_required = decision.human_required
            elif args.escalation_action == "verdict":
                from foundry.escalation import BoundedDiagnostic

                has_diagnostic = (
                    args.diagnostic_attempts is not None
                    or args.diagnostic_digest is not None
                )
                if args.category == "durable_ambiguity":
                    if args.diagnostic_attempts is None or args.diagnostic_digest is None:
                        raise RoutingConfigError(
                            "ambiguïté durable : attestation de diagnostic absente."
                        )
                    diagnostic = BoundedDiagnostic(
                        outcome="durable_ambiguity",
                        attempts=args.diagnostic_attempts,
                        maximum_attempts=MAX_DIAGNOSTIC_ATTEMPTS,
                        evidence_digest=args.diagnostic_digest,
                    )
                elif has_diagnostic:
                    raise RoutingConfigError(
                        "attestation réservée à une ambiguïté durable."
                    )
                else:
                    diagnostic = None
                decision = store.record_human_verdict(
                    args.issue, args.role, args.current_tier,
                    category=args.category, diagnostic=diagnostic,
                )
                payload = decision.to_dict()
                human_required = decision.human_required
            elif args.escalation_action == "reclassify-legacy-terminal":
                payload = store.reclassify_legacy_terminal(
                    args.issue, args.halt_generation,
                )
                human_required = False
            elif args.escalation_action == "resume-technical":
                payload = store.resume_technical_remediation(
                    args.issue, args.halt_generation, args.diagnostic_digest,
                )
                human_required = False
            elif args.escalation_action == "claim-technical-route":
                payload = store.claim_technical_remediation_route(
                    args.issue, args.role, args.halt_generation, args.route_id,
                )
                human_required = False
            else:
                decision = store.record_explicit_request(
                    args.issue, args.role, args.current_tier,
                )
                payload = decision.to_dict()
                human_required = decision.human_required
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            if human_required:
                raise SystemExit(2)
            return
        if args.command == "codex-plan":
            from foundry.routing_facades import (  # imported lazily: façade -> contract
                codex_available_models,
                codex_review_plan,
                codex_spawn_plan,
            )

            try:
                packet = (sys.stdin.read() if args.packet_file == "-" else
                          Path(args.packet_file).read_text(encoding="utf-8"))
            except OSError as exc:
                raise RoutingConfigError(
                    f"task packet illisible '{args.packet_file}' : {exc.strerror or exc}."
                ) from exc
            reviewer_technical = (
                args.role == "reviewer" and args.technical_remediation
            )
            if reviewer_technical and args.issue is None:
                raise RoutingConfigError(
                    "diagnostic reviewer local : --issue est requis pour la route auditée."
                )
            if reviewer_technical and (
                args.git_diff
                or args.diff_hash is not None
                or args.claim_id is not None
                or args.replay_interrupted
            ):
                raise RoutingConfigError(
                    "diagnostic reviewer local : aucune claim ou lecture Git n'est admise."
                )
            if args.role == "reviewer" and not reviewer_technical:
                if not args.git_diff:
                    raise RoutingConfigError(
                        "reviewer Codex : --git-diff est requis pour "
                        "obtenir le verdict commun avant spawn."
                    )
                if args.issue is None:
                    raise RoutingConfigError(
                        "reviewer Codex : --issue est requis avant de lire ou "
                        "réserver le diff."
                    )
                if args.base is None:
                    raise RoutingConfigError(
                        "reviewer Codex : --base SHA Git figé est requis avant toute claim."
                    )
                if args.root is None:
                    raise RoutingConfigError(
                        "reviewer Codex : --root Git exact est requis avant toute claim."
                    )
                EscalationStore.for_root(args.root).active_floor(
                    args.issue, "reviewer",
                )
                review_root, review_base = review_diff_coordinates(args.root, args.base)
            elif args.git_diff:
                raise RoutingConfigError("--git-diff est réservé au rôle reviewer.")
            if (args.diff_hash is None) != (args.claim_id is None):
                raise RoutingConfigError(
                    "reviewer Codex récupéré : --diff-hash et --claim-id sont requis ensemble."
                )
            if args.diff_hash is not None and args.role != "reviewer":
                raise RoutingConfigError(
                    "--diff-hash et --claim-id sont réservés au rôle reviewer."
                )
            if args.technical_remediation != (args.technical_remediation_id is not None):
                raise RoutingConfigError(
                    "--technical-remediation et --technical-remediation-id sont requis ensemble."
                )
            profile = {}
            if args.profile_model_active:
                profile["model"] = True
            if args.profile_effort_active:
                profile["model_reasoning_effort"] = True
            plan_args = {
                "root": args.root,
                "user": _user_request(args),
                "available_models": (
                    set(args.available_models) if args.available_models is not None
                    else codex_available_models()
                ),
                "effective_profile": profile,
                "task_name": args.task_name,
                "subagent_available": not args.no_subagent,
                "issue_id": args.issue,
            }
            if args.role == "reviewer" and not reviewer_technical:
                if args.technical_remediation or args.technical_remediation_id is not None:
                    raise RoutingConfigError(
                        "une route technique locale ne peut pas financer un reviewer ; "
                        "utilisez un claim de review distinct."
                    )
                plan = codex_review_plan(
                    packet, base=review_base,
                    recovered_diff_hash=args.diff_hash,
                    recovered_claim_id=args.claim_id,
                    claim_attempt_token=args.claim_attempt_token,
                    replay_interrupted=args.replay_interrupted,
                    **{**plan_args, "root": review_root},
                )
            else:
                plan = codex_spawn_plan(
                    args.role, packet,
                    technical_remediation=args.technical_remediation,
                    technical_remediation_id=args.technical_remediation_id,
                    **plan_args,
                )
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        policy = RoutingPolicy.load(args.root)
        # Public Claude route inspection must report the same executable
        # contract as the invocation facade.  Keep this import local: the
        # facade itself imports the policy primitives from this module.
        if args.command == "show" and (args.host is None or args.host == "claude"):
            from foundry.routing_facades import _load_claude_policy
            policy = _load_claude_policy(args.root)
        elif args.command == "resolve" and args.host == "claude":
            from foundry.routing_facades import _load_claude_policy
            policy = _load_claude_policy(args.root)
        if args.command == "show":
            hosts = (args.host,) if args.host else HOSTS
            routes = [policy.resolve(role, host) for host in hosts for role in ROLE_DEFAULTS]
        else:
            routes = [policy.resolve(
                args.role,
                args.host,
                user=_user_request(args),
                available_models=args.available_models,
                active_host_overrides=args.host_override,
            )]
    except (RoutingConfigError, RoutingUnavailableError) as exc:
        if not handle_config_errors:
            raise
        raise SystemExit(f"Configuration de routage invalide : {exc}") from None
    print(json.dumps(_policy_payload(policy, routes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
