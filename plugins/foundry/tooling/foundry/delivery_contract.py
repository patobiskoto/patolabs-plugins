"""Deterministic, read-only delivery-proof contract and exact-SHA receipts.

This prototype deliberately has no provider client, command runner, persistence, or
CLI entry point.  Callers supply already-read observations; this module only validates
the closed declarative contract and materializes a canonical receipt.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


CONTRACT_SCHEMA = "foundry-delivery-contract.v1"
PROOF_SCHEMA = "foundry-delivery-proof.v1"
RECEIPT_SCHEMA = "foundry-delivery-receipt.v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_STATES = frozenset(("success", "pending", "missing", "unavailable"))


class DeliveryContractError(ValueError):
    """A contract or proof violated the deliberately closed prototype schema."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DeliveryContractError(f"{field} must be a lowercase identifier")
    return value


def _sha(value: object, field: str = "sha") -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise DeliveryContractError(f"{field} must be an exact 40-character lowercase SHA")
    return value


def _closed(mapping: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise DeliveryContractError(f"{label} has unsupported fields: {', '.join(sorted(unknown))}")


def validate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize the small, declarative, non-executable contract."""
    if not isinstance(contract, Mapping):
        raise DeliveryContractError("contract must be an object")
    _closed(contract, frozenset(("schema", "version", "sources", "requirements")), "contract")
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise DeliveryContractError("unsupported contract schema")
    version = _identifier(contract.get("version"), "contract version")
    sources = contract.get("sources")
    requirements = contract.get("requirements")
    if not isinstance(sources, list) or not sources:
        raise DeliveryContractError("contract requires non-empty sources")
    if not isinstance(requirements, list) or not requirements:
        raise DeliveryContractError("contract requires non-empty requirements")
    canonical_sources = []
    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            raise DeliveryContractError("source must be an object")
        _closed(source, frozenset(("id", "adapter", "adapter_version", "provenance")), "source")
        source_id = _identifier(source.get("id"), "source id")
        if source_id in source_ids:
            raise DeliveryContractError("source ids must be unique")
        source_ids.add(source_id)
        canonical_sources.append({
            "id": source_id,
            "adapter": _identifier(source.get("adapter"), "adapter"),
            "adapter_version": _identifier(source.get("adapter_version"), "adapter version"),
            "provenance": _identifier(source.get("provenance"), "source provenance"),
        })
    canonical_requirements = []
    requirement_ids: set[str] = set()
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            raise DeliveryContractError("requirement must be an object")
        _closed(requirement, frozenset(("id", "source", "required_facts")), "requirement")
        requirement_id = _identifier(requirement.get("id"), "requirement id")
        if requirement_id in requirement_ids:
            raise DeliveryContractError("requirement ids must be unique")
        requirement_ids.add(requirement_id)
        source = _identifier(requirement.get("source"), "requirement source")
        if source not in source_ids:
            raise DeliveryContractError("requirement references an unknown source")
        facts = requirement.get("required_facts", [])
        if not isinstance(facts, list) or len(facts) != len(set(facts)):
            raise DeliveryContractError("required_facts must be unique identifiers")
        canonical_requirements.append({
            "id": requirement_id,
            "source": source,
            "required_facts": sorted(_identifier(fact, "required fact") for fact in facts),
        })
    return {
        "schema": CONTRACT_SCHEMA,
        "version": version,
        "sources": sorted(canonical_sources, key=lambda item: item["id"]),
        "requirements": sorted(canonical_requirements, key=lambda item: item["id"]),
    }


def contract_digest(contract: Mapping[str, Any]) -> str:
    """Return the SHA-256 of the validated, canonical contract content."""
    return _digest(validate_contract(contract))


@dataclass(frozen=True)
class Proof:
    source: str
    project: str
    sha: str
    state: str
    provenance: str
    facts: dict[str, object]


def _proof(value: object, source: str) -> Proof:
    if not isinstance(value, Mapping):
        raise DeliveryContractError("proof must be an object")
    _closed(value, frozenset(("schema", "source", "project", "sha", "state", "provenance", "facts")), "proof")
    if value.get("schema") != PROOF_SCHEMA:
        raise DeliveryContractError("unsupported proof schema")
    if _identifier(value.get("source"), "proof source") != source:
        raise DeliveryContractError("proof source does not match its observation key")
    facts = value.get("facts", {})
    if not isinstance(facts, Mapping):
        raise DeliveryContractError("proof facts must be an object")
    canonical_facts = {}
    for key, fact in sorted(facts.items()):
        key = _identifier(key, "fact")
        # Facts are deliberately a nullable boolean vocabulary, not a place to
        # persist provider responses, logs, URLs, commands, or secrets.
        if fact is not None and not isinstance(fact, bool):
            raise DeliveryContractError("proof facts must be boolean or null")
        canonical_facts[key] = fact
    return Proof(source, _identifier(value.get("project"), "proof project"), _sha(value.get("sha")),
                 value.get("state") if value.get("state") in _STATES else _bad_state(),
                 _identifier(value.get("provenance"), "proof provenance"), canonical_facts)


def _bad_state() -> str:
    raise DeliveryContractError("proof state is unsupported")


def delivery_receipt(contract: Mapping[str, Any], *, project: str, sha: str,
                     observations: Mapping[str, object], observation_id: str,
                     observed_at: str) -> dict[str, Any]:
    """Evaluate supplied proof observations without contacting or changing anything."""
    canonical = validate_contract(contract)
    project = _identifier(project, "project")
    sha = _sha(sha)
    observation_id = _identifier(observation_id, "observation id")
    if not isinstance(observed_at, str) or not observed_at:
        raise DeliveryContractError("observed_at must be a non-empty timestamp")
    if not isinstance(observations, Mapping):
        raise DeliveryContractError("observations must be an object")
    sources = {item["id"]: item for item in canonical["sources"]}
    unexpected = set(observations) - set(sources)
    if unexpected:
        raise DeliveryContractError("observations contain an unknown source")
    proof_rows: list[dict[str, Any]] = []
    outcomes: list[str] = []
    for requirement in canonical["requirements"]:
        source = requirement["source"]
        raw = observations.get(source)
        row: dict[str, Any] = {
            "requirement": requirement["id"], "source": source,
            "adapter": sources[source]["adapter"],
            "adapter_version": sources[source]["adapter_version"],
            "source_provenance": sources[source]["provenance"],
        }
        if raw is None:
            row["outcome"] = "missing-proof"
        else:
            try:
                proof = _proof(raw, source)
            except DeliveryContractError as exc:
                row["outcome"] = "unsupported-schema" if str(exc) == "unsupported proof schema" else "missing-proof"
            else:
                row["provenance"] = proof.provenance
                if proof.project != project or proof.sha != sha:
                    row["outcome"] = "wrong-sha"
                elif proof.state == "pending":
                    row["outcome"] = "pending"
                elif proof.state == "unavailable":
                    row["outcome"] = "inaccessible"
                elif proof.state == "missing":
                    row["outcome"] = "missing-proof"
                elif any(proof.facts.get(fact) is None for fact in requirement["required_facts"]):
                    row["outcome"] = "unavailable"
                elif any(proof.facts[fact] is False for fact in requirement["required_facts"]):
                    row["outcome"] = "failed-proof"
                else:
                    row["outcome"] = "success"
        outcomes.append(row["outcome"])
        proof_rows.append(row)
    # Order is deliberately conservative and stable: no missing/unknown fact is a
    # failed proof, and only all successes can become verified.
    verdict = next((state for state in ("wrong-sha", "unsupported-schema", "inaccessible",
                                        "pending", "missing-proof", "unavailable", "failed-proof")
                    if state in outcomes), "verified")
    return {
        "schema": RECEIPT_SCHEMA,
        "receipt_id": observation_id,
        "project": project,
        "sha": sha,
        "contract": {"schema": CONTRACT_SCHEMA, "version": canonical["version"], "digest": _digest(canonical)},
        "observed_at": observed_at,
        "verdict": verdict,
        "proofs": proof_rows,
    }


def claude_delivery_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Claude facade; deliberately just the shared canonical implementation."""
    return delivery_receipt(*args, **kwargs)


def codex_delivery_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Codex facade; deliberately just the shared canonical implementation."""
    return delivery_receipt(*args, **kwargs)
