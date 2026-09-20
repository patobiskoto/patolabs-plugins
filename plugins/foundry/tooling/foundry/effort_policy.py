"""Declarative reasoning-effort scopes shared by routing and telemetry.

An effort is only ordered inside one ``(host, model family, policy version)``
scope.  The declarations are data, so a project can add a provider model family
without changing the resolver or the telemetry schema.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Mapping


ULTRA_INADMISSIBLE_REASON = (
    "automatic task delegation is inadmissible pending a Foundry ADR"
)

# These identifiers cross the telemetry boundary.  Keep the vocabulary open to
# provider additions while refusing free-form text (which could be personal or
# prompt-derived data) in a journal intended for aggregate export.
PUBLIC_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")


def is_public_identifier(value: object) -> bool:
    return isinstance(value, str) and PUBLIC_IDENTIFIER.fullmatch(value) is not None


@dataclass(frozen=True)
class EffortScope:
    host: str
    family: str
    version: int
    levels: tuple[str, ...]
    inadmissible: Mapping[str, str]


def _default_effort_scopes() -> dict[str, dict[str, EffortScope]]:
    """Load the versioned shipped declaration without changing its policy values."""
    raw = json.loads(Path(__file__).with_name("default-effort-scopes.json").read_text())
    return {
        host: {
            family: validate_scope_data(host, family, scope, "default effort scope")
            for family, scope in families.items()
        }
        for host, families in raw.items()
    }


def model_family(host: str, model: str) -> str:
    """Return the most-specific declared family, falling back to ``default``."""
    families = DEFAULT_EFFORT_SCOPES.get(host, {})
    matches = [name for name in families if name != "default" and model.startswith(name)]
    return max(matches, key=len) if matches else "default"


def scope_for(host: str, model: str, scopes=None) -> EffortScope:
    scopes = DEFAULT_EFFORT_SCOPES if scopes is None else scopes
    families = scopes.get(host, {})
    matches = [name for name in families if name != "default" and model.startswith(name)]
    family = max(matches, key=len) if matches else "default"
    return families[family]


def validate_scope_data(host: str, family: str, raw: object, where: str) -> EffortScope:
    if not is_public_identifier(family):
        raise ValueError(f"{where}.family: identifiant public attendu.")
    if not isinstance(raw, dict) or set(raw) - {"version", "levels", "inadmissible"}:
        raise ValueError(f"{where}: objet avec version, levels et inadmissible attendu.")
    version = raw.get("version", 1)
    levels = raw.get("levels")
    inadmissible = raw.get("inadmissible", {})
    if type(version) is not int or version < 1:
        raise ValueError(f"{where}.version: entier positif attendu.")
    if (not isinstance(levels, list) or not levels or any(not is_public_identifier(x) for x in levels)
            or len(set(levels)) != len(levels)):
        raise ValueError(f"{where}.levels: identifiants publics uniques attendus.")
    if (not isinstance(inadmissible, dict) or any(level not in levels or not isinstance(reason, str) or not reason.strip()
                                                  for level, reason in inadmissible.items())):
        raise ValueError(f"{where}.inadmissible: raisons non vides requises pour des niveaux déclarés.")
    clean_inadmissible = dict(inadmissible)
    if "ultra" in levels:
        # ADR-0013 is a Foundry-wide authority boundary. A project may extend a
        # vocabulary, but configuration cannot grant delegation authority.
        clean_inadmissible["ultra"] = ULTRA_INADMISSIBLE_REASON
    return EffortScope(host, family, version, tuple(levels), clean_inadmissible)


DEFAULT_EFFORT_SCOPES = _default_effort_scopes()
