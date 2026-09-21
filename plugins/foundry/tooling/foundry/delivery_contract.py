"""Deterministic, read-only delivery-proof contract and exact-SHA receipts.

This prototype deliberately has no provider client, command runner, credential, or
CLI entry point.  A one-source read-only adapter seam supplies observations; this
module only validates the closed declarative contract and materializes a canonical
receipt.  Its optional journal is a bounded local append-only evidence log, never a
remote durable store.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


CONTRACT_SCHEMA = "foundry-delivery-contract.v1"
PROOF_SCHEMA = "foundry-delivery-proof.v1"
RECEIPT_SCHEMA = "foundry-delivery-receipt.v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_STATES = frozenset(("success", "pending", "missing", "unavailable"))
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
MAX_LOCAL_RECEIPTS = 128


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


def _digest_value(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise DeliveryContractError(f"{field} must be a 64-character lowercase digest")
    return value


def _timestamp(value: object) -> str:
    """Accept only a canonical UTC instant, never arbitrary provider text."""
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise DeliveryContractError("observed_at must be a canonical UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise DeliveryContractError("observed_at must be a canonical UTC timestamp") from exc
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
        if not isinstance(facts, list):
            raise DeliveryContractError("required_facts must be unique identifiers")
        canonical_facts = [_identifier(fact, "required fact") for fact in facts]
        if len(canonical_facts) != len(set(canonical_facts)):
            raise DeliveryContractError("required_facts must be unique identifiers")
        canonical_requirements.append({
            "id": requirement_id,
            "source": source,
            "required_facts": sorted(canonical_facts),
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


class ReadOnlyProofAdapter(Protocol):
    """One declared source of already-existing proof; it has no mutation method."""

    source_id: str
    adapter: str
    adapter_version: str
    provenance: str

    def read_proof(self, *, project: str, sha: str) -> Mapping[str, object]: ...


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


def _source_adapter(adapter: object, source: Mapping[str, str]) -> ReadOnlyProofAdapter:
    """Bind an adapter's declared identity to exactly one contract source."""
    for field, declared_field in (("source_id", "id"), ("adapter", "adapter"),
                                  ("adapter_version", "adapter_version"),
                                  ("provenance", "provenance")):
        if getattr(adapter, field, None) != source[declared_field]:
            raise DeliveryContractError("adapter does not match its declared source")
    reader = getattr(adapter, "read_proof", None)
    if not callable(reader):
        raise DeliveryContractError("adapter must provide read_proof")
    return adapter  # type: ignore[return-value]


def read_delivery_proof(contract: Mapping[str, Any], *, project: str, sha: str,
                        source: str, adapter: ReadOnlyProofAdapter) -> dict[str, object]:
    """Read one contract-bound source through the deliberately read-only seam."""
    canonical = validate_contract(contract)
    project = _identifier(project, "project")
    sha = _sha(sha)
    source = _identifier(source, "source")
    declared = next((item for item in canonical["sources"] if item["id"] == source), None)
    if declared is None:
        raise DeliveryContractError("adapter source is not declared by the contract")
    bound = _source_adapter(adapter, declared)
    proof = bound.read_proof(project=project, sha=sha)
    if not isinstance(proof, Mapping):
        raise DeliveryContractError("adapter proof must be an object")
    return dict(proof)


def delivery_receipt_from_adapter(
    contract: Mapping[str, Any], *, project: str, sha: str, source: str,
    adapter: ReadOnlyProofAdapter, observation_id: str, observed_at: str,
) -> dict[str, Any]:
    """Evaluate exactly one proof obtained through a contract-bound read-only adapter."""
    proof = read_delivery_proof(contract, project=project, sha=sha, source=source, adapter=adapter)
    return delivery_receipt(
        contract, project=project, sha=sha, observations={source: proof},
        observation_id=observation_id, observed_at=observed_at,
    )


def delivery_receipt(contract: Mapping[str, Any], *, project: str, sha: str,
                     observations: Mapping[str, object], observation_id: str,
                     observed_at: str) -> dict[str, Any]:
    """Evaluate supplied proof observations without contacting or changing anything."""
    canonical = validate_contract(contract)
    project = _identifier(project, "project")
    sha = _sha(sha)
    observation_id = _identifier(observation_id, "observation id")
    observed_at = _timestamp(observed_at)
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
                if proof.provenance != sources[source]["provenance"]:
                    row["outcome"] = "provenance-mismatch"
                elif proof.project != project:
                    row["outcome"] = "cross-project"
                elif proof.sha != sha:
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
    verdict = next((state for state in ("provenance-mismatch", "cross-project", "wrong-sha", "unsupported-schema", "inaccessible",
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


class DeliveryReceiptJournal:
    """Bounded local append-only receipt log; it neither contacts nor owns a provider."""

    def __init__(self, path: Path, *, max_receipts: int = MAX_LOCAL_RECEIPTS):
        if not isinstance(path, Path):
            raise DeliveryContractError("journal path must be a Path")
        if not isinstance(max_receipts, int) or not 1 <= max_receipts <= MAX_LOCAL_RECEIPTS:
            raise DeliveryContractError("journal max_receipts is out of bounds")
        self.path = path
        self.max_receipts = max_receipts

    def append(self, receipt: Mapping[str, object]) -> dict[str, Any]:
        """Append one generated receipt exactly once, returning a detached copy."""
        canonical = self._validated_receipt(receipt)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="ascii") as handle:
            self._lock(handle)
            try:
                handle.seek(0)
                try:
                    rows = [json.loads(line) for line in handle if line.strip()]
                except json.JSONDecodeError as exc:
                    raise DeliveryContractError("local receipt journal is malformed") from exc
                if len(rows) >= self.max_receipts:
                    raise DeliveryContractError("local receipt journal is full")
                if any(row.get("receipt_id") == canonical["receipt_id"] for row in rows):
                    raise DeliveryContractError("receipt_id already exists in local journal")
                handle.seek(0, os.SEEK_END)
                handle.write(_canonical(canonical) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                self._unlock(handle)
        return json.loads(_canonical(canonical))

    def receipts(self) -> tuple[dict[str, Any], ...]:
        """Read local journal entries without exposing its mutable backing state."""
        if not self.path.exists():
            return ()
        try:
            with self.path.open(encoding="ascii") as handle:
                return tuple(self._validated_receipt(json.loads(line)) for line in handle if line.strip())
        except json.JSONDecodeError as exc:
            raise DeliveryContractError("local receipt journal is malformed") from exc

    @staticmethod
    def _lock(handle: Any) -> None:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock(handle: Any) -> None:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _validated_receipt(value: Mapping[str, object]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise DeliveryContractError("receipt must be an object")
        _closed(value, frozenset(("schema", "receipt_id", "project", "sha", "contract", "observed_at", "verdict", "proofs")), "receipt")
        if value.get("schema") != RECEIPT_SCHEMA:
            raise DeliveryContractError("unsupported receipt schema")
        _identifier(value.get("receipt_id"), "receipt id")
        _identifier(value.get("project"), "receipt project")
        _sha(value.get("sha"), "receipt sha")
        _timestamp(value.get("observed_at"))
        # Only receipts emitted by this module's closed vocabulary can be journaled.
        if value.get("verdict") not in {"verified", "provenance-mismatch", "cross-project", "wrong-sha", "unsupported-schema", "inaccessible", "pending", "missing-proof", "unavailable", "failed-proof"}:
            raise DeliveryContractError("receipt verdict is unsupported")
        contract = value.get("contract")
        if not isinstance(contract, Mapping) or set(contract) != {"schema", "version", "digest"}:
            raise DeliveryContractError("receipt contract is invalid")
        if contract.get("schema") != CONTRACT_SCHEMA:
            raise DeliveryContractError("receipt contract schema is invalid")
        _identifier(contract.get("version"), "receipt contract version")
        _digest_value(contract.get("digest"), "receipt contract digest")
        proofs = value.get("proofs")
        if not isinstance(proofs, list):
            raise DeliveryContractError("receipt proofs must be a list")
        for proof in proofs:
            if not isinstance(proof, Mapping):
                raise DeliveryContractError("receipt proof must be an object")
            _closed(proof, frozenset(("requirement", "source", "adapter", "adapter_version",
                                      "source_provenance", "provenance", "outcome")), "receipt proof")
            for field in ("requirement", "source", "adapter", "adapter_version", "source_provenance", "outcome"):
                _identifier(proof.get(field), f"receipt proof {field}")
            if "provenance" in proof:
                _identifier(proof["provenance"], "receipt proof provenance")
        # Canonical JSON round-trips the closed primitive tree and removes aliases.
        try:
            return json.loads(_canonical(value))
        except (TypeError, ValueError) as exc:
            raise DeliveryContractError("receipt is not canonical JSON data") from exc


def claude_delivery_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Claude facade; deliberately just the shared canonical implementation."""
    return delivery_receipt(*args, **kwargs)


def codex_delivery_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Codex facade; deliberately just the shared canonical implementation."""
    return delivery_receipt(*args, **kwargs)
